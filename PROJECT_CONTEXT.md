# MyOS Project Context

> Persistent source of truth for future ChatGPT and coding-agent sessions.
> Read this before planning any work, then re-verify every status claim
> against the actual repository (see the Operating Contract). This file is
> updated at the end of every completed task.

## Primary objective (do not dilute)

**Display the exact text `Hello User` directly on the physical iPhone 6
screen using MyOS.**

- Target hardware: Apple iPhone 6 — Apple A8/T7000 SoC (CPID 0x7000), booted
  via the checkm8 BootROM exploit through PongoOS (or checkra1n `-k`).
- This first visible output milestone takes precedence over general
  operating-system feature work. Terminal, filesystem, and driver ideas are
  deferred until `Hello User` renders on the physical screen.

## Architecture and development direction

| Layer | Location | Status |
|---|---|---|
| ARM64 kernel code | `kernel/`, `arch/aarch64/` | built and exercised on QEMU virt |
| QEMU virt platform | `platform/qemu-virt/` | the only implemented platform |
| Apple A8 platform | `platform/apple/a8/` | contract/spec only — **UNSUPPORTED** as a boot target |
| Host validation tools + tests | `tools/host/`, `tests/` | run offline on the host; 34 test files |
| PongoOS + protected artifacts | `pongoos-src/` (gitignored), `artifacts/` (gitignored) | external boot chain; never modified |

Validation-boundary rules (state these whenever reporting progress):

- **UART output is not screen output.** All current "Hello, World!" evidence
  is serial output (QEMU UART or documented diagnostic frames), never pixels.
- **QEMU success is not physical-device success.** Nothing has been shown on
  an iPhone.
- **A passing raw-entry integrity check is not a physical boot.** It proves
  artifact contract compliance and byte-deterministic rebuild only.
- **Physical boot/display: UNVERIFIED. Apple A8: UNSUPPORTED. Physical-test
  readiness: NO-GO.**

## Verified state — M7.31 pre-commit audit (2026-10-21, HISTORICAL)

Evidence from the audit session; re-verify before relying on any of it.

- `make host-test-all`: **PASS — 34/34 test files**.
- `make integrity-check`: **PASS — 95 checks** (67 build-integrity incl. 7
  declared-dependency freshness checks, + 28 Pongo probe checks).
- `make raw-entry-check`: **PASS** — artifact validator all-pass, plus `cmp`
  byte-equality between `build/raw_entry.bin` and an independent second
  derivation (`build/raw_entry-verify/`).
- Audit classified **all 75 Makefile rule prerequisites** against Git HEAD
  (re-counted in M7.32: still exactly 75).
- **Untracked source build inputs at the pre-commit baseline:** the M7.31
  report identified **25**; an M7.32 recount of the same classification
  measured **24** of the 35 non-build prerequisites (report off-by-one;
  substance confirmed) — all of `kernel/boot_diag.*`, `kernel/console.*`,
  most of `platform/**`, `tests/a8_runtime/*`, and `tools/host/m1_proto.h`.
  This is why the refactor cannot yet be reproduced from a clean checkout.
- QEMU boot and runtime-exception validation were **not re-run** during
  M7.31 (those targets would overwrite existing evidence).
- **Clean-checkout reproducibility: UNPROVEN** until a fresh checkout can be
  built and validated.

Never claim (no evidence exists): that the refactor is committed, that a
clean checkout has passed, or that `Hello User` (or anything) has appeared
on an iPhone screen.

## Worktree / Git snapshot (M7.32, 2026-10-21)

- Base HEAD at this M7.32 update: `ffbf25d55ba3b3f584f3ef05242334cc79acabfe`
  (this file lands as its own dedicated commit on top of it, before any
  refactor commit).
- Refactor footprint in the working tree (excluding this file): **19
  modified + 10 deleted + 51 untracked = 80 entries, 0 staged** — the
  entire platform-tree refactor, still uncommitted.
