/*
 * krk_scramble.c - F12: the HR_C7000's 16-bit "basic privacy" voice scrambler (docs/basic-privacy.md).
 *
 * SCRAMBLE_REG 0x1100038c: [31:16] receive key, [15:0] transmit key (datasheet 10.2.2.13). Stock writes
 * it once, to 0, at boot (hr_c7000_modem_core_register_init, st.w at 0x030236f2) and never reads it.
 * There is no enable bit: a non-zero key IS "on" (LOCAL_CC bit 16 stays 0, we never touch LOCAL_CC).
 * The algorithm is undocumented, so this is a WEAK scrambler that only another patched radio running
 * the same key format can undo; it is not encryption and not for amateur-band use (the enable popups say so).
 *
 * State is RAM only: power-up = Off (the SRAM holds noise, so a magic word validates it).
 * The register is (re)applied (review fix 6) from FOUR places, so it never lags a mode change by up to a second:
 *   channel change   krk_scramble_chan_change, extension hook slot 2, called by the main blob's aprs_mode_hook stub
 *                    inside channel_change_apply_dispatcher (0x03057aa8), right after the new channel's mode nibble
 *                    (0x3e1e4) is stored. The cipher byte (0x33824) may still be the OLD channel's if the stock
 *                    key load runs later in the dispatcher: the two paths below correct it;
 *   TX voice start   krk_scramble_tx_hook, called by krk_vf_tx_hook (TX vocoder task, once per 60 ms burst, i.e.
 *                    a compare + a tick-counter read per burst). A gap > 200 ms since the previous burst marks the
 *                    FIRST burst of a transmission; only then is the register re-evaluated (and written, if it
 *                    differs), before that burst is copied to the modem window, so the key cannot change mid-TX;
 *   1 Hz tick        krk_hook_maint_tick -> krk_scramble_tick, never mid-call;
 *   the screen       straight from the screen:
 *   want = (key format applied to K) only when On, the loaded channel is digital and carries no stock
 *   cipher ([0x33824] == 0, dmr_privacy_key_load_and_mi_draw); otherwise 0.
 *
 * Key-format sweep (the pairing between the two radios' fields is unproven until the 2-radio bench
 * test): the Scrambler Fmt row selects the format, so both radios can be stepped together:
 *   0  tx K,      rx K          (the datasheet-literal guess)
 *   1  tx K,      rx ~K         (rx field = inverse of tx)
 *   2  tx swap(K) rx swap(K)    (byte-swapped K, in case the key is taken big-endian)
 *   3  tx rev(K)  rx rev(K)     (bit-reversed K, in case of MSB-first)
 * State: scr_st (+ sweep fields, see below) in .bss.feat. The settings are three stock rows (bottom of this file): Scrambler (Off/On),
 * Scrambler Key (numeric), Scrambler Fmt (option level); the custom screen was removed.
 *
 * ONE-ACTION FORMAT SWEEP (key P1, 2026-10-29; the key was removed 2026-11-02 after F12 passed on hardware, so nothing arms the
 * sweep any more; the engine below is kept because its in-target checks still exercise it). A TX sweep with a synthetic PTT was rejected (it would need the
 * parrot's press/release watch around a captured buffer, docs/parrot-tx.md Review fixes; there is none for a
 * live transmission). Instead BOTH radios run the same 12-slot schedule of SW_SLOT_T ticks (3 s) driven by
 * time alone, so the only transmitter action is the operator's ONE normal PTT hold (~36 s into a dummy load):
 *   slot s: tx field of format {0,2,3}[s / 4] (fmt1's tx field is fmt0's), rx field of format s % 4.
 * That covers every tx x rx pairing of the four formats (12 combinations) in one transmission. P1 ARMS a radio
 * (it stays armed through Back, 5 min); the sweep then starts on its own: on the transmitting radio at its
 * first TX burst (krk_scramble_tx_hook), on the receiving radio when its squelch opens (screen 100 ms tick or
 * the 1 Hz maintenance tick). Each slot's register word is written whole, WITHOUT the receiver-busy guard (a mid-call
 * key change is the point of the test; the stateful-scrambler risk is documented). The sweep ends itself after the
 * last slot and restores the normal On/Off value. Slot changes are logged on UART0 ("SCR ...") from the screen
 * and maintenance ticks only, never from the TX burst hook.
 */
#include "krk.h"
#include "krk_stock.h"

#define SCRAMBLE_REG  ((volatile u32 *)0x1100038c)

