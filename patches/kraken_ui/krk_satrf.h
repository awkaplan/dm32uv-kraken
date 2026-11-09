/*
 * krk_satrf.h - F10 radio side: UTC source, the Doppler track and the 1 Hz tick (krk_satrf.c), used by the
 * Satellites screen (krk_satscr.c). Design and addresses: docs/satellite-radio.md; as built: docs/satellite-mode.md.
 */
#ifndef KRK_SATRF_H
#define KRK_SATRF_H

#include "krk.h"

typedef signed char sat_s8;                     /* dm32_patch_api.h has only the unsigned types */
typedef short sat_s16;
typedef int sat_s32;

#define SATR_STEP   10          /* seconds between track samples */
#define SATR_NMAX   181         /* 30 min = 1800 s: samples 0..180 */
#define SATR_CAP_S  1800        /* hard cap on one tracking session */
#define SATR_HYST   50          /* RX retune hysteresis, 10 Hz units (500 Hz) */
#define SATR_CLAMP  1500        /* |word - nominal| <= 15 kHz, 10 Hz units */
#define SATR_MAGIC  0x53415431u /* "SAT1": patch SRAM is not zeroed at power-up */

enum { ST_IDLE, ST_BUILD, ST_ACTIVE, ST_RESTORE };
enum { WHY_NONE, WHY_USER, WHY_END, WHY_CAP, WHY_LOST, WHY_RANGE };
enum { TS_NONE, TS_RMC, TS_RTC };

struct satr {
    u32 magic;
    u8 state, stop_req, fake, tx_blocked;
    u8 why, tsrc, tx_dead, owned;
    u8 sat;                     /* index of the tracked satellite */
    u8 rx_pend;                 /* RX word written, chip retune skipped (PTT/TX): apply at the next free pass */
    u16 n;                      /* samples in ppb[] */
    sat_s32 trim_hz;                /* user trim, both words, same sign */
    u32 t0;                     /* unix time of ppb[0] */
    u32 base_unix, base_tick;   /* time = base_unix + (OSTickCtr - base_tick) / 100 */
    u32 sync_tick, armed_tick;
    u32 nom_rx, nom_tx;         /* satellite frequencies, 10 Hz units */
    u32 saved_rx, saved_tx;     /* the channel's own words, restored on stop */
    u32 owned_rx, owned_tx;     /* what we wrote last */
    u32 cur_rx, cur_tx;         /* last targets (display) */
    u32 n_tick, n_skip, n_retune;
    u16 rssi_pk;                /* peak-hold of satr_rssi() while tracking (0 = unseen); reset at arm */
    sat_s32 last_ppb;
    u32 stamp;                  /* krk_rebooted() stamp (review fix 9) */
    sat_s16 ppb[SATR_NMAX];         /* range-rate / c in ppb, positive = receding */
};
extern struct satr R;

void satr_ensure(void);                         /* initialises R if its magic is absent */
u32 satr_now(void);                             /* unix UTC seconds (valid after a successful satr_sync) */
int satr_sync(int allow_rtc);                   /* RMC else (allow_rtc) RTC; returns TS_*; foreground only for RTC */
int satr_tz_min(void);                          /* stock time-zone setting, minutes east of UTC */
int satr_fix(int *lat, int *lon);               /* 1 live GPS fix, 2 last one seen, 0 none (1e-4 arc-minute) */
void satr_civil(u32 unix_s, int *y, int *mo, int *d, int *h, int *mi, int *s);
u32 satr_unix(int y, int mo, int d, int h, int mi, int s);
u32 satr_rx_for(int ppb);                       /* corrected words for a ppb value (nominal, trim included) */
u32 satr_tx_for(int ppb);
int satr_check(int need_tx);                    /* 0 = may arm/test, else an error code (satr_err) */
const char *satr_err(int code);
void satr_fake_track(void);                     /* fills ppb[] with a time-accelerated pass shape */
int satr_arm(int sat, u32 dl_hz, u32 ul_hz, u32 t0, int fake);   /* ppb[0..n) ready: capture words, go ACTIVE */
void satr_stop(void);                           /* request stop + restore at the next tick */
void satr_cancel_build(void);
int satr_apply_rx(u32 rx10);                    /* self-test: write RX word + stock retune; 0 ok, -1 busy */
void satr_capture(u32 dl_hz);                   /* self-test: remember the channel's words + the nominal RX */
int satr_restore_now(void);                     /* self-test: put the channel's words back (guarded like restore()) */
unsigned satr_rssi(void);
void krk_sat_tick(void);                        /* 1 Hz, maintenance task, integer only */

#endif
