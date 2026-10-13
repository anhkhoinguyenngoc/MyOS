# MyOS M-P2.4 — PongoOS Runtime Symbol & Probe-Buffer Address Resolution

Date: 2026-10-11
Run on: Arch Linux main PC (`/home/khoi/Projects/myos`), read-only audit
Physical iPhone connection: **not used, not required, not claimed**
Pinned PongoOS: `4c9b7541629234147fcc778f0ce4162482aaccef`
Pinned artifact parsed: `pongoos-src/build/Pongo`

**Final recommendation: `STOP_AT_EVIDENCE` for the runtime address.** The
PongoOS **symbol table is usable and reproducible**: image base, entry, and
every named link-time symbol resolve exactly from the artifact. The **runtime
upload-buffer address is not resolvable from any artifact** — it is a
heap allocation whose value depends on device runtime state. No post-execution
report channel exists in the current architecture. The smallest justified next
step is a *read-only runtime datum* (the runtime value of
`loader_xfer_recv_data`), not a physical payload run.

This audit does **not** claim that the probe executed, that MyOS booted, or
that any runtime property was observed.

## 1. Executive conclusion

1. The only artifact carrying a usable symbol table is
   `pongoos-src/build/Pongo`: a **Mach-O 64-bit arm64 preload executable**
   (`MH_PRELOAD`, `cputype 0x0100000c`), **not stripped**, `LC_SYMTAB` with
   **2361 symbols**. It parses with a minimal stdlib parser; no Apple
   toolchain is needed.
2. Static facts resolve exactly and reproducibly:
   - image base (`__TEXT.vmaddr`) = `0x100000000`;
   - entry point (`LC_UNIXTHREAD` `pc`) = `0x100000000`;
   - every named symbol (loader, allocator, slide, boot-args) resolves to a
     fixed link-time VM address (Section 4).
3. The **runtime slide cannot be derived from artifacts.** `pongo_base =
   (uint64_t)&start` and `gPongoSlide = 0x100000000 - pongo_base` are runtime
   values; the artifact only supplies the link-time side. A runtime (slid)
   address therefore requires a device datum (Section 5).
4. The **probe's upload-buffer address cannot be derived from artifacts.**
   `usbloader_init()` sets `loader_xfer_recv_data = alloc_contig(UPLOADSZ)`
   (usbloader.c:94-99); `alloc_contig` → `alloc_phys` returns
   `(page_index << 14) + gBootArgs->physBase` from a runtime free-list scan
   (mm.c:844-889). It is **not a symbol**, and no symbol value equals it.
   `loader_xfer_recv_data` is a *pointer variable* (in `__DATA,__bss`), not
   the buffer (Section 3, Section 4).
5. The address model is fully reproducible **as a formula** and is exercised
   offline by `tools/host/pongo_addr_resolve.py` + `tests/test_pongo_addr_resolve.py`
   (18 tests, all pass; `make pongo-addr-check` exit 0). Synthetic fixtures
   test the parser only and are never presented as real addresses (Section 7).
6. **No reliable post-execution report channel exists.** `usb_teardown()` runs
   at entry.c:232, *before* the raw jump at entry.c:309-312, so the host USB
   link is gone before the probe runs. The probe parks (`b park`), so the CPU
   never returns. The probe does write a report block at buffer+0x200 with the
   MMU off (directly into DRAM), and the upload buffer is not freed on this
   path — but nothing reads it back. Full register readback is **NOT** claimed
   (Section 8).
7. **Exact missing datum:** the runtime *value* of `loader_xfer_recv_data`
   (the buffer cacheable VA), or equivalently the buffer PA, obtained from a
   debugger/monitor at the PongoOS shell before `bootr`. Without it, the
   physical test cannot place or read the probe (Section 9-10).

## 2. Artifacts and symbol usability

