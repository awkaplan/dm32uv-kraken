"""Where the tests find the build's products and the source's numbers (shared by every QEMU test file).

Importing this module also puts the project's tool directories on sys.path (the tests need tests/mi_rng_test.py, and
dm32uv-re's gdb_rsp, build_patch and QEMU drivers), so a test file needs no sys.path lines of its own.

  elf(n)                  the n-th kraken_ui blob's ELF (1 = kraken_ui.elf, 2 = kraken_ui2.elf, ...) in this build's
                          out/kraken/steps/*_kraken_ui/build/ ($DM32_KRAKEN_OUT instead of out/kraken when set: build_image.py
                          sets it to its --out, so a variant image's tests read that build's ELFs). Looked up at call time, so nothing is cached across
                          builds (a stale steps dir feeds stale symbols: delete out/kraken/steps/* if values look a few
                          bytes off).
  syms(n_or_path, sizes=False)
                          csky-abiv2-elf-nm of an ELF, cached per file. Flat {name: addr} by default (the last duplicate wins);
                          sizes=True gives {name: [(addr, size), ...]} for tests that tell duplicated statics apart. Takes a
                          path too: tools/build_image.py repoints a test module's ELF global at the combined build's copy, and
                          a test that passes that global here keeps honouring it.
  krk_define(name)        a decimal #define from krk.h / krk_menu_rows.h (the per-host counts and menu item numbers are generated).
  krk_ext_ranges()        [(lo, hi)] of the KRK_EXT*_LO/HI constants krk_core.c's ext_ok() checks extension screens against.
  EXT_MAX, TBL_SLOTS, HOOK_TBL
                          the extension screen table (0x030ee000) size, the number of slots that follow it (APRS, Radio
                          Settings, Channel Set) and the address of the hook table behind them.
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


def elf(n=1):
    name = "kraken_ui.elf" if n == 1 else f"kraken_ui{n}.elf"
    found = next((OUT / "steps").glob(f"*_kraken_ui/build/{name}"), None)
    if found is None:
        sys.exit(f"build_artifacts.elf({n}): no {name} under {OUT / 'steps'} (run tools/build.sh first)")
    return found


_syms = {}


def syms(elf_=1, sizes=False):
    path = elf(elf_) if isinstance(elf_, int) else pathlib.Path(elf_)
    key = (str(path), path.stat().st_mtime_ns, sizes)
    if key not in _syms:
        out = subprocess.run(["csky-abiv2-elf-nm"] + (["-S"] if sizes else []) + [str(path)],
                             capture_output=True, text=True, check=True).stdout
        res = {}
        for line in out.splitlines():
            p = line.split()
            if sizes:
                if len(p) == 4:
                    res.setdefault(p[3], []).append((int(p[0], 16), int(p[1], 16)))
                elif len(p) == 3:
                    res.setdefault(p[2], []).append((int(p[0], 16), 0))
            elif len(p) == 3:
                res[p[2]] = int(p[0], 16)
        _syms[key] = res
    return dict(_syms[key]) if not sizes else {k: list(v) for k, v in _syms[key].items()}


def krk_header():
    return "".join((SRC / f).read_text() for f in ("krk.h", "krk_menu_rows.h"))


def krk_define(name):
    return int(re.search(rf"#define {name}\s+(\d+)", krk_header())[1])


def krk_ext_ranges():
    hdr = krk_header()
    return [(int(lo, 16), int(re.search(rf"#define KRK_EXT{k}_HI\s+(0x[0-9a-fA-F]+)u", hdr)[1], 16))
            for k, lo in re.findall(r"#define KRK_EXT(\d?)_LO\s+(0x[0-9a-fA-F]+)u", hdr)]


EXT_MAX = krk_define("KRK_EXT_MAX")      # screen-table size; the hook table follows it, the APRS, Radio Settings and Channel Set slots
TBL_SLOTS = EXT_MAX + krk_define("KRK_APRS_MAX") + krk_define("KRK_RS_MAX") + krk_define("KRK_CS_MAX")
EXT_TABLE = 0x030EE000
HOOK_TBL = EXT_TABLE + 4 * TBL_SLOTS
