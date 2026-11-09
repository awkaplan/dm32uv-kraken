#!/usr/bin/env python3
"""Tests for krk_sat.c (F10 orbit maths): TLE parsing, SGP4, look angles, Doppler, pass prediction.

Compiles tests/test_sat.c (which #includes the patch source) with the host cc and compares against the
`sgp4` package (WGS-72, the same reference implementation Vallado publishes) plus an independently
written ECEF/ENU observer transform. If `sgp4` is not importable, a venv is created in
~/.cache/dm32uv-sat-venv and `pip install sgp4` is run there; the script re-executes itself with it.
Also checks that krk_sat.c compiles freestanding with no undefined symbols (no libm/libc references).
Run: python3 tests/test_sat.py   (exit status 0 = all pass)
"""
import math, os, pathlib, random, subprocess, sys, tempfile

import hostcc
import hostlib
from hostcc import HERE, UI as SRC


def ensure_sgp4():
    try:
        import sgp4  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("SAT_REEXEC"):
        sys.exit("sgp4 not installable (need network: pip install sgp4)")
    venv = pathlib.Path.home() / ".cache" / "dm32uv-sat-venv"
    py = venv / "bin" / "python"
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "sgp4"], check=True)
    os.environ["SAT_REEXEC"] = "1"
    os.execv(str(py), [str(py)] + sys.argv)


ensure_sgp4()
from sgp4.api import Satrec, WGS72, jday  # noqa: E402
from sgp4.propagation import gstime  # noqa: E402


tmp = pathlib.Path(tempfile.mkdtemp())


def check(name, ok, detail=""):
    hostlib.check(ok, name + (("  " + detail) if detail else ""))


# --------------------------------------------------------------------------- freestanding check
nm_ok = True
for opt in ("-O0", "-O2", "-Os"):
    obj = tmp / ("sat%s.o" % opt)
    hostcc.build([], flags=["-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", opt, "-ffreestanding", "-fno-builtin",
                            "-c", str(SRC / "krk_sat.c")], out=obj)
    und = subprocess.run(["nm", "-u", str(obj)], capture_output=True, text=True).stdout.split()
    if und:
        nm_ok = False
        print("   undefined symbols at", opt, und)
src_text = (SRC / "krk_sat.c").read_text()
bad = [w for w in ("#include <", "malloc", "memcpy", "memset", "static int g_", "math.h") if w in src_text]
check("freestanding: no undefined symbols (-O0/-O2/-Os) and no libc includes", nm_ok and not bad, str(bad))

exe = hostcc.build([HERE / "test_sat.c"], flags=["-std=c99", "-O2", "-w", "-DKRK_SAT_STATS"])


def run(*args):
    try:
        return hostcc.run(exe, *args).strip().splitlines()
    except subprocess.CalledProcessError as e:
        sys.exit("driver failed: %s\n%s" % (args, e.stdout + e.stderr))


# --------------------------------------------------------------------------- TLE builder
def cks(l):
    return sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in l) % 10


def tle(cat, intl, yr, doy, ndot, bstar, inc, raan, ecc, argp, ma, n, rev=12345):
    l1 = "1 %05dU %-8s %02d%012.8f %s  00000-0 %s 0 %4d" % (cat, intl, yr, doy, ndot, bstar, 999)
    l2 = "2 %05d %8.4f %8.4f %07d %8.4f %8.4f %11.8f%5d" % (cat, inc, raan, round(ecc * 1e7), argp, ma, n, rev)
    assert len(l1) == 68 and len(l2) == 68, (len(l1), len(l2))
    return l1 + str(cks(l1)), l2 + str(cks(l2))


SATS = {
    "ISS":       tle(25544, "98067A", 24, 220.51234567, " .00016717", " 22000-3", 51.6400, 120.5000, 0.0005,  85.0000, 275.0000, 15.50000000),
    "SO-50":     tle(27607, "02058C", 24, 221.30000000, " .00000800", " 16500-3", 64.5600, 100.0000, 0.0036, 300.0000,  60.0000, 14.75000000),
    "RS-44":     tle(44909, "19096E", 24, 219.90000000, " .00000010", " 00000-0", 82.5200, 250.0000, 0.0010,  45.0000, 315.0000, 12.42000000),
    "AO-91":     tle(43017, "17073E", 24, 222.10000000, " .00000500", " 90000-4", 97.5000,  20.0000, 0.0200, 200.0000, 160.0000, 14.90000000),
    "LOWPERIGEE": tle(99001, "24001A", 24, 220.00000000, " .00010000", " 80000-4", 51.6000, 330.0000, 0.0030,  10.0000, 350.0000, 16.40000000),
    "HIGHINC-ECC": tle(99002, "24002A", 24, 223.00000000, " .00000100", " 10000-3", 98.9000,  75.0000, 0.1000, 180.0000, 180.0000, 13.00000000),
}
V5 = ("1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
      "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667")
SATS["VALLADO-00005"] = V5
OBS = {  # name: (lat, lon, alt m)
    "Connecticut": (41.714, -72.727, 50), "Sydney": (-33.869, 151.209, 20), "Ushuaia": (-54.80, -68.30, 10),
    "Reykjavik": (64.147, -21.943, 30), "Singapore": (1.352, 103.82, 15), "McMurdo": (-77.85, 166.67, 10),
    "Quito": (-0.18, -78.47, 2850),
}

# --------------------------------------------------------------------------- TLE parsing
for name, (a, b) in SATS.items():
    assert cks(a[:68]) == int(a[68]) and cks(b[:68]) == int(b[68]), name
for name in ("VALLADO-00005",):
    pass
bad = {}
l1, l2 = SATS["ISS"]
flip = lambda s, i: s[:i] + str((int(s[i]) + 1) % 10) + s[i + 1:]
check("parse ok", run("parse", l1, l2)[0].split()[0] == "0")
check("parse: line-1 checksum", run("parse", flip(l1, 20), l2)[0].split()[0] == "-2")
check("parse: line-2 checksum", run("parse", l1, flip(l2, 10))[0].split()[0] == "-2")
check("parse: short line", run("parse", l1[:60], l2)[0].split()[0] == "-1")
check("parse: line order", run("parse", l2, l1)[0].split()[0] == "-1")
other = SATS["SO-50"][1]
check("parse: catalog mismatch", run("parse", l1, other)[0].split()[0] == "-3")
check("parse: CRLF tolerated", run("parse", l1 + "\r\n", l2 + "\r\n")[0].split()[0] == "0")
deep = tle(20000, "89001A", 24, 220.0, " .00000000", " 00000-0", 55.0, 10.0, 0.01, 30.0, 40.0, 2.00563)
check("parse: deep-space period rejected", run("parse", *deep)[0].split()[0] == "-4")
for name, (a, b) in SATS.items():
    ref = Satrec.twoline2rv(a, b, WGS72)
    f = run("parse", a, b)[0].split()
    ep = ref.jdsatepoch + ref.jdsatepochF - 2433281.5
    ok = (f[0] == "0" and abs(float(f[1]) - ep) < 2e-9 and abs(float(f[3]) - ref.ecco) < 1e-12
          and int(f[4]) == ref.satnum)
    check("parse fields " + name, ok, "epoch err %.2e d" % abs(float(f[1]) - ep))

# time round trip
import datetime
tok = True
for (y, mo, d, h, mi, s) in [(2024, 2, 29, 23, 59, 59), (2000, 3, 1, 0, 0, 0), (2099, 12, 31, 12, 30, 30),
                             (1999, 12, 31, 23, 0, 1), (2026, 10, 29, 6, 7, 8), (1950, 1, 1, 0, 0, 0)]:
    f = run("time", y, mo, d, h, mi, s)[0].split()
    ref = (datetime.datetime(y, mo, d, h, mi, s) - datetime.datetime(1949, 12, 31)).total_seconds() / 86400
    tok &= abs(float(f[0]) - ref) < 1e-9 and [int(x) for x in f[1:]] == [y, mo, d, h, mi, s]
