"""Stock option level, stock popups and the one-shot runner (krk_optrow.c, krk_core.c, docs/stock-option-level.md).

Three parts, called from tests/kraken_ui_test.py:
  * static_checks / unit_checks: on the halted machine. The three splices and the two stubs, the cells krk_opt_enter fills,
    the title and commit hooks (stock functions faked, calls recorded), the one-shot branch of the screen runner, the
    popup draw sequence, and the Upload Beacon one-shot with a faked transmit entry (a single OK never transmits).
  * boot_checks: booted runs of the real image under -M dm32 (qemu_run.py with a keyseq, memory read back over a gdb stub,
    frames compared): the KRAKEN > RX Monitor level opens on the stock radio-mark list, Back cancels, OK commits through
    the setter (the Monitor state in RAM flips) with the stock "Setting is OK" popup and the list returns with the cursor
    where it was; the first rows of the level are pixel-identical to the stock Auto PowerOff level; Upload Beacon: one OK
    never reaches the transmit entry, OK-OK reaches it exactly once, a timeout or another key cancels, the menu returns
    with the cursor kept in every case.
"""
import os
import pathlib
import re
import struct
import subprocess

import mi_rng_test as m
from build_artifacts import EXT_TABLE, REPO, STOCK_IMG, krk_define, krk_ext_ranges, stock_is
from build_patch import decode_branch
from gdb_rsp import read_mem
from qemu_menu import (CELL_COUNT, CELL_CUR, CELL_ICON, CELL_IDX, CELL_PTR, CELL_SUB, CELL_VIS, CURSOR, EDIT_BUF, EDIT_CUR, EDIT_FLAGS, EDIT_FRESH, EDIT_LEN,
                       HIST, L2, L3_LIST, L3_ROW, OS_TICKS, PATHS, ROW, SLOT, TOP, TOPROW, Boot, keys, run_scenarios)
from target import cstr, fcall, open_category, r32, w32

STOCK_MARKS, STOCK_OK_POPUP, STOCK_SETMODE, STOCK_EMPTY = 0x03041598, 0x0303A4DC, 0x03035EC4, 0x030B1458
SEMP, SEMQ, SCANK, LCDR, FILL, POP, QPEND, DLY = 0x0300C500, 0x0300C53C, 0x03013660, 0x0301CAD0, 0x03017F54, 0x03036C00, 0x0306A1B8, 0x0300C4BC
BOX, BITMAP, TEXT = 0x03019BE8, 0x03039034, 0x03018B5C
MON_MAGIC = 0x4D4F4E31
SCAN_OK, SCAN_BACK, SCAN_UP = 0x03, 0x06, 0x02


# ------------------------------------------------------------------------------------------------------------------
def disasm(elf, addr, nbytes):
    out = subprocess.run(["csky-abiv2-elf-objdump", "-d", f"--start-address={addr:#x}", f"--stop-address={addr + nbytes:#x}", str(elf)],
                         capture_output=True, text=True, check=True).stdout
    return [re.sub(r"\s+", " ", l.split("\t", 2)[-1].split("//")[0]).strip() for l in out.splitlines() if re.match(r"\s+[0-9a-f]+:\t", l)]


def static_checks(img, s, elf):
    print("== stock option level: splices and stubs")
    rd = lambda a, n: img[a - 0x3000000:a - 0x3000000 + n]
    for name, a, want, nops in (("opt_ok_hook", 0x0303AB42, s["krk_opt_ok"], b"\x03\x6c\x03\x6c"),
                                ("opt_title_hook", 0x030385F4, s["krk_title_stub"], b"\x03\x6c"),
                                ("aprs_opt_ok_hook", 0x0304D81C, s["krk_aprs_ok_stub"], b"")):
        kind, half = decode_branch(rd(a, 4))
        m.report(kind == "bsr" and a + half * 2 == want, f"{name}: {kind} at {a:#x} -> {a + half * 2:#x} (want {want:#x})")
        m.report(rd(a + 4, len(nops)) == nops, f"{name}: the displaced stock bytes after the bsr are nop16 filler ({nops.hex(' ') or 'none'})")
    ins = disasm(elf, s["krk_title_stub"], 16)
    m.report([i.split()[0] for i in ins[:4]] == ["bsr", "mov", "lrw", "jmp"] and ins[1] == "mov r3, r0" and "0x3038ece" in ins[2] and ins[3] == "jmp r2",
             f"krk_title_stub = bsr krk_opt_title; mov r3,r0; lrw r2,0x3038ece; jmp r2 (stock header draw): {ins[:4]}")
    ins = disasm(elf, s["krk_aprs_ok_stub"], 24)
    m.report(ins[0] == "cmpnei r3, 7" and ins[1].startswith("bf") and ins[2] == "mov r0, r3" and ins[3].startswith("bsr") and "0x304d846" in ins[4]
             and ins[5] == "jmp r3" and ins[6] == "jmp r15",
             f"krk_aprs_ok_stub: item 7 returns into the stock block, items >= 8 -> krk_aprs_opt_ok then the handler epilogue 0x0304d846: {ins[:7]}")
    # the stock bytes the three splices replace (and the table the title hook sits behind) are what the traces saw
    stock = STOCK_IMG.read_bytes()
    sb = lambda a, n: stock[a - 0x3000000:a - 0x3000000 + n]
    m.report(stock_is(0x0303AB42, "1c3001 31ffe3bfd9"), "stock 0x0303ab42: movi r0,28; movi r1,1; bsr 0x03035ec4 (set mode 0x1c)")
    m.report(stock_is(0x030385F4, "641300e86c04"), "stock 0x030385f4: lrw r3,[0x030b1458]; br 0x03038ece (empty header for category >= 10)")
    m.report(sb(0x0304D81C, 4) == bytes.fromhex("473b1408"), "stock 0x0304d81c: cmpnei r3,7; bt 0x0304d846 (category 9: items >= 8 do nothing)")


