# Last-heard list: hook points in stock DM32.00.01.048

Static analysis only (corpus: [dm32uv-re](https://github.com/awkaplan/dm32uv-re)). Nothing here was run on
a radio or in QEMU. Every address below is a literal-pool-resolved value read from the stock
image (`progmem_stock_DP570UV_20260926_FULL.bin` / `IRAM_dm32.00.01.048.bin`), not a Ghidra
name guess. Corpus function names in this area are often wrong; where I rely on one I say so.

Confidence tags: **HIGH** = read directly from disassembly and cross-checked by a second
independent code path; **MED** = one code path, or inferred from UI behaviour; **LOW** = guess.

## 0. How a received DMR voice call flows (the short version)

```
HR_C7000 baseband  --(L2 interrupt, ISR in IRAM 0x2c454)-->  snapshots RX_TYPE_INFO/RX_INFO to RAM
   deferred dispatcher FUN_03023a68 (flash 0x03023a68)
      bit 0x4 -> FUN_03028c78                       embedded-LC in voice superframes (late entry + TALKER ALIAS blocks)
      ... frame-type jump table @0x30af5a4: idx1 -> dmr_call_setup_response_process_and_signal @0x03028338  (Voice LC Header)
                                            idx2 -> dmr_call_setup_frame_unpack_and_dispatch  @0x03028214  (Terminator with LC)
        each: reads the 12-byte LC from the modem buffer, stores src/dst/type into block D (0x3c5f8),
              posts event flag 0x2 (call start) or 0x4 (call end) to flag group *0x300c784 (= 0x36570)
   task "rxvoice" = rxvoice_task_entry @0x03020778 (pends 0x40006807 on that group)
      flag 0x2 handler (0x03020842..0x0302095e): copies D -> snapshot block B (0x3bd2c), starts audio/UI
      flag 0x4 handler (0x0302095e..0x03020994): ends the call
   UI task ui_event_flag_dispatch_loop @0x0305ee70 -> event_flags9_dispatch_and_name_encode @0x0305ed30
      event9 bit0 -> dialog_tick_event_dispatcher @0x0305e02c   fills tx_ptt_ui_state src/type (what the screen draws)
      event9 bit3 -> encode_name_tag_field @0x0301ef14           decodes talker alias bits -> string at 0x3e0ff
```

Both start paths (Voice LC Header, and embedded-LC late entry) converge on flag 0x2 in `rxvoice`.
Both end paths converge on flag 0x4. That convergence is why the best hook is inside `rxvoice`.

The corpus calls 0x03020778 `rxvoice_task_entry` in one place and `dmr_tx_state_machine_main_loop`
in another (the latter is the corpus's older name; same function, dm32uv-re `docs/encryption.md` confirms
the task is "rxvoice", TCB 0x364a8).

## 1. RAM layout (the data a hook reads)

### 1.1 Block B: "current RX call snapshot", base 0x0003bd2c  (best source)

Written in exactly one place: `rxvoice_task_entry` flag-2 handler, `0x0302084e..0x030208b0`. Not
cleared at call end (the flag-4 handler never touches it), so it still describes the last call
when the end hook runs.

| addr | size | field | evidence |
|---|---|---|---|
| 0x0003bd2c | u8 | call type: **1 = group (FLCO 0), 0 = private (FLCO 3)** | `0x0302084c ld.b r3,(r6,0x8); 0x0302084e st.b r3,(r10,0x0)`; D[8] is set to 1 when FLCO==0 and 0 when FLCO==3 in both `dmr_call_setup_response_process_and_signal` (`if ((bVar12&0x3f)==0) ...(+8)=1 else if (bVar13==3) ...=0`) and `FUN_03028c78` |
| 0x0003bd2d | u8 | dst class: 0 = n/a (private), 1 = ordinary group id, 2 = id in 0xFFFFE0..0xFFFFEF, 3 = id in 0xFFFFF0..0xFFFFFF (all-call range) | `0x0302086e..0x030208aa` (range tests on the dst word, writes 1/2/3 to `(r10,0x1)`); only evaluated when type==1, else forced 0 at `0x030208b0` |
| 0x0003bd30 | u32 | **destination ID** (talkgroup or private target), 24-bit value in a u32 | `0x0302085c st.w r1,(r10,0x4)` where r1 = D[(slot+6)*4]; D[(slot+6)] is loaded from LC bytes 3..5 (see 1.2) |
| 0x0003bd34 | u32 | **source ID** (the talker), 24-bit value in a u32 | `0x03020866 st.w r2,(r10,0x8)` where r2 = D[(slot+8)*4]; from LC bytes 6..8 |

Readers that confirm the meaning: `radio_mode_classify_from_status_bytes` @0x03020e00 (reads
B[0], B[1]) and `dialog_state_get_field_03020df8` @0x03020df8 (`ld.w r0,(B,0x8)` = src) /
`dialog_state_get_field_03020e28` @0x03020e28 (`ld.w r0,(B,0x4)` = dst). The voice-recorder
header builder `channel_activity_log_flush_task` @0x030212f4 writes B+8 first then B+4 into the
recording header. **Confidence HIGH.**

`radio_mode_classify_from_status_bytes` is a leaf (no push, `rts`) and a ready-made call-type
decoder: returns **1 = private, 2 = group, 3 = all-call**.
(B[0]==0 -> 1; B[0]==1 && B[1]!=3 -> 2; B[0]==1 && B[1]==3 -> 3; B[0] other -> 3.) Disassembly
`0x03020e00..0x03020e26`. These are also the values `tx_ptt_ui_state.field_0x14` receives.

### 1.2 Block D: per-slot LC receive state, base 0x0003c5f8

D is the base the flag-2 handler holds in `r6`. The two-element arrays are indexed by the slot
index. Slot index = byte at 0x0003be8e (see 1.4).

| addr | field | evidence |
|---|---|---|
| D+0x08 = 0x3c600 | u8 call type (1 group / 0 private) | above |
| D+0x0b = 0x3c603 | u8 LC **service-options byte** (LC byte 2) | `0x030282ce st.b r1,(r2,0xb)` (r1 = LC byte at sp+8+2); `dmr_call_setup_response_process_and_signal`: `PTR_DAT_030285c8[0xb] = PTR[slot*0xc+0x2a]` |
| D+0x06 = 0x3c5fe | u8 **received colour code** = `(RX_TYPE_INFO >> 4) & 0xf`, reset to 0xff by `modem_control_state_stop` | `FUN_03028c78`: `*(byte *)(D + 6) = (byte)(uVar9 >> 4) & 0xf` where uVar9 = `*0x3beb0` (RX_TYPE_INFO snapshot); `0x030293be st.b r1,(r2,0x6)` with r1 = -1 |
| D+0x18 + 4*s = 0x3c610 + 4*s | u32 dst ID for slot s | `0x0302829e str.w r0,(r2,r1<<2)` with r1 = slot+6 and r0 = the first out-param of `call_setup_target_id_decode_and_check` (sp+4) |
| D+0x20 + 4*s = 0x3c618 + 4*s | u32 src ID for slot s | same, r3 = slot+8, value = out-param sp+0 |
| D+0x28 + 12*s = 0x3c620 + 12*s | 12-byte raw LC copy for slot s: [0] FLCO|PF, [1] FID, [2] service options, [3..5] dst (BE), [6..8] src (BE), [9..11] unused/CRC | `copy_bytes_from_offset_table(0, D+slot*12+0x28, 9)`; decode in `call_setup_target_id_decode_and_check` mode 1: dst = b3<<16 | b4<<8 | b5 (`0x03027742..0x03027754`), src = b6<<16 | b7<<8 | b8 (`0x0302775e..0x0302776e`) |
| D+0x0c+s = 0x3c604+s | u8 "slot s has an active call" (1 after header, 0 after terminator / modem_control_state_stop) | `dmr_call_setup_response_process_and_signal`: `...(+slot+0xc) = 1`; unpack: `...(slot+0xc)=0` |

**Argument order trap:** `call_setup_target_id_decode_and_check(mode, lc, &out_a, &out_b)` returns
**out_a = destination, out_b = source** (LC order is dst then src). The corpus decompile names them
`local_18/local_1c` with no semantics, and the first reading of `rxvoice` suggests the opposite.
The proof is `0x03027742` (`ld.b r12,(r1,3)` -> shifted 16 -> `st.w r0,(r2,0)`), i.e. LC bytes 3..5
land in the first out-param. Confirmed independently by the three `FUN_03028c78` store sites
(`Dpair[(slot+6)] = bytes 0x2b..0x2d` of D+slot*12, `Dpair[(slot+8)] = bytes 0x2e..0x30`).
**Confidence HIGH.**

The FLCO source: ETSI FLCO 0 = Group Voice Channel User, FLCO 3 = Unit-to-Unit Voice Channel User.
Other FLCOs (4..7 = talker alias, 8 = GPS, etc.) are not stored as call info.

### 1.3 Block A "call setup state", base 0x0003ba38 (= `p_call_setup_sm_state`)

`rxvoice` re-copies, on every wake (before testing any flag): A+0x09 = D[0xb] (service options),
A+0x0c = dst (D[(idx+6)*4]), A+0x10 = src (D[(idx+8)*4]) (`0x030207b8..0x030207ce`). So A is also
a valid source, but it is overwritten on every wake including wakes for other events, so B is
cleaner. A+0x00 = RX state (1 active, 2 after flag 0x2000, 0 after flag 0x4), A+0x01 = 1 while an RX
call is up (set at `0x030208b8`, cleared at `0x03020988`).

### 1.4 Timeslot

- Slot index (0/1) = byte at **0x0003be8e** (= `p_dmr_call_config_state` 0x3be8c + 2, "active
  call slot"). `rxvoice` uses it as the array index: `0x030207c0 ld.b r3,(r8,0x2)` (r8 = 0x3be8c)
  then `addi r2,r3,6; ldr.w r2,(r6,r2<<2)`. `FUN_03028c78` sets it from the current-burst slot
  byte 0x3be8c when no call is active (`_DAT_030292bc[2] = bVar8`). The current-burst slot byte
  comes from `modem_mode_select` @`0x0302337e..0x0302338a`:
  `LAYER2_STATUS(0x1100041c) bit29 XOR 0x3be9b`. **MED**: it is certainly the slot index the
  firmware uses; that index 0 means TS1 and 1 means TS2 is an inference (no code that prints
  "TS" from it).
- The radio's **configured** timeslot is `vfo_display_state.flags_0xd` bit 4 (0 = "T1", 1 = "T2"),
  address **0x0003e1e9** (p_vfo_display_state = 0x3e1dc, +0xd). Evidence: `talker_alias_info_screen`
  `0x0302bbd4..` draws the string `0x3154` ("T1") or `0x3254` ("T2") at (0xfa,0x0f) depending on that
  bit, and `vfo_field_write_bit4_at_0xd` @0x03056c8a is the menu writer. **HIGH** that this is the
  channel's TS. Since the radio listens on one slot, configured TS == received TS in normal
  operation; use the configured value as the primary, the slot index as a cross-check.

### 1.5 Colour code

- Configured CC = `(*(u8*)0x3e1e9) & 0xf`. Evidence: same screen prints it through the format
  string at 0x30af9a8 which is `"CC:%d"` (bytes `43 43 3a 25 64`) at `0x0302bbe4`
  (`number_to_string_format_2args(buf, "CC:%d", flags_0xd & 0xf)`). **HIGH.**
- Received CC (actual) = byte 0x0003c5fe (D+6). The firmware drops frames whose CC differs from
  `p_call_setup_sm_state_aux.field_0x5` (0x33cb1) unless `A.flags_0x18 & 8` (monitor): see
  `FUN_03028c78` `if (*(aux+5) != rxCC && (A[0x18]&8)==0) return;` and the same compare at
  `0x03028226..0x0302822e` in the unpack. So CC at the hook == the matched CC. **MED-HIGH.**

### 1.6 Own ID (useful for "is this to me")

`p_call_setup_sm_state_aux.field_0x24` = word at **0x00033cd0** (0x33cac+0x24). The ID-filter
(`call_setup_target_id_decode_and_check`) compares dst against it. Also `*(u32*)0x0003eff8` is
compared in the call-log gate (below). **MED** which of the two is the canonical copy.

### 1.7 Talker alias

| addr | meaning | evidence |
|---|---|---|
| 0x0003ba57 (A+0x1f) | flags byte: bit0 = alias header seen, bit1 = repeated header, bits 6:3 = **data format** (0 = 7-bit packed, 1 = ISO-8 bytes, 2 = UTF-8, 3 = UTF-16 pairs) | `FUN_03028c78` FLCO 4 branch: `*(0x3ba54+3) = (*..&0x87) \| (bVar7>>6)<<3`; `encode_name_tag_field` switches on `[3]&0x78` |
| 0x0003ba58 | alias **length in characters** (5 bits) | `*(0x3ba54+4) = bVar7 >> 1 & 0x1f` |
| 0x0003ba59.. (28 bytes, 4 x 7) | raw alias bytes. Block FLCO 4 is stored at 0x3ba59 (its first byte is the header byte: fmt<<6 \| len<<1 \| first data bit), FLCO 5/6/7 blocks at 0x3ba60 / 0x3ba67 / 0x3ba6e (`0x3ba3d + FLCO*7`) | `FUN_03028c78`: `memcpy_bytes_iram(0x3ba54+5, LC+2, 7)` for FLCO 4; `memcpy_bytes_iram(0x3ba3d + (flco&7)*7, 0x3c622 + slot*12, 7)` for 5..7, then `OSFlagPost_wrapper_set_fixed_grp(0x40000000)` after block 7 and on a repeated FLCO 4 |
| **0x0003e0ff** (tx_ptt_ui_state + 0x3f), 0x20 bytes | **decoded alias string** (NUL-terminated, <= 0x12 chars, 7-bit ASCII / ISO8 / UTF8 passthrough; UTF-16 mapped through `name_tag_encoded_length_table_lookup`). The info screen draws it | `event_flags9_dispatch_and_name_encode`: `lrw r4,0x305ee60` (= 0x3e0ff), `bsr 0x0301ef14` with r0 = r4; `talker_alias_info_screen` draws `&field_0x3f` when `field_0x5f & 1` |
| 0x0003e11f (tx_ptt_ui_state + 0x5f) bit 0 | alias valid for display | set at `0x0305ede4 ins r3,r2,0,0` |
| 0x0003fd70, 0x20 bytes | copy of the last accepted alias (the dedupe reference) | `0x0305edc8..0x0305edd8` strcmp then memcpy |

Alias is not fixed per call: it arrives over ~4 embedded-LC superframes after the header, so it is
late relative to call start. The alias is wiped at call teardown: `call_teardown_on_event_flag`
@0x0305e220 does `memset(0x3e0ff,0,0x20)` and zeroes the two raw buffers; so a hook must read it
before then. **HIGH** on location and format; **MED** on the exact trigger that sets event9 bit3
(see Unknowns).

### 1.8 Name lookups the firmware already does

Two stock DBs. Both are flash reads under a mutex: call from task context only, never an ISR.

- `contact_name_lookup_or_format` @**0x03034d58**: `r0` ignored, `r1` = ID, `r2` = out buffer (needs
  17 bytes: 16 chars + NUL at +0x10), `r3` = type (the screens pass call_class+2: 3 = private/user
  contact, 4 = group, 5 = all-call; `talker_alias_info_screen` always uses 3 for the source). Returns
  **r0 = 1 and the 16-char contact name in the buffer if found**; returns **0 and `"%d"` of the ID
  in the buffer if not found**. Source: decompile + disassembly `0x03034d58..0x03034df0`
  (`bez r0,0x03034de2` is the not-found leg that calls `number_to_string_format_2args`). It
  binary-searches the contacts in flash block 0xb (the data chip). **HIGH.** Callable stock
  function, ABI-standard (r0-r3 in, r0 out, pushes r4-r9 + r15).
- DMR-ID user database (callsign/name/city/...): `flash_binary_search_le_record(id, outbuf)`
  @**0x03033598** returns the record index (1-based, <= 50000 entries, 0x5c-byte records, ID is 3
  bytes at record+0x10) and `flash_talker_record_read(index, buf)` @**0x03033718** fills a
  0x5c-byte record. The screen runs them only if the contact lookup missed (this is how
  callsign/name are drawn). `talker_alias_info_screen` buffer for it is 0x0003e460. **MED** on the
  record field layout (not decoded); HIGH on the call interface from the call sites.

For a RAM-ring last-heard list: store raw IDs, resolve names lazily at draw time through those two
functions. Don't resolve inside the receive hook.

## 2. Ranked hook candidates

All are existing 32-bit `bsr` call sites (opcode word `0xE000xxxx` pair), so the hook is a
**retarget of an existing `bsr`**, not a new bsr over an instruction. That makes r15 liveness a
non-issue for H1/H2/H3 (the original `bsr` already clobbered r15 at that spot). The wrapper must call
the original target (via `lrw rX,addr ; jsr rX`, saving/restoring r15 itself) and return its r0.

### H1 (recommended, call START): `rxvoice_task_entry` flag-2 block, `bsr read_byte_field_030238b4` at **0x030208ba**

- Stock bytes at 0x030208ba: `00 e0 fd 17` (= `bsr 0x030238b4`, target is a 3-instruction leaf
  `lrw r3,0x30238f8; ld.b r0,(r3,0x5); rts`, i.e. returns byte 0x3c5fd; the caller compares r0 to 2).
  Verify the bytes immediately before writing (the build's `expect` check does this).
- Containing function: `rxvoice_task_entry` @0x03020778, size 702, task "rxvoice", an endless loop.
- Why this spot: it is after B is fully written (`0x0302084e..0x030208b0`), after `A[0]=1,A[1]=1`
  (`0x030208b4..0x030208b8`), and before the audio/UI side effects, in the single task that owns B.
  So there is no re-entrancy and no race with the producer.
- State at the instant of the hook (registers set up by the loop prologue, `0x0302077c..0x03020792`):
  r4 = wake flags (bit1 set), r5 = 0x3ba38 (A), r6 = 0x3c5f8 (D), r7 = ptr to 0x30df3,
  r8 = 0x3be8c, r9 = 0, r10 = 0x3bd2c (B), r11 = 0. r0..r3 are free. The wrapper must preserve r4-r11
  (callee-saved) and return the original's r0 (the next instruction is `cmpnei r0,0x2`).
- Data available there: src = *(u32*)0x3bd34; dst = *(u32*)0x3bd30; type = *(u8*)0x3bd2c (1 group, 0
  private) and class *(u8*)0x3bd2d, or `radio_mode_classify_from_status_bytes()` (1 private / 2 group
  / 3 all); slot = *(u8*)0x3be8e; CC = *(u8*)0x3c5fe, configured TS/CC = `*(u8*)0x3e1e9`;
  service options = *(u8*)0x3c603.
- r15: `push {r4-r11, r15}` at 0x03020778 saved it; the function never returns; every other `bsr` in the
  loop already uses r15. Safe.
- Frequency: **once per call-start event**, in task context, but see "duplicates" below. Not per frame.
- Duplicates: flag 0x2 is posted by the Voice LC Header handler on every accepted header burst
  (no de-dup there) and again by the embedded-LC handler when the slot's active flag was 0 or the src
  changed (`(D[slot+0xc]!=1 || D[(slot+8)*4]!=src)`, `FUN_03028c78 0x03028fb6`). The event flag is a
  level (pend with consume), so bursts that arrive while the task is busy coalesce, but two headers
  seconds apart can both fire. The wrapper should de-dup: ignore if the most recent ring entry has
  same src + dst + type and no end event has been seen since. A talker change mid-call (different
  src) legitimately fires again and should create a new entry.
- Filter: only calls that pass the radio's receive filter reach here (`call_setup_target_id_decode_and_check`
  returned 0: for me / in my RX group list / CC match), so this is "calls I would have heard",
  not all channel traffic. That is the right semantics for a last-heard list.
- Confidence: addresses + fields **HIGH**; "fires once per call" **MED-HIGH** pending the de-dup note.
- Patch risk (corpus): sector 32 (0x03020000), `patch_risk medium`.

### H2 (call END): `rxvoice_task_entry` flag-4 block, `bsr modem_control_state_stop` at **0x03020984**

- Stock bytes at 0x03020984: `00 e0 14 45` (= `bsr 0x030293ac`). Target is a leaf that zeroes
  D[0xc..0xf], D[9], sets D[6]=0xff, clears a byte at 0x3029414+2 and `rts`. Alternative site
  immediately before it: `bsr tx_gate_state_transition` at 0x03020980 (`00 e0 4a 08`), which runs while D is
  still intact (choose this if the wrapper wants D[6]/D[0xc..0xf] before they are cleared).
- Block: `0x0302095e andi r3,r4,0x4 ; bez` -> clears the flag, `A[0]=0`, post 0x20000000 if `*(0x3ba20+5)`,
  `tx_gate_state_transition`, `modem_control_state_stop`, `A[1]=0`, `ui_group_flag_set_and_notify(1,1)`.
- State: r4 = flags (bit2), r5 = A, B still holds the finished call's type/src/dst (nothing in this block or
  after it writes B), so the ring entry can be closed (end timestamp / duration) using B alone.
- r15 safe (same reason as H1). Same task, so no race with H1.
- Frequency: once per end event. Flag 0x4 is posted from many places (Terminator-with-LC handler
  `dmr_call_setup_frame_unpack_and_dispatch` @0x030282e4/0x03028316, `modem_stop` 0x03023440,
  `dmr_call_config_reset` 0x302769a.., `dmr_incoming_call_setup_conflict_resolve`, `FUN_030286d8 0x3028a9e`),
  i.e. normal terminator, loss of signal and abort all end here. It can also fire when no call was
  up (A[0]==0); the wrapper should ignore ends without a preceding start.
- Confidence **HIGH** on semantics (it is the only place A[0]/A[1] go to 0 and where `ui_group_flag_set_and_notify(1,1)`
  is issued); **MED** that every audible call ends via flag 4 (hang-time handling not traced).

### H3 (talker alias, once per distinct alias): `event_flags9_dispatch_and_name_encode`, `bsr memcpy_bytes_iram` at **0x0305edd8**

- Stock bytes at 0x0305edd8: `7e e2 b0 62` (= `bsr 0x0002b338`, `memcpy_bytes_iram`).
- Context (`0x0305edc6..0x0305edf2`): the block reached from event9 bit3, after `encode_name_tag_field(0x3e0ff)` has decoded the
  alias; it then compares against the last accepted alias (`strcmp_byte_ordinal(0x3e0ff, 0x3fd70)`), and only if different
  does `memcpy_bytes_iram(0x3fd70, 0x3e0ff, 0x20)` (this site), then sets `tx_ptt_ui_state.field_0x5f` bit0 and `flags_0xa` bit6.
- State: at the call r0 = 0x3fd70 (dst), r1 = 0x3e0ff (**decoded alias string, NUL-terminated**), r2 = 0x20, r4 = 0x3e0ff.
  The wrapper must save r1 before calling the original memcpy. Preserve r4 (the code does `subi r4,0x3f` right after).
- r15: the function begins `push {r4, r15}` at 0x0305ed30. Safe (and it is retargeting a `bsr` anyway).
- Frequency: once per **new** alias string (de-duped by the stock strcmp), UI task context. Also gated by
  `(p_dialog_flags_from_flash[0x1c..0x1f] & 0xf0100) != 0x20100` (`0x0305edb0..0x0305edc4`), which skips it in some
  TX/dialog states. Pair the alias with the current call by reading B (src/dst) in the same wrapper and updating the
  matching ring entry (match on B src).
- Cleaner but more work alternative: replace the `bsr encode_name_tag_field` at 0x0305edaa (bytes `fe e3 b5 00`) and read
  0x3e0ff after the original returns. That fires on every event9-bit3 (not de-duped), so H3 is better.
- Confidence: location/format **HIGH**; "reached for every RX alias" **MED** (see Unknowns about who raises event9 bit3).
- Raw-bits alternative (no UI task dependency): read A+0x1f..0x3ba75 at call end (H2) and decode yourself
  (format per 1.7). The raw buffer holds the data until `call_teardown_on_event_flag` zeroes it. Needs your own decoder; **HIGH** that
  the data is there, but it is partial if the alias never completed.

### H4 (UI-side start, fallback): `dialog_tick_event_dispatcher` @0x0305e02c

- Two stores of the RX call into `tx_ptt_ui_state`: `+0x10` = src (`dialog_state_get_field_03020df8()` = B+8), `+0x14` = call
  class (`radio_mode_classify_from_status_bytes()`), then `*p_call_active_flag |= 1`. Same data as H1 but later, on the UI task, and the function has
  two near-identical copies of the sequence (event9 bit0 and bit1 paths), so there is no single clean site. Use only if H1 is blocked.
- Hook at the `bsr 0x03020df8` that precedes the store, e.g. the one whose operand sits at 0x0305e05a region (`0d 08 75 12` neighbours). Not recommended.
- UI-visible state (for a screen patch): `tx_ptt_ui_state` base **0x0003e0c0**: +0x10 = RX src (u32), +0x14 = RX class (1 private, 2 group, 3 all),
  +0x18 = TX target, +0x1c = TX class, +0x2e = contact-name string (also `p_caller_id_display_text`), +0x3f = alias. `p_call_active_flag` = byte **0x0003fdc8**:
  bit0 = RX call in progress, bit1 = TX in progress.

### Rejected / not useful, with reasons

| candidate (from the brief) | verdict |
|---|---|
| `talker_alias_info_screen` @0x0302ba20 | The in-call screen draw (status bar, T1/T2, CC:n, name/alias, call-type icon). Reads the UI state, writes the LCD. Redraws many times; not a data source. Good model for what the stock screen reads (see 1.x). |
| `talker_alias_screen_refresh_by_call_state` @0x0302c27c, `talker_alias_display_debounce_tick` @0x0302c3cc, `talker_alias_refresh_gate_by_dialog_flags` @0x0302d5b0 | UI refresh/poll wrappers around the draw. Per-tick. No. |
| `caller_info_dialog_screen` @0x0302d79c, `dmr_caller_id_log_and_display` @0x0304df24 | Misnomers. The first draws a TX-side caller dialog from `p_dialog_flags_from_flash`; the second writes a **contact index** to the data chip (`dmr_id_log_write`) and shows a number (channel/contact selection). Not RX. |
| `call_log_entry_render_*`, `scan_group11_call_log_record_or_notify` | The first two render the contact-list/dialog header; the third is a scan/channel record writer. Not the Call Log. |
| `call_event_record_append_to_history` @0x03029708, `history_record_push*`, `history_slot_allocate_and_init` @0x03029838, `dmr_history_bitmask_record_builder`, `modem_timeout_stop_and_history_record_task`, `tx_gate_write_call_record` | These feed a 24-byte-slot **layer-3 event queue** (`history_slot_allocate` / `history_slot_link_append`, drained by other tasks), not the user-visible Call Log. `rxvoice` pushes `history_slot_allocate_and_init(1, src)` and a type-5 record (+0xc src, +0x10 dst) on its flags 0x2000 / 0x800 (`0x030207e8`, `0x03020836`), which look like private-call-request style events and not ordinary voice. Not traced further. Use only if needed. |
| `flash_talker_record_read`, `dmr_contact_record_copy` | Not hook points; the first is the ID-DB reader (callable, see 1.8). |
| `contact_edit_background_commit_task_loop` @0x0302df64 | Despite the name, this is the post-call task that writes the stock Call Log (see §3). Fires only for private calls. Not usable for group calls. |

## 3. The stock Call Log: private calls to me only

Strings: top-level "Call Log" (0x30bc16c) with Missed (0x30bc584) / Answered (0x30bc58c) / a third entry whose string at 0x30be258 is not in the string index (presumably "Sent"); "Del Call Logs".

Writer: `contact_edit_background_commit_task_loop` @0x0302df64 (task, wakes on flag 0x1000) calls
`flash_history_record_append_with_date(list, ...)` @0x0305878c with `list` = 1, 2 or 3 depending on bits of
`tx_ptt_ui_state.field_0xc` (bit1 -> list 1; bit2 with bit4 clear -> list 3, bit4 set -> list 2; the list->name mapping
(Sent/Missed/Answered) is not pinned down). Each list is capped at 20 entries (0x14) of 14-byte records
(3-byte ID + date/time), stored in flash blocks, not in a RAM ring.

Whether group calls reach it: the bit that arms the "answered/missed" path is set only in
`tx_ptt_ui_field_set_on_call_match_gate` @0x0302c578:

```
if (!(radio_mode_state[1].flags_0x3 & 2) && tx_ptt_ui_state.field_0x14 == 1 /*PRIVATE*/ &&
    !(field_0xc & 0x10) && A.field_0xc == *0x3eff8 /*dst == own ID*/ && (settings[0x6f] & 1))
        field_0xc = (field_0xc & 0xfc) | 3;
```

So: **private calls addressed to my own ID only; group and all calls never enter the Call Log**,
and it needs a settings bit. **MED-HIGH** (one gate function + one consumer; I did not trace
`field_0xc & 4` for the missed case). The Call Log is therefore not a usable source or sink for
a last-heard list. A patch needs its own RAM ring, fed from H1.

## 4. What the stock receive screen reads (reference for a last-heard screen)

`talker_alias_info_screen` @0x0302ba20 RX branch reads: `tx_ptt_ui_state.field_0x10` (src) -> contact lookup
(type 3) into `+0x2e`; if not found -> `flash_binary_search_le_record` + `flash_talker_record_read` into 0x3e460; call class
`field_0x14` -> string table pointer `PTR_PTR_0302bc30` (language-indexed by `settings[0x30]>>6&1`, entries Private / Group / All);
`"CC:%d"` of `0x3e1e9 & 0xf`; "T1"/"T2" from bit4 of 0x3e1e9; alias at `+0x3f` when `+0x5f & 1`.
The draw primitives: `display_draw_string_gb2312` @0x03018998 (x,y,text,fg,bg), `display_fill_screen` @0x03017f54,
`display_draw_status_bar` @0x03019434, `number_to_string_format_2args` @0x0306fcf8.

## 5. Unknowns and weak spots

1. **Who raises UI event9 bit3 (alias decode)** and exactly when. I traced the consumer
   (`event_flags9_dispatch_and_name_encode`) and the raw-buffer producer (`FUN_03028c78`), which posts flag 0x40000000 to the
   rxvoice group after the last alias block (and on a repeated header). `rxvoice` pends that bit but I found no handler for it
   and no other pender; so the bridge from the raw alias to event9 bit3 is probably the generic UI event bytes
   (`ui_mode_group_flag_set_and_post(2, 8)` family) raised from somewhere I did not find (candidates: `dmr_call_setup_teardown_event_loop`
   calls it with (2,1)). Net effect: H3 reliably sees the decoded alias, but I cannot prove it fires for every alias that completes.
2. **TS1/TS2 from the slot index** (1.4) is inferred. Prefer the configured value at 0x3e1e9 bit4. A radio in dual-slot/monitor
   (`A.flags_0x18 & 8`) is the one case where received TS may differ from the configured one.
3. **Exact meaning of D[0xb] bits** (service options). Stock tests `& 0x30` in {0x20,0x30} only. Record raw.
4. **Late-entry timing**: for a late-entry start (no header), flag 2 comes from the embedded-LC handler and may be hundreds of ms
   after audio begins. Fine for a log; just note it.
5. **Hang-time / voice-call end**: flag 4 sources include abort paths. A short gap between two transmissions in one QSO may or may not
   produce an end between them. Make the ring entry "last heard time" = end if present, else last start.
6. **Colour code with monitor on**: with the monitor bit (`A.flags_0x18 & 8`) the CC filter is skipped; then the received CC (0x3c5fe) can
   differ from the configured one. Log 0x3c5fe, it is the truth.
7. **Contact lookup thread safety**: `contact_name_lookup_or_format` is stack-only plus mutex-guarded flash reads, which is why the UI calls it from
   several screens, but I did not prove it safe to call from the `rxvoice` task while the UI task is mid-lookup. Resolve names on the draw path,
   not in H1.
8. **Not verified under QEMU or on hardware.** Under the QEMU model the DMR baseband is not simulated (dm32uv-re `docs/qemu-workflow.md`), so H1-H3 can only be
   boot-tested (patched image reaches standby), not exercised with a call. Hardware needed to confirm fire counts.
9. IRAM-resident pieces (ISR at IRAM 0x2c454 and the superframe worker FUN_0002d6a4) were read but not needed; they only publish snapshots at
   0x3be84 (SYS_INTERP_LIST), 0x3be88 (RX_INFO), 0x3beb0 (RX_TYPE_INFO).

## 6. Summary of recommended design

- Start: H1 at **0x030208ba** -> read B (0x3bd2c): src 0x3bd34, dst 0x3bd30, type 0x3bd2c/classify, slot 0x3be8e, CC/TS
  0x3e1e9, rxCC 0x3c5fe; de-dup against the previous entry; append `{time, src, dst, type, ts, cc}`.
- Alias: H3 at **0x0305edd8** -> r1 = decoded alias (<= 0x12 chars + NUL) -> copy into the entry whose src == *(u32*)0x3bd34.
- End: H2 at **0x03020984** (or 0x03020980 if D is wanted intact) -> stamp end/duration on the open entry.
- Names: at draw time, `contact_name_lookup_or_format(0, id, buf17, type)` @0x03034d58 (type 3 for source, 4 for group dst), fall back to
  `flash_binary_search_le_record` @0x03033598 + `flash_talker_record_read` @0x03033718.
- Do not rely on the stock Call Log (private-to-me only) or on the `history_*` L3 queue.
- The screen is reachable from the stock Call Log menu as its 5th row since v43 (`docs/calllog-submenu.md`).
