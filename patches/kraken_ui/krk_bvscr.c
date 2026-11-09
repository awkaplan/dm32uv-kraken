/*
 * krk_bvscr.c - F13 "Burst Voice" screen (KRAKEN slot 7), blob kraken_ui4 (docs/burst-voice.md section 11).
 *
 * A message is a list of stock AMBE+2 units (27 B = 60 ms each, the unit the DMR vocoder tasks move). Sources and sinks, all RAM only
 * (nothing is written to flash or the data chip):
 *   sources  relay capture  the last received DMR call, taken at the vframe RX sink (as the parrot does)          setting
 *            received burst a BV1 burst demodulated from the FM Receive RAM while the screen is open on an analog channel (automatic)
 *            microphone     the stock TX encoder run UNKEYED, tapped through the vframe TX provider (hardware probe)  key 1
 *   sinks    analog burst   BV1 modem -> FM Send RAM -> FM modulator (the proven v39 APRS path)                    OK
 *            speaker        the stock Record playback sequence with units from RAM                                 key 4
 *            DMR call       the units retransmitted as a normal voice call through the vframe TX substitution      key 5
 * (The bench keys were removed 2026-11-02 once F13 passed on hardware: key 6 UART dump, key 7/8 H1 sounding probe and listen, key 9 test
 * message. Key 0 clears the message.)
 *
 * Settings (2026-11-06, docs/burst-voice.md 13): Mode, Key-up Lead and Relay Capture are stock option levels in
 * Settings > Channel Set > Burst Voice, kept in a flash record (krk_bvcfg.c, blob 2). The screen reads them at every enter
 * (krk_bv_cfg) and no key changes them: 2, 3 and Up/Down, which used to, say where they went. Relay capture ON means: while this
 * screen is open on a digital channel the capture sink is installed (at enter, and by the tick when the channel turns digital);
 * on an analog channel, and on Back, it is removed. Nothing is captured while the screen is closed (the sink writes into the
 * message buffer, and only the screen's tick turns a finished call into a message).
 *
 * Memory: the screen owns the shared 5632 B bigbuf (krk_vf_big_claim owner 2) while it is open and, after Back, while a message exists; the parrot (owner 1) refuses while we
 * own it and we refuse while it is armed. Layout (BIG_*): A = the message (92 units), B = packed payload / received chunks, SC = one
 * 256-sample half, then the tail (BVT_SIZE, kraken_ui4.ld) with the framing state and this screen's state S. While the parrot owns
 * the buffer NOTHING in it (S included) may be touched: W.own (ui4 .bss) says whether we own it.
 *
 * TX safety layers: gate (channel mode on the SELECTED side, stock TX allowed and not inhibited, channel idle, no stock cipher for DMR),
 * a second press within 3 s confirms, hard budget = air time + lead + 1 s, any key aborts, every exit path restores the registers and
 * posts the PTT release, and the maintenance task re-posts it past the deadline (krk_bv_wd_tick).
 */
#include "krk.h"
#include "krk_bv.h"
#include "krk_ambe.h"
#include "krk_bvmsg.h"
#include "krk_vframe.h"
#include "krk_stock.h"

/* stock voice-playback sequence (channel_send_dtmf_autodial_call @0x0306158c, docs/parrot-recording.md section 2) */
#define ac_playback_mode       ((void (*)(u32 on))0x00016000)           /* [0x48160+0x1494] = on */
#define ac_playback_on         ((void (*)(void))0x000160c0)             /* [0x1150]=1, bit 5 of 0x110003b0 / 0x11000080 */
#define ac_playback_off        ((void (*)(void))0x00016104)             /* clears bit 5 of 0x11000080 */
#define ac_set_key             ((void (*)(const u8 *key7, u32 n))0x00016064)   /* the 7-byte privacy key block */
#define ac_push                ((u32 (*)(u32 off, const u8 *buf, u32 n, u32 ms))0x000161a8)   /* unit -> ring 0x4a73c, 70 ms flow control */
#define spk_volume_set         ((void (*)(u32 level))0x03055850)
#define spk_volume_restore     ((void (*)(u32 idx))0x030230bc)
#define audio_pa_enable_gate   ((void (*)(u32 on))0x0301d6cc)
/* stock TX encoder start/stop (IRAM; the calls the stock call setup makes) */
#define ac_enc_start           ((void (*)(void))0x00016088)             /* [0x48160+0x1728] = 1, bit 4 of 0x110003b0 / 0x11000080 */
#define ac_enc_stop            ((void (*)(void))0x000160b0)             /* [0x1728] = 0 */

#define STOCK_KEY7  ((const u8 *)0x00033825)
#define STOCK_VOL   (*(volatile u8 *)0x0003ecfe)
/* PTT_RELEASE, TX_UP, GPIO_AF_SWITCH, AUDIO_CONTROL, FM_ADDR_SW: krk_stock.h. The analog send is the shared FM Send RAM engine
 * (krk_aprs_tx.c krk_fm_send_opt, blob ui2), and so is the dual-watch hold: */
#define on_selected_side krk_on_selected_side

/* ---- bigbuf layout ---- */
#define REC_SKIP    4                   /* mic record: units dropped at the start (encoder warm-up), NU_MAX are kept after them */
#define NU_MAX      BVM_NU_MAX          /* message units: 5.52 s */
#define BIG_A       0                   /* the message, NU_MAX * 27 = 2484 B */
#define BIG_B       (NU_MAX * 27)       /* packed payload (TX) / received chunks (RX) */
#define B_CAP       BVM_B_CAP           /* 78 chunks = 13 blocks */
#define BIG_SC      (BIG_B + B_CAP)     /* 512 B: one 256-sample half */
#define BIG_END     (BIG_SC + 512)      /* 5102 */
#define BIG_TOTAL   5632
#define BVT_SIZE    0x200               /* must equal BVT_SIZE in kraken_ui4.ld */
#define OWNER       2
#define TESTABLE    __attribute__((noinline))   /* keeps a symbol for tests/f13_checks.py */
typedef char big_fits[(BIG_END <= BIG_TOTAL - BVT_SIZE) ? 1 : -1];

#define MAGIC_W     0x57443133u         /* "WD13" */
#define CONFIRM_TICKS 300
#define DLEAD       2                   /* DMR relay: silence bursts before the message */
#define KEYWAIT_TICKS 300

extern char __bvm_start[], __bvm_end[], __bvt_start[], __bvt_end[];

/* ---- persistent state (ui4 .bss, magic guarded): the watchdog and the ownership flag ---- */
static struct {
    u32 magic, stamp;
    volatile u32 deadline;              /* tick after which the maintenance task releases PTT */
    volatile u8 txing;
    u8 own;                             /* this screen owns the bigbuf (S is valid) */
    u8 pad[2];
} W;

/* ---- screen state (bigbuf tail, valid while W.own; zeroed at enter) ---- */
static struct {
    u8 *buf;                            /* claimed bigbuf */
    const char *why;                    /* last status text (a literal) */
    u32 src, t_done, orig_aud, conf_t, sig, air_ms;
    volatile u32 cap_src;               /* relay capture (RX task writes) */
    volatile u16 cap_n;
    volatile u16 n;                     /* units in the message (the sink clears it when a new call starts) */
    volatile u8 cap_in, cap_done;
    u16 msg_id, rs;                     /* message counter; RS bytes corrected in the last received burst */
    u8 origin;                          /* 0 none, 1 relay capture, 2 received burst, 3 microphone */
    u8 layout, parity;
    u8 mode, lead, rxon, relay, have_orig, released, conf_key, rx_ok, rx_tot, evm_db, rx_last, good;
    volatile u32 cap_t;                 /* OS_TICKS of the last captured frame (appended: tests/f13_checks.py S_OFF) */
    u32 t_half;                         /* OS_TICKS of the last finished FM receive RAM half */
    u8 norx;                            /* no half for 1 s: show the reload hint */
    u8 rly_want;                        /* the Relay Capture setting, read at enter (relay = the sink is installed now) */
} S __attribute__((section(".bss.bvs")));
typedef char s_fits[sizeof S <= 96 ? 1 : -1];

#define A(i)    (S.buf + BIG_A + 27 * (i))
#define BPAY    (S.buf + BIG_B)
#define SC      ((short *)(S.buf + BIG_SC))

static void winit(void)
{
    if (W.magic != MAGIC_W || krk_rebooted(&W.stamp)) {     /* power-up noise, warm reset or a stale stamp (30 s unrefreshed):
                                                         * nothing is being sent, nobody owns anything */
        W.txing = 0;
        W.own = 0;
        W.deadline = 0;
        W.magic = MAGIC_W;
    }
}

static void post_release(void)
{
    OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
}

/* Maintenance task (krk_periodic.c through the hook table at 0x030e8020): if a send of ours is past its deadline, keep
 * releasing PTT. Integer only, no screen. */
void krk_bv_wd_tick(void)
{
    winit();
    if (W.txing && (int)(OS_TICKS - W.deadline) > 0)
        post_release();
}

static void wd_arm(u32 ticks)
{
    winit();
    W.deadline = OS_TICKS + ticks;
    W.txing = 1;
}

static void wd_clear(void)
{
    W.txing = 0;
}

static void say(const char *t, int good)
{
    S.why = t;
    S.good = (u8)good;
}

/* ---- small helpers ---- */
/* -Os turns the struct copy in krk_bv_frame.c into a memcpy call; the blob is freestanding, so it brings its own (no loop-to-memcpy
 * transformation: -fno-tree-loop-distribute-patterns) */
void *memcpy(void *d, const void *s, unsigned n)
{
    u8 *a = d;
    const u8 *b = s;

    while (n--)
        *a++ = *b++;
    return d;
}

static void zero(void *p, u32 n)
{
    u8 *b = p;

    while (n--)
        *b++ = 0;
}

/* Any key, Back included, aborts a long operation (the one that started the call is ignored until it was seen released). The key is
 * consumed here: the runner never sees it, so after an aborted operation Back has to be pressed again to leave the screen. The runner's keypad
 * ownership is kept alive here: a blocking call would otherwise let the stock poll re-enable itself after 20 blank polls. */
static int key_abort(void)
{
    if (krk_key_scan() == SCAN_NONE) {             /* the runner's scan (krk_core.c, blob 1): poll parked, menu-exit counter zeroed,
                                                     * the bus semaphore held, SCAN_TIMEOUT read as none */
        S.released = 1;
        return 0;
    }
    return S.released;
}

