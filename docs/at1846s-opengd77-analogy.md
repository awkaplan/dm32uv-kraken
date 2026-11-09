# AT1846S / OpenGD77 analogy for the DM-32 (FD6818 + HR_C7000): what transfers, what does not

Research only (web + datasheets). Nothing run on hardware. [INFER] = inference. [LOCAL] = from the vendor
datasheets. Date 2026-10-16.

**Headline: the hypothesis is half right.** The architecture is analogous (RF chip on I2C taking mic or baseband
into a modulator, plus a DMR baseband chip). But (a) **OpenGD77 has no analog APRS/AFSK transmitter at all**, so
there is no firmware to copy, and (b) the FD6818 is **not** register-compatible with the AT1846S except for a
handful of coincident addresses. Datasheets: AT = AT1846S programming guide, FD = FD6818 programming guide rev 1.0.0 (Chinese; summary table L2670-3500).
Line numbers are of Markdown conversions of the vendor PDFs.

## 1. OpenGD77 APRS TX mechanism

1. **There is none.** Clone of `github.com/open-ham/OpenGD77` at commit `b843c1f050ebcfdb7aea7fad9182af13bc5ef3e6`
   (2022-05-19, default branch HEAD at fetch time): `grep -rIil -e afsk -e "bell.202" -e aprs firmware` returns
   nothing. The only "APRS" hits in the repo are in `docs/OpenGD77_User_Guide*.md` and refer to the Brandmeister
   self-care APRS text field shown for DMR callers. `gh search code aprs --repo open-ham/OpenGD77` agrees.
   There is no HRC6000 FM voice-buffer writer for analog TX either (not exhaustively verified; analog TX there is
   mic -> AT1846S, the HR-C6000 only carries DMR). A web search for AT1846S/RDA1846 AFSK/APRS turned up no project.
2. What OpenGD77 does for non-voice analog TX is **the AT1846S tone generators**, which is the same approach that
   failed on our FD6818 (retune REG_71H per bit, dual-tone gain switching):
   - `firmware/source/functions/trx.c`: `trxSetTone1()`/`trxSetTone2()` write AT 0x35 / 0x36 with `Hz*10`
     (matches AT guide L~560 "tone1_freq = Hz*10"); `trxSetDTMF()` sets both. Used for DTMF, 1750 Hz burst and CTCSS-like beeps.
   - `trxSelectVoiceChannel()` (same file): tone/DTMF -> `0x79 |= 0xC000` ("select single tone"), `0x57 bit0 = 1`
     ("audio feedback on" = sidetone), then `0x3A[14:12]` = voice_sel (`AT1846_VOICE_CHANNEL_NONE 0x00 / TONE1 0x10 /
     TONE2 0x20 / DTMF 0x30 / MIC 0x40` in the high byte, header `include/hardware/AT1846S.h`). Mic path: `0x3A`
     high-byte 0x40, i.e. voice_sel=100.
   - `trxUpdateDeviation()`: tone deviation is `0x41[6:0] = calibration DEV_TONE`; the saved voice deviation `0x59[15:6]` is restored afterwards.
3. Sample rate/format/pacing/half-switching: **not applicable** (no software-sampled path exists in OpenGD77).
   Our HR_C7000 FM Send RAM route (docs/fm-send-ram-tx.md) is original; OpenGD77 gives no precedent.