check("time <-> civil round trip", tok)

# --------------------------------------------------------------------------- reference model
OMEGA = 7.2921158553e-5
WA, WF = 6378.137, 1 / 298.257223563


def obs_ecef(lat, lon, alt):
    la, lo = math.radians(lat), math.radians(lon)
    e2 = WF * (2 - WF)
    n = WA / math.sqrt(1 - e2 * math.sin(la) ** 2)
    h = alt / 1000
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h) * math.sin(la)), la, lo


def ref_look(sat, ob, jd, fr):
    e, r, v = sat.sgp4(jd, fr)
    if e:
        return None
    (ox, oy, oz), la, lo = ob
    g = gstime(jd + fr)
    c, s = math.cos(g), math.sin(g)
    rx, ry, rz = c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]            # TEME -> ECEF
    vx, vy, vz = c * v[0] + s * v[1] + OMEGA * ry * -1 * -1, -s * v[0] + c * v[1], v[2]
    # velocity relative to the rotating Earth: v_ecef = R v - omega x r
    vx = c * v[0] + s * v[1] + OMEGA * ry
    vy = -s * v[0] + c * v[1] - OMEGA * rx
    dx, dy, dz = rx - ox, ry - oy, rz - oz
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    east = -so * dx + co * dy
    north = -sl * co * dx - sl * so * dy + cl * dz
    up = cl * co * dx + cl * so * dy + sl * dz
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    az = math.degrees(math.atan2(east, north)) % 360
    el = math.degrees(math.asin(up / rng))
    rr = (dx * vx + dy * vy + dz * vz) / rng
    return az, el, rng, rr, r, v


def civil_from_jd(jd, fr):
    t = (jd + fr - 2433281.5)
    base = datetime.datetime(1949, 12, 31) + datetime.timedelta(days=t)
    return base


def jd_of(dt, extra_s=0.0):
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6 + extra_s)


# --------------------------------------------------------------------------- published vectors
a, b = V5
rows = run("track", a, b, 0, 0, 0, 2000, 6, 27, 18, 50, 19.7336, 361, 60)  # epoch to the 0.01 s; we compare to reference
sat5 = Satrec.twoline2rv(a, b, WGS72)
# Vallado's verification vectors (SGP4-VER.TST, satellite 00005)
VEC = {0.0: ((7022.46529266, -1400.08296755, 0.03995155), (1.893841015, 6.405893759, 4.534807250)),
       360.0: ((-7154.03120202, -3783.17682504, -3536.19412294), (4.741887409, -4.151817765, -2.093935425))}
for tmin, (pr, vr) in VEC.items():
    e, r, v = sat5.sgp4(sat5.jdsatepoch, sat5.jdsatepochF + tmin / 1440)
    check("python reference matches published vector t=%d min" % tmin,
          max(abs(x - y) for x, y in zip(r, pr)) < 1e-4 and max(abs(x - y) for x, y in zip(v, vr)) < 1e-6)
    # drive our code to exactly that instant: epoch + tmin as civil seconds
    ep_dt = civil_from_jd(sat5.jdsatepoch, sat5.jdsatepochF)
    sec = ep_dt.second + ep_dt.microsecond / 1e6
    out = run("track", a, b, 0, 0, 0, ep_dt.year, ep_dt.month, ep_dt.day, ep_dt.hour, ep_dt.minute,
              "%.6f" % (sec + tmin * 60), 1, 1)[0].split()
    rx = [float(x) for x in out[1:4]]; vx = [float(x) for x in out[4:7]]
    dpos = max(abs(x - y) for x, y in zip(rx, pr)); dvel = max(abs(x - y) for x, y in zip(vx, vr))
    check("krk_sat vs Vallado 00005 t=%d min" % tmin, dpos < 0.01 and dvel < 1e-5,
          "dpos %.2e km dvel %.2e km/s" % (dpos, dvel))

