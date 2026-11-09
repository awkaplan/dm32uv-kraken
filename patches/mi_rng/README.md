# mi_rng: a real random source for the DMR privacy message indicator

Replaces the libc `rand()` that stock uses to draw the AES/ARC4 message indicator (MI), with an
AES-256 CTR_DRBG built on the firmware's own AES and seeded from ADC and timer noise.

Why: stock's MI comes from an unseeded LCG with only the low byte kept, so at most 512 distinct
initial MIs exist and they repeat across power-ups. Under AES-OFB that is keystream reuse. The
analysis is in `docs/encryption.md` of [dm32uv-re](https://github.com/awkaplan/dm32uv-re), sections 2-4.

This is feature F0a of the Kraken image. `tools/build.sh` builds it first, before `kraken_ui`,
and runs `tests/mi_rng_test.py` against the combined image. The entropy rate of the noise sources
is unmeasured; the measurement build and the estimator below exist to close that. The patch writes
one 4 KiB sector in the erased region and 4 bytes of one stock sector; it touches neither the
per-unit sector nor the calibration data (it *reads* the per-unit sector, XIP, nothing more).

## Two builds

| manifest | purpose | sectors | SRAM |
|---|---|---|---|
| `mi_rng.patch.json` | the fix | `0x0305c000` (4 bytes), `0x030e2000` (824 bytes) | state `0x18007400..0x180075d4` |
| `mi_rng_tap.patch.json` | **measurement only**: same code with `-DMI_RNG_SAMPLE_TAP`; every raw noise sample is also copied to a ring buffer, and the first seed uses 253 rounds so one boot fills it | same, 936 bytes | plus tap buffer `0x18005000..0x18007000` |

The two replace each other (same sectors). The tap build keeps a plaintext copy of the seed
material in RAM: use it to measure, then put the production build on.

## What it changes

| where | what |
|---|---|
| `0x0305caec` (4 bytes, sector `0x0305c000`) | `bsr lcg_rand_next` becomes `bsr mi_rng_entry`. This is the call inside the 6-iteration MI fill loop of `dmr_privacy_key_load_and_mi_draw`. The caller stores `r0`'s low byte and uses only `r4`/`r5` afterwards, both callee-saved. |
| `0x030e2000` (sector 226) | the generator. Claimed in `allocations.json` (this folder) as `mi_rng_code`. |
| SRAM `0x18007400..0x18007600` | its state, claimed as `mi_rng_state` (`0x1d4` bytes used). |
| SRAM `0x18005000..0x18007000` | tap buffer, `mi_rng_tap_buf`, measurement build only. |

`lcg_rand_next` is untouched and still serves its other callers (the ALPU exchange and the
scan-timer jitter). Those are not security-relevant.

## Design

```
first draw after power-up:   heavy seed  = 64 rounds x 4 ADC conversions + per-unit sector -> update()
every generate:              light mix   =  2 rounds x 4 ADC conversions -> update() -> generate
```

- **DRBG**: the NIST SP 800-90A CTR_DRBG shape with AES-256 and no derivation function (`update`
  = three counter blocks XORed with the provided data, giving the new key and V; `generate` = one
  block, then `update(0)` as a ratchet). It is not a validated implementation and is not claimed
  to be one.
- **AES**: the firmware's `aes_key_schedule_expand` and `aes_block_encrypt`,
  called at their IRAM execution addresses (flash-view address minus `0x030afc20`). Those were
  checked against FIPS-197 by `crypto_kat.py`.
- **IRQs off around each block encrypt.** The encrypt routine works in a global buffer (IRAM
  `0x33c88`, found in its literal pool), so it is not reentrant, and the DMR TX and RX keystream
  code share it from two different tasks ("CclTraffic", "rxvoice") with no stock lock. The patch
  uses stock's own `cpu_sr_save_and_disable_irq_iram` / `cpu_sr_restore_irq_iram` pair around
  each call; one block is 1221 instructions (QEMU exec trace, `mi_rng_test.py --measure-irq-off`),
  about 6 µs at 192 MHz. The key schedule writes only to the patch's own buffer.
- **Lock.** Three tasks can reach the hook (see "Call context"), with no stock lock on one of the
  paths, and the patch's state is static. `lock()` takes a busy word with IRQs off; a contending
  caller sleeps 1 ms (`OSTimeDly_ms_wrapper`) and retries, up to 2 s, then proceeds anyway so a
  stuck owner cannot wedge channel changes. Contention is counted in `contended`.
- **Noise**: four conversions per round on ADC channels **1, 2, 1, 2**. Only ADC1 and ADC2 are
  muxed to the ADC on stock (`adc_stop_and_reset` @0x0301caec clears IO_DIPLEX2 bits 20 and 21;
  no stock code clears bit 19 or 22-26, so other channels read a GPIO constant). Channel 2 is the
  battery sense; channel 1 is read by stock diagnostics, purpose unknown. Each conversion is
  stamped with TIMER0's down-counter (84 MHz, `CurrentValue` at `0x14000004`) before and after. A
  record is the after-stamp, the 10-bit result and the conversion time in ticks. Four records
  fill one 32-byte block, and each block is absorbed with a Davies-Meyer step over AES-256
  (`h = E_blk(h) ^ h`).
- **Per-unit mix** (heavy seed only): the first `0x200` bytes of the per-unit parameter sector
  (flash `0x0300b000`: the eFuse-derived 4-byte binding record at `+0x10c`, hardware bytes at
  `+0x80`/`+0xa0`, per-unit strings) go through the same compressor as 16 extra blocks. It is
  unique per radio but neither secret nor random: it only prevents two radios with equally poor
  noise from sharing a sequence. Shipped images have the sector blank (`0xFF`), which the code
  does not care about.
- **When it seeds**: on the first draw after power-up. That is the boot-time initial channel load
  (`radio_boot_full_init_sequence` reads the ADC at `0x0305f3e2` and starts the timers at
  `0x0305f2e0`, both before `channel_change_apply_with_zone_and_flash_validate` at `0x0305f436`),
  so the ADC and its semaphore are live. If the radio has no encrypted channel the generator never
  runs and costs nothing.
- **State validity** is an exact magic word, not "non-zero": SRAM holds power-on noise on
  hardware. A false positive (2^-32) just means the generator starts from noise and still mixes
  fresh samples before every output.
- **Stack**: all large buffers are static. Worst-case depth is about 140 bytes of stack frames.

## Call context (static, resolved 2026-10-14)

Every path to the hook ends in a task; none in an ISR (all registered interrupt handlers were
walked; ~14 vectors' subgraphs are only partly resolved, so this is "no path found", not proof).

| root | task | path |
|---|---|---|
| `radio_boot_full_init_sequence` | "task init" (TCB `0x00040da0`) | → `channel_change_apply_with_zone_and_flash_validate` → `channel_change_apply_dispatcher` |
| keypad handler index 79 (`FUN_030175c8`) | "task init" | → `dtmf_key_poll_tick` → `channel_load_synchronized` → dispatcher |
| screen-mode row 5 (`channel_vfo_commit_with_password_and_flash_write`) | "task menu" (TCB `0x0003e4fc`) | → `..._with_zone_and_flash_validate` → dispatcher |
| `FUN_0303240c` | "task CodeSw" (prio 28, created at `0x03030360`) | → `channel_switch_and_screen_refresh` → `channel_load_synchronized` → dispatcher |

`channel_load_synchronized` waits on an event flag (group `0x0003fd90`, bit `0x20`); it is a gate,
not a mutex, and the zone path has no lock. Hence the patch's own lock.

## Build and test

`tools/build.sh` builds and tests it as part of the Kraken image (`docs/building.md`). To build and
test it alone, from the repo root:

```sh
docker run --rm -v "$PWD":/w/dm32uv-unleashed -v "$(cd "${DM32UV_RE:-../dm32uv-re}" && pwd)":/w/dm32/dm32uv-re \
    -w /w/dm32/dm32uv-re dm32uv-re-pipeline:latest \
    -c 'python3 tools/src/dm32_patch_tools/build_manifest_patch.py /w/dm32uv-unleashed/patches/mi_rng/mi_rng.patch.json \
        --extra-allocations /w/dm32uv-unleashed/patches/mi_rng/allocations.json --require-claims \
        --out /w/dm32uv-unleashed/out/mi_rng \
        && python3 -u /w/dm32uv-unleashed/tests/mi_rng_test.py --noise-dependence --measure-irq-off'
```

Output lands in `out/mi_rng/` (gitignored). `tests/mi_rng_test.py` documents each check in its
docstring. In short: the DRBG core is compared with an independent OpenSSL model, the real
entry and the stock MI loop are run on a booted emulator with canary registers, the lock is
exercised with a preset holder, the finalisation is recomputed independently, and the splice is
decoded back from the image. The other QEMU tests in `tests/` reuse its QEMU and gdb helpers.

## Measuring the entropy (hardware step, not yet done)

1. Build the measurement variant and flash it (manual step, backup first):
   the build above with `patches/mi_rng/mi_rng_tap.patch.json` and `--out out/mi_rng_tap`.
   It replaces Kraken (its tap buffer overlaps Kraken's SRAM claims), so it goes on a radio
   running stock plus this patch only. A RAMDUMP-capable patch must also be on the radio
   (dm32uv-re's `docs/live-ram-capture.md`).
2. Power up on an encrypted digital channel (the first draw seeds and fills the tap).
3. `python3 dm32_ramdump.py --port PORT --addr 0x18005000 --size 0x2000 --out out/mi_rng_tap_<n>.bin`
   (`out/` is gitignored: a capture is per-radio data).
4. Repeat across boots, temperatures, battery levels and, if possible, radios.
5. `python3 tools/mi_rng_entropy.py out/mi_rng_tap_*.bin` — SP 800-90B
   most-common-value estimates per field and channel, a conservative per-round credit, and the
   `SEED_ROUNDS` that data supports with a 2x margin. Treat it as an upper bound on what the
   hardware gives.
6. Set `SEED_ROUNDS` from the result (or add a noise source if the credit is tiny), rebuild the
   production variant, re-run `tests/mi_rng_test.py`, and only then consider the production build.

The pipeline itself is exercised under QEMU by `tests/mi_rng_test.py --tap OUT.bin` followed by
`tools/mi_rng_entropy.py OUT.bin --source qemu`; the tool refuses to call that a measurement.

## What is not proven

- **Entropy.** Unmeasured on hardware. `SEED_ROUNDS = 64` is a guess; the section above is how
  to replace it with a number. Attacker influence (temperature, supply, a strong carrier) and
  unit-to-unit variation are not addressed by any amount of bench measurement.
- **IRQ-off time on silicon.** The 1221-instruction figure is an emulator instruction count.
- **Behaviour on a radio** of any kind, including the interaction with a call in progress.

## What it does not fix

Protocol limits from `docs/encryption.md` section 5 stay: the MI is 32 bits, so keys should be
rotated after on the order of 2^16 sessions; there is no integrity; metadata is in the clear;
keys sit in plaintext in the codeplug. And CPS type 1 "Custom" is not a cipher at all
(`docs/encryption.md` §2).
