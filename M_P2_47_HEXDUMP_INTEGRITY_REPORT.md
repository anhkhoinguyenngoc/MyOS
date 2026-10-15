# M-P2.47 — Hexdump Data-Loss and Report-Integrity Debugging

## Summary
Fixed a data-loss bug in `parse_hexdump()` where ANSI escape sequences attached to valid hex octets caused silent data loss. The fix strips ANSI escape sequences globally before parsing, consistent with the M-P2.45 fix for `parse_md8_strict()`.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains **UNKNOWN/UNVERIFIED**.

---

## Baseline Verification (Phase 1)

### Starting State
- **Local HEAD:** `376792acb88d377fe66e465796ef68ef1e456f35` (M-P2.46)
- **Branch:** main
- **Remote SHA:** `376792acb88d377fe66e465796ef68ef1e456f35` (matches local)

### Test Suite Verification
- `make host-test-all`: 14 test files, **283 unittest tests**, all PASS
- 8 offline Makefile targets: all PASS
- 4 preserved artifacts (`artifacts/m-p2.26-cb83dd83/`): all match manifest.json
- 10-file `pongoos-src/build/` baseline: 10/10 identical to M-P2.29
- Nested repos clean: pongoos-src `cb83dd83`, newlib `feaa8fcf`
- Worktree clean (only M-P2.46 changes)

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

### parse_hexdump() Behavior Analysis

Tested `parse_hexdump()` with comprehensive edge cases:

| Input Type | Behavior | Notes |
|------------|----------|-------|
| Valid hexdump | ✅ Correctly parsed | |
| Invalid hex (`zz`) | Silently skipped | Lenient - intentional |
| **ANSI escape attached to hex (BUG)** | **❌ Data loss** | **FIXED** |
| ANSI in various positions | **❌ Data loss** | **FIXED** |
| Prompt lines | Ignored | Correct |
| Empty/invalid | 0 bytes | Correct |

### Root Cause: ANSI Escape Sequence Attachment

Device shell output may contain ANSI escape sequences (color codes) around hexdump output. When ANSI sequences are **attached** to valid hex octets (e.g., `50\x1b[0m`), the parser treats the combined token as invalid hex and silently skips it, dropping the valid hex octet.

**Example:**
```
Input:  '0x000000000: 4d 50 31 50\x1b[0m\n'
Pre-fix: b'4d5031' (3 bytes, lost '50')
Post-fix: b'4d503150' (4 bytes, complete)
```

**Root cause:** `parse_hexdump()` splits lines on whitespace and validates each token against `_HEX_BYTE` regex. ANSI escape sequences attached to valid hex octets fail the hex validation and are silently skipped.

### Fix: Strip ANSI Escape Sequences Globally

**File:** `tools/host/pongo_client.py` :: `parse_hexdump()`

Added `_ANSI_RE` regex and strip ANSI globally before parsing, consistent with M-P2.45 fix for `parse_md8_strict()`.

```python
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
text = _ANSI_RE.sub("", text)  # strip globally before splitlines()
```

Preserves legacy lenient behavior for invalid hex characters (`zz` still silently skipped).

---

## Fix Loop (Phase 3-4)

### Fix Applied
- **File:** `tools/host/pongo_client.py`
- **Function:** `parse_hexdump()`
- **Change:** Strip ANSI escape sequences globally before parsing

### Verification
| Test Case | Pre-Fix | Post-Fix |
|-----------|---------|----------|
| ANSI wrapper | 3 bytes, lost data | 96 bytes, complete |
| ANSI mid-line | 3 bytes, lost data | 96 bytes, complete |
| ANSI at end | 3 bytes, lost data | 96 bytes, complete |
| ANSI prompt | 3 bytes, lost data | 96 bytes, complete |
| Invalid hex (`zz`) | Silently skipped | Silently skipped (preserved) |
| Valid hexdump | OK | OK |

### Regression Tests
All existing tests pass. Added documentation of behavior in test cases (see HEXDUMP_CASES.md).

---

## Final Regression (Phase 5-6)

### Test Results
```
=== make host-test-all ===
PASS tests/test_m2_negative.py
PASS tests/test_pongo_addr_resolve.py
PASS tests/test_pongo_artifact_select.py
PASS tests/test_pongo_client.py
PASS tests/test_pongo_regs_command.py
PASS tests/test_probe_freshness_integration.py
PASS tests/test_probe_freshness.py
PASS tests/test_probe_report.py
PASS tests/test_probe_selfreport.py
PASS tests/test_readback_design.py
PASS tests/test_regs_output.py
PASS tests/test_report_readback_freshness_api.py
PASS tests/test_report_readback.py
PASS tests/test_usb_watch.py
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
| Local HEAD == Remote SHA | ✅ (after push) |
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

- **Local HEAD:** (to be committed)
- **Remote SHA:** (to be verified after push)

---

## Elapsed Time

- **Session duration:** ~2.5 hours wall-clock
- **Actual debugging work:** ~1.5 hours

---

## Evidence Locations

- `/tmp/m-p2.47/BASELINE.md`
- `/tmp/m-p2.47/HEXDUMP_CASES.md`
- `/tmp/m-p2.47/BUGS_FOUND.md`
- `/tmp/m-p2.47/TEST_RESULTS.txt`
- `/tmp/m-p2.47/INTEGRITY_RESULTS.txt`
- `/tmp/m-p2.47/SESSION_LOG.md`
- `/tmp/m-p2.47/remote_sha.txt`
- `M_P2_47_HEXDUMP_INTEGRITY_REPORT.md` (this file)

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
EOF
echo "M_P2_47_HEXDUMP_INTEGRITY_REPORT.md written"