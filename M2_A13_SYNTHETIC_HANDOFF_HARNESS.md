# MyOS M2-A13 — Synthetic PongoOS Handoff Harness

**Audit date:** 2026-10-11 (Asia/Ho_Chi_Minh)  
**Repository:** `/home/khoi/Projects/myos`  
**Recommendation:** `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

This is an isolated contract model. It does not execute PongoOS, does not
measure a physical runtime, does not modify the boot chain, and did not access
a physical device. Every register, address, lifetime, and memory-map value
produced by the harness is explicitly labeled `SIMULATED`.

## 1. Preflight and isolation

Verified repository state:

```text
root:    /home/khoi/Projects/myos
branch:  main
HEAD:    3bea11141fea05bb90017069cff22b9a37d91e9e
Pongo:   4c9b7541629234147fcc778f0ce4162482aaccef
Newlib:  feaa8fcf90de497485b74e6f65fd78a58a27b9d7
```

The initial pre-existing changes were preserved: modified `Makefile`,
`arch/aarch64/boot_diag.c`, `arch/aarch64/boot_diag.h`, and
`arch/aarch64/vectors.S`, plus the previously existing untracked reports,
kernel, boot, include, and test files. Both nested `pongoos-src` worktrees
were clean before and after the task. The top-level `git submodule status`
has no registered entries; the two nested checkout revisions above were
recorded directly.

All new harness source, fixtures, logs, results, and hash manifests are under
`build/audit/m2-a13/`. No file under `pongoos-src/`, the canonical kernel, M1,
the raw bootstrap, existing A10 tooling, or existing fixtures was modified.

## 2. Harness architecture

The implementation is:

```text
build/audit/m2-a13/harness.py
    fixed-root path resolver and fixture hash gate
    source-fact catalogue (not runtime measurements)
    deterministic SIMULATED register/alias/memory model
    overlap, mapping, state, lifetime, and stack invariant checks
    per-scenario JSON/log output and relative-path hash manifest
