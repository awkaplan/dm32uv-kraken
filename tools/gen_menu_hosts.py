#!/usr/bin/env python3
"""Generate the per-host boilerplate of the stock-menu rows from one spec.

A "host" is a stock sub-list this project appends rows to (APRS, Call Log, GNSS, Radio Settings,
Channel Set). Each row used to be written down in up to six places that had to agree: its label
string, both language arrays, both descriptor counts, the dispatch jump tables and their cmphsi bounds
(all in kraken_ui.patch.json), and the C slot table plus its KRK_*_MAX (krk_ext.c, krk.h).
patches/kraken_ui/menu_hosts.json is now the one place; this script writes the rest:

  kraken_ui.patch.json  the host-owned "data" entries (labels, label arrays, jump tables) and
                           "splices" entries (descriptor pointer/count, dispatch bound, table pointer),
                           replaced in place by name, new ones appended, stale label entries removed.
                           Every other entry is left exactly as it is.
  krk_menu_rows.h          KRK_<HOST>_FIRST / _MAX / _GEN_MODE / _ROWS / _ITEM_<ROW> for the C side.

The generator-call sites (a host's "gen_sites": stock `bsr menu_option_list_dispatch` -> `bsr krk_hook_gen`) are generated too.

More ride on the same machinery (docs/menu-hosts.md section 3):
  * the 11th top-level category is a host with "pfx"/"label_name"/"arr_names"/"desc_names" naming overrides, "no_header" (no
    KRK_*_ defines) and three "jump_tables" (render / OK / item, each with its cmphsi bound and pool-word pointer);
  * a row of a host with jump tables may override a table's stub with its own entry ("stubs": {table key: "blob:symbol" | "0x..."}):
    the Channel Set Burst Voice row, a nest parent whose level-4 init and OK are not the host's (docs/chanset-submenu.md 8);
  * "nests": the Other Func level-5 parents (a per-slot template: label in both language arrays, the level-5 init slot -> a stub,
    the level-5 OK slot -> stock's VOX arm) and the VOX child-list descriptor ("child_lists").
What stays hand-written: the other splice SITES that reach our code (compare-chain and jump-table hooks, krk_stubs.S;
the hooks table patches/kraken_ui/hooks.json, expanded by tools/gen_manifest.py, which also holds the Other Func
generator and level-6 hooks and the Channel Set level-5 hooks) - those are reverse-engineering findings with their own expected bytes.

Usage:
  tools/gen_menu_hosts.py           rewrite the manifest and the header
  tools/gen_menu_hosts.py --check   exit 1 if either is out of date (build_image.py runs this)
"""
import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PATCH = REPO / "patches/kraken_ui"
SPEC = PATCH / "menu_hosts.json"
MANIFEST = PATCH / "kraken_ui.patch.json"
HEADER = PATCH / "krk_menu_rows.h"

LABEL_STRIDE = 16           # labels are packed on 16-byte boundaries (the layout the hand-written entries used)
CMPHSI16_MAX = 32           # a 16-bit cmphsi encodes value - 1 in 5 bits: a bounded dispatcher takes at most 32 rows
LIST_ITEMS_MAX = 33         # stock level lists are 35 bytes: u16 count + 33 items
GEN_STOCK = "@stock:menu_option_list_dispatch"      # what every gen_site calls in stock ...
GEN_HOOK = "@kraken_ui:krk_hook_gen"             # ... and what it calls now (stock generator first, then our rows)


class SpecError(Exception):
    pass


def num(v):
    return int(v, 0) if isinstance(v, str) else int(v)


def hx(v):
    return f"0x{v:08x}"


def label_size(s):
    n = len(s.encode("ascii")) + 1
    return (n + 3) & ~3


