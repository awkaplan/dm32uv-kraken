#pragma GCC optimize ("Os")
/*
 * krk_aprs_alert.c - F14b APRS RX alert (issue #5, docs/aprs-rx-monitor.md section 5). With RX Monitor set to "Alert",
 * a packet decoded at standby shows the stock popup box with the station and the start of its text, as the APRS RX
 * list shows them (the newest ring entry):
 *
 *     (i)  N0CALL-9
 *          !4140.5
 *
 * Receive only: nothing here can key the transmitter. Writes: the LCD (stock popup primitives) and one stock UI event.
 *
 * Who runs what. krk_aprs_alert_frame() is called by the decoder's harvest (krk_aprsrx.c) after every decoded frame went
 * into the station ring, in the background task "init" (priority 30) when the RX Monitor decodes, or in "task menu" while
 * the APRS RX screen is open (then it returns at once: the owner is looking at the list). It only marks a notice pending.
 * krk_aprs_alert_tick() runs on every pass of the same background loop (krk_periodic.c krk_hook_bg_tick, one pass per
 * 10 ms OS tick), so the pending notice, the ring entry it shows and the tick never race.
 *
 * Drawing. The tick draws the popup itself, only when ALL of these hold: the stock screen-mode byte says standby (0 or
 * 0x5b, as the standby tags), no key is held (the stock poll's confirmed key 0x392c4), not keyed, PTT up, and "task menu"
 * is idle: it is the one task pending on its own UI queue (keymsgq) and that queue is empty. Task menu draws every stock
 * screen and runs every Kraken screen (a screen, a channel load, a menu, a stock popup keep it busy); while it waits on an
 * empty queue it is not drawing, and the background task outranks it (30 vs 31), so it cannot start a redraw before the
 * popup is complete. A Kraken send keys the transmitter (not keyed covers it).
 *
 * Clearing. After AL_SHOW, or when Back is released, the tick posts stock UI event 100 (write_field_and_mbox_post(1, 100):
 * what stock posts at the end of every call and TX, and from the Menu Exit timer) under the same gates; task menu then
 * redraws standby itself. Any other screen change (a key that opens a menu, a call, PTT) replaces the popup by stock's own
 * redraw; the tick sees the mode byte leave standby and forgets the popup. Keys and PTT are never taken away from stock.
 *
 * Rate limit. At most one notice per AL_GAP, the newest station wins; a pending notice not shown within AL_STALE is
 * dropped (e.g. the owner was in a menu); a station that had a notice within AL_REPEAT seconds gets none (digipeated
 * copies, short beacon intervals). All times are OS ticks: under stock power save the tick can run ~2x fast
 * (docs/fm-send-ram-tx.md section 6), which at worst halves each of them; nothing else depends on the rate.
 *
 * State: 24 bytes in .bss.aprs, RAM only, off at power-on (a magic word, as the Monitor's). The RX Monitor option row
 * (krk_aprsrx.c mon_set) switches it with the Monitor: Alert = Monitor on + this.
 */
#include "krk.h"
#include "krk_stock.h"

#define AL_MAGIC   0x414c5254u              /* "ALRT": Alert on */
#define AL_IDLE    0
#define AL_SHOWN   1
#define AL_DELAY   50u                      /* ticks after the frame: the carrier's tail and stock's end-of-receive redraw */
#define AL_STALE   1000u                    /* a notice not shown within 10 s is dropped */
#define AL_SHOW    500u                     /* on screen 5 s */
#define AL_GAP     800u                     /* at least 8 s from one notice to the next */
#define AL_REPEAT  30000u                   /* no second notice for the same station within 5 min of its last */
#define EV_STANDBY 100                      /* stock UI event: back to standby (docs/menu-exit-timer.md) */

