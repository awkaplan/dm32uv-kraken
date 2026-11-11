/*
 * krk_aprs_menu.c - the analog rows of the stock APRS menu (krk_core.c krk_aprs_render): on an
 * analog channel "Upload ID" picks the digipeater path (a stock option level) and "Upload Beacon" sends one AFSK beacon
 * now (krk_aprs_tx.c). "APRS On/Off" and "Intervals Set" are the stock rows, shared with DMR APRS.
 */
#include "krk.h"

/* ---- Upload ID (analog): digipeater path ----------------------------------------------------- */
/* A stock option level (docs/stock-option-level.md 6.2) over the four path names (krk_aprs_tx.c krk_aprs_path_names), backed by the path
 * field of the callsign record. set() runs the same write as the old screen's OK (krk_aprs_cfg_set_path: flash block 4, verified read-back);
 * the stock "Setting is OK" popup follows, and a failed write is reported by a second popup (the old screen's "Save FAILED"). */
static u8 aprs_path_failed;

static int aprs_path_get(void)
{
    return (int)krk_aprs_cfg_path();
}

static void aprs_path_set(int i)
{
    aprs_path_failed = krk_aprs_cfg_set_path((unsigned)i) ? 0 : 1;
}

static void aprs_path_after(int i)
{
    (void)i;
    if (aprs_path_failed) {                  /* aprs_path_set() ran just before in this same OK; never stale */
        krk_popup_draw(KRK_POP_ERR, "Save FAILED", 0);
        os_time_dly_ms(1500);
    }
}

static const struct krk_opt aprs_path_opt = {.title = "APRS path", .labels = krk_aprs_path_names, .n = KRK_APRS_PATHS,
                                        .get = aprs_path_get, .set = aprs_path_set, .after = aprs_path_after};

const struct krk_screen krk_aprs_path_screen = {
    .label = "Upload ID",
    .opt = &aprs_path_opt,
};

/* ---- Upload Beacon (analog): send one beacon now ------------------------------------------------ */
/* A stock-style one-shot (docs/stock-option-level.md part C): OK on the row runs this, popups say what happened, the menu returns.
 * No custom screen. OK is also MENU (one scan code, 0x03) and the send TRANSMITS, so a single OK must never key the transmitter:
 * the row's own OK only arms (popup "OK again to send", 3 s); the send needs a second, fresh OK press (krk_popup ignores a key that
 * is still down from the first one until it is released). Any other key, or the timeout, cancels. The send path and every gate of
 * it (krk_aprs_tx_send: analog only, TX inhibited, dual-watch side, busy channel, callsign, GPS) are unchanged. */
#define POP_ARM_MS    3000              /* krk_tx_arm waits this long for the second OK */
#define POP_LINE      16                /* characters that fit the popup box */

/* Splits s over the popup's two lines at the last space that keeps line 1 within POP_LINE characters. */
static void split2(const char *s, char *a, char *b)
{
    unsigned n = 0, cut = 0, i;

    a[0] = b[0] = 0;
    while (s[n])
        n++;
    if (n <= POP_LINE) {
        krk_catn(a, KRK_LINE_BUF, s);
        return;
    }
    for (i = 0; i < n && i <= POP_LINE; i++)
        if (s[i] == ' ')
            cut = i;
    if (!cut)
        cut = POP_LINE;
    for (i = 0; i < cut; i++)
        a[i] = s[i];
    a[cut] = 0;
    krk_catn(b, KRK_LINE_BUF, s + cut + (s[cut] == ' '));
}

/* The transmit-arm popup shared with krk_parrot.c: 1 only when OK is pressed fresh within POP_ARM_MS. */
int krk_tx_arm(const char *line1)
{
    return krk_popup(KRK_POP_WARN, line1, "TRANSMITS", POP_ARM_MS) == SCAN_OK;
}

static void upload_run(void)
{
    const char *why, *kind;
    char l1[KRK_LINE_BUF], l2[KRK_LINE_BUF];
    int r;

    if (!krk_channel_analog()) {
        krk_popup(KRK_POP_ERR, "Analog channel", "only", POP_RESULT_MS);
        return;
    }
    if (!krk_tx_arm("OK again to send"))
        return;                                 /* timeout or any other key: cancelled, back to the menu */
    krk_popup(KRK_POP_INFO, "Sending...", 0, 0);
    r = krk_aprs_chan_send(&why, &kind, 0);
    l1[0] = 0;
    if (r == 0) {
        krk_cat(l1, "Sent ");
        krk_cat(l1, kind);
        krk_popup(KRK_POP_OK, l1, 0, POP_RESULT_MS);
        return;
    }
    split2(why, l1, l2);
    krk_popup(KRK_POP_ERR, l1, l2, POP_RESULT_MS);
}

const struct krk_screen krk_aprs_upload_screen = {
    .label = "Upload Beacon",
    .enter = upload_run,
    .flags = KRK_SF_ONESHOT,
};
