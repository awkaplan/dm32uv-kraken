/*
 * kraken_ui5.ld - the F3 DMR link blob in kraken_ui5_code (0x030e1000, one 4 KiB sector; dm32uv-re's
 * ramdump_probe_code claim, excluded in image.json because that probe is not in this image). Built LAST, so it
 * can bind symbols of every other blob by --defsym, but nothing earlier can: kraken_ui2's screen table
 * reaches the screen through the constant 0x030e1000 (krk_ext.c slot 8), where the descriptor sits first.
 *
 * SRAM: no claim of its own. Its .bss.feat is appended to the shared kraken_feat_state claim right after
 * kraken_ui2's (krk_feat_end, exported by kraken_ui2.ld), so the shared claim keeps its layout and the
 * region bound still catches an overflow.
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

ENTRY(krk_dmrlink_screen)

SECTIONS
{
    .text : {
        KEEP(*(.rodata.krk_dmrlink_screen))
        KEEP(*(.text .text.*))
        *(.rodata .rodata.*)
    } > PATCH_OWN_KRAKEN_UI5_CODE

    .bss.feat krk_feat_end (NOLOAD) : {
        *(.bss.feat)
    } > PATCH_OWN_KRAKEN_FEAT_STATE

    /* plain .bss would start at the claim origin and collide with kraken_ui2: this blob keeps all state in .bss.feat */
    .bss (NOLOAD) : { *(.bss .bss.* COMMON) }
    ASSERT(SIZEOF(.bss) == 0, "kraken_ui5 has plain .bss: put its state in .bss.feat (appended after kraken_ui2's)")

    .data : { *(.data .data.*) }
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
