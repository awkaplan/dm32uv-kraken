/*
 * krk_aprs_tx.c - analog APRS transmit: an AX.25 UI frame as Bell 202 AFSK (1200 baud) on the
 * current ANALOG channel, generated in software and played through the HR_C7000 FM Send RAM
 * (docs/fm-send-ram-tx.md; on-air results docs/afsk-tx.md "capture14".."capture17").
 *
 *   key:    OSFlagPost(0x0003a4f0, 0x08) = the PTT-press event, so every stock TX gate runs
 *           (ptt_press_handler -> tx_key_from_vfo_mode_guard -> radio_init_sequence); wait for
 *           call_active (0x0003fdc8) bit 3
 *   audio:  AUDIO_CONTROL bit 0 (tx_voice_source) makes the FM modulator read PCM from the FM Send
 *           RAM halves (0x16000030 / 0x16000230, 256 big-endian int16 at 8 kHz, ping-pong on
 *           FM_ADDR_SW) instead of the codec ADC; the MOD DAC reaches the FD6818 MICIN through the
 *           DMR mux (GPIO 0x2E low, REG_47H 0x6740). Stock keeps fm_dev_coef_t = 0 on analog, so the
 *           deviation coefficient is raised to 0x40 for the send and restored after.
 *   tones:  one phase accumulator, 1200/2200 Hz, a 1200/8000 bit-clock accumulator: phase-continuous
 *           and exactly 1200 baud (capture16/17: frames bit-exact at rate 1.000). Mark and space at
 *           amplitude 8000: the TX path normalises the level, so other ratios only hurt (capture17).
 *   info:   "!lat/lon[comment" from the GPS fix; without a fix an APRS status packet ">comment no GPS
 *           fix", so the link works indoors too
 *   unkey:  OSFlagPost(0x0003a4f0, 0x10), the PTT-release event (also what TOT posts)
 *
 * Every exit path after keying restores the modem registers and releases PTT. Hard caps: TX-up wait
 * 2 s, frame <= 1500 bits (1.25 s), engine stall 300 ms, ROUTE_MAX re-routes.
 *
 * Standby and stock power save (2026-11-06, CW ID on hardware, Save 1:4). Two things a send from
 * standby needs that a send from one of our screens got for free:
 *   1. The radio awake. Stock wakes it in the keypad dispatch (event_queue_reset @0x03054d80) before any key
 *      handler runs; the PTT event loop itself has no wake, so a PTT_PRESS we post while the power-save
 *      machine has the radio asleep (state byte 0x3ebf1 == 2, ~80 % of the time at Save 1:4) keys it up in
 *      the sleep clock setup (CLK_MGR 0x2c = 0x00f00200): carrier, FM Send RAM consumed, no modulation.
 *      On the air: Morse on about 1 ID in 6, the rest silent, "Sent" every time. claim() now wakes the radio
 *      first, as stock's own beacon tick does before its send.
 *   2. The modem's FM clocks (CLK_MGR 0x2c bits 7:6, modem_clk_fmrx_en / fmtx_en). Only an analog channel
 *      apply sets them (modem_rf_mode_transition_apply @0x0301e628, |= 0xc0); every power-save sleep
 *      (clock_uart_reconfigure @0x0305499c) and wake (crystal_osc_and_timer5_init @0x03054a6c, 0xfff0ff3c)
 *      rewrites the register without them, and stock never misses them (its analog TX audio is the mic
 *      straight into the FD6818). Without them no half is consumed: FM_ADDR_SW stuck at 0, "Modem stalled".
 *      Setting them over the cable started the engine at once. route_on() now sets them.
 * The sender also re-takes the route whenever it finds a part of it undone (route_ok), up to ROUTE_MAX
 * times, and mutes the mic before keying so nothing of it goes out while TX comes up. In the sleep clock
 * setup OS_TICKS runs about twice as fast as real time (measured 2.06x over a minute at Save 1:4), so
 * every tick-based interval, the CW ID's included, comes out short while the radio power-saves.
 *
 * The three earlier methods (FD6818 tone retune, dual-tone gain switching, FSK engine) could not make
 * Bell 202 and were removed in v39.
 */
#include "krk.h"
#include "krk_ax25.h"
#include "krk_afsk.h"
#include "krk_config.h"
#include "krk_stock.h"

