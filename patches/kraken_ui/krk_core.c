/*
 * krk_core.c - the "KRAKEN" top-level menu category (11th, index 10) and its sub-list.
 *
 * Wiring is Snake's, unchanged (dm32uv-re tools/examples/snake, docs/menu-system.md):
 *   render-path slot  -> krk_render_stub -> krk_category_entry   (fills the sub-list cells)
 *   OK-path slot      -> krk_ok_stub     -> krk_category_entry   (opens the sub-list)
 *   item-action slot  -> krk_item_stub   -> krk_item_ok          (runs the selected screen)
 * What differs from Snake: the sub-list has one row per entry of krk_screens[], and krk_item_ok
 * picks the screen from the highlighted row (KRK_SELECTED_ROW, docs/sublist-cursor.md).
 *
 * Adding a screen: write a `const struct krk_screen` in its own file, list it in krk_screens[]
 * below, add the file to the manifest's sources. Nothing else changes.
 */
#include "krk.h"
#include "krk_stock.h"

/* The highlighted sub-list row when the item-action case runs: byte 2 of the menu cursor struct
 * 0x0003e4e8 (byte 1 is the top-level row). Every stock multi-item case block reads it the same
 * way, e.g. category 0 at 0x0303cf1c: `ld.b r2,(0x3e4e8+2)`, then item = index_list[row + 2],
 * bound-checked, then a jump table. Our index list is sequential, so the row is the item. */
/* (KRK_SELECTED_ROW is defined in krk.h: krk_optrow.c reads it too.) */

extern const struct krk_screen krk_about_screen;
extern const struct krk_screen krk_rssi_screen;
extern const struct krk_screen krk_grid_screen;
extern const struct krk_screen krk_lastheard_screen;
extern const struct krk_screen krk_scope_screen;

static const struct krk_screen *const krk_screens[] = {
    &krk_rssi_screen,       /* F2 */
    &krk_scope_screen,      /* F5 */
    &krk_about_screen,
};
/* Last Heard moved to the stock Call Log (docs/calllog-submenu.md) and GPS Grid to the stock GNSS
 * menu (docs/gnss-submenu.md); both are reached from there, not from this list. The screen structs
 * stay linked because those rows reference them. */
#define KRK_NBUILTIN ((int)(sizeof krk_screens / sizeof krk_screens[0]))
#define KRK_LIST_MAX 28            /* 4 built-in + KRK_EXT_MAX + About = 25; .bss.krk_list holds 2 x 28 pointers = 224 of its 256 bytes */

/* The visible list: built-in screens, then the extension blob's (validated: a missing blob reads
 * as erased flash). Rebuilt at every category entry; the row index selects from it. */
static const struct krk_screen *krk_list[KRK_LIST_MAX] __attribute__((section(".bss.krk_list")));
static int krk_nlist;

static int ext_ok(const void *p)
{
    return ((u32)p >= KRK_EXT_LO && (u32)p < KRK_EXT_HI) || ((u32)p >= KRK_EXT3_LO && (u32)p < KRK_EXT3_HI)
        || ((u32)p >= KRK_EXT4_LO && (u32)p < KRK_EXT4_HI) || ((u32)p >= KRK_EXT5_LO && (u32)p < KRK_EXT5_HI);
}

static void build_list(void)
{
    int i, n = 0;

    for (i = 0; i < KRK_NBUILTIN - 1 && n < KRK_LIST_MAX; i++)          /* all but About */
        krk_list[n++] = krk_screens[i];
    for (i = 0; i < KRK_EXT_MAX && n < KRK_LIST_MAX - 1; i++) {
        const struct krk_screen *e = KRK_EXT_TABLE[i];
        if (!e)
            continue;                   /* a reserved slot not filled yet */
        if (!ext_ok(e) || !ext_ok(e->label))
            continue;                   /* a blob that is not flashed (erased flash) must not hide later rows */
        krk_list[n++] = e;
    }
    krk_list[n++] = krk_screens[KRK_NBUILTIN - 1];                     /* About stays last */
    krk_nlist = n;
}

/* Sub-list string array: the generic list renderer reads ACTIVE_OPTION_PTR_ARRAY[i] as a
 * GB2312/ASCII string pointer. Held in patch SRAM. */
static const char *krk_item_labels[KRK_LIST_MAX] __attribute__((section(".bss.krk_list")));

/* Installs a plain n-row icon list: count, string array, generic subtype, sequential index list. Shared by the KRAKEN
 * list, option levels and Other Func's child lists (one copy, as krk_desc_restore). */
__attribute__((noinline)) void krk_list_cells(const char *const *labels, unsigned n)
{
    *ACTIVE_ITEM_COUNT = (u8)n;
    *ACTIVE_OPTION_PTR_ARRAY = (u32)labels;
    *ACTIVE_SCREEN_SUBTYPE = 2;                 /* stock generic icon list */
    build_sequential_index_list((u8)n, ACTIVE_INDEX_LIST, ACTIVE_INDEX_LIST + 2);
}

void krk_category_entry(void)
{
    int i, n;

    build_list();
    n = krk_nlist;
    for (i = 0; i < n; i++)
        krk_item_labels[i] = krk_list[i]->label;
    krk_list_cells(krk_item_labels, (unsigned)n);
    *(volatile u8 *)0x00032ab6 = 1;             /* row-icon mode: 1 = numbered boxes, 2 = radio marks (an option level just left) */
    /* the renderer bounds its row loop on this cell, not on the count (Snake v11) */
    *ACTIVE_VISIBLE_ROWS = (u8)(n < MENU_MAX_VISIBLE_ROWS ? n : MENU_MAX_VISIBLE_ROWS);
}

