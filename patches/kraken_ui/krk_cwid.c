/*
 * krk_cwid.c - periodic CW (Morse) station ID on analog channels (2026-11-05, docs/cw-id.md).
 *
 *   what:   the stored callsign (krk_callsign.c, shared with APRS; the SSID is not sent) as Morse at
 *           CW_WPM, an on/off-keyed CW_HZ tone with CW_RAMP-sample edges, played through the FM Send
 *           RAM exactly like the AFSK beacon (krk_aprs_tx.c krk_fm_send: same keying, same gates)
 *   when:   a fixed timer, by design: every `Interval` minutes while the switch is on and
 *           the selected channel is analog, whether or not the user has transmitted. The first ID
 *           comes one interval after the timer arms (switch on, analog channel, callsign set).
 *   never:  over a busy channel (retry in RETRY_S), while the user's PTT or VOX has the transmitter
 *           (retry), with TX inhibited (skip this round), or while a menu or one of our screens is up
 *           (stock drops any menu to standby on a PTT press): then it waits, checking once a second.
 *   where:  Settings > Radio Settings > Other Func > CW ID (item 11, listed on analog channels only),
 *           a stock-style sub-menu: CW ID SW (ON / OFF), Interval (eight times, 1..30 min), Callsign
 *           (the shared editor). The nesting is krk_vox.c's, docs/otherfunc-submenu.md.
 *   stored: radio-wide, in the callsign record's spare byte (krk_cwid_cfg: bit 7 on, bits 6-0
 *           minutes), so a CPS write or factory reset turns the ID off.
 *
 * The scheduler runs in the maintenance task (krk_periodic.c, about once a second; 0x180-word stack)
 * and a send blocks it for the length of the ID (a 6-letter call at 20 WPM is about 5 s), as the
 * beacon's send does. The generator state lives on that stack for the send, so the feature's only
 * RAM is the 20-byte CW struct below.
 *
 * The interval is real time. It is counted in OS ticks, but those run about twice as fast as real
 * time while stock power save has the radio asleep (2026-11-06, on hardware, Save 1:4: 2.06x, "1 min" came
 * out at about 30 s; docs/fm-send-ram-tx.md 6.3), so krk_real_time() takes the surplus back out against
 * the RTC's seconds register.
 */
#include "krk.h"
#include "krk_afsk.h"
#include "krk_stock.h"

#define CW_WPM     20
#define CW_HZ      800
#define CW_UNIT    (9600 / CW_WPM)      /* samples per unit at 8 kHz: 1.2 s / WPM */
#define CW_INC     (CW_HZ * 536871u)    /* CW_HZ / 8000 * 2^32 */
#define CW_RAMP    32                   /* 4 ms rise and fall: no key clicks */
#define CW_AMP     8000                 /* the beacon's level (krk_aprs_tx.c AMP_DEFAULT) */
#define CW_LEAD    8                    /* units of carrier before the first element (480 ms): receivers' squelch */
#define CW_TAIL    2
#define RETRY_S    5
#define CFG_ON     0x80
#define CFG_MIN    0x7f
#define MIN_DEFAULT 10
#define MIN_MAX    60

#define RTC_NONE     0xff
#define TICK_HZ      100u
/* Stock's screen-mode byte UI_MODE_BYTE (krk_stock.h): menus and our screens are 7..0x3f, plus 0x69 and 0x77. */
int krk_ui_in_menu(void)                /* also krk_vox.c's */
{
    u8 m = UI_MODE_BYTE;

    return (m >= 7 && m <= 0x3f) || m == 0x69 || m == 0x77;
}

/* ---- Morse ------------------------------------------------------------------------------------ */

/* (elements << 5) | pattern; pattern bit i is element i (first sent = bit 0), 1 = dah. */
static const u8 morse_az[26] = {
    0x42, 0x81, 0x85, 0x61, 0x20, 0x84, 0x63, 0x80, 0x40, 0x8e, 0x65, 0x82, 0x43,   /* A..M */
    0x41, 0x67, 0x86, 0x8b, 0x62, 0x60, 0x21, 0x64, 0x88, 0x66, 0x89, 0x8d, 0x83,   /* N..Z */
};
static const u8 morse_09[10] = { 0xbf, 0xbe, 0xbc, 0xb8, 0xb0, 0xa0, 0xa1, 0xa3, 0xa7, 0xaf };

/* Keys s (up to its end or a '-', which starts the SSID) into units: bit k of out (LSB first) is
 * unit k, 1 = tone. Dit 1 unit, dah 3, 1 off between elements, 3 between characters. out holds
 * max units and is cleared first. Returns the unit count, or -1 for a character Morse cannot send
 * here, an empty call, or more than max units. */
