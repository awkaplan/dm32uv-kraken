/*
 * krk_afsk.c - see krk_afsk.h.
 *
 * Demodulator: two quadrature correlators (1200 and 2200 Hz local oscillators from a 64-entry
 * Q14 sine table, Q16 phase accumulators) over a sliding window of W = 8 samples (1.2 bits);
 * mark/space decision = sign(E_mark - E_space). Bit clock: a Q16 phase accumulator at 1200/8000
 * per sample, sampled on wrap, pulled a quarter of the way toward the half-bit point on every tone
 * transition. NRZI: 1 = no change. HDLC: flag 0x7E on the raw bit stream, bit unstuffing after
 * five ones, LSB-first bytes, abort on seven ones, FCS = CRC-16/X.25 (krk_ax25_fcs).
 * State lives in its own section (.bss.afsk) so the linker can place it in a separate SRAM claim.
 */
#include "krk_afsk.h"

#if defined(AFSK_DEBUG) || defined(AFSK_TRACE)
#include <stdio.h>
#endif
#ifndef AFSK_SPACE_GAIN
#define AFSK_SPACE_GAIN 2   /* default energy weight of the space correlator (twist compensation) */
#endif
#ifndef AFSK_SMOOTH
#define AFSK_SMOOTH 3       /* samples of the decision metric summed before the sign is taken (1..3) */
#endif
#ifndef AFSK_PLL_MINRUN
#define AFSK_PLL_MINRUN 1   /* a tone change counts for the bit clock only after this many samples */
#endif
#ifndef AFSK_PREEMPH_DEF
#define AFSK_PREEMPH_DEF 0  /* power-on state of the pre-emphasis / radio profile (the UI sets it explicitly) */
#endif
#ifndef AFSK_PE_NUM
#define AFSK_PE_NUM 28      /* pre-emphasis coefficient: y = v - v[n-1] * NUM/32 */
#endif
/*
 * Two parameter sets. The default one is the flat-channel demodulator the host tests exercise
 * (synthetic CPFSK, two-generator switching, wide twist and baud-rate ranges). The "radio" profile
 * is selected together with the pre-emphasis (krk_afsk_set_preemph) and is tuned on captures of a
 * real transmit -> receive chain (voice filters + de-emphasis in both radios), where the two tones
 * are delayed differently and a bit's tail reaches 1.5 bits:
 *   W       correlator window (samples): 8 flat, 6 radio (shorter = a 1-bit tone is not swamped)
 *   RELDLY2 the space-tone energy is delayed this many half-samples against the mark energy
 *           (the 2200 Hz path settles ~2.5 samples before the 1200 Hz path: aligning them opens the eye)
 *   EQ      decision per bit instant = m[b] - m[b-1]>>A + m[b+1]>>B, one bit late (3-tap ISI correction)
 *   DIV     bit-clock phase gain; LEAK relaxes the bit-rate integrator toward nominal on every
 *           transition so the noise flips before a burst cannot park it at a limit
 *
 * The radio values were re-tuned against tools/afsk_channel.py (a channel model fitted to capture2's
 * known-content bursts) with a one-off sweep: 30+60+60 random packets x 17 impairment conditions.
 * LEAK 3 -> 6 and pre-emphasis 29/32 -> 28/32 raised the pooled decode rate from 14.1 % to 17.8 %
 * (hold-out sets +5.2 / +3.6 points) and capture2 from 9/12 to 10/12 frames; every other parameter
 * (W, RELDLY2, EQ_A/B, DIV, IDIV, SMOOTH, MINRUN, space gain) is a sharp optimum: one step either way
 * loses 50-100 % of the capture2 frames and of the model's. That narrowness is the known weakness: the
 * profile is matched to a channel with 2200/1200 twist ~ -3 dB; at 0, -6 and -10 dB twist, +-1..2 %
 * clock error or SNR 12 dB it decodes (almost) nothing (same sweep).
 *
 * Cost of the radio profile per input sample, integer only: DC removal + AGC + pre-emphasis ~25 ops
 * (the AGC normalising loop runs <= 7 times), four 32x32 products and table lookups ~30, four sliding
 * sums ~16, two 64-bit energies (4 multiplies, ~2 x 30 cycles on a CK803S without a 64-bit multiplier),
 * space-energy delay + gain + 3-sample smoothing ~25 64-bit ops, the clock accumulator ~8; at each
 * transition (<= 1200/s) two divisions, at each bit (1200/s) the 3-tap decision and the HDLC state
 * machine ~60. About 300-400 instructions per sample = 2.4-3.2 MIPS at 8 kHz, far below the
 * 2000 instructions per sample the 84 MHz core can spare next to UART streaming. State: one 'S'
 * struct (960 bytes, section .bss.afsk), no allocation, no new state in the retune.
 */
