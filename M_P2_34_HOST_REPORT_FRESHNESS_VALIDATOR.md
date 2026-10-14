# M-P2.34 — Host-Side Report Freshness Validator & Offline Regression

**Verdict: PASS (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**
No iPhone connected/contacted. No rebuild/relink. Probe firmware unchanged.

## 1) Scope & behavior
- Host-side validator only (`tools/host/probe_freshness.py`). No firmware changes.
- v1 (MP1PRB01): structurally valid → `LEGACY_VALID_FRESHNESS_UNVERIFIED`. Supplying expected token does **not** make it fresh (invariant enforced).
- v2 (MP1PRB02, DESIGN PROPOSAL ONLY): `V2_VALID_FRESH` only if full token matches; `V2_VALID_STALE` if mismatch; `V2_VALID_FRESHNESS_UNVERIFIABLE` if no expected token; `INVALID` on bad magic/version/size/truncation/unknown.
- Exact full-token comparison (LE u64). Unknown versions rejected. Missing expected token cannot produce fresh result.

## 2) Files
- `tools/host/probe_freshness.py` (new)
- `tests/test_probe_freshness.py` (new)
- `M_P2_34_HOST_REPORT_FRESHNESS_VALIDATOR.md` (new, doc)

## 3) Tests (all exit 0)
- `make regs-output-check`: 0
- `tests/test_pongo_regs_command.py`: 0 (23)
- `tests/check_pongo_probe.py`: 0
- `tests/test_readback_design.py`: 0 (20)
- `tests/test_pongo_artifact_select.py`: 0 (14)
- `tools/host/pongo_addr_resolve.py --verify-source`: 0
- `tests/test_probe_freshness.py`: 0 (15)

## 4) Integrity
- Preserved artifacts 4/4 match manifest.
- `pongoos-src/build/`: 10/10 identical to M-P2.29 baseline (sha/size/mtime).
- Repos clean (MyOS shows only intended new files; PongoOS/newlib clean). No firmware/probe changes.

## 5) Git
- Start: `3fbfba7436c1b617631fe80a3c50dd00703703fd`
- Commit: `0bcdd82a3ae5f23464809bf8e386d2a44a1d7c69`
- Push: to `origin/main`; `git ls-remote origin main` matches commit. Worktree clean.

## 6) Paths
`/tmp/m-p2.34/REPORT.md`, `preflight.txt`, `tests.log`, `integrity-check.txt`, `ending-state.txt`

## 7) Statement
No iPhone interaction; no hardware test/rebuild/relink. Offline correctness only. v2 is DESIGN PROPOSAL ONLY; matching token ≠ crypto auth or hardware proof.
