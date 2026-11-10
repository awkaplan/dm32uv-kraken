#!/usr/bin/env python3
"""make_official_update.py - one universal update file in Baofeng's official format, and its read-back check.

The radio's own updater (hold SK1+PTT while powering on; green LED) takes a "BFUV32-V2" file over the
programming cable: a 0x100-byte header ("BFUV32-V2", 0xFF fill, "END") and then the raw program flash from
0x0300C000 up. It erases 0x0300C000-0x030FFFFF and writes the body there, checking only a CRC16 per 1 KB
block. It never touches the boot header and Baofeng loader (0x03000000-0x0300AFFF), the per-unit sector
0x0300B000 (binding record, TX-band model byte) or the data chip (calibration). So one file fits every
radio on the same firmware line; see docs/flashing.md.

  build   IMAGE -> UPDATE.bin (+ .sha256), after these checks:
            - IMAGE is 1 MiB with the boot header, on the DM32.00.01.048 line (version string at 0xADAA0), or with
              --firmware on that build's line (its version string where firmware/firmwares.json says);
            - IMAGE equals --base below 0x0300B000 (the updater cannot deliver changes there);
            - the file fits the updater's size limit (0xF4002 bytes, header included);
            - no linked section (--maps) ends past the last non-FF byte: the upload tool pads the last
              1 KB block (Roger Clark's loader with 0x00), so that tail must be unused;
            - a simulated update on --base with a made-up per-unit sector gives IMAGE from 0x0300C000 up
              (tail padding aside) and leaves 0x03000000-0x0300BFFF untouched.
  verify  UPDATE.bin + a read-back of the radio (+ --before, its backup) -> exit 0 only if the radio holds
            exactly what the updater should have written, and (with --before) everything below 0x0300C000,
            the per-unit sector included, is unchanged.

To go back to stock, build an update file from the radio's own backup: build BACKUP.bin --base BACKUP.bin.

Nothing here talks to a radio. Flash the file with Roger Clark's DM32_firmware_loader.py
(https://github.com/rogerclarkmelbourne/DM32); read the radio back with his C7000_read_progmem.py.
"""
import argparse
import hashlib
import os
import re
import sys

FLASH_BASE = 0x03000000
SIZE = 0x100000
APP = 0xC000                 # the updater erases and writes from 0x0300C000 up
UNIT = 0xB000                # per-unit sector 0x0300B000
SECTOR = 0x1000
BLOCK = 0x400                # updater block size
SIZE_LIMIT = 0xF4002         # bootloader refuses a declared size above this (literal @0x030047c4)
VERSION_AT = 0xADAA0         # the reference build's; --firmware picks another build's (firmware/firmwares.json)
VERSION = b"DM32.00.01.048"
HEADER = b"BFUV32-V2" + b"\xff" * (0xFD - 9) + b"END"
assert len(HEADER) == 0x100


def sha(b):
    return hashlib.sha256(b).hexdigest()


def die(msg):
    sys.exit(f"REFUSED: {msg}")


def use_firmware(key):
    """Check images against another build in firmware/firmwares.json (its version string and where it keeps it)."""
    global VERSION, VERSION_AT
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fwreg
    fw = fwreg.get(key)
    VERSION, VERSION_AT = fw["version"].encode(), int(fw["version_at"], 16) - FLASH_BASE


def check_image(img, what):
    if len(img) != SIZE:
        die(f"{what} is {len(img):#x} bytes, not 1 MiB")
    if img[:4] != b"thd\x0c":
        die(f"{what} has no boot header (thd\\x0c) at 0x03000000")
    if img[VERSION_AT:VERSION_AT + len(VERSION)] != VERSION:
        die(f"{what} is not on the {VERSION.decode()} line (0x{FLASH_BASE + VERSION_AT:08x} reads "
            f"{img[VERSION_AT:VERSION_AT + 16]!r})")


def map_end(paths):
    """Highest end address of any non-empty output section in program flash, over GNU ld map files."""
    hi, src = 0, None
    pat = re.compile(r"^(\.\S+)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", re.M)
    for p in paths:
        for name, a, n in pat.findall(open(p).read()):
            a, n = int(a, 16), int(n, 16)
            if n and FLASH_BASE <= a < FLASH_BASE + SIZE and a + n > hi:
                hi, src = a + n, f"{os.path.basename(p)} {name}"
    return hi, src


def body_of(img):
    end = max(i for i in range(len(img)) if img[i] != 0xFF) + 1
    if end <= APP:
        die("image has nothing above 0x0300C000")
    return img[APP:end]


def apply_update(radio, body, pad):
    """What the updater leaves in flash: below 0xC000 untouched, 0xC000+ erased, body written in 1 KB blocks."""
    out = bytearray(radio)
    out[APP:] = b"\xff" * (SIZE - APP)
    padded = body + bytes([pad]) * ((-len(body)) % BLOCK)
    out[APP:APP + len(padded)] = padded
    return bytes(out)


def parse_file(data):
    if len(data) < 0x100 or data[:0x100] != HEADER:
        die("not a BFUV32-V2 file (header mismatch)")
    return data[0x100:]


def detect_firmware(img):
    """The build in firmware/firmwares.json whose version string IMG has at that build's own place, or None."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fwreg
    return next((f["id"] for f in fwreg.firmwares() if len(img) == SIZE and fwreg.version_of(img, f)), None)


def cmd_build(a):
    img = open(a.image, "rb").read()
    fw = a.firmware or detect_firmware(img)
    if fw:
        use_firmware(fw)
    base = open(a.base, "rb").read()
    check_image(img, a.image)
    check_image(base, a.base)
    low = [FLASH_BASE + o for o in range(0, UNIT, SECTOR) if img[o:o + SECTOR] != base[o:o + SECTOR]]
    if low:
        die("image differs from the base below 0x0300B000 (the updater cannot write there): "
            + ", ".join(f"{s:#010x}" for s in low))
    body = body_of(img)
    out = HEADER + body
    if len(out) > SIZE_LIMIT:
        die(f"file is {len(out):#x} bytes, over the updater's {SIZE_LIMIT:#x} limit")
    data_end = FLASH_BASE + APP + len(body)
    if a.maps:
        hi, src = map_end(a.maps)
        if hi > data_end:
            die(f"{src} ends at {hi:#010x}, past the last non-FF byte {data_end:#010x}: the upload tool's "
                "padding would land on it")
    if a.official_sample:
        if not os.path.exists(a.official_sample):
            die(f"--official-sample {a.official_sample}: no such file")
        if open(a.official_sample, "rb").read()[:0x100] != HEADER:
            die(f"header differs from the official sample {a.official_sample}")
        print(f"header matches the official sample {os.path.basename(a.official_sample)}")
    # simulated update on the base with a made-up per-unit record: below 0xC000 must survive, above must be IMAGE
    radio = bytearray(base)
    radio[UNIT:UNIT + SECTOR] = bytes((i * 37 + 11) & 0xFF for i in range(SECTOR))
    for pad in (0x00, 0xFF, 0x1A):                      # Roger's loader, erased flash, YMODEM's usual pad
        res = apply_update(radio, body, pad)
        if res[:APP] != bytes(radio[:APP]):
            die("simulation: the update changed flash below 0x0300C000")
        if res[APP:data_end - FLASH_BASE] != img[APP:data_end - FLASH_BASE]:
            die("simulation: flash from 0x0300C000 does not match the image")
        tail = res[data_end - FLASH_BASE:]
        if any(b not in (pad, 0xFF) for b in tail):
            die("simulation: unexpected bytes after the image")
    open(a.out, "wb").write(out)
    open(a.out + ".sha256", "w").write(f"{sha(out)}  {os.path.basename(a.out)}\n")
    pad_n = (-len(body)) % BLOCK
    print(f"UPDATE {a.out}\n  sha256 {sha(out)}\n  {len(out):#x} bytes (limit {SIZE_LIMIT:#x}); writes "
          f"0x{FLASH_BASE + APP:08x}-{data_end - 1:#010x}; last block padded by the upload tool: "
          f"{pad_n} bytes at {data_end:#010x} (unused)\n  image sha256 {sha(img)}")


def cmd_verify(a):
    body = parse_file(open(a.file, "rb").read())
    dump = open(a.dump, "rb").read()
    if len(dump) != SIZE:
        die(f"{a.dump} is not 1 MiB")
    before = open(a.before, "rb").read() if a.before else None
    bad = []
    data_end = APP + len(body)
    pad_end = data_end + (-len(body)) % BLOCK
    if dump[APP:data_end] != body:
        bad += [f"{FLASH_BASE + o:#010x}" for o in range(APP, data_end, SECTOR)
                if dump[o:min(o + SECTOR, data_end)] != body[o - APP:min(o + SECTOR, data_end) - APP]]
    tail = dump[data_end:pad_end]
    pads = {b for b in tail}
    if len(pads) > 1:
        bad.append(f"padding {data_end + FLASH_BASE:#010x} holds mixed bytes {sorted(pads)}")
    if dump[pad_end:] != b"\xff" * (SIZE - pad_end):
        bad.append(f"flash after {FLASH_BASE + pad_end:#010x} is not erased")
    if before:
        low = [f"{FLASH_BASE + o:#010x}" for o in range(0, APP, SECTOR) if dump[o:o + SECTOR] != before[o:o + SECTOR]]
        if low:
            bad.append("changed below 0x0300C000: " + ", ".join(low))
        unit_state = "unchanged" if dump[UNIT:UNIT + SECTOR] == before[UNIT:UNIT + SECTOR] else "CHANGED"
    if bad:
        print("VERIFY FAILED:\n  " + "\n  ".join(bad))
        sys.exit(1)
    print(f"VERIFY OK: 0x{FLASH_BASE + APP:08x}-{FLASH_BASE + data_end - 1:#010x} match {os.path.basename(a.file)}; "
          f"tail padding {len(tail)} bytes of {', '.join(f'{p:#04x}' for p in sorted(pads)) or '-'}; rest erased"
          + (f"; below 0x0300C000 unchanged (per-unit sector {unit_state})" if before else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("image")
    b.add_argument("--base", required=True, help="the stock image the build is based on")
    b.add_argument("--out", required=True)
    b.add_argument("--maps", nargs="*", default=[], help="linker maps of every blob in the image")
    b.add_argument("--official-sample", help="an official BFUV32-V2 file to compare the header with (optional)")
    b.add_argument("--firmware", help="the stock firmware build (id or version in firmware/firmwares.json); default: "
                                      "the build whose version string IMAGE has at that build's own place")
    v = sub.add_parser("verify")
    v.add_argument("file", help="the BFUV32-V2 update file that was flashed")
    v.add_argument("dump", help="1 MiB read-back of the radio afterwards")
    v.add_argument("--before", help="the radio's backup from before the update")
    a = ap.parse_args()
    (cmd_build if a.cmd == "build" else cmd_verify)(a)


if __name__ == "__main__":
    main()
