/*
 * mi_rng_tap.ld -- linker script for the MEASUREMENT build of the MI-RNG patch (mi_rng.ld plus the
 * sample-tap buffer in its own claim, mi_rng_tap_buf). Code and state live in this patch's own
 * claims (allocations.json in this folder: mi_rng_code, mi_rng_state), via the own-memory file the builder
 * generates from the manifest's "allocations".
 */
INCLUDE dm32_patch_own_memory.ld
INCLUDE dm32_stock_symbols.ld

/*
 * The AES routines run from the IRAM copy of the load image, not from their flash-view address.
 * dm32_stock_symbols.ld gives flash-view addresses (0x030ccbcc, 0x030ccc9c); the IRAM load image
 * is flash 0x030bfc20.. copied to IRAM 0x00010000.. (docs/encryption.md section 2; the same
 * delta crypto_kat.py uses and verifies against FIPS-197).
 */
IRAM_DELTA = 0x030BFC20 - 0x00010000;
mi_aes_key_schedule = aes_key_schedule_expand - IRAM_DELTA;
mi_aes_encrypt = aes_block_encrypt - IRAM_DELTA;   /* corpus name since 2026-10-14 (was block_cipher_encrypt_16byte_variant_a) */

ENTRY(mi_rng_entry)

SECTIONS
{
    /* Entry first, by name (GCC's emission order is not source order). mi_rng_step is KEEP'd
     * only so the QEMU test can call it; nothing in stock reaches it. */
    .text : {
        KEEP(*(.text.mi_rng_entry))
        KEEP(*(.text.mi_rng_step))
        *(.text .text.*)
        *(.rodata .rodata.*)
    } > PATCH_OWN_FLASH

    .tap (NOLOAD) : {
        *(.bss.mi_rng_tap)
    } > PATCH_OWN_MI_RNG_TAP_BUF

    .bss (NOLOAD) : {
        *(.bss .bss.*)
        *(COMMON)
    } > PATCH_OWN_MI_RNG_STATE

    /* Nothing copies initialised RAM data from flash at boot, so a patch cannot have any. */
    .data : { *(.data .data.*) } > PATCH_OWN_MI_RNG_STATE
    ASSERT(SIZEOF(.data) == 0, "patch has initialised writable data (.data): nothing copies it to RAM; make it const or zero-initialised")
}
