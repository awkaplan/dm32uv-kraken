"""F13 (burst voice) in-target checks, called from kraken_ui_test.py on the halted QEMU machine (check_f13), or standalone:

    docker run ... python3 /w/dm32uv-unleashed/tests/f13_checks.py        (tools/build.sh mounts the workspace; see the header of main())

  1. static: the descriptor in Channel Set slot 1, code and state inside Kraken's claims (tools/pack_kraken.py places them), the
     bigbuf tail, and the skip-an-invalid-row path of krk_core.c (krk_hook_gen) run on a second machine whose Burst Voice / DMR Link
     descriptor has an erased label word: the generator must still append the other rows.
  2. the BV1 modem on the CK803S under QEMU: the fixed 12-unit message gives the SAME PCM as the host build (tests/data/f13_burst.pcm,
     written by tests/test_f13.py), and the receiver, fed the host-generated burst from memory 256 samples at a time, decodes it on target
     and the screen stores the units as the received message.
  3. the screen on the halted machine with the stock calls intercepted: ownership vs the parrot, the TX gates (no PTT press without them),
     the full analog send sequence (press, source switch and restore, release, watchdog state), the watchdog, relay capture sink, DMR
     provider and send sequence, the stock playback sequence (order, arguments, the 27 bytes of every push), the unkeyed record sequence.
  4. CPU: exact instruction counts per 256-sample block of the TX fill and the RX feed (QEMU one-instruction-per-TB trace).
"""
import hashlib
import json
import pathlib
import struct
import sys
import tempfile

from build_artifacts import EXT_TABLE, REPO, STOCK_IMG, elf, in_claims, syms, krk_define      # first: puts the tool directories on sys.path
import mi_rng_test as m
import dw_checks as dwc
import target as tg
from claims import claim as alloc      # (check_f13 has a local called claim)
from gdb_rsp import read_mem
from qemu_menu import OS_TICKS, free_port
from target import halted_machine

DATA = REPO / "tests/data"
BIG, _BIG_END = alloc("kraken_bigbuf")
BIG_LEN, BVT_SIZE = _BIG_END - BIG, 0x200
BIG_SC = 92 * 27 + 2106                    # krk_bvscr.c: message, payload buffer, then the 512-byte sample scratch
BPAY, SCP = BIG + 92 * 27, BIG + BIG_SC     # payload buffer and sample scratch addresses (the tail from BIG + 0x1400 is state: never scribble there)
OSFLAG_POST, TX_INHIBIT, RX_ACTIVE = 0x0300C494, 0x0302ED50, 0x0300F2A4
DLY, SEM_PEND, SEM_POST, KEYSCAN, LCDRESTORE = 0x0300C4BC, 0x0300C500, 0x0300C53C, 0x03013660, 0x0301CAD0
GPIO_SET, GPIO_CLR, FD_W, FD_R = 0x0002C500, 0x0002C544, 0x0002C2F4, 0x0002C33C
DW_STATE = 0x030641E4
TXUP, VFO8, CIPHER, BAND, CALLACT, OWN_ID = 0x3BD50, 0x3E1E4, 0x33824, 0x3E31B, 0x3FDC8, 0x33CD0
SEL, LOADED = 0x3E9E1, 0x400D9
MODEM = 0x11000000
FM_SW, AUDC, DEVC = MODEM + 0x56C, MODEM + 0x80, MODEM + 0x504
PTT_GROUP = 0x3A4F0
# stock playback / encoder entries used by the screen
AC = dict(mode=0x16000, on=0x160C0, off=0x16104, key=0x16064, push=0x161A8, vol_set=0x3055850, vol_restore=0x30230BC, pa=0x301D6CC,
          enc_start=0x16088, enc_stop=0x160B0)
S_OFF = dict(buf=0, why=4, src=8, t_done=12, orig_aud=16, conf_t=20, sig=24, air_ms=28, cap_src=32, cap_n=36, n=38, cap_in=40, cap_done=41,
             msg_id=42, rs=44, origin=46, layout=47, parity=48, mode=49, lead=50, rxon=51, relay=52, have_orig=53, released=54,
             conf_key=55, rx_ok=56, rx_tot=57, evm_db=58, rx_last=59, good=60, cap_t=64, t_half=68, norx=72, rly_want=73)
S_SIZE, W_SIZE = 76, 16
W_OFF = dict(magic=0, stamp=4, deadline=8, txing=12, own=13)
VF = dict(magic=0, stamp=4, flags=8, done=9, tap_n=10, quiet=11, t_quiet=12, tx_fn=16, rx_sink=20, t_begin=24, max_ticks=28, max_bursts=32, tx_idx=36,
          end_idx=40, tx_calls=44, tx_subst=48, tx_silence=52, tx_pass=56, rx_calls=60, rx_sunk=64, rel_posts=68)   # krk_vframe.h, 180 B
SIL = bytes.fromhex("b9e88152617300 2a6b".replace(" ", "")) * 3


