/*
 * arch/aarch64/boot_diag.h — Boot Diagnostic Framework for MyOS
 *
 * target: iphone6 (Apple A8 real hardware) AND qemu-virt (tests)
 *
 * Purpose: make boot failures observable and diagnosable by reporting
 * EXACTLY which boot stage and subsystem failed, with stable machine-readable
 * error codes. No dynamic allocation; usable from the first instruction.
 *
 * Design: freestanding, allocation-free, deterministic, architecture-conscious.
 */

#ifndef MYOS_ARCH_AARCH64_BOOT_DIAG_H
#define MYOS_ARCH_AARCH64_BOOT_DIAG_H

#include <stdint.h>
#include <stddef.h>

/* ========================================================================
 * BOOT STAGES
 *
 * Stable identifiers for each boot stage. These correspond to the actual
 * initialization sequence in start.S. Only stages that actually exist in
 * the current implementation are defined.
 * ======================================================================== */

typedef enum {
    BOOT_STAGE_BOOTSTRAP     = 0x00,  /* entry: park cores, stack, vectors */
    BOOT_STAGE_CPU_INIT      = 0x01,  /* CurrentEL, MIDR, CurrentEL checks */
    BOOT_STAGE_MEMORY_INIT   = 0x02,  /* RAM validation, DRAM map discovery */
    BOOT_STAGE_MMU_INIT      = 0x03,  /* page tables, identity map, enable */
    BOOT_STAGE_EXCEPTION_INIT = 0x04, /* VBAR_EL1, vector table install */
    BOOT_STAGE_UART_INIT     = 0x05,  /* USB console / UART initialization */
    BOOT_STAGE_TIMER_INIT    = 0x06,  /* ARMv8 generic timer (CNTPCT/CNTFRQ) */
    BOOT_STAGE_DRIVER_INIT   = 0x07,  /* platform driver probes (AIC, etc.) */
    BOOT_STAGE_USB_INIT      = 0x08,  /* USB controller / DFU / pongo shell */
    BOOT_STAGE_KERNEL_INIT   = 0x09,  /* kernel subsystems, allocators */
    BOOT_STAGE_KERNEL_MAIN   = 0x0A,  /* kernel_main() reached */

    /* Sentinel for "no stage / unknown" */
    BOOT_STAGE_UNKNOWN       = 0xFF,
} boot_stage_t;

/* Human-readable stage names (for console output) */
extern const char * const boot_stage_names[];

/* ========================================================================
 * ERROR CODES
 *
 * Stable machine-readable error codes. Each encodes:
 *   - subsystem (high nibble of byte 1)
 *   - failure type (low nibble of byte 1)
 *   - specific code (bytes 0-1)
 *
 * Format: 0xSSTT where SS=subsystem, TT=type+code
 *
 * Subsystems (high nibble of second byte):
 *   0x0 = CPU
 *   0x1 = Memory/MMU
 *   0x2 = UART/Console
 *   0x3 = Timer
 *   0x4 = USB
 *   0x5 = Driver/Platform
 *   0x6 = Kernel
 *   0xF = Generic/Unknown
 *
 * Failure types (per subsystem, encoded in low nibble + code):
 *   0x0 = FAILED (generic initialization failure)
 *   0x1 = TIMEOUT
 *   0x2 = INVALID_STATE
 *   0x3 = UNSUPPORTED_HARDWARE
 *   0x4 = EXCEPTION (unexpected exception during init)
 *   0x5 = INVALID_PARAMETER
 *   0x6 = RESOURCE_EXHAUSTED
 *   0x7 = INVALID_STATE_TRANSITION
 * ======================================================================== */

typedef uint32_t boot_error_t;

/* Subsystem identifiers (high byte) */
#define DIAG_SUBSYS_CPU       0x00000000
#define DIAG_SUBSYS_MEMORY    0x00010000
#define DIAG_SUBSYS_UART      0x00020000
#define DIAG_SUBSYS_TIMER     0x00030000
#define DIAG_SUBSYS_USB       0x00040000
#define DIAG_SUBSYS_DRIVER    0x00050000
#define DIAG_SUBSYS_KERNEL    0x00060000
#define DIAG_SUBSYS_GENERIC   0x000F0000

/* Failure types */
#define DIAG_TYPE_FAILED          0x00000000
#define DIAG_TYPE_TIMEOUT         0x00000010
#define DIAG_TYPE_INVALID_STATE   0x00000020
#define DIAG_TYPE_UNSUPPORTED_HW  0x00000030
#define DIAG_TYPE_EXCEPTION       0x00000040
#define DIAG_TYPE_INVALID_PARAM   0x00000050
#define DIAG_TYPE_RESOURCE_EXH    0x00000060
#define DIAG_TYPE_INVALID_TRANS   0x00000070

