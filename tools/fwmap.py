"""fwmap.py - map addresses of the reference firmware build onto another build of the same firmware, with proof.

Kraken is written against one stock build (firmware/firmwares.json "reference"). The other supported builds are the
same source compiled again with small changes, so nearly every function, table and variable is still there, moved by
a few bytes. FwMap(target) answers "where is reference address X in the target build?" only when it can show that
what sits there is the same thing; otherwise it raises Unresolved and a person decides (firmware/<id>/overrides.json).

How (docs/firmware-builds.md has the long form):

  boot stub      The stub at 0x0300C000 is checked to be the reference's; its literal pool gives the IRAM load image
                 (flash -> 0x00010000), the SRAM data image (flash -> 0x18000800) and the end of IRAM bss.
  functions      Every reference function (dm32uv-re meta/functions.json, instruction boundaries from
                 meta/disasm.jsonl) is looked for in the target near its running offset, as a byte pattern with its
                 PC-relative instructions and literal pools masked out. A found function is then checked whole:
                 every call must reach the mapped callee, every branch out of it the mapped place, every literal
                 the corresponding value. "same" = all of that holds; "moved" = it holds except calls to callees
                 that are themselves not found; "changed" = not found.
  evidence       The literal pools of the matched functions pair reference values with target values. A value seen
                 with one partner everywhere is evidence that the reference data/variable at that address is at the
                 partner address in the target.
  translate(x)   code in a same/moved function: x + its offset. Code in a changed function: a window of 48 bytes
                 each side of x must be equivalent code at exactly one place near the expected offset. IRAM load
                 image and SRAM data image: their own offsets, content checked. Flash data: evidence at x, or
                 evidence on both sides (within 0x400) agreeing on one offset, content checked. RAM: evidence at x
                 or on both sides agreeing. IRAM code: same address (the IRAM image has the same layout in every
                 build checked so far; translate checks the bytes). Kraken's own windows: unchanged.

Run as a script for a report:  python3 tools/fwmap.py l01 [0x0300c494 ...]
"""
import bisect
import collections
import json
import pathlib
import re
import struct
import sys

import fwreg
from csky_pcrel import decode, insn_len

FLASH_BASE, FLASH_END, APP = 0x03000000, 0x03100000, 0x0300C000
IRAM_LO, IRAM_HI = 0x00010000, 0x00058000
SRAM_LO, SRAM_HI = 0x18000000, 0x18008000
KRAKEN_FLASH = (0x030E1000, 0x03100000)       # allocations.json windows: erased in every supported build
KRAKEN_SRAM = (0x18004C00, 0x18008000)
STUB_POOL = 0x0300C0B8                          # boot stub literal pool (dm32uv-re derive_iram.py)
STUB_END = 0x0300C0E4
WINDOW = 48                                     # bytes each side of a code address in a changed function
SEARCH = 0x400                                  # how far around the expected offset a changed site is looked for
BRACKET = 0x400                                 # evidence on both sides must be this close
FAR_BRACKET = 0x1400                            # or, agreeing, at most this far apart in all (large arrays)


class Unresolved(Exception):
    pass


def u32(img, a):
    return struct.unpack_from("<I", img, a - FLASH_BASE)[0]


def region(v):
    if FLASH_BASE <= v < FLASH_END:
        return "flash"
    if IRAM_LO <= v < 0x00060000:
        return "ram"
    if SRAM_LO <= v < SRAM_HI:
        return "sram"
    return None


def layout(img):
    """The boot stub's ranges: iram_src, iram_len, sram_src, sram_len, bss_end, app_end."""
    p = [u32(img, a) for a in range(STUB_POOL, STUB_END, 4)]
    fixed = {0: 0x10000, 1: 0xE000F000, 2: 0x0300002D, 3: 0x18000800, 4: 0x18004BFC, 6: 0x10000, 7: 0x2F098, 9: 0x2F098}
    if any(p[i] != v for i, v in fixed.items()):
        raise Unresolved(f"boot stub literal pool at {STUB_POOL:#x} is not the known one: {[hex(x) for x in p]}")
    end = len(img)
    while end > 0 and img[end - 1] == 0xFF:
        end -= 1
    return dict(iram_src=p[8], iram_len=0x2F098 - 0x10000, sram_src=p[5], sram_len=0x18004BFC - 0x18000800,
                bss_end=p[10], app_end=FLASH_BASE + end)


class Func:
    __slots__ = ("addr", "size", "name", "insns", "data", "flash")

    def __init__(self, addr, size, name):
        self.addr, self.size, self.name = addr, size, name
        self.insns = []           # (addr, len) in execution address space
        self.data = []            # (lo, hi) bytes that are not instructions (pools, tables)
        self.flash = addr         # where the bytes are in the reference flash (IRAM functions: in the load image)

    def __repr__(self):
        return f"{self.name}@{self.addr:#x}"


