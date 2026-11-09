/*
 * krk_bv.h - F13 "BV1" compressed burst voice: D8PSK/DQPSK software modem for the 8 kHz FM Send/Receive
 * RAM audio path + burst container + Reed-Solomon FEC. Design: docs/burst-voice.md. Pure integer code
 * (no float, no libc, no malloc, no .data), host-tested by tests/test_bv.py. Half duplex: one burst in flight, TX and RX share
 * the segment buffer; the receiver ignores its input while a transmission is active.
 *
 * Files:  krk_bv_modem.c  TX modulator / RX demodulator (symbol level, 8 kHz PCM in/out)
 *         krk_bv_frame.c  CRC16, RS(182,166) / RS(24,16) over GF(256), header and block codecs, the
 *                         message assembler (TX segment stream) and disassembler (RX events, unit buffer)
 *         krk_bv_sound.c  channel "sounding" probe signal for the hardware gate test H1
 *
 * Air format (all PSK, 2000 baud, 4 samples/symbol, carrier 1700 Hz, RRC alpha 0.35, 33 taps; 516 symbols = 258 ms fixed overhead):
 *   CW 64 sym (energy trigger + gain) | SYNC 64 sym (differential QPSK, one word per mode = the mode sense)
 *   | TRAIN 192 sym (known QPSK, the last 96 of a fast burst use all 8 phases; equalizer + carrier training)
 *   | n x [RESYNC 8 sym (known) | segment]   segments: header(24 B, DQPSK), blocks(182 B, mode),
 *                                            tail header copy (24 B, DQPSK)
 *   | PAD 8 sym | FLUSH 12 zero symbols
 */
#ifndef KRK_BV_H
#define KRK_BV_H

#define BV_UNIT_BYTES       27      /* one stock AMBE+2 unit (60 ms) */
#define BV_UNITS_PER_BLOCK  6
#define BV_BLOCK_DATA       166     /* idx|last, gap, 6 x 27, CRC16 */
#define BV_BLOCK_BYTES      182     /* RS(182,166), t = 8 */
#define BV_HDR_DATA         16
#define BV_HDR_BYTES        24      /* RS(24,16), t = 4 */
#define BV_MAX_BLOCKS       40
#define BV_MODE_ROBUST      0       /* DQPSK 2000 bd, 4.0 kbps raw */
#define BV_MODE_FAST        1       /* D8PSK 2000 bd, 6.0 kbps raw */
#define BV_BLOCK_LAST       0x80    /* flag in the block index byte */
#define BV_FLAG_TRIMMED     1       /* header flags */
#define BV_FLAG_STRIPPED    2
#define BV_FLAG_LASTPASS    4
#define BV_FLAG_LAYOUT_SHIFT 3      /* bits 3-5: krk_ambe layout of a STRIPPED payload (KRK_AMBE_L_*), docs/burst-voice.md 10.4 */
#define BV_FLAG_PARITY_SHIFT 6      /* bits 6-7: C0 parity convention (krk_ambe_set_parity) */

/* RX event mask returned by krk_bv_rx_feed() (OR of everything that happened in the call). */
#define BV_EV_SYNC     1            /* sync word found, mode known (krk_bv_rx_info().mode) */
#define BV_EV_HEADER   2            /* a valid header was decoded */
#define BV_EV_BLOCK    4            /* a block was processed (ok or bad) */
#define BV_EV_DONE     8            /* the burst is complete */
#define BV_EV_ABORT    16           /* lost lock / corrupt burst, receiver re-armed */

typedef struct {
    unsigned char mode;             /* BV_MODE_* */
    unsigned char version;
    unsigned char flags;
    unsigned char tick;             /* ms per unit / 4 (60 ms -> 15) */
    unsigned char nblocks;
    unsigned short msg_id;
    unsigned src_id;           /* 24 bit */
    unsigned short nunits;
} krk_bv_hdr_t;

/* ---- framing (krk_bv_frame.c) -------------------------------------------------------------------- */
unsigned krk_bv_crc16(const unsigned char *p, int n);                  /* CRC-16/CCITT-FALSE */
/* Systematic shortened RS over GF(256) (poly 0x11D, roots a^0..a^(nroots-1)): cw[0..k-1] = data in,
 * cw[k..k+nroots-1] = parity out. */
void krk_bv_rs_encode(unsigned char *cw, int k, int nroots);
/* Corrects up to nroots/2 byte errors in place. Returns the number of corrected bytes, or -1. */
int krk_bv_rs_decode(unsigned char *cw, int n, int nroots);
void krk_bv_hdr_encode(unsigned char *out /*24*/, const krk_bv_hdr_t *h);
int krk_bv_hdr_decode(unsigned char *in /*24, corrected in place*/, krk_bv_hdr_t *h);   /* 0 ok, -1 bad */
/* in_units: units present in this block (0..6); the rest of the 6 slots is zero-padded. */
void krk_bv_block_encode(unsigned char *out /*182*/, int idx, int last, int gap, const unsigned char *units,
                         int in_units);
/* Returns RS corrections (>= 0) with idx, last and gap set and units at in + 2, -1 = RS failure, -2 = CRC. */
int krk_bv_block_decode(unsigned char *in /*182, corrected in place*/, int *idx, int *last, int *gap);
int krk_bv_nblocks(int nunits);