/* Preset B (krk_afsk_set_preset(2)): the radio profile re-fitted on capture16 (2026-10-27), the
 * DM-32's own FM Send RAM transmitter received by the other DM-32 (frames bit-exact on the air,
 * twist -4..-7 dB). Window 8, space delay 2 samples, lighter first equaliser tap: decodes all five
 * capture16 frames at space gain 2 (f1 at 2..9); the model-fitted values above decode none of them,
 * and these decode none of the channel-model cases (tests/test_afsk.py), so both stay selectable. */
#define AFSK_B_W 8
#define AFSK_B_RELDLY2 4
#define AFSK_B_EQ_A 3
#ifndef AFSK_R_W
#define AFSK_R_W 6
#endif
#ifndef AFSK_R_RELDLY2
#define AFSK_R_RELDLY2 5
#endif
#ifndef AFSK_R_EQ_A
#define AFSK_R_EQ_A 4
#endif
#ifndef AFSK_R_EQ_B
#define AFSK_R_EQ_B 5
#endif
#ifndef AFSK_R_DIV
#define AFSK_R_DIV 14
#endif
#ifndef AFSK_R_IDIV
#define AFSK_R_IDIV 2048
#endif
#ifndef AFSK_R_LEAK
#define AFSK_R_LEAK 7
#endif
#ifndef AFSK_R_AGC_T
#define AFSK_R_AGC_T 3200   /* AGC target of the mean |v| (Q0, before the clip); 0 = old power-of-two AGC */
#endif
#ifndef AFSK_R_CLIP
#define AFSK_R_CLIP 8191    /* peak limiter after the AGC = 2.56 x the mean level: tames the pre-emphasis spikes */
#endif
#ifndef AFSK_F_W
#define AFSK_F_W 8
#endif
#ifndef AFSK_F_DIV
#define AFSK_F_DIV 4
#endif
#ifndef AFSK_F_IDIV
#define AFSK_F_IDIV 1024
#endif
static signed char afsk_space_gain; /* 0 = default (patch .data is not loaded, .bss not zeroed: see get) */
static signed char afsk_preemph; /* 1: first-difference pre-emphasis y = v - 0.9 v[n-1] before the correlators */
static signed char afsk_preset;  /* 2: preset B (capture16 fit); 0/1: the model-fitted radio profile */

void krk_afsk_set_preemph(int on)
{
    afsk_preemph = on ? 1 : 2;      /* 0 = never set: AFSK_PREEMPH_DEF */
}
int krk_afsk_get_preemph(void)
{
    return afsk_preemph == 1 || (afsk_preemph == 0 && AFSK_PREEMPH_DEF);
}

void krk_afsk_set_preset(int p)
{
    afsk_preset = p == 2 ? 2 : 1;
}
int krk_afsk_get_preset(void)
{
    return afsk_preset == 2;
}

void krk_afsk_set_space_gain(int g)
{
    afsk_space_gain = (signed char)(g < 1 ? 1 : g > 16 ? 16 : g);
}
int krk_afsk_get_space_gain(void)
{
    if (afsk_space_gain < 1 || afsk_space_gain > 16)
        afsk_space_gain = AFSK_SPACE_GAIN;
    return afsk_space_gain;
}
#include "krk_ax25.h"

