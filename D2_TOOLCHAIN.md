# D2 Toolchain — PongoOS Build Dependency Verification (Phase 1, OFFLINE)

Date: 2026-10-08
Scope: OFFLINE build preparation only. No physical iPhone interaction.
No `sudo`/`pacman`/`yay` was executed. Package queries (`pacman -Ss`,
`yay -Ss`) are read-only and were used only to locate the exact packages.

## Host environment

- Arch Linux x86_64, kernel 7.2.8-arch1-2
- User `khoi` (groups: docker, wheel); `sudo` requires a password
  (unavailable to automation) → privileged installs must be run manually.
- AUR helper `yay` present.

## PongoOS source under inspection

- Cloned (shallow) to `pongoos-src/` for primary-source verification.
- **Exact revision:** commit `4c9b7541629234147fcc778f0ce4162482aaccef`
  (short `4c9b754`), dated 2021-11-04, subject "This should definitely
  have been changed too...". PongoOS master has not moved since
  2021-11-04 (project effectively frozen at this commit).
- **PONGO_VERSION** (Makefile L44): `2.5.1-$(git log -1 | cut -c1-8)`
  → **`2.5.1-4c9b7541`**.
- **newlib submodule** (`.gitmodules` → `https://github.com/checkra1n/newlib.git`):
  fetched via `git submodule update --init --recursive`, checked out at
  commit `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` (newlib 4.1.0-based,
  "bare metal AArch64 with Darwin ABI (aarch64-none-darwin)").

## A8 / iPhone 6 support — CONFIRMED BY SOURCE (at 4c9b7541)

- `src/drivers/plat/t7000.c:52`: `.name = "Apple A8 (T7000)"`;
  `:30`: `if (device->cpid == 0x7000)` — dedicated T7000 platform driver.
- `src/kernel/entry.c:137`: `else if(strcmp(soc_name, "t7000") == 0)
  socnum = 0x7000;` — SoC detection maps t7000 → CPID 0x7000.
- `src/drivers/aes/aes.c:154` and `src/drivers/aes/aes_a7.c:56`:
  `case 0x7000:` — AES driver handles the A8.
- `src/drivers/recfg/recfg_soc.c:160`: `if(socnum == 0x8960 || socnum ==
  0x7000 || socnum == 0x7001)` — recfg handles the A8.
⇒ The exact target SoC (Apple A8 / T7000 / CPID 0x7000) is explicitly
supported by this PongoOS revision.

## Required toolchain (from the actual Makefiles)

PongoOS `Makefile` (Linux branch, L36-41) and `newlib/Makefile`
(L14-19) and `example/testmodule/Makefile` (L36-41) all require:

| Variable | Makefile default (Linux) | Used by |
|---|---|---|
| `EMBEDDED_CC` | `clang` (L37) | Pongo, checkra1n-kpf-pongo, newlib, testmodule |
| `EMBEDDED_LDFLAGS` | `-fuse-ld=/usr/bin/ld64` (L38) | Pongo, checkra1n-kpf-pongo, newlib, testmodule |
| `STRIP` | `cctools-strip` (L39) | checkra1n-kpf-pongo (PongoOS Makefile L103-104) |
| `EMBEDDED_AR` | `llvm-ar` (newlib L17) | newlib build |
| `EMBEDDED_RANLIB` | `llvm-ranlib` (newlib L18) | newlib build |

Key Mach-O-specific linker flags that mandate ld64 (PongoOS Makefile L54,
L58, L62): `-Wl,-preload`, `-Wl,-no_uuid`, `-Wl,-order_file`,
`-Wl,-image_base`, `-Wl,-sectalign`, `-Wl,-segalign`, `-Wl,-kext`,
`-Wl,-dead_strip`, `-Wl,-Z`, `-Wl,-fatal_warnings`.

### lld is NOT a supported substitute (task rule #4)

- `grep -rn lld` across `Makefile`, `newlib/Makefile`,
  `example/testmodule/Makefile` → **zero references**.
- The linker is pinned to the absolute path `/usr/bin/ld64`
  (`-fuse-ld=/usr/bin/ld64`). lld (`ld.lld`) is neither referenced nor
  does it support the full Mach-O kext/preload/order_file flag set.
⇒ **ld64 is required; lld must not be substituted.**

## Component-by-component verification (INSTALLED ✅)

