# Developer docs

Notes for people who build, change or study Kraken. Radio owners want the [user guide](user-guide/README.md).

Addresses are of stock `DM32.00.01.048` unless a note says otherwise. Many notes start with a status line
(static research, QEMU-verified, or tested on a radio); the code may have moved on since. The user guide
and [feature-status.md](feature-status.md) have the current feature status.

The stock firmware corpus, patch tools and Docker build image these notes refer to are in
[dm32uv-re](https://github.com/awkaplan/dm32uv-re).

## Project

| Doc | What it covers |
|---|---|
| [feature-status.md](feature-status.md) | Every feature, what it does and how tested it is |
| [feature-ideas.md](feature-ideas.md) | Gap analysis against other radios and firmware; ideas not built and why |

## Building and flashing

| Doc | What it covers |
|---|---|
| [building.md](building.md) | Set up dm32uv-re and the Docker image, run `tools/build.sh`, what the outputs are |
| [flashing.md](flashing.md) | The update file format and the radio's updater; sector-by-sector flashing for test builds |
| [web-flasher.md](web-flasher.md) | The web installer: backs up, patches in the browser and flashes over Web Serial, so no stock firmware is published |
| [user-guide/install.md](user-guide/install.md) | Installing and going back to stock, step by step for radio owners |

## Architecture: how Kraken fits into stock

| Doc | What it covers |
|---|---|
| [flash-allocation.md](flash-allocation.md) | Free program flash and SRAM, and which Kraken blob lives where |
| [menu-hosts.md](menu-hosts.md) | How a feature gets a menu row: `struct krk_screen`, hosts, the generated wiring |
| [stock-option-level.md](stock-option-level.md) | Stock option lists, numeric entry and popups for Kraken settings, with a cookbook |
| [settings-submenu.md](settings-submenu.md) | Adding a row to stock Settings > Radio Settings |
| [settings-field.md](settings-field.md) | Saving a new field inside stock Radio Settings block 0x04 |
| [chanset-submenu.md](chanset-submenu.md) | Adding rows to stock Settings > Channel Set |
| [otherfunc-submenu.md](otherfunc-submenu.md) | The Other Func sub-menus for VOX and CW ID. The VOX engine itself is in `patches/kraken_ui/krk_vox.c` |
| [nested-submenu.md](nested-submenu.md) | Nesting a sub-menu under a stock Channel Set row, and why the first attempt failed |
| [aprs-submenu.md](aprs-submenu.md) | Adding rows to the stock APRS sub-list |
| [calllog-submenu.md](calllog-submenu.md) | The Last Heard row in the stock Call Log sub-list |
| [gnss-submenu.md](gnss-submenu.md) | The GPS Grid row in the stock GNSS sub-list |
| [sublist-cursor.md](sublist-cursor.md) | The RAM byte that holds the selected sub-list row |
| [menu-exit-timer.md](menu-exit-timer.md) | The stock menu timeout, and how Kraken screens keep it from firing |
| [standby-tags.md](standby-tags.md) | The two-letter tags on the standby screen |
| [dual-watch.md](dual-watch.md) | Which channel is "the selected channel" while dual watch runs |

## Per-feature notes

| Doc | Feature |
|---|---|
| [afsk-tx.md](afsk-tx.md) | AFSK (Bell 202) transmit: stock tone generator, timing, risks |
| [fm-send-ram-tx.md](fm-send-ram-tx.md) | Software AFSK through the HR_C7000 FM Send RAM (the path the beacon uses) |
| [aprs-stock-gaps.md](aprs-stock-gaps.md) | Stock's unused analog APRS mode and the bearing hemisphere bug (F7) |
| [aprs-positions.md](aprs-positions.md) | How stock stores received APRS/GPS positions |
| [aprs-rx-monitor.md](aprs-rx-monitor.md) | F14: APRS decoding at standby |
| [kiss-tnc-scoping.md](kiss-tnc-scoping.md) | Scoping a host KISS TNC (not built) |
| [last-heard-hooks.md](last-heard-hooks.md) | F1: hook points for the last-heard list |
| [dmr-ber.md](dmr-ber.md) | F3: DMR link quality from the modem's per-burst registers |
| [retune-for-scope.md](retune-for-scope.md) | F5: retune, lock, RSSI read and restore for the band scope |
| [parrot-recording.md](parrot-recording.md) | F9: what stock recording and playback really do |
| [parrot-tx.md](parrot-tx.md) | F9: transmit insertion point, keying and capture for the parrot |
| [satellite-mode.md](satellite-mode.md) | F10: orbit maths (SGP4, pass search, Doppler) |
| [satellite-radio.md](satellite-radio.md) | F10: Doppler-corrected RX and TX on an analog channel |
| [noise-squelch.md](noise-squelch.md) | F11: FD6818 noise squelch registers and the patch |
| [basic-privacy.md](basic-privacy.md) | F12: the HR_C7000 16-bit scrambler |
| [burst-voice.md](burst-voice.md) | F13: compressed burst voice design |
| [cw-id.md](cw-id.md) | F15: periodic Morse ID |
| [freq-unlock.md](freq-unlock.md) | F16: wider RX and TX ranges |

## Hardware and reverse-engineering research

| Doc | What it covers |
|---|---|
| [rx-audio-fm-mode.md](rx-audio-fm-mode.md) | Analog receive audio as PCM through the HR_C7000 FM mode (the APRS RX path) |
| [rx-audio-fm-gain.md](rx-audio-fm-gain.md) | Why the FM receive engine first gave near-silent output |
| [rx-audio-board-ics.md](rx-audio-board-ics.md) | Which board ICs the receive audio passes through (from photos) |
| [rx-audio-sources-forums.md](rx-audio-sources-forums.md) | Receive audio path facts collected from forums and other projects |
| [analog-vs-digital-rx-config.md](analog-vs-digital-rx-config.md) | How stock configures the receiver for analog vs digital |
| [fd6818-disc-mode-rx.md](fd6818-disc-mode-rx.md) | FD6818 raw discriminator output: datasheet options and risks |
| [hr_c7000-af-input-notes.md](hr_c7000-af-input-notes.md) | HR_C7000 datasheet notes on the audio input and ADC registers |
| [at1846s-opengd77-analogy.md](at1846s-opengd77-analogy.md) | What transfers from OpenGD77's AT1846S work to the FD6818 + HR_C7000 |

## Images

`img/` holds QEMU screenshots used by [stock-option-level.md](stock-option-level.md).
