#!/usr/bin/env python3
"""Host test for F13 "BV1" compressed burst voice: patches/kraken_ui/krk_bv_modem.c, krk_bv_frame.c, krk_bv_sound.c
(D8PSK / DQPSK 2000 baud software modem + RS(182,166) burst container + sounding probe) and
tools/bv_sounding_analysis.py.

Everything is compiled with the host cc and driven through ctypes (tests/bv_glue.c). Pure python + C, numpy not needed.
Run: python3 tests/test_bv.py       (about 2-3 minutes; seeds are fixed, so every number is reproducible)

Hard gates (exit status 1 when one fails):
  - ideal channel and channel B (smooth band-pass 300-2900 Hz, -3 dB tilt 1200->2200 Hz, the radio model's all-pass dispersion)
    at 20 and 25 dB SNR, fast and robust mode: a 200-unit burst must come back byte exact
  - the same on channel B with +-0.5 % sample clock, amplitude 0.03x / 5x, DC offset, other carriers, back-to-back bursts
  - the container, RS codec, TX properties, no float/double, no initialised writable data, no false syncs on noise
  - the sounding probe: the analysis tool's measured response and recommendation on synthetic captures
Reported only (never fail the suite): 15 dB cases and tools/afsk_channel.py verbatim (channel A, which has a -26 dB notch at
1700 Hz that is probably a fit artifact; section 1a of docs/burst-voice.md).
"""
import array
import ctypes
import math
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import time

import hostcc
from hostcc import HERE, TOOLS, UI
from hostlib import check, finish
sys.path.insert(0, str(TOOLS))
import afsk_channel as ach                              # noqa: E402
import bv_sounding_analysis as snd                      # noqa: E402

T0 = time.time()


# ---------------------------------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------------------------------
SRC = [UI / "krk_bv_modem.c", UI / "krk_bv_frame.c", UI / "krk_bv_sound.c"]
WORK = pathlib.Path(tempfile.mkdtemp(prefix="bvtest"))
hostcc.build(SRC + [HERE / "bv_glue.c"], flags=["-O2", "-Wall", "-Wextra", "-Werror"], shared=True, out=WORK / "libbv.so", libs=["-lm"])
L = ctypes.CDLL(str(WORK / "libbv.so"))
L.krk_bv_crc16.restype = ctypes.c_uint
L.krk_bv_tx_samples.restype = ctypes.c_uint
L.krk_bv_msg_next.restype = ctypes.POINTER(ctypes.c_ubyte)
UB = ctypes.POINTER(ctypes.c_ubyte)


def arr_u8(b):
    return (ctypes.c_ubyte * len(b)).from_buffer_copy(bytes(b))


def arr_i16(v):
    return (ctypes.c_short * len(v))(*v)


# ---------------------------------------------------------------------------------------------------------------------
# 0. static properties of the C sources
# ---------------------------------------------------------------------------------------------------------------------
def strip_comments(t):
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"//[^\n]*", "", t)


def static_checks():
    print("\n== static properties ==")
    for f in SRC + [UI / "krk_bv.h"]:
        hits = re.findall(r"\b(float|double|long\s+double)\b", strip_comments(f.read_text()))
        check(not hits, f"no 'float'/'double' in {f.name}")
        check(not re.search(r"\b(malloc|calloc|free|printf|memcpy|memset|strlen)\b", strip_comments(f.read_text())), f"no libc calls in {f.name}")
    for f in SRC:
        for flag in ("-mgeneral-regs-only",):
            try:
                hostcc.build([f], flags=["-O2", flag, "-c"], out=WORK / "fp.o")
                err = ""
            except RuntimeError as e:
                err = str(e)
            if "unknown argument" in err or "unrecognized" in err:
                check(True, f"{f.name}: compiler has no {flag}; source grep above stands in", hard=False)
            else:
                check(not err, f"{f.name} compiles with {flag} (no floating point instruction can be emitted)", extra=err[-200:])
    # code size / data sections with -Os (proxy for csky -O2)
    tot_text = tot_const = tot_bss = 0
    for f in SRC:
        o = WORK / (f.stem + ".o")
        hostcc.build([f], flags=["-w", "-Os", "-c"], out=o)
        sz = subprocess.run(["size", "-m", str(o)], capture_output=True, text=True)
        if sz.returncode == 0 and "Section" in sz.stdout:          # mach-o
            sec = {m.group(1): int(m.group(2)) for m in re.finditer(r"Section \((\w+, \w+)\): (\d+)", sz.stdout)}
            text, const, bss, data = sec.get("__TEXT, __text", 0), sec.get("__TEXT, __const", 0) + sec.get("__TEXT, __literal8", 0), sec.get("__DATA, __bss", 0), sec.get("__DATA, __data", 0)
        else:
            sz = subprocess.run(["size", str(o)], capture_output=True, text=True)
            v = sz.stdout.split("\n")[1].split()
            text, const, data, bss = int(v[0]), 0, int(v[1]), int(v[2])
        check(data == 0, f"{f.name}: no initialised writable data (.data = {data} B)")
        tot_text += text
        tot_const += const
        tot_bss += bss
        print(f"      {f.name}: text+rodata {text + const} B, bss {bss} B (host arm64/x86 -Os)")
    print(f"      total host -Os: {tot_text + tot_const} B of code and constant tables (about 2 kB of it tables), state {tot_bss} B"
          "  (the 16/32-bit C-SKY encoding is typically 25-35 % denser than arm64)")
    check(tot_bss <= 1536, f"modem + framing + sounding state {tot_bss} B <= 1536 B (the 5632 B payload buffer is external)")
    check(tot_text + tot_const <= 24000, "host -Os code + tables stays in the budget class (hard cap 24 kB arm64, target <= 12 kB csky)")


