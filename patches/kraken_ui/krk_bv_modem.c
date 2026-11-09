/*
 * krk_bv_modem.c - see krk_bv.h. Single-carrier D8PSK / DQPSK modem, 2000 baud, 4 samples/symbol, carrier 1700 Hz, 8 kHz PCM.
 *
 * TX: symbol -> RRC (alpha 0.35, 33 taps, one polyphase phase per sample: 8 x 2 MACs) -> 80-entry Q14 carrier table
 *     (a carrier of k * 100 Hz is every k-th entry, exactly periodic) -> PCM, 256 samples per call, stateful.
 * RX: energy trigger (floor tracker, DC blocker, block AGC) -> complex mix -> RRC matched filter with 8 fractional-delay banks (the
 *     matched filter is also the timing interpolator) -> sync search: soft differential correlation of the last 64 symbols
 *     against two sync words (mode sense), 4 integer timing phases -> Gardner timing loop (feed-forward from the carrier
 *     loop) + T/2-spaced 24-tap complex LMS equalizer (known training, then decision directed) + 2nd order carrier PLL
 *     -> absolute decisions, differential bit decode, RESYNC words for cycle slips -> krk_bv_frame.c.
 * No float, no libc, no 64-bit arithmetic, no division in the per-sample path. All state is zero-initialised .bss.
 * Host tuning knobs are -D overridable (see the macro block); the values below are what the radio build uses.
 */
#include "krk_bv.h"

static const short bv_mf[8][33] = {
    {33, 218, 157, -156, -417, -242, 420, 1071, 936, -362, -2215, -3091, -1388, 3390, 9959, 15684, 17954, 15684, 9959, 3390, -1388, -3091, -2215, -362, 936, 1071, 420, -242, -417, -156, 157, 218, 33},
    {4, 205, 181, -112, -402, -295, 323, 1017, 1017, -146, -2000, -3096, -1775, 2651, 9124, 15113, 17917, 16198, 10782, 4158, -950, -3044, -2415, -588, 836, 1112, 517, -181, -425, -201, 128, 227, 63},
    {-26, 188, 201, -67, -380, -339, 227, 952, 1078, 58, -1773, -3061, -2111, 1947, 8281, 14491, 17806, 16653, 11585, 4952, -463, -2954, -2596, -821, 716, 1138, 613, -112, -425, -243, 95, 232, 92},
    {-54, 168, 216, -23, -353, -374, 135, 878, 1120, 247, -1539, -2991, -2396, 1280, 7437, 13822, 17622, 17044, 12363, 5767, 73, -2816, -2755, -1059, 578, 1149, 707, -36, -417, -283, 58, 232, 119},
    {-81, 145, 226, 19, -320, -400, 47, 795, 1143, 421, -1300, -2888, -2631, 654, 6597, 13111, 17368, 17368, 13111, 6597, 654, -2631, -2888, -1300, 421, 1143, 795, 47, -400, -320, 19, 226, 145},
    {-107, 119, 232, 58, -283, -417, -36, 707, 1149, 578, -1059, -2755, -2816, 73, 5767, 12363, 17044, 17622, 13822, 7437, 1280, -2396, -2991, -1539, 247, 1120, 878, 135, -374, -353, -23, 216, 168},
    {-130, 92, 232, 95, -243, -425, -112, 613, 1138, 716, -821, -2596, -2954, -463, 4952, 11585, 16653, 17806, 14491, 8281, 1947, -2111, -3061, -1773, 58, 1078, 952, 227, -339, -380, -67, 201, 188},
    {-151, 63, 227, 128, -201, -425, -181, 517, 1112, 836, -588, -2415, -3044, -950, 4158, 10782, 16198, 17917, 15113, 9124, 2651, -1775, -3096, -2000, -146, 1017, 1017, 323, -295, -402, -112, 181, 205},
};
static const short bv_cos80[80] = {
    16384, 16333, 16182, 15931, 15582, 15137, 14598, 13970, 13255, 12458, 11585, 10641,
    9630, 8561, 7438, 6270, 5063, 3825, 2563, 1285, 0, -1285, -2563, -3825,
    -5063, -6270, -7438, -8561, -9630, -10641, -11585, -12458, -13255, -13970, -14598, -15137,
    -15582, -15931, -16182, -16333, -16384, -16333, -16182, -15931, -15582, -15137, -14598, -13970,
    -13255, -12458, -11585, -10641, -9630, -8561, -7438, -6270, -5063, -3825, -2563, -1285,
    0, 1285, 2563, 3825, 5063, 6270, 7438, 8561, 9630, 10641, 11585, 12458,
    13255, 13970, 14598, 15137, 15582, 15931, 16182, 16333,
};
const short krk_bv_cos256[256] = {
    16384, 16379, 16364, 16340, 16305, 16261, 16207, 16143, 16069, 15986, 15893, 15791, 15679, 15557, 15426, 15286,
    15137, 14978, 14811, 14635, 14449, 14256, 14053, 13842, 13623, 13395, 13160, 12916, 12665, 12406, 12140, 11866,
    11585, 11297, 11003, 10702, 10394, 10080, 9760, 9434, 9102, 8765, 8423, 8076, 7723, 7366, 7005, 6639,
    6270, 5897, 5520, 5139, 4756, 4370, 3981, 3590, 3196, 2801, 2404, 2006, 1606, 1205, 804, 402,
    0, -402, -804, -1205, -1606, -2006, -2404, -2801, -3196, -3590, -3981, -4370, -4756, -5139, -5520, -5897,
    -6270, -6639, -7005, -7366, -7723, -8076, -8423, -8765, -9102, -9434, -9760, -10080, -10394, -10702, -11003, -11297,
    -11585, -11866, -12140, -12406, -12665, -12916, -13160, -13395, -13623, -13842, -14053, -14256, -14449, -14635, -14811, -14978,
    -15137, -15286, -15426, -15557, -15679, -15791, -15893, -15986, -16069, -16143, -16207, -16261, -16305, -16340, -16364, -16379,
    -16384, -16379, -16364, -16340, -16305, -16261, -16207, -16143, -16069, -15986, -15893, -15791, -15679, -15557, -15426, -15286,
    -15137, -14978, -14811, -14635, -14449, -14256, -14053, -13842, -13623, -13395, -13160, -12916, -12665, -12406, -12140, -11866,
    -11585, -11297, -11003, -10702, -10394, -10080, -9760, -9434, -9102, -8765, -8423, -8076, -7723, -7366, -7005, -6639,
    -6270, -5897, -5520, -5139, -4756, -4370, -3981, -3590, -3196, -2801, -2404, -2006, -1606, -1205, -804, -402,
    0, 402, 804, 1205, 1606, 2006, 2404, 2801, 3196, 3590, 3981, 4370, 4756, 5139, 5520, 5897,
    6270, 6639, 7005, 7366, 7723, 8076, 8423, 8765, 9102, 9434, 9760, 10080, 10394, 10702, 11003, 11297,
    11585, 11866, 12140, 12406, 12665, 12916, 13160, 13395, 13623, 13842, 14053, 14256, 14449, 14635, 14811, 14978,
    15137, 15286, 15426, 15557, 15679, 15791, 15893, 15986, 16069, 16143, 16207, 16261, 16305, 16340, 16364, 16379,
};


