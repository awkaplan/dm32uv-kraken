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
**Show log**. Each screen has an `id` (`off`, `on`, `update-mode`, `verify`, ...) that the tests use, so the
wording can change freely:

| Step | The person | The page | Time |
|---|---|---|---|
| Check | Picks the cable's port, turns the radio off, then on normally | Loads the reader through the boot ROM, raises the line to 1.5 Mbaud if the cable can, checks the boot header and the firmware version (`DM32.00.01.048`), and has the radio CRC all 256 sectors: a radio that is not stock, or already runs this build, is told so here | seconds |
| Back up | Waits | Reads all 1 MiB in 4 KB CRC-checked frames, then checks every sector against the radio's own CRC and the firmware area against stock's SHA-256 | ~10 s at 1.5 Mbaud, ~1.5 min at 115200 (Prolific cables) |
| Save | Turns the radio off, saves the file | Saves with the file picker and reads the saved file back to compare; without a picker it downloads and asks the person to confirm | |
| Install | Turns the radio on holding SK1 + PTT (green LED), clicks Install | Builds the update file, checks its hash against the build's, and sends it to Baofeng's updater | ~1.5 min (the updater runs at 115200) |
| Verify | Turns the radio off, clicks Check, turns it on normally | Has the radio CRC its flash and compares with what was sent. Can be skipped | seconds |

**Update or remove Kraken** takes a saved backup instead of reading the radio: **Update Kraken** (a radio already on
an older Kraken build), or put stock back from it. Both still connect first for the boot-header and version
checks and say whether the backup's per-unit sector matches this radio.

The page refuses, and writes nothing, when the radio reports another firmware version (Baofeng's public
`DM32.01.L01.048` is named in the message), when its firmware area is not stock 048 (it points to the backup
option), when it already runs this build, or when a chosen backup is not a 1 MiB stock 048 image.

## 2. Pieces

| File | What |
|---|---|
| `web/index.html`, `style.css`, `app.js` | The page and the `ui` the flows talk to. Its Content-Security-Policy allows scripts and network access only to the site itself |
| `web/lib/flow.js` | The three flows (install, from backup, restore). No DOM, so the tests drive the same code |
| `web/lib/radio.js` | Boot ROM (load and start the reader, choose the line rate) and Baofeng's updater. Nothing else writes flash |
| `web/lib/kr.js` | Host side of the reader protocol |
| `web/lib/link.js` | Web Serial as an exact-length byte link with deadlines, reopened to change the baud rate |
| `web/lib/image.js` | Checks, applying the patch, the update file, the expected flash after an install |
| `reader/reader.c`, `start.S`, `reader.ld` | The reader: about 1.5 KB of C-SKY code that runs from IRAM |
| `tools/build_reader.py` | Builds the reader with the C-SKY toolchain in the Docker image |
| `tools/make_web_patch.py` | Writes `kraken_patch.json`, copies `web/` and `reader.bin` beside it |

### The patch file

`kraken_patch.json` (`kraken-web-patch-1`) holds:

- `records`: the changed bytes as (offset, base64) runs, about 103 KB. A run never carries an unchanged stock
  byte that is not erased; the tool refuses to write the file otherwise. The only stock-derived content is
  what Kraken itself writes: about 650 bytes of copied stock menu pointer tables (extended with new rows) and
  hook instructions.
- `stock`: where the stock firmware area ends, its SHA-256, and the CRC-32 of each 4 KB sector from
  `0x0300C000` up.
- `target`: the same for the Kraken image. The installer pads the updater's last block with 0xFF, so after
  an install the radio holds exactly this image, and these CRCs check it.
- `update`: the update file's size and SHA-256. `reader`: load address, entry, size and SHA-256 of `reader.bin`.

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

Two tests, both after a build:

```sh
node tests/web_flasher_test.mjs                                   # the page's flows, ~40 s
tools/build.sh --run 'python3 tests/reader_qemu_test.py'          # the reader in QEMU, ~15 s
```

- `web_flasher_test.mjs` runs the real flows against a simulated radio
  ([tests/web/mock_radio.mjs](../tests/web/mock_radio.mjs)) that plays the boot ROM, the reader protocol (baud
  changes included) and Baofeng's updater, with faults: corrupted, cut and subtly wrong frames, a cable chip
  limited to 1 Mbaud, fast rates that garble, the wrong firmware line, a modified or already-installed radio,
  a stock radio restored earlier, no update mode, a cable pulled mid-flash, a bad write caught afterwards, and
  both backup flows. It also checks that the update file matches the build's byte for byte.
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

1. Build (`tools/build.sh`). Besides the update file it writes the release's web files,
   `out/kraken/kraken_patch.json` and `out/kraken/kraken_reader.bin` (`reader.bin` under its release
   name), each with a `.sha256`.
2. Check them as the workflow will: `node tools/check_web_site.mjs out/kraken/web`.
3. Tag and publish a release with both files attached, for example:
   ```sh
   gh release create v1.0 out/kraken/kraken_patch.json out/kraken/kraken_reader.bin \
       out/kraken/kraken_patch.json.sha256 out/kraken/kraken_reader.bin.sha256
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
- Releases still publish `kraken_update_BFUV32-V2.bin`, which contains the stock firmware. Dropping it in
  favour of the web installer is a separate decision.
