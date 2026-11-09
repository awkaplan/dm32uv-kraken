/*
 * krk_ext.c - the extension blob's screen table, linked first at 0x030ee000 (kraken_ui2.ld).
 * Slots 0..KRK_EXT_MAX-1: extra rows of the KRAKEN list; then KRK_APRS_MAX slots: the analog rows
 * of the stock APRS sub-list (item 8 = slot 0, krk_core.c krk_aprs_render). krk_core.c reads both by address and range-checks
 * every pointer, so an image without this blob shows no extension rows.
 */
#include "krk.h"

extern const struct krk_screen krk_aprsrx_screen;
extern const struct krk_screen krk_monitor_row;     /* RX Monitor on/off, a stock option level (krk_aprsrx.c) */
extern const struct krk_screen krk_callsign_screen;   /* F-APRS callsign editor, blob kraken_ui3 (manifest defsym) */
extern const struct krk_screen krk_aprs_path_screen;
extern const struct krk_screen krk_aprs_upload_screen;
extern const struct krk_screen krk_scramble_on_screen;  /* F12 Scrambler Off/On: a stock option level */
extern const struct krk_screen krk_scrkey_row;          /* Scrambler Key: a NUMERIC entry row */
extern const struct krk_screen krk_scrfmt_row;          /* Scrambler Fmt: stays a flat KRAKEN row */
extern const struct krk_screen krk_squelch_screen;      /* F11 Squelch Mode: a stock option level */
extern const struct krk_screen krk_sqsurvey_screen;     /* F11 Squelch Survey: the live screen */
extern const struct krk_screen krk_vox_screen;          /* VOX Monitor: the software VOX engine's live screen (krk_vox.c).
                                                         * Its bench predecessor, a VOX Probe screen (slot 0 on
                                                         * 2026-11-04/05) is retired from the build: SRAM budget
                                                         * (.bss.feat shares its claim with blob ui5), findings in
                                                         * docs/otherfunc-submenu.md. */
extern const struct krk_screen krk_txtrim_screen;       /* F6 TX Power Trim: a stock option level (moved here from the main blob) */
extern const struct krk_screen krk_band_screen;         /* Freq Unlock: a stock option level with a hold-OK warning (krk_band.c) */
extern const struct krk_screen krk_sat_screen;     /* F10, blob kraken_ui3 (manifest defsym) */
extern const struct krk_screen krk_parrot_arm_screen;   /* F9 Parrot Off/On: a stock option level */
extern const struct krk_screen krk_parrot_play_screen;  /* one-shots with popups */
extern const struct krk_screen krk_parrot_check_screen;
extern const struct krk_screen krk_parrot_clear_screen;
extern const struct krk_screen krk_parrot_status_screen;
void krk_parrot_rx_start(void);
void krk_parrot_rx_end(void);
void krk_scramble_chan_change(void);

const struct krk_screen *const krk_ext_screens[KRK_EXT_MAX + KRK_APRS_MAX + KRK_RS_MAX + KRK_CS_MAX] = {
    /* KRAKEN, list order = slot order (2026-11-02 regrouping; tests locate rows by symbol or by these indices): 0 VOX Monitor (the VOX engine's bench screen), 1 free, 2 RX Monitor,
     * 3 free, 4 free, 5 Scrambler Fmt, 6 free, 7 F11 Squelch Survey, 8 F10 satellite (blob ui3),
     * 9..13 free, 14 free, 15 free, 16..19 free (a 0 slot is skipped)
     *
     * Slots 3, 4 (F12 Scrambler Off/On and Scrambler Key) and 9..13 (the five F9 Parrot rows) were retired on
     * 2026-11-03: both features moved to the stock Channel Set menu as FLAT rows (the KRK_CS_TABLE entries below,
     * docs/chanset-submenu.md). Nesting them under two parent rows was investigated and does NOT work on this
     * firmware - a child list renders but its own option level cannot commit; see docs/nested-submenu.md. Scrambler Fmt (slot 5)
     * stays here: it is a format/interop knob, not part of the requested { Scrambler On/Off, Key } layout.
     *
     * Slot 1 (APRS RX) was retired from this list on 2026-11-03: it is an APRS feature and the same
     * screen is already item 11 of the stock APRS menu below, so it appeared in two menus. The slot
     * stays in place rather than shifting the later rows up, so the indices tests use do not move.
     * RX Monitor (slot 2) was retired the same way and is now APRS item 12 (KRK_APRS_MAX 5).
     * Slots 0 (TX Power Trim) and 6 (Squelch Mode) were retired on 2026-11-03: both are radio-wide
     * settings, so they moved to the stock Radio Settings menu (the KRK_RS_TABLE entries below,
     * docs/settings-submenu.md). Squelch Survey stays here: it is a live instrument, not a setting.
     * Slots 14 (F13 Burst Voice, blob ui4) and 15 (F3 DMR Link, blob ui5) were retired on 2026-11-03:
     * both are per-channel/digital features, so they moved to the stock Channel Set menu (the
     * KRK_CS_TABLE entries below, docs/chanset-submenu.md). They are still referenced there as the
     * bare blob origins KRK_EXT4_LO / KRK_EXT5_LO, because blob 2 is linked before blobs 4 and 5 and
     * cannot name their symbols; each blob's linker script puts its screen descriptor FIRST, at the
     * blob origin, which is what krk_core.c's ext_ok() range check then accepts. */
    &krk_vox_screen, 0, 0, 0, 0, &krk_scrfmt_row, 0, &krk_sqsurvey_screen, &krk_sat_screen,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0,
    /* The rows of the stock-menu hosts, in item order, generated from menu_hosts.json (krk_menu_rows.h).
     * APRS: the analog rows (path, beacon, callsign, APRS RX, RX Monitor).
     * Radio Settings (docs/settings-submenu.md): radio-wide settings, so they belong in the stock
     * radio-wide menu rather than the KRAKEN category.
     * Channel Set (docs/chanset-submenu.md): DMR Link (blob ui5) and Burst Voice (blob ui4), by blob
     * origin for the reason given above - both blocking screens, not option rows - then the Scrambler
     * and Parrot rows, moved here from the KRAKEN list on 2026-11-03 because both are per-channel
     * digital features and this is the stock per-channel menu. They are FLAT rows: making them NESTED
     * parents with child lists was investigated in full and does not work on this firmware
     * (docs/nested-submenu.md). */
    [KRK_EXT_MAX] = KRK_APRS_ROWS,
    [KRK_EXT_MAX + KRK_APRS_MAX] = KRK_RS_ROWS,
    [KRK_EXT_MAX + KRK_APRS_MAX + KRK_RS_MAX] = KRK_CS_ROWS,
};

/* Entry points called by the main blob (krk.h krk_ext_hook); linked right after the table, at
 * 0x030ee000 + 4 * (KRK_EXT_MAX + KRK_APRS_MAX + KRK_RS_MAX + KRK_CS_MAX). The address is COMPUTED from those
 * constants in krk.h (KRK_EXT_HOOK_TABLE). */
void (*const krk_ext_hooks[KRK_EXT_HOOKS_MAX])(void) __attribute__((section(".rodata.krk_ext_hooks"), used)) = {
    krk_parrot_rx_start, krk_parrot_rx_end, krk_scramble_chan_change, krk_mon_yield,
};
