/*
 * krk_txtrim.c - F6: finer TX power steps. A global trim (100% down to 12.5% in 1/8 steps) applied
 * to the PA drive byte that stock calibration picks for Low/Mid/High.
 *
 * Mechanism (dm32uv-re docs/tx-power-control.md section 1): freq_calibration_apply_all @0x0300db48
 * calls freq_calibration_interpolate_selected (bsr at 0x0300db66) and stores its r0 result as the
 * PA drive byte, RAM 0x00038498 (= 0x38470 + 0x28; st.b r0,(r5,0x28) at 0x0300db6c). The manifest
 * redirects that bsr to krk_hook_txtrim_interp, which calls the stock function and scales its result.
 * The caller only uses r4/r5 (callee-saved) afterwards, and the function saved r15 in its prologue.
 *
 * Composes with Auto Power (adaptive_power): that patch reads the byte at key-up as its baseline,
 * so it scales the trimmed value further and restores the trimmed value at key-down.
 *
 * The trim lives in RAM only (a cold power-up = 100%, stock behaviour; it survives a flash-loader reboot, hardware 2026-11-01). It is a fraction of the DAC
 * code, NOT of the output power. One point of the DAC-to-RF curve is
 * measured (hardware 2026-11-01, 433.05 MHz, other radio's FD6818 RSSI at 2 m): 1/8 drive (byte 14 from 110
 * at Low) is about 34 dB below full Low, and that low a drive is safe for the PA, so the floor is 1/8.
 */
#include "krk.h"

#define freq_calibration_interpolate_selected ((u32 (*)(u32 freq, u32 level))0x0301b424)
#define PA_DRIVE ((volatile u8 *)0x00038498)

#define TRIM_MIN 1              /* eighths: 1/8 = 12.5% (was 4/8 until 2026-11-01) */
#define TRIM_MAX 8
#define TRIM_MAGIC 0x54524d31   /* "TRM1": SRAM holds power-on noise, so state is validated */

static struct {
    u32 magic;
    u8 eighths;
    u8 last_raw;                /* last unscaled byte stock calibration produced */
    u8 last_out;                /* what we returned for it */
    u8 have_raw;
} t;

static u32 trim_get(void)
{
    if (t.magic != TRIM_MAGIC) {
        t.magic = TRIM_MAGIC;
        t.eighths = TRIM_MAX;
        t.have_raw = 0;
    }
    return t.eighths;
}

static u8 scale(u8 raw)
{
    return (u8)((raw * trim_get() + 4) / 8);
}

/* Spliced in place of `bsr freq_calibration_interpolate_selected` at 0x0300db66. */
u32 krk_hook_txtrim_interp(u32 freq, u32 level)
{
    u32 raw = freq_calibration_interpolate_selected(freq, level) & 0xff;

    t.last_raw = (u8)raw;
    t.last_out = scale((u8)raw);
    t.have_raw = 1;
    return t.last_out;
}

/* Re-apply after a change, but only if nobody else (Auto Power during TX, a channel change in
 * between) has touched the byte since we produced it. Otherwise the next channel load applies it. */
static void apply_now(void)
{
    if (t.have_raw && *PA_DRIVE == t.last_out) {
        t.last_out = scale(t.last_raw);
        *PA_DRIVE = t.last_out;
    }
}

/* ---- "TX Power Trim": a stock option level, 8 options "1/8".."8/8" (docs/stock-option-level.md 6.1) ----------------------
 * The row lives in the extension blob (the whole file does since 2026-11-02: the custom stepping screen it replaces was in the
 * main blob). Presentation only: the state `t`, the apply path and the hook are exactly what the old screen used; Up/Down used
 * to step trim_get() by one, now OK on an option sets it. The trim is a fraction of the PA DAC code (NOT of the output power)
 * and is RAM only: it is lost at power-off (a cold power-up = 8/8, stock behaviour). */
static int trim_opt_get(void)
{
    return (int)trim_get() - TRIM_MIN;
}

static void trim_opt_set(int i)
{
    if ((unsigned)i > TRIM_MAX - TRIM_MIN)
        return;
    trim_get();                         /* initialise the state first */
    t.eighths = (u8)(i + TRIM_MIN);
    apply_now();
}

static const char *const trim_labels[8] = { "1/8", "2/8", "3/8", "4/8", "5/8", "6/8", "7/8", "8/8" };
static const struct krk_opt trim_opt = { .title = "TX Power Trim", .labels = trim_labels, .n = 8, .get = trim_opt_get, .set = trim_opt_set };

const struct krk_screen krk_txtrim_screen = {
    .label = "TX Power Trim",
    .opt = &trim_opt,
};