def part_size(p, off):
    if "string" in p:
        return len(p["string"].encode(p.get("encoding", "ascii"))) + (1 if p.get("nul") else 0)
    if "align" in p:
        a = p["align"]
        return (-off) % a
    if "stock_u32" in p:
        return 4 * p["count"]
    if "u32" in p:
        return 4 * len(p["u32"])
    raise SpecError(f"cannot size data part {p!r}")


def data_size(e):
    off = 0
    for p in e["parts"]:
        off += part_size(p, off)
    return off


def host_fmt(h, rows):
    """Placeholders for the spec's "why" templates: {stock} {n} {total} {first} {last} {items} {labels}
    (and, in a row's label why, {item} {label})."""
    stock = h["stock"]["count"]
    n = len(rows)
    last = stock + n - 1
    return dict(stock=stock, n=n, total=stock + n, first=stock, last=last,
                items=f"item {stock}" if n == 1 else f"items {stock}..{last}",
                labels=", ".join(r["label"] for r in rows), id=h["id"])


def table_target(t):
    """A jump-table entry: "blob:symbol" (a stub of ours) or a raw stock address "0x..." (a row that reuses a stock case block,
    e.g. the Burst Voice parent's commit entry = stock's set_mode(0x23, 1) arm, docs/chanset-submenu.md 8)."""
    return t if t.startswith("0x") else "@" + t


def why(h, key, fmt):
    w = h.get("why", {}).get(key)
    return w.format(**fmt) if w is not None else None


def entry(name, addr, fields, w):
    """A splice entry, keys in the order the hand-written file uses: name, addr, the fields, why."""
    e = {"name": name, "addr": hx(addr)}
    e.update(fields)
    if w is not None:
        e["why"] = w
    return e


def data_entry(name, addr, w, parts):
    """A data entry: name, addr, why, parts (why before parts, as in the hand-written file)."""
    e = {"name": name, "addr": hx(addr)}
    if w is not None:
        e["why"] = w
    e["parts"] = parts
    return e


def check_ascii(label, where):
    try:
        label.encode("ascii")
    except UnicodeEncodeError:
        raise SpecError(f"{where}: label {label!r} is not ASCII (the stock list renderer reads GB2312/ASCII)")


