/*
 * MyOS M1 linker script — single flat payload.
 * The load address equals the runtime address: the payload is appended at
 * DFU image buffer + 0x50 (see M1_PLAN.md §3/§4; constant sourced from
 * gaster.c for CPID 0x7000 / SRTG iBoot-1992.0.0.1.19).
 */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(myos_m1_entry)

SECTIONS
{
    . = 0x180380050;
    .text : {
        *(.text)
        *(.text.*)
        *(.rodata*)
    }
    .data : {
        *(.data)
        *(.data.*)
    }
    .bss : {
        *(.bss)
        *(.bss.*)
        *(COMMON)
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.eh_frame_hdr)
    }
}
