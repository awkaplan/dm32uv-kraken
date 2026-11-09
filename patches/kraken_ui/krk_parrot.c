/*
 * krk_parrot.c - F9 DMR parrot: capture a received DMR voice call into RAM, then retransmit it.
 * Design and addresses: docs/parrot-tx.md, docs/parrot-recording.md; layer: krk_vframe.c; logic: krk_parrot_sm.c.
 *
 * 2026-11-02: presentation split into stock-menu rows (docs/stock-option-level.md 6): "Parrot" is an Off/On option level over
 * P.armed (par_set claims/releases the big buffer exactly as the old OK key did); "Parrot Play" / "Parrot Check" are one-shot rows
 * (arm popup "OK again to TX", a fresh second OK, then tx_run() drives the unchanged state machine to the end and a result popup
 * says what happened); "Parrot Clear" is a one-shot without TX; "Parrot Status" is the slimmed live screen (it still steps the
 * state machine, so the automatic echo after a heard call only runs while that screen is open, as before) and its Back no longer
 * disarms: the arm state belongs to the Parrot row. Nothing keys unless a screen/row is stepping the machine.
 *
 * Flow (armed; the automatic echo needs the Parrot Status screen open, or a Play/Check row running):
 *   call start  H1 (existing Last Heard splice 0x030208ba; krk_lastheard.c calls krk_ext_hooks[0] here):
 *               start capture, install the RX sink (RX task prio 15) that stores every 27-byte burst
 *   call end    H2 (0x03020984, krk_ext_hooks[1]): stop capture
 *   screen tick 100 ms: gather inputs, ps_step(), execute: krk_vf_tx_begin + PTT press (event 0x08 on
 *               group 0x3a4f0, as krk_aprs_tx.c does), the TX hook substitutes the stored bursts, the layer
 *               releases (0x10) right after the last one
 *   maint tick  krk_parrot_tick(): watchdog; re-posts the release every 500 ms past the deadline even if
 *               the screen is gone
 * SAFETY (layers, strongest first; docs/parrot-tx.md 6.3 and the Implementation section):
 *   G  gates evaluated at the moment of keying: armed, capture >= 5 bursts, DIGITAL channel on the selected
 *      side, no stock cipher (0x33824 == 0, docs/basic-privacy.md), stock TX allowed (0x3e31b bit 5 clear,
 *      docs/satellite-radio.md), channel idle (stock_rx_active, 0x3bd50 == 0), not TX-inhibited; the
 *      source of the captured call is not our own ID (0x33cd0)
 *   T  hard time cap: bursts*60 ms + 1 s after TX voice is up (SM deadline), session age limit in the layer
 *   H  the TX hook posts the release itself after the last burst and every 8th silence burst afterwards
 *   W  ps_watch from the screen tick AND krk_hook_maint_tick, independent of the state machine state: from the
 *      press post until TX voice has been down >= 1.5 s the radio is "maybe keyed"; TX voice up while we think
 *      we are done is released every 500 ms (review fix 1: a press can cross the release)
 *   K  any key press aborts and releases; Back/exit disarms and releases, the maintenance tick finishes the watch
 *   Q  abort/unkey/stuck/disarm keep the substitution on with SILENCE frames (krk_vf_tx_quiet) until the watch
 *      is over: no microphone audio of our session reaches the air (review fix 7)
 *   S  an exhausted provider sends the silence burst (krk_vf_silence), never stale data
 *   Parrot-specific: default DISARMED at boot (state is RAM only and validated against the tick counter),
 *   one echo per call, 3 s hold-off after an echo, calls from our own ID are not captured.
 * Threading: P is written by the menu task (screen), the rxvoice task (H1/H2) and read by the RX/TX vocoder
 * tasks (sink/provider); fields have one writer each, see the comments. No locks.
 */
#include "krk.h"
#include "krk_vframe.h"
#include "krk_parrot_sm.h"
#include "krk_stock.h"

#define P_MAGIC   0x50523031u           /* "PR01" */
#define CAP       KRK_VF_BIG_FRAMES     /* 208 bursts = 12.48 s */
#define LEAD_MAX  8
#define CHK_PASS  4                     /* self-check: stock (microphone) bursts passed and tapped before the stored ones */
#define CHK_SYNTH 15                    /* self-check without a capture: this many silence bursts (0.9 s) */
#define OWNER     1

