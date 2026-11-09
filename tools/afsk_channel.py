#!/usr/bin/env python3
"""Channel model of the DM-32UV over-the-air receive chain, for stress-testing the AFSK demodulator
(patches/kraken_ui/krk_afsk.c).  Pure python (stdlib only; runs in the Docker image, which
has no numpy).

    channel(samples, rms=160.0, twist_db=None, snr_db=None, noise_rms=4.0, dc=-10.9, seed=0) -> list[int]

samples: clean audio, 8 kHz, any scale, silence = exact zeros around the burst (the burst gate is
detected from that).  Returns int16-range ints at the radio's level (burst RMS = `rms`).
Helpers: cpfsk(tones, ...) renders a tone list (0 = 1200 Hz, 1 = 2200 Hz, 1 per bit, as
krk_ax25_tones / out/expected_tones_*.txt) to phase-continuous AFSK; random_packet(seed) builds a
random AX.25 UI frame and its tones + the line the decoder should print.

MODEL (cascade, fitted in this order)
  1. Burst gate smoothed by a one-pole (tau = ATTACK samples): the key-up/squelch ramp.
  2. Two general biquads (pole/zero pairs, coefficients in _BQ) = the radio's band-pass + de-emphasis
     + mic/speaker path.  Net amplitude 2200/1200 = -3.1 dB (twist), group delay 1200 Hz 1.9 / 2200 Hz
     0.5 samples (before 3.).
  3. A second-order all-pass (_AP: f0 = 900 Hz, r = 0.8): flat magnitude, group-delay bump of
     ~+3.5 samples at 1200 Hz vs ~+0.7 at 2200 Hz.  This is what makes the 1200 Hz energy arrive ~2.5
     samples later than the 2200 Hz energy (the skew krk_afsk.c's RELDLY2 undoes).
  4. Optional twist re-trim (a one-pole de-emphasis or one-zero pre-emphasis) to hit twist_db net.
  5. Scale to burst RMS `rms`, add white Gaussian noise (noise_rms, or rms / snr_db), DC, round, clip.

HOW EACH PART WAS FITTED (all from out/c2_b0.pcm, c2_b1.pcm, c2_b2.pcm = the three known-content
bursts of capture2, tones out/expected_tones_n0call9.txt, bit-0 sample offsets 1998/1603/1939 from
out/align.pkl; fit scripts were scratch work, summarised here)
  * TX phase: the arg of the 2200 Hz component over successive preamble flags advances +55..+64 deg per
    flag in all three bursts = exactly what continuous-phase FSK predicts (+60), so the source is
    CPFSK at the nominal rate (fitted rate 0.99999, tone scale 1.000003).
  * Linear part: with an exact fractional-sample CPFSK reference, a 24-tap FIR explains 87 % of the burst
    energy (residual 13 %, not time-varying, not a memoryless nonlinearity: it is coloured noise
    ~ -17 dB, mostly < 1.5 kHz); two pole/zero biquads + gain + attack fitted by Levenberg-Marquardt on
    the same time-domain residual (3 bursts jointly, 10 random starts) explain 78 %.  Twist -3.1 dB
    (the W=4 envelope of the bursts says -2.0..-2.2 dB), gain 226 (unit sine -> RMS ~160 at the output
    as measured: burst RMS 150-165 for c2_b*), attack tau 34.6 samples (4.3 ms, visible key-up ramp).
  * Skew: the time-domain fit alone does NOT reproduce the real chain's dispersion (the profile-off
    decoder made 2 errors on that model vs 42-45 on the real bursts, and RELDLY2 had no effect).  The
    all-pass (f0, r) was therefore fitted in the decision domain: grid over f0 500..2600 Hz, r 0.3..0.9
    minimising sum(log1p(err_model) - log1p(err_real))^2 over 7 front ends x 3 bursts (corr W8 g2,
    pre-emphasised W6/W8 with space-energy delay 0..3.5 samples; out/decoder-study/fe.py's scoring):
    f0 = 900 Hz, r 0.7..0.8.  r was then set to 0.75 so that the ORIGINAL radio profile (before the
    retune) decoded the c2 content (out/expected_tones_n0call9.txt through the model, 8 noise seeds)
    about as often as it decodes the real capture2 (5-6/8 vs 9/12); r = 0.7 gave 8/8, r = 0.8 gave 1/8.
  * Noise: pre-burst floor of the captures, white-ish (all bands 32..43 dB per bin, 300-800 Hz and
    3.5-4 kHz ~7 dB higher): RMS 4.0 over samples 300..1500 of each c2_b*, median 4.0 over all 400-sample
    quiet blocks of out/capture2.pcm (burst-block median RMS 213; 9.7 only in the first 300 samples,
    a start-up transient, and 10-16 in the tail after a burst).  Default 4.0; snr_db overrides it.
  * DC: capture mean -10.9.

VALIDATION (python mirror of the profile-off decoder: W8 g2 smooth 3, mid-bit sampling at fixed phase
0.5, c2 expected tones; the sweep script that produced this table was one-off work and is not kept)
                    1-bit runs      2-bit    3-bit  4+
    real c2_b0..2   22-23%          3%       2      1      (17% / 4-5% / 4 / 0 at the best phase per burst)
    channel model   17-19%          2%       1      0-1    (3 noise seeds)
  The brief quoted 24% / ~5% / ~0.  The model matches the 1-bit rate to within ~5 points, under-produces
  2-bit errors a little, and reproduces the effect of the space-energy delay (RELDLY2) that the real
  bursts show.  Known limits: no non-LTI residual (the real chain leaves ~13 % of the burst energy
  unexplained by any LTI fit: coloured noise ~ -17 dB, mostly < 1.5 kHz, probably acoustic/AGC), one
  fixed channel (the twist knob re-trims it with a one-pole filter, which also moves the delays), and
  the all-pass was fitted on one 634-bit packet.
"""
import math
import random

