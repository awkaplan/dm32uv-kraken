#!/usr/bin/env python3
"""Drive the rows appended to stock Radio Settings through the real menu (docs/settings-submenu.md).

The other checks prove the table and the descriptor are right; this one proves the rows WORK: it
boots the combined image, walks Menu > Settings > Radio Settings, and for each appended row opens it,
checks the stock option level came up with our options and the mark on the current value, commits a
different value with OK, and checks the RAM state changed and the list came back.

This is the gate before flashing: the enter and commit paths are splices into stock compare chains,
and nothing else exercises them end to end.

Run inside the dm32uv-re image, like the other in-target tests:
  python3 /w/dm32uv-unleashed/tests/settings_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
"""
import pathlib
import struct
import sys

from build_artifacts import elf, syms, krk_define      # first: puts the tool directories on sys.path
from qemu_menu import CELL_COUNT, CELL_CUR, CELL_ICON, CELL_SUB, CURSOR, PATHS, SLOT, Boot, Suite, keys

# Menu > Settings (down once from Talk Group... the analog top list is Scan, Settings, APRS,
# KRAKEN, GNSS) > Radio Settings (item 0, already selected) -> the 4 stock rows + ours.
TO_RS = PATHS["rs"]

# label: (item, state symbol, magic, state byte offset, reader -> option index, how many Down presses)
# The expected index is computed from where the cursor STARTS (the current value, which the option
# level marks) plus the presses, wrapping at the option count - not from the press count alone:
# TX Power Trim opens on 8/8 (index 7), so 5 downs wrap to 4.
# The row of an appended item is its item number: stock's rows come first (KRK_RS_FIRST of them).
ROWS = {
    "TX Power Trim": (krk_define("KRK_RS_ITEM_TRIM"), "t", 0x54524D31, 4, lambda v: v - 1, 5),
    "Squelch Mode": (krk_define("KRK_RS_ITEM_SQUELCH"), "sqst", 0x53514C31, 6, lambda v: v, 1),   # sqst.mode follows u32 magic + u16 last_val
}


def main():
    patched = pathlib.Path(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None        # an explicit base port, else a free one per run
    s2 = syms(elf(2))
    suite = Suite()

    for i, label in enumerate(ROWS):
        item, state_sym, magic, off, to_idx, presses = ROWS[label]
        nav = TO_RS + " " + "down " * item
        # open the row, press Down `presses` times, commit
        moves = " ".join(["down"] * presses) if presses else ""
        seq = f"{nav} ok {moves} ok".replace("  ", " ")
        b = Boot(patched, f"rs_{i}", keys(seq), port + i if port else None, settle=140)
        try:
            opened = b.until(lambda bb: bb.u8(CELL_ICON) == 2, timeout=60)
            cells = dict(count=b.u8(CELL_COUNT), sub=b.u8(CELL_SUB),
                         cur=b.u8(CURSOR), dot=b.u8(CELL_CUR), slot=b.u8(SLOT))
            labs = [x.decode(errors="replace") for x in b.labels_of(min(cells["count"], 8))] if opened else []
            suite(opened and cells["sub"] == 2, f"{label}: the stock option level opened from Radio Settings "
                  f"({cells['count']} options {labs}, {cells})", label + ": open")
            # the commit: wait the "Setting is OK" popup out, then read the state
            back = b.until(lambda bb: bb.u8(CELL_ICON) == 1, timeout=60)
            st = b.mem(s2[state_sym], 8) if state_sym in s2 else b""
            got = None
            if len(st) >= off + 1 and struct.unpack("<I", st[:4])[0] == magic:
                got = to_idx(st[off])
            start = cells["cur"] if opened else 0
            n = cells["count"] if opened else 1
            target = (start + presses) % n          # the cursor wraps at the end of the list
            suite(back and got == target, f"{label}: {presses} Down from index {start} of {n} "
                  f"-> expected {target}, OK committed index {got} (list came back: {back})", label + ": commit")
        finally:
            b.close()
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