#define SCR_MAGIC 0x53435231u                           /* "SCR1" */
#define TX_GAP    20                                    /* ticks: bursts are 6 apart, a larger gap = a new transmission */
#define SCR_FMTS  4

static struct {
    u32 magic;
    u16 key;
    u8 on, fmt;
    u32 last_tx;                                        /* tick of the previous TX burst seen by the hook */
    u32 sw_t0;                                          /* sweep: arm tick (sw 1) or start tick (sw 2) */
    u8 sw, sw_slot, sw_logged;                          /* 0 off, 1 armed, 2 running; current slot; last slot logged */
    u8 ciph;                                            /* bit per side: STOCK_CIPHER was set when that side's struct was live */
} scr_st KRK_FEAT;

static void scr_init(void)
{
    if (scr_st.magic != SCR_MAGIC) {
        scr_st.magic = SCR_MAGIC;
        scr_st.key = 0;
        scr_st.on = 0;
        scr_st.fmt = 0;
        scr_st.last_tx = 0;
        scr_st.sw = 0;
        scr_st.ciph = 0;
    }
}

static u16 bitrev16(u16 k)
{
    u16 r = 0;
    int i;

    for (i = 0; i < 16; i++, k >>= 1)
        r = (u16)(r << 1 | (k & 1));
    return r;
}

/* The register value for key k in format fmt. Pure; k == 0 gives 0 in every format. */
u32 krk_scramble_value(u16 k, u8 fmt)
{
    u16 tx = k, rx = k;

    if (fmt == 1)
        rx = (u16)~k;
    else if (fmt == 2)
        tx = rx = (u16)(k >> 8 | k << 8);
    else if (fmt == 3)
        tx = rx = bitrev16(k);
    return k ? ((u32)rx << 16 | tx) : 0;
}

/* Why the wanted value is 0 although the user has the scrambler on: 0 none, 1 analog, 2 stock cipher.
 * The mode is the SELECTED side's (krk_channel_dmr, dual-watch safe: the live struct may be the other
 * side's, docs/dual-watch.md). STOCK_CIPHER describes the loaded channel only, so it is sampled while the
 * live struct is the selected side's and remembered per side for the other side's dwell. */
static u32 scr_idle_reason(void)
{
    unsigned sel = SIDE_SELECTED;

    if (!krk_channel_dmr())
        return 1;
    if (krk_channel_on_selected())
        scr_st.ciph = (u8)(scr_st.ciph & ~(1u << sel) | (STOCK_CIPHER ? 1u << sel : 0));
    return scr_st.ciph >> sel & 1 ? 2 : 0;
}

/* Register word of sweep slot 0..11: tx field of format {0,2,3}[slot / 4], rx field of format slot % 4. Pure. */
u32 krk_scramble_sweep_value(u16 k, u8 slot)
{
    static const u8 txf[3] = { 0, 2, 3 };

    return (krk_scramble_value(k, (u8)(slot & 3)) & 0xffff0000u) |
           (krk_scramble_value(k, txf[slot / 4 % 3]) & 0xffffu);
}

static u32 scr_want(void)
{
    if (scr_st.magic != SCR_MAGIC || !scr_st.key || scr_idle_reason())
        return 0;
    if (scr_st.sw == 2)
        return krk_scramble_sweep_value(scr_st.key, scr_st.sw_slot);
    return scr_st.on ? krk_scramble_value(scr_st.key, scr_st.fmt) : 0;
}

/* Writes SCRAMBLE_REG when it differs from the wanted value. Tick calls (force 0) leave it alone while
 * the receiver is busy (a mid-call change could desynchronise a stateful scrambler); the retry is the
 * next pass. The screen forces: the user is in the menu, not in a call. */
static void scr_apply(int force)
{
    u32 want = scr_want();

    if (*SCRAMBLE_REG == want)
        return;
    if (!force && krk_channel_on_selected() && stock_rx_active())
        return;                                          /* stock_rx_active() answers 1 for any analog struct: only meaningful on our side */
    *SCRAMBLE_REG = want;
}

/* The sweep clock. from_tx: called from the TX burst hook (the first burst starts an armed sweep); otherwise an
 * armed sweep starts when the receiver's squelch is open. Safe to call from any context that may run
 * scr_apply(1) (the hook, the maintenance tick, the screen). Returns nothing; UART logging is separate. */
