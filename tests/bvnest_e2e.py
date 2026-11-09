#!/usr/bin/env python3
"""Drive the Burst Voice nest under stock Channel Set end to end (docs/chanset-submenu.md 8, docs/burst-voice.md 13).

    Menu > Settings > Channel Set > Burst Voice > Burst Voice     the screen (blob ui4)
                                                > Mode            Fast / Robust
                                                > Key-up Lead     0 .. 960 ms, eight values
                                                > Relay Capture   ON / OFF

The parent is Channel Set item 23. Its level-4 init and OK are its own entries in the two relocated Channel Set tables; level 5
(a child's option level, or the screen) is reached through three splices in Channel Set's level-5 init, OK and header
(krk_bvcfg.c, krk_ofstubs.S, hooks.json). On the analog boot channel this checks:

  list     the Channel Set list carries the Burst Voice parent (item 23) with its label
  kids     OK on it opens the child list: depth 4, mode 0x1c, four numbered rows, sequential index list
  open     each setting child opens a stock option level (depth 5, mode 0x23, radio marks) with the default marked
  commit   OK there stores the setting: the record handed to stock flash_block_write(4, 0xFC0, rec, 16) (a breakpoint there; the
           model keeps the data chip for the run but cannot write it back), the child list back with the cursor on the child,
           and the child reopened with the mark on the new value (its get() reads the record back through flash_block_read)
  back     Back from the reopened option level returns to the child list with the cursor kept
  screen   the screen child runs the screen, which reads the stored settings (S.mode / S.lead / S.rly_want; relay capture ON
           installs no sink on an analog channel), and Back returns to the child list with the cursor on it

One boot per setting walks all of that in one key sequence (three in parallel, plus one that finds the row): every step that is
checked is followed by a long gap, and each check waits for a state the previous one cannot satisfy. The checks keep the stock
Menu Exit Time counter at 0 (docs/menu-exit-timer.md), so a long gap does not drop the radio to standby.

Run inside the dm32uv-re image, like the other in-target tests:
  python3 /w/dm32uv-unleashed/tests/bvnest_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
tests/kraken_ui_test.py runs it in process (run()) in its booted section.
"""
import functools
import pathlib
import sys
import threading

from build_artifacts import elf, syms, krk_define      # first: puts the tool directories on sys.path
from qemu_menu import CELL_COUNT, L3_LIST, L3_ROW, PATHS, Boot, Suite
from qemu_menu import keys as _keys

keys = functools.partial(_keys, fast_gap=110)           # a dozen Downs to the row: see qemu_menu.keys

BV = krk_define("KRK_CS_ITEM_BV")
KIDS = ["Burst Voice", "Mode", "Key-up Lead", "Relay Capture"]
MODES = ["Fast", "Robust"]
LEADS = ["0 ms", "128 ms", "192 ms", "256 ms", "320 ms", "480 ms", "640 ms", "960 ms"]
RELAY = ["ON", "OFF"]
S_MODE, S_LEAD, S_RLY_WANT, S_RELAY = 49, 50, 73, 52    # krk_bvscr.c S (tests/f13_checks.py S_OFF)
VF_RX_SINK = 20                                         # krk_vframe.h: the RX sink pointer
SCR_MAGIC = 0x53435252                                  # krk_screen_owner while a blocking screen runs
MENU_EXIT_COUNTER = 0x3E396
KEYSEQ_MAX = 32                                         # dm32_gpio.c DM32_KEYSEQ_MAX: later steps are dropped without a word
FLASH_BLOCK_WRITE = 0x0300F878                          # stock flash_block_write(logical block, offset, src, len) (krk_stock.h)
G = 2500                                                # scans (~10 s guest) after every key whose result is checked


def rec_bytes(mode, lead, relay):
    """krk_bvcfg.c struct bvc_rec as flash holds it: magic "BVC1", mode, lead, relay, eight zero bytes, checksum (seed 0x3c)."""
    r = b"BVC1" + bytes([mode, lead, relay]) + bytes(8)
    x = 0x3C
    for byte in r:
        x = (((x << 1) | (x >> 7)) & 0xFF) ^ byte
    return r + bytes([x])


