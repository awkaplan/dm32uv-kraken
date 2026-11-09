# Retune-for-scope: RX retune, lock, RSSI, restore (DM32.00.01.048)

Static RE only. Nothing here was run on a radio. Addresses: flash `0x03xxxxxx`, IRAM `0x000xxxxx`.
Sources: [dm32uv-re](https://github.com/awkaplan/dm32uv-re) decompile corpus + `cs_disasm.py` on `DM32.00.01.048.bin` / `IRAM_dm32.00.01.048.bin`,
the FD6818 programming guide rev 1.0.0 (Chinese edition).
Confidence tags: HIGH = read in disassembly, MED = inferred from code shape/context, LOW = guess.

## 0. Corrections to earlier notes (read first)

1. **`rf_channel_frequency_apply` @0x0300e10c is NOT the RF tune.** It is the sub-audio (CTCSS/CDCSS) apply. (HIGH)
   - Its only caller chain is `radio_init_sequence` @0x0300e40c and `rf_channel_mode_select_and_apply` @0x0300e218.
   - `radio_init_sequence` feeds it `tone_signaling_type_classify(*(u16*)(0x3e1dc+0x13))`, the channel's tone code, at 0x0300e432..0x0300e47c.
   - `param_1` is the tone type 0..3. `param_2` is the tone code: BCD CTCSS in 0.1 Hz units, or a DCS code run through `freq_bitpack_crc12_encode` into `fd6818b_write_cdcss_code_reg08`.
   - `fd6818b_program_divider` @0x0300e7e0 is `movi r0,7; bsr 0x2c2f4`, i.e. a write to **REG_07H** (CTCSS tone divider). It is not the PLL.
   - `freq_code * 2.064888` is therefore tone Hz x 10 to a divider (550 -> 0x470 is the "no tone" default). It has nothing to do with RF frequency. Do not call it for a sweep.
2. **The RF frequency is `REG_38H/REG_39H`, 10 Hz units**, written by IRAM `fd6818_set_frequency` @0x0002b56c. (HIGH)
3. **An earlier FHSS plan (not in this repo) was wrong that the stock tune never re-triggers RXON.** It searched for writes to `0x03`. (HIGH, see §A.3)
   - Stock writes **register `0x30`** (not `0x03`) as `0` then `0xbff1` right after every frequency write.
   - `0xbff1` is byte-for-byte the datasheet's `WRITE(0x03,0xBFF1)` RXON, with `vco_cal_en` (bit 15) going 0 -> 1.
   - The `pllprobe` result (lock <= 40 us) was measured **without** that re-trigger. See §B.

## A. Chain: channel frequency -> FD6818 registers

### A.1 Units and storage (HIGH)
- Frequencies are **uint32 in 10 Hz units** (14400000 = 144.000000 MHz).
  - Evidence: the thresholds in `frequency_band_range_check_and_status_set` @0x0300d84c are literal `14400000`, `0x29f6300` (44000000), `0xe1d480` (14800000), `0xdec740` (14600000).
  - `bcd_frequency_decode(buf,4)` @0x0305f560 turns the 4-byte BCD of a channel record into this integer. `local_5c` in `channel_change_apply_dispatcher` @0x030573e8 is its result.
- **Live RX frequency = `*(u32*)0x0003846c`**. **Live TX frequency = `*(u32*)0x00038468`.**
  - `bandwidth_deviation_threshold_apply(iStack_58, local_5c)` @0x0300da88 stores param1 -> `0x38468` and param2 -> `0x3846c` (via `lrw 0x300db44` = 0x38468, `st.w` +0 and +4).
  - TX use of `0x38468`: IRAM TX-start @0x0002b440 does `lrw 0x2b500` (0x38468), `ld.w r0,(r3,0)`, `bsr 0x2b674` (TX retune), then `pa_band_select_vhf_uhf_gpio(1)`, then the PA ramp `0x2b364`.
  - RX use of `0x3846c`: `channel_change_apply_squelch` @0x0300df08 does `lrw 0x300df50` (0x38468), `ld.w r0,(r3,0x4)` at 0x0300df14, then `bsr 0x2b6a0` at 0x0300df16.
  - So `local_5c` is TX and `iStack_58` is RX in the dispatcher's naming.
  - **MED-HIGH**: the RX/TX assignment is read from code, not observed on a radio. Read `0x3846c` on a live radio once to confirm.
- Register values for a sweep point: `F10 = F_Hz / 10`, `REG_39H = F10 >> 16`, `REG_38H = F10 & 0xFFFF`.
  - 146.000 MHz: F10 = 14600000 = 0x00DEC740, so REG39 = 0x00DE, REG38 = 0xC740.
  - 440.000 MHz: F10 = 44000000 = 0x029F6300, so REG39 = 0x029F, REG38 = 0x6300.
  - 12.5 kHz step = +1250.
  - No scaling, divider math or per-frequency interpolation is applied on this path. HIGH, disassembly §A.2.

### A.2 `fd6818_set_frequency` @IRAM 0x0002b56c (HIGH)
```
0002b56c push l0,lr
0002b56e zexth l0,a0          ; l0 = a0 (high half)
0002b570 movi  a0,0x38
0002b572 zexth a1,a1          ; a1 = low half
0002b574 bsr   0x2c2f4        ; reg_write(0x38, a1)   REG_38H = low16
0002b578 movi  a0,0x39
0002b57a mov   a1,l0
0002b57c bsr   0x2c2f4        ; reg_write(0x39, a0)   REG_39H = high16
0002b580 pop   l0,lr
```
- Signature: `void fd6818_set_frequency(u32 hi16 /*a0*/, u32 lo16 /*a1*/)`. It returns nothing and takes only the bus semaphore, once per write.
- `fd6818b_reg_write` @0x2c2f4 pends semaphore `0x3a49c` (`lrw 0x2c384`) via `0x300c500(sem, 0)` (infinite wait), bit-bangs, then posts via `0x300c53c`.
- `fd6818_reg_read` @0x2c33c uses the same semaphore, so it is safe to call from any task (see §A.5).

### A.3 The two stock RF retune functions (HIGH)
Both are IRAM.

**`0x0002b6a0`: RX retune, `void f(u32 F10)`** (flash symbol `FUN_0002b6a0`).
Callers: `channel_change_apply_squelch` @0x0300df08 (call at 0x0300df16) and an IRAM caller at 0x2b4ea.
Order:
1. `bsr 0x2b634`
   - Calls `0x300e918` (`config_table_entry_0x30`) = **reg 0x30 := 0** (IDLE).
   - Clears a few state bytes if the channel is not digital.
2. `fd6818_set_frequency(F10>>16, F10&0xffff)` (0x2b6a8..0x2b6ac)
3. `reg 0x32 := 0x244` (0x2b6b0..0x2b6b6)
4. Digital branch (`([0x3e1dc+8] & ~0xf) == 0x10`), at 0x2b6c8:
   - `0x2cbd0(4)` and `0x2c5d8(0x3b)` (`gpio_interrupt_disable_bit(0x3b)`; likely masks the FD6818 INT pin).
   - **`0x2b5e4`**, then `reg 0x47 := 0x140`, `[0x38688] := 0`, `reg 0x3f := 0`.
5. Analog branch (0x2b700):
   - `tone_signaling_type_classify`, `0x300e218` (tone setup, i.e. `rf_channel_frequency_apply`), etc.
   - Mode-dependent `reg 0x3f` write.
   - **Common tail 0x2b7c6: `reg 0x48 := [0x2b810 table byte] | 0x31e0`, `0x2b5e4`, `reg 0x47 := 0x6042`.**

**`0x0002b5e4` = `fd6818b_filter_select_then_chipmode_normal`** (HIGH):
- `reg 0x37 := 0x1f1f` if `([0x3e1dc+8] & ~0xf) == 0x10` (digital), else `0x1f0f`.
- `reg 0x30 := 0`, then `reg 0x30 := 0xbff1`.
- This is the RXON re-trigger (REG_03H[15] 0->1 recalibrates the VCO/PLL; datasheet p.20 and §"PLL lost" note near line 2504).
- I read `0x30` as the same register as the guide's `REG_03H`, because the values match exactly. The mapping is MED (see §Open).

**`0x0002b674`: TX retune, `void f(u32 F10)`**:
- `0x2cbd0(4)`, `0x2c5d8(0x3b)`, `set_frequency`.
- Then `0x2b634`, `0x2b550` (REG_47H afout_mode bits [11:8] cleared = audio mute), `0x2b5b8` (`reg 0x37=0x1d0f, 0x52=0x28f, 0x30=0, 0x30=0xc1fe` = TXON), `0x2b50c`.
- Callers: `radio_init_sequence` and IRAM TX-start `0x2b440`. **Do not use it for a scope. It keys the TX chain.**

### A.4 Band switching VHF/UHF (HIGH for mechanism, MED for polarity)
- **No FD6818 band register is written anywhere.**
  - The datasheet's REG_1AH / REG_40H band table is not used by the stock firmware. A grep of all 297 `reg_write` call sites finds no `0x1a`.
  - The chip VCO/band follows the frequency (calibrated by the `vco_cal_en` 0->1 in the 0x30 sequence).
  - REG_37H differs only by analog/digital (0x1f0f/0x1f1f); it is **not** band-dependent.
- **Band switching is done with board GPIOs.**
  - `antenna_switch_select_gpio_24_22(1)` @IRAM 0x0002c878 selects the RX LNA path.
    - It reads `flags_0x3` bit 3 of `radio_mode_state` (`*(u8*)(0x3e318+3) & 8`).
    - bit3 == 0: `gpio_clear(0x24); gpio_set(0x22)`.
    - bit3 == 1: `gpio_clear(0x22); gpio_set(0x24)`.
    - Vendor pin map (`PinMap_Baofeng_DM-32_14.06.26.xls`, GPIO sheet): **0x22 = LNA VHF, 0x24 = LNA UHF**.
  - `pa_band_select_vhf_uhf_gpio(1)` @IRAM 0x0002c838 selects the PA path on bit 4 of the same byte (0x37 = VHF PA, 0x23 = UHF PA).
    - TX only. A scope leaves it alone.
    - The 0x37/0x23 polarity is consistent with `frequency_band_range_check_and_status_set`'s TX-side bit 4 test, which sets bit 4 for 430-450 MHz.
  - `frequency_band_range_check_and_status_set(RXfreq, TXfreq)` @0x0300d84c computes those flag bits.
    - It is called from the dispatcher only, against a runtime band table at `0x38470+0x00..0x24` that is loaded from calibration/model data.
    - I cannot read that table statically.
    - **Retuning with `0x2b6a0`/`set_frequency` alone does NOT move the LNA GPIO.** For a cross-band sweep the patch must switch GPIOs 0x22/0x24 itself (`gpio_set_output_bit` @0x2c500, `gpio_clear_output_bit` @0x2c544).
- For the two ranges the dispatcher tests against the first range pair `[0x38470+0..4]` vs `[+8..0xc]`: first range sets bit3 = UHF -> LNA 0x24. The second range clears it = VHF -> LNA 0x22. MED.
- I did not find a filter or LNA register that changes per band. The only other RF "band" inputs are the GPIO pair above and the chip's own VCO cal.

### A.5 Per-frequency calibration (HIGH that it is not on the RF-tune path)
- `freq_calibration_apply_all` @0x0300db48 is called from `channel_change_apply_dispatcher` (line "freq_calibration_apply_all();" before `channel_change_apply_squelch`), not from `0x2b6a0`.
- It does the following:
  - Interpolates calibration tables for the VFO frequency (`freq_calibration_interpolate_selected/single`, `channel_step_frequency_bound_check`, `frequency_range_clamp_and_dispatch`) into RAM bytes at `0x38470+0x28/0x29/0x2d/0x38` and into `mmio_11000114` (a **SoC audio-gain MMIO**, not an FD6818 register).
  - Those are TX power, mic/audio level and similar. It does not rewrite the RX synth.
- RX sensitivity calibration (`REG_78H` squelch thresholds) is applied by `hw_register_0x78_write_from_byte_pair(byte [0x3e210+2])` at the top of `channel_change_apply_squelch`. It is not per-frequency in a way a sweep needs.
- Consequence: a bare retune gives a valid RX at the new frequency. RSSI is uncalibrated across frequency (no per-frequency RSSI offset is applied by the chip). A scope shows **relative** dB: `rssi_sq/2 - 160` per the datasheet, with `REG_2CH` ext_lna_gain not set by stock (not seen).

### A.6 Which function retunes RX, and from where (HIGH)
- **Stock idiom for "retune RX to an arbitrary frequency"**, with direct precedent in `rf_frequency_adjust_and_display_refresh` @0x0302fcd0:
  ```
  *(u32*)0x3846c = F10;           // bandwidth_deviation_threshold_store(F10, _DAT_0302fe38 == 0x3846c)
  channel_change_apply_squelch(); // flash 0x0300df08, no args
  ```
  - `channel_change_apply_squelch` does: `hw_register_0x78_write_from_byte_pair([0x3e210+2])`, then `0x2b6a0(*(u32*)0x3846c)`, then `zero_fields_by_index_pair` + `rf_agc_step_adaptive_control_loop` if `[0x3e318+0x11]&0x20`, then `antenna_switch_select_gpio_24_22(1)`.
- Callers of `channel_change_apply_squelch` (6): `channel_change_apply_dispatcher`, `rf_frequency_adjust_and_display_refresh`, `screen_enter_apply_channel_and_rf_mode`, `channel_change_tx_led_reset`, `cps_serial_field_rw_dispatcher_variant`, `factory_service_command_dispatch`.
- Task contexts for stock RX channel changes:
  - "task menu": `channel_change_apply_with_zone_and_flash_validate` -> `channel_change_apply_dispatcher` (docs/encryption.md l.224).
  - "task init" (the `main_ui_background_task_loop` @0x03013a3c task): `rf_frequency_adjust_and_display_refresh`, `channel_load_synchronized`.
  - "task CodeSw" (prio 28): `channel_load_synchronized`.
  - So retuning RX from the menu task is **stock behaviour**. No special lock is needed beyond the FD6818 bus semaphore taken inside each reg access.
- `channel_load_synchronized` @0x0305813c(`u16 chan, u8 mode`) is a full channel re-apply.
  - It brackets with `radio_mode_dispatcher_char(0,0)` (sets `call_setup_sm_state->flags_0x19` bit0, resets the modem via `radio_reset_sequence_with_clock_reset`) and `radio_mode_dispatcher_char(1,0)` (drains the modem queue, clears the flag).
  - It also pends and clears event-flag group `0x3fd90` bit `0x20`.
  - It is heavier than needed for a sweep restore.

## B. Lock status (HIGH for register, MED for behaviour)
- **Yes: `REG_0CH` bit 9 `pll_lock`** (1 = locked, 0 = lost). Guide register summary l.2776. REG_0CH also has `afc_rail` bit 8, `weak_rssi` bit 7, `sq_out` bit 1, `vox_out` bit 2, `tot_out` bit 3.
  - Read it with `fd6818_reg_read(0x0c)` @IRAM 0x2c33c and test `& 0x200`.
  - Stock RE'd accessors for REG_0CH (flash 0x0300eb44..0x0300eb90) read only bit 1 (`sq_out`), bits [11:10], [13:12], [15:14] (undocumented), and bit 2. **Stock never reads bit 9.**
  - The guide itself says PLL lock lost can be recovered by REG_03H[15] 0->1.
- An earlier `pllprobe` measurement (a RAM-dump probe build, not in this repo) saw bit 9 already set at the first read (~40 us) after `set_frequency` alone, including 20 MHz jumps.
  - Caveat: **it did not do the reg-0x30 0->0xbff1 re-trigger the stock tune does** (§A.3), and the datasheet says a new frequency takes effect only after RXON is re-issued.
  - The probe may therefore have read a stale "locked" bit from the old frequency. Treat the "<= 40 us settle" figure as unproven for the full tune.
- **Recommended wait strategy:**
  1. Do the full stock sequence (§C step 4).
  2. Poll `REG_0CH & 0x200` until set, bounded (e.g. 2 ms, 40 us per read).
  3. Then wait a **fixed** AGC/RSSI settle time (§D). Lock alone does not mean the digital RSSI/AGC has settled.
  4. Measure it empirically: time pll_lock deassert/assert around the `reg30 0->0xbff1` with the 84 MHz APB timer at `0x14000004`, as the probe did.

## C. Recipe (safest concrete)
All calls are plain C-SKY ABIv2 calls: args in r0/r1, no stack args. `F10` = Hz/10.

**Preconditions (patch checks):** not in TX (`call_active_flag`/TX state idle), not scanning (scan-active flag; see §E), no active call. Run only from the screen handler in "task menu".

**Save (cheap, no chip state needed):**
```
saved_rx10 = *(volatile u32*)0x0003846c;   // live RX frequency (10 Hz); also leave 0x38468 untouched
```
The sweep below never writes `0x3846c`/`0x38468`, so the channel state stays intact.
The chip registers `0x30,0x32,0x37,0x38,0x39` and GPIO 0x22/0x24 are the only things touched. They are all rewritten by the restore.

**Per point (preferred minimal sequence; each step is a stock routine):**
1. If the band changes: set the LNA GPIOs.
   - VHF: `gpio_clear_output_bit(0x24); gpio_set_output_bit(0x22);`
   - UHF: `gpio_clear_output_bit(0x22); gpio_set_output_bit(0x24);`
   - Leave PA GPIOs 0x37/0x23 alone.
2. `fd6818b_reg_write(0x30, 0x0000)`. This is stock IDLE; the same as `config_table_entry_0x30` @0x0300e918, which takes no args and can be called instead.
3. `fd6818_set_frequency(F10 >> 16, F10 & 0xFFFF)` @IRAM 0x2b56c.
4. `fd6818b_reg_write(0x32, 0x244)`. Optional. Stock does it in 0x2b6a0; it is a tone-detector reset (see the poll function).
5. `fd6818b_filter_select_then_chipmode_normal()` @IRAM 0x2b5e4, no args.
   - Writes REG_37H, then reg30 0 -> 0xbff1 (RXON + VCO cal).
   - It selects 0x1f0f vs 0x1f1f from the **current channel's** analog/digital type, so it matches the radio's present mode.
6. Poll `fd6818_reg_read(0x0c) & 0x200` (bounded ~2 ms), then wait the AGC/RSSI settle time (§D).
7. `rssi = fd6818_reg_read(0x67) & 0x1ff` (stock helper `reg_0x67_lower9_read` @0x0300ebb0 does exactly this). dB = `rssi/2 - 160` (0.5 dB steps, relative).

**Restore:** call `channel_change_apply_squelch()` @flash 0x0300df08 (no args).
- It re-reads `0x3846c` and runs the complete stock RX tune (IDLE -> freq -> mode-specific config incl. CTCSS/DCS -> RXON), `REG_78H`, AGC reset, and `antenna_switch_select_gpio_24_22(1)` (restores the LNA GPIO from the channel's band flag).
- This is exactly what `rf_frequency_adjust_and_display_refresh` does after changing `0x3846c`.
- If the patch wants zero extra side effects instead: write `*(u32*)0x3846c = saved_rx10` and call `0x0002b6a0(saved_rx10)` directly, then `antenna_switch_select_gpio_24_22(1)`. Same effect without the REG_78/AGC part.
- Do **not** use `channel_load_synchronized`/the dispatcher unless a full channel reload is wanted. Those reset the modem and pend on flag group `0x3fd90`.

**Alternative "full stock" per-point recipe (simpler, slower, some side effects):** `*(u32*)0x3846c = F10; channel_change_apply_squelch();` per point.
- It does the same chip sequence plus REG_78H, AGC resets and CTCSS/DCS setup each time.
- It does **not** switch the LNA GPIO for a band different from the channel's, because the band flag byte (`0x3e31b` bit 3) is only updated by `frequency_band_range_check_and_status_set`, called from the dispatcher.
- You would have to run that and update `0x3846c` anyway. Also this overwrites the saved channel RX word, so restore it afterward.

## D. Timing evidence for settle (what stock does)
- No stock function was found that loops retune + RSSI read. See the open question on "Freq Search".
- Busy-wait helpers: `0x0002df80(n)` is `busy_wait_delay_loop_4arg`, about n microseconds (unverified; calibrated against `0x2e0d8(n)` = n x 1000 x `0x2df80`, i.e. n ms). MED.
- Stock settle constants:
  - TX-start `0x2b440` waits 800-1600 us before the retune (by power mode). PA band GPIO and the PA ramp follow.
  - TX-stop path waits 1600 us then 300 us.
  - `rf_frequency_adjust_and_display_refresh`: `channel_change_apply_squelch()`, **100 ms** wait, `reg 0x51 := 0x300`, `fd6818b_filter_select_then_chipmode_normal`, **500 ms** wait, all under an OS scheduler lock (`OSSchedLockNestingCtr++`). This is generous and not a speed hint.
  - Digital RX retune (`0x2b6a0` digital branch): **no delay at all** between the RXON write and return.
  - Background AGC step (`rf_agc_step_adaptive_control_loop` @0x0301bff8) reads REG_67 every tick of event-flag group `0x3fd90` bit 1; the UI meter (`rssi_rolling_average_meter_update` @0x0305ffe4) averages 10 REG_67 samples, once per 10th tick. The tick period itself is not pinned by me.
- Practical empirical plan:
  - Start with lock-poll + 2 ms, step the fixed settle down while watching a known signal.
  - The AGC loop in "task init" moves the gain while you measure (§E), so make the AGC inhibit work first.

## E. Concurrency and inhibiting stock activity
What runs alongside the "task menu" screen handler:
- **"task init"** (entry 0x03068688) loops `main_ui_background_task_loop` @0x03013a3c on a periodic event flag.
  - `rf_agc_step_adaptive_control_loop` (reads REG_67 and adjusts gain registers) when `radio_mode_state[1].flags_0x7 & 0x20`.
  - `fd6818b_status_register_poll_with_reinit` @0x0300eea4 (reads REG_0D/REG_0E, rewrites `reg 0x32 := 0x244`, and after 200 failed polls pulses `reg 0x32 := 0x244`/`0x245` with busy-waits of 10 ms and 200 ms).
  - `ui_background_periodic_subtask_dispatcher` @0x030131e4 (the RSSI meter, `adc_rssi_rolling_average_tick`).
  - `channel_squelch_tone_scan_and_apply` @0x0302fe94 (CTC/DCS tone detect; can call `rf_channel_mode_select_and_apply`).
  - All of these use `fd6818_reg_read/_write`, so each single register access is atomic. **A multi-register retune is not atomic against them**, and the AGC step can move the gain between your lock poll and your RSSI read.
  - The status poll's `reg 0x32` writes are harmless to frequency but can toggle the tone detector.
- "task CodeSw" and the scan timers can call `channel_load_synchronized` (a scan tick will retune the radio behind your back).
- **Do not hold the FD6818 semaphore (0x3a49c) across calls.** It is non-recursive and `reg_read/_write` pend on it with an infinite timeout. A self-deadlock follows.
- **Do not use `OSSchedLock` to quiet other tasks**, as the factory path does, unless you are sure no other task holds the bus semaphore at that moment; a lock holder that cannot run will deadlock the first reg access. The factory path's use of it is a hint it is possible, not that it is safe from the menu task. LOW.
- Inhibit options (all untested):
  1. **Refuse to sweep** when scanning, in a call, or in TX (check `call_active_flag`, `0x3e318` flags, `scan active = *(u8*)(0x3ff84+0x2b) & 1`). MED on the flag meaning, taken from `scan_toggle_persist_and_notify`.
  2. **Park the AGC**: clear bit 5 of `radio_mode_state[1].flags_0x7` for the duration and restore it. The bit is the gate at `main_ui_background_task_loop` line ~47 and is set/cleared by `frequency_band_range_check_and_status_set`. Its exact address is `0x3e318 + 0x0b` if `[1]` is a 0xa-byte-sized struct. I did not pin the struct stride. LOW. Verify before use.
  3. **Squelch/audio**: stock RX audio is gated by `REG_47H` afout_mode (`0x2b550` clears bits [11:8] = mute, `0x2b50c` and others set 0x1xx). A sweep step that ends with `0x2b5e4` leaves REG_47H untouched (it was left by the last stock call). To keep audio muted explicitly, call `0x2b550` (`fd6818b_reg47_afout_clear_mode`, no args) once before the sweep. LOW-MED. Restore via `channel_change_apply_squelch()`, whose analog tail writes `reg 0x47 := 0x6042`.
  4. **Dual watch**: no dedicated suppress flag was found. `vfo_exit_dual_watch_and_refresh` @0x03064280 shows the entry/exit points, so exiting dual watch before the sweep is the safe bracket. LOW.
- TX: none of the sweep path touches PA GPIOs, `reg 0x30 := 0xc1fe` (TXON) or `0x2b674`/`0x2b440`. Never call those.

## F. Open questions
1. **Where is the stock "Freq Search" (key function id 40, UI string at 0x030bf16c, table entry 0x030ba4cc)?**
   - Not located. The manual §5.16 says "Seek..." then a found frequency + CTCSS/DCS.
   - The "Seek..." string 0x030b7988 belongs to the FM-radio seek.
   - The closest candidates are `fd6818b_status_register_poll_with_reinit` + `rf_frequency_adjust_and_display_refresh` in "task init" (the REG_0D/0E value is mapped to a displayed frequency). This is more likely a chip-assisted carrier search than a retune+RSSI sweep loop.
   - The executor (the code that maps side-key function 40 to it) is not found, so the loop timing in §D is a lower-confidence proxy. LOW.
2. `reg 0x30` vs the guide's `REG_03H`: same values (0xbff1 RXON, 0xc1fe TX pattern) but different number. MED that they are one register on this FD6818B. It does not change the recipe because every call reuses the stock writes verbatim.
3. Band-range table at `0x38470+0..0x24` (which range is UHF vs VHF) is runtime data. Read it from a live RAM dump (existing `dm32_ramdump.py`) before relying on the GPIO polarity in §A.4.
4. Whether `0x2b6a0`'s non-digital branch is acceptable to bypass for a sweep: the recommended sequence skips the tone/CTCSS setup and `reg 0x3f`/`0x48`/`0x47` writes, which are channel mode config and are already in place and unchanged by the sweep. MED. They are restored by the final `channel_change_apply_squelch()`.
5. True settle time of the full tune (with RXON re-trigger) and AGC convergence: unmeasured.
6. Whether the `pllprobe`'s ~40 us lock is a stale bit (§B).

## G. Confidence summary
| Item | Conf |
|---|---|
| `rf_channel_frequency_apply` is the tone apply, not RF tune | HIGH |
| RF freq = REG_39H:REG_38H, units 10 Hz, args (hi16, lo16) to IRAM 0x2b56c | HIGH |
| RX freq word = u32 at 0x3846c (TX at 0x38468), 10 Hz units | MED-HIGH |
| Stock RX retune idiom: store 0x3846c + `channel_change_apply_squelch` 0x0300df08 | HIGH |
| Menu task may call it (stock does) | HIGH |
| Stock tune includes IDLE -> freq -> RXON re-trigger via reg 0x30 | HIGH |
| `pll_lock` = REG_0CH bit 9, stock does not poll it | HIGH (reg) / MED (behaviour) |
| LNA GPIO 0x22 VHF / 0x24 UHF need manual switching on cross-band sweep | MED-HIGH |
| No per-frequency calibration on the RX tune path | HIGH |
| Inhibit AGC via the flag at `radio_mode_state[1].flags_0x7` bit 5 | LOW |
| Stock sweep loop timing | LOW (not found) |
