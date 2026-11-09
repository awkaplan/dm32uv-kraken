# Analog RX audio as PCM via the HR_C7000 "FM mode": static findings and probe recipe

Update 2026-11-02: the capture path described here is also used at standby by the F14 RX Monitor (`docs/aprs-rx-monitor.md`): GPIO 0x2F low plus AUDIO_CONTROL bit 6, driven from a hook in the stock background loop.

Status: STATIC research only (vendor datasheet + stock firmware RE). Nothing here has been run on
a radio. Every "Expected" below is a prediction the probe must confirm.

Goal: let patch code read demodulated analog FM RX audio as PCM so it can decode 1200-baud AFSK
APRS.

Evidence rules: vendor datasheet and stock-firmware RE only. The OpenRTX DM-32 port is an
experimental, unpublished port and was not used as evidence.

Sources:
- DS = HR_C7000 datasheet v2.7 (English). Cited as "DS L<line>" (line of a text conversion of it) plus section and page.
- zh = the Chinese HR_C7000 datasheet v2.7 and HR_C7000 User Manual v2.2 (96 pages).
- FW = stock firmware 1.01.048, the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) decompiled corpus `src/`
  plus `meta/disasm.jsonl`. Addresses are flash (0x03xxxxxx).
- Modem base = 0x11000000 (DS L22416 "Base Address 0x1100_0000").

---------------------------------------------------------------------------------------------

## 0. Headline

1. Stock ALREADY runs the HR_C7000 in FM mode on every analog channel. Evidence:
   - `modem_rf_mode_transition_apply` @0x0301e628, branch `param_1==0` (analog), does
     `CLK_MGR_REG0x2c |= 0xc0` and `WORK_MODE |= 0x80`.
   - `radio_mode_transition_sequencer` @0x03021670 writes `WORK_MODE = 0x80`.
   - `rf_mode_transition_on_freq_delta` @0x03055f88 calls the apply function with channel-type
     nibble `flags_0x8>>4`, which is 0 for analog. It runs on every channel change.
2. Stock never reads the FM output. A full scan finds no access to FM_SQL 0x508,
   FM_DECODE_FLAG1/2 0x564/0x568, FM_ADDR_SW 0x56c, SYS_INTERP_LIST 0x398 or SYS_INTERP_CLEAR
   0x3a0. It also never reads the FM RX voice halves 0x160004e0..0x160008df. Details in 3.6.
3. FM RX RX-RAM recording is documented as ON BY DEFAULT in FM mode (DS L18118-18126, §7.2.2.3.2
   p.478). So on an analog channel, a probe may need ZERO register writes. It only has to read
   0x1100056c and 0x160004e0..0x160008df.
4. The input is NOT I/Q or IF on this board. The firmware programs the "AF mode" input
   (RF_MODE[24]=1), so the modem ADC takes the FD6818 AF_out signal. Confidence is high for the
   configuration and medium for the exact spectrum of the PCM (see 1.4).
5. Speaker on an analog channel = FD6818 direct (GPIO 0x2F = 1). The modem FM output goes to the
   codec DAC but the codec PWM path is not selected, so a read-only probe does not disturb audible
   audio.

---------------------------------------------------------------------------------------------

## 1. What FM-mode RX takes as input, and what comes out

### 1.1 Input (answer to Q1)

Datasheet:
- The receive RF interface is "differential input, supports baseband IQ, IF and AF" (DS L1692-1693,
  §2.2.8 p.36).
- AF mode is selected by `RF_MODE[24]` (`af_mode`). `RF_MODE[23:16] af_recv_ctrl` is the gain,
  `amplitude = in * af_recv_ctrl/16`. `RF_MODE[25] phase_avg_en` is AF averaging (DS L6340-6365,
  §4.6.5.8 p.152-154).
- §8.2.3.1 "AF take over" (DS L18365-18400, p.485), reference circuit Fig 64:
  `RDA1846S AF_out -> HR_C7000`.
  - "the signal entering HR_C7000 is a frequency-modulated signal; gain and offset are adjusted
    inside the chip".
  - `PHASE_OUT` (0x128) reads back the AF phase amplitude; about +-1300 at 2.749 kHz deviation
    with -47 dBm in.
  - The CPU DAC supplies the AF bias.
  - The v2.2 manual §5.2.1 / Fig 5.3 (zh md L4731-4760) says the same.
- ADC_CONTROL `adc_semode` (bit 0): 1 = single-ended, VINP carries the signal, and "in AF mode
  VINM and VCM are connected to a fixed level (AF bias)" (DS L6140-6150, §4.6.5.2 p.148).
- FM-mode data flow (Fig 60 text, DS L16696-16706, §6.4.3.3 ~p.437): "the RF receiving signal is
  in MODEM internally completed FM demodulation, the output audio data is directly given to
  CODEC DAC".
- §7.2.2.2 p.477 (DS L18090-18101): "FM Receive the demodulated audio data output to the built-in
  CODEC DAC port; data flow does not require CPU to migrate".
- In AF mode the "phase" fed to the FM demodulator is the AF signal itself.
  - UNVERIFIED reading: the FM block then does filtering, de-emphasis, squelch and sub-tone on
    that.
  - The datasheet never says explicitly how AF input interacts with FM mode.

