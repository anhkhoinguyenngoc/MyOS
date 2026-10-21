# M6.1 — A8 Entry-Capture Specification (preparation only)

Date: 2026-10-18
Status: **specification / preparation. No device was contacted, nothing was
executed on hardware, and no register value below is claimed as measured.**
Scope: offline review + a bounded, non-invasive specification for future A8
entry-state diagnostics. iPhone 6 remains disconnected.

Related: `PLATFORM_INTERFACE.md` (M6 platform boundary), `M1_PLAN.md` (§3–§4
pwned-DFU entry ABI), `M2_A10_RUNTIME_HANDOFF_AUDIT.md` (source audit of the
PongoOS `bootr` RAW handoff), `BOOT_FEASIBILITY.md` (evidence and matrix).

---

## 0. Purpose and honesty constraints

The platform boundary (M6) left the Apple A8 `UNSUPPORTED` because its entry
contract, memory map, and console are unestablished. This document specifies
the **smallest architectural observation step** that could convert some of
those unknowns into evidence, and it states precisely what that step can and
cannot prove.

Two hard rules govern everything below:

1. **A register read proves a value at one instant on one core.** It is a
   sample, not a contract. It does not prove firmware intent, that the state is
   stable, or that it generalizes to another boot path.
2. **CPU architectural registers do not reveal the physical DRAM map.**
   `CurrentEL`, `SCTLR_EL1`, `TTBR0_EL1`, `MIDR_EL1`, `CNTFRQ_EL0`, and
   `CNTPCT_EL0` describe the CPU, its translation regime, and the system timer.
   None enumerates RAM base, size, holes, or reserved ranges. Any claim that
   they do would be false.

The M1 payload already samples a small subset (`CurrentEL`, `MIDR_EL1`,
`CNTFRQ_EL0`, `CNTPCT_EL0`) into fixed DFU response slots
(`platform/apple/a8/m1_entry.S`, `M1_PLAN.md §3`). This specification generalizes
that into a **versioned, self-describing, truncation-detectable record** and
fixes the semantics of each field. It does not change the M1 payload.

---

## 1. Proposed capture point and its preconditions

The capture must run where (a) the registers of interest are architecturally
accessible at the current exception level, and (b) an unexpected trap cannot
silently kill the run. The two candidate contexts differ sharply:

| Context | Vector table installed? | Consequence |
|---|---|---|
| Pwned-DFU exec hook (M1 carrier; `m1_entry.S`) | No | Reading a register that traps (e.g. an EL1 register while at EL2/EL3) is **fatal or undefined**. The hook must not disturb `sp`/`x30`/`x19`/`x20`. |
| A MyOS kernel entry (future) | Yes (MyOS `VBAR_EL1`) | Traps are observable; the full register set can be attempted with per-field validity flags. |

Because the DFU hook has no vector table, the **first safe capture must read
only registers guaranteed accessible at the level actually observed**, and must
decide what to read *after* reading `CurrentEL` — never before. This is the
central safety property, and it is also why `SCTLR_EL1`/`TTBR0_EL1` cannot be
blindly added to the existing DFU payload.

Precondition summary:

- Capture at `CurrentEL >= EL1` (the intended MyOS entry is source-audited to
  EL1; see `M2_A10 §4`).
- Do not read an `_EL1` register unless `CurrentEL == EL1` (or a MyOS-owned
  vector table is installed and can catch the resulting trap).
- No writes, no MMU/cache changes, no memory access of any kind for the
  architectural capture described in §2.

---

## 2. Per-register semantics

Legend for "Answers" / "Still unknown" is relative to the *entry-state*
question, not to memory discovery.

### 2.1 `CurrentEL` — current exception level

- **Architectural meaning:** read-only register whose bits `[3:2]` encode the
  current exception level (`0x4` = EL1, `0x8` = EL2, `0xC` = EL3); bits `[1:0]`
  are RES0.
- **Preconditions:** readable at every EL. No trap risk.
- **Safe and meaningful at the capture point?** Yes — it is the *first* thing to
  read, and it gates every other read.
- **If unavailable:** architecturally it never is; a value with bits `[1:0]`
  nonzero is invalid and must be rejected, not "corrected".
- **Answers:** which privilege level MyOS was actually handed control at
  (validates the EL1 source audit on real silicon).
- **Still unknown:** why that EL was chosen; whether EL2/EL3 exist and are used
  on this SoC in this boot path; any monitor/firmware state at higher ELs.

