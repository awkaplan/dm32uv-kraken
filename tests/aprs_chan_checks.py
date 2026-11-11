"""F8b Beacon Chan (krk_aprs_chan.c, docs/aprs-submenu.md 7.6) in-target checks, called from kraken_ui_test.py on the halted
QEMU machine. Emulator only: nothing here ran on a radio.

The stock calls the switch makes are intercepted at their entries: ui_event_notify_alt 0x030305ac (the 0x16 "load the report
channel" and 0x17 "return" reloads; the fake writes the struct, live TX word and last-loaded number a real load would), the RX
retune 0x0300df08, os_time_dly_ms, the IRQ save/restore pair, the flash record read, and krk_aprs_tx_send itself. Checked:

  1. Off (no record, CPS FF, factory 00): the send is krk_aprs_tx_send unchanged, no reload at all.
  2. Set: 0x16 with our number in 0x4007a and 0x3e198 cleared; the RX retune; krk_aprs_tx_send runs with the beacon channel
     loaded and the switch marked active; then 0x17 with 0x3e198 and 0x4007a back; the state is idle afterwards. A failed send
     returns its result and still returns to the selected channel.
  3. Refusals after the load (digital, TX-forbidden, out of band, the load did not run with our number): nothing sent, 0x17 runs.
  4. Refusals before the load (menu open for the automatic beacon, VOX/repeater on, a send in progress, keyed, dual watch):
     no reload at all, the lock released.
  5. krk_aprs_chan_keep(): idle 1; switch holding the sender: 0 for every other task (claim() then says "Busy"), 1 for the
     owner only while the TX word, mode, TX-forbid bit and band bit still match.
  6. The guards in krk_aprs_tx.c: a CW ID send (krk_fm_send) while the switch holds the sender is "Busy" (1) and never keys;
     the beacon's own send that finds the TX word changed after key-up unkeys at once without switching the audio source;
     with nothing changed it sends.
"""
import struct

import mi_rng_test as m
import dw_checks as dwc
from target import fcall, r8, r16, r32, w8, w16, w32, cstr

NOTIFY, RETUNE, DLY, EQR = 0x030305AC, 0x0300DF08, 0x0300C4BC, 0x03054D80
IRQS, IRQR, FBREAD, FBRES, FBWRITE = 0x0001018C, 0x0001019C, 0x0300F71C, 0x0300F594, 0x0300F878
OSFLAG_POST, TX_INHIBIT, RX_ACTIVE, DW_STATE = 0x0300C494, 0x0302ED50, 0x0300F2A4, 0x030641E4
GPIO_SET, GPIO_CLR, FD_W, FD_R = 0x0002C500, 0x0002C544, 0x0002C2F4, 0x0002C33C
REPORT, LAST, TXW, RXW, CHRX, FLAGS8, BAND = 0x4007A, 0x3E198, 0x38468, 0x3846C, 0x3E1DC, 0x3E1E4, 0x3E31B
CALLACT, TCB, PSAVE = 0x3FDC8, 0x42500, 0x3EBF0
MODEM = 0x11000000
FM_SW, AUDC, DEVC = MODEM + 0x56C, MODEM + 0x80, MODEM + 0x504
REC_MAGIC, LOCK, ACT = 0x4E484342, 0x4B434C42, 0x4B434C43
TASK = 0x18001230                       # an arbitrary "current TCB"
ORIG = dict(rx=43912500, tx=43412500, f8=0x00, num=3)       # the selected channel: a UHF repeater (memory channel 3)
BEAC = dict(rx=14439000, tx=14439000, f8=0x00, num=12)      # the beacon channel: 144.390 simplex (memory channel 12)


def rec(ch, magic=REC_MAGIC):
    return struct.pack("<IHH8x", magic, ch & 0xFFFF, ~ch & 0xFFFF)