def gen_host(h):
    """Returns (data entries, splice entries, header lines, label-entry prefix) for one host."""
    hid = h["id"]
    rows = h["rows"]
    st = h["stock"]
    fmt = host_fmt(h, rows)
    pfx = h.get("pfx", f"krk_{hid}")
    if not rows:
        raise SpecError(f"host {hid}: no rows (remove the host instead)")
    keys = [r["key"] for r in rows]
    if len(set(keys)) != len(keys):
        raise SpecError(f"host {hid}: duplicate row keys {keys}")
    if fmt["total"] > LIST_ITEMS_MAX:
        raise SpecError(f"host {hid}: {fmt['total']} rows, a stock level list holds {LIST_ITEMS_MAX}")
    if h.get("jump_tables") and fmt["total"] > CMPHSI16_MAX:
        raise SpecError(f"host {hid}: {fmt['total']} rows, but its dispatch is bounded by a 16-bit cmphsi "
                        f"(at most {CMPHSI16_MAX}); see docs/chanset-submenu.md")

    data, splices = [], []

    # labels: packed from labels_at, LABEL_STRIDE apart, unless a row pins its own (label_at)
    lbl_name = {}
    at = num(h["labels_at"]) if "labels_at" in h else None
    for k, r in enumerate(rows):
        if "stock_label" in r:
            continue
        check_ascii(r["label"], f"host {hid} row {r['key']}")
        if "label_at" in r:
            a = num(r["label_at"])
        else:
            if at is None:
                raise SpecError(f"host {hid} row {r['key']}: no label_at and the host has no labels_at")
            a = at
            at += (label_size(r["label"]) + LABEL_STRIDE - 1) // LABEL_STRIDE * LABEL_STRIDE
        name = r.get("label_name", f"{pfx}_lbl_{r['key']}")
        lbl_name[r["key"]] = name
        w = r.get("why", h.get("why", {}).get("label"))     # a row's own why, else the host's label template
        data.append(data_entry(name, a, w.format(**fmt, item=fmt["first"] + k, label=r["label"]) if w else None,
                               [{"string": r["label"], "encoding": "ascii", "nul": True}, {"align": 4}]))

    # the two language arrays: the stock strings, then one pointer per row
    for lang in ("zh", "en"):
        sp = {"stock_u32": st[lang], "count": st["count"]}
        if f"expect_{lang}" in st:
            if len(st[f"expect_{lang}"]) != st["count"]:
                raise SpecError(f"host {hid}: expect_{lang} has {len(st[f'expect_{lang}'])} entries, count is {st['count']}")
            sp["expect"] = st[f"expect_{lang}"]
        parts = [sp]
        for r in rows:
            if "stock_label" in r:
                parts.append({"stock_u32": r["stock_label"][lang], "count": 1})
            elif parts[-1].get("u32") is not None and parts[-1] is not sp:
                parts[-1]["u32"].append("@" + lbl_name[r["key"]])
            else:
                parts.append({"u32": ["@" + lbl_name[r["key"]]]})
        data.append(data_entry(h.get("arr_names", {}).get(lang, f"{pfx}_arr_{lang}"), num(h["arrays_at"][lang]), why(h, f"arr_{lang}", fmt), parts))

    # the descriptor: {u32 strings, u8 count, u8 subtype, pad} per language, zh first
    desc = num(h["descriptor"])
    for k, lang in enumerate(("zh", "en")):
        d = desc + 8 * k
        dn = h.get("desc_names", {}).get(lang, f"{hid}_desc_{lang}")
        arr = h.get("arr_names", {}).get(lang, f"{pfx}_arr_{lang}")
        splices.append(entry(f"{dn}_ptr", d,
                             {"expect_u32": st[lang], "u32": f"@{arr}"}, why(h, f"desc_{lang}_ptr", fmt)))
        splices.append(entry(f"{dn}_count", d + 4,
                             {"expect_u8": f"0x{st['count']:02x}", "u8": f"0x{fmt['total']:02x}"},
                             why(h, f"desc_{lang}_count", fmt)))

    # relocated dispatch jump tables (stock entries kept, one stub entry per row) with their bound and pool word
    for t in h.get("jump_tables", []):
        tk = t["key"]
        if len(t["expect"]) != st["count"]:
            raise SpecError(f"host {hid} table {tk}: expect has {len(t['expect'])} entries, the host has {st['count']} stock rows")
        for r in rows:
            if set(r.get("stubs", {})) - {x["key"] for x in h["jump_tables"]}:
                raise SpecError(f"host {hid} row {r['key']}: stubs for unknown tables {sorted(r['stubs'])}")
        data.append(data_entry(f"{pfx}_{tk}", num(t["at"]), why(h, tk, fmt), [
            {"stock_u32": t["stock"], "count": st["count"], "expect": t["expect"]},
            {"u32": [table_target(r.get("stubs", {}).get(tk, t["stub"])) for r in rows]}]))
        b = t["bound"]
        op = b["op"]
        splices.append(entry(b["name"], num(b["addr"]),
                             {"expect": f"{st['count'] - 1:02x} {op}", "bytes": f"{fmt['total'] - 1:02x} {op}"},
                             why(h, b["name"], fmt)))
        p = t["ptr"]
        splices.append(entry(p["name"], num(p["addr"]),
                             {"expect_u32": t["stock"], "u32": f"@{pfx}_{tk}"}, why(h, p["name"], fmt)))

    # the generator-call sites: stock menu_option_list_dispatch -> krk_hook_gen (the host needs a gen_mode)
    if h.get("gen_sites") and "gen_mode" not in h:
        raise SpecError(f"host {hid}: gen_sites without gen_mode")
    for s in h.get("gen_sites", []):
        splices.append({"name": s["name"], "addr": s["addr"], "why": s["why"],
                        "expect_branch": {"kind": "bsr", "target": GEN_STOCK},
                        "branch": {"kind": "bsr", "target": GEN_HOOK}})

    # C side (the 11th category has none: its constants live in krk_core.c)
    if h.get("no_header"):
        return data, splices, [], f"{pfx}_lbl_"
    H = hid.upper()
    hdr = [f"/* {h['menu']}: stock items 0..{fmt['stock'] - 1}, ours {fmt['items']} ({fmt['labels']}) */",
           f"#define KRK_{H}_FIRST {fmt['first']}",
           f"#define KRK_{H}_MAX {fmt['n']}"]
    if "gen_mode" in h:
        hdr.append(f"#define KRK_{H}_GEN_MODE {h['gen_mode']}")
    for k, r in enumerate(rows):
        hdr.append(f"#define KRK_{H}_ITEM_{r['key'].upper()} {fmt['first'] + k}")
    hdr.append(f"#define KRK_{H}_ROWS " + ", ".join(r["screen"] for r in rows))
    return data, splices, hdr, f"{pfx}_lbl_"


