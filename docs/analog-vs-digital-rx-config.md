# Analog vs digital RX configuration diff, and why analog AF may not reach the modem ADC

Status: STATIC research only (stock firmware DM32.00.01.048 + vendor datasheets). Nothing here was run on a
radio. Every experiment in section 6 is a prediction to be tested, not a result.

Evidence rules: stock firmware RE + vendor datasheets only. An experimental OpenRTX DM-32 port was not used
as evidence.

Sources and conventions:
- FW = the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) corpus (`src/`, `meta/disasm.jsonl`). All addresses below were
  re-checked with `csky-abiv2-elf-objdump` (via `tools/src/dm32_patch_tools/cs_disasm.py`, capstone-csky was
  not available, so the objdump fallback was used). The generated `src/` C and `meta/disasm.jsonl` are
  desynchronised in places (e.g. the tail of `channel_change_apply_dispatcher`, the GPIO calls at
  0x0305809a..0x030580b4 are missing from the corpus disassembly), so objdump listings were used for those.
- IRAM = `data/firmware/IRAM_dm32.00.01.048.bin`, base 0x10000.
- FDG = FD6818 programming guide rev 1.0.0 (Chinese), line numbers of a Markdown conversion
  (plus a `pdftotext -layout` copy for the register summary; the .flow.md drops several register rows).
- DS = HR_C7000 datasheet v2.7 (English), line numbers of a Markdown conversion;
  DS-zh = the Chinese v2.7 datasheet; UM22 = HR_C7000 User Manual v2.2 (Chinese).
- Working listings (not in the repo): every constant FD6818 write in flash, the GPIO call sites, and full
  objdump listings of flash and IRAM. They are regenerated from the corpus with objdump.

Channel-type nibble: `p_vfo_display_state->flags_0x8 & 0xf0` (RAM 0x3e1dc + 8): 0x00 analog, 0x10 digital,
0x20 / 0x30 are analog+digital "mixed" types that the dispatcher folds back into 0 or 0x10 at
0x030573e8 (L~340-370 of the decompile). "Analog" below means nibble 0; "digital" means nibble 0x10.

Register-name caveat (important): stock addresses the FD6818B with literal wire addresses. These match the
guide for 0x02 (irq status, `task_body_uCOS3` 0x0300e4f2), 0x38/0x39 (frequency), 0x3b/0x3c (xtal),
0x3f (irq mask), 0x43, 0x47, 0x40, 0x50, 0x51. The RXON/TXON register is wire 0x30 in stock but REG_03H in
the guide, with byte-identical values (0xBFF1 = guide RXON, 0xC1FE = guide TXON, bit 9 = afout_en).
Registers 0x13, 0x2B, 0x32, 0x37, 0x48 (only the DMR "cic mode" value is documented), 0x49, 0x4A, 0x4E,
0x7B are NOT in the guide's register summary (the guide's table skips from REG_28H to REG_38H and has no
REG_13H/2BH/32H/37H rows), so their field meanings are unknown. Treat any decode of them as open.

---------------------------------------------------------------------------------------------

## 0. Headline findings

1. The modem-side configuration that touches the RX ADC is IDENTICAL for analog and digital. ADC_CONTROL,
   RF_MODE (including af_mode and af_recv_ctrl), REG_0x168, RF_RECV_DELAY, AUDIO_CONTROL are written once
   at init and never per channel type (section 3). The differences are on the FD6818, in two GPIOs, and in
   modem WORK_MODE / RF_CONTROL.
2. The FD6818 is configured as a SET, not one register. On a channel-type change stock switches six
   registers together: REG 0x47, 0x48, 0x7E, 0x2B, 0x37 (and 0x3F, 0x43). The digital set equals the
   structure of the guide's "BYPASS mode for DMR/dPMR" recipe (FDG L1759-1796). The analog set does not.
   Testing REG 0x47 alone may therefore not be sufficient (section 6, E1b).
3. There is exactly ONE nonzero static DAC write in the whole firmware: CPU DAC channel C = 0x6e2 at boot,
   channel A is powered OFF, channel B is 0 whenever not transmitting. None of it depends on channel type.
   So the DAC cannot be the analog/digital DIFFERENCE, only a common factor.
4. The vendor documents disagree on where AF bias lives. DS v2.7 (en and zh): a CPU DAC provides the bias to
   the ADC N/M terminal. UM v2.2 zh: "AF signal bias is adjusted by SIG_CENTER (receive IQ bias)".
   `SIG_CENTER[15:0]` (recv_i / recv_q) is NEVER written by stock; only the TX half [31:16] is (section 2).
5. Measured by the caller: `adc_bias_i` = 234/255 on analog. In offset-code mode (ADC_CONTROL bit14 = 1) mid
   scale is 128, so the I input DC sits +106 codes (+83% of the positive half) from centre, leaving about
   17% positive headroom. The Q channel (129) is centred. Whether digital reads the same is the single most
   useful missing measurement (experiment E0, read-only).
6. A caveat on the metric: `PHASE_OUT` (0x128) is documented as the phase amplitude in "AF receiving mode".
   Stock runs analog with WORK_MODE bit7 = 1 (FM engine). It is not documented whether PHASE_OUT keeps
   updating in FM mode. A frozen 37/38 may be an artefact, not proof of a dead ADC. Use `rssi_value`
   (0x11c[31:16]) and `adc_bias_i` (0x11c[15:8]) as the cross-check.

---------------------------------------------------------------------------------------------

## 1. FD6818 register writes that differ by channel type

