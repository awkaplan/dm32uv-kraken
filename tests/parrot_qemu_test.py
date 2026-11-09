#!/usr/bin/env python3
"""In-target tests of the F9 parrot / voice-frame layer (patches/kraken_ui/krk_vframe.c, krk_parrot.c) on the
COMBINED image under -M dm32, halted at reset (no boot): the real CK803S code runs over the gdb stub.

  static_checks(img, ...)   the five IRAM-copy splices (decoded from the IRAM pc), stock bytes, the boot copy range,
                            the hook table, the claims
  run_checks(m, c, ..., window)
                            the TX hook at its REAL call sites (IRAM 0x2e9e4->0x2e9ee, 0x2eaea->0x2eaf4), the RX hook
                            at 0x2ed5e->0x2ed68, H1/H2 through the main blob's Last Heard wrappers, the screen tick state
                            machine with every safety gate, abort, watchdog and stale-session case. Stock RTOS/radio
                            calls (event-flag post, rx-active, inhibit) are intercepted at their entry and answered.
  window=True               also checks the bytes in the baseband voice window 0x16000960. That window only retains
                            data in a dm32uv-re QEMU build whose dm32_dmrram model keeps writes; in the default
                            image it reads zeros, so the normal suite passes window=False.

Standalone, with --window, inside such an image (mount the workspace as tools/build.sh does):
  python3 /w/dm32uv-unleashed/tests/parrot_qemu_test.py --window
"""
import argparse
import struct
import sys

from build_artifacts import EXT_MAX, EXT_TABLE, HOOK_TBL, PATCHED, STOCK_IMG as STOCK, elf, syms as _syms, krk_define      # first: puts the tool directories on sys.path
import mi_rng_test as m
from build_patch import decode_branch
import dw_checks as dwc
import target as tg
from claims import claim
from gdb_rsp import read_mem
from qemu_menu import OS_TICKS
from target import halted_machine

FLASH = 0x03000000
BIG, BIG_END = claim("kraken_bigbuf")
IRAM_TO_FLASH = 0x030AFC20
WINDOW = 0x16000960
TX_BUF, RX_BUF = 0x49B18, 0x46B38
PTT_GROUP, OSFLAG_POST = 0x0003A4F0, 0x0300C494
STOCK_RX_ACTIVE, STOCK_INHIBIT, STOCK_DUAL = 0x0300F2A4, 0x0302ED50, 0x030641E4
STOCK_LH_READ, STOCK_LH_STOP = 0x030238B4, 0x030293AC
TXUP, VFO8, CIPHER, BAND3, OWN_ID = 0x3BD50, 0x3E1E4, 0x33824, 0x3E31B, 0x33CD0
RX_DST, RX_SRC, RX_TYPE = 0x3BD30, 0x3BD34, 0x3BD2C
# (flash splice address, IRAM pc, hook symbol, live?) ; the call-site run starts 6 bytes before the bsr
TX_SITES = [(0x030DE60A, 0x2E9EA), (0x030DE710, 0x2EAF0)]          # live
TX_DEAD = [(0x030DE2E0, 0x2E6C0), (0x030DE440, 0x2E820)]
RX_SITE = (0x030DE984, 0x2ED64)
SILENCE = bytes.fromhex("b9e88152617300 2a6b".replace(" ", "")) * 3
# struct layouts (krk_vframe.h / krk_parrot.c; sizes asserted against nm -S)
VF = dict(magic=0, stamp=4, flags=8, done=9, tap_n=10, quiet=11, t_quiet=12, tx_fn=16, rx_sink=20, t_begin=24, max_ticks=28,
          max_bursts=32, tx_idx=36, end_idx=40, tx_calls=44, tx_subst=48, tx_silence=52, tx_pass=56, rx_calls=60, rx_sunk=64,
          rel_posts=68, tap=72)
PP = dict(magic=0, stamp=4, state=8, reason=9, t_state=12, deadline=16, t_rel=20, watch=24, t_press=28, t_clear=32,
          armed=36, rec=37, ready=38, play_req=39, chk_req=40, lead=41, pass_=42, chk=43, chk_done=44, synth=45,
          last_state=46, up_seen=47, side=48, n=50, n_tx=52, src=56, dst=60, kind=64, buf=68, echoes=72, conf_t=108, conf_key=112)
PS = dict(OFF=0, IDLE=1, REC=2, HOLD=3, KEYING=4, SEND=5, UNKEY=6, COOL=7)
SCAN_OK, SCAN_1, SCAN_0 = 0x03, 0x10, 0x0F


def syms(elf_):
    return _syms(elf_, sizes=True)


def feat(sy, name):
    """the instance of a (possibly duplicated static) symbol that lives in the shared .bss.feat claim"""
    lo, hi = claim("kraken_feat_state")
    c = [a for a, _ in sy[name] if lo <= a < hi]
    assert len(c) == 1, (name, sy[name])
    return c[0]