import pcmio

FS = 8000.0
ATTACK = 34.56355398
GAIN = 226.14081327
# (pole radius, pole angle rad, zero radius, zero angle rad) per biquad
_BQ = ((0.91738658, 1.27165909, 0.99403458, 1.3354162),
       (0.9708808, 0.67851824, 1.05796493, 0.66845253))
_AP = (900.0, 0.75)
BASE_RMS = 160.0


def _sos():
    out = []
    for rp, tp, rz, tz in _BQ:
        out.append((1.0, -2 * rz * math.cos(tz), rz * rz, -2 * rp * math.cos(tp), rp * rp))
    f0, r = _AP
    th = 2 * math.pi * f0 / FS
    a1, a2 = -2 * r * math.cos(th), r * r
    out.append((a2, a1, 1.0, a1, a2))
    return out


def _biquad(sos, x):
    for b0, b1, b2, a1, a2 in sos:
        z1 = z2 = 0.0
        y = []
        for v in x:
            o = b0 * v + z1
            z1 = b1 * v - a1 * o + z2
            z2 = b2 * v - a2 * o
            y.append(o)
        x = y
    return x


def _mag(sos, f):
    w = 2 * math.pi * f / FS
    z1 = complex(math.cos(-w), math.sin(-w))
    z2 = z1 * z1
    h = 1 + 0j
    for b0, b1, b2, a1, a2 in sos:
        h *= (b0 + b1 * z1 + b2 * z2) / (1 + a1 * z1 + a2 * z2)
    return abs(h)


def twist_db_of_model():
    """Net amplitude ratio 2200/1200 Hz of the untrimmed model, dB."""
    s = _sos()
    return 20 * math.log10(_mag(s, 2200.0) / _mag(s, 1200.0))


def _trim(delta_db):
    """First-order filter (b0, b1, a1) with |H(2200)|/|H(1200)| = delta_db (bisection)."""
    if abs(delta_db) < 0.05:
        return None
    want = 10 ** (delta_db / 20.0)

    def ratio(a):
        if delta_db < 0:        # y = (1-a) x + a y[n-1]
            b = (1 - a, 0.0, -a)
        else:                   # y = x - a x[n-1]
            b = (1.0, -a, 0.0)
        sos = [(b[0], b[1], 0.0, b[2], 0.0)]
        return b, _mag(sos, 2200.0) / _mag(sos, 1200.0)
    lo, hi = 0.0, 0.995
    for _ in range(50):
        mid = (lo + hi) / 2
        r = ratio(mid)[1]
        if (delta_db < 0 and r > want) or (delta_db > 0 and r < want):
            lo = mid
        else:
            hi = mid
    return ratio((lo + hi) / 2)[0]


def channel(samples, rms=BASE_RMS, twist_db=None, snr_db=None, noise_rms=4.0, dc=-10.9, seed=0):
    """samples -> list of ints (see the module docstring). rms: burst RMS at the output (c2 bursts 160,
    capture2 bursts median 213, range 150-450). twist_db: net 2200/1200 amplitude ratio (None = the
    fitted -3.1 dB). snr_db: noise = rms / 10^(snr/20) (overrides noise_rms, the measured 4.0 floor)."""
    x = [float(v) for v in samples]
    n = len(x)
    pk = max(abs(v) for v in x) or 1.0
    thr = 1e-3 * pk
    gate = [0.0] * n
    last = -10 ** 9
    for i, v in enumerate(x):                       # burst gate, held over zero crossings
        if abs(v) > thr:
            last = i
        gate[i] = 1.0 if i - last < 12 else 0.0
    y = _biquad(_sos(), x)
    if twist_db is not None:
        t = _trim(twist_db - twist_db_of_model())
        if t:
            b0, b1, a1 = t
            z = 0.0
            xp = 0.0
            out = []
            for v in y:
                o = b0 * v + b1 * xp - a1 * z
                xp = v
                z = o
                out.append(o)
            y = out
    a = math.exp(-1.0 / ATTACK)
    g = 0.0
    on = 0
    acc = 0.0
    for i in range(n):
        g = a * g + (1 - a) * gate[i]
        y[i] *= g
        if gate[i]:
            acc += y[i] * y[i]
            on += 1
    cur = math.sqrt(acc / on) if on else 1.0
    k = rms / cur
    sigma = rms / 10 ** (snr_db / 20.0) if snr_db is not None else noise_rms
    rnd = random.Random(seed)
    return [max(-32768, min(32767, int(round(v * k + dc + rnd.gauss(0, sigma))))) for v in y]


