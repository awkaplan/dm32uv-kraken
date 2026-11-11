#!/usr/bin/env python3
"""Host test for the APRS RX alert (krk_aprs_alert.c, docs/aprs-rx-monitor.md section 5): the standby notice's logic.

  1. Off: noise state and Alert off draw and post nothing; a frame records nothing.
  2. Show: not before AL_DELAY; then once, with the ring entry's call and text, and only when every gate is open
     (standby mode byte 0 / 0x5b, no key, not keyed, PTT up, task menu pending on an empty queue).
  3. Clear: event 100 posted (kind 1) after AL_SHOW, or right after Back is released; never while a key is held, off
     standby or while task menu is busy.
  4. Rate limit: one notice per AL_GAP, the newest station wins; the same station again within AL_REPEAT gets none, a
     different one does, the same one after AL_REPEAT does; a notice that waited past AL_STALE is dropped; the APRS RX
     screen (Kraken screen owner) records nothing.
  5. No transmit path: the PTT flag post is never called.

The tick and the frame entry are the real code; the stock RAM cells and calls are host variables. Runs with the host cc.
"""
import pathlib
import tempfile

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
#define AL_BSS
#undef OS_TICKS
#undef UI_MODE_BYTE
#undef CALL_ACTIVE
#undef ptt_pin_down
#undef write_field_and_mbox_post
#undef OSFlagPost_wrapper_set
unsigned ticks; unsigned char ui_mode, call_active, key_cur, ptt; unsigned short q[0x30];
#define OS_TICKS ticks
#define UI_MODE_BYTE ui_mode
#define CALL_ACTIVE call_active
#define KEY_CUR key_cur
#define UI_Q ((volatile const u8 *)q)
static unsigned char host_ptt(void) { return ptt; }
#define ptt_pin_down host_ptt
static void host_post(int kind, int ev) { printf("POST %d %d\n", kind, ev); }
#define write_field_and_mbox_post host_post
#define OSFlagPost_wrapper_set(g, f) (printf("PTTPOST\n"), 0)
#include "krk_aprs_alert.c"

volatile u32 krk_screen_owner;
int krk_mon_on(void) { return 1; }
void krk_popup_draw(int kind, const char *l1, const char *l2) { printf("DRAW %d %s|%s\n", kind, l1, l2); }
static char call[16], info[16];

