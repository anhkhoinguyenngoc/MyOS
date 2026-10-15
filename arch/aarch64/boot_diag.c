/*
 * arch/aarch64/boot_diag.c — Boot Diagnostic Framework Implementation
 *
 * target: iphone6 (Apple A8 real hardware) AND qemu-virt (tests)
 *
 * Implementation of the boot diagnostic framework. Freestanding,
 * allocation-free, deterministic.
 */

#include "boot_diag.h"

/* ========================================================================
 * GLOBAL STATE
 * ======================================================================== */

static boot_diag_state_t diag_state = {
    .magic = DIAG_STATE_MAGIC,
    .version = DIAG_STATE_VERSION,
    .current_stage = BOOT_STAGE_UNKNOWN,
    .last_ok_stage = BOOT_STAGE_UNKNOWN,
    .last_error = 0,
    .failure_flag = 0,
    .timestamp = 0,
    .esr_el1 = 0,
    .far_el1 = 0,
    .elr_el1 = 0,
    .spsr_el1 = 0,
    .elr_el2 = 0,
    .spsr_el2 = 0,
    .event_head = 0,
    .event_count = 0,
    /* events array zero-initialized by default */
};

/* Console instance (set by platform code) */
struct boot_console *boot_console = NULL;

/* Framebuffer renderer (optional, set by platform when available) */
const boot_fb_renderer_t *boot_fb_renderer = NULL;

/* Forward declaration for assembly implementation */
extern void boot_console_register(struct boot_console *console);

/* Register a framebuffer renderer (called by platform display driver) */
void boot_fb_register_renderer(const boot_fb_renderer_t *renderer) {
    boot_fb_renderer = renderer;
}

/* Stage names for console output */
const char * const boot_stage_names[] = {
    [BOOT_STAGE_BOOTSTRAP]      = "BOOTSTRAP",
    [BOOT_STAGE_CPU_INIT]       = "CPU_INIT",
    [BOOT_STAGE_MEMORY_INIT]    = "MEMORY_INIT",
    [BOOT_STAGE_MMU_INIT]       = "MMU_INIT",
    [BOOT_STAGE_EXCEPTION_INIT] = "EXCEPTION_INIT",
    [BOOT_STAGE_UART_INIT]      = "UART_INIT",
    [BOOT_STAGE_TIMER_INIT]     = "TIMER_INIT",
    [BOOT_STAGE_DRIVER_INIT]    = "DRIVER_INIT",
    [BOOT_STAGE_USB_INIT]       = "USB_INIT",
    [BOOT_STAGE_KERNEL_INIT]    = "KERNEL_INIT",
    [BOOT_STAGE_KERNEL_MAIN]    = "KERNEL_MAIN",
    [BOOT_STAGE_UNKNOWN]        = "UNKNOWN",
};

/* ========================================================================
 * INTERNAL UTILITIES
 * ======================================================================== */

static inline void diag_write_str(const char *s) {
    if (boot_console && boot_console->ops && boot_console->ops->puts) {
        boot_console->ops->puts(boot_console, s);
    }
}

static inline void diag_write_char(char c) {
    if (boot_console && boot_console->ops && boot_console->ops->putc) {
        boot_console->ops->putc(boot_console, c);
    }
}

static inline void diag_flush(void) {
    if (boot_console && boot_console->ops && boot_console->ops->flush) {
        boot_console->ops->flush(boot_console);
    }
}

static inline void diag_write_buf(const void *buf, size_t len) {
    if (boot_console && boot_console->ops && boot_console->ops->write) {
        boot_console->ops->write(boot_console, buf, len);
    }
}

/* Get current CNTPCT_EL0 if available (EL1 only) */
static uint64_t diag_get_timestamp(void) {
    /* Temporarily disable timestamp to avoid potential hang on CNTPCT_EL0 read */
    return 0;
}