# ------------------------------------------------------------------------------------------------------------------
def unit_checks(c, s, s2, dwc):
    SCR = m.STACK_A - 0x300           # scratch for fake structs
    mon_get, mon_set = s2["mon_get"], s2["mon_set"]

    def mkopt(n, title=0, labels=0):
        m.wmem(c, SCR, struct.pack("<IIBxxxIIIIII", title, labels or SCR + 0x40, n, mon_get, mon_set, 0, 0, 0, 0))
        return SCR

    print("== option level: krk_opt_enter fills the stock list cells like the stock case block 0x0303d634")
    for n, cur, want_cur in ((2, 0, 0), (2, 1, 1), (4, 3, 3), (8, 7, 7), (6, 9, 0), (1, 0, 0), (9, 5, 5)):
        for a, v in ((CELL_COUNT, 0xAA), (CELL_SUB, 0xAA), (CELL_ICON, 0), (CELL_CUR, 0xAA), (CURSOR, 0xAA), (SLOT, 0xAA), (CELL_VIS, 0xAA)):
            m.wmem(c, a, bytes([v]))
        m.wmem(c, CELL_IDX, bytes(24))
        n_eff = min(max(n, 2), 8)
        lab = SCR + 0x40
        stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (mkopt(n),), fakes={mon_get: cur})
        idx_n = struct.unpack("<H", read_mem(c, CELL_IDX, 2))[0]
        items = list(read_mem(c, CELL_IDX + 2, n_eff))
        ok = (abi and read_mem(c, CELL_COUNT, 1)[0] == n_eff and r32(c, CELL_PTR) == lab and read_mem(c, CELL_SUB, 1)[0] == 2
              and read_mem(c, CELL_VIS, 1)[0] == n_eff and idx_n == n_eff and items == list(range(n_eff))
              and read_mem(c, CELL_ICON, 1)[0] == 2 and read_mem(c, CURSOR, 1)[0] == want_cur and read_mem(c, CELL_CUR, 1)[0] == want_cur
              and read_mem(c, SLOT, 1)[0] == want_cur + 1 and len(stops) == 1)
        m.report(ok, f"n={n} get()={cur}: count {read_mem(c, CELL_COUNT, 1)[0]}, subtype 2, index list 0..{n_eff - 1}, icon mode 2, "
                     f"cursor = dot cell = {read_mem(c, CURSOR, 1)[0]} (want {want_cur}), slot {read_mem(c, SLOT, 1)[0]}")

    print("== option level: row lookup, title, OK commit (stock functions faked)")
    rows = {}
    for r in range(28):
        _, p, abi, _ = fcall(c, s["krk_row_screen"], (r,))
        rows[r] = p
    # RX Monitor left the KRAKEN list for the stock APRS menu (item 12) on 2026-11-03, so the KRAKEN
    # option-row machinery (krk_opt_title / krk_opt_ok, which look the row up in that list) is exercised
    # with Scrambler Fmt, a plain option row that stays. Located by symbol, never by a fixed number.
    row_mon = next((r for r, p in rows.items() if p == s2["krk_scrfmt_row"]), None)
    m.report(row_mon is not None and rows[27] == 0 and rows[26] == 0 and s2["krk_monitor_row"] not in rows.values(),
             f"krk_row_screen: Scrambler Fmt is KRAKEN list row {row_mon}; RX Monitor is no longer in the list; rows past the list give NULL")
    row_plain = 0
    # the screen struct: label, enter, tick, exit, tick_ms (u16), opt, flags
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2["krk_monitor_row"], 28))
    m.report(cstr(c, sc[0]) == b"RX Monitor" and sc[1] == 0 and sc[2] == 0 and sc[5] == s2["mon_opt"] and sc[6] == 0,
             "RX Monitor row: label 'RX Monitor', no enter/tick (a setting, not a screen), opt = mon_opt, flags 0")
    o = struct.unpack("<IIBxxxII", read_mem(c, s2["mon_opt"], 20))
    m.report(read_mem(c, s2["mon_opt"] + 28, 8) == bytes(8), "mon_opt: no title_fn, no after hook (the new optional fields stay 0)")
    labs = struct.unpack("<III", read_mem(c, o[1], 12))
    m.report(cstr(c, o[0]) == b"RX Monitor" and o[2] == 3 and o[3] == mon_get and o[4] == mon_set and [cstr(c, p_) for p_ in labs] == [b"Off", b"On", b"Alert"],
             f"mon_opt: title 'RX Monitor', 3 options {[cstr(c, p_) for p_ in labs]}, getter mon_get, setter mon_set")

    m.wmem(c, ROW, bytes([row_mon]))
    open_category(c, 6)                 # another category (Record, which DM32.01.L01.048 routes to the same default arm)
    _, p, abi, _ = fcall(c, s["krk_opt_title"])
    m.report(abi and p == STOCK_EMPTY, "krk_opt_title with another category open (id 6): the stock empty header string")
    open_category(c)
    _, p, abi, _ = fcall(c, s["krk_opt_title"])
    m.report(abi and cstr(c, p) == b"Scrambler Fmt", f"krk_opt_title on a KRAKEN option row (Scrambler Fmt): {cstr(c, p)!r}")
    m.wmem(c, ROW, bytes([row_plain]))
    _, p, abi, _ = fcall(c, s["krk_opt_title"])
    m.report(abi and p == STOCK_EMPTY, "krk_opt_title on a screen row: the stock empty header string (as stock for category >= 10)")
    m.wmem(c, ROW, bytes([200]))
    _, p, abi, _ = fcall(c, s["krk_opt_title"])
    m.report(abi and p == STOCK_EMPTY, "krk_opt_title with a stale row byte (out of range): the stock empty header string")

    calls = []

    def rec(tag, ret=0):
        def f(regs, r0, r1):
            calls.append((tag, r0, r1))
            return ret
        return f
    cat = s["krk_category_entry"]
    fk = {mon_set: rec("set"), STOCK_MARKS: rec("marks"), STOCK_OK_POPUP: rec("popup"), STOCK_SETMODE: rec("mode"), cat: rec("restore"),
          s["krk_aprs_render"]: rec("restore")}
    for cursor, want in ((1, 1), (0, 0), (2, 2), (5, 0)):
        calls.clear()
        calls.clear()
        m.wmem(c, CURSOR, bytes([cursor]))
        _, _, abi, _ = fcall(c, s["krk_aprs_opt_ok"], (12,), fakes=fk)      # APRS item 12 = RX Monitor
        order = [t for t, _, _ in calls]
        m.report(abi and order == ["set", "marks", "popup", "restore"] and calls[0][1] == want and read_mem(c, CELL_CUR, 1)[0] == want,
                 f"OK on the APRS RX Monitor level (item 12) with the cursor on {cursor}: set({calls[0][1] if calls else '?'}) (want {want}), marks, stock popup, APRS list cells restored {order}")
    calls.clear()
    m.wmem(c, ROW, bytes([row_plain]))
    _, _, abi, _ = fcall(c, s["krk_opt_ok"], fakes=fk)
    m.report(abi and [(t, a_, b_) for t, a_, b_ in calls] == [("mode", 0x1C, 1)], f"OK on a level-2 screen of a row that is not a setting: stock's set-mode(0x1c, 1) only {calls}")

    print("== numeric entry (the stock text-entry screen, subtype 8): krk_opt_enter / krk_opt_commit with a numeric spec")
    RESET, HPOP = 0x030596B0, 0x03036C00

    def mknum(n, lo, hi, title=0):
        m.wmem(c, SCR, struct.pack("<IIBxxxIIIIII", title, 0, n, mon_get, mon_set, lo, hi, 0, 0))
        return SCR
    calls.clear()
    for val, n, fresh, want in ((0, 5, 0, b"0"), (12345, 5, 0, b"12345"), (123456, 5, 0, b"12345"), (70000, 8, 1, b"70000"), (7, 1, 0, b"7")):
        m.wmem(c, EDIT_BUF, b"XXXXXXXXXXXX")
        m.wmem(c, EDIT_FLAGS, bytes([0x08]))
        m.wmem(c, EDIT_FRESH, bytes([fresh]))
        m.wmem(c, CELL_COUNT, bytes([0xAA]))
        calls.clear()
        stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (mknum(n, 3, 65535),), fakes={mon_get: val, RESET: rec("reset")})
        buf = cstr(c, EDIT_BUF, 12)
        ok = (abi and read_mem(c, CELL_COUNT, 1)[0] == 1 and read_mem(c, CELL_SUB, 1)[0] == 8 and buf == want and read_mem(c, EDIT_LEN, 1)[0] == len(want)
              and struct.unpack("<H", read_mem(c, EDIT_CUR, 2))[0] == 12 * len(want) + 5 and read_mem(c, EDIT_FLAGS, 1)[0] == 0x48
              and [t for t, _, _ in calls] == (["reset"] if fresh else []) and r32(c, CELL_PTR) != 0)
        m.report(ok, f"numeric value {val}, {n} digits, fresh={fresh}: text-entry cells (count 1, subtype 8), buffer {buf!r}, length {read_mem(c, EDIT_LEN, 1)[0]}, cursor px, edit flag 0x40 set, stock reset {'called' if fresh else 'not called'}")
    ok_set = {mon_set: rec("set"), STOCK_OK_POPUP: rec("popup"), TEXT: rec("text"), BOX: rec("box"), BITMAP: rec("bmp"), DLY: rec("dly"), HPOP: rec("pop"), cat: rec("restore")}
    for text, lo, hi, good in ((b"123", 3, 65535, 123), (b"3", 3, 65535, 3), (b"65535", 3, 65535, 65535), (b"2", 3, 65535, None), (b"65536", 3, 65535, None),
                               (b"", 3, 65535, None), (b"0", 0, 9, 0), (b"00042", 1, 100, 42)):
        m.wmem(c, EDIT_BUF, text + b"\0")
        m.wmem(c, EDIT_LEN, bytes([len(text)]))
        m.wmem(c, EDIT_FLAGS, bytes([0x48]))
        calls.clear()
        _, _, abi, _ = fcall(c, s["krk_opt_commit"], (mknum(5, lo, hi), cat), fakes=ok_set)
        order = [t for t, _, _ in calls]
        flag = read_mem(c, EDIT_FLAGS, 1)[0]
        if good is not None:
            m.report(abi and order == ["set", "popup", "restore"] and calls[0][1] == good and flag == 0x08,
                     f"OK with '{text.decode()}' in [{lo},{hi}]: set({calls[0][1] if calls else '?'}) = {good}, stock 'Setting is OK' popup (pops), restore, edit flag cleared")
        else:
            m.report(abi and order == ["box", "bmp", "text", "dly", "pop", "restore"] and calls[3][1] == 1500 and flag == 0x08,
                     f"OK with '{text.decode()}' outside [{lo},{hi}]: 'Out of Range' popup 1.5 s, history pop, restore, nothing committed {order}")
    calls.clear()
    m.wmem(c, EDIT_BUF, b"7\0")
    m.wmem(c, EDIT_LEN, bytes([1]))
    fcall(c, s["krk_opt_commit"], (mknum(5, 0, 9), cat), fakes={**ok_set, TEXT: lambda regs, r0, r1: calls.append(("text", cstr(c, regs["r3"]))) or 0})
    m.report(("text", b"Out of Range") not in calls, "a valid value shows no error text")
    calls.clear()
    m.wmem(c, EDIT_BUF, b"70000\0")
    m.wmem(c, EDIT_LEN, bytes([5]))
    fcall(c, s["krk_opt_commit"], (mknum(5, 0, 9), cat), fakes={**ok_set, TEXT: lambda regs, r0, r1: calls.append(("text", cstr(c, regs["r3"]))) or 0})
    m.report(("text", b"Out of Range") in calls, "the error popup text is 'Out of Range'")

    print("== open level lookup (krk_open_opt) and the numeric header (krk_num_title)")
    # TOP / TOPROW: top-level list byte of the open category and its row; L2: the level-2 list (item per row)
    m.wmem(c, TOPROW, bytes([0]))
    for cat_id, row, want, what in ((10, row_mon, s2["scr_fmt_opt"], "KRAKEN Scrambler Fmt"), (10, row_plain, 0, "KRAKEN row 0 (Last Heard, a screen)"),
                                    (10, 100, 0, "KRAKEN row out of range"), (9, 0, 0, "APRS row 0"), (3, 0, 0, "another category")):
        m.wmem(c, TOP, bytes([cat_id]))
        m.wmem(c, ROW, bytes([row]))
        m.wmem(c, L2 + (row & 0x1F), bytes([0]))
        _, p, abi, _ = fcall(c, s["krk_open_opt"])
        m.report(abi and p == want, f"krk_open_opt, category {cat_id}: {what} -> {p:#x}")
    m.wmem(c, TOP, bytes([9]))
    for item in (8, 9, 10, 11, 12, 0, 7):
        m.wmem(c, ROW, bytes([4]))
        m.wmem(c, L2 + 4, bytes([item]))
        _, p, abi, _ = fcall(c, s["krk_open_opt"])
        want = {8: s2["aprs_path_opt"], 12: s2["mon_opt"]}.get(item, 0)
        m.report(abi and p == want, f"krk_open_opt, APRS item {item}: {({8: 'the Upload ID option row', 12: 'the RX Monitor option row'}).get(item, 'no option row (stock item or an appended screen)')} -> {p:#x}")
    m.wmem(c, ROW, bytes([0]))
    en = struct.unpack("<II", read_mem(c, 0x030BA280, 8))
    for lang in (0, 1):
        m.wmem(c, 0x3E9B4 + 0x30, bytes([lang << 6]))
        _, p, abi, _ = fcall(c, s["krk_num_title"], fakes={s["krk_open_opt"]: 0})
        m.report(abi and p == en[lang], f"krk_num_title with no numeric row open, language {lang}: the stock 'Input ID' string {cstr(c, p)!r}")
    m.wmem(c, 0x3E9B4 + 0x30, bytes([0x40]))
    for title, want_t in ((SCR + 0x80, b"Scrambler key"), (0, None)):
        m.wmem(c, SCR + 0x80, b"Scrambler key\0")
        o = mknum(5, 0, 9, title)
        _, p, abi, _ = fcall(c, s["krk_num_title"], fakes={s["krk_open_opt"]: o})
        if want_t:
            m.report(abi and cstr(c, p) == want_t, f"krk_num_title with a numeric row open: its title {cstr(c, p)!r}")
        else:
            m.report(abi and p == en[1], "krk_num_title with a numeric row without a title: the stock string")
    o = SCR
    m.wmem(c, SCR, struct.pack("<IIBxxxIIIIII", 0, SCR + 0x40, 2, mon_get, mon_set, 0, 0, 0, 0))
    _, p, abi, _ = fcall(c, s["krk_num_title"], fakes={s["krk_open_opt"]: o})
    m.report(abi and p == en[1], "krk_num_title with an OPTION row open (labels set): the stock string, never the option title")

    print("== APRS sub-list: OK at the option level of an appended row (krk_aprs_opt_ok)")
    calls.clear()
    fkc = {mon_set: rec("set"), STOCK_MARKS: rec("marks"), STOCK_OK_POPUP: rec("popup"), s["krk_aprs_render"]: rec("restore")}
    for item in (0, 7, 9, 10, 11, 255):
        _, _, abi, _ = fcall(c, s["krk_aprs_opt_ok"], (item,), fakes=fkc)
        m.report(abi and not calls, f"item {item}: no option row there (stock items and the appended screens are not settings): nothing committed")
    check_scrambler_rows(c, s, s2, dwc, SCR, rows, rec, calls)
    check_aprs_path(c, s, s2, dwc, SCR, rec, calls)

    print("== screen runner: KRK_SF_ONESHOT")
    SCR2 = m.STACK_A - 0x400
    rest = s["krk_category_entry"]
    drained = [0]

    def qpend(regs, r0, r1):
        if drained[0] > 0:
            drained[0] -= 1
            return 0x38E29
        return 0
    key_script = []

    def scank(regs, r0, r1):
        return key_script.pop(0) if key_script else 0
    base = {SEMP: 0, SEMQ: 0, LCDR: 0, FILL: 0, POP: 0, rest: 0, QPEND: qpend, SCANK: scank, DLY: 0}

    def run_one(flags, enter_fn=0, keys=()):
        m.wmem(c, SCR2, struct.pack("<IIIIHxxIBxxx", 0, enter_fn, 0, 0, 0, 0, flags))
        key_script[:] = list(keys)
        stops, _, abi, _ = fcall(c, s["krk_run_screen"], (SCR2, rest), fakes=base)
        return [p_ for p_, _, _ in stops], [(a_) for p_, a_, _ in stops if p_ == DLY], abi
    entered = []
    enter_addr = s2["upload_run"]                   # any callable address: its entry is faked below
    base[enter_addr] = lambda regs, r0, r1: entered.append(1) or 0
    order, dl, abi = run_one(1, enter_addr)
    m.report(abi and entered == [1] and FILL not in order and order[0] == enter_addr and order[-3:] == [POP, rest, QPEND],
             f"one-shot: enter() runs, the content area is NOT cleared (no display_fill_screen), then the exit path (pop, restore, drain): {[hex(x) for x in order[-5:]]}")
    order, dl, abi = run_one(1, enter_addr, keys=(SCAN_OK, SCAN_OK, 0))
    m.report(abi and len([x for x in order if x == SCANK]) == 3 and dl == [50, 50] and order.index(POP) > max(i for i, x in enumerate(order) if x == SCANK),
             f"one-shot with the confirming OK still down: it waits (2 x 50 ms) until every key is up before the exit path: delays {dl}")
    order, dl, abi = run_one(1, enter_addr, keys=(SCAN_OK,) * 100)
    m.report(abi and dl == [50] * 40 and POP in order, f"one-shot with a stuck key: the release wait is bounded (40 x 50 ms), then it leaves anyway ({len(dl)} delays)")
    order, dl, abi = run_one(0, 0, keys=(SCAN_BACK,))
    m.report(abi and order.count(FILL) == 1 and order[-3:] == [POP, rest, QPEND], "a normal screen: content area cleared once, Back exits through the same path")

    print("== popups: the stock box, icon and centered text calls")
    pops = []

    def fb(regs, r0, r1):
        pops.append(("box",))
        return 0

    def fbm(regs, r0, r1):
        pops.append(("bmp", r0, r1, regs["r2"], regs["r3"], r32(c, regs["r14"])))
        return 0

    def ft(regs, r0, r1):
        pops.append(("txt", r0, r1, regs["r2"], cstr(c, regs["r3"])))
        return 0
    fp = {BOX: fb, BITMAP: fbm, TEXT: ft, SEMP: 0, SEMQ: 0, SCANK: 0, LCDR: 0, DLY: 0}
    m.wmem(c, SCR + 0x100, b"Line one\0Line two\0")
    for kind, icon in ((0, 0x309DF80), (1, 0x309CD80), (2, 0x309BB80), (3, 0x309A980), (5, 0x309CD80)):
        pops.clear()
        _, _, abi, _ = fcall(c, s["krk_popup_draw"], (kind, SCR + 0x100, SCR + 0x109), fakes=fp)
        m.report(abi and pops == [("box",), ("bmp", 96, 86, 48, 48, icon), ("txt", 144, 0, 0xC618, b"Line one"), ("txt", 174, 0, 0xC618, b"Line two")],
                 f"popup kind {kind}: box, 48x48 icon {icon:#x} at (96,86), black text on grey at y 144 and 174")
    pops.clear()
    _, _, abi, _ = fcall(c, s["krk_popup_draw"], (1, SCR + 0x100, 0), fakes=fp)
    m.report(abi and [p_[0] for p_ in pops] == ["box", "bmp", "txt"], "popup with a NULL second line: one text line")
    pops.clear()
    m.wmem(c, SCR + 0x120, b"\0")
    _, _, abi, _ = fcall(c, s["krk_popup_draw"], (1, SCR + 0x100, SCR + 0x120), fakes=fp)
    m.report(abi and [p_[0] for p_ in pops] == ["box", "bmp", "txt"], "popup with an empty second line: one text line")

    print("== popups: krk_popup waits with yields and reports the first key PRESSED")
    ticks = [0]

    def dly(regs, r0, r1):
        ticks[0] += max(r0, 1) // 10 or 1
        w32(c, OS_TICKS, ticks[0])
        return 0
    fw = dict(fp)
    fw[DLY] = dly

    def popup(ms, keys):
        ticks[0] = 1000
        w32(c, OS_TICKS, 1000)
        key_script[:] = list(keys)
        fw[SCANK] = scank
        stops, r0, abi, _ = fcall(c, s["krk_popup"], (1, SCR + 0x100, 0, ms), fakes=fw)
        return r0 & 0xFF, len([1 for p_, _, _ in stops if p_ == DLY]), abi
    r, nd, abi = popup(0, ())
    m.report(abi and r == 0 and nd == 0, "ms = 0: draws only, no wait, no key read")
    r, nd, abi = popup(3000, ())
    m.report(abi and r == 0 and nd == 60, f"timeout: 3000 ms = {nd} yields of 50 ms (want 60), returns SCAN_NONE")
    r, nd, abi = popup(3000, (0, 0, SCAN_OK))
    m.report(abi and r == SCAN_OK, f"a key pressed after {nd} yields is returned (OK = {r:#x})")
    r, nd, abi = popup(3000, (SCAN_OK, SCAN_OK, SCAN_OK, 0, 0))
    m.report(abi and r == 0, "a key already down when the call starts is ignored while it stays down (the OK that opened the popup)")
    r, nd, abi = popup(3000, (SCAN_OK, SCAN_OK, 0, SCAN_OK))
    m.report(abi and r == SCAN_OK, "the same key released and pressed again counts")
    r, nd, abi = popup(3000, (SCAN_OK, SCAN_UP))
    m.report(abi and r == SCAN_UP, "another key pressed while the first is still down counts")
    w32(c, OS_TICKS, 0)

    print("== Upload Beacon one-shot: a single OK never transmits; OK-OK transmits once; the result is a popup")
    check_upload(c, s, s2, dwc, SCR)


