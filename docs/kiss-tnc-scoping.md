# Host KISS TNC: scoping (2026-11-04)

Status: **scoping only, nothing built.** What a KISS interface over the programming cable would
entail, grounded in the code and docs that exist today.

## 1. What already exists (the reason this is the cheapest remaining gap)

The hard parts — the modem and the protocol — are done and hardware-verified in both directions:

| Piece | Where | State |
|---|---|---|
| AX.25 UI frame build + FCS (CRC-16/X.25) | `krk_ax25.c` (145 lines) | host-tested (`tests/test_ax25.py`) |
| Bit stuffing, flags, NRZI → tone bits | `krk_ax25_tones()` | host-tested |
| Bell-202 demod + HDLC deframer, FCS-checked | `krk_afsk.c` (494 lines) | host-tested (`tests/test_afsk.py`); real off-air frame decoded on the radio (capture17, v38) |
| FM Send RAM TX path | `fmram_send()`, `docs/fm-send-ram-tx.md` | on air, 1200 baud bit-exact, decoded by Dire Wolf via RTL-SDR (v53, 2026-11-01) |
| Standby RX decode, no screen needed | `krk_mon_tick()` in `krk_aprsrx.c`, `bg_tick_hook` @`0x03013f48` | QEMU-verified (47 checks); **not yet run on a radio** |

So KISS is a **framing and transport layer over the serial link**, not new DSP or RF work.

## 2. The blocking unknown: UART0 receive

This is the one real research gate, and it is not small.

**Every UART use in this repo is transmit-only and polled.** `krk_squelch.c`, `krk_dmrlink.c`,
`krk_scramble.c`, `krk_satrf.c`, `krk_bvscr.c` all do the same thing:

```c
#define UART0_THR (*(volatile u32 *)0x14030000)   /* DesignWare 16550 */
#define UART0_LSR (*(volatile u32 *)0x14030014)
while ((UART0_LSR & 0x20) && ++guard < 200000)    /* wait THRE */
    ;
UART0_THR = (u8)c;
```

A `grep` for `0x14030004` (RBR), `RBR`, or an `LSR & 1` (data-ready) test across `patches/`,
`tools/` and `docs/` returns **nothing**. No patch has ever read a byte from the cable.

What has to be established before any KISS work starts:

1. **Who owns UART0 receive.** Stock's CPS protocol listens on this port — that is how
   the vendor CPS and the cable tools talk to the radio. There is almost certainly a stock
   ISR or task draining RBR. If so, a polled read in the background hook will **lose a race with
   stock for every byte**, and the CPS tools will break while KISS is active. This needs the
   same treatment `bg_tick_hook` got in `docs/aprs-rx-monitor.md` §G1: find the handler, read the
   disassembly, decide between chaining it and taking the interrupt over.
