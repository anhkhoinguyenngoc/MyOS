# BOOT_FEASIBILITY.md — iPhone 6 (Apple A8) Boot-Chain & Security Audit

Status: research complete, no kernel code written.
Date: 2026-10-08
Target: iPhone 6, Apple A8 (codename T7000), AArch64, iOS 12.5.7
Host: Arch Linux x86_64, Lightning→USB cable

---

## 0. Direct evidence captured from OUR host (primary source)

The device was enumerated on this machine during this audit (`lsusb -v -d 05ac:1227`):

```
idVendor  = 0x05ac  Apple, Inc.
idProduct = 0x1227  Mobile Device (DFU Mode)
iSerial   = "CPID:7000 CPRV:11 CPFM:03 SCEP:01 BDID:06
             ECID:00022CDA36100026 IBFL:1C SRTG:[iBoot-1992.0.0.1.19]"
```

What this confirms:

| Field | Value | Meaning |
|---|---|---|
| CPID:7000 | T7000 | Apple A8 — matches the claimed target SoC |
| CPFM:03 | production | production-mode chip (not a dev board) |
| SRTG | iBoot-1992.0.0.1.19 | the SecureROM (BootROM) build string running right now |
| IBFL:1C | DFU flags | device is in the DFU boot path of the BootROM |
| ECID | 00022CDA36100026 | unique chip id — needed by exploit tooling for nonces |

The SecureROM version `iBoot-1992.0.0.1.19` predates every checkm8 fix (the
fix only ever shipped in *newer* SoCs; mask ROM cannot be patched).
checkm8 (CVE-2019-8900) explicitly covers A5–A11, which includes T7000/A8.
⇒ **The root of trust of this exact device is publicly known to be
exploitable, and we can talk to it over USB today.** That is the single most
important feasibility fact in this document.

---

## 1. iPhone 6 boot chain (as designed by Apple)

Sources: Apple Platform Security guide [S2], The Apple Wiki boot/DFU pages [S3][S4],
checkm8 analyses [S1][S5][S6].

### Normal boot
```
BootROM (SecureROM, mask ROM, immutable)
  → verifies & loads LLB (signed, APTicket on A7+)
    → LLB initializes the rest of the SoC, verifies & loads iBoot
      → iBoot initializes display/USB/storage, verifies & loads kernelcache (XNU)
        → XNU kernel handoff (boot-args, device tree, memory map)
```

### DFU / Recovery boot (the path that matters to us)
```
BootROM runs → USB DFU mode (PID 05ac:1227)
  normal: accepts signed iBSS → iBEC → restore/restoreOS chain
  pwned:  checkm8 exploit in the BootROM USB stack → attacker code runs
```

- DFU is *part of the SecureROM* — it cannot be removed or updated [S3].
- On A7+ DFU recognizes APTickets and generates ApNonce; a signed image is
  normally required to leave DFU.
- BootROM does hardware init itself (CPU from reset, minimal DRAM init so the
  first image can be loaded into RAM, USB controller for DFU).
- There is **no documented path where iBoot loads an arbitrary/unsigned
  kernel**. The normal iBoot→kernel handoff requires Apple signatures.
  Rule from the task: do not assume iBoot will execute an ELF — it will not.

### CPU execution levels
- A8 is ARMv8.0-A (ARM64), dual-core Apple "Typhoon" @ ~1.4 GHz, 1 GB LPDDR3-1600,
  PowerVR GX6450 GPU [S7][S8].
- Apple's stack convention (BootROM/iBoot/XNU) has XNU at EL1; higher ELs
  (EL3 monitor) are used on some SoCs [S9]. **The exact EL/MMU state we inherit
  when executing attacker code on T7000 is UNKNOWN until measured** (read
  `CurrentEL`, `SCTLR_EL1`, `TTBR0_EL1` in our own stub and report them).
  This is a 10-line measurement, not a blocker.

---

## 2. Apple A8 — hardware documentation status

