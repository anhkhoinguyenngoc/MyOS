/*
 * arch/aarch64/vectors_defs.h — Assembly-compatible constants for vector table
 *
 * This file contains only constants and macros needed by vectors.S.
 * No C typedefs, structs, or function declarations.
 */

#ifndef MYOS_ARCH_AARCH64_VECTORS_DEFS_H
#define MYOS_ARCH_AARCH64_VECTORS_DEFS_H

/* Exception class identifiers (matching vector table) */
#define EXC_CLASS_SYNC    0x00
#define EXC_CLASS_IRQ     0x01
#define EXC_CLASS_FIQ     0x02
#define EXC_CLASS_SERROR  0x03

/* Error codes needed by vectors.S (subset from boot_diag.h) */
/* Subsystem identifiers (high byte) */
#define DIAG_SUBSYS_GENERIC   0x000F0000

/* Failure types */
#define DIAG_TYPE_EXCEPTION   0x00000040

/* Predefined error codes */
#define ERR_GENERIC_EXCEPTION (DIAG_SUBSYS_GENERIC | DIAG_TYPE_EXCEPTION)

/* DIAG_STATE_MAGIC for validation if needed */
#define DIAG_STATE_MAGIC      0x44494147  /* "DIAG" */

#endif /* MYOS_ARCH_AARCH64_VECTORS_DEFS_H */