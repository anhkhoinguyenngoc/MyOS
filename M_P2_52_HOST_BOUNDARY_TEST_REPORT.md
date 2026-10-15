# M-P2.52 Final Report

## Summary
Completed property-based boundary testing of all host-side report parsers, validators, address resolution, and CLI interfaces. No new defects found. All previously fixed bugs remain fixed. All invariants hold.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains **UNKNOWN/UNVERIFIED**.

---

## Verified Invariants

### 1. Report Parser Invariants
| Function | Size Check | Magic | Version | Size Field | Reserved=0 | Freshness | v2 Support |
|----------|------------|-------|---------|------------|------------|-----------|------------|
| probe_report.parse_report() | ✅ Exact | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| probe_freshness.validate_report() | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| report_readback.validate_report() | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pongo_client.read_report_via_md8() | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| ReportReadback.read_report() | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| probe_selfreport.validate_prehandoff() | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

### 2. Address Resolution Invariants
All address arithmetic functions correct:
- `phystokv` / `vatophys_static` roundtrip: ✅
- `handoff_alias` equals buffer PA: ✅
- `runtime_va` / `runtime_va_from_base` equivalence: ✅
- `kcacheable` override: ✅
- Buffer resolution: all 5 states correct
- Symbol resolution with synthetic Mach-O: ✅

### 3. Parser Strictness
| Feature | parse_hexdump (lenient) | parse_md8_strict (strict) |
|---------|------------------------|---------------------------|
| Invalid hex | Silently skips | Rejects |
| Missing rows | Accepts partial | Rejects (IncompleteTransfer) |
| Extra rows | Accepts | Rejects |
| Wrong address | Accepts | Rejects |
| Truncated row | Accepts partial | Rejects |
| ANSI escapes | Strips globally | Strips globally (M-P2.45) |

---

## Previously Fixed Bugs (Verified Still Fixed)

| Bug | Milestone | Status |
|-----|-----------|--------|
| parse_report() oversized blob | M-P2.48 | ✅ FIXED |
| probe_freshness v1 reserved_zero | M-P2.46 | ✅ FIXED |
| parse_report reserved_zero | M-P2.46 | ✅ FIXED |
| parse_hexdump ANSI data loss | M-P2.47 | ✅ FIXED |
| pongo_addr_resolve malformed hex | M-P2.44/M-P2.45 | ✅ FIXED |

### Prefix Injection Attack (MITIGATED)
**Threat:** 16-byte valid hexdump prefix in stdout ring prepends to report.
- **Legacy path:** Fixed by M-P2.48 exact-size check
- **Strict path:** Rejects via row count/address validation
- **Binary prefix:** Safely ignored (not valid hexdump text)

---

## Test Results

### Regression Suite
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
| Local HEAD == Remote SHA | ✅ `3c70c7cd3400359c21004cbcd7cea8b581aba82b` |
| Preserved artifacts (4) | ✅ All match manifest.json |
| Build baseline (10 files) | ✅ 10/10 identical to M-P2.29 |
| pongoos-src repo | ✅ Clean at `cb83dd83` |
| newlib repo | ✅ Clean at `feaa8fcf` |
| Firmware files | ✅ Untouched |

---

## Unresolved Design Blockers (Preserved)

1. **Token transport / v2 protocol:** No production CLI supplies expected token; `read_report_via_md8()` cannot be safely extended; no legitimate token source in repo. v2 remains **DESIGN PROPOSAL ONLY**.

2. **End-to-end freshness verification:** Blocked until probe-side token conveyance exists (requires probe firmware change).

3. **parse_hexdump leniency:** Silently ignores invalid hex - left as-is (intentional for legacy path compatibility with device stdout ring behavior).

---

## Git & Remote Verification

- **Local HEAD:** `3c70c7cd3400359c21004cbcd7cea8b581aba82b`
- **Remote SHA:** `3c70c7cd3400359c21004cbcd7cea8b581aba82b` (verified via `git ls-remote origin refs/heads/main`)
- **Match:** ✅ YES

---

## Evidence Locations

- `/tmp/m-p2.52/BASELINE.md`
- `/tmp/m-p2.52/INVARIANTS.md`
- `/tmp/m-p2.52/GENERATED_CASES.md`
- `/tmp/m-p2.52/BUGS_FOUND.md`
- `/tmp/m-p2.52/TEST_RESULTS.txt`
- `/tmp/m-p2.52/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.52/SESSION_LOG.md`
- `/tmp/m-p2.52/remote_sha.txt`
- `M_P2_52_HOST_BOUNDARY_TEST_REPORT.md` (this file)

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

- **Session duration:** ~2.5 hours wall-clock
- **Actual debugging work:** ~1.5 hours

---

## Evidence Locations

- `/tmp/m-p2.52/BASELINE.md`
- `/tmp/m-p2.52/INVARIANTS.md`
- `/tmp/m-p2.52/GENERATED_CASES.md`
- `/tmp/m-p2.52/BUGS_FOUND.md`
- `/tmp/m-p2.52/TEST_RESULTS.txt`
- `/tmp/m-p2.52/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.52/SESSION_LOG.md`
- `/tmp/m-p2.52/remote_sha.txt`
- `M_P2_52_HOST_BOUNDARY_TEST_REPORT.md` (this file)

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

- **Session duration:** ~2.5 hours wall-clock
- **Actual debugging work:** ~1.5 hours
