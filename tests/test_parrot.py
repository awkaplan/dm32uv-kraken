#!/usr/bin/env python3
"""Host test of the F9 parrot state machine (patches/kraken_ui/krk_parrot_sm.c) via tests/test_parrot.c, plus a
check that the pure logic and the voice-frame layer compile freestanding for the radio. Run: python3 tests/test_parrot.py"""
import subprocess, sys
import hostcc

exe = hostcc.build([hostcc.HERE / "test_parrot.c"], flags=["-std=gnu11", "-O1", "-Wall", "-Werror", "-fsanitize=undefined"])
r = subprocess.run([str(exe)], capture_output=True, text=True)
print(r.stdout, end="")
print(r.stderr, end="")
sys.exit(r.returncode)