def static_checks(img, s2, stock, s1):
    """no QEMU: bytes of the built image against the stock image and the design (docs/parrot-tx.md)"""
    rd32 = lambda a: struct.unpack("<I", img[a - FLASH:a - FLASH + 4])[0]
    top = {k: v[0][0] for k, v in s2.items()}
    print("== F9 parrot: IRAM-copy splices")
    for name, fl, pc, hook, expect in (("parrot_tx1", 0x030DE2E0, 0x2E6C0, "krk_vf_tx_hook", "ffe31c46"),
                                       ("parrot_tx2", 0x030DE440, 0x2E820, "krk_vf_tx_hook", "ffe36c45"),
                                       ("parrot_tx3", 0x030DE60A, 0x2E9EA, "krk_vf_tx_hook", "ffe38744"),
                                       ("parrot_tx4", 0x030DE710, 0x2EAF0, "krk_vf_tx_hook", "ffe30444"),
                                       ("parrot_rx", 0x030DE984, 0x2ED64, "krk_vf_rx_hook", "ffe3b442")):
        assert fl == pc + IRAM_TO_FLASH
        sb = stock[fl - FLASH:fl - FLASH + 4]
        kind, half = decode_branch(img[fl - FLASH:fl - FLASH + 4])
        got = pc + half * 2                       # the CPU runs this at the IRAM address: decode from the IRAM pc
        naive = fl + half * 2
        m.report(sb.hex() == expect, f"{name}: stock bytes at {fl:#x} = {sb.hex()} (want {expect})")
        m.report(kind == "bsr" and got == top[hook], f"{name}: bsr from IRAM pc {pc:#x} lands on {hook} {got:#x} (want {top[hook]:#x}); flash-pc decode would give {naive:#x}")
        # stock bsr target it displaces: 0x172f8 (write) / 0x172cc (read) decoded from the stock bytes at the IRAM pc
        sk, sh = decode_branch(sb)
        want_t = 0x172CC if hook == "krk_vf_rx_hook" else 0x172F8
        m.report(sk == "bsr" and pc + sh * 2 == want_t, f"{name}: displaced stock call = bsr {pc + sh * 2:#x} (want {want_t:#x})")
    print("== F9 parrot: call-site context (stock argument setup r0=0, r1=buffer, r2=27)")
    mv0, mv27 = b"\x00\x30", b"\x1b\x32"
    for fl, pc in TX_SITES + TX_DEAD:
        ctx = stock[fl - FLASH - 6:fl - FLASH]
        m.report(mv0 in [ctx[i:i + 2] for i in (0, 2, 4)] and mv27 in [ctx[i:i + 2] for i in (0, 2, 4)],
                 f"site {pc:#x}: movi r0,0 and movi r2,27 in the three halfwords before the bsr ({ctx.hex()})")
    ctx = stock[RX_SITE[0] - FLASH - 6:RX_SITE[0] - FLASH]
    m.report(ctx[:2] == b"\x2f\x6c" and ctx[4:] == mv27, f"RX site 0x2ed64: mov r0,r11 / lrw r1 / movi r2,27 ({ctx.hex()})")
    for pool_iram, want in ((0x2EB2C, TX_BUF), (0x2EED8, RX_BUF), (0x2E864, TX_BUF)):
        v = struct.unpack("<I", stock[pool_iram + IRAM_TO_FLASH - FLASH:pool_iram + IRAM_TO_FLASH - FLASH + 4])[0]
        m.report(v == want, f"IRAM literal pool {pool_iram:#x} = {v:#x} (want {want:#x})")
    print("== F9 parrot: the boot copy covers the splices, nothing else in the IRAM image changed")
    pool = [rd32(0x0300C0D0 + 4 * i) for i in range(5)]
    dst, end, src = pool[0], pool[1], pool[2]
    m.report(dst == 0x10000 and src == 0x030BFC20 and end >= 0x2F098, f"boot stub pool: IRAM {dst:#x}.. <- flash {src:#x}, copy end {end:#x}")
    m.report(all(dst <= pc < end and src + (pc - dst) == fl for fl, pc in TX_SITES + TX_DEAD + [RX_SITE]),
             "every splice pc lies inside the copied range and flash = IRAM + 0x030afc20")
    lo, hi = src - FLASH, src - FLASH + (end - dst)
    diff = [i for i in range(lo, hi) if img[i] != stock[i]]
    sites = {fl - FLASH + k for fl, _ in TX_SITES + TX_DEAD + [RX_SITE] for k in range(4)}
    m.report(set(diff) <= sites and len(diff) >= 15, f"IRAM image differs from stock only at the five 4-byte sites ({len(diff)} bytes)")
    m.report(img[0xC6000:0xC7000] == stock[0xC6000:0xC7000], "forbidden sector 0x030c6000 untouched")
    print("== F9 parrot: blob2 hook table and symbols")
    tbl = HOOK_TBL
    m.report(top["krk_ext_hooks"] == tbl and rd32(tbl) == top["krk_parrot_rx_start"] and rd32(tbl + 4) == top["krk_parrot_rx_end"]
             and rd32(tbl + 8) == top["krk_scramble_chan_change"] and rd32(tbl + 12) == top["krk_mon_yield"], f"krk_ext_hooks at {top['krk_ext_hooks']:#x} (want {tbl:#x}): [rx_start, rx_end, scramble_chan_change, krk_mon_yield (F14)]")
    # 2026-11-03: the five Parrot rows left the KRAKEN list (slots 9..13) for stock Channel Set,
    # where they are items 26..30 with the user-facing labels Parrot On/Off, Replay Audio, Check,
    # Clear Audio, Status (docs/chanset-submenu.md). The Channel Set table follows the KRAKEN,
    # APRS and Radio Settings tables, so item 22 + k is slot EXT_MAX + APRS_MAX + RS_MAX + k.
    cs0 = EXT_TABLE + 4 * (EXT_MAX + krk_define("KRK_APRS_MAX") + krk_define("KRK_RS_MAX"))
    want = [("krk_parrot_arm_screen", b"Parrot On/Off"), ("krk_parrot_play_screen", b"Replay Audio"),
            ("krk_parrot_check_screen", b"Check"), ("krk_parrot_clear_screen", b"Clear Audio"),
            ("krk_parrot_status_screen", b"Status")]
    got = []
    for k in range(5):
        p_ = rd32(cs0 + 4 * (4 + k))          # Channel Set items 26..30 = our slots 4..8
        got.append((p_, img[rd32(p_) - FLASH:].split(b"\0")[0] if EXT_TABLE <= p_ < claim("kraken_ui3_code")[0] else None))
    m.report(got == [(top[sym], lb) for sym, lb in want],
             f"the five Parrot rows are stock Channel Set items 26..30, not KRAKEN rows: "
             f"{[g[1] for g in got]}")
    m.report(all(rd32(EXT_TABLE + 4 * k) == 0 for k in range(9, 14)),
             "KRAKEN extension slots 9..13 are empty now that the Parrot rows moved")
    sz = {k: v[0][1] for k, v in s2.items()}
    m.report(top.get("big_buf") == BIG and sz["big_buf"] == 5616 <= BIG_END - BIG, f"big_buf at {top.get('big_buf'):#x}, {sz['big_buf']} B in the {BIG_END - BIG}-byte kraken_bigbuf claim")
    vf, pp = top["krk_vf"], feat(s2, "P")
    fslo, fshi = claim("kraken_feat_state")
    m.report(fslo <= vf and vf + sz["krk_vf"] <= fshi and sz["krk_vf"] == VF["tap"] + 108, f"krk_vf {vf:#x} + {sz['krk_vf']} B inside kraken_feat_state (layout assumed by the tests)")
    m.report(sz["krk_vf"] == 180 and [x for x in s2["P"] if x[0] == pp][0][1] == 116, f"P is {[x for x in s2['P'] if x[0] == pp][0][1]} B (test layout assumes 116)")
    print(f"     .bss.feat used by F9: {sz['krk_vf'] + 116 + 1} B of {fshi - fslo}")
    cl = [x for x in s1.get("krk_hook_lh_start", [])]
    m.report(bool(cl), "main blob krk_hook_lh_start present (calls krk_ext_hook(0))")


