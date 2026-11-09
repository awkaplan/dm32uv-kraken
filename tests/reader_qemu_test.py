#!/usr/bin/env python3
"""The Kraken reader (reader/) running in QEMU's DM-32 model, driven over UART0 in the reader protocol.

The boot ROM itself is not emulated: the test loads reader.bin into IRAM where the ROM would DOWNLOAD it,
puts UART0 in the state the ROM leaves it (divisor 13, IER.PTIME, FIFO on, its RUN reply still in the TX
FIFO), and starts the CPU at the reader's entry. A second implementation of the host side (Python, here)
then checks every command against the flash image: HELLO, PING, SUMS, READ (all of flash), a READ stopped
by a new command, BAUD with and without the PING at the new rate (QEMU ignores the divisor, so this checks
the protocol and the fallback timing, not the line rate), and the error replies.

Run inside the dm32uv-re Docker image after a build:  tools/build.sh --run 'python3 tests/reader_qemu_test.py'
"""
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mi_rng_test as m  # noqa: E402  (start_qemu-style helpers: setreg, wmem, RspClient)
from gdb_rsp import RspClient  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "out" / "kraken"
FLASH = OUT / "progmem_KRAKEN_patched.bin"
READER = OUT / "reader"
UART0 = 0x14030000
SECTOR = 0x1000
fails = 0


def ok(cond, what):
    global fails
    print(f"  {'PASS' if cond else 'FAIL'} {what}", flush=True)
    if not cond:
        fails += 1


class Host:
    def __init__(self, port):
        for _ in range(100):
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except OSError:
                time.sleep(0.1)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def read(self, n, timeout=10):
        end = time.time() + timeout
        while len(self.buf) < n:
            self.s.settimeout(max(0.05, end - time.time()))
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                d = b""
            if not d and time.time() > end:
                raise TimeoutError(f"got {len(self.buf)} of {n}")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, typ, payload=b""):
        body = bytes([typ]) + struct.pack("<H", len(payload)) + payload
        self.s.sendall(b"\x5a\xa5" + body + struct.pack("<I", zlib.crc32(body)))

    def frame(self, timeout=10):
        """(type, payload, bytes skipped before the sync)"""
        skipped, prev = 0, -1
        while True:
            b = self.read(1, timeout)[0]
            if prev == 0x5A and b == 0xA5:
                break
            if prev >= 0:
                skipped += 1
            prev = b
        head = self.read(3, timeout)
        n = struct.unpack("<H", head[1:3])[0]
        rest = self.read(n + 4, timeout)
        crc_ok = zlib.crc32(head + rest[:n]) == struct.unpack("<I", rest[n:])[0]
        if not crc_ok:
            raise ValueError("frame CRC")
        return head[0], rest[:n], skipped


