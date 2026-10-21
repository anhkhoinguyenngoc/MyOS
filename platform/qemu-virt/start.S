/*
 * MyOS kernel entry point for QEMU virt (AArch64)
 *
 * This is the FIRST kernel code that runs when QEMU loads the ELF via -kernel.
 * It sets up the minimal execution environment:
 *   - Validates CurrentEL (must be EL1 or EL2)
 *   - Sets up stack pointer
 *   - Enables FP/SIMD (for NEON instructions in memcpy/memset)
 *   - Initializes early UART for diagnostics
 *   - Installs VBAR_EL1 exception vectors
 *   - Initializes boot diagnostic framework
 *   - Runs through boot stages via diagnostic framework
 *   - Calls kernel_main()
 *
 * Target: QEMU virt (AArch64) with PL011 UART at MYOS_QEMU_VIRT_PL011_BASE
 * CPU: cortex-a57 (or any AArch64 CPU supported by QEMU virt)
 *
 * NOT for iPhone 6 / A8 hardware - that uses the M1 payload path via PongoOS.
 */

/*
 * Build-time platform guard (M6). This file is QEMU virt boot code: it talks
 * to the QEMU PL011 at MYOS_QEMU_VIRT_PL011_BASE and assumes the QEMU virt load
 * address from platform/qemu-virt/kernel.ld. Refuse to assemble it for any
 * other platform so a future physical target cannot silently reuse QEMU
 * assumptions.
 */
#ifndef MYOS_PLATFORM_QEMU_VIRT
#error "platform/qemu-virt/start.S is QEMU virt boot code only; refusing non-QEMU build (see PLATFORM_INTERFACE.md)."
#endif

#include "platform/qemu-virt/qemu_virt_defs.h"

/* PL011 UART register offsets */
#define PL011_BASE    MYOS_QEMU_VIRT_PL011_BASE
#define UARTDR        0x00
#define UARTFR        0x18

    .section .text.boot, "ax"
    .global _start
    .type _start, %function

/* Forward declarations for C functions */
    .global kernel_main
    .global vbar_el1_install
    .global uart_pl011_init_early

_start:
    /* ========================================================================
     * 1. Capture initial CPU state
     * ======================================================================== */
    mrs     x0, CurrentEL
    lsr     x0, x0, #2              /* CurrentEL[3:2] = EL (0=EL0, 1=EL1, 2=EL2, 3=EL3) */
    /* We expect to enter at EL1 (QEMU virt default) or EL2 (with virtualization) */
    cmp     x0, #1
    b.eq    1f
    cmp     x0, #2
    b.eq    1f
    /* Unexpected EL - halt with error code in x0 */
    b       .

1:
    /* ========================================================================
     * 2. Set up stack pointer
     * ======================================================================== */
    /* Stack grows down from _stack_top (defined in linker script) */
    adr     x1, _stack_top
    mov     sp, x1

    /* ========================================================================
     * 3. Enable FP/SIMD (required for NEON instructions in memcpy/memset)
     * ======================================================================== */
    mrs     x0, CPACR_EL1
    orr     x0, x0, #(3 << 20)        /* CPACR_EL1.FPEN = 0b11 (enable FP/SIMD at EL1/EL0) */
    msr     CPACR_EL1, x0
    isb

    /* ========================================================================
     * 4. EARLY UART TEST - direct write to verify execution
     * ======================================================================== */
    adr     x2, early_msg
    bl      early_uart_puts

    /* ========================================================================
     * 5. Early UART initialization (before diagnostic framework)
     * ======================================================================== */
    bl      uart_pl011_init_early
    /* x0 = returned console pointer */
    cbz     x0, uart_null
    /* Register console immediately before any other calls */
    bl      boot_console_register
    adr     x2, after_uart_init_msg
    bl      early_uart_puts
    b       uart_init_done

uart_null:
    adr     x2, debug_console_null_msg
    bl      early_uart_puts