Normal RX state (the stock flag at RAM 0x3e318+0x11 bit5, i.e. `p_radio_mode_state_0003e318[1].flags_0x7`
bit5, is clear; when it is set the IRAM retune takes a shortcut that writes the analog set, see note b).

Order of events on a channel change (`channel_change_apply_dispatcher` @0x030573e8):
1. `tx_timeout_cleanup` (0x0305fc24) at entry.
2. `tx_power_mode_apply_from_flags` @0x0300dfa0 -> `tx_bandwidth_deviation_mode_set` @0x0300f1b4 (writes the
   set in Table A, cached by two state bytes at `PTR_DAT_0300f254[1]` and `[2]`, so it only rewrites when the
   mode changes).
3. `freq_calibration_apply_all` @0x0300db48.
4. `channel_change_apply_squelch` @0x0300df08: REG 0x78, IRAM `FUN_0002b6a0` (retune), antenna GPIOs.
5. On a type change: `tone_or_volume_step_lookup` @0x030230bc.
6. `channel_type_mode_settings_write` @0x0304c68c, `rf_mode_transition_on_freq_delta` @0x03055f88
   (modem, section 3).
7. Tail @0x03058078..0x030580b4: FM_BANDWIDTH and GPIO 0x2E / 0x2F (section 4).

### Table A: registers whose value differs (wire address = what stock sends)

| reg | analog | digital | where written |
|---|---|---|---|
| 0x47 | 0x6042 | 0x0140 | (1) `config_table_init_entries_0300e884` @0x0300e88c = 0x6042 (analog) and `config_table_init_group1_variant_0300f16c` @0x0300f174 = 0xA0<<1 = 0x0140 (digital), both via step 2. (2) IRAM retune 0x2b6a0: analog 0x2b7d8 = 0x6042, digital 0x2b6ec = 0x0140. |
| 0x48 | 0x31E0 \| sel | 0xBBBF | analog: IRAM `fd6818b_reg48_cic_mode_write` 0x2b624 (`sel` = byte at RAM 0x3ecfd, from `config_table_init_entries_and_param_2b` @0x0300f19e, retune 0x2b7ca, and `tone_or_volume_step_lookup` @0x030230d0 on a type change). digital: `config_table_init_group1_variant_0300f16c` @0x0300f17e. The digital retune does NOT write 0x48. |
| 0x7E | 0x302E | 0xB03F | `config_table_init_entries_0300e884` @0x0300e896 / variant_0300f16c @0x0300f188 |
| 0x2B | 0xC000 | 0x0700 | `config_table_init_entries_and_param_2b` @0x0300f1ac (0xC0<<8) / variant_0300f16c @0x0300f192 (0xE0<<3) |
| 0x37 | 0x1F0F | 0x1F1F | IRAM `fd6818b_filter_select_then_chipmode_normal` 0x2b5e4 (tests nibble == 0x10) and flash `rf_transceiver_mode_init` @0x03054c24/0x03054c30. (Sleep/idle: 0x1D00 analog / 0x1D11 digital in `fd6818b_partial_display_mode_reconfigure` @0x03054c80.) |
| 0x3F | tone irq mask: 0x38688 shadow (0 or 0x3002 / 0x0800 bits for CTCSS/DCS/DTMF/tone modes) | 0x0000 | IRAM retune 0x2b7c2 (analog) / 0x2b6fa (digital) |
| 0x43 | 0x4048 (flags_0x9 bit7 = 0) or 0x3028 (bit7 = 1) | 0x4048 | `fd6818_tx_deviation_bandwidth_mode_set` @0x0300e794 via step 2. By the guide (REG_43H): [14:12] firlpf_bw, [5] wb, [1:0] firlpf_gain. firlpf_gain is 0 dB in both. |
| 0x07, 0x58-0x5E, 0x72 (analog sub-audio decoder) | programmed (divider reg 0x07 via `rf_channel_mode_select_and_apply` @0x0300e218; 0x58/0x59/0x5A-5E/0x72 via `FUN_03067564`, `FUN_03067224`, `FUN_0300f068`, `FUN_030645ec`, chosen by `vfo_display_state.field_0x16 & 0xe`) | not written | IRAM retune 0x2b72a..0x2b7b8, analog branch only. Sub-tone / DTMF / 2-tone / 5-tone decode, not audio level. |
| 0x30 | 0x0000 then 0xBFF1 | 0x0000 then 0xBFF1 | identical (same RXON value, afout_en = 1 in both). Listed because it brackets the 0x37/0x47 writes. |

Registers written with the SAME value for both types on a channel change:
- 0x13 = 0x037E (both; `config_table_entry_0300f15c` @0x0300f164). 0x3FF only in the special window below.
- 0x32 = 0x0244 (IRAM retune 0x2b6b6; 0x0245 in tx-abort / status-poll paths).
- 0x78 = (b-1)&0xFF | b<<8 (`hw_register_0x78_write_from_byte_pair` @0x0300de78, `channel_change_apply_squelch`).
- 0x38 / 0x39 frequency (`fd6818_set_frequency` 0x2b56c).
- 0x40 = byte | 0x3400 (`fd6818b_reg_write_wrapper` @0x0300de60, TX deviation), 0x50, 0x51, 0x52 and 0x07
  entries (`config_table_entry_dispatch` @0x0300e89c: 0x52 = 0x828F/0xA28F/0xC28F/0xE28F, idx 4 = reg 7 =
  0x046F) depend on settings, not on channel type.

