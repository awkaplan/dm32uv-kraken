/* Host test glue for krk_ambe.c: exhaustive Golay sweeps that would be too slow through ctypes. Not part of the radio image. */
#include "krk_ambe.h"

/* Every message m = 0, step, 2*step ... and every 1..3 bit error pattern over the 24 bits of the extended code.
 * out[0] patterns tried, out[1] wrong results (word, data or error count), out[2] weight-4 patterns tried, out[3] weight-4 not flagged. */
void ambet_golay_sweep(int step, int *out)
{
    unsigned m, cw, r, d, a, b, c, w4;
    int e;
    out[0] = out[1] = out[2] = out[3] = 0;
    for (m = 0; m < 4096; m += (unsigned)step) {
        cw = krk_golay24_encode(m);
        for (a = 0; a < 24; a++)
            for (b = a; b < 24; b++)
                for (c = b; c < 24; c++) {
                    unsigned pat = (1u << a);
                    int wt = 1;
                    if (b != a) { pat |= 1u << b; wt = 2; }
                    if (c != b) { if (b == a) continue; pat |= 1u << c; wt = 3; }
                    if (b == a && c != b) continue;
                    r = cw ^ pat;
                    e = krk_golay24_decode(&r, &d);
                    out[0]++;
                    if (r != cw || d != m || e != wt) out[1]++;
                }
    }
    cw = krk_golay24_encode(0xA5C);
    for (a = 0; a < 24; a++) for (b = a + 1; b < 24; b++) for (c = b + 1; c < 24; c++) for (w4 = c + 1; w4 < 24; w4++) {
        r = cw ^ (1u << a) ^ (1u << b) ^ (1u << c) ^ (1u << w4);
        e = krk_golay24_decode(&r, &d);
        out[2]++;
        if (e >= 0) out[3]++;
    }
}
