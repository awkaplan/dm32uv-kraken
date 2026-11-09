#!/usr/bin/env python3
"""Estimate the min-entropy of the raw noise samples behind patches/mi_rng, from a live-RAM
capture of the measurement build's sample tap, and size SEED_ROUNDS from it.

WHAT THE INPUT IS: a dump of SRAM 0x18005000..0x18007000 taken from a radio running the
mi_rng_tap build (patches/mi_rng/mi_rng_tap.patch.json), e.g.
    python3 dm32_ramdump.py --port /dev/tty.usbserial-XXXX --addr 0x18005000 --size 0x2000 \\
        --out out/mi_rng_tap_1.bin
The layout is struct mi_rng_tap in mi_rng.c: u32 magic "MITP", u32 count, u32 capacity, u32 rounds,
then capacity x 8-byte records (u32 timer-after, u16 adc value, u16 conversion ticks). Records are
written in rounds of four: ADC channels 1,2,1,2. Several captures from several boots (ideally
several radios) can be given at once; they are pooled, and per-file counts are reported.

METHOD (deliberately conservative, after NIST SP 800-90B section 6.3.1, the most-common-value
estimator): for each field and each ADC channel, H = -log2(p_u) where p_u is the 99 % upper
confidence bound on the most common value's frequency. The fields are looked at both whole and
by their low bits, because the whole 32-bit timer stamp is nearly unique in a few hundred samples,
which the estimator rightly refuses to credit (p_u saturates). The per-sample credit is the BEST
single field of that sample, not a sum: the fields of one record are not independent (the stamp
and the conversion time share the ADC phase). The per-round credit is the SUM over the four
samples only under --assume-independent-samples; by default it is the WORST channel's credit times
the number of samples of that channel in a round, which is the right answer if the two channels'
noise is correlated. SEED_ROUNDS is then ceil(256 / H_round) with a 2x margin, rounded up to 8.

WHAT THIS CANNOT TELL YOU: whether the noise is attacker-influenceable (temperature, supply,
a strong carrier on the antenna), whether it is stationary across radios, or anything at all
from an emulator run (--source qemu marks the report so it is not mistaken for a measurement).
A handful of boots on one radio is a first look, not a characterisation. Treat any estimate here
as an upper bound on what the hardware gives, and the SEED_ROUNDS it suggests as a floor.

USAGE:
    python3 mi_rng_entropy.py capture1.bin [capture2.bin ...] [--source hardware|qemu]
                              [--assume-independent-samples] [--json out.json]
Exit status 0 on a parsable capture; the verdict is in the printed report.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import struct
import sys
from collections import Counter

MAGIC = 0x5054494D
REC = 8
CHANNELS = (1, 2, 1, 2)       # mi_rng.c adc_channel[]
TARGET_BITS = 256
MARGIN = 2.0
Z99 = 2.576


def parse(path: pathlib.Path):
    b = path.read_bytes()
    if len(b) < 16:
        raise ValueError(f"{path}: too short")
    magic, count, cap, rounds = struct.unpack_from("<IIII", b, 0)
    if magic != MAGIC:
        raise ValueError(f"{path}: magic {magic:#x} is not MITP; is this SRAM 0x18005000 of a mi_rng_tap build?")
    if cap * REC + 16 > len(b):
        raise ValueError(f"{path}: capacity {cap} exceeds the file")
    n = min(count, cap)
    recs = []
    # ring: if count > cap the oldest record is at index count % cap
    start = count % cap if count > cap else 0
    for k in range(n):
        i = (start + k) % cap
        t, v, d = struct.unpack_from("<IHH", b, 16 + i * REC)
        recs.append((t, v, d))
    return dict(path=str(path), count=count, capacity=cap, rounds=rounds, kept=n, recs=recs)


def mcv_entropy(values):
    """Most-common-value estimate: bits per value, with the 99 % upper bound on p."""
    n = len(values)
    if n == 0:
        return 0.0, 0, n
    c = Counter(values)
    top, k = c.most_common(1)[0]
    p = k / n
    p_u = min(1.0, p + Z99 * math.sqrt(p * (1 - p) / n))
    return -math.log2(p_u) if p_u < 1 else 0.0, len(c), n


def serial_corr(values, lag=1):
    n = len(values) - lag
    if n < 3:
        return float("nan")
    a = values[:n]
    b = values[lag:lag + n]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else float("nan")


def bit_bias(values, nbits):
    out = []
    n = len(values)
    for bit in range(nbits):
        ones = sum((v >> bit) & 1 for v in values)
        out.append(ones / n if n else float("nan"))
    return out


def views(t, v, d):
    """Named derived fields of one record. Keep each small enough for MCV to be meaningful."""
    return {
        "adc10": v & 0x3FF,
        "adc_lo4": v & 0xF,
        "adc_lo2": v & 0x3,
        "ticks16": d & 0xFFFF,
        "ticks_lo8": d & 0xFF,
        "ticks_lo4": d & 0xF,
        "stamp_lo8": t & 0xFF,
        "stamp_lo12": t & 0xFFF,
        "stamp_lo16": t & 0xFFFF,
    }


def analyse(captures, independent):
    recs = [r for c in captures for r in c["recs"]]
    # channel index within a round: position mod 4 -> CHANNELS; valid only if every capture
    # starts on a round boundary (count % 4 == 0 when not wrapped), which the tap guarantees for
    # the first fill. Warn otherwise.
    per_ch = {1: [], 2: []}
    for c in captures:
        off = 0 if c["count"] <= c["capacity"] else c["count"] % 4
        for k, r in enumerate(c["recs"]):
            per_ch[CHANNELS[(k + off) % 4]].append(r)

    report = {"samples": len(recs), "per_channel": {}}
    best_per_ch = {}
    for ch, rs in per_ch.items():
        fields = {}
        for name in views(0, 0, 0):
            vals = [views(*r)[name] for r in rs]
            h, distinct, n = mcv_entropy(vals)
            fields[name] = dict(bits=round(h, 3), distinct=distinct, n=n,
                                serial_corr_lag1=round(serial_corr(vals), 3),
                                serial_corr_lag2=round(serial_corr(vals, 2), 3))
        # per-sample credit: the best single field, but never more than the field's own width
        # and never more than log2(n) (an n-sample test cannot witness more).
        cap = math.log2(len(rs)) if rs else 0
        best_name, best = max(((k, min(f["bits"], cap)) for k, f in fields.items()), key=lambda kv: kv[1])
        best_per_ch[ch] = best
        lo8 = [r[1] & 0xFF for r in rs]
        report["per_channel"][ch] = dict(
            samples=len(rs), fields=fields, best_field=best_name, credit_bits_per_sample=round(best, 3),
            adc_lo8_bit_bias=[round(x, 3) for x in bit_bias(lo8, 8)],
            adc_min=min((r[1] for r in rs), default=None), adc_max=max((r[1] for r in rs), default=None),
            ticks_min=min((r[2] for r in rs), default=None), ticks_max=max((r[2] for r in rs), default=None))

    if independent:
        h_round = sum(best_per_ch[CHANNELS[i]] for i in range(4))
        basis = "sum over the 4 samples of a round (--assume-independent-samples)"
    else:
        h_round = min(best_per_ch.values()) * 4 if best_per_ch else 0
        basis = "worst channel's per-sample credit x 4 (channels assumed correlated)"
    rounds_needed = math.inf if h_round <= 0 else math.ceil(TARGET_BITS * MARGIN / h_round / 8) * 8
    report["per_round_bits"] = round(h_round, 3)
    report["basis"] = basis
    report["seed_rounds_for_256_bits_with_2x_margin"] = rounds_needed
    report["current_SEED_ROUNDS"] = 64
    report["mix_rounds_bits"] = round(2 * h_round, 3)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("captures", nargs="+", type=pathlib.Path)
    ap.add_argument("--source", choices=("hardware", "qemu"), default="hardware")
    ap.add_argument("--assume-independent-samples", action="store_true")
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args()

    caps = []
    for p in args.captures:
        try:
            caps.append(parse(p))
        except ValueError as e:
            sys.exit(f"FAIL: {e}")
        c = caps[-1]
        print(f"{c['path']}: count={c['count']} capacity={c['capacity']} first-seed rounds={c['rounds']} "
              f"records used={c['kept']}" + ("  (ring wrapped: oldest records lost)" if c["count"] > c["capacity"] else ""))

    rep = analyse(caps, args.assume_independent_samples)
    rep["source"] = args.source
    print()
    if args.source == "qemu":
        print("*** SOURCE IS THE EMULATOR: its ADC is a constant and its timer follows host time.")
        print("*** Nothing below is a statement about hardware entropy. This run checks the pipeline only.")
        print()
    for ch, r in rep["per_channel"].items():
        print(f"ADC channel {ch}: {r['samples']} samples, adc {r['adc_min']}..{r['adc_max']}, "
              f"conversion ticks {r['ticks_min']}..{r['ticks_max']}")
        for name, f in r["fields"].items():
            print(f"   {name:11s} H_mcv={f['bits']:6.3f} bits  distinct={f['distinct']:5d}  "
                  f"corr(lag1)={f['serial_corr_lag1']:+.3f} corr(lag2)={f['serial_corr_lag2']:+.3f}")
        print(f"   adc low-8 bit bias (P(1) per bit, LSB first): {r['adc_lo8_bit_bias']}")
        print(f"   credit per sample: {r['credit_bits_per_sample']} bits (from {r['best_field']})")
    print()
    print(f"per round: {rep['per_round_bits']} bits   [{rep['basis']}]")
    print(f"first seed needs {rep['seed_rounds_for_256_bits_with_2x_margin']} rounds for {TARGET_BITS} bits with "
          f"{MARGIN:g}x margin (mi_rng.c SEED_ROUNDS is {rep['current_SEED_ROUNDS']}); "
          f"each later mix adds ~{rep['mix_rounds_bits']} bits")
    n = rep["samples"]
    if n < 1000:
        print(f"NOTE: {n} samples is too few for a 99 % bound to mean much; take several boots.")
    if args.source == "hardware" and rep["per_round_bits"] > 0 and \
            rep["seed_rounds_for_256_bits_with_2x_margin"] > rep["current_SEED_ROUNDS"]:
        print("VERDICT: SEED_ROUNDS is too small for this data; raise it (or add a noise source).")
    elif args.source == "hardware":
        print("VERDICT: SEED_ROUNDS covers this data's estimate; keep collecting across radios and conditions.")
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
