# F10 satellite mode: orbit maths (`krk_sat.c`)

Status: the orbit maths and its host tests are done, and the radio integration is built and tested under QEMU (section
"Implementation (2026-10-29)" at the end; the older "Proposal" section is kept for the reasoning). Prediction and tracking
have since run on a radio on real passes; see the [satellites user guide](user-guide/satellites.md).

Files: `patches/kraken_ui/krk_sat.h`, `patches/kraken_ui/krk_sat.c`, `tests/test_sat.c` (host driver),
`tests/test_sat.py` (run `python3 tests/test_sat.py`; exits non-zero on any failure).

## What it does

| Step | Function | Notes |
|---|---|---|
| TLE text to elements | `krk_sat_init(s, line1, line2)` | Both 69-column lines, modulo-10 checksums, line numbers, matching catalog numbers. Rejects periods >= 225 min (`KRK_SAT_E_DEEP`). Also runs the SGP4 initialisation. Trailing CR/LF/space is fine. |
| Time | `krk_sat_time(y,mo,d,h,mi,s)`, `krk_sat_civil()` | A time is one `double`: days since 1949-12-31 00:00 UTC. Double keeps ~1 us at 2026 dates, so no split date/fraction is needed. UTC is used as UT1 (< 0.9 s of Earth rotation, < 0.4 km at the surface; not visible in Doppler). |
| Propagate | `krk_sat_propagate(s, t, r, v)` | TEME position (km) and velocity (km/s). |
| Observer | `krk_sat_obs_deg()`, `krk_sat_obs_min4(lat, lon, alt_m)` | `obs_min4` takes the `struct krk_gps_fix` units (1e-4 arc-minute, signed, so `fix.lat`/`fix.lon` with the N/S, E/W sign already applied; 600000 units = 1 deg). WGS-84 geodetic to geocentric is precomputed once. |
| Look angles | `krk_sat_look(s, o, t, &L)` | `L.az`, `L.el` (deg), `L.range` (km), `L.rr` (km/s, positive = receding), plus `L.r`, `L.v`. |
| Doppler | `krk_sat_doppler_rx(f, rr)`, `krk_sat_doppler_tx(f, rr)`, `krk_sat_round_hz(f, step)` | RX = f(1 - rr/c), TX = f(1 + rr/c), in Hz. Round to the tuning step afterwards (10 Hz native step of the RX register, or 2.5/5/6.25/12.5 kHz when following the channel raster). |
| Next pass | `krk_sat_next_pass(s, o, t0, horizon_days, min_el, &pass)` | AOS, LOS (1 s resolution), max elevation and its time. Flags: `INPROGRESS` (already up at t0; AOS = t0, max covers only the remainder), `LOS_CLIPPED`. |

## Algorithm notes

* SGP4 is the near-earth branch of Vallado, Crawford, Hujsak, Kelso, "Revisiting Spacetrack Report #3" (the
  reference implementation behind the python `sgp4` package), WGS-72 constants, improved mode, including the
  simplified-drag branch for perigee < 220 km. SDP4 (period >= 225 min: GPS, Molniya, GEO) is omitted.
  All of the amateur LEO satellites (ISS, SO-50, AO-91, AO-27, PO-101, RS-44 at ~1500 km, period ~116 min) are
  near-earth.
* Everything is IEEE double. Single precision was not used: with a 7000 km radius the 24-bit mantissa gives
  ~0.5 m of position resolution but the Kepler solve, the 7-day secular terms and the range-rate difference
  lose more than that, and the saving on a soft-float CPU is only 2-3x on a ~2 ms job.
* Private math, no libm: `sat_sincos` (Cody-Waite pi/2 reduction, degree-15/16 Taylor on |r| <= pi/4),
  `sat_atan2` (reciprocal + 15-degree shift to |x| <= 0.268, 14-term series), `sat_sqrt` (exponent-halving
  seed from the bit pattern + 4 Newton steps), `cbrt` (Newton, init only). Accurate to ~1e-15.
* Topocentric transform is done directly in TEME: the observer is rotated by GMST (IAU-82), the rotating-Earth
  velocity omega x r is subtracted for the range rate. No nutation, polar motion or UT1-UTC: these are
  arc-second effects, far below TLE accuracy. Cross-checked against skyfield (see below).