Stock firmware confirms AF input is what the board uses. `hr_c7000_modem_core_register_init`
@0x03023608:
- `FUN_0300c2c4()` is the constant 4 ("RF type"), so
  `RF_MODE |= 0x2000000 | 0x1000000 | 0x260000`.
  - That is phase_avg_en, af_mode, and af_recv_ctrl = 0x26 (2.375x).
  - `REG_0x168 = 0x14` and `RF_RECV_DELAY = 14000` are set in the same branch.
- `RF_MODE[14:13] = 0` (IF field, inert once af_mode=1). `[6:5] = 3` (two-point TX).
  `RF_MODE |= 0x8000`.
  - `RF_MODE &= 0x5fffffff` at the end clears bits 31 and 29.
- Predicted RF_MODE in steady state: `0x0326_8060` plus reset-default bits. Bit 11 (dual PHY) is
  set only by DMR relay mode 4.
- `ADC_CONTROL |= 0x41c3`: bit14 offset code, bit8 enadc0 (I), bit7 enadc1 (Q), bit6 enref, bit1
  extcm, bit0 semode single-ended.
- `FM_DEV_COEF = 0x33500`: fm_dev_coef_r = 0x35, tr_sig_lim = 3, fm_dev_coef_t = 0.
  - Stock programs the FM RX deviation coefficient, so the FM RX chain is expected to be live.
- `modem_fm_bandwidth_write_0x5f` @0x0300c2cc writes `FM_BANDWIDTH(0x500) = 0x5f` unconditionally.
  - That is fmbpfon=1, preemph=1, bandwidth=1 (25k), bandwidth_adj=1, bandwidth_r=1 (25k),
    bandwidth_ctc=1 (DS L22520-22545, §11.2.2.2 p.593-594).
  - The disassembly is `movih r3,0x1100; bseti r3,0xa; movi r2,0x5f; st.w r2,(r3,0x100)`, so the
    stores go to 0x11000500, not 0x11000100. Offset-0x500 style registers are reached through a
    0x11000400 base, which is why a naive `(0x11000000, off)` scan misses them.

Physical wiring: the vendor pin map lists no explicit "AF_out" net. The strongest evidence is
that DMR 4FSK demod on this board needs AF_out -> ADC (af_mode is set for DMR too).
- The FD6818 register 0x47 "AF out" functions are `fd6818b_reg47_*` @0x0002b51c, 0x0002b550,
  0x0002b584. They are used for TX tone injection.
- Treat "AF_out feeds the modem ADC on analog channels too" as HIGH (same pin, same chip, ADC
  enabled, see 3.3). The PCM spectrum is MEDIUM.

### 1.2 Processing in the FM block (DS §11, p.590-610)

- SQL: `FM_SQL` 0x508[15:0] RO. Higher means no valid in-band signal. On by default (DS L23094,
  §11.5 p.608).
- Sub-tone: CTCSS / CDCSS / DTMF / 2-tone / 5-tone / XTCSS detect and generate; detection
  results are in 0x564/0x568 (DS L22421-22470, §11.1-11.2).
- Tone coefficients use a 32 kHz sampling clock (USER_DEFINE_CTC: "frequency / 32000 Hz", DS
  L22545 area).
- Features: emphasis and de-emphasis (DS L1654, §2.2.4 p.35). FM_BANDWIDTH has an FM bandpass
  filter enable (bit 6) and a pre-emphasis switch (bit 4).
- The datasheet does not give the audio filter corner frequencies or a statement of whether
  de-emphasis is applied to the CPU-visible RX RAM copy. Both are open (section 7).

### 1.3 PCM format

- RAM: depth 1200 bytes, data width 8 (DS L23060, §11.4 p.608). Each FM voice half is 512 bytes
  = 256 16-bit PCM samples (DS L23066-23090, Tables 57/58).
- Sample rate: 256 samples = 32 ms (DS L18082, §7.2.2.1.2: "256 16-bit audio data (32 ms audio
  playback length)"), so 8 kHz. This is inferred from that sentence about the TX RAM. The RX RAM
  uses the same 256 / 512 B structure.
- Byte order: "low address stores the high 8 bits of the PCM audio data" (Table 58; the v2.7 English
  text is garbled for Table 58, but the zh-CN Table 58 reads 低位地址存放高8bits, i.e. high byte
  first = big endian). So `s = (int16)((b[2k] << 8) | b[2k+1])`.
- Signedness: not stated. Presumably two's complement 16-bit (it feeds the codec DAC parallel PCM
  interface). The probe tests both interpretations (offset binary has mean about 0x8000).
- RX RAM base 0x160004b0. Halves start at base + 0x30 and base + 0x230:
  - FM_VOICE_1 = 0x160004e0 .. 0x160006df
  - FM_VOICE_2 = 0x160006e0 .. 0x160008df
  - This matches the §7.2.2.3.2 statement "RX RAM 0x1600_04E0~0x1600_08DF" (DS L18121-18125,
    p.478).
- Access: byte reads. Stock only ever uses `ld.b` / `st.b` on the 0x16000000 region (e.g.
  `copy_bytes_from_offset_table` @0x0300d41c, `FUN_030c6780` @0x030c6780). Do not use ld.w or
  ld.h until proven.

### 1.4 What the PCM could look like