#define SPS        4
#define NCW        64
#define NSYNC      64
#ifndef NTRAIN
#define NTRAIN     192
#endif
#ifndef TRAIN8
#define TRAIN8     96                   /* trailing training symbols that use all 8 phases in fast mode */
#endif
#ifndef PLL_FAST
#define PLL_FAST   32                   /* symbols of carrier pull-in (fast PLL, equalizer frozen) before the equalizer adapts */
#endif
#define NRESYNC    8
#define NPAD       8
#define NFLUSH     12
#define EQ_N       24
#define EQ_D       6                    /* equalizer decision delay in symbols (centre tap = 2 * EQ_D) */
#define AMP        4096                 /* nominal symbol amplitude at the equalizer output */
#define AGC_RMS    1440                 /* CW passband RMS after the gain stage (-> |y| = AMP) */
/* Tuning (override with -D on the host; the radio build uses these values). */
#ifndef SYNC_TH
#define SYNC_TH    3800                 /* soft differential sync correlation threshold (perfect = 64 * ~120) */
#endif
#define TRAIN_EVM_MAX 24000             /* a training that ends worse than 0.37 of the symbol power (Q16) restarts the search */
#ifndef PLL_KI_D
#define PLL_KI_D   3                    /* carrier loop, data: integrator ep >> KI, proportional ep << KP (Q24 turns) */
#endif
#ifndef PLL_KP_D
#define PLL_KP_D   4
#endif
/* timing loop: wide while training (te >> KP, integrator >> KI), narrow afterwards */
#ifndef TED_KP_T
#define TED_KP_T   5
#endif
#ifndef TED_KI_T
#define TED_KI_T   12
#endif
#ifndef TED_KP_D
#define TED_KP_D   8
#endif
#ifndef TED_KI_D
#define TED_KI_D   18
#endif
#ifndef LMS_TR
#define LMS_TR     10                   /* equalizer LMS step shift: training / decision directed */
#endif
#ifndef LMS_DD
#define LMS_DD     13
#endif
#define RESYNC_W   0x6C1Bu              /* 8 absolute QPSK points (2 bits each, LSB pair first) */
#define TRAIN_SEED 0xACE1u

