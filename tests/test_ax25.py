#!/usr/bin/env python3
"""Host test for patches/kraken_ui/krk_ax25.c: build frames with the C code, then decode the
tone sequence with an independent Python receiver (NRZI decode, flag hunt, unstuff, FCS check,
address parse) and compare against the CRC-16/X.25 check value. Also writes a 1200-baud AFSK
WAV of the beacon for an off-air decoder (direwolf / multimon-ng) if one is available.

Run inside the dm32uv-re Docker image (host cc may be unusable):
  python3 /w/dm32uv-unleashed/tests/test_ax25.py
"""
import sys
import hostcc
from hostcc import ROOT, TOOLS, UI
from hostlib import check, finish
sys.path.insert(0, str(TOOLS))
import afsk_channel as ch
import pcmio

SRC = UI / "krk_ax25.c"


DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "krk_ax25.h"
int main(int argc, char **argv) {
    /* argv: dest src info [path...] ; prints frame hex, then tone bits as 0/1 chars */
    unsigned char f[400], t[4000];
    const char *path[6]; int np = argc - 4, i;
    if (strcmp(argv[1], "crc") == 0) { printf("%04x\n", krk_ax25_fcs((const unsigned char *)"123456789", 9)); return 0; }
    for (i = 0; i < np; i++) path[i] = argv[4 + i];
    int n = krk_ax25_frame(f, sizeof f, argv[1], argv[2], path, np, argv[3]);
    if (n < 0) { printf("ERR\n"); return 0; }
    for (i = 0; i < n; i++) printf("%02x", f[i]);
    printf("\n");
    int nb = krk_ax25_tones(f, n, 8, 2, t, sizeof t * 8);
    for (i = 0; i < nb; i++) putchar('0' + ((t[i >> 3] >> (i & 7)) & 1));
    printf("\n");
    return 0;
}
'''


def decode(tones):
    """Independent receiver: NRZI -> bits, find flags, unstuff, return the frames between flags."""
    bits, prev = [], 0                       # transmitter starts on mark (0)
    for t in tones:
        bits.append(1 if t == prev else 0)
        prev = t
    frames, cur, ones, inframe, i = [], [], 0, False, 0
    s = "".join(map(str, bits))
    while i < len(bits):
        if s.startswith("01111110", i):
            if inframe and len(cur) >= 8:
                frames.append(cur)
            cur, ones, inframe = [], 0, True
            i += 8
            continue
        b = bits[i]
        if inframe:
            if ones == 5:
                if b != 0:
                    inframe = False          # abort
                ones = 0
                i += 1
                continue
            cur.append(b)
            ones = ones + 1 if b else 0
        i += 1
    out = []
    for c in frames:
        if len(c) % 8:
            continue
        out.append(bytes(sum(c[k + j] << j for j in range(8)) for k in range(0, len(c), 8)))
    return out


def parse_addrs(fr):
    addrs, i = [], 0
    while True:
        a = fr[i:i + 7]
        call = bytes(x >> 1 for x in a[:6]).decode().strip()
        ssid = (a[6] >> 1) & 0xF
        addrs.append(call + (f"-{ssid}" if ssid else ""))
        i += 7
        if a[6] & 1:
            return addrs, fr[i:]


def afsk_wav(tones, path, rate=48000):
    pcmio.write_wav(path, ch.cpfsk(tones, fs=rate, lead_ms=0, tail_ms=0, amp=12000), rate)


def main():
    exe = hostcc.build([SRC], flags=["-Wall", "-O2"], driver_src=DRIVER)
    out = hostcc.run(exe, "crc").strip()
    check(out == "906e", f"CRC-16/X.25 check value of '123456789' = 0x{out} (want 0x906e)")

    cases = [
        ("APZKRK", "N0CALL-9", "!4142.85N/07243.60W>DM-32UV Kraken", ["WIDE1-1", "WIDE2-1"]),
        ("APZKRK", "W1AW", ">status", []),
        ("APRS", "N0CALL-15", "!0000.00N/00000.00E-" + "x" * 60, ["WIDE2-2"]),
        ("APZKRK", "VERYLONGCALL", "x", []),     # invalid: 7+ chars
        ("APZKRK", "N0CALL-16", "x", []),        # invalid SSID
    ]
    beacon_tones = None
    for dest, src, info, path in cases:
        lines = hostcc.run(exe, dest, src, info, *path).split("\n")
        if lines[0] == "ERR":
            check(src in ("VERYLONGCALL", "N0CALL-16"), f"{src}: rejected")
            continue
        frame = bytes.fromhex(lines[0])
        tones = [int(c) for c in lines[1]]
        body, fcs = frame[:-2], frame[-2] | frame[-1] << 8
        check(fcs == ch.crc_x25(body), f"{src}: FCS 0x{fcs:04x} matches the reference CRC")
        got = decode(tones)
        check(len(got) == 1 and got[0] == frame, f"{src}: independent NRZI/unstuff decoder recovers the frame ({len(tones)} bits)")
        addrs, rest = parse_addrs(got[0][:-2] if got else frame[:-2])
        want = [dest, src] + path
        check(addrs == want and rest[:2] == b"\x03\xf0" and rest[2:].decode() == info,
               f"{src}: addresses {addrs}, control/PID 03 f0, info round-trips")
        ones = run = 0
        stuffed_ok = True
        # no six 1s in a row between the flags (bit stuffing works)
        bits, prev = [], 0
        for t in tones:
            bits.append(1 if t == prev else 0)
            prev = t
        s = "".join(map(str, bits))[64:-16]
        check("111111" not in s, f"{src}: no 6 consecutive ones inside the frame (stuffing)")
        if beacon_tones is None:
            beacon_tones = tones
    wav = ROOT / "out/afsk_beacon_test.wav"
    wav.parent.mkdir(exist_ok=True)
    afsk_wav(beacon_tones, wav)
    print(f"wrote {wav} ({len(beacon_tones)} bits, {len(beacon_tones) / 1200 * 1000:.0f} ms)")
    finish()


if __name__ == "__main__":
    main()