- The refactor is planned as **one dedicated, reviewed commit of 79 paths**
  (18 modified + 10 deleted + 51 untracked; `AUTONOMOUS_SESSION_REPORT.md`
  deliberately excluded — append-only history with M7.29-documented stale
  counts). Note: `AUTONOMOUS_SESSION_REPORT.md` currently ends at **M7.23**;
  later milestone reports (M7.24–M7.31) exist only in session transcripts —
  this file is the in-repo bridge for those results.
- **Remote divergence (found M7.32):** the server-side `origin/main` is
  ahead of the local branch (push dry-run rejected non-fast-forward).
  Reconciling (fetch/pull/rebase — user decision) is required before any
  push of the refactor work. No fetch/pull was performed.

## Outstanding refactor blockers (from M7.31)

1. `README.md` lines 14–15 ("Repository State" notice, pins `ffbf25d5…`)
   become stale/self-contradictory the moment the refactor commit lands —
   revise within that commit.
2. `BOOT_DIAGNOSTICS.md` lines 128 and 254 reference files the refactor
   deletes (`arch/aarch64/boot_diag.c`, `boot/aarch64/m1_entry.S`) —
   fix in the same commit or an immediate follow-up.
3. The refactor still requires: user-reviewed dedicated commit →
   clean-checkout build + test validation → only then may reproducibility
   be claimed.

## Roadmap (evidence-gated; mark complete only with proof)

1. **In progress** — Complete, reviewable source tree + reproducible
   host/QEMU validation workflow. *(M7.31 audit complete; commit and
   clean-checkout validation pending.)*
2. **In progress** — Finish the platform-tree refactor with explicit file
   inventory and integrity checks. *(Inventory + checks done in M7.31;
   commit pending.)*
3. **Not started** — Investigate the actual Apple A8 boot and handoff
   requirements (PongoOS `bootr`, memory map, EL state).
4. **Not started** — Establish a viable display initialization path for
   the iPhone 6 (this is the hard unknown behind the primary milestone).
5. **Not started** — Render `Hello User` on the physical iPhone 6
   (**primary milestone**).
6. **Gated on 5** — Terminal and broader OS features.

The roadmap is a plan, not evidence that any stage works.

## Operating contract (mandatory for every future session)

1. Read this file before planning work.
2. Inspect the actual repository state (`git status`, relevant sources)
   before trusting any recorded status here.
3. Pick the smallest concrete task that advances the current milestone.
4. Preserve unrelated work and existing evidence; never stage broadly
   (no `git add -A` / `git add .`); never modify `pongoos-src/`,
   `build/raw_entry.bin`, `artifacts/`, or `AUTONOMOUS_SESSION_REPORT.md`.
5. Define acceptance criteria up front and run appropriate tests
   (never run targets that overwrite evidence unless that is the task).
6. Distinguish verified-now results from historical results and assumptions.
7. At the end of every task, update this file: verified progress, test
   results, decisions, blockers, and the next concrete task.
8. Review the context diff for accuracy and secrets before staging.
9. When the task's safety rules permit it: stage **only**
   `PROJECT_CONTEXT.md` (`git add -- PROJECT_CONTEXT.md`), create a
   dedicated commit, and push normally (no force).
10. If a task forbids file changes, staging, commits, or pushes, that
    prohibition wins — report the context update as pending instead.

## Next concrete task

1. User decision: reconcile the `origin/main` divergence (local is behind
   the server), since no fetch/pull/rebase is allowed inside agent tasks
   unless explicitly authorized.
2. Resolve blockers 1–2, then commit the refactor as one reviewed commit
   (79 paths: all modified/deleted/untracked files except
   `AUTONOMOUS_SESSION_REPORT.md`; path lists are derivable from
   `git status`), followed by clean-checkout build + test validation.
3. With a reproducible baseline established, move to roadmap 3–4
   (A8 boot/handoff investigation → display init path), driving toward the
   primary milestone: **`Hello User` on the physical iPhone 6**.
