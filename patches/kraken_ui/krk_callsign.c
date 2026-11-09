/*
 * krk_callsign.c - the APRS callsign: an on-radio editor screen plus a validated 16-byte record,
 * persisted in a NEW field inside the stock Radio Settings block (logical block 0x04, offset
 * 0xFE0-0xFEF) through stock's own flash_block_write (docs/settings-field.md).
 *
 * Why there: no data-chip region is free (a blank-check of the data-chip records), so the field lives
 * inside an existing stock record. 0xFE0-0xFEF: no firmware access found by a static scan of every
 * block-4 call site plus the IRAM image; 0x00 in the spec's factory capture, 0xFF in its OEM write,
 * and 0x00 in both of this radio's own CPS snapshots (2026-09-26). A vendor-CPS write sets it to
 * 0xFF, a factory reset to 0x00: both fail the magic/checksum, and the beacon asks again.
 * Wear: one sector rewrite per save (the same as any stock settings change); saves only on OK and
 * only when the value changed. The record is read once and cached.
 */
#include "krk.h"
#include "krk_stock.h"

#define CALL_MAGIC 0x4c4c4143u     /* "CALL" little-endian */
#define CALL_MAX   6

struct call_rec {
    u32 magic;
    char call[CALL_MAX + 1];
    u8 ssid;                        /* 0..15 */
    u8 version;
    u8 path;                        /* analog APRS digipeater path index (krk_aprs_path_name); was pad */
    u8 spare;                       /* CW ID config (krk_cwid.c): bit 7 on, bits 6-0 minutes; was always 0 */
    u8 csum;
};

static struct call_rec cache;       /* last record read from / written to flash */
static u32 cache_state;             /* CACHE_MAGIC once cache holds what flash holds */
#define CACHE_MAGIC 0x43414348u

#define CALL_BLOCK 4
#define CALL_OFF   0xFE0

/* Checksum of a 16-byte flash record (its last byte is the sum): rotate-xor over the first 15, seeded
 * per record kind. Shared with krk_band.c (blob ui2), which seeds it 0xa5. */
u8 krk_rec_csum(const void *rec, u8 seed)
{
    const u8 *p = (const u8 *)rec;
    u8 x = seed;
    unsigned i;

    for (i = 0; i < 15; i++)
        x = (u8)((x << 1 | x >> 7) ^ p[i]);
    return x;
}

static u8 csum(const struct call_rec *r)
{
    return krk_rec_csum(r, 0x5a);
}

static int valid_call(const char *c)
{
    int i, letters = 0, digits = 0;

    for (i = 0; c[i]; i++) {
        if (i >= CALL_MAX) return 0;
        if (c[i] >= 'A' && c[i] <= 'Z') letters++;
        else if (c[i] >= '0' && c[i] <= '9') digits++;
        else return 0;
    }
    return i >= 3 && letters && digits;
}

static int rec_ok(const struct call_rec *r)
{
    return r->magic == CALL_MAGIC && r->call[CALL_MAX] == 0 && r->ssid <= 15 && csum(r) == r->csum
        && valid_call(r->call);
}

/* ---- storage backend: block 0x04 offset 0xFE0, stock read/write path ----------------------- */

static void flash_read_rec(struct call_rec *r)
{
    flash_block_read(flash_block_id_resolve_locked(CALL_BLOCK), CALL_OFF, r, sizeof *r);
}

static int krk_call_load(struct call_rec *r)
{
    if (cache_state != CACHE_MAGIC) {      /* SRAM is noise at power-up: read flash once */
        flash_read_rec(&cache);
        cache_state = CACHE_MAGIC;
    }
    *r = cache;
    return rec_ok(r);
}

static int same(const struct call_rec *a, const struct call_rec *b)
{
    const u8 *x = (const u8 *)a, *y = (const u8 *)b;
    unsigned i;

    for (i = 0; i < sizeof *a; i++)
        if (x[i] != y[i])
            return 0;
    return 1;
}

static int krk_call_save(const struct call_rec *r)
{
    struct call_rec back;

    krk_call_load(&back);
    if (same(&back, r))
        return 1;                           /* unchanged: no flash write */
    flash_block_write(CALL_BLOCK, CALL_OFF, r, sizeof *r);
    flash_read_rec(&back);                  /* verify through the same read path */
    cache = back;
    cache_state = CACHE_MAGIC;
    return same(&back, r);
}

