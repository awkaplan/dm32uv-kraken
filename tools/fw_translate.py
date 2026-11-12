#!/usr/bin/env python3
"""fw_translate.py - Kraken's sources, manifests and tests for another firmware build, under out/fw/<id>/.

The reference build's tree is the only one anyone edits. For a build in firmware/firmwares.json other than the reference,
this writes out/fw/<id>/ laid out like the repo, with every stock address replaced through firmware/<id>/addrmap.json
(tools/gen_fw_maps.py; a value missing from it is an error, never left as it was):

  patches/<p>/            the patch folders of image.json. Code (.c .h .S .ld) translated; the manifests translated
                          splice by splice: address, the expected stock bytes (taken from the target only after
                          tools/fwmap.py site_equiv shows they are the same instructions, PC-relative ones reaching the
                          translated targets), replacement bytes re-encoded where they are PC-relative, asserts,
                          copied stock tables and their expected words, allow_occupied ranges (same length)
  tests/                  the QEMU tests (tests/*.py but the host tests) translated; tests/data copied
  image.json, allocations.json
                          image.json pointing at the above and at the target's base image; Kraken's own claims unchanged
                          (its windows are erased in every supported build, which the builder checks)
  re/link/                dm32uv-re's link directory, dm32_stock_symbols.ld holding only the symbols that translate
  re/include/             dm32uv-re's patch headers, dm32_patch_api.h with each address translated, or replaced by an
                          undefined name (a compile error if used) when it does not translate
  re/allocations.json     dm32uv-re's claim registry with its forbidden ranges moved to the target's copies
  re/occupied_regions.json  the target's IRAM and SRAM load images and the per-unit sector
  re/globals.json         module globals tools/re_tool.py sets in dm32uv-re's tools for this build (stock symbols,
                          link and include folders, the three QEMU-only patches, the IRAM load range)
  re/sector_alpubypass.bin  the QEMU-only ALPU bypass, applied to the target's own sector (emulator only)

  python3 tools/fw_translate.py l01 [049 ...]       (tools/build_image.py runs it for each non-reference build)
"""
import argparse
import copy
import json
import pathlib
import re
import shutil
import struct
import sys

import fw_text
import fwmap
import fwreg
import gen_fw_maps

REPO = fwreg.REPO
W_PREFIX = "dm32uv-unleashed"  # image.json paths start with the repo's folder in the build container (/w/dm32uv-unleashed)
POISON = "__KRAKEN_NOT_IN_{fw}_{name}"


class TranslateError(Exception):
    pass


