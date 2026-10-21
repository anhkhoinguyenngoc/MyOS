/*
 * MyOS M-P1 — PongoOS-booted Apple A8 probe (raw image for `bootr`).
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW path
 *         [NOT qemu-virt; NOT SecureROM-DFU exec; NOT checkra1n -k]
 *
 * Freestanding AArch64 assembly only (no C, no libc, no stack, no GOT).
 * Position-independent by construction: every access is PC-relative
 * (adr), no absolute addresses, no literal pools with link addresses,
 * no `bl` (x30 holds the image base on entry), no `sp` use.
 *
 * --- Handoff contract (all PongoOS line refs = pinned 4c9b7541 checkout,
 * --- audited in M2_A11/M2_A16; local report citations below) ---
 *   Entry:  entry.c:309-312 jumps to the upload-buffer START, so this
 *           symbol must link at file offset 0 (M2_A4:309-310: a nonzero
 *           entry_offset would need a trampoline).
 *   x30 = first raw-payload address (buffer physical alias)  (M2_A11:101-102)
 *   x0  = gBootArgs (CACHEABLE VA, not dereferenced here; recorded raw.
 *         With MMU off a cacheable VA must be converted before use:
 *         PA = VA + 0x400000000 (M2_A4:199-200; kCacheableView pongo.h:344).
 *         Conversion/dereference is left to offline analysis.)
 *   x8  = gEntryPoint; x1..x3 = 0                             (M2_A11:101-102)
 *   EL1 guaranteed on the successful Pongo path                (M2_A11:172)
 *   MMU/data-cache-enable cleared by _disable_mmu_el1
 *         (lowlevel.c:117-128, M2_A11:174); data cache cleaned over
 *         Pongo's RAM range + `ic iallu` at the jump
 *         (entry.c:303-308, jump_to_image.S:47-51, M2_A11:177-178).
 *         With SCTLR.M=C=0 our stores land directly in DRAM.
 *   Upload buffer (1 MiB alloc_contig, usbloader.c:94-99) is NOT freed
 *         on the bootr path: count cleared, data/size retained; only USB
 *         registers are torn down (main.c:40-48, M2_A11:182). Our image
 *         bytes — including this report — survive our own execution.
 *   Image size MUST fit the upload buffer (M2_A4:301-306). This probe is
 *         < 1 KiB; the linker ASSERTs the 1 MiB bound (pongo_probe.ld).
 *
 * --- Report layout (file offsets; single source of truth = the .equ
 * --- block below, cross-checked by tests/check_pongo_probe.py) ---
 *   +0x00  u64 magic    "MP1PRB01" (0x313042525031504D, ASCII-visible)
 *   +0x08  u32 version  1
 *   +0x0C  u32 report_size (bytes, = RPT_SIZE)
 *   +0x10  u64 x30 at entry (buffer physical base = our load address)
 *   +0x18  u64 x0  at entry (gBootArgs cacheable VA, raw)
 *   +0x20  u64 x8  at entry (gEntryPoint)
 *   +0x28  u64 CurrentEL (raw MRS; always recorded)
 *   +0x30  u64 SCTLR_EL1 (EL1 only, else 0)
 *   +0x38  u64 MIDR_EL1  (EL1 only, else 0)
 *   +0x40  u64 CNTPCT_EL0 sample 1 (EL1 only, else 0)
 *   +0x48  u64 CNTPCT_EL0 sample 2 (after a short delay loop, EL1 only)
 *   +0x50  u64 CNTFRQ_EL0 (EL1 only, else 0; supports later ticks->time)
 *   +0x58  u64 reserved_zero (always 0)
 *   Report block lives at file offset RPT_OFF (0x200) in .report;
 *   entry code lives at file offset 0 in .text.probe.
 *   EL-guarding pattern follows platform/apple/a8/m1_entry.S:60-81
 *   (MIDR/timer reads only at EL1; CurrentEL read is safe anywhere).
 *
 * --- Parking ---
 *   `msr daifset, #0xf` (ARCHITECTURE.md §4: mask interrupts; safe at
 *   EL>=1, skipped at EL0) then a self-branch spin (`b .`). A `wfe` park
 *   is deliberately NOT used: with an unknown HCR_EL2 it could trap;
 *   a branch-to-self cannot fault.
 *
 * --- Read-back (tiers; shell dies at the jump, so same-session shell
 * --- read-back is impossible — jump_to_image.S `ret` is terminal) ---
 *   Tier 0 (pre-bootr, shell alive): verify upload bytes with Pongo
 *         shell memory reads (md8/peek/physdump family, D2_FEASIBILITY:80-81)
 *         at the buffer PA. Buffer PA discovery: `nm` on the built Pongo
 *         Mach-O for loader_xfer_recv_data + runtime slide resolution
 *         (workaround noted at M2_A17:61; Pongo Mach-O lives on the Arch
 *         build machine, slide is a device-side unknown). Exact md8
 *         argument syntax is NOT in local evidence — confirm against the
 *         live shell; do not guess.
 *   Tier 1 (post-bootr): 05ac:4141 disappearance watch via `lsusb`
 *         (D2_RUNBOOK:81) — jump occurred (1-bit execution signal).
 *   Tier 2 (register values): recorded here for future retrieval
 *         (reboot-forensics UNVERIFIED and likely clobbered by allocator
 *         bzero, M2_A4:137-138; modload-module path needs the Apple
 *         toolchain + shell client, D2_REPORT:95-101). NOT claimed now.
 *
 * Non-goals: checkra1n -k entry (different convention: stage1 trampoline,
 * x27/x29, D2_FEASIBILITY:72-79); QEMU-virt behavior (ARCHITECTURE.md §8:
 * QEMU never evidences A8 behavior); MYOSKERN packaging (bootr takes RAW,
 * M2_A7:13 — no ELF parsing).
 *
 * Status: IMPLEMENTED offline. No device test performed; every runtime
 * property above remains UNVERIFIED until a physical run.
 */
    .section .text.probe, "ax"
    .align 2
    .global pongo_probe_entry
    .type pongo_probe_entry, %function

