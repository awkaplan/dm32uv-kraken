# Menu Exit Time: the menu reset timer (stock DM32.00.01.048)

Status: found, verified under QEMU (control vs keep-alive, same key script). Confidence: HIGH for the
counter/limit cells, the increment/overflow code and the keep-alive; MEDIUM for the exact seconds-per-count
on hardware (derived from code plus QEMU timing); LOW for how the limit byte is initialised at boot from the codeplug (not found statically).

## 1. Answer in one block

- **Counter**: `u8` at RAM `0x0003e396`, counts UP in 1 s steps, starts from 0.
- **Limit**: `u8` at RAM `0x0003e397` = `Menu Exit Time setting * 5` seconds (0 = Off, 5..60 s). QEMU default (no codeplug) reads 10.
- **Tick**: `keypad_text_entry_commit_gate` @`0x030544ac` (misnamed; it is the menu inactivity checker) runs once per ~1 s from `ui_periodic_maintenance_task_loop` @`0x0305476c`.
- **Overflow** (`++counter >= limit`): counter reset to 0, then `write_field_and_mbox_post(1, 100)` posts UI event 100 (= leave menu to standby) into the UI mailbox. Nothing is torn down at that moment: the event just sits in the mailbox until the stock menu loop next reads it.
- **Reload on key**: any UI event dispatched by `ui_event_code_dispatch_main` @`0x0303fa04` does `st.b 0 -> 0x3e396` (two sites, `0x0303fb42` and `0x0303fb5a`). Stock keys reach it through the stock poll. A patch screen that parks the poll never produces those events, hence no reload.
- **Recommended keep-alive**: write byte `0` to `0x0003e396` once per 100 ms tick of the blocking screen (and once at screen entry).

```c
*(volatile uint8_t *)0x0003e396 = 0;   // "menu activity" for the Menu Exit Time timer
```

asm form (C-SKY): `lrw r3,0x3e396 ; movi r2,0 ; st.b r2,(r3,0)`. There is no stock "reload" function to call: stock reloads inline with exactly this store.

## 2. Why the patch screen got kicked (explains the repro)

- Counter keeps counting while the patch screen runs: the gate only requires screen-mode byte `*(u8*)0x000320af` in `7..0x3f` (or `0x69`, `0x77`) and `(*(u8*)0x0003fdb8 & 2) == 0`. The KRAKEN screens run with mode byte 7 / 14 / 21 (measured), so they are counted as "in menu".
- At overflow the stock code only posts event 100. The patch screen runs in task menu and never reads the mailbox, so the screen stays up (measured: mode byte stays 21 after overflow). The counter restarts at 0 and overflows again every `limit` seconds, re-posting.
- When the patch screen returns via Back, the stock menu loop consumes the pending event 100 and goes to standby instead of showing the sub-list. That is exactly the behaviour observed on hardware ("first Back lands on standby; with a short dwell Back returns to the sub-list").
- Consequence: the damage is a deferred "go to standby", so a keep-alive must stop the overflow, not merely repair state after it.

## 3. Evidence: static

Setting plumbing (Display menu row "Menu Exit Time", string `0x030bcf90`, index 34 of the 36-entry Display pointer array at `0x030b85c0`):

- Option-init table `0x030b0900..` (entries in menu order: Language `0x0304471c`, Display DIR `0x0304478c`, LED Indicator `0x030447f0`, **Menu Exit Time `0x03044860`**, Time Set `0x03044888`).
- `0x03044860` (corpus name `zone_record_template_load_id0x5c`, misnamed) loads the current value from `settings_block[0x59]` (block base `0x0003e9b4`, so RAM `0x0003ea0d`) into the screen state word. It is a plain index 0..12 (steps of 5 s, 0 = Off).
- Apply handler `menu_wrapper_field0x32` @`0x0304fe20`:

```
0x0304fe22  ld.b r2,(r3,0x0)        ; r3 -> active_screen_state_word 0x3e5cc
0x0304fe26  st.b r2,(r3,0x59)       ; settings_block[0x59] = value      (r3 = 0x3e9b4)
0x0304fe2a  ixw  r1,r2,r2           ; r1 = value * 5
0x0304fe2e  lrw  r3,0x304fe50       ; pool slot -> 0x0003e38c
0x0304fe30  st.b r1,(r3,0xb)        ; *(u8*)0x3e397 = value*5   <-- LIMIT
0x0304fe34  movi r1,0x32 ; bsr 0x0300fa28   ; flash_field_write_cached(4, 0x32)  (codeplug persist)
```

Pool slots read with the "slot vs. value" gotcha in mind: `0x0304fe50` holds `0x0003e38c` (value), so offset 0xb is `0x3e397`.

Tick / check function (`0x030544ac`), the relevant lines (pool slot `0x03054604` = `0x0003e38c`; `0x03054614` = `0x000320af`; `0x03054618` = `0x0003fdb8`):

```
0x0305458e  lrw r3,0x3054604
0x03054590  ld.b r3,(r3,0xb)        ; limit  (0x3e397)
0x03054592  bez r3,0x030546d6       ; limit == 0 -> Off, return
0x03054596  lrw r0,0x3054604
0x03054598  ld.b r1,(r0,0xa)        ; counter (0x3e396)
0x0305459a  addi r1,0x1
0x0305459c  zextb r1,r1
0x0305459e  st.b r1,(r0,0xa)        ; ++counter
0x030545a0  cmphs r1,r3            ; counter >= limit ?
0x030545a2  bf 0x030546d6           ;   no -> return
0x030545a4  movi r1,0x0
0x030545a6  st.b r1,(r0,0xa)        ; counter = 0
...                                 ; (subtype 8/10 only: OSTmr_state_dispatch_callback(inputcursor timer))
0x030545c6  lrw r3,0x3054624        ; p_radio_mode_state_0003e318 + 0x...
0x030545c8  ld.b r2,(r3,0x11) ; ins r2,0,0,0 ; st.b r2,(r3,0x11)   ; flags_0x7 &= ~1
0x030545d2  movi r0,0x1
0x030545d4  movi r1,0x64            ; event 100
0x030545d6  bsr 0x03013178          ; write_field_and_mbox_post(1,100)
```

Other writers of the counter (all reset it to 0, i.e. these are the stock "reload"s):

- `0x0303fb42`, `0x0303fb5a` in `ui_event_code_dispatch_main` (`st.b r2,(r3,0xa)` with r3=`0x3e38c`): every dispatched UI event.
- `0x0303ca16` in `FUN_0303c9fc`.
- `0x0305451e`, `0x03054548`, `0x0305462c` inside the gate itself: cleared while a call is active, in certain dialog states (`flags_0x1e & 0x70 == 0x20`, `flags_0x1 & 3 == 1`), and whenever the mode byte is outside the "menu" range (the non-menu branch also runs the FM/standby bookkeeping).

Tick source: `ui_periodic_maintenance_task_loop` pends on flag `0x20000` of group `0x0003fd90`. The flag is posted by `FUN_030546dc` @`0x030546dc`, called once per `main_ui_background_task_loop` iteration (watchpoint on `0x3e38e` showed `lr = 0x3013f4c`, inside that loop); it counts a prescaler byte at `0x0003e38e` to 100 and then posts. Hence the gate runs at the rate of one per 100 background-loop iterations, measured as 1 count per ~1.0 s of guest time (see below). (Note: dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md) says the UI tick is 10 Hz; with a 100-count prescaler the measured 1 s per count implies the loop actually runs at 100 Hz. I did not resolve that discrepancy statically; the QEMU measurement is the evidence for 1 s.)

Ruled out while searching (do not confuse): `settings_set_menu_hold_interval` @`0x0304a32c` (settings byte `0x3a`; it is the key-hold timer, limit `u16 0x3e398`), `countdown_timer_tick_standby_trigger` @`0x03054700`, `idle_timeout_counter_tick` @`0x0306069c`, `ui_inactivity_timeout_tick` @`0x0301cc68`, and the `tmrkey` uC/OS timer (settings 0x51/0x5c, key repeat).

## 4. Evidence: dynamic (QEMU, gdb_rsp.py, patched image `out/kraken/progmem_KRAKEN_patched.bin`)

