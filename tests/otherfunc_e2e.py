#!/usr/bin/env python3
"""Drive the Other Func nest end to end (docs/otherfunc-submenu.md).

    Menu > Settings > Radio Settings > Other Func > VOX      > VOX SW, Gain Level, VOX Delay
                                                  > CW ID    > CW ID SW, Interval, Callsign

Stock nests VOX natively; the patch adds a third VOX child (data plus two level-6 arms), turns the
never-listed Other Func item 11 into a second parent, and takes the VOX Delay row (item 4) out of the
Other Func list. This walks every new level in QEMU and checks the cells the stock renderer reads, the
value each commit stores, and that stock's own two VOX children still open.

CW ID (2026-11-05, docs/cw-id.md) is item 11, listed on analog channels only (the boot channel is analog)
in the place of item 4: its two option children store into the callsign record's spare byte, its Callsign
child runs the shared editor screen and Back returns to the child list, and its scheduler sends at standby
but not in a menu. Item 10 (Lone Worker) is stock's again: never listed, its slots untouched.

It also covers the stale-latch fix in krk_settings_opt_ok: an option row of ours under Radio Settings
that was opened and left must not claim the OK pressed later on a stock Other Func row.

Run inside the dm32uv-re image, like the other in-target tests:
  python3 /w/dm32uv-unleashed/tests/otherfunc_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
"""
import functools
import pathlib
import struct
import sys

from build_artifacts import elf, syms      # first: puts the tool directories on sys.path
from qemu_menu import CELL_COUNT, KEY, OS_TICKS, PATHS, UI_MODE, Boot, Suite
from qemu_menu import keys as _keys

keys = functools.partial(_keys, fast_gap=110)

# Menu > Settings (one Down from Scan) > OK > Radio Settings (first row) > OK > Other Func (row 3) > OK
TO_OF = PATHS["of"]
TO_RS = PATHS["rs"]
VOX_GAIN, VOX_DELAY = 0x0003EA12, 0x0003EA13
DELAYS = [3, 5, 10, 15, 20, 30, 40, 50]
DELAY_LABELS = [b"0.3 s", b"0.5 s", b"1.0 s", b"1.5 s", b"2.0 s", b"3.0 s", b"4.0 s", b"5.0 s"]
# Other Func item 10 (Lone Worker): its four per-item slots and their stock values (zh / en label, level-5 init / OK)
ITEM10_SLOTS = ((0x030B8688, 0x030BD024), (0x030B86DC, 0x030BD124), (0x030B09B8, 0x03045082), (0x030B1334, 0x030500A4))
CW_KIDS = ["CW ID SW", "Interval", "Callsign"]
CW_MINS = [1, 2, 3, 5, 10, 15, 20, 30]
CW_MAGIC, SCR_MAGIC = 0x43574944, 0x53435252


def boot(patched, tag, seq):
    return Boot(patched, tag, keys(seq), settle=140)


def level(b):
    c = b.cells()
    return c, [x.decode(errors="replace") for x in b.labels_of(c["count"])]


