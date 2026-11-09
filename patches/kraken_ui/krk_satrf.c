/*
 * krk_satrf.c - F10 radio side of satellite mode: UTC source, the Doppler track and the 1 Hz tick that
 * applies it. docs/satellite-radio.md has the evidence; the short version:
 *
 *   * The radio keys and tunes from two RAM words, 10 Hz units: TX 0x00038468 (read by radio_init_sequence at
 *     every keying) and RX 0x0003846c (read by channel_change_apply_squelch 0x0300df08, which runs after every
 *     TX and every channel load). Writing the words and calling that stock function is the stock idiom
 *     (rf_frequency_adjust_and_display_refresh). No chip register, flash byte or channel struct is touched.
 *   * frequency_band_range_check_and_status_set(rx, tx) 0x0300d84c recomputes the LNA/PA band bits and bit 5 of
 *     0x0003e31b ("TX outside this model's TX ranges", which ptt_press_handler refuses). Stock calls it only at
 *     channel load, so a shifted TX word is checked by calling it ourselves BEFORE the word is written, and a
 *     failing candidate is replaced by the satellite's nominal uplink (stock's gate then decides about that).
 *   * Foreground (krk_satscr.c, "task menu", 2560-byte stack) does the soft-double SGP4 and builds the track:
 *     range-rate / c in ppb every 10 s for up to 30 minutes. This file's tick runs in the maintenance task
 *     (0x180-word stack): integer only, interpolates the track and writes the words. One writer.
 *
 * Safety: the tick does nothing while keyed (0x3fdc8 bit 3) or while a channel load is running (0x3e31c bit 3),
 * ends tracking when someone else changed the words (channel/VFO change, A/B swap, dual watch, CPS), when the
 * track is over, on request, and after 30 minutes; every ending except "someone else changed it" puts the
 * channel's own words back. Both words stay within +/-15 kHz of the satellite's nominal frequencies.
 */
#include "krk_satrf.h"
#include "krk_sat.h"
#include "krk_vframe.h"                     /* krk_rebooted() only (static inline) */
#include "krk_stock.h"

#define TXABORT   (*(volatile u8 *)0x0003fdb8)      /* bit 2 = call active/abort (dispatcher top) */
#define SCANFLAG  (*(volatile u8 *)(0x0003ff84 + 0x2b))
#define TZ_INDEX  (*(volatile u8 *)0x00038d22)      /* stock time-zone table index */
#define tz_entry          ((void (*)(u32 idx, sat_s8 *hour, u8 *min))0x03011aa4)

struct satr R;                                  /* ~480 bytes of the 1 KiB blob state */
static struct { int lat, lon, have; } lastfix;

static void r_reset(void)
{
    u8 *p = (u8 *)&R;
    unsigned i;
    u32 st = OS_TICKS;

    for (i = 0; i < sizeof R; i++)
        p[i] = 0;
    R.magic = SATR_MAGIC;
    R.stamp = st;
    lastfix.have = 0;
}

/* Review fixes 2 and 9: after a reboot the SRAM may still hold ST_ACTIVE with a valid magic, but the radio has
 * loaded its own words: nothing of ours is on the radio any more, so the state is DROPPED, never restored. */
void satr_ensure(void)
{
    if (R.magic != SATR_MAGIC || krk_rebooted(&R.stamp))
        r_reset();
}

/* ---- civil time: the integer date maths is krk_sat.c's (same blob) ------------------------------- */

u32 satr_unix(int y, int mo, int d, int h, int mi, int s)
{
    return (u32)krk_days_from_civil(y, mo, d) * 86400u + (u32)(h * 3600 + mi * 60 + s);
}

void satr_civil(u32 u, int *y, int *mo, int *d, int *h, int *mi, int *s)
{
    u32 rem = u % 86400u;

    krk_civil_from_days((long)(u / 86400u), y, mo, d);
    *h = (int)(rem / 3600);
    *mi = (int)(rem % 3600 / 60);
    *s = (int)(rem % 60);
}

/* ---- time source ----------------------------------------------------------------------------------- */

u32 satr_now(void)
{
    return R.base_unix + (OS_TICKS - R.base_tick) / 100;
}