# ---------------------------------------------------------------------------------------------------------------------
# 1. Reed-Solomon, CRC, container
# ---------------------------------------------------------------------------------------------------------------------
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 256:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def gmul(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def ref_parity(data, nr):
    """Independent python reference: polynomial remainder of data * x^nr by prod (x - a^i), i = 0..nr-1."""
    g = [1]
    for i in range(nr):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= gmul(c, EXP[i])
        g = ng
    msg = list(data) + [0] * nr
    for i in range(len(data)):
        c = msg[i]
        if c:
            for j in range(1, len(g)):
                msg[i + j] ^= gmul(g[j], c)
    return msg[len(data):]


def ref_crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def rs_tests():
    print("\n== Reed-Solomon / CRC ==")
    rnd = random.Random(1)
    check(L.krk_bv_crc16(arr_u8(b"123456789"), 9) == 0x29B1 == ref_crc16(b"123456789"), "CRC-16/CCITT-FALSE check value 0x29B1")
    for k, nr in ((166, 16), (16, 8)):
        n = k + nr
        bad = fixed = 0
        for t in range(300):
            d = bytes(rnd.randrange(256) for _ in range(k))
            buf = arr_u8(d + bytes(nr))
            L.krk_bv_rs_encode(buf, k, nr)
            if list(buf[k:]) != ref_parity(d, nr):
                bad += 1
            orig = bytes(buf)
            ne = rnd.randint(0, nr // 2)
            for p in rnd.sample(range(n), ne):
                buf[p] ^= rnd.randint(1, 255)
            r = L.krk_bv_rs_decode(buf, n, nr)
            fixed += (r == ne and bytes(buf) == orig)
        check(bad == 0, f"RS({n},{k}) parity equals the python reference (300 random codewords)")
        check(fixed == 300, f"RS({n},{k}) corrects 0..{nr // 2} random byte errors (300/300)", extra=f"{fixed}/300")
        det = wrong = 0
        for t in range(400):
            d = bytes(rnd.randrange(256) for _ in range(k))
            buf = arr_u8(d + bytes(nr))
            L.krk_bv_rs_encode(buf, k, nr)
            orig = bytes(buf)
            for p in rnd.sample(range(n), nr // 2 + 1):
                buf[p] ^= rnd.randint(1, 255)
            r = L.krk_bv_rs_decode(buf, n, nr)
            det += r < 0
            wrong += (r >= 0 and bytes(buf) != orig)
        check(det >= 392, f"RS({n},{k}): t+1 = {nr // 2 + 1} errors detected (not 'corrected') in >= 98 %", extra=f"{det}/400, miscorrected {wrong}")
    d = bytes(rnd.randrange(256) for _ in range(166))
    buf = arr_u8(d + bytes(16))
    L.krk_bv_rs_encode(buf, 166, 16)
    orig = bytes(buf)
    for p in range(100, 108):
        buf[p] ^= 0xA5
    check(L.krk_bv_rs_decode(buf, 182, 16) == 8 and bytes(buf) == orig, "RS(182,166): burst of 8 consecutive bytes corrected")


def container_tests():
    print("\n== burst container / message assembler / disassembler ==")
    rnd = random.Random(2)
    for nu in (200, 203, 1, 6, 7):
        units = bytes(rnd.randrange(256) for _ in range(27 * nu))
        nb = (nu + 5) // 6
        gaps = bytes((i * 37 + 3) & 255 for i in range(nb))
        keep_u, keep_g = arr_u8(units), arr_u8(gaps)           # the assembler keeps the pointers: the arrays must stay alive
        L.krk_bv_msg_begin(1, 0xBEEF, 0x123456, keep_u, nu, keep_g, 5, 15)
        segs = []
        ln = ctypes.c_int()
        while True:
            p = L.krk_bv_msg_next(ctypes.byref(ln))
            if not p:
                break
            segs.append(bytes(p[:ln.value]))
        check([len(s) for s in segs] == [24] + [182] * nb + [24], f"nu={nu}: segments = header, {nb} blocks, header copy")
        buf = (ctypes.c_ubyte * 5632)()
        L.krk_bv_rxmsg_begin(1, buf, 5632)
        st = (ctypes.c_int * 128)()
        ev = 0
        for s in segs:
            if L.krk_bv_rxmsg_expect() != len(s):
                check(False, f"nu={nu}: rxmsg_expect() announces the next segment length")
            ev |= L.krk_bv_rxmsg_segment(arr_u8(s))
        L.bvt_info(st)
        good = (st[1] == nb and st[2] == 0 and st[4] == 1 and st[5] == nu and st[6] == nb and st[7] == 0xBEEF and st[8] == 0x123456
                and st[9] == 5 and bytes(buf[:27 * nu]) == units and [st[72 + i] for i in range(nb)] == list(gaps)
                and ev & 8 and L.krk_bv_rxmsg_expect() == 0)
        check(good, f"nu={nu}: header fields, {nb} blocks, units, per-block silence gaps and DONE event round-trip")
    # damaged material
    nu = 200
    units = bytes(rnd.randrange(256) for _ in range(27 * nu))
    keep_u = arr_u8(units)
    L.krk_bv_msg_begin(0, 7, 99, keep_u, nu, None, 0, 15)
    segs = []
    ln = ctypes.c_int()
    while True:
        p = L.krk_bv_msg_next(ctypes.byref(ln))
        if not p:
            break
        segs.append(bytearray(p[:ln.value]))

    def feed(segments):
        buf = (ctypes.c_ubyte * 5632)()
        L.krk_bv_rxmsg_begin(0, buf, 5632)
        for s in segments:
            L.krk_bv_rxmsg_segment(arr_u8(bytes(s)))
        st = (ctypes.c_int * 128)()
        L.bvt_info(st)
        return st, bytes(buf)

    s2 = [bytearray(s) for s in segs]
    for p in range(10, 19):
        s2[6][p] ^= 0x5A                               # 9 errors in block 5 (> t = 8): lost
    for p in range(0, 8):
        s2[7][p] ^= 0xFF                               # 8 errors in block 6: corrected
    st, buf = feed(s2)
    ok = [st[32 + i] for i in range(34)]
    check(st[2] == 1 and ok[5] == 0 and sum(ok) == 33 and st[12] >= 8, "block with 9 byte errors is rejected, 8 errors are corrected, the rest unaffected",
          extra=f"bad={st[2]} corrected_bytes={st[12]}")
    check(buf[:27 * 30] == units[:27 * 30] and buf[27 * 36:27 * 200] == units[27 * 36:], "lost block leaves its neighbours' units intact")
    s3 = [bytearray(s) for s in segs]
    for p in range(2, 12):
        s3[0][p] ^= 0xFF                               # first header destroyed (10 errors > 4)
    st, buf = feed(s3)
    check(st[4] == 1 and st[5] == 200 and st[1] == 34 and buf[:27 * 200] == units, "destroyed first header: the header copy at the end restores the message info")
    s4 = [bytearray(s) for s in segs]
    s4[0][3] ^= 0x11
    s4[0][20] ^= 0x22
    st, buf = feed(s4)
    check(st[4] == 1 and st[5] == 200, "first header with 2 byte errors is corrected by RS(24,16)")
    # blind mode: header gone, the 'last block' flag ends the blocks and the tail header is still parsed
    s5 = [bytearray(s) for s in segs]
    s5[0] = bytearray(24)
    st, buf = feed(s5)
    check(st[1] == 34 and st[4] == 1 and buf[:27 * 200] == units, "blind decoding: end marker of the last block + tail header")
    # frames in a different order than expected are not misplaced
    s6 = [bytearray(s) for s in segs]
    s6[3], s6[4] = s6[4], s6[3]
    st, buf = feed(s6)
    check(st[1] == 34 and buf[:27 * 200] == units, "blocks are placed by their index byte, not by arrival order")


# ---------------------------------------------------------------------------------------------------------------------
# 2. modem loop-back
# ---------------------------------------------------------------------------------------------------------------------
NU = 200


def payload(seed, nu=NU):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(27 * nu))


_tx_cache = {}


def tx(mode, seed, nu=NU, rms=4000):
    key = (mode, seed, nu, rms)
    if key not in _tx_cache:
        units = payload(seed, nu)
        out = (ctypes.c_short * 130000)()
        n = L.bvt_tx_all(mode, 0x1234, 0xABCDEF, arr_u8(units), nu, None, out, len(out), rms)
        _tx_cache[key] = (units, list(out[:n]))
    return _tx_cache[key]


# channels ---------------------------------------------------------------------------------------------------------
def _rbj(kind, f0, q=1 / math.sqrt(2)):
    w = 2 * math.pi * f0 / 8000.0
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    b = [(1 - c) / 2, 1 - c, (1 - c) / 2] if kind == "lp" else [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a0 = 1 + al
    return (b[0] / a0, b[1] / a0, b[2] / a0, -2 * c / a0, (1 - al) / a0)


def _tilt_coeff():
    """One-pole low-pass pole giving -3 dB at 2200 vs 1200 Hz for the whole chain B."""
    lo, hi = 0.0, 0.95
    for _ in range(40):
        a = (lo + hi) / 2
        s = sos_b(a)
        d = 20 * math.log10(ach._mag(s, 2200.0) / ach._mag(s, 1200.0))
        lo, hi = (a, hi) if d > -3.0 else (lo, a)
    return (lo + hi) / 2


def sos_b(tilt):
    f0, r = ach._AP
    th = 2 * math.pi * f0 / 8000.0
    a1, a2 = -2 * r * math.cos(th), r * r
    return [_rbj("hp", 300.0), _rbj("lp", 2900.0), (a2, a1, 1.0, a1, a2), (1 - tilt, 0.0, 0.0, -tilt, 0.0)]


TILT = _tilt_coeff()


def chan_b(x):
    """Doc model B: 2nd-order high-pass 300 Hz + 2nd-order low-pass 2900 Hz, the afsk_channel all-pass (dispersion, group delay
    bump near 900 Hz), and a one-pole tilt that gives -3 dB at 2200 Hz relative to 1200 Hz."""
    return ach._biquad(sos_b(TILT), [float(v) for v in x])


def chan_a(x):
    """tools/afsk_channel.py verbatim (cascade of its biquads + all-pass + gate + noise floor); rms 300, DC -10.9, SNR set later."""
    return [float(v) for v in ach.channel(x, rms=300.0, snr_db=None, noise_rms=4.0, seed=1)]


def chan_ideal(x):
    return [float(v) for v in x]


def awgn(x, snr_db, seed):
    nz = [i for i, v in enumerate(x) if abs(v) > 1e-9]
    seg = x[nz[0]:nz[-1] + 1]
    p = sum(v * v for v in seg) / len(seg)
    sigma = math.sqrt(p / 10 ** (snr_db / 10.0))
    rnd = random.Random(seed)
    return [v + rnd.gauss(0, sigma) for v in x]


def resample(x, ratio):
    n = len(x)
    nout = int((n - 20) / ratio)
    a = array.array("d", x)
    o = array.array("d", [0.0] * nout)
    L.bvt_resample(ctypes.c_void_p(a.buffer_info()[0]), n, ctypes.c_void_p(o.buffer_info()[0]), nout, ctypes.c_double(ratio))
    return list(o)


def rx(x, nu=NU, units=None):
    pcm = [max(-32768, min(32767, int(round(v)))) for v in x]
    buf = (ctypes.c_ubyte * 5632)()
    st = (ctypes.c_int * 128)()
    L.bvt_rx_all(arr_i16(pcm), len(pcm), buf, 5632, st, 256)
    got = bytes(buf[:27 * nu])
    nb = (nu + 5) // 6
    res = dict(sync=bool(st[0] & 1), hdr=st[4], ok=st[1], nb=nb, bad=st[2], corrected=st[12], evm=st[13] / 65536.0,
               done=bool(st[0] & 8), abort=bool(st[0] & 16), syncs=st[0] & 1)
    if units is not None:
        res["byte_err"] = sum(1 for a, b in zip(got, units) if a != b)
        res["match"] = got == units
    res["snr_evm"] = -10 * math.log10(res["evm"]) if res["evm"] > 0 else float("nan")
    lost = nb - res["ok"]
    res["coded_err"] = (res["corrected"] + 182 * lost) / (182.0 * nb)
    return res


CHANNELS = {"ideal": chan_ideal, "B": chan_b, "A": chan_a}


def run_case(mode, ch, snr, seed=3, ratio=1.0, scale=1.0, dc=0.0, pre=500, attack=0.0, units=None):
    units, pcm = tx(mode, seed)
    x = [0.0] * pre + [float(v) for v in pcm] + [0.0] * 3000
    if attack:                                   # key-up: one-pole attack of the burst envelope (afsk_channel: tau 34.6 samples = 4.3 ms)
        a = math.exp(-1.0 / attack)
        g = 0.0
        for i in range(pre, len(x)):
            g = a * g + (1 - a)
            x[i] *= g
    if ratio != 1.0:
        x = resample(x, ratio)
    y = CHANNELS[ch](x)
    if snr is not None and ch != "A":
        y = awgn(y, snr, seed + 100)
    elif snr is not None and ch == "A":
        # afsk_channel scales to its own burst RMS; add the white noise at the requested SNR on top of its result
        y = awgn(y, snr, seed + 100)
    y = [v * scale + dc for v in y]
    return rx(y, units=units)


def fmt(r):
    return (f"sync {'y' if r['sync'] else 'n'}  blocks {r['ok']:2d}/{r['nb']}  payload byte err {r.get('byte_err', -1):4d}/{27 * NU}  "
            f"coded-byte err {100 * r['coded_err']:5.2f}%  eq SNR {r['snr_evm']:5.1f} dB  RS-corrected {r['corrected']:3d}  "
            f"{'FRAME OK' if r.get('match') else 'frame lost'}")


def modem_tests():
    print("\n== modem loop-back: 200 units (5400 B), random payload ==")
    for mode, name in ((1, "fast  D8PSK 6.0 kbps"), (0, "robust DQPSK 4.0 kbps")):
        units, pcm = tx(mode, 3)
        rms = math.sqrt(sum(v * v for v in pcm) / len(pcm))
        print(f"-- {name}: {len(pcm)} samples = {len(pcm) / 8000:.2f} s on air for {NU * 0.06:.1f} s of voice, "
              f"air-time ratio {(len(pcm) / 8000) / (NU * 0.06):.2f}; tx rms {rms:.0f}, peak {max(abs(v) for v in pcm)}")
        for ch, hard_snr in (("ideal", (25, 20)), ("B", (25, 20)), ("A", ())):
            for snr in (25, 20, 15):
                r = run_case(mode, ch, snr)
                hard = snr in hard_snr
                label = f"{name.split()[0]:6s} channel {ch:5s} SNR {snr} dB"
                check(bool(r.get("match")), label, hard=hard, extra=fmt(r))
    # noiseless channels too
    for mode in (1, 0):
        for ch in ("ideal", "B"):
            r = run_case(mode, ch, None)
            check(bool(r.get("match")), f"{'fast' if mode else 'robust'} channel {ch} no noise", extra=fmt(r))


def impairment_tests():
    print("\n== impairments on channel B at 22 dB SNR (both modes) ==")
    cases = [("clock +0.5 %", dict(ratio=1.005)), ("clock -0.5 %", dict(ratio=0.995)), ("amplitude x0.03 (-30 dB)", dict(scale=0.03)),
             ("amplitude x5", dict(scale=5.0)), ("DC offset +3000", dict(dc=3000.0)), ("DC offset -1500", dict(dc=-1500.0)),
             ("key-up ramp, tau 4.3 ms (afsk_channel's attack)", dict(attack=34.56)), ("key-up ramp, tau 40 ms", dict(attack=320.0)),
             ("other payload (seed 11)", dict(seed=11)), ("other payload (seed 12), clock +0.3 %", dict(seed=12, ratio=1.003))]
    for mode in (1, 0):
        for name, kw in cases:
            r = run_case(mode, "B", 22, **kw)
            check(bool(r.get("match")), f"{'fast  ' if mode else 'robust'} B 22 dB, {name}", extra=fmt(r))
    # amplitude x0.003 is below the useful dynamic range: report only
    r = run_case(1, "B", None, scale=0.002)
    check(bool(r.get("match")), "fast B no noise, amplitude x0.002 (rms ~5 LSB; quantization limited)", hard=False, extra=fmt(r))


def cliff_report():
    print("\n== SNR cliff on channel B (report only) ==")
    for mode, nm in ((1, "fast"), (0, "robust")):
        row = []
        for snr in (14, 12, 11, 10, 9, 8, 6):
            r = run_case(mode, "B", snr)
            row.append(f"{snr}dB:{r['ok']}/{r['nb']}")
        print(f"      {nm:6s} blocks ok  " + "  ".join(row))


def carrier_tests():
    print("\n== carrier frequency (krk_bv_set_carrier) ==")
    for fc in (1500, 1900, 2100):
        L.bvt_carrier(fc)
        _tx_cache.clear()
        for mode in (1, 0):
            r = run_case(mode, "B", 22)
            check(bool(r.get("match")), f"carrier {fc} Hz, {'fast' if mode else 'robust'}, channel B 22 dB", extra=fmt(r))
    L.bvt_carrier(1700)
    _tx_cache.clear()


def stream_tests():
    print("\n== continuous operation ==")
    # two bursts back to back with noise before, between and after; both must complete (state machine re-arms)
    u1, p1 = tx(1, 21)
    u2, p2 = tx(0, 22, nu=60)
    x = [0.0] * 2000 + [float(v) for v in p1] + [0.0] * 2400 + [float(v) for v in p2] + [0.0] * 4000
    y = awgn(chan_b(x), 22, 5)
    pcm = arr_i16([int(round(v)) for v in y])
    buf = (ctypes.c_ubyte * 5632)()
    st = (ctypes.c_int * 128)()
    L.krk_bv_rx_begin(buf, 5632)
    done = []
    for i in range(0, len(y) - 255, 256):
        ev = L.krk_bv_rx_feed(ctypes.byref(pcm, 2 * i), 256)
        if ev & 8:
            L.bvt_info(st)
            done.append((st[5], bytes(buf[:27 * st[5]]), st[1], st[6]))
    check(len(done) == 2 and done[0][1] == u1 and done[1][1] == u2[:27 * 60],
          "two bursts (fast 200 units, then robust 60 units, 0.3 s apart) both decoded in one stream", extra=f"{[(d[0], d[2], d[3]) for d in done]}")
    # noise only: no false sync (the 64-symbol soft correlator)
    rnd = random.Random(9)
    for rms, name in ((3.0, "quiet noise (rms 3)"), (300.0, "loud noise (rms 300)")):
        x = [rnd.gauss(0, rms) for _ in range(8000 * 30)]
        pcmn = arr_i16([int(round(v)) for v in x])
        st = (ctypes.c_int * 128)()
        L.bvt_rx_all(pcmn, len(x), (ctypes.c_ubyte * 5632)(), 5632, st, 256)
        check((st[0] & 1) == 0, f"30 s of {name}: no false sync", extra=f"events {st[0]}")
    # tones / chirps / speech-like bursts: energy trigger fires, sync search must stay quiet
    ref = snd.reference()
    st = (ctypes.c_int * 128)()
    buf = (ctypes.c_ubyte * 5632)()
    first_frame = 33280
    L.bvt_rx_all(arr_i16(ref[:first_frame - 200]), first_frame - 200, buf, 5632, st, 256)
    check((st[0] & 1) == 0, "the sounding probe's tone and chirps (before the burst) cause no false sync", extra=f"events {st[0]}")
    L.bvt_rx_all(arr_i16(ref), len(ref), buf, 5632, st, 256)
    pay = (ctypes.c_ubyte * 324)()
    L.krk_bv_sound_payload(pay)
    check(st[1] == 2 and bytes(buf[:324]) == bytes(pay), "the whole sounding probe: chirps, then its BV burst, decodes 2/2 blocks", extra=f"ok={st[1]}")
    # receiver is deaf while transmitting, tx_start re-arms
    keep_p = arr_u8(payload(1, 6))
    L.krk_bv_tx_start(1, 1, 1, keep_p, 6, None, 0)
    ev = L.krk_bv_rx_feed(arr_i16(ref[33280:33280 + 256]), 256)
    check(ev == 0 and L.krk_bv_tx_active() == 1, "receiver ignores input while a transmission is active")
    out = (ctypes.c_short * 256)()
    while L.krk_bv_tx_fill(out):
        pass


def tx_tests():
    print("\n== transmitter properties ==")
    for mode in (1, 0):
        units, pcm = tx(mode, 3)
        n = len(pcm)
        calc = L.krk_bv_tx_samples(mode, NU)
        check(n % 256 == 0 and n == calc, f"mode {mode}: samples {n} = krk_bv_tx_samples {calc}")
        body = pcm[2048:n - 2048]
        rms = math.sqrt(sum(v * v for v in body) / len(body))
        check(3200 < rms < 4800 and max(abs(v) for v in pcm) < 12000, f"mode {mode}: rms {rms:.0f} (target 4000), peak {max(abs(v) for v in pcm)} (< 12000, no clipping)")
        check(all(v == 0 for v in pcm[-64:]), f"mode {mode}: burst ends in silence (filter flushed)")
    a = tx(1, 3)[1]
    _tx_cache.clear()
    b = tx(1, 3)[1]
    check(a == b, "transmitter is deterministic / restartable")
    # spectrum: energy only inside 300..3100 Hz
    pcm = tx(1, 3)[1][4096:4096 + 8192]
    def goertzel(f):
        k = 2 * math.cos(2 * math.pi * f / 8000)
        s1 = s2 = 0.0
        for v in pcm:
            s1, s2 = v + k * s1 - s2, s1
        return s1 * s1 + s2 * s2 - k * s1 * s2
    inband = sum(goertzel(f) for f in range(500, 3000, 100))
    oob = sum(goertzel(f) for f in (100, 200, 3500, 3700, 3900))
    check(10 * math.log10(inband / oob) > 25, f"out-of-band (<=200 Hz, >=3500 Hz) energy is {10 * math.log10(inband / oob):.0f} dB below the band")


# ---------------------------------------------------------------------------------------------------------------------
# 3. sounding probe and its analysis tool
# ---------------------------------------------------------------------------------------------------------------------
def sounding_tests():
    print("\n== sounding probe + tools/bv_sounding_analysis.py (synthetic captures) ==")
    ref = snd.reference()
    check(len(ref) % 256 == 0 and 38000 <= len(ref) <= 40000, f"probe is {len(ref)} samples = {len(ref) / 8000:.2f} s (whole 256-sample blocks, a few seconds)")
    check(max(abs(v) for v in ref) <= 12000, "probe peak <= 12000")
    pad = [0.0] * 4000
    x = pad + [float(v) for v in ref] + [0.0] * 3000

    def truth(sos, f):
        band = sorted(ach._mag(sos, q) for q in range(1000, 2400, 50))
        return 20 * math.log10(ach._mag(sos, f) / band[len(band) // 2])

    # a) smooth channel B, 27 dB: measured response equals the model, FAST
    y = [v * 0.3 - 11.0 for v in awgn(chan_b(x), 27, 31)]
    r = snd.analyse(y)
    sb = sos_b(TILT)
    errs = [abs(m - truth(sb, f)) for f, m, g in r["freq"] if 400 <= f <= 3000]
    check(max(errs) < 1.0, f"B: measured magnitude within {max(errs):.2f} dB of the model over 400-3000 Hz")
    gdtruth = None
    check(r["gd_spread_ms"] < 2.5 and r["gd_spread_ms"] > 0.2, f"B: group-delay spread {r['gd_spread_ms']:.2f} ms measured (all-pass dispersion)")
    check(r["notch"][1] > -6.0, f"B: no notch (deepest {r['notch'][1]:+.1f} dB)")
    check(r["recommendation"]["mode"].startswith("FAST"), "B 27 dB: recommendation FAST", extra=r["recommendation"]["mode"])
    check(abs(r["clock_ppm"]) < 200 and abs(r["level_gain_db"]["chirpB"]) < 0.5, "B: linear (no level dependence), clock offset ~0",
          extra=f"clock {r['clock_ppm']:+.0f} ppm, level {r['level_gain_db']}")
    check(25 < r["snr_db"] < 29, f"B: measured SNR {r['snr_db']:.1f} dB (27 dB injected)")
    print("\n".join("      " + ln for ln in snd.report(r).split("\n")[:5]) + "\n      ...")
    # b) 11 dB: the fast burst is lost, the tool must not claim FAST
    y = [v * 0.3 for v in awgn(chan_b(x), 9, 32)]
    r = snd.analyse(y)
    check(not r["recommendation"]["mode"].startswith("FAST"), "B 9 dB: not FAST", extra=f"{r['recommendation']['mode']} (burst blocks {r['bv']['blocks_ok']}/2, eq SNR {r['bv']['snr_evm_db']})")
    # c) channel A (afsk_channel verbatim with its 1700 Hz notch): the tool sees the hole
    y = awgn(chan_a(x), 27, 33)
    r = snd.analyse(y)
    check(r["notch"][1] < -10 and abs(r["notch"][0] - 1700) < 60, f"A: notch found at {r['notch'][0]:.0f} Hz, {r['notch'][1]:+.1f} dB (smoothed)")
    check(r["recommendation"]["mode"].startswith("NEITHER"), "A: recommendation NEITHER single carrier", extra=r["recommendation"]["mode"])
    # d) a non-linear (limiting) transmitter: -6 dB chirp is boosted relative to the 0 dB chirp
    def limiter(v):
        return max(-1200.0, min(1200.0, v * 0.4))
    y = [limiter(v) for v in awgn(chan_b(x), 40, 34)]
    r = snd.analyse(y)
    check(abs(r["level_gain_db"]["chirpB"]) > 1.0, f"hard limiter: level dependence of the gain is reported ({r['level_gain_db']['chirpB']:+.1f} dB at -6 dB)")
    # e) a carrier that avoids a notch: a 1700 Hz notch at 1000-2400 Hz is everywhere; a notch at 1400 shifts the best carrier up
    def notch(y0, f0=1400.0, depth=0.05):
        w0 = 2 * math.pi * f0 / 8000.0
        rr = 0.985
        sos = [(1.0, -2 * math.cos(w0), 1.0, -2 * rr * math.cos(w0), rr * rr)]
        g = ach._mag(sos, 100.0)
        return [v / g for v in ach._biquad(sos, y0)]
    y = awgn(notch(chan_b(x)), 30, 35)
    r = snd.analyse(y)
    print(f"      notch at 1400 Hz: best carrier {r['best_fc']} Hz, deepest dip {r['notch'][1]:+.1f} dB at {r['notch'][0]:.0f} Hz; {r['recommendation']['mode']}; {r['recommendation']['carrier']}")
    check(r["best_fc"] >= 1900 or r["notch"][1] > -10, "narrow notch below the carrier: the tool prefers a higher carrier or sees a mild notch")


def poll_tests():
    """v53: the screen polls the FM Receive RAM halves (256 samples = 32 ms; a half is stable for 32 ms after it finishes). Model of
    the radio-side polling with the REAL receiver: v52 left its loop after 6 polls (10 ms each), the runner's keypad scan + delay
    made a 20-30 ms hole and halves were lost (hardware 2026-11-01: 'burst aborted'); v53 stays in the loop while the receiver is
    searching/receiving and re-arms on a detected gap."""
    print("\n== half polling (krk_bvscr.c rx_poll model) ==")
    u1, p1 = tx(0, 31, nu=40)
    x = [0.0] * 4000 + [float(v) for v in p1] + [0.0] * 6000
    y = awgn(chan_b(x), 14, 31)
    pcm = arr_i16([int(round(v)) for v in y])
    nh = len(y) // 256

    def feed(k):
        ev = L.krk_bv_rx_feed(ctypes.byref(pcm, 2 * 256 * k), 256)
        return ev

    def run(policy, gap, phase, drop=None, use_gap=True):
        """policy 'v52': 6 polls then a runner gap; 'v53': stay while the receiver is not idle. Returns (decoded, missed halves)."""
        buf = (ctypes.c_ubyte * 5632)()
        L.krk_bv_rx_begin(buf, 5632)
        t, last, prev_t, missed, got = phase, -1, 0, 0, None
        end = 32 * (nh + 2)
        while t < end:
            idle_exit = False
            i = 0
            halves = 0
            while True:
                j = t // 32 - 1
                if j > last and j < nh:
                    missed += j - last - 1 if last >= 0 else 0
                    if use_gap and L.krk_bv_rx_state() != 0 and L.krk_bv_rx_gap(t // 10, prev_t // 10) and last >= 0:
                        L.krk_bv_rx_begin(buf, 5632)
                    prev_t = t
                    last = j
                    halves += 1
                    if drop is None or j != drop:
                        ev = feed(j)
                        if ev & 8:
                            got = bytes(buf[:27 * 40])
                            L.krk_bv_rx_begin(buf, 5632)
                        elif ev & 16:
                            L.krk_bv_rx_begin(buf, 5632)
                else:
                    t += 10
                if t >= end:
                    break
                if policy == "v52":
                    i += 1
                    if i >= 6:
                        break
                else:
                    st = L.krk_bv_rx_state()
                    if st == 0 and (halves or i >= 5):
                        break
                    i += 1
            t += gap
        return got == u1, missed

    ok_all, bad = [], []
    for gap in (20, 25, 30):
        for phase in range(0, 32, 4):
            ok52, m52 = run("v52", gap, phase, use_gap=False)
            ok53, m53 = run("v53", gap, phase)
            ok_all.append(ok53)
            bad.append(ok52)
    check(sum(bad) < len(bad), "v52 polling (6 polls, then a 20-30 ms runner gap) loses halves and fails to decode in some phases",
          extra=f"{sum(bad)}/{len(bad)} decoded")
    check(all(ok_all), "v53 polling decodes the same burst in every phase and runner gap", extra=f"{sum(ok_all)}/{len(ok_all)}")
    # one half really lost mid-burst: the receiver cannot decode across the discontinuity, the gap rule flags it
    ok_drop, _ = run("v53", 25, 0, drop=nh // 2 - 2, use_gap=False)
    check(not ok_drop, "a half dropped mid-burst: the burst is not decoded (the discontinuity is real)")
    check(L.krk_bv_rx_gap(110, 100) == 1 and L.krk_bv_rx_gap(104, 100) == 0 and L.krk_bv_rx_gap(107, 100) == 1 and L.krk_bv_rx_gap(106, 100) == 0,
          "gap rule: 6 ticks (60 ms) between halves is normal jitter, 7 or more is a lost half")


# ---------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    static_checks()
    rs_tests()
    container_tests()
    tx_tests()
    modem_tests()
    impairment_tests()
    carrier_tests()
    stream_tests()
    poll_tests()
    cliff_report()
    sounding_tests()
    t = f"   ({time.time() - T0:.0f} s)"
    finish("\nALL PASS" + t, "\nFAILURES: {names}" + t)
