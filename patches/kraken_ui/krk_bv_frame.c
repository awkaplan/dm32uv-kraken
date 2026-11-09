/*
 * krk_bv_frame.c - see krk_bv.h. CRC16, Reed-Solomon over GF(256) (poly 0x11D, first root a^0), the BV1
 * header (RS(24,16)) and block (RS(182,166), 6 x 27 B AMBE units + CRC16) codecs, and the message
 * assembler / disassembler that sit between the unit buffer and the modem.
 * Freestanding: constant tables only, mutable state is zero-initialised .bss, no libc.
 */
#include "krk_bv.h"

static const unsigned char bv_exp[510] = {
    1, 2, 4, 8, 16, 32, 64, 128, 29, 58, 116, 232, 205, 135, 19, 38,
    76, 152, 45, 90, 180, 117, 234, 201, 143, 3, 6, 12, 24, 48, 96, 192,
    157, 39, 78, 156, 37, 74, 148, 53, 106, 212, 181, 119, 238, 193, 159, 35,
    70, 140, 5, 10, 20, 40, 80, 160, 93, 186, 105, 210, 185, 111, 222, 161,
    95, 190, 97, 194, 153, 47, 94, 188, 101, 202, 137, 15, 30, 60, 120, 240,
    253, 231, 211, 187, 107, 214, 177, 127, 254, 225, 223, 163, 91, 182, 113, 226,
    217, 175, 67, 134, 17, 34, 68, 136, 13, 26, 52, 104, 208, 189, 103, 206,
    129, 31, 62, 124, 248, 237, 199, 147, 59, 118, 236, 197, 151, 51, 102, 204,
    133, 23, 46, 92, 184, 109, 218, 169, 79, 158, 33, 66, 132, 21, 42, 84,
    168, 77, 154, 41, 82, 164, 85, 170, 73, 146, 57, 114, 228, 213, 183, 115,
    230, 209, 191, 99, 198, 145, 63, 126, 252, 229, 215, 179, 123, 246, 241, 255,
    227, 219, 171, 75, 150, 49, 98, 196, 149, 55, 110, 220, 165, 87, 174, 65,
    130, 25, 50, 100, 200, 141, 7, 14, 28, 56, 112, 224, 221, 167, 83, 166,
    81, 162, 89, 178, 121, 242, 249, 239, 195, 155, 43, 86, 172, 69, 138, 9,
    18, 36, 72, 144, 61, 122, 244, 245, 247, 243, 251, 235, 203, 139, 11, 22,
    44, 88, 176, 125, 250, 233, 207, 131, 27, 54, 108, 216, 173, 71, 142, 1,
    2, 4, 8, 16, 32, 64, 128, 29, 58, 116, 232, 205, 135, 19, 38, 76,
    152, 45, 90, 180, 117, 234, 201, 143, 3, 6, 12, 24, 48, 96, 192, 157,
    39, 78, 156, 37, 74, 148, 53, 106, 212, 181, 119, 238, 193, 159, 35, 70,
    140, 5, 10, 20, 40, 80, 160, 93, 186, 105, 210, 185, 111, 222, 161, 95,
    190, 97, 194, 153, 47, 94, 188, 101, 202, 137, 15, 30, 60, 120, 240, 253,
    231, 211, 187, 107, 214, 177, 127, 254, 225, 223, 163, 91, 182, 113, 226, 217,
    175, 67, 134, 17, 34, 68, 136, 13, 26, 52, 104, 208, 189, 103, 206, 129,
    31, 62, 124, 248, 237, 199, 147, 59, 118, 236, 197, 151, 51, 102, 204, 133,
    23, 46, 92, 184, 109, 218, 169, 79, 158, 33, 66, 132, 21, 42, 84, 168,
    77, 154, 41, 82, 164, 85, 170, 73, 146, 57, 114, 228, 213, 183, 115, 230,
    209, 191, 99, 198, 145, 63, 126, 252, 229, 215, 179, 123, 246, 241, 255, 227,
    219, 171, 75, 150, 49, 98, 196, 149, 55, 110, 220, 165, 87, 174, 65, 130,
    25, 50, 100, 200, 141, 7, 14, 28, 56, 112, 224, 221, 167, 83, 166, 81,
    162, 89, 178, 121, 242, 249, 239, 195, 155, 43, 86, 172, 69, 138, 9, 18,
    36, 72, 144, 61, 122, 244, 245, 247, 243, 251, 235, 203, 139, 11, 22, 44,
    88, 176, 125, 250, 233, 207, 131, 27, 54, 108, 216, 173, 71, 142,
};
static const unsigned char bv_log[256] = {
    0, 0, 1, 25, 2, 50, 26, 198, 3, 223, 51, 238, 27, 104, 199, 75,
    4, 100, 224, 14, 52, 141, 239, 129, 28, 193, 105, 248, 200, 8, 76, 113,
    5, 138, 101, 47, 225, 36, 15, 33, 53, 147, 142, 218, 240, 18, 130, 69,
    29, 181, 194, 125, 106, 39, 249, 185, 201, 154, 9, 120, 77, 228, 114, 166,
    6, 191, 139, 98, 102, 221, 48, 253, 226, 152, 37, 179, 16, 145, 34, 136,
    54, 208, 148, 206, 143, 150, 219, 189, 241, 210, 19, 92, 131, 56, 70, 64,
    30, 66, 182, 163, 195, 72, 126, 110, 107, 58, 40, 84, 250, 133, 186, 61,
    202, 94, 155, 159, 10, 21, 121, 43, 78, 212, 229, 172, 115, 243, 167, 87,
    7, 112, 192, 247, 140, 128, 99, 13, 103, 74, 222, 237, 49, 197, 254, 24,
    227, 165, 153, 119, 38, 184, 180, 124, 17, 68, 146, 217, 35, 32, 137, 46,
    55, 63, 209, 91, 149, 188, 207, 205, 144, 135, 151, 178, 220, 252, 190, 97,
    242, 86, 211, 171, 20, 42, 93, 158, 132, 60, 57, 83, 71, 109, 65, 162,
    31, 45, 67, 216, 183, 123, 164, 118, 196, 23, 73, 236, 127, 12, 111, 246,
    108, 161, 59, 82, 41, 157, 85, 170, 251, 96, 134, 177, 187, 204, 62, 90,
    203, 89, 95, 176, 156, 169, 160, 81, 11, 245, 22, 235, 122, 117, 44, 215,
    79, 174, 213, 233, 230, 231, 173, 232, 116, 214, 244, 234, 168, 80, 88, 175,
};


