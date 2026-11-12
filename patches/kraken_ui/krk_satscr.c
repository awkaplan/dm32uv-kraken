/*
 * krk_satscr.c - F10 "Satellites" screen (KRAKEN slot 5, blob kraken_ui3).
 *
 * List view: the compiled-in satellites (krk_sat_tles.c) with their next pass (AOS, max elevation) from the GPS
 * position and UTC, one satellite computed per screen tick so keys stay responsive. OK opens a satellite.
 * Info view: once a second az / el / range / range-rate and the Doppler-corrected RX and TX. OK arms tracking
 * (the 1 Hz tick in krk_satrf.c then writes the corrected words, also after Back), OK again stops it; Up/Down
 * trims both words by 100 Hz; * runs the Doppler self-test sweep; # starts a time-accelerated fake pass.
 *
 * Stack: this runs in "task menu" (OSTaskCreate 0x03035ea0..: 0x280 words = 2560 bytes, shared with the stock
 * menu frames above us). The big objects (struct krk_sat 288 bytes, observer, track) are static; the deepest
 * path is draw -> krk_sat_look/next_pass -> krk_sat_propagate with a few hundred bytes of frames. The in-target
 * test measures the real headroom (docs/satellite-mode.md, Implementation).
 */
#include "krk.h"
#include "krk_sat.h"
#include "krk_satdb.h"
#include "krk_satrf.h"
#include "krk_stock.h"

#define TLE_OLD_DAYS 14
#define PASS_MIN_EL 5.0
#define C_KM_S 299792.458

enum { V_LIST, V_INFO };

/* screen-only (sat, obs, pc, loaded_idx): sat_enter clears pc[].st and loaded_idx; obs is read only when ui.have_obs (set with obs) */
static struct krk_sat sat KRK_SCREEN_BSS;                      /* the satellite loaded for the info view / pass search */
static struct krk_sat_obs obs KRK_SCREEN_BSS;
static struct { u32 aos; u16 dur; u8 el; u8 st; } pc[KRK_SAT_MAXN] KRK_SCREEN_BSS;     /* st: 0 todo, 1 pass, 2 none in 24 h, 3 bad */
static struct {
    u8 view, sel, top, loc, have_obs, loaded, bi, pad;
    u32 last_s, retry;
    int tz;
    const char *msg;
} ui;
static const char done_msg[] = "self-test done";
static int loaded_idx KRK_SCREEN_BSS;                          /* satellite index + 1 held in `sat`; 0 = none (no .data: SRAM is not initialised) */

static double t70(void)
{
    return krk_sat_time(1970, 1, 1, 0, 0, 0);
}

static double unix_to_t(u32 u)
{
    return t70() + (double)u / 86400.0;
}

static u32 t_to_unix(double t)
{
    return (u32)((t - t70()) * 86400.0 + 0.5);
}

/* ---- small formatting helpers (lines are 19 columns; every buffer is KRK_LINE_BUF) --------------------- */

static void cat_n(char *l, int v, int w, char pad)
{
    char t[12], p[2] = { pad, 0 };
    int len, k;

    krk_itoa(v, t);
    for (len = 0; t[len]; len++)
        ;
    for (k = len; k < w; k++)
        krk_cat(l, p);
    krk_cat(l, t);
}

static void cat_hm(char *l, u32 u, int loc)
{
    int y, mo, d, h, mi, s;

    satr_civil(u + (loc ? (u32)(ui.tz * 60) : 0), &y, &mo, &d, &h, &mi, &s);
    cat_n(l, h, 2, '0');
    krk_cat(l, ":");
    cat_n(l, mi, 2, '0');
}

static void cat_mhz(char *l, u32 hz)
{
    krk_catfix(l, (int)(hz / 10), 100000, 3);
}

static void cat_s(char *l, const char *s)
{
    krk_cat(l, s);
}