#define SW_SLOTS   12
#define SW_SLOT_T  300                                  /* ticks (10 ms) per slot */
#define SW_ARM_MAX 30000                                /* an armed radio gives up after 5 min */
static void scr_sweep_step(int from_tx)
{
    u32 now = OS_TICKS;

    if (scr_st.magic != SCR_MAGIC || !scr_st.sw)
        return;
    if (scr_st.sw == 1) {
        if (now - scr_st.sw_t0 > SW_ARM_MAX) {
            scr_st.sw = 0;
        } else if (from_tx || (!scr_idle_reason() && krk_channel_on_selected() && stock_rx_active())) {
            scr_st.sw = 2;
            scr_st.sw_t0 = now;
            scr_st.sw_slot = 0;
            scr_st.sw_logged = 0xff;
        } else {
            return;
        }
    } else {
        u32 slot = (now - scr_st.sw_t0) / SW_SLOT_T;

        if (slot >= SW_SLOTS)
            scr_st.sw = 0;
        else
            scr_st.sw_slot = (u8)slot;
    }
    scr_apply(1);                                       /* sw 2: the slot word; sw 0 (ended): the normal value */
}

/* Extension hook 2: a channel was just loaded (maintenance/menu/PTT task). The receiver is idle here by
 * definition, so the write is forced. */
void krk_scramble_chan_change(void)
{
    if (scr_st.magic == SCR_MAGIC)
        scr_apply(1);
}

/* Called by krk_vf_tx_hook for every TX burst (TX vocoder task, prio 16: keep it tiny). */
void krk_scramble_tx_hook(void)
{
    u32 now = OS_TICKS, gap = now - scr_st.last_tx;

    if (scr_st.magic != SCR_MAGIC)
        return;
    scr_st.last_tx = now;
    if (scr_st.sw)
        scr_sweep_step(1);
    else if (gap > TX_GAP)
        scr_apply(1);
}

static void scr_sweep_log(void);

/* Called from krk_hook_maint_tick (about once a second, maintenance task: integer-only, tiny). */
void krk_scramble_tick(void)
{
    scr_sweep_step(0);
    scr_apply(0);
    scr_sweep_log();
}

/* ---- UART markers -------------------------------------------------------------------------------- */

/* UART markers of the sweep, from the screen and 1 Hz ticks only (a UART line takes ~4 ms: never in the TX hook). */
static void scr_sweep_log(void)
{
    char l[KRK_LINE_BUF], num[12];

    if (scr_st.magic != SCR_MAGIC)
        return;
    if (scr_st.sw == 2) {
        if (scr_st.sw_logged == 0xff)
            krk_uart_puts("SCR SWEEP START (slot = 3 s; tx fmt {0,2,3}[slot/4], rx fmt slot%4)\r\n");
        if (scr_st.sw_slot != scr_st.sw_logged) {
            scr_st.sw_logged = scr_st.sw_slot;
            l[0] = 0;
            krk_cat(l, "SCR SLOT ");
            krk_cati(l, scr_st.sw_slot + 1);
            krk_cat(l, "/12 tx fmt ");
            krk_cati(l, scr_st.sw_slot / 4 ? scr_st.sw_slot / 4 + 1 : 0);
            krk_cat(l, " rx fmt ");
            krk_cati(l, scr_st.sw_slot & 3);
            krk_cat(l, " reg ");
            krk_hex(num, *SCRAMBLE_REG, 8);
            krk_cat(l, num);
            krk_cat(l, " t10ms=");
            krk_cati(l, (int)(OS_TICKS & 0xffffff));
            krk_cat(l, "\r\n");
            krk_uart_puts(l);
        }
    } else if (scr_st.sw_logged < 0xfd) {
        scr_st.sw_logged = 0xfd;
        krk_uart_puts("SCR SWEEP END\r\n");
    }
}

/* ---- settings rows (stock option levels / numeric entry, docs/stock-option-level.md; the former custom screen is gone) ----
 * "Scrambler (weak)"  Off / On          get/set = scr_st.on, set applies the register at once (the old OK:set path).
 * "Scrambler Key"     numeric 1..65535  the first shipped stock numeric-entry row. 0 is NOT accepted: a zero key means "no key" to the
 *                     engine (scr_want() gives register 0), and the old screen refused On with an empty key; "Out of Range" says so.
 * "Scrambler Fmt"     fmt0..fmt3        the former Up/Down format cycle.
 * The live register readout is folded into the On/Off level's header ("Scrambler 12341234" = SCRAMBLE_REG as applied now; all zeros
 * while Off, on an analog channel, or with a stock cipher on the channel), so no extra info screen is needed. */
