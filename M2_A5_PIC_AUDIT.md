# M2-A5 — AArch64 Kernel Position-Independence Audit

**Status:** audit only. No loader implemented, no canonical build/image format modified,
no `pongoos-src/` changes, no device operations, nothing committed.

**Question answered (A4 unresolved question 8):** can the MyOS AArch64 QEMU kernel
execute correctly when loaded at an *arbitrary runtime address* by PongoOS's existing
`BOOT_FLAG_RAW` / `bootr` path?

**Short answer:** the current kernel is a **fixed-address (Design C)** image. All of its
*code* is position-relative, but its *data* contains **32 link-time-frozen absolute
pointers** and the final ELF contains **no relocation table at all**. It cannot run at a
second base as it stands. A relocation table for Strategy B is however small, uniform and
fully characterized by this audit (35 `R_AARCH64_RELATIVE` entries, no symbol
resolution) — see the GO decision in §7.

---

## 0. Environment, tool versions, build commands

Environment recorded from the host:

| Tool | Version |
|---|---|
| `aarch64-linux-gnu-gcc` | gcc (GCC) 16.1.0 |
| `aarch64-linux-gnu-ld` (GNU binutils) | 2.47 |
| `readelf` / `objdump` (host binutils) | 2.47 (`/usr/bin/readelf`) |
| `qemu-system-aarch64` | 11.1.1 |
| `make` | GNU Make 4.4.1 |
| `python3` | 3.14.7 (used for the pointer scanner) |

Canonical kernel build (from `Makefile`, lines 25–27 and 65–86):

```
KERNEL_CROSSFLAGS := -ffreestanding -nostdlib -nostartfiles -fno-pic -fno-pie \
                     -fno-asynchronous-unwind-tables -fno-unwind-tables -O2 \
                     -Wall -Wextra -mgeneral-regs-only

$(BUILD)/start.o:        aarch64-linux-gnu-gcc $(KERNEL_CROSSFLAGS) -c boot/aarch64/start.S
$(BUILD)/boot_diag.o:    aarch64-linux-gnu-gcc $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -c arch/aarch64/boot_diag.c
$(BUILD)/vectors.o:      aarch64-linux-gnu-gcc $(KERNEL_CROSSFLAGS) -c arch/aarch64/vectors.S
$(BUILD)/uart_pl011.o:   aarch64-linux-gnu-gcc $(KERNEL_CROSSFLAGS) -c arch/aarch64/uart_pl011.S
$(BUILD)/main.o:         aarch64-linux-gnu-gcc $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -I. -c kernel/main.c

$(KERNEL_ELF):           aarch64-linux-gnu-ld -T boot/aarch64/kernel.ld --build-id=none \
                             -z noexecstack -o build/myos_kernel.elf \
                             start.o boot_diag.o vectors.o uart_pl011.o main.o
```

**Recorded kernel target and source set** (the *only* objects linked into the QEMU
kernel; no libraries, no crt files):

| Object | Source |
|---|---|
| `build/start.o` | `boot/aarch64/start.S` |
| `build/boot_diag.o` | `arch/aarch64/boot_diag.c` (+ `arch/aarch64/boot_diag.h`) |
| `build/vectors.o` | `arch/aarch64/vectors.S` (+ `arch/aarch64/vectors_defs.h`) |
| `build/uart_pl011.o` | `arch/aarch64/uart_pl011.S` |
| `build/main.o` | `kernel/main.c` |
| link | `boot/aarch64/kernel.ld` → `build/myos_kernel.elf`, `objcopy -O binary` → `build/myos_kernel.bin` |

QEMU baseline target (unchanged): `qemu-system-aarch64 -machine virt -cpu cortex-a57
-nographic -kernel build/myos_kernel.elf`, PASS = `MYOS_KERNEL_PASS` in output.

**The build is explicitly non-PIC.** `Makefile:25` passes `-fno-pic -fno-pie`, and
`boot/aarch64/kernel.ld:18` pins `. = 0x40080000`. Neither `-fPIC` nor `-pie` appears
anywhere in the canonical build.

