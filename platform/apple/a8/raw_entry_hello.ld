/*
 * platform/apple/a8/raw_entry_hello.ld — opt-in "Hello User" raw demo image.
 *
 * target: iphone6 (Apple A8) via PongoOS BOOT_FLAG_RAW.
 *
 * This is NOT the production raw_entry.bin layout. The production image
 * (raw_entry.ld) must keep ALL of its code before the pinned report block at
 * 0x200 because its artifact is part of the audited baseline. The opt-in demo
 * adds the renderer and A8 adapter (about 2 KiB), so those translation units
 * are placed in `.code` AFTER the vector table and exception record. The
 * handoff-critical layout is identical to production:
 *
 *   entry    == 0x0     (entry.c jumps to the upload-buffer start)
 *   .report  == 0x200   (unchanged report block/ABI)
 *   .stack   == 0x4000 bytes, 16-byte aligned
 *   .vectors == 2 KiB aligned, exactly 16 slots
 *   .excrecord follows it, 0x48 bytes
 *   .code    ALIGN(16) after the exception record
 *
 * The image remains position-independent: the trampoline uses `adr` only and
 * the C objects use PC-relative references (no absolute relocation may
 * survive the link). It is still bounded by the default 1 MiB PongoOS upload
 * buffer. tests/test_a8_hello_image.py validates this layout and the rendered
 * call path; the production raw-entry-check is unaffected.
 */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(myos_a8_raw_entry)

SECTIONS
{
    . = 0x0;
    /* Handoff-critical trampoline only: everything else may move below. */
    .text : {
        KEEP(*(.text.raw))
    }
    .report 0x200 : {
        KEEP(*(.report))
        KEEP(*(.report.*))
    }
    .stack : {
        KEEP(*(.stack))
    }
    .vectors ALIGN(0x800) : {
        KEEP(*(.vectors))
    }
    .excrecord : {
        KEEP(*(.excrecord))
    }
    /* Renderer + adapter + font: never executed before the owned stack and
     * vector table are active. */
    .code ALIGN(16) : {
        *(.text*)
        *(.rodata*)
    }
    .data ALIGN(16) : {
        *(.data*)
    }
    /* A raw upload has no loader to zero BSS: an uninitialized global would
     * be silently whatever the upload buffer contained. Refuse it. */
    .bss (NOLOAD) : {
        *(.bss*)
        *(COMMON)
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.eh_frame_hdr)
        *(.gnu*)
    }
}

/* Handoff-critical pins (same meanings as raw_entry.ld). */
ASSERT(myos_a8_raw_entry == 0x0, "raw entry must be at file offset 0");
ASSERT(SIZEOF(.text) <= 0x200, "trampoline must stay below the report block");
ASSERT(raw_entry_report == 0x200, "raw entry report must be at file offset 0x200");
ASSERT((__stack_bottom & 0xf) == 0, "stack bottom must be 16-byte aligned");
ASSERT((__stack_top & 0xf) == 0, "stack top must be 16-byte aligned");
ASSERT(__stack_top - __stack_bottom == 0x4000, "stack size must be RAW_STACK_SIZE (0x4000)");
ASSERT(__stack_bottom >= raw_entry_report + 0x40, "stack must not overlap the report block");
ASSERT((__vectors & 0x7ff) == 0, "vector table must satisfy the VBAR_EL1 2 KiB alignment");
ASSERT(SIZEOF(.vectors) == 0x800, "vector table must be 16 slots of 128 bytes");
ASSERT(__vectors >= __stack_top, "vector table must not overlap the stack");
ASSERT(myos_a8_exc_record == __vectors_end, "exception record must follow the vector table");
ASSERT((myos_a8_exc_record & 7) == 0, "exception record must be 8-byte aligned");
ASSERT(myos_a8_exc_record >= __stack_top, "exception record must not overlap the stack");
ASSERT(SIZEOF(.excrecord) == 0x48, "exception record must be EXC_SIZE (0x48)");
ASSERT(SIZEOF(.bss) == 0, "raw upload cannot carry uninitialized globals");
ASSERT(ADDR(.code) >= myos_a8_exc_record + 0x48, "renderer code must follow the exception record");
ASSERT(. <= 0x100000, "raw hello image exceeds the 1 MiB PongoOS upload buffer");
