# F10 satellite mode: the radio side (Doppler-corrected RX and TX on an analog channel), DM32.00.01.048

Static RE, written before the build. The design in section 6 was built as `krk_satrf.c` / `krk_satrf.h`
(not `krk_sat_radio.c`); for what has since been tested, see the [satellites user guide](user-guide/satellites.md).
Addresses: flash `0x03xxxxxx`, IRAM `0x000xxxxx`
(IRAM image base `0x10000`). Every literal below was read from `DM32.00.01.048.bin` / `IRAM_dm32.00.01.048.bin`
(literal pools dumped with Python, not taken from Ghidra names). Confidence: HIGH = read in disassembly and
cross-checked by a second path, MED = one path or inferred from code shape, LOW = guess.
Companion docs: `docs/retune-for-scope.md` (RX retune), `docs/aprs-submenu.md` 7.1/7.2 (periodic hook, dual watch),
`patches/kraken_ui/krk_rf.c`. The SGP4/Doppler maths is `krk_sat.c` (not touched here).

## 0. Answers in one page

1. **The live RX/TX words are two u32 in 10 Hz units: `0x0003846c` = RX, `0x00038468` = TX.** (HIGH)
   - TX is read from `0x38468` at every keying (`radio_init_sequence` @0x0300e40c -> `0x2b674(*(u32*)0x38468)`).
   - RX is read from `0x3846c` by `channel_change_apply_squelch` @0x0300df08 (which runs after every TX and
     after every channel load).
   - So: **write the two words, then call `channel_change_apply_squelch()` for RX; TX needs no call at all**
     (it picks the word up at the next PTT). No flash write is involved. Stock itself does exactly
     "store 0x3846c + `channel_change_apply_squelch`" in `rf_frequency_adjust_and_display_refresh` @0x0302fcd0.
   - Stock CTCSS/DCS is untouched (tone setup lives in `radio_init_sequence` for TX and in the analog branch of
     IRAM 0x2b6a0 for RX, both driven by the channel struct, not by the frequency words).
2. **PTT does not need our screen to be closed to be *detected*, but it is unverified that it works cleanly inside
   `krk_run_screen`** (section 3). Recommended design: the satellite screen only *arms* tracking; a new 1 Hz hook in
   the stock maintenance task applies the corrections; the user presses Back and operates from standby with fully
   stock PTT.
3. **1 Hz piggyback**: not `aprs_tick_hook` (0x03054888 is behind the APRS-enabled, APRS-active and fix gates), but the
   unconditional `bsr 0x03068224` at **`0x030547ae`** at the top of `ui_periodic_maintenance_task_loop` @0x0305476c.
4. **Resolution is 10 Hz** in RAM and chip (HIGH). Plan RX with a retune hysteresis (about 500 Hz); TX word exact.
5. **UTC**: stock keeps the last RMC sentence (`0x00038c79`, valid bit `[0x38c6c]&0x40`); date is RMC field 9, time field
   1. The RTC holds *local* time (UTC + the stock time zone setting) as seconds/minutes/hours in regs 0..2 and a
   unix day count in regs 3/4 (section 5).
6. Section 6 is the module design.

## 1. RAM state and the stock functions that program the FD6818

### 1.1 The three places a frequency lives (HIGH unless noted)

| Where | What | Units | Written by | Read by |
|---|---|---|---|---|
| `0x0003e1dc` = `p_vfo_display_state` (+0) | RX frequency of the **currently loaded channel record** (VFO or memory channel alike), binary u32 | 10 Hz | `commit_channel_record_dual_vfo` in the dispatcher | display, `freq_calibration_apply_all` (RX side), the dispatcher's reuse |
| `0x0003e1e0` (+4) | TX frequency of the loaded record (= RX +/- offset for a VFO, via `channel_apply_frequency_offset` @0x03031500) | 10 Hz | same | `freq_calibration_apply_all` (TX power / mic cal), `vfo_display_config_apply`, display of the split TX |
| `0x0003e1e4` (+8) | flags: `& 0xf0` = mode (0 analog FM, 0x10 DMR), **bit 3 = channel may not transmit** | | | `ptt_press_handler` gate `(flags_0x8 & 8)==0` |
| **`0x00038468`** | **live TX word** | 10 Hz | `channel_freq_pair_store` @0x0300da88 (a0 -> here) | `radio_init_sequence` @0x0300e40c (literal 0x300e5c4); IRAM `rf_tx_enable_sequence` 0x2b440 (digital, literal 0x2b500) |
| **`0x0003846c`** | **live RX word** | 10 Hz | `channel_freq_pair_store` (a1 -> here) | `channel_change_apply_squelch` @0x0300df08 (`ld.w a0,(0x38468+4)` at 0x0300df14, then `bsr 0x2b6a0`) |

