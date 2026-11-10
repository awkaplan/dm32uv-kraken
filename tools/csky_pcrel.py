"""csky_pcrel.py - instruction lengths and PC-relative operands of C-SKY v2 (CK803S) code, in plain Python.

Only what tools/fwmap.py needs to tell whether two copies of stock code are the same code moved to another
address: how long an instruction is, and, for the instructions whose encoding depends on where they sit,
what they point at. Everything else is compared byte for byte.

  insn_len(img, off)      2 or 4 (the first halfword's top two bits are 11 for a 32-bit instruction)
  decode(img, off, pc)    (kind, target) for a PC-relative instruction at file offset `off` executing at `pc`,
                          else None. kind is "call" (bsr), "branch" (br/bt/bf/bez/bnez/bhz/blsz/blz/bhsz),
                          "pool" (lrw: target is the address of the literal word; jmpi/jsri the same)
  encode_like(img, off, pc, target)
                          the same instruction (same opcode, same register) re-encoded to reach `target` from
                          `pc`, or None if it cannot (out of range, or not PC-relative)

Halfwords are little-endian; a 32-bit instruction is the first halfword (high part) then the second.
tests/test_csky_pcrel.py checks every instruction of dm32uv-re's disassembly of DM32.00.01.048 against this.
"""


def _h(img, off):
    return img[off] | img[off + 1] << 8


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def insn_len(img, off):
    return 4 if (_h(img, off) & 0xC000) == 0xC000 else 2


# 32-bit branch-on-condition-or-register forms: first halfword with the low 5 bits (register) cleared
_BR32 = {0xE800: "br", 0xE860: "bt", 0xE840: "bf"}                    # register field must be 0
_BRZ32 = {0xE900: "bez", 0xE920: "bnez", 0xE940: "bhz", 0xE960: "blsz", 0xE980: "blz", 0xE9A0: "bhsz"}


def decode(img, off, pc):
    h1 = _h(img, off)
    if (h1 & 0xC000) != 0xC000:                                         # 16-bit
        top = h1 & 0xFC00
        if top in (0x0400, 0x0800, 0x0C00):                             # br16 / bt16 / bf16
            return "branch", pc + (_sext(h1 & 0x3FF, 10) << 1)
        if top == 0x1000:                                               # lrw16 rz, [pc + imm7*4]
            imm = ((h1 >> 3) & 0x60) | (h1 & 0x1F)
            return "pool", (pc + (imm << 2)) & ~3
        return None
    h2 = _h(img, off + 2)
    if (h1 >> 10) == 0b111000:                                          # bsr32
        return "call", pc + (_sext((h1 & 0x3FF) << 16 | h2, 26) << 1)
    if h1 in _BR32:
        return "branch", pc + (_sext(h2, 16) << 1)
    if (h1 & 0xFFE0) in _BRZ32:
        return "branch", pc + (_sext(h2, 16) << 1)
    if (h1 & 0xFFE0) == 0xEA80 or h1 in (0xEAC0, 0xEAE0):              # lrw32 / jmpi / jsri
        return "pool", (pc + (h2 << 2)) & ~3
    return None


def encode_like(img, off, pc, target):
    """Bytes of the instruction at `off` re-encoded to reach `target` from `pc`; None if not possible."""
    d = decode(img, off, pc)
    if d is None:
        return None
    h1 = _h(img, off)
    kind = d[0]
    if (h1 & 0xC000) != 0xC000:
        top = h1 & 0xFC00
        if top in (0x0400, 0x0800, 0x0C00):
            delta = target - pc
            if delta & 1 or not -0x400 <= delta >> 1 < 0x200:
                return None
            v = top | ((delta >> 1) & 0x3FF)
            return bytes([v & 0xFF, v >> 8])
        delta = target - (pc & ~3)
        if delta & 3 or target & 3:
            return None
        imm = delta >> 2
        # lrw16's pool address is (pc + imm*4) & ~3: try the imm values around it
        for k in (imm, imm + 1, imm - 1):
            if 0 <= k < 0x80 and ((pc + (k << 2)) & ~3) == target:
                v = (h1 & 0xFC00) | ((k & 0x60) << 3) | (h1 & 0xE0) | (k & 0x1F)
                return bytes([v & 0xFF, v >> 8])
        return None
    if kind == "call":
        delta = target - pc
        if delta & 1 or not -(1 << 25) <= delta >> 1 < (1 << 25):
            return None
        v = (delta >> 1) & 0x3FFFFFF
        a, b = 0xE000 | (v >> 16), v & 0xFFFF
        return bytes([a & 0xFF, a >> 8, b & 0xFF, b >> 8])
    if kind == "branch":
        delta = target - pc
        if delta & 1 or not -0x8000 <= delta >> 1 < 0x8000:
            return None
        b = (delta >> 1) & 0xFFFF
        return bytes([h1 & 0xFF, h1 >> 8, b & 0xFF, b >> 8])
    for k in range(max(0, (target - pc) >> 2) - 1, ((target - pc) >> 2) + 2):   # pool
        if 0 <= k < 0x10000 and ((pc + (k << 2)) & ~3) == target:
            return bytes([h1 & 0xFF, h1 >> 8, k & 0xFF, k >> 8])
    return None
