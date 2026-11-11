/*
 * krk.h - shared interface of the Kraken UI patch: the screen runner and drawing helpers that
 * every feature screen (About, RSSI meter, last heard, ...) is built on.
 */
#ifndef KRK_H
#define KRK_H

#include "dm32_patch_api.h"

/* State that must sit in the shared feature-state claim (.bss.feat). It is not zeroed at power-up: guard it with a magic. */
#ifndef KRK_FEAT                    /* tests/test_dmrlink.py defines it away */
#define KRK_FEAT __attribute__((section(".bss.feat")))
#endif
/* Small state of the APRS features of 2026-11-11, in the claim kraken_aprs_state (.bss.aprs, kraken_ui2.ld).
 * Host test builds (Mach-O on a Mac) have no such section: there it is plain .bss. */
#ifndef KRK_APRS_BSS
#ifdef __ELF__
#define KRK_APRS_BSS __attribute__((section(".bss.aprs")))
#else
#define KRK_APRS_BSS
#endif
#endif

#define KRK_BG      0x0000      /* black */
#define KRK_FG      0xFFFF      /* white */
#define KRK_DIM     0x8410      /* grey */
#define KRK_ACCENT  0x07E0      /* green */
#define KRK_WARN    0xF800      /* red */

/* Content area: display_fill_screen clears rows 0x1b..0x121 (27..289) of the 240-px-wide panel.
 * ASCII glyphs are 12 x 24 px, so a line holds KRK_COLS characters and the area KRK_LINES lines. */
#define KRK_SCREEN_W 240
#define KRK_X0      6
#define KRK_Y0      0x20
#define KRK_LINE_H  24
#define KRK_COLS    19
#define KRK_LINES   10

/* A screen is two callbacks run by krk_run_screen() in the "task menu" context:
 *   enter()     once, after the content area has been cleared
 *   tick(scan)  every KRK_TICK_MS with a newly pressed key's scan code, else SCAN_NONE (a held
 *               key is reported once; Back is handled by the runner and never reaches tick) */
struct krk_opt;
struct krk_screen {
    const char *label;
    void (*enter)(void);
    void (*tick)(u8 scan);
    void (*exit)(void);         /* optional: on Back, before the list is restored */
    u16 tick_ms;                /* 0 = KRK_TICK_MS; a streaming screen sets 1 (~10 ms) */
    const struct krk_opt *opt;  /* non-NULL: the row opens the STOCK option level (enter/tick unused), see krk_optrow.c */
    u8 flags;                   /* KRK_SF_* */
};
/* KRK_SF_ONESHOT: the row is an action, not a screen. The runner parks the keypad, runs enter() over the list (the content area
 * is NOT cleared, so popups draw over the menu like the stock ones), and leaves as soon as enter() returns; tick() and exit()
 * are not used. enter() presents its result with krk_popup() (docs/stock-option-level.md part C). */
#define KRK_SF_ONESHOT 1

#define KRK_TICK_MS 100

/* Extension screens live in a second blob (kraken_ui2) whose table is at a fixed address:
 * KRK_EXT_MAX slots for the KRAKEN list, then KRK_APRS_MAX slots for rows appended to the stock
 * APRS sub-list (zero-terminated; every pointer is range-checked before use). */
/* analog APRS (krk_aprs_tx.c, krk_callsign.c) */
#define KRK_APRS_PATHS 4
/* Selected side's channel, dual-watch safe (krk_core.c, docs/dual-watch.md). Use these where the code means
 * "the channel the user selected"; read the live struct (0x3e1dc, VFO_FLAGS8) only for what the receiver is
 * tuned to right now (RF register work). */