class Fx:
    def __init__(self, c, s, s2):
        self.c, self.s, self.s2 = c, s, s2
        self.record = rec(0, 0)
        self.load = dict(BEAC)          # what the 0x16 load puts in (tests vary it)
        self.load_runs = True
        self.calls = []
        self.send_ret = 0
        self.vox = self.menu = self.busy = 0
        self.side_ok = 1

    def put(self, ch):
        w32(self.c, CHRX, ch["rx"])
        w32(self.c, CHRX + 4, ch["tx"])
        w8(self.c, FLAGS8, ch["f8"])
        w32(self.c, TXW, ch["tx"])
        w32(self.c, RXW, ch["rx"])
        w16(self.c, LAST, ch["num"])

    def env(self):
        dwc.chan_poke(self.c, 0x00, rx=ORIG["rx"], tx=ORIG["tx"])
        self.put(ORIG)
        w16(self.c, REPORT, 0x0007)                     # stock's DMR report channel cache: must come back unchanged
        w8(self.c, BAND, 0)
        w8(self.c, CALLACT, 0)
        w32(self.c, TCB, TASK)
        w32(self.c, self.s["krk_screen_owner"], 0)
        m.wmem(self.c, self.s2["krk_bchan"], b"\xA5" * 12)     # SRAM noise: not a magic, so idle

    def st(self):
        return r32(self.c, self.s2["krk_bchan"])

    def notify(self, regs, r0, r1):
        self.calls.append(("notify", r0, r16(self.c, REPORT), r16(self.c, LAST)))
        if r0 == 0x16 and self.load_runs:
            self.put(self.load)
            w8(self.c, BAND, self.load.get("band", 0))
        elif r0 == 0x17:
            self.put(ORIG)
            w8(self.c, BAND, 0)
        return 0

    def fbread(self, regs, r0, r1):
        m.wmem(self.c, regs["r2"], self.record)
        return 0

    def tx_send(self, regs, r0, r1):
        self.calls.append(("send", r32(self.c, TXW), r8(self.c, FLAGS8), self.st(), r32(self.c, RXW)))
        w32(self.c, r0, self.s2["krk_bchan"])           # *why: any valid pointer
        return self.send_ret & 0xFFFFFFFF

    def run(self, automatic=0):
        self.calls = []
        why = m.STACK_A - 0x100
        kind = why + 4
        w32(self.c, why, 0)
        s2 = self.s2
        fakes = {NOTIFY: self.notify, RETUNE: lambda r, a, b: self.calls.append(("retune", r32(self.c, RXW))) or 0,
                 DLY: 0, EQR: 0, IRQS: 0x80000140, IRQR: 0, FBREAD: self.fbread, FBRES: 4,
                 FBWRITE: lambda r, a, b: self.calls.append(("write",)) or 0,
                 s2["krk_aprs_tx_send"]: self.tx_send, s2["krk_vox_tag"]: lambda r, a, b: self.vox,
                 s2["krk_ui_in_menu"]: lambda r, a, b: self.menu, s2["krk_aprs_tx_busy"]: lambda r, a, b: self.busy,
                 s2["krk_on_selected_side"]: lambda r, a, b: self.side_ok}
        _, ret, abi, _ = fcall(self.c, s2["krk_aprs_chan_send"], (why, kind, automatic), fakes=fakes)
        wp = r32(self.c, why)
        return ret if ret < 0x80000000 else ret - (1 << 32), abi, (cstr(self.c, wp).decode("latin-1") if wp else None)

    def kinds(self):
        return [x[0] if x[0] != "notify" else f"n{x[1]:x}" for x in self.calls]


def check_aprs_chan(c, s, s2):
    print("== F8b Beacon Chan: the beacon switches to a set memory channel, sends, returns (krk_aprs_chan.c)")
    fx = Fx(c, s, s2)

    # 1. Off
    for name, r in (("no record", rec(0, 0)), ("vendor CPS write (FF)", b"\xFF" * 16), ("factory reset (00)", bytes(16)),
                    ("record says 0", rec(0)), ("bad inverse", struct.pack("<IHH8x", REC_MAGIC, 12, 12))):
        fx.env()
        fx.record = r
        fx.send_ret = 0
        ret, abi, _ = fx.run()
        m.report(abi and ret == 0 and fx.kinds() == ["send"] and fx.calls[0][1] == ORIG["tx"] and r16(c, REPORT) == 7,
                 f"Off ({name}): krk_aprs_tx_send on the selected channel only, no reload: {fx.kinds()}")

    # 2. Set: the full sequence
    fx.env()
    fx.record = rec(12)
    fx.load = dict(BEAC)
    fx.send_ret = 0
    ret, abi, _ = fx.run()
    k = fx.kinds()
    m.report(abi and ret == 0 and k == ["n16", "retune", "send", "n17"],
             f"set to 12: load 0x16, RX retune, send, return 0x17, in that order: {k}")
    n16 = fx.calls[0]
    m.report(n16[2] == 12 and n16[3] == 0, f"0x16 load: report channel cell 0x4007a = {n16[2]} (want 12), last-loaded 0x3e198 cleared ({n16[3]})")
    snd = [x for x in fx.calls if x[0] == "send"][0]
    m.report(snd[1] == BEAC["tx"] and snd[3] == ACT and fx.calls[1][1] == BEAC["rx"],
             f"during the send: TX word {snd[1]} (beacon {BEAC['tx']}), RX retuned to {fx.calls[1][1]}, switch active {snd[3] == ACT}")
    n17 = fx.calls[-1]
    m.report(n17[3] == ORIG["num"] and n17[2] == 7 and r16(c, REPORT) == 7 and r32(c, TXW) == ORIG["tx"] and fx.st() == 0,
             f"0x17 return with 0x3e198 = {n17[3]} (original {ORIG['num']}) and 0x4007a = {n17[2]} (stock's, 7); after: TX word "
             f"{r32(c, TXW)}, state idle {fx.st() == 0}")
    fx.env()
    fx.send_ret = -1
    ret, abi, _ = fx.run()
    m.report(abi and ret == -1 and fx.kinds() == ["n16", "retune", "send", "n17"] and r32(c, TXW) == ORIG["tx"] and fx.st() == 0,
             f"failed send (busy channel, TX did not come up...): -1 and still back on the selected channel: {fx.kinds()}")

    # 3. refusals after the load
    for name, load in (("digital", dict(BEAC, f8=0x10)), ("TX-forbidden", dict(BEAC, f8=0x08)), ("out of the TX bands", dict(BEAC, band=0x20)),
                       ("load did not run with our number", dict(BEAC, num=13))):
        fx.env()
        fx.load = load
        ret, abi, why = fx.run()
        m.report(abi and ret == -1 and fx.kinds() == ["n16", "n17"] and r32(c, TXW) == ORIG["tx"] and fx.st() == 0 and why == "Analog channel only",
                 f"beacon channel {name}: nothing sent, returned at once ({fx.kinds()}, {why!r})")
    fx.load = dict(BEAC)

    # 4. refusals before the load
    for name, setup, auto in (("automatic beacon, menu open", ("menu", 1), 1), ("VOX / repeater armed", ("vox", 1), 0),
                              ("a send in progress", ("busy", 1), 0), ("dual watch would not settle", ("side_ok", 0), 0)):
        fx.env()
        fx.menu = fx.vox = fx.busy = 0
        fx.side_ok = 1
        setattr(fx, setup[0], setup[1])
        ret, abi, why = fx.run(auto)
        m.report(abi and ret == -1 and "n16" not in fx.kinds() and "send" not in fx.kinds() and fx.st() not in (LOCK, ACT) and r32(c, TXW) == ORIG["tx"],
                 f"{name}: no reload, no send, lock free ({fx.kinds()}, {why!r})")
    fx.menu = fx.vox = fx.busy = 0
    fx.side_ok = 1
    fx.env()
    w8(c, CALLACT, 0x08)
    ret, abi, why = fx.run()
    m.report(ret == -1 and not fx.calls and why == "Busy" and fx.st() not in (LOCK, ACT), f"keyed (PTT/VOX TX up): no reload ({why!r})")
    fx.env()
    w32(c, s["krk_screen_owner"], 0x53435252)
    ret, _, why = fx.run(1)
    m.report(ret == -1 and not fx.calls, f"automatic beacon with one of our screens open: no reload ({why!r})")
    fx.send_ret = 0
    ret, _, _ = fx.run(0)
    m.report(ret == 0 and fx.kinds() == ["n16", "retune", "send", "n17"], f"manual send from a screen (Upload Beacon): switches ({fx.kinds()})")

    # 5. keep()
    def keep(st, owner=TASK, tx=BEAC["tx"]):
        m.wmem(c, s2["krk_bchan"], struct.pack("<III", st, tx, owner))
        _, r, _, _ = fcall(c, s2["krk_aprs_chan_keep"])
        return r
    fx.env()
    fx.put(BEAC)
    res = dict(idle=keep(0), noise=keep(0xA5A5A5A5), lock=keep(LOCK), act=keep(ACT), other=keep(ACT, owner=TASK + 0x40),
               lock_other=keep(LOCK, owner=TASK + 0x40), txchg=keep(ACT, tx=BEAC["tx"] + 1250))
    w8(c, FLAGS8, 0x10)
    res["digital"] = keep(ACT)
    w8(c, FLAGS8, 0x08)
    res["noTX"] = keep(ACT)
    w8(c, FLAGS8, 0)
    w8(c, BAND, 0x20)
    res["band"] = keep(ACT)
    w8(c, BAND, 0)
    m.report(res == dict(idle=1, noise=1, lock=0, act=1, other=0, lock_other=0, txchg=0, digital=0, noTX=0, band=0),
             f"krk_aprs_chan_keep: {res}")

    # 6. the guards in krk_aprs_tx.c
    posts, fdw = [], []

    def post(regs, r0, r1):
        posts.append(r1)
        if r1 == 0x08:
            w8(c, CALLACT, r8(c, CALLACT) | 0x08)
            if fx.retune_on_key:
                w32(c, TXW, ORIG["tx"])             # something reloaded the selected channel just as TX came up
        if r1 == 0x10:
            w8(c, CALLACT, r8(c, CALLACT) & ~0x08)
        return 0
    n = [0]

    def dly(regs, r0, r1):                          # the FM Send RAM engine: flip the half index so the stream makes progress
        n[0] += 1
        w32(c, FM_SW, 1 if n[0] % 2 else 2)
        return 0
    fd47 = [0x1234]

    def fd_write(regs, r0, r1):
        fdw.append((r0, r1))
        if r0 == 0x47:
            fd47[0] = r1
        return 0
    call_a = m.STACK_A - 0x200

    def call_get(regs, r0, r1):
        m.wmem(c, r0, b"N0CALL\0")
        return 1
    base = {OSFLAG_POST: post, DLY: dly, EQR: 0, TX_INHIBIT: 0, RX_ACTIVE: 0, DW_STATE: 0, GPIO_SET: 0, GPIO_CLR: 0, FD_W: fd_write,
            FD_R: lambda r, a, b: fd47[0] if a == 0x47 else 0, s2["krk_mon_yield"]: 0, s["krk_gps_get"]: 0,
            s2["krk_call_get"]: call_get, s2["krk_aprs_cfg_path"]: 0}

    def send(fn, args, st, retune=False):
        fx.env()
        fx.put(BEAC)
        dwc.chan_poke(c, 0x00, rx=BEAC["rx"], tx=BEAC["tx"])
        w32(c, TXW, BEAC["tx"])
        w8(c, PSAVE + 1, 0)
        w32(c, AUDC, 0x1234)
        w32(c, FM_SW, 0)
        m.wmem(c, s2["krk_bchan"], struct.pack("<III", st, BEAC["tx"], TASK))
        fx.retune_on_key = retune
        posts.clear()
        fdw.clear()
        n[0] = 0
        w32(c, call_a, 0)
        _, r, abi, _ = fcall(c, fn, args, fakes=base, limit=20000)
        wp = r32(c, args[2] if fn == s2["krk_fm_send"] else args[0])
        return (r if r < 0x80000000 else r - (1 << 32)), abi, (cstr(c, wp).decode("latin-1") if wp else None)

    fill = s2["krk_aprs_tx_send"]                   # any code address: krk_fm_send must not reach the fill at all here
    r, abi, why = send(s2["krk_fm_send"], (fill, 0, call_a), LOCK)
    m.report(abi and r == 1 and why == "Busy" and not posts, f"CW ID (krk_fm_send) while the switch holds the sender: {r}, {why!r}, posts {posts}")
    r, abi, why = send(s2["krk_aprs_tx_send"], (call_a, call_a + 4), ACT, retune=True)
    m.report(abi and r == -1 and why == "Aborted" and posts[:2] == [0x08, 0x10] and (0x47, 0x6740) not in fdw and r32(c, AUDC) == 0x1234,
             f"beacon keyed but the TX word changed at key-up: unkeyed at once, audio source never switched ({why!r}, posts {posts})")
    r, abi, why = send(s2["krk_aprs_tx_send"], (call_a, call_a + 4), ACT)
    m.report(abi and r == 0 and why in ("Sent", "Sent, re-routed") and posts[0] == 0x08 and posts[-1] == 0x10,
             f"beacon on the beacon channel, nothing changed: sends ({why!r})")
    m.wmem(c, s2["krk_bchan"], bytes(12))