| Subsystem | Documented publicly? | Notes |
|---|---|---|
| CPU | Yes | 2× Typhoon, ARMv8-A, AArch64 [S7][S8] |
| RAM | Partially | 1 GiB LPDDR3; **exact physical base/size must be confirmed on-device** (older Apple SoCs conventionally place DRAM high; do not trust any address until measured) |
| Interrupt controller | Not for A8 | Apple AIC is documented for M1-class SoCs (Asahi/m1n1) [S10]; T7000 register map not publicly documented → derive from SecureROM/iBoot reverse engineering later |
| Timer | Not for A8 | Same as above; generic ARMv8 generic timer (CNTPCT_EL0) is always available and is the safest first timer |
| UART/debug | Not practical | No public iPhone 6 UART pinout; board-level debug would need hardware probing (DEF CON 30 "Hitchhacker's Guide" shows Lightning/JTAG exploration [S11]). Treat serial console as unavailable. |
| Display | Not for A8 | iBoot draws the Apple logo, so a framebuffer path exists inside Apple's code; registers undocumented → late milestone |
| Storage (NAND) | Not for A8 | Apple ANS controller, encrypted, proprietary → not needed for first boot |
| USB/Lightning | Yes, functionally | We speak DFU protocol today; on-device USB driver only needed if *we* re-implement the transport (see §3) |

⇒ For the FIRST milestone, the only peripherals we need are: CPU + RAM + the
USB stack **already running in the BootROM/our payload**. No GIC, no display,
no NAND.

---

## 3. DFU — what it actually is and what it allows

- USB PID `05ac:1227` = Apple DFU (A9-and-older generation), iBoot-based DFU
  protocol: a DFU-1.1-style state machine (`DFU_DNLOAD`, `DFU_UPLOAD`,
  `DFU_GETSTATUS`, `DFU_CLRSTATUS`, `DFU_ABORT`) plus Apple-specific vendor
  requests (serial string read = how we got the CPID/SRTG string) [S3][S4].
- Normal DFU operations: upload firmware images (img4/im4p) **that pass
  SecureROM signature+ticket checks**. Data accepted = signed iBSS (or a
  payload in pwned state). Nothing else executes.
- **Security boundary:** SecureROM signature verification. It is the whole
  ballgame. DFU by itself does NOT allow arbitrary code.
- checkm8 breaks that boundary *in DFU*:
  - Use-after-free in the DFU data phase: an incomplete transfer leaves the
    global `ep0DataPhaseBuffer` pointing at the IO buffer; `DFU_ABORT`/USB
    reset frees the buffer without resetting the globals; on DFU re-entry the
    stale pointer is used [S1][S5][S6].
  - On **A8/A8X/A9 specifically, a DFU abort bug allows direct code execution
    without ROP/JOP and without the extra heap leak** needed on A7/A10/A11
    [S4]. This makes A8 one of the *easiest* checkm8 targets.
  - Exploitation finishes by overwriting a queued `usb_device_io_request`
    (callback + next pointer), then a USB reset walks the hijacked callback
    chain into attacker shellcode sent over USB [S1][S6].
- After pwn: attacker shellcode runs in BootROM context, can (a) patch the
  signature-check routine in RAM for the session, (b) receive and jump to a
  second-stage image over USB. This is exactly how checkra1n's stage2 and
  pongoOS are delivered. It is **tethered** (RAM-only): every reboot returns
  to a clean BootROM. Nothing persistent is written to NAND.

**Conclusion for the task's rule #5:** DFU alone does *not* allow arbitrary
kernel execution. Arbitrary code execution comes from checkm8 (a memory
corruption bug in the BootROM USB stack), with DFU merely being the USB
transport it lives in.

---

## 4. Existing open-source research we build on (not copy)

