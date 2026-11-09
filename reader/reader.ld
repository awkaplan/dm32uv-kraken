/* Kraken reader: one RAM image the boot ROM DOWNLOADs to IRAM at 0x00010000 and RUNs at _start (first
 * byte). Everything, data included, is in the downloaded image; .bss is cleared by start.S. The stack
 * sits well above the image, inside IRAM (0x00010000-0x00057fff). */
ENTRY(_start)
SECTIONS
{
    . = 0x00010000;
    .text : { KEEP(*(.text.start)) *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) *(.sdata*) }
    . = ALIGN(4);
    __image_end = .;
    .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; }
    __stack_top = 0x00020000;
    /DISCARD/ : { *(.comment) *(.note*) *(.csky_stack_size) }
}