Special window (neither plain analog nor plain digital): if the RX frequency is in
[108.000, 136.000) MHz (`value_at_or_above_low_bound_check` @0x0300d810, bounds at RAM 0x38470+0x20/+0x24,
set in `rf_config_load_from_flash_block2`: 10,800,000 and 13,600,000 in 10 Hz units),
`tx_bandwidth_deviation_mode_set` writes instead `config_table_init_group1_variant_0300f130`: 0x43 = 0x0048,
0x47 = 0x6740, 0x13 = 0x03FF, 0x4A = 0x5450. That is the guide's TX-bypass value (afout_mode 0x07) used in RX
for the air band. Irrelevant to the DM-32 UV 2 m/70 cm channels but explains an otherwise odd 0x6740.

Boot default (`fd6818b_default_register_init` @0x0300ecb8, runs once): 0x47 = 0x0100, 0x48 = 0xBBBF,
0x30 = 0xBFF1, 0x37 = 0x1D0F then 0x1D1F, 0x25 = 0xC1BA, 0x52 = 0x028F, 0x7B = 0x8420, 0x3E = 0xA037,
0x4E = 0x4708, 0x4F = 0x302F. So the post-boot state is digital-LIKE (0x47 mode 1, 0x48 = 0xBBBF); the analog
set is written on the first analog channel change.

Notes:
a. Mode-nibble toggling of REG 0x47 outside channel changes. `fd6818b_reg47_tx_bypass_or_rx_afout` (IRAM
   0x2b51c) writes 0x6740 if RAM 0x3e318+0x11 bit5 is set (TX) else does a read-modify-write that clears
   bits [11:8] and sets bit 8 (afout_mode low nibble = 1). `fd6818b_reg47_afout_clear_mode` (0x2b550) clears
   [11:8] (nibble 0). `fd6818b_reg47_write_byte_or_6042` (0x2b584) writes (byte<<8)|0x6042.
   Callers pair them with `audio_pa_enable_gate(1/0)` (GPIO 0x35) and `audio_ctrl_fm_play_set` (AUDIO_CONTROL
   bit6): `tx_enable_caller2` (0x0305fbf8) = nibble 1 + PA on + fm_play 1; `tx_timeout_cleanup`
   (0x0305fc24, called first thing on every channel change, analog only) = nibble 0 + PA off + fm_play 0.
   So on analog, REG 0x47 is 0x6142 while those paths consider "audio on" and 0x6042 otherwise. Which of the
   two is live in steady analog RX is unconfirmed (section 8, item 6).
b. When RAM 0x3e318+0x11 bit5 is set the IRAM retune jumps to 0x2b7c6 for BOTH types and writes the analog
   set (0x48 = 0x31E0|sel, 0x37 per nibble, 0x47 = 0x6042). The flag's meaning is not established; it is 0 in
   normal RX.
c. `config_table_init_group1` @0x0300e8ec (0x37 = 0x1D0F, 0x52 = 0x028F, 0x30 = 0, 0x30 = 0xC3FA) is only
   called from the tone / alert playback paths (`channel_type_apply_tone_idx_default`,
   `radio_mode_entry_reinit_and_display`), not on the normal channel-change path.
d. An older FD6818 init-table note listed a 0x47 = 0x00A0 write. The instructions are `movi r1,160; lsli r1,r1,1`
   (IRAM 0x2b6e8 / 0x2b6ea), i.e. 0x0140; the old table recorded the pre-shift immediate. Its 0x6740,
   0x6042 and 0x1F1F / 0x1F0F entries match section 1.

---------------------------------------------------------------------------------------------

## 2. DAC writes

CPU DAC = HR_C7000 DAC controller at 0x140F0000 (DS §5.10, Table 17): channels A/B/C = pins DAC_VOUT_MCUA/B/C
(K1/K2/L1), 12 bit, 3.3 V domain, shared with GPIO PTAC23/24/25. Registers: PD_CTRL 0x00 (bit n = 1 means
channel power OFF), PD_MODE_EN 0x04, DATA_A 0x08, DATA_B 0x0C, DATA_C 0x10.

`dac_channel_write_12bit(ch, v)` @IRAM 0x2c388: ch 1 -> DATA_A, 2 -> DATA_B, 4 -> DATA_C, `v & 0xFFF`.
`dac_channel2_write_from_byte(b)` @0x2c3b4 = channel 2 with `b << 4`.

| call site | channel | value | when |
|---|---|---|---|
| `FUN_0301cc30` @0x0301cc30 (from `radio_boot_full_init_sequence` @0x0305f2ec) | PD_MODE_EN = 1, DATA_A = DATA_B = DATA_C = 0, PD_CTRL = 1 (A OFF, B and C ON) | then `dac_channel_write_12bit(4, 0x6E2)` | once at boot. DATA_C = 0x6E2 = 1762 = about 1.42 V if VREF = 3.3 V. The ONLY nonzero static DAC value in the firmware. |
| `config_table_entry_0x30_init_sequence` @0x0300d838 | B | 0 | init |
| `channel_switch_teardown_and_apply` @0x0305fa38 (call at 0x0305fb38) | B | 0 | after TX / channel teardown |
| `factory_service_command_dispatch` @0x030682bc (0x03068340) | B | 0 | service mode |
| `call_teardown_tx_release_sequence` @0x03068538 (0x03068650) | B | 0 | TX release |
| `FUN_03054e70` @0x03054e70 = entry 0 of a thunk table at 0x030722B8 (entries are `set_global_upper_16bits`, 0x108 high-word write, 0x114 write, reg 0x78 write, 0x51 helpers: a factory/CPS calibration-item table) | B | calibration byte << 4 | only via the calibration table; no direct literal reference to the table base was found, so who selects entry 0 at runtime is unresolved |