| Project | Relevance to MyOS |
|---|---|
| axi0mX **ipwndfu** [S12] | Reference Python implementation of checkm8; pwned-DFU helpers. Blueprint for our host-side harness. |
| 0x7FF **gaster** [S1] | C/libusb checkm8 implementation, works on Linux — likely our host tool. |
| **checkra1n** [S13][S14] | Product-quality chain: checkm8 → stage1 → stage2 → pongoOS. Supports iPhone 5s–X (A7–A11) ⇒ iPhone 6 supported. **Since 0.9.8.1 accepts a custom image replacing pongoOS (`checkra1n -k <img>`)** [S9] — this is our documented "boot my own code" door. |
| **pongoOS** [S13] | Pre-boot AArch64 execution environment (bare-metal, Mach-O modules, USB shell). `Pongo.bin` is "a bare metal binary that can be jumped to" [S13]. Example modules exist [S15]. Our preferred MyOS *carrier* for milestone 1. |
| **palera1n** [S16] | Modern checkm8 jailbreak (iOS 15+); relevant as workflow reference only (our device is iOS 12). |
| **m1n1 / Asahi** [S10] | Architecture-relevant only: Apple SoC conventions, AIC/UART/timer docs for M1-class. NOT directly portable to A8 register-wise. |
| **libimobiledevice / libirecovery** [S17] | DFU/recovery USB utilities; `irecovery` reads the CPID/SRTG string (we already got it via lsusb). |
| The Apple Wiki [S3][S4][S8] | Device/SoC/boot-mode reference. |
| XNU open source [S18] | Platform-expert code for older Apple SoCs — a *source* of peripheral addresses once we need them; must be cross-checked on-device. |

Nothing here is an OS we can just adopt: pongoOS is a *bootstrap carrier*,
not a general-purpose OS. MyOS remains our own code.

---

## 5. SECURITY / FEASIBILITY MATRIX

Legend: **CONFIRMED** = backed by primary evidence (our device, upstream docs,
or replicated by established projects). **LIKELY** = strong secondary evidence,
not yet validated on this host. **UNKNOWN** = no reliable public data; must be
measured/reverse-engineered. **BLOCKED** = mechanism prevents us, not
resolvable by effort we are willing to spend now.

| Stage | Component | Can we control it? | Evidence | Required mechanism | Status |
|------|-----------|--------------------|----------|--------------------|--------|
| BootROM (SecureROM iBoot-1992.0.0.1.19) | USB DFU stack in mask ROM | Yes | CVE-2019-8900 checkm8 covers A5–A11 [S1]; A8 uses DFU-abort path: direct code exec, no ROP needed [S4]; our device reports the vulnerable SRTG (§0) | checkm8 exploit over USB (ipwndfu/gaster/checkra1n class tool) | CONFIRMED (vulnerability + tooling); local run pending |
| BootROM | Device enumeration / transport | Yes | We read the full CPID/SRTG/ECID descriptor on this host today (§0) | libusb + DFU control transfers | CONFIRMED |
| DFU | What we can send | Yes, within DFU | DFU-1.1 requests + Apple vendor requests [S3][S4]; normally only signed iBSS accepted | `DFU_DNLOAD` transfers; post-pwn arbitrary payload | CONFIRMED |
| payload / handoff stage | checkm8 shellcode → second stage | Yes | checkra1n chain checkm8→stage1→stage2→pongoOS [S13][S9]; ipwndfu/gaster do the same [S1][S12] | Run shellcode in BootROM context, receive next image over USB, patch session sigcheck or jump directly | CONFIRMED (mechanism); our own run pending |
| payload / handoff stage | **Our** tiny MyOS payload as carrier | Yes | checkra1n ≥0.9.8.1 boots custom image replacing pongoOS (`-k`) [S9][S13]; alternatively load MyOS as a pongoOS module (`modload` over USB shell) [S13][S15] | Build AArch64 bare-metal/Mach-O artifact, hand it to checkra1n `-k` or pongo shell | LIKELY (documented paths, not yet exercised by us) |
| iBoot | Normal iBoot→kernel path | No | iBoot only loads Apple-signed images; no ELF/unsigned execution path [S2][S3] | *(none — not used in chosen architecture)* | BLOCKED by design → **not required** |
| kernel entry | Replacing XNU via iBoot | No | Same as above | Not the chosen handoff; MyOS is entered from the pwned payload stage instead | BLOCKED by design → **not required** |
| A8 CPU execution | Running our own AArch64 code on T7000 | Yes | checkra1n/pongoOS routinely execute AArch64 code on A8-class SoCs [S13]; CPU is standard ARMv8-A [S7] | Payload loaded by the checkm8/pongo stage; inherit CPU state, read CurrentEL/SCTLR to characterize it | CONFIRMED (others do it); our first instruction pending |
| RAM initialization | DRAM usable without our own init code | Almost certainly | BootROM must init DRAM to load LLB/images into RAM [S2][S5]; pongoOS and payloads run from DRAM [S13] | Inherit BootROM-initialized DRAM; validate with a memory test + report RAM base/size | LIKELY (validate at entry; exact physical map UNKNOWN) |
| RAM | Exact physical memory map (base, size, holes) | Unknown | No public T7000 memory map found | Read on-device (payload reports observed DRAM, iBoot memory descriptors from pwned state) | UNKNOWN (measurable in milestone 1) |
| USB (post-pwn) | Talking back to the PC | Yes | pongoOS provides a USB shell used by pongoterm/ipsw [S13][S19]; checkm8 payload itself receives images over USB [S1][S6] | Reuse pongoOS USB stack (as module) or BootROM USB code (as raw payload) | LIKELY |
| Display | Framebuffer output | Later | iBoot renders logo ⇒ HW exists; no public A8 display register docs | Reverse-engineer iBoot display init (DFU/iBoot RE) | UNKNOWN (deferred) |
| Input | Touch/buttons | Later | No public A8 touch controller docs; buttons work in BootROM (DFU entry combo) [S4] | RE of button/touch paths | UNKNOWN (deferred) |
| Storage | NAND read/write | Later/avoid | ANS controller proprietary, storage encrypted; **task forbids flashing/erasing** | — | BLOCKED (by policy + docs) for now |
| Debug output | UART serial console | Probably not | No public iPhone 6 UART pinout; Lightning/JTAG research exists but is hardware work [S11] | Board probing (out of scope initially) | UNKNOWN → design around USB output instead |
| Timer / IRQ | Generic ARM timer | Yes | ARMv8 generic timer is architectural (CNTPCT_EL0 etc.), no SoC docs needed | Direct register access, no driver needed initially | LIKELY (measure) |
| Timer / IRQ | Apple AIC interrupt controller | Unknown | Documented only for Apple Silicon M-class [S10] | RE from SecureROM/iBoot/XNU [S18] | UNKNOWN (deferred; poll timer first) |
| Secondary cores | SMP | Later | postmarketOS lists Apple A8 SMP bring-up as an open testing item [S20] | RE of core release mechanism | UNKNOWN (single-core first) |

