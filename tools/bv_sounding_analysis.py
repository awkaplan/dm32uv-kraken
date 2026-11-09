#!/usr/bin/env python3
"""Analyse a capture of the BV1 sounding probe (hardware gate test H1, docs/burst-voice.md 6.2 and 9).

The transmitting radio sends the probe from patches/kraken_ui/krk_bv_sound.c with one key press (about 4.9 s:
1 kHz tone, a 200-3800 Hz chirp at 0 dB, two 300-3300 Hz chirps at -6 / -12 dB, then a 12-unit fast-mode BV burst with a
known payload). Record the receiving radio's audio as an 8 kHz mono WAV (raw s16le also works).
This tool regenerates the exact reference with the host cc, aligns the capture to it and prints

  * magnitude and group delay of the whole radio-to-radio audio channel, 300-3800 Hz, in 100 Hz steps (from the 0 dB chirp,
    deconvolved on the full 0.24 Hz grid, smoothed over +-25 Hz)
  * a notch / ripple / roll-off check around the 1700 Hz carrier (the suspected -26 dB artifact of tools/afsk_channel.py)
  * the noise floor from the quiet parts, the SNR, and the level dependence of the gain (TX limiter / AGC) from the -6 and
    -12 dB chirps
  * the BV burst run through the real receiver (krk_bv_*.c): sync, blocks, byte errors, EVM / SNR
  * a recommendation: fast D8PSK / robust DQPSK / neither, and the best carrier frequency in 1500-2100 Hz

Usage: bv_sounding_analysis.py capture.wav        (or  capture.pcm  = raw signed 16 bit little endian, 8 kHz)
       bv_sounding_analysis.py --reference ref.pcm   writes the probe the radio generates (for a simulator or a file)
Needs only the python standard library and a host C compiler; numpy is used when installed (faster FFT).
"""
import math
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))
import hostcc  # noqa: E402
import pcmio  # noqa: E402

UI = hostcc.UI
FS = 8000.0
SEG = {"tone": 0, "chirpA": 3072, "chirpB": 19712, "chirpC": 26112, "frame": 33280}    # reference sample offsets
LEN = {"chirpA": 16128, "chirpB": 6400, "chirpC": 6400}
try:
    import numpy as np
except ImportError:                                  # pragma: no cover - fallback path
    np = None

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "krk_bv.h"
int main(int argc, char **argv) {
    static unsigned char pay[BV_SOUND_UNITS * BV_UNIT_BYTES], got[5632];
    static short blk[256];
    int i, n = 0;
    if (!strcmp(argv[1], "ref")) {                  /* ref out.pcm */
        FILE *fp = fopen(argv[2], "wb");
        krk_bv_sound_start(pay);
        for (;;) { int more = krk_bv_sound_fill(blk); fwrite(blk, 2, 256, fp); n += 256; if (!more) break; }
        fclose(fp); printf("%d\n", n); return 0; }
    if (!strcmp(argv[1], "rx")) {                   /* rx in.pcm [carrier_hz] */
        FILE *fp = fopen(argv[2], "rb");
        int ev = 0, synced = 0, byte_err = 0;
        const krk_bv_info_t *p;
        if (argc > 3) krk_bv_set_carrier(atoi(argv[3]));
        krk_bv_sound_payload(pay);
        krk_bv_rx_begin(got, sizeof got);
        while (fread(blk, 2, 256, fp) == 256) { int e = krk_bv_rx_feed(blk, 256); ev |= e; if (e & BV_EV_SYNC) synced++; }
        p = krk_bv_info();
        for (i = 0; i < BV_SOUND_UNITS * BV_UNIT_BYTES; i++) if (got[i] != pay[i]) byte_err++;
        printf("events %d syncs %d have_hdr %d mode %d blocks_ok %d blocks_bad %d blocks_seen %d rs_corrected %u evm_q16 %u byte_errors %d\n",
               ev, synced, p->have_hdr, p->mode, p->blocks_ok, p->blocks_bad, p->blocks_seen, p->rs_corrected, p->evm_q16, byte_err);
        return 0; }
    return 1; }
