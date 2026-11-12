/*
 * krk_dmrlink.c - F3 phase 1: DMR link quality (docs/dmr-ber.md). NOT a bit error rate: the HR_C7000
 * has no counter for the AMBE+2 FEC corrections. What the modem does expose, per received burst and
 * never read by stock for voice:
 *   RX_TYPE_INFO    0x11000390  [28] qr_check_flag (EMB QR error), [27] cs_check_flag, [19] slot (1 = TS1),
 *                               [14:12] current_voice_burst (1..6 = A..F), [7:4] cc, [1:0] sync_type
 *   RECV_INFO_COUNT 0x11000164  [31:24] sync_error_num (bit errors in the 48-bit sync field),
 *                               [7:0] fsk_error (EVM statistic while fsk_error_en [8] = 0)
 * READ ONLY: this file never writes a modem register (0x110003a0 SYS_INTERP_CLEAR belongs to the stock
 * ISR; 0x164 bit 8 stays 0). The semantics of 0x164 (latched per burst or cumulative, valid on B..F, EVM
 * scale) are unproven until the phase-0 hardware capture (key 1 = raw log + UART markers).
 *
 * The screen runs with tick_ms = 1 (about 10 ms) so every 30 ms burst is polled several times; a
 * sample is taken when the RX_TYPE_INFO word CHANGES (consecutive voice bursts always differ in the
 * burst letter). The accumulator and formatter (lq_feed / lq_tick / lq_line) are pure and host
 * tested by tests/test_dmrlink.py. State: ~230 bytes in .bss.feat.
 */
#ifndef KRK_FEAT                                   /* the host test defines KRK_FEAT away and builds the pure part only */
#define SYSCTL(off) MODEM(off)                     /* (the screen below is under #ifdef SYSCTL) */
#endif
#include "krk.h"
#include "krk_stock.h"

#define REG_RECV_INFO_COUNT 0x164
#define REG_RX_TYPE_INFO    0x390

#define LQ_HALVE_AT 1024u       /* keep the figures a moving average: halve every counter past this many bursts */
#define LQ_STALE    100u        /* ticks (~1 s) without a voice burst: the call has ended */
#define LQ_LINES    7           /* summary lines 1..6 (0 is the header) */

struct lq {
    u32 last_word;
    u32 n, emb_err, cs_err, evm_sum;
    u32 sync_err, sync_n;
    u32 age;
    u8 have_word, last_evm, last_sync, cc, ts, burst;
};

void lq_reset(struct lq *q)
{
    u8 *p = (u8 *)q;
    unsigned i;

    for (i = 0; i < sizeof *q; i++)
        p[i] = 0;
}

/* One poll of the two registers. Returns 1 when it counted a voice burst. */
int lq_feed(struct lq *q, u32 word, u32 cnt)
{
    u32 burst = word >> 12 & 7, stype = word & 3;

    if (q->have_word && word == q->last_word)
        return 0;                               /* same burst polled again */
    q->have_word = 1;
    q->last_word = word;
    if (burst < 1 || burst > 6 || stype > 1)    /* not a voice burst (data / RC / nothing) */
        return 0;
    q->n++;
    q->emb_err += word >> 28 & 1;
    q->cs_err += word >> 27 & 1;
    q->last_evm = (u8)cnt;
    q->evm_sum += cnt & 0xff;
    q->cc = (u8)(word >> 4 & 15);
    q->ts = (u8)(word >> 19 & 1 ? 1 : 2);       /* hardware 2026-11-01: a TS1 sender reads bit 19 = 1 */
    q->burst = (u8)burst;
    if (burst == 1) {                           /* only burst A carries a sync pattern */
        q->last_sync = (u8)(cnt >> 24);
        q->sync_err += cnt >> 24;
        q->sync_n++;
    }
    q->age = 0;
    if (q->n >= LQ_HALVE_AT) {
        q->n >>= 1;
        q->emb_err >>= 1;
        q->cs_err >>= 1;
        q->evm_sum >>= 1;
        q->sync_err >>= 1;
        q->sync_n >>= 1;
    }
    return 1;
}