* Pass search: while the satellite is below the horizon the step is not fixed. The range cannot shrink faster
  than 10 km/s, and the satellite must come within sqrt(r^2 - Ro^2) (+2 % + 100 km) of the observer before it
  can rise, so the next sample is `(range - horizon_distance - margin) / 10 km/s` later, clamped to 20..900 s.
  Near or above the horizon the step is 20 s; inside a pass 30 s. AOS/LOS are bisected to 1 s, the peak is
  refined with three parabolic fits through 3-sample windows (an overhead pass has a sharp peak). A pass whose
  peak is within ~20 s of the threshold can be missed.

## Accuracy (measured by `tests/test_sat.py`)

Reference: python `sgp4` 2.25 (WGS-72) for the satellite, an independently written ECEF/ENU observer transform
for look angles. 2891 samples = 7 TLEs (ISS, SO-50, RS-44, AO-91-like, a low-perigee/simplified-drag orbit,
an e = 0.10 polar orbit, Vallado's 00005 with e = 0.186) x 7 observers (Connecticut, Sydney, Ushuaia, Reykjavik,
Singapore, McMurdo, Quito at 2850 m) x 60 times spread over 0..7 days after epoch. Samples after decay are
required to be reported as decayed by both.

| Quantity | Max error vs reference | Target |
|---|---|---|
| position | 2.7e-6 km | 10 km |
| velocity | 2.5e-6 m/s | |
| azimuth (x cos el), elevation | 5e-7 deg, 2e-7 deg | |
| range | 9e-6 km | |
| range rate | 3.7e-5 m/s | 50 m/s |
| Doppler at 435.8 MHz | 5.4e-5 Hz | 100 Hz |

Also: Vallado's published 00005 verification vectors (t = 0 and 360 min) match to 2e-5 km / 2e-8 km/s; skyfield
(full TEME-ITRS with nutation, UT1) agrees to < 0.001 deg and 0.02 km range for el > 5 deg; 70 pass-prediction
cases (5 observers x 7 TLEs x min elevation 0 and 10, random start times, 24 h) agree with a 10 s brute-force
scan to 0.9 s for AOS/LOS and 5e-5 deg for max elevation. Arithmetic error is therefore negligible, the error
budget is the TLE itself.

## Cost

* Per `krk_sat_look`: ~10 sincos, 3 atan2, 6 sqrt (4 divides each) and ~250 other double operations, about 900
  soft-float operations. Host: 0.26-0.29 us. The radio estimate (not measured) is 100-300 cycles per
  add/mul and 500-1000 per divide, so ~2-3 ms at ~100 MHz. That is ample for a 1 Hz update.
* `krk_sat_next_pass` for 24 h: 7 to 361 propagations in the tests, mean 109 (about 0.3 s on the radio, worst
  case ~1 s). Upper bound `horizon / 20 s + ~150`, which only occurs for an orbit that lingers near the horizon.
* Do pass-list searches in the screen's idle path, one satellite per tick, not in the 1 Hz retune path.
* Code size, csky `-mcpu=ck803s -ffreestanding -ffunction-sections`: 16.0 KB at `-O2` (13.8 KB at `-Os`; 4.7 KB of
  it is `krk_sat_init`, where the SGP4 initialisation is inlined), no `.data`, no `.bss`, no `.rodata`.
  Plus the libgcc soft-double helpers, which the file references: `__adddf3 __subdf3 __muldf3 __divdf3
  __eqdf2 __nedf2 __ltdf2 __ledf2 __gtdf2 __gedf2 __floatsidf __floatunsidf __fixdfsi __fixunsdfsi`. Check the
  patch link includes libgcc and whether stock already has these (they cost several KB if not).
  Building this file with `-Os` saves ~2 KB.
* RAM: `struct krk_sat` 288 bytes, `struct krk_sat_obs` 48, `struct krk_sat_look` 88, `struct krk_sat_pass`
  40. No globals; stack use is a few hundred bytes (largest frame is `krk_sat_propagate`).

## Limits

* Near-earth only. `krk_sat_init` returns `KRK_SAT_E_DEEP` for periods >= 225 min.
* TLE staleness: SGP4 error grows ~1-3 km/day along-track for LEO (more for low, high-drag orbits, and
  after manoeuvres such as ISS reboosts). A 5 km along-track error is ~0.7 s of timing error; with a peak
  range-acceleration of ~0.14 km/s^2 that is up to ~90 m/s of range rate (~130 Hz at 435 MHz) near closest
  approach, still well inside FM tolerance. Show the TLE age (`t - krk_sat_epoch(s)`) and warn above ~14 days.
