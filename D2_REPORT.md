# D2 Report — PongoOS Bootstrap Feasibility for iPhone 6 / A8 / CPID 0x7000

## Objective
Determine whether PongoOS is a realistic, documented, open-source bootstrap
bridge from the physical iPhone 6 DFU state (`05ac:1227`) toward MyOS-controlled
AArch64 execution on Apple A8 / T7000 / CPID `0x7000`, and define the next safe
experiment. Research only; no new bootstrap executed on the phone.

## Method
Primary-source review of github.com/checkra1n/PongoOS (README, Makefile,
`src/boot/entry.S`, `src/boot/stage3.c`, `src/shell/main.c`,
`src/shell/usbloader.c`, `src/dynamic/modload.c`, `src/dynamic/modload_macho.c`,
`src/drivers/usb/usb.h`, `example/testmodule/*`) and checkra.in /
checkra.in/linux. Cross-checked against the A8 checkm8 profile already vendored
in `tools/external/gaster/gaster.c` (CPID `0x7000`, SRTG `iBoot-1992.0.0.1.19`)
and a host toolchain audit. Every claim is classified
CONFIRMED BY SOURCE / INFERRED / UNKNOWN / NOT APPLICABLE (full table in
D2_FEASIBILITY.md).

## Key Findings
1. **PongoOS is open-source (MIT) and supports A8/iPhone 6.** checkra.in:
   "Jailbreak for iPhone 5s through iPhone X, iOS 12.0 and up." iPhone 6
   (A8, CPID 0x7000) is in range. CONFIRMED BY SOURCE.
2. **The handoff contract is well-defined.** checkra1n's stage1 trampoline
   branches to PongoOS `entry.S` `start` (from x27/x29); PongoOS self-copies
   to a fixed high address (~0x180000000) and demotes to EL1 via `_setup_el1`
   (handling both EL1 and EL3). `boot_args==NULL` → bootloader mode;
   `boot_args!=NULL` → kernel mode. CONFIRMED BY SOURCE.
3. **The module path (D2a) is production-shaped.** PongoOS has a Mach-O module
   loader (`modload`) with a `__DATA,__pongo_exports` section, a
   `resolve_symbol` API, and an official `example/testmodule` implementing
   `module_entry()` + `command_register` + `puts()`. CONFIRMED BY SOURCE.
4. **The observable channel is a real USB console.** PongoOS exposes a USB
   shell via vendor requests (`0x21` OUT: upload/stdin/control; `0xA1` IN:
   stdout/async-status), giving a `puts()` → PC channel far more observable
   than the gaster magic-value probe. CONFIRMED BY SOURCE.
5. **D2 does NOT escape checkm8.** checkra1n loads PongoOS via
   `checkra1n -k Pongo.bin`, which first requires checkra1n's checkm8
   (CVE-2019-8900) — the same BootROM exploit gaster uses. The device-side
   is identical; only the host-side implementation differs (closed-source
   product-quality vs open-source). INFERRED (from the documented load path).
6. **checkra1n is closed-source (binary only).** Its host-side checkm8 cannot
   be audited. PongoOS/patchfinder/SEP are open-source; the checkra1n app is
   not. CONFIRMED BY SOURCE.
7. **Linux checkm8 has device-specific caveats** (A7 excluded on Linux), and
   checkm8 reliability is host-dependent. A8 is not excluded, but success is
   not guaranteed. CONFIRMED BY SOURCE / INFERRED.
8. **Toolchain gaps are identified and fillable.** Missing: `clang`, `ld64`
   (required, not lld — PongoOS uses Mach-O kext/preload/order_file flags),
   `cctools-strip`, `newlib` (submodule), `python-pyusb`, `checkra1n` CLI
   binary. Present: `g++`, `gcc`, `make`, `git`, `curl`, `wget`, `tar`,
   `python3`, `libusb`, `yay`. CONFIRMED BY AUDIT.

## The D2 Chain
PC → checkra1n (closed-source checkm8) → checkm8 CVE-2019-8900 (same as
gaster) → pwned DFU → `checkra1n -k Pongo.bin` → PongoOS (EL1, MMU, USB
shell, newlib) → host USB-shell client → `modload myos.module` →
`module_entry()` → `puts("MYOS M1 OK")` → PC.

