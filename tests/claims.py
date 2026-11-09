"""claim(name) -> (start, end_exclusive) of an allocations.json claim, read at run time.

A test that bounds a symbol by a claim must read the claim, not copy its addresses: allocations.json moves (kraken_feat_state
shrank to end at 0x180053a0 while parrot_qemu_test.py still bounded it at 0x18005600). Looks in this repo's allocations.json
first, then in the other allocation files image.json lists.
"""
import json

from build_artifacts import REPO, WS

_cache = {}


def _load():
    if not _cache:
        files = [REPO / "allocations.json"] + [WS / f for f in json.loads((REPO / "image.json").read_text())["allocation_files"]]
        for f in files:
            if f.exists():
                for a in json.loads(f.read_text())["allocations"]:
                    _cache.setdefault(a["name"], (int(a["start"], 16), int(a["end_exclusive"], 16)))
    return _cache


def claim(name):
    return _load()[name]
