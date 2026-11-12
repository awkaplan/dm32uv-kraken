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
  code over the gdb stub. It pulls in the feature checks: `dw_checks.py`, `mon_checks.py`, `alert_checks.py`, `msg_checks.py`,
  `optrow_checks.py`, `convert_checks.py`, `aprs_text_checks.py`, `aprs_chan_checks.py`, `f10_checks.py`, `f13_checks.py` and
  `parrot_qemu_test.py`. In a second thread it also
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

QEMU helpers: `build_artifacts.py` (where the build's ELF and image are, symbol lookup, the claims `tools/pack_kraken.py`
generated: `in_claims()`, `piece()`), `claims.py` (reads `allocations.json`'s pinned claims), `target.py` (calls into patch
code on a halted machine), `qemu_menu.py` (booted runs driven by key sequences). Kraken is one link and its addresses move
from build to build: read them from the ELF (`syms()`), never write one into a test.

Useful environment variables: `DM32_TIMEOUT_SCALE` multiplies the booted-run timeouts on a slow host.

### Saved machine states

Booted scenarios (`qemu_menu.Boot`) do not all boot the radio and walk the menus themselves. Most start with the same keys: `lead`
(wait for standby) and then one of the `PATHS` walks (`cs`, `rs`, `of`, `krk`, `aprs`). The first scenario that needs one of those
prefixes boots a radio, walks it, waits until the screen is steady and saves the machine (QMP `migrate` to a file). Every later
scenario restores that file (about 0.5 s instead of 5 to 15 s) and sends only the keys after the prefix: through the QOM property
`keyseq` of the machine (dm32uv-qemu `docs/key-injection.md`). `Boot.step()` and `Boot.nsteps` stay counted from the start of the
whole sequence, so a scenario is written as before.

- **Where:** `out/snapcache/<key>.mig` (about 18 MB each, `DM32_SNAP_DIR` moves it). The key is a hash of the four composed images, the
  QEMU binary, `qemu_run.py`, every `DM32_*` variable except the harness knobs, and the prefix's key steps with their gaps. A new
  build, binary or prefix makes a new file; files not used for 3 days are deleted. `rm -r out/snapcache` is always safe.
- **Parallel:** one lock per state (a directory made with `mkdir`: `flock` is not honoured on every bind mount). The first scenario saves it, the others wait and then restore the same file read-only.
  A deeper state (`of`) is made from a shallower one (`rs`), so no walk is done twice.
- **The walk is the oracle:** when a state is saved, a second QEMU restores it and its IRAM, SRAM, CPU registers and LCD frame must equal
  the saved machine's. If not, the state is marked `.bad` and every scenario boots cold.
- **Off switch:** `DM32_SNAPSHOT=0` boots every scenario from power-on, as before.
- **Fallback:** a QEMU binary without the machine's migration (the key-sequencer properties are the probe: no `keyseq-remaining` in
  the binary), a pending `DM32_PINSEQ`/`KNOB`/`PTT`/`KEY`/`KEYSEQ` (not saved), or any failure to save or restore turns saving off for
  the run (or boots that scenario cold) and the scenario passes the same way, slower. Each `Boot` prints which path it took
  (`boot <tag>: restored at key step 6 in 0.5 s` or `cold (<why>)`).
- **Cold scenarios:** `Boot(..., cold=True)`, or `run_scenarios(..., cold={names})`. Use it for anything about power-on state, first-boot
  behaviour, boot timing or the RTC (a restored machine's host-time clock has moved on since the save). `aprs_beacon_e2e.py` is cold
  (beacon timing at standby counts from power-on). Scenarios that pass a key sequence that does not begin with `lead` or a `PATHS`
  walk, or write it by hand without `keys()`, are cold by themselves. If unsure, make it cold.
- **Things that differ from a cold boot:** a breakpoint (`bp=`) is set after the restore, so hits during the prefix are not counted
  (no scenario depends on them); the guest has been running at standby a little longer before the first key.
- **Adding a scenario:** write `keys(PATHS["cs"] + " ok ...")` as before. To share a new walk, add it to `PATHS`.

### Key timing in booted scenarios

A key sequence (`qemu_menu.keys()`) is handed to QEMU up front. A plain gap, `ok:2500`, counts keypad scans (reads of the GPIO row
bank), not milliseconds and not guest time. The scans the guest gets per second depend on how busy the host is: the same 2500 gap
took 13 s of guest time on a quiet host and 28 s on a loaded one, so a scenario that needs "after the popups, before the menu exit
timer" fails under `OPTROW_PARALLEL=12`. Write a gap that means guest time or a guest state as a guarded gap. The next key is then
sent when the guest has got there:

- `ok:t450` waits 450 guest ticks (uC/OS-III `OS_TICKS`, 100 Hz) after the key went down.
- `ok:?name` waits until `conds["name"](boot)` is true. Pass `conds={...}` to `run_scenarios()` (or `keys()`); the predicate reads
  guest state, for example `lambda b: b.hits >= 3 and b.in_list(0x15)` (three popups drawn, back in the Channel Set list).
- `ok:?name:t60` needs both.

`Boot.wait_step()` releases a guarded gap by writing QEMU's key-sequence counter through `/proc/<pid>/mem` (the QEMU runs in the same
container; `qemu_menu._qemu_seq_reads` finds the symbol with `nm`). A condition that never comes true is released after the wait
timeout, so the scenario's own check reports the failure. Write the check as before: it still asserts what the scenario tests.
The default gap (a key with no `:g`) is a guarded `t150` (`DM32_TICK_GAP=<ticks>`, 0 = old scan counting). Explicit short scan gaps
(`ok:60`) stay as they are; they only separate two keys that go in back to back.

## Data (`data/`)

- `capture16_f1.pcm`, `capture16_f2.pcm`: two APRS frames one DM-32UV received from another over the air
  (raw s16le, 8 kHz). The frame text is fixed by the recording; do not edit it.
- `f13_burst.pcm`, `f13_golden.json`: the Burst Voice golden burst written by `test_f13.py`.
- `*_pokes.txt`: memory pokes for screenshots with `tools/qemu_ui_run.sh` (`POKE=...`, format in `tools/qemu_poke.py`).
