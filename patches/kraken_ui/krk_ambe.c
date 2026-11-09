/*
 * krk_ambe.c - see krk_ambe.h. Tables: mbelib / DSD / MMDVM (public), cross-checked in tests/test_ambe.py.
 *
 * PN (mbelib mbe_demodulateAmbe3600x2450Data): pr[0] = 16 * (C0 data, 12 bit); pr[i] = (173 * pr[i-1] + 13849) mod 65536;
 * C1 bit j (j = 22 .. 0, 22 = first transmitted) is XORed with pr[23 - j] >> 15.
 * Interleave (DSD dmr_const.h): dibit i (36 per frame, first bit = MSB) puts its high bit at ambe_fr[rW[i]][rX[i]] and its
 * low bit at ambe_fr[rY[i]][rZ[i]], where ambe_fr[c][j] is bit j of codeword c (C0..C3 of 24/23/11/14 bits).
 */
#include "krk_ambe.h"

#ifndef __APPLE__                   /* host tests on macOS: Mach-O has no ELF section names */
#define AMBE_BSS __attribute__((section(".bss.ambe")))
#else
#define AMBE_BSS
#endif

static unsigned char g_par AMBE_BSS;            /* parity mode */
static unsigned short g_score_pm AMBE_BSS;

/* syndromes of the single-bit errors x^i mod g, i = 0..22 */
static const unsigned short syn1[23] = {
    0x001, 0x002, 0x004, 0x008, 0x010, 0x020, 0x040, 0x080, 0x100, 0x200, 0x400, 0x475,
    0x49F, 0x54B, 0x6E3, 0x1B3, 0x366, 0x6CC, 0x1ED, 0x3DA, 0x7B4, 0x31D, 0x63A
};

/* air bit position (0..71) -> codeword (bits 7-5... see below): (codeword << 5) | bit index. W/X for even positions, Y/Z for odd. */
#define PK(w, x) (unsigned char)(((w) << 5) | (x))
static const unsigned char air_tbl[72] = {
    PK(0,23), PK(0, 5), PK(1,10), PK(2, 3), PK(0,22), PK(0, 4), PK(1, 9), PK(2, 2), PK(0,21), PK(0, 3), PK(1, 8), PK(2, 1),
    PK(0,20), PK(0, 2), PK(1, 7), PK(2, 0), PK(0,19), PK(0, 1), PK(1, 6), PK(3,13), PK(0,18), PK(0, 0), PK(1, 5), PK(3,12),
    PK(0,17), PK(1,22), PK(1, 4), PK(3,11), PK(0,16), PK(1,21), PK(1, 3), PK(3,10), PK(0,15), PK(1,20), PK(1, 2), PK(3, 9),
    PK(0,14), PK(1,19), PK(1, 1), PK(3, 8), PK(0,13), PK(1,18), PK(1, 0), PK(3, 7), PK(0,12), PK(1,17), PK(2,10), PK(3, 6),
    PK(0,11), PK(1,16), PK(2, 9), PK(3, 5), PK(0,10), PK(1,15), PK(2, 8), PK(3, 4), PK(0, 9), PK(1,14), PK(2, 7), PK(3, 3),
    PK(0, 8), PK(1,13), PK(2, 6), PK(3, 2), PK(0, 7), PK(1,12), PK(2, 5), PK(3, 1), PK(0, 6), PK(1,11), PK(2, 4), PK(3, 0)
};
static const unsigned char cw_bits[4] = {24, 23, 11, 14};
static const unsigned char sil_info[7] = {0xF8, 0x01, 0xA9, 0x9F, 0x8C, 0xE0, 0x80};

void krk_ambe_set_parity(int mode) { g_par = (unsigned char)(mode < 0 || mode > 2 ? 0 : mode); }

/* ---- Golay ------------------------------------------------------------------------------------------------ */
static unsigned gsyn(unsigned r)
{
    int i;
    for (i = 22; i >= 11; i--)
        if ((r >> i) & 1) r ^= 0xC75u << (i - 11);
    return r & 0x7FF;
}