/* Predefined error codes */
#define ERR_CPU_INIT_FAILED       (DIAG_SUBSYS_CPU     | DIAG_TYPE_FAILED)
#define ERR_CPU_INVALID_EL        (DIAG_SUBSYS_CPU     | DIAG_TYPE_INVALID_STATE)
#define ERR_CPU_MIDR_READ_FAILED  (DIAG_SUBSYS_CPU     | DIAG_TYPE_FAILED)

#define ERR_MEM_INIT_FAILED       (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_FAILED)
#define ERR_MEM_INVALID_MAP       (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_INVALID_STATE)
#define ERR_MEM_VALIDATION_FAILED (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_FAILED)
#define ERR_MMU_INIT_FAILED       (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_FAILED)
#define ERR_MMU_PAGE_TABLE_FAILED (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_FAILED)
#define ERR_MMU_ENABLE_FAILED     (DIAG_SUBSYS_MEMORY  | DIAG_TYPE_FAILED)

#define ERR_UART_INIT_FAILED      (DIAG_SUBSYS_UART    | DIAG_TYPE_FAILED)
#define ERR_UART_TIMEOUT          (DIAG_SUBSYS_UART    | DIAG_TYPE_TIMEOUT)
#define ERR_UART_INVALID_STATE    (DIAG_SUBSYS_UART    | DIAG_TYPE_INVALID_STATE)

#define ERR_TIMER_INIT_FAILED     (DIAG_SUBSYS_TIMER   | DIAG_TYPE_FAILED)
#define ERR_TIMER_TIMEOUT         (DIAG_SUBSYS_TIMER   | DIAG_TYPE_TIMEOUT)
#define ERR_TIMER_FREQ_ZERO       (DIAG_SUBSYS_TIMER   | DIAG_TYPE_INVALID_STATE)

#define ERR_USB_INIT_FAILED       (DIAG_SUBSYS_USB     | DIAG_TYPE_FAILED)
#define ERR_USB_TIMEOUT           (DIAG_SUBSYS_USB     | DIAG_TYPE_TIMEOUT)
#define ERR_USB_DEVICE_NOT_FOUND  (DIAG_SUBSYS_USB     | DIAG_TYPE_INVALID_STATE)
#define ERR_USB_CONTROL_XFER_FAILED (DIAG_SUBSYS_USB   | DIAG_TYPE_FAILED)

#define ERR_DRIVER_INIT_FAILED    (DIAG_SUBSYS_DRIVER  | DIAG_TYPE_FAILED)
#define ERR_DRIVER_UNSUPPORTED_HW (DIAG_SUBSYS_DRIVER  | DIAG_TYPE_UNSUPPORTED_HW)
#define ERR_DRIVER_TIMEOUT        (DIAG_SUBSYS_DRIVER  | DIAG_TYPE_TIMEOUT)

#define ERR_KERNEL_INIT_FAILED    (DIAG_SUBSYS_KERNEL  | DIAG_TYPE_FAILED)
#define ERR_KERNEL_ALLOC_FAILED   (DIAG_SUBSYS_KERNEL  | DIAG_TYPE_RESOURCE_EXH)
#define ERR_KERNEL_INVALID_STATE  (DIAG_SUBSYS_KERNEL  | DIAG_TYPE_INVALID_STATE)

#define ERR_GENERIC_FAILED        (DIAG_SUBSYS_GENERIC | DIAG_TYPE_FAILED)
#define ERR_GENERIC_TIMEOUT       (DIAG_SUBSYS_GENERIC | DIAG_TYPE_TIMEOUT)
#define ERR_GENERIC_INVALID_STATE (DIAG_SUBSYS_GENERIC | DIAG_TYPE_INVALID_STATE)
#define ERR_GENERIC_EXCEPTION     (DIAG_SUBSYS_GENERIC | DIAG_TYPE_EXCEPTION)
#define ERR_GENERIC_UNSUPPORTED   (DIAG_SUBSYS_GENERIC | DIAG_TYPE_UNSUPPORTED_HW)

/* ========================================================================
 * DIAGNOSTIC STATE
 *
 * Fixed-size storage in RAM. No heap allocation. State does NOT survive
 * power cycles or hardware resets. RAM contents are not preserved across
 * warm resets unless the platform explicitly retains them.
 * ======================================================================== */

/* Maximum number of recent diagnostic events to track */
#define DIAG_MAX_EVENTS 16

