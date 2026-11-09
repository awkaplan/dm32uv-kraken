"""Host build and batch driver for the AFSK decoder (patches/kraken_ui/krk_afsk.c + krk_ax25.c), shared by
afsk_offline.py and tests/test_afsk.py.

    exe = build_decoder(cflags=(), ui=hostcc.UI, warn=False)       # cc -O2 [-w | -Wall] -I ui cflags
    results = decode(exe, paths, preemph=None, preset=None, space_gain=0, block=256)

decode() feeds each raw s16le 8 kHz file to the decoder in `block`-sample blocks (256 = the radio's FM RX RAM
halves) after setting the options that are not None / > 0 and krk_afsk_reset(); one process for all files.
Result: frames, flags, badfcs, samples, mark, trans (the tone-decision statistics), lines (the non-empty
decoder result lines), line (the first, "" if none) and summary (the offline report line).
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tests"))
import hostcc  # noqa: E402

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "krk_afsk.h"
/* args: preemph preset space_gain block (a negative preemph / preset, a space_gain of 0: leave the default);
   stdin: one file path per line. Per file: "\x1e frames flags badfcs samples mark trans", then the result lines. */
int main(int argc, char **argv) {
    char path[512], line[AFSK_LINE_LEN + 1]; short b[256]; size_t n;
    int pe = atoi(argv[1]), ps = atoi(argv[2]), sg = atoi(argv[3]), blk = atoi(argv[4]);
    unsigned fl = 0, bad = 0, ns = 0, mk = 0, tr = 0;
    while (scanf("%511s", path) == 1) {
        FILE *fp = fopen(path, "rb");
        if (sg > 0) krk_afsk_set_space_gain(sg);
        if (pe >= 0) krk_afsk_set_preemph(pe);
        if (ps >= 0) krk_afsk_set_preset(ps);
        krk_afsk_reset();
        while (fp && (n = fread(b, 2, blk, fp)) > 0) krk_afsk_feed(b, (int)n);
        if (fp) fclose(fp);
        krk_afsk_stats(&fl, &bad); krk_afsk_tone_stats(&ns, &mk, &tr);
        printf("\x1e %u %u %u %u %u %u\n", krk_afsk_frames(), fl, bad, ns, mk, tr);
        for (int i = 0; i < AFSK_LINES; i++) { krk_afsk_line(i, line); if (line[0]) printf("%s\n", line); }
    }
    return 0; }
'''


class Result(collections.namedtuple("Result", "frames flags badfcs samples mark trans lines")):
    @property
    def line(self):
        return self.lines[0] if self.lines else ""

    @property
    def summary(self):
        return (f"frames {self.frames} flags {self.flags} badfcs {self.badfcs} samples {self.samples} "
                f"mark% {self.samples and self.mark * 100 // self.samples} "
                f"trans/s {self.samples and self.trans * 8000.0 / self.samples:.0f}")


def build_decoder(cflags=(), ui=hostcc.UI, warn=False):
    ui = pathlib.Path(ui)
    return hostcc.build([ui / "krk_afsk.c", ui / "krk_ax25.c"], flags=["-O2", "-Wall" if warn else "-w", f"-I{ui}", *cflags],
                        driver_src=DRIVER)


def decode(exe, paths, preemph=None, preset=None, space_gain=0, block=256):
    args = [-1 if preemph is None else preemph, -1 if preset is None else preset, space_gain, block]
    out = hostcc.run(exe, *args, input="".join(f"{p}\n" for p in paths))
    res = []
    for rec in out.split("\x1e")[1:]:
        head, *lines = rec.split("\n")
        res.append(Result(*map(int, head.split()), lines[:-1]))
    return res
