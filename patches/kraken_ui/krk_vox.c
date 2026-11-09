/*
 * krk_vox.c - software VOX: voice-keyed transmit on analog channels, and the VOX Monitor screen.
 *
 * Bench facts this is built on (docs/otherfunc-submenu.md, docs/user-guide/vox-cwid.md):
 *  - The FD6818 VOX amplitude vox_amp (REG_64H[9:0]) only tracks the mic while wire 0x30 has
 *    rxon = 0 and txon = 1: the receiver DSP owns the audio path whenever it is on. The word
 *    0x0206 (txon | micin | afout; PLL off, no pabias/padrv) is the guide's own "no transmit,
 *    side-tone only" mode plus the mic ADC: floor ~16, voice to 924, no RF.
 *  - The chip comparator vox_out never asserts (REG_04H vox_en reads back 0), so the VOX
 *    decision is made here, in firmware, on polled vox_amp.
 *  - While actually transmitting (stock TX word 0xC1FE) vox_amp is live natively, so hang time
 *    is measured without any mode games.
 *
 * Shape: a state machine driven from krk_hook_bg_tick (one pass per OS tick, ~10 ms, task
 * "init", loop-level stack), exactly like the RX Monitor. The screen (KRAKEN > VOX Monitor) only
 * displays and edits the knobs; the machine runs from standby.
 *
 *   RX    stock RX word. Every `period` ticks, if sensing is allowed, switch to SENSE.
 *   SENSE 0 -> 0x0206 (idiom), wait `settle` ticks, sample vox_amp for `window` ticks, then
 *         0 -> rx word (as read back from the chip before the switch). A window is "hot" when
 *         at least `need` samples are >= thr_on (~40 ms sustained: short clicks are far
 *         shorter). Hot in mode LIVE -> KEY; in mode SENSE -> count.
 *   KEY   post PTT_PRESS (the beacon's primitive), wait <= 2 s for TX_UP, else REFUSED.
 *         NOT RF-free under an "abort on TX" guard: such a guard can act only after stock has
 *         raised TX, and the analog un-key takes > 200 ms, so every key radiates a short burst
 *         (v5 bench: heard on a nearby receiver). Mode DRY is the RF-free rehearsal.
 *   TX    vox_amp polled every tick: below thr_off for `hang` ticks -> UNKEY; `tot` -> UNKEY;
 *         TX dropped under us -> UNKEY.
 *   UNKEY post PTT_RELEASE, wait for TX down, then `lockout` ticks before sensing again.
 *   DRY   (mode DRY only) a hot window keeps the chip in the sense word instead of keying: the
 *         same hang / TOT logic runs on vox_amp with the PLL and PA off, and no PTT flag is
 *         ever posted. Ends by restoring the RX word, then LOCK.
 *
 * Sensing is skipped (stay in RX) while: mode OFF; not analog; not on the selected side; dual
 * watch running; already TX_UP; the stock receiver active (sensing would drop the receiver
 * during an incoming signal, and VOX does not key over one).
 *
 * Never touches pabias, padrv or the PA GPIOs: all TX is stock's own PTT path.
 */
#include "krk.h"
#include "krk_rf.h"
#include "krk_stock.h"

#define REG_MODE     0x30
#define REG_VOX_AMP  0x64
#define REG_RSSI_TH  0x78     /* [15:8] rssi_sq_th_in, [7:0] out (krk_squelch.c); 0xFFFF = never opens */
#define RSSI_GATE_SHUT 0xffffu
#define SENSE_WORD_AF 0x0206   /* v7 probe word: txon | micin | afout */
#define SENSE_WORD    0x0006   /* v4 default: no afout - the AF DAC stepping on the switch was the audible tick */
#define sense_word() ((VX.flags & 1) ? SENSE_WORD_AF : SENSE_WORD)

/* Stock's VOX settings (Radio Settings > Other Func > VOX / VOX Delay). Stock stores them, shows
 * the V icon and never reads them again; located on hardware by toggling each row and diffing RAM
 * (2026-11-04): VOX SW flips bit 0 here and bit 4 of the loaded channel's flags (0x3e1f1);
 * Gain Level is the row index 0..8; VOX Delay is tenths of a second, 3..50. */
#define STOCK_VOX_SW    (*(volatile u8 *)0x0003e9e4)   /* bit 0 */
#define CHAN_VOX_FLAG   (*(volatile u8 *)0x0003e1f1)   /* bit 4, shown on the screen only */
#define STOCK_VOX_GAIN  (*(volatile u8 *)0x0003ea12)
#define STOCK_VOX_DELAY (*(volatile u8 *)0x0003ea13)

