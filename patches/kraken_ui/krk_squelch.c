/*
 * krk_squelch.c - F11: noise-based squelch modes (docs/noise-squelch.md).
 *
 * The FD6818 squelch is a hardware RSSI + noise comparator pair with no mode bit: REG_78H holds the
 * RSSI thresholds ((b << 8) | (b - 1), written by stock for every channel apply, squelch-menu change
 * and TX end) and REG_4FH the noise thresholds (out << 8 | in; stock writes 0x302F once at boot). SQ
 * opens when rssi > rssi_in AND noise < noise_in, closes when rssi < rssi_out OR noise > noise_out.
 * A LOWER noise threshold is a TIGHTER squelch.
 *
 * One splice: the `bsr fd6818_reg_write` at 0x0300de86 inside hw_register_0x78_write_from_byte_pair, the
 * single funnel of every stock REG_78H write, goes to krk_hook_sq_write(0x78, word). Off = the stock
 * write, unchanged. Normal / Tight / Custom on an analog channel with a non-zero squelch level write
 * the noise pair (and optionally an RSSI "gate open" word) instead. The noise numbers are UNMEASURED
 * (LOW confidence): the screen shows noise_sq / rssi_sq / snr / sq_out live, Custom lets the user
 * move the pair with Up/Down, and key 5 runs a read-only 5 s noise survey.
 *
 * FD6818 access uses the stock bus-semaphore wrappers by absolute address (krk_rf.h macros), so no
 * defsym into the main blob is needed. State is RAM only (power-up = Off), ~110 bytes in .bss.feat.
 *
 * Key 6 (2026-10-29) is the ONE-KEY hardware survey: a 25 s window sampling noise_sq + RSSI every 100 ms
 * while the second radio keys up once; krk_sqsurvey.h derives Tight / Normal pairs from the idle and carrier
 * noise extremes, shows them and logs "SQ ..." lines on UART0. It never applies anything.
 *
 * 2026-11-02 (docs/stock-option-level.md 6.1): two KRAKEN rows. "Squelch Mode" is a stock option level
 * (Off (stock) / Normal / Tight) over the same RAM mode and the same apply path (sq_set = what the '#'/'*' keys did:
 * set the mode, re-run the REG_78H/REG_4FH splice with the last stock word). "Squelch Survey" is the old live screen
 * with its mode-setting keys removed (#, *, Up/Down Custom tuning, OK/9 apply-suggestion, OK field select): it shows the
 * current mode read-only in its header; keys 1 (RSSI gate), 5 (5 s survey) and 6 (25 s survey) are unchanged.
 * Custom (M_CUSTOM) is no longer reachable from the UI; the hook still honours it.
 */
#include "krk_rf.h"
#include "krk_sqsurvey.h"
#include "krk_stock.h"

#define REG_NOISE_TH   0x4f     /* [14:8] noise_sq_th_out, [6:0] noise_sq_th_in */
#define REG_RSSI_TH    0x78     /* [15:8] rssi_sq_th_in,  [7:0]  rssi_sq_th_out */
#define REG_NOISE      0x65     /* [7:0] noise_sq (RO) */
#define REG_SNR        0x61     /* [15:8] snr_out (RO) */
#define REG_STATUS_SQ  0x0c     /* [1] sq_out */
#define STOCK_NOISE_TH 0x302f
#define RSSI_GATE_OPEN 0x201f   /* in 32, out 31: -144 dBm, always exceeded: only the noise pair decides */
#define CH_LEVEL       (*(volatile u8 *)0x0003e1e8)     /* loaded channel record +0xC, high nibble = squelch level 0-9 */

#define SQ_MAGIC 0x53514c31u    /* "SQL1" */
enum { M_OFF, M_NORMAL, M_TIGHT, M_CUSTOM, M_N };

/* noise in/out per mode; Custom's pair lives in sqst.cin/cout */
static const u8 preset[M_N][2] = { {47, 48}, {50, 52}, {40, 42}, {50, 52} };

