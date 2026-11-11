"""F14b APRS RX alert in-target checks (patches/kraken_ui/krk_aprs_alert.c, the RX Monitor option row in krk_aprsrx.c,
docs/aprs-rx-monitor.md section 5), called from kraken_ui_test.py. The decision logic (gates, rate limit) is covered on the
host by tests/test_aprs_alert.py; these check the real image.

  unit_checks (halted machine):
    * the state sits in the kraken_aprs_state claim, at most 40 bytes;
    * the RX Monitor option row: Off / On / Alert, the setter switches the Monitor and Alert together, the getter reads them back;
    * through the real background-loop hook (krk_hook_bg_tick): a pending frame at standby with the stock UI queue idle draws the
      stock popup with the ring entry's strings; with the UI queue busy it does not; after AL_SHOW the hook posts UI event 100
      (kind 1); no PTT post on the way.
  boot_checks (booted runs, own QEMUs; the frame is planted in RAM as the decoder would leave it, QEMU has no receive audio):
    * Alert set through Menu > APRS > RX Monitor (OK, Down, Down, OK), back at standby: the popup box is drawn, and about 5 s
      later stock's own standby redraw (event 100) has removed it, standby mode byte kept;
    * On set the same way: no popup;
    * Alert on and the APRS list open (a menu): no popup, the notice stays pending.
"""
import re
import struct

import mi_rng_test as m
from build_artifacts import REPO
from claims import claim
from gdb_rsp import read_mem
from qemu_menu import HIST, OS_TICKS, PATHS, UI_MODE, run_scenarios
from target import cstr, fcall, r32, w32

AL_MAGIC, MON_MAGIC = 0x414C5254, 0x4D4F4E31
UI_MODE_BYTE, KEY_CUR, CALLACT, KEYMSGQ_LIT = 0x000320AF, 0x000392C4, 0x0003FDC8, 0x030131D8
PTT_PIN, POST, STOCK_PRESC, PTT_FLAGPOST = 0x03012F48, 0x03013178, 0x030546DC, 0x0300C494
O_MAGIC, O_TFRAME, O_TSHOWN, O_CALL, O_INFO, O_LASTH, O_ST, O_PEND = 0, 4, 8, 12, 16, 20, 22, 23
BOX = (23, 50, 217, 210)            # the stock popup box (display_draw_dialog_panel 0x03019be8), grey 0xc618


def grey(fr):
    x0, y0, x1, y1 = BOX
    return sum(1 for y in range(y0, y1, 2) for x in range(x0, x1, 2) if fr[y][x] == 0xC618)


def unit_checks(c, s, s2):
    print("== F14b APRS RX alert: state, option row, the background-loop hook")
    A, M, RING = s2["A"], s2["M"], s2["ring"]
    lo, hi = claim("kraken_aprs_state")
    m.report(lo <= A and A + 24 <= hi, f"alert state A {A:#x}+24 B inside kraken_aprs_state {lo:#x}-{hi:#x}")
    w8 = lambda a, v: m.wmem(c, a, bytes([v & 0xFF]))

    # the option row: labels, get/set through the real Monitor
    o = struct.unpack("<IIBxxxII", read_mem(c, s2["mon_opt"], 20))
    labs = [cstr(c, p) for p in struct.unpack("<III", read_mem(c, o[1], 12))]
    m.report(o[2] == 3 and labs == [b"Off", b"On", b"Alert"], f"RX Monitor option level: 3 options {labs}")
    get, set_ = o[3], o[4]
    m.wmem(c, M, bytes(20))
    m.wmem(c, A, b"\xA5" * 24)
    w32(c, OS_TICKS, 50000)
    res = []
    for idx in (2, 1, 2, 0):
        _, _, abi, _ = fcall(c, set_, (idx,))
        _, r0, abi2, _ = fcall(c, get)
        res.append((idx, r0, r32(c, M) == MON_MAGIC, r32(c, A) == AL_MAGIC, abi and abi2))
    m.report(res == [(2, 2, True, True, True), (1, 1, True, False, True), (2, 2, True, True, True), (0, 0, False, False, True)],
             f"setter/getter: Alert = Monitor on + Alert, On = Monitor only, Off = both off (idx, get, mon, alert, abi): {res}")
    # through the hook: Alert on (real setter), a frame as harvest hands it over, standby, UI queue idle
    fcall(c, set_, (2,))
    m.wmem(c, M, bytes(20))                      # Monitor engine off for the hook pass (its own checks are in mon_checks.py)
    w32(c, A, AL_MAGIC)
    m.wmem(c, RING + 2, b"N0CALL-9\0\0" + b"!4140.5\0")
    q = r32(c, KEYMSGQ_LIT)
    calls = []

    def rec(tag, ret=0):
        def f(regs, r0, r1):
            calls.append((tag, r0, r1, regs.get("r2", 0)))
            return ret
        return f
    fakes = {s["krk_popup_draw"]: rec("draw"), POST: rec("post"), PTT_PIN: rec("ptt"), STOCK_PRESC: rec("presc"), PTT_FLAGPOST: rec("pttpost")}
    w8(UI_MODE_BYTE, 0)
    w8(KEY_CUR, 0)
    w8(CALLACT, 0)
    w32(c, s["krk_screen_owner"], 0)
    m.wmem(c, q + 0x10, b"\x01\x00")
    m.wmem(c, q + 0x2a, b"\x00\x00")
    now = 60000
    w32(c, OS_TICKS, now)
    fcall(c, s2["krk_aprs_alert_frame"], (RING + 2, RING + 12))
    m.report(read_mem(c, A + O_PEND, 1)[0] == 1 and r32(c, A + O_CALL) == RING + 2 and r32(c, A + O_INFO) == RING + 12,
             "krk_aprs_alert_frame: pending, pointing at the ring entry")

    def hook(t):
        del calls[:]
        w32(c, OS_TICKS, t)
        _, _, abi, _ = fcall(c, s2["krk_hook_bg_tick"], fakes=fakes)
        return [x for x in calls if x[0] in ("draw", "post", "pttpost")], abi
    m.wmem(c, q + 0x10, b"\x00\x00")             # task menu not pending on its queue (busy)
    got, abi = hook(now + 60)
    m.report(got == [] and abi and read_mem(c, A + O_PEND, 1)[0] == 1, f"hook, task menu busy (no task pending on keymsgq {q:#x}): nothing drawn, still pending {got}")
    m.wmem(c, q + 0x10, b"\x01\x00")
    got, abi = hook(now + 61)
    ok = len(got) == 1 and got[0][0] == "draw" and got[0][1] == 0 and got[0][2] == RING + 2 and got[0][3] == RING + 12
    m.report(ok and abi and read_mem(c, A + O_ST, 1)[0] == 1, f"hook at standby, UI idle: the stock popup is drawn (info icon, the ring entry's call and text) {got}")
    got, abi = hook(now + 61 + 400)
    m.report(got == [] and abi, "hook 4 s later: no post yet")
    got, abi = hook(now + 61 + 501)
    m.report([x[:3] for x in got] == [("post", 1, 100)] and abi and read_mem(c, A + O_ST, 1)[0] == 0,
             f"hook after 5 s: UI event 100 posted (kind 1), so task menu redraws standby {got}")
    m.report(not any(x[0] == "pttpost" for x in calls), "no PTT flag post on any of these passes")
    fcall(c, set_, (0,))
    m.report(r32(c, A) != AL_MAGIC and r32(c, M) != MON_MAGIC, "setter(0): Alert and Monitor off again")


