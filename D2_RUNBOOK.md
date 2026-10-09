# D2 Runbook — Next Experiment (DEFINED, NOT EXECUTED)

> **This runbook is a plan. It is NOT executed in the D2 phase.**
> D2 is RESEARCH ONLY. Execute these steps only after explicit go-ahead.
> Phase 1 is offline (no device). Phase 2 touches the device but is
> safe and reversible (RAM-only, tethered; reboot returns to stock iOS).

## Purpose
Answer the single gating unknown from D2_FEASIBILITY.md: **does checkra1n's
checkm8 reach pwned DFU on this host, and does PongoOS then boot and expose
its USB shell?** If yes, the D2 bridge (DFU → checkm8 → PongoOS → module
execution on A8) is proven before any MyOS code is attempted.

## Safety / Reversibility
- checkm8 and PongoOS are RAM-only. Nothing is written to persistent storage.
- A device reboot (or DFU exit) returns the iPhone 6 to stock iOS 12.5.7.
- No erase, restore, update, downgrade, or flash occurs at any point.
- The device is only ever in DFU mode (`05ac:1227`) or the PongoOS USB-shell
  state. Both are non-destructive.
- Run checkra1n inside the docker container (validated path to root USB
  access, since `sudo` is unavailable and the user is in the `docker` group).

## Prerequisites

### Phase 1 — offline build (no device)
Install/build the PongoOS toolchain. **Do NOT auto-run `sudo`/`pacman`/`yay`;
these are the exact commands for manual approval:**
```
# 1. clang (PongoOS cross compiler)
yay -S clang            # or: sudo pacman -S clang

# 2. ld64 (Apple Mach-O linker, REQUIRED — lld does not support the
#    Mach-O kext/preload/order_file flags PongoOS uses)
git clone https://github.com/Siguza/ld64.git
cd ld64 && make && sudo make install     # builds with the present g++

# 3. cctools-strip (Mach-O strip)
git clone https://github.com/tpoechtrager/cctools-port.git   # or apple-oss-distributions/cctools
# build cctools, install cctools-strip

# 4. python-pyusb (host-side USB-shell client, if not using pongoterm)
yay -S python-pyusb       # or: sudo pacman -S python-pyusb

# 5. checkra1n Linux x86_64 CLI binary
#    Download from https://checkra.in/linux (CLI build for x86_64).
#    Note: assets.checkra.in returned HTTP 403 to plain curl; use a browser
#    or the documented download path.
```
Then build PongoOS:
```
git clone --recursive https://github.com/checkra1n/PongoOS.git
cd PongoOS
git submodule update --init --recursive     # newlib
EMBEDDED_CC=clang \
EMBEDDED_LDFLAGS=-fuse-ld=/usr/bin/ld64 \
STRIP=cctools-strip \
make all
# Expected artifacts: build/Pongo (Mach-O), build/Pongo.bin (bare metal),
#                     build/checkra1n-kpf-pongo, build/PongoConsolidated.bin
file build/Pongo.bin      # verify it is a flat AArch64 binary
```

### Phase 2 — physical test (the actual experiment; NOT executed now)
```
# 1. Put the iPhone 6 into DFU mode; verify passive detection:
lsusb | grep 05ac:1227
#    Expected: Bus 001 Device NNN: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)

# 2. Run checkra1n in the docker container (root USB access):
docker run --rm -it --device /dev/bus/usb --privileged \
  -v /path/to/PongoOS/build:/pongo \
  -v /path/to/checkra1n:/checkra1n \
  archlinux /bin/bash
#    inside the container:
/checkra1n/checkra1n --help            # confirm -k / -c CLI flags
/checkra1n/checkra1n -k /pongo/Pongo.bin -c
#    -k loads Pongo.bin; -c = CLI/console mode (verify exact flags from --help)

# 3. Observe: does checkra1n reach pwned state? Does a new USB device
#    enumerate (the PongoOS shell)?
lsusb          # look for a NEW Apple device (PID != 0x1227) — the PongoOS shell
#    (the exact PongoOS shell PID is UNKNOWN; record whatever appears)

# 4. Connect a host-side USB-shell client (pongoterm, or a libusb client
#    written from the documented usbloader.c protocol):
#    - bmRequestType 0x21, bRequest 1, wLength 0        -> bulk upload init
#    - bulk OUT ep2                                      -> upload myos.module
#    - bmRequestType 0x21, bRequest 3, wLength=len      -> write "modload\n" to stdin
#    - bmRequestType 0xA1, bRequest 1, wLength 512/0x1000 -> read stdout
#    - bmRequestType 0xA1, bRequest 2, wLength 1        -> async completion status

# 5. First milestone: observe the PongoOS shell banner / prompt over USB.
# 6. Second milestone: modload the official example/testmodule and observe
#    "Called pre-boot hook" / "Hello world!".
```

## PASS / FAIL Criteria
- **PASS (D2 bridge proven):** checkra1n reaches pwned DFU, PongoOS boots,
  its USB shell enumerates and is reachable from the PC, AND a PongoOS
  module's `puts()` output is observed on the PC. This proves MyOS-controlled
  AArch64 code can execute on the A8 via the PongoOS module path.
- **PARTIAL:** PongoOS boots and the shell enumerates, but the host client
  cannot read stdout → fix the client (protocol is documented; PID is
  observable via lsusb).
- **FAIL (D2-BLOCKED):** checkra1n's checkm8 does not reach pwned DFU on
  this host (same symptom as M1: repeated RESET/SETUP/SPRAY/PATCH cycles,
  no pwned state). This confirms the blocker is host-level / device-side,
  not gaster-specific. D2 collapses to D2-BLOCKED at the same root cause as M1.

## Failure Diagnosis
- **checkra1n never reaches pwned:** host-level checkm8 failure (USB
  controller, kernel 7.2.8, udev timing). Same class as the M1 gaster
  failure. Try: a different USB port (USB2 vs USB3), a different host, the
  docker `--privileged` path, or the `usb_settle_shim` LD_PRELOAD approach
  already built for M1.
- **PongoOS boots but no USB shell:** the PongoOS USB driver did not
  enumerate; check `lsusb` for any new device, verify the Synopsys OTG driver
  initialized for the A8.
- **Shell up but no stdout:** host-client protocol mismatch; re-read
  `src/shell/usbloader.c` `ep0_device_request` and match the vendor requests
  exactly (0x21/0xA1, bRequest, wLength).
- **modload fails:** the module Mach-O is malformed; rebuild with
  `-Wl,-kext` and verify the `__DATA,__pongo_exports` section exists
  (`objdump -h` / `otool -l` equivalent).

## Recovery
- If the device is stuck, exit DFU (release the DFU timing) or reboot the
  iPhone 6. Because checkm8/PongoOS are RAM-only, the device returns to stock
  iOS 12.5.7 with no persistent changes.
- If checkra1n hangs, kill the process; the device remains in DFU and can be
  re-detected.

## What NOT To Do
- Do NOT run `checkra1n` with any flag that writes to the device (no `--restore`,
  no `--update`, no `--downgrade`). Only `-k <image>` (load to RAM) is used.
- Do NOT attempt D2b (custom MyOS image) until D2a (module) is proven.
- Do NOT auto-run the Phase 1 install commands without approval.
