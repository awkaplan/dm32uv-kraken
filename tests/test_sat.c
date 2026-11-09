/* Host driver for krk_sat.c (see test_sat.py). Built with -DKRK_SAT_STATS.
 *   test_sat parse L1 L2                         -> error code, epoch, no, ecco
 *   test_sat time Y M D h m s                    -> t, then the civil round trip
 *   test_sat track L1 L2 lat lon alt Y M D h m s N step_s
 *                                                -> N rows: sec r[3] v[3] az el range rr
 *   test_sat pass L1 L2 lat lon alt Y M D h m s horizon_h min_el
 *                                                -> found aos los tmax max_el flags props (seconds from t0)
 *   test_sat bench L1 L2 N                       -> us/look, sincos/atan/sqrt per look, sizeof
 *   test_sat doppler f_hz rr step                -> rx tx rounded_rx */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "../patches/kraken_ui/krk_sat.c"

static double D(char **a, int i) { return atof(a[i]); }

static int setup(char **a, struct krk_sat *s, struct krk_sat_obs *o, double *t0, int i)
{
    int e = krk_sat_init(s, a[0], a[1]);
    if (e) { printf("init error %d\n", e); return e; }
    krk_sat_obs_deg(o, D(a, 2), D(a, 3), D(a, 4));
    *t0 = krk_sat_time(atoi(a[5]), atoi(a[6]), atoi(a[7]), atoi(a[8]), atoi(a[9]), D(a, 10));
    (void)i;
    return 0;
}

int main(int argc, char **argv)
{
    struct krk_sat s;
    struct krk_sat_obs o;
    struct krk_sat_look L;
    double t0;
    const char *cmd = argc > 1 ? argv[1] : "";
    char **a = argv + 2;
    if (!strcmp(cmd, "parse")) {
        int e = krk_sat_init(&s, a[0], a[1]);
        printf("%d %.10f %.15g %.15g %u\n", e, s.epoch, s.no, s.ecco, s.catnr);
    } else if (!strcmp(cmd, "time")) {
        int y, mo, d, h, mi, se;
        double t = krk_sat_time(atoi(a[0]), atoi(a[1]), atoi(a[2]), atoi(a[3]), atoi(a[4]), D(a, 5));
        krk_sat_civil(t, &y, &mo, &d, &h, &mi, &se);
        printf("%.10f %d %d %d %d %d %d\n", t, y, mo, d, h, mi, se);
    } else if (!strcmp(cmd, "track")) {
        int n = atoi(a[11]), k;
        if (setup(a, &s, &o, &t0, 0)) return 1;
        for (k = 0; k < n; k++) {
            double ts = k * D(a, 12);
            int e = krk_sat_look(&s, &o, t0 + ts / 86400.0, &L);
            if (e) { printf("%.3f err %d\n", ts, e); continue; }
            printf("%.3f %.9f %.9f %.9f %.12f %.12f %.12f %.9f %.9f %.9f %.12f\n", ts, L.r[0], L.r[1], L.r[2],
                   L.v[0], L.v[1], L.v[2], L.az, L.el, L.range, L.rr);
        }
    } else if (!strcmp(cmd, "pass")) {
        struct krk_sat_pass p;
        int e;
        if (setup(a, &s, &o, &t0, 0)) return 1;
        krk_sat_n_prop = 0;
        e = krk_sat_next_pass(&s, &o, t0, D(a, 11) / 24.0, D(a, 12), &p);
        if (e == 1)
            printf("1 %.3f %.3f %.3f %.4f %u %u\n", (p.aos - t0) * 86400, (p.los - t0) * 86400,
                   (p.tmax - t0) * 86400, p.max_el, p.flags, krk_sat_n_prop);
        else
            printf("%d 0 0 0 0 0 %u\n", e, krk_sat_n_prop);
    } else if (!strcmp(cmd, "bench")) {
        int n = atoi(a[2]), k;
        double acc = 0, t_start = 0;
        clock_t c0;
        if (krk_sat_init(&s, a[0], a[1])) return 1;
        krk_sat_obs_deg(&o, 41.7, -72.7, 30);
        krk_sat_n_sincos = krk_sat_n_atan = krk_sat_n_sqrt = 0;
        c0 = clock();
        for (k = 0; k < n; k++) {
            krk_sat_look(&s, &o, s.epoch + t_start + k * 0.0007, &L);
            acc += L.rr;
        }
        printf("%.3f us/look  sincos %.1f  atan2 %.1f  sqrt %.1f per look  sizeof sat %u obs %u  (%g)\n",
               (double)(clock() - c0) / CLOCKS_PER_SEC / n * 1e6, (double)krk_sat_n_sincos / n,
               (double)krk_sat_n_atan / n, (double)krk_sat_n_sqrt / n, (unsigned)sizeof s,
               (unsigned)sizeof o, acc);
    } else if (!strcmp(cmd, "doppler")) {
        unsigned f = (unsigned)atof(a[0]);
        double rr = D(a, 1);
        printf("%u %u %u\n", krk_sat_doppler_rx(f, rr), krk_sat_doppler_tx(f, rr),
               krk_sat_round_hz(krk_sat_doppler_rx(f, rr), (unsigned)atoi(a[2])));
    } else
        return 2;
    return 0;
}
