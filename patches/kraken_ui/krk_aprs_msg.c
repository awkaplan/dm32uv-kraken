/*
 * krk_aprs_msg.c - F18 APRS Messages, phase 1 (issue #6): a receive-only inbox of the analog APRS messages
 * addressed to this radio's callsign (docs/aprs-messages.md).
 *
 * Blob kraken_ui4 (built after kraken_ui2, so blob 2 reaches it by address): the AFSK decoder (krk_afsk.c emit, blob 2)
 * hands every FCS-valid frame to krk_aprs_msg_frame (krk_periodic.c), which calls krk_msg_rx through entry 1 of this blob's
 * hook table (0x030e8024, kraken_ui4.ld); the Messages screen descriptor follows at 0x030e8028, a constant in blob 2's APRS
 * rows. The decoder runs in the APRS RX screen (task menu) or the F14 RX Monitor at standby (task init). The two never run at
 * the same time (the monitor yields while any Kraken screen is open), and the Messages screen is a Kraken screen, so
 * the inbox has one writer at a time and never changes under the screen.
 *
 * Kept: ":ADDRESSEE:text[{msgno]" with ADDRESSEE (9 characters, space-padded) equal to the saved APRS callsign-SSID,
 * case-insensitive, also inside a third-party "}SRC>...:" packet (messages from the
 * internet reach RF that way through an igate). Not kept: acks and rejects (a text "ack..."/"rej..." without a message
 * number), other addressees (bulletins "BLN*", "NWS-*" and the rest never equal a saved callsign), empty texts, and a copy
 * of a message already held from the last 30 minutes (same sender, number and text: senders retry, phase 1 never acks).
 *
 * RECEIVE ONLY: nothing here transmits, acks or replies. No PTT, no FM Send RAM, no RF register.
 *
 * State: the kraken_aprs_inbox SRAM claim (256 bytes, through its absolute address like mi_rng_pool), magic-guarded
 * (patch SRAM is not zeroed at power-up). RAM only: lost at power-off. No .bss of its own.
 */
#include "krk.h"
#include "krk_stock.h"

#define MSG_MAX    3
#define FROM_LEN   10               /* "CALL-SS" (third-party senders up to 9 characters) + NUL */
#define NO_LEN     6                /* message number, 1-5 characters + NUL */
#define TEXT_LEN   60               /* 59 characters (an APRS message text is at most 67): what fits 3 entries in 256 B */
#define DUP_SEC    1800             /* a copy within 30 minutes is the same message */
#define IB_MAGIC   0x4753534du      /* "MSSG" */

struct krk_msg {
    u32 sec;                        /* msg_now() when it arrived */
    char from[FROM_LEN];            /* from, no and text are zero-filled past their NUL: an entry compares as words */
    char no[NO_LEN];
    char text[TEXT_LEN];
};
#define MSG_WORDS ((int)sizeof(struct krk_msg) / 4)

struct krk_inbox {
    u32 magic;
    u32 spare;
    u8 n, sel, pad[2];              /* messages held; the screen's current message */
    u32 shown;                      /* the screen's last drawn second (OS_TICKS / 100: only paces the redraw) */
    struct krk_msg m[MSG_MAX];      /* newest first */
};

#ifndef MSG_IB                      /* the host test points it at its own buffer (and has no ELF sections) */
#define MSG_IB ((struct krk_inbox *)KRK_APRS_INBOX_LO)
#define MSG_SECTION(x) __attribute__((section(x), used))
typedef char inbox_fits_claim[sizeof(struct krk_inbox) <= KRK_APRS_INBOX_HI_EXCL - KRK_APRS_INBOX_LO ? 1 : -1];
#else
#define MSG_SECTION(x)
#endif

/* Copies n bytes as a string of at most max - 1 characters, non-printable ones as '.' (d is zero-filled already). */
static void put(char *d, const u8 *s, int n, int max)
{
    int i;

    for (i = 0; i < n && i < max - 1; i++)
        d[i] = (s[i] >= 0x20 && s[i] < 0x7f) ? (char)s[i] : '.';
}

