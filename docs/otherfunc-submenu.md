# The Other Func nest: a third VOX child and a CW ID parent

How `Settings > Radio Settings > Other Func` was extended on 2026-11-06 so that VOX and CW ID each
have a stock-style sub-menu:

```
Other Func > VOX      > VOX SW (stock), Gain Level (stock), VOX Delay (ours, moved in)
           > CW ID    > CW ID SW, Interval, Callsign             (all ours, analog channels only)
```

Everything here comes from two static traces of the pristine image
`progmem_stock_DP570UV_20260926_FULL.bin`; `tests/otherfunc_e2e.py` is the booted check. It also
corrects the model in `docs/nested-submenu.md`, which blamed the wrong code for the Channel Set
failure.

## 1. The menu is a state table; a level's cells are filled by its RENDER entry

`0x030b019c` holds 12-byte records `{u8 ids[8]; u32 handler}`, one per state; a menu level owns seven
states (mode, mode+1 .. mode+6) and the engine runs `handler[mode]` to draw a level. A key moves to
`ids[k]` (OK is `ids[5]`, Back `ids[6]`), runs that handler, and afterwards re-runs `handler[mode]`
whenever the redraw flag `[0x320b0]` is non-zero.

| level | mode | render | OK | Back |
|---|---|---|---|---|
| 3 Radio Settings rows | 0x15 | `0x0303cdf0` | `0x0303a770` | `0x030376b4` |
| 4 Other Func rows | 0x1c | `0x0303cbc4` | `0x0303a57c` | `0x03037698` |
| 5 a row's option level, or the VOX child list | 0x23 | `0x0303cb54` | `0x03036420` | `0x0303767c` |
| 6 a VOX child's option level | 0x2a | `0x0303cb04` | `0x03035bf0` | `0x03037620` |

- **OK handlers only call `set_mode(next, 1)`** (`0x03035ec4`). The render entry of the new mode
  then runs `0x03036a48` (on a fresh entry: history push, cursor 0, slot 1, index count zeroed), the
  per-category init that fills the cells, and the draw `0x0303b6c4`.
- `history_queue_pop` sets the flag to 3, so the level popped back to is **rendered again**. Nothing
  has to be restored by hand after a commit, and a list init must not write the cursor (the pop
  restored it).
- This is what defeated the Channel Set nest (`docs/nested-submenu.md` 3): cells installed from an
  OK handler are overwritten by the render entry that follows. It was not the tail of `0x0303a57c`.

The OK at mode 0x1c is the same handler for every level-4 screen under Radio Settings: for a stock
row list it advances to level 5 (`0x0304e560` -> table `0x030b11c4`[0] = `0x0304e5a0`,
`set_mode(0x23, 1)`); that site is also where our Radio Settings option rows commit
(`krk_settings_opt_ok`). The two are told apart by the latch **and** by the icon mode (2 = one of our
option levels, 1 = a row list): the latch alone was stale after a row of ours had been opened and
left, and would have committed it on the next OK in any stock list.

## 2. Other Func (level 4)

| What | Address | Stock |
|---|---|---|
| Descriptor | `0x030b8708` | zh `{0x030b8660, 21, 2}`, en `{0x030b86b4, 21, 2}` (arrays packed against it) |
| Generator | mode 21 = `0x03049c00`, called only from `0x030432e6` | items 0..9 always, 12..20 by capability; **10 and 11 never** |
| Level-5 init table | `0x030b0990` (21 entries, bound `cmphsi r2,21` at `0x03044920`) | [3] VOX `0x030449ec`, [4] VOX Delay `0x03044a28`, [10], [11] = exit `0x03045082` |
| Level-5 OK table | `0x030b130c` (21 entries, bound at `0x0304fe72`) | [3] `0x0304fec8` = `set_mode(0x2a,1); br 0x030500ac`, [10], [11] = popup tail `0x030500a4` |
| Level-5 header | `0x03038994` | `0x030b8708[lang].ptr[item]`: a lookup |

Items are item numbers, not screen rows: every dispatcher resolves the row through the saved index
list (`[0x3e6a8 + 2 + [0x3e4ec]]`), so the list may be reordered freely.

**Items 10 (Lone Worker) and 11 (Man Down) are free slots.** Stock never lists them and their table
slots are the no-op exits, so a new parent needs no relocation and no bound change. Item 11 is the CW ID
parent (2026-11-05, `docs/cw-id.md`); item 10 is still free and untouched. The splices (`of_item11_*`, one
4-entry template per item: label in the zh and en arrays, level-5 init slot, level-5 OK slot) are
**generated** from the `nests` section of `patches/kraken_ui/menu_hosts.json` (`docs/menu-hosts.md`
section 3), not tabulated here. What each one does:

- the two label slots (zh `0x030b868c`, en `0x030b86e0`) point at `CW ID`;
- the init slot (`0x030b09bc`) points at `krk_of_cwid_kids_stub`, which installs the child list: count 3,
  label array, subtype 2, sequential index list (as `0x030449ec` does for VOX);
- the OK slot (`0x030b1338`) points at stock's own VOX arm `0x0304fec8`, which tests neither row nor
  child;
- `of_gen_hook` (`0x030432e6`, a branch hook in `hooks.json`) calls `krk_hook_of_gen`: stock generator,
  then item 4 leaves the list. On an analog channel (`krk_channel_analog()`) item 11 takes its place;
  on a digital one the rows after it move up one.

## 3. The VOX child list (level 5) is data

Descriptor `0x030b82b8`: zh `{0x030b82a8, 2, 2}`, en `{0x030b82b0, 2, 2}`, used by the installer
`0x030449ec` and the level-6 header only. Both follow the count, so the third row and its title are
two relocated 3-pointer arrays (child 2 = stock's own VOX Delay strings) and two counts. The four
splices (`vox_kids_{zh,en}_{ptr,count}`) and the two arrays are generated from the `child_lists` of the
`nests` section of `menu_hosts.json`.

## 4. Level 6: three compare chains on the level-4 item

There is no table at level 6. The init `0x03045fd8`, the commit `0x03050de8` and the header
`0x03038b9e` each test `level-3 item == 3` (Other Func), then `level-4 item == 3` (VOX), then treat
child 0 as VOX SW and **every other child as Gain Level**. An unpatched child 2 would therefore open
the Gain list and store its cursor as the gain.

The three branch hooks (`of_l6_enter_hook`, `of_l6_ok_hook`, `of_l6_title_hook`) are lines of
`hooks.json` (expanded by `tools/gen_manifest.py`), each over the stock `cmpnei r3,3; bt` pair on the
level-4 item, with its stock bytes checked at build:

- **init** (`0x0304623c`, stock `43 3b 7c 08`) -> `krk_of_l6_enter_stub`: r3 = item; return into stock's
  VOX arm, or leave at `0x03046336`;
- **commit** (`0x0305102e`, stock `60 e8 2c 03`) -> `krk_of_l6_ok_stub`: likewise, exit `0x03051686`;
- **header** (`0x03038c32`, stock `43 3b 3e 09`) -> `krk_of_l6_title_stub`: stock lookup, our string to the
  draw tail `0x03038ece`, or stock's blank.

The C side (`krk_vox.c`) maps (item, child) to a `struct krk_opt` and reuses `krk_opt_enter` /
`krk_opt_commit` unchanged: the cells of an option level are the same at every depth, only the mode
byte differs. The header handler does not test the level-2 item, so the title hook does.

## 5. What the user sees, and what is stored where

| Row | Editor | Storage |
|---|---|---|
| VOX > VOX SW | stock ON / OFF | per channel / VFO, stock |
| VOX > Gain Level | stock Level 1..9 | block 4, stock |
| VOX > VOX Delay | radio marks: 0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0 s | stock's value (`0x3ea13`), stock's write `flash_field_write_cached(4, 0xa3, v)` |
| CW ID > CW ID SW | ON / OFF | callsign record spare byte bit 7 (block 4 0xFE0, `docs/cw-id.md`) |
| CW ID > Interval | 1, 2, 3, 5, 10, 15, 20, 30 min | the same byte, bits 6-0 |
| CW ID > Callsign | the shared callsign editor (a screen) | the callsign record, shared with APRS |

The Callsign child is the one level-6 entry that is a **screen**, not an option level: `krk_of_l6_enter`
runs it with `krk_run_screen` from the level-6 init, where the render entry has just pushed level 6 as for
any option level. Back pops that level (`krk_leave`) and the restore reinstalls the CW ID child list
before the stock tail draws, so the list comes back with the cursor on Callsign.

Stock's VOX Delay editor (a 0.1 s spinner in a popup, subtype 4, tied to item 4 by table slots and
field id 94) is no longer reachable; a stored value between two list entries shows as the nearest.
Option levels of ours are capped at eight rows (`krk_opt_enter`), hence eight delays.

## 6. The VOX engine's one stock hook outside the menus

`vox_sq_read_hook` (`0x0300f2c2`, `hooks.json`) replaces the `bsr reg_0xc_bit1_read` in
`vfo_display_state_nibble_bits_check` (`0x0300f2a4`, = `stock_rx_active`), stock's only read of the
squelch bit. To hear the mic, the engine (`krk_vox.c`) switches the RF chip to a sense word for a few
tens of ms about three times a second, and in that word the bit reads 1: without the hook stock lit the
RX LED, opened the audio (a tick per window) and latched "receiving" for about 60 s after speech.
`krk_vox_sq_read` returns 0 during a sense window and for 150 ms after it, and does the stock read
otherwise.
