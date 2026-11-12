# F18 APRS Messages: receive inbox (phase 1) and the design for sending and acks (phases 2-3)

Status: phase 1 built and checked in QEMU and host tests only (2026-11-11), **not run on a radio**. Phases 2 and 3 are a
design; nothing of them is built. Issue #6. All addresses are for `DM32.00.01.048`.

## 1. Phase 1 as built: a receive-only inbox

**Menu > APRS > Messages** (analog channels) shows the APRS messages addressed to the saved APRS callsign-SSID. Nothing in
phase 1 transmits: no acks, no replies, no automatic sends.

### 1.1 Where the frames come from

Messages are decoded wherever the AFSK decoder already runs: while **APRS RX** is open, and at standby while **RX Monitor**
is on ([aprs-rx-monitor.md](aprs-rx-monitor.md)). Nothing new listens; with RX Monitor off and APRS RX closed, nothing is
received.

| Step | Where | What |
|---|---|---|
| 1 | `krk_afsk.c` `emit()` | after an FCS-valid frame is turned into its text line: `krk_aprs_msg_frame(S.line[0], frame, len)` (the frame without its FCS). The 60-character line is too short for a message, so the whole frame is passed. `krk_afsk.c` has a weak empty default, so the host tests and tools that build the decoder alone are unchanged |
| 2 | `krk_periodic.c` `krk_aprs_msg_frame` | the strong definition of the hand-off: calls `krk_msg_rx` |
| 3 | `krk_aprs_msg.c` `krk_msg_rx` | walks the AX.25 address field to the extension bit, skips control and PID, parses the info field, stores the message |

Kraken is one link (since 2026-11-11, [flash-allocation.md](flash-allocation.md)), so the call and the APRS row
(`&krk_aprs_msg_screen` in `menu_hosts.json`) name the symbols. Before that, the decoder's blob could not name the
messages blob's symbols and reached them through a hook entry and a descriptor at fixed offsets of that blob.

Contexts: the decoder runs in task `menu` (APRS RX screen, priority 31) or task `init` (RX Monitor, priority 30). They never
run together, and the monitor yields while any Kraken screen is open, including the Messages screen, so the inbox has one
writer at a time and never changes under the screen. Stack: the deepest monitor pass that completes a message frame
measures **448 bytes** in QEMU (`msg_checks.py`, canary-filled stack; 308 bytes before F18), far under task init's 2880-byte
stack with 124 bytes in use at its pend.

### 1.2 Parser rules (`msg_parse`)

| Rule | Detail |
|---|---|
| Shape | info `:ADDRESSEE:text`, ADDRESSEE 9 characters, space-padded; `info[0] == ':'` and `info[10] == ':'` |
| Third-party | info starting with `}` is `}SRC>DST,PATH:` plus the inner info. The sender is the inner `SRC`. Messages from APRS-IS reach RF this way through an igate |
| Addressee | trailing spaces dropped, compared case-insensitively with `krk_call_get()` (`CALL` or `CALL-n`). `CALL-0` does not match `CALL` (APRS writes no `-0`) |
| Number | the text after the last `{`, up to `}` (reply-ack form `{MM}AA` gives `MM`), at most 5 characters |
| Acks, rejects | a text starting `ack` or `rej` **without** a `{number}` is dropped (`ack12`, `rej12`, `ack3}AB`). A message with a number is kept even if it starts with "ack" |
| Bulletins and others | `BLN*`, `NWS-*` and every other addressee never equal a saved callsign (letters and at least one digit, 3-6 characters), so they fall out with the addressee rule |
| Empty text | dropped |
| Characters | non-printable bytes (also CR and LF that some senders add) are stored as `.` |
| Truncation | sender 9 characters, number 5, text **59** (an APRS text is at most 67: up to 8 characters can be lost) |
| No callsign saved | nothing is stored |

Duplicates: a message equal to one held in the inbox (same sender, number and text, compared as zero-filled words) and
received less than **30 minutes** after it is dropped. Senders retry until they get an ack, and phase 1 never sends one
(section 2.4); APRSdroid's default 7 tries with doubling intervals last about 30 minutes. A copy of a message that has
already dropped off the end of the 3-entry inbox comes back as new.