/* Parses one frame's info field (n bytes). src: the sender's call, ended by '>' or NUL. me: the saved callsign (upper case,
 * as krk_call_get returns it). Returns 1 with *e filled (sec not set; e zeroed beforehand) if it is a message for me. */
static int msg_parse(const char *me, const char *src, const u8 *info, int n, struct krk_msg *e)
{
    const u8 *t;
    int i, tn, k;

    if (info[0] == '}') {                           /* third-party: "}SRC>DST,PATH:" then the inner info */
        src = (const char *)info + 1;
        for (i = 1; i < n && info[i] != ':'; i++)
            ;
        info += i + 1;
        n -= i + 1;
    }
    if (n < 11 || info[0] != ':' || info[10] != ':')
        return 0;
    for (k = 9; k > 0 && info[k] == ' '; k--)       /* the addressee without its padding: info[1..k] */
        ;
    for (i = 0; i < k && me[i]; i++) {
        int c = info[1 + i];

        if ((c >= 'a' && c <= 'z' ? c - 32 : c) != me[i])
            return 0;
    }
    if (me[i] || i < k)
        return 0;                                   /* not me (an addressee "CALL-0" is not "CALL" either: APRS writes no "-0") */
    for (i = 0; i < FROM_LEN - 1 && src[i] && src[i] != '>'; i++)
        ;
    put(e->from, (const u8 *)src, i, FROM_LEN);
    t = info + 11;
    tn = n - 11;                                    /* a CR or LF some senders add is shown as '.' */
    for (k = tn; k > 0 && t[k - 1] != '{'; k--)
        ;
    if (k > 0) {                                    /* "{NNNNN" (or "{MM}AA", a reply-ack) */
        for (i = k; i < tn && t[i] != '}'; i++)
            ;
        put(e->no, t + k, i - k, NO_LEN);
        tn = k - 1;
    } else if (tn >= 3) {                           /* an ack or reject carries no number of its own: "ack12", "rej12" */
        k = t[0] << 16 | t[1] << 8 | t[2];
        if (k == ('a' << 16 | 'c' << 8 | 'k') || k == ('r' << 16 | 'e' << 8 | 'j'))
            return 0;
    }
    if (tn == 0)
        return 0;
    put(e->text, t, tn, TEXT_LEN);
    return 1;
}

/* Seconds for ages and the repeat window: the RTC (regs 0..2 sec/min/hour binary, 3|4 days; docs/satellite-radio.md 5.2), not
 * OS_TICKS, which runs about 2x fast at standby under stock power save (docs/fm-send-ram-tx.md 6). rtc_reg is the locked I2C
 * read stock makes from several tasks. A read torn across a minute or an hour is not retried: that one stamp is then off by
 * up to the step. */
static u32 msg_now(void)
{
    return (((rtc_reg(3) | (u32)rtc_reg(4) << 8) * 24 + rtc_reg(2)) * 60 + rtc_reg(1)) * 60 + rtc_reg(0);
}

/* The inbox, initialised if it is noise. */
static struct krk_inbox *ib_get(void)
{
    struct krk_inbox *ib = MSG_IB;

    if (ib->magic != IB_MAGIC) {                    /* only this file writes n, sel: with the magic they are in range */
        ib->n = 0;
        ib->sel = 0;
        ib->magic = IB_MAGIC;
    }
    return ib;
}

/* The decoder's hand-off (hook entry 1): f = the frame without its FCS, len bytes. The info field follows the address that
 * has the extension bit, then control and PID. The clock is read only for a message for me. A message for me is stored newest first, unless the same one (sender,
 * number, text) is held from the last DUP_SEC seconds. */
void krk_msg_rx(const char *line, const unsigned char *f, int len)
{
    struct krk_inbox *ib;
    struct krk_msg e;
    u32 *w = (u32 *)&e, *d;
    char me[16];
    int i, j;

    for (i = 13; i + 7 < len && !(f[i] & 1); i += 7)
        ;
    if (!(f[i] & 1))                                /* (emit() passes at least 16 bytes) */
        return;
    if (!krk_call_get(me))                          /* a cached copy of the flash record (krk_callsign.c): cheap per frame */
        return;
    f += i + 3;
    len -= i + 3;
    for (i = 0; i < MSG_WORDS; i++)
        w[i] = 0;
    if (!msg_parse(me, line, f, len, &e))
        return;
    e.sec = msg_now();
    ib = ib_get();
    d = (u32 *)ib->m;
    for (i = 0; i < ib->n; i++) {
        u32 *o = d + MSG_WORDS * i;

        for (j = 1; j < MSG_WORDS && o[j] == w[j]; j++)
            ;
        if (j == MSG_WORDS && e.sec - o[0] < DUP_SEC)
            return;
    }
    if (ib->n < MSG_MAX)
        ib->n++;
    for (i = MSG_WORDS * ib->n - 1; i >= 0; i--)    /* shift down by one entry (the oldest drops off), the new one first */
        d[i] = i < MSG_WORDS ? w[i] : d[i - MSG_WORDS];
    ib->sel = 0;                                    /* the screen opens on the newest */
}

/* ---- the Messages screen (APRS > Messages, analog list): one message per page, newest first ---------------------- */

static void msg_draw(void)
{
    struct krk_inbox *ib = ib_get();
    const struct krk_msg *m = &ib->m[ib->sel];
    const char *why = 0;
    char b[KRK_LINE_BUF], age[12];
    int row;
    u32 a;

    ib->shown = OS_TICKS / 100;
    if (!krk_call_get(b))
        why = "Set callsign first";
    else if (!ib->n)
        why = "No messages";
    b[0] = 0;
    krk_catn(b, sizeof b, "Messages ");
    if (!why) {                                     /* "Messages 1/3" */
        b[9] = (char)('1' + ib->sel);
        b[10] = '/';
        b[11] = (char)('0' + ib->n);
        b[12] = 0;
    }
    krk_line(0, b, KRK_ACCENT);
    for (row = 1; row < KRK_LINES; row++) {         /* every row, so a shorter page leaves nothing behind */
        const char *p = "";
        u16 fg = KRK_FG;

        b[0] = 0;
        if (why) {
            if (row == 2)
                p = why;
            fg = KRK_DIM;
        } else if (row == 1) {                      /* sender and age ("?" when the RTC was set back) */
            a = msg_now() - m->sec;
            age[0] = '?';
            age[1] = 0;
            if (!(a & 0x80000000u))
                krk_fmt_age(age, a);
            krk_catn(b, sizeof b, m->from);
            krk_catn(b, sizeof b, " ");
            p = age;
            fg = KRK_ACCENT;
        } else if (row < 6) {                       /* the text: 4 rows of 19 hold the 59 characters (zero-filled past its end) */
            p = m->text + (row - 2) * KRK_COLS;
        }
        krk_catn(b, KRK_COLS + 1, p);
        krk_line(row, b, fg);
    }
}

static void msg_tick(u8 scan)
{
    struct krk_inbox *ib = ib_get();

    if (ib->n && (scan == SCAN_UP || scan == SCAN_DOWN))   /* Up: newer, Down: older, both wrap */
        ib->sel = (u8)((ib->sel + (scan == SCAN_UP ? ib->n - 1 : 1)) % ib->n);
    else if (OS_TICKS / 100 == ib->shown)           /* ages move once a second */
        return;
    msg_draw();
}

const struct krk_screen krk_aprs_msg_screen MSG_SECTION(".rodata.krk_msg_screen") = {
    .label = "Messages",
    .enter = msg_draw,                              /* on the newest message after one arrived, else where it was left */
    .tick = msg_tick,
};