static struct {
    u32 magic, stamp;                   /* stamp: krk_rebooted() */
    struct ps_ctx sm;
    volatile u8 armed, rec, ready, play_req, chk_req;
    u8 lead, pass, chk, chk_done, synth, last_state, up_seen, side;      /* side: loaded side at the call start (H1) */
    volatile u16 n;                     /* captured bursts (sink writes, screen reads) */
    u16 n_tx;                           /* bursts of the running/last transmission (snapshot at begin) */
    u32 src, dst;
    u8 kind;                            /* 1 group, 2 private, 3 all */
    u8 *buf;                            /* bigbuf, CAP x 27 */
    u32 echoes;
    u32 t_begin, t_up;                  /* ticks: press posted, TX voice seen */
    u32 c_calls, c_subst, c_sil, c_pass, c_rel;     /* layer counters at begin (for per-run deltas) */
    u32 sig;
    u32 conf_t;                         /* unused since 2026-11-02 (the two-press confirm is the Play/Check arm popup); kept so the older offsets and the size stay */
    u8 conf_key;                        /* unused, see conf_t */
} P KRK_FEAT;

static void pinit(void)
{
    int i;
    u8 *p = (u8 *)&P;

    if (P.magic != P_MAGIC || krk_rebooted(&P.stamp)) {   /* power-up noise or warm reset / stale state: start disarmed */
        for (i = 0; i < (int)sizeof P; i++)
            p[i] = 0;
        P.magic = P_MAGIC;
        P.lead = 2;
        ps_init(&P.sm);
        P.stamp = krk_vf_ticks();
    }
}

/* ---- capture ------------------------------------------------------------------------------ */

/* RX task (prio 15): store one burst. Tiny on purpose. */
static void rx_sink(const u8 *f)
{
    u32 n = P.n;
    u8 *d;
    int i;

    if (!P.rec || n >= CAP)             /* over the cap: the rest of the call is dropped */
        return;
    d = P.buf + n * KRK_VF_LEN;
    for (i = 0; i < KRK_VF_LEN; i++)
        d[i] = f[i];
    P.n = (u16)(n + 1);
}

static int keyed(void);

/* H1, rxvoice task: every accepted voice header (several per call). */
void krk_parrot_rx_start(void)
{
    u32 src = RX_SRC, dst = RX_DST;

    krk_vf_service();
    pinit();
    if (!P.armed || !P.buf || keyed() || dual_watch_state())    /* dual watch: the call may be on the other side */
        return;
    if (P.rec && P.src == src && P.dst == dst)
        return;                         /* repeated header / late entry of the same call */
    krk_vf_rx_sink(0);
    P.rec = 0;
    if (src == OWN_ID)                  /* never echo ourselves */
        return;
    P.n = 0;
    P.src = src;
    P.dst = dst;
    P.side = (u8)krk_channel_side();       /* the side the receiver is on (the struct's tag, not the racy loaded bit) */
    P.kind = RX_DSTCLASS == 3 ? 3 : (RX_TYPE == 1 ? 1 : 2);
    P.ready = 0;
    P.rec = 1;
    krk_vf_rx_sink(rx_sink);
}

/* H2, rxvoice task: call end. */
void krk_parrot_rx_end(void)
{
    pinit();
    if (!P.rec)
        return;
    krk_vf_rx_sink(0);
    P.rec = 0;
    P.ready = P.n >= PS_MIN_FRAMES;
}

/* ---- transmit ----------------------------------------------------------------------------- */

/* TX task (prio 16): provider for the layer. Stock mic bursts (self-check), lead silence, then the stored
 * bursts (or, for a self-check without a capture, silence for the same length). */
static int tx_src(u8 *dst, u32 idx)
{
    u32 n = P.n_tx;
    const u8 *s, *b = P.buf;
    int i;

    if (idx < P.pass)
        return KRK_VF_PASS;
    idx -= P.pass;
    if (idx < P.lead || P.synth) {
        krk_vf_silence(dst);
        if (P.synth)
            return idx + 1 >= P.lead + n ? KRK_VF_LAST : KRK_VF_FRAME;
        return KRK_VF_FRAME;
    }
    idx -= P.lead;
    if (idx >= n || !b)                 /* no buffer (disarmed meanwhile): the layer sends silence + release */
        return KRK_VF_NONE;
    s = b + idx * KRK_VF_LEN;
    for (i = 0; i < KRK_VF_LEN; i++)
        dst[i] = s[i];
    return idx + 1 == n ? KRK_VF_LAST : KRK_VF_FRAME;
}

