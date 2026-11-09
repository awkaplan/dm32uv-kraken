# DMR parrot: TX insertion point, keying, capture and end-to-end design (F9, part 2)

Static analysis plus one QEMU register/memory experiment (section 9). Nothing was flashed or sent to a radio; no existing
code was edited. Companion to `docs/parrot-recording.md` (stock recording, which this document corrects in places, section 11).
Corpus: [dm32uv-re](https://github.com/awkaplan/dm32uv-re), image `progmem_stock_DP570UV_20260926_FULL.bin` (sha in `docs/flash-allocation.md`).
IRAM addresses are execution addresses; the flash alias of IRAM `A` is `A + 0x030afc20` (IRAM `0x2e9ea` = flash `0x030de60a`).
All pool values and call targets below were decoded from the image (the value in each pool slot, not the slot's address), not taken from Ghidra names.
Branch targets were re-derived by decoding every `bsr` in the 1 MB image (throwaway scripts, not kept).

Tags: HIGH = disassembly plus an independent second path (or run in QEMU); MED = one path or inferred; LOW = guess/untested.

## 0. Verdict

- **Substitution point (HIGH):** the vocoder TX task (IRAM `0x2e5a0`) hands each finished 60 ms burst to the modem with
  `bsr 0x172f8` (copy 27 bytes from `0x49b18` to MMIO `0x16000960`). That call exists at four sites, all in flash sector
  `0x030DE000`, all with the identical argument setup `r0=0, r1=0x49b18, r2=27`. Replacing those four `bsr` with a `bsr` to a
  blob in our flash that overwrites `0x49b18` with the stored frame and then calls `0x172f8` is the substitution. It rides on the
  stock encoder's own 60 ms cadence, so no timer and no new task is needed.
- **RX capture (HIGH):** the decoder task (IRAM `0x2ec30`) reads each received burst with `bsr 0x172cc` at IRAM `0x2ed64`
  into `0x46b38`. A `bsr` hook there copies the 27 bytes into our SRAM buffer. No stock flag, ring or semaphore is touched.
- **IRAM is patchable by this tooling (HIGH):** IRAM is a verbatim boot-time copy of flash `0x030bfc20..0x030decb8`. A splice at
  flash `IRAM + 0x030afc20` changes the running code. The builder supports it via `allow_occupied`, but a pc-relative `bsr` must be
  encoded against the IRAM pc: use target `"@blob:sym + 0x30afc20"` (section 1).
- **Keying (HIGH for the mechanism, MED for the DMR target selection):** post `0x08` / `0x10` to flag group `0x0003a4f0`, exactly as
  `krk_aprs_tx.c` and the FH engine do. "TX voice is up" on DMR is `*(u8*)0x3bd50 & 1`, not `call_active & 8` (that is analog).
- **Budget (HIGH):** `0x18005000..0x18007000` (8 KiB) is unused by the production image (the `mi_rng_tap_buf` claim is only used
  by the measurement manifest), but the claim blocks it until `patches/mi_rng/allocations.json` is changed (section 5).
- **Not testable without hardware:** modem acceptance of the substituted bytes (format symmetry, MED-HIGH), DMR call-setup timing,
  TOT, which target the stock PTT picks after a received call (MED-LOW).

## 1. Can IRAM be patched by this project's tooling? Yes (HIGH)

Evidence:
1. Boot stub literal pool `0x0300c0d0..0x0300c0e0` (checked by `derive_iram.py`, which refuses an image whose pool differs):
   copy flash `0x030bfc20..0x030decb8` to IRAM `0x00010000..0x0002f098`, zero `0x2f098..0x4a7b0`. So the IRAM code is a plain memcpy
   of flash on every boot; editing the flash bytes edits the running code. All 5 sites below sit in the copy region
   (`0x2e6c0..0x2ed64` < `0x2f098`).
2. Precedent on this hardware: the ALPU bypass is `03 6c 03 6c` over `20 e9 04 00` at flash `0x030c6866` = IRAM `0x16c46`
   (the ALPU anti-clone check bypass, see [dm32uv-re](https://github.com/awkaplan/dm32uv-re)). It lives in the IRAM image and runs.
   Caveat: that sector (`0x030c6000`) is the QEMU-only/forbidden one (`stage_sectors.py` refuses it, `compose_qemu_image.py` replaces
   the whole sector with the bypass file). **Therefore `0x172f8` / `0x172cc` themselves
   (IRAM `0x16f18..` flash `0x030c6f18`) must NOT be edited**; hook their callers instead. All callers we need are in sector
   `0x030DE000`, which is none of the special sectors.
3. `compose_qemu_image.py --iram-out` derives IRAM from the composed image, so splices in the load image carry into QEMU
   (for the stock image the only 4 bytes that differ from a RAM dump of the running IRAM are the ALPU bypass at IRAM 0x16c46).
4. `build_manifest_patch.py`: splices inside `occupied_regions.json` ranges (the IRAM load image is one) are allowed when listed in
   the manifest's `allow_occupied` with a non-empty justification and carrying `expect` bytes (policy text at lines 76-80 and
   `check_policy`). Blobs and data may never land there; ours live in `0x030ee000..` (existing `kraken_ui_code2` claim) or a new
   sector. Nothing may place `.bss` in IRAM (`ram_sections` check) - not needed.

**Branch encoding trap (HIGH, verified by running `build_patch.encode_branch`/`decode_branch`).** The builder encodes `bsr` relative
to the FLASH address of the splice, but at run time the pc is the IRAM address (`flash - 0x030afc20`). With the naive target the
branch lands at IRAM `0x3e4e0` (wrong). The fix that the builder already accepts (`resolve()` handles `@blob:sym + offset`, and the
post-write check `w.addr + decoded*2 == tgt` is self-consistent):

    "branch": {"kind": "bsr", "target": "@kraken_ui2:krk_parrot_tx_hook + 0x30afc20"}

I checked blob `0x030ee100` for all five sites: encoded bytes decode, from the IRAM pc, to exactly `0x030ee100`
(e.g. site TX3 `85 e1 8b fb`, RX `85 e1 ce f9`); range is 51 MB, inside the +-64 MB `bsr26` reach. If a future builder rejects
a target outside flash, the fallback is `"bytes"` with values computed after linking.

Flashing note: sector `0x030DE000` holds running code (encoder, decoder: IRAM bytes up to flash `0x030decb8`) followed by trailing vendor data. A corrupted write of it breaks the vocoder or boot. It ships in the update file like every other changed sector
([flashing.md](flashing.md)); treat it as higher risk than the menu sectors.

## 2. TX voice path: where the 27-byte frame goes to the modem

### 2.1 The task and its cadence (HIGH structure, MED on the interrupt source)

- Created in the modem-codec init (IRAM `0x164f4..0x16512`): `OSTaskCreate(tcb 0x4805c, name @0x18000e7c, entry 0x2e5a0,
  prio = byte 0x18000bd0 via IRAM 0x1616c, stack 0x46b94 ...)`. The priority byte comes from the flash call at `0x0305f360`
  (`movi r0,16; movi r1,15; bsr 0x03021b24` -> `0x16164/0x16174` store 16 and 15): **TX encoder prio 16, RX decoder prio 15**
  (decoder entry `0x2ec30`, TCB `0x47f94`, created at IRAM `0x16586..0x1659a`). dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md) lists 15 and 16 as "free"
  only because it scanned flash `OSTaskCreate` sites; these two are taken by the IRAM-created tasks.
- Loop (`0x2e5a0`): `bsr 0x12720` (task-semaphore pend, once per 80-sample PCM block = 10 ms), then per block
  `0x16f3c` (analysis of 80 samples), half/phase toggle in `[0x48160+0x1732]`; on the second half of a 160-sample (20 ms)
  frame: XOR with the table at `+0x90`, `0x1893c` (FEC/frame encode), `0x2e494` (pack 72 bits MSB-first to 9 bytes at
  `0x49b18 + [0x48160+0x19d3]`, then `[0x19d3] += 9`), `[0x48160+0x1734] += 1`. When `[0x1734] >= 3` (3 frames = 60 ms) the
  27-byte burst is complete. Evidence: IRAM `0x2e68a..0x2e6c0`, `0x2e9b6..0x2e9ea`, `0x2eabc..0x2eaf0`.
- So one modem write per 60 ms, 27 bytes, produced inside the encoder task at prio 16. The PCM block pend is posted by an audio
  interrupt that was not traced (LOW on which ISR; the 10 ms/80-sample arithmetic is from the constants `80`, `160`, `3`).

### 2.2 The hand-off instruction (HIGH)

`0x172f8` (IRAM; flash alias `0x030c6f18`): `r0` = byte offset into the window, `r1` = source, `r2` = count; loop of `ld.b/st.b`
into `0x16000960 + r0 + i` (pool `lrw r12,0x16000960` at `0x172f8`; matching reader `0x172cc` with the same pool). Its callers
(bsr sweep of the whole 1 MB image, both target spellings): `0x167d2`, `0x2e33c`, `0x2e350`, `0x2e6c0`, `0x2e820`, `0x2e9ea`, `0x2eaf0`.

| IRAM pc | flash | stock bytes | role | live? |
|---|---|---|---|---|
| `0x2e6c0` | `0x030de2e0` | `ff e3 1c 46` (bsr 0x172f8) | variant `[0x4988a]!=0, [0x49888]!=0` | dead: `[0x4988a]` is cleared at init (`0x163b4`, `0x165d8`) and has no setter anywhere in flash/IRAM (movi/literal scans) |
| `0x2e820` | `0x030de440` | `ff e3 6c 45` | same variant, second phase | dead (same reason) |
| **`0x2e9ea`** | **`0x030de60a`** | `ff e3 87 44` | variant `[0x4988a]==0, [0x49888]==1` (set by `0x16088` at TX start) | **live** |
| **`0x2eaf0`** | **`0x030de710`** | `ff e3 04 44` | same variant, other phase | **live** |
| `0x167d2` | `0x030c67d2` | | final partial-burst flush (`0x166b8`, called from `0x2e87c/0x2e8a8` when TX stops with `[0x1734]>0`) | live at TX end, **in the forbidden sector - cannot be hooked** (section 2.4) |
| `0x2e33c/0x2e350` | `0x030ddf5c/0x030ddf70` | | function `0x2e310`, no callers/pointers anywhere (dead stock "inject external frame" helper) | dead |

Context at the live sites (to assert in the manifest, stock bytes read from the image):

    2e9e4  00 30            movi r0,0
    2e9e6  32 12            lrw  r1,0x49b18     ; pool word = 0x49b18 (read from the image)
    2e9e8  1b 32            movi r2,27
    2e9ea  ff e3 87 44      bsr  0x172f8
    2e9ee  00 33            movi r3,0           ; first insn after the call
    ...
    2eaee  30 10            lrw  r1,0x49b18  (preceded by 1b32 movi r2,27 ; 0030 movi r0,0)
    2eaf0  ff e3 04 44      bsr  0x172f8

Registers after the call: `0x172f8` clobbers r0-r3 and r12; the code after all four sites uses only r5, r6, r11 (callee-saved, preserved by
a normal C hook) and re-loads r2/r3/r12 before use; r13 is not read before being written. So a standard C-ABI function with the
signature `void hook(u32 off, u8 *src, u32 n)` is a drop-in. It runs in the encoder task context (1280-byte stack, prio 16): keep it tiny,
no printf, no blocking calls.

All four write sites are gated by `[0x48160+0x19d5] != 1` (`0x2e6b4`, `0x2e816`, `0x2e9e0`, `0x2eae6`): when that byte is 1 the burst is only
tapped into ring `0x46b5c` and NOT written to the modem. Stock sets it (with `[0x19d6]`) only when byte `0x33cac+0xb != 0`
(`0x03021a70` TX start, via the function at `0x03023b04`), a mode this firmware never enters in normal use. Both bytes are zero in
normal TX (HIGH: init clears them at `0x163c4/0x163cc`; the only setters `0x15ea0/0x15f00` have the single caller `0x03021aea/0x03021ae6`
behind that gate).

### 2.3 Frame format symmetry (MED-HIGH, hardware check required)

- RX: window -> `0x46b38` (27 B) -> 3 x `0x2e3f0(out_halfwords, in_bytes, 72)` (MSB-first unpack) -> `0x182bc` (decode) ...
  (`0x2ed8c..0x2ee62`, `0x2ef..`).
- TX: ... `0x1893c` -> `0x2e494(out_bytes, in_halfwords, 72)` (MSB-first pack) -> `0x172f8`.
- `0x2e3f0` and `0x2e494` were read in full: exact inverses (8 bits per byte, MSB first, one bit per halfword). Both directions use the
  same window address `0x16000960`, the same 72-bit-per-9-byte structure and the same XOR-with-`+0x90` table step. The stock "Record
  playback" feeds stored RX-window bytes straight into the RX decoder, so the RX window format is by definition what the recorder
  stores. Therefore stored RX bursts are 3 x 72 channel bits, the same unit the TX encoder emits. Errors in the received bits are
  carried through (as in any simplex repeater).
- Residual risk: the modem could present RX bits with a different polarity/order than it expects on TX. Cheap offline check on
  hardware (no TX): capture the RX window (our own buffer) and the TX encoder output (`0x49b18` or ring `0x46b5c`, stock TX recording)
  and compare the per-frame structure (the first 24 bits of each 72-bit frame form a Golay codeword in the stock AMBE+2 layout).

### 2.4 End of transmission (partial burst) - MED

When TX stops mid-burst (`[0x1734]` in {1,2}) the encoder calls `0x166b8`, which finishes the burst from whatever mic frames were already
encoded and writes it at `0x167d2` (sector `0x030c6000`, unhookable). Stock produces exactly this on every PTT release, so it is not a new
behaviour, but the payload is mic-derived (quiet room noise), up to 60 ms. Mitigations: (a) after the last stored frame keep
substituting a silence frame for every full burst until TX voice stops, so only that one tail burst is mic-derived;
(b) release PTT right after the last stored burst is written, i.e. at a burst boundary, from the hook itself (section 6).

### 2.5 Alternative without any IRAM splice (Plan B, MED-LOW, not recommended)

Set `[0x49b35]=1` and `[0x49b36]=1` (the stock "bypass" bytes; use plain byte stores, not the setters, to avoid `[0x1498]`) so the
encoder skips the modem write (and the `0x1893c` encode, `0x2e650`) and only taps `0x2e558` into ring `0x46b5c`, which posts semaphore
`0x45670` every 60 ms. A patch task pends on `0x45670`, then calls the stock helper `IRAM 0x2e310(frame)` (with `[0x19d6]=1` it just
does `0x172f8(0, frame, 27)`). Needs an `OSTaskCreate` task (TCB + stack in the scarce SRAM, free priorities 9 or 30; 15/16 are taken),
clears must happen on every exit (a leftover `0x19d5` = silent TX until reboot), and the write latency depends on scheduling. It avoids
touching sector `0x030DE000`, which is its only advantage.

## 3. Single best substitution design

Hook function (flash blob, `krk_parrot_tx_hook`, installed over the four `bsr 0x172f8`):

    void krk_parrot_tx_hook(u32 off, u8 *src, u32 n)       /* r1 == 0x49b18, n == 27, off == 0 */
    {
        if (P.sending && n == 27 && src == (u8 *)0x49b18) {
            if (P.tx_i < P.n_frames)       memcpy(src, P.frame[P.tx_i], 27);
            else                           memcpy(src, P.silence, 27);
            P.tx_i++;
            if (P.tx_i == P.n_frames)      OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);   /* burst-synchronous unkey */
            else if (P.tx_i > P.n_frames + 8 && (P.tx_i & 7) == 0) re-post release;          /* still keyed: keep asking */
            if (P.tx_i > P.n_frames + 40)  P.sending = 0;                                    /* give up; never hijack a later TX */
        }
        stock_172f8(off, src, n);          /* IRAM 0x172f8; plain call, not a tail jump, so r15 handling is the C ABI's */
    }

`stock_172f8` is the IRAM address `0x000172f8` (a call from flash to a fixed IRAM address uses an absolute-correct `bsr`/`jsri`; the
other patches already do this, e.g. `fd6818_reg_write = 0x0002c2f4`). Writing `src` (the encoder's own buffer) means the TX
record tap `0x2e558` that follows sees the substituted bytes too.

Confidence: hook mechanics HIGH; "modem accepts the bytes" MED-HIGH; timing HIGH (same call site, same task, zero added latency
beyond ~30 instructions).

## 4. RX capture

### 4.1 Recommended: bsr hook at IRAM `0x2ed64` (HIGH)

Decoder task, per burst when the in-burst frame counter `[0x456c0+0x1164]` is 0 (so `r0=r11=0` is the offset argument):

    2ed5e  2f 6c            mov  r0,r11            ; 0
    2ed60  3e 12            lrw  r1,0x46b38
    2ed62  1b 32            movi r2,27
    2ed64  ff e3 b4 42      bsr  0x172cc           ; <- hook (flash 0x030de984)
    2ed68  03 ea 96 14      movi r3,5270           ; then: if [0x456c0+0x1496]==1 -> stock RX tap 0x2ebe0

Hook: call `0x172cc(off, dst, n)` first, then if `P.cap_active && P.n < CAP` copy the 27 bytes from `0x46b38` (`dst`) to `P.frame[P.n++]`.
Registers after the site: r8 (callee-saved) is used next; r12/r13 not live. The other read site `0x2efe8` (flash `0x030dec08`,
`ff e3 72 41`) is dead: it is on the `[0x456c0+0x1497]==1` branch, and the only setter `0x15ec0` has no callers. Hooking it is optional.

### 4.2 Why not the stock ring `0x48128` (HIGH)

`0x2ebe0` runs only when `[0x456c0+0x1496]==1` (set by `0x15fa4(2)`, cleared by `0x15fa4(0)` at every stock call end, flash
`0x03061194` and others) and the ring is two 27-byte slots with a consumer that must pend on `0x45648` within 60 ms (reader `0x1620c`:
`r0` offset, `r1` buf, `r2` len, `r3` timeout ms; returns 0). It needs a task and races the stock `record` task. The direct hook has none of that.

### 4.3 Can a flash-resident hook see each frame without an IRAM splice? No (MED-HIGH)

`rxvoice_task_entry` (0x03020778, prio 24) only gets call start/end flags; the burst payload is read by the prio-15 decoder task.
The only other way is the ring + task of 4.2. So the RX capture needs the one extra IRAM splice (still the same sector).

## 5. SRAM budget

- 27 B per 60 ms = 450 B/s. 10 s = 4.5 KiB; 15 s = 6.75 KiB; 18 s = 8.1 KiB.
- Production `mi_rng.patch.json` does not allocate `mi_rng_tap_buf`: `mi_rng.ld` has only `PATCH_OWN_FLASH` and the state claim; the tap buffer
  (`TAP_CAPACITY`, 8 KiB at `0x18005000`) is compiled only under `-DMI_RNG_SAMPLE_TAP` (`mi_rng_tap.patch.json`, "measurement only"). The
  stock placement scan found nothing referencing `0x18004c00..0x18007000` (`docs/flash-allocation.md`, `patch-cookbook.md`: highest literal `0x18004bfc`).
- **But the claim still blocks us:** `add_claims()` in `build_manifest_patch.py` raises "allocation overlap" at load time for any extra claim that
  overlaps an existing one, used or not. `patches/mi_rng/allocations.json` must be changed first: split the
  tap claim off into the measurement manifest's own file, or shrink it. Ask the owner; until then F9 cannot get an 8 KiB buffer.
- Proposed claim (after that change): `kraken_parrot_state` sram `0x18005000..0x18007000`. Layout: 64 B header (magic, state,
  counters, dst/src/type, 27 B silence frame) + frames from `+0x40`: `(8192-64)/27 = 301` frames max (18.0 s). Default cap 250 frames
  (15.0 s, 6750 B). At 0x18007000..0x18007310 the Snake claim (dm32uv-re) is also free in our image (784 B = 29 frames) but a second
  region complicates indexing; not recommended.
- Cold power-on SRAM holds noise (not zero): the header needs a magic + init at arm time (as the beacon code does).

## 6. Keying, unkeying, TOT and failure paths

### 6.1 How PTT works for DMR (HIGH chain, MED on target selection)

`keypad_debounce_and_commit_event` (GPIO 0x2b) -> `call_setup_mode_flag_dispatch` posts bit `0x08` on group `0x0003a4f0` ->
`main_ptt_and_channel_event_loop` (0x03012d10) -> `ptt_press_handler` (0x0305bfb8) -> `tx_key_from_vfo_mode_guard(mode, id, flag)` (0x0305be64;
digital branch: sets `vfo.flags_0x8 = 0x10 | ...`, `dialog_request_enqueue(request_id, id)`; request 0/1 private, 2 group, 3 all call) -> call-setup
state machine -> `tx_dialog_event_state_dispatcher` (0x0305e4e8) event bit 2 -> `tx_voice_start_begin_record` = flash `0x03021a70`
(`audio_vocoder_buffer_flush2`): sets modem reg `0x11000084` bit 0, optional TX recording, `bsr 0x16088` (sets `[0x48160+0x1728]=1` =
encoder runs, SoC `0x11000380/0x11000080` bit 4), then **`0x3bd50 |= 1`** (`0x03021b04`). Teardown `0x03021850`: `bsr 0x160b0`
(`[0x1728]=0`) ... `0x3bd50 &= ~1` (`0x0302188c`). `0x3bd50` bit 1 is the RX-voice twin (`0x30219f0` set, `0x03021a14` path clear).
So **"TX voice up" = `*(volatile u8*)0x0003bd50 & 1`** (HIGH from disassembly). `call_active` (`0x3fdc8`) bit 3 is the analog TX bit; the digital
dispatcher sets bit 1 (`& 0xbd | 2`) at the same event (MED).

Posting (HIGH, as `krk_aprs_tx.c` does on hardware): `OSFlagPost_wrapper_set(0x0003a4f0, 0x08)` with `OSFlagPost_wrapper_set = 0x0300c494`.
Release: `0x10`. TOT and the keypad release post the same `0x10`.

Which target the stock press chooses after a received call (MED-LOW, unverified): `ptt_press_handler` branches on `tx_ptt_ui_state.field_0x7`
(5: private call to `dialog_flags.field_0x24`; 4 with `dialog_flags.flags_0x1&3==2`: group call to `dialog_flags.field_0x8`; else the
channel's own contact via `settings[0x53]`), and on `call_active&1` / `get_field_0301ea64_0x14()` for the "reply to last caller" case (1: private to
`field_0x10` = last source, 2: group to the last destination). One hardware observation decides it. Fallback with explicit control:
call `tx_key_from_vfo_mode_guard(4, captured_dst, 0)` (group) or `(3, captured_src, 0)` (private) from the screen task after doing the
gates yourself (`tx_is_inhibited`, busy). `docs/parrot-recording.md` 3.1 lists the same chain; calling from another task risks racing the UI,
so prefer the posted event first.

Pre-conditions to check before posting (all HIGH, from `afsk-tx.md` 1.3 / `krk_aprs_tx.c`): DMR channel (`(*(u8*)0x3e1e4 & 0xf0) == 0x10`),
`tx_is_inhibited() == 0` (0x0302ed50), not `stock_rx_active()` (0x0300f2a4), CPS link `0x320af == 0`, selected side loaded
(`krk_channel_on_selected`), not encrypted (`vfo.field_0xf & 0x40` at `0x3e1eb`; the cipher keystream is applied somewhere we did not trace, so refuse),
`(*(u8*)0x3e31f & 0x18) == 0`. From inside a menu screen `ptt_press_handler` may take menu branches (`get_current_mode_state1() != 0`,
afsk-tx.md 1.3). The analog beacon works from the KRAKEN screen on air, so the plain branch is reached there; for DMR this is MED.

### 6.2 TOT

`call_display_countdown_and_ui_event_trigger` 0x030556cc: limit `settings[0x35] * 15` s (0 = off), warning at limit minus `settings[0x37]`, expiry posts
`0x10` (afsk-tx.md, CONF for analog; assumed shared for DMR, MED). Rule: `n_frames <= min(cap, settings[0x35]*15 s / 60 ms - 1)` when TOT is on;
if stock cuts us early the hook simply keeps writing into a closed TX (harmless, section below) and the screen sees `0x3bd50&1 == 0`.

### 6.3 Failure paths and guaranteed unkey (layers)

| # | situation | handling |
|---|---|---|
| F1 | press ignored (inhibited, busy, menu branch) | no TX voice within 3 s -> post `0x10` once (idempotent: also posted by every real key release), state = IDLE, `P.sending=0`. LOW-MED on whether a stray `0x10` while idle is a no-op; the real PTT release does exactly that on every release |
| F2 | TX voice up but encoder never reaches the hook | burst count stays 0: screen watchdog releases at `T_press + cap + 3 s` |
| F3 | hook runs, UI task hung | **L1: the hook posts `0x10` itself at the last stored burst** and re-posts every 8 bursts while still keyed; independent of the menu task |
| F4 | release posted but TX voice stays up | L2: screen tick re-posts `0x10` every 500 ms, max 8 times, then logs "stuck TX" and keeps trying while the screen is open |
| F5 | any key pressed / Back / screen exit | `tick` sees a scan code: abort -> post `0x10` and wait (bounded 2 s) for `0x3bd50&1==0`; `exit()` does the same |
| F6 | incoming RX or channel change while waiting to key | cancel before pressing (RX start sets `P.abort`); after pressing: release |
| F7 | stock TOT fires | detected as `0x3bd50&1==0`; hook stops by itself (`tx_i > n+40`) |
| F8 | substitution state left set | `P.sending` is cleared when TX voice is observed down, after `n+40` bursts, and at arm/disarm; the hook also requires `src==0x49b18 && n==27` |
| F9 | user presses the real PTT while `P.sending` | the user transmits our stored frames until the sequence ends (max cap, ~15 s). Accept; documented. Cannot distinguish the sources in the hook |
| F10 | power loss/crash | radio resets; nothing persists (RAM-only, no flash writes) |

Maximum keyed time by construction: `n_frames * 60 ms + ~0.5 s` normally, `cap + 3 s` in the worst software failure, and stock TOT when enabled.
Nothing in this design keys TX by any route other than the stock PTT event (no direct `rf_tx_enable_sequence` / FD6818 writes; HIGH that
such calls are not needed: `afsk-tx.md` 1.2).

## 7. End-to-end design (KRAKEN > Parrot screen)

Components and data flow:

    RX call start  H1 (existing last-heard wrapper, flash 0x030208ba, rxvoice task)  -> krk_parrot_rx_start():
                     if armed && idle && !own-ID: P.n=0; P.cap_active=1; P.dst/src/type from block B (0x3bd30/0x3bd34/0x3bd2c); P.t_start
    each RX burst  RX hook (IRAM 0x2ed64, decoder task prio 15)       -> P.frame[P.n++] = 0x46b38[0..26]  (while cap_active && n<cap)
    RX call end    H2 (existing wrapper, flash 0x03020984, rxvoice task)-> P.cap_active=0; P.ready=1; P.t_end = OS_TICKS (0x426d0, 100 Hz)
    screen tick    (menu task, every 100 ms)                           -> state machine IDLE -> HOLD -> KEY -> SEND -> DONE
        HOLD: wait >= 1.0 s after t_end, n >= 5 (0.3 s), no new RX, channel idle (stock_rx_active==0), not inhibited, not own ID
        KEY : P.tx_i=0; P.sending=1; post 0x08; wait <=3 s for 0x3bd50&1
        SEND: the TX hook substitutes frames; screen shows "TX 37/120"; watchdogs F2-F5
        DONE: wait for 0x3bd50&1==0 (<=2 s); hold-off 3 s (anti-loop); keep the capture for "replay" (key) or discard
    TX burst       TX hook (IRAM 0x2e9ea/0x2eaf0 [+0x2e6c0/0x2e820], encoder task prio 16) -> section 3

Safety interlocks (all must hold, else do nothing): digital channel; not encrypted; `armed`; `n` in [5, cap]; call type group or private as allowed by
a menu option (default group only); source != own DMR ID (`0x33cd0`, last-heard-hooks 1.6); hold-off since last echo; `tx_is_inhibited()==0`;
no CPS link; screen still open; TOT clip; one echo per call.

Screen: lines for state (IDLE/ARMED/RECORDING n s/READY/KEYING/TX i/n/ABORT reason), last src/dst/type, stored seconds, count of echoes. Keys: OK = arm/disarm,
`1` = replay stored frames now ("play last call on air", same code path from KEY), `0` = discard, Back = disarm+exit (F5).

Confidence per element:

| element | conf | note |
|---|---|---|
| TX hook at the four `bsr 0x172f8` sites | HIGH | sites, bytes, regs verified; live pair is `0x2e9ea/0x2eaf0` |
| RX hook at `bsr 0x172cc` (0x2ed64) | HIGH | single live read site |
| IRAM splice via flash sector 0x030DE000, branch encoding | HIGH | encode/decode verified; first IRAM splice in this image (precedent: ALPU bypass) |
| Format symmetry RX window == TX window | MED-HIGH | pack/unpack inverse, same window; needs one hardware comparison |
| Cadence from encoder (no timer) | HIGH | 80-sample blocks, 3 frames per burst |
| H1/H2 chaining into existing wrappers | HIGH | sites already hooked for Last Heard |
| posting PTT 0x08/0x10 | HIGH (analog on air); MED (digital) | |
| "TX up" = `0x3bd50 & 1` | HIGH | |
| target (TG/private) chosen by stock reply logic | MED-LOW | observe once; fallback `tx_key_from_vfo_mode_guard` |
| end-of-TX partial burst | MED | stock behaviour, mic-derived tail <=60 ms |
| TOT on DMR | MED | |
| SRAM 8 KiB | HIGH free / blocked by claim | needs an allocations change in patches/mi_rng |
| refuse encrypted | MED | keystream placement untraced |
| anti-loop / repeater etiquette | LOW | policy decisions, user's licence responsibility |

## 8. Concrete manifest sketch (not applied; for the implementer)

    "allow_occupied": [{"start": "0x030de000", "end_exclusive": "0x030df000",
        "justification": "F9: splice five bsr in the IRAM load image tail (IRAM 0x2e6c0..0x2ed64) to hook the vocoder TX/RX modem copies"}],
    "splices": [   # each with expect = stock 4 bytes, branch bsr to "@kraken_ui2:krk_parrot_tx_hook + 0x30afc20"
      {"name":"parrot_tx3","addr":"0x030de60a","expect":"ffe38744", ...}, {"name":"parrot_tx4","addr":"0x030de710","expect":"ffe30444"},
      {"name":"parrot_tx1","addr":"0x030de2e0","expect":"ffe31c46"},   {"name":"parrot_tx2","addr":"0x030de440","expect":"ffe36c45"},
      {"name":"parrot_rx", "addr":"0x030de984","expect":"ffe3b442", target "@kraken_ui2:krk_parrot_rx_hook + 0x30afc20"} ]

(`expect` bytes are the file bytes in order, e.g. `ff e3 87 44`.) Adding a sector to the staged set: `0x030DE000` joins the changed-sector list in
`build_image.py` (the ownership bookkeeping handles shared sectors). The blob can live in the existing `kraken_ui_code2`; the blob symbols must be
reachable from `krk_ext.c`'s screen table. `check_bsr.py` (gas drops `bsr <absolute>`) applies to the stub if written in assembly; a C hook avoids it.

## 9. QEMU: what can and cannot be tested

Model facts (dm32uv-re [`docs/qemu-workflow.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/qemu-workflow.md), `hw/misc/dm32_dmrram.c` in [dm32uv-qemu](https://github.com/awkaplan/dm32uv-qemu)): the DMR baseband RAM at `0x16000000` is plain RAM with logging; no modem,
vocoder, interrupts for audio, or PTT-to-TX-voice chain. The vocoder tasks never get PCM blocks.

**Experiment run for this report (throwaway script, not kept):** compose the stock image, boot halted, call IRAM `0x172f8(0, buf, 27)` then
`0x172cc(0, dst, 27)` through the gdb stub. Result in a QEMU build that includes the `dm32_dmrram` model (public in
[dm32uv-qemu](https://github.com/awkaplan/dm32uv-qemu)): the 27 bytes land at `0x16000960..0x1600097a` and read back
identically, callee-saved r4-r11 intact. In builds without that model the window reads back zeros and logs nothing.
**The `--window` parrot checks need a QEMU with `dm32_dmrram`**; otherwise they will fail for the wrong reason.

Testable (in the style of `tests/kraken_ui_test.py` + the `mi_rng_test` checks; needs the IRAM image: `compose_qemu_image.py --patched <combined> --iram-out`, then
`-device loader,file=iram.bin,addr=0x10000`):
1. Splice decode: for each of the five flash sites, the stock-bytes assertion, and `decode_branch` from the IRAM pc lands on the blob symbol (catches the
   0x30afc20 mistake: the naive build lands at 0x3e4e0). Check the derived IRAM bytes at 0x2e9ea etc.
2. TX hook at its REAL call site: set `r5=r6=0x48160, r11=6611, r14=STACK`, preload `P` (sending, 3 frames), place `pc=0x2e9e4`, run to `0x2e9ee`: the window
   `0x16000960` equals frame[i]; `0x49b18` equals it; r4-r11, sp preserved; `P.tx_i` advanced; after the last frame the PTT group flag `0x3a4f0` has bit `0x10`
   (read the flag group); silence after the end; `P.sending` clears after n+40; with `P.sending=0` the window equals the original buffer.
3. RX hook: pre-fill `0x16000960` with a pattern, run `pc=0x2ed60..0x2ed68`: `P.frame[n]` equals the pattern, `n` caps at CAP, no capture when `cap_active==0`.
4. Pure logic (state machine, caps, watchdogs, TOT clip, abort) in a host unit test with stubbed `0x3bd50`/flag-post/ticks (like `tests/test_grid.c`).
5. Screen rendering and key handling with the existing UI key scripts.
6. Round trip RX hook -> TX hook through the baseband RAM: confirms both use the same 27 bytes unmodified.

Not testable in QEMU: modem acceptance, timing against the DMR slot, whether the PTT press reaches `0x3bd50` on DMR (call setup needs the modem), TOT, target
selection, `0x167d2` tail burst, any RF. Optional stretch (LOW-MED effort): run the stock encoder task in place by posting its task semaphore 6 times per burst
with `[0x48160+0x1728]=1` and check that exactly one write per 6 posts goes through the hook.

## 10. Hardware ladder (for later; nothing here touches a radio)

1. Flash with hooks installed but `P.armed=0` (hooks are pass-through): normal DMR RX/TX must be bit-identical to before (compare TX record ring / a normal QSO).
2. RX capture only: dump the SRAM buffer after a call with the RAMDUMP probe; check frame count = duration/60 ms and the Golay-structure check of 2.3 against a TX-recorded burst.
3. Replay to the speaker through the stock playback ring (`0x4a73c`, recipe L in parrot-recording.md) to prove the stored frames decode to the original audio.
4. TX into a dummy load / second radio: single 3 s echo; observe target (TG vs private), CC/slot, and the end tail.
5. Only then lengthen, enable auto-echo.

## 11. Corrections to `docs/parrot-recording.md`

- 3.2 / 4: the insertion point is now fixed: four `bsr 0x172f8` sites, two live (IRAM `0x2e9ea`, `0x2eaf0`); `0x2e5a0` is the encoder task entry, not "FUN_0002f000"; the RX read
  is at IRAM `0x2ed64`; "0x2e8xx..0x2eb30 and 0x2e1d4" need no further tracing for the substitution.
- 4 step 2/3: the TX-up test for DMR is `0x3bd50 & 1`, not `call_active & 8`; the burst-synchronous unkey from the hook replaces the polling release.
- 5: prios 15 and 16 are the IRAM vocoder tasks (the dm32uv-re rtos-and-memory.md "free" list is wrong about them); free are 9 and 30 (and 39-57).
- 3.2: "stop the mic encoder from interfering / AUDIO_CONTROL bit0" is not needed with substitution; the mic still runs and is simply overwritten.
- IRAM function hooks cannot go in sector `0x030c6000` (QEMU-only); the sector `0x030DE000` sites avoid it.

## 12. Open risks

1. Format symmetry (2.3) and the modem's tolerance of replaced payloads: needs the hardware ladder; no QEMU evidence possible.
2. First IRAM splice in this image: a bad write of sector `0x030DE000` breaks the vocoder (flash, verify, keep the rollback file).
3. SRAM claim conflict with `mi_rng_tap_buf` must be resolved in `patches/mi_rng/allocations.json`.
4. Target selection after RX (reply logic) and menu-branch behaviour of `ptt_press_handler` for DMR from inside the KRAKEN screen.
5. Tail burst from the unhookable flush `0x167d2`; mic-derived, <=60 ms (MED).
6. Encrypted channels refused (keystream placement not traced); channel/TX permissions and echo etiquette are the operator's responsibility; keep the anti-loop hold-off.
7. Hook runs in a prio-15/16 task: it must stay tiny and never block; stack is the stock 1280 bytes.

## 13. Implementation (2026-10-29)

Built as designed in sections 3-8, with the corrections listed in 13.6. Everything here is RAM-only (no data-chip or settings writes)
and nothing was run on a radio.

### 13.1 Files

| file | what |
|---|---|
| `patches/kraken_ui/krk_vframe.c/.h` (blob2) | voice-frame layer: TX substitution hook, RX sink hook, counters, TX tap, silence burst, the shared bigbuf (`.bss.big`, 5616 B = 208 bursts) and its owner tag |
| `patches/kraken_ui/krk_parrot_sm.c/.h` (blob2) | the parrot state machine as a PURE function (host-tested) |
| `patches/kraken_ui/krk_parrot.c` (blob2) | the "Parrot" screen (KRAKEN slot 6), H1/H2 handlers, gates, provider, watchdog, UART diagnostics |
| `krk_ext.c`, `kraken_ui2.ld`, `krk.h` | hook table at `0x030ee040` (`krk_ext_hooks[4]`, `krk_ext_hook(i)` in `krk.h`): how the MAIN blob calls blob2 (blobs build main first, so a symbol bind is impossible) |
| `krk_lastheard.c` | H1/H2 wrappers call `krk_ext_hook(0)` / `(1)` after their own bookkeeping (no new splice at 0x030208ba / 0x03020984) |
| `krk_periodic.c` | `krk_vf_service()` and `krk_parrot_tick()` (watchdog) from `krk_hook_maint_tick` |
| `kraken_ui.patch.json` | `allow_occupied` 0x030de000..0x030df000, five splices `parrot_tx1..4`, `parrot_rx`, sources added to blob2 |
| `tests/test_parrot.py/.c`, `tests/parrot_qemu_test.py`, `check_f9` hook in `tests/kraken_ui_test.py` | tests (13.5) |

No builder change was needed: `allow_occupied` plus a branch target spelled `@kraken_ui2:sym + 0x30afc20` works as section 1 predicted
(the builder's own post-write check passes because it decodes from the flash address). The build reports each splice as a warning
("edits occupied region ... under an explicit allow_occupied entry"); that is the intended acknowledgement.

### 13.2 Hooks (all verified against the stock image and the IRAM copy)

| splice | flash | IRAM pc | stock bytes | now |
|---|---|---|---|---|
| `parrot_tx3` | `0x030de60a` | `0x2e9ea` live | `ff e3 87 44` | `bsr krk_vf_tx_hook` |
| `parrot_tx4` | `0x030de710` | `0x2eaf0` live | `ff e3 04 44` | same |
| `parrot_tx1` | `0x030de2e0` | `0x2e6c0` dead variant | `ff e3 1c 46` | same |
| `parrot_tx2` | `0x030de440` | `0x2e820` dead variant | `ff e3 6c 45` | same |
| `parrot_rx` | `0x030de984` | `0x2ed64` | `ff e3 b4 42` | `bsr krk_vf_rx_hook` |

Disassembly re-check (IRAM image, `csky-abiv2-elf-objdump`): at every TX site `movi r0,0`, `lrw r1,0x49b18` (pool words `0x2eb2c` and `0x2e864` read back as
`0x49b18`) and `movi r2,27` precede the bsr; the RX site is `mov r0,r11 / lrw r1,0x46b38 (pool 0x2eed8) / movi r2,27`. After the TX sites only r5, r6, r11 (callee-saved)
are used and `r2/r3/r12` are written before use; after the RX site r4/r8 are used and `r3/r1/r13` are reloaded. The C hooks preserve r4-r11, r14 (checked in target on
every call). The sites sit in the boot copy range (`0x030bfc20..0x030decb8` -> IRAM `0x10000..0x2f098`; test reads the pool at `0x0300c0d0`). Exactly 20 bytes of
sector `0x030de000` differ from stock; `0x030c6000` is untouched.

Idle cost of the TX hook: load+compare (magic), a counter increment, load+test (flags), then the stock call. The RX hook: stock copy, load+compare (magic), counter,
load+test (sink). Both are inert until `krk_vf_service()` has run (patch SRAM is noise at power-up), which the 1 Hz maintenance tick does.

### 13.3 Layer API and threading (`krk_vframe.h` has the full text)

`krk_vf_rx_sink(fn)`: `fn(frame27)` for every received burst while set (RX task, prio 15). `krk_vf_tx_begin(fn, max_bursts, max_ticks, tap)`: while the session lasts each
TX burst at `0x49b18` is replaced by `fn(dst27, idx)` (TX task, prio 16): return `FRAME`, `LAST` (the layer posts the PTT release right after that burst), `PASS` (leave the
microphone burst) or `NONE` (silence + release). `krk_vf_tx_end()`. F13 uses the same three calls with its own provider/sink; it takes the buffer with
`krk_vf_big_claim(2)` (the parrot uses owner 1 and releases it on disarm/exit). Counters (`krk_vf.tx_calls/tx_subst/tx_silence/tx_pass/rx_calls/rx_sunk/rel_posts`) have one writer
each. No locks: the owner fills a session then sets `flags` last; hooks only ever read. A hook that races an owner update sees at worst one burst of silence or stock audio.

The layer protects itself, whatever the owner does: a session older than `max_ticks` (also a stale session after a warm reset: the tick counter restarts, the age wraps) is dropped by the
hook; after the end every burst is silence and the release is re-posted on every 8th burst (480 ms) for at most 100 bursts; the TX tap keeps the first 4 stock bursts of a session.
Silence burst: three copies of the MMDVM `NULL_AMBE` frame `B9 E8 81 52 61 73 00 2A 6B` (comfort-silence AMBE+2 in over-the-air bit order). UNVERIFIED for this modem (no silence
capture exists; it is not in the stock image or IRAM). See 13.7 for how the hardware round settles it.

### 13.4 The screen, the safety layers, RAM

KRAKEN > Parrot (slot 6). Keys: OK arm/disarm, `1` play last, `2` self-check, `0` clear capture, `3` UART dump (counters line `PARROT chk`, then ALL captured bursts `rx0..rx(n-1)`, 8 lines per 100 ms tick so the screen never blocks long, then `tx0..tx3` and `sil`; compare the rx lines unit by unit with the sender), Up/Down lead silence bursts (0..8, default 2), Back = disarm and exit.
Armed lines: state (IDLE / REC / HOLD n.ns / KEYING / SEND / UNKEY / COOL + last reason), `Cap n fr s.ss`, the call (TG/PC/ALL, from ID), `TX i/n` or echo count, counters
`txN subN rxN`, the first failing gate, key help. Disarmed at every boot (state validated by a magic word and the tick counter) and whenever the screen is left.

Flow: H1 (`krk_hook_lh_start`, every voice header) starts a capture when armed and idle (not our own ID: `0x33cd0`); the RX sink stores each burst in bigbuf (cap 208 bursts = 12.48 s, the rest
dropped); H2 ends it. The screen tick (100 ms) runs `ps_step`: 1.5 s of quiet after the call end -> gates -> `krk_vf_tx_begin` + press (`0x08` on group `0x3a4f0`) -> TX voice up (`0x3bd50 & 1`) -> the TX hook
substitutes: lead silence, the stored bursts, release after the last -> TX down -> 3 s hold-off. One echo per call; a new call at any time cancels a pending echo.

Safety layers (each implemented, the ones marked T are tested in target or on the host; HW = needs a radio):

| # | layer | where | test |
|---|---|---|---|
| G | gates at the key moment: armed, capture >= 5 bursts, digital channel on the SELECTED side (`0x3e1e4` nibble 1 + `krk_channel_on_selected`), no stock cipher (`0x33824 == 0`), stock TX allowed (`0x3e31b` bit 5 clear), channel idle (`stock_rx_active() == 0`, `0x3bd50 & 3 == 0`), `tx_is_inhibited() == 0`, source != own ID | `gate_block()` | T (each gate individually, in target) |
| T | TX budget: `(lead + bursts) * 60 ms + 1 s` from TX-up; past it the release is re-posted every 500 ms; +1 s later the substitution is ended by force | `ps_step`, `ps_watchdog` | T host + target |
| H | the hook posts the release itself after the last burst, then on every 8th silence burst | `tx_session` | T target |
| W | watchdog from the screen tick AND from `krk_hook_maint_tick` (`krk_parrot_tick`) | `ps_watchdog` | T target (screen not running) |
| K | any key aborts: release + substitution end (also cancels a pending echo) | `ps_step` abort | T |
| X | leaving the screen disarms: release, wait <= 2 s for TX down (release every 500 ms), drop capture, release bigbuf | `parrot_exit` | T |
| L | KEYING gives up after 3 s without TX voice (release); RX appearing while keying aborts; stock ending the TX (TOT/PTT) ends the session | `ps_step` | T host |
| S | exhausted provider / cap -> silence burst + release, never stale data; the hook only touches `0x49b18` when `src == 0x49b18 && n == 27` | `tx_session` | T target |
| R | warm reset / noise: layer and parrot state re-initialised (tick counter below the stamp); stale sessions dropped by age | `krk_vf_service`, `pinit` | T target |
| F | fuzz: 400 000 random steps assert "press only when armed, gates open, begin issued, not already keyed" and "never keyed while disarmed" | `tests/test_parrot.c` | T host |

Not covered (accepted, documented in section 6.3 F9): the user pressing the real PTT while a session is active transmits the stored bursts until the session ends (<= 13.6 s + 1 s). The stock
TOT (min 15 s when on) is longer than the worst case 12.48 s + lead + 1 s; the echo does not read the TOT setting. Repeater etiquette, licence and the one-echo/hold-off policy are the operator's responsibility.

Burst voice (F13, `docs/burst-voice.md` 11.2) takes the same buffer as owner 2: while its screen is open, or a message is kept after Back, the parrot's arm key is refused (`krk_vf_big_claim` fails, PR_BUSY); while the parrot is armed the Burst Voice screen shows "Parrot is armed" and touches nothing in the buffer.

RAM: `.bss.feat` 273 B of the shared 1280 B (`krk_vf` 176 B, parrot state 96 B, owner tag 1 B); `.bss.big` 5616 of 5632 B; blob2 plain `.bss` unchanged. Flash: blob2 +6.1 KB (parrot 4161 B, SM 1069 B, layer 853 B, hook table 16 B);
the main blob grew by about 40 B (two `krk_ext_hook` calls).

### 13.5 Tests

| test | what | image |
|---|---|---|
| `python3 tests/test_parrot.py` (host) | 50 checks of the pure state machine: happy path timings, each gate, abort, no-TX-up, stock end, deadline, watchdog, disarm from every keyed state, a 400 000-step property run | host cc |
| `tools/build.sh` -> `tests/kraken_ui_test.py::check_f9` | static: five splices decoded FROM THE IRAM PC (the naive flash-pc decode lands at 0x61a2a94, shown in the output), stock bytes, call-site context, pool words, boot-copy range, 20-byte sector diff, forbidden sector untouched, hook table, symbols/claims. In target: TX hook at both live real sites (`0x2e9e4`/`0x2eaea` run to the instruction after the bsr), RX hook at `0x2ed5e..0x2ed68`, H1/H2 through the main blob's wrappers, gates, press/release posts (intercepted at `OSFlagPost_wrapper_set`), abort, watchdog, stale session, warm-reset init, disarm on exit | qemufix3 (window checks off) |
| `tests/parrot_qemu_test.py --window` | the same in-target run with the baseband window checks on: the stock write reaches `0x16000960` with exactly the substituted bytes, the RX capture equals the window bytes, `0x46b38` still gets the stock read | `dm32uv-re-pipeline:models-20261014` (only that image's `dm32_dmrram` retains the window) |

QEMU cannot show: modem acceptance, DMR timing, whether `0x3bd50` rises on a synthetic press from inside a menu, TOT, target selection, the tail burst, the silence pattern.

### 13.6 Corrections to the earlier sections

- `0x320af` is NOT a "CPS link" flag: it is the stock screen-mode byte (7/14/21 while a KRAKEN screen runs, `docs/menu-exit-timer.md`). Section 6.1's gate "CPS link `0x320af == 0`" would block every echo from the screen, so it is not used.
- The SRAM claim problem of section 5 is solved by `image.json` excluding `mi_rng_tap_buf`; the buffer is 5616 B (208 bursts, 12.48 s), not 8 KiB.
- Own-ID filter and the "no TX voice within 3 s" timeout are in the state machine (section 7 listed them as interlocks).
- Leaving the screen disarms (section 7 had a replay key that kept working; with nothing watching, nothing keys).
- Hear (speaker playback of the capture through the stock ring `0x4a73c`) was NOT built: it needs the stock playback path (section 2 of parrot-recording.md) which blocks its caller for the clip length; the self-check below proves the stored bursts decode on the radio that sends them.

### 13.7 Hardware test (ONE action per round)

Prerequisites: the radio under test on a digital simplex channel that works (CC, TS, TG), no stock cipher, into a dummy load, with a second DMR radio on the same channel nearby; UART capture running on the host
(any terminal on the programming cable at 115200, log to a file).

Round 1 (the loopback self-check, one key, the radio under test alone): KRAKEN > Parrot, OK (arm), `2`. It keys up by itself on its channel for about 1.3 s: 4 stock (microphone) bursts are passed and tapped first, then 0.9 s of silence bursts
are substituted (or the stored capture if one exists), then the layer releases. The screen then shows `chk up0.4s rel1`, `1s rx..% tx..%`, and the counters line; `PARROT chk ...` is printed on the UART with all counters and hex dumps of
4 captured RX bursts (if any), the 4 tapped stock TX bursts and the silence pattern. Reading it:

| symptom | failed stage |
|---|---|
| `gate: ...` shown, no TX | a safety gate (the reason names it) |
| press posted, `chk up NO` | stock never raised TX voice (menu branch of `ptt_press_handler`, busy, TOT): the TX hook is never reached |
| `up` ok but `tx_calls` unchanged | the encoder task never reaches the hook (splices/IRAM) |
| `tx_calls` grows, `subst` = 0 | session/provider problem (the `PARROT chk` line shows `state`, `why`) |
| `subst` right, `rel` = 0 or TX stays up | release path |
| all counters right, nothing on the second radio | modem does not accept the substituted bytes (format) or the silence frame is wrong: next round |

Format check, folded into the same round: compare the `tx0..tx3` lines (stock bursts from the microphone path, TX side) with `rx0..` (a received call, RX side; do a round 2 first if none was captured) and `sil`.
Expected if the symmetry of 2.3 holds: roughly 50% one bits in both (the `1s rx/tx` percentages), AMBE frame structure with high-entropy bytes, `sil` close to a TX burst taken while the room is silent. If RX and TX bursts differ in polarity or
bit order, the byte pairs will show it (e.g. every byte inverted or bit-reversed). Settle with a quiet-room tap: `tx0..tx3` are 240 ms of room noise; the silence burst should look similar in structure.

Round 2 (the parrot): the radio under test armed on the screen; the second radio transmits a short call (2-5 s, group call on the channel) -> the screen goes REC (green `Cap`), HOLD (1.5 s), KEYING, SEND `TX i/n`, COOL. The second radio hears its own call back.
Then `1` replays the same capture on demand. Any key aborts; Back disarms.
If round 2 is garbled: change the lead (Up/Down) and repeat `1`; send `3` after the call for the UART dump of the captured bursts; the next suspects are the silence pattern and the 0-2 burst lead.

### Review fixes (2026-10-29)

A static review of the first implementation led to these changes (verified against the code first; all findings stood).
- **Press/release crossing (HIGH).** The press is posted at step N and nothing proves the stock PTT task has acted on it
  until TX voice (`0x3bd50` bit 0) is seen, so every "TX is down" decision made inside that window was premature. The state
  machine now has a **watch** (`ps_watch`, `krk_parrot_sm.c`): it starts at the press post and ends only when TX voice has been
  continuously down for 1.5 s (`PS_WATCH_TICKS`) AND 1.5 s have passed since the press and since the last release post.
  While the watch runs, TX voice up with the state machine saying OFF/IDLE/COOL (or past the deadline) posts the release at
  once and then every 500 ms, whatever the state; a screen-less frozen SEND state is ended by the same function. It runs in the
  100 ms screen step and in the 1 Hz maintenance tick (`krk_parrot_tick`), so it also covers the time after Back disarmed
  the parrot. `disarm()` no longer waits 2 s in the menu task: it posts a release, goes silent and leaves the watch to the tick.
  A new echo is refused (`gate busy`) while the watch is on. A key press during the first 1.5 s after an echo (while TX voice is
  up) is released too: that is the price of the safe direction and is documented, not hidden.
- **Silence, never the microphone (7).** `krk_vf_tx_quiet()` (abort, UNKEY, STUCK, disarm, watch): sets `max_bursts = 0` so the
  next burst is silence + release, and the `quiet` flag stops the grace (100 bursts) and age limits from handing the microphone
  back while TX voice is up (30 s hard cap; a session older than `max_ticks` with TX voice up also goes quiet instead of
  being abandoned). `krk_vf_tx_end()` is called only when the watch ends. The provider returns NONE if the buffer is gone.
- **Gate re-check (8).** Immediately before the press post `step()` re-evaluates every gate (`gate_block`: channel idle,
  digital, TX allowed, not busy) and that the capture is unchanged (`P.n` snapshot, `P.rec` clear); on a mismatch the
  state machine is put back to IDLE with the reason, nothing is begun or posted. H1 ignores calls once the state is KEYING.
- **Dual watch (5).** Refused: H1 does not capture while `dual_watch_state()` (0x030641e4) is on, the key-time gate refuses
  (`dual/side chg`) while it is on, and the loaded side recorded at H1 (`0x400d9` bit 0) must equal the loaded and the
  selected side at key time.
- **Reboot detection (9).** `krk_rebooted(&stamp)` (`krk_vframe.h`, shared with F10): reboot if the 100 Hz counter is below
  the stamp OR the stamp is more than 30 s old (every owner is refreshed at least once a second by the maintenance tick), which
  also catches a previous run that died early. Applied to `P`, `krk_vf` and F10.
- Tests: `tests/test_parrot.c` (watch, crossing, frozen SEND), `tests/parrot_qemu_test.py` (press crossing with late TX
  voice, silence while quiet, maintenance tick alone, gate race via a scripted `stock_rx_active`, dual watch / side, stale-stamp
  reboot, SCRAMBLE_REG from the TX hook). `.bss.feat`: `krk_vf` 180 B, `P` 108 B.

## Result and removal (v52)

The instrumentation described below was built in v51 and removed in v52 (krk_rxlog.c/.h, the voice-frame hook glue, Parrot key 4, the ui4 table at 0x030e8024 and the capture tail are gone; the Parrot capture is back to 208 bursts / 12.48 s). Its question is answered:
torn reads 0; duplicate units (14-15 per call) appeared only while the receiving radio's RAM was being read over its programming cable (the cable traffic delays the RX voice task, late reads repeat the previous burst), 1 per call otherwise; scrambler case C was 53/53 bit-exact when the receiving radio's cable was idle (hardware, 2026-11-01). The text below is kept as the record of the method.

## RX path instrumentation (2026-11-01)

Why: the v50 scrambler captures showed 19 of 50 units random with both radios scrambling, about 10 adjacent duplicate units in every capture (also scrambler off) and `rx_calls` about twice `rx_sunk`.
The RX sink sees the same buffer (0x46b38) stock's vocoder decodes, so the question is whether stock's own read is stale or torn (a listener hears it too) or only our capture is wrong.

What it records (`krk_rxlog.c`, called from `krk_vf_rx_hook`, only while the log is on): per `bsr 0x172cc` call the OS tick (100 Hz, 0x426d0) and `TIMER.CH[0]` down-counter (0x14000004, read as in `krk_tm_us`, sampled before the stock copy, re-read if the counter reloaded in between), `off`, `n`, the first 4 bytes of the read, and for `n <= 27` a SECOND read of the same window into a stack scratch right after the stock read (dst is never written by it),
with an FNV-1a hash of each read. Hash1 != hash2 means the window changed under the first read (`TORN`). Calls with `n != 27` are logged with their first bytes (the bytes stock read into dst) and, up to 27 bytes, their own second read; `n > 27` shows `skip`.
Counters over ALL calls since the call start: `calls`, `n27`, `torn` (any `n <= 27`), `dup` (a 27-byte unit whose hash1 equals the previous 27-byte unit's), `other_n`.

Arming: nothing extra. The log starts at H1 (new call accepted while the Parrot is armed: ring and counters reset) and stops at H2 (call end), so the ring holds the first 64 calls of the call (about 1.9 s of a call with the 2 calls per 60 ms seen in v50; later calls are only counted). Outside a call and with the Parrot disarmed the hook pays one load and test.
Storage and placement: the shared `.bss.feat` claim and the blob2 flash are both full, so
- SRAM: header (32 B: `on`, counters, `load`) plus ring (64 x 24 B = 1536 B) are the TAIL of the shared big buffer (`kraken_bigbuf`, owner Parrot), header at bigbuf+4048, entries at bigbuf+4080. **The Parrot capture is now 149 bursts / 8.94 s (was 208 / 12.48 s).** `.bss.feat` is unchanged (`krk_vf` 180 B, `P` 108 B: the dump index took a padding byte). The RX hook trusts the header only while `big_owner == 1` (the parrot); `krk_vf_big_claim` clears `on` before it hands the buffer over.
- Flash: the recorder, `krk_rxl_line` and `krk_rxl_summary` (`krk_rxlog.c`, about 1.0 KB) are in the F13 blob (kraken_ui4, which had room and is built after blob2), reached from blob2 through the 3-entry table `krk_rxl_tbl` at the fixed address 0x030e8024 (right behind the F13 hook table at 0x030e8020, `kraken_ui4.ld`); blob2 checks that all three point into 0x030e8000..0x030ee000 (erased flash = absent: the log is then silently off, key 4 prints nothing). Blob2 itself grew by the hook glue and the key-4 code only (see the build summary).
- Hook stack: 27 B scratch + a few words.

Dump (Parrot screen, key 4, only after the call ended): UART lines, 6 per 100 ms tick (about 62 chars each, like the key 3 dump), then the summary:

    VFLOG i t_us dt_us off n b0b1b2b3 sum1 sum2 same|TORN|skip
    VFLOG sum calls=.. n27=.. torn=.. dup=.. other_n=.. logged=..

`t_us` is relative to entry 0, `dt_us` to the previous entry (ticks x 10 ms plus the counter difference over `LoadCount/10000` counts per us). Key 4 prints `VFLOG none ...` without a ring and `VFLOG busy` while a call runs. The help line is `0clr 3dump 4log U/D`.

Reading it: `TORN` lines with matching `dt_us` jitter mean the window is read mid-update, so stock decodes garbage too (a real, audible defect, and our capture reads the same garbage). `same` everywhere with duplicated units (`dup` high) means the window itself repeats the previous unit (stale read, or the modem delivers 2 reads per update),
and the `n != 27` lines (see `n`, `off`, `b0..3`) show what the second half of the calls read. dt_us of about 30 ms vs 60 ms tells whether the calls come in pairs.

Hardware procedure (ONE action): the receiving radio on KRAKEN > Parrot, OK (armed); a second radio makes a 3 s DMR call on the channel; after the call ends press `4` on the receiving radio and capture its UART (programming cable, 115200) for about 15 s. Do the same with the scrambler on both radios (key 12345) to compare TORN counts. Tests at the time: a host test of the ring, torn injection, counters and line format (removed with the instrumentation) and the F9 in-target checks (`tests/parrot_qemu_test.py`: log reset at H1, 40 bursts logged, stopped at H2, dst untouched).