#define KRK_CALL_PERSISTENT 1

typedef char call_rec_is_16_bytes[sizeof(struct call_rec) == 16 ? 1 : -1];

/* ---- API used by the beacon ---------------------------------------------------------------- */

/* The record a config setter edits: the stored one, or, when it holds no valid callsign yet, a fresh
 * one that keeps both config bytes (APRS path, CW ID) if the record itself is intact (magic and
 * checksum), so setting one before a callsign exists does not wipe the other. */
static void load_for_cfg(struct call_rec *r)
{
    struct call_rec z = {0};

    if (krk_call_load(r))
        return;
    z.magic = CALL_MAGIC;
    if (r->magic == CALL_MAGIC && csum(r) == r->csum) {
        z.path = r->path;
        z.spare = r->spare;
    }
    *r = z;
}

/* Analog APRS path (APRS > Upload ID on an analog channel). Stored in the callsign record so the
 * same flash field and checksum cover it; a record without a valid callsign still yields path 0. */
unsigned krk_aprs_cfg_path(void)
{
    struct call_rec r;

    krk_call_load(&r);
    return r.magic == CALL_MAGIC && csum(&r) == r.csum && r.path < KRK_APRS_PATHS ? r.path : 0;
}

int krk_aprs_cfg_set_path(unsigned idx)
{
    struct call_rec r;

    if (idx >= KRK_APRS_PATHS)
        return 0;
    load_for_cfg(&r);
    r.path = (u8)idx;
    r.csum = csum(&r);
    return krk_call_save(&r);
}

/* CW ID config byte (krk_cwid.c, 2026-11-05): kept in the record's spare byte, so the same field and
 * checksum cover it and a CPS write / factory reset (which fail the checksum) turn the ID off. A
 * record without a valid callsign still yields 0 (off). */
unsigned krk_cwid_cfg(void)
{
    struct call_rec r;

    krk_call_load(&r);
    return r.magic == CALL_MAGIC && csum(&r) == r.csum ? r.spare : 0;
}

int krk_cwid_cfg_set(unsigned v)
{
    struct call_rec r;

    load_for_cfg(&r);
    r.spare = (u8)v;
    r.csum = csum(&r);
    return krk_call_save(&r);
}

/* "CALL" or "CALL-SSID" into out (>= 10 bytes). Returns 1 if a valid callsign is set. */
int krk_call_get(char *out)
{
    struct call_rec r;
    char num[12];

    out[0] = 0;                        /* out holds at least 16 bytes ("CALL-SS" + NUL is 10) */
    if (!krk_call_load(&r))
        return 0;
    krk_catn(out, 16, r.call);
    if (r.ssid) {
        krk_catn(out, 16, "-");
        krk_catn(out, 16, krk_itoa(r.ssid, num));
    }
    return 1;
}

/* ---- editor screen ------------------------------------------------------------------------- */