uart_init_done:

    /* ========================================================================
     * 6. Initialize diagnostic framework
     * ======================================================================== */
    bl      boot_diag_init
    adr     x2, after_diag_init_msg
    bl      early_uart_puts

    /* ========================================================================
     * 7. Initialize diagnostic framework
     * ======================================================================== */
    bl      boot_diag_init
    adr     x2, after_diag_init_msg
    bl      early_uart_puts

    /* ========================================================================
     * 10. Run through boot stages via diagnostic framework
     * ======================================================================== */
    /* BOOTSTRAP stage - entry, stack, vectors */
    mov     x0, #0                  /* BOOT_STAGE_BOOTSTRAP */
    bl      boot_diag_begin
    adr     x2, stage0_msg
    bl      early_uart_puts
    mov     x0, #0
    bl      boot_diag_ok
    adr     x2, stage0_ok_msg
    bl      early_uart_puts

    /* CPU_INIT stage - already done implicitly */
    mov     x0, #1                  /* BOOT_STAGE_CPU_INIT */
    bl      boot_diag_begin
    adr     x2, stage1_msg
    bl      early_uart_puts
    mov     x0, #1
    bl      boot_diag_ok
    adr     x2, stage1_ok_msg
    bl      early_uart_puts

    /* EXCEPTION_INIT stage - vectors installed */
    mov     x0, #4                  /* BOOT_STAGE_EXCEPTION_INIT */
    bl      boot_diag_begin
    adr     x2, stage4_msg
    bl      early_uart_puts
    mov     x0, #4
    bl      boot_diag_ok
    adr     x2, stage4_ok_msg
    bl      early_uart_puts

    /* UART_INIT stage - early UART already working */
    mov     x0, #5                  /* BOOT_STAGE_UART_INIT */
    bl      boot_diag_begin
    adr     x2, stage5_msg
    bl      early_uart_puts
    mov     x0, #5
    bl      boot_diag_ok
    adr     x2, stage5_ok_msg
    bl      early_uart_puts

    /* TIMER_INIT stage */
    mov     x0, #6                  /* BOOT_STAGE_TIMER_INIT */
    bl      boot_diag_begin
    adr     x2, stage6_msg
    bl      early_uart_puts
    mov     x0, #6
    bl      boot_diag_ok
    adr     x2, stage6_ok_msg
    bl      early_uart_puts

    /* DRIVER_INIT stage */
    mov     x0, #7                  /* BOOT_STAGE_DRIVER_INIT */
    bl      boot_diag_begin
    adr     x2, stage7_msg
    bl      early_uart_puts
    mov     x0, #7
    bl      boot_diag_ok
    adr     x2, stage7_ok_msg
    bl      early_uart_puts

    /* USB_INIT stage - not applicable for QEMU virt */
    mov     x0, #8                  /* BOOT_STAGE_USB_INIT */
    bl      boot_diag_begin
    adr     x2, stage8_msg
    bl      early_uart_puts
    mov     x0, #8
    bl      boot_diag_ok
    adr     x2, stage8_ok_msg
    bl      early_uart_puts

    /* KERNEL_INIT stage */
    mov     x0, #9                  /* BOOT_STAGE_KERNEL_INIT */
    bl      boot_diag_begin
    adr     x2, stage9_msg
    bl      early_uart_puts
    mov     x0, #9
    bl      boot_diag_ok
    adr     x2, stage9_ok_msg
    bl      early_uart_puts

    /* ========================================================================
     * 11. Call kernel_main (never returns)
     * ======================================================================== */
    mov     x0, #10                 /* BOOT_STAGE_KERNEL_MAIN */
    bl      boot_diag_begin
    adr     x2, stage10_msg
    bl      early_uart_puts
    bl      kernel_main

    /* Should never reach here */
    b       .

/* ========================================================================
 * Early UART output functions (direct, no framework)
 * ======================================================================== */
early_uart_puts:
    /* x2 = string pointer */
    mov     x0, #PL011_BASE
1:  ldrb    w1, [x2], #1
    cbz     w1, 2f
    /* Wait for TX FIFO not full */
3:  ldr     w3, [x0, #UARTFR]
    tbnz    w3, #5, 3b
    str     w1, [x0, #UARTDR]
    b       1b
2:  ret

/* ========================================================================
 * String constants
 * ======================================================================== */
    .section .rodata
early_msg:
    .asciz  "MyOS kernel entry\n"
after_uart_init_msg:
    .asciz  "After uart init\n"
after_diag_init_msg:
    .asciz  "After diag init\n"
debug_console_null_msg:
    .asciz  "DBG: console NULL\n"
after_vbar_msg:
    .asciz  "After vbar install\n"
stage0_msg:
    .ascii  "S0b\n"
    .align 3
stage0_ok_msg:
    .ascii  "S0o\n"
    .align 3
stage1_msg:
    .ascii  "S1b\n"
    .align 3
stage1_ok_msg:
    .ascii  "S1o\n"
    .align 3
stage4_msg:
    .ascii  "S4b\n"
    .align 3
stage4_ok_msg:
    .ascii  "S4o\n"
    .align 3
stage5_msg:
    .ascii  "S5b\n"
    .align 3
stage5_ok_msg:
    .ascii  "S5o\n"
    .align 3
stage6_msg:
    .ascii  "S6b\n"
    .align 3
stage6_ok_msg:
    .ascii  "S6o\n"
    .align 3
stage7_msg:
    .ascii  "S7b\n"
    .align 3
stage7_ok_msg:
    .ascii  "S7o\n"
    .align 3
stage8_msg:
    .ascii  "S8b\n"
    .align 3
stage8_ok_msg:
    .ascii  "S8o\n"
    .align 3
stage9_msg:
    .ascii  "S9b\n"
    .align 3
stage9_ok_msg:
    .ascii  "S9o\n"
    .align 3
stage10_msg:
    .ascii  "S10b\n"
    .align 3

    .section .bss
    .align 16
    .global _stack_bottom
_stack_bottom:
    .skip   4096                    /* 4KB stack */
    .global _stack_top
_stack_top: