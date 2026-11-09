#!/usr/bin/env python3
"""Fail if any patch assembly source branches (bsr/br/jbsr/jbr) to an absolute address or an
.equ-defined absolute symbol: this C-SKY gas silently emits NOTHING for `bsr <absolute>`
(found 2026-10-15). Call stock code by name instead (tools/link/dm32_stock_symbols.ld resolves it).

Macro-aware: `.macro` definitions are parsed and every invocation is expanded (arguments
substituted, nested macros expanded) before the branch check, so `bsr \\h` inside a macro body
is checked against the value each call site passes. It does NOT check assembled sizes.

Usage: check_bsr.py [dir ...] (default: patches/). Exit 1 on any hit.
       check_bsr.py --self-test    only the built-in fixtures (a normal run does them first too)."""
import pathlib, re, sys, tempfile
REPO = pathlib.Path(__file__).resolve().parent.parent

BR = re.compile(r"\s*(?:\w+:\s*)*(j?bsr|j?br)\s+([\w.]+)")
MACRO = re.compile(r"\s*\.macro\s+(\w+)\s*(.*)")
ENDM = re.compile(r"\s*\.endm\b")


def strip(line):
    return line.split("/*")[0].split("//")[0]


def parse_params(s):
    """`lo, hi=3` or `lo hi` -> [(name, default|None)]"""
    out = []
    for p in re.split(r"[,\s]+", s.strip()):
        if p:
            n, _, d = p.partition("=")
            out.append((n, d if _ else None))
    return out


def parse(text):
    """-> (macros{name: (params, [body lines])}, [(lineno, code)] of top-level lines, equs)"""
    macros, top, cur = {}, [], None
    equs = set(re.findall(r"^\s*\.(?:equ|set)\s+(\w+)\s*,", text, re.M))
    for n, line in enumerate(text.splitlines(), 1):
        code = strip(line)
        if cur is None:
            m = MACRO.match(code)
            if m:
                cur = (m.group(1), parse_params(m.group(2)), [])
            else:
                top.append((n, code))
        elif ENDM.match(code):
            macros[cur[0]] = (cur[1], cur[2])
            cur = None
        else:
            cur[2].append((n, code))
    return macros, top, equs


def expand(code, macros, depth=0):
    """Expand one source line to the list of lines it assembles from (itself if not a macro call)."""
    m = re.match(r"\s*(?:\w+:\s*)*(\w+)\s*(.*)", code)
    if not m or m.group(1) not in macros or depth > 8:
        return [code]
    params, body = macros[m.group(1)]
    args = [a.strip() for a in re.split(r"[,\s]+", m.group(2).strip()) if a.strip()]
    sub = {}
    for i, (pn, dflt) in enumerate(params):
        sub[pn] = args[i] if i < len(args) else (dflt or "")
    out = []
    for _, b in body:
        for pn in sorted(sub, key=len, reverse=True):
            b = b.replace("\\" + pn, sub[pn])
        out.extend(expand(b, macros, depth + 1))
    return out


def check_text(text, name):
    macros, top, equs = parse(text)
    bad = []
    for n, code in top:
        for ex in expand(code, macros):
            m = BR.match(ex)
            if m and (m.group(2) in equs or re.fullmatch(r"0x[0-9a-fA-F]+|\d+", m.group(2))):
                via = "" if ex == code else f" (macro expansion of `{code.strip()}`)"
                bad.append(f"{name}:{n}: `{m.group(1)} {m.group(2)}` targets an absolute value: "
                           f"gas drops it silently{via}")
    return bad


def self_test():
    good = ".macro CALL h\n bsr \\h\n.endm\n.equ ABS, 0x1000\nf: CALL krk_func\n bsr 1f\n1: rts\n"
    bad_macro = ".macro CALL h\n bsr \\h\n.endm\n.equ ABS, 0x1000\nf: CALL ABS\n"
    bad_hex = ".macro OUTER h\n CALL \\h\n.endm\n.macro CALL h\n bsr \\h\n.endm\nOUTER 0x03045082\n"
    bad_plain = ".equ ABS, 0x1000\n bsr ABS\n"
    ok = (not check_text(good, "good") and len(check_text(bad_macro, "m")) == 1
          and len(check_text(bad_hex, "n")) == 1 and len(check_text(bad_plain, "p")) == 1)
    print("check_bsr self-test:", "PASS" if ok else "FAIL")
    return ok


def main(argv):
    if argv[:1] == ["--self-test"]:
        return 0 if self_test() else 1
    if not self_test():     # the checker proves itself before it is trusted on the real sources
        return 1
    bad = []
    for root in (argv or [REPO / "patches"]):
        for f in pathlib.Path(root).rglob("*.S"):
            bad += check_text(f.read_text(), f)
    if bad:
        print("\n".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
