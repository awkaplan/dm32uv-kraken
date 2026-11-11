# Tests

There are two kinds of test. Host tests compile the patch C code with your own `cc` and check its logic.
QEMU tests run the real CK803S code of the built image in the `dm32uv-re` emulator.

Both need a clone of `dm32uv-re` next to this repo (`../dm32uv-re`), or its path in `DM32UV_RE`. See
[docs/building.md](../docs/building.md).

## Host tests (`test_*.py`)

Each one is a standalone script. Run it from the repo root:

```sh
python3 tests/test_ax25.py
for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

A script exits 1 if any hard check fails. Lines marked `info` are reported only and never fail.

- They need Python 3 and a C compiler. `hostcc.py` builds the C sources with `-I patches/kraken_ui` and the
  `dm32uv-re` patch API headers. On macOS it works around a broken SDK by picking a Command Line Tools SDK
  that links.
- `test_sat.py` needs the `sgp4` package. It makes a venv in `~/.cache/dm32uv-sat-venv` on first run.
- `test_bv.py` takes about half a minute; the rest take seconds.
- They also run inside the Docker image if your host `cc` does not work:
  `tools/build.sh --run 'python3 tests/test_ax25.py'`
- `test_f13.py --regen` rewrites the golden burst in `data/` that the QEMU test compares against.

The web installer has three: `web_diag_test.mjs` checks the diagnostics download (ZIP writer, recorder)
and needs no build: `node tests/web_diag_test.mjs`. `web_flasher_test.mjs` (Node 20 or newer) runs its flows against a simulated
radio (`web/mock_radio.mjs`): `node tests/web_flasher_test.mjs`. `reader_qemu_test.py` runs the reader
program in QEMU: `tools/build.sh --run 'python3 tests/reader_qemu_test.py'`. Those two need a build.
`reader_hw_test.py` runs the reader on a real radio over the cable (pyserial) and measures which fast line
rates work: `python3 tests/reader_hw_test.py --port /dev/cu.usbserial-XXXX`. It only reads. See
[docs/web-flasher.md](../docs/web-flasher.md).

Shared helpers: `hostcc.py` (compiler and paths), `hostlib.py` (PASS/FAIL bookkeeping), and the C drivers
`*_glue.c`, `test_grid.c`, `test_parrot.c`, `test_sat.c`.

## QEMU tests

`tools/build.sh` builds the image and then runs the test of every patch listed in `image.json`, inside the
Docker image, each against the combined image:

- `mi_rng_test.py` for the `mi_rng` patch.
- `kraken_ui_test.py` for the `kraken_ui` patch. It runs on a halted machine (no boot) and calls the patch
  code over the gdb stub. It pulls in the feature checks: `dw_checks.py`, `mon_checks.py`, `alert_checks.py`, `optrow_checks.py`,
  `convert_checks.py`, `aprs_text_checks.py`, `aprs_chan_checks.py`, `f10_checks.py`, `f13_checks.py` and `parrot_qemu_test.py`. In a second thread it also
  boots the image and walks the stock menu by key: `optrow_checks.boot_checks`, `chanset_rows_e2e.py`,
  `bvnest_e2e.py`, `alert_checks.boot_checks` and `aprs_beacon_e2e.py` (the analog automatic APRS beacon, issue #7).

`tools/build.sh --no-test` builds without testing.

Some booted end-to-end checks are not in the default run. Run them inside the Docker image after a build,
with the workspace mounted as `tools/build.sh` does:

```sh
python3 /w/dm32uv-unleashed/tests/settings_e2e.py     /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
python3 /w/dm32uv-unleashed/tests/otherfunc_e2e.py    /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
python3 /w/dm32uv-unleashed/tests/freq_unlock_e2e.py  /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
python3 /w/dm32uv-unleashed/tests/f13_checks.py
```

`parrot_qemu_test.py --window` also checks the baseband voice window. That needs a QEMU build whose DMR RAM
model keeps writes; the default image reads zeros there.

Every QEMU test also runs for the L01 and 049 builds, from the translated copy `out/fw/<id>/tests/` that
`tools/fw_translate.py` writes (stock addresses replaced, [docs/firmware-builds.md](../docs/firmware-builds.md));
`tools/build_image.py` then sets `DM32_FW` and `DM32_WS`. Write tests against 048 as before. A hex number that indexes
an image (`img[0x3D556:0x3D558]`) counts as a flash offset; build images through `build_artifacts.COMPOSE`.
`web_flasher_test.mjs` also installs the L01 and 049 builds on a simulated radio when their builds exist.

QEMU helpers: `build_artifacts.py` (where the build's ELFs and image are, symbol lookup), `claims.py`
(reads `allocations.json`), `target.py` (calls into patch code on a halted machine), `qemu_menu.py`
(booted runs driven by key sequences).

Useful environment variables: `DM32_TIMEOUT_SCALE` multiplies the booted-run timeouts on a slow host.

## Data (`data/`)

- `capture16_f1.pcm`, `capture16_f2.pcm`: two APRS frames one DM-32UV received from another over the air
  (raw s16le, 8 kHz). The frame text is fixed by the recording; do not edit it.
- `f13_burst.pcm`, `f13_golden.json`: the Burst Voice golden burst written by `test_f13.py`.
- `*_pokes.txt`: memory pokes for screenshots with `tools/qemu_ui_run.sh` (`POKE=...`, format in `tools/qemu_poke.py`).
