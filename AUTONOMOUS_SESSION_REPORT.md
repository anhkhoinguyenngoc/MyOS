# Autonomous Kernel Correctness Session — Stage-Ack Invariant

Session start: 2026-10-15T19:04:48+07:00
Session end:   2026-10-15T19:17:05+07:00
Starting commit: e5b747e7b2bf54cc4f94aed0259f1f17ee14728e
Final commit: see independently verified remote SHA in terminal summary.

## Issue found
`boot_diag_ok()` in `arch/aarch64/boot_diag.c` accepted an OK for any stage regardless
whether `diag_state.current_stage` matched it. A call for a stage not currently being
begun (e.g. typo / driver ordering bug) would claim an earlier failure clean, distorting
diagnostics and stateful recovery.

## Source evidence
Function previously:
```
if (diag_state.current_stage != stage) {
    /* Stage mismatch - log but continue */
}
diag_state.last_ok_stage = stage;
```
Thus no rejection or telemetry effectively.

## Reproduction
Temporarily invoked `boot_diag_ok(BOOT_STAGE_CPU_INIT)` at entry to `kernel_main`,
when the diagnostic current stage was not CPU_INIT. Before patch, QEMU boot report
marked `CPU_INIT [OK]` despite no corresponding benign begin/normal CPU initialization sequence.
After patch, QEMU printed:
`WARN: boot_diag_ok for CPU_INIT is ignored (current stage mismatch)`
and `CPU_INIT` was not marked `[OK]`.
Log evidence saved under `/tmp/myos-autonomous-session/before.log` and
`/tmp/myos-autonomous-session/after2.log`.

## Fix
`boot_diag_ok()` now returns without changing state when `current_stage != stage`.
It emits a warning so the caller can identify the ordering bug early. Valid stages still
advance as before.

## Validation
- `make qemu`: PASS (exit=0), serial contains MYOS_KERNEL_PASS.
- `make host-test-all`: PASS 300 tests.
- eight offline targets: PASS.
- integrity: 4 artifacts PASS, 10/10 pongo build baseline, nested repo unchanged.

## Notes
A8 physical compatibility remains UNKNOWN / UNVERIFIED.
No sudo, no firmware rebuild, no device access.