static void secs(char *o, u32 ms)                   /* "5.2s" */
{
    krk_catfix(o, (int)(ms / 100), 10, 0);
    krk_cat(o, "s");
}

/* ---- radio state: analog receive ---- */
static int is_analog(void)                           /* the SELECTED side is analog (dual-watch safe, krk_core.c) */
{
    return krk_channel_analog();
}

static void rx_apply(void)
{
    gpio_clear_output_bit(GPIO_AF_SWITCH);          /* AFOUT -> modem ADC (docs/rx-audio-fm-mode.md) */
}

static void rx_rearm_burst(void)                    /* a fresh receiver and a clean chunk buffer */
{
    u8 *b = BPAY;
    u32 i;

    for (i = 0; i < B_CAP; i++)
        b[i] = 0;
    krk_bv_rx_begin(b, 78 * 27);
}

static void rx_arm(void)
{
    if (S.rxon)
        return;
    if (!S.have_orig) {
        S.orig_aud = AUDIO_CONTROL;
        S.have_orig = 1;
    }
    rx_rearm_burst();
    rx_apply();
    S.rxon = 1;
    S.rx_last = (u8)(FM_ADDR_SW & 3);
    S.t_half = OS_TICKS;
    S.norx = 0;
}

static void rx_disarm(void)
{
    if (!S.rxon)
        return;
    S.rxon = 0;
    gpio_set_output_bit(GPIO_AF_SWITCH);
    if (S.have_orig)
        AUDIO_CONTROL = S.orig_aud;
}

/* ---- message store ---- */
static void msg_clear(void)
{
    S.n = 0;
    S.origin = 0;
    S.src = 0;
    S.cap_n = 0;
    S.cap_in = S.cap_done = 0;
    S.layout = 0;
    S.air_ms = 0;
}

/* Air time estimate of the message (shown before sending; the real packing happens at send time and may be shorter with trimming). */
static void msg_air(void)
{
    int l, chunks = krk_bvm_chunks_estimate(A(0), S.n, &l);

    S.layout = (u8)l;
    S.air_ms = krk_bv_tx_samples(S.mode, chunks) / 8;
}

static void set_msg(int n, int origin, u32 src)
{
    S.n = (u16)n;
    msg_air();
    S.origin = (u8)origin;
    S.src = src;
    S.t_done = OS_TICKS;
}

/* A received burst: chunks in B -> units in A (krk_bvmsg.c). */
static TESTABLE void rx_store(void)
{
    const krk_bv_info_t *in = krk_bv_info();
    int n;

    S.rx_ok = in->blocks_ok;
    S.rx_tot = in->hdr.nblocks;
    S.rs = (u16)in->rs_corrected;
    {
        unsigned e = in->evm_q16, db = 0;

        while (db < 30 && (65536u >> (db / 3 + 1)) > e)     /* about 3 dB per halving of the error power */
            db += 3;
        S.evm_db = (u8)db;
    }
    if (!in->have_hdr || !in->blocks_ok) {
        say("burst lost", 0);
        return;
    }
    n = krk_bvm_unpack(in, BPAY, S.buf + BIG_A, NU_MAX);
    if (n < 1) {
        say("payload bad", 0);
        return;
    }
    set_msg(n, 2, in->hdr.src_id);
    say(in->blocks_ok != in->hdr.nblocks ? "burst damaged" : "burst received", in->blocks_ok == in->hdr.nblocks);
}

/* The 256 samples in SC -> the modem; a finished burst is stored as the message. (Separate from the RAM read so the in-target test
 * can feed a host-generated burst from memory: the FM Receive RAM window is not writable from the debugger.) */
TESTABLE void krk_bv_rx_block(void)
{
    int ev = krk_bv_rx_feed(SC, 256);

    if (ev & (BV_EV_DONE | BV_EV_ABORT)) {
        if (ev & BV_EV_DONE)
            rx_store();
        else
            say("burst aborted", 0);
        rx_rearm_burst();                           /* the message was consumed above */
    }
}

/* One finished FM Receive RAM half -> the modem. */
static void rx_half(int h)
{
    volatile const u8 *p = FM_RX_HALF(h);
    short *x = SC;
    int i;

    for (i = 0; i < 256; i++)
        x[i] = (short)((p[2 * i] << 8) | p[2 * i + 1]);
    krk_bv_rx_block();
}

/* Consume finished FM Receive RAM halves. A half is stable for only 32 ms (the writer returns to it after the next flip) and the
 * runner's gap between two bv_tick calls (keypad scan under the LCD bus semaphore, 10 ms delay) is 20-30 ms, so a half seen at the
 * end of that gap was already being overwritten and one that flipped during it was missed: hardware 2026-11-01 (v52), two real-voice
 * bursts "burst aborted" in the normal mode while listen mode (which never leaves its loop) decoded the same burst 3/3.
 * Idle (no sync yet): one half or 6 polls, as before. Searching/receiving: stay here, yielding 1 tick per poll, until the receiver
 * is idle again (a key aborts it, 10 s cap). A gap between two halves longer than BV_RX_GAP_TICKS (OS_TICKS stalled elsewhere, a half
 * overwritten unread) re-arms instead of feeding the discontinuity. NEVER spin without os_time_dly_ms: OS_TICKS stops (v47). */
