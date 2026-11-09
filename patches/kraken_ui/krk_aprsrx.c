/*
 * krk_aprsrx.c - "APRS RX": analog APRS receive as a list of heard stations. While the screen is open
 * it streams the HR_C7000 FM Receive RAM halves (docs/rx-audio-fm-mode.md; 256 big-endian 16-bit
 * samples at 8 kHz per half, FM_ADDR_SW names the finished half) through the AFSK/AX.25 decoder
 * (krk_afsk.c, preset B + radio profile, the only profile) and keeps the last STN_MAX stations,
 * newest first, one per callsign.
 *
 * Audio path (HARDWARE, Audio Probe E9, 2026-10-15): GPIO 0x2F ("Switch audio PWM/FD6818") is an
 * analog switch on the FD6818's AFOUT. Stock drives it HIGH on analog channels (AFOUT -> speaker
 * amp) and LOW on digital (AFOUT -> modem ADC). With it LOW on an analog channel the FM RX RAM
 * carries the received audio. The screen drives it low on every finished half (the stock RX-audio
 * code re-drives it high when the squelch opens) and restores it on Back. The speaker is silent
 * meanwhile. Writes: GPIO 0x2F only. No FD6818 or TX-side register. Never keys PTT.
 *
 * F14 RX Monitor (docs/aprs-rx-monitor.md): the same decoder also runs at standby, from a hook at the tail of the stock
 * background task's loop (task "init", priority 30, one pass per 10 ms OS tick; krk_periodic.c krk_hook_bg_tick ->
 * krk_mon_tick), filling the same station ring. The screen is then a viewer. The monitor holds GPIO 0x2F low AND
 * AUDIO_CONTROL bit 6 (fm_play_ctrl: the modem's demodulated audio to the codec DAC, hardware 2026-10-15 "mode 2"), so the
 * speaker keeps playing. It runs only while every one of these holds, and gives the path back to stock the moment one
 * fails: Monitor on (RAM, magic word, default off, '#' in this screen); no Kraken screen running (krk_screen_owner:
 * Burst Voice, Parrot, Upload Beacon, this screen and the rest own the path/FM RAM/transmitter); no analog beacon send in
 * progress; loaded channel analog (live struct, so dual watch decodes whichever analog side is
 * loaded); no channel load in progress; not keyed; PTT pin up. Writes: GPIO 0x2F and AUDIO_CONTROL bit 6 only. No FD6818 or TX-side register.
 * Never keys PTT.
 */
#include "krk.h"
#include "krk_afsk.h"
#include "krk_stock.h"

#define NS          256
#define FM_PLAY_CTRL  0x40u                         /* AUDIO_CONTROL[6] fm_play_ctrl */

#define STN_MAX   8
#define CALL_LEN  10                /* "CALL-SS" + NUL; longer sources are cut */
#define INFO_LEN  8                 /* the row only has room for the first few characters */

struct stn {
    u16 sec;                        /* OS_TICKS / 100 of the last frame (wraps after 18 h) */
    char call[CALL_LEN];
    char info[INFO_LEN];
};

/* SRAM budget: the ring (STN_MAX * 20 B) sits in the blob's general .bss claim, the small state in the
 * decoder's claim (.bss.afsk has ~76 B spare); the .bss claim has no room for more (link-checked). */
static struct stn ring[STN_MAX];
static struct {
    u8 n;                           /* stations in the ring */
    u8 analog, norx;
    u8 div;
    u32 last_sw, shown_frames, last_sec;
    u32 t_half;                     /* OS_TICKS of the last finished half (or of entry) */
} R __attribute__((section(".bss.afsk.R")));       /* placed first in the claim by kraken_ui2.ld: the layout is R, M, then the decoder state */

/* RX Monitor state, also in the decoder's claim. Every field is valid only after krk_mon_set(1) (the magic is the
 * switch; SRAM is not zeroed at power-up), except ring_magic, which guards the ring on its own. */
#define MON_MAGIC  0x4d4f4e31u                      /* "MON1": Monitor on */
#define RING_MAGIC 0x52494e47u                      /* "RING": ring (and R.n) initialised */
#define MST_IDLE   0
#define MST_ARMED  1
#define MON_SETTLE 3                                /* passes (30 ms) the conditions must hold before arming */
#define MON_GAP_TICKS 6                             /* a half later than this after the previous one: re-arm the decoder (rx_poll precedent) */
static struct {
    u32 magic;                                      /* MON_MAGIC = on */
    u32 ring_magic;
    u32 last_tick;                                  /* OS_TICKS of the previous pass: going backwards = rebooted, SRAM kept, Off */
    u32 halves;                                     /* halves the monitor decoded since it was switched on */
    u8 st, own6, settle;
} M __attribute__((section(".bss.afsk.M")));