Because the input is the FD6818's own AF_out, the PCM is presumably the chip's audio after the
FD6818's demod filtering (it may include the FD6818's de-emphasis and its 300 Hz high-pass for
sub-tone removal), then HR_C7000 scaling by `fm_dev_coef_r` and any FM-block filters. APRS 1200/
2200 Hz tones lie in the normal voice band, so this should be fine. The 300 Hz-3 kHz skirts
matter; the probe measures the 1200/2200 Hz relative level.

---------------------------------------------------------------------------------------------

## 2. Enabling FM-mode RX (answer to Q2)

All offsets are from 0x11000000.

| Item | Register / bit | Value for FM RX | DS cite | Stock |
|---|---|---|---|---|
| FM mode | WORK_MODE 0x100 bit7 `modulator_mode` | 1 (FM analog); bit8 `digital_analog_en` = 0 | DS L22472-22520 §11.2.2.1 p.592; Table 27 L5808 "FM = 0x80" p.140 | analog channel sets bit7 (apply @0x0301e628; sequencer @0x03021670 writes 0x80) |
| FM clocks | CLK_MGR_REG0x2c 0x2c bit7 `modem_clk_fmrx_en`, bit6 `modem_clk_fmtx_en` (modem rx/tx are bits 9/8) | 1 | DS L3873-3877 §4.2.5.11 p.86-87 | `|= 0xc0` in apply @0x0301e628; boot `0xffffffff` @0x030043c8; but `crystal_osc_and_timer5_init` @0x03054a6c writes literal 0xfff0ff3c (0x03054bb4), bits 7:6 OFF |
| FM block out of reset | SYS_SOFT_RSTN 0x00 bit2 `fm_soft_rstn` (low active) | 1 | DS L3233 §4.1.4.1 | `register_init` writes 0x190 (bit2=0, held in reset), then sequencer writes 0xfffffffc (bit2=1) |
| ADC on | ADC_CONTROL 0x74 bit8 `enadc0`, bit7 `enadc1`, bit6 enref, bit0 semode | `0x41c3` base plus bits 8:7 = 1 | DS L6080-6150 §4.6.5.2 p.146-148 | register_init sets `|=0x41c3` then clears 8:7. `mmio_11000074_bits_7_8_set_clear(1)` (`|=0x180`) @0x0301e578 is called through `mmio_11000074_bits78_set_from_status_nibble` @0x0305ba70 when the channel-type nibble is 0 (analog) or 1 (digital); else ADC is turned off |
| AF input | RF_MODE 0x104 bit24 `af_mode`, [23:16] `af_recv_ctrl`, bit25 | 1 / gain / 1 | DS L6313-6420; L18365-18400 | `0x0326_8060`-ish, see 1.1. Gain 0x26 is a DMR-tuned value; unknown whether FM level wants a different one |
| Audio path | AUDIO_CONTROL 0x80 bit0 `tx_voice_source` | 0 (codec DAC path) | DS L18097 §7.2.2.2; L6195 §4.6.5.3 p.148-150 | register_init/reinit clear bit0 |
| Codec DAC | codec 0x160009C8 bit5=0 (master), bit4=0 (on), [1:0]=00; CR_DAC `0x160009cd` bit4 (sb_dac)=0, bit7 (soft mute)=0 | per DS | DS L18095-18101 | `hr_c7000_modem_reset_sequence` @0x03021b80 stores 0xC0 to 0x160009c8 (master, enabled, parallel PCM), clears 0x160009cd bit4 and bit7. Codec is powered (SB/SB_SLEEP cleared) |
| RX recording | none | "enabled by default" in FM mode | DS L18118-18126 §7.2.2.3.2 p.478 | n/a |

FM_ADDR_SW (0x56c) (DS L22996-23008, §11.2.2.28 p.606):
- `[1:0] RO`, reset 0. 1 = "CPU reads or writes the LOW address data" (FM_VOICE_1), 2 = "HIGH
  address data" (FM_VOICE_2). The wording means "the half the CPU should be using now" (the
  other is the one the hardware is filling). Semantics are INFERRED from the ping-pong text;
  the probe verifies them empirically (4.3).
- It is a plain read-only status register. Polling it needs no interrupt. The datasheet never
  says the FM RX interrupt must be acknowledged for the engine to continue (open question).

SYS_INTERP and FM_RX_INTERP:
- `FM_RX_INTERP` = SYS_INTERP_LIST bit14 (also bit15 FM_TX, bit13 FM_SIG_RX). Table 28 items 11-13
  (DS L5979-6000, p.144); LIST 0x398 bits (DS L6448-6449 p.156). Level triggered, high active.
  Masked via SYS_INTERP_MASK 0x39c bit14 (0 = masked, 1 = enabled; reset default 1 = enabled
  per DS L6511). Cleared via SYS_INTERP_CLEAR 0x3a0 (write 1 to clear).
- Stock mask values: 0x1187f at init (@0x03023608, store at 0x030236fc) = bits 16,12,11,6..0.
  `0x1800` in `screen_enter_apply_channel_and_rf_mode` @0x03038058 and in the CPS serial
  dispatcher variants. `0` in `hr_c7000_modem_core_reinit_sequence` @0x030c65f8. In every case
  bits 13/14/15 are 0 (masked). So stock never takes an FM interrupt, and the stock
  `System_inter` handler is never exercised with FM bits. A patch must keep bits 13..15 clear.
