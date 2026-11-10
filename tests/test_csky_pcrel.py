#!/usr/bin/env python3
"""tools/csky_pcrel.py against dm32uv-re's disassembly of DM32.00.01.048: every instruction's length, and for each
PC-relative one (bsr, branches, lrw) its kind, its target and its re-encoding to the same target.

The disassembly also lists literal words it took for instructions; those decode to nothing here or to targets outside
flash, and are not counted as errors (csky_pcrel errs towards "not PC-relative", which tools/fwmap.py then compares
byte for byte). Needs dm32uv-re's meta/disasm.jsonl and the stock image; skips without them (CI)."""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from csky_pcrel import decode, encode_like, insn_len  # noqa: E402

PCREL = {"bsr": "call", "br": "branch", "bt": "branch", "bf": "branch", "bez": "branch", "bnez": "branch",
         "bhz": "branch", "blsz": "branch", "blz": "branch", "bhsz": "branch", "lrw": "pool"}


def re_root():
    env = os.environ.get("DM32UV_RE")
    for p in ([pathlib.Path(env)] if env else []) + [REPO.parent / "dm32uv-re", REPO.parent / "dm32" / "dm32uv-re"]:
        if (p / "meta/disasm.jsonl").exists():
            return p
    return None


def main():
    root = re_root()
    img_path = root and root / "data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin"
    if not root or not img_path.exists():
        print("SKIP: no dm32uv-re meta/disasm.jsonl or stock image (set DM32UV_RE)")
        return 0
    img = img_path.read_bytes()
    base = 0x03000000
    n = checked = 0
    bad = []
    for line in open(root / "meta/disasm.jsonl"):
        d = json.loads(line)
        for i in d["instructions"]:
            pc = int(i["addr"], 16)
            if not base <= pc < base + len(img):
                continue
            off = pc - base
            n += 1
            if insn_len(img, off) != i["length"]:
                bad.append(f"{pc:#x}: length {insn_len(img, off)} vs {i['length']}")
                continue
            kind = PCREL.get(i["mnemonic"])
            got = decode(img, off, pc)
            if kind is None:
                if got is not None and base <= got[1] < base + len(img) and i["mnemonic"] not in ("bkpt",):
                    bad.append(f"{pc:#x}: {i['text']} decoded as {got}")
                continue
            want = int(i["text"].split(",")[-1].split()[-1], 16)
            if got is None or not base <= want < base + len(img):
                continue                        # a literal word the disassembly took for an instruction
            checked += 1
            if got != (kind, want):
                bad.append(f"{pc:#x}: {i['text']} decoded as {got}")
            elif encode_like(img, off, pc, want) != img[off:off + i["length"]]:
                bad.append(f"{pc:#x}: {i['text']} does not re-encode to itself")
    print(f"{n} instructions, {checked} PC-relative checked, {len(bad)} wrong")
    for b in bad[:20]:
        print("  FAIL", b)
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
