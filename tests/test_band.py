#!/usr/bin/env python3
"""Host test for Freq Unlock (krk_band.c, docs/freq-unlock.md).

  1. The flash record (block 4, 0xFD0): default OFF on a factory reset (00) and a vendor CPS write (FF), ON survives a
     power cycle, OFF again, an unchanged setting writes nothing, a corrupted record reads OFF.
  2. The RX band table 0x38470: wide edges when ON (at boot and when switched), the stock edges back exactly when OFF,
     the calibration span [4..7] and [9] never touched.
  3. The TX bits of 0x3e31b, against a model of the stock check (model byte "other": 136-174 / 400-470): ON allows
     136-520 MHz with bit 4 = UHF PA from 280 MHz, refuses below 136 and above 520; OFF is the stock result; the other bits
     are kept.
  4. The hold: OK must be pressed fresh after the warning appears (the OK that chose ON does not count) and held 10 s;
     releasing early restarts it; Back or 60 s idle cancels; OFF and ON-again need no hold.

Runs on the host with the host cc (no Docker), like test_cwid.py.
"""
import hostcc
from hostlib import check as _check, finish

TU = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "krk.h"
/* stock calls and addresses -> host state */
unsigned char blk4[4096];
int writes;
static unsigned host_resolve(unsigned l) { return l == 4 ? 0x77 : 0; }
static void host_read(unsigned id, unsigned off, void *d, unsigned n) { memcpy(d, id == 0x77 ? blk4 + off : blk4, n); }
static void host_write(unsigned l, unsigned off, const void *s, unsigned n) { if (l == 4) { memcpy(blk4 + off, s, n); writes++; } }
u32 tbl[10];
u8 flags3;
u32 live_rx, live_tx, ticks;
const u32 lit[4] = { 40000000, 47000000, 17400000, 10800000 };
static void host_load(void)
{
    tbl[0] = 40000000; tbl[1] = 47000000; tbl[2] = 13600000; tbl[3] = 17400000;
    tbl[4] = 40000000; tbl[5] = 47000000; tbl[6] = 13600000; tbl[7] = 17400000;
    tbl[8] = 10800000; tbl[9] = 13600000;
}
/* The stock check's TX half for model byte "other", and its RX bit 3 from the table (decompile 0x0300d84c). */
static void host_check(u32 rx, u32 tx)
{
    if (!(rx >= tbl[8] && rx < tbl[9])) {
        if (rx >= tbl[0] && rx <= tbl[1]) flags3 |= 8;
        else if (rx >= tbl[2] && rx <= tbl[3]) flags3 &= ~8;
        if (tx >= 40000000 && tx < 47000000) flags3 = (flags3 & ~0x20) | 0x10;
        else if (tx >= 13600000 && tx < 17400000) flags3 &= ~0x30;
        else flags3 |= 0x20;
    }
}
#define flash_block_id_resolve_locked host_resolve
#define flash_block_read  host_read
#define flash_block_write host_write
#define stock_band_load   host_load
#define stock_band_check  host_check
#define BAND_TBL          tbl
#define FLAGS3            flags3
#define LIVE_TX           live_tx
#define LIVE_RX           live_rx
#define OS_TICKS          ticks
#define STOCK_UHF_LO      lit[0]
#define STOCK_UHF_HI      lit[1]
#define STOCK_VHF_HI      lit[2]
#define STOCK_AM_LO       lit[3]
#define KEYMSGQ_PTR       0u
static void *host_pend(u32 q, u32 t, short o, u16 *s, u32 *ts, u32 *e) { (void)q; (void)t; (void)o; (void)s; (void)ts; *e = 1; return 0; }
#define os_q_pend host_pend
/* the keypad, display and delay: a scripted keypad on a fake 100 Hz clock */
static const char *script;          /* one char per 50 ms: o OK down, b Back, . none */
static u32 keypark;
#undef display_fill_screen
#undef os_time_dly_ms
#undef KEYPAD_BLANK_REARM
#undef KEYPAD_BLANK_COUNTER
#undef KEYPAD_POLL_ENABLE
static void host_fill(u16 c) { (void)c; }
static void host_dly(int ms) { ticks += (u32)ms / 10; if (*script) script++; }
static u32 rearm, poll_en = 1; static u8 blank;
#define display_fill_screen host_fill
#define os_time_dly_ms host_dly
#define KEYPAD_BLANK_REARM (&rearm)
#define KEYPAD_BLANK_COUNTER (&blank)
#define KEYPAD_POLL_ENABLE (&poll_en)
u8 krk_key_scan(void) { char c = *script; keypark++; return c == 'o' ? SCAN_OK : c == 'b' ? SCAN_BACK : SCAN_NONE; }
void krk_line(int n, const char *s, u16 fg) { (void)fg; if (n == 9 && getenv("BAND_VERBOSE")) fprintf(stderr, "[%u] %s\n", ticks, s); }
void krk_popup_draw(int k, const char *a, const char *b) { (void)k; (void)a; (void)b; }
char *krk_catn(char *d, unsigned n, const char *s) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return d; }
char *krk_itoa(int v, char *b) { sprintf(b, "%d", v); return b; }
const char *const krk_onoff_labels[2] = { "ON", "OFF" };
u8 krk_rec_csum(const void *rec, u8 seed) { const u8 *p = rec; u8 x = seed; int i; for (i = 0; i < 15; i++) x = (u8)((x << 1 | x >> 7) ^ p[i]); return x; }
#include "krk_band.c"