def main():
    flash = FLASH.read_bytes()
    info = json.loads((READER / "reader.json").read_text())
    w = tempfile.mkdtemp(prefix="reader_")
    gport = int(os.environ.get("DM32_GDB_PORT", "1234"))
    sport = gport + 1000
    cmd = ["qemu-system-cskyv2", "-M", "dm32", "-cpu", "ck803", "-display", "none", "-monitor", "none",
           "-serial", f"tcp:127.0.0.1:{sport},server,nowait", "-S", "-gdb", f"tcp::{gport}",
           "-kernel", str(FLASH), "-device", f"loader,file={READER / 'reader.bin'},addr={info['load']:#x}"]
    proc = subprocess.Popen(cmd, stdout=open(w + "/qemu.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                c = RspClient("127.0.0.1", gport, timeout=30)
                break
            except OSError:
                time.sleep(0.1)
        host = Host(sport)
        # UART0 as the boot ROM leaves it: LCR=0x83, DLL=13, LCR=0x03, IER=0x80 (PTIME), FCR=0x01; then the
        # OK it sends for RUN, still going out when it jumps to the reader
        # (QEMU resets DLH to 4; on the radio it is 0, or the ROM's line would not run at 115200)
        for off, v in ((0x0C, 0x83), (0x00, 13), (0x04, 0), (0x0C, 0x03), (0x04, 0x80), (0x08, 0x01)):
            m.wmem(c, UART0 + off, struct.pack("<I", v))
        for b in (0x02, 0x05, 0x00, 0x03):
            m.wmem(c, UART0, struct.pack("<I", b))
        m.setreg(c, 0x48, info["entry"])
        c.send_packet("c")

        print("hello")
        ok(host.read(4) == b"\x02\x05\x00\x03", "the ROM's RUN reply got out before the reader reset the FIFOs")
        t, p, _ = host.frame()
        ok(t == 0x80 and p[:3] == b"KRD", f"HELLO {p[:3]!r} version {p[3] if len(p) > 3 else '?'}")
        ok(struct.unpack("<H", p[4:6])[0] == 13, "reports divisor 13")

        print("ping")
        host.send(0x01, b"\x34\x12")
        t, p, _ = host.frame()
        ok((t, p) == (0x81, b"\x34\x12"), "PONG echoes")

        print("sums")
        t0 = time.time()
        host.send(0x04, struct.pack("<III", 0, 0x100000, SECTOR))
        t, p, _ = host.frame(timeout=60)
        want = b"".join(struct.pack("<I", zlib.crc32(flash[s:s + SECTOR])) for s in range(0, 0x100000, SECTOR))
        ok(t == 0x84 and p == want, f"256 sector CRC-32s of all flash match ({time.time() - t0:.1f} s)")
        host.send(0x04, struct.pack("<III", 0xC000, 0x1234, 0x400))
        t, p, _ = host.frame()
        want = b"".join(struct.pack("<I", zlib.crc32(flash[a:min(a + 0x400, 0xC000 + 0x1234)])) for a in range(0xC000, 0xC000 + 0x1234, 0x400))
        ok(t == 0x84 and p == want, "a partial last piece is summed over what is left")

        print("read")
        t0 = time.time()
        got = bytearray()
        for a in range(0, 0x100000, 0x10000):
            host.send(0x03, struct.pack("<II", a, 0x10000))
            for k in range(16):
                t, p, _ = host.frame()
                if t != 0x83 or struct.unpack("<I", p[:4])[0] != a + k * SECTOR:
                    ok(False, f"DATA frame order at {a + k * SECTOR:#x}")
                    return
                got += p[4:]
        ok(bytes(got) == flash, f"READ of all 1 MiB matches the image ({time.time() - t0:.1f} s)")

        print("a new command stops a READ")
        host.send(0x03, struct.pack("<II", 0, 0x100000))
        host.frame()
        host.send(0x01, b"\x99\x99")
        frames = 0
        while True:
            t, p, _ = host.frame()
            if t == 0x81:
                break
            frames += 1
        ok(p == b"\x99\x99" and frames < 32, f"PONG after {frames} more DATA frame(s), not the whole MiB")

        print("baud")
        host.send(0x02, struct.pack("<HH", 1, 300))
        t, p, _ = host.frame()
        ok(t == 0x82, "OK before switching")
        host.send(0x01, b"\x01\x02")
        t, p, skipped = host.frame()
        ok(t == 0x81 and p == b"\x01\x02", f"PING at the new rate answered (after {skipped} fill bytes)")
        host.send(0x02, struct.pack("<HH", 2, 100))  # no PING this time: it must time out and go back
        t, p, _ = host.frame()
        ok(t == 0x82, "OK")
        expect_fill = 100 * 1152 // 100 * 13 // 2  # timed against the ROM's divisor 13, not the current 1
        fill = 0
        deadline = time.time() + 20
        while time.time() < deadline:
            b = host.read(1)
            if b != b"\xff":
                host.buf = b + host.buf
                break
            fill += 1
            if fill == expect_fill:
                break
        ok(abs(fill - expect_fill) <= 2, f"{fill} fill bytes timed the 100 ms wait (expected {expect_fill})")
        time.sleep(0.2)
        host.buf = b""
        host.send(0x01, b"\x05\x06")
        t, p, _ = host.frame()
        ok(t == 0x81 and p == b"\x05\x06", "back at the old rate: PING answered")
        # a host at the wrong rate sends garbage during the wait: the wait must not stretch
        host.send(0x02, struct.pack("<HH", 3, 100))
        t, p, _ = host.frame()
        ok(t == 0x82, "OK")
        junk = bytearray(os.urandom(600))
        junk[100:105] = b"\x5a\xa5\x01\x40\x00"  # a sync and a header that promise a payload, then nothing
        junk[300:305] = b"\x5a\xa5\x01\xff\xff"  # an over-long one
        host.s.sendall(bytes(junk))
        expect_fill = 100 * 1152 // 100 * 13 // 3
        fill = 0
        deadline = time.time() + 20
        while time.time() < deadline and fill < expect_fill + 50:
            try:
                b = host.read(1, timeout=1)
            except TimeoutError:
                break
            if b != b"\xff":
                host.buf = b + host.buf
                break
            fill += 1
        ok(abs(fill - expect_fill) <= 2, f"garbage during the wait: still {fill} fill bytes (expected {expect_fill})")
        time.sleep(0.2)
        host.buf = b""
        host.send(0x01, b"\x07\x08")
        for _ in range(4):
            t, p, _ = host.frame()
            if t == 0x81:
                break
        ok(t == 0x81 and p == b"\x07\x08", "back at the old rate after the garbage: PING answered")
        c.sock.sendall(b"\x03")  # gdb interrupt: halt to read the divisor
        c.read_packet()
        m.wmem(c, UART0 + 0x0C, struct.pack("<I", 0x83))
        dll = struct.unpack("<I", m.read_mem(c, UART0, 4))[0] & 0xFF
        m.wmem(c, UART0 + 0x0C, struct.pack("<I", 0x03))
        ok(dll == 1, f"divisor restored to the one before the failed switch ({dll})")
        c.send_packet("c")

        print("errors")
        host.s.sendall(b"\x5a\xa5\x01\x00\x00\xde\xad\xbe\xef")
        t, p, _ = host.frame()
        ok((t, p) == (0x8F, b"\x01"), "bad CRC -> ERR 1")
        host.send(0x03, struct.pack("<II", 0xFF000, 0x2000))
        t, p, _ = host.frame()
        ok((t, p) == (0x8F, b"\x02"), "READ past the end of flash -> ERR 2")
        host.send(0x77)
        t, p, _ = host.frame()
        ok((t, p) == (0x8F, b"\x03"), "unknown command -> ERR 3")
    finally:
        proc.kill()
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
