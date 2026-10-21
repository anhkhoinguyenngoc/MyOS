/*
 * MyOS M7.15 — TEST-ONLY boot shim for the raw-entry SPSel runtime
 * scenarios (QEMU virt emulation only).
 *
 * This shim is NOT part of the production raw-entry artifact. It is loaded
 * by the runtime harness at a separate, documented address (SHIM_LOAD_ADDR,
 * 0x43000000) via a second QEMU generic-loader device that also carries
 * cpu-num=0, so the shim receives the boot PC; the production image is
 * loaded without cpu-num at its usual address. `make raw-entry` and
 * `make raw-entry-check` never see this file.
 *
 * Purpose: the M7.14 harness inherited the QEMU virt reset state (EL1h,
 * SPSel=1) for vector-group selection. M7.15 instead EXPLICITLY establishes
 * the intended pre-exception state before transferring control to the
 * unmodified production trampoline:
 *
 *   1. `msr daifset, #0xf` — DAIF fully masked, matching the source-traced
 *      PongoOS bootr handoff contract (entry.c:233 disable_interrupts) and
 *      making the run independent of any reset-state assumption;
 *   2. `msr spsel, #1` then `adr`/`mov sp` — select the SP_EL1 bank and
 *      seed it with `myos_a8_shim_sp_area_top`, a 16-byte-aligned,
 *      file-backed, image-owned area inside this shim. The seed is written
 *      through the SELECTED bank (`mov sp` always writes SP_ELx for the
 *      active SPSel) rather than through `msr sp_el1`: this milestone
 *      observed a toolchain/emulator encoding discrepancy for that
 *      instruction (aarch64 binutils 2.47 assembles `msr sp_el1, xN` to
 *      0xd51c4109 = S3_4_C4_C1_0, which QEMU 11.1.1 TCG rejects with an
 *      Undefined Instruction exception, ESR EC=0, while 0xd5184029 =
 *      S3_0_C4_C0_1 reads back the seeded SP_EL1 bank — both observed in
 *      controlled probe runs). The spsel/mov technique uses only
 *      unambiguous instructions and avoids taking a side on that
 *      discrepancy;
 *   3. `msr spsel, #MYOS_A8_SHIM_SPSEL` — select SP0 (0) or SPx (1) for
 *      the pre-exception state that decides the vector group. The harness
 *      re-reads both spsel immediates out of the shim disassembly and
 *      refuses to run unless they are (1, then requested value) in that
 *      order (fail-closed: a wrong-scenario shim is rejected before QEMU
 *      starts). After this point:
 *        - SP0 scenario: the active bank is SP_EL0; the production
 *          trampoline then replaces SP_EL0 with __stack_top and never
 *          touches SP_EL1, which keeps the seed. Exception entry to EL1
 *          automatically selects SP_EL1 for the handler (Arm: "When an
 *          exception is taken, the stack pointer for the target Exception
 *          level SP_ELx is automatically selected"). The capture handler
 *          therefore records this seed — a value that is nonzero, distinct
 *          from __stack_top, and only present if the handler really ran on
 *          SP_EL1 with the record holding live architectural state;
 *        - SPx scenario: the active bank is still SP_EL1 holding the seed,
 *          so the production trampoline overwrites it with __stack_top
 *          before any stack access. The recorded sp must be __stack_top
 *          and NOT the seed — that proves the trampoline replaced the
 *          inherited bank value;
 *   4. branch to the production image entry through an absolute word
 *      (.quad MYOS_A8_SHIM_IMAGE_ADDR, supplied by the build and
 *      re-validated byte-for-byte by the harness against --load-addr;
 *      adr cannot reach 32 MiB).
 *
 * The shim uses no stack memory before the branch: every instruction is
 * straight-line with register operands only (`mov sp` writes only the
 * banked register, it does not touch memory), so the initial SP values
 * (both banks 0 at emulator reset; seeded SP_EL1 above) are never read.
 * x0 stays at its reset value (0): the production bootr boot_args
 * semantics are deliberately not exercised here, exactly as documented in
 * M7.14.
 *
 * Runtime status: TEST-ONLY. Nothing here pertains to physical n61ap
 * behavior; physical boot remains UNVERIFIED and A8 stays UNSUPPORTED.
 */
    .text
    .align 2
    .global myos_a8_shim_start
    .type myos_a8_shim_start, %function
myos_a8_shim_start:
    msr     daifset, #0xf
    msr     spsel, #1
    adr     x9, myos_a8_shim_sp_area_top
    mov     sp, x9
    msr     spsel, #MYOS_A8_SHIM_SPSEL
    adr     x9, myos_a8_shim_image_entry
    ldr     x9, [x9]
    br      x9
    .size myos_a8_shim_start, .-myos_a8_shim_start

    .align 3
    .global myos_a8_shim_image_entry
    .type myos_a8_shim_image_entry, %object
myos_a8_shim_image_entry:
    .quad   MYOS_A8_SHIM_IMAGE_ADDR
    .size myos_a8_shim_image_entry, .-myos_a8_shim_image_entry

    .balign 16
    .global myos_a8_shim_sp_area_bottom
    .type myos_a8_shim_sp_area_bottom, %object
myos_a8_shim_sp_area_bottom:
    .skip 0x100
    .global myos_a8_shim_sp_area_top
    .type myos_a8_shim_sp_area_top, %object
myos_a8_shim_sp_area_top:
    .size myos_a8_shim_sp_area_bottom, .-myos_a8_shim_sp_area_bottom
    .global myos_a8_shim_end
    .type myos_a8_shim_end, %object
myos_a8_shim_end:
