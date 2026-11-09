/* Entry point of the Kraken reader (reader.c). The boot ROM RUNs it here, at the load address, with
 * unknown register state: interrupts off, our own stack, .bss cleared, then reader_main (never returns). */
    .section .text.start, "ax"
    .global _start
_start:
    psrclr  ie
    lrw     r0, __stack_top
    mov     sp, r0
    lrw     r1, __bss_start
    lrw     r2, __bss_end
    movi    r3, 0
1:  cmphs   r1, r2
    bt      2f
    stw     r3, (r1, 0)
    addi    r1, 4
    br      1b
2:  lrw     r0, reader_main
    jsr     r0
3:  br      3b
