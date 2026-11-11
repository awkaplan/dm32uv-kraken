#!/usr/bin/env python3
"""Host test for F18 APRS Messages, phase 1 (patches/kraken_ui/krk_aprs_msg.c, docs/aprs-messages.md).

  1. Layout: the inbox struct is exactly the 256-byte kraken_aprs_inbox claim.
  2. Parser: addressee matching (padding, case, SSID, "-0" = no SSID, other SSIDs and calls rejected), the message number
     ("{NN}", reply-ack "{MM}AA"), acks and rejects dropped, bulletins dropped, third-party ("}SRC>...:") messages, CR/LF and other
     non-printable characters as '.', truncation to 59 characters, short/odd info fields, no callsign set = nothing kept.
  3. Store: newest first, oldest drops at 3, duplicates (same sender + number, or same text without a number) within 30 min are
     one message, after 30 min a repeat is new; noise SRAM and a tick counter that went backwards clear the inbox.
  4. Decoder wiring: a message frame synthesized as 1200-baud AFSK, fed through the real decoder (krk_afsk.c) in 256-sample
     halves, reaches the inbox through the hand-off with its full text (longer than the decoder's 60-character line); the
     address walk over digipeater entries.
  5. The screen: empty / no callsign texts, one message per page (newest first: sender, age, text), Up/Down wrap.

Runs on the host with the host cc (no Docker), like test_tags.py.
"""
import pathlib, sys, tempfile
import hostcc
from hostcc import TOOLS, UI
from hostlib import check as _check, finish
sys.path.insert(0, str(TOOLS))
import afsk_channel as ch
import pcmio


def check(label, cond, info=None):
    _check(cond, label, extra="" if cond else str(info))


TU = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "krk.h"
#include "krk_stock.h"
#include "krk_afsk.h"
#undef OS_TICKS
static u32 host_ticks;
#define OS_TICKS host_ticks
static unsigned char host_ib[256] __attribute__((aligned(4)));
#define MSG_IB ((struct krk_inbox *)host_ib)
#undef rtc_reg
static u32 host_rtc = 20400u * 86400u;         /* RTC seconds since 1970 (local); bit 31 = the RTC reads out of range */
static u8 rtc_reg(u32 r) {
    u32 t = host_rtc;
    if (host_rtc & 0x80000000u) return 0xff;
    switch (r) { case 0: return t % 60; case 1: return t / 60 % 60; case 2: return t / 3600 % 24; case 3: return (t / 86400) & 0xff; default: return (t / 86400) >> 8; }
}
#include "krk_aprs_msg.c"
void krk_aprs_msg_frame(const char *line, const unsigned char *f, int len) { krk_msg_rx(line, f, len); }   /* krk_periodic.c's hand-off */

static char host_me[16];
int krk_call_get(char *out) { strcpy(out, host_me); return host_me[0] != 0; }
char *krk_catn(char *d, unsigned size, const char *s) { size_t n = strlen(d); while (*s && n + 1 < size) d[n++] = *s++; d[n] = 0; return d; }
char *krk_cati(char *d, int v) { char t[16]; sprintf(t, "%d", v); return krk_catn(d, KRK_LINE_BUF, t); }
void krk_fmt_age(char *out, u32 sec) { if (sec >= 3600) sprintf(out, "%uh", sec / 3600); else if (sec >= 60) sprintf(out, "%um", sec / 60); else sprintf(out, "%us", sec); }
void krk_line(int n, const char *s, u16 fg) { printf("L%d %04x |%.19s|\n", n, fg, s); }

static void unesc(char *s) { char *o = s; for (; *s; s++) { if (s[0] == '\\' && s[1] == 'r') { *o++ = '\r'; s++; } else if (s[0] == '\\' && s[1] == 'n') { *o++ = '\n'; s++; } else if (s[0] == '\\' && s[1] == 'x') { unsigned v; sscanf(s + 2, "%2x", &v); *o++ = (char)v; s += 3; } else *o++ = *s; } *o = 0; }

