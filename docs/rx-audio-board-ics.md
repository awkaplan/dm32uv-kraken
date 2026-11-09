# RX audio path: board IC identification (photo-based)

Scope: can analog FM RX audio (FD6818 AFOUT, or the speaker line) reach anything the CPU can sample?
Method: viewed annotated board photo + ~25 forum/teardown photos; read HR_C7000 v2.2 manual pin tables
and FD6818 programming guide. Read-only research. Nothing flashed or probed.
Legend: READ = legible in a photo. INFERRED = deduced. DOC = from a datasheet/pin-map text.

Sources:
- A = `DM32-component-side-annotated.jpg` from YO3HJV's [Baofeng-DM32](https://github.com/yo3hjv/Baofeng-DM32) repo (first-gen board, hand-labelled by YO3HJV)
- IMG = photos posted in the infotex58.ru DM-32 thread ([topic 1148](http://infotex58.ru/forum/index.php?topic=1148.0)). Names
  below are `pageNN-<name>` (forum page NN of the thread) or `attachNNNN` (forum attachment id).
- HR_C7000 pin tables: HR_C7000 User Manual v2.2 (Chinese), pin-description chapter
- FD6818: FD6818 programming guide rev 1.0.0 (Chinese), pin and AFOUT/BYPASS sections
- Pin map: the vendor DM-32 pin map spreadsheet (`PinMap_Baofeng_DM-32_14.06.26.xls`, GPIO sheet)

## 1. Headline finding (corrects an earlier "no analog input pin" conclusion)

- DOC: HR_C7000 has TWO separate analog input families, and an earlier M17 transport note only checked one:
  - (a) MCU SAR ADCs ADC0..ADC7_IN (balls B1,A2,B3,A3,C4,B4,A4,A5; shared with PTC15-22). Pin map assigns them
    nothing audio-related. This is the family the earlier note ruled out. Still correct.
  - (b) DEDICATED MODEM RX ADC, two channels I/Q: ADC_VINP_I (N2), ADC_VINM_I (P2), ADC_VINCM_I (P1),
    ADC_VINP_Q (M1), ADC_VINM_Q (M2), ADC_VINCM_Q (N1). Manual section 5.2: "two high-performance ADCs, support AF and
    IF RF interfaces". Section 5.2.1 "AF receive" (RF_MODE[24]=1) and Figure 5.3 show the intended hookup:
    `RDA1846S AF_out -> low-pass -> HR_C7000`. FD6818 is the RDA1846S-class part, and its guide (p.38 BYPASS mode)
    says AFOUT delivers the demodulated 4FSK signal for DMR. That is the DMR RX path.
  - So FD6818 AFOUT -> HR_C7000 modem ADC (I or Q single-ended input) is not just plausible. It is the vendor reference design
    and the only way DMR RX can work on this board. INFERRED for the exact ball (single-ended mode uses VINP_Q M1 or VINCM_I P1 per the
    pin-table text; the table text is internally inconsistent about I/Q naming, check the v2.7 en pin table).
- The modem ADC output is consumed by the DMR demod hardware, not exposed as a sample FIFO. CPU-visible handles are
  registers only: PHASE_OUT (0x128, AF-mode phase amplitude, "-1300..1300" range), ADC_CONTROL (0x74), RF_MODE (0x104,
  gain [23:16], AF_MODE enable [24]), SIG_CENTER (0x108, I/Q offsets). A baseband FM ping-pong buffer is mentioned elsewhere
  as dormant (M17-GAP2 doc). Whether CPU can read demod samples at >=24 kHz via this block is NOT answered by the photos.
- The codec MIC inputs (Codec_MIC_INP1/INN1 A14/B14, INP2/INN2 A13/B13, differential or single-ended Line-in) exist
  and are a second possible entry for an analog signal. DOC. Nothing in the photos or the pin map shows AFOUT wired there
  (the pin map has the "Audio path MIC Analog/DMR" switch on the mic/TX side, see section 4).

## 2. IC inventory

| IC / part | Marking as READ | Photo | Function | Confidence |
|---|---|---|---|---|
| SoC | `HR_C7000` / `D16429MP1` / `EQT452-B220` (BGA; bare BGA footprint visible in IMG/page27-without_hr_c7000.jpg) | IMG/page53-ultra-teardown-attach4523.jpg, attach4533.jpg; A "uC+Codec" | CK803S CPU + DMR modem + codec + modem ADC/DAC | READ, high |
| RF transceiver | `FD6818` / `CS4143XA` (QFN-32-ish, 5x5) ; A labels it "FD6818/CS3417xa" (hand label, likely misread) | attach4523.jpg, attach4529.jpg (clearest) | AFOUT/MICIN baseband RF chip | READ, high |
| FD6818 crystal | `28.00AN D523` in attach4529.jpg (prior review read "26.00AN"; this crop reads 28 or 26) | attach4529.jpg | 4-pad XO/TCXO next to FD6818 | READ, digit ambiguous |
| SoC osc | `BN24.0 D518` (also `D519`/`D419` on other units) | attach4533.jpg, page27-without_hr_c7000.jpg | 24 MHz clock | READ |
| Data flash | `winbond 25Q128JVSQ 2524` | attach4535.jpg, attach4523.jpg | W25Q128JV, SPI0 | READ, high |
| Program flash | `GD 25Q80ETIG` / `PJ2503` / `C120596` (8-pin) | attach4523.jpg top | GD25Q80 (not "Q08") | READ, high |
| FM tuner | `5807M` / `TN551` (10-pin MSOP) | attach4529.jpg | RDA5807M | READ, high |
| 2-pad crystal beside flash/RDA | `32.768`-style, marking unreadable (`M2S`/`32K`-like) | attach4529.jpg | probable RTC or RDA5807 32.768 kHz ref | INFERRED |
| GNSS | `ATGM336H` / `5N-31` / `C50840` | attach4527.jpg | GPS+BDS module | READ, high |
| Speaker amp | `TDA2822` / `DAWY4T` (SOIC-8, left of GNSS) | attach4527.jpg (readable); A at lower right; attach4525.jpg top-right | dual BTL/stereo audio PA | READ, high |
| PA devices x2 | `HTL7G06S011P` / `ETRE0057` / `2433-JY` | attach4537.jpg | LDMOS RF final(s), VHF+UHF | READ, high |
| Buck inductor `4R7` + SOT-23-6 `A6164N`/`A9164N` | `4R7`, `A6164N` | attach4523.jpg right | DC/DC (A labels "DC/DC + PON sw") | READ part text, function INFERRED |
| ALPU-MP | not legible in the board shots; `page25-alpu-mp*.jpg` show only a SOT-23-5 `2345` | IMG/page25-alpu-mp_onboard.jpg | anti-clone IC (per prior notes) | marking `2345` READ; identity from prior notes |
| RTC | NOT FOUND as a separate IC on either side. Only a 2-pad xtal near flash. HR_C7000 has an on-chip RTC block (manual diagram "RTC"); I2C 0xE0 probably belongs to some other I2C slave. | - | - | negative, INFERRED |
| Misc SOT-23-5/6 | `MIAT1` (x3, one beside Winbond), `2515`, `N9.`, `L6`, `AVAYV`-ish x2, `A14PR`, `2345`, `R25`, `BM`/`ZR`/`JF` | attach4523.jpg, attach4529.jpg, attach4533.jpg, attach4527.jpg | LDOs / small switches / diodes | markings READ, parts NOT identified |

Notes:
- `MIAT1`-style markings sit next to the Winbond flash, near RDA5807M, and near the SoC; three look identical.
  They are 6-pin SOT-23. This is a candidate pattern for a SPDT analog switch (SGM3157/74LVC1G3157 family is SOT-23-6)
  but I could NOT tie a marking code to a part number from the photos. Do not treat as identified.
- Sibling-board facts: V1.2 (2024-08) and V1.5 (2025-02) layouts match for the audio area.

## 3. (1) Speaker audio amplifier

- READ: TDA2822 (ST dual low-voltage power amp, SOIC-8) at the speaker end of the board, beside the GNSS module. Datasheet: ST TDA2822.
- What feeds it: NOT visible. DOC from pin map: GPIO 0x35 = "Audio PA control" (enable), GPIO 0x2F = "Switch audio PWM/FD6818".
  INFERRED reading of 0x2F: an analog switch selects the amp input between (i) the HR_C7000 codec LINEOUT1/2 PWM signal
  (pins C15/C14, an RC-filtered PWM output, DOC) and (ii) FD6818 AFOUT (analog FM RX audio direct).
  The amp is fed by exactly one of those two sources. No photo shows the traces.
- Where the amp sits: speaker pads `SPK`/`SW` are on the keypad-side of the main board near the u.FL connector
  (IMG/page53-ultra-teardown-attach4513.jpg, crop). An 8-pin IC with 5 resistor-like parts is right next to them on that
  side (unlabelled, unreadable; could be a second small switch/op-amp or the TDA2822's neighbour). UNIDENTIFIED.
- Speaker: `SJ36 16R 1W` (attach4509.jpg READ).

## 4. (2) Analog switches / muxes

- No analog switch part number is legible in any photo. NOT identified: no SGM3157/BL1551/WAS4729 marking found.
- DOC pin map says two audio steering GPIOs exist: 0x2E "Audio path MIC Analog/DMR" and 0x2F "Switch audio PWM/FD6818".
  Taking them at their labels:
  - 0x2E: mic/TX side. Chooses what drives FD6818 MICIN: the analog microphone chain (codec mic path or raw mic) vs the DMR 4FSK baseband
    from the SoC (DAC_VOUT_A/B J1/J2, "single-point modulation" signal, DOC). Fits FD6818 BYPASS TX ("MIC ADC output goes straight to TX").
  - 0x2F: speaker side, as above.
- Candidate switch locations (INFERRED, markings only): the three `MIAT1` SOT-23-6 chips; the 8-pin IC near SPK on the back; two small SOT-23 parts
  left of the TDA2822 (`24`, `74`-ish). Each would need continuity-testing or a datasheet match.
- GPIO 0x2E/0x2F rows share the AK2401-bank bits with a different bank; the pin-map analysis is in the M17 doc.

## 5. (3) FD6818 AFOUT to HR_C7000: is a trace plausible?

- FD6818 and HR_C7000 are in adjacent shielded/segmented zones. FD6818 sits lower-middle-left, SoC upper-left
  (attach4523.jpg: FD6818 ~ (680,620), SoC ~ (270,270), about 1.5 cm). Gold copper-pour fences separate the cells
  but the cells are adjacent, so a short AF line between them is geometrically trivial. READ (layout), route itself NOT readable.
- The HR_C7000 is a BGA (footprint visible, 0.8 mm-ish pitch, page27-without_hr_c7000.jpg) so all ball routing is under
  the package or on inner layers. No surface trace can be followed to a specific ball. INFERRED only.
- FD6818 QFN pin numbers: the FD6818 programming guide has no pin table (pin names AFOUT/MICIN appear only in block diagrams).
  I could not give FD6818 pin numbers. Need the FD6818 hardware datasheet, or probe.
- Evidence summary for AFOUT -> modem ADC: manual Fig 5.3 (vendor ref design), FD6818 guide p.38 (AFOUT -> demod 4FSK for DMR),
  and the fact that DMR RX works on this radio. Strong DOC/INFERRED. No contradiction in photos.
- Evidence for AFOUT -> codec mic pins: none.
- Test to settle: with RX live, scope or continuity-test from the FD6818 AFOUT pin to ball N2/P1/M1 region vs A13/A14 (codec MIC pins).

## 6. (4) Unpopulated pads / test points on the audio path

All READ unless stated.
- Main board back (IMG/page53-ultra-teardown-attach4513.jpg and IMG/page01-top2.jpg): gold pads labelled `AF1`, `AF2`
  (beside the LCD FPC), `SW`, `SPK`, `MIC`, `TX`, `RX`, `3V3`. These look like wire-through test points, partly bridged to jack wiring.
  `AF1/AF2` are the best candidates for audio-frequency tap points, but the label alone does not say which net. NOT confirmed.
- Keypad-end corner (IMG/page27-hr_c7000_dm32.jpg, V1.2): six-pad JTAG column (TMS/TCK/nTRST/TDO/TDI/GND, READ) plus pads `AF-0` (with arrow)
  and `AF-1`, and a `3V3` pad. A mic electret and a small 0402 cluster sit beside `AF-0`. Most likely mic-bias/mic-in test points. INFERRED.
- SoC side: a column of ~14 small passives along the right and left edges of the SoC (series/bypass parts on signal balls) in
  attach4533.jpg. No bare test pads on the audio nets visible on the RF side of the board.
- No pad was found that is labelled AFOUT or LINEOUT.

## 7. What was NOT resolved (be explicit)

- No part number for any analog switch.
- The 8-pin IC beside `SPK` on the back is unidentified.
- RTC: not found as a discrete IC. The "I2C 0xE0" device is unlocated.
- FD6818 pin numbers not obtained.
- Exact ball receiving AFOUT not determinable from photos (BGA).
- Whether CPU can read the modem ADC at audio rate: documentation lists only PHASE_OUT/AF-mode registers. See HR_C7000 v2.2 sec 5.2.1, and the
  FM RX ping-pong buffers ([rx-audio-fm-mode.md](rx-audio-fm-mode.md)).

## 10-line summary

1. Photos CONFIRM: HR_C7000 (D16429MP1), FD6818 (CS4143XA), W25Q128JVSQ, GD25Q80ETIG, 5807M/TN551, ATGM336H-5N-31, TDA2822 (DAWY4T), 2x HTL7G06S011P, 24 MHz BN24.0 and 28/26 MHz XO.
2. Speaker amp = TDA2822 (readable). Its input source is not visible; pin map GPIO 0x2F "Switch audio PWM/FD6818" implies a 2:1 analog switch choosing codec LINEOUT PWM vs FD6818 AFOUT.
3. No analog switch part number legible anywhere. Candidates: three 6-pin `MIAT1` SOT-23-6 parts and an unmarked 8-pin IC beside the `SPK` pad (UNIDENTIFIED).
4. RTC: no separate IC visible. Only a small 2-pad crystal near the flash. 0xE0 device unlocated.
5. NEW: HR_C7000 has a dedicated modem RX ADC with I/Q input pins (ADC_VINP_I N2, ADC_VINM_I P2, ADC_VINCM_I P1, ADC_VINP_Q M1, ADC_VINM_Q M2, ADC_VINCM_Q N1), separate from MCU ADC0-7.
6. The vendor manual (sec 5.2.1, Fig 5.3) wires RDA1846S/FD6818 AF_out to that modem ADC. FD6818 BYPASS mode gives demodulated 4FSK on AFOUT for DMR. So AFOUT reaching an SoC ADC is the intended design (INFERRED, strongly).
7. That ADC feeds the DMR demod, not a CPU FIFO. CPU sees only PHASE_OUT (0x128), RF_MODE (0x104, AF gain/enable), SIG_CENTER (0x108), ADC_CONTROL (0x74). Needs a firmware/register test.
8. Codec MIC_INP/INN 1/2 (A14,B14,A13,B13) and LINOUT1/2 PWM (C15,C14) exist but no evidence AFOUT goes to the MIC pins. MCU ADC0-7 unused for audio (agrees with the earlier note).
9. Traces cannot be followed (BGA, inner layers). FD6818 and HR_C7000 are adjacent cells so a short AF line is geometrically trivial. FD6818 pin numbers not available.
10. Test points on the audio path: `AF1`,`AF2`,`SW`,`SPK`,`MIC`,`TX`,`RX`,`3V3` on the back, `AF-0`,`AF-1`,`3V3` near the JTAG column. None labelled AFOUT/LINEOUT.
