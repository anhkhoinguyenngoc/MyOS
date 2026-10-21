# MyOS — M1 build (target: iphone6 real hardware for payload;
#                    host x86_64 for m1ctl; QEMU NOT used in M1)
#       Kernel build (target: qemu-virt for runtime testing)

CROSS   := aarch64-linux-gnu-
CC_HOST := gcc
BUILD   := build

CROSS_CC  := $(CROSS)gcc
CROSS_LD  := $(CROSS)ld
CROSS_OBJCOPY := $(CROSS)objcopy

# ========================================================================
# M6 platform selection — the QEMU-vs-physical-target boundary.
# QEMU `virt` is the only implemented, known-good platform. apple-a8 is
# declared under platform/apple/a8/ as UNSUPPORTED (contract only); selecting
# it fails the build on purpose so QEMU code cannot be reused under an
# "iphone6" name. See PLATFORM_INTERFACE.md.
# ========================================================================
PLATFORM ?= qemu-virt

ifeq ($(PLATFORM),qemu-virt)
  PLATFORM_DEFS := -DMYOS_PLATFORM_QEMU_VIRT=1
else ifeq ($(PLATFORM),apple-a8)
  $(error PLATFORM=apple-a8 is UNSUPPORTED: no validated boot handoff, memory map, or console exists. QEMU virt is the only implemented platform; see PLATFORM_INTERFACE.md)
else
  $(error Unknown PLATFORM '$(PLATFORM)': expected `qemu-virt`)
endif

PAYLOAD_ELF := $(BUILD)/myos_m1.elf
PAYLOAD_BIN := $(BUILD)/myos_m1.bin
M1CTL       := $(BUILD)/m1ctl

KERNEL_ELF  := $(BUILD)/myos_kernel.elf
KERNEL_BIN  := $(BUILD)/myos_kernel.bin

CROSSFLAGS := -ffreestanding -nostdlib -nostartfiles -fno-pic -fno-pie \
              -fno-asynchronous-unwind-tables -fno-unwind-tables -Os \
              -Wall -Wextra

# Kernel-specific flags (allow larger code, no size constraint)
KERNEL_CROSSFLAGS := -ffreestanding -nostdlib -nostartfiles -fno-pic -fno-pie \
                     -fno-asynchronous-unwind-tables -fno-unwind-tables -O2 \
                     -Wall -Wextra -mgeneral-regs-only $(PLATFORM_DEFS)

.PHONY: all payload m1ctl kernel probe raw-entry raw-entry-check \
        qemu offline test clean \
        usb-watch usb-watch-test usb-watch-check pongo-addr-check \
        pongo-artifact-check regs-output-check \
        pongo-client-test probe-report-test report-readback-test \
        probe-selfreport-test capture-record-check

all: payload m1ctl

# ========================================================================
# M1 Payload (iPhone 6 DFU command) - must fit M1_MAX_PAYLOAD (0x800-0x50)
# ========================================================================

payload: $(PAYLOAD_BIN)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/m1_entry.o: platform/apple/a8/m1_entry.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(BUILD)/boot_diag_m1.o: platform/apple/a8/boot_diag_m1.c kernel/boot_diag.h | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -I. -c $< -o $@

# link with ld directly: the gcc driver would add PIE/dynamic-linker
# sections (.interp/.dynamic/.got) that must never reach the payload blob
$(PAYLOAD_ELF): $(BUILD)/m1_entry.o $(BUILD)/boot_diag_m1.o platform/apple/a8/m1.ld
	$(CROSS_LD) -T platform/apple/a8/m1.ld --build-id=none -o $@ $(BUILD)/m1_entry.o $(BUILD)/boot_diag_m1.o

