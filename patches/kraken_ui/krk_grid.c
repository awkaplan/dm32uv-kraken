/*
 * krk_grid.c - F4: GPS position as a Maidenhead grid locator (8 characters), plus the raw fix.
 *
 * Source: stock's copy of the last checksum-valid $GPRMC/$BDRMC/$GNRMC sentence with status 'A'.
 * gps_nmea_rmc_gga_sentence_parser @0x03012758 (runs in "task init", main_ui_background_task_loop)
 * zeroes GPS_RMC (0x96 bytes) and copies the sentence there, then sets bit 6 of GPS_STATUS;
 * on a void fix or bad checksum it clears that bit. Literal pools: 0x03012940/0x03012ac4 ->
 * 0x00038c79, 0x03012ab8/0x03012928 -> 0x00038c6c.
 *
 * That parser runs in another task and may rewrite the buffer while this screen reads it, so the
 * screen copies it and re-checks the NMEA checksum on the copy; a torn read is simply skipped.
 * All arithmetic is integer, in 1e-4 arc-minute units (360 deg = 216,000,000: fits in 32 bits).
 */
#include "krk.h"
#include "krk_stock.h"

#define GPS_RMC_MAX  0x96
#define GPS_RMC_VALID 0x40

#define MIN_UNITS 10000         /* units per arc-minute */

static struct {
    u32 seen;                   /* sentences decoded since entry */
    u32 hash;                   /* last sentence drawn */
    u8 shown_nofix;
} g;

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Copies the sentence and checks its checksum. Returns the length, or 0. */
static int copy_rmc(char *buf)
{
    int i, n = 0;
    u8 x = 0;

    if (!(GPS_STATUS & GPS_RMC_VALID))
        return 0;
    for (i = 0; i < GPS_RMC_MAX - 1; i++) {
        char c = GPS_RMC[i];
        if (c == 0 || c == '\r' || c == '\n')
            break;
        buf[n++] = c;
    }
    buf[n] = 0;
    /* The parser matches the talker token against "$GPRMC" from the same ring index the copy
     * starts at, so the copy should begin with '$'; accept it without one too (one static
     * reading said so, docs/aprs-positions.md). The checksum covers what follows the '$'. */
    if (n < 10)
        return 0;
    for (i = buf[0] == '$' ? 1 : 0; i < n && buf[i] != '*'; i++)
        x ^= (u8)buf[i];
    /* need "*HH" inside the copy */
    if (i + 2 >= n || buf[i] != '*' || hexval(buf[i + 1]) < 0 || hexval(buf[i + 2]) < 0)
        return 0;
    if (((hexval(buf[i + 1]) << 4) | hexval(buf[i + 2])) != x)
        return 0;
    return n;
}

/* Field k (0 = talker/type) of a comma-separated sentence, copied into out (max len-1 chars). */
static void field(const char *s, int k, char *out, int len)
{
    int n = 0;

    while (k && *s) {
        if (*s++ == ',')
            k--;
    }
    while (*s && *s != ',' && *s != '*' && n < len - 1)
        out[n++] = *s++;
    out[n] = 0;
}

/* "ddmm.mmmm" / "dddmm.mmmm" -> 1e-4 arc-minutes; deg_digits is 2 or 3. -1 if malformed. */
static int parse_angle(const char *s, int deg_digits)
{
    int i, deg = 0, min = 0, frac = 0, scale = MIN_UNITS;

    for (i = 0; i < deg_digits; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;
        deg = deg * 10 + (s[i] - '0');
    }
    s += deg_digits;
    for (i = 0; i < 2; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;
        min = min * 10 + (s[i] - '0');
    }
    s += 2;
    if (*s == '.') {
        s++;
        while (*s >= '0' && *s <= '9' && scale > 1) {
            scale /= 10;
            frac += (*s++ - '0') * scale;
        }
    }
    return (deg * 60 + min) * MIN_UNITS + frac;
}

/* lon/lat in 1e-4 arc-minutes, already offset to 0..360 / 0..180 degrees. */
static void maidenhead(int lon, int lat, char *out)
{
    /* field 20x10 deg, square 2x1 deg, subsquare 5'x2.5', extended 30"x15" */
    static const int lon_step[4] = {20 * 60 * MIN_UNITS, 2 * 60 * MIN_UNITS, 5 * MIN_UNITS, MIN_UNITS / 2};
    static const int lat_step[4] = {10 * 60 * MIN_UNITS, 1 * 60 * MIN_UNITS, 5 * MIN_UNITS / 2, MIN_UNITS / 4};
    static const char base[4] = {'A', '0', 'a', '0'};
    int k;

    for (k = 0; k < 4; k++) {
        int a = lon / lon_step[k], b = lat / lat_step[k];
        out[2 * k] = (char)(base[k] + a);
        out[2 * k + 1] = (char)(base[k] + b);
        lon -= a * lon_step[k];
        lat -= b * lat_step[k];
    }
    out[8] = 0;
}