void lq_tick(struct lq *q)
{
    if (q->age < 0x7fffffffu)
        q->age++;
}

static void cat_pct(char *l, u32 part, u32 n, u32 count)
{
    char num[12];

    krk_cat(l, krk_itoa((int)(part * 100 / n), num));
    krk_cat(l, "% (");
    krk_cat(l, krk_itoa((int)count, num));
    krk_cat(l, ")");
}

/* One-key capture (the screen key was removed 2026-11-02, F3 passed; the pure step function stays host-tested): 1 = armed, waiting for the first voice burst; 2 = recording; 3 = done.
 * Ends by itself: once a call was seen, when it has gone quiet for LQ_STALE ticks (end of that call) or
 * LQ_AUTO_MAX ticks after arming; with no call at all, after LQ_AUTO_WAIT ticks. Pure; elapsed in ticks. */
#define LQ_AUTO_WAIT 6000u      /* 60 s to start the call after pressing */
#define LQ_AUTO_MAX  12000u     /* 120 s hard stop */
int lq_auto_next(const struct lq *q, int st, u32 elapsed)
{
    if (st == 1)
        return q->n ? 2 : elapsed >= LQ_AUTO_WAIT ? 3 : 1;
    if (st == 2)
        return q->age > LQ_STALE || elapsed >= LQ_AUTO_MAX ? 3 : 2;
    return st;
}

/* Formats summary line k (1..6) into l (KRK_LINE_BUF bytes): one line at a time keeps the stack small. */
void lq_line(const struct lq *q, int analog, int k, char *l)
{
    char num[12];

    l[0] = 0;
    if (analog) {
        if (k == 1)
            krk_cat(l, "n/a (analog)");
        return;
    }
    if (!q->n) {
        if (k == 1)
            krk_cat(l, "no DMR burst");
        return;
    }
    if (k == 1) {
        krk_cat(l, q->age > LQ_STALE ? "idle, bursts " : "voice bursts ");
        krk_cat(l, krk_itoa((int)q->n, num));
    } else if (k == 2) {
        krk_cat(l, "EMB err ");
        cat_pct(l, q->emb_err, q->n, q->emb_err);
    } else if (k == 3) {
        krk_cat(l, "chk err ");
        cat_pct(l, q->cs_err, q->n, q->cs_err);
    } else if (k == 4) {
        krk_cat(l, "EVM ");
        krk_cat(l, krk_itoa((int)(q->evm_sum / q->n), num));
        krk_cat(l, " last ");
        krk_cat(l, krk_itoa(q->last_evm, num));
    } else if (k == 5) {
        if (q->sync_n) {
            u32 t = q->sync_err * 10 / q->sync_n;
            krk_cat(l, "sync ");
            krk_cat(l, krk_itoa(q->last_sync, num));
            krk_cat(l, "/48 avg ");
            krk_cat(l, krk_itoa((int)(t / 10), num));
            krk_cat(l, ".");
            krk_cat(l, krk_itoa((int)(t % 10), num));
        } else {
            krk_cat(l, "sync: no burst A");
        }
    } else if (k == 6) {
        krk_cat(l, "CC ");
        krk_cat(l, krk_itoa(q->cc, num));
        krk_cat(l, " TS");
        krk_cat(l, krk_itoa(q->ts, num));
        krk_cat(l, " burst ");
        num[0] = (char)('A' + q->burst - 1);
        num[1] = 0;
        krk_cat(l, num);
    }
}

/* ---- screen (target only) ------------------------------------------------------------------ */
#ifdef SYSCTL

extern int krk_channel_analog(void);

#define RAW_N 12
static struct {
    struct lq q;
    u32 hash[KRK_LINES];
    u32 ring[RAW_N][2];         /* raw log: (RX_TYPE_INFO, RECV_INFO_COUNT) on every change, newest at head-1 */
    u32 prev[2];
    u32 ticks;
    u8 head, count, raw, div, have_prev;
} lqst KRK_SCREEN_BSS;   /* screen-only: lq_enter resets q, hash, head, count, raw, have_prev, div, ticks; ring[] is read only below count */