def check_scrambler_rows(c, s, s2, dwc, SCR, rows, rec, calls):
    """F12: Scrambler (Off/On, with the follow-up warning popups), Scrambler Key (the first shipped numeric entry), Scrambler Fmt."""
    ST, REG, SCRM = s2["scr_st"], 0x1100038C, 0x53435231
    DRAW, HPOP, MARKS, OKP, DLYA = s["krk_popup_draw"], POP, STOCK_MARKS, STOCK_OK_POPUP, DLY
    cat = s["krk_category_entry"]
    print("== F12 rows: Scrambler (Off/On), Scrambler Key (numeric), Scrambler Fmt")
    # 2026-11-03: the Scrambler switch and the numeric Key moved to stock Channel Set (items 24, 25;
    # docs/chanset-submenu.md), so only Scrambler Fmt is still in the KRAKEN list. Rows are located
    # by symbol, never by a hardcoded index, so only Fmt has a row number here; the two that moved are
    # driven end to end by tests/chanset_rows_e2e.py instead.
    names = {"on": "krk_scramble_on_screen", "key": "krk_scrkey_row", "fmt": "krk_scrfmt_row"}
    row = {k: next((r for r, p in rows.items() if p == s2[v]), None) for k, v in names.items()}
    m.report(row["fmt"] is not None and row["on"] is None and row["key"] is None,
             f"KRAKEN list rows: Scrambler Fmt {row['fmt']} only; the Scrambler switch and Key are stock "
             f"Channel Set rows now ({row['on']}, {row['key']})")
    for k, v, label, optname in (("on", "krk_scramble_on_screen", b"Scrambler On/Off", "scr_on_opt"), ("key", "krk_scrkey_row", b"Key", "scr_key_opt"),
                                 ("fmt", "krk_scrfmt_row", b"Scrambler Fmt", "scr_fmt_opt")):
        sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2[v], 28))
        m.report(cstr(c, sc[0]) == label and sc[1] == 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == s2[optname] and sc[6] == 0,
                 f"{label.decode()} row: no enter/tick/exit (a setting, not a screen), opt = {optname}, flags 0")
    o = struct.unpack("<IIBxxxIIIIII", read_mem(c, s2["scr_on_opt"], 36))
    labs = struct.unpack("<II", read_mem(c, o[1], 8))
    m.report(cstr(c, o[0]) == b"Scrambler" and o[2] == 2 and [cstr(c, p_) for p_ in labs] == [b"Off", b"On"] and o[3] == s2["scr_on_get"] and o[4] == s2["scr_on_set"]
             and o[7] == s2["scr_on_title"] and o[8] == s2["scr_on_after"], "scr_on_opt: Off / On, getter, setter, dynamic header, follow-up popups")
    o = struct.unpack("<IIBxxxIIIIII", read_mem(c, s2["scr_key_opt"], 36))
    m.report(cstr(c, o[0]) == b"Scrambler Key" and o[1] == 0 and o[2] == 5 and o[3] == s2["scr_key_get"] and o[4] == s2["scr_key_set"] and (o[5], o[6]) == (1, 65535)
             and o[7] == 0 and o[8] == 0, f"scr_key_opt: numeric (labels NULL), 5 digits, range {o[5]}..{o[6]} (0 = no key is refused)")
    o = struct.unpack("<IIBxxxIIIIII", read_mem(c, s2["scr_fmt_opt"], 36))
    labs = struct.unpack("<IIII", read_mem(c, o[1], 16))
    fl = [cstr(c, p_) for p_ in labs]
    m.report(cstr(c, o[0]) == b"Scrambler Fmt" and o[2] == 4 and fl == [b"fmt0 plain K", b"fmt1 rx=~K", b"fmt2 swap K", b"fmt3 bitrev K"] and all(len(x) <= 16 for x in fl)
             and o[3] == s2["scr_fmt_get"] and o[4] == s2["scr_fmt_set"] and o[7] == 0 and o[8] == 0, f"scr_fmt_opt: 4 options {fl}, each <= 16 characters")

    # The Scrambler switch and Key are stock Channel Set rows since 2026-11-03, so their commit entry
    # point is krk_cs_opt_ok(item), not krk_opt_ok() (which resolves the open row through the
    # KRAKEN list). cs_open() puts the menu state where the real menu would: category 5 at the top
    # row, Settings item 1 at the level-2 row, and the item at the level-3 row through the level-3
    # index-list copy - which is exactly what krk_open_opt() and krk_nest-free krk_cs_opt_ok() read.
    CS_SCR_ON, CS_SCR_KEY = krk_define("KRK_CS_ITEM_SCRON"), krk_define("KRK_CS_ITEM_SCRKEY")

    def cs_open(item):
        m.wmem(c, TOPROW, b"\x00")
        m.wmem(c, TOP, bytes([5]))                      # top list[0] = category 5 (Settings)
        m.wmem(c, ROW, b"\x00")
        m.wmem(c, L2, bytes([1]))                       # level-2 list[0] = Settings item 1 (Channel Set)
        m.wmem(c, L3_ROW, b"\x00")
        m.wmem(c, L3_LIST, bytes([item]))                    # level-3 list[0] = the Channel Set item

    def state(key=0x1234, on=0, fmt=0, magic=True):
        w32(c, ST, SCRM if magic else 0xA5A5A5A5)
        m.wmem(c, ST + 4, struct.pack("<H", key) + bytes([on, fmt]))
        w32(c, ST + 8, 0)
        w32(c, ST + 12, 0)
        w32(c, ST + 16, 0)
        w32(c, REG, 0)
        dwc.chan_poke(c, b"\x10")
        m.wmem(c, 0x33824, b"\x00")
    rd = lambda: (struct.unpack("<H", read_mem(c, ST + 4, 2))[0], read_mem(c, ST + 6, 1)[0], read_mem(c, ST + 7, 1)[0])   # key, on, fmt

    # headers
    state()
    w32(c, REG, 0x0A0B0C0D)
    # krk_opt_title resolves the open row through the KRAKEN list, so only Fmt can be checked here.
    # The two that moved get their header from the Channel Set descriptor's relocated label array
    # instead, drawn by the stock level-4 handler 0x03038720 - which is also why the Scrambler switch's
    # dynamic title_fn (the live key in the header) is not shown there (docs/nested-submenu.md 4).
    for k, want in (("fmt", b"Scrambler Fmt"),):
        m.wmem(c, ROW, bytes([row[k]]))
        open_category(c)
        _, p_, abi, _ = fcall(c, s["krk_opt_title"])
        m.report(abi and cstr(c, p_) == want and len(want) <= 19, f"krk_opt_title, {k} row: {cstr(c, p_)!r}")
    # scr_on_title itself still works and is still wired to the opt: call it directly.
    _, p_, abi, _ = fcall(c, s2["scr_on_title"])
    m.report(abi and cstr(c, p_) == b"Scrambler 0A0B0C0D", f"scr_on_title (the live-key header, unused in the Channel Set placement): {cstr(c, p_)!r}")
    w32(c, REG, 0)

    # open: cursor / dot from the real getters
    def cells(opt):
        for a, v in ((CELL_COUNT, 0xAA), (CELL_SUB, 0xAA), (CELL_ICON, 0), (CELL_CUR, 0xAA), (CURSOR, 0xAA), (SLOT, 0xAA)):
            m.wmem(c, a, bytes([v]))
        stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (opt,))
        return abi, read_mem(c, CELL_COUNT, 1)[0], read_mem(c, CELL_SUB, 1)[0], read_mem(c, CELL_ICON, 1)[0], read_mem(c, CURSOR, 1)[0], read_mem(c, CELL_CUR, 1)[0], read_mem(c, SLOT, 1)[0]
    for on in (0, 1):
        state(on=on)
        r = cells(s2["scr_on_opt"])
        m.report(r == (True, 2, 2, 2, on, on, on + 1), f"Scrambler opens with the radio mark on {'On' if on else 'Off'}: (abi, count, subtype, icon, cursor, dot, slot) {r}")
    state(magic=False)
    r = cells(s2["scr_on_opt"])
    m.report(r == (True, 2, 2, 2, 0, 0, 1), f"Scrambler with power-up noise in the state: opens on Off {r}")
    for f in range(4):
        state(fmt=f)
        r = cells(s2["scr_fmt_opt"])
        m.report(r == (True, 4, 2, 2, f, f, f + 1), f"Scrambler Fmt opens with the mark on fmt{f}: {r}")

    # commit On / Off through the stock OK hook: order and effect
    pops = []

    def fdraw(regs, r0, r1):
        pops.append((r0, cstr(c, regs["r1"]), cstr(c, regs["r2"]) if regs["r2"] else None))
        calls.append(("draw", r0))
        return 0
    fk = {MARKS: rec("marks"), OKP: rec("popup"), DRAW: fdraw, DLYA: rec("dly"), cat: rec("restore")}
    cs_open(CS_SCR_ON)
    # No "restore" in these orders: krk_cs_opt_ok hands krk_opt_commit its own krk_cs_restore (which
    # refills the cells from the Channel Set descriptor), not krk_category_entry, so the `cat` fake
    # does not fire. That the cells ARE refilled is checked end to end by tests/chanset_rows_e2e.py
    # (the list comes back at depth 3 with the cursor on the row).
    for key, cur, want_calls, want_pops, tag in (
            (0x1234, 1, ["marks", "popup", "draw", "dly", "draw", "dly"], [(3, b"Weak scrambler,", b"not encrypted"), (3, b"Not for amateur", b"radio use")], "On with a key"),
            (0, 1, ["marks", "popup", "draw", "dly", "draw", "dly", "draw", "dly"], [(3, b"Set Scrambler", b"Key first")], "On without a key"),
            (0x1234, 0, ["marks", "popup"], [], "Off")):
        state(key=key, on=1 - cur)
        calls.clear()
        pops.clear()
        m.wmem(c, CURSOR, bytes([cur]))
        _, _, abi, _ = fcall(c, s["krk_cs_opt_ok"], (CS_SCR_ON,), fakes=fk)
        order = [t for t, *_ in calls]
        want_reg = (key << 16 | key) if cur and key else 0
        k_, on_, f_ = rd()
        m.report(abi and order == want_calls and pops[:len(want_pops)] == want_pops and on_ == cur and r32(c, REG) == want_reg and read_mem(c, CELL_CUR, 1)[0] == cur,
                 f"OK on Scrambler {tag}: state on={on_}, register {r32(c, REG):#010x}, marks, stock 'Setting is OK', warning popups {[p_[1:] for p_ in pops]}, list cells back {order}")
    state(on=1)
    calls.clear()
    m.wmem(c, CURSOR, bytes([0]))
    fcall(c, s["krk_cs_opt_ok"], (CS_SCR_ON,), fakes=fk)
    m.report(rd()[1] == 0 and r32(c, REG) == 0, "Off commit clears the register at once")
    state(on=0)
    calls.clear()
    pops.clear()
    cs_open(CS_SCR_ON)
    m.wmem(c, CURSOR, bytes([1]))
    fcall(c, s["krk_cs_opt_ok"], (CS_SCR_ON,), fakes={**fk, MARKS: lambda regs, r0, r1: 0})
    m.report(len(pops) == 2 and all(len(p_[1]) <= 16 and len(p_[2]) <= 16 for p_ in pops), "warning popup lines fit the 16-character box")

    # Fmt commit
    m.wmem(c, ROW, bytes([row["fmt"]]))
    for f, want in ((1, 0xEDCB1234), (2, 0x34123412), (3, 0x2C482C48), (0, 0x12341234)):
        state(key=0x1234, on=1, fmt=(f + 1) & 3)
        calls.clear()
        m.wmem(c, CURSOR, bytes([f]))
        open_category(c)                # a KRAKEN row: cs_open() above left Channel Set open
        _, _, abi, _ = fcall(c, s["krk_opt_ok"], fakes=fk)
        m.report(abi and [t for t, *_ in calls] == ["marks", "popup", "restore"] and rd()[2] == f and r32(c, REG) == want,
                 f"OK on Scrambler Fmt fmt{f}: fmt {rd()[2]}, register {r32(c, REG):#010x} (want {want:#010x}), no warning popup")
    state(key=0x1234, on=0, fmt=0)
    calls.clear()
    m.wmem(c, CURSOR, bytes([2]))
    open_category(c)
    fcall(c, s["krk_opt_ok"], fakes=fk)
    m.report(rd()[2] == 2 and r32(c, REG) == 0, "fmt changed while Off: stored, register stays 0")
    # numeric entry: enter
    for key, want in ((0, b"0"), (1234, b"1234"), (65535, b"65535"), (1, b"1")):
        state(key=key)
        m.wmem(c, EDIT_BUF, b"XXXXXXXXXXXX")
        m.wmem(c, EDIT_FLAGS, bytes([0x08]))
        m.wmem(c, EDIT_FRESH, bytes([0]))
        stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (s2["scr_key_opt"],))
        ok = (abi and cstr(c, EDIT_BUF, 12) == want and read_mem(c, EDIT_LEN, 1)[0] == len(want) and read_mem(c, CELL_COUNT, 1)[0] == 1 and read_mem(c, CELL_SUB, 1)[0] == 8
              and read_mem(c, EDIT_FLAGS, 1)[0] == 0x48 and struct.unpack("<H", read_mem(c, EDIT_CUR, 2))[0] == 12 * len(want) + 5)
        m.report(ok, f"Scrambler Key opens the stock text entry (subtype 8, edit flag) with the current key {want.decode()!r} in the buffer")
    # numeric header through the real lookup
    cs_open(CS_SCR_KEY)
    _, p_, abi, _ = fcall(c, s["krk_num_title"])
    m.report(abi and cstr(c, p_) == b"Scrambler Key", f"text-entry header on the Key row (krk_open_opt resolves it through the Channel Set level-3 row): {cstr(c, p_)!r}")
    cs_open(CS_SCR_ON)
    _, p_, abi, _ = fcall(c, s["krk_num_title"])
    m.report(abi and not cstr(c, p_).startswith(b"Scrambler"), f"on an option row the text-entry header is the stock string, not ours: {cstr(c, p_)!r}")

    # numeric commit through the Channel Set OK hook, real setter and state
    cs_open(CS_SCR_KEY)
    fkn = {OKP: rec("popup"), TEXT: lambda regs, r0, r1: calls.append(("text", cstr(c, regs["r3"]))) or 0, BOX: rec("box"), BITMAP: rec("bmp"),
           DLYA: rec("dly"), HPOP: rec("pop"), cat: rec("restore"), MARKS: rec("marks"), DRAW: fdraw}
    for text, good in ((b"1", 1), (b"1234", 1234), (b"65535", 65535), (b"00042", 42), (b"0", None), (b"65536", None), (b"", None), (b"99999999", None), (b"0000", None)):
        state(key=777, on=1)
        m.wmem(c, EDIT_BUF, text + b"\0")
        m.wmem(c, EDIT_LEN, bytes([len(text)]))
        m.wmem(c, EDIT_FLAGS, bytes([0x48]))
        calls.clear()
        _, _, abi, _ = fcall(c, s["krk_cs_opt_ok"], (CS_SCR_KEY,), fakes=fkn)
        order = [t for t, *_ in calls]
        k_, on_, f_ = rd()
        flag = read_mem(c, EDIT_FLAGS, 1)[0]
        # no "restore": the Channel Set commit uses krk_cs_restore, not the faked krk_category_entry
        if good is not None:
            m.report(abi and order == ["popup"] and k_ == good and r32(c, REG) == (good << 16 | good) and flag == 0x08 and on_ == 1,
                     f"Scrambler Key OK with '{text.decode()}': key {k_} (want {good}), applied to the register {r32(c, REG):#010x} (On), stock 'Setting is OK', edit flag cleared")
        else:
            m.report(abi and order == ["box", "bmp", "text", "dly", "pop"] and ("text", b"Out of Range") in calls and calls[3][1] == 1500 and k_ == 777
                     and r32(c, REG) == 0 and flag == 0x08,
                     f"Scrambler Key OK with '{text.decode()}': 'Out of Range' popup 1.5 s then history pop, key still 777, register untouched {order}")
    # a key typed while Off is stored but not applied
    state(key=777, on=0)
    m.wmem(c, EDIT_BUF, b"55\0")
    m.wmem(c, EDIT_LEN, bytes([2]))
    m.wmem(c, EDIT_FLAGS, bytes([0x48]))
    fcall(c, s["krk_cs_opt_ok"], (CS_SCR_KEY,), fakes=fkn)
    m.report(rd()[0] == 55 and r32(c, REG) == 0, "key committed while Off: stored, register stays 0")
    # power-up noise: the commit initialises the state
    state(magic=False)
    m.wmem(c, EDIT_BUF, b"9\0")
    m.wmem(c, EDIT_LEN, bytes([1]))
    m.wmem(c, EDIT_FLAGS, bytes([0x48]))
    fcall(c, s["krk_cs_opt_ok"], (CS_SCR_KEY,), fakes=fkn)
    m.report(r32(c, ST) == SCRM and rd() == (9, 0, 0), f"key committed on power-up noise: state initialised, key 9, Off, fmt0 {rd()}")
    state()
    w32(c, ST, 0xA5A5A5A5)


