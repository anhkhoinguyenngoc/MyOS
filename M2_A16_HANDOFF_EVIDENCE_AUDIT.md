# MyOS M2-A16 — PongoOS-to-MyOS Handoff Evidence Audit

Date: 2026-10-11
Run on: Arch Linux main PC (`/home/khoi/Projects/myos`), read-only audit
Physical iPhone connection: **not used, not required, not claimed**
Target: iPhone 6 / Apple A8, CPID `0x7000`
Pinned PongoOS: `4c9b7541629234147fcc778f0ce4162482aaccef`
Pinned Newlib: `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`

**Final recommendation: `STOP_AT_EVIDENCE_AUDIT` — no authorized runtime exists in
this workspace that executes the pinned PongoOS `bootr` handoff. Every runtime
handoff property remains `UNVERIFIED`. MyOS has not booted on Apple hardware,
and nothing in this audit claims otherwise.**

## 1. Executive conclusion

1. The PongoOS-to-MyOS handoff *contract* is fully pinned down **by source**: the
   `bootr` command path, the register contract at
   `jump_to_image_extended` (`x30`=raw image address, `x0`=`gBootArgs`, `x8`=
   `gEntryPoint`, `x1..x3`=0, `isb; dsb sy; ic iallu; dsb sy; isb`, then `ret`),
   EL1 entry, MMU/data-cache disable, and the upload-buffer formula are all
   traceable to exact lines of the pinned checkout (Section 4).
2. **Not one of those properties has a runtime measurement.** No environment in
   this workspace executes the pinned Apple-target PongoOS artifact through
   `bootr`. Generic QEMU `virt` (11.1.1, re-verified this audit) has no Apple
   machine, and the built Pongo artifacts produced no Pongo marker under QEMU
   (M2-A14, re-confirmed classification). The A13 harness is explicitly
   `SIMULATED`; the A10/A11 fixtures are an isolated simulator of the register
   contract, not PongoOS.
3. The **single most important unresolved blocker** is the absence of any
   authorized runtime trace of the pinned `bootr` path — see Section 6. Until it
   exists, the downstream unknowns (numeric `VBAR_EL1`/`SCTLR_EL1`/`TTBR*`
   values, upload-buffer placement, boot-arg alias validity, allocator
   reservation, and post-cleanup fault behavior) cannot be closed by more static
   analysis.
4. No new simulated harness was introduced (per the hard constraints): no
   existing gap makes one essential, because A13 already models the contract and
   its own report (Section 7 of `M2_A13_SYNTHETIC_HANDOFF_HARNESS.md`) states it
   cannot substitute for runtime evidence. Repeating generic QEMU simulations
   would add nothing.
5. One previously recorded test divergence was observed and is reported honestly
   in Section 9: `tests/offline_m1.sh` exited **0** in this run (previously
   recorded `FAIL_m1ctl_usage`). No failure was suppressed or relabeled; the
   other known failure (`tests/test_boot_diag_extended.sh` → missing
   `.text.vectors`) reproduced exactly.

## 2. Repository and revision integrity

Recorded in `build/audit/m2-a16/preflight.txt` before inspection:

| Item | Value |
|---|---|
| Root | `/home/khoi/Projects/myos` |
| Branch / HEAD | `main` / `3bea11141fea05bb90017069cff22b9a37d91e9e` |
| PongoOS checkout (`pongoos-src`, nested repo, gitignored) | `4c9b7541629234147fcc778f0ce4162482aaccef` — clean (`git status --porcelain` empty, `diff --exit-code` = 0) |
| Newlib checkout (`pongoos-src/newlib`) | `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` — clean |
| Pre-existing tracked changes (unchanged by this audit) | `Makefile`, `arch/aarch64/boot_diag.c`, `arch/aarch64/boot_diag.h`, `arch/aarch64/vectors.S` |
| Pre-existing untracked work (unchanged) | M2-A4…A15 reports, `arch/aarch64/uart_pl011.S`, `boot/aarch64/{start.S,kernel.ld}`, `fix_runner.py`, `include/`, `kernel/`, `tests/{make_m2_image.py,sim_mmap.py,test_m2_negative.py,validate_m2.py}` |