**Flag fidelity check (experiment E1, §3):** all five canonical objects were rebuilt
into `build/audit/rebuild/` with exactly the flags recorded in the Makefile and compared
byte-for-byte with `cmp`:

```
start.o: IDENTICAL to canonical
boot_diag.o: IDENTICAL to canonical
vectors.o: IDENTICAL to canonical
uart_pl011.o: IDENTICAL to canonical
main.o: IDENTICAL to canonical
```

So the artifacts really were produced by the recorded `-fno-pic -fno-pie` command lines;
there is no hidden PIC anywhere in the pipeline. (Rebuild emitted one pre-existing
warning: `boot_diag.c:601 'diag_check_injected_failure' defined but not used` — unrelated
to this audit.)

---

## 1. ELF and relocation-table findings

`readelf -h`, `readelf -lW`, `readelf -SW`, `readelf -rW` on `build/myos_kernel.elf`:

```
Type:       EXEC (Executable file)
Machine:    AArch64
Entry:      0x40080000

PHDR 0: LOAD  vaddr/paddr 0x40080000  filesz 0x003030  memsz 0x003030  R E  align 0x10000  (.text)
PHDR 1: LOAD  vaddr/paddr 0x40090000  filesz 0x021120  memsz 0x021120  RW  align 0x10000  (.data)

Sections: .text 0x40080000/0x3030 AX | .data 0x40090000/0x21120 WA |
          .ARM.attributes | .symtab | .strtab | .shstrtab      (7 section headers total)

readelf -rW build/myos_kernel.elf
→ "There are no relocations in this file."
```

* **No `.rela.*`, `.got`, `.plt`, `.dynamic`, `.interp` sections exist** in the final
  ELF. Nothing that a runtime loader could consume exists in the shipped artifact.
* **`filesz == memsz` for both segments**, and there is **no NOBITS section** at all —
  the linker script (`kernel.ld:26–32`) merges `*(.bss) *(.bss.*) *(COMMON)` into the
  `.data` output section, whose type is therefore `PROGBITS`. The BSS is *file-backed
  zero data* (see §2.4).
* The QEMU ELF load and the `MYOS_KERNEL_PASS` baseline exercise only the *link address*
  path (Design C), at `0x40080000`.

### 1.1 Relocation types in the input objects (resolved at link time, lost afterwards)

Histograms from `readelf -rW` per canonical object:

| Object | relocation types |
|---|---|
| `start.o` | `ADR_PREL_LO21` ×23, `ADR_PREL_PG_HI21` ×1, `CALL26` ×22 |
| `boot_diag.o` | **`ABS64` ×28**, `ADD_ABS_LO12_NC` ×99, `ADR_PREL_PG_HI21` ×110, `LDST64_ABS_LO12_NC` ×31, `LDST32_ABS_LO12_NC` ×2 |
| `vectors.o` | `ADR_PREL_LO21` ×1, `ADR_PREL_PG_HI21` ×4, `CALL26` ×8, `LDST64_ABS_LO12_NC` ×4 |
| `uart_pl011.o` | **`ABS64` ×4**, `ADR_PREL_LO21` ×2, `ADR_PREL_PG_HI21` ×1 |
| `main.o` | `ADD_ABS_LO12_NC` ×6, `ADR_PREL_PG_HI21` ×7, `CALL26` ×3, `LDST64_ABS_LO12_NC` ×9 |

Interpretation:

* `CALL26` (BL), `ADR_PREL_LO21` (ADR), `ADR_PREL_PG_HI21` (ADRP),
  `ADD_ABS_LO12_NC`, `LDST*_ABS_LO12_NC` are all **PC-relative instruction
  encodings**. The linker resolves them into instruction bits; after linking they
  depend only on *relative* distances inside the image and therefore **survive a
  whole-image move unchanged**. They are position-independent *as linked*.