def boot_checks(patched, s, s2, report=None):
    """Booted runs with the generic codeplug (analog VFO A 144.125, memory channels 1..9 DMR, 10..16 analog, 17 on empty): the
    real stock 0x16 / 0x17 reloads. Beacon Chan is typed in the stock numeric entry, then Upload Beacon (OK, OK)."""
    import re
    from build_artifacts import REPO
    import threading
    from qemu_menu import PATHS, Boot, keys
    report = report or m.report
    core = (REPO / "patches/kraken_ui/krk_core.c").read_text()
    analog = [t.strip() for t in re.search(r"aprs_analog_items\[\d*\]\s*=\s*\{([^}]*)\}", core)[1].split(",")]
    row_chan, row_up = analog.index("KRK_APRS_ITEM_CHAN"), analog.index("KRK_APRS_ITEM_UPLOAD")
    walk = lambda digits: (PATHS["aprs"] + " down" * row_chan + " ok " + " ".join("k" + d for d in digits) + " ok:1500"
                           + " up" * (row_chan - row_up) + " ok:60 ok")
    VFO_A = 14412500
    seen = {}

    def at_send(tag, b):                                # the guest is stopped at krk_aprs_tx_send, inside the switch
        seen.setdefault(tag, []).append(dict(rx=struct.unpack("<I", b.mem_halted(CHRX, 4))[0], txw=struct.unpack("<I", b.mem_halted(TXW, 4))[0],
                                               f8=b.mem_halted(FLAGS8, 1)[0], last=struct.unpack("<H", b.mem_halted(LAST, 2))[0],
                                               st=struct.unpack("<I", b.mem_halted(s2["krk_bchan"], 4))[0]))

    def at_notify(tag, b):
        seen.setdefault(tag, []).append(b.regs()["r0"] & 0xFF)

    def after(b):
        b.pump(8)                                       # the send (or the refusal popup) and the 0x17 return are over
        return struct.unpack("<I", b.mem(CHRX, 4))[0], struct.unpack("<I", b.mem(s2["krk_bchan"], 4))[0], b.mem(FLAGS8, 1)[0]

    def c_send(tag, b, rep):
        rx, st, f8 = after(b)
        got = seen.get(tag, [])
        rep(len(got) == 1 and got[0]["rx"] == 14402250 and got[0]["txw"] == 14402250 and got[0]["last"] == 15 and got[0]["f8"] & 0xF8 == 0
            and got[0]["st"] == ACT, f"Beacon Chan 15, Upload Beacon: at the send the radio is on channel 15 (144.0225 analog): {got}")
        rep(rx == VFO_A and st not in (LOCK, ACT) and f8 & 0xF0 == 0, f"after the send: back on VFO A {rx} (want {VFO_A}), switch idle")

    def c_refuse(want):
        def f(tag, b, rep):
            rx, st, _ = after(b)
            got = seen.get(tag, [])
            ours = [x for x in got if x in (0x16, 0x17)]      # stock reloads (0x13: the selected side) may come too
            rep(ours == [0x16, 0x17] and rx == VFO_A and st not in (LOCK, ACT),
                f"Beacon Chan {want}: reloads {[hex(x) for x in got]} (ours: 0x16 then 0x17, no send), back on VFO A {rx}")
        return f
    scen = {"bc_send": (walk("15"), s2["krk_aprs_tx_send"], c_send),
            "bc_digital": (walk("5"), NOTIFY, c_refuse("5 (DMR)")),
            "bc_empty": (walk("26"), NOTIFY, c_refuse("26 (empty slot)"))}
    hooks = {"bc_send": at_send, "bc_digital": at_notify, "bc_empty": at_notify}
    results = {}

    def work(tag):
        res = results[tag] = []
        rep = lambda ok, msg: res.append((bool(ok), msg))
        seq, bp, check = scen[tag]
        b = None
        try:
            b = Boot(patched, tag, keys(seq), bp=bp, on_hit=lambda bb: hooks[tag](tag, bb))
            rep(b.wait_step(b.nsteps - 1), f"[{tag}] all {b.nsteps} key steps delivered")
            check(tag, b, rep)
        except Exception as e:
            rep(False, f"[{tag}] scenario error: {type(e).__name__}: {e}")
        finally:
            if b is not None:
                b.close()
    th = [threading.Thread(target=work, args=(t,)) for t in scen]
    for x in th:
        x.start()
    for x in th:
        x.join()
    for t in scen:
        for ok, msg in results.get(t, []):
            report(ok, msg)


def main():
    """standalone: python3 tests/aprs_chan_checks.py   (inside the build image with the workspace mounted, after a build)"""
    import sys
    from build_artifacts import syms
    from target import halted_machine
    ut = __import__("kraken_ui_test")
    if "--boot-only" not in sys.argv:
        with halted_machine(ut.PATCHED) as c:
            check_aprs_chan(c, syms(ut.ELF), syms(ut.ELF2))
    if "--no-boot" not in sys.argv:
        boot_checks(ut.PATCHED, syms(ut.ELF), syms(ut.ELF2))
    print("ALL PASS" if m.ok else "FAILURES")
    sys.exit(0 if m.ok else 1)


if __name__ == "__main__":
    main()
