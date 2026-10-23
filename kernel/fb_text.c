/*
 * kernel/fb_text.c — target-independent framebuffer text renderer.
 *
 * See kernel/fb_text.h for the contract. No library calls, no allocation, no
 * floating point, no MMIO register programming: only integer arithmetic and
 * volatile 32-bit stores into the descriptor the caller supplied.
 */
#include "kernel/fb_text.h"

/*
 * Hand-authored 8x8 font, one byte per row, top row first, bit 0 = leftmost
 * pixel. The table contains exactly the glyphs of "Hello User": uppercase H
 * and U occupy rows 1..6; lowercase e/o/s/r occupy the x-height rows 2..6 and
 * l is an ascender in rows 1..6; rows 0 and 7 are blank leading/descender
 * margins. Unknown characters are not substituted: myos_fb_draw_text()
 * rejects them before writing anything.
 */
typedef struct {
    char ch;
    uint8_t rows[MYOS_FB_FONT_HEIGHT];
} myos_fb_glyph_t;

#if defined(MYOS_A8_FONT_TEXT) && MYOS_A8_FONT_TEXT
#define MYOS_FB_FONT_SECTION \
    __attribute__((section(".text.font"), used, aligned(8)))
#else
#define MYOS_FB_FONT_SECTION __attribute__((section(".rodata.early")))
#endif

static const myos_fb_glyph_t myos_fb_font[] MYOS_FB_FONT_SECTION = {
    { ' ', { 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 } },
    { 'H', { 0x00, 0x66, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x00 } },
    { 'U', { 0x00, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00 } },
    { 'e', { 0x00, 0x00, 0x3C, 0x66, 0x7E, 0x60, 0x3C, 0x00 } },
    { 'l', { 0x00, 0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x00 } },
    { 'o', { 0x00, 0x00, 0x3C, 0x66, 0x66, 0x66, 0x3C, 0x00 } },
    { 'r', { 0x00, 0x00, 0x36, 0x06, 0x06, 0x06, 0x06, 0x00 } },
    { 's', { 0x00, 0x00, 0x3E, 0x60, 0x3C, 0x06, 0x7C, 0x00 } },
};

static const myos_fb_glyph_t *glyph_for(char c)
{
    size_t i;

    for (i = 0; i < (sizeof(myos_fb_font) / sizeof(myos_fb_font[0])); i++) {
        if (myos_fb_font[i].ch == c) {
            return &myos_fb_font[i];
        }
    }
    return NULL;
}

int myos_fb_font_has(char c)
{
    return glyph_for(c) != NULL;
}

const char *myos_fb_status_name(myos_fb_status_t status)
{
    switch (status) {
    case MYOS_FB_OK:          return "ok";
    case MYOS_FB_ERR_NULL:    return "null";
    case MYOS_FB_ERR_FORMAT:  return "format";
    case MYOS_FB_ERR_GEOMETRY:return "geometry";
    case MYOS_FB_ERR_STRIDE:  return "stride";
    case MYOS_FB_ERR_ALIGNMENT:return "alignment";
    case MYOS_FB_ERR_RANGE:   return "range";
    case MYOS_FB_ERR_GLYPH:   return "glyph";
    default:                  return "invalid";
    }
}

