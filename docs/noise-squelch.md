# F11 noise-based squelch (Off / Normal / Tight): FD6818 registers, stock behaviour, patch recipe

Status: the sections up to 9 are static research (datasheet + disassembly); the implementation and test notes follow. Evidence tags: HIGH = read in the
datasheet AND matched to stock bytes/disassembly; MED = one source or a short inference; LOW = guess or unmeasured.
Addresses are flash/IRAM addresses of stock DM32.00.01.048 unless noted.

Sources:
- FDG = FD6818 programming guide rev 1.0.0 (Chinese edition; FDG line numbers refer to a text conversion of it)
- SQDOC = RDA1846S "SQ algorithmic introduction" application note (AT1846S, different register map)
- RE = the [dm32uv-re](https://github.com/awkaplan/dm32uv-re) decompile corpus; disassembly re-read from the images with `cs_disasm.py`
- docs in this repo: `analog-vs-digital-rx-config.md`, `retune-for-scope.md`, `fd6818-disc-mode-rx.md`

---------------------------------------------------------------------------------------------

## 1. Bottom line

1. The FD6818 already does an RSSI + noise squelch in hardware (FDG 2261-2271):
   ```
   if (rssi_sq > rssi_sq_th_in && noise_sq < noise_sq_th_in)   SQ = 1;
   else if (rssi_sq < rssi_sq_th_out || noise_sq > noise_sq_th_out) SQ = 0;
   ```
   There is NO mode-select bit. "Noise squelch" vs "RSSI squelch" is purely a matter of which thresholds are
   set: the RSSI pair (REG_78H) and the noise pair (REG_4FH). HIGH.
2. Stock sets the noise pair ONCE, at boot, to a fixed 0x302F (out 48, in 47), and rewrites only the RSSI pair
   (REG_78H) per channel and per squelch-menu change, from the per-frequency calibration table indexed by the
   squelch level 0-9. So stock = "RSSI squelch with a fixed, always-on noise guard". HIGH.
3. Recommended design: a RAM-only mode (Off / Normal / Tight). Off = stock, untouched. Normal/Tight, on analog
   channels only, replace the RSSI pair with a "gate effectively off" value and set a tighter or looser noise pair,
   so the NOISE comparator decides. One splice, at the single funnel through which every stock REG_78H write
   passes (the `bsr fd6818_reg_write` at 0x0300de86), re-applies our values after every stock write. No polling.
4. The numeric noise thresholds are UNMEASURED (LOW). The scale of `noise_sq` on this board is unknown, so the
   screen must show live noise_sq / RSSI / sq_out, and the first hardware round measures them (section 8).
5. Biggest open unknown: who actually mutes the speaker when SQ closes in analog RX (section 4). Static analysis
   found no MCU-side mute. The strongest evidence is that the stock Ana SQL Level menu changes ONLY REG_78H and
   that changes audible squelch, so the chip SQ decision gates the audio somehow. MED.

---------------------------------------------------------------------------------------------

## 2. FD6818 registers (all page 0: no REG_7FH page select needed)

Paging rule (FDG 787-791): only addresses above 0x7F need `WRITE(0x7F,1)`. Every squelch register below is
<= 0x78, so the existing `fd6818_reg_read/write` (IRAM 0x2c33c / 0x2c2f4) can be used as is. HIGH.

| Function | Reg | Bits | Meaning | Reset | Stock | Cite |
|---|---|---|---|---|---|---|
| RSSI open threshold | 0x78 | [15:8] `rssi_sq_th_in` | SQ=1 needs rssi_sq above this; 0.5 dB/step | 0x44FF | (b<<8) per channel | FDG 2288, 3244 |
| RSSI close threshold | 0x78 | [7:0] `rssi_sq_th_out` | SQ=0 when rssi_sq below this | (same word) | (b-1)&0xFF | FDG 2289, 3246 |
| Noise close threshold | 0x4F | [14:8] `noise_sq_th_out` | SQ=0 when noise_sq above this | 0x7E34 | 0x30 | FDG 2290, 2998-3001 |
| Noise open threshold | 0x4F | [6:0] `noise_sq_th_in` | SQ=1 needs noise_sq below this | (same word) | 0x2F | FDG 2291, 3001 |
| RSSI reading | 0x67 | [8:0] `rssi_sq` | dBm = raw/2 - 160 (uncalibrated here) | RO | read by stock meter | FDG 2293, 3143 |
| Noise reading | 0x65 | [7:0] `noise_sq` | "NOISE indicator, dB/step" (RO) | RO | stock reader masks 7 bits | FDG 2297, 3141-3142 |
| RSSI relative | 0x65 | [15:8] `rssi_rel` | RO | | not read by stock | FDG 2296 |
| SNR reading | 0x61 | [15:8] `snr_out` | RO, dB/step | | not read | FDG 2295, 3135 |
| SQ result | 0x0C | [1] `sq_out` | the comparator output (RO) | | read by stock | FDG 2298, 2785 |
| Weak signal | 0x0C | [7] `weak_rssi` | RO | | not read | FDG 2299, 2781 |
| AGC / LNA RSSI | 0x62 | [15:8]/[7:0] | RO, 1 dB/step | | not read | FDG 3136 |
| External LNA comp. | 0x2C | [4:0] `ext_lna_gain` | RSSI normalisation | | not written | FDG 2292 |
| SQ interrupt | 0x3F | [0] enable | `irq_mask`; result in REG_02H[4] | | stock does not use SQ irq | FDG 2388-2403, 2416 |
| SQ to a pin | 0x26/0x27 | gpioN_out_sel = 2 | SQ on a GPIO | 0 (value mode) | not routed | FDG 2546-2570 |
| Soft mute | 0x90 (page 1) | [12] en, SNR threshold | attenuates weak audio; alternative "tight" lever | 0x8C20 (en = 0) | not written | FDG 2318-2330, 3279 |

Notes:
- Direction of the noise numbers: bigger noise_sq = noisier. A signal opens SQ only while noise_sq is BELOW
  `noise_sq_th_in` and closes it when it rises ABOVE `noise_sq_th_out` (FDG 2263-2271). So a LOWER threshold is a
  TIGHTER squelch. HIGH on the rule; the actual noise_sq values per condition are not documented.
- The datasheet's own default is in 52 / out 126 (REG_4FH = 0x7E34); stock overrides it with 47 / 48 (narrow
  hysteresis). OpenGD77's AT1846S noise pair, 0x48 = 0x1A32 / 0x1DB6 / 0x1E38, decodes to high/low = 52/50, 59/54,
  60/56 (the AT1846S field is [13:7]/[6:0], FDG-like layout but a different chip). So 45-60 is the plausible
  range of the useful number. LOW (cross-chip prior, `at1846s-opengd77-analogy.md` line 53).