static const unsigned char sync_w[2][NSYNC / 4] = {      /* [mode], 4 dibits per byte, first symbol in the low pair */
    { 7, 208, 157, 156, 164, 86, 254, 116, 154, 42, 205, 67, 151, 24, 248, 73 },      /* robust */
    { 50, 85, 223, 2, 122, 10, 174, 186, 44, 20, 66, 7, 185, 167, 41, 136 } };        /* fast */
static const unsigned char gray3[8] = { 0, 1, 3, 2, 7, 6, 4, 5 };  /* bits -> phase step */
static const unsigned char ungray3[8] = { 0, 1, 3, 2, 6, 7, 5, 4 };/* phase step -> bits */
static const unsigned char gray2[4] = { 0, 1, 3, 2 };

static int sync_d(int mode, int i) { return (sync_w[mode][i >> 2] >> (2 * (i & 3))) & 3; }

static unsigned lfsr_bits(unsigned *s, int n)    /* 16 bit Fibonacci LFSR x^16+x^14+x^13+x^11+1, n bits per call */
{
    int k;
    for (k = 0; k < n; k++) {
        unsigned b = (*s ^ (*s >> 2) ^ (*s >> 3) ^ (*s >> 5)) & 1;
        *s = (*s >> 1) | (b << 15);
    }
    return *s >> (16 - n);
}

/* Training symbol k (absolute phase index): QPSK points, the last TRAIN8 symbols of a fast-mode burst use all eight points so the
 * equalizer and carrier loop see the constellation they will meet in the blocks. */
static int train_m(unsigned *s, int k, int mode)
{
    if (mode == BV_MODE_FAST && k >= NTRAIN - TRAIN8) return (int)lfsr_bits(s, 3);
    return 2 * (int)lfsr_bits(s, 2);
}

static int pt_re(int m) { return krk_bv_cos256[(m & 7) << 5]; }
static int pt_im(int m) { return krk_bv_cos256[(((m & 7) << 5) + 192) & 255]; }

/* ============================================================== TX */
enum { T_DONE, T_CW, T_SYNC, T_TRAIN, T_RESYNC, T_DATA, T_PAD, T_FLUSH };      /* T_DONE = 0: idle after reset */
static int cstride;                              /* carrier = 100 Hz * stride (0 = default 17 = 1700 Hz) */
static int cstep(void) { return cstride ? cstride : 17; }
static int tx_stage, tx_cnt, tx_mode, tx_m, tx_ph, tx_cidx, tx_head;
static int tx_rms, tx_peak, tx_gain;                /* 0 = defaults (4000 / 30000) */
static short tx_si[9], tx_sq[9];
static unsigned tx_lfsr;
static const unsigned char *tx_seg;
static int tx_len, tx_pos, tx_nsym, tx_symcnt, tx_bps, tx_accn;
static unsigned tx_acc;
static int rs_state;                             /* receiver state, defined below */
static void rx_reset(void);

static void tx_next_segment(void)
{
    tx_seg = krk_bv_msg_next(&tx_len);
    if (!tx_seg) { tx_stage = T_PAD; tx_cnt = 0; tx_lfsr = TRAIN_SEED ^ 0x55AAu; return; }
    tx_bps = (tx_len == BV_HDR_BYTES || tx_mode == BV_MODE_ROBUST) ? 2 : 3;
    tx_nsym = (tx_len * 8 + tx_bps - 1) / tx_bps;
    tx_stage = T_RESYNC; tx_cnt = 0;
}

static int tx_sym(void)                          /* sets tx_m; returns 1 for a zero (flush) symbol */
{
    for (;;) switch (tx_stage) {
    case T_CW:
        if (tx_cnt < NCW) { tx_cnt++; tx_m = 0; return 0; }
        tx_stage = T_SYNC; tx_cnt = 0; break;
    case T_SYNC:
        if (tx_cnt < NSYNC) { tx_m = (tx_m + 2 * sync_d(tx_mode, tx_cnt++)) & 7; return 0; }
        tx_stage = T_TRAIN; tx_cnt = 0; tx_lfsr = TRAIN_SEED; break;
    case T_TRAIN:
        if (tx_cnt < NTRAIN) { tx_cnt++; tx_m = train_m(&tx_lfsr, tx_cnt - 1, tx_mode); return 0; }
        tx_next_segment(); break;
    case T_RESYNC:
        if (tx_cnt < NRESYNC) { tx_m = 2 * (int)((RESYNC_W >> (2 * tx_cnt++)) & 3); return 0; }
        tx_stage = T_DATA; tx_symcnt = 0; tx_pos = 0; tx_accn = 0; tx_acc = 0; break;
    case T_DATA:
        if (tx_symcnt < tx_nsym) {
            int bits, step;
            while (tx_accn < tx_bps) {
                tx_acc = (tx_acc << 8) | (tx_pos < tx_len ? tx_seg[tx_pos] : 0); tx_pos++; tx_accn += 8;
            }
            bits = (int)((tx_acc >> (tx_accn - tx_bps)) & ((1u << tx_bps) - 1));
            tx_accn -= tx_bps; tx_acc &= (1u << tx_accn) - 1;
            step = tx_bps == 3 ? gray3[bits] : 2 * gray2[bits];
            tx_m = (tx_m + step) & 7; tx_symcnt++;
            return 0;
        }
        tx_next_segment(); break;
    case T_PAD:
        if (tx_cnt < NPAD) { tx_cnt++; tx_m = 2 * (int)lfsr_bits(&tx_lfsr, 2); return 0; }
        tx_stage = T_FLUSH; tx_cnt = 0; break;
    case T_FLUSH:
        if (tx_cnt < NFLUSH) { tx_cnt++; return 1; }
        tx_stage = T_DONE; break;
    default:
        return 1;
    }
}