static struct {
    u32 magic;
    u16 last_val;               /* the word stock last tried to write to REG_78H */
    u8 mode, dirty;
    u8 cin, cout, field, keep_rssi, have_last;
} sqst KRK_FEAT;

static void sq_init(void)
{
    if (sqst.magic != SQ_MAGIC) {
        sqst.magic = SQ_MAGIC;
        sqst.mode = M_OFF;
        sqst.dirty = 0;
        sqst.cin = preset[M_CUSTOM][0];
        sqst.cout = preset[M_CUSTOM][1];
        sqst.field = 2;           /* both */
        sqst.keep_rssi = 1;       /* hybrid: keep stock's RSSI word, only the noise pair changes */
        sqst.have_last = 0;
    }
}

/* Clamps keep the pair sane whatever the table or the user says: in >= 8 (in = 0 never opens),
 * out >= in, both 7-bit. Pure. */
u32 krk_sq_noise_word(u32 in, u32 out)
{
    if (in < 8)
        in = 8;
    if (in > 127)
        in = 127;
    if (out < in)
        out = in;
    if (out > 127)
        out = 127;
    return out << 8 | in;
}

static void pair_of(u32 m, u32 *in, u32 *out)
{
    *in = m == M_CUSTOM ? sqst.cin : preset[m][0];
    *out = m == M_CUSTOM ? sqst.cout : preset[m][1];
}

/* Spliced in place of `bsr fd6818_reg_write` at 0x0300de86: r0 = 0x78, r1 = the stock RSSI word. */
void krk_hook_sq_write(u32 reg, u32 val)
{
    u32 m, in, out;

    sq_init();
    sqst.last_val = (u16)val;
    sqst.have_last = 1;
    m = sqst.mode < M_N ? sqst.mode : M_OFF;
    if (m != M_OFF && (VFO_FLAGS8 & 0xf0) == 0 && (CH_LEVEL >> 4) != 0) {
        pair_of(m, &in, &out);
        fd6818_reg_write(REG_RSSI_TH, sqst.keep_rssi ? val : RSSI_GATE_OPEN);
        fd6818_reg_write(REG_NOISE_TH, krk_sq_noise_word(in, out));
        sqst.dirty = 1;
        return;
    }
    fd6818_reg_write(reg, val);                         /* stock, byte for byte */
    if (sqst.dirty) {                                     /* leaving a mode / digital channel / level 0 */
        fd6818_reg_write(REG_NOISE_TH, STOCK_NOISE_TH);
        sqst.dirty = 0;
    }
}

/* Re-runs the hook with the last stock word so a mode change acts at once (no retune needed). */
static void sq_reapply(void)
{
    if (sqst.have_last)
        krk_hook_sq_write(REG_RSSI_TH, sqst.last_val);
}


/* ---- "Squelch Mode": a stock option level (Off (stock) / Normal / Tight), docs/stock-option-level.md 6.1 ------------------ */
static int sq_get(void)
{
    sq_init();
    return sqst.mode <= M_TIGHT ? sqst.mode : M_OFF;
}

static void sq_set(int idx)
{
    if ((unsigned)idx > M_TIGHT)
        return;
    sq_init();
    sqst.mode = (u8)idx;
    sq_reapply();                       /* acts at once; no hardware wait */
}

static const char *const sq_labels[3] = { "Off (stock)", "Normal", "Tight" };
static const struct krk_opt sq_opt = { .title = "Squelch Mode", .labels = sq_labels, .n = 3, .get = sq_get, .set = sq_set };

const struct krk_screen krk_squelch_screen = {
    .label = "Squelch Mode",
    .opt = &sq_opt,
};

/* ---- "Squelch Survey": the live screen --------------------------------------------------------- */

static const char *const mode_name[M_N] = { "Off (stock)", "Normal", "Tight", "Custom" };
#define SURVEY_TICKS 50

static struct {
    u32 hash[KRK_LINES];
    int nsum, rsum, n;
    u8 nmin, nmax;
    short rmin, rmax;             /* rssi, half-dB */
    u8 surveying, have_survey;
    short sn[3], sr[3];           /* last survey result: min/avg/max noise and rssi (half-dB) */
    struct sqsv sv;               /* key 6 window (appended: the older offsets are unchanged) */
    u8 svon, svdone, svstat, pending, nrm[2], tgt[2];
    u8 sqo_prev;                  /* last squelch state logged (appended: tests read the older offsets) to the UART (2 = none yet) */
} squi KRK_FEAT;

#define put(n, s, fg) krk_line_cached(squi.hash, (n), (s), (fg))

/* dBm (integer) of an rssi half-dB reading: raw/2 - 160 */
static void put_dbm(char *buf, int raw)
{
    int d = raw / 2 - 160;

    if (d < 0) {
        krk_cat(buf, "-");
        d = -d;
    }
    krk_cati(buf, d);
}

static void survey_line(char *l, const char *tag, const short *v, int rssi)
{
    int k;

    l[0] = 0;
    krk_cat(l, tag);
    for (k = 0; k < 3; k++) {
        if (k)
            krk_cat(l, "/");
        if (rssi)
            put_dbm(l, v[k]);
        else
            krk_cati(l, v[k]);
    }
}

/* "SQ <tag> a b c ..." with decimal fields */
static void uart_vals(const char *tag, const int *v, int n)
{
    char l[KRK_LINE_BUF];
    int i;

    l[0] = 0;
    krk_cat(l, tag);
    for (i = 0; i < n; i++) {
        krk_cat(l, " ");
        krk_cati(l, v[i]);
    }
    krk_cat(l, "\r\n");
    krk_uart_puts(l);
}

/* "SQ OPEN|SHUT <tick> noise <n> rssi <r>" */
static void uart_sq_edge(u32 open, const int *v)
{
    char l[KRK_LINE_BUF];

    l[0] = 0;
    krk_cat(l, open ? "SQ OPEN " : "SQ SHUT ");
    krk_cati(l, v[0]);
    krk_cat(l, " noise ");
    krk_cati(l, v[1]);
    krk_cat(l, " rssi ");
    krk_cati(l, v[2]);
    krk_cat(l, "\r\n");
    krk_uart_puts(l);
}

static void sv_finish(void)
{
    int v[8], i;

    squi.svon = 0;
    squi.svdone = 1;
    squi.svstat = (u8)sv_derive(&squi.sv, squi.nrm, squi.tgt);
    squi.pending = squi.svstat == SV_OK;
    v[0] = squi.sv.base_rssi; v[1] = squi.sv.idle_n; v[2] = squi.sv.idle_min;
    v[3] = squi.sv.carr_n; v[4] = squi.sv.carr_max; v[5] = squi.svstat;
    uart_vals("SQ SVY END base/idleN/idleMin/carrN/carrMax/status", v, 6);
    for (i = 0; i < 2; i++) {
        v[i] = squi.nrm[i];
        v[2 + i] = squi.tgt[i];
    }
    uart_vals(squi.pending ? "SQ SVY SUGGEST nrm in/out tgt in/out" : "SQ SVY NO SUGGESTION", v, squi.pending ? 4 : 0);
}

static const char *const mode_short[M_N] = { "Off", "Normal", "Tight", "Custom" };

static void sq_enter(void)
{
    char l[KRK_LINE_BUF];
    int i;

    sq_init();
    squi.svon = squi.svdone = squi.pending = 0;
    for (i = 0; i < KRK_LINES; i++)
        squi.hash[i] = 0;
    squi.surveying = squi.have_survey = 0;
    squi.sqo_prev = 2;
    l[0] = 0;                           /* current mode, read-only (it is set in the Squelch Mode row) */
    krk_cat(l, "Sq survey: ");
    krk_cat(l, mode_short[sqst.mode < M_N ? sqst.mode : M_OFF]);
    krk_line(0, l, KRK_DIM);
    put(9, "1:gate 5/6:survey", KRK_DIM);
}