Conclusions:
- No DAC write is channel-type dependent. Channel B = 0 in RX in both modes. Channel C is constant.
- B follows TX state and calibration (consistent with DS §8.1.3 "APC ... DAC_MCU outputs analog signals",
  and the two-point-modulation MOD1 control). C = 0x6E2 is the best candidate for a static analog bias
  (could be AF bias, VCM, or something else). The schematic is not available, so this is NOT established.
- DAC A is powered off by stock (PD_CTRL bit0 = 1). If the board's AF bias pin were A, it would be floating.
- Baseband DAC_CONTROL (0x11000070) is a different (TX modulation) DAC: `hr_c7000_modem_core_register_init`
  sets bit31 and clears bits 5 and 2. Unrelated to RX.

Other modem registers asked about:
- ADC_CONTROL 0x74: `|= 0x41C3` once in `hr_c7000_modem_core_register_init` @0x03023608 (bit14 offset code,
  bit8 enadc0, bit7 enadc1 cleared after, bit6 enref, bit1 adc_extcm, bit0 semode). Bits 8:7 toggle via
  `mmio_11000074_bits_7_8_set_clear` and `mmio_11000074_bits78_set_from_status_nibble` @0x0305ba70: enabled
  for BOTH nibble 0 and nibble 0x10, disabled for other types. No analog/digital difference.
- RF_MODE 0x104: init only (`|= 0x2000000 | 0x1000000 | 0x260000`, bits [6:5] = FUN_0300c2c8() & 3 = 3,
  bits [14:13] = FUN_0300c2c4() & 3 = 0, `|= 0x8000`, `&= 0x5FFFFFFF`). Per channel type only bit11:
  `modem_rf_mode_transition_apply` sets it for DMR relay mode 4 and clears it for other digital modes;
  analog does not touch it. Reading 0x03269060 is therefore consistent (bit12 is not written by stock).
- REG_0x168 / RF_RECV_DELAY: 0x14 / 14000 in init when RF type == 4 (this board), else 0x98 / 0.
  `radio_mode_transition_sequencer` @0x03021670 writes 0x98 / 0 only for mode >= 0xb. Same for analog and
  digital.
- AUDIO_CONTROL 0x80: bit0 (tx_voice_source), bit6 (fm_play_ctrl), bit7 (ring_play_ctrl) are tone / TX
  related; not changed by channel type.
- SIG_CENTER 0x108: `mmio_11000108_high_word_write_from_byte` @0x0302180c writes `b<<24 | b<<16` (TX I/Q
  centre) with the low 16 bits preserved. Only caller: `rf_config_load_from_flash_block2` @0x0300d568 (flash
  calibration block 2 offset 0x16) and the thunk at 0x03054e64. `recv_i_sig_center` (0x108[15:8]) and
  `recv_q_sig_center` (0x108[7:0]) are never written, so they stay at the reset value 0.
- RF_MOD_BIAS_CTRL 0x114: `mmio_11000114_low_word_duplicate_write` @0x03021830, TX amplitude, analog only
  (`freq_calibration_apply_all`), not an RX knob.

---------------------------------------------------------------------------------------------

## 3. Modem (HR_C7000) differences by type

`modem_rf_mode_transition_apply(type, mode, flag)` @0x0301e628, reached via `rf_mode_transition_on_freq_delta`
@0x03055f88 on every channel change:

| item | analog (type 0) | digital (type 1, relay 0/1/4) |
|---|---|---|
| CLK_MGR_REG0x2c | `|= 0xC0` (FM rx+tx clocks) | unchanged |
| call_setup_sm_state_aux byte | 1 | 0 (mode_0x1 = 0/1/4) |
| WORK_MODE (0x100) | `|= 0x80` (init 0x6E gives 0xEE) | `(v & 0x7E) | 4` or `(v & ~0x80) | 5` (bit7 cleared) |
| RF_CONTROL (0x110) | untouched | `|= 0x1500` (relay 0/1) or `0x1A00` (relay 4), then `|= 0x12` |
| RF_MODE (0x104) bit11 | untouched | cleared (relay 0/1) or set (relay 4) |
| LOCAL_CC | untouched | bit8 per `flags_0x18` |
| SYNC words, TDMA sync MSB set | written for both | written for both |
| FM_BANDWIDTH 0x11000500 | 0x5F | 0x5F (the "0 / 1" argument at 0x0305808c/0x03058094 is ignored; `modem_fm_bandwidth_write_0x5F` @0x0300c2cc stores 0x5F unconditionally) |

Also digital-only on a channel change: `write_byte_field_0300c3b4`, `mmio_11000384_low_byte_write`,
`flash_field_block67_signed_threshold_check`, `call_setup_sm_aux_field24_store` (colour code / slot setup).
Digital retune (IRAM 0x2b6d6) also disables a timer channel (`0x2cbd0(4)`, 0x14000000 timer) and the
GPIO interrupt of pin 0x3B (FD6818 GPIO_0, `gpio_interrupt_disable_bit` 0x2c5d8). Analog leaves both enabled.

The AF/ADC front end (ADC_CONTROL, RF_MODE[24] af_mode, [23:16] af_recv_ctrl = 0x26, RF_MODE[25]
phase_avg_en, RSSI/threshold registers) has NO type-dependent configuration.

---------------------------------------------------------------------------------------------

## 4. GPIOs

Complete labelled list from `PinMap_Baofeng_DM-32_14.06.26.xls` (sheet "GPIO", parsed with xlrd; every
unlabelled number omitted):

