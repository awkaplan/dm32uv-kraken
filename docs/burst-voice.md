# F13 Compressed burst voice: feasibility and design (DM32.00.01.048)

Status: sections 0-8 are static research plus one host simulation, written before anything was transmitted.
Sections 9-13 record the build, the tests and the hardware findings.
Evidence tags: HIGH = datasheet/firmware agree or measured on air in this project; MED = one source or
a sound inference; LOW = recollection, guess or untested extrapolation.
An earlier feasibility note claimed 6-10x; that claim rests on a
19.2 kbps DMR modem that nobody has located the registers for (section 2b). Parrot capture/injection is
being researched separately (`docs/parrot-recording.md`, possibly `docs/parrot-tx.md`); this note only reuses its
facts. The early air-time numbers come from a throwaway host simulation (not in the repo).

## 0. Verdict

- A credible design exists, but the payoff is **about 2x to 3x shorter air time, not 6-10x**.
  Typical 6 s message (70 % speech activity): about 2.3 s on air with the fast mode and silence trimming,
  about 4.4 s without trimming, about 6.4 s (worse than real time) with the robust mode and no tricks.
  The key-up overhead (about 0.35 s, MED/LOW) is a fixed cost that caps the ratio for short messages.
- Chosen design: **software single-carrier D8PSK modem (2000 baud, 6 kbps raw) in the 8 kHz PCM domain
  through the proven FM Send RAM / FM Receive RAM paths**, carrying the **stock AMBE+2 27-byte units** (the
  vocoder is CPU code in IRAM, so it needs no extra RAM), with Reed-Solomon + CRC per block, optional silence
  trimming, and playback of the received units through the stock playback ring.
- Verifiable without a radio: the whole modem, framing, FEC, container and the receiver state machine, against
  channel models. NOT verifiable without a radio: mic to AMBE capture, playback injection, the true audio channel
  above 2.2 kHz, TX linearity, key-up timing (section 6).
- Not credible: codec2/LPC-class software vocoders (RAM and CPU, section 2b), the DMR modem at a non-standard
  symbol rate (registers not located, hardware sync engine), stock DMR data bursts (no gain, section 1c).
- The gating hardware unknown is the **audio channel above 2.2 kHz** (section 1a). A single-key "sounding" probe
  settles it (section 6.3). Build the host side first; it is useful whatever the probe says.

## 1. Throughput candidates

Common facts (all HIGH unless noted): TX = software PCM into HR_C7000 FM Send RAM (8 kHz, 16-bit big-endian, 2 x 512 B
halves, 32 ms each) -> FM modulator -> MOD DAC -> FD6818 (`docs/fm-send-ram-tx.md`); 1200 baud AFSK went on air and
was decoded bit-exact by the other radio (v38, capture17). RX = FM Receive RAM 8 kHz PCM of the FD6818 AF_out, read
with byte reads (`docs/rx-audio-fm-mode.md`; AF-mode input confirmed in firmware; spectrum above 2.2 kHz MED/LOW).
CPU: 84 MHz per this project's own timing work (`krk_afsk.c` header), 120/168 MHz in other notes (MED); the AFSK
decoder costs 2.4-3.2 MIPS.

### 1a. Software modem over the 8 kHz PCM path

Hard limits: Nyquist 4 kHz. Measured: the RX chain passes 1200 Hz and 2200 Hz with twist -4 to -7 dB (capture16/17), the
APRS pre-emphasis profile exists because of that tilt. Nothing is measured above 2.2 kHz or below 1.2 kHz. The TX
"level control normalises the PCM amplitude" (capture16/17, the amplitude cannot set the twist): there is a
limiter/AGC in the chain, so amplitude-carrying modulation (QAM) is at risk and constant-envelope or phase
modulation is preferred. Usable band assumed 400-2900 Hz = about 2.5 kHz (MED/LOW).

Channel model caveat (important): `tools/afsk_channel.py` is fitted on 1200/2200 Hz tones only. Its first biquad has
a zero at 1700 Hz (radius 0.994) = a **-26 dB notch at 1700 Hz**, which is a fit artifact between the two tones
(the data cannot see it) and is lethal for any wideband single carrier. I therefore simulated two channels:
A = that model verbatim; B = smooth: 2nd-order band-pass 300-2900 Hz, the model's all-pass dispersion, and a
first-order tilt giving about -3 dB at 2200 vs 1200 Hz. Receiver in the sim: complex mix at 1700 Hz, RRC (alpha 0.35)
matched filter, fractionally-spaced least-squares equalizer trained on 300 symbols, differential detection for PSK.
White noise, SNR referenced to total in-band signal power. Results = symbol error rate after the equalizer
(0.0000 = zero errors in 3000 symbols; a single run per cell, seeds fixed):

| modem | raw bps | B 15 dB | B 20 dB | B 25 dB | A (notch) 25 dB |
|---|---|---|---|---|---|
| DQPSK 1600 bd | 3200 | 0 | 0 | 0 | 0.032 |
| DQPSK 2000 bd | 4000 | 0 | 0 | 0 | 0.034 |
| D8PSK 1600 bd | 4800 | 0.0003 | 0 | 0 | 0.21 |
| D8PSK 2000 bd | 6000 | 0.0023 | 0 | 0 | 0.22 |
| 16-QAM 1600 bd | 6400 | 0.0003 | 0 | 0 | 0.077 |
| 16-QAM 2000 bd | 8000 | 0.0003 | 0 | 0 | 0.082 |
| D8PSK 2400 bd | 7200 | 0.033 | 0 | 0 | 0.42 |

Reading: if the real channel is "smooth", 6 kbps D8PSK works down to 15-20 dB audio SNR and 8 kbps is possible with
a linear TX. If the 1700 Hz notch is real, no wideband single carrier works (the equalizer cannot undo 26 dB) and
the fallback is a bit-loaded multi-tone (OFDM-lite): a capacity estimate over the channel A shape with a 9.8 dB gap
and 3 dB margin gives about 2.4 / 4.9 / 8.1 / 11.7 kbps at 15 / 20 / 25 / 30 dB SNR (channel B: 3.2 / 6.1 / 9.6 / 13.5
kbps). That fallback needs a 128-point integer FFT (about 2 KB code) and a fixed tone map chosen from the sounding.
Evidence: MED for the simulation mechanics, LOW for what the real channel does.
What audio SNR to expect on real links: strong FM signals give 30-40 dB in the voice band (LOW, general NBFM
knowledge); the earlier 34 dB figure was an acoustic path. The audio SNR falls off a cliff at weak signal, so
the burst has a hard range limit shorter than voice (voice is readable at 10 dB SINAD, an 8PSK burst is not).

Ceilings I would plan on: robust DQPSK 2000 bd = 4.0 kbps; fast D8PSK 2000 bd = 6.0 kbps; strong-link-only
16-QAM/OFDM 8-10 kbps. FM Send RAM at 8 kHz fixes the ceiling; the 25 kHz channel-width option does not help because
the data path is audio-band (HIGH that the TX RAM is 8 kHz).

### 1b. DMR modem at a non-standard symbol rate

Prior note's idea: reprogram `RECVFIR_COEF1-17` and `modem_clk_div_num` (CLK_MGR 0x18, default 12). Why I do not rely on it:
- Only the RX FIR was found; the TX pulse-shaper (SENDFIR) registers were not located (the prior doc says so). HIGH.
- Symbol timing recovery, the DMR sync-word correlator, CACH/slot-type decoding, BPTC/trellis and CRC are fixed
  hardware/firmware state machines at 4800 baud; a faster rate means a different frame engine, not a different clock. MED.
- The 4800 baud x 2 bits = 9.6 kbps is the gross figure for BOTH slots together. One slot of a normal MS is about 4.8 kbps
  gross, and the voice burst already uses 216 of the payload bits per 60 ms. So even a perfect stock channel is about 1x
  (single slot). At 2x symbol rate in the 25 kHz mode one might reach 19.2 kbps gross = the prior note's 6-10x, but that
  needs three unlocated register blocks and a custom frame engine. Verdict: research-grade, XL, LOW. Not part of this design.

### 1c. Stock DMR data bursts

Per slot one 30 ms burst every 60 ms; payload 196 bits (data) vs 216 bits (voice). Unconfirmed rate-1 data = 24 B / 60 ms
= 3.2 kbps, rate 3/4 = 18 B / 60 ms = 2.4 kbps, voice = 27 B / 60 ms = 3.6 kbps (HIGH, ETSI burst structure; the 27 B
voice unit is verified in the firmware, `docs/parrot-recording.md` 1.1). So a single slot is **at or below the voice rate:
compression 0.9x or less**. Using both slots would give up to 2x, but a DMR MS in direct mode transmits in one slot only
and nothing here shows the HR_C7000 TX engine can be driven in both (LOW). The stock data path also has no
"send these bytes" entry point that has been located. Not credible as the compression mechanism. It does win on
robustness (hardware FEC and sync), which is why DMR remains the better choice for 1x.

## 2. Source coding

### 2a. What the vocoder is and where mic PCM lives

- The vocoder is **CPU code**, not an external chip: datasheet 7.1.2.1/7.1.2.2 say the audio RAM data is handed to the
  "CPU built-in voice coding function", and the firmware has a vocoder task in IRAM (`FUN_0002f000`) that copies 27 B
  to/from MMIO 0x16000960. HIGH (matches `docs/parrot-recording.md`; older notes about an STM32 HR_V3000 were
  corrected on 2026-10-02).
- **Mic PCM IS CPU-readable, in the digital TX configuration**: the codec ADC delivers 16-bit PCM into SRAM
  0x18000000..0x1800009F (80 words = 160 B = 10 ms at 8 kHz), interrupt `PCM_wr_data_interp` = IRQ28, cleared by writing
  1 to bit 4 of 0x110003B0; the software encoder consumes it and writes 27 B to 0x16000960 (DS 7.1.2.1, §7.1.2.3.1
  "CPU can choose to save the audio data before encoding (0x18000000-0x1800009F)"). HIGH for the datasheet, MED for
  this firmware using it that way. Decoded RX PCM goes to 0x180000A0..0x18000140 (IRQ27).
