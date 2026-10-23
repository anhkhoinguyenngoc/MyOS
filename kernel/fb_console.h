#ifndef MYOS_KERNEL_FB_CONSOLE_H
#define MYOS_KERNEL_FB_CONSOLE_H

#include <stdint.h>

#define CONSOLE_SCALE 2u
#define CONSOLE_CELL_WIDTH (8u * CONSOLE_SCALE)
#define CONSOLE_CELL_HEIGHT (8u * CONSOLE_SCALE)

int console_init(uint64_t boot_args);
void console_putc(char c);
void console_puts(const char *s);
void console_clear(void);
void console_scroll(void);
void console_get_cursor(uint32_t *column, uint32_t *row);
void console_get_size(uint32_t *columns, uint32_t *rows);

#if defined(MYOS_A8_CONSOLE_BLACK) && MYOS_A8_CONSOLE_BLACK
int console_init_only(uint64_t boot_args);
#endif

#if defined(MYOS_A8_CONSOLE_H) && MYOS_A8_CONSOLE_H
int console_init_h(uint64_t boot_args);
#endif

#if defined(MYOS_A8_CONSOLE_DIAG) && MYOS_A8_CONSOLE_DIAG
int console_init_diag(uint64_t boot_args);
#endif

#endif
