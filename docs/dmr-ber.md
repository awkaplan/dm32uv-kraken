# F3: DMR bit-error-rate / link-quality readout (DM32.00.01.048)

Sections 1-6 are static analysis, written before anything ran on a radio or in QEMU; the build is in
"Implementation" at the end. Sources: HR_C7000 v2.7 EN datasheet (page numbers are the
datasheet's "page N of 734"), the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) corpus (`meta/disasm.jsonl`, `src/`), and a fresh objdump of
the stock IRAM image (`IRAM_dm32.00.01.048.bin`, base 0x10000, run through `csky-abiv2-elf-objdump` in the
dm32uv-re build container). Addresses are literal-pool values or direct disassembly, not Ghidra names
(a Ghidra `PTR_` name is the pool slot, and a raw search for an address also hits unrelated literal pools).

Confidence tags: HIGH = disassembly or datasheet text plus a second independent path; MED = one path or
inferred; LOW = guess / needs a hardware capture.

## 0. Verdict (read this first)

- **There is no hardware BER counter for live voice.** The HR_C7000 does not count AMBE+2 FEC corrections:
  the vocoder is a separate block fed 27 bytes per burst, and no vocoder error register exists in the
  datasheet (section 1.5). The one "bit error rate" item in the datasheet is the PHYSICAL-layer receive
  test mode (`phy_rx_interp`, datasheet 9.6), which replaces Layer-2 operation and is not usable during a
  normal call. HIGH.
- **The modem does expose per-burst link-quality data that stock never reads for voice**, all in the
  MMIO block at 0x11000000 and all documented read-only:
  - `RECV_INFO_COUNT` 0x11000164: `sync_error_num` [31:24] = bit errors in the 48-bit sync field of the
    last frame (a true BER sample on a KNOWN 48-bit pattern), and `fsk_error` [7:0] = EVM statistic
    (default) or FSK-error statistic (if `fsk_error_en` [8] is set). Datasheet 9.2.2.25 p.507-508.
  - `RX_TYPE_INFO` 0x11000390: per-burst check flags (`qr_check_flag` [28] EMB QR, `cs_check_flag` [27],
    `golay_check_flag` [29], `crc16_error_flag` [26], `rs24_error_flag` [25], `crc8_error_flag` [30]),
    plus `current_voice_burst` [14:12] (1..6 = A..F), `sync_type` [1:0], `cc` [7:4]. Datasheet 10.2.2.14
    p.535-537.
  - `RSSI_BIAS_VALUE` 0x1100011c: `rssi_value` [31:16] (baseband signal energy; unscaled). Datasheet 9.2.2.8.
- **Recommended design: read-only polling of those registers from the existing RSSI screen**, no stock
  hook needed, shown as an "EVM / sync-bit-errors / EMB-error-%" line pair (not literal BER). Effort S.
- **Optional second stage (true BER, effort M):** compute AMBE+2 Golay C0/C1 errors in software from the
  264-bit raw burst the modem leaves in RX RAM at 0x160004e0, or from the 27-byte vocoder frame (IRAM
  0x46b38). Needs one hardware capture to fix the bit order first (section 3).

## 1. What the HR_C7000 datasheet provides

### 1.1 Register map (SystemControl block, base 0x11000000; same offsets as `HR_C7000-DM32.h`)

| addr | name | access | content | datasheet | valid |
|---|---|---|---|---|---|
| 0x11000164 | `RECV_INFO_COUNT` | RO (+1 RW bit) | [31:24] `sync_error_num`; [8] `fsk_error_en` RW (0 = EVM, 1 = FSKError); [7:0] `fsk_error` | 9.2.2.25 p.507-508; table line "Receive and calculate FSKError" | see 1.4 |
| 0x11000390 | `RX_TYPE_INFO` | RO | per-burst flags, listed in 1.2 | 10.2.2.14 p.535-537 | per received burst |
| 0x11000394 | `RX_INFO` | RO | [24:16] `tdma_slot_num`, [10:0] `rc_info` | 10.2.2.15 p.537 | per RC / shortLC |
| 0x11000398 | `SYS_INTERP_LIST` | RO | Layer-2 interrupt vector list (bit 11 `phy_rx_interp` = BER-test interrupt, bit 3 `dll_frame_rx_interp`, bit 16 `phy_recv_slot_abnormal`) | 4.6.5.9 p.156 | pending until cleared |
| 0x110003a0 | `SYS_INTERP_CLEAR` | WC | write 1 to clear. **Do not touch from patch code**: the stock ISR owns it (1.3) | 4.6.5.11 p.160 | |
| 0x1100011c | `RSSI_BIAS_VALUE` | RO | [31:16] `rssi_value`, [15:8] `adc_bias_i`, [7:0] `adc_bias_q` | 9.2.2.8 p.498-499 | continuous |
| 0x11000120 | `THRESHOLD_VALUE` | RW | sync detection thresholds (`sync_real_limit`, `maxvalue_low_limit`, `maxvalue`, `sig_cor_value`) | 9.2.2.9 p.499 | config |
| 0x11000128 | `PHASE_OUT` | RO | AF-mode phase amplitude (analog only) | 9.2.2.10 | |
| 0x110001c0 | `PHY_RECEIVE_STATE` | RO | 32-bit physical-layer status, no field breakdown in the datasheet | 9.2.2.47 | |

dm32uv-re's [`HR_C7000-DM32.h`](https://github.com/awkaplan/dm32uv-re/blob/main/data/registers/HR_C7000-DM32.h) still lists 0x164 as `RESERVED_164`; the datasheet page and the SVD notes
("Register Table 27 jumps 0x164 RECV_INFO_COUNT -> 0x16c")
confirm it is a real register missing from the header. HIGH on the address and layout (datasheet text), MED
on the reading of the fields (the descriptions are one line each).

### 1.2 RX_TYPE_INFO bit fields (datasheet 10.2.2.14)

| bits | name | meaning |
|---|---|---|
| 30 | `crc8_error_flag` | shortLC CRC8: 1 = error |
| 29 | `golay_check_flag` | SLOT_TYPE Golay(20,8) decode of a data burst: 1 = error |
| 28 | `qr_check_flag` | EMB QR decode of a VOICE burst: 1 = error |
| 27 | `cs_check_flag` | parity of the access info after the EMB: 1 = error |
| 26 | `crc16_error_flag` | CRC16: 1 = error |
| 25 | `rs24_error_flag` | RS check on a data frame: 1 = error |
| 24 | `data_block_error` | CRC9/CRC32 data block: 1 = error |
| 23 / 22 / 21:20 | `cach_at` / `cach_tc` / `cach_lcss` | CACH fields |
| 19 | `current_rx_slot` | slot number |
| 18 | `save_soft_finish` | "soft information storage completion flag" (soft decisions exist; no reader documented) |
| 17 / 16:15 | `pi` / `lcss` | PI present / voice LCSS |
| 14:12 | `current_voice_burst` | 1..6 = voice burst A..F |
| 11:8 | `data_type` | data frame type |
| 7:4 | `cc` | colour code received |
| 3:2 | `recv_sync_type` | 00 MS, 01 BS, 10 TDMA1, 11 TDMA2 |
| 1:0 | `sync_type` | 00 none, 01 voice, 10 data, 11 RC |

Stock's own reads confirm the layout: `FUN_0002d6a4` (IRAM) uses `>>0x17&1` (cach_at), `>>0x16&1` (cach_tc),
`>>0x14&3` (lcss); `dmr_call_setup_event_dispatch` @0x03027c30 / `dmr_call_setup_message_handler`
@0x03028154 test `>>0x1a&1` (crc16_error_flag) and `>>2&3` (recv_sync_type); `rx_type_info_read_and_copy`
@0x03027400 returns `&3` (sync_type). HIGH.

### 1.3 Validity and side effects

- `RX_TYPE_INFO` is a live RO status word that the modem rewrites for each decoded burst. Stock reads it
  directly from at least six places in three contexts (IRAM worker `FUN_0002d6a4`, the IRAM sys-interrupt
  ISR at 0x2c454, and the flash sites in 3.1 of this table), with no clear step. The datasheet marks it
  RO. A task-level read is therefore safe. HIGH (read-only status is documented and stock polls it freely).
- The IRAM sys-interrupt ISR at **IRAM 0x2c454** (disassembly):
  ```
  2c454 push r15
  2c456 lrw r3,0x3fdb8 ; ld.h r3,(r3,0)            ; state half-word
  2c45a andi r3,r3,2050 ; cmpnei r3,2 ; bf 0x2c498 ; run only when (state & 0x802) == 2
  2c468 ld.w r1,(r3,0x398)                          ; SYS_INTERP_LIST
  2c46c or r2,r1 ; 2c46e st.w r2,(r3,0x3a0)         ; SYS_INTERP_CLEAR = accumulated list
  2c472 ld.w r1,(r3,0x398) ; bnez r1,0x2c468        ; repeat until the list reads 0
  2c47c st.w r2,(0x3be84)                           ; RAM snapshot: SYS_INTERP_LIST
  2c482 ld.w r1,(r3,0x390) ; 2c488 st.w r1,(0x3beb0)   ; RAM snapshot: RX_TYPE_INFO
  2c48a ld.w r2,(r3,0x394) ; 2c490 st.w r2,(0x3be88)   ; RAM snapshot: RX_INFO
  2c492 lrw r0,0x37820 ; bsr 0x300c53c             ; OSSemPost to the deferred-dispatch task
  ```
  So stock keeps the interrupt-time RX_TYPE_INFO in RAM at **0x0003beb0** (u32), the interrupt list at
  0x0003be84 and RX_INFO at 0x0003be88. Flash consumers of 0x3beb0 (literal-pool slots found in the stock
  image): 0x03025014, 0x030274e8, 0x03027670, 0x030283f0, 0x03028784, 0x03028de4. HIGH on the ISR, MED
  that these snapshots are only refreshed on a Layer-2 sys interrupt (frame-level events), not per voice
  burst B..F.
- `SYS_INTERP_LIST`/`CLEAR`: the ISR above is the only owner. A patch must not write 0x110003a0.
- `RECV_INFO_COUNT` clear/latch behaviour is NOT documented: the table says only "Receive and calculate
  FSKError" and `sync_error_num` is "per frame synchronization field error statistics". Whether it is
  latched per frame, cumulative since reset, or cleared on read is unknown. LOW. Stock never reads or
  writes 0x164 (section 2), so there is no stock reader to disturb either way; the risk is only that
  a read clears something nothing else uses.

### 1.4 When the values are valid

- Per received burst (30 ms slot, a given voice call occupies one slot every 60 ms with TDMA, each voice
  burst repeats A..F every 360 ms). `current_voice_burst` tells which burst of the superframe the word
  describes. `sync_error_num` only means something on a burst that carries a sync pattern (voice burst A
  of each superframe, or data/CSBK bursts); on B..F the EMB replaces the sync (EMB QR result is
  `qr_check_flag`). MED (derived from the DMR frame structure in the datasheet figures; not measured).
- The registers are only meaningful in DMR digital RX with the modem in Layer-2 mode. In analog FM mode
  (`WORK_MODE` bit 7 etc.) they hold stale or zero data. The patch must test the channel type first
  (`krk_channel_analog()` in `krk_core.c`).
- `fsk_error`: EVM scale and the meaning of the number (RMS error in what unit, over which window) are not
  specified. Treat it as a relative figure (lower is better) until compared against a signal generator or
  a second radio at several levels. LOW.

### 1.5 What does NOT exist

- No register or RAM field reports the number of Golay/AMBE corrections for the 27-byte voice payload.
  Voice is handed over as a block at RX RAM 0x16000960..0x1600097A (27 bytes) to the "voice coding function
  area" (datasheet 7.1.2.2 p.474-477); the decoder gives PCM at 0x18000A0.. and an IRQ, no error count.
- The BER test in 9.6 ("Physical layer reception is mainly used for receiving bit error rate testing",
  p.524) works by receiving a known pseudo-random pattern in physical-layer mode (sequence given at p.523)
  into RX RAM, and the CPU compares. It is a bench mode (it takes over from Layer 2 and `phy_rx_interp`
  SYS_INTERP_LIST bit 11 fires), not a live-call feature. It could in principle give a true BER against
  a test transmitter but cannot run during a normal call.

## 2. Does stock read any of these?

Method: scanned `meta/disasm.jsonl` for every `ld`/`st` whose base register is `0x11000000` (built by
`movi rX,0x88; lsli rX,rX,21`, or `movih rX,0x1100`) with offset >= 0x100, plus the decompiled IRAM
(`uRam1100xxxx` forms). This catches both flash code and IRAM.

| register | stock access | where | note |
|---|---|---|---|
| 0x164 `RECV_INFO_COUNT` | **none** (no read, no write) | corpus-wide | HIGH |
| 0x11c `RSSI_BIAS_VALUE` | read only | `FUN_030c6780` @0x030c6780 (`ld.w r8,(r3,0x11c)` at 0x030c678a), corpus name `rng_seed_from_fm_voice_bytes`. The value is mixed into a random-number LCG, i.e. stock treats it as free-running entropy. Nothing stores or displays it | HIGH |
| 0x390 `RX_TYPE_INFO` | read | `dmr_rx_control_state_update_from_status` @0x030248fc (bits 3:2, `andi 0xc`, `cmpnei 8` = TDMA1), `FUN_03027400` @0x03027400, `dmr_call_setup_event_dispatch` @0x03027c30, `dmr_call_setup_message_handler` @0x03028154, `dmr_call_setup_response_process_and_signal` @0x03028338, IRAM `FUN_0002d6a4`, IRAM ISR 0x2c454 | all for call setup/LC and slot logic. None looks at qr/cs/golay flags | HIGH |
| 0x394 `RX_INFO` | read via the RAM snapshot and by the ISR | ISR 0x2c48a; the flash `ld.b (r,0x394)` hits are a different structure (RAM field), not MMIO | MED |
| 0x398/0x3a0 | read/write | IRAM ISR 0x2c454 only | HIGH |

Conclusions:
1. Stock never surfaces any quality metric for DMR voice. There is no stored RAM figure to read for EVM or
   sync errors; the only RAM copies are the interrupt-time snapshots (0x3beb0, 0x3be84, 0x3be88).
2. Reading 0x11000390 / 0x1100011c from the menu task (UI tick) is the same operation stock already does
   from other tasks. HIGH that it is harmless. Reading 0x11000164 is unprecedented: MED-LOW risk, mitigated
   by the diagnostic step in section 5.
3. The QEMU model of this block is plain read/write storage (`dm32_sysreg.c`; no special case for 0x164,
   0x390, 0x11c), so test values can be poked in (section 6).

## 3. Alternative: compute BER from data we can reach

### 3.1 Where the raw bits are

- Per-burst RX RAM: base 0x160004b0, "RECV_INFO" at offset 0x30..0x53 = the 264-bit de-framed burst, i.e.
  absolute **0x160004e0** (36 bytes with padding; datasheet 9.4.2 Table 47, p.521-522). Stock copies exactly
  this window: `rx_type_info_read_and_copy` @0x03027400 calls `copy_bytes_from_offset_table(0x30, dst, 0x24)`
  (0x24 = 36 bytes). 0x160004b0..0x160004bb holds the 12-byte LC / address (datasheet 10.7.3 text, p.633-635).
  HIGH on address and size, LOW on the bit order inside (air order assumed).
- Vocoder input: the same burst's 27 payload bytes at 0x16000960 are copied by IRAM `0x172cc(0, 0x46b38,
  0x1b)` into IRAM 0x46b38 (docs/parrot-recording.md section 1.1; the RX tap ring is at 0x48128, index byte
  0x48124, only active while the RX-record flag `[0x48160+0x1496]` is set). The decoder then runs in software
  on the CPU (vocoder task, see parrot doc), so its FEC state could in theory be inspected, but the
  decoder internals are not traced. LOW.

### 3.2 What to compute

The AMBE+2 frame is 72 bits = Golay(24,12) C0 (12 data + 12 parity) + Golay(23,12) C1 (12 data + 11 parity,
scrambled by a PRNG seeded from C0) + 11 + 14 unprotected bits. Counting corrected bits in C0 and C1 over the
3 frames of a burst gives up to 3 x (24 + 23) = 141 coded bits per burst, the same estimator MMDVM and the
OpenGD77 hotspot report as "BER". Per burst: `err = pop(C0 ^ regen(C0)) + pop(C1 ^ regen(C1))`, BER% =
sum(err) / (141 x bursts).

Two inputs, one decision to make first:
- If the 27 bytes at 0x46b38 are already in AMBE codeword order (likely, because the vocoder block takes a
  plain 27-byte frame): take C0 and C1 straight from them, ~60 lines of C plus a 4096-entry-free Golay decoder
  (syndrome table of 2048 x 2 bytes can live in the patch flash, or use the 3-error brute-force search).
- If they are in air (interleaved) order: also need the DMR A/B/C bit tables (3 x 72 entries), ~220 bytes.
- The correct order is decided empirically: capture ~100 frames from a strong call, test candidate orders
  for "C0 is a valid Golay word". On a clean signal the right mapping gives ~100% valid words, a wrong one
  ~0.1% (2^-12 chance each). That is one hardware round plus offline analysis (dm32_ramdump tools).

Effort: S for the register approach (section 4), M (3-5 days incl. the capture and a host-side C test like
`tests/test_grid.c`) for the Golay BER. Risk of 3.2: reading 0x16000960/0x160004e0 from a task races with the modem
writing the next burst; guard with a double read plus `current_voice_burst` unchanged, or read from the stock
ring at 0x48128 (IRAM RAM, not MMIO) only while recording is enabled (not recommended: it needs the Record
switch, which writes settings flash).

## 4. Patch design

### 4.1 Phase 1: link-quality lines on the RSSI screen (read-only, no hooks)

No stock bytes are patched. The code is inside `patches/kraken_ui/krk_rssi.c`'s `rssi_tick`, which runs
in the menu task every KRK_TICK_MS (100 ms). Lines 6 and 8 of the screen are free (used: 0-4, bar in row 5,
7 = "RX avg"/"RX: idle", 9 = "Back: exit").

```c
#define SYSCTL_REG(off)   (*(volatile u32 *)(0x11000000u + (off)))
#define REG_RECV_INFO_COUNT 0x164    /* [31:24] sync_error_num, [7:0] fsk_error (EVM) */
#define REG_RX_TYPE_INFO    0x390    /* [28] qr, [27] cs, [14:12] voice burst, [1:0] sync_type */
```

Per tick (digital channel and `stock_rx_active()` true):
1. `t = SYSCTL_REG(0x390)`; take the sample only if `t>>12 & 7` is 1..6 (a voice burst) and `(t&3)` is 0 or 1
   (not data/RC). Accumulate `n_voice++`, `n_emb_err += (t>>28)&1`, `n_cs_err += (t>>27)&1`.
2. `c = SYSCTL_REG(0x164)`: if the sampled burst is A (`current_voice_burst == 1`, sync present) add
   `c>>24` to `sync_err` and 48 to `sync_bits`; add `c & 0xff` to the EVM sum for every voice sample.
3. Read both registers once per tick, never write them. Do NOT write `fsk_error_en` in phase 1.
4. Window: exponential or 64-sample ring so the number tracks the last ~6 s.

Display (KRK_COLS = 19 chars):
```
line 6:  "EVM 12  sync 2/48"        evm avg (fsk_error), last sync-field bit errors
line 8:  "EMB err 3%  n=210"       percentage of voice samples with qr_check_flag, sample count
```
"n/a (analog)" when `krk_channel_analog()`, "no DMR burst" when no voice sample in the last second.
The title makes clear it is a link-quality figure, not BER: "Link: EMB err" rather than "BER".

An early return in `rssi_tick` (`raw == 0x1ff`, the QEMU/no-RF case, line "now n/a (no RF)") currently
skips everything below it: place the new sampling BEFORE that return, or the lines never update under
QEMU. In QEMU also set `DM32_FD6818_RSSI=<n>` so the early return is not taken.

Because ticks are 100 ms and bursts 60 ms, each tick samples about 60% of bursts and only about a quarter
of burst-A samples (sync field). Statistically fine for the EMB and EVM figures; the sync-bit figure needs
~minutes for 100 samples. A dedicated screen with `.tick_ms = 1` (about 10 ms, as the streaming screens use)
catches every burst and would be a new `krk_dmrq.c` in the extension blob (KRK_EXT_MAX = 6 slots). That is
phase 1b; start with the two lines.

### 4.2 Phase 2: Last Heard column (optional)

`krk_lastheard.c` keeps `rssi` per call (`struct lh_entry`, byte copied at call start). A quality byte needs
a sampler that runs while the Last Heard screen is NOT open. Candidates: the existing
`aprs_tick_hook` (0x03054888, expect `58 11 40 82`, `bsr krk_hook_aprs_tick`, runs in
`ui_periodic_maintenance_task_loop`; period not measured, assume about 1 s, which is too slow for per-burst
sampling but fine for EVM). Safer: sample at the existing call-start/alias/end hooks (0x030208ba, 0x0305edd8,
0x03020984 in `docs/last-heard-hooks.md`) and store the EVM byte of the last voice burst seen, labelled "last".
Do this only after phase 1 proves the registers are meaningful. Hook bytes are those already in
`kraken_ui.patch.json`; this feature adds none.

### 4.3 Phase 3: Golay BER (optional, section 3)

Same reading point (UI tick) but reads 36 bytes at 0x160004e0 (double read; compare `current_voice_burst`
before and after) and decodes. A host-side unit test (C, like `tests/test_grid.c`) feeds MMDVM-style test
vectors.

## 5. Hardware verification plan (per project rule: one key, one capture, firmware sweeps variants)

Phase 0 diagnostic (needed before trusting any number): add a hidden screen or an RF Lab page, started by one
key press, that logs 256 samples of `{0x390, 0x164, 0x11c}` at 10 ms into a static RAM ring and shows min/max
and the first rows. One reception of a strong DMR call is one capture; a weak call (move the antenna, or
use a lower-power second radio) is a second round. The firmware sweeps the variants inside one run:
(a) read-only, `fsk_error_en` = 0; (b) optional: second half of the capture with `fsk_error_en` set to 1
(this is the only write, one bit in a register stock never touches; restore to 0 at the end; skip it if the
reads in (a) already look sane). Expected: `current_voice_burst` cycles 1..6 every 360 ms; `qr_check_flag`
mostly 0 on a strong signal; `sync_error_num` 0..few on A bursts; `fsk_error` small and rising when the signal
is weakened. Questions the capture answers: is 0x164 latched per burst or cumulative; is it valid on B..F;
the EVM scale; whether 0x390 changes between bursts on the same slot; the voice-sample predicate.

## 6. Testing under QEMU

The `-M dm32` model has no modem, but the 0x11000000 block is plain read/write storage
(`vendor/dm32uv-qemu/hw/misc/dm32_sysreg.c`, exceptions listed there do not include 0x164, 0x390, 0x11c), so a
test can poke values over the gdb stub and read the rendered text.

1. Host unit test (pure C, like `tests/test_grid.c`): put the accumulator and formatter in a function pair
   `krk_lq_feed(u32 rx_type, u32 recv_count)` and `krk_lq_format(char *line6, char *line8)` with no MMIO
   inside, and test: no voice samples, a qr-error sample, sync errors summed over burst A only, wrap of the
   window, "n/a (analog)". Fast, no QEMU.
2. System test with the real screen: `DM32_FD6818_RSSI=100 tools/qemu_ui_run.sh lq <steps> '<keys to open
   RSSI Meter>' --gdb --keep`, then a Python script (same pattern as `tests/kraken_ui_test.py`: `RspClient`
   and `wmem` from `mi_rng_test.py`) writes, every 60 ms in a loop, `0x11000390 = (burst << 12) | 1 |
   (qr_flag << 28)` with `burst` cycling 1..6, and `0x11000164 = (sync_err << 24) | evm`. After a few
   seconds fetch the frame with `render_frames.py` and check lines 6 and 8 (for example sync_err=2, evm=12,
   qr_flag on every 10th burst gives "EMB err 10%"). A halted-at-reset variant is not enough: the screen needs
   a booted menu. The poke loop is needed because nothing in QEMU updates the registers, so the burst number
   would otherwise be constant.
