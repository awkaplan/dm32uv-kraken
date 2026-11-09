#!/usr/bin/env python3
"""Host test for F13 AMBE+2 FEC stripping and silence trim: patches/kraken_ui/krk_ambe.c (+ the krk_bv_*.c TX path for the air time).

Run: python3 tests/test_ambe.py      (a few seconds, fixed seeds)
Cross-checks: the C code against an independent python model of the same published algorithm (mbelib/DSD), and against the one
published full frame we know in both forms: MMDVM DMR_SILENCE_DATA B9E881526173002A6B (air) == F801A99F8CE080 (49-bit params).
"""
import ctypes
import pathlib
import random
import re
import subprocess
import tempfile

import hostcc
from hostcc import HERE, UI
from hostlib import check, finish

SRC = [UI / "krk_ambe.c"]
BVSRC = [UI / "krk_bv_modem.c", UI / "krk_bv_frame.c", UI / "krk_bv_sound.c"]
WORK = pathlib.Path(tempfile.mkdtemp(prefix="ambetest"))

hostcc.build(SRC + [HERE / "ambe_glue.c"], flags=["-O2", "-Wall", "-Wextra", "-Werror"], shared=True, out=WORK / "libambe.so")
hostcc.build(BVSRC, flags=["-O2", "-w"], shared=True, out=WORK / "libbv.so", libs=["-lm"])
A = ctypes.CDLL(str(WORK / "libambe.so"))
B = ctypes.CDLL(str(WORK / "libbv.so"))
B.krk_bv_tx_samples.restype = ctypes.c_uint
UB = ctypes.c_ubyte
U4 = ctypes.c_uint * 4
L_RAW, L_AIR, L_PLAIN, L_AIR_LSB, L_PLAIN_LSB = range(5)
NAMES = {1: "L1 air", 2: "L2 plain", 3: "L1 lsb", 4: "L2 lsb"}


def cb(b):
    return (UB * len(b))(*b)


# ---------------------------------------------------------------------------------------------------------------------
# independent python model (mbelib / DSD)
# ---------------------------------------------------------------------------------------------------------------------
rW = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2]
rX = [23, 10, 22, 9, 21, 8, 20, 7, 19, 6, 18, 5, 17, 4, 16, 3, 15, 2, 14, 1, 13, 0, 12, 10, 11, 9, 10, 8, 9, 7, 8, 6, 7, 5, 6, 4]
rY = [0, 2, 0, 2, 0, 2, 0, 2, 0, 3, 0, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3]
rZ = [5, 3, 4, 2, 3, 1, 2, 0, 1, 13, 0, 12, 22, 11, 21, 10, 20, 9, 19, 8, 18, 7, 17, 6, 16, 5, 15, 4, 14, 3, 13, 2, 12, 1, 11, 0]
SIZES = [24, 23, 11, 14]


def g23(d):
    r = d << 11
    for i in range(22, 10, -1):
        if r >> i & 1:
            r ^= 0xC75 << (i - 11)
    return (d << 11) | (r & 0x7FF)


def pyenc(info49):
    """49-bit int -> 72-bit air int (even C0 parity)."""
    d = [(info49 >> (48 - i)) & 1 for i in range(49)]
    v = lambda bits: int("".join(map(str, bits)), 2)
    u0, u1, c2, c3 = v(d[0:12]), v(d[12:24]), v(d[24:35]), v(d[35:49])
    c0 = g23(u0)
    c0 = (c0 << 1) | (bin(c0).count("1") & 1)
    pr, c1 = 16 * u0, g23(u1)
    for k in range(1, 24):
        pr = (173 * pr + 13849) & 0xFFFF
        c1 ^= (pr >> 15) << (23 - k)
    cw = [c0, c1, c2, c3]
    fr = {(c, j): cw[c] >> j & 1 for c in range(4) for j in range(SIZES[c])}
    out = 0
    for i in range(36):
        out = (out << 2) | (fr[(rW[i], rX[i])] << 1) | fr[(rY[i], rZ[i])]
    return out


def info_bytes(x):
    return (x << 7).to_bytes(7, "big")


def rnd_info(rng, silent=None):
    """Random 49-bit parameter vector; silent=None any non-silent b0 (< 120), True: b0 = 124/125 with random other bits."""
    while True:
        x = rng.getrandbits(49)
        b0 = ((x >> 43) << 1) | (x & 1)
        if silent is None and b0 < 120:
            return x
        if silent:
            b0 = rng.choice((124, 125))
            return (x & ~((0x3F << 43) | 1)) | ((b0 >> 1) << 43) | (b0 & 1)


def unit_from_infos(infos, layout):
    u = (UB * 27)()
    for k, x in enumerate(infos):
        cw = U4()
        A.krk_ambe_info_to_cw(cb(info_bytes(x)), cw)
        A.krk_ambe_unit_put(u, k, layout, cw)
    return bytes(u)


def infos_from_unit(u, layout):
    res = []
    for k in range(3):
        cw = U4()
        info = (UB * 7)()
        A.krk_ambe_unit_get(cb(u), k, layout, cw)
        A.krk_ambe_cw_to_info(cw, info, None, None)
        res.append(int.from_bytes(bytes(info), "big") >> 7)
    return res


# ---------------------------------------------------------------------------------------------------------------------
print("== tables and published vectors ==")
perm = {(rW[i], rX[i]) for i in range(36)} | {(rY[i], rZ[i]) for i in range(36)}
check(len(perm) == 72 and perm == {(c, j) for c in range(4) for j in range(SIZES[c])},
      "DSD rW/rX/rY/rZ is a permutation of the 72 codeword bit positions (C0 24, C1 23, C2 11, C3 14)")
SIL49 = int.from_bytes(bytes.fromhex("F801A99F8CE080"), "big") >> 7
MMDVM = bytes.fromhex("B9E881526173002A6B")
check(pyenc(SIL49).to_bytes(9, "big") == MMDVM, "python model: F801A99F8CE080 -> B9E881526173002A6B (MMDVM DMR_SILENCE_DATA, air order)")
cw = U4()
A.krk_ambe_info_to_cw(cb(info_bytes(SIL49)), cw)
air = (UB * 9)()
A.krk_ambe_cw_to_air(cw, air)
check(bytes(air) == MMDVM, "C: silence 49-bit params -> MMDVM silence frame (checks Golay + even parity + PN + interleave in one go)")
cw2 = U4()
A.krk_ambe_air_to_cw(cb(MMDVM), cw2)
inf = (UB * 7)()
c0e, c1e = ctypes.c_int(), ctypes.c_int()
e = A.krk_ambe_cw_to_info(cw2, inf, ctypes.byref(c0e), ctypes.byref(c1e))
check(bytes(inf) == bytes.fromhex("F801A99F8CE080") and e == 0, "C: MMDVM silence frame -> F801A99F8CE080, 0 errors")
check(A.krk_ambe_info_is_silence(inf) == 1, "silence detector fires on the canonical pattern (b0 = 125)")
s2 = (UB * 7)()
A.krk_ambe_silence_info(s2)
check(bytes(s2) == bytes.fromhex("F801A99F8CE080"), "canonical silence info bytes")
# PN: LCG pr[i+1] = 173 pr[i] + 13849 mod 65536 (TIA-102.BABA IMBE and mbelib), seed 16 * u0
pr = [0]
for _ in range(3):
    pr.append((173 * pr[-1] + 13849) & 0xFFFF)
check(pr[1:3] == [13849, 174 * 13849 % 65536] and 174 * 13849 % 65536 == 50430, "LCG from seed 0: 13849, 50430 (hand-computed: 173 * 13849 + 13849 = 2409726 = 36 * 65536 + 50430)")
A.krk_ambe_pn_mask.restype = ctypes.c_uint
exp_mask = 0
p = 16 * 0xABC
for k in range(1, 24):
    p = (173 * p + 13849) & 0xFFFF
    exp_mask |= (p >> 15) << (23 - k)
check(A.krk_ambe_pn_mask(0xABC) == exp_mask, "PN mask matches the python model (seed 0xABC)")
rng = random.Random(1)
ok = 0
for _ in range(300):
    x = rng.getrandbits(49)
    c = U4()
    A.krk_ambe_info_to_cw(cb(info_bytes(x)), c)
    a9 = (UB * 9)()
    A.krk_ambe_cw_to_air(c, a9)
    ok += int.from_bytes(bytes(a9), "big") == pyenc(x)
check(ok == 300, f"C encoder == python model on 300 random 49-bit vectors ({ok}/300)")

print("\n== Golay ==")
A.krk_golay23_encode.restype = ctypes.c_uint
A.krk_golay24_encode.restype = ctypes.c_uint
bad = 0
wts = set()
cws = []
for m in range(4096):
    c = A.krk_golay23_encode(m)
    cws.append(c)
    bad += c != g23(m)
check(bad == 0, "golay23 encode matches the python model for all 4096 messages")
mind = min(bin(c).count("1") for c in cws if c)
check(mind == 7, f"golay23 minimum weight {mind} (d = 7)")
c24 = [A.krk_golay24_encode(m) for m in range(4096)]
check(min(bin(c).count("1") for c in c24 if c) == 8 and all(bin(c).count("1") % 2 == 0 for c in c24), "golay24 minimum weight 8, all words even weight")
cnt = 0
for m in range(4096):
    r = ctypes.c_uint(c24[m])
    d = ctypes.c_uint()
    cnt += A.krk_golay24_decode(ctypes.byref(r), ctypes.byref(d)) == 0 and d.value == m
check(cnt == 4096, "golay24 clean decode of all 4096 messages, 0 errors")
out = (ctypes.c_int * 4)()
A.ambet_golay_sweep(16, out)
check(out[1] == 0 and out[0] == 256 * 2324, f"all 1-3 bit error patterns over 24 bits corrected exactly for 256 messages: {out[0]} patterns, {out[1]} wrong",
      extra="linear code: the error pattern result does not depend on the message")
check(out[3] == 0 and out[2] == 10626, f"all {out[2]} 4-bit patterns detected (decode returns -1)")
# 23-bit perfect code: every 4-error pattern decodes to some codeword (no failures)
r = ctypes.c_uint(cws[7] ^ 0b11110)
check(A.krk_golay23_decode(ctypes.byref(r)) == 3, "golay23 is perfect: 4 errors miscorrect with 3 'corrections' (why C0's parity bit matters)")

print("\n== frame <-> info ==")
rng = random.Random(2)
for lay in (L_AIR, L_PLAIN):
    bad = 0
    for _ in range(200):
        infos = [rnd_info(rng) for _ in range(3)]
        bad += infos_from_unit(unit_from_infos(infos, lay), lay) != infos
    check(bad == 0, f"{NAMES[lay]}: info -> unit -> info exact on 200 random units")
# unit in layout 1 equals three consecutive MMDVM-style frames
infos = [rnd_info(rng) for _ in range(3)]
u1 = unit_from_infos(infos, L_AIR)
check(u1 == b"".join(pyenc(x).to_bytes(9, "big") for x in infos), "L1 unit == 3 consecutive air-order frames (python model)")
# error correction of C0 / C1
fixed_ok = 0
tot = 0
st_fix = 0
for _ in range(300):
    infos = [rnd_info(rng) for _ in range(3)]
    for lay in (L_AIR, L_PLAIN):
        u = bytearray(unit_from_infos(infos, lay))
        nerr = 0
        for k in range(3):
            cwk = U4()
            A.krk_ambe_unit_get(cb(bytes(u)), k, lay, cwk)
            n0, n1 = rng.randint(0, 3), rng.randint(0, 3)
            for b in rng.sample(range(24), n0):
                cwk[0] ^= 1 << b
            for b in rng.sample(range(23), n1):
                cwk[1] ^= 1 << b
            nerr += n0 + n1
            A.krk_ambe_unit_put((UB * 27).from_buffer(u), k, lay, cwk)
        res = infos_from_unit(bytes(u), lay)
        tot += 1
        fixed_ok += res == infos
