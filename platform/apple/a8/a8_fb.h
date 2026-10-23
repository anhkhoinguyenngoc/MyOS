/*
 * platform/apple/a8/a8_fb.h — Apple A8 inherited-framebuffer adapter.
 *
 * target: iphone6 (Apple A8), PongoOS `bootr` raw handoff.
 *
 * STATUS: the framebuffer is INHERITED from the carrier's boot_args Video
 * block; this adapter never initializes a display, panel, DSI link, clock,
 * backlight, or MMIO controller. It only:
 *   1. reads the six Video words from the boot_args pointer the carrier
 *      handed over (layout pinned by pongoos-src cb83dd83
 *      src/kernel/pongo.h:69-85, all fields `unsigned long` = 64-bit),
 *   2. rejects geometry that the renderer cannot prove safe,
 *   3. asks kernel/fb_text.c to draw into the inherited buffer.
 *
 * The raw handoff view is expected to be identity/physical: pongo_entry()
 * passes gBootArgs unchanged into jump_to_image_extended() after
 * lowlevel_cleanup() disables the MMU (entry.c:307-311; mm.c:344-348), and the
 * image address itself is converted from the cacheable upload pointer to the
 * 0x800000000 identity view. The XNU path is the one that converts gBootArgs
 * to kCacheableView (xnu.c:283). The actual pointer value, Video values, and
 * scanout are still unobserved, so the production raw image does NOT call this
 * adapter. Enabling it is an explicit, build-time opt-in
 * (`MYOS_A8_RENDER_HELLO`) and a physical run is required to observe the
 * result. If the pointer is unusable, the raw image's own vector table records
 * the fault; the adapter cannot detect that without dereferencing.
 *
 * Pixel format: PongoOS treats the inherited rows as `v_rowBytes >> 2` uint32
 * pixels (fb.c:214-217); that is the available source evidence for 4
 * bytes/pixel. The demo paints opaque white (0xFFFFFFFF) text on an opaque
 * black (0xFF000000) box; this is defensible under ARGB and under an ignored
 * high byte, but exact panel order and high-byte semantics remain unverified
 * until a physical observation. `v_depth` is recorded but not interpreted;
 * decoding it would be a guess. Geometry/stride/bytes are rejected when
 * inconsistent with this 4B/px view; a non-32bpp format with a compatible
 * stride is not separately detectable, so a physical observation is required.
 */
#ifndef MYOS_PLATFORM_APPLE_A8_A8_FB_H
#define MYOS_PLATFORM_APPLE_A8_A8_FB_H

#include "kernel/fb_text.h"

/*
 * Absolute byte offsets inside the carrier's packed `struct boot_args`.
 * Source: pongoos-src cb83dd83 src/kernel/pongo.h:69-85, verified by a host
 * compile in M7.35 (Video starts at 0x28; each field is `unsigned long`).
 */
#define MYOS_A8_BOOTARGS_VIDEO_BASE    0x28u  /* v_baseAddr  */
#define MYOS_A8_BOOTARGS_VIDEO_DISPLAY 0x30u  /* v_display   */
#define MYOS_A8_BOOTARGS_VIDEO_ROWBYTES 0x38u /* v_rowBytes  */
#define MYOS_A8_BOOTARGS_VIDEO_WIDTH   0x40u  /* v_width     */
#define MYOS_A8_BOOTARGS_VIDEO_HEIGHT  0x48u  /* v_height    */
#define MYOS_A8_BOOTARGS_VIDEO_DEPTH   0x50u  /* v_depth     */
/* Highest byte the adapter reads (inclusive): depth is the last word. */
#define MYOS_A8_BOOTARGS_READ_LAST     0x57u