- Reserved bits: REG_4FH[15] and [7] must be written 0 (reset 0x7E34 has them 0).
- The datasheet has no SQ detection-time or averaging register (the AT1846S has 0x5A, SQDOC). The chatter
  behaviour of a narrow noise hysteresis cannot be tuned on this chip. MED.
- Datasheet inconsistency: the soft-mute field positions in the prose table (FDG 2319-2323) differ from the
  register summary (FDG 3282-3287). Do not use soft mute without a readback test. Not needed for the recommended design.

---------------------------------------------------------------------------------------------

## 3. What stock writes, from where, and when

### 3.1 Evidence that the wire addresses mean what the datasheet says

All of these match the FDG semantics exactly (HIGH):
- `hw_register_0x78_write_from_byte_pair` @0x0300de78:
  `zextb r0; lsli r3,r0,8; subi r1,r0,1; zextb r1; movi r0,0x78; or r1,r3; bsr 0x2c2f4`.
  So REG_78H = (b << 8) | ((b - 1) & 0xFF): in = b, out = b - 1 (0.5 dB hysteresis, FDG rssi_sq_th_in > _out).
- `fd6818b_default_register_init` @0x0300ecb8 (run once from `rf_chip_init_and_spawn_worker_tasks` @0x0300d4b0):
  at 0x0300edb0.. `movi a0,0x4e; movi a1,0x4708; bsr; movi a0,0x4f; movi a1,0x302f; bsr`. So REG_4FH = 0x302F:
  out = 0x30 (48), in = 0x2F (47), the same "out is one above in" shape.
- Reads: `reg_0xc_bit1_read` @0x0300eb44 (sq_out), `reg_0x67_lower9_read` @0x0300ebb0 (rssi_sq),
  `fd6818_page0_reg65_low7_get` @0x0300ebc0 (`movi r0,0x65; bsr 0x2c33c; andni/andi 0x7f`: noise_sq low 7 bits).
  The 0x65 reader has NO callers in the corpus (and no function-pointer reference found by scanning the image for its
  address), so stock computes the noise reading but never uses it. MED (a table reference could have been missed).

### 3.2 Complete write inventory (searched)

- Flash scan of every `movi r0,N ... bsr 0x2c2f4` in `meta/disasm.jsonl` (3334 functions): REG 0x4F appears once
  (0x0300edba, boot), REG 0x78 appears once (the helper above). The only non-literal reg arguments are the tone
  programming paths (`fd6818b_channel_table_register_program` @0x030645ca, `tone_gen_config_apply` @0x3067df4/38,
  DTMF @0x3064a48/c20, `task_body_uCOS3` @0x300e4f2) which program sub-audio/tone registers, not 0x4F/0x78. MED
  (the disassembly corpus is partly desynchronised in places, see `analog-vs-digital-rx-config.md` section 8).
- IRAM scan (linear sweep of 0x2b400..0x2f100): no write of 0x4F or 0x78; the retune `0x2b6a0` writes 0x32, 0x38/39,
  0x47, 0x48, 0x37, 0x3F, 0x50, 0x30, 0x58-0x5E, 0x72, 0x07 only. MED (sweep, not call-graph complete).
- Result: after boot, REG_4FH is never rewritten by stock; REG_78H is rewritten on every path below. HIGH/MED.

### 3.3 Where the squelch value b comes from (squelch level 0-9)

- Per-channel squelch level = high nibble of byte +0xC of the channel/VFO record (same byte the codeplug doc calls 0x1C,
  bits 7-4; `aprs-stock-gaps.md` lines 20, 33). Factory default 3.
- `freq_calibration_apply_all` @0x0300db48 calls `channel_step_frequency_bound_check` @0x0301b214 at 0x0300db90 with
  (RX freq, level = record[0xC] >> 4, wb = record[9] >> 7) and stores the returned byte as a halfword at
  RAM 0x0003e212 (literal pool 0x0300dc88 = 0x0003e210, `st.h r0,(r3,2)` at 0x0300db96). The Ghidra name is wrong:
  this is the squelch-threshold calibration interpolation.
- `channel_step_frequency_bound_check`: reads two calibration points (lo/hi frequency neighbours, per-unit data in
  codeplug block 2: offsets 0xE0..0xF3 for the 2 m band, 0x64..0x77 for 70 cm, 0x80..0x86 in the special window);
  for each frequency point there is a level-1 value and a level-9 value; levels 2..8 interpolate
  `lo + level*(hi-lo)/8`; level 0 behaves as level 1 here; the result is then interpolated across frequency
  (`linear_interpolate_freq_calibration` @0x0301b000) and forced to >= 1. HIGH on structure, MED on level 0
  (the formula gives level 0 the level-1 value, so "squelch 0" must open the audio some other way, or via a very low
  calibrated level-1 value; unresolved and per-unit).
- The squelch menu ("Ana SQL Level", label table entry at 0x30b86f8) calls `menu_wrapper_vfo_step` @0x03050070 ->
  `vfo_step_field_write` @0x0300de8c: it stores the new nibble in the record, recomputes b through
  `channel_step_frequency_bound_check` (store to 0x3e212+... as `PTR_DAT_0300df4c[1]`) and calls the 0x78 helper.
  MED (the menu-to-function link goes through a function table; the string and the only RF effect are consistent).

### 3.4 When REG_78H is written (all via the helper's single `bsr 0x2c2f4` at 0x0300de86)

The helper has 3 callers. `channel_change_apply_squelch` @0x0300df08 does
`ld.b r0,[0x3e210+2]; bsr 0x300de78` (bsr at 0x0300df0e), then the RX retune `0x2b6a0(*0x3846c)`, antenna GPIOs.
Its callers (6): `channel_change_apply_dispatcher` @0x030573e8 (every channel change, bsr at 0x030573e8 region),
`rf_frequency_adjust_and_display_refresh` @0x0302fcd0, `screen_enter_apply_channel_and_rf_mode` @0x03038058,
`channel_change_tx_led_reset` @0x030553ec (this is the path after TX ends), `cps_serial_field_rw_dispatcher_variant`
@0x0302ae0e, `factory_service_command_dispatch` @0x030682bc. Other callers of the helper: `vfo_step_field_write`
(squelch menu change) and the thunk `hw_register_0x78_write_from_byte_pair_fwd_thunk` @0x03054df4 (no callers).

Boot/power-on: `fd6818b_default_register_init` writes REG_4FH = 0x302F, then the first channel apply writes REG_78H.
Because our hook sits on the 0x78 write, it always runs AFTER the boot 0x4F write. HIGH.

`freq_calibration_apply_all` is also called from `ptt_press_handler` @0x0305bfb8 and `settings_write_dispatcher`
@0x0304b088; those only refresh 0x3e212 (no register write) and the value is applied at the next
`channel_change_apply_squelch`. HIGH.

Consequence for the patch: a single hook on the helper's register write sees every stock squelch write. REG_4FH is
never touched by stock after boot, so writing it from the hook is enough; we write it each time anyway (idempotent).

### 3.5 Per-unit data

The level tables are calibration data in the codeplug (per radio), not in this repo. The patch never needs them: it
sees the final byte b and the final REG_78H word at the hook.

---------------------------------------------------------------------------------------------

## 4. How the audio actually gets muted (OPEN, MED)

- `sq_out` (REG_0CH[1]) is read only through `reg_0xc_bit1_read` <- `vfo_display_state_nibble_bits_check`
  @0x0300f2a4 <- `vfo_display_debounce_and_hw_flag_clear` @0x03060060 (in `tx_state_main_task_loop` @0x03060754) and
  `rssi_rolling_average_meter_update` @0x0305ffe4. That is stock's "receiving" indicator (UI flags, RX LED path,
  RSSI meter). No analog-RX caller of `audio_pa_enable_gate` @0x0301d6cc was found (the callers are PTT, tone, digital
  rxvoice, channel-change teardown): `analog-vs-digital-rx-config.md` section 8 item 6.
- The FD6818 has no documented hardware mute driven by SQ (no SQ mute bit; the IRQ and GPIO-select routes are not
  configured by stock: REG_26H/27H are not written, REG_25H = 0xC1BA is the value mode, FDG 2546).
  The block diagram puts SQ in the metering group (FDG 459, 575-577), not in the audio mux.
