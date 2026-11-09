/*
 * kraken_ui4.ld - the F13 burst voice blob in kraken_ui4_code (0x030e8000, 24 KiB), state in
 * kraken_ui4_state (1 KiB at 0x18007000) and the TAIL of the shared bigbuf (kraken_bigbuf,
 * 0x18005a00, 5632 B: F9 parrot / F13 own it exclusively, krk_vframe.c krk_vf_big_claim).
 *
 * The screen descriptor comes FIRST, at the origin: krk_ext.c slot 7 is the constant 0x030e8000 (blob2 is
 * built before this blob and cannot name its symbols), and krk_core.c reads the descriptor from there.
 * Right behind it (0x030e8020) sits a one-entry hook table that blob2's maintenance tick calls (the TX
 * watchdog, krk_periodic.c) - same trick as blob2's own krk_ext_hooks.
 *
 * SRAM:  .bss.bvm   modem state (krk_bv_modem.c) in kraken_ui4_state - only touched while F13 owns bigbuf
 *        .bss.bvt   framing + sounding + AMBE state at the end of bigbuf (BVT_SIZE bytes; the payload area
 *                   below it is what the screen hands to the modem). Valid only while F13 owns bigbuf.
 *        .bss       the watchdog + ownership flag (krk_bvscr.c W), magic guarded; the screen state S is in .bss.bvt
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

ENTRY(krk_bv_screen)

SECTIONS
{
    .text : {
        KEEP(*(.rodata.krk_bv_screen))
        . = ALIGN(0x20);                    /* 0x030e8020: hook table that blob2's maintenance tick reads (krk_periodic.c) */
        KEEP(*(.rodata.krk_bv_hooks))
        KEEP(*(.text .text.*))
        *(.rodata .rodata.*)
    } > PATCH_OWN_KRAKEN_UI4_CODE

    .bss.bvm (NOLOAD) : {
        __bvm_start = .;
        *krk_bv_modem*(.bss .bss.* COMMON)
        __bvm_end = .;
    } > PATCH_OWN_KRAKEN_UI4_STATE

    BVT_SIZE = 0x200;
    .bss.bvt (ORIGIN(PATCH_OWN_KRAKEN_BIGBUF) + LENGTH(PATCH_OWN_KRAKEN_BIGBUF) - BVT_SIZE) (NOLOAD) : {
        __bvt_start = .;
        *krk_bv_frame*(.bss .bss.* COMMON)
        *krk_bv_sound*(.bss .bss.* COMMON)
        *krk_ambe*(.bss .bss.* COMMON)
        *(.bss.bvs)                         /* krk_bvscr.c S: the screen state */
        __bvt_end = .;
    } > PATCH_OWN_KRAKEN_BIGBUF
    ASSERT(SIZEOF(.bss.bvt) <= BVT_SIZE, "F13 framing state does not fit the bigbuf tail")

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
    } > PATCH_OWN_KRAKEN_UI4_STATE

    .data : { *(.data .data.*) } > PATCH_OWN_KRAKEN_UI4_STATE
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