```

The fixed input fixture is
`build/audit/m2-a13/fixtures/handoff_seed.txt`, with SHA-256:

```text
8c91edc5f49ea2daeb4e9940b9943987f7344f53a6199af75a95cf5b6b435545
```

`fixtures/contract.json` pins that hash and the scenario ordering. The harness
resolves relative fixture/output paths from the repository or audit directory,
never from the caller's current working directory. It refuses missing files
and exits `2` on a stale fixture hash.

The source implementation and contract hashes are:

```text
harness.py       da4d7e1f3a4503783ad6d3ad932985c8b5611701fdc5a1329ea6bca14cb23565
contract.json    ab29e87ad3b91c07f77336c6f39e138df44549debf4da8c4d6cd49cc4aa172e6
```

The second line above was recorded from the final file as
`ab29e87ad3b91c07f77336c6f39e138df44549debf4da8c4d6cd49cc4aa172e6`.

## 3. Source-backed facts versus simulated assumptions

The harness includes the following source-backed facts, copied as evidence
references from the M2-A10 through M2-A12 reports:

| Property | Classification | Evidence |
|---|---|---|
| Successful raw path exception level | `GUARANTEED_BY_SOURCE` | Pongo accepts EL1 or transitions EL3 to EL1; `src/boot/entry.S:84-109`, `src/kernel/mm.c:331-334`. This is static evidence, not a runtime capture. |
| Raw register handoff | `GUARANTEED_BY_SOURCE` | `gBootArgs` is passed in `x0`, `x1..x3` are cleared, and `gEntryPoint` is passed in `x8`; `src/kernel/entry.c:309-312`, `src/boot/jump_to_image.S:42-55`. |
| MMU/cache transition operations | `GUARANTEED_BY_SOURCE` | Identity mapping, RAM cache maintenance, `SCTLR_EL1.M/C` clearing, barriers, and `ic iallu`; `src/kernel/entry.c:303-312`, `src/kernel/lowlevel.c:117-128,641-653`, `src/boot/jump_to_image.S:47-51`. |
| Upload allocator formula and limits | `GUARANTEED_BY_SOURCE` | Contiguous allocator-backed upload with cacheable/physical aliases and source-defined size limits; `src/shell/usbloader.c:32-33,66-89`, `src/kernel/mm.c:844-902`. Actual placement is unknown. |
| Upload explicit lifetime path | `GUARANTEED_BY_SOURCE_WITH_LIMIT` | `bootr` and USB teardown do not explicitly free the upload; `src/shell/main.c:40-48`, `src/drivers/usb/synopsys_otg.c:1866-1870`. Complete external lifetime safety remains unknown. |
| Complete allocator non-overlap | `UNKNOWN_RUNTIME` | Source does not prove disjointness from every live Pongo, boot-argument, DT, framebuffer, or firmware-owned range; `src/kernel/mm.c:794-882`. |
| Vector usability after cleanup | `UNKNOWN_RUNTIME` | Pongo retains/inherits its vector base, but post-MMU-off usability is not proven; MyOS installs vectors later. |

The following are model-only values. The JSON output repeats
`"provenance": "SIMULATED"` for each value and region:

| Model item | Simulated value |
|---|---|
| `CurrentEL` | `0x4` |
| `VBAR_EL1` | `0x8100f000` |
| `SCTLR_EL1` | `0x1000` (`M=0`, `C=0`, modeled `I=1`) |
| `TTBR0_EL1` / `TTBR1_EL1` | `0x812000000` / `0x812020000` |
| raw entry / `x30` | `0x842000000` |
| `x0` boot-argument cacheable alias | `0x418000000` |
| `x8` entry metadata | `0x100000000` |
| upload physical / cacheable aliases | `0x842000000` / `0x442000000` |
| upload size | `0x4c000` |
| boot-argument physical alias | `0x818000000` |
| bootstrap scratch | `[0x842001648, 0x842001720)` |
| proposed MyOS image | `[0x850000000, 0x852000000)` |
| proposed MyOS stack | `[0x852000000, 0x852040000)` |
| proposed vectors/page tables | `[0x852100000,0x852110000)` / `[0x852200000,0x852240000)` |

The modeled transition sequence is also labeled `SIMULATED`: data-cache
maintenance, `dsb sy`/`isb`, clearing `SCTLR_EL1.M/C`, TLBI/barriers, then
`ic iallu; dsb sy; isb`. It is a representation of the source sequence, not
an observation of PongoOS execution.

## 4. Scenario inventory and validation

Scenario execution uses two levels:

- `--scenario NAME` executes the invariant directly. A safe control returns
  `0`; an unsafe state returns `1` after emitting its diagnostic.
- `--scenario all` is the meta-test. It returns `0` only when the valid case
  passes and every negative case returns its expected nonzero status and exact
  diagnostic.

| Scenario | Individual status | Diagnostic |
|---|---:|---|
| `valid_control` | 0 | none; synthetic contract passes |
| `upload_overlap_live` | 1 | `UPLOAD_BUFFER_OVERLAP: myos_image intersects upload_buffer` |
| `boot_args_alias_bootstrap` | 1 | `BOOT_ARGS_ALIAS: boot_args_alias intersects bootstrap_scratch` |
| `invalid_vector_mapping` | 1 | `VECTOR_MAPPING_INVALID: VBAR_EL1 mapping is inaccessible` |
| `inconsistent_mmu_cache` | 1 | `MMU_CACHE_STATE: SCTLR_EL1.M/C contradict MMU-off and D-cache-off contract` |
| `upload_released_early` | 1 | `UPLOAD_LIFETIME: upload buffer is released before raw entry` |
| `insufficient_stack` | 1 | `STACK_BOUNDS: synthetic stack is smaller than bootstrap minimum` |

The aggregate run observed all seven expected outcomes and returned `0`.
The per-scenario result hashes from `run-a/results/` were:

```text
valid_control             06218c30289a8490bec1ba44f9fb7d32a3841f3d65c7d0e819ca237e78c434f4
upload_overlap_live       7d9dd037066a155e83ebadab424e359232227cb9a9f6606c5244074a22e3dc31
boot_args_alias_bootstrap f915c192a777b7e5a07e490ce7d408b6d02c06b990d3a1a7369f3dc231aeeac7
invalid_vector_mapping    9c38fb6b7c05c2618f49c3dfa1c1023d575f53ffb1ace692fce9bc55ddba2636
inconsistent_mmu_cache    792a3f88a0cfa8b6dc351509314a5eacd95887102f386c73a38d68e493f85ff0
upload_released_early     2b735c63e6ce19c0b912d29903d544edb8492d0ffea986b10506de62913d7765
insufficient_stack        b8b8d8ab9988a9485b7c2e903594c93bfb9e374fd71d2a436601d20362110f7c
```

The aggregate `summary.json` hash is
`c9fb945ff1fa46afb18bc64051bf0f2ed615fdb6ee78c6269d8b59cf150f9296`.

## 5. Determinism and fixture controls

The full scenario set was run twice:

```sh
python3 build/audit/m2-a13/harness.py \
  --scenario all \
  --fixture build/audit/m2-a13/fixtures/handoff_seed.txt \
  --outdir build/audit/m2-a13/run-a