### 2.2 `SCTLR_EL1` — EL1/EL0 system control

- **Architectural meaning:** controls the EL1&0 translation regime. Key bits:
  `M` (bit 0) MMU enable; `A` (bit 1) alignment check; `C` (bit 2) data-cache
  enable; `I` (bit 12) instruction-cache enable; plus write-control fields.
- **Preconditions:** read at `CurrentEL == EL1`. At EL2/EL3 the active control
  register is `SCTLR_EL2`/`SCTLR_EL3`, not `SCTLR_EL1`; reading `SCTLR_EL1`
  from a higher EL describes a *different* (lower) regime and is not the live
  one.
- **Safe and meaningful at the capture point?** Only when `CurrentEL == EL1`.
  With no vector table (DFU hook), reading it at a higher EL risks a trap.
- **If unavailable:** mark `UNAVAILABLE_EXCEPTION_LEVEL`; value `0`; do not
  guess.
- **Answers:** is the **EL1 MMU on or off** at entry (`M`)? Are data/instruction
  caches enabled (`C`/`I`)? Is alignment checking on (`A`)?
- **Still unknown:** the full translation configuration (granule, VA size, memory
  attributes live in `TCR_EL1`/`MAIR_EL1`, not read here); whether the current
  physical access path is identity-mapped; how to change any of it safely; the
  EL2/EL3 regime (`SCTLR_EL2`/`SCTLR_EL3` are not captured).
- **Trap to avoid:** `SCTLR_EL1.M == 0` means the EL1 MMU is off *for the EL1&0
  regime*; it does **not** mean all translation everywhere is off.

### 2.3 `TTBR0_EL1` — EL1&0 lower-range translation table base

- **Architectural meaning:** base address of the translation table used for the
  lower virtual-address range of the EL1&0 regime; carries an ASID field and
  configuration bits in the upper bits.
- **Preconditions:** read at `CurrentEL == EL1`.
- **Safe and meaningful at the capture point?** Only when `CurrentEL == EL1`
  **and** interpreted together with `SCTLR_EL1.M`.
- **If unavailable:** `UNAVAILABLE_EXCEPTION_LEVEL` (or `UNAVAILABLE_NOT_ACTIVE`
  when `M == 0`); value `0`.
- **Answers:** what base address the EL1&0 lower-regime page tables had at
  handoff.
- **Still unknown / not proven:** a nonzero `TTBR0_EL1` does **not** prove the
  MMU is on. When `SCTLR_EL1.M == 0`, the register is stale firmware residue and
  does not control translation. Its value says nothing about DRAM layout, the
  contents or size of the tables, which physical pages are usable, or whether
  the pointed-to tables remain valid. It is **not** a memory map.

### 2.4 `MIDR_EL1` — main ID register

- **Architectural meaning:** identifies the core implementation: Implementer
  `[31:24]`, Variant `[23:20]`, Architecture `[19:16]`, PartNum `[15:4]`,
  Revision `[3:0]`.
- **Preconditions:** read-only, read at `CurrentEL >= EL1`. (The M1 payload
  already guards it to EL1; see `m1_entry.S`.)
- **Safe and meaningful at the capture point?** Yes, at `CurrentEL == EL1`.
- **If unavailable:** `UNAVAILABLE_EXCEPTION_LEVEL`; value `0`.
- **Answers:** which CPU core revision we are executing on — a cross-check that
  the A8 "Typhoon" core (and a specific implementation/revision) is what runs
  our code.
- **Still unknown:** it does **not** identify the SoC (T7000), the device
  (`n61ap`), the board, the cache geometry (that is `CTR_EL0`, out of scope), or
  anything about memory.

### 2.5 `CNTFRQ_EL0` — system counter frequency

- **Architectural meaning:** the frequency of the system counter, in Hz.
  Firmware is expected to program it; it is writable at the highest implemented
  EL (typically EL3).
- **Preconditions:** read at `CurrentEL >= EL1` (EL0 readability is controlled
  by `CNTKCTL_EL1`).
- **Safe and meaningful at the capture point?** Yes, at EL1; but the value is
  only meaningful if nonzero.
- **If unavailable / not programmed:** a returned `0` must be flagged
  `UNAVAILABLE_NOT_PROGRAMMED`. `0` is **not** "0 Hz"; it means the frequency
  was not established.
