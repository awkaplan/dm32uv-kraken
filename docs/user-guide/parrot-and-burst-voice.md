# Parrot and Burst Voice

Two features that store DMR voice in the radio and send it again. **Parrot** records the last DMR call
you heard and plays it back on the same channel as a DMR call. **Burst Voice** takes a short DMR voice
message and sends it as a fast data burst on an analog FM channel. Another Kraken radio receives the burst
and plays it.

Both use the same memory in the radio, so only one can be in use at a time. While Parrot is On, Burst Voice
shows `Parrot is armed`. While Burst Voice holds a message, Parrot cannot be turned On.

## Parrot

Parrot records the last DMR voice call the radio heard. It can send that call back as a DMR call on the same
channel, either when you ask for it or automatically after each call (a simplex parrot). Use it to let
another station hear how they sound, or to check your own audio and timing.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > Settings > Channel Set**, rows `Parrot On/Off`, `Replay Audio`, `Check`, `Clear Audio`,
`Status`. The rows are shown on every channel. Recording and transmitting work on digital (DMR) channels only.

| Row | Kind | What it does | Transmits? |
|---|---|---|---|
| `Parrot On/Off` | Off / On choice | Turns recording on. Stays On after you leave the menu. | No |
| `Replay Audio` | action | Sends the recorded call once. | Yes, after `OK` twice |
| `Check` | action | A short transmit test (see below). | Yes, after `OK` twice |
| `Clear Audio` | action | Deletes the recording. | No |
| `Status` | live screen | Shows what Parrot is doing. Runs the automatic echo while open. | Yes, automatic echo |

### Using it
1. Select a digital channel. Turn dual watch off (see Limits).
2. **Menu > Settings > Channel Set > Parrot On/Off**, select `On`, press `OK`. From now on the radio records
   each DMR call it hears. Each new call replaces the previous recording.
3. To send the last call once: open `Replay Audio`. The popup `OK again to TX` / `TRANSMITS` appears. Release
   `OK`, then press `OK` again within 3 seconds. Any other key, or waiting, cancels with nothing sent.
   The radio shows `Playing...`, transmits the recording, and then shows `Played` with the number of 60 ms
   voice frames sent, or a reason why not (table below).
4. For an automatic echo: open `Status` and leave it open. After a call ends and the channel has been quiet
   for 1.5 seconds, the radio keys up and sends the call back. One echo per call, then a 3-second pause.
5. To stop a transmission: press any key. On the `Status` screen, `Back` also stops it.
6. To delete the recording: open `Clear Audio` (popup `Cleared`).
7. To stop Parrot: set `Parrot On/Off` to `Off`. This also deletes the recording and frees the memory for
   Burst Voice.

The echo is a normal DMR call from your radio: your DMR ID, the channel's Tx Contact, timeslot and power.

**Check** is a transmitter test. After the same `OK again to TX` confirmation it keys up, sends about a
quarter second of your microphone, then the recording (or 0.9 s of silence if there is none), and unkeys.
The result popup `Check done` / `up 0.3s rel 1` gives the time from key-up to transmitting voice (`up`) and how
many unkey commands were needed (`rel`, normally 1). Afterwards lines 7 and 8 of the `Status` screen show the
same result. Check works without a recording.

### What the screen shows (`Status`)
| Line | Example | Meaning |
|---|---|---|
| 0 | `Parrot ARMED` | `ARMED` = On, `off` = Off |
| 1 | `HOLD 0.8s` | State: `OFF`, `IDLE` (waiting), `REC` (recording a call), `HOLD` (quiet time before the echo), `KEYING`, `SEND`, `UNKEY`, `COOL` (3 s pause after an echo); then the last reason, e.g. `done` or `aborted` |
| 2 | `Cap 49 fr 2.9s` | Recorded frames and length; `FULL` when the 12.5 s limit was reached |
| 3 | `TG 91` | Destination of the recorded call: `TG`, `PC` (private call) or `ALL` |
| 4 | `from 1234567` | DMR ID of the station recorded |
| 5 | `echoes 3 lead2` | Echoes sent; while sending, `TX 12/51` (frame sent / total). `lead` = silent frames before the voice |
| 6 | `tx.. sub.. rx..` | Frame counters, for troubleshooting |
| 7 | `gates ok` | Or `gate <reason>`: why an echo would be refused right now |
| 8-9 | `U/D:lead Back:exit` | Key help |

Keys: `Up` / `Down` change the lead (not while transmitting). Any key stops an echo in progress or cancels a
pending one. `Back` leaves the screen; Parrot stays On and the recording is kept, but no automatic echo
happens until you open `Status` again. A call that ended before you opened `Status` is not echoed.

