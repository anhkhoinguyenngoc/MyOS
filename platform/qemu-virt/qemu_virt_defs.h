/*
 * platform/qemu-virt/qemu_virt_defs.h — QEMU virt machine constants.
 *
 * target: qemu-virt   (QEMU-ONLY; never an Apple A8 value)
 *
 * Single authoritative definition of the QEMU `virt` constants shared by the
 * C platform descriptor and the AArch64 boot/peripheral assembly. The
 * descriptor header, the early boot stub, and the PL011 driver all include
 * this file, so each value exists in exactly one place and cannot drift apart.
 *
 * Preprocessor defines only, so a `.S` translation unit may include it.
 * QEMU-only: per ARCHITECTURE.md §8 these must never be presumed to describe
 * the Apple A8. The load address cannot be shared with the linker script
 * (linker scripts are not preprocessed); tests/test_platform_boundary.py
 * cross-checks kernel.ld against MYOS_QEMU_VIRT_LOAD_ADDR.
 */
#ifndef MYOS_PLATFORM_QEMU_VIRT_DEFS_H
#define MYOS_PLATFORM_QEMU_VIRT_DEFS_H

/* QEMU virt PL011 UART base. QEMU-ONLY. */
#define MYOS_QEMU_VIRT_PL011_BASE  0x09000000

/* QEMU virt `-kernel` load address used by platform/qemu-virt/kernel.ld.
 * QEMU-ONLY; NOT an iPhone 6 address. */
#define MYOS_QEMU_VIRT_LOAD_ADDR   0x40080000

#endif /* MYOS_PLATFORM_QEMU_VIRT_DEFS_H */
