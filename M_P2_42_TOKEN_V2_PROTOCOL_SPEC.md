# M-P2.42 — Token v2 Protocol Specification and Compatibility Design

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**  
No hardware interaction; no firmware/probe changes; no rebuild/relink. All findings are source-based. v2 is DESIGN PROPOSAL ONLY.

## 1. Executive summary

This document defines a concrete, reviewable proposal for a freshness/correlation token in the MP1PRB01 probe report (v2). It distinguishes existing facts from proposals and from unresolved dependencies. Key observations:

- **Transport:** No implemented host→probe token transport exists. Command callbacks receive `(cmd, args)` but there is no established protocol for carrying a request token to the probe that is echoed in the report.
- **Probe behavior:** No firmware change proposed; the probe currently writes MP1PRB01 as defined in `pongo_probe.S` with `reserved_zero` at `+0x58` (8 bytes). No token echo implemented.
- **Host validation:** Canonical freshness validator (`probe_freshness.validate_report`) exists and requires exact token match for v2 fresh; `ReportReadback.read_report()` accepts `expected_token`. `read_report_via_md8()` bypasses freshness and must remain backward compatible.
- **API compatibility:** `read_report_via_md8()` return shape `(parsed_report, text)` must remain unchanged. Freshness status cannot be injected into that existing return shape without breaking callers.
- **Lifecycle completeness:** Creation/transport/probe handling/report encoding for token are PROPOSED/UNRESOLVED; host validation is EXISTING.

## 2. Existing protocol baseline (source facts)

- **Command registration:** `command_register(name, desc, cb)` in `pongoos-src/src/shell/command.c` (help at 179). Commands stored in array; dispatch via `command_execute`. Callback signature: `void (*cb)(const char* cmd, char* args)`.
- **regs command:** `regs_cmd(const char* cmd, char* args)` in `pongoos-src/src/shell/regs.c` (81-103) registers via `regs_commands_register()` (105-107). Parses no token; reads registers and prints via `iprintf` (key=value). Output contract documented (M-P2.32).
- **Shell I/O:** Shell loop reads via `fgets`; prompt is `iprintf("pongoOS> ")` (command.c:143). Stdout buffered in `stdout_buf` (io.c, `STDOUT_BUFLEN=0x1000`, pongo.h).
- **Probe report v1 (implemented):** MP1PRB01 defined in `pongoos-src/boot/aarch64/pongo_probe.S` (layout `.equ` block). `probe_report.py` mirrors `EQU_MIRROR` (RPT_SIZE 0x60, RPT_VERSION 1, RPT_OFF 0x200, reserved `RPT_ZERO_OFF` = 0x58). Magic `MP1PRB01`; little-endian.
- **Host readback (channel):** md8 path: host sends `md8 <base> <size>` via ep0 OUT (usbloader.c:120); device hexdumps via `iprintf` (main.c); host drains stdout via ep0 IN (usbloader.c:151). This works pre-handoff only.
- **Freshness:** `tools/host/probe_freshness.py` implements canonical validator (statuses: `INVALID`, `LEGACY_VALID_FRESHNESS_UNVERIFIED`, `V2_VALID_FRESH`, `V2_VALID_STALE`, `V2_VALID_FRESHNESS_UNVERIFIABLE`). `ReportReadback.validate_report()` composes it. `ReportReadback.read_report(buffer_va)` accepts `expected_token` but does not propagate freshness to caller; legacy path returns parsed dict. `read_report_via_md8()` uses `probe_report.parse_report()` directly (bypasses freshness) and returns `(parsed_report, text)`.

## 3. Proposed token lifecycle

