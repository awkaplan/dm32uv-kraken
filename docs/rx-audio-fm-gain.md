# HR_C7000 FM RX engine runs but output is near-silent: static analysis and reversible experiments

Status: STATIC research (datasheet + stock firmware corpus). No radio touched. Everything marked
HYPOTHESIS is a prediction for a probe to confirm or kill.

Hardware fact being explained (2026-10-15, stock firmware + read-only probe, analog channel):
- WORK_MODE (0x11000100) bit7 = 1, FM_ADDR_SW (0x1100056c) toggles 1/2 every 32 ms.
- FM RX RAM halves (0x160004e0 / 0x160006e0, 256 x s16 big-endian, 8 kHz) are rewritten.
- Content: mean -8, RMS 2 with no signal, RMS 3 with a strong audible 1 kHz tone. Zero-cross frequency 200-500 Hz (noise).
- So the tone adds only about RMS 2.2 on top of a 2 LSB floor: roughly -55 to -60 dB below a healthy 16-bit audio level.

Sources:
- DS = HR_C7000 datasheet v2.7 (English; line numbers refer to a text conversion of it).
- FD = FD6818 programming guide rev 1.0.0 (Chinese edition).
- FW = the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) decompiled corpus, `src/` (stock 1.01.048).
- Prior: [rx-audio-fm-mode.md](rx-audio-fm-mode.md).
- The OpenRTX DM-32 port was not used.

---------------------------------------------------------------------------------------------

## 1. The FM RX signal chain (answer to Q1)

### 1.1 What the datasheet actually documents

The datasheet gives NO internal block diagram of the FM RX engine. What it does say:
- Input side, AF mode (DS L18365-18400, §8.2.3.1, plus RF_MODE §4.6.5.8 L6313-6420):
  - RF_MODE[24] `af_mode` = 1 makes the chip take the modem ADC (single-ended, VINP = signal, VINM/VCM = fixed AF bias) as the PHASE signal.
  - "The signal entering HR_C7000 is a frequency-modulated signal and needs to be [demodulated] within the chip; gain and offset are adjusted inside the chip."
  - Gain: RF_MODE[23:16] `af_recv_ctrl`, amplitude = in * af_recv_ctrl/16 (0x26 = 2.375x).
  - PHASE_OUT 0x128 reads back the post-gain AF phase range: [29:16] max, [13:0] min, 14-bit. Healthy value is about -1300..+1300 at 2.749 kHz deviation, -47 dBm in.
  - RF_MODE[25] `phase_avg_en`: recommended ON "to avoid interference of the HF signal envelope on the AF phase signal".
  - The AF bias comes from a CPU DAC on the board.
- Output side (DS L16696-16706 §6.4.3.3; L18090-18101 §7.2.2.2; L18118-18126 §7.2.2.3.2):
  - "The RF receiving signal is in MODEM internally completed FM demodulation, the output audio data is directly given to CODEC DAC" (the data flow needs no CPU).
  - RX recording to RAM 0x160004e0..0x160008df is "enabled by default" in FM mode. It is a tap of the signal that goes to the codec DAC.
- FM block register facts (DS L22395-22600, ch.11):
  - FM_BANDWIDTH 0x500: [6] `fmbpfon` FM bandpass filter on; [4] `fmpreemphasison`; [3] `fmbandwidth` (TX); [2] `fmbandwidth_adj` (only for baseband IQ / IF IQ TX); [1] `fmbandwidth_r` RX 12.5/25 kHz; [0] `fmbandwidth_ctc`.
  - FM_DEV_COEF 0x504: [23:16] `tr_sig_lim` (TX limiter), [15:8] `fm_dev_coef_r` (RX deviation coefficient, reset 0x10), [7:0] `fm_dev_coef_t` (TX).
  - FM_SQL 0x508: RO 16-bit squelch metric computed over the audio band. "System default has SQL enabled." Higher = no valid in-band signal. §11.5 says the CPU reads it and decides whether to shut the audio path. The datasheet describes no hardware mute gate driven by it, and no programmable threshold register.
  - FM_FUNCTION_COEF1..8 (0x530..0x54C): the English table title says "Receive demodulation coefficient", but the register bodies are the DTMF/tone-detect Goertzel coefficients: 697/770/852/941/1209/1336/1477/1633 Hz (DS L22794-22889, examples L23575-23590). They are NOT audio-demodulator filter coefficients. Stock never writing them is irrelevant to RX audio level.
  - SUBVOICE_COEF 0x50C, FM_MOD 0x510, CTCSS/CDCSS/DTMF registers: sub-tone and signaling only. The tone coefficient sampling clock is 32 kHz, so the FM block's internal audio rate is probably 32 kHz, decimated to 8 kHz at the RAM/codec.
