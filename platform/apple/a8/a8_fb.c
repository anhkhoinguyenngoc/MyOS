/*
 * platform/apple/a8/a8_fb.c — Apple A8 inherited-framebuffer adapter.
 *
 * See a8_fb.h. This translation unit is only linked into the opt-in raw demo
 * image; it is not part of the production raw_entry.bin. It contains no
 * display-controller initialization or register writes; its only stores are
 * the caller-authorized framebuffer pixels.
 */
#include "platform/apple/a8/a8_fb.h"

_Static_assert(sizeof(uint64_t) == 8u, "Video words must be 64-bit");

/* Apple A8 / PongoOS cache maintenance granularity (lowlevel.c uses 64). */
#define MYOS_A8_DCACHE_LINE 64u

static uint64_t read_u64(uint64_t address)
{
    return *(const volatile uint64_t *)(uintptr_t)address;
}

/*
 * Clean the data-cache lines covering the framebuffer after pixel stores.
 * PongoOS itself calls cache_clean() after drawing (fb.c:47,60,69,184,208,
 * 262); the raw handoff currently arrives with D-cache off, so this is a
 * no-op on the observed path, but it keeps the demo correct if the carrier
 * leaves caches enabled. The host build has no AArch64 cache instructions.
 */
static void myos_a8_fb_clean_dcache(volatile void *base, uint64_t bytes)
{
#if defined(__aarch64__)
    uintptr_t start;
    uintptr_t end;
    uintptr_t line;

    if (base == NULL || bytes == 0u) {
        return;
    }

    start = (uintptr_t)base;
    end = start + (uintptr_t)bytes;
    line = start & ~(uintptr_t)(MYOS_A8_DCACHE_LINE - 1u);
    while (line < end) {
        __asm__ __volatile__("dc cvac, %0" : : "r"(line) : "memory");
        line += MYOS_A8_DCACHE_LINE;
    }
    __asm__ __volatile__("dsb sy" ::: "memory");
#else
    (void)base;
    (void)bytes;
#endif
}

const char *myos_a8_fb_status_name(myos_a8_fb_status_t status)
{
    switch (status) {
    case MYOS_A8_FB_OK:          return "ok";
    case MYOS_A8_FB_ERR_NULL:    return "null";
    case MYOS_A8_FB_ERR_BOOTARGS:return "bootargs";
    case MYOS_A8_FB_ERR_VIDEO:   return "video";
    case MYOS_A8_FB_ERR_GEOMETRY:return "geometry";
    case MYOS_A8_FB_ERR_STRIDE:  return "stride";
    case MYOS_A8_FB_ERR_RANGE:   return "range";
    case MYOS_A8_FB_ERR_RENDER:  return "render";
    default:                     return "invalid";
    }
}

myos_a8_fb_status_t myos_a8_video_read(uint64_t boot_args,
                                       myos_a8_video_t *video)
{
    if (video == NULL) {
        return MYOS_A8_FB_ERR_NULL;
    }

    video->base = 0;
    video->display = 0;
    video->row_bytes = 0;
    video->width = 0;
    video->height = 0;
    video->depth = 0;

    /* 64-bit loads from a packed struct require an 8-byte-aligned base; the
     * adapter refuses rather than issuing a potentially faulting unaligned
     * Device-memory access. Also refuse a base so close to the end of the
     * address space that the last Video word read would wrap. */
    if (boot_args == 0u || (boot_args & 7u) != 0u ||
        boot_args > (uint64_t)UINTPTR_MAX -
                    (uint64_t)MYOS_A8_BOOTARGS_READ_LAST) {
        return MYOS_A8_FB_ERR_BOOTARGS;
    }

    video->base = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_BASE);
    video->display = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_DISPLAY);
    video->row_bytes = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_ROWBYTES);
    video->width = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_WIDTH);
    video->height = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_HEIGHT);
    video->depth = read_u64(boot_args + MYOS_A8_BOOTARGS_VIDEO_DEPTH);
    return MYOS_A8_FB_OK;
}

