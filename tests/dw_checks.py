"""Dual-watch channel tracking (krk_core.c: dw state, krk_hook_chan_commit, krk_commit_end_stub, docs/dual-watch.md).

Two parts:
  * helpers other test files use to put the machine in a known "selected side / live struct" state
    (chan_poke, set_tag): pokes the live struct, the selected-side bit and the dw snapshot the way the commit hook
    leaves them;
  * check_dw(): the dual-watch scenarios themselves, on the halted QEMU machine, run through the REAL stock
    commit_channel_record_dual_vfo @0x030303bc (with the spliced epilogue), emulating what hardware does:
    the "loaded side" bit flips first, the struct is rewritten ~130 ms later.
"""
import struct

from build_artifacts import syms
import mi_rng_test as m
from target import fcall, r32, w32

SEL, LOADED, DWSTATE, FLAGS8, LIVE_RX, LIVE_TX = 0x3E9E1, 0x400D9, 0x400D8, 0x3E1E4, 0x3E1DC, 0x3E1E0
STORE_A, STORE_B = 0x3E358, 0x3E21C          # stock's per-side copies written by commit (param_14 == 1)
COMMIT = 0x030303BC
DW_MAGIC = 0x44575331
# struct dw in krk_core.c: u32 magic; s8 tag; u8 ok[2]; u8 flags8[2]; (pad) u32 rx[2]; u32 tx[2]
O_TAG, O_OK, O_F8, O_RX, O_TX, DW_SIZE = 4, 5, 7, 12, 20, 28

_dw = None


def dw_addr():
    global _dw
    if _dw is None:
        addr, size = syms(1, sizes=True)["dw"][-1]
        assert size == DW_SIZE, f"struct dw is {size} bytes, tests assume {DW_SIZE}"
        _dw = addr
    return _dw


def _w8_of(c):
    return lambda a, v: m.wmem(c, a, bytes([v & 0xFF]))


def _wb(w8, a, b):
    for i, x in enumerate(b):
        w8(a + i, x)


def _norm(f):
    return (f & 0x0F | 0x10) if f & 0xE0 else f


def chan_poke(c, flags, sel=0, rx=14439000, tx=14439000, w8=None):
    """Quiet single-side state: side `sel` selected and live (tag = sel), live flags8 = flags (bytes or int)."""
    w8 = w8 or _w8_of(c)
    flags = flags[0] if isinstance(flags, (bytes, bytearray)) else flags
    d = dw_addr()
    w8(SEL, 0x80 if sel else 0)
    w8(DWSTATE, 0)
    w8(FLAGS8, flags)
    _wb(w8, LIVE_RX, struct.pack("<II", rx, tx))
    _wb(w8, d, struct.pack("<I", DW_MAGIC))
    w8(d + O_TAG, sel)
    w8(d + O_OK + sel, 1)
    w8(d + O_F8 + sel, _norm(flags))
    _wb(w8, d + O_RX + 4 * sel, struct.pack("<I", rx))
    _wb(w8, d + O_TX + 4 * sel, struct.pack("<I", tx))


def set_tag(w8, side):
    """The live struct is `side`'s (dual-watch engine moved the receiver), selected side untouched."""
    w8(dw_addr() + O_TAG, side & 0xFF)


