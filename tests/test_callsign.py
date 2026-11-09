#!/usr/bin/env python3
"""Host test for krk_callsign.c's editor and record: drive call_tick() with key scan codes and check
what krk_call_get() returns (what the beacon transmits). Run inside the dm32uv-re Docker image."""
import hostcc
from hostlib import check, finish
DRV = r'''
#include <stdio.h>
#include <string.h>
#include <string.h>
#define flash_block_id_resolve_locked host_resolve
#define flash_block_read host_read
#define flash_block_write host_write
static unsigned char blk4[4096];
static int writes;
static unsigned host_resolve(unsigned l) { return l == 4 ? 0x77 : 0; }
static void host_read(unsigned id, unsigned off, void *d, unsigned n) { memcpy(d, id == 0x77 ? blk4 + off : blk4, n); }
static void host_write(unsigned l, unsigned off, const void *s, unsigned n) { if (l == 4) { memcpy(blk4 + off, s, n); writes++; } }
#include <string.h>
#include "krk_callsign.c"
void krk_line(int n, const char *s, u16 fg) { (void)n; (void)s; (void)fg; }
char *krk_cat(char *d, const char *s) { return strcat(d, s); }
char *krk_catn(char *d, unsigned n, const char *s) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return d; }
char *krk_itoa(int v, char *b) { sprintf(b, "%d", v); return b; }
static void key(int sc) { call_tick((u8)sc); call_tick(SCAN_NONE); }      /* press, release */
static void pause(void) { for (int i = 0; i < 12; i++) call_tick(SCAN_NONE); }
int main(int argc, char **argv) {
    char out[32];
    const char *script = argv[1];
    if (argc > 2) memset(blk4, argv[2][0] == 'f' ? 0xFF : 0x00, sizeof blk4);   /* CPS write / factory reset */
    call_enter();
    for (const char *p = script; *p; p++) {
        switch (*p) {
        case '.': pause(); break;
        case '*': key(SCAN_STAR); break;
        case '#': key(SCAN_HASH); break;
        case 'K': key(SCAN_OK); break;
        case 'U': key(SCAN_UP); break;
        case 'D': key(SCAN_DOWN); break;
        default:  key(*p == '0' ? SCAN_0 : SCAN_1 + (*p - '1')); break;
        }
    }
    int ok = krk_call_get(out);
    /* power cycle: drop the RAM cache, read the field back from "flash" */
    cache_state = 0;
    char out2[32]; int ok2 = krk_call_get(out2);
    int outside = 0; for (int i = 0; i < 4096; i++) if ((i < 0xFE0 || i >= 0xFF0) && blk4[i]) outside = 1;
    printf("%d|%s|%s|%d|%s|%d|%d\n", ok, ok ? out : "", E.status, ok2, ok2 ? out2 : "", writes, outside);
    return 0;
}
'''
exe = hostcc.build([], driver_src=DRV)
def case(script, want_ok, want_call, why, fill=None):
    out = hostcc.run(exe, script, *([fill] if fill else [])).strip().split("|")
    good = (out[0] == str(want_ok) and out[1] == want_call and out[3] == out[0] and out[4] == out[1]
            and (fill == "f" or out[6] == "0") and int(out[5]) <= 1)
    check(good, f"{why}: script {script!r} -> ok={out[0]} call={out[1]!r} status={out[2]!r}; after power cycle ok={out[3]} {out[4]!r}; flash writes {out[5]}; bytes outside 0xFE0-0xFEF touched: {out[6]}")
# N0CALL: 6,6=N ; 0 ; 2,2,2=C ; pause ; 2=A ; 5,5,5=L ; pause ; 5,5,5=L ; then SSID 7, save
case("660222.2555.555#7K", 1, "N0CALL-7", "multi-tap N0CALL, SSID 7, saved")
case("660222.2555.555K", 1, "N0CALL", "no SSID")
case("660222K", 1, "N0C", "3 characters with a letter and a digit is accepted")
case("2222", 0, "", "nothing saved without OK")
case("22K", 0, "", "B alone rejected (needs a letter and a digit, 3+ chars)")
case("660222.2555.555*.555K", 1, "N0CALL", "backspace then retype")
case("660222.2555.555#99K", 1, "N0CALL-9", "SSID 99 is clamped to the last valid digit")
case("660222.2555.555#15K", 1, "N0CALL-15", "SSID 15")
case("660222.2555.555.6K", 1, "N0CALL", "a 7th character is refused")
case("U" * 3 + ".1.1K", 1, "C11", "Up cycles letters, digits typed")
case("660222KK", 1, "N0C", "OK twice: the unchanged second save does not write again")
case("", 0, "", "field all-FF (after a vendor CPS write) reads as not set", fill="f")
case("", 0, "", "field all-00 (after a factory reset) reads as not set", fill="0")
case("660222K", 1, "N0C", "saving over an all-FF field works", fill="f")
finish()