#ifndef KEY_CUR                                                     /* tests/test_aprs_alert.py supplies host versions */
#define KEY_CUR      (*STOCK_KEY_CUR)                               /* 0x392c4: the stock poll's confirmed key, 0 = none */
#define UI_Q         ((volatile const u8 *)KEYMSGQ_PTR)             /* task menu's OS_Q (0x38d70) */
#endif
#define UI_Q_WAITERS (*(volatile const u16 *)(UI_Q + 0x10))         /* OS_Q.PendList.NbrEntries: tasks pending on it */
#define UI_Q_MSGS    (*(volatile const u16 *)(UI_Q + 0x2a))         /* OS_Q.MsgQ.NbrEntries: events queued */

#ifndef AL_BSS                              /* the host test defines it away */
#define AL_BSS __attribute__((section(".bss.aprs")))               /* claim kraken_aprs_state */
#endif

/* 24 bytes. Valid only while magic == AL_MAGIC (SRAM is not zeroed at power-up). */
static struct {
    u32 magic;
    u32 t_frame, t_shown;                   /* OS_TICKS of the pending frame and of the last notice */
    const char *call, *info;                /* the newest station ring entry (krk_aprsrx.c ring[0]) */
    u16 last_h;                             /* callsign hash of the last notice */
    u8 st, pend;
} A AL_BSS;

const char *const krk_mon_labels[3] = {"Off", "On", "Alert"};    /* the RX Monitor option level (krk_aprsrx.c mon_opt) */

int krk_aprs_alert_on(void)                 /* Alert is set (the caller checks the Monitor) */
{
    return A.magic == AL_MAGIC;
}

void krk_aprs_alert_set(int on)
{
    A.magic = 0;
    if (!on)
        return;
    A.st = AL_IDLE;                         /* t_shown and last_h may stay noise: at worst one notice waits AL_GAP or is skipped */
    A.pend = 0;
    A.magic = AL_MAGIC;                     /* last: the tick sees initialised state */
}

/* After a decoded frame went into the station ring: call and info are its entry's strings (always the newest, ring[0]). */
void krk_aprs_alert_frame(const char *call, const char *info)
{
    if (A.magic != AL_MAGIC || krk_screen_owner == KRK_SCR_MAGIC)
        return;
    A.call = call;
    A.info = info;
    A.t_frame = OS_TICKS;
    A.pend = 1;
}

/* Stock standby is on screen, no key held, not keyed, PTT up, and task menu waits on an empty queue: a Kraken screen (the
 * runner blocks task menu), a channel load, a menu, a stock popup or any redraw keep it busy. A Kraken send keys (TX_UP). */
static int ui_free(void)
{
    u8 m = UI_MODE_BYTE;

    return (m == 0 || m == 0x5b) && !KEY_CUR && !(CALL_ACTIVE & TX_UP) && !(ptt_pin_down() & 1) && UI_Q_WAITERS && !UI_Q_MSGS;
}

/* Every pass of the stock background loop (about every 10 ms). */
void krk_aprs_alert_tick(void)
{
    u32 now = OS_TICKS, h = 5381;
    const char *p;

    if (A.magic != AL_MAGIC)
        return;
    if (KEY_CUR == SCAN_BACK && A.st == AL_SHOWN)
        A.t_shown = now - AL_SHOW;          /* Back while the notice is up: clear it once Back is released */
    if (!ui_free())
        return;
    if (A.st == AL_SHOWN) {
        if (now - A.t_shown >= AL_SHOW) {
            write_field_and_mbox_post(1, EV_STANDBY);   /* task menu redraws standby */
            A.st = AL_IDLE;
        }
        return;
    }
    if (!A.pend || now - A.t_frame < AL_DELAY || now - A.t_shown < AL_GAP)
        return;
    A.pend = 0;
    for (p = A.call; *p; p++)
        h = h * 33 + (u8)*p;
    if (now - A.t_frame > AL_STALE || !A.call[0] || ((u16)h == A.last_h && now - A.t_shown < AL_REPEAT))
        return;                             /* waited too long (a menu was open), or the same station again: drop it */
    krk_popup_draw(KRK_POP_INFO, A.call, A.info);
    A.last_h = (u16)h;
    A.t_shown = now;
    A.st = AL_SHOWN;
}