## Integration Sub-Paths
- **D2a (RECOMMENDED):** MyOS as a PongoOS Mach-O module. Inherits EL1/MMU/
  drivers/newlib/USB console; observable proof is a `puts()` string. Backed by
  `example/testmodule`.
- **D2b:** MyOS as a custom `checkra1n -k` image. Must satisfy the trampoline
  entry contract and bring up its own console/drivers. Harder, no free console.
  Deferred until D2a is proven.

## Limitations
- D2 does not escape the checkm8 dependency (the gating unknown).
- checkra1n is closed-source (binary only).
- Semi-tethered: a reboot without checkra1n reverts to stock iOS.
- Linux checkm8 has device-specific caveats; reliability is host-dependent.
- checkm8/PongoOS are RAM-only and tethered (this is what makes the next
  experiment reversible).

## Decision: D2-GO
PongoOS is a credible, source-confirmed bridge (open-source MIT, A8 in the
checkra1n support range, documented module system with an official example,
USB shell + `puts()` observable channel). A concrete, safe, reversible,
minimal next experiment exists that directly tests the open question
(whether checkra1n's checkm8 completes on this host). All prerequisites for
the next experiment are identifiable and fillable.

**Caveat carried forward:** D2 shares M1's checkm8 dependency. If checkra1n's
checkm8 also fails on this host, D2 collapses to D2-BLOCKED at the same root
cause as M1 (host-level / device-side checkm8 failure). The next experiment is
designed to detect exactly this, cheaply and reversibly, before any MyOS code
is written.

## Next Experiment (defined, NOT executed)
See D2_RUNBOOK.md. Two phases:
- **Phase 1 (offline, no device):** install/build `clang`, `ld64` (from
  Siguza/ld64), `cctools-strip`, fetch the `newlib` submodule, build PongoOS
  → `build/Pongo.bin`; verify it is a flat AArch64 binary.
- **Phase 2 (physical, safe/reversible):** download the checkra1n Linux x86_64
  CLI binary; device in DFU (`05ac:1227`); run `checkra1n -k Pongo.bin` in
  the docker container (root USB access); observe via `lsusb` whether a new
  PongoOS USB-shell device enumerates; connect a host-side USB-shell client
  (pongoterm or a libusb client built from the documented `usbloader.c`
  protocol) and observe the PongoOS shell; then `modload` the official
  `example/testmodule` and observe its `puts()` output on the PC.

**PASS criterion:** checkra1n reaches pwned DFU, PongoOS boots, its USB shell
is reachable, and a PongoOS module's `puts()` output is observed on the PC —
proving MyOS-controlled AArch64 code executes on the A8 via the PongoOS module
path.

**FAIL criterion:** checkra1n's checkm8 does not reach pwned DFU (same symptom
as M1) → D2-BLOCKED at the same root cause as M1.

## Open Questions (UNKNOWN)
- Whether checkra1n's checkm8 completes on this host (the gating unknown).
- The exact USB PID of the PongoOS shell device (observe via lsusb).
- The exact checkra1n CLI flags for a headless `-k` run (confirm from
  `checkra1n --help`).
- Whether a Linux pongoterm client exists, or one must be written from the
  documented `usbloader.c` protocol via libusb.

## References
- PongoOS: https://github.com/checkra1n/PongoOS (MIT)
  - Makefile, src/boot/entry.S, src/boot/stage3.c, src/shell/main.c,
    src/shell/usbloader.c, src/dynamic/modload.c, src/dynamic/modload_macho.c,
    src/drivers/usb/usb.h, example/testmodule/Makefile, example/testmodule/main.c
- checkra1n: https://checkra.in/ and https://checkra.in/linux
- Vendored A8 checkm8 profile: tools/external/gaster/gaster.c L595-609
- Related project docs: BOOT_FEASIBILITY.md, TOOLCHAIN.md, ARCHITECTURE.md,
  M1_PLAN.md, M1_REPORT.md, M1_RUNBOOK.md, D2_PLAN.md, D2_FEASIBILITY.md,
  D2_RUNBOOK.md
