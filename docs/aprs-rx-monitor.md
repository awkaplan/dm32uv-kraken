# F14 APRS RX Monitor: analog APRS receive at standby (2026-11-02)

Status: built, QEMU-verified (47 in-target checks in `tests/mon_checks.py` plus a booted run), **not run on a radio**.
Default OFF, RAM only (policy P4). Switch: **APRS > RX Monitor > Off / On / Alert** (Alert: section 5), a genuine stock option level (radio marks, `docs/stock-option-level.md`; since 2026-11-02 the `#` key of the APRS RX stations screen is gone). The stations screen shows `RX Monitor on h123` / `RX Monitor off` on row 9 as a status line (no key hint).

What it does: with the Monitor on, the AFSK/AX.25 decoder of the APRS RX screen (`krk_aprsrx.c`) keeps running while no screen
is open, on analog channels, and fills the same 8-station ring. Opening the screen shows what arrived (title "APRS MON n
stations"). With `Alert` a popup at standby shows each packet (F14b, section 5). No sound, **no transmit path of any kind**.

## 1. Feasibility study (the gates)

### G1 Execution vehicle: PASS, a hook at the tail of the stock background loop

The decoder needs one call per 32 ms FM Receive RAM half (a half lives 32 ms; `rx_poll` in Burst Voice learned that a 20-30 ms
hole already loses halves).

(a) The maintenance-task hook (`krk_hook_maint_tick`, `krk_periodic.c`) FAILS. `ui_periodic_maintenance_task_loop` @0x0305476c
pends flag 0x20000 of group 0x0003fd90 (`bsr 0x300c464` at 0x03054796) and that flag is posted by `ui_tick_prescaler_post`
@0x030546dc once per 100 calls (`cmphs 0x63`, 0x030546ea): one pass per second. Measured in a booted QEMU run: 10 passes per 1000
OS ticks. A 1 Hz consumer sees one half in 31.

(c) The loop that calls the prescaler does not. `main_ui_background_task_loop` @0x03013a3c ("task init", entry 0x03068688, the
task that also runs the stock keypad poll) pends flag 1 of the same group at the top (0x03013a62) and ends every pass with
`bsr 0x030546dc` at **0x03013f48** followed by `br32 0x03013a56` back to the pend; all branches inside the body (`bnez`s to
0x03013f40) converge there. Measured in a booted QEMU run (`POKE` reads of `krk_maint_ticks` and OS_TICKS at 20 s and 30 s): 5 and 15 maintenance
passes at OS_TICKS 1013 and 2013, i.e. 10 per 1000 ticks and, with the 100-count prescaler, **one loop
pass per 10 ms OS tick** (`docs/menu-exit-timer.md` reached the same 1 s per count). 3 passes per half. Hardware support: the
stock seconds-based features that hang off the same prescaler (beacon interval, Menu Exit Time) run on time on the radios.
Splice `bg_tick_hook` replaces that `bsr` with `bsr krk_hook_bg_tick`, which makes the displaced call first, unchanged, then
calls `krk_mon_tick()`; the identical idiom as `maint_tick_hook` (void(void) callee, r0-r3 reloaded by the loop head, callee
saved registers preserved by C). Nothing else in this repository or in dm32uv-re's patches touches 0x03013f48.

(b) A new OS task is not needed, and the premise "no patch has created a task yet" is wrong: a separate frequency-hopping experiment (not part of this repository)
has created one on hardware since 2026-10-20. The exact call, from its working source:
`OSTaskCreate(tcb, "fh", entry, 0, prio, stk_base, 0, stk_words, 0, 0, 0, opt = 2 /* STK_CLR */, &err)` with a 256-byte TCB
(64 words), 0x200 words first and 0x400 words after a measured overflow of the 2 KB stack, `err` a 32-bit store; it started at
priority 30 and moved to 9 (free priorities: 9, 15, 16, 30, 39-57). Rejected for this feature anyway, on memory (G2): every byte of the
13 KB SRAM window is claimed, and the only multi-KB region is the F9/F13 shared buffer that Parrot and Burst Voice own while
they work, which a sleeping task's stack cannot live in. The loop hook needs no stack of its own.

**Where the hook runs and what that means for stock (starvation bound).** Booted QEMU TCB read (`tools/qemu_poke.py`, TCB
0x00040da0): priority **30**, stack 0x40260..0x40da0 = 720 words (2880 bytes), saved SP 0x40d24 = **124 bytes in use at the pend**. The
hook runs at loop level (called from the loop body, not under a deep stock call chain), so the stack in use is those 124 bytes
plus the monitor's own. Measured on a canary-filled stack in the halted-machine test, deepest monitor pass (a half that completes
a frame: decode, `krk_afsk_line`, `stn_add`): **308 bytes**. 124 + 308 + the hook frame is far under 2880. Priority 30 outranks
`task menu` (31, the screens and the UI event dispatcher), Led Manage (32), cpchanscan (33), TmrHnd (34), SqlCheck (35), CallDisp
(36), InputAlter (37), ScanDisp (38) and the tick/timer tasks (58/60); it is outranked by everything numbered 0-29 (keys 7/8, rf 10,
the TX/RX slot tasks, cds 29). The monitor never blocks, never waits on OS_TICKS and never loops on data: it returns after at
most one half (256 samples) of decoding per pass and does nothing at all on the other two passes of three. So tasks below 30 lose
at most one half's CPU time every 32 ms.

