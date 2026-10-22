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

static uint64_t read_u64(uint64_t address)
{
    return *(const volatile uint64_t *)(uintptr_t)address;
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

myos_a8_fb_status_t myos_a8_fb_draw_hello(uint64_t boot_args,
                                          uint64_t *pixels_written)
{
    myos_fb_t fb;
    myos_a8_fb_status_t status;
    uint32_t scale = 4u;
    uint32_t text_width;
    uint32_t text_height;
    int32_t x;
    int32_t y;

    if (pixels_written != NULL) {
        *pixels_written = 0;
    }

    status = myos_a8_fb_from_boot_args(boot_args, &fb);
    if (status != MYOS_A8_FB_OK) {
        return status;
    }

    text_width = myos_fb_text_width("Hello User", scale);
    text_height = MYOS_FB_FONT_HEIGHT * scale;
    if (text_width == 0u || text_width > fb.width || text_height > fb.height) {
        return MYOS_A8_FB_ERR_GEOMETRY;
    }

    x = (int32_t)((fb.width - text_width) / 2u);
    y = (int32_t)((fb.height - text_height) / 2u);

    status = (myos_a8_fb_status_t)myos_fb_fill_rect(
        &fb, x, y, (int32_t)text_width, (int32_t)text_height,
        MYOS_FB_BLACK, NULL);
    if (status != MYOS_A8_FB_OK) {
        return MYOS_A8_FB_ERR_RENDER;
    }

    if (myos_fb_draw_text(&fb, x, y, "Hello User", scale,
                          pixels_written) != MYOS_FB_OK) {
        return MYOS_A8_FB_ERR_RENDER;
    }
    return MYOS_A8_FB_OK;
}