void krk_item_ok(void)
{
    unsigned row = KRK_SELECTED_ROW;

    build_list();                       /* krk_nlist/krk_list are not zeroed at power-up: never trust them here */
    if (row >= (unsigned)krk_nlist)
        row = 0;
    krk_row_open(krk_list[row], krk_category_entry);
}

/* ---- stock-menu hosts (krk.h struct krk_host) -------------------------------------------------
 * The shared half of every stock sub-list we append rows to. Each host section below keeps only what
 * is particular to it: how stock hands us the item, and how its cells are refilled. */

/* A setting opens the stock option level and returns at once (krk_optrow.c); anything else is a screen. */
void krk_row_open(const struct krk_screen *e, void (*restore)(void))
{
    if (!e)
        return;
    if (e->opt) {
        krk_opt_enter(e->opt);
        return;
    }
    krk_run_screen(e, restore);
}

const struct krk_screen *krk_host_row(const struct krk_host *h, unsigned item)
{
    const struct krk_screen *e;

    item -= h->first;                   /* unsigned: an item below first wraps past n */
    if (item >= h->n)
        return 0;
    e = h->rows[item];
    if (h->ext && (!ext_ok(e) || !ext_ok(e->label)))
        return 0;                       /* an empty slot, or a blob that is not flashed (erased flash) */
    return e;
}

void krk_host_open(const struct krk_host *h, unsigned item)
{
    krk_row_open(krk_host_row(h, item), h->restore);
}

void krk_host_commit(const struct krk_host *h, unsigned item)
{
    const struct krk_screen *e = krk_host_row(h, item);

    if (e && e->opt)
        krk_opt_commit(e->opt, h->restore);
}

/* The screen of a KRAKEN list row; the OK / title splices of the option level find their spec through it. */
const struct krk_screen *krk_row_screen(unsigned row)
{
    build_list();
    return row < (unsigned)krk_nlist ? krk_list[row] : 0;
}

/* ---- the stock APRS sub-list (category id 9), per channel mode -------------------------------
 * docs/aprs-submenu.md: the stock descriptor at 0x030ba924 ({u32 strings, u8 count, u8 subtype}
 * per language) is re-pointed by the manifest at arrays holding the 8 stock rows plus ours, and the
 * item-action case block 0x0303d54c dispatches items >= 8 through krk_aprs_item_stub to here.
 * The render case block 0x0303dd40 is replaced by `bsr krk_aprs_render; br tail` (v39): on a DIGITAL
 * channel the list is the stock one (items 0..7, stock DMR APRS), on an ANALOG channel it is
 * APRS On/Off (stock item 0, shared setting), Upload ID (our path picker, item 8), Intervals Set
 * (stock item 4, shared setting), Upload Beacon (our AFSK send, item 9) and Callsign (item 10).
 * Labels and dispatch both go through the index list by item value (renderer 0x03041644 reads
 * string_array[index_list[row]]; the history push copies the list by value to 0x3e664). */
#define APRS_DESC        ((const volatile u8 *)0x030ba924)
#define APRS_STOCK_ROWS  KRK_APRS_FIRST
static const u8 aprs_analog_items[] = {0, KRK_APRS_ITEM_PATH, 4, KRK_APRS_ITEM_UPLOAD, KRK_APRS_ITEM_CALL, KRK_APRS_ITEM_RX, KRK_APRS_ITEM_MON};

void krk_aprs_render(void);
static const struct krk_host aprs_host = {KRK_APRS_TABLE, krk_aprs_render, KRK_APRS_FIRST, KRK_APRS_MAX, 0, 1};

/* Dual watch (docs/dual-watch.md). The struct at 0x0003e1dc holds whichever side the receiver is on. The
 * "loaded side" bit (0x000400d9 bit 0) and the struct are NOT in step (hardware 2026-10-31: samples with the
 * struct on one side and the bit on the other, in both directions), so the bit says nothing about what the
 * struct holds. Every write of the struct goes through commit_channel_record_dual_vfo @0x030303bc, which itself
 * picks the side to store a copy for: `get_current_mode_state2() == 0 ? selected side (0x3e9e1 bit 7) : loaded
 * side (0x400d9 bit 0)` (the reload event 0x12 reads the same bit to choose its source, so at commit time it is
 * the side being loaded). The splice at its epilogue (krk_commit_end_stub -> krk_hook_chan_commit) repeats that choice, snapshots
 * rx/tx/flags8 into dw.c[side] and records dw.tag = the side the struct now describes. The selected side's
 * channel is then the live struct when dw.tag == selected, else that side's last snapshot. */
#define SIDE_LOADED   (*(volatile u8 *)0x000400d9 & 1)
#define DW_STATE      (*(volatile u8 *)0x000400d8)       /* get_current_mode_state2: 0 = no dual-watch engine activity */
#define CHAN_RX_FREQ  (*(volatile u32 *)0x0003e1dc)   /* the loaded channel record (not the receiver's live words, RX_FREQ10 / TX_FREQ10) */
#define CHAN_TX_FREQ  (*(volatile u32 *)0x0003e1e0)
#define DW_MAGIC      0x44575331u
static struct { u32 magic; signed char tag; u8 ok[2]; u8 flags8[2]; u32 rx[2], tx[2]; } dw;   /* tag: -1 unknown, else 0/1 */

static void dw_init(void)
{
    if (dw.magic != DW_MAGIC) {                  /* SRAM noise at power-up */
        dw.tag = -1;
        dw.ok[0] = dw.ok[1] = 0;
        dw.magic = DW_MAGIC;
    }
}

