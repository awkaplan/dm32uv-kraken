/* Host test for krk_grid.c's parsing and Maidenhead maths: cc -I<api> test_grid.c && ./a.out */
#include <stdio.h>
#include <string.h>
#include "../patches/kraken_ui/krk_grid.c"
/* krk.h helpers the screen code links against; unused by the functions under test */
void krk_line(int n, const char *s, u16 fg) { (void)n; (void)s; (void)fg; }
void krk_text(int x, int y, const char *s, u16 fg) { (void)x; (void)y; (void)s; (void)fg; }
char *krk_cat(char *d, const char *s) { return strcat(d, s); }

int main(int argc, char **argv)
{
    /* argv: lat "ddmm.mmmm" N/S lon "dddmm.mmmm" E/W ; prints the 8-char locator */
    int lat = parse_angle(argv[1], 2), lon = parse_angle(argv[3], 3);
    char loc[9], f[16];
    lat = (argv[2][0] == 'S' ? -lat : lat) + 90 * 60 * MIN_UNITS;
    lon = (argv[4][0] == 'W' ? -lon : lon) + 180 * 60 * MIN_UNITS;
    maidenhead(lon, lat, loc);
    field("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A", 3, f, sizeof f);
    printf("%s %s\n", loc, f);
    (void)argc;
    return 0;
}