Both revisions match the values recorded by M2-A11/A13/A14/A15; nothing under
`pongoos-src/` was modified. The pinned A12 Apple-target build artifacts were
re-hashed this audit and match `M2_A14` exactly (`ARTIFACT_VERIFIED`):

| Artifact | SHA-256 (re-verified) |
|---|---|
| `build/audit/m2-a12/repro/build/Pongo` | `24cd343f…530610fa6` |
| `build/audit/m2-a12/repro/build/Pongo.bin` | `7d3f3e09…1125678e` |
| `build/audit/m2-a12/repro/build/PongoConsolidated.bin` | `841ff2f9…c9682f3b` |
| `build/audit/m2-a12/repro/build/checkra1n-kpf-pongo` | `e179e407…d81eac0de` |
| `build/audit/m2-a12/repro/build/libc.a` | `872c95f8…9004df0de4` |

Known limitation carried forward (M2-A14 §2): the artifact banner is
`pongoOS 2.5.1-3bea1114` because upstream's `PONGO_VERSION :=` (Makefile:44)
reads the enclosing git HEAD; the artifact is built from the pinned source but
is not version-tagged to it. A correctly tagged rebuild (`PONGO_VERSION=2.5.1-4c9b7541`
as a *make command-line variable*) is a prerequisite for any future runtime
comparison (Section 8).

Previous evidence artifacts were preserved: SHA-256 manifests of all
`M2_A*.md`/`M2_REPORT.md`/`D2_PHASE2_REPORT.md` files before and after this
audit are identical (`build/audit/m2-a16/evidence_hashes_{before,after}.txt`,
diff exit 0), and the A10/A13 fixture trees are byte-identical
(`build/audit/m2-a16/ab_{before,after}.sha256`, 40 files, diff exit 0).

## 3. How to read the evidence matrix

Classifications used, as required:

- `PROVEN_BY_SOURCE` — follows from the pinned PongoOS/MyOS source lines cited;
  static, not a runtime measurement.
- `PROVEN_BY_TEST` — observed by an executed test recorded in Section 9; its
  scope limitation is stated (generic QEMU or isolated simulator only).
- `UNVERIFIED` — neither source nor any test in this workspace establishes it;
  it requires a runtime trace that does not exist here.
- `NOT_APPLICABLE` — the question does not arise on the pinned path.

Evidence-tier vocabulary used alongside these:
`static analysis` (source citations), `synthetic harness` (A13, `SIMULATED`),
`generic QEMU test` (`virt` machine, not an Apple platform),
`Apple-compatible runtime` (none available), `physical iPhone test` (prohibited
here; previously attempted and blocked in D2 Phase 2/D3).

## 4. Handoff evidence matrix

All PongoOS line references are against the pinned checkout
`pongoos-src @ 4c9b754`; MyOS references against the current working tree.
The pinned call chain (static trace, re-verified line numbers this audit):

```
src/shell/main.c:257           command_register("bootr", ..., pongo_boot_raw)
src/shell/main.c:40-48         pongo_boot_raw: require count; clear count; gBootFlag=BOOT_FLAG_RAW(4); task_yield()
src/shell/usbloader.c:94-99    upload buffer = alloc_contig(UPLOADSZ=1 MiB) at usbloader_init
src/kernel/entry.c:210-234     scheduler loop sees gBootFlag; timer_disable; usb_teardown; disable_interrupts
src/kernel/entry.c:303-308     lowlevel_set_identity(); rebase_pc(-gPongoSlide); lowlevel_cleanup()
src/kernel/entry.c:309-312     jump_to_image_extended(loader_xfer_recv_data - kCacheableView + 0x800000000, gBootArgs, gEntryPoint)
src/boot/jump_to_image.S:42-55 x30=image; x0=args; x8=entry; x1=x2=x3=0; isb; dsb sy; ic iallu; dsb sy; isb; ret
```

### Item 1 — Entry point and register contract at `jump_to_image_extended`

