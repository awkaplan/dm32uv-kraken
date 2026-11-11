#!/usr/bin/env python3
"""Host test for the CW ID (krk_cwid.c) and its config byte in the callsign record (krk_callsign.c).

  1. krk_morse_units: every letter and digit, and real callsigns, decoded back with an independent
     Morse table; dit 1 unit, dah 3, 1 off between elements, 3 between characters; the SSID is not
     sent; bad input and overflow refused.
  2. krk_cw_fill: the audio the radio would play - lead-in silence, element lengths at 20 WPM, the tone
     frequency, the level, and no key clicks (bounded rise and fall).
  3. The CW ID setting stored in the callsign record's spare byte: default off, persists across a power
     cycle, survives a callsign edit, keeps the APRS path, and a CPS write / factory reset turns it off.

Runs on the host with the host cc (no Docker), like test_callsign.py.
"""
import math, pathlib, re, tempfile
import hostcc
from hostcc import UI
from hostlib import check, finish

SINTAB = re.search(r"krk_afsk_sintab\[64\] = \{([^}]*)\}", (UI / "krk_afsk.c").read_text()).group(1)

CALLSIGN_TU = r'''
#include <string.h>
#include <stdio.h>
#define flash_block_id_resolve_locked host_resolve
#define flash_block_read host_read
#define flash_block_write host_write
unsigned char blk4[4096];
int writes;
static unsigned host_resolve(unsigned l) { return l == 4 ? 0x77 : 0; }
static void host_read(unsigned id, unsigned off, void *d, unsigned n) { memcpy(d, id == 0x77 ? blk4 + off : blk4, n); }
static void host_write(unsigned l, unsigned off, const void *s, unsigned n) { if (l == 4) { memcpy(blk4 + off, s, n); writes++; } }
#include "krk_callsign.c"
void cs_power_cycle(void) { cache_state = 0; }
void cs_key(int sc) { call_tick((u8)sc); call_tick(SCAN_NONE); }
void cs_pause(void) { for (int i = 0; i < 12; i++) call_tick(SCAN_NONE); }
void cs_enter(void) { call_enter(); }
'''

CWID_TU = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "krk_cwid.c"
const short krk_afsk_sintab[64] = {''' + SINTAB + r'''};
volatile u32 krk_screen_owner;
int krk_channel_analog(void) { return 1; }
int krk_fm_send(int (*fill)(void *, volatile u8 *), void *ctx, const char **why) { (void)fill; (void)ctx; *why = "stub"; return -1; }
void krk_line(int n, const char *s, u16 fg) { (void)n; (void)s; (void)fg; }
void krk_popup_draw(int k, const char *a, const char *b) { (void)k; (void)a; (void)b; }
char *krk_cat(char *d, const char *s) { return strcat(d, s); }
char *krk_catn(char *d, unsigned n, const char *s) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return d; }
char *krk_itoa(int v, char *b) { sprintf(b, "%d", v); return b; }
extern unsigned char blk4[4096];
extern int writes;
void cs_power_cycle(void); void cs_key(int); void cs_pause(void); void cs_enter(void);