#define event_queue_reset      ((void (*)(void))0x03054d80)    /* stock's activity hook: wakes a power-saving radio */
#define PSAVE_STATE            ((volatile u8 *)0x0003ebf0)     /* [1] == 2: asleep (rf_mode_debounce_state_machine) */
/* PTT_PRESS / PTT_RELEASE / TX_UP, GPIO_MIC_MUX, AUDIO_CONTROL, FM_DEV_COEF, FM_ADDR_SW: krk_stock.h (Burst Voice uses them too) */

#define CLK_MGR_2C      MODEM(0x02c)    /* bit 7 modem_clk_fmrx_en, bit 6 modem_clk_fmtx_en */
#define FM_CLKS         0xc0u
#define INC_1200 644245094u             /* 1200/8000 * 2^32 */
#define INC_2200 1181116006u            /* 2200/8000 * 2^32 */


#define PRE_FLAGS  72               /* 480 ms TXDELAY of flags (+100 ms mark): the DM-32's own squelch
                                     * takes ~0.3-0.4 s to open (capture5), standard TXDELAY is 300-500 */
#define POST_FLAGS 3
#define MAX_BITS   1500
#define AMP_DEFAULT 8000
#define DEV_DEFAULT 0x40

static struct {
    u8 frame[160];
    u8 tones[MAX_BITS / 8 + 1];
    u32 sent, sent_magic;           /* .bss is not zeroed at power-up: sent is valid only with SENT_MAGIC */
} B;
#define SENT_MAGIC 0x53454e54u

static u32 sent_get(void)
{
    if (B.sent_magic != SENT_MAGIC) {   /* hardware 2026-11-01: Upload Beacon showed "sent 1895737346" */
        B.sent = 0;
        B.sent_magic = SENT_MAGIC;
    }
    return B.sent;
}

/* Digipeater paths selectable from the APRS menu ("Upload ID" on an analog channel). */
const char *const krk_aprs_path_names[KRK_APRS_PATHS] = {"WIDE1-1,WIDE2-1", "WIDE1-1", "WIDE2-1", "Direct"};
static const char *const path_1[KRK_APRS_PATHS] = {"WIDE1-1", "WIDE1-1", "WIDE2-1", 0};
static const char *const path_2[KRK_APRS_PATHS] = {"WIDE2-1", 0, 0, 0};

const char *krk_aprs_path_name(unsigned idx)
{
    return krk_aprs_path_names[idx < KRK_APRS_PATHS ? idx : 0];
}

/* The selected side is analog (dual-watch safe, krk_core.c). */
static int analog(void)
{
    return krk_channel_analog();
}

/* Dual watch: wait (up to ms) until the selected side is the one loaded, then hold the dual-watch
 * engine on it for the send, as stock's own beacon path does before its DMR send (gps_display_
 * refresh_and_notify @0x03062510: state 4, reload, state 3). State 1 = alternating sides, 3 = hold,
 * which resumes alternating by itself after ~10 engine ticks. Returns 0 if the other side stays loaded.
 * Exported: Burst Voice (krk_bvscr.c, blob ui4) holds the side with it before packing its burst. */
int krk_on_selected_side(int ms)
{
    while (!krk_channel_on_selected()) {
        if (ms <= 0)
            return 0;
        os_time_dly_ms(10);
        ms -= 10;
    }
    if (dual_watch_state() == 1) {
        DW_COUNTERS[0x1d] = 0;
        DW_COUNTERS[0x1c] = 0;
        dual_watch_set_state(3);
    }
    return krk_channel_on_selected();
}

int krk_aprs_tx_analog(void)
{
    return analog();
}

static void unkey(void)
{
    int i;

    OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
    for (i = 0; i < 200 && (CALL_ACTIVE & TX_UP); i++)
        os_time_dly_ms(10);
}

/* "!DDMM.mmN/DDDMM.mmW[comment" from the RMC text (truncated to 2 decimals of a minute). */
static int build_info(char *o, const struct krk_gps_fix *f)
{
    int i, n = 0;

    o[n++] = '!';
    for (i = 0; i < 7; i++) {                       /* ddmm.mm */
        if (!f->lat_s[i]) return -1;
        o[n++] = f->lat_s[i];
    }
    o[n++] = f->ns;
    o[n++] = KRK_APRS_SYMTAB;
    for (i = 0; i < 8; i++) {                       /* dddmm.mm */
        if (!f->lon_s[i]) return -1;
        o[n++] = f->lon_s[i];
    }
    o[n++] = f->ew;
    o[n++] = KRK_APRS_SYMBOL;
    o[n] = 0;
    krk_cat(o, KRK_APRS_COMMENT);
    return 0;
}

