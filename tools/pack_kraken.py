#!/usr/bin/env python3
"""pack_kraken.py - link Kraken as one program and pack it into the build's free flash and SRAM.

Kraken's code was five blobs, each linked on its own into a claim sized by hand, with fixed addresses wherever one blob had
to reach another. This tool replaces that with one link (docs/flash-allocation.md, "How Kraken is placed"):

  plan     1. compile every source of the manifest's "pack" (patches/kraken_ui/link.json, copied in by gen_manifest.py);
           2. link them once with --gc-sections, one output section per file, to measure what each file keeps: its flash
              piece (.text, .rodata) and its SRAM piece (.bss; sram_groups split or pin some of it);
           3. work out the pool: the flash and SRAM windows minus every claim of the build's allocation files, minus the
              "keep_free" ranges they list, minus flash sectors that are not erased in the base image;
           4. pack the pieces into the pool (largest first, first fit) and link the master ELF at those addresses: that is
              the program;
           5. write what the builder (dm32uv-re build_manifest_patch.py, unchanged) builds: a manifest whose blobs place each
              file exactly where the master link put it (one blob per contiguous flash run and compiler-flag set; every
              symbol defined in another blob bound with --defsym to its master address, so blob order does not matter), and
              an allocations file claiming exactly the sectors and SRAM used. "@kraken:sym" references in the manifest
              become "@<blob>:sym" of the blob that defines sym.
  verify   after the builder ran: every byte of every master section equals the built image, and every symbol a blob
           defines has its master address. Copies the master ELF and map into the step's build/ as kraken_ui.elf / .map
           (what the tests read).

A file is placed whole (its .text, .rodata and .bss go into one blob): --defsym can bind a global, never a static.

  lint     no toolchain: the rules that keep every feature on this path (tools/build_image.py step 0, tests/test_pack_lint.py).
           Every krk_*.c / *.S of patches/kraken_ui is in link.json's sources or its not_linked list (with a why); no hand-written
           linker script there; the committed manifest has a "pack" and no "blobs"; every claim of allocations.json says why it
           is "pinned" (only something reached by address may have a fixed claim; ordinary code and .bss are packed).

  pack_kraken.py [--fw ID] plan MANIFEST --base IMAGE --registry ALLOC.json [--extra-allocations F ...] --out DIR
  pack_kraken.py [--fw ID] verify --out DIR --image IMAGE --build STEP/build
  pack_kraken.py lint [--repo DIR]

Runs inside the Docker image (csky-abiv2-elf-* on PATH), like the builder; tools/build_image.py calls it.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import shutil
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import re_tool  # noqa: E402  (loads dm32uv-re's tools with this build's globals)

FLASH_BASE = 0x03000000
SECTOR = 0x1000
FLASH_SECTIONS = (".text", ".text.*", ".rodata", ".rodata.*")
SRAM_SECTIONS = (".bss", ".bss.*", "COMMON")
SHN_ABS, SHN_UNDEF = 0xFFF1, 0
STB_GLOBAL, STB_WEAK = 1, 2


class PackError(Exception):
    pass


def num(v):
    return v if isinstance(v, int) else int(v, 0)


def align_up(v, a):
    return (v + a - 1) & ~(a - 1)


def hx(v):
    return f"{v:#010x}"


# ---- ELF ------------------------------------------------------------------------------------------------------------
class Elf:
    """Sections (with alignment) and every symbol (with binding and section index) of an ELF32 LE file."""

    def __init__(self, path):
        d = pathlib.Path(path).read_bytes()
        if d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
            raise PackError(f"{path}: not an ELF32 little-endian file")
        self.data = d
        (_, _, _, _, _, shoff, _, _, _, _, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", d, 16)
        raw = [struct.unpack_from("<IIIIIIIIII", d, shoff + i * shentsize) for i in range(shnum)]
        names = raw[shstrndx][4]
        cstr = lambda off: d[off:d.index(b"\0", off)].decode()  # noqa: E731
        self.sections = [dict(name=cstr(names + r[0]), type=r[1], flags=r[2], addr=r[3], offset=r[4], size=r[5],
                              link=r[6], align=max(r[8], 1)) for r in raw]
        self.symbols = []
        for s in self.sections:
            if s["type"] != 2:
                continue
            stroff = self.sections[s["link"]]["offset"]
            for i in range(s["size"] // 16):
                n, v, sz, info, _other, shndx = struct.unpack_from("<IIIBBH", d, s["offset"] + i * 16)
                if n:
                    self.symbols.append(dict(name=cstr(stroff + n), value=v, size=sz, bind=info >> 4, type=info & 15,
                                             shndx=shndx))

    def section(self, name):
        return next((s for s in self.sections if s["name"] == name), None)

    def bytes_of(self, s):
        return self.data[s["offset"]:s["offset"] + s["size"]]


# ---- the link map ---------------------------------------------------------------------------------------------------
def map_inputs(map_text):
    """{output section: [(input section, address, size, object)]} from a GNU ld map, in link order."""
    lines = map_text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("Linker script and memory map"))
    out, cur, pend = {}, None, None
    for ln in lines[i + 1:]:
        m = re.match(r"^(\.[^\s]+|/DISCARD/)(?:\s+0x[0-9a-f]+\s+0x[0-9a-f]+.*)?$", ln)
        if m:                                           # an output section line (column 0)
            cur, pend = m.group(1), None
            out.setdefault(cur, [])
            continue
        m = re.match(r"^ (\S+)\s*$", ln)                # an input section name whose numbers wrap to the next line
        if m and not m.group(1).startswith("0x") and m.group(1) not in ("*fill*",):
            pend = m.group(1)
            continue
        m = re.match(r"^ (\S+)?\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S.*\S|\S)\s*$", ln)
        if m and cur is not None:
            name = m.group(1) or pend
            pend = None
            if name is None or name == "*fill*" or name.startswith("*("):
                continue
            out[cur].append((name, int(m.group(2), 16), int(m.group(3), 16), m.group(4)))
            continue
        if not ln.startswith("                "):
            pend = None
    return out


def obj_stem(path):
    """kraken__krk_vox.o / kraken_3__krk_vox.o / libgcc.a(_muldi3.o) -> ("krk_vox", None) / ("_muldi3", "libgcc")."""
    m = re.search(r"([^/()]+)\.a\(([^()]+)\.o\)$", path)
    if m:
        return m.group(2), m.group(1)
    m = re.search(r"__([^/]+)\.o$", path)
    if m:
        return m.group(1), None
    raise PackError(f"input from an unexpected file: {path}")


# ---- spec -----------------------------------------------------------------------------------------------------------
class Spec:
    def __init__(self, manifest_path):
        self.manifest_path = pathlib.Path(manifest_path).resolve()
        self.m = json.loads(self.manifest_path.read_text())
        if "pack" not in self.m:
            raise PackError(f"{manifest_path}: no \"pack\" (run tools/gen_manifest.py)")
        p = self.m["pack"]
        self.src_dir = self.manifest_path.parent
        self.entry = p["entry"]
        self.keep = list(p.get("keep", []))
        self.sources = []
        stems = set()
        for s in p["sources"]:
            stem = pathlib.Path(s["file"]).stem
            if stem in stems:
                raise PackError(f"two sources named {stem}: object names must be unique")
            stems.add(stem)
            self.sources.append(dict(file=s["file"], stem=stem, cflags=list(s["cflags"]), keep=bool(s.get("keep")),
                                     claim=s.get("claim"), sram_claim=s.get("sram_claim")))
        self.by_stem = {s["stem"]: s for s in self.sources}
        self.groups = []
        taken = {}
        for g in p.get("sram_groups", []):
            take = []
            for t in g["take"]:
                stem = pathlib.Path(t["file"]).stem
                if stem not in self.by_stem:
                    raise PackError(f"sram_groups.{g['name']}: {t['file']} is not a source")
                secs = tuple(t.get("sections", SRAM_SECTIONS))
                if (stem, secs) in taken or (stem, SRAM_SECTIONS) in taken:
                    raise PackError(f"sram_groups.{g['name']}: {t['file']} {secs} already taken by {taken.get((stem, secs))}")
                taken[(stem, secs)] = g["name"]
                take.append((stem, secs))
            self.groups.append(dict(name=g["name"], take=take, claim=g.get("claim"),
                                    offset=num(g["offset"]) if "offset" in g else None,
                                    offset_from_end=num(g["offset_from_end"]) if "offset_from_end" in g else None,
                                    max=num(g["max"]) if "max" in g else None, symbols=g.get("symbols")))
        self.pinned = list(self.m.get("allocations", []))
        self.cpu = self.m.get("cpu", "ck803s")

    def refs(self):
        """Every "@kraken:sym" the manifest uses (splice targets, data words, symbols)."""
        found = set()

        def walk(v):
            if isinstance(v, str):
                for m in re.finditer(r"@kraken:([A-Za-z_][\w.$]*)", v):
                    found.add(m.group(1))
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, dict):
                for k, x in v.items():
                    if k not in ("pack", "_comment", "why", "description"):
                        walk(x)
        walk(self.m)
        bad = set(re.findall(r"@kraken_ui\d*:\w+", json.dumps({k: v for k, v in self.m.items() if k != "pack"})))
        if bad:
            raise PackError(f"the manifest still names blobs: {sorted(bad)[:5]}... (write @kraken:sym)")
        return found


# ---- allocations ----------------------------------------------------------------------------------------------------
def subtract(ranges, lo, hi):
    out = []
    for a, b in ranges:
        if hi <= a or b <= lo:
            out.append((a, b))
            continue
        if a < lo:
            out.append((a, lo))
        if hi < b:
            out.append((hi, b))
    return out


def pool(bmp, registry, extras, base_img):
    """(claims, flash runs, sram runs): what is free for Kraken in this build."""
    alloc = bmp.load_allocations(extras, base_path=registry)
    keep_free = []
    for f in [registry, *extras]:
        for k in json.loads(pathlib.Path(f).read_text()).get("keep_free", []):
            keep_free.append((k["space"], num(k["start"]), num(k["end_exclusive"]), k.get("why", "")))
    runs = {}
    for space in ("flash", "sram"):
        free = [alloc["windows"][space]]
        for c in alloc["claims"]:
            if c["space"] == space:
                free = subtract(free, c["lo"], c["hi"])
        for sp, lo, hi, _w in keep_free:
            if sp == space:
                free = subtract(free, lo, hi)
        if space == "flash":
            for lo, hi, _n in alloc["forbidden"]:
                free = subtract(free, lo, hi)
            secs = []
            for a, b in free:
                for s in range(align_up(a, SECTOR), b - SECTOR + 1, SECTOR):
                    if base_img[s - FLASH_BASE:s - FLASH_BASE + SECTOR] == b"\xff" * SECTOR:
                        secs.append(s)
            free = []
            for s in secs:
                if free and free[-1][1] == s:
                    free[-1] = (free[-1][0], s + SECTOR)
                else:
                    free.append((s, s + SECTOR))
        runs[space] = [(a, b) for a, b in free if b - a >= 4]
    return alloc, runs


# ---- linking --------------------------------------------------------------------------------------------------------
def pat(stem, lib=None):
    return f"*{lib}.a:{stem}.o" if lib else f"*__{stem}.o"


def piece_name(kind, stem, lib=None, tag=None):
    base = f".{kind}.{'lg.' if lib else ''}{stem}"
    return f"{base}.{tag}" if tag else base


class Packer:
    def __init__(self, bmp, spec, out, fw):
        self.bmp, self.spec, self.out, self.fw = bmp, spec, pathlib.Path(out), fw
        self.tc = bmp.Toolchain(os.environ.get("CSKYBUILD"), spec.cpu)
        self.obj_dir = self.out / "obj"

    # 1. compile
    def compile(self):
        self.obj_dir.mkdir(parents=True, exist_ok=True)

        def one(s):
            src = (self.spec.src_dir / s["file"]).resolve()
            obj = self.obj_dir / f"kraken__{s['stem']}.o"
            self.tc.run("gcc", [f"-mcpu={self.spec.cpu}", *s["cflags"], "-I", str(self.bmp.API_INCLUDE_DIR),
                                "-c", str(src), "-o", str(obj)], self.obj_dir)
            return s["stem"], obj
        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
            self.objs = dict(ex.map(one, self.spec.sources))
        self.obj_align = {}
        for stem, obj in self.objs.items():
            e = Elf(obj)
            for s in e.sections:
                if s["flags"] & 2 and s["size"]:
                    if s["name"].startswith((".data", ".sdata")) or s["name"] == ".data":
                        raise PackError(f"{stem}: initialised writable data in {s['name']}: nothing copies it to RAM; "
                                        f"make it const or zero-initialised")
                    self.obj_align[(stem, s["name"])] = s["align"]
        self.libgcc = self.tc.libgcc(self.obj_dir)

    def ld(self, script, elf, extra=()):
        mp = elf.with_suffix(".map")
        self.tc.run("ld", ["-L", str(self.out), "-L", str(self.bmp.LINK_DIR), "-T", str(script), "-e", self.spec.entry,
                           "-Map", str(mp), *extra, "-o", str(elf), *map(str, self.objs.values()), self.libgcc], self.out)
        return Elf(elf), mp.read_text()

    def sram_takes(self):
        """{stem: [(tag, sections, group)]}: how each file's .bss is split (groups first, then the rest)."""
        t = {}
        for g in self.spec.groups:
            for i, (stem, secs) in enumerate(g["take"]):
                t.setdefault(stem, []).append((f"{g['name']}{i}", secs, g["name"]))
        return t

    # 2. measure
    def measure(self, roots):
        L = ["/* GENERATED by tools/pack_kraken.py: the measuring link (one output section per file) */",
             "INCLUDE dm32_stock_symbols.ld", f"ENTRY({self.spec.entry})", "SECTIONS", "{", "    . = 0x03000000;"]
        keep = " ".join(self.spec.keep)
        for s in self.spec.sources:
            p = pat(s["stem"])
            body = []
            if keep:
                body.append(f"KEEP({p}({keep}))")
            body.append(f"KEEP({p}(.text .text.*))" if s["keep"] else f"KEEP({p}(.text))")
            body.append(f"{p}({' '.join(FLASH_SECTIONS)})")
            L.append(f"    {piece_name('k', s['stem'])} : ALIGN(4) {{ {' '.join(body)} }}")
        L.append(f"    .k.libgcc : ALIGN(4) {{ *libgcc.a:*({' '.join(FLASH_SECTIONS)}) }}")
        L.append("    . = 0x18000000;")
        takes = self.sram_takes()
        for stem, ts in takes.items():
            for tag, secs, _g in ts:
                if secs != SRAM_SECTIONS:
                    L.append(f"    {piece_name('b', stem, tag=tag)} (NOLOAD) : ALIGN(4) {{ {pat(stem)}({' '.join(secs)}) }}")
        for stem, ts in takes.items():
            for tag, secs, _g in ts:
                if secs == SRAM_SECTIONS:
                    L.append(f"    {piece_name('b', stem, tag=tag)} (NOLOAD) : ALIGN(4) {{ {pat(stem)}({' '.join(secs)}) }}")
        for s in self.spec.sources:
            L.append(f"    {piece_name('b', s['stem'])} (NOLOAD) : ALIGN(4) {{ {pat(s['stem'])}({' '.join(SRAM_SECTIONS)}) }}")
        L.append(f"    .b.libgcc (NOLOAD) : ALIGN(4) {{ *libgcc.a:*({' '.join(SRAM_SECTIONS)}) }}")
        for g in self.spec.groups:
            for sym in g["symbols"] or ():
                L.append(f"    {sym} = 0;                  /* placed by the pack */")
        L += ["    .data : { *(.data .data.* .sdata .sdata.*) }",
              "    ASSERT(SIZEOF(.data) == 0, \"initialised writable data (.data): nothing copies it to RAM\")", "}"]
        script = self.out / "measure.ld"
        script.write_text("\n".join(L) + "\n")
        undef = [a for r in sorted(roots) for a in ("-u", r)]
        elf, mp = self.ld(script, self.out / "measure.elf", ["--gc-sections", *undef])
        # scratch addresses: never leave an ELF or .map behind that a symbol loader (the harness globs **/*.elf, **/*.map) could read
        (self.out / "measure.elf").unlink()
        (self.out / "measure.map").rename(self.out / "measure-map.txt")
        inputs = map_inputs(mp)
        pieces = []
        for osec, ins in inputs.items():
            m = re.match(r"^\.(k|b)\.(.+)$", osec)
            if not m or not ins:
                continue
            kind = m.group(1)
            if osec in (".k.libgcc", ".b.libgcc"):
                members = {}
                for name, addr, size, obj in ins:
                    stem, lib = obj_stem(obj)
                    members.setdefault(stem, []).append((name, addr, size, obj))
                for stem, mi in members.items():
                    pieces.append(self._piece(kind, stem, "libgcc", None, None, mi))
                continue
            rest = m.group(2)
            stem, _, tag = rest.partition(".")
            if stem not in self.spec.by_stem:
                raise PackError(f"measure: unexpected output section {osec}")
            group = next((g for t, _s, g in self.sram_takes().get(stem, []) if t == tag), None) if tag else None
            sec = elf.section(osec)        # the real size: string sections merge within an output section
            pieces.append(self._piece(kind, stem, None, tag or None, group, ins, sec and (sec["addr"], sec["size"])))
        return [p for p in pieces if p["size"]]

    def _piece(self, kind, stem, lib, tag, group, ins, span=None):
        if span:
            lo, hi = span[0], span[0] + span[1]
        else:
            lo = min(a for _n, a, _s, _o in ins)
            hi = max(a + s for _n, a, s, _o in ins)
        seen, secs = set(), []
        for name, addr, size, obj in ins:
            if name in seen:
                raise PackError(f"{stem}: two input sections named {name}; the blob scripts list sections by name")
            seen.add(name)
            secs.append(name)
        al = 4
        for name in secs:
            al = max(al, self.obj_align.get((stem, name), 1) if not lib else 4)
        if lo % al:
            raise PackError(f"{stem}: measured at {lo:#x}, not aligned to {al}")
        return dict(kind=kind, stem=stem, lib=lib, tag=tag, group=group, sections=secs, size=hi - lo, align=al,
                    name=piece_name(kind, stem, lib, tag))

    # 3-4. pack
    def pack(self, pieces, alloc, runs):
        claims = {c["name"]: c for c in alloc["claims"]}
        src = self.spec.by_stem
        flash = [p for p in pieces if p["kind"] == "k"]
        sram = [p for p in pieces if p["kind"] == "b"]
        # flash: files first (largest first, first fit), then libgcc members into runs that hold a file
        fruns = [dict(lo=a, hi=b, cur=a, pieces=[]) for a, b in runs["flash"]]
        pinned_runs = {}
        for p in flash:
            cl = src[p["stem"]]["claim"] if not p["lib"] else None
            if cl:
                c = claims.get(cl)
                if c is None or cl not in self.spec.pinned:
                    raise PackError(f"{p['stem']}: claim {cl} is not one of the manifest's allocations")
                pinned_runs.setdefault(cl, dict(lo=c["lo"], hi=c["hi"], cur=c["lo"], pieces=[], claim=cl))
        order = sorted(flash, key=lambda p: (p["lib"] is not None, -align_up(p["size"], 4), p["stem"]))
        for p in order:
            if p["lib"] is None and src[p["stem"]]["claim"]:
                cands = [pinned_runs[src[p["stem"]]["claim"]]]
            elif p["lib"] is None:
                cands = fruns
            else:
                cands = [r for r in fruns if any(q["lib"] is None for q in r["pieces"])]     # never a pinned claim: shared code
            for r in cands:
                at = align_up(r["cur"], p["align"])
                if at + align_up(p["size"], 4) <= r["hi"]:
                    r["pieces"].append(p)
                    r["cur"] = at + align_up(p["size"], 4)
                    break
            else:
                free = sum(r["hi"] - r["cur"] for r in fruns)
                raise PackError(f"flash is full: {p['name']} ({p['size']} B) fits in no free run "
                                f"({free} B free in all, largest run {max((r['hi'] - r['cur'] for r in fruns), default=0)} B)")
        # lay each run out again with each compiler-flag set contiguous (one blob per set), libgcc last
        blobs = []
        for r in fruns + list(pinned_runs.values()):
            if not r["pieces"]:
                continue
            keyed = {}
            for p in r["pieces"]:
                k = None if p["lib"] else tuple(src[p["stem"]]["cflags"])
                keyed.setdefault(k, []).append(p)
            keys = [k for k in keyed if k is not None]
            keys.sort(key=lambda k: min(self.spec.sources.index(src[p["stem"]]) for p in keyed[k]))
            cur = r["lo"]
            for i, k in enumerate(keys):
                ps = sorted(keyed[k], key=lambda p: self.spec.sources.index(src[p["stem"]]))
                if i == len(keys) - 1:
                    ps += keyed.get(None, [])
                b = dict(cflags=list(k), pieces=[], run=(r["lo"], r["hi"]), claim=r.get("claim"))
                for p in ps:
                    cur = align_up(cur, p["align"])
                    p["addr"] = cur
                    cur += p["size"]
                    b["pieces"].append(p)
                if cur > r["hi"]:
                    raise PackError(f"run {hx(r['lo'])}: {cur - r['hi']} B over after grouping by compiler flags")
                blobs.append(b)
            r["used_hi"] = cur
        # SRAM: groups (pinned in a claim, or packed as one unit), then every other piece on its own
        units = []
        for g in self.spec.groups:
            ps = [next((p for p in sram if p["stem"] == stem and p["tag"] == f"{g['name']}{i}"), None)
                  for i, (stem, _s) in enumerate(g["take"])]
            units.append(dict(group=g, pieces=[p for p in ps if p]))
        for p in sram:
            if p["group"] is None:
                units.append(dict(group=None, pieces=[p]))
        sruns = [dict(lo=a, hi=b, cur=a, units=[]) for a, b in runs["sram"]]
        group_syms = {}

        def lay(unit, at):
            for p in unit["pieces"]:
                at = align_up(at, p["align"])
                p["addr"] = at
                at += p["size"]
            return at

        def usize(unit):
            at = 0
            for p in unit["pieces"]:
                at = align_up(at, p["align"]) + p["size"]
            return at
        for u in sorted(units, key=lambda u: (u["group"] is None or u["group"]["claim"] is None, -usize(u))):
            g = u["group"]
            size = usize(u)
            if g and g["max"] is not None and size > g["max"]:
                raise PackError(f"sram group {g['name']}: {size} B, more than its {g['max']} B")
            stem0 = u["pieces"][0]["stem"] if u["pieces"] else None
            pin = g["claim"] if g else (src[stem0]["sram_claim"] if stem0 else None)
            if pin:
                c = claims.get(pin)
                if c is None or pin not in self.spec.pinned:
                    raise PackError(f"sram: claim {pin} is not one of the manifest's allocations")
                at = c["lo"] + (g["offset"] or 0) if (g and g["offset_from_end"] is None) else \
                    (c["hi"] - g["offset_from_end"] if g else c["lo"])
                if not g:       # a file pinned to an sram claim: append after what is already there
                    at = max([c["lo"]] + [p["addr"] + p["size"] for p in sram if p.get("addr") and c["lo"] <= p["addr"] < c["hi"]])
                end = lay(u, at)
                if end > c["hi"]:
                    raise PackError(f"sram: {g['name'] if g else stem0} ends {end - c['hi']} B past claim {pin}")
            else:
                for r in sruns:
                    at = align_up(r["cur"], max([p["align"] for p in u["pieces"]] + [4]))
                    if at + size <= r["hi"]:
                        r["cur"] = lay(u, at)
                        r["units"].append(u)
                        break
                else:
                    raise PackError(f"SRAM is full: {g['name'] if g else u['pieces'][0]['name']} ({size} B) fits in no "
                                    f"free run ({sum(r['hi'] - r['cur'] for r in sruns)} B free in all)")
            if g and g["symbols"]:
                lo = u["pieces"][0]["addr"] if u["pieces"] else None
                hi = (u["pieces"][-1]["addr"] + u["pieces"][-1]["size"]) if u["pieces"] else None
                group_syms[g["symbols"][0]], group_syms[g["symbols"][1]] = lo, hi
        # claims: per flash run the sectors used from its start; per SRAM run the bytes used from its start
        gen = []
        for r in fruns:
            if r["pieces"]:
                gen.append(dict(name=f"kraken_flash_{r['lo']:08x}", space="flash", start=hx(r["lo"]),
                                end_exclusive=hx(align_up(r["used_hi"], SECTOR)),
                                owner="kraken_ui: code and constants, placed by tools/pack_kraken.py",
                                source="generated per build (out/.../pack/); see docs/flash-allocation.md"))
        for r in sruns:
            if r["units"]:
                gen.append(dict(name=f"kraken_sram_{r['lo']:08x}", space="sram", start=hx(r["lo"]),
                                end_exclusive=hx(align_up(r["cur"], 4)),
                                owner="kraken_ui: .bss, placed by tools/pack_kraken.py",
                                source="generated per build (out/.../pack/); see docs/flash-allocation.md"))
        return blobs, gen, group_syms, fruns, sruns

    # scripts
    def section_lines(self, pieces):
        L = []
        for p in sorted(pieces, key=lambda p: p["addr"]):
            pt = pat(p["stem"], p["lib"])
            body = " ".join(f"KEEP({pt}({s}))" for s in p["sections"])
            L.append(f"    {p['name']} {hx(p['addr'])}{' (NOLOAD)' if p['kind'] == 'b' else ''} : {{ {body} }}")
        return L

    def script(self, path, pieces, group_syms=None, externs=()):
        L = ["/* GENERATED by tools/pack_kraken.py: do not edit (out/ only) */", "INCLUDE dm32_stock_symbols.ld"]
        L += [f"EXTERN({s})" for s in externs]
        L += ["SECTIONS", "{", *self.section_lines(pieces)]
        for k, v in (group_syms or {}).items():
            if v is not None:
                L.append(f"    {k} = {hx(v)};")
        L += [f"    /DISCARD/ : {{ *({' '.join(FLASH_SECTIONS + SRAM_SECTIONS)} .data .data.*) }}", "}"]
        path.write_text("\n".join(L) + "\n")
        return path