myos_a8_fb_status_t myos_a8_fb_from_boot_args(uint64_t boot_args,
                                               myos_fb_t *fb)
{
    myos_a8_fb_status_t status;
    myos_a8_video_t video;
    uint64_t min_stride;
    uint64_t required;

    if (fb == NULL) {
        return MYOS_A8_FB_ERR_NULL;
    }

    /* Zero the descriptor on every failure path so a caller that ignores the
     * status cannot accidentally render with stale fields. */
    fb->base = NULL;
    fb->width = 0;
    fb->height = 0;
    fb->stride = 0;
    fb->bpp = 0;
    fb->format = MYOS_FB_FMT_NONE;
    fb->bytes = 0;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }

    if (video.base == 0u || (video.base & 3u) != 0u) {
        return MYOS_A8_FB_ERR_VIDEO;
    }

    if (video.width == 0u || video.height == 0u ||
        video.width > MYOS_FB_MAX_DIM || video.height > MYOS_FB_MAX_DIM) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }

    min_stride = video.width * 4u;
    if (video.row_bytes < min_stride ||
        (video.row_bytes & 3u) != 0u ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return MYOS_A8_FB_ERR_STRIDE;
    }

    required = video.row_bytes * video.height;
    if (required > MYOS_FB_MAX_BYTES ||
        (video.row_bytes != 0u &&
         required / video.row_bytes != video.height)) {
        return MYOS_A8_FB_ERR_RANGE;
    }

    fb->base = (volatile uint8_t *)(uintptr_t)video.base;
    fb->width = (uint32_t)video.width;
    fb->height = (uint32_t)video.height;
    fb->stride = (uint32_t)video.row_bytes;
    fb->bpp = 4u;
    fb->format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    fb->bytes = required;

    if (myos_fb_validate(fb) != MYOS_FB_OK) {
        fb->base = NULL;
        fb->width = 0;
        fb->height = 0;
        fb->stride = 0;
        fb->bpp = 0;
        fb->format = MYOS_FB_FMT_NONE;
        fb->bytes = 0;
        return MYOS_A8_FB_ERR_RENDER;
    }
    return MYOS_A8_FB_OK;
}

static const char myos_a8_hello_text[]
    __attribute__((section(".rodata.early"))) = "Hello User";

myos_a8_fb_status_t myos_a8_fb_draw_hello(uint64_t boot_args,
                                          uint64_t *pixels_written)
{
    myos_fb_t fb;
    myos_a8_fb_status_t status;
    myos_fb_status_t text_status;
    uint32_t scale = 3u;
    uint32_t text_width;
    uint32_t text_height;
    int32_t x;
    int32_t y;
    uint64_t count = 0;
    uint64_t total;
    volatile uint32_t *raw;

    if (pixels_written != NULL) {
        *pixels_written = 0;
    }

    status = myos_a8_fb_from_boot_args(boot_args, &fb);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }

    text_width = myos_fb_text_width(myos_a8_hello_text, scale);
    text_height = MYOS_FB_FONT_HEIGHT * scale;
    if (text_width == 0u || text_width > fb.width || text_height > fb.height) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }

    raw = (volatile uint32_t *)(uintptr_t)fb.base;
    total = (uint64_t)(fb.stride / 4u) * (uint64_t)fb.height;
    for (uint64_t i = 0; i < total; i++) {
        raw[i] = MYOS_FB_BLACK;
    }
    myos_a8_fb_clean_dcache(fb.base, fb.bytes);

    x = (int32_t)((fb.width - text_width) / 2u);
    y = (int32_t)((fb.height - text_height) / 2u);

    text_status = myos_fb_draw_text(&fb, x, y, myos_a8_hello_text, scale,
                                    &count);
    if (text_status != MYOS_FB_OK) {
        return MYOS_A8_FB_ERR_RENDER;
    }
    myos_a8_fb_clean_dcache(fb.base, fb.bytes);

    if (pixels_written != NULL) {
        *pixels_written = count;
    }
    return MYOS_A8_FB_OK;
}