- In **analog FM mode** the codec ADC feeds the modem directly with no CPU copy (DS 7.2.2.1.1 "data flow does not require
  CPU to migrate"), so there is no known mic PCM buffer on an analog channel. LOW whether the 0x18000000 window can be
  enabled there (open question Q1).
- 0x18000000..0x180007FF is "modem-owned" in dm32uv-re `docs/rtos-and-memory.md`; consistent with this window. The IRAM image keeps
  its own task stacks; there is no big free IRAM. Free patch SRAM is only **0x18004C00..0x18008000 = 13,312 bytes**
  (minus Snake 784 B, AFSK about 960 B, guard words). This is the binding resource for any software codec.

### 2b. Options

| option | bit rate | CPU (no FPU, 84 MHz) | RAM | verdict |
|---|---|---|---|---|
| (i) stock AMBE+2 units as captured (27 B / 60 ms) | 3600 bps incl. vocoder FEC | 0 extra (stock task) | 27 B per 60 ms = 450 B/s | **use** |
| (i') AMBE+2 with vocoder FEC stripped (49 bits / 20 ms) | 2450 bps | trivial | 306 B/s | phase 2 (needs real frames to verify, 2.3) |
| (ii-a) codec2 700C/1300 (needs FFT 512, LSP, VQ, NLP) | 700-1300 bps | float code. M4F-class benchmarks I remember (LOW) are tens of MHz per real-time second; soft-float is 20-60x worse than FPU, so 2-20 s CPU per second of speech, plus a fixed-point rewrite | 10-20 KB state, VQ tables 30 KB flash | **not credible**: does not fit 13 KB SRAM |
| (ii-b) LPC-10 / FS1015 class, integer | 2400 bps | about 5-8 MIPS | 3-4 KB | works, but 2400 bps is no better than AMBE 2450; worse quality; no gain |
| (ii-c) MELP/LPC at 1200 bps | 1200 | no public small integer implementation known | - | would need a research port, LOW |
| (ii-d) ADPCM 2-bit at 8 kHz | 16,000 | trivial | 2 KB/s | 5x bigger than AMBE: defeats the purpose |
| (ii-e) CVSD 16 kbps | 16,000 | trivial | 2 KB/s | same |

So the source coder is the **stock AMBE+2 vocoder**. The only way to compress further is to remove what the speech does
not need: silence (VAD trimming, 1.4-1.6x on typical speech, 2.4) and the vocoder's FEC overhead (1.24x, 2.3).

### 2c. Capturing the units (record side)

The encoder only runs in the DMR TX path (`[0x1497]==1`, input PCM window above, output 27 B at IRAM `0x49b18`,
written to the modem by `0x172f8`; the recorder tap `0x2e558` copies to ring `0x46b5c`). Three routes; the first is
the plan, the others are fallbacks:
1. **Run the TX vocoder task unkeyed** on a digital channel: set the TX-mode flags and codec/AUDIO_CONTROL
   configuration that `audio_vocoder_buffer_flush2` @0x03021a70 sets for a call, but skip the RF keying (do not post
   0x3a4f0 bit 0x08), and tap `0x49b18`/ring `0x46b5c`. The encode output is then 27 B / 60 ms and nothing is written to
   0x16000960 or radiated, provided the modem TX is not enabled. Feasibility: MED-LOW (the modem may require TX on to
   clock the codec; the task is real-time, 60 ms budget). Identical code is needed by the parrot work for the TX side,
   so coordinate (`docs/parrot-tx.md`).
2. **Store raw PCM in the data chip** and feed the encoder later: 16 kB/s, 5 s = 80 KB = 20 sectors erased per message.
   Possible (the recording pool is flash), but slow and a wear cost; and the encoder entry has not been found as a
   callable function (it is the task loop). LOW.
3. **Relay mode only**: the burst is built from units received off-air (a RX capture, as the parrot); the operator "records"
   by speaking on a DMR channel to a second radio / or the stock Record switch, and "Record burst" imports the last
   recording. The stock record format is documented (151 units per sector, `docs/parrot-recording.md` 1.3). MED.

### 2d. FEC stripping / re-adding (public AMBE+2 3600x2450 knowledge, to be verified on real frames; LOW-MED)

The 72-bit vocoder frame = four codewords: C0 = Golay(24,12) (12 info bits), C1 = Golay(23,12) with the information
XOR-masked by a PN stream, C2 = 11 bits uncoded, C3 = 14 bits uncoded: 12 + 12 + 11 + 14 = 49 info bits; the
remaining 23 bits are parity. Steps (from the mbelib/DSD implementations as I remember them):
1. De-interleave the 72 bits with the fixed AMBE+2 table (the on-air order is a permutation; the 27 B from the modem
   window are 3 frames in on-air bit order; I cannot reproduce the table from memory reliably).
2. C0: Golay(24,12) decode (correct up to 3 errors), take 12 info bits u0.
3. C1: demodulate with the PN: `pr[0] = 16 * u0; pr[i] = (173 * pr[i-1] + 13849) mod 65536`; the mask bit for C1 position j
   is `pr[j+1] >> 15`; XOR it off, then Golay(23,12) decode to u1. C2 and C3 pass unchanged.
4. Pack 12 + 12 + 11 + 14 bits = 49 bits per 20 ms frame, 147 bits (18.4 B) per 60 ms unit.
5. Receive side: Golay-encode u0, generate the same PN from u0, Golay-encode u1 and XOR the mask, append C2 and C3,
   interleave back to the on-air order, and write the 27 B unit into the playback ring.
Self-check that needs no radio once any real units exist (a stock recording dump or a parrot capture): strip then
re-add and compare with the original bytes; a bit-exact match for >99 % of frames proves the tables (frames with
channel errors correct to the same codeword). Until that passes, ship the unstripped 27 B units.
Gain: payload 450 -> about 276 B/s (49 bits x 50 frames/s plus 2 bits/unit housekeeping) = 1.6x smaller before
burst FEC; with burst FEC and block overhead about 1.24-1.3x on air vs the unstripped design (section 3 table).
The vocoder's own FEC protects only 24 of 49 bits; the burst's RS replaces it, so nothing is lost.

## 3. Compression ratios (air time of a 6 s message, incl. 0.35 s fixed key-up cost)

Assumptions: speech activity 70 % (silence trimming removes the rest, MED/LOW), RS/CRC/header overhead 12 % (section 5),
fixed overhead (PA ramp, AGC/squelch settle at the receiver, preamble+training of 192 symbols) 0.35 s (LOW: the APRS beacon
needed a 480 ms preamble mostly for the receiver squelch, which a burst RX arm mode would hold open).

| design | on-air payload | robust DQPSK 4 kbps | fast D8PSK 6 kbps | strong link 8-10 kbps |
|---|---|---|---|---|
| AMBE as captured, no trimming | 6 s x 4.03 kbps = 24.2 kbit | 6.4 s (0.9x) | 4.4 s (1.4x) | 3.4 s at 8 kbps (1.8x) |
| AMBE as captured + trimming | 16.9 kbit | 4.6 s (1.3x) | 3.2 s (1.9x) | 2.5 s (2.4x) |
| FEC-stripped, no trimming | 6 s x 2.9 kbps = 17.4 kbit | 4.7 s (1.3x) | 3.3 s (1.8x) | 2.5 s (2.4x) |
| **FEC-stripped + trimming** | 12.2 kbit | 3.4 s (1.8x) | **2.4 s (2.5x)** | 1.9 s (3.2x) |

Longer messages approach the asymptote: fast mode with everything = 6000 / (2900 x 0.7) = 3.0x; strong link 8 kbps 3.9x.
With a 3 s message the fixed 0.35 s costs about 12 %. Only the fully-proven pieces (AMBE as captured, robust mode, no
trimming) give no gain at all, so the feature stands or falls on the fast mode and trimming.
Note for the DF rationale: the transmitter is on for the whole 2.4 s, and the receivers' benefit is real but modest.

## 4. Chosen design ("BV1")

Layer summary: [stock AMBE unit store] -> [block framer: gap byte, 6 units, CRC16, RS(182,166)] -> [D8PSK 2000 baud
modulator, fc = 1700 Hz, RRC alpha 0.35] -> 8 kHz PCM -> FM Send RAM (same path as the v39 APRS beacon).
Receive: FM Receive RAM -> sync correlator -> LS/LMS equalizer -> differential detector -> RS+CRC -> unit buffer
-> stock playback ring.

### 4.1 Modem parameters

| item | value |
|---|---|
| sample rate | 8000 Hz (HIGH, FM TX/RX RAM) |
| modes | 0 robust: DQPSK 2000 bd, 4.0 kbps; 1 fast: D8PSK 2000 bd, 6.0 kbps; 2 strong: reserved (16-QAM/OFDM) |
| carrier | 1700 Hz (centre of 400-2900 Hz). If the sounding shows the notch is real: move to the OFDM-lite fallback |
| pulse | RRC alpha 0.35, 4 samples/symbol, 33 taps (8 symbols span), Q15; TX polyphase = 1 phase per sample => 8 MACs/sample |
| mapping | differential: phase step in units of 2pi/4 or 2pi/8, Gray-coded bit groups |
| peak control | hard peak limit at 0.9 of the PCM full-scale chosen from the probe; crest factor of RRC D8PSK about 5-6 dB |
| level | constant RMS (the TX chain normalises anyway); target PCM RMS as the APRS beacon (default dev 0x40, 8000/8000, v38) |
| receiver | IQ mix 1700 Hz with a Q14 table, RRC matched filter (33-tap real x 2), gain/AGC from the preamble, symbol timing from the sync correlator (clocks of the two radios are the same crystal class, drift over 3 s is below 0.05 symbol), 21-tap T/2-ish complex equalizer trained by the sync word then decision-directed LMS (mu = 2^-6), differential detection |
| CPU | TX about 0.3 MIPS, RX about 3-5 MIPS (equalizer 21 complex taps at 2000 sym/s = 84k complex MAC/s = about 340k int MAC/s plus the matched filter 2 x 33 x 8000 = 0.5 M MAC/s): below the AFSK decoder's headroom (MED, estimate) |

### 4.2 Burst layout

```
[ lead   ]  FM key-up: AFSK-beacon-style raw carrier is not needed; send 48 symbols of alternating phase (0, pi) so the
            receiver AGC/squelch settle (about 24 ms) -- the receiver is armed with squelch open
[ SYNC   ]  64 symbols, fixed QPSK m-sequence (31/63 chip PN + 1), also the equalizer training, timing, mode sense
[ HEADER ]  DQPSK always, 24 B = RS(24,16) over 16 B: 
              b0   0xB5 magic, b1 version(4)|mode(4), b2-3 message id, b4-6 source id (DMR ID, 24 bit),
              b7-8 number of 60 ms units (u16), b9 number of blocks, b10 flags (bit0 silence-trimmed, bit1 FEC-stripped,
              bit2 last-pass), b11 tick = 60 ms code, b12-13 reserved, b14-15 CRC16-CCITT of b0..b13
[ BLOCK ] x N  each 182 B codeword = 166 B payload + 16 B parity, RS(182,166) over GF(256), poly 0x11D, t = 8 bytes:
              payload: blk idx (u8), gap (u8: silence ticks of 60 ms to insert BEFORE this block, 0 = none),
                       6 units x 27 B = 162 B (stripped mode: 6 x 18.4 B packed as 111 B + 1 B pad),
                       CRC16 over the above (2 B)
            + 16-symbol resync word between blocks (cheap slip detect; equalizer keeps adapting)
[ TAIL   ]  header copy (24 B) for a receiver that missed the first one, 16 symbols of silence
```
Block time at 6 kbps: 182 x 8 / 6000 = 243 ms (= 360 ms of speech, 6 units). Header 32 ms, SYNC 32 ms, lead 24 ms, tail 32 ms.
Overhead: parity 9.6 %, CRC+idx+gap 2.4 % => 12 %. A lost block (RS failure or CRC fail) plays as a repeat of the last good
unit fading to silence; the stock decoder itself conceals a handful of bad bits, so a few uncorrected bits inside the
27 B are better delivered than dropped: deliver CRC-failed blocks flagged "degraded" if RS could not correct and the
setting "Accept damaged" is on (default off).
FEC choice: RS only (no convolutional): the sim shows zero errors down to the cliff (about 15 dB audio SNR), and the
fade length we care about (impulse noise, squelch clicks) is a few ms = a few bytes. A burst-error test of RS(182,166)
at t = 8 and an interleaving depth of 2 blocks is part of the host test (6.1).

### 4.3 Receive-side detection

Armed explicitly from the menu (like the APRS RX screen) on an analog channel: squelch is forced open (as the APRS
decoder does; `rx-audio-fm-mode.md`), the 8 kHz stream is correlated against the SYNC word at all 4 timing phases
(complex correlation over 64 symbols = 256 samples, run once per sample only while the energy detector is above
threshold, so idle CPU is under 1 MIPS). Mode comes from which of two SYNC words correlates (robust vs fast) so the
receiver does not have to demodulate to learn the mode. Messages are stored in RAM only (no flash writes).

### 4.4 UI flow (stock-style menu, entry "Burst Voice" under the KRAKEN menu)

1. **Record burst**: only on a digital channel (the vocoder TX task needs it, 2c). Hold PTT-sized key (or press-to-start,
   press-to-stop): shows elapsed time, a level bar from the PCM window, and RAM used. Max 15 s at 450 B/s = 6.75 KB
   (fits with the modem state: SRAM budget in 4.6). Stops at the limit. Result: one pending message ("Msg 1: 6.0 s
   voice, 4.2 s after trimming").
2. **Play** (local): feeds the units through the stock playback ring (section 4.5) to the speaker for review. "Re-record"
   and "Discard".
3. **Send burst**: only on an analog channel (the FM Send RAM path), confirms with the projected on-air time ("2.4 s"),
   mode Auto/Robust/Fast, then posts the PTT event exactly as the beacon does (`afsk-tx.md` 1.4: 0x3a4f0 bit 0x08, wait
   call_active bit 3, burst, post 0x10), and obeys `tx_is_inhibited`, TOT and band limits. Optional "send twice".
4. **Received bursts**: list of up to 3 messages in RAM, each line: age, source ID, quality (blocks ok/total), duration.
   Select -> Play, Delete. A toast ("Burst from 1234567, 6 s") on completion when armed.
5. **Settings**: Mode (Auto/Robust/Fast), Arm RX (on/off), Silence trim (on/off), Accept damaged (on/off).

### 4.5 Playback of received units (receive side)

Stock playback is the Record playback (`channel_send_dtmf_autodial_call` @0x0306158c): it reads units from the data chip and
pushes them with IRAM `0x161a8(0, buf, 0x1b, 0x46)` into the 2-slot ring `0x4a73c` (70 ms flow control), after setting
`FUN_00016000(1)`, `FUN_000160c0()`, clearing WORK_MODE bit 7 and `audio_pa_enable_gate(1)`, and tears down with the inverse
(`docs/parrot-recording.md` section 2). The patch calls the same sequence with units from RAM instead of the flash read.
The call is blocking and speaker-only, no TX permission involved (HIGH, static). Run from a patch task, not from `rxvoice`.
On an analog channel the vocoder task is idle and the WORK_MODE restore is the part to check (MED-LOW). Silence gaps are
played by delaying the next push by gap x 60 ms.

### 4.6 Size and RAM budget

| piece | flash |
|---|---|
| TX modulator (RRC, mapper, framer) | about 1.5 KB |
| RX (sync correlator, mixer, MF, equalizer, differential detector, block sync) | about 5-6 KB |
| RS(182,166) enc + decoder (Berlekamp-Massey, Chien, Forney; log/exp tables 512 B) | about 3 KB |
| CRC16, header, container, VAD/trim, timing | about 2 KB |
| UI (5 screens) and stock hooks (playback sequence, recorder tap, PTT post) | about 4-5 KB |
| tables (Q14 sine 256 x 2 B, RRC 33 taps, sync words) | about 1 KB |
| total | **about 17-19 KB flash = 5 sectors**; free spare sectors exist (`docs/flash-allocation.md`, 0x030E7000..0x030FF000), but each claim must be declared in `allocations.json` |

| SRAM (13,312 B window) | bytes |
|---|---|
| message buffer: TX/pending 6.75 KB (15 s) or 3 s x 450 = 1.35 KB per received message x 3 | 6,750 |
| modem state (equalizer taps, delay lines, correlator, RS scratch 0.5 KB) | about 3,500 |
| UI, list, headers | about 400 |
| total | about 10.7 KB; the AFSK decoder state (960 B) must be shared via a union or the burst RX armed only when APRS RX is off |
Reduce to fit: stripped AMBE halves the message buffer; 12 s limit if needed. (HIGH for the window size, MED for the estimates.)

## 5. Receiver robustness and limits (honest list)

- Cliff effect: a D8PSK burst needs about 15-20 dB audio SNR; stock voice is usable far below that. The range of the burst
  is smaller than the range of voice. (MED, simulation.)
- AGC/limiter in the TX chain (LOW, observed only as "amplitude cannot set the twist"): linear modes (QAM) may be damaged;
  PSK with a hard peak limit is chosen.
- Frequency error: both radios share the TCXO class; the carrier is generated in baseband PCM at both ends, so there is no
  RF-frequency to audio-frequency conversion error; the 8 kHz sample clocks of the two HR_C7000s differ by crystal tolerance
  only (tens of ppm): 3 s x 50 ppm = 0.15 ms = 0.3 symbol at 2000 bd over the burst => the equalizer + symbol-clock
  tracking loop is needed (the correlator re-times at each 16-symbol resync word). MED.
- Pre-emphasis in the FD6818 TX path and de-emphasis in the receive chain are not measured at 1.7 kHz +- 1 kHz; the
  equalizer trained on SYNC absorbs a smooth tilt. LOW whether FM Send RAM bypasses FD6818 pre-emphasis; the APRS note says
  the twist is the receiver's, so equalized.
- Sub-audio filters: CTCSS/DCS on the channel: the 300 Hz high-pass in the chain removes content under 300 Hz, which the
  band plan avoids.
- Regulatory (not engineering): a digital voice message that is not decodable by anyone without this patch is "obscured";
  in the amateur service the AMBE+2 vocoder and data modes are allowed to be published; the format here is open. Left as the
  operator's responsibility.

## 6. Verification plan

### 6.1 Host, no radio (all of it can be built and run first)

Add (new files only, mirrored on the AFSK work; as built, these became `patches/kraken_ui/krk_bv_*.c`,
`krk_bv.h` and `tests/test_bv.py`): `patches/kraken_ui/krk_burst.c/.h` (TX modulator, RX demodulator, RS,
framing), `tools/burst_channel.py` (wrapper around `tools/afsk_channel.py`, see below), `tests/test_burst.py`.
1. Round trip: random 27 B units -> framer -> modulator -> PCM -> channel -> demodulator -> units; compare exactly.
   Channels: (B) the smooth model from the sim; (A) the afsk_channel.py cascade verbatim (expected to fail for the single
   carrier, which is the point: it documents why the sounding is needed); noise SNR sweep 12-35 dB; twist -2 to -10 dB;
   sample clock error +-100 ppm; key-up ramp from the model (4.3 ms attack); AGC level changes +-6 dB; DC offset -10.9.
   Targets: block success >=99 % at 22 dB SNR on B, >= 90 % at 17 dB; plus a pooled SNR sweep.
2. RS(182,166): random 8-byte error patterns, burst of 8 consecutive bytes, 9 errors (must detect), compare to a Python
   reference decoder (e.g. `reedsolo`, run with `uv run --with reedsolo`).
3. Silence-trim container: gaps and block indices, lost-block handling, header-copy recovery.
4. Fixed-point checks: Q15 RRC vs float reference (error below -45 dB), equalizer convergence on the SYNC word
   (training MSE below -20 dB), integer overflow guard (the same discipline as `krk_afsk.c`, 64-bit accumulators avoided on the
   CK803S).
5. CPU cost: count of instructions per sample by compiling for the C-SKY target and using the existing QEMU bench (`m17bench`
   style) to confirm the 3-5 MIPS estimate.
6. FEC stripping: the Golay/PN round trip on any real frames when available (a stock recording dump of the data chip, or the
   parrot capture). Until then, only synthetic self-consistency (encode -> decode) is testable, which proves the code but
   not the tables.
7. Capture16 noise floor (RMS 4.0, spectrum) can be used as the noise generator.

### 6.2 What needs hardware (single-action rounds, per the bench-test convention)

| id | item | what the capture tells |
|---|---|---|
| H1 | **channel sounding** (one key, one capture): firmware sends 3.4 s: multitone comb 300-3300 Hz (16 tones), amplitude staircase at 1700 Hz (0, -3, -6, -12 dB), then a 1 s PRBS D8PSK 2000 bd | true H(f) above 2.2 kHz (is there a 1700 Hz notch?), TX limiter/AGC behaviour, SNR, equalizer residual, symbol error rate |
| H2 | mic PCM window: read 0x18000000..0x1800009F with the codec ADC enabled in the TX-vocoder configuration, unkeyed | whether route 2c-1 works; level and sample format |
| H3 | unkeyed encode: units appear in `0x49b18`/ring `0x46b5c` at 27 B / 60 ms | the record side |
| H4 | playback of RAM units through the ring on an analog channel (speaker only) | the receive playback |
| H5 | end-to-end radio pair: send a 6 s burst, measure block success vs distance | range cliff, key-up cost |
| H6 | key-up/squelch timing: lead length needed with squelch forced open | the 0.35 s overhead figure |

H1 can be done today with the existing FM Send RAM TX path and `tools/pcm_capture.py`; it needs no new modem code, only
a generator (tone comb and PRBS output are tens of lines of C).

## 7. Decision

Build order: (1) host modem + framing + RS + tests (no hardware needed; verifies the whole signal chain against
channel models), (2) H1 sounding (decides the single-carrier vs OFDM-lite choice and the achievable mode),
(3) H4 playback and H2/H3 record-side probes, (4) UI.
Estimated effort: modem+framing+tests L-XL; record side XL (the unkeyed encode and the playback sequence are the highest risks);
UI M.
The feature is worth it only if the probes confirm >= 6 kbps and the unkeyed encode route works; otherwise the relay-style
variant (2c route 3) still gives a "forward a stored call as a burst" capability with the same modem, or fall back to not
building F13. Compared with the prior note's 6-10x, the realistic figure is **2-3x** (2.5x typical, 3.2x on a strong link,
4x asymptote for long messages), at about 18 KB flash and 11 KB SRAM.

## 8. Open questions

- Q1: Can the codec-ADC PCM window (0x18000000) and IRQ28 be enabled outside a keyed digital call, and does the stock encoder
  tolerate an unkeyed modem? (determines the record side)
- Q2: Is the 1700 Hz notch of `tools/afsk_channel.py` real? (H1)
- Q3: Does FM Send RAM data pass the FD6818 pre-emphasis/limiter, and what is the audio low-pass above 2.9 kHz? (H1)
- Q4: AMBE+2 interleave table and Golay/PN bit order (needs real frames). Until proven: unstripped 27 B units only.
- Q5: Can playback run on an analog channel without disturbing WORK_MODE (the stock sequence clears bit 7)?
- Q6: Real key-up overhead and squelch behaviour of the receiver when armed.
- Q7: The IRAM vocoder task patches are real-time sensitive (60 ms budget) and live in the flash alias with sector-risk notes
  (`parrot-recording.md` section 3.2); coordinate with the parrot injection work so the same hook serves both.

## 9. Implementation status (host)

Status: the host-verifiable core of BV1 is implemented and tested (2026-10-29). Nothing here has run on a radio. Every number
below comes from `python3 tests/test_bv.py` (17 s, seeds fixed, exit status 1 if a hard gate fails) unless it says "sweep"
(30 payload/noise seeds, run by hand with the same code, not part of the suite).

### 9.1 Files

| file | content |
|---|---|
| `patches/kraken_ui/krk_bv.h` | API and air format |
| `patches/kraken_ui/krk_bv_modem.c` | TX modulator and RX demodulator (D8PSK / DQPSK, 2000 baud, 4 samples/symbol, fc 1700 Hz, RRC 0.35) |
| `patches/kraken_ui/krk_bv_frame.c` | CRC16, GF(256) RS(182,166) and RS(24,16), header / block codecs, message assembler (TX) and disassembler (RX) |
| `patches/kraken_ui/krk_bv_sound.c` | the sounding probe for H1 (about 5 s, one key press) |
| `tools/bv_sounding_analysis.py` | analyses a capture of the probe: H(f), group delay, notch, SNR, level dependence, BV burst through the real receiver, recommendation |
| `tests/test_bv.py`, `tests/bv_glue.c` | the suite (ctypes over the host cc build) |

Style as `krk_afsk.c`: integer only, no libc, no `.data`, constant tables `const`, state zero-initialised `.bss`. The test
compiles all three C files with `-mgeneral-regs-only` (no FP instruction can be emitted) and checks the object files for a `.data` section.

### 9.2 Differences from the section 4 sketch (what the simulation forced)

- SYNC is **64** symbols (two words, one per mode = the mode sense) and is detected by a **soft differential correlation** (octant
  of y[k]*conj(y[k-1]) against the word, 4 timing phases, threshold about 0.5 of a perfect match). A first version with a 32-symbol hard-decision
  detector failed on channel B (the all-pass dispersion corrupts a large share of the raw differential decisions), and a 32-symbol soft version locked 11 symbols early
  on a partial match that reached 0.43 of the peak.
- TRAIN is **192** known symbols (QPSK, in a fast burst the last 96 use all eight phases), equalizer = 24-tap T/2 complex LMS
  (fixed step, shift 10 while training, 13 decision directed), no LS solve. The first 32 symbols only pull in the carrier PLL with the
  equalizer frozen; training shorter than that lost block 0 in about 1 of 8 bursts at 20 dB.
- The symbol-timing loop is Gardner on the matched-filter output (8 fractional banks of the RRC = the interpolator), **wide while training,
  narrow after the header**, and fed forward from the carrier PLL: a sample clock error e moves the carrier by 1700 e and the symbol
  timing by 4 e samples per symbol, so the PLL frequency gives the drift directly. Without the feed-forward the narrow loop needed
  more than 1000 symbols to learn +-0.5 %.
- Decisions are absolute (PLL derotated, nearest of 4 or 8 points) and the bits come from the step between consecutive decisions. The
  8-symbol RESYNC word before every segment is decoded with the data and compared as a histogram of (decision - known): a majority offset
  is a carrier cycle slip and is undone (phase += offset), fewer than 5 of 8 agreeing = lost lock = abort.
- Container as in 4.2, plus: bit 7 of the block index byte is the **end marker** (a burst whose first header is destroyed still ends
  correctly and the tail header copy fills in the message info); `gap` = silence ticks (60 ms) to insert before the block. RX places units by
  the index byte. Header RS(24,16), block RS(182,166), both t = floor(parity/2) byte errors, errors only, 100 % detection of t+1
  errors in 400 trials each.
- The carrier is selectable in 100 Hz steps, 1500-2100 Hz (`krk_bv_set_carrier`; every k-th entry of the 80-entry cosine table). The sounding
  analysis recommends one. The range is clamped there because the RRC spectrum spans fc +-1350 Hz: below about 1400 Hz its lower edge folds through 0 Hz and a 1300 Hz carrier
  failed in an early build.
- Receiver front end: DC blocker, 32-sample energy blocks with a minimum-tracking noise floor (trigger 9 dB over), block AGC while
  searching, search ends when the channel is quiet again or after 1 s without a sync word, then waits for quiet. A false lock is
  rejected after training (EVM above 0.37 -> search again). No false sync in 30 s of white noise at rms 3 and 300, nor on the probe's tone and chirps.

### 9.3 Measured results (200 units = 5400 B = 12 s of voice, 34 blocks)

Air time: fixed 516 symbols (CW 64, SYNC 64, TRAIN 192, header 96+8, tail header 96+8, pad/flush 20) = 258 ms, plus 247 ms
(fast) or 368 ms (robust) per 6-unit block, i.e. 5.25 kbps (fast) or 3.52 kbps (robust) of unit payload against the 3.6 kbps the voice needs. 200 units: **fast 8.70 s**
(1.38x faster than real time), **robust 12.80 s** (1.07x slower than real time: robust only pays with silence trimming). With 30 % silence trimmed (140 units, 24 blocks) fast
is 6.2 s = 1.9x. TX rms 4000 (settable), peak 8500, out-of-band energy 40 dB below the band.

SNR = white Gaussian noise power relative to the burst's total signal power (the whole 8 kHz band; the receiver sees about 1.5 dB better
after the RRC). Channel B = 2nd order high-pass 300 Hz + 2nd order low-pass 2900 Hz + the radio model's all-pass (900 Hz, r 0.75:
0.9 ms delay spread) + one-pole tilt (-3 dB at 2200 vs 1200 Hz); channel A = `tools/afsk_channel.py` verbatim (rms 300, DC -10.9, its 4 LSB
noise floor, the -23 dB notch at 1700 Hz) with the noise added on top. "FRAME OK" = all 34 blocks and all 5400 bytes exact. EVM-SNR =
mean decision error power relative to symbol power as measured by the receiver.