/* TLE age in whole days from line 1's epoch (cols 19-32: yyddd.dddddddd), integer maths. -999 if unreadable. */
static int tle_age(const struct krk_sat_tle *t)
{
    const char *e = t->l1 + 18;
    int yy = (e[0] - '0') * 10 + (e[1] - '0');
    int doy = (e[2] - '0') * 100 + (e[3] - '0') * 10 + (e[4] - '0');
    u32 epoch;

    if (yy < 0 || yy > 99 || doy < 1 || doy > 366)
        return -999;
    epoch = satr_unix(yy < 57 ? 2000 + yy : 1900 + yy, 1, 1, 0, 0, 0) + (u32)(doy - 1) * 86400u;
    return (int)(((sat_s32)satr_now() - (sat_s32)epoch) / 86400);
}

/* ---- pass list ------------------------------------------------------------------------------------ */

static int load_sat(int i)
{
    int e;

    if (loaded_idx == i + 1)
        return 0;
    loaded_idx = 0;
    e = krk_sat_init(&sat, krk_sat_tles[i].l1, krk_sat_tles[i].l2);
    if (!e)
        loaded_idx = i + 1;
    return e;
}

static void compute_pass(int i)
{
    struct krk_sat_pass p;
    int r;

    pc[i].st = 3;
    if (load_sat(i))
        return;
    r = krk_sat_next_pass(&sat, &obs, unix_to_t(satr_now()), 1.0, PASS_MIN_EL, &p);
    if (r < 0)
        return;
    if (r == 0) {
        pc[i].st = 2;
        return;
    }
    pc[i].aos = (p.flags & KRK_SAT_PASS_INPROGRESS) ? 0 : t_to_unix(p.aos);
    pc[i].dur = (u16)(t_to_unix(p.los) - t_to_unix(p.aos));
    pc[i].el = (u8)(p.max_el > 90.0 ? 90 : (int)(p.max_el + 0.5));
    pc[i].st = 1;
}

static void draw_list(void)
{
    char l[KRK_LINE_BUF];
    int r, i;

    l[0] = 0;
    cat_s(l, "Satellites ");
    cat_s(l, ui.loc ? "LOC " : "UTC ");
    cat_s(l, R.tsrc == TS_RMC ? "GPS" : R.tsrc == TS_RTC ? "RTC" : "---");
    krk_line(0, l, R.tsrc ? KRK_DIM : KRK_WARN);
    for (r = 0; r < 8; r++) {
        i = ui.top + r;
        if (i >= (int)krk_sat_ntles) {
            krk_line(1 + r, "", KRK_FG);
            continue;
        }
        l[0] = 0;
        cat_s(l, i == ui.sel ? ">" : " ");
        cat_s(l, krk_sat_tles[i].name);
        {
            int len;
            for (len = 0; l[len]; len++)
                ;
            for (; len < 10; len++)
                l[len] = ' ', l[len + 1] = 0;
        }
        if (pc[i].st == 1) {
            if (pc[i].aos == 0) {
                cat_s(l, "NOW  ");
            } else {
                cat_hm(l, pc[i].aos, ui.loc);
            }
            cat_s(l, " ");
            cat_n(l, pc[i].el, 2, ' ');
        } else {
            cat_s(l, pc[i].st == 0 ? (ui.have_obs ? "..." : "no fix") : pc[i].st == 2 ? "none 24h" : "TLE bad");
        }
        {
            int a = tle_age(&krk_sat_tles[i]);
            if (a > TLE_OLD_DAYS) {
                int len;
                for (len = 0; l[len]; len++)
                    ;
                while (len < 18)
                    l[len++] = ' ';
                l[len++] = '!';
                l[len] = 0;
            }
        }
        krk_line(1 + r, l, i == ui.sel ? KRK_ACCENT : KRK_FG);
    }
    krk_line(9, "OK:open #:UTC/local", KRK_DIM);
}

/* ---- info view ------------------------------------------------------------------------------------- */

static const char *const whys[] = { "", "stopped", "pass over", "30 min cap", "channel changed", "range clamp" };

