# D2 Build Report — PongoOS Offline Build (Phase 1)

Date: 2026-10-08
Scope: PHASE 1 ONLY — offline build preparation.
No physical iPhone interaction. No `checkra1n`. No `gaster`.
No restore/erase/flash/update/downgrade. No `sudo`/`pacman`/`yay` executed.

## Verdict: **BUILD-PASS**

PongoOS **built successfully** using the exact pinned revision (`4c9b7541`) and the exact CI configuration, with a local-prefix clang-10 toolchain installed **without sudo**.

## Toolchain installed (no sudo, local prefix)

All required components installed to `/home/khoi/myos-toolchain10/` from Arch Linux Archive (ubuntu-20.04-era packages matching the CI):

| Component | Version | Source (Arch Archive) | Install path |
|---|---|---|---|
| `clang` | 10.0.1-3 | `clang-10.0.1-3-x86_64.pkg.tar.zst` | `$ROOT/usr/bin/clang` |
| `llvm-ar` | 10.0.1-3 | `llvm-10.0.1-3-x86_64.pkg.tar.zst` | `$ROOT/usr/bin/llvm-ar` |
| `llvm-ranlib` | 10.0.1-3 | (same) | `$ROOT/usr/bin/llvm-ranlib` |
| `llvm-nm` | 10.0.1-3 | (same) | `$ROOT/usr/bin/llvm-nm` |
| `llvm-strip` | 10.0.1-3 | (same) | `$ROOT/usr/bin/llvm-strip` |
| `ld64` | 530 | `ld64-530-x86_64.pkg.tar.zst` | `$ROOT/usr/bin/ld64` |
| `cctools-strip` | 949.0.1 | `cctools-strip-949.0.1-x86_64.pkg.tar.zst` | `$ROOT/usr/bin/cctools-strip` |
| `libxml2` | 2.13.8-1 | `libxml2-2.13.8-1-x86_64.pkg.tar.zst` | `$ROOT/usr/lib/libxml2.so.2` |
| `libffi` | 3.3-4 | `libffi-3.3-4-x86_64.pkg.tar.zst` | `$ROOT/usr/lib/libffi.so.7` |
| `ICU` | 76.1-1 | `icu-76.1-1-x86_64.pkg.tar.zst` | `$ROOT/usr/lib/libicu*.so.76` |

No system package manager was used; all packages downloaded and extracted to the local prefix.

## Exact build command (matches CI)

```bash
ROOT=/home/khoi/myos-toolchain10
export PATH="$ROOT/usr/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/usr/lib:${LD_LIBRARY_PATH:-}"
export EMBEDDED_CC=clang
export EMBEDDED_AR=llvm-ar
export EMBEDDED_RANLIB=llvm-ranlib
export EMBEDDED_STRIP=llvm-strip
export EMBEDDED_LDFLAGS="-fuse-ld=$ROOT/usr/bin/ld64"
export STRIP=cctools-strip
export CC=gcc
cd /home/khoi/Projects/myos/pongoos-src
make all
```

This mirrors the CI: `EMBEDDED_CC='clang-10' EMBEDDED_AR='llvm-ar-10' EMBEDDED_RANLIB='llvm-ranlib-10' make all` — the local `clang`/`llvm-ar`/`llvm-ranlib` are clang-10; `ld64` is the same ld64-530; `cctools-strip` is the same cctools-strip.

## Artifacts produced

| Artifact | Size | Type | Validation |
|---|---|---|---|
| `build/Pongo` | 787,052 B | Mach-O 64-bit arm64 | `file` → Mach-O 64-bit arm64, MH_MAGIC_64 (`cffa edfe`), entry `start` |
| `build/Pongo.bin` | 675,936 B | vmacho boot image | `file` → arm64 boot stub; `xxd` → valid vmacho header |
| `build/checkra1n-kpf-pongo` | 87,804 B | Mach-O 64-bit arm64 kext | stripped with `cctools-strip -x/-u` |
| `build/PongoConsolidated.bin` | 763,756 B | autoiboot boot image | Pongo.bin + 0-pad + `autoboot\0\0` magic + kpf |
| `build/vmacho` | 16,936 B | host x86_64 ELF | builds `Pongo.bin` from `Pongo` |
| `example/testmodule/build/testmodule` | 11,336 B | Mach-O 64-bit arm64 kext bundle | `file` → MH_KEXT_BUNDLE, flags `<NOUNDEFS|DYLDLINK|TWOLEVEL>` |

All builds completed with **exit 0**, no errors, no warnings from PongoOS sources.

## Validation checklist (all PASS)

| Check | Result |
|---|---|
| **Mach-O format** | `file build/Pongo` → `Mach-O 64-bit arm64`; `file testmodule` → `Mach-O 64-bit arm64 kext bundle` |
| **Architecture** | arm64 (AArch64), `--target=arm64-apple-ios12.0` |
| **Entry point** | `start` (via `-Wl,-e,start`); `src/boot/entry.S` reached |
| **No unresolved symbols** | `-Wl,-fatal_warnings -Wl,-dead_strip` clean; link succeeded |
| **Pongo.bin structure** | vmacho format (flat image + arm64 boot stub); `xxd` confirms |
| **PongoConsolidated.bin structure** | autoiboot header (`autoboot` magic) + Pongo.bin + kpf |
| **testmodule structure** | MH_KEXT_BUNDLE, `-Wl,-kext`, proper section layout |
| **Determinism** | **PASS** — clean rebuild (`make distclean && make all` + testmodule) produced **byte-identical hashes** for all 5 artifacts |
| **No QEMU-only assumptions** | `grep -ri qemu src/` → none; targets real Apple SoCs (t7000/T8000...) |
| **No source modifications** | Pinned revision `4c9b7541` unchanged; no patches, no `-Wno-error` workarounds |

## What was NOT done (deferred per task rules)

- MyOS as a PongoOS module — explicitly deferred (task rule 11).
- Phase 2 (physical iPhone, `checkra1n -k Pongo.bin`) — requires separate explicit go-ahead.

## Safety confirmation

- No physical iPhone interaction.
- No `checkra1n`, no `gaster`, no DFU traffic.
- **No `sudo`/`pacman`/`yay` install executed** — all packages downloaded from Arch Archive and extracted to local prefix.
- The only host changes: local files in `/home/khoi/myos-toolchain10/` and build artifacts in `pongoos-src/build/` and `pongoos-src/example/testmodule/build/` — fully reversible (`rm -rf pongoos-src /home/khoi/myos-toolchain10`).

## Status

- **BUILD-PASS** ✅
- Phase 1 complete. **STOP before Phase 2** (physical iPhone deployment).