- Whether the raw bit14 in SYS_INTERP_LIST toggles while masked is unknown (LIST may be raw or
  masked). The probe may read it as a bonus (read-only); a raw bit toggling every 32 ms is
  strong evidence the engine runs.

---------------------------------------------------------------------------------------------

## 3. What stock does on an analog channel (answer to Q3)

### 3.1 Modem mode

Analog / digital is chosen by channel-type nibble `flags_0x8 & 0xf0` (0 = analog, 0x10 = digital;
see `channel_change_apply_dispatcher` @0x030573e8 which tests `(uStack_54 & 0xf0) == 0`).

Analog (nibble 0):
- `modem_rf_mode_transition_apply(0, ...)` @0x0301e628:
  - `CLK_MGR_REG0x2c |= 0xc0` (FM tx+rx clocks on)
  - `*(p_call_setup_sm_state_aux) = 1` (analog flag byte)
  - `WORK_MODE |= 0x80`
  - writes the sync-word registers
  - ends with `WORK_MODE = v`, `LOCAL_CC = v2`
- `radio_mode_transition_sequencer(mode)` @0x03021670: for modes outside 1..4 and < 0xb it writes
  `WORK_MODE = 0x80` (FM); `modem_fm_ptt_set(0)` is called afterwards (FM_PTT = 0, RX).
  - Modes 5..7 call `modem_fm_ptt_set(1)`; mode 9 calls apply(0,0,1).
  - `SYS_SOFT_RSTN = 0xfffffffc` is written first: FM and audio resets released, bits 1:0
    (phy/protocol) held in reset.
- `modem_fm_bandwidth_write_0x5f` -> FM_BANDWIDTH = 0x5f.
- `radio_init_sequence` / `status_byte_bit5_set_clear_wrapper(0,1)` = codec bit toggles
  (`0x160009cb` bit5).

Digital (nibble 0x10): WORK_MODE `= (v & 0x7e) | 4` (or `(v & ~0x80) | 5` when param_3) i.e.
bit7 clear. WORK_MODE init value in register_init is 0x6e (L2 relay TS2, Table 27).

### 3.2 GPIO audio-path switches

The vendor pin map spreadsheet `PinMap_Baofeng_DM-32_14.06.26.xls`
(parsed with xlrd, sheet "GPIO"):

| GPIO | Label |
|---|---|
| 0x2E (GPIO_B 0x2E) | "Audio path MIC Analog/DMR" |
| 0x2F (GPIO_B 0x2F) | "Switch audio PWM/FD6818" |
| 0x35 | "Audio PA control" |
| 0x07 / 0x08 / 0x18 | FD6818 clock / data / CS |
| 0x3B / 0x3A | FD6818 GPIO_0 / GPIO_1 |

Stock usage (`channel_change_apply_dispatcher` @0x030573e8 L474-489; also
`screen_enter_apply_channel_and_rf_mode` @0x03038058):
- Analog channel: `gpio_set_output_bit(0x2e)` and `gpio_set_output_bit(0x2f)`, so both HIGH.
  - 0x2F = 1 therefore selects the FD6818 direct output path to the speaker amp (since DMR /
    codec PWM clears it, and the tone playback in `tx_gate_companded_audio_burst_send`
    @0x03022fac clears 0x2F to use the codec PWM, then re-sets 0x2F only
    `if (flags_0x8 & 0xf0) == 0`, i.e. restores it on analog).
  - 0x2E = 1 = MIC analog path.
- Digital channel: both cleared (codec PWM path, DMR mic path).
- Speaker amp: GPIO 0x35 via `audio_pa_enable_gate` @0x0301d6cc.
- INFERENCE (label vs behaviour): "1 = FD6818" on 0x2F is inferred from the restore-on-analog
  pattern, not from a schematic.

### 3.3 Is the modem RX ADC powered/clocked on analog?

Yes. `mmio_11000074_bits78_set_from_status_nibble` @0x0305ba70 enables ADC_CONTROL bits 8:7 when
nibble == 0 or 0x10, disables otherwise. It is called from `ui_event_notify_alt` @0x030305ac,
`channel_change_apply_with_zone_and_flash_validate` @0x03058180, and
`event_flags14_dispatch_hw_and_flaggrp` @0x0305edfc.
`screen_enter_apply_channel_and_rf_mode` calls `mmio_11000074_bits_7_8_set_clear(1)` for
digital/other branches. The FM clocks (bits 7:6) are set by the apply function on every analog
channel change.

### 3.4 Codec

Initial codec state comes from `hr_c7000_modem_reset_sequence` @0x03021b80:
- SB / SB_SLEEP cleared (0x160009d2)
- 0x160009d3 = 0x40
- 0x160009cb = 0
- 0x160009cc |= 0x40
- 0x160009c9 = 0xc0 (ADC master, enabled, parallel PCM)
- 0x160009c8 = 0xc0 (DAC master, enabled, parallel PCM)
- 0x160009cd &= ~0x10 and &= ~0x80 (DAC powered, soft mute off)

This matches the datasheet's FM RX recipe (§7.2.2.2). So codec DAC clocking, which the modem FM
output may depend on, is on in stock.

