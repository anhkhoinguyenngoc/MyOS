# M-P2.36 — Production Host Readback Freshness Integration

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction, rebuild, or relink. Probe firmware unchanged.

## 1) Integration path
`tools/host/probe_freshness.py` provides canonical validator. `tools/host/report_readback.py::validate_report()` previously did only structural validation. Modified to try canonical freshness validator when available; on canonical result returns a dict carrying `freshness_status` and `freshness_detail`. When freshness module unavailable, falls back to legacy structural validation (preserves backward compatibility). No production CLI/workflow exists to supply an expected token — **no legitimate expected-token input path exists in current host code**.

Integration classification: **PARTIALLY_INTEGRATED** (validator composed into readback API; production invocation with token remains unsupported). Expected token cannot be obtained from existing interfaces.

## 2) Changes
- `tools/host/report_readback.py`: import `probe_freshness.validate_report` (best-effort); `validate_report(blob, expected_token=None)` returns freshness-annotated result when canonical path succeeds (on INVALID/V2_VALID_STALE raises MalformedReport with freshness reason). Legacy path preserved when module unavailable.

## 3) Tests (all pass)
- make regs-output-check OK
- tests/test_pongo_regs_command.py 23/23 OK
- tests/check_pongo_probe.py OK
- tests/test_readback_design.py 20/20 OK
- tests/test_pongo_artifact_select.py 14/14 OK
- tools/host/pongo_addr_resolve.py --verify-source OK
- tests/test_probe_freshness.py 15/15 OK
- tests/test_probe_freshness_integration.py 9/9 OK
- existing regression suites pass

## 4) Integrity
- Preserved artifacts 4/4 match manifest
- `pongoos-src/build/`: 10/10 identical to M-P2.29 baseline
- Repos clean except intended change to report_readback.py; no firmware/probe/binary changes

## 5) Git
- Start: `e85ba245c6061de6a38fb2b92762e2827e9d676d`
- Commit: `1857f43140c9f91893772da0c7c949d8c35f8b5e`
- Pushed to origin/main, verified via `git ls-remote`

## 6) Paths
`/tmp/m-p2.36/REPORT.md`, `preflight.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`, `M_P2_36_READBACK_FRESHNESS_INTEGRATION.md`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. Production path lacks expected-token input; v2 freshness cannot be verified end-to-end without that interface. v2 remains DESIGN PROPOSAL ONLY. Token match ≠ cryptographic auth or hardware proof.