### 1.3 Inbox layout (claim `kraken_aprs_inbox`, `0x18007b00..0x18007c00`, 256 bytes)

Reached through `KRK_APRS_INBOX_LO` (krk_claims.h) as an absolute address, the way `mi_rng_pool` is used. No `.bss` of its
own. Patch SRAM is not zeroed at power-up: the inbox is valid only with its magic.

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | magic `0x4753534d` ("MSSG") |
| 0x04 | 4 | spare |
| 0x08 | 1 | n, messages held (0..3) |
| 0x09 | 1 | sel, the message the screen shows (reset to 0 when a message arrives) |
| 0x0a | 2 | padding |
| 0x0c | 4 | shown: `OS_TICKS / 100` of the last screen draw (only paces the redraw) |
| 0x10 | 3 x 80 | entries, newest first: `u32 sec`, `char from[10]`, `char no[6]`, `char text[60]`, each string zero-filled past its NUL |

`sec` is RTC time, not `OS_TICKS`: under stock power save the OS tick runs about 2x fast at standby
([fm-send-ram-tx.md](fm-send-ram-tx.md) section 6.3), which would make ages and the 30-minute window wrong. `msg_now()` reads
`rtc_register_read` (`0x0301c2d4`, the locked I2C read stock makes from several tasks; `krk_stock.h` `rtc_reg`) registers
0-4: seconds, minutes, hours (binary) and days since 1970 ([satellite-radio.md](satellite-radio.md) 5.2), as
`((days * 24 + h) * 60 + m) * 60 + s`. The RTC is local time; only differences are used. A read torn across a minute or hour
boundary is not retried (that one stamp is off by up to the step); an age that comes out negative (the RTC was set back, for
example by a GPS time sync) shows `?`. The RTC is read only for a message that is kept, and on screen redraws.

The radio's own callsign comes from `krk_call_get` (blob 3), a cached copy of the flash record. The cache is filled within
about a second of power-up by the standby-tag tick in the maintenance task (`krk_tags_tick` -> `krk_cwid_active` ->
`krk_cwid_cfg`), so the decoder path does not read the data chip.

### 1.4 The screen

A live screen (`.enter`, `.tick`; the runner owns Back and the menu-exit timer). One message per page:

| Row | Shows |
|---|---|
| 0 | `Messages 1/3` (`Messages` when there is nothing to show) |
| 1 | sender and age, `W1AW-9 4m` (`12s`, `4m`, `1h`; `?` if the RTC went back) |
| 2-5 | the text, 19 characters per row (59 at most) |
| 2 instead | `No messages`, or `Set callsign first` when no callsign is saved |

Up: newer, Down: older, both wrap. Ages redraw once a second. Opening the screen shows the newest message after one has
arrived, otherwise the page last viewed. The screen only reads RAM, the RTC and the callsign record; it has no transmit path.
The message number is stored (dedupe, and the ack of phase 3) but not shown.

### 1.5 Cost

| | Bytes |
|---|---|
| `kraken_ui4` code and rodata (`krk_aprs_msg.c`, hook entry, descriptor) | 1078 (`.text` 0x532a -> 0x5760) |
| `kraken_ui2` (the `emit()` call, `krk_aprs_msg_frame`) | 58, plus 4 for the new APRS table slot (`KRK_APRS_MAX` 6) |
| main blob | 1 (`aprs_analog_items[]`) |
| SRAM | the 256-byte `kraken_aprs_inbox` claim; no `.bss` |
| New hooks into stock code | none. New stock addresses: none (`rtc_reg` was already used by `krk_cwid.c`, `krk_satrf.c`) |

### 1.6 Tests

- `tests/test_aprs_msg.py` (host): layout, every parser rule, dedupe and its window on the RTC clock, the address walk,
  a synthesized AFSK message frame through the real decoder into the inbox, the screen's pages and keys.
- `tests/msg_checks.py` (QEMU, from `kraken_ui_test.py`): the APRS row and its label, the constant descriptor and hook entry,
  injection through blob 2's hand-off (ack, other SSID, bulletin, repeat, no callsign), a synthesized message frame fed half
  by half through the RX Monitor into the inbox (stack depth measured), the screen, and that no send function is reached.

