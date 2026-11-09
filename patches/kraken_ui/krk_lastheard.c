/*
 * krk_lastheard.c - F1: last-heard list of received DMR voice calls (RAM only; lost at power-off).
 *
 * Hooks (all traced in docs/last-heard-hooks.md; each wraps a stock call and then records):
 *   call start  0x030208ba  rxvoice_task_entry, flag-0x2 block: bsr read_byte_field_030238b4
 *               (a no-argument byte read). The caller uses only r0 after it. Fires on every
 *               accepted voice header (late entry included), so an open entry for the same
 *               src/dst is refreshed instead of duplicated.
 *   call end    0x03020984  same task, flag-0x4 block: bsr modem_control_state_stop (void(void)).
 *               Closes the open entry and records the duration.
 *   alias       0x0305edd8  event_flags9_dispatch_and_name_encode: bsr memcpy_bytes_iram(dst, src,
 *               0x20) copying the decoded talker alias. Attached to the open entry whose source ID
 *               matches the current call. It arrives hundreds of ms after call start.
 * State at call start (rxvoice state block): dst u32 0x3bd30, src u32 0x3bd34, call type 0x3bd2c
 * (1 group, 0 private), dst class 0x3bd2d (3 = all-call). The CC and TS are the channel's
 * configured values (byte 0x3e1e9: CC = low nibble, TS = bit 4), which a call must match to be
 * heard. The RSSI is stock's 10-sample mean (raw, 0.5 dB/step), refreshed by later headers.
 *
 * Time: CPU_TS_TmrRd (0x0306ced4) returns a monotonic counter (RAM 0x426d0) whose rate is not
 * traced. The screen calibrates it against its own OSTimeDly-paced loop. A 32-bit counter can
 * wrap, so very old ages may be wrong.
 *
 * Names are resolved at draw time in the menu task with contact_name_lookup_or_format
 * (0x03034d58: r0 unused, r1 id, r2 buf >= 17, r3 type 3 private / 4 group; returns 1 and the
 * contact name, or 0 and the id as "%d"). It reads flash under a mutex, so never from a hook.
 */
#include "krk.h"
#include "krk_stock.h"

#define CH_CC_TS    ((volatile u8 *)0x0003e1e9)

#define read_byte_field_030238b4 ((u32 (*)(void))0x030238b4)
#define modem_control_state_stop ((void (*)(void))0x030293ac)
#define memcpy_bytes_iram ((void (*)(void *dst, const void *src, u32 n))0x0002b338)
#define cpu_ts ((u32 (*)(void))0x0306ced4)
#define contact_name_lookup_or_format ((int (*)(int unused, u32 id, char *buf, int type))0x03034d58)

#define LH_N        16
#define LH_ALIAS    20
#define LH_MAGIC    0x4c483031      /* "LH01" */

enum { LH_GROUP = 1, LH_PRIVATE = 2, LH_ALL = 3 };

struct lh_entry {
    u32 src, dst;
    u32 t_start, t_last;
    u8 kind, cc, ts, rssi;
    u8 open, hits, have_alias, pad;
    char alias[LH_ALIAS];
};

static struct {
    u32 magic;
    u32 seq;                        /* bumped on every change; the screen redraws on change */
    u8 head;                        /* index of the newest entry */
    u8 count;
    struct lh_entry e[LH_N];
} lh;

static void lh_init(void)
{
    if (lh.magic != LH_MAGIC) {     /* SRAM holds power-on noise on a real radio */
        lh.magic = LH_MAGIC;
        lh.seq = 0;
        lh.head = 0;
        lh.count = 0;
    }
}

static struct lh_entry *newest(void)
{
    return lh.count ? &lh.e[lh.head] : 0;
}

static void on_call_start(void)
{
    u32 src = RX_SRC, dst = RX_DST, now = cpu_ts();
    struct lh_entry *e;
    u8 cfg = *CH_CC_TS;

    lh_init();
    e = newest();
    if (e && e->open && e->src == src && e->dst == dst) {
        e->t_last = now;            /* repeated header / late entry of the same call */
        e->rssi = *STOCK_RSSI_MEAN;
        if (e->hits < 255)
            e->hits++;
        lh.seq++;
        return;
    }
    if (e)
        e->open = 0;
    lh.head = (u8)((lh.head + 1) % LH_N);
    if (lh.count < LH_N)
        lh.count++;
    e = &lh.e[lh.head];
    e->src = src;
    e->dst = dst;
    e->t_start = e->t_last = now;
    e->kind = RX_DSTCLASS == 3 ? LH_ALL : (RX_TYPE == 1 ? LH_GROUP : LH_PRIVATE);
    e->cc = cfg & 0x0f;
    e->ts = (cfg & 0x10) ? 2 : 1;
    e->rssi = *STOCK_RSSI_MEAN;
    e->open = 1;
    e->hits = 1;
    e->have_alias = 0;
    e->alias[0] = 0;
    lh.seq++;
}

u32 krk_hook_lh_start(void)
{
    u32 r = read_byte_field_030238b4();

    on_call_start();
    krk_ext_hook(0);                /* extension blob: F9 parrot capture start (krk.h hook table) */
    return r;
}