#if defined(MYOS_A8_RED_TEST) && MYOS_A8_RED_TEST
myos_a8_fb_status_t myos_a8_fb_draw_minimal_red(uint64_t boot_args)
{
    myos_a8_video_t video;
    myos_a8_fb_status_t status;
    volatile uint32_t *fb;
    uint64_t required;
    uint64_t pixels;
    uint64_t i;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }
    if (video.base == 0u || (video.base & 3u) != 0u) {
        return MYOS_A8_FB_ERR_VIDEO;
    }
    if (video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return MYOS_A8_FB_ERR_STRIDE;
    }
    if (video.height == 0u || video.height > MYOS_FB_MAX_DIM) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }
    if (video.height > MYOS_FB_MAX_BYTES / video.row_bytes) {
        return MYOS_A8_FB_ERR_RANGE;
    }
    required = video.row_bytes * video.height;
    if (video.base > (uint64_t)UINTPTR_MAX - (required - 1u)) {
        return MYOS_A8_FB_ERR_RANGE;
    }

    fb = (volatile uint32_t *)(uintptr_t)video.base;
    pixels = required / 4u;
    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFFFF0000u;
    }
    return MYOS_A8_FB_OK;
}
#endif

#if defined(MYOS_A8_DIAGNOSTIC) && MYOS_A8_DIAGNOSTIC
myos_a8_fb_status_t myos_a8_fb_draw_diagnostic(uint64_t boot_args)
{
    myos_a8_video_t video;
    volatile uint32_t *fb;
    uint64_t required;
    uint64_t pixels;
    uint64_t i;
    myos_fb_t myfb;
    uint32_t text_width;
    uint32_t text_height;
    int32_t x;
    int32_t y;
    myos_a8_fb_status_t status;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }
    if (video.base == 0u || (video.base & 3u) != 0u) {
        return MYOS_A8_FB_ERR_VIDEO;
    }
    if (video.width == 0u || video.height == 0u ||
        video.width > MYOS_FB_MAX_DIM || video.height > MYOS_FB_MAX_DIM) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }
    if (video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return MYOS_A8_FB_ERR_STRIDE;
    }
    if (video.height > MYOS_FB_MAX_BYTES / video.row_bytes) {
        return MYOS_A8_FB_ERR_RANGE;
    }
    required = video.row_bytes * video.height;
    if (video.base > (uint64_t)UINTPTR_MAX - (required - 1u)) {
        return MYOS_A8_FB_ERR_RANGE;
    }
    pixels = required / 4u;
    fb = (volatile uint32_t *)(uintptr_t)video.base;

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFFFF0000u;
    }
    for (volatile uint32_t d = 0u; d < 50000000u; d++) {
    }

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFF00FF00u;
    }
    for (volatile uint32_t d = 0u; d < 50000000u; d++) {
    }

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFF0000FFu;
    }
    for (volatile uint32_t d = 0u; d < 50000000u; d++) {
    }

    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }

    myfb.base = (volatile uint8_t *)(uintptr_t)video.base;
    myfb.width = (uint32_t)video.width;
    myfb.height = (uint32_t)video.height;
    myfb.stride = (uint32_t)video.row_bytes;
    myfb.bpp = 4u;
    myfb.format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    myfb.bytes = required;

    text_width = myos_fb_text_width("Hello User", 4u);
    text_height = MYOS_FB_FONT_HEIGHT * 4u;
    if (text_width == 0u || text_width > myfb.width ||
        text_height > myfb.height) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }
    x = (int32_t)((myfb.width - text_width) / 2u);
    y = (int32_t)((myfb.height - text_height) / 2u);

    if (myos_fb_validate(&myfb) != MYOS_FB_OK) {
        return MYOS_A8_FB_ERR_RENDER;
    }
    if (myos_fb_draw_text(&myfb, x, y, "Hello User", 4u, NULL) != MYOS_FB_OK) {
        return MYOS_A8_FB_ERR_RENDER;
    }
    return MYOS_A8_FB_OK;
}
#endif

#if defined(MYOS_A8_DIAGNOSTIC_V2) && MYOS_A8_DIAGNOSTIC_V2
static void diag_delay(uint32_t loops)
{
    for (volatile uint32_t d = 0u; d < loops; d++) {
    }
}

