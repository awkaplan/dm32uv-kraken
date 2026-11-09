/*
 * krk_bvcfg.c - Burst Voice settings: their flash record and their stock-style sub-menu under Channel Set. Blob 2.
 * docs/burst-voice.md 11.3 and 13, docs/chanset-submenu.md 8, docs/nested-submenu.md 7.
 *
 *   Settings > Channel Set > Burst Voice > Burst Voice    the screen (blob ui4), run from the level-5 init
 *                                        > Mode           Fast / Robust
 *                                        > Key-up Lead    eight leads, 0 .. 960 ms
 *                                        > Relay Capture  ON / OFF
 *
 * How the nest works (static trace of the stock image, 2026-11-06; the model of docs/otherfunc-submenu.md 1): a level's cells
 * are filled by that level's RENDER entry, which runs after every OK handler; an OK handler only calls set_mode(next, 1).
 *   level 4 (mode 0x1c), the child list: render 0x0303cbc4 -> 0x03047d44 -> Channel Set's level-4 init 0x03047324, which jumps
 *       through the relocated per-row table. The Burst Voice row's entry is krk_cs_bv_kids_stub (menu_hosts.json "stubs"):
 *       the child list, without the cursor (0x03036a48 zeroes it on a fresh entry, the history pop restores it after).
 *   OK at level 4: 0x0303a57c -> 0x0304e560 -> 0x0304dc8c -> the relocated commit table; the row's entry is stock's own
 *       0x0304e254 (`set_mode(0x23, 1)`, the arm of stock's nested row 15), which tests nothing.
 *   level 5 (mode 0x23): render 0x0303cb54 -> 0x0304547c -> Channel Set's level-5 init 0x03045084, a 13-entry table for
 *       rows 3..15 behind `cmphsi r2,13; bt exit` at 0x03045094 (spliced: krk_cs_l5_enter);
 *   OK at level 5: 0x03036420 -> 0x03050414 -> 0x030500b0, a compare chain on the row ending `cmpnei r3,15; bt exit` at
 *       0x0305031a (spliced: krk_cs_l5_ok);
 *   the level-5 header 0x03038994, Channel Set arm 0x03038a7a, ends `cmpnei r3,15; bt blank` at 0x03038ab8 (spliced:
 *       krk_cs_l5_title). The three stubs are in krk_ofstubs.S, the sites in hooks.json.
 *
 * The record: 16 bytes in stock block 4 at 0xFC0 (docs/settings-field.md 3.3: the free span 0xF10-0xFEF; 0xFD0 is Freq
 * Unlock's record, 0xFE0 the callsign's), magic + checksum (krk_rec_csum, seed 0x3c). Written through stock's
 * flash_block_write on OK and only when the value changes. A factory reset (00) or a vendor CPS write (FF) fails the check and
 * the defaults apply: Fast, 320 ms, relay capture off. Read on every get (16 bytes; no RAM is free in this blob's claims).
 */
#include "krk.h"
#include "krk_bv.h"
#include "krk_stock.h"

#define BVC_MAGIC   0x31435642u         /* "BVC1" */
#define BVC_BLOCK   4
#define BVC_OFF     0xFC0
#define BVC_SEED    0x3c
#define LEAD_MAX    30                  /* 30 x 32 ms = 960 ms (krk_bvscr.c) */
#define LEAD_DEF    10                  /* 320 ms */

struct bvc_rec {
    u32 magic;
    u8 v[3];                            /* [0] mode BV_MODE_*, [1] key-up lead in 32 ms units, [2] relay capture 0 / 1 */
    u8 pad[8];
    u8 csum;
};
typedef char bvc_rec_is_16_bytes[sizeof(struct bvc_rec) == 16 ? 1 : -1];

static const u8 bvc_max[3] = { 1, LEAD_MAX, 1 };

/* The record, or the defaults when flash holds anything else. Returns 1 when it was valid. */
static int rec_load(struct bvc_rec *r)
{
    unsigned i;

    flash_block_read(flash_block_id_resolve_locked(BVC_BLOCK), BVC_OFF, r, sizeof *r);
    if (r->magic == BVC_MAGIC && r->csum == krk_rec_csum(r, BVC_SEED)) {
        for (i = 0; i < 3 && r->v[i] <= bvc_max[i]; i++)
            ;
        if (i == 3)
            return 1;
    }
    for (i = 0; i < sizeof *r; i++)
        ((u8 *)r)[i] = 0;
    r->magic = BVC_MAGIC;
    r->v[0] = BV_MODE_FAST;
    r->v[1] = LEAD_DEF;
    return 0;
}

