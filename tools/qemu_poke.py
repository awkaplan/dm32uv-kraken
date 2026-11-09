#!/usr/bin/env python3
"""Poke emulator memory at given times during a tools/qemu_ui_run.sh run (POKE=<file>), over the gdb stub.

Used to put a GPS fix / clock into a QEMU run when no NMEA feed reaches stock (GNSS is off in the factory
defaults) and to hold the dual-watch state at 0, so the F10 satellite screen can be rendered with real data.

File format, one poke per line ('#' comments):   T0 [T1 PERIOD] ADDR HEXBYTES
  or a read:   T0 r ADDR LEN      (prints "READ addr hex" to the log; used for stack high-water marks)
  T0 = seconds after this script started; with T1 and PERIOD the poke repeats every PERIOD s from T0 to T1.
  ADDR = hex, HEXBYTES = hex string. A literal 'text:...' may replace HEXBYTES (written with a trailing NUL).
Each poke halts the guest for a few milliseconds (gdb stop, 'M', continue).
"""
import sys
import time

sys.path.insert(0, "/w/dm32/dm32uv-re/tools/src/dm32_patch_tools")
from gdb_rsp import RspClient  # noqa: E402


def main():
    jobs = []
    for line in open(sys.argv[1]):
        f = line.split("#")[0].split()
        if not f:
            continue
        if len(f) == 4 and f[1] == "r":
            jobs.append((float(f[0]), int(f[2], 16), int(f[3], 16)))
            continue
        if len(f) == 3:
            t0, t1, per, addr, data = float(f[0]), float(f[0]), 1.0, int(f[1], 16), f[2]
        elif len(f) == 5:
            t0, t1, per, addr, data = float(f[0]), float(f[1]), float(f[2]), int(f[3], 16), f[4]
        else:
            sys.exit("qemu_poke: bad line: " + line)
        b = (data[5:].encode() + b"\0") if data.startswith("text:") else bytes.fromhex(data)
        t = t0
        while t <= t1:
            jobs.append((t, addr, b))
            t += per
    jobs.sort()
    start = time.time()
    c = None
    for _ in range(300):
        try:
            c = RspClient("127.0.0.1", 1234)
            break
        except OSError:
            time.sleep(0.5)
    if c is None:
        sys.exit("qemu_poke: no gdb stub on :1234")
    c.sock.settimeout(30)
    running = False
    for t, addr, b in jobs:
        d = t - (time.time() - start)
        if d > 0:
            time.sleep(d)
        try:
            if running:                       # after the first attach (which stops the guest) it is running again
                c.sock.sendall(b"\x03")
                c.read_packet()
            running = False
            if isinstance(b, int):
                raw = b""
                while len(raw) < b:
                    raw += bytes.fromhex(c.command("m%x,%x" % (addr + len(raw), min(0x100, b - len(raw)))))
                print("READ %x %s" % (addr, raw.hex()), flush=True)
                c.send_packet("c")
                running = True
                continue
            c.command("M%x,%x:%s" % (addr, len(b), b.hex()))
            c.send_packet("c")
            running = True
        except Exception as e:
            print("qemu_poke:", e, file=sys.stderr)
            return
    print("qemu_poke: done", len(jobs), "pokes")


if __name__ == "__main__":
    main()
