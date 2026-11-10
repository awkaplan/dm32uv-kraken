#!/usr/bin/env python3
"""make_web_patch.py - the patch file and the page of the web installer (web/, docs/web-flasher.md).

The web installer reads the stock firmware off the user's own radio, applies this patch in the browser and
flashes the result with the radio's own updater. So the patch carries only Kraken's bytes, never Baofeng's.
It holds one entry per stock firmware build Kraken supports (firmware/firmwares.json); the page uses the one whose
version string the radio has at that build's own place.

  entry   one build: --firmware ID --stock STOCK.bin --image PATCHED.bin --update UPDATE.bin --out ENTRY.json
            firmware, version_at  the build's version string and its file offset
            radio_tested  whether Kraken has run on a real radio on this build (firmware/firmwares.json); the page
                      warns before it flashes Kraken onto a build where it has not
            records   the bytes Kraken changes, as (offset, bytes) runs. A run is split wherever the stock byte is
                      unchanged and not erased (0xFF), so no stock code or data rides along in a run.
            stock     where the stock firmware area ends, its SHA-256, and the CRC-32 of each 4 KB sector from
                      0x0300C000 up (the page compares them with CRCs the radio computes, to refuse a radio that is
                      not stock in seconds)
            target    the same for the Kraken image (the installer pads the updater's last block with 0xFF, so
                      the radio ends up holding exactly the image): the page checks an install by these CRCs
                      instead of reading the radio back
            update    the update file's size and SHA-256
          It refuses to write the entry unless applying it to the stock image gives back the patched image exactly,
          and header + firmware area gives back the update file byte for byte.
  site    --reader READER.json --out DIR/kraken_patch.json [--site DIR] [--name kraken] ENTRY.json ...
          the patch file: every entry, and our reader program (reader/, built by build_reader.py: where the boot
          ROM loads and starts it, its size and SHA-256), copied beside it as reader.bin. With --site it also copies
          web/ to that folder first, so the folder is the whole site: serve it as static files.

Plain Python 3, no dependencies. Nothing here talks to a radio.
"""
import argparse
import base64
import hashlib
import json
import pathlib
import shutil
import sys
import zlib

import fwreg

SIZE = 0x100000
APP = 0xC000                 # the updater writes from 0x0300C000 up
SECTOR = 0x1000
HEADER = b"BFUV32-V2" + b"\xff" * (0xFD - 9) + b"END"
SIZE_LIMIT = 0xF4002
FORMAT = "kraken-web-patch-2"
HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE.parent / "web"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def die(msg):
    sys.exit(f"make_web_patch: REFUSED: {msg}")


def end_of(img):
    """One past the last non-erased byte: the end of what the updater must write."""
    return max(i for i in range(len(img)) if img[i] != 0xFF) + 1


def sector_crcs(img):
    return [zlib.crc32(img[s:s + SECTOR]) for s in range(APP, SIZE, SECTOR)]


def records_of(stock, img):
    """Changed-byte runs. Two runs merge only across bytes that are erased in stock (so nothing of stock's)."""
    runs, i = [], APP
    while i < SIZE:
        if stock[i] == img[i]:
            i += 1
            continue
        j = i
        while j < SIZE:
            if stock[j] != img[j]:
                j += 1
                continue
            k = j                       # unchanged byte: may we bridge to the next change?
            while k < SIZE and stock[k] == img[k] and stock[k] == 0xFF and k - j < 32:
                k += 1
            if k < SIZE and k > j and stock[k] != img[k]:
                j = k
                continue
            break
        runs.append((i, img[i:j]))
        i = j
    return runs


