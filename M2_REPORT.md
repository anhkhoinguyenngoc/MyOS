# MyOS M2 — Kernel Image Format & Loader Feasibility (M2-A3)

## Status: COMPLETE (host-side deliverables)

This phase defines the canonical M2 kernel image format, implements a
host-side validator with negative tests, corrects the memory-map
simulator, and adds a `make m2-check` target. No PongoOS source was
modified. No device operations were performed.

## 1. Canonical Image Format

Defined in `include/myos_image.h` (the authoritative spec). All fields
are little-endian, fixed-width, packed.

### Header (64 bytes, `myos_image_hdr_t`)

| Offset | Size | Field          | Meaning                                  |
|--------|------|----------------|------------------------------------------|
| 0x00   | 8    | magic          | `0x4E52454B534F594D` ("MYOSKERN")        |
| 0x08   | 4    | version        | `1`                                      |
| 0x0C   | 4    | arch           | `0` = AArch64                            |
| 0x10   | 8    | load_addr      | target physical load address (16 KB aligned) |
| 0x18   | 8    | entry_offset   | entry point offset from `load_addr`      |
| 0x20   | 8    | image_size     | total file size (header+table+payloads)  |
| 0x28   | 8    | bss_size       | zero-fill size AFTER `image_size` (not in file) |
| 0x30   | 4    | segment_count  | number of segments (<= 16)               |
| 0x34   | 4    | flags          | reserved, must be 0                      |
| 0x38   | 8    | reserved       | must be 0                                |

### Segment entry (48 bytes, `myos_segment_t`)

| Offset | Size | Field    | Meaning                                  |
|--------|------|----------|------------------------------------------|
| 0x00   | 8    | p_paddr  | target physical address (`load_addr + p_offset`) |
| 0x08   | 8    | p_offset | offset in file from start of header      |
| 0x10   | 8    | p_filesz | size in file (0 for pure-BSS segment)    |
| 0x18   | 8    | p_memsz  | size in memory (>= `p_filesz`)           |
| 0x20   | 4    | p_prot   | `MYOS_PROT_READ\|WRITE\|EXEC` (1\|2\|4)  |
| 0x24   | 4    | p_align  | power of 2, >= 16 KB                     |
| 0x28   | 8    | reserved | must be 0                                |

### File layout

```
[0x0000, 0x0040)                header (64 bytes)
[0x0040, 0x0040+48*N)           segment table
[after segments .. image_size)  segment payloads (file data)
[image_size .. image_size+bss_size)  zero-filled BSS (NOT in file)
```

### Enforced invariants (validated by host validator and future loader)

1. `magic == MYOS_IMAGE_MAGIC`
2. `version == MYOS_IMAGE_VERSION`
3. `arch == MYOS_ARCH_AARCH64`
4. `segment_count <= MYOS_MAX_SEGMENTS`
5. `load_addr % MYOS_PAGE_SIZE == 0`
6. `entry_offset < image_size`
7. `image_size <= received_bytes` (upload buffer capacity)
8. `header_size + segment_count * segment_size <= image_size`
9. For each segment:
   - `p_offset + p_filesz <= image_size`
   - `p_filesz <= p_memsz`
   - `p_paddr == load_addr + p_offset` (identity file-to-memory mapping)
   - `p_align` is power of 2, `p_align >= MYOS_PAGE_SIZE`
   - `p_paddr % p_align == 0`
   - `p_paddr + p_memsz <= load_addr + image_size + bss_size`
   - no overlapping destination ranges with any other segment
   - no overlapping file payload ranges with any other segment
10. Entry point (`load_addr + entry_offset`) lies within an executable
    segment (`p_prot & MYOS_PROT_EXEC`) and within its `p_memsz`.
11. BSS region `[image_size, image_size + bss_size)` is zeroed by loader.
12. All reserved fields and `flags` must be 0.
13. Unknown permission bits in `p_prot` cause rejection.

Note: because invariant 9c enforces identity mapping, `p_offset` must be
a multiple of `p_align` (since `load_addr` is page-aligned). The file
payload overlap check (9h) is therefore defense-in-depth: under identity
mapping, file-payload overlap implies destination overlap, so the
destination check fires first.

## 2. Host Validator

`tests/validate_m2.py` — standalone Python 3, no dependencies.
Parses the header and segment table with explicit little-endian
`struct.unpack` formats, performs overflow-safe arithmetic
(`check_add_overflow` / `check_mul_overflow`), and enforces every
invariant above. Exit code 0 = valid, 1 = invalid. `--verbose` prints
the decoded layout.

## 3. Negative Tests

`tests/test_m2_negative.py` — 31 cases covering every rejection class:
bad magic/version/arch, nonzero flags/reserved, truncated header and
segment table, declared `image_size` > actual file, segment-count
overflow, integer-overflow attempts (`entry_offset`, `load_addr +
p_offset`, `bss_size`), file-range OOB, `filesz > memsz`, destination
range OOB, destination overlap, file-payload overlap, alignment not a
power of two, alignment too small, unknown permission bits, entry
outside executable segment, entry in RW-only segment, empty executable
segment, unaligned `load_addr`, unaligned `p_paddr`, identity-mapping
violation, nonzero segment reserved field, zero segments with entry set;
plus two positive multi-segment cases (RX+RW, and a BSS-only segment).

## 4. Memory-Map Simulator

`tests/sim_mmap.py` — host-side, hypothetical. Models the PongoOS
physical layout using the audited formulas:

- `phystokv(pa) = pa - 0x800000000 + kCacheableView` (mm.c:893)
- `kCacheableView = 0x400000000` (pongo.h:344), so `VA = PA - 0x400000000`
- `alloc_static_base (VA) = (topOfKernelData - 0x400000000) & ~0x3fff`
  (mm.c:821) — note `topOfKernelData` is a PHYSICAL address
  (pongo.h:84)
- `alloc_static_end (VA) = 0x417fe0000`, hard-capped at
  `alloc_static_base + 64 MB` (mm.c:822-826)
- `alloc_heap_end (VA) = align_up(phystokv(physBase) + memSize, 0x4000) - 1 MB`
  (mm.c:830)

All absolute addresses derived from `physBase`, `memSize`,
`topOfKernelData` are marked `[RUNTIME]` / `[HYPOTHETICAL]` because
they cannot be verified without a device.

## 5. Makefile Target

`make m2-check` — generates a valid test image
(`tests/make_m2_image.py`), validates it, runs the negative suite, and
runs the simulator. Does not touch the M1 payload or QEMU kernel.

## 6. Test Log

```
$ make m2-check
=== M2 image format validation ===
Image validation OK:
  load_addr:       0x801000000
  entry_offset:    0x4000 (entry_pa: 0x80104000)
  image_size:      0xc000
  bss_size:        0x2000
  segment_count:   2
  seg 0: paddr=0x801004000 offset=0x4000 filesz=0x4000 memsz=0x4000 prot=RX align=0x4000
  seg 1: paddr=0x801008000 offset=0x8000 filesz=0x2000 memsz=0x2000 prot=RW align=0x4000
PASS: build/myos_m2_test.myos - OK
=== M2 negative tests ===
Results: 31 passed, 0 failed, 31 total
=== M2 memory-map simulation ===
Sanity checks:
  load range within DRAM:          YES
  load_addr page-aligned:          YES
  load_addr above PongoOS data:    YES
  load range within free heap:     YES
RESULT: LOAD PLAN VALID for the assumed runtime values (HYPOTHETICAL).
=== M2 CHECK: PASS ===
```

Existing baselines (unchanged, all pass):
- `tests/offline_m1.sh` — ALL_PASS (M1 payload 1240/2016 bytes)
- `tests/test_boot_diag.sh` — All offline tests PASSED
- `tests/test_boot_diag_extended.sh` — exit 0 (expected warnings only)
- `make qemu` — MYOS_KERNEL_PASS

C header size check: `sizeof(myos_image_hdr_t) == 64`,
`sizeof(myos_segment_t) == 48` (verified by compilation).

## 7. Unresolved Runtime-Only Questions

These cannot be answered without a connected device:

1. **`gBootArgs->physBase`** — assumed `0x800000000` (A12 typical);
   must be read at runtime.
2. **`gBootArgs->memSize`** — assumed 1 GB; must be read at runtime.
3. **`gBootArgs->topOfKernelData`** — assumed `0x800400000`; the
   MyOS `load_addr` must be chosen above this at runtime.
4. **`early_heap` / `alloc_heap_base`** — depends on PongoOS's own
   load address and size; the exact start of the free heap is runtime.
5. **Free-list state** — which specific pages are FREE at the moment
   `bootr` runs depends on PongoOS's allocations up to that point.
6. **Framebuffer base** — `0xfb0000000` (virtual) is the known
   mapping, but the physical framebuffer address is runtime.

## 8. GO / NO-GO for a Future PongoOS Loader

**NO-GO for implementing the loader in this phase** (by design — M2-A3
forbids PongoOS modification). The format, validator, and simulator are
GO.

**GO (conditional) for a future loader**, provided it:

- Reads `physBase`, `memSize`, `topOfKernelData` from `gBootArgs` at
  runtime and chooses `load_addr` above `topOfKernelData`, page-aligned,
  inside the free heap `[alloc_heap_base, alloc_heap_end)`.
- Reserves the MyOS pages by **popping them from the free list
  directly** (walk `pa_head`, unlink the nodes, set `ppage_list[i] =
  PAGE_WIRED`). It must NOT use `phys_force_free()` (unsafe on
  arbitrary ranges after init) or `mark_phys_wired()` (panics if any
  page is not FREE).
- Uploads the image via the existing `bootr`/USB path
  (`loader_xfer_recv_data`, 1 MB `alloc_contig` buffer), then calls
  `jump_to_image_extended(upload_buf_pa, gBootArgs, gEntryPoint)` with
  the entry ABI `x0=boot_args*, x1=x2=x3=0, x8=gEntryPoint, sp=UNDEFINED`.
- Zeroes the BSS region `[image_size, image_size + bss_size)` before
  jumping.

The blocking risk is the page-reservation primitive: stock PongoOS has
no safe fixed-address reservation, so the loader must manipulate the
free list directly. This is feasible but requires careful audit of
`pa_head` / `ppage_list` consistency under interrupts (the existing
`phys_unlink_contiguous` at mm.c:650-674 is the closest reference
implementation and disables interrupts).

## 9. Files Added / Changed

Added:
- `include/myos_image.h` — canonical format (authoritative spec)
- `tests/validate_m2.py` — host validator
- `tests/test_m2_negative.py` — 31-case negative suite
- `tests/make_m2_image.py` — valid test-image generator
- `tests/sim_mmap.py` — corrected memory-map simulator
- `M2_REPORT.md` — this report

Changed:
- `Makefile` — added `m2-check` target (no existing targets modified)

Not modified: `pongoos-src/` (revision
`4c9b7541629234147fcc778f0ce4162482aaccef` preserved), M1 payload,
QEMU kernel, boot diagnostics.
