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

#include "kernel/boot_diag.h"
#include "kernel/console.h"
#include "platform/platform.h"

/* Kernel main - called from start.S after early init */
void kernel_main(void)
{
    const myos_platform_descriptor_t *platform = myos_platform_current();

    /*
     * Boot contract: start.S registers a console backend before calling here,
     * so the kernel must be console-ready. If it is not, the success marker
     * could never be observed, so fail closed with a recorded UART error
     * instead of running on with all output silently dropped.
     */
    if (!kconsole_ready()) {
        boot_diag_fail(BOOT_STAGE_UART_INIT, ERR_UART_INIT_FAILED);
    }

    /* Print "Hello, World!" to UART early in boot */
    kconsole_puts("Hello, World!\n");

    /*
     * State which platform this kernel was built for. This is the visible
     * result of the M6 platform boundary: the boot path selects a descriptor
     * instead of assuming QEMU virt everywhere. It says nothing about the
     * iPhone 6, which has no implemented platform here.
     */
    kconsole_puts("Platform: ");
    kconsole_puts(platform->name);
    kconsole_puts(" [");
    kconsole_puts(myos_platform_status_name(platform->status));
    kconsole_puts("]\n");

    /* KERNEL_MAIN stage - we are here! */
    boot_diag_begin(BOOT_STAGE_KERNEL_MAIN);

    /* Print success banner */
    kconsole_puts("\n");
    kconsole_puts("========================================\n");
    kconsole_puts("MyOS Kernel Runtime - QEMU virt\n");
    kconsole_puts("========================================\n");
    kconsole_puts("\n");
    kconsole_puts("Boot stages completed:\n");
    boot_diag_print_report();

    kconsole_puts("\n");
    kconsole_puts("MYOS_KERNEL_PASS\n");
    kconsole_puts("========================================\n");

    boot_diag_ok(BOOT_STAGE_KERNEL_MAIN);

    /* Halt the CPU */
    while (1) {
        asm volatile("wfe");
    }
}