void krk_bv_set_carrier(int hz)
{
    int k = (hz + 50) / 100;
    cstride = k < 15 ? 15 : (k > 21 ? 21 : k);
}

void krk_bv_tx_level(int rms, int peak)
{
    tx_rms = rms > 0 ? rms : 4000; tx_peak = peak > 0 ? peak : 30000;
    rms = tx_rms;
    tx_gain = (rms * 2896) >> 10;                /* RMS(passband, unit symbols) = 0.3536 -> LSB per unit = rms * 2.83 */
}

void krk_bv_tx_start(int mode, unsigned msg_id, unsigned src_id, const unsigned char *units, int nunits,
                     const unsigned char *gaps, int flags)
{
    int i;
    rx_reset();
    krk_bv_msg_begin(mode, msg_id, src_id, units, nunits, gaps, flags, 15);
    tx_mode = mode ? 1 : 0; tx_stage = T_CW; tx_cnt = 0; tx_m = 0; tx_ph = 0; tx_cidx = 0; tx_head = 0;
    for (i = 0; i < 9; i++) tx_si[i] = tx_sq[i] = 0;
    krk_bv_tx_level(tx_rms, tx_peak);       /* keeps a previous level, defaults on first use */
}

int krk_bv_tx_active(void) { return tx_stage != T_DONE; }
void krk_bv_tx_stop(void) { tx_stage = T_DONE; }

unsigned krk_bv_tx_samples(int mode, int nunits)
{
    int nb = krk_bv_nblocks(nunits), bps = mode ? 3 : 2;
    unsigned sym = NCW + NSYNC + NTRAIN + 2 * (NRESYNC + (BV_HDR_BYTES * 8) / 2) + nb * (NRESYNC + (BV_BLOCK_BYTES * 8 + bps - 1) / bps) + NPAD + NFLUSH;
    return ((sym * SPS) / 256 + 1) * 256;        /* krk_bv_tx_fill() reports 'done' in the block that starts the first idle symbol */
}

int krk_bv_tx_fill(short *out)
{
    int n;
    for (n = 0; n < 256; n++) {
        int j, I = 0, Q = 0, v, c, s;
        if (tx_ph == 0) {
            tx_head = tx_head ? tx_head - 1 : 8;
            if (tx_sym()) tx_si[tx_head] = tx_sq[tx_head] = 0;
            else { tx_si[tx_head] = (short)pt_re(tx_m); tx_sq[tx_head] = (short)pt_im(tx_m); }
        }
        for (j = 0; j < 9; j++) {
            int k = tx_ph + SPS * j, q = tx_head + j;
            if (k > 32) break;
            if (q > 8) q -= 9;
            I += bv_mf[0][k] * tx_si[q]; Q += bv_mf[0][k] * tx_sq[q];
        }
        I >>= 15; Q >>= 15;
        c = bv_cos80[tx_cidx]; s = bv_cos80[tx_cidx >= 20 ? tx_cidx - 20 : tx_cidx + 60];
        tx_cidx += cstep(); if (tx_cidx >= 80) tx_cidx -= 80;
        v = (((I * c - Q * s) >> 14) * tx_gain) >> 14;
        if (v > tx_peak) v = tx_peak; else if (v < -tx_peak) v = -tx_peak;
        out[n] = (short)v;
        if (++tx_ph == SPS) tx_ph = 0;
    }
    return tx_stage != T_DONE;
}

