/*
 * krk_parrot_sm.h - F9 parrot state machine as a PURE function (no hardware access, no globals), so
 * tests/test_parrot.py can drive it on the host with scripted inputs. krk_parrot.c gathers the inputs
 * from the radio, calls ps_step once per screen tick (100 ms), and executes the outputs.
 * Time is the stock 100 Hz tick (60 ms = 6 ticks).
 */
#ifndef KRK_PARROT_SM_H
#define KRK_PARROT_SM_H

#ifndef DM32_PATCH_API_H
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#endif

enum ps_state { PS_OFF, PS_IDLE, PS_REC, PS_HOLD, PS_KEYING, PS_SEND, PS_UNKEY, PS_COOL };

/* reasons shown on screen / UART (ps_reason_name) */
enum ps_reason {
    PR_NONE, PR_NOT_DIGITAL, PR_ENCRYPTED, PR_TX_FORBID, PR_BUSY, PR_INHIBIT, PR_CPS, PR_OWN_ID,
    PR_NO_CAP, PR_ABORT, PR_NO_TXUP, PR_STUCK, PR_STOCK_END, PR_DONE, PR_DUAL, PR_N
};

#define PS_HOLD_TICKS   150         /* quiet time after the call end before keying (1.5 s) */
#define PS_KEY_TICKS    300         /* TX voice must be up within 3 s of the press */
#define PS_COOL_TICKS   300         /* hold-off after an echo (anti-loop, 3 s) */
#define PS_REL_TICKS    50          /* release re-post period (500 ms) */
#define PS_MIN_FRAMES   5           /* shorter captures (0.3 s) are not echoed */
#define PS_EXTRA_TICKS  100         /* TX budget beyond the burst time (1 s) */
#define PS_STUCK_TICKS  100         /* past the deadline + this: the substitution goes silent by force */
#define PS_WATCH_TICKS  150         /* "maybe keyed" window: TX voice must stay down this long (and this long after the last release post / press) before the watch ends */

struct ps_ctx {
    u8 state;
    u8 reason;                      /* last reason (enum ps_reason) */
    u32 t_state;                    /* tick at which the state was entered */
    u32 deadline;                   /* tick after which the release is re-posted every PS_REL_TICKS */
    u32 t_rel;                      /* tick of the last release post (watchdog and step share it) */
    u8 watch;                       /* a press was posted and TX is not yet proven down for PS_WATCH_TICKS (review fix 1) */
    u8 pad[3];
    u32 t_press;                    /* tick of the press post */
    u32 t_clear;                    /* tick since which TX voice was seen continuously down (0 = unknown / seen up) */
};

struct ps_in {
    u32 now;
    u32 budget;                     /* TX budget in ticks: bursts * 6 + PS_EXTRA_TICKS */
    u8 armed;
    u8 frames_ok;                   /* a capture of >= PS_MIN_FRAMES exists */
    u8 rec;                         /* a received call is being captured */
    u8 fresh;                       /* a call ended since the last step (covers calls shorter than one tick) */
    u8 play;                        /* user asked to transmit the last capture now */
    u8 abort;                       /* a key was pressed */
    u8 block;                       /* first failed safety gate (enum ps_reason), 0 = all pass */
    u8 tx_up;                       /* stock TX voice is up (0x3bd50 bit 0) */
    u8 ended;                       /* the layer reached the last burst (release posted) */
};

struct ps_out {
    u8 press;                       /* post PTT press */
    u8 release;                     /* post PTT release */
    u8 begin;                       /* start substituting (krk_vf_tx_begin) before the press */
    u8 quiet;                       /* substitute silence only from now on (krk_vf_tx_quiet); the real end comes from ps_watch */
};

void ps_init(struct ps_ctx *c);
void ps_step(struct ps_ctx *c, const struct ps_in *in, struct ps_out *o);
/* The press-to-proven-down watch, for the screen step AND the maintenance tick (independent of the state
 * machine state): bit0 = post release now, bit1 = go silent (krk_vf_tx_quiet), bit2 = watch over, end the
 * substitution (krk_vf_tx_end). Only touches c->t_rel/t_clear/watch. Safe to call from any task at any time. */
int ps_watch(struct ps_ctx *c, u32 now, u8 tx_up);
const char *ps_state_name(u32 s);
const char *ps_reason_name(u32 r);

#endif
