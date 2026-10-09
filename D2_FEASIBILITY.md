# D2 Feasibility — PongoOS as a Bootstrap Bridge for iPhone 6 / A8 / CPID 0x7000

## Executive Summary
PongoOS is a credible, open-source (MIT), source-confirmed pre-boot execution
environment that runs on Apple A8 (iPhone 6) via checkra1n. It provides a well-defined
module system (`modload`) with an official example, a full PongoOS public API (including
`puts`, `command_register`, `get_el`, `soc_name`), and an observable USB-shell channel.
The recommended integration sub-path is **D2a: MyOS as a PongoOS Mach-O module**, which
inherits PongoOS's EL1 environment, MMU, drivers, newlib, and the USB console for free.

**Critical caveat:** D2 does **NOT** escape the checkm8 dependency that blocked M1.
checkra1n loads PongoOS via `checkra1n -k Pongo.bin`, which first requires checkra1n's
checkm8 (CVE-2019-8900) to reach pwned DFU — the same BootROM exploit gaster uses.
The device-side (BootROM mask ROM) is identical; only the host-side implementation
differs (checkra1n closed-source/product-quality vs gaster open-source). Whether
checkra1n's checkm8 completes on this host is the **gating unknown** that the next
experiment tests.

**Decision: D2-GO** (see D2_REPORT.md). The next experiment is defined in D2_RUNBOOK.md
and is NOT executed automatically.

---

## The D2 Chain
```
PC (host)
  │  checkra1n Linux x86_64 CLI (closed-source binary)
  ▼
checkm8  (CVE-2019-8900, BootROM mask-ROM exploit, SAME as gaster)
  │  DFU 05ac:1227  →  pwned DFU
  ▼
checkra1n -k Pongo.bin
  │  stage1 trampoline branches to PongoOS entry (x27/x29)
  ▼
PongoOS  (EL1, MMU on, newlib, USB shell, drivers)
  │  USB shell: vendor requests 0x21/0xA1
  ▼
host-side USB-shell client (pongoterm-class; libusb)
  │  upload myos.module (bulk OUT ep2) + send "modload\n" (stdin)
  ▼
PongoOS dynamic loader (modload)  →  myos.module
  │  module_entry() runs
  ▼
MyOS AArch64 code executes on A8  →  puts("MYOS M1 OK")  →  USB stdout  →  PC
```

---

## Evidence Classification

### CONFIRMED BY SOURCE
- **PongoOS is open-source, MIT license.** README: "A pre-boot execution environment
  for Apple boards built on top of checkra1n." Repo: github.com/checkra1n/PongoOS.
  `PONGO_VERSION := 2.5.1-$(git log -1 --pretty=format:"%H" | cut -c1-8)`.
- **checkra1n supports iPhone 6 / A8.** checkra.in: "Jailbreak for iPhone 5s through
  iPhone X, iOS 12.0 and up." iPhone 6 (A8, CPID 0x7000) is in range. Latest: 0.12.4 beta.
- **checkra1n has a Linux build.** checkra.in/linux: Debian APT repo
  (`assets.checkra.in/debian`); for other distros (incl. Arch): "CLI builds are
  available for x86_64, arm, arm64 and i486 CPU types. Just download the latest
  binary for your CPU, and run the CLI."
- **PongoOS build toolchain.** Makefile: `EMBEDDED_CC=clang`,
  `EMBEDDED_LDFLAGS=-fuse-ld=/usr/bin/ld64`, `STRIP=cctools-strip`;
  `EMBEDDED_CC_FLAGS=--target=arm64-apple-ios12.0 -std=gnu17 -Wall -Wunused-label
  -Werror -O3 -flto -ffreestanding -U__nonnull -nostdlibinc`;
  `PONGO_LDFLAGS=-L$(LIB)/lib -lc -lm -Wl,-preload -Wl,-no_uuid -Wl,-e,start
  -Wl,-order_file,$(SRC)/sym_order.txt -Wl,-image_base,0x100000000
  -Wl,-sectalign,__DATA,__common,0x8 -Wl,-segalign,0x4000`. Requires `newlib`
  submodule (`DEP := newlib`, `LIB := $(DEP)/aarch64-none-darwin`).
