# Contributing to Kraken

Thanks for helping. Bug reports, hardware test reports, documentation fixes and new features are all
welcome.

Kraken patches the stock firmware of a radio transmitter. A bad patch can key the transmitter, damage
a radio's calibration, or make a radio unusable until it is reflashed. The safety rules below are
not optional.

## Ways to help

- **Report a web installer problem.** Open a "Web installer problem" issue and attach the diagnostics
  file the page saves (**Download diagnostics**, at the bottom of the page). The form requires it. How to
  get it: [docs/user-guide/install.md](docs/user-guide/install.md#when-the-web-installer-fails).
- **Report a bug.** Open a "Bug report" issue. Say which Kraken release you run, what you did, what you
  expected and what happened.
- **Report a hardware test.** Many features have only been checked in the emulator, or on one radio.
  Open a "Hardware test report" issue for anything you tried, good or bad. Features marked **emulator
  only** or **menu newer** in [docs/feature-status.md](docs/feature-status.md) are the most useful.
- **Fix the docs.** Small fixes can go straight to a pull request.
- **Build a feature.** For anything larger than a fix, open an issue first to discuss the idea. Check
  [docs/feature-ideas.md](docs/feature-ideas.md): many ideas are already known to be blocked by the
  hardware.

## Building and testing

Setup and build are in [docs/building.md](docs/building.md). In short: clone
[dm32uv-re](https://github.com/awkaplan/dm32uv-re) next to this repository, build its Docker image, then:

```sh
tools/build.sh             # build, stage, and run every test
tools/build.sh --no-test   # build and stage only
```

- `tools/build.sh` runs all tests against the combined image: host tests of the pure code, in-target
  tests that boot the image in QEMU, and the checks on the update file. A pull request must pass it.
- Host tests (`tests/test_*.py`) are quick. Run them while you work. The booted QEMU tests take longer.
  See [tests/README.md](tests/README.md).
- Add a test with every feature or fix: a host test for pure logic (parsers, encoders, maths), an
  in-target test for anything that touches stock code or RAM.
- GitHub Actions ([.github/workflows/checks.yml](.github/workflows/checks.yml)) runs what it can
  without the stock firmware on every pull request: the generators' `--check`, the host tests, a syntax
  check of the web installer, `tools/check_repo_hygiene.py` on every commit, and a check of the links
  and anchors in the docs. It does not build the image or run the QEMU tests, so a green CI run does not
  replace `tools/build.sh`.
- Tools are described in [tools/README.md](tools/README.md).
- Working with an AI coding assistant? [CLAUDE.md](CLAUDE.md) is a condensed guide to the layout, the feature
  recipe and the safety rules.

## Safety rules for patches

1. **Never write the per-unit sector `0x0300B000`.** It holds the radio's binding record and TX-band
   model byte, which differ on every radio. The build refuses images that change anything below
   `0x0300C000` (the boot loader and this sector).
2. **Never write the data chip's calibration.** Calibration lives on the separate data chip and is
   unique to each radio. Patch code must not write to it. The only data-chip writes allowed are the
   stock Radio Settings block write, on user `OK`, to fields documented in
   [docs/settings-field.md](docs/settings-field.md).
3. **Never transmit by default.** Anything that can key the transmitter must be off after install and
   need an explicit user action to turn on. Transmit-related settings should be RAM-only (off after
   power-on) unless there is a strong reason. Automatic transmitters (beacons, IDs, VOX) must show a
   red standby tag ([docs/standby-tags.md](docs/standby-tags.md)) and respect stock's TX inhibit, busy
   channel and menu gates.
4. **Keep the update file under the size limit.** The BFUV32-V2 updater refuses files larger than
   `0xF4002` bytes in total, header included. The build checks this. Do not work around the check.
5. **Put new code in erased flash, and claim it.** New code and data go in erased program flash, claimed
   in [allocations.json](allocations.json). The build checks that claims and hook sites do not overlap.
   The survey is in [docs/flash-allocation.md](docs/flash-allocation.md).
6. **Change stock behaviour only where the feature needs it.** Hooks should fall through to stock
   unchanged when the feature is off.
7. **Do not commit firmware images or radio backups.** No `.bin` dumps of a radio, no calibration data,
   no codeplugs. `private/` and `out/` are ignored by git for this reason.

Never test new transmit code on the air first. Use the emulator, then a dummy load.

## Pull requests

- One feature or fix per pull request.
- Say what changed, why, and how it was tested (host, QEMU, or on a radio, and which).
- Update the user guide page and [docs/feature-status.md](docs/feature-status.md) if a feature's
  behaviour, menu or status changes. A user guide status may say **works on hardware** only after a
  test on a real radio.
- Run `tools/build.sh` and confirm all tests pass.

## Documentation style

- Short, plain sentences. No marketing language.
- User guide pages are for radio owners, not developers: explain every step, and explain any technical
  term the first time you use it.
- Every user guide feature has a **Status** line and a **Where** line. Settings tables say whether each
  setting is kept at power-off.
- Every page that describes a transmit feature says when the radio will transmit, and what the legal
  limits are.
- Developer notes in `docs/` start with a one-line status (static research, QEMU-verified, or tested on
  a radio). Give addresses for stock `DM32.00.01.048`, the reference build; the L01 and 049 builds are translated
  from it (`docs/firmware-builds.md`). A change that adds or moves a stock address also commits the regenerated
  `firmware/*/addrmap.json` (`python3 tools/gen_fw_maps.py`).
- No personal details in docs, tests or commit messages: no callsigns, DMR IDs, locations, serial
  numbers or radio backups. Use example values such as `N0CALL`.

## License

By contributing, you agree that your contribution is licensed under the same terms as the rest of the
project: [PolyForm Noncommercial 1.0.0](LICENSE.md).
