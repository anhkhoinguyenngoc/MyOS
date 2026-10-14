# M-P2.39 — Backward-Compatible Freshness-Aware Readback API

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction; no rebuild/relink; probe firmware unchanged.

## 1) API contract decision
Legacy `read_report_via_md8()` returns `(parsed_report, text)` from `probe_report.parse_report()`; changing return shape breaks existing callers/tests. No legitimate expected-token source exists in production workflow. New opt-in API would require reliable byte stream to canonical validator without breaking contract — not safely supported by current MD8 path. **No new API added.**

Integration classification: **PARTIALLY_INTEGRATED** (validator composed in ReportReadback; token not plumbable to MD8 without breaking contract).

## 2) Changes
None (only documentation/test log updates via evidence).

## 3) Regression
All existing suites pass: regs-output-check, test_pongo_regs_command (23), check_pongo_probe, test_readback_design (20), test_pongo_artifact_select (14), pongo_addr_resolve --verify-source, test_probe_freshness (15), test_probe_freshness_integration (9), test_pongo_client (28).

## 4) Integrity
Preserved artifacts 4/4 match manifest; `pongoos-src/build/` 10/10 identical to M-P2.29 baseline; repos clean.

## 5) Git
Start: `9a8beff48c610efef015a3a362e25fe3902a1fe4`
Commit: `01519d2164d4442e63011aef584263b3a073b22d`
Pushed to origin/main; verified via `git ls-remote`

## 6) Evidence
`/tmp/m-p2.39/REPORT.md`, `preflight.txt`, `api-contract-audit.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. No safe backward-compatible freshness-aware MD8 API feasible without breaking contract; token source missing. v2 remains DESIGN PROPOSAL ONLY; token match ≠ cryptographic auth or hardware proof.
