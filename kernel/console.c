/*
 * kernel/console.c — Kernel console boundary state.
 *
 * target: target-independent (no MMIO, no platform constants).
 *
 * Owns the active console backend and the registration contract. Platform code
 * provides a backend and calls boot_console_register; this file never learns a
 * machine-specific address.
 */
#include "console.h"

struct boot_console *boot_console = NULL;

void boot_console_register(struct boot_console *console) {
    boot_console = console;
}
