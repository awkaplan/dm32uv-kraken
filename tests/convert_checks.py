"""2026-11-02 stock-menu conversions: Squelch Mode / Squelch Survey (F11), TX Power Trim (F6), Parrot / Parrot Play / Check / Clear / Status (F9).

Called from tests/kraken_ui_test.py next to tests/optrow_checks.py (whose pattern it copies, docs/stock-option-level.md 6.1 and 6.4):
  unit_checks   on the halted machine (stock functions faked): the KRAKEN list order, the three option rows (struct fields, labels read back
                from the ELF, krk_opt_enter cells for every current value, krk_opt_ok commit order, header title), the TX Power Trim state round trip
                (getter, setter, apply-now rule) and the one-shot rows. Returns {row label: KRAKEN list row}.
The booted half is gone: every option row it drove left the KRAKEN list on 2026-11-03 (its scenarios could only walk that list, so
it had nothing to run). The coverage moved with the rows: the option levels opening / committing / cancelling are in
tests/chanset_rows_e2e.py (RAM state read back) and tests/optrow_checks.py boot_checks (the Scrambler switch and the numeric Key);
the Parrot one-shots' two-press TX gate and the Clear popups are in tests/parrot_qemu_test.py.
The engine-level behaviour of Play / Check (real state machine, real PTT posts, gates, abort, TX never up) is in tests/parrot_qemu_test.py.
"""
import struct

import mi_rng_test as m
from gdb_rsp import read_mem

from qemu_menu import CELL_COUNT, CELL_CUR, CELL_ICON, CELL_IDX, CELL_PTR, CELL_SUB, CELL_VIS, CURSOR, ROW, SLOT
from optrow_checks import STOCK_MARKS, STOCK_OK_POPUP, STOCK_EMPTY
from target import cstr, fcall, r32, w32

PA_DRIVE = 0x00038498
# The KRAKEN list after the 2026-11-03 moves. Parrot's five rows and the Scrambler switch/Key
# went to stock Channel Set (docs/chanset-submenu.md), so only Scrambler Fmt is left of those two
# features; the rows are located by symbol, never by a hardcoded index (tests/optrow_checks.py
# krk_list_rows derives them from the image).
ORDER = [b"Scrambler Fmt", b"Squelch Survey", b"Satellites"]

# row label: (screen symbol, opt symbol, getter, setter, option labels, header text)
# The header text is struct krk_opt.title and is NOT always the row label: a row moved to a stock menu
# keeps its own short header for the KRAKEN title hook, while the stock menu draws the label from
# the relocated descriptor array instead (docs/chanset-submenu.md 3).
OPTIONS = {   # label: (screen symbol, opt symbol, getter, setter, labels)
    "Squelch Mode": ("krk_squelch_screen", "sq_opt", "sq_get", "sq_set", [b"Off (stock)", b"Normal", b"Tight"], b"Squelch Mode"),
    "TX Power Trim": ("krk_txtrim_screen", "trim_opt", "trim_opt_get", "trim_opt_set", [b"%d/8" % i for i in range(1, 9)], b"TX Power Trim"),
    # Parrot On/Off and the Scrambler switch are Channel Set rows now, so krk_opt_title / krk_opt_ok
    # (which resolve the open row through the KRAKEN list) no longer apply to them; the struct,
    # label and krk_opt_enter cell checks below are menu-independent and still run on both.
    "Parrot On/Off": ("krk_parrot_arm_screen", "par_opt", "par_get", "par_set", [b"Off", b"On"], b"Parrot"),
    "Scrambler On/Off": ("krk_scramble_on_screen", "scr_on_opt", "scr_on_get", "scr_on_set", [b"Off", b"On"], b"Scrambler"),
}


