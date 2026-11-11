"""F14 RX Monitor in-target checks (patches/kraken_ui/krk_aprsrx.c krk_mon_*, krk_periodic.c krk_hook_bg_tick,
krk_core.c krk_run_screen, krk_aprs_tx.c), called from kraken_ui_test.py on the halted QEMU machine.

QEMU has no FD6818 audio: the FM Receive RAM does not retain data and FM_ADDR_SW does not toggle. The two hardware reads of
the half consumer (mon_fm_sw, mon_half_ptr) are therefore intercepted at their entries and fed from the test: FM_ADDR_SW
toggles 1/2 per tick and the "half" pointer is a scratch SRAM buffer holding the next 256 samples (big endian, as the FM RAM
has them) of tests/data/capture16_f1.pcm, a REAL over-the-air frame (DM-32 FM Send RAM TX received by the other DM-32). The
decoder, the station ring and every register/GPIO write are the real code. Stock calls (gpio_set/clear_output_bit, the PTT
pin read, the stock prescaler) are intercepted and recorded.

  1. static: the splice at 0x03013f48 (stock bytes, target), hook table slot 3, the SRAM budget
  2. off by default / noise SRAM: no write of any kind
  3. state machine: settle -> arm (GPIO 0x2F low, AUDIO_CONTROL bit 6 set) -> run -> release on every gate
  4. real packet through the monitor into the shared ring; dedupe; the gap re-arm
  5. reboot detection, krk_mon_yield, krk_mon_set(0), the screen runner flag, the beacon yield
  6. the stations screen as a viewer: no key toggles (the RX Monitor option row does), the ring survives a visit while the monitor is on
  7. stack depth of the deepest pass on a canary-filled stack
"""
import struct

from build_artifacts import HOOK_TBL, REPO, stock_is      # first: puts the tool directories on sys.path
import mi_rng_test as m
from claims import claim
from gdb_rsp import read_mem
from qemu_menu import OS_TICKS
from target import fcall, r32, w32

GPIO_SET, GPIO_CLR, PTT_PIN, STOCK_PRESC = 0x0002C500, 0x0002C544, 0x03012F48, 0x030546DC
AUDIO_CONTROL = 0x11000080
VFO_FLAGS8, LOADFLAGS, CALLACT = 0x0003E1E4, 0x0003E31C, 0x0003FDC8
MON_MAGIC, RING_MAGIC, SCR_MAGIC, TXB_MAGIC = 0x4D4F4E31, 0x52494E47, 0x53435252, 0x42555359
SCRATCH = 0x18006000                  # a half buffer in the (idle here) shared F9/F13 buffer
STK_LO, STK_HI = 0x0002F0A0, 0x0002F800   # free IRAM below the test stack: canary-filled for the depth measurement
KEY_HASH = 0x0E