* `ABS64` ×32 (`boot_diag.o` 28 + `uart_pl011.o` 4) are **absolute addresses written
  into data**. They are resolved at link time and baked into `.rodata`; the information
  needed to fix them at runtime (their addends) is **discarded** by the final link —
  the canonical ELF has no relocation section. These are the only constructs that
  require relocation processing (or source changes to eliminate them).
* These are exactly the “linker-generated relocations resolved at link time and
  therefore unavailable to a runtime loader” category: after `ld`, they are gone.

### 1.2 Embedded absolute pointers — complete inventory (experiment E2, §3)

A host-side scanner (Python, `build/audit/`) walked the raw image
(`objcopy -O binary` of the canonical ELF, 0x31120 = 200,992 bytes) 8 bytes at a time
and reported every little-endian qword whose value lies inside the link range
`[0x40080000, 0x400b1120)`. **Exactly 32 hits**, matching the 32 `ABS64` relocations
of §1.1 one-for-one:

| Location (link addr) | Contents | Source |
|---|---|---|
| `uart_pl011_ops+0x08..0x20` (4 qwords, `0x40083008–0x40083020`) | `&uart_pl011_putc`, `&uart_pl011_puts`, `&uart_pl011_write`, `&uart_pl011_flush` | `arch/aarch64/uart_pl011.S:87–92` (`.quad uart_pl011_*`) |
| `boot_stage_names[0..10]` + `boot_stage_names[0xFF]` (12 live qwords of a 256-entry/2 KiB array, `0x40082768–0x400827b8`, `0x40082f60`) | pointers to string literals | `arch/aarch64/boot_diag.c:50–64` (`const char * const boot_stage_names[]`, `[BOOT_STAGE_UNKNOWN]=0xFF` forces 256 slots) |
| anonymous local table `0x400826e8–0x40082760` (16 qwords) | pointers to string literals | `arch/aarch64/boot_diag.c:514–520` — `subsys_names[8]` + `type_names[8]` inside `boot_diag_format_error()` |

No other absolute image pointers exist anywhere in the image (`.data` runtime
variables `boot_console`, `boot_fb_renderer`, `early_console`, `diag_state` are all
zero/constant-initialized; function pointers only occur in the tables above).

### 1.3 Instruction-level scan — no hidden absolute addresses

`aarch64-linux-gnu-objdump -d build/myos_kernel.elf`:

* `adr` uses: **26** (e.g. `adr x1, _stack_top`, string addresses, `adr x0, vbar_el1_table`)
* `adrp` uses: **123** (e.g. `adrp x0, diag_state`, `adrp x0, boot_console`) — always
  paired with `ldr/str … [xN, #:lo12:…]`
* `b`/`bl` uses: **190** — PC-relative, ±128 MiB range
* `ldr`/`ldrsw` *literal* (PC-relative pool load with bare address operand): **0**
* immediates in the link-address range (`#0x4008…`, `#0x4009…`, `#0x400a…`, `#0x400b…`):
  **0**
* `movz/movk` materializing `0xffff…` absolute addresses: **0**

All PC-relative ranges are satisfied with margin: the image is 200,992 bytes; farthest
ADR target from `_start` is `_stack_top` at `+0x31000` (limit ±1 MiB), ADRP limit ±4 GiB,
BL limit ±128 MiB. These constraints are enforced by the linker at link time anyway.

---

## 2. Source-backed relocation-sensitive references

### 2.1 Position-independent as linked (no processing needed)

| Construct | Where | Why safe |
|---|---|---|
| Entry/`_start` flow, stack setup | `boot/aarch64/start.S:59` (`adr x1, _stack_top`) | ADR is PC-relative; stack lives *inside* the image (`.bss`, `start.S:306–317`) and moves with it. No incoming-`sp` dependency: `sp` is set before any stack use. |
| All string references | `start.S` (`.rodata` `adr`), `boot_diag.c`, `main.c` (ADRP/`lo12`) | PC-relative pairs |
| Global variable access (`boot_console`, `diag_state`) | `start.S:101`, `boot_diag.c`, `main.c`, `uart_pl011.S` (`boot_console_register`) | `adrp`+`ldr/str` pairs — relative |
| Exception vector install | `arch/aarch64/vectors.S:208` (`adr x0, vbar_el1_table`) | VBAR gets the *runtime* address of the table; table code uses only `b` (PC-relative) and ADRP to `.rodata.vectors` |
| Function calls | everywhere (`bl`, `CALL26`) | PC-relative |
| Diagnostic state machine | `boot_diag.c` | plain code + PC-relative data refs |

