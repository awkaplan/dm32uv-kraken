# Appending rows to the stock Channel Set menu

How per-channel Kraken features become rows of **Settings > Channel Set**, the stock per-channel
menu, instead of rows of the KRAKEN category. Written up 2026-11-03; the nesting investigation
(`docs/nested-submenu.md`) added the measurements in section 4.

Companion to `docs/settings-submenu.md`, which does the same for **Radio Settings** (Settings item 0,
radio-wide settings). The two differ in one important way, section 4. Since 2026-11-06 one row, Burst Voice, is the
parent of a nested sub-menu with stock option levels (section 8).

## 1. Where Channel Set lives

Channel Set is **not** a top-level category. The ten categories (labels at `0x030b7d68` zh /
`0x030b7d90` en) are Talk Group, Scan, Zone, SMS, Call Log, **Settings**, Record, Roaming, GNSS, APRS:

```
Menu -> Settings [category 5] -> Channel Set [level-2 item 1] -> its rows [level 3, depth 3, mode 0x15]
                                                              -> a row's option level [depth 4, mode 0x1c]
```

Settings' own sub-list descriptor is `0x030b8128` = `{0x030b80f0 zh, 7, 2}` / `{0x030b810c en, 7, 2}`:
Radio Settings, Channel Set, Device Info, DTMF, BDC1200, TWO TONE, FIVE TONE.

## 2. The structures

| What | Address | Stock value |
|---|---|---|
| **Descriptor** | `0x030b89a0` | `{zh 0x030b88f0, count 22, subtype 2}`, `{en 0x030b8948, 22, 2}` |
| Row generator | `menu_option_gen_idx6` @ `0x03049930` | a 21-arm CPS / channel-mode visibility chain |
| Generator call site | `0x03042406` | `bsr 0x030493f8`, mode 6 (the menu-tree site) |
| **ENTER dispatch** | `0x03047324` | `r2` = level-3 row; `cmphsi r2,22` @ `0x03047332`; table `0x030b0a18` |
| **COMMIT dispatch** | `0x0304dc8c` | `r3` = level-3 row; `cmphsi r3,22` @ `0x0304dc9a`; table `0x030b116c` |
| Level-4 header | `0x03038720` | category-5 arm; for item 1 reads `[0x30387c4]` = the descriptor |

The 22 stock en labels are Rx Frequency, Tx Frequency, TX Power, CTC/DCS, Tx Contact, Color Code,
Time Slot, DMR ID, VFO Save To CH, Channel Type, Double Slot, Channel Name, Rx Group List, New Chan,
Bandwidth, Digit Encrypt, Optional Signal, PTT ID, Rx Squelch Mode, SFT-D, Freq Offset, APRS Receive.

### Four things must be relocated, not grown

- The **two label arrays** abut each other and the descriptor (`zh + 22*4 = en`, `en + 22*4` = the
  descriptor), so neither can grow in place.
- The **ENTER table** `0x030b0a18` is packed directly before `0x030b0a70`.
- The **COMMIT table** `0x030b116c` is immediately followed by `0x030b11c4`.

Each has exactly one reference image-wide - the descriptor's own pointer field, or the literal-pool
word the `lrw` loads (`0x03047370` and `0x0304dcc0`) - so relocation is clean. All four live in
`kraken_ui_data` (`0x030e7000`-`0x030e8000`).

### Both bounds must grow, and the commit one matters more

The raw `cmphsi` immediate is **value - 1**. The ENTER bound's out-of-range target `0x03047cf4` is the
function's own epilogue, so an un-grown ENTER bound merely does nothing. The COMMIT bound's
out-of-range target `0x0304e416` is **not** an epilogue - it is the stock numeric / Channel-Name path -
so an un-grown COMMIT bound would silently run stock's numeric editor on one of our rows.

## 3. The header comes free

