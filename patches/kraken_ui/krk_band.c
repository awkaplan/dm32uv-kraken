/*
 * krk_band.c - Freq Unlock: Settings > Radio Settings > Freq Unlock (ON / OFF), docs/freq-unlock.md.
 *
 * ON widens the radio to the edges published for DM-32 firmware range mods (infotex58 DM-32 thread, 2025: the
 * "23-136 / 136-279 / 280-520 MHz" mod and its variants):
 *   RX and keypad entry  23-520 MHz: 23-136 AM (stock's airband slot, extended down), 136-280 the VHF LNA, 280-520 the UHF LNA;
 *   TX                   136-520 MHz: 136-280 the VHF PA, 280-520 the UHF PA.
 * Below 136 MHz the radio stays receive-only: stock's channel-change dispatcher marks every channel whose RX lies in the AM slot
 * "may not transmit" (flags8 bit 3), and owners report TX there does not work. Off-band TX power uses the nearest calibration point
 * (the stock interpolators clamp to their table), so it is uncalibrated.
 *
 * How (both stock, unchanged except for the numbers):
 *   - RX: every RX range check and the keypad entry check read one RAM table, 0x00038470 (u32 x 10, 10 Hz units), filled at boot by
 *     rf_config_load_from_flash_block2 @0x0300d568: [0]/[1] UHF, [2]/[3] VHF, [8]/[9] the AM slot ([4]..[7] are the calibration span,
 *     left alone). krk_hook_band_load (splice band_load_hook, its only call) runs the stock load, then writes the wide edges.
 *   - TX: frequency_band_range_check_and_status_set @0x0300d84c tests TX against literal ranges of the model byte and sets 0x3e31b
 *     bit 4 (TX on the UHF PA) and bit 5 (TX not allowed, which ptt_press_handler refuses). krk_hook_band_check (splices
 *     band_check_hook_1..4, all of its stock calls) runs it, then rewrites those two bits from the wide range.
 * OFF puts the stock edges back from the loader's own literals; stock code is not re-run.
 *
 * Turning it ON needs a 10 s hold of OK on a legal warning (band_confirm, the option row's confirm hook in krk_optrow.c). OFF is
 * immediate. Stored in flash: a 16-byte record of its own in block 4 at 0xFD0 (docs/settings-field.md 3.3, candidate 2), written
 * through stock's flash_block_write on change only. A vendor CPS write (FF) or factory reset (00) fails the check: OFF.
 * RAM: one word, at the tail of the AFSK claim (blob 2's .bss and .bss.feat are full), set from flash by the boot hook.
 */
#include "krk.h"

#ifndef flash_block_read             /* host tests substitute these (tests/test_band.py) */
#include "krk_stock.h"
#define stock_band_load   ((void (*)(void))0x0300d568)              /* rf_config_load_from_flash_block2 */
#define BAND_TBL          ((volatile u32 *)0x00038470)
#define FLAGS3            BAND_FLAGS                                /* radio_mode_state + 3: bit 4 TX UHF PA, bit 5 TX not allowed (the test substitutes this name) */
#define LIVE_TX           TX_FREQ10                                 /* the receiver's live words, not the loaded channel's (krk_core.c CHAN_*_FREQ) */
#define LIVE_RX           RX_FREQ10
/* Stock edges, from the loader's literal pool (manifest asserts band_lit_*): 400.0, 470.0, 174.0, 108.0 MHz. 136.0 is an immediate. */
#define STOCK_UHF_LO      (*(const volatile u32 *)0x0300d7fc)
#define STOCK_UHF_HI      (*(const volatile u32 *)0x0300d800)
#define STOCK_VHF_HI      (*(const volatile u32 *)0x0300d804)
#define STOCK_AM_LO       (*(const volatile u32 *)0x0300d808)
#endif
#define STOCK_VHF_LO      13600000u

/* The wide edges, 10 Hz units. VHF ends one step below the UHF start so the two LNA/PA paths never overlap. */
#define W_AM_LO   2300000u      /*  23 MHz */
#define W_VHF_HI  27999999u     /* 279.99999 MHz */
#define W_UHF_LO  28000000u     /* 280 MHz */
#define W_UHF_HI  52000000u     /* 520 MHz */
#define W_TX_LO   13600000u     /* 136 MHz: no TX in the AM slot */