def check_overlaps(placed, union):
    """Fail on any two placed pieces that overlap, except pieces of groups pinned in the same claim. Returns the names of the
    pieces that overlap as declared."""
    ok = set()
    ps = sorted(placed, key=lambda p: (p["kind"], p["addr"]))
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if b["kind"] != a["kind"] or b["addr"] >= a["addr"] + a["size"]:
                if b["kind"] != a["kind"]:
                    continue
                break
            ga, gb = union.get(a["tag"] or ""), union.get(b["tag"] or "")
            if ga and ga == gb:
                ok |= {a["name"], b["name"]}
                continue
            raise PackError(f"{a['name']} [{hx(a['addr'])},+{a['size']}) overlaps {b['name']} [{hx(b['addr'])},+{b['size']})")
    return ok


def kraken_symbols(elf, piece_names):
    """{name: value} of the globals defined in Kraken's own sections of the master ELF."""
    secs = {i for i, s in enumerate(elf.sections) if s["name"] in piece_names}
    out = {}
    for s in elf.symbols:
        if s["bind"] in (STB_GLOBAL, STB_WEAK) and s["shndx"] in secs:
            out.setdefault(s["name"], s["value"])
    return out


def plan(args, bmp):
    spec = Spec(args.manifest)
    out = pathlib.Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pk = Packer(bmp, spec, out, args.fw)
    pk.compile()
    roots = spec.refs() | {spec.entry}
    pieces = pk.measure(roots)
    base_img = pathlib.Path(args.base).read_bytes()
    alloc, runs = pool(bmp, args.registry, args.extra_allocations, base_img)
    blobs, gen, group_syms, fruns, sruns = pk.pack(pieces, alloc, runs)

    # the master link: the program. Pieces of groups pinned in the same claim may overlap on purpose (one owner at a time,
    # e.g. the F13 framing state in the tail of the F9/F13 buffer); every other overlap is an error of this tool.
    placed = [p for b in blobs for p in b["pieces"]] + [p for p in pieces if p["kind"] == "b"]
    union = {f"{g['name']}{i}": g["claim"] for g in spec.groups if g["claim"] for i in range(len(g["take"]))}
    overlapping = check_overlaps(placed, union)
    elf, _mp = pk.ld(pk.script(out / "kraken.ld", placed, group_syms), out / "kraken.elf",
                     ["--no-check-sections"] if overlapping else [])
    names = {p["name"] for p in placed}
    ksyms = kraken_symbols(elf, names)
    for r in sorted(roots):
        if r not in {s["name"] for s in elf.symbols if s["shndx"] != SHN_UNDEF}:
            raise PackError(f"the manifest references @kraken:{r}, which no source defines")
    for p in placed:
        s = elf.section(p["name"])
        if s is None or s["addr"] != p["addr"] or s["size"] != p["size"]:
            raise PackError(f"master link moved {p['name']}: planned {hx(p['addr'])}+{p['size']}, got "
                            f"{s and hx(s['addr'])}+{s and s['size']}")

    # the builder's blobs: one per run and flag set; each file's SRAM pieces go with its flash piece's blob
    owner = {}
    for i, b in enumerate(blobs):
        b["name"] = f"kraken_{i}"
        for p in b["pieces"]:
            if not p["lib"]:
                owner[p["stem"]] = i
    for p in pieces:
        if p["kind"] == "b" and not p["lib"]:
            if p["stem"] not in owner:          # a file with only SRAM: any blob with its flags
                i = next((i for i, b in enumerate(blobs) if b["cflags"] == spec.by_stem[p["stem"]]["cflags"]), None)
                if i is None:
                    raise PackError(f"{p['stem']}: no flash piece and no blob with its compiler flags")
                owner[p["stem"]] = i
            blobs[owner[p["stem"]]]["pieces"].append(p)
        elif p["kind"] == "b":
            raise PackError(f"libgcc member {p['stem']} has .bss: not supported")
    sym_blob = {}
    mblobs = []
    rel = lambda f: os.path.relpath(spec.src_dir / f, out)  # noqa: E731
    for i, b in enumerate(blobs):
        mine = {p["name"] for p in b["pieces"]}
        own = kraken_symbols(elf, mine)
        for n in own:
            sym_blob.setdefault(n, b["name"])
        # statics too, for "@kraken:sym" references to a static (first definition wins, as in the builder)
        secs = {k for k, s in enumerate(elf.sections) if s["name"] in mine}
        for s in elf.symbols:
            if s["shndx"] in secs:
                sym_blob.setdefault(s["name"], b["name"])
        stems = [p["stem"] for p in b["pieces"] if not p["lib"] and p["kind"] == "k"]
        stems += [p["stem"] for p in b["pieces"] if p["kind"] == "b" and p["stem"] not in stems]
        defsym = {n: hx(v) for n, v in sorted(ksyms.items()) if n not in own}
        defsym.update({n: hx(v) for n, v in group_syms.items() if v is not None})
        fl = sorted((p for p in b["pieces"] if p["kind"] == "k"), key=lambda p: p["addr"])
        first = fl[0]["name"]
        entry = min(((v, n) for n, v in own.items() if elf.sections[next(
            k for k, s in enumerate(elf.sections) if s["name"] == first)]["addr"] <= v < fl[-1]["addr"] + fl[-1]["size"]),
            default=None)
        if entry is None:
            raise PackError(f"{b['name']}: no global symbol in its flash pieces to use as the entry")
        externs = sorted({s["name"] for s in elf.symbols if s["bind"] == STB_GLOBAL and s["shndx"] in
                          {k for k, sec in enumerate(elf.sections) if sec["name"] in {p["name"] for p in b["pieces"] if p["lib"]}}})
        ld = pk.script(out / f"{b['name']}.ld", b["pieces"], None, externs)
        mb = {"name": b["name"], "sources": [rel(spec.by_stem[s]["file"]) for s in stems], "cflags": b["cflags"],
              "ldflags": [], "linker_script": ld.name, "entry": entry[1], "expect_entry": hx(entry[0]), "defsym": defsym}
        mb["libgcc"] = any(p["lib"] for p in b["pieces"])
        if sum(1 for p in b["pieces"] if p["name"] in overlapping) > 1:
            mb["ldflags"] = ["--no-check-sections"]     # its pieces overlap as declared (checked above)
        mblobs.append(mb)

    m = json.loads(spec.manifest_path.read_text())
    del m["pack"]
    m["blobs"] = mblobs
    m["allocations"] = spec.pinned + [c["name"] for c in gen]

    def fix(v):
        if isinstance(v, str):
            def sub(mt):
                n = mt.group(1)
                if n not in sym_blob:
                    raise PackError(f"@kraken:{n}: not defined in any blob")
                return f"@{sym_blob[n]}:{n}"
            return re.sub(r"@kraken:([A-Za-z_][\w.$]*)", sub, v)
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: (x if k in ("_comment", "why", "description") else fix(x)) for k, x in v.items()}
        return v
    m = fix(m)
    (out / spec.manifest_path.name).write_text(json.dumps(m, indent=1) + "\n")
    (out / "claims.json").write_text(json.dumps({"_comment": ["GENERATED by tools/pack_kraken.py for this build"],
                                                 "allocations": gen}, indent=1) + "\n")
    report = make_report(blobs, gen, fruns, sruns, pieces, alloc)
    (out / "plan.json").write_text(json.dumps(dict(
        manifest=str(out / spec.manifest_path.name), master=str(out / "kraken.elf"),
        blobs=[dict(name=b["name"], pieces=[p["name"] for p in b["pieces"]]) for b in blobs],
        pieces=[{k: p[k] for k in ("name", "kind", "stem", "lib", "addr", "size", "align", "sections")} for p in placed],
        claims=gen, group_symbols=group_syms, report=report), indent=1) + "\n")
    (out / "report.txt").write_text(report["text"])
    print(report["text"], end="")
    return 0


def make_report(blobs, gen, fruns, sruns, pieces, alloc):
    fl = [p for p in pieces if p["kind"] == "k"]
    sr = [p for p in pieces if p["kind"] == "b"]
    used_f = sum(p["size"] for p in fl)
    used_s = sum(p["size"] for p in sr)
    free_f = sum(r["hi"] - r.get("used_hi", r["lo"]) for r in fruns)
    free_s = sum(r["hi"] - r["cur"] for r in sruns)
    lines = ["Kraken placement (tools/pack_kraken.py)",
             f"  flash: {used_f} B of code and constants in {len(blobs)} blobs; {free_f} B free in the pool "
             f"({sum(1 for r in fruns if not r['pieces'])} whole free runs)",
             f"  SRAM:  {used_s} B of state; {free_s} B free in the pool"]
    for r in fruns:
        lines.append(f"    flash run {hx(r['lo'])}-{hx(r['hi'])}: {r.get('used_hi', r['lo']) - r['lo']:6} used, "
                     f"{r['hi'] - r.get('used_hi', r['lo']):6} free")
    for r in sruns:
        lines.append(f"    sram  run {hx(r['lo'])}-{hx(r['hi'])}: {r['cur'] - r['lo']:6} used, {r['hi'] - r['cur']:6} free")
    for c in gen:
        lines.append(f"    claim {c['name']:24} {c['start']}-{c['end_exclusive']}")
    return dict(flash_used=used_f, flash_free=free_f, sram_used=used_s, sram_free=free_s, text="\n".join(lines) + "\n")