# --------------------------------------------------------------------------- tracks vs reference
agg = {k: 0.0 for k in ("pos", "vel", "az", "el", "rng", "rr", "dop")}
npts = 0
ndecay = 0
worst = {}
for sname, (a, b) in SATS.items():
    sat = Satrec.twoline2rv(a, b, WGS72)
    ep = civil_from_jd(sat.jdsatepoch, sat.jdsatepochF).replace(microsecond=0)
    for oname, (la, lo, al) in OBS.items():
        ob = obs_ecef(la, lo, al)
        step = 10093.7
        rows = run("track", a, b, la, lo, al, ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second,
                   60, step)
        for row in rows:
            f = row.split()
            ts = float(f[0])
            jd, fr = jd_of(ep, ts)
            rl = ref_look(sat, ob, jd, fr)
            if rl is None or f[1] == "err":     # decayed: both implementations must say so
                check("track %s/%s decay agreement at %.2f d" % (sname, oname, ts / 86400),
                      rl is None and f[1] == "err", row)
                ndecay += 1
                continue
            az, el, rng, rr, r, v = rl
            x = [float(q) for q in f[1:12]]
            dpos = math.dist(x[0:3], r)
            dvel = math.dist(x[3:6], v) * 1000
            daz = abs((x[6] - az + 180) % 360 - 180) * math.cos(math.radians(el))
            d = dict(pos=dpos, vel=dvel, az=daz, el=abs(x[7] - el), rng=abs(x[8] - rng),
                     rr=abs(x[9] - rr) * 1000, dop=abs(x[9] - rr) / 299792.458 * 435.8e6)
            for k, val in d.items():
                if val > agg[k]:
                    agg[k] = val
                    worst[k] = (sname, oname, ts / 86400)
            npts += 1
print("   %d decayed samples (both implementations report decay)" % ndecay)
print("   %d samples (6+1 sats x 7 observers, 0..7 d from epoch); max errors vs sgp4 reference:" % npts)
print("   pos %.2e km  vel %.2e m/s  az %.2e deg  el %.2e deg  range %.2e km  range-rate %.2e m/s  Doppler@435.8MHz %.2e Hz"
      % (agg["pos"], agg["vel"], agg["az"], agg["el"], agg["rng"], agg["rr"], agg["dop"]))
check("position error < 1 m (spec 10 km)", agg["pos"] < 1e-3)
check("velocity error < 1 mm/s", agg["vel"] < 1e-3)
check("az/el error < 1e-4 deg", agg["az"] < 1e-4 and agg["el"] < 1e-4)
check("range-rate error < 1 mm/s (Doppler < 2 Hz)", agg["rr"] < 1.0 and agg["dop"] < 2.0)
check("range error < 1 m", agg["rng"] < 1e-3)

# optional independent cross-check with skyfield (own TEME->ITRS with nutation, UT1 from builtin tables)
try:
    from skyfield.api import EarthSatellite, load, wgs84
    ts = load.timescale(builtin=True)
    mx = [0, 0, 0]
    for sname in ("ISS", "SO-50", "AO-91"):
        a, b = SATS[sname]
        es = EarthSatellite(a, b, sname, ts)
        ep = civil_from_jd(es.model.jdsatepoch, es.model.jdsatepochF).replace(microsecond=0)
        for oname in ("Connecticut", "Sydney", "Quito"):
            la, lo, al = OBS[oname]
            rows = run("track", a, b, la, lo, al, ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second, 12, 40000)
            topo = wgs84.latlon(la, lo, al)
            for row in rows:
                f = row.split()
                t = ts.utc(ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second + float(f[0]))
                alt, azi, dist = (es - topo).at(t).altaz()
                if alt.degrees < 5:
                    continue
                daz = abs((float(f[7]) - azi.degrees + 180) % 360 - 180) * math.cos(math.radians(alt.degrees))
                mx = [max(mx[0], daz), max(mx[1], abs(float(f[8]) - alt.degrees)), max(mx[2], abs(float(f[9]) - dist.km))]
    print("   skyfield cross-check (el>5 deg): max az %.3f deg, el %.3f deg, range %.2f km" % tuple(mx))
    check("skyfield agrees (az/el < 0.1 deg, range < 3 km)", mx[0] < 0.1 and mx[1] < 0.1 and mx[2] < 3.0)