static char *expand(const char *spec)  /* "o20 .5 o200" -> 20 x 'o', 5 x '.', 200 x 'o' */
{
    static char buf[1 << 16];
    char *p = buf, c;
    int n;

    while (*spec) {
        c = *spec++;
        n = (int)strtol(spec, (char **)&spec, 10);
        while (n-- > 0 && p < buf + sizeof buf - 1)
            *p++ = c;
        while (*spec == ' ')
            spec++;
    }
    *p = 0;
    return buf;
}

int main(int argc, char **argv)
{
    if (!strcmp(argv[1], "rec")) {            /* argv[2]: fill; then ops: 1 on, 0 off, b boot; prints on/table/writes after each */
        memset(blk4, argv[2][0] == 'f' ? 0xff : 0x00, sizeof blk4);
        band_on = 0x12345678;                  /* power-up noise */
        for (int i = 3; i < argc; i++) {
            char op = argv[i][0];
            if (op == 'b') { band_on = rand(); krk_hook_band_load(); }
            else if (op == 'x') blk4[0xFD0 + 4] ^= 1;   /* corrupt the record */
            else band_set_on(op == '1');
            printf("%d %d", krk_band_on(), writes);
            for (int k = 0; k < 10; k++) printf(" %u", tbl[k]);
            printf("\n");
        }
    } else if (!strcmp(argv[1], "tx")) {      /* argv[2] on/off, argv[3] tx in 10 Hz, argv[4] initial flags3 (hex) */
        host_load();
        band_on = argv[2][0] == '1' ? B_ON_MAGIC : 0;
        table_apply(krk_band_on());
        flags3 = (u8)strtoul(argv[4], 0, 16);
        krk_hook_band_check(14600000, (u32)strtoul(argv[3], 0, 10));
        printf("%02x\n", flags3);
    } else if (!strcmp(argv[1], "hold")) {    /* argv[2] script; prints result and the tick it ended at */
        script = expand(argv[2]);
        ticks = 0;
        int ok = hold_ok();
        printf("%d %u %u\n", ok, ticks, rearm);
    } else if (!strcmp(argv[1], "confirm")) { /* argv[2] idx, argv[3] already on, argv[4] script */
        script = expand(argv[4]);
        band_on = argv[3][0] == '1' ? B_ON_MAGIC : 0;
        keypark = 0;
        int r = sw_confirm(atoi(argv[2]));
        printf("%d %u\n", r, keypark);
    }
    return 0;
}
'''

def check(name, cond, detail=""):
    _check(cond, name + ("" if cond else "  " + detail))


exe = hostcc.build([], driver_src=TU)

def run(*args):
    return hostcc.run(exe, *args).split("\n")[:-1]

STOCK = [40000000, 47000000, 13600000, 17400000, 40000000, 47000000, 13600000, 17400000, 10800000, 13600000]
WIDE = [28000000, 52000000, 2300000, 27999999, 40000000, 47000000, 13600000, 17400000, 2300000, 13600000]

def rows(lines):
    out = []
    for l in lines:
        v = [int(x) for x in l.split()]
        out.append((v[0], v[1], v[2:]))
    return out

# 1-2. record and table
for fill in ("0", "f"):
    r = rows(run("rec", fill, "b"))
    check(f"blank record ({fill}) boots OFF with the stock table", r[0] == (0, 0, STOCK), str(r[0]))
r = rows(run("rec", "0", "b", "1", "b", "1", "0", "b", "0"))
check("ON: wide table at once, one write", r[1] == (1, 1, WIDE), str(r[1]))
check("ON survives a power cycle", r[2] == (1, 1, WIDE), str(r[2]))
check("ON again: no flash write", r[3][1] == 1, str(r[3]))
check("OFF: stock table back exactly, one more write", r[4] == (0, 2, STOCK), str(r[4]))
check("OFF survives a power cycle", r[5] == (0, 2, STOCK), str(r[5]))
check("OFF again: no flash write", r[6][1] == 2, str(r[6]))
r = rows(run("rec", "0", "1", "x", "b"))
check("a corrupted record boots OFF", r[2][0] == 0 and r[2][2] == STOCK, str(r[2]))

# 3. TX bits (bit 4 UHF PA, bit 5 not allowed); start from bits 0-3/6/7 set to see they are kept
def tx(on, mhz, init="cf"):
    return int(run("tx", on, str(round(mhz * 100000)), init)[0], 16)

for mhz, ok, uhf in [(136.0, 1, 0), (146.52, 1, 0), (174.0, 1, 0), (222.0, 1, 0), (279.99999, 1, 0), (280.0, 1, 1),
                     (300.0, 1, 1), (446.0, 1, 1), (520.0, 1, 1), (135.99999, 0, None), (100.0, 0, None), (27.185, 0, None),
                     (520.00001, 0, None), (900.0, 0, None)]:
    f = tx("1", mhz)
    good = (f & 0xc7) == 0xc7 and not f & 0x08 and bool(f & 0x20) == (not ok) and (uhf is None or bool(f & 0x10) == bool(uhf))
    check(f"ON  TX {mhz} MHz: {'allowed' if ok else 'refused'}{'' if uhf is None else ', UHF PA' if uhf else ', VHF PA'}", good, hex(f))
for mhz, ok in [(146.52, 1), (446.0, 1), (136.0, 1), (222.0, 0), (300.0, 0), (480.0, 0), (100.0, 0)]:
    f = tx("0", mhz)
    check(f"OFF TX {mhz} MHz: the stock result ({'allowed' if ok else 'refused'})", bool(f & 0x20) == (not ok), hex(f))

# 4. the hold (one script char = 50 ms = 5 ticks)
def hold(s):
    ok, t, rearm = (int(x) for x in run("hold", s)[0].split())
    return ok, t, rearm

ok, t, rearm = hold(". 5 o 250")
check("release, then hold 12.5 s: accepted", ok == 1 and rearm == 1, (ok, t))
check("  ... not before 10 s of holding", t >= 25 + 1000, t)
ok, t, _ = hold("o 300 . 5 o 250")
check("the OK that chose ON (held 15 s) does not count; a fresh 10 s hold does", ok == 1 and t >= 1525 + 1000, (ok, t))
ok, t, _ = hold(". 2 o 190 . 2 o 250")
check("released at 9.5 s: restarts; the second full hold counts", ok == 1 and t >= 960 + 1000, (ok, t))
ok, t, _ = hold(". 2 o 100 b 1")
check("Back cancels", ok == 0, (ok, t))
ok, t, _ = hold(". 1300")
check("60 s idle cancels", ok == 0 and 6000 <= t < 6100, (ok, t))
ok, t, _ = hold("o 2000")
check("OK never released since ON was chosen: cancelled at 60 s", ok == 0, (ok, t))

r, scans = (int(x) for x in run("confirm", "1", "0", ". 10")[0].split())
check("OFF needs no hold", r == 1 and scans == 0, (r, scans))
r, scans = (int(x) for x in run("confirm", "0", "1", ". 10")[0].split())
check("ON while already ON needs no hold", r == 1 and scans == 0, (r, scans))
r, scans = (int(x) for x in run("confirm", "0", "0", ". 2 o 250")[0].split())
check("ON from OFF holds, then confirms", r == 1 and scans > 200, (r, scans))
r, scans = (int(x) for x in run("confirm", "0", "0", ". 2 b 1")[0].split())
check("ON from OFF, Back: refused", r == 0, (r, scans))

finish("test_band: 0 failure(s)", "test_band: {n} failure(s)")