static void diag_fill_box(volatile uint32_t *fb, uint32_t stride_px,
                          uint32_t height, int32_t x, int32_t y,
                          int32_t width, int32_t box_height, uint32_t color)
{
    int32_t x0;
    int32_t y0;
    int32_t x1;
    int32_t y1;
    int32_t yy;
    int32_t xx;

    if (fb == NULL || stride_px == 0u || height == 0u ||
        width <= 0 || box_height <= 0) {
        return;
    }
    x0 = x;
    y0 = y;
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    x1 = x + width;
    y1 = y + box_height;
    if (x1 > (int32_t)stride_px) {
        x1 = (int32_t)stride_px;
    }
    if (y1 > (int32_t)height) {
        y1 = (int32_t)height;
    }
    if (x1 <= x0 || y1 <= y0) {
        return;
    }
    for (yy = y0; yy < y1; yy++) {
        for (xx = x0; xx < x1; xx++) {
            fb[(uint32_t)yy * stride_px + (uint32_t)xx] = color;
        }
    }
}

static uint32_t diag_status_color(myos_fb_status_t status)
{
    switch (status) {
    case MYOS_FB_OK:            return 0xFF00FFFFu;
    case MYOS_FB_ERR_GLYPH:     return 0xFFFFFF00u;
    case MYOS_FB_ERR_GEOMETRY:  return 0xFFFF00FFu;
    case MYOS_FB_ERR_FORMAT:    return 0xFFFF8800u;
    case MYOS_FB_ERR_STRIDE:    return 0xFF00FF00u;
    case MYOS_FB_ERR_ALIGNMENT: return 0xFF0000FFu;
    case MYOS_FB_ERR_RANGE:     return 0xFF808080u;
    case MYOS_FB_ERR_NULL:      return 0xFFFF88AAu;
    default:                    return 0xFFFFFFFFu;
    }
}

myos_a8_fb_status_t myos_a8_fb_draw_diagnostic_v2(uint64_t boot_args)
{
    myos_a8_video_t video;
    volatile uint32_t *fb;
    uint64_t required;
    uint64_t pixels;
    uint64_t i;
    myos_fb_t myfb;
    uint32_t stride_px;
    uint32_t width32;
    uint32_t height32;
    uint32_t text_width;
    uint32_t text_height;
    int32_t tx;
    int32_t ty;
    int position_ok;
    int32_t box_x;
    int32_t box_y;
    int32_t box_width;
    int32_t box_height;
    uint64_t pw;
    myos_fb_status_t ret;
    uint32_t ret_color;
    uint32_t pw_color;
    myos_a8_fb_status_t status;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }
    if (video.base == 0u || (video.base & 3u) != 0u) {
        return MYOS_A8_FB_ERR_VIDEO;
    }
    if (video.height == 0u || video.height > MYOS_FB_MAX_DIM) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }
    if (video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return MYOS_A8_FB_ERR_STRIDE;
    }
    if (video.height > MYOS_FB_MAX_BYTES / video.row_bytes) {
        return MYOS_A8_FB_ERR_RANGE;
    }
    required = video.row_bytes * video.height;
    if (video.base > (uint64_t)UINTPTR_MAX - (required - 1u)) {
        return MYOS_A8_FB_ERR_RANGE;
    }

    fb = (volatile uint32_t *)(uintptr_t)video.base;
    stride_px = (uint32_t)(video.row_bytes / 4u);
    height32 = (uint32_t)video.height;
    width32 = (video.width > (uint64_t)UINT32_MAX)
                  ? 0u
                  : (uint32_t)video.width;
    pixels = required / 4u;

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFFFF0000u;
    }
    diag_delay(25000000u);

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFF00FF00u;
    }
    diag_delay(25000000u);

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFF0000FFu;
    }
    diag_delay(25000000u);

    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }

    text_width = myos_fb_text_width("Hello User", 2u);
    text_height = MYOS_FB_FONT_HEIGHT * 2u;
    tx = 0;
    ty = 0;
    position_ok = 0;
    if (width32 > 0u && text_width > 0u && text_width <= width32 &&
        text_height <= height32) {
        tx = (int32_t)((width32 - text_width) / 2u);
        ty = (int32_t)((height32 - text_height) / 2u);
        if (tx >= 0 && ty >= 0 &&
            (uint64_t)(uint32_t)tx + text_width <= width32 &&
            (uint64_t)(uint32_t)ty + text_height <= height32 &&
            (uint64_t)(uint32_t)tx + text_width <= stride_px) {
            position_ok = 1;
        }
    }

    if (position_ok) {
        box_x = tx;
        box_y = ty;
        box_width = (int32_t)text_width;
        box_height = (int32_t)text_height;
    } else {
        box_x = 20;
        box_y = 100;
        box_width = 200;
        box_height = 30;
    }
    diag_fill_box(fb, stride_px, height32, box_x, box_y,
                  box_width, box_height, MYOS_FB_WHITE);
    diag_delay(100000000u);

    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }

    myfb.base = (volatile uint8_t *)(uintptr_t)video.base;
    myfb.width = width32;
    myfb.height = height32;
    myfb.stride = (uint32_t)video.row_bytes;
    myfb.bpp = 4u;
    myfb.format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    myfb.bytes = required;

    pw = 0u;
    ret = myos_fb_draw_text(&myfb, tx, ty, "Hello User", 2u, &pw);

    ret_color = diag_status_color(ret);
    pw_color = (pw > 0u) ? 0xFF00FFFFu : 0xFFFF0000u;

    diag_fill_box(fb, stride_px, height32, 20, 20, 60, 60, ret_color);
    diag_fill_box(fb, stride_px, height32, 100, 20, 60, 60, pw_color);

    return MYOS_A8_FB_OK;
}
#endif