class Rig:
    def __init__(self, c, s1, s2, s4):
        self.c, self.s1, self.s2, self.s4 = c, s1, s2, s4
        self.a4 = {k: v[0][0] for k, v in s4.items()}
        self.S, self.W = self.a4["S"], self.a4["W"]
        self.posts = []
        self.abi_bad = 0

    # memory helpers (tests/target.py)
    def w8(self, a, v):
        tg.w8(self.c, a, v)

    def w16(self, a, v):
        tg.w16(self.c, a, v)

    def w32(self, a, v):
        tg.w32(self.c, a, v)

    def r8(self, a):
        return tg.r8(self.c, a)

    def r16(self, a):
        return tg.r16(self.c, a)

    def r32(self, a):
        return tg.r32(self.c, a)

    def cstr(self, a):
        return tg.cstr(self.c, a).decode() if a else None

    def sget(self, k):
        a = self.S + S_OFF[k]
        return self.r32(a) if k in ("buf", "why", "src", "t_done", "orig_aud", "conf_t", "sig", "air_ms", "cap_src", "cap_t", "t_half") else \
            (self.r16(a) if k in ("cap_n", "n", "msg_id", "rs") else self.r8(a))

    def sset(self, k, v):
        a = self.S + S_OFF[k]
        (self.w32 if k in ("buf", "why", "src", "t_done", "orig_aud", "conf_t", "sig", "air_ms", "cap_src", "cap_t", "t_half") else
         self.w16 if k in ("cap_n", "n", "msg_id", "rs") else self.w8)(a, v)

    def wget(self, k):
        a = self.W + W_OFF[k]
        return self.r32(a) if k in ("magic", "stamp", "deadline") else self.r8(a)

    def wset(self, k, v):
        a = self.W + W_OFF[k]
        (self.w32 if k in ("magic", "stamp", "deadline") else self.w8)(a, v)

    def why(self):
        return self.cstr(self.sget("why"))

    def vf(self, k):
        a = self.s2["krk_vf"] + VF[k]
        return self.r8(a) if k in ("flags", "done", "tap_n", "quiet") else self.r32(a)

    # the fake stock environment
    def env(self, analog=True, digital=False, inhibit=0, rx_active=0, txup=0, band=0, cipher=0, ticks=100000, call_active=0, own=None):
        dwc.chan_poke(None, 0x10 if digital else 0x00, w8=self.w8)      # selected side A, live and tagged (krk_core.c dw state)
        self.w8(TXUP, txup)
        self.w8(BAND, band)
        self.w8(CIPHER, cipher)
        self.w8(CALLACT, call_active)
        self.w32(OS_TICKS, ticks)
        self.w32(self.s2["krk_vf"] + VF["stamp"], ticks)       # krk_rebooted(): a stamp far from the tick means "rebooted, state foreign"
        self.w32(self.W + W_OFF["stamp"], ticks)
        self.inhibit, self.rx_active = inhibit, rx_active

    def call(self, fn, args=(), extra=None, dly=None, watch=()):
        """Runs a target function with every stock call it can reach intercepted. extra: {entry: callable(regs, r0, r1) -> r0 | int}.
        dly(n): called at the n-th os_time_dly_ms (a chance to poke the machine). Returns (stops, r0)."""
        self.posts = []
        self.n_dly = 0
        s1 = self.s1

        def post(regs, r0, r1):
            self.posts.append((r0, r1))
            if r1 == 0x08 and self.press_sets_up:
                self.w8(CALLACT, self.r8(CALLACT) | 0x08)
                if self.press_sets_txup:
                    self.w8(TXUP, self.r8(TXUP) | 1)
            if r1 == 0x10 and not self.ignore_release:
                self.w8(CALLACT, self.r8(CALLACT) & ~0x08)
                self.w8(TXUP, self.r8(TXUP) & ~1)
            return 0

        def delay(regs, r0, r1):
            self.n_dly += 1
            if dly:
                dly(self.n_dly)
            return 0

        self.fd47 = 0x1234
        fakes = {OSFLAG_POST: post, DLY: delay, TX_INHIBIT: lambda r, a, b: self.inhibit, RX_ACTIVE: lambda r, a, b: self.rx_active,
                 SEM_PEND: 0, SEM_POST: 0, KEYSCAN: lambda r, a, b: self.key, LCDRESTORE: 0, GPIO_SET: 0, GPIO_CLR: 0,
                 FD_W: self.fd_w, FD_R: lambda r, a, b: self.fd47 if a == 0x47 else 0x1234, DW_STATE: 0, s1["krk_line"]: 0, s1["krk_text"]: 0,
                 self.s2["krk_bv_cfg"]: lambda r, a, b: self.cfg}     # the menu's settings record (krk_bvcfg.c): faked, no flash read
        fakes.update(extra or {})
        stops, r0, abi, viol = tg.fcall(self.c, fn, args, fakes=fakes, watch=watch)
        self.abi_bad += 0 if abi else 1
        return stops, r0

    def fd_w(self, regs, r0, r1):       # the FD6818 keeps REG_47H: the shared FM Send RAM engine reads it back (krk_aprs_tx.c route_ok)
        if r0 == 0x47:
            self.fd47 = r1
        return 0

    cfg = 1 | 10 << 8               # krk_bv_cfg(): mode fast, lead 10 x 32 ms, relay capture off (the defaults)
    press_sets_up = True
    press_sets_txup = False
    ignore_release = False
    key = 0