$(PAYLOAD_BIN): $(PAYLOAD_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

m1ctl: $(M1CTL)

$(M1CTL): tools/host/m1ctl.c tools/host/m1_proto.h platform/apple/a8/m1_a8.h | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -o $@ tools/host/m1ctl.c -lusb-1.0

# ========================================================================
# M-P1.3 Tier-1 USB Watch (host observation tool for PongoOS device)
# ========================================================================

USB_WATCH := $(BUILD)/usb_watch

usb-watch: $(USB_WATCH)

$(USB_WATCH): tools/host/usb_watch.c | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -pthread -o $@ tools/host/usb_watch.c -lusb-1.0

# Offline C-level harness: links the REAL usb_watch.c (as usb_watch_main)
# against a mock libusb backend. No USB hardware or libusb needed.
USB_WATCH_MOCK_DIR := tests/mock
USB_WATCH_TEST     := $(BUILD)/test_usb_watch

usb-watch-test: $(USB_WATCH_TEST)
	$(USB_WATCH_TEST)

# Build + offline Python checks (real binary arg/signal handling + C harness).
usb-watch-check: $(USB_WATCH) $(USB_WATCH_TEST)
	python3 tests/test_usb_watch.py

$(BUILD)/usb_watch_impl.o: tools/host/usb_watch.c | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -pthread -I$(USB_WATCH_MOCK_DIR) \
		-Dmain=usb_watch_main -c $< -o $@

$(BUILD)/mock_usb.o: tests/mock_usb.c tests/mock_usb.h \
                     tests/mock/libusb-1.0/libusb.h | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -pthread -I$(USB_WATCH_MOCK_DIR) -c $< -o $@

$(BUILD)/test_usb_watch_c.o: tests/test_usb_watch_c.c tests/mock_usb.h \
                             tests/mock/libusb-1.0/libusb.h | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -pthread -I$(USB_WATCH_MOCK_DIR) -c $< -o $@

$(USB_WATCH_TEST): $(BUILD)/usb_watch_impl.o $(BUILD)/mock_usb.o \
                   $(BUILD)/test_usb_watch_c.o
	$(CC_HOST) -pthread -o $@ $^

# ========================================================================
# M-P1 PongoOS Probe (iPhone 6 raw image for `bootr`) - must stay <= 1 MiB
# Entry at file offset 0; position-independent; report block at +0x200.
# Requires an AArch64 linker (e.g. Arch dev machine); NOT built on hosts
# without aarch64-linux-gnu-ld. QEMU kernel path above is untouched.
# ========================================================================

PROBE_ELF := $(BUILD)/pongo_probe.elf
PROBE_BIN := $(BUILD)/pongo_probe.bin

probe: $(PROBE_BIN)

$(BUILD)/pongo_probe.o: platform/apple/a8/pongo_probe.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(PROBE_ELF): $(BUILD)/pongo_probe.o platform/apple/a8/pongo_probe.ld
	$(CROSS_LD) -T platform/apple/a8/pongo_probe.ld --build-id=none -o $@ $(BUILD)/pongo_probe.o

$(PROBE_BIN): $(PROBE_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

# ========================================================================
# M7.11 A8 RAW entry trampoline (PongoOS `bootr` handoff, stack-owning).
# Position-independent; linked at 0; entry at file offset 0; the image owns
# its stack inside its own uploaded bytes (.stack, 0x4000). Requires an
# AArch64 toolchain; QEMU kernel path untouched.
# ========================================================================

RAW_ELF := $(BUILD)/raw_entry.elf
RAW_BIN := $(BUILD)/raw_entry.bin

raw-entry: $(RAW_BIN)

$(BUILD)/raw_entry.o: platform/apple/a8/raw_entry.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

# -mgeneral-regs-only: FP/SIMD state at handoff is unknown (CPACR_EL1 is
# never touched on this path); the payload must use general registers only.
$(BUILD)/raw_main.o: platform/apple/a8/raw_main.c | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -mgeneral-regs-only -c $< -o $@

$(BUILD)/raw_vectors.o: platform/apple/a8/raw_vectors.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(RAW_ELF): $(BUILD)/raw_entry.o $(BUILD)/raw_main.o $(BUILD)/raw_vectors.o platform/apple/a8/raw_entry.ld
	$(CROSS_LD) -T platform/apple/a8/raw_entry.ld --build-id=none -o $@ \
		$(BUILD)/raw_entry.o $(BUILD)/raw_main.o $(BUILD)/raw_vectors.o

$(RAW_BIN): $(RAW_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

# ========================================================================
# M7.12 raw-image artifact validation (host-side, OFFLINE, strict).
#   raw-entry        : authoritative image build -> build/raw_entry.bin
#   raw-entry-check  : rebuilds the image from source into
#                      build/raw_entry-verify/ (full second derivation),
#                      byte-compares it with build/raw_entry.bin (determinism),
#                      then runs the artifact validator in STRICT mode
#                      (--require-artifact: a missing artifact, a missing
#                      AArch64 tool or any contract violation fails the
#                      target; nothing is silently skipped). A missing
#                      toolchain fails the compile/link steps with the
#                      concrete tool name. Validation covers the real linked
#                      ELF and flat binary; no device, no upload, no boot.
# ========================================================================

RAW_VERIFY_BIN := $(BUILD)/raw_entry-verify/raw_entry.bin

$(RAW_VERIFY_BIN): platform/apple/a8/raw_entry.S \
                   platform/apple/a8/raw_main.c \
                   platform/apple/a8/raw_vectors.S \
                   platform/apple/a8/raw_entry.ld
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -c platform/apple/a8/raw_entry.S -o $(dir $@)raw_entry.o
	$(CROSS_CC) $(CROSSFLAGS) -mgeneral-regs-only -c platform/apple/a8/raw_main.c -o $(dir $@)raw_main.o
	$(CROSS_CC) $(CROSSFLAGS) -c platform/apple/a8/raw_vectors.S -o $(dir $@)raw_vectors.o
	$(CROSS_LD) -T platform/apple/a8/raw_entry.ld --build-id=none \
		-o $(dir $@)raw_entry.elf $(dir $@)raw_entry.o $(dir $@)raw_main.o $(dir $@)raw_vectors.o
	$(CROSS_OBJCOPY) -O binary $(dir $@)raw_entry.elf $@

# Stage separation: validator failure stops before the marker line; a missing
# marker after the validator means the cmp of the two derivations failed.
raw-entry-check: $(RAW_BIN) $(RAW_VERIFY_BIN)
	python3 tests/test_a8_raw_entry.py --require-artifact
	@echo "raw-entry-check: artifact validation PASS; comparing independent derivations"
	cmp $(RAW_BIN) $(RAW_VERIFY_BIN)
	@echo "=== RAW-ENTRY CHECK: PASS (artifact contract + deterministic rebuild) ==="

# ========================================================================
# M7.14 exception-path RUNTIME verification (QEMU emulation, fail-closed).
#   raw-entry-runtime-test : builds a TEST-ONLY image in
#                      build/raw-entry-runtime/ from the SAME production
#                      raw_entry.S + raw_vectors.S + raw_entry.ld (the C
#                      entry raw_main.c is replaced by the test trigger
#                      tests/a8_runtime/a8_trigger.S, which executes a
#                      controlled BRK), boots it at EL1 on QEMU virt
#                      (secure=off, virtualization=off: no EL3/EL2), lets
#                      the REAL VBAR_EL1 install / vector dispatch /
#                      capture handler / park run, and validates the
#                      record read back from guest memory against the
#                      trigger contract (tests/a8_exc_runtime.py).
#                      The production image is hashed before and after, so
#                      the test-only build cannot leak into it. A timeout,
#                      a missing emulator, a stale image, a wrong slot,
#                      syndrome, PC or marker all fail the target; nothing
#                      is skipped and no QEMU boot of the M1/M2 kernel
#                      path is involved or modified.
#   M7.15 scenarios : the same target also runs two separately identified
#                      SCENARIO runs that EXPLICITLY establish the
#                      pre-exception state with a test-only boot shim
#                      (tests/a8_runtime/a8_boot_shim.S) built into
#                      build/raw-entry-runtime/shim/ (never into the
#                      production image): SP0 (shim sets spsel 0 ->
#                      expected slot 0, Current EL SP0 Synchronous) and
#                      SPx (shim sets spsel 1 -> expected slot 4, Current
#                      EL SPx Synchronous). Each scenario writes its own
#                      evidence directory; the harness rejects a
#                      wrong-scenario, stale or tampered shim before QEMU
#                      starts.
# ========================================================================

RUNTIME_DIR := $(BUILD)/raw-entry-runtime
RUNTIME_ELF := $(RUNTIME_DIR)/raw_entry_runtime.elf
RUNTIME_BIN := $(RUNTIME_DIR)/raw_entry_runtime.bin
RUNTIME_LOAD_ADDR := 0x41000000
RUNTIME_TIMEOUT := 10
SHIM_DIR := $(RUNTIME_DIR)/shim
SHIM_LOAD_ADDR := 0x43000000
SHIM_S := tests/a8_runtime/a8_boot_shim.S
SHIM_SP0_O := $(SHIM_DIR)/a8_boot_shim_sp0.o
SHIM_SP0_ELF := $(SHIM_DIR)/a8_boot_shim_sp0.elf
SHIM_SP0_BIN := $(SHIM_DIR)/a8_boot_shim_sp0.bin
SHIM_SPX_O := $(SHIM_DIR)/a8_boot_shim_spx.o
SHIM_SPX_ELF := $(SHIM_DIR)/a8_boot_shim_spx.elf
SHIM_SPX_BIN := $(SHIM_DIR)/a8_boot_shim_spx.bin

# TEST-ONLY boot shims: one object per scenario (MYOS_A8_SHIM_SPSEL
# selects the SPSel the shim must establish), linked at the documented shim
# load address with the runtime image base baked into the branch word.
# These never enter the production artifact: `make raw-entry` does not
# depend on them, and the runtime target re-hashes production
# raw_entry.bin around the whole scenario sequence.
$(SHIM_SP0_O): $(SHIM_S) | $(BUILD)
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -DMYOS_A8_SHIM_SPSEL=0 \
		-DMYOS_A8_SHIM_IMAGE_ADDR=$(RUNTIME_LOAD_ADDR) -c $< -o $@

$(SHIM_SP0_ELF): $(SHIM_SP0_O)
	$(CROSS_LD) -e myos_a8_shim_start -Ttext=$(SHIM_LOAD_ADDR) --build-id=none -o $@ $<

$(SHIM_SP0_BIN): $(SHIM_SP0_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

$(SHIM_SPX_O): $(SHIM_S) | $(BUILD)
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -DMYOS_A8_SHIM_SPSEL=1 \
		-DMYOS_A8_SHIM_IMAGE_ADDR=$(RUNTIME_LOAD_ADDR) -c $< -o $@

$(SHIM_SPX_ELF): $(SHIM_SPX_O)
	$(CROSS_LD) -e myos_a8_shim_start -Ttext=$(SHIM_LOAD_ADDR) --build-id=none -o $@ $<

$(SHIM_SPX_BIN): $(SHIM_SPX_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

$(RUNTIME_DIR)/raw_entry.o: platform/apple/a8/raw_entry.S | $(BUILD)
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(RUNTIME_DIR)/raw_vectors.o: platform/apple/a8/raw_vectors.S | $(BUILD)
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(RUNTIME_DIR)/a8_trigger.o: tests/a8_runtime/a8_trigger.S | $(BUILD)
	@mkdir -p $(dir $@)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(RUNTIME_ELF): $(RUNTIME_DIR)/raw_entry.o $(RUNTIME_DIR)/a8_trigger.o \
                $(RUNTIME_DIR)/raw_vectors.o platform/apple/a8/raw_entry.ld
	$(CROSS_LD) -T platform/apple/a8/raw_entry.ld --build-id=none -o $@ \
		$(RUNTIME_DIR)/raw_entry.o $(RUNTIME_DIR)/a8_trigger.o $(RUNTIME_DIR)/raw_vectors.o

$(RUNTIME_BIN): $(RUNTIME_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

raw-entry-runtime-test: $(RAW_BIN) $(RUNTIME_BIN) \
                         $(SHIM_SP0_BIN) $(SHIM_SPX_BIN)
	@before=$$(sha256sum $(RAW_BIN) | cut -d' ' -f1); rc=0; \
	python3 tests/a8_exc_runtime.py \
		--image $(RUNTIME_BIN) --elf $(RUNTIME_ELF) \
		--qemu $(QEMU) --nm $(CROSS)nm --objdump $(CROSS)objdump \
		--load-addr $(RUNTIME_LOAD_ADDR) --timeout $(RUNTIME_TIMEOUT) \
		--evidence-dir $(RUNTIME_DIR)/evidence \
		--source platform/apple/a8/raw_entry.S \
		--source platform/apple/a8/raw_vectors.S \
		--source tests/a8_runtime/a8_trigger.S \
		--source platform/apple/a8/raw_entry.ld || rc=$$?; \
	if [ $$rc -eq 0 ]; then \
		python3 tests/a8_exc_runtime.py \
			--image $(RUNTIME_BIN) --elf $(RUNTIME_ELF) \
			--qemu $(QEMU) --nm $(CROSS)nm --objdump $(CROSS)objdump \
			--load-addr $(RUNTIME_LOAD_ADDR) --timeout $(RUNTIME_TIMEOUT) \
			--evidence-dir $(RUNTIME_DIR)/scenario-sp0/evidence \
			--shim $(SHIM_SP0_BIN) --shim-elf $(SHIM_SP0_ELF) \
			--shim-load-addr $(SHIM_LOAD_ADDR) --spsel 0 \
			--source platform/apple/a8/raw_entry.S \
			--source platform/apple/a8/raw_vectors.S \
			--source tests/a8_runtime/a8_trigger.S \
			--source platform/apple/a8/raw_entry.ld \
			--shim-source $(SHIM_S) || rc=$$?; \
	fi; \
	if [ $$rc -eq 0 ]; then \
		python3 tests/a8_exc_runtime.py \
			--image $(RUNTIME_BIN) --elf $(RUNTIME_ELF) \
			--qemu $(QEMU) --nm $(CROSS)nm --objdump $(CROSS)objdump \
			--load-addr $(RUNTIME_LOAD_ADDR) --timeout $(RUNTIME_TIMEOUT) \
			--evidence-dir $(RUNTIME_DIR)/scenario-spx/evidence \
			--shim $(SHIM_SPX_BIN) --shim-elf $(SHIM_SPX_ELF) \
			--shim-load-addr $(SHIM_LOAD_ADDR) --spsel 1 \
			--source platform/apple/a8/raw_entry.S \
			--source platform/apple/a8/raw_vectors.S \
			--source tests/a8_runtime/a8_trigger.S \
			--source platform/apple/a8/raw_entry.ld \
			--shim-source $(SHIM_S) || rc=$$?; \
	fi; \
	after=$$(sha256sum $(RAW_BIN) | cut -d' ' -f1); \
	if [ "$$before" != "$$after" ]; then \
		echo "raw-entry-runtime-test: FAIL: production raw_entry.bin hash changed during the test-only build"; \
		rc=1; \
	fi; \
	exit $$rc

.PHONY: raw-entry-runtime-test

# ========================================================================
# M-P2.4 PongoOS symbol / address resolution check (host-side, offline).
# Resolves link-time symbols from the non-stripped Pongo Mach-O and
# verifies the address model still matches the pinned source. Does NOT
# and cannot resolve the runtime upload-buffer address (heap-allocated).
# ========================================================================

PONGO_ADDR := tools/host/pongo_addr_resolve.py

pongo-addr-check:
	@echo "=== PongoOS source-assumption verification ==="
	python3 $(PONGO_ADDR) --verify-source --repo .
	@echo "=== PongoOS symbol/address resolution tests ==="
	python3 tests/test_pongo_addr_resolve.py
	@echo "=== PONGO-ADDR CHECK: PASS ==="

# ========================================================================
# M-P2.30 Explicit preserved-artifact selection (host-side, OFFLINE).
# Selects a preserved PongoOS Mach-O explicitly, without touching the
# default pongoos-src/build/Pongo (no bytes/timestamps change). Never
# executes code and never contacts a device.
#   make pongo-artifact-check
#   make pongo-artifact-check PONGO_ARTIFACT_DIR=artifacts/m-p2.26-cb83dd83
# ========================================================================

pongo-artifact-check:
	@echo "=== explicit artifact-selection tests ==="
	python3 tests/test_pongo_artifact_select.py
	@if [ -n "$(PONGO_ARTIFACT_DIR)" ]; then \
		echo "=== validate preserved artifact: $(PONGO_ARTIFACT_DIR) ==="; \
		python3 $(PONGO_ADDR) --artifact-dir "$(PONGO_ARTIFACT_DIR)" --json; \
	else \
		echo "(tip: pass PONGO_ARTIFACT_DIR=artifacts/<milestone-commit> to validate a preserved artifact)"; \
	fi
	@echo "=== PONGO-ARTIFACT CHECK: PASS ==="

# ========================================================================
# M-P2.32 PongoOS `regs` shell-command output contract (host-side, OFFLINE).
# Parses/validates the text `regs` prints over the Pongo shell, against a
# parser whose field set/order is re-derived from regs.c. Synthetic fixtures
# only; never asserts an Apple A8 runtime value; no hardware, no boot.
#   make regs-output-check
# ========================================================================

regs-output-check:
	@echo "=== regs output contract (source check) ==="
	python3 tools/host/regs_output.py --source-check
	@echo "=== regs output contract fixtures ==="
	python3 tests/test_regs_output.py
	@echo "=== REGS-OUTPUT CHECK: PASS ==="

# ========================================================================
# M-P2.7 Post-handoff probe-report readback proof (host-side, OFFLINE).
# probe-report-test : report parser vs .equ layout + static header in the
#                     built probe image (dynamic slots must be zero).
# pongo-client-test : PongoOS host client protocol against a mock device
#                     (closed request set: unimplemented IN requests are
#                     EP0-stalled), plus the handoff-channel SOURCE
#                     CONTRACT and readback capability inventory proving
#                     no service survives `bootr` to read report bytes
#                     back, and the smallest-mechanism primitive check.
# No hardware, no DFU, no PongoOS entry involved.
# ========================================================================

probe-report-test:
	@echo "=== Probe report parser / layout contract ==="
	python3 tests/test_probe_report.py
	@echo "=== PROBE-REPORT TEST: PASS ==="

pongo-client-test:
	@echo "=== PongoOS host client protocol + handoff contract ==="
	python3 tests/test_pongo_client.py
	@echo "=== PONGO-CLIENT TEST: PASS ==="

# ========================================================================
# M-P2.8 Offline report-readback prototype (host-side, OFFLINE, SIMULATED).
# report-readback-test : selfcheck + deterministic tests for the
#   source-justified candidate (md8 -> stdout ring -> ep0 IN): fixed-size
#   report transfer with magic/version/size/reserved/integrity validation
#   and distinct handling of partial, incomplete, timeout, disconnect,
#   malformed and unavailable-memory outcomes.  Mock transport only --
#   no hardware, no DFU, no PongoOS entry, no probe changes.
# ========================================================================

report-readback-test:
	@echo "=== M-P2.8 offline report-readback prototype (SIMULATED) ==="
	python3 tools/host/report_readback.py --selfcheck
	python3 tests/test_report_readback.py
	@echo "=== M-P2.10 post-handoff readback design (source-contract) ==="
	python3 tests/test_readback_design.py
	@echo "=== REPORT-READBACK TEST: PASS ==="

# ========================================================================
# M-P2.11 Pre-Handoff Self-Report (design B; host-side, OFFLINE).
# Generates and validates the MP1PRB01 report BEFORE `bootr`, so the
# readback path never depends on USB servicing after the raw jump.
#   probe-selfreport-test : offline self-check + exact-layout / register-
#     field provenance / invalid-data / host-parsing tests, plus the
#     source contract design B rests on (bootr teardown ordering, jump
#     register contract, MIDR/CNTFRQ absence, shell command inventory,
#     caller-supplied-address rule).  Reads pongoos-src, does NOT modify
#     it; does not rebuild or modify the existing probe binary; no
#     hardware, no DFU, no payload upload, no boot.
# ========================================================================

probe-selfreport-test:
	@echo "=== M-P2.11 pre-handoff self-report (OFFLINE) ==="
	python3 tools/host/probe_selfreport.py --selfcheck
	python3 tests/test_probe_selfreport.py
	@echo "=== PROBE-SELFREPORT TEST: PASS ==="

# ========================================================================
# M6.2 A8 entry-capture record format (host-side, OFFLINE).
# Reference encoder/parser + malformed-input matrix for
# M6_1_A8_ENTRY_CAPTURE_SPEC.md §3. stdlib only; no USB, no device, no
# on-target producer, no hardware value asserted. See the M1 wire-format
# helper tests (tests/test_m1_proto.py) for the exec-block side.
# ========================================================================

capture-record-check:
	@echo "=== capture-record self-check (SYNTHETIC) ==="
	python3 tools/host/capture_record.py --selftest
	@echo "=== capture-record format tests (SYNTHETIC fixtures) ==="
	python3 tests/test_capture_record.py
	@echo "=== CAPTURE-RECORD CHECK: PASS ==="

# ========================================================================
# M-P3.0 boot diagnostic log parser checks (host-side, no device required)
# Parses captured UART/serial logs and reports last entered/OK/failure
# stages, exception context, and MYOS_KERNEL_PASS presence. Source label
# is operator-supplied; QEMU output is never physical proof.
# ========================================================================

diag-parse-test:
	@echo "=== M-P3.0 boot diagnostic parser tests ==="
	python3 tests/test_boot_diag_parse.py
	@echo "=== DIAG PARSE TESTS: PASS ==="

# ========================================================================
# M-P3.0 read-only Apple USB detector checks (host-side, no device change)
# Runs lsusb read-only and classifies 05ac:12a8/1227/4141; never switches
# modes, sends payloads, or writes. Detection is not boot evidence.
# ========================================================================

usb-detect-test:
	@echo "=== M-P3.0 USB detector tests ==="
	python3 tests/test_usb_detect.py
	@echo "=== USB DETECT TESTS: PASS ==="

.PHONY: diag-parse-test usb-detect-test

# ========================================================================
# QEMU Kernel (runtime test) - separate from M1 payload
# ========================================================================

kernel: $(KERNEL_ELF)

$(BUILD)/start.o: platform/qemu-virt/start.S platform/qemu-virt/qemu_virt_defs.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -I. -c $< -o $@

$(BUILD)/boot_diag.o: kernel/boot_diag.c kernel/boot_diag.h kernel/console.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -c $< -o $@

$(BUILD)/console.o: kernel/console.c kernel/console.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -c $< -o $@

$(BUILD)/vectors.o: arch/aarch64/vectors.S arch/aarch64/vectors_defs.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -c $< -o $@

$(BUILD)/uart_pl011.o: platform/qemu-virt/uart_pl011.S platform/qemu-virt/qemu_virt_defs.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -I. -c $< -o $@

$(BUILD)/main.o: kernel/main.c kernel/boot_diag.h kernel/console.h platform/platform.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -I. -c $< -o $@

# Platform boundary (M6): selection glue + the ONLY concrete descriptor
# (qemu-virt). platform/apple/a8/iphone6.c is intentionally NOT linked.
$(BUILD)/platform.o: platform/platform.c platform/platform.h \
                     platform/qemu-virt/qemu_virt.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -I. -c $< -o $@

$(BUILD)/qemu_virt.o: platform/qemu-virt/qemu_virt.c platform/qemu-virt/qemu_virt.h \
                      platform/platform.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -I. -c $< -o $@

$(KERNEL_ELF): $(BUILD)/start.o $(BUILD)/boot_diag.o $(BUILD)/vectors.o \
               $(BUILD)/uart_pl011.o $(BUILD)/main.o $(BUILD)/platform.o \
               $(BUILD)/qemu_virt.o $(BUILD)/console.o platform/qemu-virt/kernel.ld
	$(CROSS_LD) -T platform/qemu-virt/kernel.ld --build-id=none -z noexecstack -o $@ \
		$(BUILD)/start.o $(BUILD)/boot_diag.o $(BUILD)/vectors.o \
		$(BUILD)/uart_pl011.o $(BUILD)/main.o $(BUILD)/platform.o \
		$(BUILD)/qemu_virt.o $(BUILD)/console.o

$(KERNEL_BIN): $(KERNEL_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

# ========================================================================
# QEMU Runtime Test
# ========================================================================

QEMU := qemu-system-aarch64
QEMU_FLAGS := -machine virt -cpu cortex-a57 -nographic -kernel $(KERNEL_ELF)
QEMU_TIMEOUT := 10

qemu: kernel
	@echo "=== Booting MyOS kernel on QEMU virt ==="
	@rm -f /tmp/qemu_output.log; \
	timeout $(QEMU_TIMEOUT) $(QEMU) $(QEMU_FLAGS) > /tmp/qemu_output.log 2>&1; \
	EXIT_CODE=$$?; \
	cat /tmp/qemu_output.log; \
	if [ $$EXIT_CODE -eq 124 ]; then \
		echo "QEMU timed out after $(QEMU_TIMEOUT)s (kernel may be running)"; \
	elif [ $$EXIT_CODE -eq 0 ]; then \
		echo "QEMU exited normally"; \
	else \
		echo "QEMU exited with code $$EXIT_CODE"; \
	fi; \
	if grep -q "MYOS_KERNEL_PASS" /tmp/qemu_output.log; then \
		if grep -q "DBG: console NULL" /tmp/qemu_output.log; then \
			echo "=== QEMU TEST: FAIL (false console-ready failure reported) ==="; \
			exit 1; \
		fi; \
		echo "=== QEMU TEST: PASS ==="; \
		exit 0; \
	else \
		echo "=== QEMU TEST: FAIL (MYOS_KERNEL_PASS not found) ==="; \
		exit 1; \
	fi

# ========================================================================
# External bootstrap (documented dependency, unmodified upstream source)
# ========================================================================

gaster:
	$(MAKE) -C tools/external/gaster libusb CC=gcc

# ========================================================================
# Test targets
# ========================================================================

offline: all
	bash tests/offline_m1.sh

test: offline
	bash tests/test_boot_diag.sh
	bash tests/test_boot_diag_extended.sh

# Full test suite including QEMU runtime test
test-all: test qemu

# ========================================================================
# M-P2.44 aggregate host-side regression runner.
# Runs EVERY tests/test_*.py so a newly added test can never be silently
# omitted from the regression set.  The M-P2.36 report_readback regression
# slipped through precisely because the earlier runner listed tests by
# hand and left test_report_readback.py out.  OFFLINE only: no hardware,
# no DFU, no cross-build, no PongoOS entry, no probe change.
# ========================================================================

host-test-all:
	@echo "=== aggregate host-side regression (OFFLINE) ==="
	@fail=0; \
	for t in tests/test_*.py; do \
	  log=$$(mktemp); \
	  if python3 "$$t" >"$$log" 2>&1; then \
	    echo "PASS $$t"; \
	  else \
	    echo "FAIL $$t"; cat "$$log"; fail=1; \
	  fi; \
	  rm -f "$$log"; \
	done; \
	if [ $$fail -eq 0 ]; then echo "=== HOST-TEST-ALL: PASS ==="; \
	else echo "=== HOST-TEST-ALL: FAIL ==="; exit 1; fi

.PHONY: host-test-all

# ========================================================================
# Artifact-integrity checkers (host-side, READ-ONLY, OFFLINE).
#   check_build_integrity.py : verifies every linked build artifact against
#                              the linker scripts and Makefile variables
#                              (ELF format, entry points, section addresses,
#                              freestanding-ness, size bounds, probe magic).
#   check_pongo_probe.py     : PongoOS probe source/layout invariants
#                              (report offsets, magic, no stack/branch use).
# Both are READ-ONLY: they verify existing artifacts and never rebuild or
# modify them; a missing artifact FAILS with the concrete file name.
# They are intentionally NOT part of host-test-all, which must run on a
# bare tree without any compiled artifacts.
# ========================================================================

integrity-check:
	python3 tests/check_build_integrity.py
	python3 tests/check_pongo_probe.py
	@echo "=== INTEGRITY-CHECK: PASS ==="

.PHONY: integrity-check

# ========================================================================
# M2 Kernel Image Format Check (host-side, no device required)
#   1. Generate a valid canonical M2 image
#   2. Validate it with the host validator
#   3. Run the negative test suite (malformed images must be rejected)
#   4. Run the memory-map simulator (hypothetical, runtime values)
# ========================================================================

M2_TEST_IMAGE := $(BUILD)/myos_m2_test.myos

m2-check: $(M2_TEST_IMAGE)
	@echo "=== M2 image format validation ==="
	python3 tests/validate_m2.py --verbose $(M2_TEST_IMAGE)
	@echo "=== M2 negative tests ==="
	python3 tests/test_m2_negative.py
	@echo "=== M2 memory-map simulation ==="
	python3 tests/sim_mmap.py
	@echo "=== M2 CHECK: PASS ==="

$(M2_TEST_IMAGE): tests/make_m2_image.py | $(BUILD)
	python3 tests/make_m2_image.py $(M2_TEST_IMAGE)

.PHONY: m2-check

clean:
	rm -rf $(BUILD)