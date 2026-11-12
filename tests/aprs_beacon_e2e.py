#!/usr/bin/env python3
"""F8 analog automatic APRS beacon, booted end to end in QEMU (issue #7; docs/aprs-submenu.md 7.1).

Every run sets the beacon up THROUGH THE MENU, the way an owner does (Menu > APRS > APRS On/Off = ON, Intervals Set =
30 s, Back Back), then watches krk_hook_aprs_tick's due branch: a breakpoint on its call of event_queue_reset, the
step right after the gates this issue is about (each hit records the OS tick and the screen-mode byte 0x320af). The
send itself is not reached in QEMU: there [0x3e31f] bit 3 is always set by stock's ADC level check (0x0301bb44, the
emulated ADC reads above its 0x383 threshold), and stock's beacon tick and ours skip the round on [0x3e31f] & 0x18, as
they do on a radio in that state. (The send entry is covered by optrow_checks' Upload Beacon runs.) QEMU is not on
the air; nothing here keys a transmitter.

  now        APRS On from the menu sets [0x40078] bit 0 (APRS active on this channel) at once, while the list is still
             open, without a channel change (the stock commit, settings_write_dispatcher case 0x58, writes it).
  standby    back at standby, the first due beacon comes one interval (30 s) after standby, the next one 30 s later.
  standby5b  the same with the screen-mode byte at 0x5b, the value stock's "go to standby" event (0x2c) leaves at standby
             on a radio (QEMU's standby path leaves 0, so this is planted). The old gate (`0x320af != 0` = skip) never
             sent here: issue #7.
  menu       a real menu opened about 15 s after standby and kept open over the moment the beacon falls due: nothing
             is due while it is open.
  mode69     screen-mode byte planted at 0x69 (krk_ui_in_menu's mode the stock loop does not skip): no send while it
             holds; the due beacon goes out within seconds of standby (0) coming back.
  owner      krk_screen_owner planted (one of our screens owns the radio): no send; the due beacon follows its release.
  cps_off    stock's APRS-enabled bit [0x3fdc0] & 4 cleared at standby: the maintenance loop never reaches the hook, so
             nothing is sent; set again, the beacon resumes an interval later. (The same bit shows the Menu > APRS
             category, docs/menu-system.md in dm32uv-re.)

Timing is measured in guest OS ticks (100 Hz), not wall time, so a loaded host only stretches the wall time. The RTC
correction of the interval (krk_real_time) is for stock power save, which QEMU does not emulate; the interval bounds
leave room for it moving the timer by a second or two against the emulated RTC.

Run inside the dm32uv-re image, like the other in-target tests:
  python3 /w/dm32uv-unleashed/tests/aprs_beacon_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
tests/kraken_ui_test.py runs it in-process (run()).
"""
import os
import pathlib
import struct
import sys
import threading
import time

from build_artifacts import elf, syms                  # first: puts the tool directories on sys.path
from gdb_rsp import read_mem
from qemu_menu import HIST, OS_TICKS, PATHS, UI_MODE, Boot, Suite, timeout_scale
from qemu_menu import keys as _keys
import subprocess

keys = _keys                                            # every key at the default gap: under load a fast Down was lost
APRS_RT, APRS_INTERVAL, CPS_FLAGS = 0x40078, 0x4007C, 0x3FDC0
DW_STATE, CHAN_MODE = 0x400D8, 0x3E1E4                  # dual-watch state (0 = off); the loaded channel's mode nibble (0 = analog)
SCR_MAGIC = 0x53435252
HZ = 100
IVL = 30                                                # Intervals Set: one Up from Off
# Menu > APRS (row 0 APRS On/Off) > OK > Up (OFF -> ON) > OK, Down Down (Intervals Set) > OK > Up (30 s) > OK, Back Back
SETUP = PATHS["aprs"] + " ok up ok:800 down down ok up ok:800 back back"
MENU_HOLD = " back menu down:1500 up:1500"        # Back wakes the backlight (the first key after the light went out does only that),
                                                    # then the top menu, two keys restarting its exit timer: open about 30 s


def due_site(elf2, hook):
    """Address of krk_hook_aprs_tick's `jsr` to event_queue_reset (0x03054d80): reached only when a beacon is due and
    every gate passed (TX not inhibited, no menu, no Kraken screen, selected side loaded)."""
    out = subprocess.run(["csky-abiv2-elf-objdump", "-d", f"--start-address={hook:#x}", f"--stop-address={hook + 0x200:#x}", str(elf2)],
                         capture_output=True, text=True, check=True).stdout
    ins = [l.split("\t") for l in out.splitlines() if l.startswith(" ") and ":\t" in l]
    for i, l in enumerate(ins):
        if l[2].strip() == "lrw" and "0x3054d80" in l[3]:
            for k in ins[i + 1:i + 4]:
                if k[2].strip() == "jsr":
                    return int(k[0].strip().rstrip(":"), 16)
    raise RuntimeError("krk_hook_aprs_tick: no call of event_queue_reset found")


def in_menu(mode):
    return 7 <= mode <= 0x3F or mode in (0x69, 0x77)


class Run:
    """One booted run with the breakpoint at `bp`; every hit records (OS tick, screen-mode byte, [0x40078])."""

    def __init__(self, patched, tag, seq, bp):
        self.rec, self.wall = [], []
        self.b = Boot(patched, f"bcn_{tag}", keys(seq), bp=bp, settle=600, on_hit=self._hit)

    def _hit(self, b):
        t = struct.unpack("<I", b.mem_halted(OS_TICKS, 4))[0]
        self.rec.append((t, b.mem_halted(UI_MODE, 1)[0], b.mem_halted(APRS_RT, 1)[0]))
        self.wall.append(time.time())                   # the guest is stopped here: host time of the hit

    def ticks(self):
        return self.b.u32(OS_TICKS)

    def at_standby(self, timeout=120):
        return self.b.until(lambda bb: bb.u8(HIST) == 0 and not in_menu(bb.u8(UI_MODE)), timeout=timeout)

    def dw_off(self):
        """Dual watch off (state 0) while the analog side A is loaded. The emulated receiver reads squelch open all the
        time, so the dual-watch engine keeps dwelling on the other side (state 2), and the stock loop skips the APRS
        block in that state: the hook would run only every 10-40 s and re-arm each time. On a radio, a quiet other side
        keeps state 1 and the hook runs about once a second."""
        b = self.b
        b._halt()                                       # read and write in one stop: the engine flips sides between two
        try:
            mode, cm = read_mem(b.c, UI_MODE, 1)[0], read_mem(b.c, CHAN_MODE, 1)[0]
            st, side = read_mem(b.c, DW_STATE, 2)                 # state; bit 0 of the next byte = the loaded side (0 = A)
            if not in_menu(mode):                       # (a menu pauses dual watch itself)
                if cm & 0xF0 == 0 and not side & 1 and st != 0:   # struct AND side bit on A: a later reload then picks A
                    b.c.command("M%x,1:00" % DW_STATE)
                elif cm & 0xF0 and st == 0:             # left on the digital side B: let dual watch bring A back
                    b.c.command("M%x,1:01" % DW_STATE)
        finally:
            b._go()

    def wait_ticks(self, n, keep=None):
        """Let the guest run until n OS ticks have passed (keep(): called about once a second, e.g. to re-plant)."""
        end = time.time() + n / HZ                  # HOST time: under stock power save the OS tick runs ~2x fast (QEMU's timer follows
        while True:                                 # stock's ch0 LoadCount 840000 <-> 240000), the RTC and krk_real_time keep real time
            self.b.pump(1.0)
            self.dw_off()
            if keep:
                keep()
            if time.time() >= end:
                return

    def hits_after(self, t0):
        return [r for r in self.rec if (r[0] - t0) & 0x80000000 == 0]

    def hit_walls(self, t0):
        """Host-time stamps (s) of the hits hits_after(t0) returns."""
        return [w for r, w in zip(self.rec, self.wall) if (r[0] - t0) & 0x80000000 == 0]

    def close(self):
        self.b.close()


def setup_ok(r, rep, name):
    """At standby after the menu walk, with APRS On and a 30 s interval stored. Returns the standby tick or None."""
    ok = r.b.until(lambda bb: bb.u8(HIST) == 0 and not in_menu(bb.u8(UI_MODE)) and bb.u8(APRS_RT) & 1
                   and bb.u16(APRS_INTERVAL) == IVL, timeout=150)
    for _ in range(10):                                 # dual watch off with side A loaded (see Run.dw_off)
        r.dw_off()
        if r.b.u8(DW_STATE) == 0:
            break
        r.b.pump(0.3)
    rt, ivl = r.b.u8(APRS_RT), r.b.u16(APRS_INTERVAL)
    rep(ok and rt & 1 and ivl == IVL, f"[{name}] set up through the menu: standby (mode {r.b.u8(UI_MODE):#x}), "
                                      f"[0x40078] bit 0 = {rt & 1}, interval {ivl} s (want 1, {IVL})")
    r.w0 = time.time()
    return r.ticks() if ok and rt & 1 and ivl == IVL else None