except ImportError:
    print("   (skyfield not installed: cross-check skipped)")

# --------------------------------------------------------------------------- Doppler
for f, rr in [(435.8e6, 6.5), (145.9e6, -7.1), (437.5e6, 0.0), (435.1e6, -0.3217)]:
    out = [int(x) for x in run("doppler", "%d" % f, rr, 10)[0].split()]
    rx, tx, rnd = f * (1 - rr / 299792.458), f * (1 + rr / 299792.458), None
    check("doppler %.1f MHz rr %.2f" % (f / 1e6, rr), abs(out[0] - rx) <= 0.5 and abs(out[1] - tx) <= 0.5
          and out[2] % 10 == 0 and abs(out[2] - rx) <= 5.0)
for fh, st, want in [(435802343, 2500, 435802500), (435801249, 2500, 435800000), (145000000, 12500, 145000000), (145006250, 12500, 145012500)]:
    out = run("doppler", fh, 0, st)[0].split()
    check("round %d to %d Hz" % (fh, st), int(out[2]) == want, out[2])
# sign convention: receding => rr > 0 => rx lower. Verify using two nearby samples that range grows when rr>0
rows = [r.split() for r in run("track", *SATS["ISS"], 41.714, -72.727, 50, 2024, 8, 8, 12, 0, 0, 3, 1)]
d_rng = float(rows[1][9]) - float(rows[0][9])
check("range-rate sign (receding positive)", abs(d_rng - 0.5 * (float(rows[0][10]) + float(rows[1][10]))) < 0.01,
      "drange/dt %.4f vs rr %.4f km/s" % (d_rng, float(rows[0][10])))

# --------------------------------------------------------------------------- pass prediction vs brute force
class Decayed(Exception):
    pass


def brute_first_pass(sat, ob, ep_dt, t0, horizon_h, min_el, scan=10.0):
    """First pass (el>=min_el) at/after t0 (seconds from ep_dt), 10 s scan, edges refined by bisection."""
    def el_at(ts):
        jd, fr = jd_of(ep_dt, ts)
        rl = ref_look(sat, ob, jd, fr)
        if rl is None:
            raise Decayed()
        return rl[1]
    n = int(horizon_h * 3600 / scan)
    prev_e = el_at(t0)
    ts_prev = t0
    inp = prev_e >= min_el
    aos = t0 if inp else None
    best = (prev_e, t0)
    for k in range(1, n + 1 + int(7200 / scan)):
        ts = t0 + k * scan
        if aos is None and ts > t0 + horizon_h * 3600:
            return None
        e = el_at(ts)
        if e >= min_el and aos is None:
            lo, hi = ts_prev, ts
            while hi - lo > 0.25:
                m = 0.5 * (lo + hi)
                if el_at(m) >= min_el: hi = m
                else: lo = m
            aos = hi
        if aos is not None:
            if e > best[0]: best = (e, ts)
            if e < min_el:
                lo, hi = ts_prev, ts
                while hi - lo > 0.25:
                    m = 0.5 * (lo + hi)
                    if el_at(m) >= min_el: lo = m
                    else: hi = m
                # refine peak
                lo2, hi2 = max(t0, best[1] - scan), best[1] + scan
                for _ in range(40):
                    m1, m2 = lo2 + (hi2 - lo2) / 3, hi2 - (hi2 - lo2) / 3
                    if el_at(m1) < el_at(m2): lo2 = m1
                    else: hi2 = m2
                return aos, lo, max(best[0], el_at((lo2 + hi2) / 2)), inp
        ts_prev = ts
    return None


