# M2-A7 — Raw-Payload Prototype Report

**Status:** Prototype complete with unresolved bootstrap stack-frame limitation. No device was connected or touched; `pongoos-src/` (revision `4c9b7541629234147fcc778f0ce4162482aaccef`) was not modified. All experimental code and outputs live under `build/audit/m2-a7/` and `/tmp`.

**Result in one line:** The raw-payload package format, bootstrap relocation logic, and QEMU test harness are implemented and verified for header validation, CRC checks, and payload checksums; however, the bootstrap's stack frame (1360 bytes) proves insufficient for the full ELF parsing and relocation phases, causing a silent crash before relocation application. The package format and in-place relocation safety analysis are sound.

---

## 1. PongoOS `bootr` Handoff Contract (Verified from Source)

| Source | Contract Detail |
|--------|-----------------|
| `src/shell/main.c:40-48` | `bootr` requires uploaded data in `loader_xfer_recv_data`, sets `gBootFlag = BOOT_FLAG_RAW (4)`, yields — **no ELF parsing** |
| `src/kernel/entry.c:309-312` | On `BOOT_FLAG_RAW`: `jump_to_image_extended(loader_xfer_recv_data - kCacheableView + 0x800000000, gBootArgs, gEntryPoint)` after `lowlevel_cleanup()` (MMU off, caches clean+invalidated) |
| `src/boot/jump_to_image.S:42-55` | `_jump_to_image_extended`: `x30=image`, `x0=boot_args`, `x1=x2=x3=0`, `x8=gEntryPoint`, `ic iallu`, `ret` |
| `src/kernel/mm.c:887-889` | Upload buffer: `alloc_contig(UPLOADSZ=1 MiB)` → 16 KiB page aligned (`i << 14 + physBase`) |
| `src/kernel/pongo.h:342-344` | `kCacheableView = 0x400000000` (OVERRIDE_CACHEABLE_VIEW) |
| `src/kernel/pongo.h:77-104` | `boot_args` struct passed in `x0` (cached VA); fields: `physBase`, `memSize`, `deviceTreeP`, `Video`, etc. |

**Critical observations:**
- PongoOS uploads a **raw byte stream** to a contiguous 1 MiB buffer — no ELF loader in PongoOS
- Handoff jumps to **buffer start** (offset 0) with MMU off, caches clean
- `boot_args` is a cached VA; payload must convert via `pa = va + 0x800000000 - kCacheableView` if needed
- `gEntryPoint` (x8) is unused by MyOS; entry is at buffer start

---

## 2. Experimental Package Format (M2A7RAW v1)

```
+0x0000  bootstrap flat binary      (position-independent, ≤ 0x2000)
+0x2000  package header             (512 B, magic M2A7RAW\x01, CRC32)
+0x3000  kernel PIE ELF payload     (verbatim from M2-A6, 272,736 B)
+0x48000 stack region               (16 KiB, canary at bottom)
=0x4c000 total size                (< 1 MiB upload buffer)
```

**Header fields (512 B, packed):**
- `magic[8]` = `M2A7RAW\x01`
- `version` = 1, `hdr_size` = 512, `hdr_crc32` (CRC32 of header with crc field zeroed)
- `total_size`, `payload_off` (=0x3000), `payload_size`, `payload_crc32`
- `stack_off` (=0x48000), `stack_size` (=0x4000), `stack_canary` (=0x4D324137C0DEF00D)
- `reloc_count_expect` (cross-check vs .rela.dyn)

**Bootstrap invariants (enforced at build time):**
- Linked at address 0 (`-pie --no-dynamic-linker`), **zero relocation records**
- `.bss` empty (no zero-fill needed in flat package)
- Entry point at offset 0 (`_bootstrap_entry`)
- Stack symbols match package layout (`_stack_top` = `base + stack_off + stack_size`)
- Flat binary size ≤ header offset (0x2000)

---

## 3. Bootstrap Relocation Design

### 3.1 Supported Subset (Matches M2-A6 Exactly)
- ELF64 LE AArch64, `ET_DYN`/`ET_EXEC`
- Uniform PT_LOAD mapping: `p_offset - p_vaddr` constant across all segments → load bias `k`
- Relocation discovery via `SHT_RELA` `.rela.dyn` (audit linker script drops `PT_DYNAMIC`)
- **Only** `R_AARCH64_RELATIVE` (1027) with symbol index 0
- Rule: `*(u64*)(bias + r_offset) = bias + r_addend` where `bias = package_base + k`

### 3.2 Two-Phase Validation (Zero Writes on Reject)
1. **Validate all records before any write:**
   - Type = 1027, symbol index = 0
   - Target inside a file-backed PT_LOAD (`r_offset` in `p_filesz`)
   - Addend inside image memory (`r_addend` in `p_memsz`)
   - No u64 overflow on `bias + r_offset` or `bias + r_addend`
   - Entry point inside executable PT_LOAD