/* Message assembler (TX). units: nunits x 27 B; gaps: per block silence ticks (60 ms) before the block
 * (nblocks bytes) or 0. Segments come out as: header, blocks..., header copy. */
void krk_bv_msg_begin(int mode, unsigned msg_id, unsigned src_id, const unsigned char *units, int nunits,
                      const unsigned char *gaps, int flags, int tick);
/* Next segment (valid until the next call) or 0 at the end. *len = 24 or 182. */
const unsigned char *krk_bv_msg_next(int *len);

/* Message disassembler (RX). Received units are stored at buf + 27 * (6 * block + k). */
void krk_bv_rxmsg_begin(int mode, unsigned char *buf, int cap_bytes);
/* Length of the next expected segment (24 header/tail, 182 block), 0 = message finished. */
int krk_bv_rxmsg_expect(void);
/* seg: the raw segment bytes (corrected in place). Returns BV_EV_* bits. */
int krk_bv_rxmsg_segment(unsigned char *seg);

/* Shared TX/RX segment scratch (BV_BLOCK_BYTES + 2 bytes). */
unsigned char *krk_bv_segbuf(void);

typedef struct {
    krk_bv_hdr_t hdr;               /* valid when have_hdr */
    unsigned char have_hdr;
    unsigned char blocks_ok, blocks_bad, blocks_seen;
    unsigned rs_corrected;          /* total corrected bytes in good blocks */
    unsigned char mode;
    unsigned char gap[BV_MAX_BLOCKS];
    unsigned char ok[BV_MAX_BLOCKS];/* 1 = block decoded (RS+CRC), 0 = lost/bad */
    unsigned evm_q16;               /* mean decision error power / symbol power, Q16 (smaller = better) */
    unsigned resync_err;            /* mismatching RESYNC symbols so far */
} krk_bv_info_t;
const krk_bv_info_t *krk_bv_info(void);
void krk_bv_rxmsg_stat(unsigned evm_q16, unsigned resync_err);   /* modem -> info */

/* ---- modem (krk_bv_modem.c) ---------------------------------------------------------------------- */
/* TX: nothing is allocated; units/gaps are read while the burst is generated and must stay valid.
 * krk_bv_tx_start() abandons any burst in progress and resets the receiver (half duplex). */
void krk_bv_tx_start(int mode, unsigned msg_id, unsigned src_id, const unsigned char *units, int nunits,
                     const unsigned char *gaps, int flags);
/* Carrier frequency, multiples of 100 Hz in 1500..2100 (default 1700; 80-entry table stride). Both ends must agree; call before tx_start / rx_begin. */
void krk_bv_set_carrier(int hz);
void krk_bv_tx_level(int rms, int peak);            /* PCM RMS (default 4000) and hard clip (default 30000) */
/* Fills the next 256 samples. Returns 1 while more blocks are needed, 0 once this block was the last one
 * (the unused tail is silence). Always writes 256 samples. */
int krk_bv_tx_fill(short *out);
int krk_bv_tx_active(void);
void krk_bv_tx_stop(void);                          /* abandon a burst (re-opens the receiver) */
/* Total samples of a burst (for the airtime estimate), same arguments as krk_bv_tx_start. */
unsigned krk_bv_tx_samples(int mode, int nunits);

/* RX: krk_bv_rx_begin() arms the receiver and sets the payload buffer (nunits x 27 B, e.g. the shared
 * 5632 B buffer). Feed any number of samples (256 per FM RX RAM half); idle cost is under 0.5 MIPS. */
void krk_bv_rx_begin(unsigned char *buf, int cap_bytes);
int krk_bv_rx_feed(const short *x, int n);
#define BV_RX_GAP_TICKS 6                               /* OS_TICKS (10 ms): a 32 ms FM receive RAM half normally follows after 3-4 */
int krk_bv_rx_gap(unsigned now, unsigned prev);        /* nonzero: more than BV_RX_GAP_TICKS since the previous half, one was lost */
int krk_bv_rx_state(void);                          /* 0 idle, 1 searching for sync, 2 receiving */

/* ---- sounding probe (krk_bv_sound.c), hardware test H1 -------------------------------------------- */
/* One key press, about 4.9 s: 1 kHz tone 0.32 s | gap | linear chirp 200-3800 Hz 2.0 s (peak 8000) | gap |
 * chirp 300-3300 Hz 0.8 s at -6 dB | same at -12 dB | gap | a 12-unit fast-mode BV burst with the fixed payload below.
 * Every stage is a whole number of 256-sample blocks. tools/bv_sounding_analysis.py regenerates the reference. */
#define BV_SOUND_UNITS 12
void krk_bv_sound_payload(unsigned char *out /* 12 * 27 bytes */);
void krk_bv_sound_start(unsigned char *payload /* 324 bytes of scratch, filled by this call; must stay valid */);
/* Next 256 samples of the probe; returns 0 once this block was the last one. */
int krk_bv_sound_fill(short *out);
extern const short krk_bv_cos256[256];     /* Q14 cosine, 256 entries per turn */

#endif