/* ============================================================== RX */
enum { S_IDLE, S_ACQ, S_RUN };
static short ri[64], rq[64];
static unsigned rn;                              /* samples pushed since ACQ start */
static int rcidx, rgain, r_dc;
static unsigned r_blkE, r_floor;
static unsigned r_gE;                            /* gained-signal block energy (AGC while searching) */
static int r_blkn, r_hf, r_needq, r_quiet, r_busy;
static int rx_ev;
static unsigned char *rx_buf;
static int rx_cap;
/* acquisition */
static short a_pr[4], a_pi[4], a_h[4][4][2];
static unsigned char a_cnt[4], a_zc[4];
static int a_wait, a_mm, a_p, a_c, a_mode;
/* run */
static int tpos, tint, pon_r, pon_i, mode_rx, ted_kp, ted_ki;
/* The sync-search history and the equalizer are never live together: one union. */
static union {
    struct { unsigned char z[4][NSYNC]; } a;                 /* octant of each differential product, per timing phase */
    struct { short xr[EQ_N], xi[EQ_N]; int wr[EQ_N], wi[EQ_N]; } e;   /* delay line (T/2) and taps (Q20) */
} un;
#define a_z un.a.z
#define xr  un.e.xr
#define xi  un.e.xi
#define wr  un.e.wr
#define wi  un.e.wi
static int phi, frq, arr, m_prev, pop;
static unsigned lfsr_r;
static int sg_stage, sg_cnt, sg_nsym, sg_bps, sg_len, sg_pos, sg_accn, rs_err_total, lowcnt, pw_avg;
static unsigned sg_acc, ev_sum, ev_cnt;
static unsigned char rs_hist[8];
enum { G_TRAIN, G_RESYNC, G_DATA };

static unsigned isqrt32(unsigned v)
{
    unsigned r = 0, b = 1u << 30;
    while (b > v) b >>= 2;
    while (b) { if (v >= r + b) { v -= r + b; r = (r >> 1) + b; } else r >>= 1; b >>= 2; }
    return r;
}

static void rx_reset(void)
{
    rs_state = S_IDLE; r_blkE = 0; r_blkn = 0; r_needq = 0; rgain = 256; rx_ev = 0;
}

void krk_bv_rx_begin(unsigned char *buf, int cap_bytes)
{
    rx_buf = buf; rx_cap = cap_bytes; r_hf = 0; r_floor = 0; rx_reset();
    krk_bv_rxmsg_begin(0, buf, cap_bytes);
}

int krk_bv_rx_state(void) { return rs_state; }
int krk_bv_rx_gap(unsigned now, unsigned prev) { return (int)(now - prev) > BV_RX_GAP_TICKS; }

static void mf(int c, int f, int *yr_, int *yi_)
{
    const short *h = bv_mf[f];
    int a = 0, b = 0, k, idx = (c - 16) & 63;
    for (k = 0; k < 33; k++) {
        a += h[k] * ri[idx]; b += h[k] * rq[idx];
        idx = (idx + 1) & 63;
    }
    *yr_ = a >> 14; *yi_ = b >> 14;
}

static int quant8(int r, int i)
{
    int ar = r < 0 ? -r : r, ai = i < 0 ? -i : i, q;
    if (ai * 12 < ar * 5) q = 0; else if (ai * 5 > ar * 12) q = 2; else q = 1;
    if (r >= 0) return i >= 0 ? q : (8 - q) & 7;
    return i >= 0 ? 4 - q : 4 + q;
}
static int quant4(int r, int i)
{
    int ar = r < 0 ? -r : r, ai = i < 0 ? -i : i;
    if (ai > ar) return i >= 0 ? 2 : 6;
    return r >= 0 ? 0 : 4;
}

static void rx_finish(int ev)
{
    rx_ev |= ev; rs_state = S_IDLE; r_blkn = 0; r_blkE = 0; r_needq = 1;     /* wait for the channel to go quiet */
}

