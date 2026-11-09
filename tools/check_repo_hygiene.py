#!/usr/bin/env python3
"""Check that no firmware, radio data or personal details are about to be committed.

    python3 tools/check_repo_hygiene.py                  # the files git would commit (tracked + untracked, not ignored)
    python3 tools/check_repo_hygiene.py --range A..B     # every file added or changed in commits A..B, and their messages

The second form is what CI runs on a pull request: a file added in one commit and deleted in the next is
gone from the tree but still in the history, which is forever once merged.

Rules:
  - nothing under private/ or out/
  - no firmware, dump, archive, backup or log files (by extension)
  - no file over 1 MiB
  - no binary file other than images and test audio
  - no image with embedded metadata (EXIF in JPEG or PNG: phone photos carry GPS position)
  - no serial port names with a real device suffix (/dev/cu.usbserial-XXXX is fine; -1420 is not)
  - optional: none of the strings in the KRAKEN_DENYLIST environment variable (one per line, any case;
    meant for your own callsign, DMR ID, serials). In CI it comes from a repository secret of the same
    name. Matches are reported by file and line only, never the string. Without it, this rule is skipped.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_BYTES = 1 << 20
BANNED_DIRS = ("private/", "out/")
BANNED_EXT = {
    # firmware and memory images
    ".bin", ".img", ".hex", ".dfu", ".elf", ".axf", ".srec", ".s19", ".dmp", ".dump",
    # codeplugs, backups, radio sessions
    ".dat", ".rdt", ".sav", ".bak", ".log",
    # archives (stock firmware ships in them)
    ".zip", ".rar", ".7z", ".gz", ".tgz", ".xz", ".bz2", ".tar",
}
BINARY_OK_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pcm", ".wav"}
PORT_RE = re.compile(rb"(?:cu|tty)\.(?:usbserial|usbmodem|wchusbserial|SLAB_USBtoUART)-?[A-Za-z0-9]*[0-9][A-Za-z0-9]*")


def git(*args, binary=False):
    out = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout
    return out if binary else out.decode()


def png_has_metadata(data):
    # walk the chunks: eXIf is EXIF; zTXt/iTXt/tEXt can carry XMP with GPS too
    pos = 8
    while pos + 8 <= len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        if kind == b"eXIf":
            return True
        if kind in (b"iTXt", b"tEXt", b"zTXt") and b"XML:com.adobe.xmp" in data[pos + 8:pos + 8 + n]:
            return True
        if kind == b"IEND":
            break
        pos += 12 + n
    return False


def jpeg_has_metadata(data):
    return b"Exif\x00\x00" in data[:65536] or b"http://ns.adobe.com/xap/" in data[:65536]


def denylist():
    raw = os.environ.get("KRAKEN_DENYLIST", "")
    return [w.strip().lower().encode() for w in raw.splitlines() if w.strip() and not w.strip().startswith("#")]


def check_file(path, size, read, deny, problems):
    """path: repo-relative str; size: bytes; read(): the content (called only when needed)."""
    def bad(msg, line=None):
        problems.append((path, line, msg))

    low = path.lower()
    ext = pathlib.PurePosixPath(low).suffix
    if low.startswith(BANNED_DIRS):
        bad("is under private/ or out/, which must never be committed")
    if ext in BANNED_EXT or ".bak-" in low:
        bad(f"'{ext or low}' files are not committed (firmware, dumps, backups, archives, logs)")
    if size > MAX_BYTES:
        bad(f"is {size} bytes (limit {MAX_BYTES}); large binaries do not belong in this repo")
        return
    for i, w in enumerate(deny):
        if w in low.encode():
            bad(f"path matches denylist entry {i + 1}")
    data = read()
    is_binary = b"\0" in data[:8192]
    if is_binary and ext not in BINARY_OK_EXT:
        bad("is a binary file; only images and test audio may be binary")
    if ext == ".png" and png_has_metadata(data):
        bad("has embedded metadata (EXIF/XMP); strip it, e.g. exiftool -all= FILE")
    if ext in (".jpg", ".jpeg") and jpeg_has_metadata(data):
        bad("has embedded metadata (EXIF/XMP, may include GPS); strip it, e.g. exiftool -all= FILE")
    if is_binary:
        return
    low_data = data.lower()
    for m in PORT_RE.finditer(data):
        bad("names a real serial port; use a placeholder like /dev/cu.usbserial-XXXX",
            data.count(b"\n", 0, m.start()) + 1)
    for i, w in enumerate(deny):
        start = 0
        while (at := low_data.find(w, start)) >= 0:
            bad(f"matches denylist entry {i + 1}", low_data.count(b"\n", 0, at) + 1)
            start = at + len(w)


def check_tree(deny, problems):
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    n = 0
    for path in sorted(set(f for f in files if f)):
        p = ROOT / path
        if not p.is_file() or p.is_symlink():
            continue  # deleted in the working tree but still in the index
        check_file(path, p.stat().st_size, p.read_bytes, deny, problems)
        n += 1
    return n


def check_range(rng, deny, problems):
    # every blob added or modified by a commit in the range, under the path it had there
    raw = git("log", "--format=", "--raw", "-z", "--no-renames", "--diff-filter=AM", rng, binary=True)
    fields = raw.split(b"\0")
    seen = set()
    i = 0
    while i + 1 < len(fields):
        meta, path = fields[i].decode().strip(), fields[i + 1].decode()
        i += 2
        if not meta.startswith(":"):
            continue
        sha = meta.split()[3]
        if (sha, path) in seen:
            continue
        seen.add((sha, path))
        size = int(git("cat-file", "-s", sha))
        check_file(path, size, lambda s=sha: git("cat-file", "blob", s, binary=True), deny, problems)
    # commit messages: personal details must stay out of them too
    for c in git("rev-list", rng).split():
        msg = git("log", "-1", "--format=%B", c).lower().encode()
        for n, w in enumerate(deny):
            if w in msg:
                problems.append((f"commit {c[:12]}", None, f"message matches denylist entry {n + 1}"))
        if PORT_RE.search(msg):
            problems.append((f"commit {c[:12]}", None, "message names a real serial port"))
    return len(seen)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--range", help="check commits BASE..HEAD instead of the working tree")
    args = ap.parse_args()

    deny = denylist()
    problems = []
    n = check_range(args.range, deny, problems) if args.range else check_tree(deny, problems)
    what = f"files changed in {args.range}" if args.range else "files"
    print(f"checked {n} {what}; denylist: {f'{len(deny)} entries' if deny else 'not set, skipped'}")

    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for path, line, msg in problems:
        where = f"{path}:{line}" if line else path
        if gha and not path.startswith("commit "):
            print(f"::error file={path}{f',line={line}' if line else ''}::{msg}")
        else:
            print(f"FAIL {where}: {msg}")
    if problems:
        print(f"{len(problems)} problem(s)")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
