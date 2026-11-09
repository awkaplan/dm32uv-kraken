#!/usr/bin/env python3
"""Host test for the pure logic of F13 "Burst Voice" (docs/burst-voice.md section 11): the message pack / unpack between the unit store and
the burst payload (patches/kraken_ui/krk_bvmsg.c), the bigbuf layout constants shared with the linker script, the header flags, and the
end-to-end path units -> pack -> BV1 modem -> PCM -> BV1 receiver -> unpack -> units (clean and with a lost block).

It also (re)writes the golden data the in-target check uses (tests/data/f13_burst.pcm + f13_golden.json) and fails if the committed copy differs.
  python3 tests/test_f13.py           check
  python3 tests/test_f13.py --regen   rewrite the golden data
"""
import ctypes
import hashlib
import json
import pathlib
import re
import sys
import tempfile

import hostcc
from hostcc import HERE, ROOT, UI
from hostlib import check, finish

DATA = HERE / "data"
WORK = pathlib.Path(tempfile.mkdtemp(prefix="f13test"))
SRC = [UI / f for f in ("krk_bv_modem.c", "krk_bv_frame.c", "krk_bv_sound.c", "krk_ambe.c", "krk_bvmsg.c")] + [HERE / "f13_glue.c"]
hostcc.build(SRC, flags=["-O2", "-Wall", "-Wextra", "-Werror"], shared=True, out=WORK / "libf13.so", libs=["-lm"])
L = ctypes.CDLL(str(WORK / "libf13.so"))
L.f13t_pack.restype = ctypes.c_int
L.f13t_rx.restype = ctypes.c_int
L.f13t_tx.restype = ctypes.c_int
UB = ctypes.c_ubyte


def units(n, layout, seed=7, sil=0):
    b = (UB * (27 * n))()
    L.f13t_units(b, n, layout, seed, sil)
    return b


def pack(u, n):
    out = (UB * 2106)()
    fl, ly = ctypes.c_int(), ctypes.c_int()
    r = L.f13t_pack(u, n, out, ctypes.byref(fl), ctypes.byref(ly))
    return r, bytes(out), fl.value, ly.value


def burst(chunks, nchunks, flags, mode, rms=4000):
    buf = (ctypes.c_short * 60000)()
    n = L.f13t_tx(mode, chunks, nchunks, flags, buf, 60000, rms)
    return list(buf[:n])


def receive(pcm, cap_units=92):
    pcm = [0] * 4096 + list(pcm) + [0] * 3072          # the receiver measures the noise floor first and waits for the quiet after the burst
    x = (ctypes.c_short * len(pcm))(*pcm)
    buf = (UB * (78 * 27))()
    un = (UB * (27 * cap_units))()
    st = (ctypes.c_int * 4)()
    n = L.f13t_rx(x, len(pcm), buf, un, cap_units, st)
    return n, bytes(un), list(st)


