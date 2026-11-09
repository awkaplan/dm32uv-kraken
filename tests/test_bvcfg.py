#!/usr/bin/env python3
"""Host test for the Burst Voice settings record (krk_bvcfg.c, docs/burst-voice.md 13).

  1. Defaults: a factory reset (00) and a vendor CPS write (FF) read as Fast, 320 ms, relay capture off, and the three option
     levels open with those marked.
  2. Each setter writes the 16-byte record at block 4 0xFC0 once (magic "BVC1", checksum seed 0x3c), touches no other byte of
     block 4, keeps the other two settings, and survives a "power cycle" (nothing is cached: every read goes to flash).
  3. An unchanged setting writes nothing; a corrupted record (checksum or an out-of-range field) reads as the defaults.
  4. A stored lead between two of the eight options marks the nearest.
  5. The record sits in the free span of block 4 and clear of the other two records of ours, per allocations.json.

Runs on the host with the host cc (no Docker), like test_band.py.
"""
import json

import hostcc
from hostlib import check as _check, finish

TU = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "krk.h"
unsigned char blk4[4096];
int writes;
static unsigned host_resolve(unsigned l) { return l == 4 ? 0x77 : 0; }
static void host_read(unsigned id, unsigned off, void *d, unsigned n) { memcpy(d, id == 0x77 ? blk4 + off : blk4, n); }
static void host_write(unsigned l, unsigned off, const void *s, unsigned n) { if (l == 4) { memcpy(blk4 + off, s, n); writes++; } }
#define flash_block_id_resolve_locked host_resolve
#define flash_block_read  host_read
#define flash_block_write host_write
const char *const krk_onoff_labels[2] = { "ON", "OFF" };
u8 krk_rec_csum(const void *rec, u8 seed) { const u8 *p = rec; u8 x = seed; int i; for (i = 0; i < 15; i++) x = (u8)((x << 1 | x >> 7) ^ p[i]); return x; }
int krk_nearest8(const u8 *v, unsigned x) { unsigned d, bd = 0xffff; int i, b = 0; for (i = 0; i < 8; i++) { d = x > v[i] ? x - v[i] : v[i] - x; if (d < bd) { bd = d; b = i; } } return b; }
/* the menu side is not run here (fixed RAM addresses); these only satisfy the linker */
void krk_list_cells(const char *const *l, unsigned n) { (void)l; (void)n; }
void krk_opt_enter(const struct krk_opt *o) { (void)o; }
void krk_opt_commit(const struct krk_opt *o, void (*r)(void)) { (void)o; (void)r; }
void krk_run_screen(const struct krk_screen *s, void (*r)(void)) { (void)s; (void)r; }
#include "krk_bvcfg.c"

static void show(void)
{
    u32 c = krk_bv_cfg();
    unsigned i, outside = 0;

    for (i = 0; i < sizeof blk4; i++)
        if ((i < BVC_OFF || i >= BVC_OFF + 16) && blk4[i] != blk4[0])
            outside = 1;
    printf("%u %u %u %d %d %d %d %d ", KRK_BVC_MODE(c), KRK_BVC_LEAD(c), KRK_BVC_RELAY(c),
           kid_opt[0].get(), kid_opt[1].get(), kid_opt[2].get(), writes, outside);
    for (i = 0; i < 16; i++)
        printf("%02x", blk4[BVC_OFF + i]);
    printf("\n");
}