### 2.2 Requires relocation processing (frozen at link time)

1. **`uart_pl011_ops` vtable** — `arch/aarch64/uart_pl011.S:87–92`. Four absolute
   function pointers in read-only `.rodata`. First use: the very first
   `diag_puts()`/`boot_console->ops->…` dispatch (`boot_diag.h` macros,
   `boot_diag.c` `diag_write_str()`).
2. **`boot_stage_names[]`** — `arch/aarch64/boot_diag.c:50–64`, used by
   `boot_diag_print_report()`, `boot_diag_print_stage()`, `boot_diag_fail()`,
   `boot_diag_panic()`, the exception handler (`boot_diag.c:156,182,215,221,270,354,381,423`).
3. **`subsys_names[]` / `type_names[]`** — `boot_diag.c:514–520`, used by
   `boot_diag_format_error()`.

Failure mode if executed at base `B ≠ 0x40080000`: every pointer above still points
into `[0x40080000, …)`. Code reached through them executes/reads at the *old* absolute
address (below the actual image if `B` is higher, above it if `B` is lower) → wrong
instruction fetch or data abort. Concretely, the boot would print the early messages
(they use PC-relative `early_uart_puts` + fixed MMIO), then jump to
`0x400814a4` (`uart_pl011_putc`’s *link* address) on the first framework print.

### 2.3 MMIO — must stay absolute physical, must NOT be rebased

* `PL011_BASE = 0x09000000` — defined at `arch/aarch64/uart_pl011.S:37`, used at
  `uart_pl011.S:56,99,110,126,143`; `boot/aarch64/start.S:22` (`#define`) +
  `early_uart_puts` (`start.S:217–226`); `arch/aarch64/vectors.S:204,212,217`
  (debug chars in `vbar_el1_install`).
* These are `mov` immediates (platform constants), *not* image-relative references.
  They are unaffected by image relocation and must never be delta-adjusted by a
  relocator. They are also QEMU-virt-specific — irrelevant to PIC-ness itself, but a
  reminder that this kernel is the QEMU target, not the iPhone M1 payload.

### 2.4 Vectors, stack, BSS/startup

* **Vector table alignment:** `vectors.S:18` `.align 11` (2 KiB). `vbar_el1_table`
  sits at link offset `+0x1800`, so the runtime base must satisfy
  `base % 2048 == 0`. `alloc_contig`-backed `bootr` buffers are page-aligned (4 KiB),
  so the constraint is satisfiable — but it is a *real* load-time constraint and must
  be documented, not assumed.
* **No BSS-clear code exists.** There is no `__bss_start`/`__bss_end` (grep across
  `boot/`, `kernel/`, `arch/`, `Makefile`: zero hits) and `start.S` contains no zeroing
  loop. This is currently *harmless only because* `kernel.ld:26–32` folds `.bss` into
  the `PROGBITS` `.data` section: `filesz == memsz == 0x21120`, so BSS bytes are zeros
  *carried in the ELF/raw image*. `boot_diag_init()` also relies on this (it resets
  scalars but never zeroes `diag_state.events[]`).
  **Landmine:** if the linker script is later split so `.bss` becomes a genuine
  `NOBITS` region (the normal way to do it), startup silently runs on garbage unless a
  clear loop is added *and* the loader guarantees zero-fill of `memsz − filesz`.
  Any Strategy B design must decide this explicitly.