GPIO_A: 0x00 JTAG_TMS, 0x01 JTAG_TCK, 0x02 JTAG_TDI, 0x03 JTAG_TDO, 0x04 JTAG_RST_N, 0x05 ALPU-MP I2C_SCL,
0x06 ALPU-MP I2C_SDA, 0x07 Clock FD6818, 0x08 Data FD6818, 0x09 TXD GPS ATGM336H, 0x0F LCD BackLight,
0x10 Power check OFF, 0x11 Power key, 0x12 CS 25Q128 SPI0, 0x14 Clock 25Q128 SPI0, 0x15 MOSI 25Q128 SPI0,
0x16 MISO 25Q128 SPI0, 0x17 SPI1_CSN_0, 0x18 CS FD6818, 0x19 SDIO RDA5807M, 0x1A SCLK RDA5807M.

GPIO_B: 0x20 Red LED, 0x21 Green LED, 0x22 LNA VHF Control, 0x23 UHF PA Control, 0x24 LNA UHF Control,
0x26 Encoder rotary B, 0x27 Encoder rotary A, 0x28 Keyboard BackLight, 0x2B PTT Key, 0x2C GPS power control,
0x2E Audio path MIC Analog/DMR, 0x2F Switch audio PWM/FD6818, 0x31-0x34 Keyboard COL_1..4,
0x35 Audio PA control, 0x37 VHF PA Control, 0x3A GPIO_1 FD6818, 0x3B GPIO_0 FD6818.

GPIO_C: 0x42 LCD_NRESET, 0x43 LCD_CS, 0x44 LCD_RS, 0x45 LCD_WR, 0x46 LCD_RD, 0x47-0x4B Keyboard ROW 0..4 +
LCD_DB0..4, 0x4C-0x4E LCD_DB5..7.