class Rig:
    """a halted machine with helpers to run target code and answer intercepted stock calls"""

    def __init__(self, c, s1, s2, window):
        self.c, self.s1, self.s2, self.window = c, s1, s2, window
        self.flag_posts = []
        self.rx_active = 0
        self.rx_seq = []                    # one-shot answers of stock_rx_active, consumed first (race tests)
        self.dual = 0
        self.inhibit = 0
        self.vf = [a for a, _ in s2["krk_vf"]][0]
        self.P = feat(s2, "P")
        self.fn = lambda n: [a for a, _ in s2[n]][0]
        self.tick_fn = self.fn("parrot_tick")          # the Parrot Status screen's tick (it steps the state machine)
        self.fake = {}                                  # extra intercepts: stock/main-blob entry -> callable(regs) -> r0

    def arm(self):
        """OK on the 'Parrot' option level, On: the setter (what the old OK key did on the screen)"""
        return self.call(self.fn("par_set"), 1)

    # memory (tests/target.py)
    def w8(self, a, v):
        tg.w8(self.c, a, v)

    def w32(self, a, v):
        tg.w32(self.c, a, v)

    def r8(self, a):
        return tg.r8(self.c, a)

    def r16(self, a):
        return tg.r16(self.c, a)

    def r32(self, a):
        return tg.r32(self.c, a)

    def vfr(self, k):
        return self.r8(self.vf + VF[k]) if k in ("flags", "done", "tap_n", "quiet") else self.r32(self.vf + VF[k])

    def pr(self, k):
        a = self.P + PP[k]
        return self.r16(a) if k in ("n", "n_tx") else (self.r32(a) if k in ("magic", "stamp", "src", "dst", "buf", "echoes", "t_state", "deadline", "t_rel", "t_press", "t_clear") else self.r8(a))

    def set_ticks(self, t):
        self.w32(OS_TICKS, t)

    def run(self, stops, start=None, regs=None):
        """run from `start` (or the current pc) until one of `stops`; answer stock calls on the way"""
        c = self.c
        if start is not None:
            m.setreg(c, 0x48, start)
        for k, v in (regs or {}).items():
            m.setreg(c, k, v)
        icpt = [OSFLAG_POST, STOCK_RX_ACTIVE, STOCK_INHIBIT, STOCK_DUAL, STOCK_LH_READ, STOCK_LH_STOP] + list(self.fake)
        while True:
            pc = m.run_to(c, list(stops) + icpt)
            if pc in stops:
                return pc
            r = m.regs(c)
            ret = 0
            if pc == OSFLAG_POST:
                self.flag_posts.append((r["r0"], r["r1"]))
            elif pc == STOCK_RX_ACTIVE:
                ret = self.rx_seq.pop(0) if self.rx_seq else self.rx_active
            elif pc == STOCK_DUAL:
                ret = self.dual
            elif pc == STOCK_INHIBIT:
                ret = self.inhibit
            elif pc in self.fake:
                ret = self.fake[pc](r) or 0
            m.setreg(c, 0, ret)
            m.setreg(c, 0x48, r["r15"] & ~1)

    def call(self, fn, *args):
        tg.setup_call(self.c, fn, args)
        self.run([m.SENTINEL])
        r = m.regs(self.c)
        return tg.abi_ok(r), r

    def presses(self):
        return [f for g, f in self.flag_posts if g == PTT_GROUP and f == 0x08]

    def releases(self):
        return [f for g, f in self.flag_posts if g == PTT_GROUP and f == 0x10]

    def site(self, flash_pc_iram, rx=False, r11=6611):
        """run the real call site: 6 bytes before the bsr up to the instruction after it"""
        pc_bsr = flash_pc_iram
        tg.setup_call(self.c, pc_bsr - 6)
        regs = {5: 0x48160, 6: 0x48160, 11: r11}
        if rx:
            regs = {8: 0x456C0, 11: 0}
        self.run([pc_bsr + 4], start=pc_bsr - 6, regs=regs)
        r = m.regs(self.c)
        return tg.abi_ok(r, expect=regs)

    def tx_burst(self, pattern, site=0):
        """one TX burst through the real site; returns (buffer after, window after, abi)"""
        self.flag_posts.clear()
        m.wmem(self.c, TX_BUF, pattern)
        if self.window:
            m.wmem(self.c, WINDOW, b"\xEE" * 27)
        abi = self.site(TX_SITES[site][1] if site >= 0 else TX_SITES[0][1])
        buf = read_mem(self.c, TX_BUF, 27)
        win = read_mem(self.c, WINDOW, 27) if self.window else None
        return buf, win, abi


