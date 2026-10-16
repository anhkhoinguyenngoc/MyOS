/*
 * MyOS kernel main for QEMU virt
 *
 * This runs after the early assembly entry (start.S) has set up
 * the stack, exception vectors, and early UART console.
 *
 * It runs through the remaining boot stages via the diagnostic framework
 * and emits MYOS_KERNEL_PASS when kernel_main is genuinely reached.
 *
 * Target: QEMU virt (AArch64)
 */

#include "arch/aarch64/boot_diag.h"

/* Forward declarations for platform-specific init */
int uart_pl011_init(struct boot_console *console);

/* Kernel main - called from start.S after early init */
void kernel_main(void)
{
    /* Print "Hello, World!" to UART early in boot */
    diag_puts("Hello, World!\n");

    /* KERNEL_MAIN stage - we are here! */
    boot_diag_begin(BOOT_STAGE_KERNEL_MAIN);

    /* Print success banner */
    diag_puts("\n");
    diag_puts("========================================\n");
    diag_puts("MyOS Kernel Runtime - QEMU virt\n");
    diag_puts("========================================\n");
    diag_puts("\n");
    diag_puts("Boot stages completed:\n");
    boot_diag_print_report();

    diag_puts("\n");
    diag_puts("MYOS_KERNEL_PASS\n");
    diag_puts("========================================\n");

    boot_diag_ok(BOOT_STAGE_KERNEL_MAIN);

    /* Halt the CPU */
    while (1) {
        asm volatile("wfe");
    }
}