static void draw_info(void)
{
    struct krk_sat_look L;
    const struct krk_sat_tle *t = &krk_sat_tles[ui.sel];
    char l[KRK_LINE_BUF];
    u32 now = satr_now(), rxhz = 0, txhz = 0;
    int age = tle_age(t), rr10, ok, i;

    ok = ui.have_obs && R.tsrc && !load_sat(ui.sel) && !krk_sat_look(&sat, &obs, unix_to_t(now), &L);
    l[0] = 0;
    cat_s(l, t->name);
    cat_s(l, " TLE ");
    cat_n(l, age, 0, ' ');
    cat_s(l, "d");
    if (age > TLE_OLD_DAYS)
        cat_s(l, " OLD!");
    krk_line(0, l, age > TLE_OLD_DAYS ? KRK_WARN : KRK_FG);

    if (ok) {
        int el = (int)(L.el < 0 ? L.el - 0.5 : L.el + 0.5);
        l[0] = 0;
        cat_s(l, "Az ");
        cat_n(l, (int)(L.az + 0.5) % 360, 3, ' ');
        cat_s(l, " El ");
        cat_n(l, el, 3, ' ');
        krk_line(1, l, el > 0 ? (el >= 10 ? KRK_ACCENT : KRK_FG) : KRK_DIM);
        rr10 = (int)(L.rr * 10.0 + (L.rr < 0 ? -0.5 : 0.5));
        l[0] = 0;
        cat_s(l, "Rng ");
        cat_n(l, (int)(L.range + 0.5), 0, ' ');
        cat_s(l, "km ");
        cat_s(l, rr10 < 0 ? "-" : "+");
        cat_n(l, (rr10 < 0 ? -rr10 : rr10) / 10, 0, ' ');
        cat_s(l, ".");
        cat_n(l, (rr10 < 0 ? -rr10 : rr10) % 10, 0, ' ');
        krk_line(2, l, KRK_FG);
        rxhz = krk_sat_round_hz(krk_sat_doppler_rx(t->dl_hz, L.rr), 10) + (u32)R.trim_hz;
        txhz = t->ul_hz ? krk_sat_round_hz(krk_sat_doppler_tx(t->ul_hz, L.rr), 10) + (u32)R.trim_hz : 0;
    } else {
        krk_line(1, ui.have_obs ? (R.tsrc ? "orbit error" : "no time source") : "no GPS position", KRK_WARN);
        krk_line(2, "", KRK_FG);
        rxhz = t->dl_hz;
        txhz = t->ul_hz;
    }
    l[0] = 0;
    cat_s(l, "RX ");
    cat_mhz(l, rxhz);
    cat_s(l, ok ? " MHz" : " nom");
    krk_line(3, l, KRK_FG);
    l[0] = 0;
    if (txhz) {
        cat_s(l, "TX ");
        cat_mhz(l, txhz);
        cat_s(l, ok ? " MHz" : " nom");
    } else {
        cat_s(l, "TX none (RX only)");
    }
    krk_line(4, l, KRK_FG);
    l[0] = 0;
    if (t->pl_x10) {
        cat_s(l, "PL ");
        cat_n(l, t->pl_x10 / 10, 0, ' ');
        cat_s(l, ".");
        cat_n(l, t->pl_x10 % 10, 0, ' ');
        cat_s(l, " ");
    }
    cat_s(l, "trim ");
    cat_s(l, R.trim_hz < 0 ? "-" : "+");
    cat_n(l, R.trim_hz < 0 ? -R.trim_hz : R.trim_hz, 0, ' ');
    cat_s(l, "Hz");
    krk_line(5, l, KRK_DIM);

    l[0] = 0;
    if (R.state == ST_BUILD) {
        cat_s(l, "building track ");
        cat_n(l, R.n * 100 / SATR_NMAX, 0, ' ');
        cat_s(l, "%");
        krk_line(6, l, KRK_WARN);
    } else if (R.state == ST_ACTIVE || R.state == ST_RESTORE) {
        u32 el = (OS_TICKS - R.armed_tick) / 100, left;
        u32 endt = (u32)(R.n ? R.n - 1 : 0) * SATR_STEP + 30;

        if (endt > SATR_CAP_S)
            endt = SATR_CAP_S;
        left = el < endt ? endt - el : 0;
        if (R.state == ST_RESTORE) {
            cat_s(l, "restoring...");
        } else {
            cat_s(l, R.fake ? "FAKE " : "TRACK ");
            cat_n(l, (int)(left / 60), 2, '0');
            cat_s(l, ":");
            cat_n(l, (int)(left % 60), 2, '0');
            cat_s(l, R.tx_blocked ? " TX!" : R.tx_dead ? " RX" : " ok");
        }
        krk_line(6, l, KRK_ACCENT);
    } else {
        cat_s(l, "idle");
        if (R.why)
            cat_s(l, ": "), cat_s(l, whys[R.why]);
        krk_line(6, l, KRK_DIM);
    }

    l[0] = 0;
    i = ui.sel;
    if (R.state == ST_ACTIVE && R.n_tick) {
        /* live RSSI (REG_67H[8:0], uncalibrated) with peak-hold, so a brief signal
         * on an otherwise silent pass is still visible, plus the retune count (the
         * Doppler offset itself is on the RX line above). */
        unsigned rssi = satr_rssi();

        if (rssi > R.rssi_pk)
            R.rssi_pk = (u16)rssi;
        cat_s(l, "S");
        cat_n(l, (int)rssi, 0, ' ');
        cat_s(l, " pk");
        cat_n(l, (int)R.rssi_pk, 0, ' ');
        cat_s(l, " rt");
        cat_n(l, (int)R.n_retune, 0, ' ');
    } else if (pc[i].st == 1 && ui.have_obs) {
        cat_s(l, pc[i].aos ? "AOS " : "LOS ");
        cat_hm(l, pc[i].aos ? pc[i].aos : now + pc[i].dur, ui.loc);
        cat_s(l, " max ");
        cat_n(l, pc[i].el, 0, ' ');
    }
    krk_line(7, l, KRK_DIM);
    krk_line(8, ui.msg ? ui.msg : (R.state == ST_ACTIVE ? "Back keeps tracking" : "PTT: from standby"), ui.msg == done_msg ? KRK_ACCENT : ui.msg ? KRK_WARN : KRK_DIM);
    krk_line(9, R.state == ST_ACTIVE ? "OK:stop Back:keeps" : "OK:trk *:tst #:fake", KRK_DIM);
}

/* ---- track building (incremental, ~40 ms per screen tick) ----------------------------------------- */

static void build_step(void)
{
    struct krk_sat_look L;
    u32 t_end = OS_TICKS + 4;

    while (R.n < SATR_NMAX && (sat_s32)(OS_TICKS - t_end) < 0) {
        if (krk_sat_look(&sat, &obs, unix_to_t(R.t0 + (u32)R.n * SATR_STEP), &L)) {
            R.state = ST_IDLE;
            ui.msg = "orbit error";
            return;
        }
        R.ppb[R.n++] = (sat_s16)(int)(L.rr * (1e9 / C_KM_S) + (L.rr < 0 ? -0.5 : 0.5));
    }
    if (R.n >= SATR_NMAX) {
        const struct krk_sat_tle *t = &krk_sat_tles[ui.sel];
        int e = satr_arm(ui.sel, t->dl_hz, t->ul_hz, R.t0, 0);

        ui.msg = e ? satr_err(e) : 0;
        if (e)
            R.state = ST_IDLE;
    }
}

static void start_build(void)
{
    int e;

    ui.msg = 0;
    if (!ui.have_obs) { ui.msg = "no GPS position"; return; }
    if (!R.tsrc) { ui.msg = "no time source"; return; }
    if (load_sat(ui.sel)) { ui.msg = "TLE bad"; return; }
    e = satr_check(0);
    if (e) { ui.msg = satr_err(e); return; }
    R.t0 = satr_now();
    R.n = 0;
    R.state = ST_BUILD;
}