def sc_now(patched, s, s2, rep):
    r = Run(patched, "now", PATHS["aprs"] + " ok up ok", s["krk_hook_aprs_mode"])     # channel-change hook: counts channel changes
    try:
        r.b.wait_step(r.b.nsteps - 1)
        ok = r.b.until(lambda bb: bb.u8(APRS_RT) & 1 and bb.u8(UI_MODE) == 0x0E, timeout=30)
        menu_cc = [x for x in r.rec if in_menu(x[1])]
        rep(ok and not menu_cc, f"[now] APRS On from the menu: [0x40078] bit 0 set while the APRS list is still open "
                                f"(mode {r.b.u8(UI_MODE):#x}), with no channel change in the menu ({len(menu_cc)} hook hits with a menu mode)")
    finally:
        r.close()


def sc_standby(patched, s, s2, rep, plant=None, name="standby"):
    r = Run(patched, name, SETUP, s2["DUE"])
    try:
        r.b.wait_step(r.b.nsteps - 1)
        t0 = setup_ok(r, rep, name)
        if t0 is None:
            return
        keep = None
        if plant is not None:
            def keep():
                if r.b.u8(UI_MODE) != plant and r.b.u8(HIST) == 0:
                    r.b.poke(UI_MODE, bytes([plant]))
            keep()
        r.wait_ticks((2 * IVL + 12) * HZ, keep)
        h = r.hits_after(t0)
        rep(len(h) >= 2, f"[{name}] two due beacons within {2 * IVL + 12} s of standby ({len(h)}: "
                         f"{[(round((t - t0) / HZ, 1), hex(m)) for t, m, _ in h]} as (s after standby, mode))")
        if len(h) >= 2:
            hw = r.hit_walls(t0)
            d1, d2 = hw[0] - r.w0, hw[1] - hw[0]            # host seconds: the interval is real time (krk_real_time)
            rep(IVL - 6 <= d1 <= IVL + 8, f"[{name}] first due beacon {d1:.1f} s after standby was seen (one interval, {IVL} s; the timer arms at "
                                         f"the first tick at standby, which a loaded host sees a few seconds late)")
            w2 = (h[1][0] - h[0][0]) / HZ
            # krk_real_time keeps the interval on the RTC, which QEMU runs on host time: on a loaded host the guest tick
            # runs slow, so the tick-counted interval comes out a little short (seen: 28.7 s of ticks for 30 s)
            rep(IVL - 4 <= d2 <= IVL + 6, f"[{name}] second due beacon {d2:.1f} s host time ({w2:.1f} s of OS ticks) after the first (one interval)")
        if plant is not None:
            rep(h and all(m == plant for _, m, _ in h), f"[{name}] every due beacon ran with the screen-mode byte at {plant:#x} "
                                                         f"({[hex(m) for _, m, _ in h]})")
    finally:
        r.close()


def sc_menu(patched, s, s2, rep):
    r = Run(patched, "menu", SETUP + ":1800" + MENU_HOLD, s2["DUE"])     # about 15 s at standby, then the menu over the due time
    try:
        r.b.wait_step(r.b.nsteps - len(MENU_HOLD.split()) - 1)       # the walk's last Back is out
        t0 = setup_ok(r, rep, "menu")
        if t0 is None:
            return
        trace = []                                      # (tick, menu open) about once a second until the keys are out
        step0 = r.b.step()
        while r.b.step() < r.b.nsteps - 1 or (trace and trace[-1][1] and len(trace) < 400):
            r.b.pump(1.0)
            r.dw_off()
            trace.append((r.ticks(), in_menu(r.b.u8(UI_MODE))))
            if r.b.step() >= r.b.nsteps - 1 and not trace[-1][1]:
                break
        best, run0 = (0, 0, 0), None                    # the longest stretch with the menu open
        for t, m in trace + [(trace[-1][0] if trace else 0, False)]:
            if m and run0 is None:
                run0 = t
            elif not m and run0 is not None:
                best = max(best, (t - run0, run0, t))
                run0 = None
        held, ta, tb = best[0] / HZ, best[1], best[2]
        inside = [x for x in r.rec if (x[0] - ta) & 0x80000000 == 0 and (tb - x[0]) & 0x80000000 == 0]
        due = (t0 + IVL * HZ - ta) & 0x80000000 == 0 and (tb - t0 - IVL * HZ) & 0x80000000 == 0
        rep(held > 10 and due and not inside,
            f"[menu] menu open {held:.0f} s, from {(ta - t0) / HZ:.0f} s to {(tb - t0) / HZ:.0f} s after standby, over the moment the "
            f"beacon fell due ({IVL} s): no due beacon in it ({len(inside)} hits; key step {step0}/{r.b.nsteps - 1} at standby, "
            f"{len(trace)} samples, menu open in {sum(m for _, m in trace)})")
        # What happens after the menu is not checked here. In QEMU the menu's channel apply can land on side B (dual watch
        # on, B digital) and the emulator's dual-watch flips back to A do not re-apply the channel, so [0x40078] bit 0
        # and krk_channel_on_selected() stay on B and nothing more is due (docs/aprs-submenu.md 7.1, open item). The
        # re-arm after a gap is covered by cps_off, the prompt send after standby returns by mode69 and owner.
    finally:
        r.close()