static int gmul(int a, int b) { return (a && b) ? bv_exp[bv_log[a] + bv_log[b]] : 0; }
static int gdiv(int a, int b) { return a ? bv_exp[bv_log[a] + 255 - bv_log[b]] : 0; }   /* b != 0 */

unsigned krk_bv_crc16(const unsigned char *p, int n)
{
    unsigned c = 0xFFFF;
    int i, k;
    for (i = 0; i < n; i++) {
        c ^= (unsigned)p[i] << 8;
        for (k = 0; k < 8; k++) c = (c & 0x8000) ? ((c << 1) ^ 0x1021) & 0xFFFF : (c << 1) & 0xFFFF;
    }
    return c;
}

#define RS_MAXR 16

void krk_bv_rs_encode(unsigned char *cw, int k, int nroots)
{
    unsigned char g[RS_MAXR + 1], par[RS_MAXR];
    int i, j, fb;
    g[0] = 1;
    for (i = 0; i < nroots; i++) {                 /* g(x) *= (x + a^i), highest coefficient first */
        g[i + 1] = 0;
        for (j = i + 1; j > 0; j--) g[j] ^= (unsigned char)gmul(g[j - 1], bv_exp[i]);
    }
    for (j = 0; j < nroots; j++) par[j] = 0;
    for (i = 0; i < k; i++) {
        fb = cw[i] ^ par[0];
        for (j = 0; j < nroots - 1; j++) par[j] = (unsigned char)(par[j + 1] ^ gmul(fb, g[j + 1]));
        par[nroots - 1] = (unsigned char)gmul(fb, g[nroots]);
    }
    for (j = 0; j < nroots; j++) cw[k + j] = par[j];
}

