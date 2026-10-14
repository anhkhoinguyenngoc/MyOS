# M-P2.44 — Continuous Debug Session (Host-Side, Offline)

## Summary
Fixed **4 reproducible host-side defects** discovered during sustained
offline debugging. Added regression tests for each, ran the full offline
test suite (14 test files, 134 tests), and introduced an aggregate
`host-test-all` Makefile target to prevent regression omission.

**No hardware was touched.** iPhone 6 / Apple A8 compatibility remains
**UNKNOWN/UNVERIFIED**. No firmware/probe modifications, rebuilds, or
relinking occurred. All artifacts and build baselines preserved.

---

## Bugs Fixed

### BUG-1 — REGRESSION (M-P2.36): `report_readback.validate_report()`
**File:** `tools/host/report_readback.py`

The M-P2.36 composition of the canonical freshness validator (`probe_freshness`)
into `validate_report()` accidentally replaced the legacy return value (the
parsed report dict with `magic`/`version`/`report_size`/`reserved_zero`/dynamic
slots) with a freshness-only dict, and **dropped the v1 reserved-field-must-be-zero
check**.

**Reproduction:**
```bash
python3 tools/host/report_readback.py --selfcheck  # KeyError: 'magic'
python3 tests/test_report_readback.py              # 1 failure + 5 errors
make report-readback-test                          # FAIL (broken since M-P2.36)
```

**Root cause:** The composed path returned `{"freshness_status","freshness_detail"}`
instead of the parsed report, and skipped `reserved_zero == 0` validation
(`probe_freshness` does not validate the v1 reserved field).

**Fix:** `validate_report()` now **always** runs the structural parser + reserved
check, then augments the parsed dict with `freshness_status`/`freshness_detail`.
A genuine MP1PRB02 (v2) report is structurally vetted by the canonical validator
but cannot be parsed by the v1 parser, so it returns a freshness-only dict.
A stale v2 report raises `MalformedReport`.

**Regression coverage:** `tests/test_report_readback_freshness_api.py` (14 tests)
+ the previously-omitted `tests/test_report_readback.py` (48 tests) now passes.

---

### BUG-2 — BUG: Freshness tests compute wrong `sys.path` (3× `dirname`)
**Files:** `tests/test_probe_freshness.py`, `tests/test_probe_freshness_integration.py`

Both test files used three `os.path.dirname()` calls instead of two,
inserting the nonexistent `/home/khoi/Projects/tools/host` into `sys.path`.
They only passed when an external `PYTHONPATH` was set.

**Reproduction:**
```bash
python3 tests/test_probe_freshness.py  # ModuleNotFoundError: probe_freshness
```

**Fix:** Changed both to two `dirname()` calls (`.../myos/tools/host`).

---

### BUG-3 — BUG: `pongo_addr_resolve` exit code masks `--verify-source` failure
**File:** `tools/host/pongo_addr_resolve.py :: main()`

When `--verify-source` failed but `--symbol` resolved successfully,
the `--symbol` branch contained `rc = 0`, clobbering the `rc = 1`
set by the failed source verification.

**Reproduction:**
```bash
python3 tools/host/pongo_addr_resolve.py \
  --repo /tmp/empty --macho pongoos-src/build/Pongo \
  --verify-source --symbol _pongo_entry
# printed "RESULT: FAIL" but exited 0
```

**Fix:** Removed the clobbering assignment; a successful symbol resolution
no longer clears a prior non-zero status.

**Regression coverage:** `tests/test_pongo_addr_resolve.py::ExitCodeTests` (3 tests).

---

### BUG-4 — BUG: Malformed CLI hex/token input crashes with a traceback
**Files:** `tools/host/pongo_addr_resolve.py` (`--slide/--pongo-base/--buffer-pa/
--buffer-va/--k-cacheable-view`), `tools/host/probe_freshness.py`
(`--expected-token`)

Numeric CLI options were parsed with a bare `int(value, 16|0)` and raised
uncaught `ValueError` on malformed input (traceback + exit 1), instead of the
documented usage error (exit 2).

**Reproduction:**
```bash
python3 tools/host/pongo_addr_resolve.py --buffer-pa zzz
# ValueError traceback
python3 tools/host/probe_freshness.py --blob B --expected-token notanumber
# ValueError traceback
```

**Fix:** Parse hex/token options in `try/except`, return 2 with a clear stderr
message. Normal numeric paths unchanged.

**Regression coverage:**
- `tests/test_pongo_addr_resolve.py::ExitCodeTests` (3 malformed-input tests)
- `tests/test_probe_freshness.py::TestCli` (2 tests)

---

## New Test Infrastructure

### `tests/test_report_readback_freshness_api.py` (14 tests)
Pins the combined contract of `validate_report()` + `ReportReadback.read_report()`
when the canonical freshness validator is composed in:
- v1 reports return parsed fields + `freshness_status = LEGACY_VALID_FRESHNESS_UNVERIFIED`
- Structural rules (exact size, magic/version/size, `reserved_zero == 0`) enforced
- v2 reports classified by canonical validator: fresh only on exact token match,
  unverifiable without expected token, mismatch raises `MalformedReport`