### Refusals and results
| Text | Meaning |
|---|---|
| `Parrot is off` / `Set it On first` | `Replay Audio` or `Check` with Parrot Off |
| `no capture` | No recording of at least 0.3 s |
| `not digital` | The selected channel is analog |
| `dual/side chg` | Dual watch is active, or the recording came from the other side (A/B) |
| `encrypted` | The channel uses the radio's built-in encryption |
| `TX not allowed` | Transmit is not allowed on this frequency |
| `TX inhibited` | The radio's transmit inhibit is on |
| `channel busy` | A call is being received or recorded, or the previous transmission is still ending |
| `own ID` | The recording is of your own DMR ID |
| `aborted` | A key was pressed, or a call started while keying up |
| `TX never up` | The radio did not start transmitting within 3 s |
| `TX stuck` | The radio did not unkey on time; Kraken kept sending unkey commands |
| `stock ended TX` | The radio ended the transmission itself (for example the TOT) |
| `Recording now` / `Try again` | `Clear Audio` while a call is being recorded |

### Settings
| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| `Parrot On/Off` | `Off`, `On` | `Off` | No: Off after every power-on |
| Lead (on `Status`, `Up`/`Down`) | 0-8 frames of 60 ms | 2 (120 ms) | No |

The recording itself is lost at power-off.

### Limits
- Records up to 12.5 s of a call; the rest of a longer call is dropped. Calls shorter than 0.3 s are not echoed.
- Calls from your own DMR ID are never recorded.
- While dual watch is active Parrot does not record, and it never transmits on the other side from where it
  recorded. Turn dual watch off for reliable use.
- The automatic echo runs only while the `Status` screen is open.
- If Burst Voice is holding a message, `On` does not take: the stock `Setting is OK` popup still appears, but
  the row reads `Off` when you reopen it. Clear the message in Burst Voice first (key `0`, then `Back`).
- Every transmission has a time limit (recording length plus 1 s) and keeps sending unkey commands until the
  radio is proven unkeyed. After a stopped or failed transmission the rest is sent as silence, never as live
  microphone audio.
- Legal: the automatic echo retransmits other stations' audio. Some licences restrict this (in the US, Part 97
  allows automatic retransmission of other amateur stations only by repeater, auxiliary and space stations).
  Check your rules before leaving the echo running, and stay at the radio.

How it works: [../parrot-tx.md](../parrot-tx.md)

## Burst Voice

Burst Voice takes a short DMR voice message in the radio's compressed digital voice format and sends it as a data
burst on an analog FM channel. Another radio running Kraken receives the burst, rebuilds the voice and plays it
on its speaker, or sends it on as a normal DMR call. The message can come from the last DMR call you heard, from
your microphone, or from a burst you received. Both radios need Kraken; a radio without it hears only a few
seconds of data noise.

**Status:** works on hardware; the menu layout described here is newer and has only been checked in the emulator.
**Where:** **Menu > Settings > Channel Set > Burst Voice**, a sub-menu with four rows:
`Burst Voice` (the screen), `Mode`, `Key-up Lead`, `Relay Capture`. Sending and receiving bursts work on analog
channels; recording, relay capture and sending as a DMR call work on digital channels.

### Using it
To send a heard DMR call as a burst:
1. **Settings > Channel Set > Burst Voice > Relay Capture**, select `ON`, press `OK`.
2. Select a digital channel. Open the `Burst Voice` screen. Line 1 shows `relay armed`.
3. Wait for a call. Line 1 shows `relay REC` and a frame count; after the call the status line shows
   `call captured` and line 2 shows `Msg 3.2s relay`. Each new call replaces the message.
4. Press `Back`. The message is kept. Change to an analog channel with the normal channel keys.
5. Open the `Burst Voice` screen again. Press `OK`; line 7 shows `PRESS AGAIN TO TX`. Press `OK` again
   within 3 seconds. The radio shows `Sending...`, transmits the burst, then shows `Sent`.

To record your own voice instead: on a digital channel, open the screen, press `1` and speak into the
microphone without pressing `PTT`. The status line shows `Recording...`, then `Recorded`. Recording stops when
the message is full (about 5.5 s) or when you press a key. The first quarter second is dropped. Key `1` never
transmits.

To receive: on the other radio, select the same analog channel and open the `Burst Voice` screen. Line 1 shows
`RX listening`. When a burst arrives the screen pauses while it decodes, then shows `burst received` and the
sender's DMR ID. Press `4` to play it.

### Keys on the screen
| Key | Action |
|---|---|
| `OK`, `OK` | Send the message as an analog burst (analog channel only; second press within 3 s). On a digital channel: `OK = analog send` |
| `1` | Record from the microphone (digital channel only; no transmit) |
| `4` | Play the message on the speaker (any channel) |
| `5`, `5` | Send the message as a DMR voice call (digital channel only; second press within 3 s) |
| `0` | Delete the message (`cleared`) |
| `2`, `3`, `Up`, `Down` | `Change in the menu`: these settings are now in the sub-menu |
| `Back` | Leave the screen. A message is kept |