/* Stock power save (Other Func > PowerSave): rf_mode_debounce_state_machine @0x03054ca8, called
 * from the background loop, counts idle passes in this u16 and from 800 on cycles the chip
 * between mode word 0 and the RX word (30 ms on, 30/60/120 ms asleep). Asleep, the mic ADC is
 * off (windows read 0) and a held sense word is overwritten at once (v7 bench: `DRY DROP word 0`,
 * no key on voice at Save 1:4). Stock's own activity hooks (0x03054d80, 0x03054da0) zero the
 * counter; below 800 the machine also wakes a sleeping chip. Doing the same every tick while
 * armed keeps the radio awake without touching the user's setting. */
#define PSAVE_IDLE (*(volatile u16 *)0x0003e390)

#define VOX_MAGIC 0x31584f56u   /* "VOX1" */
enum { M_OFF, M_SENSE, M_DRY, M_LIVE, M_N };
enum { S_RX, S_SENSE, S_KEY, S_TX, S_UNKEY, S_LOCK, S_DRY, S_MENU, S_N };

/* .bss.feat is not zeroed at power-up: magic-guarded, initialised by vox_init(). At most 36 bytes:
 * blob2's .bss.feat shares its claim with blob ui5's (appended at krk_feat_end) and the claim is
 * full (v9: 4 more bytes failed ui5's link). thr_off is derived, TOT/lockout/window are constants. */
static struct {
    u32 magic;
    u32 t;                      /* tick the current state was entered */
    u32 t_hot;                  /* last tick vox_amp >= thr_off while keyed */
    u16 t_warm;                 /* v10: low 16 bits of the tick the current run of warm windows began (0 = none) */
    u16 rx_word;                /* wire 0x30 as read before the last switch to SENSE */
    u16 thr_on;                 /* manual threshold in vox_amp units; 0 = follow the stock Gain Level */
    u16 lvl;                    /* last window max, or live level while keyed */
    u16 rssi78;                 /* v3: stock REG_78H saved across a sense window (squelch held shut) */
    u8 period;                  /* sense period, units of 10 ticks */
    u8 settle;                  /* v2: ticks after the switch before the first sample (knob 4/5) */
    u8 flags;                   /* bit0 = sense word with afout, bit1 = keep micin in RX (bench knobs, key 8) */
    u8 windows, hits, keys, refused;
    u8 mode, st;
    u8 n, hot;                  /* samples taken in the current window / v9: the last WINDOW samples as bits, newest in bit 0 */
    u8 why;                     /* last unkey/refuse reason, R_* */
} VX KRK_FEAT;   /* not "R": the RX Monitor tests find its ring by the symbol name R */

#define WINDOW   6              /* samples per sense window (one per tick) */
#define NEED     4              /* hot samples among the last WINDOW that make a window hot (~40 ms sustained) */
/* v9 (keyed bench: ~1 s from voice to TX): a window that straddled the start of speech used to
 * fail 4-of-6 and cost a whole extra period. Now a window ends hot the moment 4 of the last 6
 * samples are hot, and stays open (up to WINDOW_MAX samples) while any of the last 6 was. */
#define WINDOW_MAX 25
/* v10 (v9 keyed bench: still ~1 s). A window is "warm" when its peak reached thr_off. A warm
 * window stays open up to WINDOW_MAX samples (the level may still be rising towards thr_on), and
 * for WARM_RUN ticks after the first warm window the next one starts WARM_GAP ticks later instead
 * of a period later. A cold window ends the run. Steady noise between the two thresholds costs
 * one run, then the normal period. */
#define WARM_RUN 60
#define WARM_GAP 5
#define MENU_HOLD 150           /* v9: Live only - no sensing while a menu is open and for 1.5 s after it closes
                                 * (key beep, the hand leaving: the v8 keyed bench self-keyed there). State S_MENU. */
#define TOT      18000          /* 180 s */
#define LOCKOUT  50             /* 500 ms after unkey/refuse before sensing resumes */
#define BOOT_HOLD 500           /* no sensing in the first 5 s after power-up */

/* What the stock VOX SW arms while the screen's own mode is Off. M_LIVE = a real VOX (since
 * v9: the v8 keyed bench into a dummy load ran 8 clean cycles, good audio). M_DRY was the bench
 * rehearsal. The setting survives power cycles: BOOT_HOLD, the menu gates, the busy-channel gate
 * and TOT are what stand between it and an unwanted transmission. */
#define STOCK_ARMS M_LIVE

/* Gain Level 1..9, higher = less sensitive (the manual's sense). Level 3 is the 150 the
 * earpiece rounds ran on; an earpiece voice peaks around 900, the idle floor is 16..26. */
