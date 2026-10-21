/*
 * MyOS M7.14 — TEST-ONLY controlled exception trigger for the raw-entry
 * runtime harness (QEMU virt emulation).
 *
 * This file replaces raw_main.c (the production C entry) in a special
 * runtime-test image only. It is NOT part of the production raw-entry
 * artifact: `make raw-entry` still builds raw_main.c, and the runtime
 * target keeps its objects in build/raw-entry-runtime/. The rest of the
 * test image — myos_a8_raw_entry (raw_entry.S, installs VBAR_EL1), the
 * vector table and capture handler (raw_vectors.S), and the linker script
 * (raw_entry.ld) with all its layout ASSERTs — is the production source,
 * compiled with the production flags.
 *
 * Contract:
 *   - raw_entry.S tail-branches here with the image-owned stack already
 *     active (sp = __stack_top on the bank selected by the entry state,
 *     here SPSel=1 so SP_EL1) and VBAR_EL1 already installed (adr / msr /
 *     isb);
 *   - the FIRST instruction executed is `brk #TRIG_BRK_IMM`: an AArch64
 *     Breakpoint exception, a pure CPU synchronous exception with no
 *     dependency on the QEMU memory map, on peripherals, on the MMU, or on
 *     debug registers. Expected syndrome: ESR_EL1.EC = 0x3C (BRK
 *     instruction execution, AArch64), IL = 1, ISS[15:0] = TRIG_BRK_IMM;
 *     ELR_EL1 = the address of this `brk` instruction (the architectural
 *     preferred return address for BRK is the BRK instruction itself);
 *     FAR_EL1 is not a fault address for this class and is never asserted.
 *   - the exception is taken from EL1 to the Current EL, so the real
 *     capture handler in raw_vectors.S runs (slot 4: Current EL, SPx,
 *     Synchronous, because the emulator enters at EL1h with SPSel=1),
 *     writes the real exception record, and parks forever. There is no
 *     ERET path: `brk` cannot return.
 *   - the trailing branch is a fail-closed park (re-executes the trigger
 *     if the architecture ever did return, which it must not).
 *
 * The pinned report-block address (raw_entry.ld ASSERTs
 * raw_entry_report == 0x200, a symbol owned by raw_main.c in the
 * production image) is reserved here with the identical size so the same
 * linker script can be reused verbatim. The runtime test never reads or
 * writes this reservation.
 */
    .equ TRIG_BRK_IMM, 0xcafe

    .section .text.testmain, "ax"
    .align 2
    .global myos_a8_raw_main
    .type myos_a8_raw_main, %function
myos_a8_raw_main:
    brk     #TRIG_BRK_IMM
1:
    b       1b
    .size myos_a8_raw_main, .-myos_a8_raw_main

    .section .report, "aw", @progbits
    .balign 8
    .global raw_entry_report
    .type raw_entry_report, %object
raw_entry_report:
    .skip 0x40
    .size raw_entry_report, .-raw_entry_report
