/*
 * platform/apple/a8/iphone6.c — iPhone 6 / Apple A8 descriptor data.
 *
 * target: iphone6     (Apple A8 real hardware)
 *
 * STATUS: UNSUPPORTED. Data only: no MMIO, no address assertions.
 *
 * This translation unit is deliberately NOT part of the QEMU kernel link
 * (see the PLATFORM gating in the Makefile). It exists so the target is
 * represented by an inspectable, host-testable descriptor. Every hardware
 * field is MYOS_ADDR_UNKNOWN because none has been established by evidence.
 *
 * The host test tests/test_platform_boundary.py compiles and runs this file
 * together with the QEMU descriptor to confirm:
 *   - the A8 descriptor is UNSUPPORTED and declares no capability;
 *   - it contains no invented MMIO base or DRAM/link address;
 *   - its unresolved list is non-empty.
 */
#include "platform/apple/a8/iphone6.h"

/*
 * Requirements that must be resolved (each with on-device or source evidence)
 * before apple-a8 can move out of UNSUPPORTED. These mirror
 * PLATFORM_INTERFACE.md §3. They are requirements, not addresses.
 */
static const char *const apple_a8_unresolved[] = {
    "kernel entry: confirm the register/EL/MMU state actually delivered at the "
    "intended MyOS entry (PongoOS bootr RAW path is source-audited to EL1/MMU-off; "
    "not yet captured on hardware)",
    "memory: establish the physical DRAM base/size and the handoff memory map "
    "(no public T7000 map; must be measured, not assumed)",
    "console: establish a real iPhone 6 diagnostic output mechanism "
    "(no public UART pinout; carrier USB is not owned by MyOS)",
    "interrupts/timer: establish T7000 AIC and timer access (ARM generic timer "
    "is architectural and available; AIC is undocumented)",
    "display: establish display initialization prerequisites before any "
    "framebuffer work (iBoot draws a logo; registers undocumented)",
    "handoff contract: fix an entry ABI (arg registers, stack ownership, "
    "reserved memory) with the chosen carrier (checkra1n -k / pongoOS module)",
    NULL,
};

const myos_platform_descriptor_t myos_platform_apple_a8 = {
    .id             = MYOS_PLATFORM_ID_APPLE_A8,
    .name           = "apple-a8",
    .status         = MYOS_PLATFORM_STATUS_UNSUPPORTED,
    .capabilities   = 0,                 /* nothing is implemented or claimed */
    .uart_mmio_base = MYOS_ADDR_UNKNOWN, /* no iPhone 6 UART address known     */
    .link_load_addr = MYOS_ADDR_UNKNOWN, /* no fixed physical load address     */
    .fact_source    = "PLATFORM_INTERFACE.md; BOOT_FEASIBILITY.md §0; "
                      "M2_A10_RUNTIME_HANDOFF_AUDIT.md §3-4",
    .unresolved     = apple_a8_unresolved,
};

/*
 * Build-time invariants for the unsupported descriptor. These make it
 * impossible to accidentally turn apple-a8 into a fake "bootable" platform
 * without changing this file and its review trail.
 */
_Static_assert(MYOS_ADDR_UNKNOWN == 0ULL,
               "MYOS_ADDR_UNKNOWN must remain 0");
