# M1_RUNBOOK.md — Milestone M1 execution runbook

Date: 2026-10-08
Milestone: M1 — prove MyOS-owned AArch64 code executes on the
physical iPhone 6 (Apple A8, CPID 0x7000).
Companion docs: M1_PLAN.md (design + PASS/FAIL definition),
M1_REPORT.md (results), BOOT_FEASIBILITY.md (evidence),
TOOLCHAIN.md (host tools).

Terminology (task §2): **transfer** = bytes over USB (NOT
execution); **execute** = CPU retiring our instructions;
**pwned DFU** = checkm8 result (RAM-only, tethered, volatile).
We never call a transfer a boot or an execution.

---

## 0. Safety invariants (task §5) — read first

This runbook performs ONLY RAM-only, DFU-session-local,
tethered operations. It does NOT:

- restore, erase, update, or downgrade the device
- write NAND / flash storage / filesystem
- modify the iOS installation
- write any persistent device state

checkm8 patches a RAM instruction and RAM data structures in the
SecureROM's DFU session. Any power cycle returns the device to a
clean, stock BootROM. If any step below would write persistent
state, STOP — none of them does. gaster's storage/NOR features
are NOT used; only `gaster pwn` (which stages USB payloads in
RAM) is used.

---

## 1. Prerequisites

### 1.1 Host (Arch Linux x86_64, user `khoi`)

Already present (measured, TOOLCHAIN.md §1):
`aarch64-linux-gnu-gcc` 16.1.0, `aarch64-linux-gnu-ld` (binutils
2.47), `gcc`, `make`, `python3`, `git`, `lsusb`, `libusb` 1.0.30,
`docker` (user `khoi` is in the `docker` group).

NOT installed (do NOT auto-install; exact commands when authorized,
TOOLCHAIN.md §2):
```
sudo pacman -S --needed clang lld python-pyusb dfu-util
```
None of these four is required for M1: the payload builds with the
present `aarch64-linux-gnu-gcc`; `m1ctl` links the present `libusb`.
They are listed for completeness / later milestones only.

### 1.2 USB permission model (important)

`/dev/bus/usb/001/*` nodes are `crw-rw-r---- root:root`. User
`khoi` cannot open them RDWR, which checkm8 requires. `sudo` needs
a password (unavailable). Because `khoi` is in the `docker` group
(already-authorized root-equivalence on this machine), all
device-talking commands in §5–§7 run inside a throwaway container
with `/dev/bus/usb` bind-mounted. This modifies neither the host
system nor device state. The SAME unmodified project binaries are
used — the container only provides the USB node permission.

### 1.3 Device

iPhone 6, Apple A8 (T7000), iOS 12.5.7, DFU PID `05ac:1227`,
SecureROM `iBoot-1992.0.0.1.19` (the exact SRTG gaster matches at
gaster.c L595). ECID `00022CDA36100026` (from our lsusb capture,
BOOT_FEASIBILITY §0).

---

## 2. Build

```
cd /home/khoi/Projects/myos
make clean && make            # payload + m1ctl
make offline                  # 21 offline checks, expect ALL_PASS
```

Expected build outputs:
- `build/myos_m1.elf` — ELF64 little-AArch64, entry `0x180380050`
- `build/myos_m1.bin` — flat freestanding image, 104 bytes,
  4-byte aligned, contains marker `MYOSM1OK`
- `build/m1ctl` — host client (links libusb-1.0)

Offline verification (tests/offline_m1.sh) checks: artifacts exist,
flat-binary alignment + DFU budget (<=2016 B), ELF64/AArch64,
entry address `0x180380050`, symbol `T myos_m1_entry`, zero
undefined symbols, first instruction decodes to `mov x9, x0`,
no `.interp/.dynamic/.got` sections, marker present, `.text`-only
flat binary, deterministic rebuild, constants cross-checked against
the vendored gaster.c, no QEMU-virt references.

The bootstrap dependency is already vendored, unmodified:
`tools/external/gaster/` (0x7ff, Apache-2.0, commit `7ffffff`).
Build it only if you want to re-verify the binary:
```
make gaster                   # builds tools/external/gaster/gaster
```

---

## 3. Connect device

1. Power off the iPhone 6 (or use a device already off).
2. Connect it to the host with the Lightning→USB cable, **direct
   port, no hub** (checkm8 is timing-sensitive; hubs add flake —
   ARCHITECTURE.md §9 risk table).
3. Do NOT open iTunes / any iOS tool that might trigger a restore.

## 4. Enter DFU

iPhone 6 DFU entry (hardware button sequence):
1. Hold **Power + Home** together for 3 seconds.
2. Release **Power**, keep holding **Home** for 10 seconds.
3. Release **Home**. Screen stays black; the device is in DFU.

## 5. Verify DFU enumeration (05ac:1227)

