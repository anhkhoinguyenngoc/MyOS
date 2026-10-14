# M-P2.33 — Report Freshness Protocol Design & Offline Validation

**Verdict: PASS (offline). Hardware compatibility remains UNKNOWN/UNVERIFIED.**

- Base MyOS commit: `7350cf5c9cd2c96dc586c9c119146350c9184bf1`
- PongoOS pinned: `cb83dd83452d5107628b32eeb462222609f565a6` (untouched)
- newlib submodule: `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` (untouched)
- Machine: Arch Linux, user `khoi`. No iPhone connection/contact. No rebuild/relink.

## 1. Existing report (version 1) — source-derived

**Status: IMPLEMENTED AND TESTED OFFLINE** (layout/validation); **NOT VERIFIED ON HARDWARE**.

Layout derived directly from:
- `pongoos-src/src/shell/pongo_probe.ld` — report section pinned at `0x200`; image linked at 0
- `pongoos-src/src/shell/pongo_probe.S` — `.equ` block (RPT_*)

| property | value | notes |
|---|---|---|
| magic | `MP1PRB01` (`0x313042525031504D` LE) | in .quad in file + rewritten |
| version | `1` (u32 at +0x08) | RPT_VERSION |
| report_size | `0x60` (u32 at +0x0C) | RPT_SIZE; full size 96 bytes |
| offset | `0x200` (RPT_OFF) | file offset == buffer offset (position-independent) |
| endianness | little-endian | ELF64 little-endian |
| dynamic range | `+0x10`..`+0x58` (u64 slots) | written by probe at entry after handoff |
| header range | `+0x00`..`+0x0C` | static in file, rewritten at entry |
| slot 1 (x30) | `+0x10` | buffer physical base |
| slot 2 (x0) | `+0x18` | gBootArgs |
| slot 3 (x8) | `+0x20` | gEntryPoint |
| slot 4 (current_el) | `+0x28` | raw CurrentEL |
| slot 5 (sctlr_el1) | `+0x30` | (EL1 path) |
| slot 6 (midr_el1) | `+0x38` | (EL1 path) |
| slot 7 (cntpct_el0_1) | `+0x40` | first sample |
| slot 8 (cntpct_el0_2) | `+0x48` | second sample (delay) |
| slot 9 (cntfrq_el0) | `+0x50` | frequency |
| slot 10 (reserved_zero) | `+0x58` | written xzr (currently unused) |

Initialization/semantics:
- Static header pre-initialised in file; probe rewrites header fields. Dynamic slots start at 0 in file.
- Writing happens **post-handoff** by probe code (after PongoOS teardown context implied by design; see `probe_selfreport.py`, `report_readback.py`). This creates an intrinsic readback-channel asymmetry — a key reason freshness cannot be “read back” blindly.
- No nonce/epoch field exists. The only freshness signal currently is “all dynamic slots zero” before bootr (pre-handoff Tier-0), which does not survive or prove post-handoff identity of a run.

**Validity hierarchy (current):** structurally valid = full 0x60, magic/version/size correct. Complete = all dynamic slots written. Fresh (current run) = cannot be reliably asserted from content alone. Stale = any content left over from prior run is content-indistinguishable from a newly generated one except via external context (pre-handoff all-zero).

## 2. Threat model and failure modes

**Not goals:** cryptographic authentication, integrity against active attacker, attestation, device identity. **Goal:** detect stale/cached/leftover reports vs a report plausibly produced by the current invocation.

Failure modes considered:
- **Stale report**: buffer region contains an old MP1PRB01 from a previous bootr/probe execution; host sees same layout but old values.
- **Partial write**: probe crashed/parked early; some dynamic slots written, others zero; header written but content incomplete.
- **Truncated capture**: host reads < 0x60 bytes at 0x200 (buffer/image boundary) — must be rejected.
- **Wrong version/magic/size**: mismatch → reject.
- **Buffer reuse/re-alias**: same physical upload buffer reused → possible cross-run contamination.
- **Transport framing**: short/shifted reads → must validate header+size.
- **Legacy ambiguity**: v1 has **no usable freshness token** → a legacy report must never be described as “freshness-verified”.

**Crucial point:** without a run-specific token written into the report, the report bytes alone cannot distinguish “fresh this run” from “stale previous run”. The pre-handoff all-zero state is the only safe freshness guard that exists today, and it is not applicable after the handoff/channel teardown (by design).

## 3. Design options

### Option A — Freshness token (host-generated, echoed by probe)

Host generates a **non-secret** run token `T` (e.g. 64-bit random value or monotonic host sequence) before invoking the probe path. Convey `T` to the payload/probe at upload/start time via a mechanism that exists or can be reasoned about without inventing hardware behaviour. Probe writes `T` into the report at a fixed offset. Host reads report and requires `T == T_expected`.