| Aspect | Status | Evidence |
|---|---|---|
| Register contract (`x30`=image, `x0`=`gBootArgs`, `x8`=`gEntryPoint`, `x1..x3`=0; barriers + `ic iallu`; `ret` is terminal — nothing returns to `exit_to_el1_image`) | `PROVEN_BY_SOURCE` | `pongoos-src/src/boot/jump_to_image.S:42-55`; call site `src/kernel/entry.c:309-312`; prototype `src/kernel/entry.c:291` |
| `bootr` only flips a flag; the upload is not parsed by PongoOS | `PROVEN_BY_SOURCE` | `src/shell/main.c:40-48` (clears only `loader_xfer_recv_count`) |
| First executed MyOS instruction = byte 0 of the upload buffer (physical alias) | `PROVEN_BY_SOURCE` | `src/kernel/entry.c:311` computes `loader_xfer_recv_data - kCacheableView + 0x800000000`; `src/kernel/pongo.h:344` (`kCacheableView = 0x400000000`) |
| Remaining general registers (x4-x7, x9-x29), FP/SIMD and PSTATE state at `ret` | `UNVERIFIED` — source specifies nothing beyond the registers above | `jump_to_image.S:42-55` touches only x0-x3, x8, x30 |
| Runtime values of any register at the jump | `UNVERIFIED` | No runtime capture exists (Section 5) |

### Item 2 — Current exception level (`CurrentEL`) and CPU state

| Aspect | Status | Evidence |
|---|---|---|
| Successful `bootr` path executes at EL1 | `PROVEN_BY_SOURCE` | `_setup_el1` accepts only EL1 (`SPSR=0x4`) or transitions EL3→EL1 (`src/boot/entry.S:84-109`); `lowlevel_setup` panics unless `get_el()==1` (`src/kernel/mm.c:331`); no later instruction changes EL before the raw `ret` (`src/kernel/entry.c:294-322`) |
| Interrupts masked at handoff (`daifset` executed; timer disabled; USB torn down) | `PROVEN_BY_SOURCE` | `src/kernel/entry.c:210-234` (`timer_disable`, `usb_teardown`, `disable_interrupts`); `src/kernel/lowlevel.c:71-74` |
| MyOS `start.S` accepts EL1 or EL2; only EL1 is evidenced by the Pongo path | `PROVEN_BY_SOURCE` (compatibility), EL2 branch is QEMU-only | `boot/aarch64/start.S:44-56` |
| Actual runtime `CurrentEL` / PSTATE capture on the pinned artifact | `UNVERIFIED` | Generic-QEMU probe value `0x4` (A11) describes QEMU `virt` only and is not PongoOS evidence |

### Item 3 — MMU and data-cache state at handoff

| Aspect | Status | Evidence |
|---|---|---|
| MMU and data-cache enable bits cleared before the jump | `PROVEN_BY_SOURCE` | `lowlevel_cleanup` (`src/kernel/mm.c:344-348`) → `_disable_mmu_el1` (`src/kernel/lowlevel.c:117-128`) clears `SCTLR_EL1.M` (bit 0) and `SCTLR_EL1.C` (bit 2), then `tlbi vmalle1`, `ic iallu`, `dsb sy`, `isb` |
| Data cache cleaned/invalidated over Pongo's calculated RAM range before MMU-off | `PROVEN_BY_SOURCE` | `src/kernel/mm.c:346` → `cache_clean_and_invalidate` (`src/kernel/lowlevel.c:642-653`, `dc civac` per 64-byte line, `dsb sy`/`isb`) |
| Physical alias made executable before MMU-off | `PROVEN_BY_SOURCE` | `lowlevel_set_identity` (`src/kernel/mm.c:339-343`) and `map_range_noflush_rwx(0x800000000+phys_off, …)` (`src/kernel/mm.c:319`) |
| Numeric final `SCTLR_EL1`, actual cleaned range, TLB/TTBR residual state | `UNVERIFIED` | Source never reads back the register; runtime values require a trace |
| Non-RAM/device cache state, physical cache hierarchy behavior | `UNVERIFIED` | Architectural operations issued are source-proven; platform effects are not |

### Item 4 — Instruction-cache maintenance and synchronization

| Aspect | Status | Evidence |
|---|---|---|
| I-cache invalidated with full barriers at the final jump | `PROVEN_BY_SOURCE` | `src/boot/jump_to_image.S:47-51` (`isb; dsb sy; ic iallu; dsb sy; isb`) |
| I-cache invalidated again during cleanup | `PROVEN_BY_SOURCE` | `src/kernel/lowlevel.c:124-127` |
| `SCTLR_EL1.I` (instruction-cache enable) **not** cleared by PongoOS | `PROVEN_BY_SOURCE` (negative) | `_disable_mmu_el1` clears only bits 0 and 2 (`src/kernel/lowlevel.c:120-123`) — "i-cache disabled at handoff" must **not** be claimed |
| Runtime effectiveness of the invalidation on A8 | `UNVERIFIED` | Requires runtime execution |

### Item 5 — `VBAR_EL1` and exception-vector availability during early bootstrap

| Aspect | Status | Evidence |
|---|---|---|
| PongoOS installs its own vector and inherits it through the raw jump | `PROVEN_BY_SOURCE` | `src/kernel/mm.c:333` and `src/kernel/entry.c:61-62,162` (`set_vbar_el1(&exception_vector)`); `lowlevel_cleanup` does not change `VBAR_EL1` (`src/kernel/mm.c:344-348`) |
| Vector symbol lives in the `0x100000000` Pongo view; that view is a *translation* created before MMU-off | `PROVEN_BY_SOURCE` | `src/kernel/mm.c:325-327` (`map_range_noflush_rx(0x100000000, pongo_base, …)`; `gPongoSlide = 0x100000000 - pongo_base`); `pongoos-src/Makefile:58` (`-image_base,0x100000000`) |
| Whether the inherited `VBAR_EL1` address still resolves to the vector bytes after MMU-off | `UNVERIFIED` | After cleanup, addressing is physical; the source contains no identity mapping of `0x100000000` to `pongo_base`, and no runtime capture of `VBAR_EL1` exists |
| MyOS installs its own vectors | `PROVEN_BY_SOURCE` (late) | Canonical path: `boot/aarch64/start.S:115` (`bl vbar_el1_install`) — *after* CurrentEL check, stack, FP enable and early UART writes at the QEMU PL011 address (`start.S:22,73`). Raw M2-A7 bootstrap (`build/audit/m2-a7/src/bootstrap_entry.S:27-58`) installs **no** vectors before `bootstrap_main` |

### Item 6 — Upload-buffer base, size, allocation lifetime, alignment

| Aspect | Status | Evidence |
|---|---|---|
| Allocation: contiguous physical pages via PongoOS allocator, cacheable-view pointer | `PROVEN_BY_SOURCE` | `src/shell/usbloader.c:94-99` (`alloc_contig(UPLOADSZ)`), `src/kernel/mm.c:887-888` (`alloc_contig = phystokv(alloc_phys(size))`), `src/kernel/mm.c:893-895` (`phystokv(pa) = pa - 0x800000000 + kCacheableView`) |
| Size: 1 MiB initial; resizable to ≤128 MiB in 512-byte-rounded transfers | `PROVEN_BY_SOURCE` | `src/shell/usbloader.c:32-33,66-89` |
| Alignment/granularity: allocator works in PongoOS pages (16 KiB on this platform per `is_16k`/`tt_bits` handling, `src/kernel/mm.c:287-300`) | `PROVEN_BY_SOURCE` | `src/kernel/mm.c:844-889` |
| Lifetime: `bootr` clears only the byte count; USB teardown does not free the buffer; the raw branch consumes it immediately | `PROVEN_BY_SOURCE` | `src/shell/main.c:40-48`; `src/drivers/usb/synopsys_otg.c:1866-1870`; `src/kernel/entry.c:311` |
| Actual runtime VA/PA, actual size chosen by a real transfer, behavior across the scheduler teardown | `UNVERIFIED` | Requires allocator/runtime trace |

### Item 7 — Potential overlap between the uploaded image and live PongoOS memory