static int gate_block(void)
{
    if (!krk_channel_dmr() || !krk_channel_on_selected())
        return PR_NOT_DIGITAL;
    if (dual_watch_state() || (P.n && (u8)krk_channel_side() != P.side))
        return PR_DUAL;                 /* review fix 5: the capture must come from the side we would key on */
    if (STOCK_CIPHER)
        return PR_ENCRYPTED;
    if (BAND_FLAGS & 0x20)
        return PR_TX_FORBID;
    if (P.rec || (TXUP & 3) || stock_rx_active())
        return PR_BUSY;
    if (tx_is_inhibited())
        return PR_INHIBIT;
    if (P.src == OWN_ID && P.n)
        return PR_OWN_ID;
    return 0;
}

/* gate_block plus the press watch: no new echo while the previous press is not proven finished. Not used by the
 * re-check right before the press (the state machine has just started the watch for THIS press). */
static int gate_now(void)
{
    int why = gate_block();

    return why ? why : (P.sm.watch ? PR_BUSY : 0);
}

static int keyed(void)
{
    return P.sm.state >= PS_KEYING && P.sm.state <= PS_UNKEY;
}

static void begin_tx(u32 n0)
{
    u32 total, burst_ticks;

    P.synth = (u8)(P.chk_req && n0 < PS_MIN_FRAMES);
    P.pass = P.chk_req ? CHK_PASS : 0;
    P.n_tx = P.synth ? CHK_SYNTH : n0;
    total = P.pass + P.lead + P.n_tx;
    burst_ticks = total * 6;
    P.chk = P.chk_req;
    P.chk_req = 0;
    P.chk_done = 0;
    P.up_seen = 0;
    P.t_begin = krk_vf_ticks();
    P.c_calls = krk_vf.tx_calls;
    P.c_subst = krk_vf.tx_subst;
    P.c_sil = krk_vf.tx_silence;
    P.c_pass = krk_vf.tx_pass;
    P.c_rel = krk_vf.rel_posts;
    P.echoes++;
    /* layer: a few spare bursts past the data, session age = key wait + burst time + 1 s + 2 s */
    krk_vf_tx_begin(tx_src, total + 16, PS_KEY_TICKS + burst_ticks + PS_EXTRA_TICKS + 200, P.chk);
}

/* executes ps_watch's result: bit0 release, bit1 silence-only, bit2 watch over -> end the substitution */
static void do_watch(int w)
{
    if (w & 1)
        krk_vf_release();
    if (w & 2)
        krk_vf_tx_quiet();
    if (w & 4)
        krk_vf_tx_end();
}

static void step(u8 any_key)
{
    struct ps_in in;
    struct ps_out o;
    u32 n0;
    int w;

    in.now = krk_vf_ticks();
    in.budget = (P.pass + P.lead + P.n_tx) * 6 + PS_EXTRA_TICKS;     /* valid once begin_tx ran (SM reads it at TX up) */
    in.armed = P.armed;
    in.frames_ok = P.n >= PS_MIN_FRAMES || P.chk_req;
    in.rec = P.rec;
    in.fresh = P.ready;
    in.play = P.play_req || P.chk_req;
    in.abort = any_key != SCAN_NONE;
    in.block = (u8)gate_now();
    in.tx_up = TXUP & 1;
    in.ended = krk_vf.end_idx != 0xffffffffu || krk_vf.done;
    n0 = P.n;
    ps_step(&P.sm, &in, &o);
    if (o.begin) {
        /* review fix 8: the gates and the capture were evaluated a step ago in ps_step's input; re-test them
         * right before the press (H1 may have started a new call or the channel may have gone busy since).
         * H1 ignores calls once the state is KEYING, so only the window before ps_step's state write is open. */
        int why = gate_block();

        if (!why && (P.rec || P.n != n0 || (n0 < PS_MIN_FRAMES && !P.chk_req)))
            why = PR_BUSY;
        if (why) {
            o.begin = o.press = 0;
            P.sm.watch = 0;
            P.sm.reason = (u8)why;
            P.sm.state = PS_IDLE;
            P.sm.t_state = in.now;
        } else {
            begin_tx(n0);
        }
    }
    if (o.press)
        krk_vf_press();
    if (o.release)
        krk_vf_release();
    if (o.quiet)
        krk_vf_tx_quiet();
    w = ps_watch(&P.sm, in.now, in.tx_up);
    do_watch(w);
    if (P.sm.state == PS_SEND && !P.up_seen) {
        P.up_seen = 1;
        P.t_up = in.now;
    }
    P.play_req = P.chk_req = 0;         /* requests are one-shot: taken now or refused */
    if (P.sm.state != PS_IDLE && P.sm.state != PS_COOL)
        P.ready = 0;
}

