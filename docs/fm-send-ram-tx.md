# Software AFSK through the HR_C7000 FM TX path (FM Send RAM): static findings and probe plan

Status: sections 1-5 are static research, written before anything ran on a radio; section 6 is hardware findings.
Tags: [DS] datasheet, [FW] stock firmware, [UNCERTAIN] = inference or untested. DS = HR_C7000 datasheet v2.7 (English;
cited "DS L<line>", line numbers of a Markdown conversion); zh = the Chinese v2.7 datasheet. FW corpus = the decompiled
C of [dm32uv-re](https://github.com/awkaplan/dm32uv-re) (`src/`).
Modem base M = 0x11000000. Companion docs: `rx-audio-fm-mode.md`, `analog-vs-digital-rx-config.md`,
`rx-audio-board-ics.md`, `afsk-tx.md`.

Bottom line: the datasheet gives a complete, CPU-fed analog TX path (TX RAM -> FM modulator -> MOD DAC). Stock
does not use it on analog; stock actively disables its deviation (`fm_dev_coef_t = 0`). Whether the MOD DAC is wired to
FD6818 MICIN on this board is NOT established, so the first probe is a 40 Hz DAC test tone, not AFSK.

## 1. Datasheet facts

1. Two audio sources for analog FM TX (DS L44848-44905, §7.2.2.1; zh L40768-40800):
   - CODEC ADC (default): "data flow does not require CPU to migrate". Needs `M+0x80[0]=0`, `M+0x100[7]=1` (FM),
     `M+0x560[0]=1` (PTT) and the codec ADC enabled (DS L44873-44886).
   - TX RAM: `M+0x80[0]=1` selects "the data in TX RAM" (DS L44887-44899). Text: "usually used in relay mode".
     RAM `0x16000030~0x1600042F`, two slices (`0x16000030~0x1600022F`, `0x16000230~0x1600042F`), "ping-pong access by
     AHB or SPI", 512 bytes = 256 16-bit samples = "32 ms audio playback length" (so 8 kHz).
2. AUDIO_CONTROL (M+0x80, DS L16155-16290): [0] `tx_voice_source` (0 codec/vocoder, 1 MCU RAM; "receiving also needs
   to be configured" - so setting it may also redirect the RX path [UNCERTAIN]); [3] `fm_sidetone_en` (local playback of
   the TX audio); [4] `ahb_wr_voice_en`, [5] `ahb_rd_voice_en` (AHB audio enables, role in FM unstated); [6]
   `fm_play_ctrl` ("select FM playback channel"); [7] `ring_play_ctrl` (prompt-tone FIFO).
3. FM_PTT (M+0x560 [0] RW, DS L55447-55470): "Configure FM to send PTT command, high effective".
4. FM_ADDR_SW (M+0x56c [1:0], listed RO, DS L55604-55625): "FM audio data address space selection control: 1 = CPU
   reads or writes low address data; 2 = CPU reads or writes high address data". The zh text (L50869-50900) says
   "FM send/receive audio data" (收发), so one register serves both RAMs. It names the half the CPU may touch.
   Whether, on TX, hardware flips it as it consumes a half is INFERRED from the RX wording [UNCERTAIN].
5. FM Send RAM map (DS L55715-55765, Table 57): base 0x16000000, FM_VOICE_1 at +0x030..+0x22f, FM_VOICE_2 at
   +0x230..+0x42f, each 512 B; "low address stores high 8-bit PCM" = big-endian int16. RX RAM (Table 58) is a separate area at base 0x160004b0; TX voice ends at 0x1600042f, so they do not overlap.
6. Interrupt (DS L55646-55720, Table 56): `FM_TX_INTERP` = "FM send data processing interrupt", level, high active,
   masked by `SYS_INTERP_MASK` (M+0x39c) bit 15 `fm_tx_interp_mask` (reset 1 = enabled, DS L17110-17125), cleared by
   writing 1 to the matching bit of `SYS_INTERP_CLEAR` (M+0x3a0). Bit 14 = FM_RX, bit 13 = FM_SIG_RX.
7. Deviation and limiter (FM_DEV_COEF, M+0x504, DS L54405-54450): [23:16] `tr_sig_lim` (reset 0x0f) "FM transmitter
   amplitude limiting modulation coefficient"; [15:8] `fm_dev_coef_r` (reset 0x10) RX deviation coefficient;
   [7:0] `fm_dev_coef_t` (reset 0x10) "FM transmitter modulation frequency deviation coefficient". Units not given.
   FM_BANDWIDTH (M+0x500) bit 4 is a pre-emphasis switch, bit 6 an FM bandpass enable (rx doc §1.1).
8. Output stage (DS L44970-45215, §8.1): two baseband DACs (A/B) with single-ended output, "supports two-point
   modulation and single-point modulation". `RF_MODE[6:5]=3` (reset 3) = two-point mode: MOD1 -> crystal, MOD2 -> VCO
   (DS L45052-45066); single-point = DAC output 1 (MOD1) only, modulation via `SIG_CENTER[31:24]` bias (DS
   L45151-45170). Amplitudes: `RF_MOD_BIAS_CTRL` (M+0x114) [7:0] MOD1, [15:8] MOD2, 8.75 mV/step, up to 2240 mV; bias
   `SIG_CENTER` [31:24] MOD1, [23:16] MOD2, ~3.3 mV/step, +-422 mV. `DAC_CONTROL` (M+0x70, DS L15872-15935): [31]
   `dac_data_ctrl` (1 = offset code), [5] `pwda`, [4] `pwdb` (0 = powered), [2] `lpmodea`, [1] `lpmodeb` (low-power;
   0 = off). `RF_MODE[10] two_point_ctr` = 40 Hz sine on the MOD outputs (DS L16745-16750), `RF_MODE[9] up_ctr` = smooth
   power ramp at slot edges. Pin names in the board docs: DAC_VOUT_A/B = J1/J2 (`rx-audio-board-ics.md` §4).
9. The datasheet's own reference design (Fig 63, DS L45151-45170) sends the single-point MOD1 signal into an analog FM
   transceiver (RDA1846S) as its modulation input; that is the same role FD6818 MICIN would play here.
10. FD6818 side (FD6818 programming guide rev 1.0.0, Chinese; line numbers of a Markdown conversion):
    the only audio-in pin is MICIN (PGA -> ADC -> DC filter -> mux with tone1/2 -> limiter/pre-emphasis/LPF -> FM
    modulator; block diagram L90-140, tone bypass L296). DMR BYPASS mode (L2731-2760): "MICIN takes the already
    pulse-shaped 4FSK signal"; TXON = `WRITE(0x47,0x6740)` "MIC ADC straight to TX", then `REG_03H` 0 -> 0xC1FE;
    also `0x7E |= 1<<7` (`dcc_tx_bypass`, L327-341). Deviation: `REG_40H` [12] `dev_en`, [11:8] `dev_sh`, [7:0]
    `dev_lvl`, gain = (256+dev_lvl)>>dev_sh (L1555-1610); mic gain `REG_97H[13:8]`; `REG_50H[9:0]` TX limit,
    `REG_50H[10]` limit bypass, `REG_50H[15]` TX mute; pre-emphasis bypass `REG_9BH`/`audio_emph_tx_bypass`.

## 2. Stock firmware usage

1. HR_C7000 FM TX is keyed on every analog PTT but its voice engine is configured off.
   - `radio_mode_entry_reinit_and_display` @0x0305baec calls `modem_fm_ptt_set(1)` @0x0300c304 (FM_PTT = 1) then
     `radio_init_sequence` @0x0300e40c (RF half). Release: `modem_fm_ptt_set(0)` in `channel_switch_teardown_and_apply`
     @0x0305fa38 / `radio_mode_reset_to_idle` @0x030217b8 (afsk-tx.md §1.1).
   - `hr_c7000_modem_core_register_init` @0x03023608 writes `FM_DEV_COEF = 0x33500`: `tr_sig_lim=3`,
     `fm_dev_coef_r=0x35`, **`fm_dev_coef_t=0`**. So the HR_C7000 FM modulator contributes zero voice deviation on
     analog TX. Stock analog voice therefore almost certainly goes mic -> FD6818 MICIN directly (GPIO 0x2E = 1 on analog).
     The CPS lists "Analog MIC" and "Digital MIC" as separate calibrations.
   - `AUDIO_CONTROL[0]` is never set during analog TX. Writers of `audio_ctrl_bit0_tx_voice_source_set` @0x0305ce5c:
     `audio_vocoder_buffer_flush2` @0x03021a70 (sets 1 only when `bitwise_not_of_nonzero_check()==0`, DMR vocoder flush),
     `tx_voice_source_teardown` @0x03021850 (clears). The reinit sequence @0x030c65f8 also clears bit 0.
   - `audio_ctrl_fm_play_set` @0x0300c2dc (AUDIO_CONTROL[6]) is toggled around analog TX tones and PTT preamble
     (`tx_enable_caller2` @0x0305fbf8, `channel_change_tx_led_reset` @0x030553ec, `radio_mode_entry_reinit_and_display`).
     Its function is unverified. The prompt-tone FIFO (`audio_ctrl_ring_play_set` @0x0301e598, bit 7) is used for local
     beeps (`tx_gate_companded_audio_burst_send` @0x03022fac clears GPIO 0x2F and sets ring_play).
2. FM Send RAM: no stock use as a data buffer. Only reader: `rng_seed_from_fm_voice_bytes` @0x030c6780 (`ld.b` of
   0x16000032/33, 0x16000232/33 as an LCG seed; from `rx-audio-fm-mode.md` §3.6 and `confirmed_findings.json`).
   Corpus grep: no function references `FM_ADDR_SW` (M+0x56c), `SYS_INTERP_LIST` (0x398) or `SYS_INTERP_CLEAR` (0x3a0);
   the DMR RAM users (0x16000960, 0x16000980, 0x160004b0 L2 area) are separate. The 0x16000000 DMR/L2 areas share the
   chip RAM, so the TX voice halves are clear of the 0x960+ vocoder units but not of DMR frame buffers on a digital channel.
3. Interrupt mask: `SYS_INTERP_MASK` is written 0x1187f (init @0x03023608), 0x1800 (`screen_enter_apply_channel_and_rf_mode`
   @0x03038058 line 182), 0 (`hr_c7000_modem_core_reinit_sequence` @0x030c65f8), or restored from a save area
   (`FUN_030c66f0`). Bit 15 (FM_TX) is 0 in all: stock never takes FM_TX_INTERP. `System_inter` handler never sees it.
4. Modem registers stock writes that matter for TX output (all written once at init / calibration):
   `DAC_CONTROL |= 0x80000000; &= ~0x20 (DAC A on); &= ~0x04` (@0x03023608), `RF_MODE[6:5]=3` (two-point),
   `TX_IF_FREQ = 0xbb800`, `SIG_CENTER[31:16]` from flash (`mmio_11000108_high_word_write_from_byte` @0x0302180c, via
   `rf_config_load_from_flash_block2` @0x0300d568), `RF_MOD_BIAS_CTRL[15:0] = amp*0x101` from the per-frequency
   calibration on ANALOG channels only (`mmio_11000114_low_word_duplicate_write` @0x03021830 from
   `freq_calibration_apply_all` @0x0300db48, line 53). So an HR_C7000 MOD amplitude is calibrated per analog channel:
   MOD1/MOD2 are live in analog TX in some role (sub-tone / two-point), though not carrying voice [UNCERTAIN].
5. FD6818 TX bring-up on analog: `fd6818b_retune_and_reinit_sequence` @IRAM 0x2b674 -> `fd6818b_reg47_afout_clear_mode`
   @0x2b550 (`REG_47 &= 0xf0ff`) -> `fd6818b_init_seq_filter_ctcss_chipmode` @0x2b5b8 (`REG_37=0x1d0f`, `REG_52=0x028f`,
   `REG_30`(=REG_03H) 0 then 0xC1FE). Stock analog REG_47 = 0x6042 (`analog-vs-digital-rx-config.md` §2).
6. DMR TX feeding the FD6818: `rf_tx_enable_sequence` @IRAM 0x2b440 runs the same `fd6818b_retune_and_reinit_sequence`,
   no extra modem register writes before keying (it only switches antenna GPIO, waits, retunes, enables PA). The 4FSK
   reaches the FD6818 with `fd6818b_reg47_tx_bypass_or_rx_afout` @IRAM 0x2b51c writing `REG_47 = 0x6740` when struct
   bit `+0x11 & 0x20` is set (digital), called from the preamble tail `tone_ptt_preamble_seq36` @0x03022d94 only when
   `flags_0x8 & 0xf0 == 0x10`. Digital channel setup (`channel_change_apply_dispatcher` @0x030573e8 line 481) clears
   GPIO 0x2E and 0x2F; analog sets both. Board docs read 0x2E as the MICIN source mux (analog mic vs SoC DAC_VOUT)
   (`rx-audio-board-ics.md` §4); that is label-based, not schematic-based [UNCERTAIN].
   Net: the DMR path = {GPIO 0x2E=0, REG_47=0x6740, WORK_MODE digital, MOD DAC fed by the 4FSK modulator}.

## 3. Proposed recipe (analog PTT already up via the stock PTT event, afsk-tx.md §1.4)

Save every register first (read-modify-write only; all in M unless noted). Steps are ordered by risk.

A. Route (no modem writes yet; verify with probe P1/P2 before continuing):
   1. `gpio_clear_output_bit(0x2E)` (analog mic off MICIN, SoC DAC on) [UNCERTAIN: may be the wrong net; if the mic stays
      audible on RX of the other radio the mux is not here]. Leave 0x2F alone (speaker path).
   2. FD6818 `REG_47 = 0x6740` via `fd6818b_reg47_tx_bypass_or_rx_afout`-style write (reads: expect 0x6042 before).
      Bypasses post-ADC audio processing so 1200/2200 Hz tones are not tilted; keeps `REG_40H` deviation stage
      [UNCERTAIN: whether pre-emphasis/limiter are really bypassed by afout_mode 7].
   3. Optional: `REG_7EH |= 0x80` (`dcc_tx_bypass`) per the DMR recipe so 1200 Hz is not DC-filtered.
B. HR_C7000 FM TX with RAM source:
   1. `DAC_CONTROL`: confirm bit 5 = 0 (DAC A on), bit 2 = 0. Do not touch DAC B.
   2. `FM_DEV_COEF = (3<<16) | (0x35<<8) | T` with T from 0x10 upward (reset value); `tr_sig_lim` raise from 3 toward
      0x0f if the output clips low [UNCERTAIN: units unknown; start at reset values 0x0f / 0x10].
   3. `FM_BANDWIDTH` bit 4 (pre-emphasis): keep 1 for first probe (stock value 0x5f), then try 0.
   4. `AUDIO_CONTROL |= 1` (tx_voice_source = RAM). Do not set bits 4/5/6 [UNCERTAIN].
   5. Pre-fill both halves with a mid-scale sample (0) BEFORE step 4 to avoid a click: write 512 zero bytes to each half.
   6. FM_PTT is already 1 from the stock key-up. Do not toggle it.
   7. Keep `SYS_INTERP_MASK` bit 15 = 0. Use polling.
C. Feed (8 kHz, 32 ms per half) - polling `FM_ADDR_SW` as the RX code does:
   - Loop: `a = M[0x56c] & 3`; if `a` changed since last read, write the NEW half indicated by `a` (1 -> 0x16000030..0x1600022f,
     2 -> 0x16000230..0x1600042f) with the next 256 samples, big-endian int16 via `st.b` only (stock uses byte accesses).
     Prefill order: half 1, half 2, then enter the loop. Deadline per half 32 ms; a stock-task `OSTimeDly_ms_wrapper(5)`
     poll cadence is enough. Disable the scheduler only around the 512-byte copy if jitter shows.
   - Which half FM_ADDR_SW names on TX (the one being sent, or the one to fill) is the key open item [UNCERTAIN]; the probe
     decides it (P3). If it names the half being consumed, write the OTHER half.
   - Sample generator: phase accumulator `ph += 2^32*f/8000` (f = 1200/2200 selected by the bit stream), amplitude from a
     256-entry sine table, bit clock accumulator `bt += 1200/8000` per sample so bit edges fall on fractional samples
     (6.667 samples/bit; phase continuous across bits by construction). Append 2-3 flag bytes of idle and 20-40 ms of
     tone preamble; end with 1 half of zeros so the carrier drops without a click.
   - Mark/space level: set per-tone amplitude to equalize deviation after the pre-emphasis (see P4).
D. Stop and restore (reverse order):
   1. Write one half of zeros, wait one half period; `AUDIO_CONTROL &= ~1`.
   2. Restore `FM_DEV_COEF`, `FM_BANDWIDTH`, `REG_47` (to 0x6042 via `fd6818b_reg47_afout_clear_mode` semantics),
      `REG_7EH`, GPIO 0x2E (set), saved `SYS_INTERP_MASK` (should be untouched).
   3. Release PTT through the stock event (afsk-tx.md §1.4), not by writing FM_PTT.

## 4. Probe plan (Audio Probe screen: modem and FD6818 register read/write; PCM capture on a second radio)

P0 Read-only snapshot during stock analog TX (hold PTT, no writes): `M+0x80`, `0x100` (expect 0x80/0xEE), `0x104`
   (RF_MODE), `0x114` (MOD amplitude), `0x108`, `0x70`, `0x504` (expect 0x33500), `0x500` (0x5f), `0x560` (1),
   `0x56c` (watch for change across 100 ms), `0x398` bit 15 (raw FM_TX flag toggling every 32 ms = engine running),
   FD6818 `REG_47` (0x6042?), `REG_40`, `REG_97`, `REG_7E`, GPIO 0x2E/0x2F. Also the first bytes of 0x16000030 / 0x16000230
   (the codec-ADC recording lands there, DS L44907-44920: with the mic open they should hold live mic audio; that proves
   the TX engine is clocked). This costs nothing and answers "is the FM TX block alive with FM_PTT=1".
P1 Does the MOD DAC reach MICIN? During stock analog TX set `RF_MODE[10]=1` (40 Hz test sine, DS L16745) with the mic
   quiet. Capture the other radio's audio: a 40 Hz modulation is below the second radio's HPF, so also use GPIO 0x2E=0
   and look for DC/step on the other side, or keep a scope on FD6818 MICIN if available. Pass: any deviation change
   tracking the 40 Hz bit or the `RF_MOD_BIAS_CTRL[7:0]` sweep. Fail: no change -> DAC A is not on MICIN; stop and try
   DAC B (`pwdb` = bit 4, `RF_MOD_BIAS_CTRL[15:8]`) one at a time.
P2 Deviation scaling: with source RAM (`AUDIO_CONTROL[0]=1`) and a constant 1 kHz sine pre-filled in both halves (refilled
   by the polling loop), sweep `fm_dev_coef_t` 0x10..0x40 and `RF_MOD_BIAS_CTRL[7:0]` 0x20..0xFF; record RX level on the
   second radio. Expect monotone growth; find the value that gives ~+-2.5 kHz (0.7 of stock mic deviation).
P3 Half semantics: fill half 1 with +A DC, half 2 with -A DC; poll 0x56c with timestamps and watch the other radio's
   audio. If the pattern on air flips polarity in step with 0x56c, FM_ADDR_SW is the consumed half (or the fill half
   if the flip occurs one period later). Also measure the 0x56c period (expect 32 ms) and the behaviour on underrun
   (stop refilling for 3 halves: repeat last half, silence, or carrier drop).
P4 Tone fidelity: 1200 Hz and 2200 Hz at equal DAC amplitude, capture PCM on the second radio, compare amplitude, phase
   continuity at 6.667-sample bit boundaries (transmit alternating 1200/2200 at 1200 baud) and spectrum. Repeat with
   `FM_BANDWIDTH` bit 4 = 0 and `REG_47 = 0x6742`/0x6740 to see which one is flat. Final check: decode with the APRS
   decoder on the second radio's capture.
P5 Stop/restore: after each probe confirm (read-back) all saved registers and that a normal voice PTT still works.

## 5. Open questions

1. Is the HR_C7000 MOD output (DAC A / J1) electrically on FD6818 MICIN on analog PTT, or only through GPIO 0x2E's other
   position? (label-based; no schematic.) P1 answers it.
2. Does FM mode with `AUDIO_CONTROL[0]=1` output audio to the MOD DAC at all, given stock keeps `fm_dev_coef_t=0` and the
   datasheet only describes RAM-source use "in relay mode"? What does `tr_sig_lim` do to a sine? (P2.)
3. FM_ADDR_SW on TX: consumed half vs fill half, and whether it is written by hardware (RO in the table) (P3).
4. Is FM_TX_INTERP required to be acknowledged for the engine to keep advancing (polling-only may stall after the first
   half, as for RX in `rx-audio-fm-mode.md` §7.2)? Fallback: unmask bit 15, but stock's `System_inter` handler has no FM
   code - the patch would need its own ISR hook and `SYS_INTERP_CLEAR` writes (not recommended before P3).
5. Pre-emphasis and limiter inside the HR_C7000 FM TX path (`FM_BANDWIDTH[4]`, `tr_sig_lim`) and the FD6818 post-MICIN
   chain with `REG_47 = 0x6740`: expected tilt of 1200 vs 2200 Hz.
6. Does `AUDIO_CONTROL[0]=1` also disturb RX audio ("receiving also needs to be configured")? Confirm RX still works after
   restore; the reinit sequence @0x030c65f8 clears the bit on a modem reinit.
7. Sample sign/scale: DAC offset-code vs complement mode (`DAC_CONTROL[31]=1` in stock) versus int16 in RAM: does a 0 sample
   map to the DAC mid-scale? P2/P3 DC steps settle it.
8. Whether CTCSS/DCS on analog channels (HR_C7000 vs FD6818 `ctcss_dcs_tone_apply` @0x0300e10c) shares the MOD path.
   Run the beacon on a channel with no sub-tone for the first tests.

## 6. Hardware findings 2026-11-06: what a send from standby needs (stock power save)

Found with the CW ID (`docs/cw-id.md`), the first FM Send RAM send started from standby instead of
from one of our screens. UHF analog simplex channel, Low power, dummy load, `PowerSave` = Save 1:4.
A send from standby failed in two separate ways; both come from stock power save
(`rf_mode_debounce_state_machine` @0x03054ca8, state bytes at `0x3ebf0`, `[1]` = 2 while asleep).

### 6.1 The FM clocks are off ("Modem stalled")

- Symptom: the ID keyed, carried no audio and ended as "Modem stalled" (no `FM_ADDR_SW` change).
  Sampled over the cable during a keyed attempt: `CALL_ACTIVE` bit 3 set, `FM_PTT` = 1,
  `WORK_MODE` = 0xEE, `AUDIO_CONTROL` = 3, FD6818 `REG_47H` = 0x6740, and `FM_ADDR_SW` = 0 throughout.
- `CLK_MGR_REG0x2c` (`0x1100002c`) read `0x00f0073c`: bit 7 `modem_clk_fmrx_en` and bit 6
  `modem_clk_fmtx_en` both 0.
- Writing `0x00f007fc` (|= 0xc0) over the cable: `FM_ADDR_SW` started cycling 1/2 at once. Written
  during a stalled attempt, that attempt ran to its end ("Sent"); so did the radio's next ID.
- The bits stayed set while the cable was polled (polling keeps the radio out of power save) and
  were gone again after about 30 s with the cable quiet.

Who writes the register (decompiled C only; no scan of raw stores):

| function | write | bits 7:6 | when |
|---|---|---|---|
| `modem_rf_mode_transition_apply` @0x0301e628 | `\|= 0xc0` | set | an analog channel is applied |
| `clock_uart_reconfigure` @0x0305499c | `= 0x00d00200`, `\|= 0x200000` | cleared | power-save sleep (`fd6818b_partial_display_mode_reconfigure` @0x03054c54 from the state machine) |
| `crystal_osc_and_timer5_init` @0x03054a6c | `= 0xfff0ff3c` (literal at 0x03054bb4) | cleared | every wake: `rf_transceiver_mode_init` @0x03054bb8 from the state machine and from `event_queue_reset` @0x03054d80 |
| `hr_c7000_modem_core_reset_and_poll_ready` @0x030043c8 | `= 0xffffffff` | set | boot |

So after the first power-save sleep the FM engines (TX RAM and RX RAM) are unclocked until the next
analog channel change. Stock does not notice: its analog TX audio is the mic into the FD6818, and
it never reads the FM RX RAM. This also explains an earlier observation (2026-11-01) that the FM receive RAM
"did not run until the channel was reloaded".

Fix: `route_on()` sets bits 7:6 when they are not both set, `route_ok()` checks them with the rest
of the route, and they are left on afterwards (stock's own state after a channel change).

### 6.2 The radio is asleep when the PTT event arrives (silent carrier, "Sent")

- Symptom with 6.1 fixed: every ID keyed for the right length and read "Sent", but only about one
  in six carried Morse; the rest were an unmodulated carrier. While the cable was being polled
  (radio held awake) every ID carried Morse.
- Cause: stock wakes a power-saving radio in the keypad code, not in the PTT code.
  `keypad_poll_and_dispatch_key_event` @0x03013744 calls `event_queue_reset` (which runs
  `rf_transceiver_mode_init(1)` when state `[1]` = 2) before the key handler. The PTT event loop
  (`main_ptt_and_channel_event_loop` @0x03012d10 -> `ptt_press_handler` @0x0305bfb8 ->
  `tx_key_from_vfo_mode_guard` -> `radio_mode_entry_reinit_and_display`) has no wake, and once
  `call_active` bit 3 is set the state machine is gated off, so nothing wakes it later either.
  A `PTT_PRESS` posted during the sleep part of the cycle therefore keys the radio in the sleep
  clock setup (`CLK_MGR_REG0x2c` = `0x00f00200`: bits 2-5, 8 and 10 off as well as 7:6). Setting
  7:6 alone lets the FM Send RAM be consumed, but nothing reaches the modulator.
- At Save 1:4 the radio is asleep about 80 % of the time, which is the one-in-six.
- Fix: `claim()` calls `event_queue_reset()` before any send (and waits 60 ms when the radio was
  asleep, so the busy-channel check has a running receiver). Stock's own beacon tick does the same
  before its DMR send.

### 6.3 The tick runs fast in power save

`OS_TICKS` advanced 12744 in 61.9 s of wall time with the cable quiet (2.06x). The sleep path
reprograms the clocks and timer ch0 (`LoadCount` = clock variable / 100: 840000 awake, 240000
asleep), and the result is not 100 Hz. Every tick-based interval is short while the radio
power-saves: the CW ID's "1 min" came out at about 30 s. The CW ID corrects its own timer against
the RTC seconds (`docs/cw-id.md`); the analog automatic beacon's interval is not corrected.

Confirmed on the air with 6.1 to 6.3 fixed (cable quiet, Save 1:4, SDR recording): five CW IDs in a
row carried their Morse, 63.3-64.5 s apart at Interval 1 min (`docs/cw-id.md`).

The receive side (APRS RX, RX Monitor, Burst Voice receive) reads the FM RX RAM and depends on the
same clocks and on the radio being awake; it is not changed here.
