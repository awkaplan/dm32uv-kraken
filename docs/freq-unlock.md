# Freq Unlock: wider RX and TX ranges behind a legal warning

**Settings > Radio Settings > Freq Unlock**, a stock option level: ON / OFF, default OFF. Choosing ON shows a warning that
OK must be held for 10 s to accept; OFF is immediate. Code: `patches/kraken_ui/krk_band.c`. Host test:
`tests/test_band.py`. Booted check: `tests/freq_unlock_e2e.py`. Stock addresses are from DM32.00.01.048.

## 1. The ranges

| | Stock (model byte `0x3fdc4` = "other", as on the radios tested) | Freq Unlock ON |
|---|---|---|
| RX and keypad entry | 108-136 AM, 136-174, 400-470 MHz | 23-136 AM, 136-280 (VHF path), 280-520 MHz (UHF path) |
| TX | 136-174, 400-470 MHz | 136-520 MHz: 136-280 on the VHF PA, 280-520 on the UHF PA |

The edges are the ones published for DM-32 firmware range mods. Nothing here was measured on a radio. The
source is the DM-32 thread on the infotex58.ru forum, http://infotex58.ru/forum/index.php?topic=1148.0 (Russian; post numbers below):

- The mod shared most (697 downloads, firmware 046, page 11-15 notes post #204) covers
  23-136 MHz AM with a 25 kHz step, plus 136-279 and 280-520 MHz. Variants: 220-470, 220-520, 350-520 MHz
  (rebuilt on 048 and 049), and 136-260 / 400-520 MHz.
- Owners report 23-136 MHz is receive-only in practice and "very deaf" at 27 MHz (posts #208, #229). Sensitivity at
  250 MHz is poor because of the duplex filter (post #32). The thread's main modder warns that the filters and PA stages
  are built only for the rated bands (posts #70, #72, #293).
- The only power-versus-frequency figures come from a different radio with the same FD6818 chip, a TH-F12 clone (post #71):
  7-10 W at 136-160, 1 W at 170, 2.5-18 W at 200-240 (erratic), 0 W at 300-350, 0.1-0.2 W at 380-390,
  8-11 W at 400-500 and 3.6 W at 510 MHz.

TX covers the same range as RX, except below 136 MHz: the radio stays receive-only for three reasons:

- Stock's channel-change dispatcher marks any channel whose RX is in the AM slot as "may not transmit" (it sets
  flags8 bit 3 when `[0x3e31f]` bit 5 is set, `channel_change_apply_dispatcher` after its band check).
- Owners report TX does not work there.
- The harmonics of 36-68 MHz would land unfiltered in the 108-136 MHz aviation band.

## 2. How it works

**RX.** Every RX range check and the keypad entry check read one RAM table, `0x00038470`: ten u32 values in 10 Hz
units. `rf_config_load_from_flash_block2` @`0x0300d568` fills it once, at boot. Its readers are:

- `frequency_band_range_check_and_status_set` @`0x0300d84c`
- `value_in_range_multi_band_check` @`0x0300da48`
- `bcd_frequency_in_range_check` @`0x0302f6c4`
- `parse_bcd_numeric_input_dual_range_validation` @`0x0304dcc4` (keypad entry)
- `channel_step_frequency_bound_check` @`0x0301b214` (calibration span only)

| Index | Stock | ON | Meaning |
|---|---|---|---|
| [0], [1] | 400.0, 470.0 | 280.0, 520.0 | UHF: RX LNA bit 3 set |
| [2], [3] | 136.0, 174.0 | 23.0, 279.99999 | VHF. The keypad check tests [0..3] only, so VHF must start at 23 to accept 23-136 |
| [4] to [7] | from flash block 2 | unchanged | calibration span (the power and mic interpolators clamp to it) |
| [8], [9] | 108.0, 136.0 | 23.0, 136.0 | AM slot. The band check tests it first, so 23-136 is AM and receive-only |

The loader writes 400 / 470 / 136 / 174 / 108 as constants after its flash reads, so the table is not calibration data.
Splice `band_load_hook` (its one call, in `radio_boot_full_init_sequence` at `0x0305f384`, before
`startup_channel01_frequency_load_and_validate`) runs it and then writes the ON values. OFF writes the stock values back
from the loader's own literals (`0x0300d7fc` / `0x0300d800` / `0x0300d804` / `0x0300d808`, pinned by the manifest
asserts `band_lit_*`; 136.0 is an immediate).

**TX.** The band check tests TX against literal ranges chosen by the model byte. It sets `0x3e31b` bit 4 (TX on the UHF
PA) and bit 5 (TX not allowed, which `ptt_press_handler` refuses with a status message). Splices `band_check_hook_1..4`
cover all four of its stock calls (`0x03036f34`, `0x030381ea`, `0x03057820`, `0x03057906`). Each runs the stock check and,
while ON, rewrites only bits 4 and 5:
- bit 4 is set from 280 MHz up;
- bit 5 is set outside 136-520 MHz.

Satellite mode (`krk_satrf.c`) calls the stock check directly, so its TX keeps the stock limits.

**Calibration.** `freq_calibration_apply_all` interpolates PA drive and mic gain from calibration points, clamped to the
span [4..7] and bounded to index 0..3 (`frequency_to_step_index_resolve`). Off-band TX therefore uses the nearest edge's
calibration point. That never reads past the table, but the power is uncalibrated.

## 3. The warning

When OK commits ON from OFF, the option row's `confirm` hook (new in `struct krk_opt`, run by `krk_opt_commit` before the
setter) does the following:

1. Clears the content area and draws nine lines of warning plus a status line, "Hold OK 10 s: ON".
2. Ignores the OK that chose ON until it is released, then counts a fresh hold of 10 s on the 100 Hz OS tick, showing
   "Holding... N s". Releasing early restarts the count.
3. Back cancels. So do 60 s without a completed hold.
4. After a completed hold, shows "Release OK" and waits up to 30 s for every key to come up, so stock's key poll does not
   get a held key back.
5. Drains the UI events queued while it blocked the menu task, as the screen runner does. Hands the keypad back the
   stock way (`KEYPAD_BLANK_REARM`).

Accepted: the setter runs, then stock's "Setting is OK" and pop, then a popup with the ranges for 2 s. Refused:
"Unlock cancelled" for 1 s, then the same pop without "Setting is OK"; the setting stays OFF. OFF, or ON while already
ON, needs no hold.

## 4. Storage

Its own 16-byte record in block 4 at `0xFD0`: magic "FUNL", on, ~on, nine zero bytes, checksum. `0xFD0` is candidate 2 in
`docs/settings-field.md` 3.3; candidate 1, `0xFE0`, is the callsign record. The record is written through stock's
`flash_block_write`, and only on a change. A vendor CPS write (all FF) or a factory reset (all 00) fails the check and
reads as OFF.

RAM: one word, `band_on`, at the tail of the AFSK claim (`.bss.afsk`, 4 of its last 20 bytes). The boot hook sets it from
flash. Only the magic `"FBON"` means ON, so power-up noise reads as OFF.

## 5. Not verified

- Verified 2026-11-05 in QEMU only: the image builds; `tests/freq_unlock_e2e.py` passes (a short OK press and Back on the
  warning leave it OFF with the stock table; a held OK turns it ON with the wide table, and the list comes back);
  `tests/settings_e2e.py` still passes for the other Radio Settings rows.
- Nothing has run on a radio. Before keying anywhere new, the radio needs a dummy load and a spectrum check of power and
  harmonics. Transmitting outside the bands your licence or authorization covers is illegal.
- Unknowns that need a radio:
  - VFO display and frequency step at the new edges.
  - Stock's AM demodulation below 108 MHz.
  - What the stock CPS does with channels outside its own limits. The thread says stock CPS cannot write them; they are
    entered on the keypad instead.