/* Maintenance task, about once a second (krk_periodic.c): the watch that does not need the screen or the
 * state machine state (review fix 1). The TX-voice bit is sampled here too, so a late press is caught even
 * after Back disarmed the parrot. */
void krk_parrot_tick(void)
{
    krk_vf_service();
    pinit();
    do_watch(ps_watch(&P.sm, krk_vf_ticks(), TXUP & 1));
}

/* percentage of one bits in the first nf bursts at f (bit-density sanity for the format check) */
static int ones_pct(const u8 *f, int nf)
{
    int i, b, c = 0;

    for (i = 0; i < nf * KRK_VF_LEN; i++)
        for (b = 0; b < 8; b++)
            c += (f[i] >> b) & 1;
    return nf ? c * 100 / (nf * KRK_VF_LEN * 8) : 0;
}

/* ---- screen ------------------------------------------------------------------------------- */

static void secs(char *out, u32 bursts)     /* "5.2s" from bursts of 60 ms */
{
    krk_catfix(out, (int)(bursts * 6 / 10), 10, 0);     /* bursts * 6 = 10 ms units */
    krk_cat(out, "s");
}

static void draw(void)
{
    char l[KRK_LINE_BUF];
    u32 now = krk_vf_ticks();
    int why;
    u16 col;

    l[0] = 0;
    krk_cat(l, "Parrot ");
    krk_cat(l, P.armed ? "ARMED" : "off");
    krk_line(0, l, P.armed ? KRK_WARN : KRK_DIM);

    l[0] = 0;
    krk_cat(l, ps_state_name(P.sm.state));
    if (P.sm.state == PS_HOLD) {
        krk_cat(l, " ");
        secs(l, (now - P.sm.t_state) / 6);
    } else if (P.sm.reason) {
        krk_cat(l, " ");
        krk_catn(l, 20, ps_reason_name(P.sm.reason));
    }
    krk_line(1, l, KRK_FG);

    l[0] = 0;
    krk_cat(l, "Cap ");
    krk_cati(l, P.n);
    krk_cat(l, " fr ");
    secs(l, P.n);
    krk_cat(l, P.n >= CAP ? " FULL" : "");
    krk_line(2, l, P.rec ? KRK_ACCENT : KRK_FG);

    l[0] = 0;
    if (P.n || P.rec) {
        krk_cat(l, P.kind == 1 ? "TG " : P.kind == 3 ? "ALL" : "PC ");
        if (P.kind != 3)
            krk_cati(l, (int)P.dst);
    }
    krk_line(3, l, KRK_DIM);
    l[0] = 0;
    if (P.n || P.rec) {
        krk_cat(l, "from ");
        krk_cati(l, (int)P.src);
    }
    krk_line(4, l, KRK_DIM);

    l[0] = 0;
    if (keyed() || P.up_seen) {
        krk_cat(l, "TX ");
        krk_cati(l, (int)krk_vf.tx_idx);
        krk_cat(l, "/");
        krk_cati(l, (int)(P.pass + P.lead + P.n_tx));
    } else {
        krk_cat(l, "echoes ");
        krk_cati(l, (int)P.echoes);
    }
    krk_cat(l, " lead");
    krk_cati(l, P.lead);
    krk_line(5, l, KRK_FG);

    l[0] = 0;
    krk_cat(l, "tx");
    krk_cati(l, (int)krk_vf.tx_calls);
    krk_cat(l, " sub");
    krk_cati(l, (int)krk_vf.tx_subst);
    krk_cat(l, " rx");
    krk_cati(l, (int)krk_vf.rx_sunk);
    krk_line(6, l, KRK_DIM);

    l[0] = 0;
    why = P.armed ? gate_now() : 0;
    col = KRK_DIM;
    if (P.chk_done) {                       /* self-check verdict, set when the run finished */
        krk_cat(l, "chk up");
        if (P.up_seen)
            secs(l, (P.t_up - P.t_begin) / 6);
        else
            krk_cat(l, " NO");
        krk_cat(l, " rel");
        krk_cati(l, (int)(krk_vf.rel_posts - P.c_rel));
        col = KRK_ACCENT;
    } else if (why) {
        krk_cat(l, "gate ");
        krk_catn(l, 20, ps_reason_name((u32)why));
        col = KRK_WARN;
    } else if (P.armed) {
        krk_cat(l, "gates ok");
    }
    krk_line(7, l, col);

    l[0] = 0;
    if (P.chk_done) {                       /* format check: one-bit density, captured RX vs stock TX tap */
        krk_cat(l, "1s rx");
        krk_cati(l, ones_pct(P.buf, P.n < 4 ? P.n : 4));
        krk_cat(l, "% tx");
        krk_cati(l, ones_pct(&krk_vf.tap[0][0], krk_vf.tap_n));
        krk_cat(l, "%");
    } else {
        krk_cat(l, "Play/Check/Clear: rows");
    }
    krk_line(8, l, KRK_DIM);
    krk_line(9, "U/D:lead Back:exit", KRK_DIM);
}

/* ---- the Parrot Status screen (slimmed: no arm/play/check/clear keys any more) ------------------------------------------- */
static void status_enter(void)
{
    krk_vf_service();
    pinit();
    P.sig = 0xffffffffu;
    P.ready = 0;                        /* a call that ended before the screen was opened is not echoed now */
    if (P.sm.state == PS_HOLD) {        /* ... and no stale pending echo either */
        P.sm.state = PS_IDLE;
        P.sm.t_state = krk_vf_ticks();
    }
    krk_line(0, "Parrot", KRK_DIM);
    draw();
}

/* Disarm: stop capturing, release the buffer. Also the "Parrot Off" path of the option row. */
static void disarm(void)
{
    P.armed = 0;
    krk_vf_rx_sink(0);
    P.rec = 0;
    P.ready = 0;
    if (P.sm.watch) {                           /* a press may be in flight or TX up: release now, silence until proven down */
        krk_vf_release();
        P.sm.t_rel = krk_vf_ticks() | 1;
        krk_vf_tx_quiet();                      /* the maintenance tick (ps_watch) keeps releasing and ends it; no wait here */
    }
    P.sm.state = PS_OFF;
    P.n = 0;
    P.chk_done = 0;
    P.buf = 0;
    krk_vf_big_release(OWNER);
}

static void step_tail(u8 any)
{
    step(any);
    if (P.sm.state == PS_COOL && P.last_state != PS_COOL && P.chk) {   /* self-check finished: report once */
        P.chk_done = 1;
        P.chk = 0;
    }
    P.last_state = P.sm.state;
}

static void status_exit(void)
{
    int i, was;

    pinit();
    if (keyed())
        step_tail(SCAN_BACK);           /* leaving ends a running echo: release + silence, exactly like any key */
    was = P.sm.watch;
    P.ready = 0;                        /* cancel a pending automatic echo; the capture itself is kept (arm state is the Parrot row's) */
    if (P.sm.state == PS_HOLD) {
        P.sm.state = PS_IDLE;
        P.sm.t_state = krk_vf_ticks();
    }
    if (was) {                          /* a press may be in flight or TX up: release now, silence until proven down */
        krk_vf_release();
        P.sm.t_rel = krk_vf_ticks() | 1;
        krk_vf_tx_quiet();
    }
    /* A release was posted. Its consequences (stock TX teardown, call-end UI events) reach the UI queue from other tasks;
     * the runner drains that queue right AFTER this hook returns, so let them happen first: wait (yielding, bounded, never on
     * OS_TICKS) until TX voice is down, then 100 ms for the follow-up events. Otherwise they land after the drain and the stock
     * UI can drop to standby behind the list (docs/menu-exit-timer.md section 7). */
    if (was) {
        for (i = 0; i < 100 && (TXUP & 1); i++)
            os_time_dly_ms(20);
        os_time_dly_ms(100);
    }
}

