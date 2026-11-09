/*
 * krk_parrot_sm.c - the F9 parrot state machine (pure; see krk_parrot_sm.h).
 *
 *   OFF --arm--> IDLE --call heard--> REC --call ended--> HOLD --1.5 s quiet--> KEYING --TX up--> SEND
 *   SEND --last burst--> UNKEY --TX down--> COOL (3 s hold-off) --> IDLE
 * "Play last" jumps IDLE -> (gates) -> KEYING. Every gate failure, abort or timeout lands in IDLE, COOL or
 * UNKEY with a reason; a release is posted on every path that may have pressed PTT.
 * Safety layers it implements (krk_parrot.c adds the hook and the periodic watchdog):
 *   L1 gates (in.block) are evaluated by the caller immediately before the press
 *   L2 the TX budget: deadline = TX-up tick + budget; after it the release is re-posted every 500 ms
 *   L3 KEYING times out after 3 s (no TX voice) with a release
 *   L4 any key aborts: release + substitution end
 *   L5 disarm releases and goes silent from any state
 *   L6 ps_watch (review fix 1, 2026-10-29): from the press post until TX voice has been down for PS_WATCH_TICKS
 *      (and that long after the last release post) the radio is "maybe keyed", whatever the state: TX voice up
 *      while we think we are done (the press crossed the release) is released at once, every 500 ms; the
 *      substitution stays on (silence, never the microphone) until the watch is over; a new echo is refused
 */
#include "krk_parrot_sm.h"

static const char *const states[] = {"OFF", "IDLE", "REC", "HOLD", "KEYING", "SEND", "UNKEY", "COOL"};
static const char *const reasons[PR_N] = {
    "", "not digital", "encrypted", "TX not allowed", "channel busy", "TX inhibited", "CPS link", "own ID",
    "no capture", "aborted", "TX never up", "TX stuck", "stock ended TX", "done", "dual/side chg",
};

const char *ps_state_name(u32 s)
{
    return s < sizeof states / sizeof *states ? states[s] : "?";
}

const char *ps_reason_name(u32 r)
{
    return r < PR_N ? reasons[r] : "?";
}

void ps_init(struct ps_ctx *c)
{
    c->state = PS_OFF;
    c->reason = PR_NONE;
    c->t_state = c->deadline = c->t_rel = c->t_press = c->t_clear = 0;
    c->watch = 0;
}

static void enter(struct ps_ctx *c, u32 st, u32 now)
{
    c->state = (u8)st;
    c->t_state = now;
}

static int keyed(const struct ps_ctx *c)
{
    return c->state == PS_KEYING || c->state == PS_SEND || c->state == PS_UNKEY;
}

/* due: release re-post allowed now (at most one per PS_REL_TICKS) */
static int rel_due(struct ps_ctx *c, u32 now)
{
    if (c->t_rel && now - c->t_rel < PS_REL_TICKS)
        return 0;
    c->t_rel = now ? now : 1;
    return 1;
}

