#!/usr/bin/env python3
"""Compile tests/test_grid.c (native cc) and compare krk_grid.c's locators with a reference.
Run inside the dm32uv-re Docker image (host cc may be unusable): see tools/build.sh for the mount."""
import hostcc
from hostlib import check, finish
exe = hostcc.build([hostcc.HERE / "test_grid.c"], flags=["-w"])

def ref(lat, lon):
    lon += 180; lat += 90
    s = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10)); lon %= 20; lat %= 10
    s += str(int(lon // 2)) + str(int(lat)); lon %= 2; lat %= 1
    s += chr(97 + int(lon * 12)) + chr(97 + int(lat * 24)); lon %= 1 / 12; lat %= 1 / 24
    return s + str(int(lon * 120)) + str(int(lat * 240))

def nmea(v, dd):
    a = abs(v); d = int(a); m = round((a - d) * 60, 4)
    return f"{d:0{dd}d}{m:07.4f}"

for lat, lon in [(41.714167, -72.726667), (48.1173, 11.516667), (-33.8688, 151.2093),
                 (64.1466, -21.9426), (-0.0001, -0.0001), (89.99, 179.99), (19.4326, -99.1332)]:
    # feed the reference the same rounded minutes the NMEA string carries
    la, lo = nmea(lat, 2), nmea(lon, 3)
    lat_r = (int(la[:2]) + float(la[2:]) / 60) * (1 if lat >= 0 else -1)
    lon_r = (int(lo[:3]) + float(lo[3:]) / 60) * (1 if lon >= 0 else -1)
    out = hostcc.run(exe, la, "N" if lat >= 0 else "S", lo, "E" if lon >= 0 else "W").split()
    r = ref(lat_r, lon_r)
    good = out[0] == r and out[1] == "4807.038"
    check(good, f"{lat:10.5f} {lon:11.5f}  got {out[0]}  ref {r}  field3={out[1]}")
finish(ok="", fail="")