def check_aprs_path(c, s, s2, dwc, SCR, rec, calls):
    """APRS > Upload ID (analog): the path picker is a stock option level over the callsign record's path field."""
    print("== APRS Upload ID: option level over the four digipeater paths")
    GET, SETP = s2["krk_aprs_cfg_path"], s2["krk_aprs_cfg_set_path"]
    opt, DRAW = s2["aprs_path_opt"], s["krk_popup_draw"]
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2["krk_aprs_path_screen"], 28))
    m.report(cstr(c, sc[0]) == b"Upload ID" and sc[1] == 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == opt and sc[6] == 0,
             "Upload ID row: label 'Upload ID' (item 8's list text is the stock string), no custom screen, opt = aprs_path_opt, flags 0")
    o = struct.unpack("<IIBxxxIIIIII", read_mem(c, opt, 36))
    labs = [cstr(c, p_) for p_ in struct.unpack("<IIII", read_mem(c, o[1], 16))]
    m.report(o[2] == 4 and labs == [b"WIDE1-1,WIDE2-1", b"WIDE1-1", b"WIDE2-1", b"Direct"] and o[7] == 0 and o[8] == s2["aprs_path_after"]
             and o[3] == s2["aprs_path_get"] and o[4] == s2["aprs_path_set"],
             f"aprs_path_opt: 4 options {labs} (the names the beacon uses), getter, setter, failure popup hook, no custom header")
    for i, name in enumerate(labs):
        m.report(len(name) <= 16 and cstr(c, struct.unpack("<I", read_mem(c, s2["krk_aprs_path_names"] + 4 * i, 4))[0]) == name, f"option {i} '{name.decode()}' fits beside the radio mark and is krk_aprs_path_name({i})")
    for cur in range(4):
        for a, v in ((CELL_COUNT, 0xAA), (CELL_ICON, 0), (CELL_CUR, 0xAA), (CURSOR, 0xAA), (SLOT, 0xAA)):
            m.wmem(c, a, bytes([v]))
        stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (opt,), fakes={GET: cur})
        m.report(abi and len(stops) == 1 and (read_mem(c, CELL_COUNT, 1)[0], read_mem(c, CELL_SUB, 1)[0], read_mem(c, CELL_ICON, 1)[0], read_mem(c, CURSOR, 1)[0], read_mem(c, CELL_CUR, 1)[0], read_mem(c, SLOT, 1)[0]) == (4, 2, 2, cur, cur, cur + 1),
                 f"path {cur} stored in the callsign record: the level opens with cursor, mark and slot on option {cur}")
    # commit through the APRS hook (item 8), the old 'Saved' write path
    rend = s["krk_aprs_render"]
    pops = []

    def fdraw(regs, r0, r1):
        pops.append((r0, cstr(c, regs["r1"])))
        calls.append(("draw", r0))
        return 0
    for ret, want, tag in ((1, ["set", "marks", "popup", "restore"], "write verified"), (0, ["set", "marks", "popup", "draw", "dly", "restore"], "write failed")):
        for idx in range(4):
            calls.clear()
            pops.clear()
            m.wmem(c, CURSOR, bytes([idx]))
            fk = {SETP: rec("set", ret), STOCK_MARKS: rec("marks"), STOCK_OK_POPUP: rec("popup"), DRAW: fdraw, DLY: rec("dly"), rend: rec("restore")}
            _, _, abi, _ = fcall(c, s["krk_aprs_opt_ok"], (8,), fakes=fk)
            order = [t for t, *_ in calls]
            ok = abi and order == want and calls[0][1] == idx and read_mem(c, CELL_CUR, 1)[0] == idx
            if not ret:
                ok = ok and pops == [(2, b"Save FAILED")] and calls[4][1] == 1500
            else:
                ok = ok and not pops
            m.report(ok, f"OK on Upload ID option {idx} ({tag}): krk_aprs_cfg_set_path({calls[0][1] if calls else '?'}), marks, stock 'Setting is OK', "
                         f"{'no error popup' if ret else 'cross popup Save FAILED 1.5 s'}, APRS list cells restored")
    # the APRS item-action: item 8 opens the level, the others run as before
    m.wmem(c, ROW, bytes([1]))
    m.wmem(c, L2 + 1, bytes([8]))
    calls.clear()
    fcall(c, s["krk_aprs_item"], fakes={s["krk_opt_enter"]: lambda regs, r0, r1: calls.append(("enter", r0)) or 0, s["krk_run_screen"]: rec("run")})
    m.report(calls == [("enter", opt)], f"APRS item 8 (Upload ID) opens the option level, no blocking screen {calls}")
    for item, scr in ((9, "krk_aprs_upload_screen"), (10, "krk_callsign_screen"), (11, "krk_aprsrx_screen"), (13, "krk_aprs_text_screen")):
        m.wmem(c, L2 + 1, bytes([item]))
        calls.clear()
        fcall(c, s["krk_aprs_item"], fakes={s["krk_opt_enter"]: rec("enter"), s["krk_run_screen"]: rec("run")})
        m.report([t for t, *_ in calls] == ["run"] and calls[0][1] == s2[scr] if scr in s2 else [t for t, *_ in calls] == ["run"],
                 f"APRS item {item}: still a runner screen ({scr})")
    # the list: analog sub-list has Upload ID at row 1; the digital sub-list is the 8 stock rows, unchanged
    ICNT, IL, VIS = 0x3E8F8, 0x3E5F8, 0x3E8F4
    dwc.chan_poke(c, b"\x00")
    fcall(c, s["krk_aprs_render"])
    n = read_mem(c, ICNT, 1)[0]
    core = (REPO / "patches/kraken_ui/krk_core.c").read_text()   # the analog list as krk_core.c builds it (later rows appended)
    an = [int(t) if t.strip().isdigit() else krk_define(t.strip()) for t in re.search(r"aprs_analog_items\[\d*\]\s*=\s*\{([^}]*)\}", core)[1].split(",")]
    items = list(read_mem(c, IL + 2, len(an)))
    m.report(n == len(an) and items == an and an[:7] == [0, 8, 4, 9, 10, 11, 12] and struct.unpack("<H", read_mem(c, IL, 2))[0] == n,
             f"analog APRS list: {n} rows, items {items} (Upload ID = item 8 at row 1)")
    dwc.chan_poke(c, b"\x10")
    fcall(c, s["krk_aprs_render"])
    n = read_mem(c, ICNT, 1)[0]
    items = list(read_mem(c, IL + 2, 8))
    m.report(n == 8 and items == list(range(8)), f"digital APRS list: {n} rows, items {items} - the stock list, no Upload ID path row")



