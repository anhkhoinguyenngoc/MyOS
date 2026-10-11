/*
 * MyOS kernel linker script for QEMU virt
 * Load address: 0x40080000 (standard QEMU virt kernel load address)
 * This is separate from the M1 payload (which loads at 0x180380050)
 */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)

PHDRS
{
    text PT_LOAD FLAGS(5);   /* PF_R | PF_X = 5 */
    data PT_LOAD FLAGS(6);   /* PF_R | PF_W = 6 */
}

SECTIONS
{
    . = 0x40080000;
    .text : ALIGN(0x1000) {
        *(.text.boot)
        *(.text)
        *(.text.*)
        *(.rodata*)
        *(.rodata.*)
    } :text
    .data : ALIGN(0x1000) {
        *(.data)
        *(.data.*)
        *(.bss)
        *(.bss.*)
        *(COMMON)
    } :data
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.eh_frame_hdr)
        *(.gnu*)
    }
}