def cmd_entry(a):
    fw = fwreg.get(a.firmware)
    at = int(fw["version_at"], 16) - fwreg.FLASH_BASE
    stock = open(a.stock, "rb").read()
    img = open(a.image, "rb").read()
    upd = open(a.update, "rb").read()
    for what, b in (("stock", stock), ("image", img)):
        if len(b) != SIZE or b[:4] != b"thd\x0c" or fwreg.version_of(b, fw) is None:
            die(f"{what} is not a 1 MiB {fw['version']} program-flash image")
    if stock[:APP] != img[:APP]:
        die("the image differs from stock below 0x0300C000; the updater cannot write there")
    s_end, t_end = end_of(stock), end_of(img)

    recs = records_of(stock, img)
    # the patch must rebuild the image exactly from stock alone
    rebuilt = bytearray(stock)
    for off, data in recs:
        rebuilt[off:off + len(data)] = data
    if bytes(rebuilt) != img:
        die("applying the records to stock does not give the image")
    # and no record may carry a stock byte that is not erased (only changed bytes and bridged 0xFF gaps)
    carried = sum(1 for off, data in recs for k, b in enumerate(data) if stock[off + k] == b and b != 0xFF)
    if carried:
        die(f"{carried} unchanged stock bytes would ride along in the records")
    if upd != HEADER + img[APP:t_end]:
        die("header + the image's firmware area is not the update file")
    if len(upd) > SIZE_LIMIT:
        die(f"update file is {len(upd):#x} bytes, over the updater's limit {SIZE_LIMIT:#x}")
    entry = {
        "firmware": fw["version"],
        "version_at": at,
        "radio_tested": bool(fw.get("radio_tested")),
        "stock": {"end": s_end, "sha256": sha(stock[APP:s_end]), "sector_crc32": sector_crcs(stock)},
        "target": {"end": t_end, "sha256": sha(img[APP:t_end]), "sector_crc32": sector_crcs(img)},
        "update": {"size": len(upd), "sha256": sha(upd)},
        "kraken_sectors": sorted({s for off, d in recs for s in range(off & ~(SECTOR - 1), off + len(d), SECTOR)}),
        "records": [[off, base64.b64encode(data).decode()] for off, data in recs],
    }
    pathlib.Path(a.out).write_text(json.dumps(entry, separators=(",", ":")) + "\n")
    nbytes = sum(len(d) for _, d in recs)
    print(f"WEB ENTRY {fw['version']} {a.out}\n  {len(recs)} records, {nbytes} bytes; stock firmware area ends "
          f"{0x03000000 + s_end:#010x}, Kraken's {0x03000000 + t_end:#010x}\n  rebuilds the image and the update file "
          f"{sha(upd)[:16]}")


def cmd_site(a):
    reader = json.loads(pathlib.Path(a.reader).read_text())
    reader_bin = pathlib.Path(a.reader).with_name("reader.bin").read_bytes()
    if len(reader_bin) != reader["size"] or sha(reader_bin) != reader["sha256"] or len(reader_bin) % 4:
        die("reader.bin does not match reader.json")
    builds = [json.loads(pathlib.Path(e).read_text()) for e in a.entries]
    if not builds:
        die("no build entries")
    names = [b["firmware"] for b in builds]
    if len(set(names)) != len(names):
        die(f"a firmware is listed twice: {names}")
    spots = {}
    for b in builds:
        if b["version_at"] in spots:
            die(f"{b['firmware']} and {spots[b['version_at']]} keep their version string at the same place")
        spots[b["version_at"]] = b["firmware"]
    patch = {
        "format": FORMAT,
        "name": a.name,
        "reader": {k: reader[k] for k in ("load", "entry", "size", "sha256")},
        "builds": builds,
    }
    out = pathlib.Path(a.out)
    if a.site:
        site = pathlib.Path(a.site)
        if site.exists():
            shutil.rmtree(site)
        shutil.copytree(WEB, site)
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / "reader.bin").write_bytes(reader_bin)
    out.write_text(json.dumps(patch, separators=(",", ":")) + "\n")
    print(f"WEB PATCH {out}: {', '.join(names)}" + (f"\n  site {a.site}" if a.site else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("entry")
    e.add_argument("--firmware", required=True)
    e.add_argument("--stock", required=True)
    e.add_argument("--image", required=True)
    e.add_argument("--update", required=True)
    e.add_argument("--out", required=True)
    s = sub.add_parser("site")
    s.add_argument("--reader", required=True, help="reader.json from build_reader.py (reader.bin beside it)")
    s.add_argument("--out", required=True)
    s.add_argument("--site", help="also copy web/ here (the patch should be written into it)")
    s.add_argument("--name", default="kraken")
    s.add_argument("entries", nargs="+")
    a = ap.parse_args()
    (cmd_entry if a.cmd == "entry" else cmd_site)(a)


if __name__ == "__main__":
    main()