/* One popped (equalized, delayed) symbol. */
static void pop_symbol(int yr, int yi)
{
    int c = krk_bv_cos256[(phi >> 16) & 255], s = krk_bv_cos256[(((phi >> 16) & 255) + 192) & 255];
    int dr = (yr * c + yi * s) >> 14, di = (yi * c - yr * s) >> 14;
    int m, mt, ar, ai, ep, er, ei, tr, ti_, i, sh, p2;
    int step;
    if (sg_stage == G_TRAIN) {
        mt = train_m(&lfsr_r, sg_cnt, mode_rx); m = mt;
    } else if (sg_stage == G_RESYNC) {
        m = quant8(dr, di);                       /* decision-directed; the known word only measures a cycle slip */
        rs_hist[(m - 2 * (int)((RESYNC_W >> (2 * sg_cnt)) & 3)) & 7]++;
        mt = m;
    } else {
        m = (sg_bps == 3) ? quant8(dr, di) : quant4(dr, di);
        mt = m;
    }
    ar = (pt_re(mt) * AMP) >> 14; ai = (pt_im(mt) * AMP) >> 14;
    ep = (di * ar - dr * ai) >> 12;
    if (pop < PLL_FAST) { p2 = 0; sh = 31; }                  /* carrier pull-in, equalizer frozen */
    else if (sg_stage == G_TRAIN) { p2 = 1; sh = LMS_TR; }
    else { p2 = 2; sh = LMS_DD; }
    if (ep > 2048) ep = 2048; else if (ep < -2048) ep = -2048;
    frq += p2 == 0 ? ep : (p2 == 1 ? ep >> 1 : ep >> PLL_KI_D);  /* phi/frq: angle in Q24 (1 turn = 2^24) */
    phi = (phi + frq + (ep << (p2 == 2 ? PLL_KP_D : 6 - p2))) & 0xFFFFFF;
    /* equalizer error in the pre-rotation domain */
    tr = (ar * c - ai * s) >> 14; ti_ = (ar * s + ai * c) >> 14;
    er = tr - yr; ei = ti_ - yi;
    if (er > AMP) er = AMP; else if (er < -AMP) er = -AMP;
    if (ei > AMP) ei = AMP; else if (ei < -AMP) ei = -AMP;
    if (sh < 31)
        for (i = 0; i < EQ_N; i++) {
            wr[i] += (er * xr[i] + ei * xi[i]) >> sh;
            wi[i] += (ei * xr[i] - er * xi[i]) >> sh;
        }
    /* statistics / lock supervision */
    {
        int e2r = (dr - ar) >> 4, e2i = (di - ai) >> 4;
        ev_sum += (unsigned)(e2r * e2r + e2i * e2i); ev_cnt++;
        pw_avg += ((((dr >> 4) * (dr >> 4) + (di >> 4) * (di >> 4))) - pw_avg) >> 4;
        if (pw_avg < ((AMP >> 4) * (AMP >> 4)) / 8) lowcnt++; else lowcnt = 0;
    }
    /* segment bookkeeping */
    step = (m - m_prev) & 7; m_prev = m;
    pop++;
    switch (sg_stage) {
    case G_TRAIN:
        if (sg_cnt == NTRAIN - 64) ev_sum = ev_cnt = 0;
        if (++sg_cnt >= NTRAIN) {
            if (ev_sum / (ev_cnt ? ev_cnt : 1) > TRAIN_EVM_MAX) {      /* false sync or hopeless channel: search again */
                int k;
                rs_state = S_ACQ; a_wait = 0; r_quiet = 0; r_busy = 0;
                for (k = 0; k < 4; k++) { a_pr[k] = a_pi[k] = 0; a_cnt[k] = a_zc[k] = 0; }
                return;
            }
            goto next_seg;
        }
        break;
    case G_RESYNC:
        if (++sg_cnt >= NRESYNC) {
            int mj = 0;
            for (i = 1; i < 8; i++) if (rs_hist[i] > rs_hist[mj]) mj = i;
            rs_err_total += NRESYNC - rs_hist[mj];
            if (rs_hist[mj] < 5) { krk_bv_rxmsg_stat(ev_cnt ? ev_sum / ev_cnt : 0, (unsigned)rs_err_total); rx_finish(BV_EV_ABORT); return; }
            if (mj) { phi = (phi + (mj << 21)) & 0xFFFFFF; m_prev = (m_prev - mj) & 7; }   /* undo the slip */
            for (i = 0; i < 8; i++) rs_hist[i] = 0;
            if (sg_len == BV_BLOCK_BYTES && ted_ki != TED_KI_D) { tint = 0; ted_kp = TED_KP_D; ted_ki = TED_KI_D; }   /* narrow the timing loop once the header is through; drift comes from the carrier loop */
            sg_stage = G_DATA; sg_cnt = 0; sg_pos = 0; sg_accn = 0; sg_acc = 0;
        }
        break;
    default: {
        int bits = sg_bps == 3 ? ungray3[step] : gray2[step >> 1];
        unsigned char *sb = krk_bv_segbuf();
        sg_acc = (sg_acc << sg_bps) | (unsigned)bits; sg_accn += sg_bps;
        while (sg_accn >= 8) {
            if (sg_pos < sg_len) sb[sg_pos++] = (unsigned char)(sg_acc >> (sg_accn - 8));
            sg_accn -= 8;
        }
        sg_acc &= (1u << sg_accn) - 1;
        if (++sg_cnt >= sg_nsym) {
            rx_ev |= krk_bv_rxmsg_segment(sb);
            krk_bv_rxmsg_stat(ev_cnt ? ev_sum / ev_cnt : 0, (unsigned)rs_err_total);
            goto next_seg;
        }
        break; }
    }
    if (lowcnt > 24) { rx_finish(BV_EV_ABORT); }
    return;
next_seg:
    sg_len = krk_bv_rxmsg_expect();
    if (!sg_len) { rx_finish(rx_ev & BV_EV_DONE ? 0 : BV_EV_DONE); return; }
    sg_stage = G_RESYNC; sg_cnt = 0;
    sg_bps = (sg_len == BV_HDR_BYTES || mode_rx == BV_MODE_ROBUST) ? 2 : 3;
    sg_nsym = (sg_len * 8 + sg_bps - 1) / sg_bps;
}

