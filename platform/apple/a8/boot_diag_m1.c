/*
 * platform/apple/a8/boot_diag_m1.c — Minimal Boot Diagnostic for M1
 *
 * target: iphone6 (Apple A8 real hardware)
 *
 * M1-only minimal implementation. Only BOOTSTRAP stage exists.
 * No console output, no formatting, no panic handling.
 * Fits within the single DFU command-block budget (M1_MAX_PAYLOAD).
 */

#include "kernel/boot_diag.h"

/* ========================================================================
 * GLOBAL STATE (minimal)
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

/* Console instance (not available in M1) */
struct boot_console *boot_console = NULL;

/* Framebuffer renderer (not available in M1) */
const boot_fb_renderer_t *boot_fb_renderer = NULL;

/* Stage names for console output (minimal, for symbol presence) */
/* Compact array: 12 stages (0-11), not sparse 256-entry array */
const char * const boot_stage_names[] = {
    "BOOTSTRAP",      /* 0x00 */
    "CPU_INIT",       /* 0x01 */
    "MEMORY_INIT",    /* 0x02 */
    "MMU_INIT",       /* 0x03 */
    "EXCEPTION_INIT", /* 0x04 */
    "UART_INIT",      /* 0x05 */
    "TIMER_INIT",     /* 0x06 */
    "DRIVER_INIT",    /* 0x07 */
    "USB_INIT",       /* 0x08 */
    "KERNEL_INIT",    /* 0x09 */
    "KERNEL_MAIN",    /* 0x0A */
    "UNKNOWN",        /* 0xFF -> index 11 */
};

/* ========================================================================
 * MINIMAL API
 * ======================================================================== */

void boot_diag_init(void) {
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

int boot_diag_begin(boot_stage_t stage) {
    if (stage >= BOOT_STAGE_UNKNOWN) {
        return -1;
    }
    diag_state.current_stage = stage;
    /* No timestamp in M1 (no timer init yet) */
    return 0;
}

void boot_diag_ok(boot_stage_t stage) {
    if (stage >= BOOT_STAGE_UNKNOWN) {
        return;
    }
    if (diag_state.current_stage == stage) {
        diag_state.last_ok_stage = stage;
        diag_state.current_stage = BOOT_STAGE_UNKNOWN;
    }
}

/* Stub functions (not used in M1, but required for linking) */

void boot_diag_fail(boot_stage_t stage, boot_error_t error) {
    (void)stage; (void)error;
    diag_state.last_error = error;
    diag_state.failure_flag = 1;
    while (1) { asm volatile("wfe"); }
}

void boot_diag_panic(boot_stage_t stage, boot_error_t error) {
    (void)stage; (void)error;
    diag_state.last_error = error | DIAG_TYPE_EXCEPTION;
    diag_state.failure_flag = 1;
    while (1) { asm volatile("wfe"); }
}

const boot_diag_state_t *boot_diag_get_state(void) {
    return &diag_state;
}

void boot_diag_print_report(void) { }
void boot_diag_print_error(boot_error_t error) { (void)error; }
void boot_diag_print_stage(boot_stage_t stage) { (void)stage; }

void boot_diag_exception_handler(
    boot_error_t base_error,
    uint32_t exc_class,
    boot_stage_t stage,
    const exception_context_t *ctx) {
    (void)base_error; (void)exc_class; (void)stage; (void)ctx;
    diag_state.last_error = base_error | DIAG_TYPE_EXCEPTION;
    diag_state.failure_flag = 1;
    while (1) { asm volatile("wfe"); }
}

void boot_console_register(struct boot_console *console) {
    (void)console;
    /* No-op in M1 */
}

void boot_fb_register_renderer(const boot_fb_renderer_t *renderer) {
    (void)renderer;
    /* No-op in M1 */
}

/* Minimal formatting - only used if needed */

size_t boot_diag_format_hex32(char *buf, size_t bufsz, uint32_t val) {
    (void)val;
    if (bufsz >= 3) {
        buf[0] = '0'; buf[1] = 'x'; buf[2] = '0'; buf[3] = '\0';
        return 3;
    }
    return 0;
}

size_t boot_diag_format_hex64(char *buf, size_t bufsz, uint64_t val) {
    (void)val;
    if (bufsz >= 3) {
        buf[0] = '0'; buf[1] = 'x'; buf[2] = '0'; buf[3] = '\0';
        return 3;
    }
    return 0;
}

size_t boot_diag_format_error(char *buf, size_t bufsz, boot_error_t error) {
    (void)error;
    if (bufsz >= 5) {
        buf[0] = 'E'; buf[1] = 'R'; buf[2] = 'R'; buf[3] = '0'; buf[4] = '\0';
        return 4;
    }
    return 0;
}

int boot_diag_state_valid(const boot_diag_state_t *state) {
    return state && state->magic == DIAG_STATE_MAGIC && state->version == DIAG_STATE_VERSION;
}

/* Event buffer API stubs */
void boot_diag_record_event(diag_event_type_t type, boot_stage_t stage, boot_error_t error) {
    (void)type; (void)stage; (void)error;
    /* No-op in M1 */
}

uint32_t boot_diag_get_event_count(void) {
    return 0;
}

const diag_event_t *boot_diag_get_event(uint32_t index) {
    (void)index;
    return NULL;
}

void boot_diag_clear_events(void) {
    /* No-op in M1 */
}

/* Minimal formatting - only used if needed */