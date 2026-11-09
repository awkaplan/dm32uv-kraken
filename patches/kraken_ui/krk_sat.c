/*
 * krk_sat.c - see krk_sat.h and docs/satellite-mode.md.
 *
 * SGP4 is the near-earth branch of Vallado et al., "Revisiting Spacetrack Report #3" (AIAA 2006-6753),
 * WGS-72 constants, "improved" operation mode, ported to this library's private math. All maths
 * is double. Private math (accurate to ~1e-15, enough for 1e-9 rad arguments):
 *   sat_sincos  quadrant reduction (Cody-Waite pi/2) + degree-15/16 Taylor on |r| <= pi/4
 *   sat_atan2   reciprocal + 15-degree shift to |x| <= 0.268, 14-term series
 *   sat_sqrt    exponent-halving seed + 4 Newton steps (no libm, no div by zero)
 */
#include "krk_sat.h"

#ifdef KRK_SAT_STATS
unsigned krk_sat_n_sincos, krk_sat_n_atan, krk_sat_n_sqrt, krk_sat_n_prop;
#define STAT(x) (x)++
#else
#define STAT(x) ((void)0)
#endif

#define PI      3.14159265358979323846
#define TWOPI   6.28318530717958647692
#define DEG2RAD 0.01745329251994329577
#define RAD2DEG 57.2957795130823208768

/* WGS-72 (what the TLE mean elements are fitted with) */
#define RE      6378.135
#define XKE     0.0743669161331734132   /* 60 / sqrt(RE^3 / mu) */
#define J2      0.001082616
#define J3      (-0.00000253881)
#define J4      (-0.00000165597)
#define J3OJ2   (J3 / J2)
#define VKMPS   (RE * XKE / 60.0)       /* ER/min -> km/s */

#define OMEGA_E 7.2921158553e-5         /* rad/s */
#define C_KMS   299792.458

/* ------------------------------------------------------------------ private math */

static double sat_sqrt(double a)
{
    union { double d; unsigned long long u; } c;
    double x;
    int i;
    if (a <= 0.0) return 0.0;
    STAT(krk_sat_n_sqrt);
    c.d = a;
    c.u = (c.u >> 1) + 0x1ff8000000000000ULL;   /* halve the exponent: seed within ~4% */
    x = c.d;
    for (i = 0; i < 4; i++) x = 0.5 * (x + a / x);
    return x;
}

static void sat_sincos(double x, double *ps, double *pc)
{
    double k = x * 0.63661977236758134308;      /* 2/pi */
    long n = (long)(k + (k < 0 ? -0.5 : 0.5));
    double r = x - n * 1.57079632673412561417e+00 - n * 6.07710050650619224932e-11;
    double z = r * r, s, c;
    STAT(krk_sat_n_sincos);
    s = r * (1.0 + z * (-1.0 / 6 + z * (1.0 / 120 + z * (-1.0 / 5040 + z * (1.0 / 362880
        + z * (-1.0 / 39916800 + z * (1.0 / 6227020800.0 + z * (-1.0 / 1307674368000.0))))))));
    c = 1.0 + z * (-0.5 + z * (1.0 / 24 + z * (-1.0 / 720 + z * (1.0 / 40320 + z * (-1.0 / 3628800
        + z * (1.0 / 479001600 + z * (-1.0 / 87178291200.0 + z * (1.0 / 20922789888000.0))))))));
    switch (n & 3) {
    case 0:  *ps = s;  *pc = c;  break;
    case 1:  *ps = c;  *pc = -s; break;
    case 2:  *ps = -s; *pc = -c; break;
    default: *ps = -c; *pc = s;  break;
    }
}

static double sat_atan(double x)
{
    double a = x < 0 ? -x : x, off = 0.0, z, p;
    int inv = a > 1.0;
    STAT(krk_sat_n_atan);
    if (inv) a = 1.0 / a;
    if (a > 0.26794919243112270647) {           /* tan 15 deg: atan a = 30 deg + atan((a*sqrt3-1)/(sqrt3+a)) */
        a = (a * 1.73205080756887729353 - 1.0) / (1.73205080756887729353 + a);
        off = PI / 6;
    }
    z = a * a;
    p = a * (1.0 + z * (-1.0 / 3 + z * (1.0 / 5 + z * (-1.0 / 7 + z * (1.0 / 9 + z * (-1.0 / 11
        + z * (1.0 / 13 + z * (-1.0 / 15 + z * (1.0 / 17 + z * (-1.0 / 19 + z * (1.0 / 21
        + z * (-1.0 / 23 + z * (1.0 / 25 + z * (-1.0 / 27))))))))))))));
    p += off;
    if (inv) p = PI / 2 - p;
    return x < 0 ? -p : p;
}

