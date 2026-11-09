/*
 * krk_vframe.h - voice-frame I/O layer (F9 parrot now, F13 burst voice later).
 *
 * The DM-32UV's vocoder runs on the CPU in two IRAM tasks (docs/parrot-tx.md):
 *   TX  task prio 16 (entry IRAM 0x2e5a0): every 60 ms it packs one 27-byte burst (3 AMBE+2 frames,
 *       72 channel bits each) into 0x49b18 and copies it to the modem window with `bsr 0x172f8`
 *   RX  task prio 15 (entry IRAM 0x2ec30): every 60 ms it reads the received burst from the modem
 *       window into 0x46b38 with `bsr 0x172cc` (site IRAM 0x2ed64)
 * Five manifest splices (parrot_tx1..4, parrot_rx; IRAM copy in flash sector 0x030DE000) redirect those
 * calls to krk_vf_tx_hook / krk_vf_rx_hook, which call the stock copy and add the hooks below.
 *
 * An owner (the parrot now; burst voice later) uses three calls:
 *   krk_vf_rx_sink(fn)            fn(frame27) for EVERY received burst while set (0 = off)
 *   krk_vf_tx_begin(fn, ...)      substitute each TX burst with fn(dst27, idx) until it says LAST
 *   krk_vf_tx_end()               stop substituting
 *
 * THREADING (no locks; every cross-task field is volatile and has ONE writer per field):
 *   krk_vf_rx_hook   runs in the RX task (prio 15). It calls the sink, so a sink must be tiny, never
 *                    block, never call stock code that pends, and uses < ~100 bytes of the 1280-byte
 *                    task stack. Counters rx_calls/rx_sunk are written only there.
 *   krk_vf_tx_hook   runs in the TX task (prio 16): same rules for a provider. tx_* counters and
 *                    tx_idx/end_idx are written only there (and reset by tx_begin while flags == 0).
 *   krk_vf_tx_begin/end, krk_vf_rx_sink, krk_vf_service
 *                    run in the menu or maintenance task. They fill every field first and set the
 *                    activating word (flags / rx_sink) LAST; a hook that sees the old value just
 *                    passes the stock bytes through. A hook that is preempted between two reads of a
 *                    field can see a half-updated session for one burst; that is harmless (one
 *                    burst of silence or stock audio), which is why no lock is used.
 *   The hooks never run while IRQs are masked and never take a mutex; the only stock call from a
 *   hook is OSFlagPost_wrapper_set (a non-blocking event-flag post) to release PTT.
 *
 * SAFETY, in the hook itself (independent of the owner): a session is abandoned when it is older than
 * max_ticks (also catches a stale session after a warm reset: the tick counter restarts, so the age
 * wraps to a huge value), when the provider returns NONE, past max_bursts, or after VF_GRACE bursts
 * beyond the end. After the end every burst is a silence frame and PTT release is re-posted every
 * 8th burst (480 ms); a QUIET session (abort/unkey/stuck) never gives up while TX voice stays up (30 s cap). A pre-init (power-on noise) layer is inert: it needs the magic word.
 */
#ifndef KRK_VFRAME_H
#define KRK_VFRAME_H

#include "krk.h"

#define KRK_VF_LEN   27             /* bytes per burst: 3 x 72 bits */
#define KRK_VF_TAP   4              /* stock TX bursts kept for the format check */
#define KRK_VF_BIG_BYTES 5616       /* kraken_bigbuf = 5632 B; 208 bursts = 12.48 s */
#define KRK_VF_BIG_FRAMES 208

/* provider return values */
#define KRK_VF_NONE  0              /* nothing more: the layer sends silence and releases PTT */
#define KRK_VF_FRAME 1              /* dst holds the burst to send */
#define KRK_VF_LAST  2              /* dst holds the last burst; the layer releases PTT after it */
#define KRK_VF_PASS  3              /* leave the stock (microphone) burst on the air */