static const u16 gain_thr[9] = { 60, 100, 150, 200, 260, 330, 420, 520, 640 };

static u16 thr_on(void)
{
    u8 g = STOCK_VOX_GAIN;

    if (VX.thr_on)
        return VX.thr_on;
    return gain_thr[g < 9 ? g : 2];
}
/* Hang threshold, and since v10 the "warm" level that keeps a window open. Never under 40: the
 * live floor reaches 28, and Level 1 used to give 24. */
static u16 thr_off(void)
{
    u16 v = (u16)(thr_on() * 2 / 5);

    return v < 40 ? 40 : v;
}

/* Hang time in ticks from the stock VOX Delay (tenths of a second). */
static int hang_ticks(void)
{
    u8 d = STOCK_VOX_DELAY;

    return (d >= 3 && d <= 50 ? d : 10) * 10;
}

static u8 eff_mode(void)
{
    if (VX.mode != M_OFF)
        return VX.mode;
    return (STOCK_VOX_SW & 1) ? STOCK_ARMS : M_OFF;
}

#define CUT      60             /* TX gone within 600 ms of coming up: cut by the rx_only guard (it re-posts at 500 ms) */
enum { R_NONE, R_NOTX, R_HANG, R_TOT, R_DROP, R_INHIBIT, R_MENU, R_CUT, R_N };

static void uart_ev(const char *tag, int a, int b)
{
    char l[KRK_LINE_BUF];

    l[0] = 0;
    krk_cat(l, "VOX ");
    krk_cat(l, tag);
    krk_cat(l, " ");
    krk_cati(l, a);
    krk_cat(l, " ");
    krk_cati(l, b);
    krk_cat(l, "\r\n");
    krk_uart_puts(l);
}

static void vox_init(void)
{
    VX.mode = M_OFF;
    VX.st = S_RX;
    VX.thr_on = 0;               /* follow the stock Gain Level */
    VX.period = 3;               /* 300 ms (v9; was 400) */
    VX.settle = 2;               /* 20 ms; v1 bench: too short, windows read 0 (detector cold start) */
    VX.flags = 3;                /* bit1 = mic ADC kept on in the RX word; bit0 = sense word with afout (v5 bench: deaf without it) */
    VX.lvl = 0;
    VX.windows = VX.hits = VX.keys = VX.refused = 0;
    VX.why = R_NONE;
    VX.t_warm = 0;
    VX.t = OS_TICKS;
    VX.t_hot = OS_TICKS;
    VX.magic = VOX_MAGIC;        /* last */
}

/* v2: the 0 (IDLE) step of stock's idiom powers the whole chip down and the VOX detector then
 * reads 0 for hundreds of ms (v1 bench: most 80 ms windows read 0). Default is a direct write;
 * flags bit0 restores the idiom for comparison. */
static void mode_switch(u32 word)
{
    fd6818_reg_write(REG_MODE, word);              /* v4: always direct; the 0 idiom was never the issue */
}
#define mode_idiom(w) mode_switch(w)

static u32 rx_word_wanted(void)
{
    return (VX.flags & 2) ? (VX.rx_word | 0x0004u) : VX.rx_word;
}

static int tx_up(void)
{
    return (CALL_ACTIVE & TX_UP) != 0;
}

static int win_hot(void)                              /* hot samples among the last WINDOW */
{
    u8 b = VX.hot;
    int c = 0;

    for (; b; b >>= 1)
        c += b & 1;
    return c;
}

/* Why may this radio NOT drop its receiver for a sense window right now? 0 = it may.
 * 1 mode off, 2 not analog, 3 not on the selected side, 4 dual watch, 5 TX up, 6 stock RX active. */
static int gate_block(void)
{
    if (eff_mode() == M_OFF) return 1;
    if (!krk_channel_analog()) return 2;
    if (!krk_channel_on_selected()) return 3;
    if (dual_watch_state()) return 4;
    if (tx_up()) return 5;
    if (stock_rx_active()) return 6;
    return 0;
}
static int may_sense(void) { return gate_block() == 0; }

static void enter(u8 st)
{
    VX.st = st;
    VX.t = OS_TICKS;
}

/* v5: stock's whole notion of "receiving" is one read of sq_out (REG_0CH[1]) through
 * reg_0xc_bit1_read @0x0300eb44, whose only caller is vfo_display_state_nibble_bits_check
 * @0x0300f2a4 = stock_rx_active(). In the sense word (txon, afout) that bit reads 1, so stock
 * lit the RX LED, opened the audio (the tick), latched RX-active for ~60 s after a hot phase,
 * and dropped the UI to its call screen. The manifest splices the
 * `bsr 0x0300eb44` at 0x0300f2c2 to this wrapper: 0 while a window is open and for SQ_MASK ticks
 * after it, the real read otherwise. Runs in stock's task: no state changes, no I/O beyond the
 * original read. */