1. Counter counts while the patch screen runs (no codeplug, limit 10). Sampling `0x3e396`/`0x3e397`/`0x320af` with 0.4 s polling:
   - `0x3e396` steps +1 about every 1.0-1.2 s wall (QEMU about real time, gdb halts small), wraps `9 -> 0` at 20.4 s, 33.1 s, then again; mode byte `0x320af` = 7 (top menu), 14 (category list), 21 (RSSI screen). Limit byte `0x3e397` = 10 throughout. After each wrap the screen stays displayed (event queued, not consumed).
2. Control (no writer), key script: lead-in, MENU, Up, Ok, Ok(dwell gap 3000), Back, Back. Final frames show standby ("Null Ch.") after the first Back: nonblack 2997 px. (Same as the failure seen on hardware.)
3. Keep-alive (gdb `M3e396,1:00` every ~0.4 s while mode byte >= 7, otherwise identical script): after the same dwell the first Back lands on the sub-list (RSSI Meter / About), second Back on the main menu: frames 9235 / 9786 / 13072 nonblack px. No standby.
   - Conclusion: forcing `0x3e396 = 0` periodically fully prevents the exit.
4. Watchpoint on `0x3e38e`: writer is `pc 0x030546e8` (inside `FUN_030546dc`), `lr 0x3013f4c`. Watchpoint on `0x3e396` showed the stock reload write at `pc 0x0303fb5c` (just after the `0x0303fb5a` store) when the Down key was injected.

## 5. Recommended patch code

- Where: in the RSSI-Meter screen's 100 ms redraw loop (and the other blocking KRAKEN screens), and once on entry.
- What: `*(volatile uint8_t *)0x0003e396 = 0;`
- Rate: any period well under `limit` seconds works; the 100 ms loop is plenty (the minimum limit is 5 s, and the counter changes at most once per second).
- Atomicity: single byte store vs. the stock single-byte read-modify-write (`ld.b / addi / st.b`) once per second in a different task; worst case a lost increment, which is harmless.
- Also zero it at screen entry because the counter may already be 9 on entry (the operator pressed keys in stock menus a while ago); a pending event 100 posted *before* entry cannot occur unless the operator dwelled in stock menus for `limit` seconds, in which case the stock UI would already have exited.
- Not recommended: writing `0x3e397 = 0` (Off) during the screen and restoring on exit: needs a guaranteed restore on every exit path and fights the Settings menu writer. Also not recommended: calling `write_field_and_mbox_post` or draining the mailbox.
- Side note: this also keeps the cursor timer branch untouched; the gate's subtype 8/10 side effect (`OSTmr_state_dispatch_callback(p_ucos_tmr_inputcursor...)`) is skipped as well, which is fine.

## 6. Open items

- How `0x3e397` is initialised at boot from the codeplug byte (`flash_field 0x32` -> `settings_block[0x59]` -> `limit`): no static writer found other than the menu apply handler (scan covered flash corpus only, not IRAM). In QEMU with no codeplug `settings_block[0x59]` reads `0xFF` while the limit reads 10, so the limit has an independent default. Irrelevant for the keep-alive, relevant only if you want to read the configured value (read `0x3e397` directly, or `0x3ea0d * 5`).
- Hardware confirmation of the 1 s/count (4 s timeouts differ from 5 s?): derived from QEMU time; the stock setting range 5..60 s matches `value * 5` counts of 1 s, which supports it.

## 7. UI freeze after exit (2026-11-01)

**Symptom (hardware v50/v51, three times).** After Back out of a KRAKEN screen (Scrambler twice, Parrot; TX Power Trim landed on standby too) the list was drawn but the stock UI was actually at standby: the next Up/Down changed the channel and redrew standby. If the next key was Back and/or MENU the display froze (no draws, injected and physical keys ignored; one radio needed a power cycle, another recovered on a physical Down). Back from RSSI Meter returned to the list correctly.