- **Answers:** the counter frequency needed to convert `CNTPCT_EL0` ticks into
  time — the prerequisite for any delay/timeout logic without SoC documents.
- **Still unknown:** counter width (not directly readable); whether the counter
  is stable across power states on this SoC; whether EL0 access is enabled.

### 2.6 `CNTPCT_EL0` — physical counter value

- **Architectural meaning:** the current value of the physical system counter.
  Reading it has no side effects.
- **Preconditions:** read at `CurrentEL >= EL1` (EL0 readability controlled by
  `CNTKCTL_EL1`). Architecturally monotonic for a running counter.
- **Safe and meaningful at the capture point?** A single read is nearly
  meaningless on its own; it becomes evidence only as **two samples** bracketing
  a known instruction sequence, interpreted with `CNTFRQ_EL0`.
- **If unavailable:** `UNAVAILABLE_EXCEPTION_LEVEL`; value `0`.
- **Answers:** (with `CNTFRQ_EL0` and a second sample) is the counter advancing,
  and roughly how much time elapsed between two points?
- **Still unknown:** wall-clock alignment to anything absolute; timer interrupt
  routing; anything about memory.

### 2.7 What the set does and does not establish

| Dimension | Register(s) | Established? |
|---|---|---|
| CPU identity / revision | `MIDR_EL1` | Yes (core only) |
| Exception level at entry | `CurrentEL` | Yes |
| EL1 MMU enable / cache enable | `SCTLR_EL1` | Yes (at EL1) |
| EL1&0 page-table base | `TTBR0_EL1` | Recorded, but only meaningful with `M` |
| Timer frequency / counter | `CNTFRQ_EL0`, `CNTPCT_EL0` | Yes (if `CNTFRQ_EL0 != 0`) |
| Physical DRAM base/size/holes | — | **No** — not recoverable from these registers |
| A MyOS-owned console | — | **No** |
| AIC interrupt controller map | — | **No** |

---

## 3. Bounded capture-record format (proposed)

Conventions mirror `include/myos_image.h`: little-endian, fixed-width, packed,
reserved fields must be 0, host-side validation. Fields are serialized
explicitly (no raw C struct) and sorted ascending by `field_id`. The reference
implementation is `tools/host/capture_record.py` (host-only, stdlib); its
byte-level expectations are pinned independently in
`tests/test_capture_record.py`. There is as yet **no on-target producer** — the
module encodes/parses only, and no test asserts a hardware value.

```
Header (32 bytes):
  0x00  u64  magic            = MYOS_A8_CAP_MAGIC ("MYOSA8EC", LE)
  0x08  u32  version          = 1
  0x0C  u32  record_size      = 32 + 16*field_count + 4
  0x10  u32  field_count      = number of 16-byte entries (0..16)
  0x14  u32  flags            = reserved, must be 0
  0x18  u64  capture_seq      = caller-supplied label, 0 if unavailable

Entry (16 bytes, field_count of them, ascending field_id):
  +0x00 u32  field_id         = MYOS_A8_FIELD_*
  +0x04 u32  field_flags      = exactly one of VALID or a single UNAVAILABLE_*
  +0x08 u64  value            = raw register value; MUST be 0 unless VALID

Trailer (4 bytes):
  u32  crc                  = CRC-32/ISO-HDLC over bytes [0, record_size-4)
```

**CRC parameters (pinned):** CRC-32/ISO-HDLC (IEEE 802.3), i.e. identical to
`zlib.crc32`: polynomial `0x04C11DB7` (reflected `0xEDB88320`), initial value
`0xFFFFFFFF`, input and output reflected, final XOR `0xFFFFFFFF`. The covered
bytes are exactly the `record_size - 4` bytes preceding the trailer. Check
value: `crc32(b"123456789") == 0xCBF43926`.

Numeric constants: `magic` LE = `0x43453841534F594D`; `field_count` bound = 16;
`record_size` bound = 292 (`0x124`); `ENTRY_SIZE` = 16.

Proposed field IDs:

| ID | Field | Notes |
|---|---|---|
| `0x01` | `CurrentEL` | gates the rest |
| `0x02` | `SCTLR_EL1` | EL1 only |
| `0x03` | `TTBR0_EL1` | EL1 only; interpret with `M` |
| `0x04` | `MIDR_EL1` | EL1 |
| `0x05` | `CNTFRQ_EL0` | EL1 |
| `0x06` | `CNTPCT_EL0` (sample 1) | EL1 |
| `0x07` | `CNTPCT_EL0` (sample 2) | EL1; after a known instruction sequence |
| `0x08` | capture PC (`ADRP`/`ADR` of the capture site) | provenance: where the values were sampled |
| `0x09` | handoff arg `x0` (raw) | provenance only; **never dereferenced** |

