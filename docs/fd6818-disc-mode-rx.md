# FD6818 raw FM discriminator on AFOUT: datasheet options, recipe, risks

Status: STATIC research from the datasheets and this repo only. Nothing was run or flashed. [INFER] marks inference.

Sources (line numbers are of these files; datasheet line numbers are of Markdown conversions of the vendor PDFs):
- FDG = FD6818 programming guide rev 1.0.0 (Chinese)
- DOC2 = `docs/analog-vs-digital-rx-config.md` (this repo)
- DOC3 = `docs/rx-audio-fm-mode.md` (this repo)
- HRC = HR_C7000 datasheet v2.7 (English)
- PATCH = `patches/kraken_ui/krk_aprsrx.c` and `krk_rf.h`

---

## 1. Bottom line

- The datasheet documents one RX AFOUT source: afout_mode 0x11 "RX AFOUT" (FDG 378-379, 1219-1220, 1995-1996, 2977-2978). It is the last RX item before the DAC, after HPF300, LPF1, de-emphasis, expander, LPF2 and volume (FDG 498-733).
- No register table lists a raw discriminator tap. The only candidate is the DMR bypass recipe: `WRITE(0x47,0x6140) //FM 解调后直接送 AFOUT` ("FM demod output straight to AFOUT", FDG 1881). In bits [12:8] that is afout_mode 0x01, a value the mode table never lists. [INFER] 0x01 is the raw-DISC tap.
- Plan: afout_mode 0x01, plus the RX bypass bits in REG_9BH and the compander bypass in REG_96H. Whether this is a true raw discriminator is unproven.
- Two issues could defeat the goal even if the FD6818 tap is raw: (a) the modem's own FM block (FM_BANDWIDTH 0x5F = bandpass + pre-emphasis, DOC3 90-91) may sit between AFOUT and the RX RAM we read; (b) REG_9BH and REG_96H are above 0x7F and need page select (FDG 787-791), which the existing patch does not do.

## 2. AFOUT sources and paths the datasheet offers

A. Normal RX voice path, afout_mode = 0x11 (FDG 372-383, 1201-1212, 1219-1223, 1995-1998).
   - Items in order: DISC gain REG_96H[13:8] (582-588); FM demod gain REG_44H[12:11], reset 0 dB (565-571); CTCSS/DCS (600-624); HPF300 REG_9BH[9] (626-633); LPF1 REG_9BH[8] (642-648); de-emphasis REG_9BH[6] (651-657); expander REG_96H[2:0] (660-665, 3324-3328); LPF2 REG_9BH[7] (667-684); volume REG_53H (705-713); AFOUT mux (717-719 refers to 372-383); DAC (731-733).
   - Reset REG_9BH = 0x0004 (3386), so HPF300, LPF1, LPF2 and de-emphasis are all ON after reset.
   - Other afout_mode values: 0x10 MUTE, 0x18 BEEP/TX side tone, 0x15 RX alarm tone (378-382, 1219-1223, 2977-2982).
   - AFOUT DAC needs REG_03H[9] afout_en = 1 (390-392, 2730); level REG_23H[3:0] dac_vgain, reset max (390-392, 2810-2811).

B. DISC baseband mode (FDG 1740-1835; block item 23 at 747-751).
   - FDG 1745-1747: the DISC and MIC baseband signals both enter through MICIN; received DISC must be under 200 mV. FDG 749-751: the demodulated DISC is sent INTO the chip via MICIN for voice, sub-audio, DTMF and 5-tone decoding.
   - RX in this mode: `WRITE(0x03,0x0005)` (FDG 1765). No afout_en bit is set, so this path does not drive AFOUT.
   - The VCO variant (FDG 1816-1818) writes 0x03 = 0x81FD with the comment "RFO outputs VCO, AF outputs audio, internal sub-audio decode". This is a VCO mode, not a raw tap.
   - Conclusion: DISC mode is an input path, not an AFOUT tap.

