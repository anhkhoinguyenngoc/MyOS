/*
 * tests/console_lifecycle_main.c — host-side lifecycle harness for the
 * kernel console boundary (kernel/console.c + kernel/console.h).
 *
 * Links the real kernel console implementation against a fake backend that
 * records bytes in memory. No QEMU MMIO, no hardware. Asserts observable
 * behavior of registration, readiness, dispatch, null/zero safety, and
 * repeated registration.
 */
#include <stdio.h>
#include <string.h>
#include "console.h"

static char rec[256];
static size_t rec_len;
static int flush_count;

static int fake_putc(struct boot_console *c, char ch) {
    (void)c;
    if (rec_len < sizeof(rec) - 1) {
        rec[rec_len++] = ch;
    }
    rec[rec_len] = '\0';
    return 0;
}

static int fake_puts(struct boot_console *c, const char *s) {
    (void)c;
    while (s != NULL && *s != '\0') {
        fake_putc(c, *s++);
    }
    return 0;
}

static int fake_write(struct boot_console *c, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    (void)c;
    if (buf == NULL || len == 0) {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        fake_putc(c, p[i]);
    }
    return (int)len;
}

static void fake_flush(struct boot_console *c) {
    (void)c;
    flush_count++;
}

static const boot_console_ops_t fake_ops = {
    .putc = fake_putc,
    .puts = fake_puts,
    .write = fake_write,
    .flush = fake_flush,
    .platform_data = NULL,
};

static struct boot_console fake_console = {
    .ops = &fake_ops,
    .platform_data = NULL,
};

static int failures;

static void expect(int cond, const char *what) {
    if (cond) {
        printf("PASS %s\n", what);
    } else {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static void reset_rec(void) {
    rec_len = 0;
    rec[0] = '\0';
}

int main(void) {
    /* Before registration: not ready, output is safely dropped. */
    reset_rec();
    flush_count = 0;
    expect(kconsole_ready() == 0, "not ready before registration");
    kconsole_puts("dropped");
    kconsole_putc('x');
    kconsole_write("dropped", 7);
    kconsole_flush();
    expect(rec_len == 0, "output before registration is dropped");
    expect(flush_count == 0, "flush before registration is not dispatched");

    /* Register: becomes ready, output dispatched. */
    boot_console_register(&fake_console);
    expect(kconsole_ready() != 0, "ready after registration");
    reset_rec();
    kconsole_puts("hello\n");
    expect(strcmp(rec, "hello\n") == 0, "puts dispatches to backend");

    reset_rec();
    kconsole_putc('A');
    expect(strcmp(rec, "A") == 0, "putc dispatches to backend");

    reset_rec();
    kconsole_write("abc", 3);
    expect(strcmp(rec, "abc") == 0, "write dispatches to backend");

    flush_count = 0;
    kconsole_flush();
    expect(flush_count == 1, "flush dispatches to backend");

    /* Null / zero-length safety after registration. */
    reset_rec();
    kconsole_puts(NULL);
    kconsole_write(NULL, 0);
    kconsole_write("abc", 0);
    expect(rec_len == 0, "null and zero-length writes emit nothing");

    /* Repeated registration: last backend wins. */
    static struct boot_console second = {
        .ops = &fake_ops,
        .platform_data = NULL,
    };
    boot_console_register(&second);
    expect(boot_console == &second, "repeated registration replaces backend");
    reset_rec();
    kconsole_puts("z");
    expect(strcmp(rec, "z") == 0, "output uses the latest backend");

    /* Registering a backend with no ops is not ready. */
    static struct boot_console noops = {
        .ops = NULL,
        .platform_data = NULL,
    };
    boot_console_register(&noops);
    expect(kconsole_ready() == 0, "backend without ops is not ready");
    reset_rec();
    kconsole_puts("dropped");
    expect(rec_len == 0, "no-ops backend emits nothing");

    /* Backend with partial ops: per-op dispatch. */
    static const boot_console_ops_t partial_ops = {
        .putc = NULL,
        .puts = fake_puts,
        .write = fake_write,
        .flush = NULL,
        .platform_data = NULL,
    };
    static struct boot_console partial = {
        .ops = &partial_ops,
        .platform_data = NULL,
    };
    boot_console_register(&partial);
    expect(kconsole_ready() != 0, "backend with partial ops is ready");
    reset_rec();
    kconsole_putc('X');
    kconsole_flush();
    expect(rec_len == 0, "unwired op is safely no-op");
    reset_rec();
    kconsole_puts("y");
    expect(strcmp(rec, "y") == 0, "wired ops remain functional");

    if (failures) {
        printf("CONSOLE LIFECYCLE: FAIL (%d)\n", failures);
        return 1;
    }
    printf("CONSOLE LIFECYCLE: PASS\n");
    return 0;
}