/* Splice target of krk_commit_end_stub (any task that commits a channel: UI, maintenance, PTT). Snapshot
 * first, tag last: a reader that sees the new tag sees the new snapshot. */
void krk_hook_chan_commit(void)
{
    unsigned side = DW_STATE ? SIDE_LOADED : SIDE_SELECTED;
    u8 f = VFO_FLAGS8;

    dw_init();
    if (f & 0xe0)                                /* the dispatcher folds modes 2 and 3 into 1 right after the commit */
        f = (u8)(f & 0x0f | 0x10);
    dw.rx[side] = CHAN_RX_FREQ;
    dw.tx[side] = CHAN_TX_FREQ;
    dw.flags8[side] = f;
    dw.ok[side] = 1;
    dw.tag = (signed char)side;
}

/* The side the live struct describes: 0 = A, 1 = B, -1 = not seen yet (the receiver is tuned to it). */
int krk_channel_side(void)
{
    return dw.magic == DW_MAGIC ? dw.tag : -1;
}

/* True when the live struct IS the selected side's channel (TX gates: refuse unless this holds). */
int krk_channel_on_selected(void)
{
    return krk_channel_side() == (int)SIDE_SELECTED;
}

/* flags8 of the selected side's channel (bits 4-7 mode, 1-2 power, 3 may not transmit), or -1 if never seen. */
int krk_channel_flags(void)
{
    unsigned sel = SIDE_SELECTED;

    if (krk_channel_on_selected())
        return VFO_FLAGS8;
    return dw.magic == DW_MAGIC && dw.ok[sel] ? dw.flags8[sel] : -1;
}

/* 0 analog, 1 digital, -1 unknown. */
int krk_channel_mode(void)
{
    int f = krk_channel_flags();

    return f < 0 ? -1 : (f & 0xf0) != 0;
}

int krk_channel_analog(void)
{
    return krk_channel_mode() == 0;
}

/* RX (tx = 0) or TX frequency of the selected side's channel in 10 Hz, 0 if unknown. */
u32 krk_channel_freq(int tx)
{
    unsigned sel = SIDE_SELECTED;

    if (krk_channel_on_selected())
        return tx ? CHAN_TX_FREQ : CHAN_RX_FREQ;
    return dw.magic == DW_MAGIC && dw.ok[sel] ? (tx ? dw.tx[sel] : dw.rx[sel]) : 0;
}

/* Installs the APRS sub-list cells for the current channel mode. Called from the stock render
 * case block (clobbers r0-r3, r12 only; nothing is live there) and after one of our screens. */
void krk_aprs_render(void)
{
    const volatile u8 *d = APRS_DESC + 8 * UI_LANG_IS_ENGLISH();
    u8 n;

    *ACTIVE_OPTION_PTR_ARRAY = *(const volatile u32 *)d;
    *ACTIVE_SCREEN_SUBTYPE = 2;
    if (krk_channel_analog()) {
        volatile u8 *list = (volatile u8 *)ACTIVE_INDEX_LIST + 2;
        int i;

        for (i = 0; i < (int)sizeof aprs_analog_items; i++)
            list[i] = aprs_analog_items[i];
        n = (u8)sizeof aprs_analog_items;
        *(volatile u16 *)ACTIVE_INDEX_LIST = n;
    } else {
        n = APRS_STOCK_ROWS;
        build_sequential_index_list(n, ACTIVE_INDEX_LIST, ACTIVE_INDEX_LIST + 2);
    }
    *ACTIVE_ITEM_COUNT = n;
    *ACTIVE_VISIBLE_ROWS = (u8)(n < MENU_MAX_VISIBLE_ROWS ? n : MENU_MAX_VISIBLE_ROWS);
}

void krk_aprs_item(void)
{
    krk_host_open(&aprs_host, MENU_L2_ITEMS[KRK_SELECTED_ROW]);
}

static const struct krk_screen *krk_rs_screen(void);     /* defined with the rest of the Radio Settings hook below */
static const struct krk_host cs_host;                    /* and the Channel Set host after it */

/* The option spec of the level-2 screen that is open: category 10 = the KRAKEN row, category 9 = the appended APRS item (level-2 list
 * 0x3e664 + row, items >= 8), category 5 = the row appended to Radio Settings (Settings item 0, one level deeper: the level-3 row byte
 * 0x3e4eb indexes the level-3 list 0x3e687); NULL for anything else. Category = top-level list 0x3e63f + 2 at the top row byte (0x3e4e9). */
const struct krk_opt *krk_open_opt(void)
{
    unsigned cat = ((volatile u8 *)0x0003e641)[*(volatile u8 *)0x0003e4e9];
    const struct krk_screen *e = 0;

    if (cat == 10) {
        e = krk_row_screen(KRK_SELECTED_ROW);
    } else if (cat == 5) {
        /* Settings: items 0 (Radio Settings) and 1 (Channel Set) carry rows of ours.
         * Item 0: krk_rs_screen() reads the latch (the level-3 row byte is not reliable there) and
         *         returns NULL for stock's own items.
         * Item 1: Channel Set needs no latch - both of its key events dispatch on the level-3 row
         *         through jump tables, so the level-3 index list resolves the row directly. */
        unsigned sub = ((volatile u8 *)0x0003e664)[*(volatile u8 *)0x0003e4ea];

        if (sub == 0) {
            e = krk_rs_screen();
        } else if (sub == 1) {
            e = krk_host_row(&cs_host, ((volatile u8 *)0x0003e687)[*(volatile u8 *)0x0003e4eb]);
        }
    } else if (cat == 9) {
        e = krk_host_row(&aprs_host, MENU_L2_ITEMS[KRK_SELECTED_ROW]);
    }
    return e ? e->opt : 0;
}

