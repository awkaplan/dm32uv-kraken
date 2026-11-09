# APRS

Stock DM-32UV APRS works on DMR only. Kraken adds analog APRS: a 1200 baud AFSK beacon, a list of analog stations
heard, and an optional background decoder. It also fixes the bearing that stock shows for received DMR positions.

The APRS menu (**Menu > APRS**) is always shown, on analog and digital channels and in VFO mode, even when APRS is
turned off in the CPS. Its rows follow the mode of the selected channel:

| Selected channel | Rows |
|---|---|
| Analog | `APRS On/Off`, `Upload ID`, `Intervals Set`, `Upload Beacon`, `Callsign`, `APRS RX`, `RX Monitor` |
| Digital | the 8 stock rows: `APRS On/Off`, `Upload ID`, `Upload Type`, `Report Channel`, `Intervals Set`, `APRS Info`, `Upload Beacon`, `Delete All` |

`Upload ID` and `Upload Beacon` mean different things in the two lists. On an analog channel they are Kraken's
digipeater path and one-shot send. On a digital channel they are the stock DMR ID entry and the stock beacon
type setting (`Upload Beacon` there does not transmit).

With dual watch on, the list follows the side you selected with A/B, not the side being watched.

## Analog APRS beacon

Sends your position (or a status text when the radio has no GPS fix) as a standard APRS packet on the current
analog channel, by hand or on a timer. Any APRS receiver, digipeater or igate can decode it.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > APRS** (analog channels only): `APRS On/Off`, `Upload ID`, `Intervals Set`, `Upload Beacon`, `Callsign`

### Set your callsign
The beacon will not transmit until a callsign is saved.

1. Open **Menu > APRS > Callsign**. The screen shows `Call:` and `SSID:`.
2. Type the callsign with the keypad:
   - `2`-`9`: letters by multi-tap, as on a phone (`2` gives A, B, C, then 2). Press the same key again within
     1 second for the next letter; pause 1 second to accept it. `0` and `1` type the digit.
   - `Up`/`Down`: step the last character through A-Z and 0-9.
   - `*`: delete the last character.
3. Press `#` to move to `SSID:`. Type 0-15 with the digit keys (`*` deletes a digit). `none` means no SSID.
   Press `#` again to go back to the callsign.
4. Press `OK` to save. The status line shows `Saved`, or `Need 3-6 A-Z/0-9` if the callsign is not 3 to 6
   letters and digits with at least one of each.
5. Press `Back` to leave. Unsaved changes are dropped.

The same callsign is used by CW ID (**Settings > Radio Settings > Other Func > CW ID > Callsign**).

### Choose the digipeater path
1. Open **Menu > APRS > Upload ID**. The list is titled `APRS path` and the dot marks the current path.
2. Choose one with `Up`/`Down` and press `OK`. The radio shows `Setting is OK` (or `Save FAILED` if the
   settings memory could not be written).

| Path | Use |
|---|---|
| `WIDE1-1,WIDE2-1` | the usual path for a handheld (default) |
| `WIDE1-1` | one hop through a fill-in digipeater |
| `WIDE2-1` | one hop through a wide-area digipeater |
| `Direct` | no digipeating |

### Send one beacon now
1. Select the analog channel to send on (for example 144.390 MHz in North America).
2. Open **Menu > APRS > Upload Beacon** and press `OK`. A popup shows `OK again to send` / `TRANSMITS`.
3. Press `OK` again within 3 seconds. Any other key, or waiting, cancels with nothing sent.
4. The radio shows `Sending...`, transmits for about one second, then shows `Sent position` or `Sent status`.

If it cannot send, a popup says why: `Set callsign first`, `Channel busy` (a signal is being received),
`TX inhibited`, `TX in use` (you are already transmitting), `Dual watch: retry`, `Analog channel only`, or
`Busy` (another send is running). `TX did not come up` or `Modem stalled` mean the send failed; try again.

### Automatic beacon
The stock interval timer drives the analog beacon on analog channels, the same way it drives DMR APRS on
digital channels.

1. On the analog channel, set **Menu > APRS > APRS On/Off** to on. This is the stock per-channel switch.
2. Set **Menu > APRS > Intervals Set** to the interval you want. Off stops the automatic beacon.
3. Return to standby. The first beacon goes out one interval later, then one every interval.

- If the channel is busy when a beacon is due, the radio tries again 5 seconds later until it gets through.
- If the radio is scanning or the channel does not allow transmit, that beacon is skipped.
- Intervals shorter than 30 seconds do not send.
- A programmable key set in the CPS to the stock beacon-send function sends the analog beacon on an analog
  channel at once, with no confirmation. Stock allows that key only with a GPS fix (or the stock fixed-beacon
  type).

### What is sent

| | With a GPS fix | Without a fix |
|---|---|---|
| Packet | position: `!DDMM.mmN/DDDMM.mmW[DM-32UV Kraken` | status: `>DM-32UV Kraken no GPS fix` |
| Symbol | person (`/[`) | none |

Source is your callsign and SSID, destination (tocall) `APZKRK`, path as set under `Upload ID`. The position is
rounded to 1/100 of a minute (about 18 m). Symbol and comment are fixed and cannot be changed on the radio.
The mic is muted during the send. The channel's own power level is used.

### Dual watch
The beacon always goes out on the side you selected with A/B. If the receiver is on the other side, the radio
waits up to 1.5 seconds for it to return, holds dual watch on the selected side for the send, and then resumes.
If the selected side does not come back in time, the send is refused with `Dual watch: retry` (automatic
beacons try again shortly).

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| Callsign, SSID | 3-6 letters/digits; SSID none or 1-15 | none (beacon refuses) | Yes |
| Upload ID (path) | see the path table | `WIDE1-1,WIDE2-1` | Yes |
| APRS On/Off | stock, per channel; shared with DMR APRS | from the CPS | Yes (stock) |
| Intervals Set | stock, radio-wide; shared with DMR APRS | from the CPS | Yes (stock) |

