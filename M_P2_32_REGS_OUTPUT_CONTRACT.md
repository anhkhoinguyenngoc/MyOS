# M-P2.32 — `regs` Output Contract, Offline Fixtures & Report-Capture Readiness

**Status: PASS (offline)** — contract derived from the pinned source, host-side
parser + fixtures added and passing, 0x200 report analysed from source.
**Hardware status: UNKNOWN / UNVERIFIED.** No iPhone was connected or
contacted; nothing here proves Apple A8 runtime behaviour.

Milestone: M-P2.32
Base commit: `5444fa2010d17b7fde4bafc99caf5a5ec28ea268` (MyOS `main`)
PongoOS pinned: `cb83dd83452d5107628b32eeb462222609f565a6` (untouched)
newlib submodule: `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`
Machine: Arch Linux, user `khoi`. Offline; no rebuild, no relink, no device.

---

## 1. Preflight (Phase A)

| item | value |
|---|---|
| OS / user | Arch Linux / `khoi` |
| MyOS HEAD / branch | `5444fa20…` / `main` |
| MyOS `origin/main` | `5444fa20…` (matches HEAD, worktree clean) |
| PongoOS HEAD / branch | `cb83dd83…` / `master` (clean) |
| newlib HEAD | `feaa8fcf…` (clean) |
| remotes | `origin https://github.com/anhkhoinguyenngoc/MyOS.git` |
| preserved artifacts | 4/4 match `artifacts/m-p2.26-cb83dd83/manifest.json` (size + sha256) |

All four preserved artifacts (`Pongo`, `Pongo.bin`, `PongoConsolidated.bin`,
`checkra1n-kpf-pongo`) verified against the manifest; `REPORT.md` verified too.

---

## 2. `regs` output contract (Phase B)

### 2.1 Source of truth

`pongoos-src/src/shell/regs.c` (107 lines) — read in full, not assumed:

* `regs_cmd` (regs.c:81) prints five lines via newlib `iprintf(...)`.
* `regs_commands_register` (regs.c:105) registers the command name `regs`
  with description `"print system register values"`.
* Registered from `shell_main()` (`pongoos-src/src/shell/main.c`).

### 2.2 Line grammar

```
<key>=0x<hex>
```

* **key** — one of the five field names, lower-case, exactly as in the C string.
* `=` — single literal separator (no spaces).
* `0x` — literal **lower-case** prefix, part of the format string, always present.
* `<hex>` — produced by `iprintf("%llx", …)`: **lower-case** hexadecimal digits,
  **minimal width** (no leading zeros, no zero-padding, no sign, no `0X`, no `+`).
  Value `0` prints as `0`, i.e. the full token is `0x0`.
* **line ending** — LF (`\n`). No trailing whitespace, no separators, no labels.
* **one field per line**; exactly five lines per invocation, in a fixed order.

### 2.3 Fields (fixed order)

| # | key | source line | value semantics |
|---|---|---|---|
| 1 | `current_el` | regs.c:82,85 | **raw** `CurrentEL` MRS value (not the decoded EL) |
| 2 | `midr_el1` | regs.c:88,93 | `MIDR_EL1` read (only when `el == 1`) |
| 3 | `sctlr_el1` | regs.c:89,94 | `SCTLR_EL1` read (only when `el == 1`) |
| 4 | `cntpct_el0` | regs.c:90,95 | `CNTPCT_EL0` single sample (only when `el == 1`) |
| 5 | `cntfrq_el0` | regs.c:91,96 | `CNTFRQ_EL0` read (only when `el == 1`) |

### 2.4 Stability

| field | class | why |
|---|---|---|
| `current_el` | variable across contexts, constant within a run | depends on the EL the shell runs at |
| `midr_el1` | implementation-defined constant | per-core ID; only read at EL1 |
| `sctlr_el1` | runtime-dependent | boot/MMU control state; only read at EL1 |
| `cntpct_el0` | **strictly variable** | free-running counter; changes every read |
| `cntfrq_el0` | firmware/implementation constant | programmed by boot firmware; only at EL1 |

