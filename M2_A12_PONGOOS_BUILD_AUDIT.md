# MyOS M2-A12 — Reproducible PongoOS Apple-Target Build Audit

**Audit date:** 2026-10-11 (Asia/Ho_Chi_Minh)  
**Repository:** `/home/khoi/Projects/myos`  
**Decision:** `CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

This audit is build/offline-emulator evidence only. No physical device was
accessed, and no boot fix or production build configuration was implemented.

## 1. Provenance and preservation

The verified repository root is `/home/khoi/Projects/myos`, branch `main`,
HEAD `3bea11141fea05bb90017069cff22b9a37d91e9e`.

The pre-existing user changes were preserved. They included the modified
`Makefile`, `arch/aarch64/boot_diag.c`, `arch/aarch64/boot_diag.h`, and
`arch/aarch64/vectors.S`, plus the pre-existing untracked reports, kernel,
boot, include, and test files. No protected source file was edited by this
audit.

`pongoos-src` is a nested Git checkout rather than a registered top-level Git
submodule in this repository (`git submodule status --recursive` has no
entries). Its clean revision is:

| Tree | Revision | Commit subject |
|---|---|---|
| `pongoos-src` | `4c9b7541629234147fcc778f0ce4162482aaccef` | This should definitely have been changed too... |
| `pongoos-src/newlib` | `feaa8fcf90de497485b74e6f65fd78a58a27b9d7` | Damn multilib |

Both nested worktrees were clean before and after the audit. Their pre-existing
ignored `build/` outputs were not used as proof of a clean build.

## 2. Supported Apple-target build traced

The checked-out source was inspected directly:

- `pongoos-src/Makefile:31-40` selects Clang, `ld64`, cctools `strip`, and
  Linux `stat` behavior.
- `pongoos-src/Makefile:44-59` defines the version, target
  `arm64-apple-ios12.0`, freestanding/LLVM-LTO flags, Mach-O preload link,
  image base `0x100000000`, and segment alignment `0x4000`.
- `pongoos-src/Makefile:90-116` builds `Pongo`, converts it to `Pongo.bin`,
  builds the KPF module, and concatenates the consolidated image.
- `pongoos-src/newlib/Makefile:22-29` selects the same Apple target and
  Newlib's `clang`, `llvm-ar`, and `llvm-ranlib` tools.
- `pongoos-src/newlib/Makefile:55-73` shows the exact Newlib configure
  options used by the parent build.
- `pongoos-src/README.md:10-35` documents the Linux dependencies and the
  four Apple/PongoOS artifacts. `README.md:37-41` documents checkra1n as the
  execution/transport path, not generic QEMU.

The Arch Linux host is x86_64. Relevant versions were:

| Tool | Version/evidence |
|---|---|
| Clang/LLVM | 10.0.1, local toolchain at `/home/khoi/myos-toolchain10/usr/bin` |
| GNU cross compiler | `aarch64-linux-gnu-gcc` 16.1.0 |
| GNU linker | binutils 2.47 |
| GNU Make | 4.4.1 |
| QEMU | 11.1.1 |
| Git | 2.56.0 |

The exact executable hashes used were:

```text
clang-10       0b1a91cd18806a66d206af6c55f7b4512be804660c87c7ded97bda3941d2a87d
ld64           08d9799e4d53ba431221a59dca98526c2cab7c456802af8606bacc2524c58bb4
cctools-strip  a27944d0d2e7b1faf3141e989524039ace2bc53b02723bc3ad2494aeb9770588
llvm-ar        8ba34b025c9d8c136f07463b3ac808adac9031da4e193b59733cd85749b7b028
llvm-ranlib    8ba34b025c9d8c136f07463b3ac808adac9031da4e193b59733cd85749b7b028
```

The toolchain directory is locally present but is not owned by the current
Arch package database. Its root `.PKGINFO` describes `libffi`, not the LLVM
and cctools binaries, and `pacman -Qo` reports no owner for those binaries.
Therefore the binary hashes and versions are pinned for this audit, but the
toolchain's package provenance is not independently established.

## 3. Reproducible build procedure and results

The clean source snapshots were made outside the PongoOS checkout and excluded
`.git`, generated PongoOS `build/`, generated Newlib `build/`, and generated
`newlib/aarch64-none-darwin/` outputs. No dependency was downloaded.

The snapshot command was:

```sh
tar --exclude='./.git' --exclude='./build' \
  --exclude='./newlib/build' \
  --exclude='./newlib/aarch64-none-darwin' \
  -C pongoos-src -cf - . | tar -C "$SRC" -xf -