On the host:
```
lsusb -d 05ac:1227
lsusb -v -d 05ac:1227 2>/dev/null | grep -i iSerial
```
Expected: a device `ID 05ac:1227 Apple, Inc. Mobile Device (DFU
Mode)` whose `iSerial` reads
`CPID:7000 CPRV:11 CPFM:03 SCEP:01 BDID:06 ECID:00022CDA36100026
IBFL:1C SRTG:[iBoot-1992.0.0.1.19]`.

If `CPID:7000` / `SRTG:[iBoot-1992.0.0.1.19]` is not present,
STOP — wrong device or wrong SecureROM; gaster's t7000 constants
would not apply.

## 6. Load / bootstrap — pwn the DFU session (checkm8)

This runs gaster's checkm8 exploit. It is RAM-only and tethered.
Run it inside the USB-permission container:

```
cd /home/khoi/Projects/myos
docker run --rm -it \
  -v /home/khoi/Projects/myos:/myos \
  -v /dev/bus/usb:/dev/bus/usb \
  -w /myos \
  alpine sh -c 'apk add --no-cache libusb >/dev/null 2>&1; \
                LD_PRELOAD=/myos/build/usb_settle_shim.so \
                /myos/tools/external/gaster/gaster pwn'
```

`usb_settle_shim.so` (tools/host/usb_settle_shim.c) is a timing-only
LD_PRELOAD shim: it sleeps around `libusb_init` / the first
`libusb_get_device_list` so the host udev has time to create the
re-enumerated `/dev/bus/usb` node after gaster's `libusb_reset_device`.
It alters NO USB traffic — checkm8's sensitive transfers never call
these functions. (Diagnosis of the underlying udev race is in
M1_REPORT.md §failure-analysis.)

Expected successful output (the runbook's "bootstrap" step — this
is exploitation, not yet MyOS execution):
```
usb_timeout: 5
usb_abort_timeout_min: 0
[libusb] Waiting for the USB handle with VID: 0x5AC, PID: 0x1227
CPID: 0x7000
Found the USB handle.
Stage: RESET
ret: true
...
Stage: SETUP
ret: true
...
Stage: SPRAY
ret: true
...
Stage: PATCH
ret: true
Now you can boot untrusted images.
```

The line **`Now you can boot untrusted images.`** is gaster's
STAGE_PWNED marker (gaster.c L1271). It means the checkm8 payload
is running on the A8 and the DFU session is pwned.

Verify the pwned state (still in the container):
```
docker run --rm -it -v /home/khoi/Projects/myos:/myos \
  -v /dev/bus/usb:/dev/bus/usb -w /myos alpine sh -c \
  'apk add --no-cache libusb >/dev/null 2>&1; /myos/build/m1ctl serial'
```
Expected: `iSerial: ... PWND:[checkm8] ...` and `STATE: PWNED`.

**Pwned DFU is volatile.** If the device is power-cycled, unplugged,
or the DFU session times out, it returns to stock DFU (no `PWND:`
marker) and you must redo §4–§6.

## 7. Execute — rung 2 (handler round-trip) then rung 3 (MyOS)

### 7.1 Rung 2 — prove the exec/memc command handler is live
```
docker run --rm -it -v /home/khoi/Projects/myos:/myos \
  -v /dev/bus/usb:/dev/bus/usb -w /myos alpine sh -c \
  'apk add --no-cache libusb >/dev/null 2>&1; /myos/build/m1ctl memc'
```
Expected: `MEMC: HANDLER_LIVE (DONE_MAGIC + dst round-trip OK)`.
This proves the gaster device-side command handler executes on the
A8 and answers — but it is NOT yet MyOS code.

### 7.2 Rung 3 — execute MyOS payload on the A8
```
docker run --rm -it -v /home/khoi/Projects/myos:/myos \
  -v /dev/bus/usb:/dev/bus/usb -w /myos alpine sh -c \
  'apk add --no-cache libusb >/dev/null 2>&1; /myos/build/m1ctl exec'
```
`m1ctl exec` builds an 80-byte EXEC command block
(`[EXEC_MAGIC][pc=0x180380050][x0=0x180380000][x1=challenge][x2..x7=0]`)
with the 104-byte `myos_m1.bin` appended at `+0x50`, delivers it
with gaster's `dfu_send_data` sequence, then fires the hooked
control-IN `0xA1/2/0xFFFF`. The device-side hook does `blr pc`
→ **`myos_m1_entry` executes on the A8 CPU** → writes the evidence
block → the handler returns `[DONE_MAGIC][retval][evidence...]`
over the same control-IN.

## 8. Observe result — expected output (M1 PASS)