/* Record a diagnostic event to the ring buffer */
static void diag_record_event(diag_event_type_t type, boot_stage_t stage, boot_error_t error) {
    uint32_t idx = diag_state.event_head;
    diag_state.events[idx].type = (uint32_t)type;
    diag_state.events[idx].stage = stage;
    diag_state.events[idx].error = error;
    diag_state.events[idx].timestamp = diag_get_timestamp();

    diag_state.event_head = (idx + 1) % DIAG_MAX_EVENTS;
    if (diag_state.event_count < DIAG_MAX_EVENTS) {
        diag_state.event_count++;
    }
}

/* ========================================================================
 * PUBLIC API
 * ======================================================================== */

void boot_diag_init(void) {
    /* Zero out state except magic/version */
    diag_state.current_stage = BOOT_STAGE_UNKNOWN;
    diag_state.last_ok_stage = BOOT_STAGE_UNKNOWN;
    diag_state.last_error = 0;
    diag_state.failure_flag = 0;
    diag_state.timestamp = 0;
    diag_state.esr_el1 = 0;
    diag_state.far_el1 = 0;
    diag_state.elr_el1 = 0;
    diag_state.spsr_el1 = 0;
    diag_state.elr_el2 = 0;
    diag_state.spsr_el2 = 0;
    diag_state.event_head = 0;
    diag_state.event_count = 0;
}

/* Forward declaration for fault injection helper (only compiled with
   injector support; kept aligned with the #ifdef guarded definition). */
#ifdef MYOS_DIAG_FAULT_INJECTION
static int diag_check_injected_failure(boot_stage_t stage, boot_error_t *out_error);
#endif

int boot_diag_begin(boot_stage_t stage) {
#ifdef MYOS_DIAG_FAULT_INJECTION
    boot_error_t injected_error;
    if (diag_check_injected_failure(stage, &injected_error)) {
        boot_diag_fail(stage, injected_error);
    }
#endif

    if (stage >= BOOT_STAGE_UNKNOWN) {
        return -1;
    }

    diag_state.current_stage = stage;
    diag_state.timestamp = diag_get_timestamp();
    diag_record_event(DIAG_EVENT_STAGE_BEGIN, stage, 0);

    /* Print stage begin */
    if (boot_console) {
        diag_write_str("[....] ");
        diag_write_str(boot_stage_names[stage]);
        diag_write_str(" - BEGIN\n");
        diag_flush();
    }

    return 0;
}

void boot_diag_ok(boot_stage_t stage) {
    if (stage >= BOOT_STAGE_UNKNOWN) {
        return;
    }

    /* A stage can only be marked successful when it is the one currently
     * being initialized. Allowing unrelated/out-of-order acks would let a
     * later stage claim an earlier failure state was clean. */
    if (diag_state.current_stage != stage) {
        if (boot_console) {
            diag_write_str("WARN: boot_diag_ok for ");
            diag_write_str(boot_stage_names[stage]);
            diag_write_str(" is ignored (current stage mismatch)\n");
            diag_flush();
        }
        return;
    }

    diag_state.last_ok_stage = stage;
    diag_state.current_stage = BOOT_STAGE_UNKNOWN;
    diag_state.timestamp = diag_get_timestamp();
    diag_record_event(DIAG_EVENT_STAGE_OK, stage, 0);

    /* Print stage OK */
    if (boot_console) {
        diag_write_str("[  OK ] ");
        diag_write_str(boot_stage_names[stage]);
        diag_write_str("\n");
        diag_flush();
    }

    /* Update framebuffer if available */
    if (boot_fb_renderer && boot_fb_renderer->render_report) {
        boot_fb_renderer->render_report(&diag_state);
    }
}

