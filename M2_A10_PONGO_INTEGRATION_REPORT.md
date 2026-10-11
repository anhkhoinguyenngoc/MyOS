# M2-A10 — PongoOS `bootr` Integration Audit & Independent M2-A9 Verification

**Scope.** Independently verify the M2-A9 raw-payload bootstrap / memory-integrity claims, complete the M2-A10 PongoOS `bootr` ABI integration audit, and select the safest evidence-supported next QEMU prototype.

**Hard constraints honored.** `pongoos-src/` inspected **read-only**; submodule revision unchanged. No commits, pushes, branch changes, resets, or cleanup. No physical device connected or operated. Canonical kernel / M1 sources untouched. Pre-existing uncommitted work preserved.

---

## 1. Executive Summary

This pass **corrects three claims** carried in the earlier M2-A9 reports and **confirms** the core design.

1. **The "8/8 PASS" claim was neither true nor false as stated — it was measured wrong.** The committed negative runner (`run_m2a7.py`) marks 6 of 8 reject cases `FAIL` with the note *"payload region was modified before rejection."* Independent byte-level attribution (this pass) proves those warnings are a **false positive in the runner's no-write heuristic**, not a memory-integrity violation. **All 8 malformed packages were genuinely executed against rebuilt fixtures and all 8 were rejected with their expected messages** (stored RAM dumps, `logs/neg_*_ram.elf`, 2026-10-10 15:18–15:20).

2. **The differing bytes are the bootstrap's OWN `.data` scratch, not the payload and not the stack.** `nm bootstrap.elf` places `img` (`image_desc`) at package offset `[0x1648, 0x1720)`. Every differing byte in every case (0 / 0 / 7 / 7 / 30 / 30 / 19 / 7) lies inside that range; **zero** bytes differ in the package header (0x2000–0x2200), the ELF payload, any relocation target, or the BSS. The earlier explanation *("the active stack is included in the comparison range")* is **false**: `run_m2a7.py` already truncates the comparison at `stack_off = 0x48000`, and the bootstrap stack is at `[0x49e00, 0x4c000)`, strictly above it.

3. **The `bootr` ABI is confirmed from source.** `bootr` performs no ELF parsing; it flags the uploaded buffer `BOOT_FLAG_RAW` and yields. `_jump_to_image_extended` transfers control by `ret` to **buffer offset 0**, with `x0 = boot_args`, `x1 = x2 = x3 = 0`, `x8 = gEntryPoint`, and a global `ic iallu`. This matches the M2-A9 raw-image assumption exactly.

**Also found (pre-existing, unrelated):** `tests/test_boot_diag_extended.sh` exits 1 — root-caused this pass to an unguarded pipeline at line 111 under `set -euo pipefail` (`.text.vectors` is absent from the M1 payload, so `grep` returns 1 and aborts the script). Not an M2-A9/A10 defect and not modified.

**Final decision: `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`.** Strategy B is the only evidence-supported path. The M2-A10 follow-up closes B1, B2, and B3 with deterministic QEMU evidence; the remaining condition is the separately unresolved PongoOS/runtime handoff evidence in §12. A successful audit does **not** authorize physical-device testing.

---

## 2. Starting Git Commit and Working-Tree Status

| Item | Value |
|------|-------|
| Repository root | `/home/khoi/Projects/myos` |
| HEAD commit | `3bea11141fea05bb90017069cff22b9a37d91e9e` — "Add MyOS boot diagnostics and exception handling" |
| Branch | `main` |
| Pre-existing tracked modifications | `Makefile`, `arch/aarch64/boot_diag.c`, `arch/aarch64/boot_diag.h`, `arch/aarch64/vectors.S` |
| Pre-existing untracked files | `M2_A4…M2_A9` reports, `arch/aarch64/uart_pl011.S`, `boot/aarch64/kernel.ld`, `boot/aarch64/start.S`, `fix_runner.py`, `include/`, `kernel/`, `tests/{make_m2_image.py,sim_mmap.py,test_m2_negative.py,validate_m2.py}` |
| PongoOS submodule | `pongoos-src @ 4c9b7541629234147fcc778f0ce4162482aaccef` (clean, **not** modified) |

No `git reset`, `clean`, or branch operation was performed. All pre-existing changes were preserved exactly.

---

## 3. Source Files Inspected