static void rx_poll(void)
{
    u32 t_in = OS_TICKS;
    int i, halves = 0;

    for (i = 0; ; i++) {
        u32 sw = FM_ADDR_SW & 3;

        if (sw != S.rx_last && (sw == 1 || sw == 2)) {
            u32 now = OS_TICKS;

            S.rx_last = (u8)sw;
            if (krk_bv_rx_state() != 0 && krk_bv_rx_gap(now, S.t_half)) {
                rx_rearm_burst();
                say("rx half missed", 0);
            }
            S.t_half = now;
            S.norx = 0;
            rx_apply();                             /* a squelch opening re-drives GPIO 2F: re-assert every half */
            rx_half(sw == 1 ? 0 : 1);
            halves++;
            if (krk_bv_rx_state() != 0 && (halves & 15) == 0 && key_abort()) {
                rx_rearm_burst();
                say("rx cancelled", 0);
                return;
            }
        } else {
            os_time_dly_ms(1);
        }
        if (krk_bv_rx_state() == 0) {
            if (halves || i >= 5)
                return;
        } else if ((int)(OS_TICKS - t_in) > 1000) {
            return;
        }
    }
}

/* ---- TX plumbing ---- */
/* FM TX gate: returns a refusal text or 0. */
__attribute__((noinline)) const char *krk_bv_gate_analog(void)
{
    if (!W.own)
        return "Parrot armed";
    if (!is_analog())
        return "Analog channel only";
    if (!krk_channel_on_selected())                  /* TX gate: the live struct (what keys up) must be the selected side's */
        return "Dual watch: retry";
    if (BAND_FLAGS & 0x20)
        return "TX not allowed here";
    if (tx_is_inhibited())
        return "TX inhibited";
    if (stock_rx_active() || (TXUP & 3) || krk_bv_rx_state() == 2)
        return "Channel busy";
    return 0;
}

__attribute__((noinline)) const char *krk_bv_gate_dmr(void)
{
    if (!W.own)
        return "Parrot armed";
    if (!krk_channel_dmr() || !krk_channel_on_selected())
        return "Digital channel only";
    if (STOCK_CIPHER)
        return "Encrypted channel";
    if (BAND_FLAGS & 0x20)
        return "TX not allowed here";
    if (tx_is_inhibited())
        return "TX inhibited";
    if (stock_rx_active() || (TXUP & 3) || S.cap_in || krk_vf.flags)
        return "Channel busy";                      /* krk_vf.flags: a substitution session (e.g. the parrot's release watch) is still on */
    return 0;
}

static void unkey_wait(void)
{
    int i;

    post_release();
    for (i = 0; i < 200 && ((CALL_ACTIVE & TX_UP) || (TXUP & 1)); i++) {
        os_time_dly_ms(10);
        if (i % 50 == 49)
            post_release();
    }
}

/* Second press within CONFIRM_TICKS confirms a transmit key. */
static int confirm(u8 key)
{
    u32 now = OS_TICKS;

    if (S.conf_key == key && now - S.conf_t < CONFIRM_TICKS) {
        S.conf_key = 0;
        return 1;
    }
    S.conf_key = key;
    S.conf_t = now;
    return 0;
}

/* The burst's fill for the shared engine: S.lead silent halves of key-up lead (squelch opening at the receiver), then gen()'s
 * 256-sample blocks through SC as big-endian int16; gen() returns 0 for the block holding its last samples, from which on the fill
 * returns 1 (that half and the silent ones after it are the engine's tail). */
struct bv_fill {
    int (*gen)(short *);
    int lead, ended;
};

static int bv_fill(void *ctx, volatile u8 *h)
{
    struct bv_fill *f = ctx;
    short *sc = SC;
    int i;

    if (f->lead > 0 || f->ended) {
        if (f->lead > 0)
            f->lead--;
        for (i = 0; i < 256; i++)
            sc[i] = 0;
    } else if (!f->gen(sc)) {
        f->ended = 1;                               /* this half holds the last samples */
    }
    for (i = 0; i < 256; i++) {
        h[2 * i] = (u8)(sc[i] >> 8);
        h[2 * i + 1] = (u8)sc[i];
    }
    return f->ended;
}

/* Keys up and streams gen() through the shared FM Send RAM engine (krk_aprs_tx.c krk_fm_send_opt: the beacon's gates, power-save
 * wake, FM clocks and route checks, docs/fm-send-ram-tx.md section 6) with Burst Voice's abort key and air-time budget; the watchdog
 * covers the whole call. Returns 0 sent; *why starts with 'S' only then (do_send_burst). */
static TESTABLE int fm_send(int (*gen)(short *), u32 air_ms, const char **why)
{
    struct bv_fill f;
    struct krk_fm_opts o;
    int r;

    S.released = 0;
    rx_disarm();                                    /* the proven TX path starts from the stock RX audio routing */
    f.gen = gen;
    f.lead = S.lead;
    f.ended = 0;
    o.abort = key_abort;
    o.budget = (air_ms + (u32)S.lead * 32 + 1000) / 10 + 20;
    wd_arm(KEYWAIT_TICKS + o.budget + 100);
    r = krk_fm_send_opt(bv_fill, &f, &o, why);
    if ((CALL_ACTIVE & TX_UP) || (TXUP & 1))
        unkey_wait();                               /* the engine waits for call_active only, posting once: keep releasing */
    wd_clear();
    if (r && **why == 'S')
        *why = "Modem stalled";                     /* "Stalled, re-routed" */
    return r ? -1 : 0;
}