| channel | SNR | fast D8PSK | robust DQPSK |
|---|---|---|---|
| ideal | 25 / 20 / 15 dB | FRAME OK, eq SNR 26.7 / 22.5 / 17.8 dB | FRAME OK, 26.8 / 22.5 / 17.8 dB |
| B | 25 / 20 / 15 dB | FRAME OK, eq SNR 24.5 / 21.2 / 17.0 dB, RS corrected 0 / 0 / 2 bytes | FRAME OK, 24.9 / 21.4 / 17.0 dB, 0 bytes |
| A (afsk_channel, report only) | 25 / 20 / 15 dB | no sync (3/3 lost) | no sync at 25 and 15 dB; synced at 20 dB but 0/34 blocks |
| B, no noise | - | FRAME OK, eq SNR 28.0 dB | FRAME OK, 28.9 dB |

Impairments on channel B at 22 dB, both modes, all FRAME OK: sample clock +0.5 %, -0.5 % (and +0.3 % with another payload), amplitude x0.03
(-30 dB) and x5, DC offset +3000 and -1500 (rms 2400), another payload seed; carriers 1500, 1900, 2100 Hz; two bursts 0.3 s apart (fast 200 units, robust
60 units) decoded back to back in one stream; key-up ramps with a 4.3 ms and a 40 ms attack time constant; the full probe (4.96 s of tone + three chirps, then its burst) decoded 2/2 blocks. Amplitude x0.002
(rms 5 LSB, report only) does not sync.