| Aspect | Status | Evidence |
|---|---|---|
| PongoOS allocator protects pages it tracks (wired/allocated) | `PROVEN_BY_SOURCE` | `src/kernel/mm.c:794-833` (`alloc_init`, `topOfKernelData`-based static base), `src/kernel/mm.c:844-882` (`alloc_phys` scans `PAGE_FREE` only) |
| Any proof that a MyOS destination/stack/vector range is disjoint from every live PongoOS object (boot args, device tree, framebuffer, page tables, firmware-owned memory) | `UNVERIFIED` | No such check exists in the pinned source; `bootr` does not reserve anything for the payload |
| Simulator evidence of non-overlap | `PROVEN_BY_TEST` (scope-limited) | A10/A11 suites: eight malformed cases rejected with zero protected-range writes; valid packages reached `MYOS_KERNEL_PASS` at `0x42000000`/`0x44000000` — evidence for the *simulator's* memory model only (`M2_A10_PONGO_INTEGRATION_REPORT.md`, `M2_A11_PONGO_HANDOFF_TRACE.md` §7) |

### Item 8 — Stack location, alignment, early stack use

| Aspect | Status | Evidence |
|---|---|---|
| PongoOS hands off with its own boot-time SP (inherited, not reset by the jump code) | `PROVEN_BY_SOURCE` | `jump_to_image.S:42-55` never touches `sp`; Pongo stacks are set earlier at `src/boot/entry.S:79-126` (`_exception_stack`, `_sched_stack`) and rebased by `rebase_pc` (`src/kernel/lowlevel.c:51-57`, adjusts `sp`, `x29`, `x30`) |
| The inherited SP numeric value and physical location | `UNVERIFIED` | Runtime-only fact |
| MyOS raw bootstrap does not depend on the inherited SP: it sets SP inside its own package from its PC | `PROVEN_BY_SOURCE` + `PROVEN_BY_TEST` (QEMU) | `build/audit/m2-a7/src/bootstrap_entry.S:29-32` (`adr x1, _stack_top; sub sp, x1, #0x2000; sub sp, sp, #0x200`; 16-byte-aligned deltas); observed at two simulator bases (A10/A11) |
| Canonical kernel stack is PC-relative to `_stack_top` in `kernel.ld` (`.` = `0x40080000`) | `PROVEN_BY_SOURCE` | `boot/aarch64/start.S:58-60`, `boot/aarch64/kernel.ld:8,18` — the `0x40080000` link address is QEMU-virt-specific and is **not** evidence for any Apple layout |
| Writability of the package stack region on the real post-handoff mapping | `UNVERIFIED` | Follows from the mapping facts in Items 3/7, which are runtime-unverified |

### Item 9 — Boot-argument pointers and their lifetime

| Aspect | Status | Evidence |
|---|---|---|
| `x0` receives `gBootArgs` (pointer value as PongoOS stores it), `x8` receives `gEntryPoint` | `PROVEN_BY_SOURCE` | `src/kernel/entry.c:296-297,311`; `src/boot/jump_to_image.S:44-46` |
| `boot_args` field layout (`physBase`, `memSize`, `topOfKernelData`, `deviceTreeP`, video info) | `PROVEN_BY_SOURCE` | `src/kernel/pongo.h:77-104` |
| `gBootArgs` is stored/used elsewhere in the `0x800000000`+phys view (PongoOS converts with `- 0x800000000 + kCacheableView` before dereferencing in the cached phase) | `PROVEN_BY_SOURCE` | `src/drivers/xnu/xnu.c:78,283` (conversion pattern); consistent with a physical-alias pointer being valid after MMU-off — **but** the source never asserts it and no runtime capture exists |
| Actual runtime address/view and values of the boot arguments; validity for a raw payload after MMU-off | `UNVERIFIED` | Requires runtime trace |
| MyOS usage | `PROVEN_BY_SOURCE` | The M2-A7 bootstrap ignores `x0`/`x8` entirely (`build/audit/m2-a7/src/bootstrap_entry.S:27-35`), so the unknown is currently harmless for the prototype |

### Item 10 — Earliest observable MyOS bootstrap and kernel-entry markers