/* Packs the message for the air into B. Returns the number of 27-byte chunks and sets *flags, or 0 with *why. */
__attribute__((noinline)) int krk_bv_prepare(int *flags, const char **why)
{
    int l, r = krk_bvm_pack(A(0), S.n, BPAY, flags, &l);

    S.layout = (u8)l;
    if (r < 1) {
        *why = r == -2 ? "Too long (raw units)" : "No message";
        return 0;
    }
    return r;
}

/* OK: send the message as an analog burst. */
static TESTABLE void do_send_burst(void)
{
    const char *why = krk_bv_gate_analog();
    int flags = 0, chunks;
    u32 ms;

    if (why) {
        say(why, 0);
        return;
    }
    rx_disarm();                                    /* B is the receiver's chunk buffer too */
    chunks = krk_bv_prepare(&flags, &why);
    if (!chunks) {
        say(why, 0);
        return;
    }
    if (!on_selected_side(1500)) {
        say("Dual watch: retry", 0);
        return;
    }
    ms = krk_bv_tx_samples(S.mode, chunks) / 8;
    S.msg_id++;
    krk_bv_tx_start(S.mode, (OS_TICKS & 0xfff0u) | (S.msg_id & 15), OWN_ID & 0xffffff, BPAY, chunks, 0, flags);
    say("Sending...", 1);
    fm_send(krk_bv_tx_fill, ms, &why);
    krk_bv_tx_stop();
    say(why, why[0] == 'S');
}

/* ---- unkeyed microphone record (hardware probe H2/H3, docs/burst-voice.md 11.4) ---- */
static TESTABLE int rec_prov(u8 *dst, u32 idx)               /* TX task, prio 16: keep the stock burst, send silence to the modem window */
{
    u32 n = S.n;
    int k;

    if (idx < REC_SKIP) {                           /* encoder start-up codes (hardware 2026-11-01: b0 117/77/117, 23/109): ~1/4 s garble */
        krk_vf_silence(dst);
        return KRK_VF_FRAME;
    }
    if (n < NU_MAX) {
        u8 *d = S.buf + BIG_A + 27 * n;

        for (k = 0; k < 27; k++)
            d[k] = dst[k];
        S.n = (u16)(n + 1);
    }
    krk_vf_silence(dst);                            /* whatever reaches the modem is silence, never the microphone */
    return KRK_VF_FRAME;
}

static TESTABLE void do_record(void)
{
    u32 aud, r3b0, t0, calls0;

    if (!W.own) {
        say("Parrot armed", 0);
        return;
    }
    if (!krk_channel_dmr() || !krk_channel_on_selected()) {
        say("Digital channel only", 0);
        return;
    }
    if (stock_rx_active() || (TXUP & 3) || (CALL_ACTIVE & TX_UP) || S.cap_in) {
        say("Channel busy", 0);
        return;
    }
    msg_clear();
    S.released = 0;
    aud = AUDIO_CONTROL;
    r3b0 = MODEM(0x3b0);
    calls0 = krk_vf.tx_calls;
    krk_vf_tx_begin(rec_prov, REC_SKIP + NU_MAX + 20, 700, 0);   /* session age cap 7 s: the layer drops it by itself */
    ac_enc_start();                                 /* the stock call setup's encoder start; NO key, NO PTT event */
    say("Recording...", 1);
    t0 = OS_TICKS;
    while ((int)(OS_TICKS - t0) < 640 && S.n < NU_MAX) {
        os_time_dly_ms(20);
        if ((TXUP & 1) || (CALL_ACTIVE & TX_UP))   /* anything keyed the radio: stop at once */
            break;
        if (key_abort())
            break;
    }
    krk_vf_tx_end();
    ac_enc_stop();
    AUDIO_CONTROL = aud;
    MODEM(0x3b0) = r3b0;
    if (S.n) {
        set_msg(S.n, 3, OWN_ID);
        say("Recorded", 1);
    } else {
        S.origin = 0;
        say(krk_vf.tx_calls == calls0 ? "no encoder bursts" : "no frames kept", 0);
    }
}

/* ---- relay capture (digital) ---- */
static TESTABLE void cap_sink(const u8 *f)                   /* RX task, prio 15: tiny */
{
    u32 n;
    u8 *d;
    int i;

    if (!(TXUP & 2)) {                              /* no RX voice: a call just ended */
        if (S.cap_in) {
            S.cap_in = 0;
            S.cap_done = 1;
        }
        return;
    }
    if (!S.cap_in) {                                /* a call starts: the capture overwrites the message */
        S.n = 0;
        S.cap_n = 0;
        S.cap_src = RX_SRC;
        S.cap_in = 1;
    }
    S.cap_t = OS_TICKS;
    n = S.cap_n;
    if (n >= NU_MAX)
        return;
    d = S.buf + BIG_A + 27 * n;
    for (i = 0; i < 27; i++)
        d[i] = f[i];
    S.cap_n = (u16)(n + 1);
}

/* The capture sink follows the setting: installed while it is ON and the channel is digital, removed otherwise. */
static TESTABLE void relay_sync(void)
{
    u8 want = S.rly_want && krk_channel_dmr();

    if (want == S.relay)
        return;
    S.relay = want;
    if (want) {
        S.cap_in = S.cap_done = 0;
        S.cap_n = 0;
    }
    krk_vf_rx_sink(want ? cap_sink : 0);
}

/* ---- DMR retransmit ---- */
static TESTABLE int dmr_src(u8 *dst, u32 idx)                /* TX task, prio 16 */
{
    u32 n = S.n;
    const u8 *s;
    int i;

    if (idx < DLEAD) {
        krk_vf_silence(dst);
        return KRK_VF_FRAME;
    }
    idx -= DLEAD;
    if (idx >= n)
        return KRK_VF_NONE;
    s = S.buf + BIG_A + 27 * idx;
    for (i = 0; i < 27; i++)
        dst[i] = s[i];
    return idx + 1 == n ? KRK_VF_LAST : KRK_VF_FRAME;
}

static TESTABLE void do_dmr(void)
{
    const char *why = krk_bv_gate_dmr();
    u32 total, budget, t0;
    int i;

    if (!why && S.n < 5)
        why = "No message";
    if (why) {
        say(why, 0);
        return;
    }
    S.released = 0;
    krk_vf_rx_sink(0);
    total = DLEAD + S.n;
    budget = total * 6 + 100;                       /* ticks: bursts + 1 s */
    krk_vf_tx_begin(dmr_src, total + 16, KEYWAIT_TICKS + budget + 200, 0);
    wd_arm(KEYWAIT_TICKS + budget + 100);
    say("DMR key-up...", 1);
    krk_vf_press();
    for (i = 0; i < 300 && !(TXUP & 1); i++) {
        os_time_dly_ms(10);
        if (key_abort())
            break;
    }
    if (!(TXUP & 1)) {
        krk_vf_release();
        krk_vf_tx_end();
        unkey_wait();
        wd_clear();
        say("TX did not come up", 0);
        return;
    }
    t0 = OS_TICKS;
    say("DMR sending", 1);
    while (TXUP & 1) {
        os_time_dly_ms(20);
        if (key_abort() || (int)(OS_TICKS - t0 - budget) > 0) {
            krk_vf_release();
            say("Aborted", 0);
            break;
        }
        if (krk_vf.end_idx != 0xffffffffu || krk_vf.done)
            break;                                  /* the layer has released; wait for TX down below */
    }
    if (S.why[0] == 'A')
        krk_vf_tx_quiet();                          /* abort/timeout: from now on silence frames only, never the microphone */
    for (i = 0; i < 100 && (TXUP & 1); i++) {       /* up to 2 s for TX down, release every 500 ms */
        if (i % 25 == 0)
            krk_vf_release();
        os_time_dly_ms(20);
    }
    if (TXUP & 1) {                                 /* still up: keep the quiet session and the watchdog (blob 2's tick keeps releasing) */
        krk_vf_tx_quiet();
        say("TX stuck: released", 0);
    } else {
        krk_vf_tx_end();
        wd_clear();
        if (S.why[0] == 'D')
            say("DMR sent", 1);
    }
    if (S.relay)
        krk_vf_rx_sink(cap_sink);
}

/* ---- playback to the speaker ---- */
static TESTABLE void do_play(void)
{
    u8 zero7[7];
    int i, ana;

    if (S.n < 1) {
        say("No message", 0);
        return;
    }
    if ((TXUP & 3) || stock_rx_active() || (CALL_ACTIVE & TX_UP)) {
        say("Channel busy", 0);
        return;
    }
    S.released = 0;
    ana = is_analog();
    rx_disarm();
    for (i = 0; i < 100 && (UI_BUSY_FLAGS & 2); i++)      /* stock waits for its audio-busy bit first */
        os_time_dly_ms(10);
    zero(zero7, 7);
    ac_set_key(zero7, 7);                           /* recorded units are not scrambled: zero key, restored below */
    ac_playback_mode(1);
    ac_playback_on();
    spk_volume_set(13);
    MODEM(0x100) &= ~0x80u;
    audio_pa_enable_gate(1);
    if (ana)
        gpio_clear_output_bit(GPIO_AF_SWITCH);      /* v53: analog leaves 0x2F high = FD6818 -> speaker amp and the vocoder (codec PWM) is cut
                                                     * off: silence on hardware 2026-11-01. A digital channel has 0x2F low (analog-vs-digital-rx-config
                                                     * s.4); stock's analog beeps clear it too (tx_gate_companded_audio_burst_send) */
    say("Playing...", 1);
    for (i = 0; i < S.n; i++) {
        if (ana && (i & 7) == 0)
            gpio_clear_output_bit(GPIO_AF_SWITCH);  /* a squelch opening re-drives it */
        ac_push(0, A(i), 27, 70);
        if ((i & 3) == 3 && key_abort())
            break;
    }
    os_time_dly_ms(150);                            /* the 2-slot ring still holds the last two units */
    ac_playback_mode(0);
    ac_set_key(STOCK_KEY7, 7);
    if (!(VFO_FLAGS8 & 0xf0))
        MODEM(0x100) |= 0x80u;                      /* stock re-sets it only on an analog channel */
    spk_volume_restore(STOCK_VOL);
    ac_playback_off();
    audio_pa_enable_gate(0);
    if (ana)
        gpio_set_output_bit(GPIO_AF_SWITCH);        /* back to the analog speaker path (rx_disarm's value) */
    say("Played", 1);
}

/* ---- screen ---- */
static void draw_idle(const char *why)
{
    krk_line(0, "Burst Voice", KRK_WARN);
    krk_line(1, why, KRK_WARN);
    krk_line(2, "Disarm the parrot,", KRK_DIM);
    krk_line(3, "then press a key", KRK_DIM);
}

static void draw(void)
{
    char l[KRK_LINE_BUF];
    int ana = is_analog(), conf = S.conf_key && OS_TICKS - S.conf_t < CONFIRM_TICKS;

    l[0] = 0;
    krk_cat(l, "Burst ");
    krk_cat(l, S.mode ? "FAST " : "ROBUST ");
    krk_cat(l, ana ? "analog" : krk_channel_dmr() ? "DMR" : "?");
    krk_line(0, l, KRK_FG);

    l[0] = 0;
    if (ana) {
        int st = krk_bv_rx_state();

        krk_cat(l, S.rxon ? "RX " : "rx off");
        if (S.rxon)
            krk_cat(l, st == 2 ? "receiving" : st == 1 ? "sync" : "listening");
    } else {
        krk_cat(l, S.relay ? (S.cap_in ? "relay REC " : "relay armed ") : "relay off");
        if (S.cap_in) {
            krk_cati(l, S.cap_n);
            krk_cat(l, "fr");
        }
    }
    krk_line(1, l, KRK_ACCENT);

    l[0] = 0;
    if (S.n) {
        krk_cat(l, "Msg ");
        secs(l, (u32)S.n * 60);
        krk_cat(l, S.origin == 1 ? " relay" : S.origin == 2 ? " rx" : " mic");
    } else {
        krk_cat(l, "No message");
    }
    krk_line(2, l, KRK_FG);

    l[0] = 0;
    if (S.n) {
        krk_cat(l, "from ");
        krk_cati(l, (int)S.src);
        krk_cat(l, " ");
        krk_cati(l, (int)((OS_TICKS - S.t_done) / 100));
        krk_cat(l, "s");
    }
    krk_line(3, l, KRK_DIM);

    l[0] = 0;
    if (S.n) {
        krk_cat(l, "air ~");
        secs(l, S.air_ms);
        krk_cat(l, S.layout ? " L" : " raw");
        if (S.layout)
            krk_cati(l, S.layout);
    }
    krk_line(4, l, KRK_DIM);

    l[0] = 0;
    if (S.rx_tot) {
        krk_cat(l, "blk ");
        krk_cati(l, S.rx_ok);
        krk_cat(l, "/");
        krk_cati(l, S.rx_tot);
        krk_cat(l, " rs");
        krk_cati(l, S.rs);
        krk_cat(l, " -");
        krk_cati(l, S.evm_db);
        krk_cat(l, "dB");
    }
    krk_line(5, l, S.rx_tot && S.rx_ok == S.rx_tot ? KRK_ACCENT : KRK_DIM);

    if (S.norx && S.rxon)
        krk_line(6, "no RX: reload ch", KRK_WARN);
    else
        krk_line(6, S.why ? S.why : "", S.good ? KRK_ACCENT : KRK_WARN);

    l[0] = 0;
    if (conf) {
        krk_cat(l, "PRESS AGAIN TO TX");
    } else {
        krk_cat(l, "lead ");
        krk_cati(l, S.lead * 32);
        krk_cat(l, "ms");
    }
    krk_line(7, l, conf ? KRK_WARN : KRK_DIM);
    krk_line(8, "OK:send 1:rec 0:clr", KRK_FG);
    krk_line(9, "4:play 5:dmr", KRK_DIM);
}

static TESTABLE void bv_enter(void)
{
    u8 *b;
    int kept;
    u32 cfg;

    krk_vf_service();
    winit();
    b = krk_vf_big_claim(OWNER);
    if (b && ((u32)&__bvt_start != (u32)b + BIG_TOTAL - BVT_SIZE || (u32)&__bvt_end - (u32)&__bvt_start > BVT_SIZE)) {
        krk_vf_big_release(OWNER);                  /* the tail would not sit where the linker script put it */
        b = 0;
    }
    kept = b && W.own && S.buf == b;                /* a message was left behind by the last visit: keep the buffer and S */
    W.own = b != 0;
    if (!b) {                                       /* the parrot owns the buffer: touch nothing in it */
        draw_idle("Parrot is armed");
        return;
    }
    if (kept) {
        S.rxon = S.relay = S.cap_in = S.cap_done = S.conf_key = S.released = 0;
    } else {
        zero(__bvt_start, (u32)(__bvt_end - __bvt_start));
        zero(__bvm_start, (u32)(__bvm_end - __bvm_start));
        S.buf = b;
    }
    cfg = krk_bv_cfg();                             /* the menu's settings (flash record; defaults fast, 320 ms, relay off) */
    S.mode = (u8)KRK_BVC_MODE(cfg);
    S.lead = (u8)KRK_BVC_LEAD(cfg);
    S.rly_want = (u8)KRK_BVC_RELAY(cfg);
    if (kept && S.n)
        msg_air();                                  /* a kept message: its air time in the mode now set */
    S.sig = 0xffffffffu;
    if (is_analog())
        rx_arm();
    relay_sync();
    draw();
}