static void stn_copy(struct stn *d, const struct stn *s)
{
    unsigned i;

    d->sec = s->sec;
    for (i = 0; i < CALL_LEN; i++)
        d->call[i] = s->call[i];
    for (i = 0; i < INFO_LEN; i++)
        d->info[i] = s->info[i];
}

static int str_eq(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

/* Enters one decoded frame text "SRC>DST,PATH:INFO" (krk_afsk_line). Lines without '>' or ':' are ignored. */
static void stn_add(const char *line)
{
    struct stn e;
    const char *p;
    unsigned i;
    int at;

    for (i = 0; line[i] && line[i] != '>'; i++)
        ;
    if (!line[i] || i == 0)
        return;
    for (p = line + i; *p && *p != ':'; p++)
        ;
    if (!*p)
        return;
    p++;
    for (i = 0; i < CALL_LEN - 1 && line[i] != '>'; i++)
        e.call[i] = line[i];
    e.call[i] = 0;
    for (i = 0; i < INFO_LEN - 1 && p[i]; i++)
        e.info[i] = (p[i] >= 0x20 && p[i] < 0x7f) ? p[i] : '.';
    e.info[i] = 0;
    e.sec = (u16)(OS_TICKS / 100);
    for (at = 0; at < R.n && !str_eq(ring[at].call, e.call); at++)
        ;
    if (at == R.n) {                            /* new callsign: take a free slot or drop the oldest */
        if (R.n < STN_MAX)
            R.n++;
        at = R.n - 1;
    }
    for (; at > 0; at--)
        stn_copy(&ring[at], &ring[at - 1]);
    stn_copy(&ring[0], &e);
}

/* Row 9: the Monitor switch and, when on, how many halves the background monitor has decoded since it was switched on
 * (hardware check: grows by ~31 per second while the screen is closed on an analog channel). */
static void draw_help(void)
{
    char b[KRK_LINE_BUF], num[12];

    b[0] = 0;
    krk_catn(b, sizeof b, "RX Monitor ");        /* status only: the switch is the KRAKEN > RX Monitor option row */
    if (krk_mon_on()) {
        krk_catn(b, sizeof b, "on h");
        krk_catn(b, sizeof b, krk_itoa((int)(M.halves & 0xffff), num));
    } else {
        krk_catn(b, sizeof b, "off");
    }
    krk_line(9, b, KRK_DIM);
}

static void draw_all(void)
{
    char b[KRK_LINE_BUF], num[12];
    int i;

    b[0] = 0;
    krk_catn(b, sizeof b, krk_mon_on() ? "APRS MON " : "APRS RX ");
    if (R.n) {
        krk_catn(b, sizeof b, krk_itoa(R.n, num));
        krk_catn(b, sizeof b, R.n == 1 ? " station" : " stations");
    } else {
        krk_catn(b, sizeof b, "listening");
    }
    krk_line(0, b, KRK_ACCENT);
    for (i = 0; i < STN_MAX; i++) {
        b[0] = 0;
        if (i < R.n) {
            char age[12];

            krk_fmt_age(age, (u16)((u16)(OS_TICKS / 100) - ring[i].sec));
            krk_catn(b, sizeof b, ring[i].call);
            krk_catn(b, sizeof b, " ");
            krk_catn(b, sizeof b, age);
            krk_catn(b, sizeof b, " ");
            krk_catn(b, sizeof b, ring[i].info);
            b[KRK_COLS] = 0;
        }
        krk_line(1 + i, b, i == 0 ? KRK_FG : KRK_DIM);
    }
    if (R.norx)
        krk_line(9, "no RX: reload ch", KRK_WARN);   /* cold boot: stock FM receive RAM idle until the channel is reloaded */
    else
        draw_help();
}

static void process_half(volatile const u8 *h)
{
    short buf[32];
    int i, k;

    for (i = 0; i < NS; i += 32) {
        for (k = 0; k < 32; k++)
            buf[k] = (short)((h[2 * (i + k)] << 8) | h[2 * (i + k) + 1]);
        krk_afsk_feed(buf, 32);
    }
}

/* Moves frames decoded since the last call into the ring (oldest first). Returns 1 if there were any. */
static int harvest(void)
{
    unsigned fr = krk_afsk_frames();
    unsigned fresh;
    char line[AFSK_LINE_LEN + 1];

    if (fr == R.shown_frames)
        return 0;
    fresh = fr - R.shown_frames;
    if (fresh > AFSK_LINES)
        fresh = AFSK_LINES;
    R.shown_frames = fr;
    while (fresh--) {
        if (krk_afsk_line((int)fresh, line) > (int)fresh)
            stn_add(line);
    }
    return 1;
}

static void ring_clear(void)
{
    int i;

    R.n = 0;
    for (i = 0; i < STN_MAX; i++) {
        ring[i].sec = 0;
        ring[i].call[0] = 0;
        ring[i].info[0] = 0;
    }
}

static void ring_init(void)
{
    if (M.ring_magic != RING_MAGIC) {
        ring_clear();
        M.ring_magic = RING_MAGIC;
    }
}

static void dec_arm(void)                           /* a clean decoder in the stations screen's profile */
{
    krk_afsk_reset();
    krk_afsk_set_preemph(1);           /* radio profile: 2.5-sample tone skew compensation (decoder study 2026-10-16) */
    krk_afsk_set_preset(2);            /* preset B: fitted on capture16 (our own FM Send RAM TX), decoded the other radio's beacon on air */
    R.shown_frames = krk_afsk_frames();
}

/* ---- F14 RX Monitor ------------------------------------------------------------------------------ */

/* The two hardware reads of the half-consumer, out of line so the in-target tests can feed them (QEMU has no FD6818
 * audio, the FM RAM does not retain data and FM_ADDR_SW does not toggle). */
static u32 __attribute__((noinline)) mon_fm_sw(void)
{
    return FM_ADDR_SW & 3;
}

static volatile const u8 *__attribute__((noinline)) mon_half_ptr(u32 sw)
{
    return FM_RX_HALF(sw == 1 ? 0 : 1);
}

int krk_mon_on(void)
{
    return M.magic == MON_MAGIC;
}

/* Every condition under which the monitor may touch the audio path or the decoder. */
static int mon_may_run(void)
{
    if (krk_screen_owner == KRK_SCR_MAGIC)          /* a screen owns the path, the FM RAM or the transmitter */
        return 0;
    if (krk_aprs_tx_busy())                         /* analog beacon send (maintenance task) */
        return 0;
    if (VFO_FLAGS8 & 0xf0)                         /* loaded channel digital (FM RAM overlaps the DMR L2 area there) */
        return 0;
    if (LOADFLAGS & 0x08)                           /* channel load in progress: stock writes GPIO 0x2F itself */
        return 0;
    if (CALL_ACTIVE & 0x08)                             /* keyed */
        return 0;
    if (ptt_pin_down() & 1)                         /* PTT pressed, stock has not keyed yet */
        return 0;
    return 1;
}

/* Back to stock: bit 6 cleared if it is ours, GPIO 0x2F high only on an analog channel with no load running (on a digital
 * channel stock keeps it low; during a load stock writes it itself). Idempotent. */
static void mon_release(void)
{
    if (M.own6) {
        AUDIO_CONTROL &= ~FM_PLAY_CTRL;
        M.own6 = 0;
    }
    if ((VFO_FLAGS8 & 0xf0) == 0 && !(LOADFLAGS & 0x08))
        gpio_set_output_bit(GPIO_AF_SWITCH);
}

/* Capture path on (GPIO 0x2F low) with the speaker kept (AUDIO_CONTROL bit 6). Re-checks the gates first: the maintenance
 * task may have started a beacon since the pass began. Returns 0 if it did nothing. */
static int mon_assert(void)
{
    u32 a;

    if (!mon_may_run())
        return 0;
    gpio_clear_output_bit(GPIO_AF_SWITCH);
    a = AUDIO_CONTROL;
    if (!(a & FM_PLAY_CTRL)) {
        AUDIO_CONTROL = a | FM_PLAY_CTRL;
        M.own6 = 1;
    }
    return 1;
}

/* Releases the path now if the monitor holds it (the screen runner before enter(), the beacon before it keys). */
void krk_mon_yield(void)
{
    if (M.magic != MON_MAGIC)
        return;
    if (M.st == MST_ARMED || M.own6)
        mon_release();
    M.st = MST_IDLE;
    M.settle = 0;
}

void krk_mon_set(int on)
{
    if (!on) {
        if (M.magic == MON_MAGIC)
            krk_mon_yield();
        M.magic = 0;
        return;
    }
    if (M.magic == MON_MAGIC)
        return;
    M.st = MST_IDLE;
    M.own6 = 0;
    M.settle = 0;
    M.halves = 0;
    M.last_tick = OS_TICKS;
    ring_init();
    M.magic = MON_MAGIC;                            /* last: the tick sees fully initialised state */
}

/* One pass of the stock background loop (about every 10 ms). */
void krk_mon_tick(void)
{
    u32 now, sw;

    if (M.magic != MON_MAGIC)                       /* off (or SRAM noise): nothing at all */
        return;
    now = OS_TICKS;
    if ((int)(now - M.last_tick) < 0) {             /* tick counter went backwards: the radio rebooted with SRAM kept */
        M.magic = 0;
        return;
    }
    M.last_tick = now;
    if (!mon_may_run()) {
        if (M.st == MST_ARMED || M.own6) {
            mon_release();
            M.st = MST_IDLE;
        }
        M.settle = 0;
        return;
    }
    if (M.st != MST_ARMED) {
        if (M.settle < MON_SETTLE) {                /* conditions must hold for a few passes (dual-watch flips, channel loads) */
            M.settle++;
            return;
        }
        ring_init();
        dec_arm();
        R.last_sw = mon_fm_sw();
        R.t_half = now;
        if (mon_assert())
            M.st = MST_ARMED;
        return;
    }
    sw = mon_fm_sw();
    if (sw == R.last_sw || (sw != 1 && sw != 2))
        return;
    R.last_sw = (u32)sw;
    if ((int)(now - R.t_half) > MON_GAP_TICKS)      /* a half was missed: the decoder must not bridge the hole */
        dec_arm();
    R.t_half = now;
    if (!mon_assert()) {                            /* a gate closed since this pass began */
        mon_release();
        M.st = MST_IDLE;
        M.settle = 0;
        return;
    }
    process_half(mon_half_ptr(sw));                 /* finished half named by FM_ADDR_SW (datasheet); ~1-3 ms */
    M.halves++;
    harvest();
}

static void rx_enter(void)
{
    ring_init();
    if (!krk_mon_on())                              /* monitor off: a fresh list per visit; on: the viewer keeps what arrived */
        ring_clear();
    R.div = 0;
    R.norx = 0;
    R.analog = (VFO_FLAGS8 & 0xf0) == 0;
    R.last_sw = FM_ADDR_SW & 3;
    R.t_half = OS_TICKS;
    R.last_sec = OS_TICKS / 100;
    dec_arm();
    if (!R.analog) {
        krk_line(0, "APRS RX", KRK_ACCENT);
        krk_line(2, "Analog channel only", KRK_WARN);
        draw_help();
        return;
    }
    gpio_clear_output_bit(GPIO_AF_SWITCH);
    draw_all();
}

static void rx_tick(u8 scan)
{
    int i, redraw = 0;

    (void)scan;                                     /* no keys: the Monitor switch is the "RX Monitor" option row */
    if (!R.analog)
        return;
    /* consume halves for ~60 ms, then let the runner scan the keypad (tick_ms = 1 -> ~10 ms). */
    for (i = 0; i < 6; i++) {
        u32 sw = FM_ADDR_SW & 3;
        if (sw != R.last_sw && (sw == 1 || sw == 2)) {
            R.last_sw = sw;
            R.t_half = OS_TICKS;
            R.norx = 0;
            /* Re-assert the capture path every half: when the squelch opens on a new carrier the
             * stock RX-audio code re-drives GPIO 0x2F (speaker path) and the modem ADC parks. */
            gpio_clear_output_bit(GPIO_AF_SWITCH);
            /* FM_ADDR_SW names the FINISHED half the CPU may read (datasheet). */
            process_half(FM_RX_HALF(sw == 1 ? 0 : 1));
        }
        os_time_dly_ms(1);
    }
    if (harvest())
        redraw = 1;
    if (++R.div >= 3) {
        u8 nr;

        R.div = 0;
        nr = (u8)((int)(OS_TICKS - R.t_half) > 100);
        if (nr != R.norx) {
            R.norx = nr;
            redraw = 1;
        }
    }
    if (OS_TICKS / 100 != R.last_sec) {         /* ages move once a second */
        R.last_sec = OS_TICKS / 100;
        redraw = 1;
    }
    if (redraw)
        draw_all();
}

static void rx_exit(void)
{
    if (R.analog)
        gpio_set_output_bit(GPIO_AF_SWITCH);
}

const struct krk_screen krk_aprsrx_screen = {
    .label = "APRS RX",
    .enter = rx_enter,
    .tick = rx_tick,
    .exit = rx_exit,
    .tick_ms = 1,
};

/* The Monitor switch as a STOCK option level (krk_optrow.c, docs/stock-option-level.md): KRAKEN > RX Monitor > Off / On.
 * RAM only (policy P4): krk_mon_set() is the existing state machine entry, unchanged. */

static int mon_get(void)
{
    return krk_mon_on() ? 1 : 0;
}

static void mon_set(int idx)
{
    if ((idx != 0) != (krk_mon_on() != 0))
        krk_mon_set(idx != 0);
}

static const struct krk_opt mon_opt = {
    .title = "RX Monitor",
    .labels = krk_offon_labels,
    .n = 2,
    .get = mon_get,
    .set = mon_set,
};

const struct krk_screen krk_monitor_row = {
    .label = "RX Monitor",
    .opt = &mon_opt,
};
