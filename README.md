# DM-32UV Kraken

Kraken is add-on firmware for the Baofeng DM-32UV handheld radio. It keeps the stock firmware
(`DM32.00.01.048`) and everything it does, and adds features found on pricier radios.

## Features

- **Analog APRS:** a 1200 baud position beacon, a list of stations heard, background decoding, and a fix
  for the bearings stock shows for DMR APRS positions.
- **Receive tools:** RSSI meter in dBm and S-units, band scope, DMR last-heard list, DMR link quality,
  Maidenhead grid from GPS.
- **Satellites:** pass prediction and Doppler tracking for FM amateur satellites.
- **Parrot and Burst Voice:** record a DMR call and replay it, or send it as a fast data burst on analog.
- **VOX that works** (stock shows VOX in the menu but never implemented it), and periodic CW ID.
- **Radio settings:** finer TX power steps, noise-based squelch, wider frequency range (behind a warning).
- **DMR privacy:** a 16-bit scrambler, and real random numbers for stock DMR encryption.

New rows sit in the stock menus where you would look for them. Tools that fit nowhere else are in a new
**KRAKEN** menu. The full list, with where each feature is, is in the [user guide](docs/user-guide/README.md).
How well tested each feature is: [docs/feature-status.md](docs/feature-status.md).

## I just want it on my radio

The quickest way is the **[web installer](https://awkaplan.github.io/dm32uv-kraken/)**: open it in Chrome or
Edge, plug in the programming cable, and click Install Kraken. It backs up your radio first. It is
experimental: installing with it has not yet been tested on a radio. If it fails, click **Download
diagnostics** at the bottom of the page and attach the file to a
[web installer problem report](https://github.com/awkaplan/dm32uv-kraken/issues/new?template=web_installer.yml).

To install by hand instead:

1. Download `kraken_update_BFUV32-V2.bin` from
   [Releases](https://github.com/awkaplan/dm32uv-kraken/releases).
2. Follow [Installing Kraken](docs/user-guide/install.md). It covers the backup, the install with the
   radio's own updater, and going back to stock.
3. Read the [user guide](docs/user-guide/README.md) for what's new and where to find it.

Only for radios that show firmware `DM32.00.01.048`. You need the USB programming cable and a computer
with Python.

## Legal and safety

- **At your own risk.** Kraken is unofficial. It is not made, endorsed or supported by Baofeng.
  Installing it may void your warranty. The software comes with no warranty (see [LICENSE.md](LICENSE.md)).
- **You are responsible for what you transmit.** Some features can transmit in ways your licence may not
  allow: wider frequency range, scrambling, data bursts, automatic beacons and IDs. Each user guide page
  says when the radio transmits and what the rules are. Transmit features are off until you turn them on.
- The install never touches the radio's calibration, its codeplug or its per-unit data. You can always
  go back to stock.

## For developers

### How it works

- The stock firmware stays in place. Each feature is a small **hook** in stock code plus new code in
  **erased program flash**, which stock does not use.
- New code is C, built for the radio's C-SKY CK803S CPU. It calls stock display, menu, RTOS and radio
  functions at their known addresses.
- New menu rows reuse the stock menu engine, option lists and popups, so they look and behave like stock.
- The build produces one patched image and one update file, `kraken_update_BFUV32-V2.bin`, in
  Baofeng's official `BFUV32-V2` format. The radio's own bootloader installs it. The updater rewrites
  only the main firmware area, so one file fits every radio on `DM32.00.01.048`.
- Every build boots the patched image in QEMU and runs the tests.

The stock firmware, its reverse-engineering corpus, the patch tools and the Docker build image are in
[dm32uv-re](https://github.com/awkaplan/dm32uv-re). This repository holds only Kraken's own code.

### Quick start

```sh
git clone --recurse-submodules https://github.com/awkaplan/dm32uv-re
git clone https://github.com/awkaplan/dm32uv-kraken
(cd dm32uv-re/docker && docker compose build)   # builds dm32uv-re-pipeline:latest
cd dm32uv-kraken
tools/build.sh                                   # build + all tests
```

The update file is written to `out/kraken/kraken_update_BFUV32-V2.bin`. Details, options and the other
outputs: [docs/building.md](docs/building.md).

### Repository layout

| Path | What |
|---|---|
| `patches/kraken_ui/` | Kraken's code: the KRAKEN menu, the screens, the hooks, and the rows added to stock menus. Split into several blobs in erased flash (`blobs.json`, `kraken_ui*.ld`) |
| `patches/mi_rng/` | The random number fix for stock DMR encryption (F0a) |
| `web/`, `reader/` | The web installer: a static page that backs up the radio, patches in the browser and flashes over Web Serial, and the small reader program it loads into the radio's RAM. See [docs/web-flasher.md](docs/web-flasher.md) |
| `image.json` | Which patch manifests make up the image, in order, and their tests |
| `allocations.json` | The flash and SRAM each patch claims. Survey: [docs/flash-allocation.md](docs/flash-allocation.md) |
| `data/` | Build-time data: satellite orbits and frequencies (`tles.txt`) |
| `tools/` | Build, update-file and QEMU tools. `tools/build.sh` is the entry point. See [tools/README.md](tools/README.md) |
| `tests/` | Host tests of the pure code and in-target tests under QEMU. See [tests/README.md](tests/README.md) |
| `docs/` | Developer notes: how each feature hooks into stock, and the research behind it. Index: [docs/README.md](docs/README.md) |
| `docs/user-guide/` | The user guide for radio owners |

### More

- [docs/README.md](docs/README.md): index of the developer docs
- [docs/feature-ideas.md](docs/feature-ideas.md): ideas not built, and why
- [CONTRIBUTING.md](CONTRIBUTING.md): how to propose changes, test, and the safety rules for patches
- [CREDITS.md](CREDITS.md): prior art and related projects
- [LICENSE.md](LICENSE.md): PolyForm Noncommercial 1.0.0

### Related projects

- [dm32uv-re](https://github.com/awkaplan/dm32uv-re): reverse-engineering corpus, patch kit and build image
- [dm32uv-qemu](https://github.com/awkaplan/dm32uv-qemu): QEMU model of the DM-32UV
- [csky-qemu](https://github.com/awkaplan/csky-qemu): C-SKY CPU for QEMU
- [ghidra_csky](https://github.com/awkaplan/ghidra_csky): C-SKY support for Ghidra
- [Roger Clark's DM32 tools](https://github.com/rogerclarkmelbourne/DM32): the backup reader and firmware
  loader used to install Kraken

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You may use, change and share Kraken for any noncommercial
purpose. Baofeng's firmware is not covered; see the license file for what it does not cover.