#define B_ON_MAGIC  0x4e4f4246u /* "FBON": the RAM word when ON; anything else (power-up noise included) is OFF */
#define REC_MAGIC   0x4c4e5546u /* "FUNL" */
#define REC_BLOCK   4
#define REC_OFF     0xFD0

#ifndef __APPLE__                   /* host tests on macOS: Mach-O has no ELF section names */
#define BAND_BSS __attribute__((section(".bss.afsk")))
#else
#define BAND_BSS
#endif
static volatile u32 band_on BAND_BSS;

struct band_rec {
    u32 magic;
    u8 on, inv;                     /* on is 0 or 1, inv = ~on */
    u8 pad[9];
    u8 csum;
};
typedef char band_rec_is_16_bytes[sizeof(struct band_rec) == 16 ? 1 : -1];

static u8 rec_csum(const struct band_rec *r)
{
    return krk_rec_csum(r, 0xa5);       /* krk_callsign.c's record checksum (blob ui3), other seed */
}

static int rec_read_on(void)
{
    struct band_rec r;

    flash_block_read(flash_block_id_resolve_locked(REC_BLOCK), REC_OFF, &r, sizeof r);
    return r.magic == REC_MAGIC && r.on <= 1 && r.inv == (u8)~r.on && r.csum == rec_csum(&r) && r.on;
}

static void rec_write_on(int on)
{
    struct band_rec r;
    unsigned i;

    if (rec_read_on() == on)
        return;                             /* unchanged: no flash write */
    r.magic = REC_MAGIC;
    r.on = (u8)(on ? 1 : 0);
    r.inv = (u8)~r.on;
    for (i = 0; i < sizeof r.pad; i++)
        r.pad[i] = 0;
    r.csum = rec_csum(&r);
    flash_block_write(REC_BLOCK, REC_OFF, &r, sizeof r);
}

int krk_band_on(void)
{
    return band_on == B_ON_MAGIC;
}

static void table_apply(int on)
{
    volatile u32 *t = BAND_TBL;

    t[0] = on ? W_UHF_LO : STOCK_UHF_LO;
    t[1] = on ? W_UHF_HI : STOCK_UHF_HI;
    t[2] = on ? W_AM_LO : STOCK_VHF_LO;     /* the keypad entry check tests [0..3] only: VHF must start at 23 MHz for 23-136 */
    t[3] = on ? W_VHF_HI : STOCK_VHF_HI;
    t[8] = on ? W_AM_LO : STOCK_AM_LO;      /* the band check tests the AM slot first, so 23-136 is AM (and receive-only) */
}

/* Splice band_load_hook (radio_boot_full_init_sequence's call of the loader). Runs before the startup channel check. */
void krk_hook_band_load(void)
{
    stock_band_load();
    band_on = rec_read_on() ? B_ON_MAGIC : 0;
    if (krk_band_on())
        table_apply(1);
}

/* Splices band_check_hook_1..4: every stock call of the band check. */
void krk_hook_band_check(u32 rx, u32 tx)
{
    u8 f;

    stock_band_check(rx, tx);
    if (!krk_band_on())
        return;
    f = FLAGS3 & (u8)~0x30;
    if (tx >= W_UHF_LO)
        f |= 0x10;                          /* UHF PA */
    if (tx < W_TX_LO || tx > W_UHF_HI)
        f |= 0x20;                          /* TX not allowed */
    FLAGS3 = f;
}

static void band_set_on(int on)
{
    rec_write_on(on);
    band_on = on ? B_ON_MAGIC : 0;
    table_apply(on);
    krk_hook_band_check(LIVE_RX, LIVE_TX);  /* the loaded channel's bits now; a channel change recomputes them anyway */
}

/* ---- the warning: hold OK for 10 s ---------------------------------------------------------------------------------------------- */

#define HOLD_TICKS    1000u     /* 10 s */
#define IDLE_TICKS    6000u     /* 60 s without a completed hold: cancelled */
#define RELEASE_TICKS 3000u     /* after the hold: wait this long at most for OK to come up ("Release OK"), so the stock poll
                                 * does not get a held key back */

