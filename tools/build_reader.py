#!/usr/bin/env python3
"""build_reader.py - build the Kraken reader (reader/), the RAM program the web installer loads through the
boot ROM to read program flash. Runs inside the dm32uv-re Docker image (csky-abiv2-elf-* on PATH), from
tools/build_image.py or on its own:

  build_reader.py --out out/kraken/reader      -> reader.elf, reader.bin, reader.map, reader.json

reader.json: {"load", "entry", "size", "sha256"} for make_web_patch.py. Refuses an image that does not
start with _start at the load address, has bytes outside IRAM, or is not a whole number of words.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "reader"
P = "csky-abiv2-elf-"
LOAD = 0x00010000
IRAM_END = 0x00058000
CFLAGS = ["-mcpu=ck803s", "-Os", "-ffreestanding", "-fno-builtin", "-nostdlib", "-Wall", "-Wextra", "-Werror",
          "-fno-common", "-ffunction-sections"]


def run(cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, type=pathlib.Path)
    a = ap.parse_args()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    objs = []
    for src in ("start.S", "reader.c"):
        o = out / (src.rsplit(".", 1)[0] + ".o")
        run([P + "gcc", *CFLAGS, "-c", SRC / src, "-o", o])
        objs.append(o)
    elf, binf = out / "reader.elf", out / "reader.bin"
    run([P + "ld", "-T", SRC / "reader.ld", "--gc-sections", "-Map", out / "reader.map", "-o", elf, *objs])
    run([P + "objcopy", "-O", "binary", "-R", ".bss", elf, binf])
    syms = {l.split()[2]: int(l.split()[0], 16) for l in run([P + "nm", elf]).splitlines() if len(l.split()) == 3}
    if syms.get("_start") != LOAD:
        sys.exit(f"build_reader: _start is at {syms.get('_start')}, not {LOAD:#x}")
    if syms["__stack_top"] > IRAM_END or syms["__bss_end"] > syms["__stack_top"] - 0x1000:
        sys.exit("build_reader: image, .bss and stack do not fit")
    data = binf.read_bytes()
    if len(data) % 4:
        data += b"\0" * (4 - len(data) % 4)
        binf.write_bytes(data)
    if LOAD + len(data) != syms["__image_end"]:
        sys.exit(f"build_reader: reader.bin is {len(data)} bytes, the image ends at {syms['__image_end']:#x}")
    info = {"load": LOAD, "entry": syms["_start"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (out / "reader.json").write_text(json.dumps(info, indent=1) + "\n")
    print(f"READER {binf}: {len(data)} bytes at {LOAD:#x}, .bss to {syms['__bss_end']:#x}, sha256 {info['sha256'][:16]}")


if __name__ == "__main__":
    main()