### `tests/test_pongo_addr_resolve.py::ExitCodeTests` (3 new tests)
Exit-code propagation under combined `--verify-source` + `--symbol` and malformed
hex inputs.

### `tests/test_probe_freshness.py::TestCli` (2 new tests)
Malformed `--expected-token` → exit 2; valid token still accepted.

### Makefile: `host-test-all` target
Runs **every** `tests/test_*.py` so a newly added test can never be silently
omitted from the regression set. The M-P2.36 regression slipped through because
the earlier ad-hoc runner listed tests by hand and left `test_report_readback.py`
out.

```bash
make host-test-all  # PASS (14 test files, 134 tests)
```

---

## Offline Test Results

All 14 test files pass standalone (no external `PYTHONPATH`):

```
tests/test_m2_negative.py
tests/test_pongo_addr_resolve.py
tests/test_pongo_artifact_select.py
tests/test_pongo_client.py
tests/test_pongo_regs_command.py
tests/test_probe_freshness_integration.py
tests/test_probe_freshness.py
tests/test_probe_report.py
tests/test_probe_selfreport.py
tests/test_readback_design.py
tests/test_regs_output.py
tests/test_report_readback_freshness_api.py
tests/test_report_readback.py
tests/test_usb_watch.py
```

All offline Makefile targets pass:
- `report-readback-test` (selfcheck + test_report_readback + test_readback_design)
- `probe-report-test`
- `pongo-client-test`
- `probe-selfreport-test`
- `pongo-addr-check`
- `pongo-artifact-check`
- `regs-output-check`
- `m2-check`

Aggregate `host-test-all`: **PASS (14/14 test files)**

---

## Integrity Evidence

### Nested Repos (unchanged)
| Repo | Commit | Dirty? |
|------|--------|--------|
| pongoos-src | cb83dd83452d5107628b32eeb462222609f565a6 | 0 files |
| pongoos-src/newlib | feaa8fcf90de497485b74e6f65fd78a58a27b9d7 | 0 files |

### Preserved Artifacts (`artifacts/m-p2.26-cb83dd83/`, 4 files)
All match `manifest.json` (sha256 + size):
- `artifacts/Pongo` — OK (770696 bytes)
- `artifacts/Pongo.bin` — OK (675936 bytes)
- `artifacts/PongoConsolidated.bin` — OK (763760 bytes)
- `artifacts/checkra1n-kpf-pongo` — OK (87808 bytes)

### Build Baseline (`pongoos-src/build/`, 10 files)
All 10/10 identical to M-P2.29 baseline (sha256 + size + mtime within 1e-6s):
- `checkra1n-kpf-pongo`
- `checkra1n-kpf-pongo.arm64.ld/filelist`
- `checkra1n-kpf-pongo.arm64.ld/files/main-3bc7c2.o`
- `checkra1n-kpf-pongo.arm64.ld/files/shellcode-f20758.o`
- `checkra1n-kpf-pongo.arm64.ld/link_command`
- `checkra1n-kpf-pongo.arm64.ld/orig_command_line`
- `Pongo`
- `Pongo.bin`
- `PongoConsolidated.bin`
- `vmacho`

### Preserved Firmware Files (untouched, not staged)
- `boot/aarch64/pongo_probe.S`
- `boot/aarch64/pongo_probe.ld`
- `pongoos-src/build/` (entire directory)

### Top-level Worktree (only M-P2.44 changes)
```
 M Makefile
 M tests/test_pongo_addr_resolve.py
 M tests/test_probe_freshness.py
 M tests/test_probe_freshness_integration.py
 M tools/host/pongo_addr_resolve.py
 M tools/host/probe_freshness.py
 M tools/host/report_readback.py
?? tests/test_report_readback_freshness_api.py
```

---

## Constraints Honored
- iPhone 6 / Apple A8 NOT connected (no USB/DFU/checkm8/payload/flashing/restore)
- Arch Linux only; no package installs, no `sudo`, no global config changes
- `pongo_probe.S`, `pongo_probe.ld`, PongoOS firmware, on-device report writer
  NOT modified; no rebuild/relink; `pongoos-src/build/` bytes/timestamps unchanged
- Nested `pongoos-src` (and newlib) NOT modified/committed/pushed
- `artifacts/m-p2.26-cb83dd83/` (4 files) + M-P2.29 10-file build baseline preserved
- No build outputs, binaries, logs, caches, bytecode, secrets committed
- No speculative protocol fields/token transport/API contracts/device responses
- Legacy `read_report_via_md8()` return shape `(parsed_report, text)` preserved
- Host-side/offline tests NEVER described as proof of iPhone 6/A8 compatibility
- Token match ≠ cryptographic auth or hardware compatibility
- v2 (MP1PRB02) remains **DESIGN PROPOSAL ONLY**

---

## Elapsed Time
- M-P2.44 session start: 2026-10-14T16:44:38+07:00 (T0 = 1791971078)
- Report capture:        2026-10-14T20:37:41+07:00
- **Elapsed: ~3h 53m wall-clock**