def check_upload(c, s, s2, dwc, SCR):
    SEND = s2["krk_aprs_tx_send"]
    POPUP = s["krk_popup"]
    ANALOG = s["krk_channel_analog"]
    run = s2["upload_run"]
    strs = {}
    base = m.STACK_A - 0x700                     # strings for the faked send: clear of the frames of the code under test (they use the first ~0x300 bytes below STACK_A)
    for i, t in enumerate((b"position", b"status", b"Sent", b"Channel busy", b"Set callsign first", b"Dual watch: retry", b"TX inhibited", b"Analog channel only", b"Busy", b"Modem stalled")):
        m.wmem(c, base + 32 * i, t + b"\0")
        strs[t] = base + 32 * i
    rec = {"popups": [], "sends": 0}
    state = {"first": 0, "send": (0, b"Sent", b"position")}
    n_call = [0]

    def fpop(regs, r0, r1):
        n_call[0] += 1
        rec["popups"].append((r0, cstr(c, r1), cstr(c, regs["r2"]) if regs["r2"] else None, regs["r3"]))
        return state["first"] if n_call[0] == 1 else 0

    def fsend(regs, r0, r1):
        rec["sends"] += 1
        r, why, kind = state["send"]
        w32(c, r0, strs[why])
        w32(c, r1, strs[kind] if kind in strs else strs[b"status"])
        return r & 0xFFFFFFFF
    fk = {POPUP: fpop, SEND: fsend, ANALOG: 1}

    def go(analog, first, send=(0, b"Sent", b"position")):
        rec["popups"].clear()
        rec["sends"] = 0
        n_call[0] = 0
        state["first"] = first
        state["send"] = send
        fk[ANALOG] = 1 if analog else 0
        _, _, abi, _ = fcall(c, run, fakes=fk)
        return abi, list(rec["popups"]), rec["sends"]
    arm = (3, b"OK again to send", b"TRANSMITS", 3000)
    abi, pops, sends = go(True, 0)
    m.report(abi and sends == 0 and pops == [arm], f"analog, the arming popup times out (no key): nothing sent, one popup {pops}")
    for k, name in ((SCAN_BACK, "Back"), (SCAN_UP, "Up"), (0x05, "Down"), (0x0E, "#"), (0x09, "P1")):
        abi, pops, sends = go(True, k)
        m.report(abi and sends == 0 and pops == [arm], f"arming popup answered by {name}: cancelled, nothing sent")
    abi, pops, sends = go(True, SCAN_OK)
    m.report(abi and sends == 1 and pops[0] == arm and pops[1] == (0, b"Sending...", None, 0) and pops[2] == (1, b"Sent position", None, 1800) and len(pops) == 3,
             f"OK, OK: exactly one send; popups {pops}")
    for why, l1, l2 in ((b"Channel busy", b"Channel busy", None), (b"Set callsign first", b"Set callsign", b"first"), (b"Dual watch: retry", b"Dual watch:", b"retry"),
                        (b"TX inhibited", b"TX inhibited", None), (b"Analog channel only", b"Analog channel", b"only"), (b"Busy", b"Busy", None),
                        (b"Modem stalled", b"Modem stalled", None)):
        abi, pops, sends = go(True, SCAN_OK, send=(-1, why, b""))
        m.report(abi and sends == 1 and pops[-1] == (2, l1, l2 or b"", 1800) and len(pops) == 3,
                 f"send refused with '{why.decode()}': error popup lines {l1!r} / {l2!r} for 1.8 s; one send call (got {pops[-1]}, {sends} sends, {len(pops)} popups)")
    abi, pops, sends = go(True, SCAN_OK, send=(0, b"Sent", b"status"))
    m.report(abi and pops[-1] == (1, b"Sent status", None, 1800), f"no GPS fix: 'Sent status' popup {pops[-1]}")
    abi, pops, sends = go(False, SCAN_OK)
    m.report(abi and sends == 0 and pops == [(2, b"Analog channel", b"only", 1800)], f"digital channel: no arming, no send, error popup {pops}")
    # the runner flags it is run under: an action row, no screen
    sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2["krk_aprs_upload_screen"], 28))
    m.report(cstr(c, sc[0]) == b"Upload Beacon" and sc[1] == run and sc[2] == 0 and sc[3] == 0 and sc[5] == 0 and sc[6] == 1,
             "Upload Beacon row: enter = upload_run, no tick (no custom screen), KRK_SF_ONESHOT")


