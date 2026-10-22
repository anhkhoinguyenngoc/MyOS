/*
 * MyOS M1 entry point — first MyOS-owned instructions on the Apple A8.
 *
 * target: iphone6 (Apple A8 real hardware)   [NOT qemu-virt]
 * Freestanding AArch64. No libc, no OS, no MMU/caches/interrupt changes,
 * no peripheral access, no guessed register addresses.
 *
 * Delivery contract (pwned-DFU exec interface, gaster payload_handle_
 * checkm8_request.S — see M1_PLAN.md §3):
 *   invoked via `blr pc` from the DFU request hook,
 *   x0 = command block base (DFU image buffer, 0x180380000),
 *   x1 = host challenge constant,
 *   x2..x7 = 0,
 *   handler expects: return x0 = computed value,
 *   evidence slots written to [x0 + 0x10 .. x0 + 0x38].
 *   On return the handler stores DONE_MAGIC at [base] and our x0 at [base+8],
 *   and ships the block back to the host over the control-IN transfer.
 *
 * Slot layout written by this payload:
 *   +0x10  CurrentEL  (raw MRS value; 0x4/0x8/0xC => EL1/EL2/EL3)
 *   +0x18  MIDR_EL1   (only read when CurrentEL==EL1; else 0)
 *   +0x20  marker "MYOSM1OK"
 *   +0x28  challenge echo (x1 unchanged from entry)
 *   +0x30  CNTFRQ_EL0 (only when EL1; else 0)
 *   +0x38  CNTPCT_EL0 (only when EL1; else 0)
 *
 * Register discipline: only caller-saved scratch (x9-x17) is used inside the
 * call-free evidence window; sp is restored before return; x19/x20 are never
 * touched (the handler keeps live values in them). x0/x1 (command block base
 * and challenge) and x30 (the hook's return address) are preserved on the
 * stack across the boot_diag_* calls: each `bl` is a normal C call that may
 * clobber every caller-saved register (x0-x18) and unconditionally overwrites
 * x30, so the link register must be stashed before the first call and
 * restored before the final `ret` — otherwise the payload would return into
 * its own epilogue instead of back to the hook's `blr x8` (M7.35 finding V2;
 * the pre-diagnostics "no calls, x30 untouched" design was invalidated when
 * the boot_diag_* calls were added). The handler-provided stack is valid:
 * the DFU request hook entered us via `blr x8` (gaster's vendored hook)
 * after pushing two 16-byte frames (stp x29,x30; stp x19,x20), so sp is
 * 16-byte aligned at entry and the 32-byte frame below keeps that
 * alignment at every call site. Safe to run inside SecureROM's request
 * hook context.
 *
 * Provenance of constants: M1_PLAN.md §4 (gaster.c, commit 7fffffff38a1).
 * Nothing here invents hardware addresses.
 */
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function

    /* Forward declarations for C functions */
    .global boot_diag_init
    .global boot_diag_begin
    .global boot_diag_ok

myos_m1_entry:
    /* Save x0 (command block base), x1 (challenge) and x30 (the hook's
       return address set by `blr x8`). The boot_diag_* calls below are normal
       C functions: AAPCS lets them clobber every caller-saved register
       (x0-x18) and each `bl` overwrites x30, so all three values must live
       in the 32-byte frame until the epilogue. */
    mov     x9, x0                      /* x9 = command block base        */
    mov     x10, x1                     /* x10 = challenge constant       */
    stp     x9, x10, [sp, #-32]!        /* [sp]=base, [sp+8]=challenge    */
    str     x30, [sp, #16]              /* [sp+16]=hook return address    */

    /* Initialize diagnostic state (no-op if no console registered yet)    */
    bl      boot_diag_init

    /* Mark BOOTSTRAP stage begin */
    mov     x0, #0                      /* BOOT_STAGE_BOOTSTRAP = 0       */
    bl      boot_diag_begin

    /* Reload base/challenge (peek; sp unchanged). No call occurs until the
       final boot_diag_ok below, so x9-x17 are safe scratch in this window. */
    ldp     x9, x10, [sp]

    /* slot+0x10 = CurrentEL (architecturally safe at EL1/EL2/EL3)         */
    mrs     x11, CurrentEL
    str     x11, [x9, #0x10]
    ubfx    x12, x11, #2, #2            /* x12 = EL field                 */

    /* slot+0x18 = MIDR_EL1, read ONLY at EL1 (avoid EL2/EL3 trap risk)    */
    mov     x13, xzr
    cmp     x12, #1
    b.ne    1f
    mrs     x13, MIDR_EL1
1:  str     x13, [x9, #0x18]

    /* slot+0x20 = marker "MYOSM1OK" (little-endian bytes in memory)       */
    adr     x14, m1_marker
    ldr     x15, [x14]
    str     x15, [x9, #0x20]

    /* slot+0x28 = challenge echo (proof x1 arrived intact)                */
    str     x10, [x9, #0x28]

    /* slot+0x30/+0x38 = timer freq + counter, ONLY at EL1                  */
    mov     x16, xzr
    mov     x17, xzr
    cmp     x12, #1
    b.ne    2f
    mrs     x16, CNTFRQ_EL0
    mrs     x17, CNTPCT_EL0
2:  str     x16, [x9, #0x30]
    str     x17, [x9, #0x38]

    /* Mark BOOTSTRAP stage OK. This call may clobber x9/x10/x30; the
       challenge and the hook return address are read back from the stack. */
    mov     x0, #0                      /* BOOT_STAGE_BOOTSTRAP = 0       */
    bl      boot_diag_ok

    ldr     x10, [sp, #8]               /* challenge (survives the calls) */
    ldr     x30, [sp, #16]              /* hook return address             */
    add     sp, sp, #32                 /* restore sp                     */

    /* return value: deterministic function of the input only this code    */
    /* could have computed: x0 = ~x1                                       */
    mvn     x0, x10
    ret

    .align 3
m1_marker:
    .ascii "MYOSM1OK"
    .size myos_m1_entry, .-myos_m1_entry