#define stock_sq_read ((u32 (*)(void))0x0300eb44)
#define SQ_MASK 15                                  /* 150 ms after a window: the squelch re-settles */
u32 krk_vox_sq_read(void)
{
    if (VX.magic == VOX_MAGIC) {
        u8 m = eff_mode();

        if (VX.st == S_SENSE || VX.st == S_DRY)       /* whatever the mode says now: the chip is in the sense word */
            return 0;
        if (m != M_OFF && (VX.st == S_RX || (VX.st == S_LOCK && m == M_DRY))
            && (int)(OS_TICKS - VX.t) < SQ_MASK && VX.windows)
            return 0;
    }
    return stock_sq_read();
}

/* Hand the chip and stock's PTT back before a knob changes under the machine (screen task). */
static void vox_abort(void)
{
    if (VX.st == S_SENSE || VX.st == S_DRY) {
        mode_switch(rx_word_wanted());                /* never leave the chip in the sense word */
        fd6818_reg_write(REG_RSSI_TH, VX.rssi78);
    } else if (VX.st == S_KEY || VX.st == S_TX) {
        OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
    } else {
        return;
    }
    enter(S_RX);
}

/* One pass of the stock background loop (~10 ms). Integer only, no delays, no drawing. */
void krk_vox_tick(void)
{
    u32 now, a;
    u8 mode;
    int hot;

    if (VX.magic != VOX_MAGIC) {                      /* power-up: the stock VOX SW arms it with no screen visit */
        vox_init();
        return;
    }
    now = OS_TICKS;
    mode = eff_mode();
    if ((int)(now - VX.t) < 0) {                     /* rebooted with SRAM kept (t is never ahead of the tick) */
        VX.magic = 0;
        return;
    }
    if (mode != M_OFF)
        PSAVE_IDLE = 0;                               /* armed: no stock power save */

    switch (VX.st) {
    case S_RX:
        if (mode == M_OFF || now < BOOT_HOLD)
            return;
        if (mode == M_LIVE && krk_ui_in_menu() && (int)(now - VX.t) >= SQ_MASK) {   /* stock exits any menu to standby on PTT: never key with one open */
            enter(S_MENU);                            /* not before the post-window squelch mask has run out */
            return;
        }
        if ((int)(now - VX.t) < (VX.t_warm && (u16)((u16)now - VX.t_warm) < WARM_RUN
                                 ? WARM_GAP : (int)VX.period * 10))
            return;
        if (!may_sense())
            return;
        VX.rx_word = (u16)fd6818_reg_read(REG_MODE) & ~0x0004u;
        if ((VX.rx_word & 0x0003) != 0x0001)          /* not a plain RX word (rxon=1, txon=0): leave it */
            return;
        /* v3: stock polls sq_out every tick and saw the sense word as "signal": RX LED pulse, an
         * audible tick, and after a hot phase a ~60 s RX-active latch that blocked sensing. Hold
         * the RSSI squelch shut for the window (the squelch feature's gate, inverted). */
        VX.rssi78 = (u16)fd6818_reg_read(REG_RSSI_TH);
        fd6818_reg_write(REG_RSSI_TH, RSSI_GATE_SHUT);
        mode_switch(sense_word());
        VX.n = VX.hot = 0;
        VX.lvl = 0;
        enter(S_SENSE);
        return;

    case S_SENSE:
        if ((int)(now - VX.t) < (int)VX.settle)
            return;
        a = fd6818_reg_read(REG_MODE);
        if ((u16)a != sense_word()) {                 /* stock retuned under us: abandon the window */
            fd6818_reg_write(REG_RSSI_TH, VX.rssi78);
            enter(S_RX);
            return;
        }
        a = fd6818_reg_read(REG_VOX_AMP) & 0x3ff;
        if (a > VX.lvl) VX.lvl = (u16)a;
        VX.hot = (u8)(((VX.hot << 1) | (a >= thr_on() ? 1 : 0)) & ((1 << WINDOW) - 1));
        VX.n++;
        hot = win_hot() >= NEED;
        if (!hot && (VX.n < WINDOW || (VX.lvl >= thr_off() && VX.n < WINDOW_MAX)))
            return;                                   /* not decided yet / speech may be starting */
        if (VX.lvl >= thr_off()) {                    /* warm: VX.t is this window's start */
            if (!VX.t_warm)
                VX.t_warm = (u16)VX.t ? (u16)VX.t : 1;
        } else {
            VX.t_warm = 0;
        }
        if (hot)                                      /* v10 bench: decision 30-50 ms after the first warm window began */
            VX.t_warm = 0;
        if (mode == M_DRY && hot) {                    /* dry key: hold the sense word, post nothing */
            VX.windows++;
            VX.hits++;
            VX.keys++;
            VX.t_hot = now;
            uart_ev("DRY KEY lvl", VX.keys, VX.lvl);
            enter(S_DRY);
            return;
        }
        mode_switch(rx_word_wanted());                /* receiver back first, always */
        fd6818_reg_write(REG_RSSI_TH, VX.rssi78);
        VX.windows++;
        if (hot) {
            VX.hits++;
            uart_ev("HOT lvl n", VX.lvl, VX.n);
            if (mode == M_LIVE) {
                if (krk_ui_in_menu()) {                   /* opened during the window */
                    VX.why = R_MENU;
                    uart_ev("HOT in menu: not keyed", 0, 0);
                    enter(S_LOCK);                    /* then RX -> S_MENU, after the squelch mask */
                    return;
                }
                if (tx_is_inhibited()) {
                    VX.why = R_INHIBIT;
                    VX.refused++;
                    enter(S_LOCK);
                    return;
                }
                OSFlagPost_wrapper_set(PTT_GROUP, PTT_PRESS);
                VX.keys++;
                uart_ev("KEY", VX.keys, 0);
                enter(S_KEY);
                return;
            }
        }
        enter(S_RX);
        return;

    case S_KEY:
        if (tx_up()) {
            VX.t_hot = now;
            uart_ev("TXUP", (int)(now - VX.t), 0);
            enter(S_TX);
            return;
        }
        if ((int)(now - VX.t) > 200) {                 /* 2 s: refused (rx_only guard, TOT, inhibit) */
            OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
            VX.why = R_NOTX;
            VX.refused++;
            uart_ev("REFUSED noTX", VX.refused, 0);
            enter(S_LOCK);
        }
        return;

    case S_TX:
        if (!tx_up()) {
            if ((int)(now - VX.t) < CUT) {             /* the rx_only guard: a short burst DID go out */
                VX.why = R_CUT;
                VX.refused++;
            } else {
                VX.why = R_DROP;
            }
            enter(S_UNKEY);
            return;
        }
        a = fd6818_reg_read(REG_VOX_AMP) & 0x3ff;
        VX.lvl = (u16)a;
        if (a >= thr_off())
            VX.t_hot = now;
        if ((int)(now - VX.t_hot) > hang_ticks()) {
            VX.why = R_HANG;
            enter(S_UNKEY);
            return;
        }
        if ((int)(now - VX.t) > TOT) {
            VX.why = R_TOT;
            enter(S_UNKEY);
        }
        return;

    case S_UNKEY:
        OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
        uart_ev("UNKEY why", VX.why, (int)(now - VX.t));
        enter(S_LOCK);
        return;

    case S_LOCK:
        if (tx_up())                                  /* wait for the transmitter to drop first */
            return;
        if ((int)(now - VX.t) < LOCKOUT)
            return;
        enter(S_RX);
        return;

    case S_MENU:
        if (mode == M_LIVE && krk_ui_in_menu()) {
            VX.t = now;                               /* the hold runs from the moment the menu closes */
            return;
        }
        if (mode != M_LIVE || (int)(now - VX.t) >= MENU_HOLD)
            enter(S_RX);
        return;

    case S_DRY:
        a = fd6818_reg_read(REG_MODE);
        if ((u16)a != sense_word()) {                 /* stock retuned under us: it owns the chip again */
            fd6818_reg_write(REG_RSSI_TH, VX.rssi78);
            VX.why = R_DROP;
            uart_ev("DRY DROP word", (int)a, (int)(now - VX.t));
            enter(S_LOCK);
            return;
        }
        a = fd6818_reg_read(REG_VOX_AMP) & 0x3ff;
        VX.lvl = (u16)a;
        if (a >= thr_off())
            VX.t_hot = now;
        if ((int)(now - VX.t_hot) > hang_ticks())
            VX.why = R_HANG;
        else if ((int)(now - VX.t) > TOT)
            VX.why = R_TOT;
        else
            return;
        mode_switch(rx_word_wanted());
        fd6818_reg_write(REG_RSSI_TH, VX.rssi78);
        uart_ev("DRY UNKEY why", VX.why, (int)(now - VX.t));
        enter(S_LOCK);
        return;
    }
}

