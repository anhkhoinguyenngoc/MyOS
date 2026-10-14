# M-P2.35 — Host Freshness Integration Audit & End-to-End Offline Simulation

**Verdict: PASS (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction, rebuild, or relink. Probe firmware unchanged.

## 1) Integration audit
- `tools/host/probe_freshness.py` exists (validator). Only caller site is `tools/host/report_readback.py` (own `validate_report` function) — not wired into `probe_freshness.validate_report` in production path. Integration classification: **NOT_INTEGRATED** (validator exists; no production workflow invokes it). No other call sites found.

Call graph evidence:
- `tools/host/probe_freshness.py::validate_report(blob, expected_token=None)` — canonical validator (statuses: INVALID, LEGACY_VALID_FRESHNESS_UNVERIFIED, V2_VALID_FRESH, V2_VALID_STALE, V2_VALID_FRESHNESS_UNVERIFIABLE)
- `tools/host/report_readback.py::validate_report(blob)` — legacy structural validator (checks length, calls `probe_report.parse_report`, enforces `reserved_zero==0`). Does **not** call `probe_freshness.validate_report` and has no token parameter.
- No CLI/workflow invokes `probe_freshness.validate_report`.

Safety: legacy path preserves structural checks; freshness states not propagated in existing production code.

## 2) Orchestration (end-to-end offline)
Created `tests/test_probe_freshness_integration.py` demonstrating propagation of freshness states using the validator API (no hardware). Cases cover v1 unverified, v2 fresh/stale/unverifiable, truncated, unknown version, and invariant (v1 never fresh). All 9 tests pass.

## 3) Regression
All existing offline suites pass:
- `make regs-output-check` OK
- `tests/test_pongo_regs_command.py` (23/23) OK
- `tests/check_pongo_probe.py` OK
- `tests/test_readback_design.py` (20/20) OK
- `tests/test_pongo_artifact_select.py` (14/14) OK
- `tools/host/pongo_addr_resolve.py --verify-source` OK
- `tests/test_probe_freshness.py` (15/15) OK
- `tests/test_probe_freshness_integration.py` (9/9) OK

## 4) Integrity
- Preserved artifacts 4/4 match manifest.
- `pongoos-src/build/`: 10/10 identical to M-P2.29 baseline (sha/size/mtime).
- Repos clean (MyOS shows only intended test; PongoOS/newlib clean). No firmware/probe changes.

## 5) Git
- Start: `0cea3ae7b6d3f82131572b067a5e32065cffd37b`
- Commit: `d0d89005abf558e2a48be93e4d7470d0ea91f1d9`
- Push: to `origin/main`, verified via `git ls-remote`. Worktree clean.

## 6) Paths
`/tmp/m-p2.35/REPORT.md`, `preflight.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. Validator not integrated into production workflow (documented). Integration remains pending if needed; v2 is DESIGN PROPOSAL ONLY. Freshness match ≠ cryptographic auth or hardware proof.
