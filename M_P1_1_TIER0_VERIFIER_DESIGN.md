# M-P1.1 Tier-0 Verifier Design Document

**Status**: DESIGN ONLY — Implementation blocked by missing evidence (buffer address discovery)  
**Date**: 2026-10-11  
**Repository**: MyOS @ f7f89d5

---

## 1. Objective

Build a host-side tool that verifies the M-P1 probe bytes are correctly present in the PongoOS upload buffer **before** `bootr` execution, by reading the pre-initialized report header at buffer offset `0x200`:

| Offset | Field | Expected Value |
|--------|-------|----------------|
| 0x00   | magic | `0x313042525031504D` ("MP1PRB01") |
| 0x08   | version | `1` |
| 0x0C   | report_size | `0x60` (96 bytes) |

---

## 2. Verified Protocol (Source-Backed)

### 2.1 USB Endpoints (`pongoos-src/src/drivers/usb/synopsys_otg.c:1270-1335`)
| Endpoint | Address | Direction | Type | Used By |
|----------|---------|-----------|------|---------|
| EP0 | 0x00 | IN/OUT | Control | All shell I/O, upload control |
| EP2 | 0x02 | OUT | Bulk | **Upload only** (`usb_out_transfer_dma(2, ...)`) |
| EP1 | 0x81 | IN | Bulk | ktrw debug (not for shell) |

### 2.2 Control Transfer Requests (`pongoos-src/src/shell/usbloader.c:105-168`)

#### Host → Device (bmRequestType = 0x21, Class OUT)
| bRequest | wValue | wLength | Data Stage | Purpose |
|----------|--------|---------|------------|---------|
| 1 | 0 | 0 | — | Arm EP2 OUT for bulk upload |
| 1 | 0 | 4 | 4 bytes (new size) | Resize upload buffer |
| 2 | 0 | 0 | — | Discard uploaded data |
| 3 | 0 | 1..512 | command string | Write to stdin (shell) |
| 4 | 0 | 0 | — | Set non-blocking stdout |
| 4 | 1 | 0 | — | Set blocking stdout (wait for command) |
| 4 | 0xffff | 0 | — | Reset all blocking state |

#### Device → Host (bmRequestType = 0xA1, Class IN)
| bRequest | wValue | wLength | Response | Purpose |
|----------|--------|---------|----------|---------|
| 1 | 0 | 512 / 0x1000 | stdout bytes | Read console output |
| 2 | 0 | 1 | 1 byte (0/1) | Command completion status |

### 2.3 Shell Memory Commands (`pongoos-src/src/shell/main.c:126-176`)

| Command | Syntax | Address Space | Output Format |
|---------|--------|---------------|---------------|
| `md8` | `md8 <base> <size>` | **Virtual (cacheable VA)** | `0x<addr>: <hex bytes...>\n` (16 bytes/line) |
| `peek` | `peek <addr>` | **Virtual (cacheable VA)** | `0x<addr>: <hex32> (<b0> <b1> <b2> <b3>)\n` |
| `physdump` | `physdump <base>` | **Physical** | Maps at 0xc10000000, hexdumps 0x4000 bytes |

### 2.4 Upload Buffer Allocation (`pongoos-src/src/shell/usbloader.c:94-99`, `mm.c:887-901`)

```c
// usbloader.c:94-99
void usbloader_init() {
    loader_xfer_recv_data = alloc_contig(UPLOADSZ);  // UPLOADSZ = 1 MiB
    loader_xfer_size = UPLOADSZ;
    loader_xfer_recv_size = UPLOADSZ;
    loader_xfer_recv_count = 0;
}

// mm.c:887-895
void* alloc_contig(uint32_t size) {
    return phystokv(alloc_phys(size));
}
void* phystokv(uint64_t paddr) {
    return (void*)(paddr - 0x800000000 + kCacheableView);  // kCacheableView = 0x400000000
}
uint64_t vatophys_static(void* kva) {
    return (((uint64_t)kva) - kCacheableView + 0x800000000);  // VA + 0x400000000
}
```

**Consequences:**
- Buffer VA ∈ **[0x400000000, 0x500000000)** (kCacheableView range, 1 GiB window)
- Buffer PA = Buffer VA + **0x400000000**
- Buffer VA is **not fixed** — depends on `alloc_static_base` which depends on `gBootArgs->topOfKernelData` (build-dependent)

### 2.5 Probe Report Layout (`boot/aarch64/pongo_probe.S:93-196`, `pongo_probe.ld:29-32`)

