/*
 * krk_about.c - the "About" screen: what this image is and which patches it carries.
 * Lines are at most KRK_COLS (19) characters.
 */
#include "krk.h"

/* The stock firmware's own version string (Menu > Device Info shows it; NUL-terminated, at most 15 characters).
 * Written for DM32.00.01.048; the build for another firmware moves the address to that build's string
 * (docs/firmware-builds.md), so the line names the firmware this image is built on. */
#define STOCK_VERSION ((const char *)0x030adaa0)

static void about_enter(void)
{
    krk_line(0, "DM-32UV Kraken", KRK_ACCENT);
    krk_line(1, STOCK_VERSION, KRK_DIM);
    krk_line(3, "Patches:", KRK_FG);
    krk_line(4, " MI RNG (DMR IV)", KRK_FG);
    krk_line(5, " Auto Power", KRK_FG);
    krk_line(6, " Last heard, RSSI", KRK_FG);
    krk_line(7, " Grid, TX trim", KRK_FG);
    krk_line(9, "Back: exit", KRK_DIM);
}

const struct krk_screen krk_about_screen = {
    .label = "About",
    .enter = about_enter,
    .tick = 0,
};
