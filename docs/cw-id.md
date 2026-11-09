# CW ID: periodic Morse station ID on analog channels

Built 2026-11-05. Code: `patches/kraken_ui/krk_cwid.c` (blob 2), the sender in `krk_aprs_tx.c`
(`krk_fm_send`), the setting in `krk_callsign.c` (blob 3). Tests: `tests/test_cwid.py` (host),
`tests/otherfunc_e2e.py` `check_cwid` (booted, run standalone). The four Other Func splices are checked
against stock's bytes by the build (`expect_u32`).
**Works on a radio (2026-11-06, UHF analog simplex channel, Low power, dummy load, `PowerSave` = Save
1:4).** With the cable quiet (power save active), a 320 s SDR recording (`rtl_fm`, decoded with a
Goertzel at 800 Hz) holds five IDs, each keyed 4.4-4.5 s and each decoding as `N0NAME`, 63.3-64.5 s
apart at Interval 1 min; the radio's `CW.sent` read 5, `CW.why` "Sent". The first builds keyed but
sent no Morse, for two reasons that both come from stock power save (below, "The audio"): the
modem's FM clocks were off ("Modem stalled"), and with that fixed the radio was still asleep when
it keyed (silent carrier on about five IDs in six). The interval also ran about twice as fast while
the radio power-saved, and is now corrected against the RTC. Not tested: the row hidden on a
digital channel, the busy-channel and TX-in-use retries, intervals other than 1 min.

## What it does

Every *Interval* minutes, while the switch is on and the selected channel is analog, the radio keys up
and sends the stored callsign in Morse: 20 WPM, an 800 Hz tone, 480 ms of carrier first so receivers'
squelch is open. A six-character call takes about 4.4 s of Morse, about 5 s on the air.

It is a **fixed timer, by design**: it sends whether or not anyone has transmitted. (The
alternative, an "owed" ID that only follows the operator's own transmissions, is what
FCC 97.119 asks for and never transmits on its own; it was considered and not built.) The first ID comes one
interval after the timer arms (switch on, analog channel, callsign set); changing channel to digital
and back re-arms it.

It never keys:

| situation | what happens |
|---|---|
| channel busy (stock squelch open) | retries in 5 s |
| the operator's PTT or the VOX engine has the transmitter (`call_active` bit 3) | retries in 5 s |
| dual watch has the other side loaded | waits up to 1.5 s for the selected side, else retries in 5 s |
| a beacon send is in progress | retries in 5 s |
| a menu or one of our screens is up (screen-mode byte `0x320af` in 7..0x3f, 0x69, 0x77 - the VOX engine's `ui_in_menu`; `krk_screen_owner`) | waits, checks once a second (stock drops any menu to standby on a PTT press) |
| TX inhibited | skips this round |

The "TX in use" gate is new in the shared `claim()` and protects the APRS beacon too: before it, a
timed send during the operator's own transmission would have switched the modulator to the FM Send RAM
and then posted PTT release, cutting the operator off.

## The menu

`Settings > Radio Settings > Other Func > CW ID`, a stock-style sub-menu like VOX
(`docs/otherfunc-submenu.md`). The parent is Other Func item 11, a slot stock never lists (Man Down);
`krk_hook_of_gen` lists it in the place of the old VOX Delay row (item 4), right after VOX, and only
while the selected channel is analog.

```
Other Func > CW ID > CW ID SW   ON / OFF                         stock option level, radio marks
                   > Interval   1, 2, 3, 5, 10, 15, 20, 30 min   stock option level, radio marks
                   > Callsign   the callsign editor (shared with APRS > Callsign); Back returns here
```

OK on an option row commits with stock's "Setting is OK". Switching ON when no ID can be sent adds a
warning popup with the reason ("Set a callsign"; "Digital: no ID" cannot happen from this menu, since
the row is hidden on digital channels). Our option levels hold at most eight rows, hence eight
intervals; a stored value between two of them (the byte holds 1..60) shows the nearest.

It was first built (2026-11-05) as a hand-drawn page under Channel Set with a live countdown; it moved
here the same day because settings follow the stock UI. There is no countdown display now: `CW.sent`
and `CW.why` (the last result) can be read over the cable.

## Storage

Radio-wide, in the callsign record (block 4, offset 0xFE0, `docs/settings-field.md`): its `spare`
byte, which was always written 0, now holds bit 7 = on and bits 6-0 = minutes (0 or out of range
reads as 10). The record's checksum covers it, so a vendor CPS write (all FF) or a factory reset
(all 00) turns the ID off. `load_for_cfg` keeps both config bytes (APRS path, CW ID) when one is set
before a callsign exists; before this, setting the APRS path on a callsign-less record wiped the
other byte (found by `test_cwid.py`).

RAM: 20 bytes (`CW` in `krk_cwid.c`) at the end of the `.bss.afsk` claim, the only claim of blob 2
with room left (blob 2's `.bss` and the shared `.bss.feat` are full). The Morse units and the
generator state are a stack struct of the maintenance task for the length of one send.

## The audio

`krk_morse_units` turns the call (up to the SSID's `-`) into a bitmap of units: dit 1, dah 3, 1 off
between elements, 3 between characters; at most 129 units for six characters. `krk_cw_fill` is an
`krk_fm_send` fill: 256 samples per FM Send RAM half, the beacon's 64-entry sine table and level
(8000 of 32767), a 32-sample (4 ms) linear rise and fall per element, phase restarted at 0 whenever
the tone is fully off. `test_cwid.py` checks every character against an independent Morse table,
element timing at 60 ms per unit, 800 Hz, the level, and that no sample step exceeds the steady
tone's (no key clicks).

The send path is the beacon's: mic muted (GPIO 0x2E low), PTT press posted to `0x3a4f0`, wait for
TX up, FM clocks on, `AUDIO_CONTROL` bit 0 to the FM Send RAM, deviation coefficient 0x40, then
restore and release (`docs/fm-send-ram-tx.md`, `docs/afsk-tx.md`). The maintenance task is blocked
for the length of the ID, as it is for a beacon.

The ID is the first send made from standby, where stock power save runs, and it found two things
missing there (`docs/fm-send-ram-tx.md` section 6). The modem's FM clocks (`CLK_MGR` `0x1100002c`
bits 7:6) are off after any power-save sleep, so the FM Send RAM is never consumed. And stock wakes
a sleeping radio in its keypad code, not in its PTT code, so a posted PTT press keys it up half
asleep: carrier, no modulation. The sender now wakes the radio first and switches the FM clocks on.
The analog automatic beacon uses the same sender.

## The interval is real time

The interval is counted in OS ticks, and those run about twice as fast as real time while the radio
power-saves (measured 2.06x at Save 1:4): "1 min" came out at about 30 s. `real_time()` corrects
it: each time the scheduler sees the RTC's seconds register change (`rtc_register_read`
@0x0301c2d4, register 0, a locked I2C read stock makes from several tasks), it compares the ticks
counted since the previous change with the real seconds that passed and moves the timer's start by
the difference. Seconds are compared modulo 60, so setting the clock in between shifts one interval
by under a minute. An RTC that does not advance leaves the plain tick timer.

## Bench test (done 2026-11-06)

Dummy load fitted, an SDR on the same analog channel, the programming cable left quiet (cable
traffic holds the radio out of power save and hides both power-save faults): callsign set,
Interval 1 min, CW ID SW ON, back to standby, record five minutes. Result above.