def check_dw(c, s, s2, img, stock):
    from gdb_rsp import read_mem
    from build_patch import decode_branch
    STACK_A = m.STACK_A
    w8 = _w8_of(c)
    d = dw_addr()
    rb = lambda a, n=1: read_mem(c, a, n)
    SCR, CIPHER, RX_ACTIVE = 0x1100038C, 0x33824, 0x0300F2A4

    print("== dual watch: channel commit hook (docs/dual-watch.md)")
    off = 0x03030550 - 0x03000000
    m.report(stock[off:off + 4].hex(" ") == "ee d9 00 20", f"stock bytes at 0x03030550: {stock[off:off + 4].hex(' ')} (want ee d9 00 20 = ld.w r15,(sp,0))")
    kind, half = decode_branch(img[off:off + 4])
    m.report(kind == "bsr" and 0x03030550 + half * 2 == s["krk_commit_end_stub"],
             f"splice 0x03030550 -> bsr {0x03030550 + half * 2:#x} (krk_commit_end_stub {s['krk_commit_end_stub']:#x})")

    def commit(rx, tx, flags, sel, state2, loaded, store=1):
        """Runs the stock commit with the 14-word argument list (10 on the stack) under the given engine state."""
        w8(SEL, 0x80 if sel else 0)
        w8(DWSTATE, state2)
        w8(LOADED, loaded)
        words = [0x00001234, 0x00005678, 0x9ABCDEF0, 0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555]   # params 5..12
        m.wmem(c, STACK_A, struct.pack("<10I", *words, 0x00ABCDEF, store))                                         # param 13 (u16 + u8), param 14
        _, _, abi, _ = fcall(c, COMMIT, (rx, tx, flags, 0x0A0B0C0D))
        return abi

    def state():
        t = rb(d + O_TAG)[0]
        return t - 256 if t > 127 else t

    def api(sel):
        w8(SEL, 0x80 if sel else 0)
        out = {}
        for k in ("krk_channel_side", "krk_channel_on_selected", "krk_channel_flags", "krk_channel_mode", "krk_channel_analog"):
            _, r0, _, _ = fcall(c, s[k])
            out[k] = struct.unpack("<i", struct.pack("<I", r0))[0]
        out["rx"] = fcall(c, s["krk_channel_freq"], (0,))[1]
        out["tx"] = fcall(c, s["krk_channel_freq"], (1,))[1]
        return out

    A_RX, A_TX, A_FL = 43305000, 43305000, 0x14        # side A: 433.05 MHz DMR, power field 2 (high)
    B_RX, B_TX, B_FL = 14439000, 14439000, 0x02        # side B: 144.390 analog, power field 1 (low)
    # ---- power-up: SRAM noise ------------------------------------------------------------------
    m.wmem(c, d, b"\xA5" * DW_SIZE)
    r = api(0)
    m.report(r["krk_channel_side"] == -1 and r["krk_channel_on_selected"] == 0 and r["krk_channel_mode"] == -1 and r["krk_channel_analog"] == 0
             and r["rx"] == 0, f"noise SRAM: side unknown, not on selected, mode unknown (analog() 0), no frequency: {r}")
    # ---- dual watch ON, A selected: A loaded, then the hardware sequence B / A / B / A ------------------
    ok = commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)
    m.report(ok and r32(c, d) == DW_MAGIC and state() == 0 and r32(c, LIVE_RX) == A_RX, "commit (A, dual watch, loaded A): hook initialised the state, tag A, stock struct written")
    ok = commit(B_RX, B_TX, B_FL, sel=0, state2=1, loaded=1)
    st = {"A": struct.unpack("<I", rb(STORE_A, 4))[0], "B": struct.unpack("<I", rb(STORE_B, 4))[0]}
    m.report(ok and state() == 1 and st == {"A": A_RX, "B": B_RX},
             f"commit (B loaded): tag B, and the side the hook picked is the one stock stored a copy for (0x3e358 = A {st['A']}, 0x3e21c = B {st['B']})")
    r = api(0)
    m.report(r["krk_channel_side"] == 1 and r["krk_channel_on_selected"] == 0 and r["krk_channel_mode"] == 1 and r["krk_channel_flags"] == A_FL
             and r["rx"] == A_RX and r["tx"] == A_TX and r["krk_channel_analog"] == 0,
             f"A selected while the struct is B's: A's snapshot (digital, flags {r['krk_channel_flags']:#x}, rx {r['rx']}), not on selected")
    # the race: the loaded bit flips to A, the struct is still B's
    w8(LOADED, 0)
    r = api(0)
    m.report(r["krk_channel_on_selected"] == 0 and r["krk_channel_mode"] == 1 and r["rx"] == A_RX,
             "loaded bit == selected (A) but the struct is still B's: NOT on selected (the old SIDE_LOADED test said yes), mode still A's digital")
    ok = commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)
    r = api(0)
    m.report(ok and r["krk_channel_on_selected"] == 1 and r["krk_channel_side"] == 0 and r["krk_channel_flags"] == A_FL and r["rx"] == A_RX,
             "struct reloaded (A): on selected, live values")
    # B selected: mirror image
    r = api(1)
    m.report(r["krk_channel_on_selected"] == 0 and r["krk_channel_mode"] == 0 and r["krk_channel_analog"] == 1 and r["rx"] == B_RX and r["krk_channel_flags"] == B_FL,
             "B selected while the struct is A's: B's snapshot (analog, 144.390, power field 1)")
    # ---- the loaded bit is unsynchronised with the struct, in BOTH directions: it must never matter -------------
    commit(B_RX, B_TX, B_FL, sel=0, state2=1, loaded=1)                # struct = B
    bad = []
    for bit in (0, 1, 0, 1):                                           # bit == A (lags) and bit == B, repeatedly
        w8(LOADED, bit)
        r = api(0)
        if not (r["krk_channel_mode"] == 1 and r["krk_channel_analog"] == 0 and r["krk_channel_on_selected"] == 0 and r["rx"] == A_RX):
            bad.append(bit)
    m.report(not bad, "struct B, A selected, loaded bit toggled both ways (incl. bit==A while the struct is still B): always A's digital / 433.05 / not on selected")
    commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)                # struct = A
    bad = []
    for bit in (1, 0, 1):                                              # bit leads to B before the next reload
        w8(LOADED, bit)
        r = api(0)
        if not (r["krk_channel_on_selected"] == 1 and r["krk_channel_flags"] == A_FL):
            bad.append(bit)
    m.report(not bad, "struct A, A selected, loaded bit already flipped to B (leads): still on selected, live flags")
    # ---- dual watch off: no flipping, the struct follows the selected side -------------------------------
    ok = commit(B_RX, B_TX, B_FL, sel=1, state2=0, loaded=0)           # state2 0: stock picks the SELECTED side for its copy
    r = api(1)
    m.report(ok and r["krk_channel_on_selected"] == 1 and r["krk_channel_side"] == 1 and r["krk_channel_analog"] == 1 and r["krk_channel_flags"] == B_FL
             and struct.unpack("<I", rb(STORE_B, 4))[0] == B_RX,
             "dual watch off, B selected, loaded bit stale (0): tag follows SELECTED (B), like stock's own store choice")
    # A/B key: the selected bit flips first, the struct is reloaded afterwards
    r = api(0)
    m.report(r["krk_channel_on_selected"] == 0 and r["krk_channel_mode"] == 1, "A/B key to A before the reload: not on selected, A's snapshot")
    commit(A_RX, A_TX, A_FL, sel=0, state2=0, loaded=0)
    r = api(0)
    m.report(r["krk_channel_on_selected"] == 1 and r["krk_channel_mode"] == 1, "after the reload: on selected")
    # channel change / VFO frequency entry on the live side: snapshot follows
    commit(A_RX + 1000, A_TX + 1000, 0x02, sel=0, state2=0, loaded=0)
    r = api(0)
    m.report(r["krk_channel_analog"] == 1 and r["rx"] == A_RX + 1000, "channel/frequency change on A (commit with new analog channel): API follows")
    # stock modes 2/3 are folded to 1 by the dispatcher right after the commit: snapshot stores 1
    commit(A_RX, A_TX, 0x32, sel=0, state2=0, loaded=0)
    w8(SEL, 0x80)
    r = api(1)                                                           # B's snapshot is not touched by A's commits
    m.report(r["krk_channel_mode"] == 0 and r["krk_channel_flags"] == B_FL, "A's commits do not touch B's snapshot")
    r = api(0)
    m.report(r["krk_channel_flags"] == 0x32, "live flags are returned as they are while on selected (dispatcher folds 3 -> 1 itself)")
    w8(SEL, 0)
    w8(FLAGS8, 0x10)
    commit(A_RX, A_TX, 0x32, sel=0, state2=0, loaded=0)
    m.report(rb(d + O_F8)[0] == 0x12, f"snapshot of a mode-3 flags byte 0x32 is normalised to {rb(d + O_F8)[0]:#x} (mode 1, power bits kept)")

    # ---- consumers --------------------------------------------------------------------------------
    print("== dual watch: F12 scrambler written while the struct is the other side's")
    st_ = s2["scr_st"]

    def scr_setup(cipher=0, key=0x1234):
        w32(c, st_, 0x53435231)
        m.wmem(c, st_ + 4, struct.pack("<H", key) + bytes([1, 0]))
        w32(c, st_ + 8, 0)
        w32(c, st_ + 12, 0)
        w32(c, st_ + 16, 0)
        m.wmem(c, CIPHER, bytes([cipher]))
        w32(c, SCR, 0)

    scr_setup()
    commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)                # A seen once (its snapshot exists) ...
    commit(B_RX, B_TX, B_FL, sel=0, state2=1, loaded=1)                # ... then the struct is B's forever
    stops, _, abi, _ = fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 1})   # stock_rx_active() is 1 for ANY analog struct
    m.report(abi and r32(c, SCR) == 0x12341234,
             f"maint tick in the B phase (stock_rx_active says 1): SCRAMBLE_REG = {r32(c, SCR):#010x} (A is digital)")
    w32(c, SCR, 0)
    fcall(c, s2["krk_scramble_chan_change"])
    m.report(r32(c, SCR) == 0x12341234, "forced apply (screen / channel change) in the B phase: written")
    r = api(0)
    commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)
    w32(c, SCR, 0xCAFE)
    fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 1})
    m.report(r32(c, SCR) == 0xCAFE, "A live and its receiver busy: the tick leaves the register alone (guard kept where it means something)")
    scr_setup(cipher=2)
    commit(A_RX, A_TX, A_FL, sel=0, state2=1, loaded=0)
    fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 0})
    commit(B_RX, B_TX, B_FL, sel=0, state2=1, loaded=1)
    w32(c, SCR, 0xCAFE)
    w8(CIPHER, 0)                                                      # B's channel has no cipher
    fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 1})
    m.report(r32(c, SCR) == 0, "A carries a stock cipher (seen in its phase): in the B phase the register is still cleared")
    scr_setup()
    commit(B_RX, B_TX, B_FL, sel=1, state2=1, loaded=1)
    commit(A_RX, A_TX, A_FL, sel=1, state2=1, loaded=0)                # B selected (analog), struct is A's (digital)
    w32(c, SCR, 0x12341234)
    fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 0})
    m.report(r32(c, SCR) == 0, "B (analog) selected while the struct is A's digital: register cleared, not left scrambling (old code read A's digital struct)")

    print("== dual watch: TX gates refuse unless the live struct IS the selected side and the right mode")
    for sel_side, live_side, want_tx_analog in ((0, 0, 0), (0, 1, 0), (1, 1, 1), (1, 0, 1)):
        commit(A_RX, A_TX, A_FL, sel=sel_side, state2=1, loaded=0)
        commit(B_RX, B_TX, B_FL, sel=sel_side, state2=1, loaded=1)
        if live_side == 0:
            commit(A_RX, A_TX, A_FL, sel=sel_side, state2=1, loaded=0)
        w8(SEL, 0x80 if sel_side else 0)
        _, ana, _, _ = fcall(c, s2["krk_aprs_tx_analog"])
        _, onsel, _, _ = fcall(c, s["krk_channel_on_selected"])
        m.report(ana == want_tx_analog and onsel == (sel_side == live_side),
                 f"selected {'AB'[sel_side]}, struct {'AB'[live_side]}: aprs analog {ana} (want {want_tx_analog}), on_selected {onsel} (want {int(sel_side == live_side)})")