The level-4 header handler `0x03038720` special-cases category 5 and, for level-2 item 1, does
`lrw a3,[0x30387c4]` (= `0x030b89a0`, the descriptor) then `desc[lang].ptr[level-3 row]`. So relocating
the label arrays titles the new rows in both languages with **no title hook**.

The cost: a **dynamic header is not available** for a row placed here. `opt_title_hook`
(`0x030385f4`) only fires for category >= 10, so `struct krk_opt.title_fn` is ignored - the Scrambler
switch's live-key header (`Scrambler 00001234`) is not shown on its Channel Set row. The same applies
to Radio Settings (`docs/settings-submenu.md` 3). `docs/nested-submenu.md` 4 shows the splice that
*would* restore it, at `0x03038ab8`, for a level one deeper.

## 4. Which handler each keypress reaches (MEASURED)

**Opening a row and committing a value are two different key events handled by two different
functions.** Getting that wrong cost most of a day on Radio Settings (`docs/settings-submenu.md` 4),
so it is settled here by per-keypress attribution rather than by analogy -
`tests/chanset_rows_e2e.py --measure-all`, which reports `hits_before_last_key` vs
`hits_FROM_last_key` for a truncated key sequence whose LAST key is the one under test:

| address | the OK that OPENS the option level | the OK that COMMITS |
|---|---|---|
| `0x03047324` ENTER dispatch | before 0, **FROM 1** | before 1, FROM 0 |
| `0x0304dc8c` COMMIT dispatch | before 0, FROM 0 | before 0, **FROM 1** |
| `0x0304e5a0` Radio Settings' commit arm | 0 | 0 |

The commit chain in full:

```
OK (event 0x22) -> 0x0303a57c -> bsr 0x0304e560          dispatch on the Settings level-2 item
                -> 0x030b11c4[1] = 0x0304e5aa -> bsr 0x0304dc8c   dispatch on the level-3 row
                -> 0x030b116c[row] = krk_cs_ok_stub
```

**Channel Set needs no latch, and that is the whole difference from Radio Settings.** Both of its key
events dispatch on the level-3 row through a per-row jump table, so the row arrives in a register at
each site (`r2` at the enter site, `r3` at the commit site) and `krk_cs_item(item)` /
`krk_cs_opt_ok(item)` are handed it directly. Radio Settings' commit arm (`0x030b11c4[0]` =
`0x0304e5a0`) is a pure no-op with no row information at all, which is why that menu needs the
`krk_rs_open` latch and this one does not.

Both case blocks run with their dispatcher's prologue already done, so the stubs must **not** return:
`krk_cs_item_stub` ends at `0x03047cf4` (`addi sp,104; pop r4-r5,r15`) and `krk_cs_ok_stub` at
`0x0304e55a` (`addi sp,48; pop r4-r8,r15`).

## 5. The patch

### Data (`kraken_ui_data`)

| name | content |
|---|---|
| `krk_cs_lbl_*` | one ASCII label per appended row, NUL, `{align:4}` |
| `krk_cs_arr_zh` / `krk_cs_arr_en` | the 22 stock string pointers + one per appended row |
| `krk_cs_itemjmp` | the 22 stock ENTER case blocks + `krk_cs_item_stub` per appended row |
| `krk_cs_okjmp` | the 22 stock COMMIT case blocks + `krk_cs_ok_stub` per appended row |

### Splices

The descriptor pointers and counts, the relocated label arrays, and the two relocated jump tables with their `cmphsi` bounds are generated from the `cs` host in `patches/kraken_ui/menu_hosts.json` (see `docs/menu-hosts.md`). The bounds stay value-1 encoded, as section 2 notes, and the count bytes are plain counts. The generator call site and the item and commit stubs are hand-written in `kraken_ui.patch.json` and `krk_stubs.S`; the table entries point at `krk_cs_item_stub` and `krk_cs_ok_stub`.