| Aspect | Status | Evidence |
|---|---|---|
| Generic-QEMU markers exist and were observed: bootstrap reached, `MYOS_KERNEL_PASS` emitted, parked guest (`wfe`) handled as timeout | `PROVEN_BY_TEST` | `make qemu` exit 0 this audit (Section 9); marker at `kernel/main.c:34`; park loop `kernel/main.c:41`; Makefile pass check `Makefile:110-114` |
| Bootstrap rejection messages are PL011-console-only | `PROVEN_BY_SOURCE` | `build/audit/m2-a7/src/bootstrap.c:24` (QEMU virt PL011 console) |
| Earliest observable marker **on an Apple target** | `UNVERIFIED` | The early output path writes the QEMU PL011 address `0x09000000` (`boot/aarch64/start.S:22`); the pinned PongoOS platform code has no PL011 at that address on A8, and the raw bootstrap emits nothing at all. No Apple-visible marker (USB, screen, or otherwise) is defined for the M2 path |
| The M1 `MYOSM1OK` marker | `NOT_APPLICABLE` to the M2 `bootr` handoff | It belongs to the earlier M1 payload path (`platform/apple/a8/m1_a8.h:41`, `boot/aarch64/m1_entry.S:67-95`) |

### Item 11 — Fault behavior if execution stops before MyOS installs its own exception vectors

| Aspect | Status | Evidence |
|---|---|---|
| Inherited `VBAR_EL1` is PongoOS's vector at the moment of entry | `PROVEN_BY_SOURCE` | Item 5 |
| Where that vector resolves after MMU-off, and what a synchronous exception would do | `UNVERIFIED` | Same gap as Item 5; a fault in this window could vector to unmapped physical memory (fatal, unobservable) — the source cannot rule this out |
| MyOS installs vectors only after early UART writes (canonical path) or never before C validation (M2-A7 bootstrap) | `PROVEN_BY_SOURCE` | `boot/aarch64/start.S:73-115`; `build/audit/m2-a7/src/bootstrap_entry.S:27-58` |
| WFE-park on success path is designed behavior, not a fault | `PROVEN_BY_SOURCE` + `PROVEN_BY_TEST` | `kernel/main.c:41`; `build/audit/m2-a7/src/bootstrap_entry.S:59-61`; observed timeouts (exit 124) treated per the standing rule only when accompanied by the expected marker and memory evidence |

## 5. Known facts versus assumptions versus unknowns

**Known (source-proven, pinned revision):** the entire static contract above —
`bootr` mechanics, EL1, MMU/data-cache teardown, i-cache invalidation and
barriers, register contract, upload-buffer formula/limits/lifetime, allocator
rules, and the exact addresses MyOS relies on being PC-relative rather than
inherited.

**Known (test-proven, scope-limited):** the MyOS prototype package parser,
validator and relocator behave as specified in the A10/A11 simulator and under
generic QEMU; the canonical kernel reaches `kernel_main` in QEMU `virt`; the
existing regression suite behaves as recorded in Section 9.

**Assumptions (flagged, not proven):** that the inherited `VBAR_EL1` is either
harmless or fatal-but-detectable in the early window; that `x0`'s boot-arg
pointer is dereferenceable after MMU-off; that the allocator-chosen upload
buffer and any MyOS destination are disjoint from live firmware/PongoOS state;
that A8 cache/branch predictor behavior matches the architectural operations
issued. None of these may be used as evidence.

**Unknowns (runtime-only):** every numeric system-register value at the jump;
actual upload VA/PA and boot-arg address/view; allocator page-state snapshot;
actual cleaned cache range; post-cleanup vector resolution; behavior of a real
fault in the early window; any Apple-visible MyOS marker.

## 6. The single most important unresolved blocker

**There is no authorized runtime that executes the pinned PongoOS `bootr`
handoff and exposes CPU/memory state at the jump.** Everything else (vector
usability, upload placement, boot-arg validity, overlap, early-fault behavior)
is a downstream consequence of that one gap: each is `UNVERIFIED` precisely
because no pinned PongoOS execution has ever been observed outside an Apple
board — and physical-device testing is out of scope for this phase (the last
physical attempt, D2 Phase 2 / D3, was blocked at the USB/checkm8 layer on this
host, `D2_PHASE2_REPORT.md`, `D3_REPORT.md`).

