# MyOS — M1 build (target: iphone6 real hardware for payload;
#                    host x86_64 for m1ctl; QEMU NOT used in M1)

CROSS   := aarch64-linux-gnu-
CC_HOST := gcc
BUILD   := build

CROSS_CC  := $(CROSS)gcc
CROSS_LD  := $(CROSS)ld
CROSS_OBJCOPY := $(CROSS)objcopy

PAYLOAD_ELF := $(BUILD)/myos_m1.elf
PAYLOAD_BIN := $(BUILD)/myos_m1.bin
M1CTL       := $(BUILD)/m1ctl

CROSSFLAGS := -ffreestanding -nostdlib -nostartfiles -fno-pic -fno-pie \
              -fno-asynchronous-unwind-tables -fno-unwind-tables -Os \
              -Wall -Wextra

.PHONY: all payload m1ctl gaster offline test clean

all: payload m1ctl

payload: $(PAYLOAD_BIN)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/m1_entry.o: boot/aarch64/m1_entry.S | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

$(BUILD)/boot_diag.o: arch/aarch64/boot_diag_m1.c arch/aarch64/boot_diag.h | $(BUILD)
	$(CROSS_CC) $(CROSSFLAGS) -c $< -o $@

# link with ld directly: the gcc driver would add PIE/dynamic-linker
# sections (.interp/.dynamic/.got) that must never reach the payload blob
$(PAYLOAD_ELF): $(BUILD)/m1_entry.o $(BUILD)/boot_diag.o boot/aarch64/m1.ld
	$(CROSS_LD) -T boot/aarch64/m1.ld --build-id=none -o $@ $(BUILD)/m1_entry.o $(BUILD)/boot_diag.o

$(PAYLOAD_BIN): $(PAYLOAD_ELF)
	$(CROSS_OBJCOPY) -O binary $< $@

m1ctl: $(M1CTL)

$(M1CTL): tools/host/m1ctl.c platform/apple/a8/m1_a8.h | $(BUILD)
	$(CC_HOST) -O2 -Wall -Wextra -o $@ tools/host/m1ctl.c -lusb-1.0

# external bootstrap (documented dependency, unmodified upstream source)
gaster:
	$(MAKE) -C tools/external/gaster libusb CC=gcc

offline: all
	bash tests/offline_m1.sh

test: offline

clean:
	rm -rf $(BUILD)
