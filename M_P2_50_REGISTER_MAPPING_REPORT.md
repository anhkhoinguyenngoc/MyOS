# M-P2.50 Final Report

## Summary
Audited all host-side register mapping and address resolution paths in the MyOS offline tooling. Verified that M-P2.48's fix for the oversized-blob vulnerability in `probe_report.parse_report()` effectively closes the prefix injection attack vector across all report ingress paths. No new defects found; all validators enforce their documented contracts consistently.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains **UNKNOWN/UNVERIFIED**.

---

## Key Findings

### 1. Register Layout Consistency (VERIFIED)
All report parsers use a single source of truth for field offsets: the `.equ` block in `boot/aarch64/pongo_probe.S` (mirrored in `probe_report.EQU_MIRROR`). Test `test_equ_mirror_matches_source` ensures 1:1 correspondence.

| Field | Offset | Size | Byte Order | Type |
|-------|--------|------|------------|------|
| magic | 0x00 | 8 | LE | u64 |
| version | 0x08 | 4 | LE | u32 |
| report_size | 0x0C | 4 | LE | u32 |
| x30_entry | 0x10 | 8 | LE | u64 |
| x0_entry | 0x18 | 8 | LE | u64 |
| x8_entry | 0x20 | 8 | LE | u64 |
| current_el | 0x28 | 8 | LE | u64 |
| sctlr_el1 | 0x30 | 8 | LE | u64 |
| midr_el1 | 0x38 | 8 | LE | u64 |
| cntpct_el0_1 | 0x40 | 8 | LE | u64 |
| cntpct_el0_2 | 0x48 | 8 | LE | u64 |
| cntfrq_el0 | 0x50 | 8 | LE | u64 |
| reserved_zero | 0x58 | 8 | LE | u64 |

### 2. Address Resolution Model (VERIFIED)
All address arithmetic functions correct:
- `phystokv` / `vatophys_static` roundtrip: ✅
- `handoff_alias` equals buffer PA: ✅
- `runtime_va` / `runtime_va_from_base` equivalence: ✅
- `kcacheable` override respected: ✅
- Buffer resolution states correct: ✅
- Symbol resolution with synthetic Mach-O: ✅

### 3. Prefix Injection Attack (MITIGATED)
**Threat:** 16-byte valid hexdump prefix in stdout ring prepends to report.
- **Legacy path:** Fixed by M-P2.48 exact-size check in `parse_report()`
- **Strict path:** Rejects via row count/address validation in `parse_md8_strict()`
- **Binary prefix:** Safely ignored (not valid hexdump text)

### 4. Previously Fixed Bugs (Verified Still Fixed)
| Bug | Milestone | Status |
|-----|-----------|--------|
| `parse_report()` oversized blob | M-P2.48 | ✅ FIXED |
| `probe_freshness` v1 reserved_zero | M-P2.46 | ✅ FIXED |
| `parse_report()` reserved_zero | M-P2.46 | ✅ FIXED |
| `parse_hexdump` ANSI data loss | M-P2.47 | ✅ FIXED |
| `pongo_addr_resolve` malformed hex | M-P2.44/M-P2.45 | ✅ FIXED |

## Intentional Design Differences (Not Bugs)

| Difference | Reason |
|------------|--------|
| v2 (MP1PRB02) only in freshness validator | v2 is DESIGN PROPOSAL ONLY (M-P2.42) |
| `parse_hexdump` leniency | Legacy path tolerates device stdout noise |
| `parse_md8_strict` strictness | Prototype path enforces exact integrity |
| `parse_hexdump` skips invalid hex | Legacy path tolerates device stdout noise |
| v2 token at offset 0x58 (v1 reserved) | Design proposal reuses reserved offset |

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
| Local HEAD == Remote SHA | ✅ `6a8037bd14e6f8fa9f630be50fe3ff995daa987b` |
| Preserved artifacts (4) | ✅ All match manifest.json |
| Build baseline (10 files) | ✅ 10/10 identical to M-P2.29 |
| pongoos-src repo | ✅ Clean at `cb83dd83` |
| newlib repo | ✅ Clean at `feaa8fcf` |
| Firmware files | ✅ Untouched |
| Worktree changes | ✅ Only `M_P2_50_REGISTER_MAPPING_REPORT.md` added |

---

## Unresolved Design Blockers (Preserved)

1. **Token transport / v2 protocol:** No production CLI supplies expected token; `read_report_via_md8()` cannot be safely extended; no legitimate token source in repo. v2 remains **DESIGN PROPOSAL ONLY**.

2. **End-to-end freshness verification:** Blocked until probe-side token conveyance exists (requires probe firmware change).

3. **parse_hexdump leniency:** Silently ignores invalid hex - left as-is (intentional for legacy path compatibility).

---

## Git & Remote Verification

- **Local HEAD:** `6a8037bd14e6f8fa9f630be50fe3ff995daa987b`
- **Remote SHA:** `6a8037bd14e6f8fa9f630be50fe3ff995daa987b` (verified via `git ls-remote`)
- **Match:** ✅ YES

---

## Elapsed Time

- **Session duration:** ~1.5 hours wall-clock
- **Actual debugging work:** ~1 hour

---

## Evidence Locations

- `/tmp/m-p2.50/BASELINE.md`
- `/tmp/m-p2.50/REGISTER_LAYOUT.md`
- `/tmp/m-p2.50/SYNTHETIC_FIXTURES.md`
- `/tmp/m-p2.50/ADDRESS_ARITHMETIC.md`
- `/tmp/m-p2.50/BUGS_FOUND.md`
- `/tmp/m-p2.50/TEST_RESULTS.txt`
- `/tmp/m-p2.50/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.50/SESSION_LOG.md`
- `/tmp/m-p2.50/remote_sha.txt`
- `M_P2_50_REGISTER_MAPPING_REPORT.md` (this file)

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