### MyOS
| File | Purpose |
|------|---------|
| `include/myos_image.h` | Canonical M2 image format (header 64 B, segment 48 B) |
| `boot/aarch64/kernel.ld`, `start.S`, `m1.ld`, `m1_entry.S` | Canonical kernel / M1 linker + entry |
| `kernel/main.c` | `MYOS_KERNEL_PASS` emission + `wfe` parking |
| `tests/validate_m2.py`, `test_m2_negative.py`, `make_m2_image.py`, `sim_mmap.py` | Host validator + 31-case host negative suite + image tooling |
| `tests/offline_m1.sh`, `test_boot_diag.sh`, `test_boot_diag_extended.sh` | Regression suites |
| `Makefile` | `qemu`, `m2-check`, `offline` targets |
| `build/audit/m2-a7/src/bootstrap.c` | M2-A9 parser / relocator / CRC / handoff |
| `build/audit/m2-a7/src/bootstrap_entry.S` | M2-A9 entry (SP, header copy) |
| `build/audit/m2-a7/bootstrap.ld` | Bootstrap linker script (`_stack_top`, STACK_OFF/SIZE) |
| `build/audit/m2-a7/src/sim_bootr.S` | QEMU PongoOS `bootr` simulator |
| `build/audit/m2-a7/tools/make_package.py` | Package + CRC builder |
| `build/audit/m2-a7/tools/make_negcases.py` | 8-case malformed-package generator |
| `build/audit/m2-a7/tools/make_fixture.py` | QEMU fixture builder |
| `build/audit/m2-a7/tools/run_m2a7.py` | QEMU runner + QMP/RAM evidence |
| `build/audit/m2-a7/logs/neg_*.json`, `neg_*_ram.elf` | Stored negative-test evidence (2026-10-10) |
| `build/audit/m2-a10/baseline.txt` | Baseline snapshot |

### PongoOS (read-only)
| File | Contract detail |
|------|-----------------|
| `pongoos-src/src/kernel/pongo.h:77–104` | packed `boot_args` struct |
| `pongoos-src/src/kernel/pongo.h:125–130` | `BOOT_FLAG_*` (`RAW = 4`) |
| `pongoos-src/src/shell/main.c:40–48` | `pongo_boot_raw()` (`bootr` handler) |
| `pongoos-src/src/shell/main.c:257` | `command_register("bootr", …)` |
| `pongoos-src/src/boot/jump_to_image.S:42–55` | `_jump_to_image_extended` register contract |
| `pongoos-src/src/boot/{entry.S,main.c}` | PongoOS EL1 entry / image jump |

> `src/kernel/entry.c` and `src/kernel/mm.c` were **not** re-opened in this pass; the prior report's citations of them are marked **UNVERIFIED** in §9/§11.

---

## 4. Verified Package / Header / CRC Semantics

### 4.1 Package layout (`M2A7RAW` v1)
```
+0x0000  bootstrap flat binary      (position-independent)
+0x1648  bootstrap .data (`img` image_desc scratch, 216 bytes)  <-- see §5/§6
+0x1810  __bss_start == __bss_end    (empty BSS)
+0x2000  package header              (512 B, magic "M2A7RAW\x01", CRC32)
+0x3000  kernel PIE ELF payload
+0x48000 stack region               (_stack_bottom, canary at bottom)
+0x4c000 _stack_top / total_size
```

### 4.2 Header fields (verified against `bootstrap.c:152–166`, packed, 8-byte aligned)
| Offset | Field | Verified semantics |
|--------|-------|--------------------|
| 0x000 | `magic[8]` | `M2A7RAW\x01` (checked byte-by-byte) |
| 0x008 | `version` | must `== 1` |
| 0x00A | `hdr_size` | must `== 512` |
| 0x00C | `hdr_crc32` | CRC32 over 512 header bytes with this field zeroed |
| 0x010 | `_pad0_a` | alignment padding |
| 0x014 | `_pad0_b` | alignment padding |
| 0x018 | `total_size` | whole package size, `≤ 1 MiB` |
| 0x020 | `payload_off` | must `== 0x3000` |
| **0x028 (40)** | `payload_size` | payload length |
| **0x030 (48)** | `payload_crc32` | CRC32 over payload bytes |
| 0x034 | `_pad1` | alignment padding |
| 0x038 (56) | `stack_off` | must `== 0x48000` |
| 0x040 (64) | `stack_size` | `≥ 0x400` |
| 0x048 (72) | `stack_canary` | expected at `base + stack_off` |
| 0x050 (80) | `reloc_count_expect` | cross-check vs `.rela.dyn` count |

**Correction to prior reporting:** `payload_size` is at **0x28 / 40**; the **CRC** is at **0x30 / 48**. The M2-A9 report's phrase "payload CRC offset fixed: from 40 to 48" refers to the CRC field and is *not* a statement that `payload_size` moved. `make_negcases.py` mutates ELF **segment** `p_offset/p_filesz/p_memsz` *inside the payload* and then recomputes both CRCs; it never edits the package `payload_size`, so that field is not at risk.

### 4.3 CRC algorithm
- CRC-32/ISO-HDLC (reflected poly `0xEDB88320`, init `0xFFFFFFFF`, final XOR `0xFFFFFFFF`), bitwise.
- Header CRC covers exactly 512 bytes with `hdr_crc32` temporarily zeroed (restored afterwards).
- Payload CRC covers `payload_size` bytes at `base + payload_off`.
- **Validation order:** magic → version → hdr_size → header CRC → total_size → stack region → payload range → payload offset → stack size min → payload CRC → ELF parse.