Pros:
- Simple, testable offline (synthetic fixtures).
- Directly proves the report was written after (or in the context of) planting `T` if the write uses that `T`.
- Fits in a 64-bit slot; preserves header compatibility by repurposing an unused slot.

Cons:
- Requires that `T` can reach the probe in the current upload model. The current pinned codebase uploads the raw image bytes (probe.bin) via PongoOS USB loader; there is no existing field in the probe image that carries a host token except by baking it into the image before upload (or by having the probe read from a known location). Baking a per-run token into the image changes the image bytes each run — that’s a small operational detail but must be documented (determinism of uploads differs). Alternatively the token could live in a fixed scratch location in the upload buffer agreed by host/probe — but that requires the probe to know where. Without source evidence for a second channel, the cleanest offline-reasonable choice is: **bake the token into the report image before upload** (write `T` into `probe_report + offset_token` in the buffer/image copy) so the probe’s stores don’t need to “read” a token from outside except what’s already there — but the probe currently just writes values; echoing a pre-baked token is trivial.
- Token is not secret and not authenticated.
- If the token is baked into the image, the uploaded binary differs per run (non-reproducible by hash alone unless recorded) — must be noted.

### Option B — Monotonic/sequence identifier generated on-device

Try to use a counter that increments each probe run (e.g. read a register, use a global, or read a stable counter). Examples: `CNTPCT_EL0` sampled at a known moment, or a software counter in memory. But:
- `CNTPCT_EL0` is already captured twice (v1 does this) — but the counter value alone doesn’t tell you if it’s “this run” vs “previous run” unless you know the expected window; two independent runs can produce values in overlapping ranges and you can’t distinguish stale bytes.
- No software counter persists meaningfully across `bootr` teardown in the pinned probe (probe is freestanding, no non-volatile store, no saved state across independent uploads except the buffer contents). Also uploads are independent.
- A device-generated monotonic ID without host contribution doesn’t allow the host to know the “expected” value in advance unless it also observed it — impossible post-teardown.
- Uniqueness across resets/independent runs not guaranteed by available state.

Conclusion: **Option B is not well supported by the pinned source**; it cannot provide host-side “expected vs actual” freshness without external coordination. Treat as **NOT VIABLE** given constraints.

### Other notes
- The `reserved_zero` slot at `+0x58` (u64) is unused and written as `xzr` by current probe (pongo_probe.S:167). It’s the natural candidate to hold a freshness token in a v2 layout while keeping the rest identical in placement.
- Size remains `0x60` if we repurpose that single slot. Version must change if semantics change.

## 4. Preserve existing format; propose v2 separately

**Status: DESIGN PROPOSAL ONLY (not implemented).**

### 4.1 Keep v1 unchanged
- Do **not** modify `pongoos-src` probe sources in this milestone.
- `MP1PRB01` (v1) remains valid as **structurally valid / complete / freshness-unverified**. Host validators must not claim freshness for v1.
- Backward compatibility: any future host must accept v1 reports but treat freshness as unknown.

### 4.2 Proposed v2 schema (MP1PRB02)
Only if we introduce a freshness token, use version 2 and the same size `0x60` by repurposing `reserved_zero`:

| offset | field | semantics |
|---|---|---|
| `+0x00` | magic `MP1PRB02` (proposed) | change magic to avoid ambiguity |
| `+0x08` | version `2` (u32) | RPT_VERSION_V2 (proposed) |
| `+0x0C` | report_size `0x60` (u32) | unchanged size |
| `+0x10..+0x50` | same dynamic slots as v1 | x30,x0,x8,current_el,sctlr_el1,midr_el1,cntpct_el0_1,cntpct_el0_2,cntfrq_el0 |
| `+0x58` | `freshness_token` (u64, LE) | **run token** written by probe context (value provided by host/pre-baked) |

Constraints: same little-endian, same offsets for existing fields (compatibility of parsers that read up to +0x50), size unchanged. Header-only magic/version change plus one slot repurposed.

### 4.3 Validator rules (proposed)
- **v1 (MP1PRB01, ver=1)**: accept if magic/version/size correct and bytes well-formed. Set `freshness_verified = false`. Treat “all dynamic non-zero” as completeness hint but **not** proof of current run.
- **v2 (MP1PRB02, ver=2)**: accept only if magic/version/size correct **and** `freshness_token == expected_token`. Also enforce basic sanity (e.g. header written, dynamic slots present). Set `freshness_verified = true` only on match.
- **unknown**: reject (bad magic/version/size).
- **truncation**: reject if < 0x60.
- **partial**: distinguish incomplete (some dynamic zero, token match impossible or not expected) — treat as invalid for “complete+fresh” assertion.

### 4.4 Migration strategy
1. Keep v1 support forever (or until all consumers updated). v1 remains “freshness-unverified”.
2. Introduce v2 only when host+probe can actually carry the token end-to-end. Until then, **do not change probe sources**.
3. Version bump + magic change is required to prevent accidental acceptance of a token-bearing “v1-looking” report.
4. Document the token conveyance mechanism explicitly before implementing probe changes.

## 5. Token conveyance: feasible offline model

Given pinned tree constraints (no second channel described, probe is freestanding), the **minimum viable offline model** is:

1. Host prepares `probe.bin` image copy in memory/temporary file.
2. Host generates `T` (64-bit). Write `T` into the report block at `+0x58` of that image copy (the `.report` section at offset 0x200). The header/magic/version remain as v2 values (or v1 unchanged if not baking) — but baking v2 values changes the image.
3. Host uploads the modified image via existing PongoOS upload path to the buffer.
4. Probe executes: it already stores header/dynamic fields into the report at `probe_report` address (PC-relative). If we want the probe to “echo” the token that’s already there, no code change is needed — the token is already in the buffer. But v1 probe overwrites `+0x58` with `xzr` (pongo_probe.S:167). Therefore to preserve a pre-baked v2 token, the **probe must not zero `+0x58`**; instead it must write the token (or leave it). That requires a small probe change.

**Implication:** Full end-to-end freshness requires **probe source change** (don’t zero +0x58 in v2 path) plus host-side baking+validation. This is a **DESIGN PROPOSAL ONLY** in this milestone — not implemented.

## 6. Offline fixtures and tests

All fixtures synthetic, no A8 values asserted. Tests focus on validation logic (independent of hardware).

**New fixtures (proposed for a future validator):**
- `v1_valid_complete_zero_dynamic` — header ok, dynamic all zero (pre-handoff state) — structurally valid, freshness-unverified.
- `v1_valid_dynamic_nonzero` — dynamic non-zero, v1 — structurally valid, freshness-unverified.
- `v2_valid_fresh` — magic `MP1PRB02`, ver=2, size 0x60, `freshness_token == T`.
- `v2_stale_mismatch` — v2 but token != expected.
- `v2_missing_token_zero` — v2 with token 0 (depends on policy; 0 could be special) — treat as mismatch unless policy allows.
- `truncated_short` — < 0x60 bytes.
- `bad_magic` — wrong magic.
- `bad_version` — unknown version (e.g. 3).
- `bad_size` — size != 0x60.
- `partial_dynamic` — some dynamic zero, token present/mismatched — completeness check.

**Test assertions (design):**
1. v1 accepted but `freshness_verified == False`.
2. v2 with matching token → `freshness_verified == True`.
3. v2 with mismatched token → rejected (stale/mismatch).
4. truncated/size/magic/version → rejected.
5. legacy never claimed fresh.

**Existing suites (re-run):** `make regs-output-check`, `tests/test_pongo_regs_command.py`, `tests/check_pongo_probe.py`, `tests/test_readback_design.py`, `tests/test_pongo_artifact_select.py`, `tools/host/pongo_addr_resolve.py --verify-source` (all offline). No new executable tests added to repo in this design-only milestone (documented only).

## 7. Integrity checks

- Preserved artifacts: 4/4 match `artifacts/m-p2.26-cb83dd83/manifest.json` (size+sha256), REPORT.md verified.
- `pongoos-src/build/`: 10/10 identical to M-P2.29 baseline (sha/size/mtime; drift 0.000000000s).
- Repos: MyOS clean (only intended doc added), PongoOS clean, newlib clean.
- No hardware-facing scripts executed, no rebuild/relink/package install/privileged commands.

## 8. Recommendation and next steps

**Recommendation: DESIGN-ONLY for v2 in this milestone.** Do not change probe sources yet.

Rationale:
- End-to-end token conveyance requires a concrete mechanism (bake token into image before upload, and ensure probe does not clobber `+0x58` in v2 mode). That’s feasible but is a **behavioural change** to the probe.
- `MP1PRB01` must remain accepted with `freshness_verified=false` (no false claim of freshness).
- The probe change is small and testable offline (synthetic fixtures + parser), but implementing it now would blur “design” vs “implementation”. The milestone asks for design+validation; the cleanest compliant path is to document the full protocol and mark implementation as a **separate follow-up** contingent on the chosen conveyance mechanism.

**Proposed next steps (separates DESIGN from IMPLEMENTATION):**
1. Add a host-side validator `tools/host/probe_report_v2.py` (or extend `probe_report.py`) that enforces v1/v2 rules and `freshness_verified` flag — **offline only**.
2. Add synthetic fixture tests for v1/v2 cases.
3. If/when implementing end-to-end: modify `pongo_probe.S` in v2 mode to **not** zero `+0x58` (or write the token). Update linker/asserts if needed. Keep v1 path behaviour unchanged or version-gated. Add regression tests.
4. Only then consider baking token into image per run (operational detail) and runtime validation.

**Non-goals:** cryptographic auth, hardware attestation, proving anything on A8.

## 9. Files added

- `M_P2_33_REPORT_FRESHNESS_PROTOCOL.md` — this design document (added to MyOS repo).

No probe sources modified; no binaries/logs/caches added to repo.