**CPU per half.** Measured by single-stepping `krk_afsk_feed` on the emulated CK803S over the real capture16 frame
(a one-off instruction-count script, three positions in the frame): 14163 / 14399 / 14473 instructions per 32 samples = **443-452
instructions per sample, about 115,000 per half** (including the libgcc 64-bit helpers); the screen copy loop and harvesting add
a few thousand. At 84 MHz: 1.4 ms per half at one cycle per instruction, 3-4 ms if the XIP flash waits and the multiplies cost 2-3
cycles each. So 4 % (best) to about 13 % (pessimistic) of the CPU at priority 30, only while armed, and a worst-case blocking of
the lower-priority tasks of 4 ms per 32 ms. The earlier "cNN" figure of the old screen header is not recoverable (no number
was ever written to the docs, the field is gone with the test screen); the decoder header's estimate of 2.4-3.2 MIPS is
consistent with the measurement (8000 x 450 = 3.6 MIPS). Hardware must confirm the cycles (list in section 4).

### G2 Memory: PASS, no new claim

* Stack: none (the hook uses task init's own 2880-byte stack, section G1).
* Monitor state `M`, 20 bytes, section `.bss.afsk` of the existing decoder claim `kraken_afsk_state` (0x18004c00-0x18005000). The
  claim was 0x3c8 used; now 0x3dc, **36 bytes free**. Layout: `R` 0x18004c00 (screen state, 20 B), `M` 0x18004c14 (20 B), the decoder `S` 0x18004c28
  (948 B). The shared fields (`R.last_sw`, `R.t_half`, `R.shown_frames`) are used by both the screen and the monitor, which never run
  together, so no duplicate state.
* Screen-owner flag `krk_screen_owner`, 4 bytes at 0x18007a34, in the main blob's `.bss` (kraken_ui_state, 0x18007640-0x18007c40:
  used to 0x18007a38, 520 bytes free). A new claim was not needed.
* The ring (8 x 20 B) stays where it was, ui2 `.bss` (0x18007e04). **ui2 `.bss` is unchanged: 9 bytes free.**
* Flash: ui2 grew from 21888 to 22972 bytes (+1084) in `kraken_ui_code2` (0x9000): **13,892 bytes free**. Main blob +48 B.
* No change to `allocations.json`. The build's overlap check passes.

### G3 Audio side effects: PASS (design), speaker coexistence by ear is hardware-only

State machine (`krk_aprsrx.c`, `krk_mon_tick`; one call per background pass, ~10 ms):