int main(int argc, char **argv)
{
    memset(blk4, argv[1][0] == 'f' ? 0xff : 0x00, sizeof blk4);
    for (int i = 2; i < argc; i++) {            /* ops: m<idx> mode, l<idx> lead, r<idx> relay, x corrupt the csum,
                                                 * f<val> force the lead byte (csum fixed), . just print */
        char op = argv[i][0];
        int v = atoi(argv[i] + 1);
        if (op == 'm' || op == 'l' || op == 'r')
            kid_opt[op == 'm' ? 0 : op == 'l' ? 1 : 2].set(v);
        else if (op == 'x')
            blk4[BVC_OFF + 15] ^= 0x55;
        else if (op == 'f') {
            blk4[BVC_OFF + 5] = (u8)v;
            blk4[BVC_OFF + 15] = krk_rec_csum(blk4 + BVC_OFF, BVC_SEED);
        }
        show();
    }
    printf("%d %s\n", BVC_OFF, kid_labels[1]);
    return 0;
}
'''


def check(name, cond, detail=""):
    _check(cond, name + ("" if cond else "  " + str(detail)))


exe = hostcc.build([], driver_src=TU)


def run(*args):
    lines = hostcc.run(exe, *args).split("\n")[:-1]
    out = []
    for l in lines[:-1]:
        v = l.split()
        out.append(tuple(int(x) for x in v[:8]) + (bytes.fromhex(v[8]),))
    return out, int(lines[-1].split()[0])


def rec(mode, lead, relay):
    r = b"BVC1" + bytes([mode, lead, relay]) + bytes(8)
    x = 0x3C
    for byte in r:
        x = (((x << 1) | (x >> 7)) & 0xFF) ^ byte
    return r + bytes([x])


# 1. defaults
for fill in ("0", "f"):
    r, off = run(fill, ".")
    check(f"blank block ({fill}): Fast, 320 ms, relay off; Fast / 320 ms / OFF marked; nothing written",
          r[0][:7] == (1, 10, 0, 0, 4, 1, 0), r[0])

# 2. each setter, one write each, the others kept, nothing else touched
r, off = run("0", "m1", "l7", "r0", ".")
check("Mode = Robust: one write, the record is magic + mode 0 + lead 10 + relay 0 + checksum",
      r[0][:7] == (0, 10, 0, 1, 4, 1, 1) and r[0][8] == rec(0, 10, 0), r[0])
check("Key-up Lead = 960 ms: one more write, mode kept", r[1][:7] == (0, 30, 0, 1, 7, 1, 2) and r[1][8] == rec(0, 30, 0), r[1])
check("Relay Capture = ON: one more write, both others kept", r[2][:7] == (0, 30, 1, 1, 7, 0, 3) and r[2][8] == rec(0, 30, 1), r[2])
check("a read after the writes (a power cycle: nothing is cached) gives the same", r[3][:3] == (0, 30, 1), r[3])
check("no byte of block 4 outside 0xFC0-0xFCF was written", all(x[7] == 0 for x in r), [x[7] for x in r])
r, _ = run("f", "l5")
check("on a CPS-erased (FF) block the first write is a whole default record with the one change",
      r[0][8] == rec(1, 15, 0) and r[0][7] == 0, r[0])

# 3. no-op and corruption
r, _ = run("0", "m1", "m1", "l4", "r1")
check("unchanged settings write nothing (mode again, the default lead, relay already off)", [x[6] for x in r] == [1, 1, 1, 1],
      [x[6] for x in r])
r, _ = run("0", "m1", "x")
check("a corrupted checksum reads as the defaults", r[1][:3] == (1, 10, 0), r[1])
r, _ = run("0", "m1", "f31")
check("a lead past 960 ms (31) fails the range check: defaults", r[1][:3] == (1, 10, 0), r[1])

# 4. nearest
r, _ = run("0", "m1", "f12")
check("a stored lead of 12 (384 ms) marks 320 ms, the nearest of the eight", r[1][1] == 12 and r[1][4] == 4, r[1])

# 5. where it lives
alloc = json.loads((hostcc.ROOT / "allocations.json").read_text())
recs = {d["name"]: (d["block"], int(d["start"], 16), int(d["end_exclusive"], 16)) for d in alloc["datachip_records"]["records"]}
mine = recs.get("burst_voice_cfg")
check(f"allocations.json claims block 4 {off:#x}..+16 for it", mine == (4, off, off + 16), mine)
others = [(n, v) for n, v in recs.items() if n != "burst_voice_cfg" and v[0] == 4 and v[1] < off + 16 and off < v[2]]
check("no other datachip claim overlaps it", not others, others)
check("inside the block-4 span no stock code reaches (0xF10-0xFEF, docs/settings-field.md 3.3)", 0xF10 <= off and off + 16 <= 0xFF0, off)

finish("test_bvcfg: 0 failure(s)", "test_bvcfg: {n} failure(s)")