- Stock analog uses REG_47H afout_mode 0x00 (undocumented), `analog-vs-digital-rx-config.md` section 5. The best
  hypothesis there is "chip-controlled default audio path, squelch-gated". If true, the chip itself gates on SQ and
  changing the thresholds changes audibility directly. If instead some task polls `sq_out`, the same thresholds
  change `sq_out` and the result is the same. Either way the thresholds are the lever. The two cases differ only for
  a software fallback (section 7).
- Evidence the thresholds are the lever: the stock squelch menu's only RF-side effect is the REG_78H write and the
  radio audibly squelches by level (forum S11, `rx-audio-sources-forums.md` line 84; the APRS captures lost the first
  ~0.3 s of audio to squelch, `afsk-tx.md` captures 4-8).
- Test that settles it: read REG_0CH[1] and watch audible state on the bench (section 8 round 1).

---------------------------------------------------------------------------------------------

## 5. Is there a noise reading to poll? Yes (HIGH on register, LOW on scale)

- `noise_sq` = REG_65H[7:0], read-only, "dB/step" (FDG 2297, 3142). Stock's own reader takes the low 7 bits (matches
  the 7-bit thresholds). `rssi_sq` = REG_67H[8:0] (already used by `krk_rssi.c` and `krk_rf_rssi_raw`),
  `sq_out` = REG_0CH[1], `snr_out` = REG_61H[15:8].
- All are plain page-0 reads through `fd6818_reg_read` @0x2c33c, which takes the bus semaphore (safe from the menu
  task, `retune-for-scope.md` section A.5).
- The HR_C7000 also has an independent FM-block squelch metric `FM_SQL` (0x508, higher = no valid in-band signal;
  `rx-audio-fm-mode.md` lines 106, 363-367) that stock never reads. A possible second noise metric, but it needs the
  modem RAM probe and has no documented unit. LOW. Not needed.
- Expected behaviour (from the datasheet rule and every FM squelch): noise_sq is HIGH with no carrier (hiss) and falls
  as a signal quiets the discriminator. The chip computes it continuously, also while SQ is closed. MED (rule),
  LOW (values).

---------------------------------------------------------------------------------------------

## 6. Patch design

### 6.1 State (P4: RAM only, magic validated)

Same shape as `krk_txtrim.c` (`TRIM_MAGIC`): a static struct in the patch's SRAM state window:
```
static struct { u32 magic; u8 mode; u8 dirty; u8 pad[2]; } q;      /* mode 0 Off, 1 Normal, 2 Tight */
#define SQ_MAGIC 0x53514c31   /* "SQL1" */
static u32 sq_mode(void) { if (q.magic != SQ_MAGIC) { q.magic = SQ_MAGIC; q.mode = 0; q.dirty = 0; }
                           return q.mode <= 2 ? q.mode : 0; }
```
Power-up (SRAM noise) = Off = stock. A CPS write or reflash does not matter. Not persisted (by design).
No flash write at all. If persistence is wanted later, the existing block-0x04 pattern (`krk_callsign.c`) is the
precedent; not recommended for a mode that can make the radio quieter than the user expects.

### 6.2 Register values per mode (analog channels only)

Analog test: `(*VFO_FLAGS8 & 0xf0) == 0` (`krk_rf.h` VFO_FLAGS8 = 0x0003e1e4; stock uses the same flags_0x8 high nibble,
0 = analog, 0x10 = digital). On a digital channel the hook passes stock through and restores REG_4FH.

| Mode | REG_4FH (noise out<<8 \| in) | REG_78H (RSSI) | Meaning |
|---|---|---|---|
| Off | 0x302F (stock; written back once if `dirty`) | stock word, untouched | exact stock behaviour |
| Normal | 0x3432 (out 52, in 50) [LOW, tune] | 0x201F (in 0x20, out 0x1F = -144 dBm, RSSI gate effectively off) | noise decides, ~OpenGD77-like |
| Tight | 0x2A28 (out 42, in 40) [LOW, tune] | 0x201F | needs more quieting to open |

- Why 0x201F: rssi_sq cannot go below the receiver noise floor (about -125 dBm = raw 70), so th_in = 32 (raw/2 - 160 =
  -144 dBm) is always exceeded and th_out = 31 never trips. Result: only the noise comparator decides. MED (FDG formula
  FDG 2293; the real noise floor of this unit is unmeasured). `weak_rssi`/RSSI meter are unaffected.
- Optional "hybrid" variant (recommended for first hardware test because it cannot be deafer than stock on RSSI):
  keep the stock REG_78H word, change only REG_4FH. Then the squelch-level menu still means something, and the noise
  pair only tightens or loosens the stock guard. Make this a compile-time switch `SQ_KEEP_RSSI`.
- Safety clamps applied in the hook before any write: noise `in >= 8`, `out >= in`, both <= 127, bit 15 and 7 clear;
  RSSI pair `in > out`. A table error can then not produce "never opens" (in = 0) or an inverted pair.
- All numbers live in one `static const` table so the hardware round (section 8) only edits constants.

### 6.3 Hook (single splice)

