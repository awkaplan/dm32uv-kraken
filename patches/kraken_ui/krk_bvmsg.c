/* krk_bvmsg.c - see krk_bvmsg.h. */
#include "krk_bvmsg.h"
#include "krk_ambe.h"

/* the 27-byte silence unit (3 x MMDVM NULL_AMBE, krk_vframe.c krk_vf_silence) */
static const unsigned char sil27[27] = {
    0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B, 0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B,
    0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B,
};

int krk_bvm_pack(const unsigned char *units, int n, unsigned char *out, int *flags, int *layout)
{
    int l, par, len, i;
    krk_ambe_stats_t st;

    if (n < 1)
        return -1;
    if (n > BVM_NU_MAX)
        n = BVM_NU_MAX;
    l = krk_ambe_detect_layout(units, n);
    *layout = l;
    if (l != KRK_AMBE_L_RAW) {
        par = krk_ambe_detect_parity(units, n, l);
        krk_ambe_set_parity(par);
        len = krk_ambe_pack(units, n, l, 1, out, BVM_B_CAP, &st);
        if (len > 0) {
            int chunks = (len + BV_UNIT_BYTES - 1) / BV_UNIT_BYTES;

            for (i = len; i < chunks * BV_UNIT_BYTES; i++)
                out[i] = 0;
            *flags = BV_FLAG_TRIMMED | BV_FLAG_STRIPPED | (l << BV_FLAG_LAYOUT_SHIFT) | (par << BV_FLAG_PARITY_SHIFT);
            return chunks;
        }
    }
    *layout = KRK_AMBE_L_RAW;
    if (n > BVM_MAX_CHUNKS)
        return -2;
    for (i = 0; i < n * BV_UNIT_BYTES; i++)
        out[i] = units[i];
    *flags = 0;
    return n;
}

int krk_bvm_unpack(const krk_bv_info_t *in, unsigned char *b, unsigned char *a, int cap_units)
{
    int chunks = in->hdr.nunits, n, blk, k, i;

    if (!in->have_hdr || !in->blocks_ok)
        return 0;
    if (chunks > BVM_MAX_CHUNKS)
        chunks = BVM_MAX_CHUNKS;
    if (in->hdr.flags & BV_FLAG_STRIPPED) {
        krk_ambe_set_parity((in->hdr.flags >> BV_FLAG_PARITY_SHIFT) & 3);
        return krk_ambe_unpack(b, chunks * BV_UNIT_BYTES, (in->hdr.flags >> BV_FLAG_LAYOUT_SHIFT) & 7,
                               (in->hdr.flags & BV_FLAG_TRIMMED) != 0, a, cap_units);
    }
    n = chunks > cap_units ? cap_units : chunks;
    for (blk = 0; blk < in->hdr.nblocks && blk < BV_MAX_BLOCKS; blk++) {
        if (in->ok[blk])
            continue;
        for (k = 0; k < BV_UNITS_PER_BLOCK && blk * BV_UNITS_PER_BLOCK + k < chunks; k++)
            for (i = 0; i < BV_UNIT_BYTES; i++)
                b[BV_UNIT_BYTES * (blk * BV_UNITS_PER_BLOCK + k) + i] = sil27[i];
    }
    for (i = 0; i < n * BV_UNIT_BYTES; i++)
        a[i] = b[i];
    return n;
}

int krk_bvm_chunks_estimate(const unsigned char *units, int n, int *layout)
{
    int l = krk_ambe_detect_layout(units, n);

    *layout = l;
    return l == KRK_AMBE_L_RAW ? n : ((n * 147 + 7) / 8 + 1 + BV_UNIT_BYTES - 1) / BV_UNIT_BYTES;
}

/* Deterministic non-silent test message (Burst Voice key 9): xorshift32 from a fixed seed gives each frame's 49 info bits; b0 (pitch index)
 * is forced into 30..99 so no frame is silence (124/125); frames are encoded with C0 parity mode 0 into L1 (air order), the layout the mic
 * recording shows on hardware. Encode-from-info makes re-encoding in krk_ambe_unpack idempotent. */
void krk_bvm_testmsg(unsigned char *units, int n)
{
    unsigned s = 0x1BADB002u;
    int u, k, i;

    krk_ambe_set_parity(0);
    for (i = 0; i < n * KRK_AMBE_UNIT_BYTES; i++)
        units[i] = 0;
    for (u = 0; u < n; u++)
        for (k = 0; k < 3; k++) {
            unsigned char info[7];
            unsigned cw[4], b0;

            for (i = 0; i < 7; i++) {
                s ^= s << 13;
                s ^= s >> 17;
                s ^= s << 5;
                info[i] = (unsigned char)(s >> 11);
            }
            b0 = 30 + (((unsigned)info[6] << 8 | info[5]) % 70);
            info[0] = (unsigned char)((info[0] & 3) | (b0 >> 1) << 2);
            info[6] = (unsigned char)((b0 & 1) << 7);
            krk_ambe_info_to_cw(info, cw);
            krk_ambe_unit_put(units + u * KRK_AMBE_UNIT_BYTES, k, KRK_AMBE_L_AIR, cw);
        }
}
