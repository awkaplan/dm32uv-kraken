# Stock APRS gaps: analog APRS mode field, and the distance/bearing hemisphere bug

Status: static reverse engineering of stock DM32.00.01.048 (CK803S). Nothing was flashed, run on a radio, or run in QEMU.
Corpus: [dm32uv-re](https://github.com/awkaplan/dm32uv-re) (`meta/disasm.jsonl`, `meta/functions.json`, `src/`, image
`data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin`). Literal-pool values were read from the image (a Ghidra `PTR_` name is the pool slot, not the value);
call sites were found by brute-forcing every `bsr` target over a full objdump of 0x03001000-0x030bfc20 (a raw search for an
address also hits unrelated literal pools).

## 0. Verdicts

- A. Channel byte 0x1C bits 3-2 value 2 ("Analog") does nothing in stock. Nothing reads that bit-field at all, and the image contains no AFSK/AX.25 code or strings. Stock APRS is DMR-data only, matching manual 7.10.1. Confidence: medium-high (negative result from static scans).
- B. The hemisphere bug is CONFIRMED. N/S/E/W letters are used only for the on-screen text. Bearing and distance see unsigned magnitudes. Bearing is mirrored E/W for a user at 122W. Distance is right only when both points are in the same hemisphere. Confidence: high.
- Stored bearing (+0x46) and distance (+0x48) are computed once, when the RX-position popup is drawn, and copied into the log at append time. The log detail screen only displays them. A fix therefore affects new records only. Confidence: high.
- Minimal fix: ONE `bsr` hook at 0x03063a04 (4 bytes) plus a 158-byte stub that rewrites 4 stack doubles to `360.0 - x` for W/S. Negating is NOT enough (see 2.4).

## A. Analog APRS in stock?

### A.1 What the codeplug spec says
The codeplug structure notes: byte 0x1C = squelch level (bits 7-4), APRS Report Mode (bits 3-2: 0 Off, 1 Digital, 2 Analog), bits 1-0 unknown. Factory default written by `codeplug_factory_defaults_write` is `movi r3,0x30; st.b r3,(r4,0x1c)` at 0x03032520/0x030325b8 (squelch 3, APRS mode 0). The manual says APRS is "valid for digital mode only".

### A.2 Where the channel byte lives in RAM
- `channel_record_load_by_index` @0x0303153c builds a 0x33-byte channel struct: rx/tx frequency decoded to +0..+7, then `memcpy_bytes_iram(&s+8, rec+8, 0x18)`. So struct offset == record offset for 0x08..0x1F. Byte 0x1C of the current channel is at 0x0003e1dc+0x1c = 0x0003e1f8 (`p_vfo_display_state`, slot 0x0300db40).
- The VFO A/B copies are `PTR_DAT_03030560/03030564` (see `commit_channel_record_dual_vfo` @0x030303bc).

