"""F8a analog APRS beacon text (krk_aprs_text.c in blob ui2, krk_aprs_trec.c and krk_tapedit.c in blob ui3; GitHub issue #3):
halted-machine checks of the built image, called from tests/kraken_ui_test.py.

  1. The row: APRS item 13 is the Beacon Text editor in the extension table, labelled "Beacon Text" (the row opening it through
     the runner is optrow_checks.check_aprs_path).
  2. The editor in the image: enter over a vendor-CPS-blank record (FF), type "N0 W" with the keypad, OK: exactly one
     flash_block_write(4, 0xF90, rec, 48) with magic "BXT1", the text zero-padded and its checksum; "Saved" on line 5. OK again:
     no write. The editor reopens with the saved text.
  3. The packet: krk_aprs_tx_send with the radio gates faked open builds its frame from the saved record: the status packet
     ">N0 W no GPS fix" without a fix, the position packet "!...[N0 W" with one; a blank record gives the default
     "DM-32UV Kraken". The send is stopped at krk_ax25_tones (it returns "too long"), so nothing keys.
Stock calls are faked by their 048 addresses (the L01/049 copies of this file are translated by tools/fw_translate.py).
"""
import struct

from build_artifacts import EXT_TABLE, krk_define
import mi_rng_test as m
from gdb_rsp import read_mem
from target import cstr, fcall, r32, w32

RESOLVE, BREAD, BWRITE = 0x0300F594, 0x0300F71C, 0x0300F878     # flash_block_id_resolve_locked, flash_block_read, flash_block_write
EQ_RESET, TX_INH, RX_ACTIVE = 0x03054D80, 0x0302ED50, 0x0300F2A4
CALL_ACTIVE, PSAVE = 0x0003FDC8, 0x0003EBF0
SCRATCH = 0x18006000                    # a half buffer of the (idle here) shared F9/F13 buffer
SCAN_OK, SCAN_0, SCAN_1 = 0x03, 0x0F, 0x10


def rec(text):
    r = b"BXT1" + text.ljust(43, b"\0")
    x = 0x69
    for byte in r:
        x = (((x << 1) | (x >> 7)) & 0xFF) ^ byte
    return r + bytes([x])


