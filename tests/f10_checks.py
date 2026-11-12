"""F10 (satellite mode) in-target checks, called from kraken_ui_test.py on the halted QEMU machine.

  1. static: the KRAKEN slot-6 descriptor lives in blob 3 and is labelled "Satellites"; blob 3 fits its claim.
  2. krk_sat_tick (the 1 Hz Doppler writer) driven through gdb with poked state. The stock functions it calls
     (frequency_band_range_check_and_status_set 0x0300d84c, channel_change_apply_squelch 0x0300df08) are
     intercepted at their entries, their arguments recorded, and "returned" from, so the checks cover the words
     written, the hysteresis, the guards (keyed, channel load), the TX band-check fallback, ownership loss,
     restore on stop / cap / end / range clamp, and the deferred restore while keyed.
  3. the shared maintenance hook (krk_periodic.c) reaches krk_sat_tick through the manifest defsym.
  4. SGP4 + look angles + pass search compiled for the CK803S (soft double through libgcc) run on the emulated CPU
     against a host reference (tests/test_sat.c built on the host, which tests/test_sat.py proves against the
     python sgp4 package): same TLE, same time.
  5. stack headroom: the deepest foreground paths are run on a canary-filled stack and the used depth reported.
"""
import datetime
import struct
import sys

from build_artifacts import EXT_TABLE, in_claims      # first: puts the tool directories on sys.path
import mi_rng_test as m
from claims import claim
from gdb_rsp import read_mem
from qemu_menu import OS_TICKS
from target import fcall

BAND, SQUELCH, STOCK_LED, PTT_PIN = 0x0300D84C, 0x0300DF08, 0x03068224, 0x03012F48
RXW, TXW, FLAGS3, LOADF, CALLACT, TXABORT = 0x3846C, 0x38468, 0x3E31B, 0x3E31C, 0x3FDC8, 0x3FDB8
MENU_STACK = 2560                  # task menu: 0x280 words (OSTaskCreate at 0x03035ea0)
SP = 0x18004000                    # scratch stack in unclaimed SRAM (nothing else runs on the halted machine)
CANARY = 0xA5A5A5A5
NMAX = 181
NOM_RX, NOM_TX = 43780000, 14599000      # ISS repeater 437.800 down / 145.990 up, 10 Hz units
BASE = 1_700_000_000


def cdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def delta10(nom, r):
    v = (nom // 1000) * r + cdiv((nom % 1000) * r, 1000)
    return cdiv(v + (-500000 if v < 0 else 500000), 1000000)


class Fx:
    """One tick harness: poke state, run krk_sat_tick (or a hook) to the sentinel, intercepting stock calls."""

    def __init__(self, c, s3):
        self.c, self.s3 = c, s3
        self.r_addr = s3["R"]
        self.ostick = 1000
        self.ptt = 0                     # PTT pin (stock 0x03012f48): default released
        self.ptt_seq = []                # one-shot answers consumed first (race checks)
        self.ret = 0

    def put_r(self, **f):
        d = dict(magic=0x53415431, state=2, stop_req=0, fake=0, tx_blocked=0, why=0, tsrc=1, tx_dead=0, owned=0, sat=0, rx_pend=0, stamp=None,
                 n=NMAX, trim_hz=0, t0=BASE, base_unix=BASE, base_tick=1000, sync_tick=1000, armed_tick=1000,
                 nom_rx=NOM_RX, nom_tx=NOM_TX, saved_rx=14400000, saved_tx=14400000, owned_rx=0, owned_tx=0,
                 cur_rx=0, cur_tx=0, n_tick=0, n_skip=0, n_retune=0, rssi_pk=0, last_ppb=0, ppb=10000)
        d.update(f)
        if d["stamp"] is None:
            d["stamp"] = self.ostick
        ppb = d["ppb"] if isinstance(d["ppb"], list) else [d["ppb"]] * NMAX
        keys = ("t0", "base_unix", "base_tick", "sync_tick", "armed_tick", "nom_rx", "nom_tx", "saved_rx", "saved_tx",
                "owned_rx", "owned_tx", "cur_rx", "cur_tx", "n_tick", "n_skip", "n_retune")
        hdr = struct.pack("<I4B4BBBHi", d["magic"], d["state"], d["stop_req"], d["fake"], d["tx_blocked"], d["why"], d["tsrc"],
                          d["tx_dead"], d["owned"], d["sat"], d["rx_pend"], d["n"], d["trim_hz"])
        hdr += struct.pack("<16I", *(d[k] for k in keys)) + struct.pack("<H2xiI", d["rssi_pk"], d["last_ppb"], d["stamp"])
        m.wmem(self.c, self.r_addr, hdr + struct.pack("<%dh" % NMAX, *ppb) + b"\0\0")

    def get_r(self):
        raw = read_mem(self.c, self.r_addr, 96)
        v = struct.unpack("<I4B4BBBHi16IH2xiI", raw)
        names = ["magic", "state", "stop_req", "fake", "tx_blocked", "why", "tsrc", "tx_dead", "owned", "sat", "rx_pend", "n", "trim_hz",
                 "t0", "base_unix", "base_tick", "sync_tick", "armed_tick", "nom_rx", "nom_tx", "saved_rx", "saved_tx",
                 "owned_rx", "owned_tx", "cur_rx", "cur_tx", "n_tick", "n_skip", "n_retune", "rssi_pk", "last_ppb", "stamp"]
        return dict(zip(names, v))

    def mem(self, rx=14400000, tx=14400000, flags3=0, loadf=0, callact=0, txabort=0, ostick=1000):
        m.wmem(self.c, RXW, struct.pack("<I", rx))
        m.wmem(self.c, TXW, struct.pack("<I", tx))
        m.wmem(self.c, FLAGS3, bytes([flags3]))
        m.wmem(self.c, LOADF, bytes([loadf]))
        m.wmem(self.c, CALLACT, bytes([callact]))
        m.wmem(self.c, TXABORT, bytes([txabort]))
        m.wmem(self.c, OS_TICKS, struct.pack("<I", ostick))
        self.ostick = ostick

    def words(self):
        return struct.unpack("<II", read_mem(self.c, TXW, 8))[::-1]       # (rx, tx)

    def run(self, fn, band_fail=(), extra_stops=None):
        """Runs fn() to the sentinel. band_fail: per-call list of bools = set FLAGS3 bit 5 when the n-th band check is
        made (missing entries = clear it). Returns the list of intercepted stock calls (name, r0, r1)."""
        c = self.c
        for i in range(4, 12):
            m.setreg(c, i, 0x5A5A0000 + i)
        m.setreg(c, 14, SP)
        m.setreg(c, 15, m.SENTINEL)
        m.setreg(c, 0x48, fn)
        calls, nband = [], 0
        stops = [m.SENTINEL, BAND, SQUELCH, STOCK_LED, PTT_PIN]
        while True:
            pc = m.run_to(c, stops)
            if pc == m.SENTINEL:
                break
            r = m.regs(c)
            if pc == BAND:
                bad = nband < len(band_fail) and band_fail[nband]
                nband += 1
                f = struct.unpack("B", read_mem(c, FLAGS3, 1))[0]
                m.wmem(c, FLAGS3, bytes([(f | 0x20) if bad else (f & ~0x20)]))
                calls.append(("band", r["r0"], r["r1"]))
            elif pc == PTT_PIN:
                m.setreg(c, 0, self.ptt_seq.pop(0) if self.ptt_seq else self.ptt)
                m.setreg(c, 0x48, r["r15"] & ~1)
                continue
            elif pc == SQUELCH:
                calls.append(("squelch", struct.unpack("<I", read_mem(c, RXW, 4))[0], 0))
            else:
                calls.append(("led", 0, 0))
            m.setreg(c, 0x48, r["r15"] & ~1)
        r = m.regs(c)
        self.ret = r["r0"]
        self.abi = r["r14"] == SP and all(r[f"r{i}"] == 0x5A5A0000 + i for i in range(4, 12))
        return calls


def check_f10(c, img, s2, s3):
    rd32 = lambda a: struct.unpack("<I", img[a - 0x3000000:a - 0x3000000 + 4])[0]
    fx = Fx(c, s3)

    print("== F10 satellite mode")
    slot5 = rd32(EXT_TABLE + 4 * 8)
    lbl = img[rd32(slot5) - 0x3000000:].split(b"\0")[0] if in_claims(slot5, "flash") else None
    m.report(slot5 == s3["krk_sat_screen"] and lbl == b"Satellites",
             f"KRAKEN slot 8 (was 5 before the 2026-11-02 regrouping) -> {slot5:#x} (krk_sat_screen {s3['krk_sat_screen']:#x}), label {lbl}")
    m.report(in_claims(s3["krk_sat_tick"], "flash") and in_claims(s3["R"], "sram", 456),
             f"krk_sat_tick {s3['krk_sat_tick']:#x} in a Kraken flash claim; state R {s3['R']:#x}+456 inside a Kraken SRAM claim")

    print("-- tick: idle / noise")
    fx.mem()
    m.wmem(c, fx.r_addr, b"\xA5" * 8)                       # SRAM noise: no magic
    calls = fx.run(s3["krk_sat_tick"])
    m.report(not calls and fx.words() == (14400000, 14400000) and fx.abi, f"no magic -> nothing touched ({calls})")
    fx.put_r(state=0)
    calls = fx.run(s3["krk_sat_tick"])
    m.report(not calls and fx.words() == (14400000, 14400000), "idle state -> nothing touched")

    print("-- tick: first apply, hysteresis, trim")
    ppb = 10000
    rx = NOM_RX - delta10(NOM_RX, ppb)
    tx = NOM_TX + delta10(NOM_TX, ppb)
    fx.mem()
    fx.put_r(ppb=ppb)
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(fx.words() == (rx, tx) and calls == [("band", rx, tx), ("squelch", rx, 0)] and fx.abi,
             f"receding {ppb} ppb: RX {fx.words()[0]} (want {rx}), TX {fx.words()[1]} (want {tx}); calls {calls}")
    m.report(r["owned"] == 1 and r["owned_rx"] == rx and r["owned_tx"] == tx and r["n_retune"] == 1 and r["n_tick"] == 1,
             f"ownership recorded {r['owned_rx']}/{r['owned_tx']}, retunes {r['n_retune']}")
    # same state again with a tiny change (100 ppb = 43 units < 50): TX word follows at full precision, RX not retuned
    fx.put_r(ppb=ppb + 100, owned=1, owned_rx=rx, owned_tx=tx, n_retune=1)
    calls = fx.run(s3["krk_sat_tick"])
    tx2 = NOM_TX + delta10(NOM_TX, ppb + 100)
    m.report(fx.words() == (rx, tx2) and calls == [("band", rx, tx2)],
             f"+100 ppb: RX kept {fx.words()[0]}, TX {fx.words()[1]} (want {tx2}), no retune; calls {calls}")
    fx.put_r(ppb=ppb + 1200, owned=1, owned_rx=rx, owned_tx=tx2)
    calls = fx.run(s3["krk_sat_tick"])
    rx3 = NOM_RX - delta10(NOM_RX, ppb + 1200)
    m.report(fx.words()[0] == rx3 and ("squelch", rx3, 0) in calls, f"+1200 ppb (>= 500 Hz): retuned to {fx.words()[0]} (want {rx3})")
    fx.mem()
    fx.put_r(ppb=-10000, trim_hz=500)
    fx.run(s3["krk_sat_tick"])
    want = (NOM_RX - delta10(NOM_RX, -10000) + 50, NOM_TX + delta10(NOM_TX, -10000) + 50)
    m.report(fx.words() == want, f"approaching -10000 ppb with +500 Hz trim: {fx.words()} (want {want})")
    # interpolation: ramp 0..+18000 ppb over 1800 s (100 ppb per sample); at dt = 45 s expect 450 ppb (exact for a linear ramp)
    ramp = [i * 100 for i in range(NMAX)]
    fx.mem(ostick=1000 + 4500)
    fx.put_r(ppb=ramp)
    fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(r["last_ppb"] == 450, f"quadratic interpolation of a linear ramp at 45 s: {r['last_ppb']} ppb (want 450)")

    print("-- tick: guards")
    for name, kw in (("keyed (0x3fdc8 bit 3)", dict(callact=0x08)), ("channel load (0x3e31c bit 3)", dict(loadf=0x08)),
                     ("tx abort (0x3fdb8 bit 2)", dict(txabort=0x04))):
        fx.mem(**kw)
        fx.put_r(owned=1, owned_rx=14400000, owned_tx=14400000)
        calls = fx.run(s3["krk_sat_tick"])
        r = fx.get_r()
        m.report(not calls and fx.words() == (14400000, 14400000) and r["n_skip"] == 1 and r["state"] == 2,
                 f"{name}: nothing written, no stock call, skip counted")
    fx.mem(tx=14400000)
    fx.put_r(ppb=10000)
    calls = fx.run(s3["krk_sat_tick"], band_fail=[True, False])
    r = fx.get_r()
    m.report(fx.words()[1] == NOM_TX and r["tx_blocked"] == 1 and [x[0] for x in calls] == ["band", "band", "squelch"]
             and calls[1][2] == NOM_TX,
             f"shifted TX fails the stock band check -> nominal uplink {fx.words()[1]}, tx_blocked, second check with the nominal")
    fx.mem()
    fx.put_r(ppb=10000)
    fx.run(s3["krk_sat_tick"], band_fail=[True, True])
    m.report(struct.unpack("B", read_mem(c, FLAGS3, 1))[0] & 0x20 and fx.get_r()["tx_blocked"] == 1,
             "nominal also rejected: stock's TX-denied bit stays set (PTT refused by stock)")
    fx.mem()
    fx.put_r(ppb=10000, tx_dead=1)
    fx.run(s3["krk_sat_tick"])
    m.report(fx.words()[1] == 14400000 and fx.words()[0] != 14400000, "RX-only satellite (tx_dead): TX word never touched")
    fx.mem(rx=11111111, tx=14400000)
    fx.put_r(owned=1, owned_rx=14400000, owned_tx=14400000)
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(r["state"] == 0 and r["why"] == 4 and fx.words() == (11111111, 14400000) and not calls,
             "someone else changed the RX word: tracking ends (why=lost), their word is left alone")

    print("-- tick: restore on stop / cap / end / range")
    for name, kw, mem_kw, why in (
            ("stop request", dict(stop_req=1), {}, 1),
            ("30-minute cap", {}, dict(ostick=1000 + 180001), 3),
            ("track end + 30 s", dict(n=19), dict(ostick=1000 + 100 * 211), 2),
            ("range clamp (-25000 ppb + 5 kHz trim = 15.9 kHz)", dict(ppb=-25000, trim_hz=5000), {}, 5)):
        fx.mem(**mem_kw)
        fx.put_r(owned=1, owned_rx=43779562, owned_tx=14599146, **kw)
        m.wmem(c, RXW, struct.pack("<I", 43779562))
        m.wmem(c, TXW, struct.pack("<I", 14599146))
        calls = fx.run(s3["krk_sat_tick"])
        r = fx.get_r()
        m.report(fx.words() == (14400000, 14400000) and r["state"] == 0 and r["why"] == why
                 and calls == [("band", 14400000, 14400000), ("squelch", 14400000, 0)] and fx.abi,
                 f"{name}: words back to the channel's own, band re-check + retune, why={r['why']}")
    fx.mem(callact=0x08)
    fx.put_r(stop_req=1, owned=1, owned_rx=43779562, owned_tx=14599146)
    m.wmem(c, RXW, struct.pack("<I", 43779562))
    m.wmem(c, TXW, struct.pack("<I", 14599146))
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(r["state"] == 3 and not calls and fx.words() == (43779562, 14599146),
             "stop while keyed: state RESTORE, the over is not touched")
    m.wmem(c, CALLACT, bytes([0]))
    calls = fx.run(s3["krk_sat_tick"])
    m.report(fx.get_r()["state"] == 0 and fx.words() == (14400000, 14400000) and [x[0] for x in calls] == ["band", "squelch"],
             "next tick after the over: restored")

    print("-- review fixes: ownership before restore, reboot, PTT race, self-test restore")
    for name, kw, mem_kw in (("stop request", dict(stop_req=1), {}), ("30-minute cap", {}, dict(ostick=1000 + 180001)),
                             ("track end", dict(n=19), dict(ostick=1000 + 100 * 211))):
        fx.mem(rx=11111111, tx=22222222, **mem_kw)              # the radio now holds another channel's words
        fx.put_r(owned=1, owned_rx=43779562, owned_tx=14599146, **kw)
        calls = fx.run(s3["krk_sat_tick"])
        r = fx.get_r()
        m.report(not calls and fx.words() == (11111111, 22222222) and r["state"] == 0 and r["owned"] == 0 and r["why"] == 4,
                 f"{name} with words that are no longer ours: nothing written, no stock call, why=lost")
    fx.mem(callact=0x08, rx=11111111, tx=22222222)
    fx.put_r(stop_req=1, owned=1, owned_rx=43779562, owned_tx=14599146)
    fx.run(s3["krk_sat_tick"])
    fx.mem(rx=11111111, tx=22222222)                            # over ended, the channel changed meanwhile
    calls = fx.run(s3["krk_sat_tick"])
    m.report(not calls and fx.words() == (11111111, 22222222) and fx.get_r()["state"] == 0,
             "deferred restore (RESTORE state): ownership re-tested at the retry, foreign words left alone")
    fx.mem(ostick=1000 + 3001)                                  # stale magic: counter above the stamp but no tick for 30 s
    fx.put_r(owned=1, owned_rx=43779562, owned_tx=14599146, stamp=1000)
    m.wmem(c, RXW, struct.pack("<I", 43779562))
    m.wmem(c, TXW, struct.pack("<I", 14599146))
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(not calls and r["state"] == 0 and r["owned"] == 0 and fx.words() == (43779562, 14599146),
             "reboot with a stale stamp (counter above it): state dropped, even matching words are NOT restored")
    fx.mem(ostick=500)                                          # counter below the stamp
    fx.put_r(owned=1, owned_rx=43779562, owned_tx=14599146, stamp=90000)
    calls = fx.run(s3["krk_sat_tick"])
    m.report(not calls and fx.get_r()["state"] == 0 and fx.get_r()["magic"] == 0x53415431, "reboot with the counter below the stamp: state dropped")
    fx.mem(ostick=1000 + 200)                                   # normal 2 s gap: no reboot
    fx.put_r(ppb=10000, stamp=1000)
    calls = fx.run(s3["krk_sat_tick"])
    m.report(fx.get_r()["state"] == 2 and fx.get_r()["n_tick"] == 1, "(control) a 2 s gap since the last tick is no reboot")
    # satr_ensure on a stale stamp
    fx.mem(ostick=1000 + 5000)
    fx.put_r(state=2, stamp=1000)
    fx.run(s3["satr_ensure"])
    m.report(fx.get_r()["state"] == 0 and fx.get_r()["magic"] == 0x53415431, "satr_ensure (screen entry) also drops a stale ACTIVE state")

    fx.mem()
    fx.ptt = 1
    fx.put_r(ppb=10000)
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(not calls and fx.words() == (14400000, 14400000) and r["n_skip"] == 1, "PTT pin down (before any keyed bit): nothing written, no retune, skip counted")
    fx.ptt = 0
    fx.mem()
    fx.ptt_seq = [0, 1]                                         # pressed after the first test, before the retune
    fx.put_r(ppb=10000)
    calls = fx.run(s3["krk_sat_tick"])
    m.report(fx.words()[0] == 14400000 and ("squelch" not in [x[0] for x in calls]) and fx.get_r()["n_retune"] == 0,
             "PTT pin goes down just before the RX word write: RX word and chip untouched")
    fx.mem()
    fx.ptt_seq = [0, 0, 1]                                      # pressed after the word write, before apply_squelch
    fx.put_r(ppb=10000)
    calls = fx.run(s3["krk_sat_tick"])
    r = fx.get_r()
    m.report(("squelch" not in [x[0] for x in calls]) and r["rx_pend"] == 1 and fx.words()[0] != 14400000,
             "PTT pin goes down right before apply_squelch: chip retune skipped and left pending")
    fx.mem(rx=fx.words()[0], tx=fx.words()[1])
    fx.put_r(ppb=10000, owned=1, owned_rx=fx.words()[0], owned_tx=fx.words()[1], rx_pend=1)
    calls = fx.run(s3["krk_sat_tick"])
    m.report(("squelch", fx.words()[0], 0) in calls and fx.get_r()["rx_pend"] == 0, "next free pass applies the pending retune")

    print("-- self-test restore (satr_restore_now) has the restore() guards")
    fx.mem()
    fx.put_r(state=0, owned=1, owned_rx=43779562, owned_tx=14599146)
    m.wmem(c, RXW, struct.pack("<I", 43779562))
    m.wmem(c, TXW, struct.pack("<I", 14599146))
    calls = fx.run(s3["satr_restore_now"])
    m.report(fx.ret == 1 and fx.words() == (14400000, 14400000) and [x[0] for x in calls] == ["band", "squelch"], "words still ours: restored")
    fx.mem(rx=11111111, tx=22222222)
    fx.put_r(state=0, owned=1, owned_rx=43779562, owned_tx=14599146)
    calls = fx.run(s3["satr_restore_now"])
    m.report(fx.ret == 1 and not calls and fx.words() == (11111111, 22222222), "channel changed meanwhile: nothing written")
    fx.mem(callact=0x08, rx=43779562, tx=14599146)
    fx.put_r(state=0, owned=1, owned_rx=43779562, owned_tx=14599146)
    calls = fx.run(s3["satr_restore_now"])
    m.report(fx.ret == 0 and not calls and fx.get_r()["state"] == 3, "keyed: deferred to the maintenance tick (state RESTORE), nothing written")

    print("-- shared maintenance hook reaches krk_sat_tick")
    fx.mem()
    fx.put_r(ppb=10000)
    calls = fx.run(s2["krk_hook_maint_tick"])
    r = fx.get_r()
    m.report(calls[0] == ("led", 0, 0) and r["n_tick"] == 1 and fx.words() == (rx, tx),
             f"krk_hook_maint_tick: stock call first, then the tick ran (n_tick {r['n_tick']}), words {fx.words()}")

    check_orbit(c, fx, s3)
    check_stack(c, fx, s3)
    m.wmem(c, fx.r_addr, b"\0" * 8)


# ---- orbit maths on the target -------------------------------------------------------------------------------

L1 = b"1 25544U 98067A   26302.17661613  .00005622  00000+0  10975-3 0  9993"
L2 = b"2 25544  51.6334 357.0447 0007004 322.8383  37.2120 15.49096712592526"
LAT, LON = 41.714, -72.727


def sat_time(y, mo, d, h, mi, s):
    return (datetime.datetime(y, mo, d) - datetime.datetime(1949, 12, 31)).days + (h * 3600 + mi * 60 + s) / 86400.0


# host build of tests/test_sat.c ("track" at 2026-10-29 12:00:00 UTC, +60 s steps), WGS-72 SGP4, python-sgp4-verified:
# columns az el range rr
HOST = [(317.941771622, -61.745273922, 11712.745036800, -3.244633789623),
        (317.533079819, -59.812638419, 11511.933729738, -3.448438211232),
        (317.152096130, -57.876755132, 11299.011191004, -3.648298241367)]


def check_orbit(c, fx, s3):
    print("-- SGP4 / look angles on the CK803S (soft double) vs the host reference")
    SAT, OBS, OUT, STR = 0x18003000, 0x18003200, 0x18003240, 0x18003400
    m.wmem(c, STR, L1 + b"\0" + L2 + b"\0")
    m.setreg(c, 14, SP)
    m.call_a(c, s3["krk_sat_init"], SAT, STR, STR + 70) if False else None

    def call(fn, args, stack=()):
        return fcall(c, fn, tuple(args) + tuple(stack), sp=SP - 64)[1]      # the 5th argument and later sit on the stack at sp

    e = call(s3["krk_sat_init"], (SAT, STR, STR + 70))
    m.report(e == 0, f"krk_sat_init(ISS) on target -> {e}")
    call(s3["krk_sat_obs_min4"], (OBS, int(LAT * 600000), int(round(LON * 600000)), 0))
    for k, want in enumerate(HOST):
        t = sat_time(2026, 10, 29, 12, 2 * 0 + k, 0)
        lo, hi = struct.unpack("<II", struct.pack("<d", t))
        e = call(s3["krk_sat_look"], (SAT, OBS, lo, hi), stack=(OUT,))
        az, el, rng, rr = struct.unpack("<4d", read_mem(c, OUT, 32))
        ok = e == 0 and abs(az - want[0]) < 1e-4 and abs(el - want[1]) < 1e-4 and abs(rng - want[2]) < 1e-3 and abs(rr - want[3]) < 1e-6
        m.report(ok, f"+{k} min: az {az:.6f} el {el:.6f} rng {rng:.5f} rr {rr:.7f} km/s (host {want})")
        if not ok and k == 0:
            print("     NOTE: if these differ wildly the soft-double helpers misbehave under QEMU (older docs: stock int->double)")
    # Doppler on target: 437.800 MHz at rr = -3.2446 km/s -> f * (1 + 3.2446/c)
    rrv = HOST[0][3]
    lo, hi = struct.unpack("<II", struct.pack("<d", rrv))
    rx = call(s3["krk_sat_doppler_rx"], (437800000, lo, hi))
    want = round(437800000 * (1 - rrv / 299792.458))
    m.report(abs(rx - want) <= 1, f"krk_sat_doppler_rx(437.8 MHz, {rrv:.4f} km/s) = {rx} (want {want})")


def check_stack(c, fx, s3):
    print("-- stack headroom of the foreground paths (task menu has 2560 bytes in total)")
    SAT, OBS = 0x18003000, 0x18003200
    top, base = SP, SP - 0x700

    def depth(fn, args, stack=()):
        m.wmem(c, base, struct.pack("<I", CANARY) * ((top - base) // 4))
        for i, a in enumerate(args):
            m.setreg(c, i, a & 0xFFFFFFFF)
        sp = top - 64
        m.wmem(c, sp, b"".join(struct.pack("<I", a & 0xFFFFFFFF) for a in stack) or b"\0" * 4)
        m.setreg(c, 14, sp)
        m.setreg(c, 15, m.SENTINEL)
        m.setreg(c, 0x48, fn)
        m.run_to(c, [m.SENTINEL], timeout=600)
        raw = read_mem(c, base, top - base)
        used = 0
        for off in range(0, top - base, 4):
            if struct.unpack_from("<I", raw, off)[0] != CANARY:
                used = top - (base + off)
                break
        return used - 64 + 64          # includes the 64 bytes of caller area we reserved

    # observer and satellite are initialised by check_orbit; run a look and a pass search
    t = sat_time(2026, 10, 29, 12, 0, 0)
    lo, hi = struct.unpack("<II", struct.pack("<d", t))
    OUT = 0x18003240
    d1 = depth(s3["krk_sat_look"], (SAT, OBS, lo, hi), stack=(OUT,))
    # next_pass(s, o, t0, horizon, min_el, &p): doubles after r3 go on the stack
    hz = struct.pack("<d", 1.0)
    me = struct.pack("<d", 5.0)
    P = 0x18003300
    # try 8-byte aligned stack doubles first (ABIv2), reading the result to see which layout was right
    for layout, stk in (("8-byte", (struct.unpack("<I", hz[:4])[0], struct.unpack("<I", hz[4:])[0],
                                    struct.unpack("<I", me[:4])[0], struct.unpack("<I", me[4:])[0], P)),):
        d2 = depth(s3["krk_sat_next_pass"], (SAT, OBS, lo, hi), stack=stk)
    pas = struct.unpack("<4dI", read_mem(c, P, 36)) if False else None
    nxt = struct.unpack("<5d", read_mem(c, P, 40))
    print(f"     next_pass result (aos, los, tmax, max_el): {[round(x, 5) for x in nxt[:4]]}")
    m.report(abs((nxt[0] - t) * 86400 - 1826.734) < 0.5 and abs((nxt[1] - t) * 86400 - 2194.234) < 0.5 and abs(nxt[3] - 12.7268) < 1e-3,
             f"next_pass on target: AOS +{(nxt[0] - t) * 86400:.1f} s, LOS +{(nxt[1] - t) * 86400:.1f} s, max el {nxt[3]:.4f} "
             "(host: 1826.734, 2194.234, 12.7268)")
    print(f"     stack used: krk_sat_look {d1} bytes, krk_sat_next_pass {d2} bytes")
    m.report(0 < d1 < 900 and 0 < d2 < 1200, f"deepest foreground chains fit with room below the stock menu frames "
                                           f"(look {d1}, next_pass {d2} of {MENU_STACK})")
