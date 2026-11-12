/*
 * krk_scope.c - F5: band scope. Sweeps SCOPE_N points centred on a frequency (initially the
 * channel's RX frequency) and plots RSSI per point, with peak hold.
 *
 * The radio has one receiver: while the scope sweeps, the channel is NOT being received. Audio is
 * muted for the duration and the channel is restored (stock channel_change_apply_squelch) on exit.
 * v1 limits: analog channels only (a digital channel's modem state is not ours to disturb) and
 * in-band only (the LNA band GPIOs are never switched; docs/retune-for-scope.md section A.4).
 * Per-point timing, measured on hardware by the RF Lab (2026-10-15): PLL lock 1.25-1.32 ms after
 * the retune, then REG_67H changes to the new frequency's value 8.7 ms after lock in every trial
 * (a fixed RSSI update schedule, not gradual settling). So each point waits for the lock bit, then
 * sleeps S.settle_ms. os_time_dly_ms has 10 ms tick granularity: 20 ms sleeps 10-20 ms, which is
 * always >= 8.7 ms and never busy-waits. '*' / '#' change it in 10 ms steps (minimum 20).
 *
 * Keys: Up/Down step size, 4/6 move the centre by a quarter span, '*'/'#' settle -/+, OK pause.
 *
 * The runner reads the keypad once per tick and only sees a key that is down at that moment, so a
 * sweep runs SCOPE_CHUNK points per tick (about 150 ms) instead of all 48 (about 1.5 s): a whole-sweep
 * tick swallowed a short Back press (hardware, 2026-10-30).
 */
#include "krk_rf.h"

#define SCOPE_N     48
#define BAR_W       5           /* 48 * 5 = 240 px */
#define GRAPH_TOP   (KRK_Y0 + 2 * KRK_LINE_H)
#define GRAPH_BOT   (KRK_Y0 + 8 * KRK_LINE_H - 2)
#define DB_FLOOR    (-130)      /* dBm (uncal) at the graph's bottom */
#define DB_SPAN     80          /* dB over the graph's height */
#define SCAN_4      (SCAN_1 + 3)
#define SCAN_6      (SCAN_1 + 5)
#define SCOPE_CHUNK 6           /* points per tick: 6 x (lock ~1.3 ms + settle 20-30 ms) */

static const u32 steps10[] = {625, 1250, 2500, 5000, 10000};   /* 6.25 .. 100 kHz in 10 Hz */

static struct {
    u32 center, lo, hi;
    u8 step_i, paused, running, settle_ms, pos;
    u8 cur[SCOPE_N], peak[SCOPE_N];   /* RSSI raw/2 (1 dB/unit), 0 = not measured */
    u8 peak_age;
    u32 sweeps, timeouts, norf;
} S KRK_SCREEN_BSS;   /* screen-only: scope_enter sets running/center/steps, reset_trace the trace; all uses are in this screen */

static u32 point_f10(int i)
{
    return S.center + (u32)(i - SCOPE_N / 2) * steps10[S.step_i];
}

static int bar_h(u8 db_from_floor_raw)
{
    /* value is raw/2 = dB above -160; convert to the graph's -130..-50 dBm window */
    int dbm = (int)db_from_floor_raw - 160, h;

    h = (dbm - DB_FLOOR) * (GRAPH_BOT - GRAPH_TOP) / DB_SPAN;
    if (h < 0) h = 0;
    if (h > GRAPH_BOT - GRAPH_TOP) h = GRAPH_BOT - GRAPH_TOP;
    return h;
}

static void fmt_mhz(char *out, u32 f10)
{
    out[0] = 0;                        /* out holds KRK_LINE_BUF bytes */
    krk_catfix(out, (int)(f10 / 100), 1000, 0);
}

static void draw_header(void)
{
    char a[KRK_LINE_BUF];

    a[0] = 0;
    fmt_mhz(a, S.center);
    krk_cat(a, " +-");
    krk_cati(a, (int)(steps10[S.step_i] * (SCOPE_N / 2) / 100));
    krk_cat(a, "k");
    krk_line(0, a, S.paused ? KRK_WARN : KRK_FG);
}

static void draw_footer(void)
{
    char a[KRK_LINE_BUF], f[KRK_LINE_BUF];
    int i, best = -1;

    for (i = 0; i < SCOPE_N; i++)
        if (S.cur[i] && (best < 0 || S.cur[i] > S.cur[best]))
            best = i;
    a[0] = 0;
    if (best >= 0) {
        fmt_mhz(f, point_f10(best));
        krk_cat(a, f);
        krk_cat(a, " ");
        krk_cati(a, (int)S.cur[best] - 160);
        krk_cat(a, "dBm");
    }
    krk_line(8, a, KRK_ACCENT);
    a[0] = 0;
    krk_cat(a, "settle ");
    krk_cati(a, S.settle_ms);
    krk_cat(a, "ms");
    if (S.norf) {
        krk_cat(a, " NO RF");
    } else if (S.timeouts) {
        krk_cat(a, " nolock ");
        krk_cati(a, (int)S.timeouts);
    }
    krk_line(9, a, KRK_DIM);
}

