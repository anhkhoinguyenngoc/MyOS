# M2-A10 — PongoOS Runtime Handoff Audit

**Audit date:** 2026-10-10  
**Scope:** read-only source audit of the pinned PongoOS `bootr` path and comparison with the existing M2-A9/M2-A10 prototype.  
**Recommendation:** `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

This audit did not modify PongoOS, the canonical kernel, M1, the M2-A9/M2-A10 tooling, or existing tests. No physical device was accessed.

## 1. Initial repository and submodule state

The repository root is `/home/khoi/Projects/myos`.

| Item | Recorded value |
|---|---|
| HEAD | `3bea11141fea05bb90017069cff22b9a37d91e9e` |
| Branch | `main` |
| PongoOS submodule | `pongoos-src @ 4c9b7541629234147fcc778f0ce4162482aaccef` |
| PongoOS worktree | clean |
| Pre-existing tracked changes | `Makefile`; `arch/aarch64/boot_diag.c`; `arch/aarch64/boot_diag.h`; `arch/aarch64/vectors.S` |
| Pre-existing untracked work | M2-A4 through M2-A10 reports and the previously listed `arch/`, `boot/`, `fix_runner.py`, `include/`, `kernel/`, and `tests/` files |

The initial `git status --short`, HEAD, and submodule revision were recorded before source inspection. The pre-existing changes were preserved.

## 2. Files and source ranges inspected

### PongoOS, read-only

- `pongoos-src/Makefile:53-59,65-99` — pinned link image base and build inputs.
- `pongoos-src/src/boot/entry.S:27-130` — stage entry, `CurrentEL` handling, EL3-to-EL1 transition, and stacks.
- `pongoos-src/src/boot/stage3.c:105-150` and `src/boot/main.c:27-39` — stage-3 handoff into `pongo_entry`.
- `pongoos-src/src/shell/main.c:40-48,248-277` — `bootr`, command registration, and USB upload initialization.
- `pongoos-src/src/shell/command.c:104-124,135-164` — command dispatch and yielding command task.
- `pongoos-src/src/shell/usbloader.c:32-99,105-117` — upload allocation, resize, DMA address translation, and ownership.
- `pongoos-src/src/kernel/pongo.h:77-104,125-130,233-269,341-345,483-494` — `boot_args`, flags, address-view constants, and low-level interfaces.
- `pongoos-src/src/kernel/entry.c:30-63,123-234,285-325` — Pongo setup, scheduler wait, teardown, and raw branch.
- `pongoos-src/src/kernel/lowlevel.c:32-128,627-667` — EL read, vector setup, MMU controls, and cache maintenance.
- `pongoos-src/src/kernel/mm.c:269-348,619-903` — RAM mappings, allocator metadata, physical allocator, and cacheable/physical aliases.
- `pongoos-src/src/kernel/task.c:259-281,659-680` and `src/kernel/int.S:320-405,463-493` — task yield/switch state and EL1 task entry.
- `pongoos-src/src/kernel/main_task.c:31-107` — shell task registration and execution.
- `pongoos-src/src/drivers/usb/synopsys_otg.c:1866-1870` — USB teardown; it does not free the upload buffer.
- `pongoos-src/src/boot/jump_to_image.S:27-55` — final register setup, barriers, instruction-cache invalidation, and `ret`.

### MyOS and audit evidence

- `build/audit/m2-a7/src/bootstrap_entry.S:24-52` and `bootstrap.ld:16-47` — raw entry, package-relative stack, BSS, and linker symbols.
- `build/audit/m2-a7/src/bootstrap.c:130-170,268-350,366-572` — package validation, ELF/relocation validation, writes, BSS, canary, and kernel handoff.
- `build/audit/m2-a7/src/sim_bootr.S:1-69` and `sim_bootr.ld:1-36` — QEMU-only handoff simulator, not PongoOS runtime.
- `build/audit/m2-a7/tools/make_package.py`, `make_fixture.py`, `make_negcases.py`, and `run_m2a7.py` — existing package and test infrastructure reviewed as provenance for the latest results.
- `build/audit/m2-a10/m2a10lib.py`, `run_case.py`, `run_suite.py`, and `negcontrol/make_injected_control.py` — isolated follow-up evidence tooling reviewed, not changed.
- `boot/aarch64/start.S:40-120,202-212`, `kernel/main.c:18-42`, and `boot/aarch64/kernel.ld:16-32` — current prototype kernel assumptions.
- `M2_A10_PONGO_INTEGRATION_REPORT.md`, `M2_A9_RAW_PAYLOAD_REPORT.md`, and `M2_A9_MEMORY_INTEGRITY_AUDIT.md` — previous findings and latest reported test status.

## 3. Handoff timeline

1. Stage 3 receives the boot arguments and image entry. `trampoline_entry()` stores them and calls `_setup_el1(main, ...)` (`stage3.c:123-149`). `_setup_el1` accepts an EL1 entry directly or executes `eret` from EL3 with `SPSR_EL3 = 4` (`entry.S:84-109`). `main()` selects the kernel boot arguments and calls `pongo_entry()` (`boot/main.c:31-39`).
2. `pongo_entry()` stores `gBootArgs` and `gEntryPoint`, calls `lowlevel_setup()`, rebases Pongo execution state, and enters `pongo_entry_cached()` (`kernel/entry.c:294-308`).
3. `lowlevel_setup()` builds the Pongo page tables, maps RAM and Pongo text/data, checks `get_el() == 1`, sets `VBAR_EL1`, and enables the EL1 MMU (`mm.c:285-338`).
4. `pongo_entry_cached()` installs its vector again, creates the scheduler/main task, starts drivers and the shell, and waits while `gBootFlag == 0` (`entry.c:123-229`).
5. The shell registers `bootr` at `main.c:257`. The command handler checks `loader_xfer_recv_count`, clears only that count, sets `gBootFlag = BOOT_FLAG_RAW` (`4`), and calls `task_yield()` (`main.c:40-48`). The command task yield is a scheduler context switch; it does not free or relocate `loader_xfer_recv_data` (`task.c:674-680`, `int.S:320-405`).
6. The scheduler loop observes the nonzero boot flag, then disables the timer, tears down USB/interrupt activity, and exits the cached Pongo loop (`entry.c:210-234,264-283`). `usb_teardown()` only writes USB registers (`synopsys_otg.c:1866-1870`).
7. `pongo_entry()` calls `lowlevel_set_identity()`, restores the execution-state addresses, assigns the framebuffer, and calls `lowlevel_cleanup()` (`entry.c:303-308`). `lowlevel_set_identity()` maps the physical RAM alias executable and flushes TLBs (`mm.c:339-343`). `lowlevel_cleanup()` cleans/invalidate RAM and disables the EL1 MMU (`mm.c:344-348`).
8. For `BOOT_FLAG_RAW`, Pongo computes `image = loader_xfer_recv_data - kCacheableView + 0x800000000` and calls `_jump_to_image_extended(image, gBootArgs, gEntryPoint)` (`entry.c:309-312`).
9. `_jump_to_image_extended` sets `x30 = image`, `x0 = boot_args`, `x8 = gEntryPoint`, clears `x1..x3`, executes `isb; dsb sy; ic iallu; dsb sy; isb`, and returns to `x30` (`jump_to_image.S:42-55`). The first raw-payload instruction is `_bootstrap_entry` at upload-buffer offset zero (`bootstrap_entry.S:24-29`).

No call site after the raw branch returns to `exit_to_el1_image`; the `ret` is the terminal Pongo-to-raw transfer for this path.

## 4. Runtime-state findings

| Property | Classification | Source-backed result and limit |
|---|---|---|
| `CurrentEL` | **GUARANTEED** for a successful source path | `_setup_el1` accepts only encoded EL1 (`0x4`) or EL3 (`0xc`); EL3 is changed to EL1 with `SPSR_EL3=4` (`entry.S:89-109`). `lowlevel_setup` then panics unless `get_el()==1` (`mm.c:331-334`). No later instruction changes exception level before the raw `ret`. This is static source evidence, not a runtime register capture. |
| `VBAR_EL1` ownership | **GUARANTEED** inherited Pongo vector | `lowlevel_setup` and `pongo_reinstall_vbar` set `VBAR_EL1` to Pongo's `exception_vector` (`mm.c:331-334`, `entry.c:158-163`; `lowlevel.c:62-65`). There is no replacement before raw entry and `lowlevel_cleanup` does not change it. |
| `VBAR_EL1` fault usability after MMU-off | **UNKNOWN** | Pongo links at `0x100000000` (`Makefile:58`) and creates a translation for that view (`mm.c:325-327`), then disables translation (`mm.c:344-348`). The source does not prove that the inherited vector address is executable/accessible in the post-cleanup address regime. A fault before MyOS installs its own vectors can therefore be fatal or vector unexpectedly. |
| MMU at raw entry | **GUARANTEED** on the successful path | `disable_mmu_el1` clears `SCTLR_EL1.M` and performs TLBI/barriers (`lowlevel.c:117-128`); the raw call follows it (`entry.c:308-312`). Pongo first maps the physical RAM alias executable (`mm.c:339-343`). |
| Data-cache state | **GUARANTEED** only for the documented RAM range | `cache_clean_and_invalidate` uses `dc civac` over `ram_phys_off..ram_phys_off+ram_phys_size`, with `dsb sy`/`isb` (`lowlevel.c:641-653`), then `disable_mmu_el1` clears `SCTLR_EL1.C` (`lowlevel.c:117-128`). This does not prove anything about non-RAM/device cache state or a physical cache hierarchy beyond the architectural operations issued. |
| Instruction-cache state | **GUARANTEED** invalidation; enabled bit **UNKNOWN** | Pongo executes `ic iallu` with barriers in both cleanup and the final jump (`lowlevel.c:117-128`, `jump_to_image.S:47-51`). It does not clear `SCTLR_EL1.I`, so “I-cache disabled” is not source-guaranteed. |
| Barriers | **GUARANTEED** as issued | The final sequence is `isb; dsb sy; ic iallu; dsb sy; isb` (`jump_to_image.S:47-51`). These order/invalidate the architectural instruction stream; `ic iallu` is not a data-cache clean and does not establish allocator or vector safety. |
| Upload-buffer view | **GUARANTEED by source, runtime location variable** | `alloc_contig()` returns `phystokv(alloc_phys(size))` (`mm.c:844-889`); `phystokv(pa) = pa - 0x800000000 + kCacheableView` (`mm.c:893-895`). USB DMA separately converts the cached pointer with `vatophys` (`usbloader.c:89,111`). |
| Upload size/alignment | **GUARANTEED by source** | Initial upload allocation is `UPLOADSZ = 1 MiB`; resize is capped at `UPLOADSZ_MAX = 128 MiB`, rounded to 0x200 for transfers, and physical allocation rounds to the 16-KiB page size (`usbloader.c:32-33,66-89`; `mm.c:844-889`). |
| Upload lifetime through handoff | **GUARANTEED for this path** | `bootr` clears `loader_xfer_recv_count` but not the pointer or allocation (`main.c:40-48`). `usb_teardown()` does not free it (`synopsys_otg.c:1866-1870`), and the raw branch uses the pointer immediately (`entry.c:309-312`). |
| `boot_args` register | **GUARANTEED as a passed pointer; usable contents after MMU-off UNKNOWN** | `_jump_to_image_extended` passes `gBootArgs` in `x0` (`entry.c:296-297,309-312`; `jump_to_image.S:44-46`). Pongo does not construct a raw-payload copy. The pointer's address-view validity after translation is disabled is not established. MyOS deliberately ignores `x0`. |
| `physBase`, `memSize`, `topOfKernelData` meaning | **GUARANTEED only at the documented field/use level** | `pongo.h:81-84` documents physical base, RAM size, and highest kernel-data physical address. Pongo uses `physBase`/`memSize` for mappings and page count, and `topOfKernelData` to start static allocation (`entry.c:127-130,298`; `mm.c:797-832`). Actual device values and completeness of the supplied reservation are **UNKNOWN**. |
| Allocator exclusion of Pongo code/metadata | **PARTIALLY GUARANTEED, overall safety UNKNOWN** | `alloc_init()` wires all pages, places `ppage_list` after the early heap, and frees only selected aligned ranges (`mm.c:794-833`). `alloc_phys()` scans `PAGE_FREE` pages, unlinks them, and references them (`mm.c:844-882`). This protects pages represented as wired/allocated by this allocator, but the source does not prove that every live boot argument, FDT, framebuffer, or firmware-owned object is represented in those ranges. |
| Upload-buffer non-overlap with all live state | **UNKNOWN** | The buffer is an allocated contiguous region, but no source-backed complete memory map proves it cannot overlap every live Pongo/boot object. The QEMU simulator embeds a package at fixed test addresses and does not exercise `alloc_contig()` or the real boot-argument memory map. |

### Address translation conclusion

Before cleanup, `loader_xfer_recv_data` is a cacheable-view pointer. Pongo's raw target calculation converts it to the `0x800000000 + physical-offset` alias. Pongo maps that alias executable before disabling the MMU. Consequently, the source path is consistent with a physical-address/identity-alias raw entry, not with a package running through the cached `0x400000000` view. The bootstrap's `adr`-derived base is therefore appropriate for the source path, but the actual physical address remains allocation-dependent.

## 5. Comparison with the current MyOS bootstrap

### Source-backed compatible assumptions

- `bootstrap_entry.S:27-49` derives the package base from its own PC using `adr`, derives `_stack_top` the same way, places SP at `stack_top - 0x2200`, zeros its own BSS, and copies the package header to the reserved stack buffer. This does not rely on Pongo's `x0`; the bootstrap's `x0` is overwritten with its PC-derived base.
- `bootstrap.ld:16-47` links at zero and makes `_stack_bottom`, `_stack_top`, and `_stack_canary` package-relative. The package builder checks those symbols and the flat image entry at offset zero.
- `bootstrap.c:272-350` validates package/header/payload bounds and CRC before phase 2. `bootstrap.c:366-509` validates all ELF segments and relocation records before phase 3 writes. `bootstrap.c:512-528` applies relocations; `bootstrap.c:530-569` initializes BSS, checks the canary, and calls the relocated entry with `base`.
- The bootstrap treats the raw package base, package header, payload, relocation targets, and stack as addresses in the post-MMU-off physical alias. That matches the source-level Pongo conversion above.

### Unverified or incompatible assumptions

- The bootstrap comment calls `x0` a cached-view `boot_args` pointer, but it ignores it. The source does not prove that this pointer remains usable after MMU disable; this is harmless only because MyOS does not dereference it.
- `bootstrap.c:40-50` hardcodes QEMU virt PL011 at `0x09000000`. The Pongo source audit does not establish that address on an Apple target. An exception or inaccessible MMIO access before MyOS installs vectors is not safely handled.
- MyOS installs `VBAR_EL1` only after early UART activity (`boot/aarch64/start.S:70-117`). Thus its own vector table is not available for faults during the first UART writes, early setup, header copy, or bootstrap validation. It also assumes the inherited Pongo vector state is usable until installation; that is UNKNOWN.
- `boot/aarch64/start.S:44-52` accepts EL1 or EL2, while the successful Pongo `bootr` path is source-constrained to EL1. The EL1 case is compatible; the EL2 branch is not evidence for Pongo and is not exercised by the Pongo path.
- The current kernel uses QEMU-specific PL011 output and parks in `wfe` (`kernel/main.c:18-42`). Its successful QEMU marker proves the simulator path, not a device UART or physical-device runtime.
- The bootstrap intentionally writes relocation targets and possible BSS on a valid package. The package-range checks prove only package-relative bounds; they do not prove that those physical pages are safe relative to all PongoOS live allocations.

## 6. Evidence classification

### GUARANTEED by PongoOS source

Successful raw handoff is at EL1; `bootr` does not parse the upload; the raw target is upload-buffer offset zero through the physical alias; `x0` is the original `gBootArgs`, `x1..x3` are zero, and `x8` is `gEntryPoint`; Pongo issues the stated barriers and instruction-cache invalidation; the documented RAM range is cleaned/invalidate and MMU/data-cache enable bits are cleared before the final jump; the upload pointer is not freed by `bootr`/USB teardown; and the uploader allocates tracked contiguous physical pages.

### OBSERVED_IN_QEMU

The isolated QEMU simulator reached the bootstrap and valid kernel marker at package bases `0x42000000` and `0x44000000`, and the M2-A10 suite recorded exact fixture identity, eight expected rejection messages, protected-range diffs of zero, and the clean/injected controls. This is evidence for the prototype parser/relocator and the isolated register-sequence model. It is **not** evidence that actual PongoOS ran, nor a capture of `CurrentEL`, `VBAR_EL1`, `SCTLR_EL1`, or the real allocator.

### UNKNOWN without additional evidence

The post-cleanup usability of the inherited Pongo vector; exact runtime `boot_args` address/view and values; actual upload physical placement; complete reservation of Pongo code/data, allocator metadata, boot arguments, device tree, framebuffer, and firmware-owned memory; physical cache behavior beyond the issued architectural operations; and compatibility of QEMU PL011 accesses with an Apple target.

## 7. Latest reported test and regression status

These results are recorded from the latest `M2_A10_PONGO_INTEGRATION_REPORT.md`; they were **not rerun in this read-only audit**.

| Existing command/evidence | Reported result |
|---|---|
| `python3 build/audit/m2-a10/run_suite.py` | exit 0; 8/8 malformed cases, clean control, injection control, bypass control, and reproducible fixtures passed their intended assertions |
| Eight malformed cases | all reached the expected rejection path/message; zero protected-range violations in every case; scratch-only differences were in `img` `[0x1648,0x1720)` |
| Clean control | PASS; `MYOS_KERNEL_PASS`; 35/35 relocation slots; zero protected violations |
| Injection control | FAIL as intended at package offset `0x5000`, address `0x42005000`, observed marker `0x4d3241314d41524b` |
| Injection bypass control | FAIL as intended with the eight injected bytes bypassed; this demonstrates the checker is byte-driven rather than log-driven |
| Valid package at `0x42000000` | exit 0; `MYOS_KERNEL_PASS`; 35/35 relocations; exact fixture verified |
| Valid package at `0x44000000` | exit 0; `MYOS_KERNEL_PASS`; 35/35 relocations; exact fixture verified |
| `make qemu` | exit 0; canonical marker and pass marker observed |
| `make m2-check` | exit 0; host M2 checks reported 31/31 |
| `python3 tests/test_m2_negative.py` | exit 0; 31/31 |
| `bash tests/offline_m1.sh` | exit 1; existing `FAIL_m1ctl_usage` only |
| `bash tests/test_boot_diag.sh` | exit 0 |
| `bash tests/test_boot_diag_extended.sh` | exit 1; existing missing `.text.vectors`/line-111 `pipefail` failure |

QEMU exit `124` in the existing case runner is a timeout/parked-guest result, not a clean guest exit. It was accepted only where the corresponding expected rejection or success marker and independent memory evidence were also present.

### Existing fixture evidence

All eight regenerated fixtures were recorded with their exact path, size, and SHA-256:

| Case | Package size / SHA-256 | Fixture path | Fixture size / SHA-256 |
|---|---:|---|---:|
| `trunc_header` | 8,191 / `8709b4e4fff34cc5a880f532e50a6cad7c06fabe3e57ff2ddeea9421ddd73a2a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_trunc_header.elf` | 140,424 / `e02d6295ee3fa15cbb2d4a6897fe6a861dac4ea440cea44621fed67d16bafe89` |
| `trunc_payload` | 196,608 / `d038ba56399346f6fe34fc7420198547c895f247b30bf757484ef7353aeb6e0a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_trunc_payload.elf` | 328,840 / `8cb2f450724251bd7253d42d489442f5639a1f7b449133b4f22ffee08f9764db` |
| `seg_bounds` | 311,296 / `63a8e9d8b50c2d8b232c23d7acc862230608bd31b895c34088b2898e03335f1b` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_seg_bounds.elf` | 443,520 / `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8` |
| `overflow` | 311,296 / `2497af0b2e651d85c44e9a45bee445b136e8f19363d3e102f904fa89a134eaad` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_overflow.elf` | 443,520 / `86e5b8d3d8448a571cde69f56e74b989ad67fc01663cfa16e134bedcea1d27fc` |
| `bad_reloc_type` | 311,296 / `9f3790a2ed921d14922b176a7c8551b3167fafbac88e6bb27e144046347763db` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_bad_reloc_type.elf` | 443,536 / `9ca1a7560a3d9974aa67a2d5c53d1a9d2a9a708dde3c0af0dc7a87b5b601f620` |
| `reloc_oob` | 311,296 / `bebb0e179d7c9bc99125e0259f77632dde18e37fda8649e5a30f4312585e56ff` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_reloc_oob.elf` | 443,520 / `c4fb2be0768f9222b4ba2cae1f4dcb11f7863dc1e1df2f0c4fcd1adb63038a0f` |
| `entry_oob` | 311,296 / `2b79f3463135c038c74aa50136559c6bea927418f45f0c2cc10786586a75465a` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_entry_oob.elf` | 443,520 / `493e037b35ba3b530a82ecf84140337273038183abcbd2557adf6bce58777fc4` |
| `bss_invalid` | 311,296 / `667ef1d14513f681d424be5056c650fe9331fbaf96fe2d14e17d1cf643140c61` | `build/audit/m2-a10/fixtures/fixture_42000000_neg_bss_invalid.elf` | 443,528 / `05d71a7a1059d88fa44cea2cc57118cd723bc4d05861feeec8d7d2f10ba07012` |