check(fixed_ok == tot, f"0-3 random errors in every C0 and C1: info recovered {fixed_ok}/{tot}")
# error counts reported
cwk = U4()
A.krk_ambe_info_to_cw(cb(info_bytes(SIL49)), cwk)
cwk[0] ^= 0b101
cwk[1] ^= 0b1
e = A.krk_ambe_cw_to_info(cwk, inf, ctypes.byref(c0e), ctypes.byref(c1e))
check((e, c0e.value, c1e.value) == (3, 2, 1) and bytes(inf) == bytes.fromhex("F801A99F8CE080"), f"error report: total {e}, C0 {c0e.value}, C1 {c1e.value}")

print("\n== layout detection ==")


def gen_units(rng, lay, n, sil_frac=0.0):
    infos = []
    for _ in range(3 * n):
        infos.append(SIL49 if rng.random() < sil_frac else rnd_info(rng))
    return b"".join(unit_from_infos(infos[3 * i:3 * i + 3], lay) for i in range(n)), infos


def noise(rng, b, ber):
    b = bytearray(b)
    for i in range(len(b) * 8):
        if rng.random() < ber:
            b[i >> 3] ^= 0x80 >> (i & 7)
    return bytes(b)


rng = random.Random(3)
A.krk_ambe_detect_layout.restype = ctypes.c_int
for lay in (1, 2, 3, 4):
    res = {}
    for ber in (0.0, 0.01, 0.03):
        good = 0
        trials = 60
        for _ in range(trials):
            u, _ = gen_units(rng, lay, 10)
            u = noise(rng, u, ber)
            good += A.krk_ambe_detect_layout(cb(u), 10) == lay
        res[ber] = good
    sc = A.krk_ambe_detect_score()
    check(res[0.0] == 60 and res[0.01] == 60, f"{NAMES[lay]}: detected on 10 clean units 60/60 and with 1% bit errors {res[0.01]}/60",
          extra=f"3% BER {res[0.03]}/60, last score {sc} per mille")
u, _ = gen_units(rng, 2, 1)
check(A.krk_ambe_detect_layout(cb(u), 1) == 2, "a single clean unit (3 frames) is enough")
u, _ = gen_units(rng, 1, 20, sil_frac=0.9)
check(A.krk_ambe_detect_layout(cb(u), 20) == 1, "90 % silence frames still detected")
fp = 0
scs = []
for _ in range(2000):
    n = rng.choice((1, 2, 5, 20))
    u = bytes(rng.getrandbits(8) for _ in range(27 * n))
    fp += A.krk_ambe_detect_layout(cb(u), n) != L_RAW
    scs.append(max(A.krk_ambe_layout_score(cb(u), n, l) for l in (1, 2, 3, 4)))
check(fp == 0, f"random data (2000 buffers of 1-20 units) never accepted; highest wrong-layout score {max(scs)} per mille (needs >= 250 per mille, >= 2 frames, 2x the runner-up)")
u = bytes(27 * 10)
check(A.krk_ambe_detect_layout(cb(u), 10) == L_RAW, "all-zero data refused (equal scores on every layout)")
# wrong-layout score on real data
u, _ = gen_units(rng, 1, 40)
others = [A.krk_ambe_layout_score(cb(u), 40, l) for l in (2, 3, 4)]
print(f"      L1 data scored per mille: L1 {A.krk_ambe_layout_score(cb(u), 40, 1)}, L2 {others[0]}, L1lsb {others[1]}, L2lsb {others[2]}")
# parity convention
for mode in (0, 1, 2):
    A.krk_ambe_set_parity(mode)
    u, _ = gen_units(rng, 1, 10)
    got = A.krk_ambe_detect_parity(cb(u), 10, 1)
    check(got == mode, f"parity convention {mode} (even/odd/zero) autodetected -> {got}")
