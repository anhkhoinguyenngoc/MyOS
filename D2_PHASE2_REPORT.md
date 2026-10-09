# D2 Phase 2 Report — Physical PongoOS Experiment

Date: 2026-10-08
Scope: PHASE 2 ONLY — minimal, reversible physical experiment.
No restore/erase/flash/update/downgrade. No persistent changes. RAM-only/tethered.

## Verdict: **D2-PHYSICAL-BLOCKED**

The checkm8 exploit (CVE-2019-8900) in checkra1n 0.12.4 **does not reach pwned DFU on this host**. The failure is at the same bootstrap boundary that blocked M1 (gaster).

---

## Attempt 1 — Original USB Port (2026-10-08 11:39:13–11:39:39)

**Command:**
```bash
docker run --rm -i --device /dev/bus/usb --privileged \
  -v /home/khoi/Projects/myos/pongoos-src/build:/pongo \
  -v /tmp:/checkra1n \
  archlinux /bin/bash -c "/checkra1n/checkra1n -c -p -k /pongo/Pongo.bin -v"
```

**Result:** `Failed to open USB interface: (No such file or directory) (error code: -76)` after DFU abort. Device remained in DFU (`05ac:1227`). No PongoOS shell.

---

## Attempt 2 — Different USB Port (2026-10-08 11:52:07–11:52:33)

**USB port changed:** Moved to direct motherboard/rear USB port.

**Device before:** `Bus 001 Device 058: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)`

**Result:** **IDENTICAL FAILURE**

```
 - [10/08/26 11:52:07] <Info>: Waiting for DFU devices
 - [10/08/26 11:52:07] <Verbose>: DFU device connected: 22cda36100026
 - [10/08/26 11:52:07] <Info>: Exploiting
 - [10/08/26 11:52:07] <Verbose>: Attempting to perform checkm8 on 7000 11...
 - [10/08/26 11:52:07] <Info>: Checking if device is ready
 - [10/08/26 11:52:07] <Verbose>: == Checkm8 Preparation stage ==
 - [10/08/26 11:52:08] <Info>: Setting up the exploit (this is the heap spray)
 - [10/08/26 11:52:08] <Verbose>: == Checkm8 Setup stage ==
 - [10/08/26 11:52:08] <Verbose>: Disabled probabilistic mode since we encountered a partial xfer
 - [10/08/26 11:52:08] <Verbose>: Deterministic approach was successful!
 - [10/08/26 11:52:08] <Info>: Right before trigger (this is the real bug setup)
 - [10/08/26 11:52:08] <Verbose>: Entered initial checkm8 state after 0 steps, issuing DFU abort..
 - [10/08/26 11:52:08] <Error>: Failed to open USB interface: (No such file or directory) (error code: -76)
 - [10/08/26 11:52:33] <Error>: Timed out in state 1, assuming we are back to square one with this device. (error code: -31)
```

**Device after:** `Bus 001 Device 059: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)`

---

## Step 1 — Device Verification

**Physical device detected: YES** (both attempts)

```
Attempt 1: Bus 001 Device 048: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)
Attempt 2: Bus 001 Device 058: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)
```

---

## Step 2 — Artifact Verification

**All artifacts verified (no rebuild):**

| Artifact | SHA256 | Status |
|---|---|---|
| `build/Pongo` | `5b808bb05f99f96ae319476ee81f5636b6cf9e5185d6fa20fc262729b1ac6625` | ✅ |
| `build/Pongo.bin` | `c49e04e4a1b3eedc25ef7986053163020f7d330876aa68054f4d3b37bc40621a` | ✅ |
| `build/PongoConsolidated.bin` | `c6fd941f70aac49c3763b3848afa48ffeecc378814da0cce307338cae9dbeab9` | ✅ |
| `build/checkra1n-kpf-pongo` | `d12b53d295c256492d9de135455d5a87f304826c269e4aa01a3a4273b090c517` | ✅ |
| `example/testmodule/build/testmodule` | `da0de998830bcfbb15e1e8609a1daa6303f980f8badd9098653e78b04c6963b2` | ✅ |

---

## Step 3 — checkra1n Deployment

**Binary:** Official checkra1n 0.12.4 Linux x86_64 CLI (SHA256 verified: `dac9968939ea6e6bfbdedeb41d7e2579c4711dc2c5083f91dced66ca397dc51d`)

**Flags:** `-c -p -k /pongo/Pongo.bin -v` (CLI, boot to Pongo shell, override Pongo image, verbose)

---

## Step 4 — Observe Boot (Both Attempts)

| Stage | Attempt 1 | Attempt 2 |
|---|---|---|
| DFU detection | ✅ | ✅ |
| Checkm8 Preparation | ✅ | ✅ |
| Checkm8 Setup | ✅ | ✅ |
| Trigger / DFU abort | ✅ | ✅ |
| **USB re-acquisition** | ❌ **Error -76** | ❌ **Error -76** |
| PongoOS load | ❌ | ❌ |
| PongoOS shell | ❌ | ❌ |

**Key observation:** The `Failed to open USB interface: (No such file or directory) (error code: -76)` occurs **identically** on both USB ports, immediately after the DFU abort trigger. The exploit reaches the trigger point but fails to re-acquire the USB interface for payload upload.

---

## Step 5 — Official Test Module

**NOT ATTEMPTED** — PongoOS execution never confirmed on either attempt.

---

## Step 6 — Failure Classification

**Result: D2-PHYSICAL-BLOCKED**

**Reason:** checkra1n's checkm8 exploit reaches the Setup stage and issues the DFU abort trigger on **both USB ports**, but then fails with `Failed to open USB interface: (No such file or directory) (error code: -76)` and times out in state 1. The device never reaches pwned DFU, and PongoOS is never loaded.

**This is the same fundamental bootstrap boundary identified in M1.** The gaster open-source checkm8 implementation cycled RESET→SETUP→SPRAY→PATCH 19× without reaching pwned DFU. checkra1n's product-quality closed-source checkm8 reaches the Setup stage and issues the trigger on two different USB ports, but then fails to re-acquire the USB interface after the DFU abort. The root cause is host-level: USB controller / kernel 7.2.8 / udev timing on this Arch Linux host.

---

## Step 7 — Documentation Updated

- `D2_PHASE2_REPORT.md` (this file — updated with Attempt 2)
- `D2_PHASE2_RUNBOOK.md` (already contains the exact failure logs)

---

## Next Safe Step

**STOP.** Two controlled attempts on different USB ports produced **identical failure** at the same point (USB interface re-acquisition after DFU abort). The gating unknown from D2_FEASIBILITY.md is resolved: checkra1n's checkm8 does NOT complete on this host regardless of USB port. The same host-level bootstrap boundary that blocked M1 also blocks D2.

**Options for future (NOT executed now):**
1. Try a **different host machine** (different USB controller / kernel).
2. Try the `usb_settle_shim` LD_PRELOAD approach built for M1.
3. Explicit udev rules for immediate post-abort device access.
4. Ubuntu 20.04 host (matching CI environment).

**No MyOS module implementation attempted.** Phase 2 complete.