### 4.4 Two-phase no-write design (source-confirmed)
`bootstrap_main` runs **all** header/bounds/payload-CRC checks before `phase2_elf_parse`. Phase 2 validates **every** relocation record (type, symbol index, target in file-backed range, addend in image memory, u64 overflow) **before** phase 3 writes a single slot. This design is confirmed by source; §5 confirms it end-to-end via stored evidence.

---

## 5. The Eight Negative Tests — Independent Evidence

`make_negcases.py` defines exactly 8 cases. Stored runs exist under `build/audit/m2-a7/logs/neg_*` (2026-10-10 15:18–15:20), each with a RAM dump (`neg_<case>_ram.elf`) and a result JSON. This pass re-derived the exact differing-byte offsets from each dump (script: `build/audit/m2-a10/audit_diff_offsets.py`).

| # | Case | Mutation | Observed rejection (stored dump) | Diff bytes | Diff location |
|---|------|----------|----------------------------------|-----------|---------------|
| 1 | `trunc_header` | file cut at 0x1fff | `package header magic mismatch` | 0 | — |
| 2 | `trunc_payload` | payload cut at 0x30000 | `payload checksum mismatch` | 0 | — |
| 3 | `seg_bounds` | PT_LOAD filesz/memsz beyond payload | `PT_LOAD file range outside payload bounds` | 7 | `img` |
| 4 | `overflow` | `p_memsz` near u64 max | `PT_LOAD memory footprint outside payload bounds` | 7 | `img` |
| 5 | `bad_reloc_type` | first reloc type = 257 | `unsupported relocation type` | 30 | `img` |
| 6 | `reloc_oob` | reloc target beyond payload | `relocation target outside permitted memory` | 30 | `img` |
| 7 | `entry_oob` | `e_entry` outside exec PT_LOAD | `entry point outside executable memory` | 19 | `img` |
| 8 | `bss_invalid` | `p_filesz > p_memsz` | `invalid BSS range (p_filesz > p_memsz)` | 7 | `img` |

**Result: 8/8 rejections correctly observed at the guest, each with its expected message.**

### 5.1 Attribution of the differing bytes (the actual "modification")
`nm -S bootstrap.elf` → `img` at `0x1648`, size `0xd8` (216 B), in `.data`; `__bss_start == __bss_end == 0x1810` (empty BSS). The runner compares `[base, base+min(len,stack_off))` — which **includes** the bootstrap's `.data` at `[0x1648,0x1720)` — against the case file. The bootstrap legitimately writes its own `img` scratch while parsing, so those bytes differ.

Every differing offset across all cases falls in `[0x1648, 0x1720)`; e.g. for `seg_bounds`: `0x1660,0x166a,0x16a8,0x16a9,0x16c8,0x16c9,0x16e8` (all `img`). **No** difference occurs in `[0x2000,0x2200)` (header), `[0x3000, payload_end)` (payload), any relocation slot, or the BSS. The number of diffs scales with how far parsing progressed before rejection (7 early, 30 at relocation validation, 19 at entry check).

### 5.2 Why the runner reports FAIL
`run_m2a7.py:493`: a reject case fails if `negative_no_write` is false, and `negative_no_write = (len(diff) == 0)` (`:420–423`). Because the bootstrap's own `.data` is inside the compared window, `negative_no_write` is false for every case that reaches phase 2 → 6 false-positive FAILs. The two truncation cases (which reject before any `img` write) report `negative_no_write = true`.

**The security property the tests target — "no header, payload, relocation-target, or BSS byte is written before rejection" — holds for all 8 cases.** The runner's heuristic merely fails to exclude the bootstrap's own writable scratch.

### 5.3 Original re-run defect (closed by §15)
The original `run_m2a7.py --rebuild-fixture` path did **not** reproduce. Two defects were identified:
- `run_m2a7.py:162,176` compute `outdir_abs = .../build/audit` and then look for `build/fixture_<base>_<tag>.elf`, but `make_fixture.py:53` writes `build/fixture_<base>.elf` (no tag suffix). The rebuilt fixture is therefore never the file QEMU loads.
- The package path is `os.path.abspath(args.package)` (`:169`) resolved against the process CWD, so a repo-root-relative path doubles once CWD is inside `m2-a7`.

Consequently the old runner could boot the stale prebuilt valid fixture and print `MYOS_KERNEL_PASS` + "no rejection observed". That was a runner artifact, not a bootstrap failure. The isolated M2-A10 runner closes this defect without modifying the old M2-A7 files; its exact-input evidence is in §15.

---

## 6. Memory-Range and Stack-Overlap Analysis