| Stage | Status | Specification (proposed unless marked EXISTING) |
|---|---|---|
| 1. Host creates fresh token | **PROPOSED CHANGE** | Per readback operation, host generates a fresh token. Token size/encoding: **UNRESOLVED** (v2 proposal suggested repurposing +0x58; must confirm against all readers/writers if implemented). Collisions acceptable at operational scope; scope = single request. |
| 2. Host retains expected token | **PROPOSED CHANGE** | Host stores expected token for the operation until completion/timeout; never reused across independent operations. |
| 3. Host sends token | **UNRESOLVED DEPENDENCY** | Existing transport: md8 is `md8 <base> <size>` (no token). No supported mechanism in audited source. Proposed: extend a supported command to carry token (e.g. `regs <token>` or new command) — **UNRESOLVED**. Cannot invent new transport; must use existing if any. |
| 4. Probe receives token | **UNRESOLVED DEPENDENCY** | `regs_cmd` receives `(cmd, args)` string but does not parse token. Probe-side parsing and validation **PROPOSED**. |
| 5. Probe associates token with report | **UNRESOLVED DEPENDENCY** | Probe writes token into MP1PRB01 at token field (proposed +0x58, width **UNRESOLVED**). Must not change total size/version/magic unless versioned. |
| 6. Probe returns report containing token | **UNRESOLVED DEPENDENCY** | Report bytes read back via existing channel (md8 of report range) — but channel behavior unchanged; token appears in bytes. No change to md8 format. |
| 7. Host compares token | **EXISTING IMPLEMENTATION** | `probe_freshness.validate_report(blob, expected_token)` performs exact token match; statuses V2_VALID_FRESH/V2_VALID_STALE/V2_VALID_FRESHNESS_UNVERIFIABLE as defined. |
| 8. Host propagates freshness status to caller | **PROPOSED CHANGE** | Propagation to callers: cannot alter `read_report_via_md8()` return shape. Must use opt-in API or return metadata in separate object; see §6. |

Constraints: missing token must never imply fresh; mismatched token never accepted as fresh. Token match is correlation/freshness check, **not cryptographic authentication**.

## 4. Transport contract (proposed, with unresolved)

- **Request representation (PROPOSED):** If using existing `regs` command, carry token in `args` (string). Parsing format **UNRESOLVED** (hex vs decimal; width). No existing support.
- **Response representation (EXISTING):** Report bytes at `buffer_va + 0x200`, size 0x60. Token appears as bytes in report if probe writes it.
- **Transport limits:** md8 operates while shell alive (pre-handoff). Size limits: stdout ring 0x1000; ep0 IN wLength 512 or 0x1000. **UNRESOLVED** whether token affects these.
- **Timeout/retry:** Deterministic offline poll budget modeled in `ReportReadback` (default 16). Real transport uses I/O timeouts. **PROPOSED** semantics: one request→one response correlation; timeout means no status established.
- **Compatibility:** Existing commands/callers must not break. Adding token to command is backward compatible only if absent token treated as legacy path. **PROPOSED**.

**Conclusion:** Existing transport does **not** support a token mechanism; command callback signature is insufficient evidence of implemented token transport.

## 5. Report v2 layout and compatibility (proposed)

Current v1 (implemented, source facts):
- magic 8 bytes `MP1PRB01` at 0x00
- version 4 bytes (LE) at 0x08, value 1
- report_size 4 bytes (LE) at 0x0C, value 0x60
- dynamic fields at 0x10..0x58 (u64): x30_entry,x0_entry,x8_entry,current_el,sctlr_el1,midr_el1,cntpct_el0_1,cntpct_el0_2,cntfrq_el0
- reserved_zero at 0x58: u64, value 0 (implemented in probe writer/expected by parser)

Proposed v2 (DESIGN ONLY; **UNRESOLVED**):
- Keep total size 0x60, magic unchanged, version bumped to 2 (PROPOSED). All parsers must accept v1; new parsers must reject unknown versions.
- Repurpose `reserved_zero` (0x58, 8 bytes) as `freshness_token` (PROPOSED). Width/format **UNRESOLVED** (8 bytes LE assumed by host validator? validator treats blob bytes as-is; `probe_freshness` reads u64 at 0x58 in v2 context). 
- Do **not** change other offsets/fields.
- Backward compatibility: v1 reports must remain `LEGACY_VALID_FRESHNESS_UNVERIFIED`; v2 with no expected token → `V2_VALID_FRESHNESS_UNVERIFIABLE`; v2 with match → `V2_VALID_FRESH`; v2 with mismatch → `V2_VALID_STALE` or `INVALID` per validator rules. Missing expected token must never produce fresh.

**Compatibility risks:** Any firmware change that populates +0x58 breaks assumptions if version not bumped; all host readers must be tolerant. Since firmware changes not authorized, v2 remains DESIGN ONLY.

## 6. Host API contract (proposed, backward compatible)

