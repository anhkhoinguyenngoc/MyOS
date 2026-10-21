/*
 * platform/platform.h — MyOS platform descriptor interface.
 *
 * target: target-independent (no hardware access, no MMIO, data only).
 *
 * This header is the boundary between target-independent kernel logic and
 * platform-specific boot/console/memory assumptions. Platform code provides a
 * descriptor; the kernel selects exactly one at build time (see
 * platform/platform.c and PLATFORM_INTERFACE.md).
 *
 * Rules (ARCHITECTURE.md §8, PLATFORM_INTERFACE.md §4):
 *   - Only a descriptor whose `status` is KNOWN_GOOD may be selected to boot.
 *   - A hardware value that has not been established MUST be MYOS_ADDR_UNKNOWN.
 *     Never substitute a QEMU constant for a physical-target value.
 *   - A platform with no validated boot handoff is declared UNSUPPORTED and
 *     carries its open questions in `unresolved`; it does not invent data.
 */
#ifndef MYOS_PLATFORM_H
#define MYOS_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

/* Platform identity. Values are stable and explicit (never 0). */
typedef enum {
    MYOS_PLATFORM_ID_QEMU_VIRT = 1,
    MYOS_PLATFORM_ID_APPLE_A8  = 2,
} myos_platform_id_t;

/*
 * Platform support status as a first-class, machine-checkable property.
 *   KNOWN_GOOD   : a boot path exists and is validated by this repository
 *                  (host tests and/or the QEMU runtime test). This is a
 *                  statement about the platform implementation, not about
 *                  physical-device compatibility.
 *   UNSUPPORTED  : only a contract/descriptor exists; the boot path is not
 *                  implemented. Selecting it must fail the build.
 */
typedef enum {
    MYOS_PLATFORM_STATUS_KNOWN_GOOD   = 0,
    MYOS_PLATFORM_STATUS_UNSUPPORTED  = 1,
} myos_platform_status_t;

/*
 * Sentinel for "no address is asserted for this platform".
 *
 * MYOS_ADDR_UNKNOWN is a reserved value, NOT a claim that the address is
 * unmapped. On QEMU virt, physical 0 is part of the flash region; on the A8
 * the memory map is unknown. The sentinel means only "this platform has not
 * established a value". Because it is 0, a zero-initialized or omitted field
 * reads as UNKNOWN (a safe, fail-closed default). Callers must compare against
 * it explicitly; it is never a valid asserted `uart_mmio_base` or
 * `link_load_addr` for a bootable platform.
 */
#define MYOS_ADDR_UNKNOWN 0ULL

/* Capability bits. A bit may only be set when a real implementation backs it
 * for that platform. `capabilities` is only meaningful for a KNOWN_GOOD
 * status: an UNSUPPORTED descriptor must declare 0, so capabilities never
 * describe absent or untested hardware. The A8 descriptor sets none. */
#define MYOS_CAP_UART_PL011    (1u << 0)  /* PL011-compatible MMIO UART        */
#define MYOS_CAP_FRAMEBUFFER   (1u << 1)  /* usable framebuffer driver         */
#define MYOS_CAP_TIMER_GENERIC (1u << 2)  /* ARM generic timer (CNTPCT_EL0)    */

/*
 * Platform descriptor. Data only: a platform never performs I/O to fill this
 * in, so an UNSUPPORTED descriptor is safe to compile and inspect anywhere.
 */
typedef struct {
    myos_platform_id_t      id;
    const char             *name;           /* "qemu-virt", "apple-a8"       */

    myos_platform_status_t  status;

    uint32_t                capabilities;   /* MYOS_CAP_* bits               */

    /* PL011 (or compatible) UART MMIO base, or MYOS_ADDR_UNKNOWN. */
    uint64_t                uart_mmio_base;

    /* Link/load address the platform's guide linker script uses, or
     * MYOS_ADDR_UNKNOWN when no address is established. */
    uint64_t                link_load_addr;

    /* One-line provenance for the values above (source of truth). */
    const char             *fact_source;

    /* NULL-terminated list of requirements that must be resolved before this
     * platform can move out of UNSUPPORTED. NULL when nothing is open. */
    const char *const      *unresolved;
} myos_platform_descriptor_t;

/*
 * Returns the descriptor selected at build time. The definition lives in
 * platform/platform.c and fails the build for any unimplemented selection.
 */
const myos_platform_descriptor_t *myos_platform_current(void);

static inline const char *myos_platform_status_name(myos_platform_status_t s) {
    switch (s) {
    case MYOS_PLATFORM_STATUS_KNOWN_GOOD:  return "known-good";
    case MYOS_PLATFORM_STATUS_UNSUPPORTED: return "unsupported";
    default:                               return "invalid";
    }
}

#endif /* MYOS_PLATFORM_H */
