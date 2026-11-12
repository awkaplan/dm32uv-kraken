#!/usr/bin/env python3
"""tools/hook_probe.py on two hooks already in patches/kraken_ui/hooks.json: the bytes it reports at each site are the
hook's "expect", and its L01 and 049 translations are the ones firmware/<id>/addrmap.json records.

Needs the 048, L01 and 049 stock images (out/stock/ and dm32uv-re's base image) and dm32uv-re's disassembler (capstone-csky,
or objdump). Without them it prints a SKIP line and exits 0, so the host run skips and the build container runs it:

  DM32UV_RE=... tools/build.sh --run 'python3 tests/test_hook_probe.py'
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def skip(why):
    print(f"SKIP test_hook_probe: {why}")
    sys.exit(0)


import fwreg  # noqa: E402

try:
    fwreg.re_root()
except SystemExit as e:
    skip(str(e))
missing = [str(fwreg.base_path(f)) for f in fwreg.firmwares() if not fwreg.base_path(f).exists()]
if missing:
    skip("stock images missing (python3 tools/fetch_stock.py): " + ", ".join(missing))

import hook_probe  # noqa: E402

if not hook_probe.disassembler_available():
    skip("no capstone-csky and no csky-abiv2-elf-objdump here (run inside the build container)")

HOOKS = ["cs_l5_title_hook", "menu_append_hook"]          # hooks.json names: a 4-byte hook each, in two menus


def hook_by_name(name):
    doc = json.loads((REPO / "patches" / "kraken_ui" / "hooks.json").read_text())
    for h in doc["hooks"]:
        if h.get("name") == name:
            return h
    raise SystemExit(f"hooks.json has no hook named {name}")


def check_hook(h):
    addr = int(h["addr"], 16)
    expect = h["expect"].split()
    rep = hook_probe.analyse(addr, len(expect))
    assert rep["expect"] == " ".join(expect), f"{h['name']}: probe bytes {rep['expect']} != hooks.json {h['expect']}"
    assert rep["boundary"] in (True, None), f"{h['name']}: {addr:#x} is not an instruction boundary"
    assert "=>" in hook_probe.render(rep), "the disassembly does not mark ADDR"
    for t in rep["translations"]:
        assert "error" not in t, f"{h['name']} {t['fw']}: fwmap cannot translate: {t['error']}"
        entry = json.loads((REPO / "firmware" / t["fw"] / "addrmap.json").read_text())["map"].get(f"0x{addr:08x}")
        assert entry is not None, f"{h['name']}: firmware/{t['fw']}/addrmap.json has no entry for {addr:#x}"
        assert int(entry["to"], 16) == t["to"], (
            f"{h['name']} {t['fw']}: probe says {t['to']:#x}, addrmap.json says {entry['to']}")
        assert t["addrmap"].startswith("agrees"), f"{h['name']} {t['fw']}: {t['addrmap']}"
    return rep


def main():
    # A non-flash address is refused before any image is read.
    try:
        hook_probe.analyse(0x00010000, 4)
    except ValueError:
        pass
    else:
        raise AssertionError("an address below program flash was accepted")
    for name in HOOKS:
        rep = check_hook(hook_by_name(name))
        print(f"ok {name} {rep['addr']:#010x} expect \"{rep['expect']}\" "
              + " ".join(f"{t['fw']}={t['to']:#010x}" for t in rep["translations"]))
    print("PASS test_hook_probe")


if __name__ == "__main__":
    main()