/* OK at the option level of an appended APRS row (docs/stock-option-level.md 2.6): stock's category-9 OK handler 0x0304d6c0 dispatches
 * items 0..7 and ignores the rest; the splice at 0x0304d81c (krk_aprs_ok_stub) hands items >= 8 here. */
void krk_aprs_opt_ok(unsigned item)
{
    krk_host_commit(&aprs_host, item);
}

/* ---- the stock Call Log sub-list (category id 4): a "Last Heard" row (docs/calllog-submenu.md) ----
 * Stock builds the list with menu_option_list_dispatch(5, count, list) @0x030493f8, which appends items
 * 0..3 (Missed, Answered, Sent, Delete) by the CPS menu-visibility bits. All three call sites
 * (0x03036b62, 0x0303dc06 render block, 0x0303ee3e) are spliced to krk_hook_gen, which runs the stock
 * generator and appends our items. The descriptor at 0x030b8060 ({u32 strings, u8 count, u8 subtype}
 * per language) is re-pointed by the manifest at the relocated arrays, and the item-action compare chain
 * at 0x0303cf80 sends our items through krk_calllog_item_stub to krk_calllog_item. */
#define CALLLOG_DESC     ((const volatile u8 *)0x030b8060)
#define LIST_ITEMS_MAX   33          /* level lists are 35 bytes: u16 count + 33 items */

/* Back from a screen row: the runner's history pop has already restored the index list (as stock's
 * own return path does, render block 0x0303dbf6 when depth != 1); put the cells back from the
 * descriptor exactly as that block does. */
/* Shared by every stock sub-list we append a row to (Call Log, GNSS, Radio Settings, Channel Set):
 * the four cells are reinstalled from the per-language descriptor exactly as each menu's own stock
 * render block does, so only the descriptor address differs. One copy, not four - the main blob's
 * code claim has ~100 bytes of headroom and four clones of this cost ~56 of them. */
static __attribute__((noinline)) void krk_desc_restore(const volatile u8 *desc)
{
    const volatile u8 *d = desc + 8 * UI_LANG_IS_ENGLISH();
    unsigned n = *(volatile u16 *)ACTIVE_INDEX_LIST;

    *ACTIVE_ITEM_COUNT = d[4];
    *ACTIVE_OPTION_PTR_ARRAY = *(const volatile u32 *)d;
    *ACTIVE_SCREEN_SUBTYPE = d[5];
    *ACTIVE_VISIBLE_ROWS = (u8)(n < MENU_MAX_VISIBLE_ROWS ? n : MENU_MAX_VISIBLE_ROWS);
}

static void krk_calllog_restore(void)
{
    krk_desc_restore(CALLLOG_DESC);
}

static const struct krk_screen *const calllog_rows[KRK_CALLLOG_MAX] = {KRK_CALLLOG_ROWS};
static const struct krk_host calllog_host = {calllog_rows, krk_calllog_restore, KRK_CALLLOG_FIRST, KRK_CALLLOG_MAX,
                                             KRK_CALLLOG_GEN_MODE, 0};

void krk_calllog_item(unsigned item)
{
    krk_host_open(&calllog_host, item);
}

/* ---- the stock GNSS sub-list (category id 8): a "GPS Grid" row (docs/gnss-submenu.md) ----
 * Unlike Call Log there is NO row generator: the render block 0x0303dd06 copies count / strings /
 * subtype from the descriptor at 0x030b7e28 and calls build_sequential_index_list(count), so the
 * generated count edit and the relocated label arrays are all the list needs (item = row).
 * The item-action compare chain at 0x0303d2a0 sends our items through krk_gnss_item_stub to
 * krk_gnss_item. Item 4 (GNSS Mode) is the last stock item. */
#define GNSS_DESC ((const volatile u8 *)0x030b7e28)

/* Back from a screen row: reinstall the cells from the descriptor, as the stock render block does. */
static void krk_gnss_restore(void)
{
    krk_desc_restore(GNSS_DESC);
}

static const struct krk_screen *const gnss_rows[KRK_GNSS_MAX] = {KRK_GNSS_ROWS};
static const struct krk_host gnss_host = {gnss_rows, krk_gnss_restore, KRK_GNSS_FIRST, KRK_GNSS_MAX, 0, 0};

void krk_gnss_item(unsigned item)
{
    krk_host_open(&gnss_host, item);
}

/* ---- the stock Radio Settings sub-list (Settings = category 5, level-2 item 0) ----------------
 * docs/settings-submenu.md. Radio Settings is not a category of its own: it is the first item of
 * Settings, so its rows are level 3 and a row's option level is level 4. Stock builds the four rows
 * (Voice/Key/Display/Other Func) with menu_option_list_dispatch(20, count, list) from the single
 * call site 0x030423b8, spliced here to append item 4. The descriptor at 0x030b8488 is re-pointed by
 * the manifest at 5-entry arrays, so the row's header comes free: the level-4 header handler
 * 0x03038720 draws desc[lang].ptr[row] itself (no title hook, unlike the KRAKEN and APRS rows -
 * which also means title_fn is NOT available for a row moved here).
 * The enter chain 0x030431cc dispatches items 0,1,2 and falls through at `cmpnei r3,3`; the splice at
 * 0x030431e6 hands item 3 back to stock and item 4 to krk_rs_item. The commit runs through a separate
 * chain: settings_menu_confirm_dispatch -> the Radio Settings arm 0x0304fab4, whose default edge
 * 0x0304fad0 is spliced to krk_rs_ok_stub (measured in QEMU with a breakpoint probe). */
#define RS_DESC      ((const volatile u8 *)0x030b8488)

#define RS_OPEN_MAGIC 0x52534f31u            /* "RSO1" */
static struct {
    u32 magic;
    u8 item;
} krk_rs_open;          /* plain .bss (kraken_ui_state), NOT .bss.krk_list: that section holds
                         * krk_list[]/krk_item_labels[], which the menu rewrites while the option
                         * level is open, and the latch was being clobbered (measured 2026-11-03). */

static void krk_rs_open_set(unsigned item)
{
    krk_rs_open.magic = RS_OPEN_MAGIC;
    krk_rs_open.item = (u8)item;
}

/* Radio Settings rows are listed by krk_hook_gen (mode KRK_RS_GEN_MODE). NOTE: the generator must NOT
 * clear the latch below. It also runs while the option level is being opened, so clearing it there
 * wipes what krk_rs_item() just latched and the commit finds nothing (measured 2026-11-03). Staleness is
 * handled in krk_rs_screen() instead, which checks the live level-2 item, so a latch left over from an
 * earlier row cannot claim a stock row's commit. */

/* Back from a screen row: put the cells back from the descriptor, as krk_calllog_restore does. */
static void krk_rs_restore(void)
{
    krk_desc_restore(RS_DESC);
}

static const struct krk_host rs_host = {KRK_RS_TABLE, krk_rs_restore, KRK_RS_FIRST, KRK_RS_MAX, KRK_RS_GEN_MODE, 1};

/* Which of our rows has its option level open, latched by krk_rs_item() when the level is opened.
 *
 * MEASURED 2026-11-03 (a breakpoint inside krk_open_opt): the level-3 row byte 0x3e4eb still reads 0
 * while the OK that commits is being handled, even though it reads our item once the keys have
 * settled. Deriving the row from menu state therefore resolved stock's item 0 and the commit was
 * silently skipped. The enter path does know the right item (the stock chain passes it in r3), so it
 * records it here and the commit reads the latch. Validated by a magic word because this is SRAM.
 * It lives in the main blob's own .bss, away from the list arrays the menu rewrites. */
static const struct krk_screen *krk_rs_screen(void)
{
    if (krk_rs_open.magic != RS_OPEN_MAGIC)
        return 0;
    /* Staleness guard: the latch is only meaningful while Radio Settings (Settings item 0) is the
     * open level-2 item. Anywhere else a leftover latch must not claim the row. */
    if (((volatile u8 *)0x0003e664)[*(volatile u8 *)0x0003e4ea] != 0)
        return 0;
    return krk_host_row(&rs_host, krk_rs_open.item);
}

/* OK on the row itself (enter): open its option level, or run it if it is a screen. The stub passes
 * the item it already holds in r3 rather than having us re-read the menu state. */
void krk_rs_item(unsigned item)
{
    const struct krk_screen *e = krk_host_row(&rs_host, item);

    krk_rs_open.magic = 0;                      /* a stock row or a screen of ours: no option level of ours is open */
    if (e && e->opt)
        krk_rs_open_set(item);                  /* the commit reads this back (see krk_rs_screen) */
    krk_row_open(e, krk_rs_restore);
}

/* OK inside the option level of one of our Radio Settings rows.
 *
 * Measured, 2026-11-03 (tests/settings_e2e.py + a breakpoint sweep): our rows do NOT commit through
 * settings_menu_confirm_dispatch @0x03050414, which is where STOCK Radio Settings rows commit. Stock
 * rows latch a field id at enter time and are dispatched by it; ours are set up by krk_opt_enter, so
 * OK runs the generic chain 0x0303a770 -> 0x0303a57c and falls out at the category-5 edge 0x0303ab38
 * (`movi r0,0x1c; movi r1,1; bsr set_mode`), the sibling of the category->=10 edge 0x0303ab42 that
 * opt_ok_hook already uses. That edge is spliced here.
 *
 * The first attempt hooked the stock arm instead and never ran at all - the e2e gate caught it. */
void krk_settings_opt_ok(void)
{
    /* This site is the OK handler of EVERY mode-0x1c level under Radio Settings: one of our option
     * levels, but also the stock Voice/Key/Display/Other Func row lists, where stock's displaced
     * set_mode(0x23, 1) is what opens the row. The latch alone cannot tell them apart - it is only
     * cleared when krk_rs_item() runs, which it does not for items 0..3 - so a row of ours opened
     * and left earlier would claim the OK pressed on, say, an Other Func row and commit itself with
     * that row's cursor (found by reading, 2026-11-06). An open option level of ours has radio
     * marks (icon mode 2, krk_opt_enter); the stock row lists are numbered (1, forced by the
     * level-4 render at 0x0303cd7c). */
    const struct krk_screen *e = *(volatile u8 *)0x00032ab6 == 2 ? krk_rs_screen() : 0;

    if (!e || !e->opt) {
        krk_rs_open.magic = 0;
        stock_set_mode(0x23, 1);                /* not one of ours: stock's own level-4 -> level-5 advance */
        return;
    }
    krk_opt_commit(e->opt, krk_rs_restore);
}