### 3.5 Other modem registers stock touches in FM

Writes found by disassembly with base tracking: FM_BANDWIDTH(0x500)=0x5f,
FM_DEV_COEF(0x504)=0x33500, FM_PTT(0x560)=0/1. The set is complete for the 3334 flash functions
of `meta/disasm.jsonl` (`meta/xrefs.json` `source_disasm_coverage.partial == false`).

### 3.6 What stock does not touch (consistent with the lead)

- No access to 0x508, 0x564, 0x568, 0x56c, 0x398, 0x3a0 (flash functions). No `lrw` literal
  0x1100056c or 0x1100xxxx in flash or IRAM; the only 0x1100xxxx hits are stray non-address
  words.
- 0x160004b0 appears once, at 0x0300d46c, as a literal-pool word. It is the base pointer inside
  `copy_bytes_from_offset_table` @0x0300d41c (a byte memcpy "RX RAM + offset"), used by the DMR
  call-setup handlers (`dmr_call_setup_*`): this is the L2 receive RAM, which shares the base
  with the FM voice halves. Together with 0x16000960 / 0x16000980 it is not an FM usage.
- IRAM objdump search (`docker run ... csky-abiv2-elf-objdump` on `IRAM_dm32.00.01.048.bin`)
  found no 0x398/0x3a0/0x56c users; it did find INTERP_CLEAR 0x3b0 and mask 0x39c users (the
  reinit sequence). IRAM disassembly was not exhaustive.
- Reading TX RAM FM_VOICE bytes: `FUN_030c6780` @0x030c6780 reads 0x16000032/33 and
  0x16000232/33 (TX RAM FM_VOICE_1/2, +2/+3) with `ld.b`, mixes them with RSSI_BIAS_VALUE
  (0x11c) into an LCG: it is a RANDOM SEED source. That means the FM TX recording RAM is live in
  stock (mic ADC audio lands in 0x16000030..0x1600042f when in FM mode, DS L18104-18112 p.477) and
  stock authors treat those bytes as non-constant. No such use exists for the RX RAM.

---------------------------------------------------------------------------------------------

## 4. Probe recipe (answer to Q4)

Design principle: read-only first. The expected state on an analog channel is that all the
enables are already stock-set, so step 1 and 2 need no writes. Writing is only a fallback, and
every write is reversible.

### 4.0 Gate

- Run only when the active channel is analog (stock: `(p_vfo_display_state->flags_0x8 & 0xf0) == 0`)
  and not transmitting.
- Hardware sanity (all reads, base `M = 0x11000000`):
  - `WORK_MODE` (`M+0x100`) bit7 == 1 and bit8 == 0, else ABORT. Never write WORK_MODE: switching
    modem modes mid-session fights the stock DMR state machine and the FM_PTT / layer registers.
  - Print and log: `SYS_SOFT_RSTN` (M+0x00; expect bit2 = 1), `CLK_MGR_REG0x2c` (M+0x2c; expect
    bits 7:6 = 1), `ADC_CONTROL` (M+0x74; expect bits 8:7 = 1, bit14 = 1, bit0 = 1),
    `AUDIO_CONTROL` (M+0x80; expect bit0 = 0), `RF_MODE` (M+0x104; expect bit24 = 1, [23:16] =
    0x26), `SYS_INTERP_MASK` (M+0x39c; expect bits 15:13 = 0), `SYS_INTERP_LIST` (M+0x398),
    `FM_BANDWIDTH` (M+0x500), `FM_DEV_COEF` (M+0x504), `FM_SQL` (M+0x508), `FM_PTT` (M+0x560;
    expect 0), `PHASE_OUT` (M+0x128), `FM_ADDR_SW` (M+0x56c).
  - If `SYS_INTERP_MASK` bit14 is 1, ABORT (stock's ISR would receive an FM_RX interrupt it does
    not handle).

### 4.1 Step 1: is the FM engine alive? (read only, about 300 ms)

Poll `FM_ADDR_SW` (32-bit load at 0x1100056c) every 1 ms for 300 ms (OSTimeDly; see
dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md)). Record each value change and its time.
- Expected: alternates 1,2,1,2 with period 32 ms (about 9 transitions in 300 ms), 8 kHz x 256.
- Always 0: the engine is not producing data. Go to 4.4 (fallback). Do not guess.
- Also log `SYS_INTERP_LIST` bit14 over the same loop, and `FM_SQL` min/max.

### 4.2 Step 2: read the half the register points at

On each edge to value v (1 or 2):
1. `addr = 0x160004e0 + (v-1)*0x200`; read 512 bytes with byte loads into a small buffer (or stream
   into the accumulators below). Reading takes microseconds; a half lives for 32 ms.
2. Re-read FM_ADDR_SW; if it changed, DISCARD this half (torn read).
3. Convert `s[k] = (int16)((b[2k]<<8)|b[2k+1])`.
4. Accumulate over about 32 halves (about 1 s), no big buffer needed:
   - mean and RMS (and peak)
   - zero-crossing count of `s - mean`
   - Goertzel power at 1200 Hz and 2200 Hz (8 kHz rate)
   - a smoothness metric `sum|s[k]-s[k-1]| / sum|s[k]|`