* **Entry offset:** `pongoos-src/src/kernel/entry.c:311` jumps to the *start* of the
  upload buffer (`jump_to_image_extended(loader_xfer_recv_data − kCacheableView +
  0x800000000, …)`). Verified: fresh-objcopy raw image bytes `[0:4] == 0x404238d5 ==
  ELF .text[0:4]` — `_start` is at image offset 0 (`*(.text.boot)` first in
  `kernel.ld:20`), so the buffer-start jump lands on `_start`.
  *Note:* the checked-in `build/myos_kernel.bin` (135,456 B, mtime 13:35) is **stale**
  relative to `build/myos_kernel.elf` (mtime 14:17) — `make kernel` only maintains the
  ELF. A fresh `objcopy -O binary` of the current ELF is 200,992 B (0x31120; it includes
  the 0xCFD0-byte zero gap between `.text` and `.data`). The QEMU baseline uses the ELF
  and is unaffected; any future raw-image artifact must be regenerated, not trusted from
  `build/myos_kernel.bin`.
* **Handoff ABI** (`pongoos-src/src/boot/jump_to_image.S`): `x0 = gBootArgs`,
  `x1–x3 = 0`, `x8 = original gEntryPoint`, `x30 = image entry (so the image can
  `ret` back)`, I-cache synced (`ic iallu`), entered at PongoOS’s EL (EL1 on A8).
  `start.S` expects EL1 or EL2 (`start.S:44–51`) and immediately replaces `sp`, so the
  incoming stack is not assumed. *(MMU/cache state at handoff after
  `lowlevel_cleanup()` was not fully audited here — out of scope, listed as a
  limitation in §6.)*
* **Image size vs. buffer:** raw image 200,992 B < default 1 MiB `bootr` upload
  buffer (`usbloader.c`, resizable to 128 MB) — fits.

---

## 3. Experiments performed (host-side, non-destructive)

All outputs went to `build/audit/` (git-ignored) or `/tmp`. The canonical
`build/*.o`, `build/myos_kernel.elf`, `build/myos_kernel.bin`, the Makefile, the
linker script, QEMU flags and the M2 image format were **not modified**. `git status`
before and after the audit shows the identical pre-existing change set.

**E1 — Flag fidelity.** Rebuild of all 5 objects with the exact Makefile flags →
`cmp` byte-identical to canonical (§0). *Proves:* the artifacts are the recorded
`-fno-pic` build.

**E2 — Raw-image absolute-pointer scan (§1.2).** Fresh `objcopy -O binary` →
`build/audit/myos_kernel.audit.bin` (200,992 B); scanned for qwords in link range →
**exactly 32**, all in the three tables of §1.2. *Proves:* the frozen-pointer set is
complete and tiny. *Does not prove* anything about runtime behavior.

**E3 — Temporary `-fPIC` / `-pie` audit build** (same sources, *not* the canonical
build; all under `build/audit/pic/`):

```
# same KERNEL_CROSSFLAGS but -fPIC instead of -fno-pic -fno-pie, then:
aarch64-linux-gnu-ld --hash-style=sysv -T boot/aarch64/kernel.ld -pie \
    --no-dynamic-linker -z noexecstack --build-id=none -o build/audit/pic/myos_kernel_pic.elf ...
```

Results:

* With the **canonical script unchanged**, the link succeeds but `ld` still reports
  `Type: EXEC` (the script pins `. = 0x40080000`), **while already emitting
  `.rela.dyn` with 35 `R_AARCH64_RELATIVE` entries** — plus orphan `.dynsym`,
  `.dynamic`, `.got` (0x20), `.got.plt` (0x18) sections.
* With an **audit-only copy** of the script (`sed 's/. = 0x40080000;/. = 0x0;/'` →
  `build/audit/pic/kernel_pic.ld`), the link produces a genuine
  **`Type: DYN`, entry `0x0`** ELF with the same **35 `R_AARCH64_RELATIVE`** and
  **no other relocation type** (no `GLOB_DAT`, no `JUMP_SLOT`, no `TEXTREL`, no
  undefined symbols — `nm -u` is empty).
* Attribution of the 35 fixups (from `readelf -rW` + `nm -n`):
  * 4 → `uart_pl011_ops+0x08..0x20`
  * 12 → `boot_stage_names` live entries
  * 16 → `subsys_names` + `type_names`
  * 3 → `_GLOBAL_OFFSET_TABLE_+0x08..0x18` (GOT slots for global-variable access)
  * i.e. **32 data pointers + 3 GOT slots** — precisely the set found in E2 plus the
    GOT `-fPIC` introduces.
* Loader rule for every entry: `*(u64*)fixup += runtime_base − link_base` (or
  `+= runtime_base` for a base-0 link). No symbol lookup, no additive/relative
  variants, nothing instruction-local.

*Proves:* the relocation surface for Strategy B is complete, uniform and small
(35 single-pass fixups), and an `ET_DYN` + `.rela.dyn` artifact builds from the existing
sources with the existing script plus a one-line base change.
*Does **not** prove* arbitrary-base execution — nothing was executed at a second base.

**E4 — QEMU baseline re-verification.** `make qemu` after all audit work:

```
MYOS_KERNEL_PASS
=== QEMU TEST: PASS ===   (make exit 0)
```

The established `MYOS_KERNEL_PASS` baseline is intact.

**Proposed-but-not-run experiment** (future work, §8): second-base QEMU boot.

---

## 4. Designs A, B, C — evaluated independently

### A. True PIC kernel (runs at any base, *no* runtime relocation)

* **Not what the current build is.** `-fno-pic -fno-pie` + `ET_EXEC` + fixed
  `. = 0x40080000`.
* **Not achievable by flags alone.** E3 shows `-fPIC` still emits 35 RELATIVE fixups:
  the 32 `const`-pointer slots are data-level absolute references that survive any
  code-model setting. The kernel *code* is already fully PC-relative (§1.3), so the
  blockers are exactly the three pointer tables of §1.2.
* Achievable *in principle* with source changes only, e.g.: build the console ops
  table into `.bss` at runtime (or dispatch via an `adrp`-based accessor), replace
  `boot_stage_names[]`/`subsys_names[]`/`type_names[]` arrays with accessor functions
  that materialize string addresses via `adrp`+`add`. Note the ops tables currently
  live in a `R E` segment — patching them in place would additionally require making
  that data writable, another reason to build them in `.bss`.
* Documented platform constraints either way: 2 KiB VBAR alignment, entry at image
  offset 0, ADR/ADRP/BL range limits, MMIO stays fixed physical, loader must supply
  zeroed BSS if the script is ever split.

### B. Relocatable kernel (loader applies a complete, well-defined table)

* **Does not exist in the canonical artifacts** — no `.rela.dyn`, no `.dynamic`
  (§1). A loader today has *nothing* to apply; the necessary addends were discarded
  at link time.
* **Feasible and fully characterized** (E3): 35 `R_AARCH64_RELATIVE` entries,
  single-pass delta rule, no symbol resolution, no TEXTREL; buildable from the
  existing sources with the existing script modulo the location counter.
* **Loader constraint:** stock `bootr`/`BOOT_FLAG_RAW` does **not** apply
  relocations (`entry.c:311` only jumps). So under Strategy B the “loader” is either
  (a) a self-relocation stub placed at image offset 0 that applies `.rela.dyn` before
  the first relocated pointer is used (first use = first `diag_puts` →
  `uart_pl011_ops`), or (b) a PongoOS modification — which is out of scope/prohibited
  in this phase. The stub approach needs no `pongoos-src` change but needs the script
  to expose the relocation range (e.g. `__rela_start/__rela_end` symbols or a parseable
  `PT_DYNAMIC`) — a future, auditable build change.

### C. Fixed-address kernel (must execute at its link address)

* **This is the status quo.** `ET_EXEC`, entry/link `0x40080000`, no relocation table,
  32 frozen pointers.
