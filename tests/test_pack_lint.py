#!/usr/bin/env python3
"""tools/pack_kraken.py lint on this tree: every source of patches/kraken_ui is placed by the pack (link.json) or a feature spec
(features/*.json), no hand-written linker script or blob list exists, and every fixed claim in allocations.json says why it is
pinned (docs/flash-allocation.md). A feature spec must name a user-guide page with Status and Where lines, have a row in
docs/feature-status.md and not list a source that link.json lists too. The second part runs the spec rules on a small
throwaway tree. The build runs the same check in its step 0; this host test catches it in seconds."""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from hostlib import check, finish  # noqa: E402
import pack_kraken  # noqa: E402

bad = pack_kraken.lint_problems(ROOT)
for b in bad:
    print("     " + b)
check(not bad, f"every Kraken source, script and claim goes through the pack ({len(bad)} problem(s))")


def tree(root, spec, link_sources=("krk_a.c", "krk_b.c"), guide_text="**Status:** x\n**Where:** y\n", status_row="| F9 | A | x | y | z |"):
    """A minimal repo: two sources, one feature spec, its guide page and the status table."""
    ui = root / "patches/kraken_ui"
    (ui / "features").mkdir(parents=True)
    for f in ("krk_a.c", "krk_b.c"):
        (ui / f).write_text("")
    (ui / "link.json").write_text(json.dumps({"sources": list(link_sources)}))
    (ui / "kraken_ui.patch.json").write_text(json.dumps({"pack": {}}))
    (ui / "features/a.json").write_text(json.dumps(spec))
    (root / "allocations.json").write_text(json.dumps({"allocations": []}))
    (root / "docs/user-guide").mkdir(parents=True)
    (root / "docs/user-guide/a.md").write_text(guide_text)
    (root / "docs/feature-status.md").write_text("| ID |\n|---|\n" + status_row + "\n")


def problems(**kw):
    spec = kw.pop("spec", {"feature": "F9", "sources": ["krk_b.c"], "guide": "user-guide/a.md"})
    with tempfile.TemporaryDirectory() as d:
        tree(pathlib.Path(d), spec, **kw)
        return pack_kraken.lint_problems(d)


good = problems(link_sources=("krk_a.c",))
check(not good, "a spec with its source, guide page and status row is clean", extra="; ".join(good))
check(any("also in link.json" in b for b in problems()), "a source in link.json and a spec is refused")
check(any("no row in docs/feature-status.md" in b or "has no row" in b for b in problems(link_sources=("krk_a.c",), status_row="| F8 | A |")),
      "a feature id without a docs/feature-status.md row is refused")
check(any("**Where:**" in b for b in problems(link_sources=("krk_a.c",), guide_text="**Status:** x\n")), "a guide page without a Where line is refused")
check(any("**Status:**" in b for b in problems(link_sources=("krk_a.c",), guide_text="**Where:** y\n")), "a guide page without a Status line is refused")
check(any("does not exist" in b for b in problems(link_sources=("krk_a.c",), spec={"feature": "F9", "sources": ["krk_b.c"], "guide": "user-guide/none.md"})),
      "a missing guide page is refused")
check(any("not in link.json" in b for b in problems(link_sources=("krk_a.c",), spec={"feature": "F9", "sources": [], "guide": "user-guide/a.md"})),
      "a source that is in neither link.json nor a spec is refused")
finish()
