/*
 * kraken_ui3.ld - the F10 satellite blob in kraken_ui3_code (0x030f8000, 32 KiB), state in
 * kraken_ui3_state (1 KiB). The screen descriptor comes FIRST so that krk_sat_screen is the blob's
 * entry at the origin (the manifest's expect_entry); kraken_ui2 binds krk_sat_screen and the 1 Hz
 * tick krk_sat_tick by --defsym "@kraken_ui3:...". Helpers from the main blob are bound with
 * --defsym in the manifest.
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

ENTRY(krk_sat_screen)

SECTIONS
{
    .text : {
        KEEP(*(.rodata.krk_sat_screen))
        KEEP(*(.text.krk_sat_tick))
        /* the APRS callsign editor (krk_callsign.c, moved here from kraken_ui2 on 2026-10-30): bound by kraken_ui2 with
         * --defsym "@kraken_ui3:...", so --gc-sections must not drop what only blob 2 references */
        KEEP(*(.rodata.krk_callsign_screen .text.krk_call_get .text.krk_aprs_cfg_path .text.krk_aprs_cfg_set_path))
        KEEP(*(.text .text.*))
        *(.rodata .rodata.*)
    } > PATCH_OWN_KRAKEN_UI3_CODE

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
    } > PATCH_OWN_KRAKEN_UI3_STATE

    .data : { *(.data .data.*) } > PATCH_OWN_KRAKEN_UI3_STATE
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