int krk_morse_units(const char *s, u8 *out, int max)
{
    int n = 0, i, k, len, first = 1;
    u8 code;

    for (i = 0; i < (max + 7) / 8; i++)
        out[i] = 0;
    for (; *s && *s != '-'; s++) {
        if (*s >= 'A' && *s <= 'Z')
            code = morse_az[*s - 'A'];
        else if (*s >= '0' && *s <= '9')
            code = morse_09[*s - '0'];
        else
            return -1;
        for (k = 0; k < code >> 5; k++) {
            if (!first)
                n += k ? 1 : 3;
            first = 0;
            len = (code >> k) & 1 ? 3 : 1;
            if (n + len > max)
                return -1;
            for (i = 0; i < len; i++, n++)
                out[n >> 3] |= (u8)(1 << (n & 7));
        }
    }
    return n ? n : -1;
}

/* Sets c up to key nunits units of c->units after lead units of carrier. */
void krk_cw_start(struct krk_cw *c, int nunits, int lead)
{
    c->ph = 0;
    c->smp = 0;
    c->unit = (short)-lead;
    c->nunits = (short)nunits;
    c->env = 0;
    c->done = 0;
}

/* krk_fm_send fill: the next 256 samples of the keyed tone; returns 1 once the stream is over. The
 * envelope rises over CW_RAMP samples when a unit is on and falls over CW_RAMP in the off unit after
 * it; the phase restarts at 0 whenever the tone is fully off, so every element starts on a zero. */
int krk_cw_fill(void *ctx, volatile u8 *h)
{
    struct krk_cw *c = ctx;
    int i, v, on;

    for (i = 0; i < 256; i++) {
        v = 0;
        if (c->unit < c->nunits) {
            on = c->unit >= 0 && (c->units[c->unit >> 3] >> (c->unit & 7)) & 1;
            if (on && c->env < CW_RAMP)
                c->env++;
            else if (!on && c->env)
                c->env--;
            if (c->env) {
                c->ph += CW_INC;
                v = (krk_afsk_sintab[c->ph >> 26] * ((CW_AMP * c->env) / CW_RAMP)) >> 14;
            } else {
                c->ph = 0;
            }
            if (++c->smp >= CW_UNIT) {
                c->smp = 0;
                c->unit++;
            }
        } else {
            c->done = 1;
        }
        h[2 * i] = (u8)(v >> 8);
        h[2 * i + 1] = (u8)v;
    }
    return c->done;
}

/* ---- scheduler -------------------------------------------------------------------------------- */

#define CW_MAGIC 0x43574944u    /* "CWID": .bss is not zeroed at power-up */

/* In the AFSK claim's spare tail: blob 2's own .bss claim and the shared .bss.feat claim are full. */
#ifndef __APPLE__                   /* host tests on macOS: Mach-O has no ELF section names */
#define CW_BSS __attribute__((section(".bss.afsk")))
#else
#define CW_BSS
#endif
static struct {
    u32 magic;
    u32 last;                   /* OS tick of the last ID, or of the moment the timer (re-)armed */
    const char *why;            /* last send's result text (flash string), 0 = none yet; for debugging (read over gdb) */
    u8 sent;                    /* IDs sent since power-up; likewise */
    u8 rtc_sec;                 /* the RTC seconds value last seen, RTC_NONE = none yet */
    u32 rtc_tick;               /* OS tick at which rtc_sec was first seen */
} CW CW_BSS;

static void cw_init(void)
{
    CW.last = OS_TICKS;
    CW.why = 0;
    CW.sent = 0;
    CW.rtc_sec = RTC_NONE;
    CW.magic = CW_MAGIC;        /* last */
}

/* Keeps a tick-counted interval on real time: whenever the RTC's seconds are seen to change, the
 * difference between the ticks counted since the previous change and the real seconds that passed
 * moves *last (later for fast ticks, earlier for slow ones; the errors of single readings cancel
 * from one change to the next). An RTC that stands still or is absent (QEMU) changes nothing. The
 * seconds are compared modulo 60: the caller runs about once a second, and a clock set by hand or
 * by GPS in between shifts one interval by under a minute. *sec = RTC_NONE (re-)starts it. Shared
 * with the analog automatic beacon (krk_aprs_tx.c krk_hook_aprs_tick). */
void krk_real_time(u32 *last, u8 *rsec, u32 *rtick, u32 now)
{
    unsigned sec = rtc_reg(0);
    int surplus;

    if (sec == *rsec)
        return;
    if (*rsec < 60 && sec < 60) {
        surplus = (int)(now - *rtick) - (int)(((sec + 60 - *rsec) % 60) * TICK_HZ);
        if (surplus > (int)(now - *last))
            surplus = (int)(now - *last);       /* never ahead of the tick: that reads as a reboot */
        *last += (u32)surplus;
    }
    *rsec = (u8)sec;
    *rtick = now;
}

static unsigned cfg_min(unsigned cfg)
{
    unsigned m = cfg & CFG_MIN;

    return m >= 1 && m <= MIN_MAX ? m : MIN_DEFAULT;
}

/* Why the timer is not running, or 0 when it is (callsign in call). */
static const char *idle_reason(char *call)
{
    unsigned cfg = krk_cwid_cfg();

    if (!(cfg & CFG_ON))
        return "ID off";
    if (!krk_call_get(call))
        return "Set a callsign";
    if (!krk_channel_analog())
        return "Digital: no ID";
    return 0;
}