2. **PIC source 12 is UART0** (dm32uv-re [`data/registers/HR_C7000-DM32.h`](https://github.com/awkaplan/dm32uv-re/blob/main/data/registers/HR_C7000-DM32.h): sources 12-15 are UART0, UART3,
   GPIOB, GPIO). dm32uv-re [`docs/rtos-and-memory.md`](https://github.com/awkaplan/dm32uv-re/blob/main/docs/rtos-and-memory.md) §106 notes the general-purpose interrupt vector table at
   `0x00045514 + N*4` and points at `dm32_patch_api.h` for adding an ISR. So installing a
   receive ISR is a known-shape operation — but it would be **the first ISR this project installs**,
   and it would sit on the same port the CPS tools use.
3. **Contention with stock's TX mutex.** `uart_tx_poll_mutex_guarded` @`0x0002ce78` shares semaphore
   `0x0003b9f8` with the I2C2/RTC path (dm32uv-re `docs/rtos-and-memory.md`). Existing patch code bypasses the
   mutex and writes THR directly, which is tolerable for debug lines but not for a sustained
   bidirectional protocol.

**Consequence to decide up front:** while KISS is running, the programming cable is the TNC. The
CPS tools, `pcm_capture.py`, the UART debug logs of F3/F11/F12 and the CPS tools all contend for
that one port — and `docs/flashing.md` already warns only one tool may use it at a time. KISS has to
be an explicit mode the operator enters, not something always on.

## 3. Work breakdown

### Phase A — research (the gate; no code ships)
- Find and disassemble the stock UART0 receive path; identify the ISR or polling task and its buffer.
- Decide: chain the stock ISR, or own the interrupt and re-dispatch CPS bytes to it.
- Confirm on hardware that bytes can be taken without breaking the CPS protocol.
- **If this fails, the feature stops here** — exactly as B2 stopped until GPIO 0x2F was understood.

### Phase B — KISS framing (pure code, host-testable)
- SLIP framing: FEND 0xC0, FESC 0xDB, TFEND 0xDC, TFESC 0xDD; escape on both directions.
- Command byte: port/command nibbles. Honour at minimum cmd 0 (data). TXDELAY/P/SlotTime/FullDuplex
  (1-5) can be accepted and ignored; cmd 0xFF (Return) exits the mode.
- A new `krk_kiss.c` with no hardware dependency, host-tested like `test_ax25.py` — round-trip,
  escapes, oversize frames, truncated frames, garbage resync. This is the cheap, low-risk half.

### Phase C — RX: radio → host
- Feed every FCS-valid frame from `krk_afsk_feed()` out as a KISS data frame. The monitor already
  harvests frames in `krk_mon_tick()`; add a tap beside `stn_add()`.
- Buffering: a 1200-baud frame is at most 330 bytes (`AFSK_MAX_FRAME`), the link is 115200 baud, so
  the link is ~95× faster than the air. A single-frame queue plus a drop counter is sufficient.
- **This direction is much easier than TX** — it needs no UART receive at all, so Phase C can ship
  before Phase A succeeds, as a one-way "monitor to host" / iGate-feed mode.

### Phase D — TX: host → radio
- Needs Phase A. Take a KISS frame, validate, hand the raw AX.25 bytes to `krk_ax25_tones()` and
  then `fmram_send()`.
- **Refactor required:** `krk_aprs_tx_send()` builds its own frame from the GPS fix
  (`build_frame()` → `build_info()`) and is tangled with the beacon's gates. A KISS frame arrives
  pre-built, so a new entry point is needed that takes frame bytes and reuses the gate block:
  `analog()`, `tx_is_inhibited()`, `on_selected_side()`, `stock_rx_active()`, `tx_busy`, and
  `krk_mon_yield()`.
- Keep every existing gate. A host application can ask for TX at any rate; the radio must refuse on
  a digital channel, under dual watch on the wrong side, on a busy channel, or when TX is inhibited.
- **Consider a CSMA/duty-cycle limiter.** Nothing in the current design rate-limits TX, because the
  beacon runs on the stock interval timer (≥30 s). A host TNC can key continuously. This is a
  regulatory and a thermal concern, not just a code one.

### Phase E — integration
- A `KRAKEN > KISS TNC` row. It cannot be a stock option level with a RAM-only value like F14's
  RX Monitor if it needs a live screen; model it on F14 (option row Off/On) plus a status screen
  showing frames in/out and drops.
- Yield interlocks: KISS mode must participate in the same `krk_screen_owner` / `krk_aprs_tx_busy()`
  / `krk_mon_yield()` protocol that F14 established, and refuse to coexist with Parrot and Burst
  Voice (which own the shared buffer).
- RAM-only per policy P4, default Off.

## 4. Budget

- **Flash:** ui2 has 13,892 bytes free (`docs/aprs-rx-monitor.md` §G2). `krk_kiss.c` plus the TX
  entry point should be well under 2 KB. Not a constraint.
- **SRAM: this is the constraint.** The decoder claim has **36 bytes free** and ui2 `.bss` has
  **9 bytes free**. A KISS RX buffer plus a TX frame buffer is ~700 bytes at minimum. That needs
  either a new claim in `allocations.json`, or sharing the F9/F13 buffer with the same
  mutual-exclusion discipline Parrot and Burst Voice use.
- **CPU:** the monitor already costs ~115k instructions per 32 ms half (4-13 % at priority 30).
  SLIP framing is trivial beside that; a receive ISR at 115200 baud is ~11.5k bytes/s worst case,
  which is real but bounded work at interrupt level.

## 5. Honest estimate

- Phase B alone (pure code, host tests): **S**.
- Phase C (RX to host, one-way iGate feed): **S-M**, and it ships without Phase A.
- Phase A (UART0 receive research): **M-L, and genuinely may fail** — it is the same shape of
  unknown that blocked B2 for weeks.
- Phase D+E: **M** once Phase A lands.

Overall this is **not XL** like F10 or F13, because no new DSP or RF work is involved. But it is the
first project to install an interrupt handler and the first to contend with stock for a shared
peripheral, so the risk is concentrated entirely in Phase A.

**Recommended order:** B → C → ship a one-way host monitor feed (useful on its own: that is the
iGate case) → then attempt A, and only do D if A succeeds.

## 6. What this does and does not close

Closes, if all phases land: the KISS TNC gap and the iGate-with-a-computer gap.

Does **not** close: hotspot/MMDVM (B5 — needs raw DMR bursts, unrelated to this), IP over DMR,
the network services, or APRS messaging on the radio's own UI (that is a UI feature, not a transport
one, though a KISS host makes it moot for users with a PC attached).