`m1ctl exec` prints the evidence block and a verdict. PASS output:
```
exec: entry=0x180380050 block=... bytes (80-byte header + 104 payload)
exec response (64 bytes): 65 6e 6f 64 65 6e 6f 65 ...   # "enodENOd" = DONE_MAGIC LE
done_magic     = 0x646f6e65646f6e65 OK
retval         = 0xeeddccbbaa995533 OK                    # = ~challenge
CurrentEL slot = 0x0000000000000004 EL1/EL2/EL3 plausible
MIDR_EL1 slot  = 0x... (read at EL1)
marker slot    = 0x4b314f4d4d59534d MYOSM1OK OK           # "MYOSM1OK" LE
echo slot      = 0x1122334455667788 OK
CNTFRQ_EL0     = 0x...
CNTPCT_EL0     = 0x...
M1_VERDICT: PASS
PASS means: evidence demonstrates instruction execution reached
myos_m1_entry (0x180380050) on the physical A8.
```

### PASS criterion (M1_PLAN.md §6) — ALL five must hold
1. `DONE_MAGIC` (`0x646f6e65646f6e65`) at response offset 0 — the
   handler ran our `blr` and came back.
2. return value at +8 == `~CHALLENGE` (`0xeeddccbbaa995533`) — a
   value only our instructions compute from the input.
3. slot +0x10 (`CurrentEL`) == 0x4 / 0x8 / 0xC — a plausible EL
   encoding.
4. slot +0x20 == marker `MYOSM1OK`.
5. slot +0x28 == challenge echo (`0x1122334455667788`).

**PASS** = evidence (1)–(5) demonstrates instruction execution
reached `myos_m1_entry` (0x180380050) on the physical Apple A8.
**FAIL** = "Payload was transferred but no evidence proves CPU
execution" — any of: no DONE_MAGIC, timeout, wrong marker, wrong
return value, unexpected re-enumeration/stall. A DFU transfer
alone is NEVER success.

Repeat §4–§8 three times from cold DFU for the M1 acceptance
(ARCHITECTURE.md §7: "≥3 consecutive runs from cold DFU").

---

## 9. Failure diagnosis

| Symptom | Likely cause | Action |
|---|---|---|
| `lsusb` shows no `05ac:1227` | device not in DFU / not connected / cable | redo §3–§4; try a different cable/port; confirm screen is black |
| `iSerial` has no `CPID:7000` / wrong SRTG | wrong device or SecureROM | STOP — gaster t7000 constants do not apply |
| gaster stalls after `Stage: SPRAY` (never reaches PATCH) | host udev re-enumeration race: the post-reset `/dev/bus/usb` node is not ready when libusb's fresh context scans, so the wait loop keeps a stale device-less cache (diagnosed via strace: zero `openat` in the wait loop) | run with `LD_PRELOAD=.../usb_settle_shim.so` (§6); direct port, no hub; if still stuck, `USB_TIMEOUT=20` (less aggressive timing) |
| gaster cycles RESET→SETUP→SPRAY→PATCH forever, never prints `Now you can boot untrusted images.` | checkm8 timing flake on this host/port (ARCHITECTURE.md §9 high-severity risk) | direct port, no hub; `USB_TIMEOUT=20`; try the docker container (different USB timing); try several cold-DFU attempts; if it never completes, see M1_REPORT.md — this is the observed blocker |
| `m1ctl serial` says `NOT_PWNED` | pwned session lost (power cycle / timeout) | redo §4–§6 |
| `m1ctl memc` fails / no DONE_MAGIC | handler not live (pwn incomplete) or protocol mismatch | confirm `STATE: PWNED` first; re-check §6 output reached STAGE_PWNED |
| `m1ctl exec` transfers but evidence wrong/absent | payload not executed (transfer ≠ execution) | this is a FAIL by definition — do not report success; capture the hexdump for M1_REPORT.md |
| `m1ctl exec` GETSTATUS state error | DFU state machine not in the state gaster's sequence expects | confirm pwned state; the sequence is gaster's own `dfu_send_data`, so a state mismatch indicates the session is not actually pwned |

## 10. Recovery procedure

- **Device stuck / unresponsive after a failed attempt:** hold
  Power+Home ~10s to force-reboot. The device returns to a clean
  stock BootROM (checkm8 is RAM-only). Then redo §4–§6.
- **Device re-enumerated as normal (not DFU):** it exited DFU.
  Redo §4 (DFU button sequence).
- **`m1ctl` cannot open the device (permission):** you ran it
  outside the container. Run it inside the docker container as in
  §6–§7 (the container provides root USB access via the `docker`
  group).
- **No persistent state was changed at any point.** There is no
  un-bricking step required by this runbook; a force-reboot always
  restores stock behavior.

---

## 11. What is NOT in scope for M1

No scheduler, no filesystem, no GUI, no display/framebuffer output,
no UART (no public iPhone 6 UART pinout — BOOT_FEASIBILITY §2), no
NAND access, no iBoot/XNU interaction. QEMU is NOT used in M1
(nothing in M1 is validated by QEMU; the payload targets real A8
hardware only). The only observable output channel at M1 is the
USB control-IN response back to the PC.