Byte / symbol error rates: the container has no uncoded path, so every row above is either exact (payload byte error 0/5400, coded-byte
error 0.00 %, except 0.03 % = 2 corrected bytes of 6188 at B/15 dB fast) or a lost block. The raw symbol error rate can be read from the
eq SNR: an 8PSK decision fails when the error vector exceeds 22.5 degrees, i.e. at 17 dB eq SNR the symbol error rate is below 1e-3 and the
RS code corrects what remains; the cliff below shows where it stops.

SNR cliff on B, sweep of 30 payload/noise seeds (bursts fully received / blocks lost of 1020):

| mode | 20 | 15 | 13 | 12 | 11 | 10 | 8 | 7 |
|---|---|---|---|---|---|---|---|---|
| fast | 30/30, 0 | 30/30, 0 | 28/30, 2 | 11/30, 32 | 0/30, 472 | - | - | - |
| robust | - | 30/30, 0 | - | - | - | 28/30, 68 | 25/30, 123 | 11/30, 345 |

So the fast mode needs about 14 dB (all bursts) and the robust mode about 11 dB (all bursts) of audio SNR on a smooth channel; the doc's
section 1a simulation (batch LS equalizer, symbol error rate only) put the D8PSK cliff at 15-20 dB, the LMS receiver with the container is
better than that, but it is the same smooth-channel assumption. **Channel A (the fitted notch) kills the single carrier, as predicted**;
whether that notch exists is exactly what H1 decides.

### 9.4 Sounding probe and analysis tool

`krk_bv_sound_start(scratch324)` + `krk_bv_sound_fill(out256)` (4.96 s, 155 blocks): 1 kHz tone 0.32 s, linear chirp 200-3800 Hz 2.0 s (peak 8000,
flat to 0.1 dB over 250-3800 Hz), 300-3300 Hz chirps at -6 and -12 dB (0.8 s each, TX limiter / AGC), then a 12-unit fast-mode burst with a fixed payload.
Capture the receiving radio with `tools/pcm_capture.py`, then `python3 tools/bv_sounding_analysis.py capture.wav`. It locks to the chirp
by cross-correlation (also measures the clock offset in ppm), deconvolves H(f) on a 0.24 Hz grid (smoothed +-25 Hz), and prints magnitude
and group delay every 100 Hz, the deepest dip in 1000-2400 Hz, ripple, noise floor, SNR, the gain of the -6/-12 dB chirps relative to the 0 dB chirp
(limiter / AGC), the burst through the real receiver, the best carrier in 1500-2100 Hz and a recommendation: FAST (error-free burst, eq SNR >= 13 dB, no
dip deeper than 10 dB), ROBUST (burst partly decoded or eq SNR 8-13), NEITHER (a dip of 10 dB or more: use the multi-tone fallback), INCONCLUSIVE.
Tested on synthetic captures through channel B (27 dB: magnitude within 0.23 dB of the model over 400-3000 Hz, 0.92 ms delay spread, SNR 28.7 dB measured for
27 injected, FAST), B at 9 dB (ROBUST), channel A (notch found at 1702 Hz, -17 dB smoothed, NEITHER), a hard limiter (level dependence reported: +2.4 dB at
-6 dB) and a notch at 1400 Hz (carrier moves to 1500 Hz). Captures from `pcm_capture.py` are int8 << shift: expect a quantization floor in the SNR
figure; the tool says so when the noise rms is below 1 LSB.

### 9.5 API (patches/kraken_ui/krk_bv.h)

```c
/* TX (half duplex; units/gaps must stay valid until the burst is done) */
void krk_bv_set_carrier(int hz);            /* 1500..2100 in 100 Hz steps, both ends must match; default 1700 */
void krk_bv_tx_level(int rms, int peak);    /* PCM rms (default 4000) and clip (default 30000) */
void krk_bv_tx_start(int mode /*BV_MODE_ROBUST|BV_MODE_FAST*/, unsigned msg_id, unsigned src_id,
                     const unsigned char *units /*nunits*27*/, int nunits, const unsigned char *gaps /*per block or 0*/, int flags);
int  krk_bv_tx_fill(short *out);            /* 256 samples per call (one FM Send RAM half); 1 = more to come, 0 = this was the last block */
void krk_bv_tx_stop(void);  int krk_bv_tx_active(void);  unsigned krk_bv_tx_samples(int mode, int nunits);
/* RX */
void krk_bv_rx_begin(unsigned char *buf, int cap_bytes);   /* arm; buf = the shared 5632 B payload buffer */
int  krk_bv_rx_feed(const short *x, int n);  /* any n; returns BV_EV_SYNC|HEADER|BLOCK|DONE|ABORT mask; deaf while a TX is active */
const krk_bv_info_t *krk_bv_info(void);     /* header, blocks_ok/bad, per-block ok[] and gap[] (silence ticks), rs_corrected, evm_q16, resync_err, mode */
/* units land at buf + 27 * (6 * block + k); a lost block leaves its slot untouched (zero after rx_begin's reset) */
/* framing (usable alone): krk_bv_rs_encode/decode, krk_bv_crc16, krk_bv_hdr_*, krk_bv_block_*, krk_bv_msg_begin/next, krk_bv_rxmsg_begin/expect/segment */
/* H1: krk_bv_sound_start(scratch324), krk_bv_sound_fill(out), krk_bv_sound_payload(out) */
```

### 9.6 Cost

Code: host `-Os` (arm64) text + constant tables: modem 8.7 kB, framing 4.6 kB, probe 0.7 kB = **14.0 kB, of which about 2.0 kB are tables** (RRC bank 528 B, cos80 160 B,
cos256 512 B, GF log/exp 766 B, sync words). C-SKY's 16/32-bit encoding is typically 25-35 % denser, so the estimate is **about 9.5-11 kB** at `-O2`
(a cross compile was not possible here; to be measured when it is built). RAM: **1361 B** of `.bss` (modem 960: matched-filter ring 256, sync history / equalizer union 288, TX and loop
state; framing 377: shared 184 B segment buffer, 80 B per-block tables, header; probe 24), no stack beyond about 150 B (RS decode); the 5632 B payload buffer is the caller's.