# ---------------------------------------------------------------------------------------------------------------------------
print("== layout constants (krk_bvscr.c, krk_bvmsg.h, kraken_ui4.ld, allocations.json)")
scr = (UI / "krk_bvscr.c").read_text()
msg_h = (UI / "krk_bvmsg.h").read_text()
ld = (UI / "kraken_ui4.ld").read_text()
nu = int(re.search(r"#define BVM_NU_MAX\s+(\d+)", msg_h)[1])
bcap = int(re.search(r"#define BVM_B_CAP\s+(\d+)", msg_h)[1])
maxch = int(re.search(r"#define BVM_MAX_CHUNKS\s+(\d+)", msg_h)[1])
bvt_c = int(re.search(r"#define BVT_SIZE\s+(0x[0-9a-f]+)", scr)[1], 16)
bvt_ld = int(re.search(r"BVT_SIZE = (0x[0-9a-f]+);", ld)[1], 16)
big_end = nu * 27 + bcap + 512
check(bvt_c == bvt_ld, "BVT_SIZE in krk_bvscr.c equals kraken_ui4.ld", extra=f"{bvt_c:#x}")
check(big_end <= 5632 - bvt_c, "message + payload + scratch fit below the tail of the 5632-byte bigbuf", extra=f"{big_end} <= {5632 - bvt_c}")
check(maxch * 27 <= bcap and (maxch + 5) // 6 <= 40 and maxch % 6 == 0, "78 chunks = 13 whole blocks, <= BV_MAX_BLOCKS, fit the payload buffer")
check(nu * 27 + bcap + 512 + bvt_c <= 5632, "owner layout sums to <= 5632 B")
alloc = {a["name"]: a for a in json.load(open(ROOT / "allocations.json"))["allocations"]}
check(int(alloc["kraken_bigbuf"]["start"], 16) + 0x1600 == int(alloc["kraken_ui4_state"]["start"], 16),
      "bigbuf ends where the ui4 state claim starts (tail stays inside the claim)")
check("ALIGN(0x20)" in ld and "KEEP(*(.rodata.krk_bv_screen))" in ld and ld.index("krk_bv_screen") < ld.index("krk_bv_hooks"),
      "screen descriptor first at the origin, hook table aligned to 0x20 (0x030e8020)")

print("== header flags")
check(all(L.f13t_hdr_flags_roundtrip(f) == 1 for f in (0, 1, 2, 4, 0x3F, 0x7F, 0xFF)), "header flags byte round-trips (0x3F = layout 7 + trimmed + stripped)")

print("== pack / unpack, no radio")
for layout in (1, 2, 3, 4):
    for n, sil in ((1, 0), (5, 0), (40, 3), (92, 0), (92, 4)):
        u = units(n, layout, seed=11 + n + layout, sil=sil)
        r, out, fl, ly = pack(u, n)
        ok = r > 0 and ly == layout and fl & 3 == 3 and (fl >> 3) & 7 == layout and r <= maxch
        # rebuild through a synthetic info: the framing is exercised end to end below, here the codec alone
        check(ok, f"layout {layout} n={n} sil={sil}: packed to {r} chunks, flags {fl:#04x}, detected layout {ly}")
check(pack(units(92, 1, 3), 92)[0] <= 63, "worst case (no silence) 92 units packs to <= 63 chunks (1 + 92*147/8 bytes)", extra=str(pack(units(92, 1, 3), 92)[0]))
rnd = bytes((i * 37 + 11) % 251 for i in range(27 * 78))
r, out, fl, ly = pack((UB * len(rnd)).from_buffer_copy(rnd), 78)
check(r == 78 and fl == 0 and ly == 0 and out[:27 * 78] == rnd, "units without an AMBE layout go raw (flags 0), 78 units fit")
r2 = pack((UB * (27 * 79)).from_buffer_copy(rnd + bytes(27)), 79)[0]
check(r2 == -2, "79 raw units are refused (-2)")
check(L.f13t_pack((UB * 27)(), 0, (UB * 2106)(), ctypes.byref(ctypes.c_int()), ctypes.byref(ctypes.c_int())) == -1, "no units: -1")

print("== end to end: units -> pack -> modem -> receiver -> unpack")
results = {}
for mode in (1, 0):
    for layout in (1, 3):
        n = 40
        u = units(n, layout, seed=100 + layout, sil=5)
        r, out, fl, ly = pack(u, n)
        pcm = burst((UB * (r * 27)).from_buffer_copy(out[:r * 27]), r, fl, mode)
        got, un, st = receive(pcm)
        # silence frames come back as the canonical silence frame: compare through a second pack (bit exact apart from that)
        r2, out2, fl2, ly2 = pack((UB * (27 * got)).from_buffer_copy(un[:27 * got]), got) if got > 0 else (0, b"", 0, 0)
        check(got == n and st[1] == st[2] and r2 == r and out2[:r * 27] == out[:r * 27],
              f"mode {mode} layout {layout}: {got}/{n} units back, blocks {st[1]}/{st[2]}, re-pack identical", extra=f"samples {len(pcm)}, air {len(pcm) / 8000:.2f} s")
        results[(mode, layout)] = (got, st)
# a non-silent stream comes back bit exact
u = units(30, 2, seed=5, sil=0)
r, out, fl, ly = pack(u, 30)
got, un, st = receive(burst((UB * (r * 27)).from_buffer_copy(out[:r * 27]), r, fl, 1))
check(got == 30 and un[:810] == bytes(u), "stripped, no silence: units bit exact", extra=f"{r} chunks")
# raw
rawu = (UB * (27 * 24)).from_buffer_copy(rnd[:27 * 24])
r, out, fl, ly = pack(rawu, 24)
pcm = burst((UB * (r * 27)).from_buffer_copy(out[:r * 27]), r, fl, 1)
got, un, st = receive(pcm)
check(r == 24 and fl == 0 and got == 24 and un[:648] == rnd[:648], "raw units: 24 units bit exact over the air")
# a lost block of a raw message plays as silence units, the neighbours are intact
blk = 258 * 8 + 0           # samples of the fixed overhead (516 symbols * 4 samples / 2000 baud... = 1032 samples)
fixed = 516 * 4
per_block_fast = 182 * 8 * 8000 // 6000          # samples per fast block (1941)
cut = list(pcm)
a0 = fixed + per_block_fast * 2 + 60
for i in range(a0, a0 + per_block_fast - 80):
    cut[i] = (i * 7919) % 2001 - 1000            # destroy block 1 (noise)
got, un, st = receive(cut)
sil = bytes.fromhex("b9e88152617300 2a6b".replace(" ", "")) * 3
# (a destroyed block can drag the following ones with it: the receiver may lose lock; whatever is lost must be silence, whatever
# arrived must be exact, and the message must still be 24 units long)
lost = [b for b in range(4) if un[162 * b:162 * (b + 1)] == sil * 6]
exact = [b for b in range(4) if un[162 * b:162 * (b + 1)] == rnd[162 * b:162 * (b + 1)]]
check(got == 24 and st[1] < st[2] and len(lost) == st[2] - st[1] and len(exact) == st[1] and 2 not in exact,
      "raw message, a block destroyed: lost blocks play as silence units, received blocks are exact", extra=f"blocks {st[1]}/{st[2]}, lost {lost}, exact {exact}")

print("== Burst Voice key 9: deterministic non-silent test message")
tm = (UB * (27 * 92))()
bad = ctypes.c_int()
nsil = L.f13t_testmsg(tm, 92, ctypes.byref(bad))
tmb = bytes(tm)
tm2 = (UB * (27 * 92))()
L.f13t_testmsg(tm2, 92, ctypes.byref(ctypes.c_int()))
check(nsil == 0 and bad.value == 0, "test message: 276 frames, none silence, all FEC-clean", extra=f"silent {nsil}, corrected {bad.value}")
check(bytes(tm2) == tmb and tmb != bytes(len(tmb)), "test message is deterministic and non-zero")
r, out, fl, ly = pack(tm, 92)
check(ly == 1 and r > 1 and r > 4 and fl & 3 == 3, "test message packs to a multi-block burst in layout L1", extra=f"{r} chunks = {(r + 5) // 6} blocks, flags {fl:#04x}")
got, un, st = receive(burst((UB * (r * 27)).from_buffer_copy(out[:r * 27]), r, fl, 1))
check(got == 92 and un[:27 * 92] == tmb and st[1] == st[2], "test message: pack -> burst -> receive -> unpack is unit-for-unit bit exact", extra=f"{got} units, blocks {st[1]}/{st[2]}")
r2, out2, fl2, ly2 = pack((UB * (27 * got)).from_buffer_copy(un[:27 * got]), got)
check(r2 == r and out2[:r * 27] == out[:r * 27], "re-pack of the received message is identical (idempotent)")

print("== golden data for the in-target check")
gu = bytes((i * 131 + 7) % 253 for i in range(27 * 12))          # 12 raw units (not valid AMBE: the raw path)
gr, gout, gfl, gly = pack((UB * len(gu)).from_buffer_copy(gu), 12)
check(gr == 12 and gfl == 0, "golden message is raw, 12 units, 2 blocks")
gpcm = burst((UB * (gr * 27)).from_buffer_copy(gout[:gr * 27]), gr, gfl, 1)
pcm_bytes = b"".join(int(v & 0xFFFF).to_bytes(2, "little") for v in gpcm)
golden = {"units_hex": gu.hex(), "mode": 1, "msg_id": 0x5A5, "src_id": 0x123456, "rms": 4000, "flags": gfl, "chunks": gr,
          "samples": len(gpcm), "first_blocks": 6, "tx_sha256": hashlib.sha256(pcm_bytes[:6 * 512]).hexdigest(),
          "pcm_sha256": hashlib.sha256(pcm_bytes).hexdigest()}
if "--regen" in sys.argv:
    DATA.mkdir(exist_ok=True)
    (DATA / "f13_burst.pcm").write_bytes(pcm_bytes)
    (DATA / "f13_golden.json").write_text(json.dumps(golden, indent=1) + "\n")
    print("regenerated tests/data/f13_burst.pcm and f13_golden.json")
cur = json.loads((DATA / "f13_golden.json").read_text()) if (DATA / "f13_golden.json").exists() else {}
check(cur == golden and (DATA / "f13_burst.pcm").exists() and (DATA / "f13_burst.pcm").read_bytes() == pcm_bytes,
      "committed golden data equals the current host build", extra=f"{len(gpcm)} samples, first-6-blocks sha {golden['tx_sha256'][:12]}")
got, un, st = receive(gpcm)
check(got == 12 and un[:324] == gu, "golden burst decodes on the host")

finish(fail="{n} FAILED: {fails}")