4. Closest OpenGD77 facts for a "baseband straight to the modulator" path (this is the DMR case):
   `AT1846DMRSettings` in `firmware/source/hardware/AT1846S.c`: `0x3A=0x00C2` ("Tx no mic input, DMR signal
   directly modulates the master reference oscillator"), `0x58=0xBCFD` ("Disable all filters in DMR mode"),
   `0x40=0x0031`, `0x44=0x06CC`, `0x43=0x00BB`. DM-32 analogue: FD DMR "BYPASS" recipe (FD L1759-1796, REG_47=0x6140/0x6740, REG_7E dcc_tx_bypass).

## 2. Register semantics (AT1846S per AT guide + OpenGD77 vs FD6818 guide)

"Stock" = DM-32 stock firmware per docs/analog-vs-digital-rx-config.md (analog / digital). "n/a" = FD guide has no such row.

| wire reg | AT1846S meaning [LOCAL AT guide] | OpenGD77 value (AT1846S.c) | FD6818 guide at the same address | Stock DM-32 |
|---|---|---|---|---|
| 0x30 | Control: [14] xtal_mode, [13] filter_band_sel, [12] band_mode, [6] tx_on, [5] rx_on, [3] sq_on, [0] soft_reset | 0x0001 reset, 0x40A6 cal, 0x0004 power-on; `0x30` masks 0xCF/0x9F set 25 kHz bits, 0x20 = RX on | **no REG_30H**. Same function is REG_03H (txon[1], rxon[0], afout_en[9], ...) with a different bit layout | writes 0xBFF1 (RX), 0xC1FE (TX), identical to FD REG_03H values |
| 0x58 | Filters: [13] rssi_lpf bypass, [12] vox LPF byp, [11] vox HPF byp, [7] **pre/de-emphasis bypass**, [6] voice HPF byp, [5] voice LPF byp, [4] ctcss LPF byp, [3] ctcss HPF byp, [0] ctcss_lpfil_bw (others 15:14=10, 10:8=100, 2:1=10) | FM 0xBCCD / 0xBCED, FM25k 0xBC05 ("enable de-emph/pre-emph/HPF/LPF"), DMR 0xBCFD (all voice filters bypassed: bits 7,6,5,4,3 set) | **no REG_58H** (table jumps 0x56 -> 0x59). Equivalent *function* is REG_9BH (page 1): [3] HPF-tx byp, [2] LPF1-tx byp, [1] LPF2-tx byp, [0] pre-emph byp, [9:6] the RX counterparts | writes 0x3C03 in the FSK PTT-ID routine (meaning unknown on FD) |
| 0x59 | `xmitter_dev[15:6]` voice+sub-audio deviation, `c_dev[5:0]` CTCSS dev | 0x09D2 (value), restored by `trxSelectVoiceChannel` | REG_59H = **FSK control** (fifo clears, fsk_tx_en, prmb/sync sizes) | not a deviation reg on FD6818 |
| 0x2B | Not in the AT guide register list (undocumented in the local copy) | not written | not in FD table | analog 0xC000, digital 0x0700 |
| 0x3A | voice_sel [14:12] (0 none, 1 tone1, 2 tone2, 3 both, 4 mic), sq_dten [10:6], ctcss_dten [4:0]; reset 0x40C3 | 0x44CB/0x40CB 12.5k/25k; 0x40C3; 0x00C2 DMR | not in FD table but FD recipe writes it: wide 0x00E0, narrow 0x0040 (FD L1009-1018; revision note: "REG_3AH setting removed" for wide/narrow, L69) | digital recipe 0x0040 |
| 0x40 | undocumented in AT guide | 0x0030 FM, **0x0031 = "magic" bit0 enabling low-frequency audio** (comment), 0x0031 in DMR | REG_40H = **TX deviation**: dev_en[12], dev_sh[11:8], dev_lvl[7:0], gain=(256+lvl)>>sh | byte \| 0x3400 |
| 0x41 | voice_gain_tx[6:0] digital TX gain (reset 0x4006) | 0x470F/0x4731/0x4431 | not in FD table | n/a |
| 0x43 | undocumented in AT guide; OpenGD77 comment "FM deviation" | 0x00BB / 0x00A9 | REG_43H = filter select: firlpf_bw[14:12], **firlpf_bw_for_weak[11:9]**, audio_lpf2_tx_sel[8:6], firlpf_gain[1:0] | 0x4048 narrow / 0x3028 wide |
| 0x44 | [11:8] gain_tx, [7:4] dac_vgain (analog DAC gain), [3:0] volume; reset 0x00FF | 0x06FF, 0x05FF, 0x06CC, 0x05CF (Colin G4EML: "set internal volume to 100%") | REG_44H = fmdem_gain[12:11] (0/6/12/18 dB), reset 0x43EC | only in dead code |
| 0x45 | not in AT guide table | not written | not in FD table | only in dead code |
| 0x47 | undocumented in AT guide ("UV82 and GD77 use the same values") | 0x7F2F | REG_47H = afout_invert[13], **afout_mode[12:8]**, dig_gain_tx[3:0] | 0x6042 / 0x0140 digital; 0x6740 TX bypass |
| 0x48 | noise1_th (SQ) | 0x1A32, 0x1DB6, 0x1E38 | not in FD table (stock: "CIC mode" 0x31E0\|sel / 0xBBBF) | see analysis doc |
| 0x50/0x51 | not in AT guide | not written | REG_50H TX mute[15], limit bypass[10], limit[9:0]; REG_51H sub-audio | 0x3B18 etc. |
| 0x7E | AT guide: dtmf_idle[5] (TX), dtmf_sample[4] (RX), dtmf_code[3:0] | not written | REG_7EH = dcc_tx_bypass[7], dcc_rx_bypass[6], dcc_tx_bw[5:3], reset 0x341E | 0x302E / 0xB03F |
| 0x35/0x36 | tone1/2 freq = Hz*10 | trxSetTone1/2 | REG_71H/72H = tone1/2 freq = Hz*2^26/6.5 MHz (our measurement: 1200 Hz = 0x3065) | n/a |
| 0x57 | bit0 = audio feedback (sidetone) [OpenGD77 comment] | trxSelectVoiceChannel | not in FD table | n/a |

AT register-numbers that match FD in *meaning and format*: none beyond the following ambiguous cases (see §3).

## 3. Register-compatibility verdict

**Not register-compatible. Block-level family resemblance only.** Evidence:

1. **Frequency**: AT 0x29/0x2A (kHz/16) vs FD 0x38/0x39 (10 Hz units); stock uses FD's 0x38/0x39. Same conclusion as earlier AT1846S
   datasheet notes ("never for addresses").
2. **Tone generator**: AT 0x35/0x36 (Hz*10) vs FD 0x71/0x72 (6.5 MHz-referenced); stock DTMF uses the FD addresses.
3. **Many FD addresses mean something else on the AT**: 0x40 (FD TX deviation; OpenGD77 treats 0x40 as the LF-audio "magic bit"),
   0x43, 0x44 (FD fmdem_gain vs AT gain/volume), 0x47, 0x59 (FD FSK control vs AT deviation), 0x7E (FD DC-filter vs AT DTMF flags).
4. **The REG_03H = wire 0x30 question is settled as "same function, FD layout"**: FD REG_03H bits [15] vco_cal, [14] pabias, [13:10] rxlink, [9] afout,
   [8:4] pll, [3] padrv, [2] micin, [1] txon, [0] rxon. Stock 0xC1FE = TX (pabias, pll, padrv, micin, txon) and 0xBFF1 = RX (vco_cal,
   rxlink, afout, pll, rxon) decode perfectly in the FD layout; in the AT layout (tx_on=[6], rx_on=[5]) they do not. The FD guide itself writes
   `WRITE(0x03,0xC1FE)` (L930, L1773). So **wire 0x30 is an alias of FD REG_03H on the FD6818B** [INFER: the "B" part, or its bus
   wrapper, exposes the AT control address; the nibble-swapped number is probably deliberate AT-compat aliasing, but 0x2B/0x58 may be
   similar aliases, which is why they matter]. Unresolved: the FD guide has no table entry for 0x2B, 0x30, 0x32, 0x37, 0x58, 0x13, 0x45.
5. **AT 0x58 vs FD**: AT 0x58 is the filter-bypass register (pre-emphasis [7], voice HPF [6], voice LPF [5]) with a clean spec. FD has **no REG_58H** but
   puts the same four switches in REG_9BH (page 1, via REG_7F) and an output selector in REG_9DH[3:2] (01 pre-emphasis output, 11 LPF2 output).
   Stock writes 0x58 = 0x3C03 only in the FSK routine. [INFER] 0x3C03 would be a deliberate AT-style value; if wire 0x58 is a live AT-compat alias
   of REG_9B's cluster, bits 13..10 = 1 (AT: rssi/vox filters bypassed) and low byte 0x03 = all voice filters normal. If it is not an alias, the
   write is inert. **Test: read wire 0x58 and REG_9BH before and after writing 0x58 = 0xBCFD (OpenGD77's all-voice-filters-bypassed value) and see if 9BH changes.**
6. **AT 0x2B and 0x44/0x45**: absent from the AT guide (local copy) and the FD table. AT 0x44 is a real documented register (gain_tx/dac_vgain/volume)
   while FD 0x44 is fmdem_gain; both can be true only if the register is documented per-chip. Stock writes 0x2B (0xC000 / 0x0700) with no known meaning, 0x44/0x45 only in dead code. 0x2B and 0x45 remain unknown.

Confidence in "not compatible": **high** (items 1-3). Confidence in "0x30 alias of REG_03H": **high** for function, **medium** for mechanism.

## 4. Concrete suggestions

### 4a. Receiver 1-bit smearing (24% mid-bit errors on 1-bit runs, ~5% at 2 bits)

Smearing of one 833 us bit means the receive audio bandwidth is too narrow or ringing, not a clock issue. In order:

1. **REG_43H[11:9] `firlpf_bw_for_weak`** (confidence: medium-high that this is the biggest lever). Stock narrow 0x4048 sets [14:12]=100 (3.75 kHz) but
   [11:9]=000 = **1.7 kHz** (the FD table: 000 = 1.7 kHz), matching the "FIR narrows to 1.7 kHz on weak signals" suspicion. 2200 Hz tones plus
   1200 Hz-wide skirts need > 3 kHz. Test: write 0x4048 | (0b100 << 9) = 0x4848 (narrow) or 0x3028 | (0b011<<9) = 0x3828 (wide, bw x2); read back first. Also try
   `firlpf_bw` 111 (4.5 kHz). Reversible (restore at next channel change; stock rewrites REG_43H).
2. **REG_9BH page 1 (via REG_7F=1)**: set `audio_lpf1_rx_bypass`[8], `audio_lpf2_rx_bypass`[7], `audio_emph_rx_bypass`[6] and leave HPF alone. De-emphasis (bit 6) is the
   6 dB/oct tilt that makes 2200 Hz weaker than 1200 Hz; it also adds group delay. This changes both amplitude balance and ringing
   [INFER]. Stock never touches page 1 (docs/analog-vs-digital-rx-config.md). Confidence medium.
3. **REG_9DH[3:2]** (audio_tx_path_sel: 01 pre-emphasis output / 11 LPF2 output) is TX-side; skip for RX.
4. **Compander** `REG_96H[2:0]` (cmpd_rx_bypass, reset 000 = ON) and REG_98H expander: amplitude-dependent gain makes 1-bit bursts look non-flat. Set
   110/001 (bypass). Confidence low-medium (affects level not timing).
5. **REG_7EH dcc_rx_bypass[6]** and the DC filter: low value; skip unless step response shows droop.
6. Digital side: since the decoder already uses a pre-emphasis equaliser (16% vs 24%), adding the AT-style "no-filters" trial of REG_9BH bypass is the
   hardware-equivalent of OpenGD77's DMR `0x58 = 0xBCFD` ("disable all filters"), which is exactly how OpenGD77 gets clean raw baseband at the discriminator.

### 4b. Software AFSK TX through the HR_C7000 FM Send RAM

1. OpenGD77 offers no precedent (no software audio into an analog TX). Use its DMR settings as the nearest analogue of "bypass the audio filters, drive the modulator
   with pre-shaped baseband": mirror it on the FD side with **REG_47H=0x6740 (TX bypass), REG_7EH |= 0x80, REG_9BH tx bypass bits, REG_50H limit bypass[10]**, as
   already planned in docs/fm-send-ram-tx.md. Confidence: medium.
2. AT-style gating idea to copy: OpenGD77 DMR sets `0x3A=0x00C2` (voice_sel=000, mic off) because baseband comes in elsewhere. On the FD6818 the equivalent mic-vs-baseband
   selection is GPIO 0x2E plus REG_47H afout_mode (docs/fm-send-ram-tx.md §3A). Low confidence that more is hidden in wire 0x3A.
3. For levels, OpenGD77 sets tone deviation via a *separate* calibrated gain (0x41 DEV_TONE) and restores it. Do the same: keep `REG_40H` untouched and set deviation with
   `fm_dev_coef_t` in the HR_C7000 or REG_97H mic gain, saved/restored. Confidence medium.
4. Keep tone generators off during RAM TX: the FD guide requires tone1_gen=1 for MIC bypass/dual-tone mixing but only for the tone case (capture7); for RAM TX set REG_70H=0 and REG_47H bypass.
5. Realistic expectation: the source of the TX audio is the question, not the AT registers; the AT guide cannot answer whether the HR_C7000 MOD DAC reaches FD6818 MICIN (probe P1 of
   fm-send-ram-tx.md). Datasheet Fig 63 of the HR_C7000 (cited in that doc) shows the MOD1 -> RDA1846S audio-input arrangement, which is the actual precedent for this wiring. [INFER, medium].

## 5. Other chips (BK4819, UV-K5)

- BK4819 has the same limitation as our FD6818 FSK engine: community testing says "BK4819 produces FSK, not AFSK. It will not be useful for general ham radio packet radio (APRS/BBS)" (UV_K5_playground issue #20);
  a datasheet-listed FFSK 1200/1800 and 1200/2400 mode exists, and `uv-k5-firmware-custom` `driver/bk4819.c` comments REG_58 FSK TX modes: 0 = FSK 1.2k/2.4k direct FM no tones, 1 = FFSK 1200/1800, 3 = FFSK 1200/2400
  (MDC1200 TX is built on it, loading a packet through REG_5F). Proposed workaround in that thread (unconfirmed): NRZI + HDLC framing in software, 0x7E sync words, 1200 baud, then FFSK/F2D mode.
- No evidence found of a working BK4819 or AT1846S/FD6818 APRS beacon. The FD6818 guide has no FFSK/Bell-202 mode (grep for ffsk/afsk/1200 finds only the tone-generator formula).
  [INFER] The closest chip-native option on the FD6818 is none; software PCM through the HR_C7000 (our v31) or an external audio route are the only routes.

## 6. Sources

- OpenGD77 (open-ham fork of rogerclarkmelbourne/OpenGD77), commit b843c1f050ebcfdb7aea7fad9182af13bc5ef3e6, https://github.com/open-ham/OpenGD77:
  `firmware/source/hardware/AT1846S.c` (`AT1846InitSettings`, `AT1846FMSettings`, `AT1846DMRSettings`, `AT1846SetBandwidth`, `AT1846Init`),
  `firmware/include/hardware/AT1846S.h`, `firmware/source/functions/trx.c` (`trxSelectVoiceChannel`, `trxSetTone1/2`, `trxUpdateDeviation`, `trxSetMicGainFM`).
- AT1846S programming guide: AT guide L324-380 (0x30), L386 (0x3A voice_sel), L661-677 (0x44), L765-790 (0x58), L525-560 (tone regs 0x35/0x36).
- FD6818 programming guide: L930, L1009-1018, L1759-1796, L2726 (REG_03H), REG_40H/43H/44H/47H/50H/7EH/96H-9DH rows.
- BK4819: https://github.com/piotr022/UV_K5_playground/issues/20 ; https://code.popov.link/mirrors/uv-k5-firmware-custom/src/branch/main/driver/bk4819.c (REG_58 comment; mirror, may lag upstream).
- Not fetched (time): OpenRTX AT1846S.cpp, md380tools wiki, dmrconf. Register semantics above come from the local AT guide plus OpenGD77 comments only.
