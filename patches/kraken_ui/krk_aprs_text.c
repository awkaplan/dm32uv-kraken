#pragma GCC optimize ("Os")
/*
 * krk_aprs_text.c - F8a, the analog APRS beacon text (GitHub issue #3): an editor screen under Menu > APRS (analog channels)
 * (blob ui2). Its 48-byte record in stock block 4 at 0xF90-0xFBF is krk_aprs_trec.c's (blob ui3), written on OK and only when
 * the text changed (docs/settings-field.md 6, docs/aprs-submenu.md 7.6).
 *
 * The beacon (krk_aprs_tx.c) appends krk_aprs_text_cat() where it used to append the fixed KRK_APRS_COMMENT: the position
 * packet is "!lat/lon[" + text, the no-fix status packet ">" + text + " no GPS fix". An empty text, or a record that fails the
 * check (a vendor CPS write leaves 0xFF, a factory reset 0x00), means the default text KRK_APRS_COMMENT, as before.
 *
 * Length: at most 43 characters, the APRS limit for a position comment (APRS 1.0.1 ch. 5; no data extension is sent). The
 * status packet then is 1 + 43 + 11 = 55 characters, under the 62 a status text may hold. 43 characters and the cursor fit
 * three screen lines. The character set is what the keypad types: A-Z, a-z, 0-9, space and . - / , : + ( ). It leaves out
 * '|' and '~' (APRS forbids them in a comment) and every printable character with five 1 bits in a row ('_', '>', '?', '}'),
 * so the comment adds no HDLC stuffing bits and the longest frame stays under the sender's 1500-bit cap
 * (tests/test_aprs_text.py builds it). Typing is krk_tapedit.c's (blob ui3, shared with the callsign editor); the editor
 * state lies over krk_edit_ram (ui3 .bss), so this file has no RAM of its own.
 */
#include "krk.h"
#include "krk_tapedit.h"

static const struct krk_tap_spec txt_spec = { " 0", ".-/,:+()1", KRK_TXT_MAX };
#define E krk_edit_ram.text

static void draw(const char *status)
{
    int i;

    if (E.t.len < KRK_TXT_MAX)
        E.text[E.t.len] = '_';          /* the cursor, for this draw only */
    for (i = 0; i < 3; i++)             /* 43 characters and the cursor on three lines (krk_line draws KRK_COLS at most) */
        krk_line(1 + i, E.text + KRK_COLS * i, KRK_ACCENT);
    E.text[E.t.len] = 0;
    krk_line(5, status, KRK_FG);
}

static void text_enter(void)
{
    /* straight into the editor RAM: the text lands in E.text, zero after it and at [43] (the record's csum byte) */
    E.t.len = (u8)krk_aprs_text_load(&krk_edit_ram.rec);
    E.t.key = -1;
    E.t.lower = 0;
    krk_line(0, "Beacon text", KRK_DIM);
    krk_line(6, "Empty: default text", KRK_DIM);
    krk_line(7, "0=spc 1=.-/,:+()", KRK_DIM);
    krk_line(8, "*=del #=aA OK=save", KRK_DIM);
    draw("");
}

static void text_tick(u8 scan)
{
    const char *st = "";

    if (scan == SCAN_OK) {
        E.t.key = -1;
        st = krk_aprs_text_store() ? "Saved" : "Save FAILED";
    } else if (scan == SCAN_HASH) {
        E.t.key = -1;
        E.t.lower ^= 1;
    } else if (krk_tap_key(&E.t, E.text, &txt_spec, scan) <= 0) {
        return;                         /* the multi-tap timeout, or a key that changes nothing */
    }
    draw(st);
}

const struct krk_screen krk_aprs_text_screen = {
    .label = "Beacon Text",
    .enter = text_enter,
    .tick = text_tick,
};