int krk_channel_side(void);                  /* side (0 A, 1 B) the live struct describes, -1 unknown */
int krk_channel_on_selected(void);           /* the live struct IS the selected side's channel: TX gates require it */
int krk_channel_flags(void);                 /* selected side's flags8 (bits 4-7 mode, 1-2 power, 3 no-TX), -1 unknown */
int krk_channel_mode(void);                  /* 0 analog, 1 digital, -1 unknown */
int krk_channel_analog(void);                /* mode == 0 (unknown: 0) */
u32 krk_channel_freq(int tx);                /* RX or TX frequency, 10 Hz, 0 unknown */
static inline int krk_channel_dmr(void) { return krk_channel_mode() == 1; }   /* unknown: 0 */
void krk_hook_chan_commit(void);             /* splice target (krk_stubs.S krk_commit_end_stub) */
u8 krk_rec_csum(const void *rec, u8 seed);   /* 16-byte flash record checksum (krk_callsign.c, blob ui3) */
int krk_call_get(char *out);                 /* "CALL[-SSID]" into out (>= 16 bytes); 1 if a callsign is set */
int krk_aprs_tx_send(const char **why, const char **kind);
/* F8b Beacon Chan (krk_aprs_chan.c): every beacon send goes through krk_aprs_chan_send (automatic = the interval tick) */
int krk_aprs_chan_send(const char **why, const char **kind, int automatic);
int krk_aprs_chan_get(void);                /* 0 = Off, else the memory channel number */
int krk_aprs_chan_keep(void);               /* 0: the beacon channel switch holds the sender (other task), or the radio left the channel */
int krk_aprs_tx_analog(void);
unsigned krk_aprs_tx_count(void);
const char *krk_aprs_path_name(unsigned idx);
extern const char *const krk_aprs_path_names[KRK_APRS_PATHS];   /* the four names, for the Upload ID option level */
unsigned krk_aprs_cfg_path(void);
int krk_aprs_cfg_set_path(unsigned idx);
int krk_aprs_text_cat(char *dst, unsigned size);   /* F8a beacon text, else KRK_APRS_COMMENT, appended (krk_aprs_trec.c, blob ui3); 1 = saved text */
/* CW ID (krk_cwid.c, docs/cw-id.md). The generator state is the caller's (a stack struct for one send). */
#define KRK_CW_UNITS 160            /* a 6-character call is at most 129 units */
struct krk_cw {
    u32 ph;
    u16 smp;
    short unit, nunits;
    u8 env, done;
    u8 units[KRK_CW_UNITS / 8];
};
int krk_morse_units(const char *s, u8 *out, int max);
void krk_cw_start(struct krk_cw *c, int nunits, int lead);
int krk_cw_fill(void *ctx, volatile u8 *h);
void krk_cwid_tick(void);                    /* maintenance-task tick (krk_periodic.c) */
unsigned krk_cwid_cfg(void);                 /* krk_callsign.c (blob ui3): bit 7 on, bits 6-0 minutes */
int krk_cwid_cfg_set(unsigned v);
#define KRK_CWID_KIDS 3                      /* Other Func > CW ID children (krk_cwid.c, krk_vox.c) */
extern const char *const krk_cwid_kid_labels[KRK_CWID_KIDS];
const struct krk_screen *krk_cwid_kid(unsigned child);   /* a child's option row, or the callsign editor; NULL none */
extern const struct krk_screen krk_callsign_screen;
/* Freq Unlock (krk_band.c, docs/freq-unlock.md): splice targets of the boot band-table load and the four band checks. */
void krk_hook_band_load(void);
void krk_hook_band_check(u32 rx, u32 tx);
int krk_band_on(void);
/* Burst Voice settings (krk_bvcfg.c, blob 2; docs/burst-voice.md 13): the flash record, packed for the screen (blob ui4), and the
 * Channel Set nest's level-4 init and three level-5 splice targets (stubs in krk_ofstubs.S). */
u32 krk_bv_cfg(void);
#define KRK_BVC_MODE(c)  ((c) & 0xff)                /* BV_MODE_FAST 1 / BV_MODE_ROBUST 0 */
#define KRK_BVC_LEAD(c)  (((c) >> 8) & 0xff)         /* key-up lead, 32 ms units, 0..30 */
#define KRK_BVC_RELAY(c) (((c) >> 16) & 1)           /* relay capture wanted */
void krk_cs_bv_kids(void);
void krk_cs_l5_enter(void);
void krk_cs_l5_ok(void);
const char *krk_cs_l5_title(void);
/* Streams fill(ctx, half) on the selected analog channel with the beacon's gates (krk_aprs_tx.c).
 * 0 sent, 1 not now but soon, -1 refused or failed; *why always set. */
int krk_fm_send(int (*fill)(void *ctx, volatile u8 *h), void *ctx, const char **why);
/* The same send with options (Burst Voice, krk_bvscr.c); krk_fm_send is this with o = NULL. */
struct krk_fm_opts {
    int (*abort)(void);             /* or NULL: polled every 10 ms while TX comes up and every 16th idle ms of the stream;
                                     * nonzero ends the send ("Aborted"), registers restored and PTT released as on any exit */
    u32 budget;                     /* OS ticks the stream may run once the route is taken, then "TX timeout"; 0 = no limit */
};
int krk_fm_send_opt(int (*fill)(void *ctx, volatile u8 *h), void *ctx, const struct krk_fm_opts *o, const char **why);
int krk_on_selected_side(int ms);   /* dual watch: wait up to ms for the selected side, then hold the engine on it (krk_aprs_tx.c) */
void krk_hook_aprs_tick(void);              /* splice targets (krk_aprs_tx.c, manifest aprs_tick_hook / aprs_send_now_hook) */
void krk_hook_aprs_send_now(void);
#define KRK_EXT_MAX   20            /* 2026-11-02: 12 -> 20 (the stock-menu conversions added rows); the hook table follows the FOUR screen tables at
                                     * 0x030ee000 + 4 * (KRK_EXT_MAX + KRK_APRS_MAX + KRK_RS_MAX + KRK_CS_MAX) */
/* KRK_APRS_MAX, KRK_RS_MAX, KRK_CS_MAX and every other per-host row count, first item number and row list come from
 * menu_hosts.json through tools/gen_menu_hosts.py (krk_menu_rows.h); see "stock-menu hosts" below. Channel Set rows are FLAT:
 * nesting them under parent rows does not work on this firmware (docs/nested-submenu.md). */
#include "krk_menu_rows.h"
#define KRK_EXT_TABLE  ((const struct krk_screen *const *)0x030ee000)
#define KRK_APRS_TABLE (KRK_EXT_TABLE + KRK_EXT_MAX)
#define KRK_RS_TABLE   (KRK_APRS_TABLE + KRK_APRS_MAX)
#define KRK_CS_TABLE   (KRK_RS_TABLE + KRK_RS_MAX)
#include "krk_claims.h"          /* KRK_<CLAIM>_LO / _HI_EXCL, generated from allocations.json by tools/gen_claims.py */
/* BEGIN generated by tools/gen_claims.py: do not edit (see krk_claims.h) */
#define KRK_EXT_LO  0x030ee000u
#define KRK_EXT_HI  0x030f7000u    /* = kraken_ui_code2 end; krk_core rejects any screen or label at or past it */
#define KRK_EXT3_LO 0x030f8000u    /* kraken_ui3 (F10 satellite blob): screens there may sit in the table too */
#define KRK_EXT3_HI 0x03100000u
#define KRK_EXT4_LO 0x030e8000u    /* kraken_ui4 (F13 burst voice blob): likewise */
#define KRK_EXT4_HI 0x030ee000u
#define KRK_EXT5_LO 0x030e1000u    /* kraken_ui5 (F3 DMR link blob, dm32uv-re's reclaimed ramdump_probe_code sector): likewise */
#define KRK_EXT5_HI 0x030e2000u
/* END generated by tools/gen_claims.py */

/* Hook table of the extension blob (krk_ext.c, linked right after the screen table): void(void) entry
 * points the MAIN blob calls without knowing blob2's symbols. Slot 0 = DMR call start (Last Heard
 * splice H1), 1 = DMR call end (H2), 2 = channel change (aprs_mode_hook site, F12 scrambler), 3 = a screen is about to run
 * (krk_run_screen: the F14 RX Monitor releases the audio path, krk_mon_yield). A slot outside the extension range (or 0) is skipped. */
#define KRK_EXT_HOOKS_MAX 4
#define KRK_EXT_HOOK_TABLE ((void (*const *)(void))(KRK_EXT_TABLE + KRK_EXT_MAX + KRK_APRS_MAX + KRK_RS_MAX + KRK_CS_MAX))
static inline void krk_ext_hook(unsigned i)
{
    void (*f)(void) = i < KRK_EXT_HOOKS_MAX ? KRK_EXT_HOOK_TABLE[i] : 0;

    if ((u32)f >= KRK_EXT_LO && (u32)f < KRK_EXT_HI)
        f();
}

/* F14 RX Monitor (krk_aprsrx.c, docs/aprs-rx-monitor.md). krk_screen_owner (main blob .bss) holds KRK_SCR_MAGIC while
 * krk_run_screen runs any screen: the monitor then stays out of the audio path and the decoder (a plain flag would be
 * stale SRAM noise at power-up; only the exact magic counts). krk_mon_yield() releases the path now (called by the runner
 * before enter() and by the analog beacon before it keys); krk_aprs_tx_busy() is the beacon's send-in-progress state. */
#define KRK_SCR_MAGIC 0x53435252u
extern volatile u32 krk_screen_owner;
void krk_mon_tick(void);                    /* bg-loop hook body (krk_periodic.c krk_hook_bg_tick) */
void krk_mon_yield(void);
void krk_mon_set(int on);
int krk_mon_on(void);
int krk_aprs_tx_busy(void);
/* F14b APRS RX alert (krk_aprs_alert.c): RX Monitor "Alert" shows a stock popup at standby for a decoded packet. */
void krk_aprs_alert_frame(const char *call, const char *info);   /* after every decoded frame: the newest ring entry (krk_aprsrx.c harvest) */
void krk_aprs_alert_tick(void);                 /* bg-loop hook body (krk_periodic.c krk_hook_bg_tick) */
void krk_aprs_alert_set(int on);
int krk_aprs_alert_on(void);                    /* Alert set and the Monitor on */
extern const char *const krk_mon_labels[3];     /* "Off", "On", "Alert" */

/* Standby tags (krk_tags.c, docs/standby-tags.md): each feature's "on and in effect on this channel
 * now" read. All in blob 2 except krk_sat_active (blob 3, imported). */
#define KRK_VOX_TAG_TEST 1u                     /* krk_vox_tag(): VOX Monitor test mode set */
#define KRK_VOX_TAG_SW   2u                     /* the channel's VOX SW on */
unsigned krk_vox_tag(void);
int krk_cwid_active(void);
int krk_beacon_active(void);
int krk_sat_active(void);
int krk_sq_active(void);
int krk_parrot_armed(void);
extern volatile u32 krk_tags_shown;             /* main blob .bss: the tag set last drawn (krk_tags.c) */
void krk_tags_tick(void);                       /* maintenance-task tick (krk_periodic.c) */

/* Runs s until Back; then calls restore() to refill the sub-list cells of the list it came from. */
void krk_run_screen(const struct krk_screen *s, void (*restore)(void));

/* Text helpers. krk_text clears the background under the string; krk_line draws on line n
 * (0-based) of the content area. */
void krk_text(int x, int y, const char *s, u16 fg);
void krk_line(int n, const char *s, u16 fg);
/* Formats v as decimal into buf (at least 12 bytes) and returns buf. */
char *krk_itoa(int v, char *buf);
/* Appends src to dst (dst must have room) and returns dst. */
/* krk_cat appends src to dst, never writing past KRK_LINE_BUF bytes in total: EVERY krk_cat
 * destination must hold KRK_LINE_BUF bytes (v35 hardware freeze: a 32-byte header buffer
 * overflowed into the saved return address, 2026-10-27). krk_catn takes the real size instead. */
#define KRK_LINE_BUF 64
char *krk_cat(char *dst, const char *src);
char *krk_catn(char *dst, unsigned size, const char *src);
/* krk_cati appends v in decimal (krk_cat(dst, krk_itoa(v, num)) without the buffer). krk_catfix appends v/div with
 * log10(div) fraction digits, the integer part space-padded to w (see krk_core.c). */
char *krk_cati(char *dst, int v);
char *krk_catfix(char *dst, int v, int div, int w);
/* krk_pad pads buf to KRK_COLS with spaces; krk_line_cached draws a padded line only when it changed (hash[n]); krk_fmt_age
 * writes seconds as "12s"/"5m"/"3h" (out: 12 bytes). */
void krk_pad(char *buf);
void krk_line_cached(u32 *hash, int n, const char *s, u16 fg);
void krk_fmt_age(char *out, u32 sec);
/* UART0 (debug port): one character / a string, waiting for TX FIFO room with a guard; krk_hex writes v as digits hex digits. */
void krk_uart_putc(u8 c);
void krk_uart_puts(const char *s);
char *krk_hex(char *out, u32 v, int digits);

/* Current GPS fix from stock's last valid RMC sentence (krk_grid.c). lat/lon are signed, in
 * 1e-4 arc-minutes (north/east positive). lat_s/lon_s are the RMC's own "ddmm.mmmm"/"dddmm.mmmm"
 * text. Returns 1 with a valid fix, else 0. */
struct krk_gps_fix {
    int lat, lon;
    char lat_s[12], lon_s[12];
    char ns, ew;
    char utc[8];                /* "hhmmss" */
};
int krk_gps_get(struct krk_gps_fix *f);

/* ---- stock option level and stock popups (krk_optrow.c, docs/stock-option-level.md) ----------------------------------------
 * A KRAKEN (or analog-APRS) list row whose struct krk_screen has .opt set opens the genuine stock option level: the radio-mark
 * list of "Settings > Radio Settings > Other Func > Auto PowerOff". Up/Down/OK/Back/scroll are the stock key handlers; our code only
 * (1) fills the list cells like the stock case block 0x0303d634 (krk_opt_enter), (2) supplies the header text (krk_opt_title) and
 * (3) commits on OK (krk_opt_ok), which then shows the stock "Setting is OK" popup and pops the level exactly as stock does. */
struct krk_opt {
    const char *title;                  /* header text, at most 19 characters; NULL = the row's label */
    const char *const *labels;          /* n ASCII option labels, in flash (the stock renderer reads the pointers directly); NULL = numeric entry */
    u8 n;                               /* option list: 2..8 options (8 = one screenful; the cursor math assumes no scrolling); numeric: 1..8 digits */
    int (*get)(void);                   /* option list: current index 0..n-1; numeric: current value (read when the level opens) */
    void (*set)(int v);                 /* OK: commit option index / number (RAM-only state per policy P4) */
    u32 min, max;                       /* numeric entry only: accepted range, inclusive */
    const char *(*title_fn)(void);      /* optional: dynamic header (option list only; overrides title), at most 19 characters, in a static buffer */
    void (*after)(int idx);             /* optional: option list only, runs after the stock "Setting is OK" popup (+ pop), e.g. a follow-up warning popup */
    int (*confirm)(int idx);            /* optional: option list only, runs on OK BEFORE set(); 0 = refused: no set, no "Setting is OK", the level is
                                         * popped. It may draw over the level (blocking, keypad its own) but must leave the content area cleared. */
};
/* ---- nested child lists (docs/nested-submenu.md, docs/otherfunc-submenu.md, docs/chanset-submenu.md 8) ----------------------
 * There is no .kids field: a nest is built from stock's own per-level machinery. A level's cells are filled by that level's RENDER
 * entry, which runs after every OK handler; an OK handler only calls set_mode(next, 1). Other Func nests in stock's tables
 * (krk_vox.c); the Channel Set Burst Voice parent (2026-11-06, krk_bvcfg.c) has its own entries in the relocated Channel Set
 * tables and three splices in Channel Set's level-5 init, OK and header. The 2026-11-03 attempt, which installed a child's cells
 * from the OK handler, is kept in docs/nested-submenu.md with its measurements. */

/* ---- stock-menu hosts (krk_core.c) ---------------------------------------------------------------------------------------------
 * A host is a stock sub-list we append rows to (APRS, Call Log, GNSS, Radio Settings, Channel Set). Every host does the same things
 * with its rows - list them, open one (option level or screen), commit one's option level, refill the list's cells afterwards - so
 * that is done once, here, from a struct krk_host. What differs per host is only how stock reaches us (a generator splice, a compare
 * chain, a jump table: krk_stubs.S + the manifest's hand-written splices), which stays in each host's few lines in krk_core.c.
 * Rows, labels, item numbers, counts and dispatch bounds are generated from menu_hosts.json (tools/gen_menu_hosts.py). */
struct krk_host {
    const struct krk_screen *const *rows;   /* slot k = item first + k */
    void (*restore)(void);                  /* refills the host list's cells after one of its screens or a commit */
    u8 first, n;                            /* item number of slot 0 (= stock row count), slot count */
    u8 gen;                                 /* menu_option_list_dispatch mode that lists the rows (krk_hook_gen), 0 = the descriptor count does */
    u8 ext;                                 /* the rows sit in an extension blob: range-check every pointer (a blob may not be flashed) */
};
const struct krk_screen *krk_host_row(const struct krk_host *h, unsigned item);   /* the row of item, NULL if none (out of range, empty slot, blob missing) */
void krk_host_open(const struct krk_host *h, unsigned item);    /* OK on the row: open its option level, or run its screen with h->restore */
void krk_host_commit(const struct krk_host *h, unsigned item);  /* OK inside its option level: commit, then h->restore */
void krk_row_open(const struct krk_screen *e, void (*restore)(void));   /* the open step on its own (NULL e: nothing) */
void krk_hook_gen(u32 mode, volatile u16 *count, volatile u8 *list);  /* splice target at every hooked generator call: stock's list, then ours */

