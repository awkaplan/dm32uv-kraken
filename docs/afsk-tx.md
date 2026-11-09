# AFSK (Bell-202, 1200 baud) TX on the analog channel: stock recipe, FD6818 tone generator, timing, risks

Status: sections 0-6 are static reverse engineering of DM32.00.01.048, written before anything was
transmitted. The hardware results from "Hardware result 2026-10-16" on show what happened on the air.
Every address was read from the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) corpus
or from the stock flash/IRAM images; pool-slot values were read from the image (a Ghidra `PTR_` name is the
literal-pool slot, not the table).
IRAM addresses are execution addresses (flash alias = addr + 0x030afc20 for 0x10000..0x2f098).
Confidence tags: [CONF] = two independent sources or datasheet + stock literal agree, [STRUCT] = read
from code but not cross-checked, [INFER] = inference, [OPEN] = not determined.

## 0. One-paragraph answer

- Key TX the way PTT does: post bit `0x08` to event-flag group `0x0003a4f0` (that is what the
  PTT-press event ends up doing); unkey by posting `0x10` (the TOT-expiry path posts exactly that).
  The analog TX-on work (`radio_init_sequence`) then runs in the PTT/event task, with every stock gate.
- Do NOT use `rf_tx_enable_sequence` (IRAM 0x2b440): in stock its only caller is the DMR RF-TX task
  and that task runs it only when the channel is digital. It is never reached on an analog channel.
- Tone generator: FD6818 REG_70H (enables + gains), REG_71H (tone1 word), REG_72H (tone2 word).
  Word = `round(f_Hz * 2^26 / 6.5e6)` = `f_Hz * 10.32444`: **1200 Hz = 0x3065 (12389), 2200 Hz = 0x58BA (22714)**.
  `tone1_gen=1` bypasses the MIC input (datasheet), so voice cannot mix in.
- One REG_71H write per bit is enough. Phase continuity is not stated in the datasheet; the formula is
  a 26-bit DDS (6.5 MHz clock, 16-bit word), so a word change is almost certainly continuous [INFER].
- Pace bits with a busy-wait on `TIMER.CH[0].CurrentValue` (0x14000004). One bit is exactly 70000 counts
  at 84 MHz, and CH0 reloads every 840000 counts = exactly 12 bits.
- Stock tone timing is NOT usable as a model: it is a 1 ms timer ISR posting an OS flag
  (millisecond granularity, task wake-up jitter).

## 1. Keying analog TX programmatically

### 1.1 What stock actually does (call chain, analog channel)

