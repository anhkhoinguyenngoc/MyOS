/*
 * MyOS M7.11 — Apple A8 PongoOS `bootr` RAW entry trampoline (stack-owning).
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW
 *         [NOT qemu-virt; NOT SecureROM-DFU exec; NOT checkra1n -k]
 *
 * Handoff contract (source-verified; M7.10 report section, pinned cb83dd8):
 *   entry : physical upload-buffer base — entry.c:309-312 jumps to
 *           loader_xfer_recv_data - kCacheableView + 0x800000000, so this
 *           symbol must link at file offset 0 (ASSERT in raw_entry.ld).
 *   EL1 at entry; MMU off; D-cache off; I-cache invalidated; DAIF masked.
 *   x0  = boot_args (raw pointer; preserved and recorded, never dereferenced
 *         here). x8 = gEntryPoint. x30 = image base. x1..x3 = 0.
 *   sp  : inherited PongoOS C stack — NOT read and NOT used: this trampoline
 *         overwrites the active stack pointer (SP_EL0 bank; PongoOS leaves
 *         SPSel=0 per M7.10) with the image-owned stack below before the
 *         first stack access. SP_EL1 (PongoOS exception stack) is not
 *         assumed either.
 *
 * Stack ownership: the stack is a file-backed region INSIDE this image
 * (zero-filled .stack section). The uploader writes the whole image into ONE
 * contiguous PongoOS alloc_contig allocation (usbloader.c), so the stack
 * cannot overlap anything the image itself does not already occupy; nothing
 * frees or reuses the buffer on the bootr path (pongo_boot_raw clears only
 * loader_xfer_recv_count; usb_teardown touches USB registers only).
 *
 * Register use: x19 holds the preserved boot_args across setup (nothing else
 * from PongoOS is live; the image never returns to PongoOS — tail branch
 * below, no `bl`). Every address is PC-relative (`adr`) or a link-resolved
 * PC-relative delta; no absolute address is materialised anywhere, so the
 * image runs at the dynamic alloc_contig buffer base.
 *
 * M7.13 exception capture: after the owned stack is active and before C is
 * entered, this trampoline installs the image's own EL1 vector table
 * (platform/apple/a8/raw_vectors.S) into VBAR_EL1 and issues ISB so the
 * write takes effect (same sequence as pongoos-src `_set_vbar_el1` and
 * arch/aarch64/vectors.S `vbar_el1_install`). Until that msr, any exception
 * goes to PongoOS's inherited table (unchanged pre-install behavior). The
 * table address is `adr`-relative like everything else, so it relocates with
 * the upload buffer; its 2 KiB alignment at RUN time holds only if the
 * alloc_contig upload base is at least 2 KiB aligned — page-granular
 * physical allocation implies that, but it is a source-derived assumption,
 * not an observed value. No absolute address is invented here.
 *
 * Status: IMPLEMENTED offline. No device test performed; every runtime
 * property remains UNVERIFIED until a physical run.
 */
    .section .text.raw, "ax"
    .align 2
    .global myos_a8_raw_entry
    .type myos_a8_raw_entry, %function

myos_a8_raw_entry:
    mov     x19, x0                 /* preserve boot_args (x0) first       */
    adr     x9, __stack_top         /* owned stack top (PC-relative)       */
    mov     sp, x9                  /* replace inherited sp entirely       */
    adr     x9, __vectors           /* image-relative vector table base    */
    msr     vbar_el1, x9            /* install OUR table (was PongoOS's)   */
    isb                             /* VBAR writes need ISB to take effect */
    mov     x0, x19                 /* AAPCS argument register = boot_args */
    b       myos_a8_raw_main        /* C entry parks; no return path       */

    .size myos_a8_raw_entry, .-myos_a8_raw_entry

/*
 * Owned stack region: file-backed (zero-filled) so the uploaded bytes cover
 * it; the 16-byte-aligned top satisfies the AAPCS stack-alignment
 * requirement. Size 0x4000 (16 KiB) is a payload design choice for future C
 * growth, consistent with PongoOS's own per-stack reservation granularity
 * (boot/entry.S: `_exception_stack` + 0x4000); it is not a hardware
 * guarantee. The region sits above the entry code and the report block, so
 * overflow can only reach image-owned data, never foreign memory. The M7.13
 * vector table and exception record live ABOVE the stack top, where
 * downward stack growth cannot reach them.
 */
    .equ RAW_STACK_SIZE, 0x4000
    .section .stack, "aw", @progbits
    .balign 16
    .global __stack_bottom
__stack_bottom:
    .skip RAW_STACK_SIZE
    .balign 16
    .global __stack_top
__stack_top:
