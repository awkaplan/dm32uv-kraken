#pragma GCC optimize ("Os")
/*
 * krk_aprs_chan.c - F8b Beacon Chan (issue #4): send the analog APRS beacon on a set memory channel, then come back.
 * docs/aprs-submenu.md section 7.6 has the evidence; the short version (addresses DM32.00.01.048):
 *
 *   Stock's DMR "Report Channel" already switches channel for a beacon and returns. gps_display_refresh_and_notify
 *   @0x03062510 calls ui_event_notify_alt(0x16) @0x030305ac when the report channel (u16 at 0x0004007a) is set; the event
 *   makes the CodeSw task run channel_switch_and_screen_refresh @0x03032024 with reason 0x16, which loads memory channel
 *   *(u16*)0x4007a through channel_load_synchronized -> channel_change_apply_dispatcher (full apply: tones, power, band
 *   flags, calibration, the live TX word; no redraw, no flash write) and waits for it to finish. When the data call ends,
 *   stock posts ui_event_notify_alt(0x17), which reloads the selected channel (memory or VFO) from the settings.
 *   We do the same for the AFSK beacon: 0x4007a holds our channel only for the 0x16 load, 0x3e198 (the last loaded channel
 *   number, read by the reload when 0x3fd2c bit 1 is set) is put back before the 0x17 return.
 *
 * Gates before the switch: setting valid; no menu (automatic beacon); VOX/repeater off; no send or
 * transmission in progress (lock, taken with IRQs off). After the 0x16 load: the dispatcher ran with our number (it
 * stores it at 0x3e198 at the end of a load), the loaded channel is analog, may transmit (flags8 bit 3) and its TX is
 * inside the radio's bands (0x3e31b bit 5); otherwise nothing is keyed and the 0x17 return runs. Then RX is retuned to the
 * beacon channel (channel_change_apply_squelch @0x0300df08, as stock does after every load) for the busy check, and
 * krk_aprs_tx_send runs with all its gates. krk_aprs_chan_keep() is checked again in claim() and after TX comes up
 * (krk_aprs_tx.c): if anything reloaded the channel meanwhile (dual watch, a knob turn), the send is refused or unkeyed.
 * The 0x17 return runs on every path after the 0x16 load. While the switch holds the sender, claim() tells every other
 * send (CW ID, Burst Voice, a second beacon) "Busy".
 *
 * Setting: APRS > Beacon Chan, stock numeric entry, 0 = Off (today's behaviour), 1..4000 = memory channel number (the CPS
 * channel number, not the position in a zone). Stored in block 4 at 0xF80 (docs/settings-field.md); 00/FF = Off.
 */
#include "krk.h"
#include "krk_stock.h"

#define ui_event_notify_alt   ((void (*)(u32 reason))0x030305ac)  /* sets the reason, CodeSw reloads, waits for the end */
#define stock_rx_retune       ((void (*)(void))0x0300df08)        /* channel_change_apply_squelch: RX from 0x3846c */
#define event_queue_reset     ((void (*)(void))0x03054d80)        /* wakes a power-saving radio */
#define irq_save              ((u32 (*)(void))0x0001018c)
#define irq_restore           ((void (*)(u32 sr))0x0001019c)
#define REPORT_CHAN           (*(volatile u16 *)0x0004007a)       /* stock APRS cache +2: the DMR report channel */
#define OS_TCB_CUR            (*(volatile u32 *)0x00042500)       /* OSTCBCurPtr */
#define LAST_CHAN             (*(volatile u16 *)0x0003e198)       /* 0x3e144 + 0x54: number the dispatcher last loaded */
#define EV_REPORT  0x16
#define EV_RETURN  0x17
#define SETTLE_MS  200
#define CHAN_MAX   4000

#define REC_OFF    0xF80
#define REC_MAGIC  0x4e484342u          /* "BCHN" */
#define LOCK_MAGIC 0x4b434c42u          /* even */
#define ACT_MAGIC  (LOCK_MAGIC | 1u)

struct chan_rec {
    u32 magic;
    u16 ch, inv;                        /* inv = ~ch */
    u32 z0, z1;
};
typedef char chan_rec_is_16_bytes[sizeof(struct chan_rec) == 16 ? 1 : -1];

/* st: 0 idle, LOCK_MAGIC = the switch holds the sender, ACT_MAGIC (= LOCK_MAGIC | 1) = and the beacon channel is loaded.
 * tx: the live TX word the load set; owner: the task doing the switch. Magics: SRAM is not zeroed. */
struct krk_bchan { u32 st, tx, owner; } krk_bchan __attribute__((section(".bss.aprs")));   /* tests/aprs_chan_checks.py reads it */

int krk_aprs_chan_get(void)
{
    struct chan_rec r;

    flash_block_read(flash_block_id_resolve_locked(4), REC_OFF, &r, sizeof r);
    if (r.magic != REC_MAGIC || r.inv != (u16)~r.ch)
        return 0;                       /* CPS write (FF), factory reset (00), noise: Off */
    return r.ch;
}

static void chan_set(int v)
{
    struct chan_rec r;

    r.magic = REC_MAGIC;
    r.ch = (u16)v;
    r.inv = (u16)~v;
    r.z0 = r.z1 = 0;
    if (v != krk_aprs_chan_get())       /* unchanged: no flash write */
        flash_block_write(4, REC_OFF, &r, sizeof r);
}

static const struct krk_opt chan_opt = {.n = 4, .get = krk_aprs_chan_get, .set = chan_set, .max = CHAN_MAX};

const struct krk_screen krk_aprs_chan_screen = {
    .label = "Beacon Chan",
    .opt = &chan_opt,
};

/* claim() (every send: beacon, CW ID, Burst Voice) and fmram_send after key-up, krk_aprs_tx.c. 1 = keying allowed: no switch
 * in progress; or this task's switch has the beacon channel loaded and the radio is still on it (the live TX word the load
 * set, analog, may transmit, TX inside the bands). While the switch holds the sender every other send is told "Busy". */
int krk_aprs_chan_keep(void)
{
    return (krk_bchan.st | 1) != ACT_MAGIC || (krk_bchan.st == ACT_MAGIC && krk_bchan.owner == OS_TCB_CUR && TX_FREQ10 == krk_bchan.tx
                                       && !(VFO_FLAGS8 & 0xf8) && !(BAND_FLAGS & 0x20));
}

/* The beacon send of Upload Beacon, the programmable key and the interval tick. Beacon Chan Off: krk_aprs_tx_send unchanged.
 * Set: never falls back to the selected channel; returns -1 with *why when anything stops the switch. The callers have
 * already required an analog selected channel; TX inhibit and the busy channel are claim()'s, on the beacon channel. */
int krk_aprs_chan_send(const char **why, const char **kind, int automatic)
{
    unsigned ch = (unsigned)krk_aprs_chan_get();
    u32 sr;
    u16 save_rpt, save_num;
    int r = -1, took;

    if (!ch)
        return krk_aprs_tx_send(why, kind);
    *kind = "";
    if (krk_vox_tag() || (automatic && (krk_ui_in_menu() || krk_screen_owner == KRK_SCR_MAGIC))) {
        *why = "Busy";        /* a menu (automatic beacon) or VOX/repeater on */
        return -1;
    }
    *why = "Busy";
    sr = irq_save();                    /* the lock, taken atomically: no other send or transmission in progress */
    took = (krk_bchan.st | 1) != ACT_MAGIC && !krk_aprs_tx_busy() && !(CALL_ACTIVE & TX_UP);
    if (took) {
        krk_bchan.st = LOCK_MAGIC;
        krk_bchan.owner = OS_TCB_CUR;
    }
    irq_restore(sr);
    if (!took)
        return -1;
    event_queue_reset();
    if (!krk_on_selected_side(1500)) {
        *why = "Dual watch: retry";
    } else {
        save_rpt = REPORT_CHAN;
        save_num = LAST_CHAN;
        REPORT_CHAN = (u16)ch;
        LAST_CHAN = 0;                          /* the dispatcher writes the number it loaded here: proof the load ran */
        ui_event_notify_alt(EV_REPORT);         /* load the beacon channel; returns when the load is done */
        REPORT_CHAN = save_rpt;
        krk_bchan.tx = TX_FREQ10;
        krk_bchan.st = ACT_MAGIC;
        if (LAST_CHAN != ch || !krk_aprs_chan_keep()) {
            *why = "Analog channel only";       /* the beacon channel: the load did not run with our number, or digital / TX-forbid / out of band */
        } else {
            stock_rx_retune();                  /* listen on the beacon channel for the busy check */
            os_time_dly_ms(SETTLE_MS);
            r = krk_aprs_tx_send(why, kind);
        }
        krk_bchan.st = LOCK_MAGIC;
        LAST_CHAN = save_num;
        ui_event_notify_alt(EV_RETURN);         /* back to the selected channel, on every path after the load */
    }
    krk_bchan.st = 0;
    return r;
}