static void set_base(u32 unix_s)
{
    R.base_unix = unix_s;
    R.base_tick = OS_TICKS;
    R.sync_tick = R.base_tick;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int d2(const char *p)
{
    if (p[0] < '0' || p[0] > '9' || p[1] < '0' || p[1] > '9')
        return -1;
    return (p[0] - '0') * 10 + (p[1] - '0');
}

/* UTC from stock's last valid RMC sentence (krk_grid.c has the same copy + checksum; its helper is
 * static). Field 1 = hhmmss(.sss), field 9 = ddmmyy. Returns 1 and the unix time, or 0. */
static int rmc_unix(u32 *out)
{
    char b[0x96];
    int i, n = 0, f = 0, hh, mm, ss, dd, mo, yy;
    const char *tf = 0, *df = 0;
    u8 x = 0;

    if (!(GPS_STATUS & 0x40))
        return 0;
    for (i = 0; i < (int)sizeof b - 1; i++) {
        char c = GPS_RMC[i];
        if (c == 0 || c == '\r' || c == '\n')
            break;
        b[n++] = c;
    }
    b[n] = 0;
    if (n < 30)
        return 0;
    for (i = b[0] == '$' ? 1 : 0; i < n && b[i] != '*'; i++)
        x ^= (u8)b[i];
    if (i + 2 >= n || b[i] != '*' || hexval(b[i + 1]) < 0 || hexval(b[i + 2]) < 0
        || ((hexval(b[i + 1]) << 4) | hexval(b[i + 2])) != x)
        return 0;
    for (i = 0; i < n && b[i] != '*'; i++) {
        if (b[i] == ',') {
            f++;
            if (f == 1) tf = b + i + 1;
            if (f == 9) df = b + i + 1;
        }
    }
    if (!tf || !df)
        return 0;
    hh = d2(tf); mm = d2(tf + 2); ss = d2(tf + 4);
    dd = d2(df); mo = d2(df + 2); yy = d2(df + 4);
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 60 || dd < 1 || dd > 31 || mo < 1 || mo > 12 || yy < 24)
        return 0;
    *out = satr_unix(2000 + yy, mo, dd, hh, mm, ss > 59 ? 59 : ss);
    return 1;
}

int satr_tz_min(void)
{
    sat_s8 h = 0;
    u8 m = 0;

    tz_entry(TZ_INDEX, &h, &m);                 /* stock: hour sign applies to the minutes too */
    return h * 60 + (h < 0 ? -(int)m : (int)m);
}

/* The RTC holds LOCAL time (docs/satellite-radio.md 5.2): regs 0..2 sec/min/hour (binary), 3/4 = days since
 * 1970-01-01. Reads over I2C: foreground only. */
static int rtc_unix(u32 *out)
{
    u32 days = rtc_reg(3) | ((u32)rtc_reg(4) << 8);
    u32 s = rtc_reg(0), m = rtc_reg(1), h = rtc_reg(2);
    sat_s32 u;

    if (s > 59 || m > 59 || h > 23)
        return 0;
    u = (sat_s32)(days * 86400u + h * 3600 + m * 60 + s) - satr_tz_min() * 60;
    if ((u32)u < 1704067200u || (u32)u > 4102444800u)       /* 2024-01-01 .. 2100: otherwise the RTC is unset */
        return 0;
    *out = (u32)u;
    return 1;
}

int satr_sync(int allow_rtc)
{
    u32 u;

    satr_ensure();
    if (rmc_unix(&u)) {
        set_base(u);
        R.tsrc = TS_RMC;
    } else if (allow_rtc && rtc_unix(&u)) {
        set_base(u);
        R.tsrc = TS_RTC;
    }
    return R.tsrc;
}

int satr_fix(int *lat, int *lon)
{
    struct krk_gps_fix f;

    if (krk_gps_get(&f)) {
        lastfix.lat = f.lat;
        lastfix.lon = f.lon;
        lastfix.have = 1;
        *lat = f.lat;
        *lon = f.lon;
        return 1;
    }
    if (lastfix.have) {
        *lat = lastfix.lat;
        *lon = lastfix.lon;
        return 2;
    }
    return 0;
}

/* ---- guards ---------------------------------------------------------------------------------------- */

static int keyed(void)
{
    return (CALL_ACTIVE & 0x08) != 0;
}