def gen_nest(n):
    """The Other Func nesting: per item a label in both language arrays, a level-5 init slot -> stub and a level-5 OK
    slot -> stock's VOX arm (one 4-splice template, `{id}_item{slot}_{lbl_zh,lbl_en,enter,ok}`), plus the child-list
    descriptors (`child_lists`: a stock {ptr, count} descriptor pointed at a relocated, longer array)."""
    nid, data, splices = n["id"], [], []
    base = {k: num(v) for k, v in n["slot_base"].items()}      # zh / en label arrays, enter / ok level-5 tables
    for it in n["items"]:
        slot, label = it["slot"], it["label"]
        check_ascii(label, f"nest {nid} item {slot}")
        ln = it["label_name"]
        data.append(data_entry(ln, num(it["label_at"]), it["label_why"],
                               [{"string": label, "encoding": "ascii", "nul": True}, {"align": 4}]))
        pre = f"{nid}_item{slot}"
        for lang in ("zh", "en"):
            w = n["why"]["lbl"].format(lang=lang, slot=slot, label=label, stock_name=it["stock_name"])
            splices.append(entry(f"{pre}_lbl_{lang}", base[lang] + 4 * slot,
                                 {"expect_u32": it["stock_" + lang], "u32": "@" + ln}, w))
        splices.append(entry(f"{pre}_enter", base["enter"] + 4 * slot,
                             {"expect_u32": it["stock_enter"], "u32": "@" + it["stub"]}, it["enter_why"]))
        splices.append(entry(f"{pre}_ok", base["ok"] + 4 * slot,
                             {"expect_u32": it["stock_ok"], "u32": n["ok_to"]}, it["ok_why"]))
    for c in n.get("child_lists", []):
        cid, desc = c["id"], num(c["descriptor"])
        for k, lang in enumerate(("zh", "en")):
            L = c[lang]
            parts = [{"stock_u32": L["stock"], "count": c["stock_count"], "expect": L["expect"]}]
            if "extra" in L:
                parts.append({"stock_u32": L["extra"]["stock"], "count": 1, "expect": L["extra"]["expect"]})
            data.append(data_entry(L["name"], num(L["at"]), L.get("why"), parts))
            d = desc + 8 * k
            splices.append(entry(f"{cid}_{lang}_ptr", d, {"expect_u32": L["stock"], "u32": "@" + L["name"]}, L.get("ptr_why")))
            splices.append(entry(f"{cid}_{lang}_count", d + 4,
                                 {"expect_u8": f"0x{c['stock_count']:02x}", "u8": f"0x{c['stock_count'] + 1:02x}"},
                                 L.get("count_why")))
    return data, splices


