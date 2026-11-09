#!/usr/bin/env python3
"""Generate the branch-hook splices and the blob list of kraken_ui.patch.json from two compact sources.

  patches/kraken_ui/hooks.json  the branch-hook splices: one line per site (name, addr, the stock bytes or the stock bsr's
                                   target, our target) plus why-templates for the sites that share a shape (the four
                                   frequency-band checks, the five vocoder-window copies). Expanded into "splices" entries,
                                   replaced in place by name, new ones appended. stock-call targets are written as dm32uv-re
                                   symbol names and become "@stock:<name>".
  patches/kraken_ui/blobs.json  the blob list: shared cflags/ldflags (defaults, -O2 replaced per blob), each blob's exported
                                   symbols and its imports from the other blobs, expanded into "blobs" with cflags, ldflags and
                                   defsym ("@blob:symbol").

The dm32uv-re builder takes neither defaults nor lists, so this writes the expanded form it reads. Every other manifest entry is left
exactly as it is; the Call Log / Radio Settings / Channel Set generator-call sites come from menu_hosts.json (gen_menu_hosts.py).

Usage:
  tools/gen_manifest.py           rewrite the manifest
  tools/gen_manifest.py --check   exit 1 if it is out of date (build_image.py runs this)
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_menu_hosts import MANIFEST, PATCH, SpecError, dump, merge, num   # noqa: E402

HOOKS = PATCH / "hooks.json"
BLOBS = PATCH / "blobs.json"


def hx(v):
    return f"0x{v:08x}"


def gen_hook(h, templates):
    name = h["name"]
    try:
        t = dict(h)
        bias = num(h["bias"]) if "bias" in h else None
        if bias is not None:
            t["iram"] = hex(num(h["addr"]) - bias)
        if "t" in h:
            if "why" in h:
                raise SpecError("both t and why")
            t["why"] = templates[h["t"]].format(**t)
        target = h["target"] + (f" + {h['bias']}" if bias is not None else "")
        e = {"name": name, "addr": h["addr"]}
        if "expect" in h:
            e["expect"] = h["expect"]
        e["why"] = t["why"]
        if "expect_branch" in h:
            eb = h["expect_branch"]
            e["expect_branch"] = {"kind": "bsr", "target": eb if eb.startswith("0x") else "@stock:" + eb}
        e["branch"] = {"kind": "bsr", "target": target}
        if "fill" in h:
            e["fill"] = h["fill"]
        return e
    except (KeyError, SpecError) as ex:
        raise SpecError(f"hook {name}: {ex!r}")


def gen_blobs(spec):
    d = spec["defaults"]
    exports = {b["name"]: b.get("exports", []) for b in spec["blobs"]}
    out = []
    for b in spec["blobs"]:
        o = {"name": b["name"], "sources": b["sources"],
             "cflags": [b.get("opt", d["opt"]), *d["cflags"]], "ldflags": list(d["ldflags"]),
             "linker_script": b["linker_script"], "entry": b["entry"], "expect_entry": b["expect_entry"]}
        defsym = {}
        for imp in b.get("imports", []):
            src = imp["from"]
            if src not in exports:
                raise SpecError(f"blob {b['name']}: imports from unknown blob {src}")
            if "syms" in imp:
                syms = imp["syms"]
            else:
                syms = [s for s in exports[src] if s not in imp.get("except", [])]
                bad = set(imp.get("except", [])) - set(exports[src])
                if bad:
                    raise SpecError(f"blob {b['name']}: except {sorted(bad)} is not exported by {src}")
            for s in syms:
                defsym[s] = f"@{src}:{s}"
        if defsym:
            o["defsym"] = defsym
        if b.get("libgcc"):
            o["libgcc"] = True
        out.append(o)
    return out


def build(manifest):
    hs = json.loads(HOOKS.read_text())
    entries = [gen_hook(h, hs["templates"]) for h in hs["hooks"]]
    names = [e["name"] for e in entries]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SpecError(f"hooks.json: duplicate names {sorted(dup)}")
    m = dict(manifest)
    m["splices"] = merge(manifest["splices"], entries, ())
    m["blobs"] = gen_blobs(json.loads(BLOBS.read_text()))
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the manifest is out of date")
    args = ap.parse_args()
    raw = MANIFEST.read_text()
    try:
        new = dump(build(json.loads(raw)))
    except SpecError as e:
        print(f"gen_manifest: {e}", file=sys.stderr)
        return 2
    if args.check:
        if raw != new:
            print(f"gen_manifest: {MANIFEST.name} is out of date with {HOOKS.name} / {BLOBS.name}; run tools/gen_manifest.py",
                  file=sys.stderr)
            return 1
        return 0
    if raw != new:
        MANIFEST.write_text(new)
    print(f"gen_manifest: {'updated ' + MANIFEST.name if raw != new else 'up to date'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
