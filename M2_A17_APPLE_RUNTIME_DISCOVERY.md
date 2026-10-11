# MyOS M2-A17 — Apple-Compatible Runtime Discovery & Feasibility Verification

Date: 2026-10-11
Run on: Arch Linux main PC (`/home/khoi/Projects/myos`), read-only inspection + documentation research
Physical iPhone: **not connected, not tested, not required, not claimed**
Target: iPhone 6 / Apple A8, CPID `0x7000`
Pinned PongoOS: `4c9b7541629234147fcc778f0ce4162482aaccef`
Pinned Newlib: `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` (verified from the live checkout this task)
Previous report: `M2_A16_HANDOFF_EVIDENCE_AUDIT.md`

**Executive decision: `REQUEST_AUTHORIZED_ACCESS`**

One realistic candidate was found and its required *debugging* capabilities are
substantiated by current official vendor documentation, including support for
an iPhone 6 (A8) virtual device, custom raw-binary BootROM/iBoot chainloading,
and a GDB-remote stub with register inspection, single-step, hardware
breakpoints, and system-register access: **Corellium virtual devices**. What is
*not* substantiated anywhere is that the **pinned PongoOS revision's**
`pongo_boot_raw()` → `jump_to_image_extended` path has been executed on
Corellium — that is the first gate of the verification procedure in Section 7.
No such environment is accessible from this workspace (no account, no
subscription, no local client), so the honest next action is to request
authorized access and run the gated procedure. Every other credible candidate
is source-substantiated as incompatible or insufficient for the pinned
handoff. No generic QEMU test was rerun for this report.

## 1. Phase 0 — Protection of existing work

Recorded in `build/audit/m2-a17_preflight.txt` before any research:

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `3bea11141fea05bb90017069cff22b9a37d91e9e` |
| Status | Identical to the A16 preflight: 4 pre-existing modified files (`Makefile`, `arch/aarch64/boot_diag.c/.h`, `vectors.S`), the same pre-existing untracked set, plus `M2_A16_HANDOFF_EVIDENCE_AUDIT.md` from the previous task |
| PongoOS | `4c9b7541629234147fcc778f0ce4162482aaccef`, clean (`git status --porcelain` empty) |
| Newlib | `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`, clean |

No reset, clean, stash, rebase, or overwrite was performed. Nothing under
`pongoos-src/`, kernel, bootstrap, linker scripts, fixtures, or prior reports
was modified. This task produced no tracked changes; the only new files are
this report and the ignored `build/audit/m2-a17_preflight.txt` record. Prior
evidence hashes were re-derived and compared: all 18 prior reports
(`M2_A*.md`, `M2_REPORT.md`, `D2_PHASE2_REPORT.md`) unchanged.

## 2. Phase 1 — Runtime requirements matrix

Derived from `M2_A16_HANDOFF_EVIDENCE_AUDIT.md` §4/§7 and the pinned source.

