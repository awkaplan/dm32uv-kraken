/*
 * krk_rf.c - see krk_rf.h. Addresses: docs/retune-for-scope.md.
 */
#include "krk_rf.h"

#define TMR_LOAD ((volatile u32 *)0x14000000)   /* TIMER.CH[0]: the 100 Hz uC/OS tick, down-counter */
#define TMR_CUR  ((volatile u32 *)0x14000004)

#define fd6818_set_frequency ((void (*)(u32 hi, u32 lo))0x0002b56c)
#define fd6818_filter_select_then_chipmode_normal ((void (*)(void))0x0002b5e4)
#define fd6818b_reg47_afout_clear_mode ((void (*)(void))0x0002b550)

#define REG_STATUS 0x0c
#define PLL_LOCK   0x200

static struct {
    u32 prev, acc, cpu;
} tm;

void krk_tm_start(void)
{
    u32 load = *TMR_LOAD + 1;

    tm.cpu = load / 10000;      /* LoadCount spans one 10 ms tick */
    if (!tm.cpu)
        tm.cpu = 1;
    tm.prev = *TMR_CUR;
    tm.acc = 0;
}

u32 krk_tm_us(void)
{
    u32 cur = *TMR_CUR, load = *TMR_LOAD + 1;

    tm.acc += tm.prev >= cur ? tm.prev - cur : tm.prev + load - cur;
    tm.prev = cur;
    return tm.acc / tm.cpu;
}

u32 krk_tm_counts_per_us(void)
{
    return tm.cpu;
}

void krk_rf_retune(u32 f10)
{
    fd6818_reg_write(0x30, 0);
    fd6818_set_frequency(f10 >> 16, f10 & 0xffff);
    fd6818_filter_select_then_chipmode_normal();
}

int krk_rf_wait_lock(u32 timeout_us)
{
    u32 t, polls = 0;

    krk_tm_start();
    if (fd6818_reg_read(REG_STATUS) & PLL_LOCK)
        return 0;
    do {
        t = krk_tm_us();
        if (fd6818_reg_read(REG_STATUS) & PLL_LOCK)
            return t ? (int)t : 1;
    } while (t < timeout_us && ++polls < KRK_RF_MAX_POLLS);
    return -1;
}

u32 krk_rf_rssi_raw(void)
{
    return fd6818_reg_read(KRK_RF_REG_RSSI) & 0x1ff;
}

void krk_rf_restore(void)
{
    channel_change_apply_squelch();
}

void krk_rf_mute(void)
{
    fd6818b_reg47_afout_clear_mode();
}

int krk_rf_channel_is_digital(void)
{
    return (VFO_FLAGS8 & 0xf0) == 0x10;
}

int krk_rf_band(u32 f10, u32 *lo, u32 *hi)
{
    if (f10 >= 13600000 && f10 <= 17400000) {
        *lo = 13600000;
        *hi = 17400000;
        return 1;
    }
    if (f10 >= 40000000 && f10 <= 48000000) {
        *lo = 40000000;
        *hi = 48000000;
        return 1;
    }
    return 0;
}