CPU (multiplies per sample; 1 MAC is about 4-5 C-SKY instructions with the loads):
- RX receiving: per symbol 132 (two matched-filter outputs, I and Q, 33 taps) + 96 (equalizer) + 96 (LMS update) + about 20 (timing, PLL, derotation, statistics) = 344, i.e.
  **86 per sample plus 3 for gain and mixer = 89 multiplies/sample = 0.71 M/s**, about **3 MIPS**. Idle (waiting for a burst): DC blocker and block energy, about 12 instructions per sample = 0.1 MIPS.
- RX searching for sync (starts when the energy trigger fires, ends when the channel goes quiet, at most 1 s): 66 (matched filter) + 128 table lookups per sample, about **8 MIPS**, i.e. about 3 ms of an
  84 MHz CPU per 32 ms block; after sync it drops to the 3 MIPS above.
- TX: 2 x 8-9 MACs for the RRC + 4 = about 21 multiplies per sample = 0.17 M/s, about **0.8 MIPS**.
The host build processes the 12.8 s robust burst in well under 0.2 s.

### 9.7 What the on-radio integration has to do (later, together with the parrot frame capture / injection)

1. Run H1 first (probe on one key press, `tools/bv_sounding_analysis.py` on the capture). If the tool says NEITHER, stop: the single carrier does not work and 1a's multi-tone fallback is next.
   If it recommends another carrier, call `krk_bv_set_carrier` on both ends. The fast/robust choice and a link margin of about 6 dB over the cliff (14 / 11 dB) come from the same capture.
2. RX: feed every FM Receive RAM half (256 samples, 8 kHz s16) to `krk_bv_rx_feed` from a task, with the squelch forced open while armed (as the APRS decoder does). Do not rely on the
   32 ms CW lead for squelch opening: the APRS beacon needed 480 ms, so either hold the squelch open (H6 measures it) or raise `NCW` in `krk_bv_modem.c` (every symbol is 0.5 ms). On `BV_EV_DONE` (or `BV_EV_ABORT`) read
   `krk_bv_info()`: `ok[b]` says which 6-unit groups arrived, `gap[b]` how many 60 ms ticks of silence precede block b; a lost block is concealed by the caller (repeat the last unit, fade). Copy
   or consume the payload buffer before the next `krk_bv_rx_begin`. `rx_begin` zeroes the info, not the buffer.
3. TX: `krk_bv_tx_start` once, then one `krk_bv_tx_fill` per FM Send RAM half in the same loop that refills the AFSK beacon (v39 path); the first half already contains the CW lead, so insert the
   key-up delay (PA ramp, H6) as silent halves before it. Release PTT when `krk_bv_tx_fill` returns 0 and the last half has played (one more half of silence is in the block). Call `krk_bv_tx_stop` on abort.
   `krk_bv_tx_samples()` gives the exact air time for the confirmation screen.
4. Units: the modem and container carry opaque 27-byte units; the parrot capture side delivers them, the playback side consumes them in order, inserting `gap` ticks. The `flags` byte (trimmed, FEC-stripped,
   last pass) and `tick` are carried but unused here. The silence-trim decision (VAD) is not implemented: the caller passes the per-block `gaps[]`.
5. Linker: three new objects, about 11 kB flash (declare it in `allocations.json`) and 1.4 kB SRAM, in their own `.bss` section like `.bss.afsk`; the state may share an overlay with the AFSK decoder if
   only one of them is armed at a time (both are armed explicitly from the menu). Nothing else is required: no interrupts, no allocation, no libc, calls are non-blocking and bounded (the worst case is a search block
   at about 8 MIPS, a 256-sample call takes about 3 ms).
6. Not covered by anything here and needing hardware: the true audio channel above 2.2 kHz (H1), the TX limiter's effect on PSK peaks (the probe's -6/-12 dB chirps measure it), the real key-up ramp and squelch behaviour
   (the suite shows a one-pole attack of 4.3 ms and even 40 ms is tolerated), real FM noise (impulsive, triangular spectrum; the tests use white noise), and the AMBE side (capture, playback, FEC stripping).

## 10. AMBE FEC stripping and silence trim (host)

Status (2026-10-29): `patches/kraken_ui/krk_ambe.c/.h` and `tests/test_ambe.py` (+ `tests/ambe_glue.c`), all host, nothing has run on a radio. `python3 tests/test_ambe.py` (about 10 s) passes. This replaces the
"from memory" sketch of section 2d with an implementation whose tables are cross-checked. krk_bv*.c is untouched.

### 10.1 Design

One 20 ms frame = 72 bits = C0 (24, extended Golay(24,12)) | C1 (23, Golay(23,12) XOR PN) | C2 (11, raw) | C3 (14, raw); 12+12+11+14 = **49 info bits**, ordered as mbelib's `ambe_d[0..48]`
(C0 data, C1 data, C2, C3, each MSB first). A 27-byte unit holds 3 frames. Stripping sends 49 of 72 bits (2450 of 3600 bps, x1.47); the receiver Golay-encodes, applies the PN, interleaves, and hands the stock
decoder a valid unit. Decoding corrects up to 3 errors in C0 and in C1 and counts them (`krk_ambe_stats_t.c0_fixed/c1_fixed`, usable as the F3 link-quality number; 4 errors in C0 are flagged through the
parity bit, `stats.bad`).

* Golay: g(x) = x^11+x^10+x^6+x^5+x^4+x^2+1 (0xC75), systematic (data << 11 | remainder). Decoder: syndrome, then the unique error pattern of weight <= 3 (the code is perfect: 23 + 253 + 1771 = 2047 syndromes);
  about 2000 compares worst case, 3 frames/60 ms is nothing. C0's 24th bit = even parity over the 24 bits (mode switchable: `krk_ambe_set_parity`, auto-detectable: `krk_ambe_detect_parity`).
* PN (mbelib `mbe_demodulateAmbe3600x2450Data`, same LCG as the P25 IMBE spec TIA-102.BABA): `pr[0] = 16 * u0` (u0 = the 12 C0 data bits), `pr[i] = (173 * pr[i-1] + 13849) mod 65536`; C1 bit j (22 first on air, down to 0)
  is XORed with `pr[23-j] >> 15`, applied to the whole 23-bit C1 codeword (data and parity).
* Interleave: DSD `dmr_const.h` rW/rX/rY/rZ: dibit i of 36 (first bit MSB) puts its high bit at codeword rW[i], bit rX[i] and its low bit at codeword rY[i], bit rZ[i]. Stored as one 72-byte const table.
* Burst layouts of the 27-byte unit (not hardware-confirmed): **L1** = 3 consecutive 72-bit frames in the air interleave (the DMR voice burst has 108 dibits + 24 dibits of sync/EMB in the middle of frame 2;
  with the 48 sync bits removed the 216 payload bits are simply frame1|frame2|frame3, so "frame 2 split around the sync" becomes contiguous), **L2** = 3 consecutive de-interleaved frames C0|C1|C2|C3, MSB
  first. Because the bit order in the bytes could also be LSB first, the code carries **L1-lsb** and **L2-lsb** (layouts 3, 4). Layout 0 = RAW (no stripping).
* Detector `krk_ambe_detect_layout(units, n)`: per layout, count frames whose C0 23-bit word has syndrome 0 without any correction. A wrong layout hits by chance with p = 1/2048 per frame (not 1/16:
  the 11 syndrome bits must vanish); the right one on about 79 % of frames at 1 % BER. Accept when score >= 250 per mille, >= 2 valid frames and valid count >= 2 * runner-up + 1, else RAW.
* Silence: b0 (the 7-bit pitch index = info bits 0-5 and bit 48; mbelib `mbe_decodeAmbe2450Parms`: 120-123 erasure, 124-125 silence, 126-127 tone) in {124, 125}. The canonical silence vector
  `F8 01 A9 9F 8C E0 80` (b0 = 125, DSD/DSD-FME silence) encodes to **`B9 E8 81 52 61 73 00 2A 6B`**, which is MMDVM's `DMR_SILENCE_DATA` in air order: this was checked by the test and is the
  strongest evidence for the tables (Golay, even parity, PN and interleave all have to be right at once). A gain-based "near silence" is not defined: the gain index b2 is differentially coded and
  its bit positions inside the 49 bits are not something I can cite, so no such heuristic is shipped.
* Payload (`krk_ambe_pack` / `krk_ambe_unpack`): byte 0 = number of units n (<= 255); with trim the run lengths of the 3n frames (alternating non-silent / silent, starting non-silent; a run is bytes of 255 plus one
  byte < 255), then the 49-bit infos of the non-silent frames MSB first. Silent frames come back as the canonical silence frame, everything else bit-exact (tested for all four layouts, n = 1..255, with and
  without trim, empty, all-silent, all-speech, every truncation rejected). A silence frame with b0 = 124/125 but other parameters is replaced by the canonical one: the decoder outputs silence for both.

### 10.2 Measured gains (`tests/test_ambe.py`, 12 s = 200 units, 600 frames; air time from `krk_bv_tx_samples`, verified against the samples `krk_bv_tx_fill` really produces)

Stripped payload = 1 + 3675 B (no trim) -> 137 units of 27 B. 70 % activity = 1.4 s speech / 0.6 s pause cycles ("raw+block trim" = the container's present mechanism: silent 60 ms units dropped, a block
of 6 time-consecutive units, partial blocks pay the full 182 B):

