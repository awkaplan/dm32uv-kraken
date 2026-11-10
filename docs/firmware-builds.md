# Firmware builds: 048, L01 and 049

Status: static analysis and QEMU-verified on all three builds. Only DM32.00.01.048 has been tested on a radio.
DM32.01.L01.048 and DM32.01.01.049 have not run Kraken on a real radio yet.

Kraken runs on three stock firmware builds of the DM-32UV:

| Build | Where owners get it | Version string | Kraken builds it from |
|---|---|---|---|
| `DM32.00.01.048` (reference) | not published; read from a radio | `0x030ADAA0` | this repo's files |
| `DM32.01.L01.048` | Baofeng's public download | `0x030AB858` | translated copies |
| `DM32.01.01.049` | shipped on radios sold from late 2025 | `0x030ADA20` | translated copies |

The list is [`firmware/firmwares.json`](../firmware/firmwares.json). No firmware is committed. The L01 and 049 update
files are fetched from a commit-pinned M7OCM URL and checked against a pinned SHA-256 (`tools/fetch_stock.py`, run by
`tools/build.sh`).

## 1. How the builds relate

All three come from one source tree. Functions appear in the same order. Most are unchanged and only moved.

- **L01** is 048 without the voice recorder. The "Record List", "Rx/Tx Record" and "Record Full" strings, the recorder
  task and the recorder's state byte `0x3BD38` are gone. Code moved down by up to 0x24B8 bytes. Low RAM is unchanged up
  to `0x3BD34`; from `0x3BD3C` to the end of IRAM bss (`0x4A7B0`) every variable is 4 bytes lower. The top-level menu
  still has 10 categories. Record (6) keeps its label, but its case blocks in the menu dispatchers point at the shared
  tails, so selecting it does nothing.
- **049** is closer to 048. Code moved by at most a few hundred bytes. RAM is identical except two byte variables at
  `0x38E28`/`0x38E29`, which are one byte higher.
- In all three, the IRAM load image (0x2F098 bytes run at `0x00010000`) has the same layout, SRAM has the same
  layout, and Kraken's windows (flash `0x030E1000`-`0x030FFFFF`, SRAM `0x18004C00`-`0x18007FFF`) are unused. Each
  build's boot stub says where its IRAM and SRAM images are in flash.

The update files hold flash from `0x0300C000` up. A 1 MiB base image is the 048 image's boot region (boot header,
Baofeng's loader, the blanked per-unit sector) with the update body on top. The updater never writes below
`0x0300C000`, so that region only matters to the emulator.

## 2. One source, translated

Every source, spec and test is written against 048. For L01 and 049 the build makes copies with every stock address
replaced, and builds those with the same tools and checks:

```
firmware/<id>/addrmap.json    every stock address Kraken uses -> its address in <id>, and how that was proved (GENERATED)
firmware/<id>/overrides.json  the few translations proved by hand (reviewed; each has its evidence)
firmware/not_addresses.json   numbers in address ranges that are not addresses (none so far)
tools/gen_fw_maps.py          builds addrmap.json; --check in every build
tools/fw_translate.py         writes out/fw/<id>/: translated patches, manifests, tests, dm32uv-re files
tools/re_tool.py              runs a dm32uv-re tool with that build's symbols, policy files and QEMU-only patch sites
tools/fwmap.py                the matcher and its proofs; tools/csky_pcrel.py decodes PC-relative instructions
```

What counts as a stock address (`tools/fw_text.py`): a hex literal in program flash below `0x030E1000`, in RAM
`0x00010000`-`0x0005FFFF`, or in SRAM below `0x18004C00`. It may appear in C, assembly or linker code (comments
skipped; in `.S` also a `movih`/`ori` pair), a Python token (a subscript of an image, `img[0x3D556:0x3D558]`, is a
flash offset), or a JSON string in a manifest. A value missing from `addrmap.json` stops the build; it is never left
as the 048 address.

The manifests are translated splice by splice. The expected stock bytes for the target are taken from the target
image only after `fwmap.site_equiv` shows they are the same instructions, with every PC-relative operand reaching the
translated target. Replacement bytes that are PC-relative are re-encoded and must reach the same place. Asserts,
copied stock tables and their expected words, and `allow_occupied` ranges are translated as well. dm32uv-re's builder
then runs with all of its usual checks against the target's own stock image.

dm32uv-re's stock symbol file and `dm32_patch_api.h` are translated the same way. A symbol or header address that does
not translate is left out (link error) or replaced by an undefined name (compile error), never kept.

## 3. How an address is proved (`tools/fwmap.py`)