| Artifact | Type | Symbols? | Use |
|---|---|---|---|
| `pongoos-src/build/Pongo` | Mach-O 64 arm64 preload exe, `MH_PRELOAD` | **Yes** — `LC_SYMTAB`, 2361 nlist_64 entries | Symbol + image base + entry resolution |
| `pongoos-src/build/Pongo.bin` | raw flat image (675936 B) | No symbol table | Boot image only |
| `pongoos-src/build/PongoConsolidated.bin` | raw flat image | No | Boot image only |
| `pongoos-src/build/checkra1n-kpf-pongo` | Mach-O arm64 kext bundle | (separate) | Not the primary image |
| `build/pongo_probe.bin` | raw AArch64 image (608 B) | No | The payload under test |

`file` misidentifies `Pongo.bin`/`PongoConsolidated.bin` as "DOS executable
(COM)" because they are headerless flat images; this is not an error. `nm`
cannot read the Mach-O on this host, so symbol resolution is done by the
stdlib parser in `tools/host/pongo_addr_resolve.py`.

## 3. The address model (four distinct representations)

Source anchors (pinned checkout):

- **Cacheable view constant:** `kCacheableView = 0x400000000ULL`
  (`src/kernel/pongo.h:341-345`). *Caveat:* the checkra1n KPF **userspace
  test** build defines `OVERRIDE_CACHEABLE_VIEW=0x800000000ULL`
  (`checkra1n/kpf-test/Makefile:17`); that is a test-only override and does
  **not** apply to the A8/checkra1n `Pongo` build. The artifact here is the
  default build, so `0x400000000` is the operative constant.
- **PA → cacheable VA:** `phystokv(paddr) = paddr - 0x800000000 + kCacheableView`
  (`src/kernel/mm.c:893-895`).
- **cacheable VA → PA alias:** `vatophys_static(kva) = kva - kCacheableView + 0x800000000`
  (`src/kernel/mm.c:896-902`). Round-trip identity holds.
- **Contiguous allocation:** `alloc_contig(size) = phystokv(alloc_phys(size))`
  (`src/kernel/mm.c:887-889`); `alloc_phys` returns `(i << 14) + gBootArgs->physBase`
  for a runtime free-list run (`src/kernel/mm.c:844-882`).
- **Runtime slide:** `pongo_base = (uint64_t)&start`
  (`src/kernel/mm.c:303`); `gPongoSlide = 0x100000000ULL - pongo_base`
  (`src/kernel/mm.c:327`).
- **Raw handoff (x30):** `loader_xfer_recv_data - kCacheableView + 0x800000000`
  (`src/kernel/entry.c:309-312`), which by the formulas above **equals the
  buffer PA**. `jump_to_image.S:42-55` sets `x30 = arg0` (image), `x0 = arg1`
  (`gBootArgs`), `x8 = arg2` (`gEntryPoint`), `x1..x3 = 0`, barriers, `ret`.
- **Upload buffer:** `UPLOADSZ = (1024*1024)` (usbloader.c:32);
  `uint8_t * loader_xfer_recv_data;` (usbloader.c:35);
  `loader_xfer_recv_data = alloc_contig(UPLOADSZ)` (usbloader.c:94-99).

For a symbol with link-time address `L`:

| Representation | Value | Derivable statically? |
|---|---|---|
| image-relative | `L - image_base` | **Yes** (artifact) |
| link-time VM | `L` | **Yes** (artifact) |
| runtime cacheable VA | `L - gPongoSlide` = `L - image_base + pongo_base` | **No** (needs `pongo_base`/slide) |
| runtime physical alias | `VA - kCacheableView + 0x800000000` | **No** (same dependency) |

For the **upload buffer** there is no `L` at all — it is not in the image:

| Representation | Value | Derivable statically? |
|---|---|---|
| image-relative | n/a (external heap object) | n/a |
| link-time VM | n/a | n/a |
| runtime cacheable VA | runtime value of `loader_xfer_recv_data` | **No** (needs runtime read) |
| physical PA | `(page_index << 14) + gBootArgs->physBase` | **No** (needs free-list + boot args) |
| handoff alias x30 | buffer VA `- kCacheableView + 0x800000000` (= buffer PA) | **No** (follows from VA) |
| report physical | buffer PA `+ 0x200` | **No** (follows from PA) |

