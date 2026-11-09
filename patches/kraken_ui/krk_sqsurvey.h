/*
 * krk_sqsurvey.h - pure logic of the F11 one-key squelch survey (key 6, docs/noise-squelch.md "One-action
 * hardware test"). Header-only so tests/test_sqsurvey.py can compile it on the host; no MMIO in here.
 *
 * The operator starts the survey with the second radio idle, then keys it up ONCE (an unmodulated carrier)
 * somewhere in the window. Every 100 ms the screen feeds (noise_sq, rssi). The first SV_BASE samples
 * (3 s, carrier assumed absent) give the idle RSSI baseline; afterwards a sample is "carrier" when its RSSI is
 * more than SV_RISE half-dB (10 dB) above that baseline. A class change is ignored for SV_SETTLE samples
 * (the RSSI/noise detectors lag), then each class keeps its extreme noise:
 *   idle_min  lowest noise_sq with no carrier   (a threshold above it would open on noise)
 *   carr_max  highest noise_sq with the carrier (a threshold below it would never open on that signal)
 * The squelch opens when noise < in, so a usable `in` lies between them. gap = idle_min - carr_max;
 *   Tight  in = carr_max + gap / 3        (only a clean signal opens it)
 *   Normal in = carr_max + gap * 2 / 3    (a noisier signal opens, idle noise still has 1/3 gap of margin)
 *   out = in + 2 (the stock pair 47/48 and the presets use a 1..2 hysteresis)
 * Needs gap >= SV_MINGAP; otherwise no suggestion and the status says why.
 */
#ifndef KRK_SQSURVEY_H
#define KRK_SQSURVEY_H
#include "krk.h"

#define SV_TICKS  250       /* 25 s at the 100 ms screen tick */
#define SV_BASE   30
#define SV_RISE   20        /* half-dB */
#define SV_SETTLE 3
#define SV_MINN   5         /* samples needed per class */
#define SV_MINGAP 4

enum { SV_OK, SV_NO_CARRIER, SV_NO_IDLE, SV_NO_GAP };

struct sqsv {
    u32 base_sum;
    u16 n, base_rssi, idle_n, carr_n;
    u8 cls, run, idle_min, carr_max;
};

static inline void sv_reset(struct sqsv *s)
{
    u8 *p = (u8 *)s;
    unsigned i;

    for (i = 0; i < sizeof *s; i++)
        p[i] = 0;
    s->idle_min = 255;
}

/* One 100 ms sample. Returns 1 when the window is complete. */
static inline int sv_step(struct sqsv *s, u32 noise, u32 rssi)
{
    u8 cls = 0;

    if (s->n < SV_BASE) {
        s->base_sum += rssi;
        if (s->n == SV_BASE - 1)
            s->base_rssi = (u16)(s->base_sum / SV_BASE);
    } else {
        cls = rssi > (u32)s->base_rssi + SV_RISE;
    }
    if (cls != s->cls) {
        s->cls = cls;
        s->run = 0;
    } else if (s->run < 255) {
        s->run++;
    }
    if (s->run >= SV_SETTLE) {
        if (!cls) {
            s->idle_n++;
            if (noise < s->idle_min)
                s->idle_min = (u8)noise;
        } else {
            s->carr_n++;
            if (noise > s->carr_max)
                s->carr_max = (u8)noise;
        }
    }
    return ++s->n >= SV_TICKS;
}

/* Suggested pairs. Returns SV_OK or why not; nrm/tgt = {in, out}. */
static inline int sv_derive(const struct sqsv *s, u8 nrm[2], u8 tgt[2])
{
    int gap;

    if (s->carr_n < SV_MINN)
        return SV_NO_CARRIER;
    if (s->idle_n < SV_MINN)
        return SV_NO_IDLE;
    gap = (int)s->idle_min - (int)s->carr_max;
    if (gap < SV_MINGAP)
        return SV_NO_GAP;
    tgt[0] = (u8)(s->carr_max + gap / 3);
    nrm[0] = (u8)(s->carr_max + gap * 2 / 3);
    tgt[1] = (u8)(tgt[0] + 2);
    nrm[1] = (u8)(nrm[0] + 2);
    return SV_OK;
}
#endif