#define WMAX     8
#define FS       8000
#define INC_MARK  ((1200 << 16) / FS)        /* 9830 */
#define INC_SPACE ((2200 << 16) / FS)        /* 18022 */
#define INC_BIT   ((1200 << 16) / FS)        /* bit clock: 1200 baud at 8 kHz */

const short krk_afsk_sintab[64] = {
    0, 1606, 3196, 4756, 6270, 7723, 9102, 10394, 11585, 12665, 13623, 14449, 15137, 15679, 16069,
    16305, 16384, 16305, 16069, 15679, 15137, 14449, 13623, 12665, 11585, 10394, 9102, 7723, 6270,
    4756, 3196, 1606, 0, -1606, -3196, -4756, -6270, -7723, -9102, -10394, -11585, -12665, -13623,
    -14449, -15137, -15679, -16069, -16305, -16384, -16305, -16069, -15679, -15137, -14449, -13623,
    -12665, -11585, -10394, -9102, -7723, -6270, -4756, -3196, -1606,
};

#define SIN(ph) krk_afsk_sintab[((ph) >> 10) & 63]
#define COS(ph) krk_afsk_sintab[(((ph) >> 10) + 16) & 63]

static struct {
    /* demod */
    unsigned ph_m, ph_s;
    int ring[WMAX][4];                 /* Im Qm Is Qs products */
    int ri;
    int sum[4];
    long long esd[8];               /* recent space energies, for the mark/space group-delay skew */
    int ei;
    int wl;                         /* active window length (profile) */
    long long m0, m1, sm;           /* decision metric at the last two bit instants and now */
    int tone_d;
    long long d1, d2;               /* last two decision metrics (3-sample smoothing) */
    int tone, prev_tone;            /* 0 mark, 1 space */
    int dc;                         /* DC estimate, Q4 */
    int lvl;                        /* running |x - dc| estimate (AGC), Q4 */
    int agc_g, agc_n;               /* radio profile: smooth AGC gain (Q10), refreshed every 32 samples */
    int inc;                        /* bit-clock increment (Q16 per sample), tracks the TX rate */
    int run;                        /* samples since the last tone change */
    int pe_prev;                    /* previous DC-free sample, for the pre-emphasis */
    /* bit clock */
    unsigned bph;
    int last_bit_tone;
    int lock;
    /* hdlc */
    unsigned raw;
    unsigned ones, nbits, cur;
    int in_frame, len;
    unsigned char frame[AFSK_MAX_FRAME];
    /* output */
    unsigned nframes, nflags, nbadfcs;
    unsigned nsamp, nmark, ntrans;  /* tone statistics since reset */
    char line[AFSK_LINES][AFSK_LINE_LEN + 1];
    int nlines;
} S
#ifndef __APPLE__                   /* host tests on macOS: Mach-O has no ELF section names */
    __attribute__((section(".bss.afsk")))
#endif
    ;

void krk_afsk_reset(void)
{
    unsigned char *p = (unsigned char *)&S;
    unsigned i;

    for (i = 0; i < sizeof S; i++)
        p[i] = 0;
    S.inc = INC_BIT;
}

static void put_call(char **o, const unsigned char *a, int max)
{
    int i, ssid = (a[6] >> 1) & 15;
    char *p = *o;

    for (i = 0; i < 6; i++) {
        char c = (char)(a[i] >> 1);
        if (c == ' ' || !c) break;
        if (p - *o < max) *p++ = c;
    }
    if (ssid) {
        *p++ = '-';
        if (ssid >= 10) *p++ = '1';
        *p++ = (char)('0' + ssid % 10);
    }
    *o = p;
}

/* F18 APRS Messages (krk_periodic.c -> blob 4 krk_aprs_msg.c, docs/aprs-messages.md) gets every decoded frame: the line, then
 * the frame without its FCS. The weak empty default keeps this decoder self-contained for the host tests and tools. */
