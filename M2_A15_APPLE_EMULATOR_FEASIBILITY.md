# M2-A15 — Documented Apple-Platform Emulator Feasibility Audit

Date: 2026-10-11  
Repository: `/home/khoi/Projects/myos`  
Pinned PongoOS: `4c9b7541629234147fcc778f0ce4162482aaccef`  
Pinned Newlib: `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`  
Physical Apple hardware accessed: **No**

## 1. Result

No documented, locally reproducible offline environment was found that both:

1. executes the relevant Apple boot chain or the pinned PongoOS Apple-target
   `bootr` path; and
2. exposes instruction-level CPU registers, memory mappings, exceptions, and
   allocator state at the raw-payload handoff.

The strongest documented candidate is Corellium's virtual-device/debugging
environment, but it is not available in this workspace and the inspected
documentation does not establish that the pinned PongoOS `bootr` handoff can
run there with the required state. The public `citruz/pongoOS-QEMU` project
does expose GDB stepping, but it is a modified PongoOS fork on generic QEMU
`virt`, explicitly reports that everything beyond a small device subset is not
working, and does not document the Apple checkra1n/iBoot path.

No `RUNTIME_VERIFIED` Apple/PongoOS handoff value was obtained. Generic QEMU
execution remains `OBSERVED_IN_QEMU` only; synthetic A13 results remain
`SIMULATED`.

## 2. Provenance and preservation

### Initial repository state

The preflight was recorded in
`build/audit/m2-a15/preflight.txt`.

| Item | Value |
|---|---|
| Root | `/home/khoi/Projects/myos` |
| Branch | `main` |
| HEAD | `3bea11141fea05bb90017069cff22b9a37d91e9e` |
| PongoOS checkout | `4c9b7541629234147fcc778f0ce4162482aaccef` |
| Newlib checkout | `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` |
| PongoOS worktree | clean at preflight and post-check |
| Newlib worktree | clean at preflight and post-check |

The pre-existing top-level changes were preserved. They include modified
`Makefile` and boot-diagnostic sources, pre-existing M2 reports, and existing
untracked kernel/test files. No existing source, fixture, test, PongoOS file,
or submodule revision was changed by this audit.

The five required reports were present under their exact requested names and
were read before relying on their results:

- `M2_A10_RUNTIME_HANDOFF_AUDIT.md`
- `M2_A11_PONGO_HANDOFF_TRACE.md`
- `M2_A12_PONGOOS_BUILD_AUDIT.md`
- `M2_A13_SYNTHETIC_HANDOFF_HARNESS.md`
- `M2_A14_PONGOOS_RUNTIME_FEASIBILITY.md`

All new A15 files are under `build/audit/m2-a15/`, except this report.
No candidate repository or binary was downloaded, cloned, installed, or
executed during this audit.

### Reconciled baseline

The prior reports establish the following, independently of the candidate
search:

| Finding | Classification | Basis |
|---|---|---|
| `bootr` is registered by PongoOS and sets `BOOT_FLAG_RAW` after an upload | `SOURCE_VERIFIED` | `pongoos-src/src/shell/main.c:40-48,257-269` |
| The raw path computes the uploaded image address and calls the extended jump | `SOURCE_VERIFIED` | `pongoos-src/src/kernel/entry.c:294-312` |
| The jump passes boot arguments in `x0`, entry point in `x8`, clears `x1-x3`, and performs `ic iallu` with barriers | `SOURCE_VERIFIED` | `pongoos-src/src/boot/jump_to_image.S:42-55` |
| The pinned source has no PongoOS-specific QEMU machine, simulator, or test mode | `SOURCE_VERIFIED` | Source inventory in `build/audit/m2-a15/pinned-source-candidate-check.txt` |
| Generic QEMU can execute the existing raw test artifacts | `OBSERVED_IN_QEMU` | A10/A14 probes and A15 `make_qemu.log` |
| Generic QEMU values prove Apple/PongoOS handoff state | `CONTRADICTED` | Generic `virt` has no Apple boot-chain model |
| A13 synthetic safety scenarios model the contract | `SIMULATED` | `M2_A13_SYNTHETIC_HANDOFF_HARNESS.md` and its saved evidence |

The pinned PongoOS README identifies PongoOS as a pre-boot environment built on
checkra1n and documents `checkra1n -k Pongo.bin` as the supported execution
workflow. It does not document generic QEMU execution. See the
[pinned PongoOS README](https://github.com/checkra1n/PongoOS) and the exact
checked-out lines cited above.

## 3. Candidate investigation

Candidate metadata is machine-readable in
`build/audit/m2-a15/candidate-summary.json` (SHA-256:
`72d07a21b113dc56d29d2da2dc984ab801b9d58f4599d89dcd4250c2acef3deb`). The
candidate summary uses the required evidence vocabulary: `SOURCE_VERIFIED`,
`ARTIFACT_VERIFIED`, `RUNTIME_VERIFIED`, `SIMULATED`, and `UNKNOWN`.

### Candidate A — `citruz/pongoOS-QEMU`

Official project: [citruz/pongoOS-QEMU](https://github.com/citruz/pongoOS-QEMU)

The project is directly relevant in name and documents QEMU GDB use (`-s -S`)
under TCG. Its README also documents:

- a fork of PongoOS rather than the pinned checkout;
- generic AArch64 QEMU `virt` with `cortex-a72`;
- ramfb, PL011, GICv2, device-tree, and `fw_cfg` support;
- an `utmapp` QEMU fork and a documented QEMU `virt` memory-map patch; and
- “Everything else” as not working.

Therefore:

| Property | Classification | Finding |
|---|---|---|
| Project documentation and GDB capability | `SOURCE_VERIFIED` | The README documents the fork, TCG, and GDB stepping |
| Generic ARM64 execution | `SOURCE_VERIFIED` | The documented machine is QEMU `virt` |
| Execution of the pinned PongoOS checkout | `UNKNOWN` | No evidence that this exact revision was executed |
| Apple checkra1n/iBoot boot chain | `UNKNOWN` | No such path is documented |
| Actual pinned `bootr` handoff state | `UNKNOWN` | No runtime trace was obtained |

This candidate is useful as a possible research starting point, but it does
not meet the A15 requirement without replacing or validating substantial
platform behavior. Using it would not establish Apple/PongoOS runtime facts.

### Candidate B — `qemu-t8030`

Official project and documentation:

- [qemu-t8030 source](https://github.com/TrungNguyen1909/qemu-t8030)
- [Bringing up the emulator](https://github.com/TrungNguyen1909/qemu-t8030/wiki/Bringing-up-the-emulator)
- [USB and restore workflow](https://github.com/TrungNguyen1909/qemu-t8030/wiki/USB-and-Restore)

The documentation describes T8030/A13 iPhone 11 emulation, SecureROM/iOS
restore work, custom QEMU, and firmware/tooling requirements. The repository
is archived. It does not document execution of the pinned PongoOS `bootr`
path or the required post-checkra1n handoff trace.

The pinned PongoOS platform-driver inventory contains `s5l8960`, `s8000`,
`s8001`, `s8003`, `t7000`, `t7001`, `t8010`, `t8011`, `t8012`, and `t8015`;
it contains no `t8030` platform driver. The only T8030/A13 text found in the
pinned tree is in platform-address comment tables in
`pongoos-src/src/boot/patches.S`, not a T8030 platform implementation.

| Property | Classification | Finding |
|---|---|---|
| T8030/A13 project and documented iOS/restore path | `SOURCE_VERIFIED` | Project documentation |
| Pinned PongoOS compatibility | `CONTRADICTED` | No T8030 platform driver in the pinned source |
| Required register/memory handoff trace | `UNKNOWN` | Not documented or locally run |
| Offline reproducibility here | `UNKNOWN` | Required firmware/tools are absent |

It is not a valid exact-use-case emulator for this pinned PongoOS revision.

### Candidate C — `alephsecurity/xnu-qemu-arm64`

Official project and documentation:

- [xnu-qemu-arm64](https://github.com/alephsecurity/xnu-qemu-arm64)
- [Build iOS on QEMU](https://github.com/alephsecurity/xnu-qemu-arm64/wiki/Build-iOS-on-QEMU)
- [xnu-qemu-arm64-tools](https://github.com/alephsecurity/xnu-qemu-arm64-tools)

This project documents a custom iPhone 6s Plus `n66`/S8000 QEMU machine, direct
iOS 12.1 kernel/device-tree/disk boot, and GDB scripts for XNU debugging. Those
are useful Apple-platform and debugger capabilities, but the documented path
boots a patched XNU environment directly. It does not establish the
checkra1n/PongoOS pre-boot path or the pinned PongoOS `bootr` state.

| Property | Classification | Finding |
|---|---|---|
| Apple machine model and direct iOS execution | `SOURCE_VERIFIED` | Project and wiki |
| GDB/XNU tracing | `SOURCE_VERIFIED` | Project and tools documentation |
| Pinned PongoOS execution | `UNKNOWN` | No exact path documented |
| PongoOS handoff state | `UNKNOWN` | No runtime evidence |

It cannot substitute for the requested PongoOS handoff environment.

### Candidate D — Corellium virtual devices

Official documentation:

- [iBoot and custom BootROM](https://support.corellium.com/device-settings/iboot)
- [LLDB connection](https://support.corellium.com/features/connect/lldb)
- [Kernel debugging](https://support.avh.corellium.com/features/kernel-debugging)
- [Onsite virtual devices](https://support.corellium.com/setup/onsite/virtual-devices)

Corellium documents virtual iPhone devices, starting from iBoot or a custom
BootROM, loading raw binaries, and debugger features including register and
memory inspection, breakpoints, watchpoints, and single-stepping. Its iBoot
documentation covers iPhone 6 through iPhone 12 Pro Max, overlapping the
device generations relevant to the pinned PongoOS drivers.

However, no Corellium service, on-prem appliance, client, or authorized
runtime was available locally. The inspected documentation does not prove
that this exact PongoOS build can be loaded through the checkra1n/Pongo path,
nor that its raw handoff reproduces PongoOS's `CurrentEL`, vector base, MMU,
cache, boot-argument aliases, upload-buffer lifetime, and allocator state.

| Property | Classification | Finding |
|---|---|---|
| Virtual Apple platform | `SOURCE_VERIFIED` | Corellium documentation |
| iBoot/custom BootROM and raw-binary support | `SOURCE_VERIFIED` | Corellium iBoot documentation |
| Instruction/register/memory debugging | `SOURCE_VERIFIED` | Corellium LLDB/kernel-debugging documentation |
| Pinned PongoOS `bootr` handoff | `UNKNOWN` | No exact compatibility evidence |
| Offline reproducibility in this workspace | `UNKNOWN` | No service/appliance available |
| Actual runtime measurements | `UNKNOWN` | No execution performed |

Corellium is a potential separately authorized external prerequisite, not an
established A15 result.

### Candidate E — iEmu / Inferno derivatives

Public project references:

- [iEmu](https://github.com/satyamisme/iEmu)
- [Inferno-iOS](https://github.com/MakrSas/Inferno-iOS)

The inspected project descriptions establish Apple ARM SoC/iOS emulation,
including T8030/A13-oriented work, but not the pinned PongoOS/checkra1n
`bootr` path, its allocator state, or the required deterministic handoff
trace.

| Property | Classification | Finding |
|---|---|---|
| Apple SoC/iOS emulation claims | `SOURCE_VERIFIED` | Public project descriptions |
| Pinned PongoOS compatibility | `UNKNOWN` | No exact path documented |
| Required instruction/memory trace | `UNKNOWN` | Not documented or locally available |

These projects do not meet the requirement.

### Candidate F — upstream QEMU `virt`

Official documentation:

- [QEMU Arm `virt`](https://www.qemu.org/docs/master/system/arm/virt)
- [QEMU Arm system emulation](https://www.qemu.org/docs/master/system/target-arm)

The local executable is QEMU 11.1.1. Its available machine inventory is saved
in `build/audit/m2-a15/qemu_machine_help.txt`; it contains generic `virt` and
`sbsa-ref` machines but no Apple, iPhone, PongoOS, T8030, T8010, S8000, or
T7000 machine. QEMU's `virt` model supplies generic PL011/GIC/PCI/virtio-style
platform devices, not Apple's boot ROM and SoC interfaces.

Earlier A12/A14 probes loaded Pongo-derived artifacts into generic QEMU and
ended with timeout/termination output without a PongoOS marker. The A15
regression run confirms that generic QEMU can run the existing MyOS diagnostic
test, but this is not PongoOS runtime evidence.

| Property | Classification | Finding |
|---|---|---|
| Generic AArch64 execution | `SOURCE_VERIFIED` / `OBSERVED_IN_QEMU` | Local QEMU and `make qemu` |
| Apple boot-chain execution | `CONTRADICTED` | No Apple machine or firmware path |
| Pinned PongoOS handoff | `UNKNOWN` | No Pongo marker or faithful machine |
| Generic debugger capability | `SOURCE_VERIFIED` | QEMU supports generic debugging |

QEMU's own documentation warns that Arm images are machine-model dependent;
generic instruction compatibility is not platform compatibility.

## 4. Requirement matrix

| Required capability | Best available evidence | Classification | Result |
|---|---|---|---|
| Execute pinned PongoOS Apple-target code | Pinned source/build artifacts only | `ARTIFACT_VERIFIED` | Build exists; execution not verified |
| Execute actual Apple boot chain | Corellium documentation; no local service | `SOURCE_VERIFIED` / `UNKNOWN` | Not locally reproducible |
| Execute exact `bootr` handoff | Pongo source describes it; no faithful runtime | `SOURCE_VERIFIED` / `UNKNOWN` | Static path only |
| Capture `CurrentEL`, `VBAR_EL1`, `SCTLR_EL1`, `TTBR0_EL1`, `TTBR1_EL1` at handoff | Generic QEMU probe only | `UNKNOWN` for PongoOS | No valid runtime values |
| Capture upload-buffer PA/VA, lifetime, and ownership | Source formula only | `UNKNOWN` | Requires actual allocator/runtime state |
| Capture boot-argument aliases and mappings | Source structure only | `UNKNOWN` | Requires actual platform mapping |
| Capture allocator reservations and overlap | Synthetic A13 model | `SIMULATED` | Not PongoOS runtime evidence |
| Repeat offline deterministically | Generic and synthetic tests | `ARTIFACT_VERIFIED` / `SIMULATED` | Exact Apple/Pongo runtime not established |

There are no values in this audit classified `RUNTIME_VERIFIED` for the actual
PongoOS Apple-target handoff.

## 5. Exact commands and saved evidence

All commands were run from `/home/khoi/Projects/myos` with output captured
under `build/audit/m2-a15/`. The commands below are the reproducible local
checks; candidate web pages were inspected read-only and no candidate source
was downloaded.

### Provenance and local capability checks

~~~sh
pwd
git branch --show-current
git rev-parse HEAD
git status --short
git -C pongoos-src rev-parse HEAD
git -C pongoos-src diff --exit-code
git -C pongoos-src/newlib rev-parse HEAD
git -C pongoos-src/newlib diff --exit-code
command -v qemu-system-aarch64
qemu-system-aarch64 --version
qemu-system-aarch64 -machine help
~~~

The captured results are in `preflight.txt`, `tool_inventory.txt`, and
`qemu_machine_help.txt`. The Pongo source/platform search is in
`pinned-source-candidate-check.txt`.

### Regression commands

~~~sh
timeout 30s make qemu > build/audit/m2-a15/make_qemu.log 2>&1
make m2-check > build/audit/m2-a15/make_m2_check.log 2>&1
bash tests/test_boot_diag.sh > build/audit/m2-a15/test_boot_diag.log 2>&1
bash tests/offline_m1.sh > build/audit/m2-a15/offline_m1.log 2>&1
bash tests/test_boot_diag_extended.sh > build/audit/m2-a15/test_boot_diag_extended.log 2>&1
~~~

The first three commands exited 0. The last two preserve known pre-existing
failures and exited 1; details are in Section 6.

### Integrity checks

The A10/A13 fixture and evidence baselines were copied before testing and
compared after testing. Both comparison files are empty and both diff commands
exited 0:

- `build/audit/m2-a15/a10_a13_fixture_diff.txt`
- `build/audit/m2-a15/a13_evidence_diff.txt`

The A13 evidence baseline includes the prior deterministic summary hash
`c9fb945ff1fa46afb18bc64051bf0f2ed615fdb6ee78c6269d8b59cf150f9296`; the
before/after comparison preserved it. PongoOS and Newlib `git diff --exit-code`
also returned 0 after testing.

## 6. Regression results

| Command | Exit | Result |
|---|---:|---|
| `make qemu` | 0 | `MYOS_KERNEL_PASS`; QEMU then parked until the test timeout and terminated normally |
| `make m2-check` | 0 | Existing 31/31 negative tests pass; memory-map output remains explicitly hypothetical |
| `bash tests/test_boot_diag.sh` | 0 | All offline diagnostic checks pass |
| `bash tests/offline_m1.sh` | 1 | Known `FAIL_m1ctl_usage`; other listed M1 checks pass |
| `bash tests/test_boot_diag_extended.sh` | 1 | Known missing `.text.vectors`; warnings and failure are preserved, not reclassified |

No regression command was used as evidence of Apple/PongoOS runtime execution.
The QEMU timeout/parked-guest behavior is treated as a timeout result unless
the independent expected marker is present; `make qemu` has the marker
`MYOS_KERNEL_PASS`, while the earlier Pongo probes did not have a Pongo marker.

## 7. Remaining blockers

The following facts remain unresolved and cannot be inferred from generic QEMU,
the A13 model, or the Apple-oriented XNU/T8030 projects:

1. The actual `CurrentEL`, `VBAR_EL1`, `SCTLR_EL1`, `TTBR0_EL1`, and `TTBR1_EL1`
   at the pinned PongoOS raw handoff.
2. The actual physical and virtual aliases of the upload buffer and boot
   arguments.
3. Upload-buffer ownership and lifetime across PongoOS cleanup, yield, and
   the final branch.
4. PongoOS allocator reservations and overlap with the proposed MyOS image,
   stack, vectors, page tables, boot arguments, and live PongoOS state.
5. Apple SoC, SecureROM, iBoot, checkra1n, and PongoOS interactions for the
   exact target and pinned revision.

The minimum additional capability is an authorized, documented Apple-platform
runtime that can load this PongoOS artifact through the relevant boot stage and
pause at or immediately before `jump_to_image_extended`, with register and
physical-memory inspection. Corellium/on-prem or an equivalent environment
could be evaluated for that role, but it must first prove exact PongoOS loading
and handoff compatibility. Otherwise, obtaining a real runtime trace requires
a separately authorized hardware-testing plan; this audit does not authorize
or perform that test.

## 8. Final recommendation

Recommended next action: obtain authorization and access to one documented
Apple-platform runtime (Corellium/on-prem or an equivalent environment), then
first validate the pinned PongoOS `bootr` register and memory contract at the
raw jump before proposing any new MyOS boot-path work.

CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE
