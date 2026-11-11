# Flash and SRAM allocation

Where Kraken's code and state live, and why those places are safe. The claims themselves are in
`allocations.json` (this repo's blobs) and `patches/mi_rng/allocations.json` (F0a), in dm32uv-re's
`data/allocations.json` format. Those files are the source of truth; this page explains them. The builder
refuses overlapping claims and any changed byte outside a declared one.

## Base image

- `dm32uv-re/data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin` (`image.json` `base_image`)
- SHA-256 `9b08ce6d8d0b63617d9c1f9548d93ea6008a9ebce5cd36089752f74a62a09716`, 1 MiB, mapped at `0x03000000`
- Firmware `DM32.00.01.048`. The per-unit sector `0x0300B000` is blank (0xFF) in this image; the update
  file never writes it.
- The same claims serve the `DM32.01.L01.048` and `DM32.01.01.049` builds: both windows are unused there too (the
  translator checks the flash window is erased in each build's stock image, and no stock reference points into the
  SRAM window). See [firmware-builds.md](firmware-builds.md).

## The window

dm32uv-re's `data/allocations.json` "windows":

- Flash `0x030E1000`-`0x03100000` (31 sectors of 4 KiB). Every byte is 0xFF in the base image.
- SRAM `0x18004C00`-`0x18008000`.

Below the window, sector `0x030E0000` is partly used: its first 0x4B8 bytes are stock data, the rest is
erased. It is not claimed and not written.

Stock never reads the flash window. The only stock code that touches it is the firmware updater, which
erases `0x0300C000`-`0x03100000` before writing a new image (`flash_erase_firmware_region_locked`
@0x03004040, called only from the YMODEM receive loop). See "Flagged sectors re-checked" below for the
literal scan.

## Flash claims

| Claim | Range | Size | Blob | What |
|---|---|---|---|---|
| `kraken_ui5_code` | `0x030E1000`-`0x030E2000` | 4 KiB | `kraken_ui5` (F3) | DMR link quality screen (`krk_dmrlink.c`). Reclaimed from dm32uv-re's `ramdump_probe_code` (excluded, see below). Built last; reached through the constant descriptor address `KRK_EXT5_LO` in `krk_ext.c` |
| `mi_rng_code` | `0x030E2000`-`0x030E3000` | 4 KiB | `mi_rng` (F0a) | CTR_DRBG for the DMR privacy MI (`patches/mi_rng/`) |
| (free) | `0x030E3000`-`0x030E4000` | 4 KiB | | Unclaimed, erased. Held an earlier experiment that is no longer in the image |
| `kraken_ui_code` | `0x030E4000`-`0x030E7000` | 12 KiB | `kraken_ui` (P3) | KRAKEN menu, screen runner and the main screens: Last Heard (F1), RSSI (F2), grid (F4), band scope (F5), TX power trim (F6), RF Lab (P5), About |
| `kraken_ui_data` | `0x030E7000`-`0x030E8000` | 4 KiB | `kraken_ui` (P3) | Relocated menu label arrays and jump tables, the KRAKEN label |
| `kraken_ui4_code` | `0x030E8000`-`0x030EE000` | 24 KiB | `kraken_ui4` (F13) | Burst voice: modem, framing, AMBE packing, screens. Starts on dm32uv-re's `snake_code` sectors (excluded) |
| `kraken_ui_code2` | `0x030EE000`-`0x030F7000` | 36 KiB | `kraken_ui2` | Extension screens: APRS (F8) and the AFSK/AX.25 encoder and decoder, audio probe, F9/F11/F12/F13 hooks and controls, libgcc. The extension screen table is first, at `0x030EE000` |
| (free) | `0x030F7000`-`0x030F8000` | 4 KiB | | Unclaimed, erased |
| `kraken_ui3_code` | `0x030F8000`-`0x03100000` | 32 KiB | `kraken_ui3` (F10) | Satellite mode (SGP4, TLE table, screens) and the APRS callsign editor `krk_callsign.c`. Ends at the top of flash |

Free space at the end of each claim in the current build (trailing 0xFF bytes, so an upper bound):

| Claim | Free |
|---|---|
| `kraken_ui5_code` | 2060 |
| `mi_rng_code` | 3272 |
| `kraken_ui_code` | 44 |
| `kraken_ui_data` | 1940 |
| `kraken_ui4_code` | 3738 |
| `kraken_ui_code2` | 3653 |
| `kraken_ui3_code` | 536 |

`out/kraken/steps/*/build/*.map` has the exact section sizes after a build.

Cross-blob rule: blobs build in manifest order (`kraken_ui`, `kraken_ui3`, `kraken_ui2`, `kraken_ui4`,
`kraken_ui5`); `@blob:sym` binds only to earlier blobs. A later blob's screen is reached through a constant
descriptor address in the `krk_ext.c` table (`kraken_ui4` at `0x030E8000`, `kraken_ui5` at `0x030E1000`).

## SRAM claims

| Claim | Range | Size | Blob | What |
|---|---|---|---|---|
| `kraken_afsk_state` | `0x18004C00`-`0x18005000` | 1 KiB | `kraken_ui2` (F8) | AFSK/AX.25 decoder state (`.bss.afsk`) |
| `kraken_ui_state2` | `0x18005000`-`0x18005100` | 256 B | `kraken_ui` (P3) | KRAKEN visible list and label array (`.bss.krk_list`) |
| `kraken_feat_state` | `0x18005100`-`0x180053A0` | 672 B | `kraken_ui2`, `kraken_ui5` | State of F3, F9, F11, F12, F13 controls (`.bss.feat`). `kraken_ui5`'s `.bss.feat` is appended right behind blob 2's |
| `kraken_ui3_state` | `0x18005600`-`0x18005A00` | 1 KiB | `kraken_ui3` (F10) | Satellite mode `.bss`, and the callsign editor's |
| `kraken_bigbuf` | `0x18005A00`-`0x18007000` | 5.5 KiB | `kraken_ui2` | One buffer shared by F9 parrot and F13 burst voice, one owner at a time (`.bss.big`) |
| `kraken_ui4_state` | `0x18007000`-`0x18007400` | 1 KiB | `kraken_ui4` (F13) | Burst voice modem and framing state |
| `mi_rng_state` | `0x18007400`-`0x18007600` | 512 B | `mi_rng` (F0a) | DRBG state |
| `kraken_ui_state` | `0x18007640`-`0x18007A80` | 1088 B | `kraken_ui` (P3) | UI `.bss`: screens and the last-heard ring (uses 0x3FC) |
| `kraken_aprs_state` | `0x18007A80`-`0x18007B00` | 128 B | `kraken_ui2` | `.bss.aprs` (`KRK_APRS_BSS`): small state of the APRS features of 2026-11-11 (issues #3-#7). Taken from the unused tail of `kraken_ui_state`, because `kraken_ui2_state` is full |
| `kraken_aprs_inbox` | `0x18007B00`-`0x18007C00` | 256 B | `kraken_ui2`/`kraken_ui4` | APRS messages inbox (F18, issue #6), RAM only, used through `KRK_APRS_INBOX_LO` |
| `kraken_ui2_state` | `0x18007C40`-`0x18007EB0` | 624 B | `kraken_ui2` | `.bss` of the extension blob |

Unclaimed in the window: `0x180053A0`-`0x18005600`, `0x18007600`-`0x18007640`, `0x18007C00`-`0x18007C40`, `0x18007EB0`-`0x18007FE0`.
`0x18007FE0`-`0x18008000` belongs to dm32uv-re (`overlay_guard`, `ramdump_cursor`).

## Excluded claims

`image.json` `exclude_claims` lists claims of other files that this image provably never uses, with the
reason for each. The build passes the builder filtered copies without them.

- `ramdump_probe_code` (flash `0x030E1000`-`0x030E2000`, dm32uv-re): the live-RAM-capture probe is not in
  this image; stock has 0xFF there and nothing calls it. `kraken_ui5_code` takes the sector. Its SRAM claim
  `ramdump_cursor` stays reserved.
- `snake_code` (flash `0x030E8000`-`0x030EA000`, dm32uv-re): the Snake example edits the same menu sites as
  `kraken_ui`, so it cannot be in this image. `kraken_ui4_code` starts there.
- `snake_state` (SRAM, dm32uv-re): Snake's state, unused here. `kraken_ui4_state` takes its range.
- `mi_rng_tap_buf` (SRAM `0x18005000`-`0x18007000`, `patches/mi_rng/allocations.json`): the mi_rng
  measurement build only (`mi_rng_tap.patch.json`). The production `mi_rng` never touches it, so Kraken's
  SRAM claims reuse the range.

## Checking a new claim

dm32uv-re's `placement_scan.py` checks a range for literal references and claim overlaps:

```sh
tools/build.sh --run 'python3 -I /w/dm32/dm32uv-re/tools/src/dm32_patch_tools/placement_scan.py \
    --extra-allocations allocations.json --extra-allocations patches/mi_rng/allocations.json \
    --lo 0x030f7000 --hi 0x030f8000'
```

Before writing code to a sector, check it is 0xFF in the image it lands on; the builder refuses a blob that
lands on non-erased flash.

## Flagged sectors re-checked (2026-10-29)

Static check only (read-only; stock `progmem_stock_DP570UV_20260926_FULL.bin`, `cs_disasm.py` capstone-csky plus the corpus). Result: every flagged literal is two adjacent halfwords of a 32-bit C-SKY instruction (or bitmap bytes) read as a 32-bit word. None is an `lrw` literal-pool slot, and none is used as an address. The upper halfword `0x030X` is the instruction's second halfword (an imm field such as a `st.b` offset `0x30e`, a `br`/`bf` displacement, or a `movi32` immediate `0x30f`), and the low halfword is the first halfword of the next opcode.

| Literal | Site | Instruction context | Verdict | Confidence |
|---|---|---|---|---|
| 0x030EDC64 | 0x03032ED4 (aligned) | `st32.b a3,(l0,0x30e)` = bytes `64 dc 0e 03`; run of `st32.b` (0x306..0x319) writing ASCII digits in a codeplug-defaults routine | coincidental | HIGH |
| 0x030EDC64 | 0x03033140 | `st32.b a3,(l0,0x30e)`, same pattern (`64dc 0e03`) | coincidental | HIGH |
| 0x030FDC24 | 0x03032EDA (2 mod 4) | `st32.b a1,(l0,0x30f)` (`24dc 0f03`), preceded by `movi16 a1,0x4e` | coincidental | HIGH |
| 0x030FDC64 | 0x03033146 (2 mod 4) | `st32.b a3,(l0,0x30f)` (`64dc 0f03`) | coincidental | HIGH |
| 0x030F809F | 0x03082462 (2 mod 4) | inside the 1bpp bitmap blob 0x03077674-0x03082494 (`... 06 01 9f 00 07 03 9f 80 0f 03 18 c0 ...`), not code, not word aligned | coincidental | HIGH |
| 0x030FE800 | 0x03022776 | `br32 0x3022d94` (`00e8 0f03`) in `tone_ptt_preamble_seq37`'s switch | coincidental (a branch displacement, not a pointer) | HIGH |
| 0x030FE840 | 0x030CAD02 / IRAM 0x1B0E2 | `bf32 0x30cb320` (`40e8 0f03`) after `cmpnei16 a3,3`. IRAM 0x1B0E2 is the same bytes (flash 0x030BFC20 + 0xB0E2 = 0x030CAD02) | coincidental | HIGH |
| 0x030FE860 | 0x0303D254 | `bt32 0x303d872` (`60e8 0f03`) after `cmpnei16 a3,2` | coincidental | HIGH |
| 0x030FE902 | 0x03028D88 (aligned) | `bez32 a2,0x30293a6` (`02e9 0f03`) after `andi32 a2,a2,8` | coincidental | HIGH |
| 0x030FEA01 | 0x03061F5E (2 mod 4) | `movi32 a1,0x30f` (`01ea 0f03`) in `settings_block_cache_load_from_flash`, arg to `flash_field_read_cached` (0x0300f780) = a field/block id (0x30f), not an address | coincidental | HIGH |

Other 0x030E0000-0x030FFFFF literals found by a raw whole-image scan (flash and IRAM, every byte offset) are all unaligned or inside non-pointer data. Examples are 0x030E0F03 at 0x030773FC, which is bytes in the 8x16 ASCII font blob (the earlier open item), and a handful of `st.b`/`bf` immediates elsewhere. No aligned word in that range is an `lrw` target anywhere, in flash or IRAM.

Other routes into 0x030E0000-0x03100000 at runtime:
- Pool constants: none. No `lrw` pool value (18129 `lrw` in the corpus, plus the raw scan) falls in 0x030C0000-0x03100000, and no `movih` builds a 0x030E/0x030F base. The pool values 0x03000000 appear only inside data blobs, with no `lrw` pointing at them.
- Erase: `flash_erase_firmware_region_locked` (0x03004040) erases 0x0300C000 up to 0x03100000 sector by sector with `flash_sector_erase_range_4kb`. Its only caller is `xmodem_ymodem_block_receive_loop` (0x03004618), so this is the stock YMODEM firmware-upgrade path. `ymodem_flash_block_program` and `flash_region_write_chunked` then write the new image linearly. This is not an OTA staging area; it just wipes the whole app region (including every sector in this document) before a full reflash. That applies to every claimed sector, flagged or not.
- Other flash routines are for the external SPI NOR (`spi_flash_*`, `flash_block_*`, `flash_field_read_cached`) and take block ids or SPI addresses, not XIP addresses.

Limit: this is static. A computed address built from non-literal data (for example a base read from SRAM) would not show up. Nothing in the scan suggests one reaches 0x030E0000+, but it is not proven by a runtime trace.

Per-sector verdicts:

| Sector | Verdict | Confidence |
|---|---|---|
| 0x030ED000 | no real reference; safe to claim | HIGH |
| 0x030FD000 | no real reference; safe to claim | HIGH |
| 0x030F8000 | no real reference; safe to claim | HIGH |
| 0x030FE000 | no real reference (all 5 literals are instruction halfwords); safe to claim | HIGH |

Nothing here changes the 0x030E0000 warning. The `0x030E0F03` literal is font data, so that sector is no longer a reference-flagged one, but it remains "partially erased" per the other notes and was not part of this check.