**Evidence (RAM dump of a frozen radio).** Key events reach "task menu" (keymsgq 0x38d70, value 5 = MENU), poll and mailbox healthy; `dialog_history_ui_state` 0x320ac = `03 03 04 5b`, depth counter 0x3e8ec = 0. Screen code 0x5b is what `write_status_mode_byte_pair(0x5b, 1)` stores on event 0x2c ("go to standby"); a healthy list inside the item reads `03 03 04 15`, after a proper pop `02 02 03 0e`.

**Cause.** `krk_run_screen` runs inside "task menu" and never returns to its event loop while a screen is open. The stock UI posts events on that task's queue with `write_field_and_mbox_post` @0x03013178 (`a0` = kind, `a1` = event byte at 0x38e29, queue pointer literal at 0x030131d8 = 0x38d70). Kinds 1 and 3 are queued unconditionally; only kind 2 (the key events from the poll handlers) is dropped while no pend is outstanding (flag 0x0003078c, set by `keymsgq_command_dispatch_and_store` @0x03013144 around its pend). The posters that run during a screen are the ordinary ones: call end / TX end (event 100 from `tx_supervisory_tick`, `ptt_release_handler`, `call_display_countdown_and_ui_event_trigger`), channel refresh (`channel_switch_and_screen_refresh`, `vfo_freq_display_refresh`), `dialog_close_and_tx_release` (event 0x2c), the periodic dispatcher 0x030131e4 (status events 0x18). The Scrambler, Parrot and TX Power Trim screens are the ones that key or listen on RF; RSSI Meter sees fewer. When the runner returns, the item-action tail (0x0303d872 -> main_menu_screen_dispatch) draws the list and task menu then dequeues the stale events one by one, for example 0x2c: the state byte goes to 0x5b with no repaint, so the list stays on screen over a standby state. MENU/Back in 0x5b go through handler 0x0303fe08 and `tx_gate_write_call_record(1)` 0x030134c4, which draw nothing. (The 2026-10 exit-timer finding of section 4, "Back lands on standby after the timer wrapped, event queued, not consumed", was the same mechanism with event 100.)

**Fix.** After the history pop and the list restore (the last thing before the poll is re-armed), `krk_run_screen` empties the queue with stock's own pend in non-blocking mode: `guarded_request_command_dispatch` @0x0306a1b8 is uC/OS `OSQPend(q, timeout, opt, &size, &ts, &err)` (checks: q->type == 'OSQ.', `opt` 0 or 0x8000 else error 0x5e25, empty + non-blocking returns 0 with err 0x61b0 before any scheduling). Loop: `OSQPend(*(u32*)0x030131d8, 0, 0x8000, &size, 0, &err)` until it returns NULL or sets err, at most 40 times (`krk_drain_keymsgq`, krk_core.c, about 50 bytes of the main blob). Nothing is lost that the operator could see: the stale events described states of the screen we were in, and every posted message is the same static byte pointer, so nothing needs freeing. The dropped events are by construction the ones that happened while the screen owned the UI.

**Alternatives not taken.** (2) redrawing standby when 0x320af == 0x5b would leave the operator at standby after Back, not in the list. (3) v41's dispatch of the stock Back through the normal event path costs a visible black frame.

**Residual risk.** An event posted in the few milliseconds between the drain and task menu's next pend (the list redraw in the tail) is still delivered. If the freeze returns, the follow-up is a second drain on the first poll tick after re-arm, or a splice at the tail's end.

**Tests.** `tests/kraken_ui_test.py` `check_exit_and_rssi`: the runner is run on the halted machine with the stock functions faked; after Back it must call the history pop, then the restore, then OSQPend(0x38d70, 0, 0x8000) until the (faked) queue reports empty (3 stale events -> 4 pends, empty -> 1, never-empty -> bounded at 40, error with a message -> 1), r4-r11/sp preserved.

**Hardware test (one action).** On any radio: KRAKEN > Scrambler (or Parrot), open it, wait about 10 s (or hand-key / receive a call while it is open), press Back, then press Back again and MENU. Before: after the first Back the list showed but Up/Down changed the channel and the second Back froze the display. After: the first Back leaves the list on screen; the second Back goes up one level to the main menu, MENU/Back keep working, and Up/Down move the list cursor.
