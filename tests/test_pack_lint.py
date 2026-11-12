#!/usr/bin/env python3
"""tools/pack_kraken.py lint on this tree: every source of patches/kraken_ui is placed by the pack (link.json), no hand-written
linker script or blob list exists, and every fixed claim in allocations.json says why it is pinned (docs/flash-allocation.md).
The build runs the same check in its step 0; this host test catches it in seconds."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from hostlib import check, finish  # noqa: E402
import pack_kraken  # noqa: E402

bad = pack_kraken.lint_problems(ROOT)
for b in bad:
    print("     " + b)
check(not bad, f"every Kraken source, script and claim goes through the pack ({len(bad)} problem(s))")
finish()
