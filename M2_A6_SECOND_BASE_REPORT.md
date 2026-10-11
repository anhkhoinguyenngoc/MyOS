# M2-A6 — Prove AArch64 PIE Kernel Execution at a Second Base

**Status:** host/QEMU experiment only, complete. No device was connected or touched; no
iPhone compatibility is claimed or implied. `pongoos-src/` (revision
`4c9b7541629234147fcc778f0ce4162482aaccef`) was not modified. Nothing was committed or
pushed. All experimental code and outputs live under `build/audit/` and `/tmp`.

**Result in one line:** the PIE kernel, relocated by a host-side harness, prints the
complete `MYOS_KERNEL_PASS` marker **at its original base `0x40080000` (Experiment A)**
and **at a second base `0x44000000` (Experiment B)**, with all 35 relocations applied
correctly. Getting there required root-causing and fixing (in an isolated copy only) a
latent canonical-source bug that the PIE layout exposed — reported in §4.

---

## 0. Tool versions

| Tool | Version |
|---|---|
| `qemu-system-aarch64` | 11.1.1 |
| `aarch64-linux-gnu-gcc` | GCC 16.1.0 |
| `aarch64-linux-gnu-ld` | GNU Binutils 2.47 |
| `python3` | 3.14.7 (harness, stdlib only) |
| `make` | GNU Make 4.4.1 |

---

## 1. Reading the existing audit first (`M2_A5_PIC_AUDIT.md`)

All four confirmation points were re-verified against the artifacts, not taken from the
report text:

1. **Exact temporary PIE build command and flags** (A5 §3, re-run confirmed): the same
   `KERNEL_CROSSFLAGS` as the canonical build with `-fPIC` replacing `-fno-pic
   -fno-pie`, then:

   ```
   aarch64-linux-gnu-ld --hash-style=sysv -T kernel_pic.ld -pie --no-dynamic-linker \
       -z noexecstack --build-id=none -o build/audit/pic/myos_kernel_pic_dyn.elf \
       {start,boot_diag,vectors,uart_pl011,main}.o
   ```

   where `kernel_pic.ld` is an audit-only copy of `boot/aarch64/kernel.ld` with a single
   change: `. = 0x40080000;` → `. = 0x0;`. (`--hash-style=sysv` is required because the
   script's `/DISCARD/ : { *(.gnu*) }` removes `.gnu.hash`, which `-pie` otherwise wants.)

2. **ELF type, load segments, entry semantics, relocation records** (re-verified with
   `readelf`):

   ```
   Type: DYN    Entry point: 0x0    e_phnum: 2
   LOAD  off=0x010000 vaddr=0x000000 filesz=memsz=0x002ae0  R E  align 0x10000
   LOAD  off=0x020000 vaddr=0x010000 filesz=memsz=0x021278  RW  align 0x10000
   .rela.dyn: 35 entries (sh_offset 0x12798, size 0x348 = 35 × 24)
   ```

   *Entry semantics:* `e_entry = 0` lies inside the executable PT_LOAD (`vaddr 0`);
   runtime entry = `base + (e_entry − min_vaddr)`. *BSS:* `filesz == memsz` on both
   segments (BSS is file-backed, as in the canonical build — A5 §2.4 confirmed), so no
   external zero-fill is strictly required; the harness zeroes the whole mapped span
   regardless.

3. **The 35 `R_AARCH64_RELATIVE` records** — re-verified count and attributed content:
   4 slots in `uart_pl011_ops` (function pointers), 12 live entries of
   `boot_stage_names`, 16 slots of `subsys_names`+`type_names`, and 3 `_GLOBAL_OFFSET_TABLE_`
   slots = **32 data pointers + 3 GOT**. Every record has symbol index 0; the rule is
   `*(u64*)(base + r_offset) = base + r_addend` (min_vaddr = 0).

4. **Startup/linker/BSS/console compatibility with a runtime base** — code is fully
   PC-relative (A5 §1.3 re-confirmed: 0 absolute immediates, 0 literal loads), VBAR is
   installed via `adr` so the vector table moves with the image (base must be 2 KiB
   aligned: table at `+0x1800`), the stack is set via `adr x1, _stack_top` before any
   stack use, and the only relocation-dependent runtime data are the 35 slots above.
   **One incompatibility was found during execution, not in review — see §4.**

**New finding vs. A5:** the audit linker script's explicit `PHDRS { text; data; }` block
means the `-pie` output has **no `PT_DYNAMIC` program header** (and `readelf -d` reports
no dynamic section) even though a `.rela.dyn` section exists. A production loader that
follows the ELF ABI would look for `PT_DYNAMIC`; it must be added to the script (or
`__rela_start/__rela_end` symbols emitted) before PongoOS integration. The harness
therefore locates relocations via the `SHT_RELA` `.rela.dyn` section header (documented
assumption, §8).

---

## 2. Isolated test build (canonical artifacts untouched)

Everything new lives under `build/audit/`:

| Path | What it is |
|---|---|
| `build/audit/pic/*.o`, `myos_kernel_pic_dyn.elf` | PIE build objects/ELF (sha256 `38662db8…`, pre-fix ELF `a15dd575…`) |
| `build/audit/pic/kernel_pic.ld` | audit-only copy of `kernel.ld`, base 0 |
| `build/audit/src/uart_pl011.S` | **copy** of canonical source with the one-line fix (§4) |
| `build/audit/pie_harness.py` | the loader/test harness (sha256 `66b008bd…`) |
| `build/audit/q6_qmp.py` | minimal QMP client for evidence collection |
| `build/audit/run_qemu_case.sh` | experiment runner (boot → wait for marker → QMP evidence → real exit status) |
| `build/audit/reloc_A.elf` / `reloc_B.elf` | relocated images actually executed (sha256 `48e3e88a…` / `053c9388…`) |
| `build/audit/record_A.json` / `record_B.json` | relocation count + exact load base + hashes (required by §3) |

**Not modified:** canonical `build/*.o`, `build/myos_kernel.elf`, `build/myos_kernel.bin`,
M1 payloads, `include/myos_image.h` (canonical M2 image format), `Makefile` (so
`make qemu` and its `MYOS_KERNEL_PASS` baseline are untouched), `pongoos-src/`. Verified
by `git status` (identical to the pre-experiment state) at the end of the run.

**MyOS image format was not used as a stand-in for ELF semantics.** The experiment is
pure ELF: parse ELF → map PT_LOAD → apply `R_AARCH64_RELATIVE` → validate entry →
execute. The canonical fixed-`load_addr` image format plays no part; how a future boot
image carries ELF semantics (or an equivalent relocation step) is explicitly a
PongoOS-integration *design* question (§9), not answered here.

---

## 3. The harness (`build/audit/pie_harness.py`)

Usage:

```
python3 build/audit/pie_harness.py relocate --elf IN.elf --base 0x... --out OUT.elf --record REC.json
python3 build/audit/pie_harness.py negtests --elf IN.elf
```

Behaviour, matching §3 of the task:

* **Bounds-checked, checked-arithmetic parsing** — ELF magic/class/endian/machine,
  bounded `e_phoff/e_shoff` table ranges, `u64_add`/`u64_mul` overflow helpers used for
  every offset+size computation; `p_filesz ≤ p_memsz`; file ranges within the file;
  `p_vaddr + p_memsz` no u64 overflow; `p_align` power-of-two with offset/vaddr
  congruence; mapping cap of 64 MiB.
* **Segment mapping** — both PT_LOADs copied into a bytearray sized exactly to the image
  span (`0x31278` = 201,336 bytes), remainder zero-filled (BSS policy, §1.4).
* **Relocation per the AArch64 ELF ABI** — only `R_AARCH64_RELATIVE` (1027) with
  symbol index 0 is supported; `value = base + r_addend`; every record checked for type,
  target-inside-mapped-image, `r_offset+8` overflow, and runtime-address overflow —
  **all 35 records are validated before any byte is applied (two-phase)**, so a rejected
  input causes zero writes.
* **Entry validation** — `e_entry` must lie inside a `PF_X` PT_LOAD before anything is
  applied.
* **Emission** — writes an `ET_EXEC` ELF with `PT_LOAD` vaddrs/paddrs rebased to the
  chosen base, entry rebased, section headers dropped, relocations consumed; then
  re-parses the output and byte-compares every emitted segment against the in-memory
  relocated image (`"verify": "emitted PT_LOAD bytes match in-memory relocated image"`).
* **Recording** — `record_{A,B}.json` stores `base`, `delta`, `reloc_count` (35),
  `reloc_method`, `entry_runtime`, segment table, and sha256 of source and output.
* **Rejection** — unsupported types, missing/truncated relocation tables, malformed
  records and out-of-bounds everything raise `Reject`; the CLI prints
  `REJECTED: …` and exits 1 **without writing the output file** (verified: running it
  against the canonical `build/myos_kernel.elf` yields
  `REJECTED: no relocation table found (PT_DYNAMIC/.rela.dyn)`, exit 1, no output —
  a `-fno-pic` ET_EXEC cannot masquerade as a relocatable image).

---

## 4. Bases selected after inspecting the ELF segments and the QEMU memory layout

**QEMU inspection (not guessed):** QEMU started paused (`-S`) with QMP;
`info mtree -f` returned the full flat view:

```
0000004000000000-00000040ffffff (prio 0, ram): mach-virt.ram      ← RAM: [0x40000000, 0x48000000) = 128 MiB
0000000000000000-0000000003ffffff (romd): virt.flash0              ← everything else (flash, GIC, pl011 @0x09000000, …)
0000000008000000-0000000008011fff (i/o): gic … / 0000000009000000 pl011
```

Reset state with the canonical kernel: `PC=0x40080000` (= ELF entry), all GPRs zero,
`PSTATE … EL1h`. A full 128 MiB RAM dump (QMP `dump-guest-memory`) was scanned for the
FDT magic `d00dfeed` in every configuration:

| Configuration | FDT in RAM? | Image range |
|---|---|---|
| canonical baseline | **none anywhere** | `0x40080000–0x400b1120` |
| reloc_A (base 0x40080000) | **none anywhere** | `0x40080000–0x400b1278` |
| reloc_B (base 0x44000000) | **one, at `0x40000000`** (valid FDT header: magic ✓, version 17, structure block ends ≈ `0x40001e71`, header `totalsize` padded to `0x100000`) | `0x44000000–0x44031278` |

So QEMU's generated device tree appears only in the B configuration, at RAM base. Its
*declared* range `[0x40000000, 0x40100000)` and its actual content are both >60 MiB away
from the B image; it does not overlap. (Observed facts; the placement *mechanism* is a
hypothesis — that QEMU places its FDT at RAM base only when that region is free of the
loaded kernel — and was not verified against QEMU source. It does not matter for the
experiment either way: no mapped data overlaps the B image, verified by dump.)

**Chosen bases:**

* **Experiment A = `0x40080000`** — the original compatible base (canonical link base
  and the baseline `-kernel` entry point; `min_vaddr = 0` so `delta = 0x40080000`).
* **Experiment B = `0x44000000`** — mid-RAM (64 MiB mark of the inspected 128 MiB),
  64 KiB aligned (satisfies `p_align` and the 2 KiB VBAR constraint; `vbar_el1_table`
  lands at `0x44001800`), image span `[0x44000000, 0x44031278)` does not overlap the
  FDT (`0x40000000…`), the DTB-free region, or any device/MMIO window (all of which sit
  below `0x40000000` or at `0x09000000`).

*(Note: HMP `pmemsave` rejected the filename — `invalid char 't' in expression` — so
RAM evidence was collected with QMP `dump-guest-memory` instead.)*

---

## 5. Relocation processing summary

```
source ELF : build/audit/pic/myos_kernel_pic_dyn.elf   (ET_DYN, entry 0, min_vaddr 0)
relocs     : 35 × R_AARCH64_RELATIVE, sym=0            (section .rela.dyn; no PT_DYNAMIC)
rule       : *(u64*)(base + r_offset) = base + r_addend
validate   : ALL 35 records + entry point BEFORE any write (two-phase)
A          : base 0x40080000, delta 0x40080000, entry 0x40080000 → reloc_A.elf (48e3e88a…)
B          : base 0x44000000, delta 0x44000000, entry 0x44000000 → reloc_B.elf (053c9388…)
verify     : emitted PT_LOAD bytes byte-identical to in-memory relocated image (both runs)
```

Spot verification of applied fixups (from RAM dumps / QMP examine):

| Slot | Expected | Observed (B) |
|---|---|---|
| `uart_pl011_ops+8` @ `0x44002708` | `0x44001464` (`putc`, base+0x1464) | `0x44001464` ✓ |
| `uart_pl011_ops+16` @ `0x44002710` | `0x4400147c` (`puts`) | `0x4400147c` ✓ |
| `boot_console` @ `0x44031008` | `0x44031100` (`&early_console`) | `0x44031100` ✓ |

---

## 6. Experiment A and B — commands, logs, exit statuses

Runner used for both (isolated, `build/audit/run_qemu_case.sh`):

```
timeout <T> qemu-system-aarch64 -machine virt -cpu cortex-a57 -nographic \
    -kernel <elf> -qmp unix:/tmp/q6_<tag>.sock,server,nowait > /tmp/q6_<tag>.log 2>&1
# poll for MYOS_KERNEL_PASS, then QMP evidence (info registers, examine, dump)
# exit status captured directly from `wait` (no pipes) — real exit, not a filter's
```

### Experiment A — original base `0x40080000`

Command: `bash build/audit/run_qemu_case.sh A build/audit/reloc_A.elf 10 "info registers"`

```
MYOS_KERNEL_PASS found=1 (after 2 polls)
qmp-exit=0
qemu-exit-status=124   (124 = killed by timeout after full run)
MYOS_KERNEL_PASS: 1
MyOS banner: 1
```

Log tail (identical in structure to the canonical baseline, full stage report present):

```
[....] KERNEL_MAIN - BEGIN
S10b
[....] KERNEL_MAIN - BEGIN
========================================
MyOS Kernel Runtime - QEMU virt
...
MYOS_KERNEL_PASS
========================================
[  OK ] KERNEL_MAIN
qemu-system-aarch64: terminating on signal 15 … (timeout)
```

QMP evidence taken while running: `PC=0x40081644` (= `base + 0x1644`, the
`while(1){wfe}` loop in `kernel_main` — the kernel's designed terminal state),
`SP=0x400b0fc0` (rebased stack, just below `_stack_top = 0x400b1000`), `EL1h`,
`X01=0x09000000` (fixed PL011 MMIO base, correctly not rebased). RAM dump: image word
at `0x40080000` = `404238d5` (`mrs x0, CurrentEL`), ops slot `0x40082708 = 0x40081464`.

**Exit status: 124** — QEMU never exits on its own because the kernel parks in `wfe`
forever (by design; the canonical baseline behaves the same way). `124` from `timeout`
with the complete marker in the log is the pass criterion; a missing marker would have
been a failure.

### Experiment A0 (pre-fix attempt, reported for honesty)

The first attempt at base `0x40080000` — before the §4 bug was found — printed every
early/`early_uart_puts` stage marker, then **stopped after `S10b` with no
`MYOS_KERNEL_PASS`** (log `/tmp/q6dbg.log`, marker count 0), QEMU exit 124. QMP
forensics: `PC=0x40081644` (already in the `wfe` loop — no crash),
`boot_console @ 0x400b1008 = 0` (NULL) → every `diag_puts` silently no-op'd. Root cause
in §7. This run was a **failure** and is recorded as one.

### Experiment B — second base `0x44000000`

Command: `bash build/audit/run_qemu_case.sh B build/audit/reloc_B.elf 12 "info registers" "x/2gx 0x44031008" "x/2gx 0x44002708" "x/4i 0x44000000" "pmemsave …"`

```
MYOS_KERNEL_PASS found=1 (after 2 polls)
qmp-exit=0
qemu-exit-status=124   (124 = killed by timeout after full run)
MYOS_KERNEL_PASS: 1
MyOS banner: 1
```

The full log is the complete expected pass output from a second base — every stage line
(`[....] BOOTSTRAP - BEGIN` … `[ OK ] KERNEL_MAIN`), the full diagnostic report, and
`MYOS_KERNEL_PASS` (`/tmp/q6_B.log`, marker count 1).

QMP evidence while running:

```
PC=0x44001644          ← base + 0x1644, kernel_main wfe loop
SP=0x44030fc0          ← base stack, below _stack_top 0x44031000
PSTATE … EL1h
x/2gx 0x44031008 → 0x44031100      boot_console = &early_console (relocated)
x/2gx 0x44002708 → 0x44001464, 0x4400147c   ops fixups applied
x/4i  0x44000000 → mrs x0, currentel ; lsr ; cmp ; b.eq    ← image intact at new base
```

RAM dump analysis (128 MiB, full scan): image bytes present at `0x44000000`; FDT at
`0x40000000` only (no overlap, §4); word at `0x40080000` = `00000000` (nothing else
loaded); no other relocated-pointer mismatches.

**Both experiments demonstrated the complete expected pass marker — not merely ELF
parsing or reaching the entry point.** The harness was never in a position where it
could not safely support second-base execution; no blocker remains unaddressed (the one
blocker found was in the *kernel source*, root-caused and fixed in isolation).

---

## 7. The latent bug this experiment exposed (isolated fix)

`arch/aarch64/uart_pl011.S`, `boot_console_register` (canonical source):

```
adrp x1, boot_console
str  x0, [x1]              ← missing: add x1, x1, #:lo12:boot_console
```

`ADRP` yields the **page base**; without the lo12 addend the store lands on the page
base, not on the symbol. Disassembly of the canonical object confirms only
`R_AARCH64_ADR_PREL_PG_HI21 boot_console` and no `ADD_ABS_LO12_NC` companion.

* **Why the canonical baseline works:** in the canonical layout
  `boot_console == 0x400b1000` — *exactly* a page boundary — so the wrong-address store
  happens to hit the right variable. Correct by layout luck, not by construction.
* **Why the PIE build fails:** its (different, equally valid) layout puts
  `boot_console` at `0x31008` → store hits `0x31000` (`boot_fb_renderer`) instead →
  `boot_console` stays NULL → all framework output silently disappears (A0 run, §6).
* **Same class, second instance:** `start.S` step 7 (`adrp x0, boot_console;
  ldr x0, [x0]`) also omits the lo12 addend. In the canonical layout it reads the right
  word by the same luck; in the PIE layout it reads `0x31000`. It is functionally
  harmless there (both branches of the check print nothing), so it was **not** fixed —
  only reported.
* **Fix scope:** `arch/aarch64/uart_pl011.S` was **not** modified. A copy
  (`build/audit/src/uart_pl011.S`, sha256 `f0345140…` vs canonical `277cdecd…`) gained
  the one `add x1, x1, #:lo12:boot_console` line; only `build/audit/pic/uart_pl011.o`
  was rebuilt from the copy and the isolated PIE ELF relinked (35 relocations —
  unchanged). The relocated `add` is itself PC-relative, so position-independence is
  unaffected.

This is a genuine, source-backed deliverable of the experiment: **any future layout
change (including the PIE/relocatable build Strategy B needs) must fix this in the
canonical source, or console registration will silently break.**

---

## 8. Negative-test results

Command: `python3 build/audit/pie_harness.py negtests --elf build/audit/pic/myos_kernel_pic_dyn.elf`
Result: **exit 0, `NEGATIVE TESTS: ALL PASS`** (13 checks). Each case mutates a copy of
the valid ELF; the harness must raise `Reject`, and the mutated input's sha256 must be
unchanged afterwards (no writes). Final output:

```
[PASS] unsupported relocation type (R_AARCH64_ABS64=257)          REJECTED: unsupported relocation type 257 (record 0)
[PASS] truncated relocation table (extends past end of file)      REJECTED: .rela range: range 0x12798+0x42958 exceeds 0x42960
[PASS] partial trailing relocation record (size % entsize != 0)   REJECTED: relocation table size not a multiple of entsize
[PASS] invalid segment bounds (p_offset+filesz beyond EOF)        REJECTED: PT_LOAD file range: range 0x42950+0x2ae0 exceeds 0x42960
[PASS] invalid segment (p_filesz > p_memsz)                       REJECTED: PT_LOAD filesz > memsz
[PASS] bad record in the MIDDLE of the table (no partial apply)   REJECTED: unsupported relocation type 4660 (record 17)
[PASS] relocation target outside mapped image                     REJECTED: relocation target 0x7fffffff outside mapped image
[PASS] relocation target address overflow (r_offset+8)            REJECTED: u64 add overflow: 0xfffffffffffffffc+0x8
[PASS] entry point outside executable segment                     REJECTED: entry 0x50000 not inside an executable PT_LOAD
[PASS] arithmetic overflow (p_vaddr + p_memsz overflows u64)      REJECTED: u64 add overflow: 0xfffffffffffff000+0x2000
[PASS] implausible segment size (1 TiB memsz) refused             REJECTED: PT_LOAD memsz 0x10000000000 exceeds map limit
[PASS] program header table beyond EOF                            REJECTED: program header table: range 0x42958+0x70 exceeds 0x42960
[PASS] original file unchanged by all tests                       sha256 match
```

Mapping to the required minimum:

1. *Unsupported relocation type* → case 1 (+ case 6, a bad record at index 17 of 35,
   proving rejection happens **before** any of the other 34 are applied — the two-phase
   design makes partial application impossible).
2. *Truncated relocation record* → cases 2–3.
3. *Invalid segment bounds* → cases 4–5.
4. *Relocation target outside mapped memory* → cases 7–8. (Applicability note: for
   `R_AARCH64_RELATIVE` the slots legitimately include read-only segments — the loader
   writes before execution; the criterion enforced is “inside a mapped PT_LOAD”. Any
   type that would require a writable target, or a symbol, is rejected outright as
   unsupported.)
5. *Entry point outside an executable segment* → case 9.
6. *Arithmetic overflow* → cases 8, 10, 11, 12.

Additional: the untouched original file's sha256 matches before/after the whole suite;
invalid images never produce an output ELF (CLI `REJECTED` + exit 1, verified on the
canonical kernel as an extra case). Development note: two of these tests initially
patched the wrong header field and tripped a different (still correct) rejection path;
the *test mutations* were corrected so each case provably exercises its intended check —
the harness logic itself required no change.

---

## 9. Limitations and harness-specific assumptions

* **Host/QEMU only.** This proves relocation + execution semantics under
  `qemu-system-aarch64 -machine virt` at EL1 with MMU off. It does not prove iPhone
  compatibility, cache/MMU behaviour at PongoOS’s `BOOT_FLAG_RAW` handoff, DRAM
  persistence, or USB/`bootr` transport. Those remain device-phase questions.
* **The harness is not a PongoOS loader.** It plays the loader’s role host-side against
  the ELF itself. The future PongoOS-side artifact (self-relocation stub at image offset
  0, or a loader in the module) must reproduce exactly these steps; the MyOS M2 image
  format was deliberately not used here and its relationship to ELF semantics is an
  open design question for the integration phase.
* **Relocation discovery assumes section headers** (`.rela.dyn`, `SHT_RELA`) because the
  audit linker script drops `PT_DYNAMIC` (§1). A production build must expose
  `PT_DYNAMIC`/`DT_RELA` or explicit `__rela_*` symbols; alternatively section headers
  must be retained in the shipped artifact.
* **Supported ABI subset:** ELF64 little-endian AArch64, `ET_DYN`/`ET_EXEC`, `RELA`
  only, `R_AARCH64_RELATIVE` with symbol index 0 only. Everything else is rejected by
  design (no `REL`, no `GLOB_DAT`/`ABS64`/TEXTREL) — which matches this kernel’s actual
  relocation surface but is not a general-purpose dynamic loader.
* **`min_vaddr = 0`** for this build, so `delta = base`. The code paths for nonzero
  `min_vaddr` exist but were not exercised by a nonzero-base artifact.
* **Exit statuses:** `124` for A and B = `timeout` killed a QEMU that was still running
  the kernel’s intentional `while(1){wfe}` loop. The pass signal is the complete marker
  in the serial log plus QMP state; QEMU has no “kernel finished” exit for this design.
* **FDT placement mechanism** (§4) is an observation + hypothesis, not verified against
  QEMU source; overlap was ruled out empirically by full-RAM dumps instead.
* The `start.S` lo12 read issue (§7) remains in the PIE build (harmless, reported).

---

## 10. GO / NO-GO for continuing to a PongoOS integration design

> ### **GO — proceed to the PongoOS integration *design* (still no implementation on device), conditioned on five requirements established by this experiment:**
>
> 1. **Fix `boot_console_register` in the canonical source** (add the `#:lo12`
>    addend) before any layout change ships — the current canonical kernel is correct
>    only because `boot_console` happens to be page-aligned (§7). Assess the same-class
>    `start.S` read while at it.
> 2. **Ship a real relocation artifact:** the Strategy B build must expose its
>    relocation table to the runtime applier — `PT_DYNAMIC`/`DT_RELA` or linker-provided
>    `__rela_start/__rela_end` (the current `PHDRS` script silently drops
>    `PT_DYNAMIC`). The relocation *content* is proven small and stable: 35
>    `R_AARCH64_RELATIVE`, single-pass `base + addend`, no symbol resolution.
> 3. **Preserve the load-time invariants proven here:** entry at image offset 0 (the
>    `bootr` path jumps to the buffer start), base ≥2 KiB-aligned (VBAR at `+0x1800`),
>    whole image contiguous (201,336 B ≤ 1 MiB default upload buffer), MMIO constants
>    (`0x09000000`-class) excluded from rebasing.
> 4. **Decide the BSS policy explicitly:** today `filesz == memsz` (BSS is file-backed
>    and there is no startup clear loop); if a future script makes `.bss` `NOBITS`, a
>    startup clear and/or loader zero-fill becomes mandatory.
> 5. **Keep the evidence boundary:** relocation must be applied *before* the first
>    relocated pointer is dereferenced (first use: `boot_console->ops->puts`), i.e. by a
>    stub at offset 0 or a loader step — and the PongoOS handoff ABI/cache-state
>    questions (§9) must be answered in the design before any device work.
>
> **NO-GOs carried forward:** no device work, no PongoOS modification, no image-format
> change in this phase; the canonical `make qemu` baseline and M1 remain untouched.

---

## 11. Constraint compliance

* `pongoos-src/` — not modified (read-only; revision preserved).
* M1, canonical image format (`include/myos_image.h`), canonical QEMU build artifacts —
  not modified (the bug fix exists only as a copy under `build/audit/src/`).
* `make qemu` target and `MYOS_KERNEL_PASS` baseline — untouched; re-verified at the end
  of the run (§12).
* No iPhone connection, no device operations, no flash/restore/persist of any device.
* No commits, no pushes.
* All experimental code and outputs under `build/audit/` or `/tmp`.

### Artifacts produced (disposable)

| Path | sha256 (short) | Purpose |
|---|---|---|
| `build/audit/pie_harness.py` | `66b008bd…` | loader/test harness + negative tests |
| `build/audit/q6_qmp.py` | — | QMP evidence client |
| `build/audit/run_qemu_case.sh` | — | experiment runner |
| `build/audit/src/uart_pl011.S` | `f0345140…` | canonical copy + one-line lo12 fix |
| `build/audit/pic/myos_kernel_pic_dyn.elf` | `38662db8…` | PIE input ELF |
| `build/audit/reloc_A.elf` | `48e3e88a…` | executed @ 0x40080000 |
| `build/audit/reloc_B.elf` | `053c9388…` | executed @ 0x44000000 |
| `build/audit/record_A.json`, `record_B.json` | — | base + reloc count + hashes |
| `/tmp/q6_A.log`, `/tmp/q6_B.log`, `/tmp/q6dbg.log` | — | A / B / A0(pre-fix) serial logs |
| `/tmp/q6_*_qmp.json`, `/tmp/q6*_ram*.bin` | — | QMP register/examine + RAM dumps |

---

## 12. Final baseline re-verification

Run as the last step of this task, with the exit status captured directly (no pipes):

```
make qemu > /tmp/q6_final_makeqemu.log 2>&1; echo $?
→ REAL make qemu exit status = 0
MYOS_KERNEL_PASS occurrences: 1
=== QEMU TEST: PASS ===
[  OK ] KERNEL_MAIN
qemu-system-aarch64: terminating on signal 15 … (timeout)
QEMU exited normally
=== QEMU TEST: PASS ===
```

Both required markers are present — `MYOS_KERNEL_PASS` and
`=== QEMU TEST: PASS ===` — and the real exit status of `make qemu` is **0**.
Final `git status` shows only the pre-existing working-tree changes plus the three
report files (`M2_A4…`, `M2_A5…`, `M2_A6_SECOND_BASE_REPORT.md`); no canonical source,
build artifact, Makefile, image format, or `pongoos-src/` file was modified.
