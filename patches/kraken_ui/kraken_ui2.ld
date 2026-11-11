/*
 * kraken_ui2.ld - the extension blob in kraken_ui_code2 (0x030ee000). The screen table
 * krk_ext_screens comes FIRST, at the region's origin, because the main blob finds it at that fixed
 * address (krk_core.c KRK_EXT_TABLE) rather than by symbol. Helpers from the main blob are bound
 * with --defsym in the manifest.
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

ENTRY(krk_ext_screens)

SECTIONS
{
    .text : {
        KEEP(*(.rodata.krk_ext_screens))
        KEEP(*(.rodata.krk_ext_hooks))      /* 0x030ee090: hook table the main blob calls (krk.h; address computed there) */
        KEEP(*(.text .text.*))
        *(.rodata .rodata.*)
    } > PATCH_OWN_KRAKEN_UI_CODE2

    .bss.afsk (NOLOAD) : {
        *(.bss.afsk.R)                      /* krk_aprsrx.c: screen state first (20 B), then the monitor state, then the decoder's */
        *(.bss.afsk.M)
        *(.bss.afsk)
    } > PATCH_OWN_KRAKEN_AFSK_STATE

    /* 2026-10-29 features: small state in .bss.feat, the shared F9/F13 buffer in .bss.big
     * (declare with __attribute__((section(".bss.feat"))) / (".bss.big")) */
    .bss.feat (NOLOAD) : {
        *(.bss.feat)
    } > PATCH_OWN_KRAKEN_FEAT_STATE
    /* first free byte of the shared feat claim: kraken_ui5 (built after this blob) puts its own .bss.feat
     * here, bound by the manifest defsym "@kraken_ui2:krk_feat_end" (a linker symbol, not a C one) */
    krk_feat_end = ALIGN(ADDR(.bss.feat) + SIZEOF(.bss.feat), 4);

    .bss.big (NOLOAD) : {
        *(.bss.big)
    } > PATCH_OWN_KRAKEN_BIGBUF

    /* APRS features of 2026-11-11 (issues #3-#7): kraken_ui2_state is full, so their small state has its own claim */
    .bss.aprs (NOLOAD) : {
        *(.bss.aprs)
    } > PATCH_OWN_KRAKEN_APRS_STATE

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
    } > PATCH_OWN_KRAKEN_UI2_STATE

    .data : { *(.data .data.*) } > PATCH_OWN_KRAKEN_UI2_STATE
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