/* Back: stop everything. A message that exists is KEPT (the buffer stays owned, the parrot is refused) so it can be carried to
 * another channel: capture or record on a digital channel, Back, change the channel with the stock keys, come back, send on
 * analog. Key 0 clears it, and Back then releases the buffer. */
static TESTABLE void bv_exit(void)
{
    int i;

    if (!W.own)
        return;
    rx_disarm();
    krk_vf_rx_sink(0);
    S.relay = 0;
    S.cap_in = 0;
    for (i = 0; i < 40 && ((TXUP & 1) || (CALL_ACTIVE & TX_UP)); i++) {     /* nothing of ours should be up; belt and braces */
        if (i % 10 == 0)
            post_release();
        os_time_dly_ms(50);
    }
    if (i)                                          /* a release was posted: its stock teardown / call-end UI events must reach the queue before the */
        os_time_dly_ms(100);                        /* runner drains it right after this hook (docs/menu-exit-timer.md section 7) */
    krk_bv_tx_stop();
    wd_clear();
    if (S.n)
        return;
    S.buf = 0;
    W.own = 0;
    krk_vf_big_release(OWNER);
}

static u32 screen_sig(void)
{
    u32 s = (u32)S.n | (u32)S.origin << 8 | (u32)S.mode << 12 | (u32)S.lead << 13 | (u32)S.rxon << 18 | (u32)S.relay << 19
            | (u32)S.cap_in << 20 | (u32)S.good << 21 | (u32)S.rx_ok << 22 | (u32)S.rx_tot << 27;

    s ^= (u32)(S.norx && S.rxon) * 0x5bd1e995u;
    s ^= (u32)S.why * 2654435761u + S.cap_n * 40503u + (u32)S.rs * 977u + (u32)S.evm_db * 131u;
    s ^= (u32)krk_bv_rx_state() << 24 ^ (u32)is_analog() << 31 ^ (u32)(S.conf_key && OS_TICKS - S.conf_t < CONFIRM_TICKS) << 30;
    if (S.n)
        s ^= (OS_TICKS - S.t_done) / 100 * 7919u;
    return s;
}

static TESTABLE void bv_tick(u8 scan)
{
    int i;
    u32 sig;

    winit();
    if (!W.own) {
        if (scan != SCAN_NONE)
            bv_enter();                             /* retry the claim on a key */
        return;
    }
    if (scan != SCAN_NONE) {
        if (scan != SCAN_OK && scan != SCAN_1 + 4)
            S.conf_key = 0;
        if (scan == SCAN_OK) {
            if (is_analog()) {
                if (confirm(scan))
                    do_send_burst();
            } else {
                say("OK = analog send", 0);
            }
        } else if (scan == SCAN_1) {
            do_record();
        } else if (scan == SCAN_1 + 1 || scan == SCAN_1 + 2 || scan == SCAN_UP || scan == SCAN_DOWN) {
            say("Change in the menu", 0);           /* relay, mode, lead: Settings > Channel Set > Burst Voice */
        } else if (scan == SCAN_1 + 3) {
            do_play();
        } else if (scan == SCAN_1 + 4) {
            if (confirm(scan))
                do_dmr();
        } else if (scan == SCAN_0) {
            msg_clear();
            S.rx_tot = 0;
            say("cleared", 1);
        }
    }
    relay_sync();                                   /* the channel may have changed mode */
    if (S.cap_in && (int)(OS_TICKS - S.cap_t) > 50) {
        S.cap_in = 0;                               /* hardware 2026-11-01: the RX sink is not called after the call */
        S.cap_done = 1;                             /* ends, so 0.5 s without a frame ends the capture here */
    }
    if (S.cap_done) {                               /* a relayed call finished */
        S.cap_done = 0;
        if (S.cap_n >= 5) {
            set_msg(S.cap_n, 1, S.cap_src);
            say("call captured", 1);
        } else {
            say("call too short", 0);
        }
        S.cap_n = 0;
    }
    /* analog receive: consume finished halves (rx_poll), then let the runner scan the keypad (tick_ms = 1) */
    if (is_analog() && S.rxon) {
        if (!S.norx && (int)(OS_TICKS - S.t_half) > 100)
            S.norx = 1;                             /* cold boot: stock FM receive RAM idle until the channel is reloaded */
        rx_poll();
    } else if (is_analog()) {
        rx_arm();
    } else if (S.rxon) {
        rx_disarm();                                /* the channel changed to digital */
    }
    if (S.rxon && krk_bv_rx_state() != 0)
        return;                                     /* never draw while a burst is being searched or received: a 30 ms redraw would miss halves
                                                     * (searching costs 31 MIPS, receiving 8: docs/burst-voice.md 11.10) */
    sig = screen_sig();
    if (sig != S.sig) {
        S.sig = sig;
        draw();
    }
}

const struct krk_screen krk_bv_screen __attribute__((section(".rodata.krk_bv_screen"))) = {
    .label = "Burst Voice",
    .enter = bv_enter,
    .tick = bv_tick,
    .exit = bv_exit,
    .tick_ms = 1,
};

/* the hook table blob2's maintenance tick reads (fixed address 0x030e8020, kraken_ui4.ld) */
void (*const krk_bv_hooks[1])(void) __attribute__((section(".rodata.krk_bv_hooks"), used)) = { krk_bv_wd_tick };
