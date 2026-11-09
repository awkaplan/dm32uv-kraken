# Received APRS/GPS positions: storage, own position, formats

Static RE of stock DM32.00.01.048 (CK803S). Read-only analysis; nothing was flashed. Corpus: [dm32uv-re](https://github.com/awkaplan/dm32uv-re) (`meta/functions.json`, `meta/disasm.jsonl`, `data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin`).
Literal-pool rule applied everywhere: the `lrw rX,0xSLOT` operand printed by `lookup.py` is the SLOT address; the VALUE is the word stored there. Every RAM/flash address below is a slot VALUE that I read from the image (slot addresses noted in brackets where useful).

## 0. Headline findings

- The stock "APRS Info" list (`aprs_info_list_screen`, `aprs_waypoint_list_dialog_screen`, `aprs_waypoint_list_conditional_show`) is a viewer for a **log of received position reports kept in data-chip logical block 1**. Same log feeds `gps_waypoint_display_screen` (detail page). These are misnamed "gps_waypoint_*" in the corpus; they are the same thing.
- Each log record is 0x50 bytes: 24-bit DMR source ID, the received NMEA-RMC-style CSV text, plus **a bearing (u16 degrees) and distance (soft-double km) that stock already computed from our fix at receive time**.
- Positions are stored as ASCII NMEA text (`ddmm.mmmm` / `dddmm.mmmm` + hemisphere letter), NOT in the "2p23" packed format. The 2p23 format is only used for OUR position (`gps_nmea_rmc_gga_sentence_parser`).
- Stock bearing/distance code **ignores the N/S/E/W letters** (all coordinates treated as positive N/E). Distance is fine when both points share hemispheres; bearing is mirrored E/W (and N/S) for W/S hemisphere users. Do not trust the stored bearing; recompute in the patch.

## 1. Where received positions are stored

### 1.1 Medium and access
- Medium: external SPI NOR "data chip", logical block id **1** (a 4 KB sector found through the tag table: `flash_block_id_resolve(1)` @0x0300f3a8-ish, locked wrapper `flash_block_id_resolve_locked` @0x0300f594, reads via `flash_block_read(block_id, offset, dest, len)` @0x0300f71c, which splits reads at 256-byte page boundaries).
- Stock read functions a patch can call directly (all in sector 0x03062000; all `bsr`-callable, args in a0..a3, mutex taken internally):

| Function | Addr | Signature | Evidence |
|---|---|---|---|
| `gps_waypoint_count_read` | 0x0306265c | `u16 count = f()` | `0x03062668 movi r0,1; 0x0306266a bsr 0x0300f594 (block_id_resolve_locked); movi r1,0; r3=2; bsr 0x0300f71c (flash_block_read(blk,0,&u16,2))` |
| `gps_waypoint_record_read` | 0x030626b0 | `f(u16 idx, void *dst80)` copies 0x50 bytes | `0x030626bc movi r3,0x50; mult r5,r3; addi r1,r5,0x10; and r1,0xfff0 (r2=dst); bsr 0x0300f71c`. Decompile: `flash_block_read(block_id,(idx&0xffff)*0x50+0x10&0xfff0,dst,0x50)` |
| `gps_waypoint_field_read_3byte` | 0x03062680 | `u32 f(u16 idx)` returns 24-bit source ID | same offset formula, len 3 (`0x030626a2 movi r3,3`) |
| `contact_name_lookup_or_format` | 0x03034d58 | `bool f(1, u32 id, char *dst, 3)`; dst >= 17 bytes | returns true and writes 16-char contact name + NUL if the ID is in the contacts DB, else writes `"%d"` of the ID and returns false |
| `flash_block1_write_zero_word_tagged` | 0x03062e90 | clears the log (erase sector, write count=0) | decompile: erase, `program(blk,0,&0,2)` |

Call from patch code like any stock function (CK803S ABI a0..a3). The record buffer is 0x50 bytes; the whole read is a few SPI transfers, so read one record at a time (do not read the whole 4 KB onto the task stack).

### 1.2 Block layout (confidence: high)
- Block offset 0x00: `u16 count` (little-endian). Offsets 0x02..0x0f: not interpreted (preserved by the append, which reads 0x10 bytes and rewrites them).
- Records start at block offset 0x10, stride 0x50: `record(i)` at `0x10 + 0x50*i`. Index 0 = oldest, count-1 = newest (append writes at `count*0x50+0x10`).
- Capacity: **50 records** (0x32). Max offset 0x10+0x50*50 = 0xFB0 fits in 4 KB.
  Evidence in `gps_log_record_append_compact` @0x03062d2c (decompile): `if (count < 0x33) { if (count == 0x32) drop = 0x14; else drop = 0; } else { erase; count = 0 }`; when full (50) the oldest **20** records are dropped by copying records 20..49 into a freshly allocated block (`spi_flash_read_seq(src,i*0x50+0x10,&buf,0x50)` -> `spi_flash_program_pagesplit(dst,j*0x50+0x10,...)`), so it then holds 30 + the new one. The new header is `count = old-drop+1`.
- UI list indexing: the list screen shows `APRS Info %d/%d` (format string @0x030b771c `"APRS Info %d/%d"`), rows come from `gps_waypoint_field_read_3byte(idx)`, 8 rows per page. Selected index is a u16 at RAM 0x3e8f2, window row u16 at 0x3e8fa, detail-screen mode byte at 0x3e8f9 (1 = waypoint detail -> `gps_waypoint_display_screen(*(u16*)0x3e8f2)`).

### 1.3 Record layout (0x50 bytes) (confidence: high for fields below, medium for the tail pad)

| Off | Size | Field | Evidence |
|---|---|---|---|
| 0x00 | 3 | Source DMR ID, 24-bit little-endian (byte0 = low) | writer `record_build_value_plus_csv_field` @0x0305d4f0: `st.b r0,(r3,0); lsri r2,r0,8; st.b r2,(r3,1); lsri r0,r0,16; st.b r0,(r3,2)`. Reader: `memcpy_bytes_iram(&id, rec, 3); contact_name_lookup_or_format(1,id,...)` in `gps_waypoint_display_screen` (end of function) |
| 0x03 | up to 0x43 | ASCII CSV string S, NUL terminated (max 0x43 = 67 chars is enforced when building, `numeric_dialog_field_apply_or_gps_update` @0x0305ebc4: `movi r2,0x43; cmphs..; st.h r2,len`) | see 1.4 |
| 0x46 | 2 | `u16 bearing` degrees; **0xFFFF = unknown/no own fix**. Reader treats `>= 0x169` (361) as "--" | `gps_waypoint_display_screen`: `if (uStack_2e < 0x169)` with uStack_2e = rec+0x46 (stack offset 0x74-0x2e); writer `gps_position_display_screen` sets `st.h r3,(0x40078,0x24)` with r3=-1 at 0x03063ba2-0x03063ba6 when no fix, else value from `gps_coordinate_pair_distance_calc` |
| 0x48 | 8 | soft-double (IEEE-754 binary64, little-endian, lo word first) **distance in km** from our position at receive time | writer: `memcpy_bytes_iram(auStack_30, 0x40078+0x28, 8)` in append (`PTR_DAT_03062e8c`, slot value 0x00040078); reader prints it as `"%d.%03dkm"` (@0x030b76f0 string) after `soft_double_to_int` and `(x-int)*1000` |
| 0x50 | - | end | |

(The 0x40078 base: slot 0x03062e8c holds 0x00040078; the decompiler's `PTR_DAT_03062e8c[0x12]` is an `undefined2*` index, so it is byte +0x24 = bearing and `+0x14` index = byte +0x28 = distance. This matches `gps_position_display_screen` writing `*(0x4009c)` via the param_9 out-pointer (0x4009c = 0x40078+0x24) and `PTR_DAT_03063ae4[10]` (= +0x28) for the distance double.)

### 1.4 The string S: what a position report looks like (confidence: high for field order)
`record_build_value_plus_csv_field(srcID, rawMsg, len, out)` @0x0305d4f0: if `rawMsg[0]=='\'' && rawMsg[1]=='~'` (0x27, 0x7e at `0x0305d506-0x0305d510`), then it takes `rawMsg+4`, finds the position after the **1st** comma (r6) and after the **10th** comma (r7) with `string_find_nth_comma_index` @0x03011420 and copies `rawMsg[4+r6 .. 4+r7)` to `out+3`. So S = fields 1..9 of an RMC-like sentence, each followed by a comma. That is exactly NMEA GPRMC (the firmware's own TX sample is `"$GPRMC,072431.000,A,2452.4974,N,11838.0406,E,0.00,0.00,220219,,,A*77\r\n"`, string pointed by slot 0x030624a8):

```
S = "hhmmss.sss,A,ddmm.mmmm,N,dddmm.mmmm,E,speed,course,ddmmyy,"
      F1        F2 F3        F4 F5         F6 F7    F8     F9
```
Reader field map (`gps_waypoint_display_screen` @0x030626d4 and `gps_position_display_screen` @0x030636e0; `idx(n)` = `string_find_nth_comma_index(S,n)` = offset just after the n-th comma):
- time = `S[0 .. idx(1)-1)`; passed as the 3rd arg of `gps_datetime_format(2, date, time, out)`.
- F2 status `A`/`V`: not checked by the display code (validity must be checked by the patch: `S[idx(1)] == 'A'`).
- lat text = `S[idx(2) .. idx(3))` (includes trailing comma, `strtod` ignores it), lat hemisphere letter = `S[idx(3)]` (`N`/`S`).
- lon text = `S[idx(4) .. idx(5))`, lon hemisphere = `S[idx(5)]` (`E`/`W`).
- speed (km/h, text) = `S[idx(6) .. idx(7)-1)`, shown as `"%sKM/H"` (@0x030b76fc).
- course F8 = skipped by the display (the course shown is the computed bearing from our fix).
- date = `S[idx(8) .. idx(9))`, 7 bytes copied (memset 7 before).
- UTC, as in NMEA. Lat/lon digits: `ddmm.mmmm` (lat) and `dddmm.mmmm` (lon), i.e. NMEA decimal-minutes; no other units.

Caveat (medium): the `'~` + 2 bytes header before `$GPRMC` is inferred from `rawMsg+4`; I did not see a captured message. If the check `'~` fails the function returns without filling S, giving an ID-only record (S empty). A reader must treat `S[0]==0` or fewer than 9 commas as invalid.

### 1.5 When records get appended (open question: see section 5)
`gps_position_display_screen` (@0x030636e0) is the live "position received" popup (it draws the lat/lon text, speed, distance, course arrow, and the source name). Its last action (confirmed from raw disasm; the corpus function is cut off at 0x03063bbe): `if ((byte)[0x3e329] & 4) == 0: gps_log_record_append_compact(0x3f006, *(u16*)0x3f004)` (`0x03063be4..0x03063bf8`: `lrw a3,[0x3063ca4]=0x3e318; ld.b a3,(a3,0x11); andi a3,4; bnez skip; lrw a0,[0x3063ca8]=0x3f006; subi a3,a0,2; ld.h a1,(a3,0); bsr 0x3062d2c`).

## 2. Our own position

### 2.1 GPS state struct, base 0x38c6c (confidence: high)
Slots 0x03012ab8, 0x03012928, 0x03012cfc, 0x030629a4, 0x03063ad8 all hold 0x00038c6c.

| Address | Size | Meaning | Evidence |
|---|---|---|---|
| 0x38c6c bit 6 (0x40) | | RMC valid (fix), set by the parser when a checksum-good `A` RMC line was copied; cleared otherwise | parser: `*(byte*)0x38c6c = ... & 0xbf | 0x40` on success, `& 0xbf` on bad checksum / status != 'A' / overflow |
| 0x38c6c bit 7 | | last-reported status shadow (used to redraw the status bar when bit7 != bit6) | `0x030129ca` block |
| 0x38c79 (= base+0xd) | up to 0x96 | copy of the last good RMC line, **without leading `$`**, starting `GPRMC,...` up to `\n` (so `nth_comma` indices are F0=`GPRMC`, F1=time, F2=A, F3=lat, F4=N/S, F5=lon, F6=E/W, F7=speed, F8=course, F9=date) | loop copying ring-buffer chars into slot 0x03012ac4 (= 0x38c79); `gps_position_display_screen` parses own lat/lon from it with nth-comma indices 3,4 / 5,6 |
| 0x38d24 (base+0xb8) | u32 | **packed latitude** (2p23, see 2.2) | `st.w r0,(r5,0xb8)` with r5 = 0x38d28-0xbc at `0x03012868-0x0301286a` |
| 0x38d28 (base+0xbc) | double | latitude in **unsigned decimal degrees** (soft-double, LE, lo word first) | `double_to_fixed_point_pack_2p23(str, 0x38d28)` stores the double through its out pointer (`st.w r0,(r5,0); st.w r1,(r5,4)` @0x030114a0-0x030114a2) |
| 0x38d30 (base+0xc4) | u32 | **packed longitude** (2p23) | `st.w r0,(r5,0xc4)` @0x03012880 |
| 0x38d34 (base+0xc8) | double | longitude, unsigned decimal degrees | out pointer `addi r1,r5,0xc8` @0x03012878 |
| 0x38d42 (base+0xd6) | char | latitude hemisphere `'N'`/`'S'` | `st.b r3,(r5,0xd6)` from the extracted field |
| 0x38d43 (base+0xd7) | char | longitude hemisphere `'E'`/`'W'` | `st.b ...,(r5,0xd7)` (the stack byte of the `sp+0x50` field) |
| 0x38d44 (base+0xd8) bit 6 | | display-format selector for received positions (clear: decimal degrees via `format_computed_ratio_as_string`; set: `D deg M' S.s"` via `gps_coordinate_seconds_to_hms_string_format`) | `gps_waypoint_display_screen`: `PTR_DAT_030629a4[0xd8] & 0x40` |
| 0x38ca0 (base+0x34) | s16 | altitude (GGA field 10) | GGA branch of parser |
| base+0x69 / +0x6a | s16 | speed / course scaled ints from RMC fields 8/9 (`strtod*const`) | not needed |

Hemisphere letters and the 2p23 values are **unsigned**: sign must be applied by the consumer from the hemisphere bytes. A fix is valid only if bit 6 of byte 0x38c6c is set; the packed words are zero/stale otherwise (struct is zeroed `memset(0x96)` only for the line buffer, so check bit 6, not the values).

### 2.2 `double_to_fixed_point_pack_2p23` @0x03011450 decoded exactly (confidence: high)
Disassembly (literal slots 0x3011608/0c/10/14 -> doubles 0.01, 100.0, 60.0, 100000.0 at 0x3081ddc/de4/dec/df4):
```
x   = strtod(str)                       ; "ddmm.mmmm" or "dddmm.mmmm" (NMEA)
i   = to_int32(x * 0.01)                ; whole degrees D (truncate)     0x03011464-0x03011468
D   = (double)i
min = x - D*100.0                       ; minutes mm.mmmm                0x0301147a-0x03011486
deg = D + min/60.0                      ; decimal degrees                0x03011490-0x03011498
*out = deg                              ; double stored at out           0x030114a0
n   = (int)deg                          ; integer degrees (soft_double_to_int, trunc)
f   = (deg - (double)n) * 100000.0      ; 0x030114b6-0x030114c0 (0x306f494 converts the int back to double)
fi  = round_half_away(f)                ; soft_double_conditional_add_or_sub: +/-0.5 then trunc
return (n << 23) + fi                   ; lsli r5,r5,0x17 @0x030114c8 ; addu r0,r5,r0 @0x030114ce
```
So the name "2p23" is misleading: it is **NOT binary fixed point**. Layout of the u32:
- bits 31..23 (9 bits): integer degrees (0..180).
- bits 22..0 (23 bits): fractional degrees in **1e-5 degree decimal units** (0..100000, so only the low 17 bits are ever used).
- Decode: `deg = (v >> 23) + (v & 0x7FFFFF) / 100000.0`; in micro-degrees: `udeg = (v>>23)*1000000 + (v & 0x7FFFFF)*10`. Resolution 1e-5 deg (~1.1 m).
- Example: `2452.4974` -> deg 24.874957 -> `24<<23 + 87496` = 0xc0155c8 ... (24<<23 = 201326592, +87496 = 201414088).
- It is unsigned/absolute; the hemisphere is a separate byte.
- Input is NMEA ddmm.mmmm, so it is only valid for strings in that form. The `format_computed_ratio_as_string(flag, str, out)` @0x030114d4 reuses it and prints the double with `"%f"` (@0x3081e60; `flag` is ignored), which is how stock shows received positions as decimal degrees.
- The received-position records do NOT use this packed format (they keep NMEA ASCII, section 1.4).

## 3. How a received position is decoded and displayed (units cross-check)

`gps_position_display_screen` @0x030636e0 (RX popup) and `gps_waypoint_display_screen` @0x030626d4 (log detail) do:
1. Locate lat/lon substrings of S with `string_find_nth_comma_index` (section 1.4).
2. Convert to degrees: `format_computed_ratio_as_string` -> pack_2p23 -> `"%f"` (decimal degrees text, 6 decimals); then `strtod` of that text to get the decimal-degree double (live screen, `uVar8`/`uVar9`). `strcat` the hemisphere letter for display. If 0x38d44 bit6 is set the detail screen instead uses `gps_coordinate_seconds_to_hms_string_format` (`%d°%d'%d.%d"`, string @0x3081e64) on the raw digits.
   -> Confirms units: NMEA `ddmm.mmmm` / `dddmm.mmmm`, minutes decimal, hemisphere as letter.
3. Own position for the comparison: only if byte 0x38c6c bit 6 is set, parse `0x38c79` fields 3/4 and 5/6 (`ddmm.mmmm` and `dddmm.mmmm`) with `strtod`, then `gps_coordinate_normalize_pair` @0x03062fe4 (`x/100` -> degrees, remainder*100/60, constants 100.0 @0x30b7554) to decimal degrees.
4. Bearing + sector code: `gps_coordinate_pair_distance_calc(ownLat, ownLon, rxLat, rxLon, &u16)` @0x0306319c (spherical law of cosines, deg->rad via pi/180 @0x30b7584; returns sector code 1..8 or 0 = invalid, stored at 0x4009a = 0x40078+0x22; bearing written to 0x4009c = 0x40078+0x24). Called as `(own..., rx...)`, so bearing is from us to the station (semantic confidence: medium; I did not run it).
5. Distance km: `gps_coordinate_distance_soft_float(rxLat,rxLon,ownLat,ownLon)` @0x030630b4: haversine form, `Earth R = 6378.137` (constant slot 0x3063198 -> 0x30b757c) multiplied last, result km, stored at 0x40078+0x28 and printed `"%d.%03dkm"`. Course is printed `"%d°"` (@0x30b7714) and drawn as an arrow (angle-90 rotation).

**Hemisphere bug (confidence: medium-high, from absence)**: `gps_position_display_screen` reads the hemisphere char only into `local_64[0]` for `strcat` (display). It is never passed to `gps_coordinate_*`. The distance function takes all four values as positive degrees and `gps_coordinate_pair_distance_calc` additionally rejects values `<= 0` / `>= 360` (`softdouble_compare_le(x, 0.0)` / `compare(x, 360.0)` with constants 0x30b759c/0x30b75a4), then picks quadrant by comparing longitudes numerically. Net effect: S/W stations are treated as N/E. Same-hemisphere distances are right; bearing is reflected for W longitude (US users) and S latitude. So stored bearings should be recomputed.

## 4. Recipe for the patch screen (distance + bearing to received stations)

Own fix (cheap, integer-only): if `(*(u8*)0x38c6c & 0x40)`: lat = packed `*(u32*)0x38d24` (deg = v>>23, frac_1e5 = v & 0x7FFFFF), sign from `*(u8*)0x38d42` ('S' -> negative), lon = `*(u32*)0x38d30`, sign from `*(u8*)0x38d43` ('W' -> negative). Micro-degrees: `(v>>23)*1000000 + (v&0x7FFFFF)*10`. The two doubles at 0x38d28/0x38d34 are stock soft-double decimal degrees and need `soft_double_*` calls; avoid.

Stations: `n = gps_waypoint_count_read()` (u16 @ call), loop `i = n-1 .. 0` (newest first), `gps_waypoint_record_read(i, buf)` (buf 0x50 aligned bytes), then in the patch:
- id = `buf[0] | buf[1]<<8 | buf[2]<<16`; name via `contact_name_lookup_or_format(1, id, name17, 3)`.
- S = `(char*)buf+3` (NUL-terminated; guard `strlen<=0x43`). Find commas (`string_find_nth_comma_index(S,n)` @0x03011420 can be called, args a0=ptr, a1=n, returns u8 offset after the n-th comma, scans at most 100 chars and returns garbage if comma missing, so check yourself). Require >= 9 commas and `S[idx(1)]=='A'`.
- lat: integer parse of `ddmm.mmmm` -> `micro = dd*1e6 + (mm.mmmm)*1e6/60` using integers (minutes in 1e-4 units: `mmmm`), apply hemisphere letter `S[idx(3)]`. lon similar with `dddmm.mmmm`, letter `S[idx(5)]`.
- Compute distance/bearing with integer math or a small fixed-point haversine/atan2 (CORDIC); this avoids the stock hemisphere bug and the soft-float ABI. Cheaper alternative with caveats: show `rec[0x46]` (u16 bearing, valid if `< 361`, but hemisphere-mirrored, and relative to the fix at receive time) and the km double at `rec+0x48` (needs `soft_double_to_int` @0x0306e374 `(r0,r1)=lo,hi` to print; units km, correct for same-hemisphere).
- Records age: they carry an UTC time/date in S (F1 time, F9 date); use as a timestamp (compact: hhmmss + ddmmyy).

## 5. Confidence and open questions

Confidence:
- Block 1 log, 0x50 stride, +0x10 base, u16 count at 0, capacity 50, drop-20 on overflow: high (decompile + disasm of append and 3 readers).
- Record fields +0x00 ID, +0x03 CSV string, +0x46 bearing, +0x48 distance double: high.
- S is RMC fields F1..F9 (NMEA ddmm.mmmm, letters separate): high (field walk matches NMEA exactly and matches the firmware's own TX sample).
- 2p23 format decode: high (instruction-level), with the caveat that I did not execute it; the example above is hand-computed.
- Own-struct addresses (0x38c6c bit6, 0x38c79, 0x38d24/28/30/34/42/43): high.
- Bearing direction and the hemisphere-ignored claim: medium-high (static reading of the soft-float call graph, not executed).

Open questions (not resolved statically):
1. **Append source mismatch.** The log append copies from RAM 0x3f006 (len u16 @0x3f004), but the display code reads the same record from 0x3e920 (3-byte ID at 0x3e920, S at 0x3e923; `numeric_dialog_field_apply_or_gps_update` @0x0305ebc4 builds it there and rewrites the u16 @0x3f004 with the record length). 0x3f006 is also the TX payload buffer used by `channel_select_type_dispatch` (the `gb2312_string_reencode(..., 0x3f006)` path), and 0x3e920 is also the 0x40-byte text buffer filled by `text_field_copy_and_status_write` @0x0304852c. I did not find a copy 0x3e920 -> 0x3f006. Either another path I did not trace copies it, or the log stores something else in some flows. Verify against a real data-chip block 1 dump (or QEMU with an injected RX event) before relying on records beyond what the readers imply. The readers, however, only assume the layout in 1.3, so the layout is what stock itself parses.
2. **Append condition.** The append runs only when `*(u8*)0x3e329 & 4 == 0`; the tick callers (`dialog_request_tick` @0x0302d884, `dialog_panel_update_tick` @0x0302da7c) call the screen while that flag is SET (when kind byte 0x3f32c == 2 and own fix valid), then clear it. So logging may happen on a later redraw (e.g. `band_range_select_screen` @0x0304857c with subtype 0x1b), not on first display. Consequence: a position may be shown but not logged if the screen is never redrawn. If every received position is wanted, hook `record_build_value_plus_csv_field` @0x0305d4f0 (its output `out` buffer 0x3e920 is the ID+S record, return value is length) instead of reading the log.
3. Whether non-`'~` messages ever produce log entries (empty S) was not traced.
4. Where bits of block header 0x02..0x0f come from (copied through; unknown purpose).
5. Whether the 9-bit integer-degree field could hold a pre-offset (no evidence; values 0..180 straight).

## 6. Key addresses (quick table)
- Log API: count 0x0306265c, record_read 0x030626b0, id_read 0x03062680, clear 0x03062e90, append 0x03062d2c.
- Screens: info list 0x03062c14, list dialog 0x0303c58a, conditional show 0x03062cdc, detail 0x030626d4, live popup 0x030636e0, detail dispatch 0x0303c63e.
- Format helpers: nth comma 0x03011420, pack_2p23 0x03011450, ratio->"%f" 0x030114d4, DMS 0x030114f4, datetime fmt 0x03011b40, normalize 0x03062fe4, bearing 0x0306319c, distance 0x030630b4.
- RAM: GPS struct 0x38c6c, RMC copy 0x38c79, packed lat 0x38d24, lat dbl 0x38d28, packed lon 0x38d30, lon dbl 0x38d34, hemis 0x38d42/0x38d43, UI scratch struct 0x40078 (+0x22 sector, +0x24 bearing, +0x28 distance), list state 0x3e8f2/0x3e8f9/0x3e8fa, RX record buffer 0x3e920, RX raw msg 0x3fac8, msg struct 0x3f004 (len, data +2, srcID +0x324, kind +0x328), RX hw struct 0x3baa4 (+0x14 len, +0xc srcID).
