#!/usr/bin/env python3
"""make_web_patch.py - the patch file and the page of the web installer (web/, docs/web-flasher.md).

The web installer reads the stock firmware off the user's own radio, applies this patch in the browser and
flashes the result with the radio's own updater. So the patch carries only Kraken's bytes, never Baofeng's:

  records   the bytes Kraken changes, as (offset, bytes) runs. A run is split wherever the stock byte is
            unchanged and not erased (0xFF), so no stock code or data rides along in a run.
  stock     where the stock firmware area ends, its SHA-256, and the CRC-32 of each 4 KB sector from
            0x0300C000 up (the page compares them with CRCs the radio computes, to refuse a radio that is
            not stock in seconds)
  target    the same for the Kraken image (the installer pads the updater's last block with 0xFF, so
            the radio ends up holding exactly the image): the page checks an install by these CRCs
            instead of reading the radio back
  update    the update file's size and SHA-256
  reader    our reader program (reader/, built by build_reader.py): where the boot ROM loads and starts it,
            its size and SHA-256. It is copied beside the patch as reader.bin

It refuses to write the patch unless applying it to the stock image gives back the patched image exactly,
and header + firmware area gives back the update file byte for byte. With --site it also copies web/ to
that folder, so the folder is the whole site: serve it as static files.

  make_web_patch.py --stock STOCK.bin --image PATCHED.bin --update UPDATE.bin --reader READER.json
                    --out DIR/kraken_patch.json [--site DIR] [--name kraken]

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

SIZE = 0x100000
APP = 0xC000                 # the updater writes from 0x0300C000 up
SECTOR = 0x1000
VERSION_AT = 0xADAA0
VERSION = b"DM32.00.01.048"
HEADER = b"BFUV32-V2" + b"\xff" * (0xFD - 9) + b"END"
SIZE_LIMIT = 0xF4002
FORMAT = "kraken-web-patch-1"
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stock", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--update", required=True)
    ap.add_argument("--reader", required=True, help="reader.json from build_reader.py (reader.bin beside it)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--site", help="also copy web/ here (the patch should be written into it)")
    ap.add_argument("--name", default="kraken")
    a = ap.parse_args()

    reader = json.loads(pathlib.Path(a.reader).read_text())
    reader_bin = pathlib.Path(a.reader).with_name("reader.bin").read_bytes()
    if len(reader_bin) != reader["size"] or sha(reader_bin) != reader["sha256"] or len(reader_bin) % 4:
        die("reader.bin does not match reader.json")
    stock = open(a.stock, "rb").read()
    img = open(a.image, "rb").read()
    upd = open(a.update, "rb").read()
    for what, b in (("stock", stock), ("image", img)):
        if len(b) != SIZE or b[:4] != b"thd\x0c" or b[VERSION_AT:VERSION_AT + len(VERSION)] != VERSION:
            die(f"{what} is not a 1 MiB {VERSION.decode()} program-flash image")
    if stock[:APP] != img[:APP]:
        die("the image differs from stock below 0x0300C000; the updater cannot write there")
    s_end, t_end = end_of(stock), end_of(img)

    recs = records_of(stock, img)
    # the patch must rebuild the image exactly from stock alone
    rebuilt = bytearray(stock)
    for at, data in recs:
        rebuilt[at:at + len(data)] = data
    if bytes(rebuilt) != img:
        die("applying the records to stock does not give the image")
    # and no record may carry a stock byte that is not erased (only changed bytes and bridged 0xFF gaps)
    carried = sum(1 for at, data in recs for k, b in enumerate(data) if stock[at + k] == b and b != 0xFF)
    if carried:
        die(f"{carried} unchanged stock bytes would ride along in the records")
    if upd != HEADER + img[APP:t_end]:
        die("header + the image's firmware area is not the update file")
    if len(upd) > SIZE_LIMIT:
        die(f"update file is {len(upd):#x} bytes, over the updater's limit {SIZE_LIMIT:#x}")

    patch = {
        "format": FORMAT,
        "name": a.name,
        "firmware": VERSION.decode(),
        "stock": {
            "end": s_end,
            "sha256": sha(stock[APP:s_end]),
            "sector_crc32": sector_crcs(stock),
        },
        "target": {"end": t_end, "sha256": sha(img[APP:t_end]), "sector_crc32": sector_crcs(img)},
        "reader": {k: reader[k] for k in ("load", "entry", "size", "sha256")},
        "update": {"size": len(upd), "sha256": sha(upd)},
        "kraken_sectors": sorted({s for at, d in recs for s in range(at & ~(SECTOR - 1), at + len(d), SECTOR)}),
        "records": [[at, base64.b64encode(data).decode()] for at, data in recs],
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
    nbytes = sum(len(d) for _, d in recs)
    print(f"WEB PATCH {out}\n  {len(recs)} records, {nbytes} bytes; stock firmware area ends {0x03000000 + s_end:#010x}, "
          f"Kraken's {0x03000000 + t_end:#010x}\n  rebuilds the image and the update file {sha(upd)[:16]}"
          + (f"\n  site {a.site}" if a.site else ""))


if __name__ == "__main__":
    main()