void krk_list_cells(const char *const *labels, unsigned n);   /* count, strings, subtype 2, sequential index list (krk_core.c) */
void krk_category_entry(void);                        /* refills the KRAKEN list cells (krk_core.c) */
void krk_opt_enter(const struct krk_opt *o);          /* open the level for the row just selected (item-action context) */
void krk_opt_ok(void);                                /* splice target: OK on a level-2 screen of the KRAKEN category */
void krk_opt_commit(const struct krk_opt *o, void (*restore)(void));   /* the commit sequence shared by every category's OK hook */
void krk_aprs_opt_ok(unsigned item);                  /* splice target (krk_aprs_ok_stub): OK at the option level of an appended APRS row */
void krk_cs_item(unsigned item);                      /* OK on a row appended to stock Channel Set (krk_cs_item_stub): open its option level or run its screen */
void krk_cs_opt_ok(unsigned item);                    /* OK INSIDE that option level (krk_cs_ok_stub): commit it */
const char *krk_opt_title(void);                      /* splice target: header text of an option list (category >= 10) */
const char *krk_num_title(void);                      /* splice target: header text of a numeric entry (any category: stock 'Input ID' unless ours) */
const struct krk_opt *krk_open_opt(void);             /* the option/numeric spec of the open level-2 screen (category 10 row or appended APRS item), else NULL */
const struct krk_screen *krk_row_screen(unsigned row);   /* the screen of a KRAKEN list row (rebuilds the list), NULL if out of range */
#define KRK_SELECTED_ROW (*(volatile u8 *)0x0003e4ea)    /* highlighted row of the open sub-list (docs/sublist-cursor.md) */

/* Stock popup box (the "Setting is OK" dialog): a 194 x 160 grey box, a 48 x 48 icon, one or two centered black lines (y = 144 and
 * 174, 16 characters fit the box). Icons are the stock bitmaps 0x0309df80 (i), 0x0309cd80 (tick), 0x0309bb80 (cross), 0x0309a980 (warning). */
#define KRK_POP_INFO 0
#define KRK_POP_OK   1
#define KRK_POP_ERR  2
#define KRK_POP_WARN 3
#define POP_RESULT_MS 1800          /* a result popup stays this long */
int krk_tx_arm(const char *line1);  /* "<line1> / TRANSMITS" arm popup; 1 if OK was pressed again (krk_aprs_menu.c) */
extern const char *const krk_onoff_labels[2];   /* "ON", "OFF" (index 0 = on; krk_cwid.c) */
extern const char *const krk_offon_labels[2];   /* "Off", "On" (index 1 = on; krk_scramble.c) */
int krk_ui_in_menu(void);           /* stock screen-mode byte says a menu is open (krk_cwid.c) */
void krk_real_time(u32 *last, u8 *rsec, u32 *rtick, u32 now);   /* moves *last so an OS-tick interval keeps RTC time (power save runs the tick ~2x fast); *rsec = 0xff restarts it (krk_cwid.c) */
int krk_nearest8(const u8 *vals, unsigned v);   /* index of the entry of vals[8] nearest to v (krk_cwid.c) */
/* Draws the popup over whatever is on screen (no clear, no wait). */
void krk_popup_draw(int kind, const char *line1, const char *line2);
/* Draws it, then waits up to ms (yielding every 50 ms; Back is not special here). Returns the first key PRESSED during the wait
 * (a key already down when the call starts is ignored until it is released), SCAN_NONE on timeout. ms = 0 draws only. Valid only
 * inside a screen runner (enter()/tick() of a screen, and KRK_SF_ONESHOT enter()): the runner has parked the stock keypad poll. */
u8 krk_popup(int kind, const char *line1, const char *line2, unsigned ms);
/* One scan of the keypad under the bus semaphore, for code that is already inside a runner. */
u8 krk_key_scan(void);

#endif
