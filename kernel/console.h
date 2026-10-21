/*
 * kernel/console.h — Kernel console boundary.
 *
 * target: target-independent (no MMIO, no platform constants).
 *
 * Owns the contract by which kernel code emits console output. Platform code
 * supplies a backend (a struct boot_console plus its operations) and registers
 * it; the kernel core and diagnostics emit only through this interface.
 *
 * Lifecycle:
 *   - Before a backend is registered boot_console is NULL and every
 *     kconsole_* call is a safe no-op; kconsole_ready() returns 0.
 *   - A backend becomes ready as soon as it is registered. Platform backends
 *     initialize their hardware BEFORE registering (the QEMU PL011 driver
 *     configures the UART and returns a populated console object, which the
 *     boot path then registers). There is no separate post-registration
 *     initialization step and therefore no "registered but not initialized"
 *     state to represent.
 *   - Registering again replaces the active backend (last registration wins).
 *   - Registering a NULL backend, or a backend whose `ops` is NULL, leaves the
 *     kernel not ready: output is dropped rather than sent to any
 *     machine-specific address.
 *
 * The kernel owns boot_console_register (kernel/console.c); a platform must
 * never define it, so registration semantics cannot diverge per platform.
 */
#ifndef MYOS_KERNEL_CONSOLE_H
#define MYOS_KERNEL_CONSOLE_H

#include <stddef.h>
#include <stdint.h>

struct boot_console;

/*
 * Backend operations. A platform may leave an operation NULL; the matching
 * kconsole_* call then becomes a no-op for that operation only, so a partially
 * wired backend degrades safely instead of faulting.
 */
typedef struct {
    int (*putc)(struct boot_console *console, char c);
    int (*puts)(struct boot_console *console, const char *s);
    int (*write)(struct boot_console *console, const void *buf, size_t len);
    void (*flush)(struct boot_console *console);
    void *platform_data;
} boot_console_ops_t;

struct boot_console {
    const boot_console_ops_t *ops;
    void *platform_data;
};

/* Active backend, or NULL when no console is registered. */
extern struct boot_console *boot_console;

/* Publish a backend. Defined once by the kernel (kernel/console.c). */
void boot_console_register(struct boot_console *console);

/* Nonzero iff a usable backend (registered with ops) is available. */
static inline int kconsole_ready(void) {
    return boot_console != NULL && boot_console->ops != NULL;
}

/* Emit a NUL-terminated string, or nothing when unavailable or s is NULL. */
static inline void kconsole_puts(const char *s) {
    if (s != NULL && boot_console != NULL && boot_console->ops != NULL &&
        boot_console->ops->puts != NULL) {
        boot_console->ops->puts(boot_console, s);
    }
}

/* Emit one character, or nothing when unavailable. */
static inline void kconsole_putc(char c) {
    if (boot_console != NULL && boot_console->ops != NULL &&
        boot_console->ops->putc != NULL) {
        boot_console->ops->putc(boot_console, c);
    }
}

/* Emit len bytes, or nothing when unavailable, buf is NULL, or len is 0. */
static inline void kconsole_write(const void *buf, size_t len) {
    if (buf != NULL && len != 0 && boot_console != NULL &&
        boot_console->ops != NULL && boot_console->ops->write != NULL) {
        boot_console->ops->write(boot_console, buf, len);
    }
}

/* Flush buffered output, or nothing when unavailable. */
static inline void kconsole_flush(void) {
    if (boot_console != NULL && boot_console->ops != NULL &&
        boot_console->ops->flush != NULL) {
        boot_console->ops->flush(boot_console);
    }
}

#endif /* MYOS_KERNEL_CONSOLE_H */
