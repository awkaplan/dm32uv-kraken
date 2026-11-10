# Web installer

Status: built 2026-11-08. Tested end to end against a simulated radio (Node and Chrome), and the reader
program in QEMU. On a radio, the reader has run (one radio, one cable, macOS; section 5). **Install and
restore have not yet been run on a radio.**

A static web page that installs Kraken over the programming cable, from Chrome or Edge, with no Python and
no download of a Kraken update file. It reads the stock firmware off the user's own radio with our own
reader program, adds Kraken's bytes in the browser, and flashes the result with the radio's own updater.
Nothing Kraken publishes holds Baofeng's firmware, and nothing in it comes from third-party flashing
tools: the page, the reader and both protocols are written for this project from the chip's and the
bootloader's documented and reverse-engineered behaviour (section 3).

Source: [web/](../web/) (the page), [reader/](../reader/) (the reader). The build writes the whole site to
`out/kraken/web/` (section 4).

## 1. What the person does

One button, **Install Kraken**, then the page walks through it one screen at a time (a title, one or a few
short instructions, one button), with a four-part bar: Connect, Back up, Install, Done. The log is behind
**Show log**, and **Download diagnostics** in the footer saves everything the page knows as a zip at any
moment, also from the error screen (section 7). Each screen has an `id` (`off`, `on`, `update-mode`, `verify`, ...) that the tests use, so the
wording can change freely:

| Step | The person | The page | Time |
|---|---|---|---|
| Check | Picks the cable's port, turns the radio off, then on normally | Loads the reader through the boot ROM, raises the line to 1.5 Mbaud if the cable can, checks the boot header and the firmware version (one of the builds in the patch file: `DM32.00.01.048`, `DM32.01.L01.048`, `DM32.01.01.049`), and has the radio CRC all 256 sectors: a radio that is not stock, or already runs this build, is told so here | seconds |
| Back up | Waits | Reads all 1 MiB in 4 KB CRC-checked frames, then checks every sector against the radio's own CRC and the firmware area against stock's SHA-256 | ~10 s at 1.5 Mbaud, ~1.5 min at 115200 (Prolific cables) |
| Save | Turns the radio off, saves the file | Saves with the file picker and reads the saved file back to compare; without a picker it downloads and asks the person to confirm | |
| Install | Turns the radio on holding SK1 + PTT (green LED), clicks Install | Builds the update file, checks its hash against the build's, and sends it to Baofeng's updater | ~1.5 min (the updater runs at 115200) |
| Verify | Turns the radio off, clicks Check, turns it on normally | Has the radio CRC its flash and compares with what was sent. Can be skipped | seconds |

**Update or remove Kraken** takes a saved backup instead of reading the radio: **Update Kraken** (a radio already on
an older Kraken build), or put stock back from it. Both still connect first for the boot-header and version
checks and say whether the backup's per-unit sector matches this radio.

`kraken_patch.json` holds one entry per stock firmware build ([firmware-builds.md](firmware-builds.md)): its version
string and where it keeps it, the stock and Kraken sector CRCs, the update file's hash and Kraken's records. The page
reads the sectors of every build's version spot and uses the build whose own string is at its own address
(`0x030ADAA0` for 048, `0x030AB858` for L01, `0x030ADA20` for 049); a string elsewhere does not count. From then on
the flow works on that one build. For **Update or remove Kraken** the build is the backup's, and the radio must report
the same firmware.

