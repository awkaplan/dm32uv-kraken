# Persisting one new 16-byte field inside stock Radio Settings (logical block 0x04)

Status: static reverse engineering only (DM32.00.01.048). Nothing was run on a radio or in QEMU; no
radio was touched. Corpus: [dm32uv-re](https://github.com/awkaplan/dm32uv-re). Pool-slot values were read from
the image, not from the decompiler's guesses. The scratch scripts were not kept.
Tags: [CONF] two independent sources agree, [STRUCT] read from code, [INFER] inference, [OPEN] undetermined.

## 0. Answer in ten lines

1. There is NO RAM image of block 0x04. `p_dm32_settings_block04` is a decoded, re-packed runtime state struct
   at RAM `0x0003e9b4` (pool slots hold the value `0x0003e9b4`, e.g. `*(u32*)0x0300d9b8 = 0x3e9b4`) [CONF].
   Nothing bulk-loads block 0x04 into it. Its offsets do not match flash offsets (see 1.2).
2. Stock reads block 0x04 field-wise, on demand, straight from the data chip with
   `flash_field_read_cached(blk=4, off, dst, len)` @0x0300f780 / `flash_block_read` @0x0300f71c [CONF].
3. Stock persists a change with a read-modify-write of the whole 4 KB SECTOR on the chip (old sector -> spare
   sector, 32 pages of 128 B, patch bytes spliced in, new sector tagged), NOT from RAM. Editing the mirror persists nothing [CONF].
4. The callable commit: `flash_block_write(4, off, src, 16)` @0x0300f878 (`void(u8 blk,u16 off,void*src,u16 len)`),
   task context, takes its own mutex + sched lock. Direct stock precedents write 2/3/4 bytes to block 4 the same way [CONF].
5. Cost: one sector erase + 32 page programs per call (scheduler locked). Same wear profile as any stock settings toggle;
   write only on user confirm [CONF].
6. Best range: block 0x04 offsets `0xFE0-0xFEF` (inclusive). Next: `0xFD0-0xFDF`, `0xFC0-0xFCF`, ... down to `0xF10`.
   Confidence about 85-90% that stock never touches them [STRUCT + CONF on captures].
7. `0xF00-0xF03`, `0x700-0x704`, `0x439-0x44A`, `0x4F9-0x5FF`, `0x50-0x5F` ARE used by stock although the spec lists them
   unmapped (section 3.2). Do not use them.
8. Vendor CPS "W" writes the whole 4096-byte sector raw (no merge) -> it overwrites the field with 0xFF (OEM file) [CONF].
9. Stock "factory reset" (`codeplug_factory_defaults_write` @0x03032434) rebuilds block 0x04 from a zeroed buffer -> field
   reads 0x00 afterwards. Treat all-00 and all-FF as "unset" [CONF].
10. Open: indirect/variable-offset accessors could not be proven absent for 0xF10-0xFEF by pure static means (3.4).

## 1. How block 0x04 reaches RAM

### 1.1 The "mirror" address and size

- `p_dm32_settings_block04` @0x0300d9b8 is a literal-pool slot whose VALUE is `0x0003e9b4` (same value in 1237 `lrw`
  sites across the corpus; verified by reading the pool words from `progmem_stock_DP570UV_20260926_FULL.bin`). [CONF]
- That RAM object is accessed at offsets 0x00..0x377 (111 distinct offsets, brute-forced over `meta/disasm.jsonl`
  with base-register tracking; the list was a scratch file and was not kept). It is a general runtime-state struct,
  not a settings image: other literal pools point INSIDE it at +0x7c, +0x94.. and `flash_block_read` is used with
  destinations `0x3e9b5`/`0x3e9c8` (`contact_record_copy_with_limit` @0x0304a130) and `+0x350`
  (`channel_record_commit` @0x0301765c), so it overlays contact/record scratch buffers. [STRUCT]
- IRAM (`data/firmware/IRAM_dm32.00.01.048.bin`, objdump-disassembled): the only IRAM reference to the base is one literal at
  0x2df64 used by three tiny helpers (0x2deba/0x2df14/0x2df40) that read byte `+0x2d`. IRAM has no `bsr` to
  `spi_flash_read_seq` @0x2b99c and no literal for any of `flash_field_*`/`flash_block_*`/`flash_block_id_resolve*`. [CONF]

### 1.2 Proof that mirror offsets are not flash offsets

| Stock function | Mirror write | Flash write |
|---|---|---|
| `menu_wrapper_callsign_colour` @0x0304fd00 | `mirror[0x62]` | `flash_field_write_cached(4,0x34)` |
| `menu_wrapper_field0x32` @0x0304fe20 | `mirror[0x59]` | `flash_field_write_cached(4,0x32)` |
| `menu_wrapper_field0x80_readback` @0x0304fd84 | `mirror[0x6c]` | `flash_field_write_cached(4,0x80)` |
| `menu_wrapper_field0xa4_toggle` @0x0304ff9e | `mirror[0x6d]` bit 2 | `flash_field_write_cached(4,0xa4)` |

(Also: the code reads language from `mirror[0x30]` bit 6 while flash 0x030 is backlight brightness.)

### 1.3 How block 0x04 is actually consumed at boot

No bulk load. `radio_boot_full_init_sequence` @0x0305f2c8 calls small loaders that each do 1-byte `flash_block_read`/`flash_field_read_cached`:
`FUN_0304af40` (0x60-0x64), `apply_flags_from_flash_0x65_0x66` @0x0305d544 (0x65,0x66), `rf_config_load_from_flash_block2`
@0x0300d568 (0xa5,0xa6), `FUN_0305dbb0` (0xF00), and last `settings_block_cache_load_from_flash` @0x03061e8c, which reads the APRS
fields 0x301,0x302,0x306(9),0x310(9),0x30f,0x319,0x331(4) into a RAM cache struct at `0x00040078`
(pool slot @0x03062058 -> 0x40078; `flash_block4_write_field_0x332_and_cache` @0x0304ce50 updates the same cache). [CONF]
That function is the natural place for a patch to read its own field (neighbouring APRS data, runs after the data chip is initialised).

## 2. How stock persists a change

### 2.1 Mechanism

`settings_write_dispatcher` @0x0304b088 (opcode, value) updates the mirror and then, per opcode, either
`flash_field_write_cached(4, off, byte)` @0x0300fa28 (1 byte) or `flash_block_write(4, off, src, len)` @0x0300f878. Both:

1. raise the sched-lock counter at `0x0004250d` (cpu_sr save/restore around it), pend `p_ucos_mutex_0003944c` (timeout 0 = forever);
2. `flash_block_id_resolve(4)` -> physical sector holding logical block 4 (found via the tag byte at offset 0xFFF);
   `flash_block_id_resolve_helper1/2` pick/erase a spare sector; temp tag 0x7f written to the new one;
3. loop 32 pages: `spi_flash_read_seq(old, page*128, buf, 128)`, splice the new byte(s), `spi_flash_program_pagesplit(new, ...)`;
   on page 31 buf[0x7f] (= offset 0xFFF) is forced to the logical id;
4. `flash_page_tag_write(new, 4)` then `flash_page_tag_write(old, 0)`, `flash_page_invalidate_range`, post mutex, drop sched lock.

So it IS a read-modify-write of the whole 4 KB, but sourced from the OLD FLASH SECTOR, not from RAM. Power loss mid-way leaves
the old sector valid (tag still 4). A new field persists only if the patch itself calls the write function. [CONF]

### 2.2 Recipe (C-SKY ABIv2, plain C call, r0-r3 args, no shim)

```c
#define flash_block_write      ((void (*)(u8 blk, u16 off, const void *src, u16 len))0x0300f878)
#define flash_field_read_cached ((void (*)(u8 blk, u16 off, void *dst, u16 len))0x0300f780)
/* commit: */  flash_block_write(4, 0xFE0, buf16, 16);
/* load:   */  flash_field_read_cached(4, 0xFE0, buf16, 16);
```

- `blk` is the LOGICAL id (4), not a resolved sector; the function resolves it itself. (`flash_block_read` is the opposite: it
  takes a RESOLVED id from `flash_block_id_resolve_locked(4)` @0x0300f594.) [CONF]
- Stock precedents with the identical call shape: `flash_block4_write_offset0x700_u16` @0x03065904 (2 B), `bcd_pack_and_write_flash_block4_0x702`
  @0x03065918 (3 B), `flash_block4_write_field_0x332_and_cache` @0x0304ce50 (3 B), `settings_write_0xf00_wrapper` @0x0305dbfc (4 B),
  `settings_write_0x50_wrapper` @0x03060914 (variable len, user text), and 16-byte writes to other blocks
  (`channel_record_commit` @0x0301765c: `flash_block_write(blk,off,p_text_entry_buffer,0x10)`). [CONF]
- Task context only (os_sem_pend, sched lock): call from the menu/UI task like the stock menu wrappers do; never from an ISR, the PTT/TX/RX
  hot path, or while already holding `p_ucos_mutex_0003944c`. Frame: ~0xB0 bytes of stack (0x94 locals + 9 saved regs); the stock callers are
  menu tasks, so a menu-task caller is safe. [STRUCT]
- Latency: sector erase (tens of ms typical) + 32 page programs, scheduler locked the whole time. Acceptable for a user "Save", not periodic. [INFER]
- `flash_field_write_cached` writes ONE byte per call = one full erase/relocate per byte: never loop it for 16 bytes; use `flash_block_write`.

### 2.3 Wear

One sector erase (plus possibly one erase of the spare in `helper1`) per call; the target rotates through spare sectors of the
logical-block pool (about 200 metadata pages per spec 04), so wear is levelled. Same cost as one stock settings toggle. Concern only if a
patch writes repeatedly (e.g. on every beacon). Write once on user confirm; skip the write if bytes are unchanged. [STRUCT/INFER]

## 3. Candidate 16-byte ranges

### 3.1 Evidence base

- Static: every call to `flash_field_read/write_cached`, `flash_block_read/write`, `flash_block_id_resolve(_locked)` in `meta/disasm.jsonl`
  with a constant-folded block id and offset (scripts `scan3.py`, `scan4.py`), cross-checked against the decompiled C
  (`grep 'flash_field_*_cached(4,'` = 140+ sites), plus all 11 functions using `resolve_locked(4)`. IRAM scanned (1.1).
- Captures: block 0x04 was re-extracted from both captures in the public [DM32-Protocol-Spec](https://github.com/infamy/DM32-Protocol-Spec)
  (`serial_capture_example.txt` read #581, 4102 B reply; `serial_capture_write_example.txt` write #450). 134 offsets hold data other than 00/FF; ALL of them fall inside the accessed set below. [CONF]
- Firmware's own default writer: `codeplug_factory_defaults_write` @0x03032434 (block-4 part at corpus lines 437-524) memsets a 4 KB buffer
  to 0, sets 0xFFF=4 and only: 0x00-0x1D names/flags, 0x1D,0x20,0x21,0x30-0x33,0x40-0x42,0x60-0x65,0x80,0x81,0xA3,0xA5-0xA7,
  0x306-0x319 (lat/long text), 0x332, 0x500-0x507 (FF), 0x601,0x602 (=10), 0x700 (=0x0F), then writes it. Everything else is 0. [CONF]

### 3.2 Offsets stock really uses that the spec calls "unmapped" (do not use)

| Offsets | Evidence |
|---|---|
| 0x050.. (len = typed text) | `settings_write_0x50_wrapper` @0x03060914 called from `settings_save_confirm_dialog` @0x0304e416: `flash_block_write(4,0x50,p_text_entry_buffer,len)`. Spec says 0x046-0x05F empty. |
| 0x120-0x12F | `flash_stored_setting_load_and_dispatch` @0x03064f54 reads `(idx+0x8f)*2` |
| 0x320-0x32F | `(idx+400)*2`, idx<8 (`channel_change_apply_dispatcher` @0x030573e8) |
| 0x439-0x44A | `cps_serial_handshake_command_dispatcher` @0x0302b3d4 (0x439, 2 B); `serial_password_command_handler` @0x0302b2b8 (0x43b/0x443, 8 B each: radio password) |
| 0x4F9-0x5FF | `FUN_0301163c` @0x0301163c: 7-byte records at `n*0x10+0x4f9`, n=1..16 |
| 0x500-0x504 | menu enable bits (spec) |
| 0x700-0x704 | `flash_block4_write_offset0x700_u16` + `bcd_pack_and_write_flash_block4_0x702` (FM radio frequency, `rda5807m`) |
| 0xF00-0xF03 | `settings_write_0xf00_wrapper` (4 B write); reads at 0xF00 (`FUN_0305dbb0`), 0xF01, 0xF02 (`channel_change_apply_with_zone_and_flash_validate` @0x03058180) |
| 0x601/0x602 | written by factory defaults (=10); 0x600 written by CPS (spec) |

Array readers with caller-bounded index: Fun+ `0x230+7*k` (k = key digit, max ever 0x929 even for k=255, still below 0xE00),
`0x200+5*k` (k<5 checked). No stock code reaches 0xF04-0xFFE of block 4.

### 3.3 Ranked candidates (16-aligned, free of every accessor above, 00 in factory read, FF in OEM write, unmapped in spec)

Free spans (16-byte units): 0x70-0x7F, 0xB0-0x10F, 0x130-0x1FF, 0x220-0x22F, 0x280-0x2FF, 0x340-0x42F, 0x450-0x4EF, 0x610-0x6FF,
0x710-0xEFF, **0xF10-0xFEF**. In all of them both captures are uniformly 00 / FF (checked byte for byte).

| Rank | Range (inclusive) | Why | Confidence |
|---|---|---|---|
| 1 | `0xFE0-0xFEF` | End of the saved region; last full 16 B before 0xFF0-0xFFE and the id tag 0xFFF (stock rewrites 0xFFF itself on every save, so no extra interaction). Never named in any accessor, any disassembly constant, either capture or the default writer. Sits in page 31 (0xF80-0xFFF); `flash_block_write` handles the page-31 tag explicitly and its splice loop precedes the tag overwrite. | 85-90% |
| 2 | `0xFD0-0xFDF` (then `0xFC0`, `0xFB0` ... `0xF10`) | Same span, one unit further from the tag/end. Use if 0xFE0 is later found to collide. | 85% |
| 3 | `0xA00-0xA0F` (anywhere in 0x710-0xEFF) | Deep interior, far from every known user; page-aligned (0xA00 = page 20), so it never shares a page with the tag. The only reason to rank it below 1 is "near the end" preference. The unmapped-interior block 0x06 uses 0xA20 for its own fields, but that is a different block. | 85% |
| 4 | `0x340-0x34F` / `0x450-0x45F` | Low area adjacent to dense used regions (0x334, 0x44A): higher risk of off-by-N overruns by neighbouring stock writers (`settings_write_0x50_wrapper` has a caller-controlled length). Not recommended. | 60% |

Not usable: 0xFF0-0xFFE (only 15 bytes, and 0xFFF is the sector id tag; any write there that changes 0xFFF breaks logical-block lookup).

### 3.4 What I could not prove (why not 100%)

- Constant-folding covers calls whose block id / offset are `movi`/`lrw`/`lsli` derived. Calls through computed offsets were reviewed by
  hand: `(idx+0x8f)*2`, `(idx+400)*2`, `0x230+7k`, `0x200+5k`, `n*16+0x4f9` (all bounded below 0xE00).
- The CPS 'R'/'W' path (PROGRAM mode) addresses sectors physically and can read/write any byte; irrelevant to firmware semantics.
- No code path was found that checksums or blank-checks the whole of block 4. `spi_flash_config_block_magic_scan` only reads tag bytes at 0xFFF.
- An OEM firmware update (different version) could repurpose the range; this analysis is for .048 only.
- A third data point (a CPS read of a used radio, not done here because it is per-unit data) would
  confirm the range is 00/FF on a used radio too.

## 4. Vendor CPS write behaviour

- Serial 'W' (0x57) with a 24-bit physical address and 4096-byte payload is handled in `serial_protocol_vframe_command_loop_variant` @0x0302a4dc:
  `if ((addr & 0xfff)==0) flash_mutex_guarded_op(addr>>12); spi_flash_write_multi_sector(addr, buf, len)`. It erases the target sector and writes the host's
  bytes verbatim, including the 0xFFF tag; no merge with existing contents. The firmware then resets (`flash_quad_enable_and_system_reset`). [CONF]
- OEM CPS write capture: block 0x04 bytes 0xF10-0xFFE are all 0xFF (re-extracted). So a CPS write replaces the field with FF*16.
  A third-party writer that follows the spec's "preserve unknown bytes" rule keeps it. Acceptable per the requirement: validate magic+checksum on
  load, treat all-00/all-FF/invalid as "unset", ask the user to re-enter. [CONF]
- Factory reset (`codeplug_factory_defaults_write`, callers `FUN_03014a54`, `FUN_030156f4`) also clears it to 00. Same handling. [CONF]

## 5. Open questions

- Is 0xFE0 or 0xFD0 preferable if a future vendor CPS version starts writing a field at the very end? (Unknowable; both 00/FF on the factory and OEM images.)
- Is there extra stack headroom concern calling `flash_block_write` (~0xB0 B frame + callees) from the patch's own task? The menu task is the safe place.
- Which task owns `p_ucos_mutex_0003944c` contention while recording/audio runs? Not analysed; stock settings saves have the same exposure.

## 6. Records of ours in block 4

Records grow downwards from 0xFE0 inside the free span 0xF10-0xFEF (3.3). Each is claimed in `allocations.json`
(`datachip_records`), carries a magic and a checksum, and is written through `flash_block_write(4, off, rec, len)` only on the
user's OK and only when its bytes change. All-00 (factory reset) and all-FF (vendor CPS write) fail the check and read as
"not set".

| Offsets | Size | Name | Owner | Layout |
|---|---|---|---|---|
| 0xFE0-0xFEF | 16 | `aprs_callsign` | `krk_callsign.c` | "CALL", callsign, SSID, version, APRS path, CW ID byte, checksum (seed 0x5a) |
| 0xFD0-0xFDF | 16 | `freq_unlock` | `krk_band.c` | [freq-unlock.md](freq-unlock.md) |
| 0xFC0-0xFCF | 16 | `burst_voice_cfg` | `krk_bvcfg.c` | [burst-voice.md](burst-voice.md) 13 |
| 0xF90-0xFBF | 48 | `aprs_beacon_text` | `krk_aprs_trec.c` (F8a, 2026-11-11) | below |

`aprs_beacon_text` (the analog APRS beacon comment, [aprs-submenu.md](aprs-submenu.md) 7.6): bytes 0-3 magic `BXT1`
(0x31545842 little-endian), bytes 4-46 the text (at most 43 ASCII characters from `A-Z a-z 0-9 space . - / , : + ( )`,
zero-padded), byte 47 the checksum: rotate-left-by-one then xor over bytes 0-46, seed 0x69 (the 16-byte records' rule over 47
bytes). An empty text and a record that fails the check both mean the default comment. 48 bytes at 0xF90 stay inside one 256-byte
read unit of `flash_block_read` (0xF00-0xFFF) and one 128-byte page (0xF80-0xFFF) of the write. Read on every beacon (no RAM copy):
`flash_block_read` takes the same mutex as the write, and a torn read fails the checksum and sends the default. 0xF10-0xF8F
stays free.
