/* Host test of the F9 parrot state machine (patches/kraken_ui/krk_parrot_sm.c): scripted scenarios plus a
 * randomised property check. Compiled and run by tests/test_parrot.py. Prints "PASS"/"FAIL" lines. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../patches/kraken_ui/krk_parrot_sm.c"

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (c) printf("PASS  "); else { printf("FAIL  "); fails++; } printf(__VA_ARGS__); printf("\n"); } while (0)

struct rig {
    struct ps_ctx c;
    struct ps_in in;
    struct ps_out o;
    u32 presses, releases, begins, ends;
};

static void rig_init(struct rig *r)
{
    memset(r, 0, sizeof *r);
    ps_init(&r->c);
    r->in.now = 1000;
    r->in.armed = 1;
    r->in.budget = 30 * 6 + PS_EXTRA_TICKS;
}

/* one 100 ms step */
static void st(struct rig *r)
{
    ps_step(&r->c, &r->in, &r->o);
    r->presses += r->o.press;
    r->releases += r->o.release;
    r->begins += r->o.begin;
    r->ends += r->o.quiet;
    r->in.play = r->in.abort = 0;
    r->in.now += 10;
}

static void steps(struct rig *r, int n)
{
    while (n-- > 0)
        st(r);
}

/* arm and capture a call, ending in HOLD */
static void to_hold(struct rig *r)
{
    rig_init(r);
    st(r);                               /* OFF -> IDLE */
    r->in.rec = 1;
    st(r);                               /* IDLE -> REC */
    steps(r, 5);
    r->in.rec = 0;
    r->in.frames_ok = 1;
    st(r);                               /* REC -> HOLD */
}

static void to_send(struct rig *r)
{
    to_hold(r);
    steps(r, PS_HOLD_TICKS / 10 + 1);    /* HOLD -> KEYING */
    r->in.tx_up = 1;
    st(r);                               /* KEYING -> SEND */
}

static unsigned rnd_state = 12345;
static unsigned rnd(void)
{
    rnd_state = rnd_state * 1103515245u + 12345u;
    return rnd_state >> 16;
}

int main(void)
{
    struct rig r;
    int i;

    /* ---- happy path */
    to_hold(&r);
    CHECK(r.c.state == PS_HOLD && r.presses == 0, "capture ends -> HOLD, nothing pressed yet");
    steps(&r, PS_HOLD_TICKS / 10 - 1);
    CHECK(r.c.state == PS_HOLD && r.presses == 0, "still holding after %d ticks", PS_HOLD_TICKS - 10);
    st(&r);
    CHECK(r.c.state == PS_KEYING && r.o.press && r.o.begin && r.presses == 1, "1.5 s after the call: begin + press together");
    CHECK(r.begins == 1 && !r.o.release, "begin precedes the press in the same step, no release");
    steps(&r, 5);
    CHECK(r.c.state == PS_KEYING && r.presses == 1, "waiting for TX voice: no second press");
    r.in.tx_up = 1;
    st(&r);
    CHECK(r.c.state == PS_SEND && r.c.deadline == r.in.now - 10 + r.in.budget, "TX voice up -> SEND, deadline = now + budget");
    steps(&r, 20);
    CHECK(r.c.state == PS_SEND && r.releases == 0, "sending: no release while within the budget");
    r.in.ended = 1;
    st(&r);
    CHECK(r.c.state == PS_UNKEY && r.releases == 0, "layer reached the last burst -> UNKEY (the hook posted the release)");
    r.in.tx_up = 0;
    st(&r);
    CHECK(r.c.state == PS_COOL && r.o.quiet && r.c.reason == PR_DONE, "TX down -> COOL, substitution ended, reason done");
    steps(&r, PS_COOL_TICKS / 10 - 1);
    CHECK(r.c.state == PS_COOL, "hold-off lasts %d ticks", PS_COOL_TICKS);
    st(&r);
    CHECK(r.c.state == PS_IDLE, "-> IDLE after the hold-off");

    /* ---- gates */
    to_hold(&r);
    r.in.block = PR_ENCRYPTED;
    steps(&r, PS_HOLD_TICKS / 10 + 2);
    CHECK(r.presses == 0 && r.begins == 0 && r.c.state == PS_IDLE && r.c.reason == PR_ENCRYPTED, "gate failing at the key moment: no press, reason encrypted");
    for (i = PR_NOT_DIGITAL; i <= PR_OWN_ID; i++) {
        to_hold(&r);
        r.in.block = (u8)i;
        steps(&r, PS_HOLD_TICKS / 10 + 2);
        CHECK(r.presses == 0 && r.c.reason == i, "gate %d (%s) blocks", i, ps_reason_name((u32)i));
    }
    rig_init(&r);
    st(&r);
    r.in.play = 1;
    r.in.frames_ok = 0;
    st(&r);
    CHECK(r.presses == 0 && r.c.reason == PR_NO_CAP && r.c.state == PS_IDLE, "play without a capture: refused");
    rig_init(&r);
    r.in.armed = 0;
    r.in.frames_ok = 1;
    r.in.play = 1;
    steps(&r, 3);
    CHECK(r.presses == 0 && r.c.state == PS_OFF, "disarmed: play does nothing");
    rig_init(&r);
    st(&r);
    r.in.play = 1; r.in.frames_ok = 1;
    st(&r);
    CHECK(r.presses == 1 && r.begins == 1 && r.c.state == PS_KEYING, "play last from IDLE with a capture and open gates: keys");
    rig_init(&r);
    st(&r);
    r.in.fresh = 1; r.in.frames_ok = 0;
    steps(&r, 30);
    CHECK(r.presses == 0, "a capture shorter than the minimum is never echoed");
    rig_init(&r);
    st(&r);
    r.in.fresh = 1; r.in.frames_ok = 1;
    st(&r);
    CHECK(r.c.state == PS_HOLD, "a call shorter than one tick (fresh flag) still reaches HOLD");

    /* ---- new call during HOLD */
    to_hold(&r);
    steps(&r, 5);
    r.in.rec = 1;
    st(&r);
    CHECK(r.c.state == PS_REC && r.presses == 0, "a new call during the hold cancels the echo");

    /* ---- abort */
    to_hold(&r);
    r.in.abort = 1;
    st(&r);
    CHECK(r.c.state == PS_IDLE && r.presses == 0, "key during HOLD cancels the echo");
    to_hold(&r);
    steps(&r, PS_HOLD_TICKS / 10 + 1);
    r.in.abort = 1;
    st(&r);
    CHECK(r.c.state == PS_UNKEY && r.o.release && r.o.quiet && r.c.reason == PR_ABORT, "key while KEYING: release + end");
    to_send(&r);
    r.in.abort = 1;
    st(&r);
    CHECK(r.c.state == PS_UNKEY && r.o.release && r.o.quiet && r.c.reason == PR_ABORT, "key while SEND: release + end");
    r.in.tx_up = 0;
    st(&r);
    CHECK(r.c.state == PS_COOL, "after the abort TX goes down -> COOL");

    /* ---- RX appears while keying */
    to_hold(&r);
    steps(&r, PS_HOLD_TICKS / 10 + 1);
    r.in.rec = 1;
    st(&r);
    CHECK(r.c.state == PS_UNKEY && r.o.release && r.o.quiet, "RX call while KEYING: give up with a release");

    /* ---- TX never comes up */
    to_hold(&r);
    steps(&r, PS_HOLD_TICKS / 10);              /* the last of these steps presses */
    steps(&r, PS_KEY_TICKS / 10 - 1);
    CHECK(r.c.state == PS_KEYING && r.releases == 0, "waiting for TX voice");
    st(&r);
    CHECK(r.c.state == PS_UNKEY && r.o.release && r.o.quiet && r.c.reason == PR_NO_TXUP, "3 s without TX voice: release + end, reason TX never up");

    /* ---- stock ends TX (TOT) */
    to_send(&r);
    r.in.tx_up = 0;
    st(&r);
    CHECK(r.c.state == PS_COOL && r.o.quiet && r.c.reason == PR_STOCK_END, "stock dropped TX during SEND: end, COOL");

    /* ---- deadline: release re-posted every 500 ms, then forced end */
    to_send(&r);
    r.releases = 0;
    while ((int)(r.in.now - r.c.deadline) < 0)
        st(&r);
    CHECK(r.releases == 0, "no release before the deadline");
    st(&r);
    CHECK(r.releases == 1 && r.c.state == PS_SEND, "first release at the deadline");
    steps(&r, 4);
    CHECK(r.releases == 1, "not re-posted within 500 ms");
    st(&r);
    CHECK(r.releases == 2, "re-posted after 500 ms");
    steps(&r, PS_STUCK_TICKS / 10);
    CHECK(r.ends >= 1 && r.c.reason == PR_STUCK, "stuck TX: substitution ended by force, reason TX stuck");

    /* ---- UNKEY keeps releasing while TX stays up */
    to_send(&r);
    r.in.ended = 1;
    st(&r);
    r.releases = 0;
    steps(&r, 60);
    CHECK(r.releases >= 1 && r.c.state == PS_UNKEY, "TX stuck up in UNKEY: release re-posted every 500 ms (%u)", r.releases);

    /* ---- disarm from every keyed state */
    for (i = 0; i < 3; i++) {
        if (i == 0) { to_hold(&r); steps(&r, PS_HOLD_TICKS / 10 + 1); }
        else if (i == 1) to_send(&r);
        else { to_send(&r); r.in.ended = 1; st(&r); }
        r.in.armed = 0;
        st(&r);
        CHECK(r.c.state == PS_OFF && r.o.release && r.o.quiet && r.c.watch, "disarm in keyed state %d: release + silence, OFF, watch continues", i);
    }
    to_hold(&r);
    r.in.armed = 0;
    st(&r);
    CHECK(r.c.state == PS_OFF && !r.o.release, "disarm in HOLD: OFF, nothing to release");

    /* ---- watch (periodic hook and screen step; review fix 1) */
    to_send(&r);
    CHECK(r.c.watch && ps_watch(&r.c, r.in.now, 1) == 0, "watch quiet before the deadline while TX is up and the state is SEND");
    {
        u32 t = r.c.deadline;
        int w1 = ps_watch(&r.c, t, 1), w2 = ps_watch(&r.c, t + 10, 1), w3 = ps_watch(&r.c, t + PS_REL_TICKS, 1);
        CHECK((w1 & 3) == 3 && !(w2 & 1) && (w3 & 1), "watch: release+silence at the deadline, then every 500 ms (%d %d %d)", w1, w2, w3);
    }
    to_hold(&r);
    CHECK(ps_watch(&r.c, r.in.now + 100000, 1) == 0, "watch never acts before any press");

    /* press crossing the release: abort one step after the press, TX voice comes up late */
    to_hold(&r);
    steps(&r, PS_HOLD_TICKS / 10);              /* presses */
    CHECK(r.c.state == PS_KEYING && r.c.watch, "press posted: watch armed");
    r.in.abort = 1;
    st(&r);                                     /* abort -> UNKEY, release */
    r.in.tx_up = 0;
    steps(&r, 3);
    CHECK(r.c.state == PS_COOL && r.c.watch, "TX not up: state COOL but the watch is still on");
    CHECK(ps_watch(&r.c, r.in.now, 0) == 0, "watch holds for %d ticks with TX down", PS_WATCH_TICKS);
    {
        int w = ps_watch(&r.c, r.in.now + 20, 1);
        CHECK((w & 3) == 3, "TX voice appears late while COOL: release + silence (%d)", w);
        w = ps_watch(&r.c, r.in.now + 21, 1);
        CHECK(!(w & 1), "not re-posted within 500 ms (%d)", w);
        w = ps_watch(&r.c, r.in.now + 20 + PS_REL_TICKS, 1);
        CHECK(w & 1, "re-posted after 500 ms while TX stays up (%d)", w);
        w = ps_watch(&r.c, r.in.now + 200, 0);
        CHECK(w == 0 && r.c.watch, "TX down again: the watch restarts its quiet period");
        w = ps_watch(&r.c, r.in.now + 200 + PS_WATCH_TICKS - 1, 0);
        CHECK(w == 0 && r.c.watch, "still within the quiet period");
        w = ps_watch(&r.c, r.in.now + 200 + PS_WATCH_TICKS + PS_REL_TICKS, 0);
        CHECK(w == 4 && !r.c.watch, "quiet period over: watch ends (end substitution)");
    }
    /* after the end a new echo is possible again (watch clear) and a disarm with no press has no release */
    rig_init(&r);
    r.in.armed = 1;
    st(&r);
    r.in.armed = 0;
    st(&r);
    CHECK(!r.o.release && !r.c.watch, "disarm without any press: nothing to release");
    /* disarm while IDLE after a press crossing (state not keyed, watch on) still releases */
    to_hold(&r);
    steps(&r, PS_HOLD_TICKS / 10);
    r.in.abort = 1;
    st(&r);
    r.in.tx_up = 0;
    steps(&r, 3);
    r.in.armed = 0;
    st(&r);
    CHECK(r.o.release && r.c.state == PS_OFF && r.c.watch, "disarm after an abort with the watch on: release again, watch continues");
    CHECK((ps_watch(&r.c, r.in.now + 60, 1) & 3) == 3, "state OFF + TX up: the watch (maintenance tick) releases and silences");

    /* screen gone while SEND: the maintenance tick alone must end the watch once TX is down */
    to_send(&r);
    {
        u32 t = r.c.deadline, e = 0;
        int k, w, rel = 0;

        for (k = 0; k < 60 && !e; k++) {
            w = ps_watch(&r.c, t + 10 * k, 0);
            rel += w & 1;
            e = w & 4;
        }
        CHECK(e && !r.c.watch && rel >= 1, "SEND state frozen, TX down: release during the quiet period, then the watch ends (%d releases)", rel);
    }

    /* ---- randomised property check */
    {
        u32 keyed_run = 0, worst = 0, bad_press = 0, bad_keyed = 0, n_press = 0;
        int k;

        rig_init(&r);
        for (k = 0; k < 400000; k++) {
            unsigned a = rnd();
            u8 was_keyed;
            r.in.armed = (a & 0x3ff) != 0;
            r.in.rec = (a >> 3 & 15) == 0;
            r.in.frames_ok = (a >> 7 & 7) != 0;
            r.in.fresh = (a >> 10 & 31) == 0;
            r.in.play = (a >> 5 & 63) == 0;
            r.in.abort = (a >> 9 & 127) == 0;
            r.in.block = (a >> 8 & 15) == 0 ? (u8)(1 + a % 7) : 0;
            r.in.tx_up = (a & 4) ? r.in.tx_up : (u8)(a >> 4 & 1);
            r.in.ended = (a >> 11 & 63) == 0;
            was_keyed = r.c.state >= PS_KEYING && r.c.state <= PS_UNKEY;
            st(&r);
            if (r.o.press) {
                n_press++;
                if (!r.in.armed || r.in.block || was_keyed || !r.o.begin)
                    bad_press++;
            }
            if (r.c.state >= PS_KEYING && r.c.state <= PS_UNKEY) {
                if (!r.in.armed)
                    bad_keyed++;
                keyed_run++;
                if (r.o.quiet)
                    keyed_run = keyed_run > 0 ? keyed_run : 0;
                if (keyed_run > worst)
                    worst = keyed_run;
            } else {
                keyed_run = 0;
            }
            if (r.c.state >= PS_KEYING && r.c.state <= PS_UNKEY && r.in.now - r.c.deadline < 0x80000000u &&
                r.in.now - r.c.deadline > PS_STUCK_TICKS + 3 * PS_REL_TICKS && !r.o.release && ps_watch(&r.c, r.in.now, r.in.tx_up) == 0 && r.c.t_rel + PS_REL_TICKS <= r.in.now)
                bad_keyed++;                    /* past the deadline a release must be due */
        }
        CHECK(n_press > 100, "random run: %u presses exercised", n_press);
        CHECK(bad_press == 0, "random run: every press had armed, gates open, begin, and was not already keyed (%u bad)", bad_press);
        CHECK(bad_keyed == 0, "random run: never keyed while disarmed, release always due past the deadline (%u bad)", bad_keyed);
        printf("      longest keyed stretch %u steps (%u ms)\n", worst, worst * 100);
    }

    printf("%d checks, %d failed\n", checks, fails);
    return fails != 0;
}
