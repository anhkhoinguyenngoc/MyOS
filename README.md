# MyOS — A8 Diagnostic Architecture

## Overview
MyOS is a research operating system for the Apple A8 (iPhone 6, CPID 0x7000, T7000).
It runs via the checkm8 BootROM exploit through PongoOS as a dynamic module
(`modload`), or via custom images through checkra1n (-k flag).

**Current status: A8 = UNSUPPORTED** (per platform/apple/a8/iphone6.h, all hardware
fields are `MYOS_ADDR_UNKNOWN`). No physical device, no USB/DFU, no boot verified.

## Repository State

The platform-tree refactor — `kernel/`, `platform/`, the host diagnostic
pipeline, and their tests — is committed. See `PROJECT_CONTEXT.md` for
verified project status, open limitations, and the operating contract for
future sessions. (Historical milestone reports in this repository describe
the tree layout as of their writing.)

## Quick Links
- [Documentation Index](docs/README.md) — full documentation map
- [Milestone Reports](AUTONOMOUS_SESSION_REPORT.md) — M7.13 onward
- [Architecture](ARCHITECTURE.md) — boot architecture description
- [Boot Feasibility](BOOT_FEASIBILITY.md) — feasibility audit
- [Boot Diagnostics](BOOT_DIAGNOSTICS.md) — diagnostic framework
- [Build and Test](#build-and-test) — prerequisites and supported commands
- [Test harness](tests/) — host regression targets
- [Protected Artifacts](artifacts/m-p2.26-cb83dd83/manifest.json) — recorded provenance and verified hashes

## Implemented
- Host-side exception record decoder (MYOSA8E1 format)
- QEMU runtime harness (`make qemu`, `make raw-entry-runtime-test`)
- Capture envelope schema (v1.0.0)
- Exception-record failure classification (**VALID / INCOMPLETE / TRUNCATED /
  MALFORMED**), exercised with real assertions in
  [`tests/test_a8_exc_record_decoder.py`](tests/test_a8_exc_record_decoder.py)
  (bad magic/version/size → MALFORMED, short input → TRUNCATED, marker=0 →
  INCOMPLETE) and [`tests/test_a8_pipeline_integration.py`](tests/test_a8_pipeline_integration.py)
  (all four statuses end-to-end plus CLI fail-closed paths); stale-artifact
  detection lives in [`tests/test_a8_raw_entry.py`](tests/test_a8_raw_entry.py)
  (run by `make raw-entry-check`)
- 16-pass decoder test suite

## Not Implemented / Blocked
- Physical device boot or display
- A8 hardware initialization
- MMU/cache configuration on real hardware
- USB/DFU driver ownership

## Safety Constraints
- No physical device, USB/DFU, exploit execution, or flashing
- No sudo, package installation, or network downloads
- No commits or pushes
- Protected artifacts and PongoOS remain unchanged
- `pongoos-src/` remains read-only

## Repository Structure
- `docs/` — organized technical documentation
- `AUTONOMOUS_SESSION_REPORT.md` — append-only milestone history
- `tools/` — host-side diagnostic tools
- `platform/apple/a8/` — A8-specific source files
- `tests/` — regression test harness

## Build and Test

### Prerequisites (offline host)
| Tool | Minimum tested version | Used for |
|---|---|---|
| `aarch64-linux-gnu-gcc` / `-ld` / `-objcopy` / `-nm` / `-objdump` | binutils 2.47, gcc 16.1.0 | AArch64 freestanding builds |
| `make` | 4.x | build orchestration |
| `python3` | 3.14 | host tests and diagnostic pipeline |
| `qemu-system-aarch64` | 11.1.1 | `make qemu` and `raw-entry-runtime-test` (emulation only) |
| `cmp`, `sha256sum`, `timeout` | coreutils/diffutils | determinism and artifact checks |

No network access, device, or package installation is required or used by any
target below. `PLATFORM` defaults to `qemu-virt`; selecting `PLATFORM=apple-a8`
fails the build deliberately (see `PLATFORM_INTERFACE.md`).

### Supported commands
- `make host-test-all` — runs every `tests/test_*.py` offline; writes only to
  temporary files. Exit 0 required.
- `make qemu` — builds the kernel and boots it on QEMU `virt`; PASS requires
  the `MYOS_KERNEL_PASS` banner. Emulation evidence only.
- `make raw-entry` — builds the production raw-entry image to
  `build/raw_entry.bin`. This target is how the production image is produced;
  do not run it casually: the current artifact's recorded hash is
  `0eb3af05…b2cd8c635` (see `artifacts/m-p2.26-cb83dd83/manifest.json`).
- `make raw-entry-check` — rebuilds a *second* copy into
  `build/raw_entry-verify/`, byte-compares it with `build/raw_entry.bin`
  (determinism), then runs the strict artifact validator
  (`tests/test_a8_raw_entry.py --require-artifact`). Never overwrites the
  production image.
- `make raw-entry-runtime-test` — builds a TEST-ONLY image under
  `build/raw-entry-runtime/`, boots it on QEMU with a controlled exception
  trigger, and validates the on-guest exception record against the trigger
  contract. Writes evidence files under `build/raw-entry-runtime/evidence/`.
  The production image is hash-checked before and after.
- `make integrity-check` — read-only verification of the existing build
  artifacts against the linker scripts and Makefile variables
  (`tests/check_build_integrity.py`, 60 checks) plus the PongoOS probe
  source/layout invariants (`tests/check_pongo_probe.py`). Requires prior
  build outputs; never rebuilds or modifies anything.

### Generated evidence vs. source
Everything under `build/` is generated (binaries, logs, QEMU captures,
decoded envelopes, evidence directories) and is safe to delete and regenerate.
`pongoos-src/` is vendored upstream source and stays unmodified.
`artifacts/m-p2.26-cb83dd83/` holds *preserved* build outputs plus
`manifest.json` — those four binaries are protected: never regenerate or
overwrite them.

### Validation boundary
- **Validated offline:** host test suite, QEMU `virt` kernel boot, raw-entry
  artifact contract and deterministic rebuild, QEMU exception-path runtime
  scenarios (3), diagnostic decode pipeline.
- **Not validated:** physical iPhone 6 boot, physical display, real A8
  memory-map/cache behavior, USB/DFU handoff. Apple A8 support is
  **UNSUPPORTED** (contract-only, `platform/apple/a8/iphone6.h`); physical
  boot/display are **UNVERIFIED**; physical-test readiness is **NO-GO**.
  QEMU results are emulation evidence and are never promoted to hardware
  claims.

### PongoOS artifact provenance
`artifacts/m-p2.26-cb83dd83/manifest.json` records how the four PongoOS
binaries were built (pinned Docker image, exact package versions, source
commit `cb83dd83…`). That is **recorded** provenance: the hashes are
independently re-verifiable at any time with `sha256sum`, but the Docker
build itself has **not** been re-run in this repository, so the build is not
independently reproduced here.
