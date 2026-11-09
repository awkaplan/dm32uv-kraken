# DM-32UV RX audio path: findings from forum threads and GitHub repos

Read-only source survey (2026-10). The hardware answer came later: see [rx-audio-fm-mode.md](rx-audio-fm-mode.md), "Hardware result (2026-10-15)".

Source shorthand used below:
- `1148` = infotex58.ru DM-32 thread, [topic 1148](http://infotex58.ru/forum/index.php?topic=1148.0) (Russian). "pp. a-b" = forum pages; page N is `topic=1148.<15*(N-1)>`.
- `1168` = infotex58.ru DM-32 developer thread, [topic 1168](http://infotex58.ru/forum/index.php?topic=1168.0). Same page rule.
- `1155` = infotex58.ru "Flash Dump HR_C7000" thread, [topic 1155](http://infotex58.ru/forum/index.php?topic=1155.0).
- GitHub: [yo3hjv/Baofeng-DM32](https://github.com/yo3hjv/Baofeng-DM32), [gzalo/dm32-uv](https://github.com/gzalo/dm32-uv),
  [fenfir/hd2_infodump](https://github.com/fenfir/hd2_infodump) (Ailunce HD2, same SoC), [OpenRTX-external-docs](https://github.com/OpenRTX/OpenRTX-external-docs),
  [infamy/DM32-Protocol-Spec](https://github.com/infamy/DM32-Protocol-Spec), [SarahRoseLives/dm-32uv](https://github.com/SarahRoseLives/dm-32uv), [M7OCM/DM-32UV](https://github.com/M7OCM/DM-32UV).
- `OpenRTX/` = an experimental, unpublished OpenRTX DM-32 port. Not used as evidence.
- `DemoUV008_2` = the OpenGD77-derived HR_C7000 vendor SDK source (`DemoUV008_2.rar`) shared by R3MDG in the OpenGD77 forum
  ([topic 5142](https://www.opengd77.com/viewtopic.php?f=10&t=5142), page 2).
- `BAP` = an earlier unpublished project note on the baseband audio path, read from HR_C7000 datasheet v2.7. Its results are in
  [rx-audio-fm-mode.md](rx-audio-fm-mode.md) and dm32uv-re [`docs/audio-and-fm-mode.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/audio-and-fm-mode.md).

Items from the OpenRTX port, the vendor SDK and BAP are marked **[out of scope]**: they are not forum or GitHub sources.

Relevance scale: **direct** = states the fact about this radio's audio hardware or firmware. **indirect** = supports an inference, or concerns a related chip or another radio.

---

## 1. Headline

- The forum sources contain **no schematic, no block diagram, no analog-switch part numbers, and no BOM entry for any audio mux**. Every audio-path statement is either a GPIO label from a pin map or a reading of firmware by someone else.
- The OpenRTX DM-32 port is an experimental, unpublished port and is not used as evidence. Any finding that rests only on the port is labelled "OpenRTX port only — not evidence".
- The most useful new facts are: the HR_C7000 integrates CPU, DMR modem, codec, DAC and ADC on one die (1168 post #47). GPIO 0x2E is the mic source mux (1168 post #67, forum). The roles of 0x2F (speaker-source mux) and 0x35 (audio PA) come only from the OpenRTX port — OpenRTX port only — not evidence. The speaker amp is most likely a TDA2822M (yo3hjv/Baofeng-DM32, photo label); its drive from 0x35 is not established.
- The vendor SDK tree (`DemoUV008_2`, [out of scope]) has **no working codec ADC or DAC driver**. Its I2S and DAC bodies are HAL calls commented out with `//***`. The only ADC code that runs reads a general 10-bit SAR ADC, with channel 0 used for VOX/mic level.
- The best candidate for a CPU-readable RX sample buffer is the **FM Receive RAM at 0x1600_04B0** inside the codec/modem window (datasheet Table 58, per BAP). **No source shows RX audio actually filling it.**

---

## 2. Findings

### 2a. SoC composition and codec

| # | Source | Key sentence | Rating |
|---|---|---|---|
| F1 | 1168 pp. 2-6 (post #47, 12 Feb 2026) | Russian: "HR_C7000 это набор микросхем на одном кристале CPU CK803S + DMR modem + Audio Codec + DAC + ADC". English: "The **HR_C7000** is a set of chips integrated onto a single die: **CPU CK803S + DMR modem + Audio Codec + DAC + ADC**." | **direct** (chip composition, by a developer, not a datasheet quote) |
| F2 | yo3hjv/Baofeng-DM32 (annotated board photo, section 2) | Labels on the DM-32 board: "uC+Codec — HR_C7000, with the codec called out as on-die", "TDA2822 — audio power amp", "RDA5807 — FM broadcast tuner", "traced RX path / TX path overlays". | **direct** for part presence (photo labels). The overlays are not in text. |
| F3 | yo3hjv/Baofeng-DM32 | `TDA2822.pdf` = TDA2822M (11 pp, ST), in the repo. | **direct** (datasheet present) |
| F4 | fenfir/hd2_infodump, "PIC ... IRQ 0-37" and "IO_DIPLEX0/1/2" paragraphs | IRQ table includes `DAC`, `ADC`, `PCM_rd/wr_data_interp`, `Codec_interp`. The IO_DIPLEX tables "also document AK-series audio codec pin assignments (`AK_ADC_SDO/SCLK/FS`, `AK_DAC_SDI/SCLK/FS`)". | **indirect**: same HR_C7000 silicon, but a different radio (Ailunce HD2). Confirms the codec has a serial PCM interface on pins. Board wiring not covered. |
| F5 | Ingenic SCODA codec driver (Linux BSP, GPL) **[out of scope]** | The datasheet's `FCR_DAC` (offset 0x14) and `FCR_ADC` (offset 0x17) rate fields say "0000:8KHz; 其它略" ("others omitted"). The note recovers the table from Ingenic SCODA BSP code. | **indirect** (codec identity and table; the table is not verified on DM-32 hardware) |
| F6 | 1148 pp. 16-20; 1148 pp. 41-45 | Stock voice prompts: "Audio format: 8 kHz, Mono, 16-bit PCM". | **indirect** (consistent with 8 kHz codec default) |
| F7 | 1148 pp. 1-5 | "Radio only records audio in DMR (digital) mode, not analog." | **indirect**: stock recorder never captures analog RX |

### 2b. GPIO muxes and the audio PA (the pin-map evidence)

| # | Source | Key sentence | Rating |
|---|---|---|---|
| G1 | 1168 pp. 2-6 (post #67, 14 Jun 2026) | Russian: "Исправлено назначение порта GPIO_B, 0x2E Audio path MIC Analog/DMR". English: "GPIO_B pin **0x2E** corrected/identified as controlling the **microphone audio-path switch between Analog and DMR modes**." | **direct** (pin map, author Koshak) |
| G2 | **[out of scope]** `OpenRTX/platform/targets/DM-32/pinmap.h` lines 62-67, 93-94, 117-118 | `AUDIO_PA 0x35` ("Audio PA control" per the forum pin map). `0x2E` "Audio path MIC Analog/DMR" selects the mic source. `0x2F` "Switch audio PWM/FD6818" selects the audio destination. Code comment: `AUDIO_MUX_MIC 0x2E /* SET = analog (FM), CLEAR = DMR */`; `AUDIO_MUX_DEST 0x2F /* SET = FD6818, CLEAR = PWM */`. | **OpenRTX port only — not evidence.** Only the 0x2E label is confirmed by forum post #67 (G1). The 0x2F and 0x35 labels are not in any of the forum threads. |
| G3 | **[out of scope]** `OpenRTX/platform/drivers/baseband/radio_DM32.c` lines 29-31 | "pin 0x35 ... It is the AUDIO PA. The forum pin map labels it 'Audio PA control', and the stock firmware agrees." Stock drives it alongside the TONE1 generator. | **OpenRTX port only — not evidence.** No forum post mentions pin 0x35. |
| G4 | **[out of scope]** `OpenRTX/platform/drivers/audio/audio_DM32.c` lines 368-376 (`dm32_tonePathClose`) | `gpio_setPin(... AUDIO_MUX_DEST)` with comment "speaker <- FD6818 (FM RX)"; `gpio_clearPin` gives "speaker <- SoC codec". | **OpenRTX port only — not evidence.** Says the 0x2F mux sits on the RX-to-speaker path. |
| G5 | 1168 pp. 7-11 (post #108 changelog, 11 Jul 2026) | "Fixed/added in FW (OpenGD77_DM32_20260711): 1. Analog microphone signal bleeding into DMR modulation". | **indirect**: a mic/DMR path interaction existed and was fixed in firmware. Consistent with a shared mux. |
| G6 | 1148 pp. 11-15 | CPS path: "Option Feature → Other … microphone gain for analog and digital modes". | **direct** (CPS setting) |
| G7 | 1148 pp. 1-5 | Mic gain in engineering menu had no effect (post #10/#11). One user removed a film from the mic and modulation got louder. FW `DM32.01.02.046` added "extended DMR mic gain adjustment". | **indirect** (one teardown, anecdotal) |

**Where 0x2F is documented:** it was assumed to be the FM RX speaker path. The forum posts do not name 0x2F at all. The label "PWM/FD6818" comes only from the OpenRTX pin map, which cites the vendor pin map spreadsheet (not among these sources). OpenRTX port only — not evidence. Treat the "FM RX speaker path" wording as an OpenRTX inference.

### 2c. FD6818 and where AFOUT / MICIN appear

- The string `AFOUT` and the string `MICIN` do **not appear anywhere in these forum threads or GitHub repos** (text search, no hits).
- OpenRTX-external-docs (FD6818B vs AT1846S register comparison) gives FD6818B PA bias as `REG_19H[3:0]`, RSSI taps `REG_62H`, and says the FD6818 has two RSSI taps (pre-AGC and post-AGC). It says nothing about audio pins. **indirect** (wrong domain).
- OpenRTX-external-docs: the Schematics archive has **no DM-32 or Baofeng DM-32 schematic**. Closest is Ailunce HD1. **direct** (negative result).
- **[out of scope]** BAP says that in FD6818 BYPASS mode "shaped 4FSK in at MICIN, demodulated 4FSK out at AFOUT". **indirect**: the source is the FD6818 datasheet as read for BAP, not the forum.

### 2d. Where the mic goes (contested)

| # | Source | Claim | Rating |
|---|---|---|---|
| M1 | gzalo/dm32-uv | Links the DR5800 service manual (a **different** radio with HR_C7000), which shows the mic on the codec's MIC2 differential input: not DM-32 evidence. What settled the question here was the vendor DM-32 pin map (**no FD6818 mic pin exists on this radio**) plus HR_C7000 v2.7 EN 6.4.3.2. | **indirect** (the strongest DM-32-specific argument, but it cites an unconfirmed datasheet section) |
| M2 | yo3hjv/Baofeng-DM32 | "with the schematic finding that the mic path is the SoC codec." No schematic is named. | **indirect** (provenance unclear) |
| M3 | **[out of scope]** `OpenRTX/platform/drivers/audio/audio_DM32.c` lines 259-261 | "the former on-demand codecCaptureUp() never captured anything anyway (**the FM mic path is the 0x2E mux, not this ADC**)." | **OpenRTX port only — not evidence** (a porting conclusion; would contradict M1 and M2 on the FM case if true) |
| M4 | **[out of scope]** `OpenRTX/platform/drivers/audio/dm32_audio_out.c` lines 361-363 | "whether the codec DAC output can reach the FD6818 MICIN at all is NOT established -- the 0x2E mux ('MIC Analog/DMR') appears to route the MICROPHONE, not the DAC." | **OpenRTX port only — not evidence** (the port author marks it unverified) |

**Reading:** M1 and M2 say the mic lands on the codec. M3 (OpenRTX port only — not evidence) says the analog FM mic goes through the 0x2E mux to the FD6818. Both can be true if 0x2E chooses the mic's destination: codec in DMR mode, FD6818 in analog mode. Nothing in these sources settles this. BAP's section on `MIC1/MIC2` says the codec ADC feeds the modem in DMR (`7.1 Voice sending data flow`, quoted in BAP).

### 2e. Analog signalling decoders (indirect evidence that RX audio is decoded somewhere)

| # | Source | Key sentence | Rating |
|---|---|---|---|
| S1 | 1148 pp. 1-5 | Spec: "analog signaling systems: DTMF, MDC1200, 2-Tone, 5-Tone". | **direct** (spec) |
| S2 | 1148 pp. 16-20 | "MDC encode/decode on the DM32 is a great feature!" User confirms MDC1200 works. | **direct** (feature works) |
| S3 | 1148 pp. 6-10 (post #147) | Menu path: "Menu>Scan>Scan CTCSS" finds a subtone on RX; then "Menu>Settings>Channel Set>CTC/DCS>Tx CTC/DCS". | **indirect**: the firmware detects CTCSS on received audio. Whether detection is in the FD6818, the modem, or CPU DSP is not stated. |
| S4 | 1148 pp. 21-25 | RX subtone enabled on Koshak's own unit gave "silent as a fish" on a live frequency (single test, unverified). | **indirect** |
| S5 | 1148 pp. 21-25 | CPS screens for "Optional Signal" feature set: DTMF, 2-Tone, 5-Tone. | **direct** (CPS screens) |
| S6 | 1148 pp. 36-40 | Five-Tone BOT: releasing PTT before BOT tone finishes leaves the radio stuck transmitting. Koshak independently reproduced on FW 01.01.049. | **direct** for a TX-side signalling bug |
| S7 | 1148 pp. 36-40 | "Optional Signal" enabled (Menu > Settings > 2. Channel Set > 9. Optional Signal) makes that channel never open squelch during scan. Single-user report. | **direct** (reported bug, one source) |
| S8 | 1148 pp. 31-35 (posts #467-468) | Five-Tone CCIR repeat digit shows as "E". Koshak: "Chapter 11.9 Selcall in datasheet HR_C7000". | **direct** (datasheet chapter existence) |
| S9 | 1148 pp. 26-30 (post #445, Nov 2025) | OpenDM32 (Koshak's from-scratch firmware): "keyboard, display, power-amplifier control, LNA, SPI flash, audio control … already implemented". DMR not yet. | **direct** (developer's own list; audio control exists, code not published) |
| S10 | 1148 pp. 16-20 | DMR squelch does not open on 395 MHz (signal bars full) on DM-32; DM1701 with OpenGD77 decodes it. | **indirect** (DMR, not analog) |
| S11 | 1148 pp. 46-50 | Full signal bar, squelch open, no audible modulation; forcing squelch off gives faint speech. | **indirect** (symptom suggests RX audio detector/gate problem) |

**Caveat on S2-S4:** BAP lists CTCSS and DTMF among FM-block registers (BAP, the Table 29 description). So decoding may be in the RF chip, not on the CPU. The existence of decode features does not prove CPU access to samples.

### 2f. Codec and voice RAM (datasheet-level, mostly from the OpenRTX driver and BAP)

| # | Source | Key statement | Rating |
|---|---|---|---|
| R1 | **[out of scope]** `OpenRTX/platform/drivers/audio/audio_DM32.c` lines 13-21 (header) | `AUDIO_CONTROL 0x11000080`: "[5] ahb_rd_voice_en: enable RX voice stream from the codec", "[4] ahb_wr_voice_en: enable TX voice stream to the codec", "[6] fm_play_ctrl", "[7] ring_play_ctrl". | **OpenRTX port only — not evidence** (the header's direction labels are not verified) |
| R2 | **[out of scope]** `audio_DM32.c` lines 46-50 | "RX to speaker sets AC_RD_VOICE_EN | AC_FM_PLAY -- and on hardware the receiver demonstrably works (PLL locks, and the S-meter moves). But nothing ever powered the audio amplifier." | **OpenRTX port only — not evidence** (the port author's own hardware observation, not independently confirmed) |
| R3 | **[out of scope]** `audio_DM32.c` lines 85-111 | Codec base `0x1600_09C0` (Register Table 26). "FM Receive Ram (base 0x1600_04B0)" sits in the same Modem_Buffer window (`0x1600_0000-0x1600_FFFF`). "Stock's accesses here are buffer streaming, not register writes". | **OpenRTX port only — not evidence** for the Table 26 and RAM-address claims (the datasheet is the authority). It does not say RX audio fills the RAM. |
| R4 | **[out of scope]** `OpenRTX/platform/targets/DM-32/...` and `dm32_audio_out.c` lines 46-54 | Prompt FIFO `0x1600_0980`-`0x1600_09BF`. `CODEC_FCR_DAC 0x160009D4`, `CODEC_FCR_ADC 0x160009D7`. | **OpenRTX port only — not evidence** (addresses taken from the port's source; check against datasheet Tables 26, 57, 58) |
| R5 | **[out of scope]** BAP (path table) | Three voice interfaces: **A** AHB voice RAM (27-byte AMBE frames, not PCM); **B** prompt FIFO (8 kHz, alert tones only); **C** FM voice RAM at `0x1600_0000` (send) and `0x1600_04B0` (receive), 16-bit PCM, 512 B = 256 samples per ping-pong half, "rate not stated anywhere", "**The only candidate**". | **indirect** (BAP's analysis of v2.7 Tables 57/58; not tested) |
| R6 | **[out of scope]** BAP | Datasheet v2.7 §7.1 (p. 436): "MIC the audio from the end is transmitted through CODEC ADC … 16bit Parallel data and corresponding 8KHz Enable signal … In simulation [analog] mode, codec ADC The output audio data is directly on the MODEM". | **direct** (datasheet quote, via BAP) |
| R7 | **[out of scope]** same doc, the `FM_ADDR_SW` description | `FM_ADDR_SW (0x56C)` is read-only: "hardware tells the CPU which ping-pong half to use." | **indirect** |
| R8 | **[out of scope]** `DemoUV008_2/user/SOURCE/interfaces/dac.c` (53 lines) | `dacOut()` returns with no effect; `HAL_DAC_SetValue` is commented out (`//***`). | **direct** (vendor DAC driver is a stub) |
| R9 | **[out of scope]** `DemoUV008_2/.../interfaces/i2s.c` | `I2SStartDMA()` only clears flags; `HAL_I2SEx_TransmitReceive_DMA` is commented out. `i2s_Rx_Buffer` is declared but nothing fills it. | **direct** (vendor I2S RX is dead code) |

### 2g. Vendor SDK (DemoUV008_2) and the general ADC

Tree: `DemoUV008_2/` **[out of scope]**.

- `user/DRV/C7000_DRV.c` lines 804-841: `ADC1_GetValue()` and `ADC0_GetValue()` run a single-channel conversion on a C7000 ADC block. Each returns `& 0x3FF`, which is 10-bit. Their register macros (`ADC_CTRL`, `ADC_CH_VLD`, `ADC_DATA_AB`, ...) are **not defined anywhere in the tree**, so register offsets are not known from source.
- Base address from disassembly (`Debug/DemoUV008_2.elf.asm` around line 75263, `ADC1_GetValue`): `movih r3, 5133` = `0x140D0000`. This is the ADC block base. Treat it as an inference from one function.
- `user/SOURCE/interfaces/adc.c` lines 92, 125-128: `adcVal[1] = ADC1_GetValue()` is used for battery; `adcGetVOX()` does `micLevel = ADC0_GetValue()`.
- `user/SOURCE/functions/vox.c` line 119: `uint16_t sample = micLevel; // NOTE: not using adcGetVOX(), but direct (and valid) ADC value.` VOX samples this on a timer.

**Verdict on the general ADC:** the only ADC channel with code that uses it is channel 0, which is called mic/VOX level. That is the TX side. No code in the tree reads RX audio from any ADC. Whether ADC0's input pin is the mic line or FD6818 AFOUT is **not stated**. The tree is derived from OpenGD77 (STM32 naming, `HAL_ADC_ConvCpltCallback`, `TIMER3_EOI` on C-SKY), so the channel-to-pin mapping is inherited and unverified for DM-32.

**Codec ADC/DAC driver:** none working. See R8, R9.

**Other vendor code:** `user/SOURCE/dmr_codec/codec.c` and `codec_bin.S` are linked as a binary section (`.codec_bin_section_1`). This is most likely the AMBE vocoder, not the audio codec. Not verified.

### 2h. Other items checked (no audio content)

- 1155: flash-dump tool thread. No audio content. Covers 25Q80/25Q16/25Q128 and GD25G16 chips.
- infamy/DM32-Protocol-Spec, SarahRoseLives/dm-32uv: serial codeplug protocol and memory layout. No audio path content.
- fenfir/hd2_infodump (follow-up): boot ROM and load base only. No audio content.
- M7OCM/DM-32UV: `DMR-APRS.jpg` is "presumably an APRS-related screenshot; not independently reviewed". Not analog APRS.
- M7OCM/DM-32UV (firmware images): the HR_C7000 SYSCTRL (base `0x11000000`) pin-mux init code is "not present in any of the downloadable application partition images".
- gzalo/dm32-uv: bootloader protocol. `02 18 xx 03 + addr + size + data` writes arbitrary memory, `02 1b xx 03 + addr` jumps to an address. Documented as a way to run code without flashing. Not verified here. This may matter if a probe needs to be loaded into RAM.
- 1148 pp. 41-45: no Bluetooth chip on the PCB. Not audio.
- 1148 pp. 51-54: PCB revisions 1.2, 1.4, 1.5. The author guesses the schematic did not change. Not verified.
- 1148 pp. 16-20: LCD driven by a direct parallel bus from the CPU. Not audio.
- 1148 pp. 6-10: RTC supercap sits near the microphone. Physical location only.

### 2i. Hardware mods, test points, and AF pads

- **No "AF test pad", "AFOUT test point" or "MICIN" pad is mentioned anywhere** in these forum threads or GitHub repos.
- Mods that touch audio: removing a film from the mic (G7); mic gain changes via CPS (G6). Neither is an analog tap.
- JTAG pads on the PCB: 1148 pp. 26-30. Unlabeled header; standard JTAG signals. Not audio.
- Forum pin map files (`PinMap_Baofeng_DM-32_*.zip`, attachments 4304, 4404, 4475): not downloaded in this scrape. The GPIO table in them is the best source for any missing mux pin. Only the pins quoted in posts are known.

### 2j. BOM / IC list (audio-relevant parts only)

| Part | Role | Source | Rating |
|---|---|---|---|
| HR_C7000 (CK803S + modem + codec + DAC + ADC) | SoC | F1, F2 | direct |
| FD6818 / FD6818B (also marked CS3417xa per yo3hjv) | RF / direct-conversion transceiver with AFOUT/MICIN per BAP | 1168; yo3hjv/Baofeng-DM32 | direct (part), indirect (audio pins) |
| TDA2822M | audio power amp (speaker) | F2, F3 | direct (part present), indirect (driven by 0x35) |
| RDA5807M | FM broadcast receiver. Control on GPIO 0x19 (SDIO) and 0x1A (SCLK) per pin map (1168 post #60, 1168 pp. 2-6). Audio path unknown. | F2, post #60 | direct (part, control pins) |
| ATGM336H | GPS module | F2, 1168 | direct |
| GD25Q80 / W25Q128JV / PY25Q128HA | SPI flash (not audio) | yo3hjv/Baofeng-DM32 | direct |
| ALPU-MP | security chip (I2C) | 1148 | direct (not audio) |
| Analog mux / switch parts for 0x2E / 0x2F | **not identified in any source** | — | **open** |

---

## 3. What this confirms / contradicts / leaves open

### Confirms
- The HR_C7000 has an **on-die codec with ADC and DAC**. The DM-32 uses it at 8 kHz by default (F1, F4, F5, F6, R6).
- **GPIO 0x2E is the mic source mux** (analog vs DMR), per forum post #67 (G1). The 0x2F and 0x35 roles are OpenRTX port only — not evidence (G2-G4).
- **A TDA2822M is on the board** as the speaker amp (F2, F3). That it is driven from 0x35 is OpenRTX port only — not evidence.
- The **vendor ADC is a 10-bit SAR** with channel 0 used for mic/VOX level, not RX audio (2g).
- The stock firmware has **no codec-register writes in the FM path** (R3; the SCODA rate-table note). Both rest on OpenRTX-spike notes: OpenRTX port only — not evidence.

### Contradicts or conflicts
- **Mic destination** (2d): gzalo and yo3hjv say the mic goes to the codec. OpenRTX says the analog FM mic goes through 0x2E to the FD6818. These cannot both be complete descriptions of the FM case. The OpenRTX reading (M3, OpenRTX port only — not evidence) is the only one that explains the 0x2E label, so it cannot be used as evidence here.
- **"No FD6818 mic pin exists"** (M1) conflicts with OpenRTX's "analog FM audio has to reach the FD6818, so 0x2F must select FD6818" (pinmap.h lines 110-113; OpenRTX port only — not evidence). Both could hold if the mic reaches FD6818 through the mux, not a dedicated pin.
- **Codec-to-DAC as a "TX-side only" path:** the OpenRTX `dm32_audio_out.c` says a stream to SINK_RTX (FD6818) is "routing UNVERIFIED" (OpenRTX port only — not evidence).
- **The vendor SDK does not contain a working codec ADC or DAC driver** (R8, R9). The "DemoUV008 has codec drivers" premise is false for this tree.

### Leaves open (the questions that matter for RX audio to the CPU)
1. **Does any RX audio sample reach a CPU-readable buffer?** The best candidate is **FM Receive RAM `0x1600_04B0`** (R3, R5). Nothing in the sources shows it being written during analog RX. It needs a live read test: set the FM RX path, poll `FM_ADDR_SW` (`0x1100_056C`, read-only per R7), and see whether the RAM changes.
2. **What drives FM Receive RAM?** BAP says the FM voice RAM is hardware-paced by the codec-derived enable (R5, R6). If it is fed from the codec ADC, RX audio only enters it when the codec ADC is routed to the FD6818 path. The 0x2E mux is the likely switch.
3. **What is the rate of the FM voice RAM?** "Not stated anywhere" (R5). The FM filter coefficients may assume 8 kHz (BAP, "Open question 1").
4. **Which pin is ADC0?** The VOX code reads it as "mic level" (2g). If ADC0's input is the mic line, it is only a TX-side reading, and it may be a slow sampler, not an audio-rate stream.
5. **Which chip carries the speaker-side switch?** The 0x2F mux part number is unknown. So is whether TDA2822M's input comes from 0x2F or from the codec.
6. **Analog decoders (MDC1200, CTCSS, DTMF, 5-tone):** these are decoded somewhere (S1-S5). It is not known whether that is in the FD6818/modem hardware or in CPU DSP. If it is CPU DSP, RX audio exists in firmware memory at some point, and a hook there would be the simplest access point.
7. **The 0x2F label and the "FM RX speaker path" wording** come from OpenRTX, not from the forum. OpenRTX port only — not evidence. Treat them as unverified.
8. **The RDA5807M audio path** is unknown. It may be a second RX audio source that bypasses FD6818.
9. **Pin-map PDFs and attachments** (PinMap_Baofeng_DM-32_14.06.26.zip, attachment 4475) were recorded but not downloaded. They may name the mux parts or the AFOUT/MICIN pins.
10. **HR_C7000_v2.7 datasheet** (EN and zh-CN) is cited by chapter number in several sources (6.4.2.3, 6.4.2.4, 6.4.3.1, 6.4.3.2, 6.4.7, 7.1, 7.2.2.2, 7.3.2.2, 11.9, Table 26, Tables 57-58) but is not quoted in full by any of them. Chapter 6.4.3.2 is what the project used to settle M1. The full text is needed to check the mic and codec-ADC routing.

---

## 4. Ten-line summary

1. The HR_C7000 integrates CPU, DMR modem, codec, DAC and ADC on one die (1168 post #47). Direct.
2. GPIO 0x2E selects mic source (analog vs DMR) per forum post #67. The 0x2F and 0x35 roles come only from the OpenRTX port — OpenRTX port only — not evidence. No mux part numbers are named anywhere.
3. Speaker amp is most likely TDA2822M (photo label and datasheet archived in yo3hjv/Baofeng-DM32). Its drive from 0x35 rests only on the OpenRTX port — OpenRTX port only — not evidence.
4. AFOUT and MICIN do not appear in any of these forum threads or GitHub repos. The FD6818 BYPASS description (AFOUT out, MICIN in) is in BAP, from the FD6818 datasheet.
5. Where the mic goes is contested: codec ADC (gzalo, yo3hjv) vs FD6818 via the 0x2E mux (OpenRTX port only — not evidence). The forum label alone does not settle it.
6. The vendor DemoUV008 tree has a 10-bit SAR ADC (`ADC0` = mic/VOX, `ADC1` = battery). It has no working codec ADC or DAC driver; I2S and DAC bodies are commented-out HAL stubs.
7. Analog MDC1200, CTCSS, DTMF and 5-tone are supported (S1-S5). Decoder location (CPU or RF chip) is unknown, so this does not prove CPU sample access.
8. Best candidate for CPU-readable RX audio: FM Receive RAM at 0x1600_04B0, 16-bit PCM, hardware-paced via FM_ADDR_SW (0x1100_056C). Rate and whether RX audio fills it are unknown.
9. No source contains a schematic for the DM-32, an AF test pad, or an AFOUT/MICIN tap. The HR_C7000 v2.7 datasheet is cited by chapter but not quoted in full.
10. Next step: test FM Receive RAM and FM_ADDR_SW on a live unit while the FM RX path is active, and read the datasheet chapters 6.4.3.2 and 7.1 to settle the mic routing.