# ------------------------------------------------------------------------------------------------------------------
# booted scenarios (the Boot harness, KEY and keys live in tests/qemu_menu.py)


def krk_list_rows(patched, s, s2):
    """{screen symbol: its row number in the KRAKEN list}, built from the image.

    Mirrors krk_core.c build_list(): the built-in screens except About, then the extension table's
    non-null slots whose screen and label both lie in an extension blob, then About. Row numbers
    therefore move whenever a row is added or retired, which is why nothing here hardcodes one.
    """
    img = pathlib.Path(patched).read_bytes()
    rd32 = lambda a: struct.unpack_from("<I", img, a - 0x03000000)[0]
    ok = lambda a: any(lo <= a < hi for lo, hi in krk_ext_ranges())
    # The built-ins, read from krk_screens[] in krk_core.c rather than hardcoded: Last Heard and GPS
    # Grid left this list on 2026-11-03 for the stock Call Log and GNSS menus, and `sym in s` cannot
    # tell - both symbols are still LINKED, because those stock rows reference them. A stale entry
    # here silently shifts every extension row (it reported Scrambler Fmt as row 4 instead of 2).
    core = (REPO / "patches/kraken_ui/krk_core.c").read_text()
    body = re.search(r"krk_screens\[\]\s*=\s*\{(.*?)\}", core, re.S)[1]
    builtin = [m for m in re.findall(r"&(krk_\w+_screen)", body) if m != "krk_about_screen"]
    rows, r = {}, 0
    for sym in builtin:                                  # all the built-ins but About
        if sym in s:
            rows[sym] = r
            r += 1
    by_addr = {v: k for k, v in list(s.items()) + list(s2.items())}
    for i in range(krk_define("KRK_EXT_MAX")):                    # then the extension slots that survive ext_ok()
        p = rd32(EXT_TABLE + 4 * i)
        if not p or not ok(p) or not ok(rd32(p)):
            continue
        if p in by_addr:
            rows[by_addr[p]] = r
        r += 1
    rows["krk_about_screen"] = r                         # About stays last
    return rows


