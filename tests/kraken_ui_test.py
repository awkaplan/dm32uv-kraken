#!/usr/bin/env python3
"""In-target tests for the kraken_ui patch, run on the COMBINED image under -M dm32 (halted at
reset, no boot), driving the real CK803S code over the gdb stub. Uses the QEMU helpers of
tests/mi_rng_test.py (start_qemu, setreg, run_to, report).

Checks:
  1. Every splice decodes to the intended target (menu wiring + the F6 hook).
  2. F6 krk_hook_txtrim_interp: for trim 8..1 eighths and several stock results, the wrapper calls
     the stock freq_calibration_interpolate_selected with the caller's (freq, level) untouched,
     returns round(raw * e / 8), and preserves r4-r11 and sp. The stock function is not executed:
     the run stops at its entry, r0 is set to a chosen raw value and it "returns" (calibration
     reads need the data chip, which a halted emulator does not have).
  3. F6 power-up state: with the state's magic absent (SRAM noise), the wrapper treats trim as 100%.

Run inside the dm32uv-re Docker image (tools/build.sh mounts the workspace at /w):
  python3 /w/dm32uv-unleashed/tests/kraken_ui_test.py
"""
import re
import struct
import subprocess
import sys
import threading
import traceback

from build_artifacts import EXT_TABLE, HOOK_TBL, PATCHED, REPO, STOCK_IMG, TBL_SLOTS, elf, syms, krk_define, krk_ext_ranges, stock_is  # first: puts the tool directories on sys.path
import mi_rng_test as m
from build_patch import decode_branch
from claims import claim
from gdb_rsp import read_mem
import dw_checks as dwc
import mon_checks
import optrow_checks
import convert_checks
from qemu_menu import OS_TICKS
from target import CANARY, call_via, cstr, fcall, halted_machine, r32, w32

ELF = elf(1)
ELF2 = elf(2)
ELF3 = elf(3)
ELF4 = elf(4)           # F13 Burst Voice
ELF5 = elf(5)           # F3 DMR link (moved out of blob 2 on 2026-10-30)
STOCK_INTERP = 0x0301B424
TRIM_MAGIC = 0x54524D31


# ---------------------------------------------------------------------------------------------
# 2026-10-29 small features: F12 scrambler, F11 squelch mode, F3 DMR link (docs/basic-privacy.md,
# noise-squelch.md, dmr-ber.md). The generic helper (tests/target.py fcall) runs a patch function on the halted machine
# with stock functions faked (stop at their entry, record r0/r1, set r0, return).
FD_WRITE, FD_READ, RX_ACTIVE, LED_TICK = 0x0002C2F4, 0x0002C33C, 0x0300F2A4, 0x03068224