def merge(section, generated, owned_prefixes):
    """Replace entries by name in place; drop stale label entries of a host; append new ones."""
    by_name = {e["name"]: e for e in generated}
    out, seen = [], set()
    for e in section:
        n = e["name"]
        if n in by_name:
            out.append(by_name[n])
            seen.add(n)
        elif any(n.startswith(p) for p in owned_prefixes):
            continue                        # a label of a row that is no longer in the spec
        else:
            out.append(e)
    out += [e for e in generated if e["name"] not in seen]
    return out


def check_layout(manifest, claim):
    """Every data entry inside its claim (generated ones) and no two data entries overlapping."""
    lo, hi = claim
    spans = []
    for e in manifest["data"]:
        a = num(e["addr"])
        spans.append((a, a + data_size(e), e["name"]))
    spans.sort()
    for (a0, e0, n0), (a1, _, n1) in zip(spans, spans[1:]):
        if a1 < e0:
            raise SpecError(f"data entries overlap: {n0} {a0:#x}..{e0:#x} and {n1} at {a1:#x}")
    return [(a, e, n) for a, e, n in spans if not (lo <= a and e <= hi)]


def build(spec, manifest):
    data, splices, hdr, owned = [], [], [], []
    for h in spec["hosts"]:
        d, s, c, o = gen_host(h)
        data += d
        splices += s
        hdr += c + [""] if c else []
        owned.append(o)
    for n in spec.get("nests", []):
        d, s = gen_nest(n)
        data += d
        splices += s
    names = [e["name"] for e in data + splices]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SpecError(f"generated entry names collide: {sorted(dup)}")
    m = dict(manifest)
    m["data"] = merge(manifest["data"], data, owned)
    m["splices"] = merge(manifest["splices"], splices, ())
    claim = (num(spec["data_claim"]["start"]), num(spec["data_claim"]["end_exclusive"]))
    gen_names = {e["name"] for e in data}
    outside = [x for x in check_layout(m, claim) if x[2] in gen_names]
    if outside:
        raise SpecError("generated data outside the data claim " + ", ".join(f"{n} {a:#x}..{e:#x}" for a, e, n in outside))
    header = "\n".join([
        "/* krk_menu_rows.h - GENERATED by tools/gen_menu_hosts.py from menu_hosts.json. Do not edit:",
        " * change the spec and rerun the script (the build fails while this file is out of date).",
        " *",
        " * Per stock-menu host: KRK_<HOST>_FIRST (item number of our first row), _MAX (our row count),",
        " * _GEN_MODE (menu_option_list_dispatch mode that lists the host's rows, when a generator does),",
        " * _ITEM_<ROW> (each row's item number) and _ROWS (the screens, slot order = item order).",
        " * Only #defines: krk_stubs.S includes it too. */",
        "#ifndef KRK_MENU_ROWS_H",
        "#define KRK_MENU_ROWS_H",
        "",
        *hdr,
        "#endif",
        ""])
    return m, header


def dump(m):
    return json.dumps(m, indent=1)          # the manifest's own format: indent 1, ASCII, no final newline


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the manifest or the header is out of date")
    args = ap.parse_args()
    spec = json.loads(SPEC.read_text())
    raw = MANIFEST.read_text()
    try:
        m, header = build(spec, json.loads(raw))
    except SpecError as e:
        print(f"gen_menu_hosts: {e}", file=sys.stderr)
        return 2
    new = dump(m)
    old_header = HEADER.read_text() if HEADER.exists() else ""
    stale = [p.name for p, a, b in ((MANIFEST, raw, new), (HEADER, old_header, header)) if a != b]
    if args.check:
        if stale:
            print(f"gen_menu_hosts: out of date with {SPEC.name}: {', '.join(stale)}; run tools/gen_menu_hosts.py",
                  file=sys.stderr)
            return 1
        return 0
    if raw != new:
        MANIFEST.write_text(new)
    if old_header != header:
        HEADER.write_text(header)
    print(f"gen_menu_hosts: {'updated ' + ', '.join(stale) if stale else 'up to date'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
