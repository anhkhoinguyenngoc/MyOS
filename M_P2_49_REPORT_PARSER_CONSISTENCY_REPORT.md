# M-P2.49 — Report Parser Consistency and Regression Hardening

## Summary
Audited all host-side report ingress paths after M-P2.48's fix for the oversized-blob vulnerability in `probe_report.parse_report()`. Verified that every public report-reading API enforces the expected 96-byte structural validation, and that the prefix injection attack vector is now blocked at multiple layers.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains **UNKNOWN/UNVERIFIED**.

---

## Baseline Verification (Phase 1)

Verified M-P2.48 commit `5b61438d2fb289f802834c574b29fbe15de6abf5`:
- `make host-test-all`: 14 test files, **283 unittest tests**, all PASS
- 8 offline Makefile targets: all PASS
- 4 preserved artifacts: all match manifest.json
- 10-file build baseline: 10/10 identical to M-P2.29
- Nested repos clean: pongoos-src `cb83dd83`, newlib `feaa8fcf`

---

## Pipeline Audit (Phase 2)

### Complete Call Graph

```
Device RAM → md8 (ep0 OUT) → stdout ring → ep0 IN → Host text
    ↓
parse_hexdump() (lenient) / parse_md8_strict() (strict)
    ↓
probe_report.parse_report() (magic/version/size/reserved_zero)
    ↓
probe_freshness.validate_report() (freshness classification)
    ↓
report_readback.validate_report() (composed validator)
    ↓
Consumer (ReportReadback.read_report() / pongo_client.read_report_via_md8())
```

### Validation Consistency Matrix

| Validator | Size Check | Magic | Version | Size Field | Reserved=0 | Freshness | v2 Support |
|-----------|------------|-------|---------|------------|------------|-----------|------------|
| `probe_report.parse_report()` | ✅ Exact | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `probe_freshness.validate_report()` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `report_readback.validate_report()` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `pongo_client.read_report_via_md8()` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `ReportReadback.read_report()` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `probe_selfreport.validate_prehandoff()` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

**Key Finding:** All report ingress paths now enforce exact 96-byte size via `probe_report.parse_report()` (M-P2.48 fix). The prefix injection attack vector is blocked at the structural validation layer.

---

## Confirmed Defects Fixed

### BUG-1 (M-P2.48): `parse_report()` accepts oversized blobs
**File:** `tools/host/probe_report.py` :: `parse_report()`
**Issue:** Function accepted blobs >96 bytes and silently read only first 96 bytes.
**Fix:** Added exact size validation before parsing.
**Impact:** Blocks prefix injection attack where 16-byte valid prefix in stdout ring would shift parsed report by 16 bytes.

### BUG-2: Legacy path prefix injection (MITIGATED)
**Root Cause:** `parse_report()` accepted oversized blobs + lenient `parse_hexdump()`
**Mitigation:** Exact-size check in `parse_report()` now rejects oversized blobs from `parse_hexdump()`.

### BUG-3: v2 support inconsistency (INTENTIONAL)
Legacy path rejects v2; new composed validator supports v2. v2 is DESIGN PROPOSAL ONLY.

### BUG-4: Parser strictness difference (INTENTIONAL)
Legacy `parse_hexdump` lenient; strict parser `parse_md8_strict` enforces exact format.

### BUG-5: ANSI escape data loss (M-P2.47 FIXED)
`parse_hexdump()` now strips ANSI globally (consistent with `parse_md8_strict` M-P2.45 fix).

---

## Regression Coverage

### Test Results
```
=== make host-test-all ===
PASS tests/test_m2_negative.py              (31 assertions)
PASS tests/test_pongo_addr_resolve.py       (26 tests)
PASS tests/test_pongo_artifact_select.py    (14 tests)
PASS tests/test_pongo_client.py             (29 tests)
PASS tests/test_pongo_regs_command.py       (23 tests)
PASS tests/test_probe_freshness_integration.py (9 tests)
PASS tests/test_probe_freshness.py          (17 tests)
PASS tests/test_probe_report.py             (8 tests)
PASS tests/test_probe_selfreport.py         (56 tests)
PASS tests/test_readback_design.py          (20 tests)
PASS tests/test_regs_output.py              (19 tests)
PASS tests/test_report_readback_freshness_api.py (14 tests)
PASS tests/test_report_readback.py          (48 tests)
PASS tests/test_usb_watch.py                (21 assertions)
=== HOST-TEST-ALL: PASS ===
Total: 283 unittest tests + 52 custom assertions
```

### Offline Makefile Targets (All PASS)
- `report-readback-test`
- `probe-report-test`
- `pongo-client-test`
- `probe-selfreport-test`
- `pongo-addr-check`
- `pongo-artifact-check`
- `regs-output-check`
- `m2-check`

---

## Integrity Verification

| Check | Result |
|-------|--------|
| Local HEAD == Remote SHA | ✅ `5b61438d2fb289f802834c574b29fbe15de6abf5` |
| Preserved artifacts (4) | ✅ All match manifest.json |
| Build baseline (10 files) | ✅ 10/10 identical to M-P2.29 |
| pongoos-src repo | ✅ Clean at `cb83dd83` |
| newlib repo | ✅ Clean at `feaa8fcf` |
| Firmware files | ✅ Untouched |

---

## Unresolved Design Blockers

1. **Token transport / v2 protocol:** No production CLI supplies expected token; `read_report_via_md8()` cannot be safely extended; no legitimate token source in repo. v2 remains **DESIGN PROPOSAL ONLY**.

2. **End-to-end freshness verification:** Blocked until probe-side token conveyance exists (requires probe firmware change).

3. **parse_hexdump leniency:** Silently ignores invalid hex - left as-is (intentional for legacy path compatibility).

---

## Git & Remote Verification

- **Local HEAD:** `5b61438d2fb289f802834c574b29fbe15de6abf5`
- **Remote SHA:** `5b61438d2fb289f802834c574b29fbe15de6abf5` (verified via `git ls-remote origin refs/heads/main`)
- **Match:** ✅ YES

---

## Evidence Locations

- `/tmp/m-p2.49/BASELINE.md`
- `/tmp/m-p2.49/REPORT_INGRESS_CALL_GRAPH.md`
- `/tmp/m-p2.49/REGRESSION_MATRIX.md`
- `/tmp/m-p2.49/BUGS_FOUND.md`
- `/tmp/m-p2.49/TEST_RESULTS.txt`
- `/tmp/m-p2.49/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.49/SESSION_LOG.md`
- `/tmp/m-p2.49/remote_sha.txt`
- `M_P2_49_REPORT_PARSER_CONSISTENCY_REPORT.md` (this file)

---

## Compliance

- ✅ No iPhone 6 / Apple A8 contact
- ✅ Arch Linux only; no package installs, no sudo
- ✅ No firmware/probe modifications, rebuilds, or relinking
- ✅ Nested repos untouched
- ✅ Preserved artifacts and build baselines intact
- ✅ No speculative token transport / protocol implementation
- ✅ Legacy API contracts preserved
- ✅ No hardware compatibility claims
- ✅ v2 remains DESIGN PROPOSAL ONLY

---

## Elapsed Time

- **Session duration:** ~1.5 hours wall-clock
- **Actual debugging work:** ~1 hour