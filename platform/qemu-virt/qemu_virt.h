/*
 * platform/qemu-virt/qemu_virt.h — QEMU `virt` platform descriptor.
 *
 * target: qemu-virt   (generic AArch64 virtual hardware; TESTS ONLY)
 *
 * The constants below describe the QEMU `virt` machine. They are QEMU-ONLY:
 * per ARCHITECTURE.md §8 they must never be presumed to apply to the Apple A8.
 * The values are defined exactly once, in qemu_virt_defs.h, and shared with the
 * early boot stub and the PL011 driver; the kernel linker script
 * (platform/qemu-virt/kernel.ld) is cross-checked against them by
 * tests/test_platform_boundary.py so they cannot drift apart silently.
 */
#ifndef MYOS_PLATFORM_QEMU_VIRT_H
#define MYOS_PLATFORM_QEMU_VIRT_H

#include "platform/platform.h"

/* Single authoritative QEMU virt constants (shared with the .S boot path). */
#include "platform/qemu-virt/qemu_virt_defs.h"

extern const myos_platform_descriptor_t myos_platform_qemu_virt;

#endif /* MYOS_PLATFORM_QEMU_VIRT_H */
