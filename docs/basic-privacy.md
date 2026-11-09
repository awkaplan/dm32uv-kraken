# F12 16-bit basic privacy (HR_C7000 SCRAMBLE_REG): research and patch design

Status: sections 1-5 are static reverse engineering and design, written before anything was built.
The build is described in "Implementation" at the end.
Evidence tags: HIGH = read directly from the datasheet text or the image; MED = inference from several
facts; LOW = guess or not verifiable without hardware.

Sources: HR_C7000 datasheet v2.7 EN (§10.2.2.11-13 around lines 20215-20285, Appendix D §17 around lines 25255-25420
of a Markdown conversion), the stock image `data/firmware/DM32.00.01.048.bin` (base `0x03000000`) and `meta/disasm.jsonl`
of [dm32uv-re](https://github.com/awkaplan/dm32uv-re), and dm32uv-re's
[docs/encryption.md](https://github.com/awkaplan/dm32uv-re/blob/main/docs/encryption.md).
Accessors of 0x1100038c were found by an instruction-level scan of the whole disassembly.

## 1. Register semantics

### 1.1 SCRAMBLE_REG at 0x1100038c (HIGH)

Datasheet §10.2.2.13 (reset default 0 for both fields, both RW):

| Bits | Name | Meaning |
|---|---|---|
| [31:16] | `dscramble_reg` | "Simple voice encryption method, receiving end decryption register Configuration" |
| [15:0]  | `scramble_reg`  | "Simple voice encryption method, encryption register at the sending end Configuration" |

So the TX key and the RX key are two separate 16-bit fields in one 32-bit register. For two radios to talk,
write the same key `K` to both: `value = (K << 16) | K`.

OPEN (LOW): the datasheet never says whether the RX field must equal the TX field. Table 72 says only
"Configure transmit and receive key register values", and Figure 123 draws an "encryption key register" on
the TX side and a "decryption key register" on the RX side. It may be that `dscramble` is the inverse or
a derived value of `scramble`. The scrambler algorithm itself is not defined anywhere in the datasheet.
`(K << 16) | K` is the obvious first guess; it needs a bench test (section 5).

### 1.2 Enable bit: there is none (HIGH for the text, MED for the consequence)

`LOCAL_CC` at 0x11000384 (§10.2.2.11): bit [16] `encryption_type`, 0 = "normal encryption", 1 = "enhanced
encryption"; bits [9:8] `cc_opt`; bits [3:0] `cc`. Appendix D §17.2.1 "Static basic encryption" (Table 72)
lists exactly two things for basic mode: `LOCAL_CC` = 0x1 ("Encryption mode selection") and `SCRAMBLE_REG` =
the key. `LOCAL_CC = 0x1` is bit 16 = 0 plus CC = 1, so basic mode is simply "bit 16 clear". Enhanced mode
(§17.2.2, Table 73) is `LOCAL_CC = 0x10001`, which also needs `VOICE_EMB_CTRL`, a PI frame and 216-bit keys
at 0x16000495/0x16000945.

Consequence (MED): there is no separate on/off bit. The scrambler is "on" whenever `encryption_type` = 0 and
the key is non-zero. Why key 0 must mean "off" (MED): stock writes 0 at boot and stock radios interoperate
with ordinary DMR radios, so key 0 cannot be a non-identity transform. This is an inference, not a datasheet
statement; the earlier audit flagged the same gap. We never touch `LOCAL_CC` (stock never sets bit 16
either, see 2.2), so we need only one register write.

### 1.3 TX, RX, and what is covered

- TX and RX both (HIGH): Figure 123 shows `vocoder -> voice encryption -> framing -> [air] -> deframing ->
  voice decryption -> vocoder`, driven by the "encryption key register" on TX and the "decryption key
  register" on RX.
- Voice payload only (HIGH): §17.1 says "Layer 2 voice and data frame headers, data feedback packets, and link
  control data are not encrypted", and that source/destination IDs, remote kill, call notification and
  embedded RC signalling stay in the clear. So the voice LC header, embedded LC, EMB, sync and the CC
  stay readable; the call is still visible on the air with its IDs. The modem sends no PI header in
  this mode (stock sets `VOICE_EMB_CTRL` = 0, so `pi_bit` = 0); a stock radio will not know the call is
  scrambled and will just play garbled audio.
- Data (LOW): §17.1 says "digital encryption only protects voice and data traffic" and that the function
  applies to all digital modes, but the register is named "simple *voice* encryption". Whether the
  scrambler also mangles DMR data (SMS, stock DMR-APRS) is not stated. Assume it may, and say so on the screen
  (not tested).
- Digital only (HIGH): it is a DMR framer function; it does nothing for analog FM.

### 1.4 When it must be written (LOW/MED)

The datasheet is silent on timing. It is a plain RW register in the link-layer block (the same block as `LOCAL_CC`, which stock rewrites freely
at every channel change). Safest rule: change it only while idle (no RX call, no TX), because a mid-call change would
desynchronise a stateful scrambler if there is one. Stock does not need to write it before call start, because the datasheet describes it as
a static configuration.

## 2. Every stock access (HIGH)

### 2.1 Method

The image has no literal-pool word for any `0x1100038c`-area address: a raw scan of the flash image
for the words 0x11000380..0x11000390 found none (`0x11000000` appears once, at file offset 0xaf22d, an
unaligned data hit). That agrees with the earlier audit: MMIO is reached as `movi rX,0x88; lsli rX,rX,0x15`
(or `movih rX,0x1100; bseti rX,10` for the 0x11000400 page) plus an offset. I ran a base-tracking scan over
the whole `meta/disasm.jsonl` (all 3561 functions, including the IRAM load image, which is covered at
its flash-view addresses `0x030bfc20..0x030decb8`). It tracks `movi`/`movih`/`lsli`/`bseti` and reports every
load or store whose resolved address lies in 0x11000300..0x110003ff.

### 2.2 Result

| Register | Writers | Notes |
|---|---|---|
| `SCRAMBLE_REG` 0x1100038c | exactly one: `hr_c7000_modem_core_register_init` @0x03023608, `st.w r3,(r4,0x38c)` at **0x030236f2** with r3 = 0 | boot-time, runs once from `radio_boot_full_init_sequence` @0x0305f2c8. No reader anywhere. |
| `VOICE_EMB_CTRL` 0x11000388 | same function, `st.w r3,(r4,0x388)` at 0x030236ee, value 0 | |
| `LOCAL_CC` 0x11000384 | `mmio_11000384_low_byte_write` @0x0301e864 (mask `andni 0xf`, cc[3:0]); `sysctl_0x384_bit8_toggle_with_companion_byte` @0x0301ece8 and `modem_rf_mode_transition_apply` @0x0301e628 (clear bits 8 and 9) | none touches bit 16 |

False positives found by offset-only matching and rejected (base is an SRAM struct loaded with
`lrw rX,0x3c25c`, not MMIO; same finding as the earlier audit): `config_struct_reset_only_no_unpack`
@0x0302534c, `serial_9bit_frame_handler_with_crc_validate` @0x03025514 (the `0x384/0x38c` accesses),
`config_struct_field_write_dispatch` @0x03024f10.

Other readers of the 0x390/0x394/0x39c neighbours (`RX_TYPE_INFO`, `RX_INFO`, `SYS_INTERP_MASK`) exist in the
call-setup/RX paths but are not related to the scrambler.

Conclusions:
- Stock writes `SCRAMBLE_REG` once, to 0, at boot, and never again (HIGH, instruction-level). Stock does NOT rewrite it at channel
  change or call start, so a value we set stays set. We must only write after the boot write, which is
  always true for any hook running in a task or a menu.
- The stock ARC4/AES path does not use `SCRAMBLE_REG` or `LOCAL_CC` bit 16 (HIGH): it is software
  (keystream in IRAM `0x15bd4` / `0x15728`, applied to the voice frame buffers); dm32uv-re `docs/encryption.md` section 2.
  Stock and our scrambler are therefore independent, and enabling both on one channel would just double-process the voice.
  Stock CPS key type 1 ("Custom") is not understood; dm32uv-re `docs/encryption.md` guesses it might feed a hardware
  scrambler via IRAM tables 0x46aa4/0x49a88 but no write to `0x1100038c` exists, so it does not.
- Possible reset (LOW, mitigated): `SYS_SOFT_RSTN` (0x11000000, bits 0-1 = protocol/PHY) is written at radio
  mode changes (QEMU coverage plan, section 12). If a protocol-layer reset returns `SCRAMBLE_REG` to its default 0,
  an analog/digital switch would silently clear our key. The patch therefore re-applies the key every second
  (3.3) rather than once.

### 2.3 Stock state to check before applying (MED)

`dmr_privacy_key_load_and_mi_draw` @0x0305ca48 (called from `channel_change_apply_dispatcher`) reads the loaded
channel struct (pool `0x305cbd8` = `0x0003e1dc`, checked from the image) and stores the CPS key type, or 0 when no
stock encryption is configured, into the byte `0x00033824` (pool `0x305cbdc`):

- stock encryption is configured when `[0x3e1dc+0xe] != 0` and `[0x3e1dc+0xf] & 0x40` and the channel is digital
  (`([0x3e1dc+8] & 0xf0) == 0x10`);
- else `0x33824` = 0.

So our gate: apply the key only when the loaded channel is digital (`(*(u8*)0x3e1e4 & 0xf0) == 0x10`, the
`VFO_FLAGS8` already used in `krk_core.c`) and `*(u8*)0x00033824 == 0` (no stock cipher on this channel) and the
user has the scrambler On. Otherwise write 0.

## 3. Patch design

### 3.1 State (RAM, P4)

RAM-only, validated by a magic word, power-up = Off (checklist P4; same as `krk_txtrim.c`):

```c
#define SCR_MAGIC 0x53435231u                /* "SCR1": SRAM holds power-on noise */
static struct { u32 magic; u16 key; u8 on; u8 pad; } S;   /* in the ext blob's .bss (kraken_ui2_state) */
```

A wrong or missing magic means Off, key 0. Persistence is deliberately not part of v1: an invisible
persistent scrambler that survives a power cycle would make the radio unintelligible to stock radios with no
hint, so Off at every boot is the honest default. If persistence is wanted later, the next free 16-byte
range in Radio Settings block 4 after the callsign (`0xFE0-0xFEF`) is `0xFD0-0xFDF` (`docs/settings-field.md`,
section 0 item 6, about 85-90 percent confidence that stock never touches it), using the same
`flash_block_write` path as `krk_callsign.c`. I did not verify 0xFD0-0xFDF beyond what that document says.

Per-channel keys are out of scope for v1 (needs persistence). The key is per radio and applies to every digital
channel without a stock cipher.

### 3.2 Screen "Scrambler (weak)" (extension blob, slot 3 of `krk_ext_screens`)

Slots 3, 4, 5 of the KRAKEN table in `krk_ext.c` are free (`0, 0, 0`), and `KRK_LIST_MAX` is 12 with 6 built-in rows plus 3 used ext rows. Add
`krk_scrambler.c` (built as `krk_scramble.c`) to `kraken_ui2` sources and `&krk_scrambler_screen` at slot 3. Label `"Scrambler (weak)"` (16 chars).
Layout (the screen is 19 columns):

```
line 0  16-bit scrambler            (DIM)
line 2  State: OFF | State: ON      (ON in ACCENT)
line 3  Key: 12345_                 (5-digit decimal, 1-65535)
line 5  status: "Applied" / "Need 1-65535"
line 6  Weak: 65k keys only
line 7  Patched radios only         (19)
line 8  Not for amateur use         (19)
line 9  #=on/off OK=apply
```

Keys, following `krk_callsign.c`: digits `SCAN_0`..`SCAN_9` (via the same `key_digit`) append to the key; on
overflow past 65535 start over with the new digit (as its SSID field does); `SCAN_STAR` deletes the last digit;
`SCAN_HASH` toggles On/Off; `SCAN_OK` validates and applies (On needs 1-65535; Off always valid);
`SCAN_UP`/`SCAN_DOWN` can be left unused. Back exits via the runner. On exit/OK call `scr_apply(1)` directly so the effect
does not wait for the next tick. Leaving the screen with On and a valid key keeps it On. The text must stay plain:
"weak", "patched radios only", "not for amateur use". Default Off.

### 3.3 Where it is applied: one new splice in the maintenance loop

Hook site (HIGH on the bytes, MED on the cadence): `ui_periodic_maintenance_task_loop` @0x0305476c, the call
`bsr 0x03068224` (`call_hold_led_debounce_dual_timer_tick`) at **0x030547ae**.

- Expected stock bytes (read from the image): `00 e0 3b 9d` = `bsr 0x03068224` (0x030547ae + 0x9d3b*2 = 0x03068224).
- Surrounding code (from disassembly): `ld.b r3,(r4,1); bez; subi r3,1; st.b r3,(r4,1)` at 0x030547a4, then this `bsr`, then
  `ld.b r3,(r7,0x16)`. Nothing branches over 0x030547ae, so it runs on every pass of the loop. The loop pends on
  a flag (r1 = 0x20000) and the menu-exit counter in the same loop advances at most once per second
  (`docs/menu-exit-timer.md`), so a pass is about 1 Hz. Not independently measured: confirm under
  QEMU with a counter in the hook (section 4, check 6).
- Why not the existing `aprs_tick_hook` at 0x03054888: it sits behind the CPS APRS enable and "APRS active on this
  channel" gates (0x03054870-0x03054884), so it does not run when APRS is off.
- Not claimed by the current manifest (grep of `patches/kraken_ui` found no use of 0x030547ae/0x03068224).
- The hook replaces the `bsr` with `bsr krk_hook_scramble_tick`. The hook first calls the stock function
  `0x03068224` (void, no arguments, same call as stock), then runs our code. After the call the stock code reloads r3 and r2,
  uses callee-saved r4-r11 which the C ABI preserves, so this is the same pattern as `krk_hook_aprs_tick`. Stack: the loop's task has a 0x180-word
  stack and our function needs a few words. Manifest entry (shape copied from `aprs_tick_hook`):

```json
{ "name": "scramble_tick_hook", "addr": "0x030547ae", "expect": "00 e0 3b 9d",
  "why": "ui_periodic_maintenance_task_loop: bsr call_hold_led_debounce_dual_timer_tick -> bsr krk_hook_scramble_tick, which calls it and then re-applies SCRAMBLE_REG",
  "branch": { "kind": "bsr", "target": "@kraken_ui2:krk_hook_scramble_tick" } }
```

(If the build tool wants `expect_branch` instead of raw bytes, use `{"kind":"bsr","target":"0x03068224"}` as `aprs_send_now_hook` does.)

Hook body:

```c
#define SCRAMBLE_REG   ((volatile u32 *)0x1100038c)
#define LOCAL_CC       ((volatile u32 *)0x11000384)
#define STOCK_CIPHER   (*(volatile u8 *)0x00033824)   /* CPS key type of the loaded channel, 0 = none */
#define stock_led_tick ((void (*)(void))0x03068224)
#define stock_rx_active ((int (*)(void))0x0300f2a4)   /* as krk_aprs_tx.c */

static u32 scr_want(void)
{
    if (S.magic != SCR_MAGIC || !S.on || !S.key) return 0;
    if ((VFO_FLAGS8 & 0xf0) != 0x10 || STOCK_CIPHER) return 0;   /* digital only, never on top of stock crypto */
    return ((u32)S.key << 16) | S.key;
}

static void scr_apply(int force)
{
    u32 want = scr_want();
    if (*SCRAMBLE_REG == want) return;
    if (!force && stock_rx_active()) return;     /* not mid-call; retried next second */
    *SCRAMBLE_REG = want;
}

void krk_hook_scramble_tick(void) { stock_led_tick(); scr_apply(0); }
```

Do not touch `LOCAL_CC` (bit 16 must stay 0; the test asserts it). Note that `VFO_FLAGS8` is the loaded side
(`0x0003e1e4`, as in `krk_core.c`). With dual watch the loaded side flips, so the key follows
whichever side is loaded; a scrambled call on one side and a non-scrambled one on the other may get the wrong mode
for a moment. Document it; it does not leak plaintext.

### 3.4 Clearing when off

Off, key 0, a non-digital channel and a stock-encrypted channel all give `want = 0`, and the next
tick (at most about 1 s) or the OK/Back in the screen writes 0. At power-up SRAM magic is invalid, so
`want = 0`, and the stock boot write already left 0. There is no stale-key window across reboot.

## 4. Verification under QEMU

Model facts (HIGH): `hw/misc/dm32_sysreg.c` backs the whole 0x11000000 window with plain r/w storage;
`SCRAMBLE_REG` and `LOCAL_CC` have no side effects in the model (coverage plan lists them as not modelled), and the model
does not clear them on `SYS_SOFT_RSTN`. QEMU therefore proves the register write happens, not that it scrambles anything or that a reset
would clear it. Use the QEMU scaffolding of `tests/kraken_ui_test.py` (`mi_rng_test` start_qemu/setreg/run_to,
`read_mem` over the gdb stub, machine halted at reset, hook called directly with canary registers).

Add to `tests/kraken_ui_test.py`:

1. Splice decode: `decode_branch` at 0x030547ae gives `bsr` to `s2["krk_hook_scramble_tick"]`; and the stock bytes are
   `00 e0 3b 9d` in the base image (the build asserts them already).
2. Calls the stock function first: run the hook with `pc = krk_hook_scramble_tick`, stop at `0x03068224` (and
   SENTINEL), fake its return as in `run_hook`, then continue to SENTINEL.
3. Value table. For each case seed `S.magic`, `S.on`, `S.key`, `*(u8*)0x3e1e4`, `*(u8*)0x33824`, pre-load
   `0x1100038c` with a canary, run the hook with `stock_rx_active` faked to return 0, and `read_mem(c, 0x1100038c, 4)`:
   - on, key 0x1234, flags 0x10, no cipher: `0x12341234`
   - same with `0x33824 = 2`: `0x00000000`
   - same with flags 0x00 (analog): 0
   - off, or key 0, or magic absent: 0
   - key 0xFFFF: `0xFFFFFFFF`; key 1: `0x00010001`
4. Idle gate: with `want` != current and `stock_rx_active` faked to return 1, the register is unchanged; faked to return 0, it
   is written.
5. ABI: r4-r11 and sp preserved (canaries), and `0x11000384` bit 16 is unchanged (preload `LOCAL_CC` = 0x1 and see it unchanged).
6. Full boot (qemu_ui_run.sh, key script opens KRAKEN > Scrambler, types 1 2 3 4 5, `#`, OK): (a) a gdb
   breakpoint at 0x030236f2 sees the stock zero write at boot, before the screen exists; (b) after OK the register reads
   `0x30393039` (12345 = 0x3039) on a digital channel; (c) after `#` toggling Off it reads 0 within 2 s of virtual
   time; (d) a counter in the hook confirms the cadence is about 1 Hz (settles the MED in 3.3). A digital channel needs the test
   codeplug (`DM32_SPINOR`), which the existing screen tests already use for the APRS list.
7. Screen frames for fingerprints (dm32uv-re [`docs/qemu-workflow.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/qemu-workflow.md) "Reading the results"): off state, typed key, status line.

None of this can show real scrambling. See 5.

## 5. Hardware verification (not done, for the human to schedule)

One action per bench round, firmware sweeps the variants (project convention). Needs two radios on the
same channel, both with this patch, and a legal test setup (dummy load, or a licensed non-amateur allocation; the operator decides).
Round 1: TX on A with key K; B receives with the same K and with 0, and the audio is compared. Questions to settle:
(1) does the audio actually change with K at all, (2) is `(K<<16)|K` the right receive value or must the RX field be
different, (3) is key 0 transparent, (4) does a LC/ID header still decode on a stock radio, (5) do SMS/DMR-APRS survive. If (2) is wrong, the sweep is in
the hook: a build constant that selects the RX field (K, ~K, bit-reversed K, byte-swapped K).

## 6. Risks and open items

| Risk | Tag | Handling |
|---|---|---|
| Scrambler algorithm undefined, RX field relationship unknown | LOW | bench sweep (5); feature stays Deferred until bench proves it |
| Cadence of the splice site is about 1 Hz | MED | measure in QEMU check 6d |
| A protocol reset may clear the key | LOW | 1 s re-apply |
| Data frames/SMS may be mangled | LOW | screen warning; test on the bench |
| 16-bit key is brute-forceable and headers/IDs are in the clear | HIGH | labelled "weak" on screen; no stronger claim anywhere |
| Dual watch alternates the loaded side | MED | key follows the loaded side; documented |
| Other users' radios hear noise | HIGH | default Off, RAM only; the label says "patched radios only" |
| Legality: encryption is barred on US amateur bands (47 CFR 97.113(a)(4)) | HIGH | the operator's concern; the UI says "Not for amateur use" |
| A hidden second accessor of 0x1100038c (computed base, not found by the scan) | LOW | the scan is instruction-level over the whole disassembly; a runtime register-write helper taking an offset could still exist; the 1 s re-apply makes the outcome benign |

Status note: [feature-status.md](feature-status.md) F12 was "Deferred 2026-10-15"; this document does not change the verdict on
interoperability (only other patched HR_C7000 radios), it only removes the "is it feasible and safe to place" unknown.

## Implementation (2026-10-29)

Built as `patches/kraken_ui/krk_scramble.c`, KRAKEN list slot 3 ("Scrambler (weak)", extension blob
kraken_ui2). Differences from the design above:

- No new splice. The re-apply rides on the shared maintenance hook `maint_tick_hook` (0x030547ae, stock
  `00 e0 3b 9d` = `bsr 0x03068224`, already spliced to `krk_hook_maint_tick` in `krk_periodic.c`), which now
  calls `krk_scramble_tick()` after the displaced stock call. Section 3.3's `scramble_tick_hook` manifest entry is
  therefore NOT used.
- State: `scr_st` (8 bytes: magic "SCR1", key, on, fmt) and `scr_ui` (8 bytes) in `.bss.feat`; power-up Off.
- `want` is exactly 3.3's rule: On, key != 0, loaded channel digital (`[0x3e1e4] & 0xf0 == 0x10`), stock cipher
  byte `[0x33824] == 0`; else 0. Written only when it differs from the register; tick calls skip the write while
  `stock_rx_active()` (0x0300f2a4) says busy. Correction to 3.3: that stock function is really "squelch open"
  (it returns REG_0CH bit 1, or 1 on an analog channel at squelch level 0), not a TX/RX state; TX is not gated,
  but a pass of the maintenance loop that finds the register already right does nothing, and the value only
  changes on a user action, a channel change or a stock-cipher change.
- Screen: `#` On/Off, digits type the key (a digit that would exceed 65535 starts over), `*` deletes, OK applies
  (forced write, status "Applied" / "Idle: analog ch" / "Idle: stock crypto" / "Need key 1-65535"). The lines
  "Weak, not encrypted", "Patched DM-32 only", "Not for amateur use" are on screen. The live `reg XXXXXXXX`
  line shows the register every half second.
- Key-format sweep for the 2-radio bench test (section 5's "(K, ~K, bit-reversed, byte-swapped)"): Up/Down on
  the screen step the format (every digit key is a key digit, so 7 could not be used):
  `fmt0` tx K / rx K, `fmt1` tx K / rx ~K, `fmt2` byte-swapped K in both fields, `fmt3` bit-reversed K in both.
  Pure value function `krk_scramble_value(K, fmt)`; format 0 is the register word `(K << 16) | K`.

Tests (tests/kraken_ui_test.py, `check_small_features`): stock bytes at 0x030547ae; value table for every
format (K 0x1234 -> 0x12341234, 0xEDCB1234, 0x34123412, 0x2C482C48; 0xFFFF; 1; 0); the tick on/off/analog/
stock-cipher/no-magic/busy/already-right cases with LOCAL_CC preloaded to 1 and unchanged; ABI canaries; the
maint hook calls the stock tick first, then F12, and bumps the counter; screen key handling by calling `scr_tick`
with drawing faked (typing, overflow, `*`, `#`, OK, refuse On with no key, Up/Up format) ; a QEMU frame.
The hook's ~1 Hz cadence (3.3 MED) is still unmeasured: QEMU run `krk_maint_ticks` over virtual time to settle it.

### One-action hardware test (2 patched radios, same digital channel, dummy load or a legal setup)

1. On both radios: KRAKEN > Scrambler, type the same key (e.g. 12345), `#` (State ON), OK. Status "Applied", `reg`
   shows `30393039`. The format shown (fmt0) must be the same on both.
2. Transmit from one radio (A), listen on the other (B). If B hears clean voice: format 0 is right (then also check with B at key 0 /
   Off: garbled; a stock radio: garbled). If garbled: on BOTH radios press Down/Up once (format changes, applied at
   once), transmit again. Four presses cover the four formats.
3. Report which `fmt` gave clean audio (or none). Nothing else is needed; the open items of section 5 (header
   decoding on a stock radio, SMS/DMR-APRS survival) are separate rounds.

### One-action hardware test: format sweep (key P1, 2026-10-29)

Replaces the hand-stepped "Down/Up once on both radios, transmit again" loop. A TX-side sweep through the synthetic-PTT
path was NOT built: the parrot's press/release machinery (`ps_watch`, TXUP release, `krk_vf_tx_quiet`, docs/parrot-tx.md Review
fixes) is built around replaying a captured call into a verified transmit buffer, and there is no equivalent safe
wrapper for a live, scheduled transmission; adding one for a test would put an unproven PTT path in the image. Both radios
instead follow the same 12-slot schedule of 3 s, driven purely by time, so the transmitter's only action is one ordinary PTT
hold of about 36 s into a dummy load (no timeouts or interlocks are bypassed):

    slot s = 0..11:  tx field of format {0,2,3}[s/4]  (fmt1's tx field equals fmt0's),  rx field of format s%4

That covers all 12 distinct tx x rx pairings of the four key formats. Procedure: on both radios open KRAKEN > Scrambler,
type the same key, press `P1` (the side key SCAN_P1; status `Armed: PTT or RX`; the typed key is stored, the On/Off state is
untouched). Transmitter: PTT for 36 s (it may leave the screen; the arm survives Back and expires after 5 min). Receiver: keep the
screen open (or not: the 1 Hz tick also starts it); it starts its schedule when its squelch opens, the transmitter when its first burst goes
out (`krk_scramble_tx_hook`), so the clocks differ by under a second. The screen line shows `slot n/12 tN rM`; the sweep
ends itself, restores the normal On/Off value, and `P1` while running cancels. The operator listens on the receiver and
reports which slot numbers were clean: that gives the tx format `tN` and rx format `rM`. UART0 markers (`SCR SWEEP ARMED`, `SCR SWEEP START`,
`SCR SLOT n/12 tx fmt T rx fmt R reg XXXXXXXX t10ms=...`, `SCR SWEEP END`) come from the screen and 1 Hz ticks only, never from
the TX hook; the optional capture is `stty -f PORT 115200 raw && cat PORT > scr.log` (PORT = the programming cable, e.g. `/dev/cu.usbserial-XXXX`) on either radio.
No RX metric can tell clean from scrambled audio (the scrambler works on the voice payload; sync, EMB and the
check flags that F3 reads are untouched), so the verdict is by ear. Limits: each slot change writes SCRAMBLE_REG in the middle
of a call without the receiver-busy guard (a stateful scrambler may need a moment to resynchronise; judge the last two thirds of a slot);
the sweep needs a digital channel without a stock cipher. This is one arming key per radio plus the one PTT hold; it is
not a single key on the transmitter alone. State: +8 bytes in `scr_st` (`sw_t0`, `sw`, `sw_slot`, `sw_logged`).

## Review fixes (2026-10-29)

The register used to lag a mode change by up to one second (1 Hz tick, and only while the receiver was idle), so an
immediate PTT after switching analog -> digital could go out clear while the screen said ON. Now `SCRAMBLE_REG` is also
applied (1) at channel change: extension hook slot 2 (`krk_ext_hooks[2]` = `krk_scramble_chan_change`) is called from the main blob's
`krk_hook_aprs_mode` stub at the existing `aprs_mode_hook` site 0x03057aa8 in `channel_change_apply_dispatcher` through the C
wrapper `krk_hook_chan_change`; the stub now saves r0-r3, r12, r13 and r15 around the call and recomputes C afterwards. The
dispatcher stores the new mode nibble (0x3e1e4) before this site; the stock cipher byte 0x33824 may still be the old one if
`dmr_privacy_key_load_and_mi_draw` runs later (order unverified), which the next two paths correct. (2) At the first TX burst:
`krk_vf_tx_hook` (TX vocoder task, every 60 ms) calls `krk_scramble_tx_hook`, a tick read and a compare per burst; a gap of
more than 200 ms since the previous burst marks a new transmission and only then the register is re-evaluated and written,
before that burst is copied to the modem window, so the key never changes mid-TX. (3) The 1 Hz tick and the screen as before.
In-target tests: `tests/kraken_ui_test.py::check_f12_review` (stub register preservation, analog / cipher / Off clear
at the change, TX-hook gating) and `tests/parrot_qemu_test.py` (hook at the real call site). Needs hardware: whether the
hardware scrambler picks up a register write made at the first burst for that same burst (the 1 Hz / channel-change paths mean
this only matters for the very first transmission after an unlikely missed change).
