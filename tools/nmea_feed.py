#!/usr/bin/env python3
"""Stream synthetic NMEA (RMC + GGA, 1 Hz) into the emulator's UART1 (the GPS port) over TCP.

Usage: nmea_feed.py [--port 5556] [--lat 41.714167] [--lon -72.726667] [--seconds 600]
Pair with qemu_run.py --serial1 tcp:127.0.0.1:5556,server,nowait (tools/qemu_ui_run.sh does this
when NMEA=1). Default position is W1AW (grid FN31pr21).
"""
import argparse
import socket
import time


def nmea(body):
    x = 0
    for c in body.encode():
        x ^= c
    return f"${body}*{x:02X}\r\n".encode()


def dm(v, deg_digits):
    a = abs(v)
    d = int(a)
    return f"{d:0{deg_digits}d}{(a - d) * 60:07.4f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5556)
    ap.add_argument("--lat", type=float, default=41.714167)
    ap.add_argument("--lon", type=float, default=-72.726667)
    ap.add_argument("--seconds", type=int, default=600)
    a = ap.parse_args()
    ns, ew = ("N" if a.lat >= 0 else "S"), ("E" if a.lon >= 0 else "W")
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", a.port), timeout=2)
            break
        except OSError:
            time.sleep(0.2)
    else:
        raise SystemExit("nmea_feed: could not connect")
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        t = time.gmtime()
        hms = time.strftime("%H%M%S", t) + ".00"
        dmy = time.strftime("%d%m%y", t)
        lat, lon = dm(a.lat, 2), dm(a.lon, 3)
        try:
            s.sendall(nmea(f"GNRMC,{hms},A,{lat},{ns},{lon},{ew},0.0,0.0,{dmy},,,A"))
            s.sendall(nmea(f"GNGGA,{hms},{lat},{ns},{lon},{ew},1,08,0.9,40.0,M,-34.0,M,,"))
        except OSError:
            break
        time.sleep(1)


if __name__ == "__main__":
    main()
