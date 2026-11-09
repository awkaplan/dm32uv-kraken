#!/usr/bin/env python3
"""Drive Settings > Radio Settings > Freq Unlock through the real menu (docs/freq-unlock.md).

Three booted runs of the combined image, each from a fresh datachip (Freq Unlock OFF):
  short   ON, OK, then a short OK press (well under 10 s): still OFF, stock RX table, the list comes back;
  back    ON, OK, then Back on the warning: still OFF, the list comes back;
  hold    ON, OK, release, then OK held 4000 scans (~16-26 s, over 10 s): ON, the wide RX table (23-520 MHz), the list
          comes back after "Setting is OK" and the range popup.
State is read from RAM: band_on (krk_band.c) and the band table 0x38470.

Run inside the dm32uv-re image, like settings_e2e.py:
  python3 /w/dm32uv-unleashed/tests/freq_unlock_e2e.py /w/dm32uv-unleashed/out/kraken/progmem_KRAKEN_patched.bin
"""
import pathlib
import struct
import sys

from build_artifacts import elf, syms, krk_define      # first: puts the tool directories on sys.path
from qemu_menu import CELL_ICON, PATHS, Boot, Suite, keys

ITEM = krk_define("KRK_RS_ITEM_UNLOCK")
TO_RS = PATHS["rs"]

BAND_TBL = 0x00038470
B_ON_MAGIC = 0x4E4F4246
STOCK = (40000000, 47000000, 13600000, 17400000)
WIDE = (28000000, 52000000, 2300000, 27999999)

# The level opens on OFF (index 1); Up selects ON; OK commits -> the warning. Then the case's own keys.
OPEN_ON = f"{TO_RS} " + "down " * ITEM + "ok up ok"
CASES = {
    "short": (OPEN_ON, "ok", False),
    "back": (OPEN_ON, "back", False),
    "hold": (OPEN_ON, "hold", True),               # OK held over 10 s confirms (KEY["hold"])
}


def main():
    patched = pathlib.Path(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None        # an explicit base port, else a free one per run
    s2 = syms(elf(2))
    suite = Suite()
    for i, (name, (nav, extra, want_on)) in enumerate(CASES.items()):
        b = Boot(patched, f"unlock_{name}", keys(nav + " " + extra), port + i if port else None, settle=400)
        try:
            # the warning is up once the level's radio marks are gone and nothing else has redrawn: wait for the list
            # (row icons = numbered box) to come back after the case's keys
            b.until(lambda bb: bb.u8(CELL_ICON) == 2, timeout=90)
            back = b.until(lambda bb: bb.u8(CELL_ICON) == 1, timeout=240)
            on = b.u32(s2["band_on"]) == B_ON_MAGIC
            t = struct.unpack("<4I", b.mem(BAND_TBL, 16))
            am_lo = b.u32(BAND_TBL + 32)
            want_t = WIDE if want_on else STOCK
            ok = back and on == want_on and t == want_t and am_lo == (2300000 if want_on else 10800000)
            suite(ok, f"{name}: {'ON' if on else 'OFF'} (want {'ON' if want_on else 'OFF'}), "
                      f"RX table {[x / 1e5 for x in t]} MHz, AM from {am_lo / 1e5} MHz, list came back: {back}", name)
        finally:
            b.close()
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
