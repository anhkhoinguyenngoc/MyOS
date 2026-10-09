# MyOS Boot Diagnostic Framework

## Overview

The MyOS Boot Diagnostic Framework provides a deterministic, allocation-free mechanism for reporting boot progress and failures on Apple A8 (iPhone 6) and other targets. It is designed for freestanding AArch64 environments with no heap, no dynamic allocation, and no operating system services.

## Design Goals

1. **Deterministic** — Same inputs always produce same outputs; no hidden state
2. **Allocation-free** — No malloc, no heap, no dynamic memory
3. **Low-memory** — Fits in early boot before MMU/memory init
4. **Architecture-conscious** — Uses only architecturally-defined registers
5. **Extensible** — New stages and error codes added without breaking existing code
6. **Multi-output** — UART (authoritative), framebuffer (when available), RAM state (for post-mortem)

## Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    boot_diag.h/c                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Stage Enum   │  │ Error Codes  │  │ State Structure  │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Console API  │  │ FB Renderer  │  │ Exception Ctx    │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Event Buffer │  │ Fault Inject │  │ Vector Table     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Stage Identifiers (`boot_stage_t`)

| Value | Name | Description |
|-------|------|-------------|
| 0 | `BOOT_STAGE_BOOTSTRAP` | Entry point, register capture, DFU evidence |
| 1 | `BOOT_STAGE_CPU_INIT` | CPU configuration, caches, errata workarounds |
| 2 | `BOOT_STAGE_MEMORY_INIT` | DRAM init, memory map discovery |
| 3 | `BOOT_STAGE_MMU_INIT` | Page tables, VA/PA mapping, caches on |
| 4 | `BOOT_STAGE_EXCEPTION_INIT` | Vector tables, exception handlers |
| 5 | `BOOT_STAGE_UART_INIT` | UART/serial console bring-up |
| 6 | `BOOT_STAGE_TIMER_INIT` | Generic timer, CNTPCT_EL0 |
| 7 | `BOOT_STAGE_DRIVER_INIT` | Platform driver initialization |
| 8 | `BOOT_STAGE_USB_INIT` | USB controller, DFU/UART gadget |
| 9 | `BOOT_STAGE_KERNEL_INIT` | Kernel subsystems, scheduler, VFS |
| 10 | `BOOT_STAGE_KERNEL_MAIN` | Main kernel thread entry |
| 11 | `BOOT_STAGE_UNKNOWN` | Invalid/unset |

**Current MyOS M1 state**: Only `BOOT_STAGE_BOOTSTRAP` (stage 0) exists. Future stages are reserved for planned initialization sequence.

### Error Codes (`boot_error_t`)

32-bit packed format: `0xSSTTCCCC`

| Bits | Field | Values |
|------|-------|--------|
| 31:28 | Reserved | 0 |
| 27:24 | Subsystem | 0=CPU, 1=MEMORY, 2=UART, 3=TIMER, 4=USB, 5=DRIVER, 6=KERNEL, 7=GENERIC |
| 23:20 | Type | 0=FAILED, 1=TIMEOUT, 2=INVALID_STATE, 3=UNSUPPORTED_HW, 4=EXCEPTION, 5=INVALID_PARAM, 6=RESOURCE_EXH, 7=INVALID_TRANS |
| 19:0 | Subsystem-specific code | 0x0..0xFFFFF |