def verify(args):
    out = pathlib.Path(args.out)
    pl = json.loads((out / "plan.json").read_text())
    master = Elf(pl["master"])
    img = pathlib.Path(args.image).read_bytes()
    bad = []
    for p in pl["pieces"]:
        s = master.section(p["name"])
        if p["kind"] == "k":
            got = img[s["addr"] - FLASH_BASE:s["addr"] - FLASH_BASE + s["size"]]
            if got != master.bytes_of(s):
                bad.append(f"{p['name']} at {hx(s['addr'])}: built bytes differ from the master link")
    msyms = {(s["name"], s["value"]) for s in master.symbols if s["shndx"] not in (SHN_UNDEF, SHN_ABS)}
    build = pathlib.Path(args.build)
    for b in pl["blobs"]:
        e = Elf(build / f"{b['name']}.elf")
        for s in e.symbols:
            if s["shndx"] in (SHN_UNDEF, SHN_ABS) or s["type"] in (3, 4):     # sections, files
                continue
            if (s["name"], s["value"]) not in msyms:
                bad.append(f"{b['name']}: {s['name']} at {hx(s['value'])}, not where the master link put it")
    if bad:
        print("\n".join(bad[:40]), file=sys.stderr)
        raise PackError(f"verify: {len(bad)} difference(s) between the built blobs and the master link")
    shutil.copyfile(out / "kraken.elf", build / "kraken_ui.elf")
    shutil.copyfile(out / "kraken.map", build / "kraken_ui.map")
    shutil.copyfile(out / "report.txt", build / "pack_report.txt")
    print(f"pack verify: {len(pl['pieces'])} pieces in {len(pl['blobs'])} blobs match the master link")
    return 0


