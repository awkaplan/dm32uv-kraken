# Flashing a build (developers)

How to put your own build on a radio, check it, and go back to stock. It uses the radio's own updater and
Roger Clark's public tools. The step-by-step version for non-developers is
[user-guide/install.md](user-guide/install.md).

Nothing in this repository talks to a radio. Every step here is manual.

## 1. The update file

`tools/build.sh` writes `out/kraken/kraken_update_BFUV32-V2.bin` (see [building.md](building.md)). It is
in Baofeng's official update format:

- a 0x100-byte header (`BFUV32-V2`, 0xFF fill, `END`), then the raw program flash from `0x0300C000` up;
- no encryption or signature; the updater checks a CRC16 per 1 KB block.

The radio's updater erases `0x0300C000`-`0x030FFFFF` and writes the file there. It never touches:

- the boot header and Baofeng's loader (`0x03000000`-`0x0300AFFF`), so the updater always comes back;
- the per-unit sector `0x0300B000` (binding record, TX-band model byte);
- the separate data chip (calibration, codeplug, settings).

So one file fits every radio on the same firmware, and installing it needs no per-radio data.
`tools/make_official_update.py build` refuses to write the file if the image differs from stock below
`0x0300B000`, if it is over the updater's size limit (0xF4002 bytes with the header), if it is not on the
`DM32.00.01.048` line, or if a linked section reaches the last block's padding. It then simulates the
update on the stock image.

**Only for radios that show firmware `DM32.00.01.048`** (Menu > Device Info). The updater does not check
the version. Baofeng's public download is a different build line, `DM32.01.L01.048`; cross-flashing between
the lines is reported to brick the radio (recoverably).

## 2. Tools

From [rogerclarkmelbourne/DM32](https://github.com/rogerclarkmelbourne/DM32), folder `python/`:

| Tool | Use | Radio state |
|---|---|---|
| `C7000_read_progmem.py PORT OUT.bin` | reads the whole 1 MiB program flash (read-only) | off at launch; power it on normally within 30 s |
| `DM32_firmware_loader.py PORT FILE.bin` | sends a `BFUV32-V2` file to the updater, then reboots the radio | in updater mode: hold SK1 + PTT while powering on, green LED |

Both need Python 3.11+ and pyserial. `PORT` is the cable's serial port: `COM3` on Windows,
`/dev/cu.usbserial-XXXX` on macOS, `/dev/ttyUSB0` on Linux. Only one program may use the port at a time.
Plug and unplug the cable only with the radio off.

From this repository: `tools/make_official_update.py` (plain Python 3, no dependencies).

## 3. Back up the radio

Take two reads and compare them. The reader has no retry, so a dropped byte only shows as a mismatch.

```sh
python3 C7000_read_progmem.py PORT backup1.bin     # then power the radio on
python3 C7000_read_progmem.py PORT backup2.bin     # power-cycle first
cmp backup1.bin backup2.bin && echo MATCH
```

Each read takes a few minutes. Keep `backup1.bin`; it belongs to that one radio (the per-unit sector is in
it).

Make the restore file now, while you have the backup in hand:

```sh
python3 tools/make_official_update.py build backup1.bin --base backup1.bin --out stock_restore_BFUV32-V2.bin
```

If it refuses ("not on the DM32.00.01.048 line"), the radio is not on the firmware Kraken is built for. Stop.

## 4. Flash

1. Radio off, cable in.
2. Hold SK1 and PTT, power on, release when the LED is green.
3. Run:
   ```sh
   python3 DM32_firmware_loader.py PORT out/kraken/kraken_update_BFUV32-V2.bin
   ```
   It erases (about 15 s), uploads, and reboots the radio into the new firmware.

If the upload fails or the radio does not boot, power it off (remove the battery if needed) and enter the
updater again with SK1 + PTT. The loader is never written, so this always works. Then flash again, or flash
`stock_restore_BFUV32-V2.bin`.

## 5. Verify

Read the radio back and check it holds exactly what the updater should have written:

```sh
python3 C7000_read_progmem.py PORT after.bin
python3 tools/make_official_update.py verify out/kraken/kraken_update_BFUV32-V2.bin after.bin --before backup1.bin
```

`VERIFY OK` means `0x0300C000` up matches the file, the rest of the app area is erased, and everything below
`0x0300C000`, the per-unit sector included, is unchanged. The last 1 KB block is padded by the loader
(0x00 with Roger Clark's), so compare with `verify`, not `cmp`.

Then boot the radio: it must reach the standby screen with no "Incorrect HW version" or binding error, and
the KRAKEN menu must be in the main menu.

## 6. Back to stock

Flash the restore file from step 3 the same way:

```sh
python3 DM32_firmware_loader.py PORT stock_restore_BFUV32-V2.bin
python3 C7000_read_progmem.py PORT after.bin
python3 tools/make_official_update.py verify stock_restore_BFUV32-V2.bin after.bin --before backup1.bin
```

The restore file is built from your own backup, so it puts back exactly the firmware the radio had.
Kraken's few settings records on the data chip stay where they are; no stock code reads that area
([settings-field.md](settings-field.md) section 3).

## 7. Updating to a newer build

Flash the new update file the same way (section 4). The updater rewrites the whole app area, so it does
not matter which Kraken build, or stock, was on the radio before.

## Other routes

Roger Clark's repository also has `C7000_write_progmem.py`, which writes the program flash directly at any
offset, including the loader and the per-unit sector. Kraken does not need it, and a mistake with it can
leave a radio that only a full-flash restore of its own backup recovers. Use the updater.