| Component | Status | Installed at | Package source (Arch Archive) |
|---|---|---|---|
| `clang` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/clang` (10.0.1) | `clang-10.0.1-3` + `llvm-10.0.1-3` + `llvm-libs-10.0.1-3` |
| `llvm-ar` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/llvm-ar` | `llvm-10.0.1-3` |
| `llvm-ranlib` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/llvm-ranlib` | `llvm-10.0.1-3` |
| `llvm-nm` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/llvm-nm` | `llvm-10.0.1-3` |
| `llvm-strip` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/llvm-strip` | `llvm-10.0.1-3` |
| `ld64` (at `/usr/bin/ld64` equivalent) | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/ld64` (530) | `ld64-530` (AUR) |
| `cctools-strip` | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/bin/cctools-strip` (949.0.1) | `cctools-strip-949.0.1` (AUR) |
| `libxml2` (for clang-10) | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/lib/libxml2.so.2` | `libxml2-2.13.8-1` |
| `libffi` (for clang-10, soname .so.7) | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/lib/libffi.so.7` | `libffi-3.3-4` |
| `ICU` (for libxml2 2.13.8, soname .so.76) | **INSTALLED** | `/home/khoi/myos-toolchain10/usr/lib/libicu*.so.76` | `icu-76.1-1` |
| `newlib` submodule | **FETCHED & BUILT** (commit `feaa8fcf90de497485b74e6f65fd78a58a27b9d7`) | `pongoos-src/newlib/aarch64-none-darwin/` | `git submodule update --init --recursive` (no sudo) |
| `xxd` (PongoConsolidated.bin) | present (`/usr/bin/xxd`) | — | — |
| host `cc`/`gcc` (builds `tools/vmacho.c`) | present (`/usr/bin/gcc` 16.2.1) | — | — |
| `python3` | present (3.14.7) | — | — |
| `python-pyusb` (Phase-2 host USB-shell client) | **MISSING** | — | Arch `extra/python-pyusb` (optional until Phase 2) |
| `libusb-1.0` dev | present (1.0.30) | — | — |
| `lsusb` (usbutils) | present (019) | — | — |
| `g++`, `make`, `git`, `curl`, `wget`, `tar` | present | — | — |
| `aarch64-linux-gnu-gcc/ld/strip` (binutils 2.47) | present | NOT usable for PongoOS (wrong ABI/linker; PongoOS needs the Apple arm64 Darwin ABI via clang+ld64) | — |
| `cmake`, `ninja` | MISSING | not required by the PongoOS build system | — |

## Exact install method (NO sudo — local prefix)

```bash
# Toolchain prefix
ROOT=/home/khoi/myos-toolchain10
mkdir -p "$ROOT" /tmp/pkgs10

# Download from Arch Linux Archive (ubuntu-20.04-era packages matching CI)
ARC="https://archive.archlinux.org/packages"
for pkg in clang-10.0.1-3 llvm-10.0.1-3 llvm-libs-10.0.1-3 ld64-530 \
           cctools-strip-949.0.1 libxml2-2.13.8-1 libffi-3.3-4 icu-76.1-1; do
  curl -fsSL -o "/tmp/pkgs10/${pkg}-x86_64.pkg.tar.zst" "$ARC/$(echo $pkg | cut -d- -f1)/${pkg}/${pkg}-x86_64.pkg.tar.zst"
  tar --zstd -xf "/tmp/pkgs10/${pkg}-x86_64.pkg.tar.zst" -C "$ROOT"
done

# Verify toolchain
export PATH="$ROOT/usr/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/usr/lib:${LD_LIBRARY_PATH:-}"
clang --version          # → clang version 10.0.1
llvm-ar --version        # → LLVM
llvm-ranlib --version    # → LLVM
ld64 -v                  # → @(#)PROGRAM:ld  PROJECT:ld64-530
cctools-strip 2>&1       # → works (no args → usage error is expected)
```

No `sudo`, no `pacman -S`, no `yay -S`. All packages downloaded from
`archive.archlinux.org` and extracted to the local prefix.

## Build commands (validated, working)

```bash
# 1) Build PongoOS (newlib auto-rebuilt by Makefile)
cd /home/khoi/Projects/myos/pongoos-src
export ROOT=/home/khoi/myos-toolchain10
export PATH="$ROOT/usr/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/usr/lib:${LD_LIBRARY_PATH:-}"
export EMBEDDED_CC=clang
export EMBEDDED_AR=llvm-ar
export EMBEDDED_RANLIB=llvm-ranlib
export EMBEDDED_STRIP=llvm-strip
export EMBEDDED_LDFLAGS="-fuse-ld=$ROOT/usr/bin/ld64"
export STRIP=cctools-strip
export CC=gcc
make all
# Expected artifacts:
#   build/Pongo            (Mach-O 64-bit arm64)
#   build/Pongo.bin        (vmacho boot image)
#   build/checkra1n-kpf-pongo
#   build/PongoConsolidated.bin (autoiboot image)

# 2) Build the official example module
cd /home/khoi/Projects/myos/pongoos-src/example/testmodule
make
# Expected artifact: build/testmodule (Mach-O 64-bit arm64 kext bundle)
```

## Validation results

All builds complete with **exit 0**. Artifacts structurally validated:
- `Pongo`: Mach-O 64-bit arm64, MH_MAGIC_64, entry `start`
- `Pongo.bin`: vmacho format (arm64 boot stub)
- `PongoConsolidated.bin`: autoiboot image (Pongo.bin + magic + kpf)
- `checkra1n-kpf-pongo`: Mach-O kext (stripped)
- `testmodule`: Mach-O 64-bit arm64 kext bundle, MH_KEXT_BUNDLE
- **Determinism**: PASS — clean rebuild yields byte-identical hashes for all 5 artifacts

## Notes

- The `aarch64-linux-gnu-*` binutils cross-toolchain present on this host
  targets the Linux ABI and `ld` (GNU ld), NOT the Apple arm64 Darwin ABI
  with ld64. It cannot build PongoOS and must not be used as a substitute.
- `newlib` was built as part of `make all` (forced by `always` phony target);
  output at `newlib/aarch64-none-darwin/{include,lib}`.
- No privileged command was run. No host package state was modified.
- All packages from Arch Archive (read-only mirror); local extraction only.

## Status

- **ALL REQUIRED TOOLS INSTALLED AND VERIFIED** ✅
- BUILD-PASS achieved with exact CI configuration.
- Phase 1 complete. STOP before Phase 2.