* It works **only** where the loader honors the ELF link address (QEMU `-kernel`
  does — that is exactly what the baseline proves). PongoOS’s dynamically placed
  `bootr` buffer cannot provide that: `entry.c:311` hard-jumps to the buffer start,
  and A4 already showed no fixed physical address is source-proven free. Therefore
  **Design C is incompatible with the existing `bootr` path** without an additional
  loading strategy (fixed-address reservation ⇒ requires PongoOS modification, i.e.
  A4’s NO-GO items).

---

## 5. What the existing artifacts prove — and do not prove

**Prove:**

1. The kernel boots at its link address under QEMU and prints `MYOS_KERNEL_PASS`
   (re-verified this audit, exit 0).
2. The canonical objects are exactly the recorded `-fno-pic -fno-pie` build (E1).
3. The final ELF is `ET_EXEC` with entry `0x40080000`, two `PT_LOAD`s, and **no
   relocation sections of any kind**; `filesz == memsz` for both segments; BSS is
   file-backed.
4. Every code reference is PC-relative (0 absolute immediates, 0 literal loads,
   149 ADR/ADRP uses); the complete set of frozen absolute data references is 32
   pointers in exactly three tables (E2), matching the 32 input `ABS64` relocations.
5. `_start` is at raw-image offset 0, satisfying `entry.c:311`’s buffer-start jump.
6. A `-fPIC -pie` variant builds with a complete 35-entry `R_AARCH64_RELATIVE` table
   and nothing else (E3).

**Do NOT prove (and must not be claimed):**

1. “The build accepts `-fPIC`” → nothing; the canonical build doesn’t even use it, and
   E3 shows `-fPIC` alone still leaves 35 fixups (it is *not* sufficient for Design A).
2. “The ELF is `ET_DYN`” → it is not (`ET_EXEC`); and even a true `ET_DYN` would still
   carry `.rela.dyn` that must be applied.
3. “Relocation sections are absent” → this is evidence **against** arbitrary-base
   execution, not for it: the relocations were resolved at link time and are no longer
   available to any runtime loader.
4. “It boots under QEMU at its original link address” → proves Design C only; QEMU
   never exercised a second base.
5. **No runtime behavior at any base ≠ `0x40080000` has been observed in this audit.**
   All statements about second-base behavior are static predictions from the pointer
   inventory, not measurements.

---

## 6. Limitations of this audit

* No second-base execution was performed (audit-only scope; see §8).
* The cache/MMU state PongoOS is in at `BOOT_FLAG_RAW` handoff
  (`lowlevel_cleanup()` → `jump_to_image_extended`) was not audited; relevant to
  *cache maintenance of the copied image*, not to PIC-ness.
* The audit covers the QEMU kernel target only (the M1 payload / `m1.ld` path is
  untouched and out of scope).
* MMIO (PL011 `0x09000000`) correctness on the iPhone is out of scope; the audit only
  verifies that MMIO constants are absolute-by-design and delta-invariant.

---

## 7. GO / NO-GO decision for Strategy B

> ### **GO for Strategy B — conditional on four explicit requirements.**
>
> The kernel’s relocation problem is *small, closed and fully enumerated*:
> **35 `R_AARCH64_RELATIVE` fixups (32 data pointers + 3 GOT slots), a single-pass
> `+= delta` rule, no symbol resolution**, with all code already PC-relative.
> No source-level redesign of the kernel is required, and no `pongoos-src` change is
> required **provided** relocation happens inside the image (self-relocation stub at
> offset 0) or as part of a future loader — implementing that loader is *not* part of
> this phase.
>
> **Conditions (all must hold before any device use):**
>
> 1. **Relocation table must exist in the artifact.** The canonical build produces
>    none. A Strategy B build must emit `.rela.dyn` (or an equivalent table exposed by
>    the linker script) — demonstrated feasible in E3, including the one-line
>    location-counter change needed for `ET_DYN`.
> 2. **Relocations must be applied before the first relocated pointer is dereferenced.**
>    First use is the first `diag_puts()` through `uart_pl011_ops`; a stub at image
>    offset 0 runs strictly before that. Entry-at-offset-0 must be preserved.
> 3. **Load constraints must be stated and checked:** base 2 KiB-aligned (VBAR), image
>    ≤ upload-buffer size (200,992 B ≤ 1 MiB today), whole image copied contiguously
>    (raw bin includes the `0xCFD0` text/data gap), MMIO constants excluded from
>    delta-adjustment.
> 4. **BSS policy must be decided explicitly:** today BSS is file-backed
>    (`filesz == memsz`) and there is *no* clear loop anywhere. If a Strategy B link
>    script makes `.bss` `NOBITS`, both a startup clear and loader zero-fill of
>    `memsz − filesz` become mandatory.
>
> **Explicit NO-GOs carried over:**
> * **NO-GO for running the *current* canonical image via `bootr`** — it is Design C
>   (32 frozen pointers, zero relocation data) and would crash at the first framework
>   print at any base ≠ `0x40080000`.
> * **NO-GO for claiming Design A today** — `-fPIC` is insufficient (E3), and no
>   source has been changed to remove the pointer tables.
> * **NO-GO for a fixed-address strategy on stock PongoOS** — unchanged from A4
>   (`entry.c:311` hardcodes the buffer jump; no fixed physical address is
>   source-proven free).