### 2.5 Behaviour when a register is unavailable

The five lines are **always all printed**. `current_el` is always the raw MRS
value. The four EL1-only registers are read **only when `el == 1`**; at any
other EL (`el == 0`, `2` or `3`) they print exactly `0x0` (regs.c:97-101).
They are never omitted and never error out. So `midr_el1=0x0` is the *normal*
output below EL1, not a failure.

### 2.6 Per-register architectural notes

Values below are **architectural facts only**. No Apple A8 runtime value is
asserted anywhere in this milestone.

* **CurrentEL** — read-only, accessible at every EL. Bits `[3:2]` are the EL
  field; bits `[1:0]` and `[63:4]` are RES0. The raw MRS value is therefore
  quantised to `{0x0, 0x4, 0x8, 0xC}`. `regs` prints the raw value.
  *Cannot be predicted offline:* which EL PongoOS/the shell runs at on A8.
* **MIDR_EL1** — 32-bit; bits `[63:32]` RES0. Fields: Implementer `[31:24]`,
  Variant `[23:20]`, Architecture `[19:16]`, Primary part number `[15:4]`,
  Revision `[3:0]`. Readable at EL1+ (traps at EL0).
  *Cannot be predicted offline:* the specific Implementer/Part/Revision
  encoding of the Apple A8 core.
* **SCTLR_EL1** — system control register (M/A/C/SA/I/… and many
  implementation-defined bits). 64-bit on ARMv8-A. Readable at EL1+.
  *Cannot be predicted offline:* the boot/MMU configuration left in effect.
* **CNTPCT_EL0** — 64-bit physical counter, incremented at `CNTFRQ_EL0` Hz.
  Accessible at EL0+. Reads are monotonically increasing between calls.
  *Cannot be predicted offline:* any absolute value (it is free-running).
* **CNTFRQ_EL0** — 32-bit frequency in Hz; bits `[63:32]` RES0. Accessible at
  EL0+. Set by firmware/bootloader; a value of `0` means the frequency is not
  known/programmed.
  *Cannot be predicted offline:* the A8 frequency. This is firmware-dependent;
  PongoOS does not necessarily program it.

**Not asserted (UNKNOWN):** the runtime value of any of the five fields on the
iPhone 6. Fixtures use arbitrary values for shape only.

---

## 3. Host-side parser and fixtures (Phase C)

New tool: **`tools/host/regs_output.py`** (stdlib only, offline).

* Parses/validates the `regs` text above into `{ok, fields, raw, order, el, issues}`.
* Field set + order are **re-derived from `regs.c`** (`keys_from_source`), so
  source drift fails the tests (`--source-check`).
* Tolerates real shell framing: bare CR, a `pongoOS> ` prompt prefix
  (command.c:139-143 prints `\r` + prompt with **no** trailing newline) and
  ANSI colour escapes; CRLF and LF both accepted.
* Enforces a small set of **architectural bounds** (not hardware values):
  * `current_el` ∈ `{0x0,0x4,0x8,0xC}` (RES0 bits),
  * `midr_el1` ≤ `0xFFFFFFFF` (32-bit register),
  * `cntfrq_el0` ≤ `0xFFFFFFFF` (32-bit register).
  Enforcement is separable (`enforce_arch=False`) and each rule carries its
  justification string.
* Issue types: `missing_field`, `malformed_value`, `leading_zero`, `overflow`,
  `duplicate_field`, `unknown_field`, `unexpected_line`, `out_of_order`,
  `arch_violation`.
* CLI: `--file FILE | --stdin | --source-check`, `--json`; exit 0 accepted,
  1 rejected, 2 usage/source.

New tests: **`tests/test_regs_output.py`** (19 tests, all pass). Fixtures
(inline, synthetic) cover:

* **accepted** — complete EL1 report; below-EL1 all-zero report; prompt+ANSI+CRLF
  framing; an arbitrary-but-architecturally-valid report (proves no hardware
  value is asserted).