Reproducibility evidence: the `seg_bounds` fixture was built twice from identical input and both hashes were `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8`.

## 8. Eight-case rejection summary

| Case | Expected/observed rejection | Protected diff summary |
|---|---|---|
| `trunc_header` | `package header magic mismatch (truncated/corrupt package)` | 0 protected bytes; 0 scratch bytes |
| `trunc_payload` | `payload checksum mismatch (truncated/corrupt payload)` | 0 protected bytes; 0 scratch bytes |
| `seg_bounds` | `PT_LOAD file range outside payload bounds` | 0 protected; 7 `img` scratch bytes |
| `overflow` | `PT_LOAD memory footprint outside payload bounds` | 0 protected; 7 `img` scratch bytes |
| `bad_reloc_type` | `unsupported relocation type` | 0 protected; 30 `img` scratch bytes |
| `reloc_oob` | `relocation target outside permitted memory` | 0 protected; 30 `img` scratch bytes |
| `entry_oob` | `entry point outside executable memory` | 0 protected; 19 `img` scratch bytes |
| `bss_invalid` | `invalid BSS range (p_filesz > p_memsz)` | 0 protected; 7 `img` scratch bytes |

The protected ranges were independently checked as: bootstrap image outside scratch, bootstrap tail padding, package header `[0x2000,0x2200)`, header padding, payload, every named relocation target, payload tail padding through `stack_off`, and BSS. The only excluded writable scratch was the current ELF-symbol-derived `img` range `[0x1648,0x1720)`; the bootstrap BSS is empty at `__bss_start == __bss_end == 0x1810`. The package stack `[0x48000,0x4c000)` is tracked separately.

