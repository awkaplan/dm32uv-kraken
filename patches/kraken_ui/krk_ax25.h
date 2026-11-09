/*
 * krk_ax25.h - AX.25 UI frames for APRS and their Bell-202 tone sequence (pure code, no hardware).
 */
#ifndef KRK_AX25_H
#define KRK_AX25_H

typedef unsigned char  u8;
typedef unsigned short u16;

/* Builds an AX.25 UI frame (addresses, control 0x03, PID 0xF0, info, FCS) into out.
 * calls: "DEST", "SRC-SSID" and up to 6 digipeater path entries ("WIDE1-1"); path may be 0.
 * Returns the frame length (FCS included), or -1 if it does not fit or a callsign is invalid. */
int krk_ax25_frame(u8 *out, int max, const char *dest, const char *src,
                   const char *const *path, int npath, const char *info);

/* CRC-16/X.25 (the AX.25 FCS): reflected 0x1021, init 0xFFFF, final XOR 0xFFFF. */
u16 krk_ax25_fcs(const u8 *p, int n);

/* Converts a frame to the transmitted tone sequence: pre flags (0x7E), the frame LSB-first with
 * a 0 stuffed after five 1s, post flags; then NRZI (0 = change tone, 1 = keep). Writes one tone
 * per bit as packed bits in out (bit i = (out[i/8] >> (i%8)) & 1; 0 = mark 1200 Hz, 1 = space
 * 2200 Hz), starting from mark. Returns the bit count, or -1 if max_bits is too small. */
int krk_ax25_tones(const u8 *frame, int len, int pre_flags, int post_flags, u8 *out, int max_bits);

#endif