### A.3 Every access to that byte (evidence)
Scans over all 3334 functions in `meta/disasm.jsonl`:
- No `ld.b/ld.bs/ld.h rX,(rY,0x1c)` followed (within 4 instructions) by `zext 3,2`, `andi 0xc`, `ins ..,3,2`, `lsri 4`, `zext 7,4` or `andi 0xf0`. The single regex hit, `FUN_0306715c` @0x03067200, is an unrelated flash-block unpack.
- Reads of offset 0x1C with `ld.w` (the word 0x1C..0x1F) are all whole-struct copies `ld.w r,(r,0x1c); st.w r,(sp,0x1c)`: `vfo_field_write_2bit_at_8_direct_commit` @0x03030f5a, `channel_record_load_by_index` @0x03031658, `vfo_record_read` @0x030317aa, `screen_enter_apply_channel_and_rf_mode` @0x03038214, `channel_freq_record_build_from_flash` @0x03057138, `channel_change_apply_dispatcher` @0x0305799e/7c6e/7cc8/7d10/7efc/8004. Stores: only `commit_channel_record_dual_vfo` (`st.w (r12,0x1c)` @0x030303f4 and the two VFO copies @0x0303049c/0x03030530).
- Byte 0x1A (APRS Receive bit 2, and Talkaround) has no channel-struct access at all.
- The remaining `ld.b (r,0x1c)` hits belong to other structs: radio-state block 0x3e0c0 (`talker_alias_info_screen` @0x0302be88, `ptt_press_handler` @0x0305c854, `event_flags_dispatch_bit4_group` @0x0305e6e6, `channel_activity_log_flush_task` @0x03021400), and the modem workmode table 0x00033c58 (`modem_workmode_rfmode_reload` @0x00016ad0 = flash 0x030c66f0 copies its bytes 0x18..0x1d into the modem struct; that table is filled by constants in `hr_c7000_modem_core_reinit_sequence`, not from the channel).
- The set of on-radio channel field editors (`vfo_field_write_*`) covers offsets 0x08,0x09,0x0a,0x0c,0x0d,0x0e,0x0f,0x15,0x16,0x19,0x1b only. There is no editor for 0x1a or 0x1c, so the only way to set mode 2 is the CPS.
- Per-channel squelch (the high nibble of the same byte) is also never extracted; analog squelch comes from other settings ("Ana SQL Level" menu string @0x030bd170). So the whole byte is write-only/pass-through in stock.
- Gap: the scan covers the corpus's decoded functions; a read through a stack-local copy with an `sp`-relative offset could in principle hide. None of the APRS RX/TX functions below does this.

### A.4 Stock APRS data path is DMR only
- RX: position text arrives through `numeric_dialog_field_apply_or_gps_update` @0x0305ebc4 (modem data message, `record_build_value_plus_csv_field` @0x0305d4f0, record at 0x3e920), shown by `gps_position_display_screen` @0x030636e0, logged by `gps_log_record_append_compact` @0x03062d2c.
- TX: carried as a DMR data message; the only position text template in the image is the NMEA sample `$GPRMC,072431.000,A,2452.4974,N,11838.0406,E,...` (slot 0x030624a8 -> 0x030b766c).
- Strings (`extract_gb2312_strings.py`, 1674 strings): the only APRS-related strings are UI labels: `APRS Info %d/%d`, `APRS Receive`, `APRS Send`, `APRS On/Off`, `APRS Info`, `Upload Beacon`, `GPS Beacon`, `Fixed Beacon`, `Aprs Data`, `Report Channel`, `Upload ID`, `Upload Type`. No `WIDE`, `WIDE1-1`, `TCPIP`, `RELAY`, tocall (`AP...`), `AX.25`, `AFSK`, `UI`.
- Binary search of the whole image: no `WIDE`/`WIDE1`/`APR`-style callsign bytes even in the AX.25 left-shifted-by-1 encoding, no doubles/floats 1200.0, 1700.0, 2200.0 (the int 1200 at 0x03051cc0 is a menu constant), no CRC-16/X.25 or CCITT table.
- Related existing doc: `docs/afsk-tx.md` covers how a patch could generate AFSK on the analog channel (FD6818 tone generator). Stock has none of that.

Verdict: mode 2 is ignored (behaves exactly like 0). There is no analog APRS TX or RX, no AX.25, no AFSK in stock. Enabling it needs a patch (digital-mode APRS list/UI could be reused; the AFSK/AX.25 generator is all new code).

## B. Stock distance/bearing bug

### B.1 Call graph (exact)
Brute-force `bsr` scan (`full.dis`) finds exactly these callers; no pool-slot references to any of the entry points exist:

| Function | Addr | Callers |
|---|---|---|
| `gps_position_display_screen` (RX position popup) | 0x030636e0 | `dialog_request_tick` @0x0302d8b4, `dialog_panel_update_tick` @0x0302daf6, `band_range_select_screen` @0x0304858e |
| `gps_coordinate_normalize_pair` | 0x03062fe4 | only @0x03063a00 |
| `gps_coordinate_pair_distance_calc` (bearing + sector) | 0x0306319c | only @0x03063a20 |
| `gps_coordinate_distance_soft_float` (haversine km) | 0x030630b4 | only @0x03063a52 |
| `gps_waypoint_display_screen` (log detail) | 0x030626d4 | `call_gps_id_detail_screen_dispatch` @0x0303c658; its callee list contains neither calc nor distance |

