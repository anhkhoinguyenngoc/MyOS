# M-P2.41 — Probe Protocol Extension Feasibility and ABI Audit

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No hardware interaction; no rebuild/relink; no firmware changes.

## 1) Probe architecture (source-evidence)
- Command registration: command_register(name,desc,cb) in pongoos-src/src/shell/command.c; commands stored in array; help registered. Shell reads via fgets, executes, prints prompt via iprintf.
- regs: regs_cmd(cmd,args) in regs.c, registered via regs_commands_register() -> command_register("regs", ...). args passed as string; no token parsing implemented.
- Report: MP1PRB01 in pongo_probe.S, size 0x60, offset 0x200; written post-handoff; reserved_zero at +0x58.

## 2) ABI/compatibility
- v1 implemented. v2 proposed only (no probe-side token echo). 
- read_report_via_md8() bypasses freshness; ReportReadback.read_report() accepts expected_token.
- No implemented host->probe token transport; command architecture exists but no token mechanism implemented.

## 3) Feasibility
**No implemented extension point for token transport**. Command callback signature (cmd,args) exists but no token fields/variant implemented. Repurposing reserved_zero requires probe changes (not authorized). read_report_via_md8() cannot safely propagate freshness without breaking API contract.

**Smallest justified future step:** implement token handling in probe (if authorized) plus host transport; otherwise keep design-only.

## 4) Tests
All existing suites pass: regs-output-check, test_pongo_regs_command (23), check_pongo_probe, test_readback_design (20), test_pongo_artifact_select (14), pongo_addr_resolve --verify-source, test_probe_freshness (15), test_probe_freshness_integration (9), test_pongo_client (28).

## 5) Integrity
- Preserved artifacts 4/4 match manifest
- pongoos-src/build/ 10/10 identical to M-P2.29 baseline
- Repos clean

## 6) Git
Start: d4c19c205d1e64eaf919bdbf921a03516eb2ef92
Commit: a27cc947d2a314e373381e1fa3806e4b2addb7d5
Pushed to origin/main; verified via git ls-remote

## 7) Evidence
/tmp/m-p2.41/REPORT.md, preflight.txt, probe-architecture-audit.txt, abi-compatibility-audit.txt, tests.log, integrity-check.txt, ending-state.txt

## 8) Statement
No iPhone interaction; no hardware test/rebuild/relink; no firmware changes. No implemented probe protocol extension for token transport; v2 remains DESIGN PROPOSAL ONLY. Token match ≠ cryptographic auth or hardware proof.