void krk_hook_lh_end(void)
{
    struct lh_entry *e;

    modem_control_state_stop();
    lh_init();
    e = newest();
    if (e && e->open) {
        e->open = 0;
        e->t_last = cpu_ts();
        lh.seq++;
    }
    krk_ext_hook(1);                /* F9 parrot capture end */
}

/* Channel change (channel_change_apply_dispatcher, manifest aprs_mode_hook, called from krk_stubs.S with the caller's
 * registers saved): extension hook slot 2. */
void krk_hook_chan_change(void)
{
    krk_ext_hook(2);
}

void krk_hook_lh_alias(void *dst, const void *src, u32 n)
{
    struct lh_entry *e;
    const char *a = (const char *)dst;
    int i;

    memcpy_bytes_iram(dst, src, n);
    lh_init();
    e = newest();
    if (!e || !e->open || e->src != RX_SRC)
        return;
    for (i = 0; i < LH_ALIAS - 1 && i < (int)n && a[i]; i++)
        e->alias[i] = (a[i] >= 0x20 && a[i] < 0x7f) ? a[i] : '?';
    e->alias[i] = 0;
    e->have_alias = i > 0;
    lh.seq++;
}

/* ---- screen ------------------------------------------------------------------------------- */

#define ROWS_PER_ENTRY 2
#define ENTRIES_PER_PAGE 4

static struct {
    u32 drawn_seq;
    u8 top;                         /* index (0 = newest) of the first entry shown */
    u8 calibrated;
    u8 cal_ticks;
    u32 cal_t0, per_sec;            /* counter units per second */
    u32 last_age_draw;
} s;

static void fmt_age(char *out, u32 t)    /* out: 12 bytes (the caller's num[]) */
{
    out[0] = 0;
    if (!s.calibrated || !s.per_sec)
        krk_catn(out, 12, "--");
    else
        krk_fmt_age(out, (cpu_ts() - t) / s.per_sec);
}

static void draw_entry(int slot, const struct lh_entry *e)
{
    char a[KRK_LINE_BUF], b[KRK_LINE_BUF], num[12], name[24];
    int y = KRK_Y0 + (1 + slot * ROWS_PER_ENTRY) * KRK_LINE_H;

    /* line 1: talker alias if received, else contact name, else the ID */
    a[0] = 0;
    if (e->have_alias) {
        krk_cat(a, e->alias);
    } else {
        name[0] = 0;
        contact_name_lookup_or_format(0, e->src, name, 3);
        name[16] = 0;
        krk_cat(a, name[0] ? name : krk_itoa((int)e->src, num));
    }
    krk_pad(a);
    krk_text(KRK_X0, y, a, e->open ? KRK_ACCENT : KRK_FG);

    /* line 2: destination, slot, colour code, age */
    b[0] = 0;
    krk_cat(b, e->kind == LH_GROUP ? "TG" : e->kind == LH_ALL ? "ALL" : "PC");
    if (e->kind != LH_ALL)
        krk_cati(b, (int)e->dst);
    krk_cat(b, " T");
    krk_cati(b, e->ts);
    krk_cat(b, " C");
    krk_cati(b, e->cc);
    krk_cat(b, " ");
    fmt_age(num, e->open ? e->t_start : e->t_last);
    krk_cat(b, num);
    krk_pad(b);
    krk_text(KRK_X0, y + KRK_LINE_H, b, KRK_DIM);
}

static void draw_list(void)
{
    int k;

    lh_init();
    s.drawn_seq = lh.seq;
    if (!lh.count) {
        krk_line(1, "No calls heard yet", KRK_DIM);
        return;
    }
    if (s.top >= lh.count)
        s.top = 0;
    for (k = 0; k < ENTRIES_PER_PAGE; k++) {
        int idx = s.top + k;
        if (idx < lh.count) {
            draw_entry(k, &lh.e[(lh.head + LH_N - idx) % LH_N]);
        } else {
            krk_line(1 + k * ROWS_PER_ENTRY, "", KRK_BG);
            krk_line(2 + k * ROWS_PER_ENTRY, "", KRK_BG);
        }
    }
}

static void lh_enter(void)
{
    s.top = 0;
    s.calibrated = 0;
    s.cal_ticks = 0;
    s.cal_t0 = cpu_ts();
    s.last_age_draw = 0;
    krk_line(0, "Last heard", KRK_DIM);
    krk_line(9, "Up/Dn scroll Back", KRK_DIM);
    draw_list();
}

static void lh_tick(u8 scan)
{
    int redraw = 0;

    if (!s.calibrated && ++s.cal_ticks >= 1000 / KRK_TICK_MS) {
        s.per_sec = cpu_ts() - s.cal_t0;     /* counter units in ~1 s of OSTimeDly */
        s.calibrated = 1;
        redraw = 1;
    }
    if (scan == SCAN_DOWN && s.top + ENTRIES_PER_PAGE < lh.count) {
        s.top++;
        redraw = 1;
    } else if (scan == SCAN_UP && s.top > 0) {
        s.top--;
        redraw = 1;
    }
    if (lh.seq != s.drawn_seq)
        redraw = 1;
    if (++s.last_age_draw >= 1000 / KRK_TICK_MS) {   /* ages move once a second */
        s.last_age_draw = 0;
        redraw = 1;
    }
    if (redraw)
        draw_list();
}

const struct krk_screen krk_lastheard_screen = {
    .label = "Last Heard",
    .enter = lh_enter,
    .tick = lh_tick,
};