static const char *const taps[10] = {
    "0", "1", "ABC2", "DEF3", "GHI4", "JKL5", "MNO6", "PQRS7", "TUV8", "WXYZ9",
};
static const char cycle[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

static struct {
    char call[CALL_MAX + 1];
    u8 ssid;
    u8 field;                       /* 0 callsign, 1 SSID */
    signed char tap_key;                     /* digit key of the pending multi-tap, -1 none */
    u8 tap_idx, tap_ticks, len, dirty;
    char status[20];
} E;

static int key_digit(u8 scan)
{
    if (scan == SCAN_0) return 0;
    if (scan >= SCAN_1 && scan < SCAN_1 + 9) return scan - SCAN_1 + 1;
    return -1;
}

static void draw(void)
{
    char l[KRK_LINE_BUF], num[12];

    l[0] = 0;
    krk_cat(l, "Call: ");
    krk_cat(l, E.call);
    if (E.field == 0 && E.len < CALL_MAX)
        krk_cat(l, "_");
    krk_line(2, l, E.field == 0 ? KRK_ACCENT : KRK_FG);
    l[0] = 0;
    krk_cat(l, "SSID: ");
    if (E.ssid)
        krk_cat(l, krk_itoa(E.ssid, num));
    else
        krk_cat(l, E.field == 1 ? "_" : "none");
    krk_line(3, l, E.field == 1 ? KRK_ACCENT : KRK_FG);
    krk_line(5, E.status, KRK_DIM);
}

static void commit_tap(void)
{
    E.tap_key = -1;
    E.tap_idx = 0;
}

static void call_enter(void)
{
    struct call_rec r;
    int i;

    for (i = 0; i <= CALL_MAX; i++)
        E.call[i] = 0;
    E.ssid = 0;
    E.len = 0;
    if (krk_call_load(&r)) {
        for (i = 0; r.call[i]; i++)
            E.call[i] = r.call[i];
        E.len = (u8)i;
        E.ssid = r.ssid;
    }
    E.field = 0;
    E.dirty = 0;
    commit_tap();
    E.status[0] = 0;
    krk_catn(E.status, sizeof E.status, KRK_CALL_PERSISTENT ? "" : "RAM only for now");
    krk_line(0, "Callsign: APRS, ID", KRK_DIM);
    krk_line(6, "2-9 letters *=del", KRK_DIM);
    krk_line(7, "#=SSID  OK=save", KRK_DIM);
    krk_line(9, "Back: exit", KRK_DIM);
    draw();
}

static void call_tick(u8 scan)
{
    int d = key_digit(scan);

    if (scan == SCAN_NONE) {
        if (E.tap_key >= 0 && ++E.tap_ticks >= 10)  /* 1 s without a repeat: letter accepted */
            commit_tap();
        return;
    }
    E.status[0] = 0;
    if (scan == SCAN_OK) {
        struct call_rec r;
        int i;
        commit_tap();
        if (!valid_call(E.call)) {
            krk_catn(E.status, sizeof E.status, "Need 3-6 A-Z/0-9");
        } else {
            for (i = 0; i <= CALL_MAX; i++)
                r.call[i] = E.call[i];
            r.magic = CALL_MAGIC;
            r.ssid = E.ssid;
            r.version = 1;
            r.path = (u8)krk_aprs_cfg_path();   /* keep the analog APRS path across callsign edits */
            r.spare = (u8)krk_cwid_cfg();       /* keep the CW ID setting across callsign edits */
            r.csum = csum(&r);
            krk_catn(E.status, sizeof E.status, krk_call_save(&r) ? (KRK_CALL_PERSISTENT ? "Saved" : "Saved (RAM only)") : "Save FAILED");
            E.dirty = 0;
        }
    } else if (scan == SCAN_HASH) {
        commit_tap();
        E.field ^= 1;
    } else if (scan == SCAN_STAR) {
        commit_tap();
        if (E.field == 0 && E.len)
            E.call[--E.len] = 0;
        else if (E.field == 1)
            E.ssid /= 10;
        E.dirty = 1;
    } else if (E.field == 1 && d >= 0) {
        u32 v = E.ssid * 10u + (u32)d;
        E.ssid = (u8)(v <= 15 ? v : d);
        E.dirty = 1;
    } else if (E.field == 0 && d >= 0) {
        const char *set = taps[d];
        int n = 0;
        while (set[n]) n++;
        if (E.tap_key == d && E.len) {              /* same key again: next letter in place */
            E.tap_idx = (u8)((E.tap_idx + 1) % n);
            E.call[E.len - 1] = set[E.tap_idx];
        } else if (E.len < CALL_MAX) {
            E.tap_key = (signed char)d;
            E.tap_idx = 0;
            E.call[E.len++] = set[0];
        }
        E.tap_ticks = 0;
        E.dirty = 1;
    } else if ((scan == SCAN_UP || scan == SCAN_DOWN) && E.field == 0) {
        int i = 0, n = sizeof cycle - 1;
        commit_tap();
        if (!E.len) {
            E.call[E.len++] = 'A';
        } else {
            char *c = &E.call[E.len - 1];
            while (i < n && cycle[i] != *c) i++;
            i = scan == SCAN_UP ? (i + 1) % n : (i + n - 1) % n;
            *c = cycle[i];
        }
        E.dirty = 1;
    }
    draw();
}

const struct krk_screen krk_callsign_screen = {
    .label = "Callsign",
    .enter = call_enter,
    .tick = call_tick,
};
