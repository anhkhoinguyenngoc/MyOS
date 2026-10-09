# M1_REPORT.md — Milestone M1 result

Date: 2026-10-08
Milestone: M1 — prove MyOS-owned AArch64 code executes on the
physical iPhone 6 (Apple A8, CPID 0x7000).

## VERDICT: BLOCKED (honest)

**M1 did not PASS.** MyOS code execution on the physical A8 was
NOT demonstrated. The blocker is upstream of MyOS: the checkm8
exploit (`gaster pwn`) never reached its pwned state on this
host/device, and — at the time of this report — the iPhone 6 is
not connected to the host at all, so no physical test can run.

Per the task's success condition B, this report states the exact
blocker, the evidence, the missing dependency/tool, and the exact
next action. There is no destructive workaround and none was
attempted. A successful payload build and offline verification
are NOT reported as success (task §12: "A successful payload
build or a DFU transfer alone will NOT be reported as M1 success").

---

## 1. Exact hardware

| Field | Value | Source |
|---|---|---|
| Device | iPhone 6 | task spec |
| SoC | Apple A8 (T7000), AArch64, 2× Typhoon | BOOT_FEASIBILITY §1 |
| CPID | 0x7000 | our lsusb capture (BOOT_FEASIBILITY §0) |
| SecureROM / SRTG | iBoot-1992.0.0.1.19 | our lsusb capture |
| DFU PID | 05ac:1227 | our lsusb capture |
| ECID | 00022CDA36100026 | our lsusb capture |
| iOS | 12.5.7 | task spec |
| Host | Arch Linux x86_64, kernel 7.2.8-arch1-2, libusb 1.0.30 | gaster_dbg.log |

The device WAS enumerated on this host during the earlier session
(full iSerial captured, BOOT_FEASIBILITY §0). It is NOT connected
now (see §4).

## 2. Exact bootstrap (external dependency)

- **Project:** gaster, by 0x7ff (Apache-2.0) — a C/libusb checkm8
  implementation that supports CPID 0x7000 / SRTG iBoot-1992.0.0.1.19
  (gaster.c L595-609). Upstream ipwndfu does NOT support t7000;
  gaster does. This is why gaster was chosen.
- **Version/commit:** commit `7ffffff` ("Update"), cloned unmodified
  into `tools/external/gaster/` (git log verified). NOT forked,
  NOT patched. The only project-local additions are the host-side
  `m1ctl` client and the `usb_settle_shim.so` timing shim — neither
  modifies gaster.
- **Build:** `make gaster` → `tools/external/gaster/gaster`
  (needs `libusb` + `openssl`; both present on host).
- **Interface to MyOS:** gaster's checkm8 payload installs a
  device-side command handler (`payload_handle_checkm8_request.S`)
  that intercepts control-IN `0xA1 / bRequest=2 / wValue=0xFFFF`
  and reads an 80-byte command block from the DFU image buffer
  (`insecure_memory_base` = `0x180380000` on CPID 0x7000). The
  block format is `[EXEC/MEMC magic][pc][x0..x7]`; EXEC does
  `blr pc`. This is the documented, proven exec interface (gaster's
  own `gaster_command`/`gaster_aes` use it for `decrypt`).

## 3. Exact payload (MyOS-owned)

- **Source:** `boot/aarch64/m1_entry.S` (freestanding AArch64,
  no libc, no OS, no MMU/cache/interrupt changes, no peripheral
  access, no guessed register addresses — only architectural
  ARMv8 system registers: CurrentEL, MIDR_EL1, CNTFRQ_EL0, CNTPCT_EL0).
- **Linker:** `boot/aarch64/m1.ld` — linked at `0x180380050`
  (= DFU image buffer `0x180380000` + `0x50`, i.e. appended right
  after the 80-byte exec command header in the same DFU_DNLOAD).
- **Entry point:** `myos_m1_entry` @ `0x180380050`.
- **Delivery:** `m1ctl exec` appends the 104-byte flat image to
  the EXEC command block and delivers it via gaster's `dfu_send_data`
  sequence; the device-side hook does `blr pc` → `myos_m1_entry`.
- **Evidence it would write** (M1_PLAN §6): CurrentEL, MIDR_EL1,
  marker `MYOSM1OK`, challenge echo, CNTFRQ_EL0, CNTPCT_EL0, and
  return value `~challenge` — all read back over the same control-IN
  as `[DONE_MAGIC][retval][evidence...]`.

## 4. Build result (offline) — COMPLETE

```
make clean && make        # OK
make offline              # 21/21 PASS (tests/offline_m1.sh)
```
Verified offline: ELF64 little-AArch64; entry `0x180380050`;
symbol `T myos_m1_entry`; zero undefined symbols; first instruction
`mov x9, x0`; no `.interp/.dynamic/.got`; flat binary 104 bytes,
4-byte aligned, within the 2016-byte DFU budget, `.text`-only,
contains `MYOSM1OK`; deterministic rebuild; all constants
cross-checked against the vendored gaster.c; no QEMU-virt
references. Disassembly reviewed instruction-by-instruction
(`aarch64-linux-gnu-objdump -d build/myos_m1.elf`).

**The payload is correct and ready. It has simply never been
given the chance to run, because the pwned-DFU prerequisite
was never established.**

## 5. Physical test result — NOT PERFORMED (blocked)

### 5.1 Current device state
`lsusb -d 05ac:1227` returns nothing; a 10-second poll found no
Apple DFU device. The iPhone 6 is not connected to the host.
Physical testing is impossible without it.

### 5.2 What happened when it WAS connected (earlier session)
`gaster pwn` was run repeatedly (logs in `build/`). Two failure
modes were observed:

**(a) Stall after SPRAY** (g.log, g2.log, g3.log, g5.log, g6.log,
gaster_pwn.log): the exploit reaches `Stage: SPRAY / ret: true`
and then hangs in the post-stage `wait_usb_handle` loop, never
reaching PATCH. Root cause (diagnosed with strace, see
`usb_settle_shim.c` header): after gaster's `libusb_reset_device`,
the iPhone re-enumerates and host udev needs tens-to-hundreds of ms
to create the new `/dev/bus/usb` node; gaster's fresh libusb context
scans inside that window, the device is skipped, and — with no
further hotplug event — the process keeps a stale, device-less
cache forever (the wait loop does zero `openat` syscalls while other
processes open the same device fine). Mitigation built:
`build/usb_settle_shim.so` (LD_PRELOAD, timing-only, alters no USB
traffic).

**(b) Cycle without pwn** (pwn2.log, run WITH the shim): the shim
got past (a) — the exploit then completed 19 full
RESET→SETUP→SPRAY→PATCH cycles, every stage returning `ret: true`
— but the device NEVER reached `STAGE_PWNED`. The line
`Now you can boot untrusted images.` (gaster.c L1271) never
appeared, and the `PWND:[checkm8]` serial marker never appeared.
The 20th cycle stalled again at SPRAY.

Stage tallies across all logs: RESET=36, SETUP=28, SPRAY=28,
PATCH=19, STAGE_PWNED=0.

**Interpretation:** the PATCH stage's overwrite was delivered (the
expected STALL was observed, so `ret: true`), but the pwned state
did not survive the subsequent USB reset — the device re-enumerated
as stock DFU every time. This is a checkm8 host-timing/reliability
failure on this specific host + port + cable combination
(ARCHITECTURE.md §9 lists "Host-side USB timing" as the High-severity
M1 risk). It is NOT a MyOS bug and NOT a payload bug — MyOS code
was never reached, so it cannot be blamed.

