# D2 Plan — PongoOS Bootstrap Feasibility (RESEARCH ONLY)

## Objective
Determine whether PongoOS provides a realistic, documented, open-source bootstrap path from the
physical iPhone 6 DFU state (USB `05ac:1227`) toward eventual MyOS-controlled AArch64 execution
on Apple A8 / T7000 / CPID `0x7000`, and define the exact next safe experiment.

## Scope
- RESEARCH ONLY. No new bootstrap is executed on the phone in this phase.
- Physical device contact is limited to **passive DFU detection** (lsusb). No erase, restore,
  update, downgrade, flash, or persistent-storage write.
- Primary sources only: the PongoOS repository (github.com/checkra1n/PongoOS) and checkra.in.
- Every claim is classified: **CONFIRMED BY SOURCE** / **INFERRED** / **UNKNOWN** / **NOT APPLICABLE**.

## Constraints / Safety Boundaries
- Do NOT keep retrying gaster blindly (M1 already established gaster's checkm8 does not complete
  on this host: 19 spray/patch cycles, `STAGE_PWNED=0`).
- Do NOT run `sudo` / `pacman` automatically. Missing dependencies are identified and the exact
  install commands are reported for manual approval.
- Do NOT claim physical execution unless it is observed on the device.
- checkm8 / PongoOS are RAM-only and tethered: a reboot returns the device to stock iOS. No
  persistent state is modified. This is what makes the next experiment reversible.

## Research Questions
1. What is PongoOS, what license, and what is its official support matrix (does it cover A8/iPhone 6)?
2. What is the exact handoff contract from checkra1n into PongoOS (entry point, exception level,
   MMU state, boot_args semantics)?
3. What are the two integration sub-paths (D2a: MyOS as a PongoOS module; D2b: MyOS as a custom
   `checkra1n -k` image) and what does each require?
4. What is the observable channel (PongoOS USB shell protocol) and what does a host-side client
   need to implement?
5. What are the real limitations (checkm8 dependency, semi-tethered, closed-source checkra1n,
   Linux device caveats, host-hardware reliability notes)?
6. What toolchain is required to build PongoOS and a MyOS module, and what is missing on this host?
7. Does D2 escape the checkm8 dependency that blocked M1? (Answer: NO — see D2_FEASIBILITY.md.)

## Method
- Fetch and read PongoOS primary sources: README, Makefile, `src/boot/entry.S`,
  `src/boot/stage3.c`, `src/shell/main.c`, `src/shell/usbloader.c`, `src/dynamic/modload.c`,
  `src/dynamic/modload_macho.c`, `src/drivers/usb/usb.h`, `example/testmodule/*`.
- Fetch checkra.in and checkra.in/linux for the support matrix and Linux distribution guidance.
- Cross-check the A8 checkm8 profile already vendored in `tools/external/gaster/gaster.c`
  (CPID `0x7000`, SRTG `iBoot-1992.0.0.1.19`).
- Audit the host toolchain for the PongoOS build prerequisites.

## Deliverables
- `D2_PLAN.md` (this file)
- `D2_FEASIBILITY.md` (analysis + evidence classification)
- `D2_RUNBOOK.md` (the next experiment, defined but NOT executed)
- `D2_REPORT.md` (findings + decision)

## Decision Criteria
- **D2-GO** — PongoOS is a credible, source-confirmed bridge AND a concrete, safe, reversible,
  minimal next experiment exists that directly tests the open question. The next experiment is
  defined but NOT executed automatically.
- **D2-BLOCKED** — a hard prerequisite is missing (e.g. no host where checkm8 completes) and no
  non-destructive workaround exists.
- **D2-ABANDON** — a source-based finding shows PongoOS cannot run on A8/T7000 at all.

## Current Status
- Research complete. Decision and next experiment recorded in `D2_REPORT.md`.
