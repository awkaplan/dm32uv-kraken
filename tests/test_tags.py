#!/usr/bin/env python3
"""Host test for the standby tags (krk_tags.c, docs/standby-tags.md).

  1. Layout: no tags clears only; 1..4 tags centred in x 60..199 at row 290, 24 px each, 10 px apart;
     5+ tags show the first three and "+N" in the fourth slot.
  2. Order and colour: auto-transmit features (VT VX CW BN SA) first in red, then the receive-side ones
     (SC MN SQ PR) in the stock theme colour; the theme index wraps to stock's 8-entry table.
  3. Which feature gives which tag, and the channel rules done here: Parrot only on a digital channel,
     RX Monitor only on an analog one; VOX and the VOX Monitor test mode are one tag (krk_vox_tag picks).
  4. The standby hook draws stock's line first, then the tags, and records them.
  5. The maintenance tick: no draw on a Kraken screen, while keyed or off standby (screen-mode byte not
     0 / 0x5b); no draw when the set is unchanged; one draw when it changed; never on a mode change mid-way.

Runs on the host with the host cc (no Docker), like test_cwid.py.
"""
import pathlib, tempfile
import hostcc
from hostlib import check as _check, finish


def check(label, cond, info=None):
    _check(cond, label, extra="" if cond else str(info))

TU = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "krk.h"
#include "krk_stock.h"
#undef UI_MODE_BYTE
#undef CALL_ACTIVE
unsigned char ui_mode, call_active, theme_idx;
unsigned short theme_tab[8] = { 0xFFFF, 0x8430, 0xFD20, 0xF800, 0xFFE0, 0x07E0, 0x07FF, 0x001F };
unsigned scr_reg;
#define UI_MODE_BYTE ui_mode
#define CALL_ACTIVE  call_active
#define THEME_IDX    theme_idx
#define THEME_TABLE  theme_tab
#define SCRAMBLE_REG scr_reg
static int log_on = 1;
static void host_stock_bottom(void) { if (log_on) printf("STOCK\n"); }
#define stock_standby_bottom host_stock_bottom
#undef display_fill_rect
static void host_fill(int x0, int y0, int x1, int y1, u16 c) { if (log_on) printf("FILL %d %d %d %d %04x\n", x0, y0, x1, y1, c); }
#define display_fill_rect host_fill
#include "krk_tags.c"

volatile u32 krk_tags_shown, krk_screen_owner;
unsigned vox; int cw, bn, sa, mon, sq, par, analog;
unsigned krk_vox_tag(void) { return vox; }
int krk_cwid_active(void) { return cw; }
int krk_beacon_active(void) { return bn; }
int krk_sat_active(void) { return sa; }
int krk_mon_on(void) { return mon; }
int krk_sq_active(void) { return sq; }
int krk_parrot_armed(void) { return par; }
int krk_channel_analog(void) { return analog; }
void krk_text(int x, int y, const char *s, u16 fg) { if (log_on) printf("TEXT %d %d %s %04x\n", x, y, s, fg); }