Splice site: the `bsr fd6818_reg_write` inside `hw_register_0x78_write_from_byte_pair`:
- address 0x0300de86, expected bytes `80 e2 37 f2` (= `bsr 0x0002c2f4`, the builder's `expect_branch` check
  `{"kind":"bsr","target":"0x0002c2f4"}` suffices, like `lh_call_start` in the manifest).
- Context (verified): `0300de82 movi a0,0x78; 0300de84 or a1,a3; 0300de86 bsr 0x2c2f4; 0300de8a pop {r15,}`. At the call
  r0 = 0x78, r1 = word. Nothing is live after the call except the saved lr (pop restores it), so the hook may
  clobber r0-r3 and has the normal C ABI: `void krk_hook_sq_write(u32 reg, u32 val)`.
- Manifest entry (style of `txtrim_interp`):
  `{"name":"sq_write_hook","addr":"0x0300de86","why":"F11: every REG_78H write funnels here...",
    "expect_branch":{"kind":"bsr","target":"0x0002c2f4"},"branch":{"kind":"bsr","target":"@kraken_ui2:krk_hook_sq_write"}}`
  (a target in `kraken_ui` works equally; put it with the screen).
- Hook body:
  ```
  void krk_hook_sq_write(u32 reg, u32 val) {
      u32 m = sq_mode();
      if (m && analog()) {
          fd6818_reg_write(0x78, SQ_RSSI_WORD);        /* or val if SQ_KEEP_RSSI */
          fd6818_reg_write(0x4f, NOISE_WORD[m]);
          q.dirty = 1;
      } else {
          fd6818_reg_write(reg, val);                  /* stock */
          if (q.dirty) { fd6818_reg_write(0x4f, 0x302f); q.dirty = 0; }
      }
  }
  ```
  The stock write is skipped when we override (the pair is then written once, in the right order); on pass-through
  it is the original call, so Off is byte-for-byte stock behaviour on the bus. `fd6818_reg_write` takes the semaphore
  on its own; the calls are sequential, not nested. HIGH.
- Why this hook and not a periodic poll: 0x78 is rewritten on every channel apply, every squelch-menu change, and after
  every TX, i.e. at every time stock could disturb the RF state we care about; REG_4FH is never touched after boot
  (section 3). The hook runs after the boot 0x4F write because the first 0x78 write follows it. A periodic poll would
  add a race and a semaphore customer for no benefit. If a late RF reset is ever found (e.g. a power-save wake that
  re-inits the chip; `fd6818b_default_register_init` has only the one caller at boot), add a re-assert in the existing
  `krk_hook_aprs_tick` (ui_periodic_maintenance_task_loop, splice 0x03054888) as a cheap safety net. MED.
- Dual watch / both sides: the hook sees whatever channel stock is applying. `VFO_FLAGS8` is the loaded channel, as
  for `channel_change_apply_squelch`. If stock applies A then B, our rule applies per channel type, consistent. MED.
- Screen "Apply now": after a mode change call the stock helper `0x0300de78(*(u8 *)0x0003e212)`; it runs through our
  hook, so the new values are active immediately without a retune. 0x3e212 holds the last b (written by
  `freq_calibration_apply_all` and the squelch menu). If the byte has never been computed it is zero, which the stock
  helper turns into REG_78H = 0x00FF; to avoid that, skip the immediate apply while `*(u8*)0x3e212 == 0` and rely on the
  next channel apply. LOW-MED.

### 6.4 Screen (existing framework)

`const struct krk_screen krk_squelch_screen = { .label = "Squelch Mode", .enter, .tick, .exit }` in a new file
`krk_squelch.c`, listed in `krk_screens[]` in `krk_core.c` (blob 1, next to `krk_txtrim_screen`) or in a free slot of
`krk_ext_screens[]` in `krk_ext.c` (blob 2 has 3 free KRAKEN slots, `KRK_EXT_MAX` = 6). The txtrim screen is the template.
```
line 0  "Squelch mode"            (dim)
line 2  "Off | Normal | Tight"    selected word in KRK_ACCENT; Off in KRK_FG (stock)
line 4  "noise 47  thr 47/48"     live noise_sq (REG_65H & 0xFF) and the active in/out
line 5  "rssi -118.5 dBm"         REG_67H, dBm = raw/2 - 160 (uncal), as krk_rssi.c
line 6  "SQ open" / "SQ closed"   REG_0CH bit 1
line 7  "Up/Down: change"
line 8  "RAM only; boot=Off"      ("Analog channels only" on a digital channel)
line 9  "Back: exit"
```
Keys: `SCAN_UP`/`SCAN_DOWN` cycle the mode (as in `trim_tick`), apply immediately. A 100 ms tick (default) reads three
registers, redraws changed lines with the FNV hash trick from `krk_rssi.c`. Use the `krk_cat`/`KRK_LINE_BUF`
rules from `krk.h` (v35 freeze). No `.exit` restore: leaving the screen keeps the mode, which is the feature. The mode
returns to Off at power-up.

### 6.5 Safety (never leave the radio deaf)

