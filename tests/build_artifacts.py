"""Where the tests find the build's products and the source's numbers (shared by every QEMU test file).

Importing this module also puts the project's tool directories on sys.path (the tests need tests/mi_rng_test.py, and
dm32uv-re's gdb_rsp, build_patch and QEMU drivers), so a test file needs no sys.path lines of its own.

  elf(n=1)                Kraken's ELF: the master link tools/pack_kraken.py made, copied to this build's
                          out/kraken/steps/*_kraken_ui/build/kraken_ui.elf. Kraken is one program, so every symbol is in it. n
                          still matters for statics that several files define (S, draw, ...): syms() of elf(n) prefers the one
                          defined in a file of the former blob n (LEGACY_BLOBS), as the five per-blob ELFs did. ($DM32_KRAKEN_OUT instead of out/kraken when set: build_image.py
                          sets it to its --out, so a variant image's tests read that build's ELFs). Looked up at call time, so nothing is cached across
                          builds (a stale steps dir feeds stale symbols: delete out/kraken/steps/* if values look a few
                          bytes off).
  syms(n_or_path, sizes=False)
                          csky-abiv2-elf-nm of an ELF, cached per file. Flat {name: addr} by default (the last duplicate wins);
                          sizes=True gives {name: [(addr, size), ...]} for tests that tell duplicated statics apart. Takes a
                          path too: tools/build_image.py repoints a test module's ELF global at the combined build's copy, and
                          a test that passes that global here keeps honouring it.
  krk_define(name)        a decimal #define from krk.h / krk_menu_rows.h (the per-host counts and menu item numbers are generated).
  krk_ext_ranges()        [(lo, hi)]: the program-flash range (krk.h KRK_PFLASH_LO/HI) krk_core.c's ext_ok() checks table pointers against.
  claims()                {name: (lo, hi)} of the claims tools/pack_kraken.py generated for this build (out/.../pack/kraken_ui/claims.json).
  in_claims(addr, space)  is addr inside one of those generated claims (or a pinned one of allocations.json)?
  piece(stem, kind)       (lo, hi) of one source file's code ("k") or state ("b") as the pack placed it (tells duplicated statics apart).
  EXT_MAX, TBL_SLOTS      the KRAKEN slot count of the screen table and the number of slots in all (APRS, Radio Settings, Channel Set).
  EXT_TABLE, HOOK_TBL     the screen table (krk_ext_screens) and the hook table (krk_ext_hooks): read from the ELF on first use.
  SCRATCH_SRAM            SRAM no Kraken build uses (allocations.json keep_free): scratch for the halted-machine checks.
  STOCK_IMG, DATACHIP     the stock image (image.json's base_image) and dm32uv-re's factory-defaults datachip.
  COMPOSE, FW             the command that composes a QEMU image for this build, and the build's id (None: the reference).
"""
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# For another firmware build the tests run from tools/fw_translate.py's copy, out/fw/<id>/tests/, whose parent mirrors
# the repo (patches/, image.json, allocations.json); build_image.py then sets DM32_FW to the build and DM32_WS to the
# folder above the repo (docs/firmware-builds.md).
WS = pathlib.Path(os.environ.get("DM32_WS") or REPO.parent)
TESTS = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(os.environ.get("DM32_KRAKEN_OUT") or REPO / "out/kraken")     # build_image.py sets it to its --out
PATCHED = OUT / "progmem_KRAKEN_patched.bin"
RE_ROOT = pathlib.Path(os.environ.get("DM32UV_RE") or WS / "dm32/dm32uv-re")
RE_TOOLS = RE_ROOT / "tools/src/dm32_patch_tools"
for _p in (RE_TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
FW = os.environ.get("DM32_FW") or None
KRAKEN_TOOLS = next(p / "tools" for p in pathlib.Path(__file__).resolve().parents if (p / "tools/re_tool.py").exists())
# compose_qemu_image for this build: dm32uv-re's script directly, or through tools/re_tool.py (the build's QEMU-only
# patch sites and IRAM load range)
COMPOSE = [sys.executable, str(KRAKEN_TOOLS / "re_tool.py"), "--fw", FW, "compose_qemu_image"] if FW else \
    [sys.executable, str(RE_TOOLS / "compose_qemu_image.py")]

STOCK_IMG = WS / json.loads((REPO / "image.json").read_text())["base_image"]
DATACHIP = RE_ROOT / "data/firmware/patching/datachip_generic_factory_defaults.bin.xz"
SRC = REPO / "patches/kraken_ui"


_fwmap = {}


def stock_is(addr, hexbytes):
    """Are the stock bytes at ADDR the instructions HEXBYTES (written for DM32.00.01.048)? On the reference build: the
    same bytes. On another build (FW): ADDR is already translated; the 048 address is found through
    firmware/<FW>/addrmap.json and tools/fwmap.py site_equiv checks the bytes are the same instructions there, each
    PC-relative one reaching the translated target - the check the build applies to every splice."""
    want = bytes.fromhex(hexbytes.replace(" ", ""))
    img = STOCK_IMG.read_bytes()
    got = img[addr - 0x03000000:addr - 0x03000000 + len(want)]
    if not FW:
        return got == want
    if FW not in _fwmap:
        sys.path.insert(0, str(KRAKEN_TOOLS))
        import fwmap
        amap = json.loads((KRAKEN_TOOLS.parent / "firmware" / FW / "addrmap.json").read_text())["map"]
        fwd = {int(k, 16): int(v["to"], 16) for k, v in amap.items()}
        inv = {t: f for f, t in fwd.items()}
        fm = fwmap.FwMap(FW)

        def tr(v):
            t = fwd.get(v)
            if t is None:
                try:
                    t = fm.translate(v)[0]
                except fwmap.Unresolved:
                    t = -1
            return t
        _fwmap[FW] = (fm, inv, tr)
    fm, inv, tr = _fwmap[FW]
    ref = inv.get(addr)
    if ref is None or fm.ref.img[ref - 0x03000000:ref - 0x03000000 + len(want)] != want:
        return False
    return fm.site_equiv(ref, addr, len(want), tr) is None


# The files of the five blobs Kraken was until 2026-11-11 (and the bench build's sixth). Tests still name their symbol
# tables by blob number (s2 = syms(2), s4 = syms(4)); a static defined in several files is resolved to the one in that
# blob's files. A source added later belongs to no blob: its statics resolve only when the name is unique.
LEGACY_BLOBS = {
    1: ("krk_core", "krk_about", "krk_rssi", "krk_grid", "krk_lastheard", "krk_rf", "krk_scope", "krk_optrow", "krk_stubs"),
    2: ("krk_ext", "krk_aprs_tx", "krk_aprs_text", "krk_aprs_menu", "krk_aprsrx", "krk_aprs_alert", "krk_afsk", "krk_ax25",
        "krk_periodic", "krk_scramble", "krk_squelch", "krk_vox", "krk_ofstubs", "krk_txtrim", "krk_vframe", "krk_parrot_sm",
        "krk_parrot", "krk_cwid", "krk_band", "krk_bvcfg", "krk_tags", "krk_autopwr", "krk_apc_stubs", "krk_aprs_chan"),
    3: ("krk_sat", "krk_sat_tles", "krk_satrf", "krk_satscr", "krk_callsign", "krk_tapedit", "krk_aprs_trec"),
    4: ("krk_bvscr", "krk_bvmsg", "krk_bv_modem", "krk_bv_frame", "krk_bv_sound", "krk_ambe", "krk_aprs_msg",
        "krk_bvbench_io", "krk_bvbench_pure", "krk_bvbench_plan"),
    5: ("krk_dmrlink", "krk_bvbench_rxlib"),
    6: ("krk_bvbench", "krk_bvbench_rx"),
}


class _BlobElf(type(pathlib.Path())):
    """The ELF path, remembering which former blob's statics syms() should prefer."""
    blob = None


def elf(n=1):
    found = next((OUT / "steps").glob("*_kraken_ui/build/kraken_ui.elf"), None)
    if found is None:
        sys.exit(f"build_artifacts.elf(): no kraken_ui.elf under {OUT / 'steps'} (run tools/build.sh first)")
    p = _BlobElf(found)
    p.blob = n
    return p


def _plan_pieces():
    f = OUT / "pack" / "kraken_ui" / "plan.json"
    return json.loads(f.read_text())["pieces"] if f.exists() else []


_syms = {}


def syms(elf_=1, sizes=False):
    path = elf(elf_) if isinstance(elf_, int) else elf_ if isinstance(elf_, _BlobElf) else pathlib.Path(elf_)
    blob = getattr(path, "blob", None)
    key = (str(path), path.stat().st_mtime_ns, sizes, blob)
    if key not in _syms:
        out = subprocess.run(["csky-abiv2-elf-nm", "-S", str(path)], capture_output=True, text=True, check=True).stdout
        allsym = {}
        for line in out.splitlines():
            p = line.split()
            if len(p) == 4:
                allsym.setdefault(p[3], []).append((int(p[0], 16), int(p[1], 16)))
            elif len(p) == 3:
                allsym.setdefault(p[2], []).append((int(p[0], 16), 0))
        files = LEGACY_BLOBS.get(blob, ())
        pieces = _plan_pieces() if files else []

        def rank(entry):
            """-1: not in the blob's files; else the file's place in the blob (the old link order: the last one won)."""
            stems = {p["stem"] for p in pieces if p["addr"] <= entry[0] < p["addr"] + p["size"]}
            return max((files.index(st) for st in stems if st in files), default=-1)
        res = {}
        for name, entries in allsym.items():
            if len(entries) > 1 and files:
                mine = sorted((e for e in entries if rank(e) >= 0), key=rank)
                entries = mine[::-1] + [e for e in entries if rank(e) < 0] if mine else entries
                res[name] = entries if sizes else entries[0][0]
            else:
                res[name] = entries if sizes else entries[-1][0]
        _syms[key] = res
    return dict(_syms[key]) if not sizes else {k: list(v) for k, v in _syms[key].items()}


def krk_header():
    return "".join((SRC / f).read_text() for f in ("krk.h", "krk_menu_rows.h"))


def krk_define(name):
    return int(re.search(rf"#define {name}\s+(\d+)", krk_header())[1])


def krk_ext_ranges():
    hdr = krk_header()
    return [(int(re.search(r"#define KRK_PFLASH_LO\s+(0x[0-9a-fA-F]+)u", hdr)[1], 16),
             int(re.search(r"#define KRK_PFLASH_HI\s+(0x[0-9a-fA-F]+)u", hdr)[1], 16))]


def claims():
    f = OUT / "pack" / "kraken_ui" / "claims.json"
    if not f.exists():
        sys.exit(f"build_artifacts.claims(): no {f} (run tools/build.sh first)")
    out = {c["name"]: (int(c["start"], 16), int(c["end_exclusive"], 16), c["space"]) for c in json.loads(f.read_text())["allocations"]}
    for c in json.loads((REPO / "allocations.json").read_text())["allocations"]:
        out.setdefault(c["name"], (int(c["start"], 16), int(c["end_exclusive"], 16), c["space"]))
    return out


def piece(stem, kind="b"):
    """(lo, hi) of a source file's piece as tools/pack_kraken.py placed it: kind "k" = its code and constants, "b" = its .bss
    (the part not taken by a link.json sram_group)."""
    pl = json.loads((OUT / "pack" / "kraken_ui" / "plan.json").read_text())
    p = next((p for p in pl["pieces"] if p["name"] == f".{kind}.{stem}"), None)
    if p is None:
        sys.exit(f"build_artifacts.piece({stem!r}, {kind!r}): no such piece in this build's plan")
    return p["addr"], p["addr"] + p["size"]


def in_claims(addr, space, size=1):
    return any(sp == space and lo <= addr and addr + size <= hi for lo, hi, sp in claims().values())


EXT_MAX = krk_define("KRK_EXT_MAX")      # screen-table size; the hook table follows it, the APRS, Radio Settings and Channel Set slots
TBL_SLOTS = EXT_MAX + krk_define("KRK_APRS_MAX") + krk_define("KRK_RS_MAX") + krk_define("KRK_CS_MAX")
# SRAM no Kraken build uses (the last "keep_free" SRAM range of allocations.json): scratch for halted-machine checks
SCRATCH_SRAM = [int(k["start"], 16) for k in json.loads((REPO / "allocations.json").read_text()).get("keep_free", [])
                if k["space"] == "sram"][-1]
_LAZY = {"EXT_TABLE": "krk_ext_screens", "HOOK_TBL": "krk_ext_hooks"}


def __getattr__(name):
    """EXT_TABLE / HOOK_TBL: the tables' addresses in this build (placed by tools/pack_kraken.py), read on first use."""
    if name in _LAZY:
        return syms()[_LAZY[name]]
    raise AttributeError(name)
