# Kraken: notes for working on this repo

Kraken is a set of patches to the stock firmware of the Baofeng DM-32UV (a DMR/analog handheld radio).
It adds features in erased program flash and hooks them into stock code with `bsr` splices. The output
is one update file in Baofeng's own format, installed with the radio's own updater, plus a web
installer that does the same from the browser.

Everything is written against stock firmware **`DM32.00.01.048`** (the reference build). Every address in code and
docs is for that build. Kraken also ships for `DM32.01.L01.048` (Baofeng's public download) and `DM32.01.01.049`
(radios sold from late 2025): the build translates every stock address through a checked map
(`firmware/<id>/addrmap.json`) and builds translated copies under `out/fw/<id>/`
([docs/firmware-builds.md](docs/firmware-builds.md)). The builder checks the stock bytes at every hook site of every
build, so a wrong address fails the build. It never produces a silently broken image.

This is radio-transmitter firmware. A bad patch can key the transmitter, damage a radio's calibration,
or leave it unusable until reflashed. Read the safety rules below before changing anything.

## Repo map

| Path | What |
|---|---|
| `patches/kraken_ui/` | Almost all of Kraken: `krk_*.c` features, `krk.h` (screen API), `krk_stock.h` (stock calls), `krk_stubs.S` |
| `patches/kraken_ui/menu_hosts.json` | Where each feature's menu row appears (single source; generated into the manifest and `krk_menu_rows.h`) |
| `patches/kraken_ui/hooks.json` | Every splice into stock code: address, expected stock bytes, target, and `why` |
| `patches/kraken_ui/link.json` | Kraken is ONE link: its sources (link order, per-file flags), gc roots, and SRAM placement rules |
| `patches/kraken_ui/kraken_ui.patch.json` | The patch manifest. Partly generated: never hand-edit generated entries |
| `patches/mi_rng/` | A small separate patch: a good example of a self-contained one |
| `allocations.json` | Fixed-address claims and `keep_free` ranges. The rest is placed and claimed per build by `tools/pack_kraken.py` |
| `image.json` | Which patches make up the image, in order, plus their claims and tests |
| `tools/` | Build driver, generators, update-file and web-patch tools ([tools/README.md](tools/README.md)) |
| `tests/` | Host tests (`test_*.py`), QEMU tests, web installer tests ([tests/README.md](tests/README.md)) |
| `web/`, `reader/` | The web installer page and the small read-only program it loads into the radio's RAM |
| `docs/` | Developer notes ([docs/README.md](docs/README.md) indexes them); `docs/user-guide/` is for radio owners |
| `firmware/` | The supported stock builds (`firmwares.json`), each one's generated address map and reviewed overrides. No firmware files |
| `out/` | Build output (gitignored): `out/kraken/` (048, `release/` = what a release carries, `updates/` = the update files, never published), `out/kraken-<id>/`, `out/fw/<id>/`, `out/stock/` |
| `private/` | The developer's own radio backups and dumps (gitignored). Never commit anything from it |

The stock firmware, the patch builder, the stock symbol table and the Docker build image live in the
separate [dm32uv-re](https://github.com/awkaplan/dm32uv-re) repo, checked out next to this one
(`../dm32uv-re`, or set `DM32UV_RE`). Stock functions are called by the names in its
`tools/link/dm32_stock_symbols.ld`.

## Commands

```sh
tools/build.sh                 # all three firmware builds in Docker, then every QEMU test on each (the PR gate)
tools/build.sh --no-test       # build only
tools/build.sh --fw l01        # one firmware build (048, l01, 049; repeatable)
tools/build.sh --run 'CMD'     # run CMD in the build container, from the repo root
python3 tools/gen_menu_hosts.py && python3 tools/gen_manifest.py && python3 tools/gen_claims.py
                               # regenerate after editing menu_hosts.json, hooks.json, link.json or allocations.json
                               # (each takes --check; the build fails while any output is stale)
python3 tools/fetch_stock.py && python3 tools/gen_fw_maps.py
                               # the L01/049 stock images (once), then the address maps: after any change that adds
                               # or moves a stock address (fails with what it cannot prove; docs/firmware-builds.md)
python3 tests/test_<name>.py   # one host test (seconds; your own cc)
for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done
python3 tools/check_repo_hygiene.py          # no firmware, dumps, logs, EXIF photos or private/ files about to be committed
node tests/web_flasher_test.mjs              # web installer flows against a simulated radio (after a build)
tools/build.sh --run 'python3 tests/reader_qemu_test.py'   # web installer's reader in QEMU
```

Setup (first time): [docs/building.md](docs/building.md). The Docker image is built once from dm32uv-re.

## Adding or changing a feature

**Every feature goes through the pack** (`tools/pack_kraken.py`, [docs/flash-allocation.md](docs/flash-allocation.md)):
its sources are listed in `link.json` and placed at build time. Never add a blob, a linker script or a hand-sized claim for
Kraken code or `.bss`, and never pick an address for it; a fixed claim is only for something reached by address, with a
`pinned` reason. `pack_kraken.py lint` (build step 0, `tests/test_pack_lint.py`) refuses anything else.

The full recipe is [docs/building.md section 6](docs/building.md#6-adding-a-feature). Read
[docs/menu-hosts.md](docs/menu-hosts.md) first; it explains screens, rows and hosts. In short:

1. **Code.** A new `patches/kraken_ui/krk_<name>.c` defining a `const struct krk_screen` (see `krk.h`):
   - a **live screen** (`.enter`, `.tick`): the runner owns Back, the keypad and the menu-exit timer;
   - a **one-shot action** (`.flags = KRK_SF_ONESHOT`): report the result with `krk_popup()`;
   - a **setting** (`.opt` -> `struct krk_opt`): you write only `get`/`set`. The stock option list or
     numeric editor does the rest ([docs/stock-option-level.md](docs/stock-option-level.md), cookbook in
     section 6).
2. **Link.** Add the source to `sources` in `link.json`. Any source can call any other; the pack places
   it. `out/kraken/pack/kraken_ui/report.txt` shows the flash and SRAM left. Manifest entries write Kraken
   symbols as `@kraken:symbol`.
3. **Menu row.** Add it to `menu_hosts.json`, either in the KRAKEN category or appended to a stock sub-list
   (APRS, Call Log, GNSS, Radio Settings, Channel Set). Host-specific notes: `docs/*-submenu.md`.
4. **Hooks** (only if the feature must change stock behaviour): one entry per site in `hooks.json`, with
   the expected stock bytes and a `why` that says what the stock code does there and why replacing it is
   safe. A hook must fall through to stock unchanged when the feature is off. To choose the site, run `tools/hook_probe.py ADDR` (read-only: the containing function, the disassembly, the expect bytes and each build's translation).
5. **Memory.** Ordinary code and `.bss` need nothing: the pack claims exactly what it uses. Only state that
   must sit at a fixed address gets a pinned claim in `allocations.json` (and an `sram_groups` rule in
   `link.json` if it must line up with other state). [docs/flash-allocation.md](docs/flash-allocation.md).
6. **Regenerate and build** (commands above), including `tools/gen_fw_maps.py`: every stock address must translate
   into L01 and 049. One it cannot prove needs a hand proof in `firmware/<id>/overrides.json`, or a hook placed so
   it fits every build ([docs/firmware-builds.md](docs/firmware-builds.md) section 4).
7. **Tests.** A host test for pure logic (`tests/test_<name>.py`, using `hostcc.py` and `hostlib.py`), and
   QEMU checks for anything that touches stock code or RAM, called from `tests/kraken_ui_test.py`.
8. **Docs.** Update the user guide page (a **Status** line and a **Where** line), the row in
   [docs/feature-status.md](docs/feature-status.md), and the feature's developer note in `docs/`.

A feature that is not part of `kraken_ui` gets its own `patches/<name>/` folder, manifest and claims
file, and entries in `image.json`.

Before designing something new, check [docs/feature-ideas.md](docs/feature-ideas.md): many ideas are
already known to be blocked by the hardware.

## Safety rules (not optional)

These are enforced by the build where possible. Never weaken a check to make a build pass.

- **Never write below `0x0300C000`.** The boot loader and the per-unit sector `0x0300B000` (binding
  record, TX-band model byte) differ per radio. The build refuses any image that changes them.
- **Never write the data chip's calibration.** The only data-chip writes allowed are the stock Radio
  Settings block write, on the user's OK, to fields in [docs/settings-field.md](docs/settings-field.md).
- **Never transmit by default.** Anything that can key the transmitter is off after install and needs
  an explicit user action. Transmit settings are RAM-only (off at power-on) unless there is a strong
  reason. Automatic transmitters (beacons, IDs, VOX) show a red standby tag
  ([docs/standby-tags.md](docs/standby-tags.md)) and respect stock's TX inhibit, busy-channel and menu gates.
- **One build per firmware, never crossed.** A radio gets only the update file named after the firmware it reports
  (Menu > Device Info); the updater does not check. Installers check the version string at the build's own address.
  Never weaken a translation check, or take a target's bytes as "expected" without `fwmap.site_equiv`.
- **Stay under the updater's size limit** (`0xF4002` bytes including the header). The build checks it.
- **Claim every byte.** `tools/pack_kraken.py` claims exactly what Kraken's link uses, inside the pool that
  `allocations.json` leaves free (`keep_free` ranges are off limits); a fixed address gets a pinned claim there.
  The build checks claims and hook sites for overlaps, and that the built bytes equal the master link.
- **Never store to `0x03xxxxxx` from patch code.** Program flash is execute-in-place SPI flash; a store
  there becomes an SPI flash command.
- **Don't commit firmware or radio data:** no stock images, `.bin` dumps, backups, calibration or
  codeplugs. Keep personal details out of code, tests, docs and commits: no callsigns, DMR IDs,
  locations, serial numbers or port names. Use `N0CALL`, `3125001`, and W1AW's position for examples.

## Working with a real radio

- Never key a transmitter or run anything that can transmit without the person's explicit go-ahead
  for that run. New transmit code goes to the emulator first, then a dummy load, never straight on the air.
- Hands-on tests: give the person the steps first and wait until they say they're ready, then cue each
  physical action (turn off, turn on, hold keys) at the moment it's needed.
- Reading is safe: the web installer's reader only reads flash. Writing goes only through the radio's
  updater, which never overwrites itself, so a failed install can always be redone.
- The updater is entered by holding SK1 + PTT while turning the radio on (green light).

## Conventions

- **Settings use the stock UI.** New settings use the stock option list or numeric editor through
  `struct krk_opt` and sit in the stock menus where an owner would look. Don't hand-draw settings
  pages. Stock popups for results (`krk_popup`).
- **Hooks are reverse-engineering findings.** Each needs a `why` in `hooks.json`, and usually a section in
  the feature's developer note (address, what stock does, what we change).
- **Generated files are generated.** `krk_menu_rows.h`, `krk_claims.h` and the generated parts of
  `kraken_ui.patch.json` come from the JSON specs; edit the spec and regenerate.
- **Feature IDs** (F1, F13, ...) from [docs/feature-status.md](docs/feature-status.md) are used in code
  comments, commit messages and notes.
- **Developer notes** start with a one-line status (static research, QEMU-verified, or tested on a
  radio) and give addresses for `DM32.00.01.048` (the reference; other builds are translated). **User guide pages** are for radio owners: plain
  steps, every term explained, and for transmit features, when the radio transmits and the legal limits.
- **Writing style:** short, plain sentences, no marketing language. UI text on the radio is ASCII.
- A user guide status may say **works on hardware** only after a test on a real radio.

## Things that are easy to get wrong

- Kraken's own addresses move from build to build (the pack places them). Tests and tools look Kraken
  symbols up in `out/kraken/steps/*_kraken_ui/build/kraken_ui.elf` (`tests/build_artifacts.py`, `tools/qemu_poke.py`
  names); never hard-code one. Stock addresses are fixed.
- Stock voice prompts can say digits only, no letters.
- The DM-32UV has no Bluetooth, although stock contains Bluetooth strings and menus (unwired data).
- Stock VOX is dead code: the setting exists but nothing reads a sensor. Kraken's VOX is its own engine
  (`krk_vox.c`).
- Booted QEMU end-to-end tests are timing-sensitive. A failure on a loaded machine is worth one re-run
  before you debug it. Halted-machine checks (`kraken_ui_test.py`'s first part) are deterministic.
- Key holds in the booted tests are counted in keypad scans, not milliseconds.
- In the web installer, `web/lib/flow.js` holds the flows and has no DOM, so the tests drive the same
  code; the page (`web/app.js`) only renders screens. Each screen has an `id` the tests use, so its
  wording can change freely. `web/` and `reader/` contain no third-party flashing code; keep it that way
  ([docs/web-flasher.md](docs/web-flasher.md)).
- On a Mac, Apple's driver for Prolific PL2303 cables can't set line rates the way browsers do; the page
  drives those cables over WebUSB (`web/lib/pl2303.js`). Don't remove that path.

## Pull requests

One feature or fix per PR. Say what changed, why, and how it was tested (host, QEMU, or which radio).
`tools/build.sh` must pass. More in [CONTRIBUTING.md](CONTRIBUTING.md).