### Hard blockers found
1. **No public register-level documentation for T7000 peripherals** (display,
   touch, AIC, NAND, UART). Mitigation: first milestone needs none of them;
   later milestones require SecureROM/iBoot reverse engineering. This is a
   schedule risk, not a boot-path blocker.
2. **Unsigned boot is tethered.** Every power cycle requires the PC again.
   Accepted: this is a development device.
3. **Nothing in this audit has yet executed code on the device.** All
   "CONFIRMED" rows are confirmed as *mechanisms* by primary sources; our own
   first execution is the next milestone (see ARCHITECTURE.md §10).

---

## 6. Answer to the core question (short version)

> "How does MyOS get from an Arch Linux PC connected over Lightning to
> executing its first instruction on the iPhone 6 A8?"

```
Arch Linux PC
  → libusb tool (gaster/ipwndfu/checkra1n class)
  → Lightning/USB, DFU (05ac:1227)                 [CONFIRMED on our host]
  → checkm8 exploit of SecureROM USB stack         [CONFIRMED mechanism, A8 = easiest path]
  → attacker stage1 shellcode in BootROM context   [CONFIRMED mechanism]
  → second stage over USB: custom image via
    checkra1n -k,  OR pongoOS + MyOS module        [LIKELY, documented upstream]
  → MyOS entry point on A8                         [next milestone]
```

iBoot, kernelcache signing and the normal iOS boot path are **bypassed, not
conquered** — MyOS never goes through them.

---

## 6b. M1 physical-test session results (addendum, 2026-10-08)

Status of the first attempt to exercise the chain in §6 on
real hardware. Full detail in M1_REPORT.md.

- Device WAS enumerated on this host (full iSerial captured,
  §0). At report time it is NOT connected (`lsusb -d 05ac:1227`
  empty), so no physical test can run.