int krk_bv_rs_decode(unsigned char *cw, int n, int nroots)
{
    unsigned char S[RS_MAXR], lam[RS_MAXR + 1], bb[RS_MAXR + 1], tt[RS_MAXR + 1], om[RS_MAXR];
    unsigned char pos[RS_MAXR / 2];
    int i, j, r, s, L = 0, m = 1, b = 1, d, nerr = 0, any = 0, x;
    for (j = 0; j < nroots; j++) {
        s = 0;
        for (i = 0; i < n; i++) s = gmul(s, bv_exp[j]) ^ cw[i];
        S[j] = (unsigned char)s; any |= s;
    }
    if (!any) return 0;
    for (i = 0; i <= nroots; i++) lam[i] = bb[i] = 0;
    lam[0] = bb[0] = 1;
    for (r = 0; r < nroots; r++) {                 /* Berlekamp-Massey */
        d = S[r];
        for (i = 1; i <= L; i++) d ^= gmul(lam[i], S[r - i]);
        if (!d) { m++; continue; }
        x = gdiv(d, b);
        if (2 * L <= r) {
            for (i = 0; i <= nroots; i++) tt[i] = lam[i];
            for (i = m; i <= nroots; i++) lam[i] ^= (unsigned char)gmul(x, bb[i - m]);
            L = r + 1 - L; b = d; m = 1;
            for (i = 0; i <= nroots; i++) bb[i] = tt[i];
        } else {
            for (i = m; i <= nroots; i++) lam[i] ^= (unsigned char)gmul(x, bb[i - m]);
            m++;
        }
    }
    if (L > nroots / 2) return -1;
    for (i = 0; i < n; i++) {                      /* Chien search: root X^-1 of lam for position i */
        s = 0;
        for (j = 0; j <= L; j++)
            if (lam[j]) s ^= bv_exp[(bv_log[lam[j]] + (255 - ((n - 1 - i) % 255)) * j) % 255];
        if (!s) { if (nerr >= nroots / 2) return -1; pos[nerr++] = (unsigned char)i; }
    }
    if (nerr != L) return -1;
    for (i = 0; i < nroots; i++) {                 /* omega = S * lam mod x^nroots */
        s = 0;
        for (j = 0; j <= i && j <= L; j++) s ^= gmul(lam[j], S[i - j]);
        om[i] = (unsigned char)s;
    }
    for (i = 0; i < nerr; i++) {
        int e = n - 1 - pos[i], xi = (255 - e) % 255, num = 0, den = 0, p;
        for (j = 0; j < nroots; j++) if (om[j]) num ^= bv_exp[(bv_log[om[j]] + xi * j) % 255];
        for (j = 1; j <= L; j += 2) if (lam[j]) den ^= bv_exp[(bv_log[lam[j]] + xi * (j - 1)) % 255];
        if (!den) return -1;
        p = gmul(bv_exp[e % 255], gdiv(num, den));
        cw[pos[i]] ^= (unsigned char)p;
    }
    return nerr;
}

/* ---- header ------------------------------------------------------------------------------------- */
void krk_bv_hdr_encode(unsigned char *o, const krk_bv_hdr_t *h)
{
    unsigned c;
    o[0] = 0xB5; o[1] = (unsigned char)((1 << 4) | (h->mode & 15));
    o[2] = (unsigned char)(h->msg_id >> 8); o[3] = (unsigned char)h->msg_id;
    o[4] = (unsigned char)(h->src_id >> 16); o[5] = (unsigned char)(h->src_id >> 8); o[6] = (unsigned char)h->src_id;
    o[7] = (unsigned char)(h->nunits >> 8); o[8] = (unsigned char)h->nunits;
    o[9] = h->nblocks; o[10] = h->flags; o[11] = h->tick; o[12] = o[13] = 0;
    c = krk_bv_crc16(o, 14);
    o[14] = (unsigned char)(c >> 8); o[15] = (unsigned char)c;
    krk_bv_rs_encode(o, BV_HDR_DATA, BV_HDR_BYTES - BV_HDR_DATA);
}

int krk_bv_hdr_decode(unsigned char *in, krk_bv_hdr_t *h)
{
    unsigned c;
    if (krk_bv_rs_decode(in, BV_HDR_BYTES, BV_HDR_BYTES - BV_HDR_DATA) < 0) return -1;
    c = krk_bv_crc16(in, 14);
    if (in[0] != 0xB5 || in[14] != (c >> 8) || in[15] != (c & 255)) return -1;
    h->version = (unsigned char)(in[1] >> 4); h->mode = in[1] & 15;
    h->msg_id = (unsigned short)((in[2] << 8) | in[3]);
    h->src_id = ((unsigned)in[4] << 16) | (in[5] << 8) | in[6];
    h->nunits = (unsigned short)((in[7] << 8) | in[8]);
    h->nblocks = in[9]; h->flags = in[10]; h->tick = in[11];
    if (h->nblocks > BV_MAX_BLOCKS || h->nblocks != krk_bv_nblocks(h->nunits)) return -1;
    return 0;
}

/* ---- block -------------------------------------------------------------------------------------- */
int krk_bv_nblocks(int nunits) { return (nunits + BV_UNITS_PER_BLOCK - 1) / BV_UNITS_PER_BLOCK; }

void krk_bv_block_encode(unsigned char *o, int idx, int last, int gap, const unsigned char *u, int nu)
{
    int i, n = nu * BV_UNIT_BYTES;
    unsigned c;
    o[0] = (unsigned char)((idx & 0x7F) | (last ? BV_BLOCK_LAST : 0));
    o[1] = (unsigned char)gap;
    for (i = 0; i < BV_UNITS_PER_BLOCK * BV_UNIT_BYTES; i++) o[2 + i] = (i < n) ? u[i] : 0;
    c = krk_bv_crc16(o, 164);
    o[164] = (unsigned char)(c >> 8); o[165] = (unsigned char)c;
    krk_bv_rs_encode(o, BV_BLOCK_DATA, BV_BLOCK_BYTES - BV_BLOCK_DATA);
}

int krk_bv_block_decode(unsigned char *in, int *idx, int *last, int *gap)
{
    unsigned c;
    int r = krk_bv_rs_decode(in, BV_BLOCK_BYTES, BV_BLOCK_BYTES - BV_BLOCK_DATA);
    if (r < 0) return -1;
    c = krk_bv_crc16(in, 164);
    if (in[164] != (c >> 8) || in[165] != (c & 255)) return -2;
    *idx = in[0] & 0x7F; *last = (in[0] & BV_BLOCK_LAST) != 0; *gap = in[1];
    return r;
}

/* ---- TX message assembler ----------------------------------------------------------------------- */
static struct {
    const unsigned char *units, *gaps;
    unsigned char hdr[BV_HDR_BYTES];
    int nunits, nblocks, next;                 /* next: 0 = header, 1..nblocks = block, nblocks+1 = tail */
} tm;
static unsigned char segbuf[BV_BLOCK_BYTES + 2];       /* shared TX/RX segment buffer (the radio is half duplex) */
unsigned char *krk_bv_segbuf(void) { return segbuf; }

void krk_bv_msg_begin(int mode, unsigned msg_id, unsigned src_id, const unsigned char *units, int nunits,
                      const unsigned char *gaps, int flags, int tick)
{
    krk_bv_hdr_t h;
    if (nunits > BV_MAX_BLOCKS * BV_UNITS_PER_BLOCK) nunits = BV_MAX_BLOCKS * BV_UNITS_PER_BLOCK;
    if (nunits < 1) nunits = 1;
    tm.units = units; tm.gaps = gaps; tm.nunits = nunits; tm.nblocks = krk_bv_nblocks(nunits); tm.next = 0;
    h.mode = (unsigned char)mode; h.version = 1; h.flags = (unsigned char)flags; h.tick = (unsigned char)tick;
    h.nblocks = (unsigned char)tm.nblocks; h.msg_id = (unsigned short)msg_id; h.src_id = src_id; h.nunits = (unsigned short)nunits;
    krk_bv_hdr_encode(tm.hdr, &h);
}

const unsigned char *krk_bv_msg_next(int *len)
{
    int b, i, n;
    if (tm.next == 0 || tm.next == tm.nblocks + 1) {
        tm.next++;
        for (i = 0; i < BV_HDR_BYTES; i++) segbuf[i] = tm.hdr[i];
        *len = BV_HDR_BYTES;
        return segbuf;
    }
    if (tm.next > tm.nblocks + 1) return 0;
    b = tm.next++ - 1;
    n = tm.nunits - b * BV_UNITS_PER_BLOCK;
    if (n > BV_UNITS_PER_BLOCK) n = BV_UNITS_PER_BLOCK;
    krk_bv_block_encode(segbuf, b, b == tm.nblocks - 1, tm.gaps ? tm.gaps[b] : 0,
                        tm.units + b * BV_UNITS_PER_BLOCK * BV_UNIT_BYTES, n);
    *len = BV_BLOCK_BYTES;
    return segbuf;
}

/* ---- RX message disassembler -------------------------------------------------------------------- */
static krk_bv_info_t inf;
static unsigned char *rbuf;
static int rcap, rstage, rlast;

const krk_bv_info_t *krk_bv_info(void) { return &inf; }
void krk_bv_rxmsg_stat(unsigned evm_q16, unsigned resync_err) { inf.evm_q16 = evm_q16; inf.resync_err = resync_err; }

void krk_bv_rxmsg_begin(int mode, unsigned char *buf, int cap_bytes)
{
    unsigned char *p = (unsigned char *)&inf;
    unsigned i;
    for (i = 0; i < sizeof inf; i++) p[i] = 0;
    inf.mode = (unsigned char)mode; rbuf = buf; rcap = cap_bytes; rstage = 0; rlast = 0;
}

int krk_bv_rxmsg_expect(void)
{
    if (rstage == 0 || rstage == 2) return BV_HDR_BYTES;
    return rstage == 1 ? BV_BLOCK_BYTES : 0;
}

int krk_bv_rxmsg_segment(unsigned char *seg)
{
    int ev = 0, idx, last, gap, r, i;
    if (rstage == 0 || rstage == 2) {
        krk_bv_hdr_t h;
        if (!inf.have_hdr && krk_bv_hdr_decode(seg, &h) == 0) { inf.hdr = h; inf.have_hdr = 1; ev |= BV_EV_HEADER; }
        if (rstage == 0) { rstage = 1; return ev; }
        rstage = 3; return ev | BV_EV_DONE;
    }
    r = krk_bv_block_decode(seg, &idx, &last, &gap);
    ev |= BV_EV_BLOCK; inf.blocks_seen++;
    if (r >= 0 && idx < BV_MAX_BLOCKS) {
        inf.blocks_ok++; inf.rs_corrected += (unsigned)r; inf.gap[idx] = (unsigned char)gap; inf.ok[idx] = 1;
        if (rbuf && (idx + 1) * BV_UNITS_PER_BLOCK * BV_UNIT_BYTES <= rcap)
            for (i = 0; i < BV_UNITS_PER_BLOCK * BV_UNIT_BYTES; i++)
                rbuf[idx * BV_UNITS_PER_BLOCK * BV_UNIT_BYTES + i] = seg[2 + i];
        if (last) rlast = 1;
    } else inf.blocks_bad++;
    if ((inf.have_hdr && inf.blocks_seen >= inf.hdr.nblocks) || rlast || inf.blocks_seen >= BV_MAX_BLOCKS) rstage = 2;
    return ev;
}
