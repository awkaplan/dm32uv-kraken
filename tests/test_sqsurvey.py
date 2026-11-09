#!/usr/bin/env python3
"""Host test for patches/kraken_ui/krk_sqsurvey.h (F11 one-key squelch survey: baseline, carrier/idle
classes, threshold suggestion). Pure logic, no MMIO. Run: python3 tests/test_sqsurvey.py"""
import hostcc
from hostlib import check, finish

DRV = r'''
#include <stdio.h>
#include "krk_sqsurvey.h"
/* stdin: one "noise rssi" pair per line (250 of them); prints the result */
int main(void) {
    struct sqsv s; unsigned nz, rs; int done = 0, st; u8 nrm[2] = {0, 0}, tgt[2] = {0, 0};
    sv_reset(&s);
    while (scanf("%u %u", &nz, &rs) == 2) done = sv_step(&s, nz, rs);
    st = sv_derive(&s, nrm, tgt);
    printf("done=%d st=%d base=%u idle=%u/%u carr=%u/%u nrm=%u/%u tgt=%u/%u\n", done, st, s.base_rssi, s.idle_n, s.idle_min,
           s.carr_n, s.carr_max, nrm[0], nrm[1], tgt[0], tgt[1]);
    return 0;
}
'''
exe = hostcc.build([], flags=["-Wall", "-O1"], driver_src=DRV)
def run(samples):
    inp = "".join(f"{n} {r_}\n" for n, r_ in samples)
    return hostcc.run(exe, input=inp).strip()
def case(name, samples, want):
    got = run(samples)
    check(got == want, name + (f"\n   got  {got}\n   want {want}" if got != want else ""))

IDLE_R, CARR_R = 100, 300           # half-dB
def scenario(idle_nz, carr_nz, a=100, b=170, n=250, carr_r=CARR_R):
    out = []
    for i in range(n):
        c = a <= i < b
        out.append(((carr_nz if c else idle_nz)[i % 5], carr_r if c else IDLE_R))
    return out

idle = [60, 62, 58, 61, 63]
carr = [20, 22, 25, 21, 24]
# the first sample of a class run counts as run 1: a class loses its first SV_SETTLE-1 = 2 samples at start
# and, after a change, 3 samples (run 0,1,2 skipped). 250 samples: idle 0..99 -> 98, carrier 100..169 -> 67, idle 170..249 -> 77
case("idle 58..63 vs carrier 20..25: gap 33 -> tight 25+11, normal 25+22, out = in+2",
     scenario(idle, carr), "done=1 st=0 base=100 idle=175/58 carr=67/25 nrm=47/49 tgt=36/38")
case("window not complete after 249 samples", scenario(idle, carr, n=249)[:249], "done=0 st=0 base=100 idle=174/58 carr=67/25 nrm=47/49 tgt=36/38")
case("no carrier in the window: no suggestion (status 1)", scenario(idle, carr, a=300, b=300), "done=1 st=1 base=100 idle=248/58 carr=0/0 nrm=0/0 tgt=0/0")
case("RSSI rise under 10 dB is not a carrier", scenario(idle, carr, carr_r=IDLE_R + 20), "done=1 st=1 base=100 idle=248/20 carr=0/0 nrm=0/0 tgt=0/0")
case("carrier only 4 samples long: too few (status 1)", scenario(idle, carr, a=100, b=104), "done=1 st=1 base=100 idle=241/58 carr=1/21 nrm=0/0 tgt=0/0")
case("carrier from 3 s on: only the baseline counts as idle",
     scenario(idle, carr, a=30, b=250), "done=1 st=0 base=100 idle=28/58 carr=217/25 nrm=47/49 tgt=36/38")
case("carrier noise as high as idle noise: no gap (status 3)", scenario(idle, [57, 58, 59, 60, 61]), "done=1 st=3 base=100 idle=175/58 carr=67/61 nrm=0/0 tgt=0/0")
finish()
