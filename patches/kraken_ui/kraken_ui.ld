/*
 * kraken_ui.ld - code in the kraken_ui_code claim, state in kraken_ui_state
 * (../../allocations.json), via the own-memory file the builder generates. The data tables the
 * manifest places itself live in the separate kraken_ui_data claim.
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

ENTRY(krk_item_ok)

SECTIONS
{
    .text : {
        KEEP(*(.text.krk_item_ok))
        KEEP(*(.text.krk_category_entry))
        KEEP(*(.text.krk_aprs_item))
        KEEP(*(.text.krk_aprs_render .text.krk_channel_analog .text.krk_channel_on_selected .text.krk_channel_side .text.krk_channel_flags .text.krk_channel_mode .text.krk_channel_freq))
        KEEP(*(.text.krk_opt_ok .text.krk_opt_title .text.krk_opt_enter .text.krk_row_screen .text.krk_aprs_opt_ok .text.krk_opt_commit .text.krk_num_title .text.krk_open_opt))   /* splice targets (krk_optrow.c) */
        KEEP(*(.text.krk_gnss_item .text.krk_rs_item .text.krk_settings_opt_ok .text.krk_settings_ok_stub))   /* stock GNSS + Radio Settings rows (docs/gnss-submenu.md, docs/settings-submenu.md) */
        KEEP(*(.text.krk_cs_item .text.krk_cs_opt_ok .text.krk_cs_item_stub .text.krk_cs_ok_stub))   /* stock Channel Set rows (docs/chanset-submenu.md) */
        KEEP(*(.text.krk_popup .text.krk_popup_draw .text.krk_key_scan))                  /* exported to the extension blobs (defsym) */
        KEEP(*(.text.krk_hook_*))           /* reached only from splices: --gc-sections can't see them */
        /* exported to the extension blob (kraken_ui2) by --defsym: nothing here may call them */
        KEEP(*(.text.krk_line .text.krk_text .text.krk_cat .text.krk_catn .text.krk_itoa .text.krk_gps_get .text.krk_cati .text.krk_catfix .text.krk_pad .text.krk_line_cached .text.krk_fmt_age .text.krk_uart_putc .text.krk_uart_puts .text.krk_hex))
        KEEP(*(.text))
        *(.text.*)
        *(.rodata .rodata.*)
    } > PATCH_OWN_KRAKEN_UI_CODE

    .bss.krk_list (NOLOAD) : {
        *(.bss.krk_list)
    } > PATCH_OWN_KRAKEN_UI_STATE2

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
    } > PATCH_OWN_KRAKEN_UI_STATE

    .data : { *(.data .data.*) } > PATCH_OWN_KRAKEN_UI_STATE
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
