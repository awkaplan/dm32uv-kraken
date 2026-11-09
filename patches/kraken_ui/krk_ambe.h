/*
 * krk_ambe.h - F13 AMBE+2 (DMR half rate, "3600x2450") FEC stripping and silence trim for the burst voice container.
 * Design and measured gains: docs/burst-voice.md section 10. Pure integer code (no float, no libc, no malloc, no .data),
 * host-tested by tests/test_ambe.py.
 *
 * One AMBE+2 voice frame (20 ms) = 72 bits = C0 (24 bit, extended Golay(24,12)) | C1 (23 bit, Golay(23,12) XOR a PN mask
 * seeded by C0's 12 data bits) | C2 (11 bit, uncoded) | C3 (14 bit, uncoded) = 49 information bits + 23 parity bits.
 * One stock unit = 27 bytes = 3 frames = 60 ms. The bit layout of the 27 bytes is NOT confirmed on hardware, so every
 * function takes a layout and krk_ambe_detect_layout() picks it from real data.
 *
 * Sources (all public): mbelib (ambe3600x2450.c, mbe_eccAmbe3600x2450*, mbe_demodulateAmbe3600x2450Data), DSD (dmr_const.h rW/rX/rY/rZ),
 * MMDVM DMR_SILENCE_DATA (B9 E8 81 52 61 73 00 2A 6B). The test checks that these three agree bit-exactly.
 */
#ifndef KRK_AMBE_H
#define KRK_AMBE_H

#define KRK_AMBE_UNIT_BYTES   27
#define KRK_AMBE_INFO_BYTES   7         /* 49 bits MSB first, the unused 7 low bits of byte 6 are zero */
#define KRK_AMBE_INFO_BITS    49

/* Layouts of the 27-byte unit (216 bits = 3 frames of 72 bits; the 48-bit sync/EMB field is NOT part of the unit). */
#define KRK_AMBE_L_RAW        0         /* no stripping: the unit bytes are carried as they are */
#define KRK_AMBE_L_AIR        1         /* L1: 3 consecutive frames in DMR air order (DSD rW/rX/rY/rZ dibit interleave), bytes MSB first */
#define KRK_AMBE_L_PLAIN      2         /* L2: 3 consecutive de-interleaved frames C0|C1|C2|C3, MSB first */
#define KRK_AMBE_L_AIR_LSB    3         /* L1 with each byte read LSB first */
#define KRK_AMBE_L_PLAIN_LSB  4         /* L2 with each byte read LSB first */
#define KRK_AMBE_NLAYOUTS     5

/* C0 24th bit (extended Golay parity): 0 = even parity over the 24 bits (ETSI/DSD convention, verified against MMDVM's
 * silence frame), 1 = odd, 2 = not checked and written as 0. Default 0. */
void krk_ambe_set_parity(int mode);

/* ---- Golay ---- g(x) = x^11+x^10+x^6+x^5+x^4+x^2+1 (0xC75), systematic: codeword = data << 11 | remainder */
unsigned krk_golay23_encode(unsigned data12);                   /* 23-bit codeword */
int krk_golay23_decode(unsigned *cw23);                         /* corrects in place, returns 0..3 corrected bits (perfect code: never fails) */
unsigned krk_golay24_encode(unsigned data12);                   /* 23-bit codeword << 1 | parity bit (see set_parity) */
int krk_golay24_decode(unsigned *cw24, unsigned *data12);       /* corrected word in place; errors 0..3, or -1 for 4 detected errors */

/* ---- one frame ---- cw[0..3] = C0 (24 bit), C1 (23), C2 (11), C3 (14); bit 0 = last transmitted bit of the codeword */
unsigned krk_ambe_pn_mask(unsigned c0_data12);                  /* 23-bit mask for C1 (bit 22 = pr[1] >> 15 ... bit 0 = pr[23] >> 15) */
void krk_ambe_air_to_cw(const unsigned char *fr9, unsigned *cw);        /* 9 bytes = 36 dibits in air order, MSB first */
void krk_ambe_cw_to_air(const unsigned *cw, unsigned char *fr9);
void krk_ambe_plain_to_cw(const unsigned char *fr9, unsigned *cw);     /* 72 bits C0|C1|C2|C3 MSB first */
void krk_ambe_cw_to_plain(const unsigned *cw, unsigned char *fr9);
/* cw -> 49 info bits (Golay correction on C0 and C1). Returns the number of corrected bits (>= 0) or -1 when C0 had 4 errors
 * (info is then filled from the uncorrected bits). *c0err / *c1err (optional) get the per-codeword counts. */
int krk_ambe_cw_to_info(const unsigned *cw, unsigned char *info7, int *c0err, int *c1err);
void krk_ambe_info_to_cw(const unsigned char *info7, unsigned *cw);     /* re-encode: Golay, PN, C2, C3 */
int krk_ambe_info_is_silence(const unsigned char *info7);       /* b0 (7 bit pitch index = info bits 0-5 and 48) is 124 or 125 */
void krk_ambe_silence_info(unsigned char *info7);               /* canonical silence: F8 01 A9 9F 8C E0 80 */

/* ---- 27-byte unit <-> frames, per layout (frame k = 0..2) ---- */
void krk_ambe_unit_get(const unsigned char *unit, int k, int layout, unsigned *cw);
void krk_ambe_unit_put(unsigned char *unit, int k, int layout, const unsigned *cw);

/* Layout detection: per-mille of frames whose C0 Golay word is valid without correction (23-bit part only). */
int krk_ambe_layout_score(const unsigned char *units, int n, int layout);
/* Best layout 1..4 (score >= 250 per mille, >= 2 valid frames, valid count >= 2 * runner-up + 1) or KRK_AMBE_L_RAW.  */
int krk_ambe_detect_layout(const unsigned char *units, int n);
int krk_ambe_detect_score(void);                                /* per-mille of the last krk_ambe_detect_layout() winner */
/* Majority vote of C0's 24th bit over frames whose 23-bit part is valid: 0 even, 1 odd, 2 constant 0 (or no valid frames: 0). */
int krk_ambe_detect_parity(const unsigned char *units, int n, int layout);

/* ---- container payload ---- */
typedef struct {
    unsigned frames, silent, bad;       /* frames seen, silence frames, frames with 4 C0 errors */
    unsigned c0_fixed, c1_fixed;        /* corrected bits in C0 / C1 over all frames (link quality for F3) */
} krk_ambe_stats_t;

/* Packs n units (n <= 255) into the stripped payload:
 *   byte 0 = n; if trim: run lengths of the 3n frames, alternating non-silent / silent starting with non-silent (a run is bytes
 *   of 255 ... then one byte < 255, sum = run length), ending when they total 3n; then the 49-bit infos of the non-silent frames
 *   (all frames if !trim) back to back MSB first, the last byte zero padded.
 * layout RAW: out = the n * 27 bytes unchanged (no header; trim ignored). Returns bytes written or -1 (cap too small / bad n). */
int krk_ambe_pack(const unsigned char *units, int n, int layout, int trim, unsigned char *out, int cap, krk_ambe_stats_t *st);
/* Inverse. Returns the number of units written (<= cap_units) or -1 on a malformed payload. Silence frames come back as the canonical
 * silence frame; every other frame is re-encoded, bit-exact for any valid frame. RAW: len must be a multiple of 27. */
int krk_ambe_unpack(const unsigned char *in, int len, int layout, int trim, unsigned char *units, int cap_units);
/* Stripped payload size for n units with ns silent frames (trim) - ns = 0 without trim. Run-length bytes are not included. */
int krk_ambe_packed_bits(int n, int ns);

#endif