```
OFF  --krk_mon_set(1)--> IDLE (settle 0)                       the RX Monitor option row (OK on On); also krk_mon_set(0): release, magic cleared
IDLE --every gate open for 3 passes (30 ms)--> dec_arm(); R.last_sw = FM_ADDR_SW; assert --> ARMED
ARMED --FM_ADDR_SW changed to 1 or 2--> [gap > 6 ticks: dec_arm()] ; assert ; decode the finished half ; harvest frames into the ring
ARMED --any gate closes--> release --> IDLE (settle 0)
any   --OS tick counter went backwards--> OFF (rebooted with SRAM kept)
assert  = recheck all gates; GPIO 0x2F low; AUDIO_CONTROL bit 6 set (kept if already set; own6 remembers who set it)
release = AUDIO_CONTROL bit 6 cleared if own6; GPIO 0x2F high ONLY if the loaded channel is analog and no channel load runs
```

Gates (`mon_may_run`): (1) `krk_screen_owner == KRK_SCR_MAGIC` (any Kraken screen is running: Burst Voice, Parrot, Upload
Beacon, this screen, ...); (2) `krk_aprs_tx_busy()` (an analog beacon send, which runs in the maintenance task); (3) loaded
channel digital (`[0x3e1e4] & 0xf0`, the live struct, so under dual watch the monitor follows whichever side is loaded);
(4) channel load in progress (`[0x3e31c] & 8`, set by `channel_change_apply_dispatcher` from 0x03057426 to its end at 0x03057944);
(5) keyed (`[0x3fdc8] & 8`); (6) PTT pin down (`0x03012f48`, as `krk_satrf.c` does).
`[0x3e31f]` and `[0x3fdb8]` were left out on purpose: their meaning during a receive is not known and a false positive would silently
turn the feature off.

Stock transitions and where the monitor sees them:

* **Channel change / dual-watch flip.** Stock writes GPIO 0x2F only in `channel_change_apply_dispatcher` (0x030573e8, set for analog,
  clear for digital) and `screen_enter_apply_channel_and_rf_mode` (0x03038058), and in the TX tone sequences
  (`tone_ptt_preamble_seq*`, `tx_gate_companded_audio_burst_send`) that clear it and restore it on analog. The dispatcher runs in
  `task menu` (priority 31), which cannot preempt the monitor (30), and holds `[0x3e31c]` bit 3 for the whole load, so the monitor
  does not write anything inside a load: it neither asserts nor sets 0x2F high (which, on a digital target, would put the pin the wrong
  way). Afterwards flags8 describes the new channel; analog again: settle 3 passes, `dec_arm()`, assert. Digital: bit 6 is cleared
  and GPIO 0x2F is left to stock. A load shorter than one pass is not seen, which is fine: stock has already set 0x2F for the
  new channel and the next half re-asserts. Booted QEMU, stock dual watch (VFO A analog, B digital): the monitor followed 40 samples
  of flags8 0x04/0x14 with st = ARMED / IDLE, no crash (section 3).
* **TX start.** PTT pin and `[0x3fdc8]` bit 3 are polled every pass; the pin goes low well before stock keys (debounce 4 samples,
  `docs/satellite-radio.md`), so the release lands before the TX tone sequences touch 0x2F / AUDIO_CONTROL bit 6. The analog beacon
  (`krk_aprs_tx_send`) calls `krk_mon_yield()` right after setting its own busy word and before any keying, which releases
  synchronously (the beacon samples AUDIO_CONTROL after keying, `fmram_send`); the monitor also sees `tx_busy` and stays out.
* **Screens.** `krk_run_screen` sets `krk_screen_owner` and calls hook-table slot 3 (`krk_mon_yield`) BEFORE `enter()` runs
  (Burst Voice samples AUDIO_CONTROL in its enter), and clears the flag when it returns. The flag is a magic word, not a bit: SRAM noise
  at power-up cannot hold the monitor off.
* **Monitor toggled off** (RX Monitor row, OK on Off): `krk_mon_set(0)`, which yields first.

Rationale for bit 6: hardware 2026-10-15 "APRS RX mode 2" (docs/rx-audio-fm-mode.md, B2 row): with GPIO 0x2F low and AUDIO_CONTROL
bit 6 set the demodulated tone was audible again, i.e. capture and speaker coexist. Not hardware-verified: loudness/quality with real
voice, the volume knob, hiss with the squelch closed (the amplifier is gated by GPIO 0x35, switched by stock's rxvoice events
per the FH trace, so it should be silent).

