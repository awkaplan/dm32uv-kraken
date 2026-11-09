#!/usr/bin/env python3
"""The Kraken reader on a real radio: which fast line rates the cable holds, and how fast a backup is.

Does what the web installer does, from Python: waits for the boot ROM's NOTIFY as the radio powers on,
DOWNLOADs reader.bin into IRAM and RUNs it, then for each rate the UART can make exactly in ROM mode
(24 MHz / 16 / divisor: 1.5M, 750k, 500k, ...) switches to it, PINGs, reads all 1 MiB in CRC-checked
frames and compares every sector with the CRC-32 the radio computes itself. Read-only: the reader has
no command that writes flash, and the radio never transmits.

  python3 tests/reader_hw_test.py --port /dev/cu.usbserial-XXXX [--divisors 1,2,3] [--passes 1]
                                  [--baseline] [--save out/web-test/radio_backup.bin]

Needs pyserial and a built reader (tools/build.sh). Turn the radio off before starting; the script
asks for it to be turned on (normally, no keys held), and to be turned off at the end.
"""
import argparse
import json
import os
import pathlib
import struct
import sys
import time
import zlib

import serial

REPO = pathlib.Path(__file__).resolve().parent.parent
READER = REPO / "out" / "kraken" / "reader"
NOTIFY = bytes([0x02, 0x24, 0x00, 0x03])
DEBUG = bytes([0x02, 0x14, 0x00, 0x03])
OK = bytes([0x02, 0x05, 0x00, 0x03])
BASE = 115200
CLOCK = 24_000_000
SIZE = 0x100000
SECTOR = 0x1000
REQ = 0x10000
T_PING, T_BAUD, T_READ, T_SUMS = 0x01, 0x02, 0x03, 0x04
T_HELLO, T_PONG, T_OK, T_DATA, T_SUMSR, T_ERR = 0x80, 0x81, 0x82, 0x83, 0x84, 0x8F


class BadFrame(Exception):
    pass