C. Bypass / 4FSK mode for DMR/dPMR (FDG 1839-1886).
   - FDG 1844-1846: "AFOUT outputs the demodulated 4FSK signal"; chip must be in narrowband 12.5 kHz.
   - RX: `WRITE(0x47,0x6140)` (1881); afout_mode 0x01, invert 1, dig_gain_tx 0.
   - TX: `WRITE(0x47,0x6740)` "MIC ADC directly transmitted" (1873); afout_mode 0x07.
   - Undocumented mode values in this section: 0x01 (RX) and 0x07 (TX). Mode 0x00 is used by stock analog and is not in the datasheet at all (DOC2 section 5).

## 3. Register facts

| Reg | Bits | Meaning (cite) | Reset | Stock analog | Stock digital |
|---|---|---|---|---|---|
| REG_47H | 13 / 12:8 / 3:0 | afout_invert; afout_mode; dig_gain_tx (TX only) (2974-2982) | 0x6140 | 0x6042 (invert 1, mode 0x00) (DOC2 85, 268) | 0x0140 (invert 0, mode 0x01) (DOC2 85, 270) |
| REG_9BH | 9 / 8 / 7 / 6 | RX: HPF300, LPF1, LPF2, de-emph bypass (3386-3391); TX bits 3:0 (3393-3397) | 0x0004 | not written (DOC2 section 5) | not written |
| REG_96H | 13:8 / 2:0 | audio_rx_gain1 (DISC gain, 0.5 dB/step) (3321-3322); compander bypass: 000 = CMPD on for RX, 001 = bypass without gain, 110 = bypass with gain (3324-3328) | 0x1AE8 (compander on) | not written | not written |
| REG_44H | 12:11 | fmdem_gain 0-18 dB (2963-2967). Bits 10:0 "reserved" in the table | 0x43EC | not written | not written |
| REG_43H | 14:12 / 11:9 / 5 / 8:6 | RX firlpf_bw; firlpf_bw_for_weak; wb (1 = 25/20 kHz); TX audio_lpf2_tx_sel (524-540, 308, 2924-2962) | 0x6009 | 0x3028 (wb 1) or 0x4048 (wb 0) (DOC2 91) | 0x4048 |
| REG_7EH | 7 / 6 / 5:3 | dcc_tx_bypass; dcc_rx_bypass; dcc_tx_bw (208-212, 3257-3262) | 0x341E | 0x302E | 0xB03F |
| REG_23H | 3:0 | dac_vgain, "use max" (390-394, 2810-2811) | 0x8F8F | not written | not written |
| REG_03H | 9 | afout_en (2726-2735) | 0x0000 | 0xBFF1 (wire 0x30) | 0xBFF1 |
| REG_9DH | 3:2 | audio_tx_path_sel: TX only, 01 pre-emph out, 11 LPF2 out (332, 3398-3413) | 0x29AD | not RX | not RX |
| REG_48H | - | No row in the register summary (FDG 2675-3420). Appears only as "cic mode" 0x6C5C in DMR (1854) | - | 0x31E0 \| sel (DOC2 86) | 0xBBBF (DOC2 86) |

- Paging: FDG 787-791 says 3-wire addresses above 0x7F need `WRITE(0x7F,0x0001)` (page 1) and `0x7F = 0` to return. REG_96H, REG_9BH and REG_9DH are above 0x7F.
- Stock never writes 0x96 or 0x9B (DOC2 section 5). So no reference value in stock for these.

## 4. Recipe: stock analog RX to raw discriminator on AFOUT (RX only)

Preconditions: analog channel (channel-type nibble 0, DOC2 line 23), receiving, no PTT. Never key during the test.

Save first (read via `fd6818_reg_read`, PATCH krk_rf.h line 11): 0x47, 0x9B, 0x96, 0x43, 0x7E, 0x7F. Expect 0x6042, 0x0004, 0x1AE8, 0x3028 or 0x4048, 0x302E, 0x0000.