`krk_hook_gen` (shared by every host with a generator) calls the stock generator **first**, so all 21 arms of its CPS / channel-mode
visibility chain keep working, and only then appends our items. A consequence worth remembering when
writing tests: **a row's position in the rendered list is not its item number** - the stock chain hides
rows, so the position has to be read out of the index list the generator built (`0x3e5f8`, or its
depth-3 copy `0x3e687`).

The second mode-6 call site, `0x0303e294`, is deliberately **not** hooked: it belongs to a different
wizard that installs cells from descriptor `0x030b8150` (CTC/DCS) and never reads `0x030b89a0`.

## 6. The rows that live here

| item | label | kind |
|---|---|---|
| 22 | DMR Link | screen (blob ui5) |
| 23 | Burst Voice | nest parent: Burst Voice (the screen, blob ui4), Mode, Key-up Lead, Relay Capture (section 8) |
| 24 | Scrambler On/Off | option level |
| 25 | Key | numeric entry |
| 26 | Parrot On/Off | option level |
| 27 | Replay Audio | one-shot |
| 28 | Check | one-shot |
| 29 | Clear Audio | one-shot |
| 30 | Status | screen |

All nine are per-channel digital features, which is why they belong in the per-channel menu rather
than the KRAKEN category. Scrambler Fmt stays in the KRAKEN list: it is a key-format / interop
knob, not a per-channel setting a user reaches for.

The Scrambler and Parrot rows are **flat**. Grouping them under two nested parent rows was built, measured and abandoned on
2026-11-03 (`docs/nested-submenu.md`); the model that explains that failure, and a nest that works under Channel Set, came on
2026-11-06 (section 8).

**Mode gating:** the rows are always visible. Both engines already refuse on an analog channel
(`scr_idle_reason()` reports idle unless `krk_channel_dmr()`; Parrot's arming checks
`krk_channel_dmr()`), so the row opens and explains itself rather than vanishing. Hiding them would
mean a new arm in the stock visibility chain per row, and a row that silently disappears is worse for
a user than one that says why it will not do anything.

### The ceiling: one more row (2026-11-06)

**32 rows is the limit.** Both dispatch bounds are 16-bit `cmphsi` (oimm5: at most 32, raw 0x1f) and
the four relocated tables sit 0x80 apart (32 entries), so exactly one more row fits without a 32-bit
compare and another table move. v11-v14 briefly had a 32nd row, `VOX`, listed on analog channels
only through a flag checked in the Channel Set generator hook; it was withdrawn when the VOX settings were
gathered in stock's own Other Func > VOX sub-menu (`docs/otherfunc-submenu.md`).

Past 32 needs more than a bigger table. Built and booted on 2026-11-05 for CW ID, then not shipped
because CW ID moved to Other Func (`docs/cw-id.md`): each `cmphsi rX,22; bt <out>` (6 bytes) becomes
`bsr <trampoline>; nop`, and the trampoline compares with the 32-bit `cmphsi`, loads the table from the
same stock literal-pool word and jumps, or jumps to the stock out-of-range target (`0x03047cf4` ENTER,
`0x0304e416` COMMIT, which is NOT an epilogue). The tables then need 0x100 spacing. The level list still
holds at most 33 items (u16 count + 33 bytes), so 22 stock + 11 ours is the hard end.

## 7. Tests

- `tests/kraken_ui_test.py` - static: the descriptor counts and relocated pointers, the 22 stock
  strings kept, the appended labels, both relocated tables with the stock case blocks kept and the
  stub entries appended, both widened bounds, and the screen-table slots.
- `tests/chanset_rows_e2e.py` - booted: the list carries items 22..30 with the right labels, OK opens
  an option level at depth 4 with radio marks, OK there commits with the **RAM state read back**, and
  the list returns. `--measure-all` re-runs the section-4 attribution as a regression check.
- `tests/bvnest_e2e.py` - booted: the Burst Voice nest (section 8).
- `tests/optrow_checks.py` - the Scrambler switch and Key unit checks drive `krk_cs_opt_ok(item)`
  with the menu state set to "Channel Set row `item` is open", and the booted numeric-editor
  scenarios (digits, Back deletes, range check, reopen) walk the Channel Set list.

