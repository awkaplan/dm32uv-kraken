/*
 * krk_ax25.c - see krk_ax25.h. Host-tested against an independent decoder: tests/test_ax25.py.
 * No libc: this links into the patch blob.
 */
#include "krk_ax25.h"

/* "CALL-SSID" -> 7 address bytes (callsign shifted left 1, space padded; SSID byte 0x60|ssid<<1,
 * with the extension bit set on the last address). */
static int put_addr(u8 *o, const char *s, int last)
{
    int i = 0, ssid = 0;

    for (; i < 6 && s[i] && s[i] != '-'; i++) {
        char c = s[i];
        if (c >= 'a' && c <= 'z')
            c = (char)(c - 32);
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')))
            return -1;
        o[i] = (u8)(c << 1);
    }
    if (i == 0)
        return -1;
    if (s[i] && s[i] != '-')
        return -1;                          /* callsign longer than 6 */
    if (s[i] == '-') {
        const char *p = s + i + 1;
        if (!*p)
            return -1;
        while (*p) {
            if (*p < '0' || *p > '9')
                return -1;
            ssid = ssid * 10 + (*p++ - '0');
        }
        if (ssid > 15)
            return -1;
    }
    for (; i < 6; i++)
        o[i] = ' ' << 1;
    o[6] = (u8)(0x60 | (ssid << 1) | (last ? 1 : 0));
    return 7;
}

u16 krk_ax25_fcs(const u8 *p, int n)
{
    u16 crc = 0xffff;
    int i, b;

    for (i = 0; i < n; i++) {
        crc ^= p[i];
        for (b = 0; b < 8; b++)
            crc = (crc & 1) ? (u16)((crc >> 1) ^ 0x8408) : (u16)(crc >> 1);
    }
    return (u16)~crc;
}

int krk_ax25_frame(u8 *out, int max, const char *dest, const char *src,
                   const char *const *path, int npath, const char *info)
{
    int n = 0, i;
    u16 fcs;

    if (npath < 0 || npath > 6 || max < 7 * (2 + npath) + 2 + 2)
        return -1;
    if (put_addr(out + n, dest, 0) < 0)
        return -1;
    n += 7;
    if (put_addr(out + n, src, npath == 0) < 0)
        return -1;
    n += 7;
    for (i = 0; i < npath; i++) {
        if (put_addr(out + n, path[i], i == npath - 1) < 0)
            return -1;
        n += 7;
    }
    out[n++] = 0x03;                        /* UI frame */
    out[n++] = 0xf0;                        /* no layer 3 */
    while (*info) {
        if (n >= max - 2)
            return -1;
        out[n++] = (u8)*info++;
    }
    fcs = krk_ax25_fcs(out, n);
    out[n++] = (u8)(fcs & 0xff);            /* FCS goes out low byte first */
    out[n++] = (u8)(fcs >> 8);
    return n;
}

struct bitw {
    u8 *out;
    int n, max;
    u8 tone;                                /* current tone, 0 = mark */
};

static int emit(struct bitw *w, int bit)
{
    if (w->n >= w->max)
        return -1;
    if (!bit)
        w->tone ^= 1;                       /* NRZI: a 0 is a tone change */
    if (w->tone)
        w->out[w->n >> 3] |= (u8)(1 << (w->n & 7));
    else
        w->out[w->n >> 3] &= (u8)~(1 << (w->n & 7));
    w->n++;
    return 0;
}

static int flags(struct bitw *w, int k)
{
    int b;

    while (k-- > 0)
        for (b = 0; b < 8; b++)
            if (emit(w, (0x7e >> b) & 1) < 0)
                return -1;
    return 0;
}

int krk_ax25_tones(const u8 *frame, int len, int pre_flags, int post_flags, u8 *out, int max_bits)
{
    struct bitw w = {out, 0, max_bits, 0};
    int i, b, ones = 0;

    if (flags(&w, pre_flags) < 0)
        return -1;
    for (i = 0; i < len; i++) {
        for (b = 0; b < 8; b++) {
            int bit = (frame[i] >> b) & 1;  /* LSB first */
            if (emit(&w, bit) < 0)
                return -1;
            if (bit) {
                if (++ones == 5) {
                    if (emit(&w, 0) < 0)    /* stuff */
                        return -1;
                    ones = 0;
                }
            } else {
                ones = 0;
            }
        }
    }
    if (flags(&w, post_flags) < 0)
        return -1;
    return w.n;
}
