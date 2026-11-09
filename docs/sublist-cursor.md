# Selected sub-list row

**Answer:** byte `0x0003e4ea`, i.e. `0x0003e4e8 + 2`. It is the 0-based row highlighted in the open sub-list. Byte `+1` of the same struct is the top-level menu row.

## Evidence

**Static.** Every stock multi-item item-action case block reads this byte first. Category 0's block at `0x0303cf1c`:

```
0303cf1c  lrw    r3, 0x3e4e8
0303cf1e  ld.b   r2, (r3, 0x2)        ; selected sub-list row
0303cf20  lrw    r3, 0x3e662
0303cf22  addu   r3, r2, r3
0303cf24  ld.b   r2, (r3, 0x2)        ; item = index_list[row + 2]
0303cf26  cmphsi r2, 5                ; bound check
0303cf2c  lrw    r3, 0x30aff40        ; per-item jump table
0303cf2e  ldr.w  r3, (r3, r2 << 2)
0303cf32  jmp    r3
```

Category 4's block at `0x0303cf80` reads the same byte and then compares the item against 0–3.

The dispatcher that reaches these blocks (`main_menu_render_and_field_edit_dispatch`, `0x0303cec6`) selects the **category** from byte `+1` and the top-level index list at `0x0003e63f`.

**Dynamic.** QEMU run `out/qemu/rows` (2026-10-15) used this key script:

```
-1:0:1:31:0:20;-1:0:1:17:20:120;2:11:1:17:60:400;2:7:1:17:60:400;2:11:1:18:60:400;2:11:1:18:60:400;2:7:1:17:60:300;2:10:1:17:150:900;2:10:1:17:150:900
```

The keys are Menu, Up, OK, Down, Down, OK, Back, Back. It opened row 2 (GPS Grid) of the KRAKEN sub-list. Back returned to the list with that row still highlighted.

## Item handler fires on OK only

The same run answers this. Two Down presses inside the sub-list moved the highlight but did not run the item handler. The handler ran on OK.

## Use in the patch

`krk_core.c` `KRK_SELECTED_ROW`. The KRAKEN index list is sequential (`build_sequential_index_list`), so the row equals the item. It is still bound-checked.
