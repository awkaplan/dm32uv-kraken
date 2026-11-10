#!/usr/bin/env python3
"""fetch_stock.py - download the stock update files of the non-reference firmware builds and compose their base images.

For every build in firmware/firmwares.json other than the reference: download its Baofeng update file from the
commit-pinned URL (skipped when the file is already there), refuse it unless its size and SHA-256 are the pinned
ones, and write out/stock/<id>/progmem_stock_<id>.bin (+ .sha256): the reference image's boot region below
0x0300C000, the update body from there, 0xFF after it (tools/fwreg.py compose). The reference image itself is
dm32uv-re's and is only checked.

  python3 tools/fetch_stock.py              all builds
  python3 tools/fetch_stock.py l01          one build
  python3 tools/fetch_stock.py --offline    never download: fail if a file is missing

DM32_STOCK_DIR names a folder with update files fetched by hand (used instead of downloading). Nothing here is committed: out/ is
ignored, and tools/check_repo_hygiene.py refuses firmware files anywhere. Plain Python 3, no dependencies; runs on
the host (tools/build.sh calls it before starting Docker).
"""
import argparse
import sys
import urllib.request

import fwreg


def fetch(fw, offline):
    up = fwreg.update_path(fw)
    if not up.exists():
        if offline:
            sys.exit(f"{up}: missing (--offline). Download {fw['update_url']} there.")
        up.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {fw['version']}: {fw['update_url']}", flush=True)
        req = urllib.request.Request(fw["update_url"], headers={"User-Agent": "kraken-fetch-stock"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        tmp = up.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(up)
    data = up.read_bytes()
    if len(data) != fw["update_size"] or fwreg.sha256(data) != fw["update_sha256"]:
        up.rename(up.with_suffix(".rejected"))
        sys.exit(f"{up}: {len(data)} bytes, sha256 {fwreg.sha256(data)}; firmware/firmwares.json pins "
                 f"{fw['update_size']} bytes, {fw['update_sha256']}. Moved aside as .rejected.")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    ref = fwreg.reference()
    ref_img = fwreg.load_base(ref)
    for fw in fwreg.firmwares()[1:]:
        if a.ids and fw["id"] not in a.ids and fw["version"] not in a.ids:
            continue
        img = fwreg.compose(ref_img, fetch(fw, a.offline))
        if fwreg.version_of(img, fw) is None:
            sys.exit(f"{fw['version']}: no version string at {fw['version_at']} in the composed image")
        out = fwreg.base_path(fw)
        if not out.exists() or out.read_bytes() != img:
            out.write_bytes(img)
        out.with_name(out.name + ".sha256").write_text(f"{fwreg.sha256(img)}  {out.name}\n")
        print(f"{fw['version']:16s} {out}  sha256 {fwreg.sha256(img)[:16]}")


if __name__ == "__main__":
    main()
