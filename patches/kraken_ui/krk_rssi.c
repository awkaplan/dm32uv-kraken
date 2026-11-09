/*
 * krk_rssi.c - F2: numeric RSSI / S-meter screen.
 *
 * Source: FD6818 REG_67H[8:0] ("rssi_sq", 0.5 dB/step), the register stock's own meter reads
 * (rssi_rolling_average_meter_update @0x0305ffe4 via reg_0x67_lower9_read; dm32uv-re
 * docs/tx-power-control.md section 2). Read here directly with fd6818_reg_read, which takes the
 * FD6818 bus semaphore itself, so it is safe from the menu task.
 *
 * dBm = raw/2 - 160 is the FD6818 datasheet's formula, NOT calibrated on this radio (checklist
 * P5(c)); the screen says "uncal" until a reference measurement replaces KRK_RSSI_OFFSET_HALF_DB.
 * S-units: IARU Region 1 above 30 MHz, S9 = -93 dBm, 6 dB per S-unit.
 */
#include "krk.h"
#include "krk_stock.h"

#define FD6818_REG_RSSI 0x67

/* Stock's 10-sample mean (raw units, clamped to 255) and the check stock uses to decide whether
 * to update it (inferred to be "receiving"; docs/tx-power-control.md section 2). */

/* dBm in half-dB units: raw - 320 (= 2 * (raw/2 - 160)). A calibration lands here. */
#define KRK_RSSI_OFFSET_HALF_DB (-320)

#define BAR_Y      (KRK_Y0 + 5 * KRK_LINE_H + 4)
#define BAR_H      16
#define BAR_X0     KRK_X0
#define BAR_X1     (KRK_SCREEN_W - KRK_X0)
#define BAR_MIN_DBM (-140)
#define BAR_MAX_DBM (-40)

static struct {
    int min_h, max_h;           /* half-dB, since entry */
    int peak_h, peak_age;       /* bar peak hold */
    int sum_h, n;               /* 1 s average */
    int avg_h;
    int last_bar, last_peak;
    u32 line_hash[KRK_LINES];   /* redraw a line only when its text changes */
    int s16;                    /* smoothed reading, half-dB x 16 (meter ballistics) */
    u8 have_s, text_div;
} rssi_st;

/* Meter ballistics: 4 register reads averaged per 100 ms tick, then a fast-attack (1/2) /
 * slow-decay (1/8, ~0.8 s) envelope, so a fading FM signal reads steadily (hardware feedback
 * 2026-10-15: the raw 10 Hz value was too jumpy to read). The numbers refresh about 3 times a
 * second and show whole dB; the bar follows the smoothed value every tick. */
#define READS_PER_TICK 4
#define TEXT_EVERY     3
/* Digital channels (TDMA: one 30 ms slot of every 60 ms is ours): four back-to-back reads see one phase of
 * the burst and their mean cycled -47.5/-48.3/-54.4 dB at 3 Hz on a steady DMR carrier (hardware 2026-11-01).
 * So the reads are DIG_GAP_MS apart (3 gaps = 60 ms, one whole frame, gap < the 30 ms slot) and the tick
 * takes the MAXIMUM, which is the on-slot level; the 1 s window then holds DIG_TICKS_PER_S ticks. */
#define DIG_GAP_MS     20
#define DIG_TICKS_PER_S (1000 / (KRK_TICK_MS + (READS_PER_TICK - 1) * DIG_GAP_MS))

static void fmt_half_db(char *buf, int h)
{
    buf[0] = 0;
    krk_catfix(buf, h * 5, 10, 0);          /* h/2 with one decimal: "-12.5" */
}

static void fmt_s_units(char *buf, int h)
{
    int dbm2 = h, s9 = -93 * 2;

    buf[0] = 0;
    if (dbm2 >= s9) {
        krk_cat(buf, "S9+");
        krk_cati(buf, (dbm2 - s9) / 2);
        krk_cat(buf, "dB");
    } else {
        int s = 9 - (s9 - dbm2 + 11) / 12;   /* round toward the lower S-unit */
        krk_cat(buf, "S");
        krk_cati(buf, s < 0 ? 0 : s);
    }
}

static int bar_x(int h)
{
    int dbm2 = h, lo = BAR_MIN_DBM * 2, hi = BAR_MAX_DBM * 2;

    if (dbm2 < lo) dbm2 = lo;
    if (dbm2 > hi) dbm2 = hi;
    return BAR_X0 + (dbm2 - lo) * (BAR_X1 - BAR_X0) / (hi - lo);
}

static void line_value(int n, const char *label, const char *value, u16 fg)
{
    char buf[KRK_LINE_BUF];

    buf[0] = 0;
    krk_cat(buf, label);
    krk_cat(buf, value);
    krk_line_cached(rssi_st.line_hash, n, buf, fg);     /* padded to the full line instead of cleared: no flicker at the tick rate */
}