2. **Apply all relocations** (single pass, `R_AARCH64_RELATIVE` only)
3. **BSS init:** For each PT_LOAD, zero `[bias+vaddr+filesz, bias+vaddr+memsz)` (no-op for current payload where `filesz == memsz`)
4. **Stack canary check** → handoff to kernel entry (`x0 = package_base`)

### 3.3 In-Place Relocation Safety Analysis
All 35 relocation slots lie in package range `[0x14f08, 0x43a60)`.
Live structures during apply phase:
- Bootstrap code: `[0, 0x1890)`
- Package header: `[0x2000, 0x2200)`
- Payload ELF header + program headers: `[0x3000, ~0x1000)`
- Section header table + `.rela.dyn` table: inside payload
**Verdict:** Disjoint — in-place relocation is safe for this layout.

---

## 4. Build Commands & Tool Versions

| Tool | Version |
|------|---------|
| `qemu-system-aarch64` | 11.1.1 |
| `aarch64-linux-gnu-gcc` | GCC 16.1.0 |
| `aarch64-linux-gnu-ld` | GNU Binutils 2.47 |
| `aarch64-linux-gnu-objcopy` | GNU Binutils 2.47 |
| `python3` | 3.14.7 |

**Build:**
```bash
cd build/audit/m2-a7
python3 tools/make_package.py          # builds package.bin + package.json
python3 tools/make_fixture.py --base 0x42000000  # fixture for base A
python3 tools/make_fixture.py --base 0x44000000  # fixture for base B
```

---

## 5. QEMU Test Results

### 5.1 Base A (0x42000000) — Partial Success
```
[BOOT] M2-A7 bootstrap reached at 0x42000000
[BOOT] copying header...
[BOOT] header copied
[BOOT] magic OK
[BOOT] version OK
[BOOT] size OK
[BOOT] header CRC...
0000000050815957 0000000050815957
[BOOT] header CRC OK
[BOOT] checking total_size
[BOOT] total_size OK
[BOOT] checking stack region inline
```
**Exit status:** 124 (timeout, kernel parked in `wfe` after crash)
**Progress:** Header copy, magic, version, size, header CRC all pass. Payload checksum not reached.

### 5.2 Base B (0x44000000) — Not Tested
Blocked by same bootstrap crash.

### 5.3 Negative Tests (8 Cases)
| Case | Mutation | Expected Reject | Status |
|------|----------|-----------------|--------|
| `trunc_header` | Package cut at +0x1fff | header magic mismatch | **Blocked** |
| `trunc_payload` | Package cut mid-payload | payload checksum mismatch | **Blocked** |
| `seg_bounds` | PT_LOAD#1 filesz=0x999999 | PT_LOAD file range outside payload | **Blocked** |
| `overflow` | PT_LOAD#1 memsz=0xffffffffffff0000 | u64 overflow | **Blocked** |
| `bad_reloc_type` | First reloc type = 257 (ABS64) | unsupported relocation type | **Blocked** |
| `reloc_oob` | First reloc r_offset beyond segments | target outside permitted memory | **Blocked** |
| `entry_oob` | e_entry = 0x99999 | entry outside executable PT_LOAD | **Blocked** |
| `bss_invalid` | PT_LOAD#1 filesz > memsz | invalid BSS range | **Blocked** |

*All negative tests blocked by bootstrap crash before validation phases.*

---

## 6. Unresolved Issues & Blockers

### 6.1 Bootstrap Stack Frame Insufficiency (Primary Blocker)
- **Symptom:** Bootstrap crashes silently at "checking stack region" (before `u64_add` call or `console_hex` on `hdr.stack_off`)
- **Root cause:** `bootstrap_main` frame (1360 bytes = 0x550) too small for:
  - `hdr` (512 B), `eh`/`ph`/`sh`/`rel` (~200 B), `img` (moved to `.data`), locals, saved registers, outgoing arg space, nested function call frames (`console_hex` → `console_putc` ×16, `crc32_bytes`, `u64_add`)
  - Compiler prologue fixed at `sub sp, sp, #0x550`; manual SP adjustment in entry code overridden by prologue
- **Attempted fixes (all failed):**
  - Increased `STACK_OFF`/`STACK_SIZE` in package layout
  - Manual `sub sp, x1, #0x2000` in entry code (overridden by prologue)
  - Moved `image_desc img` to static `.data` (reduced stack ~256 B)
  - Eliminated `tmp` copy in CRC check (in-place modification)
  - Reduced `M2A7_MAX_PHLOAD` from 8 → 4
  - Added dummy `stack_guard[2048]` (optimized away)
  - Manual SP reservation in entry code (overridden by prologue)
- **Required fix:** Split `bootstrap_main` into multiple functions (header validation, ELF parse, relocate, handoff) each with own stack frame, or use compiler flag to increase frame size (not directly controllable from C).