static void sq_survey_step(u32 noise, u32 rssi)
{
    if (squi.n == 0) {
        squi.nmin = squi.nmax = (u8)noise;
        squi.rmin = squi.rmax = (short)rssi;
        squi.nsum = squi.rsum = 0;
    }
    if (noise < squi.nmin) squi.nmin = (u8)noise;
    if (noise > squi.nmax) squi.nmax = (u8)noise;
    if ((short)rssi < squi.rmin) squi.rmin = (short)rssi;
    if ((short)rssi > squi.rmax) squi.rmax = (short)rssi;
    squi.nsum += (int)noise;
    squi.rsum += (int)rssi;
    if (++squi.n >= SURVEY_TICKS) {
        squi.sn[0] = squi.nmin; squi.sn[1] = (short)(squi.nsum / squi.n); squi.sn[2] = squi.nmax;
        squi.sr[0] = squi.rmin; squi.sr[1] = (short)(squi.rsum / squi.n); squi.sr[2] = squi.rmax;
        squi.surveying = 0;
        squi.have_survey = 1;
    }
}

static void sq_tick(u8 scan)
{
    char l[KRK_LINE_BUF];
    u32 noise, rssi, snr, sqo, in, out, m;
    int digital = krk_channel_dmr();   /* the selected side (dual-watch safe); sq_write_hook keeps the live struct */

    if (scan == SCAN_1) {
        sqst.keep_rssi ^= 1;
        sq_reapply();
    } else if (scan == SCAN_1 + 4 && !squi.svon) {      /* key 5 */
        squi.surveying = 1;
        squi.svdone = 0;
        squi.n = 0;
    } else if (scan == SCAN_1 + 5) {                    /* key 6: one-key 25 s survey (restarts if running) */
        sv_reset(&squi.sv);
        squi.svon = 1;
        squi.svdone = squi.pending = squi.surveying = 0;
        krk_uart_puts("\r\nSQ SVY START 100ms: t noise rssi_half_dB\r\n");
    }

    noise = fd6818_reg_read(REG_NOISE) & 0xff;
    rssi = fd6818_reg_read(KRK_RF_REG_RSSI) & 0x1ff;
    snr = fd6818_reg_read(REG_SNR) >> 8 & 0xff;
    sqo = fd6818_reg_read(REG_STATUS_SQ) >> 1 & 1;
    if (sqo != squi.sqo_prev) {                          /* every squelch open/shut edge (and the state at entry) to the UART */
        int v[3];

        squi.sqo_prev = (u8)sqo;
        v[0] = (int)OS_TICKS; v[1] = (int)noise; v[2] = (int)rssi;
        uart_sq_edge(sqo, v);
    }
    if (squi.surveying)
        sq_survey_step(noise, rssi);
    if (squi.svon) {
        if (squi.sv.n % 10 == 0) {                       /* one UART line per second */
            int v[4];
            v[0] = squi.sv.n / 10; v[1] = (int)noise; v[2] = (int)rssi; v[3] = squi.sv.cls;
            uart_vals("SQ", v, 4);
        }
        if (sv_step(&squi.sv, noise, rssi))
            sv_finish();
    }

    m = sqst.mode < M_N ? sqst.mode : M_OFF;
    l[0] = 0;
    krk_cat(l, mode_name[m]);
    if (m != M_OFF && !digital) {
        krk_cat(l, sqst.keep_rssi ? " gate:stock" : " gate:open");
    }
    put(1, l, m == M_OFF ? KRK_FG : KRK_ACCENT);

    pair_of(m, &in, &out);
    l[0] = 0;
    if (digital) {
        krk_cat(l, "digital ch: stock");
    } else if (m != M_OFF && !(CH_LEVEL >> 4)) {
        krk_cat(l, "sqst level 0: stock");
    } else {
        u32 w = krk_sq_noise_word(in, out);
        krk_cat(l, "in ");
        krk_cati(l, (int)(w & 0xff));
        krk_cat(l, " out ");
        krk_cati(l, (int)(w >> 8));
        if (m == M_CUSTOM)
            krk_cat(l, sqst.field == 0 ? " [i]" : sqst.field == 1 ? " [o]" : " [b]");
    }
    put(2, l, KRK_FG);

    l[0] = 0;
    krk_cat(l, "noise ");
    krk_cati(l, (int)noise);
    krk_cat(l, " snr ");
    krk_cati(l, (int)snr);
    put(3, l, KRK_FG);

    l[0] = 0;
    krk_cat(l, "rssi ");
    put_dbm(l, (int)rssi);
    krk_cat(l, sqo ? " SQ OPEN" : " SQ shut");
    put(4, l, sqo ? KRK_ACCENT : KRK_FG);

    l[0] = 0;
    krk_cat(l, "4F ");
    krk_hex(l + 3, fd6818_reg_read(REG_NOISE_TH) & 0xffff, 4);
    krk_cat(l, " 78 ");
    krk_hex(l + 11, fd6818_reg_read(REG_RSSI_TH) & 0xffff, 4);
    put(5, l, KRK_DIM);

    if (squi.svon || squi.svdone) {
        l[0] = 0;
        if (squi.svon) {
            krk_cat(l, "auto ");
            krk_cati(l, squi.sv.n / 10);
            krk_cat(l, "/25s ");
            krk_cat(l, squi.sv.n < SV_BASE ? "base" : squi.sv.cls ? "CARRIER" : "idle");
        } else {
            krk_cat(l, "idle>=");
            krk_cati(l, squi.sv.idle_min);
            krk_cat(l, " carr<=");
            krk_cati(l, squi.sv.carr_max);
        }
        put(6, l, KRK_WARN);
        l[0] = 0;
        if (squi.svon) {
            krk_cat(l, "idle>=");
            krk_cati(l, squi.sv.idle_min);
            krk_cat(l, " carr<=");
            krk_cati(l, squi.sv.carr_max);
        } else if (squi.svstat == SV_OK) {
            krk_cat(l, "nrm ");
            krk_cati(l, squi.nrm[0]);
            krk_cat(l, "/");
            krk_cati(l, squi.nrm[1]);
            krk_cat(l, " tgt ");
            krk_cati(l, squi.tgt[0]);
            krk_cat(l, "/");
            krk_cati(l, squi.tgt[1]);
        } else {
            krk_cat(l, squi.svstat == SV_NO_CARRIER ? "no carrier seen" : squi.svstat == SV_NO_IDLE ? "no idle seen" : "no gap: no advice");
        }
        put(7, l, squi.svon ? KRK_FG : KRK_ACCENT);
    } else if (squi.surveying) {
        l[0] = 0;
        krk_cat(l, "survey ");
        krk_cati(l, squi.n);
        krk_cat(l, "/50 ...");
        put(6, l, KRK_WARN);
        put(7, "", KRK_DIM);
    } else if (squi.have_survey) {
        survey_line(l, "nz ", squi.sn, 0);
        put(6, l, KRK_FG);
        survey_line(l, "dBm ", squi.sr, 1);
        put(7, l, KRK_FG);
    } else {
        put(6, "5:5s 6:25s survey", KRK_DIM);
        put(7, "", KRK_DIM);
    }
    put(8, squi.pending ? "pairs: see UART" : "mode: Squelch Mode", squi.pending ? KRK_WARN : KRK_DIM);
}

const struct krk_screen krk_sqsurvey_screen = {
    .label = "Squelch Survey",
    .enter = sq_enter,
    .tick = sq_tick,
};

/* Standby tags (krk_tags.c): 1 while Normal or Tight is set and the SELECTED channel is analog.
 * Not sqst.dirty: that follows the loaded channel, which dual watch flips every few hundred ms, so
 * a tag built on it would blink. (A channel with squelch level 0 keeps stock squelch regardless.) */
int krk_sq_active(void)
{
    return sqst.magic == SQ_MAGIC && sqst.mode != M_OFF && sqst.mode < M_N && krk_channel_analog();
}
