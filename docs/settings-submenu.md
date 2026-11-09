# Appending a row to the stock Radio Settings menu

How to add a Kraken row to **Settings > Radio Settings**, the way `aprs-submenu.md` and
`calllog-submenu.md` do for the stock APRS and Call Log sub-lists. Written while moving the
radio-wide settings (TX Power Trim, Squelch Mode) out of the KRAKEN category, 2026-11-03.

Nothing here has run on a radio. The addresses are from the pristine stock image
`progmem_stock_DP570UV_20260926_FULL.bin`; every `expect` below was dumped from it, and the two
live findings (section 4) come from QEMU runs of the current build.

## 1. Where Radio Settings lives

It is **not** a top-level category. The ten categories (labels at `0x030b7d68` zh / `0x030b7d90` en)
are Talk Group, Scan, Zone, SMS, Call Log, **Settings**, Record, Roaming, GNSS, APRS, so:

```
Menu -> Settings [category 5] -> Radio Settings [level-2 item 0] -> its 4 rows [level 3]
                                                                 -> a row's option level [level 4]
```

An appended row therefore sits one level deeper than the APRS rows. The category-level machinery
(`krk_catjmp` / `krk_okjmp` / `krk_itemjmp`, the `cmphsi r2,0xa` bound) is untouched.

Settings' own sub-list descriptor is `0x030b8128` = `{0x030b80f0 zh, 7, 2}` / `{0x030b810c en, 7, 2}`:
Radio Settings, Channel Set, Device Info, DTMF, BDC1200, TWO TONE, FIVE TONE.

## 2. The structures (all dumped from the stock image)

| What | Address | Stock bytes / value |
|---|---|---|
| **Descriptor** | `0x030b8488` | `68 84 0b 03 04 02 00 00 78 84 0b 03 04 02 00 00` |
| zh label array | `0x030b8468` | 4 pointers, count `4`, subtype `2` |
| en label array | `0x030b8478` | 4 pointers, count `4`, subtype `2` |
| zh ptr / count fields | `0x030b8488` / `0x030b848c` | `0x030b8468` / `0x04` |
| en ptr / count fields | `0x030b8490` / `0x030b8494` | `0x030b8478` / `0x04` |
| Row generator | `menu_option_gen_idx20` @ `0x03049b30` | appends items 0..3 unconditionally, no gating |
| **Generator call site** | `0x030423b8` | `00 e0 20 38` (`bsr 0x030493f8`, mode 20) — the only one in the image |
| Enter dispatch | `0x030431cc` | compare chain on the level-3 item; real function (`push16 lr`), epilogue `0x030432ea` |
| **Item-hook window** | `0x030431e6` | `82 08 62 04` (`bt16 0x030432ea; br16 0x030432ac`) |
| **Commit default edge** | `0x0304fad0` | `60 e8 ee 02` (`bt32 0x030500ac`) |

The four en labels are Voice Func, Key Func, Display Func, Other Func.

### The label arrays cannot grow in place

`0x030b8468` + 16 = `0x030b8478` (the en array) + 16 = `0x030b8488` (the descriptor itself). Exact
abutment on both sides; the `00 00` bytes are the high halves of the little-endian count words, not
padding. **Relocate both arrays**, as APRS and Call Log did. Each array has exactly one 4-byte
literal occurrence image-wide — its own descriptor pointer field — so relocation is clean.

Free space: `kraken_ui_data` is `0x030e7000`-`0x030e8000`; manifest data ends near `0x030e72a0`
and `0x030e72a0`-`0x030e8000` is erased (3424 bytes). This needs about 60.

## 3. The header comes free

The level-4 header handler `0x03038720` special-cases category 5 and then reads the label straight
out of the descriptor's array, indexed by the level-3 row:

```
03038720  cat = ...;  cmpnei16 a3,5 ; bt16 0x3038862     (non-category-5 elsewhere)
03038730  sub = ...;  bnez32 a3,0x303875e                (item 0 = Radio Settings falls through)
03038748  row = *(0x0003e685+2+[0x0003e4eb])
03038750  lrw a3,[0x30387c0]      -> 0x030b8488          (THE DESCRIPTOR)
03038752  a3 = desc[lang].ptr ; a3 = a3[row<<2]          (OUR RELOCATED ARRAY)
0303875a  br32 0x03038ece                                (draw centered)
```