Two traps these were written around, both of which cost flaky runs:

- `Boot.wait_step()` reports a key as **sent**, not dispatched. Always `b.until(<a predicate that
  identifies the level you want>)` before reading; never read straight after the walk.
- The walk to our rows is a dozen Downs deep, and at the default 400 ms gap it outlasts the stock 5 s
  Menu Exit Time, which drops the radio to standby part-way through. Runs of Downs use ~110 ms.
- A commit is best waited on by **the RAM state**, not the menu level: the setter runs on the
  committing keypress but the stock "Setting is OK" popup then blocks the key handler for a second
  before the level pops, so a predicate on the menu can time out while the commit has in fact happened.

## 8. A nested sub-menu under a Channel Set row: Burst Voice (2026-11-06)

```
Settings > Channel Set > Burst Voice > Burst Voice     the screen (blob ui4): send, record, play, retransmit, clear
                                     > Mode            Fast / Robust
                                     > Key-up Lead     0, 128, 192, 256, 320, 480, 640, 960 ms
                                     > Relay Capture   ON / OFF
```

The settings were keys of the screen (3, Up/Down, 2) until 2026-11-06; they are now stock option levels and a flash record
(`docs/burst-voice.md` 13). The code is `krk_bvcfg.c` (blob 2) and four stubs in `krk_ofstubs.S`.

### 8.1 The mechanism (static trace of the stock image)

A level's cells are filled by that level's RENDER entry, which runs after every OK handler; an OK handler only calls
`set_mode(next, 1)` (`docs/otherfunc-submenu.md` 1). Under Channel Set that gives:

| level | mode | render (cells) | OK |
|---|---|---|---|
| 3 Channel Set rows | 0x15 | stock | `0x0303a770` -> `set_mode(0x1c, 1)` |
| 4 a row's level (option level, or the child list) | 0x1c | `0x0303cbc4` -> category table `0x030afef0`[5] -> `0x03047d44` -> Settings item 1 -> **`0x03047324`**, which jumps through the per-row table (the "ENTER" table of section 2) | `0x0303a57c` -> `0x0304e560` -> `0x030b11c4`[1] -> **`0x0304dc8c`**, the per-row "COMMIT" table |
| 5 a child's level | 0x23 | `0x0303cb54` -> category table `0x030afecc`[5] -> `0x0304547c` -> Settings item 1 -> **`0x03045084`**: rows 3..15 through the 13-entry table `0x030b09e4` behind `cmphsi r2,13; bt 0x03045478` at `0x03045094` | `0x03036420` -> `0x03050414` -> Settings item 1 -> **`0x030500b0`**: a compare chain on the row (3, 7, 12, 13, 15) ending `cmpnei r3,15; bt 0x0305040e` at `0x0305031a` |
| 5 header | 0x23 | `0x03038994`, Channel Set arm `0x03038a7a`, ending `cmpnei r3,15; bt 0x03038e9c` (blank) at `0x03038ab8` (`docs/nested-submenu.md` 4) | |

