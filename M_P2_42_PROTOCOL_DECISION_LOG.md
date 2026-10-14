# M-P2.42 — Protocol Decision Log

**Verdict: PARTIAL (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**

## Decision 1: Existing transport cannot carry token (evidence-based)
- **Status:** DECIDED (based on source)
- **Evidence:** Command callbacks receive `(cmd, args)` (command.c/regs.c) but md8 is `md8 <base> <size>` (usbloader.c/main.c); no token parameter exists. No implemented mechanism to echo token in report.
- **Rationale:** Cannot infer transport support from callback signature.
- **Alternative:** Invent transport → rejected (violates constraints).
- **Unresolved:** exact command/token format.

## Decision 2: Report v2 layout (design-only)
- **Status:** PROPOSED (unresolved details)
- **Proposal:** Keep size 0x60, bump version to 2, repurpose `reserved_zero` (+0x58) as `freshness_token`.
- **Rationale:** Uses existing reserved field; preserves other offsets.
- **Unresolved:** token width/encoding, confirmation across all readers.
- **Risk:** Firmware change required (not authorized).

## Decision 3: Preserve legacy MD8 API
- **Status:** DECIDED
- **Evidence:** `read_report_via_md8()` returns `(parsed_report, text)`; changing shape breaks callers.
- **Rationale:** Backward compatibility critical.
- **Decision:** Do not change; add opt-in API instead.

## Decision 4: Token match = correlation, not auth
- **Status:** DECIDED
- **Rationale:** Explicitly distinguish freshness from cryptographic authentication/hardware compatibility.

## Unresolved questions
1. Token width/representation
2. Host→probe command format
3. Opt-in API name/signature
4. Probe-side versioning rules when token present

All unresolved marked as UNRESOLVED in spec. No hardware tests conducted. No firmware changes.