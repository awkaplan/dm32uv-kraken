"""F18 APRS Messages, phase 1, in-target checks (patches/kraken_ui/krk_aprs_msg.c, the decoder hand-off in krk_afsk.c emit),
called from kraken_ui_test.py on the halted QEMU machine after the F14 RX Monitor checks.

  1. static: the APRS row (label in both language arrays, the descriptor krk_aprs_msg_screen), the claim
  2. direct injection through the hand-off krk_aprs_msg_frame (it calls krk_msg_rx): noise SRAM -> one message; ack, other addressee and a
     repeat are not stored; no callsign -> nothing
  3. end to end: a message frame synthesized as AFSK, fed half by half through the RX Monitor (the real decoder, the fakes of
     mon_checks for the FM RAM reads), goes through the hand-off and lands in the inbox with its whole text; stack depth of that pass
  4. the Messages screen: one message per page, newest first, Down to the older one; nothing in it transmits

The RTC (rtc_register_read) and the callsign record (krk_call_get, blob 3) are faked: QEMU has neither a set RTC nor a saved call.
"""
import struct
import sys

from build_artifacts import EXT_TABLE, KRAKEN_TOOLS, krk_define      # first: puts the tool directories on sys.path
import mi_rng_test as m
from claims import claim
from gdb_rsp import read_mem
from qemu_menu import OS_TICKS
from target import fcall, r32, w32

sys.path.insert(0, str(KRAKEN_TOOLS))
import afsk_channel as ch                                            # noqa: E402

RTC_READ = 0x0301C2D4
GPIO_SET, GPIO_CLR, PTT_PIN, STOCK_PRESC = 0x0002C500, 0x0002C544, 0x03012F48, 0x030546DC
LOADFLAGS, CALLACT = 0x0003E31C, 0x0003FDC8
SCRATCH = 0x18006000                  # the shared F9/F13 buffer (idle on the halted machine): half buffers and call arguments
STK_LO, STK_HI = 0x0002F0A0, 0x0002F800
MON_MAGIC, IB_MAGIC = 0x4D4F4E31, 0x4753534D
BASE_DAY = 20400                      # an RTC date (days since 1970) for the fake