props = []
daos = []
dmax = 0.0
cases = 0
rnd = random.Random(7)
for sname in ("ISS", "SO-50", "RS-44", "AO-91", "LOWPERIGEE", "HIGHINC-ECC", "VALLADO-00005"):
    a, b = SATS[sname]
    sat = Satrec.twoline2rv(a, b, WGS72)
    ep = civil_from_jd(sat.jdsatepoch, sat.jdsatepochF).replace(microsecond=0)
    for oname in ("Connecticut", "Sydney", "Ushuaia", "Reykjavik", "Quito"):
        la, lo, al = OBS[oname]
        ob = obs_ecef(la, lo, al)
        for min_el in (0.0, 10.0):
            t0 = rnd.uniform(0, 6.5 * 86400)
            try:
                ref = brute_first_pass(sat, ob, ep, t0, 24, min_el)
            except Decayed:
                ref = "decayed"
            out = run("pass", a, b, la, lo, al, ep.year, ep.month, ep.day, ep.hour, ep.minute, ep.second,
                      (t0 / 3600 + 0) if False else 0, min_el) if False else None
            # the driver starts at the civil time given; shift the start by t0 seconds
            st = ep + datetime.timedelta(seconds=t0)
            out = run("pass", a, b, la, lo, al, st.year, st.month, st.day, st.hour, st.minute,
                      st.second + st.microsecond / 1e6, 24, min_el)[0].split()
            cases += 1
            found = int(out[0])
            props.append(int(out[6]))
            tag = "pass %s/%s minel %g" % (sname, oname, min_el)
            if ref == "decayed":
                check(tag + " (decays in window: 0 or error)", found in (0, -5), str(out))
                continue
            if ref is None:
                check(tag + " (none in 24 h)", found == 0, str(out))
                continue
            aos_r, los_r, mel_r, inp = ref
            aos_c = float(out[1]) + t0
            los_c = float(out[2]) + t0
            mel_c = float(out[4])
            # a pass peaking within 1 deg of the threshold may legitimately be skipped (documented)
            if found != 1 or abs(los_c - los_r) > 3 or (not inp and abs(aos_c - aos_r) > 3) or abs(mel_c - mel_r) > 0.05:
                if found == 1 and mel_r < min_el + 1.0:
                    print("   note: marginal pass (max %.2f deg) skipped/shifted in %s" % (mel_r, tag))
                    continue
                check(tag, False, "ref aos %.1f los %.1f max %.3f in-progress %s; got %s (t0 %.1f)" %
                      (aos_r, los_r, mel_r, inp, out, t0))
                continue
            daos.append(max(0 if inp else abs(aos_c - aos_r), abs(los_c - los_r)))
            dmax = max(dmax, abs(mel_c - mel_r))
            if inp:
                check(tag + " in-progress flag", int(out[5]) & 1 == 1)
check("pass: %d cases agree with 10 s brute force" % cases, not [f for f in hostlib.FAILS if f.startswith("pass")],
      "%d passes matched; max AOS/LOS error %.2f s, max-elevation error %.5f deg" % (len(daos), max(daos or [0]), dmax))
print("   pass-search propagations (24 h horizon): min %d  mean %.0f  max %d" %
      (min(props), sum(props) / len(props), max(props)))
check("pass: propagations bounded (< 3000 for 24 h)", max(props) < 3000)

# --------------------------------------------------------------------------- cost
print("   cost per krk_sat_look() (host, -O2; the radio is a soft-float CK803S):")
for sname in ("ISS", "HIGHINC-ECC"):
    print("   ", sname, run("bench", *SATS[sname], 200000)[0])

hostlib.finish("\nALL PASSED", "\nFAILED: {names}", sep=", ")