def boot_checks(patched, s, s2):
    M = s2["M"]
    r_u32 = lambda d: struct.unpack("<I", d)[0]
    CELLS = lambda b: b.cells()
    mon_on = lambda b: r_u32(b.mem(M, 4)) == MON_MAGIC
    in_list = lambda b: b.in_list()                     # the KRAKEN / APRS list is the active level again
    to_krk = PATHS["krk"]
    # Rows are located by SYMBOL, never by a fixed number: a row added to or retired from the list
    # shifts every row after it (TX Power Trim left for stock Radio Settings on 2026-11-03, APRS RX
    # for the stock APRS menu the same day). krk_list_rows() walks the extension table the way
    # krk_core.c build_list() does, so these follow whatever the image actually ships.
    ROWS = krk_list_rows(patched, s, s2)
    row_of = lambda sym: ROWS[sym]
    to_row = lambda sym: to_krk + " " + "down " * row_of(sym) if row_of(sym) else to_krk
    ROW_FMT = row_of("krk_scrfmt_row")
    # RX Monitor left the KRAKEN list for the stock APRS menu (item 12) on 2026-11-03, so its row is
    # its position in krk_core.c's aprs_analog_items, not a row of the KRAKEN list. Derived from the
    # source so the walk follows whatever the image ships.
    core = (REPO / "patches/kraken_ui/krk_core.c").read_text()
    analog = [t.strip() for t in re.search(r"aprs_analog_items\[\d*\]\s*=\s*\{([^}]*)\}", core)[1].split(",")]
    ROW_MON = analog.index("KRK_APRS_ITEM_MON")                  # item numbers are generated (krk_menu_rows.h)
    N_APRS = len(analog)
    # 2026-11-03: the Scrambler switch and the numeric Key moved to stock Channel Set (items 24, 25).
    # These scenarios were worth keeping - they are the only booted coverage of the numeric editor's
    # digits, Back-deletes, range check and reopen - so they walk the Channel Set list instead of the
    # KRAKEN one. Their row POSITION in that list is not their item number: the stock 21-arm
    # visibility chain hides rows, so the position is read from the index list the generator built
    # (cs_row below), exactly as tests/chanset_rows_e2e.py does.
    to_mon = PATHS["aprs"] + " " + "down " * ROW_MON             # APRS list (analog), the last row = RX Monitor
    to_aprs_beacon = PATHS["aprs"] + " down down down"          # APRS list (analog), row 3 = Upload Beacon
    to_stock_autooff = PATHS["of"]                              # Settings > Radio Settings > Other Func (row 3)
    send_bp = s2["krk_aprs_tx_send"]
    popup_bp = s["krk_popup_draw"]
    SCRST = s2["scr_st"]
    to_fmt = to_row("krk_scrfmt_row")
    TO_CS = PATHS["cs"]

    def cs_rows(*items):
        """The positions of Channel Set items in the list the stock generator actually built."""
        b = Boot(patched, "csrows", keys(TO_CS), settle=140)
        try:
            b.until(lambda bb: bb.at_depth(3, 1), timeout=90)
            for item in items:
                b.wait_list(item)
            lst = b.read_list()
            return [lst.index(item) for item in items]
        finally:
            b.close()
    ROW_SCR, ROW_KEY = cs_rows(krk_define("KRK_CS_ITEM_SCRON"), krk_define("KRK_CS_ITEM_SCRKEY"))
    to_scr = TO_CS + " " + "down " * ROW_SCR
    to_key = TO_CS + " " + "down " * ROW_KEY
    to_path = "lead menu down down ok down"                  # APRS list (analog), row 1 = Upload ID
    frames = {}
    # every scenario: (keys, breakpoint, check function(boot, rep)); each is checked by its own thread right after its last key
    # (the stock Menu Exit Time sends an idle menu to standby, which would erase the state being read)

    def c_open(b, rep):
        ok = b.until(lambda b: b.mem(CELL_ICON, 1)[0] == 2)
        frames["ours"] = b.last_frame()
        cells = CELLS(b)
        ptr = r_u32(b.mem(CELL_PTR, 4))
        labs = [b.mem(r_u32(b.mem(ptr + 4 * i, 4)), 6).split(b"\0")[0] for i in range(3)] if ok else []
        rep(ok and cells["count"] == 3 and cells["sub"] == 2 and cells["cur"] == 0 and cells["dot"] == 0 and cells["slot"] == 1 and labs == [b"Off", b"On", b"Alert"],
            f"RX Monitor row: the stock option level is open (radio-mark mode), 3 options {labs}, cursor and dot on Off {cells}")
        rep(not mon_on(b), "opening the level does not change the Monitor (Off)")
        hist = b.mem(HIST, 4)
        rep(hist[3] == 0x15 and hist[0] == hist[1] == 3, f"menu history: one level deeper (depth {hist[0]}, mode code {hist[3]:#x}, as the stock option level 0x15)")

    def c_commit(b, rep):
        # The commit OK is the last key of this scenario, so the checks start while the stock
        # "Setting is OK" popup (1 s) is still up and the list has not been reinstalled yet; wait
        # the popup out rather than sampling through it.
        rep(b.until(mon_on, timeout=40), "OK on 'On': the Monitor state in RAM is on (M.magic = MON1) - the setter ran")
        ok = b.until(in_list, timeout=40)
        cells = CELLS(b)
        hist = b.mem(HIST, 4)
        rep(ok and cells["sub"] == 2 and cells["icon"] == 1 and cells["cur"] == ROW_MON and hist[0] == hist[1] == 2 and cells["count"] == N_APRS,
            f"after the stock 'Setting is OK' popup the APRS list is back: numbered-box mode, {N_APRS} rows, cursor on row {ROW_MON} (RX Monitor), history depth 2 {cells}")

    def c_cancel(b, rep):
        ok = b.until(in_list, timeout=40)
        cells = CELLS(b)
        rep(ok and cells["cur"] == ROW_MON and not mon_on(b), f"Down, Back: nothing committed (Monitor off), list back with the cursor on row {ROW_MON} {cells}")

    def c_toggle(b, rep):
        ok = b.until(in_list, timeout=40)
        cells = CELLS(b)
        rep(ok and cells["cur"] == ROW_MON and not mon_on(b), f"On, reopen (cursor on the current value On), Up, OK: the Monitor is Off again, list back {cells}")

    def c_stock(b, rep):
        b.until(lambda b: b.mem(CELL_ICON, 1)[0] == 2)
        frames["stock"] = b.last_frame()

    def scr_state(b):                                   # (magic ok, key, on, fmt)
        d = b.mem(SCRST, 8)
        return (r_u32(d[:4]) == 0x53435231, d[4] | d[5] << 8, d[6], d[7])
    key_of = lambda b: scr_state(b)[1] if scr_state(b)[0] else 0
    labels_of = lambda b, n: b.labels_of(n)

    def c_level(count, labs, cur, what, hdr=0x15):
        def f(b, rep):
            ok = b.until(lambda b: b.mem(CELL_ICON, 1)[0] == 2)
            cells = CELLS(b)
            got = labels_of(b, count) if ok else []
            rep(ok and cells["count"] == count and cells["sub"] == 2 and cells["cur"] == cur and cells["dot"] == cur and cells["slot"] == cur + 1 and got == labs,
                f"{what}: the stock option level is open (radio marks), options {got}, cursor and mark on {cur} {cells}")
            rep(b.mem(HIST, 4)[3] == hdr, f"{what}: menu history mode code {b.mem(HIST, 4)[3]:#x}")
        return f

    def c_back_in_list(row, what, check=None, count_min=0, timeout=40):
        def f(b, rep):
            ok = b.until(in_list, timeout=timeout)
            cells = CELLS(b)
            rep(ok and cells["cur"] == row and cells["sub"] == 2 and cells["icon"] == 1,
                f"{what}: the list is back (numbered rows) with the cursor kept on row {row} {cells}")
            if check:
                check(b, rep)
        return f
    # The Channel Set list is a level-3 list (mode 0x15), not the level-2 KRAKEN one (0x0e).
    in_cs_list = lambda b: b.in_list(0x15)
    def c_back_in_cs(row, what, check=None, timeout=60):
        """c_back_in_list for a row of the stock Channel Set list (a level-3 list, mode 0x15)."""
        def f(b, rep):
            ok = b.until(in_cs_list, timeout=timeout)
            cells = CELLS(b)
            rep(ok and cells["cur"] == row and cells["sub"] == 2 and cells["icon"] == 1,
                f"{what}: the Channel Set list is back (numbered rows) with the cursor kept on row {row} {cells}")
            if check:
                check(b, rep)
        return f
    ok_scr_on = lambda b, rep: (rep(scr_state(b)[0] and scr_state(b)[2] == 1, f"Scrambler On committed: state (magic, key, on, fmt) = {scr_state(b)}"),
                                rep(b.hits == 3, f"Scrambler On with no key: exactly 3 warning popups were drawn by our code (Set Scrambler Key first, weak, not for amateur) - got {b.hits}"))
    def c_scr_cancel(b, rep):
        ok = b.until(in_cs_list)
        rep(ok and CELLS(b)["cur"] == ROW_SCR and scr_state(b)[2] == 0 and b.hits == 0, f"Scrambler: Down, Back: nothing committed, no warning popup {scr_state(b)} hits {b.hits}, Channel Set list back on row {ROW_SCR}")

    def c_scr_off(b, rep):
        ok = b.until(in_cs_list, timeout=60)
        rep(ok and CELLS(b)["cur"] == ROW_SCR and scr_state(b)[0] and scr_state(b)[2] == 0 and b.hits == 3,
            f"Scrambler On, reopen (mark on On), Up, OK: Off again {scr_state(b)}, only the On commit showed the 3 warning popups (hits {b.hits})")

    def key_entry(text, what):
        def f(b, rep):
            b.until(lambda b: b.mem(EDIT_BUF, 12).split(b"\0")[0] == text and b.mem(EDIT_FLAGS, 1)[0] & 0x40, timeout=30)   # a loaded host may still be in the popup
            buf = b.mem(EDIT_BUF, 12).split(b"\0")[0]
            rep(buf == text and b.mem(EDIT_LEN, 1)[0] == len(text) and b.mem(EDIT_FLAGS, 1)[0] & 0x40 and b.mem(CELL_SUB, 1)[0] == 8,
                f"Scrambler Key, {what}: the stock text entry (subtype 8, edit flag) shows {buf!r} (length {b.mem(EDIT_LEN, 1)[0]})")
        return f

    def key_result(want, what):
        def f(b, rep):
            ok = b.until(lambda bb: in_cs_list(bb) and key_of(bb) == want, timeout=60)
            cells = CELLS(b)
            rep(ok and cells["cur"] == ROW_KEY and key_of(b) == want and not (b.mem(EDIT_FLAGS, 1)[0] & 0x40),
                f"Scrambler Key, {what}: key now {key_of(b)} (want {want}), Channel Set list back on its row, edit flag cleared {cells}")
        return f

    def path_val(b):                                    # the dot of a reopened level
        return b.mem(CELL_CUR, 1)[0]
    def c_path_reopen(b, rep):
        ok = b.until(lambda b: b.mem(CELL_ICON, 1)[0] == 2 and b.mem(CELL_COUNT, 1)[0] == 4, timeout=60)
        rep(ok and CELLS(b)["dot"] == 2 and b.hits == 0, f"Upload ID: after Down Down OK the stored path is 2 - the reopened level has the mark on option 2 {CELLS(b)}, no 'Save FAILED' popup (hits {b.hits})")

    def c_bcn(sends, why):
        def f(b, rep):
            b.pump(5.5)                                 # past the longest popup chain (arm 3 s, or arm + result), before Menu Exit Time (about 10 s)
            ok = in_list(b) and b.mem(CURSOR, 1)[0] == 3
            rep(ok, f"Upload Beacon, {why}: the menu is back (APRS list, numbered rows) with the cursor on Upload Beacon {CELLS(b)} history {b.mem(HIST, 4).hex(' ')}")
            rep(b.hits == sends, f"Upload Beacon, {why}: the transmit entry was reached {b.hits} time(s), want {sends}")
        return f
    scen = {
        "open": (to_mon + " ok", None, c_open),
        "commit": (to_mon + " ok down ok", None, c_commit),
        "cancel": (to_mon + " ok down back", None, c_cancel),
        "toggle": (to_mon + " ok down ok ok up ok", None, c_toggle),
        "stock": (to_stock_autooff + " ok", None, c_stock),
        # one level deeper than the KRAKEN rows: a Channel Set row's option level is mode 0x1c
        "scr_open": (to_scr + " ok", None, c_level(2, [b"Off", b"On"], 0, "Scrambler On/Off", hdr=0x1C)),
        "scr_on": (to_scr + " ok down ok", popup_bp, c_back_in_cs(ROW_SCR, "Scrambler On (no key set)", ok_scr_on)),
        "scr_cancel": (to_scr + " ok down back", popup_bp, c_scr_cancel),
        "scr_off": (to_scr + " ok down ok:2500 ok up ok", popup_bp, c_scr_off),
        "fmt_open": (to_fmt + " ok", None, c_level(4, [b"fmt0 plain K", b"fmt1 rx=~K", b"fmt2 swap K", b"fmt3 bitrev K"], 0, "Scrambler Fmt")),
        "fmt_set": (to_fmt + " ok down down ok", None, c_back_in_list(ROW_FMT, "Scrambler Fmt fmt2", lambda b, rep: rep(scr_state(b)[0] and scr_state(b)[3] == 2, f"Scrambler Fmt: fmt = 2 {scr_state(b)}"))),
        "key_digits": (to_key + " ok k1 k2 k3", None, key_entry(b"0123", "typed 1 2 3 after the shown 0")),
        "key_delete": (to_key + " ok k1 k2 back k4", None, key_entry(b"014", "typed 1 2, Back deletes the 2, typed 4")),
        "key_commit": (to_key + " ok k1 k2 k3 ok", None, key_result(123, "typed 123, OK")),
        "key_max": (to_key + " ok k6 k5 k5 k3 k5 ok", None, key_result(65535, "65535 is the top of the range")),
        "key_over": (to_key + " ok k6 k5 k5 k3 k6 ok", None, key_result(0, "65536 is refused ('Out of Range'), key stays unset")),
        "key_zero": (to_key + " ok ok", None, key_result(0, "OK on the shown 0 (no key) is refused")),
        "key_back": (to_key + " ok k1 back back back", None, key_result(0, "digit, Back x3 (delete, delete, leave): cancelled, nothing stored")),
        "key_reopen": (to_key + " ok k4 k2 ok ok", None, key_entry(b"42", "reopened after storing 42: the buffer shows the stored key")),   # no fixed gap: the reopen OK is sent right behind the commit OK and waits in the stock key queue until the 1 s popup handler returns (the same pattern as the RX Monitor "toggle" scenario); a long gap races the popup against the menu-exit timer
        "path_open": (to_path + " ok", None, c_level(4, [b"WIDE1-1,WIDE2-1", b"WIDE1-1", b"WIDE2-1", b"Direct"], 0, "APRS Upload ID", hdr=0x15)),
        "path_cancel": (to_path + " ok down back", None, c_back_in_list(1, "Upload ID: Down, Back (cancel)", lambda b, rep: rep(CELLS(b)["count"] == N_APRS, f"APRS analog list again: {N_APRS} rows {CELLS(b)}"), 0)),
        "path_commit": (to_path + " ok down down ok", popup_bp, c_back_in_list(1, "Upload ID: Down Down OK", lambda b, rep: rep(CELLS(b)["count"] == N_APRS and b.hits == 0, f"APRS analog list again after 'Setting is OK': {N_APRS} rows {CELLS(b)}, no Save FAILED popup (hits {b.hits})"), 0)),
        "path_reopen": (to_path + " ok down down ok ok", popup_bp, c_path_reopen),   # same: queued behind the commit, no fixed gap
        "bcn1": (to_aprs_beacon + " ok", send_bp, c_bcn(0, "one OK, the popup times out")),
        "bcn2": (to_aprs_beacon + " ok:60 ok", send_bp, c_bcn(1, "OK then OK within the window")),
        "bcn_other": (to_aprs_beacon + " ok:60 up", send_bp, c_bcn(0, "OK then Up (any other key cancels)")),
        "bcn_timeout": (to_aprs_beacon + " ok:1500 ok", send_bp, c_bcn(0, "OK, 4 s idle, OK (the second OK arms again)")),
    }
    print("== booted runs (RX Monitor option level, Upload Beacon one-shot; about 1 minute, parallel)")
    BATCH = int(os.environ.get("OPTROW_PARALLEL", "12"))          # parallel QEMUs; 12 since the idle hook sleeps (qemu_menu.idle_wait):
    # each QEMU then takes ~1/4 core at idle, not one core, and 12 ran all 27 scenarios clean in 173 s on a loaded host (2026-11-06)
    only = os.environ.get("OPTROW_ONLY", "").split(",") if os.environ.get("OPTROW_ONLY") else None
    run_scenarios(patched, scen, m.report, batch=BATCH, only=only)
    # pixel faithfulness against the stock Auto PowerOff level (first row: 'Off', highlighted, dot)
    fo, fs = frames.get("ours"), frames.get("stock")
    m.report(fo is not None and fs is not None, "frames of both levels captured")
    if fo and fs:
        diff = [y for y in range(320) if fo[y] != fs[y]]
        title = [y for y in diff if y < 27]
        m.report(not [y for y in diff if 27 <= y < 75],
                 "first row of the level ('Off', highlighted, dot) is pixel-identical to the stock Auto PowerOff level (rows 27-74)")
        m.report(all(fo[y][228:] == fs[y][228:] for y in range(27, 290)) and not [y for y in diff if y >= 290],
                 "scroll bar column and the OK/BACK bar are pixel-identical to the stock level")
        m.report(len(title) > 0 and 0 < len(diff) < 160,
                 f"only the header text and the other option labels differ ({len(diff)} differing pixel rows, {len(title)} of them in the header)")
