# Building Kraken

How to build the Kraken image and its update file from source, run the tests, and add a feature.
To put the result on a radio, see [flashing.md](flashing.md) (developers) or
[user-guide/install.md](user-guide/install.md) (everyone else).

## 1. What you need

- **Docker.** The compiler, the QEMU emulator and the patch tools all run inside one Docker image. On
  macOS and Windows use Docker Desktop. The image is about 7 GB.
- **git.**
- **Python 3.11 or newer** on the host, for the host tests and the flashing tools. The flashing tools also
  need pyserial: `python3 -m pip install pyserial`, or run them with `uv run --with pyserial python3 ...`.
- **A C compiler** (`cc`) on the host, for the host tests only. Without one, run them in the container
  (section 5).

## 2. Get the sources

Kraken needs a checkout of [dm32uv-re](https://github.com/awkaplan/dm32uv-re). It holds the stock firmware
image Kraken patches, the stock symbol names, the patch builder and the Docker recipe. Clone both side by
side:

```sh
git clone --recurse-submodules https://github.com/awkaplan/dm32uv-re
git clone https://github.com/awkaplan/dm32uv-kraken
```

```
workspace/
  dm32uv-re/       stock firmware, corpus, patch tools, Docker recipe
  dm32uv-kraken/   this repository (any folder name works)
```

If `dm32uv-re` lives somewhere else, set `DM32UV_RE=/path/to/dm32uv-re`. Without it, `tools/build.sh` uses
`../dm32uv-re`, or `../dm32/dm32uv-re` if only that exists.

dm32uv-re is under the PolyForm Noncommercial license; read its `LICENSE.md`.

## 3. Build the Docker image (once)

```sh
cd dm32uv-re/docker
docker compose build
```

This tags `dm32uv-re-pipeline:latest`, which `tools/build.sh` uses. The first build takes a long time:
it compiles the C-SKY GCC cross-compiler and QEMU from source. Later builds reuse the cached layers.
The submodules must be checked out (`--recurse-submodules` above, or
`git submodule update --init --recursive` in `dm32uv-re`). dm32uv-re's `docker/README.md` has the
details. Do not publish the image: dm32uv-re's license asks that QEMU builds with its board model are not
redistributed.

To use another tag, set `DM32UV_RE_IMAGE`, for example `DM32UV_RE_IMAGE=dm32uv-re-pipeline:mytag`.

## 4. Build

From the root of this repository:

```sh
tools/build.sh             # build, then run every QEMU test
tools/build.sh --no-test   # build only (seconds)
```

What it does (`tools/build_image.py`, run inside the container):

1. Checks that the generated files are up to date (`gen_menu_hosts.py`, `gen_manifest.py`,
   `gen_claims.py` with `--check`; see section 6).
2. Builds each patch in `image.json` in order with dm32uv-re's `build_manifest_patch.py`, each on top of
   the previous one's output: first `patches/mi_rng`, then `patches/kraken_ui`. The builder refuses the
   build if a hook site does not hold the expected stock bytes, if code would land on non-erased flash,
   if two claims overlap, or if any changed byte is not declared.
3. Checks that the sectors changed versus stock are exactly the ones the patches account for.
4. Writes the outputs below.
5. Unless `--no-test`, runs each patch's QEMU test against the combined image.

Inside the container this repo is always at `/w/dm32uv-unleashed` and dm32uv-re at `/w/dm32/dm32uv-re`,
whatever the folders are called on the host. `image.json` and the tests use those paths.

### Outputs (`out/kraken/`, gitignored)

| File | What |
|---|---|
| `kraken_update_BFUV32-V2.bin` (+ `.sha256`) | **The update file.** Baofeng's official `BFUV32-V2` format, for the radio's own updater. This is what you flash |
| `progmem_KRAKEN_patched.bin` (+ `.sha256`) | The full 1 MiB program-flash image (stock plus Kraken) |
| `kraken.bps` | The same change as a BPS patch against the stock image |
| `build.json` | Base and image hashes, and which patch owns each changed sector |
| `kraken_patch.json`, `kraken_reader.bin` | The web installer's two build files under their release names, with `.sha256`. A release carries them and the Pages workflow publishes the page with them ([web-flasher.md](web-flasher.md)) |
| `web/` | The web installer's site: `web/`, `reader.bin` and `kraken_patch.json` (Kraken's bytes only) ([web-flasher.md](web-flasher.md)). Serve it as static files |
| `reader/` | The web installer's reader program (`reader.bin`, its ELF, map and `reader.json`), built from `reader/` by `tools/build_reader.py` |
| `staged/` | Each changed 4 KiB sector, and `rollback/` with its stock bytes. Written by dm32uv-re's `stage_sectors.py`, which also refuses sensitive sectors. Not needed for the update-file flow |
| `steps/NN_<patch>/` | Per-patch builder output: the ELFs, link maps and summaries. The tests read symbols from here |
| `test_NN.log` | Each patch test's full output |