static void start_fake(void)
{
    const struct krk_sat_tle *t = &krk_sat_tles[ui.sel];
    int e;

    ui.msg = 0;
    satr_fake_track();
    e = satr_arm(ui.sel, t->dl_hz, t->ul_hz, satr_now(), 1);
    if (e)
        ui.msg = satr_err(e);
}

/* ---- Doppler self-test ----------------------------------------------------------------------------- */

#define ST_SPAN_HZ 6000
#define ST_STEP_HZ 500
#define ST_DWELL_MS 350

/* Sweeps the RX word around the satellite's downlink in 500 Hz steps with stock's retune, and prints
 * one UART line per step with the RSSI. One key press, one capture: a signal generator (or a second radio)
 * keying the downlink frequency makes the RSSI trace a peak at k = 0, which proves the retune path, its
 * 10 Hz resolution and its sign. docs/satellite-mode.md "Hardware test". */
static void selftest(void)
{
    const struct krk_sat_tle *t = &krk_sat_tles[ui.sel];
    char l[KRK_LINE_BUF], n[12];
    int k, e;
    u32 sum;
    int j;

    ui.msg = 0;
    if (R.state != ST_IDLE) { ui.msg = "stop tracking first"; return; }
    e = satr_check(0);
    if (e) { ui.msg = satr_err(e); return; }
    satr_capture(t->dl_hz);
    l[0] = 0;
    cat_s(l, "\r\nSATTEST start ");
    cat_s(l, t->name);
    cat_s(l, " dl=");
    cat_n(l, (int)t->dl_hz, 0, ' ');
    cat_s(l, " step=500 dwell=350ms\r\n");
    krk_uart_puts(l);
    for (k = -ST_SPAN_HZ; k <= ST_SPAN_HZ; k += ST_STEP_HZ) {
        u32 rx10 = R.nom_rx + (u32)(k / 10);

        MENU_EXIT_COUNTER = 0;
        if (satr_apply_rx(rx10) < 0) {
            krk_uart_puts("SATTEST abort: keyed/loading\r\n");
            break;
        }
        os_time_dly_ms(ST_DWELL_MS);
        for (sum = 0, j = 0; j < 4; j++) {
            sum += satr_rssi();
            os_time_dly_ms(20);
        }
        l[0] = 0;
        cat_s(l, "SATTEST k=");
        cat_s(l, krk_itoa(k, n));
        cat_s(l, " rx=");
        cat_s(l, krk_itoa((int)rx10, n));
        cat_s(l, " rssi=");
        cat_s(l, krk_itoa((int)(sum / 4), n));
        cat_s(l, "\r\n");
        krk_uart_puts(l);
        l[0] = 0;
        cat_s(l, "k ");
        cat_s(l, krk_itoa(k, n));
        cat_s(l, " rssi ");
        cat_s(l, krk_itoa((int)(sum / 4), n));
        krk_line(7, l, KRK_ACCENT);
    }
    krk_uart_puts(satr_restore_now() ? "SATTEST end (channel words restored if still ours)\r\n"
                                      : "SATTEST end (restore deferred: keyed or loading, the 1 Hz tick finishes it)\r\n");
    ui.msg = done_msg;
}

/* ---- screen ---------------------------------------------------------------------------------------- */

static void open_info(int i)
{
    ui.sel = (u8)i;
    ui.view = V_INFO;
    ui.msg = 0;
    ui.last_s = 0;
    display_fill_screen(KRK_BG);
}

