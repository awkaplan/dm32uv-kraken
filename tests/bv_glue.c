/* Host test glue for the BV1 burst modem: flat-integer wrappers so tests/test_bv.py can drive the C code
 * through ctypes without mirroring C structs. Not part of the radio image. */
#include <math.h>
#include <string.h>
#include "krk_bv.h"

void bvt_carrier(int hz) { krk_bv_set_carrier(hz); }

int bvt_tx_all(int mode, unsigned msg, unsigned src, const unsigned char *units, int nunits,
               const unsigned char *gaps, short *out, int maxn, int rms)
{
    int n = 0;
    krk_bv_tx_level(rms, 30000);
    krk_bv_tx_start(mode, msg, src, units, nunits, gaps, 0);
    while (n + 256 <= maxn) { int more = krk_bv_tx_fill(out + n); n += 256; if (!more) break; }
    return n;
}

/* st (int[128]) layout: 0 events, 1 blocks_ok, 2 blocks_bad, 3 blocks_seen, 4 have_hdr, 5 nunits, 6 nblocks, 7 msg_id, 8 src,
 * 9 flags, 10 sync mode, 11 header mode, 12 rs_corrected, 13 evm_q16, 14 resync_err, 20 chunk index of the DONE event,
 * 32..71 block ok, 72..111 block gap. */
void bvt_info(int *st)
{
    int i;
    const krk_bv_info_t *p = krk_bv_info();
    st[1] = p->blocks_ok; st[2] = p->blocks_bad; st[3] = p->blocks_seen; st[4] = p->have_hdr;
    st[5] = p->hdr.nunits; st[6] = p->hdr.nblocks; st[7] = p->hdr.msg_id; st[8] = (int)p->hdr.src_id;
    st[9] = p->hdr.flags; st[10] = p->mode; st[11] = p->hdr.mode; st[12] = (int)p->rs_corrected;
    st[13] = (int)p->evm_q16; st[14] = (int)p->resync_err;
    for (i = 0; i < BV_MAX_BLOCKS; i++) { st[32 + i] = p->ok[i]; st[72 + i] = p->gap[i]; }
}

int bvt_rx_all(const short *x, int n, unsigned char *buf, int cap, int *st, int chunk)
{
    int i, ev = 0, e;
    krk_bv_rx_begin(buf, cap);
    memset(st, 0, 128 * sizeof(int));
    for (i = 0; i < n; i += chunk) { e = krk_bv_rx_feed(x + i, (n - i < chunk) ? n - i : chunk); ev |= e; if (e & BV_EV_DONE) st[20] = i; }
    bvt_info(st);
    st[0] = ev;
    return ev;
}

/* windowed-sinc resampler (host test only, uses double): out[i] = in(i * ratio) */
void bvt_resample(const double *in, int n, double *out, int nout, double ratio)
{
    int i, k;
    const double pi = 3.14159265358979323846;
    for (i = 0; i < nout; i++) {
        double t = i * ratio, acc = 0;
        int i0 = (int)t;
        for (k = -8; k <= 9; k++) {
            int j = i0 + k;
            double d = t - j, s, w;
            if (j < 0 || j >= n) continue;
            s = (d == 0.0) ? 1.0 : sin(pi * d) / (pi * d);
            w = 0.5 + 0.5 * cos(pi * d / 9.0);
            acc += in[j] * s * w;
        }
        out[i] = acc;
    }
}

int bvt_sound_all(short *out, int maxn, unsigned char *payload)
{
    int n = 0;
    krk_bv_sound_start(payload);
    while (n + 256 <= maxn) { int more = krk_bv_sound_fill(out + n); n += 256; if (!more) break; }
    return n;
}