static const char *const warn_text[9] = {
    "!! FREQ UNLOCK !!",
    "Transmitting off",
    "your licensed bands",
    "is illegal and can",
    "jam emergency and",
    "aviation users.",
    "Off-band TX is",
    "unfiltered. At your",
    "own risk. Back=exit",
};

/* Stale UI events queued while this blocks the menu task (as the screen runner drains them, krk_core.c). */
static void drain_keymsgq(void)
{
    u16 size;
    u32 err;
    int n;

    for (n = 0; n < 40; n++) {
        err = 0;
        if (!os_q_pend(KEYMSGQ_PTR, 0, (short)0x8000, &size, 0, &err) || err)
            break;
    }
}

static void hold_status(int left_s)
{
    char t[KRK_COLS + 1];

    if (left_s < 0) {
        krk_line(9, "Hold OK 10 s: ON", KRK_ACCENT);
        return;
    }
    t[0] = 0;
    krk_catn(t, sizeof t, "Holding... ");
    krk_itoa(left_s, t + 11);
    krk_catn(t, sizeof t, " s");
    krk_line(9, t, KRK_WARN);
}

/* Returns 1 once OK has been held for HOLD_TICKS, pressed fresh after the warning appeared; 0 on Back or IDLE_TICKS without one. */
static int hold_ok(void)
{
    u32 start = OS_TICKS, t0 = 0, now;
    int stale = 1, holding = 0, shown = -2, left, ok = 0, i;
    u8 k;

    display_fill_screen(KRK_BG);
    for (i = 0; i < 9; i++)
        krk_line(i, warn_text[i], i == 0 ? KRK_WARN : KRK_FG);
    for (;;) {
        k = krk_key_scan();                 /* parks the stock poll and zeroes the menu-exit counter */
        now = OS_TICKS;
        if (k == SCAN_BACK)
            break;
        if (k == SCAN_OK && !stale) {
            if (!holding) {
                holding = 1;
                t0 = now;
            }
            if (now - t0 >= HOLD_TICKS) {
                ok = 1;
                break;
            }
            left = (int)((HOLD_TICKS - (now - t0) + 99) / 100);
        } else {
            holding = 0;
            if (k == SCAN_NONE)
                stale = 0;                  /* the OK that chose ON, or any other key, has come up */
            if (now - start >= IDLE_TICKS)
                break;
            left = -1;
        }
        if (left != shown) {
            hold_status(left);
            shown = left;
        }
        os_time_dly_ms(50);
    }
    if (ok)
        krk_line(9, "Release OK", KRK_ACCENT);
    for (start = OS_TICKS; OS_TICKS - start < RELEASE_TICKS && krk_key_scan() != SCAN_NONE; )
        os_time_dly_ms(50);
    drain_keymsgq();
    /* give the keypad back to the stock poll ~20 polls from now (dm32_patch_api.h, as the screen runner does) */
    *KEYPAD_BLANK_REARM = 1;
    *KEYPAD_BLANK_COUNTER = 0;
    *KEYPAD_POLL_ENABLE = 0;
    display_fill_screen(KRK_BG);
    return ok;
}

/* ---- the option row --------------------------------------------------------------------------------------------------------------- */

static int sw_get(void)
{
    return krk_band_on() ? 0 : 1;
}

static void sw_set(int idx)
{
    band_set_on(idx == 0);
}

static int sw_confirm(int idx)
{
    if (idx != 0 || krk_band_on())
        return 1;                           /* OFF, or ON again: nothing to confirm */
    if (hold_ok())
        return 1;
    krk_popup_draw(KRK_POP_ERR, "Unlock cancelled", 0);
    os_time_dly_ms(1000);
    return 0;
}

static void sw_after(int idx)
{
    if (idx != 0)
        return;
    krk_popup_draw(KRK_POP_WARN, "RX 23-520 MHz", "TX 136-520 MHz");
    os_time_dly_ms(2000);
}

static const struct krk_opt sw_opt = { .title = "Freq Unlock", .labels = krk_onoff_labels, .n = 2, .get = sw_get, .set = sw_set,
                                       .after = sw_after, .confirm = sw_confirm };
const struct krk_screen krk_band_screen = { .label = "Freq Unlock", .opt = &sw_opt };