5. Also do the same on the other half for the first few frames (policy B), to validate the
   register semantics (4.3).

### 4.3 Validating the half selection (decides whether FM_ADDR_SW means "read this" or "being filled")

Read the same half twice about 8 ms apart (inside one FM_ADDR_SW epoch).
- Half that FM_ADDR_SW indicates (policy A): bytes IDENTICAL between the two reads, and smoothness
  low (audio-like).
- The other half is being written: bytes differ between the two reads.
- If instead the indicated half changes and the other is stable, the register means the opposite
  (swap, and note it in the doc).
- If both are stable and identical to each other, the RAM is not being filled (engine off, or
  the recording is not active in this state).

### 4.4 Fallback: only if step 1 sees FM_ADDR_SW stuck at 0 (writes begin here)

Capture the old value, then do the minimum. After each step re-run 4.1.
1. If `CLK_MGR_REG0x2c` bit7 (and 6) were 0: `*(M+0x2c) |= 0x80` (fmrx). Restore: clear the
   bit(s) you set.
2. If `ADC_CONTROL` bits 8:7 were 0: `|= 0x180`. Restore: `&= 0xfffffe7f`.
3. If `SYS_SOFT_RSTN` bit2 was 0: DO NOT touch; abort and report. (It holds the whole FM block in
   reset.)
4. If `AUDIO_CONTROL` bit0 = 1: abort and report (do not flip).
5. Keep `SYS_INTERP_MASK` unchanged. Never unmask bits 13..15.
6. If still dead, the possibilities are (a) the FM engine needs a codec-clock handshake, (b)
   RX recording needs an additional switch not in the datasheet, (c) this board's analog mode
   never uses the FM PCM engine. Stop. Report the register snapshot. Do not try to write
   WORK_MODE, the codec, or the GPIOs.

### 4.5 Proof statistics (what proves "this is real audio")

Run each window about 1 s. Compare windows with different conditions:
- (a) Carrier present, unmodulated, versus no carrier, versus squelch noise:
  - Real FM audio: no carrier gives broadband noise with high RMS (open squelch hiss), an
    unmodulated carrier gives RMS dropping sharply (quieting).
  - The FM_SQL value should move the opposite way (high with no carrier).
  - `FM_SQL`, RMS and smoothness should move together; a constant or ramp is a dead path.
- (b) A 1 kHz test tone on the air (second radio with a function-generator-like tone: DTMF
  "single tone" is not available, use a bench generator into a mic input, or a 1750 Hz tone-burst
  button):
  - Zero-crossings per 256-sample half: `zc ~= 2 f * 0.032` so 1 kHz gives about 64 and
    1750 Hz gives about 112. Equivalent frequency estimate `f = zc * 15.625 Hz`. Over 32 halves:
    `f_est = (total_zc / 2) / (nhalves * 0.032)`.
  - Pass if |f_est - f| / f < 3% for two different tone frequencies (1000 and 1750 Hz), which also
    rules out a fixed-rate artifact.
  - Goertzel power at the tone frequency at least 20 dB above the average of 1200 and 2200 Hz
    neighbours when the tone is not 1200/2200.
- (c) Amplitude linearity: raise TX deviation (or change the source level) by 2x: RMS should
  rise by about 2x (not saturate). This also tells whether `af_recv_ctrl` = 0x26 is a sensible
  gain for FM.
- (d) Byte-order/sign check: compute the above for both big-endian and little-endian, signed and
  offset interpretations. Only the right one gives a clean tone; wrong ones give a sawtooth-like
  amplitude with many ZC jumps. (Expect big-endian signed.)
- (e) End-to-end: ultimately feed the stream to the AFSK decoder and receive a known APRS beacon.

### 4.6 Restore

- Steps 1-3 and 4.2/4.3/4.5 (the expected path): nothing to restore, no register was written.
- Any fallback write is restored to the exact value read before it, by read-modify-write on the
  same bit mask (list in 4.4). Never write whole-register constants.
- At exit leave `FM_ADDR_SW` etc. untouched. The probe must not leave SYS_INTERP_CLEAR written.

### 4.7 Risks

- Reading RX RAM when the digital/DMR state machine owns it: gated by WORK_MODE bit7 and the
  analog-channel check. The FM voice halves overlap the DMR L2 receive area at the same
  base (0x160004b0), so never run on a digital channel or in digital/analog dual-detect mode
  (WORK_MODE bit8 = 1).
- FM_RX_INTERP: keep bit14 masked; stock does not handle it and the PIC `System_inter` source
  would fire repeatedly (level triggered). The probe polls.
- Acknowledge requirement: if the hardware holds the ping-pong until the CPU acknowledges
  FM_RX_INTERP, a polled design would see FM_ADDR_SW stop toggling after the first buffer.
  Mitigation: if that is observed, `*(M+0x3a0) = 1<<14` (write-1-to-clear, only that bit) after each
  half. This is the only tentative write and is a no-op on bits that are not set.
- Torn reads: handled by the double FM_ADDR_SW read.
- CPU load / watchdog: 512 byte reads per 32 ms is negligible; do not block longer than the
  stock tick. Use the RTOS delay, not a spin, in the 300 ms poll loop.
- Do not toggle GPIO 0x2E / 0x2F or `fm_play_ctrl` (AUDIO_CONTROL bit6). The user keeps hearing
  FD6818 audio because the speaker path is not touched.