void ps_step(struct ps_ctx *c, const struct ps_in *in, struct ps_out *o)
{
    u32 now = in->now;

    o->press = o->release = o->begin = o->quiet = 0;
    if (!in->armed) {                               /* L5: disarm stops everything */
        if (keyed(c)) {
            o->release = 1;
            c->t_rel = now ? now : 1;
        }
        if (c->watch) {                             /* a press may still be in flight: release too, even from IDLE/COOL */
            o->release = 1;
            c->t_rel = now ? now : 1;
        }
        if (c->state != PS_OFF)
            o->quiet = 1;
        c->state = PS_OFF;
        return;
    }
    if (c->state == PS_OFF)
        enter(c, PS_IDLE, now);
    if (in->abort && (c->state == PS_KEYING || c->state == PS_SEND)) {      /* L4 */
        o->release = 1;
        o->quiet = 1;
        c->t_rel = now ? now : 1;
        c->reason = PR_ABORT;
        c->deadline = now;
        enter(c, PS_UNKEY, now);
        return;
    }
    if (in->abort && c->state == PS_HOLD) {         /* cancel the pending echo, keep the capture */
        c->reason = PR_ABORT;
        enter(c, PS_IDLE, now);
        return;
    }
    switch (c->state) {
    case PS_IDLE:
    case PS_HOLD:
        if (in->rec) {                              /* a new call overrides everything pending */
            enter(c, PS_REC, now);
            break;
        }
        if (c->state == PS_IDLE) {
            if (in->fresh && in->frames_ok && !in->play) {      /* a call ended: wait the hold time, then echo */
                enter(c, PS_HOLD, now);
                break;
            }
            if (!in->play)
                break;
            if (!in->frames_ok) {
                c->reason = PR_NO_CAP;
                break;
            }
        } else if (now - c->t_state < PS_HOLD_TICKS) {
            break;
        }
        if (in->block) {                            /* L1: no gate may fail at the moment of keying */
            c->reason = in->block;
            enter(c, PS_IDLE, now);
            break;
        }
        o->begin = 1;
        o->press = 1;
        c->watch = 1;                               /* from here on "maybe keyed" until ps_watch proves otherwise */
        c->t_press = now ? now : 1;
        c->t_clear = 0;
        c->reason = PR_NONE;
        c->deadline = now + PS_KEY_TICKS;
        enter(c, PS_KEYING, now);
        break;
    case PS_REC:
        if (in->rec)
            break;
        if (in->frames_ok)
            enter(c, PS_HOLD, now);
        else
            enter(c, PS_IDLE, now);
        break;
    case PS_KEYING:
        if (in->rec) {                              /* RX appeared while keying: give up */
            o->release = 1;
            o->quiet = 1;
            c->t_rel = now ? now : 1;
            c->reason = PR_ABORT;
            c->deadline = now;
            enter(c, PS_UNKEY, now);
        } else if (in->tx_up) {
            c->deadline = now + in->budget;         /* L2 */
            enter(c, PS_SEND, now);
        } else if (now - c->t_state >= PS_KEY_TICKS) {      /* L3 */
            o->release = 1;
            o->quiet = 1;
            c->t_rel = now ? now : 1;
            c->reason = PR_NO_TXUP;
            c->deadline = now;
            enter(c, PS_UNKEY, now);
        }
        break;
    case PS_SEND:
        if (!in->tx_up) {                           /* stock ended the TX (TOT, PTT release) */
            o->quiet = 1;
            c->reason = PR_STOCK_END;
            enter(c, PS_COOL, now);
        } else if (in->ended) {
            c->deadline = now;                      /* release was posted by the layer */
            enter(c, PS_UNKEY, now);
        } else if ((int)(now - c->deadline) >= 0 && rel_due(c, now)) {
            o->release = 1;
            if ((int)(now - c->deadline) >= PS_STUCK_TICKS) {
                o->quiet = 1;
                c->reason = PR_STUCK;
            }
        }
        break;
    case PS_UNKEY:
        if (!in->tx_up) {
            o->quiet = 1;
            if (c->reason == PR_NONE)
                c->reason = PR_DONE;
            enter(c, PS_COOL, now);
        } else if (now - c->t_state >= PS_REL_TICKS && rel_due(c, now)) {
            o->release = 1;
            o->quiet = 1;
            if (now - c->t_state >= PS_STUCK_TICKS)
                c->reason = PR_STUCK;
        }
        break;
    case PS_COOL:
        if (in->rec)
            enter(c, PS_REC, now);
        else if (now - c->t_state >= PS_COOL_TICKS)
            enter(c, PS_IDLE, now);
        break;
    default:
        break;
    }
}

/* A press was posted at t_press. The stock PTT handler may consume a press and a release posted a step apart in
 * one pass, in either order, so "TX is not up yet" proves nothing for the first PS_WATCH_TICKS. */
int ps_watch(struct ps_ctx *c, u32 now, u8 tx_up)
{
    int r = 0, k;

    if (!c->watch)
        return 0;
    k = keyed(c);
    if (tx_up) {
        c->t_clear = 0;
        if (!k || (int)(now - c->deadline) >= 0) {  /* not ours any more (state says done) or past the budget */
            if (rel_due(c, now))
                r |= 1;
            r |= 2;                                 /* never let the microphone reach the air */
        }
        return r;
    }
    if (!c->t_clear)
        c->t_clear = now ? now : 1;
    if (k) {                                        /* state machine still running (or its screen is gone/stuck) */
        if ((int)(now - c->deadline) < 0)
            return r;                               /* within the budget: the state machine decides */
        if (now - c->t_clear < PS_WATCH_TICKS && rel_due(c, now))
            r |= 1;                                 /* past it and TX voice never came/went: release during the quiet period */
    }
    if (now - c->t_clear >= PS_WATCH_TICKS && now - c->t_press >= PS_WATCH_TICKS &&
        (!c->t_rel || now - c->t_rel >= PS_WATCH_TICKS)) {
        c->watch = 0;
        r |= 4;
    }
    return r;
}
