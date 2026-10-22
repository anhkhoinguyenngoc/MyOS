/*
 * kernel/fb_text.h — target-independent framebuffer text renderer.
 *
 * target: target-independent (pure computation over a caller-supplied buffer;
 *         no MMIO register programming, no platform constants, no hardware
 *         initialization).
 *
 * This is the renderer half of the display work. It deliberately knows nothing
 * about the Apple A8, PongoOS, boot_args, or display controllers: the caller
 * supplies an explicit framebuffer descriptor and owns the base pointer. That
 * keeps every glyph/stride/bounds decision host-testable and keeps the boot
 * path's "no invented hardware values" rule intact.
 *
 * The only pixel encoding supported is a 32-bit-per-pixel view. PongoOS
 * derives the inherited framebuffer row length as
 * `gBootArgs->Video.v_rowBytes >> 2` and stores `uint32_t` pixels
 * (pongoos-src cb83dd83 src/drivers/framebuffer/fb.c:214-217); that is the
 * source evidence for 4 bytes/pixel on this carrier. The panel's exact channel
 * order and high-byte semantics are NOT proven. PongoOS's own color_compose
 * says ARGB8,8,8,8, so the text renderer writes opaque white (0xFFFFFFFF);
 * the A8 demo fills its box with opaque black (0xFF000000). Both are correct
 * under ARGB and remain black/white if the high byte is padding/ignored.
 * `myos_fb_fill_rect` still accepts any caller-supplied 32-bit color; only
 * `myos_fb_draw_text` is fixed to white. No other format is guessed:
 * mismatched geometry/stride/bytes fail closed.
 *
 * The font is a hand-authored 8x8 bitmap containing exactly the glyphs needed
 * by "Hello User" (space, H, U, e, l, o, r, s). Drawing any other character
 * fails closed before a single pixel is written.
 */
#ifndef MYOS_KERNEL_FB_TEXT_H
#define MYOS_KERNEL_FB_TEXT_H

#include <stddef.h>
#include <stdint.h>

#define MYOS_FB_FONT_WIDTH   8u
#define MYOS_FB_FONT_HEIGHT  8u
#define MYOS_FB_MAX_SCALE    16u
#define MYOS_FB_MAX_DIM      8192u
#define MYOS_FB_MAX_STRIDE   (1024u * 1024u)
#define MYOS_FB_MAX_BYTES    (64ull * 1024ull * 1024ull)
#define MYOS_FB_MAX_TEXT     4096u

#define MYOS_FB_BLACK 0xFF000000u  /* opaque black under ARGB; black if X ignored */
#define MYOS_FB_WHITE 0xFFFFFFFFu  /* opaque white under ARGB; white if X ignored */

/* Pixel encodings the renderer is allowed to touch. */
typedef enum {
    MYOS_FB_FMT_NONE = 0,
    /*
     * 32 bits/pixel, 4-byte aligned rows. RGB channel order is not asserted;
     * the high byte is opaque alpha under ARGB and ignored padding under XRGB.
     * Callers that pass colors other than MYOS_FB_BLACK/WHITE must know the
     * panel order; the renderer's text path uses only opaque white.
     */
    MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC = 1,
} myos_fb_format_t;

/*
 * Caller-owned framebuffer view.
 *
 * base     first byte of the framebuffer (must be 4-byte aligned).
 * width    visible pixels per row.
 * height   visible rows.
 * stride   bytes from one row start to the next (>= width*4, multiple of 4).
 * bpp      bytes per pixel; must be 4 for the supported format.
 * format   must be MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC.
 * bytes    accessible bytes starting at base; must be >= stride*height.
 *
 * `bytes` is the caller's assertion about its own buffer. The renderer never
 * writes outside [base, base+bytes), and it never uses `bytes` as a substitute
 * for the geometry checks above.
 */
typedef struct {
    volatile uint8_t *base;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t bpp;
    myos_fb_format_t format;
    uint64_t bytes;
} myos_fb_t;

typedef enum {
    MYOS_FB_OK = 0,
    MYOS_FB_ERR_NULL,        /* NULL descriptor, base, or text          */
    MYOS_FB_ERR_FORMAT,      /* unsupported bpp/format                  */
    MYOS_FB_ERR_GEOMETRY,    /* zero/oversized width, height, or scale  */
    MYOS_FB_ERR_STRIDE,      /* too small, misaligned, or too large     */
    MYOS_FB_ERR_ALIGNMENT,   /* base not 4-byte aligned                 */
    MYOS_FB_ERR_RANGE,       /* bytes/offset overflow or too large      */
    MYOS_FB_ERR_GLYPH,       /* character has no glyph in this font     */
} myos_fb_status_t;

/* Validate every descriptor field and the pointer arithmetic preconditions.
 * Returns MYOS_FB_OK only when every check passes. */
myos_fb_status_t myos_fb_validate(const myos_fb_t *fb);

/* Stable one-line name for diagnostics (never NULL). */
const char *myos_fb_status_name(myos_fb_status_t status);

/* 1 iff the font contains a glyph for c (ASCII byte). */
int myos_fb_font_has(char c);

/* Width in pixels of s at integer scale, or 0 for NULL/invalid/too-long. */
uint32_t myos_fb_text_width(const char *s, uint32_t scale);

/*
 * Fill an axis-aligned rectangle with one 32-bit pixel value.
 * Clips to the descriptor; pixels outside it are never addressed. A rectangle
 * completely outside is a successful no-op. Non-positive width/height fail
 * with MYOS_FB_ERR_GEOMETRY. `pixels_written` (optional) receives the count.
 */
myos_fb_status_t myos_fb_fill_rect(const myos_fb_t *fb,
                                   int32_t x, int32_t y,
                                   int32_t width, int32_t height,
                                   uint32_t color,
                                   uint64_t *pixels_written);

/*
 * Draw a NUL-terminated string at (x,y) with an integer pixel scale.
 * (x,y) may be negative or beyond the framebuffer; drawing is clipped per
 * pixel. The whole string is checked against the font before any pixel is
 * written, so an unsupported character cannot leave a partial string behind.
 * Empty text is a successful no-op. `pixels_written` (optional) receives the
 * number of foreground pixels actually stored.
 */
myos_fb_status_t myos_fb_draw_text(const myos_fb_t *fb,
                                   int32_t x, int32_t y,
                                   const char *s, uint32_t scale,
                                   uint64_t *pixels_written);

#endif /* MYOS_KERNEL_FB_TEXT_H */
