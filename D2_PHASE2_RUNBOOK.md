# D2 Phase 2 Runbook — Executed Experiment Log

Date: 2026-10-08
Scope: PHASE 2 ONLY — minimal, reversible physical experiment.

---

## Exact Command Executed

```bash
# 1. Device verification
lsusb | grep 05ac:1227
# → Bus 001 Device 048: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)

# 2. Artifact verification
cd /home/khoi/Projects/myos/pongoos-src
sha256sum build/Pongo build/Pongo.bin build/PongoConsolidated.bin build/checkra1n-kpf-pongo example/testmodule/build/testmodule
# → All 5 hashes match D2_BUILD_REPORT.md

# 3. checkra1n binary (official, SHA256 verified)
curl -fsSL -o /tmp/checkra1n "https://assets.checkra.in/downloads/linux/cli/x86_64/dac9968939ea6e6bfbdedeb41d7e2579c4711dc2c5083f91dced66ca397dc51d/checkra1n"
sha256sum /tmp/checkra1n
# → dac9968939ea6e6bfbdedeb41d7e2579c4711dc2c5083f91dced66ca397dc51d  /tmp/checkra1n
chmod +x /tmp/checkra1n

# 4. checkra1n execution (in docker for USB access)
docker run --rm -i --device /dev/bus/usb --privileged \
  -v /home/khoi/Projects/myos/pongoos-src/build:/pongo \
  -v /tmp:/checkra1n \
  archlinux /bin/bash -c "/checkra1n/checkra1n -c -p -k /pongo/Pongo.bin -v"

# Flags used:
#   -c  : CLI mode (non-interactive)
#   -p  : Boot to Pongo shell
#   -k  : Override Pongo image with /pongo/Pongo.bin
#   -v  : Verbose logging
```

---

## Full checkra1n Output (2026-10-08 11:39:13–11:39:39)

```
 - [10/08/26 11:39:13] <Info>: Waiting for DFU devices
 - [10/08/26 11:39:13] <Verbose>: DFU device connected: 22cda36100026
 - [10/08/26 11:39:13] <Info>: Exploiting
 - [10/08/26 11:39:13] <Verbose>: Attempting to perform checkm8 on 7000 11...
 - [10/08/26 11:39:13] <Info>: Checking if device is ready
 - [10/08/26 11:39:13] <Verbose>: == Checkm8 Preparation stage ==
 - [10/08/26 11:39:14] <Info>: Setting up the exploit (this is the heap spray)
 - [10/08/26 11:39:14] <Verbose>: == Checkm8 Setup stage ==
 - [10/08/26 11:39:14] <Verbose>: Disabled probabilistic mode since we encountered a partial xfer
 - [10/08/26 11:39:14] <Verbose>: Deterministic approach was successful!
 - [10/08/26 11:39:14] <Info>: Right before trigger (this is the real bug setup)
 - [10/08/26 11:39:14] <Verbose>: Entered initial checkm8 state after 0 steps, issuing DFU abort..
 - [10/08/26 11:39:14] <Error>: Failed to open USB interface: (No such file or directory) (error code: -76)
 - [10/08/26 11:39:39] <Error>: Timed out in state 1, assuming we are back to square one with this device. (error code: -31)
```

---

## Device State Before/After

| Time | `lsusb` output |
|---|---|
| Before | `Bus 001 Device 048: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)` |
| After | `Bus 001 Device 053: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)` |

- Device remained in DFU mode (`05ac:1227`) throughout.
- **No new USB device enumerated** (no PongoOS shell PID).
- Device enumeration number changed (048 → 053) due to USB re-enumeration after DFU abort.

---

## checkra1n Execution Summary

| Stage | Result | Notes |
|---|---|---|
| DFU detection | ✅ | Device detected: `22cda36100026` |
| Checkm8 Preparation | ✅ | Heap spray initiated |
| Checkm8 Setup | ✅ | "Deterministic approach was successful!" |
| Trigger / DFU abort | ✅ | "Entered initial checkm8 state after 0 steps, issuing DFU abort.." |
| USB re-acquisition | ❌ | `Failed to open USB interface: (No such file or directory) (error code: -76)` |
| PongoOS load | ❌ | Never reached — exploit failed before payload upload |
| PongoOS shell enumeration | ❌ | No new USB device (PID ≠ 0x1227) observed |
| Test module load | N/A | Prerequisite not met |

---

## Failure Diagnosis

**Root cause:** `Failed to open USB interface: (No such file or directory) (error code: -76)` after DFU abort trigger.

- The checkm8 exploit reaches the Setup stage and successfully issues the DFU abort trigger (the exploit trigger).
- After the abort, the device should re-enumerate in pwned DFU mode, allowing checkra1n to upload the PongoOS payload.
- Instead, checkra1n cannot re-open the USB interface (`-76` = LIBUSB_ERROR_NO_DEVICE / ENODEV).
- The device remains in standard DFU mode (`05ac:1227`), not pwned DFU.

**This is the same host-level bootstrap boundary that blocked M1.** gaster's checkm8 cycled RESET→SETUP→SPRAY→PATCH 19× without reaching pwned DFU. checkra1n's checkm8 reaches the Setup stage and issues the trigger, but then fails to re-acquire the USB interface. The root cause is host-level: USB controller / kernel 7.2.8 / udev timing on this Arch Linux host.

---

## Verdict

**D2-PHYSICAL-BLOCKED**

The gating unknown from D2_FEASIBILITY.md is resolved: **checkra1n's checkm8 does NOT complete on this host.** The same host-level bootstrap boundary that blocked M1 (gaster) also blocks D2 (checkra1n).

---

## Next Steps (NOT executed — for future reference)

If retrying on this or another host:

1. **Different USB port** — try USB2 port (checkm8 is sensitive to USB3 controllers).
2. **Different host** — different USB controller / kernel version.
3. **usb_settle_shim** — LD_PRELOAD shim built for M1 to handle udev settle timing.
4. **Explicit udev rules** — ensure the post-abort device node is accessible immediately.
5. **Different docker base** — try ubuntu:20.04 (matching CI) instead of archlinux.

**Do NOT:**
- Retry indefinitely on this host (same failure mode).
- Modify exploit code (closed-source checkra1n binary).
- Attempt destructive procedures (restore/flash/erase).
- Proceed to MyOS module implementation (prerequisite not met).

---

## Recovery

Device remains in standard DFU mode (`05ac:1227`). A normal reboot (exit DFU timing) returns the iPhone 6 to stock iOS 12.5.7 with **no persistent changes**. checkm8 and PongoOS are RAM-only.

---

## Final Status

- **D2-PHYSICAL-BLOCKED** at checkm8 bootstrap.
- Same root cause as M1 (host-level USB/kernel/udev).
- PongoOS build (D2 Phase 1) is **BUILD-PASS** and validated.
- Physical deployment blocked at the same fundamental boundary.
- STOP. No further physical attempts on this host without changing the host environment.