def run_checks(c, s1, s2, window):
    t = Rig(c, s1, s2, window)
    mic = bytes(range(0xA0, 0xA0 + 27))
    chk = lambda cond, msg: m.report(cond, msg)

    print(f"== F9 voice-frame layer: TX hook at the real call sites (window check {'on' if window else 'off'})")
    t.w32(t.vf, 0xA5A5A5A5)                         # power-up noise: no magic
    for k in range(4, 180, 4):
        t.w32(t.vf + k, 0xA5A5A5A5)
    for k in range(0, 116, 4):
        t.w32(t.P + k, 0xA5A5A5A5)
    for si, (fl, pc) in enumerate(TX_SITES):
        buf, win, abi = t.tx_burst(mic, si)
        chk(abi and buf == mic and (win is None or win == mic), f"site {pc:#x}, no magic (noise SRAM): pass-through, stock write {'reached the window' if window else 'ran'}, r4-r11/sp kept")
    chk(t.vfr("tx_calls") == 0xA5A5A5A5, "no magic: the hook touches no state at all")
    ok, _ = t.call(t.fn("krk_vf_service"))
    chk(ok and t.vfr("magic") == 0x56463031 and t.vfr("flags") == 0 and t.vfr("tx_calls") == 0 and t.vfr("end_idx") == 0xFFFFFFFF, "krk_vf_service initialises the layer from noise")
    for si, (fl, pc) in enumerate(TX_SITES):
        n0 = t.vfr("tx_calls")
        buf, win, abi = t.tx_burst(mic, si)
        chk(abi and buf == mic and (win is None or win == mic) and t.vfr("tx_calls") == n0 + 1 and not t.flag_posts,
            f"site {pc:#x}, idle layer: pass-through, tx_calls {n0}->{t.vfr('tx_calls')}, no PTT post")

    print("== F9 parrot: arm through the screen tick, H1/H2 through the main blob's Last Heard wrappers, RX hook at its real site")
    dwc.chan_poke(None, 0x10, w8=t.w8)                # digital, side A selected and live (krk_core.c dw tag; earlier tests leave other values)
    t.w8(CIPHER, 0)
    t.w8(BAND3, 0)
    t.w8(TXUP, 0)
    t.w32(OWN_ID, 111)
    t.set_ticks(100000)
    ok, _ = t.call(t.fn("krk_vf_service"))
    ok, _ = t.call(t.tick_fn, 0)                    # pinit
    chk(ok and t.pr("magic") == 0x50523031 and t.pr("armed") == 0 and t.pr("state") == PS["OFF"], "noise state -> parrot initialised DISARMED")
    t.set_ticks(100010)
    t.arm()
    chk(t.pr("armed") == 1 and t.pr("buf") == BIG and t.pr("state") == PS["OFF"], f"Parrot > On (par_set): armed, bigbuf claimed ({t.pr('buf'):#x}); the state machine is not stepped yet")
    t.set_ticks(100012)
    t.call(t.tick_fn, 0)
    chk(t.pr("state") == PS["IDLE"], "the Parrot Status tick steps it: IDLE")
    chk(not t.flag_posts, "arming posts nothing")

    def call_start(src, dst, typ=1):
        t.w32(RX_SRC, src)
        t.w32(RX_DST, dst)
        t.w8(RX_TYPE, typ)
        t.w8(0x3BD2D, 0)
        t.call(s1["krk_hook_lh_start"][0][0])

    call_start(3125001, 91)
    chk(t.pr("rec") == 1 and t.pr("src") == 3125001 and t.pr("dst") == 91 and t.pr("kind") == 1 and t.vfr("tx_calls") is not None and t.r32(t.vf + VF["rx_sink"]) == s2["rx_sink"][0][0],
        "H1 (krk_hook_lh_start -> hook table -> krk_parrot_rx_start): capture started, TG 91 from 3125001, RX sink installed")
    frames = [bytes((i * 7 + j) & 0xFF for j in range(27)) for i in range(40)]
    for i, f in enumerate(frames):
        if window:
            m.wmem(c, WINDOW, f)
        m.wmem(c, RX_BUF, b"\x00" * 27)
        okx = t.site(RX_SITE[1], rx=True)
        if not okx:
            break
    chk(okx, "RX site run: r4-r11/sp preserved by the hook")
    got_n = t.pr("n")
    chk(got_n == 40 and t.vfr("rx_sunk") == 40 and t.vfr("rx_calls") == 40, f"40 bursts through the RX hook: captured {got_n}, rx_calls {t.vfr('rx_calls')}, rx_sunk {t.vfr('rx_sunk')}")
    if window:
        chk(read_mem(c, BIG, 27 * 40) == b"".join(frames), "captured bytes equal the window bytes, in order")
        chk(read_mem(c, RX_BUF, 27) == frames[-1], "stock read still fills 0x46b38 (decoder input unchanged)")
    call_start(3125001, 91)                         # repeated header of the same call
    chk(t.pr("n") == 40, "repeated header of the same call does not restart the capture")
    t.call(s1["krk_hook_lh_end"][0][0])
    chk(t.pr("rec") == 0 and t.r32(t.vf + VF["rx_sink"]) == 0 and t.pr("ready") == 1, "H2 (krk_hook_lh_end): capture stopped, sink removed, call marked ready")
    m.wmem(c, RX_BUF, b"\x00" * 27)
    n0 = t.vfr("rx_sunk")
    t.site(RX_SITE[1], rx=True)
    chk(t.pr("n") == 40 and t.vfr("rx_sunk") == n0, "after the call end the RX hook captures nothing")
    # cap (P.n is poked near the cap: running 208 bursts one by one over the gdb stub is slow)
    call_start(3125002, 91)
    m.wmem(c, t.P + PP["n"], struct.pack("<H", 205))
    for i in range(6):
        t.site(RX_SITE[1], rx=True)
    chk(t.pr("n") == 208, f"capture caps at 208 bursts / 12.48 s (got {t.pr('n')}); the rest is dropped")
    t.call(s1["krk_hook_lh_end"][0][0])
    call_start(111, 91)                             # our own ID: never captured
    chk(t.pr("rec") == 0, "a call from our own DMR ID is not captured")
    call_start(3125001, 91)
    for i in range(40):
        if window:
            m.wmem(c, WINDOW, frames[i])
        t.site(RX_SITE[1], rx=True)
    t.call(s1["krk_hook_lh_end"][0][0])
    chk(t.pr("n") == 40 and t.pr("ready") == 1, "fresh 40-burst call captured (2.4 s), ready")

    print("== F9 parrot: hold, gates, key, substitute, release")
    now = 100100
    t.flag_posts.clear()

    def tick(scan=0, dt=20):
        nonlocal now
        now += dt
        t.set_ticks(now)
        return t.call(t.tick_fn, scan)

    for _ in range(7):
        tick()
    chk(t.pr("state") == PS["HOLD"] and not t.flag_posts, f"state HOLD, nothing posted during the 1.5 s hold (state {t.pr('state')})")

    def force_idle():
        """put the machine in IDLE (pokes the state byte) instead of waiting out the 3 s hold-off"""
        t.w8(t.P + PP["state"], PS["IDLE"])
        t.w8(TXUP, 0)
        t.flag_posts.clear()

    # gates, one at a time: each blocks the key at the end of the hold
    cases = [("analog channel", lambda: t.w8(VFO8, 0x00), lambda: t.w8(VFO8, 0x10), 1),
             ("other side loaded (dual watch)", lambda: dwc.set_tag(t.w8, 1), lambda: dwc.set_tag(t.w8, 0), 1),
             ("stock cipher configured (0x33824)", lambda: t.w8(CIPHER, 2), lambda: t.w8(CIPHER, 0), 2),
             ("stock TX-forbidden (0x3e31b bit 5)", lambda: t.w8(BAND3, 0x20), lambda: t.w8(BAND3, 0), 3),
             ("channel busy (stock_rx_active)", lambda: setattr(t, "rx_active", 1), lambda: setattr(t, "rx_active", 0), 4),
             ("TX voice already up (0x3bd50)", lambda: t.w8(TXUP, 1), lambda: t.w8(TXUP, 0), 4),
             ("RX voice up (0x3bd50 bit 1)", lambda: t.w8(TXUP, 2), lambda: t.w8(TXUP, 0), 4),
             ("tx_is_inhibited", lambda: setattr(t, "inhibit", 1), lambda: setattr(t, "inhibit", 0), 5)]
    for name, on, off, why in cases:
        force_idle()
        t.w8(t.P + PP["ready"], 1)          # a call just ended
        on()
        for _ in range(10):                 # 200 ticks: the 150-tick hold expires inside
            tick()
        chk(not t.presses() and t.pr("reason") == why, f"gate '{name}': no PTT press, reason {t.pr('reason')} (want {why})")
        off()
    # play last without anything wrong, but disarmed first -> nothing
    print("== F9 parrot: a good echo (gates open)")
    force_idle()
    t.w8(t.P + PP["ready"], 1)
    for _ in range(15):
        if t.presses():
            break
        tick()
    chk(len(t.presses()) == 1 and t.pr("state") == PS["KEYING"], f"1.5 s after the call: exactly one PTT press (0x08 on group 0x3a4f0), state KEYING")
    chk(t.vfr("flags") & 1 and t.r32(t.vf + VF["tx_fn"]) == s2["tx_src"][0][0], "substitution armed before the press (flags SUBST, provider tx_src)")
    t.w8(TXUP, 1)                                   # TX voice comes up
    tick()
    chk(t.pr("state") == PS["SEND"], "TX voice up -> SEND")
    lead = t.pr("lead")
    total = lead + 40
    sent = []
    last_posts = 0
    for i in range(total + 3):
        site = i % 2
        buf, win, abi = t.tx_burst(mic, site)
        sent.append((buf, win, abi, len(t.releases())))
        if i + 1 < total:
            pass
    stored = frames if window else [b"\0" * 27] * 40     # without the retaining window the stock read yields zeros
    exp = [SILENCE] * lead + stored + [SILENCE] * 3
    chk(all(s[2] for s in sent), "ABI (r4-r11, sp) preserved on every substituted burst, both live sites")
    chk(all(sent[i][0] == exp[i] for i in range(total + 3)), f"buffer 0x49b18 holds: {lead} lead silence bursts, the 40 stored bursts in order, then silence")
    if window:
        chk(all(sent[i][1] == exp[i] for i in range(total + 3)), "the baseband window receives exactly those bytes (stock write after the substitution)")
    chk(sent[total - 2][3] == 0 and sent[total - 1][3] == 1, "PTT release (0x10) is posted by the hook on the LAST stored burst, not before")
    chk(sent[total][3] == 1 and t.vfr("tx_silence") >= 1, "bursts after the last one are silence")
    chk(t.vfr("tx_subst") == total - 0 - 0 or t.vfr("tx_subst") >= 40, f"tx_subst counted {t.vfr('tx_subst')}")
    tick()
    chk(t.pr("state") == PS["UNKEY"], "screen tick sees the layer reached the end -> UNKEY")
    t.w8(TXUP, 0)
    tick()
    chk(t.pr("state") == PS["COOL"] and t.pr("watch") == 1 and t.vfr("flags") & 1, "TX down -> COOL, but the press watch (and the substitution) stays on for 1.5 s")
    buf, win, abi = t.tx_burst(mic, 0)
    chk(buf == SILENCE, "...so a burst right after the echo is still silence, never the microphone")
    for _ in range(9):
        tick()
    chk(t.pr("watch") == 0 and t.vfr("flags") == 0, "TX stayed down 1.5 s -> watch over, substitution ended")
    buf, win, abi = t.tx_burst(mic, 0)
    chk(buf == mic, "after the echo the layer passes the microphone bursts through again")

    print("== F9 parrot: abort, deadline watchdog, stale session, disarm")
    def start_echo():
        force_idle()
        t.w8(t.P + PP["ready"], 1)
        for _ in range(15):
            if t.presses():
                break
            tick()
        t.w8(TXUP, 1)
        tick()
        return t.pr("state")
    chk(start_echo() == PS["SEND"], "second echo reaches SEND")
    for i in range(5):
        t.tx_burst(mic, i % 2)
    t.flag_posts.clear()
    tick(SCAN_1 + 4)                                # any key
    chk(t.pr("state") == PS["UNKEY"] and len(t.releases()) >= 1 and t.vfr("flags") & 1 and t.vfr("quiet") == 1, "a key press mid-transmission: release posted, substitution goes SILENT (review fix 7)")
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == SILENCE, "after the abort the remaining bursts are silence, not the microphone, while TX voice is up")
    t.w8(TXUP, 0)
    for _ in range(10):
        tick()
    chk(t.pr("watch") == 0 and t.vfr("flags") == 0 and t.tx_burst(mic, 0)[0] == mic, "TX down for 1.5 s: watch over, microphone path restored")
    chk(start_echo() == PS["SEND"], "third echo reaches SEND")
    dl = t.pr("deadline")
    t.flag_posts.clear()
    t.set_ticks(dl - 1)
    ok, _ = t.call(s2["krk_parrot_tick"][0][0])
    chk(ok and not t.releases(), "maintenance-tick watchdog is quiet before the deadline")
    t.set_ticks(dl + 1)
    t.call(s2["krk_parrot_tick"][0][0])
    chk(len(t.releases()) == 1, "watchdog (krk_hook_maint_tick path) posts the release at the deadline, with the screen not running")
    t.set_ticks(dl + 20)
    t.call(s2["krk_parrot_tick"][0][0])
    chk(len(t.releases()) == 1, "...but not again within 500 ms")
    t.set_ticks(dl + 60)
    t.call(s2["krk_parrot_tick"][0][0])
    chk(len(t.releases()) == 2, "...and again after 500 ms")
    t.set_ticks(dl + 120)
    t.call(s2["krk_parrot_tick"][0][0])
    chk(t.vfr("quiet") == 1 and t.tx_burst(mic, 0)[0] == SILENCE, "past deadline + 1 s the watchdog makes the substitution silent (no microphone)")
    t.w8(TXUP, 0)
    for k in range(1, 9):                           # the frozen SEND state: release during the quiet period, then the watch ends
        t.set_ticks(dl + 120 + 60 * k)
        t.call(s2["krk_parrot_tick"][0][0])
    chk(t.pr("watch") == 0 and t.vfr("flags") == 0, "TX down for 1.5 s (maintenance tick alone, state SEND frozen): watch over, substitution ended")
    now = dl + 400
    t.set_ticks(now)
    # stale session after a (simulated) warm reset: tick counter restarts
    t.call(t.fn("krk_vf_tx_begin"), s2["tx_src"][0][0], 50, 600, 0)
    chk(t.vfr("flags") == 1, "session begun")
    t.set_ticks(5)                                  # counter below t_begin: as after a reset
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == mic and t.vfr("flags") == 0, "stale session (age wraps negative): the hook drops it and passes the microphone through")
    t.w8(TXUP, 0)
    t.set_ticks(now)
    t.call(t.fn("krk_vf_tx_begin"), s2["tx_src"][0][0], 50, 600, 0)
    t.set_ticks(now + 700)
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == mic and t.vfr("flags") == 0, "session older than max_ticks (TX voice down) is dropped by the hook itself")
    t.set_ticks(now)
    t.call(t.fn("krk_vf_tx_begin"), s2["tx_src"][0][0], 50, 600, 0)
    t.w8(TXUP, 1)
    t.set_ticks(now + 700)
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == SILENCE and t.vfr("flags") & 1 and t.vfr("quiet") == 1, "session older than max_ticks with TX voice still up: silence, not the microphone")
    t.w8(TXUP, 0)
    t.call(t.fn("krk_vf_tx_end"))
    t.set_ticks(now)
    t.call(t.fn("krk_vf_service"))
    t.set_ticks(3)                                  # reboot: ticks restart below the stamp
    t.call(t.fn("krk_vf_service"))
    chk(t.vfr("magic") == 0x56463031 and t.vfr("flags") == 0 and t.r32(t.vf + VF["rx_sink"]) == 0, "tick counter below the stamp (warm reset) re-initialises the layer: no sink, no session")
    t.set_ticks(5)
    t.call(t.tick_fn, 0)
    chk(t.pr("armed") == 0 and t.pr("state") == PS["OFF"], "...and the parrot comes back DISARMED")
    # review fix 9: reboot with a stale stamp BELOW the new uptime (previous run died early): detected by the stale-stamp rule
    t.arm()
    t.set_ticks(7)
    t.call(t.tick_fn, 0)
    chk(t.pr("armed") == 1, "(control) armed again, state kept across ticks 2 s apart")
    t.set_ticks(7 + 3001)
    t.call(t.tick_fn, 0)
    chk(t.pr("armed") == 0 and t.pr("buf") == 0, "stale magic without a recent tick (30 s gap, counter above the stamp): treated as a reboot, disarmed")
    t.call(t.fn("krk_vf_service"))
    t.call(t.fn("krk_vf_big_release"), 1)
    # provider exhaustion -> silence + release (layer level, tiny max_bursts)
    t.set_ticks(1000)
    t.call(t.fn("krk_vf_service"))
    t.w32(t.P + PP["buf"], BIG)
    t.w8(t.P + PP["synth"], 0)
    m.wmem(c, t.P + PP["n_tx"], struct.pack("<H", 4))
    t.w8(t.P + PP["lead"], 0)
    t.w8(t.P + PP["pass_"], 0)
    t.call(t.fn("krk_vf_tx_begin"), s2["tx_src"][0][0], 3, 600, 0)   # cap: 3 bursts although the provider has 4
    rel0 = t.vfr("rel_posts")
    got = [t.tx_burst(mic, 0)[0] for _ in range(6)]
    chk(got[3] == SILENCE and got[4] == SILENCE and got[2] != SILENCE and t.vfr("rel_posts") > rel0, "bursts beyond max_bursts are silence and the release is posted (layer hard cap)")
    t.call(t.fn("krk_vf_tx_end"))

    print("== F9 parrot: review fixes (press crossing the release, gate re-check, dual watch)")
    t.w8(TXUP, 0)
    t.w32(t.P + PP["magic"], 0x50523031)
    t.set_ticks(20000)
    t.call(t.fn("krk_vf_service"))
    t.call(t.tick_fn, 0)
    t.arm()
    now = 20000
    ok = t.pr("armed") == 1
    chk(ok, "armed for the race tests")
    # a fresh capture: poke n/ready (the capture path itself is covered above)
    t.w32(t.P + PP["buf"], BIG)
    m.wmem(c, t.P + PP["n"], struct.pack("<H", 40))
    t.w8(t.P + PP["ready"], 1)
    t.w8(t.P + PP["side"], 0)
    t.w8(t.P + PP["state"], PS["IDLE"])
    t.flag_posts.clear()
    for _ in range(15):
        if t.presses():
            break
        tick()
    chk(len(t.presses()) == 1 and t.pr("state") == PS["KEYING"] and t.pr("watch") == 1, "press posted: the watch starts at the press")
    tick(SCAN_1 + 4)                                # key one step after the press: release crosses the press
    chk(t.pr("state") == PS["UNKEY"] and len(t.releases()) >= 1 and t.vfr("flags") & 1 and t.vfr("quiet") == 1, "abort one step after the press: release posted, session kept SILENT")
    tick()
    tick()
    chk(t.pr("state") == PS["COOL"] and t.pr("watch") == 1, "TX not up: the state machine says COOL but the watch stays on")
    t.w8(TXUP, 1)                                   # the stock handler consumed the press AFTER the release: TX comes up late
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == SILENCE, "late TX voice: the first burst is silence (live microphone never reaches the air)")
    t.flag_posts.clear()
    now += 100
    t.set_ticks(now)
    t.call(s2["krk_parrot_tick"][0][0])             # maintenance tick, state is COOL
    chk(len(t.releases()) >= 1 and t.pr("state") == PS["COOL"], "maintenance watchdog (state COOL, not keyed) re-posts the release because TX voice is up")
    now += 400
    t.set_ticks(now)
    n_before = len(t.releases())
    t.call(s2["krk_parrot_tick"][0][0])
    chk(len(t.releases()) > n_before, "...and keeps posting it every 500 ms while TX voice stays up")
    t.w8(TXUP, 0)
    buf, _, _ = t.tx_burst(mic, 0)
    chk(buf == SILENCE, "TX down but inside the quiet period: still silence")
    for k in range(1, 5):
        t.set_ticks(now + 60 * k)
        t.call(s2["krk_parrot_tick"][0][0])
    chk(t.pr("watch") == 0 and t.vfr("flags") == 0 and t.tx_burst(mic, 0)[0] == mic, "TX down 1.5 s after the last release: watch over, microphone path restored")

    # F12 review fix 6: the TX hook applies the scrambler key before the first burst of a transmission
    scr = feat(s2, "scr_st")
    dwc.chan_poke(None, 0x10, w8=t.w8)
    t.w8(CIPHER, 0)
    t.w32(scr, 0x53435231)
    m.wmem(c, scr + 4, struct.pack("<H", 0x4242) + bytes([1, 0]))
    t.w32(scr + 8, 0)
    t.w32(scr + 12, 0)
    t.w32(scr + 16, 0)
    t.w32(0x1100038C, 0)
    t.set_ticks(now + 100)
    buf, _, abi = t.tx_burst(mic, 0)
    chk(abi and t.r32(0x1100038C) == 0x42424242, f"krk_vf_tx_hook at the real site applies SCRAMBLE_REG on the first burst ({t.r32(0x1100038C):#x})")
    t.w32(scr, 0xA5A5A5A5)
    t.w32(0x1100038C, 0)
    t.set_ticks(now)

    print("== F9 parrot: gate re-check right before the press (H1 race), dual watch")
    now += 500
    t.set_ticks(now)
    t.w8(t.P + PP["state"], PS["IDLE"])
    t.w8(t.P + PP["ready"], 1)
    t.flag_posts.clear()
    for _ in range(20):                             # up to the tick BEFORE the one that presses
        tick()
        if t.pr("state") == PS["HOLD"] and now + 20 - t.pr("t_state") >= 150:
            break
    t.rx_seq = [0, 1]                               # idle at the SM's look, busy at the re-check
    tick()
    t.rx_seq = []
    chk(not t.presses() and t.pr("state") == PS["IDLE"] and t.pr("reason") == 4 and t.pr("watch") == 0, "channel goes busy between decision and press: no press, reason busy")
    t.w8(t.P + PP["state"], PS["IDLE"])
    t.w8(t.P + PP["ready"], 1)
    t.w8(t.P + PP["side"], 1)                       # capture came from side B ...
    for _ in range(10):
        tick()
    chk(not t.presses() and t.pr("reason") == 14, f"capture from the other side than the loaded one: no press, reason dual/side ({t.pr('reason')})")
    t.w8(t.P + PP["side"], 0)
    t.dual = 1
    t.w8(t.P + PP["state"], PS["IDLE"])
    t.w8(t.P + PP["ready"], 1)
    for _ in range(10):
        tick()
    chk(not t.presses() and t.pr("reason") == 14, "dual watch on: no echo")
    call_start(3125001, 91)
    chk(t.pr("rec") == 0 or t.pr("n") == 40, "dual watch on: a call start captures nothing")
    t.dual = 0

    print("== F9 parrot rows (2026-11-02): Parrot Off/On option level, Parrot Status, Play / Check / Clear one-shots")
    DLY = 0x0300C4BC
    A_POPUP, A_KEYSCAN = s1["krk_popup"][0][0], s1["krk_key_scan"][0][0]
    cstr_ = lambda a: read_mem(c, a, 40).split(b"\0")[0] if a else None
    popups, keyq = [], []
    sim = {"on": False, "t_up": None, "total": 0, "arm_key": 0, "never_up": False, "ended": False}

    def f_popup(r):
        l1 = cstr_(r["r1"])
        popups.append((r["r0"], l1, cstr_(r["r2"]), r["r3"]))
        return sim["arm_key"] if l1 == b"OK again to TX" else 0

    def f_key(r):
        return keyq.pop(0) if keyq else 0

    def f_dly(r):
        """os_time_dly_ms: time passes (100 Hz ticks); with sim['on'] a radio answers: TX voice comes up after the press, the layer ends
        after the substituted bursts, TX goes down. sim['never_up'] leaves TX voice down (the 3 s KEYING timeout)."""
        now_ = t.r32(OS_TICKS) + max(r["r0"] // 10, 1)
        t.set_ticks(now_)
        if sim["on"] and not sim["never_up"]:
            up = t.r8(TXUP) & 1
            if up and (t.releases() or sim["ended"]):
                t.w8(TXUP, 0)                                   # the radio obeys a release / the layer's end
            elif t.presses() and sim["t_up"] is None and not up:
                t.w8(TXUP, 1)
                sim["t_up"] = now_
                sim["total"] = t.pr("pass_") + t.pr("lead") + t.pr("n_tx")
            elif sim["t_up"] is not None and not sim["ended"] and now_ - sim["t_up"] >= sim["total"] * 6:
                t.w8(t.vf + VF["done"], 1)                      # the layer reached its last burst
                sim["ended"] = True
        return 0
    t.fake.update({A_POPUP: f_popup, A_KEYSCAN: f_key, DLY: f_dly})

    def reset_tx():
        t.w8(TXUP, 0)
        t.w8(t.P + PP["state"], PS["IDLE"])
        t.w8(t.P + PP["ready"], 0)
        t.w8(t.P + PP["watch"], 0)
        t.w8(t.vf + VF["done"], 0)
        t.flag_posts.clear()
        popups.clear()
        keyq.clear()
        sim.update(on=False, t_up=None, total=0, arm_key=0, never_up=False, ended=False)
        t.rx_active = t.dual = t.inhibit = 0

    def have_capture(n=40):
        t.w32(t.P + PP["buf"], BIG)
        m.wmem(c, t.P + PP["n"], struct.pack("<H", n))
        t.w8(t.P + PP["side"], 0)
        t.w8(t.P + PP["kind"], 1)

    # ---- the Parrot option row: getter / setter over the arm state
    t.set_ticks(2000)
    t.call(t.fn("krk_vf_service"))
    t.call(t.tick_fn, 0)
    t.arm()
    have_capture()
    t.call(t.fn("par_set"), 0)
    chk(t.pr("armed") == 0 and t.pr("n") == 0 and t.pr("buf") == 0 and t.r32(t.vf + VF["rx_sink"]) == 0 and t.pr("state") == PS["OFF"],
        f"par_set(0) (Parrot > Off): disarmed, capture dropped, bigbuf released, sink removed (armed {t.pr('armed')} n {t.pr('n')} buf {t.pr('buf'):#x} sink {t.r32(t.vf + VF['rx_sink']):#x} state {t.pr('state')})")
    t.call(t.fn("par_get"))
    chk(m.regs(c)["r0"] == 0, "par_get after Off: 0")
    call_start(3125001, 91)
    chk(t.pr("rec") == 0, "disarmed: a call start captures nothing")
    t.arm()
    chk(t.pr("armed") == 1 and t.pr("buf") == BIG and t.pr("n") == 0, "par_set(1) (Parrot > On): armed, bigbuf claimed")
    t.call(t.fn("par_get"))
    chk(m.regs(c)["r0"] == 1, "par_get after On: 1")
    t.arm()
    chk(t.pr("armed") == 1, "par_set(1) again: still armed (idempotent)")
    t.call(t.fn("par_set"), 0)
    t.call(t.fn("krk_vf_big_claim"), 2)                         # Burst Voice holds the shared buffer
    t.arm()
    chk(t.pr("armed") == 0 and t.pr("reason") == 4, "On while Burst Voice owns the shared buffer: stays Off (reason channel busy)")
    t.call(t.fn("krk_vf_big_release"), 2)
    t.arm()
    chk(t.pr("armed") == 1, "On once the buffer is free: armed")
    # The arm switch is the row labelled "Parrot On/Off" since 2026-11-03, when the five Parrot rows
    # moved to stock Channel Set (docs/chanset-submenu.md). Its struct krk_opt still carries the
    # header text "Parrot", which is what the KRAKEN title hook would draw; in the Channel Set
    # placement the header is the static label from the relocated array instead.
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, t.fn("krk_parrot_arm_screen"), 28))
    o = struct.unpack("<IIBxxxII", read_mem(c, t.fn("par_opt"), 20))
    labs = struct.unpack("<2I", read_mem(c, o[1], 8))
    chk(cstr_(sc[0]) == b"Parrot On/Off" and sc[1] == 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == t.fn("par_opt") and sc[6] == 0 and cstr_(o[0]) == b"Parrot"
        and o[2] == 2 and o[3] == t.fn("par_get") and o[4] == t.fn("par_set") and [cstr_(x) for x in labs] == [b"Off", b"On"],
        f"Parrot On/Off row: option level, header 'Parrot', Off / On, getter par_get, setter par_set, no enter/tick (a setting, not a screen) {[cstr_(x) for x in labs]}")
    # The labels are the user-facing ones the Channel Set placement asked for (2026-11-03).
    for name, lbl_ in (("krk_parrot_play_screen", b"Replay Audio"), ("krk_parrot_check_screen", b"Check"), ("krk_parrot_clear_screen", b"Clear Audio")):
        sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, t.fn(name), 28))
        chk(cstr_(sc[0]) == lbl_ and sc[1] != 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == 0 and sc[6] == 1, f"{lbl_.decode()} row: one-shot (enter set, no tick, no exit, no option, KRK_SF_ONESHOT)")
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, t.fn("krk_parrot_status_screen"), 28))
    chk(cstr_(sc[0]) == b"Status" and sc[1] == t.fn("status_enter") and sc[2] == t.tick_fn and sc[3] == t.fn("status_exit") and sc[5] == 0 and sc[6] == 0,
        "Status row: a regular screen (enter, tick, exit), not an option, not a one-shot")

    # ---- Parrot Status: only Up/Down act; nothing in it arms, plays, checks or clears; leaving keeps the arm state
    reset_tx()
    have_capture()
    now = 3000
    t.set_ticks(now)
    t.w8(t.P + PP["ready"], 1)
    t.w8(t.P + PP["state"], PS["HOLD"])
    t.call(t.fn("status_enter"))
    chk(t.pr("ready") == 0 and t.pr("state") == PS["IDLE"], "Parrot Status opened: a stale call-ended mark and a pending HOLD are cancelled (no echo of an old call on opening)")
    lead0 = t.pr("lead")
    for what, scan in (("OK", 0x03), ("1", SCAN_1), ("2", SCAN_1 + 1), ("0", SCAN_0), ("#", 0x0E)):
        now += 20
        t.set_ticks(now)
        t.call(t.tick_fn, scan)
    chk(t.pr("armed") == 1 and t.pr("n") == 40 and not t.presses() and t.pr("lead") == lead0 and t.pr("chk_req") == 0 and t.pr("play_req") == 0,
        "Parrot Status: OK, 1, 2, 0, # do nothing (still armed, capture kept, nothing requested, nothing keyed)")
    now += 20
    t.set_ticks(now)
    t.call(t.tick_fn, 0x02)
    chk(t.pr("lead") == min(lead0 + 1, 8), f"Parrot Status: Up raises the lead ({lead0} -> {t.pr('lead')})")
    now += 20
    t.set_ticks(now)
    t.call(t.tick_fn, 0x05)
    chk(t.pr("lead") == lead0, "Parrot Status: Down lowers it")
    t.w8(t.P + PP["state"], PS["HOLD"])
    t.w8(t.P + PP["ready"], 1)
    ok, _ = t.call(t.fn("status_exit"))
    chk(ok and t.pr("armed") == 1 and t.pr("n") == 40 and t.pr("buf") == BIG and t.pr("state") == PS["IDLE"] and t.pr("ready") == 0 and not t.presses(),
        "Parrot Status exit: the arm state and the capture are kept (the Parrot row owns them); a pending echo is cancelled; nothing keyed")
    reset_tx()
    t.w8(t.P + PP["state"], PS["SEND"])
    t.w8(t.P + PP["watch"], 1)
    t.w8(TXUP, 1)
    ok, _ = t.call(t.fn("status_exit"))
    chk(ok and len(t.releases()) >= 1 and t.pr("state") == PS["UNKEY"] and t.pr("armed") == 1, "Parrot Status exit while transmitting: the echo is ended (PTT release posted, silence), the arm state is kept")
    t.w8(TXUP, 0)
    reset_tx()

    # ---- Parrot Clear: a one-shot without TX
    reset_tx()
    have_capture()
    t.call(t.fn("clear_run"))
    chk(popups == [(1, b"Cleared", None, 1200)] and t.pr("n") == 0 and t.pr("ready") == 0 and not t.flag_posts, f"Parrot Clear with a capture: 'Cleared' tick popup, capture dropped, no PTT activity {popups}")
    popups.clear()
    t.call(t.fn("clear_run"))
    chk(popups == [(0, b"no capture", None, 1200)], f"Parrot Clear again: 'no capture' info popup {popups}")
    popups.clear()
    have_capture()
    t.w8(t.P + PP["rec"], 1)
    t.call(t.fn("clear_run"))
    chk(popups and popups[0][0] == 2 and popups[0][1] == b"Recording now" and t.pr("n") == 40, f"Parrot Clear while a call is being captured: refused with an error popup, capture kept {popups}")
    t.w8(t.P + PP["rec"], 0)
    t.call(t.fn("par_set"), 0)
    popups.clear()
    t.call(t.fn("clear_run"))
    chk(popups == [(0, b"no capture", None, 1200)], "Parrot Clear with Parrot Off: 'no capture'")
    t.arm()

    # ---- Parrot Play / Parrot Check: two-press confirm by popup; the engine is the old one
    def go(fn_name, arm_key=0, keys=(), n=40, simulate=False, never_up=False, **attrs):
        reset_tx()
        have_capture(n)
        sim["arm_key"] = arm_key
        sim["on"] = simulate
        sim["never_up"] = never_up
        keyq[:] = list(keys)
        for k_, v_ in attrs.items():
            setattr(t, k_, v_)
        ok, _ = t.call(t.fn(fn_name))
        return ok
    ARM = (3, b"OK again to TX", b"TRANSMITS", 3000)
    dwc.chan_poke(None, 0x10, w8=t.w8)
    t.set_ticks(50000)
    OKOK = dict(arm_key=0x03, keys=(0x03, 0x03, 0), simulate=True)
    for fn_name, what in (("play_run", "Parrot Play"), ("check_run", "Parrot Check")):
        t.call(t.fn("par_set"), 0)
        reset_tx()
        t.call(t.fn(fn_name))
        chk(popups == [(2, b"Parrot is off", b"Set it On first", 1800)] and not t.flag_posts, f"{what} with Parrot Off: error popup, no arm popup, nothing keyed {popups}")
        t.arm()
        if fn_name == "play_run":
            ok = go(fn_name, n=3)
            chk(ok and popups == [(2, b"no capture", None, 1800)] and not t.flag_posts, f"{what} with no capture: 'no capture' error popup, no arm popup, nothing keyed {popups}")
        ok = go(fn_name)
        chk(ok and popups == [ARM] and not t.presses() and not t.flag_posts, f"{what}: the arming popup times out (no key): nothing keyed, one popup {popups}")
        for k, name in ((0x06, "Back"), (0x02, "Up"), (0x05, "Down"), (0x0E, "#"), (0x09, "P1")):
            ok = go(fn_name, arm_key=k)
            chk(ok and popups == [ARM] and not t.presses() and not t.flag_posts, f"{what}: arm popup answered by {name}: cancelled, nothing keyed")
        # OK, OK: exactly one press and a completed echo
        nn = 40 if fn_name == "play_run" else 3
        ok = go(fn_name, n=nn, **OKOK)
        res = [p_[1] for p_ in popups]
        chk(ok and len(t.presses()) == 1 and res[:2] == [b"OK again to TX", b"Playing..." if fn_name == "play_run" else b"Checking..."] and popups[1][3] == 0 and popups[-1][0] == 1 and popups[-1][3] == 1800 and len(popups) == 3,
            f"{what}: OK, OK -> exactly one PTT press, 'Playing/Checking...' box, result tick popup {popups[-1]}")
        chk(t.pr("watch") == 0 and t.vfr("flags") == 0 and t.pr("state") == PS["COOL"] and t.pr("reason") == 13,
            f"{what}: the run ended through the state machine (COOL, reason done), press watch over, substitution ended")
        if fn_name == "play_run":
            chk(popups[-1][1] == b"Played" and popups[-1][2] == b"40 bursts", f"Parrot Play result: {popups[-1][1:3]}")
        else:
            chk(popups[-1][1] == b"Check done" and popups[-1][2].startswith(b"up") and b" rel" in popups[-1][2], f"Parrot Check result: {popups[-1][1:3]}")
            chk(t.pr("chk_done") == 1, "Parrot Check: the verdict flag for the Status screen is set")
        # refusals come from the unchanged gates, at the moment of keying: no press, error popup with the existing reason text
        for why, kw, text in (("channel busy", dict(rx_active=1), b"channel busy"), ("TX inhibited", dict(inhibit=1), b"TX inhibited"), ("dual watch", dict(dual=1), b"dual/side chg")):
            ok = go(fn_name, n=nn, **OKOK, **kw)
            chk(ok and not t.presses() and len(popups) == 3 and popups[-1][:2] == (2, text) and popups[-1][3] == 1800, f"{what}: gate '{why}': no press, error popup {popups[-1][1]!r}")
        t.w8(CIPHER, 2)
        ok = go(fn_name, n=nn, **OKOK)
        chk(ok and not t.presses() and popups[-1][:2] == (2, b"encrypted"), f"{what}: stock cipher configured: no press, 'encrypted'")
        t.w8(CIPHER, 0)
        dwc.chan_poke(None, 0x00, w8=t.w8)
        ok = go(fn_name, n=nn, **OKOK)
        chk(ok and not t.presses() and popups[-1][:2] == (2, b"not digital"), f"{what}: analog channel: no press, 'not digital'")
        dwc.chan_poke(None, 0x10, w8=t.w8)
        # TX voice never comes up: the 3 s KEYING timeout releases and the result says so
        ok = go(fn_name, n=nn, never_up=True, **OKOK)
        chk(ok and len(t.presses()) == 1 and len(t.releases()) >= 1 and popups[-1][:2] == (2, b"TX never up"), f"{what}: TX voice never up: one press, release posted, 'TX never up'")
        # a key pressed during the run aborts it (the key still down from the confirming OK does not)
        ok = go(fn_name, n=nn, arm_key=0x03, keys=(0x03, 0x03, 0x03, 0, 0, 0, 0, 0, 0x05), simulate=True)
        chk(ok and len(t.presses()) == 1 and len(t.releases()) >= 1 and popups[-1][:2] == (2, b"aborted"), f"{what}: a key pressed after the confirming OK is released aborts: release posted, 'aborted' {popups[-1][:2]}")
        chk(t.pr("watch") == 0 and t.vfr("flags") == 0, f"{what}: after the abort the press watch is over and the substitution ended (state clean)")
    # the engine is the old one: the keyed press goes to the same PTT group with the same event
    chk(all(g == PTT_GROUP for g, f in t.flag_posts), "every PTT post of the one-shots goes to the PTT event group 0x3a4f0 (krk_vf_press / krk_vf_release)")
    reset_tx()
    t.call(t.fn("par_set"), 0)
    t.fake.clear()
    t.call(t.fn("krk_vf_big_release"), 1)
    ok, _ = t.call(t.fn("krk_vf_big_claim"), 2)
    chk(ok and t.r32(t.vf) == 0x56463031, "bigbuf can be claimed by another owner (F13) once the parrot has released it")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true", help="also check the baseband window (models image only)")
    args = ap.parse_args()
    img = PATCHED.read_bytes()
    stock = STOCK.read_bytes()
    s1, s2 = syms(elf(1)), syms(elf(2))
    static_checks(img, s2, stock, s1)
    with halted_machine(PATCHED) as c:
        iram = read_mem(c, 0x2E9EA, 4)
        pc = 0x2E9EA
        kind, half = decode_branch(iram)
        m.report(kind == "bsr" and pc + half * 2 == s2["krk_vf_tx_hook"][0][0], "the loaded IRAM copy carries the patched bsr (0x2e9ea -> krk_vf_tx_hook)")
        run_checks(c, s1, s2, args.window)
    print("ALL PASS" if m.ok else "FAILURES")
    sys.exit(0 if m.ok else 1)


if __name__ == "__main__":
    main()