static void rssi_enter(void)
{
    rssi_st.min_h = 0x7fff;
    rssi_st.max_h = -0x7fff;
    rssi_st.peak_h = -0x7fff;
    rssi_st.peak_age = 0;
    rssi_st.sum_h = rssi_st.n = 0;
    rssi_st.avg_h = -0x7fff;
    rssi_st.have_s = 0;
    rssi_st.text_div = TEXT_EVERY - 1;
    rssi_st.last_bar = rssi_st.last_peak = -1;
    for (int i = 0; i < KRK_LINES; i++)
        rssi_st.line_hash[i] = 0;
    krk_line(0, "RSSI (dBm, uncal)", KRK_DIM);
    display_fill_rect(BAR_X0, BAR_Y, BAR_X1, BAR_Y + BAR_H, KRK_DIM);
    krk_line(9, "Back: exit", KRK_DIM);
}

static void rssi_tick(u8 scan)
{
    char v[KRK_LINE_BUF];
    int raw = 0, k, h, sm;
    int bx, px;
    int dig = krk_channel_mode() == 1;      /* selected side (dual-watch safe); unknown reads as analog */

    (void)scan;
    for (k = 0; k < READS_PER_TICK; k++) {
        int x;

        if (dig && k)
            os_time_dly_ms(DIG_GAP_MS);
        x = (int)(fd6818_reg_read(FD6818_REG_RSSI) & 0x1ff);
        if (x == 0x1ff) {
            raw = 0x1ff;
            break;
        }
        if (dig)
            raw = x > raw ? x : raw;
        else
            raw += x;
    }
    if (raw != 0x1ff && !dig)
        raw = (raw + READS_PER_TICK / 2) / READS_PER_TICK;
    h = raw + KRK_RSSI_OFFSET_HALF_DB;
    if (raw == 0x1ff) {
        /* all-ones: no answer from the chip (the QEMU model, or a bus fault); not +95.5 dBm */
        line_value(1, "now n/a (no RF)", "", KRK_WARN);
        return;
    }
    if (h < rssi_st.min_h) rssi_st.min_h = h;
    if (h > rssi_st.max_h) rssi_st.max_h = h;
    rssi_st.sum_h += h;
    if (++rssi_st.n >= (dig ? DIG_TICKS_PER_S : 1000 / KRK_TICK_MS)) {
        rssi_st.avg_h = rssi_st.sum_h / rssi_st.n;
        rssi_st.sum_h = rssi_st.n = 0;
    }
    if (!rssi_st.have_s) {
        rssi_st.s16 = h * 16;
        rssi_st.have_s = 1;
    } else if (h * 16 > rssi_st.s16) {
        rssi_st.s16 += (h * 16 - rssi_st.s16) / 2;
    } else {
        rssi_st.s16 += (h * 16 - rssi_st.s16) / 8;
    }
    sm = rssi_st.s16 >= 0 ? (rssi_st.s16 + 8) / 16 : -((-rssi_st.s16 + 8) / 16);
    if (sm >= rssi_st.peak_h || ++rssi_st.peak_age > 20) {
        rssi_st.peak_h = sm;
        rssi_st.peak_age = 0;
    }

    if (++rssi_st.text_div >= TEXT_EVERY) {
        rssi_st.text_div = 0;
        /* whole dB: round half-dB units to the nearest even value */
        fmt_half_db(v, sm >= 0 ? (sm + 1) / 2 * 2 : -((-sm + 1) / 2 * 2));
        {
            char *dot = v;
            while (*dot && *dot != '.')
                dot++;
            *dot = 0;               /* drop the ".0" */
        }
        line_value(1, "now ", v, KRK_FG);
        fmt_s_units(v, sm);
        line_value(2, "    ", v, KRK_ACCENT);
    }
    if (rssi_st.avg_h > -0x7fff) {
        fmt_half_db(v, rssi_st.avg_h);
        line_value(3, "1s  ", v, KRK_FG);
    }
    fmt_half_db(v, rssi_st.min_h);
    krk_cat(v, " / ");
    {
        char m[12];
        fmt_half_db(m, rssi_st.max_h);
        krk_cat(v, m);
    }
    line_value(4, "", v, KRK_DIM);

    /* bar: redraw only the part that changed */
    bx = bar_x(sm);
    px = bar_x(rssi_st.peak_h);
    if (bx != rssi_st.last_bar || px != rssi_st.last_peak) {
        display_fill_rect(BAR_X0, BAR_Y, bx, BAR_Y + BAR_H, KRK_ACCENT);
        display_fill_rect(bx, BAR_Y, BAR_X1, BAR_Y + BAR_H, KRK_DIM);
        if (px > bx + 2)
            display_fill_rect(px - 2, BAR_Y, px, BAR_Y + BAR_H, KRK_WARN);
        rssi_st.last_bar = bx;
        rssi_st.last_peak = px;
    }

    if (stock_rx_active()) {
        fmt_half_db(v, (int)*STOCK_RSSI_MEAN + KRK_RSSI_OFFSET_HALF_DB);
        line_value(7, "RX avg ", v, KRK_FG);
    } else {
        line_value(7, "RX: idle", "", KRK_DIM);
    }
}

const struct krk_screen krk_rssi_screen = {
    .label = "RSSI Meter",
    .enter = rssi_enter,
    .tick = rssi_tick,
};
