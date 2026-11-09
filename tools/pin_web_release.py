#!/usr/bin/env python3
"""Pin an assembled web-installer site to its release: write the tag and the SHA-256 of kraken_patch.json and
reader.bin into index.html's kraken-release meta. The page then uses only files with exactly those hashes,
so it always installs the release it was published from (web/app.js, releasePin).

  python3 tools/pin_web_release.py SITE_DIR --tag v1.0

Run by .github/workflows/pages.yml after it assembles the site; tools/check_web_site.mjs --release checks it.
"""
import argparse
import hashlib
import pathlib
import re
import sys

META = re.compile(r'<meta name="kraken-release" content="[^"]*">')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site")
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()
    site = pathlib.Path(a.site)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", a.tag):
        sys.exit(f"pin_web_release: unexpected tag {a.tag!r}")
    sha = {name: hashlib.sha256((site / name).read_bytes()).hexdigest() for name in ("kraken_patch.json", "reader.bin")}
    index = site / "index.html"
    html = index.read_text()
    if len(META.findall(html)) != 1:
        sys.exit("pin_web_release: index.html must have exactly one kraken-release meta")
    pin = f'tag={a.tag};patch={sha["kraken_patch.json"]};reader={sha["reader.bin"]}'
    index.write_text(META.sub(f'<meta name="kraken-release" content="{pin}">', html))
    print(f"pinned {site} to {a.tag}: patch {sha['kraken_patch.json'][:12]}, reader {sha['reader.bin'][:12]}")


if __name__ == "__main__":
    main()