## 7. Minimum evidence needed to resolve the blocker

One trace, produced by an authorized environment, that executes the **pinned**
revision through the `bootr` path and records, immediately before
`jump_to_image_extended`:

1. `CurrentEL`, `SCTLR_EL1`, `VBAR_EL1`, `TTBR0_EL1`, `TTBR1_EL1`, `MAIR_EL1`,
   `TCR_EL1`, `DAIF`.
2. `loader_xfer_recv_data` in both views (cacheable VA and
   `VA - 0x400000000 + 0x800000000`), its allocation size, and the allocator's
   page-state ranges (Pongo image, page list, early heap, boot args, device
   tree, framebuffer).
3. `gBootArgs` pointer value and the physical bytes it resolves to.
4. Confirmation that the jump executes the documented barrier/`ic iallu`
   sequence and lands on upload-buffer offset 0 at EL1 with the MMU off.
5. A deliberate single-step over the first ~100 MyOS bootstrap instructions,
   including a deliberate early fault test (or an explicit observation that the
   inherited vector does not resolve) if the environment allows it safely.

## 8. Exact next steps (with prerequisites, expected observations, pass/fail)

The environment evaluation (Section 9, Phase-3 checks) found: local QEMU
11.1.1 has no Apple machine (re-verified); `checkra1n`, `palera1n`,
`pongoterm`, `lldb`, `xcrun`/Xcode, `darling`, and any Corellium client are
absent; the only documented PongoOS execution path in the pinned tree is
`checkra1n -k Pongo.bin` on a real Apple board (`pongoos-src/README.md`),
which is a physical test and out of scope here. Corellium's published
documentation (`SOURCE_VERIFIED` via M2-A15 §3D and a confirmatory search this
audit) supports virtual iPhone devices starting from iBoot/custom BootROM with
register/memory debugging and LLDB, but **no authorized Corellium/on-prem
access exists in this workspace**, and no evidence establishes that this exact
checkra1n/PongoOS payload can be loaded there — its compatibility is
`UNVERIFIED`. Therefore:

**Step 1 (recommended, decisive):** obtain authorization and access to one
documented Apple-platform runtime — Corellium (cloud or on-prem) or an
equivalent — that can (a) load a custom pre-boot payload or run a
checkra1n-equivalent path for an A8-class target, and (b) single-step the CPU
with register and physical-memory inspection.
- *Prerequisites:* authorized account/appliance; correctly version-tagged
  PongoOS rebuild (`PONGO_VERSION=2.5.1-4c9b7541` as a make variable, using the
  pinned toolchain at `/home/khoi/myos-toolchain10/usr/bin`, hashes recorded);
  a read-only trace patch (log-only, in a disposable build copy — never in
  `pongoos-src/`); a host-side upload client modeled on
  `pongoos-src/scripts/pongoterm.c`.
- *Expected observation:* Pongo shell reachable, `bootr` accepted, and the five
  Section-7 capture groups recorded.
- *Pass criteria:* all Section-7 values captured, jump sequence matches
  `jump_to_image.S:42-55`, entry at upload offset 0, EL1, MMU off.
- *Fail criteria:* environment cannot load the pinned artifact, or cannot pause
  before the jump; then the environment is rejected and Step 1 must be retried
  with a different authorized runtime — do **not** substitute generic QEMU.

**Step 2 (only after Step 1 passes):** replay the captured state against the
MyOS package validator's assumptions (chosen `load_addr`, stack region, vector
install point) and decide, from measured addresses, whether the early-vector
window (Item 11) needs a fix.

**Step 3 (separately authorized, not part of this phase):** a physical iPhone 6
experiment, only after Steps 1-2 and the D3 USB-layer mitigation are resolved.

No new simulated harness is proposed. Generic QEMU `virt` runs are explicitly
not a substitute and will not be repeated for this purpose.

## 9. Validation performed in this audit (exact commands and exit statuses)