```
File offset 0x200 (RPT_OFF):
+0x00: u64 magic = 0x313042525031504D  ("MP1PRB01")
+0x08: u32 version = 1
+0x0C: u32 report_size = 0x60
+0x10: u64 x30 (buffer PA)       ← DYNAMIC, zero in file
+0x18: u64 x0 (gBootArgs VA)     ← DYNAMIC, zero in file
+0x20: u64 x8 (gEntryPoint)      ← DYNAMIC, zero in file
+0x28: u64 CurrentEL             ← DYNAMIC, zero in file
+0x30: u64 SCTLR_EL1             ← DYNAMIC, zero in file
+0x38: u64 MIDR_EL1              ← DYNAMIC, zero in file
+0x40: u64 CNTPCT_EL0 sample 1   ← DYNAMIC, zero in file
+0x48: u64 CNTPCT_EL0 sample 2   ← DYNAMIC, zero in file
+0x50: u64 CNTFRQ_EL0            ← DYNAMIC, zero in file
+0x58: u64 reserved_zero         ← DYNAMIC, zero in file
```

**Key property**: The first 16 bytes (magic, version, size) are **statically initialized in the binary** (`.report` section, `pongo_probe.S:192-196`). They exist in the uploaded image before any device execution.

---

## 3. Verification Algorithm (Given Buffer Address)

```
1. Connect to device (VID:PID 05ac:4141)
2. Set blocking mode:     CTRL OUT 0x21/4/1/0
3. Send read command:
   Option A (VA known):   CTRL OUT 0x21/3/0/<len>  "md8 <VA+0x200> 0x60\n"
   Option B (PA known):   CTRL OUT 0x21/3/0/<len>  "physdump <PA+0x200>\n"
4. Poll completion:       CTRL IN 0xA1/2/0/1  → wait for 0
5. Read stdout:           CTRL IN 0xA1/1/0/0x1000  → collect hexdump
6. Parse hexdump for magic/version/size at expected offset
7. Validate: magic==MP1PRB01, version==1, size==0x60
8. Reset blocking:        CTRL OUT 0x21/4/0xffff/0
```

### Response Parsing

**md8 output** (16 bytes/line, address prefix):
```
0x400000200: 4d 50 31 50 52 42 30 31  01 00 00 00 60 00 00 00  MP1PRB01...`...
0x400000210: 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00  ................
...
```

**physdump output** (same format, but base is physical address mapped at 0xc10000000):
```
0xc1000200: 4d 50 31 50 52 42 30 31  01 00 00 00 60 00 00 00  MP1PRB01...`...
```

Parser must handle:
- Partial transfers (multiple CTRL IN reads)
- Truncated lines
- Address prefix variation
- Little-endian byte order in hexdump

---

## 4. The Buffer Address Discovery Gap (BLOCKER)

### 4.1 What We Need
Either:
- **Buffer VA** (for `md8` / `peek`) ∈ [0x400000000, 0x500000000)
- **Buffer PA** (for `physdump`) ∈ [0x800000000, 0x900000000)

### 4.2 Available Mechanisms

| Mechanism | Available? | Evidence |
|-----------|------------|----------|
| USB control request for buffer address | ❌ NO | `usbloader.c:105-168` — no such bRequest |
| Shell command to print buffer address | ❌ NO | `main.c:253-266` — no such command registered |
| Symbol lookup in PongoOS Mach-O | ⚠️ PARTIAL | Symbol `loader_xfer_recv_data` exported (`modload.c:1577`), but **slide unknown** |
| Brute-force scan via `md8` | ❌ IMPRACTICAL | 1 GiB / 0x1000 per read = ~1M commands; each requires 3+ CTRL transfers |
| Fixed/guessed address | ❌ FORBIDDEN | Allocator base depends on `topOfKernelData` (build-dependent) |

### 4.3 Slide Problem Detail

PongoOS is linked as a Mach-O with slide support:
- `entry.c:299`: `rebase_pc(gPongoSlide)` — applies slide at runtime
- `gPongoSlide` set by checkra1n exploit before jumping to PongoOS entry
- Slide is **not stored in USB-accessible memory**; no control request exposes it
- Host has the Mach-O (on Arch build machine) but **cannot compute runtime VA** without slide

> **M2_A17:61** (referenced in `pongo_probe.S:66-71`): "Pongo Mach-O lives on the Arch build machine, slide is a device-side unknown."

---

## 5. Proposed Solutions (All Require Changes Outside Current Scope)

| Solution | Requires | Feasibility |
|----------|----------|-------------|
| Add USB control request `bRequest=5` returning buffer VA/PA | `pongoos-src` edit | ❌ Forbidden |
| Add shell command `bufaddr` printing `loader_xfer_recv_data` | `pongoos-src` edit | ❌ Forbidden |
| Leverage `modload` Mach-O symbol resolution | Apple toolchain + device execution | ❌ Requires device |
| Probe self-reports address via stdout before parking | Probe modification + `bootr` execution | ❌ Violates "no bootr" |
| Host brute-forces likely allocator base range | Heuristic, not verified | ❌ Unreliable |