**Predefined error codes** (see `boot_diag.h` for complete list):
- `ERR_CPU_INIT_FAILED` — CPU initialization failed
- `ERR_CPU_INVALID_EL` — Entered at unexpected exception level
- `ERR_CPU_MIDR_READ_FAILED` — MIDR_EL1 read failed
- `ERR_MEM_INIT_FAILED` — DRAM initialization failed
- `ERR_MEM_INVALID_MAP` — Invalid memory map
- `ERR_MEM_VALIDATION_FAILED` — Memory validation failed
- `ERR_MMU_INIT_FAILED` — MMU initialization failed
- `ERR_MMU_PAGE_TABLE_FAILED` — Page table setup failed
- `ERR_MMU_ENABLE_FAILED` — MMU enable failed
- `ERR_UART_INIT_FAILED` — UART initialization failed
- `ERR_UART_TIMEOUT` — UART timeout
- `ERR_UART_INVALID_STATE` — UART invalid state
- `ERR_TIMER_INIT_FAILED` — Timer initialization failed
- `ERR_TIMER_TIMEOUT` — Timer timeout
- `ERR_TIMER_FREQ_ZERO` — Timer frequency unknown/zero
- `ERR_USB_INIT_FAILED` — USB initialization failed
- `ERR_USB_TIMEOUT` — USB timeout
- `ERR_USB_DEVICE_NOT_FOUND` — USB device not found
- `ERR_USB_CONTROL_XFER_FAILED` — USB control transfer failed
- `ERR_DRIVER_INIT_FAILED` — Driver initialization failed
- `ERR_DRIVER_UNSUPPORTED_HW` — Unsupported hardware
- `ERR_DRIVER_TIMEOUT` — Driver timeout
- `ERR_KERNEL_INIT_FAILED` — Kernel initialization failed
- `ERR_KERNEL_ALLOC_FAILED` — Kernel allocation failed
- `ERR_KERNEL_INVALID_STATE` — Kernel invalid state
- `ERR_GENERIC_FAILED` — Generic failure
- `ERR_GENERIC_TIMEOUT` — Generic timeout
- `ERR_GENERIC_INVALID_STATE` — Generic invalid state
- `ERR_GENERIC_EXCEPTION` — Unhandled exception
- `ERR_GENERIC_UNSUPPORTED` — Generic unsupported

### State Structure (`boot_diag_state_t`)

```c
typedef struct {
    uint32_t magic;                 // DIAG_STATE_MAGIC (0x44494147 "DIAG")
    uint32_t version;               // DIAG_STATE_VERSION (1)

    /* Stage tracking */
    boot_stage_t current_stage;     // Stage currently executing
    boot_stage_t last_ok_stage;     // Last stage that completed OK

    /* Failure tracking */
    boot_error_t last_error;        // Last error code (0 = none)
    uint32_t failure_flag;          // 1 = boot failed
    uint64_t timestamp;             // CNTPCT_EL0 at failure (if available)

    /* Exception context (captured when exception occurs) */
    uint64_t esr_el1;               // ESR_EL1 (exception syndrome)
    uint64_t far_el1;               // FAR_EL1 (fault address)
    uint64_t elr_el1;               // ELR_EL1 (exception link register)
    uint64_t spsr_el1;              // SPSR_EL1 (saved processor state)
    uint64_t elr_el2;               // ELR_EL2 (if entered from EL2)
    uint64_t spsr_el2;              // SPSR_EL2 (if entered from EL2)

    /* Bounded event buffer (ring buffer) */
    diag_event_t events[DIAG_MAX_EVENTS];
    uint32_t event_head;            // Next write index
    uint32_t event_count;           // Number of valid events
} boot_diag_state_t;
```

Located at `arch/aarch64/boot_diag.c:diag_state` (static, use `boot_diag_get_state()`).

### Event Buffer (`diag_event_t`)

Records a bounded history of diagnostic events (default 16 entries, ring buffer):

```c
typedef enum {
    DIAG_EVENT_STAGE_BEGIN = 0,
    DIAG_EVENT_STAGE_OK    = 1,
    DIAG_EVENT_STAGE_FAIL  = 2,
    DIAG_EVENT_PANIC       = 3,
    DIAG_EVENT_EXCEPTION   = 4,
} diag_event_type_t;

typedef struct {
    uint32_t type;          // diag_event_type_t
    boot_stage_t stage;     // Stage at time of event
    boot_error_t error;     // Error code (0 if not applicable)
    uint64_t timestamp;     // CNTPCT_EL0 if available
} diag_event_t;
```

