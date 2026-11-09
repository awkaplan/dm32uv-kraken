# "Last Heard" row in the stock Call Log sub-list (DM32.00.01.048)

**Status:** built in v43 (`1ab726f9`), in-target tests and QEMU pass. Not yet run on a radio.

**Answer in one line:** Call Log is top-level category **4**. Its sub-list labels come from the
descriptor at `0x030b8060`, and its rows come from `menu_option_list_dispatch(5, ...)` (`0x030493f8`),
which is called at three sites. Its item action is a compare chain, not a jump table. Adding item 4
takes 2 relocated 5-entry label arrays, 4 descriptor edits, 3 `bsr` retargets and one 4-byte splice in the
compare chain. The Last Heard screen itself is F1 (`krk_lastheard.c`), unchanged. It stays in KRAKEN
as well, because Call Log is not reachable from the analog top menu.

Why Call Log: the stock Call Log is the radio's call history, and Last Heard is the other half of it. Stock
logs only private calls addressed to this radio (`docs/last-heard-hooks.md` §3), while Last Heard logs
every voice call received, talkgroups included. OpenGD77 puts Last Heard under Contacts. On this radio
that is "Talk Group" (category 0), a contact editor, which fits less well.

## 1. Stock structure

| What | Where | Notes |
|---|---|---|
| Category labels | `0x030b7d68` zh / `0x030b7d90` en | id 4 = 呼叫记录 / "Call Log" |
| Render case block | `0x0303dbf6` (entry 4 of `0x030aff74`) | If depth byte `[0x000320ac+4]` == 1 (fresh entry): `menu_option_list_dispatch(5, 0x3e5f8, 0x3e5fa)`. If the resulting count is 0: `history_queue_pop` (nothing to show). Otherwise: cells from the descriptor, then the shared tail `0x0303dd78` |
| Descriptor | `0x030b8060`: `{0x030b8040, 4, 2}` zh, `{0x030b8050, 4, 2}` en | Arrays: Missed / Answered / Sent / Delete (zh 未接呼叫 / 已接呼叫 / 已拨呼叫 / 删除记录). The two arrays are packed back to back and end at the descriptor, so they cannot grow in place. Each is referenced only by the descriptor |
| Row generator | `menu_option_list_dispatch` mode 5, case `0x03049834` (table `0x030b1074`) | Appends items 0..3 for bits 0..3 of byte 5 of the 8-byte menu-visibility record (settings block 4, offset `0x500`, read at entry). These are the CPS menu checkboxes. Returns void |
| Generator call sites | `0x0303dc06` (render block), `0x03036b62` (entry path that pushes history depth 14), `0x0303ee3e` (entry path that rebuilds the list and puts the cursor on item `r4`) | All three are `bsr 0x030493f8` with `r0 = 5`, `r1 = 0x3e5f8`, `r2 = 0x3e5fa`. After the call, each caller uses only callee-saved or freshly loaded registers |
| Item-action case block | `0x0303cf80` (entry 4 of `0x030aff18`) | `item = *(0x3e664 + row)`. Items 1/0/2 go to `0x03041cdc` / `0x03041c4c` / `0x03041d28`. Then `cmpnei r3,3; bt32 0x0303d872 (tail); br16 0x0303cfb6` (item 3 → `0x03041d74`) |
| Other descriptor readers | `0x03036b50`, `0x0303edde`, `0x0303ef86` (install cells); `0x03038674` (`strings[list[row]]` as a title) | All read through the descriptor, so the relocation covers them |
| Top-menu visibility | `menu_option_gen_idx23` `0x03049d18` | Category 4 is listed only if `byte5 & 0x0f` (at least one CPS Call Log row is on). `idx24`, the analog short list, never lists it |

## 2. Patch (manifest `kraken_ui.patch.json`)

The splices are generated from the `calllog` host in `patches/kraken_ui/menu_hosts.json` (see `docs/menu-hosts.md`): the 5-entry label arrays (the 4 stock pointers and "Last Heard"), the two descriptor pointers, and the two descriptor counts (4 to 5). The label is ASCII and is used in both languages, as the APRS "Callsign" row is. The three generator call sites and the item-action splice are hand-written in `kraken_ui.patch.json`; they are the sites listed in section 1.

- `krk_hook_gen` (`krk_core.c`, shared by every host with a generator) calls the stock generator unchanged, then
  appends our items (item 4, from `menu_hosts.json`) when the mode is 5 and the list has room (33 items). CPS row visibility is still applied to the stock rows. With all
  four stock rows hidden, the list is just Last Heard, but the top menu then hides Call Log itself
  (idx23 gate above). KRAKEN still has the screen in that case.
- `krk_calllog_item_stub` (`krk_stubs.S`) is reached by `bsr` with C still as `cmpnei r3,3` left it
  (`bsr` does not touch C) and `r3` = item. Our items are `KRK_CALLLOG_FIRST`.. (`krk_menu_rows.h`):
  - item 3: jump to the stock block `0x0303cfb6`
  - item 4: `bsr krk_calllog_item`, then the tail
  - anything else: the tail, as stock does

  It never returns. The enclosing dispatcher's prologue saved `r15`, and the tail `0x0303d872` pops it
  (the same frame argument as the APRS stub). The stock `br16 0x0303cfb6` at `0x0303cf9c` is now dead.
- `krk_calllog_item` runs F1's screen with `krk_run_screen`. On Back, the runner's `history_queue_pop`
  restores the index list. `krk_calllog_restore` then reinstalls count, pointer and subtype from the
  descriptor, as the stock render block does on a return (depth ≠ 1), so there is a single redraw
  (`docs/aprs-submenu.md` 7.4).
- Cost: about 190 bytes of `.text` in the main blob (now 11,308 of 12,288 bytes), no `.bss`, 80 bytes in the data sector.

## 3. Evidence

- In-target (`tests/kraken_ui_test.py`, "Call Log" sections):
  - all four splices decode to their targets
  - both descriptors hold 5 entries, the 4 stock strings by pointer, then "Last Heard"
  - the wrapper passes the caller's `(5, count, list)` to the stock generator and appends 4 after
    `[0,1,2,3]`, `[1,3]` and `[]`; it leaves mode 7 and a full 33-item list alone; `r4`–`r11`/`sp` are preserved
  - running the real compare chain from `0x0303cf96` with item 3 / 4 / 5 / 9 reaches the stock Delete
    block, `krk_calllog_item`, and the tail, respectively
- QEMU, generic factory datachip, digital VFO B:
  - `out/qemu/calllog_lh`: Menu → Call Log shows 5 rows (Missed, Answered, Sent, Delete, Last Heard). Up
    wraps to Last Heard; OK opens the screen under the stock "Call Log" title; Back returns to the list
    with the cursor kept; Back returns to the top menu on Call Log.
  - `out/qemu/calllog_stock`: OK on Missed gives the stock "Empty CallLog!" dialog, which returns to the
    5-row list.

## 4. Open

- Not run on hardware.
- The entry paths at `0x03036b62` and `0x0303ee3e` are hooked but were not driven in QEMU (their triggers
  were not traced). The splice is the same as at the render site, which was driven.
- The Missed / Answered / Sent detail screens with real entries were not exercised (the QEMU datachip has
  an empty log). They read the descriptor only for their title (`0x03038674`, items 0..2), which is unchanged.
