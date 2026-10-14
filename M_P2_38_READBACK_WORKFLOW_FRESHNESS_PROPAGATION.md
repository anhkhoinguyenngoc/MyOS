# M-P2.38 — Readback Workflow Freshness Propagation Audit

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction; no rebuild/relink; probe firmware unchanged.

## 1) Call graph
- `PongoClient.read_report_via_md8()` returns (parsed_report, text) using `probe_report.parse_report()` directly — **bypasses freshness validator**.
- `ReportReadback.read_report()` accepts `expected_token` and passes to canonical freshness validator (via `report_readback.validate_report()`).
- No production CLI/workflow supplies expected_token. No legitimate expected-token source found.
- Tests are the main consumers of `read_report_via_md8()`; changing its return shape breaks existing callers.

## 2) Freshness propagation
Cannot safely propagate expected-token freshness through `read_report_via_md8()` without breaking API contract. ReportReadback API is the appropriate place (already token-aware). Propagation to read_report_via_md8() deferred.

## 3) Tests
All existing suites pass: regs-output-check, test_pongo_regs_command (23), check_pongo_probe, test_readback_design (20), test_pongo_artifact_select (14), pongo_addr_resolve --verify-source, test_probe_freshness (15), test_probe_freshness_integration (9).

## 4) Integrity
Preserved artifacts 4/4 match manifest; `pongoos-src/build/` 10/10 identical to M-P2.29 baseline; repos clean.

## 5) Git
Start: `68ad08aebd812c76ffccade412d2bddbd3c44e94`
Commit: `7c12e28cf8b4a0198043a2a99df6562c99d09c67`
Pushed to origin/main; verified via `git ls-remote`

## 6) Evidence
`/tmp/m-p2.38/REPORT.md`, `preflight.txt`, `dataflow-audit.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. Production workflow lacks expected-token input; read_report_via_md8() bypasses freshness validator and cannot be safely extended without breaking API. v2 remains DESIGN PROPOSAL ONLY; token match ≠ cryptographic auth or hardware proof.