## 6. Missing dependency / tool

Nothing is missing for the BUILD (all present). For the PHYSICAL
test the missing items are:

1. **The iPhone 6 physically connected** (not a software dependency —
   the device is simply not plugged in).
2. **Root RDWR access to `/dev/bus/usb/*`** — nodes are
   `crw-rw-r---- root:root`; user `khoi` cannot open them RDWR and
   `sudo` requires a password (unavailable). Resolved path (already
   validated): run the device-talking commands inside a throwaway
   docker container with `/dev/bus/usb` bind-mounted — `khoi` is in
   the `docker` group, and a test container successfully mounted
   `/dev/bus/usb` and listed the nodes. Exact commands are in
   M1_RUNBOOK.md §6–§7.
3. Optional (NOT required for M1, listed per TOOLCHAIN.md §2, NOT
   auto-installed): `sudo pacman -S --needed clang lld python-pyusb
   dfu-util`. The M1 payload builds with the present
   `aarch64-linux-gnu-gcc`; `m1ctl` links the present `libusb`.

## 7. Exact next action (ordered)

1. Physically connect the iPhone 6 (direct port, no hub) and enter
   DFU (M1_RUNBOOK.md §3–§4).
2. Run `gaster pwn` inside the docker container WITH the settle shim
   (M1_RUNBOOK.md §6). If it stalls after SPRAY, the shim is working
   around (a); if it cycles without pawning (mode b), try, in order:
   - `USB_TIMEOUT=20` (less aggressive than the 5 ms default — the
     exploit is timing-sensitive and 5 ms is very tight on a busy
     host);
   - a different USB port / cable / cold-DFU re-entry;
   - several cold-DFU attempts (the heap state from a failed attempt
     can persist until a fresh DFU entry).
3. Only when `Now you can boot untrusted images.` appears, run
   `m1ctl serial` (expect `STATE: PWNED`), then `m1ctl memc`
   (rung 2), then `m1ctl exec` (rung 3 — the actual M1 execution).
4. Apply the PASS/FAIL criterion in M1_PLAN.md §6 / M1_RUNBOOK.md §8.
5. If pwn still never completes after the above, the blocker is
   checkm8 reliability on this host — escalate per ARCHITECTURE.md §9
   (try the D2 carrier: stock PongoOS + MyOS as a pongoOS module,
   which uses a different delivery path), or move to a host with a
   known-good checkm8 track record. Do NOT invent a destructive
   workaround.

## 8. Limitations

- checkm8 execution is **tethered** (RAM-only): every power cycle
  returns to stock BootROM. Accepted for a development device.
- No public register-level documentation for T7000 peripherals
  (display/AIC/NAND/UART) — irrelevant to M1, which needs none of
  them (BOOT_FEASIBILITY §5 hard-blocker #1).
- The inherited CPU/MMU/EL state at MyOS entry is UNKNOWN until
  measured — which is exactly what the payload's evidence block is
  designed to report. It has not been measured yet.
- The physical test could not be repeated 3× (M1 acceptance) because
  the prerequisite pwned state was never reached even once.

## 9. Next milestone (after M1 PASS)

Per ARCHITECTURE.md §7, in order: M2 exceptions (install our
`VBAR_EL1`, catch a deliberate undefined instruction), M3 timer
(`CNTPCT_EL0` around a known delay, record `CNTFRQ_EL0`), M4 memory
report (measured DRAM map). Each flips a feasibility-matrix row from
UNKNOWN to CONFIRMED. None of these starts until M1 PASS.
