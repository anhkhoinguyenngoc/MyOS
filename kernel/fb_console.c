#include "kernel/fb_console.h"
#include "kernel/font_inline.h"
#include "platform/apple/a8/a8_fb.h"

typedef struct {
    myos_fb_t fb;
    uint32_t columns;
    uint32_t rows;
    uint32_t cursor_column;
    uint32_t cursor_row;
    uint32_t initialized;
} console_state_t;

static console_state_t g_console __attribute__((section(".data"))) = {
    .initialized = 0u,
};

static void console_fill_rect(uint32_t x, uint32_t y, uint32_t width,
                              uint32_t height, uint32_t color)
{
    uint32_t x1;
    uint32_t y1;

    if (!g_console.initialized || width == 0u || height == 0u) {
        return;
    }
    if (x >= g_console.fb.width || y >= g_console.fb.height) {
        return;
    }

    x1 = x + width;
    y1 = y + height;
    if (x1 > g_console.fb.width) {
        x1 = g_console.fb.width;
    }
    if (y1 > g_console.fb.height) {
        y1 = g_console.fb.height;
    }

    for (uint32_t py = y; py < y1; py++) {
        volatile uint32_t *line = (volatile uint32_t *)(g_console.fb.base +
            (uint64_t)py * g_console.fb.stride);
        for (uint32_t px = x; px < x1; px++) {
            line[px] = color;
        }
    }
}

void console_clear(void)
{
    if (!g_console.initialized) {
        return;
    }

    for (uint32_t y = 0; y < g_console.fb.height; y++) {
        volatile uint32_t *line = (volatile uint32_t *)(g_console.fb.base +
            (uint64_t)y * g_console.fb.stride);
        for (uint32_t x = 0; x < g_console.fb.width; x++) {
            line[x] = MYOS_FB_BLACK;
        }
    }
    g_console.cursor_column = 0u;
    g_console.cursor_row = 0u;
}

void console_scroll(void)
{
    uint64_t shift;
    uint64_t bytes;

    if (!g_console.initialized) {
        return;
    }
    if (g_console.fb.height <= CONSOLE_CELL_HEIGHT) {
        console_clear();
        return;
    }

    shift = (uint64_t)CONSOLE_CELL_HEIGHT * g_console.fb.stride;
    bytes = (uint64_t)(g_console.fb.height - CONSOLE_CELL_HEIGHT) *
            g_console.fb.stride;

    for (uint64_t i = 0; i < bytes; i++) {
        g_console.fb.base[i] = g_console.fb.base[i + shift];
    }
    console_fill_rect(0u, g_console.fb.height - CONSOLE_CELL_HEIGHT,
                      g_console.fb.width, CONSOLE_CELL_HEIGHT,
                      MYOS_FB_BLACK);
    g_console.cursor_column = 0u;
    g_console.cursor_row = g_console.rows - 1u;
}

static void console_clear_cell(uint32_t column, uint32_t row)
{
    console_fill_rect(column * CONSOLE_CELL_WIDTH,
                      row * CONSOLE_CELL_HEIGHT,
                      CONSOLE_CELL_WIDTH, CONSOLE_CELL_HEIGHT,
                      MYOS_FB_BLACK);
}

static void console_draw_cell(uint32_t column, uint32_t row, uint8_t ch)
{
    uint32_t x = column * CONSOLE_CELL_WIDTH;
    uint32_t y = row * CONSOLE_CELL_HEIGHT;
    const uint8_t *glyph;

    console_clear_cell(column, row);
    glyph = myos_font8x8_basic[ch & 0x7fu];

    for (uint32_t gy = 0; gy < 8u; gy++) {
        uint8_t bits = glyph[gy];
        for (uint32_t gx = 0; gx < 8u; gx++) {
            if ((bits & (uint8_t)(1u << gx)) == 0u) {
                continue;
            }
            console_fill_rect(x + gx * CONSOLE_SCALE,
                              y + gy * CONSOLE_SCALE,
                              CONSOLE_SCALE, CONSOLE_SCALE,
                              MYOS_FB_WHITE);
        }
    }
}

static void console_newline(void)
{
    g_console.cursor_column = 0u;
    g_console.cursor_row++;
    if (g_console.cursor_row >= g_console.rows) {
        console_scroll();
    }
}

void console_putc(char c)
{
    uint8_t ch = (uint8_t)c;

    if (!g_console.initialized) {
        return;
    }
    if (ch == (uint8_t)'\n') {
        console_newline();
        return;
    }
    if (ch == (uint8_t)'\r') {
        g_console.cursor_column = 0u;
        return;
    }
    if (ch == (uint8_t)'\b') {
        if (g_console.cursor_column > 0u) {
            g_console.cursor_column--;
        } else if (g_console.cursor_row > 0u) {
            g_console.cursor_row--;
            g_console.cursor_column = g_console.columns - 1u;
        } else {
            return;
        }
        console_clear_cell(g_console.cursor_column, g_console.cursor_row);
        return;
    }
    if (ch == (uint8_t)'\t') {
        uint32_t target =
            ((g_console.cursor_column / 8u) + 1u) * 8u;
        if (target > g_console.columns) {
            target = g_console.columns;
        }
        if (target > g_console.cursor_column) {
            uint32_t spaces = target - g_console.cursor_column;
            for (uint32_t i = 0; i < spaces; i++) {
                console_putc(' ');
            }
        }
        return;
    }
    if (ch < 32u || ch > 126u) {
        ch = (uint8_t)'?';
    }
    if (g_console.cursor_column >= g_console.columns) {
        console_newline();
    }

    console_draw_cell(g_console.cursor_column, g_console.cursor_row, ch);
    g_console.cursor_column++;
    if (g_console.cursor_column >= g_console.columns) {
        console_newline();
    }
}