### 1.7 Limits and what to check on a radio

Limits: receive only; RAM only (lost at power-off); 3 messages; text cut at 59 characters; only while APRS RX is open or RX
Monitor is on at standby on an analog channel; no alert when a message arrives; the receive side has the power-save FM
clock dependency of RX Monitor, still unfixed ([fm-send-ram-tx.md](fm-send-ram-tx.md) section 6.1): at standby with power
save on, the decoder may see no audio at all.

On the air (two radios, one sending messages from another APRS station or a second radio with a messaging client):

1. Radio B saves callsign N0CALL-5, RX Monitor on, standby on 144.390 (or a simplex test frequency). A sends a message to
   N0CALL-5: B's **APRS > Messages** shows it with the right sender, text and age.
2. The sender retries (no ack comes back): B still shows **one** message. Check the sender gives up after its retries.
3. A message to N0CALL-6, a bulletin, and an ack to N0CALL-5: none is listed.
4. A message relayed by an igate from APRS-IS (third-party): listed with the original sender.
5. Ages over several minutes at standby with stock power save on, against a watch: they should follow real time (RTC),
   not run 2x fast.
6. With the Messages screen open, and after opening it: no transmission (watch the TX LED / a second receiver).
7. A 67-character message: the first 59 characters are shown.

## 2. Phase 2 design: compose and send a message (not built)

### 2.1 What the owner does

Two entry points, both owner actions, never automatic:

- **Reply**: on a message page of the Messages screen, `OK` opens the editor with the addressee set to the sender.
- **New**: a `New message` action (a one-shot row, or `#` on the Messages screen) asks for the addressee first
  (callsign-SSID editor, the existing `krk_callsign.c` one in an "addressee" mode), then the text.

Send: `OK` in the editor shows the stock-style arm popup `krk_tx_arm("Send message")` (`OK again to send` / `TRANSMITS`,
3 seconds), as Upload Beacon does. Any other key or the timeout cancels with nothing sent. The result is a popup: `Sent`,
or the reason (`Channel busy`, `TX inhibited`, `Set callsign first`, `Analog channel only`, `Dual watch: retry`).

### 2.2 Editor

Text up to 67 characters (APRS limit), ASCII, from the keypad.

- Preferred: reuse stock's DMR text-message editor (the DM-32UV has DMR SMS compose). It has multi-tap, a cursor and
  delete in the stock UI, matching the "settings and editors use the stock UI" convention. Needs reverse engineering: its
  entry, its buffer and how to get control back on OK/Back without stock sending a DMR SMS. Unknown cost.
- Fallback: extend the callsign editor's multi-tap (`krk_callsign.c`: `2`-`9` letters as on a phone, `*` delete) with
  lower case, space (`0`), and punctuation (`1`), on a 4-row text field. About 500-700 bytes of code; the 67-character buffer
  can live in the shared big buffer while the editor is open (no permanent RAM).

### 2.3 Sending

The frame: source = saved callsign-SSID, destination `APZKRK`, path = the Upload ID path, info
`:ADDRESSEE:text{NN}` with the addressee padded to 9 characters and `NN` a message number (1-5 characters; a RAM counter
starting at a random value from the `mi_rng` pool, so numbers do not repeat after a power cycle within the receivers'
dedupe windows). Built with `krk_ax25_frame` (`krk_ax25.c`) and sent by the beacon's path: a sibling of `krk_aprs_tx_send`
in `krk_aprs_tx.c` that takes a prepared info field, so it gets every gate in `claim()` unchanged: analog only, one send at
a time (`tx_busy`), stock power-save wake, `krk_mon_yield()` before keying, TX inhibit, the user's own PTT/VOX (`TX in use`),
dual watch on the selected side, **busy channel** (`stock_rx_active`). The send is about 1 second at 1200 baud.

### 2.4 Retries (sender side)