def level(b):
    c = b.cells()
    return c, [x.decode(errors="replace") for x in b.labels_of(c["count"])]


def alive(pred):
    """pred, with the stock Menu Exit Time counter zeroed on every poll (the long gaps would otherwise reach its limit)."""
    def p(bb):
        bb.poke(MENU_EXIT_COUNTER, b"\0")
        return pred(bb)
    return p


def in_kids(cur):
    return alive(lambda bb: bb.at_depth(4, 1) and bb.u8(CELL_COUNT) == 4 and bb.u8(0x3E5CC) == cur)


def in_level(cur=None):
    return alive(lambda bb: bb.at_depth(5, 2) and (cur is None or bb.u8(0x3E5CC) == cur))


def find_row(patched, report):
    b = Boot(patched, "bv_list", keys(PATHS["cs"]), settle=140)
    try:
        ok = b.until(lambda bb: bb.at_depth(3, 1), timeout=90)
        b.wait_list(BV + 1, timeout=30)                 # a row after ours: only there once the generator is done
        lst = b.read_list()
        lab = b.labels_of(BV + 1)[BV]
        good = ok and BV in lst and lab == b"Burst Voice"
        report(good, f"Channel Set list shows the Burst Voice parent: item {BV} at row "
                     f"{lst.index(BV) if BV in lst else None} of {lst}, label {lab!r}")
        return lst.index(BV) if good else None
    finally:
        b.close()


def walk(patched, rep, row, child, labels, default, target, rec, syms_):
    """One boot: the child list, the child's option level (default marked), the commit (record + child list), the level
    reopened (new mark), Back (cursor kept), the screen (reads the setting), Back. At most 32 keys: the model's DM32_KEYSEQ_MAX
    (dm32_gpio.c) silently drops the rest (a 33-key walk never pressed its last Back, 2026-11-06)."""
    s1, s2, s4 = syms_
    owner, S, sink = s1["krk_screen_owner"], s4["S"], s2["krk_vf"] + VF_RX_SINK
    name = KIDS[child]
    move = ("down " * (target - default) if target > default else "up " * (default - target)).strip()
    seq = (f"{PATHS['cs']} {'down ' * row}ok:{G} {'down ' * child}ok:{G} {move} ok:{G} ok:{G} back:{G} "
           f"{'up ' * child}ok:{G} back:{G}")
    if len(seq.split()) > KEYSEQ_MAX:
        rep(False, f"{name}: the walk is {len(seq.split())} keys, the model presses at most {KEYSEQ_MAX}")
        return
    writes = []

    def on_write(bb):                                   # stopped at flash_block_write: keep what is written to block 4 0xFC0
        r = bb.regs()
        if r["r0"] == 4 and r["r1"] == 0xFC0:
            writes.append(bb.mem_halted(r["r2"], r["r3"]))
    b = Boot(patched, f"bv_{child}", keys(" ".join(seq.split())), bp=FLASH_BLOCK_WRITE, settle=300, on_hit=on_write)
    try:
        ok = b.until(in_kids(0), timeout=180)
        c, labs = level(b)
        l3 = b.u8(L3_ROW)
        if child == 1:
            rep(ok and c["mode"] == 0x1C and c["sub"] == 2 and labs == KIDS and b.read_list() == [0, 1, 2, 3]
                and b.u8(L3_LIST + l3) == BV,
                f"OK on Burst Voice (level-3 row {l3} -> item {b.u8(L3_LIST + l3)}) opens its child list: depth 4, mode 0x1c, "
                f"numbered rows {labs}, list {b.read_list()} {c}")
        elif not ok:
            rep(False, f"{name}: the child list did not open {c}")
            return
        ok = b.until(in_level(), timeout=60)
        c, labs = level(b)
        rep(ok and c["mode"] == 0x23 and c["sub"] == 2 and labs == labels and c["cur"] == default and c["dot"] == default,
            f"Burst Voice > {name}: stock option level at depth 5, mode 0x23, radio marks {labs}, default {labels[default]!r} "
            f"marked (cursor {c['cur']}, dot {c['dot']}) {c}")
        stored = b.until(alive(lambda bb: rec in writes), timeout=90)
        back = b.until(in_kids(child), timeout=60)
        c = b.cells()
        rep(stored and len(writes) == 1,
            f"{name} = {labels[target]!r}: OK wrote the record {rec.hex()} to block 4 0xFC0 "
            f"(flash_block_write calls there: {[w.hex() for w in writes]})")
        rep(back, f"{name}: after \"Setting is OK\" the child list is back with the cursor on {name} {c}")
        again = b.until(in_level(), timeout=60)
        c, labs = level(b)
        rep(again and labs == labels and c["cur"] == target and c["dot"] == target,
            f"{name} reopened: the mark is on {labels[target]!r} (cursor {c['cur']}, dot {c['dot']}; get() read flash) {c}")
        ok = b.until(in_kids(child), timeout=60)
        c, labs = level(b)
        rep(ok and c["mode"] == 0x1C and labs == KIDS, f"{name}: Back from its option level returns to the child list with the "
                                                       f"cursor kept on {name} {c}")
        want = {1: (S_MODE, 0), 2: (S_LEAD, 15), 3: (S_RLY_WANT, 1)}[child]
        # the runner sets krk_screen_owner BEFORE enter() reads the record: wait for the value, not only for the owner
        run = b.until(alive(lambda bb: bb.u32(owner) == SCR_MAGIC and bb.mem(S, 76)[want[0]] == want[1]), timeout=90)
        st = b.mem(S, 76)
        rx = b.u32(sink)
        rep(run and st[want[0]] == want[1] and (child != 3 or (st[S_RELAY] == 0 and rx == 0)),
            f"{name}: the Burst Voice screen runs (child 0) and reads the setting: mode {st[S_MODE]}, lead {st[S_LEAD]} x 32 ms, "
            f"relay wanted {st[S_RLY_WANT]}, sink installed {st[S_RELAY]} (rx_sink {rx:#x}; analog channel: none)")
        ok = b.until(alive(lambda bb: bb.u32(owner) == 0 and in_kids(0)(bb)), timeout=90)
        c, labs = level(b)
        rep(ok and c["mode"] == 0x1C and labs == KIDS, f"{name}: Back from the screen returns to the child list on Burst Voice {c}")
    finally:
        b.close()