class Translator:
    def __init__(self, fw):
        self.fw = fwreg.get(fw)
        p = REPO / "firmware" / self.fw["id"] / "addrmap.json"
        if not p.exists():
            raise TranslateError(f"{p.relative_to(REPO)} missing: run python3 tools/gen_fw_maps.py")
        doc = json.loads(p.read_text())
        self.amap = {int(k, 16): int(v["to"], 16) for k, v in doc["map"].items()}
        self.nums = gen_fw_maps.not_addresses()
        self.m = fwmap.FwMap(self.fw)
        if doc["target_sha256"] != fwreg.sha256(self.m.img) or doc["reference_sha256"] != fwreg.sha256(self.m.ref.img):
            raise TranslateError(f"{p.relative_to(REPO)} was made from other images: run python3 tools/gen_fw_maps.py")
        self.out = REPO / "out" / "fw" / self.fw["id"]

    # the map the build uses: the checked table only
    def T(self, v, where=""):
        if v in self.nums:
            return v
        if v not in self.amap:
            raise TranslateError(f"{where}: {v:#x} is not in firmware/{self.fw['id']}/addrmap.json "
                                 f"(run python3 tools/gen_fw_maps.py)")
        return self.amap[v]

    # a wider one for checks of instructions next to a site (branch targets nobody wrote down): the map, else fwmap
    def T_check(self, v):
        if v in self.amap:
            return self.amap[v]
        try:
            return self.m.translate(v)[0]
        except fwmap.Unresolved:
            return None

    # ---- sources ----
    def code(self, src, dst):
        text = src.read_text()
        sp = fw_text.spans(src, text)
        rel = src.relative_to(REPO)
        dst.write_text(fw_text.rewrite(text, sp, lambda v: self.T(v, str(rel))))

    # ---- manifests ----
    def splice_bytes(self, s, x, y, n, where):
        """Target bytes for the n reference bytes at x, after showing they are the same code."""
        why = self.m.site_equiv(x, y, n, lambda v: self.T_check(v) if self.T_check(v) is not None else -1)
        if why:
            raise TranslateError(f"{where}: the stock bytes it overwrites differ in {self.fw['version']}: {why}")
        return self.m.img[y - fwmap.FLASH_BASE:y - fwmap.FLASH_BASE + n]

    def reencode(self, rep, x, y, where):
        """Replacement bytes written at reference x, for target y: PC-relative instructions re-aimed at the translated
        target; anything else unchanged."""
        from csky_pcrel import decode, encode_like, insn_len
        out, off = bytearray(), 0
        while off < len(rep):
            k = insn_len(rep, off)
            ins = rep[off:off + k]
            d = decode(rep, off, x + off)
            if d and (fwmap.FLASH_BASE <= d[1] < fwmap.FLASH_END):
                t = self.T_check(d[1])
                e = encode_like(rep, off, y + off, t) if t is not None else None
                if e is None:
                    raise TranslateError(f"{where}: replacement {ins.hex()} at {x + off:#x} reaches {d[1]:#x}, which "
                                         f"cannot be reached the same way in {self.fw['version']}")
                ins = e
            out += ins
            off += k
        return bytes(out)

    def manifest(self, man, where):
        T = lambda v: self.T(v, where)
        m = fw_text.json_map(man, T)          # every address-valued number (addr, u32, expect_u32, branch targets ...)
        ref = self.m.ref.img
        for s0, s in zip(man.get("splices", []), m.get("splices", [])):
            x, y = int(s0["addr"], 16), int(s["addr"], 16)
            w = f"{where} splice {s0['name']}"
            if "expect" in s0:
                e = bytes.fromhex(s0["expect"].replace(" ", ""))
                if ref[x - fwmap.FLASH_BASE:x - fwmap.FLASH_BASE + len(e)] != e:
                    raise TranslateError(f"{w}: expect is not the reference stock bytes")
                s["expect"] = self.splice_bytes(s0, x, y, len(e), w).hex(" ")
            if "bytes" in s0:
                s["bytes"] = self.reencode(bytes.fromhex(s0["bytes"].replace(" ", "")), x, y, w).hex(" ")
        for d0, d in zip(man.get("data", []), m.get("data", [])):
            for p0, p in zip(d0["parts"], d["parts"]):
                if "stock_u32" in p0 and "expect" in p0:
                    p["expect"] = [f"{T(int(v, 16)):#010x}" if fw_text.is_stock(int(v, 16)) else v for v in p0["expect"]]
        return m

    # ---- dm32uv-re's link and include folders ----
    def stock_symbols(self, src, dst):
        lines, kept, dropped = [], 0, 0
        for line in src.read_text().splitlines():
            mm = re.search(r"PROVIDE\((\w+)\s*=\s*(0x[0-9a-fA-F]+)\)", line)
            if mm:
                v = int(mm.group(2), 16)
                t = self.amap.get(v)
                if t is None and fw_text.is_stock(v):
                    try:
                        t = self.m.translate(v)[0]
                    except fwmap.Unresolved:
                        t = None
                elif t is None:
                    t = v
                if t is None:
                    lines.append(f"/* {mm.group(1)}: not found in {self.fw['version']} */")
                    dropped += 1
                    continue
                line = line.replace(mm.group(2), f"{t:#x}")
                kept += 1
            lines.append(line)
        dst.write_text(f"/* dm32_stock_symbols.ld for {self.fw['version']}, translated by tools/fw_translate.py from the "
                       f"{fwreg.reference()['version']} file: {kept} symbols, {dropped} left out */\n" + "\n".join(lines) + "\n")
        return kept, dropped

    def api_header(self, src, dst):
        text = src.read_text()
        sp = fw_text.spans(src, text)

        def tr(v):
            t = self.amap.get(v)
            if t is None:
                try:
                    t = self.m.translate(v)[0]
                except fwmap.Unresolved:
                    return POISON.format(fw=self.fw["id"].upper(), name=f"{v:08x}")
            return t
        dst.write_text(fw_text.rewrite(text, sp, tr))

    # ---- the whole tree ----
    def run(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        re_root = fwreg.re_root()
        cfg = json.loads((REPO / "image.json").read_text())
        # 1. patch folders
        code_files = {p.resolve() for p in gen_fw_maps.source_files()}
        for path, man in gen_fw_maps.manifests():
            d = path.parent
            od = self.out / d.relative_to(REPO)
            od.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                if f.is_dir() and f.name == "features":     # the per-feature specs (read by the tests; the generators do not run here)
                    shutil.copytree(f, od / f.name)
                    continue
                if f.is_dir() or f.name.startswith("."):
                    continue
                if f.resolve() in code_files:
                    self.code(f, od / f.name)
                elif f == path:
                    (od / f.name).write_text(json.dumps(self.manifest(man, str(path.relative_to(REPO))), indent=1) + "\n")
                else:
                    shutil.copy2(f, od / f.name)
        # 2. tests
        ot = self.out / "tests"
        ot.mkdir()
        tests = set(gen_fw_maps.test_files())
        for f in (REPO / "tests").iterdir():
            if f.is_dir():
                if f.name in ("data",):
                    shutil.copytree(f, ot / f.name)
                continue
            if f in tests:
                self.code(f, ot / f.name)
            elif f.suffix != ".pyc":
                shutil.copy2(f, ot / f.name)
        # 3. image.json and Kraken's claims (unchanged: Kraken's windows are the same in every supported build)
        img_cfg = copy.deepcopy(cfg)
        img_cfg["_comment"] = [f"GENERATED by tools/fw_translate.py: image.json for {self.fw['version']}."] + cfg["_comment"]
        img_cfg["base_image"] = str(fwreg.base_path(self.fw).resolve())
        rel_out = f"{W_PREFIX}/{self.out.relative_to(REPO).as_posix()}"
        for p in img_cfg["patches"]:
            p["manifest"] = p["manifest"].replace(f"{W_PREFIX}/", f"{rel_out}/", 1)
            if p.get("test"):
                p["test"]["script"] = p["test"]["script"].replace(f"{W_PREFIX}/tests/", f"{rel_out}/tests/", 1)
        (self.out / "image.json").write_text(json.dumps(img_cfg, indent=1) + "\n")
        shutil.copy2(REPO / "allocations.json", self.out / "allocations.json")
        # 4. dm32uv-re link and include folders, its policy files, the QEMU-only patches
        rd = self.out / "re"
        (rd / "link").mkdir(parents=True)
        for f in (re_root / "tools/link").iterdir():
            if f.is_file() and f.name != "dm32_stock_symbols.ld":
                shutil.copy2(f, rd / "link" / f.name)
        kept, dropped = self.stock_symbols(re_root / "tools/link/dm32_stock_symbols.ld", rd / "link/dm32_stock_symbols.ld")
        shutil.copytree(re_root / "tools/include", rd / "include")
        self.api_header(re_root / "tools/include/dm32_patch_api.h", rd / "include/dm32_patch_api.h")
        self.policy(re_root, rd)
        print(f"{self.fw['version']}: {self.out.relative_to(REPO)} written ({len(self.amap)} addresses; stock symbols: "
              f"{kept} kept, {dropped} not in this build)")

    def policy(self, re_root, rd):
        R, L = self.m.ref.lay, self.m.lay
        B = fwmap.FLASH_BASE
        # the builder's claim registry: same windows and claims, forbidden ranges moved
        reg = json.loads((re_root / "data/allocations.json").read_text())
        for f in reg.get("forbidden", []):
            lo, hi = int(f["start"], 16), int(f["end_exclusive"], 16)
            if fwreg.APP + B <= lo:
                y = self.T_check(lo + 0x866 if f["name"] == "alpu_bypass_sector" else lo)
                if y is None:
                    raise TranslateError(f"forbidden range {f['name']} does not translate")
                if f["name"] == "alpu_bypass_sector":
                    y -= 0x866
                s0, s1 = y & ~0xFFF, (y + (hi - lo) + 0xFFF) & ~0xFFF
                f["start"], f["end_exclusive"] = f"{s0:#010x}", f"{s1:#010x}"
                f["what"] = f"[{self.fw['version']}] " + f["what"]
        for w in reg["windows"]:
            if w["name"] == "flash":
                lo, hi = int(w["start"], 16), int(w["end_exclusive"], 16)
                if any(b != 0xFF for b in self.m.img[lo - B:hi - B]):
                    raise TranslateError(f"the flash window {lo:#x}-{hi:#x} is not erased in {self.fw['version']}")
        (rd / "allocations.json").write_text(json.dumps(reg, indent=1) + "\n")
        # occupied regions outside the update file: the per-unit sector and the two load images
        occ = {"_comment": f"GENERATED by tools/fw_translate.py for {self.fw['version']}: the per-unit sector and the boot "
                           f"stub's IRAM and SRAM load images (offsets from 0x03000000).",
               "occupied": [
                   {"start": "0x0000b000", "end": "0x0000c000", "what": "PER-UNIT parameter sector (never written)"},
                   {"start": f"{L['iram_src'] - B:#010x}", "end": f"{L['iram_src'] + L['iram_len'] - B:#010x}",
                    "what": "IRAM load image (copied to 0x00010000 at boot)"},
                   {"start": f"{L['iram_src'] + L['iram_len'] - B:#010x}", "end": f"{L['app_end'] - B:#010x}",
                    "what": "vendor data after the IRAM load image, to the end of the update file"},
                   {"start": f"{L['sram_src'] - B:#010x}", "end": f"{L['sram_src'] + L['sram_len'] - B:#010x}",
                    "what": "SRAM initialised-data image (copied to 0x18000800 at boot)"}]}
        (rd / "occupied_regions.json").write_text(json.dumps(occ, indent=1) + "\n")
        # the three QEMU-only patches dm32uv-re's compose_qemu_image applies, at the target's sites
        ref_alpu = (re_root / "data/firmware/patching/sector_030c6000_alpubypass.bin").read_bytes()
        ref_stock = self.m.ref.img[0xC6000:0xC7000]
        diff = [i for i in range(0x1000) if ref_alpu[i] != ref_stock[i]]
        site = 0x030C6000 + diff[0]
        y = self.T_check(site)
        if y is None:
            raise TranslateError(f"the ALPU bypass site {site:#x} does not translate")
        n = diff[-1] - diff[0] + 1
        why = self.m.site_equiv(site, y, n, lambda v: self.T_check(v) if self.T_check(v) is not None else -1)
        if why:
            raise TranslateError(f"ALPU bypass site: {why}")
        sec = y & ~0xFFF
        alpu = bytearray(self.m.img[sec - B:sec - B + 0x1000])
        alpu[y - sec:y - sec + n] = ref_alpu[diff[0]:diff[0] + n]
        (rd / "sector_alpubypass.bin").write_bytes(bytes(alpu))
        apo = self.T_check(0x0301BDE0)
        if apo is None or self.m.site_equiv(0x0301BDE0, apo, 4, lambda v: self.T_check(v) or -1):
            raise TranslateError("the auto-power-off QEMU patch site does not translate")
        glob = {
            "build_manifest_patch": {
                "STOCK_SYMBOLS_LD": str(rd / "link/dm32_stock_symbols.ld"), "LINK_DIR": str(rd / "link"),
                "API_INCLUDE_DIR": str(rd / "include"), "OCCUPIED_JSON": str(rd / "occupied_regions.json"),
                "ALLOCATIONS_JSON": str(rd / "allocations.json"), "DEFAULT_BASE": str(fwreg.base_path(self.fw).resolve())},
            "compose_qemu_image": {
                "DEFAULT_BASE": str(fwreg.base_path(self.fw).resolve()), "ALPU_BYPASS_SECTOR": str(rd / "sector_alpubypass.bin"),
                "ALPU_SECTOR_OFFSET": sec - B, "AUTOPOWEROFF_OFFSET": apo - B,
                "REPO_ROOT": "/"},   # its --help prints DEFAULT_BASE relative to REPO_ROOT; this base is outside dm32uv-re
            "derive_iram": {
                "DEFAULT_FLASH": str(fwreg.base_path(self.fw).resolve()), "LOAD_SRC": L["iram_src"],
                "STUB_POOL": {str(0x0300C0D0): 0x10000, str(0x0300C0D4): 0x2F098, str(0x0300C0D8): L["iram_src"],
                              str(0x0300C0DC): 0x2F098, str(0x0300C0E0): L["bss_end"]}},
            "stage_sectors": {"QEMU_ONLY_SECTOR_OFFSETS": sorted({sec - B, (apo - B) & ~0xFFF, 0xB000})},
        }
        (rd / "globals.json").write_text(json.dumps(glob, indent=1) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="+")
    a = ap.parse_args()
    try:
        for i in a.ids:
            Translator(i).run()
    except TranslateError as e:
        sys.exit(f"fw_translate: {e}")


if __name__ == "__main__":
    main()