/* ---- screen: display + knobs ------------------------------------------------------------- */

static const char *const mode_name[M_N] = { "Off", "Sense", "Dry", "Live" };
static const char *const st_name[S_N] = { "RX", "SENSE", "KEY", "TX", "UNKEY", "LOCK", "DRY-TX", "menu hold" };
static const char *const why_name[R_N] = { "-", "noTX", "hang", "tot", "drop", "inhib", "menu", "cut" };

static void line_kv(int n, const char *label, int val, u16 fg)
{
    char buf[KRK_LINE_BUF];

    buf[0] = 0;
    krk_cat(buf, label);
    krk_cati(buf, val);
    krk_pad(buf);
    krk_line(n, buf, fg);
}

static void mon_enter(void)
{
    if (VX.magic != VOX_MAGIC)
        vox_init();
    krk_line(0, "VOX engine v16", KRK_DIM);
    krk_line(8, "1mode 2/3thr 9auto", KRK_DIM);
    krk_line(9, "4/5set 6/7per 0zero", KRK_DIM);
}

static void mon_tick(u8 scan)
{
    char l[KRK_LINE_BUF];
    u8 m;

    if (VX.magic != VOX_MAGIC)
        vox_init();
    if (scan == SCAN_1) {
        vox_abort();
        VX.mode = (u8)((VX.mode + 1) % M_N);
        uart_ev("MODE", VX.mode, 0);
    } else if (scan == SCAN_1 + 1 && thr_on() > 40) {  /* 2/3: manual threshold, from wherever it stands */
        VX.thr_on = (u16)(thr_on() - 20);
    } else if (scan == SCAN_1 + 2 && thr_on() < 1000) {
        VX.thr_on = (u16)(thr_on() + 20);
    } else if (scan == SCAN_1 + 8) {                   /* 9: back to the stock Gain Level */
        VX.thr_on = 0;
    } else if (scan == SCAN_1 + 3 && VX.settle > 1) {
        VX.settle--;
    } else if (scan == SCAN_1 + 4 && VX.settle < 60) {
        VX.settle++;
    } else if (scan == SCAN_1 + 5 && VX.period > 1) {
        VX.period--;
    } else if (scan == SCAN_1 + 6 && VX.period < 20) {
        VX.period++;
    } else if (scan == SCAN_1 + 7) {                   /* key 8: cycle af+m / noaf / af / noaf+m */
        vox_abort();                                  /* the sense word is about to change */
        VX.flags = (u8)((VX.flags & ~3) | ((VX.flags + 1) & 3));
        uart_ev("FLAGS", VX.flags, 0);
    } else if (scan == SCAN_0) {
        VX.windows = VX.hits = VX.keys = VX.refused = 0;
        VX.why = R_NONE;
    }

    l[0] = 0;
    m = eff_mode();
    krk_cat(l, VX.mode == M_OFF && m != M_OFF ? "VOX>" : "mode ");   /* armed by the channel's VOX SW / this screen's mode */
    krk_cat(l, mode_name[m]);
    krk_cat(l, " ");
    krk_cat(l, (VX.st == S_RX || VX.st == S_SENSE) ? (m ? "sensing" : "idle") : st_name[VX.st]);
    krk_pad(l);
    krk_line(1, l, m == M_LIVE ? KRK_WARN : m ? KRK_ACCENT : KRK_DIM);
    l[0] = 0;
    krk_cat(l, "lvl ");
    krk_cati(l, VX.lvl);
    krk_cat(l, " gate ");
    krk_cati(l, gate_block());
    krk_pad(l);
    krk_line(2, l, KRK_FG);
    l[0] = 0;
    krk_cat(l, "thr ");
    krk_cati(l, thr_on());
    krk_cat(l, "/");
    krk_cati(l, thr_off());
    if (VX.thr_on) {
        krk_cat(l, " man");
    } else {                                           /* G1..G9 = the stock Gain Level it follows */
        krk_cat(l, " G");
        krk_cati(l, STOCK_VOX_GAIN + 1);
    }
    krk_cat(l, " s");
    krk_cati(l, VX.settle * 10);
    krk_pad(l);
    krk_line(3, l, KRK_DIM);
    l[0] = 0;
    krk_cat(l, "p");                                   /* period ms, hang in 0.1 s, stock VOX SW / channel flag */
    krk_cati(l, VX.period * 100);
    krk_cat(l, " h");
    krk_cati(l, hang_ticks() / 10);
    krk_cat(l, " sw");
    krk_cat(l, (STOCK_VOX_SW & 1) ? "1" : "0");
    krk_cat(l, (CHAN_VOX_FLAG & 0x10) ? "1" : "0");
    krk_cat(l, (VX.flags & 1) ? " af" : " na");
    krk_cat(l, (VX.flags & 2) ? "+m" : "");
    krk_pad(l);
    krk_line(4, l, KRK_DIM);
    l[0] = 0;
    krk_cat(l, "win ");
    krk_cati(l, VX.windows);
    krk_cat(l, " hot ");
    krk_cati(l, VX.hits);
    krk_pad(l);
    krk_line(5, l, VX.hits ? KRK_ACCENT : KRK_FG);
    l[0] = 0;
    krk_cat(l, "key ");
    krk_cati(l, VX.keys);
    krk_cat(l, " ref ");
    krk_cati(l, VX.refused);
    krk_cat(l, " ");
    krk_cat(l, why_name[VX.why < R_N ? VX.why : 0]);
    krk_pad(l);
    krk_line(6, l, VX.refused ? KRK_WARN : KRK_FG);
}