So these three math routines feed ONLY the RX-position popup. They do not drive the GPS/GNSS info screen (`gps_lock_status_or_band_select_screen` @0x030390f0 only draws and calls `band_range_select_screen`), and not the log detail screen.

### B.2 Inputs, exactly (gps_position_display_screen)
Frame notation: `sp` is the function's sp (r14) after its prologue (`push r4-r11,r15; subi sp,0x64`).

Station (received) position. Text S = `*(0x3e923)` (record 0x3e920 + 3), `string_find_nth_comma_index` @0x03011420 (a0=ptr, a1=n -> u8 offset after n-th comma):
- lat text = S[idx2..idx3) -> `format_computed_ratio_as_string` @0x030114d4 (NMEA ddmm.mmmm -> decimal-degree "%f" string at sp+0x3c) -> `strtod` @0x0306fd28 -> double stored lo `sp+0x18`, hi `sp+0x1c` (st.w at 0x03063730/32). Then the letter `S[idx3]` is stored in byte `sp+0x24` and only `strcat`ed onto the display string (0x03063738). Never used again.
- lon text = S[idx4..idx5) -> same path -> double lo `sp+0x14`, hi `sp+0x20` (st.w at 0x030637ba/bc, note non-contiguous); letter `S[idx5]` overwrites byte `sp+0x24` (0x0306379c) and is `strcat`ed (0x030637c2).
- At 0x03063a04 `sp+0x24` therefore holds the LONGITUDE letter ('E'/'W'); the latitude letter must be re-read from `0x3e923 + idx(3)`.

Our position (only if byte 0x38c6c bit 6 is set, 0x03063978-0x03063984):
- Source is the RMC line copy at 0x38c79 (`lrw r4,0x3063adc` -> 0x38c79), text fields 3/4 and 5/6, `strtod` of the raw ddmm.mmmm / dddmm.mmmm digits -> lat lo/hi `sp+0x34/0x38` (r7), lon lo/hi `sp+0x2c/0x30` (r4). Then `normalize_pair(lat, lon, &lat, &lon)` @0x03063a00 converts both in place to decimal degrees (x/100 -> degrees; remainder*100/60).
- NOT used: the packed 2p23 words at 0x38d24/0x38d30 and the doubles at 0x38d28/0x38d34 (parser outputs, never read by the popup). Our hemisphere letters at 0x38d42 (lat) / 0x38d43 (lon) are written by the parser (`st.b r3,(r5,0xd6)` @0x03012872, `st.b r3,(r5,0xd7)` @0x03012888, base 0x38c6c) but the popup never reads them.

### B.3 Math, and the stored results
- Bearing/sector: `calc(ownLat, ownLon, stack: rxLat, rxLon, &u16)` @0x0306319c: converts each degree value to radians (x*pi/180), rejects any radian value `<= 0.0` or `>= 360.0` (constants 0x30b759c = 0.0, 0x30b75a4 = 360.0, via `softdouble_compare_le`/`compare`; failure -> sector code 0, `st.h 0` to bearing out), spherical law of cosines, `acos`-style base angle, then picks the quadrant by NUMERICALLY comparing rx vs own latitude and longitude radians (0x03063370, 0x03063380 etc.), adds 90/180/270/360 offsets. Writes bearing (degrees, u16) through the out pointer `lrw r5,0x4009c` = 0x40078+0x24, and the sector code (1..8, or 3 if 0) to 0x40078+0x22 (0x03063a26-0x03063a32).
- Distance: `distance(rxLat, rxLon, stack: ownLat, ownLon)` @0x030630b4: haversine, constant R = 6378.137 (slot 0x03063198 -> 0x30b757c), result double km stored to 0x40078+0x28/+0x2c (0x03063a5c-0x03063a5e). Printed as `%d.%03dkm`; bearing printed as `%d°` from `ld.h (0x40078+0x24)` (0x03063aac) and drawn as an arrow.
- No hemisphere letter ever reaches any of the three math calls. CONFIRMED.