/* ---- AFSK sample generator --------------------------------------------------------------------- */
static struct {
    u32 ph, inc;
    u32 bacc;                   /* bit clock accumulator: += 1200 per sample, bit when >= 8000 */
    int bit, nbits;
    u16 amp, amp_s;             /* mark / space sample amplitudes (of 32767) */
    const u8 *tones;
    int done;
} G;

static void gen_start(const u8 *tones, int nbits, int amp, int amp_s, int lead_bits)
{
    G.ph = 0;
    G.inc = INC_1200;
    G.bacc = 0;
    G.bit = -lead_bits;
    G.nbits = nbits;
    G.amp = (u16)amp;
    G.amp_s = (u16)amp_s;
    G.tones = tones;
    G.done = 0;
}

/* Fills one half with the next 256 samples; silence once the stream is over. Returns 1 once it is
 * over (the fmram_send fill contract). */
static int gen_half(void *ctx, volatile u8 *h)
{
    int i;

    for (i = 0; i < 256; i++) {
        int v = 0;
        if (G.bit < G.nbits) {
            int t = G.bit < 0 ? 0 : (G.tones[G.bit >> 3] >> (G.bit & 7)) & 1;
            G.inc = t ? INC_2200 : INC_1200;
            G.ph += G.inc;
            v = (krk_afsk_sintab[G.ph >> 26] * (t ? G.amp_s : G.amp)) >> 14;
            G.bacc += 1200;
            if (G.bacc >= 8000) {
                G.bacc -= 8000;
                G.bit++;
            }
        } else {
            G.done = 1;
        }
        h[2 * i] = (u8)(v >> 8);
        h[2 * i + 1] = (u8)v;
    }
    (void)ctx;
    return G.done;
}

/* ---- FM Send RAM sender ------------------------------------------------------------------------ */
static struct {
    u32 aud, dev, r47;
    u8 dev_t;                   /* fm_dev_coef_t to use (0 = DEV_DEFAULT) */
    u16 amp_m, amp_s;           /* 0 = AMP_DEFAULT */
} FR;

#define ROUTE_MAX 8

/* Our TX audio path: FM Send RAM -> MOD DAC -> MICIN. Re-applied whenever stock has undone it. */
static void route_on(void)
{
    if ((CLK_MGR_2C & FM_CLKS) != FM_CLKS)          /* off since stock's last power-save sleep or wake: without them no */
        CLK_MGR_2C = CLK_MGR_2C | FM_CLKS;          /* half is ever consumed. Left on afterwards, as stock leaves them
                                                     * after an analog channel change; its next sleep drops them again */
    fd6818_reg_write(0x47, 0x6740);                 /* MICIN straight to the modulator (DMR bypass value) */
    gpio_clear_output_bit(GPIO_MIC_MUX);            /* SoC DAC onto MICIN instead of the mic */
    FM_DEV_COEF = (FR.dev & 0xffffff00u) | (u32)(FR.dev_t ? FR.dev_t : DEV_DEFAULT);
    AUDIO_CONTROL = AUDIO_CONTROL | 1;              /* tx_voice_source = FM Send RAM */
}

static int route_ok(void)
{
    return (CLK_MGR_2C & FM_CLKS) == FM_CLKS && (AUDIO_CONTROL & 1) && fd6818_reg_read(0x47) == 0x6740;
}

/* Keys up and streams fill(ctx, half) through the FM Send RAM. fill writes the next 256 samples
 * (big-endian int16, 8 kHz) of the half it is given and returns 1 once its stream has ended (it then
 * writes silence). The AFSK beacon (gen_half), the CW ID (krk_cwid.c, via krk_fm_send) and Burst Voice
 * (krk_bvscr.c, via krk_fm_send_opt) are the fills. o (NULL for the beacon and the CW ID) adds an abort
 * poll and an air-time budget; neither adds or moves a hardware access. Returns 0 if the engine ran;
 * a status text for the caller's screen in *why. */
