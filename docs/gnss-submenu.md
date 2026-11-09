# "GPS Grid" row in the stock GNSS sub-list (DM32.00.01.048)

**Status:** built and in the image. The build checks the stock bytes at every site below. The row in
this menu has not been run on a radio yet; the GPS Grid screen itself (F4, `krk_grid.c`) has, from the
KRAKEN menu before it moved here.

**Answer in one line:** GNSS is top-level category **8**. Its sub-list labels come from the descriptor at
`0x030b7e28`, and unlike Call Log it has no row generator: the render block builds the list straight from
the descriptor's count. Adding item 5 takes 2 relocated 6-entry label arrays, the descriptor edits, and
one 4-byte splice in the item-action compare chain. The general recipe is in [menu-hosts.md](menu-hosts.md);
the closest worked example is [calllog-submenu.md](calllog-submenu.md).

## 1. Stock structure

| What | Where | Notes |
|---|---|---|
| Descriptor | `0x030b7e28`: zh array `0x030b7e00`, en array `0x030b7e14`, count 5 | Items 0..4: GNSS On/Off, GNSS Info, Time Zone, Disp Format, GNSS Mode |
| Render block | `0x0303dd06` | Copies count, strings and subtype from the descriptor, then `build_sequential_index_list(count)`. So item = row, and raising the count is enough to show a new row |
| Descriptor readers | `0x030386fc`, `0x0303dd10`, `0x0303f270` | All read through the descriptor, so relocating the arrays covers them. The title handler `0x030386e8` draws a row's header from the same array |
| Item-action case block | `0x0303d2a0` | A compare chain. Items 0..3 are diverted early; at `0x0303d4a8`, `cmpnei r3,4; bt16 0x0303d872` guards the item-4 (GNSS Mode) block at `0x0303d4ac`. `0x0303d872` is the shared out-of-range tail |

## 2. Patch

All of it is generated from the `gnss` host in `patches/kraken_ui/menu_hosts.json`
(`python3 tools/gen_menu_hosts.py`, then `gen_manifest.py`).

- **Labels.** The 5 stock string pointers per language, plus ours, are copied to 6-entry arrays at
  `0x030e7720` (zh) and `0x030e7760` (en). Our label strings sit at `0x030e7700`. The descriptor's two
  array pointers and its count are edited to match.
- **Item action.** `gnss_item_hook` in `hooks.json` turns the 4 bytes at `0x0303d4a8` into
  `bsr krk_gnss_item_stub` (`krk_stubs.S`). The stub redoes the compare with `r3` = item: item 4 goes on to
  the stock GNSS Mode block, items `KRK_GNSS_FIRST` (5) and up go to `krk_gnss_item()` (`krk_core.c`),
  anything past our rows takes stock's out-of-range exit. The enclosing dispatcher already saved `r15`
  and its shared tail pops it, so the stub never returns.
- **Back.** When a row's screen closes, `krk_gnss_restore()` reinstalls the list cells from the descriptor,
  as the stock render block does.

Stock rows behave as before: items 0..3 never reach the splice, and the stub hands item 4 straight back
to stock.

## 3. Adding a row

Append it to the `gnss` host's `rows` in `menu_hosts.json` and regenerate. `KRK_GNSS_MAX` and the label
arrays follow. Check that the arrays still fit their claim in `allocations.json`.