static unsigned par23(unsigned x)
{
    x ^= x >> 16; x ^= x >> 8; x ^= x >> 4; x ^= x >> 2; x ^= x >> 1;
    return x & 1;
}

unsigned krk_golay23_encode(unsigned d)
{
    d &= 0xFFF;
    return (d << 11) | gsyn(d << 11);
}

int krk_golay23_decode(unsigned *cw)
{
    unsigned s = gsyn(*cw), t, u;
    int i, j, k;
    if (!s) return 0;
    for (i = 0; i < 23; i++)
        if (s == syn1[i]) { *cw ^= 1u << i; return 1; }
    for (i = 0; i < 23; i++) {
        t = s ^ syn1[i];
        for (j = i + 1; j < 23; j++)
            if (t == syn1[j]) { *cw ^= (1u << i) | (1u << j); return 2; }
    }
    for (i = 0; i < 23; i++)
        for (j = i + 1; j < 23; j++) {
            t = s ^ syn1[i] ^ syn1[j];
            for (k = j + 1; k < 23; k++)
                if (t == syn1[k]) { u = (1u << i) | (1u << j) | (1u << k); *cw ^= u; return 3; }
        }
    return 3;                                    /* unreachable: the Golay code is perfect */
}

unsigned krk_golay24_encode(unsigned d)
{
    unsigned c = krk_golay23_encode(d), p = 0;
    if (g_par == 0) p = par23(c);
    else if (g_par == 1) p = par23(c) ^ 1;
    return (c << 1) | p;
}

int krk_golay24_decode(unsigned *cw24, unsigned *data12)
{
    unsigned c = (*cw24 >> 1) & 0x7FFFFF, pb = *cw24 & 1, want;
    int e = krk_golay23_decode(&c), m = 0;
    if (g_par != 2) {
        want = par23(c) ^ (g_par == 1 ? 1u : 0u);
        m = (want != pb);
    }
    *cw24 = (c << 1) | (g_par == 2 ? pb : want);
    *data12 = c >> 11;
    if (e + m > 3) return -1;
    return e + m;
}

/* ---- one frame -------------------------------------------------------------------------------------------- */
unsigned krk_ambe_pn_mask(unsigned u0)
{
    unsigned pr = (16u * (u0 & 0xFFF)) & 0xFFFF, mask = 0;
    int k;
    for (k = 1; k < 24; k++) {
        pr = (173u * pr + 13849u) & 0xFFFF;
        mask |= (pr >> 15) << (23 - k);
    }
    return mask;
}

static unsigned getb(const unsigned char *p, unsigned i) { return (p[i >> 3] >> (7 - (i & 7))) & 1; }
static void putb(unsigned char *p, unsigned i, unsigned v)
{
    unsigned char m = (unsigned char)(0x80 >> (i & 7));
    if (v) p[i >> 3] |= m; else p[i >> 3] &= (unsigned char)~m;
}

void krk_ambe_air_to_cw(const unsigned char *fr, unsigned *cw)
{
    int i;
    cw[0] = cw[1] = cw[2] = cw[3] = 0;
    for (i = 0; i < 72; i++)
        cw[air_tbl[i] >> 5] |= getb(fr, (unsigned)i) << (air_tbl[i] & 31);
}

void krk_ambe_cw_to_air(const unsigned *cw, unsigned char *fr)
{
    int i;
    for (i = 0; i < 9; i++) fr[i] = 0;
    for (i = 0; i < 72; i++)
        putb(fr, (unsigned)i, (cw[air_tbl[i] >> 5] >> (air_tbl[i] & 31)) & 1);
}

void krk_ambe_plain_to_cw(const unsigned char *fr, unsigned *cw)
{
    int c, j;
    unsigned pos = 0;
    for (c = 0; c < 4; c++) {
        cw[c] = 0;
        for (j = 0; j < cw_bits[c]; j++) cw[c] = (cw[c] << 1) | getb(fr, pos++);
    }
}