/* Event types for the event buffer */
typedef enum {
    DIAG_EVENT_STAGE_BEGIN = 0,
    DIAG_EVENT_STAGE_OK    = 1,
    DIAG_EVENT_STAGE_FAIL  = 2,
    DIAG_EVENT_PANIC       = 3,
    DIAG_EVENT_EXCEPTION   = 4,
} diag_event_type_t;

/* Single diagnostic event record */
typedef struct {
    uint32_t type;          /* diag_event_type_t */
    boot_stage_t stage;     /* stage at time of event */
    boot_error_t error;     /* error code (0 if not applicable) */
    uint64_t timestamp;     /* CNTPCT_EL0 if available */
} diag_event_t;

/* Complete diagnostic state */
typedef struct {
    uint32_t magic;                 /* 0x44494147 = "DIAG" */
    uint32_t version;               /* 1 */

    /* Stage tracking */
    boot_stage_t current_stage;     /* stage currently executing */
    boot_stage_t last_ok_stage;     /* last stage that reported OK */

    /* Failure tracking */
    boot_error_t last_error;        /* last error code (0 = none) */
    uint32_t failure_flag;          /* non-zero if any failure occurred */
    uint64_t timestamp;             /* CNTPCT_EL0 at failure (if available) */

    /* Exception context (captured when exception occurs) */
    uint64_t esr_el1;               /* ESR_EL1 (exception syndrome) */
    uint64_t far_el1;               /* FAR_EL1 (fault address) */
    uint64_t elr_el1;               /* ELR_EL1 (exception link register) */
    uint64_t spsr_el1;              /* SPSR_EL1 (saved processor state) */
    uint64_t elr_el2;               /* ELR_EL2 (if entered from EL2) */
    uint64_t spsr_el2;              /* SPSR_EL2 (if entered from EL2) */

    /* Bounded event buffer (ring buffer) */
    diag_event_t events[DIAG_MAX_EVENTS];
    uint32_t event_head;            /* next write index */
    uint32_t event_count;           /* number of valid events */

} boot_diag_state_t;

/* Magic value for diagnostic state validation */
#define DIAG_STATE_MAGIC    0x44494147  /* "DIAG" */
#define DIAG_STATE_VERSION  1

/* Get current boot stage (for exception handlers) */
boot_stage_t boot_diag_get_current_stage(void);

/* ========================================================================
 * CONSOLE ABSTRACTION
 *
 * Minimal console interface. Implementation provided by platform code
 * (UART/USB for iphone6, PL011 for qemu-virt).
 * ======================================================================== */

struct boot_console;

/* Console operations */
typedef struct {
    /* Initialize console hardware. Returns 0 on success, error code on failure. */
    int (*init)(struct boot_console *console);

    /* Write a single character. Returns 0 on success. */
    int (*putc)(struct boot_console *console, char c);

    /* Write a string. Returns number of characters written, or error. */
    int (*puts)(struct boot_console *console, const char *s);

    /* Write a buffer. Returns number of bytes written, or error. */
    int (*write)(struct boot_console *console, const void *buf, size_t len);

    /* Flush any buffered output. */
    void (*flush)(struct boot_console *console);

    /* Platform-specific data */
    void *platform_data;
} boot_console_ops_t;

struct boot_console {
    const boot_console_ops_t *ops;
    void *platform_data;
};

/* Global console instance (initialized by platform code) */
extern struct boot_console *boot_console;

/* Register a console instance (called by platform UART driver) */
void boot_console_register(struct boot_console *console);

/* Convenience macros */
#define diag_puts(s)    do { if (boot_console) boot_console->ops->puts(boot_console, s); } while(0)
#define diag_putc(c)    do { if (boot_console) boot_console->ops->putc(boot_console, c); } while(0)
#define diag_write(b,l) do { if (boot_console) boot_console->ops->write(boot_console, b, l); } while(0)

/* ========================================================================
 * EXCEPTION HANDLING INTEGRATION
 *
 * Called from vector table when an unexpected exception occurs.
 * Captures exception context and reports via diagnostic framework.
 * ======================================================================== */

/* Exception context captured by vector.S */
typedef struct {
    uint64_t esr_el1;
    uint64_t far_el1;
    uint64_t elr_el1;
    uint64_t spsr_el1;
    uint64_t sp_el0;
    uint64_t elr_el1_val;
    uint64_t spsr_el1_val;
    uint64_t sp_el0_val;
} exception_context_t;

/* Exception class identifiers (matching vector table) */
#define EXC_CLASS_SYNC    0x00
#define EXC_CLASS_IRQ     0x01
#define EXC_CLASS_FIQ     0x02
#define EXC_CLASS_SERROR  0x03