## 9. Remaining blockers and smallest safe next action

The tooling evidence is complete, but these runtime handoff blockers remain:

1. A source-backed inherited `VBAR_EL1` is not a guaranteed usable vector after MMU disable. MyOS installs its vectors too late to remove the early-fault risk.
2. Actual runtime `boot_args` address/view and values are not captured.
3. The exact physical upload-buffer placement and complete non-overlap with PongoOS code/data, allocator metadata, boot arguments, device tree, framebuffer, and firmware-owned ranges are not proven.
4. QEMU simulator evidence does not execute the pinned PongoOS binary or observe its system registers.
5. The existing unrelated `FAIL_m1ctl_usage` and `.text.vectors` regression failures remain; they were not changed.

The smallest safe next experiment is an isolated **offline or QEMU PongoOS-instrumented handoff trace** using the pinned revision, limited to logging `CurrentEL`, `VBAR_EL1`, `SCTLR_EL1`, `TTBR0_EL1/TTBR1_EL1`, the cacheable and physical upload addresses, and the allocator's page-state ranges immediately before `jump_to_image_extended`. It must not use a physical device and must not modify the canonical MyOS path. Until that evidence exists, do not attempt hardware testing.

## 10. Final recommendation

`CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

The source audit justifies another isolated QEMU/offline runtime-instrumentation experiment: the successful PongoOS raw path is EL1, has an explicit MMU/data-cache cleanup, and has a precisely defined register handoff. It does not justify a hardware experiment because vector usability, actual runtime memory placement, and complete allocator reservation remain unresolved. This report makes no claim that MyOS boots on iPhone 6.

## 11. Commands executed for this read-only audit

Repository/source inspection commands included:

```text
pwd -P
git status --short
git rev-parse HEAD
git -C pongoos-src rev-parse HEAD
git -C pongoos-src status --short
rg --files pongoos-src/src
rg -n 'loader_xfer|gBootFlag|BOOT_FLAG_RAW|boot_args|jump_to_image|...'
nl -ba <inspected source> ...
jq/stat on existing build/audit/m2-a10 logs and summary.json
```

No build, QEMU run, regression test, package mutation, Git write operation, submodule operation, or physical-device operation was performed for this audit.

## 12. Exact changed files

Created only:

- `M2_A10_RUNTIME_HANDOFF_AUDIT.md`

All other tracked and untracked user work, the PongoOS submodule, canonical kernel, M1, M2-A9 bootstrap, M2-A10 harness, and existing test scripts were preserved.