| # | Capability | Mandatory / Desirable | Why it matters (source basis) |
|---|---|---|---|
| R1 | ARM64 execution compatible with Apple A8-class boot state (EL1-capable CPU, Apple RAM layout around `0x800000000`) | **Mandatory** | The handoff target is `upload_va - kCacheableView + 0x800000000` (`src/kernel/entry.c:311`); the runtime must present that address regime |
| R2 | Ability to load/build the pinned PongoOS revision and reach its main loop | **Mandatory** | `hal_init` panics with no matching platform driver for the CPID (`src/drivers/hal/hal.c:209`); `t7000_probe` matches `cpid == 0x7000` (`src/drivers/plat/t7000.c:29-30`) |
| R3 | Ability to enter the actual `pongo_boot_raw()` → `jump_to_image_extended` path (upload + `bootr`) | **Mandatory** | That exact path is the thing under verification (`src/shell/main.c:40-48,257`; `src/kernel/entry.c:309-312`) |
| R4 | Breakpoint/single-step at the handoff and at the first MyOS instruction | **Mandatory** | A16 blocker: no runtime value of anything at the jump is known |
| R5 | Access to `CurrentEL`, `SCTLR_EL1`, `VBAR_EL1` | **Mandatory** | A16 Items 2, 3, 5 — the early-vector window cannot be closed without `VBAR_EL1` |
| R6 | Access to `TTBR0_EL1`/`TTBR1_EL1`/`MAIR_EL1`/`TCR_EL1` and effective memory mappings | **Mandatory** | A16 Items 3, 7 — proves MMU-off and the mapping the bootstrap runs against |
| R7 | Ability to read the upload buffer VA/PA, size, allocator state, and nearby live memory | **Mandatory** | A16 Items 6, 7 — overlap question |
| R8 | Ability to read boot-argument pointer/values (`x0` = `gBootArgs`) and all GPRs at the jump | **Mandatory** | A16 Items 1, 9 |
| R9 | Reproducible, exportable debugger traces and logs | **Mandatory** | Acceptance requires repeatable inspectable evidence |
| R10 | Access authorization obtainable without bypassing licensing/access controls | **Mandatory** | Hard constraint |
| R11 | Symbols or a way to resolve `_jump_to_image_extended` / `loader_xfer_recv_data` / `gBootArgs` addresses | Desirable (workaround exists: `nm` on the built artifact) | Breakpoint placement (`src/boot/jump_to_image.S:42`; `src/shell/usbloader.c:35`; `src/kernel/entry.c:28`) |
| R12 | MMIO visibility from the debugger | Desirable | Early MyOS writes QEMU PL011 MMIO (`boot/aarch64/start.S:22`); Corellium's stub ignores MMIO reads (Section 4) — RAM visibility is what the handoff actually needs |
| R13 | Low setup effort / low cost | Desirable | Trial path exists; commercial pricing is reported but unverified (Section 6) |

## 3. Phase 2 — Candidate comparison

Statuses: `VERIFIED_COMPATIBLE` / `POTENTIALLY_COMPATIBLE` / `INCOMPATIBLE` /
`ACCESS_UNAVAILABLE` / `INSUFFICIENT_EVIDENCE`.

### Candidate A — Corellium virtual devices — `POTENTIALLY_COMPATIBLE` (+ `ACCESS_UNAVAILABLE` from this workspace)

Evidence (all fetched this task, official vendor sources):