A.krk_ambe_set_parity(0)

print("\n== silence ==")
rng = random.Random(4)
ok = sum(A.krk_ambe_info_is_silence(cb(info_bytes(rnd_info(rng, silent=True)))) for _ in range(500))
check(ok == 500, "b0 = 124/125 frames (random other bits) are silence")
ok = sum(not A.krk_ambe_info_is_silence(cb(info_bytes(rnd_info(rng)))) for _ in range(2000))
check(ok == 2000, "b0 < 120 frames are never silence")
ok = sum(not A.krk_ambe_info_is_silence(cb(info_bytes(rng.getrandbits(49)))) for _ in range(4000))
print(f"      (random 49-bit vectors not silence: {ok}/4000, expected about 4000 * 126/128)")

print("\n== pack / unpack ==")
stt = (ctypes.c_uint * 5)()
rng = random.Random(5)
cap = 255 * 27 + 600
OUT = (UB * cap)()
BACK = (UB * (255 * 27))()
for lay in (1, 2, 3, 4):
    for trim in (0, 1):
        bad = 0
        for t in range(40):
            n = rng.choice((1, 2, 5, 17, 60, 200, 255))
            infos = []
            for _ in range(3 * n):
                mode = rng.random()
                infos.append(SIL49 if mode < 0.25 else rnd_info(rng, True) if mode < 0.35 else rnd_info(rng))
            u = b"".join(unit_from_infos(infos[3 * i:3 * i + 3], lay) for i in range(n))
            ln = A.krk_ambe_pack(cb(u), n, lay, trim, OUT, cap, stt)
            m = A.krk_ambe_unpack(OUT, ln, lay, trim, BACK, 255)
            exp = u
            if trim:     # non-canonical silence frames come back canonical
                exp = b"".join(unit_from_infos([SIL49 if (((x >> 43) << 1) | (x & 1)) in (124, 125) else x for x in infos[3 * i:3 * i + 3]], lay) for i in range(n))
            bad += not (m == n and bytes(BACK[:27 * n]) == exp)
            if not trim:
                assert ln == 1 + (3 * n * 49 + 7) // 8, (ln, n)
        check(bad == 0, f"{NAMES[lay]} trim={trim}: unpack(pack(u)) == u on 40 random messages (1..255 units, 35 % silence frames)" +
              ("" if not trim else " (non-canonical silence -> canonical)"))
# exactly-255 run, all silence, all speech, n=0
for label, fn in (("all silence", lambda i: SIL49), ("all speech", lambda i: rnd_info(rng))):
    for n in (1, 85, 86, 255):
        infos = [fn(i) for i in range(3 * n)]
        u = b"".join(unit_from_infos(infos[3 * i:3 * i + 3], 1) for i in range(n))
        ln = A.krk_ambe_pack(cb(u), n, 1, 1, OUT, cap, stt)
        m = A.krk_ambe_unpack(OUT, ln, 1, 1, BACK, 255)
        check(m == n and bytes(BACK[:27 * n]) == u, f"{label}, n={n}: round trip with run lengths ({ln} B)")
ln = A.krk_ambe_pack(cb(b""), 0, 1, 1, OUT, cap, stt)
check(ln == 1 and A.krk_ambe_unpack(OUT, ln, 1, 1, BACK, 255) == 0, "empty message")
u = bytes(range(27)) * 3
ln = A.krk_ambe_pack(cb(u), 3, L_RAW, 1, OUT, cap, stt)
check(ln == 81 and bytes(OUT[:81]) == u and A.krk_ambe_unpack(OUT, 81, L_RAW, 0, BACK, 255) == 3 and bytes(BACK[:81]) == u, "raw fallback: bytes unchanged")
check(A.krk_ambe_pack(cb(u), 3, 1, 0, OUT, 10, stt) == -1, "pack refuses a too small output buffer")
# truncated / corrupt payloads: must return -1 or a unit count, never crash or read past len
rng = random.Random(6)
u, _ = gen_units(rng, 1, 20, 0.3)
ln = A.krk_ambe_pack(cb(u), 20, 1, 1, OUT, cap, stt)
res = set()
for cut in range(0, ln):
    res.add(A.krk_ambe_unpack(OUT, cut, 1, 1, BACK, 255) >= -1)
