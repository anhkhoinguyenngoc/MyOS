/*
 * platform/platform.c — build-time platform selection and accessor.
 *
 * target: target-independent selection glue; no MMIO.
 *
 * Exactly one platform may be selected, via a preprocessor define supplied by
 * the build (see the PLATFORM gate in the Makefile):
 *
 *   MYOS_PLATFORM_QEMU_VIRT  — QEMU `virt`, the only implemented platform.
 *   MYOS_PLATFORM_APPLE_A8   — declared but NOT implemented: fails the build.
 *
 * The A8 branch deliberately uses #error so that a future physical target
 * cannot be produced by renaming QEMU code. Removing this error is an explicit
 * milestone action that must be justified by PLATFORM_INTERFACE.md §3 being
 * resolved (a real handoff, memory map, and console), reviewed, and tested.
 */
#include "platform/platform.h"

#if defined(MYOS_PLATFORM_QEMU_VIRT) && defined(MYOS_PLATFORM_APPLE_A8)
#error "Select exactly one MyOS platform (both MYOS_PLATFORM_QEMU_VIRT and MYOS_PLATFORM_APPLE_A8 are defined)."

#elif defined(MYOS_PLATFORM_QEMU_VIRT)
#include "platform/qemu-virt/qemu_virt.h"

const myos_platform_descriptor_t *myos_platform_current(void) {
    return &myos_platform_qemu_virt;
}

#elif defined(MYOS_PLATFORM_APPLE_A8)
#error "PLATFORM=apple-a8 is not implemented: no validated boot handoff, memory map, or console exists. MyOS refuses to compile QEMU virt code under an iPhone 6 name. See PLATFORM_INTERFACE.md (requirements) and platform/apple/a8/iphone6.c."

#else
#error "No MyOS platform selected. Define MYOS_PLATFORM_QEMU_VIRT (the only implemented platform)."
#endif