typedef enum {
    MYOS_A8_FB_OK = 0,
    MYOS_A8_FB_ERR_NULL,       /* NULL output/descriptor argument          */
    MYOS_A8_FB_ERR_BOOTARGS,   /* NULL, unaligned, or address-wrapping    */
    MYOS_A8_FB_ERR_VIDEO,      /* v_baseAddr is NULL or misaligned         */
    MYOS_A8_FB_ERR_GEOMETRY,   /* width/height zero, oversized, or tiny    */
    MYOS_A8_FB_ERR_STRIDE,     /* v_rowBytes too small/misaligned/too big  */
    MYOS_A8_FB_ERR_RANGE,      /* stride*height overflows the safe bound   */
    MYOS_A8_FB_ERR_RENDER,     /* renderer descriptor/geometry rejected    */
} myos_a8_fb_status_t;

/* Raw Video block as read from the carrier; fields kept 64-bit. */
typedef struct {
    uint64_t base;
    uint64_t display;   /* opaque "Display Code (if Applicable)"; not decoded */
    uint64_t row_bytes;
    uint64_t width;
    uint64_t height;
    uint64_t depth;     /* opaque "Pixel Depth and other parameters"         */
} myos_a8_video_t;

const char *myos_a8_fb_status_name(myos_a8_fb_status_t status);

/*
 * Read and validate the Video block. Dereferences boot_args; call only in the
 * opt-in raw demo (identity/physical view expected; actual value unobserved).
 * Returns MYOS_A8_FB_OK and fills `video` only after alignment checks;
 * `video` is zeroed first.
 */
myos_a8_fb_status_t myos_a8_video_read(uint64_t boot_args,
                                       myos_a8_video_t *video);

/*
 * Convert a validated Video block into a renderer descriptor. Validates:
 * base alignment, width/height bounds, 4-byte stride, stride >= width*4,
 * stride*height <= MYOS_FB_MAX_BYTES, and the renderer's own checks.
 * Does not read or write any display register.
 */
myos_a8_fb_status_t myos_a8_fb_from_boot_args(uint64_t boot_args,
                                               myos_fb_t *fb);

/*
 * Draw "Hello User" centred in the inherited framebuffer, white on a
 * full-screen black background, scale 3. Returns MYOS_A8_FB_ERR_GEOMETRY
 * without writing anything if the framebuffer is too small for the text.
 * `pixels_written` (optional) receives the number of white foreground pixels
 * stored, or 0 on failure. This is the complete opt-in demo; it performs no
 * display initialization.
 */
myos_a8_fb_status_t myos_a8_fb_draw_hello(uint64_t boot_args,
                                          uint64_t *pixels_written);

#if defined(MYOS_A8_HELLO_DEBUG) && MYOS_A8_HELLO_DEBUG
myos_a8_fb_status_t myos_a8_fb_draw_hello_debug(uint64_t boot_args,
                                                uint64_t *pixels_written);
#endif

#if defined(MYOS_A8_HELLO_BYPASS) && MYOS_A8_HELLO_BYPASS
myos_a8_fb_status_t myos_a8_fb_draw_hello_bypass(uint64_t boot_args);
#endif

#if defined(MYOS_A8_HELLO_FONTFIX_BYPASS) && MYOS_A8_HELLO_FONTFIX_BYPASS
myos_a8_fb_status_t myos_a8_fb_draw_hello_fontfix_bypass(uint64_t boot_args);
#endif

#if defined(MYOS_A8_HELLO_HARDCODED) && MYOS_A8_HELLO_HARDCODED
myos_a8_fb_status_t myos_a8_fb_draw_hello_hardcoded(uint64_t boot_args);
#endif

#if defined(MYOS_A8_RED_TEST) && MYOS_A8_RED_TEST
myos_a8_fb_status_t myos_a8_fb_draw_minimal_red(uint64_t boot_args);
#endif

#if defined(MYOS_A8_CONSOLE_HARDCODED) && MYOS_A8_CONSOLE_HARDCODED
myos_a8_fb_status_t myos_a8_console_hardcoded(uint64_t boot_args);
#endif

#endif /* MYOS_PLATFORM_APPLE_A8_A8_FB_H */
