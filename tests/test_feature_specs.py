#!/usr/bin/env python3
"""Host test for the per-feature specs (patches/kraken_ui/features/*.json, tools/gen_menu_hosts.py merged_spec, tools/gen_manifest.py
merged_link): a spec row lands at its "order" and never moves another row's item number, the KRAKEN-slot rules hold, a source is
listed once, and the merged result is what the generated files on disk say. No compiler needed."""
import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from hostlib import check, finish  # noqa: E402
import gen_manifest as gm  # noqa: E402
import gen_menu_hosts as gh  # noqa: E402

spec = json.loads(gh.SPEC.read_text())
n_aprs = len(next(h for h in spec["hosts"] if h["id"] == "aprs")["rows"])


def rows(m, hid):
    return [r["key"] for h in m["hosts"] if h["id"] == hid for r in h["rows"]]


def feat(*rs, **kw):
    return dict({"feature": "F9", "_file": "t.json", "rows": list(rs)}, **kw)


def row(order, key, host="aprs", **kw):
    return dict({"host": host, "order": order, "key": key, "label": key, "screen": f"&krk_{key}_screen"}, **kw)


def refused(feats):
    try:
        gh.merged_spec(copy.deepcopy(spec), feats)
    except gh.SpecError:
        return True
    return False


base = rows(gh.merged_spec(copy.deepcopy(spec), []), "aprs")
mid = rows(gh.merged_spec(copy.deepcopy(spec), [feat(row(2, "zz"))]), "aprs")
check(mid == base[:2] + ["zz"] + base[2:], "a spec row at order 2 sits there, the host's own rows keep their order", extra=str(mid))
end = rows(gh.merged_spec(copy.deepcopy(spec), [feat(row(n_aprs, "zz"))]), "aprs")
check(end == base + ["zz"], "order = row count appends")
check(refused([feat(row(n_aprs + 1, "zz"))]), "an order past the end is refused")
check(refused([feat(row(0, "a"), row(0, "b"))]), "two rows at one order are refused")
check(refused([feat(row(0, "zz", host="nohost"))]), "an unknown host is refused")
check(refused([feat({"host": "aprs", "key": "zz", "label": "zz", "screen": "&x"})]), "a row without order is refused")
es = spec["ext_slots"]
free = next(k for k in range(es["count"]) if k not in es["hand_written"])
m = gh.merged_spec(copy.deepcopy(spec), [feat({"host": "kraken", "order": free, "screen": "&krk_zz_screen"})])
check(m["ext_rows"] == [(free, "&krk_zz_screen")], "a kraken row takes the free slot")
check(refused([feat({"host": "kraken", "order": es["hand_written"][0], "screen": "&x"})]), "a kraken row on a krk_ext.c slot is refused")
check(refused([feat({"host": "kraken", "order": es["count"], "screen": "&x"})]), "a kraken slot past the table is refused")

link = json.loads(gm.LINK.read_text())
srcs = lambda l: [s if isinstance(s, str) else s["file"] for s in l["sources"]]   # noqa: E731
ml = gm.merged_link(link, [{"_file": "t.json", "sources": [{"file": "krk_new.c", "keep": True, "after": "krk_core.c"}, "krk_last.c"], "keep": [".rodata.x"]}])
s = srcs(ml)
check(s[s.index("krk_core.c") + 1] == "krk_new.c" and s[-1] == "krk_last.c", '"after" places a source behind its file, the rest go last')
check(ml["keep"][-1] == ".rodata.x", "a spec's gc-root patterns are appended")
try:
    gm.merged_link(link, [{"_file": "t.json", "sources": ["krk_core.c"]}])
    dup = False
except gm.SpecError:
    dup = True
check(dup, "a source already in link.json is refused")

# what the generators wrote on disk is this merge
feats = gh.load_features()
m = gm.build(json.loads(gm.MANIFEST.read_text()))
check(json.dumps(m["pack"]["sources"]) == json.dumps(gm.gen_pack(gm.merged_link(link, feats))["sources"]), "the manifest's pack is link.json plus the specs")
check(gh.HEADER.read_text() == gh.build(gh.merged_spec(), json.loads(gm.MANIFEST.read_text()))[1], "krk_menu_rows.h is current")
finish()
