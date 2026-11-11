#!/usr/bin/env python3
"""Host test for F8a, the analog APRS beacon text (krk_aprs_text.c, krk_aprs_trec.c, krk_tapedit.c; GitHub issue #3).

  1. Typing: the editor screen driven by key scan codes through the shared keypad entry (multi-tap, the key 0 and 1 sets,
     '#' case, Up/Down, '*', the 43-character cap), as the radio's keys would.
  2. The record: OK writes 48 bytes at block 4 0xF90 once (magic "BXT1", checksum seed 0x69), touches no other byte, survives a
     "power cycle" (nothing is cached); OK with the text unchanged writes nothing; 00 (factory reset), FF (vendor CPS write) and
     a damaged record read as no text; an empty text saves as "use the default".
  3. The packet: krk_aprs_tx.c's own build_frame (compiled here with stubbed radio calls) puts the text after the position
     ("!lat/lon[" + text) or in the no-fix status (">" + text + " no GPS fix"), and the default "DM-32UV Kraken" without a text.
     The frame decodes as AX.25 (FCS checked), and the longest text in the characters that need the most bits fits the sender's
     1500-bit cap; no character the keypad types adds an HDLC stuffing bit, and none is '|' or '~'.
  4. The record sits in the free span of block 4, clear of the other records of ours, per allocations.json.

Runs on the host with the host cc (no Docker), like test_callsign.py.
"""
import json

import hostcc
from hostlib import check, finish

DRV = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define flash_block_id_resolve_locked host_resolve
#define flash_block_read  host_read
#define flash_block_write host_write
unsigned char blk4[4096];
int writes;
static unsigned host_resolve(unsigned l) { return l == 4 ? 0x77 : 0; }
static void host_read(unsigned id, unsigned off, void *d, unsigned n) { memcpy(d, id == 0x77 ? blk4 + off : blk4, n); }
static void host_write(unsigned l, unsigned off, const void *s, unsigned n) { if (l == 4) { memcpy(blk4 + off, s, n); writes++; } }
#include "krk_aprs_trec.c"
#undef E
#include "krk_aprs_text.c"
#undef E
/* krk_aprs_tx.c's frame builder, with its radio calls stubbed (only build_frame runs) */
#include "krk_aprs_tx.c"
const short krk_afsk_sintab[64];
volatile u32 krk_screen_owner;
int fix_on;
int krk_gps_get(struct krk_gps_fix *f)
{
    if (!fix_on) return 0;
    memset(f, 0, sizeof *f);
    strcpy(f->lat_s, "4142.8500"); strcpy(f->lon_s, "07243.6000"); f->ns = 'N'; f->ew = 'W';
    return 1;
}
int krk_call_get(char *o) { strcpy(o, "N0CALL-9"); return 1; }
unsigned krk_aprs_cfg_path(void) { return 0; }
int krk_channel_analog(void) { return 1; }
int krk_channel_on_selected(void) { return 1; }
int krk_channel_flags(void) { return 0; }
int krk_aprs_chan_send(const char **why, const char **kind, int automatic) { (void)automatic; return krk_aprs_tx_send(why, kind); }   /* F8b off */
int krk_aprs_chan_keep(void) { return 1; }
int krk_ui_in_menu(void) { return 0; }
void krk_real_time(u32 *last, u8 *rsec, u32 *rtick, u32 now) { (void)last; (void)rsec; (void)rtick; (void)now; }   /* #7: unused by build_frame */
void krk_mon_yield(void) {}
char lines[10][64];
void krk_line(int n, const char *s, u16 fg) { (void)fg; snprintf(lines[n], 20, "%s", s); }
char *krk_cat(char *d, const char *s) { size_t l = strlen(d); if (l + 1 < KRK_LINE_BUF) strncat(d, s, KRK_LINE_BUF - l - 1); return d; }
char *krk_catn(char *d, unsigned n, const char *s) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return d; }

static void key(int sc) { text_tick((u8)sc); text_tick(SCAN_NONE); }
static void pause(void) { for (int i = 0; i < 12; i++) text_tick(SCAN_NONE); }

int main(int argc, char **argv)
{
    const char *why = "", *kind = "";
    int nbits, i, outside = 0;

    memset(blk4, argv[1][0] == 'f' ? 0xff : 0x00, sizeof blk4);
    if (argv[1][0] == 'r') {                     /* a raw record: hex of 48 bytes after the 'r' */
        for (i = 0; i < 48; i++) { unsigned v; sscanf(argv[1] + 1 + 2 * i, "%2x", &v); blk4[0xF90 + i] = (u8)v; }
    }
    fix_on = argv[2][0] == '1';
    for (int a = 3; a < argc; a++) {
        const char *p = argv[a];
        text_enter();
        for (; *p; p++) {
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
    }
    nbits = build_frame(&why, &kind);
    for (i = 0; i < 4096; i++)
        if ((i < 0xF90 || i >= 0xFC0) && blk4[i] != blk4[0]) outside = 1;
    printf("%d|%d|%s|%s|%s|", writes, outside, kind, lines[1], lines[5]);
    for (i = 0; i < 48; i++) printf("%02x", blk4[0xF90 + i]);
    printf("|%d|", nbits);
    for (i = 0; i < (int)sizeof B.frame; i++) printf("%02x", B.frame[i]);
    printf("|%s%s%s\n", lines[1], lines[2], lines[3]);
    return 0;
}
'''

exe = hostcc.build([hostcc.UI / "krk_tapedit.c", hostcc.UI / "krk_ax25.c"], driver_src=DRV)


def run(fill, fix, *scripts):
    v = hostcc.run(exe, fill, "1" if fix else "0", *scripts).rstrip("\n").split("|")
    return {"writes": int(v[0]), "outside": int(v[1]), "kind": v[2], "line1": v[3], "status": v[4], "rec": bytes.fromhex(v[5]),
            "nbits": int(v[6]), "frame": bytes.fromhex(v[7]), "shown": v[8]}


def crc16_x25(b):
    c = 0xFFFF
    for x in b:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    return c ^ 0xFFFF


def info_of(frame):
    """The AX.25 UI frame's info field (address block ends at the byte with bit 0 set; then control 03, PID F0), FCS checked."""
    i = 0
    while not frame[i] & 1:
        i += 1
    i += 1
    assert frame[i:i + 2] == b"\x03\xf0", frame[i:i + 2]
    end = len(frame.rstrip(b"\0"))
    for n in range(end, i + 2, -1):            # the frame length is not printed: find the FCS that matches
        if crc16_x25(frame[:n - 2]) == frame[n - 2] | frame[n - 1] << 8:
            return frame[i + 2:n - 2].decode()
    raise AssertionError("no valid FCS")


def rec(text):
    r = b"BXT1" + text.encode().ljust(43, b"\0")
    x = 0x69
    for byte in r:
        x = (((x << 1) | (x >> 7)) & 0xFF) ^ byte
    return r + bytes([x])


# ---- 1. typing ---------------------------------------------------------------------------------------------------------
# "QRV 146.52": 77 = Q, 777 = R, 888 = V, 0 = space, 1 x9 = 1 (after . - / , : + ( )), 4444 = 4 (after G H I), 6666 = 6,
# 1 = '.', 5555 = 5, 2222 = 2
QRV = "77.777.888.0.111111111.4444.6666.1.5555.2222K"
r = run("0", False, QRV)
check(r["writes"] == 1 and r["rec"] == rec("QRV 146.52") and r["status"] == "Saved" and r["outside"] == 0,
      f"multi-tap 'QRV 146.52' (0 = space, 1 = '.', digits after the letters) saved once: {r['rec'][4:15]!r}, status {r['status']!r}, writes {r['writes']}")
r = run("0", False, "#44.666.66K")
check(r["rec"] == rec("hon"), f"'#' types lower case: {r['rec'][4:8]!r}")
r = run("0", False, "#44.#666K")
check(r["rec"] == rec("hO"), f"'#' again: back to upper case: {r['rec'][4:8]!r}")
r = run("0", False, "1.11.111.1111.11111.111111.1111111.11111111.111111111.K")
check(r["rec"] == rec(".-/,:+()1"), f"key 1 steps . - / , : + ( ) 1: {r['rec'][4:14]!r}")
r = run("0", False, "1111111111K")
check(r["rec"] == rec("."), f"key 1 pressed 10 times wraps to '.': {r['rec'][4:6]!r}")
r = run("0", False, "00.0K")
check(r["rec"] == rec("0 "), f"key 0: space, then 0: {r['rec'][4:7]!r}")
r = run("0", False, "UUU.D2K")
check(r["rec"] == rec("BA"), f"Up/Down step the last character (A B C, down to B), then a key appends: {r['rec'][4:7]!r}")
r = run("0", False, "22.33*4K")
check(r["rec"] == rec("BG"), f"'*' deletes the last character: {r['rec'][4:7]!r}")
r = run("0", False, "99999.U.D.D.K")
check(r["rec"] == rec("8"), f"Up from 9 (the last of A-Z 0-9) wraps to A, Down back to 9, 8: {r['rec'][4:7]!r}")
r = run("0", False, "0.UK")
check(r["rec"] == rec("A"), f"Up on a space (outside A-Z 0-9) gives A: {r['rec'][4:7]!r}")
r = run("0", False, "2." * 50 + "K")
check(r["rec"] == rec("A" * 43) and r["shown"] == "A" * 43, f"capped at 43 characters, all shown on three lines: {len(r['shown'])} shown")
r = run("0", False, "2." * 5)
check(r["line1"] == "AAAAA_" and r["writes"] == 0, f"the cursor '_' follows the text, nothing saved without OK: {r['line1']!r}")
r = run("0", False, "2." * 19)
check(r["shown"] == "A" * 19 + "_", f"at 19 characters the cursor wraps to line 2: {r['shown']!r}")

# ---- 2. the record -----------------------------------------------------------------------------------------------------
for fill, name in (("0", "00 (factory reset)"), ("f", "FF (vendor CPS write)")):
    r = run(fill, True)
    check(r["writes"] == 0 and info_of(r["frame"]).endswith("[DM-32UV Kraken"), f"{name}: no text, the beacon sends the default comment")
    r = run(fill, True, "K")
    check(r["writes"] == 0, f"{name}: OK on an empty text writes nothing (empty and not set are both the default)")
    r = run(fill, True, "222K")
    check(r["writes"] == 1 and r["rec"] == rec("C") and (fill == "f" or r["outside"] == 0), f"{name}: saving over it works")
r = run("r" + rec("HELLO").hex(), True)
check(r["writes"] == 0 and info_of(r["frame"]).endswith("[HELLO") and r["line1"] == "", f"a saved text is read back after a power cycle (nothing cached)")
r = run("r" + rec("HELLO").hex(), True, "")
check(r["line1"] == "HELLO_", f"the editor opens with the saved text: {r['line1']!r}")
r = run("r" + rec("HELLO").hex(), True, "K")
check(r["writes"] == 0 and r["status"] == "Saved", "OK with the text unchanged: 'Saved', no flash write")
r = run("r" + rec("HELLO").hex(), True, "*****K")
check(r["writes"] == 1 and r["rec"] == rec("") and info_of(r["frame"]).endswith("[DM-32UV Kraken"),
      "deleting the whole text and saving: an empty record, the beacon is back to the default")
bad = bytearray(rec("HELLO"))
bad[47] ^= 0x55
r = run("r" + bad.hex(), True)
check(info_of(r["frame"]).endswith("[DM-32UV Kraken") and r["line1"] == "", "a damaged record (checksum) reads as no text")
bad = bytearray(rec("HELLO"))
bad[0] = ord("X")
r = run("r" + bad.hex(), True)
check(info_of(r["frame"]).endswith("[DM-32UV Kraken"), "a record with a wrong magic reads as no text")
r = run("0", True, "2." * 43 + "K", "*3.K")
check(r["writes"] == 2 and r["rec"] == rec("A" * 42 + "D"), "a second editor session edits the saved 43-character text (delete one, type D)")
r = run("0", True, "2." * 43 + "K", "*2.K")
check(r["writes"] == 1, "... and retyping the same text writes nothing")

# ---- 3. the packet -----------------------------------------------------------------------------------------------------
r = run("r" + rec("QRV 146.52").hex(), True)
check(r["kind"] == "position" and info_of(r["frame"]) == "!4142.85N/07243.60W[QRV 146.52",
      f"with a fix: position packet with the text as its comment: {info_of(r['frame'])!r}")
r = run("r" + rec("QRV 146.52").hex(), False)
check(r["kind"] == "status" and info_of(r["frame"]) == ">QRV 146.52 no GPS fix", f"no fix: status packet '>' + text + ' no GPS fix': {info_of(r['frame'])!r}")
r = run("0", False)
check(info_of(r["frame"]) == ">DM-32UV Kraken no GPS fix", "no text, no fix: the status packet is as before")
r = run("0", True)
check(info_of(r["frame"]) == "!4142.85N/07243.60W[DM-32UV Kraken", "no text, a fix: the position packet is as before")

typable = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .-/,:+()"
stuff = [c for c in typable if any((ord(c) >> s) & 0x1F == 0x1F for s in range(4))]
check(not stuff and "|" not in typable and "~" not in typable, f"no typable character has five 1 bits in a row (no stuffing) or is | or ~: {stuff}")
# the most bits: every typable character is 8 bits unstuffed, so the longest text is the worst; check both packets
worst = "W" * 43
for fix in (True, False):
    r = run("r" + rec(worst).hex(), fix)
    check(r["kind"] == ("position" if fix else "status") and 0 < r["nbits"] <= 1500 and worst in info_of(r["frame"]),
          f"43-character text, {r['kind']} packet: {r['nbits']} tone bits (cap 1500), {len(info_of(r['frame']))}-character info")
check(len(info_of(run("r" + rec(worst).hex(), False)["frame"])) - 1 <= 62, "the status text stays within APRS's 62 characters")

# ---- 4. the claim ------------------------------------------------------------------------------------------------------
alloc = json.loads((hostcc.ROOT / "allocations.json").read_text())
recs = {d["name"]: (d["block"], int(d["start"], 16), int(d["end_exclusive"], 16)) for d in alloc["datachip_records"]["records"]}
mine = recs.get("aprs_beacon_text")
check(mine == (4, 0xF90, 0xFC0), f"allocations.json claims block 4 0xF90..0xFC0 for it: {mine}")
others = [(n, v) for n, v in recs.items() if n != "aprs_beacon_text" and v[0] == 4 and v[1] < 0xFC0 and 0xF90 < v[2]]
check(not others, f"no other datachip record overlaps it: {others}")
check(0xF10 <= 0xF90 and 0xFC0 <= 0xFF0, "inside the block-4 span no stock code reaches (0xF10-0xFEF, docs/settings-field.md 3.3)")

finish("test_aprs_text: 0 failure(s)", "test_aprs_text: {n} failure(s)")