1. **Functions.** Each 048 function (dm32uv-re `meta/functions.json`, instruction boundaries from `meta/disasm.jsonl`)
   is searched for near its running offset, as a byte pattern with calls and literal pools masked. A found function is
   then checked whole. Every call must reach the mapped callee, every branch out of it the mapped place, and every
   literal the corresponding value. Result: `same`, `moved` (callees not found, so only their shape is known) or
   `changed`.
2. **Evidence.** The literal pools of matched functions pair 048 values with target values. A pair seen everywhere
   with one partner is evidence for data and variables.
3. **Translate.**
   - Code in a matched function moves with the function, after a check of 48 bytes either side.
   - Code in a changed function, or code no function covers (switch case blocks, shared tails), needs a unique place
     near the expected offset where the code around it is the same in shape: same opcodes and registers, equivalent
     literals, mapped callees. These are marked `CHANGED` in `addrmap.json`.
   - A literal word goes where its translated readers read.
   - Flash data needs evidence at the address, or the offset of its neighbours with the same content. Pointers are
     followed two levels deep, so a table of label pointers matches when the strings do.
   - RAM needs evidence at the address, or evidence on both sides agreeing on one offset.
   - IRAM code stays where it is, with its bytes checked.

`python3 tools/fwmap.py l01 0x0303d872` prints one translation and its reason.

## 4. When a translation cannot be proved

`tools/gen_fw_maps.py` lists the address, why fwmap gave up, and where it is used. Prove it by hand: compare both
images side by side (the capstone disassembler in the Docker image, `dm32uv-re/tools/src/dm32_patch_tools/cs_disasm.py
--bin <image> --addr <hex>`). Then add it to `firmware/<id>/overrides.json` with the evidence. The L01 overrides are
the model:

- the RX state block, which L01 reads through the same base and offsets;
- the Channel Set blank-title blocks, where a `br32` became a `br16`;
- the OK handler's default arm, where L01 removed the Record arm and the epilogue differs;
- the Record and Roaming jump-table entries.

When a hook cannot sit at the same place in another build, change the hook so that one site fits every build, rather
than keeping a separate spec. `menu_append_hook` covers `st16.h; br16` instead of `br16; bkpt`, because L01 has no
padding halfword after the `br16`. Two hooks reach a stock default arm that L01 also takes for Record (id 6), so
`krk_opt_ok` and `krk_opt_title` now turn that category away.

## 5. Builds, tests and releases

`tools/build.sh` builds and tests all three, one after another. `--fw l01` (repeatable) picks builds.

| Build | Output |
|---|---|
| 048 | `out/kraken/` (as before) |
| L01, 049 | `out/kraken-l01/`, `out/kraken-049/` |
| release | `out/kraken/release/`: `kraken_patch.json` (one entry per build) and `kraken_reader.bin`, what a release carries |
| update files | `out/kraken/updates/kraken_<version>_update_BFUV32-V2.bin` per build, for installs by hand; never published (they contain the stock firmware) |

The QEMU tests run against each build from their translated copies (`out/fw/<id>/tests/`). `build_image.py` sets
`DM32_FW` and `DM32_WS`; `tests/build_artifacts.py` then composes QEMU images through `re_tool.py`, which applies that
build's ALPU, auto-power-off and hardware-binding emulator patches. The emulator boots the 048 data-chip factory image
on every build.

The web installer reads the radio's version string at every build's place and uses that build's entry
([web-flasher.md](web-flasher.md)). A backup must be of the build the radio runs. For a build with `radio_tested`
false in `firmwares.json` (L01 and 049 until someone installs them on a radio), the page warns that Kraken is
unstable there and asks the person to accept the risk before it flashes. Set it to true once a radio test passes.

## 6. Adding to Kraken

Write against 048 as before. After a change that adds or moves a stock address, run:

```sh
python3 tools/fetch_stock.py          # once: the L01 and 049 base images, under out/stock/
python3 tools/gen_fw_maps.py          # regenerate firmware/*/addrmap.json; fails with the addresses it cannot prove
```

Commit `addrmap.json` with the change. `gen_fw_maps.py --check` fails the build while it is stale. CI cannot run it,
because it needs the stock images.

## 7. Not verified

- No L01 or 049 radio has run Kraken. Hardware status per build is in [feature-status.md](feature-status.md).
- The boot region below `0x0300C000` of an L01 or 049 radio has not been read. The emulator uses 048's, and the
  updater never writes it.
- L01 reportedly holds up to 150K contacts, so its data-chip layout may differ from 048's. Kraken's data-chip records
  (Radio Settings block, `0xFC0`-`0xFEF`) have not been checked against an L01 data chip.
- Translations marked `CHANGED` or `override` (29 hook sites for L01, 13 for 049) depend on the code around the site
  being the same in shape. The QEMU tests exercise most of them; a radio test is still the final check.