### 6.1 Regions (base A = 0x42000000)
| Region | Range | Source |
|--------|-------|--------|
| Bootstrap code | `[base+0x0000, base+0x1648)` | `.text.boot` + `.rodata` |
| Bootstrap `.data` (`img`) | `[base+0x1648, base+0x1720)` | `nm`; **the only written region** |
| Bootstrap BSS | empty (`__bss_start==__bss_end==0x1810`) | `nm` |
| Package header | `[base+0x2000, base+0x2200)` | layout |
| Payload + reloc slots | `[base+0x3000, base+0x3000+payload_size)` | layout |
| Stack region (`_stack_bottom`) | `[base+0x48000, base+0x4c000)` | `nm` / layout |
| Bootstrap active SP | starts at `base+0x49e00` (`_stack_top − 0x2000 − 0x200`) | `bootstrap_entry.S` |

### 6.2 What the runner compares
`[base, base+min(len, stack_off))` with `stack_off = 0x48000` → the stack region `[base+0x48000, base+0x4c000)` is **already excluded**. The bootstrap's active frames `[base+0x49e00, base+0x4c000)` are strictly above `stack_off`.

### 6.3 Conclusion
- The compared window *does* overlap the bootstrap's own `.data` `[0x1648,0x1720)` → this (not the stack) explains the diffs.
- Compared window vs header/payload/reloc-targets/BSS: overlaps them, but **no writes occurred there pre-rejection** (verified byte-exact).
- The M2-A9 "stack included in the comparison" explanation is **false**; the correct explanation is the `.data` scratch struct. The previous reports' *conclusion* (no payload corruption) is nevertheless **correct**.

---

## 7. Negative-Control Results

The dated M2-A10 follow-up adds an isolated test-only bootstrap under
`build/audit/m2-a10/negcontrol/`. Control A uses the unmodified valid package:
it reaches `MYOS_KERNEL_PASS`, checks 35/35 relocation slots, and reports zero
unexpected protected writes. Control B writes marker
`0x4d3241314d41524b` at package offset `0x5000`, which is absolute address
`0x42005000`; the checker reports all eight changed bytes, including the exact
observed marker. The same run with detection bypassed is forced to fail the
control assertion, so a rejection log alone cannot make the suite pass.

**Status: PASS (instrumentation).** The injected bootstrap and fixture are
strictly separate from the normal package and production bootstrap artifacts.

---

## 8. Regression Tests — Commands, Exit Codes, Outcomes

| # | Command | Exit | Verdict | Evidence |
|---|---------|------|---------|----------|
| 1 | `make qemu` | 0 | **PASS** | `MYOS_KERNEL_PASS` → `=== QEMU TEST: PASS ===`; process ended by `timeout` after the marker (Makefile maps found-marker → 0) |
| 2 | `make m2-check` | 0 | **PASS** | validator PASS; host negative suite **31/31**; `sim_mmap` `LOAD PLAN VALID (HYPOTHETICAL)` |
| 3 | `bash tests/offline_m1.sh` | **1** | **FAIL (unrelated)** | All checks pass except `FAIL_m1ctl_usage`; not modified in this task. |
| 4 | `bash tests/test_boot_diag.sh` | 0 | **PASS** | 15 checks all PASS |
| 5 | `bash tests/test_boot_diag_extended.sh` | **1** | **FAIL (pre-existing, root-caused)** | Dies after test-9 `[WARN] .text.vectors section not found`. Cause: line 111 `VEC_ALIGN=$(readelf -S … | grep "\.text\.vectors" | awk …)`; `grep` finds nothing → exit 1 → `set -euo pipefail` aborts the script. Unrelated to M2-A9/A10; not modified. |
| 6 | `python3 tests/test_m2_negative.py` | 0 | **PASS** | host parser suite 31/31 (distinct code path from M2-A7 QEMU) |
| 7 | `python3 build/audit/m2-a10/run_suite.py` | 0 | **PASS** | 8/8 rejects, 0 protected writes each, clean control PASS, injected control detection PASS, bypass assertion FAIL as required, reproducible fixture hashes. |
| 8 | Existing valid package at `0x42000000` and `0x44000000` | 0 / 0 | **PASS** | `MYOS_KERNEL_PASS`, 35/35 relocation slots, exact fixture identity at both bases. |
| 9 | `python3 -m py_compile ...m2-a10...` | 0 | **PASS** | Isolated audit tooling syntax check. |

**Exit-code note:** the kernel parks in `wfe`, so QEMU never self-exits; `make qemu` treats post-marker SIGTERM as success, and the M2-A7 runner documents `exit_status = 124` as the expected "guest parked" state. `124` is not a clean exit; it is the documented parked-guest outcome.

---

## 9. Source-Backed `bootr` ABI Findings