void krk_ambe_cw_to_plain(const unsigned *cw, unsigned char *fr)
{
    int c, j;
    unsigned pos = 0;
    for (j = 0; j < 9; j++) fr[j] = 0;
    for (c = 0; c < 4; c++)
        for (j = cw_bits[c] - 1; j >= 0; j--) putb(fr, pos++, (cw[c] >> j) & 1);
}

static void putv(unsigned char *p, unsigned *pos, unsigned v, int n)
{
    while (n--) putb(p, (*pos)++, (v >> n) & 1);
}

static unsigned getv(const unsigned char *p, unsigned *pos, int n)
{
    unsigned v = 0;
    while (n--) v = (v << 1) | getb(p, (*pos)++);
    return v;
}

int krk_ambe_cw_to_info(const unsigned *cw, unsigned char *info, int *c0err, int *c1err)
{
    unsigned w0 = cw[0], u0, c1, u1, pos = 0;
    int e0, e1, i;
    e0 = krk_golay24_decode(&w0, &u0);
    if (e0 < 0) { u0 = cw[0] >> 12; }
    c1 = (cw[1] ^ krk_ambe_pn_mask(u0)) & 0x7FFFFF;
    e1 = krk_golay23_decode(&c1);
    u1 = c1 >> 11;
    for (i = 0; i < KRK_AMBE_INFO_BYTES; i++) info[i] = 0;
    putv(info, &pos, u0, 12);
    putv(info, &pos, u1, 12);
    putv(info, &pos, cw[2], 11);
    putv(info, &pos, cw[3], 14);
    if (c0err) *c0err = e0 < 0 ? 4 : e0;
    if (c1err) *c1err = e1;
    return e0 < 0 ? -1 : e0 + e1;
}

void krk_ambe_info_to_cw(const unsigned char *info, unsigned *cw)
{
    unsigned pos = 0, u0, u1;
    u0 = getv(info, &pos, 12);
    u1 = getv(info, &pos, 12);
    cw[0] = krk_golay24_encode(u0);
    cw[1] = krk_golay23_encode(u1) ^ krk_ambe_pn_mask(u0);
    cw[2] = getv(info, &pos, 11);
    cw[3] = getv(info, &pos, 14);
}

int krk_ambe_info_is_silence(const unsigned char *info)
{
    unsigned b0 = ((unsigned)(info[0] >> 2) << 1) | (info[6] >> 7);
    return b0 == 124 || b0 == 125;
}

void krk_ambe_silence_info(unsigned char *info)
{
    int i;
    for (i = 0; i < 7; i++) info[i] = sil_info[i];
}

/* ---- unit layouts ----------------------------------------------------------------------------------------- */
static unsigned ubit(const unsigned char *u, unsigned i, int lsb)
{
    return lsb ? (u[i >> 3] >> (i & 7)) & 1 : (u[i >> 3] >> (7 - (i & 7))) & 1;
}

static void uput(unsigned char *u, unsigned i, int lsb, unsigned v)
{
    unsigned char m = (unsigned char)(lsb ? 1u << (i & 7) : 0x80u >> (i & 7));
    if (v) u[i >> 3] |= m; else u[i >> 3] &= (unsigned char)~m;
}

void krk_ambe_unit_get(const unsigned char *unit, int k, int layout, unsigned *cw)
{
    unsigned char fr[9];
    int i, lsb = layout == KRK_AMBE_L_AIR_LSB || layout == KRK_AMBE_L_PLAIN_LSB;
    for (i = 0; i < 9; i++) fr[i] = 0;
    for (i = 0; i < 72; i++) putb(fr, (unsigned)i, ubit(unit, (unsigned)(72 * k + i), lsb));
    if (layout == KRK_AMBE_L_AIR || layout == KRK_AMBE_L_AIR_LSB) krk_ambe_air_to_cw(fr, cw);
    else krk_ambe_plain_to_cw(fr, cw);
}

void krk_ambe_unit_put(unsigned char *unit, int k, int layout, const unsigned *cw)
{
    unsigned char fr[9];
    int i, lsb = layout == KRK_AMBE_L_AIR_LSB || layout == KRK_AMBE_L_PLAIN_LSB;
    if (layout == KRK_AMBE_L_AIR || layout == KRK_AMBE_L_AIR_LSB) krk_ambe_cw_to_air(cw, fr);
    else krk_ambe_cw_to_plain(cw, fr);
    for (i = 0; i < 72; i++) uput(unit, (unsigned)(72 * k + i), lsb, getb(fr, (unsigned)i));
}