class Link:
    def __init__(self, port):
        self.s = serial.Serial(port, BASE, timeout=0.05)
        self.buf = bytearray()

    def set_baud(self, baud):
        self.s.baudrate = baud
        self.s.reset_input_buffer()
        self.buf.clear()

    def write(self, b):
        self.s.write(b)
        self.s.flush()

    def _fill(self, end):
        d = self.s.read(max(1, self.s.in_waiting))
        if d:
            self.buf += d
        elif time.monotonic() > end:
            raise TimeoutError(f"{len(self.buf)} byte(s) buffered")

    def read(self, n, timeout=2.0):
        end = time.monotonic() + timeout
        while len(self.buf) < n:
            self._fill(end)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def wait_for(self, pat, timeout):
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(pat)
            if i >= 0:
                del self.buf[:i + len(pat)]
                return
            del self.buf[:max(0, len(self.buf) - len(pat))]
            self._fill(end)

    def discard(self, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self.s.read(max(1, self.s.in_waiting))
        self.buf.clear()

    # --- reader protocol ---
    def send(self, typ, payload=b""):
        body = bytes([typ]) + struct.pack("<H", len(payload)) + payload
        self.write(b"\x5a\xa5" + body + struct.pack("<I", zlib.crc32(body)))

    def frame(self, timeout=2.0):
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\x5a\xa5")
            if i >= 0:
                del self.buf[:i + 2]
                break
            del self.buf[:max(0, len(self.buf) - 1)]
            self._fill(end)
        head = self.read(3, max(0.05, end - time.monotonic()))
        n = struct.unpack("<H", head[1:])[0]
        if n > 4 + SECTOR:
            raise BadFrame(f"length {n}")
        rest = self.read(n + 4, max(0.2, end - time.monotonic()))
        if zlib.crc32(head + rest[:n]) != struct.unpack("<I", rest[n:])[0]:
            raise BadFrame("CRC")
        return head[0], rest[:n]

    def ping(self, timeout=1.0):
        tag = os.urandom(2)
        self.send(T_PING, tag)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                t, p = self.frame(max(0.05, end - time.monotonic()))
            except BadFrame:
                continue
            except TimeoutError:
                break
            if t == T_PONG and p == tag:
                return True
        return False


def start_reader(link, info, binary):
    print("Turn the radio ON now (normally, no keys held). Waiting up to 90 s for its boot ROM...", flush=True)
    link.wait_for(NOTIFY, 90)
    link.write(DEBUG)
    link.discard(0.05)
    for at in range(0, len(binary), 0x100):
        words = binary[at:at + 0x100]
        body = struct.pack(">II", info["load"] + at, len(words) // 4) + words
        link.write(bytes([0x02, 0x18, sum(body) & 0xFF, 0x03]) + body)
        if link.read(4, 1.5) != OK:
            sys.exit(f"DOWNLOAD at {at:#x} not acknowledged")
    body = struct.pack(">I", info["entry"])
    link.write(bytes([0x02, 0x1B, sum(body) & 0xFF, 0x03]) + body)
    if link.read(4, 1.5) != OK:
        sys.exit("RUN not acknowledged")
    t, p = link.frame(3)
    if t != T_HELLO or p[:3] != b"KRD":
        sys.exit(f"no HELLO (got type {t:#x})")
    div = struct.unpack("<H", p[4:6])[0]
    print(f"reader v{p[3]} running, ROM divisor {div} ({CLOCK / 16 / div:.0f} baud at 24 MHz)", flush=True)
    return div


def sums(link):
    link.send(T_SUMS, struct.pack("<III", 0, SIZE, SECTOR))
    t, p = link.frame(60)
    if t != T_SUMSR or len(p) != SIZE // SECTOR * 4:
        sys.exit(f"SUMS failed (type {t:#x})")
    return list(struct.unpack(f"<{SIZE // SECTOR}I", p))


def full_read(link, timeout):
    """All flash in 64 KB requests; a bad, cut or missing frame retries the request. (image, bad frames, retries)"""
    out = bytearray(SIZE)
    bad = retries = 0
    for base in range(0, SIZE, REQ):
        for attempt in range(8):
            link.send(T_READ, struct.pack("<II", base, REQ))
            try:
                for k in range(REQ // SECTOR):
                    t, p = link.frame(timeout)
                    a = struct.unpack("<I", p[:4])[0] if len(p) >= 4 else -1
                    if t != T_DATA or a != base + k * SECTOR or len(p) != 4 + SECTOR:
                        raise BadFrame(f"type {t:#x} at {a:#x}")
                    out[a:a + SECTOR] = p[4:]
                break
            except (BadFrame, TimeoutError) as e:
                bad += 1
                retries += 1
                print(f"    {base:#07x}: {e}; retrying", flush=True)
                time.sleep(0.05)
                link.discard(0.1)
                if not link.ping(1.0):
                    link.discard(0.3)
                    link.ping(1.0)
        else:
            raise RuntimeError(f"gave up at {base:#x}")
    return bytes(out), bad, retries


def try_rate(link, rom_div, div, ref, passes, keep):
    baud = round(CLOCK / 16 / div)
    row = {"baud": baud, "div": div, "ok": False}
    link.send(T_BAUD, struct.pack("<HH", div, 1500))
    t, _ = link.frame(2)
    if t != T_OK:
        sys.exit(f"BAUD not acknowledged (type {t:#x})")
    try:
        link.set_baud(baud)
    except (serial.SerialException, OSError, ValueError) as e:
        row["note"] = f"host refused the rate: {e}"
    else:
        # the reader is sending idle 0xFF bytes at the new rate: do they arrive as 0xFF? (radio -> host)
        raw = bytearray()
        end = time.monotonic() + 0.1
        while time.monotonic() < end:
            raw += link.s.read(max(1, link.s.in_waiting))
        ff = raw.count(0xFF)
        print(f"    radio->host at the new rate: {len(raw)} bytes in 100 ms, {ff} of them 0xFF "
              f"(expect ~{baud // 100}); first: {bytes(raw[:12]).hex(' ')}", flush=True)
        for _ in range(3):  # host -> radio
            if link.ping(0.3):
                row["ok"] = True
                break
    if not row["ok"]:
        row.setdefault("note", "no PONG at this rate")
        link.set_baud(BASE)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 8:  # the reader goes back to 115200 after 1.5 s without a PING
            if link.ping(0.5):
                print(f"    reader back at {BASE} after {time.monotonic() - t0:.1f} s", flush=True)
                return row
        raw = bytearray()
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            raw += link.s.read(max(1, link.s.in_waiting))
        print(f"    no PONG at {BASE} for 8 s; it sends {len(raw)} bytes in 0.5 s: {bytes(raw[:16]).hex(' ')}", flush=True)
        link.set_baud(baud)
        alive = link.ping(0.5)
        print(f"    PING at {baud}: {'answered (reader stuck at the new rate)' if alive else 'no answer'}", flush=True)
        sys.exit("lost the reader after a failed rate")
    lat = []
    for _ in range(20):
        t0 = time.monotonic()
        if not link.ping(1.0):
            row["note"] = "a PING went unanswered"
        lat.append(time.monotonic() - t0)
    row["ping_ms"] = 1000 * sorted(lat)[len(lat) // 2]
    row["passes"] = []
    for n in range(passes):
        t0 = time.monotonic()
        img, bad, retries = full_read(link, 2.0)
        secs = time.monotonic() - t0
        wrong = [i for i in range(SIZE // SECTOR) if zlib.crc32(img[i * SECTOR:(i + 1) * SECTOR]) != ref[i]]
        row["passes"].append({"secs": secs, "bad": bad, "retries": retries, "wrong_sectors": len(wrong)})
        print(f"    pass {n + 1}: {secs:.1f} s ({SIZE / secs / 1024:.0f} KB/s), {bad} bad frame(s), "
              f"{len(wrong)} sector(s) differ from the radio's CRCs", flush=True)
        if not wrong and keep is not None and keep[0] is None:
            keep[0] = img
    # back to 115200 for the next rate
    link.send(T_BAUD, struct.pack("<HH", rom_div, 1500))
    t, _ = link.frame(2)
    link.set_baud(BASE)
    if t != T_OK or not link.ping(1.0):
        time.sleep(1.7)
        if not link.ping(1.5):
            sys.exit("lost the reader going back to 115200")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="the cable's serial port, e.g. /dev/cu.usbserial-XXXX or COM3")
    ap.add_argument("--divisors", default="3,2,1", help="UART divisors to try, in order (1=1.5M, 2=750k, 3=500k, 4=375k, 6=250k)")
    ap.add_argument("--passes", type=int, default=1, help="full reads per working rate")
    ap.add_argument("--baseline", action="store_true", help="also do one full read at 115200 (~95 s)")
    ap.add_argument("--save", help="write the first fully verified read here")
    a = ap.parse_args()
    info = json.loads((READER / "reader.json").read_text())
    binary = (READER / "reader.bin").read_bytes()
    import hashlib
    if len(binary) != info["size"] or hashlib.sha256(binary).hexdigest() != info["sha256"]:
        sys.exit("reader.bin does not match reader.json; rebuild")

    link = Link(a.port)
    print(f"{a.port} open at {BASE}. Make sure the radio is OFF.", flush=True)
    link.discard(0.3)
    rom_div = start_reader(link, info, binary)
    t0 = time.monotonic()
    ref = sums(link)
    print(f"radio's own sector CRCs: {time.monotonic() - t0:.1f} s", flush=True)
    keep = [None] if a.save else None
    rows = []
    if a.baseline:
        print(f"{BASE} baud (baseline)", flush=True)
        t0 = time.monotonic()
        img, bad, _ = full_read(link, 3.0)
        secs = time.monotonic() - t0
        wrong = sum(zlib.crc32(img[i * SECTOR:(i + 1) * SECTOR]) != ref[i] for i in range(SIZE // SECTOR))
        print(f"    {secs:.1f} s, {bad} bad frame(s), {wrong} sector(s) differ", flush=True)
        rows.append({"baud": BASE, "div": rom_div, "ok": True, "passes": [{"secs": secs, "bad": bad, "wrong_sectors": wrong}]})
        if keep is not None and not wrong:
            keep[0] = img
    for div in (int(x) for x in a.divisors.split(",")):
        print(f"{round(CLOCK / 16 / div)} baud (divisor {div})", flush=True)
        r = try_rate(link, rom_div, div, ref, a.passes, keep)
        if not r["ok"]:
            print(f"    failed: {r['note']}", flush=True)
        rows.append(r)

    print("\nrate        works  ping   full read")
    for r in rows:
        if not r["ok"]:
            print(f"{r['baud']:>9}   no     -      {r.get('note', '')}")
            continue
        p = r["passes"]
        best = min(x["secs"] for x in p)
        errs = sum(x["bad"] for x in p)
        wrong = sum(x["wrong_sectors"] for x in p)
        ping = f"{r['ping_ms']:.1f}ms" if "ping_ms" in r else "-"
        print(f"{r['baud']:>9}   yes    {ping:<6} {best:5.1f} s, {errs} bad frame(s), {wrong} wrong sector(s) over {len(p)} pass(es)")
    if a.save:
        if keep[0] is None:
            print("nothing saved: no read matched the radio's CRCs")
        else:
            pathlib.Path(a.save).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(a.save).write_bytes(keep[0])
            print(f"saved {a.save} (sha256 {hashlib.sha256(keep[0]).hexdigest()[:16]})")
    print("\nDone. Turn the radio OFF (the reader runs until power-off).")


if __name__ == "__main__":
    main()