1. **Registration/handler** — `shell/main.c:257` `command_register("bootr", "boot raw image", pongo_boot_raw);`; `:40–48` `pongo_boot_raw()` rejects if no upload (`loader_xfer_recv_count == 0`), then sets `loader_xfer_recv_count = 0`, `gBootFlag = BOOT_FLAG_RAW` (4), and `task_yield()`. **No ELF parsing happens in `bootr`** — the uploaded image must self-handle ELF/relocation (exactly the M2-A9 bootstrap).
2. **Transfer** — `boot/jump_to_image.S:42–55` `_jump_to_image_extended`: `mov x30,x0` (LR = entry = upload-buffer offset 0); `mov x0,x1` (`x0 = boot_args`); `mov x8,x2` (`x8 = gEntryPoint`); `isb; dsb sy; ic iallu; dsb sy; isb`; `x1=x2=x3=0`; `ret`.
3. **Guaranteed by code:** entry at buffer offset 0; `x0=boot_args`; `x1=x2=x3=0`; `x8=gEntryPoint`; global I-cache invalidate.
4. **`boot_args`** — `pongo.h:77–104` packed: `Revision(u16) Version(u16) __pad0(u32) virtBase(u64) physBase(u64) memSize(u64) topOfKernelData(u64) Video(6×u64) machineType(u32) __pad1(u32) deviceTreeP(ptr) deviceTreeLength(u32) {iOS12|iOS13 union}`. `physBase/memSize/topOfKernelData` are **physical**. MyOS ignores `x0`.

| Property | Status |
|----------|--------|
| Entry = upload-buffer offset 0 | **Guaranteed** (`_jump_to_image_extended`) |
| `x0=boot_args`; `x1..x3=0`; `x8=gEntryPoint` | **Guaranteed** |
| `ic iallu` before entry | **Guaranteed** |
| MMU off / caches clean at entry | **Documented by prior audit; NOT re-verified this pass** (`entry.c` not re-read) |
| Upload-buffer runtime address | **Runtime value** — unknown at build time |
| `CurrentEL` at entry | **Unknown this pass** |
| `VBAR_EL1` state | **Uncontrolled** by the payload |

---

## 10. A / B / C Strategy Comparison

| Criterion | **A — Fixed-address image** | **B — Position-independent bootstrap** | **C — PongoOS module/command** |
|-----------|-----------------------------|----------------------------------------|--------------------------------|
| Scope | Link MyOS at a hardcoded PA | Current M2-A9: PIE bootstrap validates → relocates → hands off | Modify/register a PongoOS command — **forbidden** |
| Address selection | Must avoid PongoOS text, upload buffer, boot_args, allocator metadata | None: runs wherever `bootr` places the buffer | N/A under read-only constraint |
| Safety evidence | **None** (`physBase`/`memSize`/`topOfKernelData` are runtime; no safe fixed-reserve primitive) | **Strongest**: base derived from PC (`adr`), SP inside the package, validate-before-write (now independently verified) | Depends on a PongoOS change; out of scope |
| Risk | High (silent collision) | Medium (MMU/cache/EL assumptions unverified on device) | Forbidden |
| Testability | Host-only | **QEMU-testable today** (two bases) | Not testable without rebuilding PongoOS |
| Verdict | **Rejected** | **Recommended** | **Deferred** |

**Selected strategy: B.**

---

## 11. QEMU Evidence vs Physical-Device Assumptions

**Proven in QEMU / host this pass:** bootstrap parser + CRC + relocation logic consistent with source; valid package reaches `MYOS_KERNEL_PASS`; canonical `make qemu` PASS; host parser 31/31; **all 8 malformed packages rejected with expected messages and zero protected-region writes** (independent offset attribution). `offline_m1.sh` was rerun and currently has the unrelated `FAIL_m1ctl_usage` result recorded in §8.

**NOT proven:** physical-device compatibility of any kind; runtime `physBase`/`memSize`/`topOfKernelData`/framebuffer on A8/iOS 12.5.7; upload-buffer runtime address and its non-overlap with PongoOS metadata; actual `CurrentEL`/MMU/cache state at `bootr` handoff; sufficiency of `ic iallu` on the real cache hierarchy.

QEMU success is **not** proof that MyOS boots on an iPhone 6.

---

## 12. Remaining Blockers and Smallest Next Experiment

**Blockers**
1. **B4 (ABI/runtime):** `entry.c`/`mm.c` call-site + `lowlevel_cleanup`, `CurrentEL`, VBAR state, MMU/cache state, and real upload-buffer safety remain unresolved.
2. **B5 (pre-existing):** `test_boot_diag_extended.sh` exits 1 (line-111 pipeline; unrelated).

**Smallest next experiment:** perform a separate read-only PongoOS/runtime handoff audit for `CurrentEL`, `VBAR_EL1`, MMU/cache state, and upload-buffer/allocator safety. Do not access physical hardware until that evidence exists.

This closes B1–B3. B4 requires a separate read-only PongoOS re-read.

---

## 13. Changed Files and Prohibited-File Confirmation

