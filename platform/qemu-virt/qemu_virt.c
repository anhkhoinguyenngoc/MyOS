/*
 * platform/qemu-virt/qemu_virt.c — QEMU `virt` platform descriptor data.
 *
 * target: qemu-virt   (generic AArch64 virtual hardware; TESTS ONLY)
 *
 * Data only. No MMIO is touched here. The hardware values come from the single
 * authoritative definition in qemu_virt_defs.h; the host test cross-checks them
 * against the linker script and the PL011 driver.
 */
#include "platform/qemu-virt/qemu_virt.h"

const myos_platform_descriptor_t myos_platform_qemu_virt = {
    .id             = MYOS_PLATFORM_ID_QEMU_VIRT,
    .name           = "qemu-virt",
    .status         = MYOS_PLATFORM_STATUS_KNOWN_GOOD,
    .capabilities   = MYOS_CAP_UART_PL011,
    .uart_mmio_base = MYOS_QEMU_VIRT_PL011_BASE,
    .link_load_addr = MYOS_QEMU_VIRT_LOAD_ADDR,
    .fact_source    = "QEMU virt machine model; validated by `make qemu` in this repo",
    .unresolved     = NULL,
};

/*
 * Build-time invariants. If any of these fail, this platform's descriptor no
 * longer matches its own name and the build must stop.
 */
_Static_assert(MYOS_QEMU_VIRT_PL011_BASE == 0x09000000ULL,
               "QEMU virt PL011 base drifted");
_Static_assert(MYOS_QEMU_VIRT_LOAD_ADDR == 0x40080000ULL,
               "QEMU virt load address drifted");
_Static_assert(MYOS_ADDR_UNKNOWN != MYOS_QEMU_VIRT_PL011_BASE,
               "the unknown-address sentinel must not equal a real MMIO base");