WALKS = [  # child, labels, default index, target index, the record after the commit
    (1, MODES, 0, 1, rec_bytes(0, 10, 0)),
    (2, LEADS, 4, 5, rec_bytes(1, 15, 0)),
    (3, RELAY, 1, 0, rec_bytes(1, 10, 1)),
]


def run(patched, report=None, only=None):
    """The checks, callable in process from tests/kraken_ui_test.py (which passes its own report). only: child numbers."""
    suite = Suite(report)
    row = find_row(patched, suite)
    if row is None:
        suite(False, "Burst Voice parent not found in the Channel Set list: the level checks would be meaningless")
        return 1
    syms_ = (syms(elf(1)), syms(elf(2)), syms(elf(4)))
    walks = [w for w in WALKS if not only or w[0] in only]
    recs = {w[0]: [] for w in walks}

    def one(w):
        rep = lambda ok, msg: recs[w[0]].append((ok, msg))
        try:
            walk(patched, rep, row, *w, syms_)
        except Exception as e:                          # a crashed walk is a failure, not a hang
            rep(False, f"Burst Voice > {KIDS[w[0]]}: walk error {type(e).__name__}: {e}")
    th = [threading.Thread(target=one, args=(w,)) for w in walks]
    for x in th:
        x.start()
    for x in th:
        x.join()
    for w in walks:
        for ok, msg in recs[w[0]]:
            suite(ok, msg)
    return 1 if suite.fails else 0


def main():
    suite = Suite()
    run(pathlib.Path(sys.argv[1]), suite, only=[int(x) for x in sys.argv[2:]] or None)   # e.g. `3`: the Relay Capture walk only
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