const struct krk_screen krk_vox_screen = {
    .label = "VOX Monitor",
    .enter = mon_enter,
    .tick = mon_tick,
};

/* ---- menus (v15): stock's own nest, extended ---------------------------------------------------
 * docs/otherfunc-submenu.md. Everything is a stock level in Settings > Radio Settings > Other Func:
 *
 *   VOX       > VOX SW      stock (ON / OFF, this channel or VFO)
 *             > Gain Level  stock (Level 1..9)
 *             > VOX Delay   ours: a radio-mark list of eight hang times; stock's value and stock's
 *                           flash write. It was Other Func row 4 (a spinner popup); that row is gone.
 *   CW ID     > CW ID SW, Interval, Callsign   (2026-11-05, krk_cwid.c: item 11; listed on analog
 *               channels only, in the place of the VOX Delay row. Its Callsign child runs the
 *               shared callsign editor SCREEN from the level-6 init, where the menu has pushed level 6
 *               exactly as for an option level, so the runner's exit pops it like any other screen.)
 *
 * How stock nests (two static traces, 2026-11-06): each menu level's cells are filled by that
 * level's RENDER entry, not by the OK that opened it; OK only calls set_mode(next, 1). So:
 *   - the CW ID parent is Other Func item 11, a slot stock never lists (Man Down). Its entry in the
 *     level-5 init table 0x030b0990 becomes krk_of_cwid_kids_stub (installs the child list exactly as
 *     0x030449ec does for VOX: count, label array, subtype 2, sequential index list, and NOT the
 *     cursor - the level re-renders after every child commit); its entry in the level-5 OK table
 *     0x030b130c becomes stock's VOX arm 0x0304fec8, which is set_mode(0x2a, 1) for any row.
 *   - krk_hook_of_gen takes item 4 out of the generated list and, on an analog channel, puts item 11
 *     where it was.
 *   - level 6 (a child's option level) is three compare chains on the level-4 item, one each in
 *     the init (0x03045fd8), the commit (0x03050de8) and the header (0x03038b9e). Each `== 3` test
 *     is spliced to a stub (krk_ofstubs.S) that asks the C below and either falls back into stock's
 *     VOX arm or leaves through the function's own exit.
 * v11-v14 had a hand-drawn page, then a flat ON/OFF row (Channel Set > VOX); it is gone - the settings
 * live in one place each. */