void console_puts(const char *s)
{
    if (s == NULL) {
        return;
    }
    while (*s != '\0') {
        console_putc(*s);
        s++;
    }
}

void console_get_cursor(uint32_t *column, uint32_t *row)
{
    if (column != NULL) {
        *column = g_console.cursor_column;
    }
    if (row != NULL) {
        *row = g_console.cursor_row;
    }
}

void console_get_size(uint32_t *columns, uint32_t *rows)
{
    if (columns != NULL) {
        *columns = g_console.columns;
    }
    if (rows != NULL) {
        *rows = g_console.rows;
    }
}

int console_init(uint64_t boot_args)
{
    myos_a8_video_t video;
    myos_a8_fb_status_t status;
    uint64_t stride_px;
    uint64_t visible_width;
    uint64_t total;
    uint32_t columns;
    uint32_t rows;

    g_console.initialized = 0u;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return -1;
    }
    if (video.base == 0u || (video.base & 3u) != 0u ||
        video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.width == 0u || video.height == 0u) {
        return -2;
    }
    if (video.row_bytes > UINT32_MAX || video.height > UINT32_MAX) {
        return -2;
    }
    if (video.height > MYOS_FB_MAX_DIM ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return -2;
    }

    stride_px = video.row_bytes / 4u;
    if (stride_px == 0u || video.height > UINT64_MAX / stride_px) {
        return -3;
    }
    visible_width = video.width;
    if (visible_width > stride_px) {
        visible_width = stride_px;
    }
    if (visible_width > MYOS_FB_MAX_DIM) {
        return -2;
    }

    total = stride_px * video.height;
    if (total > MYOS_FB_MAX_BYTES / 4u || total > UINT64_MAX / 4u) {
        return -3;
    }

    columns = (uint32_t)visible_width / CONSOLE_CELL_WIDTH;
    rows = (uint32_t)video.height / CONSOLE_CELL_HEIGHT;
    if (columns == 0u || rows == 0u) {
        return -4;
    }

    g_console.fb.base = (volatile uint8_t *)(uintptr_t)video.base;
    g_console.fb.width = (uint32_t)visible_width;
    g_console.fb.height = (uint32_t)video.height;
    g_console.fb.stride = (uint32_t)video.row_bytes;
    g_console.fb.bpp = 4u;
    g_console.fb.format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    g_console.fb.bytes = total * 4u;
    g_console.columns = columns;
    g_console.rows = rows;
    g_console.cursor_column = 0u;
    g_console.cursor_row = 0u;
    g_console.initialized = 1u;

    console_clear();
    return 0;
}

#if defined(MYOS_A8_CONSOLE_BLACK) && MYOS_A8_CONSOLE_BLACK
int console_init_only(uint64_t boot_args)
{
    return console_init(boot_args);
}
#endif

#if defined(MYOS_A8_CONSOLE_H) && MYOS_A8_CONSOLE_H
int console_init_h(uint64_t boot_args)
{
    int status;

    status = console_init(boot_args);
    if (status != 0) {
        return status;
    }

    g_console.cursor_column = g_console.columns / 2u;
    g_console.cursor_row = g_console.rows / 2u;
    console_putc('H');
    return 0;
}
#endif

#if defined(MYOS_A8_CONSOLE_DIAG) && MYOS_A8_CONSOLE_DIAG
int console_init_diag(uint64_t boot_args)
{
    myos_a8_video_t video;
    myos_a8_fb_status_t status;
    uint64_t stride_px;
    uint64_t total;
    volatile uint32_t *fb;
    uint32_t color;
    int result;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return -1;
    }
    if (video.base == 0u || (video.base & 3u) != 0u ||
        video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.width == 0u || video.height == 0u) {
        return -2;
    }
    if (video.row_bytes > UINT32_MAX || video.height > UINT32_MAX ||
        video.height > MYOS_FB_MAX_DIM ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return -2;
    }

    stride_px = video.row_bytes / 4u;
    if (stride_px == 0u || video.height > UINT64_MAX / stride_px) {
        return -3;
    }
    total = stride_px * video.height;
    if (total > MYOS_FB_MAX_BYTES / 4u || total > UINT64_MAX / 4u) {
        return -3;
    }

    if (stride_px < video.width) {
        color = 0xFFFF0000u;
        result = -2;
    } else {
        color = 0xFF00FF00u;
        result = 0;
    }

    fb = (volatile uint32_t *)(uintptr_t)video.base;
    for (uint64_t i = 0; i < total; i++) {
        fb[i] = color;
    }
    return result;
}
#endif
