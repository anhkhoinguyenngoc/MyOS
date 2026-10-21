/*
 * MyOS M7.11 — linker script for the Apple A8 PongoOS `bootr` RAW entry
 * trampoline + C continuation (stack-owning).
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW.
 *
 * The image is POSITION-INDEPENDENT (raw_entry.S uses `adr` only; the C code
 * is freestanding with no absolute relocations), so the link base is
 * arbitrary: it is linked at 0 so that file offsets equal link addresses and
 * `objcopy -O binary` yields the exact upload bytes. The runtime base is the
 * dynamic PongoOS alloc_contig upload-buffer base — no fixed physical
 * address is invented anywhere.
 *
 * Pins (ASSERTed below and cross-checked by tests/test_a8_raw_entry.py):
 *   entry   == 0x0     (entry.c jumps to the buffer START)
 *   .report == 0x200   (report block; mirrors the M-P1 probe convention)
 *   stack   == 0x4000 bytes, 16-byte aligned, above the report block
 *   vectors : VBAR_EL1 requires bits[10:0] == 0, so the M7.13 table is
 *             ALIGN(0x800) above the stack and exactly 16 slots of 128
 *             bytes; it must not overlap the stack
 *   record  : the M7.13 exception record sits directly after the table,
 *             8-byte aligned, exactly EXC_SIZE (0x48) bytes, outside the
 *             stack region
 *   size    <= 0x100000 (default 1 MiB PongoOS upload buffer, usbloader.c
 *            UPLOADSZ; the host may enlarge the buffer via the documented
 *            resize request, but the default capacity is the contract this
 *            script asserts against).
 */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(myos_a8_raw_entry)

SECTIONS
{
    . = 0x0;
    .text : {
        KEEP(*(.text.raw))
        *(.text*)
        *(.rodata*)
    }
    .report 0x200 : {
        KEEP(*(.report))
        KEEP(*(.report.*))
    }
    .stack : {
        KEEP(*(.stack))
    }
    /* VBAR_EL1 alignment: pad from the stack end up to the 2 KiB boundary
     * (the only gap in the flat image; zero-filled, asserted by tests). */
    .vectors ALIGN(0x800) : {
        KEEP(*(.vectors))
    }
    .excrecord : {
        KEEP(*(.excrecord))
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.eh_frame_hdr)
        *(.gnu*)
    }
}

/* bootr jumps to the upload-buffer start: entry must be at file offset 0. */
ASSERT(myos_a8_raw_entry == 0x0, "raw entry must be at file offset 0");
/* Report block pinned at 0x200 (static header verifiable before bootr). */
ASSERT(raw_entry_report == 0x200, "raw entry report must be at file offset 0x200");
/* The stack is image-owned and must satisfy the AAPCS alignment bounds. */
ASSERT((__stack_bottom & 0xf) == 0, "stack bottom must be 16-byte aligned");
ASSERT((__stack_top & 0xf) == 0, "stack top must be 16-byte aligned");
ASSERT(__stack_top - __stack_bottom == 0x4000, "stack size must be RAW_STACK_SIZE (0x4000)");
/* Stack must sit above the report block (section order makes this structural). */
ASSERT(__stack_bottom >= raw_entry_report + 0x40, "stack must not overlap the report block");
/* Vector table: VBAR_EL1 alignment, exact geometry, outside the stack. */
ASSERT((__vectors & 0x7ff) == 0, "vector table must satisfy the VBAR_EL1 2 KiB alignment");
ASSERT(SIZEOF(.vectors) == 0x800, "vector table must be 16 slots of 128 bytes");
ASSERT(__vectors >= __stack_top, "vector table must not overlap the stack");
/* Exception record: follows the table exactly, aligned, fixed size, outside
 * the stack region (stack grows down, so the record is structurally safe). */
ASSERT(myos_a8_exc_record == __vectors_end, "exception record must follow the vector table");
ASSERT((myos_a8_exc_record & 7) == 0, "exception record must be 8-byte aligned");
ASSERT(myos_a8_exc_record >= __stack_top, "exception record must not overlap the stack");
ASSERT(SIZEOF(.excrecord) == 0x48, "exception record must be EXC_SIZE (0x48)");
/* Whole image must fit the default PongoOS upload buffer. */
ASSERT(. <= 0x100000, "raw entry image exceeds the 1 MiB PongoOS upload buffer");