3. Negative checks under QEMU: with the registers left at 0, line 8 must read "no DMR burst" (no voice sample)
   and nothing may write to the registers: run with `-d unimp,guest_errors,invalid_mem` and the existing
   `unimp=N invalid=M` token must not change, and add a gdb watchpoint (`Z2`, write) on 0x11000164 and
   0x11000390 to assert the patch never stores to them.

## 7. Risks and open questions

- No true BER from hardware. What is shown is EVM, a 48-bit sync-field error count and an EMB failure rate;
  say so on the screen. (HIGH)
- 0x164 semantics (latched vs cumulative, clear-on-read, valid bursts, EVM scale) are undocumented. Phase 0
  capture is mandatory before presenting numbers. (LOW-MED)
- The `current_voice_burst` / `sync_type` predicate for "this is a voice burst" is inferred. (MED)
- Sampling at 100 ms misses about 40% of bursts; unbiased for rates but noisy. (HIGH, known)
- Golay BER bit order unknown until a capture (section 3.2). (LOW)
- Never write 0x110003a0 (SYS_INTERP_CLEAR) or 0x164 bit 8 from the UI task without the sweep plan above.
- QEMU can only prove rendering and the no-write property, not that the radio's registers hold useful
  values.

## Implementation (2026-10-29)