Proposed `field_flags` bits:

| Bit | Name | Meaning |
|---|---|---|
| `0x01` | `VALID` | register was read and the value is meaningful |
| `0x02` | `UNAVAILABLE_EXCEPTION_LEVEL` | not readable at the observed `CurrentEL` |
| `0x04` | `UNAVAILABLE_TRAPPED` | access trapped (requires a vector table to observe) |
| `0x08` | `UNAVAILABLE_NOT_PROGRAMMED` | read succeeded but value is unset (e.g. `CNTFRQ_EL0 == 0`) |
| `0x10` | `UNAVAILABLE_NOT_CAPTURED` | entry intentionally omitted at this capture point |

### 3.1 Validity and reporting rules

- A field is `VALID` **only** if the read was architecturally permitted at the
  observed `CurrentEL` and returned a defined value. Otherwise the value slot is
  0 and exactly one `UNAVAILABLE_*` reason is set.
- `CurrentEL` is always captured first and is always `VALID`; a record without a
  valid `CurrentEL` is rejected entirely.
- If `CurrentEL != EL1`, the `_EL1` fields (`SCTLR_EL1`, `TTBR0_EL1`,
  `MIDR_EL1`, `CNTFRQ_EL0`, `CNTPCT_EL0`) must be `UNAVAILABLE_EXCEPTION_LEVEL`
  unless a MyOS vector table is installed and the trap path is proven.
- `CNTFRQ_EL0 == 0` ⇒ `UNAVAILABLE_NOT_PROGRAMMED`, and the two `CNTPCT_EL0`
  samples are recorded raw but must not be interpreted as elapsed time.

### 3.2 Truncation / incompleteness detection

- **Bounded:** `field_count <= 16` and `record_size <= 292` (`0x124`).
- **Self-consistent:** the receiver requires
  `record_size == 32 + field_count*16 + 4`; anything else is rejected as
  malformed (truncated or over-long).
- **Transport-length check:** the received buffer length must equal
  `record_size`; a short buffer is truncated and a long buffer has trailing
  bytes. Both are rejected (`SizeMismatch`); the record is never partially
  accepted.
- **Integrity:** the CRC-32/ISO-HDLC must match; a mismatch is a hard reject.
- **Reserved:** header `flags == 0`; every `field_flags` must be exactly one of
  `VALID` or a single `UNAVAILABLE_*` reason (no reserved bits); an unavailable
  field's `value == 0`. Any violation is rejected.
- **Field identity:** `field_id` must be known (the closed set in §3); unknown
  ids are rejected rather than ignored, and duplicate ids are rejected.
- **Semantics:** a `VALID` `CurrentEL` must be present; otherwise the record is
  rejected (`MissingCurrentEl`).
- A record missing `CurrentEL` or the terminator/CRC is treated as incomplete,
  never "best-effort parsed".

The reference parser returns a distinct exception category for each failure
(`BadMagic`, `UnsupportedVersion`, `SizeMismatch`, `BadCrc`, `BadFlags`,
`ValueForUnavailable`, `UnknownFieldId`, `DuplicateField`, `TooManyFields`,
`MissingCurrentEl`); `tools/host/capture_record.py` documents the exact order.

### 3.3 Output provenance

- The record may only be produced by code executing on the target; its
  `capture_pc` field records *where* the sample was taken so a host parser can
  distinguish contexts (DFU hook vs kernel entry).
- Until a real capture exists, the repository holds only this specification and
  the host-side reference encoder/parser (`tools/host/capture_record.py`). The
  host must not assert any hardware value, and no test may encode an "expected"
  register value: the capture-record tests use explicitly synthetic fixtures.
  (The M6 host test suite likewise refuses to encode hardware values; see
  `tests/test_platform_boundary.py`.)

### 3.4 Relationship to the existing M1 slots

The M1 payload's DFU response slots (`+0x10 CurrentEL`, `+0x18 MIDR_EL1`,
`+0x30 CNTFRQ_EL0`, `+0x38 CNTPCT_EL0`) are a fixed, unversioned subset with no
validity flags. This record format is the versioned superset intended for a
future entry capture. Migrating M1 to it is **not** part of M6.1 and would change
the M1 response ABI, so it is deliberately deferred.