def boot_checks(patched, s, s2):
    """Booted runs: the setting through the stock menu, the popup drawn at standby and removed by stock's own redraw."""
    A, M, RING = s2["A"], s2["M"], s2["ring"]
    core = (REPO / "patches/kraken_ui/krk_core.c").read_text()
    analog = [t.strip() for t in re.search(r"aprs_analog_items\[\d*\]\s*=\s*\{([^}]*)\}", core)[1].split(",")]
    to_mon = PATHS["aprs"] + " " + "down " * analog.index("KRK_APRS_ITEM_MON")
    u32 = lambda b, a: struct.unpack("<I", b.mem(a, 4))[0]
    standby = lambda b: b.u8(UI_MODE) in (0, 0x5B)

    def plant(b):
        """What the decoder leaves after a frame: the ring entry, and krk_aprs_alert_frame's pending notice (A.magic is the setter's)."""
        b.poke(RING + 2, b"N0CALL-9\0\0" + b"!4140.5\0")
        now = u32(b, OS_TICKS)
        b.poke(A + O_TFRAME, struct.pack("<II", now, (now - 2000) & 0xFFFFFFFF))
        b.poke(A + O_CALL, struct.pack("<IIHBB", RING + 2, RING + 12, 0, 0, 1))

    def c_alert(b, rep):
        rep(b.until(lambda b: u32(b, A) == AL_MAGIC and u32(b, M) == MON_MAGIC, timeout=40), "Alert committed in the stock option level: Monitor and Alert on in RAM")
        rep(b.until(standby, timeout=40), f"back at standby (mode byte {b.u8(UI_MODE):#x})")
        b.pump(2)
        hist = b.mem(HIST, 4)
        g0 = grey(b.last_frame())
        plant(b)
        ok = b.until(lambda b: b.u8(A + O_ST) == 1, timeout=20)
        b.pump(1.5)
        g1 = grey(b.last_frame())
        rep(ok and g1 > 5000 and g0 < 500, f"a frame at standby: the popup box is drawn (grey px in the box {g0} -> {g1})")
        ok = b.until(lambda b: b.u8(A + O_ST) == 0, timeout=30)
        b.pump(2)
        g2 = grey(b.last_frame())
        rep(ok and g2 < 500 and standby(b) and b.mem(HIST, 4) == hist,
            f"about 5 s later stock redrew standby (event 100): no box ({g2}), mode byte {b.u8(UI_MODE):#x}, history {b.mem(HIST, 4).hex(' ')} unchanged")

    def c_on(b, rep):
        rep(b.until(lambda b: u32(b, M) == MON_MAGIC, timeout=40) and u32(b, A) != AL_MAGIC, "On committed: Monitor on, Alert off")
        rep(b.until(standby, timeout=40), "back at standby")
        plant(b)
        b.pump(4)
        rep(b.u8(A + O_ST) == 0 and grey(b.last_frame()) < 500, "RX Monitor On (not Alert): a frame draws no popup")

    def c_menu(b, rep):
        b.until(lambda b: b.in_list(), timeout=40)
        b.poke(A, struct.pack("<I", AL_MAGIC))
        b.poke(A + O_ST, b"\0\0")
        g0 = grey(b.last_frame())
        plant(b)
        b.pump(3)
        rep(b.u8(A + O_ST) == 0 and b.u8(A + O_PEND) == 1 and abs(grey(b.last_frame()) - g0) < 50 and b.u8(UI_MODE) == 0x0E,
            f"Alert on with the APRS list open: no popup over the menu, the notice stays pending (mode {b.u8(UI_MODE):#x})")

    scen = {
        "alert_show": (to_mon + " ok down down ok:1500 back back", None, c_alert),
        "alert_on_only": (to_mon + " ok down ok:1500 back back", None, c_on),
        "alert_in_menu": (PATHS["aprs"], None, c_menu),
    }
    print("== F14b APRS RX alert, booted (own QEMUs)")
    run_scenarios(patched, scen, m.report, batch=3)
