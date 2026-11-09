#!/usr/bin/env python3
"""Analyse an 8 kHz mono WAV (or raw s16le) of received APRS audio and run the C decoder on it.

Prints: level (RMS, peak, DC), a coarse spectrum (Goertzel at 300..3500 Hz in 100 Hz steps, the
1200/2200 balance = twist), the tone-decision statistics, and the decoder's result (frames, flags,
bad-FCS, decoded lines). Pure Python + the C decoder. With a host cc: python3 tools/afsk_offline.py FILE.
Or inside the build's Docker image: tools/build.sh --run 'python3 tools/afsk_offline.py out/capture.wav'.
"""
import math
import pathlib
import sys
import tempfile

import afsk_host
import pcmio


def goertzel(x, f, fs=8000):
    k = 2 * math.cos(2 * math.pi * f / fs)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + k * s1 - s2
        s2, s1 = s1, s0
    return math.sqrt(max(s1 * s1 + s2 * s2 - k * s1 * s2, 0)) / len(x)


def main():
    path = pathlib.Path(sys.argv[1])
    x = pcmio.read(path, strict_rate=True)
    n = len(x)
    dc = sum(x) / n
    rms = math.sqrt(sum((v - dc) ** 2 for v in x) / n)
    print(f"{path}: {n} samples ({n / 8000:.2f} s)  dc {dc:.1f}  rms {rms:.1f}  peak {max(abs(v) for v in x)}")
    # loudest 0.5 s window, for the spectrum
    best, bi = -1, 0
    for i in range(0, n - 4000, 800):
        e = sum(v * v for v in x[i:i + 4000])
        if e > best:
            best, bi = e, i
    seg = [v - dc for v in x[bi:bi + 4000]]
    print(f"spectrum of the loudest 0.5 s (at {bi / 8000:.2f} s):")
    for f in range(300, 3600, 100):
        g = goertzel(seg, f)
        print(f"  {f:5d} Hz {'#' * int(min(60, g / 3))} {g:.0f}")
    g12, g22 = goertzel(seg, 1200), goertzel(seg, 2200)
    print(f"twist 2200/1200: {20 * math.log10((g22 + 1e-9) / (g12 + 1e-9)):+.1f} dB")
    d = pathlib.Path(tempfile.mkdtemp())
    exe = afsk_host.build_decoder()
    (d / "x.pcm").write_bytes(pcmio.pack(x))
    r = afsk_host.decode(exe, [d / "x.pcm"], block=32)[0]
    print("decoder:", r.summary)
    for line in r.lines:
        print("  " + line)
    # also try with the level scaled up/down and inverted, to see if it is a level problem
    for label, k in (("x4", 4), ("x0.25", 0.25)):
        (d / "y.pcm").write_bytes(pcmio.pack([v * k for v in x]))
        print(f"decoder {label}: {afsk_host.decode(exe, [d / 'y.pcm'], block=32)[0].summary}")


if __name__ == "__main__":
    main()