def check_msg(c, s, s2, s3, s4, img, dwc):
    rb = lambda a, n=1: read_mem(c, a, n)
    lo, hi = claim("kraken_aprs_inbox")
    IB = lo
    # struct krk_inbox: u32 magic, last; u8 n, sel, view, pad; u32 shown; struct krk_msg m[3] (u32 sec; from[10]; no[6]; text[60])
    n_ = lambda: rb(IB + 8)[0]
    ent = lambda i: (r32(c, IB + 16 + 80 * i), rb(IB + 20 + 80 * i, 10).split(b"\0")[0], rb(IB + 30 + 80 * i, 6).split(b"\0")[0],
                     rb(IB + 36 + 80 * i, 60).split(b"\0")[0])

    # ---- 1. static -------------------------------------------------------------------------------
    print("== F18 APRS Messages: row, claim")
    item, first = krk_define("KRK_APRS_ITEM_MSG"), krk_define("KRK_APRS_FIRST")
    rd32 = lambda a: struct.unpack("<I", img[a - 0x03000000:a - 0x03000000 + 4])[0]
    for lang, base in ((0, 0x030BA924), (1, 0x030BA92C)):
        lab = rd32(rd32(base) + 4 * item)
        m.report(img[lab - 0x03000000:].split(b"\0")[0] == b"Messages" and img[base + 4 - 0x03000000] > item,
                 f"APRS descriptor lang {lang}: item {item} label 'Messages', count {img[base + 4 - 0x03000000]}")
    slot = rd32(EXT_TABLE + 4 * (krk_define("KRK_EXT_MAX") + item - first))
    m.report(slot == s4["krk_aprs_msg_screen"], f"APRS slot of item {item} = krk_aprs_msg_screen ({slot:#x})")
    m.report(hi - lo == 256, f"kraken_aprs_inbox claim {lo:#x}-{hi:#x}: 256 bytes (the struct is 16 + 3 x 80, size-checked at compile time)")

    # ---- harness ---------------------------------------------------------------------------------
    st = {"rtc": BASE_DAY * 86400 + 3600, "call": b"N0CALL-5", "sw": 1, "ptt": 0, "now": 300000}
    tx = []

    def fk_rtc(regs, r0, r1):
        t = st["rtc"]
        return [t % 60, t // 60 % 60, t // 3600 % 24, (t // 86400) & 0xFF, (t // 86400) >> 8][r0] if r0 < 5 else 0

    def fk_call(regs, r0, r1):
        m.wmem(c, r0, st["call"] + b"\0")
        return 1 if st["call"] else 0

    def fk_tx(regs, r0, r1):
        tx.append(regs.get("pc", 0))
        return -1
    fakes = {RTC_READ: fk_rtc, s3["krk_call_get"]: fk_call, s2["krk_fm_send_opt"]: fk_tx, s2["krk_aprs_tx_send"]: fk_tx,
             GPIO_SET: 0, GPIO_CLR: 0, STOCK_PRESC: 0, PTT_PIN: lambda r, a, b: st["ptt"],
             s2["mon_fm_sw"]: lambda r, a, b: st["sw"], s2["mon_half_ptr"]: SCRATCH}

    def frame(line, info, path=("WIDE1-1",)):
        """Blob 2's hand-off krk_aprs_msg_frame(line, f, len) with an AX.25 frame (no FCS) holding info, as emit() passes it."""
        fb = ch.ax25_frame("APDR16", line.split(">")[0], list(path), info)[:-2]
        m.wmem(c, SCRATCH, line.encode() + b"\0")
        m.wmem(c, SCRATCH + 0x100, fb)
        w32(c, OS_TICKS, st["now"])
        _, r0, abi, _ = fcall(c, s2["krk_aprs_msg_frame"], (SCRATCH, SCRATCH + 0x100, len(fb)), fakes=fakes)
        return abi

    # ---- 2. direct injection ---------------------------------------------------------------------
    print("== F18 APRS Messages: the decoder hand-off")
    m.wmem(c, IB, b"\xA5" * (hi - lo))                   # power-up noise
    abi = frame("W1AW-9>APDR16,WIDE1-1", ":N0CALL-5 :Hello from W1AW{12")
    m.report(abi and r32(c, IB) == IB_MAGIC and n_() == 1 and ent(0) == (BASE_DAY * 86400 + 3600, b"W1AW-9", b"12", b"Hello from W1AW"),
             f"noise SRAM, one message for me: inbox initialised, entry {ent(0)}")
    frame("W1AW-9>APDR16", ":N0CALL-5 :ack3")
    frame("W1AW-9>APDR16", ":N0CALL-6 :not for me")
    frame("W1AW-9>APDR16", ":BLN1     :bulletin")
    frame("W1AW-9>APDR16", ":N0CALL-5 :ack12")
    st["rtc"] += 120
    frame("W1AW-9>APDR16,WIDE1-1,WIDE2-1", ":N0CALL-5 :Hello from W1AW{12", ("WIDE1-1", "WIDE2-1"))
    m.report(n_() == 1, f"ack, other SSID, bulletin, and a repeat 2 min later (same sender + number): still one message ({n_()})")
    st["call"] = b""
    frame("K1ABC>APRS", ":N0CALL-5 :no call saved")
    st["call"] = b"N0CALL-5"
    m.report(n_() == 1, "no callsign saved: nothing stored")
    m.report(not tx, "no send function was reached")

    # ---- 3. end to end through the RX Monitor --------------------------------------------------------
    print("== F18 APRS Messages: a synthesized message frame through the RX Monitor's decoder into the inbox")
    info = ":N0CALL-5 :Test message from QEMU, longer than the sixty-character line{7"
    tones = ch.ax25_tones(ch.ax25_frame("APDR16", "K1ABC-7", ["WIDE1-1", "WIDE2-1"], info), 20, 3)
    smp = [int(v * 3000) for v in ch.cpfsk(tones)]
    smp += [0] * ((-len(smp)) % 256)
    import random
    rnd = random.Random(11)
    noise = [max(-32768, min(32767, int(rnd.gauss(0, 1000)))) for _ in range(256 * 30)]
    halves = [b"".join(struct.pack(">h", v) for v in x[k:k + 256]) for x in (noise, smp) for k in range(0, len(x), 256)]
    w32(c, s["krk_screen_owner"], 0)
    w32(c, s2["tx_busy"], 0)
    m.wmem(c, CALLACT, b"\0")
    m.wmem(c, LOADFLAGS, b"\0")
    dwc.chan_poke(c, b"\x00")
    w32(c, OS_TICKS, st["now"])
    fcall(c, s2["krk_mon_set"], (1,), fakes=fakes)
    deepest = 0
    for _ in range(5):                                   # settle and arm
        st["now"] += 1
        w32(c, OS_TICKS, st["now"])
        fcall(c, s2["krk_mon_tick"], fakes=fakes)
    armed = rb(s2["M"] + 16)[0] == 1
    for h in halves:
        m.wmem(c, SCRATCH, h)
        st["sw"] = 2 if st["sw"] == 1 else 1
        st["now"] += 3
        w32(c, OS_TICKS, st["now"])
        m.wmem(c, STK_LO, b"\xA5" * (STK_HI - STK_LO))
        fcall(c, s2["krk_mon_tick"], fakes=fakes)
        sk = rb(STK_LO, STK_HI - STK_LO)
        deepest = max(deepest, STK_HI - STK_LO - next((j for j, b in enumerate(sk) if b != 0xA5), STK_HI - STK_LO))
    m.report(armed and n_() == 2 and ent(0)[1:] == (b"K1ABC-7", b"7", info[11:-2].encode()[:59]),
             f"monitor armed, {len(halves)} halves decoded: newest entry {ent(0)[1:]}")
    m.report(ent(1)[1] == b"W1AW-9", "  the older message is second")
    print(f"     deepest stack of a monitor pass that decodes a message frame: {deepest} bytes")
    m.report(deepest <= 900, f"stack depth {deepest} <= 900 bytes (task init: 2880 B stack, 124 B in use at its OSFlagPend)")
    fcall(c, s2["krk_mon_set"], (0,), fakes=fakes)

    # ---- 4. the screen -----------------------------------------------------------------------------
    print("== F18 APRS Messages: the Messages screen")
    rows = {}

    def fk_line(regs, r0, r1):
        rows[r0] = rb(r1, 24).split(b"\0")[0]
        return 0
    fs = dict(fakes)
    fs[s["krk_line"]] = fk_line
    st["rtc"] += 300
    w32(c, OS_TICKS, st["now"])
    fcall(c, s4["msg_draw"], fakes=fs)
    m.report(rows.get(0) == b"Messages 1/2" and rows.get(1, b"").startswith(b"K1ABC-7 ") and rows.get(2) == b"Test message from Q"
             and rows.get(5) == b"in" and rows.get(6) == b"" and len(rows) == 10, f"page 1, the newest (59 characters over rows 2-5): {[rows.get(i) for i in range(7)]}")
    m.report(rows.get(1, b"").endswith(b"5m"), f"  age from the RTC: {rows.get(1)!r}")
    rows.clear()
    fcall(c, s4["msg_tick"], (0x05,), fakes=fs)
    m.report(rows.get(0) == b"Messages 2/2" and rows.get(1, b"").startswith(b"W1AW-9") and rows.get(2) == b"Hello from W1AW" and rows.get(3) == b"",
             f"Down: the older message {[rows.get(i) for i in range(7)]}")
    m.wmem(c, IB, b"\xA5" * (hi - lo))
    rows.clear()
    fcall(c, s4["msg_draw"], fakes=fs)
    m.report(rows.get(2) == b"No messages", f"noise inbox: 'No messages' {rows.get(2)!r}")
    st["call"] = b""
    rows.clear()
    fcall(c, s4["msg_draw"], fakes=fs)
    m.report(rows.get(2) == b"Set callsign first", f"no callsign: {rows.get(2)!r}")
    st["call"] = b"N0CALL-5"
    m.report(not tx, "no send function was reached by the screen or the decoder")
