# M-P2.46 — Validator Consistency and Parser Contract Debugging

## Summary
Investigated and fixed two confirmed defects in the host-side report validation pipeline: inconsistent `reserved_zero` validation across validators. All intentional design differences were documented and preserved.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains **UNKNOWN/UNVERIFIED**.

---

## Baseline Verification (Phase 1)

### Starting State
- **Local HEAD:** 73e0f58b9e484b14869ee43910a04868b7de0766 (M-P2.45)
- **Branch:** main
- **Remote SHA:** 73e0f58b9e484b14869ee43910a04868b7de0766 (matches local)

### Test Suite Verification
- `make host-test-all`: 14 test files, **283 unittest tests**, all PASS
- 8 offline Makefile targets: all PASS
- Preserved artifacts (4 files): all match manifest.json
- Build baseline (10 files): 10/10 identical to M-P2.29
- Nested repos clean: pongoos-src `cb83dd83`, newlib `feaa8fcf`
- Worktree clean (only M-P2.45 changes)

---

## Pipeline Investigation (Phase 2)

### Complete Host Pipeline
```
Device RAM → `md8` (ep0 OUT) → stdout ring → ep0 IN → parse_hexdump/parse_md8_strict
    → probe_report.parse_report() (magic/version/size)
    → probe_freshness.validate_report() (freshness classification)
    → report_readback.validate_report() (composed validator)
    → pongo_client.read_report_via_md8() (legacy path)
    → probe_selfreport.validate_prehandoff() (pre-handoff validator)
```

### Inconsistencies Found

#### 1. Reserved Zero Validation (BUG-1, BUG-2 - FIXED)

| Validator | v1 reserved=0 | v1 reserved≠0 | v2 (token at 0x58) |
|-----------|---------------|---------------|-------------------|
| `probe_report.parse_report()` | ✅ | **❌ ACCEPTED (BUG)** | ❌ REJECTS (bad magic) |
| `probe_freshness` v1 | ✅ | **❌ ACCEPTED (BUG)** | N/A |
| `report_readback.validate_report()` | ✅ | ❌ REJECTED | ✅ FRESHNESS_ONLY |
| `pongo_client.read_report_via_md8()` | ✅ | ❌ REJECTED | ❌ REJECTS |
| `probe_selfreport.validate_prehandoff()` | ✅ | ❌ REJECTED | ❌ REJECTS |

**Source Contract:** `boot/aarch64/pongo_probe.S` line 100: `+0x58 u64 reserved_zero (always 0)` and `.equ RPT_ZERO_OFF, 0x58`

**Root Cause:**
- `probe_report.parse_report()` validates magic/version/size but skips reserved_zero check
- `probe_freshness.validate_report()` v1 path checks magic/version/size but skips reserved_zero

#### 2. v1/v2 Support (INTENTIONAL)

| Validator | v1 (MP1PRB01) | v2 (MP1PRB02) |
|-----------|---------------|---------------|
| `probe_report.parse_report()` | ✅ | ❌ |
| `probe_freshness` | ✅ | ✅ |
| `report_readback` | ✅ | ✅ (freshness-only) |
| `pongo_client.read_report_via_md8()` | ✅ | ❌ |
| `probe_selfreport` | ✅ | ❌ |

**Intentional Design:** v2 is DESIGN PROPOSAL ONLY (M-P2.42). Legacy path v1-only; new composed validator supports v2 design.

#### 3. Parser Strictness (INTENTIONAL)

| Feature | `parse_hexdump` (legacy) | `parse_md8_strict` (prototype) |
|---------|--------------------------|-------------------------------|
| Invalid hex | Silently skips | Rejects (MalformedTransfer) |
| Missing rows | Accepts partial | Rejects (IncompleteTransfer) |
| Extra rows | Accepts | Rejects |
| Wrong address | Accepts | Rejects |
| Truncated row | Accepts partial | Rejects |
| ANSI escapes | N/A | Strips globally |

**Intentional Design:** Legacy path tolerates device stdout ring behavior; prototype enforces exact integrity.

---

## Fix Loop (Phase 3)

### Fix 1: `probe_report.parse_report()` reserved_zero validation

**File:** `tools/host/probe_report.py` :: `parse_report()`

```python
reserved = report.get("reserved_zero", 0)
if reserved != 0:
    raise ValueError(
        "reserved field at 0x%x must be zero, got 0x%x"
        % (EQU_MIRROR["RPT_ZERO_OFF"], reserved))
```

### Fix 2: `probe_freshness.validate_report()` v1 reserved_zero validation

**File:** `tools/host/probe_freshness.py` :: `validate_report()`

```python
reserved = _unpack_u64(blob, OFF_TOKEN)
if reserved != 0:
    return ("INVALID", {"reason": "v1_bad_reserved", "reserved": reserved})
```

### Test Update

**File:** `tests/test_probe_selfreport.py` :: `test_every_slot_lands_at_the_probe_sources_equ_offset`

Updated to respect reserved_zero=0 contract (skips sentinel for reserved_zero, explicitly verifies it's 0).

---

## Final Regression (Phase 4)

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
Total unittest tests: 283
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

### Integrity Verification
| Check | Result |
|-------|--------|
| Local HEAD == Remote SHA | ✅ `73e0f58b9e484b14869ee43910a04868b7de0766` |
| Preserved artifacts (4) | ✅ All match manifest.json |
| Build baseline (10) | ✅ 10/10 identical to M-P2.29 |
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

- **Local HEAD:** `73e0f58b9e484b14869ee43910a04868b7de0766`
- **Remote SHA:** `73e0f58b9e484b14869ee43910a04868b7de0766` (verified via `git ls-remote origin refs/heads/main`)
- **Match:** ✅

---

## Evidence Locations

- `/tmp/m-p2.46/BASELINE.md`
- `/tmp/m-p2.46/CONTRACT_MATRIX.md`
- `/tmp/m-p2.46/BUGS_FOUND.md`
- `/tmp/m-p2.46/TEST_RESULTS.txt`
- `/tmp/m-p2.46/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.46/SESSION_LOG.md`
- `/tmp/m-p2.46/remote_sha.txt`
- `M_P2_46_VALIDATOR_CONSISTENCY_REPORT.md` (this file)

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

- **Session duration:** ~1 hour wall-clock
- **Actual debugging work:** ~50 minutes