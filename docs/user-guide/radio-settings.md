# Power, squelch and frequency range

Kraken adds three rows to the end of the stock **Menu > Settings > Radio Settings** list, after Voice, Key,
Display and Other Func: `TX Power Trim`, `Squelch Mode` and `Freq Unlock`. The rows show on any channel and
in VFO mode. A fourth item, **Squelch Survey**, is a live screen in the KRAKEN menu that helps you see what
the squelch is doing.

All three rows work like stock option lists: `Up`/`Down` move the cursor, `OK` saves the choice (the radio
shows `Setting is OK` and goes back to the list), and `Back` leaves without changing anything.

## TX Power Trim

Turns the transmit drive down below the stock Low, Mid and High levels, in eighths. Use it for very short
range work, such as two radios on the same bench.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > Settings > Radio Settings > TX Power Trim** (any channel)

### Using it
1. Open **Menu > Settings > Radio Settings > TX Power Trim**. The list is titled `TX Power Trim` and the dot
   marks the current value.
2. Choose `1/8` to `8/8` with `Up`/`Down`. `8/8` is stock power.
3. Press `OK`. The radio shows `Setting is OK`.

The trim applies to every transmission, analog and digital, on top of the channel's own Low, Mid or High
setting. A Mid channel at `4/8` uses half of Mid's drive, a Low channel at `4/8` half of Low's drive. The new
value takes effect at once on the current channel, or at the latest on the next channel change.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| TX Power Trim | `1/8`, `2/8`, ... `8/8` | `8/8` (stock) | No. Switching the radio off and on returns it to `8/8`. |

### Limits
- The fraction is a fraction of the drive signal to the power amplifier, not of the output power. `4/8` is
  not half the watts.
- Output power has not been measured with a meter. One relative check (433 MHz, Low, a second radio 2 m away)
  showed: `5/8` to `8/8` about the same as full Low; `4/8` about 48 dB lower; `1/8` to `3/8` essentially no
  output. The amplifier cuts out when its drive falls below about half of what Low uses. On Mid and High
  the drive starts higher, so the cut-out comes at a lower fraction.
- Because the trim is lost at power-off, check it after every power-up if you rely on it.

How it works: [../stock-option-level.md](../stock-option-level.md)

## Squelch Mode