* **rejected** — missing field; duplicate field; truncated output; unexpected
  extra output; malformed hex (`0x`, `0X4`, `0x4A`, `4`, `0x-1`, `0xg`);
  leading zeros (`0x04`); 64-bit overflow; unknown field.
* **architectural** — `current_el=0x5`, `midr_el1=0x1ffffffff`,
  `cntfrq_el0=0x100000000` rejected as arch violations and accepted with
  `enforce_arch=False`; the five runtime fields are declared unverified.

Fixture values (e.g. `midr_el1=0x410fd083`, `cntfrq_el0=0x16e3600`) are
**placeholders for shape**, explicitly *not* A8 expectations.

---

## 4. Report capture at 0x200 (Phase D)

### 4.1 What "0x200" means (source evidence)

"0x200" is the **offset**, not the size. The existing MP1PRB01 report block is
defined by `boot/aarch64/pongo_probe.S` (`.equ` block, lines 93-108) and placed
by `boot/aarch64/pongo_probe.ld` (`.report 0x200`):

| property | value | evidence |
|---|---|---|
| offset in raw image | `0x200` (`RPT_OFF`) | pongo_probe.S:108, pongo_probe.ld:29,45 |
| size | `0x60` (96 bytes) | `RPT_SIZE` pongo_probe.S:107 |
| version | `1` | `RPT_VERSION` pongo_probe.S:106 |
| endianness | little-endian | `OUTPUT_FORMAT("elf64-littleaarch64")` pongo_probe.ld:18 |
| image size | 608 B = `0x260` | `build/pongo_probe.bin` (so the report ends exactly at EOF) |

Because the image is linked at 0 (pongo_probe.ld:24) and the report section is
pinned, **file offset == runtime offset within the upload buffer**; the probe
computes the address PC-relative (`adr x12, probe_report`, pongo_probe.S:117).

### 4.2 Byte layout (u64 slots, little-endian)

| offset | field | writes at runtime? |
|---|---|---|
| `+0x00` | magic `MP1PRB01` | static + rewritten at entry |
| `+0x08` | version (u32 = 1) | static + rewritten |
| `+0x0C` | report_size (u32 = 0x60) | static + rewritten |
| `+0x10` | `x30_entry` (buffer physical base) | runtime |
| `+0x18` | `x0_entry` (gBootArgs cacheable VA) | runtime |
| `+0x20` | `x8_entry` (gEntryPoint) | runtime |
| `+0x28` | `current_el` (raw `CurrentEL`) | runtime |
| `+0x30` | `sctlr_el1` | runtime |
| `+0x38` | `midr_el1` | runtime |
| `+0x40` | `cntpct_el0_1` | runtime |
| `+0x48` | `cntpct_el0_2` (second sample after a delay loop) | runtime |
| `+0x50` | `cntfrq_el0` | runtime |
| `+0x58` | `reserved_zero` (stored `xzr`) | runtime |

The **static header** (magic/version/size) is pre-initialised in the file, so
the uploaded bytes are verifiable *before* `bootr`; the **dynamic slots**
(`+0x10`..`+0x58`) are zero in the file and are written only at entry
(pongo_probe.S:119-167). The format overlaps the `regs` command on
`current_el`/`sctlr_el1`/`midr_el1`/`cntfrq_el0`, but via a different mechanism
(post-handoff binary vs live shell text), and it additionally captures the two
`CNTPCT_EL0` samples, the handoff registers, and a reserved-zero slot.

### 4.3 Validation and failure conditions

`tools/host/probe_report.py` (`parse_report`) rejects: blob shorter than
`0x60`; bad magic; `version != 1`; `report_size != 0x60`. It reports
`has_dynamic_data()` (any runtime slot non-zero).

### 4.4 Valid vs incomplete vs stale