def main():
    patched = pathlib.Path(sys.argv[1])
    s2 = syms(elf(2))
    report = Suite()
    fails = report.fails

    # 1. the Other Func list: item 4 gone, CW ID (item 11) in its place; item 10 never listed, its slots stock
    b = boot(patched, "of_list", TO_OF)
    try:
        ok = b.until(lambda bb: bb.at_depth(4, 1), timeout=120)
        b.wait_list(3, timeout=30)
        lst = b.read_list()
        labs = b.labels_of(21)
        report(ok and 4 not in lst and 10 not in lst and lst[:5] == [0, 1, 2, 3, 11] and lst.count(11) == 1,
               f"Other Func list on the analog boot channel: VOX Delay (4) is gone and CW ID (11) sits right after VOX: {lst}")
        report(labs[3] == b"VOX" and labs[11] == b"CW ID",
               f"label array: item 3 {labs[3]!r}, item 10 {labs[10]!r} (stock), item 11 {labs[11]!r}")
        got = [b.u32(a) for a, _ in ITEM10_SLOTS]
        report(got == [v for _, v in ITEM10_SLOTS],
               f"item 10 (Lone Worker) slots are stock's: {[hex(v) for v in got]}")
    finally:
        b.close()
    if fails:
        print("\nFAILURES: the Other Func list is wrong; the level checks would be meaningless")
        return 1
    row_vox = lst.index(3)
    to_vox = TO_OF + " " + "down " * row_vox + "ok"

    # 2. the VOX child list
    b = boot(patched, "vox_kids", to_vox)
    try:
        ok = b.until(lambda bb: bb.at_depth(5, 1) and bb.u8(CELL_COUNT) == 3, timeout=120)
        c, labs = level(b)
        report(ok and c["mode"] == 0x23 and labs == ["VOX SW", "Gain Level", "VOX Delay"] and b.read_list() == [0, 1, 2],
               f"vox_kids: child list at depth 5, mode 0x23, numbered rows {labs}, list {b.read_list()} {c}")
    finally:
        b.close()

    # 3. stock's own VOX SW child still opens (the level-6 hooks fall back into stock's arm)
    b = boot(patched, "vox_sw", to_vox + " ok")
    try:
        ok = b.until(lambda bb: bb.at_depth(6, 2), timeout=120)
        c, labs = level(b)
        report(ok and c["mode"] == 0x2A and labs == ["ON", "OFF"],
               f"VOX > VOX SW: stock's option level at depth 6, mode 0x2a, radio marks {labs} {c}")
    finally:
        b.close()

    # 4. VOX > VOX Delay: our eight-row level, and its commit stores the delay and nothing else
    b = boot(patched, "vox_delay", to_vox + " down down ok")
    try:
        ok = b.until(lambda bb: bb.at_depth(6, 2), timeout=120)
        c, labs = level(b)
        d0, g0 = b.mem(VOX_DELAY, 1)[0], b.mem(VOX_GAIN, 1)[0]
        near = min(range(8), key=lambda i: abs(DELAYS[i] - d0))
        report(ok and c["mode"] == 0x2A and c["count"] == 8 and labs == [x.decode() for x in DELAY_LABELS]
               and c["cur"] == near,
               f"VOX > VOX Delay: depth 6, mode 0x2a, radio marks, {labs}, cursor {c['cur']} (stored {d0}, nearest {near}) {c}")
        start = c["cur"]
    finally:
        b.close()
    target = (start + 1) % 8
    b = boot(patched, "vox_delay_set", to_vox + " down down ok down ok")
    try:
        done = b.until(lambda bb: bb.mem(VOX_DELAY, 1)[0] == DELAYS[target], timeout=160)
        back = b.until(lambda bb: bb.at_depth(5, 1) and bb.u8(CELL_COUNT) == 3, timeout=90)
        c, labs = level(b)
        report(done and b.mem(VOX_GAIN, 1)[0] == g0,
               f"VOX Delay commit: delay {b.mem(VOX_DELAY, 1)[0]} (want {DELAYS[target]}), Gain Level untouched "
               f"({b.mem(VOX_GAIN, 1)[0]}, was {g0})")
        report(back and labs == ["VOX SW", "Gain Level", "VOX Delay"] and c["cur"] == 2,
               f"after the popup the VOX child list is back with the cursor on VOX Delay: {labs} {c}")
    finally:
        b.close()

    # 5. stale latch: open TX Power Trim (Radio Settings row 4), Back, Up to Other Func, OK, OK on
    # Auto PowerOff. Stock must open Auto PowerOff's level; the old code committed TX Power Trim instead.
    b = boot(patched, "latch", TO_RS + " down down down down ok back up ok ok")
    try:
        ok = b.until(lambda bb: bb.at_depth(5, 2), timeout=160)
        c, labs = level(b)
        report(ok and c["mode"] == 0x23,
               f"stale latch: after visiting TX Power Trim, OK on Other Func > Auto PowerOff opens its option level "
               f"(depth 5, mode 0x23, radio marks) {labs} {c}")
    finally:
        b.close()

    # 6. CW ID
    s1, s3 = syms(elf(1)), syms(elf(3))
    check_cwid(patched, report, TO_OF + " " + "down " * lst.index(11) + "ok", s1, s2, s3)

    return report.finish()


def spare(b, s3):
    return b.mem(s3["cache"], 16)[14]       # krk_callsign.c call_rec: the CW ID byte (bit 7 on, bits 6-0 minutes)


def call_rec(call, ssid, path, cfg):
    """krk_callsign.c struct call_rec with its checksum."""
    r = struct.pack("<I", 0x4C4C4143) + call.encode().ljust(7, b"\0") + bytes([ssid, 1, path, cfg])
    x = 0x5A
    for byte in r:
        x = (((x << 1) | (x >> 7)) & 0xFF) ^ byte
    return r + bytes([x])


