# M1_PLAN.md — Milestone M1 plan

Date: 2026-10-08
Milestone: **M1 — transfer control to MyOS-owned code on the physical iPhone 6
(A8) and observe it execute.** No OS, no scheduler, no filesystem, no GUI,
no generic QEMU kernel.

Terminology discipline (task §2): this document distinguishes
- **transfer** = bytes moving over USB (NOT execution),
- **execute** = CPU fetching and retiring our instructions,
- **pwned DFU** = checkm8 exploitation result (a RAM-only, tethered state),
- **bootstrap** = existing open-source components we depend on.
We do not use "boot" for anything that is only a data transfer.

---

## 1. The M1 chain (verified against sources in this session)

```
PC (Arch Linux, x86_64)
 │
 │  [A] gaster pwn                 ← existing open-source bootstrap (external dep)
 │      checkm8 exploit over USB, stages RESET→SETUP→SPRAY→PATCH
 ▼
iPhone 6 DFU (05ac:1227, SecureROM iBoot-1992.0.0.1.19)
 │
 │  [B] checkm8 payload executes   ← gaster-owned code (NOT MyOS) runs on A8
 │      installs: PWND:[checkm8] serial marker,
 │      sigcheck patch (RAM only), DFU-request hook,
 │      exec/memc command handler at image buffer
 ▼
pwned DFU state (volatile — lost on any reboot)
 │
 │  [C] m1ctl memc                 ← MyOS-owned host tool
 │      DFU_DNLOAD command block → DFU image buffer @ 0x180380000
 │      trigger: control IN 0xA1/bRequest=2/wValue=0xFFFF
 ▼
gaster's handler executes on A8     ← proves the command interface is live
 │
 │  [D] m1ctl exec                 ← MyOS-owned host tool
 │      DFU_DNLOAD block: [EXEC_MAGIC][pc=myos_m1_entry][x0..x7][+ appended code]
 │      trigger: control IN 0xA1/2/0xFFFF
 ▼
handler: blr pc  →  MYOS CODE EXECUTES ON THE A8 CPU   ← M1 happens HERE
 │
 │  [E] payload writes evidence into the response block (CPU register
 │      snapshot + marker + computed return value), handler returns
 │      [DONE_MAGIC][retval][evidence...] over USB
 ▼
PC reads the response — observable proof
```

Every link above was verified in this session against primary artifacts:
- gaster source `tools/external/gaster/gaster.c` (cloned, commit
  `7fffffff38a1bed1cdc1c5bae0df70f14395129b`): t7000 matched by the exact
  serial `SRTG:[iBoot-1992.0.0.1.19]` of OUR device; constants table read
  directly (see §4).
- gaster device-side payloads `payload_notA9.S` + `payload_handle_checkm8_request.S`.
- ipwndfu `usbexec.py` / `dfu.py` (upstream protocol reference for the
  exec/memc USB interface; upstream ipwndfu does NOT support t7000, gaster does).

## 2. Ownership split (who owns what)

| Component | Runs on | Owner | Notes |
|---|---|---|---|
| gaster (exploit driver + checkm8 payloads) | PC + injected code on iPhone | **existing open-source** (0x7ff, Apache-2.0) | external dependency, cloned unmodified into `tools/external/gaster/`, NOT forked/patched |
| m1ctl (pwned-DFU exec client) | PC | **MyOS** (`tools/host/m1ctl.c`) | implements gaster's native command interface (gaster_command + dfu_send_data, gaster.c L977/L1478) via libusb — the exact DNLOAD/GETSTATUS sequence proven to drive gaster's device-side payload |
| `myos_m1` payload | iPhone (A8 CPU) | **MyOS** (`boot/aarch64/m1_entry.S`) | freestanding AArch64, no libc, no OS, no QEMU assumptions |
| USB device access helper | PC (container) | Docker (already on host, user in docker group) | used only because /dev/bus/usb nodes are root-writable; see §7 |
| QEMU | — | — | **not used in M1 at all** (nothing in M1 is validated by QEMU) |

## 3. Payload delivery — how bytes reach the device, where CPU starts

1. During `gaster pwn`, gaster DFU_DNLOADs its payload blob; SecureROM copies
   the completed data phase into its **DFU image buffer**. gaster's overwrite
   then hijacks a USB callback to `insecure_memory_base`, i.e. execution jumps
   to that image buffer. Therefore (verified by construction in gaster.c):
   **DFU image buffer == insecure_memory_base == 0x180380000 on CPID 0x7000
   with SRTG iBoot-1992.0.0.1.19.**
2. After pwn, every `DFU_DNLOAD` data phase our host completes overwrites that
   same image buffer (this is exactly how ipwndfu's `usbexec.py` delivers its
   command blocks on supported SoCs).
3. The hook installed by gaster intercepts control-IN requests
   `bmRequestType=0xA1, bRequest=2 (DFU_UPLOAD), wValue=0xFFFF`
   and treats the 16 bytes at `0x180380000` as a command header:
   - `+0x00` magic: EXEC (`0x6578656365786563`) or MEMC (`0x6D656D636D656D63`)
   - `+0x08` pc (EXEC only)
   - `+0x10..+0x48` x0..x7
   - EXEC ⇒ `blr x8` where x8 = pc. MEMC ⇒ `memcpy(x0, x1, x2)`.
   - result: `*(u64*)base = DONE_MAGIC`, `*(u64*)(base+8) = x0 (return value)`,
     sent back over the same control-IN transfer.
   Host delivery sequence (m1ctl `command()`): gaster's own
   `dfu_send_data` — DFU_DNLOAD chunks (≤0x800) of the command
   block, then a 16-byte DFU file suffix, then a zero-length
   DFU_DNLOAD, then DFU_GETSTATUS ×3 walking state
   MANIFEST_SYNC(6)→MANIFEST(7)→MANIFEST_WAIT_RESET(8) —
   followed by the hooked control-IN. This is the identical
   sequence gaster's `gaster_command`/`gaster_aes` use for the
   working `decrypt` feature, i.e. the proven path for THIS
   device-side payload (not the ipwndfu variant).
