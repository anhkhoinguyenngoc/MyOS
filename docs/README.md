# MyOS Documentation Index

This directory organizes maintained technical documentation for the MyOS A8 project.
Contents are grouped by task and purpose; see the root `README.md` for a project overview.

## Documentation Categories

### Architecture — stable descriptions and invariants
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — MyOS boot architecture for iPhone 6 (Apple A8)
- [`BOOT_FEASIBILITY.md`](../BOOT_FEASIBILITY.md) — iPhone 6 boot-chain & security audit
- [`BOOT_DIAGNOSTICS.md`](../BOOT_DIAGNOSTICS.md) — diagnostic framework overview
- [`PLATFORM_INTERFACE.md`](../PLATFORM_INTERFACE.md) — platform selection contract (QEMU vs apple-a8)
- [`M6_1_A8_ENTRY_CAPTURE_SPEC.md`](../M6_1_A8_ENTRY_CAPTURE_SPEC.md) — raw-entry image & exception-record contract

### Development — build, testing, tooling, and contributor workflow
- [`TOOLCHAIN.md`](../TOOLCHAIN.md) — measured host toolchain audit (versions, gaps, policy)
- [Root README — Build and Test](../README.md#build-and-test) — prerequisites, supported commands, outputs
- [Pipeline tool](../tools/host/decode_envelope.py) — decode exception record → capture envelope

### Testing — methodology, validation, reproducibility
- [Decoder tests](../tests/test_a8_exc_record_decoder.py) — MYOSA8E1 record decoder suite
- [Pipeline integration tests](../tests/test_a8_pipeline_integration.py) — hash → evidence → decode → envelope
- Regression targets: `make host-test-all`, `make qemu`, `make raw-entry-check`,
  `make raw-entry-runtime-test` (see [Build and Test](../README.md#build-and-test))

### Troubleshooting — verified failure symptoms and recovery guidance
*(no files currently in this category)*

### Milestone Reports — historical audit evidence; append-only
- [`AUTONOMOUS_SESSION_REPORT.md`](../AUTONOMOUS_SESSION_REPORT.md) — M7.13 onward
- Milestone reports: [M1](../M1_REPORT.md), [M2](../M2_REPORT.md); later
  milestone history (M6, M7.x) lives in `AUTONOMOUS_SESSION_REPORT.md`
- PongoOS integration series: `M_P2_*_REPORT.md` at the repository root
  (D2/D3 era feasibility, plan, and report files also at the root:
  [D2 feasibility](../D2_FEASIBILITY.md), [D3 plan](../D3_PLAN.md),
  [D3 report](../D3_REPORT.md))
- [D3_DIAGNOSTICS.md](../D3_DIAGNOSTICS.md) — host USB/kernel/udev analysis

## Navigation
- Start here if new: [Root README.md](../README.md)
- Find a specific topic: use the root table of contents
- Check the append-only report for milestone history
- Links above are relative to this file (`docs/`), hence the `../` prefix

## Generated / Disposable Outputs *(not maintained here)*
- Build artifacts in `build/`
- `__pycache__/` directories
- Temporary test outputs
- These are not listed in the index; they are ephemeral by nature.