def cpfsk(tones, rate=1.0, lead_ms=50.0, tail_ms=50.0, tone_scale=None, ph0=0.0, fs=FS, amp=1.0, twist_db=0.0,
          mode="continuous"):
    """Phase-continuous AFSK at sample rate fs, amplitude amp (space = mark + twist_db). rate scales the baud
    rate and (unless tone_scale is given) the tone frequencies together (a sample-clock error). mode:
    "continuous" (CPFSK); "reset" (the tone restarts at phase 0 at every bit, like a DDS whose word write resets
    it); "two" (two free-running generators switched per bit, as the DM-32's FD6818 dual-tone method does: a
    phase jump at every tone change)."""
    ts = rate if tone_scale is None else tone_scale
    spb = fs / (1200.0 * rate)
    s = [0.0] * int(fs * lead_ms / 1000)
    ph = pm = ps = ph0
    acc = 0.0
    for t in tones:
        acc += spb
        nn = int(acc)
        acc -= nn
        w = 2 * math.pi * (2200.0 if t else 1200.0) * ts / fs
        a = amp * (10 ** (twist_db / 20) if t else 1.0)
        if mode == "reset":
            ph = 0.0
        for _ in range(nn):
            if mode == "two":
                pm += 2 * math.pi * 1200.0 * ts / fs
                ps += 2 * math.pi * 2200.0 * ts / fs
                s.append(a * math.sin(ps if t else pm))
            else:
                ph += w
                s.append(a * math.sin(ph))
    s += [0.0] * int(fs * tail_ms / 1000)
    return s


# ---- random AX.25 UI packets -------------------------------------------------------------------
def crc_x25(data):
    """CRC-16/X.25 (the AX.25 FCS), as an int."""
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    return c ^ 0xFFFF


def _addr(call, last=False):
    base, _, ss = call.partition('-')
    return bytes((ord(ch) << 1) for ch in base.ljust(6)) + bytes([0x60 | ((int(ss) if ss else 0) << 1) | (1 if last else 0)])


def ax25_frame(dest, src, path, info):
    f = _addr(dest) + _addr(src, last=not path)
    for i, p in enumerate(path):
        f += _addr(p, last=i == len(path) - 1)
    f += b'\x03\xf0' + info.encode()
    c = crc_x25(f)
    return f + bytes([c & 255, c >> 8])


def ax25_tones(f, pre=20, post=3):
    bits = [0, 1, 1, 1, 1, 1, 1, 0] * pre
    ones = 0
    for byte in f:
        for k in range(8):
            b = (byte >> k) & 1
            bits.append(b)
            if b:
                ones += 1
                if ones == 5:
                    bits.append(0)
                    ones = 0
            else:
                ones = 0
    bits += [0, 1, 1, 1, 1, 1, 1, 0] * post
    out, t = [], 0
    for b in bits:
        t ^= (b == 0)
        out.append(t)
    return out


def random_packet(seed, pre=None, info_len=None):
    """-> dict(tones, line, pre, n). line = what krk_afsk_line(0) should return (60 chars max)."""
    r = random.Random(seed)
    up = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    src = ''.join(r.choice(up) for _ in range(r.choice([1, 2]))) + r.choice('0123456789') + ''.join(r.choice(up) for _ in range(r.randint(1, 3)))
    ssid = r.randint(0, 15)
    if ssid:
        src += '-%d' % ssid
    dest = r.choice(['APZKRK', 'APRS', 'APDW16', 'APT310', 'APOT21', 'CQ'])
    path = [r.choice(['WIDE1-1', 'WIDE2-1', 'WIDE2-2', 'RELAY', 'TRACE3-3']) for _ in range(r.randint(0, 3))]
    n = info_len or r.randint(20, 120)
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 /!=@>[]-.,:;#*_'
    info = r.choice('!=@>') + ''.join(r.choice(chars) for _ in range(n - 1))
    pre = r.randint(2, 72) if pre is None else pre
    line = (src + '>' + dest + ''.join(',' + p for p in path) + ':' + info)[:60]
    return dict(tones=ax25_tones(ax25_frame(dest, src, path, info), pre, 3), line=line, pre=pre, n=n)


to_pcm = pcmio.pack