static double sat_atan2(double y, double x)
{
    double a;
    if (x == 0.0) return y > 0 ? PI / 2 : (y < 0 ? -PI / 2 : 0.0);
    a = sat_atan(y / x);
    if (x < 0) a += (y >= 0) ? PI : -PI;
    return a;
}

static double sat_mod2pi(double x)              /* to [0, 2pi) */
{
    long n = (long)(x * (1.0 / TWOPI));
    x -= n * TWOPI;
    if (x < 0) x += TWOPI;
    return x;
}

/* ------------------------------------------------------------------ time */

#define EPOCH_OFS 7306      /* days from 1949-12-31 to 1970-01-01 is 7305; day 0 here is 1949-12-31 */

long krk_days_from_civil(long y, int m, int d)  /* days since 1970-01-01; krk_satrf.c shares it */
{
    long era, yoe, doy, doe;
    y -= m <= 2;
    era = (y >= 0 ? y : y - 399) / 400;
    yoe = y - era * 400;
    doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

double krk_sat_time(int year, int mon, int day, int hour, int min, double sec)
{
    return (double)(krk_days_from_civil(year, mon, day) + EPOCH_OFS)
           + (hour * 3600.0 + min * 60.0 + sec) * (1.0 / 86400.0);
}

void krk_civil_from_days(long z, int *year, int *mon, int *day)     /* z = days since 1970-01-01 */
{
    long era, doe, yoe, y, doy, mp;
    z += 719468;
    era = (z >= 0 ? z : z - 146096) / 146097;
    doe = z - era * 146097;
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    y = yoe + era * 400;
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    mp = (5 * doy + 2) / 153;
    *day = (int)(doy - (153 * mp + 2) / 5 + 1);
    *mon = (int)(mp < 10 ? mp + 3 : mp - 9);
    *year = (int)(y + (*mon <= 2));
}

void krk_sat_civil(double t, int *year, int *mon, int *day, int *hour, int *min, int *sec)
{
    long d = (long)t;
    long s = (long)((t - (double)d) * 86400.0 + 1e-6);      /* 1 us guard against 59.9999999 */
    if (s >= 86400) { s -= 86400; d++; }
    krk_civil_from_days(d - EPOCH_OFS, year, mon, day);
    *hour = (int)(s / 3600);
    *min = (int)(s % 3600 / 60);
    *sec = (int)(s % 60);
}

static double gmst(double t)                    /* IAU-82, rad, UTC taken as UT1 */
{
    double tut1 = (t - 18263.5) * (1.0 / 36525.0);  /* JD 2451545.0 is t = 18263.5 */
    double s = ((-6.2e-6 * tut1 + 0.093104) * tut1 + (876600.0 * 3600.0 + 8640184.812866)) * tut1
               + 67310.54841;                   /* seconds of time */
    return sat_mod2pi(s * (DEG2RAD / 240.0));
}

/* ------------------------------------------------------------------ TLE */

static int tle_len_ok(const char *l)
{
    int i;
    for (i = 0; i < 69; i++)
        if (!l[i]) return 0;
    return 1;
}

static int tle_checksum(const char *l)
{
    int i, sum = 0;
    for (i = 0; i < 68; i++) {
        if (l[i] >= '0' && l[i] <= '9') sum += l[i] - '0';
        else if (l[i] == '-') sum++;
    }
    return l[68] - '0' == sum % 10;
}

/* Columns a..b (1-based, inclusive): spaces, sign, digits and one '.'. implied_dp: the digits
 * are the fraction of a number with the decimal point assumed in front. 0 on success. */
static int tle_num(const char *l, int a, int b, int implied_dp, double *out)
{
    int i, neg = 0, nd = 0, seen_dp = 0, started = 0;
    double v = 0.0, scale = 1.0;
    for (i = a - 1; i < b; i++) {
        char c = l[i];
        if (c == ' ' && !started) continue;
        if (!started && (c == '-' || c == '+')) { neg = c == '-'; started = 1; continue; }
        started = 1;
        if (c == '.' && !seen_dp) { seen_dp = 1; continue; }
        if (c < '0' || c > '9') return -1;
        v = v * 10.0 + (c - '0');
        nd++;
        if (seen_dp) scale *= 10.0;
    }
    if (!nd) return -1;
    if (implied_dp) { int k; scale = 1.0; for (k = 0; k < nd; k++) scale *= 10.0; }
    *out = (neg ? -v : v) / scale;
    return 0;
}

static int tle_bstar(const char *l, double *out)    /* cols 54-61: " 12345-3" = 0.12345e-3 */
{
    double m, e;
    int i, neg = l[53] == '-', eneg = l[59] == '-';
    if (tle_num(l, 55, 59, 1, &m) || tle_num(l, 61, 61, 0, &e)) return -1;
    for (i = 0; i < (int)e; i++) m = eneg ? m / 10.0 : m * 10.0;
    *out = neg ? -m : m;
    return 0;
}

static void sgp4_init(struct krk_sat *s);

int krk_sat_init(struct krk_sat *s, const char *l1, const char *l2)
{
    double yy, doy, inc, ra, ecc, ap, ma, mm, cat1, cat2;
    long year;
    if (!tle_len_ok(l1) || !tle_len_ok(l2) || l1[0] != '1' || l2[0] != '2') return KRK_SAT_E_FORMAT;
    if (!tle_checksum(l1) || !tle_checksum(l2)) return KRK_SAT_E_CHECKSUM;
    if (tle_num(l1, 3, 7, 0, &cat1) || tle_num(l2, 3, 7, 0, &cat2) || cat1 != cat2)
        return KRK_SAT_E_MISMATCH;
    if (tle_num(l1, 19, 20, 0, &yy) || tle_num(l1, 21, 32, 0, &doy) || tle_bstar(l1, &s->bstar) ||
        tle_num(l2, 9, 16, 0, &inc) || tle_num(l2, 18, 25, 0, &ra) || tle_num(l2, 27, 33, 1, &ecc) ||
        tle_num(l2, 35, 42, 0, &ap) || tle_num(l2, 44, 51, 0, &ma) || tle_num(l2, 53, 63, 0, &mm))
        return KRK_SAT_E_FORMAT;
    year = (long)yy < 57 ? 2000 + (long)yy : 1900 + (long)yy;
    s->catnr = (unsigned)cat1;
    s->epoch = (double)(krk_days_from_civil(year, 1, 1) + EPOCH_OFS) + doy - 1.0;
    s->no = mm * (TWOPI / 1440.0);
    s->ecco = ecc;
    s->inclo = inc * DEG2RAD;
    s->nodeo = ra * DEG2RAD;
    s->argpo = ap * DEG2RAD;
    s->mo = ma * DEG2RAD;
    if (s->no <= 0.0 || ecc >= 1.0) return KRK_SAT_E_FORMAT;
    sgp4_init(s);
    /* s->no is now the Brouwer mean motion, which is what the reference tests (period >= 225 min) */
    return TWOPI / s->no >= 225.0 ? KRK_SAT_E_DEEP : 0;
}

double krk_sat_epoch(const struct krk_sat *s) { return s->epoch; }

/* ------------------------------------------------------------------ SGP4 */

static double cbrt_pos(double q)
{
    double y = 1.0, yn;
    int i;
    for (i = 0; i < 60; i++) {
        yn = (2.0 * y + q / (y * y)) * (1.0 / 3.0);
        if (yn - y < 1e-15 && y - yn < 1e-15) { y = yn; break; }
        y = yn;
    }
    return y;
}

static void sgp4_init(struct krk_sat *s)
{
    double ecco = s->ecco, no = s->no;
    double cosio, cosio2, sinio, eccsq, omeosq, rteosq, ak, d1, del, adel, ao, po, posq, rp;
    double con42, ss, qzms24, perige, sfour, pinvsq, tsi, eta, etasq, eeta, psisq, coef, coef1;
    double cc1, cc2, cc3, cc4, cosio4, temp1, temp2, temp3, tmp, sinx, cosx;

    sat_sincos(s->inclo, &sinio, &cosio);
    cosio2 = cosio * cosio;
    eccsq = ecco * ecco;
    omeosq = 1.0 - eccsq;
    rteosq = sat_sqrt(omeosq);

    /* initl: Kozai -> Brouwer mean motion */
    ak = cbrt_pos((XKE / no) * (XKE / no));
    d1 = 0.75 * J2 * (3.0 * cosio2 - 1.0) / (rteosq * omeosq);
    del = d1 / (ak * ak);
    adel = ak * (1.0 - del * del - del * (1.0 / 3.0 + 134.0 * del * del / 81.0));
    del = d1 / (adel * adel);
    no = no / (1.0 + del);
    ao = cbrt_pos((XKE / no) * (XKE / no));
    po = ao * omeosq;
    con42 = 1.0 - 5.0 * cosio2;
    s->con41 = -con42 - cosio2 - cosio2;
    posq = po * po;
    pinvsq = 1.0 / posq;
    rp = ao * (1.0 - ecco);
    s->no = no;
    s->ao = ao;
    s->sinio = sinio;
    s->cosio = cosio;

    s->isimp = rp < (220.0 / RE + 1.0);
    ss = 78.0 / RE + 1.0;
    qzms24 = (120.0 - 78.0) / RE;
    qzms24 = qzms24 * qzms24; qzms24 *= qzms24;
    sfour = ss;
    perige = (rp - 1.0) * RE;
    if (perige < 156.0) {
        sfour = perige - 78.0;
        if (perige < 98.0) sfour = 20.0;
        qzms24 = (120.0 - sfour) / RE;
        qzms24 = qzms24 * qzms24; qzms24 *= qzms24;
        sfour = sfour / RE + 1.0;
    }
    tsi = 1.0 / (ao - sfour);
    eta = ao * ecco * tsi;
    etasq = eta * eta;
    eeta = ecco * eta;
    psisq = 1.0 - etasq;
    if (psisq < 0) psisq = -psisq;
    coef = qzms24 * tsi * tsi * tsi * tsi;
    coef1 = coef / (psisq * psisq * psisq * sat_sqrt(psisq));
    cc2 = coef1 * no * (ao * (1.0 + 1.5 * etasq + eeta * (4.0 + etasq))
          + 0.375 * J2 * tsi / psisq * s->con41 * (8.0 + 3.0 * etasq * (8.0 + etasq)));
    cc1 = s->bstar * cc2;
    cc3 = 0.0;
    if (ecco > 1.0e-4) cc3 = -2.0 * coef * tsi * J3OJ2 * no * sinio / ecco;
    s->x1mth2 = 1.0 - cosio2;
    sat_sincos(2.0 * s->argpo, &sinx, &cosx);
    cc4 = 2.0 * no * coef1 * ao * omeosq * (eta * (2.0 + 0.5 * etasq) + ecco * (0.5 + 2.0 * etasq)
          - J2 * tsi / (ao * psisq) * (-3.0 * s->con41 * (1.0 - 2.0 * eeta + etasq * (1.5 - 0.5 * eeta))
          + 0.75 * s->x1mth2 * (2.0 * etasq - eeta * (1.0 + etasq)) * cosx));
    s->cc5 = 2.0 * coef1 * ao * omeosq * (1.0 + 2.75 * (etasq + eeta) + eeta * etasq);
    s->cc1 = cc1;
    s->cc4 = cc4;
    cosio4 = cosio2 * cosio2;
    temp1 = 1.5 * J2 * pinvsq * no;
    temp2 = 0.5 * temp1 * J2 * pinvsq;
    temp3 = -0.46875 * J4 * pinvsq * pinvsq * no;
    s->mdot = no + 0.5 * temp1 * rteosq * s->con41
              + 0.0625 * temp2 * rteosq * (13.0 - 78.0 * cosio2 + 137.0 * cosio4);
    s->argpdot = -0.5 * temp1 * con42 + 0.0625 * temp2 * (7.0 - 114.0 * cosio2 + 395.0 * cosio4)
                 + temp3 * (3.0 - 36.0 * cosio2 + 49.0 * cosio4);
    tmp = -temp1 * cosio;                       /* xhdot1 */
    s->nodedot = tmp + (0.5 * temp2 * (4.0 - 19.0 * cosio2) + 2.0 * temp3 * (3.0 - 7.0 * cosio2)) * cosio;
    sat_sincos(s->argpo, &sinx, &cosx);
    s->omgcof = s->bstar * cc3 * cosx;
    s->xmcof = 0.0;
    if (ecco > 1.0e-4) s->xmcof = -(2.0 / 3.0) * coef * s->bstar / eeta;
    s->nodecf = 3.5 * omeosq * tmp * cc1;
    s->t2cof = 1.5 * cc1;
    {
        double d = (cosio + 1.0 > 1.5e-12 || cosio + 1.0 < -1.5e-12) ? 1.0 + cosio : 1.5e-12;
        s->xlcof = -0.25 * J3OJ2 * sinio * (3.0 + 5.0 * cosio) / d;
    }
    s->aycof = -0.5 * J3OJ2 * sinio;
    sat_sincos(s->mo, &s->sinmao, &cosx);
    tmp = 1.0 + eta * cosx;
    s->delmo = tmp * tmp * tmp;
    s->eta = eta;
    s->x7thm1 = 7.0 * cosio2 - 1.0;
    if (!s->isimp) {
        double cc1sq = cc1 * cc1, t;
        s->d2 = 4.0 * ao * tsi * cc1sq;
        t = s->d2 * tsi * cc1 / 3.0;
        s->d3 = (17.0 * ao + sfour) * t;
        s->d4 = 0.5 * t * ao * tsi * (221.0 * ao + 31.0 * sfour) * cc1;
        s->t3cof = s->d2 + 2.0 * cc1sq;
        s->t4cof = 0.25 * (3.0 * s->d3 + cc1 * (12.0 * s->d2 + 10.0 * cc1sq));
        s->t5cof = 0.2 * (3.0 * s->d4 + 12.0 * cc1 * s->d3 + 6.0 * s->d2 * s->d2
                          + 15.0 * cc1sq * (2.0 * s->d2 + cc1sq));
    } else {
        s->d2 = s->d3 = s->d4 = s->t3cof = s->t4cof = s->t5cof = 0.0;
    }
}

int krk_sat_propagate(const struct krk_sat *s, double t, double r[3], double v[3])
{
    double tm = (t - s->epoch) * 1440.0;        /* minutes since epoch */
    double t2 = tm * tm, t3, t4;
    double xmdf = s->mo + s->mdot * tm, argpdf = s->argpo + s->argpdot * tm;
    double nodem = s->nodeo + s->nodedot * tm + s->nodecf * t2;
    double argpm = argpdf, mm = xmdf;
    double tempa = 1.0 - s->cc1 * tm, tempe = s->bstar * s->cc4 * tm, templ = s->t2cof * t2;
    double am, nm, em, xlm, tmp, tmp1, tmp2;
    double axnl, aynl, xl, u, eo1, sineo1 = 0, coseo1 = 1, tem5, ecose, esine, el2, pl, rl, rdotl, rvdotl;
    double betal, sinu, cosu, su, sin2u, cos2u, mrt, xnode, xinc, mvt, rvdot;
    double sinsu, cossu, snod, cnod, sini, cosi, xmx, xmy, ux, uy, uz, vx, vy, vz;
    double sinip, cosip, sinx, cosx;
    int ktr;
    STAT(krk_sat_n_prop);

    if (!s->isimp) {
        double delomg = s->omgcof * tm, delm, temp;
        sat_sincos(xmdf, &sinx, &cosx);
        tmp = 1.0 + s->eta * cosx;
        delm = s->xmcof * (tmp * tmp * tmp - s->delmo);
        temp = delomg + delm;
        mm = xmdf + temp;
        argpm = argpdf - temp;
        t3 = t2 * tm;
        t4 = t3 * tm;
        tempa = tempa - s->d2 * t2 - s->d3 * t3 - s->d4 * t4;
        sat_sincos(mm, &sinx, &cosx);
        tempe += s->bstar * s->cc5 * (sinx - s->sinmao);
        templ += s->t3cof * t3 + t4 * (s->t4cof + tm * s->t5cof);
    }
    am = s->ao * tempa * tempa;                 /* (xke/no)^(2/3) = ao */
    nm = XKE / (am * sat_sqrt(am));
    em = s->ecco - tempe;
    if (em >= 1.0 || em < -0.001 || am < 0.95) return KRK_SAT_E_ORBIT;
    if (em < 1.0e-6) em = 1.0e-6;
    mm += s->no * templ;
    xlm = mm + argpm + nodem;
    nodem = sat_mod2pi(nodem);
    argpm = sat_mod2pi(argpm);
    mm = sat_mod2pi(xlm - argpm - nodem);
    sinip = s->sinio;
    cosip = s->cosio;

    sat_sincos(argpm, &sinx, &cosx);
    axnl = em * cosx;
    tmp = 1.0 / (am * (1.0 - em * em));
    aynl = em * sinx + tmp * s->aycof;
    xl = mm + argpm + nodem + tmp * s->xlcof * axnl;
    u = sat_mod2pi(xl - nodem);
    eo1 = u;
    tem5 = 9999.9;
    for (ktr = 0; ktr < 10 && (tem5 >= 1e-12 || tem5 <= -1e-12); ktr++) {
        sat_sincos(eo1, &sineo1, &coseo1);
        tem5 = 1.0 - coseo1 * axnl - sineo1 * aynl;
        tem5 = (u - aynl * coseo1 + axnl * sineo1 - eo1) / tem5;
        if (tem5 >= 0.95) tem5 = 0.95;
        else if (tem5 <= -0.95) tem5 = -0.95;
        eo1 += tem5;
    }
    /* eo1 moved after the last sincos by < 1e-12: the sin/cos of the previous value are used, as in
     * the reference implementation */
    ecose = axnl * coseo1 + aynl * sineo1;
    esine = axnl * sineo1 - aynl * coseo1;
    el2 = axnl * axnl + aynl * aynl;
    pl = am * (1.0 - el2);
    if (pl < 0.0) return KRK_SAT_E_ORBIT;
    rl = am * (1.0 - ecose);
    rdotl = sat_sqrt(am) * esine / rl;
    rvdotl = sat_sqrt(pl) / rl;
    betal = sat_sqrt(1.0 - el2);
    tmp = esine / (1.0 + betal);
    sinu = am / rl * (sineo1 - aynl - axnl * tmp);
    cosu = am / rl * (coseo1 - axnl + aynl * tmp);
    su = sat_atan2(sinu, cosu);
    sin2u = (cosu + cosu) * sinu;
    cos2u = 1.0 - 2.0 * sinu * sinu;
    tmp = 1.0 / pl;
    tmp1 = 0.5 * J2 * tmp;
    tmp2 = tmp1 * tmp;
    mrt = rl * (1.0 - 1.5 * tmp2 * betal * s->con41) + 0.5 * tmp1 * s->x1mth2 * cos2u;
    su -= 0.25 * tmp2 * s->x7thm1 * sin2u;
    xnode = nodem + 1.5 * tmp2 * cosip * sin2u;
    xinc = s->inclo + 1.5 * tmp2 * cosip * sinip * cos2u;
    mvt = rdotl - nm * tmp1 * s->x1mth2 * sin2u / XKE;
    rvdot = rvdotl + nm * tmp1 * (s->x1mth2 * cos2u + 1.5 * s->con41) / XKE;
    sat_sincos(su, &sinsu, &cossu);
    sat_sincos(xnode, &snod, &cnod);
    sat_sincos(xinc, &sini, &cosi);
    xmx = -snod * cosi;
    xmy = cnod * cosi;
    ux = xmx * sinsu + cnod * cossu;
    uy = xmy * sinsu + snod * cossu;
    uz = sini * sinsu;
    vx = xmx * cossu - cnod * sinsu;
    vy = xmy * cossu - snod * sinsu;
    vz = sini * cossu;
    if (mrt < 1.0) return KRK_SAT_E_ORBIT;      /* below the Earth's surface: decayed */
    r[0] = mrt * RE * ux; r[1] = mrt * RE * uy; r[2] = mrt * RE * uz;
    v[0] = (mvt * ux + rvdot * vx) * VKMPS;
    v[1] = (mvt * uy + rvdot * vy) * VKMPS;
    v[2] = (mvt * uz + rvdot * vz) * VKMPS;
    return 0;
}

/* ------------------------------------------------------------------ observer, look, Doppler */

void krk_sat_obs_deg(struct krk_sat_obs *o, double lat_deg, double lon_deg, double alt_m)
{
    const double a = 6378.137, e2 = 0.00669437999014;
    double sl, cl, n, h = alt_m * 0.001, zz;
    sat_sincos(lat_deg * DEG2RAD, &sl, &cl);
    n = a / sat_sqrt(1.0 - e2 * sl * sl);
    o->lon = lon_deg * DEG2RAD;
    o->rxy = (n + h) * cl;
    zz = (n * (1.0 - e2) + h) * sl;
    o->z = zz;
    o->rad = sat_sqrt(o->rxy * o->rxy + zz * zz);
    o->sinlat = sl;
    o->coslat = cl;
}

void krk_sat_obs_min4(struct krk_sat_obs *o, int lat_1e4min, int lon_1e4min, int alt_m)
{
    krk_sat_obs_deg(o, lat_1e4min * (1.0 / 600000.0), lon_1e4min * (1.0 / 600000.0), alt_m);
}

/* skip (optional): seconds the satellite provably cannot be above the geometric horizon, when it
 * is below it (0 otherwise). Bound: the range cannot shrink faster than |v_sat - v_obs| < 10 km/s
 * and the satellite must come within the horizon distance sqrt(r^2 - Ro^2) (+2% +100 km margin
 * for the oblate Earth and observer altitude). */
static int look_core(const struct krk_sat *s, const struct krk_sat_obs *o, double t,
                     struct krk_sat_look *L, double *skip)
{
    double sth, cth, th, ox, oy, rx, ry, rz, c, S, E, Z, h2, d2;
    int e = krk_sat_propagate(s, t, L->r, L->v);
    if (e) return e;
    th = gmst(t) + o->lon;
    sat_sincos(th, &sth, &cth);
    ox = o->rxy * cth;
    oy = o->rxy * sth;
    rx = L->r[0] - ox;
    ry = L->r[1] - oy;
    rz = L->r[2] - o->z;
    c = cth * rx + sth * ry;
    S = o->sinlat * c - o->coslat * rz;
    E = -sth * rx + cth * ry;
    Z = o->coslat * c + o->sinlat * rz;
    h2 = S * S + E * E;
    d2 = h2 + Z * Z;
    L->range = sat_sqrt(d2);
    L->az = sat_atan2(E, -S) * RAD2DEG;
    if (L->az < 0) L->az += 360.0;
    L->el = sat_atan2(Z, sat_sqrt(h2)) * RAD2DEG;
    L->rr = (rx * (L->v[0] + OMEGA_E * oy) + ry * (L->v[1] - OMEGA_E * ox) + rz * L->v[2]) / L->range;
    if (skip) {
        double r2 = L->r[0] * L->r[0] + L->r[1] * L->r[1] + L->r[2] * L->r[2], km;
        *skip = 0.0;
        if (Z < 0.0 && r2 > o->rad * o->rad) {
            km = L->range - 1.02 * sat_sqrt(r2 - o->rad * o->rad) - 100.0;
            if (km > 0.0) *skip = km * 0.1;     /* /10 km/s */
        }
    }
    return 0;
}

int krk_sat_look(const struct krk_sat *s, const struct krk_sat_obs *o, double t,
                 struct krk_sat_look *out)
{
    return look_core(s, o, t, out, 0);
}

unsigned krk_sat_doppler_rx(unsigned f_hz, double rr)
{
    return (unsigned)((double)f_hz * (1.0 - rr * (1.0 / C_KMS)) + 0.5);
}

unsigned krk_sat_doppler_tx(unsigned f_hz, double rr)
{
    return (unsigned)((double)f_hz * (1.0 + rr * (1.0 / C_KMS)) + 0.5);
}

unsigned krk_sat_round_hz(unsigned f_hz, unsigned step)
{
    if (!step) return f_hz;
    return (f_hz + step / 2) / step * step;
}

/* ------------------------------------------------------------------ pass prediction */

#define STEP_MIN   20.0         /* seconds: step while near the horizon */
#define STEP_PASS  30.0         /* seconds: step inside a pass */
#define SKIP_MAX   900.0
#define SEC        (1.0 / 86400.0)

/* Time where (el >= min_el) flips between lo and hi (their states differ); returns the side that
 * is above, to 1 s. */
static int bisect(const struct krk_sat *s, const struct krk_sat_obs *o, double lo, double hi,
                  double min_el, int hi_above, double *res)
{
    struct krk_sat_look L;
    int e;
    while (hi - lo > SEC) {
        double mid = 0.5 * (lo + hi);
        e = look_core(s, o, mid, &L, 0);
        if (e) return e;
        if ((L.el >= min_el) == (hi_above != 0)) hi = mid; else lo = mid;
    }
    *res = hi_above ? hi : lo;
    return 0;
}

int krk_sat_next_pass(const struct krk_sat *s, const struct krk_sat_obs *o, double t0,
                      double horizon_days, double min_el, struct krk_sat_pass *p)
{
    struct krk_sat_look L;
    double t = t0, tprev = t0, skip, tend = t0 + horizon_days, tc, ec, tp, ep, tn, en;
    double bt = 0, bep = 0, bec = -1e9, ben = 0, btp = 0;
    int e, first = 1, have_p;

    p->flags = 0;
    /* 1. find the first sample at or above min_el */
    for (;;) {
        e = look_core(s, o, t, &L, &skip);
        if (e) return e;
        if (L.el >= min_el) break;
        if (L.el >= 0.0 || skip < STEP_MIN) skip = STEP_MIN;
        else if (skip > SKIP_MAX) skip = SKIP_MAX;
        tprev = t;
        t += skip * SEC;
        first = 0;
        if (t > tend) return 0;
    }
    if (first) { p->aos = t0; p->flags |= KRK_SAT_PASS_INPROGRESS; }
    else {
        e = bisect(s, o, tprev, t, min_el, 1, &p->aos);
        if (e) return e;
    }
    /* 2. walk the pass, tracking the best 3-sample window for the peak */
    tc = t; ec = L.el; tp = 0; ep = 0; have_p = 0;
    for (;;) {
        tn = tc + STEP_PASS * SEC;
        e = look_core(s, o, tn, &L, 0);
        if (e) return e;
        en = L.el;
        if (ec >= en && (!have_p || ec >= ep) && ec > bec) {
            bec = ec; bt = tc; bep = have_p ? ep : ec; ben = en; btp = have_p ? tp : tc;
        }
        if (en < min_el) {
            e = bisect(s, o, tc, tn, min_el, 0, &p->los);
            if (e) return e;
            break;
        }
        if (tn > tend + 0.1) { p->los = tn; p->flags |= KRK_SAT_PASS_LOS_CLIPPED; break; }
        tp = tc; ep = ec; have_p = 1;
        tc = tn; ec = en;
    }
    /* 3. refine the peak: parabola through the best 3-sample window (equal spacing when btp < bt),
     * then twice more through 3 samples at 1/5 of the previous spacing (an overhead pass has a
     * sharp peak: one 30 s parabola alone can be 1-2 degrees low). 3 + 3 + 3 propagations. */
    p->tmax = bt; p->max_el = bec;
    if (btp < bt) {
        double h = STEP_PASS * SEC, e0 = bep, e1 = bec, e2 = ben, tv, den;
        int it;
        tc = bt;
        for (it = 0; it < 3; it++) {
            den = e0 - 2.0 * e1 + e2;
            if (den > -1e-9) break;
            tv = tc + h * 0.5 * (e0 - e2) / den;
            if (tv < tc - h) tv = tc - h;
            if (tv > tc + h) tv = tc + h;
            h *= 0.2;
            tc = tv;
            e = look_core(s, o, tv - h, &L, 0); if (e) return e; e0 = L.el;
            e = look_core(s, o, tv, &L, 0);     if (e) return e; e1 = L.el;
            e = look_core(s, o, tv + h, &L, 0); if (e) return e; e2 = L.el;
            if (e1 > p->max_el) { p->tmax = tv; p->max_el = e1; }
        }
    }
    if (p->tmax < p->aos) p->tmax = p->aos;
    if (p->tmax > p->los) p->tmax = p->los;
    return 1;
}
