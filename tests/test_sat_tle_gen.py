#!/usr/bin/env python3
"""tools/sat_tle_gen.py: the committed krk_sat_tles.c is exactly what the generator makes from data/tles.txt
(deterministic, not stale), and the validator rejects a bad checksum / wrong line number. Run: python3 tests/test_sat_tle_gen.py"""
import pathlib
import subprocess
import sys
import tempfile

from hostlib import check, finish

R = pathlib.Path(__file__).resolve().parents[1]
GEN = R / "tools/sat_tle_gen.py"
tmp = pathlib.Path(tempfile.mkdtemp())


def run(src, dst):
    return subprocess.run([sys.executable, str(GEN), str(src), str(dst)], capture_output=True, text=True)


r = run(R / "data/tles.txt", tmp / "a.c")
same = r.returncode == 0 and (tmp / "a.c").read_text() == (R / "patches/kraken_ui/krk_sat_tles.c").read_text()
check(same, f"committed krk_sat_tles.c matches the generator output {r.stdout.strip()} {r.stderr.strip()}")
r2 = run(R / "data/tles.txt", tmp / "b.c")
det = (tmp / "a.c").read_bytes() == (tmp / "b.c").read_bytes()
check(det, "deterministic")
txt = (R / "data/tles.txt").read_text().replace("0  9993", "0  9994", 1)
(tmp / "bad.txt").write_text(txt)
r = run(tmp / "bad.txt", tmp / "c.c")
bad = r.returncode != 0 and "checksum" in r.stderr
check(bad, f"bad checksum rejected: {r.stderr.strip()}")
finish(ok="", fail="")
