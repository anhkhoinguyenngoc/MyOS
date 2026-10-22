/*
 * arch/aarch64/vectors.S — AArch64 Exception Vector Table
 *
 * target: architecture-generic (no machine-specific address). Linked into the
 * qemu-virt kernel; contains no QEMU, Apple, or other platform constant.
 *
 * Provides the VBAR_EL1 vector table with handlers that capture
 * exception context and invoke the diagnostic framework.
 *
 * Each handler preserves registers, captures ESR_EL1/FAR_EL1/ELR_EL1/SPSR_EL1,
 * then calls boot_diag_exception_handler (which never returns).
 *
 * This is freestanding AArch64. No libc, no OS.
 */

#include "vectors_defs.h"

    .section .text.vectors, "ax"
    .align 11                       /* 2KB alignment for VBAR_EL1 */

/* ========================================================================
 * Vector Table Layout (AArch64 ARM DDI 0487C.a, D1.10.4)
 *
 * 16 entries × 128 bytes = 2048 bytes (2KB)
 * Each entry: 128 bytes of handler code space
 *
 * Offsets from VBAR_EL1:
 *   0x000-0x07F: Synchronous (EL1t, EL1h, EL0_64, EL0_32)
 *   0x080-0x0FF: IRQ
 *   0x100-0x17F: FIQ
 *   0x180-0x1FF: SError
 * ======================================================================== */

.global vbar_el1_table
vbar_el1_table:

/* ----- Synchronous exceptions (current EL = EL1, SP = SP_EL0) ----- */
    .align 7
vector_sync_el1t:
    b       sync_exception_handler

    .align 7
vector_sync_el1h:
    b       sync_exception_handler

    .align 7
vector_sync_el0_64:
    b       sync_exception_handler

    .align 7
vector_sync_el0_32:
    b       sync_exception_handler

/* ----- IRQ (current EL = EL1, SP = SP_EL0) ----- */
    .align 7
vector_irq_el1t:
    b       irq_exception_handler

    .align 7
vector_irq_el1h:
    b       irq_exception_handler

    .align 7
vector_irq_el0_64:
    b       irq_exception_handler

    .align 7
vector_irq_el0_32:
    b       irq_exception_handler

/* ----- FIQ (current EL = EL1, SP = SP_EL0) ----- */
    .align 7
vector_fiq_el1t:
    b       fiq_exception_handler

    .align 7
vector_fiq_el1h:
    b       fiq_exception_handler

    .align 7
vector_fiq_el0_64:
    b       fiq_exception_handler

    .align 7
vector_fiq_el0_32:
    b       fiq_exception_handler

/* ----- SError (current EL = EL1, SP = SP_EL0) ----- */
    .align 7
vector_serror_el1t:
    b       serror_exception_handler

    .align 7
vector_serror_el1h:
    b       serror_exception_handler

    .align 7
vector_serror_el0_64:
    b       serror_exception_handler

    .align 7
vector_serror_el0_32:
    b       serror_exception_handler

/* ========================================================================
 * Common exception entry: save context and call C handler
 *
 * This macro saves the minimal register set needed for diagnostics
 * and calls boot_diag_exception_handler.
 *
 * Input: \class_id = EXC_CLASS_SYNC/IRQ/FIQ/SERROR (macro argument).
 * The base_error literal (ERR_GENERIC_EXCEPTION) is loaded from the shared
 * literal pool inside the macro.
 *
 * This macro will:
 *   - Save caller-saved registers
 *   - Capture ESR_EL1/FAR_EL1/ELR_EL1/SPSR_EL1
 *   - Load current boot stage from diag_state
 *   - Marshal the report arguments and call boot_diag_exception_handler
 *     (never returns)
 *
 * Argument marshalling order (M7.35 finding V1): boot_diag_get_current_stage
 * is a C function that returns the stage in x0 and may clobber every
 * caller-saved register (AAPCS). It therefore runs AFTER the register save
 * and BEFORE x0/x1/x2/x3 are set, so base_error/exc_class reach
 * boot_diag_exception_handler unclobbered.
 *
 * Note: This runs at EL1 with SP_EL0. We must preserve x0-x18, x30.
 * ======================================================================== */

.macro EXCEPTION_ENTRY_COMMON class_id
    /* Save caller-saved registers that C code might clobber */
    stp     x0, x1, [sp, #-16]!
    stp     x2, x3, [sp, #-16]!
    stp     x4, x5, [sp, #-16]!
    stp     x6, x7, [sp, #-16]!
    stp     x8, x9, [sp, #-16]!
    stp     x10, x11, [sp, #-16]!
    stp     x12, x13, [sp, #-16]!
    stp     x14, x15, [sp, #-16]!
    stp     x16, x17, [sp, #-16]!
    stp     x18, x30, [sp, #-16]!

    /* Capture exception syndrome registers */
    mrs     x4, ESR_EL1
    mrs     x5, FAR_EL1
    mrs     x6, ELR_EL1
    mrs     x7, SPSR_EL1

    /* Prepare exception_context_t on stack.
       The struct starts with ESR/FAR, so store those last/outermost in the
       two contiguous (lower-address) slots.  The previous ESR,FAR then
       ELR,SPSR order left C labels reading the wrong words. */
    stp     x6, x7, [sp, #-16]!     /* elr_el1, spsr_el1 (upper slots) */
    stp     x4, x5, [sp, #-16]!     /* esr_el1, far_el1 (base slots) */

    /* Load boot stage from diagnostic state. Runs before the report
       arguments are marshalled: its return (x0 = stage) and any AAPCS
       caller-saved clobber must not touch x0-x3 below. */
    bl      boot_diag_get_current_stage
    mov     w4, w0                      /* stage in w4 */

    /* Marshal the report arguments AFTER the last call that can clobber
       them: x0 = base_error (shared literal, via x0 only so no other live
       register is touched), x1 = exc_class (macro argument), x2 = stage,
       x3 = pointer to the exception_context_t pushed above. */
    adrp    x0, err_generic_exception
    ldr     x0, [x0, #:lo12:err_generic_exception]
    mov     x1, \class_id               /* exc_class */
    mov     x2, x4                      /* stage */
    mov     x3, sp                      /* exception_context_t at current sp */

    /* Call exception handler (never returns) */
    bl      boot_diag_exception_handler

    /* Should never reach here */
    b       .
.endm

/* ========================================================================
 * Exception handlers
 *
 * Each handler only supplies its exception class; the shared base_error
 * literal is loaded inside EXCEPTION_ENTRY_COMMON after the stage getter
 * call (M7.35 finding V1 — the old pre-macro `adrp x4` preamble also
 * clobbered x4 before it was saved).
 * ======================================================================== */

/* Shared error code literal pool */
    .section .rodata.vectors
    .align 3
err_generic_exception:
    .quad   ERR_GENERIC_EXCEPTION
    .section .text.vectors, "ax"

sync_exception_handler:
    EXCEPTION_ENTRY_COMMON EXC_CLASS_SYNC

irq_exception_handler:
    EXCEPTION_ENTRY_COMMON EXC_CLASS_IRQ

fiq_exception_handler:
    EXCEPTION_ENTRY_COMMON EXC_CLASS_FIQ

serror_exception_handler:
    EXCEPTION_ENTRY_COMMON EXC_CLASS_SERROR

/* ========================================================================
 * VBAR setup helper
 * ======================================================================== */

.global vbar_el1_install
vbar_el1_install:
    adr     x0, vbar_el1_table
    msr     VBAR_EL1, x0
    isb
    ret

.size vbar_el1_table, .-vbar_el1_table