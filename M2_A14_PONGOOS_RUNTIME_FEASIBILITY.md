# MyOS M2-A14 — PongoOS Runtime Feasibility

Date: 2026-10-11

Recommendation: CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE

No faithful offline PongoOS runtime was found in the repository or on this
Arch Linux host. The pinned Apple-target artifacts are reproducible as build
artifacts, but neither the available QEMU machines nor the checked-out
PongoOS tooling executes the Apple checkra1n/iBoot handoff. No physical device
was accessed.

## 1. Provenance and preservation

Repository root:

~~~text
/home/khoi/Projects/myos
~~~

Initial top-level Git state:

~~~text
branch: main
HEAD: 3bea11141fea05bb90017069cff22b9a37d91e9e
git submodule status --recursive: no registered top-level entries
~~~

The initial status was:

~~~text
 M Makefile
 M arch/aarch64/boot_diag.c
 M arch/aarch64/boot_diag.h
 M arch/aarch64/vectors.S
?? M2_A10_PONGO_INTEGRATION_REPORT.md
?? M2_A10_RUNTIME_HANDOFF_AUDIT.md
?? M2_A11_PONGO_HANDOFF_TRACE.md
?? M2_A12_PONGOOS_BUILD_AUDIT.md
?? M2_A13_SYNTHETIC_HANDOFF_HARNESS.md
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
~~~

The nested PongoOS checkout was independently inspected because it is not a
registered top-level Git submodule:

~~~text
pongoos-src:       4c9b7541629234147fcc778f0ce4162482aaccef
pongoos-src/newlib: feaa8fcf90de497485b74e6f65fd78a58a27b9d7
pongoos-src status: clean
newlib status:      clean
~~~

The four requested reports were present and read before this audit:

~~~text
M2_A10_RUNTIME_HANDOFF_AUDIT.md
M2_A11_PONGO_HANDOFF_TRACE.md
M2_A12_PONGOOS_BUILD_AUDIT.md
M2_A13_SYNTHETIC_HANDOFF_HARNESS.md
~~~

No source under pongoos-src, the canonical kernel, M1, the raw bootstrap,
existing test harnesses, or existing fixtures was edited. New evidence files
were limited to this report and ignored generated files under
build/audit/m2-a14/.

Final top-level status was the same pre-existing set above plus:

~~~text
?? M2_A14_PONGOOS_RUNTIME_FEASIBILITY.md
~~~

## 2. Pinned build and artifact provenance

The checked-out source was inspected directly:

| Source/configuration | Evidence |
|---|---|
| Apple target | pongoos-src/Makefile:31-59 selects arm64-apple-ios12.0, freestanding LLVM/LTO flags, Mach-O preload linking, image base 0x100000000, and 0x4000 segment alignment |
| Artifact graph | pongoos-src/Makefile:90-116 builds Pongo, converts it to Pongo.bin, builds the KPF module, and concatenates PongoConsolidated.bin |
| Newlib | pongoos-src/newlib/Makefile:22-73 selects the matching Darwin target and Newlib toolchain |
| Official usage | pongoos-src/README.md:1-41 describes PongoOS as a pre-boot Apple-board environment and documents checkra1n -k as the execution path |
| CI scope | pongoos-src/.github/workflows/ci.yml:11-52 installs build tools and archives binaries; it contains no emulator or runtime job |

The fixed-path artifact set produced by the M2-A12 investigation remains
available and its hashes were independently checked:

| Artifact | Size | SHA-256 | Classification |
|---|---:|---|---|
| build/audit/m2-a12/repro/build/Pongo | 787192 | 24cd343f587ce95f845edff2a06a10cc33bb75ec1291f760e56e73d530610fa6 | ARTIFACT_VERIFIED |
| build/audit/m2-a12/repro/build/Pongo.bin | 675936 | 7d3f3e09ca031c34d7ae0d79f0794b111de60fc32567b7991802ce4b1125678e | ARTIFACT_VERIFIED |
| build/audit/m2-a12/repro/build/PongoConsolidated.bin | 763748 | 841ff2f920bb361d649f6ebf2a43f3b6202c00f0a34c630bea70457fc9682f3b | ARTIFACT_VERIFIED |
| build/audit/m2-a12/repro/build/checkra1n-kpf-pongo | 87796 | e179e4078410c3cc16e6b71309059781a36983049366b55e4ccebc9d81eac0de | ARTIFACT_VERIFIED |
| build/audit/m2-a12/repro/build/libc.a | 799312 | 872c95f80226b275b571384b8a2dc2bd1b395194c310ab7931eee79004df0de4 | ARTIFACT_VERIFIED |

The M2-A12 build command used a locally pinned LLVM/cctools directory:

~~~sh
TOOL=/home/khoi/myos-toolchain10/usr
PATH="$TOOL/bin:/usr/bin:/bin" \
EMBEDDED_CC="$TOOL/bin/clang" \
EMBEDDED_LDFLAGS="-fuse-ld=$TOOL/bin/ld64" \
STRIP="$TOOL/bin/cctools-strip" \
LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1636057726 \
make -C "$SRC" BUILD="$BUILD" -j1 all
~~~

The build succeeded reproducibly at the fixed source/build path in M2-A12.
This audit found one metadata limitation that must not be hidden: the artifact
banner is:

~~~text
# pongoOS 2.5.1-3bea1114
~~~

The source Makefile assigns PONGO_VERSION with := at line 44 from git log.
Therefore an environment variable named PONGO_VERSION does not override that
assignment. The prior command's environment value 2.5.1-4c9b7541 was not
reflected in the artifact banner. The source tree used for the artifact is
the pinned PongoOS checkout, but the embedded version string identifies the
MyOS top-level HEAD. A future correctly tagged rebuild must pass
PONGO_VERSION=2.5.1-4c9b7541 as a make command-line variable, or otherwise
provide the intended Git identity in the isolated build copy. No source file
was changed here.

This does not establish runtime execution. It establishes only that the
Apple-target build inputs and the resulting files are available and
hashable.

## 3. Actual source handoff and platform dependencies

The relevant source path is:

~~~text
pongoos-src/src/shell/main.c:40-48,257-269
  bootr checks loader_xfer_recv_count, clears the count, sets BOOT_FLAG_RAW,
  and yields.

pongoos-src/src/shell/usbloader.c:32-33,66-99
  upload storage is a contiguous allocator allocation with a 128 MiB maximum.

pongoos-src/src/kernel/entry.c:294-312
  pongo_entry performs low-level setup and cleanup, then computes the physical
  raw-image alias and calls jump_to_image_extended().

pongoos-src/src/boot/jump_to_image.S:42-55
  x30 receives the raw image address, x0 receives gBootArgs, x8 receives
  gEntryPoint, x1-x3 are cleared, and the source barriers plus ic iallu run
  before ret.
~~~

PongoOS is not platform-neutral at this boundary. Platform registration probes
the SoC CPID and panics if no platform driver matches
(pongoos-src/src/drivers/hal/hal.c:185-210). The checked-out tree contains
Apple-specific platform drivers such as:

~~~text
pongoos-src/src/drivers/plat/s5l8960.c:29-60
pongoos-src/src/drivers/plat/s8000.c
pongoos-src/src/drivers/plat/t7000.c
pongoos-src/src/drivers/plat/t8010.c:29-60
pongoos-src/src/drivers/plat/t8015.c
~~~

The USB driver consumes both device-tree locations and platform-specific USB
register/IRQ data (pongoos-src/src/drivers/usb/synopsys_otg.c:1787-1810).
This is evidence that a generic CPU model alone is not the platform contract.

The checked-out PongoOS tree exposes no PongoOS-specific QEMU machine, simulator
mode, host execution mode, or test target. The source search found only a build
CI workflow, Apple platform/driver code, the USB upload path, and host-side
transport scripts. This negative result is classified SOURCE_VERIFIED for the
checked tree, not as a claim that no external project could ever provide an
emulator.

