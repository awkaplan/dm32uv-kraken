#!/usr/bin/env python3
"""Record the APRS RX screen's PCM stream from the programming cable into a WAV.

The screen (v13+, key '0') streams the captured FM RX audio as signed 8-bit samples
at 8 kHz on UART0 (115200 8N1), framed as 256-sample blocks each preceded by the 4-byte marker
7F 80 7F 80 (a pattern the clamp never produces twice in a row) and a shift byte s: sample = int8 << s.

Usage: pcm_capture.py --port PORT [--seconds 20] out.wav
Run it, then press '0' on the radio to start streaming; press '0' again to stop. Stdlib only
except pyserial: run with `uv run --with pyserial python3 tools/pcm_capture.py ...`.
"""
import argparse
import sys
import time

import pcmio

MARK = b"\x7f\x80\x7f\x80"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", required=True, help="serial port of the programming cable, e.g. /dev/cu.usbserial-XXXX or COM3")
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args()
    import serial  # pyserial
    with serial.Serial(a.port, 115200, timeout=0.2) as s:
        buf = bytearray()
        t0 = time.time()
        print(f"recording {a.seconds:.0f} s from {a.port}; press '0' on the radio...", flush=True)
        while time.time() - t0 < a.seconds:
            buf += s.read(4096)
    blocks, i, n, markers = [], 0, 0, []
    while True:
        j = buf.find(MARK, i)
        if j < 0 or j + 5 + 256 > len(buf):
            break
        sh = buf[j + 4]
        if sh > 8:                                      # not a real header: resync
            i = j + 1
            continue
        if j > i:                                       # text between blocks (CFG/RES lines of the RX sweep)
            for line in buf[i:j].decode("latin1").split("\n"):
                line = line.strip()
                if line.startswith(("CFG", "RES", "PCM8", "SNAP", "RAM")):
                    markers.append((n, line))
        blocks.append((sh, bytes(buf[j + 5:j + 5 + 256])))
        i = j + 5 + 256
        n += 1
    pcmio.write_wav(a.out, [(b - 256 if b > 127 else b) << sh for sh, blk in blocks for b in blk])
    print(f"{len(buf)} bytes received, {n} blocks ({n * 32} ms of audio) -> {a.out}")
    if markers:
        mpath = a.out + ".markers.txt"
        with open(mpath, "w") as f:
            for blk, line in markers:
                f.write(f"{blk} {line}\n")
        print(f"{len(markers)} marker lines -> {mpath}")
        for blk, line in markers:
            if line.startswith(("RES", "SNAP", "RAM")):
                print("  ", line)
    if not n:
        sys.exit(1)


if __name__ == "__main__":
    main()