```

The successful build command, with `SRC` and `BUILD` set to an isolated
absolute path, was:

```sh
TOOL=/home/khoi/myos-toolchain10/usr
PATH="$TOOL/bin:/usr/bin:/bin" \
PONGO_VERSION=2.5.1-4c9b7541 \
EMBEDDED_CC="$TOOL/bin/clang" \
EMBEDDED_LDFLAGS="-fuse-ld=$TOOL/bin/ld64" \
STRIP="$TOOL/bin/cctools-strip" \
LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1636057726 \
make -C "$SRC" BUILD="$BUILD" -j1 all
```

The first isolated attempt using the Makefile's tool names without the pinned
toolchain directory failed with exit `2`; the first reproducible error was
`clang: command not found` (and subsequently missing `llvm-ar`). This was a
host `PATH` configuration failure, not an Apple-target compiler failure.

Using the pinned toolchain, two independent clean source snapshots built
successfully with exit `0`. Their output hashes differed only because Clang
embedded the different absolute Newlib source paths, for example
`.../source-snapshot-toolchain1/.../dtoa.c` versus
`.../source-snapshot-toolchain2/.../dtoa.c`. This proves that arbitrary staging
paths are not byte-reproducible with the unmodified upstream flags.

The same source and build absolute paths were then built, `make distclean` was
run, and the same command was run again. Results:

- clean build 1: exit `0`
- `make distclean`: exit `0`
- clean build 2: exit `0`
- all four PongoOS artifacts: byte-for-byte identical (`cmp` exit `0`)

The reproducible fixed-path artifact set is under
`build/audit/m2-a12/repro/build/`:

| Artifact | Size | SHA-256 |
|---|---:|---|
| `Pongo` | 787192 | `24cd343f587ce95f845edff2a06a10cc33bb75ec1291f760e56e73d530610fa6` |
| `Pongo.bin` | 675936 | `7d3f3e09ca031c34d7ae0d79f0794b111de60fc32567b7991802ce4b1125678e` |
| `PongoConsolidated.bin` | 763748 | `841ff2f920bb361d649f6ebf2a43f3b6202c00f0a34c630bea70457fc9682f3b` |
| `checkra1n-kpf-pongo` | 87796 | `e179e4078410c3cc16e6b71309059781a36983049366b55e4ccebc9d81eac0de` |
| `newlib/.../libc.a` | 799312 | `872c95f80226b275b571384b8a2dc2bd1b395194c310ab7931eee79004df0de4` |

The complete raw build logs and the first/second hash manifests are in
`build/audit/m2-a12/repro/` and `build/audit/m2-a12/build-logs/`.

## 4. Execution and fidelity

The Apple-target build is proven to produce the expected Mach-O and raw
artifacts. It is not proof that the artifacts execute in a generic ARM64
machine or that PongoOS's Apple `bootr` handoff occurred.

Bounded generic QEMU probes were run with:

```text
timeout 3 qemu-system-aarch64 -machine virt -cpu cortex-a57 -nographic -kernel Pongo.bin
timeout 3 qemu-system-aarch64 -machine virt -cpu cortex-a57 -nographic -kernel PongoConsolidated.bin
```

Both exited `124` with only QEMU's timeout termination message and no PongoOS
marker. This is an expected non-proof: `virt` does not reproduce the Apple
pre-boot memory map, device tree, exception state, allocator state, transport,
or checkra1n/iBoot transfer path documented by this source revision.

No available offline emulator or simulator was shown to faithfully execute
PongoOS's actual Apple `bootr` path. Consequently, `CurrentEL`, `VBAR_EL1`,
MMU/cache state, upload-buffer lifetime, boot-argument aliases, and allocator
overlap remain runtime UNKNOWN. The successful generic MyOS fixture tests are
tests of the isolated A10 trampoline, not PongoOS.

## 5. Independent M2-A10 checks

The existing A10 runner was reused read-only, with all generated fixtures and
logs redirected under `build/audit/m2-a12/`. The runner verified each fixture's
embedded package byte-for-byte before QEMU execution. Because the runner uses a
local QMP socket under `/tmp`, these checks required elevated sandbox
permission; no external system or physical device was used.

The package layout and monitored ranges were derived from the current A10
manifest and bootstrap ELF:

```text
scratch:          [0x1648, 0x1720)  img symbol, nm-derived
bootstrap image:  [0x0000, 0x1648) and [0x1720, 0x1810)
tail padding:     [0x1810, 0x2000)
package header:   [0x2000, 0x2200)
header padding:   [0x2200, 0x3000)
payload:          [0x3000, 0x45960), with 35 relocation slots carved out
payload tail:     [0x45960, 0x48000)
stack:            [0x48000, 0x4c000), tracked separately
BSS:              [0x1810, 0x1810), empty in this artifact
```

The scratch range was not hardcoded. It was read from `nm -S` for `img` and
cross-checked against the ELF `.data` section. Every case still monitored all
header, payload, relocation-target, bootstrap, padding, and BSS ranges.

### Eight malformed cases

All rows are `PASS` from the runner. Every QEMU result had exit `124` only
after the expected rejection marker was observed, and every RAM dump was
readable. `protected writes` is the exact violation count; all are zero.

| Case | Generated fixture size / SHA-256 | Observed rejection | Scratch diffs | Protected writes |
|---|---:|---|---:|---:|
| `trunc_header` | 140424 / `e02d6295ee3fa15cbb2d4a6897fe6a861dac4ea440cea44621fed67d16bafe89` | `package header magic mismatch (truncated/corrupt package)` | 0 | 0 |
| `trunc_payload` | 328840 / `8cb2f450724251bd7253d42d489442f5639a1f7b449133b4f22ffee08f9764db` | `payload checksum mismatch (truncated/corrupt payload)` | 0 | 0 |
| `seg_bounds` | 443520 / `27a73496b93a99a3695eccca1096cadd8bf780ad5ae066ae844b4a5995e1ebf8` | `PT_LOAD file range outside payload bounds` | 7 | 0 |
| `overflow` | 443520 / `86e5b8d3d8448a571cde69f56e74b989ad67fc01663cfa16e134bedcea1d27fc` | `PT_LOAD memory footprint outside payload bounds` | 7 | 0 |
| `bad_reloc_type` | 443536 / `9ca1a7560a3d9974aa67a2d5c53d1a9d2a9a708dde3c0af0dc7a87b5b601f620` | `unsupported relocation type` | 30 | 0 |
| `reloc_oob` | 443520 / `c4fb2be0768f9222b4ba2cae1f4dcb11f7863dc1e1df2f0c4fcd1adb63038a0f` | `relocation target outside permitted memory` | 30 | 0 |
| `entry_oob` | 443520 / `493e037b35ba3b530a82ecf84140337273038183abcbd2557adf6bce58777fc4` | `entry point outside executable memory` | 19 | 0 |
| `bss_invalid` | 443528 / `05d71a7a1059d88fa44cea2cc57118cd723bc4d05861feeec8d7d2f10ba07012` | `invalid BSS range (p_filesz > p_memsz)` | 7 | 0 |

The generated malformed package input hashes are recorded in
`build/audit/m2-a12/a10-rerun-escalated/logs/*.json`; their resolved package
paths are the A10 `build/neg/*.bin` files and were recorded in each result.
Each case was run with the existing runner in the following form, changing
only the case tag, package, expected message, and fixture name:

```sh
python3 /home/khoi/Projects/myos/build/audit/m2-a10/run_case.py \
  --tag CASE_a12 --package /home/khoi/Projects/myos/build/audit/m2-a10/build/neg/CASE.bin \
  --base 0x42000000 --expect-reject 'EXPECTED_MESSAGE' \
  --outdir /home/khoi/Projects/myos/build/audit/m2-a12/a10-rerun-escalated \
  --fixture /home/khoi/Projects/myos/build/audit/m2-a12/a10-rerun-escalated/fixtures/fixture_42000000_CASE.elf \
  --rebuild-fixture --timeout 12
```

### Clean and corruption controls

| Control | Result | Fixture SHA-256 | Evidence |
|---|---|---|---|
| Clean valid package | `PASS` | `834867d7afb7b656c51cc91f95e0b762c117b01379ff130582eb4393b5b520ba` | `MYOS_KERNEL_PASS`, exit `124`, 30 scratch diffs, 0 protected writes |
| Injected corruption | `FAIL` as intended | `70d5d56056f2576a74637354c8acbf03e2dd49a43811326f1c6df4bb97be4f43` | rejection observed and 8 exact writes at `0x42005000..0x42005007`, values `4b 52 41 4d 31 41 32 4d` |
| Detection bypass control | `FAIL` as required | same as injected | `--require-detection` failed on the eight bypassed offsets, so a log-only control cannot pass |

The injected package is isolated at
`build/audit/m2-a10/negcontrol/package_inject.bin`; the canonical valid package
hash remained unchanged at
`26a1e1f647298785c26e620fb53b0befd5ecdad3e4626f73a16c62504acfa4d1`.

## 6. Regression results

| Command | Exit | Result |
|---|---:|---|
| `make qemu` | 0 | PASS; `MYOS_KERNEL_PASS` observed |
| `make m2-check` | 0 | PASS; load-plan check completed, with its hypothetical-runtime caveat |
| `bash tests/test_boot_diag.sh` | 0 | PASS; all offline tests passed |
| A10 eight-case suite | 0 per case | 8/8 PASS, as above |
| A10 clean control | 0 | PASS |
| A10 injected control | 1 | intentional detection failure |
| A10 bypass control | 1 | intentional required-detection failure |
| `bash tests/offline_m1.sh` | 1 | pre-existing `FAIL_m1ctl_usage` |
| `bash tests/test_boot_diag_extended.sh` | 1 | pre-existing missing `.text.vectors` |

The canonical package was SHA-256
`26a1e1f647298785c26e620fb53b0befd5ecdad3e4626f73a16c62504acfa4d1` both
before and after the regression commands. No production source, fixture, or
PongoOS checkout was modified.

The exact regression commands were:

```sh
make qemu
make m2-check
bash tests/test_boot_diag.sh
bash tests/offline_m1.sh
bash tests/test_boot_diag_extended.sh
```

## 7. Remaining blockers and recommendation

The pinned Apple-target build is now independently reproducible when the exact
absolute source/build path is held constant, and the A10 memory-integrity and
negative-case evidence is trustworthy. However:

1. The local LLVM/cctools directory lacks independently verified package
   provenance.
2. Upstream flags embed absolute Newlib source paths, so arbitrary staging
   paths do not produce identical hashes. A future audit recipe needs explicit
   path normalization (for example, an isolated `-ffile-prefix-map` build) if
   relocatable reproducibility is required.
3. Generic `virt` QEMU cannot establish the actual PongoOS Apple `bootr`
   runtime contract. Exception level, vectors, MMU/cache state, upload-buffer
   lifetime, boot arguments, and allocator reservations remain unresolved.
4. No result here supports a claim of physical iPhone compatibility.

### Final recommendation

`CONDITIONAL_GO_REQUIRES_MORE_EVIDENCE`

**Recommended next action:** create one isolated offline PongoOS handoff
harness that executes the checked-out handoff assembly with deterministic
synthetic allocator/upload/boot-argument state and emits register, mapping,
lifetime, and overlap traces; use its output to define the minimum runtime
contract before considering any physical experiment.

## 8. Audit artifacts and exact changed files

The report and all generated A12 evidence are located at:

- `M2_A12_PONGOOS_BUILD_AUDIT.md`
- `build/audit/m2-a12/` (ignored audit snapshots, logs, fixtures, hashes, and
  build outputs)

The only repository file created by this task is
`M2_A12_PONGOOS_BUILD_AUDIT.md`. No protected source file was changed.