- Units: 10 Hz, e.g. 14600000 = 146.000000 MHz. The flash record is 4-byte BCD (`bcd_frequency_decode` @0x0305f560
  turns it into this integer); RAM is binary. (HIGH, thresholds in `frequency_band_range_check_and_status_set`
  are literals `14400000`, `14800000`, `43000000` ...)
- **Correction to docs/retune-for-scope.md A.1**: that note says "`local_5c` is TX and `iStack_58` is RX" in the dispatcher.
  The disassembly says the opposite. `channel_freq_pair_store(param_1, param_2)` stores param_1 -> `0x38468` and
  param_2 -> `0x3846c`, and the dispatcher calls it as `channel_freq_pair_store(iStack_58, local_5c)`. `local_5c` is the
  first u32 of the record (struct +0, RX) and `iStack_58` the second (struct +4, TX). Proof by two independent paths:
  (a) VFO case `iStack_58 = channel_apply_frequency_offset(vfo[0], ...)` = RX +/- shift; (b) `ptt_press_handler`
  @0x0305c086..0x0305c0bc copies struct+0 over struct+4 and calls `channel_freq_pair_store(struct+4, struct+0)`
  (simplex "TX = RX" in the frequency-entry state). The RX = 0x3846c, TX = 0x38468 result of the older note stands.
- Digital channels never use this feature: refuse unless `(*(u8*)0x3e1e4 & 0xf0) == 0`.
- VFO vs memory mode: `settings[0x2d]` (= `p_dm32_settings_block04` 0x3e9b4 + 0x2d = **0x3e9e1**): bit 7 = selected side (A/B),
  bit 2 = loaded side is in memory-channel mode (0 = VFO). (MED-HIGH: dispatcher `(settings[0x2d]&4)==0` selects the
  VFO branch, `vfo_apply_active_side_config` @0x030641f4 maintains bit 2 per side.) The live words behave the same
  in both modes, so the design does not need to know.

### 1.2 Who programs the chip from them

- **RX tune (channel load, after TX, after any tone/AFC change):** `channel_change_apply_squelch` @0x0300df08
  (HIGH): `hw_register_0x78_write(...)`, then `0x2b6a0(*(u32*)0x3846c)`, then the AGC reset if `[0x3e318+0x11]&0x20`,
  then `antenna_switch_select_gpio_24_22(1)` (LNA VHF/UHF from `[0x3e31b]` bit 3).
  IRAM `0x2b6a0` = IDLE (reg 0x30 := 0) -> `fd6818_set_frequency` (REG_39H:REG_38H) -> reg 0x32 -> analog tone setup
  (`rf_channel_mode_select_and_apply`) -> regs 0x3f/0x48 -> `0x2b5e4` (reg 0x37, reg 0x30 := 0 then 0xbff1 = RXON with
  VCO cal) -> reg 0x47 := 0x6042. Details: `docs/retune-for-scope.md` A.3.
