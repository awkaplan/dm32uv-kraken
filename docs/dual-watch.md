# Dual watch: which channel is "the selected channel"? (2026-10-31)

## Hardware measurements (v46)
Serial sampling every ~130 ms of the live channel struct (`0x0003e1dc`: RX freq at `0x3846c`, flags8 at `0x3e1e4`,
`(flags & 0xf0) == 0x10` = digital), the "loaded side" bit (`0x000400d9` bit 0) and the selected side (`0x3e9e1` bit 7).
- Dual watch ON: the struct alternates between the two sides with a ~400 ms cycle (~130 ms on the short side).
  The loaded-side bit and the struct are NOT in step: samples exist with the bit == selected while the struct still
  held the other side's frequency/flags, and with the bit on the other side while the struct held the selected one.
- Dual watch OFF: side A selected = a VHF analog channel, Low power, perfectly stable; side B = a UHF DMR channel, High power.
  (The first analysis of the ON samples assumed the reverse and that UI-task code saw a frozen phase; the OFF run
  showed the UI-task observations were just the real selected channel. Nothing here depends on task context.)
- `stock_rx_active` (`0x0300f2a4`) returns 1 whenever the loaded struct is analog, whatever the receiver does.

## RE: where the struct is written
`commit_channel_record_dual_vfo` @0x030303bc is the single writer of `0x3e1dc` (the 0x33-byte record). Its tail also
stores a copy in a per-side record: `get_current_mode_state2() (byte 0x400d8) == 0 ? selected side (0x3e9e1 bit 7)
: loaded side (0x400d9 bit 0)` picks `0x3e21c` (B) or `0x3e358` (A), but only when the caller passes param_14 == 1
(events 1, 8, 9, 0xc and VFO edits), so those stock copies are not complete (event 0x12 reloads and frequency steps do
not refresh them) and are not used. `vfo_apply_active_side_config` @0x030641f4 only moves channel numbers/mode bits in
the settings block and posts UI event 0x12; the struct is rewritten later by `channel_change_apply_dispatcher`
@0x030573e8, which reads the loaded bit itself to pick its source and ends in the commit. So option (a) (read the
stock per-side records) is rejected as incomplete; option (b) is done at the commit, the one place that knows which
side it just wrote, instead of the end of vfo_apply_active_side_config (which runs BEFORE the struct changes).

## Design (krk_core.c)
- Splice `chan_commit_end_hook` at 0x03030550, the shared epilogue of the commit (stock `ee d9 00 20` =
  `ld.w r15,(sp,0)`) -> `bsr krk_commit_end_stub` (krk_stubs.S; re-executes the load) -> `krk_hook_chan_commit`.
- The hook repeats stock's side choice (`state2 ? loaded : selected`), snapshots RX, TX and flags8 (mode 2/3 folded to
  1 like the dispatcher does) into `dw.*[side]`, then sets `dw.tag = side` (snapshot first, tag last). State: 28 bytes
  in the main blob .bss, magic-guarded (SRAM is not zeroed).
- API (all exported to blobs 2-5): `krk_channel_side()` (-1 unknown), `krk_channel_on_selected()` (tag == selected),
  `krk_channel_flags()` / `krk_channel_mode()` / `krk_channel_analog()` / `krk_channel_dmr()` / `krk_channel_freq(tx)`:
  live struct when on selected, else the selected side's last snapshot, else unknown (-1 / 0).
  Power = `(flags >> 1) & 3`, bit 3 = may not transmit.
- The loaded bit is never consulted by consumers.

## Consumers
Selected-side API: scrambler (mode; stock cipher byte sampled while on selected, remembered per side; the
`stock_rx_active` busy guard only applies when on selected, since it is meaningless for the other side's analog
struct), burst voice (analog/DMR display and receive arming, TX gates), parrot gates and capture side tag, satellite
precondition, APRS list rows and APRS TX gate/no-TX flag, DMR link, squelch screen.
TX gates stay conservative: refuse unless `krk_channel_on_selected()` AND the mode matches (bvscr analog gate says
"Dual watch: retry").
Still the live struct (what the receiver is tuned to): krk_squelch sq_write_hook (REG_78 funnel), krk_aprsrx.c, krk_rf.c, bvscr 0x3e1e4 modem fix after playback, satrf RF words.

## Tests
tests/dw_checks.py: runs the REAL stock commit with the splice and checks tag/snapshots/stock store choice, noise
SRAM, both orders of the bit vs struct race, dual watch off, A/B key, mode-3 folding, scrambler written while the
struct is the other side's, cipher memory, TX gates. Other suites use `chan_poke` for a quiet single-side state.

## Hardware check (one action)
Read the 28-byte `dw` struct (address in out/kraken link map, symbol `dw`) with dual watch on: tag must equal the
side whose frequency is in `0x3e1dc`, in every sample, regardless of `0x400d9`.