check(res == {True} and A.krk_ambe_unpack(OUT, ln - 1, 1, 1, BACK, 255) == -1, "every truncation of a payload is rejected without crashing")
# stats / link quality
u, infos = gen_units(rng, 2, 30, 0.2)
u2 = bytearray(u)
nflip = 0
for k in range(0, 30 * 3, 4):                         # one bit error in the C0 of every 4th frame
    cwk = U4()
    unit = (UB * 27).from_buffer(u2, 27 * (k // 3))
    A.krk_ambe_unit_get(unit, k % 3, 2, cwk)
    cwk[0] ^= 1 << (k % 24)
    A.krk_ambe_unit_put(unit, k % 3, 2, cwk)
    nflip += 1
A.krk_ambe_pack(cb(bytes(u2)), 30, 2, 0, OUT, cap, stt)
check(stt[0] == 90 and stt[3] == nflip and stt[2] == 0, f"stats: {stt[0]} frames, {stt[3]} corrected C0 bits (injected {nflip}), {stt[2]} bad, {stt[1]} silent")

print("\n== gains (12 s of speech, 70 % activity) ==")
SPEECH_S = 12
NF = SPEECH_S * 50
rng = random.Random(7)


def activity_pattern(speech, pause):
    pat = []
    while len(pat) < NF:
        pat += [1] * speech + [0] * pause
    return pat[:NF]


def air_samples(mode, payload_len_bytes_units):
    return B.krk_bv_tx_samples(mode, payload_len_bytes_units)


def tx_count(mode, nunits, units):
    n = 0
    buf = (ctypes.c_short * 256)()
    g = (UB * 64)()
    B.krk_bv_tx_start(mode, 1, 0x123456, cb(units), nunits, None, 0)
    while True:
        more = B.krk_bv_tx_fill(buf)
        n += 256
        if not more:
            break
    return n


def raw_block_trim(pat):
    """The container's existing trim: silent 60 ms units dropped, a block = up to 6 time-consecutive units."""
    units = [any(pat[3 * i:3 * i + 3]) for i in range(NF // 3)]
    blocks, run = 0, 0
    for a in units:
        if a:
            run += 1
            if run == 1 or run > 6:
                blocks += 1
                run = 1 if run > 6 else run
        else:
            run = 0
    return blocks, sum(units)


rows = []
for name, sp, pa in (("1.4 s speech / 0.6 s pause", 70, 30), ("0.6 s speech / 0.26 s pause (choppy)", 30, 13), ("3.0 s / 1.3 s (long)", 150, 64)):
    pat = activity_pattern(sp, pa)
    act = sum(pat) / NF
    infos = [rnd_info(rng) if a else SIL49 for a in pat]
    u = b"".join(unit_from_infos(infos[3 * i:3 * i + 3], 1) for i in range(NF // 3))
    nu = NF // 3
    lnb = A.krk_ambe_pack(cb(u), nu, 1, 0, OUT, cap, stt)
    lnc = A.krk_ambe_pack(cb(u), nu, 1, 1, OUT, cap, stt)
    ch_b = -(-lnb // 27)
    ch_c = -(-lnc // 27)
    blocks, nact = raw_block_trim(pat)
    row = {"name": name, "act": act}
    for mode, mn in ((1, "fast"), (0, "robust")):
        a = air_samples(mode, nu) / 8000.0
        a2 = air_samples(mode, blocks * 6) / 8000.0
        b = air_samples(mode, ch_b) / 8000.0
        c = air_samples(mode, ch_c) / 8000.0
        row[mn] = (a, a2, b, c)
    row["bytes"] = (nu * 27, blocks * 6 * 27, lnb, lnc)
    rows.append(row)
    if sp == 70:
        for mode in (1, 0):
            exp = air_samples(mode, ch_c)
            check(tx_count(mode, ch_c, bytes(ch_c * 27)) == exp, f"krk_bv_tx_samples(mode {mode}, {ch_c} units) == samples produced by tx_fill ({exp})")
for r in rows:
    a, a2, b, c = r["bytes"]
    print(f"\n  {r['name']}: activity {100 * r['act']:.0f} %, payload bytes: raw {a}, raw+block trim {a2}, stripped {b}, stripped+trim {c}")
    for mn in ("fast", "robust"):
        t = r[mn]
        print(f"    {mn:6s} air time: raw {t[0]:5.2f} s | raw+block trim {t[1]:5.2f} s | stripped {t[2]:5.2f} s ({t[0] / t[2]:.2f}x) | stripped+trim {t[3]:5.2f} s ({t[0] / t[3]:.2f}x vs raw, {t[1] / t[3]:.2f}x vs raw+block trim)")
r0 = rows[0]
check(r0["fast"][2] < r0["fast"][0] * 0.70 and r0["fast"][3] < r0["fast"][2] * 0.85 and r0["fast"][3] < r0["fast"][1], "stripping alone saves > 30 %, trim adds > 15 %, and beats the container's block trim")
check(r0["robust"][3] < 12.0, f"robust mode with stripping + trim ({r0['robust'][3]:.2f} s) is faster than real time (12 s)")

print("\n== static properties ==")
src = re.sub(r"/\*.*?\*/", "", (UI / "krk_ambe.c").read_text(), flags=re.S)
src += re.sub(r"/\*.*?\*/", "", (UI / "krk_ambe.h").read_text(), flags=re.S)
check(not re.findall(r"\b(float|double)\b", src), "no float/double")
check(not re.search(r"\b(malloc|calloc|free|printf|memcpy|memset|strlen)\b", src), "no libc calls")
try:
    hostcc.build([UI / "krk_ambe.c"], flags=["-O2", "-mgeneral-regs-only", "-c"], out=WORK / "fp.o")
    err = ""
except RuntimeError as e:
    err = str(e)
if "unknown argument" in err or "unrecognized" in err:
    check(True, "compiler has no -mgeneral-regs-only; source grep stands in", hard=False)
else:
    check(not err, "compiles with -mgeneral-regs-only")
hostcc.build([UI / "krk_ambe.c"], flags=["-Os", "-fno-stack-protector", "-ffreestanding", "-fno-builtin", "-c"], out=WORK / "a.o")
nm = subprocess.run(["nm", "-u", str(WORK / "a.o")], capture_output=True, text=True).stdout.split()
check(not nm, f"no undefined symbols (freestanding) {nm}")
sz = subprocess.run(["size", "-m", str(WORK / "a.o")], capture_output=True, text=True)
if sz.returncode == 0 and "Section" in sz.stdout:
    sec = {m.group(1): int(m.group(2)) for m in re.finditer(r"Section \((\w+, \w+)\): (\d+)", sz.stdout)}
    check(sec.get("__DATA, __data", 0) == 0, f".data = {sec.get('__DATA, __data', 0)} B")
    print(f"      host -Os: text {sec.get('__TEXT, __text', 0)} B, const {sec.get('__TEXT, __const', 0)} B, bss {sec.get('__DATA, __bss', 0)} B")
else:
    sz = subprocess.run(["size", str(WORK / "a.o")], capture_output=True, text=True)
    v = sz.stdout.split("\n")[1].split()
    check(int(v[1]) == 0, f".data = {v[1]} B", extra=f"-Os host: text+rodata {v[0]} B, bss {v[2]} B")

print()
finish("ALL HARD GATES PASSED", "FAILED:\n  {names}", sep="\n  ")
