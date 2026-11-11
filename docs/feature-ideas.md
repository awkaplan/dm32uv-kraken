# Feature ideas and gap analysis

What pricier radios and other firmware projects do that the stock DM-32UV does not, and whether each
could be added as a patch to stock `DM32.00.01.048` (hooks plus new code in erased program flash), as
opposed to a full replacement firmware.

This started as the research that chose Kraken's features (October 2026). Most Tier 1 and Tier 2 items
have since been built; [feature-status.md](feature-status.md) has their status. This page keeps the
reasoning, the ideas that were not built, and why.

External claims were checked against the sources in [Sources](#sources). Claims marked "general
knowledge" were not checked against a source.

## 1. Summary

- Stock already covers most of the usual checklist (section 2). The gaps were in four areas:
  diagnostics (RSSI in dBm, link quality, a band scope), a heard-station view, analog APRS and GPS
  conveniences, and the random numbers behind stock encryption.
- Stock does implement ARC4 and AES in software, but its message indicator (MI) came from an unseeded
  generator. Kraken fixes that (F0a).
- Analog APRS receive was first thought blocked: it needs received audio as CPU samples. It was later
  solved on hardware. A GPIO switch routes the FD6818's audio output into the HR_C7000's FM receive
  buffer, so the CPU gets 8 kHz PCM. Notes: [rx-audio-fm-mode.md](rx-audio-fm-mode.md).
- There is almost nowhere to save new settings (section 6). Most Kraken settings are therefore RAM-only.
- What owners themselves ask for (section 8, November 2026) is mostly about scanning, seeing who is
  talking, and quicker talkgroup and channel handling, plus many complaints about the vendor CPS. Few
  of those requests overlap with what Kraken has built so far.

## 2. What stock already has

From the vendor user manual. These are not gaps:

| Feature | Notes |
|---|---|
| Roaming (strongest-repeater search) | |
| Dual watch / dual standby, Sub PTT | |
| Digital monitor; ID matching can be turned off (promiscuous) | |
| Talker alias TX/RX | |
| Analog scrambler | The FD6818 has a hardware SCRAMBLE mode |
| DTMF / 2-tone / 5-tone / BDC1200, with PTT-ID decode | |
| Man-down, lone worker, emergency alarm (6 types) | |
| NOAA weather alert | |
| Digital recording and playback | |
| DMR APRS (GPS or fixed beacon, timed) | Digital mode only |
| TX timeout with advance warning | |
| Frequency search (One Key Sweep) | |
| Call alert, radio check, remote monitor, radio disable/enable | |
| Tone burst, squelch-tail elimination, power save | |
| VOX | Listed and shown in the menu, but never implemented in 048. Kraken makes it work |
| Set the clock from GPS | Stock does this from its NMEA parser |

Hardware on the board that matters below: an ATGM336H GPS+BeiDou module on a UART at 9600 baud, with no
PPS pin wired to the CPU.

## 3. What "reasonable as a patch" means

A feature counts as reasonable when it needs only pieces already shown to work:

| Capability | Where it is documented |
|---|---|
| New top-level menu category and screens | [menu-hosts.md](menu-hosts.md); dm32uv-re `docs/menu-system.md` |
| Rows in stock menus and stock option levels | [stock-option-level.md](stock-option-level.md), [settings-submenu.md](settings-submenu.md), [chanset-submenu.md](chanset-submenu.md) |
| Keypad read from patch code | dm32uv-re `docs/keypad-input.md` |
| Stock display and RTOS calls from C | dm32uv-re `docs/patch-cookbook.md` |
| Hook / trampoline splice | dm32uv-re patch tools |
| Free program flash for new code | [flash-allocation.md](flash-allocation.md) |
| RF retune (FD6818 PLL) | [retune-for-scope.md](retune-for-scope.md) |
| RSSI read | Stock's meter reads FD6818 `REG_67H` at 0.5 dB/step |
| TX power actuator | The DAC drive byte, not FD6818 `REG_19H`; dm32uv-re `docs/tx-power-control.md` |

dm32uv-re is at [github.com/awkaplan/dm32uv-re](https://github.com/awkaplan/dm32uv-re).

Known limits that rule things out:

- CPU speed: floating-point codec2 runs 26-32x too slow for real time.
- No DMA, and the voice codec gives the CPU no sample stream.
- No free space on the data chip (section 6).
- No GPS PPS signal.

## 4. Tier 1: mostly UI work on proven pieces (all built)

| Idea | Seen on | Result |
|---|---|---|
| Last-heard list | OpenGD77 (Contacts > Last Heard) [S3]; commercial DMR radios | Built (F1). RAM ring of 16 entries |
| Numeric RSSI / S-meter | OpenGD77; service screens (general knowledge) | Built (F2). Uncalibrated dBm: no signal generator reference yet |
| DMR bit error rate | Commercial radios (general knowledge) | Built as link quality (F3). The HR_C7000 has no voice BER counter; Kraken reads per-burst sync errors and EVM instead. [dmr-ber.md](dmr-ber.md) |
| Band scope | Yaesu FT5DR [S5]; UV-K5 custom firmware (fagci spectrum analyzer) [S6] | Built (F5). One receiver, so sweeping blanks normal reception. PLL lock about 1.3 ms, RSSI updates about 8.7 ms after lock |
| More TX power steps | Commercial radios with per-channel power tables | Built as a radio-wide trim (F6). Relative only: output power not measured with a meter |
| Maidenhead grid, distance and bearing to DMR APRS stations | APRS radios generally | Grid built (F4). Stock already shows distance and bearing but mirrors them west of Greenwich; Kraken fixes it (F7). [aprs-stock-gaps.md](aprs-stock-gaps.md) |
| 16-bit basic privacy | Motorola Basic Privacy, Hytera equivalents (general knowledge) | Built (F12) on the HR_C7000's `SCRAMBLE_REG`. The UI says plainly that it is weak. [basic-privacy.md](basic-privacy.md) |

## 5. Tier 2: real engineering

| Idea | Seen on | Result |
|---|---|---|
| Analog APRS beacon (1200 baud AFSK) | AnyTone 878UV II [S1], Kenwood TH-D75 [S4] | Built (F8) through the HR_C7000 FM Send RAM. [afsk-tx.md](afsk-tx.md), [fm-send-ram-tx.md](fm-send-ram-tx.md) |
| Analog APRS receive | AnyTone 878UV II [S1], TH-D75 [S4] | Built (APRS RX, F14 RX Monitor) |
| Satellite mode with Doppler | OpenGD77 (satellite screen, pass prediction) [S2] | Built (F10): SGP4 in soft-float, TLEs built into flash. [satellite-mode.md](satellite-mode.md) |
| DMR parrot | Store-and-forward on various radios | Built (F9). [parrot-recording.md](parrot-recording.md), [parrot-tx.md](parrot-tx.md) |
| Noise squelch (tight/normal) | Commercial (general knowledge) | Built (F11) by changing the FD6818 noise threshold pair. [noise-squelch.md](noise-squelch.md) |
| Compressed burst voice | Not a mainstream feature | Built (F13). [burst-voice.md](burst-voice.md) |
| Adaptive TX power | Commercial (general knowledge) | Built and removed (F0b): the receive level is only sampled on analog and the TX hook is DMR-only. Needs a redesign |
| Real ARC4 / AES voice encryption | Motorola, Hytera (general knowledge) | Stock already has ARC4, AES-128 and AES-256. Only the MI was broken; fixed (F0a). Compatibility with other vendors is out of reach |

## 6. Where new settings can be saved

Any feature with persistent settings hits the storage problem:

- **The data chip has no free space.** Only about 24 KiB is blank on the units examined, and the large
  gap is the DMR contact database on radios with no contacts loaded. It is never assumed free.
- **There is no runtime program-flash write path**, and Kraken does not add one.
- **What Kraken does:** keep most state in RAM (lost at power-off; deliberate for anything that affects
  transmitting), and store the few persistent settings in decoded spare bytes of stock Radio Settings
  block 0x04 (callsign, Freq Unlock, Burst Voice, CW ID), written only through the stock block write when
  you press `OK`. A CPS write or factory reset clears them. [settings-field.md](settings-field.md).
- Read-only data (orbit elements) is built into the program flash at build time.

## 7. Not built

### Blocked by hardware

| Idea | Reason |
|---|---|
| M17 / codec2 digital voice | Floating-point codec2 measured 26-32x too slow; needs a fixed-point codec2 |
| Trunking (Capacity Plus, Tier III) | Capacity Plus is proprietary; Tier III is far beyond patch scale |
| Full-duplex cross-band repeat | One RF chip; it cannot receive and transmit at once |
| Bluetooth | No Bluetooth hardware on the board (menu strings exist but are unwired) |
| SSB | The FD6818 does FM (and AM receive), not SSB |
| GPS-PPS-disciplined timing | The GPS module's PPS pin is not wired to the CPU |

### Possible, not started

| Idea | Notes |
|---|---|
| Host KISS TNC over the programming cable | The AFSK/AX.25 encoder and decoder already work in both directions; what is missing is a framing layer on the serial link. This would turn the radio into a TNC for a PC and enable iGate use. Scoped in [kiss-tnc-scoping.md](kiss-tnc-scoping.md). The most reachable gap |
| APRS messaging on FM | Receiving is built (F18, a receive-only inbox, [aprs-messages.md](aprs-messages.md)). Sending, replies and automatic acks are not; their design is in the same note (issue #6) |
| Analog-mode recording | Now possible in principle with the FM receive capture path; storage is the problem |
| RX noise reduction, automatic volume | Same capture path, plus the CPU budget |
| Roger beep on DMR, hot-mic emergency | Small, not requested |
| Persistent settings for more features | Limited by section 6 |

### Out of reach for a patch

These are features of full replacement firmware such as RadioFoundry. They need the whole stack:

| Idea | Reason |
|---|---|
| Hotspot (MMDVM modem) mode, split-duplex repeater | Needs raw DMR burst access plus a host link; close to a firmware rewrite |
| IP over DMR, network services | Same |
| ARS, LRRP location reporting | Needs a DMR data stack Kraken does not own |
| Wireless zone and channel sharing | Same |
| Codeplug editor, contact import | Kraken uses the vendor CPS. (A web installer was built after all: [web-flasher.md](web-flasher.md)) |
| One codebase across many radios | Kraken is single-target by design: it patches one stock firmware |

Things Kraken has that RadioFoundry's README did not claim (November 2026): band scope, satellite mode,
DMR parrot, numeric S-meter, DMR link quality, Maidenhead grid, burst voice, noise squelch modes, the MI
fix, and keeping the stock firmware intact.

## 8. What owners ask for (November 2026)

From a read of a DM-32UV owners' group [S8] on 2026-11-09: 17 searches of the group ("feature", "wish",
"bug", "scan", "update", "firmware", "APRS", "GPS", "contacts", "talker alias", "zone", "audio",
"OpenGD77", "battery", "message", "airband", "VOX"), about 120 posts read. It is a sample of posts, not a
survey: the counts below are posts seen, a rough guide to how often a topic comes up. Most posts are
setup questions. These are the ones that ask for a feature, report a firmware bug, or complain about a
limit. Nothing here has been checked on a radio yet unless the Kraken column says so.

### Firmware requests and bugs

| Request or report | Posts | Kraken |
|---|---|---|
| **More than 16 channels per scan list** (stock: 32 lists of 16) | 5+ | The limit is in the codeplug layout that CPS writes, so a bigger list needs CPS changes too. Possible as a patch instead: a **zone scan** that scans every channel of the current zone with no scan list, as OpenGD77 does. Not investigated |
| **Scan does not work** reliably: lists show as empty or garbled, "no list to scan", the radio freezes while scanning | 5+ | Stock bug, cause unknown. One owner reports that writing a codeplug turns the scan list and scan switch off. Worth a look in QEMU first |
| **Stop on a busy channel and stay there** during a scan | 1 | Possible (scan hold key). Not investigated |
| **Keys and side keys ignored while a signal is received**, so nuisance delete cannot be used during a scan | 1 | Possible in the keypad path. Not investigated |
| **Caller's callsign or name not shown**, only the DMR ID, even with contacts loaded or talker alias on | 4 | Stock supports contact names and talker alias. Most reports look like CPS import trouble (CPS keeps only the first 3000 contacts unless the CSV is imported again). Kraken's Last Heard (F1) shows the alias or contact name |
| **Show the current talkgroup at standby**, not only during a call (as OpenGD77 does) | 1 | Possible: Kraken already draws on the standby screen (standby tags). Not investigated |
| **Change a channel's talkgroup quickly** without editing the channel; stock's manual talkgroup change is reported not to work | 2 | Possible as a Kraken screen (pick a TX contact or type a TG number for the current channel). Not investigated |
| **Up/Down keys to change zones** instead of channels (they duplicate the knob) | 1 | Possible as a key setting. Not investigated |
| **TX-only CTCSS cannot be set from the radio's menu**: it reports "Setting is OK" and goes back to None; only RX+TX works | 1 | Stock menu bug. A fix looks possible in the channel edit menu. Not investigated |
| **Remove a channel from a scan list on the radio** (there is Add Channel but no Delete) | 1 | Possible as a row in the stock channel or scan list menus. Not investigated |
| **Dual watch: only the main channel is heard**; when the other side has a signal, switching to it stops the audio | 1 | Stock behaviour; [dual-watch.md](dual-watch.md) has how stock picks the active side. Not investigated |
| **Radio keys up by itself, or PTT stays on until power-off** | 2 | **Safety.** Cause unknown (hardware, a stuck key, or firmware). Stock's TX timeout limits it only if it is set. Worth checking that no Kraken path can make it worse, and whether a firmware watchdog on the PTT input would help |
| **Low mic level on DMR** even at the highest Digi MIC Gain | 1 | Possibly a wider gain range through the HR_C7000's audio registers. Not investigated |
| **Analog (FM) APRS** wanted; stock APRS is DMR only | 2 | Built: analog beacon (F8) and APRS RX |
| **DMR APRS sends only on PTT**, not on its own; position not reaching APRS over BrandMeister | 2 | Stock has a timed DMR APRS beacon; likely configuration. A user guide note may help more than code |
| **GPS position sharing with the UV-32** (the analog sibling radio) | 2 | The UV-32's analog location-sharing format is unknown. Not investigated |
| **NOAA weather channels show RX but give no audio** | 1 | Stock bug report, single post |
| **Clock stuck** although GPS has a fix | 1 | Stock sets the clock from GPS; single report |
| Menu language **turned to Chinese** after loading a shared codeplug | 2 | The language is part of the codeplug. Not a firmware request |
| **Wider frequency ranges** (a "wideband" modified firmware already circulates) | 2 | Built: Freq Unlock (F16) |
| **Safe firmware updates and a way back**: radios bricked by an interrupted update, confusion over 047, 048, L01 and CPS versions, and owners asking whether they can go back from OpenGD77 | 5+ | The web installer backs the radio up first and can put stock back ([web-flasher.md](web-flasher.md)). Its install has not yet been tested on a radio |

### Vendor CPS problems (outside Kraken)

Many posts are about the programming software rather than the radio. Kraken uses the vendor CPS and
does not change it, but these explain many "the radio does not work" posts:

- Talkaround is not programmed by CPS; owners must set Forbid Talkaround on the radio to use repeaters.
- Inserting or moving channels mixes up zone and channel indexes (still reported in CPS 1.60).
- The contacts CSV loads only its first 3000 contacts unless imported again each time CPS is opened.
- No "save changes?" prompt when closing a codeplug.
- Excel and CSV import of scan lists fails silently.
- A mislabelled "anloag" field.

### What else the posts say

- An OpenGD77 port for the DM-32UV appeared in July 2026 (test builds, with its own firmware reader and
  CPS). Owners asking about it most often want to know whether they can go back to stock. Kraken's
  difference is that it keeps the stock firmware, menus and codeplug.
- Several owners use the radio as a receive-only DMR scanner (for example, public-service channels that
  moved to DMR). For them, scanning is the most important feature.
- At least one owner reports an original Baofeng programming cable with a CH340 chip, not the Prolific
  PL2303 tested so far. That matters for the web installer's fast line rates
  ([web-flasher.md](web-flasher.md)).

## Sources

External sources, accessed October and November 2026 (snippets, not archived):

| ID | Source | Supports |
|---|---|---|
| S1 | WiMo product pages for the AnyTone AT-D878UVII Plus: https://www.wimo.com/en/at-d878uv2b2 | Analog (FM) APRS receive; APRS on FM or DMR. Retailer copy |
| S2 | OpenGD77 User Guide (community copy): https://www.scribd.com/document/684685671/OpenGD77-User-Guide | Satellite screen with pass prediction |
| S3 | OpenGD77 write-up: https://swenton.com/ham-radio/ham-radio-articles/opengd77 | Hotspot mode; Last Heard under Contacts. Secondary source |
| S4 | Kenwood TH-D75A product page: https://www.kenwood.com/usa/com/amateur/th-d75a/ | KISS TNC, digipeater, iGate, APRS station list |
| S5 | GigaParts FT5DR listing: https://www.gigaparts.com/yaesu-ft5dr-c4fm-fm-dual-band-transceiver.html | Band scope, up to 79 channels. Retailer copy |
| S6 | egzumer/uv-k5-firmware-custom: https://github.com/egzumer/uv-k5-firmware-custom | Open UV-K5 firmware with the fagci spectrum analyzer |
| S7 | RadioFoundry: https://github.com/RadioFoundry/RadioFoundry | Full replacement firmware; feature list used in section 7 |
| S8 | A DM-32 owners' group on Facebook, read 2026-11-09 | Owner requests and bug reports in section 8. Posts are summarised, not quoted, and no member is named |