/* Review fix 3: also the raw PTT pin. Stock debounces it for 4 samples and then posts the event to its PTT task, so
 * the pin goes low well before CALL_ACTIVE bit 3 rises; while it is low nothing of ours touches a word or the chip. */
static int ptt_down(void)
{
    return ptt_pin_down() & 1;
}

static int busy(void)
{
    return keyed() || (LOADFLAGS & 0x08) || (TXABORT & 0x04) || ptt_down();
}

static const char *const errs[] = {
    "ok", "digital channel", "dual watch on", "TX/loading now", "scanning", "not selected side",
    "no time source", "no GPS position",
};

const char *satr_err(int code)
{
    return code >= 0 && code < (int)(sizeof errs / sizeof errs[0]) ? errs[code] : "?";
}

/* Preconditions shared by arming and the self-test (docs/satellite-radio.md 6.3). */
int satr_check(int need_tx)
{
    (void)need_tx;
    if (krk_channel_dmr())
        return 1;
    if (!krk_channel_on_selected() || !krk_channel_analog())
        return 5;
    if (dual_watch_state())
        return 2;
    if (busy())
        return 3;
    if (SCANFLAG & 1)
        return 4;
    return 0;
}

/* ---- the track ------------------------------------------------------------------------------------- */

/* Time-accelerated pass: nominal frequencies swept ~ -18 kppb .. +18 kppb (about +/-7.8 kHz at 435 MHz) over
 * ~3 minutes instead of ~10, so a bench session sees the whole retune behaviour in one go. */
void satr_fake_track(void)
{
    int i;

    for (i = 0; i < 19; i++) {
        int x = i - 9, ax = x < 0 ? -x : x;
        R.ppb[i] = (sat_s16)(24000 * 3 * x / (3 * ax + 9));
    }
    R.n = 19;
}

/* ppb at dt seconds after t0: 3-point Lagrange through the nearest samples, 10 s apart (error < ~100 Hz at
 * 435 MHz at closest approach of a 400 km pass). */
static int interp(u32 dt)
{
    int n = R.n, i, x, pm, p0, pp;

    if (n < 3)
        return n ? R.ppb[0] : 0;
    if (dt >= (u32)(n - 1) * SATR_STEP)
        return R.ppb[n - 1];
    i = (int)((dt + SATR_STEP / 2) / SATR_STEP);
    if (i < 1) i = 1;
    if (i > n - 2) i = n - 2;
    x = (int)dt - i * SATR_STEP;                /* -10 .. +10 */
    pm = R.ppb[i - 1]; p0 = R.ppb[i]; pp = R.ppb[i + 1];
    return p0 + (pp - pm) * x / (2 * SATR_STEP) + (pp + pm - 2 * p0) * x * x / (2 * SATR_STEP * SATR_STEP);
}

/* nominal(10 Hz units) * ppb / 1e9 without 64-bit maths: nom*r/1000 = (nom/1000)*r + (nom%1000)*r/1000. */
static int delta10(u32 nom, int r)
{
    sat_s32 v = (sat_s32)(nom / 1000) * r + (sat_s32)(nom % 1000) * r / 1000;

    return (v + (v < 0 ? -500000 : 500000)) / 1000000;
}

/* Nominal +/- Doppler for a ppb value: rx = f(1 - r), tx = f(1 + r). Exported for the screen and tests. */
u32 satr_rx_for(int ppb)
{
    return R.nom_rx - delta10(R.nom_rx, ppb) + R.trim_hz / 10;
}
u32 satr_tx_for(int ppb)
{
    return R.nom_tx + delta10(R.nom_tx, ppb) + R.trim_hz / 10;
}

/* ---- arming and ending ----------------------------------------------------------------------------- */

static int in_band(u32 f10)
{
    return (f10 >= 13600000u && f10 <= 17400000u) || (f10 >= 40000000u && f10 <= 48000000u);
}