void __attribute__((weak)) krk_aprs_msg_frame(const char *line, const unsigned char *f, int len)
{
    (void)line;
    (void)f;
    (void)len;
}

static void emit(void)
{
    const unsigned char *f = S.frame;
    int len = S.len - 2, n = 0, i;
    char *o, *end;

    /* shift the line ring: 0 = newest */
    for (i = AFSK_LINES - 1; i > 0; i--)
        for (n = 0; n <= AFSK_LINE_LEN; n++)
            S.line[i][n] = S.line[i - 1][n];
    o = S.line[0];
    end = o + AFSK_LINE_LEN;
    /* addresses: dest, src, path... until the extension bit */
    if (len >= 14) {
        put_call(&o, f + 7, 9);
        if (o < end) *o++ = '>';
        put_call(&o, f, 9);
        i = 14;
        while (!(f[i - 1] & 1) && i + 7 <= len && o < end - 10) {
            *o++ = ',';
            put_call(&o, f + i, 9);
            if (f[i + 6] & 0x80) *o++ = '*';      /* has-been-repeated */
            i += 7;
        }
        if (o < end) *o++ = ':';
        i += 2;                                   /* control + PID */
        for (; i < len && o < end; i++) {
            unsigned char c = f[i];
            *o++ = (c >= 0x20 && c < 0x7f) ? (char)c : '.';
        }
    }
    *o = 0;
    if (S.nlines < AFSK_LINES) S.nlines++;
    S.nframes++;
    krk_aprs_msg_frame(S.line[0], f, len);       /* F18 APRS Messages: the whole frame (the line is cut at 60) */
}

static void hdlc_bit(int bit)
{
    S.raw = ((S.raw >> 1) | (bit ? 0x80u : 0)) & 0xff;
    if (S.raw == 0x7e) {
        S.nflags++;
        if (S.in_frame && S.len >= 18) {
            unsigned short fcs = (unsigned short)(S.frame[S.len - 2] | (S.frame[S.len - 1] << 8));
            if (krk_ax25_fcs(S.frame, S.len - 2) == fcs)
                emit();
            else {
                S.nbadfcs++;
#ifdef AFSK_DEBUG
                { int k; fprintf(stderr, "BADFCS len %d:", S.len); for (k = 0; k < S.len; k++) fprintf(stderr, " %02x", S.frame[k]); fprintf(stderr, "\n"); }
#endif
            }
        }
        S.in_frame = 1;
        S.len = 0;
        S.nbits = 0;
        S.cur = 0;
        S.ones = 0;
        return;
    }
    if (!S.in_frame)
        return;
    if (bit) {
        if (++S.ones > 6) {           /* abort / idle */
            S.in_frame = 0;
            return;
        }
    } else if (S.ones == 5) {
        S.ones = 0;                   /* stuffed zero */
        return;
    } else {
        S.ones = 0;
    }
    S.cur |= (unsigned)bit << S.nbits;
    if (++S.nbits == 8) {
        if (S.len < AFSK_MAX_FRAME)
            S.frame[S.len++] = (unsigned char)S.cur;
        else
            S.in_frame = 0;
        S.nbits = 0;
        S.cur = 0;
    }
}

