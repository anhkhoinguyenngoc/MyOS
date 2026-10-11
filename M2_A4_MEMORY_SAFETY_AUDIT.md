# M2-A4 — PongoOS Runtime Memory Safety Audit

## 0. Scope and constraints

This is a **read-only, source-backed audit**. It determines whether a
future MyOS image loader can safely reserve and load a kernel at a
fixed physical address under the pinned PongoOS revision.

Constraints honored:
- `pongoos-src/` NOT modified (verified below).
- No loader, `bootk`, `phys_reserve_range()`, or relocation implemented.
- No device connected; no flashing/restoration/destructive operations.
- No commit/push.
- M1, QEMU kernel, and the canonical image format (`include/myos_image.h`)
  NOT modified.

The audit distinguishes **source-proven facts** (cited to file:line)
from **assumptions** and **hypothetical addresses**. A passing offline
test or a successful QEMU boot is NOT evidence that a physical iPhone
memory range is safe.

---

## 1. Source revision and evidence

```
$ git -C pongoos-src rev-parse HEAD
4c9b7541629234147fcc778f0ce4162482aaccef
$ git -C pongoos-src status --short
(empty — clean working tree)
$ git -C pongoos-src log --oneline -1
4c9b754 This should definitely have been changed too...
```

**Source-proven:** the working tree is at the pinned revision
`4c9b7541629234147fcc778f0ce4162482aaccef` and is unmodified.

---

## 2. Allocator invariants (reconstructed from source)

All references are to `pongoos-src/src/kernel/mm.c` unless noted.

### 2.1 Page size and alignment

- Page size = **16 KB = 0x4000**. Every allocator function shifts
  `pa >>= 14` (mm.c:631, 653, 685, 736, 758, 779) and panics on
  misaligned PAs (`pa & 0x3fff`, mm.c:630, 637, 652, 684, 735, 757,
  778).
- `ppages = gBootArgs->memSize >> 14` (mm.c:798) — one `ppage_list`
  entry per 16 KB page.

### 2.2 Page-state encoding (mm.c:615-617)

```c
#define PAGE_FREE  0
#define PAGE_WIRED 0xffffff
#define PAGE_REFBITS 0xffffff
```

`ppage_list[i]` (a `uint32_t`, mm.c:619) holds exactly one of:
- `0` (`PAGE_FREE`) — page is on the free list;
- `0xffffff` (`PAGE_WIRED`) — page is wired/reserved;
- `1 .. 0xfffffe` — a reference count (page is allocated, in use).

### 2.3 Free-list structure and membership proof

- `uint64_t pa_head` (mm.c:642) = physical address of the first free
  page, or `0` for an empty list.
- Free pages form a **doubly-linked list** using the page's own first
  two words (mm.c:662-663, 725-726):
  - `pa_v[0]` = next free page PA
  - `pa_v[1]` = prev free page PA