'''


_cache = {}


def driver():
    if "drv" not in _cache:
        _cache["drv"] = hostcc.build([UI / "krk_bv_modem.c", UI / "krk_bv_frame.c", UI / "krk_bv_sound.c"],
                                     flags=["-O2", "-w"], driver_src=DRIVER)
    return _cache["drv"]


def reference():
    d = driver()
    ref = d.parent / "ref.pcm"
    subprocess.run([str(d), "ref", str(ref)], check=True, capture_output=True)
    return pcmio.unpack(ref.read_bytes())


def run_receiver(samples, carrier_hz=None):
    d = driver()
    cap = d.parent / "cap.pcm"
    cap.write_bytes(pcmio.pack([round(v) for v in samples]))
    args = [str(d), "rx", str(cap)] + ([str(carrier_hz)] if carrier_hz else [])
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout.split()
    r = {out[i]: int(out[i + 1]) for i in range(0, len(out), 2)}
    r["snr_evm_db"] = -10 * math.log10(max(r["evm_q16"], 1) / 65536.0) if r["evm_q16"] else None
    return r


# ---- FFT helpers ----------------------------------------------------------------------------------------------
def _fft_py(a, inv=False):
    n = len(a)
    a = list(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    ln = 2
    while ln <= n:
        ang = 2 * math.pi / ln * (1 if inv else -1)
        wl = complex(math.cos(ang), math.sin(ang))
        for i in range(0, n, ln):
            w = 1 + 0j
            for k in range(ln // 2):
                u, v = a[i + k], a[i + k + ln // 2] * w
                a[i + k], a[i + k + ln // 2] = u + v, u - v
                w *= wl
        ln <<= 1
    return [v / n for v in a] if inv else a


def fft(x):
    return np.fft.fft(np.asarray(x, dtype=complex)) if np is not None else _fft_py(x)


def ifft(x):
    return np.fft.ifft(np.asarray(x, dtype=complex)) if np is not None else _fft_py(x, True)


# ---- input -----------------------------------------------------------------------------------------------------
def load_capture(path):
    return pcmio.read(path)


# ---- analysis --------------------------------------------------------------------------------------------------
def _xcorr_peak(cap, ref_seg, lo=0, hi=None):
    """Position in cap where ref_seg matches best (FFT cross-correlation, magnitude peak)."""
    hi = len(cap) if hi is None else hi
    seg = cap[lo:hi]
    n = 1
    while n < len(seg) + len(ref_seg):
        n <<= 1
    A = fft(list(seg) + [0] * (n - len(seg)))
    B = fft(list(ref_seg) + [0] * (n - len(ref_seg)))
    c = ifft([a * (b.conjugate() if hasattr(b, "conjugate") else b) for a, b in zip(A, B)])
    mags = [abs(v) for v in c[:len(seg)]]
    k = max(range(len(mags)), key=mags.__getitem__)
    return lo + k, mags[k]


def _estimate_response(cap, ref, off_cap, off_ref, length, f_lo, f_hi, nfft, pad=256, tail=640):
    """H(f) = Y/X for one chirp. Returns (freqs, H) restricted to [f_lo, f_hi]."""
    x = [0.0] * pad + [float(v) for v in ref[off_ref:off_ref + length]]
    y = [float(v) for v in cap[off_cap - pad:off_cap + length + tail]] if off_cap - pad >= 0 else None
    if y is None or len(y) < length:
        return None
    x += [0.0] * (nfft - len(x))
    y += [0.0] * (nfft - len(y))
    X, Y = fft(x), fft(y)
    df = FS / nfft
    k0, k1 = int(f_lo / df), int(f_hi / df)
    return df, [(Y[k] / X[k]) if abs(X[k]) > 1e-6 else 0j for k in range(k0, k1 + 1)], k0


def _smooth(vals, half):
    """Moving average with a +-half window (handles complex)."""
    n = len(vals)
    c = [0.0] * (n + 1)
    acc = 0.0
    for i, v in enumerate(vals):
        acc += v
        c[i + 1] = acc
    return [(c[min(n, i + half + 1)] - c[max(0, i - half)]) / (min(n, i + half + 1) - max(0, i - half)) for i in range(n)]


def channel_response(cap, ref, off_cap, off_ref, length, f_lo, f_hi, nfft):
    r = _estimate_response(cap, ref, off_cap, off_ref, length, f_lo - 100, f_hi + 100, nfft)
    if r is None:
        return None
    df, H, k0 = r
    half = int(25.0 / df)
    p = _smooth([abs(h) ** 2 for h in H], half)
    m = 16
    z = [H[i + m] * H[i].conjugate() for i in range(len(H) - m)] + [0j] * m
    zs = _smooth(z, half)
    freqs = [(k0 + i) * df for i in range(len(H))]
    mag_db = [10 * math.log10(max(v, 1e-12)) for v in p]
    gd = [(-math.atan2(v.imag, v.real) / (2 * math.pi * df * m)) * 1000.0 for v in zs]     # ms
    return freqs, mag_db, gd


def at(freqs, vals, f):
    i = min(range(len(freqs)), key=lambda k: abs(freqs[k] - f))
    return vals[i]


def analyse(cap, carrier_hz=1700):
    ref = reference()
    res = {"n": len(cap), "notes": []}
    mean = sum(cap) / len(cap)
    cap = [v - mean for v in cap]
    # alignment on chirp A
    segA = ref[SEG["chirpA"]:SEG["chirpA"] + LEN["chirpA"]]
    lagA, pk = _xcorr_peak(cap, segA)
    res["lagA"] = lagA
    res["start_s"] = (lagA - SEG["chirpA"]) / FS
    if lagA - SEG["chirpA"] < -50 or lagA + 33300 > len(cap) + 8000:
        res["notes"].append("capture does not contain the whole probe; results below may be partial")
    segB = ref[SEG["chirpB"]:SEG["chirpB"] + LEN["chirpB"]]
    expB = lagA + SEG["chirpB"] - SEG["chirpA"]
    lagB, _ = _xcorr_peak(cap, segB, max(0, expB - 600), min(len(cap), expB + 600 + LEN["chirpB"]))
    ratio = (lagB - lagA) / float(SEG["chirpB"] - SEG["chirpA"])
    res["clock_ppm"] = (ratio - 1.0) * 1e6
    # response from the 0 dB chirp
    out = channel_response(cap, ref, lagA, SEG["chirpA"], LEN["chirpA"], 250, 3850, 32768)
    if out is None:
        res["notes"].append("chirp not fully captured")
        return res
    freqs, mag_db, gd = out
    ref_level = sorted(at(freqs, mag_db, f) for f in range(1000, 2500, 50))[len(range(1000, 2500, 50)) // 2]
    res["freq"] = [(f, at(freqs, mag_db, f) - ref_level, at(freqs, gd, f)) for f in range(300, 3801, 100)]
    gdref = sorted(at(freqs, gd, f) for f in range(1000, 2500, 50))[len(range(1000, 2500, 50)) // 2]
    res["freq"] = [(f, m, g - gdref) for f, m, g in res["freq"]]
    # fine notch search around the carrier / anywhere in 1000-2400 (smoothed +-25 Hz)
    band = [(f, m - ref_level) for f, m in zip(freqs, mag_db) if 1000 <= f <= 2400]
    fmin, mmin = min(band, key=lambda t: t[1])
    res["notch"] = (fmin, mmin)
    res["ripple_db"] = max(m for f, m in band if True) - mmin
    res["gd_spread_ms"] = max(g for f, g in zip(freqs, gd) if 1000 <= f <= 2400) - min(g for f, g in zip(freqs, gd) if 1000 <= f <= 2400)
    res["edge_lo_db"] = at(freqs, mag_db, 500) - ref_level
    res["edge_hi_db"] = at(freqs, mag_db, 3000) - ref_level
    # carrier choice: maximise the worst level within +-1000 Hz of fc (the bulk of the RRC spectrum)
    best = None
    for fc in range(1500, 2101, 100):
        worst = min(m - ref_level for f, m in zip(freqs, mag_db) if fc - 1000 <= f <= fc + 1000)
        gdv = [g for f, g in zip(freqs, gd) if fc - 1000 <= f <= fc + 1000]
        score = worst - 0.5 * (max(gdv) - min(gdv))
        res.setdefault("fc_scores", []).append((fc, worst, max(gdv) - min(gdv), score))
        if best is None or score > best[1] + (0.0 if fc != 1700 else -0.0):
            best = (fc, score)
    res["best_fc"] = best[0]
    # level dependence from the -6 / -12 dB chirps
    lv = {}
    for nm, want in (("chirpB", -6.0), ("chirpC", -12.0)):
        off = lagA + int(round((SEG[nm] - SEG["chirpA"]) * ratio))
        o = channel_response(cap, ref, off, SEG[nm], LEN[nm], 400, 3200, 16384)
        if o is None:
            continue
        fB, mB, _ = o
        d = []
        for f in range(600, 3001, 100):
            d.append(at(fB, mB, f) - (at(freqs, mag_db, f)))
        lv[nm] = sum(d) / len(d)
    res["level_gain_db"] = lv
    # noise and SNR
    qs = []
    pre = lagA - SEG["chirpA"]
    if pre > 1200:
        qs.append(cap[200:pre - 100])
    for a, b in ((SEG["tone"] + 2560 + 256, SEG["chirpA"]), (SEG["chirpA"] + 16128 + 256, SEG["chirpB"]), (SEG["chirpC"] + 6400 + 256, SEG["frame"])):
        o = lagA - SEG["chirpA"]
        if o + b <= len(cap):
            qs.append(cap[o + a:o + b])
    noise = [v for q in qs for v in q]
    if len(noise) > 200:
        res["noise_rms"] = math.sqrt(sum(v * v for v in noise) / len(noise))
    sig = cap[lagA + 2000:lagA + 14000]
    res["chirp_rms"] = math.sqrt(sum(v * v for v in sig) / len(sig))
    if res.get("noise_rms"):
        res["snr_db"] = 20 * math.log10(res["chirp_rms"] / max(res["noise_rms"], 1e-9))
    # BV burst through the real receiver
    fo = lagA + int(round((SEG["frame"] - SEG["chirpA"]) * ratio))
    chunk = cap[max(0, fo - 3000):min(len(cap), fo + 12000)]
    res["bv"] = run_receiver(chunk, carrier_hz)
    res["recommendation"] = recommend(res)
    return res


def recommend(r):
    bv = r.get("bv") or {}
    notch_depth = -r["notch"][1]
    all_ok = bv.get("blocks_ok") == 2 and bv.get("byte_errors") == 0
    snr_evm = bv.get("snr_evm_db")
    why = []
    if notch_depth >= 10:
        mode = "NEITHER single-carrier mode (use the multi-tone fallback or stay with AFSK/DMR)"
        why.append(f"a {notch_depth:.0f} dB hole at {r['notch'][0]:.0f} Hz (smoothed) inside the 1000-2400 Hz band cannot be equalized")
    elif all_ok and snr_evm is not None and snr_evm >= 13:
        mode = "FAST (D8PSK 2000 baud, 6.0 kbps)"
        why.append(f"the fast-mode test burst decoded error free at an equalized SNR of {snr_evm:.1f} dB")
    elif bv.get("blocks_ok", 0) > 0 or (bv.get("syncs", 0) and snr_evm is not None and snr_evm >= 8):
        mode = "ROBUST (DQPSK 2000 baud, 4.0 kbps)"
        why.append(f"the fast burst was only partly decoded ({bv.get('blocks_ok', 0)}/2 blocks, equalized SNR "
                   f"{snr_evm if snr_evm is not None else float('nan'):.1f} dB); DQPSK needs about 6 dB less")
    else:
        mode = "INCONCLUSIVE (the burst was not received; repeat with a stronger signal or squelch open)"
        if r.get("snr_db") is not None and r["snr_db"] < 10:
            why.append(f"channel SNR only {r['snr_db']:.1f} dB")
    lvl = r.get("level_gain_db", {})
    if lvl and max(abs(v) for v in lvl.values()) > 1.5:
        why.append("the gain changes with level by %s dB: the TX/RX chain limits or has an AGC; keep PSK, avoid QAM" %
                   ", ".join(f"{k[-1]}:{v:+.1f}" for k, v in lvl.items()))
    fc = r["best_fc"]
    scores = {c: s for c, w, g, s in r["fc_scores"]}
    carrier = "keep 1700 Hz"
    if fc != 1700 and scores[fc] - scores[1700] > 3.0:
        carrier = f"move the carrier to {fc} Hz (krk_bv_set_carrier; {scores[fc] - scores[1700]:+.1f} dB better worst-case level)"
    return {"mode": mode, "carrier": carrier, "why": why}


def report(r):
    L = []
    L.append(f"capture: {r['n']} samples ({r['n'] / FS:.2f} s); probe starts at {r.get('start_s', float('nan')):.3f} s; "
             f"clock offset {r.get('clock_ppm', float('nan')):+.0f} ppm")
    for n in r["notes"]:
        L.append("note: " + n)
    if "freq" not in r:
        return "\n".join(L)
    L.append("")
    L.append("  f/Hz   mag/dB   group delay/ms   (mag relative to the median of 1000-2400 Hz, delay relative to the same band)")
    for f, m, g in r["freq"]:
        bar = "#" * max(0, int(round(30 + m)))
        L.append(f"  {f:5d}  {m:+6.1f}   {g:+7.2f}        {bar[:40]}")
    L.append("")
    L.append(f"deepest dip in 1000-2400 Hz: {r['notch'][1]:+.1f} dB at {r['notch'][0]:.0f} Hz (smoothed +-25 Hz); ripple {r['ripple_db']:.1f} dB; "
             f"delay spread {r['gd_spread_ms']:.2f} ms; level at 500 Hz {r['edge_lo_db']:+.1f} dB, at 3000 Hz {r['edge_hi_db']:+.1f} dB")
    if "noise_rms" in r:
        L.append(f"noise floor {r['noise_rms']:.2f} LSB rms, chirp {r['chirp_rms']:.0f} LSB rms -> SNR {r['snr_db']:.1f} dB"
                 + ("   (8-bit captures, such as the APRS RX screen's PCM stream, have a quantization floor)" if r["noise_rms"] < 1.0 else ""))
    if r.get("level_gain_db"):
        L.append("gain of the -6/-12 dB chirps vs the 0 dB chirp: " + ", ".join(f"{k}: {v:+.2f} dB" for k, v in r["level_gain_db"].items())
                 + "  (0 = linear, no limiter / AGC action)")
    bv = r.get("bv")
    if bv:
        L.append(f"BV burst: syncs {bv['syncs']}, mode {bv['mode']}, blocks ok {bv['blocks_ok']}/2 (bad {bv['blocks_bad']}), "
                 f"RS corrected bytes {bv['rs_corrected']}, byte errors {bv['byte_errors']}/324"
                 + (f", SNR from EVM {bv['snr_evm_db']:.1f} dB" if bv.get("snr_evm_db") is not None else ""))
    L.append("")
    L.append("carrier candidates (worst-case level within +-1 kHz of fc / delay spread / score):")
    L.append("  " + "   ".join(f"{fc}: {w:+.1f} dB, {g:.2f} ms" for fc, w, g, s in r["fc_scores"]))
    rec = r["recommendation"]
    L.append("")
    L.append("RECOMMENDATION: " + rec["mode"])
    L.append("  carrier: " + rec["carrier"])
    for w in rec["why"]:
        L.append("  - " + w)
    return "\n".join(L)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--reference":
        ref = reference()
        pathlib.Path(sys.argv[2]).write_bytes(pcmio.pack(ref))
        print(f"wrote {len(ref)} samples ({len(ref) / FS:.2f} s) to {sys.argv[2]}")
        return 0
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    print(report(analyse(load_capture(sys.argv[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
