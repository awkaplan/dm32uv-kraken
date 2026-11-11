/*
 * krk_periodic.c - one shared periodic hook for the 2026-10-29 features (F12 scrambler, F10 Doppler
 * tracking, ...). Splice maint_tick_hook (manifest) replaces `bsr 0x03068224` at 0x030547ae in
 * ui_periodic_maintenance_task_loop (stock call_hold_led_debounce_dual_timer_tick, void(void); r0-r3 are
 * dead after it, the loop reloads them) with `bsr krk_hook_maint_tick`. It runs on every pass of that
 * loop (docs/basic-privacy.md, docs/satellite-radio.md: about once a second, MED).
 *
 * Every feature tick called here runs in the maintenance task: 0x180-word stack, so integer-only, no
 * soft-float maths, no big locals, no drawing. Each one guards its own state with a magic word (patch
 * SRAM is not zeroed at power-up) and returns at once when its feature is off.
 */
#include "krk.h"
#include "krk_vframe.h"
#include "krk_afsk.h"

void krk_parrot_tick(void);

extern void krk_scramble_tick(void);            /* krk_scramble.c (F12) */
extern void krk_sat_tick(void);                 /* F10 Doppler tracking, blob kraken_ui3 (manifest defsym) */

/* F13 burst voice (blob kraken_ui4, built after this one): its TX watchdog is reached through a one-entry table at the fixed
 * address 0x030e8020 (kraken_ui4.ld); erased flash (blob not present) reads 0xffffffff and is skipped. */
#define BV_HOOK_TABLE ((void (*const *)(void))KRK_UI4_HOOK_TABLE)
static void bv_watchdog(void)
{
    void (*f)(void) = BV_HOOK_TABLE[0];

    if ((u32)f >= KRK_EXT4_LO && (u32)f < KRK_EXT4_HI)
        f();
}

/* F18 APRS Messages (blob ui4, krk_aprs_msg.c krk_msg_rx): every decoded frame goes through entry 1 of the same table
 * (0x030e8024, kraken_ui4.ld), range-checked as above. Strong definition of krk_afsk.c's weak hand-off. */
typedef void (*msg_rx_fn)(const char *line, const unsigned char *f, int len);
void krk_aprs_msg_frame(const char *line, const unsigned char *f, int len)
{
    msg_rx_fn fn = ((const msg_rx_fn *)KRK_UI4_HOOK_TABLE)[1];

    if ((u32)fn >= KRK_EXT4_LO && (u32)fn < KRK_EXT4_HI)
        fn(line, f, len);
}

#define stock_maint_led_tick ((void (*)(void))0x03068224)
#define stock_bg_prescaler   ((void (*)(void))0x030546dc)   /* ui_tick_prescaler_post, void(void) */

/* counts passes; read by the in-target tests and the About/diagnostic screens */
volatile u32 krk_maint_ticks KRK_FEAT;

void krk_hook_maint_tick(void)
{
    stock_maint_led_tick();                     /* the displaced stock call, first and unchanged */
    krk_maint_ticks++;
    krk_vf_service();                           /* voice-frame layer init + reboot detection */
    krk_parrot_tick();                          /* F9 watchdog first: re-posts PTT release past the TX deadline */
    krk_scramble_tick();                        /* F12: re-apply SCRAMBLE_REG */
    krk_sat_tick();                             /* F10: integer only, returns at once unless tracking */
    bv_watchdog();                              /* F13: re-posts PTT release if a burst send is past its deadline */
    krk_tags_tick();                            /* standby tags: redraw when the set changed (krk_tags.c) */
    krk_cwid_tick();                            /* CW ID: may key and block for the ID's length (~5 s), so last */
    /* feature ticks go here, one line each */
}

/* F14 RX Monitor. Splice bg_tick_hook replaces `bsr 0x030546dc` at 0x03013f48, the last call of every pass of
 * main_ui_background_task_loop @0x03013a3c (task "init", priority 30, TCB 0x00040da0, stack 0x40260..0x40da0 = 720 words;
 * every path of the loop body reaches 0x03013f40 and then this call, then `br32 0x03013a56` back to the OSFlagPend).
 * That loop pends flag 1 of group 0x0003fd90 and runs once per OS tick: measured in a booted QEMU run, 10 maintenance passes
 * (one per 100 prescaler counts) per 1000 OS ticks. The stock prescaler call is made first and unchanged (void(void); r0-r3
 * are reloaded by the loop head). The hook runs at loop level, so the stack in use is the loop's own frame (124 bytes at
 * the pend, measured) plus the monitor's; docs/aprs-rx-monitor.md has the numbers. */
void krk_vox_tick(void);                        /* krk_vox.c: sense/key state machine */
void krk_hook_bg_tick(void)
{
    stock_bg_prescaler();
    krk_mon_tick();
    krk_aprs_alert_tick();                      /* F14b: the standby notice (krk_aprs_alert.c) */
    krk_vox_tick();
}
