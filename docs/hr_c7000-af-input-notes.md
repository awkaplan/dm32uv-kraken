# HR_C7000 datasheet notes: AF input ADC, codec ADC, and ADC-related registers

Read-only research from the HR_C7000 datasheet. No OpenRTX port code or generated code was used as a source.

## Source key and conventions

- **EN** = HR_C7000 datasheet v2.7, English, converted to Markdown as `HR_C7000_v2.7_en.md` (line numbers are this file's lines unless marked otherwise).
- **FLOW** = `HR_C7000_v2.7_en.flow.md`, a second (reflowed) conversion of the same PDF. Its line numbers differ from EN; cited only where EN does not contain the text (Chapter 9 RSSI/PHASE_OUT tables).
- **ZH** = `HR_C7000_v2.7_zh-CN.md` (Chinese original; translations below are mine and marked where the English is garbled).
- **PDF p.485** = `HR_C7000_v2.7_en.pdf`, page 485 (printed page 485 of 734), rendered and viewed for Figure 64.
- Figures are images in the .md; the only figure text available is what is in the PDF render.
- Where the datasheet contradicts itself, both versions are quoted and the conflict is stated. Nothing here is inferred unless labeled "(inference)" or "(my arithmetic)".

---

## A. Modem RX ADC (ADC_CONTROL 0x74, AF interface)

### A1. Recommended AF interface circuit, bias, common mode, swing, resolution

**Coupling (series capacitor vs. direct): not specified.**

- EN 18364-18410 (section 8.2.3.1 "AF take over"). Text at EN 18366: "By setting RF_MODE[24]=1'b1, HR_C7000 can Working in AF mode."
- Figure 64 "AF Receive reference interface circuit" (EN 18386; PDF p.485) is a block diagram only: HR_C7000 <- `AF_out` <- RDA1846S <- LNA <- low-pass/antenna, plus 26 MHz VC-TCXO and I2C. The rendered page shows **no series capacitor and no bias network**. The datasheet text does not mention AC coupling, a coupling capacitor, or a divider for AF.
- Related but not AF: EN 1834-1864 and 2645-2690 list `Cin = 2 pF` for "VINP/VINM input capacitor" (EN 2994). This is internal sampling capacitance, not an external coupling part.

**DC bias source: CPU DAC on the bus.**

- EN 18398 (section 8.2.3.1): "AF Signal bias then CPU DAC on the bus Provide an offset point and configure AF Receive differential ADC N The AF signal terminal is connected to the P terminal of the ADC."
- ZH 14832 (translation): "AF 信号偏置则 CPU 总线上的 DAC 提供一个偏置一点，配置 AF 接收差分 ADC 的 N 端进行调节，AF 信号端连接到 ADC 的 P 端。" = "The AF signal bias is provided by a DAC on the CPU bus as an offset point; the N side of the AF receive differential ADC is configured for adjustment; the AF signal terminal is connected to the P side of the ADC."
- **Which CPU DAC channel is not stated.** The CPU DAC has three channels, pins `DAC_VOUT_MCUA` (K1), `DAC_VOUT_MCUB` (K2), `DAC_VOUT_MCUC` (L1) (EN 1805-1819 ball map; EN 2719-2728 pin table, "Function 0: CPU DAC A/B/C-channel output").
- The CPU DAC pins default to GPIO. EN 4418-4436 (IO_DIPLEX2 0x3c): `dac_vout_mcuc_sel [29]`, `dac_vout_mcub_sel [28]`, `dac_vout_mcua_sel [27]`, reset value 0x1, "multiplexing selection 1: GPIO_PTC25 (C/PTC25), GPIO_PTC24 (B), GPIO_PTC23 (A); 0: DAC_VOUT_MCUx". So the bias DAC pin must be switched to function 0 by clearing these bits.
- CPU DAC electrical: 12-bit (EN 2980: "DAC RES 12 bit"); output swing "VREFN to VREFP" (EN ~2978, Table 22 DAC row); resistive load min 1.5 kOhm, capacitive load max 30 pF (same table). DAC_VREFP is 2.97-3.63 V, typ 3.3 V (EN 2912, power table, "DAC_VREFP DAC reference voltage input").
- CPU DAC register block: base 0x140D_0000 (EN 16250 "DAC Register Overview (Base Address: 0x140D_0000)"). Registers: `DAC_DATA_A` (offset 0x08, bits [11:0]) at EN 16305, `DAC_PD_CTRL` 0x00 (1 = power off, EN 16262). Note the datasheet also gives the ADC controller base as 0x140D_0000 (EN 15769), so one of those two base addresses is likely a typo.
- **Resistor divider: not mentioned anywhere.** Searched for "bias", "偏置", "divider": only the sentences above.

**Expected input common-mode voltage.**

- EN 2985-2991 (Table 22, "PHY ADC"): `VINdiff` = 0.9 Vpp (differential, VINP-VINM); `VINse` = 0.45 Vpp "Single-ended or pseudo-differential input voltage, VINM=common mode of VINP"; `VINcm-dc` = 0.5 to 1.5 V "VINCM common mode voltage (DC mode)"; `VINcm-ac` = 0 V "VINCM common mode voltage (AC mode)".
- EN 2987-2991 does not define "DC mode" or "AC mode". The ZH table (ZH ~2721-2725) has the same text.
- For AF specifically, the datasheet gives **no numeric common-mode voltage**. It says the VINM and VCM pins are at a "fixed level (AF bias)" (EN 6143-6152 adc_semode, see A2).

**Full-scale input swing.**

- Differential full scale: 0.9 Vpp (EN 2985). Single-ended: 0.45 Vpp (EN 2987). Both are listed as input voltage; "full scale" is not used for the modem ADC.
- Input range for 8-channel single-ended input (this is the MCU general-purpose ADC, not the baseband ADC): 0.01 to 0.99 V with VREF (EN 3012).
- `ADC_VREF` (pin M3): "The analog reference output voltage of the baseband ADC ... default value is 500mV" (EN 2820-2826). Electrical: 0.485 / 0.5 / 0.515 V (EN 2914).

**Resolution and sample rate.**

- Resolution: **10 bit** (EN 2993 "RES 10 bit"). ENOB 9 bit (EN 3011). SNR 56 dB (EN 3010). DNL +-1 LSB, INL +-2 LSB (EN 2996-2997 region).
- Gain error: -4% to +4% before BGTRIM; -1% to +1% after BGTRIM, or with external reference (EN 2998-3000). Offset: +-5 mV (EN 3003).
- Latency: 12 cycles (EN 3004). Wake-up: 120 cycles (EN 3006). Buffer start-up: 15-20 us (EN ~3007).
- Clock: `adc_clk` "Baseband ADC Work clock" = 117.9648 MHz typ (EN 3327, Table 25).
- **Sample rate is not stated** for the baseband ADC. (My arithmetic: 117.9648 MHz / 38.4 kHz = 3072 exactly, but the datasheet does not say the ADC runs at 38.4 kHz; 38.4 kHz is the external RF-IC AD_FS in section 8.3.3, EN 18484.)

**Pin names: conflicting.** See A4.

### A2. ADC_CONTROL (0x74) bit meanings and reset values

Table at EN 6080-6150 (section 4.6.5.2). FLOW duplicate at 15961-16140. Reset value of the whole register computed from the "Default" column (my arithmetic): bits 14 and 0 are 1, so **0x0000_4001**.

| Bits | Name | Access | Reset | Meaning (quoted EN) |
|---|---|---|---|---|
| [31:30] | adc_testout | RO | 0x0 | "Test output status" (EN 6086) |
| [27:24] | adc_testin | RW | 0x0 | "Test input control" (EN 6088) |
| [14] | adc_data_ctrl | RW | 0x1 | "ADC data mode: 0: Complement code mode; 1: Offset code mode." (EN 6090-6091) |
| [13:10] | buffer_trim | RW | 0x0 | No description (EN 6093) |
| [9] | intv_mode | RW | 0x0 | No description (EN 6094) |
| [8] | enadc0 | RW | 0x0 | "I-channel ADC function is enabled. The I channel output data is valid after enadc0 is high and lasts for the wake-up time (usually 120 working clock cycles). enadc0 is set to 0, ADC I channel output data will ... The last status value is maintained." (EN 6100-6105) |
| [7] | enadc1 | RW | 0x0 | Q-channel equivalent of enadc0 (EN 6107-6112) |
| [6] | adc_enref | RW | 0x0 | "Reference level enable. 0: off; 1: on" (EN 6123) |
| [5:2] | adc_bgtrim | RW | 0x0 | "band-gap Trim configuration value: When using the internal reference (enref=1), it is used to calibrate the level difference between the bandgap voltage and VREF... Usually 8 calibrations can adjust the difference from +-4% to +-1% (bgtrim value '1000' to '1111')." (EN 6125-6131) |
| [1] | adc_extcm | RW | 0x0 | "Common-mode voltage control: 1: Common mode voltage is externally input VCM0/VCM1; 0: Common mode voltage uses internal reference, (VINP0+VINM0)/2, (VINP1+VINM1)/2." (EN 6136-6141). ZH 5164-5166 confirms "1: determined by externally input VCM0/VCM1." |
| [0] | adc_semode | RW | **0x1** | "ADC input mode is optional: 0: VINP/VINM is differential input; 1: VINP/VINM are single-ended inputs, where VINP is connected to the signal input source and VINM is connected in two ways: In AF mode, VINM and VCM are connected to a fixed level (AF bias); in IF mode, VINM and VCM to GND together." (EN 6143-6152). ZH confirms the same. |

- **Reset note:** adc_semode resets to 1 (single-ended). With enadc0/enadc1 and adc_enref reset to 0, the ADC is off after reset.
- **Undocumented bits:** `intv_mode` [9] and `buffer_trim` [13:10] have no description in EN, ZH, or the v2.2 manual. `adc_testin`/`adc_testout` have no encodings.
- **Power-down bits:** there is no explicit ADC power-down bit in ADC_CONTROL. `enadc0` and `enadc1` gate the I and Q channels; `adc_enref` gates the reference. Quote: "enadc0 is set to 0, ADC I channel output data will ... The last status value is maintained." (EN 6102-6106). Note that the datasheet also says the codec ADC has separate power-down bits (Section B), not this one.
- **Gain bits in ADC_CONTROL:** none. The ADC gain for AF is in RF_MODE (A3).

### A3. RF_MODE (0x104) AF bits and SIG_CENTER (0x108), RSSI_BIAS_VALUE (0x11C), PHASE_OUT (0x128)

**RF_MODE 0x104** (table at EN 6313-6360; second copy at EN 18700-18730 in chapter 9):

- `[25] phase_avg_en`, RW, reset 0x0: "AF mode average calculation switch, high effective" (EN 6333).
- `[24] af_mode`, RW, reset 0x0: "AF mode settings: 0: AF signal reception disabled; 1: AF signal reception enabled." (EN 6333-6337).
- `[23:16] af_recv_ctrl`, RW, reset 0x0: "AF input signal amplitude gain control. Signal amplitude = input signal amplitude * af_recv_ctrl/16" (EN 6346-6349). Chapter 9 copy (EN 18720): "AF receiving mode ADC input signal is directly phase signal".
- `[29]` (RW, reset 0x0): "In the physical layer receiving test mode, it is required to be configured as 1. Normal mode set to 0" (EN ~6320).
- Section 8.2.3.1 text on gain (EN 18389-18392): "The AF signal gain is adjusted by register RF_MODE[23:16]. 0~255 respectively represent 0~15.9375 multiplier gain, that is, the unit step is 1/16 times."
- **Bit-number conflict (important):** the register table (EN 6333-6337) puts `af_mode` at **bit 24** and `phase_avg_en` at **bit 25**. The text at EN 18401-18406 says "it is recommended to turn on register AF_MODE[25] and set '1' to be valid, which can effectively avoid the interference of the high-frequency signal envelope on the AF phase signal under strong RF signals." ZH 14834-14835 uses the same "AF_MODE[25]" wording. So the recommended bit (25) is the register-table `phase_avg_en`, not `af_mode`. Confirm on hardware.
- **Reset-value consequence (my inference):** af_recv_ctrl resets to 0. By the datasheet formula (signal = input * af_recv_ctrl/16), a zero gain means zero AF signal, so the register must be written before AF reception works. The datasheet does not say this explicitly.
- Mode bits in RF_MODE: `[14:13] rf_recv_mode`: "0x0: intermediate frequency receiving mode; 0x1: intermediate frequency IQ receiving mode; 0x2: baseband IQ reception mode" (EN ~6355-6362). AF is selected by `af_mode`, not by this field.

**SIG_CENTER 0x108** (EN 18812-18822; EN 18349 for the recv-side summary):

- `[15:8] recv_i_sig_center`, `[7:0] recv_q_sig_center`, RW, reset 0x0: "Receive I channel bias value setting 0~255" / "Receive Q channel bias value setting 0~255". No units, no scaling, no mention of full scale.
- Section 8.2.3.2 (IF mode, EN 18411-18420): "the bias of the received IF signal can be adjusted by configuring the SIG_CENTER register." **The AF section does not use SIG_CENTER**; AF bias is from the CPU DAC (A1). Section 8.2.3.2 is the only place that says SIG_CENTER is for receive bias.

**RSSI_BIAS_VALUE 0x11C** (EN 18891-18910, 9.2.2.8; FLOW 46419-46452):

- `[31:16] rssi_value` RO, reset 0x0: "Signal energy detection calculation value." No units.
- `[15:8] adc_bias_i` RO, reset 0x0: "ADC I input DC offset detection" (EN 18903).
- `[7:0] adc_bias_q` RO, reset 0x0: "ADC Q-channel input DC bias detection."
- ZH 15205-15207: "ADC的I路输入直流偏置检测" = "ADC I-channel input DC bias detection."
- **Units and scale are not given.** The datasheet does not say whether 234 (0xEA) is near full scale, what the zero point is, or whether the value is signed or offset binary. Given `adc_data_ctrl` default = offset code mode (A2), a mid-scale value is plausibly near 128 (inference only; not stated).

**PHASE_OUT 0x128** (EN 18938-18952, 9.2.2.10; FLOW 46575-46600):

- `[29:16] phase_max_out` RO, reset 0x0: "When detecting the phase input amplitude in AF receiving mode, 14-bit phase signal maximum value."
- `[13:0] phase_min_out` RO, reset 0x0: "... 14-bit phase signal minimum value."
- Section 8.2.3.1 (EN 18389-18396): "By reading the register PHASE_OUT in real time get the AF under the current gain configuration phase amplitude range (for standard signal strength of -47 dBm, modulation frequency deviation 2.749 kHz) plus or minus 3 signal, AF phase amplitude range is -1300 to 1300." 
- Register description 0x128 in the peripheral table: "AF Phase amplitude (including gain) in receive mode. Interval. Represented by maximum and minimum values respectively." (EN 18356).
- **Post-gain:** yes, per EN 18356 "(including gain)".
- **Post-DC removal:** not stated.
- **Signed:** not stated. The -1300..1300 range in the text implies signed, but the register is described only as "14-bit phase signal" and the field is 14 bits wide. Treat the sign convention as unknown.
- Note: [29:16] is 14 bits and [13:0] is 14 bits, so the register uses both halves; the bits in between ([15:14], [31:30]) are reserved.

### A4. Input clipping and saturation detection

- **Modem ADC: none documented.** No saturation, overflow, or clip flag exists in ADC_CONTROL, RF_MODE, SIG_CENTER, RSSI_BIAS_VALUE, or PHASE_OUT. The only amplitude readback is `phase_max_out`/`phase_min_out` (A3), which is a min/max of the phase signal, not a clip count.
- **Codec ADC (different block): AGC saturation threshold.** EN 16741-16760 (section 6.4.5 AGC, "CODEC ADC Automatic Gain Control"): "Critical threshold (threshold): Determine ADC is the current output value saturated? When the output value is greater than the threshold, AGC will quickly reduce ADC output gain. CRIT_THR = FS - 3dB, that is, the boundary threshold is set by CODEC. The full scale of the ADC determines the value and does not require configuration by the user." (EN 16755-16760). This is for the codec, not the modem RF ADC.
- **Pin name and mapping conflict affects which ADC pin is clipped.** See A5.

### A5. Pin mapping conflict (critical for wiring the AF input)

The datasheet gives two different assignments for the same ball positions.

- **Ball map** (EN 1858-1864): `N2 ADC_VINP_I`, `P1 ADC_VINCM_I`, `P2 ADC_VINM_I`. (ZH 1851-1861 matches.)
- **Pin-description table** (EN 2645-2667): `N2 ADC_VINP_I` (2645), `P1 ADC_VINM_I` (2655), `P2 ADC_VINCM_I` (2667). (ZH 2473-2484 matches.)

So P1 and P2 are swapped between the two tables. The pin descriptions themselves also conflict with the register semantics:

- EN 2645-2651 (N2 ADC_VINP_I): "In differential mode, the common-mode voltage of the I-way input is provided externally when EXTCM=1; when EXTCM=0 When, the pin is floating; In single-ended mode, this pin is connected to the analog ground; In pseudo-differential mode, this pin is the same as the VINM input."
- EN 2655-2660 (P1 ADC_VINM_I): "In differential input mode, it is the differential negative terminal of I input; In pseudo differential mode, the input voltage is fixed at 3.3V."
- EN 2667-2670 (P2 ADC_VINCM_I): "In differential input mode, it is the differential positive terminal of I input; In differential and single-ended modes, it is the signal input terminal."

Taken together with ADC_CONTROL (adc_semode, EN 6143-6152), which says in single-ended mode "VINP is connected to the signal input source and VINM is connected ... AF bias":

- **Register semantics:** signal = VINP, bias = VINM.
- **Pin-table semantics:** signal = VINCM (P2 in EN 2667 table; P1 in ball map), and VINP (N2) is tied to analog ground in single-ended mode, and VINM is the bias pin.
- These two cannot both be right for the same pin. The AF text (EN 18398) says "AF signal terminal is connected to the P terminal of the ADC" and "configure the N side for bias" which matches neither assignment cleanly.

Also: the IF section names the pin **`ADC_IVINP`** (EN 18446; ZH 14872) and "ADC_IVINN", which are not in any pin table (grep for them returns only that line). The pins are probably the same ADC_VINP/VINM pins under another name.

**Practical summary:** the datasheet does not unambiguously say which physical pin receives the AF signal and which receives the CPU DAC bias. Confirm on the board before wiring.

---

## B. Audio codec (register block 0x1600_09C0)

Codec base address is **0x1600_09C0** (EN 16820 "Codec Register Overview (Base Address: 0x1600_09C0)"; EN 1510). Note: this is not 0x1800_xxxx. 0x1800_0000 is a 32 KB SRAM on SAHB (EN 5654), used as a voice buffer, not the codec.

### B1. Can the CPU read codec ADC samples?

**Yes, but not via a codec ADC data register.** The codec ADC output goes to the modem, and the CPU reads the modem RAM or the SRAM buffer. There is no codec ADC data register or FIFO that the CPU reads directly.

- Codec ADC overview: EN 16364-16386: "two-way differential / single-ended Mic input support", "24bit-16bit Linear PCM Mode output", "Support 8K, 16K, 24K, 48K sampling frequency" (EN 16378-16379).
- **Digital mode path:** EN 17939-17945 (section 7.1.2.1): "Audio MIC/LINE IN signal passes through the built-in codec ADC ... output 16bit ... PCM audio code stream ... combined in pairs to form 32 bits data ... cached to the built-in RAM 0x1800_0000~1800_009F area, and output interrupt PCM_wr_data_interp (irq28, see 3.6.3)." Readable by CPU as SRAM at 0x1800_0000-0x1800_009F.
- **Analog (FM) record path:** EN 18104-18111 (section 7.2.2.3.1 "Analog sending recording"): "Configuration Modem Register: 0x1100_0080[0] = 0 Select audio source as CODEC ADC ... the analog recording function is enabled by default. ADC output audio data is given to FM when sent, it is also saved to the 0x1600_0030~0x1600_042F range in TX RAM. CPU access the area and move and save the data." This is the closest documented "CPU can read ADC samples" path: TX RAM 0x1600_0030-0x1600_042F, ping-pong (EN 18084-18086, "divided into 2 slice data space 0x1600_0030~0x1600_022F and 0x1600_0230~0x1600_042F ... 512 byte, can save 256 16-bit audio data (32 ms)").
- **Digital record path:** EN 18021-18024 and 18028-18033: pre-encoding audio at 0x1800_0000-0x1800_009F; post-encoding at 0x1600_0960-0x1600_097A (TX); RX before decoding read from 0x1600_0960-0x1600_097A.
- **Codec-side formats:** `AICR_ADC` (B2) selects parallel/I2S/DSP/left-justified; default is I2S (reset 0x3) and the datasheet says it "needs to be fixed Parallel mode" (EN 16981-17010). Mode settings for ADC: "0x1600_09C9[7:6] = 11 select ADC output width 24 bit; 0x1600_09C9[1:0] = 00 ADC audio interface parallel mode" (EN 16592-16608, section 6.4.3.1).
- **Modem AUDIO_CONTROL `sel_i2s` [2]** (EN 6154-6200): "I2S interface enabled, used in baseband test mode." `i2s_slave_mode` [1]: "0 master mode, 1 used in slave mode and baseband test mode." These are modem-side I2S controls and are the only I2S-to-CPU-facing bits found. The datasheet does not describe which I2S pins they use.

### B2. Register list for the codec ADC side

From EN 16817-16862 (Register Overview, Table 26) and register descriptions EN 16864-17770.

| Offset | Name | Relevant ADC bits (EN line) |
|---|---|---|
| 0x07 | MR | `adc_mute [4:3]` RO: "00: not muted; 01: being muted; 10: about to end the mute state; 11: in mute state" (EN 16922) |
| 0x09 | AICR_ADC | `adc_adwl [7:6]` (reset 0x3, 24 bit); `adc_slave [5]` (0 master, 1 slave); `sb_aicr_adc [4]` (0 active, 1 power-down, reset 1); `adc_audioif [1:0]` (00 parallel, 01 left-justified, 10 DSP, 11 I2S; reset 11) (EN 16981-17010) |
| 0x0B | CR_MIC1 | `micdiff1 [6]` (0 single-ended, 1 differential); `sb_micbias1 [5]` (reset 1); `sb_mic1 [4]` (0 active, reset 1); `micbias1_v [3]` (0: 2.08 V; 1: 1.66 V) (EN 17016-17030) |
| 0x0C | CR_MIC2 | `micdiff2 [6]`: "0: AIPL1/AINL1; 1: AIPL2/AINL2" (EN 17040-17050) |
| 0x0F | CR_ADC | `adc_soft_mute [7]` (reset 1); `adc_dmic_sel [6]` (0 analog mic, 1 digital mic "(none)"); `adc_left_only [5]` (reset 1: right channel disabled); `sb_adc [4]` (0 on, 1 off; reset 1) (EN 17070-17088) |
| 0x17 | FCR_ADC | `adc_hpf [6]` (high-pass filter); `adc_wnf [5:4]` (wind-noise filter off/mode1/2/3); `adc_freq [3:0]` "0000: 8 kHz; others omitted" (EN 17307-17320) |
| 0x25 | GCR_ADCL | `lrgid [7]`; `gidl [5:0]`: "000000: 0 dB; 000001: 1 dB ... 101010: 42 dB; others: 43 dB" (EN 17517-17540) |
| 0x26 | GCR_ADCR | `gidr [5:0]`, same scale (EN 17541-17560) |
| 0x29 | GCR_MIXADCL | `lrgimix [7]`, `gimixl [5:0]` (aliasing ADC gain) (EN 17608-17630) |
| 0x2A | GCR_MIXADCR | `gimixr [5:0]` (EN 17630-17640) |
| 0x2F | CR_ADC_AGC | ADC AGC control (EN 17751) |
| 0x30 | DR_ADC_AGC | ADC AGC indirect data (EN 17767) |

- **Codec power:** the datasheet does not define an ADC power-up sequence beyond the bits above. Section 6.4.3.2 (EN 16610-16640): `0x1100_0080[0] = 0`, `SB_SLEEP (0x1600_09D2[1] = 0)`, `SB_MIC1 (0x1600_09CB[4] = 0)`, `SB_AICR_ADC (0x1600_09C9[4] = 0)`, `SB_ADC (0x1600_09CF[4] = 0)`, `ADC_SOFT_MUTE (0x1600_09CF[7] = 0)`.
- **Wake-up:** "Fast ADC typical wake-up time: 100 ms" (EN 16380). Electrical: "From power-down to record mode 124 / 230 ms" (EN 3050).
- **Codec ADC performance:** "ADC full scale, Gain GID=0dB, GIM=20dB: 0.189 / 0.212 / 0.239 Vpp" (EN 3054). SNR 85 / 90 dB (EN 3054-3060). Input resistance 19-29 kOhm single-ended at GIM=20 dB (EN 3067-3068). Cbyline 1 uF (EN 3070; codec text EN 16405).

### B3. Analog bypass or mixer path from codec input to codec DAC/PWM

- **Digital aliasing mixer (ADC to DAC, digital domain)** exists. It is accessed indirectly: `CR_MIX` (0x10, EN 17090-17103): `mix_en [7]` "Digital aliasing enabled; 0: off; 1: on"; `mix_load [6]` "0: read; 1: write"; `mix_add [5:0]` "Digital aliased indirect address". Data via `DR_MIX` (0x11, EN 17205-17212) `mix_data [7:0]`.
- Indirect map entries:
  - MIX_0 (0x00): `dac_mix [0]`: "DAC1 Input audio aliasing enabled, 0: DAC only; 1: DAC+ADC" (EN 17131). `aidacl_sel [7:6]` and `aidacr_sel [5:4]` select the DAC modulator input source.
  - MIX_2 (0x02): `mix_rec [0]`: "ADC Aliasing of output data is enabled, 0: ADC only; 1: ADC+DAC" (EN 17177). `aiadcl_sel [7:6]`, `aiadcr_sel [5:4]` select which ADC channel goes to the port.
  - MIX_3 (0x03): `mixadcl_sel [7:6]`, `mixadcr_sel [5:4]`: ADC source to the mixer.
  - MIX_1 (0x01): `mixdacl_sel`, `mixdacr_sel`: DAC source to the mixer.
- **Analog bypass:** not documented. No register routes the codec analog input to the codec analog output. The only analog sidetone is at the modem level: `AUDIO_CONTROL.fm_sidetone_en [3]` ("Analog side tone playback is enabled, high effective", EN 6181-6182). Section 7.2.2.4 (EN 18132-18136) says it plays the DTMF/SelCall signaling locally: "Enable the sidetone function. DTMF and SelCall in analog mode ... the signaling signal sent will be played locally for voice playback (in this case, the corresponding CODEC DAC needs to be enabled)." This is the modem's signaling output, not the codec ADC input.

### B4. Codec interrupt list

- **Codec-local interrupt registers** (EN 17348-17430):
  - `ICR` (0x1A) `int_form [7:6]`: "00: IRQ held high; 01: IRQ held low; 11: IRQ is a negative pulse with a pulse width of 8 MCLK (parallel) or 8 MCLK (I2C)." (EN 17348-17370). The "10" encoding is missing from the table.
  - `IMR` (0x1B), mask bits: `adas_lock_mask [7]` (reset 1, "Block ADAS Lock sign"), `adc_mute_mask [2]` (reset 1, "Shield ADC Mute sign"), `dac_mute_mask [0]` (reset 1, "Shielded DAC Mute sign") (EN 17373-17385). Mask polarity: "1 = block" per the description text, but the datasheet does not say explicitly.
  - `IFR` (0x1C), event flags: `adas_lock_event [7]` "ADAS Lock event flag ... (this function is not involved in the current application, DAC always works in Master mode)"; `adc_mute_event [2]` "ADC Soft mute mode event flag ... (this function is not involved in the current application)"; `dac_mute_event [0]` (EN 17387-17410).
  - `IMR2` (0x1D): `TIMER_END_MASK [4]` (reset 1) "Timer interrupt mask" (EN 17413-17425).
  - `IFR2` (0x1E): `TIMER_END [4]` "Timer count end interrupt" (EN 17428-17440).
  - `CR_TIMER_h/l` (0x18/0x19): "The timer counts down with a slow clock (usually 512 MCLK). When the count reaches 0, an interrupt is generated." (EN 17323-17345).
  - `SR1` (0x00) `irq_ack [6]` RO: "Interrupt pending status register; 0: no pending interrupts; 1: there is currently an unresponded interrupt." (EN 16864-16880).
- **Modem-side codec-data interrupts** (EN 5821-5866 Table 28; 6926-6935 Table 27-ish; 6796-6810):
  - `PCM_WR_INTERP` (serial 6): "Codec Audio data stream write interrupt" (EN 5851).
  - `PCM_RD_INTERP` (serial 7): "Codec Audio data stream reading interrupt" (EN 5860).
  - IRQ table: `27 PCM_rd_data_interp` (IRQ 59), `28 PCM_wr_data_interp` (IRQ 60), both level-triggered high (EN 6930-6933). The table row for `26 ADC` (IRQ 58) has no description in EN; it is probably the MCU ADC.
  - Clear bits in `INTERP_CLEAR` (0x3B0, EN 6796-6810): `clear_pcm_rd_int [5]`, `clear_pcm_wr_int [4]`, write 1 to clear.
  - Section 7.1.2.1 (EN 17944-17945): PCM_wr is irq28. Section 7.2.2... (EN 18012 and 18165): irq27 signals "data reading complete at 10 ms intervals".
- **Codec IRQ to CPU: not stated.** The datasheet does not say whether the codec `IRQ` output (ICR) is wired to the CK803 interrupt controller. Only the modem IRQ numbers 27/28 are documented.

---

## C. Modem-block registers that mention test, loopback, monitor, capture, record, or readable ADC data

Searched EN for: test, loopback, monitor, capture, record, debug, ADC data, testin, testout (EN 5000-7000, 15000-19000, 44000-47000 ranges, plus codec 16364-17800).

### C1. Modem block (0x1100_xxxx)

- **ADC_CONTROL.adc_testout [31:30]** (RO, EN 6086): "Test output status". Encoding not given.
- **ADC_CONTROL.adc_testin [27:24]** (RW, EN 6088): "Test input control". Encoding not given.
- **RF_MODE[29] and [31]** (EN 6319-6328): "In the physical layer receiving test mode, it is required to be configured as 1. Normal mode set to 0."
- **RF_MODE.two_point_ctr [10]** (EN 6384; also EN 18763): "Two-point modulation test enable: 0: off; 1: output 40 Hz sine wave." TX side, not ADC.
- **RF_MODE.iq_just_debug [8]** (EN 6400-6402): "IQ two-way balance debugging test enable: 0: means unchanged; 1: adds an offset value." TX side.
- **AUDIO_CONTROL.sel_i2s [2]** and **i2s_slave_mode [1]** (EN 6186-6200): "used in baseband test mode."
- **AUDIO_CONTROL.ahb_rd_voice_en [5], ahb_wr_voice_en [4]** (EN 6154-6200): AHB audio read/write enables (not explicitly ADC).
- **AUDIO_CONTROL.tx_voice_source [0]** (EN 6196-6204): "0: voice data comes from a Codec or vocoder ... 1: voice data comes from the RAM space corresponding to the MCU." This is a source select, not a capture.
- **AUDIO_BUFFER_CLR** (EN 6210-6220): clears vocoder encode/decode buffers. Not ADC.
- **RSSI_BIAS_VALUE** `rssi_value`, `adc_bias_i`, `adc_bias_q` (RO, EN 18891-18910). Readable, see A3. Units not given.
- **PHASE_OUT** `phase_max_out`, `phase_min_out` (RO, EN 18938-18952). Readable, see A3.
- **Chapter 8.3.3, digital receive** (EN 18484-18500): external RF IC digital ADC via SPI: "AD_FS ... frequency is 38.4 KHz; AD_SCLK ... 2.4576 MHz; AD_SDO is ADC serial output data ... AD_SDO Optional status data S13-S0 in including RSSI Value, AGC Status values and other information via SPI input to HR_C7000, and this information can be obtained through the internal related registers." Quote is EN 18484-18500. This is an external digital ADC interface, not the internal ADC, but it is the only place the datasheet says RSSI/AGC status are "obtained through internal related registers". The register name is not given.

### C2. Memory regions used as ADC or demodulator data (not registers)

- **TX RAM 0x1600_0030-0x1600_042F** (EN 18084-18110): analog-send recording: ADC output saved here; ping-pong 0x1600_0030-0x1600_022F and 0x1600_0230-0x1600_042F.
- **RX RAM 0x1600_04E0-0x1600_08DF** (EN 18118-18123): "analog receiving recording ... audio signal generated by the receiver demodulation ... is also saved to the 0x1600_04E0 to 0x1600_08DF range in RX RAM."
- **Digital recording 0x1600_0960-0x1600_097A** (EN 18028-18033).
- **FM Receive RAM at 0x160004b0** (EN 23078-23088, FM voice halves FM_VOICE_1 0x030-0x22F and FM_VOICE_2 0x230-0x42F): the FM demodulated voice data. As requested, not analyzed further here.
- **Physical layer receive RAM base 0x160004b0, depth 1200** (EN 19741). Physical layer output, not ADC.
- **SRAM 0x1800_0000-0x1800_009F** (EN 17944-17945) and **0x1800_00A0-0x1800_0140** (EN 18010-18014): codec PCM buffers (B1).

### C3. MCU general-purpose ADC (not the baseband ADC, but readable and in the same family)

These are in a different block (base 0x140D_0000 per EN 15769). Listed because the question asks about "any ADC data readable register".

- `ADC_DATA_AB` (0x30), `ADC_DATA_CD` (0x34), `ADC_DATA_EF` (0x38), `ADC_DATA_GH` (0x3C): "ADC Channel AB/CD/EF/GH Scan Value" (EN 15795-15798).
- `ADC_DIR_DAT_OP` (0x44, RO): "ADC EOC then directly output ADC ... ADC data directly after EOC (for debug)" (EN 15800; EN ~16229).
- `ADC_P2S_EN` (0x2C, bit [0]): "Enables serial output of sampled data, for debugging only. 1: enable; 0: disable." (EN 15794, 16141). Serial output of samples to a pin. Pin not stated.
- Data rate: "Data conversion rate 200 KSPS, sampling accuracy 10 bit, 8 ADC channels" (EN 15402-15408, section 5.9.2).

### C4. Not found

- No register in the baseband modem block (0x1100_xxxx) gives direct readback of the raw I/Q ADC sample values. The only readbacks are RSSI_BIAS (energy and DC), PHASE_OUT (min/max phase), and the external-ADC status in C1.
- No register captures the FM demodulator output except through the RX RAM paths (C2).

---

## Open questions for hardware bring-up

1. Which pin is the AF signal (ADC_VINP_I N2, ADC_VINCM_I P1 or P2, ADC_VINM_I P2 or P1)? The datasheet is self-contradictory (A5).
2. Is there a coupling capacitor? Not specified. The figure shows none.
3. Which CPU DAC channel (A, B or C) drives the AF bias, and what voltage range is needed? Not specified (A1).
4. What is the RSSI_BIAS adc_bias_i/q scale and zero point? Not specified (A3).
5. Is `phase_max_out`/`phase_min_out` signed? Not specified (A3).
6. Is `phase_avg_en` (bit 25) or `af_mode` (bit 24) the bit the recommendation refers to? The text says bit 25, the table says bit 25 is `phase_avg_en` (A3).
7. Does the baseband ADC have a defined sample rate? Not specified (A1).

---

## 10-line summary

1. AF input: no series coupling capacitor or divider is shown or specified. Figure 64 (EN 18386; PDF p.485) shows AF_out wired directly from RDA1846S to HR_C7000.
2. DC bias: "AF Signal bias then CPU DAC on the bus Provide an offset point" (EN 18398). DAC channel is not named; pins DAC_VOUT_MCUA/B/C (EN 2719-2728) default to GPIO via IO_DIPLEX2 bits 27-29 (EN 4418-4436).
3. Common mode and swing: VINcm DC 0.5-1.5 V, AC 0 V; VINdiff 0.9 Vpp, VINse 0.45 Vpp (EN 2985-2991). Resolution 10 bit, ENOB 9, SNR 56 dB (EN 2993-3011). No modem sample rate stated; adc_clk 117.9648 MHz (EN 3327).
4. ADC_CONTROL 0x74 (EN 6080-6152): adc_semode [0] reset 1 (single-ended), "In AF mode, VINM and VCM are connected to a fixed level (AF bias)"; adc_extcm [1]; enadc0 [8] / enadc1 [7]; adc_enref [6]; adc_bgtrim [5:2]; adc_data_ctrl [14] reset 1 (offset code); test bits [31:30], [27:24] undocumented.
5. RF_MODE 0x104 (EN 6333-6349): af_mode bit 24 (reset 0), af_recv_ctrl [23:16] "Signal amplitude = input * af_recv_ctrl/16" (reset 0, so zero gain until written). Text recommends bit 25, which the table names phase_avg_en (EN 18401-18406).
6. SIG_CENTER 0x108 (EN 18812-18822) and RSSI_BIAS_VALUE 0x11C adc_bias_i/q (EN 18891-18910): 0-255, no units or scale given; PHASE_OUT 0x128 [29:16] and [13:0] are 14-bit AF phase max/min including gain (EN 18938-18952), signed not stated.
7. Pin conflict: ball map says P1=ADC_VINCM_I, P2=ADC_VINM_I (EN 1863-1864); pin table says P1=ADC_VINM_I, P2=ADC_VINCM_I (EN 2655, 2667). Single-ended pin text puts the signal on VINCM but the register puts it on VINP.
8. Codec (base 0x1600_09C0, EN 16820): ADC output is 16-bit PCM, reaches CPU via SRAM 0x1800_0000-0x1800_009F with PCM_wr irq28 (EN 17944-17945), or via TX RAM 0x1600_0030-0x1600_042F for analog recording (EN 18104-18111). No codec ADC data register; no analog ADC-to-DAC bypass; digital mix_rec/dac_mix only (EN 17090-17205).
9. Codec interrupts: ICR, IMR, IFR, IMR2, IFR2 (adas_lock, adc_mute, dac_mute, TIMER_END) at EN 17348-17440; modem PCM_rd irq27 / PCM_wr irq28 at EN 6930-6933.
10. Test or capture registers: ADC_CONTROL adc_testout/adc_testin (undocumented encodings, EN 6086-6088); readable RO: RSSI_BIAS, PHASE_OUT; memory: TX/RX RAM recording (EN 18084-18123); no raw I/Q sample readback register found.