Consequences for an operator at 40N 100W with a station at 39.8N 99.9W:
- Stock sees our lon = +100.0, theirs = +99.9, so the station is "west" of us. True: it is east. Bearing is mirrored E/W.
- Distance with both in W hemisphere is correct (|dlon| is the same). With one E and one W station, dlon is badly wrong (e.g. 100W vs 2E: stock 98 deg, true 102 deg, and worse across 180).
- Same for S latitudes.

### B.4 Where the stored values come from; display time vs receive time
- `gps_position_display_screen` writes 0x40078+0x24 (bearing u16), +0x22 (sector), +0x28 (distance double) each time it draws the popup with a valid own fix. With no own fix the bearing is set to 0xFFFF (0x03063ba2).
- Its tail (0x03063be4-0x03063bf8) calls `gps_log_record_append_compact(0x3f006, *(u16*)0x3f004)` when `(*(u8*)(0x3e318+0x11) & 4) == 0`. The append copies bearing (`0x40078+0x24`) and distance (`0x40078+0x28`, 8 bytes) into record +0x46 / +0x48 (see `docs/aprs-positions.md`, 1.3).
- `gps_waypoint_display_screen` (log detail) does not call calc/distance; it reads rec +0x46 / +0x48 and prints them. So the log values are fixed at receive (popup) time. A patch fixes only NEW records; existing records in data-chip block 1 keep the wrong bearing (and cross-hemisphere distance), and only recomputation by a patch screen (which already must parse S) can correct them.

### B.5 Why simple negation fails
`calc` rejects radian values `<= 0`, so feeding -122.0 makes it return sector 0 with no bearing. Distance (haversine) has no such check and would accept negatives. Use `360.0 - x` for W/S instead:
- sin/cos are 360-periodic, so the law of cosines and haversine are exact (for haversine `sin^2(dlon/2)` is also invariant under dlon +/- 360).
- The range check passes (radians in (0, 2pi)).
- The numeric quadrant comparisons stay monotone eastward for longitudes (238 > 237 means east), so bearing is right for any pair of stations that are both W (or both E), the owner's case. Pairs straddling the prime meridian or antimeridian can still get the wrong quadrant (stock limitation, not worsened).
- Latitude: `360-|lat|` gives correct sin/cos but the numeric N/S comparison is only correct for two stations in the SAME hemisphere (S pair or N pair). N-vs-S pairs (crossing the equator) still pick the wrong quadrant. Fully general handling would need a rewrite of the quadrant tests. Because the owner is N, the lat flips are optional.
- Edge: a coordinate exactly on 0 degrees (equator, Greenwich) still fails the `<= 0` check, as in stock.

### B.6 Minimal patch: one splice, one stub
Single hook, after `normalize_pair` has converted our fix and before the call to `calc`/`distance` consumes the four doubles.

- Hook site: `0x03063a04`, 4 bytes, two 16-bit instructions:
  - expected stock bytes: `46 98 40 b8` = `ld.w r2,(sp,0x18)` ; `st.w r2,(sp,0x0)` (the first two instructions of the `calc` argument setup, i.e. `rxLat.lo -> stack arg 0`).
  - replacement: one 32-bit `bsr STUB`. Encoding: `0xE0000000 | (((STUB - 0x03063a04) >> 1) & 0x03FFFFFF)`, stored as two little-endian halfwords (high halfword first). Example for STUB = 0x030E7000 (a "spare" sector in `docs/flash-allocation.md`; any free sector works, check `data/allocations.json`): `04 e0 fe 1a` (see exact bytes below).
  - resume address (= r15 after the bsr) = 0x03063a08 (`ld.w r3,(sp,0x1c)`), the instruction after the displaced pair.
- r15 liveness (patch-patterns section 11): SAFE. `gps_position_display_screen` opens with `push {r4-r11,r15}` @0x030636e0, and by 0x03063a04 has already executed many calls. The stub additionally `push`es r15 and pops it back to return.
- Registers at the site: r4 = &our lon double (sp+0x2c), r7 = &our lat double (sp+0x34), r10 = Y row (0x41/0x71) LIVE until 0x03063a9c, r6 = 0, r8/r9 scratch. r0-r3 are dead (reloaded at 0x03063a18). The stub preserves r4-r7 (push/pop) and clobbers only r0-r3 and the stack doubles. Calls it makes: `soft_double_sub` @0x0306ecc8 (a=r1:r0, b=r3:r2, returns a-b in r1:r0, registers-only ABI, preserves r4-r11) and `string_find_nth_comma_index` @0x03011420 (a0=ptr, a1=n, returns u8 in r0, preserves r4+).
- Stub entry sp = caller sp - 20 (push r4-r7,r15); caller sp offsets therefore shift by +20 inside the stub. The stub must re-execute the displaced pair itself (`ld.w r2,(sp,0x18+20); st.w r2,(sp,0+20)`).

Doubles to rewrite (caller-frame offsets at the hook; lo = low word, hi = high word):

| Value | lo / hi | Condition for `x = 360.0 - x` |
|---|---|---|
| our lat | sp+0x34 / sp+0x38 | `*(u8*)0x38d42 == 'S'` |
| our lon | sp+0x2c / sp+0x30 | `*(u8*)0x38d43 == 'W'` |
| station lon | sp+0x14 / sp+0x20 | `*(u8*)(sp+0x24) == 'W'` (letter left by the lon parse) |
| station lat | sp+0x18 / sp+0x1c | `*(u8*)(0x3e923 + string_find_nth_comma_index(0x3e923,3)) == 'S'` |

`360.0` = 0x4076800000000000 (hi `movih 0x4076; ori 0x8000`, lo 0). Equivalent fallback for our letters if 0x38d42/43 prove stale: read `0x38c79[idx(4)]` and `0x38c79[idx(6)]` as the stock code does.

Stub source (assembled with `csky-abiv2-elf-as -mcpu=ck803`, then `ld -e stub -Ttext=STUB --defsym SDSUB=0x0306ecc8 --defsym NTHCOMMA=0x03011420`; 158 bytes).

```
    .text
    .extern SDSUB
    .extern NTHCOMMA
    .global stub
    .set F, 20                      /* push r4-r7,r15 */
    .macro FLIP lo, hi
    movi    r0, 0
    movih   r1, 0x4076
    ori     r1, r1, 0x8000          /* r1:r0 = 360.0 */
    ld.w    r2, (sp, \lo + F)
    ld.w    r3, (sp, \hi + F)
    bsr     SDSUB                   /* r1:r0 = 360.0 - x */
    st.w    r0, (sp, \lo + F)
    st.w    r1, (sp, \hi + F)
    .endm
stub:
    push    r4-r7, r15
    movih   r4, 0x3
    ori     r4, r4, 0x8d42          /* &our lat letter; +1 = our lon letter */
    ld.b    r3, (r4, 0)
    cmpnei  r3, 0x53                /* 'S' */
    bt      1f
    FLIP    0x34, 0x38              /* our lat  */
1:  ld.b    r3, (r4, 1)
    cmpnei  r3, 0x57                /* 'W' */
    bt      2f
    FLIP    0x2c, 0x30              /* our lon  */
2:  ld.b    r3, (sp, 0x24 + F)      /* station lon letter */
    cmpnei  r3, 0x57
    bt      3f
    FLIP    0x14, 0x20              /* station lon (lo @+0x14, hi @+0x20) */
3:  movih   r4, 0x3
    ori     r4, r4, 0xe923          /* S text */
    mov     r0, r4
    movi    r1, 3
    bsr     NTHCOMMA
    zextb   r0, r0
    ldr.b   r3, (r4, r0 << 0)       /* station lat letter */
    cmpnei  r3, 0x53
    bt      4f
    FLIP    0x18, 0x1c              /* station lat */
4:  ld.w    r2, (sp, 0x18 + F)      /* displaced stock insns */
    st.w    r2, (sp, 0 + F)
    pop     r4-r7, r15
```