Phase 1 was built as a DEDICATED screen, not as lines of `krk_rssi.c` (the main blob is full):
`patches/kraken_ui/krk_dmrlink.c`, KRAKEN list slot 8 ("DMR Link", blob kraken_ui5 at 0x030e1000 since 2026-10-30), `tick_ms = 1`
(about 10 ms, so every 30 ms burst is polled several times). Read only: 0x11000390 and 0x11000164 via
`SYSCTL()` loads; no modem register is ever written (a test source-scans for stores and a QEMU write
watchpoint on both registers must stay silent). Nothing is hooked or spliced.

- Sampling: a sample is taken when the RX_TYPE_INFO word changes (consecutive voice bursts differ in the burst
  letter, so repeated polls of one burst count once; ASSUMPTION until the capture). A voice sample is
  `burst 1..6` and `sync_type` 0 or 1. Counters: voice bursts n, EMB errors (bit 28), check-flag errors (bit 27),
  EVM sum (fsk_error), sync-field bit errors and count (burst A only), last CC / slot / burst letter. They halve
  at 1024 bursts (moving average); "idle" after 100 ticks (about 1 s) without a voice burst.
- Screen (labelled link quality, not BER): `voice bursts N`, `EMB err P% (count)`, `chk err P% (count)`,
  `EVM A last L`, `sync L/48 avg X.Y`, `CC c TSt burst X`; `n/a (analog)` on an analog channel,
  `no DMR burst` with nothing seen. Key 0 resets.