static void run_symbol(void)
{
    int ti = tpos >> 12, f = (tpos >> 9) & 7, onr, oni, mdr, mdi, te, i;
    mf(ti, f, &onr, &oni); mf(ti - 2, f, &mdr, &mdi);
    te = ((onr - pon_r) * mdr + (oni - pon_i) * mdi) >> 12;
    pon_r = onr; pon_i = oni;
    if (te > 1500) te = 1500; else if (te < -1500) te = -1500;
    tint += te;
    /* tpos is Q12 samples. Feed-forward: a sample clock error e moves the carrier by 1700 e (= the PLL's frequency) and the
     * symbol timing by 4 e samples per symbol: frq / (0.85 * 2^24) * 4 * 4096 = frq * 301 / 2^18 in Q12 samples at 1700 Hz, inversely proportional to the carrier. */
    tpos += 16384 - (te >> (ted_kp - 4)) - (tint >> (ted_ki - 4)) - (((frq * 301 >> 8) * 17 / cstep()) >> 10);

    for (i = EQ_N - 1; i >= 2; i--) { xr[i] = xr[i - 2]; xi[i] = xi[i - 2]; }
    xr[0] = (short)onr; xi[0] = (short)oni; xr[1] = (short)mdr; xi[1] = (short)mdi;
    if (++arr > EQ_D) {
        int sr = 0, si = 0;
        for (i = 0; i < EQ_N; i++) {
            int a = wr[i] >> 6, b = wi[i] >> 6;
            sr += a * xr[i] - b * xi[i]; si += a * xi[i] + b * xr[i];
        }
        pop_symbol(sr >> 14, si >> 14);
    }
}

static void begin_run(int p, int c, int mode)
{
    int i, m = 0, mm[4];
    int nr = 0, ni = 0, dd = 0, j;
    mode_rx = mode;
    for (i = 0; i < NSYNC; i++) { m = (m + 2 * sync_d(mode, i)) & 7; if (i >= NSYNC - 4) mm[i - (NSYNC - 4)] = m; }
    for (j = 0; j < 4; j++) {                     /* j = 0 oldest of the last four */
        int h = (a_cnt[p] - 4 + j) & 3, hr = a_h[p][h][0] >> 4, hi = a_h[p][h][1] >> 4;
        int pr = pt_re(mm[j]), pi = pt_im(mm[j]);
        nr += pr * hr + pi * hi; ni += pi * hr - pr * hi; dd += hr * hr + hi * hi;
    }
    for (i = 0; i < EQ_N; i++) { wr[i] = wi[i] = 0; xr[i] = xi[i] = 0; }
    if (dd < 1) dd = 1;
    wr[2 * EQ_D] = (nr << 4) / dd * (AMP / 4); wi[2 * EQ_D] = (ni << 4) / dd * (AMP / 4);
    phi = frq = 0; arr = 0; pop = 0; ted_kp = TED_KP_T; ted_ki = TED_KI_T; m_prev = m; lfsr_r = TRAIN_SEED; tint = 0;
    sg_stage = G_TRAIN; sg_cnt = 0; rs_err_total = 0; lowcnt = 0; pw_avg = (AMP >> 4) * (AMP >> 4);
    ev_sum = ev_cnt = 0;
    for (i = 0; i < 8; i++) rs_hist[i] = 0;
    tpos = (c + SPS) << 12; pon_r = pon_i = 0;
    krk_bv_rxmsg_begin(mode, rx_buf, rx_cap);
    rs_state = S_RUN; rx_ev |= BV_EV_SYNC;
}

static void acq_step(void)
{
    static const signed char ux[8] = { 127, 90, 0, -90, -127, -90, 0, 90 };   /* unit phasors, 45 degree steps (Q7) */
    int c = (int)rn - 17, p = c & 3, yr, yi, zr, zi, i, k, mag, best = 0, bm = 0;
    mf(c, 0, &yr, &yi);
    zr = (yr * a_pr[p] + yi * a_pi[p]) >> 9; zi = (yi * a_pr[p] - yr * a_pi[p]) >> 9;
    a_pr[p] = (short)yr; a_pi[p] = (short)yi;
    a_h[p][a_cnt[p] & 3][0] = (short)yr; a_h[p][a_cnt[p] & 3][1] = (short)yi; a_cnt[p]++;
    a_z[p][a_zc[p] & (NSYNC - 1)] = (unsigned char)quant8(zr, zi); a_zc[p]++;
    for (k = 0; k < 2; k++) {                    /* soft differential correlation against both sync words */
        int sr = 0, si = 0, m;
        for (i = 0; i < NSYNC; i++) {
            int b = (a_z[p][(a_zc[p] - NSYNC + i) & (NSYNC - 1)] - 2 * sync_d(k, i)) & 7;
            sr += ux[b]; si += ux[(b + 6) & 7];  /* cos, sin = cos(b - 2) */
        }
        sr = sr < 0 ? -sr : sr; si = si < 0 ? -si : si;
        m = sr > si ? sr + (si >> 1) : si + (sr >> 1);
        if (m > best) { best = m; bm = k; }
    }
    if (best >= SYNC_TH && a_zc[p] >= NSYNC) {   /* candidate: rank the timing phases by their energy over the last 4 symbols */
        mag = 0;
        for (i = 0; i < 4; i++) {
            int u = a_h[p][i][0] < 0 ? -a_h[p][i][0] : a_h[p][i][0], v = a_h[p][i][1] < 0 ? -a_h[p][i][1] : a_h[p][i][1];
            mag += u > v ? u + (v >> 1) : v + (u >> 1);
        }
    } else mag = -1;
    if (a_wait) {
        if (mag > a_mm) { a_mm = mag; a_p = p; a_c = c; a_mode = bm; }
        if (--a_wait == 0) begin_run(a_p, a_c, a_mode);
    } else if (mag >= 0) {
        a_wait = 3; a_mm = mag; a_p = p; a_c = c; a_mode = bm;
    }
}