#if defined(MYOS_A8_DIAGNOSTIC_V3) && MYOS_A8_DIAGNOSTIC_V3
static void diag3_delay(uint32_t loops)
{
    for (volatile uint32_t d = 0u; d < loops; d++) {
    }
}

static void diag3_fill_box(volatile uint32_t *fb, uint32_t stride_px,
                           uint32_t height, int32_t x, int32_t y,
                           int32_t width, int32_t box_height, uint32_t color)
{
    int32_t x0;
    int32_t y0;
    int32_t x1;
    int32_t y1;
    int32_t yy;
    int32_t xx;

    if (fb == NULL || stride_px == 0u || height == 0u ||
        width <= 0 || box_height <= 0) {
        return;
    }
    x0 = x;
    y0 = y;
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    x1 = x + width;
    y1 = y + box_height;
    if (x1 > (int32_t)stride_px) {
        x1 = (int32_t)stride_px;
    }
    if (y1 > (int32_t)height) {
        y1 = (int32_t)height;
    }
    if (x1 <= x0 || y1 <= y0) {
        return;
    }
    for (yy = y0; yy < y1; yy++) {
        for (xx = x0; xx < x1; xx++) {
            fb[(uint32_t)yy * stride_px + (uint32_t)xx] = color;
        }
    }
}

static uint32_t diag3_status_color(myos_fb_status_t status)
{
    switch (status) {
    case MYOS_FB_OK:            return 0xFF00FFFFu;
    case MYOS_FB_ERR_GLYPH:     return 0xFFFFFF00u;
    case MYOS_FB_ERR_GEOMETRY:  return 0xFFFF00FFu;
    case MYOS_FB_ERR_FORMAT:    return 0xFFFF8800u;
    case MYOS_FB_ERR_STRIDE:    return 0xFF00FF00u;
    case MYOS_FB_ERR_ALIGNMENT: return 0xFF0000FFu;
    case MYOS_FB_ERR_RANGE:     return 0xFF808080u;
    case MYOS_FB_ERR_NULL:      return 0xFFFF88AAu;
    default:                    return 0xFFFFFFFFu;
    }
}