def check_aprs_text(c, s, s2, s3, dwc):
    print("== F8a APRS beacon text: the row, the editor's save, the packet")
    na = krk_define("KRK_APRS_MAX")
    item = krk_define("KRK_APRS_ITEM_TEXT")
    slot = struct.unpack("<I", read_mem(c, EXT_TABLE + 4 * (krk_define("KRK_EXT_MAX") + item - 8), 4))[0]
    scr = s2["krk_aprs_text_screen"]
    label, enter, tick = struct.unpack("<III", read_mem(c, scr, 12))
    m.report(item - 8 < na and slot == scr and cstr(c, label) == b"Beacon Text" and enter and tick,
             f"APRS item {item}: extension slot -> krk_aprs_text_screen {slot:#x}, label {cstr(c, label)!r}, a live screen")

    flash = {"rec": b"\xff" * 48, "writes": []}
    lines = {}

    def fread(regs, r0, r1):
        m.wmem(c, regs["r2"], flash["rec"][:regs["r3"]] if r1 == 0xF90 else b"\xff" * regs["r3"])
        return 0

    def fwrite(regs, r0, r1):
        data = read_mem(c, regs["r2"], regs["r3"])
        flash["writes"].append((r0, r1, regs["r3"], data))
        if r0 == 4 and r1 == 0xF90:
            flash["rec"] = data
        return 0

    def fline(regs, r0, r1):
        lines[r0] = cstr(c, r1)
        return 0
    fk = {RESOLVE: 0x44, BREAD: fread, BWRITE: fwrite, s["krk_line"]: fline}
    _, _, abi, _ = fcall(c, enter, fakes=fk)
    m.report(abi and lines.get(1) == b"_" and lines.get(0) == b"Beacon text", f"enter over an FF record: empty text, cursor only {lines.get(1)!r}")
    keys = [SCAN_1 + 5] * 2 + [0] * 12 + [SCAN_0] * 2 + [0] * 12 + [SCAN_0] + [0] * 12 + [SCAN_1 + 8] + [0] * 12 + [SCAN_OK]
    for k in keys:                                  # 6 6 = N, 0 0 = 0, 0 = space, 9 = W, OK (0 = an idle keypad tick, 12 = 1.2 s)
        fcall(c, tick, (k,), fakes=fk)
    w = flash["writes"]
    m.report(len(w) == 1 and w[0][:3] == (4, 0xF90, 48) and w[0][3] == rec(b"N0 W") and lines.get(5) == b"Saved",
             f"typed 'N0 W', OK: one flash_block_write(4, 0xF90, rec, 48) {[(a, hex(b), n) for a, b, n, _ in w]}, record {w[0][3][:10] if w else b''!r}, status {lines.get(5)!r}")
    fcall(c, tick, (SCAN_OK,), fakes=fk)
    m.report(len(flash["writes"]) == 1 and lines.get(5) == b"Saved", "OK again, text unchanged: no second write")
    lines.clear()
    fcall(c, enter, fakes=fk)
    m.report(lines.get(1) == b"N0 W_", f"the editor reopens with the saved text {lines.get(1)!r}")

    # the packet, from the record, through the real build_frame; stopped before anything keys
    frames = []

    def ftones(regs, r0, r1):
        frames.append(read_mem(c, r0, r1))
        return 0xFFFFFFFF                           # "Frame too long": the send ends here
    fix = struct.pack("<ii12s12sbb8s", 0, 0, b"4142.8500", b"07243.6000", ord("N"), ord("W"), b"")

    def fgps(on):
        def f(regs, r0, r1):
            if on:
                m.wmem(c, r0, fix)
            return 1 if on else 0
        return f

    def fcall_get(regs, r0, r1):
        m.wmem(c, r0, b"N0CALL\0")
        return 1

    def info(frame):
        i = next(k for k, b in enumerate(frame) if b & 1) + 1
        return frame[i + 2:-2] if frame[i:i + 2] == b"\x03\xf0" else None

    def send(gps):
        del frames[:]
        dwc.chan_poke(c, b"\x00")
        w32(c, s2["tx_busy"], 0)
        m.wmem(c, CALL_ACTIVE, bytes([0]))
        m.wmem(c, PSAVE + 1, bytes([0]))
        m.wmem(c, SCRATCH, bytes(8))
        fks = dict(fk)
        fks.update({EQ_RESET: 0, TX_INH: 0, RX_ACTIVE: 0, s2["krk_mon_yield"]: 0, s2["krk_on_selected_side"]: 1,
                    s3["krk_call_get"]: fcall_get, s3["krk_aprs_cfg_path"]: 0, s["krk_gps_get"]: fgps(gps), s2["krk_ax25_tones"]: ftones})
        _, r0, abi, _ = fcall(c, s2["krk_aprs_tx_send"], (SCRATCH, SCRATCH + 4), fakes=fks)
        return abi, r0, cstr(c, r32(c, SCRATCH)), [info(f) for f in frames]
    abi, r0, why, got = send(False)
    m.report(abi and why == b"Frame too long" and got == [b">N0 W no GPS fix"], f"no fix: the status packet carries the text {got}, send stopped ({why!r})")
    abi, r0, why, got = send(True)
    m.report(abi and got == [b"!4142.85N/07243.60W[N0 W"], f"a fix: the position packet's comment is the text {got}")
    flash["rec"] = b"\xff" * 48
    abi, r0, why, got = send(True)
    m.report(abi and got == [b"!4142.85N/07243.60W[DM-32UV Kraken"], f"FF record (vendor CPS write): the default comment {got}")
    flash["rec"] = bytes(48)
    abi, r0, why, got = send(False)
    m.report(abi and got == [b">DM-32UV Kraken no GPS fix"], f"00 record (factory reset): the default status {got}")
    m.report(r32(c, s2["tx_busy"]) == 0, "tx_busy released after the stopped sends")

