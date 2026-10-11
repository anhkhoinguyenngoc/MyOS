# M2-A11 — Instrumented PongoOS Handoff Trace

Date: 2026-10-10

Final recommendation: `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

This report is a read-only audit of the pinned PongoOS source plus isolated
generic-QEMU evidence. No PongoOS binary was executed, no protected source was
changed, and no physical device was accessed. The generic QEMU probe is
explicitly not treated as an iPhone/PongoOS runtime measurement.

## 1. Baseline and preservation

Repository root, verified with `git rev-parse --show-toplevel`:

```text
/home/khoi/Projects/myos
HEAD 3bea11141fea05bb90017069cff22b9a37d91e9e
PongoOS submodule 4c9b7541629234147fcc778f0ce4162482aaccef
```

Initial `git status --short` was preserved exactly:

```text
 M Makefile
 M arch/aarch64/boot_diag.c
 M arch/aarch64/boot_diag.h
 M arch/aarch64/vectors.S
?? M2_A10_PONGO_INTEGRATION_REPORT.md
?? M2_A10_RUNTIME_HANDOFF_AUDIT.md
?? M2_A4_MEMORY_SAFETY_AUDIT.md
?? M2_A5_PIC_AUDIT.md
?? M2_A6_SECOND_BASE_REPORT.md
?? M2_A7_RAW_PAYLOAD_REPORT.md
?? M2_A8_BOOTSTRAP_STACK_FIX_REPORT.md
?? M2_A9_BOOTSTRAP_STACK_FIX_REPORT.md
?? M2_A9_MEMORY_INTEGRITY_AUDIT.md
?? M2_A9_RAW_PAYLOAD_REPORT.md
?? M2_REPORT.md
?? arch/aarch64/uart_pl011.S
?? boot/aarch64/kernel.ld
?? boot/aarch64/start.S
?? fix_runner.py
?? include/
?? kernel/
?? tests/make_m2_image.py
?? tests/sim_mmap.py
?? tests/test_m2_negative.py
?? tests/validate_m2.py
```

`git -C pongoos-src status --short` was empty both before and after the
audit. The pre-existing tracked and untracked changes above were not edited.

## 2. Source trace and exact execution path

The pinned source excerpts and SHA-256 values are captured in
`build/audit/m2-a11/logs/static_trace.txt` and
`build/audit/m2-a11/logs/source_hashes.json`.

1. `src/shell/main.c:257` registers `bootr` as `pongo_boot_raw`.
   `src/shell/command.c:104-113` dispatches a received command to that
   callback.
2. `src/shell/main.c:40-48` checks `loader_xfer_recv_count`, clears only that
   count, sets `gBootFlag = BOOT_FLAG_RAW` (`4` from
   `src/kernel/pongo.h:125-130`), and calls `task_yield()`.
3. The upload buffer is initialized by `src/shell/main.c:269` through
   `src/shell/usbloader.c:94-99`. `UPLOADSZ` is 1 MiB and
   `UPLOADSZ_MAX` is 128 MiB (`usbloader.c:32-33`). Resizing rounds the
   requested size to 512 bytes and allocates contiguous pages with
   `alloc_contig()` (`usbloader.c:66-89`). The completed-transfer callback
   invalidates the received range and records the transferred count
   (`usbloader.c:42-47`).
4. `task_yield()` is `src/kernel/task.c:674-680`; the asserted context switch
   saves/restores the scheduler state and `TTBR1_EL1` in
   `src/kernel/int.S:320-405`. The cached Pongo loop in
   `src/kernel/entry.c:209-229` observes the nonzero `gBootFlag`, then disables
   the timer, tears down USB, disables interrupts, and finishes teardown at
   lines 231-278.
5. `src/boot/stage3.c:123-150` establishes `gboot_args` and
   `gboot_entry_point`, clears Pongo BSS, and enters `main`. The kernel branch
   in `src/boot/main.c:31-38` calls `pongo_entry()`.
6. `src/kernel/entry.c:294-302` saves `gBootArgs` and `gEntryPoint`, calls
   `lowlevel_setup()`, rebases Pongo into its cacheable mapping, and enters
   `pongo_entry_cached()`. `src/kernel/entry.c:123-163` maps RAM and installs
   Pongo's EL1 exception vector. The scheduler loop then reaches the raw
   branch.
7. `src/kernel/entry.c:303-308` changes the identity mapping, restores the
   Pongo code rebase, and calls `lowlevel_cleanup()`. That cleanup performs
   data-cache clean/invalidate over Pongo's calculated RAM range and disables
   the MMU.
8. For `BOOT_FLAG_RAW`, `src/kernel/entry.c:309-312` invokes:

   ```c
   jump_to_image_extended(
       ((uint64_t)loader_xfer_recv_data) - kCacheableView + 0x800000000,
       (uint64_t)gBootArgs,
       (uint64_t)gEntryPoint);
   ```

   `src/boot/jump_to_image.S:42-55` then sets `x30` to the first raw-payload
   address, moves `gBootArgs` to `x0`, moves `gEntryPoint` to `x8`, clears
   `x1..x3`, executes `isb; dsb sy; ic iallu; dsb sy; isb`, and returns into
   the upload buffer. The source does not specify the remaining general
   registers.

9. The first raw-payload instruction is therefore the first instruction at
   upload-buffer offset zero. In the current MyOS prototype, the equivalent
   isolated entry is `build/audit/m2-a7/src/bootstrap_entry.S:27-50`: it uses
   `adr` to discover its runtime base, derives its stack from `_stack_top`,
   zeros its own BSS, copies the package header to the stack, and calls
   `bootstrap_main()`.

## 3. Trace environment and fidelity

The checked-out PongoOS Makefile is an Apple-target build:

- `pongoos-src/Makefile:31-59` selects `clang`, `ld64`, and
  `cctools-strip` on Linux, targets `arm64-apple-ios12.0`, and links a
  Mach-O/preload image at `0x100000000`.
- `pongoos-src/Makefile:65-99` produces `Pongo`, `Pongo.bin`, and a
  consolidated Apple boot image.
- `pongoos-src/README.md:3,12-40` describes an Apple-board pre-boot
  environment and requires that Apple-target toolchain.

This environment has `/usr/bin/qemu-system-aarch64` and the
`aarch64-linux-gnu-*` cross tools, but `clang`, `ld64`, and `cctools-strip`
were not present. Building PongoOS was not attempted because it could invoke
the submodule build/update path and would not produce a generic `virt` image
faithful to an Apple board.

The existing M2-A7/M2-A10 QEMU fixture is explicitly a simulator:
`build/audit/m2-a7/src/sim_bootr.S:2-31` reproduces only the final register
contract and `build/audit/m2-a7/sim_bootr.ld:4-23` models the package as RWX
memory with the MMU off. It does not execute Pongo's allocator, Apple drivers,
boot-argument construction, vector setup, or MMU teardown.

An additional isolated probe was built from
`build/audit/m2-a11/qemu-probe/env_probe.S` and run with:

```text
build/audit/m2-a11/qemu-probe/build_probe.sh
timeout 3 qemu-system-aarch64 -machine virt -cpu cortex-a57 -nographic \
  -monitor none \
  -serial file:build/audit/m2-a11/logs/qemu_env_probe.log \
  -kernel build/audit/m2-a11/qemu-probe/env_probe.elf