### G4 Interaction safety: PASS (design), hardware confirmation open

* **No transmit anywhere in the monitor.** It writes GPIO 0x2F and AUDIO_CONTROL bit 6 only, reads FM_ADDR_SW, the FM RX RAM, the
  PTT pin and a few RAM flags. No PTT event, no FD6818 or FM Send RAM access (grep: no `OSFlagPost`, no `fd6818_reg_write`, no `FM_PTT`).
* **Beacon, Burst Voice, Parrot.** The screens are covered by the runner flag; the automatic analog beacon by `krk_aprs_tx_busy()`
  plus the synchronous yield. A monitor pass that was already past its gate check when the beacon starts is closed by `mon_assert()`
  rechecking every gate immediately before its two writes; what remains is a window of a few instructions if the maintenance task
  outranks priority 30 (its priority was not read), and the next pass releases anyway.
* **Ring sharing.** One writer at a time: the monitor yields while a screen runs, and the screen (priority 31) cannot preempt a
  monitor pass (priority 30), so ring/decoder access never interleaves. The screen keeps its own in-screen consumption unchanged. With the
  Monitor on the ring survives screen visits; with it off a visit starts a fresh list as before. The ring is magic-guarded (`RING_MAGIC`).
* **Dual watch.** Decodes whichever analog side is loaded. Each flip analog -> digital releases, digital -> analog re-arms with a clean decoder.
  The analog dwell is ~130-400 ms (docs/dual-watch.md), shorter than most APRS packets, so expect little decoding in dual watch (hardware).
  Gaps inside one analog side (a retune, a missed half, the satellite tracker's 1 Hz retune) re-arm the decoder when a half is
  more than 6 OS ticks late (the Burst Voice `rx_poll` precedent).
* **GPIO RMW.** `gpio_set/clear_output_bit` (IRAM 0x2c500/0x2c544) are plain read-modify-write of the bank register with no lock. The
  window is four instructions; the old screen has always written the same register 31 times a second from priority 31. Residual, not new.
* **Reboot with retained SRAM.** The magic alone could survive a reboot on a unit whose SRAM persists; the tick counter going
  backwards switches the Monitor off.

### G5 QEMU verifiability: PASS

QEMU has no FD6818 audio: the FM Receive RAM does not retain data and FM_ADDR_SW does not toggle. The two hardware reads are
out-of-line (`mon_fm_sw`, `mon_half_ptr`) and the test supplies them. Everything else is the real code: the decoder, the ring, the
gates, the register and GPIO writes (gpio_set/clear intercepted and recorded, AUDIO_CONTROL read back from the modem model).

Verifiable in QEMU (all done, section 3): splice and hook slot, off by default / noise SRAM, arm/run/release on each gate, GPIO 0x2F
and bit 6 sequence, idempotent release, real on-air frame (capture16) decoded through the monitor into the shared ring and dedupe,
the gap re-arm, the stack depth, the reboot rule, the runner flag and yield ordering, the beacon yield, the screen as a viewer, and a
booted standby run under the stock image with stock dual watch.

Hardware only: the halves actually flowing at standby (FM RAM active with the squelch closed), the audio (coexistence by ear, volume,
hiss), CPU cost and its effect on the UI, decode of real traffic, dual-watch yield, beacon-while-monitoring, the stock-vs-monitor
behaviour of GPIO 0x2F at squelch open.

> **2026-11-02 (option level):** the switch moved from the `#` key to the KRAKEN > RX Monitor row, presented through the shared stock option-level mechanism. The monitor engine
> (sections 1.G1-G5) is unchanged. Booted QEMU checks (`tests/optrow_checks.py`): the level opens on the stock radio-mark list, OK on On sets `M.magic` = `MON1`, OK on Off clears it, Back cancels.

## 2. Files

* `patches/kraken_ui/krk_aprsrx.c`: monitor (`krk_mon_tick/yield/set/on`), `harvest()` and `dec_arm()` shared with the screen, status row, and the `krk_monitor_row` option row (`mon_get` / `mon_set`, KRAKEN extension slot 2). Placement: a new row after APRS RX; engine unchanged.
* `patches/kraken_ui/krk_periodic.c`: `krk_hook_bg_tick`. `kraken_ui.patch.json`: splice `bg_tick_hook` at 0x03013f48, defsym `krk_screen_owner` for blob 2.
* `patches/kraken_ui/krk_core.c`: `krk_screen_owner`, set/cleared in `krk_run_screen`, hook slot 3 call. `krk_ext.c`: slot 3 = `krk_mon_yield`. `krk.h`.
* `patches/kraken_ui/krk_aprs_tx.c`: `krk_aprs_tx_busy()`, `krk_mon_yield()` in `krk_aprs_tx_send`.
* Every frame the decoder finishes (screen or monitor) is also handed to the F18 APRS Messages inbox (`krk_afsk.c` `emit()`, [aprs-messages.md](aprs-messages.md)).
* `tests/mon_checks.py` (new, called from `tests/kraken_ui_test.py`), `tests/parrot_qemu_test.py` (hook table slot 3 is now `krk_mon_yield`).

## 3. Checks (47 new, full build 585 PASS, 0 FAIL)

Static (6): stock bytes at 0x03013f48, splice target, the `br32` back untouched, hook table slot 3, `M` inside the decoder claim, the owner flag inside the main claim.
Off (3): noise state and zero state do nothing; the hook with the Monitor off makes the stock call only.
Machine (22): `krk_mon_set(1)` initialises from noise (ring included); three settle passes write nothing; the fourth arms (GPIO 0x2F cleared once, bit 6
added, other AUDIO_CONTROL bits kept, st 1, own6 1); no write without a new half; for each of runner flag, `tx_busy`, keyed, PTT pin, digital
channel, channel load: released on the next pass (GPIO 0x2F high only on analog without a load), bit 6 cleared, then idempotent, and re-armed after exactly
4 passes when the gate opens; a stock-cleared bit 6 is restored with the next half.
Packets (6): capture16 frame 1 (47 halves behind 30 halves of channel noise) decodes into the ring as N0CALL / ">DM-32U"; 47 halves counted; frame 2
dedupes to one entry; a 100 ms gap mid-frame discards the torn frame, the next frame decodes; deepest pass 308 bytes.
Lifecycle (7): reboot rule, yield (synchronous release, still on), yield when idle, `krk_mon_set(0)` while armed, noise state untouched, runner (flag set and
monitor already released inside `enter()`, flag cleared on Back), beacon (monitor released before the send logic, `tx_busy` back to 0).
Screen (6): enter keeps the ring with the Monitor on and wipes it with the Monitor off; no key toggles any more (`#`, OK and others leave it Off; the screen's title APRS MON/RX and status row follow the state set through the option row's `mon_set`); the option row's getter/setter round trip; digital channel
shows the message and the help row.

Booted run (not in the suite, `tools/qemu_ui_run.sh` with `SPINOR=1` and `POKE`, magic poked at 15 s, state read each second to 75 s): no crash, the
monitor armed and released with the stock dual-watch engine's flags8 flips (0x04 analog / 0x14 digital), AUDIO_CONTROL bit 6 tracked it,
task init's stack high-water 2152 of 2880 bytes in QEMU (stock's own use; the hook runs at loop level).

Known limitation of the decoder, found while writing the packet test (host experiment with `tools/`-style feed): the preset B decoder
misses a frame that follows ~1 s of exact zeros or noise of rms 100 (frames 0 of 1; rms 1000 and 3000 decode; a frame right after another decodes
with one bad FCS). The packet test therefore listens to rms-1000 noise first. If the receiver delivers digital silence to the modem
while the squelch is closed, the first packet after a quiet spell may be lost on hardware; this applies to the stations screen as well.

## 4. Hardware verification list (nothing here has run on a radio)

1. **Halves flow at standby.** Monitor on, screen closed, analog channel, squelch closed: the "h" counter on the screen's row 9 (read on the next visit) grows ~31/s.
   If it stays 0 the FM RAM is idle at standby (the "no RX: reload ch" cold-boot case) and the feature needs a channel reload or a different trigger.
2. **Audio coexistence by ear**: speaker plays a received voice call with the Monitor on; compare volume and quality with it off (the volume setting may act
   on the FD6818 path, not on the DAC path); no hiss with the squelch closed; no click at squelch open (stock re-drives 0x2F, the monitor re-lowers it within one half).
3. **CPU at standby**: UI responsiveness (key scan jitter up to 4 ms), no missed key presses, battery draw, with the Monitor on (4-13 % estimated).
4. **Real packet decode at standby**: a 144.390 packet while the radio is at the home screen; then open the screen: the station is listed.
5. **Dual watch**: A analog / B digital: no audio glitches at the flips, DMR RX unaffected (GPIO 0x2F low on digital, bit 6 clear); then analog/analog.
6. **Beacon while monitoring**: Upload Beacon (two presses) and the automatic interval beacon with the Monitor on: the send goes out as before, the receive resumes
   after (settle 30 ms + decoder reset).