int main(void) {
    char cmd[16], a[16], b[16];
    unsigned v;
    while (scanf("%15s", cmd) == 1) {
        if (!strcmp(cmd, "noise")) memset(&A, 0xa5, sizeof A);
        else if (!strcmp(cmd, "set") && scanf("%u", &v) == 1) krk_aprs_alert_set((int)v);
        else if (!strcmp(cmd, "at") && scanf("%u", &v) == 1) ticks = v;
        else if (!strcmp(cmd, "mode") && scanf("%x", &v) == 1) ui_mode = (u8)v;
        else if (!strcmp(cmd, "key") && scanf("%x", &v) == 1) key_cur = (u8)v;
        else if (!strcmp(cmd, "tx") && scanf("%u", &v) == 1) call_active = v ? TX_UP : 0;
        else if (!strcmp(cmd, "ptt") && scanf("%u", &v) == 1) ptt = (u8)v;
        else if (!strcmp(cmd, "wait") && scanf("%u", &v) == 1) q[0x10 / 2] = (u16)v;   /* tasks pending on the UI queue */
        else if (!strcmp(cmd, "msgs") && scanf("%u", &v) == 1) q[0x2a / 2] = (u16)v;   /* events queued */
        else if (!strcmp(cmd, "owner") && scanf("%u", &v) == 1) krk_screen_owner = v ? KRK_SCR_MAGIC : 0;
        else if (!strcmp(cmd, "frame") && scanf("%15s %15s", a, b) == 2) {   /* stn_add has put it in ring[0] */
            strcpy(call, a); strcpy(info, b); krk_aprs_alert_frame(call, info);
        } else if (!strcmp(cmd, "run") && scanf("%u", &v) == 1) {             /* v passes of the tick, one per OS tick */
            while (v--) { krk_aprs_alert_tick(); ticks++; }
        } else if (!strcmp(cmd, "on?")) printf("ON %d\n", krk_aprs_alert_on());
        else if (!strcmp(cmd, "st?")) printf("ST %d %d\n", A.st, A.pend);
        else { printf("BAD %s\n", cmd); return 1; }
    }
    return 0;
}
'''

d = pathlib.Path(tempfile.mkdtemp())
(d / "t.c").write_text(TU)
exe = hostcc.build([d / "t.c"])
IDLE = "mode 0 key 0 tx 0 ptt 0 wait 1 msgs 0 owner 0 "        # standby, every gate open
DELAY, STALE, SHOW, GAP, REPEAT = 50, 1000, 500, 800, 30000


def run(script):
    out = hostcc.run(exe, input=script).splitlines()
    assert not any(l.startswith("BAD") for l in out), out
    return out


def draws(out):
    return [l for l in out if l.startswith("DRAW")]


def ev(out):
    return [l for l in out if l.startswith(("DRAW", "POST", "PTTPOST"))]


# ---- 1. off ------------------------------------------------------------------------------------------------------
out = run("at 100000 " + IDLE + "noise frame N0CALL-9 !4140.5 run 2000 on?")
check("noise state: Alert reads off, a frame and 2000 ticks draw and post nothing", ev(out) == [] and "ON 0" in out, out)
out = run("at 100000 " + IDLE + "set 1 set 0 frame N0CALL-9 !4140.5 run 2000 on?")
check("Alert switched off: nothing", ev(out) == [] and "ON 0" in out, out)

# ---- 2. show -----------------------------------------------------------------------------------------------------
base = "at 100000 " + IDLE + "set 1 frame N0CALL-9 !4140.5 "
out = run(base + f"run {DELAY - 1} st?")
check(f"not before AL_DELAY ({DELAY} ticks after the frame)", ev(out) == [] and "ST 0 1" in out, out)
out = run(base + f"run {DELAY + 1} st?")
check("then drawn once: info icon, call, text (the ring entry)", ev(out) == ["DRAW 0 N0CALL-9|!4140.5"] and "ST 1 0" in out, out)
for name, gate in (("menu open (mode 7)", "mode 7"), ("a key held", "key 2"), ("keyed", "tx 1"), ("PTT pressed", "ptt 1"),
                   ("task menu busy (not pending on its queue)", "wait 0"), ("an event queued for task menu", "msgs 1")):
    out = run(base + gate + f" run {DELAY + 200} st?")
    check(f"gate: {name}: nothing drawn, still pending", ev(out) == [] and "ST 0 1" in out, out)
out = run(base + f"mode 5b run {DELAY + 1}")
check("standby after event 0x2c (mode 0x5b) counts as standby", ev(out) == ["DRAW 0 N0CALL-9|!4140.5"], out)
out = run("at 100000 " + IDLE + "set 1 owner 1 frame N0CALL-9 !4140.5 owner 0 run 200 st?")
check("frame while a Kraken screen runs (the APRS RX screen decodes): not recorded", ev(out) == [] and "ST 0 0" in out, out)

# ---- 3. clear ----------------------------------------------------------------------------------------------------
shown = base + f"run {DELAY + 1} "
out = run(shown + f"run {SHOW - 2} st?")
check("shown: no post before AL_SHOW", ev(out) == ["DRAW 0 N0CALL-9|!4140.5"] and "ST 1 0" in out, out)
out = run(shown + f"run {SHOW + 1} st?")
check("after AL_SHOW: event 100 posted once (kind 1), idle again", ev(out)[1:] == ["POST 1 100"] and "ST 0 0" in out, out)
out = run(shown + "run 20 key 6 run 30 st? key 0 run 1 st?")
e = ev(out)
check("Back held: no post while it is down; posted on the first pass after release", e[1:] == ["POST 1 100"] and out.index("ST 1 0") < out.index("POST 1 100"), out)
out = run(shown + f"run 20 mode 7 run {SHOW * 2} st? mode 0 run 1 st?")
check("a menu opened over the notice: no post in the menu; one redraw post on return to standby",
      ev(out)[1:] == ["POST 1 100"] and out.index("ST 1 0") < out.index("POST 1 100") and out[-1] == "ST 0 0", out)
out = run(shown + f"run 20 tx 1 run {SHOW * 2} tx 0 run 1")
check("keyed while the notice is up: post waits until unkeyed", ev(out)[1:] == ["POST 1 100"], out)

# ---- 4. rate limit -----------------------------------------------------------------------------------------------
out = run(shown + f"run {SHOW + 1} frame W1AW !4140.5N run {GAP - SHOW - DELAY - 20} st? run 80")
e = ev(out)
check(f"a second station soon after: waits for AL_GAP ({GAP} ticks after the first), then shown",
      e == ["DRAW 0 N0CALL-9|!4140.5", "POST 1 100", "DRAW 0 W1AW|!4140.5N"] and "ST 0 1" in out, out)
out = run(shown + f"run {SHOW + 1} frame W1AW aaa frame K1ABC bbb run {GAP}")
check("two stations in one gap: the newest wins", draws(out)[1:] == ["DRAW 0 K1ABC|bbb"], out)
out = run(shown + f"run {GAP} frame N0CALL-9 >again run {GAP} st?")
check("the same station again within AL_REPEAT: dropped, no notice", len([x for x in ev(out) if x.startswith("DRAW")]) == 1 and "ST 0 0" in out, out)
out = run(shown + f"run {GAP} frame N0CALL-7 other run {GAP}")
check("another SSID is another station: shown", draws(out)[-1] == "DRAW 0 N0CALL-7|other", out)
out = run(shown + f"run {REPEAT} frame N0CALL-9 >later run {DELAY + 1}")
check("the same station after AL_REPEAT: shown again", draws(out)[-1] == "DRAW 0 N0CALL-9|>later", out)
out = run(base + f"mode 7 run {STALE + 10} mode 0 run 100 st?")
check(f"pending through a menu visit longer than AL_STALE ({STALE} ticks): dropped on return, nothing drawn", ev(out) == [] and "ST 0 0" in out, out)
out = run(base + f"mode 7 run {STALE - 100} mode 0 run 1")
check("a shorter visit: shown on return to standby", ev(out) == ["DRAW 0 N0CALL-9|!4140.5"], out)
out = run(f"at {0xFFFFFFFF - 1300} " + IDLE + f"set 1 frame W1AW w run {DELAY + 1} at {0xFFFFFFFF - 20} frame N0CALL-9 x run {DELAY + 1}")
check("OS tick counter wrapping between frame and tick: shown", draws(out) == ["DRAW 0 W1AW|w", "DRAW 0 N0CALL-9|x"], out)

# ---- 5. receive only ---------------------------------------------------------------------------------------------
out = run(shown + f"run {SHOW * 4} frame W1AW y run {GAP * 2} key 6 run 5 key 0 run 5 ptt 1 run 5")
check("never a PTT post on any path", "PTTPOST" not in out, out)

raise SystemExit(finish())