/* Per-sample receiver. */
static void acq_start(unsigned E)                /* E: energy of the 32-sample block that crossed the trigger */
{
    unsigned rms = isqrt32(E >> 1), g;
    int k;
    if (rms < 2) rms = 2;
    g = (unsigned)AGC_RMS * 256u / rms;
    rgain = g < 16 ? 16 : (g > 16384 ? 16384 : (int)g);
    rs_state = S_ACQ; rn = 0; a_wait = 0; r_quiet = 0; r_busy = 0; r_gE = 0;
    for (k = 0; k < 4; k++) { a_pr[k] = a_pi[k] = 0; a_cnt[k] = a_zc[k] = 0; }
}

static void rx_block(unsigned E)                 /* once per 32 raw samples while idle or searching */
{
    if (rs_state == S_IDLE) {
        if (!r_hf) { r_floor = E; r_hf = 1; }
        else if (r_needq) { if (E < 3 * r_floor + 64) r_needq = 0; if (E < r_floor) r_floor = E; }
        else if (E > 8 * r_floor + 96) acq_start(E);
        else if (E < r_floor) r_floor = E;
        else r_floor += (E - r_floor) >> 6;
        return;
    }
    /* searching: leave when the channel is quiet again, or after a second of noise without a sync word */
    r_quiet = (E > 3 * r_floor + 64) ? 0 : r_quiet + 1;
    r_busy += 32;
    if (r_quiet >= 6) rs_state = S_IDLE;
    else if (r_busy > 8000) { rs_state = S_IDLE; r_needq = 1; }
}

static void rx_sample(int x)
{
    r_dc += ((x << 8) - r_dc) >> 7;               /* DC blocker (the radio audio path has an offset) */
    x -= r_dc >> 8;
    if (rs_state != S_RUN) {
        r_blkE += (unsigned)(x * x) >> 4;
        if (++r_blkn == 32) { unsigned E = r_blkE; r_blkn = 0; r_blkE = 0; rx_block(E); }
        if (rs_state == S_IDLE) return;
    }
    {
        int v = (x * rgain) >> 8, c, s, a, b;
        if (v > 32767) v = 32767; else if (v < -32767) v = -32767;
        c = bv_cos80[rcidx]; s = bv_cos80[rcidx >= 20 ? rcidx - 20 : rcidx + 60];
        rcidx += cstep(); if (rcidx >= 80) rcidx -= 80;
        a = (v * c) >> 14; b = -((v * s) >> 14);
        if (a > 12000) a = 12000; else if (a < -12000) a = -12000;
        if (b > 12000) b = 12000; else if (b < -12000) b = -12000;
        ri[rn & 63] = (short)a; rq[rn & 63] = (short)b; rn++;
        if (rs_state == S_ACQ) {                 /* keep the level in range while searching (a notched CW lead reads low) */
            r_gE += (unsigned)(v * v) >> 4;
            if ((rn & 31) == 0) {
                unsigned rms = isqrt32(r_gE >> 1);
                r_gE = 0;
                if (rms > (AGC_RMS * 7) / 4 || (rms < AGC_RMS / 2 && rms > 4)) {
                    unsigned g = (unsigned)rgain * AGC_RMS / rms;
                    rgain = g < 16 ? 16 : (g > 16384 ? 16384 : (int)g);
                }
            }
        }
    }
    if (rs_state == S_ACQ && rn >= 40) acq_step();
    while (rs_state == S_RUN && (tpos >> 12) + 16 < (int)rn) run_symbol();
}

int krk_bv_rx_feed(const short *x, int n)
{
    int i;
    rx_ev = 0;
    if (tx_stage != T_DONE) return 0;            /* half duplex: the receiver is deaf while transmitting */
    for (i = 0; i < n; i++) rx_sample(x[i]);
    return rx_ev;
}
