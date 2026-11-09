# DMR parrot: what stock recording and playback really do (DM32.00.01.048)

Static analysis only (corpus [dm32uv-re](https://github.com/awkaplan/dm32uv-re), image `progmem_stock_DP570UV_20260926_FULL.bin`, IRAM image
`IRAM_dm32.00.01.048.bin`). Nothing was run on a radio or in QEMU. Literal-pool values below were read from the image
(the value in the pool slot, not the slot's address), not taken from Ghidra names. Many corpus names in this area are WRONG; the wrong ones are listed in section 7.

Confidence tags: HIGH = disassembly plus a second independent path; MED = one path or inferred from UI behaviour; LOW = guess.

## 0. Verdict (read this first)

- Stock has recording, and stock has playback, but **stock playback is speaker-only**. There is no stock "transmit a stored recording" function.
- `flash_audio_clip_playback_tx` is NOT the answer: it is the PCM tone/prompt clip player (PTT-preamble tones, 5-tone etc.). It never touches the modem or the AMBE path.
- A parrot therefore needs ONE new piece of code: feed stored 27-byte AMBE frames into the modem TX voice buffer while the stock TX state machine is keyed.
  Everything else (capture of RX frames, keying, destination selection) has a stock mechanism that can be reused or tapped.
- RX recording is automatic once the stock "Record Switch" is on and "Record Sel" includes Rx. But turning the switch on writes the settings flash, and each recorded call erases flash sectors. A RAM-only capture is the better parrot design (section 5).

## 1. How stock recording works

### 1.1 What is recorded: raw 27-byte vocoder payloads

- Unit = **27 bytes = one DMR voice burst payload = 3 AMBE+2 frames (3 x 72 bits incl. FEC), one per 60 ms**. Stored undecoded and un-re-encoded.
  Evidence: the recorder task copies exactly `0x1b` bytes per iteration (`bsr FUN_0001620c`/`FUN_0002e37c` with `movi r2,0x1b` at 0x03021324/0x03021336; file write `movi r3,0x1b` at 0x030214d2), and the playback loop reads `0x1b` bytes per step (`flash_block_read(..., 0x1b)` in `channel_send_dtmf_autodial_call`). HIGH.
- Capture point (RX): the IRAM vocoder task (`FUN_0002f000`, loop at 0x2ec30, `OSTaskSemPend` per frame). Per RX frame it reads 27 bytes from the baseband voice window
  with `0x172cc(0, buf=0x46b38, 0x1b)` (copy loop from MMIO `0x16000960`, pool at 0x17324), then, if the RX-record flag byte `[0x48160+0x1496]==1`, calls `0x2ebe0`.
  `0x2ebe0` copies the 27 bytes from `0x46b38` into a 2-slot ping-pong ring at **0x48128** (slot index byte **0x48124**, slot size 27) and `OSSemPost`s **0x45648**.
  HIGH on the copy and post; MED on "this is the RX side" (it follows from the flag pairing in `FUN_00015fa4`, see below).
- Capture point (TX): `0x2e558` copies 27 bytes from encoder output `0x49b18` into ring **0x46b5c** (index byte **0x49b38**) and posts sem **0x45670**. Used only for Tx recording.
- Switches in IRAM: `FUN_00015fa4(n)` (0x15fa4) sets the two flag bytes: n=0 both 0; n=1 -> `[0x48160+0x19d4]=1,[0x456c0+0x1496]=0` (TX record on); n=2 -> `[..19d4]=0,[..1496]=1` (RX record on); n=3 both 1. HIGH (disassembly of 0x15fa4..0x15ffe).
- The consumer is the recorder task `channel_activity_log_flush_task` @0x030212f4 (corpus name is wrong; it is the voice recorder). It loops:
  state byte **0x0003bd39** (`write_byte_field_030212e4` @0x030212e4 writes it; 1 = RX recording, 2 = TX recording, 5 = stop requested, 0 idle):
  if state in {1,2} and bit4 of `*(0x3e318+0x11)` is clear it pulls one 27-byte frame (RX: `FUN_0001620c`, TX: `FUN_0002e37c`, both `OSSemPend` 70 ms on 0x45648/0x45670 through IRAM `0x16188`->`0x11a58`), otherwise `OSTimeDly_ms(60)` (0x0302158c). HIGH.
  (Meaning of bit4 of 0x3e329 not determined; the task idles when it is set. LOW.)

### 1.2 Is RX recording automatic? Yes, once enabled

- RX call start: `rxvoice_task_entry` flag-2 block calls `audio_vocoder_buffer_flush_init` at **0x0302093a** (`bsr 0x03021948`).
  It starts an RX recording when ALL hold: `*0x3fdc0 & 0x10`, `*0x3fdb8 & 0x10`, `0x3bd38[3] & 1` (record switch, mirrored in RAM), and `0x3bd38[2]` is 0 or 2 (Record Sel = Rx or Rx/Tx).
  It then does `FUN_00015fa4(2)` + `write_byte_field_030212e4(1)`. HIGH (decompile of 0x03021948 + disassembly).
- TX start: `audio_vocoder_buffer_flush2` @0x03021a70 (called from `tx_dialog_event_state_dispatcher`) starts TX recording when `0x3fdc0&0x10`, `0x3bd38[3]&1`, `0x3bd38[2]` in {1,2}: `FUN_00015fa4(1)` + state 2. HIGH.
- Mapping `0x3bd38[2]`: 0 = Rx, 1 = Tx, 2 = Rx/Tx follows from the two gates above and manual 7.7.2. MED-HIGH.
  `0x3fdc0 bit4` is probably "DMR mode" (manual: record is DMR only), `0x3fdb8 bit4` is an unidentified radio-state bit. LOW on both meanings.
- RX call end: `rxvoice` flag-4 block, **0x03020980 `bsr tx_gate_state_transition`** (0x03021a14), immediately BEFORE the planned hook at 0x03020984.
  It calls `gps_state_flush_mode1` @0x0306116c (corpus name wrong; it is "stop RX recording"): `FUN_00015fa4(0)`, state=5, then writes the final 2-byte duration to the header (`flash_write_split_across_page_boundary(hdrSector,0x12,0x3e120,2)`) and writes the data sector's trailing length via `mutex_guarded_param_dispatch_3` (offset 0xffe), clears the file-open bit `0x3ff84+0x2b` bit2. HIGH.
  So when the 0x03020984 hook runs, the recording of that call is already finalized and is the newest list entry.
- Record switch itself: `scan_toggle_persist_and_notify` @0x03015bb0 (really "Record Switch" toggle, key/menu) -> `settings_toggle_and_notify_0x45` @0x03061a88 which does
  `flash_field_write_cached(4,0x45,...)` = **a settings write to flash**, sets RAM bit `0x3ff84+0x2b` bit0 and calls `set_single_bit_field_0x3` (mirror to 0x3bd3b). UI events 0x18 "Record ON" / 0x19 "Record OFF". HIGH.
  A patch that wants stock recording must NOT call this at runtime (extra settings write); poke the RAM mirrors instead (or patch the gate), see section 5.

### 1.3 Where it is stored (data chip) and the format

Region (HIGH, from `spi_flash_config_block_magic_scan` @0x03061280 + pool at 0x30adb48/0x30adb4c): byte range **0x6DC000 .. 0xFFFFFF** of the 16 MB data chip (W25Q128), i.e. sectors 0x6DC..0xFFF = 2340 sectors of 4 KB (~9.1 MB).
Runtime sector variables (RAM): base sector `0x3ffc8`, then `0x3ffd0` (= base+2), `0x3ffbc` (= base+3), `0x3ffd4` (= base+8), pool start `0x3ffc4` (= base+9), last sector `0x3ffb8`, pool length-9 in `0x3ff80`.

| sectors | role | evidence |
|---|---|---|
| base .. base+2 (3) | allocation bitmap, tag byte 0xA5 at page offset 0xFFF | `flash_page_tag_search(0,0xa5)`, `flash_tagged_record_bitlength_scan` @0x030104b0, `channel_bitmap_bit_clear` @0x03010558 |
| base+3 .. base+8 (6) | recording index (u16 sector ids). Tag 0xA5 / 0xA6 on the first sector of each half; 0x7F8 entries per half | `flash_read_autodial_entry` @0x030108a8 (`<0x7f9` -> tag 0xA5 else 0xA6, `param-0x7f8`), `channel_count_scan_flash_pages` @0x03010cf8 (count up to 0x7f8*2), `spi_flash_log_append_2byte` @0x030105d0 |
| base+9 .. end | pool, 4 KB sectors, each is either a recording header sector or a data sector | `flash_state_and_tag_dispatcher(0)` @0x03010bd4 returns the next free sector |

Recording = one **header sector** + N **data sectors** (HIGH):
- Header sector: bytes 0x00..0x11 = 0x12-byte header, bytes 0x12..0x13 = 16-bit duration written at stop; then a list of u16 data-sector ids at `(n+0x10)*2` (`mutex_guarded_param_dispatch_2` @0x03010680).
  Header read for UI is `flash_block_read(sector,0,buf,0x14)` in `recording_list_screen` @0x0306139c and `call_record_detail_render` @0x030617f0.
- Header layout (from the builder at 0x03021398..0x03021496, RX case): `[0]` flags (bit0 = direction, bit4 = from `0x3e1dc[8]&0xf0==0x10`), `[1]` high nibble = call type (1 private, 2 group, other = all-call; `call_record_detail_render` picks icon/contact type 3/4/5 from it),
  `[2..4]` = u24 LE of `*(0x3bd2c+8)` (src ID), `[5..7]` = u24 LE of `*(0x3bd2c+4)` (dst ID), `[8]` = `0x3e9b4[0x29]`, `[9]` = `0x3e9b4[0x26]` (channel/zone indices, MED), `[0xa]` = 0, `[0xb..0x11]` = date/time from `rtc_decode_calendar_date` (year split in two bytes). 
  Note `call_record_detail_render` prints the ID in `[5..7]`, which this builder fills with the DESTINATION for RX; manual says "source ID". Unresolved, LOW (does not matter for audio).
- Data sector: 27-byte frames appended back to back at offset 0, rolled to a new sector when the write offset exceeds 0xFD4 -> 151 frames per sector, **about 9.06 s of audio per data sector**. Last 2 bytes at 0xFFE hold the used length.
- Capacity: ~2331 pool sectors. A recording uses 1 header sector + ceil(dur/9 s) data sectors, so a 3 s call costs 2 sectors (8 KB), a 20 s call 4 sectors. Manual claims "10 hours"; by this arithmetic it is ~5.8 h of pure audio and less with many short calls. MED.
- Full: `flash_state_and_tag_dispatcher` -> when `last sector < returned sector` it stops any open recording and calls `flash_dual_range_invalidate` @0x03010b40 (erases ALL bitmap+index sectors base..base+8 and rewrites tags). **All recordings are dropped at once.** HIGH.
  UI strings "Record Full" (0x30bc9d4) and "Record Roomis full" (0x30bca58) exist (callers not traced).

Writes per recorded call (from the open path 0x03021348..0x030213a6 and 0x030214cc..0x03021528):
- call start: 2 sector erases (header sector, first data sector), 2 bitmap bit-clears, 1 index append, 1 link write, 1 header write (0x12 B).
- every 151 frames (9 s): 1 sector erase + bitmap + link write.
- call end: 2 small programs (duration in header, length at 0xFFE).
- plus 27-byte page programs every 60 ms.

### 1.4 List / playback / delete UI

`recording_list_dialog_screen` @0x0303c504 -> `recording_list_screen` @0x0306139c (draws "Record %d/%d"; index list via `flash_read_autodial_entry`). Delete-all = `flash_dual_range_invalidate`/`flash_pagesplit_write_with_split_markers` @0x030611d4 (same erase + re-tag sequence). Per-record delete not traced.

## 2. How playback works

Playback is `channel_send_dtmf_autodial_call` @**0x0306158c** (corpus name is WRONG; size 584). Signature `void f(u32 record_index_1based)`. HIGH that it is the Record playback (reads recording headers, shows "Record playback" dialog, uses `0x3ff84` playback cursor, calls the list-header reader and contact lookup). Callers: `menu_key_action_dispatch_by_screen_state` @0x0303a57c and `channel_scroll_with_wraparound` @0x030172e0 (Play / next / prev from the list).

What it does, in order:
1. Busy-waits `while (*(0x3e318+7) & 2) OSTimeDly_ms(10)`.
2. Reads the header (`flash_read_autodial_entry(idx)` -> sector, `flash_block_read(sector,0,hdr,0x14)`), shows ID/date dialog via `contact_name_lookup_or_format` + `dialog_show_icon_and_two_numbers`.
3. `FUN_00016000(1)` (`[0x48160+0x1494]=1`: "playback mode" flag read by the vocoder task) and `FUN_000160c0()` (sets `[0x1150]=1`, sets bit5 in SoC regs `0x11000380` and `0x11000080`), `WORK_MODE &= ~0x80`, **`audio_pa_enable_gate(1)`** (GPIO 0x35 = speaker amp).
4. Loop: per recording, per data sector: `flash_block_read(dataSector, off, buf, 0x1b)` then `FUN_000161a8(0, buf, 0x1b, 0x46)` (IRAM 0x161a8: copies the 27 bytes into the 2-slot playback ring **0x4a73c** and `OSSemPend`s with a 70 ms timeout as flow control); `off += 0x1b` until the sector's recorded length (`flash_page_trailing_count_read` = the 0xFFE value); next sector via `flash_page_indexed_2byte_read(hdrSector, n)` until 0xFFFF; stop flag byte at `*PTR_DAT_03061700` (set by the UI to abort).
5. Teardown: `FUN_00016000(0)`, restore WORK_MODE, `tone_or_volume_step_lookup`, `FUN_00016104` (clear bit5), `audio_pa_enable_gate(0)`.

The vocoder task (`FUN_0002f000`) with `[0x1494]==1` copies a frame from `0x4a73c + idx*27` into `0x46b38` instead of reading the modem (`0x2f002`: `0x2f06c(0x46b38, 0x4a73c + in_r3*0x1b, 0x1b)`) and runs the normal RX decode (software AMBE+2 decode -> PCM -> speaker). **No TX keying, no modem write, no TX permission check.** It is the same decode path as a live RX call. HIGH.

It runs in the caller's task (menu/UI task), blocking for the whole duration. A patch cannot call it from `rxvoice` without stalling RX handling for the length of the clip.

### 2.1 `flash_audio_clip_playback_tx` / `flash_audio_clip_sequence_playback` (NOT DMR)

- `flash_audio_clip_playback_tx(u8 clip, int first, int last)` @0x030222f0: reads clip table entry `clip*16` from data-chip block `*(u16*)0x38698`, 3-byte offset + base `0x30adb30`-pointed constant, checks a 4-byte magic (`0x30af1d8`), then streams the clip in **64-byte chunks to MMIO `0x16000980`** (pool 0x30223e4), spinning on `AUDIO_CONTROL` bit31 (FIFO full). `first` -> `audio_pa_enable_gate(1)`; `last` -> zero-pad the tail chunk. HIGH.
- Callers: `flash_audio_clip_sequence_playback` @0x03022400 (clip list of 1..n entries from a table, `p_dm32_settings_block04[0x30]&0x40` language offset +0x6e) and `tone_ptt_preamble_seq37` @0x03022750; all 38 `tone_ptt_preamble_seqNN` are tone/voice-prompt sequences ("tone_ptt_preamble" = the PTT-ID/5-tone/DTMF/prompt audio family).
- It is a PCM (analog) path into the audio FIFO next to the modem buffer (`0x16000960` vs `0x16000980`). It cannot carry AMBE and does not key TX. The "_tx" in the corpus name is a misnomer (it plays stuff that accompanies a PTT, e.g. roger/ID tones).

## 3. How a retransmission can be produced

### 3.1 TX keying chain (stock)

`PTT key (debounced, keypad_debounce_and_commit_event @0x03012eda..)` -> event flag group **0x3a4f0**, bit 0x8 (press) / 0x10 (release) -> `main_ptt_and_channel_event_loop` @0x03012d10 (task) -> `ptt_press_handler` @0x0305bfb8 (size 2214) ->
`tx_key_from_vfo_mode_guard(mode, target_id, flag)` @0x0305be64 -> `dialog_request_enqueue(request_id, target)` @0x0301eb70 (request 2 = group call, 1/0 = private, 3 = all call, 10 = plain) -> call setup state machine -> RF: `rf_tx_enable_sequence` IRAM 0x2b440 via task `FUN_0300dc94` (flag byte `0x38464`, sem `0x38590`). Release: flag 0x10 -> `call_teardown_and_history_dispatch`. MED (chain read from code, no run).

- **What is NOT the TX gate:** `audio_pa_enable_gate` @0x0301d6cc (an older RE note calls it `tx_enable_gate`/"THE PTT gate"). It toggles GPIO 0x35 and bit1 of `*(0x3fdc0... state word+1)`; every caller around it is audio/speaker (playback, tones). HIGH that it is the audio PA, MED-HIGH that the old doc is wrong. Do not call it for keying.
- Stock PTT after a received call already **replies to the caller**: in `ptt_press_handler` the branch `(call_active_flag&1 || ...)` selects by `p_tx_ptt_ui_state->field_0x14`: 1 -> `tx_key_from_vfo_mode_guard(3, field_0x10 = last source ID)`; 2 -> `(4, dialog_state_get_field_03020e28() = last destination)`; 3 -> `(5, ...)`.
  `tx_ptt_ui_state` is filled by the RX UI path (see last-heard-hooks.md 0). So the TG/ID, CC and timeslot for a retransmission are whatever the channel config + this reply logic give, i.e. the same TG as the received call for groups. MED.

### 3.2 The missing piece: voice payload source during TX

During a stock voice TX the vocoder task is in TX mode (`[0x1497]==1`), encodes mic audio, and **writes the 27-byte result to the modem with `0x172f8(0, 0x49b18, 0x1b)`** (call at IRAM 0x2eaf0; `0x172f8` = copy to MMIO `0x16000960`, the mirror of the RX read `0x172cc`). Then, when `[0x1496]`... the recorder tap `0x2e558` copies the same frame to ring 0x46b5c.
A parrot needs, per 60 ms TX frame, to substitute a stored 27-byte frame for `0x49b18` (or write stored bytes to `0x16000960` after the encoder). There is no stock code that does that. HIGH that this is the gap; MED on the exact insertion site (0x2eaf0 region in IRAM; persistent bytes at flash 0x030bfc20 + (addr-0x10000), e.g. 0x2eaf0 -> 0x030de710; IRAM code is patched in its flash copy, which is loaded into IRAM at boot).
Also required: stop the mic encoder from interfering (TX voice source bit `AUDIO_CONTROL` bit0 via `audio_ctrl_bit0_tx_voice_source_set`, `bitwise_not_of_nonzero_check()` branch in `audio_vocoder_buffer_flush2`), and TX framing (voice header/terminator, sync, embedded LC) comes from the stock call setup, so only the payload substitution is needed. MED-LOW: embedded-LC/EMB insertion by the modem was not traced.

## 4. Minimal parrot recipes

### Recipe P (recommended, zero extra flash writes): RAM capture + TX payload substitution

1. **Capture**: tap the stock RX tap. Hooking `0x2ebe0` alone is not enough because it only runs when the stock RX-record flag is set. Instead patch the IRAM vocoder task RX branch (right after `0x172cc(0,0x46b38,0x1b)` at IRAM 0x2ed.. / `func_0x000172cc(0,PTR_DAT_0002eed8,0x1b)`) to also copy `0x46b38` into a patch-owned RAM buffer (450 B/s; 30 s = 13.5 KB). Start it at call start (hook H1 0x030208ba or `audio_vocoder_buffer_flush_init` 0x0302093a), freeze it at call end.
   Requires: free RAM (see dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md)) and an IRAM patch (risk: the vocoder task is time-critical, 60 ms frame budget, ISR-fed).
2. **Trigger**: at the call-end hook (`bsr modem_control_state_stop` 0x03020984 in `rxvoice`), arm a timer; after N s idle (`rx busy` = `A[1]==0` at 0x3ba39 and no new flag 2), request TX. `rxvoice` must not block: set a flag and let a patch task / the PTT event path do the work.
3. **Key**: post PTT-press `OSFlagPost(0x3a4f0, 0x8)` (as the debounced key does) so `main_ptt_and_channel_event_loop` runs `ptt_press_handler` (its reply branch picks TG/ID from `tx_ptt_ui_state`), OR call `tx_key_from_vfo_mode_guard(4, dst_id, 0)` for a group call / `(3, src_id, 0)` for private. After the last frame, post flag 0x10 (release). MED-LOW: whether the hardware PTT poll will post 0x10 spuriously because the pin is not pressed (the release path is driven by the same debounced key, so a synthetic press may be immediately followed by a real "released" edge). Must be checked in `keypad_debounce_and_commit_event` handler tables (`keypad_key_handler_table[1]` press, `[0x1c]` release; not decoded).
4. **Feed**: in the vocoder TX branch, replace the `0x49b18` source with the captured frame each 60 ms until exhausted, then release PTT.

### Recipe S (reuse stock recording for storage): works for storage, still needs the new TX feeder

- Enable by RAM, not by the menu: set `0x3bd38[3] |= 1` (record switch mirror), `0x3bd38[2]` = 0 (Rx), `0x3ff84+0x2b |= 1`, `0x3fdc0`/`0x3fdb8` bit4 are radio state already. Do NOT call `settings_toggle_and_notify_0x45`.
- Stock then records every RX call at 0x0302093a, and finalizes at 0x03020980, so at the 0x03020984 hook the newest list entry is complete: index = `channel_count_scan_flash_pages()` (returns count), header sector = `flash_read_autodial_entry(count)`.
- Read frames back with `flash_read_autodial_entry` + `flash_block_read(dataSector,off,buf,0x1b)` + `flash_page_indexed_2byte_read` + `flash_page_trailing_count_read` (all task-level, take the data-chip mutex `p_ucos_mutex_0003944c`).
- Cost: stock flash writes for EVERY received call forever (section 6). Also you would have to delete the recording after echo (no per-record delete function found) or it fills the pool and triggers the erase-all (user's real recordings lost).

### Recipe L (listen-back only, trivial): after call end, call `channel_send_dtmf_autodial_call(count)` from a patch task. This plays the last call on the local speaker (stock Record playback), not on air. Useful for testing the recorder path, not a parrot. It blocks its caller for the clip duration and takes the UI dialog; do not call from `rxvoice`.

## 5. Callable-from-where, locks, and risks

| item | task / context | lock | note |
|---|---|---|---|
| `rxvoice_task_entry` call-end hook 0x03020984 | `rxvoice` (TCB 0x364a8) | none | must not block; set a flag only |
| `channel_count_scan_flash_pages`, `flash_read_autodial_entry`, `flash_block_read`, `flash_page_*` | any task, never ISR | take `p_ucos_mutex_0003944c` internally (via `os_sem_pend_ms`) | slow (SPI); do not call from `rxvoice` |
| `channel_send_dtmf_autodial_call(idx)` | UI/menu task today | sleeps/blocks for the clip | speaker only |
| `tx_key_from_vfo_mode_guard`, `ptt_press_handler` | `main_ptt_and_channel_event_loop` task | n/a | calling from another task races the UI (it also calls `format_and_write_status_field`, `ui_event_notify`); prefer posting 0x3a4f0 flags |
| IRAM vocoder task patches | vocoder task | none | hard real-time (OSTaskSemPend per frame), IRAM function bytes live in flash 0x030bfc20.. (sector 197/221 risk); confirm each literal hit by disassembling its function |

Risks specific to retransmission (none verified on hardware):
- **TX timeout / TOT**: a synthetic PTT counts against the channel TOT; stock TX-timeout code (`modem_timeout_stop_and_history_record_task`, `tx_timeout_cleanup`) will cut it. Keep echo length under TOT (also releases at TOT).
- **Channel busy / admit criteria**: stock `ptt_press_handler` honors `tx_is_inhibited()` and "busy lock" (`flags_0x7 & 0x18` blocks and shows a status string; `0x3e318`). A synthetic press goes through those checks, so it will refuse while the channel is busy or inhibited. That is the desired safe behavior; do not bypass it. Repeater (colour-code/ timeslot) admit criteria are inside the call-setup SM (not traced).
- **Colour code / timeslot / TG**: the stock reply path uses channel config for CC/TS and the last RX dst/src for target (section 3.1). If the received call was on the other TS or a different TG than the channel's TX contact, retransmission goes where `tx_ptt_ui_state` says. For a private call the "reply" is to the original caller (not an echo to everybody). Decide this explicitly.
- **TX permission**: licensing is the user's; the stock code only gates on `tx_is_inhibited`, band-edge checks (`frequency_band_range_check_and_status_set`) and the TX-forbid flags (`tx_forbid_flag_clear*`).
- **Re-trigger loop**: the retransmitted signal is itself a TX; the radio will not decode its own TX, but a second radio could. Add a hold-off after echo and ignore calls whose source ID equals own ID (`0x33cd0`, see last-heard-hooks 1.6).
- **Recording while echoing (recipe S)**: with Rx/Tx record the echo TX is recorded too (state 2). Use Rx-only (`0x3bd38[2]=0`).
- **Duplicates**: hook H1 may fire several times per call; H2 may fire without a start (see last-heard-hooks.md).

## 6. Data-chip write concerns

- Recording region 0x6DC000..0xFFFFFF is the only place stock recording writes. Stock recording writes (per call) are listed in 1.3: 2+ sector erases, ~10 small programs, and 27 B every 60 ms. At ~9 s per data sector plus 1 header sector per call, **a parrot built on stock recording erases ~2 sectors for every received call** (even a 1 s kerchunk). Over a busy repeater-like day that is hundreds of erases; sectors are rated ~100k cycles and allocation is sequential from the low end until the pool wraps via erase-all, so wear is spread but real.
- Recording ON via the menu writes settings block 04 (offset 0x45) every toggle (`flash_field_write_cached`). A patch must not toggle it.
- The task `channel_activity_log_flush_task` also fires when the user has Record on; a patch that sets the RAM mirrors forces recording of all calls until reboot, including Tx.
- Therefore: Recipe P (RAM buffer, no data-chip writes at all) satisfies "no new data-chip writes beyond what stock recording already does" trivially (it adds none). Recipe S only satisfies it if the user already has stock Record enabled; otherwise it ADDS writes for every call.
- The patch itself must also never call `flash_dual_range_invalidate`/`flash_pagesplit_write_with_split_markers` or per-record deletion paths.

## 7. Corpus names that are wrong in this area (use the addresses)

| corpus name | actually | evidence |
|---|---|---|
| `channel_activity_log_flush_task` 0x030212f4 | voice recorder task | section 1.1 |
| `scan_toggle_persist_and_notify` 0x03015bb0 | Record Switch toggle | UI events 0x18/0x19, `settings_toggle_and_notify_0x45` |
| `gps_state_flush_mode1/2` 0x0306116c/0x03061110 | stop RX / TX recording | state 5 + header finalize |
| `tx_gate_state_transition` 0x03021a14 | RX voice end (stop RX record + vocoder reset) | calls `gps_state_flush_mode1` |
| `tx_voice_source_teardown` 0x03021850 | TX voice end (stop TX record) | calls `gps_state_flush_mode2` |
| `audio_vocoder_buffer_flush_init` 0x03021948 | RX voice start (starts RX record) | section 1.2 |
| `audio_vocoder_buffer_flush2` 0x03021a70 | TX voice start (starts TX record) | section 1.2 |
| `channel_send_dtmf_autodial_call` 0x0306158c | Record playback (speaker) | section 2 |
| `flash_read_autodial_entry` 0x030108a8 | recording index lookup (n -> header sector) | 1.3 |
| `channel_count_scan_flash_pages` 0x03010cf8 | recording count | 1.3 |
| `flash_audio_clip_playback_tx` 0x030222f0 | PCM clip player, not DMR | 2.1 |
| `audio_pa_enable_gate` 0x0301d6cc (old doc: `tx_enable_gate`) | speaker PA GPIO 0x35, not the TX gate | 3.1 |

## 8. Open questions

1. Exact insertion point and mechanism in the IRAM vocoder TX path to substitute payloads, and whether the modem inserts embedded LC / EMB itself (LOW-MED). First thing to verify before any code: disassemble IRAM 0x2e8xx..0x2eb30 (TX branch of `FUN_0002f000`) and `0x2e1d4` (TX tick).
2. Does a synthetic PTT-press flag (0x3a4f0 bit 0x8) hold TX until a synthetic release, or does the key-poll task post a release because the pin reads "up"? (`keypad_debounce_and_commit_event`, handler table entries [1] and [0x1c].)
3. Meaning of `0x3fdb8 bit4` and `*(0x3e318+0x11) bit4` (recorder gate) and whether recording is suppressed during something relevant to the hook time.
4. Header `[5..7]` vs `[2..4]` source/destination assignment and the unit of the 16-bit duration (appears to be 100 ms ticks from `call_record_detail_render`).
5. Which source increments `0x3e120` (duration counter) and per-record delete (not found; may not exist).
6. Free RAM for a 13+ KB echo buffer; check dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md) and [flash-allocation.md](flash-allocation.md) before choosing a size.
7. Behavior at pool-full ("Record Full") and whether recording stops or erases-all in the RX path (`flash_state_and_tag_dispatcher` erase-all path read; UI string callers not traced).
8. Nothing here was exercised in QEMU (baseband is not simulated) or on hardware.

## 9. Key addresses

| what | address |
|---|---|
| RX call start -> start RX record | `bsr audio_vocoder_buffer_flush_init` 0x0302093a (in `rxvoice_task_entry` 0x03020778) |
| RX call end -> stop RX record | `bsr tx_gate_state_transition` 0x03020980; H2 hook 0x03020984 |
| recorder task | 0x030212f4 (RAM state byte 0x0003bd39, file ctx 0x0003ff84, header scratch sp+0x10) |
| record cfg RAM | 0x0003bd38 (+2 sel, +3 switch bit0), 0x0003ff84+0x2b (bit0 switch, bit2 file open) |
| rings / sems / flags (IRAM data) | RX ring 0x48128 (idx 0x48124, sem 0x45648), TX ring 0x46b5c (idx 0x49b38, sem 0x45670), playback ring 0x4a73c, flags `0x48160+0x19d4/0x1494/0x1495` `0x456c0+0x1496/0x1497/0x1498` |
| modem voice window | MMIO 0x16000960 (read `0x172cc`, write `0x172f8`), audio PCM FIFO 0x16000980 |
| playback | 0x0306158c; list 0x0306139c; detail 0x030617f0 |
| data-chip pool | 0x6DC000..0xFFFFFF |
| PTT chain | flags 0x3a4f0 -> 0x03012d10 -> 0x0305bfb8 -> 0x0305be64 -> 0x0301eb70 |