/* mode | lead << 8 | relay << 16 (KRK_BVC_* in krk.h): what the screen reads at enter. */
u32 krk_bv_cfg(void)
{
    struct bvc_rec r;

    rec_load(&r);
    return (u32)r.v[0] | (u32)r.v[1] << 8 | (u32)r.v[2] << 16;
}

static void rec_set(unsigned field, u8 val)
{
    struct bvc_rec r;

    if (rec_load(&r) && r.v[field] == val)
        return;                         /* unchanged: no flash write */
    r.v[field] = val;
    r.csum = krk_rec_csum(&r, BVC_SEED);
    flash_block_write(BVC_BLOCK, BVC_OFF, &r, sizeof r);
}

/* ---- the three option levels ---- */
static const char *const mode_labels[2] = { "Fast", "Robust" };
static const u8 lead_vals[8] = { 0, 4, 6, 8, 10, 15, 20, 30 };     /* x 32 ms */
static const char *const lead_labels[8] = { "0 ms", "128 ms", "192 ms", "256 ms", "320 ms", "480 ms", "640 ms", "960 ms" };

static int mode_get(void)
{
    return KRK_BVC_MODE(krk_bv_cfg()) == BV_MODE_FAST ? 0 : 1;
}

static void mode_set(int idx)
{
    rec_set(0, idx ? BV_MODE_ROBUST : BV_MODE_FAST);
}

static int lead_get(void)
{
    return krk_nearest8(lead_vals, KRK_BVC_LEAD(krk_bv_cfg()));
}

static void lead_set(int idx)
{
    rec_set(1, lead_vals[idx & 7]);
}

static int relay_get(void)
{
    return KRK_BVC_RELAY(krk_bv_cfg()) ? 0 : 1;
}

static void relay_set(int idx)
{
    rec_set(2, idx == 0);
}

#define BV_KIDS 4
static const char *const kid_labels[BV_KIDS] = { "Burst Voice", "Mode", "Key-up Lead", "Relay Capture" };
static const struct krk_opt kid_opt[BV_KIDS - 1] = {
    { .labels = mode_labels, .n = 2, .get = mode_get, .set = mode_set },
    { .labels = lead_labels, .n = 8, .get = lead_get, .set = lead_set },
    { .labels = krk_onoff_labels, .n = 2, .get = relay_get, .set = relay_set },
};

/* ---- the nest ---- */

/* Level-4 init of the Burst Voice row (its entry in the relocated Channel Set enter table): the child list. Runs on every render
 * of the level, the re-render after a child's commit or Back included, so it must not write the cursor. Also the screen's restore. */
void krk_cs_bv_kids(void)
{
    krk_list_cells(kid_labels, BV_KIDS);
    MENU_ROW_ICON = 1;
    *ACTIVE_VISIBLE_ROWS = BV_KIDS;
}

/* The child open at level 5 when the level-3 row is Burst Voice, else -1. The three hooks sit in Channel Set-only code. */
static int bv_child(void)
{
    unsigned c = MENU_SAVED_LIST(4)[MENU_ROW_AT(4)];

    if (MENU_SAVED_LIST(3)[MENU_ROW_AT(3)] != KRK_CS_ITEM_BV || c >= BV_KIDS)
        return -1;
    return (int)c;
}

static void no_restore(void)
{
    /* the pop leaves the redraw flag set, and the level-4 init refills the child list */
}

/* Level-5 init (0x03045094): a setting's option level, or the screen, which blocks until Back and leaves through the runner's
 * pop with the child list reinstalled before the stock draw that follows this init. */
void krk_cs_l5_enter(void)
{
    int c = bv_child();

    if (c == 0)
        krk_run_screen((const struct krk_screen *)KRK_EXT4_LO, krk_cs_bv_kids);
    else if (c > 0)
        krk_opt_enter(&kid_opt[c - 1]);
}

/* Level-5 OK (0x0305031a): commit, "Setting is OK", stock's pop back to the child list. */
void krk_cs_l5_ok(void)
{
    int c = bv_child();

    if (c > 0)
        krk_opt_commit(&kid_opt[c - 1], no_restore);
}

/* Level-5 header (0x03038ab8): the child's label, or 0 for stock's blank. */
const char *krk_cs_l5_title(void)
{
    int c = bv_child();

    return c >= 0 ? kid_labels[c] : 0;
}