- Access width: use byte loads on the 0x16000000 region only.
- `FM_PTT` stays 0 (RX). Writing it to 1 transmits.

---------------------------------------------------------------------------------------------

## 5. Confidence

| Claim | Confidence | Basis |
|---|---|---|
| Stock sets FM mode (WORK_MODE bit7, FM clocks) on analog channels | HIGH | FW decompiles above; two independent writers |
| Input to the modem on this board is the AF (demodulated) signal on the ADC | HIGH (config), MEDIUM (what the PCM spectrum looks like) | RF_MODE 0x2000000/0x1000000/0x260000 in register_init; DS §8.2.3.1 |
| RX recording RAM (0x160004e0..0x160008df) is filled by default in FM mode | MEDIUM | DS §7.2.2.3.2 only; never exercised by stock |
| 8 kHz, 16-bit, big-endian, two's complement | MEDIUM-HIGH for rate/endianness, LOW-MEDIUM for sign | DS §7.2.2.1.2 and Tables 57/58 (zh clarifies "high 8 bits first") |
| FM_ADDR_SW toggles every 32 ms and indicates the half to read | MEDIUM | DS §11.2.2.28 wording; semantics inferred; probe validates |
| Polling FM_ADDR_SW without interrupt works | MEDIUM-LOW | no datasheet statement either way |
| Stock keeps FM interrupts masked | HIGH | all four SYS_INTERP_MASK writes have bits 13..15 = 0 |
| Speaker source on analog = FD6818 (GPIO 0x2F = 1) | MEDIUM-HIGH | write pattern; label parse |

---------------------------------------------------------------------------------------------

## 6. Key stock addresses

- 0x0301e628 `modem_rf_mode_transition_apply` (analog: FM clocks, WORK_MODE bit7)
- 0x03055f88 `rf_mode_transition_on_freq_delta` (calls it from channel change)
- 0x03021670 `radio_mode_transition_sequencer` (WORK_MODE = 0x80, SYS_SOFT_RSTN = 0xfffffffc)
- 0x03023608 `hr_c7000_modem_core_register_init` (ADC 0x41c3, RF_MODE AF bits, SYS_INTERP_MASK
  0x1187f, FM_DEV_COEF 0x33500)
- 0x030c65f8 `hr_c7000_modem_core_reinit_sequence` (saves/restores RF_MODE, WORK_MODE,
  AUDIO_CONTROL, FM_PTT, SYS_INTERP_MASK; mask = 0, RF_MODE = 0x10e0, WORK_MODE = 0x80)
- 0x030c66f0 `FUN_030c66f0` (restore half of the above)
- 0x0301e578 / 0x0305ba70 ADC enable bits 8:7
- 0x0300c2cc `modem_fm_bandwidth_write_0x5f`; 0x0300c304 `modem_fm_ptt_set`
- 0x030573e8 `channel_change_apply_dispatcher` (GPIO 0x2e / 0x2f)
- 0x03021b80 `hr_c7000_modem_reset_sequence` (codec DAC/ADC enabled)
- 0x030043c8 `hr_c7000_modem_core_reset_and_poll_ready` (CLK_MGR_REG0x2c = 0xffffffff)
- 0x03054a6c `crystal_osc_and_timer5_init` (CLK_MGR_REG0x2c = 0xfff0ff3c from literal 0x03054bb4)
- 0x0300d41c `copy_bytes_from_offset_table` (uses literal 0x160004b0 at 0x0300d46c)
- 0x030c6780 `FUN_030c6780` (random seed from TX RAM FM voice bytes)

Note: dm32uv-re `data/registers/HR_C7000-DM32.h` has no FM_SQL / FM_ADDR_SW / FM_DECODE_FLAG* members; use raw `*(volatile uint32_t *)0x1100056c`.

---------------------------------------------------------------------------------------------

## 7. Open questions

1. Is the FM RX recording RAM really written without any switch beyond WORK_MODE bit7? Only the
   datasheet says so.
2. Does the FM RX engine need the FM_RX_INTERP to be acknowledged, or the codec DAC to consume
   samples (clock master), for the ping-pong to keep running? Stock has the DAC enabled.
3. Exact meaning of FM_ADDR_SW values 1/2 (read-now vs being-written) and whether 0 can occur
   after the first buffer.
4. Spectrum of the PCM: whether FM-block filters (fmbpfon=1) or de-emphasis cut/tilt 1200-2200 Hz,
   and whether the FD6818 AF_out already contains de-emphasis. Not in the datasheet.
5. Does `af_recv_ctrl` = 0x26 (tuned by stock for DMR) clip FM audio at full deviation? Clipping
   shows up as peak == 32767 in the probe stats.