All commands run from `/home/khoi/Projects/myos`; logs under
`build/audit/m2-a16/`.

| Command | Exit | Result |
|---|---:|---|
| `git -C pongoos-src rev-parse HEAD` / `git -C pongoos-src status --porcelain` | 0 / 0 (empty) | `4c9b7541629234147fcc778f0ce4162482aaccef`, clean |
| `git -C pongoos-src/newlib rev-parse HEAD` / `status --porcelain` | 0 / 0 (empty) | `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`, clean |
| `qemu-system-aarch64 -machine help \| grep -iE "iphone\|apple\|pongo\|t7000\|t8010\|s8000"` | 1 (no match) | no Apple machine in QEMU 11.1.1 |
| `sha256sum` of the five A12 Pongo artifacts | 0 | all match `M2_A14` recorded hashes (`ARTIFACT_VERIFIED`) |
| `timeout 30s make qemu` | 0 | PASS; `[OK] KERNEL_MAIN`, `MYOS_KERNEL_PASS` path; QEMU parked until timeout — treated as parked-guest timeout per the standing rule (`make_qemu.log`) |
| `make m2-check` | 0 | PASS; 31/31 M2 negative tests; load-plan result explicitly labeled HYPOTHETICAL (`make_m2_check.log`) |
| `bash tests/test_boot_diag.sh` | 0 | PASS; all offline checks pass |
| `bash tests/offline_m1.sh` | **0** | **Divergence from prior records, reported as-is:** this run printed `PASS_m1ctl_usage` and `== result: ALL_PASS ==`. The recorded `FAIL_m1ctl_usage` did not reproduce; `build/m1ctl` exists and exits 64 (`tests/offline_m1.sh:84-86`), which is the pass condition. Nothing was modified to cause this; the difference is environment state (present/working `build/m1ctl`), not a relabel. |
| `bash tests/test_boot_diag_extended.sh` | 1 | Known failure preserved and reproduced: `[WARN] .text.vectors section not found` + `pipefail` (`test_boot_diag_extended.log`) |
| `diff` of A10/A13 fixture hashes (40 files) before/after | 0 | fixtures untouched |
| `diff` of prior-report SHA-256 manifest (17 files) before/after | 0 | previous evidence artifacts not overwritten |
| Final `git status --short` | — | identical pre-existing set plus only new `M2_A16_HANDOFF_EVIDENCE_AUDIT.md` and ignored `build/audit/m2-a16/` evidence files |

Evidence tiers actually exercised here: static analysis (PongoOS + MyOS source
with line citations), generic QEMU tests (`make qemu`, `make m2-check` —
explicitly *not* Apple-platform evidence), and previously recorded synthetic
(A13, `SIMULATED`) and simulator (A10/A11, `OBSERVED_IN_QEMU`) evidence reviewed
but not re-run or modified. **Apple-compatible runtime tests: none available.
Physical iPhone tests: none performed.**

## 10. Changed files

Created only:

- `M2_A16_HANDOFF_EVIDENCE_AUDIT.md` (this report)
- `build/audit/m2-a16/` (ignored evidence: `preflight.txt`,
  `evidence_hashes_{before,after}.txt`, `ab_{before,after}.sha256`,
  `make_qemu.log`, `make_m2_check.log`, `test_boot_diag.log`,
  `offline_m1.log`, `test_boot_diag_extended.log`)

`make qemu` / `make m2-check` regenerated ordinary build outputs under
`build/` (kernel ELF/bin, `build/m1ctl`) as part of running the existing
regression targets; no fixture, package, report, or source file was modified.
`pongoos-src/` and `pongoos-src/newlib` remain clean at their pinned
revisions.

## 11. Final recommendation

`STOP_AT_EVIDENCE_AUDIT`

The handoff contract is as well-established as static analysis allows, and the
repository, revisions, and prior evidence are intact. The next unit of progress
is not another simulation — it is one authorized Apple-compatible runtime trace
of the pinned `bootr` path as specified in Sections 7-8. If no such runtime can
be authorized, the honest status is to remain at this audit: **no GO, no
hardware attempt, no claim that MyOS boots on an iPhone.**
