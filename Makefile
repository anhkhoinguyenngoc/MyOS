# MyOS — M1 build (target: iphone6 real hardware for payload;
#                    host x86_64 for m1ctl; QEMU NOT used in M1)
#       Kernel build (target: qemu-virt for runtime testing)

CROSS   := aarch64-linux-gnu-
CC_HOST := gcc
BUILD   := build

CROSS_CC  := $(CROSS)gcc
CROSS_LD  := $(CROSS)ld
CROSS_OBJCOPY := $(CROSS)objcopy

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
                     -Wall -Wextra -mgeneral-regs-only

.PHONY: all payload m1ctl kernel probe qemu offline test clean \
        usb-watch usb-watch-test usb-watch-check pongo-addr-check \
        pongo-artifact-check \
        pongo-client-test probe-report-test report-readback-test \
        probe-selfreport-test

all: payload m1ctl

# ========================================================================
# M1 Payload (iPhone 6 DFU command) - must stay <= 2016 bytes
# ========================================================================

payload: $(PAYLOAD_BIN)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/m1_entry.o: boot/aarch64/m1_entry.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(BUILD)/boot_diag_m1.o: arch/aarch64/boot_diag_m1.c arch/aarch64/boot_diag.h | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

# link with ld directly: the gcc driver would add PIE/dynamic-linker
# sections (.interp/.dynamic/.got) that must never reach the payload blob
$(PAYLOAD_ELF): $(BUILD)/m1_entry.o $(BUILD)/boot_diag_m1.o boot/aarch64/m1.ld
	$(CROSS_LD) -T boot/aarch64/m1.ld --build-id=none -o $@ $(BUILD)/m1_entry.o $(BUILD)/boot_diag_m1.o

$(PAYLOAD_BIN): $(PAYLOAD_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

m1ctl: $(M1CTL)

$(M1CTL): tools/host/m1ctl.c platform/apple/a8/m1_a8.h | $(BUILD)
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

$(BUILD)/pongo_probe.o: boot/aarch64/pongo_probe.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(PROBE_ELF): $(BUILD)/pongo_probe.o boot/aarch64/pongo_probe.ld
	$(CROSS_LD) -T boot/aarch64/pongo_probe.ld --build-id=none -o $@ $(BUILD)/pongo_probe.o

$(PROBE_BIN): $(PROBE_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

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
# QEMU Kernel (runtime test) - separate from M1 payload
# ========================================================================

kernel: $(KERNEL_ELF)

$(BUILD)/start.o: boot/aarch64/start.S | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -c $< -o $@

$(BUILD)/boot_diag.o: arch/aarch64/boot_diag.c arch/aarch64/boot_diag.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -c $< -o $@

$(BUILD)/vectors.o: arch/aarch64/vectors.S arch/aarch64/vectors_defs.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -c $< -o $@

$(BUILD)/uart_pl011.o: arch/aarch64/uart_pl011.S | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -c $< -o $@

$(BUILD)/main.o: kernel/main.c arch/aarch64/boot_diag.h | $(BUILD)
	$(CROSS_CC) $(KERNEL_CROSSFLAGS) -Wa,--noexecstack -I. -c $< -o $@

$(KERNEL_ELF): $(BUILD)/start.o $(BUILD)/boot_diag.o $(BUILD)/vectors.o \
               $(BUILD)/uart_pl011.o $(BUILD)/main.o boot/aarch64/kernel.ld
	$(CROSS_LD) -T boot/aarch64/kernel.ld --build-id=none -z noexecstack -o $@ \
		$(BUILD)/start.o $(BUILD)/boot_diag.o $(BUILD)/vectors.o \
		$(BUILD)/uart_pl011.o $(BUILD)/main.o

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
	@timeout $(QEMU_TIMEOUT) $(QEMU) $(QEMU_FLAGS) 2>&1 | tee /tmp/qemu_output.log; \
	EXIT_CODE=$$?; \
	if [ $$EXIT_CODE -eq 124 ]; then \
		echo "QEMU timed out after $(QEMU_TIMEOUT)s (kernel may be running)"; \
	elif [ $$EXIT_CODE -eq 0 ]; then \
		echo "QEMU exited normally"; \
	else \
		echo "QEMU exited with code $$EXIT_CODE"; \
	fi; \
	if grep -q "MYOS_KERNEL_PASS" /tmp/qemu_output.log; then \
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