- `ppage_alloc()` (mm.c:697-717): pops the head
  (`pa_head = rv_v[0]`, fixes the new head's `prev` to 0), then
  `bzero(rv_v, PAGE_SIZE)` and `phys_reference(rv, PAGE_SIZE)`.
- `phys_page_was_freed()` (mm.c:718-730): pushes a page to the head
  (LIFO): `pa_v[0]=pa_head; pa_v[1]=0; pa_head=pa; free_pages++`.

**Membership proof (source-proven):** a page is free **iff**
`ppage_list[i] == PAGE_FREE (0)`. This is established by:
- `alloc_init` wires every page then carves the heap with
  `phys_force_free`, which sets `ppage_list[i] = PAGE_FREE` and pushes
  each onto the list (mm.c:816-819, 731-750).
- `ppage_alloc` pops a page and `phys_reference` raises it to refcount
  1 (≠ FREE) (mm.c:706-713, 751-771).
- `phys_dereference` decrements the refcount; when it reaches 0 it
  calls `phys_page_was_freed`, which pushes the page onto the list, and
  the decrement has already set `ppage_list[i] = 0` (mm.c:784-788).

**Therefore the page's own contents (first 16 bytes) are list pointers
that are ONLY valid while the page is free. Free-list membership must
NOT be inferred from page contents — it must be proven via
`phys_get_entry(pa) == PAGE_FREE` (mm.c:628-634), which reads
`ppage_list[i]`.** This is the exact hazard the audit brief warns about.

### 2.4 Contiguous allocation (mm.c:844-883)

`alloc_phys(size)`:
- Rounds `size` up to 0x4000 (mm.c:848).
- Single page: O(1) fast path via `ppage_alloc()` (mm.c:856-861).
- Multi-page: **linear scan** of `ppage_list[0 .. ppages)` for the
  first run of `npages` consecutive `PAGE_FREE` entries (mm.c:862-876),
  recording the start `rv`. Then `phys_unlink_contiguous(rv, size)` +
  `phys_reference(rv, size)` (mm.c:879-880).
- Panics on OOM (mm.c:877).

`alloc_contig(size)` = `phystokv(alloc_phys(size))` (mm.c:887-888) —
returns a **cacheable-view virtual address**.

### 2.5 `phys_unlink_contiguous` (mm.c:643-679)

- Silently returns if `pa == 0` or `pa < gBootArgs->physBase`
  (mm.c:644-645) — I/O map / SRAM are ignored.
- Rounds `size` up to 0x4000 (mm.c:648-649).
- For each page in the range: **panics if the page is not FREE**
  (mm.c:660), reads `pa_v[0]`/`pa_v[1]`, and unlinks the page by
  fixing its neighbors' pointers; updates `pa_head` if the page was the
  head (mm.c:656-677).
- **Does NOT modify `ppage_list[i]` and does NOT touch `free_pages`.**
  Accounting is performed by the subsequent `phys_reference` call in
  `alloc_phys` (mm.c:880, 763-768).
- Safe across multiple free-list nodes: each page is unlinked
  independently via its own next/prev, so a physically-contiguous range
  whose pages are scattered in the list is handled correctly.

### 2.6 `mark_phys_wired` (mm.c:680-696)

- **Panics if any page is not FREE** (mm.c:689).
- Sets `ppage_list[i] = PAGE_WIRED`, `free_pages--`, `wired_pages++`
  (mm.c:691-693).
- **Does NOT unlink the page from the free list.** The page's next/prev
  pointers and its neighbors' pointers are left intact.

**Safety consequence (source-proven):** calling `mark_phys_wired` on a
free page leaves that page **still linked in the free list** while
`ppage_list[i]` now says `PAGE_WIRED`. If `ppage_alloc` later pops it
(it pops from `pa_head` without checking `ppage_list`), it `bzero`s the
page and `phys_reference` **skips** it (the `!= PAGE_WIRED` guard at
mm.c:763), so the page is handed out as "allocated" while still marked
WIRED. A later `phys_dereference` on it decrements `0xffffff` to
`0xfffffe` (mm.c:785) — allocator corruption. **`mark_phys_wired`
alone is therefore NOT a safe reservation primitive.**

### 2.7 `phys_force_free` (mm.c:731-750)

- For each page: if `PAGE_WIRED`, `wired_pages--` (mm.c:741-743); if
  not `PAGE_FREE`, `phys_page_was_freed` (push to list, `free_pages++`)
  (mm.c:744-746); then `ppage_list[i] = PAGE_FREE` (mm.c:747).
- **Unsafe on allocated (refcount > 0) pages:** it pushes the page onto
  the free list and marks it FREE **without decrementing its refcount or
  fixing up other holders**, so any other reference becomes dangling.
  It also overwrites the page's first 16 bytes with list pointers,
  destroying whatever data a wired/allocated page held.
- Safe **only** on ranges that are entirely `PAGE_FREE` (no-op) or
  entirely `PAGE_WIRED` (carving out wired pages).

### 2.8 `alloc_init` (mm.c:794-833)

- `ppages = memSize >> 14` (mm.c:798).
- `ppage_list` is allocated at `early_heap` (a cacheable-view VA),
  occupying `4 * ppages` bytes (mm.c:813-814).
- All pages start `PAGE_WIRED` (mm.c:816-819).
- `alloc_static_base = (kCacheableView - 0x800000000 + gBootArgs->topOfKernelData) & ~0x3fff`
  (mm.c:821). Since `kCacheableView = 0x400000000` (pongo.h:344) and
  `phystokv(pa) = pa - 0x800000000 + kCacheableView` (mm.c:893-894),
  this equals `phystokv(topOfKernelData) & ~0x3fff`. **`topOfKernelData`
  is a PHYSICAL address** (pongo.h:84: "Highest physical address used in
  kernel data area").
- `alloc_static_end = 0x417fe0000` (VA) (mm.c:822); hard-capped at
  `alloc_static_base + 64 MB` (mm.c:823-827).
- `alloc_heap_base = align_up(early_heap, 0x4000)` (VA) (mm.c:829).
- `alloc_heap_end = align_up(phystokv(physBase) + memSize, 0x4000) - 1 MB`
  (VA) (mm.c:830).
- `phys_force_free(vatophys_static(alloc_heap_base), alloc_heap_end - alloc_heap_base)`
  (mm.c:832) — **this is the only place the free heap is created.**

**Post-init layout (source-proven structure, runtime boundaries):**
- `[physBase, topOfKernelData)` — PongoOS text/data: **WIRED**.
- ppage_list + early-heap structures (between `early_heap_base` and
  `alloc_heap_base`): **WIRED**. `early_heap_base = phystokv(pongo_base)
  + pongo_size` (aligned) (mm.c:323).
- `[alloc_static_base, alloc_static_end)` — `alloc_static` bump region
  (VA); physically `[topOfKernelData, min(topOfKernelData+64MB,
  0x817fe0000))`.
- `[alloc_heap_base, alloc_heap_end)` — **FREE** (on the free list);
  physically `[alloc_heap_base_PA, DRAM_end - 1MB)`.
- `[DRAM_end - 1MB, DRAM_end)` — **WIRED** (reserved by the `-1MB` in
  mm.c:830).

### 2.9 `alloc_static` (mm.c:834-843)

Bump allocator from `alloc_static_current`; advances
`gBootArgs->topOfKernelData` (mm.c:841). Returns a cacheable-view VA.
Used for persistent allocations (e.g., XNU ramdisk, xnu.c:1304).

### 2.10 Address conversion (mm.c:893-902)

- `phystokv(pa) = pa - 0x800000000 + kCacheableView` → `VA = PA - 0x400000000`.
- `vatophys_static(va) = va - kCacheableView + 0x800000000` → `PA = VA + 0x400000000`;
  panics unless `va ∈ [kCacheableView, kCacheableView + 0x100000000)`
  (mm.c:898-899).

### 2.11 Exported primitives (modload.c:1461-1466, 1547-1548)

**Exported to modules:** `phys_reference`, `phys_dereference`,
`phys_force_free`, `mark_phys_wired`, `phys_get_entry`, `phys_set_entry`,
`alloc_contig`, `alloc_phys`, `alloc_static`, `free_contig`,
`map_physical_range`.

**NOT exported:** `phys_unlink_contiguous` (defined mm.c:643; absent
from the `PONGO_EXPORT` list and not declared in `pongo.h`).

**This is the central finding:** the only function that safely removes
pages from the free list is not callable by a module. A module can only
reach the allocator through the exported set above.

### 2.12 Minor observation (not central)

`phys_get_entry`/`phys_set_entry`/`phys_unlink_contiguous`/
`mark_phys_wired`/`phys_force_free`/`phys_reference`/`phys_dereference`
all bound-check with `if (i > ppages)` (e.g., mm.c:632, 657, 690,
740, 762, 783) — an off-by-one that permits index `== ppages`, one
past the last valid entry. Latent, but noted for completeness.

---

## 3. Memory-range conflict matrix

All boundaries below are **runtime values** (read from `gBootArgs` or
derived from PongoOS's own load address). No absolute address is
source-proven safe.

| Region | Physical extent | State | Source |
|---|---|---|---|
| PongoOS text/data | `[physBase, topOfKernelData)` | WIRED | mm.c:816-819, 821 |
| ppage_list | `[early_heap_base_PA, +4*ppages)` | WIRED | mm.c:813-814, 323 |
| early-heap structs | `[ppage_list_end, alloc_heap_base_PA)` | WIRED | mm.c:829 |
| static region | `[topOfKernelData, min(+64MB, 0x817fe0000))` | alloc_static (bump) | mm.c:821-827 |
| **free heap** | `[alloc_heap_base_PA, DRAM_end - 1MB)` | **FREE** | mm.c:829-832 |
| reserved top | `[DRAM_end - 1MB, DRAM_end)` | WIRED | mm.c:830 |
| upload buffer | 1 MB (up to 128 MB) inside free heap, at first free run | **allocated** at jump time | usbloader.c:32,95; entry.c:232,311 |
| framebuffer | `[Video.v_baseAddr, +fbsize)` — may overlap DRAM | mapped / in use | pongo.h:70; fb.c:217-232 |
| framebuffer copy | `alloc_contig(fbsize)` inside free heap | allocated | fb.c:232 |
| device / I/O | below `physBase` | not managed | mm.c:645, 753 |

**Candidate `0x801000000` is NOT established safe.** It is a candidate
above an *assumed* `topOfKernelData` (~0x800400000), but:
1. The actual `topOfKernelData`, `physBase`, `memSize`, `pongo_base`,
   and `pongo_size` are runtime values.
2. The upload buffer (1 MB) and the framebuffer-copy buffer are
   allocated from the **low end** of the free heap (first free run,
   `alloc_phys` scan mm.c:862-876). Early `alloc_contig` callers
   (USB DMA `synopsys_otg.c:1808`, SEP `sep.c:581,700`, AES
   `aes.c:122`, VM space table `mm.c:585`, kernel stacks
   `task.c:415`) further consume the low end before `bootr` runs.
   `0x801000000` plausibly falls inside this early-allocation zone and
   **could overlap the active upload buffer or another allocation**.
3. The framebuffer physical range (`Video.v_baseAddr`) is unknown and
   may overlap DRAM.

**Conclusion:** no fixed physical address can be declared safe from
source alone. Safety is a runtime property that requires reading
`gBootArgs` and the live free-list state on the device.

---

## 4. Reservation strategy comparison

### Strategy A — Reserve a fixed range by removing its free pages

- **Prerequisites:** every page in `[pa, pa+size)` must be `PAGE_FREE`
  at reservation time; the range must be page-aligned and within
  `[physBase, physBase+memSize)`; it must not overlap the upload
  buffer, framebuffer, PongoOS data, ppage_list, static region, or the
  reserved top 1 MB.
- **Mechanism (the only correct one):** `phys_unlink_contiguous(pa,
  size)` **then** `mark_phys_wired(pa, size)`. Unlink removes the
  pages from the free list (leaving `ppage_list[i] == FREE`); wire then
  sets them to `PAGE_WIRED` with correct accounting
  (`free_pages--`, `wired_pages++`).
- **Failure modes:**
  - Either primitive **panics** if any page is not FREE (mm.c:660, 689)
    — a safe fail-closed, but a device crash.
  - `phys_unlink_contiguous` is **NOT exported** (§2.11), so a module
    cannot perform the unlink. `mark_phys_wired` alone is unsafe
    (§2.6). `phys_force_free` is unsafe on allocated pages (§2.7).
  - `entry.c:311` **hardcodes** the jump target to
    `loader_xfer_recv_data`'s PA. Even with a reserved range, the
    `BOOT_FLAG_RAW` path would still jump to the upload buffer, not the
    fixed address — unless `entry.c` is modified or a position-
    independent trampoline is uploaded to the buffer that jumps onward
    to `load_addr + entry_offset`.
- **Source-proven safe? NO.** The mechanism is half-exported (the
  essential unlink is missing), and no fixed address is proven free.

### Strategy B — Load into a dynamically allocated buffer + relocate

- **Prerequisites:** a contiguous buffer of `image_size + bss_size`
  must be allocatable; the kernel must be **position-independent** or
  relocated to the buffer's runtime address.
- **Mechanism:** `alloc_contig(image_size + bss_size)`, copy the image,
  zero the BSS, jump to the buffer PA. **This is exactly the existing
  `BOOT_FLAG_RAW` path** (entry.c:311), which loads into
  `loader_xfer_recv_data` (a 1 MB `alloc_contig` buffer, usbloader.c:95,
  resizable to 128 MB via `resize_loader_xfer_data`, usbloader.c:66-77).
- **Failure modes:** OOM panic (mm.c:877); a non-PIC kernel linked for a
  fixed address has broken absolute references when loaded elsewhere;
  `entry.c:311` jumps to the buffer **start**, so a nonzero
  `entry_offset` requires a trampoline or a modified jump.
- **Source-proven safe? YES, for the mechanism** — it is the existing,
  exercised path. It requires a relocatable (PIC) kernel.

### Strategy C — Reuse an existing PongoOS loading path

- `bootr` / `BOOT_FLAG_RAW` (shell/main.c:40-48, 257; entry.c:309-311)
  is the only raw-image path; it loads at the **upload buffer PA**
  (dynamic), not a fixed address.
- `modload` / `modload_macho` (modload_macho.c:73) loads Mach-O modules
  at dynamically allocated addresses with `linear_kvm_alloc` for virtual
  mapping — also not a fixed physical address.
- **Source-proven safe? YES** (existing paths), but **neither provides
  fixed-physical-address loading.**

---

## 5. Hypothetical reservation routine — proof outline (NOT implemented)

If a future PongoOS modification added a safe `phys_reserve_range(pa,
size)`, it would need to prove:

- **Partial overlap:** reject the whole request if any page in
  `[pa, pa+size)` is not `PAGE_FREE`. No partial reservation.
- **Already-allocated pages:** reject (fail-closed). Never
  `phys_force_free` an allocated page — it corrupts refcounts and
  destroys page content (§2.7).
- **Rollback:** pre-validate **all** pages in a first pass before
  unlinking any (two-pass), so a mid-range non-free page aborts before
  any mutation. If unlinking were interrupted, already-unlinked pages
  would need re-linking via `phys_page_was_freed` semantics and
  `ppage_list` restoration.
- **Free-list integrity:** unlinking must fix each page's neighbors'
  `next`/`prev` and update `pa_head` when the head is in range
  (as `phys_unlink_contiguous` does, mm.c:656-677).
- **Accounting:** `free_pages -= npages`, `wired_pages += npages`
  (if wiring). `phys_unlink_contiguous` does not touch accounting;
  `mark_phys_wired` does — the pair is jointly correct.
- **Atomicity:** hold interrupts disabled across validate+unlink+wire as
  a single critical section. `phys_unlink_contiguous` and
  `mark_phys_wired` each disable interrupts internally but **not across
  both calls**, so a concurrent allocation could interleave.

This outline is provided for audit completeness only; no routine is
written in this phase.

---

## 6. Unresolved questions requiring runtime evidence

1. `gBootArgs->physBase` — assumed 0x800000000 (A12 typical); unverified.
2. `gBootArgs->memSize` — assumed 1 GB; unverified.
3. `gBootArgs->topOfKernelData` — determines PongoOS-data end and the
   static-region start; unverified.
4. `pongo_base` / `pongo_size` — determine `early_heap_base`, hence the
   ppage_list location and the free-heap start; unverified.
5. Upload-buffer physical address at `bootr` time — depends on the full
   allocation history before `bootr`; unverified.
6. Framebuffer physical range (`Video.v_baseAddr`, `fbsize`) — may
   overlap DRAM; unverified.
7. Free-heap high-water mark at `bootr` time — all `alloc_contig` /
   `alloc_phys` calls before `bootr`; unverified.
8. Whether the MyOS M2 kernel can be built position-independent (PIC)
   for AArch64 — determines whether Strategy B works without relocation.

---

## 7. GO / NO-GO decision

- **NO-GO** for implementing a loader in this phase (task constraint:
  research and proof only).
- **NO-GO for a fixed-physical-address loader on stock PongoOS.**
  Source-proven reasons:
  1. The only safe free-list removal primitive, `phys_unlink_contiguous`,
     is **not exported** to modules (§2.11).
  2. The exported `mark_phys_wired` is unsafe as a standalone
     reservation (leaves pages on the free list → corruption, §2.6);
     `phys_force_free` is unsafe on allocated pages (§2.7).
  3. `entry.c:311` hardcodes the `BOOT_FLAG_RAW` jump target to the
     upload-buffer PA, so a fixed address is not reachable without
     modifying PongoOS or uploading a PIC trampoline.
  4. No fixed physical address is source-proven free (§3).
- **GO (conditional) for a dynamic-buffer loader (Strategy B) on stock
  PongoOS**, because it reuses the proven `BOOT_FLAG_RAW` path —
  **provided** the MyOS M2 kernel is position-independent (or accepts
  the runtime upload-buffer address as its load address). The canonical
  format's fixed `load_addr` is incompatible with Strategy B unless the
  kernel is PIC.

---

## 8. Next smallest justified step

Determine whether the MyOS M2 kernel can be built **position-independent
(PIC)** for AArch64 (unresolved question 8).

- If **yes**: Strategy B is viable on stock PongoOS with **no PongoOS
  modification** — upload the PIC image via the existing `bootr` path
  and let it run from the upload buffer. This is the only source-proven-
  safe loading strategy.
- If **no**: a PongoOS modification is a hard prerequisite for any
  fixed-address loader — export `phys_unlink_contiguous` (or add a
  `phys_reserve_range`), and make the `entry.c:311` jump target
  configurable. That modification must be separately audited before any
  implementation.

---

## 9. Files referenced (read-only)

- `pongoos-src/src/kernel/mm.c:615-902` — allocator core.
- `pongoos-src/src/kernel/pongo.h:69-104, 125-133, 265-271, 340-345, 392-394, 451-454` — boot_args, flags, exports, kCacheableView.
- `pongoos-src/src/kernel/entry.c:205-326` — boot sequence and RAW handoff.
- `pongoos-src/src/boot/jump_to_image.S:27-55` — handoff ABI.
- `pongoos-src/src/shell/main.c:33-48, 257` — `bootr` command.
- `pongoos-src/src/shell/usbloader.c:32-99` — upload buffer.
- `pongoos-src/src/drivers/framebuffer/fb.c:216-232` — framebuffer.
- `pongoos-src/src/dynamic/modload.c:1460-1466, 1547-1549` — PONGO_EXPORT list.
- `include/myos_image.h` — canonical M2 image format (unchanged).