4. **MyOS entry point**: `myos_m1_entry`, placed at `0x180380000 + 0x50`
   (appended right after the 80-byte command header in the same DFU_DNLOAD).
   PC-executing from DRAM that was just written over USB is the *same* mechanism
   gaster's own payload relies on during pwn, so the execution path is proven
   upstream before we use it.
5. Call convention into our code: `x0 = 0x180380000` (command block base),
   `x1 = host challenge constant`, x2..x7 = 0. We may write evidence into
   `base+0x10..+0x38` and must return with `x0 = computed value`; the handler
   stores DONE_MAGIC + x0 for the host.

## 4. Constants used (provenance: gaster.c lines, commit above — NOT guessed)

| Constant | Value | Source |
|---|---|---|
| Device match | serial contains `SRTG:[iBoot-1992.0.0.1.19]` ⇒ cpid 0x7000 | gaster.c ~L595 |
| insecure_memory_base (DFU image buffer) | `0x180380000` | gaster.c t7000 case |
| memcpy_addr | `0x100010E70` | idem |
| handle_interface_request | `0x10000EEE4` | idem |
| usb_core_do_transfer | `0x10000EBB4` | idem |
| gUSBSerialNumber | `0x1800888C8` | idem |
| dfu_handle_request | `0x180088878` | idem |
| patch_addr (sigcheck patch target, RAM) | `0x100007E98` | idem |
| EXEC/DONE/MEMC magics | `0x6578656365786563` / `0x646F6E65646F6E65` / `0x6D656D636D656D63` | gaster.c L49-51 (identical to ipwndfu usbexec.py ASCII magics) |
| DFU max transfer | `0x800` | gaster.c L44 / usbexec chunking |

No hardware register address is invented anywhere in MyOS code. Our payload
touches **only CPU system registers that are architectural ARMv8** and the
command block buffer it is given.

## 5. CPU state at entry (what we inherit, what we do)

- We do NOT cold-reset, configure MMU, caches, interrupts, or any peripheral.
- Inherited state is *whatever SecureROM's DFU loop uses* — officially
  UNKNOWN until measured (BOOT_FEASIBILITY matrix). M1's payload measures it:
  - `CurrentEL` — always safe to read at EL1/EL2/EL3.
  - `MIDR_EL1` — read **only if CurrentEL == EL1** (guarded; avoids EL2/EL3
    trap risk). Sentinel 0 otherwise.
  - `CNTFRQ_EL0`/`CNTPCT_EL0` — read only if EL1 (same caution).
  - If we are not at EL1, CurrentEL alone + marker + arithmetic still prove
    execution; deeper identification is deferred to M2 (vector table install).
- Payload uses only caller-saved temporaries (x9-x17), never touches sp,
  never returns to a corrupted x30 → safe to run inside the DFU request hook.

## 6. M1 proof definition (PASS / FAIL)

**PASS**: a USB control-IN response captured by `m1ctl exec` contains
1. `DONE_MAGIC` at offset 0 (handler ran our blr and came back), AND
2. return value at +8 == `~CHALLENGE` (value only our instructions compute), AND
3. slot +0x10 == a plausible `CurrentEL` encoding (0x4 / 0x8 / 0xC), AND
4. slot +0x20 == marker `MYOSM1OK`, AND
5. slot +0x28 == CHALLENGE echo.

⇒ *Evidence (1)-(5) demonstrates that instruction execution reached
`myos_m1_entry` (0x180380000+0x50) on the physical Apple A8.*

**FAIL**: "Payload was transferred but no evidence proves CPU execution" —
any of: no DONE_MAGIC, timeout, wrong marker, wrong return value, device
re-enumerates/stalls unexpectedly. A DFU transfer alone is NEVER called success.

Secondary evidence ladder (each rung captured in M1_REPORT):
1. `lsusb` serial shows `PWND:[checkm8]` (gaster code ran on device),
2. `m1ctl memc` round-trip returns DONE_MAGIC (command handler alive),
3. `m1ctl exec` returns full evidence block (MyOS code executed).

## 7. Device & host safety (task §5)

- All operations are USB/DFU-session-local and RAM-only: checkm8 patches a
  RAM instruction and RAM data structures; every power cycle or force-reboot
  restores stock SecureROM. **No NAND, no filesystem, no iOS state touched,
  no restore/erase/update/downgrade, no persistent writes of any kind.**
- If any planned step would write persistent device state → STOP before it
  (none does; gaster's storage/NOR features are not used — its `pwn` path
  only stages USB payloads).
- Host: no packages installed; no sudo (impossible anyway — password
  required). USB nodes are `crw-rw-r-- root:root` → user `khoi` cannot open
  them RDWR, which the exploit requires. Because `khoi` is in the `docker`
  group (already-authorized root-equivalence on this machine), the physical
  test runs the *same unmodified binaries* inside a container with an explicit
  device cgroup rule. This modifies neither the host system nor device state.
  Exact commands are recorded in M1_RUNBOOK.md §7 (documented, not hidden).

## 8. Stop conditions honored

If at any point: pwn impossible, protocol mismatch, or no observable
execution proof → stop, document as BLOCKED in M1_REPORT.md with exact
blocker/evidence/missing tool/next action. A successful payload build or a
DFU round-trip will NOT be reported as M1 success.
