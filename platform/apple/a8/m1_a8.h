/*
 * platform/apple/a8/m1_a8.h — shared M1 constants for host tools.
 *
 * target: iphone6 (Apple A8 real hardware)
 *
 * PROVENANCE (M1_PLAN.md §4): every value below is read from the vendored
 * bootstrap source tools/external/gaster/gaster.c @ commit 7fffffff38a1,
 * case `SRTG:[iBoot-1992.0.0.1.19]` (CPID 0x7000) — the exact SecureROM our
 * device reports. tests/offline_m1.sh cross-checks these against gaster.c.
 * NO address in this header is invented or assumed.
 */
#ifndef MYOS_PLATFORM_APPLE_A8_M1_A8_H
#define MYOS_PLATFORM_APPLE_A8_M1_A8_H

#include <stdint.h>

/* DFU image buffer == "insecure_memory_base" on CPID 0x7000 (gaster.c)     */
#define M1_IMG_BUF_BASE     0x180380000ULL
/* our payload is appended after the 80-byte exec command header           */
#define M1_ENTRY_OFF        0x50ULL
#define M1_ENTRY_ADDR       (M1_IMG_BUF_BASE + M1_ENTRY_OFF)

/* Command magics (gaster.c L49-51 == ipwndfu usbexec.py ASCII magics)     */
#define M1_EXEC_MAGIC       0x6578656365786563ULL  /* "cexecexe"            */
#define M1_DONE_MAGIC       0x646F6E65646F6E65ULL  /* "enodENOd"            */
#define M1_MEMC_MAGIC       0x6D656D636D656D63ULL  /* "mcmcmcm"             */

/* Host challenge: value the payload must return as ~challenge (M1_PLAN §6) */
#define M1_CHALLENGE        0x1122334455667788ULL

/* Response evidence slots (written by myos_m1_entry)                       */
#define M1_SLOT_CURRENTEL   0x10
#define M1_SLOT_MIDR        0x18
#define M1_SLOT_MARKER      0x20
#define M1_SLOT_ECHO        0x28
#define M1_SLOT_CNTFRQ      0x30
#define M1_SLOT_CNTPCT      0x38
#define M1_RESP_LEN         0x40
#define M1_EXEC_HEADER_LEN  0x50

#define M1_MARKER_BYTES     "MYOSM1OK"

/* USB identifiers (observed on this host, BOOT_FEASIBILITY §0)             */
#define M1_APPLE_VID        0x05AC
#define M1_DFU_PID          0x1227

#define M1_DFU_MAX_XFER     0x800

#endif /* MYOS_PLATFORM_APPLE_A8_M1_A8_H */