6. Sample signedness (two's complement assumed).
7. Whether AF_out is a real net into the HR_C7000 ADC_VINP pin on the DM-32 board: inferred from
   the stock DMR configuration, not from a schematic.
8. The IRAM interrupt dispatcher was not exhaustively decoded. A guarantee that no stock IRAM
   code reads or clears bit 14 of SYS_INTERP_LIST relies on the mask always keeping it off.

## Hardware result (2026-10-15): solved

- GPIO `0x2F` ("Switch audio PWM/FD6818", vendor pin map) is an analog switch on the FD6818 AFOUT. Stock: HIGH on analog channels (AFOUT → speaker amp, modem ADC parked at `adc_bias_i` ≈ 240, `PHASE_OUT` flat 37..38), LOW on digital (AFOUT → modem ADC, `adc_bias_i` 66, `PHASE_OUT` ±1213 of discriminator noise).
- Driving `0x2F` LOW on an analog channel: FM Receive RAM carries the received audio (1 kHz test tone: RMS 757, zero-crossing 1000 Hz; `PHASE_OUT` −1088..897). The FD6818 `REG_47H` (already `0x6142` in steady analog RX), the full digital FD6818 register set, and `SIG_CENTER[15:8]` made no difference.
- Speaker: with `0x2F` LOW the FD6818 → speaker path is silent. Setting `AUDIO_CONTROL` (`0x11000080`) bit 6 `fm_play_ctrl` makes the demodulated audio audible again via the codec DAC/PWM. Capture + listening = `0x2F` LOW + `AUDIO_CONTROL |= 0x40`. Restore both to leave the stock analog path.
- Keypad scan codes confirmed on hardware: `*`=0x0d, `#`=0x0e, `0`=0x0f, `1`..`9`=0x10..0x18 (the `dm32_patch_api.h` table).
- `FM_ADDR_SW` names the **finished** half the CPU may read (1 = `0x160004e0`, 2 = `0x160006e0`), as the datasheet says; the other half is being written. The Audio Probe's first on-screen label for this was inverted (its arithmetic was right, its text wrong), and the first APRS RX build read the half being written, which measures like a tone but never decodes a packet. Fixed in v12.

## Decoder investigation on real captures (2026-10-15)

- `tools/pcm_capture.py` streams the FM RX RAM over UART0 (the programming cable); `out/capture2.wav` holds twelve plays of the test packet `out/afsk_beacon_test.wav` (phone speaker → the other radio's mic → FM → this radio), RMS ~200, quiet floor RMS 4.
- The capture is contiguous (no discontinuity at the 256-sample block edges) and the preamble decodes textbook-perfectly: space runs of 48 samples and mark blips of 5–6 = the 7-bit/1-bit flag pattern at 1208 baud; space tone at 2220 Hz, negligible harmonics. The first ~20 ms of each play are lost to squelch opening (5 of 8 preamble flags survive), which HDLC tolerates.
- Against the known transmitted bits (`out/demod_compare.py`): three plays contain one bit slip mid-frame; the rest have ~10% scattered errors, 19% of bits in 1-bit tone runs, 6% in 2-bit runs, ~0 in runs of 5+, split evenly between the two tones. Twenty demodulator/clock variants (window length, smoothing, loop gain, sampling phase, band-pass, per-tone normalisation, adaptive threshold, pre-emphasis, zero-crossing instantaneous frequency, sum-over-bit) all stay at ~8% or worse; none passes an FCS.
- Reading: a 34 dB-SNR channel that loses single-bit runs of either tone equally while long runs are perfect is multipath, i.e. the acoustic coupling (phone speaker → mic, echoes of several bit-times), not the receive chain. Next test: real over-the-air APRS on 144.390 MHz.
- Kept in the C decoder: 3-sample smoothing of the decision metric and a 1/8 clock-loop gain (no slips, about half the errors on these bursts).
- Confirmation (cross-correlation of a received burst against the exact synthesized AFSK of the known packet, `out/demod_compare.py`): main peak only 0.38 (a clean channel with 10% random bit errors would still exceed 0.8) with a slowly decaying trail of secondary lobes at +2.2, +3, +4 and +5.7 ms = 0.7–2 m path differences. Room reflections of the phone-speaker → mic path, i.e. acoustic multipath; not a property of the radio's receive chain.

## Decoder study 2026-10-16: the 1-bit errors are a tone-path skew, not a filter bandwidth

Scoring the phone-sourced bursts of capture2 against the known tone sequence (out/gt_analysis.py,
out/decoder-study/) showed 24% errors on single-bit tone runs and almost none on longer runs. A
3-tap logistic fit over the two correlator energies at +-3 samples revealed the cause: through the
radio chain the 2200 Hz energy arrives about 2.5 samples (310 us) later than the 1200 Hz energy.
Delaying the space-tone energy by 2.5 samples before differencing brings c2_b0 from 41/185 one-bit
errors to 1/634 total. Pre-emphasis (29/32 first difference) and a W=6 window help on top; the
remaining failures were bit-clock faults (noise before the burst saturating the rate integrator,
noisy hard-decision edges), now addressed with a leaky integrator and a 3-tap bit-instant
correction decided one bit late. Result: out/capture2.pcm decodes 9 of 12 bursts
(N0CALL-9>APZUNL,WIDE1-1,WIDE2-1:!4000.00N/10000.00W>DM-32UV ...) where no variant of the old
decoder passed a single FCS. This is the "radio profile" in krk_afsk.c (AFSK_R_* macros), selected by
krk_afsk_set_preemph(1), now the APRS RX screen's default (v32). Our own phase-discontinuous beacon
captures (c9/c10) still do not decode with it, which is expected and moot: that TX method is retired.
Open: the profile is tuned on one packet over one link; a real on-air decode is the next check.