FEATURE_ID = re.compile(r"^F\d+[a-z]?$")


def feature_problems(repo, listed, skipped):
    """Lint of patches/kraken_ui/features/*.json (docs/menu-hosts.md section 3). Adds the specs' sources to `listed`
    (the set of linked sources) and returns the problems: a spec must carry a feature id with a row in docs/feature-status.md,
    name a user-guide page with a Status and a Where line, name existing sources that no other file lists, and name an existing note."""
    repo = pathlib.Path(repo)
    ui = repo / "patches/kraken_ui"
    bad = []
    status = repo / "docs/feature-status.md"
    status_text = status.read_text() if status.exists() else ""
    seen = {}
    for p in sorted((ui / "features").glob("*.json")):
        rel = p.relative_to(repo)
        try:
            f = json.loads(p.read_text())
        except ValueError as e:
            bad.append(f"{rel}: not JSON ({e})")
            continue
        fid = f.get("feature", "")
        if not FEATURE_ID.match(str(fid)):
            bad.append(f"{rel}: \"feature\" must be an F-number like F8b (docs/feature-status.md), got {fid!r}")
        elif not re.search(rf"^\|\s*{fid}\s*\|", status_text, re.M):
            bad.append(f"{rel}: {fid} has no row in docs/feature-status.md")
        guide = f.get("guide", "")
        gpath = (repo / "docs" / guide.split("#")[0]) if guide else None
        if not guide:
            bad.append(f"{rel}: no \"guide\" (the user-guide page, e.g. user-guide/aprs.md)")
        elif not gpath.is_file():
            bad.append(f"{rel}: guide {guide} does not exist under docs/")
        else:
            text = gpath.read_text()
            for tag in ("Status", "Where"):
                if not re.search(rf"^\*\*{tag}:\*\*", text, re.M):
                    bad.append(f"{rel}: guide {guide} has no \"**{tag}:**\" line")
        note = f.get("note")
        if note and not (repo / "docs" / note.split("#")[0]).is_file():
            bad.append(f"{rel}: note {note} does not exist under docs/")
        for s in f.get("sources", []):
            name = s if isinstance(s, str) else s["file"]
            if not (ui / name).is_file():
                bad.append(f"{rel}: source {name} does not exist in patches/kraken_ui")
            if name in listed:
                bad.append(f"{rel}: source {name} is also in link.json sources (list a source in one place)")
            if name in skipped:
                bad.append(f"{rel}: source {name} is in link.json not_linked")
            if name in seen:
                bad.append(f"{rel}: source {name} is also in {seen[name]}")
            seen[name] = rel.name
            listed.add(name)
    return bad


def lint_problems(repo=REPO):
    """The reasons this tree would bypass the pack, as a list of strings (empty: clean)."""
    repo = pathlib.Path(repo)
    ui = repo / "patches/kraken_ui"
    bad = []
    spec = json.loads((ui / "link.json").read_text())
    listed = {(s if isinstance(s, str) else s["file"]) for s in spec["sources"]}
    skipped = spec.get("not_linked", {})
    bad += feature_problems(repo, listed, skipped)
    for f in sorted(list(ui.glob("krk_*.c")) + list(ui.glob("*.S"))):
        if f.name not in listed and f.name not in skipped:
            bad.append(f"{f.relative_to(repo)}: not in link.json \"sources\" (or \"not_linked\" with why): add it there, "
                       f"tools/pack_kraken.py places it (docs/flash-allocation.md)")
    for name, why in skipped.items():
        if name in listed:
            bad.append(f"link.json: {name} is both linked and not_linked")
        if not str(why).strip():
            bad.append(f"link.json not_linked {name}: say why it is not linked")
    for f in sorted(ui.glob("*.ld")):
        bad.append(f"{f.relative_to(repo)}: a hand-written linker script; Kraken is one link placed by tools/pack_kraken.py")
    m = json.loads((ui / "kraken_ui.patch.json").read_text())
    if "blobs" in m or "pack" not in m:
        bad.append("kraken_ui.patch.json: must have \"pack\" and no \"blobs\" (run tools/gen_manifest.py)")
    for c in json.loads((repo / "allocations.json").read_text())["allocations"]:
        if not str(c.get("pinned", "")).strip():
            bad.append(f"allocations.json claim {c['name']}: no \"pinned\" reason. Only state or code reached by ADDRESS may "
                       f"have a fixed claim; everything else is packed and claimed per build")
    return bad