---

## 8. The smallest justified next experiment

**A second-base QEMU boot that does not touch the established baseline.** Keep
`make qemu` / `MYOS_KERNEL_PASS` exactly as-is; add a *separate*, clearly-named target
that:

1. Takes the audit raw image (`objcopy -O binary build/myos_kernel.elf` → 200,992 B;
   `_start` at offset 0, verified) and loads it at a second base inside QEMU `virt`
   RAM (e.g. `0x40200000`, 64 KiB-aligned and 2 KiB-aligned) via
   `qemu-system-aarch64 -device loader,file=…,addr=0x40200000` plus a loader/PC-set
   device (exact `-device loader` flags to be validated; QEMU 11.1.1 available).
2. Asserts on output:
   * **Prediction for the current (Design C) image:** early messages
     (`MyOS kernel entry`, stage strings from `early_uart_puts`) appear — they are
     PC-relative + fixed-MMIO — then output stops or garbage appears at the first
     `diag_puts()` dispatch through the stale `uart_pl011_ops` pointers
     (`0x400814a4` et al.). `MYOS_KERNEL_PASS` must **not** appear.
   * **Prediction once Strategy B is implemented (E3-style build + stub):**
     all 35 fixups applied → `MYOS_KERNEL_PASS` appears from the second base.
3. Runs both against the *same* QEMU invocation parameters as the baseline where
   possible, and reports the raw console log as evidence.

This single experiment discriminates Design C from Design B with a machine-checkable
signal, uses only host-side artifacts, and changes neither the QEMU baseline nor the
M2 image format. It should be run before any Strategy B implementation work.

---

## 9. Constraint compliance (this phase)

* `pongoos-src/` — **not modified** (read-only inspection of `entry.c`, `jump_to_image.S`).
* Canonical image format (`include/myos_image.h`) and M1 implementation — **not modified**.
* No kernel loader / allocator changes implemented.
* No device operations; no iPhone connection attempted.
* Nothing committed or pushed (`git status` identical before/after; audit outputs live
  in git-ignored `build/audit/` and `/tmp`).
* QEMU build and `MYOS_KERNEL_PASS` baseline preserved and **re-verified PASS** (E4).

### Audit artifacts produced (all under `build/audit/`, disposable)

| Path | What |
|---|---|
| `build/audit/rebuild/*.o` | E1 flag-fidelity rebuild (byte-identical) |
| `build/audit/myos_kernel.audit.bin` | fresh raw image used for the 32-pointer scan (200,992 B) |
| `build/audit/pic/*.o`, `myos_kernel_pic.elf` | E3 `-fPIC` build @ fixed base (`.rela.dyn`, 35 RELATIVE) |
| `build/audit/pic/myos_kernel_pic_dyn.elf`, `kernel_pic.ld` | E3 `ET_DYN` variant (audit-only script copy, base 0) |
