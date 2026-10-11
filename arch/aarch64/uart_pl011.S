/*
 * PL011 UART driver for QEMU virt (AArch64) - Assembly implementation
 *
 * QEMU virt machine provides a PL011 UART at 0x09000000.
 * This driver provides early initialization and console operations
 * for the boot diagnostic framework.
 *
 * Target: QEMU virt ONLY. NOT for iPhone 6 / A8 hardware.
 *
 * Written in assembly to avoid compiler bugs with -mgeneral-regs-only
 */

/* PL011 register offsets */
#define UARTDR      0x00
#define UARTRSR     0x04
#define UARTFR      0x18
#define UARTIBRD    0x24
#define UARTFBRD    0x28
#define UARTLCR_H   0x2C
#define UARTCR      0x30
#define UARTIFLS    0x34
#define UARTIMSC    0x38
#define UARTRIS     0x3C
#define UARTMIS     0x40
#define UARTICR     0x44

#define UARTFR_TXFF (1 << 5)
#define UARTFR_RXFE (1 << 4)

#define UARTCR_UARTEN (1 << 0)
#define UARTCR_TXE    (1 << 8)
#define UARTCR_RXE    (1 << 9)

#define UARTLCR_H_FEN (1 << 4)
#define UARTLCR_H_WLEN_8 (3 << 5)

#define PL011_BASE    0x09000000

    .section .text, "ax"

/* Early console instance (in .bss) */
    .section .bss
    .align 8
    .global early_console
early_console:
    .skip 32  /* struct boot_console { ops, platform_data } */

    .section .text, "ax"
    .global uart_pl011_init_early
    .type uart_pl011_init_early, %function

uart_pl011_init_early:
    /* x0 = return value (address of early_console) */

    /* UART base address in x1 */
    mov x1, #PL011_BASE

    /* Disable UART first */
    str wzr, [x1, #UARTCR]

    /* Set baud rate: IBRD = 13, FBRD = 0 */
    mov w2, #13
    str w2, [x1, #UARTIBRD]
    str wzr, [x1, #UARTFBRD]

    /* Line control: 8 bits, FIFO enable */
    mov w2, #(UARTLCR_H_WLEN_8 | UARTLCR_H_FEN)
    str w2, [x1, #UARTLCR_H]

    /* Enable UART, TX, RX */
    mov w2, #(UARTCR_UARTEN | UARTCR_TXE | UARTCR_RXE)
    str w2, [x1, #UARTCR]

    /* Set up early console structure */
    adr x0, early_console
    adr x2, uart_pl011_ops
    str x2, [x0]              /* early_console.ops = &uart_pl011_ops */
    str xzr, [x0, #8]         /* early_console.platform_data = NULL */

    ret

/* UART operations */
    .section .rodata
    .align 8
    .global uart_pl011_ops
uart_pl011_ops:
    .quad 0                    /* init = NULL */
    .quad uart_pl011_putc
    .quad uart_pl011_puts
    .quad uart_pl011_write
    .quad uart_pl011_flush
    .quad 0                    /* platform_data = NULL */

    .section .text, "ax"
    .global uart_pl011_putc
    .type uart_pl011_putc, %function
uart_pl011_putc:
    /* x0 = console, w1 = char */
    mov x2, #PL011_BASE
1:  ldr w3, [x2, #UARTFR]
    tbnz w3, #5, 1b           /* Wait while TXFF */
    str w1, [x2, #UARTDR]
    mov w0, #0
    ret

    .global uart_pl011_puts
    .type uart_pl011_puts, %function
uart_pl011_puts:
    /* x0 = console, x1 = string */
    mov x2, #PL011_BASE
    mov x3, #0                /* count */
1:  ldrb w4, [x1], #1
    cbz w4, 2f
3:  ldr w5, [x2, #UARTFR]
    tbnz w5, #5, 3b
    str w4, [x2, #UARTDR]
    add x3, x3, #1
    b 1b
2:  mov x0, x3
    ret

    .global uart_pl011_write
    .type uart_pl011_write, %function
uart_pl011_write:
    /* x0 = console, x1 = buf, x2 = len */
    mov x3, #PL011_BASE
    mov x4, #0                /* count */
    cbz x2, 2f
1:  ldrb w5, [x1], #1
3:  ldr w6, [x3, #UARTFR]
    tbnz w6, #5, 3b
    str w5, [x3, #UARTDR]
    add x4, x4, #1
    cmp x4, x2
    b.lt 1b
2:  mov x0, x4
    ret

    .global uart_pl011_flush
    .type uart_pl011_flush, %function
uart_pl011_flush:
    /* x0 = console */
    mov x1, #PL011_BASE
1:  ldr w2, [x1, #UARTFR]
    tbnz w2, #5, 1b           /* Wait while TXFF */
    /* Also wait for busy bit (bit 3) */
2:  ldr w2, [x1, #UARTFR]
    tbnz w2, #3, 2b
    ret

    .global uart_pl011_init
    .type uart_pl011_init, %function
uart_pl011_init:
    /* Already initialized in early init */
    mov w0, #0
    ret

    .size uart_pl011_init_early, .-uart_pl011_init_early

    .section .text, "ax"
    .global boot_console_register
    .type boot_console_register, %function
boot_console_register:
    /* x0 = console pointer */
    adrp x1, boot_console
    str x0, [x1]
    dsb sy
    ret

    .size boot_console_register, .-boot_console_register