---

## 4. Memory-probe safety boundary

**No memory probe is proposed, implemented, or authorized in this milestone.**
An address becomes eligible for a *future, read-only* probe only when **all** of
the following hold, each backed by target-specific evidence:

1. **Range justified.** The address lies in a range whose physical extent is
   established for *this* SoC by (a) a measured map produced by a MyOS-owned run,
   or (b) an authoritative primary source that names T7000/A8 and the address —
   not by analogy to QEMU `virt`, to another Apple SoC, or to another milestone's
   fixture.
2. **Access method justified.** The range is known to be normal memory, or the
   probe runs with a MyOS-owned vector table installed so a fault is
   observable. If the range is MMIO, the specific register must be known
   read-only and side-effect-free.
3. **Probe is minimal.** Read-only, single-shot, fixed width, explicit bounded
   count, aligned, and not crossing into an adjacent unknown range.
4. **No state is changed.** No MMU/cache reconfiguration, no DRAM init, no
   register writes, no boundary scan, no "walk until it faults".
5. **Result is filed with provenance.** Each result carries the justification for
   the address and a validity flag, exactly as in §3.

Explicitly forbidden:

- Reading a hard-coded QEMU address (`0x09000000`, `0x40080000`) and labelling
  the result as A8 data.
- Scanning/stepping addresses to discover memory extent.
- Any write, cache maintenance by address, or MMU change.

### 4.1 Missing evidence (must be obtained, not invented)

- No reliable public source for the T7000/A8 physical DRAM base, size, or holes
  (`BOOT_FEASIBILITY.md §5`).
- No public T7000 register documentation for UART, AIC, timer, or display
  (`BOOT_FEASIBILITY.md §2`).
- No hardware capture of the entry `CurrentEL`/`SCTLR_EL1`/`TTBR0_EL1` on this
  device (source audit only, `M2_A10 §4`).
- No established MyOS *kernel* entry ABI for a chosen carrier (checkra1n `-k`
  vs pongoOS module); only the pwned-DFU exec-hook ABI is established
  (`M1_PLAN.md §3–§4`).
- No MyOS-owned output channel that survives a carrier handoff; post-pwn USB is
  carrier-owned (`ARCHITECTURE.md §6`).
- Whether the inherited PongoOS vector table is usable after MMU-off
  (`M2_A10 §4`) — this directly affects whether `SCTLR_EL1`/`TTBR0_EL1` can be
  read safely at kernel entry.

Until this list is discharged, the A8 platform stays `UNSUPPORTED` and the
`#error` gate stays in place.

### 4.2 Next prerequisite for safe physical research

The next prerequisite is **an execution/output context from which a capture can
be delivered without risking an unhandled trap** — not a memory probe. Concretely:

1. Re-establish the M1 pwned-DFU path (currently BLOCKED on host timing;
   `M1_REPORT.md §5.2`). It already has a carrier-mediated return channel and a
   working `m1ctl exec` client, and it does not install vectors.
2. Extend that payload to read only the registers guaranteed accessible at the
   **observed** `CurrentEL` (read `CurrentEL` first, then gate), emitting the
   §3 record with explicit validity flags — `CurrentEL`, `MIDR_EL1`, `CNTFRQ_EL0`,
   two `CNTPCT_EL0` samples, and `SCTLR_EL1`/`TTBR0_EL1` **only if `CurrentEL`
   is EL1 and a proven vector table exists**.
3. Only after a MyOS kernel entry context with a MyOS-owned vector table exists
   may the full `_EL1` set be attempted and traps observed.

This step measures CPU/timer/EL state only. It is explicitly **not** DRAM
discovery, and it must not be described as such.

---

## 5. Unresolved assumptions (kept unresolved on purpose)

- The PongoOS `bootr` RAW handoff being the intended MyOS carrier is a
  *source-audited design choice*, not a device observation (`M2_A10 §4`).
- The entry register/EL/MMU state is audited from source, not captured.
- The DFU-image-buffer address and exec-hook ABI come from the vendored gaster
  source for CPID `0x7000` (`M1_PLAN.md §4`), not from our own measurement.
- `MYOS_ADDR_UNKNOWN == 0` is a fail-closed sentinel; it is not a claim that
  physical address 0 is unmapped (see `platform/platform.h`).
