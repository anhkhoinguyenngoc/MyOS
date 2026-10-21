# PLATFORM_INTERFACE.md — QEMU/iPhone-6 platform boundary (M6)

Date: 2026-10-18
Status: **QEMU `virt` implemented and validated; Apple A8 (iPhone 6) declared
UNSUPPORTED with an explicit contract.**
Scope: offline. No iPhone connected; no checkm8/DFU/USB/payload execution.

This document is the architecture artifact for milestone M6. It (1) separates
the QEMU-specific boot path from target-independent kernel logic, (2) records
the evidence for the future iPhone 6/A8 target while distinguishing confirmed
facts from inferences and unknowns, and (3) defines the platform interface and
the build-time rules that keep QEMU-only assumptions out of a future physical
target.

---

## 1. What is confirmed, inferred, and unknown

### 1.1 Confirmed facts (primary evidence)

| Fact | Evidence |
|---|---|
| SoC is T7000 / Apple A8; device is `n61ap` (iPhone 6) | This project's own host read the device DFU descriptor `CPID:7000 … SRTG:[iBoot-1992.0.0.1.19]` — `BOOT_FEASIBILITY.md §0` |
| SecureROM build `iBoot-1992.0.0.1.19` is vulnerable to checkm8 (CVE-2019-8900, covers A5–A11) | `BOOT_FEASIBILITY.md §0, §5`; CERT VU#941987 |
| The intended MyOS carrier handoff (PongoOS `bootr` RAW path) is source-audited to enter at **EL1 with MMU off**, passing `x0 = boot_args`, `x1..x3 = 0`, `x8 = entry`; the raw target is upload-buffer offset 0 through the physical alias | `M2_A10_RUNTIME_HANDOFF_AUDIT.md §3–4` (static source audit of the pinned PongoOS, not a hardware capture) |
| QEMU `virt` provides a PL011 UART at `0x09000000` and loads `-kernel` at `0x40080000` | `platform/qemu-virt/qemu_virt_defs.h` (single definition); guarded `platform/qemu-virt/start.S`; `platform/qemu-virt/kernel.ld`; QEMU `virt` documentation. **QEMU-only.** |
| The M1 DFU payload entry/ABI on T7000 (`0x180380000` + 0x50, x0/x1/x2..x7) is source-derived from the vendored gaster | `M1_PLAN.md §3–§4`; `platform/apple/a8/m1_a8.h` |

### 1.2 Inferences (strong secondary evidence, not measured by us)

- The A8 inherits BootROM-initialized DRAM, so a payload runs from DRAM without
  MyOS doing DRAM init (`BOOT_FEASIBILITY.md §5`, matrix row "RAM
  initialization" = LIKELY).
- Carrier-owned USB is the only documented post-pwn output channel
  (`ARCHITECTURE.md §6`); a MyOS-owned console does not exist.
- The ARM generic timer (`CNTPCT_EL0` / `CNTFRQ_EL0`) is architectural and
  available without SoC documentation (`ARCHITECTURE.md §4`).

### 1.3 Unknowns (must not be guessed)

- Physical DRAM base/size and the handoff memory map for T7000
  (`BOOT_FEASIBILITY.md §5`, matrix row "Exact physical memory map" = UNKNOWN).
- Any real iPhone 6 console address: no public UART pinout
  (`BOOT_FEASIBILITY.md §2`).
- Apple AIC interrupt-controller and timer register maps for T7000
  (`BOOT_FEASIBILITY.md §2`).
- Display initialization prerequisites (a display path exists in iBoot, but its
  registers are undocumented — `ARCHITECTURE.md §6`, matrix row "Display").
- Whether the inherited PongoOS vector table is usable after MMU-off, and the
  runtime `boot_args` view/contents (`M2_A10_RUNTIME_HANDOFF_AUDIT.md §4`).

**No address or memory range for the iPhone 6 is asserted anywhere in this
milestone.** The A8 descriptor uses `MYOS_ADDR_UNKNOWN` for every hardware
field.

---

## 2. The platform interface

Defined in `platform/platform.h`:

```c
typedef struct {
    myos_platform_id_t      id;
    const char             *name;
    myos_platform_status_t  status;        /* KNOWN_GOOD | UNSUPPORTED */
    uint32_t                capabilities;  /* MYOS_CAP_* bits          */
    uint64_t                uart_mmio_base;/* or MYOS_ADDR_UNKNOWN     */
    uint64_t                link_load_addr;/* or MYOS_ADDR_UNKNOWN     */
    const char             *fact_source;   /* provenance of the values */
    const char *const      *unresolved;    /* NULL-terminated, or NULL */
} myos_platform_descriptor_t;
```

- `myos_platform_current()` (in `platform/platform.c`) returns the
  build-selected descriptor.
- `MYOS_ADDR_UNKNOWN` (0) is the explicit "no address asserted" sentinel. It is
  never a valid value for a bootable platform's `uart_mmio_base` or
  `link_load_addr`, so it cannot be dereferenced by accident.
- `unresolved` carries the open questions for an UNSUPPORTED platform, so the
  gap between "declared" and "supported" is machine-visible.

Two descriptors exist:

| Descriptor | File | Status | Capabilities | UART base | Load address |
|---|---|---|---|---|---|
| `myos_platform_qemu_virt` | `platform/qemu-virt/qemu_virt.c` | `KNOWN_GOOD` | `MYOS_CAP_UART_PL011` | `0x09000000` | `0x40080000` |
| `myos_platform_apple_a8` | `platform/apple/a8/iphone6.c` | `UNSUPPORTED` | none | `MYOS_ADDR_UNKNOWN` | `MYOS_ADDR_UNKNOWN` |

Each QEMU-specific value is defined exactly once, in
`platform/qemu-virt/qemu_virt_defs.h`. The descriptor header includes it, and
the early boot stub and PL011 driver include it too, so the descriptor and the
boot path share one definition instead of repeating the number. The host test
`tests/test_platform_boundary.py` cross-checks the definition against
`platform/qemu-virt/kernel.ld` (linker scripts are not preprocessed and so must
restate the load address), so the two cannot drift apart silently.

---

## 3. Build-time boundary (how QEMU assumptions are kept out)

The Makefile selects a platform explicitly:

```
PLATFORM ?= qemu-virt            # the only implemented platform
```

- `PLATFORM=qemu-virt` ⇒ `-DMYOS_PLATFORM_QEMU_VIRT=1`; the kernel links
  `platform/platform.c` + `platform/qemu-virt/qemu_virt.c`.
- `PLATFORM=apple-a8` ⇒ **`$(error …)` and the build stops.** There is no
  "iphone" target that merely renames QEMU code.
- `platform/platform.c` additionally `#error`s for `MYOS_PLATFORM_APPLE_A8`,
  so bypassing the Makefile does not produce an A8 kernel either.
- The QEMU-only sources refuse to assemble for any other platform:
  `platform/qemu-virt/start.S` and `platform/qemu-virt/uart_pl011.S` contain
  `#ifndef MYOS_PLATFORM_QEMU_VIRT` → `#error`.
- `platform/apple/a8/iphone6.c` is **not** part of the kernel link, so the
  A8 descriptor and its requirement strings never enter the QEMU image
  (asserted by the host test against `build/myos_kernel.elf`).

### Requirements to move apple-a8 out of UNSUPPORTED

Represented in `platform/apple/a8/iphone6.c:apple_a8_unresolved[]` and here.
Each must be resolved with evidence before the `#error` gate is removed:

1. **Kernel entry / handoff** — capture the real register, exception-level, and
   MMU state at the intended entry on hardware (the PongoOS `bootr` RAW path is
   source-audited to EL1/MMU-off but not yet captured); fix an entry ABI
   (argument registers, stack ownership, reserved memory) with the chosen
   carrier.
2. **Memory** — measure the physical DRAM base/size and the handoff map; pin
   them as sourced constants, never assume them.
3. **Console** — a MyOS-owned diagnostic output path (no public UART pinout;
   carrier USB is not owned by MyOS).
4. **Interrupts / timer** — T7000 AIC and timer access (the ARM generic timer is
   architectural and available; the AIC is undocumented).
5. **Display** — display initialization prerequisites before any framebuffer
   work (iBoot draws a logo; registers undocumented).

---

## 4. Separation rules (enforced / documented)

The source tree is organized by ownership domain (M6.3). Each directory has one
owner and one responsibility, and dependencies point one way:

```
core         kernel/                     main entry, target-independent boot diagnostics
architecture arch/aarch64/                vectors.S + vectors_defs.h (no machine address)
platform     platform/qemu-virt/          QEMU `virt` descriptor, boot stub, PL011 driver, linker script
             platform/apple/a8/           Apple A8 descriptor (UNSUPPORTED) + M1 payload/probe artifacts
             platform/platform.{c,h}      the platform descriptor contract and build-time selection
integration  Makefile                     platform selection + controlled assembly of approved parts
host tools   tools/host/                   host-only parsers/codecs; never linked into the kernel
verification tests/, include/             regression tests and shared ABI headers
```

- Target-independent kernel logic: `kernel/` (`main.c`, `boot_diag.*`) and
  `arch/aarch64/vectors.*` — no MMIO of their own; they consume a registered
  `boot_console` and contain no machine-specific address.
- QEMU `virt` boot + linker: `platform/qemu-virt/start.S`,
  `platform/qemu-virt/kernel.ld` (guarded).
- QEMU PL011 UART: `platform/qemu-virt/uart_pl011.S` (guarded), sharing
  `platform/qemu-virt/qemu_virt_defs.h` with the descriptor.
- Apple A8: `platform/apple/a8/` holds the UNSUPPORTED descriptor plus the
  firmware-level artifacts (`m1_entry.S`, `m1.ld`, `pongo_probe.S`,
  `pongo_probe.ld`, `boot_diag_m1.c`). None of it is linked into the QEMU
  kernel.
- Platform-dependent memory/interrupt/timer/early-console init: **does not
  exist for A8.** QEMU's minimal early console is the only implementation.
- Host-side diagnostics/tests: `tests/`, `tools/host/` (never linked into the
  kernel).

`tests/test_domain_boundaries.py` enforces these rules offline (no core/arch
machine address, no platform include from core, no host tool or A8 object in the
kernel link, no stale `boot/aarch64` build reference).

Per `ARCHITECTURE.md §8`, a green QEMU run is laboratory evidence only and says
nothing about iPhone 6 behavior. This milestone does not change that.

---

## 5. Validation

- `make host-test-all` — includes `tests/test_platform_boundary.py`, which
  compiles and runs both descriptors with the host compiler and asserts the
  invariants above.
- `make offline` — M1 payload checks; unaffected.
- `make qemu` — QEMU kernel still boots and prints `MYOS_KERNEL_PASS`
  (now also `Platform: qemu-virt [known-good]`).
- Negative controls: `make PLATFORM=apple-a8` fails; assembling the QEMU-only
  sources without the platform define fails; compiling `platform.c` with
  `MYOS_PLATFORM_APPLE_A8` fails.

Physical-device compatibility remains **UNKNOWN / UNVERIFIED**.