class Reference:
    """The reference build: image, layout, functions with instruction boundaries."""

    def __init__(self):
        self.fw = fwreg.reference()
        self.img = fwreg.load_base(self.fw)
        self.lay = layout(self.img)
        re_root = fwreg.re_root()
        meta = json.loads((re_root / "meta/functions.json").read_text())
        flist = meta["functions"] if isinstance(meta, dict) else meta
        dis = {}
        with open(re_root / "meta/disasm.jsonl") as f:
            for line in f:
                d = json.loads(line)
                dis[int(d["addr"], 16)] = d
        self.funcs = []
        L = self.lay
        for m in flist:
            a, n = int(m["addr"], 16), m["size"]
            if n < 2:
                continue
            fn = Func(a, n, m["name"])
            if a < 0x00060000:
                if not (IRAM_LO <= a < IRAM_LO + L["iram_len"]):
                    continue
                fn.flash = L["iram_src"] + (a - IRAM_LO)
            elif not (APP <= a < L["app_end"]) or L["iram_src"] <= a < L["iram_src"] + L["iram_len"] \
                    or L["sram_src"] <= a < L["sram_src"] + L["sram_len"]:
                continue                                  # the IRAM/SRAM images are described at their run addresses
            d = dis.get(a)
            if d and d["size"] == n:
                fn.insns = [(int(i["addr"], 16), i["length"]) for i in d["instructions"]]
                fn.data = [(int(x, 16), int(y, 16)) for x, y in d.get("undecoded_ranges", [])]
            else:
                fn.insns, fn.data = sweep(self.img, fn.flash, a, n)
            self.funcs.append(fn)
        self.funcs.sort(key=lambda f: f.addr)
        self.starts = [f.addr for f in self.funcs]
        self.by_addr = {f.addr: f for f in self.funcs}

    def containing(self, x):
        i = bisect.bisect_right(self.starts, x) - 1
        if i >= 0:
            f = self.funcs[i]
            if x < f.addr + f.size:
                return f
        return None


def sweep(img, flash, addr, size):
    """Instruction boundaries of a function the corpus has no disassembly for: a linear sweep that skips the literal
    words its own lrw instructions point at."""
    pools = set()
    for _ in range(2):
        insns, off = [], 0
        while off < size:
            pc = addr + off
            if pc & ~3 in pools and pc % 4 == 0:
                off += 4
                continue
            n = insn_len(img, flash - FLASH_BASE + off)
            insns.append((pc, n))
            d = decode(img, flash - FLASH_BASE + off, pc)
            if d and d[0] == "pool":
                pools.add(d[1])
            off += n
    data = sorted((p, p + 4) for p in pools if addr <= p < addr + size)
    return insns, data


_REF = None


def reference():
    global _REF
    if _REF is None:
        _REF = Reference()
    return _REF


class FwMap:
    def __init__(self, fw):
        self.ref = reference()
        self.fw = fwreg.get(fw) if isinstance(fw, str) else fw
        self.img = fwreg.load_base(self.fw)
        self.lay = layout(self.img)
        r = self.ref
        if self.img[:APP - FLASH_BASE] != r.img[:APP - FLASH_BASE]:
            raise Unresolved("the target's boot region differs from the reference's (it is composed from it)")
        stub = slice(APP - FLASH_BASE, STUB_POOL - FLASH_BASE)
        if self.img[stub] != r.img[stub]:
            raise Unresolved("the boot stub code at 0x0300C000 differs from the reference's")
        if self.lay["iram_len"] != r.lay["iram_len"] or self.lay["bss_end"] != r.lay["bss_end"]:
            raise Unresolved("IRAM layout differs")
        self.d_iram = self.lay["iram_src"] - r.lay["iram_src"]
        self.d_sram = self.lay["sram_src"] - r.lay["sram_src"]
        self.delta = {}           # ref function addr -> target addr - ref addr
        self.status = {}          # ref function addr -> same / moved / changed
        self._match()
        self._evidence()
        self._verify_funcs()

    # ---- bytes of either build at an execution address (IRAM addresses read from the load image) ----
    def _flash(self, which, x):
        lay = self.ref.lay if which == "ref" else self.lay
        if IRAM_LO <= x < IRAM_LO + lay["iram_len"]:
            return lay["iram_src"] + (x - IRAM_LO)
        return x

    def _rb(self, x, n):
        f = self._flash("ref", x) - FLASH_BASE
        return self.ref.img[f:f + n]

    def _tb(self, x, n):
        f = self._flash("tgt", x) - FLASH_BASE
        return self.img[f:f + n]

    # ---- 1. find every reference function in the target ----
    def _pattern(self, f):
        r = self.ref
        mask = bytearray(f.size)
        for a, n in f.insns:
            o = a - f.addr
            d = decode(r.img, f.flash - FLASH_BASE + o, a)
            if d and (d[0] in ("call", "pool") or not (f.addr <= d[1] < f.addr + f.size)):
                mask[o:o + n] = b"\x01" * n
        for lo, hi in f.data:
            mask[max(0, lo - f.addr):max(0, hi - f.addr)] = b"\x01" * max(0, min(hi, f.addr + f.size) - max(lo, f.addr))
        body = r.img[f.flash - FLASH_BASE:f.flash - FLASH_BASE + f.size]
        if f.size - sum(mask) < 10:
            return None
        return re.compile(b"".join(b"." if mask[k] else re.escape(body[k:k + 1]) for k in range(f.size)), re.S)

    def _match(self):
        r, t = self.ref, self.img
        d_run = 0
        for f in r.funcs:
            if f.addr < 0x00060000:                       # IRAM: same layout, its own offset
                pat = self._pattern(f)
                y = f.flash + self.d_iram - FLASH_BASE
                if pat is None or pat.fullmatch(t, y, y + f.size):
                    self.delta[f.addr] = 0
                continue
            pat = self._pattern(f)
            if pat is None:
                continue
            lo = max(APP, f.addr + d_run - 0x4000) - FLASH_BASE
            hi = min(self.lay["app_end"], f.addr + d_run + 0x4000 + f.size) - FLASH_BASE
            hits = [m.start() for m in pat.finditer(t, lo, hi)]
            if len(hits) == 1:
                self.delta[f.addr] = FLASH_BASE + hits[0] - f.addr
                d_run = self.delta[f.addr]
        # second pass: short or repeated functions, at an offset one of their found neighbours has
        known = sorted(self.delta)
        for f in r.funcs:
            if f.addr in self.delta or f.addr < 0x00060000:
                continue
            pat = self._pattern(f)
            if pat is None:
                continue
            i = bisect.bisect_left(known, f.addr)
            cands = {self.delta[known[j]] for j in (i - 1, i) if 0 <= j < len(known) and known[j] >= APP}
            ok = [d for d in cands if pat.fullmatch(t, f.addr + d - FLASH_BASE, f.addr + d - FLASH_BASE + f.size)]
            if len(ok) == 1:
                self.delta[f.addr] = ok[0]

    def fn_target(self, a):
        """Target address of the reference function starting at a, or None."""
        if a in self.delta:
            return a + self.delta[a]
        return None

    # ---- 2. literal-pool evidence ----
    def _pools(self, f):
        """(ref pool address, target pool address) of every lrw/jmpi/jsri in a matched function."""
        d = self.delta[f.addr]
        out = []
        for a, n in f.insns:
            o = a - f.addr
            dr = decode(self.ref.img, f.flash - FLASH_BASE + o, a)
            if dr and dr[0] == "pool":
                dt = decode(self.img, self._flash("tgt", a + d) - FLASH_BASE, a + d)
                if dt and dt[0] == "pool":
                    out.append((dr[1], dt[1]))
        return out

    def _evidence(self):
        votes = collections.defaultdict(collections.Counter)
        pools = collections.defaultdict(set)
        for f in self.ref.funcs:
            if f.addr not in self.delta:
                continue
            for pa, pt in self._pools(f):
                pools[pa].add(pt)
                ok = lambda p: FLASH_BASE <= p < FLASH_END - 4 or IRAM_LO <= p < IRAM_LO + self.ref.lay["iram_len"]
                if not (ok(pa) and ok(pt)):
                    continue
                v, w = u32(self.ref.img, self._flash("ref", pa)), u32(self.img, self._flash("tgt", pt))
                votes[v][w] += 1
        self.ev = {"flash": {}, "ram": {}, "sram": {}}
        lim = {"flash": 0x8000, "ram": 0x400, "sram": 0x400}
        for v, c in votes.items():
            kind = region(v)
            if kind is None:
                continue
            (w, n), total = c.most_common(1)[0], sum(c.values())
            if region(w) != kind or abs(w - v) > lim[kind] or n != total:
                continue                                  # a constant that changed, or conflicting partners
            self.ev[kind][v] = w
        self.ev_keys = {k: sorted(v) for k, v in self.ev.items()}
        # literal words: where each matched function's lrw/jmpi/jsri reads in the target (one place only)
        self.pool_map = {pa: next(iter(ts)) for pa, ts in pools.items() if len(ts) == 1}

    # ---- 3. check each matched function whole ----
    def value_equiv(self, v, w):
        if v == w:
            return True
        kind = region(v)
        if kind is None or region(w) != kind:
            return False
        if kind == "flash":
            t = self.fn_target(v)
            if t is not None:
                return t == w
            R, T = self.ref.lay, self.lay
            for src, n in (("iram_src", "iram_len"), ("sram_src", "sram_len")):
                if R[src] <= v <= R[src] + R[n]:              # the image or one past its end (copy-loop bounds)
                    return w == v - R[src] + T[src]
        return self.ev[kind].get(v) == w

    def _verify_funcs(self):
        known = sorted(a for a in self.delta if a >= APP)
        for f in self.ref.funcs:
            if f.addr in self.delta or f.addr < APP or self._pattern(f) is not None:
                continue
            i = bisect.bisect_left(known, f.addr)
            cands = {self.delta[known[j]] for j in (i - 1, i) if 0 <= j < len(known)}
            ok = [d for d in cands if all(self.insn_equiv(a, a + d, f, d) is not False for a, _ in f.insns)
                  and all(self._rb(p, 1) == self._tb(p + d, 1) for lo, hi in f.data for p in range(lo, hi))]
            if len(ok) == 1:
                self.delta[f.addr] = ok[0]
        for f in self.ref.funcs:
            if f.addr not in self.delta:
                self.status[f.addr] = "changed"
                continue
            d, st = self.delta[f.addr], "same"
            for a, n in f.insns:
                res = self.insn_equiv(a, a + d, f, d)
                if res is False:
                    st = "changed"
                    break
                if res is None:
                    st = "moved"
            if st != "changed":
                for lo, hi in f.data:
                    for p in range((lo + 3) & ~3, hi - 3, 4):
                        if not self.value_equiv(struct.unpack("<I", self._rb(p, 4))[0],
                                                struct.unpack("<I", self._tb(p + d, 4))[0]):
                            st = "changed"
                            break
            self.status[f.addr] = st
            if st == "changed":
                del self.delta[f.addr]

    def insn_equiv(self, a, b, f=None, fd=None):
        """Is the instruction at reference a the same as the one at target b? True / False / None (it reaches code
        that is not found in the target, so only its shape could be compared)."""
        try:
            res = self._insn_equiv(a, b, f)
        except (IndexError, struct.error):
            return False                                          # runs off the end of the target image
        if res is False:
            n = insn_len(self.ref.img, self._flash("ref", a) - FLASH_BASE)
            if n == 4 and a % 4 == 0 and b % 4 == 0:
                # a literal word the disassembly took for an instruction: compare it as a value
                v, w = struct.unpack("<I", self._rb(a, 4))[0], struct.unpack("<I", self._tb(b, 4))[0]
                if v != w and self.value_equiv(v, w):
                    return True
        return res

    def _insn_equiv(self, a, b, f):
        n = insn_len(self.ref.img, self._flash("ref", a) - FLASH_BASE)
        rb, tb = self._rb(a, n), self._tb(b, n)
        if insn_len(self.img, self._flash("tgt", b) - FLASH_BASE) != n:
            return False
        dr = decode(self.ref.img, self._flash("ref", a) - FLASH_BASE, a)
        if dr is None or not (FLASH_BASE <= dr[1] < FLASH_END or IRAM_LO <= dr[1] < IRAM_HI):
            return rb == tb
        dt = decode(self.img, self._flash("tgt", b) - FLASH_BASE, b)
        if dt is None or dt[0] != dr[0]:
            return False
        # same opcode and register: compare with the PC-relative field cleared
        hr, ht = rb[0] | rb[1] << 8, tb[0] | tb[1] << 8
        if n == 2:
            keep = 0xFCE0 if (hr & 0xFC00) == 0x1000 else 0xFC00     # lrw16 keeps rz (bits 7:5)
        else:
            keep = 0xFC00 if dr[0] == "call" else 0xFFFF             # bsr32: offset starts in the first halfword
        if hr & keep != ht & keep:
            return False
        kind, rt = dr
        tt = dt[1]
        if kind == "pool":
            if f is not None and f.addr <= rt < f.addr + f.size and tt - rt != b - a:
                return False                                       # its own pool must not move inside it
            return self.value_equiv(struct.unpack("<I", self._rb(rt, 4))[0], struct.unpack("<I", self._tb(tt, 4))[0])
        if f is not None and f.addr <= rt < f.addr + f.size:
            return tt - rt == b - a
        if IRAM_LO <= rt < IRAM_HI:
            return tt == rt
        g = self.ref.containing(rt)
        if g is not None and g.addr in self.delta:
            return tt == rt + self.delta[g.addr]
        if tt - rt == b - a or self._dest_equiv(rt, tt, g):
            return None                                            # shared tail / unfound code that looks the same
        return None if kind == "call" else False

    def _dest_equiv(self, rt, tt, g, count=6):
        """The first `count` instructions at reference rt and target tt are equivalent (one level: their own branches
        and calls are not followed)."""
        if g is None:
            return self._rb(rt, 8) == self._tb(tt, 8)
        seen = 0
        for ia, n in g.insns:
            if ia < rt:
                continue
            if seen == count:
                break
            if self._insn_equiv_flat(ia, ia + (tt - rt)) is False:
                return False
            seen += 1
        return seen > 0

    def _insn_equiv_flat(self, a, b):
        n = insn_len(self.ref.img, self._flash("ref", a) - FLASH_BASE)
        rb, tb = self._rb(a, n), self._tb(b, n)
        if rb == tb:
            return True
        if len(tb) != n:
            return False
        dr = decode(self.ref.img, self._flash("ref", a) - FLASH_BASE, a)
        dt = decode(self.img, self._flash("tgt", b) - FLASH_BASE, b)
        if dr is None or dt is None or dr[0] != dt[0]:
            if n == 4 and a % 4 == 0 and b % 4 == 0:
                return self._ptr_equiv(struct.unpack("<I", rb)[0], struct.unpack("<I", tb)[0], 1)
            return False
        if dr[0] == "pool":
            return self._ptr_equiv(struct.unpack("<I", self._rb(dr[1], 4))[0],
                                   struct.unpack("<I", self._tb(dt[1], 4))[0], 1)
        return None

    def _shape_equiv(self, a, b):
        if self._shape_equiv1(a, b):
            return True
        # half of a literal word decoded as an instruction: compare the aligned word as a pointer
        if (b - a) % 4 == 0:
            p, q = a & ~3, b & ~3
            try:
                return self._ptr_equiv(struct.unpack("<I", self._rb(p, 4))[0], struct.unpack("<I", self._tb(q, 4))[0], 2)
            except struct.error:
                return False
        return False

    def _shape_equiv1(self, a, b):
        """Same instruction in shape: same opcode and registers; literals equivalent; a call's callee the mapped one
        when it is known; a branch's target not compared (used only around sites in changed functions)."""
        try:
            n = insn_len(self.ref.img, self._flash("ref", a) - FLASH_BASE)
            if insn_len(self.img, self._flash("tgt", b) - FLASH_BASE) != n:
                return False
            rb, tb = self._rb(a, n), self._tb(b, n)
            if rb == tb:
                return True
            dr = decode(self.ref.img, self._flash("ref", a) - FLASH_BASE, a)
            dt = decode(self.img, self._flash("tgt", b) - FLASH_BASE, b)
            if dr is None or dt is None or dr[0] != dt[0]:
                return n == 4 and a % 4 == 0 and b % 4 == 0 and self._ptr_equiv(
                    struct.unpack("<I", rb)[0], struct.unpack("<I", tb)[0], 1)
            hr, ht = rb[0] | rb[1] << 8, tb[0] | tb[1] << 8
            keep = (0xFCE0 if (hr & 0xFC00) == 0x1000 else 0xFC00) if n == 2 else (0xFC00 if dr[0] == "call" else 0xFFFF)
            if hr & keep != ht & keep:
                return False
            if dr[0] == "pool":
                return self._ptr_equiv(struct.unpack("<I", self._rb(dr[1], 4))[0],
                                       struct.unpack("<I", self._tb(dt[1], 4))[0], 1)
            if dr[0] == "call":
                g = self.ref.containing(dr[1])
                if IRAM_LO <= dr[1] < IRAM_HI:
                    return dt[1] == dr[1]
                if g is not None and g.addr == dr[1] and g.addr in self.delta:
                    return dt[1] == dr[1] + self.delta[g.addr]
            return True
        except (IndexError, struct.error):
            return False

    def _lin_insns(self, x, before, after):
        """Instruction addresses of reference code around x that no function covers: a linear decode started a little
        before x at the alignment that lands on x; forward it stops after a return or an unconditional jump."""
        for s in range(x - before, x - before + 4, 2):
            a, out = s, []
            while a < x:
                out.append(a)
                a += insn_len(self.ref.img, a - FLASH_BASE)
            if a != x:
                continue
            while a < x + after:
                out.append(a)
                h = self.ref.img[a - FLASH_BASE] | self.ref.img[a - FLASH_BASE + 1] << 8
                k = insn_len(self.ref.img, a - FLASH_BASE)
                a += k
                if (k == 2 and (h == 0x783C or 0x1480 <= h <= 0x149F or (h & 0xFC00) == 0x0400 or h & 0xFFC3 == 0x7800)) \
                        or (k == 4 and h == 0xE800):
                    break
            return out
        return [x]

    def shape_window(self, a, b, lo, hi):
        f = self.ref.containing(a)
        return f is not None and all(self._shape_equiv(ia, ia + (b - a)) for ia, _n in f.insns if lo <= ia < hi)

    def code_equiv(self, a, b, lo, hi):
        """Reference code [lo, hi) around a against the target around b, instruction by instruction, on the
        reference function's boundaries. True only if every instruction is equivalent (an unknown callee counts)."""
        f = self.ref.containing(a)
        if f is None:
            return False
        for ia, n in f.insns:
            if lo <= ia < hi:
                if self.insn_equiv(ia, ia + (b - a)) is False:
                    return False
        for p, q in f.data:
            for x in range(max(p, lo) & ~3, min(q, hi), 4):
                if x >= p and not self._words_equiv(x, x + (b - a), 4):
                    return False
        return True

    # ---- translate ----
    def _near_deltas(self, x):
        r = self.ref
        i = bisect.bisect_right(r.starts, x)
        ds = []
        for j in range(i - 3, i + 3):
            if 0 <= j < len(r.starts) and r.starts[j] in self.delta and r.starts[j] >= APP:
                ds.append(self.delta[r.starts[j]])
        if not ds:                                       # a run of changed functions: the nearest found on each side
            for rng in (range(i - 1, max(-1, i - 13), -1), range(i, min(len(r.starts), i + 12))):
                for j in rng:
                    if r.starts[j] in self.delta and r.starts[j] >= APP:
                        ds.append(self.delta[r.starts[j]])
                        break
        return ds

    def _ptr_equiv(self, v, w, depth=2):
        """Do reference pointer v and target pointer w point at the same thing? Stricter value_equiv first; then
        code (the function's offset, or equivalent instructions there) or data (the same bytes, or words that point
        at the same things, `depth` levels down)."""
        if self.value_equiv(v, w):
            return True
        if region(v) in ("ram", "sram") and region(w) == region(v):
            try:
                return self.translate(v)[0] == w           # a variable: by the RAM rules (evidence on both sides)
            except Unresolved:
                return False
        if region(v) != "flash" or region(w) != "flash" or abs(w - v) > 0x8000:
            return False
        f = self.ref.containing(v)
        if f is not None and not any(lo <= v < hi for lo, hi in f.data):
            if f.addr in self.delta:
                return w == v + self.delta[f.addr]
            return self._dest_equiv(v, w, f)
        if self._rb(v, 16) == self._tb(w, 16):
            return True
        if f is None and v % 2 == 0 and w % 2 == 0 and self._code_at(v, w, 16):
            return True
        if depth and v % 4 == 0 and w % 4 == 0:
            rv, tw = self._rb(v, 16), self._tb(w, 16)
            return all(self._ptr_equiv(p, q, depth - 1)
                       for p, q in zip(struct.unpack("<4I", rv), struct.unpack("<4I", tw)))
        return False

    def _words_equiv(self, x, y, n):
        """Aligned words around reference x and target y: equal or pointing at the same things."""
        a, b = x & ~3, y & ~3
        rv, tw = self._rb(a, n), self._tb(b, n)
        if len(rv) != n or len(tw) != n:
            return False
        return all(self._ptr_equiv(p, q, 1) for p, q in zip(struct.unpack(f"<{n // 4}I", rv),
                                                            struct.unpack(f"<{n // 4}I", tw)))

    def _data_at(self, x, y, n=16):
        """Flash data at reference x and target y are the same: aligned words that are equal or point at the same
        things; unaligned, the same bytes."""
        if y < APP or y + n > FLASH_END:
            return False
        if x % 4 or y % 4:
            return self._rb(x, n) == self._tb(y, n)
        rv, tw = self._rb(x, n), self._tb(y, n)
        return all(self._ptr_equiv(p, q, 2) for p, q in zip(struct.unpack(f"<{n // 4}I", rv),
                                                            struct.unpack(f"<{n // 4}I", tw)))

    def _code_at(self, x, y, n=32):
        """Code at reference x (no function covers it) and target y: a linear decode of n bytes, instruction by
        instruction equivalent (the instructions' own targets compared one level deep)."""
        off = 0
        while off < n:
            a = x + off
            k = insn_len(self.ref.img, a - FLASH_BASE)
            if self._insn_equiv_flat(a, y + off) is False:
                return False
            h = self.ref.img[a - FLASH_BASE] | self.ref.img[a - FLASH_BASE + 1] << 8
            if k == 2 and (h == 0x783C or 0x1480 <= h <= 0x149F or (h & 0xFC00) == 0x0400 or h & 0xFFC3 == 0x7800):
                break                                     # rts / pop16 / br16 / jmp16: what follows is not this path
            if k == 4 and (h == 0xE800 or (h & 0xFFE0) in (0xE8C0, 0xEBC0)):
                break                                     # br32 / jmp32-like
            off += k
        return off > 0

    def translate(self, x):
        """(target address, how) for reference address x. Raises Unresolved."""
        r, L, T = self.ref, self.ref.lay, self.lay
        if KRAKEN_FLASH[0] <= x < KRAKEN_FLASH[1] or KRAKEN_SRAM[0] <= x < KRAKEN_SRAM[1]:
            return x, "kraken window"
        if x == L["iram_src"] - IRAM_LO:
            return T["iram_src"] - IRAM_LO, "IRAM pc bias (load image - 0x10000)"
        if FLASH_BASE <= x < APP:
            return x, "boot region (shared)"
        if L["iram_src"] <= x < L["iram_src"] + L["iram_len"]:
            y = x + self.d_iram
            ix = IRAM_LO + (x - L["iram_src"])
            f = r.containing(ix)
            if f and not self.code_equiv(ix, ix, ix - WINDOW, ix + WINDOW):
                raise Unresolved(f"IRAM load image {x:#x} (IRAM {ix:#x}, {f.name}): code differs")
            if not f and not self._words_equiv(ix, ix, 16):
                raise Unresolved(f"IRAM load image {x:#x}: data differs")
            return y, f"IRAM load image ({f.name if f else 'data'})"
        if L["sram_src"] <= x < L["sram_src"] + L["sram_len"]:
            y = x + self.d_sram
            if r.img[x - FLASH_BASE:x - FLASH_BASE + 4] != self.img[y - FLASH_BASE:y - FLASH_BASE + 4]:
                raise Unresolved(f"SRAM data image {x:#x}: differs")
            return y, "SRAM data image"
        if APP <= x < L["app_end"]:
            return self._flash_tr(x)
        if IRAM_LO <= x < IRAM_LO + L["iram_len"]:
            f = r.containing(x)
            if f is not None:
                if not self.code_equiv(x, x, x - WINDOW, x + WINDOW):
                    raise Unresolved(f"IRAM {x:#x} ({f.name}): code differs")
                return x, f"IRAM code ({f.name})"
            if not self._words_equiv(x, x, 8):
                raise Unresolved(f"IRAM {x:#x}: initialised data differs")
            return x, "IRAM data"
        if IRAM_LO + L["iram_len"] <= x < 0x00060000:
            if x >= L["bss_end"]:
                return x, "above IRAM bss (unchanged)"
            return self._ev_tr(x, "ram")
        if SRAM_LO <= x < KRAKEN_SRAM[0]:
            ev = self.ev["sram"]
            if len(ev) >= 50 and all(w == v for v, w in ev.items()) and L["sram_len"] == T["sram_len"]:
                return x, f"SRAM: unchanged (all {len(ev)} stock references keep their address)"
            return self._ev_tr(x, "sram")
        raise Unresolved(f"{x:#x} is in no region this map knows")

    def _ev_tr(self, x, kind):
        ev, keys = self.ev[kind], self.ev_keys[kind]
        if x in ev:
            return ev[x], f"{kind}: referenced by stock code"
        i = bisect.bisect_left(keys, x)
        if 0 < i < len(keys):
            lo, hi = keys[i - 1], keys[i]
            dl, dh = ev[lo] - lo, ev[hi] - hi
            if dl == dh and x - lo <= BRACKET and hi - x <= BRACKET:
                return x + dl, f"{kind}: between references {lo:#x} and {hi:#x}, both {dl:+#x}"
            if dl == dh and hi - lo <= FAR_BRACKET:
                return x + dl, f"{kind}: between references {lo:#x} and {hi:#x} ({hi - lo:#x} apart), both {dl:+#x}"
            raise Unresolved(f"{kind} {x:#x}: the references around it disagree or are far "
                             f"({lo:#x} {dl:+#x}, {hi:#x} {dh:+#x})")
        raise Unresolved(f"{kind} {x:#x}: no stock reference near it")

    WINDOWS = ((WINDOW, WINDOW), (16, WINDOW), (WINDOW, 16), (24, 24))

    def _flash_tr(self, x):
        try:
            return self._flash_tr1(x)
        except Unresolved as e:
            # the second halfword of a 32-bit instruction: translate the instruction
            a = x - 2
            if x % 2 == 0 and APP <= a and insn_len(self.ref.img, a - FLASH_BASE) == 4 and self.ref.containing(a) is None \
                    and len(self._lin_insns(a, WINDOW, 32)) > 1 and a not in self._lin_insns(x, WINDOW, 32):
                try:
                    y, how = self._flash_tr1(a)
                    return y + 2, how + "; +2: the second halfword of that instruction"
                except Unresolved:
                    pass
            raise e

    def _flash_tr1(self, x):
        r = self.ref
        f = r.containing(x)
        in_data = f is not None and any(lo <= x < hi for lo, hi in f.data)
        if f is not None and f.addr in self.delta:
            d = self.delta[f.addr]
            ok = self._data_at(x & ~3, (x & ~3) + d, 4) if in_data else \
                (x == f.addr or self.code_equiv(x, x + d, x - WINDOW, x + WINDOW))
            if not ok:
                raise Unresolved(f"{x:#x} in {f.name}: function matched but the bytes here differ")
            return x + d, f"{f.name} ({self.status[f.addr]})" + ("" if x == f.addr else f" +{x - f.addr:#x}")
        ds = self._near_deltas(x)
        if f is not None and not in_data:
            # code in a changed function: a unique equivalent window near the expected offset
            if not ds:
                raise Unresolved(f"{x:#x} in {f.name}: changed, and no matched neighbour")
            for before, after in self.WINDOWS:
                hits = []
                for d in range(min(ds) - SEARCH & ~1, max(ds) + SEARCH + 2, 2):
                    if not self._shape_equiv(x, x + d):
                        continue
                    if self.shape_window(x, x + d, max(f.addr, x - before), min(f.addr + f.size, x + after)):
                        hits.append(d)
                if len(hits) == 1:
                    return x + hits[0], (f"{f.name} (CHANGED; {before} bytes before and {after} after the same in shape)"
                                         + ("" if x == f.addr else f" +{x - f.addr:#x}"))
                if len(hits) > 1:
                    break
            raise Unresolved(f"{x:#x} in {f.name} (changed): no unique equivalent place near it")
        # data stock code points at: matched (fully checked) functions load x, and their target copies load this
        if x in self.ev["flash"] and not (f is not None and not in_data):
            y = self.ev["flash"][x]
            same = self._data_at(x, y) if x % 4 == 0 else self._rb(x, 8) == self._tb(y, 8)
            return y, "flash: referenced by stock code" + ("" if same else " (its content differs in this build)")
        # a literal word a matched function reads
        if x & ~3 in self.pool_map:
            y = self.pool_map[x & ~3] + (x & 3)
            if self._words_equiv(x, y, 4):
                return y, "flash literal word read by a matched function"
        # a literal word: where the translated readers of it read in the target
        if x % 4 == 0:
            got = self._pool_by_readers(x)
            if got is not None:
                return got
        # code that no function covers (shared tails, switch case blocks): a unique place near the expected offset
        # where the code around it is the same in shape
        if f is None and ds and x % 2 == 0:
            insns = self._lin_insns(x, WINDOW, 32)
            if len(insns) >= 6:
                hits = [d for d in range(min(ds) - SEARCH & ~1, max(ds) + SEARCH + 2, 2)
                        if all(self._shape_equiv(a, a + d) for a in insns)]
                code_hit = hits[0] if len(hits) == 1 else None
            else:
                code_hit = None
        else:
            code_hit = None
        # data, or code that no function covers (shared tails)
        cands = set(ds) | {self.ev["flash"][k] - k for k in self.ev_keys["flash"] if abs(k - x) < 0x800}
        if x in self.ev["flash"]:
            cands.add(self.ev["flash"][x] - x)
        data = {d for d in cands if self._data_at(x, x + d)}
        code = {d for d in cands if self._code_at(x, x + d)}
        for found, what in ((data & code, "data/code"), (data, "data"), (code, "code no function covers")):
            if len(found) == 1:
                d = found.pop()
                src = "referenced by stock code" if self.ev["flash"].get(x) == x + d else "same content near the offset of its neighbours"
                return x + d, f"flash {what}: {src}"
        if code_hit is not None:
            return x + code_hit, f"flash code no function covers (CHANGED region; {WINDOW} bytes before and up to 32 after the same in shape)"
        # inside an object stock code references: the same bytes from its start up to here
        keys = self.ev_keys["flash"]
        i = bisect.bisect_right(keys, x) - 1
        if i >= 0 and x - keys[i] <= 0x4000:
            k = keys[i]
            d = self.ev["flash"][k] - k
            if self._rb(k, x - k + 16) == self._tb(k + d, x - k + 16):
                return x + d, f"flash data: {x - k:#x} into the object stock code references at {k:#x}, same bytes"
        raise Unresolved(f"flash {x:#x}: {len(data)} data / {len(code)} code matches at the nearby offsets "
                         f"{sorted(hex(d) for d in cands)}")

    def _pool_by_readers(self, x):
        """x is a literal word: find the reference lrw/jmpi/jsri instructions in the 1 KB before it that read it,
        translate each one (code rules), and take the word its target counterpart reads. All readers must agree."""
        found = set()
        readers = 0
        for a in range(max(APP, x - 0x400), x, 2):
            d = decode(self.ref.img, a - FLASH_BASE, a)
            if not d or d[0] != "pool" or d[1] != x:
                continue
            readers += 1
            try:
                b, _how = self._flash_tr(a)
            except (Unresolved, RecursionError):
                return None
            dt = decode(self.img, b - FLASH_BASE, b)
            if not dt or dt[0] != "pool":
                return None
            found.add(dt[1])
        if readers and len(found) == 1:
            y = found.pop()
            if self._words_equiv(x, y, 4):
                return y, f"flash literal word: where its {readers} reader(s) read in the target"
        return None

    def site_insns(self, x, n):
        """Instruction addresses covering reference bytes [x, x+n): the containing function's boundaries, or a linear
        decode from x (sites no function covers start on an instruction)."""
        out, a = [], x
        while a < x + n:
            k = insn_len(self.ref.img, self._flash("ref", a) - FLASH_BASE)
            out.append((a, k))
            a += k
        return out

    def site_equiv(self, x, y, n, tr):
        """The n reference bytes at x (what a splice overwrites) against the target bytes at y: every instruction the same,
        a PC-relative one reaching tr(its reference target) - tr is the translation the build uses. An n of 4 at an aligned
        address that is no instruction (a literal word) compares as a value. Returns None if equivalent, else why not."""
        ins = self.site_insns(x, n)          # an instruction that runs past x+n is compared whole (stricter)
        for a, k in ins:
            b = y + (a - x)
            rb, tb = self._rb(a, k), self._tb(b, k)
            dr = decode(self.ref.img, self._flash("ref", a) - FLASH_BASE, a)
            dt = decode(self.img, self._flash("tgt", b) - FLASH_BASE, b)
            if dr is None or not (FLASH_BASE <= dr[1] < FLASH_END or IRAM_LO <= dr[1] < IRAM_HI):
                if rb != tb:
                    return f"{a:#x}: {rb.hex()} vs {tb.hex()} at {b:#x}"
                continue
            if dt is None or dt[0] != dr[0]:
                return f"{a:#x}: a {dr[0]} instruction, but {tb.hex()} at {b:#x} is not one"
            want = dr[1] if IRAM_LO <= dr[1] < IRAM_HI else tr(dr[1])
            if dr[0] == "pool":
                v = struct.unpack("<I", self._rb(dr[1], 4))[0]
                w = struct.unpack("<I", self._tb(dt[1], 4))[0]
                if (tr(v) if region(v) else v) != w:
                    return f"{a:#x}: literal {v:#x} -> {w:#x} at {b:#x}, the map says {tr(v) if region(v) else v:#x}"
                continue
            if dt[1] != want:
                return f"{a:#x}: {dr[0]} to {dr[1]:#x}; at {b:#x} it reaches {dt[1]:#x}, the map says {want:#x}"
        return None

    def summary(self):
        c = collections.Counter(self.status.values())
        return dict(functions=len(self.ref.funcs), **c,
                    evidence={k: len(v) for k, v in self.ev.items()},
                    iram_offset=self.d_iram, sram_offset=self.d_sram)


def main(argv):
    if not argv:
        sys.exit("usage: fwmap.py FIRMWARE_ID [ADDR ...]")
    m = FwMap(argv[0])
    print(json.dumps(m.summary(), indent=1))
    for s in argv[1:]:
        x = int(s, 16)
        try:
            y, how = m.translate(x)
            print(f"{x:#010x} -> {y:#010x}  {how}")
        except Unresolved as e:
            print(f"{x:#010x} UNRESOLVED  {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