- Audio routing: AUDIO_CONTROL 0x80 [6] `fm_play_ctrl` "Select FM playback channel" (DS L6195-6215). It is documented only by that one line. Datasheet §7.2.2.2 recipe lists only 0x80[0]=0, WORK_MODE[7]=1 and the codec DAC bits; it does NOT mention bit6. Other bits: [0] tx_voice_source, [3] fm_sidetone_en, [7] ring_play_ctrl.
- Clocks: `mdem_clk_fm_rx` = 9.8304 MHz (DS L3331). Gate = CLK_MGR_REG0x2c[7] `modem_clk_fmrx_en`. Baseband receive clock gate = [9] `modem_clk_rx_en`. 8 kHz / 512 kHz clock gates = [2] / [3]. SYS_SOFT_RSTN [2] `fm_soft_rstn`, [1] `phy_soft_rstn`, [5] `adc_soft_rstn`.

### 1.2 Inferred chain (HYPOTHESIS where marked)

    FD6818 AFOUT (REG_47H afout_mode, then DC bias from CPU DAC)
      -> modem ADC (single-ended, ADC_CONTROL 0x74 enadc0/1, semode)
      -> [gain RF_MODE[23:16] af_recv_ctrl/16, optional averaging RF_MODE[25]]   <- observable as PHASE_OUT 0x128
      -> treated as the PHASE signal            (HYPOTHESIS: the FM demod block takes the same phase tap as the DMR 4FSK slicer)
      -> FM block: FM bandpass (FM_BANDWIDTH[6]), RX bandwidth (FM_BANDWIDTH[1]), de-emphasis (no switch found; [4] is described as a pre-emphasis/TX switch),
         deviation scaling fm_dev_coef_r (FM_DEV_COEF[15:8]), SQL metric (FM_SQL), CTCSS/DCS/DTMF decoders
      -> 32 kHz -> 8 kHz decimation
      -> (a) RX RAM ping-pong 0x160004e0/0x160006e0 (+FM_ADDR_SW)  and (b) codec DAC (path chosen by AUDIO_CONTROL; maybe bit6)