(cd /tmp && python3 /home/khoi/Projects/myos/build/audit/m2-a13/harness.py \
  --scenario all \
  --fixture build/audit/m2-a13/fixtures/handoff_seed.txt \
  --outdir build/audit/m2-a13/run-b)
```

Both aggregate commands returned `0`. `run-a/summary.json` and
`run-b/summary.json` both hash to `c9fb945ff1fa46afb18bc64051bf0f2ed615fdb6ee78c6269d8b59cf150f9296`.
The relative-path manifests were byte-identical; both
`hashes.sha256` files hash to
`7c591f9b99390462c02ac367051a7d121a72bb50f9b4c9e480e5fec060c7f63b`.

Fixture rejection controls were also exercised:

| Input | Exit | Result |
|---|---:|---|
| `fixtures/stale_seed.txt` | 2 | refused with expected/observed hash mismatch |
| `fixtures/missing_seed.txt` | 2 | refused because the fixture does not exist |

This harness is Python-only and emits JSON/text, so there is no compiler
binary-reproducibility claim. The M2-A12 limitation remains separately valid:
PongoOS artifacts built in arbitrary staging paths can contain absolute Clang
source paths.

## 6. Required regressions and preservation checks

| Command | Exit | Result |
|---|---:|---|
| `make qemu` | 0 | PASS; `MYOS_KERNEL_PASS` observed |
| `make m2-check` | 0 | PASS; `M2 CHECK: PASS` |
| `bash tests/test_boot_diag.sh` | 0 | PASS; all offline tests passed |
| `bash tests/offline_m1.sh` | 1 | pre-existing `FAIL_m1ctl_usage` |
| `bash tests/test_boot_diag_extended.sh` | 1 | pre-existing missing `.text.vectors` |

The canonical and existing-fixture hash manifest contained 15 artifacts before
the regression commands and the same 15 after them. The manifest comparison
returned `0` with no diff. Notable preserved canonical hashes were:

```text
build/myos_m1.bin  4b0070a402980133c8c8d8467031070acbf604688c0437ca6f36c960812706c0
build/myos_m1.elf  056c64bec4a2cc89cabf5f945c1ec9c6ab67395c0a77b844a260643996e666b8
```

The complete before/after manifests and raw regression logs are under
`build/audit/m2-a13/regression/`.

## 7. Real-runtime limitations

The harness proves only the modeled invariants:

- a proposed image cannot overlap modeled live PongoOS regions;
- a writable boot-argument alias cannot overlap modeled bootstrap scratch;
- vector accessibility, MMU/cache state, upload lifetime, and stack bounds are
  checked as explicit contract conditions;
- diagnostics and status codes are deterministic and cannot be bypassed by a
  log-only observation.

It does not prove actual PongoOS behavior. Actual `CurrentEL`, numeric
`VBAR_EL1`, complete `SCTLR_EL1`/TTBR state, upload physical placement,
boot-argument alias validity, allocator reservations, firmware-owned ranges,
post-cleanup vector usability, and physical cache effects remain unresolved.
The generic `virt` QEMU probes and existing A10 simulator remain distinct from
this model and from an Apple/PongoOS runtime.

## 8. Exact changed files and artifacts

Created or changed for M2-A13:

```text
M2_A13_SYNTHETIC_HANDOFF_HARNESS.md
build/audit/m2-a13/harness.py
build/audit/m2-a13/fixtures/contract.json
build/audit/m2-a13/fixtures/handoff_seed.txt
build/audit/m2-a13/fixtures/stale_seed.txt
```

Generated outputs remain isolated under `build/audit/m2-a13/`, including
`run-a/`, `run-b/`, `individual/`, `fixture-controls/`, and `regression/`.
No protected source, canonical artifact, existing fixture, PongoOS checkout,
or physical device was changed or accessed.

## Final recommendation

`CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

**Next action:** run one hardware-faithful offline PongoOS execution trace of
the actual pinned handoff path to replace these simulated register, mapping,
allocator, and lifetime values with runtime evidence before any physical
experiment.
