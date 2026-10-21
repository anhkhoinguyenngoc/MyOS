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
| Host validation tools + tests | `tools/host/`, `tests/` | run offline on the host; 36 test files |
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
  This is why the refactor could not yet be reproduced from a clean checkout.
  *(Superseded by M7.34: all of those inputs are now committed in the
  refactor commit, and a clean clone builds `raw_entry.bin` byte-identical.)*
- QEMU boot and runtime-exception validation were **not re-run** during
  M7.31 (those targets would overwrite existing evidence).
- **Clean-checkout reproducibility: UNPROVEN** as of M7.31 — measured in
  M7.34 (see below): build path proven, full suite still needs the
  gitignored `pongoos-src/` inputs.

Never claim (no evidence exists): that the full test suite passes from a
clean checkout without its gitignored local inputs, or that `Hello User`
(or anything) has appeared on an iPhone screen. *(Superseded: "the
refactor is committed" is now TRUE — it landed as `6aecf03` in M7.34.)*

## Verified state — M7.34 reconciliation (2026-10-21)

Evidence captured in the M7.34 session; re-verify before relying on any of it.

- **Commits created:** `6aecf0360097368c330b7f18dc306fca88e4c45f`
  (refactor, parent `0cca6c3`, 80 paths =
  41 new + 19 modified + 10 renames, 9664+/259−) and
  `b5f7db35820dab94b919cabe7b8cba35d071e974` (merge,
  parents `6aecf03` + `2b5cc79`). Pre-commit gates: `git diff --check`
  clean (after removing 5 trailing-whitespace lines in the new decoder
  test + a blank line at EOF in `uart_pl011.S`), `host-test-all` 34/34.
- **Divergence RESOLVED.** `git ls-remote origin refs/heads/main` pinned
  the server at `2b5cc7986761ec81ebe8b4279bd07c3d0a96726e` (unchanged
  since M7.33); ordinary fetch + non-rewriting merge integrated it; local
  is now strictly ahead (fast-forward push possible, no force).
- **Makefile gate (M7.33's predicted silent-drop did NOT occur):** the
  real merge raised an explicit conflict in `Makefile`. Resolved keeping
  BOTH sides: ours byte-preserved + theirs `+25/−0` (restoring the shared
  `# ===` separator the matcher consumed as context). Verified against
  both parents: `diag-parse-test:` (L488), `usb-detect-test:` (L499),
  `.PHONY` (L504); recipes propagate exit codes; conflict evidence kept
  in `/tmp/opencode/m734/`.
- **Tests after merge:** `make host-test-all` **PASS — 36/36** (0 fail,
  0 skip; wildcard discovery picks up the two PR #1 tests);
  `make diag-parse-test` PASS; `make usb-detect-test` PASS;
  `make integrity-check` PASS (95 PASS lines, 67/67 section, 0 fail);
  `make raw-entry-check` PASS (artifact hash stayed `0eb3af05…8c635`);
  `py_compile` 6 files OK; `bash -n` both changed shell scripts OK.
- **Clean-checkout audit (local clone, new in M7.34):** `make raw-entry`
  from a pristine clone **reproduces `build/raw_entry.bin`
  byte-identical** (`0eb3af057a6ba82ad55c114d0a0056c1d3cea686375f535c813ba25b2cd8c635`);
  the two M-P3.0 targets pass clean; `host-test-all` clean = **29/36**
  (7 files fail solely on missing gitignored `pongoos-src/` sources, 13
  skips are graceful absences). QEMU was NOT run (evidence-protection
  rule), so clean-checkout *boot* validation remains untested.
- **Preservation:** all 5 protected artifact hashes byte-identical to the
  M7.34 baseline; `pongoos-src` clean at `cb83dd83`; report file untouched;
  no `git gc`, reset, clean, stash, or force-push anywhere.


## Git snapshot (M7.34, 2026-10-21)

- Chain: `ffbf25d5` ← `0cca6c3` (first commit of this file) ← `6aecf03`
  (refactor, 80 paths) ← `b5f7db3` (merge of `origin/main` = `2b5cc79`,
  PR #1) ← this M7.34 context update.
- Working tree: only `AUTONOMOUS_SESSION_REPORT.md` modified (append-only,
  deliberately never committed; it ends at **M7.23**; M7.24–M7.34 exist
  only in session transcripts — this file is the in-repo bridge).
- **Remote divergence: RESOLVED in M7.34** (see Verified state above);
  local is strictly ahead of `origin/main` = `2b5cc79`; normal push only,
  never force.

## Blockers resolved in M7.34; current follow-ups

Resolved (proof in Verified state above):

1. `README.md` Repository State notice — rewritten for a committed tree
   inside the refactor commit (`6aecf03`).
2. `BOOT_DIAGNOSTICS.md` stale paths (old lines 128/254) — fixed inside
   `6aecf03` (`kernel/boot_diag.c`, `platform/apple/a8/m1_entry.S`);
   verified those were the file's only stale references.
3. Refactor committed and reconciled; clean-clone build reproduces
   `build/raw_entry.bin` byte-identical.

Follow-ups (none blocking):

- `ARCHITECTURE.md` (tracked, unchanged by the refactor) still references
  pre-refactor paths (`boot/aarch64/*`) — refresh in a future doc commit.
  Historical milestone reports (`M2_A*`, `M_P2_*`, `M1_*`) intentionally
  untouched: they describe their time of writing.
- **After any future pull: audit `Makefile` for dropped targets** (M7.34
  lesson — a 2-way `merge-file` simulation had predicted a silent drop
  where the real merge instead conflicted; either way the targets are the
  at-risk content: `diag-parse-test`, `usb-detect-test`).
- Full-suite clean-checkout pass requires the gitignored `pongoos-src/`
  tree (7 test files depend on it) — an inherent local-input dependency,
  not a code defect.

## Roadmap (evidence-gated; mark complete only with proof)

1. **In progress** — Complete, reviewable source tree + reproducible
   host/QEMU validation workflow. *(M7.34: committed, reconciled, clean
   build proven byte-identical; QEMU boot not re-run — evidence rule.)*
2. **Complete (M7.34)** — Finish the platform-tree refactor with explicit
   file inventory and integrity checks. *(Landed as `6aecf03`: 80 paths,
   `host-test-all` 36/36, integrity 95 PASS.)*
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

1. Confirm the M7.34 push landed (`git status` shows `main` at 0 ahead of
   `origin/main`, or `git ls-remote origin refs/heads/main` equals local
   HEAD). If still ahead, push normally — fast-forward only, never force.
2. Health gate for any future session: `make host-test-all` (36/36
   expected); after any `git pull`, run the Makefile target audit noted
   under Follow-ups before trusting the tree.
3. With the baseline established, move to roadmap 3–4 (A8 boot/handoff
   investigation → display init path), driving toward the primary
   milestone: **`Hello User` on the physical iPhone 6**.