/* Install VBAR_EL1 vector table */
void vbar_el1_install(void);

/* Called from vector.S when an unexpected exception occurs.
 * Parameters:
 *   base_error - subsystem-specific error code
 *   exc_class  - exception class (EXC_CLASS_*)
 *   stage      - current boot stage
 *   ctx        - exception context (ESR_EL1, FAR_EL1, ELR_EL1, SPSR_EL1)
 */
__attribute__((noreturn)) void boot_diag_exception_handler(
    boot_error_t base_error,
    uint32_t exc_class,
    boot_stage_t stage,
    const exception_context_t *ctx);

/* ========================================================================
 * FRAMEBUFFER RENDERER (OPTIONAL)
 *
 * Placeholder for future framebuffer support. When a framebuffer
 * becomes available, platform code implements this interface.
 * ======================================================================== */

typedef struct {
    void (*init)(void *framebuffer, size_t width, size_t height, size_t stride);
    void (*render_report)(const boot_diag_state_t *state);
    void (*render_panic)(const boot_diag_state_t *state, const exception_context_t *ctx);
} boot_fb_renderer_t;

extern const boot_fb_renderer_t *boot_fb_renderer;

/* Register a framebuffer renderer (called by platform display driver) */
void boot_fb_register_renderer(const boot_fb_renderer_t *renderer);

/* ========================================================================
 * DIAGNOSTIC API
 *
 * Core functions for reporting boot progress and failures.
 * All functions are allocation-free and usable from early boot.
 * ======================================================================== */

/* Initialize the diagnostic system. Must be called before any other diag function. */
void boot_diag_init(void);

/* Mark a stage as beginning. Returns 0 on success. */
int boot_diag_begin(boot_stage_t stage);

/* Mark a stage as completed successfully. */
void boot_diag_ok(boot_stage_t stage);

/* Mark a stage as failed with an error code. Does not return. */
__attribute__((noreturn)) void boot_diag_fail(boot_stage_t stage, boot_error_t error);

/* Panic with diagnostic context (exception, unrecoverable error). Does not return. */
__attribute__((noreturn)) void boot_diag_panic(boot_stage_t stage, boot_error_t error);

/* Get the current diagnostic state (read-only) */
const boot_diag_state_t *boot_diag_get_state(void);

/* Print a full diagnostic report to the console */
void boot_diag_print_report(void);

/* Print error code as human-readable string */
void boot_diag_print_error(boot_error_t error);

/* Print stage name */
void boot_diag_print_stage(boot_stage_t stage);

/* ========================================================================
 * EVENT BUFFER API
 * ======================================================================== */

/* Record a diagnostic event (internal use) */
void boot_diag_record_event(diag_event_type_t type, boot_stage_t stage, boot_error_t error);

/* Get the number of recorded events */
uint32_t boot_diag_get_event_count(void);

/* Get event at index (0 = oldest, event_count-1 = newest) */
const diag_event_t *boot_diag_get_event(uint32_t index);

/* Clear event buffer */
void boot_diag_clear_events(void);

/* ========================================================================
 * FAULT INJECTION (TEST ONLY)
 *
 * These functions are only available when MYOS_DIAG_FAULT_INJECTION is defined.
 * They allow tests to simulate failures at specific stages without hardware.
 * ======================================================================== */

#ifdef MYOS_DIAG_FAULT_INJECTION

/* Inject a failure at the next boot_diag_begin() call for the given stage.
 * Returns 0 on success, -1 if stage is invalid.
 * The failure will be reported with the given error code.
 */
int boot_diag_inject_failure(boot_stage_t stage, boot_error_t error);

/* Clear all injected failures */
void boot_diag_clear_injected_failures(void);

/* Check if fault injection is enabled */
int boot_diag_fault_injection_enabled(void);

#endif /* MYOS_DIAG_FAULT_INJECTION */

/* ========================================================================
 * UTILITY
 * ======================================================================== */

/* Format a 32-bit value as hex string into buffer. Returns length. */
size_t boot_diag_format_hex32(char *buf, size_t bufsz, uint32_t val);

/* Format a 64-bit value as hex string into buffer. Returns length. */
size_t boot_diag_format_hex64(char *buf, size_t bufsz, uint64_t val);

/* Format error code as "SUBSYSTEM.TYPE.CODE" string. Returns length. */
size_t boot_diag_format_error(char *buf, size_t bufsz, boot_error_t error);

/* Validate diagnostic state magic/version */
int boot_diag_state_valid(const boot_diag_state_t *state);

#endif /* MYOS_ARCH_AARCH64_BOOT_DIAG_H */