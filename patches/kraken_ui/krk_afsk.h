/*
 * krk_afsk.h - Bell 202 AFSK (1200 baud, 1200/2200 Hz) demodulator + HDLC/AX.25 deframer for
 * 8 kHz signed 16-bit samples. Pure integer code, no hardware dependencies: host-tested by
 * tests/test_afsk.py against synthesized audio (noise, twist, frequency error, DC offset).
 */
#ifndef KRK_AFSK_H
#define KRK_AFSK_H

#define AFSK_MAX_FRAME 330          /* AX.25 UI with 8 path entries + 256 info + FCS */
#define AFSK_LINES     4
#define AFSK_LINE_LEN  60

void krk_afsk_reset(void);
/* Feeds n samples. Returns how many complete, FCS-valid frames were decoded in this call. */
int krk_afsk_feed(const short *x, int n);
/* Frames decoded so far (total), and the text lines of the last AFSK_LINES ("SRC>DST,PATH:INFO",
 * truncated); line 0 is the newest. Returns the number of valid lines. */
unsigned krk_afsk_frames(void);
int krk_afsk_line(int i, char *out /* >= AFSK_LINE_LEN + 1 */);
/* Called for every decoded frame with its line and the frame without its FCS (len bytes); a weak empty default in krk_afsk.c,
 * in the image the F18 APRS Messages hand-off (krk_periodic.c, to blob 4's krk_aprs_msg.c). */
void krk_aprs_msg_frame(const char *line, const unsigned char *f, int len);
/* Diagnostics: last decision energy ratio proxy and current bit-PLL lock quality (0..255). */
int krk_afsk_quality(void);
/* HDLC flags (0x7E) seen and frames rejected by the FCS, since reset. */
void krk_afsk_stats(unsigned *flags, unsigned *bad_fcs);
/* Tone statistics since the last call (then cleared): samples seen, samples judged mark, and
 * mark/space transitions. Clean 1200-baud AFSK has <= 1200 transitions/s; flags give 300/s. */
void krk_afsk_tone_stats(unsigned *samples, unsigned *mark, unsigned *transitions);
/* Twist compensation: energy weight of the space (2200 Hz) correlator, 1..16 (default 2; the
 * DM-32 receives 2200 Hz 5-10 dB below 1200 Hz). */
void krk_afsk_set_space_gain(int g);
/* Radio profile (pre-emphasis y = v - 0.875 v[n-1] + the receive-chain compensation in krk_afsk.c); off by default. */
void krk_afsk_set_preemph(int on);
int krk_afsk_get_preemph(void);
int krk_afsk_get_space_gain(void);
/* Radio profile variant: 2 = preset B (window 8, fitted on capture16: the DM-32's own FM Send RAM TX);
 * anything else = the channel-model fit. Only matters with the radio profile on. */
void krk_afsk_set_preset(int p);
int krk_afsk_get_preset(void);
/* Q14 sine table, 64 entries (shared with the self-test synthesizer). */
extern const short krk_afsk_sintab[64];

#endif