## 4. Resolved symbol evidence (link-time, from the artifact)

`make`-independent, from `python3 tools/host/pongo_addr_resolve.py`:

| Symbol | Link-time VM | Image-relative | Section | Meaning |
|---|---|---|---|---|
| (entry, `LC_UNIXTHREAD.pc`) | `0x100000000` | `0x0` | __TEXT,__text | `&start` (used for `pongo_base`) |
| `_pongo_entry` | `0x1000624ac` | `0x624ac` | __TEXT,__text | kernel entry / raw-jump site |
| `_usbloader_init` | `0x10006d240` | `0x6d240` | __TEXT,__text | allocates the upload buffer |
| `_resize_loader_xfer_data` | `0x10006cf50` | `0x6cf50` | __TEXT,__text | buffer resize |
| `_reallocate_loader_xfer_data` | `0x10006d064` | `0x6d064` | __TEXT,__text | buffer realloc |
| `_alloc_phys` | `0x100066bc0` | `0x66bc0` | __TEXT,__text | physical allocator |
| `_alloc_contig` | `0x100066f5c` | `0x66f5c` | __TEXT,__text | contiguous allocator |
| `_free_contig` | `0x1000673f4` | `0x673f4` | __TEXT,__text | — |
| `_phystokv` | `0x100065818` | `0x65818` | __TEXT,__text | PA→VA |
| `_vatophys_static` | `0x1000659f4` | `0x659f4` | __TEXT,__text | VA→PA |
| `_alloc_static` | `0x100067374` | `0x67374` | __TEXT,__text | persistent allocator |
| **`_loader_xfer_recv_data`** | **`0x1000a81f0`** | **`0xa81f0`** | **__DATA,__bss** | **pointer variable (the buffer's VA), NOT the buffer** |
| `_loader_xfer_recv_count` | `0x1000a81f8` | `0xa81f8` | __DATA,__bss | note: **cleared** on `bootr` |
| `_loader_xfer_recv_size` | `0x1000a8200` | `0xa8200` | __DATA,__bss | retained on `bootr` |
| `_loader_xfer_size` | `0x1000a8204` | `0xa8204` | __DATA,__bss | — |
| `_gPongoSlide` | `0x1000a56e8` | `0xa56e8` | __DATA,__bss | runtime slide variable |
| `_gBootArgs` | `0x1000a55d8` | `0xa55d8` | __DATA,__bss | boot args pointer |
| `_gEntryPoint` | `0x1000a5600` | `0xa5600` | __DATA,__bss | — |
| `_gBootFlag` | `0x1000a55b0` | `0xa55b0` | __DATA,__bss | `BOOT_FLAG_RAW` selector |
| `_ppage_list` | `0x1000a5740` | `0xa5740` | __DATA,__bss | allocator page map |
| `_ppages` | `0x1000a5728` | `0xa5728` | __DATA,__bss | page count |

**Critical distinction:** `_loader_xfer_recv_data` is a *variable* of type
`uint8_t *` in `__bss` at link VA `0x1000a81f0`. Its **address** is static; its
**value** (the buffer's cacheable VA) is written at runtime by
`usbloader_init()`. The buffer address is that *value*, not the variable's
address.

## 5. What is reproducible vs not

**Reproducible from artifacts (yes):**
- image base `0x100000000`; entry `0x100000000`;
- any symbol's link-time VM address and image-relative offset;
- the VA↔PA↔handoff-alias formulas and constants;
- the fact that the A8 build uses `kCacheableView = 0x400000000`.

**Not reproducible from artifacts (needs runtime data):**
- `pongo_base` / `gPongoSlide` → any runtime (slid) address. `pongo_base` is
  the runtime load VA of the image (`&start`), set by the boot chain
  (checkra1n/Pongo loader), not recorded in the artifact.
- the upload buffer VA/PA → `alloc_contig` result (free-list + `gBootArgs`).
- the buffer's content (and thus the probe's later report at buffer+0x200).

**Error to avoid (now corrected):** an earlier probe comment
(`boot/aarch64/pongo_probe.S:66-68`) suggests "`nm` on the built Pongo Mach-O
for `loader_xfer_recv_data` + runtime slide resolution" as Tier-0 buffer-PA
discovery. `nm` alone yields only the *variable's* address; the *value* still
requires a runtime memory read, and the slide requires a runtime datum. This
audit refines that note: the artifact gives the address to *read*, not the
buffer address itself. (The probe source is left unchanged — no source-level
defect in the probe is proven; this is a documentation refinement.)

## 6. Reproducible tool

`tools/host/pongo_addr_resolve.py` (host-side, Python stdlib only):

```
# static artifact facts + key symbols
python3 tools/host/pongo_addr_resolve.py

# one symbol (link-time only; runtime VA reported UNRESOLVED without a datum)
python3 tools/host/pongo_addr_resolve.py --symbol _loader_xfer_recv_data

# with a runtime datum the runtime VA can be computed
python3 tools/host/pongo_addr_resolve.py --symbol _pongo_entry --slide 0x1000000

# buffer: REFUSES to invent an address (exit 3) without a runtime datum
python3 tools/host/pongo_addr_resolve.py --buffer-pa 0x812340000   # example datum

# verify the model still matches the pinned source
python3 tools/host/pongo_addr_resolve.py --verify-source
```

The tool enforces the central invariant: **a symbol name never yields the
buffer address.** `resolve_buffer()` returns `UNRESOLVED_NEEDS_RUNTIME_DATUM`
unless a runtime PA or VA is supplied, and rejects contradictory inputs with
`INCONSISTENT_RUNTIME_DATUM`.

## 7. Offline tests (synthetic fixtures) and results

`tests/test_pongo_addr_resolve.py` — 18 tests, all pass. Synthetic fixtures
build a minimal Mach-O in memory with **invented** symbol names/values
(`_synth_fn`, `_synth_ptr`, `_synth_bss`); they test the **parser only**.

- Parser: synthetic symbol round-trip; missing `LC_SYMTAB` rejected; bad magic
  rejected; synthetic section lookup.
- Address model: `phystokv`/`vatophys_static` round-trip; known pair
  (`0x800000000`↔`0x400000000`); handoff alias == PA; slope-form equivalence
  (`--slide` vs `--pongo-base`); KPF override respected but default pinned to
  `0x400000000`.
- Guards: no datum ⇒ `UNRESOLVED`; PA/VA consistent ⇒ `RESOLVED`; contradictory
  ⇒ `INCONSISTENT`.
- Source assumptions: the 8 pinned regex anchors are all present.
- Real artifact (parse only): image base `0x100000000`, entry `0x100000000`,
  pinned symbol offsets, and `_loader_xfer_recv_data` in `__DATA,__bss`.

**Non-claim:** the synthetic tests are **not** evidence of any real runtime
address, and the real-artifact tests only assert *link-time* facts. No test
claims the buffer address, the slide, or a successful run.

`make pongo-addr-check` runs the source-assumption check plus the suite and
exits 0.

## 8. Post-execution report channel audit

The probe (`boot/aarch64/pongo_probe.S`) records, at **file offset `0x200`**
(= buffer PA `+ 0x200` when running with the MMU off): magic `MP1PRB01`,
version/size, `x30` (image base), `x0` (`gBootArgs`), `x8` (`gEntryPoint`),
`CurrentEL`, and (EL1 only) `SCTLR_EL1`, `MIDR_EL1`, two `CNTPCT_EL0` samples,
`CNTFRQ_EL0`. It then parks (`b park`).

Read-back is impossible in the current architecture:

1. **USB is torn down before the jump.** `usb_teardown()` is called at
   `entry.c:232`; the raw jump is at `entry.c:309-312`. `usb_teardown()` only
   writes two USB registers (`synopsys_otg.c:1866-1870`); the host link is gone
   for the whole probe execution.
2. **The CPU never returns.** The probe's terminal state is a self-branch
   park (`b park`); `jump_to_image.S` `ret` is terminal. No shell, no command
   prompt, no callback.
3. **The buffer is not freed on this path.** `pongo_boot_raw()` clears
   `loader_xfer_recv_count` only (`main.c:40-48`); `loader_xfer_recv_data` and
   `loader_xfer_recv_size` are retained. So the report bytes physically persist
   in DRAM at buffer PA `+ 0x200`. This is *persistence*, not a *channel*.
4. **No alternate channel exists here:** no JTAG/monitor/GDB stub is present
   in this workspace, and `D2_RUNBOOK.md:81`'s only post-jump observation is the
   `05ac:4141` USB device *disappearing* under `lsusb` — a 1-bit "jump
   happened" signal, not register readback. Reboot forensics is unproven and
   risky: `ppage_alloc` bzeroes handed-out pages (`mm.c:59`), so a later Pongo
   run may zero the very page unless it is re-reserved.

**Conclusion:** there is **no reliable post-execution report channel**. The
Tier-1 `lsusb` disappearance is the only achievable post-execution signal.
Full register readback is **not** claimed.

## 9. Exact missing artifact / runtime datum

To resolve the probe's runtime buffer address, exactly one runtime datum is
required (in addition to the artifact):

- the **runtime value of `loader_xfer_recv_data`** (the buffer cacheable VA),
  or equivalently the **buffer PA**;

and to place/read the buffer by *symbol* one also needs the **runtime slide**
`gPongoSlide` (equivalently `pongo_base`). Neither is present in any local
artifact, log, or source. Until obtained, the runtime address is
`UNRESOLVED_NEEDS_RUNTIME_DATUM` — not guessed.

## 10. Smallest next step toward a justified physical test

1. **Obtain the runtime datum read-only, at the PongoOS shell before `bootr`**,
   using Pongo's own memory-read commands (no payload execution, no `bootr`):
   with the slide known (or by reading `_loader_xfer_recv_data`'s runtime
   address and dereferencing), read the 8 bytes at the runtime address of
   `_loader_xfer_recv_data` to obtain the buffer VA. This is the *only* missing
   input.
2. Feed that datum to `tools/host/pongo_addr_resolve.py --buffer-va <VA>`
   (offline) to derive the buffer PA, the handoff alias (x30 = PA), and the
   report physical address (PA + 0x200). The tool validates consistency.
3. Only then is a physical probe test *justifiable*: the run would need (a) a
   host-side shell client to upload the 608-byte probe and issue `bootr`, and
   (b) a defined readback path for the report — which does **not** exist today
   (Section 8). Absent (b), the physical test can at best observe the Tier-1
   USB-disappearance signal, not register values.

No step above was performed; this audit is offline and read-only.

## 11. Preservation and verification

- `build/pongo_probe.bin` unchanged: 608 bytes, sha256
  `c90162af659a3eb0a7eda9954dee07aaa96e52c3407434320d3ed0cc4c1d612a`.
- No device interaction; no DFU/PongoOS/`bootr`/upload/probe execution.
- No commit, push, reset, clean, or stash. HEAD `61387fb6fb256fc274454fee17b327a6d332443a`.
- `git diff --check`: clean (exit 0).
- New files (untracked): `tools/host/pongo_addr_resolve.py`,
  `tests/test_pongo_addr_resolve.py`; `Makefile` gains the `pongo-addr-check`
  target only.
- Passed: `python3 tools/host/pongo_addr_resolve.py --verify-source` (PASS),
  `python3 tests/test_pongo_addr_resolve.py` (18/18), `make pongo-addr-check` (exit 0).
