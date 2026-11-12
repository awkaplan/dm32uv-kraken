# Flash and SRAM allocation

Status: QEMU-verified on all three firmware builds (each build packs, and the builder's output equals the master link byte for
byte); the packed layout is not yet tested on a radio.

Where Kraken's code and state live, how they get there, and why those places are safe. Kraken is linked as **one
program** and placed per build by `tools/pack_kraken.py`. Only what must sit at a fixed address is written down by
hand: the pinned claims and the `keep_free` ranges in `allocations.json` (dm32uv-re's `data/allocations.json`
format). The builder refuses overlapping claims and any changed byte outside a declared one.

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

## How Kraken is placed

Until 2026-11-11 Kraken was five blobs, each linked on its own into a claim sized by hand, and a blob reached a later
one through fixed addresses (the screen table at `0x030EE000`, a hook table at `0x030E8020`, descriptors at blob
origins). Free space ended up stranded at the ends of claims while the next feature did not fit. Now one tool does
the placement at build time, for each firmware build, between the mi_rng step and the builder's `kraken_ui` step
(`tools/build_image.py`):

1. **Measure.** Compile every source of `patches/kraken_ui/link.json` (copied into the manifest's `pack` by
   `tools/gen_manifest.py`) and link them once with `--gc-sections`, one output section per file. That gives each
   file's flash piece (`.text`, `.rodata`, what is kept) and its SRAM piece (`.bss`). The gc roots are every
   `@kraken:symbol` the manifest names (hook targets, data words), the `keep` patterns and the files marked
   `"keep": true`.
2. **Pool.** The flash and SRAM windows, minus every claim of every allocation file the build loads (dm32uv-re's
   registry, `patches/mi_rng/allocations.json`, the pinned claims here), minus the `keep_free` ranges, minus any
   flash sector that is not erased in the image the step builds on.
3. **Pack.** Largest piece first, first fit, into the free runs in address order. A file is placed whole (its
   code, constants and `.bss` together), because the builder binds what one blob uses from another with `--defsym`,
   which can bind a global but never a `static`. libgcc members are pieces of their own.
4. **Master link.** Link the whole program at those addresses: `out/kraken/pack/kraken_ui/kraken.elf`. This is the
   program; everything after only reproduces it.
5. **Builder input.** One blob per contiguous flash run and compiler-flag set, each linked by the unchanged dm32uv-re
   builder at exactly the master addresses, with every symbol defined in another blob bound by `--defsym` to its
   master address (so blob order does not matter). `@kraken:symbol` in the manifest becomes `@<blob>:symbol` of the
   blob that defines it. The claims are generated for exactly what is used: per flash run, the sectors from its
   start to its last byte; per SRAM run, the bytes (`claims.json`, passed to the builder with `--extra-allocations`).
6. **Verify.** After the builder ran, every byte of every master section must equal the built image, and every
   symbol a blob defines must sit at its master address. `kraken_ui.elf` / `.map` (the master) are copied into
   `out/kraken/steps/*_kraken_ui/build/`, where the tests read symbols.

Outputs, per build, in `out/kraken/pack/kraken_ui/` (`out/kraken-<id>/pack/...` for L01 and 049): `report.txt` (used
and free per run, and the claims), `claims.json`, `plan.json` (every piece with its address), `kraken.elf`/`.map`, and
the manifest the builder built (`kraken_ui.patch.json`, with its `kraken_<n>.ld` scripts).

What it means when working on Kraken:

- A new source goes into `link.json`'s `sources`; nothing else about memory is needed for code and `.bss`.
- Kraken addresses move between builds. Never hard-code one: tests use `tests/build_artifacts.py` (`syms()`,
  `in_claims()`, `piece()`), poke files use symbol names (`tools/qemu_poke.py`).
- When the pool is full the build stops with `flash is full: <piece> (<n> B) fits in no free run (<m> B free in
  all ...)` (or the SRAM equivalent). The levers are the ones they always were: `"opt": "-Os"` on cold files in
  `link.json` (the VOX, Parrot, AFSK decoder, CW ID and voice-frame code stay `-O2`), removing dead code, or new space.
- An overlap between pieces is an error of the tool, except between `sram_groups` pinned in the same claim, which
  share it on purpose (below).

## Pinned claims (allocations.json)