def check_mon(c, s, s2, img, stock, dwc):
    rb = lambda a, n=1: read_mem(c, a, n)
    w8 = lambda a, v: m.wmem(c, a, bytes([v & 0xFF]))
    M, R, RING = s2["M"], s2["R"], s2["ring"]
    # struct M: u32 magic, ring_magic, last_tick, halves; u8 st, own6, settle      struct R: u8 n; ...
    O_MAGIC, O_RING, O_LAST, O_HALVES, O_ST, O_OWN6, O_SETTLE = 0, 4, 8, 12, 16, 17, 18
    st = lambda: rb(M + O_ST)[0]
    own6 = lambda: rb(M + O_OWN6)[0]
    magic = lambda: r32(c, M + O_MAGIC)
    halves = lambda: r32(c, M + O_HALVES)
    bit6 = lambda: (r32(c, AUDIO_CONTROL) >> 6) & 1
    ring_n = lambda: rb(R)[0]
    owner = s["krk_screen_owner"]

    # ---- 1. static -----------------------------------------------------------------------------
    from build_patch import decode_branch
    print("== F14 RX Monitor: splice, hook slot, budget")
    off = 0x03013F48 - 0x03000000
    m.report(stock_is(0x03013F48, "02 e0 ca 03 00 e8 85 fd"), f"stock bytes at 0x03013f48: {stock[off:off + 8].hex(' ')} (want bsr 0x030546dc then br32 0x03013a56)")
    kind, half = decode_branch(img[off:off + 4])
    m.report(kind == "bsr" and 0x03013F48 + half * 2 == s2["krk_hook_bg_tick"], f"bg_tick_hook: {kind} at 0x03013f48 -> {0x03013F48 + half * 2:#x} (want krk_hook_bg_tick {s2['krk_hook_bg_tick']:#x})")
    m.report(img[off + 4:off + 8] == stock[off + 4:off + 8], "bg_tick_hook: the br32 back to the OSFlagPend (0x03013f4c) is untouched")
    slot3 = struct.unpack("<I", img[HOOK_TBL - 0x03000000 + 12:HOOK_TBL - 0x03000000 + 16])[0]
    m.report(slot3 == s2["krk_mon_yield"], f"hook table slot 3 = krk_mon_yield ({slot3:#x} vs {s2['krk_mon_yield']:#x})")
    (alo, ahi), (ulo, uhi) = claim("kraken_afsk_state"), claim("kraken_ui_state")
    m.report(alo <= R and M + 20 <= ahi and s2["M"] == R + 20 and M >= alo,
             f"monitor state M {M:#x}+20 B inside the decoder claim {alo:#x}-{ahi:#x} (R {R:#x}, no ui2 .bss used)")
    m.report(owner >= ulo and owner + 4 <= uhi, f"krk_screen_owner {owner:#x} inside kraken_ui_state (main blob .bss)")

    # ---- harness ---------------------------------------------------------------------------------
    st_ = {"ptt": 0, "sw": 1, "half": SCRATCH, "now": 100000}
    rec = []

    def fk_set(regs, r0, r1):
        rec.append(("set", r0))
        return 0

    def fk_clr(regs, r0, r1):
        rec.append(("clr", r0))
        return 0

    def fk_ptt(regs, r0, r1):
        rec.append(("ptt", 0))
        return st_["ptt"]

    def fk_sw(regs, r0, r1):
        rec.append(("sw", 0))
        return st_["sw"]

    def fk_half(regs, r0, r1):
        rec.append(("half", r0))
        return st_["half"]

    def fk_presc(regs, r0, r1):
        rec.append(("presc", 0))
        return 0
    fakes = {GPIO_SET: fk_set, GPIO_CLR: fk_clr, PTT_PIN: fk_ptt, s2["mon_fm_sw"]: fk_sw, s2["mon_half_ptr"]: fk_half, STOCK_PRESC: fk_presc}

    def run(fn, args=(), now=None):
        del rec[:]
        if now is not None:
            w32(c, OS_TICKS, now)
        stops, r0, abi, _ = fcall(c, fn, args, fakes=fakes)
        return [e for e in rec if e[0] in ("set", "clr")], abi, r0

    def tick(advance=1):
        st_["now"] += advance
        return run(s2["krk_mon_tick"], now=st_["now"])

    def clear_gates():
        w32(c, owner, 0)
        w32(c, s2["tx_busy"], 0)
        w8(CALLACT, 0)
        w8(LOADFLAGS, 0)
        st_["ptt"] = 0
        dwc.chan_poke(c, b"\x00")                      # analog, selected side live

    # ---- 2. off by default ---------------------------------------------------------------------
    print("== F14 RX Monitor: off by default, noise SRAM")
    clear_gates()
    m.wmem(c, M, b"\xA5" * 20)                          # power-up noise (also st = 0xa5, own6 = 0xa5)
    w32(c, AUDIO_CONTROL, 0x00000001)
    stops_, abi, _ = tick()
    m.report(stops_ == [] and abi and r32(c, AUDIO_CONTROL) == 1 and rb(M, 20) == b"\xA5" * 20 and not any(e[0] in ("ptt", "sw", "half") for e in rec),
             "noise state: a tick touches nothing (no GPIO, no AUDIO_CONTROL, no state, no PTT/FM reads)")
    m.wmem(c, M, bytes(20))
    stops_, abi, _ = tick()
    m.report(stops_ == [] and abi and r32(c, AUDIO_CONTROL) == 1, "zero state (magic absent): same, Monitor is Off")
    del rec[:]
    stops_, abi, _ = run(s2["krk_hook_bg_tick"], now=st_["now"])
    m.report([e[0] for e in rec] == ["presc"] and abi, f"hook with the Monitor off: the stock prescaler call only {[e[0] for e in rec]}, ABI kept")

    # ---- 3. state machine ----------------------------------------------------------------------
    print("== F14 RX Monitor: arm, run, release on every gate")
    m.wmem(c, M, b"\xA5" * 20)
    m.wmem(c, RING, b"\xA5" * 160)
    m.wmem(c, R, b"\xA5" * 20)
    st_["now"] = 5000
    w32(c, OS_TICKS, st_["now"])
    stops_, abi, _ = run(s2["krk_mon_set"], (1,))
    m.report(abi and magic() == MON_MAGIC and st() == 0 and own6() == 0 and rb(M + O_SETTLE)[0] == 0 and halves() == 0 and r32(c, M + O_RING) == RING_MAGIC
             and ring_n() == 0 and stops_ == [],
             f"krk_mon_set(1): magic, st 0, own6 0, halves 0, ring initialised from noise (n {ring_n()}), no hardware access")
    m.report(all(rb(RING + 20 * i + 2)[0] == 0 and rb(RING + 20 * i + 12)[0] == 0 and rb(RING + 20 * i, 2) == b"\0\0" for i in range(8)),
             "  all 8 ring entries cleared (sec 0, call and info empty)")
    w32(c, AUDIO_CONTROL, 0x00000081)                   # some other bits set; only bit 6 may change
    clear_gates()
    seq = []
    for i in range(3):
        stops_, abi, _ = tick()
        seq.append((stops_, st()))
    m.report(all(a == [] and b == 0 for a, b in seq) and rb(M + O_SETTLE)[0] == 3 and bit6() == 0 and abi,
             "analog, no gate: three settle passes (30 ms) do nothing to the hardware")
    stops_, abi, _ = tick()
    m.report(stops_ == [("clr", 0x2F)] and st() == 1 and own6() == 1 and bit6() == 1 and r32(c, AUDIO_CONTROL) == 0x000000C1 and abi,
             f"fourth pass arms: GPIO 0x2F cleared once ({stops_}), AUDIO_CONTROL {r32(c, AUDIO_CONTROL):#x} (bit 6 added, others kept), st 1, own6 1")
    stops_, abi, _ = tick()
    m.report(stops_ == [] and st() == 1, "armed, no new half: nothing written")

    def armed():
        """Back to ARMED from whatever state (gates clear)."""
        clear_gates()
        for _ in range(8):
            tick()
            if st() == 1:
                return True
        return False

    def gate_case(name, setter, undo, expect_set, digital=False):
        assert armed(), name
        before = r32(c, AUDIO_CONTROL)
        setter()
        stops_, abi, _ = tick()
        first = stops_
        quiet = all(tick()[0] == [] for _ in range(5))
        released = st() == 0 and own6() == 0 and bit6() == 0 and r32(c, AUDIO_CONTROL) == (before & ~0x40)
        want = [("set", 0x2F)] if expect_set else []
        m.report(first == want and quiet and released and abi,
                 f"{name}: released on the next pass ({first}, want {want}), bit 6 cleared, other AUDIO_CONTROL bits kept, then idempotent")
        undo()
        n = 0
        got = []
        while st() != 1 and n < 8:
            got += tick()[0]
            n += 1
        m.report(st() == 1 and got == [("clr", 0x2F)] and bit6() == 1 and n == 4, f"  gate cleared: re-armed after {n} passes ({got})")
    gate_case("Kraken screen running (runner flag)", lambda: w32(c, owner, SCR_MAGIC), lambda: w32(c, owner, 0), True)
    gate_case("analog beacon send (tx_busy)", lambda: w32(c, s2["tx_busy"], TXB_MAGIC), lambda: w32(c, s2["tx_busy"], 0), True)
    gate_case("keyed (call_active bit 3)", lambda: w8(CALLACT, 0x08), lambda: w8(CALLACT, 0), True)

    def press():
        st_["ptt"] = 1

    def unpress():
        st_["ptt"] = 0
    gate_case("PTT pin down", press, unpress, True)
    gate_case("loaded channel digital (dual-watch flip / channel change): NO GPIO 0x2F write", lambda: dwc.chan_poke(c, b"\x10"), lambda: dwc.chan_poke(c, b"\x00"), False)
    gate_case("channel load in progress: NO GPIO 0x2F write (stock writes it)", lambda: w8(LOADFLAGS, 0x08), lambda: w8(LOADFLAGS, 0), False)
    # a gate that opens and closes between two passes (not observed) -> the next half still re-asserts
    assert armed()
    w32(c, AUDIO_CONTROL, r32(c, AUDIO_CONTROL) & ~0x40)    # stock cleared bit 6 behind our back
    w8(M + O_OWN6, 0)
    st_["sw"] = 2 if st_["sw"] == 1 else 1
    w32(c, SCRATCH, 0)
    stops_, abi, _ = tick()
    m.report(("clr", 0x2F) in stops_ and bit6() == 1 and abi, "a finished half re-asserts GPIO 0x2F every half and restores bit 6 if stock dropped it")

    # ---- 4. a real packet through the monitor into the shared ring ----------------------------------
    print("== F14 RX Monitor: real on-air frame (capture16) through the monitor into the shared ring")
    pcm = {}
    for tag in ("f1", "f2"):
        raw = (REPO / f"tests/data/capture16_{tag}.pcm").read_bytes()
        n = len(raw) // 2
        smp = struct.unpack(f"<{n}h", raw)
        smp = smp + (0,) * ((-n) % 256)
        pcm[tag] = [b"".join(struct.pack(">h", v) for v in smp[k:k + 256]) for k in range(0, len(smp), 256)]
    ring_before = ring_n()
    import random
    rnd = random.Random(7)

    def noise_halves(k, rms=1000):
        """Receiver noise between packets: the decoder needs some level to listen to (host experiment: after 1 s of zeros or
        noise of rms 100 it misses the next frame; rms >= 1000 or a recent signal is fine; the frame itself is rms 300)."""
        return [b"".join(struct.pack(">h", max(-32768, min(32767, int(rnd.gauss(0, rms))))) for _ in range(256)) for _ in range(k)]

    def feed(halves_, gap_after=None, gap=0):
        """One tick per half (3 OS ticks apart, as 32 ms is). Returns the number of halves handed to the decoder."""
        n0 = halves()
        deepest = [0]
        for i, h in enumerate(halves_):
            m.wmem(c, SCRATCH, h)
            st_["sw"] = 2 if st_["sw"] == 1 else 1
            m.wmem(c, STK_LO, bytes([0xA5]) * (STK_HI - STK_LO))
            tick(3 + (gap if gap_after is not None and i == gap_after else 0))
            used = STK_HI - STK_LO
            sk = rb(STK_LO, STK_HI - STK_LO)
            lo = next((j for j, b in enumerate(sk) if b != 0xA5), used)
            deepest[0] = max(deepest[0], used - lo)
        return halves() - n0, deepest[0]
    assert armed()
    run(s2["krk_mon_yield"])                       # a clean arm: the decoder is reset when the monitor arms
    assert armed()
    feed(noise_halves(30))                         # listening to the channel noise first
    w32(c, M + O_HALVES, 0)
    AL = s2["A"]                                    # F14b alert state (krk_aprs_alert.c): Alert on, nothing pending
    m.wmem(c, AL, struct.pack("<I", 0x414C5254) + bytes(20))
    got, depth1 = feed(pcm["f1"])
    call = rb(RING + 2, 10).split(b"\0")[0]            # struct stn { u16 sec; char call[10]; char info[8]; }
    info = rb(RING + 12, 8).split(b"\0")[0]
    m.report(rb(AL + 23)[0] == 1 and r32(c, AL + 12) == RING + 2 and r32(c, AL + 16) == RING + 12,
             "F14b: the decoded frame reached krk_aprs_alert_frame from harvest, after stn_add (pending, pointing at the new ring[0])")
    w32(c, AL, 0)
    m.report(got == len(pcm["f1"]) and ring_n() == ring_before + 1 and call == b"N0CALL" and info == b">DM-32U",
             f"frame 1 ({len(pcm['f1'])} halves, FM_ADDR_SW toggling): decoded into the ring, {ring_n()} station: {call!r} {info!r}")
    m.report(halves() == len(pcm["f1"]), f"  the monitor counted {halves()} halves")
    got, depth2 = feed(pcm["f2"])
    m.report(ring_n() == 1 and rb(RING + 2, 10).split(b"\0")[0] == b"N0CALL", "frame 2 from the same station: still one ring entry (dedupe by callsign), moved to the top")
    # the gap: lose 10 OS ticks (100 ms, > 6) in the middle of a frame -> the decoder is re-armed, the frame is not decoded
    m.wmem(c, R, bytes([0]))                            # empty the ring (R.n); entries stay but are beyond n
    cut = len(pcm["f1"]) // 2
    n_before = ring_n()
    feed(noise_halves(12))
    feed(pcm["f1"][:cut])
    feed(pcm["f1"][cut:], gap_after=0, gap=10)
    m.report(ring_n() == n_before, f"gap of 100 ms mid-frame: decoder re-armed, the torn frame is not entered (ring {n_before} -> {ring_n()})")
    feed(noise_halves(12))
    feed(pcm["f1"])
    m.report(ring_n() == n_before + 1, "  the next uninterrupted frame decodes again")
    print(f"     deepest stack of a monitor pass with a frame completing: {max(depth1, depth2)} bytes (task init stack 2880 B; 124 B in use at its OSFlagPend)")
    m.report(max(depth1, depth2) <= 900, f"stack depth of the monitor pass {max(depth1, depth2)} <= 900 bytes on a canary-filled stack (hook runs at loop level: 124 + this + 8 < 2880)")

    # ---- 5. reboot, yield, set(0), runner, beacon ------------------------------------------------------
    print("== F14 RX Monitor: reboot detection, yield, switch off, screen runner, beacon")
    assert armed()
    w32(c, M + O_LAST, st_["now"] + 50000)              # the tick counter is now far behind the last one seen: rebooted, SRAM kept
    stops_, abi, _ = tick()
    m.report(magic() == 0 and abi and stops_ == [], "OS tick counter went backwards (reboot with retained SRAM): Monitor falls back to Off, no hardware writes")
    w32(c, AUDIO_CONTROL, r32(c, AUDIO_CONTROL) & ~0x40)    # a real reboot resets the modem too
    run(s2["krk_mon_set"], (1,), now=st_["now"])
    assert armed()
    stops_, abi, _ = run(s2["krk_mon_yield"])
    m.report(stops_ == [("set", 0x2F)] and st() == 0 and bit6() == 0 and own6() == 0 and magic() == MON_MAGIC and abi,
             f"krk_mon_yield (runner / beacon): released synchronously ({stops_}), still switched on")
    stops_, abi, _ = run(s2["krk_mon_yield"])
    m.report(stops_ == [("set", 0x2F)] or stops_ == [], "  yield when already idle is harmless")
    assert armed()
    stops_, abi, _ = run(s2["krk_mon_set"], (0,))
    m.report(stops_ == [("set", 0x2F)] and magic() == 0 and bit6() == 0 and abi, f"krk_mon_set(0) while armed: released, Off ({stops_})")
    m.wmem(c, M, b"\xA5" * 20)
    stops_, abi, _ = run(s2["krk_mon_yield"])
    m.report(stops_ == [] and rb(M, 20) == b"\xA5" * 20, "krk_mon_yield on noise state (Monitor never switched on): no access")

    # the runner: flag set before enter(), hook 3 called, flag cleared on Back
    SEMP, SEMQ, SCANK, LCDR, FILL, POP, QPEND = 0x0300C500, 0x0300C53C, 0x03013660, 0x0301CAD0, 0x03017F54, 0x03036C00, 0x0306A1B8
    scr = m.STACK_A - 0x400
    enter_stub = s2["mon_fm_sw"]                         # any intercepted address works as the screen's enter()
    seen = {}
    m.wmem(c, scr, struct.pack("<IIIII", 0, enter_stub, 0, 0, 0))
    run(s2["krk_mon_set"], (1,), now=st_["now"])
    assert armed()
    w32(c, owner, 0)

    def fk_enter(regs, r0, r1):
        seen["owner"] = r32(c, owner)
        seen["st"] = st()
        seen["bit6"] = bit6()
        return 0
    left = [2]

    def qpend(regs, r0, r1):
        if left[0] > 0:
            left[0] -= 1
            return 0x38E29
        return 0
    rest = s["krk_category_entry"]
    fk = {SEMP: 0, SEMQ: 0, SCANK: 6, LCDR: 0, FILL: 0, POP: 0, rest: 0, QPEND: qpend, enter_stub: fk_enter, GPIO_SET: fk_set, GPIO_CLR: fk_clr}
    del rec[:]
    stops, _, abi, _ = fcall(c, s["krk_run_screen"], (scr, rest), fakes=fk)
    m.report(seen.get("owner") == SCR_MAGIC and seen.get("st") == 0 and seen.get("bit6") == 0 and ("set", 0x2F) in rec and abi,
             f"runner: inside enter() the flag is set, the monitor already released (st {seen.get('st')}, bit 6 {seen.get('bit6')}, GPIO set {('set', 0x2F) in rec})")
    m.report(r32(c, owner) == 0, "runner: flag cleared when the screen returns (Back)")
    left[0] = 0
    # a second runner entry while nothing is armed must not touch the hardware
    run(s2["krk_mon_set"], (0,))

    # beacon: krk_aprs_tx_send releases the monitor before anything else (TX inhibited -> returns at once)
    run(s2["krk_mon_set"], (1,), now=st_["now"])
    assert armed()
    dwc.chan_poke(c, b"\x00")
    TX_INH, ANALOG_FLAGS = 0x0302ED50, None
    fkb = dict(fakes)
    fkb[TX_INH] = 1
    del rec[:]
    out = [0, 0]
    m.wmem(c, SCRATCH, bytes(8))
    stops, r0, abi, _ = fcall(c, s2["krk_aprs_tx_send"], (SCRATCH, SCRATCH + 4), fakes=fkb)
    m.report(("set", 0x2F) in rec and st() == 0 and bit6() == 0 and r32(c, s2["tx_busy"]) == 0 and abi,
             f"analog beacon send: the monitor is released before the send logic runs ({[e for e in rec if e[0] in ('set', 'clr')]}), tx_busy back to 0")
    run(s2["krk_mon_set"], (0,))
    clear_gates()

    # ---- 6. the stations screen as a viewer ------------------------------------------------------
    print("== F14 RX Monitor: the stations screen is a viewer of the shared ring")
    fkl = {s["krk_line"]: 0, GPIO_SET: fk_set, GPIO_CLR: fk_clr}
    w32(c, s["krk_screen_owner"], 0)
    run(s2["krk_mon_set"], (1,), now=st_["now"])
    # put a station in the ring through the monitor path (frame 1), then open the screen: it is kept
    assert armed()
    feed(noise_halves(30))
    feed(pcm["f1"])
    n_mon = ring_n()
    stops, _, abi, _ = fcall(c, s2["rx_enter"], fakes=fkl)
    m.report(abi and ring_n() == n_mon and n_mon >= 1, f"screen enter with the Monitor on: the ring is kept ({n_mon} station)")
    run(s2["krk_mon_set"], (0,))
    stops, _, abi, _ = fcall(c, s2["rx_enter"], fakes=fkl)
    m.report(abi and ring_n() == 0, "screen enter with the Monitor off: a fresh list (as before F14)")
    # no key switches the Monitor any more (the '#' key is gone): the switch is the "RX Monitor" option row (mon_get / mon_set)
    rows = {}

    def fk_line(regs, r0, r1):
        rows[r0] = rb(r1, 24).split(b"\0")[0]
        return 0
    fkr = {s["krk_line"]: fk_line, GPIO_SET: fk_set, GPIO_CLR: fk_clr, s2["mon_fm_sw"]: fk_sw, s2["mon_half_ptr"]: fk_half}
    fcall(c, s2["rx_enter"], fakes=fkr)
    w32(c, OS_TICKS, st_["now"])
    for key in (0x04, KEY_HASH, 0x03):
        fcall(c, s2["rx_tick"], (key,), fakes=fkr)
    m.report(magic() != MON_MAGIC, "the screen takes no key: '#', OK and another key leave the Monitor off")
    _, r0, abi, _ = fcall(c, s2["mon_get"])
    m.report(abi and r0 == 0, "option row getter: Off (0) while the Monitor is off")
    _, _, abi, _ = fcall(c, s2["mon_set"], (1,))
    _, r0, abi2, _ = fcall(c, s2["mon_get"])
    m.report(abi and abi2 and magic() == MON_MAGIC and r0 == 1, "option row setter(1): the Monitor is on, getter reads On (1)")
    w32(c, M + 12, 7)                                               # halves
    fcall(c, s2["mon_set"], (1,))
    m.report(r32(c, M + 12) == 7, "setter(1) while already on does nothing (halves counter kept)")
    rows.clear()
    fcall(c, s2["rx_enter"], fakes=fkr)
    m.report(rows.get(0, b"").startswith(b"APRS MON") and rows.get(9, b"").startswith(b"RX Monitor on h"),
             f"screen with the Monitor on: title {rows.get(0)!r}, status row {rows.get(9)!r} (no key hint)")
    _, _, abi, _ = fcall(c, s2["mon_set"], (0,))
    _, r0, abi2, _ = fcall(c, s2["mon_get"])
    m.report(abi and abi2 and magic() != MON_MAGIC and r0 == 0, "option row setter(0): the Monitor is off again, getter reads Off")
    rows.clear()
    fcall(c, s2["rx_enter"], fakes=fkr)
    m.report(rows.get(0, b"").startswith(b"APRS RX") and rows.get(9) == b"RX Monitor off", f"screen with the Monitor off: title {rows.get(0)!r}, status row {rows.get(9)!r}")
    dwc.chan_poke(c, b"\x10")
    rows.clear()
    fcall(c, s2["rx_enter"], fakes=fkr)
    m.report(rows.get(2) == b"Analog channel only" and rows.get(9) == b"RX Monitor off", "digital channel: 'Analog channel only' and the status row is still shown")
    dwc.chan_poke(c, b"\x00")
    m.wmem(c, M, bytes(20))
    return