static int cw_send(const char *call, const char **why)
{
    struct krk_cw c;
    int n = krk_morse_units(call, c.units, KRK_CW_UNITS - CW_TAIL);

    if (n < 0) {
        *why = "Bad callsign";
        return -1;
    }
    krk_cw_start(&c, n + CW_TAIL, CW_LEAD);
    return krk_fm_send(krk_cw_fill, &c, why);
}

/* Maintenance task, about once a second (krk_periodic.c). */
void krk_cwid_tick(void)
{
    char call[16];
    u32 now = OS_TICKS, period;
    const char *why;
    int r;

    if (CW.magic != CW_MAGIC || (int)(now - CW.last) < 0)   /* power-up, or rebooted with SRAM kept */
        cw_init();
    if (idle_reason(call)) {
        CW.last = now;          /* not armed: the first ID comes one interval after it is */
        CW.rtc_sec = RTC_NONE;
        return;
    }
    krk_real_time(&CW.last, &CW.rtc_sec, &CW.rtc_tick, now);
    period = cfg_min(krk_cwid_cfg()) * 60u * TICK_HZ;
    if (now - CW.last < period)
        return;
    if (krk_ui_in_menu() || krk_screen_owner == KRK_SCR_MAGIC)
        return;                 /* due, but a menu or one of our screens is up: next second */
    r = cw_send(call, &why);
    CW.why = why;
    if (r == 0) {
        CW.sent++;
        CW.last = OS_TICKS;
    } else if (r > 0) {
        CW.last = now - (period - RETRY_S * TICK_HZ);       /* busy channel / TX in use: again in RETRY_S */
    } else {
        CW.last = OS_TICKS;     /* refused (TX inhibited, TX did not come up): skip this round */
    }
}

/* ---- the settings: Settings > Radio Settings > Other Func > CW ID ------------------------------
 * Stock-style rows (docs/otherfunc-submenu.md): Other Func item 11 is the CW ID parent, listed on analog
 * channels only (krk_vox.c krk_hook_of_gen); its children are stock option levels plus the shared
 * callsign editor. krk_vox.c's level-6 hooks ask krk_cwid_kid() for a child's spec. */

const char *const krk_onoff_labels[2] = { "ON", "OFF" };   /* stock's VOX SW order and case; the other ON/OFF rows share it */
static const u8 min_vals[8] = { 1, 2, 3, 5, 10, 15, 20, 30 };
static const char *const min_labels[8] = { "1 min", "2 min", "3 min", "5 min", "10 min", "15 min", "20 min", "30 min" };

static int sw_get(void)
{
    return krk_cwid_cfg() & CFG_ON ? 0 : 1;
}

static void sw_set(int idx)
{
    unsigned cfg = krk_cwid_cfg();

    krk_cwid_cfg_set((idx == 0 ? CFG_ON : 0) | cfg_min(cfg));
}

/* After stock's "Setting is OK": say why an ID that was just switched on will not be sent. */
static void sw_after(int idx)
{
    char call[16];
    const char *why = idx == 0 ? idle_reason(call) : 0;

    if (why) {
        krk_popup_draw(KRK_POP_WARN, why, 0);
        os_time_dly_ms(1500);
    }
}

/* Index of the entry of vals[8] nearest to v (a stored value may be any, e.g. 1..60); also krk_vox.c's VOX delay. */
int krk_nearest8(const u8 *vals, unsigned v)
{
    unsigned d, bd = 0xffff;
    int i, best = 0;

    for (i = 0; i < 8; i++) {
        d = v > vals[i] ? v - vals[i] : vals[i] - v;
        if (d < bd) {
            bd = d;
            best = i;
        }
    }
    return best;
}

static int min_get(void)
{
    return krk_nearest8(min_vals, cfg_min(krk_cwid_cfg()));
}

static void min_set(int idx)
{
    krk_cwid_cfg_set((krk_cwid_cfg() & CFG_ON) | min_vals[idx & 7]);
}

static const struct krk_opt sw_opt = { .title = "CW ID SW", .labels = krk_onoff_labels, .n = 2, .get = sw_get, .set = sw_set, .after = sw_after };
static const struct krk_opt min_opt = { .title = "Interval", .labels = min_labels, .n = 8, .get = min_get, .set = min_set };

const char *const krk_cwid_kid_labels[KRK_CWID_KIDS] = { "CW ID SW", "Interval", "Callsign" };

/* Child `child` of the CW ID parent: its option level, or the callsign editor (a screen). NULL: none. */
const struct krk_screen *krk_cwid_kid(unsigned child)
{
    static const struct krk_screen sw_row = { .label = "CW ID SW", .opt = &sw_opt };
    static const struct krk_screen min_row = { .label = "Interval", .opt = &min_opt };

    if (child == 0)
        return &sw_row;
    if (child == 1)
        return &min_row;
    if (child == 2)
        return &krk_callsign_screen;
    return 0;
}

/* Standby tags (krk_tags.c): 1 while the ID timer runs (switch on, callsign set, analog channel). */
int krk_cwid_active(void)
{
    char call[16];

    return idle_reason(call) == 0;
}