- **TX tune at PTT (analog):** PTT pin 0x2b -> `keypad_debounce_and_commit_event` @0x03012f58 -> flag group `0x3a4f0`
  bit 0x08 -> `main_ptt_and_channel_event_loop` @0x03012d10 (own task) -> `ptt_press_handler` @0x0305bfb8 ->
  `tx_key_from_vfo_mode_guard` @0x0305be64 -> `radio_mode_entry_reinit_and_display` @0x0305baec (sets `call_active`
  `0x3fdc8` bit 3) -> **`radio_init_sequence` @0x0300e40c**: antenna switch TX (`0x2c878(0)`), TX tone
  (`ctcss_dcs_tone_apply` @0x0300e10c with the channel's tone code `[0x3e1dc+0x13/0x14]`), `fd6818b_reg_write_wrapper`,
  **`0x2b674(*(u32*)0x38468)`** (TX retune: set_frequency, TXON), `tx_audio_dac_envelope_ramp`. (HIGH: disassembly
  0x0300e484..0x0300e49a, pool word 0x300e5c4 = 0x38468; verified by reading the pool.)
  The TX frequency is therefore read **once per keying**, at the start of `radio_init_sequence`. It is also read by the
  roger-beep path of `call_teardown_tx_release_sequence` @0x03068538 (calls `radio_init_sequence` again), which is why
  the word must not change while keyed.
- **Return to RX after TX:** `main_ptt_and_channel_event_loop` (flag 0x10) -> `channel_switch_teardown_and_apply`
  @0x0305fa38 -> `vfo_display_config_apply` (config tables only, no frequency) -> `pa_band_select_vhf_uhf_gpio(0)` ->
  **`channel_change_apply_squelch()`** at the end (reads `0x3846c`). (HIGH, decompile + literal of the call.)
  So after every over the radio comes back to whatever `0x3846c` holds. No stock path in this sequence re-derives the
  words from the struct, so a corrected RX word survives TX. (`ptt_press_handler` only does `channel_freq_pair_store`
  from the struct when the VFO frequency-entry state `[0x3e156]` is neither 0 nor 2, i.e. while a frequency is being typed.)
- **Anything that overwrites the words with the nominal values** (all of them re-run `channel_freq_pair_store` from the
  record): `channel_change_apply_dispatcher` @0x030573e8 (channel/VFO change, zone change, A/B swap, dual-watch side flip
  via `vfo_apply_active_side_config` -> UI event 0x12), `screen_enter_apply_channel_and_rf_mode` @0x03038058, the CPS serial
  path, and the frequency-entry case above. This is the **ownership check** in section 6.
- **Band flags** (`[0x3e31b]` = `p_radio_mode_state_0003e318 + 3`): `frequency_band_range_check_and_status_set(RX, TX)`
  @0x0300d84c is called from the dispatcher only. It sets bit 3 (RX is in the UHF range: LNA GPIO 0x24, else 0x22),
  bit 4 (TX is UHF: PA GPIO 0x23, else 0x37), and **bit 5 = TX outside the radio's TX ranges**. `ptt_press_handler`
  refuses to key when bit 5 is set (it shows a status string instead; decompile `if ((flags_0x3 & 0x20) == 0) { key }
  else { status }`). The ranges depend on the model byte `[0x3fdc4]`: 0 = 144-148 + 420-450, 1 = 144-148 + 430-450,
  2 = 144-146 + 430-440, 3 = 144-148 + 420-440, other = 136-174 + 400-470 MHz (literal pools 0x300d9c0..0x300d9d4,
  0x300db1c..0x300db30; MED on the variant mapping, HIGH on the function's logic).
  **Important**: the flags are *not* recomputed when only the live words change. A shifted TX word is therefore not
  covered by the stock TX-allowed check unless we call `frequency_band_range_check_and_status_set(rx, tx)` ourselves
  after writing the words (section 6 does).
- **Calibration** (`freq_calibration_apply_all` @0x0300db48) interpolates TX power / mic gain from the *struct* fields
  (`0x3e1e0` TX, `0x3e1dc` RX), not from the live words. Doppler (a few kHz) is irrelevant to it. For a cross-band split
  that differs from the loaded record, power/mic calibration follows the record (MED risk, section 7).

### 1.3 Is "write the words, then call a stock retune" safe? (HIGH for RX, HIGH for TX)

- RX: yes. This is the stock idiom (`rf_frequency_adjust_and_display_refresh`), already used from the menu task in
  `docs/retune-for-scope.md`. `channel_change_apply_squelch` is also called from the maintenance task itself
  (`channel_change_tx_led_reset` @0x030553ec, which is called in the same loop), so calling it from the 1 Hz hook is a
  stock-shaped context.
- TX: yes, with the extra band check; no chip register is written by us at all. The word is consumed by the stock keying.
- Not safe: calling `0x2b674` / `radio_init_sequence` / `0x2b440` ourselves (keys the PA), or `channel_load_synchronized`
  / the dispatcher (reset the modem, re-derive the nominal words from flash).
- No flash write anywhere: the words are SRAM, the struct is SRAM, and we call none of `vfo_frequency_write`,
  `commit_channel_record_dual_vfo`'s flash side, `vfo_save_to_memory_channel`, `flash_block_write`.

## 2. TX path and PTT while our screen is up (MED-LOW, needs one QEMU/bench check)

What the code says:
- PTT is **not** read by the keypad matrix poll that `krk_run_screen` parks (`*KEYPAD_POLL_ENABLE = 0`, `0x18000800`).
  The PTT pin (0x2b, `bitwise_not_of_boolean_result` @0x03012f48) is debounced by `keypad_debounce_and_commit_event`
  @0x03012f58, called from `keypad_event_maybe_forward_to_dialog` @0x030139c0 in "task init" every tick, *before* the
  poll call and outside the poll's `0x18000800` gate. (MED-HIGH.)
- The gates in front of that debounce are `keypad_dispatch_not_inhibited` @0x0305dbe4, `[0x3fdb8]&0x40`, `[0x38e20+1]&1`
  (else it calls `FUN_03013400` instead and the PTT debounce is not reached), `[0x3e3b4+8]&1`, `[0x3e318+7]&0x22`,
  `[0x3e318+4]&4`, `[0x3ff84+0x2b]&8`. I could not tell statically whether any of them is set while a stock menu screen is
  open. LOW on "PTT debounce runs inside the menu".
- If the event does arrive, `ptt_press_handler` runs in its own task, concurrently with our blocking screen handler. In
  the analog case it keys through `tx_key_from_vfo_mode_guard` -> `radio_mode_entry_reinit_and_display`, which draws the
  stock TX UI (`format_and_write_status_field`, bargraph, `display_refresh_tone_mode_icon`) over our screen and, after
  release, `channel_switch_teardown_and_apply` redraws the VFO. Keying itself would use the live TX word, i.e. the
  corrected frequency, but the display would be a mess and the screen's own redraw has to cope.
  The stock `[0x3e156]` frequency-entry state (get_current_mode_state1) is 0 in standby; inside a menu I did not
  establish its value. If it is not 0/2, `ptt_press_handler` first does `channel_freq_pair_store(struct+4, struct+0)`
  = **TX := RX := nominal** (0x305c086..0x305c0bc), which would silently drop the Doppler correction on the first PTT.
  The ownership check (section 6) catches that, but the transmission would happen on the nominal frequency.
- Conclusion: **do not rely on keying from inside the screen.** Treat "PTT in the satellite screen" as unsupported and
  show a hint "Back to standby, then PTT" (the radio is not keyed by our code in any case).

Recommended UX (simplest and uses only stock PTT in stock standby):
1. Satellite screen: pick satellite, show AOS/LOS/elevation, press OK = **Arm tracking**.
   `krk_sat_radio_start()` captures the channel's nominal words, stores the Doppler track, sets `active`.
2. The screen may stay up (display only) or the user presses Back. Either way the 1 Hz hook keeps the corrected words
   applied. In standby the VFO still shows the nominal frequency (the struct is untouched), PTT is 100 % stock and
   transmits on `0x38468`.
3. Tracking ends by: reopening the screen and pressing Stop; end of the stored track plus 30 s; 30 minutes maximum; the
   ownership check seeing a channel change; or power-off. Stop restores the nominal words and re-tunes RX (section 6.4).

A status marker in standby is possible later (the hook could draw a small icon), not needed for v1.

## 3. Retune primitives and keyed-state detection

### 3.1 RX while receiving (FM voice)

| Option | Sequence | Pros / cons |
|---|---|---|
| **A (recommended v1)** | `*(u32*)0x3846c = rx; channel_change_apply_squelch()` | Full stock sequence incl. tone setup, REG_78, AGC reset, LNA GPIO. HIGH on correctness and CTCSS. Costs a short audio gap (IDLE -> relock -> RXON: ms to tens of ms; unmeasured) |
| B (optimisation, test first) | `krk_rf_retune(rx)` (reg 0x30 := 0, REG_38H/39H, `0x2b5e4`) | Fewer register writes, shorter gap. Does **not** re-run the tone setup, so CTCSS survival after IDLE is unproven (MED-LOW). Also set `*(u32*)0x3846c` so a later stock re-apply agrees. Needs a bench A/B with a CTCSS-gated source |

Glitch control is a **hysteresis policy**, not a primitive: the receiver is FM with 5 kHz deviation, so a static RX
error of a few hundred Hz is a DC offset the audio path removes, but each retune costs an audio chop. Retune RX only when
`|target - applied| >= RX_HYST` (default 500 Hz = 50 units; at 435 MHz the Doppler rate is at most about 150-200 Hz/s,
so that is one chop every 3 s at the steepest part of a pass and none for long stretches near AOS/LOS). The first apply
at arming is unconditional. The TX word is updated every second at full 10 Hz precision because writing it is free.

Skip the tick (do nothing, try next second) when any of these is true:
- `*(u8*)0x3fdc8 & 0x08` (TX up; `call_active` bit 3, set by `radio_mode_entry_reinit_and_display`, cleared by
  `channel_switch_teardown_and_apply`; the APRS code waits on the same bit). HIGH.
- `*(u8*)0x3e31c & 0x08` (`radio_mode_state.flags_0x4` bit 3: the dispatcher sets it on entry and clears it at its end,
  so it means "channel load in progress"). MED-HIGH. Avoids racing a load.
- `*(u8*)0x3fdb8 & 0x04` (stock's `tx_abort` bit tested at the top of the dispatcher, "call active/abort"; skip is safe).
- `stock_rx_active()` is irrelevant; a tick during an RX frame is the normal case.

### 3.2 TX: only between transmissions

- Detection of keyed state: `call_active` `0x0003fdc8` bit 3 (above). The window between the PTT press and that bit being set
  is the few draws in `ptt_press_handler`; a word written in that window is simply used by the keying, which is fine.
- Never touch TX hardware: the design writes only the RAM word. It is read once at `radio_init_sequence`; while bit 3 is
  set the tick does nothing at all (including the RX retune), so the word is stable for the whole over and its roger-beep
  retune.
- Residual race: an RX retune (a few register writes, ms) landing exactly as the PTT task starts the TX retune could leave
  the chip in RXON after TXON. Effect would be a failed/silent over (the stock keying then retunes from scratch next time),
  not an out-of-band emission. Mitigated by retuning RX rarely (hysteresis) and re-reading bit 3 right after the retune.

## 4. Frequency granularity

- `REG_38H/39H` = `(REG_39H << 16 + REG_38H) * 10 Hz`; "minimum frequency resolution 10 Hz"
  (FD6818 programming guide, register list). HIGH. The RAM words are the same
  10 Hz integers (`F10 >> 16`, `F10 & 0xffff` into `fd6818_set_frequency` @0x2b56c). No scaling anywhere on the path.
- The chip's PLL resolution is claimed, not measured (LOW). The radio's own reference oscillator error (a TCXO of 1 to 2 ppm
  is 440 to 880 Hz at 435 MHz, larger than a 500 Hz hysteresis) is not corrected anywhere in the stock firmware. Give the
  screen a user **trim** in Hz applied to both words (same sign) so one measured offset can be cancelled.
- Doppler tracks need no finer than 10 Hz, and the RX path only 500 Hz; use `ppb` tables (section 6.2) so rounding to 10 Hz happens
  once, at the end.

## 5. UTC time

### 5.1 Preferred: the RMC sentence (HIGH)

- `gps_nmea_rmc_gga_sentence_parser` @0x03012758 (task init) keeps the last checksum-valid `$GPRMC/$BDRMC/$GNRMC` with status
  `A` at `0x00038c79` (0x96 bytes, NUL terminated), valid flag `*(u8*)0x00038c6c & 0x40` (see `krk_grid.c`, which already copies and
  checksums it; `krk_gps_get` returns `utc[8]` = "hhmmss" but **not** the date).
- Fields (comma separated): 1 = `hhmmss.sss` UTC, 2 = status, 9 = `ddmmyy`. The date is exactly what is missing from
  `struct krk_gps_fix`, so `krk_sat_radio.c` needs its own small copy-and-parse (krk_grid.c's `copy_rmc` is static). Year = 2000 + yy. Convert to unix seconds with the usual days-from-civil.
  Torn reads: copy first, verify `*HH` checksum like `copy_rmc`, retry next second on failure.

### 5.2 Fallback: the RTC (MED; local time!)

- RTC chip is read through `rtc_register_read(reg)` @0x0301c2d4 (flash, `u8 f(u8 reg)`, I2C device 0xe0 through
  `i2c2_master_read_reg_locked` 0x2cea8). `rtc_decode_calendar_date` @0x0301c2f0 reads regs 0..4 and returns
  `(reg0 << 16) | (reg1 << 8) | reg2`.
- Register meaning, derived from the write side (`gps_nmea_datetime_to_rtc_sync` @0x0301265c + `rtc_date_validate_and_write`
  @0x0301c498, which writes regs 0xc..0x10 and latches them): reg0 = seconds (the sync leaves it alone), reg1 = minutes,
  reg2 = hours, **binary not BCD** (the code computes `tens*10+ones` from ASCII), reg3 | reg4 << 8 = **days since 1970-01-01**
  (I checked the stored-value formula by hand for 1970-01-01: it stores 0). MED-HIGH, never observed on hardware.
- **The RTC is local time.** `gps_datetime_format` @0x03011b40 adds the user's time zone (a signed hour/minute offset from the
  26-entry table selected by the byte at `0x38c6c + 0xb6` = `0x00038d22`, parsed by `gps_coordinate_table_entry_parse`
  @0x03011aa4) to the GPS UTC before `rtc_date_validate_and_write`. So `utc = rtc - tz`. If the user set the clock by hand
  the same offset is the only information available. MED. Use the RMC route whenever a fix exists; show "time: RTC" vs
  "time: GPS" in the screen so a bad time is visible. SGP4 needs UTC to a second or so (1 s error is about 7 km of
  satellite motion; Doppler error of a few Hz per second at the steepest part).
- Do not rely on the RTC's seconds being correct to better than one second.

### 5.3 Keeping time between ticks

Read UTC once at arming (and again every 60 s as a cheap resync), then advance with `OSTickCtr` (`0x000426d0`, 100 Hz, already
used by `krk_aprs_tx.c`). The tick hook then never touches the I2C RTC in the common case.

## 6. Design: `krk_sat_radio.c` / `krk_sat_radio.h`

### 6.1 Split of work

- **Foreground (`krk_sat` screen, "task menu")**: computes SGP4, builds the **Doppler track**, calls the arm/stop API, draws.
  Heavy double-precision maths lives here (the maintenance task has only 0x180 words = 1.5 KB of stack and a soft-float SGP4
  would not fit, MED; the APRS code already uses about 400 bytes of it).
- **Background (`krk_hook_sat_tick`, "task maint", 1 Hz)**: integer-only: interpolate the track, apply words, retune RX,
  safety checks. All radio actions happen here, so there is one writer and no cross-task race; the screen only sets request
  flags.

### 6.2 Interface between the maths and the radio: a range-rate track

Doppler is proportional to carrier frequency, so one dimensionless number per time sample is enough for both links:

```
r(t) = range_rate / c   [ppb, int16, positive = receding]       (|range_rate| <= 8 km/s -> |r| < 27000 ppb, fits)
rx(t) = dl_nominal * (1 - r)       (satellite receding -> lower)
tx(t) = ul_nominal * (1 + r)       (pre-compensate so it arrives on the satellite's nominal uplink)
```
(Sign conventions to be confirmed against the `krk_sat` pass output, [satellite-mode.md](satellite-mode.md); the track format is sign-agnostic.)

```c
/* krk_sat_radio.h */
#define KRK_SATR_MAX 360                         /* 360 x 5 s = 30 min, 720 bytes */
struct krk_satr_track {
    u32 t0;            /* unix seconds UTC of ppb[0] */
    u16 step_s;        /* 5 */
    u16 n;             /* <= KRK_SATR_MAX */
    int16_t ppb[KRK_SATR_MAX];
};
/* Linear interpolation at 5 s spacing errs by < 100 Hz at 435 MHz even at TCA for a 400 km pass (second derivative
 * ~ 30 Hz/s^2), well inside the 500 Hz RX hysteresis. Use 2-3 s if the TX error budget is tighter. */

int  krk_satr_utc(u32 *unix_s);                  /* RMC, else RTC - tz; 0 = no time. */
void krk_satr_utc_civil(u32 unix_s, int *y,int *mo,int *d,int *h,int *mi,int *s);   /* for krk_sat_time() */
int  krk_satr_arm(const struct krk_satr_track *t, u32 dl10, u32 ul10, int trim_hz, int hyst_hz);
                                                 /* dl10/ul10 = nominal RX/TX in 10 Hz units, 0 = use the channel's own
                                                  * words as nominal. Copies the track. Returns 0, or <0 = refusal code. */
void krk_satr_stop(void);                        /* request stop + restore (done by the next tick) */
int  krk_satr_state(struct krk_satr_status *st); /* active, ownership lost, tx_blocked, rx/tx now, last retune ... */
void krk_hook_sat_tick(void);                    /* splice target, section 6.5 */
```

State is a `static struct` guarded by a **magic word** exactly like `krk_aprs_tx.c` (`T.magic == ARM_MAGIC`) and
`krk_core.c` (`side.magic`): static RAM is not zeroed at power-up ("SRAM noise"), so `active` is only honoured when the
magic matches; `krk_satr_arm` sets it.

### 6.3 `krk_satr_arm` preconditions (refusal codes shown to the user)

1. Analog: `(*(volatile u8*)0x3e1e4 & 0xf0) == 0` and `krk_channel_on_selected()` (dual-watch aware, `krk_core.c`).
2. Dual watch off: `get_current_mode_state2()` @0x030641e4 `== 0`. (Under dual watch `vfo_apply_active_side_config` rewrites the
   words every dwell; tracking cannot hold.)
3. Not keyed (`0x3fdc8 & 8 == 0`), no channel load in progress (`0x3e31c & 8 == 0`), not scanning
   (`*(u8*)(0x3ff84 + 0x2b) & 1`, per `docs/retune-for-scope.md` E; MED).
4. Channel allows TX at all: `(*(u8*)0x3e1e4 & 0x08) == 0`. If it does not, RX-only tracking is still allowed; set
   `tx_blocked` and never touch the TX word (stock refuses the PTT anyway).
5. Nominal band sanity: `krk_rf_band(dl10)` and `krk_rf_band(ul10)` both nonzero, and the shifted values stay within
   +/-15 kHz of nominal (hard clamp, checked every tick).
6. UTC available (`krk_satr_utc`), and the track covers `now` (`t0 <= now < t0 + n*step_s`).

At arm time capture: `saved_rx = *(u32*)0x3846c`, `saved_tx = *(u32*)0x38468`, and `saved_flags = *(u8*)0x3e31b`.
Nominal = given `dl10/ul10` else these saved words. Record `owned_rx = owned_tx = 0` (nothing written yet).

### 6.4 The 1 Hz tick (integer only)

```
tick():
  if (!armed()) return;
  now = utc_now();                                  /* OSTickCtr-advanced, resync from RTC/GPS every 60 s */
  if (stop_requested || now >= trk.t0 + trk.n*step + 30 || now - armed_at > 1800) { finish(RESTORE); return; }
  if (keyed() || loading() || tx_abort()) return;   /* section 3.1 */
  /* ownership: someone else (channel change, VFO edit, A/B, dual watch, scan) rewrote the words */
  if (owned && (*RX != owned_rx || *TX != owned_tx)) { finish(NO_RESTORE, "lost"); return; }
  r  = interp_ppb(now);                             /* int, linear */
  rx = nom_rx - nom_rx/100000*r/10000 ...           /* use 64-bit or split math in 10 Hz units; apply trim */
  tx = nom_tx + ...
  clamp |rx-nom_rx|, |tx-nom_tx| <= 1500 (10 Hz units = 15 kHz); else finish(RESTORE, "range")
  *TX = tx;                                         /* free; picked up at the next PTT */
  *RX = rx_target_if_hysteresis_exceeded else *RX unchanged;  (applied_rx tracks what the chip is tuned to)
  frequency_band_range_check_and_status_set(*RX, *TX);        /* 0x0300d84c: LNA/PA band bits + TX-allowed bit 5 */
  if (flags_0x3 (0x3e31b) & 0x20) {                 /* shifted TX is outside the stock TX ranges */
      *TX = nom_tx; band_check(*RX,*TX); tx_blocked = 1;      /* stock then decides about nominal */
  } else tx_blocked = 0;
  if (*RX changed) { if (!keyed()) channel_change_apply_squelch(); }   /* 0x0300df08 */
  owned_rx = *RX; owned_tx = *TX; owned = 1;
```
- **Restore (`finish(RESTORE)`)**: `*RX = saved_rx; *TX = saved_tx; frequency_band_range_check_and_status_set(saved_rx, saved_tx);
  if (!keyed()) channel_change_apply_squelch();` If keyed, defer to the next tick (a keyed over is never touched). This is the
  complete inverse of arm: no struct, flash or chip register was changed other than through the stock RX path.
- `NO_RESTORE` (ownership lost) leaves the new channel as stock loaded it.
- The 30 min cap and "track end + 30 s" guarantee tracking cannot stay applied indefinitely after the user walks away.
- 64-bit-free arithmetic: `delta_units = nominal10 / 100000 * r / 10`, i.e. `nominal10 (10 Hz units) * r(ppb) / 1e9`; for 4.4e7 units
  and |r| < 3e4 the product 1.3e12 overflows 32 bits, so divide first (`(nominal10 / 1000) * r / 1000000`, error under 1 unit)
  or use `unsigned long long` (the soft `__muldi3` is in libgcc, check the ld script before relying on it).

### 6.5 Hooks

Existing 1 Hz hook not usable: `aprs_tick_hook` @0x03054888 (`expect 58 11 40 82`) sits behind `[0x3fdc0]&4` (APRS enabled in the CPS),
`[0x40078]` bit 0, mode_state2 in {0,1}, the GPS/fixed-position gate and the `[0x3fdb8]&0x40` / `[0x3ff84+0x2b]&8` exits. It does not run on
most radios.

New hook (nothing in `kraken_ui.patch.json` touches `0x030547ae`; verified by grep):

| Field | Value |
|---|---|
| name | `sat_tick_hook` |
| addr | `0x030547ae` (inside `ui_periodic_maintenance_task_loop` @0x0305476c, right after the `puVar1[1]--` and before `call_hold_led_debounce_dual_timer_tick`'s result is used; reached on every iteration of the 1 Hz event `0x20000` of flag group `0x3fd90`, with no gate in front of it) |
| expect | `00 e0 3b 9d` = `bsr 0x03068224` (checked in the image; `0x03068224` begins `d0 14 61 11 66 83 03 e9`) |
| patch | `bsr krk_hook_sat_tick` (`"expect_branch": {"kind":"bsr","target":"0x03068224"}` in the manifest style of `aprs_send_now_hook`) |
| stub | `void krk_hook_sat_tick(void){ ((void(*)(void))0x03068224)(); krk_satr_tick(); }` (stock call first, then ours; r0-r3 are dead after the call at this site, the loop keeps its state in l0-l7 which a C function preserves) |
| context | "task maint", 0x180-word stack, runs every second including during TX and while a menu is open |
| cost budget | typically < 1 ms; with a retune, the length of `channel_change_apply_squelch` (about tens of ms of register writes and busy waits) |

Optional, only if keeping the screen alive needs it: nothing else is required. `krk_run_screen`'s Back path is unchanged; the screen
simply calls `krk_satr_arm/stop` and reads `krk_satr_state`.

### 6.6 Safety summary (never transmit out of band)

1. TX word only changes between transmissions; the tick is a no-op while `0x3fdc8 & 8`.
2. After every write, `frequency_band_range_check_and_status_set` recomputes the stock TX-allowed bit (`0x3e31b` bit 5) for the shifted
   value. If it is set the TX word is put back to nominal and stock's own gate (`ptt_press_handler`) decides. This inherits the
   model-specific TX ranges (section 1.2) without the patch knowing the model.
3. Hard cap of +/-15 kHz around nominal on both words, and nominal itself must pass `krk_rf_band`.
4. We never key (no call to `radio_init_sequence`, `0x2b674`, `0x2b440`, no PTT flag post). PTT is entirely stock.
5. Digital channels, dual watch, TX-forbidden channels (RX tracking only), scanning and channel changes are refused or end tracking.
6. Time-limited and restored on stop.
7. Nothing is written to flash, to the channel struct, or to the per-unit sector.

## 7. Risks and open items

| # | Risk / unknown | Conf | Mitigation / test |
|---|---|---|---|
| 1 | PTT behaviour inside `krk_run_screen` (does the debounce run in a menu, value of `[0x3e156]`) | LOW | Design assumes PTT from standby. QEMU: `DM32_PTT="t:hold_ms"` (pin 0x2b, polarity assumed) with the screen open and `0x3fdc8` / `0x38468` logged. Do not test on a radio |
| 2 | Audible chop on each RX retune; length unmeasured | MED | Hysteresis; try option B on the bench; measure with the existing `krk_tm_*` microsecond timer |
| 3 | CTCSS after option B (IDLE then RXON without the tone setup) | MED-LOW | Use option A in v1 |
| 4 | Maintenance-task stack/time budget for `channel_change_apply_squelch` | MED | stock already calls it from that task (`channel_change_tx_led_reset`); keep the hook frame small |
| 5 | Cross-band split: TX power/mic calibration follows the loaded record, not the live TX word (`freq_calibration_apply_all`) | MED | v1: loaded record and nominal pair in the same band class, or accept stock's calibration; v2: also write the struct TX field `0x3e1e0` in RAM and call `0x0300db48` once at arm (restore at stop) |
| 6 | RMC/RTC time wrong (no fix, wrong tz) | MED | show time source; refuse arm without time; user adjust +/- seconds |
| 7 | `0x20000` period is exactly 1 s | MED-HIGH | derive time from `OSTickCtr`, not from the tick count |
| 8 | Model byte mapping `[0x3fdc4]` -> TX ranges | MED | not needed by the design (stock check reused), only for documentation |
| 9 | Radio reference oscillator error (hundreds of Hz at 435 MHz) | MED | user trim in Hz |
| 10 | `0x3fdb8 & 4` and `0x3ff84+0x2b` flag meanings are inferred | MED-LOW | extra skips only, harmless if wrong; verify before relying on them as gates |

## 8. Evidence index

- Pair store: `channel_freq_pair_store` @0x0300da88, pool `0x0300db3c` (band literal), `0x0300db44 = 0x00038468`.
- Dispatcher call: `channel_freq_pair_store(iStack_58, local_5c)` in `channel_change_apply_dispatcher` @0x030573e8; VFO case
  `iStack_58 = channel_apply_frequency_offset(...)`.
- `radio_init_sequence` @0x0300e40c: `0x0300e484 lrw a3,[0x300e5c4]; ld.w a0,(a3,0); bsr 0x2b674`, pool word = `0x38468`.
- `rf_tx_enable_sequence` IRAM 0x2b440: `lrw a3,[0x2b500]` = `0x38468`, `bsr 0x2b674`.
- `channel_change_apply_squelch` @0x0300df08: `ld.w a0,(a3,4)` at 0x0300df14 (pool `0x300df50` = `0x38468`), `bsr 0x2b6a0`.
- `channel_switch_teardown_and_apply` @0x0305fa38 ends with `channel_change_apply_squelch()`.
- Maintenance loop hook site `0x030547ae` (`00 e0 3b 9d`, target `0x3068224`); pool `0x305493c` = flag group `0x3fd90`.
- TX-range check: `frequency_band_range_check_and_status_set` @0x0300d84c; TX-allowed consumer in `ptt_press_handler`.
- Time: `gps_nmea_datetime_to_rtc_sync` @0x0301265c, `gps_datetime_format` @0x03011b40, `rtc_register_read` @0x0301c2d4,
  `rtc_decode_calendar_date` @0x0301c2f0, `rtc_date_validate_and_write` @0x0301c498, tz byte `0x38d22`.
- PTT detection chain: `bitwise_not_of_boolean_result` @0x03012f48 (pin 0x2b), `keypad_debounce_and_commit_event` @0x03012f58,
  `keypad_event_maybe_forward_to_dialog` @0x030139c0, `main_ptt_and_channel_event_loop` @0x03012d10.

## Review fixes (2026-10-29)

See "Review fixes (2026-10-29)" in docs/satellite-mode.md: restore only when the words are still ours, reboot detection, and the
RX retune skipped while the PTT pin (`0x03012f48`) is down or the radio is keyed. This supersedes the "residual race"
paragraph of 3.2: the stock code offers no lock to hold across `channel_change_apply_squelch`, so the mitigation is the pin test
right before the write and before the call, and the pending-retune flag.