/* Report field offsets (single source of truth; checker-validated) */
    .equ RPT_MAGIC_OFF,  0x00
    .equ RPT_VER_OFF,    0x08
    .equ RPT_SIZE_OFF,   0x0C
    .equ RPT_X30_OFF,    0x10
    .equ RPT_X0_OFF,     0x18
    .equ RPT_X8_OFF,     0x20
    .equ RPT_EL_OFF,     0x28
    .equ RPT_SCTLR_OFF,  0x30
    .equ RPT_MIDR_OFF,   0x38
    .equ RPT_TVAL1_OFF,  0x40
    .equ RPT_TVAL2_OFF,  0x48
    .equ RPT_TFRQ_OFF,   0x50
    .equ RPT_ZERO_OFF,   0x58
    .equ RPT_SIZE,       0x60
    .equ RPT_VERSION,    1
    .equ RPT_OFF,        0x200

pongo_probe_entry:
    /* Save handoff registers first (x30/x0/x8 are our only copy). */
    mov     x9, x30                   /* x9  = image base (buffer PA) */
    mov     x10, x0                   /* x10 = gBootArgs VA (raw)      */
    mov     x11, x8                   /* x11 = gEntryPoint             */

    /* Report base (PC-relative; valid at any load address). */
    adr     x12, probe_report

    /* Magic + version + size (EL-safe: plain stores). */
    adr     x13, probe_magic
    ldr     x14, [x13]
    str     x14, [x12, #RPT_MAGIC_OFF]
    movz    x14, #RPT_VERSION
    str     w14, [x12, #RPT_VER_OFF]
    movz    x14, #RPT_SIZE
    str     w14, [x12, #RPT_SIZE_OFF]

    /* Handoff registers + CurrentEL (all EL-safe). */
    str     x9,  [x12, #RPT_X30_OFF]
    str     x10, [x12, #RPT_X0_OFF]
    str     x11, [x12, #RPT_X8_OFF]
    mrs     x14, CurrentEL
    str     x14, [x12, #RPT_EL_OFF]

    /* Below EL1 there is nothing safe left to read: park. */
    ubfx    x15, x14, #2, #2          /* x15 = EL field */
    cbz     x15, park

    /* Mask interrupts (safe at EL>=1; skipped at EL0 above). */
    msr     daifset, #0xf

    /* EL1-only registers; anything else stores zeros (m1_entry pattern). */
    cmp     x15, #1
    b.ne    1f
    mrs     x14, SCTLR_EL1
    str     x14, [x12, #RPT_SCTLR_OFF]
    mrs     x14, MIDR_EL1
    str     x14, [x12, #RPT_MIDR_OFF]
    mrs     x14, CNTPCT_EL0
    str     x14, [x12, #RPT_TVAL1_OFF]
    /* Short delay loop (regs only, no memory/stack) so sample 2 can differ. */
    movz    x15, #0x1000
2:  subs    x15, x15, #1
    b.ne    2b
    mrs     x14, CNTPCT_EL0
    str     x14, [x12, #RPT_TVAL2_OFF]
    mrs     x14, CNTFRQ_EL0
    str     x14, [x12, #RPT_TFRQ_OFF]
    b       3f
1:
    str     xzr, [x12, #RPT_SCTLR_OFF]
    str     xzr, [x12, #RPT_MIDR_OFF]
    str     xzr, [x12, #RPT_TVAL1_OFF]
    str     xzr, [x12, #RPT_TVAL2_OFF]
    str     xzr, [x12, #RPT_TFRQ_OFF]
3:
    str     xzr, [x12, #RPT_ZERO_OFF]

park:
    b       park                      /* self-branch park: cannot fault */

    .size pongo_probe_entry, .-pongo_probe_entry

    .align 3
probe_magic:
    .quad   0x313042525031504D        /* "MP1PRB01" */


/* Report block: file offset RPT_OFF (placed by pongo_probe.ld).
 *
 * The STATIC header (magic/version/size) is pre-initialized in the file so
 * the raw image itself carries MP1PRB01 at +0x200. That makes the uploaded
 * bytes verifiable BEFORE `bootr` (Tier-0 shell readback) and satisfies the
 * binary invariant in tests/check_pongo_probe.py. The entry code rewrites
 * these three fields at runtime with the same values; the DYNAMIC fields
 * (+0x10..) stay zero in the file because they are only known on device.
 * Only immediate constants are emitted here (no symbol addresses), so no
 * relocation is created. */
    .section .report, "aw"
    .global probe_report
    .type probe_report, %object
probe_report:
    .quad   0x313042525031504D        /* +0x00 u64 magic "MP1PRB01" */
    .long   RPT_VERSION               /* +0x08 u32 version (1)      */
    .long   RPT_SIZE                  /* +0x0C u32 report_size      */
    .skip   RPT_SIZE - 16             /* +0x10 dynamic fields: zero */
    .size probe_report, .-probe_report
