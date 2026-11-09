/*
 * krk_optrow.c - settings and one-shot actions in the STOCK menu idiom (docs/stock-option-level.md).
 *
 * A. Option level. The genuine stock third level, e.g. Settings > Radio Settings > Other Func > Auto PowerOff: the stock generic list
 *    (subtype 2) drawn with a radio mark per row. Stock enters it from an item-action case block that does nothing but fill the list
 *    cells (APRS "Upload Type", case block 0x0303d634) and RETURN; the stock key handlers then do Up/Down/Back, and OK runs the
 *    category's commit handler (APRS: 0x0304d6c0 -> commit, redraw the marks 0x03041598, "Setting is OK" popup + history pop 0x0303a4dc).
 *    We do the same for our category:
 *      krk_opt_enter  the item-action: fill the cells (count, label array, subtype 2, index list, row-icon mode 0x32ab6 = 2, cursor,
 *                     highlight slot, "current value" cell 0x3e91f), return;
 *      krk_opt_title  header text (stock: a per-category string table; category 10's entry is the empty string);
 *      krk_opt_ok     OK at that level: commit through the setter, then the stock marks redraw and "Setting is OK" popup (which pops).
 *    Back is the stock Back: it pops the level and re-runs the category's render path (krk_category_entry), so the list returns with
 *    the cursor where it was. Nothing here blocks.
 *
 * B. Popups. krk_popup_draw / krk_popup draw the stock popup box with stock primitives (0x03019be8 box, 0x03039034 bitmap, 0x03018b5c
 *    centered text): used by KRK_SF_ONESHOT rows (krk_core.c runner) to say what an action did.
 *
 * C. Numeric entry. A struct krk_opt with labels == NULL opens the stock text-entry screen instead (subtype 8, the APRS "Input ID" editor):
 *    same three hooks (num_enter / krk_num_title / num_commit), see below. The first shipped customer is Scrambler Key (krk_scramble.c).
 *
 * The option rows reach this code from krk_core.c (krk_item_ok, krk_aprs_item) and from four splices (kraken_ui.patch.json:
 * opt_ok_hook, opt_title_hook, aprs_opt_ok_hook, opt_numtitle_hook; stubs in krk_stubs.S).
 */
#include "krk.h"
#include "krk_stock.h"

#define OPT_ICON_MODE   (*(volatile u8 *)0x00032ab6)    /* row icon of the subtype-2 list: 0 text/bitmap, 1 numbered box, 2 radio mark (0x03039e64) */
#define OPT_CURRENT     (*(volatile u8 *)0x0003e91f)    /* the row that carries the dot in radio mode (0x03039f3c compares it with the row) */
#define LIST_CURSOR     (*(volatile u8 *)0x0003e5cc)    /* absolute highlighted item */
#define LIST_SLOT       (*(volatile u8 *)0x0003e5d5)    /* 1-based highlight position inside the window (top = cursor + 1 - slot) */
#define stock_marks_redraw ((void (*)(void))0x03041598) /* menu_row_highlight_draw: re-draws the radio marks (dot on the cursor row) */
#define stock_setting_ok   ((void (*)(void))0x0303a4dc) /* "Setting is OK" popup, 1 s, then history_queue_pop */
#define STOCK_EMPTY_TITLE  ((const char *)0x030b1458)

/* ---- numeric entry: the stock "Input ID" editor (APRS item 1, case block 0x0303d5de, OK commit 0x0304d6c0 item 1) ------------------
 * Stock entry: cells from a RAM descriptor (count 1, subtype 8 = text entry), [0x3e31e] bit 6 set (editing), the current value formatted
 * "%d" into the edit buffer 0x3ee84 (at most 8 characters, NUL at [8]), its length into 0x3edf6, the cursor's pixel column
 * 12 * length + 5 into 0x3ee72; then the shared tail draws it. Digits, Back (= delete) are the stock key handler's. OK: the handler parses the
 * buffer, range-checks and commits, then the "Setting is OK" popup (or the "Out of Range" dialog, 1.5 s) and the history pop. */
#define EDIT_BUF     ((volatile char *)0x0003ee84)
#define EDIT_LEN     (*(volatile u8 *)0x0003edf6)
#define EDIT_CURSOR  (*(volatile u16 *)0x0003ee72)
#define EDIT_FLAGS   (*(volatile u8 *)0x0003e31e)       /* bit 6: a text edit is open (0x0303d5f2) */
#define EDIT_FRESH   (*(volatile u8 *)0x000320b0)       /* dialog_history_ui_state[4] == 1: reset the edit state first (0x0303d600) */
#define stock_edit_reset ((void (*)(void))0x030596b0)
static const char *const num_cells[1] = {""};

static void num_enter(const struct krk_opt *o)
{
    char t[16];
    unsigned i = 0, n = o->n;

    if (n < 1 || n > 8)
        n = 8;
    *ACTIVE_ITEM_COUNT = 1;
    *ACTIVE_OPTION_PTR_ARRAY = (u32)num_cells;
    *ACTIVE_SCREEN_SUBTYPE = 8;
    EDIT_FLAGS |= 0x40;
    if (EDIT_FRESH == 1)
        stock_edit_reset();
    t[0] = 0;
    krk_itoa(o->get(), t);
    while (t[i] && i < n) {
        EDIT_BUF[i] = t[i];
        i++;
    }
    EDIT_BUF[i] = 0;
    EDIT_LEN = (u8)i;
    EDIT_CURSOR = (u16)(12 * i + 5);
}

static void num_commit(const struct krk_opt *o, void (*restore)(void))
{
    u32 v = 0;
    unsigned i, len = EDIT_LEN;

    for (i = 0; i < len && i < 8; i++)
        v = v * 10 + (u32)(EDIT_BUF[i] - '0');
    EDIT_FLAGS &= (u8)~0x40;
    if (len == 0 || v < o->min || v > o->max) {
        krk_popup_draw(KRK_POP_ERR, "Out of Range", 0);
        os_time_dly_ms(1500);
        history_queue_pop();
    } else {
        o->set((int)v);
        stock_setting_ok();
    }
    restore();
}

void krk_opt_enter(const struct krk_opt *o)
{
    unsigned n = o->n, cur;

    if (!o->labels) {
        num_enter(o);
        return;
    }

    if (n < 2)
        n = 2;
    if (n > 8)
        n = 8;
    cur = (unsigned)o->get();
    if (cur >= n)
        cur = 0;
    krk_list_cells(o->labels, n);
    *ACTIVE_VISIBLE_ROWS = (u8)n;
    OPT_ICON_MODE = 2;
    LIST_CURSOR = (u8)cur;
    OPT_CURRENT = (u8)cur;
    LIST_SLOT = (u8)(cur + 1);                  /* n <= 8: the window is the whole list, so slot = cursor + 1 */
}

const char *krk_opt_title(void)
{
    const struct krk_screen *s = krk_row_screen(KRK_SELECTED_ROW);

    if (!s || !s->opt)
        return STOCK_EMPTY_TITLE;
    if (s->opt->title_fn)
        return s->opt->title_fn();
    return s->opt->title ? s->opt->title : s->label;
}

/* Header of the text-entry screen (subtype 8). Stock draws one string per language, "Input ID", whenever the edit flag
 * [0x3e31e] bit 6 is set (0x0303bb30: `lrw r3,0x030ba280; ldr.w r3,(r3,r12<<2)`); ours when the open level is one of our numeric rows. */
#define STOCK_INPUT_ID_TITLES ((const char *const *)0x030ba280)
const char *krk_num_title(void)
{
    const struct krk_opt *o = krk_open_opt();

    if (o && !o->labels && o->title)
        return o->title;
    return STOCK_INPUT_ID_TITLES[UI_LANG_IS_ENGLISH()];
}

/* Commit option idx of o (the cursor), stock-style: setter, marks, "Setting is OK" popup + history pop, then the list's cells again. */
void krk_opt_commit(const struct krk_opt *o, void (*restore)(void))
{
    unsigned idx = LIST_CURSOR;

    if (!o->labels) {
        num_commit(o, restore);
        return;
    }
    if (idx >= o->n)
        idx = 0;
    if (o->confirm && !o->confirm((int)idx)) {
        history_queue_pop();                    /* refused: leave the level as stock_setting_ok would, without its popup */
        restore();
        return;
    }
    o->set((int)idx);
    OPT_CURRENT = (u8)idx;
    if (!o->confirm)                            /* a confirm cleared the level: no marks to draw before the popup */
        stock_marks_redraw();
    stock_setting_ok();                         /* popup, then stock pops the level */
    if (o->after)
        o->after((int)idx);
    restore();                                  /* the list's cells, in case nothing re-runs the render path after the pop */
}

void krk_opt_ok(void)
{
    const struct krk_screen *s = krk_row_screen(KRK_SELECTED_ROW);

    if (!s || !s->opt) {
        stock_set_mode(0x1c, 1);                /* what stock does here for a category without a commit handler */
        return;
    }
    krk_opt_commit(s->opt, krk_category_entry);
}

/* ---- popups ---------------------------------------------------------------------------------------------------------------- */
#define popup_box     ((void (*)(void))0x03019be8)                               /* 194 x 160 grey box, 0xc618 */
#define popup_bitmap  ((void (*)(u32 x, u32 y, u32 w, u32 h, const void *rgb565))0x03039034)
#define popup_text    ((void (*)(u32 y, u32 fg, u32 bg, const char *s))0x03018b5c)
#define POPUP_GREY    0xc618

static const u32 popup_icons[4] = {0x0309df80, 0x0309cd80, 0x0309bb80, 0x0309a980};

void krk_popup_draw(int kind, const char *line1, const char *line2)
{
    popup_box();
    popup_bitmap(96, 86, 48, 48, (const void *)popup_icons[(unsigned)kind & 3]);
    popup_text(144, 0, POPUP_GREY, line1);
    if (line2 && line2[0])
        popup_text(174, 0, POPUP_GREY, line2);
}

u8 krk_popup(int kind, const char *line1, const char *line2, unsigned ms)
{
    u32 t0;
    u8 held, k;

    krk_popup_draw(kind, line1, line2);
    if (!ms)
        return SCAN_NONE;
    held = krk_key_scan();                      /* a key already down does not count until it has been released */
    t0 = OS_TICKS;
    while ((u32)(OS_TICKS - t0) < ms / 10) {    /* 100 Hz ticks; read only: every pass yields in os_time_dly_ms */
        os_time_dly_ms(50);
        k = krk_key_scan();
        if (k == SCAN_NONE)
            held = SCAN_NONE;
        else if (k != held)
            return k;
    }
    return SCAN_NONE;
}