- `gaster pwn` (vendored, commit `7ffffff`, unmodified) was
  run repeatedly. Two failure modes:
  1. Stall after `Stage: SPRAY` — host udev re-enumeration
     race (diagnosed via strace; the post-reset `/dev/bus/usb`
     node is not ready when gaster's fresh libusb context
     scans, leaving a stale device-less cache). Mitigation
     built: `build/usb_settle_shim.so` (LD_PRELOAD, timing-only,
     alters no USB traffic).
  2. With the shim: 19 full RESET→SETUP→SPRAY→PATCH cycles,
     every stage `ret: true`, but `STAGE_PWNED` never reached
     — `Now you can boot untrusted images.` and the
     `PWND:[checkm8]` serial marker never appeared. The PATCH
     overwrite was delivered (expected STALL observed) but the
     pwned state did not survive the subsequent USB reset.
- Stage tallies across all runs: RESET=36, SETUP=28, SPRAY=28,
  PATCH=19, STAGE_PWNED=0.
- Interpretation: checkm8 host-timing/reliability failure on
  this host + port + cable (the High-severity risk already
  flagged in ARCHITECTURE.md §9). NOT a MyOS/payload bug —
  MyOS code was never reached.
- Matrix rows that said "our run pending" / "our first
  instruction pending" remain **not yet exercised by us**:
  the mechanism is CONFIRMED by primary sources, but our own
  execution is BLOCKED (see M1_REPORT.md §7 for the exact
  next action). No destructive workaround was attempted.

---

## Sources

- [S1] Technical analysis of the checkm8 exploit (a1exdandy) — https://www.elabforensics.com/wp-content/uploads/sites/4/2021/01/Technical-analysis-of-the-checkm8-exploit.pdf
- [S2] Apple Platform Security — Boot process for iPad and iPhone — https://support.apple.com/guide/security/boot-process-for-ipad-and-iphone-devices-secb3000f149/web
- [S3] The Apple Wiki — DFU Mode — https://theapplewiki.com/wiki/DFU_Mode
- [S4] The Apple Wiki — checkm8 Exploit — https://theapplewiki.com/wiki/Checkm8_Exploit
- [S5] littlelailo — checkm8 vulnerability writeup (referenced by [S1][S6])
- [S6] Alfie CG — A comprehensive write-up of the checkm8 BootROM exploit (2023) — https://alfiecg.uk/2023/07/21/A-comprehensive-write-up-of-the-checkm8-BootROM-exploit.html
- [S7] Wikipedia — Apple A8 — https://en.wikipedia.org/wiki/Apple_A8
- [S8] The Apple Wiki — A8 (T7000) — https://theapplewiki.com/wiki/A8
- [S9] N. Bassen — "The One Weird Trick SecureROM Hates vol. 2" (nullcon) — https://nullcon.net/wp-content/uploads/2026/05/The-One-Weird-Trick-SecureROM-Hates-vol.-2-by-Nikias-Bassen_.pdf
- [S10] Asahi Linux docs / m1n1 — Apple SoC (AIC, UART) documentation — https://asahilinux.org/docs/hw/soc/serial-debug/
- [S11] DEF CON 30 — The Hitchhacker's Guide to iPhone Lightning and JTAG Hacking; lambdaconcept Bonobo/OpenOCD blog — https://blog.lambdaconcept.com/post/2019-10/iphone-bootrom-debug/
- [S12] axi0mX — ipwndfu — https://github.com/axi0mX/ipwndfu
- [S13] checkra1n — PongoOS — https://github.com/checkra1n/pongoOS
- [S14] checkra1n — https://checkra.in/
- [S15] PongoBRIX (example pongoOS module) — https://github.com/pixelomer/PongoBRIX
- [S16] The Apple Wiki — Palera1n — https://theapplewiki.com/wiki/Palera1n
- [S17] libimobiledevice — https://libimobiledevice.org/
- [S18] Apple Open Source — XNU — https://github.com/apple-oss-distributions/xnu
- [S19] blacktop ipsw — pongoOS shell guide — https://blacktop.github.io/ipsw/docs/guides/pongo/
- [S20] postmarketOS — Apple A8 / iPhone 6 (apple-n61) testing status — https://wiki.postmarketos.org/wiki/Apple_A8
- [S21] CERT VU#941987 — Apple devices vulnerable to arbitrary code execution in SecureROM — https://www.kb.cert.org/vuls/id/941987/
- [S22] Trail of Bits — Tethered jailbreaks are back (checkm8) — https://blog.trailofbits.com/2019/09/27/tethered-jailbreaks-are-back/