def check_cwid(patched, report, to_cw, s1, s2, s3):
    # the child list
    b = boot(patched, "cw_kids", to_cw)
    try:
        ok = b.until(lambda bb: bb.at_depth(5, 1) and bb.u8(CELL_COUNT) == 3, timeout=120)
        c, labs = level(b)
        report(ok and c["mode"] == 0x23 and labs == CW_KIDS and b.read_list() == [0, 1, 2],
               f"CW ID: child list at depth 5, mode 0x23, rows {labs}, list {b.read_list()} {c}")
    finally:
        b.close()
    # CW ID SW: ON / OFF with the mark on OFF; Up + OK stores On + the default 10 min, then the warning
    # popup (no callsign yet) and the child list again
    b = boot(patched, "cw_sw", to_cw + " ok")
    try:
        ok = b.until(lambda bb: bb.at_depth(6, 2), timeout=120)
        c, labs = level(b)
        report(ok and c["mode"] == 0x2A and labs == ["ON", "OFF"] and c["cur"] == 1,
               f"CW ID > CW ID SW: depth 6, mode 0x2a, radio marks {labs}, cursor on OFF {c}")
    finally:
        b.close()
    b = boot(patched, "cw_sw_on", to_cw + " ok up ok")
    try:
        done = b.until(lambda bb: spare(bb, s3) == 0x8A, timeout=160)
        back = b.until(lambda bb: bb.at_depth(5, 1) and bb.u8(CELL_COUNT) == 3, timeout=90)
        c, labs = level(b)
        report(done, f"CW ID SW ON: spare byte {spare(b, s3):#x} (want 0x8a: on, 10 min)")
        report(back and labs == CW_KIDS and c["cur"] == 0,
               f"after the popups the CW ID child list is back on CW ID SW: {labs} {c}")
    finally:
        b.close()
    # Interval: eight times with the mark on the default 10 min; Down + OK stores 15, the switch stays off
    b = boot(patched, "cw_min", to_cw + " down ok")
    try:
        ok = b.until(lambda bb: bb.at_depth(6, 2), timeout=120)
        c, labs = level(b)
        report(ok and c["mode"] == 0x2A and labs == [f"{m} min" for m in CW_MINS] and c["cur"] == 4,
               f"CW ID > Interval: depth 6, mode 0x2a, radio marks {labs}, cursor on 10 min {c}")
    finally:
        b.close()
    b = boot(patched, "cw_min_set", to_cw + " down ok down ok")
    try:
        done = b.until(lambda bb: spare(bb, s3) == 15, timeout=160)
        report(done, f"Interval commit: spare byte {spare(b, s3):#x} (want 0x0f: off, 15 min)")
    finally:
        b.close()
    # Callsign: the editor screen runs from the level-6 init; Back returns to the CW ID child list
    seq = keys(to_cw + " down down ok")
    seq += ";" + KEY["back"].format(g=400)
    seq = seq[:seq.rindex(":")] + ":4000;" + seq[seq.rindex(";") + 1:]   # 4 s on the editor before Back
    b = Boot(patched, "cw_call", seq, settle=140)
    try:
        owner = lambda bb: struct.unpack("<I", bb.mem(s1["krk_screen_owner"], 4))[0]
        opened = b.until(lambda bb: owner(bb) == SCR_MAGIC, timeout=160)
        report(opened, f"CW ID > Callsign: OK runs the callsign editor (screen owner {owner(b):#x})")
        back = b.until(lambda bb: owner(bb) == 0 and bb.at_depth(5, 1) and bb.u8(CELL_COUNT) == 3,
                       timeout=90)
        c, labs = level(b)
        report(back and c["mode"] == 0x23 and labs == CW_KIDS and c["cur"] == 2,
               f"Back from the editor: CW ID child list at depth 5, mode 0x23, cursor on Callsign: {labs} {c}")
    finally:
        b.close()
    # the scheduler, booted: callsign N0CALL, On / 1 min, timer made due. At standby it must reach
    # krk_fm_send; with the top menu open it must not (the gate is ui_in_menu, not 'mode byte == 0')
    for tag, seq, want in (("standby", "lead", True), ("menu", "lead menu", False)):
        b = Boot(patched, f"cw_sched_{tag}", keys(seq), bp=s2["krk_fm_send"], settle=140)
        try:
            booted = b.until(lambda bb: struct.unpack("<I", bb.mem(s2["CW"], 4))[0] == CW_MAGIC, timeout=120)
            if not want:
                b.until(lambda bb: 7 <= bb.mem(UI_MODE, 1)[0] <= 0x3F, timeout=30)
            now = struct.unpack("<I", b.mem(OS_TICKS, 4))[0]
            b.poke(s3["cache"], call_rec("N0CALL", 0, 0, 0x81))
            b.poke(s3["cache_state"], struct.pack("<I", 0x43414348))
            b.poke(s2["CW"] + 4, struct.pack("<I", (now - 61 * 100) & 0xFFFFFFFF))
            b.pump(4.0)
            hit = b.hits > 0
            report(booted and hit == want,
                   f"CW ID scheduler at {tag} (screen-mode byte {b.mem(UI_MODE, 1)[0]:#x}): a due ID "
                   f"{'reached' if hit else 'did not reach'} krk_fm_send ({b.hits} hits; want {'a send' if want else 'none'})")
        finally:
            b.close()


if __name__ == "__main__":
    sys.exit(main())