```

The QEMU probe returned 124 because it deliberately parks in `wfe`. Its raw
log is `build/audit/m2-a11/logs/qemu_env_probe.log`:

```text
CurrentEL(raw)=0x0000000000000004
VBAR_EL1=0x0000000000000000
SCTLR_EL1=0x0000000000c50838
TTBR0_EL1=0x0000000000000000
TTBR1_EL1=0x0000000000000000
QEMU_ENV_PROBE_ONLY
```

These values describe QEMU's generic `-kernel` entry environment only. They
are not PongoOS measurements and do not establish iPhone behavior.

**Fidelity result: INCONCLUSIVE for actual PongoOS runtime execution.** The
remaining report is consequently a source-backed static trace, with generic
QEMU values kept separate.

## 4. Handoff state and evidence classification

| Property | Source-backed fact | Evidence classification and unresolved fact |
|---|---|---|
| `CurrentEL` | `src/boot/entry.S:84-109` accepts EL1 directly or transitions EL3 to EL1; `src/kernel/mm.c:331-334` requires `get_el() == 1` before enabling the MMU. No later handoff instruction changes EL. | **GUARANTEED** for the successful Pongo path: raw entry is reached from EL1. The probe's `0x4` is generic-QEMU-only. No actual Pongo runtime capture exists. |
| `VBAR_EL1` | `lowlevel_setup()` sets `VBAR_EL1` to `exception_vector` (`mm.c:331-334`); `pongo_entry_cached()` reinstalls it (`entry.c:158-163`). The final raw path does not replace it. | **GUARANTEED** that Pongo installs/inherits its vector through the handoff; **UNKNOWN** is the final numeric address and whether that vector remains usable after MMU disable/rebase. |
| `SCTLR_EL1` | `_enable_mmu_el1` sets M and C (`lowlevel.c:95-115`); `_disable_mmu_el1` clears M and C but does not reset every other bit (`lowlevel.c:117-128`). | **GUARANTEED** that the source clears the MMU and data-cache-enable bits on the normal raw path; **UNKNOWN** is the complete final register value. The probe's `0xc50838` is not Pongo evidence. |
| MMU / translations | `lowlevel_setup()` writes MAIR/TCR/TTBRs and maps cacheable and identity RAM (`mm.c:285-338`); `lowlevel_set_identity()` changes the RAM mapping, then cleanup disables translation (`mm.c:339-348`). | **GUARANTEED** sequence; **UNKNOWN** final TLB contents, retained TTBR values, and the mapping visible to a payload after MMU off. |
| `TTBR0_EL1`, `TTBR1_EL1` | Pongo writes both in `enable_mmu_el1`; task switching also saves/restores TTBR1. Cleanup does not clear them. | **GUARANTEED** that Pongo programs them before the cached phase; **UNKNOWN** numeric values at raw entry. Generic probe observed both zero only in its unrelated environment. |
| Instruction cache | `jump_to_image_extended` executes `ic iallu` with surrounding barriers (`jump_to_image.S:47-51`). | **GUARANTEED** source sequence. This does not prove data-cache cleanliness by itself. |
| Data cache | `lowlevel_cleanup()` calls `cache_clean_and_invalidate()` (`entry.c:303-308`, `mm.c:344-348`); the helper uses 64-byte `dc civac` lines with `dsb sy`/`isb` (`lowlevel.c:641-653`). | **GUARANTEED** source operation over Pongo's calculated RAM range; **UNKNOWN** is the runtime range and platform effect. |
| Barriers | Final branch sequence is `isb; dsb sy; ic iallu; dsb sy; isb`; cache helper has its own barriers. | **GUARANTEED** ordering/instruction synchronization as written. `isb` does not clean data cache and `dsb` does not create a missing mapping. |
| Upload VA/PA | `alloc_contig()` calls `alloc_phys()` then `phystokv()` (`mm.c:844-902`). `phystokv(pa) = pa - 0x800000000 + kCacheableView`; `kCacheableView` is `0x400000000` (`pongo.h:341-345`). The raw branch converts the upload VA back to the `0x800000000` identity/physical alias. | **GUARANTEED** formula and allocation-size limits; **UNKNOWN** actual VA, PA, alignment extent, and final mapping for the run. |
| Upload size | Default allocation is 1 MiB; resize is capped at 128 MiB and rounded to 512 bytes (`usbloader.c:32-33,66-89`). | **GUARANTEED** limits; **UNKNOWN** actual size selected by the host transfer for a future raw payload. |
| Upload lifetime | `bootr` clears `loader_xfer_recv_count` but not `loader_xfer_recv_data` or `loader_xfer_recv_size` (`main.c:40-48`). `usb_teardown()` only writes USB registers (`synopsys_otg.c:1866-1870`); no upload free occurs on this path. | **GUARANTEED** that this path does not explicitly free the upload buffer; **UNKNOWN** whether any untraced external lifecycle or allocator interaction can invalidate it. |
| Boot arguments | `boot_args` fields are defined in `pongo.h:77-104`; `pongo_entry()` passes `gBootArgs` as `x0`, and `gEntryPoint` as `x8`. | **GUARANTEED** register contract; **UNKNOWN** runtime address, physical/cacheable alias, and whether an arbitrary raw payload may safely dereference the pointer after cleanup. MyOS currently ignores `x0` and `x8`. |
| Allocator reservations | `alloc_init()` derives page count from `memSize`, places `ppage_list` in the early heap, starts static allocation from rounded `topOfKernelData`, and frees a heap range (`mm.c:794-832`). `alloc_phys()` searches only pages marked `PAGE_FREE` and references the result (`mm.c:844-882`). | **GUARANTEED** allocator rules; **UNKNOWN** actual `physBase`, `memSize`, `topOfKernelData`, early heap, page-list, and Pongo-image ranges. |
| Overlap with Pongo code/data, vectors, page tables, DT, or live state | No source check compares a future MyOS load range against every live Pongo allocation/state range. The upload itself is allocator-backed, but the MyOS destination is not reserved by `bootr`. | **UNKNOWN** and unsafe to assume. A successful generic-QEMU load does not prove non-overlap on an Apple boot. |

## 5. Memory and overlap analysis

The source-backed address model is:

```text
cacheable VA = PA - 0x800000000 + 0x400000000
raw handoff target = upload cacheable VA - 0x400000000 + 0x800000000
```

Pongo's allocator marks the page database and currently used pages wired, then
carves free pages. That protects allocations known to Pongo's page database;
it does not provide a source-backed proof that a future MyOS `load_addr` is
disjoint from every Pongo live range, the upload buffer, page tables, vectors,
device-tree storage, or boot arguments. The current `tests/sim_mmap.py` values
are explicitly hypothetical and were not used as runtime evidence.

The MyOS package validator has a different scope. It validates the package's
internal header, payload, PT_LOAD ranges, relocation table, BSS, entry point,
and relocation targets (`build/audit/m2-a7/src/bootstrap.c:272-570`). It does
not know Pongo's page database or live allocations. The package builder's
internal safety check (`build/audit/m2-a7/tools/make_package.py:237-298`)
protects package structures while relocations are applied, not external Pongo
memory.

For this reason, a valid relocated image at `0x42000000` or `0x44000000` in
the existing simulator is only **OBSERVED_IN_QEMU** for that simulator. It is
not a physical-memory overlap proof.

## 6. Comparison with the current MyOS bootstrap

| MyOS assumption | Source/QEMU evidence | Status |
|---|---|---|
| Discover the package base without a fixed address | `bootstrap_entry.S:27-29` uses `adr x0, _bootstrap_entry`; the bootstrap is linked at zero by `bootstrap.ld:21-25`. | **GUARANTEED** position-independent mechanism; **OBSERVED_IN_QEMU** at both tested simulator bases. |
| Use a stack inside the package | `bootstrap_entry.S:29-32` derives `_stack_top`; `bootstrap.ld:16-18,41-47` defines a 16 KiB package stack. | **GUARANTEED** prototype layout; actual Pongo upload-buffer PA and writable mapping are **UNKNOWN**. |
| Treat the package/header/payload addresses as directly accessible | The bootstrap uses `base + offset` and writes relocation/BSS destinations directly (`bootstrap.c:337-349,512-550`). | **OBSERVED_IN_QEMU** with the simulator's MMU-off physical-style map; **UNKNOWN** on the actual post-Pongo mapping and memory attributes. |
| Relocate only within validated image memory | `bootstrap.c:457-508` validates all relocation records before `bootstrap.c:519-525` writes them. | **GUARANTEED** package-local validation; it does not prove external RAM ownership. |
| Assume an AArch64 EL1/MMU-off handoff | The Pongo source statically reaches the raw branch in EL1 and clears M/C before it; the prototype's comments and simulator linker assume MMU off. | EL1/MMU-off is **GUARANTEED** by the traced normal Pongo source sequence, but complete system-register and mapping state remains **UNKNOWN**. |
| Have vectors available for early faults | The bootstrap does not install its own vectors before copying the header, validating, and entering C (`bootstrap_entry.S:33-50`). Pongo installs its own vector earlier (`mm.c:331-334`, `entry.c:162`). | **UNKNOWN** for post-cleanup vector usability; an exception before MyOS installs vectors could enter inherited Pongo handling or fault. |
| Use `x0` boot arguments or `x8` entry metadata | The current bootstrap takes its base from PC and does not use the handoff `x0`/`x8` contract. | **OBSERVED_IN_QEMU** for the current prototype; actual boot-argument pointer representation is **UNKNOWN**. |
| Write anywhere within reported RAM | The package checks only its own ranges. | **CONTRADICTED** as a safety claim: reported RAM is not equivalent to free, non-Pongo, correctly mapped RAM. |

## 7. A10 rerun and regression evidence

The unchanged A10 runner was invoked case-by-case with `--outdir
build/audit/m2-a11/a10-rerun` so it did not overwrite the existing A10 logs or
fixtures. Every case below has `fixture_carries_package=True`, observed the
bootstrap marker, observed the expected rejection, had exit status 124 from the
designed parked guest, and had zero protected-region differences.

The region model was derived from the current bootstrap ELF, not hardcoded:

- scratch only: `[0x1648, 0x1720)` (`img`, `nm` address `0x1648`, size
  `0xd8`); at base `0x42000000`, `[0x42001648, 0x42001720)`;
- bootstrap protected: `[0x0000,0x1648)` and `[0x1720,0x1810)`;
- bootstrap tail pad: `[0x1810,0x2000)`;
- package header: `[0x2000,0x2200)`;
- header pad: `[0x2200,0x3000)`;
- payload and tail pad: `[0x3000,0x48000)` with every relocation target
  monitored individually;
- stack `[0x48000,0x4c000)` is the package stack and is not treated as a
  protected package region;
- relocation targets are 35 eight-byte ranges, with first target
  `[0x15708,0x15710)`, targets 4–30 in `[0x231d8,0x232b0)`, target 31 at
  `[0x23a50,0x23a58)`, and targets 32–34 in `[0x44248,0x44260)`.

Per-case evidence:

| Case | Package path / size / SHA-256 | Generated fixture path / size / SHA-256 | Rejection observed | Diff summary |
|---|---|---|---|---|
| `trunc_header` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/trunc_header.bin` / 8191 / `8709b4e4fff34cc5a880f532e50a6cad7c06fabe3e57ff2ddeea9421ddd73a2a` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_trunc_header.elf` / 140424 / `e02d6295ee3fa15cbb2d4a6897fe6a861dac4ea440cea44621fed67d16bafe89` | `package header magic mismatch (truncated/corrupt package)` | compared 8191; scratch 0; protected 0 |
| `trunc_payload` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/trunc_payload.bin` / 196608 / `d038ba56399346f6fe34fc7420198547c895f247b30bf757484ef7353aeb6e0a` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_trunc_payload.elf` / 328840 / `8cb2f450724251bd7253d42d489442f5639a1f7b449133b4f22ffee08f9764db` | `payload checksum mismatch (truncated/corrupt payload)` | compared 196608; scratch 0; protected 0 |
| `seg_bounds` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/seg_bounds.bin` / 311296 / `63a8e9d8b50c2d8b232c23d7acc862230608bd31b895c34088b2898e03335f1b` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_seg_bounds.elf` / 443520 / `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8` | `PT_LOAD file range outside payload bounds` | compared 294912; scratch 7 at `0x1648`-range offsets; protected 0 |
| `overflow` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/overflow.bin` / 311296 / `2497af0b2e651d85c44e9a45bee445b136e8f19363d3e102f904fa89a134eaad` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_overflow.elf` / 443520 / `86e5b8d3d8448a571cde69f56e74b989ad67fc01663cfa16e134bedcea1d27fc` | `PT_LOAD memory footprint outside payload bounds` | compared 294912; scratch 7; protected 0 |
| `bad_reloc_type` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/bad_reloc_type.bin` / 311296 / `9f3790a2ed921d14922b176a7c8551b3167fafbac88e6bb27e144046347763db` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_bad_reloc_type.elf` / 443536 / `9ca1a7560a3d9974aa67a2d5c53d1a9d2a9a708dde3c0af0dc7a87b5b601f620` | `unsupported relocation type` | compared 294912; scratch 30; protected 0 |
| `reloc_oob` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/reloc_oob.bin` / 311296 / `bebb0e179d7c9bc99125e0259f77632dde18e37fda8649e5a30f4312585e56ff` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_reloc_oob.elf` / 443520 / `c4fb2be0768f9222b4ba2cae1f4dcb11f7863dc1e1df2f0c4fcd1adb63038a0f` | `relocation target outside permitted memory` | compared 294912; scratch 30; protected 0 |
| `entry_oob` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/entry_oob.bin` / 311296 / `2b79f3463135c038c74aa50136559c6bea927418f45f0c2cc10786586a75465a` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_entry_oob.elf` / 443520 / `493e037b35ba3b530a82ecf84140337273038183abcbd2557adf6bce58777fc4` | `entry point outside executable memory` | compared 294912; scratch 19; protected 0 |
| `bss_invalid` | `/home/khoi/Projects/myos/build/audit/m2-a10/build/neg/bss_invalid.bin` / 311296 / `667ef1d14513f681d424be5056c650fe9331fbaf96fe2d14e17d1cf643140c61` | `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_neg_bss_invalid.elf` / 443528 / `05d71a7a1059d88fa44cea2cc57118cd723bc4d05861feeec8d7d2f10ba07012` | `invalid BSS range (p_filesz > p_memsz)` | compared 294912; scratch 7; protected 0 |

The exact per-byte result, including every scratch diff and the empty
protected `diff_offsets`, is in the corresponding JSON files under
`build/audit/m2-a11/a10-rerun/logs/`.

### Controls and reproducibility

- Clean control: fixture
  `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_control_clean.elf`, size 443504, SHA-256
  `834867d7afb7b656c51cc91f95e0b762c117b01379ff130582eb4393b5b520ba`.
  It reached `MYOS_KERNEL_PASS`, exit 124, 35/35 relocation slots matched,
  and had zero protected writes.
- Injected control: fixture
  `/home/khoi/Projects/myos/build/audit/m2-a11/a10-rerun/fixtures/fixture_42000000_control_inject.elf`, size 443536, SHA-256
  `70d5d56056f2576a74637354c8acbf03e2dd49a43811326f1c6df4bb97be4f43`.
  It rejected with `payload checksum mismatch (truncated/corrupt payload)`
  and the checker identified exactly offsets `0x5000..0x5007`, addresses
  `0x42005000..0x42005007`, with observed bytes
  `4b 52 41 4d 31 41 32 4d` (`0x4d3241314d41524b` little-endian marker).
  The control verdict is intentionally FAIL because corruption was detected.
- Bypass control: the same fixture hash and rejection were observed, but the
  checker recorded exactly the eight bypassed offsets and zero violations;
  `--require-detection` made this run FAIL. This proves the control cannot
  pass solely because a log message exists.
- Reproducibility: `build/audit/m2-a11/rebuild_repro.py` built the same
  `seg_bounds.bin` twice. Both fixtures were size 443520 and SHA-256
  `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8`.
  The result is recorded in `logs/reproducibility.json` with
  `reproducible: true`.

The canonical package hash before and after the audit remained:

```text
build/audit/m2-a10/build/package.bin
26a1e1f647298785c26e620fb53b0befd5ecdad3e4626f73a16c62504acfa4d1
```

The existing A10 simulator and package were not changed. The A10 valid-base
reruns also passed at `0x42000000` (fixture SHA-256
`834867d7afb7b656c51cc91f95e0b762c117b01379ff130582eb4393b5b520ba`) and
`0x44000000` (fixture SHA-256
`4d6eb752cb23742c3d3bd142a134716865c10ad2b1b7165114d21179c1393c3f`), each
with `MYOS_KERNEL_PASS`, 35/35 relocations, and zero protected writes.

### Regression commands and statuses

| Command | Exit | Result |
|---|---:|---|
| `make qemu` | 0 | PASS; `MYOS_KERNEL_PASS`; expected parked QEMU timeout handled by the test |
| `make m2-check` | 0 | PASS; image validation, 31 M2 negative tests, and memory-map simulation passed |
| `bash tests/test_boot_diag.sh` | 0 | PASS |
| `bash tests/offline_m1.sh` | 1 | Existing `FAIL_m1ctl_usage`; all other displayed checks passed |
| `bash tests/test_boot_diag_extended.sh` | 1 | Existing failure: missing `.text.vectors` (with `pipefail`); not fixed here |

The A10 negative and control reruns above were invoked with the unchanged
`build/audit/m2-a10/run_case.py` assertions and isolated output. QEMU exit 124
was treated as a parked-guest timeout only when the required marker and RAM
evidence were also present.

## 8. Changed files and raw evidence

New M2-A11 audit inputs:

```text
M2_A11_PONGO_HANDOFF_TRACE.md
build/audit/m2-a11/static_trace.py
build/audit/m2-a11/rebuild_repro.py
build/audit/m2-a11/qemu-probe/env_probe.S
build/audit/m2-a11/qemu-probe/env_probe.ld
build/audit/m2-a11/qemu-probe/build_probe.sh
```

Generated evidence is isolated under `build/audit/m2-a11/`, including the raw
source trace, source hashes, generic-QEMU log, reproducibility JSON, eight
fixture builds, all A10 logs/JSON/RAM dumps, and build intermediates. No file
under `pongoos-src/`, the canonical kernel, M1, M2-A9 bootstrap, or the
existing A10 harness/fixtures was modified. The final PongoOS submodule
worktree is clean.

## 9. Unresolved blockers and next action

The following remain unresolved and prevent a stronger recommendation:

1. No actual PongoOS binary was available in this environment, and generic
   QEMU `virt` cannot prove Apple-board driver, boot-argument, or memory-map
   behavior.
2. The numeric `VBAR_EL1`, `TTBR0_EL1`, `TTBR1_EL1`, and complete `SCTLR_EL1`
   state at raw entry were not captured from Pongo.
3. The actual upload PA/VA, allocator reservations, Pongo live ranges, device
   tree placement, and boot-argument alias/lifetime are unknown.
4. MyOS installs its own vectors only in its later canonical kernel entry; an
   exception during the raw bootstrap before that installation has no
   source-backed safe handler guarantee.

Smallest safe next action: obtain a reproducible build of the exact pinned
PongoOS revision with an Apple-target toolchain, add read-only register and
allocator trace points to a disposable Pongo artifact, and first run that
instrumented handoff in a hardware-faithful emulator/offline harness. Do not
use the generic `virt` probe or the existing `sim_bootr` fixture as a proxy for
physical-device compatibility, and do not proceed to a physical device until
the address-overlap and vector-state evidence is complete.

No physical device was accessed.