static void sat_enter(void)
{
    int lat, lon, i;

    satr_ensure();
    satr_sync(1);
    ui.tz = satr_tz_min();
    ui.have_obs = satr_fix(&lat, &lon) != 0;
    if (ui.have_obs)
        krk_sat_obs_min4(&obs, lat, lon, 0);
    for (i = 0; i < KRK_SAT_MAXN; i++)
        pc[i].st = 0;
    loaded_idx = 0;
    ui.msg = 0;
    ui.retry = 0;                   /* garbage in the future made retry_gps skip for hours */
    ui.top = ui.sel = 0;
    ui.loc = ui.loc == 1;           /* SRAM is not zeroed at power-up: garbage made '#' toggle 0x79/0x78, stuck on local (hardware 2026-10-31) */
    ui.view = V_LIST;
    krk_line(0, "Satellites", KRK_DIM);
    if (R.state == ST_ACTIVE || R.state == ST_RESTORE)
        open_info(R.sat < krk_sat_ntles ? R.sat : 0);
    else
        draw_list();
}

static void sat_exit(void)
{
    satr_cancel_build();                        /* a half-built track is dropped; an active one keeps running */
}

/* Until both a position and the GPS clock are known, look again once a second (RMC only; the RTC was tried at
 * entry): the fix usually arrives after the screen is opened. A new position invalidates the pass cache. */
static void retry_gps(void)
{
    int lat, lon, had = ui.have_obs, i;

    if ((had && R.tsrc == TS_RMC) || (sat_s32)(OS_TICKS - ui.retry) < 100)
        return;
    ui.retry = OS_TICKS;
    satr_sync(0);
    if (!had && satr_fix(&lat, &lon)) {
        ui.have_obs = 1;
        krk_sat_obs_min4(&obs, lat, lon, 0);
        for (i = 0; i < KRK_SAT_MAXN; i++)
            pc[i].st = 0;
    }
    if (ui.view == V_LIST)
        draw_list();
}

static void sat_tick(u8 scan)
{
    u32 now;
    unsigned i;

    retry_gps();
    now = satr_now();

    if (ui.view == V_LIST) {
        if (scan == SCAN_UP && ui.sel > 0)
            ui.sel--;
        else if (scan == SCAN_DOWN && ui.sel + 1 < (int)krk_sat_ntles)
            ui.sel++;
        else if (scan == SCAN_HASH)
            ui.loc ^= 1;
        else if (scan == SCAN_OK) {
            open_info(ui.sel);
            return;
        } else {
            /* idle path: one pass search per tick */
            for (i = 0; i < krk_sat_ntles; i++) {
                if (pc[i].st == 0 && ui.have_obs && R.tsrc) {
                    compute_pass((int)i);
                    draw_list();
                    break;
                }
            }
            return;
        }
        if (ui.sel < ui.top)
            ui.top = ui.sel;
        if (ui.sel >= ui.top + 8)
            ui.top = (u8)(ui.sel - 7);
        draw_list();
        return;
    }

    /* info view */
    if (scan == SCAN_OK) {
        if (R.state == ST_ACTIVE || R.state == ST_RESTORE)
            satr_stop();
        else if (R.state == ST_IDLE)
            start_build();
        ui.last_s = 0;
    } else if (scan == SCAN_UP || scan == SCAN_DOWN) {
        R.trim_hz += scan == SCAN_UP ? 100 : -100;
        if (R.trim_hz > 5000) R.trim_hz = 5000;
        if (R.trim_hz < -5000) R.trim_hz = -5000;
        ui.last_s = 0;
    } else if (scan == SCAN_STAR) {
        selftest();
        ui.last_s = 0;
    } else if (scan == SCAN_HASH && R.state == ST_IDLE) {
        start_fake();
        ui.last_s = 0;
    } else if (scan == SCAN_P1) {
        satr_cancel_build();
        ui.view = V_LIST;
        display_fill_screen(KRK_BG);
        draw_list();
        return;
    }
    if (R.state == ST_BUILD)
        build_step();
    if (now != ui.last_s || R.state == ST_BUILD) {
        ui.last_s = now;
        draw_info();
    }
}

const struct krk_screen krk_sat_screen = {
    .label = "Satellites",
    .enter = sat_enter,
    .tick = sat_tick,
    .exit = sat_exit,
};