static int fmram_send(int (*fill)(void *ctx, volatile u8 *h), void *ctx, const struct krk_fm_opts *o, const char **why)
{
    int i, halves = 0, stall = 0, tail = 0, stalled = 0, fixes = 0, aborted = 0, timeout = 0, poll = 0;
    u32 sw, last, t0;

    gpio_clear_output_bit(GPIO_MIC_MUX);            /* mic off the modulator before keying: nothing of it goes out */
    OSFlagPost_wrapper_set(PTT_GROUP, PTT_PRESS);
    for (i = 0; i < 200 && !(CALL_ACTIVE & TX_UP); i++) {
        os_time_dly_ms(10);
        if (o && o->abort && o->abort()) {         /* Burst Voice: a key during the key-up wait */
            aborted = 1;
            break;
        }
    }
    if (aborted || !(CALL_ACTIVE & TX_UP)) {
        unkey();
        gpio_set_output_bit(GPIO_MIC_MUX);
        *why = aborted ? "Aborted" : "TX did not come up";
        return -1;
    }
    os_time_dly_ms(100);
    FR.aud = AUDIO_CONTROL;
    FR.dev = FM_DEV_COEF;
    FR.r47 = fd6818_reg_read(0x47);
    for (i = 0; i < 512; i++) {                     /* both halves silent before switching the source */
        FM_TX_HALF(0)[i] = 0;
        FM_TX_HALF(1)[i] = 0;
    }
    route_on();
    last = FM_ADDR_SW & 3;
    t0 = OS_TICKS;                                  /* o->budget runs from here */
    /* tail counts halves written after the stream ended. The half holding the last frame samples
     * starts playing one switch after it was written, so stop only at the switch after THAT: with
     * tail < 2 the closing flags were cut off on the air (capture14). */
    while (tail < 3) {
        sw = FM_ADDR_SW & 3;
        if (sw != last && (sw == 1 || sw == 2)) {
            last = sw;
            stall = 0;
            if (fill(ctx, FM_TX_HALF(sw == 2)))     /* the half FM_ADDR_SW names is the one to fill */
                tail++;
            halves++;
            if (!route_ok()) {                      /* stock undid a part of the route (see the header): retake it */
                if (++fixes > ROUTE_MAX) {
                    stalled = 1;
                    break;
                }
                route_on();
            }
        } else {
            os_time_dly_ms(1);
            ++stall;
            if (stall % 20 == 0 && !route_ok()) {   /* the engine stops when bit 0 is cleared: retake, wait again */
                if (++fixes > ROUTE_MAX) {
                    stalled = 1;
                    break;
                }
                route_on();
                stall = 0;
            } else if (stall > 300) {               /* no half switch in 300 ms with the path ours: engine not running */
                stalled = 1;
                break;
            }
            if (o && o->abort && (++poll & 15) == 0 && o->abort()) {   /* every 16th idle ms (Burst Voice: a keypad scan) */
                aborted = 1;
                break;
            }
            if (o && o->budget && (int)(OS_TICKS - t0 - o->budget) > 0) {
                timeout = 1;
                break;
            }
        }
    }
    AUDIO_CONTROL = FR.aud;
    FM_DEV_COEF = FR.dev;
    fd6818_reg_write(0x47, FR.r47);
    gpio_set_output_bit(GPIO_MIC_MUX);
    unkey();
    if (stalled)
        *why = fixes > ROUTE_MAX ? "Path kept lost" : fixes ? "Stalled, re-routed" : "Modem stalled";
    else if (aborted || timeout)
        *why = aborted ? "Aborted" : "TX timeout";
    else
        *why = fixes ? "Sent, re-routed" : "Sent";
    return stalled || aborted || timeout ? -1 : 0;
}

/* Builds the beacon frame for the current fix and path. Returns the tone-bit count, or < 0 with *why. */
static int build_frame(const char **why, const char **kind)
{
    struct krk_gps_fix fix;
    char info[64], src[16];
    const char *path[2];
    int npath = 0, len, nbits;
    unsigned p = krk_aprs_cfg_path();

    if (!krk_call_get(src))       { *why = "Set callsign first"; return -1; }
    if (path_1[p]) path[npath++] = path_1[p];
    if (path_2[p]) path[npath++] = path_2[p];
    if (krk_gps_get(&fix)) {
        if (build_info(info, &fix) < 0) { *why = "Bad GPS text"; return -1; }
        *kind = "position";
    } else {
        /* No fix (indoors): an APRS status packet instead, so the link can still be tested. */
        info[0] = 0;
        krk_cat(info, ">");
        krk_cat(info, KRK_APRS_COMMENT);
        krk_cat(info, " no GPS fix");
        *kind = "status";
    }
    len = krk_ax25_frame(B.frame, sizeof B.frame, KRK_APRS_DEST, src, path, npath, info);
    if (len < 0)                  { *why = "Frame error"; return -1; }
    nbits = krk_ax25_tones(B.frame, len, PRE_FLAGS, POST_FLAGS, B.tones, MAX_BITS);
    if (nbits < 0)                { *why = "Frame too long"; return -1; }
    return nbits;
}

/* Sends one beacon now. Returns 0 when it went out (B.sent incremented); *why always set. *kind:
 * "position" or "status" (set when the frame was built). Refuses on a digital channel, with TX
 * inhibited, or while the channel is busy. */
#define TXB_MAGIC 0x42555359u      /* SRAM is not zeroed: a plain flag read as set forever (nothing clears it but a send) */
static volatile u32 tx_busy;       /* TXB_MAGIC = a send in progress (the menu screen and the periodic task can race) */

int krk_aprs_tx_busy(void)          /* F14 RX Monitor: a send is in progress, stay out of the audio path */
{
    return tx_busy == TXB_MAGIC;
}

/* The gates every send passes, in order. On 0 the caller owns the sender (tx_busy) and must clear it;
 * otherwise *why says why and the return says whether trying again shortly may succeed (1: busy
 * channel, dual watch, a send or a transmission already in progress) or not (-1). */
static int claim(const char **why)
{
    int r = 1, asleep;

    if (!analog())                { *why = "Analog channel only"; return -1; }
    if (tx_busy == TXB_MAGIC)     { *why = "Busy"; return 1; }
    tx_busy = TXB_MAGIC;
    asleep = PSAVE_STATE[1] == 2;   /* stock power save has the radio asleep: wake it as a key press would (see the
                                     * header), or it keys up unmodulated; awake, this only restarts the idle count */
    event_queue_reset();
    if (asleep)
        os_time_dly_ms(60);         /* the receiver was off: give the busy check below something to read */
    krk_mon_yield();                /* RX Monitor: GPIO 0x2F / AUDIO_CONTROL bit 6 back to stock before anything keys (fmram_send samples AUDIO_CONTROL) */
    if (tx_is_inhibited())        { *why = "TX inhibited"; r = -1; }
    else if (CALL_ACTIVE & TX_UP) { *why = "TX in use"; }      /* 2026-11-05: the user's PTT or VOX has the transmitter: switching
                                                                  * its audio source and then unkeying would cut that transmission */
    else if (!krk_on_selected_side(1500)) { *why = "Dual watch: retry"; }
    else if (VFO_FLAGS8 & 0xf0)  { *why = "Analog channel only"; r = -1; }
    else if (stock_rx_active())   { *why = "Channel busy"; }
    else return 0;
    tx_busy = 0;
    return r;
}

int krk_aprs_tx_send(const char **why, const char **kind)
{
    int nbits, r = -1;

    *kind = "";
    if (claim(why))
        return -1;
    if ((nbits = build_frame(why, kind)) >= 0) {
        FR.dev_t = 0;
        FR.amp_m = FR.amp_s = 0;
        gen_start(B.tones, nbits, FR.amp_m ? FR.amp_m : AMP_DEFAULT, FR.amp_s ? FR.amp_s : AMP_DEFAULT, 120);
        if (fmram_send(gen_half, 0, 0, why) == 0) {
            B.sent = sent_get() + 1;
            r = 0;
        }
    }
    tx_busy = 0;
    return r;
}

/* Sends fill's stream on the selected analog channel with the beacon's gates (CW ID, krk_cwid.c; with
 * options Burst Voice, krk_bvscr.c in blob ui4). 0 sent; 1 not now but soon (busy channel, dual watch,
 * TX or a send in progress); -1 refused or failed. *why always set. */
int krk_fm_send_opt(int (*fill)(void *ctx, volatile u8 *h), void *ctx, const struct krk_fm_opts *o, const char **why)
{
    int r = claim(why);

    if (r)
        return r;
    FR.dev_t = 0;
    r = fmram_send(fill, ctx, o, why) == 0 ? 0 : -1;
    tx_busy = 0;
    return r;
}

int krk_fm_send(int (*fill)(void *ctx, volatile u8 *h), void *ctx, const char **why)
{
    return krk_fm_send_opt(fill, ctx, 0, why);
}