## 4. Candidate execution environments

### Candidate A — official checkra1n Apple-board workflow

Status: SOURCE_VERIFIED, not available offline on this host.

The pinned README identifies PongoOS as a pre-boot environment for Apple
boards and documents:

~~~text
checkra1n -k Pongo.bin
checkra1n -k PongoConsolidated.bin
checkra1n -k PongoConsolidated.bin -p
~~~

The official upstream README gives the same build artifacts and usage:
[checkra1n/PongoOS README](https://github.com/checkra1n/PongoOS).

This is the only documented execution path found in the pinned source. It
requires a checkra1n-compatible Apple boot chain and target SoC. It is not an
offline emulator, and this task prohibits the physical-device experiment that
would normally exercise it. No handoff register capture is available from the
host.

### Candidate B — PongoOS USB scripts and pongoterm

Status: SOURCE_VERIFIED transport tooling, not an emulator.

The boot helper at pongoos-src/scripts/boot-checkra1n.py:24-51 opens a USB
device with vendor 0x05ac and product 0x4141, uploads data, and issues shell
commands. The Linux pongoterm implementation at
pongoos-src/scripts/pongoterm.c:45-95,214-244 uses libusb and waits for the
Pongo USB device. These tools communicate with an already-running PongoOS
target; they do not emulate the target or expose an offline CPU/register
trace.

The host has iproxy and usbmuxd, but does not have checkra1n, palera1n,
pongo, pongoterm, idevicerestore, xcrun, simctl, xcodebuild, LLDB, or Darling.
No Apple USB target was used or accessed.

### Candidate C — available generic ARM64 QEMU

Status: ARTIFACT_VERIFIED input loading and generic QEMU process behavior;
UNKNOWN for PongoOS runtime; not faithful.

Local inventory:

~~~text
qemu-system-aarch64: /usr/bin/qemu-system-aarch64
QEMU emulator version 11.1.1
qemu-system-arm: missing
qemu-system-x86_64: missing
~~~

The available machine list contains generic virt, SBSA, Raspberry Pi,
Versal, and other non-Apple boards, but no iPhone/iPad/Apple SoC or PongoOS
machine. The selected virt machine is explicitly generic. QEMU documents
that virt does not correspond to real hardware and provides generic PL011,
GPIO, GIC, PCI, virtio, RAM, and optional SMMU devices:
[QEMU virt documentation](https://www.qemu.org/docs/master/system/arm/virt).
QEMU also warns that Arm images generally do not boot on a board model that
does not match their intended hardware:
[QEMU Arm system emulation](https://www.qemu.org/docs/master/system/target-arm).

The bounded local probes were:

~~~sh
timeout 3 qemu-system-aarch64 -machine virt -cpu cortex-a57 \
  -nographic -kernel /home/khoi/Projects/myos/build/audit/m2-a12/repro/build/Pongo.bin

timeout 3 qemu-system-aarch64 -machine virt -cpu cortex-a57 \
  -nographic -kernel /home/khoi/Projects/myos/build/audit/m2-a12/repro/build/PongoConsolidated.bin
~~~

Both returned 124 due to timeout and emitted only QEMU's termination line:

~~~text
qemu-system-aarch64: terminating on signal 15 from pid 3 (timeout)
~~~

Neither emitted a PongoOS marker. This is not a successful boot and not a
PongoOS handoff trace. It is also consistent with the missing Apple boot
chain, Apple device-tree/SoC register model, and target-specific transport.
QEMU's generic architectural ability to execute AArch64 does not supply those
missing facts. No QEMU register value from this probe is classified as
PongoOS runtime evidence.

### Candidate D — other local QEMU machines

Status: UNKNOWN for an Apple/PongoOS handoff, practically rejected by the
available machine inventory.

The full qemu-system-aarch64 -machine help output was saved at
build/audit/m2-a14/qemu_machine_help.txt. It contains no Apple machine and no
PongoOS/checkra1n/iBoot model. Selecting another non-Apple board would change,
not reproduce, the target platform. It cannot establish the actual bootr
state.

### Candidate E — Apple Simulator/Xcode, Darling, or a generic userland
simulator

Status: UNKNOWN and unavailable locally; not a faithful bare-metal candidate.

The host has no xcrun, simctl, xcodebuild, or Darling. More importantly, an
iOS application simulator or macOS userland compatibility layer would not
provide the Apple pre-boot/iBoot/checkra1n handoff, PongoOS SoC drivers,
allocator state, or raw payload entry contract required here. No local source
or artifact demonstrates otherwise.

### Candidate F — existing CI or PongoOS debug/test mode

Status: SOURCE_VERIFIED as unavailable in the checked tree.

The PongoOS CI workflow builds and archives four artifacts only. No emulator,
QEMU invocation, simulator target, GDB trace, or bootr test mode is present.
The source tree's debug-related matches are driver compile-time diagnostics
and platform code, not a host-executable runtime. The A10 QEMU fixture and
the A13 synthetic harness are explicitly MyOS models; they do not execute
PongoOS and remain SIMULATED/isolated evidence.

## 5. Evidence classification

| Property | Classification | Finding |
|---|---|---|
| PongoOS source revision | SOURCE_VERIFIED | 4c9b7541629234147fcc778f0ce4162482aaccef |
| Newlib revision | SOURCE_VERIFIED | feaa8fcf90de497485b74e6f65fd78a58a27b9d7 |
| Apple-target build configuration | SOURCE_VERIFIED | arm64-apple-ios12.0, Mach-O preload/raw conversion |
| Artifact bytes and hashes | ARTIFACT_VERIFIED | A12 artifact set reproduced at fixed path and rehashed |
| Artifact embedded Pongo version | ARTIFACT_VERIFIED | 2.5.1-3bea1114; does not identify the pinned Pongo commit |
| Official execution path | SOURCE_VERIFIED | checkra1n transfers Pongo.bin to an Apple board |
| PongoOS bootr source call chain | SOURCE_VERIFIED | command registration through jump_to_image_extended |
| Generic QEMU AArch64 execution | ARTIFACT_VERIFIED | QEMU accepted the raw artifact and remained parked until timeout |
| Actual PongoOS execution in QEMU | UNKNOWN | no Pongo marker and no Apple platform model |
| CurrentEL at actual bootr handoff | UNKNOWN | no PongoOS runtime reached |
| VBAR_EL1, SCTLR_EL1, TTBR0_EL1, TTBR1_EL1 | UNKNOWN | no faithful runtime capture |
| Upload physical/virtual address and lifetime | UNKNOWN | source formulas exist; no runtime allocator state |
| Boot-argument aliases and overlap | UNKNOWN | no runtime memory map or allocator reservation trace |
| A10 simulator results | SIMULATED/OBSERVED_IN_ISOLATED_QEMU | valid for the MyOS fixture model only |
| A13 synthetic results | SIMULATED | contract checks only; not PongoOS behavior |

No property in the required runtime table qualifies as RUNTIME_VERIFIED.

## 6. Regression and preservation results

Current A14 regression commands and logs:

| Command | Exit | Result |
|---|---:|---|
| make qemu | 0 | PASS; MYOS_KERNEL_PASS observed; expected parked-guest termination handled by the test |
| make m2-check | 0 | PASS; 31/31 negative tests and memory-map simulation completed |
| bash tests/test_boot_diag.sh | 0 | PASS; all offline checks passed |
| bash tests/offline_m1.sh | 1 | Existing FAIL_m1ctl_usage reproduced |
| bash tests/test_boot_diag_extended.sh | 1 | Existing missing .text.vectors failure reproduced |

Raw logs are:

~~~text
build/audit/m2-a14/make_qemu.log
build/audit/m2-a14/make_m2_check.log
build/audit/m2-a14/test_boot_diag.log
build/audit/m2-a14/offline_m1.log
build/audit/m2-a14/test_boot_diag_extended.log
~~~

The latest A10-A13 evidence was reviewed without changing assertions:

- M2-A10/M2-A12: the latest recorded A10 suite has 8/8 malformed cases
  rejecting with expected messages, a passing clean control, and intentional
  injection/bypass controls that fail as required. Those results are the
  isolated MyOS bootstrap fixture tests, not PongoOS runtime evidence.
- M2-A13: the existing aggregate synthetic run returned 0, with deterministic
  summary hash c9fb945ff1fa46afb18bc64051bf0f2ed615fdb6ee78c6269d8b59cf150f9296.
  Its prior valid/negative scenarios remain SIMULATED.
- A fresh A13 output could not be placed under the required A14 directory:
  the unchanged harness rejects output outside its own
  build/audit/m2-a13 root and returned 2 for both repository-CWD and /tmp
  invocations. It was not bypassed or modified.
- The command python3 build/audit/m2-a10/run_suite.py --help was issued while
  checking the runner interface. That script has no help parser and launched
  its existing suite instead. The existing fixture bytes remained unchanged;
  the preservation manifest below is the authoritative check. No result from
  this accidental invocation is used to upgrade runtime evidence.

Canonical and existing-fixture preservation:

~~~text
Before source: build/audit/m2-a13/regression/hashes_before.sha256
After source:  build/audit/m2-a14/hashes_after.sha256
Comparison:    diff exit 0
Compared:      15 canonical/existing fixture artifacts
~~~

Notable unchanged hashes:

~~~text
build/myos_m1.bin  4b0070a402980133c8c8d8467031070acbf604688c0437ca6f36c960812706c0
build/myos_m1.elf  056c64bec4a2cc89cabf5f945c1ec9c6ab67395c0a77b844a260643996e666b8
fixture_42000000_control_clean.elf  834867d7afb7b656c51cc91f95e0b762c117b01379ff130582eb4393b5b520ba
fixture_42000000_valid_base_a.elf    6ebafe75c1f57d3e0c4ac95566123cd109172383f02c392db061bc76b8b92d85
fixture_44000000_valid_base_b.elf    4e2503824507b848d12bd4e5ac809a2580fa63a612062b20836c112b15c385db
fixture_inject_42000000.elf          70d5d56056f2576a74637354c8acbf03e2dd49a43811326f1c6df4bb97be4f43
~~~

Nested PongoOS and Newlib worktrees remained clean after all checks. No
physical USB device or physical iPhone was accessed.

## 7. Remaining blockers

The unresolved runtime facts are:

1. No existing local emulator models the target Apple SoC, its PongoOS
   platform driver, device-tree values, iBoot/checkra1n transfer, and USB
   handoff together.
2. No local runtime trace captured CurrentEL, VBAR_EL1, SCTLR_EL1,
   TTBR0_EL1, TTBR1_EL1, upload-buffer physical/virtual aliases, boot-argument
   validity, allocator reservations, or live-range overlap immediately before
   jump_to_image_extended.
3. The A10 QEMU fixture and A13 synthetic harness cannot fill those gaps.
4. The available Apple-target artifact has a misleading embedded version banner
   because of upstream Makefile variable precedence. A correctly identified
   artifact must be rebuilt in an isolated copy with a command-line
   PONGO_VERSION assignment before a future runtime comparison.
5. The local LLVM/cctools directory's package provenance remains incomplete,
   as already documented by M2-A12.

The minimum additional prerequisite is an existing, offline, Apple-platform
emulator or trace backend that can run the pinned PongoOS artifact through
checkra1n/iBoot-equivalent entry and expose CPU/system registers plus physical
memory/allocator state at the raw jump. A generic AArch64 QEMU probe is not
that prerequisite.

## 8. Final recommendation

CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE

Recommended next action: validate availability of one documented offline
Apple-SoC/iBoot/checkra1n-capable emulator with instruction-level register and
memory tracing; if none is available, stop the offline runtime investigation
without attempting another generic QEMU probe or a physical-device test.