int main(int argc, char **argv) {
    if (!strcmp(argv[1], "morse")) {
        u8 out[KRK_CW_UNITS / 8 + 8];
        int max = atoi(argv[3]), n = krk_morse_units(argv[2], out, max);
        printf("%d ", n);
        for (int k = 0; k < (n > 0 ? n : 0); k++) putchar('0' + ((out[k >> 3] >> (k & 7)) & 1));
        int stray = 0;                                   /* bits past n inside the cleared area */
        for (int k = n > 0 ? n : 0; k < max; k++) stray |= (out[k >> 3] >> (k & 7)) & 1;
        printf(" %d\n", stray);
    } else if (!strcmp(argv[1], "fill")) {
        struct krk_cw c;
        u8 h[512];
        int n = krk_morse_units(argv[2], c.units, KRK_CW_UNITS - 2), halves = 0;
        krk_cw_start(&c, n + 2, 8);
        for (;;) {
            int done = krk_cw_fill(&c, h);
            for (int i = 0; i < 256; i++) printf("%d\n", (short)(h[2 * i] << 8 | h[2 * i + 1]));
            if (done || ++halves > 2000) break;
        }
    } else if (!strcmp(argv[1], "cfg")) {
        /* argv[2]: initial fill of block 4 ('0' factory reset, 'f' CPS write) */
        memset(blk4, argv[2][0] == 'f' ? 0xff : 0x00, sizeof blk4);
        cs_power_cycle();
        printf("blank %u\n", krk_cwid_cfg());
        printf("set %d\n", krk_cwid_cfg_set(0x80 | 15));
        cs_power_cycle();
        printf("after_cycle %u\n", krk_cwid_cfg());
        krk_aprs_cfg_set_path(2);
        cs_enter();                                      /* type N0C and save */
        cs_key(SCAN_1 + 5); cs_key(SCAN_1 + 5); cs_key(SCAN_0); cs_key(SCAN_1 + 1); cs_key(SCAN_1 + 1); cs_key(SCAN_1 + 1);
        cs_key(SCAN_OK);
        cs_power_cycle();
        char call[16]; krk_call_get(call);
        printf("after_edit %u %s %u\n", krk_cwid_cfg(), call, krk_aprs_cfg_path());
        printf("set_off %d\n", krk_cwid_cfg_set(15));
        cs_power_cycle();
        printf("off %u %s\n", krk_cwid_cfg(), (krk_call_get(call), call));
        int outside = 0; for (int i = 0; i < 4096; i++) if ((i < 0xFE0 || i >= 0xFF0) && blk4[i] != (argv[2][0] == 'f' ? 0xff : 0)) outside = 1;
        printf("outside %d\n", outside);
    }
    return 0;
}
'''

d = pathlib.Path(tempfile.mkdtemp())
(d / "cs.c").write_text(CALLSIGN_TU)
(d / "cw.c").write_text(CWID_TU)
exe = hostcc.build([d / "cs.c", d / "cw.c", hostcc.UI / "krk_tapedit.c"])    # the callsign editor types through krk_tapedit.c


def run(*args):
    return hostcc.run(exe, *args)


MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
         "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
         "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
         "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
         "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----."}
DECODE = {v: k for k, v in MORSE.items()}


def decode(bits):
    """Units -> text, or None if any run is not a legal dit/dah/gap length."""
    runs = [(m.group()[0], len(m.group())) for m in re.finditer(r"1+|0+", bits)]
    text, sym = "", ""
    for v, n in runs:
        if v == "1":
            if n not in (1, 3):
                return None
            sym += "." if n == 1 else "-"
        elif n == 3:
            text += DECODE.get(sym, "?"); sym = ""
        elif n != 1:
            return None
    return text + DECODE.get(sym, "?")


def units(s, mx=158):
    n, bits, stray = run("morse", s, str(mx)).split(" ")
    return int(n), bits, int(stray)


# ---- 1. encoder --------------------------------------------------------------------------------
bad_chars = []
for ch in sorted(MORSE):
    n, bits, stray = units(ch)
    want = sum(1 if e == "." else 3 for e in MORSE[ch]) + len(MORSE[ch]) - 1
    if not (decode(bits) == ch and n == want and stray == 0):
        bad_chars.append(f"{ch}: units {bits!r} decode {decode(bits)!r} (n {n}, want {want})")
check(not bad_chars, f"all 36 letters and digits encode to their own Morse and decode back {bad_chars or ''}")

for call in ("N0CALL", "NOCALL", "W1AW", "TEST12", "Q9Q"):
    n, bits, stray = units(call)
    want = sum(sum(1 if e == "." else 3 for e in MORSE[c]) + len(MORSE[c]) - 1 for c in call) + 3 * (len(call) - 1)
    check(decode(bits) == call and n == want and stray == 0 and bits[0] == "1" and bits[-1] == "1",
           f"{call}: {n} units ({n * 60 / 1000:.2f} s at 20 WPM), decodes to {decode(bits)!r}, gaps 1/3 units")
n, bits, _ = units("N0CALL-7")
check(decode(bits) == "N0CALL", "the SSID is not sent: N0CALL-7 keys as N0CALL")
n, _, _ = units("0" * 6)
check(n == 6 * 19 + 5 * 3 and n <= 158, f"worst-case 6-character call (000000) is {n} units: fits KRK_CW_UNITS")
for bad, why in (("", "empty"), ("AB/C", "a character Morse cannot send here"), ("ab", "lower case")):
    check(units(bad)[0] == -1, f"refused: {why} ({bad!r})")
check(units("N0CALL", 40)[0] == -1, "refused: more units than the buffer holds")

# ---- 2. generator ------------------------------------------------------------------------------
UNIT = 480
for call in ("E", "N0CALL"):
    x = [int(v) for v in run("fill", call).split()]
    n, bits, _ = units(call)
    total = (8 + n + 2) * UNIT
    lead_silent = all(v == 0 for v in x[:8 * UNIT])
    on = [i for i, v in enumerate(x) if v]
    first, last = on[0], on[-1]
    peak = max(abs(v) for v in x)
    jump = max(abs(x[i + 1] - x[i]) for i in range(len(x) - 1))
    check(len(x) >= total and len(x) - total < 512 and lead_silent and all(v == 0 for v in x[total:]),
           f"{call}: {len(x)} samples ({len(x) / 8000:.2f} s) for {total} wanted; 480 ms lead-in silent; silent after the last unit")
    # element timing: the sample-level on/off pattern, read at each unit's middle, is the unit pattern
    mids = "".join("1" if x[(8 + k) * UNIT + UNIT // 2] or x[(8 + k) * UNIT + UNIT // 2 + 1] else "0" for k in range(n))
    check(mids == bits, f"{call}: tone present exactly in the keyed units (unit = 60 ms)")
    # level and clicks: peak = the beacon level, sample-to-sample step no bigger than the steady tone's.
    # The beacon's 64-entry sine table, sampled 10 times a cycle, never lands exactly on the crest and
    # steps in ~3% stairs: hence 7500 and 1.10. A click would be a jump of the order of the full 8000.
    steady = 8000 * 2 * math.sin(math.pi * 800 / 8000)
    check(7500 <= peak <= 8000 and jump <= steady * 1.10,
           f"{call}: peak {peak} (beacon level 8000), largest step {jump} (steady 800 Hz tone {steady:.0f}): no key clicks")
    # rise: the first 32 samples of the first element grow from ~0
    check(abs(x[first]) < 600 and max(abs(v) for v in x[first:first + 8]) < 2500,
           f"{call}: first element starts at {x[first]} and ramps up (4 ms)")
    # frequency: zero crossings inside the first element's steady part
    seg = x[first + 40:first + UNIT - 40]
    zc = sum(1 for i in range(len(seg) - 1) if (seg[i] < 0) != (seg[i + 1] < 0))
    f = zc / 2 / (len(seg) / 8000)
    check(abs(f - 800) < 30, f"{call}: tone {f:.0f} Hz (want 800)")

# ---- 3. config byte in the callsign record ------------------------------------------------------
for fill, label in (("0", "factory-reset (all 00) field"), ("f", "CPS-written (all FF) field")):
    out = dict(l.split(" ", 1) for l in run("cfg", fill).strip().splitlines())
    check(out["blank"] == "0", f"{label}: CW ID reads off (0)")
    check(out["set"] == "1" and out["after_cycle"] == str(0x80 | 15), f"{label}: On + 15 min stored, read back after a power cycle ({out['after_cycle']})")
    cfg, call, path = out["after_edit"].split()
    check(cfg == str(0x80 | 15) and call == "N0C" and path == "2", f"a callsign edit keeps the CW ID setting ({cfg}) and the APRS path ({path}); call {call}")
    check(out["off"].split() == ["15", "N0C"], f"switching off keeps the interval and the callsign ({out['off']})")
    check(out["outside"] == "0", "nothing outside block 4 0xFE0-0xFEF was written")

finish()