Callsign and path are kept in the radio's settings memory. Writing a codeplug with the CPS, or a factory reset,
erases them (and the CW ID setting): set the callsign again afterwards.

### Limits
- Analog channels only. On a digital channel the stock DMR APRS rows and sends apply.
- The automatic analog beacon has not yet been confirmed on a radio; the hand-sent beacon has (decoded off the
  air by an independent decoder). After setting up the timer, check with a receiver or an APRS website that
  beacons arrive.
- The automatic beacon may also need APRS enabled in the CPS (the stock DMR beacon does). If APRS On/Off is set
  in the CPS, choose the Digital report mode: stock ignores the Analog value, and so does Kraken.
- With stock power save on, the interval can run short (up to about twice as often as set).
- A send cannot be stopped once it starts. It ends by itself after about one second.
- Use your own callsign. The beacon is your station identification.

How it works: [../aprs-submenu.md](../aprs-submenu.md), [../afsk-tx.md](../afsk-tx.md), [../fm-send-ram-tx.md](../fm-send-ram-tx.md)

## APRS RX

Decodes analog APRS packets on the current channel and lists the stations heard. Use it to check that your
beacon is heard, or to see who is active on 144.390 MHz.

**Status:** the decoder works on hardware (it decoded another radio's beacon on screen); this stations screen and its menu rows have only been checked in the emulator, and real APRS traffic on 144.390 has not been tried yet.
**Where:** **Menu > APRS > APRS RX** (analog channels only)

### Using it
1. Tune the analog APRS channel.
2. Open **Menu > APRS > APRS RX**. The screen starts listening at once.
3. Press `Back` to leave. There are no other keys.

The speaker is silent while this screen is open. Unless RX Monitor is on, the list starts empty each time you
open the screen.

### What the screen shows

| Line | Shows |
|---|---|
| Top | `APRS RX listening`, or `APRS RX 3 stations` (`APRS MON ...` when RX Monitor is on) |
| Lines 1-8 | one station per line, newest first: callsign-SSID, time since last heard (`12s`, `4m`, `1h`), then the first characters of the packet text (for example `!4026.7`) |
| Bottom | `RX Monitor on h...` or `RX Monitor off`; or `no RX: reload ch` (see Limits) |

A station heard again moves to the top with its new text. When the list is full, the oldest station drops off.

### Limits
- Analog only. On a digital channel the screen shows `Analog channel only`.
- It shows only the start of each packet. It does not decode positions or show distance.
- Up to 8 stations. The list is lost at power-off.
- `no RX: reload ch` means no receive audio is reaching the decoder, which can happen right after power-on.
  Switch to another channel and back.
- Decoding of another DM-32UV's beacon is confirmed on hardware. Real 144.390 MHz traffic has not been
  confirmed yet.

How it works: [../rx-audio-fm-mode.md](../rx-audio-fm-mode.md)

## RX Monitor

Keeps the APRS RX decoder running at standby, so stations heard while you are not looking are in the list when
you open **APRS RX**. The speaker keeps playing.

**Status:** checked in the emulator only; not yet tested on a radio.
**Where:** **Menu > APRS > RX Monitor** (analog channels only)

### Using it
1. Open **Menu > APRS > RX Monitor**. Choose `On` and press `OK`. The radio shows `Setting is OK`.
2. Return to standby on an analog channel. Packets are decoded in the background. Nothing shows at standby.
3. Open **Menu > APRS > APRS RX** to see the list. The title reads `APRS MON`.

The monitor pauses by itself, and resumes a moment later, while:
- any Kraken screen is open (APRS RX then does the decoding itself);
- a Kraken transmission (APRS beacon, CW ID, Burst Voice) is in progress;
- you transmit or hold `PTT`;
- the channel changes, or the received channel is digital.

With dual watch on, it decodes whichever side is being received, if that side is analog.

### Settings

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| RX Monitor | `Off`, `On` | `Off` | No. It returns to `Off` at power-off. |

### Limits
- Receive only. It never transmits, digipeats or igates.
- No alert or standby display when a packet arrives; open APRS RX to check.

How it works: [../aprs-rx-monitor.md](../aprs-rx-monitor.md)

## Bearing fix for DMR APRS Info

Stock shows the distance and course to a received DMR position, worked out from your own GPS fix. Stock ignored
the N/S/E/W letters, so for anyone west of Greenwich or south of the equator the course was mirrored (a station
to the north-east showed as north-west), and distances between hemispheres were wrong. Kraken corrects this.

**Status:** works on hardware.
**Where:** stock **Menu > APRS > APRS Info** and the position popup (digital channels). No setting; always on.

### Using it
Nothing to do. Distance and course are worked out once, when the position arrives, and stored with it in
APRS Info. Positions received after installing Kraken get the corrected values. Entries already in APRS Info
keep the old ones; clear them with the stock **Menu > APRS > Delete All** if you like.

### Limits
- Needs a GPS fix on your radio when the position arrives; without one stock shows no course.
- Correct when both stations are on the same side of Greenwich and of the equator. A pair on opposite sides can
  still get the wrong quadrant (a stock limit). A coordinate exactly on 0 degrees fails as in stock.
- Stock shows and logs a DMR position only as the reply to a position request sent from this radio; unrequested
  DMR beacons are not shown as positions. Between two DM-32UVs, such an exchange has been seen to keep
  repeating until both radios were switched off. This is stock behaviour; Kraken does not change it.

How it works: [../aprs-stock-gaps.md](../aprs-stock-gaps.md), [../aprs-positions.md](../aprs-positions.md)
