# Feature status

One row per feature. The status comes from the **Status** line of each [user guide](user-guide/README.md)
page, which is the authoritative source. If the two disagree, the user guide wins.

Status values:

- **hardware**: used on a real DM-32UV.
- **hardware, menu newer**: the feature worked on a radio, but its menu rows have since moved. The new
  layout has only been checked in the emulator (QEMU).
- **emulator only**: built and checked in QEMU, not yet on a radio.
- **experimental**: partly tested; see the note.

IDs are the ones used in commit messages, code comments and the notes in `docs/`.

**Firmware builds.** Every status below is for stock `DM32.00.01.048`. The same features are built for
`DM32.01.L01.048` and `DM32.01.01.049` from a checked translation ([firmware-builds.md](firmware-builds.md)) and run
the same QEMU tests; on those two builds every feature is **emulator only** until it is used on a radio.

| ID | Feature | What it does | Status | User guide |
|---|---|---|---|---|
| — | Update file install | One update file per firmware (`kraken_<firmware>_update_BFUV32-V2.bin`), installed with the radio's own updater | hardware (one radio so far, on 048); L01 and 049: emulator only | [install](user-guide/install.md) |
| — | Web installer | Chrome or Edge page that backs the radio up, applies Kraken in the browser and installs it with the radio's updater; can also put stock back | experimental: the reader starts on a radio (one cable, macOS); full backup, install and restore not yet tested on a radio | [web-flasher.md](web-flasher.md) |
| F0a | Stronger MI random numbers | AES-256 CTR_DRBG seeded from hardware noise replaces the unseeded generator behind stock DMR encryption | hardware | [privacy](user-guide/privacy.md) |
| F1 | Last Heard | Recent DMR callers: ID, alias or contact name, slot, colour code, RSSI, time | hardware, menu newer | [receive tools](user-guide/receive-tools.md) |
| F2 | RSSI Meter | Signal strength in dBm and S-units with a bar and peak hold | hardware | [receive tools](user-guide/receive-tools.md) |
| F3 | DMR Link | Per-burst DMR link quality: EMB/check errors, EVM, sync bit errors | hardware, menu newer | [receive tools](user-guide/receive-tools.md) |
| F4 | GPS Grid | Maidenhead locator from the GPS fix | hardware, menu newer | [receive tools](user-guide/receive-tools.md) |
| F5 | Band Scope | 48-point signal sweep around the current analog frequency | hardware | [receive tools](user-guide/receive-tools.md) |
| F6 | TX Power Trim | Transmit drive in eighths below the channel's Low/Mid/High | hardware, menu newer | [radio settings](user-guide/radio-settings.md) |
| F7 | DMR APRS bearing fix | Correct course and distance in stock APRS Info west of Greenwich or south of the equator | hardware | [APRS](user-guide/aprs.md) |
| F8 | Analog APRS beacon | 1200 baud AFSK AX.25 position or status beacon, by hand or on the stock timer | hardware, menu newer (automatic timer confirmed on a radio 2026-11-11; its power-save correction not yet) | [APRS](user-guide/aprs.md) |
| F8a | APRS Beacon Text | The owner's comment for the analog beacon (up to 43 characters, typed on the keypad), sent instead of the default `DM-32UV Kraken` | hardware | [APRS](user-guide/aprs.md#set-the-beacon-text) |
| F8b | APRS beacon channel | analog beacons switch to a set memory channel (APRS > Beacon Chan), send, and switch back, with the stock report-channel reloads (issue #4) | emulator only | [APRS](user-guide/aprs.md#beacon-channel) |
| B2 | APRS RX | Decodes analog APRS and lists the last 8 stations | decoder on hardware; screen and menu rows emulator only | [APRS](user-guide/aprs.md) |
| F14 | RX Monitor | Keeps the APRS decoder running at standby | hardware | [APRS](user-guide/aprs.md) |
| F14b | RX Monitor Alert | A popup at standby with the callsign and start of the text of each received analog APRS packet | hardware | [APRS](user-guide/aprs.md) |
| F9 | Parrot | Records a DMR call and replays it as a DMR call | hardware, menu newer | [parrot and burst voice](user-guide/parrot-and-burst-voice.md) |
| F10 | Satellites | Pass prediction (SGP4) and Doppler-corrected RX/TX for FM satellites | hardware for prediction, tracking and retune on real passes; no contact made, transmit through a satellite not tested | [satellites](user-guide/satellites.md) |
| F11 | Squelch Mode | Noise-based analog squelch: Off (stock), Normal, Tight | emulator only | [radio settings](user-guide/radio-settings.md) |
| F11 | Squelch Survey | Live noise and signal readings for the squelch | hardware, menu newer | [radio settings](user-guide/radio-settings.md) |
| F12 | Scrambler | 16-bit voice scrambler in the DMR chip, with four key formats | hardware, menu newer | [privacy](user-guide/privacy.md) |
| F13 | Burst Voice | Sends a recorded DMR call as a fast data burst on an analog channel | hardware, menu newer | [parrot and burst voice](user-guide/parrot-and-burst-voice.md) |
| F15 | CW ID | Periodic Morse callsign on analog channels | hardware, menu newer | [VOX and CW ID](user-guide/vox-cwid.md) |
| F16 | Freq Unlock | Wider receive (23-520 MHz) and transmit (136-520 MHz) ranges behind a legal warning | emulator only | [radio settings](user-guide/radio-settings.md) |
| — | VOX | Makes the stock VOX rows work (stock 048 never implemented them) | hardware, menu newer | [VOX and CW ID](user-guide/vox-cwid.md) |
| — | VOX Monitor | Live mic level and VOX engine state, for picking a Gain Level | hardware | [VOX and CW ID](user-guide/vox-cwid.md) |
| — | Standby tags | Two-letter tags on the standby screen for features that are on | emulator only | [user guide index](user-guide/README.md#standby-tags) |
| — | About | Kraken name, base firmware and patch list | — | [receive tools](user-guide/receive-tools.md#about) |

## Removed

- **F0b Adaptive TX power** (an "Auto Power" entry in the TX Power menu). Built, but failed on hardware:
  the receive level is only sampled on analog and the transmit hook is DMR-only, so Auto on analog meant
  High. Removed from the image pending a redesign.

## What owners ask for

A read of a DM-32UV owners' group in November 2026 is summarised in
[feature-ideas.md section 8](feature-ideas.md#8-what-owners-ask-for-november-2026). The requests seen most
often, and where Kraken stands:

| Request | Kraken |
|---|---|
| Bigger scan lists, and scanning that works reliably | Not built. A zone scan without scan lists looks possible |
| See the caller's callsign or name | Last Heard (F1) shows the alias or contact name; most reports are CPS contact-import trouble |
| Quicker talkgroup handling: change it on the radio, show it at standby | Not built; both look possible |
| Analog APRS | Built: analog beacon (F8) and APRS RX |
| Wider frequency ranges | Built: Freq Unlock (F16) |
| Safe installs with a way back to stock | Web installer (above), and the update file's documented restore |
| Radio keying up by itself, PTT stuck on | Reported twice; not yet investigated. Safety item |

## Ideas not built

Features considered and not built, with the reasons (hardware limits, missing storage, scale), are in
[feature-ideas.md](feature-ideas.md). The host KISS TNC idea has its own scoping note:
[kiss-tnc-scoping.md](kiss-tnc-scoping.md).

## Reporting results

If you try a feature on your radio, please open a "Hardware test report" issue. Reports of features
marked **emulator only** or **menu newer** are the most useful.