**Created / moved by this audit:**
| Path | Nature |
|------|--------|
| `M2_A10_PONGO_INTEGRATION_REPORT.md` | This report |
| `build/audit/m2-a10/m2a10lib.py` | Isolated B1/B2 library: symbol-derived ranges, exact fixture embedding, fixed-root paths |
| `build/audit/m2-a10/run_case.py` | Isolated deterministic QEMU runner and per-range diff evidence |
| `build/audit/m2-a10/run_suite.py` | Eight-case suite, clean/injected controls, bypass assertion, reproducibility check |
| `build/audit/m2-a10/negcontrol/make_injected_control.py` | Test-only injected-bootstrap builder |
| `build/audit/m2-a10/build/neg/*.bin`, `build/audit/m2-a10/build/negcases.json` | Generated malformed inputs and manifest |
| `build/audit/m2-a10/fixtures/fixture_42000000_neg_*.elf`, `fixture_42000000_control_clean.elf`, `fixture_inject_42000000.elf`, `fixture_42000000_valid_base_a.elf`, `fixture_42000000_cwd_entry_oob.elf`, `fixture_44000000_valid_base_b.elf` | Generated exact-input QEMU fixtures |
| `build/audit/m2-a10/logs/neg_*.{json,log}`, `*_ram.elf`, `control_*.{json,log}`, `control_*_ram.elf`, `summary.json`, `valid_base_*.json` | Generated run logs, RAM snapshots, and aggregate evidence |

The exact edited source/report files are the five paths in the first five
rows of this table; the remaining rows are ignored generated evidence.

**Protected files were not modified.** Specifically:
- `pongoos-src/` — **untouched**; submodule revision `4c9b754…` unchanged.
- Canonical kernel (`boot/aarch64/*`, `kernel/main.c`, `include/myos_image.h`), `Makefile`, `tests/*` — **untouched by this task**.
- M1 payload sources and the M2-A7 source/tooling — **untouched**; M2-A7 scripts were read-only inputs to the isolated tooling.
- Pre-existing uncommitted changes — **preserved**.
- No commits, pushes, branch changes, resets, or cleanup executed.

---

## 14. Final Decision

> ## `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`
>
> **Rationale.** Strategy B is the only evidence-supported path, and its core memory-integrity property — *no header, payload, relocation-target, or BSS byte is written before rejection* — is now independently verified for all 8 malformed packages. B1 uses the current ELF symbol `img` to exclude only `[0x1648,0x1720)` and reports exact per-range differences; B2 proves exact generated fixture paths, sizes, hashes, and reproducibility; B3 detects the deliberate marker at `0x42005000` and fails when detection is bypassed. The `bootr` raw-image ABI (entry at buffer offset 0, `x0=boot_args`, `x1..x3=0`, `x8=gEntryPoint`, `ic iallu`) is confirmed from source.
>
> B1/B2/B3 are closed by the dated follow-up. The decision remains conditional solely because the PongoOS/runtime handoff and physical memory-safety assumptions remain unresolved; passing these tooling tests does not authorize device testing. The prior "stack-overlap artifact" explanation is retracted and replaced with the verified `.data`-scratch cause.

---

## 15. Dated M2-A10 Follow-up — 2026-10-10

This section supersedes the earlier B1/B2/B3 statuses above. Work stayed in
the isolated `build/audit/m2-a10/` tooling and did not modify `pongoos-src/`,
the canonical kernel, M1, the Makefile, or tests.

### 15.1 B1 — protected-memory measurement

The production bootstrap ELF was checked with `nm -S` and section metadata.
The writable object is symbol-derived, not hardcoded:

```
img             [0x1648, 0x1720)  216 bytes  (nm:img; .data exact match)
__bss_start == __bss_end == 0x1810       (empty bootstrap BSS)
stack           [0x48000, 0x4c000)
```

Only `[0x1648,0x1720)` is classified as legitimate bootstrap scratch. The
stack is tracked separately and excluded from the pre-rejection comparison.
The protected model monitors bootstrap non-scratch ranges, the package header
`[0x2000,0x2200)`, header pad, payload `[0x3000,0x45960)` split around all 35
relocation targets, payload tail `[0x45960,0x48000)`, and the empty BSS
`[0x1810,0x1810)`. Relocation targets are individually named and checked.

For every negative case, every protected-range summary had an empty exact
`diff_offsets` list. The only differences were scratch bytes:

| Case | Exact scratch diff offsets | Protected diff offsets |
|---|---|---|
| `trunc_header` | none | none |
| `trunc_payload` | none | none |
| `seg_bounds` | `0x1660,0x166a,0x16a8,0x16a9,0x16c8,0x16c9,0x16e8` | none |
| `overflow` | `0x1660,0x166a,0x16a8,0x16a9,0x16c8,0x16c9,0x16e8` | none |
| `bad_reloc_type` | 30 offsets, all within `[0x1648,0x1720)` | none |
| `reloc_oob` | same 30 offsets as `bad_reloc_type` | none |
| `entry_oob` | `0x1649,0x164a,0x164b,0x1660,0x166a,0x1672,0x1692,0x16a8,0x16a9,0x16b0,0x16b1,0x16b2,0x16c8,0x16c9,0x16d0,0x16d1,0x16d2,0x16e8,0x16f0` | none |
| `bss_invalid` | `0x1660,0x166a,0x16a8,0x16a9,0x16c8,0x16c9,0x16e8` | none |

