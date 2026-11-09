# VOX and CW ID

Two hands-free transmit features in **Menu > Settings > Radio Settings > Other Func**, plus a
live screen for setting up VOX. Both key the transmitter on their own, so read the warnings
before you switch either of them on.

Other Func lists them as two sub-menus, laid out like stock's own:

```
Other Func > VOX      > VOX SW, Gain Level, VOX Delay
           > CW ID    > CW ID SW, Interval, Callsign      (analog channels only)
```

`OK` opens a row, `Up`/`Down` move, `OK` on an option confirms with stock's `Setting is OK`
popup, `Back` goes up one level. Stock's separate `VOX Delay` row in Other Func is gone; it is
now the third row under VOX. On an analog channel CW ID has taken its place in the list.

## VOX

Voice-operated transmit: the radio keys up when you talk into the mic and unkeys after you stop.
Stock firmware 048 shows VOX rows in its menu but never implemented them. Kraken makes those
stock rows work.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > Settings > Radio Settings > Other Func > VOX** (rows show on any channel; VOX only works on analog channels)

> **Warning:** with `VOX SW` set to `ON`, the radio transmits whenever its mic hears sound loud
> enough: your voice, people nearby, wind, a dropped radio. The setting is kept at power-off, so
> the radio is ready to transmit again about 5 s after you switch it on.

### Using it
1. Select an analog channel (or VFO) on the main side.
2. Open **Menu > Settings > Radio Settings > Other Func > VOX > Gain Level** and pick a level.
   Start with `Level 3`. Use the [VOX Monitor](#vox-monitor) to choose one for your mic.
3. Open **VOX Delay** and pick how long the radio stays keyed after you stop talking.
4. Open **VOX SW**, choose `ON`, press `OK`.
5. Press `Back` until you reach standby. Wait 1.5 s, then talk. The radio keys up about 30-50 ms
   after your voice gets loud enough, and unkeys once you have been quiet for the VOX Delay.
6. To stop: set **VOX SW** to `OFF`.

Both the built-in mic and an earpiece mic work.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| VOX SW | `ON`, `OFF` (this channel or VFO only) | `OFF` | Yes, stored with the channel / VFO |
| Gain Level | `Level 1` .. `Level 9`; higher = less sensitive | stock's | Yes (radio-wide) |
| VOX Delay | `0.3 s`, `0.5 s`, `1.0 s`, `1.5 s`, `2.0 s`, `3.0 s`, `4.0 s`, `5.0 s` | stock's | Yes (radio-wide) |

`VOX SW` follows the channel: each memory channel and the VFO has its own switch. Gain Level and
VOX Delay apply to all of them. If VOX Delay was set to another value by stock firmware or by
CPS, the list shows the nearest of the eight.

### Limits
- Analog only. On a DMR channel the switch can be set but nothing keys.
- It listens only on the selected (main) side, and not while Dual Watch is running.
- It does not key while the radio is receiving a signal, or with TX inhibited.
- It never keys while a menu is open, or for 1.5 s after you leave one (the key beep and your
  hand moving away would otherwise key it). It also waits 5 s after power-on.
- A single VOX transmission is cut after 180 s; after any unkey it waits 0.5 s before listening again.
- To listen for your voice, the radio switches its receiver off for a moment about three times a
  second. A weak incoming signal can be noticed a fraction of a second late.
- While VOX is armed, the radio does not enter power save (asleep it cannot hear the mic), so the
  battery drains faster. Your Power Save setting itself is not changed.

How it works: [../otherfunc-submenu.md](../otherfunc-submenu.md)

## VOX Monitor

A live screen for the VOX engine: the mic level it hears, the thresholds it uses
and what it is doing. Use it to pick a Gain Level for your mic before you switch VOX on.

**Status:** works on hardware.
**Where:** **Menu > KRAKEN > VOX Monitor** (works on analog channels only)

> **Warning:** key `1` on this screen cycles a test mode that overrides VOX SW. In
> `Live` the radio transmits on sound, and the mode **stays set after you leave the screen**,
> until you set it back to `Off` or switch the radio off.

### Using it: pick a Gain Level
1. Select an analog channel on the main side; Dual Watch off.
2. Open **Menu > KRAKEN > VOX Monitor**.
3. Press `1` until line 1 reads `mode Sense`. Sense listens and counts; it never transmits.
4. Stay quiet for a few seconds and note `lvl` (the idle floor, typically 15-30).
5. Talk as you would on the air, at your usual mic distance. Watch `lvl` and the `hot` count.
6. Pick the highest Gain Level whose threshold (table below) your speech still passes easily:
   `hot` should rise on every phrase, not on room noise.
7. Press `1` until line 1 reads `mode Off` (it goes Off > Sense > Dry > Live > Off), then `Back`.

| Gain Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| Key threshold (`thr`) | 60 | 100 | 150 | 200 | 260 | 330 | 420 | 520 | 640 |

### What the screen shows

| Line | Example | Meaning |
|---|---|---|
| 0 | `VOX engine v16` | Title. |
| 1 | `VOX>Live sensing` | What armed the engine and its state. `mode` = this screen's test mode (or nothing armed); `VOX>` = armed by VOX SW. Mode: `Off`, `Sense`, `Dry`, `Live`. State: `idle`, `sensing`, `KEY`, `TX`, `UNKEY`, `LOCK` (short wait after an unkey), `DRY-TX`, `menu hold`. |
| 2 | `lvl 34 gate 0` | `lvl`: loudest mic level in the last listening window, or the live level while transmitting. `gate`: `0` = listening allowed; `1` nothing armed, `2` not an analog channel, `3` not the main side, `4` Dual Watch running, `5` transmitting, `6` receiving a signal. |
| 3 | `thr 150/60 G3 s20` | Key threshold / release level. `G3` = following VOX Gain Level 3; `man` = a manual threshold set with `2`/`3`. `s` is a timing value for testing. |
| 4 | `p300 h10 sw10 af+m` | `p`: ms between listening windows. `h`: hold time in tenths of a second (the VOX Delay). `sw`: VOX switch state as two digits. `af`/`na`, `+m`: test settings. |
| 5 | `win 120 hot 4` | Listening windows since the last reset, and how many were loud enough to key. |
| 6 | `key 4 ref 0 hang` | Times keyed, times refused, and why the last transmission ended: `hang` (you stopped talking), `tot` (180 s limit), `drop` (ended by something else), `noTX` (transmitter did not come up), `inhib` (TX inhibited), `menu` (a menu opened), `cut` (cut short), `-` (none yet). |
| 8-9 | `1mode 2/3thr 9auto` | Key help. |

### Keys

| Key | Action |
|---|---|
| `1` | Test mode: `Off` > `Sense` (count only) > `Dry` (a hot window is timed as if keyed, no transmission) > `Live` (transmits) > `Off` |
| `2` / `3` | Lower / raise the key threshold by 20 (manual; overrides the Gain Level) |
| `9` | Back to the Gain Level threshold |
| `0` | Zero the counters on lines 5 and 6 |
| `4`-`8` | Timing and audio-path test settings; leave them alone |
| `Back` | Leave the screen (the test mode stays as set) |

Everything set on this screen is lost at power-off.

### Limits
- `Sense`, `Dry` and `Live` keep the radio out of power save until set back to `Off`.
- In `Dry` the receiver stays off for the timed "transmission".

How it works: [../otherfunc-submenu.md](../otherfunc-submenu.md)

## CW ID

Sends your callsign in Morse on the selected analog channel at a fixed interval: 20 WPM, an
800 Hz tone, after half a second of carrier so receivers' squelch opens. A six-character call
takes about 5 s on the air.

**Status:** works on hardware (IDs at a 1-minute interval, decoded off the air); the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > Settings > Radio Settings > Other Func > CW ID** (analog channels only; the row is hidden on DMR channels)

> **Warning:** with `CW ID SW` set to `ON`, the radio transmits on its own every Interval, whether
> or not you have transmitted, and keeps doing so after power-off and back on. It is a plain
> timer: it does not track your own transmissions. Check that this kind of automatic
> transmission is allowed where you operate.

### Using it
1. Select an analog channel.
2. Open **Menu > Settings > Radio Settings > Other Func > CW ID > Callsign** and enter your call
   (the same editor and the same stored callsign as **APRS > Callsign**). `Back` returns to the CW ID list.
3. Open **Interval** and pick a time.
4. Open **CW ID SW**, choose `ON`, press `OK`. If no callsign is set, a `Set a callsign` warning follows.
5. Press `Back` until you reach standby. The first ID goes out one Interval later.
6. To stop: set **CW ID SW** to `OFF`.

The timer starts when the switch is `ON`, a callsign is set and an analog channel is selected.
Moving to a DMR channel stops it; back on an analog channel it starts a full Interval again.
The Interval is real time, also while the radio is in power save.

When an ID is due but the radio cannot send it:

| Situation | What happens |
|---|---|
| Channel busy (receiving) | tries again in 5 s |
| You are transmitting (PTT or VOX) | tries again in 5 s |
| Dual Watch has the other side | waits up to 1.5 s, else tries again in 5 s |
| A menu or a KRAKEN screen is open | waits until you close it |
| TX inhibited | skips this ID; the next one is one Interval later |

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| CW ID SW | `ON`, `OFF` | `OFF` | Yes |
| Interval | `1 min`, `2 min`, `3 min`, `5 min`, `10 min`, `15 min`, `20 min`, `30 min` | `10 min` | Yes |
| Callsign | 3-6 characters, `A`-`Z` and `0`-`9`, at least one letter and one digit; shared with APRS | none | Yes |

The settings apply to the whole radio, not per channel. A vendor CPS write or a factory reset
switches CW ID off. The APRS SSID is not sent.

### Limits
- Analog only. Uses the channel's own transmit power.
- The ID is sent on the main side's channel.
- No ID while a menu or KRAKEN screen stays open, so leaving the radio in a menu delays it.

How it works: [../cw-id.md](../cw-id.md), [../otherfunc-submenu.md](../otherfunc-submenu.md)