__attribute__((noreturn)) void boot_diag_fail(boot_stage_t stage, boot_error_t error) {
    /* Only statically defined stages may reach diagnostic lookup tables.
     * Out-of-table stages must never drive array indexing. */
    boot_stage_t safe_stage;
    if (stage <= BOOT_STAGE_KERNEL_MAIN || stage == BOOT_STAGE_UNKNOWN) {
        safe_stage = stage;
    } else {
        safe_stage = BOOT_STAGE_UNKNOWN;
    }

    diag_state.current_stage = safe_stage;
    diag_state.last_error = error;
    diag_state.failure_flag = 1;
    diag_state.timestamp = diag_get_timestamp();
    diag_record_event(DIAG_EVENT_STAGE_FAIL, safe_stage, error);

    /* Print failure */
    if (boot_console) {
        diag_write_str("\n");
        diag_write_str("MyOS Boot Diagnostic\n");
        diag_write_str("====================\n\n");

        /* Print all known, in-range stages. Use the clamped stage for the
         * failing slot so unknown/reserved values cannot read past the
         * designated initializer table. */
        for (int i = 0; i <= (int)safe_stage && i <= BOOT_STAGE_KERNEL_MAIN; i++) {
            if (i <= (int)diag_state.last_ok_stage) {
                diag_write_str("[ OK ] ");
            } else if (i == (int)safe_stage) {
                diag_write_str("[FAIL] ");
            } else {
                diag_write_str("[....] ");
            }
            diag_write_str(boot_stage_names[i]);
            diag_write_str("\n");
        }

        diag_write_str("\nFailure:\n");
        diag_write_str("  Stage: ");
        diag_write_str(boot_stage_names[safe_stage]);
        diag_write_str("\n");

        /* Print error details */
        char errbuf[128];
        boot_diag_format_error(errbuf, sizeof(errbuf), error);
        diag_write_str("  Error: ");
        diag_write_str(errbuf);
        diag_write_str("\n");

        /* Print numeric code */
        char codebuf[32];
        boot_diag_format_hex32(codebuf, sizeof(codebuf), (uint32_t)error);
        diag_write_str("  Code: ");
        diag_write_str(codebuf);
        diag_write_str("\n\n");

        diag_write_str("BOOT FAILED\n");
        diag_flush();
    }

    /* Render to framebuffer if available */
    if (boot_fb_renderer && boot_fb_renderer->render_panic) {
        boot_fb_renderer->render_panic(&diag_state, NULL);
    }

    /* Halt the system */
    while (1) {
        asm volatile("wfe");
    }
}

