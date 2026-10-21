/*
 * MyOS M7.11 — C continuation of the Apple A8 PongoOS `bootr` RAW entry.
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW
 *         [NOT qemu-virt; NOT SecureROM-DFU exec]
 *
 * Entered by myos_a8_raw_entry (raw_entry.S) on the image-owned stack with
 * x0 = boot_args (raw pointer — recorded; dereferenced only by the explicit
 * opt-in display demo below). Runtime
 * preconditions (source-verified, M7.10 report section): EL1, MMU off,
 * D-cache off, DAIF masked. The trampoline inserts the image's own vector
 * table into VBAR_EL1 before entering this function (raw_entry.S:57-59); the
 * inherited PongoOS table is only what a fault would use before that msr.
 *
 * This function records entry evidence into the file-backed report block at
 * file offset 0x200 (its static part — magic/version/size — is verifiable in
 * the uploaded binary BEFORE `bootr`), validates its own stack bounds, then
 * parks. The park is a branch-to-self, deliberately NOT `wfe`: HCR_EL2 is
 * unknown and TWE could trap; a branch loop cannot fault (same reasoning as
 * pongo_probe.S).
 *
 * Default build: no console, no display/controller MMIO, no
 * MMU/cache/interrupt changes, no hardware address. The explicit opt-in demo
 * below instead writes only the inherited framebuffer pixels.
 * The report layout mirrors the M-P1 probe convention (magic/version/size
 * header + u64 fields); the offsets are pinned here and cross-checked by
 * tests/test_a8_raw_entry.py against raw_entry.ld and the built image.
 *
 * Optional display demo (MYOS_A8_RENDER_HELLO=1): after the report block is
 * complete, draw "Hello User" into the inherited boot_args Video framebuffer.
 * This is DEFAULT-OFF: the actual boot_args/Video values and scanout are not
 * observed, and the production image must keep its audited no-dereference
 * property. Static source trace is consistent with the raw image receiving
 * the identity/physical view (pongo_entry passes gBootArgs unchanged into the
 * MMU-off jump; only the XNU path uses kCacheableView), but it does not prove
 * the actual pointer value; the demo remains a
 * separate, opt-in image (`make raw-entry-hello`) and is never part of
 * build/raw_entry.bin.
 */
#include <stdint.h>

#if defined(MYOS_A8_RENDER_HELLO) && MYOS_A8_RENDER_HELLO
#include "platform/apple/a8/a8_fb.h"
#endif

#define RAW_REPORT_SIZE 0x40u

typedef struct raw_entry_report {
    char     magic[8];      /* +0x00 "MYOSA8R1"                     */
    uint32_t version;       /* +0x08 report version (1)             */
    uint32_t report_size;   /* +0x0C RAW_REPORT_SIZE                */
    uint64_t boot_args;     /* +0x10 x0 as delivered (raw)          */
    uint64_t sp_at_entry;   /* +0x18 active sp inside this function */
    uint64_t stack_bottom;  /* +0x20 __stack_bottom                 */
    uint64_t stack_top;     /* +0x28 __stack_top                    */
    uint64_t stack_checks;  /* +0x30 RAW_STACK_CHK_* bits           */
    uint64_t reserved;      /* +0x38 must stay 0                    */
} raw_entry_report_t;

/* Layout is a cross-file contract (ld: ASSERT(raw_entry_report == 0x200)). */
_Static_assert(sizeof(raw_entry_report_t) == RAW_REPORT_SIZE,
               "raw entry report must be RAW_REPORT_SIZE bytes");

volatile raw_entry_report_t raw_entry_report
    __attribute__((section(".report"))) = {
    .magic = { 'M', 'Y', 'O', 'S', 'A', '8', 'R', '1' },
    .version = 1u,
    .report_size = RAW_REPORT_SIZE,
    /* dynamic fields (+0x10..+0x38) stay zero in the file: written at run time */
};

/* Linker-defined bounds of the image-owned stack (raw_entry.S / raw_entry.ld). */
extern char __stack_bottom[];
extern char __stack_top[];

/* stack_checks bits: each must be observed set on the verified entry path. */
#define RAW_STACK_CHK_WITHIN      (1u << 0)   /* bottom <= sp < top   */
#define RAW_STACK_CHK_SP_ALIGNED  (1u << 1)   /* sp % 16 == 0         */
#define RAW_STACK_CHK_TOP_ALIGNED (1u << 2)   /* stack_top % 16 == 0  */

void myos_a8_raw_main(uint64_t boot_args)
{
    uint64_t sp_now;

    __asm__ volatile ("mov %0, sp" : "=r"(sp_now));

    uint64_t bottom = (uint64_t)(uintptr_t)__stack_bottom;
    uint64_t top = (uint64_t)(uintptr_t)__stack_top;

    uint64_t checks = 0;
    if (sp_now >= bottom && sp_now < top)
    {
        checks |= RAW_STACK_CHK_WITHIN;
    }
    if ((sp_now & 0xf) == 0)
    {
        checks |= RAW_STACK_CHK_SP_ALIGNED;
    }
    if ((top & 0xf) == 0)
    {
        checks |= RAW_STACK_CHK_TOP_ALIGNED;
    }

    raw_entry_report.boot_args = boot_args;
    raw_entry_report.sp_at_entry = sp_now;
    raw_entry_report.stack_bottom = bottom;
    raw_entry_report.stack_top = top;
    raw_entry_report.stack_checks = checks;

#if defined(MYOS_A8_RENDER_HELLO) && MYOS_A8_RENDER_HELLO
    /*
     * Opt-in only. The adapter performs no display initialization: it reads
     * the six Video words from boot_args and, only if the geometry passes
     * every check, fills a black box and draws "Hello User". Any fault here
     * is captured by raw_vectors.S (the report above is already complete).
     *
     * reserved slot (+0x38) now holds demo status code:
     *   0 = MYOS_A8_FB_OK
     *   non-zero = error code from myos_a8_fb_status_t enum
     * This is a diagnostic for physical validation; not used in production.
     */
    raw_entry_report.reserved = (uint64_t)myos_a8_fb_draw_hello(boot_args, NULL);
#endif

    for (;;)
    {
    }
}