7. **PTT**: voice PTT on an analog channel with the Monitor armed: normal modulation, no stuck audio switch afterwards.
8. **Burst Voice / Parrot / APRS RX screens** with the Monitor on: unchanged behaviour (they own the path while open), Monitor resumes on Back.
9. **Reboot**: switch the radio off and on with the Monitor on: it comes up Off.

## 5. F14b Alert: a popup at standby per decoded packet (issue #5, 2026-11-11)

Status: built, QEMU-verified (host test `tests/test_aprs_alert.py`, halted-machine and booted checks in `tests/alert_checks.py`,
one check in `tests/mon_checks.py`), **not run on a radio**. Addresses for DM32.00.01.048.

**Setting.** The RX Monitor option level has a third value: `Off` / `On` / `Alert` (Alert = On plus the popup). `mon_set`
(krk_aprsrx.c) calls `krk_aprs_alert_set(idx == 2)` and then the unchanged `krk_mon_set`; `mon_get` reads 0, 1 or 2. RAM only
(magic word `ALRT`), off at power-on. No settings record, no new menu row.

**Data path.** `harvest()` calls `krk_aprs_alert_frame(ring[0].call, ring[0].info)` right after `stn_add`, so the popup shows
exactly what the APRS RX list's top row shows (call-SSID cut at 9, the first 7 text characters, unprintables as `.`). It only
stores the two pointers and the tick count and marks the notice pending; it does nothing while a Kraken screen runs (the APRS RX
screen decodes in task menu; the owner is looking at the list). `krk_aprs_alert_tick()` runs on every pass of the background
loop (`krk_hook_bg_tick`, right after `krk_mon_tick`), in the same task as the monitor's harvest, so the pending state and the
ring entry never change under it.

**Why drawing from the background task is safe.** The popup is the stock box (`krk_popup_draw`: `display_draw_dialog_panel`
0x03019be8, the icon bitmap 0x03039034, centred text 0x03018b5c; each takes the LCD semaphore 0x39344 itself). The tick draws only when:

| Gate | Read | Why |
|---|---|---|
| standby on screen | screen-mode byte 0x320af is 0 or 0x5b | as the standby tags (docs/standby-tags.md) |
| no key held | stock poll's confirmed key 0x392c4 == 0 | a key being handled is about to change the screen; also keeps our event post out of a pass that posted a key event |
| not keyed, PTT up | 0x3fdc8 bit 3; `ptt_pin_down` 0x03012f48 | no popup during TX or a PTT press; every Kraken send keys |
| task menu idle | keymsgq OS_Q (address from the literal 0x030131d8, 0x38d70): `PendList.NbrEntries` (u16 at +0x10) != 0 and `MsgQ.NbrEntries` (u16 at +0x2a) == 0 | task menu draws every stock screen and runs every Kraken screen. Pending on an empty queue, it is drawing nothing. The background task (priority 30) outranks it (31), so task menu cannot start a redraw until the popup is complete |

The OS_Q layout is uC/OS-III with the debug fields on (Type 'QUEU', NamePtr, PendList {Head, Tail, NbrEntries}, DbgPrev/Next/Name,
MsgQ {InPtr, OutPtr, NbrEntriesSize, NbrEntries, ...}); read in a booted QEMU run at standby: `+0x10` = 1 (task menu pending),
`+0x28` = 5 (queue size), `+0x2a` = 0. A Kraken screen blocks task menu in its runner (not pending: no popup), so no separate
screen-owner gate is needed in the tick. The remaining interleave is the maintenance task (priority 38) drawing the clock or the
standby tags at the same moment, which touch other rows.

**Clearing.** After 500 ticks (5 s), or on the first pass after Back is released (Back seen at 0x392c4 = 0x06 while the popup is up),
the tick posts UI event 100 with `write_field_and_mbox_post(1, 100)` @0x03013178 under the same gates. That is the event stock posts
at the end of every call and TX and from the Menu Exit timer (docs/menu-exit-timer.md); at standby task menu answers it with a full
standby redraw (`dialog_show_number_or_alt` 0x0303b394 -> `standby_screen_refresh_with_statusbar` -> rows). Booted QEMU: the box
disappears, the mode byte stays 0, the menu history 0x320ac is unchanged, and the only pixels that differ from the frame before the
popup are the RSSI bar. Because all posted events share one byte (0x38e29), the post is made only when the queue is empty and no key
is down, so it cannot overwrite a queued key event. If the screen leaves standby while the popup is up (Menu, a call), stock's
redraw replaces it; the tick then posts one extra event 100 when standby is back and quiet (a redundant standby redraw).

**Rate limit.** Not before 50 ticks after the frame (the carrier's tail, stock's end-of-receive redraw); at most one popup per
800 ticks (the newest pending station wins); dropped if not shown within 1000 ticks of the frame (a menu was open); the same
callsign (djb2 hash of call-SSID, 16 bits) as the last popup within 30000 ticks gets none. Only the last popup's station is
remembered (two stations alternating can each pop once per 8 s). The times are OS ticks: under stock power save the tick runs up to
~2x fast (docs/fm-send-ram-tx.md section 6), which at worst halves them.

**Memory.** State `A`, 24 bytes in `.bss.aprs` (claim `kraken_aprs_state`); nothing in kraken_ui2's plain `.bss`. Code in
kraken_ui2: 468 bytes in all (048 link map, `.text` 0x8afc -> 0x8cd0): `krk_aprs_alert.c` (`-Os`) about 410 with its labels and
strings, `krk_aprsrx.c` about +56 (getter, setter, the harvest call), `krk_periodic.c` +4. No new splice: the existing `bg_tick_hook` (0x03013f48) carries it.
New stock address: RAM 0x392c4 (the confirmed key), translated identically for DM32.01.L01.048 and DM32.01.01.049 by
`tools/gen_fw_maps.py` ("ram: referenced by stock code"). All other addresses were already in the maps.

**Receive only.** No PTT post, no FD6818 or modem write; the halted-machine and host checks fail on any PTT flag post.

**Not done (follow-ups).** Distance and bearing for uncompressed positions (needs a position parser and integer distance/bearing code;
none exists in the blobs, and it did not fit). Backlight on for the popup. A per-station history of more than one entry.

**Hardware checks (two radios, after the merge).** Use a second radio to send the analog APRS beacon (Upload Beacon) on a simplex
analog channel; the receiving radio has RX Monitor = Alert and sits at standby.
1. Popup at standby: callsign-SSID and the first 7 text characters, about 0.5 s after the packet ends; gone after about 5 s with
   the standby screen intact (no leftovers in the VFO rows).
2. Back closes it at once; the next Back / Menu / arrow keys work normally (no lost key, no channel change by the popup).
3. PTT while the popup is up transmits normally; after TX standby is clean.
4. A second beacon within 8 s: no second popup; the same station again within 5 min: none; another station: one.
5. In a menu when the packet arrives: no popup over the menu; back at standby within 10 s: the popup appears.
6. `On` (not Alert): no popup, the station is in APRS RX.
7. Power save: unplug the cable, wait until the radio idles, then beacon from the other radio. Expect the monitor itself may not
   decode there (FM RX clocks, docs/fm-send-ram-tx.md section 6, not fixed for receive); note whether the popup appears and how long it stays.
8. Dual watch with one analog side: popup intact over the dual-watch flips.