Changes how the analog squelch judges a signal. Stock opens the squelch on signal strength (set by the
channel's squelch level) with a fixed noise check in the background. Normal and Tight replace that fixed
noise check with one of their own, so the squelch also looks at how clean the signal is.

**Status:** checked in the emulator only; not yet tested on a radio.
**Where:** **Menu > Settings > Radio Settings > Squelch Mode** (setting shown on any channel; it only acts on analog channels)

### Using it
1. Open **Menu > Settings > Radio Settings > Squelch Mode**. The list is titled `Squelch Mode`.
2. Choose `Off (stock)`, `Normal` or `Tight` with `Up`/`Down`.
3. Press `OK`. The radio shows `Setting is OK`. The change takes effect at once; no retune is needed.

| Mode | What it does |
|---|---|
| `Off (stock)` | Exactly the stock squelch. |
| `Normal` | Keeps the stock signal-strength threshold. The noise check opens a little more easily than stock and leaves a slightly wider gap between opening and closing, so a fading signal should chop less. |
| `Tight` | Keeps the stock signal-strength threshold. The noise check needs a cleaner signal than stock to open. Weak or noisy signals stay muted. |

Normal and Tight only act when all of these are true; otherwise the radio uses the stock squelch:
- the channel (or VFO) is analog;
- the channel's squelch level is not 0.

The **Squelch Survey** screen (below) can switch the signal-strength threshold off, so that only the noise
check decides. That choice is kept until power-off, and it changes what Normal and Tight do.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| Squelch Mode | `Off (stock)`, `Normal`, `Tight` | `Off (stock)` | No. Every power-up starts at `Off (stock)`. |

### Limits
- The Normal and Tight thresholds are estimates. They have not been tuned on a radio yet. Tight may mute
  weak signals you want to hear; if the radio seems too quiet, go back to `Off (stock)`.
- Digital (DMR) channels always use the stock squelch.

How it works: [../noise-squelch.md](../noise-squelch.md)

## Squelch Survey

A live screen that shows the receiver's noise reading, signal strength and squelch state ten times a
second. Use it to see why the squelch opens or stays shut, and to measure the noise on a quiet channel
against the noise with a station on the air.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > KRAKEN > Squelch Survey** (any channel; the readings are most useful on analog)

### Using it
1. Select the channel you want to look at, then open **Menu > KRAKEN > Squelch Survey**.
2. Watch lines 4 and 5: `noise` drops and `rssi` rises when a station keys up.
3. Optional short survey: press `5`. The screen averages 5 seconds of readings, then shows the minimum,
   average and maximum noise and signal strength.
4. Optional two-state survey: press `6`, keep the channel quiet for the first 3 seconds, then have another
   station key up once (a plain carrier is enough) somewhere in the 25-second window. Pressing `6` again
   restarts it.
5. Press `Back` to leave.

The survey never changes any setting. It only displays results.

| Key | Action |
|---|---|
| `1` | Switches between `gate:stock` (Normal and Tight keep the stock signal-strength threshold) and `gate:open` (signal strength is ignored; only the noise check decides). Acts at once and stays until power-off. Has no effect while the mode is `Off (stock)`. |
| `5` | 5-second survey of noise and signal strength. |
| `6` | 25-second survey with one key-up from another station; suggests noise thresholds. |
| `Back` | Leave the screen. |

### What the screen shows

| Line | Shows |
|---|---|
| 1 | `Sq survey:` and the current Squelch Mode (`Off`, `Normal` or `Tight`). Change the mode in Radio Settings, not here. |
| 2 | The mode name. In Normal or Tight on an analog channel, also `gate:stock` or `gate:open`. |
| 3 | `in` and `out`: the noise thresholds in use (squelch opens below `in`, closes above `out`). Shows `digital ch: stock` on a digital channel and `sqst level 0: stock` when the squelch level is 0. |
| 4 | `noise` (lower = cleaner signal) and `snr`. |
| 5 | `rssi` in dBm (uncalibrated) and `SQ OPEN` or `SQ shut`. |
| 6 | Two hex values starting `4F` and `78`: the raw threshold values in the receiver. Useful when reporting a problem. |
| 7-8, idle | `5:5s 6:25s survey` |
| 7-8, key 5 | `survey N/50 ...` while it runs, then `nz` (noise) and `dBm` (signal), each as min/avg/max. |
| 7-8, key 6 running | `auto N/25s` with `base` (first 3 s), `idle` or `CARRIER`; below it the lowest idle noise and highest carrier noise so far (`idle>=` / `carr<=`). |
| 7-8, key 6 done | `idle>=` / `carr<=`, then suggested noise pairs `nrm in/out tgt in/out` (for Normal and Tight), or `no carrier seen`, `no idle seen` or `no gap: no advice`. |
| 9 | `mode: Squelch Mode`, or `pairs: see UART` after a successful key 6 survey. |
| 10 | `1:gate 5/6:survey` |

### Limits
- The suggested pairs from key 6 are for reference. They cannot be applied from the menu; the Normal and
  Tight values are fixed in the firmware.
- `rssi` is the receiver's own uncalibrated reading. Compare readings with each other, not with a lab meter.

How it works: [../noise-squelch.md](../noise-squelch.md)

## Freq Unlock

Widens the frequencies the radio accepts, to the ranges used by the published DM-32 range mods. Use it to
listen outside the stock bands, or to transmit where you hold an authorization that covers the frequency.

**Legal and safety warning.** Transmitting outside the bands your licence or authorization covers is
illegal and can interfere with emergency, aviation and other services. The radio has no filtering for
these extra ranges. Output power and harmonics outside 136-174 and 400-470 MHz have not been measured on
this radio. Before you key up on a new frequency, check power and harmonics into a dummy load with a
spectrum analyser. Receive performance outside the stock bands is not guaranteed; owners of modded radios
report poor sensitivity on some of these frequencies.

**Status:** checked in the emulator only; not yet tested on a radio.
**Where:** **Menu > Settings > Radio Settings > Freq Unlock** (any channel)

### Ranges

| | Stock | Freq Unlock `ON` |
|---|---|---|
| Receive and keypad entry | 108-136 MHz (AM), 136-174 MHz, 400-470 MHz | 23-136 MHz (AM), 136-520 MHz |
| Transmit | 136-174 MHz, 400-470 MHz | 136-520 MHz |

- 23-136 MHz is AM and receive only, like the stock airband range. Transmit below 136 MHz is deliberately
  not allowed: the radio refuses `PTT` there.
- Below 280 MHz the radio uses its VHF receive and transmit path; from 280 MHz up it uses the UHF path.
- Transmit power outside the stock bands uses the nearest calibration point, so it is uncalibrated.

### Using it
To turn it on:
1. Open **Menu > Settings > Radio Settings > Freq Unlock**. The list is titled `Freq Unlock`.
2. Choose `ON` with `Up`/`Down` and press `OK`.
3. A warning fills the screen (`!! FREQ UNLOCK !!` ...) with `Hold OK 10 s: ON` at the bottom. Release
   `OK`, then press and hold `OK`. The bottom line counts down: `Holding... 10 s` to `1 s`. Letting go early
   restarts the count.
4. After 10 seconds the screen shows `Release OK`. Release it. The radio shows `Setting is OK`, then a
   popup `RX 23-520 MHz` / `TX 136-520 MHz` for 2 seconds.

To cancel at the warning, press `Back`. Doing nothing for 60 seconds also cancels. Either way the radio
shows `Unlock cancelled` and the setting stays `OFF`.

To turn it off: choose `OFF` and press `OK`. No hold is needed; the stock ranges return at once.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| Freq Unlock | `ON`, `OFF` | `OFF` | Yes, stored in the radio's settings memory. A factory reset turns it back to `OFF`; writing a codeplug with the programming software may also reset it. |

### Limits
- Satellite mode keeps the stock transmit limits.
- Not yet checked on a radio: how the VFO display and frequency steps behave at the new band edges, AM
  reception below 108 MHz, and whether the stock programming software accepts channels outside its own
  limits (enter such frequencies on the keypad).

How it works: [../freq-unlock.md](../freq-unlock.md)