int main(void) {
    char ln[1200];
    memset(host_ib, 0xA5, sizeof host_ib);
    while (fgets(ln, sizeof ln, stdin)) {
        ln[strcspn(ln, "\n")] = 0;
        if (!strncmp(ln, "size", 4)) printf("SIZE %u\n", (unsigned)sizeof(struct krk_inbox));
        else if (!strncmp(ln, "me ", 3)) strcpy(host_me, strcmp(ln + 3, "-") ? ln + 3 : "");
        else if (!strncmp(ln, "t ", 2)) { host_ticks = (u32)strtoul(ln + 2, 0, 0); if (!(host_rtc & 0x80000000u)) host_rtc = 20400u * 86400u + host_ticks / 100; }
        else if (!strncmp(ln, "rtc ", 4)) host_rtc = !strcmp(ln + 4, "bad") ? 0x80000000u : 20400u * 86400u + (u32)strtoul(ln + 4, 0, 0);
        else if (!strncmp(ln, "now", 3)) printf("NOW %u\n", msg_now());
        else if (!strncmp(ln, "noise", 5)) memset(host_ib, 0xA5, sizeof host_ib);
        else if (!strncmp(ln, "f ", 2) || !strncmp(ln, "p ", 2)) {   /* f|p SRC>DST,PATH|info: a frame (p: two path entries) */
            static unsigned char fr[400];
            char *bar = strchr(ln, '|');
            int a = ln[0] == 'p' ? 28 : 14, k;
            *bar = 0;
            unesc(bar + 1);
            memset(fr, 0x40, a);                       /* addresses (contents unused), the extension bit on the last */
            fr[a - 1] |= 1;
            fr[a] = 0x03;
            fr[a + 1] = 0xF0;
            k = (int)strlen(bar + 1);
            memcpy(fr + a + 2, bar + 1, k);
            krk_aprs_msg_frame(ln + 2, fr, a + 2 + k);
        } else if (!strncmp(ln, "pcm ", 4)) {           /* the real decoder on s16le audio, 256-sample halves */
            FILE *fp = fopen(ln + 4, "rb");
            short b[256];
            size_t k;
            krk_afsk_reset();
            while ((k = fread(b, 2, 256, fp)) > 0) krk_afsk_feed(b, (int)k);
            fclose(fp);
            printf("FRAMES %u\n", krk_afsk_frames());
        } else if (!strncmp(ln, "dump", 4)) {
            struct krk_inbox *ib = MSG_IB;
            printf("N %d\n", ib->magic == IB_MAGIC ? ib->n : -1);
            for (int i = 0; ib->magic == IB_MAGIC && i < ib->n; i++)
                printf("M %s|%s|%s|%u\n", ib->m[i].from, ib->m[i].no, ib->m[i].text, ib->m[i].sec);
        } else if (!strncmp(ln, "enter", 5)) msg_draw();       /* the screen's .enter */
        else if (!strncmp(ln, "key ", 4)) msg_tick((u8)strtoul(ln + 4, 0, 0));
        printf("--\n");
        fflush(stdout);
    }
    return 0;
}
'''

exe = hostcc.build([UI / "krk_afsk.c", UI / "krk_ax25.c"], flags=["-O1", "-w"], driver_src=TU)


def run(*cmds):
    """Runs one session (fresh process, inbox starts as noise); returns the output blocks, one per command."""
    out = hostcc.run(exe, input="".join(c + "\n" for c in cmds))
    return out.split("--\n")[:-1]


def msgs(block):
    """The M lines of a dump block as (from, no, text, sec)."""
    r = []
    for ln in block.splitlines():
        if ln.startswith("M "):
            f, no, text, sec = ln[2:].split("|")
            r.append((f, no, text, int(sec)))
    return r


def kept(info, me="N0CALL-5", src="W1AW-9>APDR16,WIDE1-1"):
    """Parses one frame for me in a fresh inbox: the stored (from, no, text) or None."""
    b = run(f"me {me}", "t 1000", f"f {src}|{info}", "dump")
    m = msgs(b[-1])
    return m[0][:3] if m else None


# ---- 1. layout ----------------------------------------------------------------------------------------
size = int(run("size")[0].split()[1])
check(f"inbox struct is the whole 256-byte claim ({size})", size == 256, size)

# ---- 2. parser --------------------------------------------------------------------------------------
check("plain message with number", kept(":N0CALL-5 :Hello there{12") == ("W1AW-9", "12", "Hello there"), kept(":N0CALL-5 :Hello there{12"))
check("no message number", kept(":N0CALL-5 :Hi") == ("W1AW-9", "", "Hi"), kept(":N0CALL-5 :Hi"))
check("addressee in lower case", kept(":n0call-5 :lower") == ("W1AW-9", "", "lower"))
check("other SSID is not me", kept(":N0CALL-6 :not you") is None)
check("no SSID is not me when I have -5", kept(":N0CALL   :not you") is None)
check("another call is not me", kept(":K1ABC    :not you") is None)
check("a longer call that starts with mine is not me", kept(":N0CALLX  :not you") is None)
check("my call without SSID matches 'N0CALL'", kept(":N0CALL   :yes", me="N0CALL") == ("W1AW-9", "", "yes"))
check("my call without SSID does not match 'N0CALL-0' (APRS writes no -0)", kept(":N0CALL-0 :zero", me="N0CALL") is None)
check("my call without SSID does not match 'N0CALL-1'", kept(":N0CALL-1 :one", me="N0CALL") is None)
check("SSID 15", kept(":N0CALL-15:fifteen", me="N0CALL-15") == ("W1AW-9", "", "fifteen"))
check("ack dropped", kept(":N0CALL-5 :ack12") is None)
check("reject dropped", kept(":N0CALL-5 :rej12") is None)
check("reply-ack form 'ackMM}AA' dropped", kept(":N0CALL-5 :ack3}AB") is None)
check("a text that starts with 'ack' but has a message number is kept", kept(":N0CALL-5 :ack it is{7") == ("W1AW-9", "7", "ack it is"))
check("bulletin dropped (addressee BLN1 is not my call)", kept(":BLN1     :bulletin") is None and kept(":BLN1ABC  :group bulletin") is None)
check("reply-ack number '{MM}AA' -> MM", kept(":N0CALL-5 :reply{MM}AA") == ("W1AW-9", "MM", "reply"))
check("message number cut to 5 characters", kept(":N0CALL-5 :long no{1234567") == ("W1AW-9", "12345", "long no"))
check("CR/LF shown as '.', the number still found", kept(":N0CALL-5 :line one\\r\\n{9") == ("W1AW-9", "9", "line one.."), kept(":N0CALL-5 :line one\\r\\n{9"))
check("non-printable characters shown as '.'", kept(":N0CALL-5 :a\\x01b") == ("W1AW-9", "", "a.b"))
check("empty text dropped", kept(":N0CALL-5 :") is None and kept(":N0CALL-5 :{5") is None)
check("info not a message (position) ignored", kept("!4142.85N/07243.62W[") is None)
check("short info ignored", kept(":N0CALL-5") is None and kept(":N0CALL-5 ") is None)
check("missing second ':' ignored", kept(":N0CALL-5 xHi") is None)
long_text = "".join(chr(0x41 + i % 26) for i in range(100))
got = kept(f":N0CALL-5 :{long_text}{{42")
check("text cut to 59 characters, number kept", got == ("W1AW-9", "42", long_text[:59]), got)
t59 = "x" * 59
check("a 59-character text is kept whole", kept(f":N0CALL-5 :{t59}{{1") == ("W1AW-9", "1", t59))
check("third-party (igate) message: inner sender", kept("}K1ABC-7>APRS,TCPIP,W1AW-10*::N0CALL-5 :via igate{3", src="W1AW-10>APRS") == ("K1ABC-7", "3", "via igate"))
check("third-party for someone else ignored", kept("}K1ABC-7>APRS,TCPIP::K2DEF    :x", src="W1AW-10>APRS") is None)
check("third-party without ':' ignored", kept("}K1ABC-7>APRS,TCPIP", src="W1AW-10>APRS") is None)
check("long sender cut to 9 characters", kept("}ABCDEFGHIJK>APRS::N0CALL-5 :x")[0] == "ABCDEFGHI")
b = run("me N0CALL-5", "t 1000", "p W1AW-9>APDR16,WIDE1-1,WIDE2-1|:N0CALL-5 :two hops", "dump")
check("frame with two digipeater entries: the info field is found", [x[2] for x in msgs(b[-1])] == ["two hops"], b[-1])
b = run("me N0CALL-5", "t 1000", "f W1AW-9>APDR16|", "f W1AW-9>APDR16|:N0", "dump")
check("frames with no or a short info field: nothing", msgs(b[-1]) == [], b[-1])
b = run("me -", "t 1000", "f W1AW-9>APDR16|:N0CALL-5 :Hello", "dump")
check("no callsign saved: nothing kept", msgs(b[-1]) == [], b[-1])

# ---- 3. store -----------------------------------------------------------------------------------------
S = "f W1AW-9>APDR16|"
b = run("me N0CALL-5", "dump")
check("noise SRAM reads as an empty inbox only after init (raw noise is not 'magic')", "N -1" in b[-1], b[-1])
b = run("me N0CALL-5", "t 100", S + ":N0CALL-5 :one{1", "t 200", S + ":N0CALL-5 :two{2", "t 300", S + ":N0CALL-5 :three{3",
        "t 400", S + ":N0CALL-5 :four{4", "dump")
m = msgs(b[-1])
check("newest first, oldest drops at 3", [x[2] for x in m] == ["four", "three", "two"] and "N 3" in b[-1], b[-1])
check("age stamps are RTC seconds", [x[3] - 20400 * 86400 for x in m] == [4, 3, 2], m)
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :hello{7", "t 4000", S + ":N0CALL-5 :hello{7", "p W1AW-9>APDR16,WIDE1-1*|:N0CALL-5 :hello{7", "dump")
m = msgs(b[-1])
check("same sender + number + text within 30 min (also digipeated): one message, first copy kept", len(m) == 1 and m[0][2] == "hello" and m[0][3] == 20400 * 86400 + 10, m)
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :hello{7", S + ":N0CALL-5 :hello again{7", "dump")
check("same number, other text: two messages", len(msgs(b[-1])) == 2, b[-1])
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :hello{7", "f K1ABC>APRS|:N0CALL-5 :hello{7", "dump")
check("same number from another sender: two messages", len(msgs(b[-1])) == 2, b[-1])
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :hello{7", S + ":N0CALL-5 :hello{8", "dump")
check("same text, new number: two messages", len(msgs(b[-1])) == 2, b[-1])
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :no number", "t 2000", S + ":N0CALL-5 :no number", S + ":N0CALL-5 :other text", "dump")
check("no number: same text is a repeat, a new text is new", [x[2] for x in msgs(b[-1])] == ["other text", "no number"], b[-1])
b = run("me N0CALL-5", "t 1000", S + ":N0CALL-5 :hello{7", f"t {1000 + 1800 * 100}", S + ":N0CALL-5 :hello{7", "dump")
check("a repeat after 30 minutes is new", len(msgs(b[-1])) == 2, b[-1])
b = run("me N0CALL-5", "t 100", S + ":N0CALL-5 :x", "noise", "t 200", S + ":N0CALL-5 :y", "dump")
check("noise SRAM (magic gone): cleared, then the new message", [x[2] for x in msgs(b[-1])] == ["y"], b[-1])

# ---- 3b. the clock (RTC, not OS_TICKS: the tick runs ~2x fast under stock power save) --------------------
b = run("me N0CALL-5", "t 1000", "rtc 0", S + ":N0CALL-5 :hello{7", "t 400000", "rtc 1000", S + ":N0CALL-5 :hello{7", "dump")
check("repeat window follows the RTC: 4000 ticks (~67 min of ticks) but 1000 RTC seconds = still a repeat", len(msgs(b[-1])) == 1, b[-1])
b = run("me N0CALL-5", "t 1000", "rtc 0", S + ":N0CALL-5 :hello{7", "t 2000", "rtc 1801", S + ":N0CALL-5 :hello{7", "dump")
check("1801 RTC seconds later (only 10 s of ticks): a new message", len(msgs(b[-1])) == 2, b[-1])
b = run("t 5000", "rtc 93725", "now")
nows = [int(x.split()[1]) for x in b if x.startswith("NOW")]
check("msg_now: RTC days/h/m/s -> seconds", nows[0] == 20400 * 86400 + 93725, nows)
b = run("me N0CALL-5", "t 100", "rtc 500", S + ":N0CALL-5 :later{1", "rtc 100", "enter")
check("RTC set back past a message: its age shows '?'", "W1AW-9 ?|" in b[-1], b[-1])

# ---- 4. through the real decoder --------------------------------------------------------------------------
tmp = pathlib.Path(tempfile.mkdtemp())
info = ":N0CALL-5 :Longer than what the decoder's 60-character line holds{42"
frame = ch.ax25_frame("APDR16", "W1AW-9", ["WIDE1-1", "WIDE2-1"], info)
pcm = tmp / "msg.pcm"
pcm.write_bytes(pcmio.pack(ch.cpfsk(ch.ax25_tones(frame, 20, 3), amp=3000)))
b = run("me N0CALL-5", "t 500", f"pcm {pcm}", "dump")
m = msgs(b[-1])
check("synthesized AFSK message frame -> decoder -> inbox, full text", "FRAMES 1" in b[-2] and m == [("W1AW-9", "42", info[11:-3], 20400 * 86400 + 5)], b)
frame2 = ch.ax25_frame("APDR16", "W1AW-9", [], "!4142.85N/07243.62W[position")
pcm.write_bytes(pcmio.pack(ch.cpfsk(ch.ax25_tones(frame2, 20, 3), amp=3000)))
b = run("me N0CALL-5", "t 500", f"pcm {pcm}", "dump")
check("a position frame decodes but is not a message", "FRAMES 1" in b[-2] and msgs(b[-1]) == [], b)

# ---- 5. the screen --------------------------------------------------------------------------------------
def rows(block):
    r = {}
    for ln in block.splitlines():
        if ln.startswith("L"):
            n, fg, rest = ln[1:].split(" ", 2)
            r[int(n)] = (rest[1:-1], fg)
    return r

FG, DIM, ACC, WARN = "ffff", "8410", "07e0", "f800"
b = run("me -", "t 100", "enter")
r = rows(b[-1])
check("no callsign: 'Set callsign first'", r[2] == ("Set callsign first", DIM) and len(r) == 10, r)
b = run("me N0CALL-5", "t 100", "enter")
r = rows(b[-1])
check("empty inbox: 'No messages', all 10 rows drawn", r[0][0].startswith("Messages") and r[2][0] == "No messages" and len(r) == 10, r)
txt = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
b = run("me N0CALL-5", "t 100", S + ":N0CALL-5 :old one{1", "t 200", "f K1ABC-7>APRS|:N0CALL-5 :" + txt + "{2", "t 7400", "enter",
        "key 5", "key 5", "key 2", "key 3")
r = rows(b[6])
check("page 1: title with position, newest first, sender and age", r[0] == ("Messages 1/2", ACC) and r[1] == ("K1ABC-7 1m", ACC), r)
check("page 1: the stored 59 characters over four rows, rows 6-9 empty", [r[i][0] for i in (2, 3, 4, 5)] == [txt[0:19], txt[19:38], txt[38:57], txt[57:59]]
      and all(r[i][0] == "" for i in (6, 7, 8, 9)), r)
r = rows(b[7])
check("Down: the older message, a short text leaves rows empty", r[0][0] == "Messages 2/2" and r[1] == ("W1AW-9 1m", ACC) and r[2] == ("old one", FG)
      and all(r[i][0] == "" for i in (3, 4, 5)), r)
r = rows(b[8])
check("Down wraps to the newest", r[0][0] == "Messages 1/2" and r[1][0].startswith("K1ABC-7"), r)
r = rows(b[9])
check("Up wraps to the oldest", r[0][0] == "Messages 2/2", r)
check("OK does nothing (no redraw within the second)", "L0" not in b[10], b[10])
b = run("me N0CALL-5", "t 200", S + ":N0CALL-5 :a{1", S + ":N0CALL-5 :b{2", "enter", "key 5", "enter", S + ":N0CALL-5 :c{3", "enter")
check("reopening keeps the page; a new message puts it back on the newest", "Messages 2/2" in b[6] and "Messages 1/3" in b[8], b)
b = run("me N0CALL-5", "t 100", "enter", "key 0", "t 200", "key 0")
check("no redraw within the same second, redraw on the next", "L0" not in b[3] and "L0" in b[5], b)

finish()