The full 30-offset and per-range records are stored in each case JSON under
`logs/neg_<case>.json`.

The exact 30-offset list for both `bad_reloc_type` and `reloc_oob` is:
`0x1649,0x164a,0x164b,0x1651,0x1652,0x1653,0x1659,0x165b,0x1660,0x166a,0x1672,0x1692,0x16a8,0x16a9,0x16b0,0x16b1,0x16b2,0x16c8,0x16c9,0x16d0,0x16d1,0x16d2,0x16e8,0x16f0,0x1708,0x1709,0x170a,0x1710,0x1711,0x1718`.

### 15.2 B2 — deterministic fixtures and exact loaded inputs

The isolated runner resolves relative inputs from the fixed repository/audit
roots, writes one path per `(base, tag)`, refuses missing fixtures unless
`--rebuild-fixture` is given, and byte-compares the fixture PT_LOAD against
the requested package before QEMU starts. It records both package and fixture
SHA-256 values. The eight regenerated fixtures were:

| Case | Package size / SHA-256 | Fixture path | Fixture size / SHA-256 |
|---|---|---|---|
| `trunc_header` | 8191 / `8709b4e4fff34cc5a880f532e50a6cad7c06fabe3e57ff2ddeea9421ddd73a2a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_trunc_header.elf` | 140424 / `e02d6295ee3fa15cbb2d4a6897fe6a861dac4ea440cea44621fed67d16bafe89` |
| `trunc_payload` | 196608 / `d038ba56399346f6fe34fc7420198547c895f247b30bf757484ef7353aeb6e0a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_trunc_payload.elf` | 328840 / `8cb2f450724251bd7253d42d489442f5639a1f7b449133b4f22ffee08f9764db` |
| `seg_bounds` | 311296 / `63a8e9d8b50c2d8b232c23d7acc862230608bd31b895c34088b2898e03335f1b` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_seg_bounds.elf` | 443520 / `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8` |
| `overflow` | 311296 / `2497af0b2e651d85c44e9a45bee445b136e8f19363d3e102f904fa89a134eaad` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_overflow.elf` | 443520 / `86e5b8d3d8448a571cde69f56e74b989ad67fc01663cfa16e134bedcea1d27fc` |
| `bad_reloc_type` | 311296 / `9f3790a2ed921d14922b176a7c8551b3167fafbac88e6bb27e144046347763db` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_bad_reloc_type.elf` | 443536 / `9ca1a7560a3d9974aa67a2d5c53d1a9d2a9a708dde3c0af0dc7a87b5b601f620` |
| `reloc_oob` | 311296 / `bebb0e179d7c9bc99125e0259f77632dde18e37fda8649e5a30f4312585e56ff` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_reloc_oob.elf` | 443520 / `c4fb2be0768f9222b4ba2cae1f4dcb11f7863dc1e1df2f0c4fcd1adb63038a0f` |
| `entry_oob` | 311296 / `2b79f3463135c038c74aa50136559c6bea927418f45f0c2cc10786586a75465a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_entry_oob.elf` | 443520 / `493e037b35ba3b530a82ecf84140337273038183abcbd2557adf6bce58777fc4` |
| `bss_invalid` | 311296 / `667ef1d14513f681d424be5056c650fe9331fbaf96fe2d14e17d1cf643140c61` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_bss_invalid.elf` | 443528 / `05d71a7a1059d88fa44cea2cc57118cd723bc4d05861feeec8d7d2f10ba07012` |

The exact same `seg_bounds` fixture input was rebuilt twice:

```
repro_0.elf SHA-256 = 27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8
repro_1.elf SHA-256 = 27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8
```

The exact guest rejection lines were:

```
trunc_header     [BOOT] REJECT: package header magic mismatch (truncated/corrupt package)
trunc_payload    [BOOT] REJECT: payload checksum mismatch (truncated/corrupt payload)
seg_bounds       [BOOT] REJECT: PT_LOAD file range outside payload bounds
overflow         [BOOT] REJECT: PT_LOAD memory footprint outside payload bounds
bad_reloc_type   [BOOT] REJECT: unsupported relocation type
reloc_oob        [BOOT] REJECT: relocation target outside permitted memory
entry_oob        [BOOT] REJECT: entry point outside executable memory
bss_invalid      [BOOT] REJECT: invalid BSS range (p_filesz > p_memsz)
```

A run from `/tmp` using repository-relative package/output paths passed with
the same exact `entry_oob` fixture hash. A missing requested fixture exited 1
with `requested fixture does not exist`; a stale valid fixture exited 1 with
`fixture does not carry the requested package`.

### 15.3 B3 — injection control

Control A:

```
package: build/audit/m2-a10/build/package.bin
package size / SHA-256: 311296 / 26a1e1f647298785c26e620fb53b0befd5ecdad3e4626f73a16c62504acfa4d1
fixture: build/audit/m2-a10/fixtures/fixture_42000000_control_clean.elf
fixture size / SHA-256: 443504 / 834867d7afb7b656c51cc91f95e0b762c117b01379ff130582eb4393b5b520ba
result: MYOS_KERNEL_PASS, 35/35 relocation slots, 0 protected writes, PASS
```

Control B uses only `negcontrol/bootstrap_inject.*` and
`package_inject.bin`; the normal package is not changed. It wrote the
little-endian bytes `4b 52 41 4d 31 41 32 4d` for marker
`0x4d3241314d41524b` at package offset `0x5000`, absolute address
`0x42005000`. The checker reported eight exact violations at offsets
`0x5000..0x5007`, with the observed 64-bit marker equal to the expected
marker, and the control command exited 1 as an expected detection failure.

The bypass probe recorded the same eight `bypassed_offsets` but was forced to
fail (`control_inject_bypass`, exit 1). The aggregate suite therefore proves
that disabling/bypassing detection cannot produce a passing control result.

### 15.4 Exact commands and statuses

```
python3 build/audit/m2-a7/tools/make_negcases.py --outdir build/audit/m2-a10                         # 0
(workdir build/audit/m2-a10) python3 run_suite.py                                                     # 0
(workdir /tmp) python3 /home/khoi/Projects/myos/build/audit/m2-a10/run_case.py --tag cwd_entry_oob --package build/audit/m2-a10/build/neg/entry_oob.bin --base 0x42000000 --expect-reject 'entry point outside executable memory' --rebuild-fixture --outdir build/audit/m2-a10 --timeout 8  # 0
(workdir /tmp) python3 /home/khoi/Projects/myos/build/audit/m2-a10/run_case.py --tag missing_fixture_probe --package build/audit/m2-a10/build/neg/entry_oob.bin --base 0x42000000 --expect-reject 'entry point outside executable memory' --fixture build/audit/m2-a10/fixtures/does-not-exist.elf --outdir build/audit/m2-a10 --timeout 1  # 1 expected
(workdir /tmp) python3 /home/khoi/Projects/myos/build/audit/m2-a10/run_case.py --tag stale_fixture_probe --package build/audit/m2-a10/build/neg/entry_oob.bin --base 0x42000000 --expect-reject 'entry point outside executable memory' --fixture build/audit/m2-a10/fixtures/fixture_42000000_control_clean.elf --outdir build/audit/m2-a10 --timeout 1  # 1 expected
make qemu                                                                                             # 0
make m2-check                                                                                          # 0
bash tests/offline_m1.sh                                                                               # 1 (FAIL_m1ctl_usage; unrelated)
bash tests/test_boot_diag.sh                                                                            # 0
bash tests/test_boot_diag_extended.sh                                                                   # 1 pre-existing .text.vectors pipeline failure
python3 build/audit/m2-a10/run_case.py --tag valid_base_a --package build/audit/m2-a7/build/package.bin --base 0x42000000 --expect-pass --rebuild-fixture --outdir build/audit/m2-a10 --timeout 8  # 0
python3 build/audit/m2-a10/run_case.py --tag valid_base_b --package build/audit/m2-a7/build/package.bin --base 0x44000000 --expect-pass --rebuild-fixture --outdir build/audit/m2-a10 --timeout 8  # 0
python3 -m py_compile build/audit/m2-a10/m2a10lib.py build/audit/m2-a10/run_case.py build/audit/m2-a10/run_suite.py build/audit/m2-a10/negcontrol/make_injected_control.py  # 0
python3 tests/test_m2_negative.py                                                                        # 0 (also run by make m2-check)
```

`make m2-check` included the existing host negative suite, which reported
31/31. The two valid-base runs both reached `MYOS_KERNEL_PASS`, verified the
exact generated fixture, and reported zero protected writes. No physical
device was accessed.

### 15.5 Follow-up decision

**B1: PASS. B2: PASS. B3: PASS.** The eight malformed cases are now
reproducible and independently evidenced as 8/8 rejection-message PASS with
8/8 zero protected writes. The overall decision remains
`CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE` only for the unresolved PongoOS/runtime
handoff questions: `CurrentEL`, `VBAR_EL1`, MMU/cache state, upload-buffer
placement/allocator safety, and physical-device compatibility. No claim is
made that MyOS boots on iPhone 6.
>
> A successful audit does **not** authorize physical-device testing.
