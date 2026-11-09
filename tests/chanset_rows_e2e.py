#!/usr/bin/env python3
"""Drive the Scrambler and Parrot rows of the stock Channel Set menu end to end (docs/chanset-submenu.md).

Scrambler and Parrot moved out of the KRAKEN list into stock Channel Set on 2026-11-03, as nine flat rows with the
user-facing labels the nested layout asked for:

    Menu > Settings > Channel Set > ... > Scrambler On/Off, Key, Parrot On/Off, Replay Audio, Check, Clear Audio, Status

(They were meant to be two NESTED sub-menus. That was built, measured and abandoned; docs/nested-submenu.md has the
mechanism, the evidence and the exact blocker.)

The static checks in tests/kraken_ui_test.py prove the descriptor, the four relocated 31-entry tables and the two
widened bounds are right. This one proves the ROWS WORK:

  check_list      the Channel Set list ends with our nine items 22..30 and the relocated label arrays title them.
  check_commit    OK on an option row opens the stock radio-mark level with the current value marked; OK there COMMITS
                  and the RAM state changes; the Channel Set list comes back. One Scrambler row and one Parrot row.
  measure         --measure <addr> / --measure-all: hits_before_last_key vs hits_FROM_last_key, on a row we ship. That
                  attribution is the only way to tell the OPENING OK from the COMMITTING one; counting hits alone is
                  not enough and getting it wrong cost a day earlier in this project (docs/settings-submenu.md 4).

Trap this test is written around: Boot.wait_step() reports a key as SENT, not DISPATCHED, so every read is taken after
b.until(<a predicate identifying the level we want>), never straight after the walk. The runs of Downs also use a short
gap, because a dozen Downs at 400 ms outlast the stock 5 s Menu Exit Time and the radio drops to standby mid-walk.

Run inside the dm32uv-re image, like the other in-target tests:
  python3 /w/dm32uv-unleashed/tests/chanset_rows_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
  python3 .../chanset_rows_e2e.py <img> --measure-all
Run standalone it also drives OK on the row that is still a screen (DMR Link); the in-process run from
tests/kraken_ui_test.py skips that boot. Burst Voice became a nest parent on 2026-11-06: tests/bvnest_e2e.py.
"""
import functools
import pathlib
import struct
import sys
import threading

from build_artifacts import elf, syms, krk_define      # first: puts the tool directories on sys.path
from qemu_menu import L3_LIST, L3_ROW, PATHS, Boot, Suite
from qemu_menu import keys as _keys

keys = functools.partial(_keys, fast_gap=110)           # the walk to our rows is a dozen Downs deep: see qemu_menu.keys

CS_STOCK_ROWS = krk_define("KRK_CS_ITEM_DMRLINK")       # stock Channel Set items 0..21; ours are 22..30
OURS = [b"DMR Link", b"Burst Voice", b"Scrambler On/Off", b"Key", b"Parrot On/Off",
        b"Replay Audio", b"Check", b"Clear Audio", b"Status"]
TO_CS = PATHS["cs"]             # Menu > Settings > Channel Set: the stock rows plus ours, at level 3 (depth 3, mode 0x15)

# item -> (label, (state symbol, magic, reader(first 64 bytes) -> option index), Downs in the option level)
#   scr_st (krk_scramble.c): u32 magic, u16 key, u8 on, u8 fmt   -> on at +6
#   P      (krk_parrot.c):   u32 magic, u32 stamp, ps_ctx sm, u8 armed -> armed at +36
ROWS = {
    krk_define("KRK_CS_ITEM_SCRON"): (b"Scrambler On/Off", ("scr_st", 0x53435231, lambda b: b[6] & 1), 1),
    krk_define("KRK_CS_ITEM_PARON"): (b"Parrot On/Off", ("P", 0x50523031, lambda b: 1 if b[36] else 0), 1),
}


