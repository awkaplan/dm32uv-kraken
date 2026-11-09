#!/usr/bin/env python3
"""Host test for krk_dmrlink.c's pure part (F3 DMR link quality): the burst accumulator lq_feed/lq_tick and
the line formatter lq_line (no MMIO inside). The target-side screen is compiled out on the host and is
covered by tests/kraken_ui_test.py (read-only polling checked with write watchpoints).
Run: python3 tests/test_dmrlink.py (host cc; inside the dm32uv-re image works too)."""
import hostcc
from hostlib import check, finish

DRV = r'''
#include <stdio.h>
#include <string.h>
#define KRK_FEAT
#include "krk_dmrlink.c"
char *krk_cat(char *d, const char *s) { return strcat(d, s); }
char *krk_catn(char *d, unsigned n, const char *s) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return d; }
char *krk_itoa(int v, char *b) { sprintf(b, "%d", v); return b; }
/* stdin commands: A st elapsed (lq_auto_next) | R reset | F word cnt (hex) | T n ticks | L k analog | S state */
int main(void) {
    struct lq q; char cmd[8], l[KRK_LINE_BUF];
    unsigned w, c, n, k, a;
    lq_reset(&q);
    while (scanf("%7s", cmd) == 1) {
        if (!strcmp(cmd, "R")) lq_reset(&q);
        else if (!strcmp(cmd, "F")) { scanf("%x %x", &w, &c); printf("%d\n", lq_feed(&q, w, c)); }
        else if (!strcmp(cmd, "T")) { scanf("%u", &n); while (n--) lq_tick(&q); }
        else if (!strcmp(cmd, "L")) { scanf("%u %u", &k, &a); lq_line(&q, a, k, l); printf("%s\n", l); }
        else if (!strcmp(cmd, "A")) { scanf("%u %u", &a, &n); printf("%d\n", lq_auto_next(&q, (int)a, n)); }
        else if (!strcmp(cmd, "S")) printf("n=%u emb=%u cs=%u evm=%u se=%u sn=%u\n", q.n, q.emb_err, q.cs_err, q.evm_sum, q.sync_err, q.sync_n);
    }
    return 0;
}
'''
exe = hostcc.build([], flags=["-Wall", "-O1"], driver_src=DRV)
def run(script):
    return hostcc.run(exe, input=script).strip().split("\n")
def case(name, script, want):
    got = run(script)
    check(got == want, name + (f"\n   got  {got}\n   want {want}" if got != want else ""))

def cnt(sync=0, evm=0):
    return "%x" % (sync << 24 | evm)

def word(burst, stype=1, emb=0, cs=0, cc=1, slot=1):
    return "%x" % (burst << 12 | stype | emb << 28 | cs << 27 | cc << 4 | slot << 19)

case("no samples: 'no DMR burst'", "L 1 0\nL 2 0", ["no DMR burst"])
case("analog: n/a (analog)", f"F {word(1)} 0\nL 1 1\nL 2 1", ["1", "n/a (analog)"])
case("same word polled again counts once", f"F {word(1)} 0c\nF {word(1)} 0c\nF {word(1)} 0c\nS", ["1", "0", "0", "n=1 emb=0 cs=0 evm=12 se=0 sn=1"])
case("data / RC / no-burst words are not voice samples",
     f"F {word(1, stype=2)} 5\nF {word(2, stype=3)} 5\nF {word(0, stype=1)} 5\nF {word(7, stype=1)} 5\nS",
     ["0", "0", "0", "0", "n=0 emb=0 cs=0 evm=0 se=0 sn=0"])
case("EMB / check errors and EVM",
     f"F {word(2, emb=1)} {cnt(0, 10)}\nF {word(3, cs=1)} {cnt(0, 20)}\nF {word(4)} {cnt(0, 30)}\nF {word(5, emb=1, cs=1)} {cnt(0, 40)}\nS\nL 2 0\nL 3 0\nL 4 0",
     ["1", "1", "1", "1", "n=4 emb=2 cs=2 evm=100 se=0 sn=0", "EMB err 50% (2)", "chk err 50% (2)", "EVM 25 last 40"])
case("sync errors are summed over burst A only",
     f"F {word(1)} {cnt(3, 10)}\nF {word(2)} {cnt(7, 10)}\nF {word(1)} {cnt(1, 11)}\nF {word(3)} {cnt(5, 10)}\nS\nL 5 0",
     ["1", "1", "1", "1", "n=4 emb=0 cs=0 evm=" + str(10 + 10 + 11 + 10) + " se=4 sn=2", "sync 1/48 avg 2.0"])
case("no burst A yet: sync line says so", f"F {word(2)} 5\nL 5 0", ["1", "sync: no burst A"])
case("CC / TS / burst letter", f"F {word(6, cc=7, slot=0)} 1\nL 6 0\nF {word(3, cc=2, slot=1)} 1\nL 6 0",
     ["1", "CC 7 TS2 burst F", "1", "CC 2 TS1 burst C"])
case("idle after about a second without a burst", f"F {word(1)} 1\nT 100\nL 1 0\nT 1\nL 1 0",
     ["1", "voice bursts 1", "idle, bursts 1"])
case("a new burst makes it live again", f"F {word(1)} 1\nT 500\nF {word(2)} 1\nL 1 0", ["1", "1", "voice bursts 2"])
# window: counters halve at 1024 bursts, ratios survive
feed = "".join(f"F {word(1 + (i % 6), emb=1 if i % 4 == 0 else 0)} {i % 7:x}\n" for i in range(1030))
out = run(feed + "S\nL 2 0")
n = int(out[-2].split()[0][2:])
check(500 < n < 1024, f"window: 1030 bursts fed, counters halved at 1024 (n={n}); " + out[-1])
pct = int(out[-1].split()[2].rstrip("%"))
check(24 <= pct <= 26, f"window keeps the EMB error ratio (25% fed -> {pct}%)")
# review fix 12: no summary line may be wider than the 19-column screen (a wider line wraps in the stock renderer)
feed = "".join(f"F {word(1 + (i % 6), emb=1, cs=1, cc=15)} ff\n" for i in range(1023))
for tag, tail in (("live", ""), ("idle", "T 200\n")):
    out = run(feed + tail + "".join(f"L {k} 0\n" for k in range(1, 7)))[-6:]
    wide = [x for x in out if len(x) > 19]
    check(not wide, f"worst-case figures ({tag}): all 6 lines fit 19 columns (max {max(map(len, out))}) {wide}")
# key-2 one-key capture: lq_auto_next (armed -> recording at the first burst -> done when the call ends or the cap)
case("auto: armed with no burst keeps waiting", "A 1 10\nA 1 5999", ["1", "1"])
case("auto: armed with no call gives up after 60 s", "A 1 6000", ["3"])
case("auto: first voice burst starts recording", f"F {word(1)} 5\nA 1 20", ["1", "2"])
case("auto: recording continues while bursts arrive, ends 1 s after the last", f"F {word(1)} 5\nA 2 50\nT 100\nA 2 150\nT 1\nA 2 151", ["1", "2", "2", "3"])
case("auto: 120 s hard stop during a long call", f"F {word(1)} 5\nA 2 11999\nA 2 12000", ["1", "2", "3"])
case("auto: done stays done", "A 3 0", ["3"])
finish()
