# M-P2.31 — Same-Toolchain PongoOS A/B Build Audit (summary)

Offline audit (no device). Full report + raw evidence: `/tmp/m-p2.31/`.
This document records the durable, reusable finding only; no binaries or logs are tracked.

## Goal

M-P2.29 found that the old and new PongoOS Mach-Os differ at many symbol offsets, but the
two binaries were built by **different toolchains**. M-P2.31 rebuilds both source revisions
with **one identical toolchain** to separate the effect of the source change from toolchain
variation.

- old source: `4c9b7541629234147fcc778f0ce4162482aaccef` (no `regs`)
- new source: `cb83dd83452d5107628b32eeb462222609f565a6` (adds `src/shell/regs.c`; `main.c` +2)
- old is the direct parent of new; the whole diff is `main.c` +2 / `regs.c` +107.

## Toolchain actually used

The exact M-P2.26 Ubuntu toolchain (`clang-10 1:10.0.0-4ubuntu1`, `ld64 954.16-0`) is **not
reproducible offline** — it was installed over the network from apt/checkra1n, Docker build
cache is empty, and no `.deb`s are cached (verified: `docker run --network none ubuntu:20.04
apt-get install clang-10` → `E: Unable to locate package clang-10`). So the audit uses the
on-disk Arch toolchain that already produced `pongoos-src/build/`:

| tool | version | sha256 (prefix) |
|---|---|---|
| `clang-10` | 10.0.1 | `0b1a91cd18806a66` |
| `ld64` | `ld64-530` | `08d9799e4d53ba43` |
| `llvm-ar` / `llvm-ranlib` | LLVM 10.0.1 | `8ba34b025c9d8c13` |
| `cctools-strip` | cctools | `a27944d0d2e7b1fa` |

Both revisions were built with this **same** toolchain (identical binary hashes), from clean
`git archive` exports, with a shared, once-built newlib (`libc.a`
`17bc4dd0b6b619fcc335…`) so newlib is not a variable. Overall status: **PARTIAL** (controlled
A/B complete; exact M-P2.26 toolchain not reproduced offline).

## Result: toolchain variation dwarfs the source change

| comparison | common symbols | changed offsets | unchanged | distinct deltas |
|---|---|---|---|---|
| **A/B** old vs new (one toolchain) | 2281 | **423 (18.5%)** | 1858 | 5 (0, +28, +224, +228, +412) |
| **reproducibility** new vs new2 | 2286 | **0** | 2286 | 1 (0) |
| **cross-toolchain** new(local) vs new(Ubuntu) | 2282 | **1977 (86.6%)** | 305 | 308 (≈ −8240 … +1736) |

- Net symbol change from the `regs` source diff: **+1** (`_regs_cmd`). `regs_commands_register`
  is inlined away by LTO; `_command_table.*`/`_gAES*.*` merely renumber.
- `__text`: 514504 → 514732 (**+228 B**) for one new object; `__TEXT`/`__DATA` vmsize unchanged.
- `checkra1n-kpf-pongo` is **byte-identical** between old and new — the change touches only the
  Pongo shell.
- Builds are **deterministic** (`new` == `new2` byte-for-byte across all four artifacts).

Reproducibility validation against the existing artifact: rebuilding old with the local
toolchain reproduces `pongoos-src/build/checkra1n-kpf-pongo` **byte-exactly**, and its `Pongo`
symbol table matches to **2 offsets** (residual byte diffs are only the embedded
`PONGO_VERSION` string — `2.5.1-4c9b7541` in `build/` vs `2.5.1-` for clean exports — plus a
different cached newlib).

The same source built with two toolchains differs by ~**one 0x4000 page** in `Pongo` size
(linker alignment), while `__text` size is identical across toolchains.

## Conclusion

The M-P2.29 "thousands of symbol offsets" result is **dominated by toolchain variation, not by
the `regs` addition**. The `regs` change is structurally small and localized (+1 symbol,
+228 B `__text`). Attribution of the M-P2.29 address deltas to the source change alone was not
supported, and is now superseded by this controlled measurement.

Hardware compatibility remains **UNKNOWN**: this is a build/offline audit only, not evidence
that PongoOS boots on Apple A8. No device was connected or contacted.

## Method / reproduce

```sh
# clean exports
git -C pongoos-src archive <rev> | tar -x -C /tmp/m-p2.31/work/<rev>
git -C pongoos-src/newlib archive HEAD | tar -x -C /tmp/m-p2.31/work/<rev>/newlib

# build (identical env for both revisions; local toolchain TC=/home/khoi/myos-toolchain10)
env -i PATH=/usr/bin:/bin \
  LD_LIBRARY_PATH="$TC/usr/lib:$TC/usr/lib64" \
  EMBEDDED_CC="$TC/usr/bin/clang-10" EMBEDDED_AR="$TC/usr/bin/llvm-ar" \
  EMBEDDED_RANLIB="$TC/usr/bin/llvm-ranlib" \
  EMBEDDED_LDFLAGS="-fuse-ld=$TC/usr/bin/ld64" \
  STRIP="$TC/usr/bin/cctools-strip" CC=cc \
  make -o newlib/aarch64-none-darwin/lib/libc.a all
```

Offline comparison/test drivers: `/tmp/m-p2.31/compare_symbols_m231.py`,
`compare_binary_m231.py`, plus the existing `tests/test_pongo_*.py` suites (all pass).
