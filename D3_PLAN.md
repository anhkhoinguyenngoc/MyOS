# D3 Plan — Diagnose and Retry checkm8 on Arch Linux

Date: 2026-10-08
Scope: Diagnose the `-76` failure after DFU abort, test safe mitigations, one controlled retest.

---

## Objective

Determine whether the `Failed to open USB interface: (No such file or directory) (error code: -76)` failure is caused by:
1. **Userspace/udev** (driver binding, usbmuxd, permissions, libusb version)
2. **Kernel/USB controller** (xHCI re-enumeration timing, Intel 100/C230 xHCI quirks)

Then perform **ONE controlled retest** after applying the minimal safe fix identified.

---

## Phase 1 — Diagnostics (Completed)

Documented in `D3_DIAGNOSTICS.md`:
- Intel 100/C230 xHCI controller (no EHCI fallback)
- `apple-mfi-fastcharge` driver bound to DFU interface
- `usbmuxd` udev rules match DFU 1227
- Kernel shows `unbind` of interface during exploit
- Error `-76` (LIBUSB_ERROR_NO_DEVICE) after DFU abort

---

## Phase 2 — Safe Mitigation Tests (Sequential, Reversible)

### Test A: Unload apple-mfi-fastcharge driver
```bash
sudo modprobe -r apple_mfi_fastcharge
# Verify: udevadm info -n /dev/bus/usb/001/XXX | grep DRIVER
# Retry checkra1n (one attempt)
# Restore: sudo modprobe apple_mfi_fastcharge
```

### Test B: Stop usbmuxd service
```bash
sudo systemctl stop usbmuxd
# Retry checkra1n (one attempt)
# Restore: sudo systemctl start usbmuxd
```

### Test C: Combined A + B
```bash
sudo modprobe -r apple_mfi_fastcharge
sudo systemctl stop usbmuxd
# Retry checkra1n with udevadm monitor
# Restore both
```

### Test D: Ubuntu 20.04 container (userspace test)
```bash
docker run --rm -i --device /dev/bus/usb --privileged \
  -v /home/khoi/Projects/myos/pongoos-src/build:/pongo \
  -v /tmp:/checkra1n \
  ubuntu:20.04 /bin/bash -c "apt-get update -qq && apt-get install -y -qq libusb-1.0-0 >/dev/null && /checkra1n/checkra1n -c -p -k /pongo/Pongo.bin -v"
```
Tests whether userspace (libusb/udev/systemd) differs while keeping same kernel.

---

## Phase 3 — Controlled Retest

**Only after a mitigation shows promise** (i.e., the `-76` error does not occur or progresses further):

1. Confirm iPhone in DFU: `lsusb | grep 05ac:1227`
2. Apply the working mitigation
3. Run **ONE** checkra1n attempt with udevadm monitor
4. Observe:
   - Does `-76` occur?
   - Does device re-enumerate with new PID?
   - Does PongoOS load?
   - Does PongoOS shell enumerate?
5. If PongoOS executes → load official testmodule → verify puts()

---

## Failure Criteria

If **all mitigations fail** (same `-76` at same point):

**Classify as `D3-BLOCKED-HOST-USB`**

Document:
- Exact failure point unchanged
- Same kernel/USB controller limitation
- Different physical host required
- No further retries on this host

---

## Deliverables

- `D3_DIAGNOSTICS.md` (completed)
- `D3_PLAN.md` (this file)
- `D3_REPORT.md` (final results)

---

## Safety Checklist

- ✅ No kernel modifications
- ✅ No persistent udev rule changes
- ✅ No kernel parameter changes
- ✅ Temporary module unload (auto-restores on reboot)
- ✅ Temporary service stop (auto-restores on reboot)
- ✅ One controlled retest per mitigation
- ❌ No brute-force retries
- ❌ No exploit code modification
- ❌ No device restore/flash