API:
- `boot_diag_record_event(type, stage, error)` — Record event (internal)
- `boot_diag_get_event_count()` — Number of recorded events
- `boot_diag_get_event(index)` — Get event (0 = oldest)
- `boot_diag_clear_events()` — Clear buffer

### Console Abstraction (`struct boot_console`)

```c
typedef struct boot_console_ops {
    int (*init)(struct boot_console *);
    int (*putc)(struct boot_console *, char);
    int (*puts)(struct boot_console *, const char *);
    int (*write)(struct boot_console *, const void *, size_t);
    void (*flush)(struct boot_console *);
    void *platform_data;
} boot_console_ops_t;

typedef struct boot_console {
    const boot_console_ops_t *ops;
    void *platform_data;
} boot_console_t;
```

**Current MyOS state**: `boot_console = NULL`. UART driver calls `boot_console_register(&console)` when ready. Until then, diagnostic calls are no-ops (safe to call early).

### Framebuffer Renderer (`boot_fb_renderer_t`)

Optional. When framebuffer is available:

```c
typedef struct boot_fb_renderer {
    void (*init)(void *framebuffer, size_t width, size_t height, size_t stride);
    void (*render_report)(const boot_diag_state_t *);
    void (*render_panic)(const boot_diag_state_t *, const exception_context_t *);
} boot_fb_renderer_t;
```

Set via `boot_fb_register_renderer()`. Not available in M1.

### Exception Handling

#### Vector Table (`arch/aarch64/vectors.S`)

Complete AArch64 VBAR_EL1 vector table (2KB aligned) with handlers for:
- Synchronous exceptions (EL1t, EL1h, EL0_64, EL0_32)
- IRQ (all ELs)
- FIQ (all ELs)
- SError (all ELs)

Each handler:
1. Preserves caller-saved registers (x0-x18, x30)
2. Captures ESR_EL1, FAR_EL1, ELR_EL1, SPSR_EL1
2. Builds `exception_context_t` on stack
3. Calls `boot_diag_exception_handler()` (never returns)

#### Exception Context

```c
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
```

#### Exception Classes

```c
#define EXC_CLASS_SYNC    0x00
#define EXC_CLASS_IRQ     0x01
#define EXC_CLASS_FIQ     0x02
#define EXC_CLASS_SERROR  0x03
```

#### VBAR Installation

```c
void vbar_el1_install(void);  // Sets VBAR_EL1 to vbar_el1_table, executes ISB
```

### Fault Injection (Test Only)

Available when `MYOS_DIAG_FAULT_INJECTION` is defined at compile time:

```c
int boot_diag_inject_failure(boot_stage_t stage, boot_error_t error);
void boot_diag_clear_injected_failures(void);
int boot_diag_fault_injection_enabled(void);
```

- One-shot: each injected failure triggers once, then auto-clears
- Maximum 8 concurrent injections
- Disabled in production builds (static inline stubs return -1/0)
- Checked in `boot_diag_begin()` before normal stage start

## Integration Points

### 1. Assembly Entry (`boot/aarch64/m1_entry.S`)

```assembly
.global boot_diag_init
.global boot_diag_begin
.global boot_diag_ok

myos_m1_entry:
    bl      boot_diag_init
    mov     x0, #0              /* BOOT_STAGE_BOOTSTRAP */
    bl      boot_diag_begin

    /* ... existing DFU evidence code ... */

    mov     x0, #0
    bl      boot_diag_ok

    mvn     x0, x1
    ret
```

### 2. C Initialization (Future Stages)

```c
void myos_cpu_init(void) {
    boot_diag_begin(BOOT_STAGE_CPU_INIT);
    /* CPU setup... */
    boot_diag_ok(BOOT_STAGE_CPU_INIT);
}

void myos_memory_init(void) {
    boot_diag_begin(BOOT_STAGE_MEMORY_INIT);
    if (!dram_init()) {
        boot_diag_fail(BOOT_STAGE_MEMORY_INIT, ERR_MEM_INIT_FAILED);
    }
    boot_diag_ok(BOOT_STAGE_MEMORY_INIT);
}
```