A message without an ack is resent, as every APRS client does: 4 tries in all at 30 s, 60 s, 120 s (about 3.5 minutes),
stopped by the ack (phase 3) or by the owner (`Back` on an "Awaiting ack" line, or sending another message). Retries are
automatic transmissions, so they follow section 3's rules: they run only because the owner just pressed Send twice, they
show the standby tag while pending, they pass the same gates (busy, TX inhibit, menu open = postponed), and they stop at
power-off (RAM only). Without phase 3 the radio cannot see the ack, so phase 2 alone sends **one** try and shows `Sent (no
ack check)`; the retry engine comes with phase 3.

What phase 1's missing acks mean for other stations: a station that sends us a message gets no ack, so it retries until it
gives up (APRSdroid: up to 7 tries over about 30 minutes; many radios 3-5 tries) and then shows the message as failed or
"no ack". The message was received; the sender just cannot know. The user guide says so.

## 3. Phase 3 design: automatic acks (not built)

### 3.1 Why this is an automatic transmitter

An ack is sent by the radio when a message arrives, with no key press at that moment. Under CLAUDE.md's safety rules that is
an automatic transmitter like the beacon, CW ID and VOX:

- **Off after install and after every power-on**: a RAM-only setting, `Auto Ack` `Off`/`On` under **APRS** (stock option
  level through `struct krk_opt`), with a warning popup on enabling (`Acks TRANSMIT` / `when a message arrives`), like Freq
  Unlock's.
- **Standby tag**: a red `AK` tag ([standby-tags.md](standby-tags.md)) while Auto Ack is on and the channel is analog, so
  the owner can see the radio may key by itself.
- **Requires** a saved callsign, an analog channel, and RX Monitor (or the APRS RX screen) actually decoding.

### 3.2 When an ack is sent

Only for a message that phase 1 stored or recognised as a repeat, addressed exactly to us, **with** a message number. Never
for a message without a number (APRS: no ack expected), never for bulletins, never for an ack or reject. A third-party
message (from APRS-IS through an igate) is acked the same way over RF; the igate passes the ack back. The ack is `:SENDER   :ackNN` (sender padded to 9,
reply-ack `ackNN}` not used).

Repeats: a retried message is acked again (the sender retries because our ack was lost), but at most once per 30 seconds per
sender + number, and at most 3 acks per message in all.

### 3.3 Gates and rate limits

The ack is queued in RAM (one slot per sender + number, 3 slots) and sent by the maintenance-task tick, not from the
decoder (the decoder runs in task init and in the APRS RX screen; a transmission must not start there). Each try goes
through `claim()` like the beacon: TX inhibit, busy channel (the message sender often has the channel for a moment, so the
first try is after a random 1-3 s holdoff), the user's own PTT/VOX, dual watch on the selected side, the stock menu gate
(`krk_ui_in_menu()` or a Kraken screen open: postponed, as CW ID does), not while scanning, not on a channel that forbids TX
(`[0x3e1e4] & 8`). A queued ack older than 60 s is dropped (the sender has retried by then and will get the next one).

Global limit: at most 1 ack per 10 s and 10 per 10 minutes; past that, Auto Ack switches itself off and says so on the next
screen visit. This bounds a loop with another automatic station (the stock DMR position request is known to ping-pong
between two radios; an ack must never answer an ack, which the parser rule already ensures).

### 3.4 Interaction with the beacon and the busy check

- One sender at a time: `tx_busy` already serialises beacon, CW ID, Burst Voice and messages. A beacon due while an ack is
  pending goes first or second, whichever claims first; the other sees `Busy` and retries shortly (the beacon's 5 s retry
  path).
- The busy check is the stock squelch state (`stock_rx_active`), as for the beacon. On a busy APRS frequency the ack can be
  delayed past the sender's retry; that is acceptable.
- RX Monitor yields before every send (`krk_mon_yield()` in `claim()`) and resumes after.
- The analog beacon's interval timer is not changed by acks.

### 3.5 Memory and flash (estimate)

Ack queue 3 x 16 bytes in `kraken_aprs_state`; sender retry state for phase 2 about 100 bytes plus the 67-byte text. Code:
editor 500-700 bytes (fallback), send/queue/retry 400-600 bytes. Neither fits the blob space free today; it needs a flash
review first ([flash-allocation.md](flash-allocation.md)).