/* ---- the stock Channel Set sub-list (Settings = category 5, level-2 item 1) ------------------
 * docs/chanset-submenu.md. Channel Set is Settings item 1, so like Radio Settings its rows are
 * level 3 (depth 3, mode 0x15) and a row's option level is level 4 (depth 4, mode 0x1c). A row may instead be
 * the parent of a further list (Burst Voice, 2026-11-06): its two table entries are its own (menu_hosts.json "stubs") and its
 * level 5 is krk_bvcfg.c's (docs/chanset-submenu.md 8); krk_cs_item / krk_cs_opt_ok never see it.
 * Stock builds its 22 rows with
 * menu_option_list_dispatch(6, count, list) (body menu_option_gen_idx6 @0x03049930, a 21-arm
 * CPS/channel-mode visibility chain) from the menu-tree call site 0x03042406, spliced to
 * krk_hook_cs_gen so the stock gating keeps working and our rows are appended after it.
 *
 * Unlike Radio Settings, BOTH key events here are jump tables indexed by the level-3 row, so no
 * latch is needed - the row arrives in a register at each site (MEASURED 2026-11-03, below):
 *   OK on the ROW   (opens the level) -> 0x03047324, cmphsi r2,22 @0x03047332, table 0x030b0a18[row]
 *   OK IN the level (commits)         -> 0x0303a57c -> bsr 0x0304e560 -> 0x030b11c4[1] = 0x0304e5aa
 *                                        -> bsr 0x0304dc8c, cmphsi r3,22 @0x0304dc9a, table 0x030b116c[row]
 * The Radio Settings analogue of the second site (0x030b11c4[0] = 0x0304e5a0) was a pure no-op, so
 * its commit had to be taken over wholesale and the row recovered from a latch. Channel Set's is a
 * real dispatcher with its own per-row table, which is strictly better: both tables are relocated
 * with KRK_CS_MAX extra entries each, both bounds widened to 22 + KRK_CS_MAX, and each stub gets the
 * row in a register. (The commit bound matters more than the enter one: its out-of-range target
 * 0x0304e416 is NOT an epilogue but the stock numeric / Channel-Name path, so an un-grown bound would
 * silently run stock's numeric editor on one of our rows.)
 *
 * The header comes free as it does for Radio Settings: the level-4 header handler 0x03038720
 * special-cases category 5 and, for level-2 item 1, reads lrw a3,[0x30387c4] = 0x030b89a0 then
 * desc[lang].ptr[row], so relocating the label arrays titles the new rows in both languages. */
#define CS_DESC      ((const volatile u8 *)0x030b89a0)

/* Back from a screen row: put the cells back from the descriptor, as krk_rs_restore does. */
static void krk_cs_restore(void)
{
    krk_desc_restore(CS_DESC);
}

static const struct krk_host cs_host = {KRK_CS_TABLE, krk_cs_restore, KRK_CS_FIRST, KRK_CS_MAX, KRK_CS_GEN_MODE, 1};

/* OK on the row itself (enter): open its option level, or run it if it is a screen. Reached from
 * entry `item` of the relocated enter table 0x030b0a18; the stub passes the row it dispatched on. */
void krk_cs_item(unsigned item)
{
    krk_host_open(&cs_host, item);
}

/* OK inside the option level of one of our Channel Set rows. Reached from entry `item` of the
 * relocated commit table 0x030b116c, which the Settings commit dispatcher 0x0304e560 reaches
 * through its own table entry 1 (0x0304e5aa -> bsr 0x0304dc8c). Both tables index the same level-3
 * row, so unlike Radio Settings nothing has to be latched at enter time. */
void krk_cs_opt_ok(unsigned item)
{
    krk_host_commit(&cs_host, item);
}

/* ---- the row generators of every host that has one ------------------------------------------
 * The splice target at each hooked menu_option_list_dispatch call (Call Log x 3, Radio Settings,
 * Channel Set: the manifest's *_gen_* splices). Stock's generator runs FIRST, so all of its CPS and
 * channel-mode gating keeps working; then the host whose mode this is appends its rows. An unfilled
 * slot (or a blob that is not flashed) appends no row and does not hide the rows after it. */
static const struct krk_host *const gen_hosts[] = {&calllog_host, &rs_host, &cs_host};

void krk_hook_gen(u32 mode, volatile u16 *count, volatile u8 *list)
{
    unsigned i, k, n;

    menu_option_list_dispatch(mode, count, list);
    for (i = 0; i < sizeof gen_hosts / sizeof gen_hosts[0]; i++) {
        const struct krk_host *h = gen_hosts[i];

        if (h->gen != mode)
            continue;
        for (k = h->first; k < h->first + h->n; k++) {
            if (!krk_host_row(h, k))
                continue;
            n = *count;
            if (n >= LIST_ITEMS_MAX)
                return;
            list[n] = (u8)k;
            *count = (u16)(n + 1);
        }
    }
}

/* ---- screen runner ------------------------------------------------------------------------ */

/* Stock "Menu Exit Time" counter: counts up ~1/s while in a menu and, at the limit (0x0003e397),
 * posts UI event 100 (go to standby). Stock zeroes it on every dispatched UI event, and a screen
 * that parks the keypad produces none, so the runner zeroes it every tick (docs/menu-exit-timer.md). */
#define KEY_RAW_LAST  ((volatile u8 *)0x00030794)          /* stock poll debounce: last raw scan (0x30388 + 0x40c) */
#define KEY_RAW_COUNT ((volatile u8 *)0x00030795)          /* and its change counter (+0x40d) */

/* Stale UI events (docs/menu-exit-timer.md "UI freeze after exit (2026-11-01)"). This runner blocks "task menu" for the
 * whole screen, but the stock posters keep queueing UI events on its queue (keymsgq 0x00038d70) for as long as it
 * is away: write_field_and_mbox_post @0x03013178 posts kinds 1 and 3 unconditionally (only kind 2, the key
 * events, is dropped while no pend is outstanding, flag 0x0003078c). Back in the item-action tail, task menu
 * dequeues them one at a time AFTER the list was drawn; e.g. event 0x2c ("go to standby") runs
 * write_status_mode_byte_pair(0x5b, 1) with no repaint (dialog_history_ui_state ends `03 03 04 5b`), so the
 * list is on screen over a standby state, and the next MENU/Back freezes the display. The queue is emptied here
 * with stock's own pend (guarded_request_command_dispatch @0x0306a1b8 = OSQPend(q, timeout, opt, &size, &ts, &err),
 * called by keymsgq_command_dispatch_and_store @0x03013144) in non-blocking mode (opt 0x8000: an empty queue
 * returns 0 with err 0x61b0 at once). Every queued message is the same pointer (the event byte 0x00038e29): nothing to free here. */