### 3. Exception Handler Integration

```c
/* Called from vectors.S */
__attribute__((noreturn)) void boot_diag_exception_handler(
    boot_error_t base_error,
    uint32_t exc_class,
    boot_stage_t stage,
    const exception_context_t *ctx
);
```

## Output Formats

### UART / Serial Console (Authoritative)

```
[....] BOOTSTRAP - BEGIN
[  OK ] BOOTSTRAP
[....] CPU_INIT - BEGIN
[  OK ] CPU_INIT
[....] MEMORY_INIT - BEGIN
[FAIL] MEMORY_INIT

MyOS Boot Diagnostic
====================

[ OK ] BOOTSTRAP
[ OK ] CPU_INIT
[FAIL] MEMORY_INIT
[....] MMU_INIT
[....] EXCEPTION_INIT
[....] UART_INIT
[....] TIMER_INIT
[....] DRIVER_INIT
[....] USB_INIT
[....] KERNEL_INIT
[....] KERNEL_MAIN

Failure:
  Stage: MEMORY_INIT
  Error: MEMORY.INIT_FAILED.0x001
  Code: 0x00110001

BOOT FAILED
```

### Framebuffer (When Available)

- **Progress**: Animated stage list with color-coded status (green=OK, yellow=running, red=fail)
- **Panic**: Full-screen with error details, exception context, QR code for error lookup

### RAM State

The `boot_diag_state_t` structure in RAM. **Does NOT survive power cycles or hardware resets**. RAM contents are not preserved unless platform explicitly retains them. Can be read by:
- Host tool via DFU memory read (if RAM still powered)
- JTAG/SWD probe

## API Reference

### Initialization
```c
void boot_diag_init(void);
```
Must be called before any other diagnostic function. Zeroes state except magic/version.

### Stage Control
```c
int boot_diag_begin(boot_stage_t stage);   // Returns 0 on success, -1 on invalid stage
void boot_diag_ok(boot_stage_t stage);     // Marks stage complete
__attribute__((noreturn)) void boot_diag_fail(boot_stage_t stage, boot_error_t error);
__attribute__((noreturn)) void boot_diag_panic(boot_stage_t stage, boot_error_t error);
```

### Reporting
```c
const boot_diag_state_t *boot_diag_get_state(void);
void boot_diag_print_report(void);
void boot_diag_print_error(boot_error_t error);
void boot_diag_print_stage(boot_stage_t stage);
```

### Event Buffer
```c
uint32_t boot_diag_get_event_count(void);
const diag_event_t *boot_diag_get_event(uint32_t index);
void boot_diag_clear_events(void);
```

### Exception Handling
```c
void vbar_el1_install(void);
__attribute__((noreturn)) void boot_diag_exception_handler(
    boot_error_t base_error,
    uint32_t exc_class,
    boot_stage_t stage,
    const exception_context_t *ctx
);
```

### Fault Injection (Test Only)
```c
#ifdef MYOS_DIAG_FAULT_INJECTION
int boot_diag_inject_failure(boot_stage_t stage, boot_error_t error);
void boot_diag_clear_injected_failures(void);
int boot_diag_fault_injection_enabled(void);
#endif
```

### Formatting Utilities
```c
size_t boot_diag_format_hex32(char *buf, size_t bufsz, uint32_t val);
size_t boot_diag_format_hex64(char *buf, size_t bufsz, uint64_t val);
size_t boot_diag_format_error(char *buf, size_t bufsz, boot_error_t error);
int boot_diag_state_valid(const boot_diag_state_t *state);
```

### Platform Registration
```c
void boot_console_register(struct boot_console *console);
void boot_fb_register_renderer(const boot_fb_renderer_t *renderer);
```

## Building & Testing

### Build
```bash
make clean && make
```

### Offline Tests
```bash
./tests/offline_m1.sh          # M1 payload verification
./tests/test_boot_diag.sh      # Diagnostic framework verification
./tests/test_boot_diag_extended.sh  # Extended features (events, faults, exceptions)
```

