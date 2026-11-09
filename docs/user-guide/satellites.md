# Satellites

Kraken can predict passes of a few FM amateur satellites from your GPS position and clock, and correct
the radio's receive and transmit frequencies for Doppler shift during a pass. The orbit data (TLEs) is
built into the firmware.

## Satellites

Pick a satellite, see when it next rises, then start tracking. While tracking, the radio moves the current
channel's RX and TX frequencies to follow the Doppler shift, and puts the channel back when tracking ends.

**Status:** pass prediction, tracking and Doppler retune work on hardware on real passes (AO-27, AO-85); no
contact has been made yet (the satellites were idle), and transmitting through a satellite has not been tested.
**Where:** **Menu > KRAKEN > Satellites** (opens on any channel; tracking needs an analog channel)

### Built-in satellites

| Name | Downlink (radio RX), MHz | Uplink (radio TX), MHz | Uplink tone | Note |
|---|---|---|---|---|
| `ISS-RPT` | 437.800 | 145.990 | 67.0 | ISS FM cross-band repeater (when on) |
| `ISS-PKT` | 145.825 | 145.825 | none | ISS APRS/packet digipeater, simplex |
| `SO-50` | 436.795 | 145.850 | 67.0 | FM repeater |
| `AO-27` | 436.795 | 145.850 | none | FM, rarely active |
| `AO-91` | 145.960 | 435.250 | 67.0 | FM repeater |
| `AO-95` | 145.920 | 435.300 | 67.0 | FM repeater |
| `AO-85` | 435.180 | 145.980 | 67.0 | FM repeater, failing |
| `PO-101` | 145.900 | 437.500 | 141.3 | FM repeater |
| `LILAC-2` | 437.200 | 144.350 | none | FM transponder |

All are FM. Several of these satellites are failing or only switched on at times, and the frequencies and
tones were entered from the usual AMSAT listings without a fresh check. Check a satellite's current status
and frequencies (AMSAT status pages) before you plan a contact. The radio has no SSB or CW receive, so linear
transponders are not listed.

### Before you start
1. Turn the radio's GPS on in the stock GNSS menu (it is off by default) and get a fix outdoors.
2. Select an analog channel (memory or VFO) on the main side. Dual watch off, not scanning.
3. Set that channel's TX tone to the satellite's uplink tone (the info screen shows it as `PL`), and no RX
   tone. Kraken never changes the channel's tones; the tone shown on screen is for your reference only.

### Using it
1. Open **Menu > KRAKEN > Satellites**. The list fills in one satellite at a time.
2. Use `Up`/`Down` to pick a satellite and press `OK` to open its info screen.
3. Within about 15 minutes before the pass starts (AOS on row 7), press `OK` to start tracking. Row 6 shows
   `building track` with a percentage for a moment, then `TRACK` and a countdown. Check that it says `TRACK`
   before you rely on it.
4. To transmit, press `Back` twice to return to standby. Tracking keeps running in the background; use `PTT`
   from standby, not from the Satellites screen.
5. To stop, open **Menu > KRAKEN > Satellites** again (it opens straight on the tracked satellite) and press
   `OK`. Tracking also stops by itself (see below).

### Pass list

Header: `Satellites UTC GPS`. `UTC`/`LOC` is the time shown in the list; `#` switches between UTC and local
time (local uses the radio's stock time zone setting). The last word is the time source: `GPS` (from the GPS
receiver), `RTC` (the radio's own clock, used when there is no GPS time yet) or `---` (no time; shown in warning
colour).

Each row: the satellite name, then the start time (AOS) of its next pass above 5 degrees within 24 hours, and
that pass's highest elevation in degrees, for example `>AO-91     04:47 48`.

| Row shows | Meaning |
|---|---|
| `NOW` | a pass is in progress |
| `...` | still computing (or no time source yet) |
| `no fix` | no GPS position yet; the screen checks again every second and fills in when a fix arrives |
| `none 24h` | no pass above 5 degrees in the next 24 hours |
| `TLE bad` | the built-in orbit data for this satellite is unusable |
| `!` at the end | the orbit data is more than 14 days old |

The position used is the current GPS fix, or the last fix seen since power-on. Only the next pass per
satellite is shown.

Keys: `Up`/`Down` move, `OK` opens the info screen, `#` switches UTC/local, `Back` leaves.

### What the info screen shows

Refreshed once a second.

| Row | Example | Meaning |
|---|---|---|
| 0 | `AO-91 TLE 9d` | name and age of the orbit data in days; ` OLD!` added after 14 days |
| 1 | `Az 297 El  24` | azimuth and elevation in degrees (dim below the horizon) |
| 2 | `Rng 1450km -5.7` | range in km and range rate in km/s (minus = approaching) |
| 3 | `RX 145.96350 MHz` | downlink corrected for Doppler and trim; ` nom` instead of ` MHz` = uncorrected (no position or time) |
| 4 | `TX 435.24120 MHz` | uplink corrected the same way; `TX none (RX only)` if the satellite has no uplink |
| 5 | `PL 67.0 trim +0Hz` | uplink tone to set on the channel, and your trim |
| 6 | `idle` / `TRACK 14:32 ok` | tracking state (table below) |
| 7 | `AOS 04:47 max 48` | next pass start (`LOS` and end time if the pass is in progress) and its highest elevation; while tracking: `S<live> pk<peak> rt<retunes>` |
| 8 | `PTT: from standby` | hint, or a message (errors in warning colour) |
| 9 | `OK:trk *:tst #:fake` | keys; `OK:stop Back:keeps` while tracking |

Instead of az/el, row 1 can show `no GPS position`, `no time source` or `orbit error`.

Row 6 states:

| Text | Meaning |
|---|---|
| `idle` | not tracking |
| `building track NN%` | computing the Doppler curve (about a second) |
| `TRACK mm:ss ok` | tracking; time left of the 30-minute session |
| `TRACK mm:ss TX!` | the corrected uplink is outside the radio's transmit range, so the plain uplink frequency is used |
| `TRACK mm:ss RX` | receive only: the satellite has no uplink, or the channel cannot transmit; TX is left as the channel has it |
| `FAKE mm:ss ...` | a test pass (`#`) is running |
| `restoring...` | waiting for `PTT` to be released before putting the channel back |
| `idle: stopped` | you stopped it with `OK` |
| `idle: pass over` | the computed track ran out |
| `idle: 30 min cap` | 30 minutes since start |
| `idle: channel changed` | the channel or frequency was changed by something else; tracking ended |
| `idle: range clamp` | the correction would have exceeded 15 kHz; tracking ended |

Row 7 while tracking: `S` is the live receive signal level and `pk` the highest seen since tracking started.
This is the radio's raw, uncalibrated receiver reading (the same scale as the Squelch screen), not S-units. On
hardware the noise floor read about 90 to 105; a satellite in range and transmitting should stand clearly
above that. `rt` counts RX retunes so far. The peak lets you see a short burst of signal on an otherwise quiet
pass.

Keys on the info screen:

| Key | Action |
|---|---|
| `OK` | start tracking; `OK` again stops it |
| `Up` / `Down` | trim both RX and TX by +100 / -100 Hz (up to +/-5000 Hz), to correct the radio's own frequency error |
| `*` | receive self-test (below) |
| `#` | start a fake test pass (only when idle) |
| `P1` | back to the pass list |
| `Back` | leave the screen; tracking keeps running |

Messages that refuse tracking: `digital channel`, `not selected side`, `dual watch on`, `scanning`,
`TX/loading now`, `no time source`, `no GPS position`, `TLE bad`, `orbit error`.

### What tracking does to the channel
- Once a second the radio works out the Doppler shift for that moment.
- TX: the channel's transmit frequency is set to the corrected uplink every second, in 10 Hz steps.
- RX: the receive frequency is moved only when the correction has changed by 500 Hz or more since the last
  move, because each RX retune can cause a short break in the audio. On a typical pass that is 30 to 35 retunes.
- Only the frequencies the radio is using right now change. The stored channel is not edited; tones, power and
  everything else stay as the channel has them. The display shows the shifted frequency while tracking.
- PTT guard: while `PTT` is pressed, while the radio transmits, and while a channel is loading, tracking
  changes nothing. A pending RX move is made after you release `PTT`. The uplink frequency is the one set in the
  second before you pressed `PTT` and does not change during the transmission.
- When tracking ends (`OK`, 30 minutes, track over, or range clamp), the channel's own RX and TX frequencies
  are put back. If you are transmitting at that moment, this waits until you release `PTT`.
- If you change channel or VFO frequency, swap A/B, turn on dual watch or program the radio while tracking,
  tracking ends and the new setting is kept as loaded.
- Tracking state is lost at power-off; the channel comes back as stored.

### Self-test and fake pass
- `*` sweeps the receive frequency from 6 kHz below to 6 kHz above the satellite's downlink in 500 Hz steps
  (about 11 seconds) and shows `k <offset> rssi <level>` on row 7, then `self-test done`, and puts the channel
  back. With a signal generator or a second radio on the downlink frequency, the level should peak at offset 0
  (or at your radio's frequency error, which tells you what trim to set). Refused while tracking
  (`stop tracking first`).
- `#` runs a fake pass of about 3.5 minutes with no GPS needed: the current channel's RX and TX move up to
  about +/-8 kHz (at 435 MHz) and back, then the channel is restored with `idle: pass over`. It changes the TX
  frequency too, so do not transmit during it.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| Trim (`Up`/`Down` on the info screen) | -5000 to +5000 Hz, 100 Hz steps | 0 | No |
| List time (`#` on the list) | UTC, local | UTC | No |

### Orbit data goes stale
The TLEs are built into the firmware; the ones in this build were fetched in late October 2026. Predictions
drift as the data ages: after a few weeks pass times can be off by a minute or more, and the screen marks data
older than 14 days with `!` and `OLD!`. To refresh, update `data/tles.txt` and rebuild the firmware image (see
[Building Kraken](../building.md)). Fresh TLEs come from [CelesTrak](https://celestrak.org/) (amateur group).

### Tips
- These are FM satellites with weak downlinks. A handheld directional antenna (for example a dual-band Yagi)
  pointed using the az/el on screen works far better than the stock antenna.
- The radio is half-duplex: you cannot hear your own downlink while you transmit. Listen first, keep overs
  short, and use a second receiver if you want to hear yourself through the satellite.
- Start tracking within 15 minutes of AOS: one session lasts 30 minutes from the moment you press `OK`.
- Transmit only through satellites that are active, and only within your licence privileges.

### Limits
- FM satellites in low orbit only; at most 10 satellites in the built-in list.
- No polar plot of the pass; the info screen gives live azimuth and elevation.
- Only the next pass per satellite is shown, within 24 hours, from 5 degrees elevation.
- A pass whose highest point is just above 5 degrees can be missed.
- No manual position entry: without a GPS fix since power-on there are no predictions.
- Trim is the only correction for the radio's own frequency error.
- On hardware, the standby display sometimes still showed the satellite frequency after leaving the screen at
  the end of a pass. If that happens, select the channel again (`Up` then `Down` at standby) to reload it.

How it works: [../satellite-mode.md](../satellite-mode.md), [../satellite-radio.md](../satellite-radio.md)
