/* Host test glue for F13 (tests/test_f13.py): valid AMBE units, the message pack/unpack and a burst TX with header flags.
 * Not part of the radio image. */
#include <string.h>
#include "krk_bv.h"
#include "krk_ambe.h"
#include "krk_bvmsg.h"

static unsigned rng(unsigned *s) { unsigned x = *s; x ^= x << 13; x ^= x >> 17; x ^= x << 5; return *s = x; }

/* n units in the given layout from valid frames (Golay words correct); every sil_every-th frame is the canonical silence (0 = none). */
void f13t_units(unsigned char *units, int n, int layout, unsigned seed, int sil_every)
{
    unsigned s = seed ? seed : 1;
    int u, k, i;
    memset(units, 0, (size_t)n * 27);
    for (u = 0; u < n; u++)
        for (k = 0; k < 3; k++) {
            unsigned char info[7];
            unsigned cw[4];
            for (i = 0; i < 7; i++) info[i] = (unsigned char)rng(&s);
            info[6] &= 0x80;
            if (sil_every && ((u * 3 + k) % sil_every) < 2) krk_ambe_silence_info(info);
            else if (krk_ambe_info_is_silence(info)) info[0] ^= 0x04;     /* a random frame must not be silence by accident */
            krk_ambe_info_to_cw(info, cw);
            krk_ambe_unit_put(units + u * 27, k, layout, cw);
        }
}

int f13t_pack(const unsigned char *units, int n, unsigned char *out, int *flags, int *layout)
{
    return krk_bvm_pack(units, n, out, flags, layout);
}

/* Burst PCM of a packed payload (chunks x 27 bytes) with explicit header flags; returns samples. */
int f13t_tx(int mode, const unsigned char *chunks, int nchunks, int flags, short *out, int maxn, int rms)
{
    int n = 0;
    krk_bv_tx_level(rms, 30000);
    krk_bv_tx_start(mode, 0x5A5, 0x123456, chunks, nchunks, 0, flags);
    while (n + 256 <= maxn) { int more = krk_bv_tx_fill(out + n); n += 256; if (!more) break; }
    return n;
}

/* Receive samples into buf (cap 78 chunks), then rebuild the units like the screen does. Returns units (<= 0 on failure); st[0] events,
 * st[1] blocks ok, st[2] blocks total, st[3] rs corrected. */
int f13t_rx(const short *x, int n, unsigned char *buf, unsigned char *units, int cap_units, int *st)
{
    int i, ev = 0;
    memset(buf, 0, 78 * 27);
    krk_bv_rx_begin(buf, 78 * 27);
    for (i = 0; i < n; i += 256) ev |= krk_bv_rx_feed(x + i, n - i < 256 ? n - i : 256);
    st[0] = ev;
    st[1] = krk_bv_info()->blocks_ok;
    st[2] = krk_bv_info()->hdr.nblocks;
    st[3] = (int)krk_bv_info()->rs_corrected;
    return krk_bvm_unpack(krk_bv_info(), buf, units, cap_units);
}

int f13t_hdr_flags_roundtrip(int flags)
{
    krk_bv_hdr_t h, o;
    unsigned char w[24];
    memset(&h, 0, sizeof h);
    h.version = 1; h.flags = (unsigned char)flags; h.tick = 15; h.nblocks = 3; h.msg_id = 0x1234; h.src_id = 0xABCDEF; h.nunits = 17; h.mode = 1;
    krk_bv_hdr_encode(w, &h);
    if (krk_bv_hdr_decode(w, &o) < 0) return -1;
    return o.flags == flags && o.nunits == 17 && o.src_id == 0xABCDEF;
}

/* Burst Voice key 9: the generated test message. Returns the number of silence frames and, via *bad, frames that needed FEC correction. */
int f13t_testmsg(unsigned char *units, int n, int *bad)
{
    int f, sil = 0;

    krk_bvm_testmsg(units, n);
    *bad = 0;
    for (f = 0; f < 3 * n; f++) {
        unsigned cw[4];
        unsigned char info[7];

        krk_ambe_unit_get(units + 27 * (f / 3), f % 3, KRK_AMBE_L_AIR, cw);
        if (krk_ambe_cw_to_info(cw, info, 0, 0) != 0)
            (*bad)++;
        if (krk_ambe_info_is_silence(info))
            sil++;
    }
    return sil;
}