int satr_arm(int sat, u32 dl_hz, u32 ul_hz, u32 t0, int fake)
{
    int e = satr_check(0);

    if (e)
        return e;
    if (R.state == ST_ACTIVE || R.state == ST_RESTORE)
        return 3;
    if (!in_band(dl_hz / 10) || (ul_hz && !in_band(ul_hz / 10)))
        return 1;
    R.sat = (u8)sat;
    R.fake = (u8)fake;
    R.t0 = t0;
    R.nom_rx = dl_hz / 10;
    R.nom_tx = ul_hz / 10;
    R.saved_rx = RX_FREQ10;
    R.saved_tx = TX_FREQ10;
    R.tx_dead = (ul_hz == 0 || (krk_channel_flags() & 0x08)) ? 1 : 0;   /* no uplink, or the channel may not transmit */
    R.tx_blocked = 0;
    R.owned = 0;
    R.stop_req = 0;
    R.why = WHY_NONE;
    R.n_tick = R.n_skip = R.n_retune = 0;
    R.rssi_pk = 0;
    R.armed_tick = OS_TICKS;
    R.state = ST_ACTIVE;
    return 0;
}

void satr_stop(void)
{
    if (R.magic == SATR_MAGIC && (R.state == ST_ACTIVE || R.state == ST_RESTORE))
        R.stop_req = 1;
}

void satr_cancel_build(void)
{
    if (R.magic == SATR_MAGIC && R.state == ST_BUILD)
        R.state = ST_IDLE;
}

/* Puts the channel's own words back, but ONLY if the words on the radio are still exactly what we last wrote
 * (review fix 2): after a channel change, A/B swap, CPS write or reboot they belong to someone else and are left
 * alone. Returns 1 when done (restored or dropped), 0 when keyed/loading/PTT down (try again next second). */
static int restore(void)
{
    if (busy())
        return 0;
    if (R.owned) {
        if (RX_FREQ10 == R.owned_rx && TX_FREQ10 == R.owned_tx) {
            RX_FREQ10 = R.saved_rx;
            TX_FREQ10 = R.saved_tx;
            stock_band_check(R.saved_rx, R.saved_tx);
            channel_change_apply_squelch();
        } else {
            R.why = WHY_LOST;
        }
    }
    R.owned = 0;                                /* nothing of ours is on the radio any more */
    R.rx_pend = 0;
    return 1;
}

static void finish(int why, int do_restore)
{
    R.why = (u8)why;
    R.stop_req = 0;
    R.state = ST_IDLE;
    if (!do_restore) {
        R.owned = 0;
        R.rx_pend = 0;
    } else if (!restore()) {
        R.state = ST_RESTORE;                   /* R.owned stays set: the ownership test runs at the retry */
    }
}

/* ---- the 1 Hz tick (maintenance task: integer only, small frame) ----------------------------------- */

static void fake_marker(u32 dt, int ppb, u32 rx, u32 tx, int retuned)
{
    char l[KRK_LINE_BUF];

    l[0] = 0;
    krk_cat(l, "SATF t=");
    krk_cati(l, (int)dt);
    krk_cat(l, " ppb=");
    krk_cati(l, ppb);
    krk_cat(l, " rx=");
    krk_cati(l, (int)rx);
    krk_cat(l, " tx=");
    krk_cati(l, (int)tx);
    krk_cat(l, retuned ? " RETUNE\r\n" : "\r\n");
    krk_uart_puts(l);
}

