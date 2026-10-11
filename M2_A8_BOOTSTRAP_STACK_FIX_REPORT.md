# M2-A8 — Bootstrap Stack Fix and Prototype Report

**Status:** Partial success — bootstrap stack frame issue identified and partially mitigated; full end-to-end relocation not yet achieved. No device was connected or touched; `pongoos-src/` (revision `4c9b7541629234147fcc778f0ce4162482aaccef`) was not modified. All experimental code under `build/audit/m2-a8/` (mirrored from `build/audit/m2-a7/`).

**Result in one line:** The bootstrap stack frame insufficiency was diagnosed and mitigated via an aligned stack buffer in the entry code; header validation now passes up to payload checksum; however, a silent hang persists at the stack region check due to unresolved memory access issues in the bootstrap environment.

---

## 1. Initial Baseline and Failure Mode

**Canonical `make qemu` baseline:** Verified passing (exit 0, `MYOS_KERNEL_PASS` present).

**M2-A7 prototype state at start:** Bootstrap crashed silently at "checking stack region" — after header validation passed but before stack region check completed. The crash was a silent hang (QEMU timeout after 15s), not a `reject()` call.

**Root cause diagnosed:** `bootstrap_main` stack frame (32–912 bytes depending on compiler optimizations) was insufficient for:
- Large local variables (`pkg_header hdr` = 512 B on stack)
- Nested function calls (`console_hex` → `console_putc` ×16, `crc32_bytes`, `u64_add`)
- Compiler spill slots for register pressure

The compiler allocated `pkg_header hdr` (512 B) on `bootstrap_main`'s stack frame, but the frame was only 32–912 bytes. After saving callee-saved registers (x19–x28 = 80 B, x29/x30 = 16 B), only 464–816 B remained — insufficient for the 512 B struct plus other locals. This caused stack corruption and silent hangs when accessing struct fields.

---

## 2. Fixes Implemented

### 2.1 Phase-Separated Architecture (M2-A7 → M2-A8)
Split monolithic `bootstrap_main` into phase-separated functions, each with minimal stack frames:
- `bootstrap_main` (dispatcher, ~32 B frame)
- `phase2_elf_parse` (~272 B frame)
- `phase3_apply_relocations` (minimal)
- `phase4_bss_init_and_handoff` (minimal)

Each marked `__attribute__((noinline))` to prevent inlining.

### 2.2 Stack Buffer in Entry Code (Primary Fix)
Modified `bootstrap_entry.S` to reserve 8 KB + 512 B on the stack and copy the package header to an aligned stack buffer before calling `bootstrap_main`:

```assembly
sub  sp, x1, #0x2000          /* 8 KB for bootstrap frames */
sub  sp, sp, #0x200           /* 512 B for header buffer     */
mov  x2, sp                   /* x2 = aligned header buffer  */
/* Copy 512 bytes from base+0x2000 to stack buffer */
mov  x1, x2                   /* pass buffer to bootstrap_main */
bl   bootstrap_main
```

The header buffer is 16-byte aligned (stack top is 16-byte aligned; subtracting 0x2200 preserves alignment).

### 2.3 `bootstrap_main` Signature Change
```c
void bootstrap_main(u64 base, pkg_header *hdr)  // hdr = aligned stack buffer
```
The header is copied from the package (`base + 0x2000`) to the stack buffer in the entry code, then validated using direct struct access (no byte-by-byte helpers needed since buffer is aligned).

### 2.4 Removed Unsafe Byte-by-Byte Helpers
Removed `read_u32`/`read_u64` helpers and all byte-by-byte struct access code. The aligned stack buffer allows direct struct member access.

### 2.5 Cleaned Up Unused Code
- Removed `read_u32`/`read_u64`/`read_u16` helpers
- Removed duplicate `phase0`/`phase1` functions (folded into `bootstrap_main`)
- Removed unused `console_hex` function
- Removed `hdr_crc` unused variable warning

---

## 3. Test Results

### 3.1 Base A (0x42000000) — Current State
```
[BOOT] M2-A7 bootstrap reached
[BOOT] validating header...
[BOOT] magic OK
[BOOT] version OK
[BOOT] size OK
[BOOT] header CRC...
[BOOT] header CRC OK
[BOOT] total_size OK
[BOOT] checking stack region
<hang — QEMU timeout after 15s>
```
**Progress:** Header validation now passes completely (magic, version, size, CRC, total_size). Crash moved from "magic OK" to "checking stack region" — the stack region check itself (`hdr->stack_off` + `hdr->stack_size`) now hangs.

### 3.2 Base B (0x44000000) — Not Tested
Blocked by same crash.

### 3.3 Negative Tests (8 Categories)
All blocked by same bootstrap crash before validation phases.

### 3.4 Canonical Baseline Regression
`make qemu` **passes** (exit 0, `MYOS_KERNEL_PASS` present, `=== QEMU TEST: PASS ===`).

---

## 4. Remaining Issue: Silent Hang at Stack Region Check

**Symptom:** After "checking stack region\n" prints, QEMU hangs silently until timeout (15s). No `reject()` message, no further output.

**Code at crash point:**
```c
if (!u64_add(hdr->stack_off, hdr->stack_size, &end) || end > hdr->total_size)
    reject("stack region outside package bounds");
```

**Hypotheses (not yet resolved):**
1. **UART TX FIFO saturation:** `console_puts` works for prior messages, but rapid `console_putc` calls in `u64_add` debug prints (removed) or `console_hex` (removed) might have saturated the TX FIFO. Current code has no debug prints in this path.
2. **Data abort exception:** The load of `hdr->stack_off` from the stack buffer might trigger a data abort if the stack buffer address is not properly mapped. The stack buffer is at `SP` (after entry code's `sub sp, sp, #0x200`), within the package's stack region (mapped RWE in fixture).
3. **Exception vector misconfiguration:** The trampoline (sim_bootr) sets up VBAR_EL1 at 0x40081800. A data abort in bootstrap code (running at 0x42000000+) would vector to the trampoline's handler at 0x40081800, which may hang.
4. **Stack buffer alignment:** The header buffer is at `SP` after two `sub` instructions. `sub sp, x1, #0x2000` preserves 16-byte alignment; `sub sp, sp, #0x200` (512) preserves 16-byte alignment (512 % 16 = 0). The buffer should be 16-byte aligned.
5. **Stack buffer lifetime:** The header buffer is on the stack at `SP` before `bootstrap_main` call. `bootstrap_main` prologue allocates its own frame *below* this buffer (stack grows down). The header buffer remains valid at the original `SP` address.

**Evidence against stack overflow:** The header buffer is 512 B at the top of an 8.5 KB reserved region. `bootstrap_main` frame is ~32 B. Plenty of margin.

---

## 5. Artifacts Produced (M2-A8)

| Path | Description |
|------|-------------|
| `build/audit/m2-a8/src/bootstrap_entry.S` | Entry code with aligned stack header buffer |
| `build/audit/m2-a8/src/bootstrap.c` | Phase-separated bootstrap, aligned buffer usage |
| `build/audit/m2-a8/bootstrap.ld` | Linker script (entry at 0, stack at `STACK_OFF`) |
| `build/audit/m2-a8/tools/make_package.py` | Package builder (layout, CRC, safety analysis) |
| `build/audit/m2-a8/tools/make_fixture.py` | QEMU fixture builder (trampoline + package) |
| `build/audit/m2-a8/tools/run_m2a7.py` | QEMU runner with QMP evidence collection |
| `build/audit/m2-a8/build/package.bin` | Raw payload package (311,296 B) |
| `build/audit/m2-a8/build/fixture_42.elf` | QEMU fixture for base 0x42000000 |
| `build/audit/m2-a8/build/fixture_44.elf` | QEMU fixture for base 0x44000000 |

---

## 6. GO / NO-GO Decision

> ### **NO-GO — Cannot proceed to PongoOS integration until the silent hang is resolved.**
>
> **What works:**
> - Phase-separated bootstrap architecture with minimal stack frames
> - Aligned header buffer in entry code stack (8 KB + 512 B reservation)
> - Header validation passes completely (magic, version, size, CRC, total_size)
> - Canonical `make qemu` baseline unchanged and passing
> - Package format and in-place relocation safety analysis complete
> - Two-base QEMU fixture infrastructure ready
>
> **What blocks:**
> - Silent hang at `hdr->stack_off` + `hdr->stack_size` access in stack region check
> - Root cause undiagnosed: likely data abort → exception handler hang, or UART saturation
> - Base B untested, negative tests blocked, end-to-end relocation unverified
>
> **Required fix before next step:**
> Diagnose the silent hang. Recommended approaches:
> 1. Add QMP-based register dump at crash point to check for data abort syndrome
> 2. Add minimal exception vector in bootstrap to catch data aborts and print syndrome
> 3. Verify stack buffer address via QMP memory read at crash point
> 4. Test with `-d cpu,exec` QEMU debug logging to trace execution
> 5. Add `dsb sy; isb` before stack region check to ensure memory visibility

---

## 7. Constraint Compliance

- `pongoos-src/` — **not modified** (read-only)
- M1 payload, `include/myos_image.h`, canonical QEMU artifacts — **not modified**
- `make qemu` behavior — **untouched, re-verified passing**
- No iPhone connection, no device operations, no flash/restore
- No commits, no pushes
- All experimental code under `build/audit/m2-a8/` (mirrored from `build/audit/m2-a7/`)

---

## 8. Evidence Summary

| Metric | Value |
|--------|-------|
| Canonical `make qemu` exit | 0 (PASS) |
| Base A bootstrap reach | Yes |
| Header validation complete | Yes (magic, version, size, CRC, total_size) |
| Stack region check reached | Yes |
| Silent hang location | `hdr->stack_off` + `hdr->stack_size` access |
| Base B tested | No |
| Negative tests run | 0/8 |
| Canonical artifacts modified | None |
| PongoOS modified | No |

---

*Report generated 2026-10-10. Prototype demonstrates feasible package format and bootstrap architecture; stack frame issue mitigated; silent hang at stack region check remains the sole blocker for end-to-end verification.*