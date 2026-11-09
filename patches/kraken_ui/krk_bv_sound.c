/*
 * krk_bv_sound.c - channel sounding probe for hardware test H1 (docs/burst-voice.md 6.2). A fixed 8 kHz sequence the
 * radio sends with one key press; the receiving radio's captured audio is analysed by tools/bv_sounding_analysis.py
 * (magnitude / group delay 300-3800 Hz from the chirps, TX limiter linearity from the three levels, and a real BV1
 * burst through the receiver for SNR / EVM / block success). Phase-accumulator chirps, no libc, no float.
 */
#include "krk_bv.h"

enum { K_SIL, K_TONE, K_CHIRP, K_FRAME };
static const struct { unsigned char kind, blocks; short amp; unsigned inc0, dinc; } seg[] = {
    { K_TONE,  10, 8000, 536870912u, 0 },                       /* 1000 Hz */
    { K_SIL,    2, 0, 0, 0 },
    { K_CHIRP, 63, 8000, 107374182u, 119837u },                 /* 200 -> 3800 Hz */
    { K_SIL,    2, 0, 0, 0 },
    { K_CHIRP, 25, 4000, 161061274u, 251658u },                 /* 300 -> 3300 Hz, -6 dB */
    { K_CHIRP, 25, 2000, 161061274u, 251658u },                 /* -12 dB */
    { K_SIL,    3, 0, 0, 0 },
    { K_FRAME,  0, 0, 0, 0 },
};
#define NSEG ((int)(sizeof seg / sizeof seg[0]))

static int si, sblk;
static unsigned ph, inc;
static unsigned char *spay;

void krk_bv_sound_payload(unsigned char *o)
{
    unsigned s = 0x1D2Bu;
    int i, k;
    for (i = 0; i < BV_SOUND_UNITS * BV_UNIT_BYTES; i++) {
        for (k = 0; k < 8; k++) { unsigned b = ((s >> 0) ^ (s >> 2) ^ (s >> 3) ^ (s >> 5)) & 1; s = (s >> 1) | (b << 15); }
        o[i] = (unsigned char)s;
    }
}

void krk_bv_sound_start(unsigned char *payload)
{
    spay = payload; krk_bv_sound_payload(payload);
    si = 0; sblk = 0; ph = 0; inc = seg[0].inc0;
}

static int sine(unsigned p)                     /* Q14 sine of a 32-bit phase, linear interpolation of the 256-entry table */
{
    unsigned i = (p >> 24) + 192, f = (p >> 16) & 255;
    int a = krk_bv_cos256[i & 255], b = krk_bv_cos256[(i + 1) & 255];
    return a + (((b - a) * (int)f) >> 8);
}

int krk_bv_sound_fill(short *out)
{
    int n;
    if (si >= NSEG) { for (n = 0; n < 256; n++) out[n] = 0; return 0; }
    if (seg[si].kind == K_FRAME) {
        int more;
        if (!sblk) { krk_bv_tx_level(4000, 30000); krk_bv_tx_start(BV_MODE_FAST, 0x5D1, 0x123456, spay, BV_SOUND_UNITS, 0, 0); }
        sblk = 1;
        more = krk_bv_tx_fill(out);
        if (!more) { si++; return 0; }
        return 1;
    }
    for (n = 0; n < 256; n++) {
        int v = 0;
        if (seg[si].kind != K_SIL) {
            v = (sine(ph) * seg[si].amp) >> 14;
            ph += inc; inc += seg[si].dinc;
        }
        out[n] = (short)v;
    }
    if (++sblk >= seg[si].blocks) { si++; sblk = 0; ph = 0; if (si < NSEG) inc = seg[si].inc0; }
    return 1;
}