Digital vs analog test used everywhere: `vfo_display_state.flags_0x8 & 0xf0` (`p_vfo_display_state`
= RAM 0x0003e1dc, byte +8): `0x10` digital, `0x00` analog, `0x20`/`0x30` other. [CONF] (the digital
encryption code uses the same test; dm32uv-re [docs/encryption.md](https://github.com/awkaplan/dm32uv-re/blob/main/docs/encryption.md)).

PTT press, analog:
1. `keypad_debounce_and_commit_event` @0x03012f58 reads GPIO 0x2b (PTT line), 4-sample debounce, then calls
   a handler from the SRAM table at 0x18000834 (press = entry 1, release = entry 28 (offset 0x70)). [STRUCT]
2. The key task `ui_key_and_menu_event_dispatch_loop` @0x030152cc runs `call_setup_mode_flag_dispatch`
   @0x03014d34, which does `OSFlagPost_wrapper_set(0x0003a4f0, 0x08)` (PTT press event). [CONF] (decompile + literal)
3. `main_ptt_and_channel_event_loop` @0x03012d10 (the PTT/channel event task) pends on group 0x0003a4f0
   bits 0x18 (`OSFlagPend_wrapper_consume`). Bit 0x08 -> `ptt_press_handler` @0x0305bfb8,
   bit 0x10 -> release path. [CONF]
4. `ptt_press_handler` gates (see 1.3), then `tx_key_from_vfo_mode_guard` @0x0305be64. On an analog channel
   (nibble 0 or 0x20) that function ends with `radio_mode_entry_reinit_and_display` @0x0305baec. [CONF]
5. `radio_mode_entry_reinit_and_display` sets `*p_call_active_flag |= 0x08`
   (`p_call_active_flag` = RAM 0x0003fdc8), calls `modem_fm_ptt_set(1)` (SystemControl FM_PTT)
   then **`radio_init_sequence` @0x0300e40c = the analog TX-on**: `antenna_switch_select_gpio_24_22(0)`,
   config tables, `pa_band_select_vhf_uhf_gpio(1)`, `rf_channel_frequency_apply` (TX freq plus CTCSS/DCS
   code), `fd6818b_retune_and_reinit_sequence` @IRAM 0x2b674 (sets freq, `REG_0x30` 0 -> 0xC1FE = TXON,
   REG 0x37=0x1d0f, REG_52H=0x028f), then `FUN_0002b364(1,1,*(0x38498))` (PA DAC ramp, 40 steps). [CONF]
6. Still inside `radio_mode_entry_reinit_and_display`, the key-up signalling runs (PTT-ID):
   `field_0x16 & 0xe` == 6 -> `dtmf_tone_playback_from_i2c_code(1)` @0x03064950;
   == 8 -> `tone_gen_config_apply(0x180)` @0x03067cc8 (FD6818 FSK packet engine); == 2 -> mode notify.
   Then `FUN_0002b50c` (IRAM; = `REG_50H <- 0x3b18`, un-mute). [CONF]

PTT release / TOT, analog:
- Flag 0x10 on group 0x0003a4f0 -> `main_ptt_and_channel_event_loop` (if `*0x0003fdb8 & 2` is clear) ->
  if `*p_call_active_flag & 8`: `channel_switch_teardown_and_apply(0)` @0x0305fa38 (end-of-TX tone burst
  if configured, `REG_50H <- 0xbb18` mute via `FUN_0002b514`, `dac_channel2_write_from_byte(0)` (PA DAC to
  0), 30 ms delay, `pa_band_select_vhf_uhf_gpio(0)`, clears call_active bit 3, `channel_change_apply_squelch`
  back to RX). [CONF]
- TOT: `call_display_countdown_and_ui_event_trigger` @0x030556cc counts seconds; limit = `settings[0x35]*15` s
  (0 = off, returns early); warning `ui_event_notify(0x15)` at limit minus `settings[0x37]`; at expiry it
  does `OSFlagPost_wrapper_set(0x0003a4f0, 0x10)`, i.e. TOT uses the same release event. [CONF]
  (`settings` = `p_dm32_settings_block04` = RAM 0x0003e9b4.)

### 1.2 The functions people expected, and what they really are

| Name | Address | Finding |
|---|---|---|
| `rf_tx_enable_sequence` | IRAM 0x2b440 | Only caller `FUN_0300dc94` @0x0300dcce (bsr sweep of flash + IRAM, no other `bsr`/pool hit). That task does `os_sem_pend(*0x38590)` then only if `flags_0x8 & 0xf0 == 0x10`: request byte `*0x38464`==1 -> enable, else `rf_tx_disable_sequence`. The request bytes are set by the DMR time-slot ISRs `isr_time_slot_inter_tx/rx` (IRAM 0x2c42c / 0x2c3d4, store 1/2 at 0x38464/0x38465 and `os_sem_post`). **DMR only.** [CONF] |
| `rf_tx_disable_sequence` | IRAM 0x2b4a0 | Callers `FUN_0300dc94` @0x0300dcd8 and `FUN_0300dce4` @0x0300dd16 (waits flag 0x200 of group 0x0003fd90). DMR only. [CONF] |
| `tx_enable_caller1/2` | 0x0300e2c0 / 0x0305fbf8 | Not the TX-on. They set UI/audio state (`fd6818b_reg47_tx_bypass_or_rx_afout`, `audio_pa_enable_gate(1)`, `pending_request_set_code(1)`). Called from `tone_ptt_preamble_seq36` (end of PTT preamble tones). [STRUCT] |
| `tone_ptt_preamble_seq*` | 0x030224a4.. | Software PCM tone sequences played through the prompt-tone FIFO (HR_C7000 path `audio_ctrl_ring_play_set`), i.e. speaker/alert audio, not FD6818 modulation. Not a keying mechanism. [STRUCT] |
| `dtmf_*`, `tone_gen_config_apply` | 0x0306xxxx | They do not key TX. They run inside an already-keyed TX (called from `radio_mode_entry_reinit_and_display` for key-up PTT-ID, or from the DTMF keys during PTT). They end by re-issuing the TX chip state (`fd6818b_init_seq_filter_ctcss_chipmode` = REG 0x30 0 -> 0xC1FE), which is the proof they start from TX. [CONF] |
| `tx_rf_frontend_init_and_burst`, `FUN_03068380` | 0x03068450 / 0x03068380 | Local beeps: REG 0x30 <- 0x0302 (TX DSP + AFOUT only, no PLL/PA: datasheet "sidetone only" value 0x0202|...), tone1 0xC000. No RF. [CONF] |

### 1.3 State and gates the PTT path checks (all inherited if you post the PTT event)

- `tx_is_inhibited` @0x0302ed50 (returns 0 = TX allowed, analog branch): false only if
  `p_radio_mode_state[1].flags_0x2 == 0` (array of the struct at RAM 0x0003e318, element index as in the corpus), `p_radio_mode_state[2].flags_0x2 & 0x1c == 0`
  and `& 0x01 == 0` and `& 0x40 == 0`, byte `*(PTR 0x0302edf4)[1] == 0`, and `*p_call_active_flag & 8 == 0`
  (i.e. not already transmitting). Called from `ptt_press_handler` before keying. [CONF for logic, STRUCT for the meaning of each bit]
- `ptt_press_handler` also bails (with an on-screen message) for: radio-mode flags `0x7 & 0x18`,
  `(PTR 0x0305c284)[4] & 0x40`, and a long list of menu/dialog states (`get_current_mode_state1/2`,
  `p_menu_screen_state`, `p_tx_ptt_ui_state`). When the UI is inside a menu screen it takes the
  menu branches (e.g. `dtmf_digit_input_handler(6)`, quick-text dialogs), not the plain TX branch. [CONF]
  -> the patch must be back on the standby/VFO screen (or the screen it started from) before posting the event.
- Busy-channel lockout / TX disable / out-of-band: not located separately; they live in the bits tested by
  `tx_is_inhibited` and in `ptt_press_handler`. [OPEN: which bit is "busy lockout"]
- `radio_mode_state` (RAM 0x0003e318), `vfo_display_state` (0x0003e1dc), `p_tx_ptt_ui_state` (0x0003e0c0),
  `settings` (0x0003e9b4), `call_active` (0x0003fdc8), PTT group (0x0003a4f0), TX-forbid/ms flag group (0x0003fd90).

### 1.4 Can the menu task request TX on/off safely? Yes, by posting the PTT events (recommended)

```
/* preconditions, in the menu task */
if ((((uint8_t*)0x0003e1dc)[8] & 0xf0) != 0) refuse;     /* digital or other: never */
if (tx_is_inhibited()) refuse;                           /* 0x0302ed50, 0 = allowed */
/* leave the menu so ptt_press_handler takes the plain TX branch */
OSFlagPost_wrapper_set((void*)0x0003a4f0, 0x08);         /* 0x0300c494, r0=group r1=bits */
/* wait for TX up: call_active bit 3 */
while (!(*(volatile uint8_t*)0x0003fdc8 & 0x08)) { if (timeout 1-2 s) { abort; } OSTimeDly_ms_wrapper(10); }
OSTimeDly_ms_wrapper(100);   /* settle; stock DTMF PTT-ID uses its own pre-time (settings, x20 ms) */
... tone burst (section 2/3) ...
OSFlagPost_wrapper_set((void*)0x0003a4f0, 0x10);         /* release; same event TOT posts */
while ((*(volatile uint8_t*)0x0003fdc8) & 0x08) OSTimeDly_ms_wrapper(10);   /* TX down */
```

Why this and not calling `radio_init_sequence` yourself: `radio_init_sequence` is only the RF half.
`radio_mode_entry_reinit_and_display` also sets call_active, pending-request code, TX LED/icon, signal-meter
and dialog state that `tx_is_inhibited`, the TOT tick, the release path and the RX audio gating read.
Calling it directly (or `tx_key_from_vfo_mode_guard(1, 0, settings[0x53])`, which is what
`ptt_press_handler` calls for plain analog standby) is the alternative; it skips `ptt_press_handler`'s
gates, so call `tx_is_inhibited` yourself. [INFER: both work; the posted-event route is the one stock itself uses for TOT release]

Which task runs what: PTT press handling, `radio_init_sequence` and the key-up PTT-ID tone run in the
PTT/event task (`main_ptt_and_channel_event_loop`); the DMR RF-TX task `FUN_0300dc94` is idle on analog. [CONF]
The register-write mutex is the semaphore at the pool word of `fd6818b_reg_write` (value read through
IRAM 0x2c384), pended with timeout 0 (wait forever) inside `fd6818b_reg_write`; it makes writes from two
tasks safe against each other (other tasks also touch the chip, e.g. the REG_67H RSSI poll). [STRUCT]

## 2. FD6818 tone generator (datasheet + stock agree)

Registers (page 0, wire addresses; stock uses the same addresses 0x70/0x71/0x72) [CONF]:

| Reg | Bits | Meaning | Default |
|---|---|---|---|
| REG_70H | [15] tone1_gen, [14:8] tone1_gain, [7] tone2_gen, [6:0] tone2_gain | enables and per-tone gain | 0x7070 |
| REG_71H | [15:0] | tone1 frequency word | 0x2854 (=1000 Hz) |
| REG_72H | [15:0] | tone2 frequency word | 0x3065 (=1200 Hz) |

- Formula (datasheet p.4, p.40): `word = freq(kHz) * 2^26 / 6500` = `Hz * 10.324440615...`. Checks:
  default REG_71H 0x2854 = 10324 -> 999.98 Hz; default REG_72H 0x3065 = 12389 -> 1199.97 Hz; stock writes the
  literal `REG_72H = 0x3065` (tone_gen_config_apply @0x03067cc8) and loads the double 10.32444 from flash
  0x03076944 (`int_to_double_divide_convert` @0x0300df5c, calls with 800/1000/333/625/833 Hz).
  Resolution 0.0969 Hz/LSB. [CONF]
- **1200 Hz -> 0x3065 (12389); 2200 Hz -> 0x58BA (22714)** (error -0.03 / -0.01 Hz).
- "If tone1_gen=1, the MIC input is bypassed" (datasheet p.4). Tone path position: summed after the MIC
  DC filter, before the mute mux (REG_50H[15]), compander, pre-emphasis, limiter, LPFs. [CONF]
  So: MIC is silenced by `tone1_gen=1`; nothing else needed. Stock additionally turns MICIN ADC off
  (`REG_0x30` <- 0xC3FA: bit2 micin_en=0, bit9 afout_en=1 sidetone) for DTMF.
- Mute: `REG_50H[15]`. Stock values: **0x3B18 = unmuted (limit 0x318), 0xBB18 = muted**
  (`fd6818b_reg50_write_3b18` IRAM 0x2b5a8 / `_bb18` 0x2b598; `FUN_0002b50c` = 3b18, `FUN_0002b514` = bb18).
  Voice TX already runs with 0x3B18, so no change is needed to start the tone. [CONF]
- Level/deviation: the tone is scaled by `tone1_gain` (REG_70H[14:8]) then limited by REG_50H[9:0]; maximum
  deviation is REG_40H (dev_sh/dev_lvl, set per band by stock/cal; do not touch). Stock single-tone
  values: `REG_70H = 0xC000` (gain 0x40; local 1000/1750 Hz tone bursts), `0xE000` (gain 0x60; DTMF-from-flash
  single tone), `0xE0E0` (both tones gain 0x60; DTMF dual). The gain-to-Hz-deviation mapping is not
  documented. Start with 0x60 and calibrate by receiving the burst on a second receiver. [OPEN: absolute deviation]
- The tones pass through the compander and pre-emphasis (REG_9BH[0] bypass bit, page 1, not changed by
  stock tone code), so 2200 Hz comes out ~+4 to 5 dB over 1200 Hz (normal APRS "twist"; most decoders cope).
  If needed: bypass pre-emphasis for the burst (REG_9BH bit0 via page 1: `WRITE(0x7F,1)`, RMW, `WRITE(0x7F,0)`,
  the datasheet's own page-switch procedure; not done by stock, untested) or use the two-oscillator variant
  (below). [INFER]
- Phase continuity: not stated. The 2^26/6.5 MHz word makes it a DDS with a 26-bit accumulator; changing the
  word changes only the increment, so phase stays continuous unless the chip resets the accumulator on a
  write (nothing suggests that). [INFER, medium-high]. Two-oscillator alternative (no continuity, per-tone
  gain): keep tone1_gen=1 with gain 0 and toggle `REG_70H` between `0x8000|(g_mark<<8)` (tone1=1200 Hz in
  REG_71H) and `0x8000|0x0080|g_space` (tone2=2200 Hz in REG_72H); each is also one write.
- Write cost: `fd6818b_reg_write` (IRAM 0x2c2f4) = os_sem_pend, GPIO bit-bang of 8+16 bits, os_sem_post.
  The new value latches when CS (GPIO 0x18) goes high at the end of the call (measured total 52 us).
  Interrupts stay enabled in it. Start each write ~52 us before the boundary or accept a constant delay
  (only variance matters).

### Recipe A (minimal, no extra chip state)

After TX is up and settled:
```
fd6818b_reg_write(0x71, 0x3065);       /* start on mark (idle level before first bit, send flags) */
fd6818b_reg_write(0x70, 0xE000);       /* tone1_gen=1, gain 0x60: mic bypassed, tone on */
/* per bit boundary: */   fd6818b_reg_write(0x71, bit_is_mark ? 0x3065 : 0x58BA);
/* end */                  fd6818b_reg_write(0x70, 0x0000);   /* tone off, mic path back (stock cleanup does this) */
```
`fd6818b_reg_write` @IRAM 0x0002c2f4 (reg in r0, value in r1); read-back `fd6818_reg_read` @IRAM 0x0002c33c.
Order matters: set the word first, then enable, so the first sample is already mark (stock does 71H then 50H).

### Recipe B (stock-DTMF-faithful; use if A is silent or distorted)

Copy `dtmf_tone_playback_from_i2c_code` @0x03064950 minus the speaker gating:
`config_table_init_entries_0300f2cc` (REG 0x21=0x06fd, REG_24H=0x8c2f; the DTMF-dual variant uses 0x06d8 / 0x8c7e),
`fd6818b_reg47_write_byte_or_6042(3)` (REG_47H <- 0x6342), `REG_70H <- 0xE000`, `REG_50H <- 0xBB18`,
`config_table_init_group1` @0x0300e8ec (REG 0x37=0x1d0f, 0x52=0x028f, REG 0x30: 0 then 0xC3FA),
then per symbol `REG_71H <- word` + `REG_50H <- 0x3B18`. Skip `audio_pa_enable_gate(1)` (GPIO 0x35, speaker
amp) to keep the sidetone silent (REG 0x30 bit 9 only enables the AFOUT DAC; the amp gate decides audibility).
Cleanup as stock: `config_table_entry_0x30`, `fd6818b_reg47_write_byte_or_6042(0)`, `REG_70H <- 0`,
`config_table_entry_0300f2e4` (REG_24H <- 0), `fd6818b_init_seq_filter_ctcss_chipmode` (REG 0x30: 0 -> 0xC1FE,
back to normal TX). REG_24H is MCBSP/I2S input pad select per the datasheet (REG_21H undocumented); whether
tones need them is [OPEN].

## 3. Stock tone timing and what to use instead

- Stock DTMF/PTT-ID timing is millisecond-class and RTOS-driven: `mmio_14000000_timer_config(ms)` @0x0301d900
  programs **TIMER CH1** (0x14000014 LoadCount, 0x14000018 CurrentValue, 0x1400001c ControlReg; the corpus
  names it "ch0") for `ms * 84000` counts; its ISR (IRAM 0x2caa4, source 0x22) posts flag 0x80 on group
  0x0003fd90; `wait_for_status_byte_clear(n)` @0x03022e68 pends `n` times on that flag
  (`tx_forbid_flag_clear`), i.e. n ms, then `timer_ch0_disable` @0x0301d920 stops it. Inter-symbol
  silence = REG_50H mute + the same wait. Symbol lengths are `settings` bytes x 10/20/50 ms. No µs
  precision, task-wake jitter on top. [CONF]
- `FUN_0002e0d8(n)` (IRAM) = n x `busy_wait_delay_loop_4arg(1000)`; stock uses this CPU spin for beeps. [CONF]
- Timer map (load values from `hw_timer_counter_to_time_components` @0x0301d860 plus the live capture in
  dm32uv-re [docs/rtos-and-memory.md](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md); APB = 84 MHz, 1 count = 11.9 ns):

| CH | Base | Use in stock | Free for a patch? |
|---|---|---|---|
| 0 | 0x14000000 (Load 840000 = 10 ms, Cur +4, Ctl +8) | uC/OS tick (IRQ source 1/vector 33) | No: read it, never write it |
| 1 | 0x14000014 | tone/DTMF/preamble 1 ms flag timer (see above); idle LoadCount at boot clk/1000 | No while stock tones can run |
| 2 | 0x14000028 | 20 ms, IRQ 0x23 handler IRAM 0x2ca78 calls the function pointer at pool 0x2cb90 | Unclear; avoid |
| 3 | 0x1400003c | 1 us period (84), IRQ 0x24 posts flag 0x800 to group 0x0003fd90 (IRAM 0x2ca90) | No enable site found; unverified |
| 4 | 0x14000050 | 1 s, IRQ 0x25 handler IRAM 0x2ca88 only reads EOI | Best candidate (no-op ISR); verify ControlReg (0x14000058) bit0 on hardware first |
| 5 | 0x14000064 | 1 ms UI tick `timer5_ui_tick_isr` @IRAM 0x2cab8 | No |

  The `timer_chN_enable/_write_period/_read_status` wrappers @0x0301d754..0x0301d800 have zero callers in the
  corpus; "no enabling code found" for CH2..CH4 is a negative static result, not proof. [STRUCT/OPEN]
- Recommendation: busy-wait on **CH0 CurrentValue** (read-only, nothing to arbitrate). Bit period =
  84e6/1200 = **70000 counts exactly**; CH0 wraps every 840000 = 12 bits. Pseudocode:
  ```
  uint32_t last = CH0_CUR, acc = 0;            /* counts down, reloads at 840000 */
  for each bit k: target = (k+1)*70000;
      do { cur = CH0_CUR; acc += (last - cur + 840000) % 840000; last = cur; } while (acc < target);
      write_71H(next_word);
  ```
  (poll at much less than 10 ms; one wrap per poll at most). Cumulative targets mean no drift.
- Interrupts: leave them on; the tick (10 ms), CH5 1 ms tick and GPIO ISRs each steal tens of us, comparable
  to one register write (52 us); a 52 us skew is ~6% of a bit and is tolerated by Bell-202 demods.
  The risk is task preemption by a higher-priority task during the 150-300 ms burst. Options: do nothing and
  measure; wrap the burst in `OSSchedLock`/`OSSchedUnlock` (IRAM 0x00011190 / 0x00011250, uC/OS-III style
  taking `OS_ERR*`; keep it < ~300 ms; `fd6818b_reg_write`'s `OSSemPend` returns an error instead of
  blocking when the scheduler is locked, which is fine unless another task holds the bus semaphore at that
  instant). Do not disable IRQs for the whole burst (tick, TOT and flag posts would be lost). [INFER]
- Watchdog: HR_C7000 WDG @0x14010000 exists; no evidence in the repo that stock enables it
  (tools/include/hr_c7000_regs.h:323; Snake blocks on hardware indefinitely). 300 ms of spinning
  is far below the CPU-spin stock already does in `rf_tx_enable_sequence` (800 x 1000 loops) and tone paths. [STRUCT]

## 4. Risks

1. **Digital channel**: refuse unless `((uint8_t*)0x0003e1dc)[8] & 0xf0 == 0`. (Nibble 0x20/0x30 are other modes; also refuse.)
   Posting a PTT event on digital would start a DMR call.
2. **TX timeout**: TOT only counts when `settings[0x35] != 0`; a 300 ms burst is far inside any setting (15 s units),
   but the patch must always release in a `finally` path (post 0x10 even after an error, and re-check bit 3 of
   `*0x0003fdc8` clears). If the patch hangs after keying, TOT (if enabled) will release via the same 0x10 event.
3. **Auto Power / TX trim wrappers**: `0x0300dcce` (the call to `rf_tx_enable_sequence` inside `FUN_0300dc94`)
   is on the DMR path only; **on analog it never executes**. The analog PA ramp is
   `FUN_0002b364(1,1,*(uint8_t*)0x00038498)` inside `radio_init_sequence` (IRAM callee),
   fed by the byte `freq_calibration_apply_all` @0x0300db48 writes (via `freq_calibration_interpolate_selected`
   @0x0300db66), which `ptt_press_handler` triggers (`PTR 0x0305c294[0x32] & 1`). So a wrapper at 0x0300db66
   covers analog TX through the posted-PTT route; a wrapper at 0x0300dcce does not. If you call
   `radio_init_sequence` yourself you skip the calibration refresh and use whatever byte is in RAM. [CONF for call graph]
4. **Mixing voice**: only `tone1_gen=1` protects against it. Keep the mic off the air before key-up anyway
   (the operator can be talking when the menu action fires); the posted-PTT route has the radio keyed with the
   mic live for the ~100+ ms between TX-up and tone start. Mitigation: set `REG_50H <- 0xBB18` (mute) right
   after seeing call_active bit 3 and un-mute (`0x3B18`) when starting the tone; stock DTMF does the same
   (`bb18` before `3b18`). [INFER]
5. **State restoration**: tone cleanup must write `REG_70H <- 0` and leave `REG_50H = 0x3B18`/`REG_47H` as found
   (read `REG_47H` with `fd6818_reg_read` before Recipe B and write it back; stock's cleanup writes 0x6042 and relies
   on the following RX switch). After release, `channel_switch_teardown_and_apply` restores RX itself.
6. **Concurrency**: other tasks (squelch/RSSI REG_67H polling, rxvoice, UI) call `fd6818b_reg_write`; they serialise
   on its semaphore, so interleaved reads are fine, but a task holding the semaphore delays a bit write by up to
   its own write (~52 us).
7. **UI side effects**: the posted route makes the radio display and LED behave as a PTT (TX icon, bargraph,
   possible PTT-ID/DTMF at key-up if the channel is configured for it; those run BEFORE your burst, inside
   `radio_mode_entry_reinit_and_display`, and delay the TX-up poll). On a channel with a tone-burst or
   end-of-TX tone setting (`settings[0x44] & 0xc`), `channel_switch_teardown_and_apply` plays it after your burst.
8. **Legality**: not an engineering issue but a note: the channel's own gating (band edges, TX inhibit) is
   what `tx_is_inhibited`/`ptt_press_handler` enforce; do not bypass it.
9. **Sidetone**: Recipe A leaves `REG 0x30 = 0xC1FE` (afout_en=0), so no sidetone. Recipe B sets 0xC3FA
   (afout on); keep GPIO 0x35 (`audio_pa_enable_gate`) low if silence is wanted.

## 5. Evidence index

- Datasheet: FD6818 programming guide rev 1.0.0 (Chinese; line numbers of a Markdown conversion)
  lines 186-201 (tone gen), 1456-1520 (DTMF/SELCALL TX flow: mute -> set tones -> delay -> unmute),
  1950-2030 (beep/call-tone, formula), 2726-2735 (REG_03H bits: txon [1], micin_en [2], afout_en [9]),
  3146-3153 (REG_70H..72H defaults), 80-130 (TX block diagram; tones summed after DC filter, before mute/compand/pre-emphasis).
- Wire address note: the datasheet calls the enable word REG_03H; stock writes the same values (RX 0xBFF1,
  TX 0xC1FE) to wire address 0x30 on the FD6818B. Use stock helper functions, not raw 0x03.
- Stock code: corpus files under [dm32uv-re](https://github.com/awkaplan/dm32uv-re) `src/`: `radio-state/radio_init_sequence_0300e40c.c`,
  `radio-state/radio_mode_entry_reinit_and_display_0305baec.c`, `rf-frontend/tx_key_from_vfo_mode_guard_0305be64.c`,
  `input-keypad/ptt_press_handler_0305bfb8.c`, `radio-state/main_ptt_and_channel_event_loop_03012d10.c`,
  `dmr-protocol/call_setup_mode_flag_dispatch_03014d34.c`, `unclassified/named-unmatched/call_display_countdown_and_ui_event_trigger_030556cc.c`,
  `codeplug/channel/channel_switch_teardown_and_apply_0305fa38.c`, `analog-signaling/dtmf_tone_playback_from_i2c_code_03064950.c`,
  `analog-signaling/dtmf_compand_config_apply_03063cf4.c`, `unclassified/unnamed/FUN_0300dc94_0300dc94.c`,
  `rf-frontend/rf_tx_enable_sequence_0002b440.c`, `rf-frontend/tx_is_inhibited_0302ed50.c`.
- Caller claims come from a `bsr` sweep over flash + IRAM (every computed `bsr` target matched against the
  function entry).

## 6. Open questions / verification plan (no transmission needed)

1. Does posting 0x08 from the menu task, after exiting to standby, actually reach the plain TX branch on
   an analog channel? Verify under QEMU (`-M dm32`) by watching the call_active byte 0x0003fdc8 bit 3 and the
   `fd6818b_reg_write` log (REG 0x30 0xC1FE write, REG_50H 0x3B18); QEMU has no RF, which is safe.
2. Does a REG_71H write reset the tone phase? Datasheet silent. Bench idea: dummy load + a separate SDR
   receiver, or look at TX audio on the AFOUT sidetone with Recipe B
   (0xC3FA) and the speaker amp enabled: a phase reset would show as clicks in a scope on AFOUT.
3. Tone gain to deviation, and whether mark/space need different gains (pre-emphasis twist).
4. Which radio_mode_state bit is the busy-channel lockout, and the exact meaning of the `tx_is_inhibited` bits.
5. Whether TIMER CH2..CH4 are ever enabled at runtime (read ControlReg bit 0 on hardware via the live-RAM tool).
6. Whether `OSSchedLock` around the burst is needed (measure bit-time jitter with CH0 timestamps logged to RAM).
7. REG_21H/REG_24H/REG_47H=0x6342 roles in stock DTMF (Recipe B extras): needed for the tone to be modulated, or only for the sidetone?
8. Datasheet formula uses a 6.5 MHz reference; stock's literal 0x3065 = 1200 Hz matches, so the DM-32 crystal
   matches the datasheet assumption for this block (no per-unit crystal correction found on the tone path).

## Hardware result 2026-10-16 (capture3, one radio sending, a second radio receiving)

The sender (v16) sent the status beacon three times; the receiving radio captured the FM audio (`out/capture3.wav`).
Each burst lasted 0.33-0.39 s at full level. The frame is 834 bits (40 + 3 flags, 61-byte status
packet), 0.695 s at 1200 baud, and 834/2400 = 0.347 s: **the bits went out at 2400 baud.** The v16
pacing took `(LoadCount+1)/12` counts per bit, i.e. it assumed TIMER.CH[0] reloads every 10 ms.
Whatever the real tick is, the fix in v17 does not assume it: `counts_per_ms()` measures the timer
against UART0 (115200 baud: 40 bytes back-to-back, THR refilled on THRE, first write to TEMT =
3.472 ms) at every send, and the screen shows the value (`tmr N c/ms`, expected ~84000 if the
timer runs at 84 MHz, which would mean a 5 ms tick, or ~168000). Test keys 1-4 send 1 s of steady
mark / steady space / alternating bits / 8-bit runs for a frequency and timing check in capture4.
Tone frequencies could not be judged from capture3 (half-cycle bits smear the zero crossings).

## Hardware results 2026-10-16, captures 4-8 (two radios, the receiver capturing with squelch 0)

- **Tones**: steady REG_71H words give 1204 / 2200 Hz at the receiver (capture4). Correct.
- **Receiver squelch**: at squelch level 3 the capture started ~0.3 s after the carrier, eating the
  267 ms preamble (capture5); v21 sends 72 flags (480 ms) and the tests run with squelch 0.
- **Retune method (one DDS, REG_71H per bit) cannot do 1-bit runs.** 4-bit runs show a glitch
  cycle at every change (the word write restarts/slews the generator); the alternating-bit test
  pattern comes out as a wobble between ~800 and ~1600 Hz with no energy at either tone
  (capture6/8). Frames fail exactly in the control/PID bytes, the longest alternating runs.
- **Dual-tone method works**: tone1 = 1200 Hz and tone2 = 2200 Hz both enabled, only REG_70H
  gains switched per bit. tone2 alone produces nothing (datasheet: the MIC bypass/dual tone needs
  tone1_gen = 1; capture7). The alternating pattern then shows both tones at equal strength
  (capture8); each switch is a phase jump the receiver rings on for a cycle. v25 crossfades
  through both-at-half-gain (one extra 52 us write) and uses gains 0x58 / 0x7F.
- **Bit rate**: the UART-timed calibration of the tick timer varied 83302..87162 counts/ms between
  sends, so frames went out at 1143..1208 baud (flag blips 52.7..56 samples apart) and the receiver
  decoded seven ones per flag. v25 takes the minimum of six measurements (interrupts only add
  time). The decoder's bit clock is now a second-order loop (err/4 phase, err/1024 rate, +-8%)
  that tolerates +-3% on synthetic signals.
- **Levels/twist** with the dual-tone method: mark 96 RMS at gain 0x30, space 134 at 0x60 (the
  retune method's single tone at 0x60 gave 442), so the two gain fields do not scale alike.
- **Future TX path**: the HR_C7000 has an FM Send RAM (base 0x16000000, FM_VOICE_1 at +0x030 and
  FM_VOICE_2 at +0x230, 256 PCM samples each, ping-pong on FM_ADDR_SW, FM_TX_INTERP interrupt).
  Software AFSK through it would be phase-continuous with any pre-emphasis we like, through the
  FD6818 in bypass-TXON (REG_47H 0x6740, the DMR 4FSK path). Not yet explored.

## Hardware results 2026-10-16, captures 9-11: the FD6818 cannot make Bell-202 tones by register

- **Gain switching between two running generators is ramped too** (capture10): the alternating-bit
  pattern arrives as the pure sum of two steady lines at 1200 and 2200 Hz with no switching
  sidebands, i.e. at 1200 switches/s the gains just sit near half. High gains (0x58/0x7F) add a
  600 Hz square-wave component (capture9, limiter); the crossfade changes nothing.
- **The FSK packet engine is baseband FSK** (capture11): 64 words of 0x5555 produced a 300 Hz
  square wave (the bit pattern as direct carrier shift, 600 baud for rate word 0x3065, so the rate
  formula is half the tone formula) plus a steady ~1488 Hz line (wire 0x58 = 0x3c03 looks like a
  tone word for a pilot), no 1200/2200 Hz. The engine's length field counts bytes (as the stock
  driver uses it), and its "finished" flag never reached REG_0CH bit 0 in our sequence.
- **The receive chain smears single bits even for a clean source**: the phone-sourced packet of
  capture2 (phase-continuous AFSK into the other radio's mic) scores 24% mid-bit errors on 1-bit
  tone runs, ~5% on 2-bit runs, ~0% beyond (out/gt_analysis.py). Digital pre-emphasis in the
  decoder cuts that to 16%. The receiver's voice filters (de-emphasis, LPF1/LPF2, the FIR after
  the demodulator that narrows to 1.7 kHz on weak signals) are the suspects; v28/v29 expose them
  as f0..f3 and the key-3 auto sweep.
- Conclusion for F8: generate AFSK as PCM through the HR_C7000 FM Send RAM (docs/fm-send-ram-tx.md
  when written). Conclusion for B2: either a raw-discriminator receive mode (docs/fd6818-disc-mode-rx.md)
  or a decoder that equalises the smearing.

## v31: method 3, software AFSK through the HR_C7000 FM Send RAM (untested on hardware)

Recipe and evidence: docs/fm-send-ram-tx.md. Beacon key 7 in `ram` mode runs the probe plan in one
go (40 Hz DAC test, steady 1200/2200, alternating, frames at fm_dev_coef_t 0x10 and 0x40, the other
half-switch convention, steady 1200 at 0x40). Line 6 reports `ram ok h<halves> sw<n>` or `ram STALL`
(no FM_ADDR_SW change in 300 ms = the FM TX engine is not consuming RAM in this configuration).

## Hardware result 2026-10-27, capture14 (v36, sender key 7 in `ram` mode -> receiver `0` stream): method 3 works

The FM Send RAM path modulates the carrier. All 8 probe steps ran (no STALL): the 40 Hz DAC test,
steady 1200 (measured 1200.00 Hz, RX RMS 62 at dev 0x10, 266 at dev 0x40), steady 2200 (2198.8 Hz,
RMS 21: net 2200/1200 twist -9.5 dB at the receiver), alternating, three frames, steady 1200 at 0x40.
Every frame aligns with the expected tone sequence at rate 1.000 (`out/gt_analysis.py`): the first
transmitter of this project at exactly 1200 baud. Bits 0..1073 (72 flags, data, FCS) are bit-exact
on the air: with the two weakest bits corrected the reference front end's decisions pass the FCS
(`out/c14/hdlc_check.py`). Two defects, both on the transmit side:

1. **Closing flags cut off.** Bits 1074..1097 (the 3 post flags) are missing in every frame: the
   sender left the loop at `tail == 2`, i.e. at the switch that *starts* playing the half holding
   the end of the stream, and stopped the source at once. The receiving radio's live decoder and the offline
   decoder both framed the packets and ended them on noise: `x3` (three bad-FCS frames) on the
   screen, `badfcs 1` per burst offline. Fix (v37): `tail < 3`.
2. **No pre-emphasis.** REG_47H = 0x6740 bypasses the radio's own voice processing, so the 2200
   tone leaves at the same amplitude as 1200 and arrives 9.5 dB weaker. The eye at the reference
   front end (W8, pre 0.9, smooth 3) is open only between sampling phases 0.4 and 0.6 of the bit,
   and the C decoder's bit clock sits at 0.5-0.8: 20 HDLC-bit errors per frame. With the capture's
   2200 band raised 1.8x (what standard pre-emphasis would do) the eye is open from 0.3 to 0.7 and
   the C decoder's HDLC input is error-free on both frames (`out/c14/htrace_*.txt`, fr40 and fr10).
   Fix (v37): the generator takes a space amplitude; default 14600 against mark 8000 (x1.83,
   +5.3 dB). Key 7 now sends six frames: dev 0x40 at x1 / x1.83 / x3, then x1.83 at dev 0x10,
   0x20, 0x80. The old probe steps (40 Hz test, steady tones, alternating, the other half
   convention, which was worse: 13.7 % vs 8.2 % sample error) are gone.

Decoder-side changes were tried and rejected on the real capture: space gain 1..16, pre-emphasis
on/off, window 6/8, space delay 0/3/5, equaliser on/off, AGC on/off (`out/c14/profgrid.sh`): no
combination decodes the -9.5 dB frames, all of them are fine once the transmitter pre-emphasises.
Artifacts: `out/capture14.wav`, segments `out/c14_*.wav`, analysis in `out/c14/`.

## Hardware result 2026-10-27, capture16 (v37): closing flags fixed, raised space tone made it worse, decoder re-fitted

All six v37 frames are in the capture, each followed by quiet carrier: the closing flags now go out
(0-2 of 24 bits wrong per frame, data bit-exact). The transmit twist did not work the way it should:
space RMS at the receiver fell as the space amplitude rose (dev 0x40: 163 at 8000, 142 at 14600,
122 at 24000; net twist -4.1 / -6.7 / -8.2 dB), the mark lead was identical in all three (267) and the
space level did not decay along the preamble, so this is not a compressor but the receiver running
out of IF bandwidth at the larger 2200 Hz deviation (dev 0x80 with 8000/8000 broke the eye entirely).
v38 therefore keeps space at 8000 and sweeps a LOWER mark instead (key 7: dev 0x40 at mark 8000 /
4400 / 2700, then 0x60 and 0x20 at 4400, 0x30 at 8000); key 8 sends the default frame 28 times
(~56 s) for the APRS RX screen's key-3 receive sweep.

Receiver: with ideal timing the 8-sample front end still had 2-3 errors per frame at the best phase,
a 9-sample one 0-1 (`out/c14/fe_search.py`). On the radio's own C decoder (`out/c14/profgrid4.sh`)
window 8 with space delay 2 samples and EQ_A 3 decodes all five usable frames at space gain 2 (frame 1
at 2..9); it decodes none of the channel-model cases, and the model-fitted profile decodes none of
the real frames. Both are kept: `krk_afsk_set_preset(2)` = preset B (capture16 fit), selected by the
APRS RX screen at entry (header `RX 2F+fmplay pB`), key 5 toggles to preset A. tests/test_afsk.py
now carries capture16 frames 1 and 2 (tests/data/) and requires preset B to decode them.

## Hardware result 2026-10-27, capture17 (v38): first over-the-air decode on the radio

The receiving radio (APRS RX, `2F+fmplay`, preset B, space gain 2) displayed
`N0CALL>APZUNL,WIDE1-1,WIDE2-1:>DM-32UV Unleashed fm ram test` during the sender's key-7 sweep. Offline
on capture17 the same code decodes frames 1, 3, 4, 5 of 6 at gain 2; frame 1 (dev 0x40, 8000/8000)
at every gain 2..8. Per-frame receiver levels (mark RMS / space RMS, net twist, eye errors at the best
phase): f1 8000/8000 @0x40 260/163 -4.1 dB 4; f2 4400 @0x40 290/130 -7.0 dB 72 (misaligned, failed);
f3 2700 @0x40 276/111 -7.9 dB 38; f4 4400 @0x60 381/178 -6.6 dB 9; f5 4400 @0x20 139/67 -6.4 dB 2;
f6 8000 @0x30 194/122 -4.0 dB 5. Lowering the mark did not lower the received mark but lowered the
space: the TX path normalises the audio level, so the twist cannot be set from the PCM amplitudes
(capture16 showed the same from the other side). The deviation coefficient scales both tones until
the receiver's IF limits the 2200 (0x60 and up). Default: dev 0x40, 8000/8000.

## v39 (2026-10-28): methods 0-2 retired, FM Send RAM is the beacon

`krk_beacon.c` (FD6818 tone retune, dual-tone gain switching, FSK engine) is gone; `krk_aprs_tx.c` holds
only the FM Send RAM sender (default dev 0x40, mark/space 8000/8000, tail < 3 halves). It is driven by the
stock APRS menu on analog channels (Upload Beacon) and by the stock interval timer (automatic beacon),
see `docs/aprs-submenu.md` section 7. The sweep (key 7) and repeat (key 8) tests live on in KRAKEN >
Beacon (test).

## Timer clock, measured

Screen readings of the beacon's UART-timed calibration (`counts_per_ms`, v17/v18, 2026-10-16):
`tmr 87162 c/ms` (first send), then `tmr 83302 c/ms` and `tmr 83301 c/ms` on the next two sends; the
minimum-of-six version in v25+ reports the ~83300 value. With TIMER.CH[0] LoadCount 840000 this is a
reload period of ~10.08 ms. Interpretation (83.3 MHz count rate vs. a 10.08 ms tick) left open.