int main(int argc, char **argv) {
    /* argv[1]: a feature string, one letter per feature on; argv[2]: what to run */
    const char *f = argv[1];
    vox = strchr(f, 't') ? KRK_VOX_TAG_TEST : strchr(f, 'v') ? KRK_VOX_TAG_SW : 0;
    cw = !!strchr(f, 'c'); bn = !!strchr(f, 'b'); sa = !!strchr(f, 's'); scr_reg = strchr(f, 'x') ? 0x30393039u : 0;
    mon = !!strchr(f, 'm'); sq = !!strchr(f, 'q'); par = !!strchr(f, 'p'); analog = !strchr(f, 'D');
    theme_idx = strchr(f, 'T') ? 10 : 0;                    /* out of range: must wrap to the table */
    if (!strcmp(argv[2], "hook")) {
        krk_hook_standby_bottom();
        printf("SHOWN %08x\n", (unsigned)krk_tags_shown);
    } else if (!strcmp(argv[2], "tick")) {
        /* argv[3]: gate ("owner", "tx", "mode7", "mode5b", "ok"); argv[4]: "same" = shown already matches */
        log_on = 0;
        krk_hook_standby_bottom();                          /* records the current set */
        if (strcmp(argv[4], "same")) { mon = !mon; }        /* the set changes behind the UI's back */
        log_on = 1;
        if (!strcmp(argv[3], "owner")) krk_screen_owner = KRK_SCR_MAGIC;
        if (!strcmp(argv[3], "tx")) call_active = TX_UP;
        if (!strcmp(argv[3], "mode7")) ui_mode = 7;
        if (!strcmp(argv[3], "mode5b")) ui_mode = 0x5b;
        krk_tags_tick();
        krk_tags_tick();                                    /* a second pass must not redraw */
        printf("SHOWN %08x\n", (unsigned)krk_tags_shown);
    }
    return 0;
}
'''

d = pathlib.Path(tempfile.mkdtemp())
(d / "t.c").write_text(TU)
exe = hostcc.build([d / "t.c"])
RED, WHITE = "f800", "ffff"


def run(*args):
    return hostcc.run(exe, *args).splitlines()


def texts(out):
    return [(int(l.split()[1]), int(l.split()[2]), l.split()[3], l.split()[4]) for l in out if l.startswith("TEXT")]


# 1 + 4: no tags
out = run("-", "hook")
check("no tags: stock line first", out[0] == "STOCK", out)
check("no tags: our band cleared", out[1] == "FILL 60 290 200 320 0000", out)
check("no tags: nothing drawn", texts(out) == [], out)
check("no tags: shown recorded", out[-1] == "SHOWN 54470000", out)

# one tag, centred on x 130
t = texts(run("m", "hook"))
check("one tag: MN centred at x 118, row 290", t == [(118, 290, "MN", WHITE)], t)

# four tags: all shown, centred, inside 60..199, 10 px apart
t = texts(run("vcmq", "hook"))
check("four tags: order VX CW MN SQ", [x[2] for x in t] == ["VX", "CW", "MN", "SQ"], t)
check("four tags: x 67 101 135 169", [x[0] for x in t] == [67, 101, 135, 169], t)
check("four tags: inside the free band", t[0][0] >= 60 and t[-1][0] + 24 <= 200, t)
check("four tags: red for auto-TX, theme for RX", [x[3] for x in t] == [RED, RED, WHITE, WHITE], t)

# five and ten: three tags and +N
t = texts(run("vcbmq", "hook"))
check("five tags: VX CW BN +2", [x[2] for x in t] == ["VX", "CW", "BN", "+2"], t)
check("five tags: +N in the theme colour", t[-1][3] == WHITE, t)
t = texts(run("tcbsxmqpD", "hook"))           # D: digital, so MN drops and PR shows: VT CW BN SA SC SQ PR = 7
check("seven tags on digital: VT CW BN +4", [x[2] for x in t] == ["VT", "CW", "BN", "+4"], t)

# 2: theme wraps; 3: channel rules and the one VOX tag
t = texts(run("mT", "hook"))
check("theme index 10 wraps to entry 2", t[0][3] == "fd20", t)
check("parrot hidden on analog", [x[2] for x in texts(run("p", "hook"))] == [], None)
check("parrot shown on digital", [x[2] for x in texts(run("pD", "hook"))] == ["PR"], None)
check("monitor hidden on digital", [x[2] for x in texts(run("mD", "hook"))] == [], None)
check("VOX tag VX", [x[2] for x in texts(run("v", "hook"))] == ["VX"], None)
check("test mode tag VT", [x[2] for x in texts(run("t", "hook"))] == ["VT"], None)
check("scrambler tag SC from the applied register", [x[2] for x in texts(run("xD", "hook"))] == ["SC"], None)
check("satellite tag SA in red", texts(run("s", "hook")) == [(118, 290, "SA", RED)], None)

# 5: the maintenance tick
for gate in ("owner", "tx", "mode7"):
    out = run("q", "tick", gate, "changed")
    check(f"tick: no draw ({gate})", not any(l.startswith(("FILL", "TEXT")) for l in out), out)
out = run("q", "tick", "ok", "same")
check("tick: unchanged set, no draw", not any(l.startswith(("FILL", "TEXT")) for l in out), out)
out = run("q", "tick", "ok", "changed")
check("tick: changed set drawn once", sum(l.startswith("FILL") for l in out) == 1, out)
check("tick: new set MN SQ", [x[2] for x in texts(out)] == ["MN", "SQ"], out)
check("tick: shown updated", out[-1] == "SHOWN 544700c0", out)
out = run("q", "tick", "mode5b", "changed")
check("tick: standby value 0x5b draws too", sum(l.startswith("FILL") for l in out) == 1, out)

finish()
