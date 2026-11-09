# Credits

Kraken builds on other people's work. This page lists what was used or learned, and from where. No code
from these projects is copied into this repository unless the entry says so.

## Related projects by the same author

| Project | What it is | How Kraken uses it |
|---|---|---|
| [dm32uv-re](https://github.com/awkaplan/dm32uv-re) | Reverse-engineering corpus of the stock DM-32UV firmware: decompiled code, notes, patch kit, Docker build image | The build takes the stock image, stock symbols, patch tools and the Docker image from here. Most addresses in Kraken were found with it |
| [dm32uv-qemu](https://github.com/awkaplan/dm32uv-qemu) | QEMU board model of the DM-32UV | Every build boots the patched image in it and runs the in-target tests |
| [csky-qemu](https://github.com/awkaplan/csky-qemu) | C-SKY CPU target for QEMU, ported from [c-sky/qemu](https://github.com/c-sky/qemu) by C-SKY Microsystems | The CPU under the board model |
| [ghidra_csky](https://github.com/awkaplan/ghidra_csky) | Fork of [leommxj/ghidra_csky](https://github.com/leommxj/ghidra_csky), C-SKY support for Ghidra | Disassembly and decompilation of the stock firmware |
| [csky-toolchain-build](https://github.com/awkaplan/csky-toolchain-build) | Fork of C-SKY's toolchain build scripts | The cross-compiler that builds Kraken's code |

## DM-32UV tools and prior work

| Project | Used or learned |
|---|---|
| [Roger Clark's DM32 tools](https://github.com/rogerclarkmelbourne/DM32) | The firmware loader (`DM32_firmware_loader.py`) and program-memory reader (`C7000_read_progmem.py`) used to install Kraken and back up radios. The repository has no license, so Kraken does not include or redistribute them. Users download them from Roger's repository. The web installer uses none of their code: its reader and both protocols are written from the chip's documentation and dm32uv-re's reverse engineering ([docs/web-flasher.md](docs/web-flasher.md)) |
| [OpenGD77](https://github.com/open-ham/OpenGD77) | Feature ideas (last heard, satellite screen, hotspot mode) and the AT1846S register comparison in [docs/at1846s-opengd77-analogy.md](docs/at1846s-opengd77-analogy.md) |
| [OpenRTX](https://github.com/OpenRTX/OpenRTX) | Its DM-32 port's pin map was a lead for the receive audio path. It was treated as a lead only and checked against the stock firmware and datasheets ([docs/rx-audio-sources-forums.md](docs/rx-audio-sources-forums.md)) |
| [RadioFoundry](https://github.com/RadioFoundry/RadioFoundry) | Feature comparison with a full replacement firmware ([docs/feature-ideas.md](docs/feature-ideas.md)) |
| infotex58 forum DM-32 threads | Owner reports on the hardware, pin maps, and the frequency ranges of published DM-32 range mods, used for Freq Unlock ([docs/freq-unlock.md](docs/freq-unlock.md)) |
| [egzumer/uv-k5-firmware-custom](https://github.com/egzumer/uv-k5-firmware-custom) | The UV-K5 spectrum analyzer as prior art for the band scope |

## Standards, algorithms and data

| Source | Used for |
|---|---|
| APRS Protocol Reference 1.0.1 (APRS Working Group) | Position and status packet formats of the analog beacon and the APRS RX list |
| AX.25 Link Access Protocol 2.2 | Frame format, address field, FCS (CRC-16-CCITT) |
| Bell 202 | 1200 baud AFSK, 1200 / 2200 Hz tones |
| Vallado, Crawford, Hujsak, Kelso, "Revisiting Spacetrack Report #3" (AIAA 2006-6753) | The SGP4 near-earth propagator in `krk_sat.c`, written from the paper for this project's soft-float maths |
| [CelesTrak](https://celestrak.org/) | Orbital elements (TLEs) in `data/tles.txt` |
| AMSAT satellite status pages | Satellite frequencies and tones in `data/tles.txt` |
| [Skyfield](https://rhodesmill.org/skyfield/) | Optional independent cross-check of pass predictions in `tests/test_sat.py` |
| NIST SP 800-90A | The CTR_DRBG construction of the MI random number fix (not a validated implementation). It uses the stock firmware's own AES routine |
| [mbelib](https://github.com/szechyjs/mbelib), [DSD](https://github.com/szechyjs/dsd), [MMDVM](https://github.com/g4klx/MMDVM) | Public AMBE+2 frame tables (interleave, scrambling, silence frame) in `krk_ambe.c`, cross-checked against each other in `tests/test_ambe.py` |
| [Dire Wolf](https://github.com/wb2osz/direwolf), multimon-ng | Independent off-air decoding of the analog APRS beacon |
| FD6818 and HR_C7000 datasheets (vendor) | Register maps for the RF chip and the DMR baseband chip |
| Linux `pl2303` USB-serial driver | Reference for the PL2303 vendor start-up sequence and divisor encoding. `web/lib/pl2303.js` sends the same values but is written for this project; no driver code is copied |

If you think something of yours is used here without credit, please open an issue.