myos_fb_status_t myos_fb_validate(const myos_fb_t *fb)
{
    uint64_t min_stride;
    uint64_t required;
    uintptr_t base;

    if (fb == NULL || fb->base == NULL) {
        return MYOS_FB_ERR_NULL;
    }
    if (fb->bpp != 4u ||
        fb->format != MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC) {
        return MYOS_FB_ERR_FORMAT;
    }
    if (fb->width == 0u || fb->height == 0u ||
        fb->width > MYOS_FB_MAX_DIM || fb->height > MYOS_FB_MAX_DIM) {
        return MYOS_FB_ERR_GEOMETRY;
    }

    min_stride = (uint64_t)fb->width * 4u;
    if ((uint64_t)fb->stride < min_stride ||
        (fb->stride & 3u) != 0u ||
        fb->stride > MYOS_FB_MAX_STRIDE) {
        return MYOS_FB_ERR_STRIDE;
    }

    required = (uint64_t)fb->stride * (uint64_t)fb->height;
    if (required > MYOS_FB_MAX_BYTES || fb->bytes < required) {
        return MYOS_FB_ERR_RANGE;
    }

    base = (uintptr_t)fb->base;
    if ((base & 3u) != 0u) {
        return MYOS_FB_ERR_ALIGNMENT;
    }
    /* The last addressed byte must not wrap the address space. required > 0
     * because width and height are both non-zero. */
    if (base > (uintptr_t)(UINTPTR_MAX - (required - 1u))) {
        return MYOS_FB_ERR_RANGE;
    }
    return MYOS_FB_OK;
}

uint32_t myos_fb_text_width(const char *s, uint32_t scale)
{
    uint64_t chars = 0;

    if (s == NULL || scale == 0u || scale > MYOS_FB_MAX_SCALE) {
        return 0u;
    }
    while (s[chars] != '\0') {
        if (chars >= MYOS_FB_MAX_TEXT) {
            return 0u;
        }
        chars++;
    }
    /* <= 4096 chars * 8 pixels * 16 scale = 524288, fits in 32 bits. */
    return (uint32_t)(chars * MYOS_FB_FONT_WIDTH * scale);
}

static inline void put_pixel(const myos_fb_t *fb, uint32_t x, uint32_t y,
                             uint32_t color)
{
    volatile uint32_t *p = (volatile uint32_t *)(fb->base +
        (uint64_t)y * (uint64_t)fb->stride + (uint64_t)x * 4u);
    *p = color;
}

myos_fb_status_t myos_fb_fill_rect(const myos_fb_t *fb,
                                   int32_t x, int32_t y,
                                   int32_t width, int32_t height,
                                   uint32_t color,
                                   uint64_t *pixels_written)
{
    myos_fb_status_t status;
    int64_t x0, y0, x1, y1;
    uint64_t count = 0;

    if (pixels_written != NULL) {
        *pixels_written = 0;
    }
    status = myos_fb_validate(fb);
    if (status != MYOS_FB_OK) {
        return status;
    }
    if (width <= 0 || height <= 0) {
        return MYOS_FB_ERR_GEOMETRY;
    }

    x0 = (int64_t)x;
    y0 = (int64_t)y;
    x1 = x0 + (int64_t)width;
    y1 = y0 + (int64_t)height;

    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (x1 > (int64_t)fb->width) {
        x1 = (int64_t)fb->width;
    }
    if (y1 > (int64_t)fb->height) {
        y1 = (int64_t)fb->height;
    }

    /* Completely outside: a successful no-op. */
    for (int64_t py = y0; py < y1; py++) {
        for (int64_t px = x0; px < x1; px++) {
            put_pixel(fb, (uint32_t)px, (uint32_t)py, color);
            count++;
        }
    }

    if (pixels_written != NULL) {
        *pixels_written = count;
    }
    return MYOS_FB_OK;
}