The update file is checked as it is written (`tools/make_official_update.py build`): the image must equal
stock below `0x0300B000`, fit the updater's size limit, and leave the loader's padding of the last block
unused; a simulated update must give the image back. To also compare its header with an official Baofeng
update file, set `DM32_OFFICIAL_SAMPLE=/path/to/official.bin`. Without it that one check is skipped.

### Reproducible output

A build from the same sources gives the same bytes. Compare `out/kraken/*.sha256` with a release's
checksums to confirm you built the same thing.

## 5. Tests

There are two kinds. `tests/README.md` lists them all.

- **Host tests** (`tests/test_*.py`) compile the patch C code with your own `cc` and check its logic. They
  take seconds:
  ```sh
  python3 tests/test_tags.py
  for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done
  ```
- **QEMU tests** run the real CK803S code of the built image in dm32uv-re's emulator, inside the
  container. `tools/build.sh` runs them: `tests/mi_rng_test.py` for `mi_rng` and `tests/kraken_ui_test.py`
  for `kraken_ui`. The latter calls patch code on a halted machine over the gdb stub, and also boots the
  image and walks the stock menus by key.

To run anything else in the same container, from the repo root:

```sh
tools/build.sh --run 'python3 tests/settings_e2e.py out/kraken/progmem_KRAKEN_patched.bin'
tools/build.sh --run 'python3 tests/test_ax25.py'      # a host test, if your cc does not work
tools/build.sh --shell                                 # interactive
```

Booted runs are timing-sensitive on a busy host. `DM32_TIMEOUT_SCALE=2 tools/build.sh` doubles every wait.

To see the screens, `tools/qemu_ui_run.sh <tag> <until-step> '<keyseq>'` boots the image, presses keys and
renders the frames to `out/qemu/<tag>/frames.png`.

## 6. Adding a feature

Kraken's code is `patches/kraken_ui/`. It is one patch manifest (`kraken_ui.patch.json`) with several
blobs of C in erased program flash, plus hooks: stock instructions replaced by a `bsr` into our code.
Read [menu-hosts.md](menu-hosts.md) first; it explains screens, rows and hosts.

1. **The code.** Write the feature as a `struct krk_screen` (a live screen, a one-shot action, or a
   setting with `.opt`) in a new `krk_<name>.c`. `krk.h` has the screen API. Stock functions are called by
   their names from dm32uv-re's `tools/link/dm32_stock_symbols.ld`.
2. **The blob.** Add the source to a blob in `blobs.json` (and its exports and imports if other blobs call
   it). Each blob has a linker script `kraken_ui*.ld` and a flash claim; `out/kraken/steps/*/build/*.map`
   shows how full each one is. Blobs link in manifest order and `@blob:symbol` binds only to earlier blobs.
3. **The menu row.** Append the row to its host in `menu_hosts.json` (the KRAKEN list itself is
   `krk_screens[]` in `krk_core.c` and `krk_ext_screens[]` in `krk_ext.c`).
4. **Hooks** into stock code, if any: one line per site in `hooks.json` with the address, the expected
   stock bytes or `bsr` target, and our target. Each hook is a reverse-engineering finding; put why it is
   safe in its `why` text.
5. **Memory.** New flash or SRAM goes in a claim in `allocations.json` (dm32uv-re's format). The builder
   checks claims against each other and against dm32uv-re's. [flash-allocation.md](flash-allocation.md)
   shows what is used.
6. **Regenerate** the manifest and headers, then build:
   ```sh
   python3 tools/gen_menu_hosts.py   # menu_hosts.json -> manifest rows, krk_menu_rows.h
   python3 tools/gen_manifest.py     # hooks.json, blobs.json -> manifest splices and blobs
   python3 tools/gen_claims.py       # allocations.json -> krk_claims.h
   tools/build.sh
   ```
   The build fails while any generated file is stale.
7. **Tests.** Add a host test for the logic (`tests/test_<name>.py`, with `hostcc.py` and `hostlib.py`)
   and QEMU checks for the hooks, called from `tests/kraken_ui_test.py`.

A new patch that is not part of `kraken_ui` gets its own folder under `patches/`, its own manifest and
claims file, and an entry in `image.json` (`patches`, and `allocation_files` for the claims). `patches/mi_rng/`
is a small example.

Every hook and stock address in Kraken is for `DM32.00.01.048` (the `DP570UV` program-flash image in
dm32uv-re). The builder checks the stock bytes at every site, so a different firmware fails to build
rather than producing a broken image.