def check_list(patched, report):
    b = Boot(patched, "cs_list", keys(TO_CS), settle=140)
    try:
        ok = b.until(lambda bb: bb.at_depth(3, 1), timeout=90)
        b.wait_list(CS_STOCK_ROWS + 2, timeout=30)      # our first option row: only there once the generator is done
        lst = b.read_list()
        c = b.cells()
        labs = b.labels_of(31)
        report(ok and c["depth"] == 3 and c["mode"] == 0x15,
               f"Channel Set rows are open at history depth 3, mode 0x15 (the stock level-3 list) {c}")
        good_list = lst[-len(OURS):] == list(range(CS_STOCK_ROWS, CS_STOCK_ROWS + len(OURS)))
        report(good_list,
               f"the index list ends with our items {CS_STOCK_ROWS}..{CS_STOCK_ROWS + len(OURS) - 1}: {lst} (count cell {c['count']})")
        report(labs[CS_STOCK_ROWS:CS_STOCK_ROWS + len(OURS)] == OURS,
               f"the relocated 31-entry label array titles them: "
               f"{[x.decode(errors='replace') for x in labs[CS_STOCK_ROWS:CS_STOCK_ROWS + len(OURS)]]}")
        return lst, [] if good_list else ["list"]
    finally:
        b.close()


def check_commit(patched, report, lst, item, label, state, downs, s2):
    """OK on the row opens the stock option level; OK there commits and the RAM state changes."""
    row = lst.index(item)
    sym, magic, rd = state
    seq = (TO_CS + " " + "down " * row + "ok").replace("  ", " ")
    fails = []
    b = Boot(patched, f"cs_open_{item}", keys(seq), settle=140)
    try:
        ok = b.until(lambda bb: bb.at_depth(4, 2), timeout=120)
        c = b.cells()
        labs = b.labels_of(c["count"]) if ok else []
        good = ok and c["depth"] == 4 and c["mode"] == 0x1C and c["sub"] == 2 and c["icon"] == 2
        report(good, f"{label.decode()}: OK on Channel Set row {row} (item {item}) opened the stock option level at "
                     f"depth 4, mode 0x1c, radio marks, {c['count']} options "
                     f"{[x.decode(errors='replace') for x in labs]} {c}")
        if not good:
            fails.append(f"{label.decode()}: open")
        start, n = c["cur"], max(c["count"], 1)
    finally:
        b.close()

    target = (start + downs) % n                        # the cursor wraps at the end of the option list
    b = Boot(patched, f"cs_commit_{item}", keys((seq + " " + "down " * downs + "ok").replace("  ", " ")), settle=140)
    try:
        # Wait on the RAM STATE, not on the menu: the setter runs on the committing keypress but the
        # stock "Setting is OK" popup then blocks the key handler for a second before the level pops,
        # so a predicate on the menu level can time out while the commit has in fact happened.
        read = lambda bb: (lambda st: rd(st) if len(st) >= 40 and struct.unpack("<I", st[:4])[0] == magic else None)(
            bb.mem(s2[sym], 64) if sym in s2 else b"")
        done = b.until(lambda bb: read(bb) == target, timeout=140)
        got = read(b)
        back = b.until(lambda bb: bb.at_depth(3, 1), timeout=60)      # the popup pops the level itself
        c = b.cells()
        good = done and got == target
        report(good, f"{label.decode()}: {downs} Down from index {start} of {n} -> expected {target}, OK COMMITTED "
                     f"index {got} (RAM state read back); the Channel Set list came back at depth 3: {back} {c}")
        if not good:
            fails.append(f"{label.decode()}: commit")
        if not back:
            fails.append(f"{label.decode()}: list did not return")
    finally:
        b.close()
    return fails


SCREEN_OWNER_MAGIC = 0x53435252     # krk_screen_owner while a blocking screen runs