#define OS_OPT_PEND_NON_BLOCKING ((short)0x8000)
#define KEYMSGQ_DRAIN_MAX 40                                  /* bounded: the queue is shallower than this */

static void krk_drain_keymsgq(void)
{
    u16 size;
    u32 err;
    int n;

    for (n = 0; n < KEYMSGQ_DRAIN_MAX; n++) {
        err = 0;
        if (!os_q_pend(KEYMSGQ_PTR, 0, OS_OPT_PEND_NON_BLOCKING, &size, 0, &err) || err)
            break;
    }
}

/* F14 RX Monitor arbitration: KRK_SCR_MAGIC while any screen runs (a screen may own the audio path, the FM RAM or the
 * transmitter; the monitor, running in the stock background task, stays out). Set BEFORE the yield hook so a monitor pass
 * that preempts between the two already sees it. */
volatile u32 krk_screen_owner;
volatile u32 krk_tags_shown;               /* standby tags last drawn (krk_tags.c, blob 2); here because blob 2's .bss claim is full */

u8 krk_key_scan(void)
{
    u8 scan;

    *KEYPAD_POLL_ENABLE = 0;
    *KEYPAD_BLANK_COUNTER = 0;
    MENU_EXIT_COUNTER = 0;
    os_sem_pend_ms(LCD_KEYPAD_BUS_SEM, 0);
    scan = keypad_scan_and_map_pressed_key();
    lcd_bus_restore_after_keypad_scan();
    os_sem_post(LCD_KEYPAD_BUS_SEM);
    return scan == SCAN_TIMEOUT ? SCAN_NONE : scan;
}

/* The exit protocol of every runner screen (v42, docs/aprs-submenu.md 7.4): on Back, or when a one-shot has finished. */
static void krk_leave(const struct krk_screen *s, void (*restore)(void))
{
    if (s->exit)
        s->exit();
    /* Exit in place (v42). The OK that opened us pushed a menu-history level and we return
     * into the stock item-action tail (0x0303d872 -> main_menu_screen_dispatch 0x0303b6c4),
     * which draws the active cells as that level. Earlier versions then handed a Back to the
     * stock poll to pop it, which cleared and redrew the same list a moment later (a visible
     * black frame on hardware, v41). Instead: pop the level here with stock's own pop
     * (history_queue_pop: cursor 0x3e5cc, highlight slot 0x3e5d5, index list, depths), put the
     * list's cells back, and let the tail draw it once. */
    history_queue_pop();
    if (restore)
        restore();
    else
        krk_category_entry();
    krk_drain_keymsgq();
    /* The physical Back is still down. Mark it as already dispatched (cur == prev == Back and
     * the debounce's last raw scan = Back) so the stock poll, re-enabled ~20 polls from now,
     * sees a held key, not a press; on release it runs only Back's release handler
     * (tx_state_poll_and_teardown @0x03014360), which posts nothing unless a standby-mode Back
     * press armed it. Then prev returns to 0 as after any key. A one-shot leaves with every key
     * released (it waited for that first), which the poll then reads as Back's release: the same. */
    *STOCK_KEY_CUR = SCAN_BACK;
    *STOCK_KEY_PREV = SCAN_BACK;
    *KEY_RAW_LAST = SCAN_BACK;
    *KEY_RAW_COUNT = 0;
    *KEYPAD_BLANK_REARM = 1;
    *KEYPAD_BLANK_COUNTER = 0;
    *KEYPAD_POLL_ENABLE = 0;
    krk_screen_owner = 0;
}

void krk_run_screen(const struct krk_screen *s, void (*restore)(void))
{
    u8 scan, held = SCAN_BACK;      /* the OK that opened us may still be down: ignore it */

    krk_screen_owner = KRK_SCR_MAGIC;
    krk_ext_hook(3);                /* RX Monitor: release GPIO 0x2F / AUDIO_CONTROL bit 6 now, before enter() samples them */
    MENU_EXIT_COUNTER = 0;
    if (s->flags & KRK_SF_ONESHOT) {
        int n;

        /* An action: no clear (its popups draw over the list), keypad parked for its whole run (krk_popup scans it). */
        *KEYPAD_POLL_ENABLE = 0;
        *KEYPAD_BLANK_COUNTER = 0;
        if (s->enter)
            s->enter();
        /* Leave only with every key up: a key still down would be read by the re-enabled stock poll as a fresh press
         * (the confirming OK would open the row again). Bounded: 40 x 50 ms. */
        for (n = 0; n < 40 && krk_key_scan() != SCAN_NONE; n++)
            os_time_dly_ms(50);
        krk_leave(s, restore);
        return;
    }
    display_fill_screen(KRK_BG);
    if (s->enter)
        s->enter();
    for (;;) {
        /* own the keypad: keep the stock poll parked, scan under the bus semaphore, and give
         * the LCD pads back before any draw (dm32_patch_api.h, Snake v14/v22) */
        scan = krk_key_scan();
        if (scan == SCAN_BACK) {
            krk_leave(s, restore);
            return;
        }
        /* screens see presses, not held keys: report a key once, when it goes down */
        if (scan == held) {
            scan = SCAN_NONE;
        } else {
            held = scan;
        }
        if (s->tick)
            s->tick(scan);
        os_time_dly_ms(s->tick_ms ? s->tick_ms : KRK_TICK_MS);
    }
}