M1 offline test verifies:
- ELF is 64-bit AArch64
- Entry point at 0x180380050
- No undefined symbols
- Diagnostic symbols present
- Marker "MYOSM1OK" present
- Payload size ≤ 2016 bytes (DFU budget)

Extended test verifies:
- Event buffer API symbols
- Fault injection API (static inline stubs)
- Exception handler symbols
- Vector table alignment (2KB)
- No heap allocation symbols
- Diagnostic state size

### Physical Testing (Debian 13 laptop)
```bash
# Requires checkm8 exploit + pwned DFU
sudo ./tools/host/m1ctl payload.bin
```

On failure, diagnostic output appears on UART (if connected) or can be read from DFU buffer.

## Adding New Stages

1. Add enum value in `boot_diag.h` (before `BOOT_STAGE_UNKNOWN`)
2. Add name to `boot_stage_names[]` in `boot_diag.c` (and `boot_diag_m1.c` if needed)
3. Call `boot_diag_begin()` at stage entry
4. Call `boot_diag_ok()` on success
5. Call `boot_diag_fail()` on failure with appropriate error code

## Adding New Error Codes

1. Define in `boot_diag.h` with `ERR_<SUBSYS>_<REASON>` naming
2. Use subsystem bits 27:24, type bits 23:20, code bits 19:0
3. `boot_diag_format_error()` auto-decodes subsystem and type names

## Fault Injection Testing

```c
/* In test code, compile with -DMYOS_DIAG_FAULT_INJECTION */
boot_diag_inject_failure(BOOT_STAGE_MEMORY_INIT, ERR_MEM_INIT_FAILED);
boot_diag_begin(BOOT_STAGE_MEMORY_INIT);  // Will trigger injected failure
// boot_diag_fail called automatically with injected error
```

## Constraints & Invariants

| Constraint | Enforcement |
|------------|-------------|
| No heap allocation | Static `diag_state`, no `malloc` |
| No libc | Only uses compiler builtins (`asm`, `__builtin_*`) |
| Freestanding | `-ffreestanding -nostdlib` compatible |
| Deterministic | No randomness, no external dependencies |
| Interrupt-safe | No locks; console ops must be atomic or IRQ-disabled |
| EL1/EL2/EL3 safe | Only uses architecturally-defined registers |
| Bounded buffers | Event buffer: 16 entries, Fault injection: 8 entries |
| No RAM persistence | State does NOT survive reset/power loss |

## Implemented vs Pending

| Feature | Status |
|---------|--------|
| Stage tracking (begin/ok/fail) | ✅ Implemented |
| Error code system | ✅ Implemented |
| Event buffer (ring, 16 entries) | ✅ Implemented |
| Fault injection (test-only) | ✅ Implemented |
| Exception context capture | ✅ Implemented |
| AArch64 vector table (VBAR_EL1) | ✅ Implemented (`vectors.S`) |
| Exception classes (sync/irq/fiq/serror) | ✅ Implemented |
| VBAR installation helper | ✅ Implemented |
| UART/console driver | ❌ Pending (no hardware) |
| Framebuffer renderer | ❌ Pending (no hardware) |
| Physical iPhone 6 validation | ❌ Pending (xHCI limitation) |
| QEMU-virt test path | ❌ Not configured |

## M1 Payload Budget

| Component | Size |
|-----------|------|
| `m1_entry.o` | 128 bytes |
| `boot_diag_m1.o` | ~500 bytes |
| **Total payload** | **~1240 bytes** (budget: 2016 bytes) |

Full diagnostic (`boot_diag.c` + `vectors.S`) is ~4KB and intended for M2+ builds.

---

**Related Documents**:
- `ARCHITECTURE.md` — Boot chain overview, delivery contract
- `M1_PLAN.md` — M1 milestone specification
- `D3_DIAGNOSTICS.md` — Hardware diagnosis (xHCI limitation)
- `vectors_defs.h` — Assembly-compatible constants for vector table