__attribute__((noreturn)) void boot_diag_panic(boot_stage_t stage, boot_error_t error) {
    /* Capture exception context if we're in an exception handler */
    /* This is typically called from the exception handler which already
     * has the context in registers. The context is passed separately. */

    diag_state.current_stage = stage;
    diag_state.last_error = error | DIAG_TYPE_EXCEPTION;
    diag_state.failure_flag = 1;
    diag_state.timestamp = diag_get_timestamp();
    diag_record_event(DIAG_EVENT_PANIC, stage, error | DIAG_TYPE_EXCEPTION);

    if (boot_console) {
        diag_write_str("\n");
        diag_write_str("MyOS KERNEL PANIC\n");
        diag_write_str("==================\n\n");

        diag_write_str("Stage: ");
        diag_write_str(boot_stage_names[stage]);
        diag_write_str("\n\n");

        /* Print error */
        char errbuf[128];
        boot_diag_format_error(errbuf, sizeof(errbuf), error);
        diag_write_str("Error: ");
        diag_write_str(errbuf);
        diag_write_str("\n");

        char codebuf[32];
        boot_diag_format_hex32(codebuf, sizeof(codebuf), (uint32_t)error);
        diag_write_str("Code: ");
        diag_write_str(codebuf);
        diag_write_str("\n\n");

        /* Print exception context if available */
        if (diag_state.esr_el1 != 0) {
            char buf[64];
            diag_write_str("\nException Context:\n");
            diag_write_str("  ESR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), diag_state.esr_el1);
            diag_write_str(buf);
            diag_write_str("\n");

            diag_write_str("  FAR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), diag_state.far_el1);
            diag_write_str(buf);
            diag_write_str("\n");

            diag_write_str("  ELR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), diag_state.elr_el1);
            diag_write_str(buf);
            diag_write_str("\n");

            diag_write_str("  SPSR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), diag_state.spsr_el1);
            diag_write_str(buf);
            diag_write_str("\n");
        }

        diag_write_str("\nSYSTEM HALTED\n");
        diag_flush();
    }

    /* Render to framebuffer if available */
    if (boot_fb_renderer && boot_fb_renderer->render_panic) {
        exception_context_t ctx = {
            .esr_el1 = diag_state.esr_el1,
            .far_el1 = diag_state.far_el1,
            .elr_el1 = diag_state.elr_el1,
            .spsr_el1 = diag_state.spsr_el1,
        };
        boot_fb_renderer->render_panic(&diag_state, &ctx);
    }

    /* Halt */
    while (1) {
        asm volatile("wfe");
    }
}

const boot_diag_state_t *boot_diag_get_state(void) {
    return &diag_state;
}

boot_stage_t boot_diag_get_current_stage(void) {
    return diag_state.current_stage;
}

void boot_diag_print_report(void) {
    if (!boot_console) return;

    diag_write_str("\nMyOS Boot Diagnostic Report\n");
    diag_write_str("===========================\n\n");

    for (int i = 0; i <= BOOT_STAGE_KERNEL_MAIN; i++) {
        if (i <= (int)diag_state.last_ok_stage) {
            diag_write_str("[ OK ] ");
        } else if (i == (int)diag_state.current_stage) {
            diag_write_str("[....] ");
        } else {
            diag_write_str("[....] ");
        }
        diag_write_str(boot_stage_names[i]);
        diag_write_str("\n");
    }

    if (diag_state.failure_flag) {
        diag_write_str("\nLast Error: ");
        char errbuf[128];
        boot_diag_format_error(errbuf, sizeof(errbuf), diag_state.last_error);
        diag_write_str(errbuf);
        diag_write_str("\n");
    }

    diag_flush();
}

void boot_diag_print_error(boot_error_t error) {
    if (!boot_console) return;

    char buf[128];
    boot_diag_format_error(buf, sizeof(buf), error);
    diag_write_str(buf);
    diag_write_str("\n");
}

void boot_diag_print_stage(boot_stage_t stage) {
    if (!boot_console) return;
    if (stage < BOOT_STAGE_UNKNOWN) {
        diag_write_str(boot_stage_names[stage]);
    } else {
        diag_write_str("UNKNOWN");
    }
}

/* ========================================================================
 * EXCEPTION HANDLING
 * ======================================================================== */

__attribute__((noreturn)) void boot_diag_exception_handler(
    boot_error_t base_error,
    uint32_t exc_class,
    boot_stage_t stage,
    const exception_context_t *ctx) {

    (void)exc_class;  /* Currently unused, reserved for future exception-specific handling */

    /* Capture exception context */
    if (ctx) {
        diag_state.esr_el1 = ctx->esr_el1;
        diag_state.far_el1 = ctx->far_el1;
        diag_state.elr_el1 = ctx->elr_el1;
        diag_state.spsr_el1 = ctx->spsr_el1;
    }

    /* Build error with exception type (used for framebuffer panic) */
    (void)(base_error | DIAG_TYPE_EXCEPTION);

    diag_state.current_stage = stage;
    diag_state.last_error = base_error | DIAG_TYPE_EXCEPTION;
    diag_state.failure_flag = 1;
    diag_state.timestamp = diag_get_timestamp();
    diag_record_event(DIAG_EVENT_EXCEPTION, stage, base_error | DIAG_TYPE_EXCEPTION);

    /* Call panic with exception context */
    if (boot_console) {
        diag_write_str("\n");
        diag_write_str("MyOS KERNEL PANIC - UNHANDLED EXCEPTION\n");
        diag_write_str("==========================================\n\n");

        diag_write_str("Stage: ");
        diag_write_str(boot_stage_names[stage]);
        diag_write_str("\n\n");

        char errbuf[128];
        boot_diag_format_error(errbuf, sizeof(errbuf), base_error);
        diag_write_str("Error: ");
        diag_write_str(errbuf);
        diag_write_str(" (EXCEPTION)\n\n");

        /* Print exception context */
        if (ctx) {
            char buf[64];

            diag_write_str("Exception Context:\n");
            diag_write_str("  ESR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), ctx->esr_el1);
            diag_write_str(buf);
            diag_write_str(" (EC=");
            boot_diag_format_hex64(buf, sizeof(buf), (ctx->esr_el1 >> 26) & 0x3F);
            diag_write_str(buf);
            diag_write_str(")\n");

            diag_write_str("  FAR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), ctx->far_el1);
            diag_write_str(buf);
            diag_write_str("\n");

            diag_write_str("  ELR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), ctx->elr_el1);
            diag_write_str(buf);
            diag_write_str("\n");

            diag_write_str("  SPSR_EL1: ");
            boot_diag_format_hex64(buf, sizeof(buf), ctx->spsr_el1);
            diag_write_str(buf);
            diag_write_str("\n");
        }

        diag_write_str("\nSYSTEM HALTED\n");
        diag_flush();
    }

    /* Framebuffer panic */
    if (boot_fb_renderer && boot_fb_renderer->render_panic) {
        boot_fb_renderer->render_panic(&diag_state, ctx);
    }

    /* Halt */
    while (1) {
        asm volatile("wfe");
    }
}

/* ========================================================================
 * FORMATTING UTILITIES
 * ======================================================================== */

size_t boot_diag_format_hex32(char *buf, size_t bufsz, uint32_t val) {
    if (bufsz < 11) return 0; /* "0xXXXXXXXX" + null = 11 bytes */

    const char *hex = "0123456789ABCDEF";
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 7; i >= 0; i--) {
        buf[2 + (7 - i)] = hex[(val >> (i * 4)) & 0xF];
    }
    buf[10] = '\0';
    return 10;
}

size_t boot_diag_format_hex64(char *buf, size_t bufsz, uint64_t val) {
    if (bufsz < 19) return 0; /* "0xXXXXXXXXXXXXXXXX" + null = 19 bytes */

    const char *hex = "0123456789ABCDEF";
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 15; i >= 0; i--) {
        buf[2 + (15 - i)] = hex[(val >> (i * 4)) & 0xF];
    }
    buf[18] = '\0';
    return 18;
}

size_t boot_diag_format_error(char *buf, size_t bufsz, boot_error_t error) {
    if (bufsz < 32) return 0;

    /* Extract subsystem and type */
    uint32_t subsys = (error >> 16) & 0xF;
    uint32_t type = (error >> 4) & 0xF;
    (void)(error & 0xF);  /* code - not used in current format */

    const char *subsys_names[] = {
        "CPU", "MEMORY", "UART", "TIMER", "USB", "DRIVER", "KERNEL", "GENERIC"
    };

    const char *type_names[] = {
        "FAILED", "TIMEOUT", "INVALID_STATE", "UNSUPPORTED_HW",
        "EXCEPTION", "INVALID_PARAM", "RESOURCE_EXH", "INVALID_TRANS"
    };

    size_t len = 0;
    const char *subsys_str = (subsys < 8) ? subsys_names[subsys] : "UNKNOWN";
    const char *type_str = (type < 8) ? type_names[type] : "UNKNOWN";

    /* Copy subsystem */
    for (int i = 0; subsys_str[i] && len < bufsz - 1; i++) {
        buf[len++] = subsys_str[i];
    }
    if (len < bufsz - 1) buf[len++] = '.';

    /* Copy type */
    for (int i = 0; type_str[i] && len < bufsz - 1; i++) {
        buf[len++] = type_str[i];
    }
    if (len < bufsz - 1) buf[len++] = '.';

    /* Code as hex */
    if (len < bufsz - 4) {
        buf[len++] = '0';
        buf[len++] = 'x';
        const char *hex = "0123456789ABCDEF";
        buf[len++] = hex[(error >> 8) & 0xF];
        buf[len++] = hex[(error >> 4) & 0xF];
        buf[len++] = hex[error & 0xF];
    }

    buf[len] = '\0';
    return len;
}

int boot_diag_state_valid(const boot_diag_state_t *state) {
    return state && state->magic == DIAG_STATE_MAGIC && state->version == DIAG_STATE_VERSION;
}

/* ========================================================================
 * EVENT BUFFER API
 * ======================================================================== */

void boot_diag_record_event(diag_event_type_t type, boot_stage_t stage, boot_error_t error) {
    diag_record_event(type, stage, error);
}

uint32_t boot_diag_get_event_count(void) {
    return diag_state.event_count;
}

const diag_event_t *boot_diag_get_event(uint32_t index) {
    if (index >= diag_state.event_count) {
        return NULL;
    }
    /* Ring buffer: oldest event is at (head - count) % DIAG_MAX_EVENTS */
    uint32_t start = (diag_state.event_head + DIAG_MAX_EVENTS - diag_state.event_count) % DIAG_MAX_EVENTS;
    uint32_t idx = (start + index) % DIAG_MAX_EVENTS;
    return &diag_state.events[idx];
}

void boot_diag_clear_events(void) {
    diag_state.event_head = 0;
    diag_state.event_count = 0;
}

/* ========================================================================
 * FAULT INJECTION (TEST ONLY)
 * ======================================================================== */

#ifdef MYOS_DIAG_FAULT_INJECTION

/* Maximum number of injectable failures */
#define DIAG_MAX_INJECTED_FAILURES 8

typedef struct {
    boot_stage_t stage;
    boot_error_t error;
    uint32_t armed;  /* 1 = armed, 0 = not armed */
} diag_injected_failure_t;

static diag_injected_failure_t injected_failures[DIAG_MAX_INJECTED_FAILURES];
static uint32_t injected_failure_count = 0;

#endif /* MYOS_DIAG_FAULT_INJECTION */

/* Check if a failure is injected for the given stage (always available) */
#ifdef MYOS_DIAG_FAULT_INJECTION
static int diag_check_injected_failure(boot_stage_t stage, boot_error_t *out_error) {
    for (uint32_t i = 0; i < injected_failure_count; i++) {
        if (injected_failures[i].armed && injected_failures[i].stage == stage) {
            *out_error = injected_failures[i].error;
            injected_failures[i].armed = 0;  /* One-shot */
            return 1;
        }
    }
    return 0;
}
#endif /* MYOS_DIAG_FAULT_INJECTION */

#ifdef MYOS_DIAG_FAULT_INJECTION

int boot_diag_inject_failure(boot_stage_t stage, boot_error_t error) {
    if (stage >= BOOT_STAGE_UNKNOWN) {
        return -1;
    }
    if (injected_failure_count >= DIAG_MAX_INJECTED_FAILURES) {
        return -1;
    }

    injected_failures[injected_failure_count].stage = stage;
    injected_failures[injected_failure_count].error = error;
    injected_failures[injected_failure_count].armed = 1;
    injected_failure_count++;
    return 0;
}

void boot_diag_clear_injected_failures(void) {
    injected_failure_count = 0;
}

int boot_diag_fault_injection_enabled(void) {
    return 1;
}

#else

int boot_diag_inject_failure(boot_stage_t stage, boot_error_t error) {
    (void)stage; (void)error;
    return -1;
}

void boot_diag_clear_injected_failures(void) { }

int boot_diag_fault_injection_enabled(void) {
    return 0;
}

#endif