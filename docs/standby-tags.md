# Standby tags

Two-letter marks on the standby screen's bottom line for the Kraken features that are switched on and act
at standby on the current channel. Code: `patches/kraken_ui/krk_tags.c` (blob 2). User view:
`docs/user-guide/README.md`, "Standby tags". Built 2026-11-08; QEMU-checked, not yet on a radio.

## 1. Where: the bottom line, not the status bar

The status bar (rows 0..25, `display_draw_status_bar` 0x03019434) has only two always-free gaps (x 25..43
and x 84..106, about one 18 px icon each): GPS x7, key lock x44, auto power-off x67, messages x107, DW x133,
person x161, mute x184, battery x205..232 are all conditional stock icons. So the tags go on the bottom line.

`standby_screen_refresh` 0x03018e64 (void) draws it on standby, always the same way:
`display_fill_rect(0, 290, 240, 320, 0)`, then "Menu" (or the two-glyph zh label) at x 12..59 in the theme
colour, the A/B bitmap at x 200..227, then `display_update_datetime(1)` (rows above 290). **x 60..199 of rows
290..319 is free on standby.** Other users of the line (call screens, scan, digit entry with "Option"/"Del",
menus) all start with the same full-width clear, so tags never linger on them. The widest non-standby
labels reach x 83 on the left and x 180 on the right.

Font: stock has one ASCII font, 12x24 (`display_draw_string_gb2312`, through `krk_text`). Four two-letter
tags fit: 4 x 24 + 3 x 10 = 126 px, centred on x 130.

## 2. When

| Path | Task | What |
|---|---|---|
| `standby_bottom_hook` 0x030195d2 | menu (prio 31) | `bsr standby_screen_refresh` in `standby_screen_refresh_with_statusbar` 0x030195cc -> `krk_hook_standby_bottom`: the stock line, then the tags. This is the only path that draws standby's bottom line (boot, Back from a menu, `dialog_show_number_or_alt` after calls and popups), so no screen check is needed |
| `krk_tags_tick` from `krk_hook_maint_tick` | ScanDisp (prio 38, about 1 s) | Redraws only when the set differs from `krk_tags_shown`. Gates: screen-mode byte `0x320af` is 0 or 0x5b (as stock's own standby redraws of the status bar), no Kraken screen (`krk_screen_owner`), not keyed (`0x3fdc8` bit 3); the mode byte is re-read just before drawing |

Stock's clock redraw (`display_update_datetime(0)` at 0x03054902) runs from the same maintenance loop, so drawing
there has a stock precedent. Every primitive takes the LCD semaphore 0x39344 itself; the residual race (a
call screen starting between the gate and the draw) is the one stock's clock has too.

The tick is not gated on `stock_rx_active()`: in QEMU that squelch read reports "receiving" at idle, so the tick
never drew; on a radio an analog carrier keeps standby on screen anyway, and the read is an RF-chip access.

`krk_tags_shown` (u32, `"TG"` in the top half + the mask) lives in the main blob's .bss, because blob 2's .bss
claims are full; blob 2 imports it by name.

## 3. Which features, and their reads

| Tag | Colour | Read (blob) | Rule |
|---|---|---|---|
| VT / VX | red | `krk_vox_tag()` (2) | one of them, as `eff_mode()` picks: test mode > VOX SW (0x3e9e4 bit 0); only on an analog channel on the selected side with dual watch idle (`gate_block` 2-4) |
| CW | red | `krk_cwid_active()` (2) | `idle_reason() == 0`: switch on, callsign set, analog |
| BN | red | `krk_beacon_active()` (2) | `krk_hook_aprs_tick` ran on an analog channel in the last 10 s (stock only calls it with APRS On), interval >= 30 s, callsign set |
| SA | red | `krk_sat_active()` (3, imported) | tracking ACTIVE or RESTORE |
| SC | theme | `SCRAMBLE_REG` 0x1100038c != 0 | the register is non-zero exactly while the scrambler is applied |
| MN | theme | `krk_mon_on()` and the selected channel analog | |
| SQ | theme | `krk_sq_active()` (2) | mode Normal/Tight and the selected channel analog |
| PR | theme | `krk_parrot_armed()` (2) and the selected channel digital | |

Red = can key the transmitter on its own. Theme colour = `0x030b0790[settings[0x63] & 7]` (0x3ea17), the colour
stock uses for "Menu". Not tagged (design decision): Freq Unlock and TX Power Trim (passive until tuning
or PTT), Burst Voice relay capture (only with its screen open).

**Selected side, not the loaded channel.** The first build used `sqst.dirty` and `VFO_FLAGS8` (the loaded
channel). Dual watch flips the loaded channel every few hundred ms, so SQ and MN would have blinked. Every
channel rule above uses `krk_channel_analog()` (the selected side).

## 4. Tests

- `tests/test_tags.py` (host): layout (centring, 4 slots, "+N"), order and colours, theme wrap, per-feature
  rules, the hook order, every tick gate, redraw only on change.
- QEMU frames: `SPINOR=1 POKE=/w/dm32uv-unleashed/tests/data/tags_pokes.txt tools/qemu_ui_run.sh tags 2
  '-1:0:1:31:0:20' --settle-after 80`. RX Monitor, Squelch and VOX are switched on behind the UI, then the
  Monitor off; the frames show MN, MN SQ, red VX MN SQ, VX SQ. The poke addresses come from the build's
  `kraken_ui2.elf`; re-read them if `.bss.afsk` or `.bss.feat` change.

## 5. Open (hardware)

- Channel mode and the zh language (only VFO mode and English were booted).
- That every return to standby (end of an RX call, scan exit) goes through `dialog_show_number_or_alt`. If one
  doesn't, the tags come back only at the next change of the set.
- The 0x5b standby value (QEMU reads 0).
