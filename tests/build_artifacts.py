"""Where the tests find the build's products and the source's numbers (shared by every QEMU test file).

Importing this module also puts the project's tool directories on sys.path (the tests need tests/mi_rng_test.py, and
dm32uv-re's gdb_rsp, build_patch and QEMU drivers), so a test file needs no sys.path lines of its own.

  elf(n)                  the n-th kraken_ui blob's ELF (1 = kraken_ui.elf, 2 = kraken_ui2.elf, ...) in this build's
                          out/kraken/steps/*_kraken_ui/build/. Looked up at call time, so nothing is cached across
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
  STOCK_IMG, DATACHIP     the stock image (image.json's base_image) and the factory-defaults datachip beside it.
"""
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
WS = REPO.parent
TESTS = REPO / "tests"
OUT = REPO / "out/kraken"
PATCHED = OUT / "progmem_KRAKEN_patched.bin"
RE_TOOLS = WS / "dm32/dm32uv-re/tools/src/dm32_patch_tools"
for _p in (RE_TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

STOCK_IMG = WS / json.loads((REPO / "image.json").read_text())["base_image"]
DATACHIP = STOCK_IMG.parent / "datachip_generic_factory_defaults.bin.xz"
SRC = REPO / "patches/kraken_ui"


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