static void draw_bar(int i)
{
    int x = i * BAR_W, h = bar_h(S.cur[i]), p = bar_h(S.peak[i]);
    u16 col = (i == SCOPE_N / 2) ? KRK_WARN : KRK_ACCENT;

    display_fill_rect(x, GRAPH_TOP, x + BAR_W - 1, GRAPH_BOT - h, KRK_BG);
    if (h)
        display_fill_rect(x, GRAPH_BOT - h, x + BAR_W - 1, GRAPH_BOT, col);
    if (p > h + 1)
        display_fill_rect(x, GRAPH_BOT - p, x + BAR_W - 1, GRAPH_BOT - p + 2, KRK_DIM);
}

static void reset_trace(void)
{
    int i;

    for (i = 0; i < SCOPE_N; i++)
        S.cur[i] = S.peak[i] = 0;
    S.timeouts = 0;
    S.norf = 0;
    S.pos = 0;
    display_fill_rect(0, GRAPH_TOP, KRK_SCREEN_W, GRAPH_BOT + 1, KRK_BG);
    draw_header();
}

static void clamp_center(void)
{
    u32 half = steps10[S.step_i] * (SCOPE_N / 2);

    if (S.center < S.lo + half) S.center = S.lo + half;
    if (S.center + half > S.hi) S.center = S.hi - half;
}

/* Measure the next SCOPE_CHUNK points; returns 1 when the sweep wrapped. */
static int sweep_chunk(void)
{
    int n;

    for (n = 0; n < SCOPE_CHUNK; n++) {
        int i = S.pos;
        u32 r;
        krk_rf_retune(point_f10(i));
        if (krk_rf_wait_lock(5000) < 0)
            S.timeouts++;
        os_time_dly_ms(S.settle_ms);           /* >= 1 full tick after lock: see header */
        r = (krk_rf_rssi_raw() + krk_rf_rssi_raw() + 1) / 2;
        if (r >= 0x1ff) {               /* all-ones: no answer from the chip, not +95 dBm */
            S.norf++;
            r = 0;
        }
        S.cur[i] = (u8)(r / 2);
        if (S.cur[i] >= S.peak[i] || S.peak_age == 0)
            S.peak[i] = S.cur[i];
        draw_bar(i);
        if (++S.pos >= SCOPE_N) {
            S.pos = 0;
            if (++S.peak_age >= 20)
                S.peak_age = 0;                 /* peaks decay to the current trace every 20 sweeps */
            S.sweeps++;
            return 1;
        }
    }
    return 0;
}

static void scope_enter(void)
{
    S.running = 0;
    S.center = RX_FREQ10;
    if (krk_rf_channel_is_digital()) {
        krk_line(1, "Analog channel only", KRK_WARN);
        krk_line(2, "(v1 does not disturb", KRK_DIM);
        krk_line(3, " the DMR modem)", KRK_DIM);
        return;
    }
    if (!krk_rf_band(S.center, &S.lo, &S.hi)) {
        krk_line(1, "No valid RX freq", KRK_WARN);
        return;
    }
    S.step_i = 1;
    S.settle_ms = 20;
    S.paused = 0;
    S.peak_age = 0;
    S.sweeps = 0;
    clamp_center();
    krk_rf_mute();
    S.running = 1;
    reset_trace();
    draw_footer();
}

static void scope_tick(u8 scan)
{
    if (!S.running)
        return;
    switch (scan) {
    case SCAN_UP:
        if (S.step_i + 1 < (int)(sizeof steps10 / sizeof steps10[0])) { S.step_i++; clamp_center(); reset_trace(); }
        break;
    case SCAN_DOWN:
        if (S.step_i > 0) { S.step_i--; clamp_center(); reset_trace(); }
        break;
    case SCAN_4:
        S.center -= steps10[S.step_i] * (SCOPE_N / 4); clamp_center(); reset_trace();
        break;
    case SCAN_6:
        S.center += steps10[S.step_i] * (SCOPE_N / 4); clamp_center(); reset_trace();
        break;
    case SCAN_STAR:
        if (S.settle_ms > 20) S.settle_ms -= 10;
        break;
    case SCAN_HASH:
        if (S.settle_ms < 60) S.settle_ms += 10;
        break;
    case SCAN_OK:
        S.paused = !S.paused;
        draw_header();
        break;
    default:
        break;
    }
    if (!S.paused && sweep_chunk())
        draw_footer();
}

/* The runner calls this on Back (krk.h): give the receiver back to the channel. */
static void scope_exit(void)
{
    if (S.running)
        krk_rf_restore();
    S.running = 0;
}

const struct krk_screen krk_scope_screen = {
    .label = "Band Scope",
    .enter = scope_enter,
    .tick = scope_tick,
    .exit = scope_exit,
};
