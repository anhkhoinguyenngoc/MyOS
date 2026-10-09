# ARCHITECTURE.md — MyOS boot architecture for iPhone 6 (Apple A8)

Date: 2026-10-08
Prerequisite reading: `BOOT_FEASIBILITY.md` (evidence + matrix), `TOOLCHAIN.md`
(host tools). This document is the design; **no OS code has been written yet.**

---

## 1. The real iPhone boot path

Two chains exist. Only the second one belongs to MyOS.

**(a) Apple's normal chain — NOT our path:**
```
BootROM (SecureROM iBoot-1992.0.0.1.19, immutable)
  → LLB (signed) → iBoot (signed) → kernelcache/XNU (signed)
```
Every stage requires Apple signatures. There is no door for us here
(BOOT_FEASIBILITY: iBoot/kernel-entry rows = BLOCKED by design).

**(b) The research chain MyOS actually uses:**
```
Arch Linux PC
  │  gaster/ipwndfu-class harness (libusb), vendored in tools/checkm8/
  ▼
Lightning → USB → DFU (05ac:1227)                    [CONFIRMED on this host]
  ▼
checkm8 in BootROM USB stack
  (A8 = DFU-abort variant: direct code exec, no ROP)  [CONFIRMED mechanism]
  ▼
Stage-1 shellcode executes in BootROM context,
receives stage-2 over USB                            [CONFIRMED mechanism]
  ▼
Stage-2 / carrier  ──── one of two documented doors:
  (D1) checkra1n -k <custom image>   (checkra1n ≥ 0.9.8.1)   [LIKELY]
  (D2) stock PongoOS boot, then MyOS as a pongoOS module
       (USB shell `modload`)                                 [LIKELY]
  ▼
MyOS entry point executes on the A8                   ← FIRST MILESTONE
  ▼
MyOS hardware init (only what that milestone needs)
```

iBoot and XNU are *bypassed, not conquered*. The carrier environment
(checkra1n stage2 / pongoOS) is a **bootstrap dependency**, documented as such:
we do not own it, we do not modify iOS with it, and MyOS never depends on it
being an OS — only on being a way to get a jump to our image.

## 2. MyOS entry point

- Artifact: `myos.bin` — a **freestanding AArch64 bare-metal binary**
  (no ELF/Mach-O runtime assumption at entry; linked at a fixed physical
  address chosen to not collide with the carrier; exact address is a
  measured constant filled in at milestone 1, see §5).
- Entry symbol: `_start` in `boot/aarch64/start.S`:
  1. park all secondary cores (write them to a spin loop — we run 1 core);
  2. switch to a known EL1-safe stack (our own, in DRAM we own);
  3. read and **stash** `CurrentEL`, `SCTLR_EL1`, `MIDR_EL1`, `MPIDR_EL1`,
     `CNTPCT_EL0` into a report block — this turns every UNKNOWN in the
     feasibility matrix into data;
  4. `bl myos_main`.
- Contract with the carrier (both doors): *AArch64, EL unknown-but-nonsecure,
  interrupts masked or maskable, MMU state inherited*. MyOS must be correct
  under **both** "MMU off" and "MMU on with identity map" until we measure
  which one we get (§4).

## 3. Required bootstrap / payload

Two candidates, both documented upstream; we implement against **D1 first**
(fewest moving parts), keeping D2 as fallback:

- **D1 — custom image via `checkra1n -k myos.bin`** (or the raw
  ipwndfu/gaster equivalent: pwn → patch session sigcheck → send unsigned
  stage-2 → jump). Upstream explicitly supports custom images replacing
  pongoOS since checkra1n 0.9.8.1 [S9/S13 in BOOT_FEASIBILITY].
  Dependency: a checkm8 host driver, vendored in `tools/checkm8/`.