def unit_checks(c, s, s2, dwc):
    # ---- list order: rows found by walking krk_row_screen (the list is rebuilt from the extension table)
    labels = {}
    for r in range(28):
        _, p, abi, _ = fcall(c, s["krk_row_screen"], (r,))
        if not p:
            break
        labels[r] = (cstr(c, r32(c, p)), p)
    names = [v[0] for v in labels.values()]
    rows = {v[0].decode(): r for r, v in labels.items()}
    print("== KRAKEN list after the conversions:", [n.decode() for n in names])
    pos = [names.index(x) for x in ORDER]
    m.report(pos == sorted(pos) and names[-1] == b"About" and len(names) <= 27,
             f"list order: Scrambler Fmt, Squelch Survey, Satellites, About last ({len(names)} rows)")
    m.report(b"APRS RX" not in names, "APRS RX is not in the KRAKEN list: it is an APRS feature and appears only under the stock APRS menu (2026-11-03)")
    m.report(b"RX Monitor" not in names, "RX Monitor is not in the KRAKEN list: it is APRS item 12 under the stock APRS menu (2026-11-03)")
    m.report(b"Last Heard" not in names, "Last Heard is not in the KRAKEN list: it is Call Log item 4, reachable on analog and digital (2026-11-03)")
    m.report(b"TX Power Trim" not in names and b"Squelch Mode" not in names,
             "TX Power Trim and Squelch Mode are not in the KRAKEN list: both are radio-wide and moved to stock Radio Settings (2026-11-03, docs/settings-submenu.md)")
    m.report(b"Burst Voice" not in names and b"DMR Link" not in names,
             "Burst Voice and DMR Link are not in the KRAKEN list: both are per-channel and moved to stock Channel Set (2026-11-03, docs/chanset-submenu.md)")
    gone = [b"Parrot", b"Parrot On/Off", b"Replay Audio", b"Check", b"Clear Audio", b"Status",
            b"Parrot Play", b"Parrot Check", b"Parrot Clear", b"Parrot Status",
            b"Scrambler On/Off", b"Scrambler (weak)", b"Key", b"Scrambler Key"]
    m.report(not [g for g in gone if g in names],
             "the Parrot rows and the Scrambler switch / Key are not in the KRAKEN list: both features are "
             "per-channel and digital, so they moved to stock Channel Set (2026-11-03, docs/chanset-submenu.md); "
             "only Scrambler Fmt, a key-format knob, stays here")

    # ---- the three option rows
    SCR = m.STACK_A - 0x300
    calls = []

    def rec(tag, ret=0):
        def f(regs, r0, r1):
            calls.append((tag, r0, r1))
            return ret
        return f
    cat = s["krk_category_entry"]
    for label, (scr_sym, opt_sym, get_sym, set_sym, labs, hdr) in OPTIONS.items():
        scr, opt, get, setf = s2[scr_sym], s2[opt_sym], s2[get_sym], s2[set_sym]
        # krk_opt_title / krk_opt_ok resolve the open row through KRK_SELECTED_ROW in the KRAKEN
        # list, so those two checks only apply to rows that are still in it. TX Power Trim moved to
        # stock Radio Settings (2026-11-03), which has its own title and commit paths
        # (docs/settings-submenu.md); everything above them here is menu-independent and still runs.
        row = rows.get(label)
        sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, scr, 28))
        m.report(cstr(c, sc[0]) == label.encode() and sc[1] == 0 and sc[2] == 0 and sc[3] == 0 and sc[5] == opt and sc[6] == 0,
                 f"{label}: row struct = option level (label, no enter / tick / exit, opt = {opt_sym}, flags 0)")
        o = struct.unpack("<IIBxxxII", read_mem(c, opt, 20))
        ptrs = struct.unpack("<%dI" % len(labs), read_mem(c, o[1], 4 * len(labs)))
        m.report(cstr(c, o[0]) == hdr and o[2] == len(labs) and o[3] == get and o[4] == setf and [cstr(c, p_) for p_ in ptrs] == labs,
                 f"{label}: header {cstr(c, o[0])!r} (want {hdr!r}), {o[2]} options {[x.decode() for x in labs]}, getter {get_sym}, setter {set_sym}")
        for idx in range(len(labs)):
            for a, v in ((CELL_COUNT, 0xAA), (CELL_SUB, 0xAA), (CELL_ICON, 0), (CELL_CUR, 0xAA), (CURSOR, 0xAA), (SLOT, 0xAA), (CELL_VIS, 0xAA)):
                m.wmem(c, a, bytes([v]))
            stops, _, abi, _ = fcall(c, s["krk_opt_enter"], (opt,), fakes={get: idx})
            n = len(labs)
            ok = (abi and read_mem(c, CELL_COUNT, 1)[0] == n and r32(c, CELL_PTR) == o[1] and read_mem(c, CELL_SUB, 1)[0] == 2 and read_mem(c, CELL_VIS, 1)[0] == n
                  and list(read_mem(c, CELL_IDX + 2, n)) == list(range(n)) and read_mem(c, CELL_ICON, 1)[0] == 2 and read_mem(c, CURSOR, 1)[0] == idx
                  and read_mem(c, CELL_CUR, 1)[0] == idx and read_mem(c, SLOT, 1)[0] == idx + 1)
            m.report(ok, f"{label}: current value {idx} ({labs[idx].decode()}): stock radio-mark list, {n} rows, cursor = dot = {idx}, slot {idx + 1}")
        if row is None:
            continue                       # not in the KRAKEN list: the two checks below do not apply
        m.wmem(c, ROW, bytes([row]))
        _, p, abi, _ = fcall(c, s["krk_opt_title"])
        m.report(abi and cstr(c, p) == label.encode(), f"{label}: header text {cstr(c, p)!r}")
        fk = {setf: rec("set"), STOCK_MARKS: rec("marks"), STOCK_OK_POPUP: rec("popup"), cat: rec("restore")}
        for cursor in sorted({0, 1, len(labs) - 1}):
            calls.clear()
            m.wmem(c, ROW, bytes([row]))
            m.wmem(c, CURSOR, bytes([cursor]))
            _, _, abi, _ = fcall(c, s["krk_opt_ok"], fakes=fk)
            order = [t for t, _, _ in calls]
            m.report(abi and order == ["set", "marks", "popup", "restore"] and calls[0][1] == cursor and read_mem(c, CELL_CUR, 1)[0] == cursor,
                     f"{label}: OK with the cursor on {labs[cursor].decode()}: set({calls[0][1] if calls else '?'}), marks, stock 'Setting is OK' popup, list cells restored")

    # ---- one-shot and screen rows
    # One-shot and screen rows. in_krk says whether the row is still in the KRAKEN list: only those
    # can be driven through krk_opt_title, which resolves the open row through that list. The four
    # Parrot action rows are Channel Set rows now and carry the Channel Set labels.
    for label, scr_sym, flags, has_enter, has_tick, in_krk in (
            ("Squelch Survey", "krk_sqsurvey_screen", 0, True, True, True),
            ("Replay Audio", "krk_parrot_play_screen", 1, True, False, False),
            ("Check", "krk_parrot_check_screen", 1, True, False, False),
            ("Clear Audio", "krk_parrot_clear_screen", 1, True, False, False),
            ("Status", "krk_parrot_status_screen", 0, True, True, False)):
        sc = struct.unpack("<IIIIHxxIBxxx", read_mem(c, s2[scr_sym], 28))
        m.report(cstr(c, sc[0]) == label.encode() and (sc[1] != 0) == has_enter and (sc[2] != 0) == has_tick and sc[5] == 0 and sc[6] == flags and (rows.get(label) is not None) == in_krk,
                 f"{label}: {'one-shot' if flags else 'screen'} row (enter {'set' if sc[1] else 'none'}, tick {'set' if sc[2] else 'none'}, flags {sc[6]}), "
                 f"{'in the KRAKEN list at row %s' % rows.get(label) if in_krk else 'a stock Channel Set row, not in the KRAKEN list'}")
        if not in_krk:
            continue
        m.wmem(c, ROW, bytes([rows[label]]))
        _, p, abi, _ = fcall(c, s["krk_opt_title"])
        m.report(abi and p == STOCK_EMPTY, f"{label}: not an option row, so the title hook gives the stock empty header")

    # ---- TX Power Trim: getter, setter, apply-now rule (state `t`, PA drive byte 0x38498)
    print("== TX Power Trim option row: state round trip")
    t = s2["t"]
    get, setf = s2["trim_opt_get"], s2["trim_opt_set"]

    def state(magic=True, eighths=8, raw=0xA0, out=0xA0, have=1, pa=0xA0):
        w32(c, t, 0x54524D31 if magic else 0xA5A5A5A5)
        m.wmem(c, t + 4, bytes([eighths, raw, out, have]))
        m.wmem(c, PA_DRIVE, bytes([pa]))

    def snap():
        b = read_mem(c, t + 4, 4)
        return b[0], b[1], b[2], b[3], read_mem(c, PA_DRIVE, 1)[0]
    state(magic=False)
    _, r0, abi, _ = fcall(c, get)
    m.report(abi and r0 == 7 and r32(c, t) == 0x54524D31, "trim getter with SRAM noise: the stock 8/8 (index 7), state initialised")
    for e in range(1, 9):
        state(eighths=e)
        _, r0, abi, _ = fcall(c, get)
        m.report(abi and r0 == e - 1, f"trim getter with {e}/8 in RAM: index {r0} (want {e - 1})")
    state(magic=False, pa=0x77)
    _, _, abi, _ = fcall(c, setf, (0,))
    e, raw, out, have, pa = snap()
    m.report(abi and e == 1 and have == 0 and pa == 0x77 and r32(c, t) == 0x54524D31, f"trim setter(0) with SRAM noise: state initialised, 1/8, the PA drive byte untouched (no scaled value known yet) {snap()}")
    state(eighths=8, raw=0xA0, out=0xA0, have=1, pa=0xA0)
    _, _, abi, _ = fcall(c, setf, (3,))
    e, raw, out, have, pa = snap()
    m.report(abi and e == 4 and out == 0x50 and pa == 0x50, f"trim setter(3) = 4/8 with a known raw 0xa0 and the byte still ours: PA drive byte {pa:#x} (want 0x50), last_out {out:#x}")
    state(eighths=8, raw=0xA0, out=0xA0, have=1, pa=0x11)
    _, _, abi, _ = fcall(c, setf, (0,))
    e, raw, out, have, pa = snap()
    m.report(abi and e == 1 and pa == 0x11 and out == 0xA0, f"trim setter(0) while another writer (Auto Power / a channel load) changed the byte: the byte {pa:#x} is left alone, next channel load applies 1/8")
    for bad in (8, 9, 255, 0xFFFFFFFF):
        state(eighths=5)
        _, _, abi, _ = fcall(c, setf, (bad,))
        m.report(abi and snap()[0] == 5, f"trim setter({bad:#x}): out of range, ignored")
    state(eighths=8, raw=0xFF, out=0xFF, have=1, pa=0xFF)
    for idx in range(8):
        want = (0xFF * (idx + 1) + 4) // 8
        state(eighths=8, raw=0xFF, out=0xFF, have=1, pa=0xFF)
        fcall(c, setf, (idx,))
        e, raw, out, have, pa = snap()
        m.report(e == idx + 1 and pa == want and out == want, f"trim setter({idx}) -> {idx + 1}/8: PA drive {pa:#04x} (want {want:#04x}), same rounding as the hook")
    m.wmem(c, PA_DRIVE, bytes([0]))
    state(magic=False)
    return rows