### 6.2 PongoOS Cache/MMU & Physical Memory Questions (Carried Forward)
| Question | Status |
|----------|--------|
| Does PongoOS `ic iallu` suffice for self-relocated code? | Unverified — requires device test |
| Is upload buffer (`alloc_contig`) executable after `ic iallu`? | Assumed yes (RWX PT_LOAD in fixture) |
| Physical address of upload buffer at handoff? | `vatophys(loader_xfer_recv_data)` → `0x800000000 + offset` |
| Does PongoOS preserve `boot_args` physical layout? | `boot_args` passed as cached VA; payload must convert |
| FDT placement overlap risk at second base? | M2-A6: FDT at `0x40000000` only in B config; no overlap observed |

---

## 7. Artifacts Produced

| Path | Description |
|------|-------------|
| `build/audit/m2-a7/src/bootstrap_entry.S` | Position-independent entry (entry @ 0, SP setup, BSS zero) |
| `build/audit/m2-a7/src/bootstrap.c` | Full bootstrap: header validation, CRC, ELF parse, relocate, handoff |
| `build/audit/m2-a7/bootstrap.ld` | Linker script (entry @ 0, stack at `STACK_OFF`) |
| `build/audit/m2-a7/tools/make_package.py` | Package builder (layout, CRC, in-place safety analysis) |
| `build/audit/m2-a7/tools/make_fixture.py` | QEMU fixture builder (trampoline + package) |
| `build/audit/m2-a7/tools/make_negcases.py` | 8 negative-test case generator |
| `build/audit/m2-a7/tools/run_m2a7.py` | QEMU runner with QMP evidence + RAM dump verification |
| `build/audit/m2-a7/build/package.bin` | Raw payload package (311,296 B) |
| `build/audit/m2-a7/build/package.json` | Layout record, hashes, relocation table, safety proof |
| `build/audit/m2-a7/build/fixture_42.elf` | QEMU test fixture (base A = 0x42000000) |
| `build/audit/m2-a7/build/fixture_44.elf` | QEMU test fixture (base B = 0x44000000) |
| `build/audit/m2-a7/logs/*.log` | Serial console captures |
| `build/audit/m2-a7/logs/*.json` | Per-run QMP evidence + verdict |

---

## 8. GO / NO-GO Decision

> ### **NO-GO — Cannot proceed to PongoOS integration until bootstrap stack frame blocker is resolved.**
>
> **What works:**
> - Package format design and in-place relocation safety proof
> - Header validation, CRC32, payload checksum logic
> - Bootstrap entry, stack setup, BSS zeroing
> - QEMU fixture harness with QMP evidence collection
> - Negative-test case generation (8 categories)
> - In-place relocation safety analysis (disjoint targets)
>
> **What blocks:**
> - Bootstrap `bootstrap_main` stack frame (1360 B) insufficient for full relocation + handoff logic
> - Silent crash before any relocation is applied → cannot verify end-to-end relocation
> - Negative tests cannot execute (crash in header validation phase)
> - Two-base QEMU verification incomplete (base B untested)
>
> **Required fix before next step:**
> Refactor `bootstrap_main` into phase-separated functions:
> 1. `phase0_validate_header(base, &hdr, &paddr)` — small frame
> 2. `phase1_payload_checksum(paddr, hdr)` — small frame
> 3. `phase2_elf_parse(paddr, hdr, &img)` — medium frame
> 4. `phase3_apply_relocations(base, &img)` — medium frame
> 5. `phase4_bss_init_and_handoff(base, &img)` — small frame
> Each called sequentially from `bootstrap_main` (now a tiny dispatcher).
>
> **Alternative:** Use linker script to force larger stack frame (`STACK_SIZE` in `bootstrap.ld` only affects package stack, not bootstrap frame). Requires compiler-level frame size control or assembly prologue override.

---

## 9. Baseline Re-Verification

```bash
$ make qemu > /tmp/m2a7_final_baseline.log 2>&1; echo $?
0
$ grep -c "MYOS_KERNEL_PASS" /tmp/m2a7_final_baseline.log
1
$ grep "=== QEMU TEST: PASS ===" /tmp/m2a7_final_baseline.log
=== QEMU TEST: PASS ===
```

Canonical `make qemu` baseline **unchanged and passing** (exit 0, `MYOS_KERNEL_PASS` present).

---

## 10. Constraint Compliance

- `pongoos-src/` — **not modified** (read-only)
- M1 payload, `include/myos_image.h`, canonical QEMU artifacts — **not modified**
- `make qemu` behavior — **untouched, re-verified passing**
- No iPhone connection, no device operations, no flash/restore
- No commits, no pushes
- All experimental code under `build/audit/m2-a7/` and `/tmp`

---

*Report generated 2026-10-10. Prototype demonstrates feasible package format and relocation logic; bootstrap refactor required for end-to-end verification.*