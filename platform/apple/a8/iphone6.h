/*
 * platform/apple/a8/iphone6.h — iPhone 6 / Apple A8 (T7000, n61ap) descriptor.
 *
 * target: iphone6     (Apple A8 real hardware)
 *
 * STATUS: UNSUPPORTED. This is a contract, not a port. No boot path, console
 * driver, memory map, or register address is implemented or asserted here.
 * See PLATFORM_INTERFACE.md for the evidence behind every field.
 *
 * Confirmed facts (sources in PLATFORM_INTERFACE.md §1):
 *   - SoC is T7000 (Apple A8), device n61ap. This host read the device's own
 *     DFU descriptor string `CPID:7000 ... SRTG:[iBoot-1992.0.0.1.19]`
 *     (BOOT_FEASIBILITY.md §0) — confirming the SoC and the vulnerable
 *     SecureROM, on hardware, in this project.
 *   - checkm8 (CVE-2019-8900) covers A5–A11, so the root of trust of this
 *     exact device is publicly known to be exploitable.
 *   - The intended MyOS carrier handoff (PongoOS `bootr` RAW path) is
 *     source-audited to enter at EL1 with MMU off and to pass
 *     x0 = boot_args, x1..x3 = 0 (M2_A10_RUNTIME_HANDOFF_AUDIT.md §3–4).
 *
 * Unknown / not established (therefore MYOS_ADDR_UNKNOWN, never guessed):
 *   - physical DRAM base/size and the handoff memory map;
 *   - any real iPhone 6 console (no public UART pinout; USB via the carrier
 *     is the only documented output path, and it is carrier-owned);
 *   - the Apple AIC interrupt controller and timer register maps for T7000;
 *   - display initialization prerequisites (a display path exists in iBoot,
 *     but its registers are undocumented).
 */
#ifndef MYOS_PLATFORM_APPLE_A8_IPHONE6_H
#define MYOS_PLATFORM_APPLE_A8_IPHONE6_H

#include "platform/platform.h"

/*
 * The descriptor itself. All hardware fields are MYOS_ADDR_UNKNOWN and the
 * capability set is empty: this file asserts no register address and no DRAM
 * range. It exists so the target is represented honestly (UNSUPPORTED) rather
 * than faked by compiling QEMU code under an "iphone6" name.
 */
extern const myos_platform_descriptor_t myos_platform_apple_a8;

#endif /* MYOS_PLATFORM_APPLE_A8_IPHONE6_H */