def check_screens(patched, report, lst):
    """DMR Link is still a SCREEN: OK on its row parks the stock keypad poll and runs the blocking screen (krk_run_screen sets
    krk_screen_owner). Standalone only (the old tests/chanset_e2e.py check, folded in 2026-11-06). Burst Voice opens its child
    list instead since 2026-11-06 (tests/bvnest_e2e.py)."""
    owner = syms(elf(1))["krk_screen_owner"]
    for item, name in ((krk_define("KRK_CS_ITEM_DMRLINK"), "DMR Link"),):
        row = lst.index(item)                   # position in the RENDERED list, not the item number
        b = Boot(patched, f"cs_screen_{item}", keys((TO_CS + " " + "down " * row + "ok").replace("  ", " ")), settle=140)
        try:
            opened = b.until(lambda bb: bb.u32(owner) == SCREEN_OWNER_MAGIC, timeout=120)
            l3 = b.u8(L3_ROW)
            got = b.u8(L3_LIST + l3)
            report(opened and got == item,
                   f"{name}: row {row} of the Channel Set list opened its screen (level-3 row {l3} -> item {got}, want {item}; "
                   f"krk_screen_owner {b.u32(owner):#x})", f"{name}: screen")
        finally:
            b.close()


# The addresses the mechanism depends on. Expected attribution, on the rows we ship:
#   0x03047324  the ENTER dispatcher - fires only on the OK that opens the option level
#   0x0304dc8c  the COMMIT dispatcher - fires only on the committing OK
#   0x0304e5a0  Radio Settings' commit arm - must NEVER fire for a Channel Set row
MEASURE = {"cs_enter_0x03047324": 0x03047324, "cs_commit_0x0304dc8c": 0x0304DC8C,
           "rs_arm_0x0304e5a0": 0x0304E5A0}


def measure(patched, addr, report, seq, what):
    n = len(seq.split())
    b = Boot(patched, f"cs_meas_{addr:x}_{n}", keys(seq), bp=addr, settle=140)
    try:
        b.wait_step(n - 2, timeout=200)
        b.pump(1.5)
        before = b.hits
        b.wait_step(n - 1, timeout=200)
        b.pump(6.0)
        report(True, f"{addr:#010x} [{what}]: hits_before_last_key={before}, hits_FROM_last_key={b.hits - before}")
    finally:
        b.close()


def check_rows(patched, suite):
    """check_list, then every row of ROWS; returns False when the list is wrong (the row checks would be meaningless)."""
    s2 = syms(elf(2))
    lst, f = check_list(patched, suite)
    if f:
        return None
    # the rows are independent (each has its own two QEMUs), so they run concurrently; the reports are replayed in ROWS order
    recs = {item: [] for item in ROWS}
    th = [threading.Thread(target=check_commit, args=(patched, lambda ok, msg, r=recs[item]: r.append((ok, msg)), lst, item, label, state, downs, s2))
          for item, (label, state, downs) in ROWS.items()]
    for x in th:
        x.start()
    for x in th:
        x.join()
    for item in ROWS:
        for ok, msg in recs[item]:
            suite(ok, msg)
    return lst


def run(patched, report=None):
    """The checks, callable in process from tests/kraken_ui_test.py (which passes its own report)."""
    suite = Suite(report)
    check_rows(patched, suite)
    return 1 if suite.fails else 0


def main():
    patched = pathlib.Path(sys.argv[1])
    args = sys.argv[2:]
    suite = Suite()

    if "--measure" in args or "--measure-all" in args:
        lst, f = check_list(patched, suite)
        if f:
            print("\nFAILURES: the Channel Set list is wrong; the measurements would be meaningless")
            return 1
        row = lst.index(krk_define("KRK_CS_ITEM_SCRON"))
        base = TO_CS + " " + "down " * row
        seqs = {"last OK OPENS the option level": (base + "ok").replace("  ", " "),
                "last OK COMMITS it": (base + "ok down ok").replace("  ", " ")}
        addrs = ([int(a, 16) for a in args[args.index("--measure") + 1:]]
                 if "--measure" in args else list(MEASURE.values()))
        for what, seq in seqs.items():
            print(f"\n===== {what}\n      keys: {seq}", flush=True)
            for a in addrs:
                measure(patched, a, suite, seq, what)
        return 0

    lst = check_rows(patched, suite)
    if lst is None:
        print("\nFAILURES: the Channel Set list is wrong; the row checks would be meaningless")
        return 1
    check_screens(patched, suite, lst)
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