* No refraction, no UT1-UTC, no polar motion: irrelevant at this accuracy.
* A decayed or invalid orbit returns `KRK_SAT_E_ORBIT` from propagate/look/next_pass. `next_pass` also
  returns it if the satellite decays during the search.
* The Doppler helpers use double, valid to ~4 GHz in 32-bit unsigned Hz.
* The TLE's ndot/nddot fields are ignored (SGP4 does not use them; drag is carried by B*).

## Proposal: on-radio part (not implemented)

**TLE storage.** Compile the TLEs into flash as `const` data. A `tools/` script (built as `tools/sat_tle_gen.py`)
reads a text file of 3-line records (name, line 1, line 2; the standard Celestrak/AMSAT `amateur.txt` format
trimmed to the wanted ~10-20 satellites), verifies checksums with the same rule as the C code, and emits
`krk_sat_tles.c` containing `{ name[12], nominal downlink Hz, nominal uplink Hz, mode, line1[70], line2[70] }`
records, ~190 bytes each. Prepared elements are not stored: `krk_sat_init` is run on the selected satellite when
the user picks it (one propagation's worth of work). The nominal frequencies, FM/linear and CTCSS tone come from
a second column in the same file. Updating a TLE then means regenerating and reflashing the patch; a later step
could read a user file from the data-flash/USB area with the same parser, since `krk_sat_init` takes plain
strings.

**UI.** One screen with three states (the project's `struct krk_screen` runner, `tick` every 100 ms):
1. Satellite select: list from the const table, with TLE age.
2. Pass list: for the selected satellite, `krk_sat_next_pass` repeated from the previous LOS; show AOS time
   (from `krk_sat_civil`), duration, max elevation. Run one search per tick so the keys stay responsive. Needs a
   fix: lat/lon from `krk_gps_get` (`krk_sat_obs_min4`), time from the GPS RMC (`fix.utc` plus the RMC date, or
   the RTC). Without a fix, offer a manual grid square (centre of the locator) as a fallback.
3. Live: once per second `krk_sat_look`, show AZ/EL (and a +/- arrow for approaching/receding), range, and the
   corrected RX and TX frequencies. Colour by elevation (dim below 0, accent above 10).

**Doppler retune.** Call `krk_sat_look` once per second, compute `krk_sat_doppler_rx(f_dl, L.rr)`, round with
`krk_sat_round_hz(.., 10)`, and retune only if the value changed by more than about 100 Hz (a step of one
FM channel raster is not needed; a 300 Hz change per second is the maximum slope at 435 MHz).
For RX use `krk_rf_retune(f10)` from `krk_rf.h` (10 Hz units, RX only; recipe and the register sequence in
`docs/retune-for-scope.md`) and `krk_rf_restore()` on exit. The RX frequency is 10 Hz granular in the FD6818
registers, so no coarser rounding is needed on RX. Open questions the integrator must settle: `krk_rf_retune`
re-triggers RXON (VCO calibration), so check for an audible gap at 1 Hz and consider a larger deadband;
TX is out of scope of `krk_rf` (it is RX only): the TX frequency (`krk_sat_doppler_tx`) is only displayed,
or applied by whatever TX-frequency setting path is chosen, and must be re-applied at PTT time, not mid-transmission.
Use Doppler on the downlink only if the transceiver is half-duplex (the DM-32UV is), so for FM birds the usual
operating practice is to correct the downlink continuously and the uplink at PTT.


## Implementation (2026-10-29)

Built to `docs/satellite-radio.md`'s recommended design. Nothing has run on a radio; QEMU shows the logic, the screens and the
retune path, not RF.

### Files

| File | Role |
|---|---|
| `patches/kraken_ui/kraken_ui3.ld`, manifest blob `kraken_ui3` | New blob in claim `kraken_ui3_code` (0x030f8000, 32 KiB), SRAM claim `kraken_ui3_state` (0x18005600, 1 KiB). Listed BEFORE `kraken_ui2` (the builder resolves `@blob:sym` only for blobs already built, `build_manifest_patch.py` `resolve`), `"libgcc": true`, `-O2` fits. Entry `krk_sat_screen` at the origin (`expect_entry`). |
| `krk_sat.c/.h` | The orbit maths (unchanged). |
| `krk_sat_tles.c`, `krk_satdb.h` | GENERATED const TLE table. `data/tles.txt` is the source, `python3 tools/sat_tle_gen.py` regenerates it (deterministic; the build does not run it). |
| `krk_satrf.c/.h` | UTC source (RMC, RTC fallback), the 30-minute Doppler track, arm/stop, and `krk_sat_tick` (the 1 Hz writer, integer only). |
| `krk_satscr.c` | The "Satellites" screen (KRAKEN slot 5). |
| `krk_ext.c`, `krk_periodic.c`, manifest | Slot 5 = `&krk_sat_screen` and one call line `krk_sat_tick();` in `krk_hook_maint_tick`, both bound by defsym `@kraken_ui3:...` in the `kraken_ui2` defsym map. No new splice (the shared `maint_tick_hook` at 0x030547ae already existed). |
| `tests/f10_checks.py` | In-target checks (called with one line from `kraken_ui_test.py`). |
| `tools/qemu_poke.py`, `tests/data/sat_pokes.txt`, `POKE=` in `tools/qemu_ui_run.sh` | Memory pokes over the gdb stub during a UI run (a GPS fix/clock in stock's RMC buffer, dual watch held off), because stock's GNSS is Off in the factory defaults and no NMEA reaches the parser (`NMEA=1` alone shows "No fix" in the GPS Grid screen too). `qemu_ui_run.sh` now keeps `--spinor` when `NMEA=1` and `SPINOR=1` are both set. |

### TLE table

`data/tles.txt`: 9 entries (ISS repeater 437.800 down / 145.990 up PL 67.0, ISS packet 145.825 simplex, SO-50, AO-27, AO-91, AO-95, AO-85,
PO-101, LilacSat-2). The TLEs were fetched from CelesTrak's amateur group on 2026-10-29. **The frequencies and tones are the commonly
published AMSAT values and were not re-checked, and several of these satellites are failing or rarely active: refresh both TLEs and frequencies and check the
satellite's status before relying on any of it.** IO-117 (GreenCube) and the linear transponders (RS-44, XW-2x, FO-29) are not listed:
the radio has FM and DMR only, no SSB/CW demodulator or 9k6 GMSK modem. The generator checks name length, both checksums, line numbers,
matching catalog numbers and near-earth period (> 6.4 rev/day). Refreshing: edit `data/tles.txt`, `python3 tools/sat_tle_gen.py`, rebuild
(up to 10 entries; `KRK_SAT_MAXN` sizes the screen's pass cache). The screen shows each TLE's age (from line 1's epoch) and marks it with `!`
(list) / `OLD!` (info) above 14 days. The CTCSS tone is informational: stock tone setup is driven by the channel, which this feature never edits.

### The screen

List: name, next pass AOS (UTC, `#` toggles the stock time zone, `NOW` if already up) and max elevation (min elevation 5 degrees, 24 h horizon),
one satellite searched per 100 ms tick. OK opens a satellite. Info (refreshed each second): TLE age, az/el, range, range-rate, the
Doppler-corrected RX and TX (10 Hz resolution, trim included), PL, trim, status line, AOS/LOS or, while tracking, what the tick applied
(`rxw +4.78k rt1 sk0` = RX word offset from nominal, retunes so far, skipped ticks). Keys: OK arms tracking (builds the track incrementally,
~40 ms per tick) or stops it, Up/Down trim +-100 Hz on both words (max 5 kHz), `*` self-test, `#` fake pass, P1 list. Back leaves tracking
running (the tick lives in the maintenance task); re-entering opens straight on the tracked satellite. Position: `krk_gps_get`, else the last
fix seen since power-up. Time: the RMC sentence (copy + checksum, date field 9; `GPS`), else the RTC less the stock time zone (`RTC`, via stock's
`gps_coordinate_table_entry_parse` 0x03011aa4 on the tz index at 0x38d22; RTC registers 0..4 as in `satellite-radio.md` 5.2); the header
shows the source and arming without one is refused ("no time source"; no position: "no GPS position"). A manual grid fallback was not built.

### Radio side (as designed; deviations marked)

* Words: RX 0x3846c, TX 0x38468 (10 Hz units). Re-verified in the disassembly: `channel_freq_pair_store` 0x0300da88 (r0 -> 0x38468, r1 -> 0x3846c),
  `channel_change_apply_squelch` 0x0300df08 loads `0x38468+4`, `radio_init_sequence` 0x0300e40c loads 0x38468 before `bsr 0x2b674`.
  `frequency_band_range_check_and_status_set` 0x0300d84c is `f(rx = r0, tx = r1)`; bit 5 of 0x3e31b = TX not in this model's ranges.
  Keyed = byte 0x3fdc8 bit 3 (set by `radio_mode_entry_reinit_and_display` 0x0305baec, `ins r2,r1,3,3`), channel load = 0x3e31c bit 3 (dispatcher
  0x030573e8 sets it at 0x03057426 and clears it at 0x03057944), `dual_watch_state` 0x030641e4 = byte 0x400d8.
* Tick (`krk_sat_tick`, maintenance task): idle unless magic + ACTIVE. Order: stop request -> 30-minute cap (OS tick) -> track end + 30 s ->
  skip while keyed/loading/abort -> ownership (words differ from what we wrote: end WITHOUT restore) -> every 60 s resync of the clock from
  the RMC (accepted only within 3 s of the extrapolated time, so a stale or torn sentence cannot move it) -> interpolate -> clamp +-15 kHz
  (else restore) -> **band check on the CANDIDATE words first** (deviation from the doc, which wrote then checked: the check takes its
  arguments, so the words are never out of step with bit 5), failing candidate -> nominal uplink and check again -> write TX word every second
  (free) -> RX word and `channel_change_apply_squelch` only when it moved by >= 500 Hz (first apply unconditional) and not keyed.
  Restore: both words, band check, `channel_change_apply_squelch`; deferred (state RESTORE, retried each tick) while keyed.
* RX-only satellites (no uplink in the table) and channels that may not transmit (flags8 bit 3) never touch the TX word: PTT then uses the
  channel's own TX frequency as stock does. Digital channels, the non-selected side, dual watch, scanning, keyed/loading refuse arming.
* Track: `ppb = rr / c * 1e9` as int16 every 10 s for 30 minutes (181 samples, 362 bytes), 3-point Lagrange interpolation (exact for
  a linear ramp, tested; deviation: 10 s instead of 5 s spacing and quadratic, to fit 1 KiB). Frequencies: `nom * r / 1e9` without 64-bit maths.
* CTCSS/DCS are the channel's: use an analog channel with a TX tone matching the satellite and no RX tone.

### Memory

Flash: blob3 30,248 bytes of 32,768 (-O2: sat 16 KB, libgcc, the screen, the generated table 1.7 KB); `-Os` for `krk_sat.c` would give ~2 KB back.
SRAM `kraken_ui3_state`: 908 of 1024 bytes (`R` 452 incl. the track, `struct krk_sat` 288, observer 48, pass cache 80, screen state).
Stack: the soft-double work runs in the screen ("task menu", 2560 bytes: `OSTaskCreate` at 0x03035ea0 passes stack 0x320b4 and 0x280 words, read from
the disassembly). Measured on the target (`tests/f10_checks.py`, canary-filled stack): `krk_sat_look` 652 bytes, `krk_sat_next_pass` 952 bytes. High-water
mark of the real menu stack in a booted QEMU run (zeroed at task creation, read back through the gdb stub, `tools/qemu_poke.py ... r`):
672 bytes with only the KRAKEN list open, 1280 bytes after exercising the satellite list (pass search), the info view, tracking and the self-test, so about
1280 bytes of headroom remain. The tick makes no soft-float call and its frame is small (the maintenance task has 0x180 words); in fake-pass mode it also
prints a UART line (one `krk_cat` buffer of 64 bytes).

### Tests

`tests/f10_checks.py` inside `kraken_ui_test.py` (QEMU, gdb): slot 5 and claim bounds; the tick (no magic/idle no-ops; first apply words and stock call
order; hysteresis; TX follows at full precision; trim; interpolation; keyed / channel-load / tx-abort skips; band-check fallback to nominal; nominal also
rejected leaves stock's bit set; RX-only; ownership loss; restore on stop, 30-minute cap, track end, range clamp; stop while keyed defers and then restores);
the shared hook calls the tick; SGP4 + look angles + Doppler + `next_pass` compiled for the CK803S run on the emulated CPU and compared with the host build for the
ISS TLE at 2026-10-29 12:00 UTC (az/el to 1e-4 deg, range 1e-3 km, rr 1e-6 km/s, AOS/LOS 0.5 s, max el 1e-3 deg). **QEMU soft-float: no mis-emulation was
hit by this blob's libgcc helpers** (the older note about stock's int-to-double giving 256.0 for 360 concerns stock's own routine, which the bearing test still
intercepts). `python3 tests/test_sat.py` (host) is unchanged.
QEMU UI runs (`tools/qemu_ui_run.sh`, `SPINOR=1 POKE=/w/dm32uv-unleashed/tests/data/sat_pokes.txt`, keys: Menu, Up, Up, OK, Down x9, OK):
`out/qemu/sat_list` (list with passes: ISS 12:30 el 13, matches the host 12:30:27 / 12.7), `sat_trk` (tracking: `rxw +4.78k rt1 sk0`, the real 1 Hz hook ran
and `channel_change_apply_squelch` returned), `sat_fake` (fake pass with UART markers), `sat_stk1` (self-test sweep, UART lines; RSSI reads 511 in QEMU).

### Hardware test (one action, one capture)

Set the channel (analog, VFO or memory, dual watch off, no RX tone) and put a signal generator, or a second radio keyed on a dummy load, on the selected
satellite's DOWNLINK (ISS-RPT: 437.800 MHz FM). Start `cat /dev/cu.usbserial* > sat.txt` (115200 8N1, the programming cable). Then on the radio:
KRAKEN > Satellites > OK on ISS-RPT > **`*`**. One key press sweeps the RX word from -6 kHz to +6 kHz around the downlink in 500 Hz steps (350 ms dwell,
stock `channel_change_apply_squelch` retune each step) and prints `SATTEST k=<Hz> rx=<10Hz units> rssi=<raw>` per step, then restores the channel. The RSSI peak
must sit at k = 0 (or at the radio's TCXO error, which gives the value for the trim) with the expected width; a peak at +k instead of -k would mean the sign
is wrong, no peak means the retune path is not tuning. For tracking behaviour (retune chop, hysteresis, restore) press `#` instead: a time-accelerated fake pass
(about 3.5 minutes: -18 to +18 kppb, no GPS needed) writes `SATF t= ppb= rx= tx= [RETUNE]` every second while the generator stays at the nominal downlink; the
audio should drop out as the radio walks off and the words must return to the channel's own when it ends or on OK. Open risks: audible chop per retune (unmeasured),
PTT from inside the screen is unsupported (use standby), the RTC fallback and the model's TX ranges are static-RE only, the TXCO error is not corrected beyond the trim.

### Review fixes (2026-10-29)

- **Ownership before every restore (HIGH).** `restore()` (stop / cap / end / range / the deferred ST_RESTORE retry / the
  self-test) writes the saved words only if RX/TX still equal what the tracker last wrote (`owned_rx/owned_tx`); otherwise
  they belong to a new channel and are left alone (`why = lost`). `finish()` keeps the ownership record until the restore ran.
- **Reboot (HIGH/9).** `krk_sat_tick` and `satr_ensure` call `krk_rebooted(&R.stamp)` (counter below the stamp or a stamp older than
  30 s): the state is dropped and the structure re-initialised, never restored.
- **Retune vs PTT (3).** No lock exists that a patch task could hold across the retune (the chip bus semaphore is taken inside
  `channel_change_apply_squelch`; holding the scheduler across a pend would deadlock). So: the raw PTT pin (stock
  `0x03012f48`, 1 = pressed, pin 0x2b low) is part of `busy()`; it is tested at the top of the tick, again right before the RX
  word is written and again right before `apply_squelch`. A skipped chip retune is kept pending (`rx_pend`) and done by a
  later free pass (or by stock after the over). Residual window: a press landing during the few ms of `apply_squelch` still
  has to pass stock's 4-sample debounce and the PTT task before its TX retune, which takes longer than ours. Soft PTT posts
  (F9/F13) are not seen by the pin, but they key only digital channels and F10 tracks only analog ones.
- **Self-test restore (4).** `satr_restore_now()` has the same guards and ownership test as `restore()` (the self-test records
  its own ownership in `satr_apply_rx`); when keyed/loading/PTT it hands over to the 1 Hz tick (state RESTORE) and the UART
  marker says so. SATR_CLAMP (finding 11) is unchanged: ending at an out-of-range Doppler restores harmlessly.
- Tests: `tests/f10_checks.py` (ownership lost for stop/cap/end/deferred restore, stale-stamp and counter-below-stamp reboot,
  `satr_ensure`, PTT pin at three moments, pending retune, self-test restore). State `R` is now 456 B.