static void log_pair(u32 w, u32 c)
{
    if (lqst.have_prev && lqst.prev[0] == w && lqst.prev[1] == c)
        return;
    lqst.have_prev = 1;
    lqst.prev[0] = w;
    lqst.prev[1] = c;
    lqst.ring[lqst.head][0] = w;
    lqst.ring[lqst.head][1] = c;
    lqst.head = (u8)((lqst.head + 1) % RAW_N);
    if (lqst.count < RAW_N)
        lqst.count++;
    if (lqst.raw) {                                                         /* UART marker: LQ <tick> <0x390> <0x164> */
        char l[KRK_LINE_BUF], num[12];
        l[0] = 0;
        krk_cat(l, "LQ ");
        krk_cati(l, (int)lqst.ticks);
        krk_uart_puts(l);
        krk_uart_putc(' ');
        krk_hex(num, w, 8);
        krk_uart_puts(num);
        krk_uart_putc(' ');
        krk_hex(num, c, 8);
        krk_uart_puts(num);
        krk_uart_puts("\r\n");
    }
}

static void draw(void)
{
    int k;

    if (lqst.raw) {
        krk_line_cached(lqst.hash, 0, "raw 390 164 (hex)", KRK_ACCENT);
        for (k = 0; k < 8; k++) {
            char l[KRK_LINE_BUF];
            l[0] = 0;
            if (k < lqst.count) {
                const u32 *e = lqst.ring[(lqst.head + RAW_N - 1 - k) % RAW_N];
                krk_hex(l, e[0], 8);
                l[8] = ' ';
                krk_hex(l + 9, e[1], 8);
            }
            krk_line_cached(lqst.hash, 1 + k, l, KRK_FG);
        }
        krk_line_cached(lqst.hash, 9, "1:back (UART log)", KRK_DIM);
        return;
    }
    krk_line_cached(lqst.hash, 0, "DMR link quality", KRK_DIM);
    for (k = 1; k < LQ_LINES; k++) {
        char l[KRK_LINE_BUF];
        lq_line(&lqst.q, krk_channel_analog(), k, l);
        krk_line_cached(lqst.hash, k, l, k == 1 ? KRK_ACCENT : KRK_FG);
    }
    krk_line_cached(lqst.hash, 7, "Link qual, not BER", KRK_DIM);
    krk_line_cached(lqst.hash, 8, "1:raw log 0:reset", KRK_DIM);
    krk_line_cached(lqst.hash, 9, "", KRK_DIM);
}

static void lq_enter(void)
{
    int i;

    lq_reset(&lqst.q);
    for (i = 0; i < KRK_LINES; i++)
        lqst.hash[i] = 0;
    lqst.head = lqst.count = lqst.raw = lqst.have_prev = lqst.div = 0;
    lqst.ticks = 0;
    draw();
}

static void lq_exit(void)
{
    if (lqst.raw)
        krk_uart_puts("LQ STOP\r\n");
    lqst.raw = 0;
}

static void lq_screen_tick(u8 scan)
{
    lqst.ticks++;
    if (scan == SCAN_1) {
        lqst.raw ^= 1;
        lqst.hash[0] = 0;
        if (lqst.raw)
            krk_uart_puts("\r\nLQ START tick~10ms: tick 0x390 0x164\r\n");
        else
            krk_uart_puts("LQ STOP\r\n");
        for (int i = 0; i < KRK_LINES; i++)
            lqst.hash[i] = 0;
    } else if (scan == SCAN_0) {
        lq_reset(&lqst.q);
    }
    lq_tick(&lqst.q);
    if (!krk_channel_analog()) {
        u32 w = SYSCTL(REG_RX_TYPE_INFO), c = SYSCTL(REG_RECV_INFO_COUNT);   /* read only, never written */
        lq_feed(&lqst.q, w, c);
        log_pair(w, c);
    }
    if (++lqst.div >= 10 || scan != SCAN_NONE) {       /* redraw about every 100 ms */
        lqst.div = 0;
        draw();
    }
}

const struct krk_screen krk_dmrlink_screen = {
    .label = "DMR Link",
    .enter = lq_enter,
    .tick = lq_screen_tick,
    .exit = lq_exit,
    .tick_ms = 1,
};
#endif