def read_pcm(path):
    b = path.read_bytes()
    return struct.unpack("<%dh" % (len(b) // 2), b)


def check_f13(c, img, s1, s2):
    """c: the halted machine (kraken_ui_test.py); s1/s2: symbol tables of blobs 1 and 2."""
    elf4 = elf(4)
    s4 = syms(elf4, sizes=True)
    a4 = {k: v[0][0] for k, v in s4.items()}
    sz4 = {k: v[0][1] for k, v in s4.items()}
    rd32 = lambda a: struct.unpack("<I", img[a - 0x3000000:a - 0x3000000 + 4])[0]
    stock = STOCK_IMG.read_bytes()
    golden = json.loads((DATA / "f13_golden.json").read_text())
    gpcm = read_pcm(DATA / "f13_burst.pcm")
    gunits = bytes.fromhex(golden["units_hex"])
    ok = m.report

    print("== F13 static: descriptor, Channel Set slot, claims")
    BV_SCR = a4["krk_bv_screen"]
    ok(in_claims(BV_SCR, "flash", 20), f"krk_bv_screen at {BV_SCR:#x}, inside a Kraken flash claim")
    # Burst Voice left the KRAKEN list on 2026-11-03 for the stock Channel Set menu
    # (docs/chanset-submenu.md): its slot is the SECOND Channel Set slot, which follows the
    # KRAKEN, APRS and Radio Settings slots. The counts come from krk.h / krk_menu_rows.h so a layout change
    # shows up here instead of silently losing the row.
    cs0 = krk_define("KRK_EXT_MAX") + krk_define("KRK_APRS_MAX") + krk_define("KRK_RS_MAX")
    BV, DMR = krk_define("KRK_CS_ITEM_BV"), krk_define("KRK_CS_ITEM_DMRLINK")
    ok(rd32(EXT_TABLE + 4 * 14) == 0, f"KRAKEN extension slot 14 retired (was blob 4): {rd32(EXT_TABLE + 4 * 14):#x}")
    ok(rd32(EXT_TABLE + 4 * (cs0 + 1)) == BV_SCR,
       f"Channel Set slot 1 (item 23) = {rd32(EXT_TABLE + 4 * (cs0 + 1)):#x} (krk_bv_screen)")
    desc = [rd32(BV_SCR + 4 * i) for i in range(4)]
    lbl = img[desc[0] - 0x3000000:].split(b"\0")[0]
    ok(lbl == b"Burst Voice" and desc[1] == a4["bv_enter"] and desc[2] == a4["bv_tick"] and desc[3] == a4["bv_exit"]
       and struct.unpack("<H", img[BV_SCR - 0x3000000 + 16:BV_SCR - 0x3000000 + 18])[0] == 1,
       f"descriptor: label {lbl}, enter/tick/exit, tick_ms 1")
    ok(all(in_claims(x, "flash") for x in desc), "descriptor, label and handlers inside Kraken's flash claims")
    ok(in_claims(a4["W"], "sram", sz4["W"]) and sz4["W"] == W_SIZE, f"W (watchdog, ownership) {a4['W']:#x} + {sz4['W']} B in a Kraken SRAM claim")
    bvm_end = a4["__bvm_end"]
    ok(in_claims(a4["__bvm_start"], "sram", bvm_end - a4["__bvm_start"]) and bvm_end > a4["__bvm_start"],
       f"modem state {a4['__bvm_start']:#x}..{bvm_end:#x} ({bvm_end - a4['__bvm_start']} B) in a Kraken SRAM claim (link.json group bvm)")
    tail = BIG + BIG_LEN - BVT_SIZE
    ok(a4["__bvt_start"] == tail and a4["__bvt_end"] <= BIG + BIG_LEN and tail <= a4["S"] and a4["S"] + sz4["S"] <= a4["__bvt_end"] and sz4["S"] == S_SIZE,
       f"tail {a4['__bvt_start']:#x}..{a4['__bvt_end']:#x} ({a4['__bvt_end'] - a4['__bvt_start']} of {BVT_SIZE} B) holds framing, AMBE and S ({sz4['S']} B) inside the bigbuf claim")
    ok(a4["krk_bv_tx_fill"] != 0 and in_claims(a4["krk_bv_tx_fill"], "flash"), "modem, framing, AMBE linked")

    # --- second machine: a descriptor whose label word is erased appends no row, and the OTHER appended rows
    # are still appended (krk_core.c krk_hook_gen skips a row that fails ext_ok).
    # Burst Voice moved to the stock Channel Set menu on 2026-11-03 (docs/chanset-submenu.md), so the
    # property is now checked on the Channel Set generator rather than the KRAKEN list builder.
    print("== F13 invalid row: the Channel Set generator appends no row for it and keeps the others")

    CNT, LST = 0x0003E5F8, 0x0003E5FA           # the cells the generator fills (count u16, items)

    def cs_items(cc):
        """The items krk_hook_gen appends, with the STOCK generator faked away (count 0)."""
        m.wmem(cc, CNT, struct.pack("<H", 0))
        m.wmem(cc, LST, b"\xAA" * 33)
        tg.fcall(cc, s1["krk_hook_gen"], (6, CNT, LST), fakes={0x030493F8: 0})
        n = struct.unpack("<H", read_mem(cc, CNT, 2))[0]
        return list(read_mem(cc, LST, min(n, 33)))

    def cs_items_without(lo, hi):
        """cs_items on a second machine whose image has the flash range [lo, hi) erased."""
        bad = bytearray(img)
        bad[lo - 0x3000000:hi - 0x3000000] = b"\xff" * (hi - lo)
        path = pathlib.Path(tempfile.mkdtemp(prefix="f13_noblob_")) / "patched.bin"
        path.write_bytes(bytes(bad))
        with halted_machine(path, port=free_port()) as c2:
            return cs_items(c2)

    # Items 24..30 are the Scrambler pair and the five Parrot rows (2026-11-03, docs/chanset-submenu.md).
    BLOB2_ITEMS = list(range(krk_define("KRK_CS_ITEM_SCRON"), krk_define("KRK_CS_ITEM_PARSTAT") + 1))
    with_blob = cs_items(c)
    ok(with_blob == [DMR, BV] + BLOB2_ITEMS,
       f"every row valid: Channel Set generator appends {with_blob} (want {[DMR, BV] + BLOB2_ITEMS})")
    without = cs_items_without(BV_SCR, BV_SCR + 4)
    ok(without == [DMR] + BLOB2_ITEMS,
       f"Burst Voice label word erased: only Burst Voice (item {BV}) is gone, DMR Link and the other rows stay: {without} "
       f"(want {[DMR] + BLOB2_ITEMS})")

    print("== F3 invalid row: the Channel Set generator skips DMR Link and keeps Burst Voice")
    DMR_SCR = a4["krk_dmrlink_screen"]
    without5 = cs_items_without(DMR_SCR, DMR_SCR + 4)
    ok(without5 == [BV] + BLOB2_ITEMS,
       f"DMR Link label word erased: only DMR Link (item {DMR}) is gone, Burst Voice and the other rows stay: {without5} "
       f"(want {[BV] + BLOB2_ITEMS})")

    # ------------------------------------------------------------------------------------------------------------------
    rig = Rig(c, s1, s2, s4)
    fcall = rig.call
    zero_state = lambda: (m.wmem(c, a4["__bvm_start"], b"\xA5" * (bvm_end - a4["__bvm_start"])), m.wmem(c, tail, b"\xA5" * BVT_SIZE))

    print("== F13 ownership vs the parrot (owner byte in the vframe layer)")
    claim = s2["krk_vf_big_claim"]
    release = s2["krk_vf_big_release"]
    _, r = fcall(s2["krk_vf_service"])
    _, r = fcall(release, (1,))
    _, r = fcall(release, (2,))
    _, got1 = fcall(claim, (1,))
    ok(got1 == BIG, f"parrot (owner 1) claims the free buffer: {got1:#x}")
    rig.env()
    rig.w32(rig.W + W_OFF["magic"], 0)
    sent = bytes(range(64))
    m.wmem(c, tail + 0x100, sent)
    fcall(a4["bv_enter"], ())
    ok(rig.wget("own") == 0 and read_mem(c, tail + 0x100, 64) == sent, "screen enter while the parrot owns the buffer: no ownership, nothing in the buffer touched")
    _, got2 = fcall(claim, (2,))
    ok(got2 == 0, "owner 2 cannot claim while the parrot owns it")
    fcall(release, (1,))
    zero_state()
    rig.w32(BIG + 0x40, 0x12345678)
    fcall(a4["bv_enter"], ())
    ok(rig.wget("own") == 1 and rig.sget("buf") == BIG, f"enter with a free buffer: owned, S.buf {rig.sget('buf'):#x}")
    _, got3 = fcall(claim, (1,))
    ok(got3 == 0, "the parrot cannot claim while Burst Voice owns the buffer (arming is refused)")
    ok(read_mem(c, a4["__bvm_start"], 16) == bytes(16) and read_mem(c, tail, 32) == bytes(32) and rig.sget("mode") == 1 and rig.sget("lead") == 10,
       "enter zeroed the modem and framing state (SRAM noise before), mode fast, lead 10")

    print("== F13 modem on the CK803S == host build (fixed 12-unit message)")
    msg_at = BIG + 0x600                       # inside the message area, away from S
    m.wmem(c, msg_at, gunits)
    fcall(a4["krk_bv_tx_level"], (golden["rms"], 30000))
    fcall(a4["krk_bv_tx_start"], (golden["mode"], golden["msg_id"], golden["src_id"], msg_at, golden["chunks"], 0, golden["flags"]))
    pcm = []
    for k in range(golden["samples"] // 256):
        _, more = fcall(a4["krk_bv_tx_fill"], (SCP,))
        pcm += struct.unpack("<256h", read_mem(c, SCP, 512))
        if not more:
            break
    sha6 = hashlib.sha256(b"".join(struct.pack("<h", v) for v in pcm[:6 * 256])).hexdigest()
    ok(sha6 == golden["tx_sha256"], f"first 6 blocks (1536 samples) hash {sha6[:16]} == host {golden['tx_sha256'][:16]}")
    ok(tuple(pcm) == tuple(gpcm[:len(pcm)]) and len(pcm) == golden["samples"], f"all {len(pcm)} samples of the burst identical to the host build")

    print("== F13 receiver on target decodes the host-generated burst; the screen stores it")
    rig.env()
    pad = [0] * 4096 + list(gpcm) + [0] * 3072
    ev_all = 0
    state0 = fcall(a4["krk_bv_rx_begin"], (BPAY, 78 * 27))
    for k in range(0, len(pad), 256):
        m.wmem(c, SCP, struct.pack("<256h", *(pad[k:k + 256] + [0] * (256 - len(pad[k:k + 256])))))
        _, ev = fcall(a4["krk_bv_rx_feed"], (SCP, 256))
        ev_all |= ev
        if ev & 8:
            break
    ok(ev_all & 8 and ev_all & 1 and ev_all & 2, f"events on target: sync/header/done = {ev_all:#x}")
    got = read_mem(c, BPAY, 324)
    ok(got == gunits, "the 12 units came back bit exact from the received chunks")
    # screen path: feed the same burst through krk_bv_rx_block into the message store
    zero_state()
    rig.w32(rig.W + W_OFF["magic"], 0)
    fcall(release, (2,))
    rig.env(analog=True)
    fcall(a4["bv_enter"], ())
    rig.sset("n", 0)
    for k in range(0, len(pad), 256):
        m.wmem(c, BIG + BIG_SC, struct.pack("<256h", *(pad[k:k + 256] + [0] * (256 - len(pad[k:k + 256])))))
        fcall(a4["krk_bv_rx_block"])
        if rig.sget("n"):
            break
    n_msg = rig.sget("n")
    ok(n_msg == 12 and read_mem(c, BIG, 324) == gunits and rig.sget("origin") == 2 and rig.sget("src") == golden["src_id"]
       and rig.sget("rx_ok") == rig.sget("rx_tot") == 2 and rig.why() == "burst received",
       f"screen stored the received burst: n={n_msg} origin {rig.sget('origin')} src {rig.sget('src'):#x} blocks {rig.sget('rx_ok')}/{rig.sget('rx_tot')} "
       f"rs {rig.sget('rs')} evm {rig.sget('evm_db')} dB, status {rig.why()!r}")

    print("== F13 TX gates: no PTT press without them")
    msg = lambda n: (m.wmem(c, BIG, gunits[:27 * min(n, 12)]), rig.sset("n", min(n, 12)))
    gate_a = lambda: (lambda r: rig.cstr(r))(fcall(a4["krk_bv_gate_analog"])[1])
    gate_d = lambda: (lambda r: rig.cstr(r))(fcall(a4["krk_bv_gate_dmr"])[1])
    cases = [
        (dict(), None, "analog idle: open"),
        (dict(digital=True), "Analog channel only", "digital channel"),
        (dict(band=0x20), "TX not allowed here", "band forbids TX"),
        (dict(inhibit=1), "TX inhibited", "stock TX inhibited"),
        (dict(rx_active=1), "Channel busy", "stock RX active"),
        (dict(txup=2), "Channel busy", "RX voice up"),
    ]
    for kw, want, what in cases:
        rig.env(**kw)
        got = gate_a()
        ok(got == want, f"analog gate, {what}: {got!r}")
    rig.w8(rig.W + W_OFF["own"], 0)
    rig.env()
    ok(gate_a() == "Parrot armed" and gate_d() == "Parrot armed", "gates refuse when Burst Voice does not own the buffer")
    rig.w8(rig.W + W_OFF["own"], 1)
    dcases = [
        (dict(digital=True), None, "digital idle: open"),
        (dict(), "Digital channel only", "analog channel"),
        (dict(digital=True, cipher=1), "Encrypted channel", "stock cipher on"),
        (dict(digital=True, band=0x20), "TX not allowed here", "band forbids TX"),
        (dict(digital=True, inhibit=1), "TX inhibited", "stock TX inhibited"),
        (dict(digital=True, rx_active=1), "Channel busy", "stock RX active"),
        (dict(digital=True, txup=1), "Channel busy", "TX voice already up"),
    ]
    for kw, want, what in dcases:
        rig.env(**kw)
        got = gate_d()
        ok(got == want, f"DMR gate, {what}: {got!r}")
    # dual watch (krk_core.c dw tag): the gates require the live struct to BE the selected side's
    rig.env()
    dwc.set_tag(rig.w8, 1)                                  # selected A analog, but the receiver is on B
    ok(gate_a() == "Dual watch: retry", f"analog gate, struct is the other side's: {gate_a()!r}")
    rig.env(digital=True)
    dwc.set_tag(rig.w8, 1)
    ok(gate_d() == "Digital channel only", f"DMR gate, struct is the other side's: {gate_d()!r}")
    rig.env()
    # the callers: a refused send never posts the press event; neither does an empty message
    msg(5)
    for kw, fn, what in ((dict(inhibit=1), "do_send_burst", "send, TX inhibited"), (dict(digital=True), "do_send_burst", "send on a digital channel"),
                         (dict(band=0x20), "do_send_burst", "send, band forbids"), (dict(rx_active=1), "do_send_burst", "send, channel busy"),
                         (dict(digital=True, cipher=1), "do_dmr", "DMR relay, encrypted"), (dict(), "do_dmr", "DMR relay on an analog channel")):
        rig.env(**kw)
        fcall(a4[fn])
        ok(not rig.posts, f"{what}: no PTT event posted ({rig.why()!r})")
    rig.sset("n", 0)
    rig.env()
    fcall(a4["do_send_burst"])
    ok(not rig.posts and rig.why() == "No message", "send with no message: nothing posted")
    rig.env(digital=True)
    fcall(a4["do_dmr"])
    ok(not rig.posts, "DMR relay with no message: nothing posted")

    print("== F13 key handling: a transmit key needs a second press within 3 s")
    rig.env(analog=True)
    msg(12)
    rig.sset("rxon", 0)
    rig.sset("conf_key", 0)
    rig.w32(FM_SW, 0)
    rig.press_sets_up = True
    fcall(a4["bv_tick"], (0x03,))
    ok(not rig.posts and rig.sget("conf_key") == 0x03, "OK once: confirmation asked, nothing keyed")
    fcall(a4["bv_tick"], (0x10 + 2,))
    ok(rig.sget("conf_key") == 0 and rig.sget("mode") == 1 and rig.why() == "Change in the menu",
       f"another key (3): confirmation dropped; the mode is a menu setting now, 3 only says so ({rig.why()!r})")
    rig.sset("mode", 0)
    fcall(a4["bv_tick"], (0x03,))
    ok(not rig.posts, "OK again after another key: asks again, nothing keyed")
    fcall(a4["bv_tick"], (0x03,), dly=lambda n: rig.w32(FM_SW, 1 if n % 2 else 2))
    ok(len([p for p in rig.posts if p[1] == 0x08]) == 1 and any(p[1] == 0x10 for p in rig.posts),
       f"second OK within 3 s confirms: one press, then release ({rig.why()!r})")
    rig.sset("mode", 1)
    rig.env(digital=True)
    rig.sset("conf_key", 0)
    fcall(a4["bv_tick"], (0x03,))
    ok(not rig.posts and rig.why() == "OK = analog send", "OK on a digital channel does not send an analog burst")
    rig.env(analog=True)
    print("== F13 analog send sequence (press, source switch, restore, release) and the watchdog")
    msg(12)
    rig.sset("lead", 0)
    rig.sset("rxon", 0)
    aud0, dev0 = 0x00001234, 0x0000AB00
    rig.w32(AUDC, aud0)
    rig.w32(DEVC, dev0)
    seen = {}

    def toggle(n):                      # every os_time_dly_ms flips the half index so the loop makes progress
        rig.w32(FM_SW, 1 if n % 2 else 2)
        if rig.r32(AUDC) & 1:
            seen["src"] = True
        if rig.r8(rig.W + W_OFF["txing"]):
            seen["wd"] = True
    stops_regs = []
    mark = {}

    def fd_w(regs, r0, r1):
        stops_regs.append((r0, r1))
        return rig.fd_w(regs, r0, r1)

    rig.press_sets_up = True
    rig.w32(FM_SW, 0)
    stops, _ = fcall(a4["do_send_burst"], dly=toggle, extra={FD_W: fd_w})
    presses = [p for p in rig.posts if p[1] == 0x08]
    rels = [p for p in rig.posts if p[1] == 0x10]
    ok(len(presses) == 1 and len(rels) >= 1 and rig.posts.index(presses[0]) < rig.posts.index(rels[0]) and presses[0][0] == PTT_GROUP,
       f"one PTT press (group {PTT_GROUP:#x}, 0x08) before the first release (0x10); posts {[(hex(a), hex(b)) for a, b in rig.posts[:4]]}")
    ok(rig.r32(AUDC) == aud0 and (rig.r32(DEVC) & 0xFFFFFFFF) == dev0, f"AUDIO_CONTROL {rig.r32(AUDC):#x} and FM_DEV_COEF {rig.r32(DEVC):#x} restored")
    ok(stops_regs[:1] == [(0x47, 0x6740)] and stops_regs[-1] == (0x47, 0x1234), f"FD6818 REG_47H set to 0x6740 for the send and restored: {stops_regs}")
    ok(seen.get("src") and seen.get("wd"), "tx_voice_source bit set during the send, watchdog armed during it")
    ok(rig.wget("txing") == 0, "watchdog disarmed after the send")
    ok(rig.why() in ("Sent", "Modem stalled", "TX timeout"), f"send ended with status {rig.why()!r}")
    ok(rig.r8(CALLACT) & 8 == 0, "TX released (call_active bit 3 clear)")
    # the key that started it was never released: nothing aborts; a key after release aborts
    rig.key = 0x03
    rig.w32(FM_SW, 0)
    rig.posts = []
    fcall(a4["do_send_burst"], dly=toggle)
    ok(any(p[1] == 0x10 for p in rig.posts) and rig.why() in ("Aborted", "TX did not come up", "Sent") and (rig.r8(CALLACT) & 8) == 0,
       f"held key never aborts the first 16 polls (unreleased start key), the release is posted: {rig.why()!r}")
    def key_after_release(n):                   # polls happen every 16th wait: key up at the 16th, down again before the 32nd
        rig.key = 0 if n < 24 else 0x10
        toggle(n)
    rig.key = 0
    rig.w32(FM_SW, 0)
    rig.sset("lead", 20)
    fcall(a4["do_send_burst"], dly=key_after_release)
    rig.sset("lead", 0)
    ok(rig.why() == "Aborted" and any(p[1] == 0x10 for p in rig.posts), f"any key after the start key was released aborts the send: {rig.why()!r}")
    rig.key = 0
    # press never brings TX up
    rig.press_sets_up = False
    fcall(a4["do_send_burst"], dly=lambda n: 0)
    ok(rig.why() == "TX did not come up" and any(p[1] == 0x10 for p in rig.posts), "TX that never comes up: release posted, status shown")
    rig.press_sets_up = True

    maint = s2["krk_hook_maint_tick"]
    led_tick = 0x03068224
    extra = {led_tick: 0, s2["krk_vf_service"]: 0, s2["krk_parrot_tick"] if "krk_parrot_tick" in s2 else 0: 0,
             s2["krk_scramble_tick"]: 0, s2["krk_sat_tick"] if "krk_sat_tick" in s2 else 0: 0}
    extra.pop(0, None)
    for ticks, txing, deadline, want, what in ((50000, 1, 49000, 1, "past the deadline: release posted"),
                                               (50000, 1, 51000, 0, "before the deadline: quiet"),
                                               (50000, 0, 49000, 0, "no send of ours in flight: quiet")):
        rig.w32(OS_TICKS, ticks)
        rig.wset("magic", 0x57443133)
        rig.wset("stamp", ticks - 1)
        rig.wset("txing", txing)
        rig.wset("deadline", deadline)
        fcall(maint, extra=extra)
        n_rel = len([p for p in rig.posts if p == (PTT_GROUP, 0x10)])
        ok((n_rel >= 1) == bool(want), f"krk_hook_maint_tick -> krk_bv_wd_tick via the hook table, {what}: {n_rel} release posts")
    rig.wset("magic", 0xA5A5A5A5)
    rig.wset("txing", 1)
    rig.wset("deadline", 1)
    rig.w32(OS_TICKS, 50000)
    fcall(maint, extra=extra)
    ok(rig.wget("txing") == 0 and not [p for p in rig.posts if p == (PTT_GROUP, 0x10)], "noise in W (no magic): cleared, nothing posted")
    rig.wset("magic", 0x57443133)
    rig.wset("stamp", 90000)
    rig.wset("txing", 1)
    rig.wset("deadline", 1)
    rig.w32(OS_TICKS, 100)
    fcall(maint, extra=extra)
    ok(rig.wget("txing") == 0 and rig.wget("own") == 0, "warm reset (tick counter below the stamp): W cleared, nobody owns the buffer")
    rig.wset("own", 1)
    rig.wset("stamp", 99)

    print("== F13 relay capture sink (RX task) and call completion")
    sink = a4["cap_sink"]
    rig.env(digital=True)
    rig.sset("n", 7)
    rig.sset("cap_in", 0)
    rig.sset("cap_n", 0)
    rig.w32(0x3BD34, 3125001)
    frames = [bytes((i * 29 + k) & 0xFF for k in range(27)) for i in range(95)]
    for i in range(3):
        m.wmem(c, SCP, frames[i])
        rig.w8(TXUP, 2)
        fcall(sink, (SCP,))
    ok(rig.sget("cap_in") == 1 and rig.sget("cap_n") == 3 and rig.sget("n") == 0 and rig.sget("cap_src") == 3125001
       and read_mem(c, BIG, 81) == b"".join(frames[:3]), "RX voice up: capture starts, previous message invalidated, 3 bursts stored, source ID noted")
    rig.w8(TXUP, 0)
    fcall(sink, (SCP,))
    ok(rig.sget("cap_done") == 1 and rig.sget("cap_in") == 0 and rig.sget("cap_n") == 3, "RX voice gone: call marked complete")
    rig.w8(TXUP, 0)
    fcall(sink, (SCP,))
    ok(rig.sget("cap_done") == 1 and rig.sget("cap_n") == 3, "bursts outside a call are ignored")
    rig.sset("cap_done", 0)
    for i in range(95):
        m.wmem(c, SCP, frames[i])
        rig.w8(TXUP, 2)
        fcall(sink, (SCP,))
    ok(rig.sget("cap_n") == 92 and read_mem(c, BIG + 27 * 91, 27) == frames[91] + b"" and rig.sget("cap_in") == 1, "capture stops at 92 units (5.5 s), the rest of the call is dropped")
    rig.w8(TXUP, 0)
    fcall(sink, (SCP,))
    # the screen turns a finished capture into the message
    rig.sset("cap_done", 1)
    rig.sset("cap_n", 92)
    rig.sset("cap_src", 3125001)
    rig.sset("rxon", 0)
    rig.env(digital=True)
    fcall(a4["bv_tick"], (0,))
    ok(rig.sget("n") == 92 and rig.sget("origin") == 1 and rig.sget("src") == 3125001 and rig.why() == "call captured",
       f"screen tick: capture becomes the message (n {rig.sget('n')}, origin {rig.sget('origin')}, {rig.why()!r})")
    rig.sset("cap_n", 3)
    rig.sset("cap_done", 1)
    fcall(a4["bv_tick"], (0,))
    ok(rig.why() == "call too short", "a 3-burst call is not a message")
    now = rig.r32(OS_TICKS)                    # the relay capture ends 0.5 s after the last burst (the RX sink is not called after the call)
    rig.sset("cap_done", 0)
    rig.sset("cap_in", 1)
    rig.sset("cap_n", 49)
    rig.sset("cap_t", (now - 20) & 0xFFFFFFFF)
    fcall(a4["bv_tick"], (0,))
    ok(rig.sget("cap_in") == 1, "relay capture: 0.2 s after the last burst the call is still open")
    rig.sset("cap_t", (now - 60) & 0xFFFFFFFF)
    fcall(a4["bv_tick"], (0,))
    ok(rig.sget("cap_in") == 0 and rig.sget("n") == 49 and rig.why() == "call captured", "relay capture: 0.6 s without a burst ends the call and stores 49 units")

    print("== F13 DMR retransmit: provider, gates, key sequence, release")
    dst = SCP
    msg(12)
    want = [(1, SIL)] * 2 + [(1, gunits[27 * i:27 * i + 27]) for i in range(11)] + [(2, gunits[27 * 11:])] + [(0, None)]
    got = []
    for idx in range(len(want)):
        m.wmem(c, dst, b"\xEE" * 27)
        _, rc = fcall(a4["dmr_src"], (dst, idx))
        got.append((rc, read_mem(c, dst, 27) if rc else None))
    ok(got == [(a, b) for a, b in want], f"provider: 2 silence bursts, 12 units, LAST on the 12th, NONE after ({[g[0] for g in got]})")
    rig.env(digital=True)
    rig.sset("relay", 0)
    rig.press_sets_up = True
    rig.press_sets_txup = True

    def txdown(n):
        if n == 6:
            rig.w32(rig.s2["krk_vf"] + VF["end_idx"], 3)       # as the layer does after the last burst
        if n == 8:
            rig.w8(TXUP, 0)
    rig.posts = []
    fcall(a4["do_dmr"], dly=txdown)
    pr = [p for p in rig.posts if p[1] == 0x08]
    rl = [p for p in rig.posts if p[1] == 0x10]
    ok(len(pr) == 1 and rl and rig.posts.index(pr[0]) < rig.posts.index(rl[0]), f"DMR: press then release ({len(pr)} press, {len(rl)} release posts)")
    ok(rig.vf("flags") == 0 and rig.wget("txing") == 0 and rig.why() in ("DMR sent", "Aborted"), f"DMR: substitution ended, watchdog disarmed ({rig.why()!r})")
    # a never-ending TX is cut at the budget
    rig.posts = []
    rig.w8(TXUP, 0)
    t = [100000]
    def stuck(n):
        t[0] += 50
        rig.w32(OS_TICKS, t[0])
        rig.w8(TXUP, rig.r8(TXUP) | 1)
    rig.w32(OS_TICKS, 100000)
    rig.w32(rig.s2["krk_vf"] + VF["stamp"], 100000)
    rig.w32(rig.W + W_OFF["stamp"], 100000)
    rig.ignore_release = True                       # the release event does not bring TX down: a genuinely stuck transmitter
    fcall(a4["do_dmr"], dly=stuck)
    rig.ignore_release = False
    ok(any(p[1] == 0x10 for p in rig.posts) and rig.why() == "TX stuck: released" and rig.vf("flags") != 0 and rig.r8(rig.s2["krk_vf"] + VF["quiet"]) == 1
       and rig.wget("txing") == 1, f"DMR: stuck TX is released at the budget, the substitution stays on in silence-only mode and the watchdog stays armed ({rig.why()!r})")
    rig.w8(TXUP, 0)
    fcall(a4["do_dmr"])
    ok(rig.why() == "Channel busy", "a new DMR relay is refused while a substitution session (e.g. a release watch) is still on")
    fcall(s2["krk_vf_tx_end"])
    rig.wset("txing", 0)
    rig.press_sets_txup = False

    print("== F13 speaker playback: the stock Record playback sequence with units from RAM")
    rig.env(analog=True)
    msg(12)
    rig.sset("rxon", 0)
    calls = []

    def rec(name):
        def f(regs, r0, r1):
            entry = (name, r0, r1)
            if name == "push":
                entry += (read_mem(c, r1, 27), rig.r32(MODEM + 0x100))
            if name == "key":
                entry += (read_mem(c, r0, 7),)
            calls.append(entry)
            return 0
        return f
    dwc.chan_poke(None, 0x00, w8=rig.w8)
    rig.w8(0x3E31F, 0)
    key7 = bytes([1, 2, 3, 4, 5, 6, 7])
    m.wmem(c, 0x33825, key7)
    rig.w8(0x3ECFE, 9)
    rig.w32(MODEM + 0x100, 0x80)
    fakes = {AC[k]: rec(k) for k in AC}
    gp = []
    fakes[GPIO_SET] = lambda regs, r0, r1: gp.append(("set", r0)) or 0
    fakes[GPIO_CLR] = lambda regs, r0, r1: gp.append(("clr", r0)) or 0
    fcall(a4["do_play"], extra=fakes)
    names = [x[0] for x in calls]
    want_names = ["key", "mode", "on", "vol_set", "pa"] + ["push"] * 12 + ["mode", "key", "vol_restore", "off", "pa"]
    ok(names == want_names, f"sequence: {names}")
    ok(gp == [("clr", 0x2F)] * 3 + [("set", 0x2F)],
       f"analog playback: GPIO 0x2F low (codec PWM -> speaker, as on a digital channel) before the first unit, re-asserted every 8 units, high again at the end ({gp})")
    pushes = [x for x in calls if x[0] == "push"]
    ok(all(x[1] == 0 for x in pushes) and [x[3] for x in pushes] == [gunits[27 * i:27 * i + 27] for i in range(12)],
       "every push is (offset 0, the unit, 27 bytes, 70 ms) in message order with the unit's 27 bytes")
    ok(calls[0][3] == bytes(7) and calls[0][2] == 7 and calls[-4][1] == 0x33825 and calls[-4][2] == 7, "key block zeroed for the playback, the stock 7 bytes (0x33825) restored")
    ok(calls[1][1] == 1 and calls[3][1] == 13 and calls[4][1] == 1 and calls[-5][1] == 0 and calls[-3][1] == 9 and calls[-1][1] == 0,
       "playback mode on(1)/off(0), volume 13 then the stock volume index, PA gate on then off")
    ok(all(x[4] & 0x80 == 0 for x in pushes) and rig.r32(MODEM + 0x100) & 0x80,
       "MODEM[0x100] bit 7 clear while units are pushed and set again afterwards on an analog channel (stock does the same)")
    ok(rig.why() == "Played", f"status {rig.why()!r}")
    rig.env(digital=True)
    calls.clear()
    del gp[:]
    fcall(a4["do_play"], extra=fakes)
    ok(not [g for g in gp if g[1] == 0x2F] or all(g[0] == "set" for g in gp), f"digital playback leaves GPIO 0x2F alone ({gp})")
    rig.env(analog=True, rx_active=1)
    calls.clear()
    fcall(a4["do_play"], extra=fakes)
    ok(not calls and rig.why() == "Channel busy", "no playback over a received call")

    print("== F13 unkeyed record (hardware probe): sequence, never keys, silence to the modem window")
    rig.env(digital=True)
    rig.sset("n", 0)
    calls.clear()
    t = [0]
    stock_burst = bytes((i * 7 + 3) & 0xFF for i in range(27))

    def rec_dly(n):
        if n == 5:
            rig.sset("n", 92)                 # buffer full: the loop ends
    enc = {AC["enc_start"]: rec("enc_start"), AC["enc_stop"]: rec("enc_stop")}
    rig.w32(AUDC, 0x11110000)
    rig.w32(MODEM + 0x3B0, 0x22220000)
    rig.posts = []
    fcall(a4["do_record"], dly=rec_dly, extra=enc)
    ok([x[0] for x in calls] == ["enc_start", "enc_stop"] and not rig.posts, f"record: encoder started and stopped, no PTT event ({[x[0] for x in calls]}, posts {rig.posts})")
    ok(rig.r32(AUDC) == 0x11110000 and rig.r32(MODEM + 0x3B0) == 0x22220000 and rig.vf("flags") == 0, "AUDIO_CONTROL and 0x110003b0 restored, TX substitution session ended")
    m.wmem(c, SCP, stock_burst)
    rig.sset("n", 0)
    for k in range(4):
        _, rc = fcall(a4["rec_prov"], (SCP, k))
        ok(rc == 1 and rig.sget("n") == 0 and read_mem(c, SCP, 27) == SIL, f"mic record: start-up burst {k} is dropped (not kept), silence to the modem side")
        m.wmem(c, SCP, stock_burst)
    rig.sset("n", 4)
    _, rc = fcall(a4["rec_prov"], (SCP, 4))
    ok(rc == 1 and rig.sget("n") == 5 and read_mem(c, BIG + 27 * 4, 27) == stock_burst and read_mem(c, SCP, 27) == SIL,
       "provider keeps the stock burst in the message and puts the silence burst on the modem side")
    rig.env(digital=True, txup=1)
    rig.sset("n", 0)
    calls.clear()
    fcall(a4["do_record"], extra=enc)
    ok(not calls and rig.why() == "Channel busy", "record refuses while TX voice is up")
    rig.env(digital=False)
    fcall(a4["do_record"], extra=enc)
    ok(not calls and rig.why() == "Digital channel only", "record refuses on an analog channel")

    ok(rig.abi_bad == 0, f"every target function called above preserved r4-r11 and sp ({rig.abi_bad} violations)")
    print("== F13 exit: a message is kept (buffer stays owned), an empty screen releases it")
    rig.sset("relay", 1)
    rig.env(analog=True)
    rig.sset("n", 12)
    rig.w32(rig.s2["krk_vf"] + VF["rx_sink"], 0x1234)
    fcall(a4["bv_exit"])
    ok(rig.wget("own") == 1 and rig.sget("buf") == BIG and rig.sget("relay") == 0 and rig.sget("n") == 12,
       "Back with a message: the message and ownership are kept")
    _, got4 = fcall(claim, (1,))
    ok(got4 == 0, "while the message is kept the parrot cannot arm (claim refused)")
    ok(rig.vf("rx_sink") == 0, "relay sink removed on exit")
    fcall(a4["bv_enter"], ())
    ok(rig.wget("own") == 1 and rig.sget("n") == 12 and rig.sget("mode") in (0, 1) and read_mem(c, BIG, 27) == gunits[:27],
       "enter again: the kept message and the settings are still there (S and the buffer were not zeroed)")
    rig.sset("n", 0)
    fcall(a4["bv_exit"])
    ok(rig.wget("own") == 0 and rig.sget("buf") == 0, "Back with no message: ownership dropped")
    _, got5 = fcall(claim, (1,))
    ok(got5 == BIG, "the parrot can claim again")
    fcall(release, (1,))
    rig.w8(rig.W + W_OFF["own"], 0)
    fcall(a4["bv_enter"], ())
    ok(rig.wget("own") == 1 and rig.sget("n") == 0 and rig.sget("cap_n") == 0, "a fresh enter after the release starts clean (S zeroed)")
    fcall(a4["bv_exit"])

    print("== F13 settings from the menu record (krk_bv_cfg, docs/burst-voice.md 13): read at enter, relay capture follows the setting")
    rig.env(analog=True)
    rig.cfg = 0 | 15 << 8                       # robust, 480 ms, relay off
    fcall(a4["bv_enter"], ())
    ok(rig.sget("mode") == 0 and rig.sget("lead") == 15 and rig.sget("rly_want") == 0 and rig.sget("relay") == 0,
       f"enter reads the record: mode {rig.sget('mode')} (robust 0), lead {rig.sget('lead')} (15 = 480 ms), relay wanted {rig.sget('rly_want')}")
    for scan, name in ((0x10 + 1, "2"), (0x10 + 2, "3"), (0x02, "Up"), (0x05, "Down")):
        rig.sset("why", 0)
        fcall(a4["bv_tick"], (scan,))
        ok(rig.sget("mode") == 0 and rig.sget("lead") == 15 and rig.sget("relay") == 0 and rig.why() == "Change in the menu",
           f"key {name} changes no setting and says where they are ({rig.why()!r})")
    fcall(a4["bv_exit"])
    rig.cfg = 1 | 10 << 8 | 1 << 16             # relay capture ON
    rig.env(digital=True)
    rig.w32(rig.s2["krk_vf"] + VF["rx_sink"], 0)
    fcall(a4["bv_enter"], ())
    ok(rig.sget("rly_want") == 1 and rig.sget("relay") == 1 and rig.vf("rx_sink") == a4["cap_sink"],
       f"relay ON, digital channel: enter installs the capture sink ({rig.vf('rx_sink'):#x}, cap_sink {a4['cap_sink']:#x})")
    rig.env(analog=True)
    fcall(a4["bv_tick"], (0,))
    ok(rig.sget("relay") == 0 and rig.vf("rx_sink") == 0, "the channel turns analog: the tick removes the sink")
    rig.env(digital=True)
    fcall(a4["bv_tick"], (0,))
    ok(rig.sget("relay") == 1 and rig.vf("rx_sink") == a4["cap_sink"], "digital again: the tick installs it again")
    fcall(a4["bv_exit"])
    ok(rig.sget("relay") == 0 and rig.vf("rx_sink") == 0, "Back removes the sink: nothing is captured while the screen is closed")
    rig.cfg = 1 | 10 << 8
    rig.w32(rig.s2["krk_vf"] + VF["rx_sink"], 0x1234)
    fcall(a4["bv_enter"], ())
    ok(rig.sget("relay") == 0 and rig.vf("rx_sink") == 0x1234, "relay OFF on a digital channel: enter installs nothing (another sink is left alone)")
    fcall(a4["bv_exit"])
    rig.w32(rig.s2["krk_vf"] + VF["rx_sink"], 0)



def cpu_counts():
    """Exact instruction counts of one 256-sample block (TX fill, RX feed in its three situations) by single-stepping on the QEMU gdb stub
    (the exec trace of the other benches misses chained TBs). Slow (minutes): not part of the default suite, run `f13_checks.py --cpu`."""
    elf4 = elf(4)
    golden = json.loads((DATA / "f13_golden.json").read_text())
    gp = read_pcm(DATA / "f13_burst.pcm")
    ut = __import__("kraken_ui_test")
    s4 = syms(elf4, sizes=True)
    a4 = {k: v[0][0] for k, v in s4.items()}
    res = {"tx": [], "rx": []}
    with halted_machine(ut.PATCHED, port=free_port()) as c:
        m.wmem(c, a4["__bvt_start"], b"\0" * (a4["__bvt_end"] - a4["__bvt_start"]))
        m.wmem(c, a4["__bvm_start"], b"\0" * (a4["__bvm_end"] - a4["__bvm_start"]))
        unitsat = BIG + 0x600
        m.wmem(c, unitsat, bytes.fromhex(golden["units_hex"]))
        tg.fcall(c, a4["krk_bv_tx_level"], (golden["rms"], 30000))
        tg.fcall(c, a4["krk_bv_tx_start"], (golden["mode"], golden["msg_id"], golden["src_id"], unitsat, golden["chunks"], 0, golden["flags"]))
        names = {0: "CW + sync", 12: "data"}
        for k in range(golden["samples"] // 256):
            if k in names:
                n, _ = tg.count_steps(c, a4["krk_bv_tx_fill"], (SCP,))
                res["tx"].append((k, names[k], n))
            else:
                tg.fcall(c, a4["krk_bv_tx_fill"], (SCP,))
        pad = [0] * 4096 + list(gp) + [0] * 3072
        tg.fcall(c, a4["krk_bv_rx_begin"], (BPAY, 78 * 27))
        for k in range(0, len(pad), 256):
            m.wmem(c, SCP, struct.pack("<256h", *(pad[k:k + 256] + [0] * (256 - len(pad[k:k + 256])))))
            st = tg.fcall(c, a4["krk_bv_rx_state"])[1]
            if k // 256 in (1, 17, 30):
                n, _ = tg.count_steps(c, a4["krk_bv_rx_feed"], (SCP, 256))
                res["rx"].append((k // 256, ("idle", "searching", "receiving")[st], n))
            else:
                tg.fcall(c, a4["krk_bv_rx_feed"], (SCP, 256))
    return res


def main():
    """standalone: python3 tests/f13_checks.py [--cpu]   (inside the dm32uv-re image with the workspace mounted at /w)"""
    ut = __import__("kraken_ui_test")
    s = syms(ut.ELF)
    s2 = syms(ut.ELF2)
    img = ut.PATCHED.read_bytes()
    with halted_machine(ut.PATCHED) as c:
        check_f13(c, img, s, s2)
    if "--cpu" in sys.argv:
        r = cpu_counts()
        for kind in ("tx", "rx"):
            for blk, what, n in r[kind]:
                print(f"CPU {kind} block {blk:2d} ({what}): {n} instructions per 256 samples = {n / 256:.0f} per sample = {n * 31.25 / 1e6:.2f} MIPS at real time")
    print("ALL PASS" if m.ok else "FAILURES")
    sys.exit(0 if m.ok else 1)


if __name__ == "__main__":
    main()