1. `WRITE(0x7F,0x0001)` (page 1). FDG 789-791. [INFER] needed for 0x9B and 0x96; see open question 4.
2. REG_9BH = saved | 0x03C0. Sets RX bypass bits 9, 8, 7, 6 (HPF300, LPF1, LPF2, de-emphasis). Stock 0x0004 becomes 0x03C4. Bits 3:0 (TX) are kept.
3. REG_96H = (saved & ~0x0007) | 0x0001. Compander bypass without gain. 0x1AE8 becomes 0x1AE9. FDG 3326.
4. `WRITE(0x7F,0x0000)` (page 0). FDG 789-791.
5. REG_47H = (saved & 0xE0FF) | 0x0100. Sets afout_mode to 0x01 and keeps invert and dig_gain_tx. 0x6042 becomes 0x6142. This exact value is one stock already writes on this hardware (DOC2 269, via IRAM 0x2B51C). Prefer it over the DMR literal 0x6140, which sets dig_gain_tx to 0 and invert to 1.
6. Optional, RX only: REG_43H = (saved & ~0x7E00) | 0x7E00. Sets RX RF filter to 4.5 kHz (bits 14:9). Same mask as PATCH apply_filt (PATCH 76-78).
7. Observe: FM RX RAM probe (DOC3 section 4) and PHASE_OUT 0x128 / 0x11C per DOC2 section 7 E0. Compare 1200 Hz and 2200 Hz levels and settling against the stock capture.

Restore (reverse order; each step readable):
R1. `WRITE(0x7F,0x0001)`; REG_96H = saved; REG_9BH = saved; `WRITE(0x7F,0x0000)`.
R2. REG_47H = saved (expect 0x6042). REG_43H = saved if changed.
R3. Re-assert the speaker/modem switch: `gpio_set_output_bit(0x2F)` (PATCH line 40, 483). Not documented in FDG.
R4. Read back 0x47, 0x9B, 0x96, 0x43, 0x7F and compare with the saved values.

Do not start with DOC2 E1b (0x47 = 0x0140, 0x48 = 0xBBBF, 0x7E = 0xB03F, 0x2B = 0x0700). That switches the whole digital set, changes dig_gain_tx from 2 to 0 and invert from 1 to 0, and writes undocumented 0x48 and 0x2B.

Stock digital values, for reference (DOC2 85-93): 0x47 = 0x0140, 0x48 = 0xBBBF, 0x7E = 0xB03F, 0x2B = 0x0700, 0x43 = 0x4048, 0x37 = 0x1F1F, 0x3F = 0x0000, 0x30 = 0xBFF1. Only 0x47 has a documented meaning here (mode 0x01). Use the digital set only as a last resort.

## 5. Risks

- TX, REG_7EH bit 7 (dcc_tx_bypass): the DMR and DISC recipes set it (FDG 1751, 1802, 1860). It removes the MIC DC filter on TX (208-212). Do not set it.
- TX, REG_47H bits 3:0 and 13: shared with TX. Only write with read-modify-write. Stock TX itself writes 0x6740 (DOC2 section 5 note a, FDG 1873).
- TX, REG_43H bits 8:6 (audio_lpf2_tx_sel, FDG 308): whole-register writes change TX bandwidth. The DMR line `WRITE(0x43,0x7E08)` (FDG 1852) does this. Use masks only.
- TX, REG_9BH bits 3:0: TX bypasses. Keep.
- Do not copy these DMR or DISC writes:
  - 0x40 (TX modulation, FDG 1864, 1906, 1928).
  - 0x3A (FDG 1850). The revision list says the REG_3AH setting was removed (FDG 69).
  - 0x04 = 0x0300 (FDG 1848) starts OFFSET calibration (FDG 875-886). 0x04 = 0x0800 / 0x0400 (FDG 1816, 1826) set bits not in the table.
  - 0x03 with TX or RX enable words (0xC1FE, 0x81FD, 0xBFF1). These switch PA drive and RFO. Keep REG_03H unchanged.
  - 0x34, 0x35, 0x13, 0x3C, 0x3D (FDG 1782-1788, 1904, 1922). These touch VCO, clock and XTAL. REG_3CH holds xtal_freq [23:16] (FDG 2874); a wrong value moves every RX and TX frequency.
  - REG_03H[15] 0 to 1 starts PLL calibration (FDG 2504).
- Persistence: the datasheet gives no flash or OTP write for any register above. [INFER] The recipe only touches volatile FD6818 registers. Keep it that way: never call a flash or calibration-table writer from the patch.
- Page register: if 0x7F is left at 1, every later register access goes to page 1. Always return it to 0 (R1).
- Re-application: stock keying writes 0x47 = 0x6740 and GPIO 0x2E/0x2F (DOC2 section 5 note a). Channel changes rewrite the analog set (DOC2 section 1). PATCH apply_mode() re-asserts 0x2F on every half but not 0x47, so the mode must be re-applied after any channel change or PTT.
- Current patch: apply_filt() (PATCH 67-79) sets REG_9BH bits 6, 7, 8 only. It does not bypass HPF300 (bit 9) or the compander (REG_96H). It is not a raw tap. The existing restore at PATCH 483-484 is correct for the bits it touches.

## 6. Open questions

1. Modem filters after AFOUT. FM_BANDWIDTH 0x5F sets bandpass and pre-emphasis in the modem (DOC3 90-91; bandpass bit 6 and pre-emphasis bit 4 at DOC3 112). The PCM we read from the RX RAM comes after that block. If so, raw AFOUT alone will not remove de-emphasis or the roll-off. Test: compare 1200 and 2200 Hz levels with the modem FM_BANDWIDTH bits changed. Check whether FM_BANDWIDTH is shared with TX first.
2. Does afout_mode 0x01 bypass the whole RX chain, or only switch the mux? The datasheet has no table for it, and the DMR recipe sets no REG_9BH bits. Test: step response with REG_9BH bypasses and 0x47 = 0x0142 compared to 0x0142 alone.
3. Where is the AFOUT mux relative to volume REG_53H (19) and the expander (15)? The block diagram (FDG 478-482) does not say. Does volume gate mode 0x01?
4. Paging in the stock helper. Does `fd6818_reg_write` (0x2C2F4, PATCH krk_rf.h line 12) set page 1 for addresses above 0x7F? If not, writes to 0x9B and 0x96 may alias to page 0 addresses 0x1B and 0x16. Check: read 0x1B and 0x16 before and after one write to 0x9B. The apply_filt() write at PATCH line 75 may already have this problem.
5. AF-mode input type. HRC 18387-18388 says the signal entering HR_C7000 is "a frequency modulated signal" that gain and offset are adjusted on (Chinese text in the same section says the same). If the modem expects FM rather than discriminator audio, a raw DISC on AFOUT is the wrong input. The 1 kHz capture in PATCH (RMS 757, zero-crossing 1000 Hz) is consistent with audio-band input but was taken on the stock path, so it does not settle this.
6. Undocumented bits: REG_48H (no row); REG_44H bit 0 (FDG 1749 "DISC mode enable", but the table lists 10:0 as reserved, FDG 2967); REG_04H bit 11 (FDG 1816); REG_3DH (FDG 1788). The DISC value 0x53EC also sets fmdem_gain to 12 dB (bits 12:11), so do not copy it.
7. Meaning of afout_mode 0x00 (stock analog). Not in the datasheet (DOC2 section 5).
8. Datasheet inconsistencies: the DMR recipe's `WRITE(0x43,0x7E09) -> WRITE(0x43,0x7E08)` (FDG 1852) is ambiguous. The DMR narrowband write to 0x3A conflicts with FDG 69.
9. DC: with dcc_rx_bypass = 0 (recipe keeps it), the RX DC filter may still sit in the path. The raw discriminator carries the carrier offset as DC. The RX DC filter corner (REG_7EH bits 5:3 describe TX only, FDG 3260-3262) is not given for RX. Whether AFSK needs software DC removal is untested.
10. Which analog wb applies on the DM-32 channels: stock analog uses 0x3028 (wb 1, 25 kHz) or 0x4048 (wb 0, 12.5 kHz), depending on flags bit 7 (DOC2 91). Confirm the channel bandwidth before testing the raw path.