- Raw log (key 1): shows the last 8 of 16 distinct `(0x390, 0x164)` pairs as hex, and streams one UART0 marker
  line per change at 115200 8N1: `LQ START ...`, then `LQ <tick> <0x390 hex8> <0x164 hex8>`, `LQ STOP`.
  Tick is the ~10 ms screen tick, not wall time. Key 1 again (or Back) stops it.
- Pure logic `lq_feed` / `lq_tick` / `lq_line` is host-tested by `tests/test_dmrlink.py` (no samples, analog,
  repeats, non-voice words, EMB/check errors, burst-A-only sync sums, CC/TS/letter, idle, window halving).
- State: 228 bytes in `.bss.feat` (accumulator 40, line hashes 40, raw ring 16 x 8, bookkeeping).
- Section 4.1's text "place the new sampling BEFORE the early return in `rssi_tick`" no longer applies.

### One-action hardware capture (phase 0)

Receive a DMR call (strong, then once weak). On the radio: KRAKEN > DMR Link, press key 1 and leave it
while the call is received, press key 1 again. On the host, once before: `stty -f PORT 115200
raw && cat PORT > lq.log` (PORT = the programming cable, e.g. `/dev/cu.usbserial-XXXX`) (or the same with `pyserial`). The log answers: is 0x164
latched per burst or cumulative, valid on B..F, the EVM scale, and whether the voice predicate holds. The
screen shows the summary meanwhile (key 1 again to see it).

### One-action hardware test (key 2, 2026-10-29)

Replaces the start-key / stop-key pair above for the hardware rounds. KRAKEN > DMR Link, press key `2` ONCE.
The screen shows `AUTO: waiting call`, then `AUTO: recording` at the first voice burst, and the capture ends by
itself about 1 s after the last burst (end of that call), after 120 s at the latest, or after 60 s if no call came
(`lq_auto_next`, pure, tested in `tests/test_dmrlink.py`). While it runs the same `LQ <tick> <0x390> <0x164>` lines as
the raw log go to UART0; at the end it prints one `LQ SUM <line>` per screen line (bursts, EMB %, check %, EVM, sync,
CC/TS/burst) and `LQ AUTO END ticks N` (`(no call)` if nothing arrived). The summary stays on the screen
(`AUTO done, see UART`). On an analog channel it finishes at once with `n/a (analog)`. Key 1 is ignored while it runs; keys 0 and
1 behave as before otherwise. Pressing key `2` re-arms from zero, ignoring the 0x390 word still latched from the previous call.
Capture (the only host command): `stty -f PORT 115200 raw && cat PORT > lq.log`.
The strong and the weak case stay two rounds because the operator has to change the signal physically (firmware
cannot); each round is key `2` plus one call. State: +8 bytes in `lqst` (`autom`, `arm_tick`, appended).

## Review fixes (2026-10-29)

Two summary lines were 20 characters (`EVM avg 255 last 255`, `idle, last call 1023`) on a 19-column screen; the stock renderer
wraps such a line and the hash cache then never repaints it. Now `EVM 255 last 255` and `idle, bursts 1023`;
`tests/test_dmrlink.py` checks that all six lines fit 19 columns at worst-case figures, live and idle.