#define MENU_L2_ITEM     (MENU_L2_ITEMS[MENU_ROW_AT(2)])    /* item numbers, from the saved index lists */
#define MENU_L5_CHILD    (((volatile u8 *)0x0003e6cd)[MENU_ROW_AT(5)])
#define OF_ITEM_VOX      3
#define OF_ITEM_VOXDELAY 4
#define OF_ITEM_CWID     11
#define flash_field_write_cached ((void (*)(u32 blk, u32 off, u32 byte))0x0300fa28)   /* one byte, as stock's menu wrappers call it */

static const u8 delay_vals[8] = { 3, 5, 10, 15, 20, 30, 40, 50 };
static const char *const delay_labels[8] = { "0.3 s", "0.5 s", "1.0 s", "1.5 s", "2.0 s", "3.0 s", "4.0 s", "5.0 s" };

static int vox_delay_get(void)                         /* the nearest of the eight */
{
    return krk_nearest8(delay_vals, STOCK_VOX_DELAY);
}

static void vox_delay_set(int idx)
{
    u8 v = delay_vals[idx & 7];

    STOCK_VOX_DELAY = v;
    flash_field_write_cached(4, 0xa3, v);             /* block 4 offset 0xa3, as menu_wrapper_field0xa3 @0x0304fed2 */
}

static const struct krk_opt vox_delay_opt = { .title = "VOX Delay", .labels = delay_labels, .n = 8, .get = vox_delay_get, .set = vox_delay_set };

