/*
 * krk_tags.c - standby tags: two-letter marks on the standby screen's bottom line, between stock's
 * "Menu" and "A/B", for the Kraken features that are switched on AND act at standby on the current
 * channel (docs/standby-tags.md, user guide docs/user-guide/README.md "Standby tags").
 *
 * Where: rows 290..319, x 60..199. Stock's standby_screen_refresh (0x03018e64) clears the whole line
 * (fill_rect 0,290,240,320) and draws "Menu" at x 12..59 and the A/B bitmap at x 200..227; nothing
 * else on standby draws there. Every other user of the line (call screens, scan, digit entry, menus)
 * clears it first, so tags never linger on another screen.
 *
 * When:
 *   - standby_bottom_hook (0x030195d2, the only standby path: boot, Back from a menu, end of a call,
 *     popups) replaces `bsr standby_screen_refresh` with krk_hook_standby_bottom: stock's line first,
 *     then the tags. That path runs only while stock draws standby, so it needs no screen check.
 *   - krk_tags_tick (maintenance task, about once a second, the task that also redraws stock's clock)
 *     redraws the tags when the set changed while the radio sits at standby: a feature that switched
 *     itself (tracking ended, the channel changed, the beacon armed). Gated like stock's own standby
 *     redraws (screen-mode byte 0 or 0x5b; stock's call screens use other values) plus no Kraken
 *     screen and no TX. Not gated on the squelch-open read stock_rx_active(): an analog carrier
 *     leaves standby on screen, and that read is an RF-chip register access.
 *
 * Order and colour: the features that can key the transmitter on their own come first, in red; the
 * receive-side ones follow in the theme colour stock uses for "Menu". Four tags fit; with more, three
 * are shown and the fourth slot reads "+N".
 */
#include "krk.h"
#include "krk_stock.h"

enum { T_VT, T_VX, T_CW, T_BN, T_SA,            /* can transmit on their own */
       T_SC, T_MN, T_SQ, T_PR, T_N };
#define T_FIRST_RX T_SC

static const char tag_txt[T_N][3] = { "VT", "VX", "CW", "BN", "SA", "SC", "MN", "SQ", "PR" };

#define TAG_X0   60                     /* free band of the line on standby: x 60..199 */
#define TAG_X1   200
#define TAG_MID  130
#define TAG_Y    290                    /* stock's "Menu" row */
#define TAG_Y1   320
#define TAG_W    24                     /* two 12x24 glyphs */
#define TAG_GAP  10                    /* 4 x 24 + 3 x 10 = 126 of the 140 px */
#define TAG_MAX  4

#define TAG_RED      0xF800
#ifndef THEME_IDX                                            /* tests/test_tags.py supplies host versions */
#define THEME_IDX    (*(volatile u8 *)0x0003ea17)            /* settings[0x63], the menu colour */
#define THEME_TABLE  ((const u16 *)0x030b0790)               /* stock's 8 theme colours */
#define SCRAMBLE_REG (*(volatile u32 *)0x1100038c)           /* krk_scramble.c: non-zero only while applied */
#define stock_standby_bottom ((void (*)(void))0x03018e64)    /* standby_screen_refresh: the bottom line */
#endif
#define SHOWN_MAGIC  0x54470000u                             /* "TG" in the top half: krk_tags_shown is valid */

static u32 tags_now(void)
{
    u32 m = 0;
    unsigned v = krk_vox_tag();

    if (v == KRK_VOX_TAG_TEST) m |= 1u << T_VT;
    if (v == KRK_VOX_TAG_SW)   m |= 1u << T_VX;
    if (krk_cwid_active())     m |= 1u << T_CW;
    if (krk_beacon_active())   m |= 1u << T_BN;
    if (krk_sat_active())      m |= 1u << T_SA;
    if (SCRAMBLE_REG != 0)     m |= 1u << T_SC;
    if (krk_mon_on() && krk_channel_analog()) m |= 1u << T_MN;   /* selected side, not the loaded one: no blink under dual watch */
    if (krk_sq_active())       m |= 1u << T_SQ;
    if (krk_parrot_armed() && !krk_channel_analog()) m |= 1u << T_PR;
    return m;
}

static void draw(u32 m)
{
    u16 theme = THEME_TABLE[THEME_IDX & 7];
    int i, n = 0, shown, x;
    char more[4];

    for (i = 0; i < T_N; i++)
        n += (m >> i) & 1;
    shown = n > TAG_MAX ? TAG_MAX : n;
    display_fill_rect(TAG_X0, TAG_Y, TAG_X1, TAG_Y1, KRK_BG);
    if (!shown)
        return;
    x = TAG_MID - (shown * TAG_W + (shown - 1) * TAG_GAP) / 2;
    for (i = 0; i < T_N && shown > (n > TAG_MAX ? 1 : 0); i++) {
        if (!((m >> i) & 1))
            continue;
        krk_text(x, TAG_Y, tag_txt[i], i < T_FIRST_RX ? TAG_RED : theme);
        x += TAG_W + TAG_GAP;
        shown--;
    }
    if (n > TAG_MAX) {                          /* the last slot: how many more */
        more[0] = '+';
        more[1] = (char)('0' + (n - (TAG_MAX - 1)));
        more[2] = 0;
        krk_text(x, TAG_Y, more, theme);
    }
}

/* standby_bottom_hook: stock's bottom line, then the tags. */
void krk_hook_standby_bottom(void)
{
    u32 m;

    stock_standby_bottom();
    m = tags_now();
    draw(m);
    krk_tags_shown = SHOWN_MAGIC | m;
}

/* Maintenance task: redraw when the set changed while the radio sits at standby. */
void krk_tags_tick(void)
{
    u32 m, mode;

    if (krk_screen_owner == KRK_SCR_MAGIC || (CALL_ACTIVE & TX_UP))
        return;
    mode = UI_MODE_BYTE;
    if (mode != 0 && mode != 0x5b)
        return;
    m = SHOWN_MAGIC | tags_now();
    if (m == krk_tags_shown)
        return;
    if (UI_MODE_BYTE != mode)                   /* the screen changed while we looked */
        return;
    draw(m & 0xffff);
    krk_tags_shown = m;
}