Any key stops a send, playback or reception in progress. That key is used up, so press `Back` again to leave.
Any key other than the second `OK` (or `5`) cancels a pending send.

### What the screen shows
| Line | Example | Meaning |
|---|---|---|
| 0 | `Burst FAST analog` | Mode (`FAST`/`ROBUST`) and channel type (`analog`/`DMR`) |
| 1 | `RX listening` | Analog: receiver `listening`, `sync`, `receiving`, or `rx off`. Digital: `relay off`, `relay armed`, `relay REC 12fr` |
| 2 | `Msg 3.2s relay` | Message length and source: `relay` (heard call), `rx` (received burst), `mic`; or `No message` |
| 3 | `from 1234567 40s` | Source DMR ID and age |
| 4 | `air ~4.3s L1` | Estimated air time of the burst (an upper bound; silence is trimmed when sent) |
| 5 | `blk 3/3 rs2 -9dB` | Last received burst: good blocks / total, bytes corrected, signal quality |
| 6 | status | The last result (table below), or `no RX: reload ch` |
| 7 | `lead 320ms` | Key-up lead, or `PRESS AGAIN TO TX` |
| 8-9 | `OK:send 1:rec 0:clr` / `4:play 5:dmr` | Key help |

`no RX: reload ch` means the radio's receive audio feed has not started (seen after power-on or a channel type
change). Change to another channel and back, or switch the A/B side and back.

If Parrot is On, the screen shows only `Parrot is armed` / `Disarm the parrot,` / `then press a key`. Set
Parrot Off, then press any key.

### Status messages
| Text | Meaning |
|---|---|
| `Sent` | The burst went out |
| `burst received` | A burst was decoded and is now the message |
| `burst damaged` | Some blocks were lost; those parts play as silence |
| `burst lost`, `burst aborted`, `payload bad`, `rx half missed` | Reception failed |
| `rx cancelled` | A key stopped a reception |
| `call captured` / `call too short` | Relay capture result (minimum 0.3 s) |
| `Recorded`, `no encoder bursts`, `no frames kept` | Microphone recording result |
| `Played`, `DMR sent` | Playback or DMR send finished |
| `Analog channel only`, `Digital channel only` | Wrong channel type for that key |
| `Dual watch: retry` | Dual watch was on the other side; try again |
| `Channel busy` | A signal is being received, or a transmission is still active |
| `TX not allowed here`, `TX inhibited`, `TX in use`, `Encrypted channel` | Transmit refused |
| `No message`, `Too long (raw units)` | Nothing to send, or the message could not be packed |
| `Aborted`, `TX timeout`, `TX did not come up`, `Modem stalled`, `TX stuck: released` | The send stopped early |

Receiving a burst replaces the current message, and so does a new call while relay capture is armed.

### Settings
Under **Menu > Settings > Channel Set > Burst Voice**. Each is a stock-style choice: select a value and press `OK`
(`Setting is OK`).

| Setting | Values | Default | Kept at power-off? |
|---|---|---|---|
| `Mode` | `Fast`, `Robust` | `Fast` | Yes |
| `Key-up Lead` | `0 ms`, `128 ms`, `192 ms`, `256 ms`, `320 ms`, `480 ms`, `640 ms`, `960 ms` | `320 ms` | Yes |
| `Relay Capture` | `ON`, `OFF` | `OFF` | Yes |

- `Fast` sends 6 kbit/s, `Robust` 4 kbit/s (longer on air, more tolerant of noise). The receiver detects the
  mode by itself.
- `Key-up Lead` is silence sent before the data so the receiving radio's squelch is open when it starts. If
  bursts are lost, try `Robust` and a longer lead; `Robust` with 640 ms worked well in testing.
- `Relay Capture` records heard calls only while the `Burst Voice` screen is open on a digital channel.
- A factory reset or a full write from the programming software returns these to the defaults.

The message is kept in memory after `Back` and lost at power-off.

### Limits
- A message holds up to about 5.5 s of voice. Longer calls are cut.
- The receiver listens only while the `Burst Voice` screen is open on an analog channel.
- Sending a message as a DMR call (key `5`) has only been checked in the emulator.
- While a message is kept, Parrot cannot be turned On. Press `0`, then `Back`, to free the memory.
- Legal: a burst is a data transmission (PSK tones at 2000 baud inside the FM audio). Check your band plan and
  licence conditions for data on the frequency you use. The format is published in the developer note below.

How it works: [../burst-voice.md](../burst-voice.md), [../chanset-submenu.md](../chanset-submenu.md)