/* Our option level for child `child` of Other Func item `row`, or NULL: VOX children 0 and 1 are stock's,
 * and the CW ID Callsign child is a screen (krk_of_l6_enter). */
static const struct krk_opt *of_child_opt(unsigned row, unsigned child)
{
    const struct krk_screen *e;

    if (row == OF_ITEM_VOX)
        return child == 2 ? &vox_delay_opt : 0;
    if (row == OF_ITEM_CWID && (e = krk_cwid_kid(child)) != 0)
        return e->opt;
    return 0;
}

void krk_of_cwid_kids(void);

static void of_norestore(void)
{
    /* nothing: the pop leaves the redraw flag set and the parent's level-5 init refills the child list */
}

/* The three level-6 hooks. Each returns 1 when stock's own VOX arm should run (the stub then
 * returns into it), 0 when the stub should leave through the function's exit. */
int krk_of_l6_enter(unsigned row)
{
    const struct krk_opt *o = of_child_opt(row, MENU_L5_CHILD);

    if (o) {
        krk_opt_enter(o);
        return 0;
    }
    if (row == OF_ITEM_CWID && MENU_L5_CHILD == 2) {
        krk_run_screen(krk_cwid_kid(2), krk_of_cwid_kids);   /* blocks until Back; leaves the child list installed */
        return 0;
    }
    return row == OF_ITEM_VOX;
}

int krk_of_l6_ok(unsigned row)
{
    const struct krk_opt *o = of_child_opt(row, MENU_L5_CHILD);

    if (o) {
        krk_opt_commit(o, of_norestore);
        return 0;
    }
    return row == OF_ITEM_VOX;
}

/* 1 = stock's VOX lookup (it titles all three VOX children from the relocated array), a string =
 * draw it, 0 = stock's blank. The header handler does not test the level-2 item itself. */
const char *krk_of_l6_title(unsigned row)
{
    unsigned child = MENU_L5_CHILD;

    if (row == OF_ITEM_VOX)
        return (const char *)1;
    if (row == OF_ITEM_CWID && MENU_L2_ITEM == 0 && child < KRK_CWID_KIDS)
        return krk_cwid_kid_labels[child];
    return 0;
}

/* Level-5 init of Other Func item 11: the CW ID child list (cf. stock 0x030449ec). Also the callsign
 * editor's restore: Back from it pops to this level, whose cells must be the child list again before
 * the stock tail draws it. */
void krk_of_cwid_kids(void)
{
    krk_list_cells(krk_cwid_kid_labels, KRK_CWID_KIDS);
}

/* After the stock generator: item 4 (VOX Delay, now a VOX child) leaves the list; on an analog channel
 * item 11 (CW ID) takes its place, elsewhere the rows after it move up one. Fixed addresses only, in a
 * function of its own: in one state the stock generator's preamble writes past its buffer into its
 * saved r4-r6, so nothing held in a register across that call can be trusted (static trace, 2026-11-06). */
static void __attribute__((noinline)) of_gen_fix(void)
{
    volatile u16 *count = (volatile u16 *)0x0003e5f8;
    volatile u8 *list = (volatile u8 *)0x0003e5fa;
    unsigned n = *count, i;

    if (n > 33)
        return;
    for (i = 0; i < n; i++)
        if (list[i] == OF_ITEM_VOXDELAY)
            break;
    if (krk_channel_analog()) {
        if (i < n) {
            list[i] = OF_ITEM_CWID;
        } else if (n < 33) {                          /* no VOX Delay row (stock always lists it): CW ID last */
            list[n] = OF_ITEM_CWID;
            *count = (u16)(n + 1);
        }
        return;
    }
    if (i == n)
        return;
    for (; i + 1 < n; i++)
        list[i] = list[i + 1];
    *count = (u16)(n - 1);
}

void krk_hook_of_gen(u32 mode, volatile u16 *count, volatile u8 *list)
{
    menu_option_list_dispatch(mode, count, list);
    of_gen_fix();
}

/* Standby tags (krk_tags.c): which part of the engine would act on this channel, or 0. Only the
 * effective arming is reported, as eff_mode() picks it: a VOX Monitor test mode overrides the
 * channel's VOX SW. Same channel gates as gate_block() 2-4. */
unsigned krk_vox_tag(void)
{
    if (!krk_channel_analog() || !krk_channel_on_selected() || dual_watch_state())
        return 0;
    if (VX.magic == VOX_MAGIC && VX.mode != M_OFF)
        return KRK_VOX_TAG_TEST;
    return (STOCK_VOX_SW & 1) ? KRK_VOX_TAG_SW : 0;
}