Before it flashes Kraken onto a build that has not been tested on a real radio (`radio_tested` false in
`firmware/firmwares.json`, carried in the build's entry; `DM32.01.L01.048` and `DM32.01.01.049` for now), the page
shows one more screen, `untested-firmware`: Kraken on that firmware is unstable, tested only in an emulator, install at
your own risk. **Install at my own risk** goes on to the update-mode step; **Cancel** stops with nothing written (the
backup is already saved). The diagnostics record the answer. Putting stock back shows no such screen.

The page refuses, and writes nothing, when the radio reports a firmware no entry is for (it names what it found and
the builds it supports), when a backup is of another build than the radio, when the firmware area is not that build's
stock (it points to the backup option), when it already runs this build, or when a chosen backup is not a 1 MiB stock
image of a supported build. To name an unsupported build in the refusal the page looks through the version sectors
for any `DM32.xx.xx.nnn` string; the diagnostics record the string and where it was found
([issue 2](https://github.com/awkaplan/dm32uv-kraken/issues/2): L01 radios were refused before this; they are
supported now).

## 2. Pieces

| File | What |
|---|---|
| `web/index.html`, `style.css`, `app.js` | The page and the `ui` the flows talk to. Its Content-Security-Policy allows scripts and network access only to the site itself |
| `web/lib/flow.js` | The three flows (install, from backup, restore). No DOM, so the tests drive the same code |
| `web/lib/radio.js` | Boot ROM (load and start the reader, choose the line rate) and Baofeng's updater. Nothing else writes flash |
| `web/lib/kr.js` | Host side of the reader protocol |
| `web/lib/link.js` | Web Serial as an exact-length byte link with deadlines, reopened to change the baud rate |
| `web/lib/image.js` | Checks, applying the patch, the update file, the expected flash after an install |
| `web/lib/diag.js`, `web/lib/zip.js` | The diagnostics recorder (log, cable trace, facts, the files of the zip) and a small ZIP writer (section 7) |
| `web/diagnostics.js` | The page's side of the diagnostics: browser, OS, allowed cables, the screen, the download |
| `reader/reader.c`, `start.S`, `reader.ld` | The reader: about 1.5 KB of C-SKY code that runs from IRAM |
| `tools/build_reader.py` | Builds the reader with the C-SKY toolchain in the Docker image |
| `tools/make_web_patch.py` | `entry`: one build's checked entry; `site`: `kraken_patch.json` from all entries, `web/` and `reader.bin` beside it |

### The patch file

`kraken_patch.json` (`kraken-web-patch-2`) holds `name`, `reader` (load address, entry, size and SHA-256 of
`reader.bin`, shared by every build) and `builds`, one entry per stock firmware build, each with:

- `firmware` and `version_at`: the build's version string and its file offset (the page uses the entry whose string
  the radio has at that offset).
- `records`: the changed bytes as (offset, base64) runs, about 103 KB. A run never carries an unchanged stock
  byte that is not erased; the tool refuses to write the file otherwise. The only stock-derived content is
  what Kraken itself writes: about 650 bytes of copied stock menu pointer tables (extended with new rows) and
  hook instructions.
- `stock`: where the stock firmware area ends, its SHA-256, and the CRC-32 of each 4 KB sector from
  `0x0300C000` up.
- `target`: the same for the Kraken image. The installer pads the updater's last block with 0xFF, so after
  an install the radio holds exactly this image, and these CRCs check it.
- `update`: the update file's size and SHA-256.

The tool checks that the records applied to stock give the patched image exactly, and that header plus
firmware area is the build's update file byte for byte. The page checks the same hashes before flashing.

Why not `kraken.bps`: BPS checks a CRC of the whole 1 MiB source, including the per-unit sector at
`0x0300B000`, which differs on every radio.

## 3. Protocols

All three run on UART0, the programming cable, 8N1.

### Boot ROM (reading)

The HR_C7000's 2 KB mask ROM, described in dm32uv-re `specs/ksy/hdboot_serial.ksy` from the chip's
datasheet, Dahua's own chip tool and a disassembly of the ROM. At a normal power-on it sends NOTIFY
`02 24 00 03`; any well-formed frame keeps it in its command loop. Frames are `02 <cmd> <sum8 of body> 03
<body>`, big-endian. We send DEBUG `02 14 00 03`, then DOWNLOAD (`18`, address, word count, words; up to 64
words a frame) of `reader.bin` to IRAM `0x00010000`, each answered OK `02 05 00 03`, then RUN (`1B`, address)
at the reader's entry, answered OK.

In ROM mode the chip runs from its 24 MHz oscillator (UART0 divisor 13 = 115,385 baud), no watchdog runs,
the ROM uses no RAM, and the execute-in-place window at `0x03000000` reads without any setup.

### Kraken reader (reading)

Ours, in both directions: `5A A5 <type> <len u16> <payload> <CRC-32>`. Commands: PING, BAUD (switch divisor,
wait for a PING at the new rate, else go back), READ (streams 4 KB DATA frames; a new command stops the
stream), SUMS (CRC-32 of each piece of a range, computed on the radio). Full table in
[web/lib/kr.js](../web/lib/kr.js) and [reader/reader.c](../reader/reader.c).

- **Speed.** At 24 MHz the exact rates are 1.5M, 750k and 500k baud. The page tries them in that order and
  keeps the first that answers a PING, except on Prolific PL2303 cables, which cannot make them (section 5); the reader goes back to 115200 by itself after 1.5 s without one. The
  reader times that wait by sending idle 0xFF bytes at the new rate, so it needs no CPU clock.
- **Integrity.** Every frame has a CRC-32; a bad or missing frame is asked for again. The assembled backup is
  checked per sector against the radio's own CRCs, and its firmware area against stock's SHA-256.
- **Read-only.** The reader has no command that writes flash. It never stores to `0x03xxxxxx` (stores there
  become SPI flash commands).

### Baofeng's updater (flashing)

The bootloader at `0x03001000`, entered with SK1 + PTT, as reverse engineered in dm32uv-re: top loop
`0x0300455c`, command handler `0x030016ac`, YMODEM receiver `0x03004618`.

| Host | Radio | What |
|---|---|---|
| `R` | `06` | command handler |
| `M` + 4 bytes | `M 01 09`, then after our `06`: `BFUV32-V2` | model check |
| `E` + 4 bytes | `06` | back to the top loop |
| `1` | `C` | YMODEM receiver starts |
| `SOH`, then `00 FF` + 128 bytes (`name NUL size SPACE`) + CRC16 | `C` after SOH; `06 C` after erasing (~15 s) | size checked against the limit (0xF4002), firmware area erased |
| `STX`, then `n ~n` + 1024 bytes + CRC16 | `C` after STX; `06` | one block, n = 1, 2, … mod 256 |
| `EOT` | `C` | done; the radio restarts |

The receiver answers `C` to the first byte of every block, before the rest. A bad CRC or a size over the
limit gets `18 18`. The updater writes only from `0x0300C000` up, so the boot loader and the per-unit sector
are never written, and an interrupted install is redone the same way. The last block is padded with 0xFF.

## 4. Building, serving, testing

`tools/build.sh` builds the reader and writes the site to `out/kraken/web/`. Serve it as static files. Web
Serial needs a secure context: `https://` (GitHub Pages works) or `localhost`:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 -d out/kraken/web    # then open http://127.0.0.1:8000/
```

Two tests, both after a build, and one that needs none (CI runs it):

```sh
node tests/web_flasher_test.mjs                                   # the page's flows, ~40 s
tools/build.sh --run 'python3 tests/reader_qemu_test.py'          # the reader in QEMU, ~15 s
node tests/web_diag_test.mjs                                      # the diagnostics zip and recorder, ~1 s
```

- `web_flasher_test.mjs` runs the real flows against a simulated radio
  ([tests/web/mock_radio.mjs](../tests/web/mock_radio.mjs)) that plays the boot ROM, the reader protocol (baud
  changes included) and Baofeng's updater, with faults: corrupted, cut and subtly wrong frames, a cable chip
  limited to 1 Mbaud, fast rates that garble, the wrong firmware line, a modified or already-installed radio,
  a stock radio restored earlier, no update mode, a cable pulled mid-flash, a bad write caught afterwards, and
  both backup flows. It also checks that the update file matches the build's byte for byte, and runs three
  flows with the diagnostics recorder attached: the facts it collects, and that no 12-byte run of the stock
  firmware or the update file is anywhere in its trace.
- `web_diag_test.mjs` unpacks the ZIP writer's output with Python's `zipfile` (deflated and stored), and
  checks the recorder's rules one by one: received bytes kept only in raw mode, labelled blocks kept as
  length and CRC, DATA frames as their address, the trace's bounds, and the summary.
- `reader_qemu_test.py` runs `reader.bin` in QEMU's DM-32 model with UART0 as the ROM leaves it, and checks
  every command from an independent Python host: HELLO, PING, SUMS against zlib, a full 1 MiB READ against
  the image, a READ stopped by a new command, BAUD with and without the PING (the fill-byte timing to the
  byte), and the error replies. QEMU ignores the divisor, so line rates are not tested there.
- `reader_hw_test.py` is the same reader on a real radio, from Python with pyserial: it loads the reader
  through the boot ROM as the radio powers on, then for each rate (`--divisors 1,2,3` = 1.5M, 750k, 500k)
  switches, PINGs, reads all 1 MiB and compares every sector with the radio's own CRCs. `--save` keeps the
  first fully verified read. It covers checks 1 and 2 of section 5 for one cable and computer.

### Publishing (GitHub Pages)

The public installer is `https://awkaplan.github.io/dm32uv-kraken/`, deployed by
[.github/workflows/pages.yml](../.github/workflows/pages.yml) from a **release**, never from a branch: the
page (`web/`) as it is at the release's tag, plus two files the release carries. CI does not build those
itself (the build needs the stock firmware and the C-SKY toolchain image), so the live installer is always
exactly a released build.

1. Build (`tools/build.sh`, all three firmware builds). It writes the release's files to `out/kraken/release/`:
   `kraken_patch.json` (every build's entry) and `kraken_reader.bin` (`reader.bin` under its release name), each with a
   `.sha256`. They are also in `out/kraken/`. The update files in `out/kraken/updates/` contain the stock firmware and
   are never attached.
2. Check them as the workflow will: `node tools/check_web_site.mjs out/kraken/web`.
3. Tag and publish a release with both files attached, for example:
   ```sh
   gh release create v1.0 out/kraken/release/*
   ```
   Publishing the release runs the workflow. To redeploy (a page fix in an existing release's tag, or an
   older release), run it by hand: Actions > Web installer > Run workflow, with a tag or empty for the
   latest release.

The workflow copies `web/` and the two files into one folder, **pins** the page to the release
(`tools/pin_web_release.py` writes the tag and the SHA-256 of both files into `index.html`'s
`kraken-release` meta), checks it with `tools/check_web_site.mjs --release` (every module the page imports
is there, the patch file loads with the page's own loader, `reader.bin` is the one the patch names, the pin
matches both files), and deploys it.

**The page installs only the release it was published from.** At load it hashes `kraken_patch.json` and
`reader.bin` and refuses any file that does not match its pin ("This page and its firmware files are from
different releases. Reload the page."), so a browser holding files of another deployment in its cache
cannot mix two releases. The footer names the release (`Kraken v1.0`); a local build says "Development
build" and is not pinned.

Two settings keep a release from changing after it is published, so a later redeploy of the same tag gets
the same bytes:

- **Release immutability** (Settings > General > Releases): a published release's files can no longer be
  replaced. Attach the files before or while publishing, as `gh release create` with files does.
- **A ruleset on `v*` tags** so they cannot be moved or deleted. `tools/setup_pages.sh` adds it. While the repository is private the deploy job is skipped:
Pages needs a public repository on a free plan. Once it is public, run `tools/setup_pages.sh` once. It
turns Pages on with GitHub Actions as the source, lets `v*` tags deploy to the `github-pages` environment
(which by default accepts only the default branch), and protects `v*` tags. Then turn on release
immutability by hand.

## 5. Before release: on a radio

Partly done. Needs the radio, the cable and someone at the radio:

1. Reader on a stock radio: HELLO with divisor 13, SUMS and a full READ matching a backup. *2026-11-08,
   macOS: the ROM loads and starts it, HELLO reports divisor 13, SUMS of all 1 MiB takes 2.3 s.*
2. Which of 1.5M / 750k / 500k a cable holds, on macOS, Windows and Linux. *2026-11-08: the Baofeng cable
   tested is a Prolific PL2303 (067B:2303, Apple's AppleUSBPLCOM driver). None of the three work: at each the
   host receives nothing, as when the radio is faster than the host. PL2303 parts without arbitrary-rate
   support round to a fixed table (460800, 614400, 921600, 1228800, ...) with none close enough to the
   radio's rates, so the page now skips fast rates on Prolific cables (`cableMaxBaud`). This run also found
   that a host sending at the wrong rate stalled the reader's 1.5 s fallback; the wait now takes one byte
   at a time and keeps its time. CH340 and FTDI cables are still to be tried.*
3. That the port stays open across the radio's power cycles (the USB-serial chip is in the cable).
4. The updater sequence as written here (it differs in small ways from the community tools: `M`/`E` with
   zero arguments, a space-terminated size, 0xFF padding, waiting for `C` after EOT). Then a full install,
   restore stock from the backup, and install from backup on a radio already running Kraken.

## 6. Limits

- Chrome, Edge or another Chromium browser on a computer. Firefox and Safari have no Web Serial.
- The page cannot see the battery level; it tells the person to charge it.
- The updater runs at 115200, so installing takes about a minute and a half whatever the cable.
- Releases carry no update file: it contains the stock firmware. Installing by hand means building it
  (`out/kraken/updates/`).

## 7. Diagnostics

Install problems depend on the browser, the operating system, the cable's chip and its driver, which a
simulated radio cannot cover. So the page records what it sees from the moment it loads, and **Download
diagnostics** (footer, always there; **Save diagnostics** on the error screen) saves it as
`kraken-diagnostics-<UTC date>-<time>.zip`. It only reads, so it can be clicked mid-flow without
disturbing anything. Web installer bug reports must attach it: the issue form
([.github/ISSUE_TEMPLATE/web_installer.yml](../.github/ISSUE_TEMPLATE/web_installer.yml)) requires the
upload, and [.github/workflows/issue-diagnostics.yml](../.github/workflows/issue-diagnostics.yml) labels a
`web-installer` issue `needs-diagnostics`, with one comment saying how to get the file, until it has a
`.zip` attached. Blank issues are off, so every report goes through a form.

| File | What |
|---|---|
| `summary.txt` | Start here: release, browser and OS, features, allowed cables, each flow run and how it ended (error name, code, message), the screen, the cable path, the reader's rate and the rates tried, the radio's version and classification, each install attempt (blocks acknowledged), the check afterwards, the checks that were not ok |
| `diagnostics.json` | All of it as data, including the radio's 256 sector CRC-32s (each connect), backup and update hashes, the updater's erase time |
| `log.txt` | The page's log with millisecond times: every screen, check and status, plus tab hidden or shown, focus, the browser freezing the page, serial and USB connect and disconnect |
| `trace.txt` | The cable traffic, both ways (below) |
| `console.txt` | The browser console and uncaught errors and rejections |
| `README.txt` | What the file holds and does not hold, for the person posting it |

Browser facts come from `navigator.userAgentData` high-entropy values where the browser has them (the plain
user-agent string freezes the macOS version and hides Windows 11), the Web Serial ports and WebUSB devices
the page is already allowed to use (`getPorts`, `getDevices`: USB IDs, names, chip revision, interfaces,
whether claimed; no serial numbers), feature support and permissions policy, screen size, language and
time-zone offset.

**The trace never holds firmware.** Bug reports are public, and nothing Kraken publishes may contain
Baofeng's firmware. `Link` hands every chunk to the recorder; what it keeps depends on a mode that
`radio.js` sets:

- `raw` (boot ROM and updater): received bytes are kept, up to 64 per event, with bytes arriving within
  20 ms joined into one event. Neither ever sends flash contents.
- `framed` (from the reader's RUN reply until the next ROM or updater session): received bytes are only
  counted. `kr.js readFrame` reports each frame instead: type, length, CRC good or bad, noise skipped before
  it, and only the address of a DATA frame and the count of a SUMS reply. Payloads are kept only for HELLO,
  PONG, OK and ERR.
- A sent block that carries image bytes (`ROM DOWNLOAD` of `reader.bin`, each updater block) is written
  with a label and kept as its length and CRC-32.

The trace keeps its first 3000 events and its last 20000, and says how many were dropped between. A full
install makes about 4,200 (most of them the updater's two replies per block), and the zip is about 20 KB.
`web_flasher_test.mjs` checks that no firmware reaches the trace.

The backup file, the update file and the per-unit sector are never in the zip, only their SHA-256 or
CRC-32. The log does name a backup file the person chose; `README.txt` says so.

Facts come from the flows through `env.diag` (`note`, `add`), so the flows stay free of the page: the
tests pass a `Recorder` or none.