myos_fb_status_t myos_fb_draw_text(const myos_fb_t *fb,
                                   int32_t x, int32_t y,
                                   const char *s, uint32_t scale,
                                   uint64_t *pixels_written)
{
    myos_fb_status_t status;
    size_t chars = 0;
    uint64_t count = 0;

    if (pixels_written != NULL) {
        *pixels_written = 0;
    }
    status = myos_fb_validate(fb);
    if (status != MYOS_FB_OK) {
        return status;
    }
    if (s == NULL) {
        return MYOS_FB_ERR_NULL;
    }
    if (scale == 0u || scale > MYOS_FB_MAX_SCALE) {
        return MYOS_FB_ERR_GEOMETRY;
    }

    /* Validate the complete string before touching the framebuffer. */
    while (s[chars] != '\0') {
        if (chars >= MYOS_FB_MAX_TEXT) {
            return MYOS_FB_ERR_RANGE;
        }
        if (glyph_for(s[chars]) == NULL) {
            return MYOS_FB_ERR_GLYPH;
        }
        chars++;
    }

    for (size_t i = 0; i < chars; i++) {
        const myos_fb_glyph_t *glyph = glyph_for(s[i]);
        if (glyph == NULL) {
            return MYOS_FB_ERR_GLYPH;
        }
        for (uint32_t row = 0; row < MYOS_FB_FONT_HEIGHT; row++) {
            uint8_t bits = glyph->rows[row];
            for (uint32_t col = 0; col < MYOS_FB_FONT_WIDTH; col++) {
                if ((bits & (uint8_t)(1u << col)) == 0u) {
                    continue;
                }
                for (uint32_t sy = 0; sy < scale; sy++) {
                    for (uint32_t sx = 0; sx < scale; sx++) {
                        int64_t px = (int64_t)x +
                            (int64_t)((i * MYOS_FB_FONT_WIDTH + col) * scale + sx);
                        int64_t py = (int64_t)y +
                            (int64_t)(row * scale + sy);
                        if (px < 0 || py < 0 ||
                            px >= (int64_t)fb->width ||
                            py >= (int64_t)fb->height) {
                            continue;
                        }
                        put_pixel(fb, (uint32_t)px, (uint32_t)py, MYOS_FB_WHITE);
                        count++;
                    }
                }
            }
        }
    }

    if (pixels_written != NULL) {
        *pixels_written = count;
    }
    return MYOS_FB_OK;
}

#if defined(MYOS_A8_HELLO_BYPASS) && MYOS_A8_HELLO_BYPASS
myos_fb_status_t myos_fb_draw_text_unchecked(const myos_fb_t *fb,
                                             int32_t x, int32_t y,
                                             const char *s, uint32_t scale,
                                             uint64_t *pixels_written)
{
    size_t chars = 0;
    uint64_t count = 0;

    if (pixels_written != NULL) {
        *pixels_written = 0;
    }
    if (fb == NULL || fb->base == NULL || s == NULL) {
        return MYOS_FB_ERR_NULL;
    }
    if (scale == 0u || scale > MYOS_FB_MAX_SCALE) {
        return MYOS_FB_ERR_GEOMETRY;
    }

    while (s[chars] != '\0') {
        if (chars >= MYOS_FB_MAX_TEXT) {
            return MYOS_FB_ERR_RANGE;
        }
        if (glyph_for(s[chars]) == NULL) {
            return MYOS_FB_ERR_GLYPH;
        }
        chars++;
    }

    for (size_t i = 0; i < chars; i++) {
        const myos_fb_glyph_t *glyph = glyph_for(s[i]);
        if (glyph == NULL) {
            return MYOS_FB_ERR_GLYPH;
        }
        for (uint32_t row = 0; row < MYOS_FB_FONT_HEIGHT; row++) {
            uint8_t bits = glyph->rows[row];
            for (uint32_t col = 0; col < MYOS_FB_FONT_WIDTH; col++) {
                if ((bits & (uint8_t)(1u << col)) == 0u) {
                    continue;
                }
                for (uint32_t sy = 0; sy < scale; sy++) {
                    for (uint32_t sx = 0; sx < scale; sx++) {
                        int64_t px = (int64_t)x +
                            (int64_t)((i * MYOS_FB_FONT_WIDTH + col) * scale + sx);
                        int64_t py = (int64_t)y +
                            (int64_t)(row * scale + sy);
                        if (px < 0 || py < 0 ||
                            px >= (int64_t)fb->width ||
                            py >= (int64_t)fb->height) {
                            continue;
                        }
                        put_pixel(fb, (uint32_t)px, (uint32_t)py, MYOS_FB_WHITE);
                        count++;
                    }
                }
            }
        }
    }

    if (pixels_written != NULL) {
        *pixels_written = count;
    }
    return MYOS_FB_OK;
}
#endif
