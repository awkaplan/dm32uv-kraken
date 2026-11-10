"""fw_text.py - find, and rewrite, the stock addresses written in Kraken's sources, specs and tests.

One scanner serves both tools/gen_fw_maps.py (which lists every address so each gets a checked translation) and
tools/fw_translate.py (which writes the translated copies), so what is listed and what is rewritten cannot differ.

What counts as a stock address: a hex literal whose value lies in program flash below Kraken's window
(0x0300C000-0x030E0FFF), in IRAM/RAM (0x00010000-0x0005FFFF) or in SRAM below Kraken's window (0x18000000-
0x18004BFF). Where it is looked for:

  .c .h .S .ld   code only (C comments skipped); in .S also `movih rX, HI` followed by `ori rX, rX, LO`
  .py            NUMBER tokens, and hex literals inside string tokens (gdb commands, messages); comments skipped
  .json          strings that are a hex number, or contain one after an operator ("@blob:sym + 0x30afc20");
                 "expect"/"bytes"/"fill" byte strings and "why"/"name"/"_comment" text are left to the caller

firmware/not_addresses.json lists values in those ranges that are numbers, not addresses; they are never translated.
"""
import io
import json
import re
import tokenize

HEX = re.compile(r"\b0[xX]([0-9a-fA-F]+)([uUlL]*)\b")
MOVIH = re.compile(r"(movih\s+(r\d+)\s*,\s*)(0x[0-9a-fA-F]+|\d+)(\s*(?:/\*.*?\*/)?\s*\n\s*ori\s+\2\s*,\s*\2\s*,\s*)"
                   r"(0x[0-9a-fA-F]+|\d+)", re.S)
CODE_EXT = {".c", ".h", ".S", ".ld"}
TEXT_KEYS = {"why", "name", "_comment", "_note", "description", "justification", "owner", "source", "what", "t",
             "caller", "note", "label", "title"}
BYTE_KEYS = {"expect", "bytes", "fill"}


def is_stock(v):
    return 0x0300C000 <= v < 0x030E1000 or 0x00010000 <= v < 0x00060000 or 0x18000000 <= v < 0x18004C00


def _blank_comments(s):
    """C comments replaced by spaces (newlines kept), string literals left alone."""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == "\\" else 1
            out.append(s[i:j + 1])
            i = j + 1
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", s[i:j]))
            i = j
        elif s.startswith("//", i):
            j = s.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _spans_code(text, asm):
    """(start, end, value, fmt) of every stock literal in C/asm code; fmt rebuilds the literal for a new value."""
    code = _blank_comments(text)
    spans, taken = [], set()
    if asm:
        for m in MOVIH.finditer(code):
            hi, lo = int(m.group(3), 0), int(m.group(5), 0)
            if hi > 0xFFFF or lo > 0xFFFF:
                continue
            v = hi << 16 | lo
            if is_stock(v):
                spans.append((m.start(3), m.end(5), v,
                              lambda w, m=m: f"{w >> 16:#x}{m.group(4)}{w & 0xFFFF:#x}"))
                taken.update(range(m.start(3), m.end(5)))
    for m in HEX.finditer(code):
        if m.start() in taken:
            continue
        v = int(m.group(1), 16)
        if is_stock(v):
            spans.append((m.start(), m.end(), v, _hexfmt(m.group(1), m.group(2))))
    return spans


def _hexfmt(digits, suffix=""):
    """A formatter that writes a new value like the literal it replaces: same width, same letter case."""
    upper = any(c in "ABCDEF" for c in digits)
    width = len(digits)
    return lambda w: f"0x{w:0{width}{'X' if upper else 'x'}}{suffix}"