---

## 6. Recommendation

**Do not implement the verifier.** The buffer address discovery gap is fundamental and cannot be closed with current PongoOS source and constraints.

### If Implementation Were Required (Hypothetical)

```python
# Pseudocode — NOT IMPLEMENTED
class Tier0Verifier:
    def __init__(self, buffer_va: int):
        self.buffer_va = buffer_va  # MUST be provided externally
        self.report_va = buffer_va + 0x200
    
    def verify(self) -> bool:
        # 1. Set blocking
        # 2. Send: f"md8 {self.report_va:x} 60\n"
        # 3. Poll completion
        # 4. Read stdout, parse hexdump
        # 5. Check magic/version/size
        pass
    
    @staticmethod
    def discover_buffer_address() -> Optional[int]:
        # NO IMPLEMENTATION EXISTS
        # Would require one of the solutions in Section 5
        return None
```

### Fail-Closed Requirement
Any implementation **must** fail with a clear error if `discover_buffer_address()` returns `None`. No fallback to guessed addresses.

---

## 7. Distinction: Upload Verification vs Execution Verification

| Aspect | Tier-0 (This Design) | Tier-1+ (Post-bootr) |
|--------|----------------------|----------------------|
| **What** | Probe bytes in upload buffer | Probe execution + dynamic register capture |
| **When** | Pre-`bootr` | Post-`bootr` |
| **Mechanism** | Shell `md8`/`physdump` + USB CTRL | `lsusb` watch (05ac:4141 disappearance) |
| **Report Fields** | Static header only (magic/version/size) | Full dynamic report (x30, x0, x8, EL, MIDR, timers) |
| **Device State** | PongoOS shell alive | PongoOS torn down, probe parked |
| **Verifiable Now** | **BLOCKED** (no buffer address) | **BLOCKED** (requires device) |

---

## 8. Acceptance Criteria for Future Implementation

If buffer address discovery is resolved, the verifier must:
- [ ] Parse `md8`/`physdump` hexdump output correctly (little-endian, 16 bytes/line)
- [ ] Validate all three static header fields
- [ ] Handle partial CTRL IN transfers (reassemble)
- [ ] Handle command timeout (poll completion with max retries)
- [ ] Fail closed if buffer address unavailable
- [ ] Unit test with mocked USB responses (valid/invalid/truncated/timeout)
- [ ] Zero dependencies beyond `libusb-1.0` (already in `m1ctl`)

---

## 9. Source Reference Index

| Claim | Source |
|-------|--------|
| EP2 is upload-only bulk OUT | `synopsys_otg.c:1278-1280`, `usbloader.c:89,111` |
| Control request 0x21/3 = stdin write | `usbloader.c:119-121` |
| Control request 0xA1/1 = stdout read | `usbloader.c:150-160` |
| Control request 0xA1/2 = cmd completion | `usbloader.c:162-165` |
| `md8` syntax: `md8 <base> <size>` | `main.c:126-140` |
| `physdump` syntax: `physdump <base>` | `main.c:141-151` |
| `md8` uses virtual address | `main.c:139` — `hexdump((void*)base, size)` |
| `physdump` uses physical address | `main.c:148-150` — `map_range(0xc10000000, base, ...)` |
| `kCacheableView = 0x400000000` | `pongo.h:344` |
| `phystokv` = `paddr - 0x800000000 + kCacheableView` | `mm.c:893-895` |
| `vatophys_static` = `kva - kCacheableView + 0x800000000` | `mm.c:896-901` |
| `loader_xfer_recv_data = alloc_contig(1MiB)` | `usbloader.c:95` |
| `bootr` jumps to buffer PA | `entry.c:311` |
| USB/serial torn down pre-jump | `entry.c:232, 272` |
| Probe report static header in binary | `pongo_probe.S:192-196`, `pongo_probe.ld:29-32` |
| Probe checks pass (magic at 0x200) | `tests/check_pongo_probe.py` output |
| No USB request for buffer address | `usbloader.c:105-168` (exhaustive) |
| No shell command for buffer address | `main.c:253-266` (exhaustive registration) |
| Symbol exported but slide unknown | `modload.c:1577`, `entry.c:299`, `pongo_probe.S:66-71` |

---

## 10. Conclusion

**The Tier-0 verifier cannot be implemented with current evidence.** The static probe header exists in the uploaded binary and the USB/shell protocol for memory read-back is fully documented, but **no mechanism exists for the host to discover the upload buffer address**.

This is not a code deficiency — it is a PongoOS architecture limitation. The verifier design is complete and correct *given* the buffer address; the missing piece is exclusively the address discovery path.

**Next step**: Either accept that pre-`bootr` upload verification requires manual address input (human reads `md8` output), or extend PongoOS with a buffer address query (requires `pongoos-src` changes, forbidden in current scope).