/* ---- automatic beacon: the stock APRS periodic path, per channel mode ---------------------------
 * Stock (docs/aprs-submenu.md section 7): ui_periodic_maintenance_task_loop, once a second, gates
 * on CPS APRS enabled ([0x3fdc0] & 4) and "APRS active on this channel" ([0x40078] bit 0, set at
 * channel change; krk_stubs.S krk_hook_aprs_mode extends it to analog channels with APRS On), then
 * on a GPS fix ([0x38c6c] bit 6) or the fixed-position upload type ([0x40078] bit 2), then calls
 * the tick 0x030625bc: counter [0x40084]++ ; at the interval [0x4007c] (seconds, 0 = off, >= 30),
 * not TX-inhibited, no CPS link, counter = 0, event_queue_reset, not scanning/busy, channel TX
 * allowed, then the DMR send 0x03062510. The manifest replaces the fix gate and the tick call with
 * krk_hook_aprs_tick: on a DIGITAL channel it is the stock sequence unchanged; on an ANALOG channel
 * the same counter and interval drive an AFSK beacon, without the fix gate (no fix = status packet,
 * as Upload Beacon sends). The manual Upload Beacon key (programmable key, 0x030158b8) goes through
 * krk_hook_aprs_send_now the same way. The hooks run in the maintenance task (0x180-word stack). */
#define APRS_RT_FLAGS   (*(volatile u8 *)0x00040078)
#define APRS_INTERVAL   (*(volatile u16 *)0x0004007c)
#define APRS_COUNTER    (*(volatile u16 *)0x00040084)
#define stock_aprs_tick   ((void (*)(void))0x030625bc)
#define stock_aprs_send   ((void (*)(void))0x03062510)
#define RETRY_S 5

/* Analog interval timing uses the uC/OS tick (OSTickCtr, 100 Hz, rtos-and-memory.md) rather than
 * the stock seconds counter, because with dual watch on this hook only runs while a side with APRS
 * active is loaded, which would stretch the interval. A gap of more than 10 s between calls means APRS
 * was off or inactive: re-arm, so the first beacon comes one interval after it becomes active. */
#define TICK_HZ  100u
#define ARM_MAGIC 0x41524d44u
static struct { u32 magic, last_send, last_call; } T;

void krk_hook_aprs_tick(void)
{
    const char *why, *kind;
    unsigned interval;
    u32 now = OS_TICKS;

    if (!analog()) {
        if ((GPS_STATUS & 0x40) || (APRS_RT_FLAGS & 4))
            stock_aprs_tick();
        return;
    }
    if (T.magic != ARM_MAGIC || now - T.last_call > 10u * TICK_HZ) {
        T.magic = ARM_MAGIC;
        T.last_send = now;
    }
    T.last_call = now;
    interval = APRS_INTERVAL;
    if (interval < 30 || now - T.last_send < interval * TICK_HZ)
        return;
    if (tx_is_inhibited() || UI_MODE_BYTE /* stock's "no CPS link" gate */ || !krk_channel_on_selected())
        return;                                 /* due: try again next second */
    APRS_COUNTER = 0;
    event_queue_reset();
    if ((UI_BUSY_FLAGS & 0x18) || (krk_channel_flags() & 0x08)) {
        T.last_send = now;                      /* stock skips this round the same way */
        return;
    }
    if (krk_aprs_tx_send(&why, &kind) < 0 && stock_rx_active())
        T.last_send = now - (interval - RETRY_S) * TICK_HZ;   /* channel busy: retry in RETRY_S */
    else
        T.last_send = OS_TICKS;
}

void krk_hook_aprs_send_now(void)
{
    const char *why, *kind;

    if (analog())
        krk_aprs_tx_send(&why, &kind);
    else
        stock_aprs_send();
}

unsigned krk_aprs_tx_count(void)
{
    return sent_get();
}

/* Standby tags (krk_tags.c): 1 while the automatic analog beacon is armed: the stock APRS tick
 * reached krk_hook_aprs_tick on an analog channel in the last 10 s (it only does with APRS On on
 * that channel), the interval is set and a callsign is stored. */
int krk_beacon_active(void)
{
    char call[16];

    return analog() && T.magic == ARM_MAGIC && OS_TICKS - T.last_call <= 10u * TICK_HZ
        && APRS_INTERVAL >= 30 && krk_call_get(call);
}
