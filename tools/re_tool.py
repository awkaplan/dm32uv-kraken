#!/usr/bin/env python3
"""re_tool.py - run one of dm32uv-re's patch tools for a given stock firmware build.

dm32uv-re's tools (tools/src/dm32_patch_tools) are written for DM32.00.01.048: the stock symbol file, the patch headers,
the occupied regions, the QEMU-only patch sites and the IRAM load range are module globals. This loads the tool, sets
those globals to the values tools/fw_translate.py wrote for the build (out/fw/<id>/re/globals.json), applies any --set,
and runs the tool's main() with the remaining arguments. For the reference build it changes nothing but the --set values.

  re_tool.py [--fw ID] [--set MODULE.NAME=VALUE ...] TOOL [TOOL ARGS...]

  re_tool.py --fw l01 compose_qemu_image --patched img.bin --out boot.bin --iram-out
  re_tool.py --set build_manifest_patch.ALLOCATIONS_JSON=out/x.json build_manifest_patch m.json --base b.bin ...

Replaces tools/bmp_filtered.py (the --set form does what it did). Runs inside the Docker image.
"""
import importlib
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
RE_TOOLS = pathlib.Path(os.environ.get("DM32UV_RE", "/w/dm32/dm32uv-re")) / "tools/src/dm32_patch_tools"


def coerce(old, new):
    """new (from JSON) in the type of the global it replaces."""
    if isinstance(old, pathlib.Path):
        return pathlib.Path(new)
    if isinstance(old, set):
        return set(new)
    if isinstance(old, tuple):
        return tuple(new)
    if isinstance(old, dict) and old and all(isinstance(k, int) for k in old):
        return {int(k): v for k, v in new.items()}
    if isinstance(old, int) and not isinstance(old, bool) and isinstance(new, str):
        return int(new, 0)
    return new


def apply(settings):
    for mod_name, values in settings.items():
        mod = importlib.import_module(mod_name)
        for name, v in values.items():
            if not hasattr(mod, name):
                sys.exit(f"re_tool: {mod_name} has no global {name} (dm32uv-re changed?)")
            setattr(mod, name, coerce(getattr(mod, name), v))


def main():
    argv = sys.argv[1:]
    fw, sets = None, {}
    while argv and argv[0] in ("--fw", "--set"):
        opt, val = argv[0], argv[1]
        argv = argv[2:]
        if opt == "--fw":
            fw = val
        else:
            key, _, v = val.partition("=")
            mod, _, name = key.rpartition(".")
            sets.setdefault(mod, {})[name] = v
    if not argv:
        sys.exit(__doc__)
    tool, args = argv[0], argv[1:]
    sys.path.insert(0, str(RE_TOOLS))
    sys.path.insert(0, str(HERE))
    if fw:
        import fwreg
        if not fwreg.is_reference(fwreg.get(fw)):
            g = REPO / "out" / "fw" / fwreg.get(fw)["id"] / "re" / "globals.json"
            if not g.exists():
                sys.exit(f"re_tool: {g} missing: run python3 tools/fw_translate.py {fw}")
            apply(json.loads(g.read_text()))
            # dm32uv-re's corpus (function names, patch-site catalogue) describes the reference build only: its
            # informational cross-check would name the wrong functions here
            bmp = importlib.import_module("build_manifest_patch")
            if hasattr(bmp, "corpus_crosscheck"):
                bmp.corpus_crosscheck = lambda addr: []
    apply(sets)
    mod = importlib.import_module(tool)
    sys.argv = [str(RE_TOOLS / f"{tool}.py"), *args]
    rc = mod.main()
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
