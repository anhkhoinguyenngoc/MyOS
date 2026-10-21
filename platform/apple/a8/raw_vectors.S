/*
 * MyOS M7.13 — Apple A8 raw-entry EL1 exception vector table + capture
 * record (early exception capture; park-only, no recovery).
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW
 *         [NOT qemu-virt; NOT SecureROM-DFU exec]
 *
 * Architecture basis (verified from LOCAL sources during M7.13, not assumed):
 *   - Slot geometry: arch/aarch64/vectors.S (this repo) cites ARM DDI 0487C.a
 *     D1.10.4 — 16 slots x 128 bytes = 2 KiB table, VBAR alignment 2 KiB.
 *     pongoos-src/src/kernel/int.S (the table PongoOS installs on real A8
 *     hardware) corroborates: `.align 12` table head, `.balign 128` per slot,
 *     group order Current-EL-SP0 / Current-EL-SPx / Lower-EL-A64 /
 *     Lower-EL-A32, each group Synchronous -> IRQ -> FIQ -> SError, with
 *     `b .` fail-closed parks for the unsupported AArch32 group.
 *   - Expected slot: the inspected bootr handoff is EL1 with SPSel=0
 *     (M7.10 source trace), so a synchronous exception from the parked image
 *     selects slot 0 (Current EL, SP0, Synchronous). Correctness does NOT
 *     depend on that assumption: all 16 slots capture and carry their own
 *     slot classification, so a different dispatch (e.g. SPx group) is
 *     recorded honestly instead of mislabeled.
 *   - VBAR installation needs an instruction barrier: pongoos-src
 *     lowlevel.c `_set_vbar_el1` is `msr vbar_el1, x0` / `isb`, and
 *     arch/aarch64/vectors.S `vbar_el1_install` does the same — raw_entry.S
 *     installs our table with adr / msr / isb in that order.
 *   - Register semantics: ESR_EL1 carries the exception syndrome for every
 *     class; ELR_EL1 the interrupted PC; SPSR_EL1 the interrupted PSTATE;
 *     FAR_EL1 the fault address and it is only architecturally meaningful
 *     for fault-class syndrome values (data/instruction aborts) — for IRQ/
 *     FIQ/SError and trap syndrome classes it is not a fault address. The
 *     record stores it unconditionally; interpretation is ESR-dependent
 *     (documented contract, same caveat as this repo's boot_diag capture).
 *   - Stack at entry: PongoOS int.S slot code toggles spsel explicitly and
 *     so preserves the interrupted bank; at the expected EL1/SPSel=0 state
 *     the handler would run on SP_EL0 (the image-owned stack). The handler
 *     below uses NO stack memory whatsoever and re-points sp before any
 *     store could be stack-relative, so it cannot depend on the inherited
 *     PongoOS stack, on SP_EL1, or on unverified bank-selection behavior.
 *
 * Handler contract:
 *   - every slot: `movz x16, #<slot>` (self-classification, the only
 *     interrupted-state register value reported) then `b myos_a8_exc_common`;
 *   - common: read ESR/ELR/FAR/SPSR first (nothing intervenes between
 *     exception dispatch and these reads), sample sp, re-point sp at
 *     __stack_top, store the fixed-size record, `dsb sy`, write the
 *     completion marker LAST, branch-to-self park forever;
 *   - no ERET exists anywhere in the image: no path returns to the
 *     interrupted context; no `bl`: nothing calls out; no wfe/wfi: the park
 *     is a plain deterministic instruction loop;
 *   - registers are working registers only and are never restored (the
 *     context is never resumed); x16 is consumed by its first store before
 *     the marker value reuses it.
 *
 * Runtime status: IMPLEMENTED offline. VBAR_EL1 installation, vector
 * dispatch and record capture have NOT executed on hardware — every runtime
 * property stays UNVERIFIED until a physical run.
 */

    .equ EXC_SIZE, 0x48
    .equ EXC_VERSION, 1
    .equ EXC_OFF_SLOT, 0x10
    .equ EXC_OFF_ESR, 0x18
    .equ EXC_OFF_ELR, 0x20
    .equ EXC_OFF_FAR, 0x28
    .equ EXC_OFF_SPSR, 0x30
    .equ EXC_OFF_SP, 0x38
    .equ EXC_OFF_VALID, 0x40
    .equ EXC_VALID_MARKER, 1

/*
 * Vector table: 16 slots x 128 bytes, 2 KiB aligned (VBAR_EL1 bits[10:0]
 * are RES0). Each stub sits at its slot base; the remainder of every slot
 * is zero padding (unreachable: the stub branch is unconditional). The
 * table follows the stack in the image, so downward stack growth can never
 * reach it or the record above it.
 */
    .section .vectors, "ax"
    .balign 0x800
    .global __vectors
    .type __vectors, %object

    .macro VEC_SLOT idx
    .balign 0x80
vec_slot_\idx:
    movz    x16, #\idx
    b       myos_a8_exc_common
    .fill   0x80 - (. - vec_slot_\idx), 1, 0
    .endm

__vectors:
    VEC_SLOT 0                  /* Current EL, SP0, Synchronous   */
    VEC_SLOT 1                  /* Current EL, SP0, IRQ           */
    VEC_SLOT 2                  /* Current EL, SP0, FIQ           */
    VEC_SLOT 3                  /* Current EL, SP0, SError        */
    VEC_SLOT 4                  /* Current EL, SPx, Synchronous   */
    VEC_SLOT 5                  /* Current EL, SPx, IRQ           */
    VEC_SLOT 6                  /* Current EL, SPx, FIQ           */
    VEC_SLOT 7                  /* Current EL, SPx, SError        */
    VEC_SLOT 8                  /* Lower EL, AArch64, Synchronous */
    VEC_SLOT 9                  /* Lower EL, AArch64, IRQ         */
    VEC_SLOT 10                 /* Lower EL, AArch64, FIQ         */
    VEC_SLOT 11                 /* Lower EL, AArch64, SError      */
    VEC_SLOT 12                 /* Lower EL, AArch32, Synchronous */
    VEC_SLOT 13                 /* Lower EL, AArch32, IRQ         */
    VEC_SLOT 14                 /* Lower EL, AArch32, FIQ         */
    VEC_SLOT 15                 /* Lower EL, AArch32, SError      */
    .global __vectors_end
__vectors_end:
    .size __vectors, .-__vectors

/*
 * Common capture handler. Straight-line: system registers first (they are
 * read before any instruction that could perturb reporting), then sp
 * re-pointed into the image-owned stack, then the record stores, one
 * ordering barrier, then the completion marker, then the park. No stack
 * access, no calls, no exception return.
 */
    .section .text.exc, "ax"
    .align 2
    .global myos_a8_exc_common
    .type myos_a8_exc_common, %function
myos_a8_exc_common:
    mrs     x9, esr_el1
    mrs     x10, elr_el1
    mrs     x11, far_el1
    mrs     x12, spsr_el1
    mov     x13, sp
    adr     x14, __stack_top
    mov     sp, x14
    adr     x15, myos_a8_exc_record
    stur    x16, [x15, #EXC_OFF_SLOT]
    stur    x9, [x15, #EXC_OFF_ESR]
    stur    x10, [x15, #EXC_OFF_ELR]
    stur    x11, [x15, #EXC_OFF_FAR]
    stur    x12, [x15, #EXC_OFF_SPSR]
    stur    x13, [x15, #EXC_OFF_SP]
    dsb     sy
    movz    x16, #EXC_VALID_MARKER
    stur    x16, [x15, #EXC_OFF_VALID]
myos_a8_exc_park:
    b       myos_a8_exc_park
    .size myos_a8_exc_common, .-myos_a8_exc_common

/*
 * Exception record: file-backed, deterministic initial state (magic,
 * version, size present; every captured field and the completion marker
 * zero in the uploaded bytes). The handler writes captured fields, issues
 * DSB SY, and writes EXC_OFF_VALID last, so marker != 0 means the record
 * completed. Fixed size EXC_SIZE; outside the stack region; single record:
 * it holds the MOST RECENT exception entry (no history, no reset survival
 * claims). FAR at EXC_OFF_FAR is ESR-dependent (see header).
 */
    .section .excrecord, "aw", @progbits
    .balign 8
    .global myos_a8_exc_record
    .type myos_a8_exc_record, %object
myos_a8_exc_record:
    .ascii "MYOSA8E1"
    .word EXC_VERSION
    .word EXC_SIZE
    .quad 0                     /* EXC_OFF_SLOT   +0x10 slot index 0..15 */
    .quad 0                     /* EXC_OFF_ESR    +0x18 ESR_EL1         */
    .quad 0                     /* EXC_OFF_ELR    +0x20 ELR_EL1         */
    .quad 0                     /* EXC_OFF_FAR    +0x28 FAR_EL1 (ESR-  */
                                /* dependent interpretation)            */
    .quad 0                     /* EXC_OFF_SPSR   +0x30 SPSR_EL1        */
    .quad 0                     /* EXC_OFF_SP     +0x38 sp at handler   */
                                /* entry, before re-pointing            */
    .quad 0                     /* EXC_OFF_VALID  +0x40 marker, last    */
    .global myos_a8_exc_record_end
myos_a8_exc_record_end:
    .size myos_a8_exc_record, .-myos_a8_exc_record
