/*
 * krk_sat.h - F10 orbit maths: TLE parsing, SGP4 (near-earth), look angles, Doppler, pass search.
 *
 * Freestanding C99: no libc, no libm (sin/cos/atan2/sqrt are private), no malloc, no recursion, no
 * writable globals (all state is in the caller's structs). Arithmetic is IEEE double through
 * libgcc soft-float on the radio; see docs/satellite-mode.md for the cost model.
 *
 * Time is a double: days since 1949-12-31 00:00 UTC (the SGP4 epoch), built from civil UTC with
 * krk_sat_time(). UTC is used as UT1 (error < 0.9 s = < 0.4 km of Earth rotation at the surface).
 * Frames: satellite position/velocity in TEME [km, km/s]; observer is geodetic (WGS-84).
 * Range-rate is positive when the satellite is receding (so rx = f * (1 - rr/c)).
 */
#ifndef KRK_SAT_H
#define KRK_SAT_H

/* Errors (negative returns). */
#define KRK_SAT_E_FORMAT   (-1)     /* not a pair of 69-column TLE lines / unparsable field */
#define KRK_SAT_E_CHECKSUM (-2)     /* modulo-10 checksum mismatch on line 1 or 2 */
#define KRK_SAT_E_MISMATCH (-3)     /* line numbers or catalog numbers disagree */
#define KRK_SAT_E_DEEP     (-4)     /* period >= 225 min: needs SDP4, not supported */
#define KRK_SAT_E_ORBIT    (-5)     /* eccentricity / decay: propagation failed */

/* A prepared satellite: parsed TLE plus the SGP4 initialisation constants (~300 bytes). */
struct krk_sat {
    double epoch;                   /* TLE epoch, days since 1949-12-31 00:00 UTC */
    double bstar, no, ecco, inclo, nodeo, argpo, mo;    /* no in rad/min; angles in rad */
    double ao, eta, delmo, sinmao, cc1, cc4, cc5, omgcof, xmcof, nodecf, t2cof;
    double d2, d3, d4, t3cof, t4cof, t5cof;
    double mdot, argpdot, nodedot, aycof, xlcof, con41, x1mth2, x7thm1, sinio, cosio;
    int isimp;
    unsigned catnr;                 /* NORAD catalog number */
};

/* Observer, precomputed (~64 bytes). */
struct krk_sat_obs {
    double lon, rxy, z, rad;        /* east longitude [rad]; ECEF radius in the equatorial plane,
                                       z, and geocentric radius [km] */
    double sinlat, coslat;
};

/* Result of a look-angle computation. */
struct krk_sat_look {
    double az, el;                  /* degrees: azimuth 0..360 from north clockwise, elevation -90..90 */
    double range;                   /* km */
    double rr;                      /* range rate [km/s], > 0 receding */
    double r[3], v[3];              /* satellite TEME position [km] and velocity [km/s] */
};

struct krk_sat_pass {
    double aos, los;                /* time at elevation >= min_el (1 s resolution) */
    double tmax;                    /* time of maximum elevation */
    double max_el;                  /* degrees */
    unsigned flags;
};
#define KRK_SAT_PASS_INPROGRESS 1   /* satellite already up at the start time: aos == start time, and
                                       tmax/max_el cover only the remaining part of the pass */
#define KRK_SAT_PASS_LOS_CLIPPED 2  /* pass still running at the search limit; los is the limit */

/* Civil UTC -> time. Valid for 1950..2099 (and beyond; Gregorian). */
double krk_sat_time(int year, int mon, int day, int hour, int min, double sec);
/* Time -> civil UTC (sec rounded down to a whole second in *sec). For displaying AOS/LOS. */
void krk_sat_civil(double t, int *year, int *mon, int *day, int *hour, int *min, int *sec);

/* Integer civil date <-> days since 1970-01-01 (Hinnant); the time functions above and krk_satrf.c share them. */
long krk_days_from_civil(long year, int mon, int day);
void krk_civil_from_days(long days, int *year, int *mon, int *day);

/* Parse the two 69-char lines (trailing CR/LF/spaces allowed) and initialise SGP4.
 * Verifies both checksums, the line numbers and the catalog number. Returns 0 or KRK_SAT_E_*. */
int krk_sat_init(struct krk_sat *s, const char *line1, const char *line2);

/* TLE epoch as a time (to show / test staleness: (t - epoch) in days). */
double krk_sat_epoch(const struct krk_sat *s);

/* Observer from degrees (north/east positive) or from struct krk_gps_fix units
 * (1e-4 arc-minute, signed: lat = fix.lat, lon = fix.lon; 600000 units = 1 degree). alt in metres. */
void krk_sat_obs_deg(struct krk_sat_obs *o, double lat_deg, double lon_deg, double alt_m);
void krk_sat_obs_min4(struct krk_sat_obs *o, int lat_1e4min, int lon_1e4min, int alt_m);

/* SGP4: TEME position [km] and velocity [km/s] at time t. Returns 0 or KRK_SAT_E_ORBIT. */
int krk_sat_propagate(const struct krk_sat *s, double t, double r[3], double v[3]);

/* One propagation + topocentric transform (the 1 Hz UI / Doppler call). Returns 0 or KRK_SAT_E_*. */
int krk_sat_look(const struct krk_sat *s, const struct krk_sat_obs *o, double t,
                 struct krk_sat_look *out);

/* Doppler. rr in km/s (look.rr). Downlink as heard: f*(1 - rr/c); uplink to transmit so the
 * satellite receives the nominal frequency: f*(1 + rr/c). Hz in, Hz out (rounded to nearest Hz;
 * valid up to ~4 GHz). */
unsigned krk_sat_doppler_rx(unsigned f_hz, double rr);
unsigned krk_sat_doppler_tx(unsigned f_hz, double rr);
/* Round to the radio's tuning step (e.g. 10, 2500, 5000, 6250, 12500 Hz). step 0 = unchanged. */
unsigned krk_sat_round_hz(unsigned f_hz, unsigned step);

/* Next pass from t0 within horizon_days. A pass is a span with elevation >= min_el (degrees).
 * Returns 1 and fills *p, 0 if none within the horizon, or a negative error. Cost: about
 * 1 propagation per 20..900 s while the satellite is far below the horizon (distance-based skip),
 * 1 per 30 s during a pass, ~12 more for AOS/LOS bisection and peak refinement; typically
 * 100..400 propagations for a 24 h horizon, worst case horizon/20 s + 150. A pass whose peak is
 * barely above min_el (< ~20 s long) can be missed. */
int krk_sat_next_pass(const struct krk_sat *s, const struct krk_sat_obs *o, double t0,
                      double horizon_days, double min_el, struct krk_sat_pass *p);

#ifdef KRK_SAT_STATS
/* Host-test instrumentation only (defines writable globals; never defined on the radio). */
extern unsigned krk_sat_n_sincos, krk_sat_n_atan, krk_sat_n_sqrt, krk_sat_n_prop;
#endif

#endif
