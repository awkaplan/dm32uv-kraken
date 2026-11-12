#!/usr/bin/env python3
"""hook_probe.py - read-only helper for choosing a hook site. Writes nothing.

  python3 tools/hook_probe.py ADDR [--len N] [--target SYMBOL]

ADDR is a DM32.00.01.048 (reference) program-flash address, hex. It prints:
  - the containing stock function, from dm32uv-re's symbol data (meta/functions.json), if there is one;
  - whether ADDR (and ADDR+N) is an instruction boundary there (meta/disasm.jsonl);
  - about 12 instructions around ADDR (dm32uv-re's cs_disasm.py: capstone-csky, or objdump as a fallback);
  - the N bytes at ADDR (default 4) in hooks.json's "expect" form;
  - for each other build (l01, 049): the translated address (tools/fwmap.py, the code the build uses), the bytes
    there, whether they equal 048's, fwmap.site_equiv's verdict, and what firmware/<id>/addrmap.json records;
  - a draft hooks.json entry with the fields still to fill in.

The 048 image is read as the build reads it (image.json base_image, checked by tools/fwreg.py). The other builds'
images are under out/stock/ (tools/fetch_stock.py). dm32uv-re is DM32UV_RE, ../dm32uv-re or ../dm32/dm32uv-re.
Run it in the build container (the stock images, capstone-csky and the Python dependencies are there):

  DM32UV_RE=/path/to/dm32uv-re tools/build.sh --run 'python3 tools/hook_probe.py 0x03038ab8'
"""
import argparse
import bisect
import json
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import fwmap  # noqa: E402
import fwreg  # noqa: E402

FLASH_BASE, FLASH_END = 0x03000000, 0x03100000
DEFAULT_LEN = 4
CONTEXT_BEFORE, CONTEXT_AFTER = 4, 8          # instructions shown around ADDR
OBJDUMP = "csky-abiv2-elf-objdump"


def other_builds():
    """The ids of the builds other than the reference, in firmwares.json order (l01, 049)."""
    return [f["id"] for f in fwreg.firmwares() if not fwreg.is_reference(f)]


def hexbytes(b):
    return " ".join(f"{x:02x}" for x in b)


def load_disassembler(bin_path):
    """dm32uv-re's disassembler over the 048 image (capstone-csky, else objdump). Exits with a message if neither."""
    sys.path.insert(0, str(fwreg.re_root() / "tools" / "src" / "dm32_patch_tools"))
    import cs_disasm  # dm32uv-re's; it only needs the standard library
    args = argparse.Namespace(backend="auto", bin=str(bin_path), base=hex(FLASH_BASE), lib=None, header=None)
    return cs_disasm.make_disassembler(args)


def disassembler_available():
    """True if dm32uv-re's disassembler can be built here (capstone-csky or objdump), without decoding anything."""
    try:
        sys.path.insert(0, str(fwreg.re_root() / "tools" / "src" / "dm32_patch_tools"))
        import cs_disasm
    except SystemExit:
        return False
    lib, _hdr, _tried = cs_disasm.find_capstone()
    return bool(lib) or shutil.which(OBJDUMP) is not None


def containing(ref, addr):
    """(name, start, size, insns) of the stock function holding addr, or None."""
    f = ref.containing(addr)
    if f is None:
        return None
    return f.name, f.addr, f.size, f.insns


def around(dis, addr, func):
    """Up to CONTEXT_BEFORE + CONTEXT_AFTER + 1 decoded instructions with ADDR among them, as (addr, mnem, ops, hex).
    With a containing function, decoding starts at one of its instruction boundaries so literal pools before ADDR
    are not read as code. Without one, decoding starts at ADDR."""
    if func is None or not func[3]:
        start, before = addr, 0
    else:
        starts = [a for a, _ in func[3]]
        i = max(0, bisect.bisect_right(starts, addr) - 1 - CONTEXT_BEFORE)
        start, before = starts[i], CONTEXT_BEFORE
    window = addr - start + 16 * (CONTEXT_AFTER + 2)
    decoded = dis.at(start, window=window, stop_at_return=False)
    j = next((k for k, row in enumerate(decoded) if row[0] >= addr), len(decoded))
    lo = max(0, j - before)
    return [(a, m, o, h) for a, m, o, h in decoded[lo:j + CONTEXT_AFTER]]


def overrides(fwid):
    """{reference address: (target address, why)} from firmware/<id>/overrides.json (hand proofs; may be absent)."""
    p = REPO / "firmware" / fwid / "overrides.json"
    if not p.exists():
        return {}
    return {int(k, 16): (int(o["to"], 16), o.get("why", "")) for k, o in json.loads(p.read_text())["overrides"].items()}


def translate(fwid, addr, n, expect):
    """What one other build does with the reference site: a dict, or one with "error" if fwmap cannot prove it."""
    out = {"fw": fwid}
    try:
        m = fwmap.FwMap(fwid)
    except (fwmap.Unresolved, SystemExit) as e:
        return dict(out, error=str(e))
    try:
        to, how = m.translate(addr)
    except fwmap.Unresolved as e:
        ovr = overrides(fwid).get(addr)          # a hand proof, as tools/gen_fw_maps.py takes it
        if ovr is None:
            return dict(out, error=str(e))
        to, how = ovr[0], f"{ovr[1]} [fwmap cannot prove it: {e}]"
    off = to - FLASH_BASE
    tb = m.img[off:off + n] if FLASH_BASE <= to < FLASH_END else b""

    def tr(v):
        try:
            return m.translate(v)[0]
        except fwmap.Unresolved:
            return -1

    try:
        why = m.site_equiv(addr, to, n, tr)
    except fwmap.Unresolved as e:
        why = str(e)
    out.update(to=to, how=how, bytes=tb, same_bytes=(tb == expect), site_equiv=why)
    path = REPO / "firmware" / fwid / "addrmap.json"
    entry = json.loads(path.read_text())["map"].get(f"0x{addr:08x}") if path.exists() else None
    if entry is None:
        out["addrmap"] = "no entry (not an address Kraken uses yet)"
    elif int(entry["to"], 16) == to:
        out["addrmap"] = f"agrees ({entry['to']})"
    else:
        out["addrmap"] = f"DIFFERS: addrmap.json says {entry.get('to')}"
    return out


def analyse(addr, n=DEFAULT_LEN):
    """Everything the probe reports, as data. Raises ValueError for an address or length it cannot describe."""
    if not FLASH_BASE <= addr < FLASH_END:
        raise ValueError(f"{addr:#x} is not a program-flash address (0x03000000-0x030fffff)")
    if n < 1 or addr + n > FLASH_END:
        raise ValueError(f"length {n} at {addr:#x} does not fit in program flash")
    ref = fwmap.reference()
    off = addr - FLASH_BASE
    expect = ref.img[off:off + n]
    func = containing(ref, addr)
    boundary = None
    end_boundary = None
    if func is not None and func[3]:
        starts = {a for a, _ in func[3]}
        boundary = addr in starts
        end_boundary = (addr + n) in starts or addr + n == func[1] + func[2]
    dis = load_disassembler(fwreg.base_path(ref.fw))
    rep = dict(addr=addr, len=n, function=func, boundary=boundary, end_boundary=end_boundary,
               disasm=around(dis, addr, func), expect=hexbytes(expect), expect_bytes=expect)
    rep["translations"] = [translate(fwid, addr, n, expect) for fwid in other_builds()]
    return rep


def render(rep, target=None):
    addr, n = rep["addr"], rep["len"]
    lines = [f"DM32.00.01.048  {addr:#010x}  ({n} byte{'s' if n != 1 else ''})"]
    fn = rep["function"]
    if fn:
        name, start, size, _ = fn
        lines.append(f"function: {name} at {start:#010x}, {size:#x} bytes (offset {addr - start:#x})")
    else:
        lines.append("function: none in dm32uv-re's symbol data")
    if rep["boundary"] is None:
        lines.append("instruction boundary: unknown (no function disassembly)")
    else:
        lines.append(f"instruction boundary: {'yes' if rep['boundary'] else 'NO (inside an instruction or a literal)'}"
                     f"; end at {addr + n:#x}: {'boundary' if rep['end_boundary'] else 'NOT a boundary'}")
    lines.append("disassembly:")
    for a, mnem, ops, h in rep["disasm"]:
        mark = "=>" if a == addr else "  "
        lines.append(f"  {mark} {a:08x}  {h:<12}  {mnem} {ops}".rstrip())
    lines.append(f'expect: "{rep["expect"]}"')
    for t in rep["translations"]:
        head = f"{t['fw']}:"
        if "error" in t:
            lines.append(f"{head} UNRESOLVED: {t['error']}")
            continue
        lines.append(f"{head} {t['to']:#010x}  ({t['how']})")
        lines.append(f"    bytes: \"{hexbytes(t['bytes'])}\"  same as 048: {'yes' if t['same_bytes'] else 'no'}")
        lines.append(f"    site_equiv: {'ok' if t['site_equiv'] is None else 'NOT ok: ' + t['site_equiv']}")
        lines.append(f"    addrmap.json: {t['addrmap']}")
    draft = {"name": "<fill>", "addr": f"0x{addr:08x}", "expect": rep["expect"],
             "target": f"@kraken:{target or '<SYMBOL or fill>'}",
             "why": "<fill: what stock does here and why replacing it is safe>"}
    lines.append("draft hooks.json entry (not written):")
    lines.append(json.dumps(draft))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addr", help="DM32.00.01.048 program-flash address, hex (e.g. 0x03038ab8)")
    ap.add_argument("--len", type=int, default=DEFAULT_LEN, help="bytes the hook overwrites (default 4)")
    ap.add_argument("--target", metavar="SYMBOL", help="fill the draft's target as @kraken:SYMBOL")
    a = ap.parse_args(argv)
    try:
        addr = int(a.addr, 16)
    except ValueError:
        ap.error(f"not a hex address: {a.addr}")
    try:
        rep = analyse(addr, a.len)
    except ValueError as e:
        ap.error(str(e))
    print(render(rep, a.target))


if __name__ == "__main__":
    main()