/* ---- detection -------------------------------------------------------------------------------------------- */
static int count_good(const unsigned char *units, int n, int layout)
{
    int f, good = 0;
    unsigned cw[4];
    for (f = 0; f < 3 * n; f++) {
        krk_ambe_unit_get(units + KRK_AMBE_UNIT_BYTES * (f / 3), f % 3, layout, cw);
        if (!gsyn(cw[0] >> 1)) good++;
    }
    return good;
}

int krk_ambe_layout_score(const unsigned char *units, int n, int layout)
{
    if (n <= 0 || layout < 1 || layout >= KRK_AMBE_NLAYOUTS) return 0;
    return count_good(units, n, layout) * 1000 / (3 * n);
}

int krk_ambe_detect_layout(const unsigned char *units, int n)
{
    int l, g, best = 0, bg = -1, second = 0;
    for (l = 1; l < KRK_AMBE_NLAYOUTS && n > 0; l++) {
        g = count_good(units, n, l);
        if (g > bg) { second = bg < 0 ? 0 : bg; bg = g; best = l; }
        else if (g > second) second = g;
    }
    g_score_pm = (unsigned short)(bg < 0 ? 0 : bg * 1000 / (3 * n));
    /* a wrong layout hits a valid C0 word by chance with p = 1/2048 per frame; the right one hits nearly always */
    if (bg < 2 || g_score_pm < 250 || bg < 2 * second + 1) return KRK_AMBE_L_RAW;
    return best;
}

int krk_ambe_detect_score(void) { return g_score_pm; }

int krk_ambe_detect_parity(const unsigned char *units, int n, int layout)
{
    int f, tot = 3 * n, even = 0, odd = 0, zero = 0;
    unsigned cw[4], c;
    if (layout < 1 || layout >= KRK_AMBE_NLAYOUTS) return 0;
    for (f = 0; f < tot; f++) {
        krk_ambe_unit_get(units + KRK_AMBE_UNIT_BYTES * (f / 3), f % 3, layout, cw);
        c = cw[0] >> 1;
        if (gsyn(c)) continue;
        if ((cw[0] & 1) == par23(c)) even++; else odd++;
        if (!(cw[0] & 1)) zero++;
    }
    if (even >= odd && even >= zero) return 0;
    return odd >= zero ? 1 : 2;
}

/* ---- container payload ------------------------------------------------------------------------------------ */
int krk_ambe_packed_bits(int n, int ns) { return (3 * n - ns) * KRK_AMBE_INFO_BITS; }