void krk_sat_tick(void)
{
    u32 now, dt, rx, tx, rxw, txw;
    int r, retune;

    if (R.magic != SATR_MAGIC)
        return;
    if (krk_rebooted(&R.stamp)) {               /* warm reset with stale SRAM: drop the state, never restore */
        r_reset();
        return;
    }
    if (R.state == ST_RESTORE) {
        if (restore())
            R.state = ST_IDLE;
        return;
    }
    if (R.state != ST_ACTIVE)
        return;
    R.n_tick++;
    if (R.stop_req) {
        finish(WHY_USER, 1);
        return;
    }
    now = satr_now();
    dt = now > R.t0 ? now - R.t0 : 0;
    if (OS_TICKS - R.armed_tick > SATR_CAP_S * 100u) {
        finish(WHY_CAP, 1);
        return;
    }
    if (dt > (u32)(R.n ? R.n - 1 : 0) * SATR_STEP + 30) {
        finish(WHY_END, 1);
        return;
    }
    if (busy()) {
        R.n_skip++;
        return;
    }
    if (R.owned && (RX_FREQ10 != R.owned_rx || TX_FREQ10 != R.owned_tx)) {
        finish(WHY_LOST, 0);                    /* the new channel stays as stock loaded it */
        return;
    }
    if (R.rx_pend) {                            /* an RX word written earlier whose chip retune was skipped (PTT/TX) */
        channel_change_apply_squelch();
        R.rx_pend = 0;
    }
    if (!R.fake && OS_TICKS - R.sync_tick > 6000) {         /* cheap resync of the free-running clock every 60 s */
        u32 u;
        if (rmc_unix(&u) && (u > now ? u - now : now - u) <= 3)
            set_base(u);
        else
            R.sync_tick = OS_TICKS - 3000;                  /* retry in 30 s; a stale or torn sentence is ignored */
    }

    r = interp(dt);
    R.last_ppb = r;
    rx = satr_rx_for(r);
    tx = R.tx_dead ? TX_FREQ10 : satr_tx_for(r);
    if ((rx > R.nom_rx ? rx - R.nom_rx : R.nom_rx - rx) > SATR_CLAMP
        || (!R.tx_dead && (tx > R.nom_tx ? tx - R.nom_tx : R.nom_tx - tx) > SATR_CLAMP)) {
        finish(WHY_RANGE, 1);
        return;
    }

    /* RX: only when it moved by the hysteresis (every retune costs an audio chop), always the first time */
    retune = !R.owned || (rx > RX_FREQ10 ? rx - RX_FREQ10 : RX_FREQ10 - rx) >= SATR_HYST;
    rxw = retune ? rx : RX_FREQ10;
    /* TX: the candidate is checked with stock's own range test before it is written */
    txw = tx;
    stock_band_check(rxw, txw);
    R.tx_blocked = 0;
    if ((BAND_FLAGS & 0x20) && !R.tx_dead) {
        txw = R.nom_tx;                         /* shifted word not allowed: nominal uplink, stock decides */
        stock_band_check(rxw, txw);
        R.tx_blocked = 1;
    }
    TX_FREQ10 = txw;
    R.cur_tx = txw;
    if (retune && ptt_down()) {                 /* pressed since the check at the top: leave the RX word for a later pass */
        retune = 0;
        rxw = RX_FREQ10;
    }
    if (retune) {
        RX_FREQ10 = rxw;
        R.cur_rx = rxw;
        R.n_retune++;
        /* Review fix 3: stock offers no lock that a patch task can hold across the retune (the chip bus semaphore
         * is taken inside channel_change_apply_squelch, and holding the scheduler across a pend would deadlock), so the retune is
         * skipped whenever the PTT pin is down or the radio is keyed, tested right before the call; a skipped
         * retune stays pending (rx_pend) and is applied by a later pass or by stock after the over.
         * Residual window: a press that lands during the few ms of channel_change_apply_squelch still needs stock's 4-sample
         * debounce plus the PTT task's own work before its TX retune, which is longer than our retune. */
        if (keyed() || ptt_down())
            R.rx_pend = 1;
        else
            channel_change_apply_squelch();
    }
    R.owned_rx = RX_FREQ10;
    R.owned_tx = TX_FREQ10;
    R.owned = 1;
    if (R.fake)
        fake_marker(dt, r, RX_FREQ10, TX_FREQ10, retune);
}

/* ---- self-test helpers (foreground) ---------------------------------------------------------------- */

int satr_apply_rx(u32 rx10)
{
    if (busy())
        return -1;
    RX_FREQ10 = rx10;
    R.owned_rx = rx10;                          /* the self-test owns the words too: its restore checks them */
    R.owned_tx = TX_FREQ10;
    R.owned = 1;
    channel_change_apply_squelch();
    return 0;
}

void satr_capture(u32 dl_hz)
{
    R.nom_rx = dl_hz / 10;
    R.saved_rx = RX_FREQ10;
    R.saved_tx = TX_FREQ10;
}

/* Same guards as restore() (review fix 4); returns 1 when the words are back (or were not ours), 0 when deferred
 * to the maintenance tick (keyed / loading / PTT down), which retries every second. */
int satr_restore_now(void)
{
    if (restore())
        return 1;
    R.state = ST_RESTORE;
    return 0;
}

unsigned satr_rssi(void)
{
    return fd6818_reg_read(0x67) & 0x1ff;
}

/* Standby tags (blob 2, krk_tags.c): 1 while tracking owns the channel's frequencies. */
int krk_sat_active(void)
{
    return R.magic == SATR_MAGIC && (R.state == ST_ACTIVE || R.state == ST_RESTORE);
}