So what section 4 calls the ENTER and COMMIT dispatchers are the level-4 init and the level-4 OK. Stock's own nested rows show
the pattern: row 15's COMMIT entry `0x0304e254` is `set_mode(0x23, 1); br epilogue` and tests nothing, and row 15's level-4
init `0x030479e8` installs the child list without touching the cursor (`0x03036a48` zeroes it on a fresh entry; the history
pop restores it after a child's level closes).

### 8.2 The patch

| what | where | does |
|---|---|---|
| level-4 init of item 23 | relocated ENTER table entry 23 = `krk_cs_bv_kids_stub` (`menu_hosts.json` row `stubs`) | the child list: count 4, labels, subtype 2, sequential index list, row icon 1, visible rows; never the cursor |
| level-4 OK of item 23 | relocated COMMIT table entry 23 = stock `0x0304e254` | `set_mode(0x23, 1)` for any child |
| level-5 init | `cs_l5_enter_hook` at `0x03045094` (stock `0c 3a f1 09`) -> `krk_cs_l5_enter_stub` | stock rows 3..15 return into the table jump; otherwise `krk_cs_l5_enter`: child 0 runs the screen (`krk_run_screen`, restore = the child list), children 1..3 `krk_opt_enter` |
| level-5 OK | `cs_l5_ok_hook` at `0x0305031a` (stock `4f 3b 79 08`) -> `krk_cs_l5_ok_stub` | row 15 returns into stock's arm; otherwise `krk_cs_l5_ok`: `krk_opt_commit` (setter, marks, "Setting is OK", stock pop) |
| level-5 header | `cs_l5_title_hook` at `0x03038ab8` (stock `4f 3b f1 09`) -> `krk_cs_l5_title_stub` | row 15 returns into stock's lookup; otherwise the child's label, or stock's blank |

The three level-5 sites are reached only for Channel Set (Settings item 1) and only past stock's own rows, so the C tests just
the level-3 item (23) and the child (`0x3e6aa[0x3e4ec]`). The stubs use one macro, `ROW_HOOK` (redo the compare; stock's row:
`rts`; else `CALL_TAIL handler, exit`); every site's function saved r15 in its prologue. No splice in the main blob; the
generator change is the per-row `stubs` override of a host's jump tables (`docs/menu-hosts.md` 3).

The parent stays a host row whose `screen` is the blob-4 descriptor, so `krk_hook_gen` lists it only while blob 4 is flashed
and none of the nest is reachable without it.

### 8.3 Measured (QEMU, 2026-11-06)

`tests/bvnest_e2e.py` (four QEMUs, about 3.5 minutes; part of the booted section of `tests/kraken_ui_test.py`): the parent is
listed (item 23, row 11 on the analog boot channel); OK opens the child list at depth 4, mode 0x1c, four numbered rows; each
setting child opens a radio-mark level at depth 5, mode 0x23 with the default marked (Fast, 320 ms, OFF); OK there calls
`flash_block_write(4, 0xFC0, rec, 16)` once with the expected bytes (a breakpoint captures them), the child list comes back with
the cursor on the child, and the child reopened has the mark on the new value (its `get` reads the record back from flash);
Back from the level keeps the cursor; the screen child runs the screen, which reads the stored setting, and Back returns to the
child list on row 0. A one-off QEMU probe (`cs_nest_probe.py`, not kept) repeated the per-keypress attribution of section 4 on stock's own CTC/DCS
nest and on this one (section 8.4).

Trap found on the way: the QEMU model takes at most 32 key steps (`DM32_KEYSEQ_MAX`, `dm32_gpio.c`) and drops the rest without
a word; a 33-key walk never pressed its last Back and looked like a screen that ignores Back.

### 8.4 Per-keypress attribution (`cs_nest_probe.py`, 2026-11-06)

`hits_before_last_key / hits_FROM_last_key` for walks ending on each OK (method of section 4). Stock's own CTC/DCS nest (item 3)
and the Burst Voice nest (item 23 > Mode) gave **identical** numbers:

| address | OK on the row (opens the child list) | OK on a child (opens its level) | the committing OK |
|---|---|---|---|
| `0x03047324` level-4 init | 0 / **1** | 1 / 0 | 1 / **1** (the re-render after the pop) |
| `0x0304dc8c` level-4 OK | 0 / 0 | 0 / **1** | 1 / 0 |
| `0x03045084` level-5 init | 0 / 0 | 0 / **1** | 1 / 0 |
| `0x030500b0` level-5 OK | 0 / 0 | 0 / 0 | 0 / **1** |

The OK on a child reaches the level-4 OK and then, from the same keypress, the level-5 init: the render that follows the OK
handler. The committing OK reaches the level-5 OK, and the level-4 init runs again from it, refilling the child list after
stock's pop.