def _spans_py(text):
    """Python: hex NUMBER tokens (and hex inside strings). A number written as a subscript of an image -
    `img[0x3D556:0x3D558]`, `stock[0xC6000]` - is a FLASH OFFSET: its value is reported as 0x03000000 + it."""
    spans = []
    offs = [0]
    for ln in text.splitlines(keepends=True):
        offs.append(offs[-1] + len(ln))
    skip = {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in skip]
    stack = []
    for i, tok in enumerate(toks):
        if tok.type == tokenize.OP:
            if tok.string == "[":
                prev = toks[i - 1] if i else None
                sub = prev is not None and (prev.type == tokenize.NAME and prev.string not in ("in", "return", "and", "or", "not", "else", "if") or prev.string in (")", "]"))
                stack.append("sub" if sub else "list")
            elif tok.string in "({":
                stack.append("paren")
            elif tok.string in ")]}" and stack:
                stack.pop()
            continue
        fstr = hasattr(tokenize, "FSTRING_MIDDLE") and tok.type == tokenize.FSTRING_MIDDLE
        if tok.type not in (tokenize.NUMBER, tokenize.STRING) and not fstr:
            continue
        base = offs[tok.start[0] - 1] + tok.start[1]
        if tok.type == tokenize.NUMBER and stack and stack[-1] == "sub":
            m = HEX.fullmatch(tok.string)
            prev, nxt = toks[i - 1].string, toks[i + 1].string if i + 1 < len(toks) else ""
            if m and prev in ("[", ":") and nxt in (":", "]"):
                v = int(m.group(1), 16)
                if 0xC000 <= v < 0x100000:
                    f = _hexfmt(m.group(1))
                    spans.append((base, base + len(tok.string), 0x03000000 + v, lambda w, f=f: f(w - 0x03000000)))
                continue
        for m in HEX.finditer(tok.string):
            v = int(m.group(1), 16)
            if is_stock(v):
                spans.append((base + m.start(), base + m.end(), v, _hexfmt(m.group(1), m.group(2))))
    return spans


def spans(path, text):
    """Every stock literal in a source/test file: (start, end, value, fmt)."""
    sfx = path.suffix
    if sfx in CODE_EXT:
        return _spans_code(text, sfx in (".S", ".ld"))
    if sfx == ".py":
        return _spans_py(text)
    raise ValueError(f"{path}: no scanner for {sfx}")


def rewrite(text, sp, fn):
    """text with each span's value replaced by fmt(fn(value)); fn returns a value, or a string to put verbatim."""
    out, last = [], 0
    for s, e, v, fmt in sorted(sp, key=lambda t: t[0]):
        w = fn(v)
        out.append(text[last:s])
        out.append(w if isinstance(w, str) else fmt(w))
        last = e
    out.append(text[last:])
    return "".join(out)


# ---- JSON ----
def json_values(obj, key=None):
    """Every stock value in a JSON document (outside text and byte-string keys). A range's "end_exclusive" is not
    listed: json_map keeps the range's length from its translated start."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "expect" and isinstance(v, list):      # a copied stock table's expected words (data part stock_u32)
                yield from json_values(v, k)
                continue
            if k in TEXT_KEYS or k in BYTE_KEYS:
                continue
            if k == "end_exclusive":
                continue                                  # a range's end: the translator keeps its length
            yield from json_values(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from json_values(v, key)
    elif isinstance(obj, str):
        for m in HEX.finditer(obj):
            v = int(m.group(1), 16)
            if is_stock(v):
                yield v
    elif isinstance(obj, int) and not isinstance(obj, bool) and is_stock(obj):
        yield obj


def json_map(obj, fn, key=None):
    """A copy of obj with every stock value replaced by fn(value) (same text width); text/byte keys untouched."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in TEXT_KEYS or k in BYTE_KEYS:
                out[k] = v
            elif k == "end_exclusive" and "start" in obj:
                lo, hi = int(obj["start"], 16), int(v, 16)
                out[k] = _hexfmt(HEX.fullmatch(v).group(1))(fn(lo) + (hi - lo)) if is_stock(lo) else v
            else:
                out[k] = json_map(v, fn, k)
        return out
    if isinstance(obj, list):
        return [json_map(v, fn, key) for v in obj]
    if isinstance(obj, str):
        def sub(m):
            v = int(m.group(1), 16)
            return _hexfmt(m.group(1), m.group(2))(fn(v)) if is_stock(v) else m.group(0)
        return HEX.sub(sub, obj)
    if isinstance(obj, int) and not isinstance(obj, bool) and is_stock(obj):
        return fn(obj)
    return obj


def load_json(path):
    return json.loads(path.read_text())