def main():
    if sys.argv[1:2] == ["lint"]:
        ap = argparse.ArgumentParser(description="pack_kraken.py lint")
        ap.add_argument("lint")
        ap.add_argument("--repo", default=str(REPO))
        bad = lint_problems(ap.parse_args().repo)
        for b in bad:
            print(f"pack_kraken lint: {b}", file=sys.stderr)
        if not bad:
            print("pack_kraken lint: every Kraken source goes through the pack")
        return 1 if bad else 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fw", help="firmware build id (default: the reference)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("manifest")
    p.add_argument("--base", required=True, help="the image this step builds on")
    p.add_argument("--registry", required=True, help="dm32uv-re's allocations registry (filtered) for this build")
    p.add_argument("--extra-allocations", action="append", default=[])
    p.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--out", required=True)
    v.add_argument("--image", required=True)
    v.add_argument("--build", required=True)
    args = ap.parse_args()
    try:
        if args.cmd == "verify":
            return verify(args)
        sys.path.insert(0, str(re_tool.RE_TOOLS))
        if args.fw:
            import fwreg
            f = fwreg.get(args.fw)
            if not fwreg.is_reference(f):
                re_tool.apply(json.loads((REPO / "out" / "fw" / f["id"] / "re" / "globals.json").read_text()))
        import build_manifest_patch as bmp
        return plan(args, bmp)
    except PackError as e:
        print(f"pack_kraken: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