Assembled at STUB = 0x030E7000 (verification of the assembler/linker output only; recompute the `bsr` words for a different address, they are PC-relative):
```
d4 14 24 ea 03 00 84 ec 42 8d 60 84 43 eb 53 00 0c 08 00 30 21 ea 76 40 21 ec 00 80 52 98 73 98
fc e3 54 3e 12 b8 33 b8 61 84 43 eb 57 00 0c 08 00 30 21 ea 76 40 21 ec 00 80 50 98 71 98 fc e3
45 3e 10 b8 31 b8 6e d8 38 00 43 eb 57 00 0c 08 00 30 21 ea 76 40 21 ec 00 80 4a 98 6d 98 fc e3
35 3e 0a b8 2d b8 24 ea 03 00 84 ec 23 e9 13 6c 03 31 f9 e3 d7 51 00 74 04 d0 23 00 43 eb 53 00
0c 08 00 30 21 ea 76 40 21 ec 00 80 4b 98 6c 98 fc e3 1c 3e 0b b8 2c b8 4b 98 45 b8 94 14
```
Hook bytes for STUB = 0x030E7000: `bsr` word 0xE0041AFE -> memory bytes `04 e0 fe 1a` at 0x03063a04 (replacing `46 98 40 b8`). (The encoding formula above is the check: (0x030E7000-0x03063A04)/2 = 0x41AFE; the stock `bsr` at 0x03063a00 = `ff e3 f2 fa` is the same encoding for target 0x03062fe4.)

Two other notes for whoever implements:
- This stub is a design for a patch; it is not stock behaviour and does not belong in the stock corpus.
- The stub runs in the UI task on every popup redraw. It executes at most 4 soft-double subtractions and one comma-scan; cost is negligible next to the trig that follows.
- Verification plan (not done): boot patched image under QEMU to standby (`qemu_run.py`/`render_frames.py`, expect ~4433 non-black pixels), then force the popup with a synthetic record whose S has `...,W,` and `...,N,` and our fix 0x38c6c bit 6 set with letters `N`/`W` at 0x38d42/43; read back 0x40078+0x24/+0x28. Expected: with own position 40N 100W and a station slightly north-east of us, a bearing in the first quadrant (stock returns the E/W mirror, in the fourth quadrant), and a distance equal to the same-hemisphere stock value (distance only changes for cross-hemisphere pairs).

### B.7 What a bad hook would break (risk notes)
- Do not hook the entry of `calc`/`distance`: they are separate functions, but they take eight double halves (stack args 5-8 and the out pointer). Splicing at the caller after `normalize_pair` is smaller and touches the one place where all four values exist as plain stack words.
- Do not change the displayed text; the stock text path (`format_computed_ratio_as_string` -> `%f`) ignores sign, and the stub only edits copies on the stack (`sp+0x14..0x38`), which are used afterwards solely by `calc`/`distance` (loads at 0x03063a18-0x03063a52). Confirmed by reading every instruction from 0x03063a04 to 0x03063a60: the only consumers of those slots are the two math calls.
- The sector code (0x40078+0x22) and the arrow angle derive from the same bearing, so they get fixed with it.

## Summary of confidence
- A (no analog APRS): medium-high. Absence in code and strings, plus the absence of any bit-extraction on byte 0x1C anywhere in the corpus, plus the manual. Residual risk: a read through an sp-relative local copy that the scans cannot attribute.
- B (bug and call graph): high. B.1, B.2 and B.3 come directly from the disassembly and brute-force caller scan.
- B (receive-time storage): high.
- B (fix): medium-high. Design reasoned from the disassembly; math checked by hand; stub assembled but NOT executed under QEMU or on hardware.
