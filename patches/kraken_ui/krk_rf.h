/*
 * krk_rf.h - RX retune, PLL-lock wait, RSSI read and microsecond timing shared by the RF Lab
 * (P5) and the band scope (F5). Recipe and evidence: docs/retune-for-scope.md.
 * RX only: nothing here touches the TX path. Call krk_rf_restore() when done.
 */
#ifndef KRK_RF_H
#define KRK_RF_H

#include "krk_stock.h"


#define KRK_RF_REG_RSSI 0x67
#define KRK_RF_MAX_POLLS 20000  /* cap on every polling loop, in case the timer reads stuck */

/* Microsecond stopwatch on the 100 Hz OS tick timer (sample it at least every 10 ms). */
void krk_tm_start(void);
u32 krk_tm_us(void);
u32 krk_tm_counts_per_us(void);

/* Retune RX to f10 (10 Hz units): reg 0x30 := 0, REG_38H/39H, then the stock filter/RXON tail. */
void krk_rf_retune(u32 f10);
/* Polls REG_0CH bit 9 (pll_lock) up to timeout_us. Returns microseconds to lock, 0 if the bit
 * was already set on the first read (possibly stale), or -1 on timeout. */
int krk_rf_wait_lock(u32 timeout_us);
u32 krk_rf_rssi_raw(void);
/* Re-applies the channel's own RX state with stock channel_change_apply_squelch(). */
void krk_rf_restore(void);
/* Mutes RX audio (stock fd6818b_reg47_afout_clear_mode); krk_rf_restore() unmutes. */
void krk_rf_mute(void);
int krk_rf_channel_is_digital(void);
/* Band limits (10 Hz units) for in-band sweeps; 0 if f10 is in neither band. */
int krk_rf_band(u32 f10, u32 *lo, u32 *hi);

#endif