typedef int (*krk_vf_tx_fn)(u8 *dst, u32 idx);
typedef void (*krk_vf_rx_fn)(const u8 *frame);

/* flags */
#define KRK_VF_SUBST 1              /* substitute (tx_fn set) */
#define KRK_VF_TAPON 2              /* copy the first KRK_VF_TAP stock bursts of the session to tap[] */

struct krk_vf {
    u32 magic;
    u32 stamp;                      /* tick of the last krk_vf_service (reboot detection) */
    volatile u8 flags;
    volatile u8 done;               /* hook ended the session by itself (grace over) */
    volatile u8 tap_n;
    volatile u8 quiet;              /* silence-only session until TX voice is down (krk_vf_tx_quiet) */
    u32 t_quiet;                    /* tick quiet was first set */
    krk_vf_tx_fn volatile tx_fn;
    krk_vf_rx_fn volatile rx_sink;
    u32 t_begin, max_ticks, max_bursts;
    volatile u32 tx_idx;            /* bursts seen since tx_begin */
    volatile u32 end_idx;           /* first burst index that is silence (0xffffffff = not ended) */
    /* diagnostics (only ever written by the hook that counts them) */
    volatile u32 tx_calls;          /* every TX hook call since init: the encoder task is alive */
    volatile u32 tx_subst;          /* bursts replaced by the provider */
    volatile u32 tx_silence;        /* silence bursts sent after the end */
    volatile u32 tx_pass;           /* stock bursts passed in a session */
    volatile u32 rx_calls;          /* every RX hook call since init */
    volatile u32 rx_sunk;           /* bursts delivered to a sink */
    volatile u32 rel_posts;         /* PTT-release posts made by the hook */
    u8 tap[KRK_VF_TAP][KRK_VF_LEN];
};

extern struct krk_vf krk_vf;

void krk_vf_service(void);          /* init if needed, stamp; call from the periodic hook and screen enter */
void krk_vf_tx_begin(krk_vf_tx_fn fn, u32 max_bursts, u32 max_ticks, int tap);
void krk_vf_tx_end(void);
void krk_vf_tx_quiet(void);         /* abort/unkey/stuck: from now on silence frames only (never the microphone), and the
                                       session stays alive while TX voice (0x3bd50 bit 0) is up; ends with krk_vf_tx_end */
void krk_vf_rx_sink(krk_vf_rx_fn fn);
void krk_vf_release(void);          /* post the PTT-release event (idempotent) */
void krk_vf_press(void);            /* post the PTT-press event */
void krk_vf_silence(u8 *dst);       /* the 27-byte silence burst (krk_vframe.c documents its source) */
u32 krk_vf_ticks(void);             /* stock 100 Hz tick (0x426d0) */
/* the shared F9/F13 buffer: owner 0 = free; claim returns the 5616-byte buffer or 0 if another owns it */
u8 *krk_vf_big_claim(u8 owner);
void krk_vf_big_release(u8 owner);

/* Warm-reset detection shared by the features that keep state in patch SRAM across a reboot (review fix 9).
 * *stamp is the caller's own last-seen tick, refreshed here. The stock tick counter restarts at 0 on every boot,
 * so a reboot shows as (a) the counter below the stamp, or (b) a stale stamp: nobody refreshed it for 30 s although
 * the maintenance task calls the owners once a second, which catches a run that died early (counter now above its
 * old stamp). Returns 1 when the state must be treated as foreign. */
#define KRK_REBOOT_GAP 3000u
static inline int krk_rebooted(u32 *stamp)
{
    u32 now = *(volatile u32 *)0x000426d0;
    int r = now < *stamp || now - *stamp > KRK_REBOOT_GAP;

    *stamp = now;
    return r;
}

/* splice targets (manifest parrot_tx*, parrot_rx): same signature as the stock copy routines */
void krk_vf_tx_hook(u32 off, u8 *src, u32 n);
void krk_vf_rx_hook(u32 off, u8 *dst, u32 n);

#endif