/* ---- text helpers ------------------------------------------------------------------------- */

void krk_text(int x, int y, const char *s, u16 fg)
{
    /* The stock routine takes (ROW, COLUMN), despite the API header's (x, y) names: its first
     * argument steps by 24 on wrap, its second by 12 per ASCII glyph and wraps past 228
     * (decompiled display_draw_string_gb2312 @0x03018998; QEMU run out/qemu/about, 2026-10-15). */
    display_draw_string_gb2312(y, x, s, fg, KRK_BG);
}

void krk_line(int n, const char *s, u16 fg)
{
    char t[KRK_COLS + 1];
    int i;

    /* clear the whole line first so a shorter string leaves no stale glyphs; draw at most one
     * line's worth, since the stock renderer wraps a longer string onto the next line */
    for (i = 0; i < KRK_COLS && s[i]; i++)
        t[i] = s[i];
    t[i] = 0;
    display_fill_rect(0, KRK_Y0 + n * KRK_LINE_H, KRK_SCREEN_W, KRK_Y0 + (n + 1) * KRK_LINE_H, KRK_BG);
    krk_text(KRK_X0, KRK_Y0 + n * KRK_LINE_H, t, fg);
}

char *krk_itoa(int v, char *buf)
{
    char tmp[12];
    int i = 0, j = 0;
    unsigned u = v < 0 ? (unsigned)-v : (unsigned)v;

    do {
        tmp[i++] = (char)('0' + u % 10);
        u /= 10;
    } while (u);
    if (v < 0)
        buf[j++] = '-';
    while (i)
        buf[j++] = tmp[--i];
    buf[j] = 0;
    return buf;
}

char *krk_catn(char *dst, unsigned size, const char *src)
{
    char *p = dst, *end = dst + size - 1;

    while (*p && p < end)
        p++;
    while (*src && p < end)
        *p++ = *src++;
    *p = 0;
    return dst;
}

char *krk_cat(char *dst, const char *src)
{
    return krk_catn(dst, KRK_LINE_BUF, src);
}

char *krk_cati(char *dst, int v)
{
    char num[12];

    return krk_cat(dst, krk_itoa(v, num));
}

/* v / div as decimal with log10(div) fraction digits (div = 1, 10, 100, ...; 1 = no point), the integer part padded
 * with spaces to w digits, a leading '-' for v < 0. krk_catfix(l, 1234, 100, 0) = "12.34". Built backwards. */
char *krk_catfix(char *dst, int v, int div, int w)
{
    char t[24], *p = t + sizeof t - 1;
    unsigned u = v < 0 ? (unsigned)-v : (unsigned)v;
    int fd, i = 0;

    for (fd = 0; div > 1; div /= 10)
        fd++;
    *p = 0;
    do {
        *--p = (char)('0' + u % 10);
        u /= 10;
        if (++i == fd)
            *--p = '.';
    } while (u || i <= fd);
    for (; i - fd < w; i++)
        *--p = ' ';
    if (v < 0)
        *--p = '-';
    return krk_cat(dst, p);
}

/* Pads buf (KRK_LINE_BUF bytes) with spaces to the full KRK_COLS width, and cuts it there: a redraw over the old line leaves no stale glyphs. */
void krk_pad(char *buf)
{
    int i;

    for (i = 0; buf[i] && i < KRK_COLS; i++)
        ;
    while (i < KRK_COLS)
        buf[i++] = ' ';
    buf[KRK_COLS] = 0;
}

/* Draws line n padded to the full width, only when its text or colour changed (no flicker at the tick rate); hash[n]
 * remembers the last drawn FNV-1a. */
void krk_line_cached(u32 *hash, int n, const char *s, u16 fg)
{
    char buf[KRK_LINE_BUF];
    u32 h = 2166136261u ^ fg;
    int i;

    buf[0] = 0;
    krk_catn(buf, sizeof buf, s);
    krk_pad(buf);
    for (i = 0; buf[i]; i++)
        h = (h ^ (u8)buf[i]) * 16777619u;
    if (h == hash[n])
        return;
    hash[n] = h;
    krk_text(KRK_X0, KRK_Y0 + n * KRK_LINE_H, buf, fg);
}

/* Seconds as "12s" / "5m" / "3h" into out (12 bytes). */
void krk_fmt_age(char *out, u32 sec)
{
    char c[2] = { 's', 0 };

    if (sec >= 60) {
        sec /= 60;
        c[0] = 'm';
        if (sec >= 60) {
            sec /= 60;
            c[0] = 'h';
        }
    }
    krk_itoa((int)sec, out);
    krk_catn(out, 12, c);
}

/* ---- UART0 (the DesignWare 16550 debug port, 115200 8N1) ---------------------------------------- */

void krk_uart_putc(u8 c)
{
    int guard = 0;

    /* stock sets FCR PTIME+FIFO, so LSR[5] is "TX FIFO full": wait for CLEAR (boot-ROM polarity) */
    while ((UART0_LSR & 0x20) && ++guard < 200000)
        ;
    UART0_THR = c;
}

void krk_uart_puts(const char *s)
{
    while (*s)
        krk_uart_putc((u8)*s++);
}

/* v as `digits` upper-case hex digits (at most 8) into out (digits + 1 bytes). */
char *krk_hex(char *out, u32 v, int digits)
{
    int i;

    for (i = 0; i < digits; i++)
        out[i] = "0123456789ABCDEF"[(v >> (4 * (digits - 1 - i))) & 15];
    out[digits] = 0;
    return out;
}
