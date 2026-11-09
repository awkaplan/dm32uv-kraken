# Installing Kraken

Kraken comes as one update file, `kraken_update_BFUV32-V2.bin`. It works on any DM-32UV that runs stock
firmware `DM32.00.01.048`. You install it with the radio's own built-in updater, the same way as an
official Baofeng update.

**Status:** the update file is built and checked by simulation on every build. Installing it with the
radio's updater has been tested on one radio so far.

**At your own risk.** Kraken is an unofficial modification, not made or supported by Baofeng. Installing
it may void your warranty. Read [What stays untouched](#what-stays-untouched) and keep your backup.

## What stays untouched

The updater only rewrites the main firmware area. It never writes:

- the radio's calibration (kept on a separate memory chip, and different on every radio)
- the codeplug: channels, zones, contacts and settings
- the per-radio identity data that the firmware checks at power-on
- the bootloader, so you can always enter the updater again

You do not need a backup of your own radio to install Kraken safely. Take one anyway (step 3), because
it is what you use to go back to stock.

## What you need

- A DM-32UV that shows **`DM32.00.01.048`** as its firmware version (step 1 shows how to check).
  Do not install Kraken on any other version. Baofeng's public download is a different build line,
  `DM32.01.L01.048`, and cross-flashing between the two lines is reported to brick the radio. It can be
  recovered, but it is not something you want to do.
- The radio's USB programming cable (the one the programming software, CPS, uses).
- A computer with Windows, macOS or Linux.
- About 30 minutes.

## Before you start: get the files and tools

### The update file

Download `kraken_update_BFUV32-V2.bin` from the **Releases** page of the Kraken project:
[github.com/awkaplan/dm32uv-kraken/releases](https://github.com/awkaplan/dm32uv-kraken/releases).
Pick the newest release and download the `.bin` file listed under **Assets**.

You can also build the file yourself; see [Building Kraken](../building.md).

Each release lists a SHA-256 checksum for the file. A checksum is a fingerprint: if yours matches, the
download is complete and unchanged. To check it, open a terminal (see below) in the download folder and run:

| System | Command |
|---|---|
| Windows | `certutil -hashfile kraken_update_BFUV32-V2.bin SHA256` |
| macOS | `shasum -a 256 kraken_update_BFUV32-V2.bin` |
| Linux | `sha256sum kraken_update_BFUV32-V2.bin` |

The long string it prints must match the one on the release page.

### A terminal

All the commands on this page are typed into a terminal window:

- **Windows:** press Start, type `cmd`, press Enter.
- **macOS:** open **Applications > Utilities > Terminal**.
- **Linux:** open your terminal application.

To run a command in a folder, first move there with `cd`, for example `cd Downloads`.

### Python 3 and pyserial

The install tools are small Python programs. Python is free. Use **Python 3.11 or newer**: Roger Clark's backup tool fails on older versions. pyserial is the Python add-on that talks to
the cable.

| System | Install Python 3 | Install pyserial |
|---|---|---|
| Windows | Download from [python.org/downloads](https://www.python.org/downloads/). In the installer, tick **Add python.exe to PATH**. | `py -m pip install pyserial` |
| macOS | Download from [python.org/downloads](https://www.python.org/downloads/), or `brew install python` if you use Homebrew. | `python3 -m pip install pyserial` |
| Linux | Usually already installed. Debian/Ubuntu: `sudo apt install python3 python3-serial` (this installs pyserial too). | `python3 -m pip install pyserial` if not installed above |

On Windows, type `py` where this page says `python3`.

### Roger Clark's DM32 tools

The backup reader and the installer are Roger Clark's DM32 tools. Kraken does not include them; get them
from his repository:

1. Open [github.com/rogerclarkmelbourne/DM32](https://github.com/rogerclarkmelbourne/DM32).
2. Click the green **Code** button, then **Download ZIP**.
3. Unzip it. The tools are in the `python/` folder: `C7000_read_progmem.py` (backup) and
   `DM32_firmware_loader.py` (install).
4. Copy `kraken_update_BFUV32-V2.bin` into that `python/` folder, and open your terminal there.

### The serial port

The cable shows up on the computer as a serial port. You give its name to the tools as `PORT`.

| System | How to find it | Looks like |
|---|---|---|
| Windows | Plug in the cable, open **Device Manager > Ports (COM & LPT)**. | `COM3`, `COM4`, ... |
| macOS | Run `ls /dev/cu.*` with the cable unplugged, then plugged in. The new entry is the cable. | `/dev/cu.usbserial-XXXX` |
| Linux | Run `ls /dev/ttyUSB*` with the cable plugged in. | `/dev/ttyUSB0` |

On Linux, if you get "permission denied", add yourself to the `dialout` group
(`sudo usermod -aG dialout $USER`), then log out and back in.

> **Plug and unplug the cable only while the radio is off.** Plugging it into a radio that is on can
> key the transmitter.

## 1. Read your firmware version

Turn the radio on. Press `OK` (MENU), open **Device Info** and check that `Firmware Ver:` reads
`DM32.00.01.048`. If it shows anything else, stop here.

## 2. Turn the radio off and connect the cable

Turn the radio **off**. Plug the cable into the radio's accessory socket and into the computer.

## 3. Back up the radio's firmware

This reads the whole 1 MB firmware memory. It only reads and changes nothing. You need this file to go
back to stock later.

1. With the radio **off**, run the reader:
   ```
   python3 C7000_read_progmem.py PORT backup1.bin
   ```
   Replace `PORT` with your port name, for example `COM3` or `/dev/cu.usbserial-XXXX`.
2. Turn the radio **on** within 30 seconds. The screen may stay dark while it reads. That is normal.
   A read takes a few minutes.
3. When it finishes, turn the radio off. Run the reader again with `backup2.bin`, and turn the radio
   on again.
4. Turn the radio off. Check that the two reads are identical:

   | System | Command |
   |---|---|
   | Windows | `fc /b backup1.bin backup2.bin` (should say "no differences encountered") |
   | macOS | `shasum -a 256 backup1.bin backup2.bin` (the two strings must match) |
   | Linux | `sha256sum backup1.bin backup2.bin` (the two strings must match) |

   If they differ, the cable link dropped bytes. Read again until two reads match.
5. Keep a matching file somewhere safe, off this computer too (a USB stick or cloud storage). It belongs
   to this one radio. Label it with the radio, so you never mix up backups of two radios.

## 4. Install

1. Make sure the radio is **off**. Leave the cable plugged in.
2. Hold **`SK1` and `PTT`** together and turn the radio on. Keep holding them until the LED lights
   **green**, then let go. The radio is now in its updater: blank screen, green LED.
   (`SK1` is one of the side keys next to `PTT`; the radio's manual shows which.)
3. Run the loader:
   ```
   python3 DM32_firmware_loader.py PORT kraken_update_BFUV32-V2.bin
   ```
   It prints `Official Baofeng firmware detected.` and erases the firmware area, which takes about
   15 seconds. It then sends the file with a percentage count and restarts the radio.
4. The radio restarts into Kraken. Your channels and settings are unchanged.

## 5. Check it

- The radio starts normally. It must not show `Incorrect HW version` or any other error.
- Press `OK` (MENU). The menu has a new **KRAKEN** category at the end.
- **Device Info** still shows `DM32.00.01.048`. Kraken keeps the stock version string.
  **Menu > KRAKEN > About** shows `DM-32UV Kraken`.

Then see the [user guide](README.md) for what's new and where to find it.

## Updating to a newer Kraken

Install the newer `kraken_update_BFUV32-V2.bin` exactly the same way (step 4). Your Kraken settings are
kept, including the callsign, Freq Unlock, Burst Voice and CW ID settings.

## Going back to stock

You need your backup from step 3, and a copy of the Kraken project for its `tools/` folder. Get it from
[github.com/awkaplan/dm32uv-kraken](https://github.com/awkaplan/dm32uv-kraken): **Code > Download ZIP**,
then unzip it. Open your terminal in the unzipped folder.

1. Turn your backup into an update file:
   ```
   python3 tools/make_official_update.py build backup1.bin --base backup1.bin --out stock_restore.bin
   ```
   Give the full path to `backup1.bin` if it is in another folder.
2. Install `stock_restore.bin` exactly as in step 4, with `stock_restore.bin` in place of the Kraken file.

Stock firmware ignores the few bytes Kraken saved in the settings memory, so nothing else needs undoing.

## Troubleshooting

| Problem | What to do |
|---|---|
| The loader cannot open the port | Check the port name. Close any other program using the cable (CPS, another terminal). On Linux, see the `dialout` note above. |
| The loader waits and nothing happens | The radio is not in its updater. Turn it off, hold `SK1` + `PTT`, turn it on, wait for the green LED, then run the loader again. |
| The upload stops partway (cable pulled, computer asleep) | The radio will not start normally, but nothing is lost: the updater itself is never overwritten. Turn the radio off, enter the updater again with `SK1` + `PTT`, and run the loader again. |
| The radio shows `Incorrect HW version` | Go back to stock with your backup (above), and report it as an issue. |
| The two backup reads differ | Normal on a poor link. Read again until two match. Try another USB port. |

Things you do not need to worry about:

- The radio's calibration is stored on a separate chip that the updater never touches.
- The radio's per-unit identity sector is never touched either.

If you are stuck, open an issue on the
[project page](https://github.com/awkaplan/dm32uv-kraken/issues) with what you did and what the tools
printed.

## When the web installer fails

The web installer can save a **diagnostics file**: a zip with what the page did, your browser and
computer, the cable, and what it learned about the radio. We need it to fix installer problems, because
they depend on the browser, the computer and the cable.

1. **Do not reload or close the page yet.** The file holds what happened since the page was opened.
2. Click **Download diagnostics** at the bottom of the page, or **Save diagnostics** on the error screen.
   You can click it at any moment, even while the installer is still working; it does not disturb it.
3. Your browser saves `kraken-diagnostics-<date>-<time>.zip`, usually in your Downloads folder.
4. Open a [web installer problem report](https://github.com/awkaplan/dm32uv-kraken/issues/new?template=web_installer.yml)
   and attach the file. The form will not submit without it.

The file has no firmware, no radio backup and no codeplug (channels, contacts and settings). It does have
your browser and operating system versions and the cable's USB IDs, and it may have the name of a backup
file you chose. `README.txt` inside it lists everything. Bug reports are public.

Your radio is safe while you do this: the installer reads only, except while it installs, and an
interrupted install can always be done again with the radio's updater.

## Other ways to install

- **The web installer** ([../web-flasher.md](../web-flasher.md)) backs up and installs from Chrome or
  Edge, with no Python. It is experimental: installing and restoring with it have not yet been tested on
  a radio. If it stops or fails, see [When the web installer fails](#when-the-web-installer-fails).
- **Baofeng's Windows update tool** speaks the same protocol. Nobody has yet tested whether it accepts a
  Kraken file.
- **Sector-by-sector flashing over the cable** is how developers flash test builds. It is slower and
  riskier, and you do not need it to use Kraken. See [../flashing.md](../flashing.md).

How it works: [../flashing.md](../flashing.md)