myos_a8_fb_status_t myos_a8_fb_draw_diagnostic_v3(uint64_t boot_args)
{
    myos_a8_video_t video;
    volatile uint32_t *fb;
    uint64_t required;
    uint64_t pixels;
    uint64_t i;
    myos_fb_t myfb;
    uint32_t stride_px;
    uint32_t width32;
    uint32_t height32;
    uint32_t tx;
    uint32_t ty;
    uint32_t text_width;
    uint32_t text_height;
    uint32_t text_tx;
    uint32_t text_ty;
    int32_t ind_x;
    int32_t ind_step;
    uint64_t pw;
    uint64_t pw2;
    myos_fb_status_t ret;
    myos_fb_status_t ret2;
    uint32_t ret_color;
    uint32_t pw_color;
    myos_a8_fb_status_t status;

    status = myos_a8_video_read(boot_args, &video);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }
    if (video.base == 0u || (video.base & 3u) != 0u) {
        return MYOS_A8_FB_ERR_VIDEO;
    }
    if (video.height == 0u || video.height > MYOS_FB_MAX_DIM) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }
    if (video.row_bytes == 0u || (video.row_bytes & 3u) != 0u ||
        video.row_bytes > MYOS_FB_MAX_STRIDE) {
        return MYOS_A8_FB_ERR_STRIDE;
    }
    if (video.height > MYOS_FB_MAX_BYTES / video.row_bytes) {
        return MYOS_A8_FB_ERR_RANGE;
    }
    required = video.row_bytes * video.height;
    if (video.base > (uint64_t)UINTPTR_MAX - (required - 1u)) {
        return MYOS_A8_FB_ERR_RANGE;
    }

    fb = (volatile uint32_t *)(uintptr_t)video.base;
    stride_px = (uint32_t)(video.row_bytes / 4u);
    height32 = (uint32_t)video.height;
    width32 = (video.width > (uint64_t)UINT32_MAX)
                  ? 0u
                  : (uint32_t)video.width;
    pixels = required / 4u;

    for (i = 0u; i < pixels; i++) {
        fb[i] = 0xFFFF0000u;
    }
    diag3_delay(10000000u);
    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }

    if (width32 >= 20u && height32 >= 20u) {
        tx = (width32 - 20u) / 2u;
        ty = (height32 - 20u) / 2u;
    } else {
        tx = 20u;
        ty = 100u;
    }
    diag3_fill_box(fb, stride_px, height32, (int32_t)tx, (int32_t)ty,
                   20, 20, MYOS_FB_WHITE);
    diag3_delay(100000000u);
    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }
    diag3_delay(25000000u);

    diag3_fill_box(fb, stride_px, height32, (int32_t)(tx + 8u),
                   (int32_t)(ty + 8u), 4, 4, MYOS_FB_WHITE);
    diag3_delay(100000000u);
    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }
    diag3_delay(25000000u);

    myfb.base = (volatile uint8_t *)(uintptr_t)video.base;
    myfb.width = width32;
    myfb.height = height32;
    myfb.stride = (uint32_t)video.row_bytes;
    myfb.bpp = 4u;
    myfb.format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    myfb.bytes = required;

    diag3_fill_box(fb, stride_px, height32, 10, 200, 40, 40,
                   0xFFFF0000u);
    diag3_delay(100000000u);

    pw = 0u;
    ret = myos_fb_draw_text(&myfb, 200, 200, "H", 2u, &pw);

    for (i = 0u; i < pixels; i++) {
        fb[i] = MYOS_FB_BLACK;
    }

    if (width32 >= 660u) {
        ind_x = 400;
        ind_step = 100;
    } else {
        ind_x = 10;
        ind_step = 70;
    }
    diag3_fill_box(fb, stride_px, height32, ind_x, 200, 60, 60,
                   0xFF00FFFFu);
    ret_color = diag3_status_color(ret);
    diag3_fill_box(fb, stride_px, height32, ind_x + ind_step, 200, 60, 60,
                   ret_color);
    pw_color = (pw > 0u) ? 0xFF00FFFFu : 0xFFFF0000u;
    diag3_fill_box(fb, stride_px, height32, ind_x + 2 * ind_step, 200,
                   60, 60, pw_color);
    diag3_delay(100000000u);

    if (ret == MYOS_FB_OK) {
        text_width = myos_fb_text_width("Hello User", 2u);
        text_height = MYOS_FB_FONT_HEIGHT * 2u;
        if (width32 >= text_width && height32 >= text_height) {
            text_tx = (width32 - text_width) / 2u;
            text_ty = (height32 - text_height) / 2u;
        } else {
            text_tx = 0u;
            text_ty = 0u;
        }
        for (i = 0u; i < pixels; i++) {
            fb[i] = MYOS_FB_BLACK;
        }
        pw2 = 0u;
        ret2 = myos_fb_draw_text(&myfb, (int32_t)text_tx,
                                 (int32_t)text_ty, "Hello User", 2u, &pw2);
        if (ret2 == MYOS_FB_OK) {
            diag3_fill_box(fb, stride_px, height32, 10, 300, 40, 40,
                           0xFF00FF00u);
        } else {
            diag3_fill_box(fb, stride_px, height32, 10, 300, 40, 40,
                           0xFFFF0000u);
        }
    }

    return MYOS_A8_FB_OK;
}
#endif