1. **iPhone 6 (A8) support and custom raw-binary boot chain** —
   [Corellium Support: iBoot](https://support.corellium.com/device-settings/iboot):
   *"iBoot configuration is currently supported on all iOS devices from iPhone
   6 to iPhone 12 Pro Max"*; *"the application processor can be started from a
   custom BootROM image… A raw binary image must be provided"*; *"the chainload
   will be intercepted and the images substituted for those you specify"*;
   *"You can also patch iBoot / BootROM to disable signature checks."*
2. **Debug stub with the exact capabilities required** —
   [Corellium: Kernel Debugging](https://www.corellium.com/blog/debug-kernel)
   (official vendor blog, technically detailed, 2026-08-30): GDB-remote-protocol
   stub per VM; *"on an iPhone 6, there are two threads: 1 and 2, corresponding
   to CPU 0 and CPU 1"*; *"Single-stepping is supported by using AArch64
   single-stepping features"*; *"Hardware breakpoints and watchpoints are
   supported (up to 4 of each)"*; example GDB `info reg` output showing
   `x0`–`x30`/`PC`; `monitor sr ttbr1_el1=…` showing **system-register
   read/write** (covers `SCTLR_EL1`, `VBAR_EL1`, `TTBR0/1_EL1`, etc.);
   documented limitation: *"Memory addresses from the debugger are passed
   through virtual-to-physical address translation… only actual RAM is visible
   to the debugger; access to MMIO regions is ignored and returns 0."*
3. **Debugger front-ends** —
   [Corellium Support: LLDB/GDB](https://support.corellium.com/features/connect/lldb):
   per-device stub address/port; AArch64-compatible GDB required (the local
   GDB 18.1 is built `--enable-targets=all`, see Section 5).
4. **Live patching/inspection primitive** —
   [Kernel Hooks using the Debugger](https://support.corellium.com/features/kernel-hooks/kernel-hooks-debugger):
   `mon patch` with `cpu.x[…]`/`cpu.sp` expressions — a mechanism to insert
   read-only trace logic at runtime without modifying any PongoOS source.

| Requirement | Status on Corellium (evidence) |
|---|---|
| R1 A8-class device | Substantiated (iPhone 6 explicitly named for iBoot config and as a 2-CPU debug example) |
| R2 load pinned PongoOS | **Unresolved** — custom raw BootROM/iBoot chainloading is documented; that *this* PongoOS stage can be entered from it is not documented anywhere |
| R3 reach `bootr` path | **Unresolved** (same gap; requires USB-upload path or equivalent, undocumented for virtual devices) |
| R4–R8 stepping/registers/memory | Substantiated by vendor documentation (blog + LLDB/GDB pages) |
| R9 exportable traces | Substantiated (GDB/LLDB sessions are local, scriptable) |
| R10 authorization | Requires Corellium account/trial approval — **not available in this workspace** |

Unresolved assumptions (must not be treated as facts): that a virtual iPhone 6
device can be started from a custom BootROM/iBoot payload that chains into the
pinned PongoOS stage with a context equivalent to checkra1n's; that the Pongo
USB upload path (or another upload mechanism) functions on the virtual device;
that the debug stub is attachable *before* PongoOS reaches the scheduler loop;
that trial-tier devices include iPhone 6 and debug-stub access.

Overall: `POTENTIALLY_COMPATIBLE` for requirements R1 and R4–R9;
`INSUFFICIENT_EVIDENCE` for R2–R3; `ACCESS_UNAVAILABLE` locally.

### Candidate B — `citruz/pongoOS-QEMU` — `INCOMPATIBLE`

[Repository](https://github.com/citruz/pongoOS-QEMU) README fetched this task:
it is *"Fork of PongoOS which can be run in QEMU"* — a **fork**, not the pinned
revision; the working list is ramfb, PL011, GICv2, device-tree, fw_cfg, and
*"Not working: Everything else"*; it requires the patched `utmapp` QEMU fork
and a QEMU `virt` memory-map patch; GDB stepping only under TCG. The pinned
revision additionally cannot reach its main loop on a generic machine:
`hal_init` panics without a matching CPID platform driver
(`pongoos-src/src/drivers/hal/hal.c:209`), and `virt` supplies no Apple
`arm-io`/CPID device tree. Even if ported, it would not be the pinned
revision's Apple handoff.

### Candidate C — `TrungNguyen1909/qemu-t8030` — `INCOMPATIBLE`

T8030/A13 (iPhone 11) emulation; repository archived (per M2-A15 §3B, still
archived). The pinned platform-driver inventory is
`s5l8960, s8000, s8001, s8003, t7000, t7001, t8010, t8011, t8012, t8015`
(verified locally under `pongoos-src/src/drivers/plat/`) — no `t8030` driver,
so the pinned PongoOS cannot run there (`hal.c:209` panic path).

### Candidate D — `alephsecurity/xnu-qemu-arm64` — `INSUFFICIENT_EVIDENCE`

Models an S8000 iPhone 6s Plus and boots a patched XNU directly with GDB
scripting (M2-A15 §3C, references re-checked). The pinned PongoOS does have an
`s8000` driver, which makes it the least-impossible *source-buildable* option —
but nothing in that project or the pinned tree documents entering PongoOS's
SecureROM/checkra1n-derived stage-1→stage-3→`pongo_entry` context on it
(`pongoos-src/src/boot/entry.S`, `src/boot/stage3.c`, `src/boot/main.c:31-39`
assume llktrw-provided `gboot_args`). Using it would require substantial
unproven bring-up, which Phase-2 rule 3 excludes until evidence exists.

### Candidate E — Local generic QEMU + GDB — `INCOMPATIBLE` (as an Apple runtime)

QEMU 11.1.1 re-verified: no Apple/iPhone/PongoOS machine in `-machine help`.
Useful only as the generic-A64 debugger it is; explicitly not an
Apple-compatible runtime (hard constraint).

### Candidate F — Physical checkra1n → PongoOS on the iPhone 6 — out of scope for this task

The only path with prior real-world evidence of entering PongoOS (D2 Phase 2
runbook), but physical testing is prohibited here and was previously blocked at
the host USB/checkm8 layer on this machine (`D3_REPORT.md`). Recorded for
completeness, not recommended by this report.

## 4. Official references and evidence quality

| Reference | Type | Quality for our purpose |
|---|---|---|
| support.corellium.com/device-settings/iboot (fetched, HTTP 200) | Vendor documentation | **Primary** — establishes iPhone 6 support, raw-binary custom BootROM/iBoot, chainload substitution, signature-check patching |
| www.corellium.com/blog/debug-kernel (fetched) | Vendor blog, technically detailed with real command transcripts | **Primary-adjacent** — establishes stub capabilities incl. iPhone 6, single-step, HW breakpoints/watchpoints, GPR and system-register access, RAM-only/MMIO-zero limitation |
| support.corellium.com/features/connect/lldb (fetched) | Vendor documentation | **Primary** — GDB-remote front-end, AArch64 GDB requirement |
| support.corellium.com/features/kernel-hooks/kernel-hooks-debugger (fetched) | Vendor documentation | **Primary** — runtime patch/inspection commands |
| support.corellium.com/subscriptions/trials; corellium.com/trial-solo (search snippets) | Vendor pages | **Primary for access model** (trial request required); no pricing published by vendor |
| appsecsanta.com Corellium review (2026-03, search snippet) | Third-party | **Low** — reports ~$9,995 entry pricing; unverified, labeled as such |
| github.com/citruz/pongoOS-QEMU README (fetched) | Third-party project | **Primary for that project** — establishes fork status and "everything else not working" |
| QEMU `virt` docs + local machine inventory | Vendor docs + local verification | **Primary** — no Apple machine exists |

No claim in this report exceeds what these sources state. Marketing pages were
not used to infer technical compatibility; the compatibility statements come
from support documentation and technically detailed vendor posts only.

## 5. Phase 3 — Local tool inventory (read-only, this task)

| Tool | Status | Relevance |
|---|---|---|
| `qemu-system-aarch64` 11.1.1 | Installed; no Apple machine (re-verified) | Generic only |
| `gdb` 18.1, `--enable-targets=all` | Installed — **AArch64 remote debugging supported locally**, satisfies Corellium's documented GDB requirement without installing anything | Client for the future runtime |
| `gdb-multiarch`, `lldb`, `checkra1n`, `palera1n`, `pongoterm`, Xcode tools, `darling` | Missing | No local Apple-boot path |
| `usbmuxd` / `iproxy` | Installed | Host-side device tooling only; unused |
| Pinned PongoOS toolchain `/home/khoi/myos-toolchain10/usr/bin` (clang, ld64, cctools-strip) | Present | Needed for the correctly version-tagged rebuild prerequisite |
| A12 reproducible artifacts (`build/audit/m2-a12/repro/build/…`) | Present, hashes match A14 (`ARTIFACT_VERIFIED`, re-checked in A16) | Candidate artifact to load once a runtime exists |
| `pyusb` | Missing | Would be needed for a future host-side upload client |

No toolchains were installed, no firmware downloaded, no cloud resources
created, no accounts created. Generic QEMU tests were **not** rerun (hard
constraint: no reruns for the appearance of progress).

## 6. Authorization, cost, and hardware prerequisites

- **Authorization:** a Corellium account is required and access is granted by
  request — vendor trial page states *"To start using Corellium, you need to
  request a trial. We will review your request"*; a Solo trial exists for
  students/researchers. No self-serve instant access is documented. Creating an
  account, if the user chooses to, is the user's own authorized action — this
  task did not create one.
- **Cost:** trial is free but reviewed and time/feature-limited (vendor);
  commercial pricing is not published by the vendor; a third-party 2026 review
  reports a ~$9,995 entry point — **unverified, do not budget on it**.
- **Hardware:** no iPhone required. Host needs an AArch64-capable GDB (present:
  GDB 18.1 `--enable-targets=all`) and VPN for the cloud product (vendor
  documentation).
- **Local prerequisite before any runtime work:** rebuild PongoOS with the
  version tag forced correctly — `PONGO_VERSION=2.5.1-4c9b7541` as a *make
  command-line variable* — in an isolated copy (never in `pongoos-src/`), with
  artifact hashes recorded (addresses the A14 §2 banner limitation).

## 7. Phase 4 — Minimum viable verification procedure (Corellium, gated, NOT executed)

Targets the actual PongoOS handoff. No step involves a physical iPhone.
Numeric expectations are only those derivable from source; everything else is
explicitly "unknown until measured".

**G0 — Access.** Obtain authorized Corellium access (trial or subscription)
with an iPhone 6 (A8) virtual device and debug-stub enabled.
*Stop condition:* no access granted → remain at this report; do not substitute
generic QEMU.

**G1 — Artifact.** Build the pinned revision in an isolated copy with
`PONGO_VERSION=2.5.1-4c9b7541`; record SHA-256 of `Pongo`, `Pongo.bin`,
`PongoConsolidated.bin` (compare against A12 hashes; only the banner string
should differ). *Pass:* hashes recorded; *Fail:* build not reproducible → stop.

**G2 — Platform entry (R2).** Boot the virtual device from a custom
raw-binary BootROM/iBoot chainload (per the vendor iBoot page) toward PongoOS.
*Pass:* Pongo banner/serial output and the `x806` shell prompt appear;
device CPID is `0x7000` and `t7000_probe` path taken
(`src/drivers/plat/t7000.c:29-30`). *Fail:* PongoOS halts in
`hal_init`/stage1 → the chainload context is wrong; **stop condition** —
reclassify Candidate A as `INCOMPATIBLE` for this purpose and re-report.

**G3 — Handoff identification (R3/R11).** Resolve `_jump_to_image_extended`
(`src/boot/jump_to_image.S:42`), `loader_xfer_recv_data`
(`src/shell/usbloader.c:35`), `gBootArgs`, `gEntryPoint`, `ppage_list` from the
built symbol table (or Mach-O symbols via LLDB). Attach the GDB-remote stub
early (vendor stub limit: one connection per VM, all-stop mode).
*Pass:* symbols resolved; stub attached before the scheduler loop.

**G4 — Upload and `bootr`.** Feed a MyOS raw payload through the Pongo upload
path (vendor-documented USB path on virtual hardware is **unverified**; if the
virtual device cannot carry it, use the documented `mon patch` primitive only
as a *fallback to set `loader_xfer_recv_data`/`loader_xfer_recv_count`* — any
such intervention must be logged as part of the evidence). Issue `bootr`.
*Pass:* `gBootFlag == 4` (`BOOT_FLAG_RAW`, `src/kernel/pongo.h:130`).

**G5 — Pre-handoff capture (R4–R8).** Breakpoint on `_jump_to_image_extended`
on the scheduler path (`src/kernel/entry.c:311`). Capture: full GPRs +
`CurrentEL`, `SCTLR_EL1`, `VBAR_EL1`, `TTBR0_EL1`, `TTBR1_EL1`, `MAIR_EL1`,
`TCR_EL1`, `DAIF`; the three argument registers (expected, source-derived:
`x0`=`gBootArgs`, `x2`=`gEntryPoint`; the function then moves them to
`x30`/`x0`/`x8` per `jump_to_image.S:44-46`); `loader_xfer_recv_data` value
and both of its aliases (cacheable VA and `VA - 0x400000000 + 0x800000000`);
upload size; allocator page-state snapshot around the buffer (`ppage_list`
walk); `gBootArgs` memory contents. *Expected, source-derived:* the cleanup at
`src/kernel/lowlevel.c:117-128` will have cleared `SCTLR_EL1.M` and `.C`;
all numeric values are unknown until measured. Any deviation from the
documented instruction sequence in `jump_to_image.S:42-55` is recorded, not
explained away.

**G6 — First-MyOS-instruction capture.** Hardware breakpoint at the computed
raw image address (= upload offset 0). *Pass criteria (all must hold):* PC ==
raw image address; `x0` == `gBootArgs` value captured in G5; `x8` ==
`gEntryPoint`; `x1`–`x3` == 0; `SCTLR_EL1.M == 0`; `CurrentEL == 1`.
*Crash/inconclusive criteria:* PC elsewhere, or stub-detach, or MMIO-only
state needed to proceed (documented stub limitation) → record as inconclusive
with full transcript; do not re-run indefinitely.

**G7 — Early-window fault probe (R5).** With MyOS code now at offset 0, read
`VBAR_EL1` and resolve its value against physical RAM visibility (stub shows
RAM only; MMIO reads return 0). This directly answers A16 Item 5/11 (post-cleanup
vector resolution). A deliberate-fault probe is **optional and gated**: only
with explicit user approval, since it can crash the VM (rebuildable, so
reversible).

**G8 — Evidence artifacts for independent review.** GDB command scripts +
full session transcripts; JSON register dumps at G5/G6; memory dumps with
SHA-256; the virtual-device console log; artifact hashes from G1; the exact
Corellium product/device configuration. All stored with the same discipline as
`build/audit/m2-*`.

**Overall stop conditions:** G2 failure (can't enter pinned PongoOS) ends the
procedure immediately; G4 failure (can't reach `bootr`) likewise; G6
inconclusive twice → stop and re-report rather than iterate silently. If any
stop condition fires, the candidate is reclassified on evidence and a new
report is required before any further action.

## 8. Explicit blockers and unresolved assumptions

1. **Access blocker (decisive):** no Corellium account/subscription exists in
   this workspace; trial access is request-gated and was not requested by this
   task (creating accounts/paying is the user's call).
2. **Compatibility gap:** no public evidence that the pinned PongoOS
   `bootr` handoff has ever run on Corellium (or any other accessible
   runtime). R2/R3 remain `INSUFFICIENT_EVIDENCE` until G2/G4 pass.
3. **Upload-path assumption:** the Pongo USB bulk-upload path on a virtual
   device is undocumented; G4's fallback changes the experiment from
   "end-to-end" to "handoff-state" verification and must be labeled as such.
4. **Stub limitations:** one connection/VM, all-stop, RAM-only memory,
   MMIO reads return 0 (vendor-stated) — tolerable for the handoff (RAM +
   system registers are what's required) but it means no MMIO-side evidence.
5. **Cost uncertainty:** only third-party pricing evidence exists.
6. Carried-forward: the A12 artifact banner/version-tag mismatch; the two known
   regression failures (`FAIL_m1ctl_usage` did not reproduce in A16 — recorded
   there; `test_boot_diag_extended.sh` `.text.vectors` preserved) were not
   touched.

## 9. Evidence-tier distinctions

- **Source inspection (this report's backbone):** all PongoOS/MyOS behavior
  claims cite pinned-revision lines; local facts (tool inventory, driver list,
  `hal.c` panic, `t7000_probe`) re-verified this task.
- **Generic QEMU:** exists locally; explicitly incompatible as an Apple
  runtime; **not rerun** for this task.
- **Synthetic testing (A13):** unchanged, `SIMULATED`, not runtime evidence.
- **Apple-compatible runtime testing:** none performed — none is accessible;
  the whole of Section 7 is a gated plan awaiting access.
- **Physical iPhone testing:** none performed, prohibited, not claimed. No
  statement anywhere in this report claims MyOS boots on an iPhone.

## 10. Recommendation

`REQUEST_AUTHORIZED_ACCESS`

Rationale in one line: every mandatory debugging capability for the
PongoOS-to-MyOS handoff is documented on a vendor-supported iPhone 6 virtual
device (Corellium), the only unproven steps are exactly those the Section 7
gates G2–G6 are designed to prove or disprove, and nothing less than an
authorized runtime can close the A16 blocker.
