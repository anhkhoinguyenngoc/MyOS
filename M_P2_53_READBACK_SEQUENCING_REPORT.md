# M-P2.53 -- Report Readback Sequencing and Stale-Data Isolation Report

Date: 2026-10-15
Previous HEAD: d88ab9c191f4ed141cd3725e14f132f1cf501545
Local HEAD after fix: 5fd047bf7ee5b8ebe7e4b3f521d22aa2bceee4b0
Status: No confirmed defect; comprehensive offline sequencing tests added.

## 1. Environment

- OS: Arch Linux (x86_64)
- Repo: /home/khoi/Projects/myos
- No hardware interaction; no USB/DFU; offline host-side only.

## 2. Baseline

- `make host-test-all`: PASS (pre-existing: 14 test files, 283unittest tests).
- 8 offline make targets: PASS.
- Preserved artifacts: 4/4 OK.
- Build baseline: 10/10.
- Nested repos unchanged.

## 3. Phase A -- Read lifecycle documented

See `/tmp/m-p2.53/READ_LIFECYCLE.md` for full trace. Summary:

```
ReportReadback.read_report(buffer_va, expected_token=None)
 1. Validate buffer_va (non-zero int)
 2. Validate window: [buffer_va+RPT_OFF, buffer_va+RPT_OFF+RPT_SIZE) within window
 3. client.drain_stdout()  (up to 16 rounds of up to 4096 bytes)
 4. client.set_stdout_mode(0)
 5. client.send_command("md8 0xVA 0xSIZE")
 6. _collect(): up to max_polls:
      chunk = read_stdout(maxlen=0x1000)
      if chunk: accumulate
      else: if check_complete(): break
      if max_polls exhausted -> ReadbackTimeout
 7. parse_md8_strict(text, addr, RPT_SIZE)  (strict row/addr/octet check)
 8. validate_report(blob, expected_token=...) ->
       probe_freshness.validate_report (if present, v2 only)
       probe_report.parse_report (exact 96 bytes, reserved_zero == 0)
Returns: parsed report dict (+ freshness fields)
```

`PongoClient.read_report_via_md8(buffer_va, ...)`:
  1. text = md8(buffer_va + rpt_off, rpt_size) -> send_command + drain_stdout
  2. blob = parse_hexdump(text)  (lenient; ignores invalid hex, prompts)
  3. probe_report.parse_report(blob)  (strict 96 bytes)
  4. reserved_zero == 0 check
Returns: (report_dict, text)

Key transport invariants observed:
- Mock stdout ring capacity: 4095 bytes (0x1000 - 1).
- `_ring_write()` appends, discards OLDEST bytes (FIFO) on overflow.
- `control_in(REQ_STDOUT_READ)` is DESTRUCTIVE: drains entire ring; if data > wLength returns TAIL and discards HEAD permanently.
- `check_complete()` returns idle/running flag.
- No cached results, no retry loop (only max_polls in `_collect`), no concurrent calls.
- drain_stdout clears up to 16 * 4096 = 65536 bytes >> 4095 byte ring.

## 4. Phase B -- Sequencing tests

New file: `tests/test_readback_sequencing.py` (17 tests, 391 lines), using
PROBE-DA-visible distinct synthetic reports A / B / C (differ by x30, x0, x8,
sctlr, midr, cntpct1/2, cntfrq) so any stale/mixed return value is exposed.

Reports:
- A: 0xAAA... / 0x111... / 0x444... etc
- B: 0xBBB... / 0x222... / 0x555... etc
- C: 0xCCC... / 0x333... / 0x666... etc.

Cases exercised:
1. `test_sequential_reads_A_then_B_then_C` -- sequential reads A -> B -> C return distinct values.
2. `test_stale_data_in_ring_before_read` -- stale partial row in stdout ring drained; current memory report returned.
3. `test_stale_full_report_in_ring` -- full stale report A hexdump in ring; drained; report B returned.
4. `test_valid_prefix_then_truncated_second` -- raw bytes (valid prefix + truncated second report) drained.
5. `test_valid_then_truncated_same_address` -- hexdump text report A then truncated; current memory report returned.
6. `test_empty_output_after_success` (torn-down device) -- PongoChannelGone, not stale reuse.
7. `test_malformed_after_success` -- parse_md8_strict rejects malformed framing; legitimate re-read succeeds.
8. `test_command_failure_after_success` -- device teardown after first read raises PongoChannelGone; no stale reuse.
9. `test_repeated_reads_same_report` -- 5 reads all same values; no drift.
10. `test_repeated_reads_different_addresses` -- repeated same-base reads remain stable.
11-14. token freshness: matching token (V2_VALID_FRESH), mismatch (V2_VALID_STALE -> MalformedReport), no token (unverifiable), v1 with expected_token (LEGACY_VALID_FRESHNESS_UNVERIFIED).
15-17. legacy API: accepts valid report; rejects bad reserved; rejects v2.

## 5. Phase C -- Findings

No confirmed stale-data or sequencing defect.

Key disprove:
- drain_stdout is sufficient to clear the 4095 byte ring in one to a few iterations
  (16 round budget is ample for 4 KB).
- md8 report is 96 bytes (~342 bytes hexdump), well within wLength limits; no tail-truncation occurs.
- The device-side md8 generates fresh text per send; no cached text persists between invocations.
- parse_md8_strict rejects row-count/size/addr/octet violations; reduces mixed/multi-report ambiguity to malformed/error, which surfaces promptly; no false acceptance.
- Exact 96-byte validation (M-P2.48) + reserved_zero (M-P2.46) catch oversized/contaminated blobs.

Intentional behaviors preserved:
- `parse_hexdump()` (legacy) leniency.
- `read_report_via_md8()` does not claim token-based freshness.
- v2 remains DESIGN PROPOSAL ONLY; v1 always fresh_status LEGACY_VALID_FRESHNESS_UNVERIFIED.

Freshness guarantees implemented vs proposed:
- Implemented: v2 expected_token compare inside ReportReadback (MalformedReport V2_VALID_STALE on mismatch).
- Proposed only: end-to-end token transport from probe / device -> host beyond comms (no production CLI supplies expected token).

Risk/residual items not verifiable offline:
- Actual wire-level output ordering on an Apple A8 device.
- Multi-thread/console interleaving (API is synchronous).
- Token delivery path once v2 probe firmware exists.
- Host does not detect in-flight replacement while `_collect` polls SUB a cmd already stopped running.

## 6. Regression and integrity

After edits (test file creation only; no production source change):
- `make host-test-all`: PASS (15 test files, 300unittest tests total; was 283 + 17 added).
- 8 offline make targets: PASS (listed in /tmp/m-p2.53/TEST_RESULTS.txt).
- Artifacts: 4/4 OK.
- Builds 10/10 OK.
- Nested repos unchanged (pongoos-src cb83dd83, newlib feaa8fcf).

## 7. Git

- Commit: 5fd047bf7ee5b8ebe7e4b3f521d22aa2bceee4b0 "Test report readback sequencing and stale data (M-P2.53)"
- Push + remote verification documented after commit/complete.

## 8. Hardware compatibility

Remains UNKNOWN / UNVERIFIED. No physical-device claim based on mock tests.