int krk_afsk_feed(const short *x, int n)
{
    unsigned before = S.nframes;
    int k, j0;
    const int rp = krk_afsk_get_preemph();             /* radio profile */
    const int pb = rp && krk_afsk_get_preset();         /* ... preset B (capture16 fit) */
    const int wl = rp ? (pb ? AFSK_B_W : AFSK_R_W) : AFSK_F_W, rel2 = rp ? (pb ? AFSK_B_RELDLY2 : AFSK_R_RELDLY2) : 0;
    const int eqa = rp ? (pb ? AFSK_B_EQ_A : AFSK_R_EQ_A) : 0, eqb = rp ? AFSK_R_EQ_B : 0;
    const int pdiv = rp ? AFSK_R_DIV : AFSK_F_DIV, pidiv = rp ? AFSK_R_IDIV : AFSK_F_IDIV, leak = rp ? AFSK_R_LEAK : 0;

    if (S.wl != wl) {                                  /* profile changed: restart the window */
        for (k = 0; k < WMAX; k++)
            for (j0 = 0; j0 < 4; j0++) S.ring[k][j0] = 0;
        for (j0 = 0; j0 < 4; j0++) S.sum[j0] = 0;
        S.ri = 0;
        S.wl = wl;
    }
    for (k = 0; k < n; k++) {
        int v, p[4], j;
        long long em, es;

        /* DC removal (slow IIR), then AGC: scale |x| to ~+-2048 whatever the input level, so a
         * packet at RMS 30 (a quiet acoustic coupling, capture1.wav) decodes as well as one at
         * RMS 750. Products stay < 2^28 and sums of W < 2^31. */
        S.dc += (((int)x[k] << 4) - S.dc) >> 8;
        v = (int)x[k] - (S.dc >> 4);
        if (rp) {                                         /* undo the receiver's de-emphasis, roughly */
            int y = v - ((S.pe_prev * AFSK_PE_NUM) >> 5);
            S.pe_prev = v;
            v = y;
        }
        {
            int a = v < 0 ? -v : v, sh = 0, lv;
            S.lvl += ((a << 4) - S.lvl) >> 6;            /* ~8 ms time constant */
            lv = S.lvl >> 4;
            if (lv < 1) lv = 1;
            if (rp && AFSK_R_AGC_T) {
                /* continuous gain instead of power-of-two steps: a burst whose level sits on a step
                 * boundary no longer flips its scale by 2x in the middle of a packet; the limiter
                 * then clips the pre-emphasis spikes at tone changes */
                if ((S.agc_n++ & 31) == 0) {
                    S.agc_g = (AFSK_R_AGC_T << 10) / lv;
                    if (S.agc_g > (128 << 10)) S.agc_g = 128 << 10;
                }
                v = (int)(((long long)v * S.agc_g) >> 10);
            } else {
                while ((lv << sh) < 1024 && sh < 7)
                    sh++;
                v = v << sh;
            }
            {
                const int clip = rp ? AFSK_R_CLIP : 8191;
                if (v > clip) v = clip;
                if (v < -clip) v = -clip;
            }
            v >>= 1;                                      /* 13 bits into the correlators */
        }

        p[0] = v * COS(S.ph_m);
        p[1] = v * SIN(S.ph_m);
        p[2] = v * COS(S.ph_s);
        p[3] = v * SIN(S.ph_s);
        S.ph_m += INC_MARK;
        S.ph_s += INC_SPACE;
        for (j = 0; j < 4; j++) {
            S.sum[j] += p[j] - S.ring[S.ri][j];
            S.ring[S.ri][j] = p[j];
        }
        S.ri = S.ri + 1 >= wl ? 0 : S.ri + 1;
        em = (long long)S.sum[0] * S.sum[0] + (long long)S.sum[1] * S.sum[1];
        es = (long long)S.sum[2] * S.sum[2] + (long long)S.sum[3] * S.sum[3];
        S.esd[S.ei] = es;
        if (rel2 & 1)
            es = (S.esd[(S.ei - (rel2 >> 1)) & 7] + S.esd[(S.ei - (rel2 >> 1) - 1) & 7]) / 2;
        else
            es = S.esd[(S.ei - (rel2 >> 1)) & 7];
        S.ei = (S.ei + 1) & 7;
        {
            /* space weight; the pre-emphasis already lifts 2200 Hz by ~4 dB, so with it on the same
             * setting counts half as much (default 2: x1 with pre-emphasis, x2 without, as before) */
            long long d = (((es * krk_afsk_get_space_gain()) >> rp) - em) / 4;
            /* /4: headroom for the 3-sample sum */
#if AFSK_SMOOTH == 3
            long long sm = d + S.d1 + S.d2;
            S.d2 = S.d1;
            S.d1 = d;
#elif AFSK_SMOOTH == 2
            long long sm = d + S.d1;
            S.d1 = d;
#else
            long long sm = d;
#endif
            S.sm = sm;
            S.tone = sm > 0;
        }
        S.nsamp++;
        if (!S.tone) S.nmark++;
#ifdef AFSK_TRACE
        fprintf(stderr, "T %u %d %lld %lld %d\n", S.nsamp, v, em >> 16, es >> 16, S.tone);
#endif

        /* bit clock: a transition steers the loop only if the tone had lasted at least
         * AFSK_PLL_MINRUN samples, so 1-2 sample flips at a phase jump (two-generator TX,
         * capture8) do not jerk it */
        if (S.tone != S.prev_tone && S.run >= AFSK_PLL_MINRUN) {
            S.ntrans++;
            int err = 0x8000 - (int)S.bph;
            S.bph = (unsigned)((int)S.bph + err / pdiv);
            if (pidiv) {
            /* second-order term: a transmitter whose bit rate is off by a few percent (the DM-32's
             * own beacon varied 1143-1208 baud between sends before its clock was fixed) shows up
             * as a consistent phase error sign; integrate it into the bit increment, bounded +-8% */
            S.inc += err / pidiv + (leak ? (INC_BIT - S.inc) >> leak : 0);   /* the leak undoes what noise flips did */
            if (S.inc > INC_BIT + INC_BIT / 12) S.inc = INC_BIT + INC_BIT / 12;
            if (S.inc < INC_BIT - INC_BIT / 12) S.inc = INC_BIT - INC_BIT / 12;
            }
            S.lock += (err < 0 ? -err : err) < 0x2000 ? 2 : -1;
            if (S.lock > 255) S.lock = 255;
            if (S.lock < 0) S.lock = 0;
        }
        S.run = S.tone != S.prev_tone ? 0 : S.run + 1;
        S.prev_tone = S.tone;
        S.bph += (unsigned)(S.inc ? S.inc : INC_BIT);
        if (S.bph >= 0x10000u) {
            S.bph -= 0x10000u;
#ifdef AFSK_TRACE
            fprintf(stderr, "B %u %d\n", S.nsamp, S.tone == S.last_bit_tone);
#endif
        if (eqa || eqb) {
            /* one-bit-delayed decision with a 3-tap correction from the neighbouring bit instants: the
             * radio's filters leave a deterministic few-percent tail of each bit in the next one */
            {
                long long e = S.m1;
                int t;
                if (eqa) e -= S.m0 >> eqa;
                if (eqb) e += S.sm >> eqb;
                t = e > 0;
                S.m0 = S.m1;
                S.m1 = S.sm;
                hdlc_bit(t == S.tone_d);
                S.tone_d = t;
            }
        } else {
            hdlc_bit(S.tone == S.last_bit_tone);   /* NRZI: same tone = 1 */
        }
            S.last_bit_tone = S.tone;
        }
    }
    return (int)(S.nframes - before);
}

unsigned krk_afsk_frames(void)
{
    return S.nframes;
}

int krk_afsk_line(int i, char *out)
{
    int n;

    if (i < 0 || i >= S.nlines) {
        out[0] = 0;
        return S.nlines;
    }
    for (n = 0; n <= AFSK_LINE_LEN; n++)
        out[n] = S.line[i][n];
    return S.nlines;
}

int krk_afsk_quality(void)
{
    return S.lock;
}

void krk_afsk_stats(unsigned *flags, unsigned *bad_fcs)
{
    *flags = S.nflags;
    *bad_fcs = S.nbadfcs;
}

void krk_afsk_tone_stats(unsigned *samples, unsigned *mark, unsigned *transitions)
{
    *samples = S.nsamp;
    *mark = S.nmark;
    *transitions = S.ntrans;
    S.nsamp = S.nmark = S.ntrans = 0;
}
