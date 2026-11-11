# Appending rows to the stock APRS sub-list (DM32.00.01.048)

**Status:** static analysis plus a QEMU round trip on a scratch image (stock image + the splices below + a test handler). Not run on a radio. Nothing in this document touches a radio or the repo's patches.

**Answer in one line:** APRS is top-level category 9. Its eight rows come from a constant descriptor at `0x030ba924` and an item-action jump table at `0x030aff54`. Appending N rows takes 2 relocated string arrays, 2 descriptor ptr fields, 2 descriptor count bytes, 1 bound-check byte, 1 literal-pool word and 1 relocated jump table. The OK-path and render-path category tables are untouched.

## 1. Category identity and where the rows come from

- APRS = category id **9** (0-based, last stock category; KRAKEN is 10). Row order in the top menu follows the order the builder appends ids.
- Render-path case block for id 9: **`0x0303dd40`** (entry 9 of the table at `0x030aff74`, inside `menu_screen_render_entry_variant` @`0x0303d8c8`). It runs when OK is pressed on the top-level APRS row and when returning from a deeper level. It does:
  - `lang = settings[0x0003e9b4 + 0x30] bit 6` (`zext 6,6`)
  - `desc = 0x030ba924 + lang*8`
  - `[0x0003e8f8] = desc.count` (u8 at +4)
  - `[0x0003e904] = desc.ptr` (u32 at +0)
  - `[0x18000a90] = desc.subtype` (u8 at +5, value 2)
  - `build_sequential_index_list(count, 0x0003e5f8, 0x0003e5fa)` (`0x0304a118`)
  - then falls into the shared tail `0x0303dd78` (`bsr main_menu_screen_dispatch`)
- **Descriptor `0x030ba924` is the real descriptor, not a pool slot** (gotcha 1 checked: the bytes there decode as `{ptr, count, subtype}`):

  ```
  0x030ba924: e4 a8 0b 03 | 08 02 00 00 | 04 a9 0b 03 | 08 02 00 00
              lang0 ptr=0x030ba8e4  count=8 subtype=2   lang1 ptr=0x030ba904  count=8 subtype=2
  ```

  - Lang 0 array `0x030ba8e4` (zh, 8 pointers, 32 bytes): APRS开关, 上报ID, 上报类型, 上报频道, 上报间隔, APRS信息, 上报信标, 全部删除.
  - Lang 1 array `0x030ba904` (en, 8 pointers): APRS On/Off, Upload ID, Upload Type, Report Channel, Intervals Set, APRS Info, Upload Beacon, Delete All. It ends exactly at the descriptor, so neither array can grow in place.
  - Both arrays are referenced **only** through this descriptor (image-wide search for `0x030ba8e4` / `0x030ba904` hits only the descriptor).
- **Count is constant (8) in both languages.** No row is hidden by settings, GPS state or mode. The index list is rebuilt sequentially (items 0..7) every time the case block runs.
- What is conditional is the **whole category row**, not its rows. `menu_option_gen_idx23` (`0x03049cdc`, appends at `0x03049dc0`) shows id 9 only if `[0x0003fdc0] & 4` and `settings_copy[3] & 0x20` (probably the CPS "APRS" checkbox; the stack copy at `r14+4` was not identified).
  - `menu_option_gen_idx24` (the short VFO/analog list) never appends id 9.
  - The caller `0x0303f39a` picks the generator: helper `0x0002deb8` returning 0 or 2 selects idx24 (mode 24), anything else idx23 (mode 23).
  - QEMU, default datachip: analog VFO A shows only Scan / Settings / GNSS; digital VFO B (press Back = A/B, then Menu) shows the full list ending in APRS.
  - See open question 1.
- Other readers of the APRS string array use **fixed indices**: `0x0303c618` reads `[3]` (Report Channel) as a title, `0x0304902a` reads `[4]` (Intervals Set). Appending leaves both valid.
- Other readers of descriptor `0x030ba924`: literal slots at `0x0303c7a0`, `0x0303ddac`, `0x030490d4`. All load the descriptor itself, so editing the descriptor in place covers every reader.

## 2. Row to item to handler (the item-action path)

- The category-level item table at `0x030aff18` holds, for id 9, the case block **`0x0303d54c`** (inside `main_menu_render_and_field_edit_dispatch` @`0x0303cdf0`, prologue `push r4-r6,r15`, frame popped by the shared tail `0x0303d872`).
- Block `0x0303d54c`:

  ```
  0303d54c  lrw   r3, 0x3e4e8            ; [pool 0x0303d564]
  0303d54e  ld.b  r2, (r3, 2)            ; selected sub-list row (0x0003e4ea)
  0303d550  lrw   r3, 0x3e662            ; [pool 0x0303d568]
  0303d552  addu  r3, r2, r3
  0303d554  ld.b  r2, (r3, 2)            ; item = byte at 0x0003e664 + row
  0303d556  cmphsi r2, 8                 ; bytes 07 3a   <- bound check (r2 >= 8 -> out)
  0303d558  bt    0x0303d872             ; bytes 8d 09   out of range: silently go to the tail
  0303d55a  lrw   r3, 0x30aff54          ; [pool 0x0303d56c]  <- per-item jump table
  0303d55c  ldr.w r3, (r3, r2 << 2)
  0303d560  jmp   r3
  ```

  The literal-pool words at `0x0303d564` / `0x0303d568` / `0x0303d56c` are `0x0003e4e8` / `0x0003e662` / `0x030aff54`. The value `0x030aff54` occurs once in the whole image, and the only `lrw` that loads it is at `0x0303d55a`.
- **Per-item table: real address `0x030aff54`, 8 entries, 32 bytes.** Stock values: `0x0303d570, 0x0303d5de, 0x0303d634, 0x0303d6a4, 0x0303d72c, 0x0303d756, 0x0303d7d8, 0x0303d848`. It is packed between cat 0's table (`0x030aff40`, 5 entries) and the render-path table (`0x030aff74`), so it cannot grow in place.
  - Entries are case blocks of the same function. Each ends in `br 0x0303d872`, either directly or by falling into it.
  - Verified in QEMU: item 0 opens the ON/OFF picker, item 1 the "Input ID" editor, item 6 Upload Beacon, item 7 "Confirm delete?".
- The index list read at `0x0003e664 + row` is a level-2 copy of the live sub-list list that the render block built at `0x0003e5fa`.
  - Levels sit at `0x0003e63f` (top), `0x0003e662` (sub-list) and `0x0003e685` (third level), stride 0x23, so at most **33 items**.
  - The code that does the copy on OK was not located statically. It works for 11 rows empirically.
- Dispatch order, from an in-asm translation-block diff on the stock image (`-d in_asm`, stock firmware, digital VFO):
  - **OK on top-level APRS** newly executes `0x0303d8c8`, `0x0303d9f8`, `0x0303dd40`, `0x0304a118`. This is the render-path block.
  - **OK on row 0** newly executes `0x0303cdf0`, `0x0303cec6`, `0x0303cede`, `0x0303d54c`, `0x0303d570`, `0x0303d872`. This is the item-action path.
  - The OK-path table (`0x030afef0`; entry 9 = `0x0303cde2` -> `bsr 0x030436c0`) and `main_menu_screen_render_entry` (`0x0303cbc4`) were **never** executed in either step. New APRS rows therefore do not need an OK-path entry.

## 3. Splice recipe for N new rows (N = 3 shown; row numbers are 1-based labels, items are 0-based)

The splices for the new rows are generated, not written out here. The `aprs` host in `patches/kraken_ui/menu_hosts.json` holds the rows. `tools/gen_menu_hosts.py` writes the descriptor pointers and counts, the two relocated label arrays, and the relocated item jump table with its `cmphsi` bound into `kraken_ui.patch.json`. See `docs/menu-hosts.md`. Take the addresses and values from the manifest, not from this section.

What still holds for these splices:

- All of the splices are same-size edits; none is a `bsr` hook.
- The lang-0 and lang-1 descriptor counts go from 8 to 8+N. The subtype byte (+5, value 2) is not changed.
- The bound is value-1 encoded. For N = 3 it goes from `cmphsi r2,8` to `cmphsi r2,11` (low byte 0x07+N), valid up to 32.
- Stock entries are copied as pointers, so the stock strings stay where they are. A label array can be small. New strings are NUL-terminated ASCII or GB2312, for example "Analog Beacon" or "Callsign".
- The sector `0x030e7000` is the `kraken_ui_data` allocation. When this was written the tables there ended at about `0x030e7160`, so space from `0x030e7160` onward was free.
- None of the APRS splice sites overlaps the sites the kraken_ui manifest already edits (`0x0303da02`, `0x0303da48`, `0x0303cd8e`, `0x0303cda8`, `0x0303ced8`, `0x0303cf18`, `0x030b7db8..0x030b7dc4`, `0x03049dcc`, `0x03049e0a`).

**Stub** (one shared stub is enough; all three new table entries can point to it, and the handler recovers the item from the row):

```
aprs_item_stub:                 ; a CASE BLOCK, not a function
    bsr     aprs_item_ok        ; C handler, reads row = *(u8*)0x0003e4ea; item = row (sequential list); idx = item-8
    lrw     r3, 0x0303d872      ; shared tail: bsr main_menu_screen_dispatch; addi sp,4; pop r4-r6,r15
    jmp     r3                  ; (same shape as krk_item_stub in krk_stubs.S)
```