def check_small_features(c, s, s2, img):
    stock = STOCK_IMG.read_bytes()
    sb = lambda a, n=4: stock[a - 0x3000000:a - 0x3000000 + n]
    pb = lambda a, n=4: img[a - 0x3000000:a - 0x3000000 + n]
    nop_krk = {s["krk_text"]: 0, s["krk_line"]: 0}            # the main blob's drawing: no-ops here
    SCR, CC, FLAGS8, CIPHER = 0x1100038C, 0x11000384, 0x3E1E4, 0x33824

    print("== F12 scrambler: stock bytes and the shared maint hook (no new splice)")
    m.report(stock_is(0x030547AE, "00e03b9d"), f"stock bytes at 0x030547ae: {sb(0x030547AE).hex(' ')} (want 00 e0 3b 9d)")
    kind, half = decode_branch(pb(0x030547AE))
    m.report(kind == "bsr" and 0x030547AE + half * 2 == s2["krk_hook_maint_tick"], "maint hook -> krk_hook_maint_tick (F12 rides on it)")
    st = s2["scr_st"]
    for k, fmt, want in ((0x1234, 0, 0x12341234), (0xFFFF, 0, 0xFFFFFFFF), (1, 0, 0x00010001), (0x1234, 1, 0xEDCB1234),
                         (0x1234, 2, 0x34123412), (0x1234, 3, 0x2C482C48), (0, 2, 0), (0, 1, 0)):
        _, r0, abi, _ = fcall(c, s2["krk_scramble_value"], (k, fmt))
        m.report(r0 == want and abi, f"value(K={k:#06x}, fmt {fmt}) = {r0:#010x} (want {want:#010x})")

    def scr_case(on, key, fmt, flags, cipher, cur, rx, magic=True, watch=()):
        w32(c, st, 0x53435231 if magic else 0xA5A5A5A5)
        w32(c, st + 12, 0)
        w32(c, st + 16, 0)                                   # sweep fields (sw_t0, sw/slot/logged): idle
        m.wmem(c, st + 4, struct.pack("<H", key) + bytes([on, fmt]))
        dwc.chan_poke(c, bytes([flags]))
        m.wmem(c, CIPHER, bytes([cipher]))
        w32(c, SCR, cur)
        w32(c, CC, 0x1)
        stops, _, abi, viol = fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: rx}, watch=watch)
        return r32(c, SCR), len(stops), abi and r32(c, CC) == 1, viol
    for (on, key, fmt, flags, cipher, cur, rx, magic), want, what in (
            ((1, 0x1234, 0, 0x10, 0, 0xCAFE, 0, True), 0x12341234, "on, digital, no stock cipher"),
            ((1, 0x1234, 0, 0x10, 2, 0x12341234, 0, True), 0, "stock cipher on the channel: cleared"),
            ((1, 0x1234, 0, 0x00, 0, 0x12341234, 0, True), 0, "analog channel: cleared"),
            ((0, 0x1234, 0, 0x10, 0, 0x12341234, 0, True), 0, "off: cleared"),
            ((1, 0, 0, 0x10, 0, 0xCAFE, 0, True), 0, "key 0: cleared"),
            ((1, 0x1234, 0, 0x10, 0, 0xCAFE, 0, False), 0, "SRAM noise (no magic): Off, cleared"),
            ((1, 0xFFFF, 0, 0x10, 0, 0, 0, True), 0xFFFFFFFF, "key 65535"),
            ((1, 0x1234, 3, 0x10, 0, 0, 0, True), 0x2C482C48, "format 3 (bit-reversed)")):
        got, nstops, ok_, _ = scr_case(on, key, fmt, flags, cipher, cur, rx, magic)
        m.report(got == want and ok_, f"tick: {what}: reg {got:#010x} (want {want:#010x}), LOCAL_CC untouched, ABI ok")
    got, nstops, _, _ = scr_case(1, 0x1234, 0, 0x10, 0, 0x12341234, 0)
    m.report(got == 0x12341234 and nstops == 0, "tick: register already right -> no write, stock_rx_active not even called")
    got, nstops, _, _ = scr_case(1, 0x1234, 0, 0x10, 0, 0xCAFE, 1)
    m.report(got == 0xCAFE and nstops == 1, f"tick: receiver busy -> register left alone (got {got:#x}, {nstops} rx check)")
    # positive control of the write watchpoint used by F3 below: a real register write must trip it
    _, _, _, viol = scr_case(1, 0x1234, 0, 0x10, 0, 0xCAFE, 0, watch=(SCR,))
    m.report(viol, "write watchpoint on SCRAMBLE_REG fires when the tick writes it (control for the F3 no-write check)")
    w32(c, st, 0x53435231)
    w32(c, st + 12, 0)
    w32(c, st + 16, 0)
    m.wmem(c, st + 4, struct.pack("<H", 0x4321) + bytes([1, 0]))
    dwc.chan_poke(c, b"\x10")
    m.wmem(c, CIPHER, b"\x00")
    w32(c, SCR, 0)
    before = r32(c, s2["krk_maint_ticks"])
    stops, _, abi, _ = fcall(c, s2["krk_hook_maint_tick"], fakes={LED_TICK: 0, RX_ACTIVE: 0})
    m.report([p_ for p_, _, _ in stops] == [LED_TICK, RX_ACTIVE] and r32(c, SCR) == 0x43214321 and abi
             and r32(c, s2["krk_maint_ticks"]) == before + 1, f"maint hook: stock tick first, then F12 writes {r32(c, SCR):#x}, counter +1")

    print("== F12 settings rows (Scrambler / Scrambler Key / Scrambler Fmt: stock option levels, the custom screen is gone)")
    DRAW, DLY_ = s["krk_popup_draw"], 0x0300C4BC
    for gone in ("scr_tick", "scr_enter", "scr_ui"):
        m.report(gone not in s2, f"the custom screen is gone: no symbol {gone}")
    w32(c, st, 0xA5A5A5A5)                                   # power-up noise: every getter must say Off / 0 / fmt0 without touching it
    w32(c, SCR, 0)
    dwc.chan_poke(c, b"\x10")
    m.wmem(c, CIPHER, b"\x00")
    g = lambda name: fcall(c, s2[name])[1]
    m.report((g("scr_on_get"), g("scr_key_get"), g("scr_fmt_get")) == (0, 0, 0) and r32(c, st) == 0xA5A5A5A5,
             "noise state: on/key/fmt read back 0/0/0 and the getters do not initialise the state")
    fcall(c, s2["scr_on_set"], (1,))
    m.report(r32(c, st) == 0x53435231 and read_mem(c, st + 6, 2) == bytes([1, 0]) and r32(c, SCR) == 0 and g("scr_on_get") == 1,
             "On with no key: state initialised (key 0), on = 1, register stays 0 (a zero key is no key)")
    for v, what in ((61234, "61234"), (1, "1"), (65535, "65535")):
        fcall(c, s2["scr_key_set"], (v,))
        m.report(g("scr_key_get") == v and r32(c, SCR) == (v << 16 | v), f"key {what} while On (digital, fmt0): register {r32(c, SCR):#010x}, applied at once")
    fcall(c, s2["scr_fmt_set"], (1,))
    m.report(g("scr_fmt_get") == 1 and r32(c, SCR) == (0x0000 << 16 | 0xFFFF) and read_mem(c, st + 6, 2) == bytes([1, 1]),
             f"format 1 with K = 65535: register {r32(c, SCR):#010x} (rx = ~K, tx = K)")
    fcall(c, s2["scr_key_set"], (0x1234,))
    for f, want in ((0, 0x12341234), (1, 0xEDCB1234), (2, 0x34123412), (3, 0x2C482C48)):
        fcall(c, s2["scr_fmt_set"], (f,))
        m.report(g("scr_fmt_get") == f and r32(c, SCR) == want, f"Scrambler Fmt {f}: register {r32(c, SCR):#010x} (want {want:#010x})")
    fcall(c, s2["scr_fmt_set"], (0,))
    fcall(c, s2["scr_on_set"], (0,))
    m.report(g("scr_on_get") == 0 and r32(c, SCR) == 0 and g("scr_key_get") == 0x1234, "Off: register back to 0, the key is kept")
    fcall(c, s2["scr_key_set"], (4321,))
    m.report(r32(c, SCR) == 0 and g("scr_key_get") == 4321, "key change while Off: register stays 0")
    fcall(c, s2["scr_on_set"], (1,))
    m.report(r32(c, SCR) == (4321 << 16 | 4321), "On again: register applied with the stored key and the fmt")
    dwc.chan_poke(c, b"\x00")
    fcall(c, s2["scr_key_set"], (4321,))
    fcall(c, s2["scr_on_set"], (1,))
    m.report(r32(c, SCR) == 0, "On on an analog channel: register 0 (idle), state still On")
    dwc.chan_poke(c, b"\x10")
    swv = read_mem(c, st + 16, 3)
    m.report(swv[0] == 0, "none of the rows arms the format sweep")
    # the follow-up warning popups (after the stock 'Setting is OK'): plain draw + yielding delay
    pops = []

    def fdraw(regs, r0, r1):
        pops.append((r0, cstr(c, regs["r1"]), cstr(c, regs["r2"]) if regs["r2"] else None))
        return 0
    dl = []
    fk = {DRAW: fdraw, DLY_: lambda regs, r0, r1: dl.append(r0) or 0}
    for key, idx, want in ((1234, 1, [(3, b"Weak scrambler,", b"not encrypted"), (3, b"Not for amateur", b"radio use")]),
                           (0, 1, [(3, b"Set Scrambler", b"Key first"), (3, b"Weak scrambler,", b"not encrypted"), (3, b"Not for amateur", b"radio use")]),
                           (1234, 0, [])):
        pops.clear()
        dl.clear()
        m.wmem(c, st + 4, struct.pack("<H", key))
        m.wmem(c, 0x3E396, b"\x07")                              # stock Menu Exit Time counter
        _, _, abi, _ = fcall(c, s2["scr_on_after"], (idx,), fakes=fk)
        if idx:
            m.report(read_mem(c, 0x3E396, 1)[0] == 0, "the popup chain resets the Menu Exit Time counter (no drop to standby right after the warnings)")
        m.report(abi and pops == want and len(dl) == len(want) and all(d_ == 1500 for d_ in dl),
                 f"warning popups after {'On' if idx else 'Off'} (key {key}): {[(p_[1], p_[2]) for p_ in pops]}, each followed by a yielding delay {dl}")
        m.report(all(len(p_[1]) <= 16 and len(p_[2] or b"") <= 16 for p_ in pops), "every popup line fits the 16-character box")
    w32(c, SCR, 0xCAFE1234)
    _, p_, abi, _ = fcall(c, s2["scr_on_title"])
    m.report(abi and cstr(c, p_) == b"Scrambler CAFE1234" and len(cstr(c, p_)) <= 19, f"On/Off header carries the applied register: {cstr(c, p_)!r}")
    w32(c, SCR, 0)
    w32(c, st, 0xA5A5A5A5)

    K = {d: (0x0F if d == 0 else 0x10 + d - 1) for d in range(10)}      # keypad scan codes, used by the F11 screen checks below
    OKK, HASH, STAR, UPK = 0x03, 0x0E, 0x0D, 0x02

    print("== F11 squelch mode: stock bytes and splice")
    m.report(stock_is(0x0300DE86, "80e237f2"), f"stock bytes at 0x0300de86: {sb(0x0300DE86).hex(' ')} (want 80 e2 37 f2)")
    kind, half = decode_branch(pb(0x0300DE86))
    m.report(kind == "bsr" and 0x0300DE86 + half * 2 == s2["krk_hook_sq_write"], f"sq_write_hook: {kind} -> {0x0300DE86 + half * 2:#x} (want {s2['krk_hook_sq_write']:#x})")
    sq = s2["sqst"]
    CH_LEVEL = 0x3E1E8

    def sq_set(mode, flags=0x00, level=3, dirty=0, cin=50, cout=52, keep=1, magic=True):
        w32(c, sq, 0x53514C31 if magic else 0xA5A5A5A5)
        m.wmem(c, sq + 4, struct.pack("<H", 0) + bytes([mode, dirty, cin, cout, 2, keep, 0]))
        dwc.chan_poke(c, bytes([flags]))
        m.wmem(c, CH_LEVEL, bytes([level << 4]))

    def sq_hook(val=0x2E2F):
        stops, _, abi, _ = fcall(c, s2["krk_hook_sq_write"], (0x78, val), fakes={FD_WRITE: 0})
        return [(a_, b_) for _, a_, b_ in stops], abi
    fmtw = lambda ws: [(hex(a_), hex(b_)) for a_, b_ in ws]
    for what, kw, want in (
            ("mode Off", dict(mode=0), [(0x78, 0x2E2F)]),
            ("SRAM noise (no magic) acts as Off", dict(mode=2, magic=False), [(0x78, 0x2E2F)]),
            ("Normal, analog, hybrid (stock RSSI word kept)", dict(mode=1), [(0x78, 0x2E2F), (0x4F, 0x3432)]),
            ("Normal, analog, RSSI gate open", dict(mode=1, keep=0), [(0x78, 0x201F), (0x4F, 0x3432)]),
            ("Tight, analog", dict(mode=2), [(0x78, 0x2E2F), (0x4F, 0x2A28)]),
            ("Custom 60/58: out clamped up to in", dict(mode=3, cin=60, cout=58), [(0x78, 0x2E2F), (0x4F, 0x3C3C)]),
            ("Custom 3/200: in clamped to 8, out to 127", dict(mode=3, cin=3, cout=200), [(0x78, 0x2E2F), (0x4F, 0x7F08)]),
            ("Normal on a digital channel: stock", dict(mode=1, flags=0x10), [(0x78, 0x2E2F)]),
            ("Normal on a digital channel after a mode write: stock + REG_4FH restored", dict(mode=1, flags=0x10, dirty=1), [(0x78, 0x2E2F), (0x4F, 0x302F)]),
            ("Normal at squelch level 0: stock", dict(mode=1, level=0), [(0x78, 0x2E2F)]),
            ("Off after a mode write (dirty): stock + REG_4FH restored", dict(mode=0, dirty=1), [(0x78, 0x2E2F), (0x4F, 0x302F)])):
        sq_set(**kw)
        got, abi = sq_hook()
        m.report(got == want and abi, f"hook: {what}: {fmtw(got)}, ABI ok")
    sq_set(mode=1)
    sq_hook()
    m.report(read_mem(c, sq + 7, 1)[0] == 1 and struct.unpack("<H", read_mem(c, sq + 4, 2))[0] == 0x2E2F, "hook: dirty set after a mode write; stock word remembered")
    sq_set(mode=0, dirty=1)
    sq_hook()
    got, _ = sq_hook()
    m.report(got == [(0x78, 0x2E2F)], "hook: the REG_4FH restore happens once (the next call is plain stock)")

    print("== F11 screen (registers and drawing faked)")
    regvals = {0x65: 47, 0x67: 0x44, 0x61: 0x0C00, 0x0C: 2, 0x4F: 0x302F, 0x78: 0x2E2F}
    noise_seq = []

    def rd(r_, r0, r1):
        if r0 == 0x65 and noise_seq:
            return noise_seq.pop(0)
        return regvals.get(r0, 0)
    fk = dict(nop_krk)
    fk.update({FD_READ: rd, FD_WRITE: 0})

    def sq_tick(scan):
        stops, _, abi, _ = fcall(c, s2["sq_tick"], (scan,), fakes=fk)
        return [(a_, b_) for p_, a_, b_ in stops if p_ == FD_WRITE], abi
    # --- "Squelch Mode" option row (2026-11-02): getter / setter over the same RAM mode, the setter re-applies at once
    def sq_peek():
        return read_mem(c, sq + 6, 1)[0]

    def sq_get():
        _, r0, abi, _ = fcall(c, s2["sq_get"])
        return r0, abi

    def sq_setter(idx):
        stops, _, abi, _ = fcall(c, s2["sq_set"], (idx,), fakes=fk)
        return [(a_, b_) for p_, a_, b_ in stops if p_ == FD_WRITE], abi
    sq_set(mode=0, magic=False)
    got, abi = sq_get()
    m.report(abi and got == 0 and r32(c, sq) == 0x53514C31, "sq_get with the state magic absent (SRAM noise): Off, state initialised")
    sq_set(mode=0)
    m.wmem(c, sq + 4, struct.pack("<H", 0x2E2F) + bytes([0, 0, 50, 52, 2, 1, 1]))
    for idx, want_w in ((1, [(0x78, 0x2E2F), (0x4F, 0x3432)]), (2, [(0x78, 0x2E2F), (0x4F, 0x2A28)]), (0, [(0x78, 0x2E2F), (0x4F, 0x302F)])):
        writes, abi = sq_setter(idx)
        got, _ = sq_get()
        m.report(abi and sq_peek() == idx and got == idx and writes == want_w,
                 f"sq_set({idx}) (OK on the option level): mode {sq_peek()}, getter {got}, applied at once {fmtw(writes)} (what '#'/'*' applied)")
    for bad in (3, 4, 255, 0xFFFFFFFF):
        sq_setter(2)
        writes, abi = sq_setter(bad)
        m.report(abi and sq_peek() == 2 and not writes, f"sq_set({bad:#x}): out of range (Custom is not an option): nothing changes, no register write")
    sq_set(mode=3)
    got, _ = sq_get()
    m.report(got == 0, "sq_get with the unreachable Custom mode in RAM: shows Off (never an index past the 3 labels)")
    m.wmem(c, sq + 4, struct.pack("<H", 0x2E2F) + bytes([0, 0, 50, 52, 2, 1, 0]))
    sq_set(mode=1)
    m.wmem(c, sq + 4, struct.pack("<H", 0) + bytes([1, 0, 50, 52, 2, 1, 0]))
    writes, abi = sq_setter(2)
    m.report(abi and sq_peek() == 2 and not writes, "sq_set before any stock REG_78H write was seen (have_last = 0): mode stored, applied by the next stock write (no register write now)")
    # row structs
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2["krk_squelch_screen"], 28))
    o = struct.unpack("<IIBxxxII", read_mem(c, s2["sq_opt"], 20))
    labs = struct.unpack("<3I", read_mem(c, o[1], 12))
    m.report(cstr(c, sc[0]) == b"Squelch Mode" and sc[1] == 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == s2["sq_opt"] and sc[6] == 0
             and cstr(c, o[0]) == b"Squelch Mode" and o[2] == 3 and o[3] == s2["sq_get"] and o[4] == s2["sq_set"]
             and [cstr(c, x_) for x_ in labs] == [b"Off (stock)", b"Normal", b"Tight"],
             f"Squelch Mode row: option level, title {cstr(c, o[0])!r}, 3 options {[cstr(c, x_) for x_ in labs]}, getter sq_get, setter sq_set, no enter/tick")
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2["krk_sqsurvey_screen"], 28))
    m.report(cstr(c, sc[0]) == b"Squelch Survey" and sc[1] == s2["sq_enter"] and sc[2] == s2["sq_tick"] and sc[5] == 0 and sc[6] == 0,
             "Squelch Survey row: a regular screen (enter sq_enter, tick sq_tick), no option, not a one-shot")

    print("== F11 Squelch Survey screen: header shows the mode read-only; no key sets a mode; keys 1 / 5 / 6 unchanged")
    drawn = []

    def fline(regs, r0, r1):
        drawn.append((r0, cstr(c, r1)))
        return 0
    fk2 = dict(fk)
    fk2[s["krk_line"]] = fline
    for md, nm in ((0, b"Off"), (1, b"Normal"), (2, b"Tight")):
        sq_set(mode=md)
        drawn.clear()
        fcall(c, s2["sq_enter"], fakes=fk2)
        m.report(drawn and drawn[0] == (0, b"Sq survey: " + nm), f"survey header with mode {md}: {drawn[0] if drawn else None}")
    sq_set(mode=1)
    m.wmem(c, sq + 4, struct.pack("<H", 0x2E2F) + bytes([1, 0, 50, 52, 2, 1, 1]))
    fcall(c, s2["sq_enter"], fakes=fk)
    for what, scan in (("#", HASH), ("*", STAR), ("Up", 0x02), ("Down", 0x05), ("OK", OKK), ("9", K[9] if 9 in K else 0x18), ("Left/other 0x0b", 0x0B)):
        before = bytes(read_mem(c, sq + 4, 12))
        writes, abi = sq_tick(scan)
        m.report(abi and not writes and bytes(read_mem(c, sq + 4, 12)) == before,
                 f"survey key '{what}': no register write, mode / thresholds / field untouched (the mode is set in the Squelch Mode row)")
    writes, _ = sq_tick(K[1])
    m.report(writes and writes[0] == (0x78, 0x201F) and read_mem(c, sq + 10, 1)[0] == 2, f"key 1: RSSI gate open -> REG_78H {writes[0][1]:#x} (unchanged)" if writes else "key 1 wrote nothing")
    noise_seq[:] = [40 + (i % 16) for i in range(60)]
    sq_tick(K[5])                                            # key 5: survey, 50 samples including this call
    for _ in range(49):
        sq_tick(0)
    sv = read_mem(c, s2["squi"], 0x48)
    sn = struct.unpack("<3h", sv[60:66])
    sr = struct.unpack("<3h", sv[66:72])
    vals = [40 + (i % 16) for i in range(50)]
    m.report(sv[59] == 1 and sv[58] == 0 and sn == (min(vals), sum(vals) // 50, max(vals)) and sr == (0x44, 0x44, 0x44),
             f"5 s survey after 50 samples: noise min/avg/max {sn}, rssi raw {sr}")

    print("== F3 DMR link: read-only polling")
    lq = s2["lqst"]
    SYS390, SYS164, SEL, LOADED = 0x11000390, 0x11000164, 0x3E9E1, 0x400D9
    src = (REPO / "patches/kraken_ui/krk_dmrlink.c").read_text()
    m.report(not re.search(r"SYSCTL\([^)]*\)\s*(=|\|=|&=|\^=|\+=)[^=]", src), "source has no store through SYSCTL() (the only MMIO accessor)")
    m.wmem(c, SEL, b"\x00")
    m.wmem(c, LOADED, b"\x00")
    dwc.chan_poke(c, b"\x10")                               # digital
    w32(c, lq, 0xA5A5A5A5)
    fcall(c, s2["lq_enter"], fakes=nop_krk)
    m.report(r32(c, lq + 4) == 0 and read_mem(c, lq + 221, 2) == b"\0\0", "enter: accumulator and raw log cleared")
    words = [1 << 12 | 1 | 1 << 4 | 1 << 19, 2 << 12 | 1 | 1 << 28 | 1 << 4 | 1 << 19,
             3 << 12 | 1 << 27 | 1 << 4 | 1 << 19, 1 << 12 | 1 | 1 << 4 | 1 << 19]
    cnts = [2 << 24 | 12, 14, 16, 1 << 24 | 10]
    viol = False
    for w_, cn in zip(words, cnts):
        w32(c, SYS390, w_)
        w32(c, SYS164, cn)
        for _ in range(3):                                  # the same burst polled 3 times: counted once
            _, _, abi, v = fcall(c, s2["lq_screen_tick"], (0,), fakes=nop_krk, watch=(SYS390, SYS164))
            viol |= v
    m.report(not viol, "watchpoints on 0x11000390 / 0x11000164 (write) never fired in 12 ticks")
    q = struct.unpack("<8I", read_mem(c, lq, 32))
    n, emb, cs, evm_sum, sync_err, sync_n = q[1:7]
    m.report((n, emb, cs, evm_sum, sync_err, sync_n) == (4, 1, 1, 12 + 14 + 16 + 10, 3, 2),
             f"accumulator after 4 bursts x3 polls: n={n} emb={emb} cs={cs} evm_sum={evm_sum} sync_err={sync_err} sync_n={sync_n}")
    cnt_ring, head = read_mem(c, lq + 189, 1)[0], read_mem(c, lq + 188, 1)[0]
    m.report(cnt_ring == 4 and head == 4 and r32(c, lq + 80) == words[0] and r32(c, lq + 84) == cnts[0], f"raw log holds the 4 distinct pairs (count {cnt_ring})")
    # analog channel: nothing is sampled or counted
    dwc.chan_poke(c, b"\x00")
    w32(c, SYS390, 5 << 12 | 1)
    w32(c, SYS164, 7)
    fcall(c, s2["lq_screen_tick"], (0,), fakes=nop_krk)
    m.report(r32(c, lq + 4) == 4, "analog channel: no burst counted (screen shows n/a (analog))")


from f10_checks import check_f10  # noqa: E402  (F10 satellite checks live in tests/f10_checks.py)
from f13_checks import check_f13  # noqa: E402  (F13 burst voice checks live in tests/f13_checks.py)


def check_f12_review(c, s, s2, img):
    """Review fix 6: SCRAMBLE_REG is applied at channel change (hook slot 2 through the main blob's aprs_mode_hook
    stub) and at the first burst of a transmission, not only from the 1 Hz tick."""
    print("== F12 review fix: channel-change hook and TX-start hook")
    SCR, FLAGS8, CIPHER = 0x1100038C, 0x3E1E4, 0x33824
    st = s2["scr_st"]
    tbl = HOOK_TBL
    m.report(r32(c, tbl + 8) == s2["krk_scramble_chan_change"] and r32(c, tbl) == s2["krk_parrot_rx_start"],
             f"hook table slot 2 -> krk_scramble_chan_change {r32(c, tbl + 8):#x}")

    def setup(cur, flags=0x10, cipher=0, key=0x1234, on=1, last_tx=0):
        w32(c, st, 0x53435231)
        m.wmem(c, st + 4, struct.pack("<H", key) + bytes([on, 0]))
        w32(c, st + 8, last_tx)
        w32(c, st + 12, 0)
        w32(c, st + 16, 0)
        dwc.chan_poke(c, bytes([flags]))
        m.wmem(c, CIPHER, bytes([cipher]))
        w32(c, SCR, cur)
    # the stub itself, as the dispatcher calls it: r3 = mode nibble, every register of the dispatcher survives
    setup(0)
    for i, v in ((1, 0x11111111), (2, 0x22222222), (12, 0x12121212), (13, 0x13131313)):
        m.setreg(c, i, v)
    _, r0, abi, _ = fcall(c, s["krk_hook_aprs_mode"], (0xA0A0A0A0, 0x11111111, 0x22222222, 0x30))
    regs = m.regs(c)
    m.report(abi and r0 == 0xA0A0A0A0 and regs["r1"] == 0x11111111 and regs["r2"] == 0x22222222 and regs["r3"] == 0x30
             and regs["r12"] == 0x12121212 and regs["r13"] == 0x13131313 and r32(c, SCR) == 0x12341234,
             f"krk_hook_aprs_mode (channel change): SCRAMBLE_REG {r32(c, SCR):#x} written at once; r0-r3, r12, r13, sp, r15 survive the C call")
    # the new channel is analog / carries a stock cipher: the register goes back to 0 at the change
    for what, kw in (("analog channel", dict(flags=0x00)), ("stock cipher", dict(cipher=2))):
        setup(0x12341234, **kw)
        fcall(c, s["krk_hook_aprs_mode"], (0, 0, 0, 0x30))
        m.report(r32(c, SCR) == 0, f"channel change to {what}: register cleared immediately (not after up to 1 s)")
    setup(0xCAFE, on=0)
    fcall(c, s["krk_hook_aprs_mode"], (0, 0, 0, 0x10))
    m.report(r32(c, SCR) == 0, "channel change with the scrambler Off: register 0")
    # TX start: the first burst after a gap applies; later bursts of the same transmission never rewrite it
    setup(0, last_tx=0)
    w32(c, OS_TICKS, 5000)
    _, _, abi, _ = fcall(c, s2["krk_scramble_tx_hook"])
    m.report(abi and r32(c, SCR) == 0x12341234, f"first TX burst: register {r32(c, SCR):#x} applied before the burst is sent")
    w32(c, SCR, 0)
    w32(c, OS_TICKS, 5006)
    fcall(c, s2["krk_scramble_tx_hook"])
    m.report(r32(c, SCR) == 0, "next burst 60 ms later (same transmission): not rewritten")
    w32(c, OS_TICKS, 5006 + 21)
    fcall(c, s2["krk_scramble_tx_hook"])
    m.report(r32(c, SCR) == 0x12341234, "burst after a > 200 ms gap = new transmission: applied again")
    w32(c, st, 0xA5A5A5A5)
    w32(c, SCR, 0xBEEF)
    fcall(c, s2["krk_scramble_tx_hook"])
    m.report(r32(c, SCR) == 0xBEEF, "scrambler state not initialised (noise): the hook touches nothing")
    w32(c, st, 0xA5A5A5A5)
    w32(c, SCR, 0)


def check_sweeps(c, s, s2, img):
    """Sweeps: F11 key 6 survey (suggestion only; OK/9 no longer apply), F12 sweep engine (its P1 key and F3 key 2 were removed 2026-11-02; checked as ignored)."""
    nop_krk = {s["krk_text"]: 0, s["krk_line"]: 0}
    SCR, FLAGS8, CIPHER = 0x1100038C, 0x3E1E4, 0x33824
    SYS390, SYS164, SEL, LOADED = 0x11000390, 0x11000164, 0x3E9E1, 0x400D9
    # later checks (F10 / F13 / parrot ownership) depend on the machine state the earlier ones left: put every
    # byte this function pokes back at the end
    saved = [(a_, read_mem(c, a_, 4)) for a_ in (FLAGS8, CIPHER, OS_TICKS, SEL & ~3, LOADED & ~3, 0x3E1E8, 0x11000384, SCR, SYS390, SYS164)]

    print("== F12 sweep: schedule table and the 12 slots")
    def bitrev(k):
        return int(format(k, "016b")[::-1], 2)
    def val(k, f):
        sw = ((k >> 8) | (k << 8)) & 0xFFFF
        tx, rx = {0: (k, k), 1: (k, (~k) & 0xFFFF), 2: (sw, sw), 3: (bitrev(k), bitrev(k))}[f]
        return rx << 16 | tx
    ok = True
    for slot in range(12):
        want = (val(0x1234, slot & 3) & 0xFFFF0000) | (val(0x1234, (0, 2, 3)[slot // 4]) & 0xFFFF)
        _, r0, abi, _ = fcall(c, s2["krk_scramble_sweep_value"], (0x1234, slot))
        ok &= r0 == want and abi
        if r0 != want:
            print(f"   slot {slot}: {r0:#x} want {want:#x}")
    m.report(ok, "sweep value of all 12 slots = rx field of format slot%4, tx field of format {0,2,3}[slot/4]")
    _, r0, _, _ = fcall(c, s2["krk_scramble_sweep_value"], (0, 7))
    m.report(r0 == 0, "key 0: every slot is 0")

    st = s2["scr_st"]
    T0 = 100000

    def setup(sw, t0=T0, on=0, flags=0x10, cipher=0, key=0x1234, cur=0):
        w32(c, st, 0x53435231)
        m.wmem(c, st + 4, struct.pack("<H", key) + bytes([on, 0]))
        w32(c, st + 8, 0)
        w32(c, st + 12, t0)
        m.wmem(c, st + 16, bytes([sw, 0, 0xFD, 0]))
        dwc.chan_poke(c, bytes([flags]))
        m.wmem(c, CIPHER, bytes([cipher]))
        w32(c, SCR, cur)
        w32(c, 0x11000384, 1)
    sw = lambda: tuple(read_mem(c, st + 16, 3))
    # TX radio: the first TX burst starts an armed sweep, bursts follow the clock, the end restores the normal value
    setup(1)
    w32(c, OS_TICKS, T0 + 50)
    _, _, abi, _ = fcall(c, s2["krk_scramble_tx_hook"])
    m.report(abi and sw()[0] == 2 and r32(c, st + 12) == T0 + 50 and r32(c, SCR) == 0x12341234, f"armed + first TX burst: running, slot 0, register {r32(c, SCR):#x}")
    w32(c, OS_TICKS, T0 + 50 + 300 * 5 + 7)
    fcall(c, s2["krk_scramble_tx_hook"])
    want = (val(0x1234, 5 & 3) & 0xFFFF0000) | (val(0x1234, 2) & 0xFFFF)
    m.report(sw()[:2] == (2, 5) and r32(c, SCR) == want, f"burst in slot 5 (3 s each): register {r32(c, SCR):#010x} (want {want:#010x})")
    w32(c, OS_TICKS, T0 + 50 + 300 * 12)
    fcall(c, s2["krk_scramble_tx_hook"])
    m.report(sw()[0] == 0 and r32(c, SCR) == 0, "after slot 11 the sweep ends and the scrambler is Off again (register 0)")
    setup(2, on=1)
    w32(c, OS_TICKS, T0 + 300 * 12 + 1)
    fcall(c, s2["krk_scramble_tx_hook"])
    m.report(sw()[0] == 0 and r32(c, SCR) == 0x12341234, "sweep end with the scrambler ON restores the normal fmt0 word")
    # RX radio: the maintenance tick starts it only when the squelch is open and the channel is digital
    for what, rx, kw, want_sw in (("squelch shut: stays armed", 0, {}, 1), ("squelch open: starts", 1, {}, 2),
                                  ("squelch open, analog channel: stays armed", 1, dict(flags=0x00), 1),
                                  ("squelch open, stock cipher on the channel: stays armed", 1, dict(cipher=2), 1)):
        setup(1, **kw)
        w32(c, OS_TICKS, T0 + 20)
        fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: rx})
        m.report(sw()[0] == want_sw, f"1 Hz tick, armed: {what}")
    setup(1, cur=0)
    w32(c, OS_TICKS, T0 + 30001)
    fcall(c, s2["krk_scramble_tick"], fakes={RX_ACTIVE: 1})
    m.report(sw()[0] == 0 and r32(c, SCR) == 0, "armed for more than 5 min: gives up (a later normal call is not swept)")
    # the P1 sweep key and the whole custom screen are gone: the settings rows never arm the sweep
    setup(0, on=1, cur=0)
    fcall(c, s2["scr_key_set"], (0x1234,))
    fcall(c, s2["scr_fmt_set"], (3,))
    fcall(c, s2["scr_on_set"], (1,))
    m.report(sw()[0] == 0 and r32(c, SCR) == 0x2C482C48, "Key / Fmt / On setters do not arm the sweep (sw stays 0), register = fmt3 word")
    w32(c, st, 0xA5A5A5A5)
    w32(c, SCR, 0)

    print("== F3: the key-2 one-key capture was removed (2026-11-02); key 1 raw log and key 0 reset remain")
    lq = s2["lqst"]
    RAW, TICKS = lq + 190, lq + 184                         # RAW_N 12: ring 96 B, then prev 8, ticks 4, head/count/raw/div/have_prev
    m.wmem(c, SEL, b"\x00")
    m.wmem(c, LOADED, b"\x00")
    dwc.chan_poke(c, b"\x10")
    fcall(c, s2["lq_enter"], fakes=nop_krk)
    w32(c, SYS390, 0)
    w32(c, SYS164, 0)
    fcall(c, s2["lq_screen_tick"], (0x11,), fakes=nop_krk)
    m.report(read_mem(c, RAW, 1)[0] == 0 and r32(c, TICKS) == 1, "key 2: ignored (raw view still off)")
    fcall(c, s2["lq_screen_tick"], (0x10,), fakes=nop_krk)
    m.report(read_mem(c, RAW, 1)[0] == 1, "key 1: raw log on")
    fcall(c, s2["lq_screen_tick"], (0x11,), fakes=nop_krk)
    m.report(read_mem(c, RAW, 1)[0] == 1, "key 2 while raw is on: ignored")
    fcall(c, s2["lq_screen_tick"], (0x10,), fakes=nop_krk)
    m.report(read_mem(c, RAW, 1)[0] == 0, "key 1 again: raw log off")
    dwc.chan_poke(c, b"\x10")
    fcall(c, s2["lq_exit"])

    print("== F11 key 6: 25 s survey and suggestion; nothing is applied (OK / 9 removed 2026-11-02)")
    sq, squi = s2["sqst"], s2["squi"]
    CH_LEVEL = 0x3E1E8
    regvals = {0x65: 62, 0x67: 0x44, 0x61: 0x0C00, 0x0C: 2, 0x4F: 0x302F, 0x78: 0x2E2F}
    fk = dict(nop_krk)
    fk.update({FD_READ: lambda r, r0, r1: regvals.get(r0, 0), FD_WRITE: 0})

    def tick(scan):
        stops, _, abi, _ = fcall(c, s2["sq_tick"], (scan,), fakes=fk)
        return [(a_, b_) for p_, a_, b_ in stops if p_ == FD_WRITE], abi

    def fresh():
        w32(c, sq, 0x53514C31)
        m.wmem(c, sq + 4, struct.pack("<H", 0x2E2F) + bytes([0, 0, 50, 52, 2, 1, 1]))
        dwc.chan_poke(c, b"\x00")
        m.wmem(c, CH_LEVEL, bytes([3 << 4]))
        fcall(c, s2["sq_enter"], fakes=fk)
    SV = squi + 72                                          # struct sqsv, then svon, svdone, svstat, pending, nrm[2], tgt[2]
    def poke_window(idle_n, idle_min, carr_n, carr_max):
        m.wmem(c, SV, struct.pack("<IHHHHBBBB", 0x40 * 30, 249, 0x40, idle_n, carr_n, 0, 5, idle_min, carr_max))
        m.wmem(c, SV + 16, bytes([1, 0, 0, 0]))
    fresh()
    tick(0x15)                                              # key 6
    m.report(read_mem(c, SV + 16, 1)[0] == 1, "key 6 starts the window")
    poke_window(100, 60, 50, 24)
    tick(0)                                                 # noise 62 / rssi 0x44: idle sample, window completes
    res = read_mem(c, SV + 16, 8)
    m.report(res[0] == 0 and res[1] == 1 and res[2] == 0 and res[3] == 1 and tuple(res[4:8]) == (48, 50, 36, 38),
             f"window done: status {res[2]}, pending {res[3]}, normal {tuple(res[4:6])}, tight {tuple(res[6:8])} (want 48/50, 36/38)")
    m.report(read_mem(c, sq + 6, 1)[0] == 0, "nothing applied by the survey itself (mode still Off)")
    for what, scan in (("OK", 0x03), ("9", 0x18)):
        before = bytes(read_mem(c, sq + 4, 12))
        writes, _ = tick(scan)
        m.report(not writes and bytes(read_mem(c, sq + 4, 12)) == before and read_mem(c, SV + 19, 1)[0] == 1,
                 f"{what} after a suggestion: nothing applied any more (no register write, mode / pair untouched); the pairs stay on screen and on the UART")
    for what, args, st_ in (("no carrier seen", (100, 60, 2, 24), 1), ("no idle seen", (2, 60, 50, 24), 2), ("no gap", (100, 28, 50, 26), 3)):
        fresh()
        tick(0x15)
        poke_window(*args)
        regvals[0x65] = 62
        tick(0)
        res = read_mem(c, SV + 16, 4)
        before = bytes(read_mem(c, sq + 4, 12))
        writes, _ = tick(0x03)
        m.report(res[2] == st_ and res[3] == 0 and not writes and bytes(read_mem(c, sq + 4, 12)) == before,
                 f"{what}: status {res[2]}, no suggestion; OK does nothing")
    for a_, v_ in saved:
        m.wmem(c, a_, v_)


def check_back_exit(c, s, s2, s4):
    """2026-11-02: Parrot and Burst Voice leave through krk_run_screen's pop path exactly like every other screen (hardware
    2026-11-01 saw them 'exit to standby' on v50; fixed by the v52 exit work, docs/menu-exit-timer.md section 7). The REAL screen
    structs (their real exit callbacks, enter/tick nulled) are run under the runner with a Back scan: the level the opening OK
    pushed is popped (history_queue_pop), the KRAKEN list's restore (krk_category_entry) runs, the stale events are drained, and
    NOTHING posts a stock Back/standby event (write_field_and_mbox_post @0x03013178), so the list is the active level."""
    print("== Parrot / Burst Voice: Back pops the KRAKEN list level and posts nothing")
    SEMP, SEMQ, SCANK, LCDR, FILL, POP, QPEND, POST = 0x0300C500, 0x0300C53C, 0x03013660, 0x0301CAD0, 0x03017F54, 0x03036C00, 0x0306A1B8, 0x03013178
    SCR_ = m.STACK_A - 0x400
    rest = s["krk_category_entry"]
    for name, sym_ in (("Parrot Status", s2["krk_parrot_status_screen"]), ("Burst Voice", s4["krk_bv_screen"])):
        raw = bytearray(read_mem(c, sym_, 28))
        raw[4:12] = bytes(8)                                  # enter, tick = null: only the exit callback is real
        raw[20:28] = bytes(8)                                 # no option row, no flags (a regular screen)
        m.wmem(c, SCR_, bytes(raw))
        exit_fn = struct.unpack("<I", bytes(raw[12:16]))[0]
        left = [2]

        def qpend(regs, r0, r1):
            if left[0] > 0:
                left[0] -= 1
                return 0x38E29
            return 0
        fk = {SEMP: 0, SEMQ: 0, SCANK: 6, LCDR: 0, FILL: 0, POP: 0, rest: 0, QPEND: qpend, POST: 0}
        stops, _, abi, _ = fcall(c, s["krk_run_screen"], (SCR_, rest), fakes=fk)
        order = [p_ for p_, _, _ in stops]
        m.report(abi and exit_fn != 0 and POST not in order and order[-5:] == [POP, rest, QPEND, QPEND, QPEND],
                 f"{name}: Back -> exit callback, history pop, KRAKEN list restore, drain; no stock Back/standby event posted")
        m.report(POST not in order, f"{name}: nothing posts through write_field_and_mbox_post")


def check_exit_and_rssi(c, s):
    """2026-11-01 (v52): (a) the screen runner empties the stock UI event queue on exit (docs/menu-exit-timer.md "UI freeze
    after exit"), (b) RSSI Meter takes the max of spaced reads on digital channels and the mean of back-to-back reads on analog."""
    print("== screen runner exit: stale UI events drained (keymsgq 0x38d70, non-blocking OSQPend)")
    SEMP, SEMQ, SCANK, LCDR, FILL, POP, QPEND = 0x0300C500, 0x0300C53C, 0x03013660, 0x0301CAD0, 0x03017F54, 0x03036C00, 0x0306A1B8
    SCR_ = m.STACK_A - 0x400
    m.wmem(c, SCR_, bytes(28))                                   # struct krk_screen, all callbacks null, no opt, no flags
    left = [0]
    opts = []

    def qpend(regs, r0, r1):
        opts.append((r0, r1, regs["r2"] & 0xFFFF))
        if left[0] > 0:
            left[0] -= 1
            return 0x38E29
        return 0

    def qpend_err(regs, r0, r1):                                 # a message AND a nonzero error: the error wins
        w32(c, r32(c, regs["r14"] + 4), 0x61B0)      # 6th argument = &err, on the stack
        opts.append((r0, r1, regs["r2"] & 0xFFFF))
        return 0x38E29
    rest = s["krk_category_entry"]
    fk = {SEMP: 0, SEMQ: 0, SCANK: 6, LCDR: 0, FILL: 0, POP: 0, rest: 0, QPEND: qpend}
    for n_, name in ((3, "3 stale events"), (0, "empty queue"), (1000, "a queue that never empties")):
        left[0] = n_
        opts.clear()
        stops, _, abi, _ = fcall(c, s["krk_run_screen"], (SCR_, rest), fakes=fk)
        order = [p_ for p_, _, _ in stops]
        want = min(n_, 40) + (1 if n_ < 40 else 0)
        m.report(abi and order[-(want + 2):] == [POP, rest] + [QPEND] * want and all(o == (0x38D70, 0, 0x8000) for o in opts),
                 f"Back with {name}: pop, restore, then {len(opts)} non-blocking pends (want {want}) on queue 0x38d70 with opt 0x8000, ABI kept")
    stops, _, abi, _ = fcall(c, s["krk_run_screen"], (SCR_, rest), fakes={**fk, QPEND: qpend_err})
    m.report(abi and [p_ for p_, _, _ in stops].count(QPEND) == 1, "a pend that reports an error ends the drain at once")

    print("== RSSI Meter: digital = max of spaced reads, analog = mean of back-to-back reads")
    FILLRECT, DLY = 0x03017C50, 0x0300C4BC
    st = s["rssi_st"]
    reads = []
    rf = lambda regs, r0, r1: reads.pop(0) if r0 == 0x67 and reads else 0
    fk = {s["krk_text"]: 0, s["krk_line"]: 0, FILLRECT: 0, DLY: 0, FD_READ: rf, RX_ACTIVE: 0}
    rd_i = lambda off: struct.unpack("<i", read_mem(c, st + off, 4))[0]

    def tick(flags, vals, enter=True):
        dwc.chan_poke(c, bytes([flags]))
        if enter:
            fcall(c, s["rssi_enter"], fakes=fk)
        reads[:] = list(vals)
        stops, _, abi, _ = fcall(c, s["rssi_tick"], (0,), fakes=fk)
        return [(a_) for p_, a_, _ in stops if p_ == DLY], abi
    dl, abi = tick(0x00, [0x100, 0x104, 0x10C, 0x110])
    m.report(abi and dl == [] and rd_i(0) == rd_i(4) == 264 - 320, f"analog: 4 back-to-back reads averaged -> {rd_i(4)} half-dB (want -56), no delay")
    for vals in ([0x120, 0xC0, 0xC0, 0xC0], [0xC0, 0xC0, 0xC0, 0x120], [0xC0, 0x120, 0xC0, 0xC0]):
        dl, abi = tick(0x10, vals)
        m.report(abi and dl == [20, 20, 20] and rd_i(4) == 0x120 - 320 and rd_i(0) == 0x120 - 320,
                 f"digital {[hex(v) for v in vals]}: max {rd_i(4)} half-dB (want {0x120 - 320}), 3 delays of 20 ms")
    dl, abi = tick(0x10, [0x1FF, 1, 2, 3])
    m.report(abi and rd_i(4) == -0x7FFF and dl == [], "digital: an all-ones read (no chip) is still reported as n/a (no update, no further reads)")
    tick(0x10, [0xC0] * 4)                                      # tick 1 of the window
    for _ in range(4):
        tick(0x10, [0xC0] * 4, enter=False)
    m.report(rd_i(24) == -0x7FFF, "digital 1 s window: still open after 5 ticks")
    tick(0x10, [0xC0] * 4, enter=False)
    m.report(rd_i(24) == 0xC0 - 320, f"digital 1 s window closes after the 6th tick ({1000 // (100 + 60)} per second; avg {rd_i(24)} half-dB, want {0xC0 - 320})")

def check_aprs_sublist(c, s, s2):
    """Stock APRS sub-list per channel mode (krk_aprs_render) and the APRS RX row (item 11): analog = 7 rows incl. APRS RX and RX Monitor,
    digital = the 8 stock rows; selecting item 11 hands the stations screen to the runner with krk_aprs_render as the
    restore function, and running that restore redraws all 7 rows."""
    IDX, CNT, VIS, PTR, SUB, ROW, L2 = 0x0003E5F8, 0x0003E8F8, 0x0003E8F4, 0x0003E904, 0x18000A90, 0x0003E4EA, 0x0003E664
    print("== APRS sub-list: analog shows APRS RX and RX Monitor (7 rows), digital keeps the 8 stock rows; item 11 opens the stations screen and Back restores 7 rows")
    cells = lambda: (struct.unpack("<H", read_mem(c, IDX, 2))[0], list(read_mem(c, IDX + 2, 8)), read_mem(c, CNT, 1)[0], read_mem(c, VIS, 1)[0])
    fk = {s["krk_line"]: 0}
    dwc.chan_poke(c, b"\x00")
    m.wmem(c, IDX, bytes(40))
    _, _, abi, _ = fcall(c, s["krk_aprs_render"], fakes=fk)
    n, items, cnt, vis = cells()
    m.report(abi and n == 7 and items[:7] == [0, 8, 4, 9, 10, 11, 12] and cnt == 7 and vis == 7 and read_mem(c, SUB, 1)[0] == 2,
             f"analog render: count {n}, items {items[:7]}, item count {cnt}, visible {vis}")
    dwc.chan_poke(c, b"\x10")
    fcall(c, s["krk_aprs_render"], fakes=fk)
    n, items, cnt, vis = cells()
    m.report(n == 8 and items == list(range(8)) and cnt == 8 and vis == 8, f"digital render: the 8 stock rows {items}, count {n}")
    dwc.chan_poke(c, b"\x00")
    runs = []

    def fk_run(regs, r0, r1):
        runs.append((r0, r1))
        return 0
    m.wmem(c, ROW, bytes([5]))
    m.wmem(c, L2 + 5, bytes([11]))
    fcall(c, s["krk_aprs_item"], fakes={s["krk_run_screen"]: fk_run})
    m.report(len(runs) == 1 and runs[0] == (s2["krk_aprsrx_screen"], s["krk_aprs_render"]),
             f"item 11 (row 6): runner called with the APRS RX screen and krk_aprs_render as restore: {[(hex(a_), hex(b_)) for a_, b_ in runs]}")
    m.wmem(c, IDX, bytes(40))
    m.wmem(c, CNT, bytes([0]))
    fcall(c, s["krk_aprs_render"], fakes=fk)
    n, items, cnt, vis = cells()
    m.report(n == 7 and items[:7] == [0, 8, 4, 9, 10, 11, 12] and cnt == 7 and vis == 7, f"after Back (restore = krk_aprs_render): all 7 rows redrawn {items[:7]}")


def check_f9(c):
    """F9 parrot / voice-frame layer: static splice checks (decoded from the IRAM pc) and in-target runs of the
    hooks, gates and watchdogs (tests/parrot_qemu_test.py). The baseband-window checks need a QEMU model that keeps
    the data written to 0x16000960: run tests/parrot_qemu_test.py --window in such an image."""
    import parrot_qemu_test as pq
    s1, s2 = syms(ELF, sizes=True), syms(ELF2, sizes=True)
    pq.static_checks(PATCHED.read_bytes(), s2, pq.STOCK.read_bytes(), s1)
    pq.run_checks(c, s1, s2, window=False)


class _ThreadOut:
    """sys.stdout while the booted runs overlap the halted checks: the main thread writes through, every other thread
    is buffered and replayed, in order, where the booted sections used to run (so the log reads as before)."""

    def __init__(self, real):
        self.real, self.buf, self.lock = real, [], threading.Lock()

    def write(self, s):
        if threading.current_thread() is threading.main_thread():
            return self.real.write(s)
        with self.lock:
            self.buf.append(s)
        return len(s)

    def flush(self):
        self.real.flush()


def booted_runs(s, s2):
    """Every check that boots its own QEMUs and walks the stock menu by key (no machine shared with the halted checks)."""
    try:
        optrow_checks.boot_checks(PATCHED, s, s2)        # booted runs: RX Monitor option level, Upload Beacon popups (own QEMUs)
        # Booted end-to-end runs of the nine Scrambler / Parrot rows that moved to the stock Channel Set
        # menu on 2026-11-03 (docs/chanset-submenu.md): the list carries items 22..30 with the right
        # labels, OK opens an option level and OK there commits with the RAM state read back. This is the
        # gate before flashing for those rows - the enter and commit paths are splices into stock jump
        # tables and nothing else exercises them end to end.
        import chanset_rows_e2e as cs
        print("== stock Channel Set rows, end to end (own QEMUs)")
        cs.run(PATCHED, m.report)
        # The Burst Voice nest under Channel Set (2026-11-06, docs/chanset-submenu.md 8): the child list, the three option
        # levels with their commits into the flash record, Back, and the screen child (tests/bvnest_e2e.py; four QEMUs).
        import bvnest_e2e as bvn
        print("== Channel Set > Burst Voice nest, end to end (own QEMUs)")
        bvn.run(PATCHED, m.report)
    except Exception:
        print("FAIL  booted runs raised:\n" + traceback.format_exc())


def main():
    s = syms(ELF)
    s2 = syms(ELF2)
    s2.update({k: v for k, v in syms(ELF5).items() if k.startswith("lq")})    # F3 lives in blob 5: the checks below keep their s2["lq..."] lookups
    # 2026-11-06: the booted runs (own QEMUs, walked by key) overlap the halted checks below instead of following them;
    # their output is buffered and replayed after them, so the log order is unchanged
    out = sys.stdout = _ThreadOut(sys.stdout)
    booted = threading.Thread(target=booted_runs, args=(s, s2))
    booted.start()
    img = PATCHED.read_bytes()
    rd32 = lambda a: struct.unpack("<I", img[a - 0x3000000:a - 0x3000000 + 4])[0]

    print("== splices")
    for name, a, want in (("txtrim_interp", 0x0300DB66, s2["krk_hook_txtrim_interp"]),
                          ("menu_append_hook", 0x03049DCA, s["krk_menu_append"]),
                          # analog APRS auto beacon (v39, docs/aprs-submenu.md section 7)
                          ("aprs_mode_hook", 0x03057AA8, s["krk_hook_aprs_mode"]),
                          ("aprs_tick_hook", 0x03054888, s2["krk_hook_aprs_tick"]),
                          ("aprs_send_now_hook", 0x030158B8, s2["krk_hook_aprs_send_now"]),
                          ("maint_tick_hook", 0x030547AE, s2["krk_hook_maint_tick"])):
        kind, half = decode_branch(img[a - 0x3000000:a - 0x3000000 + 4])
        m.report(kind == "bsr" and a + half * 2 == want, f"{name}: {kind} at {a:#x} -> {a + half * 2:#x} (want {want:#x})")
    m.report(img[0x57AAC:0x57AAE] == b"\x21\x08", "aprs_mode_hook: stock `bt 0x03057aee` kept after the stub call")
    m.report(img[0x5488C:0x548A4] == b"\x0c\x04" + b"\x03\x6c" * 11,
             "aprs_tick_tail: br16 0x030548a4 then nop16 filler over the displaced gate and stock tick call")
    for name, slot, idx, want in (("render", 0x0303DA48, 10, s["krk_render_stub"]),
                                  ("ok", 0x0303CDA8, 10, s["krk_ok_stub"]),
                                  ("item", 0x0303CF18, 10, s["krk_item_stub"])):
        table = rd32(slot)
        m.report(rd32(table + 4 * idx) == want, f"{name} table {table:#x}[10] -> {rd32(table + 4 * idx):#x} (want {want:#x})")

    print("== extension blob table (0x030ee000) and the stock APRS sub-list extension")
    nx, na, nrs, ncs = (krk_define(f"KRK_{k}_MAX") for k in ("EXT", "APRS", "RS", "CS"))
    CS0 = krk_define("KRK_CS_ITEM_DMRLINK")      # the first appended Channel Set item; the stock items are 0..CS0-1
    slots = [rd32(EXT_TABLE + 4 * i) for i in range(TBL_SLOTS)]
    # The runtime check in krk_core.c (ext_ok) drops any screen whose descriptor or label lies outside
    # [KRK_EXT_LO, KRK_EXT_HI): test against those same constants, not the allocation, so a blob that
    # outgrows the bound fails here instead of silently losing menu rows (v34, 2026-10-27).
    ext_rng = krk_ext_ranges()
    ext_ok = lambda a: any(lo <= a < hi for lo, hi in ext_rng)
    lbl = lambda p_: img[rd32(p_) - 0x3000000:].split(b"\0")[0] if ext_ok(p_) and ext_ok(rd32(p_)) else None
    bad = [f"{p_:#x}" for p_ in slots if p_ and not (ext_ok(p_) and ext_ok(rd32(p_)))]
    m.report(not bad, f"every extension screen and label inside {[f'[{lo:#x},{hi:#x})' for lo, hi in ext_rng]} (KRK_EXT*_LO/HI): bad {bad}")
    # Slots 1 and 2 are empty since 2026-11-03: APRS RX lives only in the stock APRS menu (item 11,
    # checked below) and TX Power Trim, being radio-wide, moved to stock Radio Settings (the
    # KRK_RS_TABLE entry checked further down). Neither appears in the KRAKEN list any more.
    # Slot 0 holds the VOX Monitor (krk_vox.c: a live instrument, so it stays in the KRAKEN list
    # like Squelch Survey); the VOX Probe it replaced is retired.
    m.report(lbl(slots[0]) == b"VOX Monitor" and [slots[1], slots[2]] == [0, 0],
             f"KRAKEN extension slot 0 = VOX Monitor (the VOX engine's bench screen; the VOX Probe it replaced is retired from the build), slots 1-2 retired: "
             f"{[lbl(slots[0])] + slots[1:3]}")
    # Slot 6 (Squelch Mode) was retired on 2026-11-03 with slot 0: both are radio-wide settings and
    # moved to stock Radio Settings. Squelch Survey stays: it is a live instrument, not a setting.
    # Slots 9..13 (the five Parrot rows) were retired the same day: Parrot is per-channel and digital,
    # so all five moved to stock Channel Set (the KRK_CS_TABLE entries checked below).
    m.report(slots[6] == 0 and [lbl(p_) for p_ in slots[7:9]] == [b"Squelch Survey", b"Satellites"]
             and slots[9:14] == [0] * 5,
             f"KRAKEN extension slots 6-13 (slot 6 retired with Squelch Mode, 9-13 with the five Parrot rows): "
             f"{[slots[6]] + [lbl(p_) for p_ in slots[7:9]] + slots[9:14]}")
    # Slots 14 (Burst Voice) and 15 (DMR Link) were retired on 2026-11-03: both are per-channel
    # features, so they moved to the stock Channel Set menu (the KRK_CS_TABLE entries checked below).
    m.report(slots[14] == 0 and slots[15] == 0, f"KRAKEN extension slots 14, 15 retired with Burst Voice and DMR Link: {[slots[14], slots[15]]}")
    (b5lo, b5hi), (fslo, fshi) = claim("kraken_ui5_code"), claim("kraken_feat_state")
    # kraken_ui5's .bss.feat is appended to the shared kraken_feat_state claim (kraken_ui5.ld). allocations.json shrank that
    # claim to end at 0x180053a0 on 2026-10-30 (the SRAM after it is reserved), so lqst is bounded by the
    # claim itself, with its linked size (a hard-coded 0xe8 here was stale: lqst is 0xc4 and ends exactly at the claim end).
    lq_end = sum(syms(ELF5, sizes=True)["lqst"][0])
    m.report(s2["lq_enter"] >= b5lo and s2["lqst"] >= fslo
             and s2["lq_screen_tick"] < b5hi and lq_end <= fshi,
             f"F3 DMR link blob kraken_ui5 still linked: code inside [{b5lo:#x},{b5hi:#x}), lqst [{s2['lqst']:#x},{lq_end:#x}) inside kraken_feat_state [{fslo:#x},{fshi:#x})")
    sec = img[b5lo - 0x3000000:b5hi - 0x3000000]
    m.report(sec != b"\xff" * 0x1000 and rd32(b5lo) in range(b5lo, b5hi),
             f"blob kraken_ui5 is present in the reclaimed ramdump_probe sector {b5lo:#x} (label pointer inside it)")
    # Slots 3 and 4 (the Scrambler Off/On switch and the numeric Key) were retired on 2026-11-03: both
    # are per-channel and digital, so they moved to stock Channel Set. Scrambler Fmt (slot 5) stays in
    # the KRAKEN list - it is a key-format / interop knob, not part of the user-facing pair.
    m.report(slots[3] == 0 and slots[4] == 0 and lbl(slots[5]) == b"Scrambler Fmt" and slots[5] == s2["krk_scrfmt_row"],
             f"F12 rows: slots 3, 4 retired with the Scrambler switch and Key (now stock Channel Set rows), "
             f"slot 5 = Scrambler Fmt: {[slots[3], slots[4], lbl(slots[5])]}")
    print("     KRAKEN extension slots 3..:", [lbl(p_) if p_ else None for p_ in slots[3:nx]])
    m.report([lbl(p_) for p_ in slots[nx:nx + 4]] == [b"Upload ID", b"Upload Beacon", b"Callsign", b"APRS RX"]
             and slots[nx + 3] == s2["krk_aprsrx_screen"],
             f"APRS analog slots (items 8, 9, 10, 11): {[lbl(p_) for p_ in slots[nx:nx + 4]]}, item 11 = the APRS RX stations screen (its only menu row)")
    # Radio Settings (Settings category 5, item 0): the row appended as item 4 (docs/settings-submenu.md).
    rs = nx + na
    want_rs = [(b"TX Power Trim", "krk_txtrim_screen"), (b"Squelch Mode", "krk_squelch_screen"),
               (b"Freq Unlock", "krk_band_screen")][:nrs]
    got_rs = [(lbl(slots[rs + i]), slots[rs + i]) for i in range(nrs)]
    m.report(got_rs == [(w, s2[sym]) for w, sym in want_rs],
             f"Radio Settings slots (items 4..) = the radio-wide rows that left the KRAKEN list: {[g[0] for g in got_rs]}")
    # The descriptor must now name 5 rows per language, the stock four plus ours, out of relocated
    # arrays (the stock arrays abut their descriptor and cannot grow in place).
    for lang, base, stock_arr in ((0, 0x030B8488, 0x030B8468), (1, 0x030B8490, 0x030B8478)):
        arr, cnt, sub = rd32(base), img[base + 4 - 0x3000000], img[base + 5 - 0x3000000]
        stock = [rd32(stock_arr + 4 * k) for k in range(4)]
        ours = [rd32(arr + 4 * k) for k in range(4 + nrs)]
        newlbls = [img[p_ - 0x3000000:].split(b"\0")[0] for p_ in ours[4:]]
        m.report(cnt == 4 + nrs and sub == 2 and arr != stock_arr and ours[:4] == stock
                 and newlbls == [w for w, _ in want_rs],
                 f"Radio Settings descriptor lang {lang}: count {cnt}, subtype {sub}, relocated to {arr:#x}, 4 stock strings kept, appended {newlbls}")
    # The level-4 header draws desc[lang].ptr[row] itself, so the row needs no title hook; the label
    # above being in place is what makes the header correct.

    # Channel Set (Settings category 5, level-2 item 1): the nine rows appended as items 22..30
    # (docs/chanset-submenu.md). DMR Link and Burst Voice are placed by their blob ORIGIN because
    # blob 2 cannot name blobs 4 and 5 symbols; the rest are blob-2 symbols.
    #
    # 2026-11-03: the Scrambler pair and the five Parrot rows joined them, with the user-facing labels
    # the nested layout asked for. They are FLAT rows: grouping them under two nested parent rows with
    # child lists was built and measured and does not work on this firmware - docs/nested-submenu.md.
    cs = nx + na + nrs
    want_cs = ([(b"DMR Link", claim("kraken_ui5_code")[0]), (b"Burst Voice", claim("kraken_ui4_code")[0])]
               + [(b"Scrambler On/Off", s2["krk_scramble_on_screen"]), (b"Key", s2["krk_scrkey_row"]),
                  (b"Parrot On/Off", s2["krk_parrot_arm_screen"]), (b"Replay Audio", s2["krk_parrot_play_screen"]),
                  (b"Check", s2["krk_parrot_check_screen"]), (b"Clear Audio", s2["krk_parrot_clear_screen"]),
                  (b"Status", s2["krk_parrot_status_screen"])])[:ncs]
    got_cs = [(lbl(slots[cs + i]), slots[cs + i]) for i in range(ncs)]
    m.report(got_cs == want_cs, f"Channel Set slots (items {CS0}..{CS0 + ncs - 1}) = the per-channel rows that left the KRAKEN list: {[g[0] for g in got_cs]}")
    # The two stock label arrays abut each other AND the descriptor (zh+22*4 = en, en+22*4 = the
    # descriptor), so neither can grow in place: both are relocated with CS0 + ncs entries.
    for lang, base, stock_arr in ((0, 0x030B89A0, 0x030B88F0), (1, 0x030B89A8, 0x030B8948)):
        arr, cnt, sub = rd32(base), img[base + 4 - 0x3000000], img[base + 5 - 0x3000000]
        stock = [rd32(stock_arr + 4 * k) for k in range(22)]
        ours = [rd32(arr + 4 * k) for k in range(CS0, CS0 + ncs)]
        newlbls = [img[p_ - 0x3000000:].split(b"\0")[0] for p_ in ours]
        m.report(cnt == CS0 + ncs and sub == 2 and arr != stock_arr and [rd32(arr + 4 * k) for k in range(CS0)] == stock
                 and newlbls == [w for w, _ in want_cs],
                 f"Channel Set descriptor lang {lang}: count {cnt}, subtype {sub}, relocated to {arr:#x}, 22 stock strings kept, appended {newlbls}")
    # Both key events dispatch on the level-3 row through a jump table, so BOTH tables are relocated
    # with two extra entries and BOTH bounds are widened. The commit table is what makes a latch
    # unnecessary here (Radio Settings' commit site 0x030b11c4[0] is a plain no-op and needs one).
    # A nest parent overrides its two entries (menu_hosts.json "stubs"): Burst Voice's level-4 init is the child list
    # (krk_cs_bv_kids_stub, blob 2) and its OK is stock's row-15 arm 0x0304e254 = set_mode(0x23, 1) (docs/chanset-submenu.md 8).
    BVI = krk_define("KRK_CS_ITEM_BV")
    override = {("enter", BVI): s2["krk_cs_bv_kids_stub"], ("commit", BVI): 0x0304E254}
    for name, bound_a, bound_want, ptr_a, stock_tbl, stub, nstock in (
            ("enter", 0x03047332, CS0 + ncs, 0x03047370, 0x030B0A18, "krk_cs_item_stub", CS0),
            ("commit", 0x0304DC9A, CS0 + ncs, 0x0304DCC0, 0x030B116C, "krk_cs_ok_stub", CS0)):
        imm = img[bound_a - 0x3000000] + 1              # cmphsi encodes value - 1
        tbl = rd32(ptr_a)
        stock = [rd32(stock_tbl + 4 * k) for k in range(nstock)]
        want_tbl = [override.get((name, nstock + i), s[stub]) for i in range(ncs)]
        m.report(imm == bound_want and tbl != stock_tbl
                 and [rd32(tbl + 4 * k) for k in range(nstock)] == stock
                 and [rd32(tbl + 4 * (nstock + i)) for i in range(ncs)] == want_tbl,
                 f"Channel Set {name} dispatch: bound cmphsi {imm} (want {bound_want}), table relocated {stock_tbl:#x} -> {tbl:#x}, "
                 f"{nstock} stock blocks kept, entries {nstock}..{nstock + ncs - 1} -> {stub} {s[stub]:#x} "
                 f"(item {BVI}: {want_tbl[BVI - nstock]:#x})")
    # The Burst Voice nest's three level-5 splices: a bsr over each stock `cmp; bt exit` pair (hooks.json cs_l5_*_hook)
    for name, a, sym_ in (("level-5 init", 0x03045094, "krk_cs_l5_enter_stub"), ("level-5 OK", 0x0305031A, "krk_cs_l5_ok_stub"),
                          ("level-5 header", 0x03038AB8, "krk_cs_l5_title_stub")):
        kind, half = decode_branch(img[a - 0x3000000:a - 0x3000000 + 4])
        m.report(kind == "bsr" and a + half * 2 == s2[sym_],
                 f"Channel Set {name} splice: bsr at {a:#x} -> {a + half * 2:#x} (want {sym_} {s2[sym_]:#x})")
    # The generator wrapper must hook the MENU-TREE call site only: the second mode-6 call at
    # 0x0303e294 belongs to a wizard that installs cells from descriptor 0x030b8150 (CTC/DCS) and
    # never reads 0x030b89a0, so hooking it would append rows to an unrelated list.
    kind, half = decode_branch(img[0x03042406 - 0x3000000:0x0304240A - 0x3000000])
    m.report(kind == "bsr" and 0x03042406 + half * 2 == s["krk_hook_gen"],
             f"cs_gen_hook: bsr at 0x03042406 -> {0x03042406 + half * 2:#x} (want krk_hook_gen {s['krk_hook_gen']:#x})")
    kind2, half2 = decode_branch(img[0x0303E294 - 0x3000000:0x0303E298 - 0x3000000])
    m.report(kind2 == "bsr" and 0x0303E294 + half2 * 2 == 0x030493F8,
             f"the other mode-6 call site 0x0303e294 is left on the stock generator {0x0303E294 + half2 * 2:#x} (a different wizard, descriptor 0x030b8150)")
    for a, want in ((s["krk_cs_item_stub"], 0x03047CF4), (s["krk_cs_ok_stub"], 0x0304E55A)):
        ins = [re.sub(r"\s+", " ", l.split("\t", 2)[-1].split("//")[0]).strip()
               for l in subprocess.run(["csky-abiv2-elf-objdump", "-d", f"--start-address={a:#x}", f"--stop-address={a + 14:#x}", str(ELF)],
                                       capture_output=True, text=True, check=True).stdout.splitlines()
               if re.match(r"\s+[0-9a-f]+:\t", l)]
        m.report(ins[0].startswith("mov r0,") and ins[1].startswith("bsr") and f"{want:#x}" in ins[2] and ins[3] == "jmp r3",
                 f"stub at {a:#x}: mov r0,<row>; bsr handler; lrw r3,{want:#x} (the dispatcher epilogue); jmp r3: {ins[:4]}")
    # Analog rows: items 8 and 9 reuse the stock strings of items 1 (Upload ID) and 6 (Upload Beacon),
    # item 10 is our Callsign label (docs/aprs-submenu.md, krk_core.c krk_aprs_render).
    for lang, base, stock_arr in ((0, 0x030BA924, 0x030BA8E4), (1, 0x030BA92C, 0x030BA904)):
        arr, cnt, sub = rd32(base), img[base + 4 - 0x3000000], img[base + 5 - 0x3000000]
        stock = [rd32(stock_arr + 4 * k) for k in range(8)]
        ours = [rd32(arr + 4 * k) for k in (8, 9, 10, 11, 12)]
        call = img[ours[2] - 0x3000000:].split(b"\0")[0]
        rxl = img[ours[3] - 0x3000000:].split(b"\0")[0]
        monl = img[ours[4] - 0x3000000:].split(b"\0")[0]
        m.report(cnt == 13 and monl == b"RX Monitor" and sub == 2 and [rd32(arr + 4 * k) for k in range(8)] == stock and ours[0] == stock[1] and ours[1] == stock[6] and call == b"Callsign" and rxl == b"APRS RX",
                 f"APRS descriptor lang {lang}: count {cnt}, subtype {sub}, 8 stock strings kept, items 8/9 = stock Upload ID/Upload Beacon strings, item 10 {call}, item 11 {rxl}, item 12 {monl}")
    tbl = rd32(0x0303D56C)
    ent = [rd32(tbl + 4 * k) for k in range(13)]
    m.report(ent[:8] == [0x0303D570, 0x0303D5DE, 0x0303D634, 0x0303D6A4, 0x0303D72C, 0x0303D756, 0x0303D7D8, 0x0303D848]
             and ent[8] == ent[9] == ent[10] == ent[11] == ent[12] == s["krk_aprs_item_stub"] and img[0x3D556:0x3D558] == b"\x0c\x3a",
             f"APRS item table {tbl:#x}: 8 stock case blocks, items 8..12 -> stub {s['krk_aprs_item_stub']:#x}; bound cmphsi 13")
    # The render case block 0x0303dd40 is `bsr krk_aprs_render; br 0x0303dd78` (v39)
    kind, half = decode_branch(img[0x3DD40:0x3DD44])
    m.report(kind == "bsr" and 0x0303DD40 + half * 2 == s["krk_aprs_render"] and img[0x3DD44:0x3DD46] == b"\x1a\x04",
             f"APRS render block: {kind} at 0x0303dd40 -> {0x0303DD40 + half * 2:#x} (want krk_aprs_render {s['krk_aprs_render']:#x}), then br16 to 0x0303dd78")

    print("== stock Call Log sub-list: Last Heard row (docs/calllog-submenu.md)")
    for name, a in (("calllog_gen_render", 0x0303DC06), ("calllog_gen_entry2", 0x03036B62), ("calllog_gen_entry3", 0x0303EE3E)):
        kind, half = decode_branch(img[a - 0x3000000:a - 0x3000000 + 4])
        m.report(kind == "bsr" and a + half * 2 == s["krk_hook_gen"],
                 f"{name}: {kind} at {a:#x} -> {a + half * 2:#x} (want krk_hook_gen {s['krk_hook_gen']:#x})")
    kind, half = decode_branch(img[0x3CF98:0x3CF9C])
    m.report(kind == "bsr" and 0x0303CF98 + half * 2 == s["krk_calllog_item_stub"] and img[0x3CF96:0x3CF98] == b"\x43\x3b",
             f"calllog_item_hook: `cmpnei r3,3` kept, {kind} at 0x0303cf98 -> {0x0303CF98 + half * 2:#x} (want stub {s['krk_calllog_item_stub']:#x})")
    for lang, base, stock_arr in ((0, 0x030B8060, 0x030B8040), (1, 0x030B8068, 0x030B8050)):
        arr, cnt, sub = rd32(base), img[base + 4 - 0x3000000], img[base + 5 - 0x3000000]
        stock = [rd32(stock_arr + 4 * k) for k in range(4)]
        lh_lbl = img[rd32(arr + 16) - 0x3000000:].split(b"\0")[0]
        m.report(cnt == 5 and sub == 2 and [rd32(arr + 4 * k) for k in range(4)] == stock and lh_lbl == b"Last Heard",
                 f"Call Log descriptor lang {lang}: count {cnt}, subtype {sub}, 4 stock strings kept, item 4 {lh_lbl}")

    with halted_machine(PATCHED) as c:
        st = s2["t"]         # struct t in krk_txtrim.c (blob 2 since 2026-11-02): u32 magic; u8 eighths, last_raw, last_out, have_raw
        def call_trim(freq, level, raw):
            res = call_via(c, s2["krk_hook_txtrim_interp"], (freq, level), stop=STOCK_INTERP, ret=raw)     # the stock function "returns" raw
            args_ok = res.hit and res.at_stop["r0"] == freq and res.at_stop["r1"] == level
            return args_ok, res.abi, res.regs["r0"]

        print("== F6 power-up: state magic absent -> 100%")
        m.wmem(c, st, b"\xA5" * 8)                    # SRAM noise, not the magic
        args_ok, abi, got = call_trim(0x0249F0, 2, 0xA0)
        m.report(args_ok and abi and got == 0xA0, f"noise state: returns stock byte {got:#x} (want 0xa0), args/ABI ok")
        magic = struct.unpack("<I", read_mem(c, st, 4))[0]
        m.report(magic == TRIM_MAGIC, f"state initialised (magic {magic:#x})")

        print("== F6 scaling")
        for e in (8, 7, 6, 5, 4, 3, 2, 1):
            m.wmem(c, st + 4, bytes([e]))
            for raw in (0x00, 0x37, 0xA0, 0xFF):
                args_ok, abi, got = call_trim(0x0249F0 + raw, raw % 3, raw)
                want = (raw * e + 4) // 8
                st_bytes = read_mem(c, st + 4, 4)
                m.report(args_ok and abi and got == want and st_bytes[1] == raw and st_bytes[2] == want and st_bytes[3] == 1,
                         f"trim {e}/8, raw {raw:#04x}: returns {got:#04x} (want {want:#04x}); last_raw/last_out/have_raw recorded")
        print("== F1 last heard")
        lh = s["lh"]
        RX_DST, RX_SRC, RX_TYPE, RX_CLASS, CH_CFG, MEAN = 0x3BD30, 0x3BD34, 0x3BD2C, 0x3BD2D, 0x3E1E9, 0x3FDE0
        ENTRY, E0 = 44, lh + 12

        def run_hook(fn, stock, args=(), fake_return=True):
            res = call_via(c, fn, args, stop=stock, ret=0x2 if fake_return else None)     # 0x2: what the stock byte read would return
            return res.hit, res.abi, res.regs["r0"]

        def entry(idx_from_newest):
            hdr = read_mem(c, lh, 12)
            head, count = hdr[8], hdr[9]
            e = read_mem(c, E0 + ENTRY * ((head + 16 - idx_from_newest) % 16), ENTRY)
            src, dst = struct.unpack("<II", e[:8])
            return dict(count=count, src=src, dst=dst, kind=e[16], cc=e[17], ts=e[18], rssi=e[19],
                        open=e[20], hits=e[21], have_alias=e[22], alias=e[24:44].split(b"\0")[0].decode("latin1"))

        def set_call(src, dst, typ, cls, cfg, mean):
            m.wmem(c, RX_DST, struct.pack("<II", dst, src))
            m.wmem(c, RX_TYPE, bytes([typ, cls]))
            m.wmem(c, CH_CFG, bytes([cfg]))
            m.wmem(c, MEAN, bytes([mean]))

        m.wmem(c, lh, b"\xA5" * 12)                  # power-on noise: no magic
        set_call(3125001, 91, 1, 1, 0x11, 180)       # group call to TG 91, CC1, TS2
        hit, abi, r0 = run_hook(s["krk_hook_lh_start"], 0x030238B4)
        e = entry(0)
        m.report(hit and abi and r0 == 2, "start hook: reaches the stock byte read, returns its r0, ABI preserved")
        m.report(e["count"] == 1 and e["src"] == 3125001 and e["dst"] == 91 and e["kind"] == 1 and e["cc"] == 1
                 and e["ts"] == 2 and e["rssi"] == 180 and e["open"] == 1 and e["hits"] == 1,
                 f"new entry from noise state: {e}")
        set_call(3125001, 91, 1, 1, 0x11, 200)
        run_hook(s["krk_hook_lh_start"], 0x030238B4)
        e = entry(0)
        m.report(e["count"] == 1 and e["hits"] == 2 and e["rssi"] == 200, f"repeated header refreshes the open entry: hits {e['hits']}, rssi {e['rssi']}")

        alias_src = claim("kraken_ui_state2")[0]
        feat = claim("kraken_feat_state")[0]       # the alias hook's destination buffer
        m.wmem(c, alias_src, b"N0CALL Bob\0".ljust(0x20, b"\0"))
        hit, abi, _ = run_hook(s["krk_hook_lh_alias"], 0x0002B338, args=(feat, alias_src, 0x20), fake_return=False)
        e = entry(0)
        copied = read_mem(c, feat, 11)
        m.report(hit and abi and copied == b"N0CALL Bob\0", "alias hook: stock memcpy ran with the caller's arguments, ABI preserved")
        m.report(e["have_alias"] == 1 and e["alias"] == "N0CALL Bob", f"alias attached to the open entry: {e['alias']!r}")

        hit, abi, _ = run_hook(s["krk_hook_lh_end"], 0x030293AC)
        e = entry(0)
        m.report(hit and abi and e["open"] == 0, "end hook: reaches modem_control_state_stop, closes the entry, ABI preserved")

        set_call(3125001, 91, 1, 1, 0x11, 150)
        run_hook(s["krk_hook_lh_start"], 0x030238B4)
        e = entry(0)
        m.report(e["count"] == 2 and e["open"] == 1 and e["have_alias"] == 0, "same talker after call end: new entry, alias cleared")

        set_call(1234567, 3125001, 0, 1, 0x05, 120)  # private call, CC5, TS1
        run_hook(s["krk_hook_lh_start"], 0x030238B4)
        e, prev = entry(0), entry(1)
        m.report(e["count"] == 3 and e["kind"] == 2 and e["cc"] == 5 and e["ts"] == 1 and prev["open"] == 0,
                 f"different call: new private entry, previous one closed: {e}")
        m.wmem(c, alias_src, b"Mallory\0".ljust(0x20, b"\0"))
        m.wmem(c, RX_SRC, struct.pack("<I", 7777777))  # alias for a talker other than the open entry's
        run_hook(s["krk_hook_lh_alias"], 0x0002B338, args=(feat, alias_src, 0x20), fake_return=False)
        m.report(entry(0)["have_alias"] == 0, "alias for a different source ID is not attached")
        print("== top-level list: APRS always shown, KRAKEN appended (both builders)")
        CUR, BUF = 0x18005800, 0x18005810            # count (u16) and item buffer, as r4/r5 point at
        def run_append(fn, stop, items):
            m.wmem(c, CUR, struct.pack("<H", len(items)))
            m.wmem(c, BUF, bytes(items) + b"\xEE" * 8)
            # r0 must survive (dispatcher return value); r4/r5 point at the count and the item buffer; r3 = the count, as stock
            # leaves it at both sites (idx23's stub redoes stock's store of it)
            res = call_via(c, fn, (0x1234,), stop=None if stop == m.SENTINEL else stop, resume=False,
                           regs={3: len(items), 4: CUR, 5: BUF}, canary=range(6, 12))
            r = res.regs
            n = struct.unpack("<H", read_mem(c, CUR, 2))[0]
            got = list(read_mem(c, BUF, n))
            keep = r["r0"] == 0x1234 and r["r4"] == CUR and r["r5"] == BUF and all(r[f"r{i}"] == CANARY + i for i in range(6, 12))
            return res.pc, got, keep, r
        for label, fn, stop in (("idx23 (full menu)", s["krk_menu_append"], 0x0304A112),
                                ("idx24 (VFO short menu)", s["krk_menu_append24"], m.SENTINEL)):
            pc, got, keep, r = run_append(fn, stop, [1, 5, 8])
            m.report(pc == stop and got == [1, 5, 8, 9, 4, 10] and keep,
                     f"{label}: APRS and Call Log missing -> list {got} (9, 4 then 10 appended), r0/r4-r11 preserved")
            pc, got, keep, r = run_append(fn, stop, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
            m.report(pc == stop and got == list(range(10)) + [10] and keep,
                     f"{label}: APRS and Call Log present -> list {got} (only 10 appended)")
            pc, got, keep, r = run_append(fn, stop, [1, 4, 8])
            m.report(pc == stop and got == [1, 4, 8, 9, 10] and keep,
                     f"{label}: Call Log present, APRS missing -> list {got} (9 then 10, no second 4)")
        pc, got, keep, r = run_append(s["krk_menu_append24"], m.SENTINEL, [1, 5])
        m.report(r["r3"] == 5 and r["r2"] == read_mem(c, 0x3FDC0, 1)[0],
                 "idx24 stub leaves r3 = new count and r2 = byte at 0x3fdc0 (the displaced instructions)")

        print("== Call Log: generator wrapper appends Last Heard (item 4) after the stock items")
        STOCK_GEN = 0x030493F8

        def run_gen(mode, stock_items):
            """Calls krk_hook_gen(mode, CUR, CUR+2); at the stock generator's entry, plays its part
            (writes stock_items, checks it got the caller's arguments) and returns to the wrapper."""
            m.wmem(c, CUR, b"\xEE" * 40)
            res = call_via(c, s["krk_hook_gen"], (mode, CUR, CUR + 2), stop=STOCK_GEN, skip=True,
                           at_stop=lambda r: m.wmem(c, CUR, struct.pack("<H", len(stock_items)) + bytes(stock_items)))
            r = res.at_stop
            args_ok = res.hit and r["r0"] == mode and r["r1"] == CUR and r["r2"] == CUR + 2
            n = struct.unpack("<H", read_mem(c, CUR, 2))[0]
            return args_ok, res.abi, list(read_mem(c, CUR + 2, n)) if n <= 40 else n
        for mode, items, want, what in ((5, [0, 1, 2, 3], [0, 1, 2, 3, 4], "all four CPS rows on"),
                                        (5, [1, 3], [1, 3, 4], "CPS hides Missed and Sent"),
                                        (5, [], [4], "CPS hides every stock row: Last Heard alone"),
                                        (7, [0, 1], [0, 1], "another list mode: untouched"),
                                        (5, list(range(33)), list(range(33)), "list full (33): no append")):
            args_ok, abi, got = run_gen(mode, items)
            m.report(args_ok and abi and got == want, f"mode {mode}, {what}: {got} (args to stock, ABI preserved)")

        print("== Call Log: item compare chain (from stock 0x0303cf96 through the spliced bsr)")
        TAIL, ITEM3 = 0x0303D872, 0x0303CFB6
        for item, want, what in ((3, ITEM3, "item 3 -> stock Delete block"),
                                 (4, s["krk_calllog_item"], "item 4 -> krk_calllog_item (Last Heard)"),
                                 (5, TAIL, "item 5 -> stock out-of-range tail"),
                                 (9, TAIL, "item 9 -> stock out-of-range tail")):
            m.setreg(c, 3, item)
            m.setreg(c, 14, m.STACK_A)
            m.setreg(c, 0x48, 0x0303CF96)
            pc = m.run_to(c, [TAIL, ITEM3, s["krk_calllog_item"], m.SENTINEL])
            m.report(pc == want, f"{what} (stopped at {pc:#x})")

        dwc.check_dw(c, s, s2, img, STOCK_IMG.read_bytes())     # dual-watch channel tracking (docs/dual-watch.md)

        print("== APRS bearing hemisphere fix (stub at the 0x03063a04 hook)")
        kind, half = decode_branch(img[0x63A04:0x63A08])
        m.report(kind == "bsr" and 0x03063A04 + half * 2 == s["krk_hook_bearing"],
                 f"splice 0x03063a04 -> bsr {0x03063A04 + half * 2:#x} (krk_hook_bearing {s['krk_hook_bearing']:#x})")
        SP = m.STACK_A - 0x80                       # the caller's frame
        dbl = lambda v: struct.pack("<d", v)
        rdd = lambda off: struct.unpack("<d", read_mem(c, SP + off, 4) + read_mem(c, SP + off + 4, 4))[0]
        rdd2 = lambda lo, hi: struct.unpack("<d", read_mem(c, SP + lo, 4) + read_mem(c, SP + hi, 4))[0]

        # QEMU mis-emulates stock soft-float (stock int->double gives 256.0 for 360 here), so the
        # stub's maths are checked at the call boundary: every soft_double_sub call is intercepted,
        # its operands recorded, and it "returns" the exact 360 - x computed here. What this checks:
        # which slots are flipped, the operands and word order the stub passes, the write-back.
        SDSUB = 0x0306ECC8

        def run_bearing(own_ns, own_ew, st_ns, st_ew, own=(41.714, 72.727), st=(39.5, 104.25)):
            frame = bytearray(0x40)
            frame[0x34:0x3C] = dbl(own[0])            # our lat (lo 0x34, hi 0x38)
            frame[0x2C:0x34] = dbl(own[1])            # our lon (lo 0x2c, hi 0x30)
            d = dbl(st[1])                            # station lon: lo 0x14, hi 0x20 (non-contiguous)
            frame[0x14:0x18], frame[0x20:0x24] = d[:4], d[4:]
            frame[0x18:0x20] = dbl(st[0])             # station lat (lo 0x18, hi 0x1c)
            frame[0x24] = ord(st_ew)
            m.wmem(c, SP, bytes(frame))
            m.wmem(c, 0x38D42, own_ns.encode() + own_ew.encode())
            m.wmem(c, 0x3E923, f"181723,A,3930.000,{st_ns},10415.000,{st_ew},0.0,0.0,151026,\0".encode())
            subs = []

            def fsub(regs, r0, r1):
                a = struct.unpack("<d", struct.pack("<II", r0, r1))[0]
                b = struct.unpack("<d", struct.pack("<II", regs["r2"], regs["r3"]))[0]
                subs.append((a, b))
                return struct.unpack("<II", struct.pack("<d", a - b))
            _, _, abi, _ = fcall(c, s["krk_hook_bearing"], fakes={SDSUB: fsub}, sp=SP, limit=4)
            got = (rdd(0x34), rdd(0x2C), rdd(0x18), rdd2(0x14, 0x20))
            displaced = read_mem(c, SP, 4) == read_mem(c, SP + 0x18, 4)
            return abi, displaced, got, subs

        close = lambda a, b: abs(a - b) < 1e-9
        abi, disp, (ola, olo, sla, slo), subs = run_bearing("N", "W", "N", "W")
        m.report(abi and disp, "stub returns to the caller, r4-r11/sp preserved, displaced ld/st re-executed")
        m.report(len(subs) == 2 and all(close(a_, 360.0) for a_, _ in subs) and close(subs[0][1], 72.727) and close(subs[1][1], 104.25),
                 f"owner N/W, station N/W: two subtractions 360 - x for x = {[round(b_, 3) for _, b_ in subs]} (our lon, station lon)")
        m.report(close(ola, 41.714) and close(olo, 360 - 72.727) and close(sla, 39.5) and close(slo, 360 - 104.25),
                 f"  written back: lons {olo:.3f}, {slo:.3f}; lats unchanged {ola:.3f}, {sla:.3f}")
        abi, disp, (ola, olo, sla, slo), subs = run_bearing("S", "E", "S", "E")
        m.report(abi and len(subs) == 2 and close(subs[0][1], 41.714) and close(subs[1][1], 39.5)
                 and close(ola, 360 - 41.714) and close(sla, 360 - 39.5) and close(olo, 72.727) and close(slo, 104.25),
                 f"owner S/E, station S/E: lats flipped ({ola:.3f}, {sla:.3f}), lons unchanged ({olo:.3f}, {slo:.3f})")
        abi, disp, got, subs = run_bearing("N", "E", "N", "E")
        m.report(abi and disp and not subs, "owner N/E, station N/E: nothing flipped, no subtraction")
        check_small_features(c, s, s2, img)          # F12 / F11 / F3 (2026-10-29)
        check_aprs_sublist(c, s, s2)
        optrow_checks.static_checks(img, s, ELF)     # option level / popup splices and stubs
        optrow_checks.unit_checks(c, s, s2, dwc)   # option level cells, title, commit; popups; one-shot runner; Upload Beacon one-shot
        convert_checks.unit_checks(c, s, s2, dwc)   # 2026-11-02: Squelch Mode / TX Power Trim / Parrot option rows, one-shot and screen rows, trim state
        print("== F14 RX Monitor")
        mon_checks.check_mon(c, s, s2, img, STOCK_IMG.read_bytes(), dwc)
        check_f12_review(c, s, s2, img)              # review fixes 2026-10-29
        check_sweeps(c, s, s2, img)                  # one-action sweeps: F3 key 2, F11 key 6, F12 P1
        check_f10(c, img, s2, syms(ELF3))
        check_back_exit(c, s, s2, syms(ELF4))        # 2026-11-02: Parrot / Burst Voice Back
        check_exit_and_rssi(c, s)                    # v52: UI freeze after exit, RSSI on DMR
        print("== F9 parrot (voice-frame layer)")
        check_f9(c)
        print("== F13 burst voice (blob 4)")
        check_f13(c, img, s, s2)
    booted.join()
    sys.stdout = out.real
    text = "".join(out.buf)
    sys.stdout.write(text)
    if re.search(r"^FAIL", text, re.M):                 # m.report's `ok &= res` is not atomic across the two threads
        m.ok = False
    print("ALL PASS" if m.ok else "FAILURES")
    sys.exit(0 if m.ok else 1)


if __name__ == "__main__":
    main()
