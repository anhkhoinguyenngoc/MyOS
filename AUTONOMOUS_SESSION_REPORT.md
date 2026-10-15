# Autonomous Kernel Correctness Session — Invalid-Stage Out-of-Bounds Safety

Session start: 2026-10-15T19:17:00+07:00
Session end:   2026-10-15T19:24:00+07:00
Starting commit: 73f21a11b80a9abd1bcbd84158564a91c72afef8
Final commit: see terminal verified SHA.

## Issue
`boot_diag_fail()` in QEMU `arch/aarch64/boot_diag.c` used caller-supplied `stage`
directly in loops and `boot_stage_names[stage]`. If any caller, fault injection surface,
or corrupted state supplied an undefined stage, it could index past the initialized
designated initializer table (`boot_stage_names`), read NULL/all zeros and invoke
`diag_write_str(NULL)`, producing undefined/crashing behavior on a failure path.

## Source evidence
```
for (int i = 0; i <= (int)stage; i++) {
    ...
    diag_write_str(boot_stage_names[i]);  // out-of-table index possible
}
diag_write_str(boot_stage_names[stage]);   // out-of-table index possible
```
`boot_diag_inject_failure` rejected stages only >= BOOT_STAGE_UNKNOWN; `boot_diag_fail`
had no equivalent guard.
`boot_stage_names` only defines 0..10 + index 255.

## Reproduction
Modified `kernel_main` to call `boot_diag_fail(BOOT_STAGE_KERNEL_MAIN + 1, ...)`.
Before the fix, such invalid stages would index outside initialized table entries.
After the fix, QEMU printed a stable `MyOS Boot Diagnostic`, and `  Stage: UNKNOWN`,
with no crash. QEMU remains non-passing by intent because a fail was triggered.
Evidence files: /tmp/myos-autonomous-session/*.log.

## Fix
`boot_diag_fail()` clamps invalid (non-zero-undefined-table) stages to
`BOOT_STAGE_UNKNOWN`, limits reporting loop to 0..10, and only reads defined
`boot_stage_names[]` cells. No normal boot path semantics changed.

## Validation
- `make qemu`: PASS exit=0, `MYOS_KERNEL_PASS` present.
- `make host-test-all`: PASS, 300 tests.
- Eight offline: PASS.
- Integrity 4/4 artifacts; 10/10 pongo build baseline; nested repos unchanged.
- diff check clean.

## Remaining
A8 compatibility still UNKNOWN / UNVERIFIED. Add exception-triggered integration test later if desired.