**Register and `r15` notes** (patch-patterns §11, section 4):
- Nothing here hooks inside a stock function, so §11 does not apply to the splices.
- The stub is reached by `jmp r3` from a case block of `main_menu_render_and_field_edit_dispatch`, whose prologue already saved `r15`, `r4-r6`. The tail pops them. `bsr` in the stub may therefore clobber `r15`.
- `r3` is dead at the stub entry (the dispatcher's `jmp r3` already used it).
- The handler is a normal C function: `r0-r3` are free, `r4-r11` are callee-saved, and `sp` must be balanced.
- A plain C function in the table slot would livelock (dm32uv-re [docs/menu-system.md](https://github.com/awkaplan/dm32uv-re/blob/main/docs/menu-system.md), Snake v10).

**Handler obligations** (same protocol as `krk_run_screen` in `krk_core.c`):
- On entry the UI is already one history level deeper.
- Park the keypad poll while blocking: `*KEYPAD_POLL_ENABLE = 0`, scan under `LCD_KEYPAD_BUS_SEM`, then `lcd_bus_restore_after_keypad_scan()`.
- On Back, restore the APRS cells before returning:
  - `[0x0003e8f8] = 8+N`
  - `[0x0003e904] = lang ? @aprs_en : @aprs_zh` (lang from `settings[0x0003e9b4+0x30]` bit 6)
  - `[0x18000a90] = 2`
  - `build_sequential_index_list(8+N, 0x0003e5f8, 0x0003e5fa)`
  - `*STOCK_KEY_CUR = SCAN_BACK; *STOCK_KEY_PREV = 0; *KEYPAD_BLANK_REARM = 1; *KEYPAD_BLANK_COUNTER = 0; *KEYPAD_POLL_ENABLE = 0`
- This mirrors the stock render block, so it can also be done by calling the stock category entry. Whoever writes it should reuse `krk_core.c`'s exit sequence.

**Visible-rows clamp.** No explicit write to `0x0003e8f4` is needed for APRS.
- The stock subtype-2 renderer (`0x0303b720..0x0303b734`) recomputes `[0x0003e8f4] = min([0x0003e5f8] (index-list count), 8)` on every redraw.
- The stock APRS render block never sets it.
- The KRAKEN entry writes it explicitly, which is harmless.

## 4. What does not break, and what to watch

- **Stock rows unchanged:** items 0..7 keep the same table slots and the same blocks. Row labels are copied by pointer.
- **Upload Beacon sub-sub-list / Delete All:** run the stock blocks (items 6 and 7), unaffected by the larger count. Back from any stock item re-runs render block `0x0303dd40`, which reads the new descriptor count, so the list comes back with 8+N rows and the cursor (`0x0003e5cc`) where it was.
- **Second language:** covered by S3/S4. The descriptor holds exactly two entries (bit 6 of the settings byte).
- **Capacity:** at most 33 rows (level-2 list is 35 bytes), at most 32 by `cmphsi`.
- **Scroll bar** changes (more than 8 rows), as for the 9-row Scan list. This is the only visible difference on stock rows.
- **Not-edited paths that still use the old count:** none found. Count is read only by the render block (`0x0303dd54..0x0303dd60`) and by nothing else for descriptor `0x030ba924`.
- **Rows beyond the bound** silently jump to the tail. Forgetting S5 shows the new rows but OK does nothing.
- **Visibility gating of the category** (section 1) is unchanged by this recipe.

## 5. Evidence (QEMU, a throwaway test image)

- Built from stock + S1..S6 + a test blob at `0x030ee000` (a test descriptor in assembly and a C handler; throwaway, nothing in the repo). Digital VFO B, Menu, Up (wrap to APRS), OK:
  - the list shows 11 rows (`APRS On/Off ... Delete All, NEWROW0, NEWROW1, NEWROW2`) with a scroll bar
  - Up from row 1 wraps to row 11
  - OK on row 11 and row 10 each run their stub (blocking blue screen "NEW ROW n")
  - Back returns to the 11-row list with the cursor still on the row
  - a second Back returns to the top menu
- Stock rows compared against the stock image with the same key script: row 1 (ON/OFF picker), row 2 ("Input ID" editor, "Out of Range" dialog), row 7 (Upload Beacon), row 8 (Delete All, "Confirm delete?") give the same screens. The Upload Beacon frames are byte-identical (md5); the others were compared by eye, and in all of them only the scroll bar differs.
- Section 2 was worked out from QEMU `-d in_asm` trace diffs (stock vs each splice step).

## 6. Confidence and open questions

Confidence:
- High for the splice set and for rows 9..11 running a blocking handler in QEMU.
- Medium-high that nothing else reads the APRS count: image-wide literal searches plus disassembly of the three readers.
- Not tested on hardware.

Open questions:
1. **Reachability in analog mode.** The APRS category is absent from the analog top menu (idx24 list) and gated by `[0x0003fdc0]&4` and a settings bit. An "Analog Beacon" row inside APRS would be unreachable in analog mode, which is the mode it is for. Options:
   - extend the existing `krk_menu_append24` hook to also append id 9 (this exposes the 8 stock rows in analog mode too; untested whether they behave)
   - keep the analog entry in the KRAKEN category
   - the gating settings bit was not identified (CPS "APRS" box is a guess)
2. The code that copies the live index list to the level-2 slot (`0x0003e662`) was not located. The 33-item capacity is inferred from the strides.
3. The OK-path entry for id 9 (`0x030436c0`, uses an uninitialised-looking RAM descriptor at `0x18000bb0`) was not triggered in any tested flow, so its trigger is unknown. It is not needed for new rows.
4. Return-path restore for the APRS rows was verified with a minimal copy of the `krk_run_screen` exit protocol in QEMU, not with the real handlers.

## 7. v39 (2026-10-27): the sub-list follows the channel mode

Implemented in `krk_core.c` (`krk_aprs_render`, `krk_aprs_item`), `krk_aprs_menu.c`, `krk_aprs_tx.c` and the
manifest (`aprs_render_hook`, `aprs_render_tail`, the 11-entry arrays and jump table, bound 11).

- The render case block `0x0303dd40` is now `bsr krk_aprs_render` (over `lrw r3; ld.b r2,(r3,0x30)` =
  `72 10 43 d8`) followed by `br16 0x0303dd78` (`1a 04` over the `30 00` tail of the ld.b). Nothing is live
  across the block (the push has run; the tail is `bsr main_menu_screen_dispatch; addi sp,4; pop`), so a C
  function that clobbers r0-r3/r12/r13 is safe there.
- `krk_aprs_render` installs the descriptor pointer for the language, subtype 2, and the index list:
  - digital channel (`[0x0003e1e4] & 0xf0 != 0`): items 0..7, the stock list, stock handlers (DMR APRS);
  - analog channel: items `{0, 8, 4, 9, 10}` = APRS On/Off (stock item 0, the per-channel report-mode
    field byte 0x1C bits 3-2, shared), Upload ID (item 8: our digipeater path picker), Intervals Set (stock
    item 4, block-4 field 0x301 in 30 s steps, shared), Upload Beacon (item 9: one AFSK beacon now),
    Callsign (item 10).
- Labels and dispatch both go through the index list by item value: the subtype-2 renderer
  `display_list_with_icons_renderer` @0x03041644 draws `string_array[index_list[row]]`, and the history push
  (`ring_buffer_multi_table_append_and_flush` @0x0303699c) copies the 35-byte list by value to the level-2
  slot 0x3e664, which the action block reads as `item = *(0x3e664 + row)`. The top-level menu already uses a
  gappy list the same way. `krk_aprs_item` therefore maps `item - 8` to the extension table slot.
- Arrays (11 entries): 8 stock pointers, then the stock strings of items 1 and 6 again (so "Upload ID" and
  "Upload Beacon" read the same in both languages), then our "Callsign". Jump table: 8 stock blocks + 3x
  `krk_aprs_item_stub`. `cmphsi r2,11`.
- QEMU (generic datachip): analog VFO A shows the five rows, Up wraps to Callsign, OK opens the editor, Back
  returns with the cursor kept; digital VFO B shows the eight stock rows (`out/qemu/aprsmenu_analog`,
  `out/qemu/aprsmenu_digital`). tests/kraken_ui_test.py checks the tables and the render splice.
- Settings storage (from the stock RE, 2026-10-27): the APRS runtime cache is the struct at 0x00040078
  (+0 bit0 active-on-this-channel, bit1 upload type, bit2 fixed beacon; +2 report channel; +4 interval
  seconds = block-4 0x301 x 30; +8 upload ID = block-4 0x332; +0xC seconds counter). Stock sets bit0 at
  channel change (`channel_change_apply_dispatcher` 0x03057a98..) only for digital channels with report
  mode 1, and the periodic tick (`0x030625bc`, called from `ui_periodic_maintenance_task_loop` at
  0x030548a0 once a second) sends through 0x03062510 -> 0x03062290 (DMR data call) only when the channel
  mode nibble is digital. The analog path is stored in the callsign record (block 4, 0xFE0, byte 12).

### 7.1 Automatic beacon and the manual key (v39 second half)

Three more splices make the stock interval timer drive the AFSK beacon on analog channels. All sites
are in the stock image; expected bytes are in the manifest.

| Site | Stock | Patch |
|---|---|---|
| `0x03057aa8` in `channel_change_apply_dispatcher` | `cmpnei r3,0x30` (4 bytes) of `cmpnei r3,0x10; bf set; cmpnei r3,0x30; bt clear` (r3 = mode nibble) deciding `[0x40078]` bit 0 after the report-mode test (`andi 0xc; cmpnei 4`) | `bsr krk_hook_aprs_mode` (krk_stubs.S): re-executes the compare and, when r3 is not 0x30, sets C from `r3 != 0`; the stock `bt 0x03057aee` at `0x03057aac` is kept. Analog channels with APRS On now get bit 0 |
| `0x03054888..0x030548a4` in `ui_periodic_maintenance_task_loop` | the GPS-fix (`[0x38c6c]&0x40`) or fixed-position (`[0x40078]&4`) gate and `bsr 0x030625bc` (the tick), after the `[0x3fdc0]&4` and bit-0 gates | `bsr krk_hook_aprs_tick` (kraken_ui2) + `br16 0x030548a4` + nop16 filler. Digital: the hook applies the same gate and calls the stock tick. Analog: `[0x40084]++`, at `[0x4007c]` (>= 30 s) with TX not inhibited, no menu (`krk_ui_in_menu`) and no Kraken screen (`krk_screen_owner`): counter = 0, `event_queue_reset` (0x03054d80), skip when `[0x3e31f]&0x18` or the channel forbids TX (`[0x3e1e4]&8`), else `krk_aprs_tx_send`; "Channel busy" sets the counter to interval - 5 |
| `0x030158b8` in the programmable-key handler `0x03015824` | `bsr 0x03062510` (void(void), the DMR send; the last instruction before `pop`) | `bsr krk_hook_aprs_send_now`: AFSK on analog, stock on digital. The key path's own gates (bit 0, fix or fixed, not busy) are unchanged |

Stack: the hooks run in the maintenance task (OSTaskCreate at 0x03054496, stack 0x180 words). The send
path uses roughly 400 bytes (RMC copy 0x96, info 64, frame/tone buffers are static).
Design choice: on analog the no-fix case sends a status packet (as Upload Beacon does) instead of staying
silent, so indoor bench tests of the interval work; the fixed-position upload type is not offered on
analog. Stock's "APRS enabled" gate `[0x3fdc0]&4` still applies in both modes.

**Gates, corrected 2026-11-11 (issue #7; QEMU-verified on all three builds, not yet on a radio).** The analog path
used to copy the stock tick's `[0x320af] == 0` test, read as a "no CPS link" flag. `0x320af` is the screen-mode byte
(`docs/menu-exit-timer.md`): 0 after a fresh boot, but stock's "go to standby" event 0x2c stores 0x5b, which a radio
can show at standby after a menu visit or a TX (QEMU's standby path leaves 0). With 0x5b the due beacon was put off
every second, forever; the hand-sent Upload Beacon has no such test, which matched the report. The analog path now
waits only while a menu or one of our screens is up: `krk_ui_in_menu()` (mode 7..0x3f, 0x69, 0x77, as the CW ID and
VOX) or `krk_screen_owner`. Stock's loop already skips the whole APRS block while the mode is 7..0x3f
(`0x03054852`..`0x0305485c`: mode 0 or above 0x3f only), so a menu open longer than 10 s re-arms the timer and the
first beacon comes one interval after standby. The digital path is stock's, unchanged (its own `== 0` test included).

What the booted test found (`tests/aprs_beacon_e2e.py`, run from `tests/kraken_ui_test.py`):
- `[0x3fdc0]&4` is required: with it clear the loop never reaches the hook (`0x0305486e`) and nothing is sent. The
  same bit gates the Menu > APRS category itself (`menu_option_gen_idx23` 0x03049cdc, dm32uv-re `docs/menu-system.md`),
  so a radio that shows the APRS menu has it set.
- APRS On from the menu sets `[0x40078]` bit 0 at once, without a channel change: the commit,
  `settings_write_dispatcher` case 0x58 (`0x0304c2d2` `bsr 0x03061f80`, then `0x0304c2d6`..`0x0304c2e2` insert the
  value into bit 0), writes it directly (a write watchpoint on 0x40078 caught that store and no other). No hook needed.
- The loop also skips the block unless the dual-watch state `[0x400d8]` is 0 or 1. In state 2 (dwelling on a side
  that has a signal) the hook is not called. QEMU's emulated squelch is always open, so there dual watch dwells most
  of the time and the test turns dual watch off. On a radio a busy other side delays the beacon the same way (a gap
  over 10 s re-arms it).
- Open (seen in QEMU, probably an emulator effect): `[0x40078]` bit 0 is one radio-wide bit that
  `channel_change_apply_dispatcher` recomputes for whichever side it applies. In QEMU, with dual watch on (A analog
  with APRS On, B digital), a channel apply while side B was loaded (report field 0x30, mode nibble 0x10) cleared the
  bit, and the emulator's dual-watch flips back to A did not re-apply the channel, so the bit (and Kraken's
  `krk_channel_on_selected`, which follows the last committed side) stayed on B and the beacon stopped. On a radio
  every flip re-applies and commits the loaded side (`docs/dual-watch.md`), which would put both back on A. Worth one
  look with dual watch on during the hardware test. The test's menu scenario checks only that nothing is due while
  the menu is open.
- The interval is corrected against the RTC seconds as the CW ID's is (`krk_real_time`, `docs/fm-send-ram-tx.md` 6.3):
  with stock power save the OS tick runs about 2x fast, so an uncorrected 60 s came out near 30 s.

### 7.2 Dual watch (v40, 2026-10-28)

Hardware report on v39: with dual watch on (A analog, B digital), Back from Upload ID or Callsign showed the
8 digital rows for about half a second before the 5 analog rows. Cause: the channel struct at 0x0003e1dc
holds whichever side the receiver is on. The dual-watch engine (`key_hold_debounce_state_dispatcher`
@0x030642c0, state 1) flips the loaded side, bit 0 of 0x000400d9, and posts UI event 0x12 so
`vfo_apply_active_side_config` @0x030641f4 reloads that side. The selected side (A/B key) is bit 7 of
settings byte 0x2d (RAM 0x0003e9e1). Stock compares the two in `vfo_sync_active_side_on_ptt` @0x03064518,
and its beacon path exits dual watch before sending (state 4, reload the selected side, state 3).

- `krk_channel_analog` now answers for the selected side. It reads the struct only while the selected side
  is loaded, and remembers each side's mode nibble for the other side's dwell (in-target test: six cases).
  A render inside the short window between the flip and the reload can still read the other side once.
- The AFSK sender waits up to 1.5 s for the selected side to be loaded, then puts a dual-watch state 1
  into state 3 (hold; it resumes by itself), as stock does after its send. Otherwise "Dual watch: retry".
- The analog interval is timed with OSTickCtr (0x000426d0, 100 Hz), not the stock seconds counter: under
  dual watch the maintenance tick only runs while a side with APRS active is loaded, which would stretch
  the interval. A gap over 10 s between tick calls re-arms (APRS was off or inactive).
- `krk_run_screen` now calls the restore function it is given on Back. It used to always restore the
  KRAKEN list cells, which the stock Back redraw then overwrote.

### 7.3 Cursor on Back (v41, 2026-10-28)

Hardware report on v40: Back from Upload ID showed the list with the first row highlighted for a moment,
then the right row. Stock does not do this. Cause: our screens run inside the item-action case block, and
on return the shared tail `main_menu_screen_dispatch` @0x0303b6c4 draws one more list level from the active
cells before the posted Back pops it. That draw used the live cursor 0x0003e5cc and highlight slot
0x0003e5d5 (renderer top row = cursor + 1 - slot), which the opening OK had reset to 0 and 1. Setting only
the cursor made it worse (the list drawn scrolled, cursor on the top line). Fix in `krk_run_screen`: on
Back, load both from the top history entries the push @0x0303699c saved for the list level (depths at
0x000320ac [0] cursor, [1] slot; arrays 0x0003e4e8 and 0x0003e908). The transient frame is then identical
to the popped one. QEMU: APRS row 2 and KRAKEN row 10 (scrolled list) both return with no intermediate
frame. Applies to every screen in the APRS and KRAKEN lists.

### 7.4 Exit without the posted Back (v42, 2026-10-28)

Hardware report on v41: Back showed the list, then a black frame of about 0.1 s, then the list again. The
first list was the item-action tail's draw (7.3); the black frame was the stock Back, posted through the
key poll, clearing the screen before redrawing the popped level. v42 removes the second draw:
`krk_run_screen` pops the level the opening OK pushed with stock's `history_queue_pop` @0x03036c00 (the
exact inverse of the push @0x0303699c: cursor, highlight slot, index list, depths), restores the list
cells, and returns, so the tail draws the list once. The physical Back is still down, so the poll cells are
set to "already handled": current = previous = Back (0x392c4/5) and the debounce's last raw scan = Back
(0x30794, count 0x30795). The stock poll then treats it as held; on release it calls only Back's release
handler (`tx_state_poll_and_teardown` @0x03014360, table entry 6 + 0x1b), which posts nothing unless a
standby-mode Back press armed it. The v41 cursor/slot copy is subsumed by the pop. QEMU: APRS > Upload ID >
Back > Down > Back > Back gives list, list with cursor moved, top menu on APRS, standby; KRAKEN > About >
Back > Back > Back likewise.


### 7.5 Option rows and one-shot rows in the APRS list (2026-11-02)

A row appended to the APRS list (items 8..11) may be an **option level** (`struct krk_screen.opt`) or a **one-shot** (`KRK_SF_ONESHOT`): `krk_aprs_item` opens the stock option level
(cells only, returns) or runs the one-shot through `krk_run_screen`; OK at that level is routed by the splice at `0x0304d81c` (stock `cmpnei r3,7; bt 0x0304d846` in the
category-9 OK handler `0x0304d6c0`) to `krk_aprs_opt_ok`. Upload Beacon (item 9) is now a one-shot with popups (no custom screen, two-press confirm kept). Details, evidence and the cookbook:
`docs/stock-option-level.md`.
