#pragma GCC optimize ("Os")
/*
 * krk_tapedit.c - the keypad text entry of the callsign editor (krk_callsign.c), shared since 2026-11-11 with the APRS beacon
 * text editor (krk_aprs_text.c, blob ui2, F8a). Blob ui3. The callsign editor's keys are unchanged: this is its former typing
 * code, with the tables of keys 0 and 1, the length limit and the letter case as parameters (krk_tapedit.h).
 */
#include "krk_tapedit.h"

union krk_edit_ram krk_edit_ram;

static const char letters[] = "ABC2DEF3GHI4JKL5MNO6PQRS7TUV8WXYZ9";    /* keys 2-9, each set ends in its digit */
static const u8 at[8] = { 0, 4, 8, 12, 16, 20, 25, 29 };
static const char cycle[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/* Place of c (either case) in s, or the length of s when it is not there. */
static int find(const char *s, char c)
{
    int i = 0;

    if (c >= 'a' && c <= 'z')
        c = (char)(c - 32);
    while (s[i] && s[i] != c)
        i++;
    return i;
}

int krk_tap_key(struct krk_tap *t, char *buf, const struct krk_tap_spec *s, u8 scan)
{
    int d = scan == SCAN_0 ? 0 : scan >= SCAN_1 && scan < SCAN_1 + 9 ? scan - SCAN_1 + 1 : -1;
    const char *set;
    int i, n;
    char c;

    if (scan == SCAN_NONE) {
        if (t->key >= 0 && ++t->ticks >= 10)        /* 1 s without a repeat: character accepted */
            t->key = -1;
        return 0;
    }
    if (d >= 0) {
        set = d == 0 ? s->k0 : d == 1 ? s->k1 : letters + at[d - 2];
        t->ticks = 0;
        if (t->key == d && t->len) {                /* same key again: next character in place, after the digit the first */
            i = find(set, buf[t->len - 1]);
            i = set[i] == '0' + d ? 0 : i + 1;
        } else if (t->len < s->max) {
            t->key = (signed char)d;
            i = 0;
            t->len++;
        } else {
            return 1;                               /* full */
        }
        c = set[i];
    } else if (scan == SCAN_STAR) {
        t->key = -1;
        if (t->len)
            buf[--t->len] = 0;
        return 1;
    } else if (scan == SCAN_UP || scan == SCAN_DOWN) {
        n = sizeof cycle - 1;
        t->key = -1;
        if (!t->len) {
            t->len = 1;
            i = 0;
        } else {
            i = find(cycle, buf[t->len - 1]);
            i = i == n ? 0 : scan == SCAN_UP ? (i + 1) % n : (i + n - 1) % n;
        }
        c = cycle[i];
    } else {
        return -1;
    }
    buf[t->len - 1] = t->lower && c >= 'A' ? (char)(c | 0x20) : c;
    return 1;
}