| 70 % activity | payload B | fast (D8PSK) | robust (DQPSK) |
|---|---|---|---|
| (a) raw units, no trim | 5400 | 8.70 s | 12.80 s |
| (a') raw + block trim (existing) | 3888 | 6.21 s | 9.12 s |
| (b) stripped | 3676 | 5.98 s (1.45x vs a) | 8.77 s (1.46x) |
| (c) stripped + frame trim | 2586 | **4.26 s** (2.05x vs a, 1.46x vs a') | **6.18 s** (2.07x, 1.48x) |

Choppy speech (0.6 s / 0.26 s): (c) 4.48 s fast / 6.56 s robust; long spurts (3.0 s / 1.3 s, 75 %): 4.74 s / 6.91 s. So stripping + trim lets the **robust** mode run faster than real time (6.2 s for 12 s), which
raw units cannot (12.8 s). The fixed 258 ms overhead and the block FEC are unchanged. Real speech has more short pauses than the synthetic cycles, which helps (c) more than (a').

### 10.3 What the radio integration must do

1. First captured/received burst (or the first recording read via 0x172cc): `l = krk_ambe_detect_layout(units, n)` with n >= 10 clean units (n = 1 works on clean data), then
   `krk_ambe_set_parity(krk_ambe_detect_parity(units, n, l))`. Cache `l`; the layout is a property of the C7000 window, not of the message. l == RAW: send raw units with the existing path. Re-detect when
   `krk_ambe_stats_t.bad` grows (a layout error looks like every frame having corrections).
2. TX: `n = krk_ambe_pack(units, n, l, trim=1, buf, cap, &st)`; hand `buf` to `krk_bv_tx_start` as ceil(len / 27) "units" (zero pad the tail). The container then does not use `gaps[]` (they would be 0).
   Payload cap: 255 units = 12.75 s of voice = about 4.7 kB stripped, which fits the 5632 B shared buffer; larger messages are split.
3. RX: after `BV_EV_DONE` concatenate the received 27-byte chunks, `krk_ambe_unpack(...)` into a 27 * n byte unit array, feed the units to the playback injection (0x172f8) at 60 ms each. A lost block
   corrupts a stretch of the packed bitstream (49-bit frames have no resync, and the run-length bytes shift everything behind them); with trim, a lost block therefore damages to the end of the message,
   so combine with the LASTPASS retransmission or send trimmed payloads in independent segments of <= 6 blocks (the packer works on any subset of units; each segment is a self-contained payload with its own n byte).
4. Golay corrections only occur when the recorded units come from a received DMR voice burst (channel errors); units the radio encoded from its own microphone are clean.

### 10.4 Header flag proposal (krk_bv header byte 10 `flags`, 8 bits, defined: 1 TRIMMED, 2 STRIPPED, 4 LASTPASS)

* bit 1 `BV_FLAG_STRIPPED` (existing) = the payload chunks are a `krk_ambe_pack` stream (49-bit info), not raw units.
* bit 0 `BV_FLAG_TRIMMED` (existing; today: gaps[] used) = for a STRIPPED payload: run-length bytes present (frame-level silence trim). With STRIPPED the block `gap` fields are 0.
* new bits 3-5: `BV_FLAG_LAYOUT_SHIFT 3`, 3-bit layout (0 RAW, 1 L1 air, 2 L2 plain, 3 L1-lsb, 4 L2-lsb); bits 6-7: parity convention (0 even, 1 odd, 2 zero). The receiver needs these to re-interleave.
* Needed change in krk_bv (not made here): `krk_bv.h`: add `BV_FLAG_LAYOUT_SHIFT`/`BV_FLAG_PARITY_SHIFT` and masks; the header already round-trips the whole `flags` byte (`krk_bv_frame.c` lines 157/173), so
  `krk_bv_msg_begin(.., flags, ..)` needs no code change. `nunits` in the header = number of 27-byte chunks of the packed stream (the true unit count is byte 0 of the stream).
  The tail pad bytes of the last chunk are zero and ignored. Test: add a header round trip with flags = 0x3F in `tests/test_bv.py`.

### 10.5 Confidence and what stays unverifiable without hardware

* Tables: HIGH for Golay generator, PN, interleave and the silence constant (three independent public sources agree bit-exactly through one frame: DSD rW..rZ + mbelib PN/Golay + MMDVM silence;
  the interleave is also a pure permutation of the 72 positions). The extended parity convention is verified by the same vector (even).
* Not verifiable here: (1) which of the 4 layouts (or none) the HR_C7000 window uses: the detector decides on real data, RAW is the fallback; (2) whether the 27 B really are 3 whole frames of one burst in
  stream order (frame order inside the unit, and the unit boundary relative to the superframe); (3) whether the stock decoder accepts re-encoded frames identically (it should: bit-exact for clean
  frames); (4) whether the C7000 emits corrected or raw channel bits on the record side (affects only the c0_fixed statistics); (5) real silence statistics of the vocoder (b0 124/125 is the
  mbelib convention; the AMBE+2 chip's own idle frames might use the erasure/other patterns, in which case less is trimmed, never anything wrong).

## 11. Implementation (2026-10-29): the Burst Voice screen on the radio (blob `kraken_ui4`)

Status of the numbered work items. "QEMU" = verified on the emulated CK803S with the stock calls intercepted (`tests/f13_checks.py`, 100+ checks, part of `tools/build.sh`); nothing here has run on a radio.

| item | what | status |
|---|---|---|
| 1a | message from the operator's own voice, unkeyed (stock encoder run without PTT, tapped through the vframe TX provider) | BUILT (key `1`), sequence QEMU-verified; **whether the encoder produces bursts without a key-up is unknown** (section 11.4), the key is the one-key probe that settles it |
| 1b | relay: the last received DMR call (vframe RX sink, the parrot's capture) as the message | BUILT (key `2`), sink and completion QEMU-verified |
| 2 | send on an analog channel: pack (layout auto-detect, stripped + silence trim, raw fallback) -> BV1 modem -> FM Send RAM; fast default, robust selectable (`3`) | BUILT (`OK`, confirm), whole sequence and every gate QEMU-verified; modem PCM identical to the host build |
| 3 | receive while the screen is open on an analog channel: FM Receive RAM -> `krk_bv_rx_feed`; on DONE units rebuilt (`krk_ambe_unpack`) into the message with sender ID, age, blocks ok / RS / EVM | BUILT, QEMU-verified with the host-generated burst fed from memory (the RAM window itself is not emulated) |
| 4 | playback to the speaker: the stock Record playback sequence with units from RAM (`4`); fallback "relay as DMR call" (`5`) | BOTH BUILT. Playback: sequence copied from the disassembly of `0x0306158c`, QEMU-verified call by call; audible result needs a radio (H4). DMR relay: gates, provider and release QEMU-verified |
| 5 | hardware gate H1: the sender's key `7` sends the sounding probe, the receiver's key `8` streams the received PCM to the cable, `tools/bv_sounding_analysis.py` decides | BUILT; procedure in 11.8 |

### 11.1 Files, linking, flash

| file | content |
|---|---|
| `patches/kraken_ui/krk_bvscr.c` | the screen (KRAKEN slot 7), key handling, FM send/receive loops, gates, playback, DMR relay, record probe, watchdog entry |
| `krk_bvmsg.c/.h` | PURE message pack/unpack between units and burst chunks (host-tested): `krk_bvm_pack`, `krk_bvm_unpack`, constants |
| `krk_bv_modem.c`, `krk_bv_frame.c`, `krk_bv_sound.c`, `krk_ambe.c` | sections 9 and 10, unchanged except `BV_FLAG_LAYOUT_SHIFT/PARITY_SHIFT` in `krk_bv.h` (header flags 3-5 layout, 6-7 parity: section 10.4, header round trip with 0x3F tested) |
| `kraken_ui4.ld` | blob 4 at `0x030e8000`: the screen descriptor FIRST (slot 7 of `krk_ext.c` is the constant `KRK_EXT4_LO`), a one-entry hook table at `0x030e8020` (aligned), then code; state sections below |
| `kraken_ui.patch.json` | blob `kraken_ui4` (built AFTER `kraken_ui2`, `-Os` because `-O2` does not fit 24 KiB), defsyms from blob 1 and blob 2; claims `kraken_ui4_code/state` |
| `krk_core.c` | `build_list()`: an invalid extension entry is skipped (`continue`) instead of ending the list (`break`): a missing blob 4 (erased flash) hides no later row (QEMU-tested on an image with blob 4 erased: only the Burst Voice row disappears) |
| `krk_periodic.c` | one call: `bv_watchdog()` reads the hook table at `0x030e8020`, range-checks it against `KRK_EXT4_LO/HI`, calls `krk_bv_wd_tick` (erased flash = 0xffffffff is skipped) |

Flash: blob 4 = 21878 B of 24576 (`out/kraken/steps/*/summary.json`; text 21450 after `-Os`: modem 8.7 KB at -O2 would not fit), blob 2 +~30 B (the hook call). The blob needs its own `memcpy` (the framing's struct copy; defined in `krk_bvscr.c`, no loop-to-memcpy transform: `-fno-tree-loop-distribute-patterns`).

### 11.2 SRAM and ownership of the shared buffer

| where | bytes | what |
|---|---|---|
| `kraken_ui4_state` `0x18007000..0x18007400` | 944 modem + 16 `W` = 960 of 1024 | modem state (`.bss.bvm`: zeroed at screen enter, only touched while we own the buffer); `W` = watchdog deadline + the ownership flag (magic guarded) |
| tail of `kraken_bigbuf` `0x18006e00..0x18007000` | 448 of 512 (`BVT_SIZE`) | framing + sounding + AMBE statics (384) and the screen state `S` (64) |
| `kraken_bigbuf` `0x18005a00..` | 5102 of 5120 below the tail | A = the message (92 units = 5.52 s, 2484 B), B = packed payload (TX) or received chunks (RX) 2106 B = 78 chunks = 13 blocks, SC = 512 B of one 256-sample half (TX fill, RX read) |

The modem state (1361 B in section 9.6) did not fit 1 KiB, so the framing part went to the tail of bigbuf and the message length is capped at 92 units (the tail overlaps the last 400 bytes of the parrot's 5616-byte capture, which is why ownership is exclusive). **Ownership** is the owner byte of `krk_vf_big_claim(owner)` (`krk_vframe.c`, owner 1 = parrot, 2 = Burst Voice): the screen claims at enter and releases on Back **unless a message exists**: then the buffer stays owned (and the screen state `S` with it) so the message can be carried to another channel (capture or record on a digital channel, Back, change channel with the stock keys, come back, send on analog); key `0` clears the message and Back then releases the buffer, and while the message is kept the parrot's arm key is refused (PR_BUSY). While the parrot is armed the claim fails and the screen shows "Parrot is armed" and touches nothing in the buffer (`W.own`, in ui4 `.bss`, is the only state written then); while Burst Voice is open the parrot's arm key is refused (its claim fails, PR_BUSY). A warm reset clears the owner byte, `W` (tick counter below its stamp) and the vframe session. Tested both ways, including "nothing in the buffer touched".

A message of 92 units packs (stripped, layout detected) to at most 63 chunks (1 + 92 x 147 / 8 bytes, 4.8 s fast / 7 s robust before trimming); RAW units (no layout found) are limited to 78. Longer relay captures are cut at 92 units.

### 11.3 Screen

KRAKEN > Burst Voice. 19 x 10 text: line 0 mode + channel kind, 1 receive / relay state, 2 message length and origin (relay / rx / mic), 3 sender ID and age, 4 estimated air time and AMBE layout, 5 last received burst (blocks ok/total, RS bytes corrected, EVM in dB), 6 status, 7 lead or the confirmation prompt, 8-9 key help.
Keys: `OK` send burst (analog; second press within 3 s confirms), `1` record unkeyed (digital), `2` relay capture on/off (digital), `3` fast/robust, `4` play to the speaker, `5` retransmit as a DMR call (second press confirms), `6` dump the message to the UART as hex, `7` H1 send sounding (second press confirms), `8` H1 listen + stream, `9` load a deterministic NON-SILENT test message (92 units from a fixed-seed PRNG, AMBE+2 L1 layout, origin mic, no RF; analog OK sends a 63-chunk / 11-block burst and the receiver's `6` dump must equal the sender's, host test in `tests/test_f13.py`), `0` clear, Up/Down key-up lead 0-960 ms in 32 ms steps (default 320 ms: the receiver's squelch opens after the key-up, the APRS beacon needed 480 ms; H6 measures it).
Frames: `out/qemu/f13` (boot to the screen on the factory analog channel: "RX listening"), `out/qemu/f13msg` (a poked received message: `tests/data/f13_pokes.txt`).

**Since 2026-11-06** (section 13) the screen is the first child of Settings > Channel Set > Burst Voice, and Mode, Key-up Lead and Relay Capture are stock option levels beside it, stored in flash. Keys `2`, `3` and Up/Down no longer change anything (the status line says `Change in the menu`); keys 6-9 were already gone on 2026-11-02. The keys today: `OK` send (analog, second press confirms), `1` record unkeyed (digital), `4` play, `5` retransmit as a DMR call (second press confirms), `0` clear. Line 7 shows the lead (`lead 320ms`) or the confirmation prompt; the help rows are `OK:send 1:rec 0:clr` / `4:play 5:dmr`.

### 11.4 Message sources

**1b relay (digital channel).** `2` installs `cap_sink` as the vframe RX sink (`krk_vf_rx_sink`). The sink runs in the decoder task (prio 15): while `0x3bd50` bit 1 (RX voice) is set it stores each 27-byte burst at A (first burst of a call: invalidate the previous message, note `0x3bd34` the source ID), when the bit is clear the call is over and the screen tick turns the capture into the message (>= 5 bursts), capped at 92. The H1/H2 hook table of blob 2 is not used (it belongs to the parrot): the sink derives the call boundary from the stock RX-voice bit instead (`docs/parrot-tx.md` 6.1, HIGH for the bit, never observed on air).

**1a unkeyed record (digital channel, key `1`) - research result.** Statically established:
- the TX encoder task (IRAM `0x2e5a0`, prio 16) runs when `[0x48160+0x1728] == 1`; the stock call setup `audio_vocoder_buffer_flush2` (`0x03021a70`) sets it with IRAM `0x16088` (which also sets bit 4 of `0x11000080` and `0x110003b0`) and clears it with `0x160b0`; the keying (RF) is a separate chain (PTT event -> `radio_init_sequence`), nothing in `0x16088` keys the transmitter;
- the encoder emits a 27-byte burst every 60 ms to `0x49b18` and hands it to `0x172f8` (the vframe TX hook sites): a provider that copies the stock burst and overwrites it with the silence burst gets the microphone units without anything reaching the modem window but silence;
- the two 7-byte reads, the stock `0x2e37c` TX-ring reader (70 ms pend, always returns 0, timeouts are not distinguishable) and the TX-record flag `0x15fa4(1)` are NOT used for this reason.
Blocker (not resolvable statically): the encoder pends once per 10 ms PCM block on a semaphore posted by the audio interrupt (IRQ28, `PCM_wr_data_interp`), and nothing found shows whether the codec ADC capture runs without the TX call setup (the TX audio path is configured by the keying sequence; its codec registers were not traced); QEMU has no audio ADC. So the built `do_record` is: gates (digital channel on the selected side, no RX, no TX voice, nothing keyed) -> `krk_vf_tx_begin(rec_prov)` -> `0x16088` -> up to 6.4 s of waiting (stops when full, on any key, or if anything keys the radio) -> `krk_vf_tx_end`, `0x160b0`, AUDIO_CONTROL and `0x110003b0` restored. **Never keys the transmitter** (no PTT event is ever posted; QEMU checks it). Hardware probe H2/H3 = this key: a quiet room then speaking; the status line says `Recorded` (units captured: the layout shown on line 4 and `6` dump show if they are valid AMBE and loud), `no encoder bursts` (the TX hook was never called: the encoder does not run unkeyed = blocker confirmed; the fallback is the relay source), or `no frames kept`.

### 11.5 Send (analog) and its safety layers

`OK`, second press within 3 s. `krk_bv_prepare` -> `krk_bvm_pack`: layout detection on the units (`krk_ambe_detect_layout`); a layout found gives a STRIPPED + TRIMMED payload with layout and parity in the header flags, none gives the raw units (<= 78). Then, in the order of the proven v39 beacon (`krk_aprs_tx.c`): receive disarmed, dual-watch hold, PTT press (`0x0003a4f0` bit 0x08), wait for `call_active` bit 3 (2 s), 100 ms, FD6818 REG_47H = 0x6740, GPIO 0x2E low, FM_DEV_COEF 0x40, AUDIO_CONTROL bit 0; per FM Send RAM half (256 big-endian samples): lead silence halves, `krk_bv_tx_fill`, silence after the end until three halves have gone out; restore every register, release (0x10), wait for TX down.

| layer | where | tested |
|---|---|---|
| gate | analog channel on the SELECTED side (`krk_channel_analog` + flags), band flag `0x3e31b` bit 5, `tx_is_inhibited`, stock RX active, RX voice, receiver in a burst, buffer owned | each individually (no PTT post in any refused case) |
| confirm | a transmit key needs a second press within 3 s; any other key drops it | yes |
| budget | air time (`krk_bv_tx_samples`) + lead + 1 s; the loop stops with "TX timeout" | no clock in the model: the code path is the stall exit; the DMR twin is tested with a moving tick |
| abort | any key after the starting key was seen released (polled every 16 ms) | yes |
| restore | every exit restores AUDIO_CONTROL, FM_DEV_COEF, REG_47H, GPIO 0x2E and posts the release | yes |
| watchdog | `W.txing` + deadline (key wait 3 s + budget + 1 s) armed before the press, checked by blob 2's 1 Hz maintenance hook: past the deadline it re-posts the release every pass | yes (via `krk_hook_maint_tick`; before the deadline and with no send in flight it posts nothing; noise or warm reset clears it) |
| stall | no half switch for 300 ms: modem stalled, release | yes |

The transmitted audio is the section-9 modem output; level 4000 rms like the probe. The TX path normalises the level (docs/fm-send-ram-tx.md).

### 11.6 Receive

While the screen is open on an analog channel the receiver is armed (GPIO 0x2F low = FM Receive RAM carries the demodulated audio, re-asserted every half because a squelch opening re-drives it; restored on every exit, before any TX and during playback). `bv_tick` consumes finished halves for about 6 ms per runner tick (`tick_ms` 1) into `krk_bv_rx_feed`; no redraw while the receiver is searching or receiving. On DONE `krk_bvm_unpack` rebuilds the units (stripped: `krk_ambe_unpack`; raw: copy, a lost block becomes silence units) into A and the screen shows sender, age, blocks, RS bytes and EVM. Background arming (screen closed) was not built: the buffer is exclusive and the receiver needs the screen task's loop.

### 11.7 Playback and DMR relay

**Speaker (key 4).** The stock Record playback, `channel_send_dtmf_autodial_call` @`0x0306158c`, disassembled: wait for `[0x3e31f] & 2` to clear; zero the 7-byte block `0x16064(zeros, 7)` (the privacy key block: recorded units are raw); `0x16000(1)` (decoder reads the playback ring instead of the modem); `0x160c0()`; `0x3055850(13)` (speaker volume); `MODEM[0x100] &= ~0x80`; `audio_pa_enable_gate(1)` (`0x0301d6cc`); per 27-byte unit `0x161a8(0, unit, 27, 70)` (copies into the 2-slot ring `0x4a73c`, 70 ms pend as flow control); teardown `0x16000(0)`, `0x16064(0x33825, 7)`, `MODEM[0x100] |= 0x80` ONLY on an analog channel, `0x30230bc([0x3ecfe])` (volume restore), `0x16104()`, PA gate off. `do_play` is that sequence minus the dialog drawing, run in the screen task (blocking about 60 ms per unit, key aborts every 4 units, 150 ms drain at the end), refused during RX or TX. It is speaker-only: no TX permission, no modem write. Verified call by call under QEMU (order, arguments, the 27 bytes of every push, bit 7 clear while pushing). Unverified: that the decoder task accepts the pushes when no call is active on an ANALOG channel (the stock sequence also runs on analog, it clears/sets bit 7 for it), and the audio itself (H4).

**DMR relay (key 5), the fallback.** The parrot's path in a blocking function: gates (digital channel on the selected side, no stock cipher, band flag, not inhibited, channel idle, not capturing), `krk_vf_tx_begin(dmr_src, ...)` with 2 silence bursts, the units, LAST on the last one; PTT press, wait <= 3 s for `0x3bd50` bit 0, then until the layer ends the session (it posts the release itself after the last burst) with the same budget (bursts x 60 ms + 1 s) and any-key abort; up to 2 s for TX down with a release every 500 ms; `krk_vf_tx_end`. The watchdog guards it too. Stock DMR radios can hear this (the same units as a normal call).

### 11.8 Hardware tests (each one action per round, as in section 6.2)

**H1 sounding (decides fast/robust and the carrier).** Host: `uv run --with pyserial python3 tools/pcm_capture.py --seconds 40 cap.wav` (programming cable of the receiving radio, 115200).
1. Receiving radio (analog channel, squelch/CTCSS as the APRS tests): KRAKEN > Burst Voice > `8` (it streams 20 s: line 6 "Listening 20s").
2. Sending radio (same analog simplex channel, dummy load or near the receiver): KRAKEN > Burst Voice > `7`, `7` again within 3 s (the confirmation). It transmits the probe once (about 5 s plus the 320 ms lead, `krk_bv_sound`).
3. `python3 tools/bv_sounding_analysis.py cap.wav` -> H(f), notch, SNR, limiter linearity, the burst through the real receiver, the recommendation FAST / ROBUST / NEITHER and the carrier. The receiver's screen also decodes the probe's closing burst live (blocks ok / RS / EVM on line 5).
**H5 end to end.** The sender needs a message: on a digital channel `2` and let a third DMR radio call (or `1` after H2), Back (the message is kept), select the analog channel with the stock keys, open Burst Voice again and `OK`, `OK`. The receiver (screen open, analog): the line "burst received" with sender and blocks; `4` plays it; `6` dumps it. Compare the dump with the sender's. **H4** is `4` alone with any message (also a relayed capture). **H2/H3** is `1`. **H6** key-up lead: Up/Down and repeat H5.

### 11.9 Tests

`python3 tests/test_f13.py` (host, a few seconds): the layout constants against the linker script and claims, header flags, pack/unpack for all four AMBE layouts and the raw path (sizes, flags, 79 raw units refused), units -> pack -> modem -> receiver -> unpack bit exact in fast and robust mode (re-pack identical for the silence-substituted frames), a destroyed block (raw: silence units, the rest exact), and the golden data of the in-target run (`tests/data/f13_burst.pcm`, `f13_golden.json`; `--regen` rewrites them). `python3 tests/test_bv.py` and `tests/test_ambe.py` still pass.
`tests/f13_checks.py::check_f13` (called from `kraken_ui_test.py`, QEMU + gdb, about 20 s): blob placement and byte equality with the ELF, descriptor, slot 7, hook table, claims, the missing-blob list on a second machine, ownership vs the parrot, the modem on the CK803S producing the SAME 6400 samples as the host build (hash of the first six blocks and the whole burst), the receiver decoding the host burst on target and the screen storing it, every TX gate, the confirm key handling, the complete analog send (one press before the release, registers restored, abort rules, never-up), the watchdog through blob 2's real hook, the relay sink, the DMR provider and send, the playback sequence, the record sequence, exit. The FM RAM windows are not writable from the debugger and not modelled by this image, so what reaches the half buffers is covered by the identical `krk_bv_tx_fill` PCM plus the visible 16-bit conversion; the baseband-window QEMU model is not needed. Note for the test code: setting `TCP_NODELAY` on the gdb socket (`nodelay()` in `f13_checks.py`) cut a target call from 0.8 s to 3 ms; `mi_rng_test.start_qemu` would profit too.

### 11.10 CPU (instructions per 256-sample block, single-stepped on the CK803S model)

`python3 tests/f13_checks.py --cpu` (about 5 minutes: single-steps representative blocks over the QEMU gdb stub; the exec-trace method of the other benches misses chained translation blocks and was not used). The golden 12-unit fast burst, the modem compiled `-Os` for the CK803S:

| block (256 samples = 32 ms) | instructions | per sample | MIPS at real time | share of 84 MHz at 1 instruction/cycle |
|---|---|---|---|---|
| TX fill, CW + sync | 53,542 | 209 | 1.7 | 2 % |
| TX fill, data | 56,962 | 223 | 1.8 | 2 % |
| RX feed, idle (no signal) | 10,190 | 40 | 0.3 | 0.4 % |
| RX feed, searching for sync (energy trigger fired) | 990,675 | 3870 | **31** | **37 %** |
| RX feed, receiving (after sync) | 255,456 | 998 | 8.0 | 9.5 % |

Real time at 84 MHz is feasible but the receive search has little headroom: 11.8 ms of each 32 ms block at one instruction per cycle (QEMU gives no cycle counts; the CK803S multiply, divide and loads cost more, assume 1.5-2x: 18-24 ms), for at most 1 s per energy trigger (the receiver gives up after 1 s without a sync word), then 8 MIPS while receiving. These are 3-4x the section 9.6 estimates (8 / 3 / 0.8 MIPS): `-Os` code (the 24 KiB claim does not hold the `-O2` modem) and the plain-C 33-tap matched filter. Consequences built in: the screen draws nothing while the receiver is not idle (a 30 ms redraw would miss halves), the analog receive loop services a half within about 6 ms of its arrival, and the burst container carries RS(182,166), which absorbs an occasionally missed half. If hardware shows lost bursts only with a crowded channel (frequent energy triggers), the cheap fix is a higher trigger threshold or a decimated first-stage search; TX is not a concern (2 % of the CPU).

### 11.11 Open risks

- H1 may say NEITHER (the 1700 Hz notch of `afsk_channel.py` is real): then the single carrier does not work and 1a's multi-tone fallback is next; everything else here (container, unit store, playback, DMR relay) stays useful.
- The squelch of the receiving radio opens late after the key-up: lead 320 ms is a guess (H6); the receiver also needs the RX audio path re-asserted every half (as the APRS RX did).
- Relay source: the call boundary comes from `0x3bd50` bit 1; if the RX hook is called outside a call or the bit is not set during the call, nothing is captured (the screen shows `relay armed 0fr`; `krk_vf.rx_calls/rx_sunk` in the parrot's counters would show the hook alive).
- The AMBE layout of the 27-byte units is unconfirmed (section 10): with a wrong detection the screen falls back to raw units (air time about 1.5x), never to wrong audio; the layout shown on line 4 (`L1`..`L4` or `raw`) is the first hardware answer.
- Playback and record: IRAM calls copied from the stock call setup / playback; a wrong guess can leave the codec in the wrong state until the next stock call (every exit restores the registers it changed, and the stock functions themselves are re-run by the next call).
- Regulatory: a burst is a data transmission nobody can decode without this patch; the operator's responsibility (section 5).

**Hint lines (2026-11-01 aids).** On an analog channel the screen shows `no RX audio: reload ch` on the status row when no finished FM receive RAM half arrived for 1 s (after a cold boot the stock receive RAM sometimes stays idle until the channel is reloaded). Key help rows are now `OK tx 1rec 2rly 3md` / `4pl5dm6dp7sn8rx9tst`.

## 12. v53 fixes (2026-11-01 hardware findings)

Hardware test of 2026-11-01 (v52, Burst Voice with real voice, judged by ear). Four findings, three code changes, one documented non-change.

### 12.1 Normal listening dropped real bursts (`rx_poll`, `krk_bv_rx_gap`)
- Symptom: two 4-block ROBUST real-voice bursts ended in "burst aborted" in the normal RX mode; listen mode (key 8) decoded the same burst 3/3 and the offline decode of its PCM 4/4 (SNR ~10 dB).
- Cause (from the code, not yet seen on a scope): a FM Receive RAM half (256 samples, 32 ms) is stable for only 32 ms after it finishes; the writer returns to the same buffer after the next flip. v52's `bv_tick` polled for 6 x 10 ms and returned to the runner, whose keypad scan (LCD bus semaphore) and `tick_ms` delay leave a 20-30 ms hole in which no poll happens. A half that flipped during the hole was read after it was overwritten or not at all; one missing half is a 32 ms discontinuity, which the receiver cannot bridge (G_RESYNC `rs_hist < 5` or `lowcnt > 24` abort). Listen mode never leaves its loop, so it has no hole. A search state costs about 10 ms per half and a redraw 30 ms, so hole + feed time can exceed 32 ms.
- Fix: `rx_poll` (krk_bvscr.c). While the receiver is idle it behaves as before but returns right after the first consumed half (the phase with the most slack before the next flip). While it is searching or receiving it does not return to the runner: it polls every tick with `os_time_dly_ms(1)` (always a yield: never a busy wait, OS_TICKS stops otherwise), capped at 10 s, until the receiver is idle again. A key aborts the reception ("rx cancelled", the key is consumed; checked every 16 halves = 0.5 s). Between two consumed halves more than `BV_RX_GAP_TICKS` (6 ticks = 60 ms; normal 3-4) means a half was lost: the receiver is re-armed ("rx half missed") instead of being fed the discontinuity. The rule is `krk_bv_rx_gap` (krk_bv_modem.c) so the host test can check it.
- Test: tests/test_bv.py `poll_tests` drives the real receiver from a model of the half timing (half k readable in [32(k+1), 32(k+1)+32) ms; poll times from the loop structure; runner gaps of 20/25/30 ms; 8 start phases). v52 policy: 8 of 24 cases decode (it reproduces the dropped-half failure); v53 policy: 24 of 24. A burst with one half removed is not decoded (the discontinuity is real) and the gap rule flags 7+ ticks. The model uses the poll cadence only; the real runner gap is an estimate.
- Hardware retest (one action): on both radios open Burst Voice on an analog channel (normal RX, do NOT press 8); on the sender record (1) with real voice, send (OK twice). The receiver must show "burst received". Repeat 3 times. A "rx half missed" status in a failed run is the diagnostic.

### 12.2 Playback silent on analog (key 4)
- Evidence: docs/analog-vs-digital-rx-config.md s.4: the only two GPIOs that differ by channel type are 0x2E and 0x2F, both HIGH on analog and both LOW on digital; 0x2F is labelled "Switch audio PWM/FD6818" (high = FD6818 receiver audio to the speaker amp, low = the SoC codec PWM, which carries the vocoder output). Stock's analog beeps clear 0x2F while the ring plays (`tx_gate_companded_audio_burst_send` @0x03022fac, fm-send-ram-tx.md s.3). v52's `do_play` ends with `rx_disarm()` which sets 0x2F high (the analog value) so the PA was on, the vocoder was playing, and the speaker listened to the quiet FD6818. A digital channel already has it low: that is why digital worked.
- Fix: `do_play` clears GPIO 0x2F (`gpio_clear_output_bit`) after the PA gate on, only on an analog channel, re-asserts it every 8 units (a squelch opening re-drives it) and sets it again at the end (the analog value; the receiver re-arms from the tick). Confidence: medium-high (three independent pieces of evidence, none a scope trace). 0x2E (MIC mux) is left alone: it is the TX side.
- Test: tests/f13_checks.py records the GPIO calls (0x2F low x3 during playback, high at the end; untouched on a digital channel).
- Hardware retest: analog channel, key 9 (test message), key 4: voice (the test message is garbled by design but must be audible AMBE sound, as on digital). If still silent, 0x2E low is the next variant to try.

### 12.3 Start of mic recordings (`rec_prov`)
- The first bursts of an unkeyed record are encoder start-up codes (b0 117/77/117, then 23/109) before the speech. `rec_prov` now drops the first `REC_SKIP` = 4 units (240 ms; the silence burst goes to the modem, nothing is kept). The session asks for `REC_SKIP + NU_MAX + 20` bursts, so the kept message is still `NU_MAX` = 92 units (5.52 s) and the recording takes 4 units (0.24 s) longer (5.76 s of the 6.4 s loop / 7 s session cap).
- Test: f13_checks.py: bursts 0..3 are not kept, burst 4 is the first unit.
- Hardware retest: key 1, speak at once, key 4 on a digital channel (or key 6 dump: unit 0 should be speech or silence, no 117/77 codes).

### 12.4 "from 16711687" on mic recordings: no change
`set_msg(..., OWN_ID)` reads word 0x33cd0, the radio's own DMR ID as loaded at boot (the same word the parrot's own-ID check and the stock call setup use, last-heard-hooks.md s.1.6). On the test radio the stored ID was corrupted to 0x00FF0007 = 16711687 (stock Device Info showed `DMR ID:16711687`, RAM `07 00 ff 00`), so the screen shows the true own ID. Correct by design. Fix the radio's DMR ID in the CPS. (The over-the-air source is `OWN_ID & 0xffffff`, so it carries the same value.)

## 13. Settings in the stock menu (2026-11-06)

The three things the screen used to change with keys are stock option levels in a nested sub-menu of their own, and they
persist in flash:

```
Settings > Channel Set > Burst Voice > Burst Voice     the screen (sections 11, 12)
                                     > Mode            Fast / Robust
                                     > Key-up Lead     0, 128, 192, 256, 320, 480, 640, 960 ms
                                     > Relay Capture   ON / OFF
```

The menu mechanism (a Channel Set row that opens a child list whose children open stock option levels) is
`docs/chanset-submenu.md` 8; the code is `krk_bvcfg.c` (blob 2).

### 13.1 The record

| | |
|---|---|
| where | stock data-chip block 4 (Radio Settings), offset `0xFC0`, 16 bytes; claim `burst_voice_cfg` in `allocations.json` (`datachip_records`) |
| why free | `docs/settings-field.md` 3.3: no stock code reaches `0xF04`-`0xFFE` of block 4 (every block-4 accessor, constant-folded and by hand); the span `0xF10`-`0xFEF` is 00 in the factory capture and FF in a vendor CPS write. Re-checked 2026-11-06: `0xF10`-`0xFCF` is all 00 in two CPS snapshots of a stock radio, and the only `movi 4043` (`0xFCB`) in the image is a `flash_block_write` to block `0x41`, not 4. `0xFD0` is Freq Unlock's record, `0xFE0` the callsign's |
| layout | magic `BVC1`, mode (`BV_MODE_FAST` 1 / `BV_MODE_ROBUST` 0), lead in 32 ms units (0..30), relay capture (0/1), eight zero bytes, checksum `krk_rec_csum(rec, 0x3c)` (`krk_callsign.c`'s helper, the third seed) |
| defaults | a factory reset (00), a CPS write (FF) or any failed check reads as Fast, 320 ms, OFF |
| writes | through stock `flash_block_write(4, 0xFC0, rec, 16)`, on OK and only when the value changes (one sector rewrite, as any stock setting) |
| reads | every time (`krk_bv_cfg`): when an option level opens and when the screen opens. No RAM cache: blob 2's SRAM claims are full |

A lead stored by an older build between two of the eight options is marked at the nearest one.

### 13.2 What the screen does with them

At every enter the screen reads the record into `S.mode`, `S.lead` and `S.rly_want` (also when it keeps a message from the
last visit; the message's air time is recomputed for the mode). Nothing on the screen changes them.

Relay capture is a setting, not an action, so the sink follows it: while the screen is open, ON and the selected channel is
digital, `cap_sink` is the vframe RX sink (installed at enter, and by the tick when the channel turns digital); on an analog
channel the tick removes it, and Back removes it as before. Nothing is captured while the screen is closed: the sink writes into
the message buffer the screen owns, and only the screen's tick turns a finished call into a message. A DMR retransmit (key 5)
still takes the sink off for the send and puts it back after.

### 13.3 Tests

- `tests/test_bvcfg.py` (host): defaults on 00 and FF, one write per change into `0xFC0`-`0xFCF` only, the other two settings
  kept, no write when unchanged, corrupted records read as defaults, the nearest lead, and the claim in `allocations.json`.
- `tests/f13_checks.py` (QEMU, halted): enter reads a faked record (robust, 480 ms); keys 2, 3, Up and Down change nothing and
  say so; relay ON on a digital channel installs `cap_sink` at enter, the tick removes it on analog and installs it again on
  digital, Back removes it; relay OFF installs nothing.
- `tests/bvnest_e2e.py` (QEMU, booted): the sub-menu end to end, each commit's record bytes at `flash_block_write`, and the screen
  reading what the menu stored (`docs/chanset-submenu.md` 8.3).

### 13.4 On a radio (not yet done)

Single actions, in this order: Menu > Settings > Channel Set > Burst Voice opens a list of four rows; OK on Mode shows Fast
marked; Down, OK shows "Setting is OK" and the list returns on Mode; power off and on, reopen Mode: Robust is marked; open the
Burst Voice child: line 0 says ROBUST. Relay Capture ON on a digital channel: the screen's line 1 says `relay armed` without a key.