1. Default Off; state validated by the magic word; RAM only, so a power cycle restores stock.
2. Off path = stock call unchanged, plus a single write-back of REG_4FH = 0x302F if we ever changed it (`dirty`).
3. Clamps in 6.2 make in = 0 / inverted pairs impossible. The Tight table must not go below `in = 20` without a
   hardware measurement (otherwise strong-but-noisy signals never open). LOW.
4. Analog channels only: digital RX (the 4FSK path) keeps stock REG_4FH and REG_78H. The noise comparator on a 4FSK
   channel was never measured and the squelch there decides DMR audio.
5. The screen always shows the live `sq_out` and noise next to the setting, so a user who finds the radio quiet sees why
   and can set Off. Any stock path that does a full re-init (`radio_init_sequence` @0x0300e40c on key-up, retune) does
   not touch 0x4F/0x78 (section 3), so the mode survives PTT.
6. No path touches TX registers, the page register, REG_03H/30H, or flash.

---------------------------------------------------------------------------------------------

## 7. Software squelch fallback (only if the hardware thresholds prove unusable)

Idea: poll noise_sq (and sq_out) from a periodic tick, decide in software, and mute with the stock audio mute. The
mute primitive already in `krk_rf.c` is `fd6818b_reg47_afout_clear_mode` @0x0002b550 (REG_47H bits 11:8 := 0, i.e.
afout nibble 1 -> 0; `krk_rf_mute()`), unmuting by `fd6818b_reg47_tx_bypass_or_rx_afout` @0x0002b51c (RMW to nibble 1)
or by the stock `krk_rf_restore()`. Problems, all LOW:
- Which of REG_47H 0x6042 / 0x6142 is the audible state in steady analog RX is unconfirmed
  (`analog-vs-digital-rx-config.md` section 8 item 6 and note a).