static int scr_on_get(void)
{
    return scr_st.magic == SCR_MAGIC && scr_st.on ? 1 : 0;
}

static void scr_on_set(int i)
{
    scr_init();
    scr_st.on = i ? 1 : 0;
    scr_apply(1);
}

/* After the stock "Setting is OK" popup: the legal / interop caveat stays in the enable flow. Plain draw + yielding delay: the OK key
 * handler context, no runner (so no key reads). */
static void scr_warn_popup(const char *l1, const char *l2, unsigned ms)
{
    MENU_EXIT_COUNTER = 0;                                      /* the chain takes up to 4.5 s and the shortest Menu Exit Time is 5 s: the counter must */
    krk_popup_draw(KRK_POP_WARN, l1, l2);               /* never reach its limit while a popup is up (the posted exit event would fire at the end) */
    os_time_dly_ms(ms);
    MENU_EXIT_COUNTER = 0;
}

static void scr_on_after(int i)
{
    if (!i)
        return;
    if (!scr_st.key)
        scr_warn_popup("Set Scrambler", "Key first", 1500);
    scr_warn_popup("Weak scrambler,", "not encrypted", 1500);
    scr_warn_popup("Not for amateur", "radio use", 1500);
}

static const char *scr_on_title(void)
{
    static char t[24];                  /* "Scrambler " + 8 hex digits + NUL = 19; krk_catn takes the real size */
    char num[12];

    t[0] = 0;
    krk_catn(t, sizeof t, "Scrambler ");
    krk_hex(num, *SCRAMBLE_REG, 8);
    krk_catn(t, sizeof t, num);
    return t;
}

const char *const krk_offon_labels[2] = {"Off", "On"};     /* shared with krk_aprsrx.c, krk_parrot.c */
static const struct krk_opt scr_on_opt = {.title = "Scrambler", .labels = krk_offon_labels, .n = 2, .get = scr_on_get, .set = scr_on_set,
                                      .title_fn = scr_on_title, .after = scr_on_after};
/* The on/off switch is now the FIRST CHILD of the Scrambler parent row (docs/nested-submenu.md), not a row of its own.
 * Its title_fn (the live key in the header, "Scrambler 00001234") SURVIVES the move: the depth-5 header of a nested
 * child is our own splice (cs_kid_title_hook -> krk_nest_title), which calls title_fn exactly as krk_opt_title does.
 * That is the one thing the flat Radio Settings conversion could NOT keep (its header is drawn straight from the
 * descriptor by stock, docs/settings-submenu.md 3). */
const struct krk_screen krk_scramble_on_screen = {.label = "Scrambler On/Off", .opt = &scr_on_opt};

static int scr_key_get(void)
{
    return scr_st.magic == SCR_MAGIC ? scr_st.key : 0;
}

static void scr_key_set(int v)
{
    scr_init();
    scr_st.key = (u16)v;
    scr_apply(1);
}

static const struct krk_opt scr_key_opt = {.title = "Scrambler Key", .labels = 0, .n = 5, .get = scr_key_get, .set = scr_key_set, .min = 1, .max = 65535};
const struct krk_screen krk_scrkey_row = {.label = "Key", .opt = &scr_key_opt};

static int scr_fmt_get(void)
{
    return scr_st.magic == SCR_MAGIC ? scr_st.fmt & 3 : 0;
}

static void scr_fmt_set(int i)
{
    scr_init();
    scr_st.fmt = (u8)(i & 3);
    scr_apply(1);
}

static const char *const scr_fmt_labels[SCR_FMTS] = {"fmt0 plain K", "fmt1 rx=~K", "fmt2 swap K", "fmt3 bitrev K"};
static const struct krk_opt scr_fmt_opt = {.title = "Scrambler Fmt", .labels = scr_fmt_labels, .n = SCR_FMTS, .get = scr_fmt_get, .set = scr_fmt_set};
const struct krk_screen krk_scrfmt_row = {.label = "Scrambler Fmt", .opt = &scr_fmt_opt};

/* "Scrambler Fmt" stays in the KRAKEN list rather than next to the two Channel Set rows: it is a key-format /
 * interop knob, not something a user sets alongside the key, and the requested layout was exactly
 * { Scrambler On/Off, Key }. Those two are flat Channel Set rows (krk_ext.c); grouping them under one nested
 * "Scrambler" parent was investigated in full and does not work on this firmware - docs/nested-submenu.md. */
