# TOOLCHAIN.md — Host toolchain audit (Arch Linux x86_64)

Date: 2026-10-08
Host: Arch Linux, x86_64, user `khoi` (wheel, docker groups)
Method: direct invocation of each tool + `pacman -Q` (no packages installed,
no system modified during this audit).

---

## 1. Installed tools (measured on this host)

| Tool | Version / state | Notes |
|---|---|---|
| `gcc` | 16.2.1 (`/usr/bin/gcc`) | native x86_64 compiler |
| `aarch64-linux-gnu-gcc` | 16.1.0 (`/usr/bin/aarch64-linux-gnu-gcc`) | **cross toolchain for AArch64 — already present** |
| `aarch64-linux-gnu-ld` | present (`/usr/bin/aarch64-linux-gnu-ld`) | binutils 2.47 |
| `binutils` | 2.47-4 | |
| `qemu-system-aarch64` | 11.1.1 | **dev/test only** (see QEMU policy) |
| `make` | 4.4.1 | |
| `python` / `python3` | 3.14.7 | exploit tooling (ipwndfu = Python) |
| `git` | 2.56.0 | |
| `lsusb` (usbutils) | 019 | device enumeration — works, we used it |
| `libusb` | 1.0.30 (`/usr/lib/libusb-1.0.so`) | C USB access — present (gaster/checkra1n-class tools need this) |
| `libimobiledevice` | 1.4.0 | `idevice*` tools + `usbmuxd` present |
| `github-cli` | 2.102.0 | |
| `llvm-libs` | 23.1.1 | runtime libs only — **clang/lld binaries NOT installed** |

**Device check (this host, today):** `Bus 001 Device 010: ID 05ac:1227 Apple,
Inc. Mobile Device (DFU Mode)` — iPhone 6 visible in DFU, `iSerial` readable
(`CPID:7000 ... SRTG:[iBoot-1992.0.0.1.19]`). USB path to the target works.

### Missing on this host
| Tool | State |
|---|---|
| `clang`, `lld` (`ld.lld`), `llvm` | MISSING (only `llvm-libs` installed) |
| `cmake` | MISSING |
| `dfu-util` | MISSING |
| `irecovery` | MISSING (libirecovery not packaged by Arch's libimobiledevice) |
| `python-pyusb` (`import usb`) | MISSING — required by ipwndfu-class Python exploit scripts |
| checkra1n / gaster / ipwndfu | not installed (not found in PATH) |

---

## 2. Tools actually REQUIRED (by milestone)

### Milestone 1 — "our first instruction runs on the A8" (see ARCHITECTURE.md §10)
| Tool | Why | State |
|---|---|---|
| `aarch64-linux-gnu-gcc` (+ ld) **or** `clang --target=aarch64-none-elf` | build the bare-metal AArch64 payload | cross-gcc **PRESENT** |
| `make` | build orchestration | PRESENT |
| `python3` + **`python-pyusb`** | run/port the checkm8 host harness (ipwndfu is Python) | python PRESENT, pyusb MISSING |
| `libusb` | C USB stack for gaster-class tools | PRESENT |
| `lsusb` / usbutils | verify DFU enumeration | PRESENT |
| a checkm8 host tool | exploit driver — **vendored into the project** (`tools/checkm8/`), not installed system-wide | to be fetched into project |
| `git` | fetch upstream references | PRESENT |

Exact Arch packages to install when authorized:
```
sudo pacman -S --needed clang lld python-pyusb dfu-util
```
- `clang` + `lld`: gives a freestanding AArch64 cross-compiler with
  `--target=aarch64-none-elf -nostdlib` (no OS headers assumed) and is also
  what pongoOS itself expects upstream (pongoOS builds with clang + ld64).
- `python-pyusb`: in [extra]; used by ipwndfu-style harnesses.
- `dfu-util`: optional-but-cheap DFU sanity tool (Apple DFU is not standard
  DFU-1.1, so treat `dfu-util` as a probe, not the main driver).

Needed only if we build pongoOS itself from source (not required for
milestone 1 if we use a prebuilt path or `-k` custom image):
```
# pongoOS upstream also expects: ld64 + cctools (strip). Not in Arch repos;
# build locally inside the project if/when we take that route.
```

### Milestone 2+ (later, listed for planning only)
- `cmake` — only if a vendored dependency wants it. (optional today)
- `libirecovery` built from libimobiledevice source, or `irecovery`
  equivalent — handy for reading SRTG/ECID without raw lsusb. (optional)

---

## 3. Tools OPTIONAL (nice to have, not blocking)

- `dfu-util` — DFU enumeration/probing convenience.
- `cmake` — only for specific vendored third-party builds.
- `libirecovery`/`irecovery` — convenience for DFU string reads.
- AUR `checkra1n` — the official chain driver; upstream ships a Debian
  package, Arch users go via AUR or a container. We can alternatively use a
  raw gaster/ipwndfu harness and skip checkra1n entirely for milestone 1.

## 4. Tools UNNECESSARY (do not install)

- `aarch64-linux-gnu` **glibc** runtime/sysroot — we build freestanding
  bare-metal code; no Linux-on-ARM userland needed on the target.
- Android tooling (`adb`, `fastboot`), iOS backup/forensics suites.
- `gcc-multilib`, cross toolchains for other architectures.
- Any GUI tooling (Xcode-style, iTunes).
- `qemu-system-aarch64` is present and fine **as a test harness**, but no
  QEMU-specific packages beyond what's installed should be added, and QEMU
  must never define the target hardware model (see QEMU policy).

## 5. Policy notes

1. Nothing was installed or modified on the host during this audit.
2. When installation is authorized, use the single `pacman -S --needed ...`
   line in §2 — nothing else.
3. Exploit/boot tooling is vendored into `/home/khoi/Projects/myos/tools/`
   (project-local), so the host stays clean and the repo is reproducible.
4. The physical iPhone is never flashed/erased/restored by this project's
   tooling; all USB operations are DFU-session-local (tethered, RAM-only).