- Stock rewrites REG_47H on channel change and keying; a polling gate would need to re-assert (a race).
- The chip gate (if section 4's hypothesis is right) would still act in parallel: software could only add muting,
  not open a chip-closed squelch; opening needs the thresholds opened wide (REG_78H/REG_4FH at their opens) and the
  software then closes. That is workable: set the chip to "always open" (4F = 0x7F7F, 78 = 0x201F) and gate in software
  with hysteresis and a hang time. But then the stock SQ indicator, the RX LED and the APRS squelch behaviour all change.
- Cost: one polled 3-wire read per tick (a few hundred microseconds), no extra hardware.
Do this only after round 1 below shows the thresholds have no effect on audibility.

The same fallback is the one place a "noise level" display is needed anyway, so build the live readout first.

---------------------------------------------------------------------------------------------

## 8. Verification

### 8.1 QEMU (no FD6818 model; register writes are observable)

Harness: `tests/kraken_ui_test.py` + `tests/mi_rng_test.py` (`start_qemu halted`, `setreg`, `run_to`,
`wmem`), image from `compose_qemu_image.py --iram-out`. The stock function is not run for bus work: run to its entry,
read the arguments, fake the return (exactly like `call_trim` for F6). New checks:
1. Splice decode: `decode_branch` of the 4 bytes at 0x0300de86 in the patched image is a `bsr` to `krk_hook_sq_write`;
   the 4 stock bytes `80 e2 37 f2` are asserted by the manifest `expect_branch`.
2. Hook sequence per state, with `run_to([SENTINEL, 0x0002c2f4])`, logging (r0, r1) at each stop, then
   `setreg pc <- r15` to return:
   - magic absent (SRAM noise, `wmem 0xA5*8`): exactly one write (0x78, val) = stock, mode reads Off, magic set.
   - mode Normal / Tight, `VFO_FLAGS8` (0x3e1e4) high nibble 0: writes (0x78, 0x201F) and (0x4F, table value); registers
     r4-r11, sp preserved (canary pattern).
   - mode Normal, high nibble 1 (digital): one stock write, plus (0x4F, 0x302F) iff `dirty` was 1; dirty then 0.
   - mode Off after Normal: stock write + (0x4F, 0x302F) once, not again on the next call.
   - clamp: forced table out of range -> clamped words.
3. Whole-flow log on the real boot (needs the booted QEMU run `tools/qemu_ui_run.sh`, `qemu-workflow.md`): a breakpoint at
   0x0002c2f4 with a condition on r0 in {0x4f, 0x78} printing r1 across boot -> first channel apply: expect
   (0x4F, 0x302F) then (0x78, ...); with the mode forced to Tight in RAM before the channel change (key press / `wmem`)
   expect (0x78, 0x201F) and (0x4F, Tight) after every channel change, and after a simulated key-up/key-down.
4. UI: screenshot of the new row and screen via `render_frames.py` (frame fingerprints), key Up/Down cycling text.
5. Host test: none needed beyond the clamp function (pure C).
The QEMU run proves the sequencing and the ABI. It cannot prove the radio opens/closes squelch; that is hardware.

### 8.2 Hardware rounds (one action per round)

Radio on an analog channel with a second radio as the signal source (dummy load or low power, never on a live
frequency). The firmware sweeps the variants, one capture per round.
1. Read-only round: build the screen with the mode switch and a live readout of noise_sq / rssi_sq / sq_out, plus the
   REG_4FH and REG_78H readback. Record: idle noise_sq (no carrier), noise_sq with the carrier at several levels
   (-110 .. -60 dBm), noise_sq with a 1 kHz tone vs unmodulated, the stock `sq_out` transitions, and the b / REG_78H
   word per squelch level 0..9. This fixes the scale and settles section 4.
2. Pick Normal/Tight words from the measured table (Normal ~ open when quieting is about 12 dB, Tight ~ 20 dB), set
   `in` a few counts below `out`.
3. Test round: mode switch over the air, confirm audio opens/closes at the intended noise level, that Off is
   indistinguishable from stock, that key-up/down and a channel change keep the mode.

---------------------------------------------------------------------------------------------

## 9. Confidence summary and open risks

| Claim | Confidence |
|---|---|
| Squelch is a dual RSSI + noise comparator with no mode bit; REG_4FH/REG_78H/REG_65H/REG_67H/REG_0CH meanings | HIGH (datasheet + stock bytes agree) |
| All squelch registers are page 0 | HIGH |
| Stock writes REG_4FH once at boot (0x302F) and REG_78H only through the helper at 0x0300de78/0x0300de86 | HIGH (flash), MED (IRAM sweep) |
| b comes from the per-frequency calibration, indexed by the 0-9 level, stored at 0x3e212 | HIGH |
| One hook at 0x0300de86 sees every stock squelch write | HIGH |
| `noise_sq` readable at REG_65H[7:0], polled safely | HIGH on register, MED on usefulness |
| Threshold numbers 50/52 (Normal), 40/42 (Tight), RSSI gate "off" word 0x201F | LOW (unmeasured scale) |
| Lower noise threshold = tighter | HIGH (rule), LOW (magnitudes) |
| Thresholds control audible squelch (chip or MCU gates) | MED |
| Software mute fallback via REG_47H nibble | LOW |

Open risks:
1. noise_sq scale and the idle value are unknown; a bad Tight value can make the radio mute on weak signals (user-visible,
   RAM only, recoverable by Off or power cycle). Mitigated by the readout and clamps.
2. The audio gate location is unproven (section 4). If the audio is gated by something other than SQ (e.g. only by the
   MCU on a different signal), the thresholds would change the indicator but not the audio. Round 1 settles it.
3. Squelch level 0 ("open") semantics with a fixed noise guard: stock may rely on the calibration giving level 0 a very
   low RSSI value while REG_4FH stays 47. In noise modes our table, not the level menu, rules, so level 0 would not be
   "open". Consider a rule: if the loaded channel's level nibble (`record[0xC] >> 4`, also visible as the b byte)
   is 0, pass stock through (monitor behaviour). The nibble lives in the VFO display state; confirm its RAM address before
   using it. LOW.
4. No averaging/debounce register on the FD6818 for SQ: a narrow hysteresis can chatter. Keep out - in >= 2 counts.
5. Digital channels excluded by design. Dual watch behaviour unverified on hardware.
6. Per-unit calibration (codeplug block 2) is read by stock only; nothing in this design reads or stores it.
7. Interaction with the APRS RX path (`krk_aprsrx.c`): squelch 0 is the documented good setting for AFSK capture
   (`afsk-tx.md`). Noise modes must not be on during an APRS decode test unless the table is known to open on that signal.

## Implementation (2026-10-29)

Built as `patches/kraken_ui/krk_squelch.c`, KRAKEN list slot 4 ("Squelch Mode", kraken_ui2).

- Splice `sq_write_hook` at 0x0300de86, stock bytes `80 e2 37 f2` (= `bsr 0x0002c2f4`, verified again against
  the dm32uv-re stock image `progmem_stock_DP570UV_20260926_FULL.bin` and by the manifest's `expect_branch`) -> `bsr krk_hook_sq_write`.
  Called as `(0x78, stock word)`. State `sqst` (16 bytes) + screen state `squi` (72 bytes) in `.bss.feat`.
- Modes: Off (stock, byte for byte) / Normal 50/52 (0x3432) / Tight 40/42 (0x2A28) / Custom (in/out adjusted
  live). Analog only; on a digital channel, and at squelch level 0, the hook passes stock through and restores
  REG_4FH = 0x302F once (`dirty`). Squelch level = high nibble of the loaded channel record byte +0xC =
  RAM 0x3e1e8 (the same record and offset `freq_calibration_apply_all` reads at 0x0300db82 with base
  0x3e1dc, and that `vfo_display_state_nibble_bits_check` tests); this closes section 9 risk 3's "confirm its RAM
  address". Caveat: the squelch menu stores the new nibble before calling the helper, but if it stores it
  into a different record than 0x3e1dc the level seen by the hook is stale until the next channel apply.
- Clamps (`krk_sq_noise_word`): in >= 8, out >= in, both <= 127 (reserved bits 15 and 7 stay 0).
- RSSI word: DEFAULT is the section 6.2 "hybrid" (stock REG_78H word kept, only the noise pair changes, so the
  mode cannot be deafer than stock on RSSI); key 1 on the screen switches to the "gate open" word 0x201F
  (rssi_in 32 / out 31) so the noise comparator alone decides.
- Mode change applies at once: the screen re-runs the hook with the last stock word (`last_val`), which the hook
  records on every call, so no retune and no read of 0x3e212 is needed.
- Screen keys: `#` / `*` next / previous mode, Up/Down: in Normal/Tight copies that preset into Custom and
  moves the pair (+1/-1 per press; OK cycles which field: both / in / out; the line shows `[b]`, `[i]`, `[o]`),
  key 1 RSSI gate stock/open, key 5 a read-only 5 s noise survey (50 samples at 100 ms: noise min/avg/max and
  rssi dBm min/avg/max, shown until the next survey). Live lines: mode, active in/out, `noise` (REG_65H[7:0]),
  `snr` (REG_61H[15:8]), `rssi` dBm (REG_67H, uncalibrated) with SQ OPEN/shut (REG_0CH[1]), and the readback of
  REG_4FH and REG_78H (`4F xxxx 78 xxxx`: also answers whether the chip reads these registers back).
- The FD6818 helpers are used through the stock absolute addresses (`krk_rf.h` macros for 0x2c33c / 0x2c2f4),
  so no defsym into the main blob and no copy was needed.
- Correction to section 4/6.3's remark about `stock_rx_active`: nothing relies on it here.

Tests (`check_small_features`): stock bytes and splice decode; hook table (Off, no magic, Normal hybrid and
gate-open, Tight, Custom clamped both ways, digital, level 0, restore-once, ABI); screen: `#` applies at once,
Up starts Custom from the preset, OK field select, key 1 gate, `*` stepping back to Off restores 0x302F,
5 s survey min/avg/max against a fed noise sequence; a QEMU frame.

### One-action hardware test (key 6 survey, 2026-10-29)

Key `6` on the Squelch Mode screen runs the whole measurement by itself: a 25 s window, one sample (noise_sq REG_65H,
RSSI REG_67H) per 100 ms. Operator: second radio idle on the same analog channel, press `6` on the radio under test, and
key the second radio up ONCE (unmodulated carrier on a dummy load, about 5 to 10 s long) anywhere after the first 3 s.
No other keys. Logic (`krk_sqsurvey.h`, host test `tests/test_sqsurvey.py`): the first 3 s give the idle RSSI baseline;
a sample more than 10 dB above it is "carrier"; each class ignores its first 2 to 3 samples after a change (detector lag);
`idle_min` = lowest noise_sq without carrier, `carr_max` = highest with it. With gap = idle_min - carr_max >= 4:
Tight in = carr_max + gap/3, Normal in = carr_max + 2 gap/3, out = in + 2 (the squelch opens when noise < in, so lower
is tighter). Otherwise the screen says `no carrier seen`, `no idle seen` or `no gap: no advice`.
Screen: `auto N/25s base|idle|CARRIER` with live `idle>=X carr<=Y`, then `nrm a/b tgt c/d`. Nothing is applied by the
survey itself. After a valid result line 8 shows `OK=nrm 9=tight set`: OK sets Custom to the Normal pair, key `9` to the
Tight pair (both through the normal hook, effective at once); any other use of OK is unchanged (field select) when no
suggestion is pending. UART0 (115200): `SQ SVY START`, one `SQ <sec> <noise> <rssi_half_dB> <carrier 0/1>` per second, then
`SQ SVY END base/idleN/idleMin/carrN/carrMax/status <values>` and `SQ SVY SUGGEST nrm in/out tgt in/out`. One capture
command: `stty -f /dev/cu.usbserial-XXXX 115200 raw && cat /dev/cu.usbserial-XXXX > sq.log` (optional; the screen has it all).
To judge the suggestion afterwards, apply it with OK and key the second radio again (SQ OPEN / shut and the audio).
The old key `5` 5 s survey is unchanged. State: +24 bytes in `squi`.

### Earlier hand procedure (superseded by key 6)

Second radio as signal source on an analog channel (dummy load / low power). On the radio under test open
KRAKEN > Squelch Mode and press key 5 with no carrier (5 s idle survey), then with the carrier at a few
levels, repeating key 5 each time (the result stays on screen: noise and dBm min/avg/max). Read idle vs carrier
noise to place the thresholds: set Custom in/out with Up/Down while the other radio keys up and watch SQ OPEN/shut
and the audio. The numbers that work replace the `preset` table. Key 1 compares hybrid vs noise-only.

## UART edge log (Squelch Mode screen)

Every change of the squelch open state (REG_STATUS_SQ bit 1) prints one line: `SQ OPEN <tick> noise <n> rssi <r>` or `SQ SHUT <tick> noise <n> rssi <r>` (tick = uC/OS 100 Hz counter, rssi in half-dB units as the screen). The first line after entering the screen reports the state at entry. The screen has no busy loop (100 ms tick), so no extra yield was added.