- **PongoOS artifacts.** `Pongo` (Mach-O), `Pongo.bin` ("bare metal binary that can be
  jumped to"), `checkra1n-kpf-pongo`, `PongoConsolidated.bin` (autoboot magic
  `6175746F626F6F74...` + Pongo.bin + KPF). Load path: `checkra1n -k Pongo.bin`.
- **Handoff contract — entry.** `src/boot/entry.S`: entry `start`, reached via
  "branch from x27" / "branch from x29" (checkra1n stage1 trampoline). PongoOS
  self-copies to a fixed high address (`mov x5,#0x80000000; movk x5,#0x1800,lsl#16`
  → ~0x180000000 region). AUTOBOOT magic check. `_setup_el1`: if CurrentEL==EL1 →
  `blr x0`; if EL3 → set `vbar_el3`/`scr_el3`, `eret` to EL1.
- **Handoff contract — boot_args.** `src/boot/stage3.c`: `trampoline_entry` —
  `boot_args==NULL` → bootloader mode (`patch_bootloader`, Stage2 KJC Loader);
  `boot_args!=NULL` → kernel mode (`setup_el1(main,...)` then `jump_to_image`).
- **PongoOS shell.** `src/shell/main.c`: commands include `modload`, `bootr` (boot raw
  image), `ramdisk`, `shell`, `md8`, `peek`, `poke`, `physdump`, `spawn`, `paging`;
  `start_host_shell()` calls `usb_init()` then `queue_rx_string("shell\n")`.
- **PongoOS USB-shell protocol.** `src/shell/usbloader.c` (`ep0_device_request`):
  - OUT `bmRequestType 0x21`: bRequest 1/wLength 0 (bulk upload init, DMA OUT ep2);
    bRequest 1/wLength 4 (upload buffer resize); bRequest 2/wLength 0 (discard);
    bRequest 3/wLength 1..512 (write to stdin); bRequest 4 (control: wValue 0 = next
    stdin write stalls until command over, wValue 1 = stdout stalls until async
    check-in, wValue 0xffff = reset all).
  - IN `bmRequestType 0xA1`: bRequest 1/wLength 512 or 0x1000 (read stdout);
    bRequest 2/wLength 1 (async command completion status, returns `command_in_progress`).
  - `UPLOADSZ = 1MB` default, `UPLOADSZ_MAX = 128MB`; bulk OUT endpoint 2; DMA into
    `loader_xfer_recv_data`.
- **PongoOS module system.** `src/dynamic/modload.c` + `modload_macho.c`: modules are
  Mach-O files carrying a `__DATA,__pongo_exports` section; `PONGO_EXPORT(name)` macros
  generate the export table; `resolve_symbol(name)` resolves against the PongoOS public
  API; `link_exports()` links module exports into the PongoOS API chain; `modload`
  loads from the USB upload buffer (`loader_xfer_recv_data`).
- **PongoOS public API (available to modules).** Console: `puts`, `iprintf`, `printf`,
  `command_puts`, `command_putc`. Shell: `command_register`, `command_tokenize`,
  `queue_rx_string`. Tasks: `task_create`, `task_register`, `task_link`, `task_yield`,
  `task_current`, `task_exit`. Memory: `vm_allocate`, `vm_deallocate`, `alloc_contig`,
  `alloc_phys`, `map_range`, `phystokv`, `vatophys`. USB: `usbloader_init`,
  `resize_loader_xfer_data`. CPU: `get_el`, `disable_interrupts`, `enable_interrupts`,
  `invalidate_icache`, `cache_invalidate`. Device: `soc_name`, `socnum`, `gDevType`,
  `device_clock_by_id`, `clock_gate`. Time: `get_ticks`, `usleep`, `sleep`, `spin`.
  Watchdog: `wdt_reset`, `wdt_enable`, `wdt_disable`. Panic: `panic`.
- **Module contract — official example.** `example/testmodule`: Makefile builds a Mach-O
  kext (`-Wl,-kext`) with the same toolchain; `main.c` implements `module_entry()`,
  `command_register("hello","Hello world!",hello)`, `preboot_hook` chaining,
  `module_name="test_module"`, `exported_symbols[]`, `#include <pongo.h>`, `puts()`,
  `queue_rx_string("bootx\n")`.
- **PongoOS drivers present.** `src/drivers/`: aes, framebuffer, gpio, hal, mipi, plat,
  recfg, sep, timer, tz, uart, usb (Synopsys DesignWare OTG: `synopsys_otg.c`), xnu.
  USB driver present; framebuffer optional (headless USB-shell operation suffices for an
  M1-class proof).
- **A8 checkm8 profile already vendored.** `tools/external/gaster/gaster.c` L595-609:
  SRTG `iBoot-1992.0.0.1.19` → cpid `0x7000`, `patch_addr=0x100007E98`,
  `memcpy_addr=0x100010E70`, `aes_crypto_cmd=0x10000DA90`, `boot_tramp_end=0x1800E1000`,
  `gUSBSerialNumber=0x1800888C8`, `dfu_handle_request=0x180088878`,
  `usb_core_do_transfer=0x10000EBB4`, `dfu_handle_bus_reset=0x180088898`,
  `insecure_memory_base=0x180380000` (matches MyOS `M1_IMG_BUF_BASE`),
  `handle_interface_request=0x10000EEE4`, `usb_create_string_descriptor=0x10000E074`,
  `usb_serial_number_string_descriptor=0x18008062A`.
- **Host toolchain (present).** `g++`, `gcc`, `make`, `curl`, `wget`, `git`, `tar`,
  `python3`, `aarch64-linux-gnu-gcc`/`-ld` (binutils 2.47), `libusb` 1.0.30, `yay`
  (AUR helper).
- **Host toolchain (missing).** `clang`, `ld.lld`, `ld64`, `cctools-strip`,
  `python-pyusb`, `dfu-util`, `cmake`.

### INFERRED
- **D2a is the lower-risk integration path.** Because PongoOS supplies EL1, MMU, drivers,
  newlib, and the USB console, a MyOS module only needs to implement `module_entry()` and
  call `puts()` to get an observable proof — no need to bring up the environment from
  scratch. Backed by the official `example/testmodule`.
- **The observable channel is superior to the gaster exec interface.** The PongoOS USB
  shell gives a real console (`puts` → stdout → PC) rather than a magic-value memory
  probe, making "MyOS code ran on A8" directly observable.
- **checkra1n's checkm8 is product-quality.** It has been used on millions of devices;
  its host-side implementation is more mature than gaster's. This raises (but does not
  guarantee) the odds it completes on this host where gaster failed.
- **PongoOS USB shell enumerates as a distinct USB device after boot.** The DFU PID
  (`05ac:1227`) is the pre-pwn state; once PongoOS runs, its Synopsys OTG driver
  enumerates a new device the host client attaches to. (Exact PID is UNKNOWN — observe
  via lsusb during the next experiment.)

### UNKNOWN
- **Whether checkra1n's checkm8 completes on this host.** This is the gating unknown.
  gaster's checkm8 cycled RESET→SETUP→SPRAY→PATCH 19× without reaching pwned DFU.
  If the cause was host-level (USB controller, kernel 7.2.8, udev timing), checkra1n
  will likely fail too. If the cause was gaster-specific (its async 5 ms timeout
  approach), checkra1n may succeed. Cannot be determined without running it — which the
  D2 scope forbids now.
- **Exact USB PID of the PongoOS shell device.** Not located in the sources fetched
  (platform USB-descriptor code path not found under `src/`; `src/platform` 404s).
  Must be observed via `lsusb` after PongoOS boots.
- **checkra1n Linux CLI exact invocation for `-k` in non-interactive mode.** The CLI
  build exists; the precise flags for a headless `checkra1n -k Pongo.bin` run need to
  be confirmed from `checkra1n --help` at run time (the GUI app is the primary
  interface; CLI `-c`/`-k` flags are documented but exact semantics should be verified).
- **Whether a Linux pongoterm client exists.** `scripts/pongoterm` is described in the
  README as "an interactive shell client for macOS"; the raw path
  `scripts/pongoterm/pongoterm.py` 404s. A Linux host-side client may need to be
  written from the documented `usbloader.c` protocol via libusb.

### NOT APPLICABLE
- **A7 Linux limitation.** checkra.in known issues: "A7 devices will currently not work
  with the Linux version." A7 = iPhone 5s (CPID 0x8930), NOT A8. Does not apply to
  iPhone 6 / A8 / CPID 0x7000.