- **D2 — PongoOS carrier.** Boot stock `Pongo.bin`, then push MyOS as a
  Mach-O pongo module over the USB shell (`modload`). Pros: USB shell gives us
  printf/log for free while bring-up is early. Cons: we inherit pongo's
  environment and toolchain (clang + ld64).
  Decision rule: **use D2's console as our debugging crutch if D1's first
  run is a black box.**

Nothing else may be a dependency for the first milestone: no XNU, no iBoot,
no filesystem, no SEP, no NAND.

## 4. CPU initialization

- We do **not** cold-reset the CPU; we inherit whatever BootROM/pongo left.
- First instructions measure, then normalize:
  - report `CurrentEL` (is it EL1? EL2? unknown → record, don't assume);
  - disable interrupts (`DAIFSet #0xf`);
  - install our own vector table (`VBAR_EL1`) — this is what makes a crash
    *observable* (we can route a unexpected exception to our USB/panic path);
  - single core: park cores 1..N in `wfe` loop before touching anything;
  - no caches/MMU reprogramming at milestone 1 — only documented-as-safe
    reads. MMU/caches only after we know the inherited state (§2 contract).
- Generic ARMv8 timer (`CNTPCT_EL0` / `CNTFRQ_EL0`) is architectural and needs
  no SoC docs → our first "is time advancing" probe.

## 5. RAM initialization

- We **inherit** DRAM initialized by the BootROM (it must init DRAM to load
  images; payloads/pongoOS run from DRAM — BOOT_FEASIBILITY row "RAM
  initialization" = LIKELY).
- Milestone 1 performs validation, not initialization:
  - read back the load address region, confirm our image bytes are intact;
  - small, bounded memory walk (e.g., verify write/read at a few offsets of a
    scratch window we control) to discover usable RAM and confirm the base;
  - record the observed map in the report block (physical base/size is an
    UNKNOWN in the matrix — this is how it becomes known).
- Physical memory map of T7000: **no public source** — measured, then pinned
  as constants in `platform/apple-a8/memmap.h`, sourced only from our own
  measurements + cross-checks against iBoot/XNU reverse-engineering output.

## 6. First observable output

There is **no usable display or UART path at milestone 1** (matrix: both
UNKNOWN). Therefore, in order of reachability:

1. **USB is the console.** The carrier already has a USB transport
   (BootROM's DFU stack in D1; pongoOS shell in D2). Milestone 1's payload
   sends a fixed, recognizable report string + the stashed CPU-state block
   back over that channel. *Seeing that string on the PC = first instruction
   ran.*
2. Fallback: watchdog-style progress signaling (deterministic hang codes the
   host can distinguish by device state re-enumeration / timeout).
3. Display output is explicitly **not** part of the first observable output.

## 7. Next hardware milestone (after "first instruction")

Ordered by documentation independence (architectural → proprietary):

1. **M1 — control transfer proven & repeatable**: our binary, our string,
   back over USB, ≥3 consecutive runs from cold DFU.
2. **M2 — exceptions**: our vector table catches a deliberate undefined
   instruction and reports it (proves we own `VBAR_EL1`).
3. **M3 — timer**: read `CNTPCT_EL0` twice around a known delay; record
   `CNTFRQ_EL0` (gives us timekeeping with zero SoC knowledge).
4. **M4 — memory report**: post the measured DRAM map; the matrix row
   "RAM exact physical map" flips UNKNOWN → CONFIRMED.
5. Only then consider: AIC identification, display RE, input, storage —
   each re-opening its own research sub-project.

## 8. QEMU's role

QEMU `virt` is a **laboratory, never a target**:

Allowed:
- assembling/validating generic AArch64 code (`start.S`, exception vectors);
- kernel-logic unit tests (data structures, allocators — later);
- debugging MMU/exception handling with `-d` tracing;
- CI that must run without hardware.

Forbidden:
- designing any component against QEMU's PL011 UART, GIC layout, `virt`
  memory map, or device tree and then presuming it applies to the A8;
- treating a green QEMU run as evidence about iPhone 6 behavior.

Every hardware-touching file declares its target in a banner comment:
```
// target: qemu-virt   (generic AArch64 virtual hardware)
// target: iphone6     (Apple A8 real hardware)
```
and platform code lives under `platform/qemu-virt/` vs `platform/apple-a8/`
so the pollution cannot happen by import accident.

## 9. Risks / blockers

| Risk | Severity | Mitigation |
|---|---|---|
| Host-side USB timing (checkm8 needs tight transfer timing; Linux + generic hubs can flake) | High for milestone 1 | use gaster-class async-with-short-timeout approach; direct port, no hubs; D2 (pongoOS) as fallback carrier |
| No public T7000 register docs (display/AIC/NAND/UART) | High long-term, **zero for M1–M4** | milestone ordering designed around it; RE iBoot when a peripheral becomes necessary |
| Inherited CPU/MMU state unknown | Medium | measure-first entry code (§2), vectors installed before anything risky |
| Tethered-only execution (every reboot = PC required) | Accepted | development device by policy |
| Old tooling vs Python 3.14 / modern libusb | Medium | vendor tools into `tools/`, pin/patch locally; C harness (gaster) preferred over Python |
| Policy risk: touching storage/iOS | High impact | tooling performs RAM-only, DFU-session-local operations; no flash/erase/restore commands exist in this repo |
| Single point of failure: checkm8 reliability | Medium | exploit is in mask ROM of millions of devices and actively used by checkra1n/palera1n; treat failures as harness bugs first |
| If neither D1 nor D2 can be made to run on this host | **BLOCKER** | stop, document in BOOT_FEASIBILITY.md, do not invent a workaround (task rule 13) |

## 10. Exact next implementation step

> **Do not write a kernel.** The next artifact is a *bootstrap probe* whose
> only job is to satisfy milestone "M1": reliably transfer control to our own
> code on the real iPhone 6 A8.

**Status (2026-10-08): the probe is BUILT and offline-verified, but M1 is
BLOCKED — see M1_REPORT.md.** Steps 1–4 below are done; step 5 (the physical
run) is blocked because (a) the iPhone 6 is not connected to the host, and
(b) in the earlier connected session, `gaster pwn` cycled RESET→SETUP→SPRAY→
PATCH (19×, all `ret: true`) but never reached `STAGE_PWNED` — a checkm8
host-timing/reliability failure, not a MyOS bug. MyOS code was never reached.

Concrete ordered steps:

1. **Install the minimal missing host deps** (authorized explicitly first):
   `sudo pacman -S --needed clang lld python-pyusb` (see TOOLCHAIN.md §2).
   **Not required for M1** — the payload builds with the present
   `aarch64-linux-gnu-gcc` and `m1ctl` links the present `libusb`.
   Not installed (host not modified, per policy).
2. **Vendor a checkm8 host driver** into `tools/checkm8/` (gaster or
   ipwndfu, upstream, unmodified at first) and verify against the device:
   enumerate DFU → read ECID/SRTG → confirm "pwned" state, *without sending
   anything else*. This is a read-mostly step.
   **DONE** — gaster (0x7ff, Apache-2.0, commit `7ffffff`) vendored
   unmodified at `tools/external/gaster/`; device enumeration + ECID/SRTG
   read confirmed (BOOT_FEASIBILITY §0). "Pwned" confirmation is the
   blocked step (M1_REPORT.md §5.2).
3. **Write the probe** `boot/probe/` — a ~200-line freestanding AArch64
   image: stack, park secondaries, install vectors, stash CPU-state block,
   emit the fixed report string through the carrier's USB channel (§6).
   **DONE (minimal form)** — `boot/aarch64/m1_entry.S` (104 bytes):
   freestanding AArch64, measures CurrentEL/MIDR_EL1/CNTFRQ_EL0/CNTPCT_EL0
   (guarded to EL1), writes the evidence block, returns `~challenge`.
   Delivered via the pwned-DFU exec interface (`tools/host/m1ctl.c`,
   gaster's native `dfu_send_data` + control-IN sequence). Offline-verified
   21/21 (`tests/offline_m1.sh`). Note: the probe deliberately does NOT
   install vectors or park secondaries at M1 — it runs inside SecureROM's
   DFU request hook, so it must not disturb sp/x30/x19/x20 (see
   `m1_entry.S` register discipline). Vectors/second-core parking are M2+.
4. **Run D1 path** (checkra1n `-k` / harness equivalent) and observe the
   report string on the host. If black-box → switch to **D2** (stock
   PongoOS + our module) to get the pongo USB console for free.
   **BLOCKED** — the D1 harness (`gaster pwn`) never reaches pwned state
   on this host (M1_REPORT.md §5.2). Next actions (M1_REPORT.md §7):
   reconnect the device; run `gaster pwn` in the docker container with the
   settle shim; try `USB_TIMEOUT=20`, direct port, cold-DFU re-entry;
   if still failing, escalate to the D2 carrier (PongoOS) which uses a
   different delivery path.
5. **Repeat 3× from cold DFU** (M1 acceptance). Record every observed value
   (EL, SCTLR, DRAM base, CNTFRQ) back into this document — several matrix
   rows flip from LIKELY/UNKNOWN to CONFIRMED at this point.
   **NOT REACHED** — requires step 4 to pass first.

Only after step 5 passes does any work on §7 milestones begin.

---

## Appendix A — Proposed source tree (design only, not yet created)

```
myos/
├── BOOT_FEASIBILITY.md        # evidence + matrix (this task)
├── TOOLCHAIN.md               # host tools (this task)
├── ARCHITECTURE.md            # this document
├── boot/                      # stage-0 carrier boundary
│   ├── aarch64/start.S        # _start: park cores, stack, vectors, report
│   └── probe/                 # M1 bootstrap probe image (next step)
├── platform/
│   ├── apple-a8/              # iPhone 6 / T7000 — REAL HARDWARE
│   │   ├── memmap.h           # measured constants only, sourced+dated
│   │   └── README.md          # what is measured vs inferred
│   └── qemu-virt/             # generic AArch64 virtual hardware (tests only)
├── arch/aarch64/              # architecture code with NO platform assumptions
│   ├── vectors.S
│   ├── cpu.h                  # CurrentEL/SCTLR/DAIF helpers
│   └── timer_generic.h        # ARMv8 generic timer (architectural)
├── kernel/                    # intentionally empty until §7 milestones
├── mm/                        # later
├── irq/                       # later (AIC research first)
├── drivers/                   # later; every file tagged target: qemuvirt|iphone6
│   ├── display/               # later (RE sub-project)
│   ├── input/                 # later
│   └── storage/               # later (policy: read-only until explicitly allowed)
├── fs/                        # later
├── userspace/                 # later
├── tests/                     # QEMU-runnable logic tests (target: qemu-virt)
├── tools/
│   └── checkm8/               # vendored host-side exploit driver (project-local)
└── Makefile                   # targets: probe-ios6 (iphone6), test-qemu (qemu-virt)
```

Separation rules:
- `boot/` and `platform/apple-a8/` may never include anything from
  `platform/qemu-virt/`.
- `arch/aarch64/` contains only architecture-legal code (no MMIO addresses).
- Every MMIO address in the repo must live under a `platform/` directory and
  carry a provenance comment (source or "measured 2026-xx-xx by probe M4").

## Appendix B — QEMU smoke-test plan (allowed use)

A tiny `qemu-virt` harness may be built in parallel to validate `start.S`,
the vector table, and generic-timer plumbing *as generic AArch64 code* —
useful precisely because those are architectural, not Apple-specific.
Its passing says nothing about the iPhone; it only keeps the arch code
honest cheaply.