def sc_hold(patched, s, s2, rep, name, hold, release, what):
    """At standby: hold() blocks for an interval and a half (re-applied every second), release() lifts it."""
    r = Run(patched, name, SETUP, s2["DUE"])
    try:
        r.b.wait_step(r.b.nsteps - 1)
        t0 = setup_ok(r, rep, name)
        if t0 is None:
            return
        hold(r)
        r.wait_ticks((IVL * 3 // 2 + 5) * HZ, lambda: hold(r))
        h = r.hits_after(t0)
        rep(not h, f"[{name}] {what} held {IVL * 3 // 2 + 5} s at standby: no due beacon ({len(h)} hits {[(hex(m), x) for _, m, x in h]})")
        release(r)
        tr = r.ticks()
        r.wr = time.time()
        r.wait_ticks((IVL + 12) * HZ)
        h = r.hits_after(tr)
        r.hw = [w - r.wr for w in r.hit_walls(tr)]
        return r, tr, h
    except Exception:
        r.close()
        raise


def sc_mode69(patched, s, s2, rep):
    def hold(r):
        if r.b.u8(UI_MODE) != 0x69:
            r.b.poke(UI_MODE, b"\x69")
    res = sc_hold(patched, s, s2, rep, "mode69", hold, lambda r: r.b.poke(UI_MODE, b"\x00"), "screen-mode byte 0x69")
    if res:
        r, tr, h = res
        try:
            d = r.hw[0] if h else None
            rep(h and d <= 8, f"[mode69] standby (0) back: the due beacon {d} s later")
        finally:
            r.close()


def sc_owner(patched, s, s2, rep):
    own = s["krk_screen_owner"]
    res = sc_hold(patched, s, s2, rep, "owner", lambda r: r.b.poke(own, struct.pack("<I", SCR_MAGIC)),
                  lambda r: r.b.poke(own, bytes(4)), "krk_screen_owner")
    if res:
        r, tr, h = res
        try:
            d = r.hw[0] if h else None
            rep(h and d <= 8, f"[owner] screen released: the due beacon {d} s later")
        finally:
            r.close()


def sc_cps(patched, s, s2, rep):
    def hold(r):
        v = r.b.u8(CPS_FLAGS)
        if v & 4:
            r.b.poke(CPS_FLAGS, bytes([v & ~4]))
    res = sc_hold(patched, s, s2, rep, "cps_off", hold, lambda r: r.b.poke(CPS_FLAGS, bytes([r.b.u8(CPS_FLAGS) | 4])),
                  "[0x3fdc0] bit 2 clear")
    if res:
        r, tr, h = res
        try:
            d = r.hw[0] if h else None
            rep(h and IVL - 6 <= d <= IVL + 8, f"[cps_off] bit set again: a due beacon {d} s later (one interval: re-armed)")
        finally:
            r.close()


SCEN = {
    "now": sc_now,
    "standby": sc_standby,
    "standby5b": lambda p, s, s2, rep: sc_standby(p, s, s2, rep, plant=0x5B, name="standby5b"),
    "menu": sc_menu,
    "mode69": sc_mode69,
    "owner": sc_owner,
    "cps_off": sc_cps,
}


def run(patched, report, only=None):
    s, s2 = syms(elf(1)), syms(elf(2))
    s2["DUE"] = due_site(elf(2), s2["krk_hook_aprs_tick"])
    names = [n for n in SCEN if not only or n in only]
    results = {n: [] for n in names}
    slots = threading.Semaphore(int(os.environ.get("BEACON_PARALLEL", "4")))

    def work(n):
        rep = lambda ok, msg: results[n].append((bool(ok), msg))
        with slots:
            try:
                SCEN[n](patched, s, s2, rep)
            except Exception as e:                      # a crashed scenario is a failure, not a hang
                rep(False, f"[{n}] scenario error: {type(e).__name__}: {e}")
    th = [threading.Thread(target=work, args=(n,)) for n in names]
    for t in th:
        t.start()
    for t in th:
        t.join()
    for n in names:
        if not results[n]:
            report(False, f"[{n}] no result")
        for ok, msg in results[n]:
            report(ok, msg)


def main():
    suite = Suite()
    only = os.environ.get("BEACON_ONLY", "").split(",") if os.environ.get("BEACON_ONLY") else None
    run(pathlib.Path(sys.argv[1]), suite, only)
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