So relocating the array titles the new row correctly in both languages. **No title hook is needed** -
`opt_title_hook` at `0x030385f4` only fires for category >= 10. A dynamic header (`title_fn`, as the
Scrambler row uses) is therefore *not* available here without a further splice inside `0x03038720`.

## 4. Which handler commits OK (settled by measurement, 2026-11-03)

**Opening a row and committing a value are two different key events, handled by two different
functions.** This is the single most important fact about this menu, and getting it wrong cost most
of a day: a splice that fires when the option level *opens* looks like it works (the row opens, the
menu navigates) while the setting silently never sticks.

| | key event | function | where our splice goes |
|---|---|---|---|
| OK on the **row** (opens its option level) | 0x1b | `0x0303a770`, category-5 edge `0x0303ab38` | the ENTER path |
| OK **inside** the option level (commits) | 0x22 | `0x0303a57c` -> `bsr 0x0304e560` -> jump table `0x030b11c4`[0] = **`0x0304e5a0`** | the COMMIT path |

The two are sibling entries of the key-event tables (`0x030b02dc` -> `0x0303a770`,
`0x030b0330` -> `0x0303a57c`), not a call chain.

### The commit site: `0x0304e5a0`

`0x0304e560` reads the Settings level-2 item and jumps through table `0x030b11c4` (7 entries, one
per Settings sub-item). Entry 0 is Radio Settings, and in stock it is a **pure no-op**:

```
0304e5a0  23 30      movi r0, 35     (0x23)
0304e5a2  01 31      movi r1, 1
0304e5a4  ff e3 90 3c  bsr 0x03035ec4   set_mode(0x23, 1)
0304e5a8  05 49      br  0x0304e83a     -> epilogue (pop r15)
```

It is the ideal site, for three independently checked reasons:

- **Nothing else reaches it.** The literal `0x0304e5a0` occurs exactly once in the 1 MB image: its
  own jump-table slot. The literal `0x030b11c4` occurs once, in the `lrw` pool at `0x0304e59c`.
- **Stock loses nothing.** Radio Settings has no commit handler in this chain at all; stock's own
  rows (Voice/Key/Display/Other Func) commit elsewhere, through `0x0304fab4`.
- **Measured.** A breakpoint on `0x0304e5a0` fires **exactly once, and only on the committing OK**
  (`hits_before_last_key=0, hits_from_last_key=1`), with `0x0304fab4` never firing for our rows.

**Frame caveat:** `0x0304e560`'s prologue is `push r15` ONLY - r4 is *not* saved, unlike the
`0x0303a770` frame that `opt_ok_hook` sits in. So the splice calls a hand-written stub
(`krk_settings_ok_stub`) that saves r4 itself and jumps to the epilogue `0x0304e83a` rather than
returning. Nothing in registers identifies the row there, so the handler reads it from the latch
(section 6).

### What was wrong before

An earlier probe walked to a **stock** row (Other Func > Auto PowerOff) and measured the commit
running through `settings_menu_confirm_dispatch` -> `0x0304fab4` -> `0x0304fad0`. That measurement
was correct *for stock rows* and was written up here as if it settled the question for ours. It did
not: our rows are set up by `krk_opt_enter` and never latch the field id stock dispatches on, so
they take the `0x0304e5a0` path instead. **Measure the row you are actually going to ship.**

Also confirmed live: Radio Settings renders the descriptor's rows (4 stock + ours) and Auto PowerOff
opens as a stock radio-marked option level. See `out/qemu/settings_nav/frames.png`.

## 5. The patch

### Data (in `kraken_ui_data`, after `0x030e72a0`)

| name | content |
|---|---|
| `krk_rs_lbl_x` | the new row's label, ASCII, NUL, `{align:4}` |
| `krk_rs_arr_zh` | `{stock_u32 0x030b8468, count 4}` + `u32 ["@krk_rs_lbl_x"]` |
| `krk_rs_arr_en` | `{stock_u32 0x030b8478, count 4}` + `u32 ["@krk_rs_lbl_x"]` |

### Splices

The descriptor pointers and counts, and the two relocated label arrays, are generated from the `rs` host in `patches/kraken_ui/menu_hosts.json` (see `docs/menu-hosts.md`). The manifest now lists three rows (TX Power Trim, Squelch Mode, Freq Unlock), so the counts are 4 + 3 = 7. The count bytes are plain counts, not the `value - 1` encoding. The host has no jump table, so there is no `cmphsi` bound. The subtype byte (descriptor + 5) stays `2`. The generator call site (section 2), the item hook and the commit splice (section 4) are hand-written in `kraken_ui.patch.json` and `krk_stubs.S`.

## 6. Which row is being committed: a latch, not menu state

Nothing in registers identifies the row at `0x0304e5a0`, and the obvious RAM source is not reliable:
the level-3 row byte `0x0003e4eb` reads our item while the keys are settled, but the handler runs
mid-keypress. So `krk_rs_item()` - which the ENTER splice calls with the correct item in r3 -
records it in a magic-validated word (`krk_rs_open`), and the commit reads that back.

Two things that are easy to get wrong here, both found by measurement:

- **Keep the latch out of `.bss.krk_list`.** That section holds `krk_list[]`/`krk_item_labels[]`,
  which the menu rewrites while a level is open. The latch lives in the main blob's plain `.bss`.
- **Do not clear the latch in the row generator.** `krk_hook_gen` also runs (mode 20) while the option
  level is opening, so clearing it there wipes what the enter path just stored. Staleness is handled
  in the lookup instead, which rejects the latch unless Radio Settings (Settings item 0) is the open
  level-2 item.

The subtype byte (descriptor + 5) stays `2`. The count byte is a plain count here, **not** the
`value - 1` encoding the `cmphsi` bounds use.

### Code

The rows are listed by `krk_hook_gen()`, the generator hook shared by every host (mode 20 here): call
the stock generator first, then append. `krk_rs_item_stub` follows `krk_calllog_item_stub` (reached by `bsr`
with the carry flag as `cmpnei r3,3` left it, `r3` = item; must not return - it jumps to
`0x030432ea`, this dispatcher's `pop16 lr`, or to `0x030432ac` for stock item 3).
`krk_rs_ok_stub` calls `krk_opt_commit()` from `krk_optrow.c` unchanged.

`krk_open_opt()` (`krk_core.c`) special-cases only `cat == 10` and `cat == 9` today; it needs a
`cat == 5` arm reading the level-2 item (`*(0x0003e662+2+[0x0003e4ea])`, 0 = Radio Settings) and the
level-3 item (`*(0x0003e685+2+[0x0003e4eb])`). The numeric-entry path needs no new splice: the
existing `opt_numtitle_hook` resolves its row through `krk_open_opt()`, so it starts working for
these rows as soon as that arm exists.

Budget: the main blob had ~260 bytes free, so the handlers belong in blob 2 (`kraken_ui2`), with
`defsym` entries for `krk_opt_enter` / `krk_opt_commit` as `krk_txtrim.c` already does, and the new
`.text.<name>` added to the `KEEP` list in `kraken_ui.ld`.

## 7. Open

- Back from a level-4 row returns one level deeper than the APRS case: re-verify the cursor/slot
  restore in `krk_run_screen` at depth 4, and the Menu Exit Time keep-alive (`0x0003e396`).
- Channel Set (Settings item 1, descriptor `0x030b89a0`, 22 rows, a jump table at `0x030b0a18`) is
  feasible the same way and is written up in the 2026-11-03 investigation, but is not used: the
  settings being moved are radio-wide, so they belong in Radio Settings.

## Stale latch (found 2026-11-06)

`krk_settings_opt_ok` sits on the OK handler of every mode-0x1c screen under Radio Settings, which
includes the stock Voice/Key/Display/Other Func row lists (`docs/otherfunc-submenu.md` 1). The latch
is only cleared by `krk_rs_item()`, which does not run for items 0..3, so after opening and leaving
one of our rows the next OK on a stock row would have committed our row with that row's cursor. The
handler now also requires icon mode 2 (an option level of ours); a row list has icon mode 1.
`tests/otherfunc_e2e.py` step 5 walks it.