- **Host-hardware reliability note.** checkra.in: "The exploit may not work as reliably
  on some devices, such as the Raspberry Pi Zero and Raspberry Pi 3." This refers to the
  HOST computer, not the iPhone. This host is x86_64 Arch Linux (not a Pi), so the note
  does not directly apply, but it confirms checkm8 reliability is host-dependent.
- **Persistent modification.** checkm8/PongoOS are RAM-only and tethered; a reboot
  returns to stock iOS. No persistent-state concern for the next experiment.

---

## The Two Integration Sub-Paths

### D2a — MyOS as a PongoOS module (RECOMMENDED)
- MyOS is built as a Mach-O kext (`-Wl,-kext`) with a `__DATA,__pongo_exports` section.
- It implements `module_entry()`, registers a shell command via `command_register`,
  and calls `puts()` for console output.
- It is uploaded over USB (bulk OUT ep2) and loaded via the `modload` shell command.
- **Advantages:** inherits PongoOS EL1/MMU/drivers/newlib/USB console; observable proof
  is a `puts()` string on the PC; backed by the official `example/testmodule`.
- **What MyOS must provide:** `module_entry()`, `module_name`, `exported_symbols[]`,
  and any MyOS-specific logic. The environment is already up.

### D2b — MyOS as a custom `checkra1n -k` image
- MyOS replaces PongoOS entirely: `checkra1n -k myos.bin`.
- MyOS must satisfy the trampoline entry contract (`entry.S` `start`, self-copy to the
  fixed high address, EL1/EL3 demotion via `_setup_el1`) and bring up its own console,
  drivers, and memory management.
- **Disadvantages:** no free console, no free environment; must reimplement what PongoOS
  already provides. Harder, higher risk. Only justified if MyOS must own the entire
  pre-boot environment rather than run as a module.

---

## Limitations
1. **D2 does not escape checkm8.** The DFU→pwned step is CVE-2019-8900, identical to
   gaster's. This is the single most important finding.
2. **checkra1n is closed-source (binary only).** Its host-side checkm8 cannot be audited
   from source. PongoOS, the kernel patchfinder, and the SEP exploit are open-source;
   the checkra1n app is not.
3. **Semi-tethered.** A reboot without checkra1n reverts the device to stock iOS. MyOS
   would need to be re-injected after every reboot.
4. **Linux checkm8 has device-specific caveats** (the A7 exclusion proves Linux builds
   are not universally reliable). A8 is not excluded, but reliability is not guaranteed.
5. **Host-dependent reliability.** checkm8 success depends on the host USB stack; this
   host (kernel 7.2.8, specific USB controller) is exactly the variable that defeated
   gaster.

## Toolchain Gaps (to build PongoOS + a MyOS module)
- `clang` — the PongoOS cross compiler (`EMBEDDED_CC`). Not in Arch repos; install via
  `yay -S clang` or build. (Do NOT auto-run.)
- `ld64` — Apple's Mach-O linker, REQUIRED (not lld): PongoOS uses Mach-O-specific
  linker flags (`-Wl,-kext`, `-Wl,-preload`, `-Wl,-order_file`, `-Wl,-image_base`,
  `-Wl,-sectalign`, `-Wl,-segalign`, `-Wl,-no_uuid`, `-Wl,-dead_strip`, `-Wl,-Z`) that
  lld's Mach-O mode does not fully support. Not in Arch repos; build from Siguza/ld64
  (reachable, HTTP 301) using the present `g++`.
- `cctools-strip` — the Mach-O strip tool (`STRIP`). Build from cctools source.
- `newlib` — PongoOS submodule (`git submodule update --init --recursive`).
- `python-pyusb` — for a host-side USB-shell client (if not using pongoterm).
- `checkra1n` Linux x86_64 CLI binary — download from checkra.in (the Debian assets
  host `assets.checkra.in` returned HTTP 403 to a plain curl; obtain via browser or
  the documented CLI download).
- Exact install commands are listed in D2_RUNBOOK.md and are NOT auto-run.

## Gating Unknown (restated)
The entire D2 path hinges on one question: **does checkra1n's checkm8 reach pwned DFU on
this host?** M1 proved gaster's checkm8 does not. D2 trades gaster's open-source
implementation for checkra1n's closed-source product-quality one, on the same device-side
exploit. The next experiment (D2_RUNBOOK.md) is designed to answer exactly this, safely
and reversibly, before any MyOS code is attempted.