* **valid** — full `0x60` bytes, correct magic/version/size.
* **incomplete** — fewer than `0x60` bytes at `0x200` (short read / clamp);
  `parse_report` raises. A capture must therefore read **exactly `0x60`** bytes:
  a `0x200`-byte window from `0x200` would run to `0x400`, **past** the
  `0x260`-byte image.
* **fresh vs stale** — **not distinguishable from content today.** The format
  has no nonce/epoch, so a report from an earlier run (same magic/version) is
  byte-indistinguishable from a fresh one. The only freshness signal currently
  available is the pre-handoff Tier-0 check (`tools/host/probe_selfreport.py`):
  before `bootr` the dynamic slots must read **all zero** in device RAM; a
  non-zero slot there means the buffer was reused/aliased. After `bootr` the
  channel is gone (hard ordering fact: `entry.c` tears down USB/serial/IRQs
  before the jump — documented in `probe_selfreport.py`, `report_readback.py`),
  so a post-handoff readback is not available on the pinned tree.

### 4.5 What remains unverified until `bootr` runs on hardware

* Every dynamic slot's runtime value — `current_el`, `sctlr_el1`, `midr_el1`,
  the two `CNTPCT_EL0` samples (and that they actually differ), `cntfrq_el0`,
  and the handoff registers `x30/x0/x8`.
* That the probe executes at file offset 0 and that `bootr` reaches it at all.
* Any post-handoff readback path (none exists live; B1/B6 remain blocked).

### 4.6 DESIGN PROPOSAL (NOT current behaviour)

To make **freshness/staleness** checkable, the following is proposed — it does
**not** exist in the pinned format and is **not** implemented here:

> **MP1PRB02 (proposed)** — keep offsets `0x00`..`0x58` identical, bump
> `version` to `2`, and use the currently `reserved_zero` slot at `+0x58` as a
> 64-bit **freshness tag** (e.g. the Tier-0 host-supplied epoch/nonce written
> into the buffer before `bootr`, echoed back by the probe). The host would then
> reject a report whose tag does not match the epoch it planted, closing the
> stale-vs-fresh gap. A version-2-aware parser would accept both `1` (no tag)
> and `2`, and the `0x60` size / little-endian layout would not change.

This is a proposal only; no wire format was modified and no offsets were
invented as existing behaviour.

---

## 5. Tests, regression and safety (Phase E)

New: `python3 tests/test_regs_output.py` → **19 tests, OK** (exit 0).
New tool self-check: `python3 tools/host/regs_output.py --source-check` → OK.

Existing offline suites (unchanged, re-run): `tests/test_pongo_regs_command.py`,
`tests/check_pongo_probe.py`, `tests/test_readback_design.py`,
`tests/test_pongo_artifact_select.py`, and the resolver
`tools/host/pongo_addr_resolve.py --verify-source`. Results are captured under
`/tmp/m-p2.32/` (see §7).

Integrity: the four preserved artifacts still match the manifest; the ten
`pongoos-src/build/` files retain their M-P2.29 sha256/size and mtimes; all
three repos are clean. **No rebuild/relink** and **no hardware-dependent script**
ran; the iPhone was not connected.

---

## 6. Evidence boundary

* **Offline correctness (this milestone):** the parser/fixtures correctly accept
  realistic valid output and reject each malformed class, and the 0x200 layout
  is documented from the actual assembly/linker sources.
* **Unverified hardware behaviour:** register values on Apple A8, `bootr`
  execution, and any post-handoff readback. Fixtures are synthetic and never
  assert a device value.

---

## 7. Changed files and evidence

Changed / added (tracked, source + tests + docs only):

* `tools/host/regs_output.py` — new host parser/validator for `regs` text output.
* `tests/test_regs_output.py` — new fixture-based test suite (19 tests).
* `M_P2_32_REGS_OUTPUT_CONTRACT.md` — this document.
* `Makefile` — new `regs-output-check` target (offline).

Evidence (untracked, under `/tmp/m-p2.32/`): `preflight.txt`,
`cli-evidence.txt`, `REPORT.md` (raw milestone report), plus test/integrity
logs.