static void parrot_tick(u8 scan)
{
    u32 sig;
    u8 any = scan;

    pinit();
    if (!keyed()) {
        if (scan == SCAN_UP && P.lead < LEAD_MAX)
            P.lead++;
        else if (scan == SCAN_DOWN && P.lead > 0)
            P.lead--;
    }
    step_tail(any);
    sig = (u32)P.sm.state | (u32)P.n << 8 | (u32)P.armed << 24 | (u32)P.chk_done << 25 | (u32)P.sm.reason << 26;
    sig ^= krk_vf.tx_idx * 31u + krk_vf.tx_calls * 7u + krk_vf.rx_sunk * 13u + krk_vf.tx_subst * 17u + P.echoes + P.lead * 101u
           + (P.sm.state == PS_HOLD ? (krk_vf_ticks() - P.sm.t_state) / 10 : 0);
    if (sig != P.sig) {
        P.sig = sig;
        draw();
    }
}

const struct krk_screen krk_parrot_status_screen = {
    .label = "Status",
    .enter = status_enter,
    .tick = parrot_tick,
    .exit = status_exit,
};

/* ---- "Parrot": the arm switch, a stock option level Off / On (it never keys by itself) --------------------------------- */
static int par_get(void)
{
    pinit();
    return P.armed != 0;
}

static void par_set(int on)
{
    krk_vf_service();
    pinit();
    if (on) {
        if (P.armed)
            return;
        if ((P.buf = krk_vf_big_claim(OWNER)) != 0) {
            P.n = 0;
            P.armed = 1;
            P.sm.reason = PR_NONE;
        } else {
            P.sm.reason = PR_BUSY;      /* Burst Voice holds the shared buffer: the row reads Off again when reopened */
        }
    } else if (P.armed) {
        disarm();
    }
}

static const struct krk_opt par_opt = { .title = "Parrot", .labels = krk_offon_labels, .n = 2, .get = par_get, .set = par_set };

/* The arm switch is now the FIRST CHILD of the Parrot parent row, not the parent itself. */
const struct krk_screen krk_parrot_arm_screen = {
    .label = "Parrot On/Off",
    .opt = &par_opt,
};

/* ---- "Parrot Play" / "Parrot Check" / "Parrot Clear": one-shot rows (docs/stock-option-level.md 6.4) ------------------- */
#define TX_RUN_MAX    600               /* 100 ms steps: 60 s hard cap, far above any capture (12.5 s) plus key wait and budget */

/* Drives the state machine for ONE user-requested transmission (chk = self-check) to its end, with the keypad parked by the runner.
 * The engine calls are the screen's: step() with play_req / chk_req set, one step per 100 ms like the old screen tick, abort on any
 * key pressed after the confirming OK is released (the key still down from the arm popup does not count). Returns 0 when the echo
 * completed, else the ps_reason that refused or ended it. Bounded; every path that may have pressed PTT leaves the SM in
 * UNKEY/COOL with the watch running, and the loop below waits the watch out (the maintenance tick finishes it otherwise). */
static int __attribute__((noinline)) tx_run(int chk)   /* noinline: the booted tests put a breakpoint on it (single OK = 0 hits, OK-OK = 1) */
{
    u8 held, k, ab = SCAN_NONE;
    int i, began = 0;

    krk_vf_service();
    pinit();
    held = krk_key_scan();
    P.ready = 0;
    if (P.sm.state == PS_HOLD || P.sm.state == PS_COOL) {   /* a user request is not the anti-loop hold-off's business (the watch gate still holds) */
        P.sm.state = PS_IDLE;
        P.sm.t_state = krk_vf_ticks();
    }
    P.sm.reason = PR_NONE;
    P.play_req = (u8)!chk;
    P.chk_req = (u8)chk;
    for (i = 0; i < TX_RUN_MAX; i++) {
        k = krk_key_scan();
        if (k == SCAN_NONE)
            held = SCAN_NONE;
        else if (k != held)
            ab = k;
        step_tail(ab);
        if (!began) {
            if (!keyed())
                return P.sm.reason ? P.sm.reason : PR_BUSY;   /* refused: a gate, no capture, or a call arrived */
            began = 1;
        } else if (!keyed()) {
            break;
        }
        os_time_dly_ms(100);
    }
    if (keyed())
        step_tail(SCAN_BACK);           /* the cap: abort (release + silence) */
    for (i = 0; i < 60 && P.sm.watch; i++) {                 /* wait the press watch out so the substitution has ended */
        os_time_dly_ms(100);
        step_tail(SCAN_NONE);
    }
    return P.sm.reason == PR_DONE ? 0 : (P.sm.reason ? P.sm.reason : PR_STOCK_END);
}

/* Common front of Play and Check; returns 1 when it already told the user why not. */
static int tx_refused(int chk)
{
    krk_vf_service();
    pinit();
    if (!P.armed) {
        krk_popup(KRK_POP_ERR, "Parrot is off", "Set it On first", POP_RESULT_MS);
        return 1;
    }
    if (!chk && P.n < PS_MIN_FRAMES) {
        krk_popup(KRK_POP_ERR, ps_reason_name(PR_NO_CAP), 0, POP_RESULT_MS);
        return 1;
    }
    return 0;
}

/* Transmitting action: the row's own OK (shared with MENU, scan code 0x03) only arms; the send needs a second, FRESH OK. Any other
 * key or the timeout cancels. Same pattern as Upload Beacon (krk_aprs_menu.c upload_run). */
static void tx_row(int chk)
{
    char l1[KRK_LINE_BUF], l2[KRK_LINE_BUF];
    int r;

    if (tx_refused(chk))
        return;
    if (!krk_tx_arm("OK again to TX"))
        return;                                 /* timeout or any other key: cancelled, back to the menu */
    krk_popup(KRK_POP_INFO, chk ? "Checking..." : "Playing...", 0, 0);
    r = tx_run(chk);
    l1[0] = l2[0] = 0;
    if (r) {
        krk_popup(KRK_POP_ERR, ps_reason_name((u32)r), 0, POP_RESULT_MS);
        return;
    }
    if (chk) {
        krk_cat(l1, "Check done");
        krk_cat(l2, "up");
        if (P.up_seen)
            secs(l2, (P.t_up - P.t_begin) / 6);
        else
            krk_cat(l2, "?");
        krk_cat(l2, " rel");
        krk_cati(l2, (int)(krk_vf.rel_posts - P.c_rel));
    } else {
        krk_cat(l1, "Played");
        krk_cati(l2, (int)P.n_tx);
        krk_cat(l2, " bursts");
    }
    krk_popup(KRK_POP_OK, l1, l2, POP_RESULT_MS);
}

static void play_run(void)
{
    tx_row(0);
}

static void check_run(void)
{
    tx_row(1);
}

/* No TX: immediate result. */
static void clear_run(void)
{
    krk_vf_service();
    pinit();
    if (P.rec) {
        krk_popup(KRK_POP_ERR, "Recording now", "Try again", 1500);
        return;
    }
    if (!P.armed || !P.n) {
        krk_popup(KRK_POP_INFO, ps_reason_name(PR_NO_CAP), 0, 1200);
        return;
    }
    if (P.sm.state == PS_HOLD) {                /* cancel the pending echo of the capture being dropped */
        P.sm.state = PS_IDLE;
        P.sm.t_state = krk_vf_ticks();
    }
    P.n = 0;
    P.chk_done = 0;
    P.ready = 0;
    krk_popup(KRK_POP_OK, "Cleared", 0, 1200);
}

const struct krk_screen krk_parrot_play_screen = {
    .label = "Replay Audio",
    .enter = play_run,
    .flags = KRK_SF_ONESHOT,
};

const struct krk_screen krk_parrot_check_screen = {
    .label = "Check",
    .enter = check_run,
    .flags = KRK_SF_ONESHOT,
};

const struct krk_screen krk_parrot_clear_screen = {
    .label = "Clear Audio",
    .enter = clear_run,
    .flags = KRK_SF_ONESHOT,
};

/* The five Parrot rows are flat rows of the stock Channel Set list (krk_ext.c), with the user-facing labels the
 * nested layout asked for: Parrot On/Off, Replay Audio, Check, Clear Audio, Status. Grouping them under one nested
 * "Parrot" parent was investigated in full and does not work on this firmware - docs/nested-submenu.md. */

/* Standby tags (krk_tags.c): 1 while Parrot is On (it records heard DMR calls at standby). */
int krk_parrot_armed(void)
{
    return P.magic == P_MAGIC && P.armed;
}