int krk_ambe_pack(const unsigned char *units, int n, int layout, int trim, unsigned char *out, int cap, krk_ambe_stats_t *st)
{
    unsigned char info[7], sil[96];
    unsigned cw[4], pos, bits, run, i, p;
    int f, tot = 3 * n, e, c0, c1, len, ns = 0, cur, s;
    krk_ambe_stats_t z;
    if (!st) st = &z;
    st->frames = st->silent = st->bad = st->c0_fixed = st->c1_fixed = 0;
    if (n < 0 || n > 255) return -1;
    if (layout == KRK_AMBE_L_RAW || layout < 0 || layout >= KRK_AMBE_NLAYOUTS) {
        if (cap < n * KRK_AMBE_UNIT_BYTES) return -1;
        for (i = 0; i < (unsigned)(n * KRK_AMBE_UNIT_BYTES); i++) out[i] = units[i];
        return n * KRK_AMBE_UNIT_BYTES;
    }
    for (i = 0; i < sizeof sil; i++) sil[i] = 0;
    for (f = 0; f < tot; f++) {                  /* pass 1: statistics and the silence map */
        krk_ambe_unit_get(units + KRK_AMBE_UNIT_BYTES * (f / 3), f % 3, layout, cw);
        e = krk_ambe_cw_to_info(cw, info, &c0, &c1);
        st->frames++;
        if (e < 0) st->bad++; else { st->c0_fixed += (unsigned)c0; st->c1_fixed += (unsigned)c1; }
        if (e >= 0 && krk_ambe_info_is_silence(info)) {
            st->silent++;
            if (trim) { ns++; sil[f >> 3] |= (unsigned char)(0x80 >> (f & 7)); }
        }
    }
    if (cap < 1) return -1;
    out[0] = (unsigned char)n;
    len = 1;
    if (trim && tot > 0) {                       /* run lengths, first run = non-silent */
        cur = 0; run = 0;
        for (f = 0; f <= tot; f++) {
            s = f < tot ? (sil[f >> 3] >> (7 - (f & 7))) & 1 : -1;
            if (s == cur) { run++; continue; }
            while (run >= 255) { if (len >= cap) return -1; out[len++] = 255; run -= 255; }
            if (len >= cap) return -1;
            out[len++] = (unsigned char)run;
            run = 1; cur = s;
        }
    }
    bits = (unsigned)((tot - ns) * KRK_AMBE_INFO_BITS);
    if (len + (int)((bits + 7) / 8) > cap) return -1;
    for (i = 0; i < (bits + 7) / 8; i++) out[len + (int)i] = 0;
    pos = (unsigned)len * 8;
    for (f = 0; f < tot; f++) {
        if (trim && ((sil[f >> 3] >> (7 - (f & 7))) & 1)) continue;
        krk_ambe_unit_get(units + KRK_AMBE_UNIT_BYTES * (f / 3), f % 3, layout, cw);
        krk_ambe_cw_to_info(cw, info, 0, 0);
        for (p = 0; p < 49; p++) putb(out, pos++, getb(info, p));
    }
    return len + (int)((bits + 7) / 8);
}

int krk_ambe_unpack(const unsigned char *in, int len, int layout, int trim, unsigned char *units, int cap_units)
{
    unsigned char info[7], sil[96];
    unsigned cw[4], pos, p, run;
    int n, tot, f, i, sum, silent = 0, cur, rp;
    if (layout == KRK_AMBE_L_RAW || layout < 0 || layout >= KRK_AMBE_NLAYOUTS) {
        if (len < 0 || len % KRK_AMBE_UNIT_BYTES || len / KRK_AMBE_UNIT_BYTES > cap_units) return -1;
        for (i = 0; i < len; i++) units[i] = in[i];
        return len / KRK_AMBE_UNIT_BYTES;
    }
    if (len < 1) return -1;
    n = in[0];
    tot = 3 * n;
    if (n > cap_units) return -1;
    for (i = 0; i < (int)sizeof sil; i++) sil[i] = 0;
    rp = 1;
    if (trim && tot > 0) {                       /* run lengths -> silence map */
        sum = 0; cur = 0;
        while (sum < tot) {
            run = 0;
            for (;;) {
                if (rp >= len) return -1;
                i = in[rp++];
                run += (unsigned)i;
                if (i != 255) break;
            }
            if ((int)run > tot - sum) return -1;
            for (f = sum; cur && f < sum + (int)run; f++) sil[f >> 3] |= (unsigned char)(0x80 >> (f & 7));
            if (cur) silent += (int)run;
            sum += (int)run;
            cur ^= 1;
        }
    }
    if (rp + ((tot - silent) * 49 + 7) / 8 > len) return -1;
    pos = (unsigned)rp * 8;
    for (i = 0; i < n * KRK_AMBE_UNIT_BYTES; i++) units[i] = 0;
    for (f = 0; f < tot; f++) {
        if (((sil[f >> 3] >> (7 - (f & 7))) & 1)) krk_ambe_silence_info(info);
        else {
            for (i = 0; i < 7; i++) info[i] = 0;
            for (p = 0; p < 49; p++) putb(info, p, getb(in, pos++));
        }
        krk_ambe_info_to_cw(info, cw);
        krk_ambe_unit_put(units + KRK_AMBE_UNIT_BYTES * (f / 3), f % 3, layout, cw);
    }
    return n;
}