int krk_gps_get(struct krk_gps_fix *f)
{
    char s[GPS_RMC_MAX], f2[4];
    int lat, lon;

    if (!copy_rmc(s))
        return 0;
    field(s, 3, f->lat_s, sizeof f->lat_s);
    field(s, 4, f2, sizeof f2);
    f->ns = f2[0];
    lat = parse_angle(f->lat_s, 2);
    field(s, 5, f->lon_s, sizeof f->lon_s);
    field(s, 6, f2, sizeof f2);
    f->ew = f2[0];
    lon = parse_angle(f->lon_s, 3);
    if (lat < 0 || lon < 0 || (f->ns != 'N' && f->ns != 'S') || (f->ew != 'E' && f->ew != 'W'))
        return 0;
    if (lat > 90 * 60 * MIN_UNITS || lon > 180 * 60 * MIN_UNITS)
        return 0;
    f->lat = f->ns == 'S' ? -lat : lat;
    f->lon = f->ew == 'W' ? -lon : lon;
    field(s, 1, f->utc, sizeof f->utc);
    return 1;
}

static void grid_enter(void)
{
    g.seen = 0;
    g.hash = 0;
    g.shown_nofix = 0;
    krk_line(0, "GPS / Grid", KRK_DIM);
    krk_line(9, "Back: exit", KRK_DIM);
}

static void grid_tick(u8 scan)
{
    char s[GPS_RMC_MAX], f[16], f2[4], line[KRK_LINE_BUF], loc[9];
    int n, lat, lon;
    u32 h = 2166136261u;

    (void)scan;
    n = copy_rmc(s);
    if (!n) {
        if (!(GPS_STATUS & GPS_RMC_VALID) && !g.shown_nofix) {
            krk_line(1, "No fix", KRK_WARN);
            krk_line(2, "(GPS off, or no", KRK_DIM);
            krk_line(3, " RMC with status A)", KRK_DIM);
            g.shown_nofix = 1;
            g.hash = 0;
        }
        return;
    }
    for (int i = 0; i < n; i++)
        h = (h ^ (u8)s[i]) * 16777619u;
    if (h == g.hash)
        return;
    g.hash = h;
    if (g.shown_nofix) {
        krk_line(3, "", KRK_BG);    /* the "No fix" text used lines 1-3 (hardware, 2026-10-15) */
        g.shown_nofix = 0;
    }
    g.seen++;

    field(s, 3, f, sizeof f);
    lat = parse_angle(f, 2);
    field(s, 4, f2, sizeof f2);
    if (lat < 0 || (f2[0] != 'N' && f2[0] != 'S'))
        return;
    lat = (f2[0] == 'S' ? -lat : lat) + 90 * 60 * MIN_UNITS;
    field(s, 5, f, sizeof f);
    lon = parse_angle(f, 3);
    field(s, 6, f2, sizeof f2);
    if (lon < 0 || (f2[0] != 'E' && f2[0] != 'W'))
        return;
    lon = (f2[0] == 'W' ? -lon : lon) + 180 * 60 * MIN_UNITS;
    if (lat < 0 || lat >= 180 * 60 * MIN_UNITS || lon < 0 || lon >= 360 * 60 * MIN_UNITS)
        return;

    maidenhead(lon, lat, loc);
    krk_line(1, "Grid", KRK_FG);
    display_fill_rect(0, KRK_Y0 + 2 * KRK_LINE_H, KRK_SCREEN_W, KRK_Y0 + 3 * KRK_LINE_H, KRK_BG);
    krk_text(KRK_X0 + 24, KRK_Y0 + 2 * KRK_LINE_H, loc, KRK_ACCENT);

    line[0] = 0;
    field(s, 3, f, sizeof f);
    krk_cat(line, f);
    field(s, 4, f2, sizeof f2);
    krk_cat(line, " ");
    krk_cat(line, f2);
    krk_line(4, line, KRK_FG);
    line[0] = 0;
    field(s, 5, f, sizeof f);
    krk_cat(line, f);
    field(s, 6, f2, sizeof f2);
    krk_cat(line, " ");
    krk_cat(line, f2);
    krk_line(5, line, KRK_FG);

    /* field 1 = hhmmss(.ss) UTC, field 9 = ddmmyy */
    field(s, 1, f, sizeof f);
    line[0] = 0;
    krk_cat(line, "UTC ");
    if (f[0]) {
        char t[9] = {f[0], f[1], ':', f[2], f[3], ':', f[4], f[5], 0};
        krk_cat(line, t);
    }
    krk_line(7, line, KRK_DIM);
}

const struct krk_screen krk_grid_screen = {
    .label = "GPS Grid",
    .enter = grid_enter,
    .tick = grid_tick,
};