Kept at fixed addresses because something reaches them by address, not by symbol (each claim's `pinned` field says what):

| Claim | Range | Size | Why it is pinned |
|---|---|---|---|
| `kraken_ui_data` | `0x030E7000`-`0x030E8000` | 4 KiB | Relocated menu label arrays and jump tables, the KRAKEN label: manifest `data` items at fixed addresses (`gen_menu_hosts.py`) |
| `kraken_bigbuf` | `0x18005A00`-`0x18007000` | 5.5 KiB | One buffer shared by F9 Parrot and F13 Burst Voice, one owner at a time (`.bss.big`); the mi_rng measurement build taps it |
| `kraken_aprs_inbox` | `0x18007B00`-`0x18007C00` | 256 B | APRS messages inbox (issue #6), RAM only, used through `KRK_APRS_INBOX_LO` |

`tools/gen_claims.py` turns these into `krk_claims.h` (`KRK_<NAME>_LO` / `_HI_EXCL`).

## keep_free

Ranges no Kraken build may take, though they are not claimed in a release image:

| Space | Range | Why |
|---|---|---|
| flash | `0x030E3000`-`0x030E4000` | kept free for the Burst Voice bench build (a release image leaves it erased) |
| flash | `0x030F7000`-`0x030F8000` | kept free for private debug builds layered on a Kraken image |
| SRAM | `0x180053A0`-`0x18005600` | kept free for the bench build's state and private debug builds |
| SRAM | `0x18007EB0`-`0x18007FE0` | kept free for private debug builds; the QEMU checks also use it as scratch (`SCRATCH_SRAM`) |

## SRAM placement rules (link.json sram_groups)

| Group | Placed | What |
|---|---|---|
| `big` | start of `kraken_bigbuf` | `krk_vframe.c` `.bss.big`: the shared F9/F13 buffer |
| `bvt` | last `0x200` bytes of `kraken_bigbuf` (`__bvt_start`/`__bvt_end`) | F13 framing, AMBE and screen state (`krk_bv_frame.c`, `krk_bv_sound.c`, `krk_ambe.c`, `krk_bvscr.c` `.bss.bvs`). Overlaps the tail of `big` on purpose; `krk_bvscr.c` checks the place and zeroes it at screen entry |
| `bvm` | anywhere, as one range (`__bvm_start`/`__bvm_end`) | F13 modem state (`krk_bv_modem.c`), zeroed as one range at screen entry |

Patch SRAM is never zeroed at power-up, wherever it is placed; all Kraken state is guarded by a magic or set up at
screen entry, so placement does not change behaviour.

## Current use (DM32.00.01.048, 2026-11-11)

From `report.txt`: 103,820 B of code and constants in 7 blobs over four flash runs (`0x030E1000`, `0x030E4000`-`0x030E7000`,
`0x030E8000`-`0x030F7000`, `0x030F8000`-`0x03100000`), 6,744 B free, most of it at the top of the last run. SRAM: 11,609 B of
state, 431 B free. Before the single link the same code left its free space stranded at the ends of five claims, and two
blobs carried libgcc's `__muldi3`. The report of every build has the current numbers.

## Excluded claims

`image.json` `exclude_claims` lists claims of other files that this image provably never uses, with the
reason for each. The build passes the builder (and the pack) filtered copies without them, so their ranges join the pool.

- `ramdump_probe_code` (flash `0x030E1000`-`0x030E2000`, dm32uv-re): the live-RAM-capture probe is not in
  this image; stock has 0xFF there and nothing calls it. Its SRAM claim `ramdump_cursor` stays reserved.
- `snake_code` (flash `0x030E8000`-`0x030EA000`, dm32uv-re): the Snake example edits the same menu sites as
  `kraken_ui`, so it cannot be in this image.
- `snake_state` (SRAM, dm32uv-re): Snake's state, unused here.
- `mi_rng_tap_buf` (SRAM `0x18005000`-`0x18007000`, `patches/mi_rng/allocations.json`): the mi_rng
  measurement build only (`mi_rng_tap.patch.json`). The production `mi_rng` never touches it.

## Checking a new pinned claim

dm32uv-re's `placement_scan.py` checks a range for literal references and claim overlaps:

```sh
tools/build.sh --run 'python3 -I /w/dm32/dm32uv-re/tools/src/dm32_patch_tools/placement_scan.py \
    --extra-allocations allocations.json --extra-allocations patches/mi_rng/allocations.json \
    --lo 0x030f7000 --hi 0x030f8000'
```

A pinned flash claim must be on erased flash in the image it lands on; the builder refuses a blob that lands on
non-erased flash, and the pack never uses a sector that is not erased.

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