- `read_report_via_md8()` (**MUST NOT CHANGE**): returns `(parsed_report, text)` as currently defined. No freshness metadata added to this tuple. This preserves all existing callers (including tests `test_pongo_client.py`).
- `ReportReadback.read_report(buffer_va, expected_token=None)` (EXISTS): accepts `expected_token`; current implementation may return parsed dict or annotated dict depending on freshness composition path (see report_readback). To expose freshness status without breaking legacy callers, **PROPOSED**: separate opt-in method returning `(parsed_report, freshness_status, freshness_detail, text?)` or similar; choose name after inspecting conventions.
- Canonical validator: reuse `probe_freshness.validate_report(blob, expected_token)`. Do not duplicate validation logic.
- Token rules: if `expected_token is None` → v2 yields `V2_VALID_FRESHNESS_UNVERIFIABLE` (never fresh); v1 yields legacy unverified. Exact match required for fresh.

**Conclusion:** No safe way to inject freshness into legacy MD8 API. Separate opt-in API is the only backward-compatible path; name/signature **UNRESOLVED** pending review of naming conventions.

## 7. Security and failure analysis

- **Freshness vs auth:** Token echo provides request→response correlation and helps detect stale responses/replay across timeouts. It does **not** provide cryptographic authentication, trusted execution, device identity, or hardware compatibility. Explicitly state this in all future documentation.
- **Failure modes:** missing token, mismatched token, malformed report, unknown version, timeout, disconnect (PongoChannelGone). 
- **Token reuse:** Scoped to single operation; host must not reuse across requests.
- **Timing:** Delayed responses after timeout → correlation lost; host must treat as no freshness.
- **Threat model:** Simple echo is vulnerable to forgery if adversary controls report bytes; out of scope for this design (separate problem).
- **Safety:** No firmware/probe changes proposed; no hardware interaction.

## 8. Implementation dependencies (UNRESOLVED)

1. **Probe-side:** Parse token from command args; write token into report at v2 token field; bump version to 2 when token present. **REQUIRES FIRMWARE CHANGE** (not authorized).
2. **Host transport:** Define command/token format and ensure it works with existing channel. **UNRESOLVED**.
3. **MD8 API:** Cannot change return shape; must keep `read_report_via_md8()` unchanged. **BLOCKS** direct integration unless opt-in API added.
4. **Token generation:** Host must generate fresh token per operation; no existing source. **PROPOSED** (host-side concern).
5. **Report encoding v2:** Define exact token width/representation; confirm no other consumers read +0x58 as reserved_zero. **UNRESOLVED**.

## 9. Step-by-step future implementation plan

1. **Audit transport** (source-evidence): confirm exact command/transport path supports carrying token; record symbols. If none → BLOCKED on transport.
2. **Design token field** (if probe change authorized): finalize width/offset (confirm +0x58 availability across all readers); define version bump rules.
3. **Implement probe changes** (separately authorized): token parsing + writing; versioning; backward compatibility in probe. **OUT OF SCOPE HERE**.
4. **Add opt-in host API** (backward compatible): e.g. `read_report_via_md8_with_freshness(base_va, size, expected_token, **kw)` returning `(parsed_report, freshness_status, freshness_detail, text?)` or similar — avoid breaking tuple shape of legacy API. Choose name after inspecting conventions.
5. **Update ReportReadback** (optional): extend to propagate freshness status to callers via opt-in method; keep existing `read_report()` behavior stable.
6. **Tests**: add offline synthetic tests for v1/v2 cases (fresh/mismatch/no-token/invalid/truncated); verify legacy API unchanged; run full regression.
7. **Hardware validation**: only if probe changes implemented and authorized. **NOT DONE HERE**.

## 10. Acceptance tests required (for future “implemented” status)

- [ ] Probe echoes token in v2 report (evidence from probe source/tests, not synthetic alone)
- [ ] Host→probe transport carries token (evidence)
- [ ] v2 fresh when token matches; stale/mismatch detected; no-token never fresh
- [ ] v1 remains legacy unverified
- [ ] `read_report_via_md8()` return type/behavior unchanged
- [ ] All existing regression suites pass
- [ ] Integrity: artifacts and build baseline unchanged
- [ ] No hardware interaction claimed

## 11. Statement

**Factual:** No implemented token transport/echo exists in audited source. `read_report_via_md8()` bypasses freshness and cannot be safely extended without breaking contract. Host validator exists.  
**Proposed:** v2 repurposing +0x58 as freshness_token with version bump; opt-in freshness-aware API separate from legacy MD8 API.  
**Unresolved:** token width/format, exact transport mechanism, probe-side changes, opt-in API name/signature.  
**Safety:** No firmware/probe changes, no rebuild/relink, no hardware interaction. Token match ≠ cryptographic authentication or hardware proof; hardware compatibility remains UNKNOWN/UNVERIFIED.