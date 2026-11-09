/*
 * krk_stock.h - the stock firmware's addresses that more than one patch source uses: functions, RAM variables and MMIO,
 * each defined once, here. A source that uses an address only itself keeps it next to that use. Names follow the stock
 * symbols (dm32uv-re) where one exists; scalars are `(*(volatile T *)addr)` lvalues, tables and buffers are pointers.
 * Included by krk_rf.h and by every source that needs these; krk.h does not include it.
 */
#ifndef KRK_STOCK_H
#define KRK_STOCK_H

#include "krk.h"

/* ---- MMIO -------------------------------------------------------------------------------------------------------- */
#define MODEM(off)      (*(volatile u32 *)(0x11000000u + (off)))   /* the modem/system register block (dmrlink calls it SYSCTL) */
#define FM_TX_HALF(n)   ((volatile u8 *)(0x16000030u + ((n) ? 0x200u : 0u)))      /* FM TX ping-pong half n */
#define FM_RX_HALF(n)   ((volatile const u8 *)(0x160004e0u + ((n) ? 0x200u : 0u)))   /* FM RX ping-pong half n */
#define UART0_THR       (*(volatile u32 *)0x14030000)   /* DesignWare 16550: transmit holding register */
#define UART0_LSR       (*(volatile u32 *)0x14030014)   /* line status; bit 5 = THR empty */
#define AUDIO_CONTROL   MODEM(0x080)    /* bit 0 tx_voice_source: 1 = the FM Send RAM feeds the FM modulator (docs/fm-send-ram-tx.md) */
#define FM_DEV_COEF     MODEM(0x504)    /* low byte fm_dev_coef_t: stock 0 on analog, the FM Send RAM sends use 0x40 */
#define FM_ADDR_SW      MODEM(0x56c)    /* FM TX/RX RAM ping-pong: 1 / 2 name the half to fill (TX) or the finished one (RX) */
#define GPIO_MIC_MUX    0x2e            /* low: the SoC MOD DAC onto the FD6818 MICIN instead of the mic */
#define GPIO_AF_SWITCH  0x2f            /* low: FD6818 AFOUT to the modem ADC (FM Receive RAM; docs/rx-audio-fm-mode.md) */

/* ---- stock functions -------------------------------------------------------------------------------------------- */
#define fd6818_reg_read  ((u32 (*)(u32 reg))0x0002c33c)
#define fd6818_reg_write ((void (*)(u32 reg, u32 val))0x0002c2f4)
#define gpio_set_output_bit   ((void (*)(u32 pin))0x0002c500)
#define gpio_clear_output_bit ((void (*)(u32 pin))0x0002c544)
#define OSFlagPost_wrapper_set ((int (*)(void *grp, u32 flags))0x0300c494)   /* with PTT_GROUP: posts the PTT press/release */
#define os_q_pend        ((void *(*)(u32 q, u32 timeout, short opt, u16 *size, u32 *ts, u32 *err))0x0306a1b8)
#define stock_rx_active  ((int (*)(void))0x0300f2a4)
#define tx_is_inhibited  ((int (*)(void))0x0302ed50)
#define dual_watch_state     ((int (*)(void))0x030641e4)    /* the engine's state, 0 = idle */
#define dual_watch_set_state ((void (*)(u32 s))0x030641ec)
#define ptt_pin_down     ((u8 (*)(void))0x03012f48)         /* stock: gpio_read_input_bit(0x2b) via IRAM 0x2c588, 1 = PTT pressed (pin low) */
#define stock_band_check ((void (*)(u32 rx, u32 tx))0x0300d84c)       /* frequency_band_range_check_and_status_set */
#define channel_change_apply_squelch ((void (*)(void))0x0300df08)
#define rtc_reg          ((u8 (*)(u32 reg))0x0301c2d4)      /* rtc_register_read: reg 0 = seconds, binary 0..59; a locked
                                                             * I2C read that stock makes from several of its tasks */
#define menu_option_list_dispatch ((void (*)(u32 mode, volatile u16 *count, volatile u8 *list))0x030493f8)
#define stock_set_mode   ((void (*)(u32 code, u32 flag))0x03035ec4)
#define history_queue_pop ((void (*)(void))0x03036c00)      /* stock menu-history pop (inverse of 0x0303699c); the pop that ends stock_setting_ok */
#ifndef flash_block_read             /* host tests substitute these (tests/test_band.py, test_callsign.py, test_cwid.py) */
#define flash_block_id_resolve_locked ((u32 (*)(u32 logical))0x0300f594)
#define flash_block_read  ((void (*)(u32 id, u32 off, void *dst, u32 len))0x0300f71c)
#define flash_block_write ((void (*)(u32 logical, u32 off, const void *src, u32 len))0x0300f878)
#endif

