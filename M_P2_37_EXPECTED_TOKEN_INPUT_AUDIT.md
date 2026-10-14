# M-P2.37 — Expected-Token Input Contract Audit & Offline Integration

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction; no rebuild/relink; probe firmware unchanged.

## 1) Dataflow audit (evidence-based)
- Canonical: `tools/host/probe_freshness.py::validate_report(blob, expected_token=None)`; CLI `--expected-token` exists.
- Readback API: `tools/host/report_readback.py::validate_report(blob, expected_token=None)` tries canonical; on INVALID/V2_VALID_STALE raises MalformedReport; on success returns dict with freshness_status/detail.
- ReportReadback.read_report() now accepts `expected_token=None` and passes through.
- PongoClient.read_report_via_md8() returns (parsed_report, text) via probe_report.parse_report only — does not call freshness/expected token.
- No production CLI/workflow supplies expected_token to readback.

Integration classification: **PARTIALLY_INTEGRATED**. API accepts expected_token, but no real production caller/CLI path supplies it.

## 2) Implementation
- `tools/host/report_readback.py`: added `expected_token` parameter to `ReportReadback.read_report()` and pass-through to `validate_report()` (safe, backward compatible).

## 3) Tests
All existing+freshness suites pass: regs-output-check, test_pongo_regs_command (23), check_pongo_probe, test_readback_design (20), test_pongo_artifact_select (14), pongo_addr_resolve --verify-source, test_probe_freshness (15), test_probe_freshness_integration (9).

## 4) Integrity
- Preserved artifacts 4/4 match manifest
- `pongoos-src/build/`: 10/10 identical to M-P2.29 baseline
- Repos clean except intended change to report_readback.py

## 5) Git
- Start: `04f475d7a41154bb3a620fd0e5216136eabca5f8`
- Commit: `09b82ef5af25c2d6c1d7084daae75cf83c15b479`
- Pushed to origin/main, verified via `git ls-remote`

## 6) Paths
`/tmp/m-p2.37/REPORT.md`, `preflight.txt`, `dataflow-audit.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`, `M_P2_37_EXPECTED_TOKEN_INPUT_AUDIT.md`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. Production path lacks expected-token input (documented). v2 remains DESIGN PROPOSAL ONLY; token match ≠ cryptographic auth or hardware proof.