There is NO pin labelled AF_out, RX audio to modem, ADC select, or AFOUT. The only audio-related GPIOs are
0x2E, 0x2F and 0x35 (plus 0x3A/0x3B, the FD6818's own GPIO0/1 outputs).

GPIOs that differ by channel type (`channel_change_apply_dispatcher` tail, objdump 0x03058098..0x030580b4,
and the factory screen at 0x030383e2-0x03038480):

| GPIO | analog | digital | notes |
|---|---|---|---|
| 0x2E Audio path MIC Analog/DMR | SET (1) | CLR (0) | label says MIC. Also set/cleared by `cps_serial_field_rw_dispatcher_variant` 0x0302b01a/0x0302b044 and `screen_enter_apply_channel_and_rf_mode` 0x030383e2/0x03038432/0x03038480. |
| 0x2F Switch audio PWM/FD6818 | SET (1) | CLR (0) | speaker source. The tone / DTMF code (`tx_gate_companded_audio_burst_send` 0x03022fd6 CLR / 0x0302303a SET, `tone_ptt_preamble_seq0` 0x0302251e CLR, `..._seq36` 0x03022e18 SET) clears 0x2F to play modem-codec tones then restores it only for analog. That is the evidence for "1 = FD6818 direct, 0 = codec PWM". |
| 0x35 Audio PA control | not type dependent | not type dependent | `audio_pa_enable_gate` @0x0301d6cc (SET at 0x0301d6e2, CLR at 0x0301d71a), with a software flag at RAM 0x301d738+1 bit1. Driven by tone / PTT / FM-radio state, not by the analog or digital RX path (no RX-squelch caller was found, so how the amp is enabled in steady analog RX is unresolved: it may be left on from an earlier state). |
| 0x22 LNA VHF / 0x24 LNA UHF | same | same | `antenna_switch_select_gpio_24_22(1)` @IRAM 0x2c878: 0x22 set and 0x24 cleared, or the reverse, by `radio_mode_state[0].flags_0x3` bit3 (frequency band from `bandwidth_deviation_threshold_apply`). Band dependent, NOT type dependent. Arg 0 (idle) clears both. |
| 0x3B FD6818 GPIO_0 | irq left enabled | irq disabled in retune | IRAM 0x2b6d6 |
| 0x20 / 0x21 LEDs, 0x23 / 0x37 PA | TX / indicator | | not RX audio |

So the ONLY two GPIOs that flip with channel type are 0x2E and 0x2F, both HIGH on analog, both LOW on digital.

---------------------------------------------------------------------------------------------

## 5. FD6818 REG_47H decode (FDG L4567 and pdftotext p.62)

Guide table (reset 0x6140):

| bits | name | meaning |
|---|---|---|
| [15] | reserved | |
| [14] | reserved | reset value 1 |
| [13] | afout_invert | 1 = invert AFOUT |
| [12:8] | afout_mode[4:0] | 0x10 = MUTE, 0x11 = RX AFOUT, 0x15 = RX ALARM TONE, 0x18 = BEEP / TX side tone (CTCSS/CDCSS not included) |
| [7:4] | reserved | reset value 4 |
| [3:0] | dig_gain_tx | digital gain after MIC ADC, 1 dB/step (TX only) |

Decode of the stock values:

| value | [15:14] | invert | afout_mode | [7:4] | dig_gain_tx | used for |
|---|---|---|---|---|---|---|
| 0x6042 | 01 (bit14 = 1) | 1 | 0x00 | 4 | 2 | stock analog RX/idle |
| 0x6142 | 01 | 1 | 0x01 | 4 | 2 | stock analog "audio on" (IRAM 0x2b51c RMW) |
| 0x0140 | 00 | 0 | 0x01 | 4 | 0 | stock digital RX |
| 0x0100 | 00 | 0 | 0x01 | 0 | 0 | boot default in stock |
| 0x6140 | 01 | 1 | 0x01 | 4 | 0 | guide reset value AND guide's DMR-bypass RXON ("FM demod direct to AFOUT", FDG L1792) |
| 0x6740 | 01 | 1 | 0x07 | 4 | 0 | guide's DMR-bypass TXON ("MIC ADC directly to TX", L1784); stock TX and 108-136 MHz window |
| 0x1100 \| ... | | | 0x11 | | | guide's "set RX AF output" RMW (L1146): `(REG_47 & 0xF0FF) | 0x1100` |

What the modes mean (documented vs inferred):
- DOCUMENTED: 0x10 mute, 0x11 RX AFOUT, 0x15 alarm tone, 0x18 beep. Only values with bit 4 set are named.
- DOCUMENTED BY USE: the guide itself writes 0x01 (0x6140, reset default) for "FM demod direct to AFOUT"
  and 0x07 (0x6740) for "MIC ADC direct to TX". Mode 0x01 therefore is the discriminator tap (DISC, before the
  audio post-processing: 300 Hz HPF, LPF, de-emphasis, expander, volume). The chip's default after reset is
  this tap, with no 0x47 write needed.
- STOCK USES 0x00 on analog, which the guide does not document at all. It is NOT the mute value (0x10) and
  the speaker is audible with it (reported), so mode 0 is some form of "normal RX audio" output.
  INFERENCE (unverified): bit4 = "forced source select", bits[3:0] = source, mode 0x00 = chip-controlled
  default audio path (post-volume, squelch-gated). 0x01 = raw DISC. 0x11 = forced RX audio.
- afout_mode 0x11 is never written by stock; stock only produces nibble 1 (0x2b51c) with bit12 unchanged
  (so 0x6142 not 0x7142).

Other registers on the AFOUT path (guide):
- REG_03H bit9 afout_en = 1 enables the AFOUT DAC. Stock's wire-0x30 value 0xBFF1 has bit9 = 1 (RX on);
  0xC3FA (tone playback) has it set; 0xC1FE (TX) does not. Identical for analog and digital RX.
- REG_23H[3:0] dac_vgain = "DAC maximum output level", reset 0x8F8F (so 0xF = max). Guide recommends max.
  Stock never writes wire 0x23. So AFOUT full-scale is the same in both modes.
- RX volume: REG_53H audio_rx_gain_sh [15:13] (0 = max, 7 = min, 6 dB/step) and audio_rx_gain [9:5]
  (0 = mute, 31 = max). Stock's only 0x53 write (0xE678 = sh 7, gain 19) is in
  `fd6818b_pa_power_level_select(1)` @0x0300e68c which has no caller with 1, so this is dead code and 0x53
  keeps its reset value. Volume must be applied elsewhere (not found among immediates).
- REG_96H audio_rx_gain1 [13:8] (DISC gain), REG_44H[12:11] fmdem_gain (0-18 dB after FM demod), REG_9BH
  bypass bits (HPF300 [9], LPF1 [8], LPF2 [7], de-emphasis [6]). Stock never writes wire 0x96 / 0x9B (page 1
  via REG_7F), and its writes to wire 0x44/0x45 are in the dead code `FUN_0300e9f8` (no caller).
  Note that the guide defines 0x44 default 0x43EC = fmdem_gain 0 dB.
- REG_7EH (reset 0x341E): [7] dcc_tx_bypass, [6] dcc_rx_bypass, [5:3] dcc_tx_bw. Stock analog 0x302E
  (bit7 = 0, bit6 = 0, [5:3] = 101), digital 0xB03F (bit7 = 0, bit6 = 0, [5:3] = 111 = 15 Hz). The RX DC
  filter is enabled in both; bits 15, 13, 12 of the digital value are reserved in the guide.
- Guide's own DMR-bypass recipe (FDG L1759-1796) for comparison with stock digital: 0x04 = 0x0300,
  0x3A = 0x0040, 0x43 = 0x7E08, 0x48 = 0x6C5C, 0x4B = 0xE442, 0x73 = 0x569A, 0x7E = 0x341E | 1<<7,
  0x47 = 0x6140, 0x03 = 0xBFF1. Stock digital differs (0x43 = 0x4048, 0x48 = 0xBBBF, 0x7E = 0xB03F,
  0x47 = 0x0140), so stock is NOT a verbatim copy of the guide's recipe. 0x4B and 0x73 are not written by
  stock at all.

Multiple audio output pins: NO evidence in the guide. The block diagrams (FDG L120-175, L750-775) show one
"DAC -> AFOUT" per path; the only analog audio pins are AFOUT (out) and MICIN (in). Digital audio can leave
the chip over I2S/MCBSP (gpioX_out_sel 12 / 13 / 14 = DOUT / DFS / DCLK, REG_26H / REG_27H), and the GPIOs
can output SQ (2), VOX (3), CTCSS/CDCSS compare (4) and code (5), INT (1) and XTAL/2 (15). Stock does not
write wire 0x26 / 0x27 (reset 0x0005 / 0x3200; only 0x25 = 0xC1BA is written, in
`fd6818b_default_register_init`), and the pin map shows only GPIO_0 / GPIO_1 routed (0x3B / 0x3A).

---------------------------------------------------------------------------------------------

## 6. HR_C7000 recommended AF_out -> ADC interface

Primary text: DS §8.2.3.1 (L18365-18400), DS-zh §8.2.3.1 (L14815-14836), UM22 §5.2.1 (L4731-4765),
ADC_CONTROL (DS L6140-6150), pin descriptions (DS L2640-2690).

- Figure 64 / 5.3 is a block diagram only: `RDA1846S AF_out -> HR_C7000`, with LNA, a low-pass, the 26 MHz
  VC-TCXO and the I2C control link. It shows NO components. There is no series capacitor, no resistor values,
  no DC level and no swing stated anywhere in the three documents. "AC-coupled or DC-coupled" is NOT answered
  by the vendor documents.
- Text, v2.7 (en and zh): "AF signal bias: a DAC on the CPU bus provides a bias point, configured to the N
  end of the AF-receive differential ADC; the AF signal end connects to the ADC's P end." ADC_CONTROL
  adc_semode = 1 (single-ended): "VINP is the signal source; in AF mode VINM and VCM are tied to a fixed level
  (the AF bias); in IF mode they are tied to GND". adc_extcm = 1 takes the common mode from the external
  VCM pins. Stock sets semode = 1 and extcm = 1.
- Text, UM v2.2 (older): "AF signal bias is adjusted by register SIG_CENTER (receive IQ bias)". SIG_CENTER
  [15:8] recv_i_sig_center and [7:0] recv_q_sig_center, 0-255, reset 0 (DS §9.2.2.3). The older manual
  therefore has a digital, register-controlled receive offset. The two statements are not exclusive: the
  external DAC sets the analog bias point, SIG_CENTER may trim the digital offset.
- Gain: RF_MODE[23:16] af_recv_ctrl, 0-255 = 0-15.94x, 1/16 step. Stock 0x26 = 2.375x (a DMR tuning).
  Use PHASE_OUT to set it: at -47 dBm and 2.749 kHz deviation (+-3 sigma) the phase span should be about
  -1300 .. +1300.
- Expected DC and swing: NOT specified in volts. Facts available: the ADC pins are in the 3.3 V domain;
  DAC output range 0-3.3 V for 12 bit (3.3/4096 = 0.8 mV/LSB); stock's one DAC value is 0x6E2 = 1.42 V.
  `RSSI_BIAS_VALUE[15:8] adc_bias_i` is the 8-bit DC offset detector, mid scale 128 in offset-code mode.
- Pin trap: the pin table (DS L2640-2690) is machine-translated and inconsistent: it says ADC_VINCM_I (P2) is
  "the signal input terminal in single-ended mode" and ADC_VINP_I (N2) "is connected to analog ground in
  single-ended mode", while ADC_CONTROL says VINP is the signal. Which physical pin the DM-32 AF net goes to is
  a board question.
- Can a DC-level mismatch saturate the ADC? Yes, and the evidence is compatible with it: the ADC reads
  signal minus bias in single-ended mode, and a measured I-channel DC at 234/255 is 17% of the positive half
  away from full scale. A speaker-level
  AFOUT swing superimposed on that would clip the positive side, and a hard-clipped or railed ADC gives a
  nearly constant phase value. Nothing in the documents rules out an AC-coupled input; if it is AC-coupled
  the 234 would be the bias network alone and the problem is the AC amplitude, not the DC.

---------------------------------------------------------------------------------------------

## 7. Ranked hypotheses and the single reversible experiment for each

Ground rules for all experiments: read-modify-write, RX only, never key PTT during a test (stock TX handlers
rewrite REG 0x47 = 0x6740, GPIO 0x2E/0x2F and the DAC on their own); keep the DAC and FD6818 restore values in
a small table before touching anything. FD6818 read primitive: `fd6818_reg_read` @IRAM 0x2c33c (r0 = reg),
write: `fd6818b_reg_write` @0x2c2f4 (r0 = reg, r1 = value). Each experiment observes
`0x1100011c` (rssi_value [31:16], adc_bias_i [15:8], adc_bias_q [7:0]) AND `0x11000128` (PHASE_OUT) with the
same 1 kHz tone as before, and logs both with the squelch open and with the tone removed.

### E0 (no writes): the missing comparison
- Read 0x1100011C, 0x11000128, 0x11000104, 0x11000074 and FD6818 reg 0x47 / 0x48 / 0x7E / 0x2B on a DIGITAL
  channel (receiving DMR if possible, otherwise idle), then on analog with the tone, then analog with squelch
  closed.
- Decision: if digital shows adc_bias_i near 128 (centred) and analog 234, the DC level differs by mode:
  H1 or H2 below. If both read about 234, the DC is a board constant and the difference is AC amplitude:
  H1 (raw DISC amplitude) or H4. If rssi_value changes with tone on analog while PHASE_OUT stays flat,
  PHASE_OUT is just not updating in FM mode (caveat 0.6) and the ADC may already be seeing the AF.
- Risk: none.

### H1 (most likely, about 40%): FD6818 AFOUT tap / processing differs (REG 0x47 mode 0 vs 1, plus the 0x48 / 0x7E / 0x2B set)
Why: this is the largest real analog/digital difference, it sits exactly on the audio path, and the digital
value equals the chip's reset / "FM demod direct to AFOUT" tap while analog uses a tap the guide never
documents. On the raw DISC the DC level carries the carrier frequency offset and the amplitude is fixed by
fmdem_gain / audio_rx_gain1; on the post-processing tap the DC and level are set by other stages (HPF300,
expander, volume, squelch gate), so both the DC (adc_bias_i) and the AC can differ.
- E1: write REG 0x47 = 0x6142 (afout_mode 1, exactly the value stock itself produces with IRAM 0x2b51c in RX).
  Observe 0x11C / 0x128. If nothing changes try E1b: 0x0140 (digital value, invert off).
- E1b (full digital set): read-and-save then write 0x48 = 0xBBBF, 0x7E = 0xB03F, 0x2B = 0x0700, 0x47 = 0x0140
  (and 0x37 = 0x1F1F is NOT needed, it only changes sub-band filtering). This reproduces the digital
  configuration of the audio block on an analog channel.
- Restore: write back the values read first (expected 0x47 = 0x6042 or 0x6142, 0x48 = 0x31E0|sel,
  0x7E = 0x302E, 0x2B = 0xC000). Any analog channel change also rewrites the whole analog set
  (0x300f1b4 re-applies only when its cache byte at `PTR_DAT_0300f254[2]` differs, so also rely on the explicit
  restore, or toggle the channel to a different type and back).
- Risk: speaker audio is the raw DISC (louder, no de-emphasis, CTCSS audible, polarity change if invert
  toggles) during the test; with E1b the receive audio is likely distorted or muted. No TX effect. 30 seconds.

### H2 (about 25%): receive-I offset never programmed (SIG_CENTER recv_i = 0) so the DC is near the rail
Why: `adc_bias_i` = 234 is a direct measurement of the problem class; the v2.2 manual names this register as the
AF bias adjustment; stock programs only the TX half; the DMR demod path may tolerate it (4FSK with long-term
DC removal) while FM audio does not.
- E2: read-modify-write 0x11000108 bits [15:8] only (mask 0xFFFF00FF; keep [31:16] TX halves and [7:0]).
  Start at 0, then 0x20, 0x40, 0x60 ... and watch adc_bias_i. Target about 128 (offset-code mid scale) and
  PHASE_OUT swing. Whether adding or subtracting moves it toward 128 is the test; stop at the first
  setting that centres it.
- Restore: write bits [15:8] back to 0x00 (the reset value; stock never writes it).
- Risk: none for TX (TX bias is in [31:16], preserved by the RMW). Only RX offset moves. A wrong direction
  just pushes adc_bias_i further into the rail, so step and look.

### H3 (about 10%): AF bias DAC level (DAC C = 0x6E2) is wrong for the analog AF swing
Why: it is the only analog bias candidate; it is constant, so it can only matter in combination with H1 or H2
(it does not explain the type difference by itself).
- E5: `dac_channel_write_12bit(4, v)` @IRAM 0x2c388 (r0 = 4, r1 = v) with v = 0x6E2 +/- 0x80 steps, watching
  adc_bias_i. Restore: `dac_channel_write_12bit(4, 0x6E2)`.
- Risk: DAC C is unidentified. DS §8.1.3 uses DAC_MCU outputs for APC and two-point-modulation MOD1 (crystal
  control). If C feeds the TCXO / VCO the RX frequency moves, no TX is involved. Small steps, short dwell.
  Do NOT power DAC A (PD_CTRL bit0) or write DATA_A: A is off on purpose and may be driven from outside.

### H4 (about 10%): GPIO 0x2F (and/or 0x2E) loads or reroutes the AF net on analog
Why: only two GPIOs flip with type, both HIGH on analog. 0x2F = 1 connects the FD6818 AFOUT to the speaker amp
input (low impedance?) and, if the modem branch taps AFOUT after the switch or through a resistor, that
loading cuts the AC to the ADC. 0x2E is labelled MIC (TX side) but is the second analog/digital control.
- E3: `gpio_clear_output_bit(0x2F)` @IRAM 0x2c544 (r0 = 0x2F). Speaker goes silent (codec PWM carries
  nothing). Observe 0x11C / 0x128. Restore `gpio_set_output_bit(0x2F)` @0x2c500.
- E4: same for 0x2E (clear, then set). Restore as above.
- Risk: only the speaker path. No TX effect while not keyed. Do not leave either low while keying.

### H5 (about 10%): digital-only demod front end settings (0x48 CIC, 0x7E, 0x2B) change the AFOUT level
Subsumed by E1b; if E1 fails but E1b works, bisect the four registers (write the digital value for one
register at a time, restore after each).

### H0 (not a hardware cause): PHASE_OUT does not run in FM mode
Covered in E0 by `rssi_value`. If the AF already reaches the ADC, no change is needed and the FM RAM probe in
`rx-audio-fm-mode.md` should be run as is.

### Low-probability items checked and ruled out by the static data
- GPIO 0x35 (audio PA) is not a type-dependent switch (section 4).
- LNA GPIOs 0x22 / 0x24 are band-selected, not type-selected.
- ADC_CONTROL, RF_MODE, REG_0x168, RF_RECV_DELAY, AUDIO_CONTROL, FM_BANDWIDTH are the same for both types.
- afout_en (bit9 of wire 0x30) and dac_vgain (wire 0x23) are the same for both.
- No second FD6818 audio output pin exists in the guide (section 5).

---------------------------------------------------------------------------------------------

## 8. Open items and risks to this analysis

1. Wire 0x30 vs guide REG_03H, and the undocumented registers 0x13 / 0x2B / 0x32 / 0x37 / 0x48-0x4A: the
   FD6818B in this radio may differ from the FD6818 in the guide. Values written by stock were used as
   facts; semantic decodes of undocumented fields are inference.
2. The meaning of afout_mode 0x00 is inferred (section 5).
3. The channel-change dispatcher's disassembly in `meta/disasm.jsonl` is desynchronised at 0x030579d8 and
   at 0x03057f6e..0x03057f8c; the GPIO calls were verified in a fresh objdump of the flash image.
4. `FUN_03054e70` / the DAC-B thunk table at 0x030722B8 has no direct reference; its runtime selector is
   unknown.
5. RAM byte at 0x3ecfd (`sel` in reg 0x48 = 0x31E0|sel) was not resolved to its source.
6. No analog RX audio-enable path (GPIO 0x35 + REG 0x47 nibble 1 + AUDIO_CONTROL bit6) was found in the
   squelch-open handling. Only tone / PTT / FM-radio paths call `audio_pa_enable_gate`. Whether the
   caller's observed 0x6042 is the idle or the active state in steady analog RX is therefore unconfirmed.