/* ---- stock RAM: clocks, queues, PTT ------------------------------------------------------------------------------- */
#define OS_TICKS         (*(volatile u32 *)0x000426d0)      /* uC/OS tick counter, 100 Hz awake, faster in power save */
#define KEYMSGQ_PTR      (*(const volatile u32 *)0x030131d8)   /* literal in keymsgq_command_dispatch_and_store: &queue */
#define PTT_GROUP        ((void *)0x0003a4f0)               /* event flag group of the PTT task */
#define CALL_ACTIVE      (*(volatile u8 *)0x0003fdc8)       /* bit 3 = keyed */
#define PTT_PRESS        0x08                               /* PTT_GROUP flags: the press and release events (TOT posts the release too) */
#define PTT_RELEASE      0x10
#define TX_UP            0x08                               /* CALL_ACTIVE bit 3 */
#define DW_COUNTERS      ((volatile u8 *)0x0003e38c)        /* +0x1c/+0x1d: engine counters, zeroed with state 3 */
#define MENU_EXIT_COUNTER (*(volatile u8 *)0x0003e396)      /* stock Menu Exit Time counter (docs/menu-exit-timer.md): 0 = "menu activity" */

/* ---- stock RAM: loaded channel and radio mode ---------------------------------------------------------------------- */
#define VFO_FLAGS8       (*(volatile u8 *)0x0003e1e4)       /* loaded channel flags: (& 0xf0) == 0 analog, 0x10 digital; bit 3: may not transmit */
#define RX_FREQ10        (*(volatile u32 *)0x0003846c)      /* the receiver's live RX frequency word, 10 Hz units (the loaded channel's own is 0x3e1dc) */
#define TX_FREQ10        (*(volatile u32 *)0x00038468)      /* and TX; krk_satrf.c retunes through these, krk_band.c checks them */
#define BAND_FLAGS       (*(volatile u8 *)0x0003e31b)       /* radio_mode_state + 3: bit 3 RX UHF, bit 4 TX UHF PA, bit 5 TX not allowed */
#define LOADFLAGS        (*(volatile u8 *)0x0003e31c)       /* + 4: bit 3 = channel load in progress (channel_change_apply_dispatcher) */
#define UI_BUSY_FLAGS    (*(volatile u8 *)0x0003e31f)       /* 0x3e318 + 7: bit 1 = stock audio busy (the DMR playback waits for it), & 0x18: stock skips the beacon */
#define SIDE_SELECTED    (*(volatile u8 *)0x0003e9e1 >> 7)  /* selected side, 0 A / 1 B */
/* Stock's screen-mode byte (the 4th byte of dialog_history_ui_state 0x320ac). NOT 0 at standby: the
 * "go to standby" event writes 0x5b (docs/menu-exit-timer.md 7). Menus and our screens are 7..0x3f,
 * plus 0x69 and 0x77 - the menu-exit timer's own gate, and the VOX engine's ui_in_menu (krk_vox.c),
 * which is hardware-validated. */
#define UI_MODE_BYTE     (*(volatile u8 *)0x000320af)
#define MENU_L2_ITEMS    ((const volatile u8 *)0x0003e664)  /* level-2 copy of the open sub-list's item numbers */
/* The menu history (history push 0x0303699c, docs/nested-submenu.md 1): the row chosen at depth d, and the saved index list of
 * each depth, so the ITEM chosen at depth d is MENU_SAVED_LIST(d)[MENU_ROW_AT(d)] (Channel Set row: d = 3, its child: d = 4). */
#define MENU_ROW_AT(d)   (((volatile u8 *)0x0003e4e8)[d])
#define MENU_SAVED_LIST(d) ((const volatile u8 *)(0x0003e61eu + 35u * (d)))   /* d = 1: 0x3e641, 2: 0x3e664, 3: 0x3e687, 4: 0x3e6aa */
#define MENU_ROW_ICON    (*(volatile u8 *)0x00032ab6)       /* list row icon: 1 numbered box (a sub-list), 2 radio mark (an option level) */

/* ---- stock RAM: DMR call, cipher, GPS, RSSI ------------------------------------------------------------------------ */
#define OWN_ID           (*(volatile u32 *)0x00033cd0)
#define STOCK_CIPHER     (*(volatile u8 *)0x00033824)       /* CPS key type of the loaded channel, 0 = none */
#define RX_TYPE          (*(volatile u8 *)0x0003bd2c)
#define RX_DSTCLASS      (*(volatile u8 *)0x0003bd2d)
#define RX_DST           (*(volatile u32 *)0x0003bd30)
#define RX_SRC           (*(volatile u32 *)0x0003bd34)
#define TXUP             (*(volatile u8 *)0x0003bd50)       /* bit 0 = DMR TX voice up, bit 1 = RX voice (docs/parrot-tx.md 6.1) */
#define STOCK_RSSI_MEAN  ((volatile u8 *)0x0003fde0)
#define GPS_STATUS       (*(volatile u8 *)0x00038c6c)       /* bit 6: valid RMC fix */
#define GPS_RMC          ((volatile char *)0x00038c79)      /* the RMC sentence copy (0x96 bytes) */

#endif