Answers:
- AF mode: does the FM block expect demodulated AF or does it demodulate itself?
  - Datasheet statement: in AF mode the chip input is "a frequency-modulated signal" (i.e. the board's discriminator output) that the chip treats as phase, and gain-scales.
  - The FM block then does its own "FM demodulation" on that phase signal. Whether it differentiates (phase -> frequency) or passes it through is NOT documented.
  - The DMR path uses the SAME input as discriminator output and works, so the ADC / gain / bias stage is probably fine.
  - UNVERIFIED: the FM demod may have been designed for the RF-IQ/IF phase (integral of frequency) and then output audio = d(phase)/dt * fm_dev_coef_r. Feeding it an already-differentiated discriminator signal (our case) would give a high-pass-tilted, strongly attenuated result.
- PHASE_OUT is the only datasheet-visible measurement point between the ADC and the FM block. It decides whether the ADC side is healthy (section 4, step E0).
- Mute/squelch/gating stages: the datasheet documents none in hardware except the SQL metric (CPU-decided). There may be an undocumented internal squelch gate on the output. The flat result (RMS 2 with and without tone, mean -8) looks more like a closed gate or a dead input than a mere -55 dB gain error. A -55 dB gain error would still show the tone clearly in a Goertzel at 1 kHz; this was not checked.

---------------------------------------------------------------------------------------------

## 2. Candidate explanations ranked (answer to Q2)

| # | Candidate | Plausibility | Why |
|---|---|---|---|
| A | The FM block receives no usable signal because the AF input differs on analog vs digital, or the modem front end is gated (PHASE_OUT small / flat) | medium-high | Stock analog and digital configure FD6818 REG_47H differently (below). Nothing in stock ever reads PHASE_OUT, so nobody verified AF reaches the modem on analog channels. |
| B | Undocumented output gate: FM RX audio to RAM/DAC is gated by AUDIO_CONTROL[6] `fm_play_ctrl` (stock leaves it 0 on plain RX; sets it only around TX tones / sidetone / factory paths) | medium | It is the only "FM playback channel" select in the register map. Stock sets it 1 before FM playback of generated tones and clears it after. The RAM tap may follow the same mux. |
| C | FM bandpass (FM_BANDWIDTH[6] fmbpfon = 1 from stock 0x5f) sits before the demod and is tuned for an RF-IF/baseband phase signal; an AF-band input is rejected | medium | Stock writes 0x5f (all filter bits on). Never tested in AF mode. A bandpass that rejects audio-rate "phase" would give exactly "engine runs, almost nothing out". |
| D | Scale: demod expects a much larger phase signal than the discriminator provides; fm_dev_coef_r 0x35 is not enough | medium-low | Gain headroom available is only af_recv_ctrl x6.7 (+16.5 dB) and fm_dev_coef_r x4.9 (+13.6 dB), +30 dB total, short of the observed -55..-60 dB. Could be part of the story, not all. |
| E | Internal SQL gate closed because FM_SQL thinks there is no in-band signal (circular with A) | medium-low | Datasheet says the CPU decides; but an internal gate cannot be ruled out. Reading FM_SQL with and without tone distinguishes. |
| F | DMR-tuned af_recv_ctrl=0x26 clipping | LOW | Clipping would make loud garbage, not silence. Output is near zero. |
| G | AF gain 0 / AF disabled | RULED OUT by stock values | register_init sets af_mode=1, af_recv_ctrl=0x26 (see §3). RF_MODE should read 0x03xx_8060-ish. Confirm by reading. |
| H | "Recording enable" bit | LOW | Datasheet says recording is default-on; the RAM is demonstrably being rewritten. |
| I | FM_FUNCTION_COEF*/FM_DECODE_FLAG* unset | RULED OUT | Those are tone-detect coefficients (§1.1). |
| J | Clock / reset gating (CLK_MGR 0x2c [7],[9],[2],[3]; SYS_SOFT_RSTN [1],[2],[5]) | low-medium | FM clock demonstrably runs (ping-pong toggles). But [9] `modem_clk_rx_en` and `phy_soft_rstn` gate the ADC/phase front end, and they were not in the probe's read-out. Stock writes SYS_SOFT_RSTN=0xfffffffc (bits 1:0 = 0) in analog mode; whether that is a self-releasing pulse or a held reset is not known (see §3.2). |

Strongest static lead (A): the analog and digital configurations of the FD6818 AFOUT differ, and the
HR_C7000 FM input is the FD6818 AFOUT:
- Datasheet BYPASS/DMR recipe (FD L1839-1881): RXON uses REG_47H = 0x6140, "FM demodulated, sent directly to AFOUT".
- Stock digital channel (`FUN_0002b6a0`, IRAM): REG_47H = 0x140 (afout_mode = 0x01, invert 0).
- Stock analog channel (same function, fall-through): REG_47H = 0x6042 (afout_mode = 0x00, afout_invert [13] = 1).
- Stock default init `fd6818b_default_register_init` @0x0300ecb8: REG_47H = 0x100.
- So the speaker (GPIO 0x2F = FD6818 path) hears afout_mode 0, which the FD6818 guide does not document (documented RX AFOUT is 0x11, mute is 0x10, beep 0x18).
- Whatever mode 0 is, the datasheet-defined "FM discriminator straight to AFOUT" is mode 1. Mode 0 may be post-filter, de-emphasised, HPF-300 Hz audio, which is not what the modem AF stage was designed for (DMR needs raw discriminator with DC).
- It is also possible that mode 0 AFOUT carries a different DC bias and a smaller swing than the ADC bias assumes.

---------------------------------------------------------------------------------------------

## 3. What stock writes (answer to Q3)

Base 0x11000000. All from the decompiled corpus; function names are manifest names.

### 3.1 Registers the question lists

| Register | Stock value / write | Where |
|---|---|---|
| RF_MODE 0x104 | `register_init` @0x03023608: `\|= 0x2000000` (phase_avg_en), `\|= 0x1000000` (af_mode), `\|= 0x260000` (af_recv_ctrl = 0x26), `&= 0xffffff9f` then `\|= (FUN_0300c2c8 & 3) << 5` (TX two-point), `&= 0xffff9fff` then `\|= (FUN_0300c2c4 & 3) << 13` (= 0 since FUN_0300c2c4 = 4), `\|= 0x8000`, end `&= 0x5fffffff`. Analog/digital transitions only touch bit11 (`modem_rf_mode_transition_apply` @0x0301e628: `\|= 0x800` digital relay, `&= ~0x800` otherwise). `hr_c7000_modem_core_reinit_sequence` @0x030c65f8 (test/vframe path via FUN_030c59ac, vframe_message_poll_and_dispatch_3x @0x030c67fc) writes `RF_MODE = 0x10e0` (AF mode OFF) and restores the saved value later via FUN_030c66f0. Normal analog operation never runs it. |
| AUDIO_CONTROL 0x80 | bit0 cleared (`audio_ctrl_bit0_tx_voice_source_set` @0x0305ce5c, reinit @0x030c65f8). bit6 `fm_play_ctrl` via `audio_ctrl_fm_play_set` @0x0300c2dc: set to 1 in `channel_type_apply_tone_idx*` (@0x030595d4..0x030595f2), `factory_service_command_dispatch` @0x030682bc, `channel_change_tx_led_reset` @0x030553ec state 1; cleared at the end of each. Normal analog RX leaves it 0. bit7 via `audio_ctrl_ring_play_set` @0x0301e598. `AUDIO_BUFFER_CLR = 3` at init. |
| ADC_CONTROL 0x74 | `\|= 0x41c3` at init; `mmio_11000074_bits_7_8_set_clear` @0x0301e578 clears/sets bits 8:7; `mmio_11000074_bits78_set_from_status_nibble` @0x0305ba70 sets them for channel-type nibble 0 (analog) or 0x10. Net on analog: 0x41c3 \| 0x180 = 0x43c3. |
| DAC_CONTROL 0x70 | init: `\|= 0x80000000`, `&= ~0x20`, `&= ~0x4`. |
| FM_BANDWIDTH 0x500 | 0x5f via `modem_fm_bandwidth_write_0x5f` @0x0300c2cc (also in the TX-ish `channel_change_tx_led_reset` states). |
| FM_DEV_COEF 0x504 | 0x33500 at init (fm_dev_coef_r = 0x35, tr_sig_lim = 3, fm_dev_coef_t = 0). |
| FM_SQL 0x508 (RO), FM_DECODE_FLAG1/2, FM_ADDR_SW, SUBVOICE_COEF, FM_MOD, FM_FUNCTION_COEF* | never accessed. Reset defaults stay (e.g. SUBVOICE_COEF 0x07d02810). |
| FM_PTT 0x560 | 0 on RX (`modem_fm_ptt_set`). |
| WORK_MODE 0x100 | 0x6e at init; analog: `\|= 0x80`; sequencer writes 0x80 for analog modes. |
| PHASE_OUT 0x128, RSSI_BIAS_VALUE 0x11c, SIG_CENTER 0x108 | never accessed (no reads, no writes; SIG_CENTER stays 0). |
| REG_0x168 (undocumented) | 0x14 with RF_RECV_DELAY = 14000 (AF-type branch, which is the one taken), else 0x98 / 0. `radio_mode_transition_sequencer` @0x03021670 writes 0x98 / RF_RECV_DELAY = 0 for modes >= 0xb. |
| CLK_MGR_REG0x2c 0x2c | boot `0xffffffff`; `crystal_osc_and_timer5_init` @0x03054a6c literal 0xfff0ff3c (bits 7:6 off); `clock_uart_reconfigure` @0x0305499c `= 0xd00200` then `\|= 0x200000` (bits 7:6, 3:2 off, bit 9 on); analog channel change `\|= 0xc0`. Final value after a UART reconfigure after the last channel change would have bits 7:6 clear, but the FM engine is observed running, so not the case. |
| SYS_INTERP_MASK 0x39c | 0x1187f at init, bits 15:13 = 0 always. |

### 3.2 SYS_SOFT_RSTN 0x00
- init: 400 (0x190); `modem_queue_drain_and_stop` @0x0302357c: 0x1fc; `radio_mode_transition_sequencer` / `radio_mode_reset_to_idle` @0x030217b8: 0xfffffffc; `hr_c7000_modem_reset_sequence` @0x03021b80: RMW clears bits 3 and 4 and never sets them back.
- Every one of these leaves some reset bits written as 0 and no code writes them back to 1. So these writes are almost certainly self-releasing pulses (write 0 triggers a reset, bit returns to 1), not held resets. That also explains how DMR RX works with 0xfffffffc being written.
- Consequence: do not treat SYS_SOFT_RSTN bits as evidence of a held PHY reset unless a readback shows 0. READ it; do not write it.

### 3.3 FD6818 side (what the modem ADC sees)
- Analog channel retune `FUN_0002b6a0` (IRAM 0x0002b6a0): `REG_47H = 0x6042` (afout_mode 0, invert 1), after `fd6818b_reg48_cic_mode_write` and `fd6818b_filter_select_then_chipmode_normal`.
- Digital channel same function: `REG_47H = 0x140` (mode 1, invert 0), `REG_3FH = 0`.
- Tone playback: `fd6818b_reg47_write_byte_or_6042(3)` = 0x6342 (mode 3), restore with `(0)` = 0x6042. `fd6818b_reg47_tx_bypass_or_rx_afout` @0x0002b51c: `REG_47H = (REG_47H & 0xf0ff) | 0x100`, or 0x6740 if a flag bit is set. `fd6818b_reg47_afout_clear_mode` @0x0002b550: `REG_47H &= 0xf0ff`.

---------------------------------------------------------------------------------------------

## 4. Ranked reversible experiments (answer to Q4)

Global rules for all steps:
- Only on an analog channel, receiving, not transmitting. Run in the patch's menu/screen task. No PTT, no TX path in between.
- Read-modify-write exactly the masked bits listed. Save the original word first, restore it afterwards (also on every exit path of the screen).
- NEVER write 0x39c SYS_INTERP_MASK, 0x3a0 SYS_INTERP_CLEAR, WORK_MODE 0x100, SYS_SOFT_RSTN 0x00, FM_PTT 0x560, TX fields of FM_DEV_COEF/FM_BANDWIDTH, GPIO 0x2E/0x2F/0x35.
- Settle 100 ms after each write, then measure about 32 FM_ADDR_SW halves (about 1 s): mean, RMS, peak, Goertzel at the tone frequency (1000 Hz), plus FM_SQL min/max. Compare against the baseline taken immediately before (same conditions: no signal; strong 1 kHz tone).
- Success criterion: RMS clearly above 3 and the zero-crossing / Goertzel frequency locking onto the tone. Magnitude should scale with the knob in the expected direction.
- Stock tasks also RMW AUDIO_CONTROL and (on channel change) other registers; do the experiment with the channel locked and no keypad-driven channel change in flight.

### E0. Read-only diagnostics, do first (no writes, zero risk)
Read and log (no signal, then strong 1 kHz tone, then carrier-only):
- PHASE_OUT 0x128: max = bits [29:16], min = bits [13:0] (14-bit).
- RSSI_BIAS_VALUE 0x11c: rssi_value [31:16], adc_bias_i [15:8], adc_bias_q [7:0] (DS L18893-18902). Shows whether the ADC sees an input DC level and how strong the signal energy is.
- FM_SQL 0x508.
- Config readback: CLK_MGR 0x2c (expect bits 9,7,6,3,2 = 1), SYS_SOFT_RSTN 0x00 (expect 0x1ff-ish, all 1), RF_MODE 0x104 (expect bits 25,24 = 1, [23:16] = 0x26, bit15 = 1), ADC_CONTROL 0x74 (expect 0x43c3), AUDIO_CONTROL 0x80 (expect bit6 = 0), FM_BANDWIDTH 0x500 (0x5f), FM_DEV_COEF 0x504 (0x33500).
- Decision tree:
  - PHASE_OUT swing about 0 or unchanged by the tone (and adc_bias_* about 0 or rail): the AF is not reaching the ADC or is on the wrong level. Then go to E5/E6 (FD6818 side, bias). RAM silence is then NOT an FM-engine problem.
  - PHASE_OUT swing hundreds to about +-1300 and rising with the tone: the ADC / gain stage is healthy. The loss is inside the FM block or its output gate. Then E1..E4.
  - FM_SQL moves low when a tone is present: the engine believes it sees a signal and a downstream gate or scale is wrong (E1, E3). FM_SQL stays high/constant: the demod sees nothing (E2, E5).

### E1. AUDIO_CONTROL bit6 `fm_play_ctrl` = 1 (most likely cheap fix for a gate)
- Original: read 0x11000080. Test: write `orig | 0x40`. Restore: write back `orig`.
- Mask: only bit6. Do not touch bit7 (ring_play), bit3, bit0.
- Evidence: only documented "FM playback channel" select; stock sets it exactly when it wants the modem FM path to play; stock leaves 0 on plain RX. Hypothesis that the RX RAM tap follows the same select.
- Risk: low. Speaker stays on the FD6818 direct path (GPIO 0x2F = 1). The codec/PWM path is not selected, so nothing audible changes. Stock toggles this bit often (tone playback, channel change); do not overlap with a tone/beep (key beep) or a channel change. Expect no effect on TX.

### E2. FM bandpass off: FM_BANDWIDTH bit6 `fmbpfon`
- Original: read 0x11000500 (expect 0x5f). Test: write `orig & ~0x40` (0x1f). Restore: `orig`.
- Mask: only bit6. Leave bits 3 and 2 (TX bandwidth) and bit4.
- Evidence: a bandpass in front of the demod, tuned for an RF/IF-derived phase signal, would reject an audio-band AF input; stock keeps it ON (0x5f) without any AF-mode check.
- Also test (separate step): bit4 `fmpreemphasison` = 0 (0x4f). If the demod includes a de-emphasis tied to this bit the level / tilt changes; expected effect is a few dB of tilt, not 55 dB.
- Risk: low if no TX during the test. TX would lose its bandpass only while the bit is cleared; hard rule: restore before leaving the screen, never key PTT during it. The bit rewrites back to 0x5f anyway at the next `modem_fm_bandwidth_write_0x5f` (TX state 0/1).

### E3. Gain boost (does the output scale, and how far does it go?)
- E3a: FM_DEV_COEF `fm_dev_coef_r` [15:8]: 0x35 -> 0xFF. RMW mask 0x0000ff00 only (keep tr_sig_lim [23:16] = 3, fm_dev_coef_t [7:0] = 0). Expect output x4.8 (+13.6 dB) if it scales RX output linearly. Restore 0x35.
- E3b: RF_MODE `af_recv_ctrl` [23:16]: 0x26 -> 0x80 first, then 0xFF. RMW mask 0x00ff0000 only. Keep bits 25,24 = 1, and bits 31 and 29 = 0. Watch PHASE_OUT: stay below about +-8000 (14-bit); if PHASE_OUT saturates, back off. Restore 0x26.
- Combined E3a+E3b: +30 dB total. If the engine is merely under-scaled, RMS 3 should rise to about 90 (real gain beyond ~+30 dB is not available from these knobs). If RMS barely moves at all (3 -> 3), there is no signal in the FM path regardless of gain (points to A/E/B/C).
- Risk: E3a rx-only field. E3b changes the DMR AF scale too, but we are on an analog channel and it is restored; DMR sync is not running. Do not leave a DMR channel selected while it is modified.

### E4. Drive the internal ADC front-end clock (only if E0 shows it is off)
- If CLK_MGR 0x2c bit9 `modem_clk_rx_en` reads 0 on the analog channel: write `orig | 0x200`. Restore `orig`. If bit2 or bit3 (8k / 512k clock gates) read 0, same treatment. Low risk, stock sets bit9 at boot.
- If SYS_SOFT_RSTN reads with bit1 or bit5 = 0 on an analog channel: report; do not write (self-release semantics unknown).

### E5. FD6818 REG_47H afout_mode (what the modem ADC is fed) (changes speaker audio; do last of the safe ones)
- Original: `fd6818_reg_read(REG_47H)` (expect 0x6042 on analog). Test A: `(orig & 0xf0ff) | 0x0100` = 0x6142 (afout_mode 1, "FM demod direct" as DMR uses). Test B: 0x0140 (mode 1, invert 0, exactly the digital-channel value). Test C: toggle only afout_invert [13] on the stock value (0x4042) as a polarity check. Restore: write back `orig` (FD6818 register write, about 53 us each).
- Evidence: the strongest static difference between analog and digital for the signal at the modem ADC input (§2 candidate A). The modem AF stage was designed for mode 1.
- Risk: MEDIUM. This changes what the SPEAKER hears (mode 1 is the raw discriminator, very different volume / bandwidth / noise, no de-emphasis, no squelch mute), because the speaker is fed from the same AFOUT. Do it with the speaker amp muted (gate 0x35 off is stock's own `audio_pa_enable_gate(0)`; call it through the stock API and restore) or keep the test under 2 s and warn the user. Must restore REG_47H on every exit path. Do not run during TX. This does not touch the modem registers and cannot hurt TX (TX writes 0x6740 itself on PTT).

### E6. DC bias / ADC (only if E0 shows adc_bias_* at a rail or zero)
- ADC_CONTROL 0x74: bit0 `adc_semode` and bit1 `adc_extcm` toggled one at a time (RMW single bit, restore) to see PHASE_OUT / adc_bias respond. Datasheet: AF bias comes from a CPU DAC on the board (not confirmed written by stock for AF). Risk: medium for DMR (bias), none on analog audio path. Lowest value of the list; only if E0 says the ADC input is wrong.

### E7. phase_avg_en (RF_MODE[25]) off
- RMW clear bit25; restore. Averaging could attenuate fast changes; expected effect small. Risk: low. Do last.

Not worth testing: FM_FUNCTION_COEF*, FM_DECODE_FLAG*, SUBVOICE_COEF, FM_MOD (tone / signaling only), CODEC bits (DAC unmuted per stock reset sequence; the RAM tap is upstream of the DAC anyway).

---------------------------------------------------------------------------------------------

## 5. Open items after this pass
1. No datasheet statement on FM RX filter corner frequencies, de-emphasis switch, or an internal SQL mute. The FM block internals are a black box; only experiments can place the loss.
2. What FD6818 afout_mode 0 (the stock analog value) is, versus the documented 0x11 / mode 1. The FD6818 guide does not define it.
3. Whether AUDIO_CONTROL[6] gates the RAM tap or only the codec path.
4. Sample rate and sign of the RAM data are still inferred (8 kHz, signed big-endian).

---------------------------------------------------------------------------------------------

## 10-line summary

1. Datasheet gives no internal FM RX diagram. In AF mode (RF_MODE[24]) the ADC value x af_recv_ctrl/16 is treated as the PHASE signal; PHASE_OUT 0x128 is the only visible tap, and stock never reads it.
2. FM block then "demodulates internally" and feeds the codec DAC and the RX RAM tap (32 kHz internal, 8 kHz out); whether it differentiates an already-demodulated input is undocumented (HYPOTHESIS: it does, hence tiny output).
3. FM_FUNCTION_COEF1-8 are DTMF/tone-detect coefficients (697..1633 Hz), not audio demod filters; stock leaving them unset is irrelevant. FM_SQL is RO and CPU-polled; no datasheet hardware mute gate or threshold register.
4. Stock sets af_mode=1, phase_avg_en=1, af_recv_ctrl=0x26, FM_DEV_COEF=0x33500 (r=0x35), FM_BANDWIDTH=0x5f, ADC_CONTROL 0x43c3 and leaves AUDIO_CONTROL[6] fm_play_ctrl=0 on plain RX (it sets it only around tone/sidetone/factory paths).
5. Strongest static analog-vs-digital difference: FD6818 REG_47H afout_mode is 0x6042 (mode 0, invert) on analog but 0x140 (mode 1, raw demod, what the datasheet DMR recipe uses) on digital; the modem ADC is fed from that AFOUT.
6. Flat result (RMS 2 vs 3, mean -8) looks like a dead input or closed gate rather than simple -55 dB gain: candidates ranked: AF not reaching modem (A), fm_play_ctrl gate (B), FM bandpass rejecting AF (C), under-scaling (D), internal SQL (E).
7. Gain knobs available only give +30 dB (af_recv_ctrl x6.7, fm_dev_coef_r x4.9), so scaling alone cannot explain 55-60 dB.
8. Experiment order: E0 read-only (PHASE_OUT, RSSI_BIAS_VALUE 0x11c, FM_SQL, CLK_MGR 0x2c, RF_MODE, ADC_CONTROL) with/without tone; E1 AUDIO_CONTROL |= 0x40; E2 FM_BANDWIDTH &= ~0x40; E3 fm_dev_coef_r 0xFF and af_recv_ctrl 0x80/0xFF; E4 clock gate bits if off; E5 FD6818 REG_47H 0x6142/0x0140.
9. All are read-modify-write single-field changes with restore; never write 0x39c/0x3a0 (FM_RX bit14 stays masked), WORK_MODE, SYS_SOFT_RSTN, FM_PTT, TX fields, or GPIO 0x2E/0x2F/0x35; E5 is the only one audible on the speaker (medium risk), E1-E4 do not touch the speaker path.
10. SYS_SOFT_RSTN writes of 0xfffffffc look like self-releasing pulses (nothing ever sets the bits back), so do not read a held PHY reset into them; just read the register in E0.
