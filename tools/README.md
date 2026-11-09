# Tools

Build:

- `build.sh` - builds the image (and by default runs the QEMU tests) inside the `dm32uv-re` Docker image.
- `build_image.py` - the build itself: applies the patches in `image.json` in order, stages the sectors, writes the update files, runs the tests. Called by `build.sh`.
- `bmp_filtered.py` - runs `dm32uv-re`'s patch builder with some of its claims left out (`image.json` `exclude_claims`). Called by `build_image.py`.
- `check_bsr.py` - fails the build if assembly branches to an absolute address (C-SKY gas emits nothing for it).
- `gen_menu_hosts.py` - generates the stock-menu row boilerplate from `patches/kraken_ui/menu_hosts.json`.
- `gen_manifest.py` - generates the branch hooks and blob list of `kraken_ui.patch.json` from `hooks.json` and `blobs.json`.
- `gen_claims.py` - generates `krk_claims.h` and the `KRK_EXT*` part of `krk.h` from `allocations.json`.
  The build runs all three generators with `--check`. Run them without it after you edit their inputs.
- `sat_tle_gen.py` - turns `data/tles.txt` into `patches/kraken_ui/krk_sat_tles.c`. Run by hand when the TLEs change.
- `make_official_update.py` - writes and checks the universal update file for the radio's own SK1+PTT updater.
- `build_reader.py`, `make_web_patch.py` - build the web installer's reader program and its patch file and site in `out/kraken/web/` ([docs/web-flasher.md](../docs/web-flasher.md)). Called by `build_image.py`.
- `check_web_site.mjs` - checks an assembled web-installer site before it is published (`node tools/check_web_site.mjs out/kraken/web`); the Pages workflow runs it.
- `pin_web_release.py` - pins an assembled site to its release (tag and file hashes in `index.html`); the Pages workflow runs it.
- `setup_pages.sh` - one-time GitHub Pages setup for the web installer, once the repository is public.

Repository:

- `check_repo_hygiene.py` - fails if firmware, dumps, backups, logs, large or unexpected binary files,
  images with EXIF/GPS metadata, real serial port names, or anything under `private/` or `out/` would be
  committed. Run it before you commit. With `--range BASE..HEAD` it checks every commit in a range, as CI
  does. Set `KRAKEN_DENYLIST` (one string per line: your callsign, DMR ID, serials) to also check for
  your own details.

Emulator:

- `qemu_ui_run.sh` - boots the built image in QEMU, plays a key sequence, renders the frames to a PNG.
- `qemu_poke.py` - writes emulator memory at set times during a `qemu_ui_run.sh` run (`POKE=<file>`).
- `nmea_feed.py` - feeds a synthetic GPS fix to the emulator's GPS UART (`NMEA=1`).

Audio and DSP (host Python, used by the host tests):

- `pcmio.py` - reads and writes 16-bit mono PCM (WAV or raw s16le).
- `pcm_capture.py` - records the RX audio stream the APRS RX screen sends over the programming cable into a WAV (`--port PORT`).
- `afsk_host.py` - builds the AFSK decoder with the host cc and runs it over PCM files.
- `afsk_channel.py` - a fitted model of the DM-32UV receive audio chain, for stress-testing the AFSK decoder.
- `afsk_offline.py` - level, spectrum and decoder report for a recorded APRS WAV.
- `bv_sounding_analysis.py` - analyses a recording of the Burst Voice sounding probe (channel response, SNR, mode advice).

Analysis:

- `mi_rng_entropy.py` - estimates the min-entropy of the `mi_rng` noise source from a RAM capture of its measurement build.
