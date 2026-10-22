#!/usr/bin/env python3
"""Host tests for kernel/fb_text.c (offline, no hardware).

Compiles the real renderer with the host compiler and runs it against
caller-owned buffers. Covers:
  * font coverage for every glyph in "Hello User" and fail-closed lookup;
  * exact pixel placement for the hand-authored 8x8 glyphs, scale 1 and 2;
  * clipping at all four edges, fully-outside draws, and INT32 extremes;
  * invalid metadata (NULL, format, geometry, stride, alignment, bytes);
  * stride padding never written;
  * canary bytes before/after the declared framebuffer never touched;
  * whole-string glyph validation before any pixel is written;
  * fill_rect clipping and non-positive sizes.

No device, no MMIO, no QEMU. The test compiles with the system C compiler; if
none is available it reports NOTE and exits 0 (repository convention for
host-side toolchain tolerance).
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = r"""
#include "kernel/fb_text.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GUARD 64u
#define FB_CAP 200000u
static unsigned char mem[GUARD + FB_CAP + GUARD] __attribute__((aligned(16)));
static int failures;

#define CHECK(name, cond) do { \
    if (cond) { printf("PASS %s\n", name); } \
    else { printf("FAIL %s\n", name); failures++; } \
} while (0)

static int guards_ok(uint64_t bytes)
{
    uint32_t i;
    for (i = 0; i < GUARD; i++) {
        if (mem[i] != 0xA5u) return 0;
    }
    for (i = 0; i < GUARD; i++) {
        if (mem[GUARD + bytes + i] != 0x5Au) return 0;
    }
    return 1;
}

static void reset_fb(myos_fb_t *fb, uint32_t w, uint32_t h, uint32_t stride)
{
    memset(mem, 0xA5, sizeof(mem));
    memset(mem + GUARD, 0, (size_t)stride * h);
    memset(mem + GUARD + (size_t)stride * h, 0x5A, GUARD);
    fb->base = mem + GUARD;
    fb->width = w;
    fb->height = h;
    fb->stride = stride;
    fb->bpp = 4u;
    fb->format = MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC;
    fb->bytes = (uint64_t)stride * h;
}

static uint32_t px(const myos_fb_t *fb, uint32_t x, uint32_t y)
{
    return *(const volatile uint32_t *)(fb->base +
        (uint64_t)y * fb->stride + (uint64_t)x * 4u);
}

static int all_pixels(const myos_fb_t *fb, uint32_t value)
{
    for (uint32_t y = 0; y < fb->height; y++) {
        for (uint32_t x = 0; x < fb->width; x++) {
            if (px(fb, x, y) != value) return 0;
        }
    }
    return 1;
}

static int padding_zero(const myos_fb_t *fb, uint32_t first_visible_px)
{
    for (uint32_t y = 0; y < fb->height; y++) {
        const unsigned char *row = (const unsigned char *)fb->base +
            (uint64_t)y * fb->stride;
        for (uint32_t b = first_visible_px * 4u; b < fb->stride; b++) {
            if (row[b] != 0u) return 0;
        }
    }
    return 1;
}

static void test_font(void)
{
    const char *needed = " HeloUsr";
    int all = 1;
    for (size_t i = 0; needed[i] != '\0'; i++) {
        if (!myos_fb_font_has(needed[i])) all = 0;
    }
    CHECK("font contains every unique glyph of Hello User", all);
    CHECK("font rejects unsupported 'A'", !myos_fb_font_has('A'));
    CHECK("font rejects unsupported '0'", !myos_fb_font_has('0'));
    CHECK("font rejects newline", !myos_fb_font_has('\n'));
    CHECK("font contains space", myos_fb_font_has(' '));
}

static void test_validate(void)
{
    myos_fb_t fb;
    reset_fb(&fb, 20, 10, 80);
    CHECK("valid descriptor accepted", myos_fb_validate(&fb) == MYOS_FB_OK);
    CHECK("status name ok", strcmp(myos_fb_status_name(MYOS_FB_OK), "ok") == 0);
    CHECK("status name invalid fallback",
          strcmp(myos_fb_status_name((myos_fb_status_t)99), "invalid") == 0);
    CHECK("NULL descriptor rejected", myos_fb_validate(NULL) == MYOS_FB_ERR_NULL);

    fb.base = NULL;
    CHECK("NULL base rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_NULL);
    reset_fb(&fb, 20, 10, 80);

    fb.bpp = 2; CHECK("2bpp rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_FORMAT);
    fb.bpp = 8; CHECK("8bpp rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_FORMAT);
    fb.bpp = 4; fb.format = MYOS_FB_FMT_NONE;
    CHECK("format NONE rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_FORMAT);
    fb.format = (myos_fb_format_t)99;
    CHECK("unknown format rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_FORMAT);
    reset_fb(&fb, 20, 10, 80);

    fb.width = 0; CHECK("zero width rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_GEOMETRY);
    fb.width = 20; fb.height = 0;
    CHECK("zero height rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_GEOMETRY);
    fb.height = 10; fb.width = MYOS_FB_MAX_DIM + 1u;
    CHECK("oversized width rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_GEOMETRY);
    fb.width = 20; fb.height = MYOS_FB_MAX_DIM + 1u;
    CHECK("oversized height rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_GEOMETRY);
    reset_fb(&fb, 20, 10, 80);

    fb.stride = 76; CHECK("stride below width*4 rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_STRIDE);
    fb.stride = 82; CHECK("non-multiple-of-4 stride rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_STRIDE);
    fb.stride = MYOS_FB_MAX_STRIDE + 4u;
    CHECK("oversized stride rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_STRIDE);
    reset_fb(&fb, 4, 4, 16);

    fb.bytes = 63; CHECK("bytes below stride*height rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_RANGE);
    fb.bytes = 64; CHECK("exact bytes accepted", myos_fb_validate(&fb) == MYOS_FB_OK);

    fb.base = mem + GUARD + 1;
    CHECK("misaligned base rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_ALIGNMENT);

    fb.base = (volatile uint8_t *)(uintptr_t)(UINTPTR_MAX - 15u);
    fb.width = 4; fb.height = 4; fb.stride = 16; fb.bytes = 64;
    CHECK("address-space wrap rejected", myos_fb_validate(&fb) == MYOS_FB_ERR_RANGE);
}

static void test_text_width(void)
{
    CHECK("text width scale 1", myos_fb_text_width("Hello User", 1) == 80u);
    CHECK("text width scale 2", myos_fb_text_width("Hello User", 2) == 160u);
    CHECK("text width scale 4", myos_fb_text_width("Hello User", 4) == 320u);
    CHECK("empty text width is zero", myos_fb_text_width("", 4) == 0u);
    CHECK("NULL text width is zero", myos_fb_text_width(NULL, 4) == 0u);
    CHECK("scale 0 width is zero", myos_fb_text_width("H", 0) == 0u);
    CHECK("scale too large width is zero",
          myos_fb_text_width("H", MYOS_FB_MAX_SCALE + 1u) == 0u);
}

static void test_glyph_rendering(void)
{
    myos_fb_t fb;
    uint64_t n = 0;

    reset_fb(&fb, 20, 10, 80);
    CHECK("black fill", myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("draw H scale 1",
          myos_fb_draw_text(&fb, 0, 0, "H", 1, &n) == MYOS_FB_OK);
    CHECK("H pixel count", n == 26u);
    CHECK("H row1 bit1 white", px(&fb, 1, 1) == MYOS_FB_WHITE);
    CHECK("H row1 bit2 white", px(&fb, 2, 1) == MYOS_FB_WHITE);
    CHECK("H row1 bit5 white", px(&fb, 5, 1) == MYOS_FB_WHITE);
    CHECK("H row1 bit6 white", px(&fb, 6, 1) == MYOS_FB_WHITE);
    CHECK("H row1 left margin black", px(&fb, 0, 1) == MYOS_FB_BLACK);
    CHECK("H row1 gap black", px(&fb, 3, 1) == MYOS_FB_BLACK);
    CHECK("H row1 right margin black", px(&fb, 7, 1) == MYOS_FB_BLACK);
    CHECK("H crossbar left edge", px(&fb, 1, 4) == MYOS_FB_WHITE);
    CHECK("H crossbar right edge", px(&fb, 6, 4) == MYOS_FB_WHITE);
    CHECK("H crossbar outside-left", px(&fb, 0, 4) == MYOS_FB_BLACK);
    CHECK("H blank top row", px(&fb, 1, 0) == MYOS_FB_BLACK);
    CHECK("H pixels do not spill to next row", px(&fb, 1, 7) == MYOS_FB_BLACK);
    CHECK("glyph guards intact", guards_ok(fb.bytes));
}

static void test_placement_and_scale(void)
{
    myos_fb_t fb;
    uint64_t n = 0;

    reset_fb(&fb, 20, 10, 80);
    CHECK("fill for placement",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("draw H at (3,2)",
          myos_fb_draw_text(&fb, 3, 2, "H", 1, &n) == MYOS_FB_OK && n == 26u);
    CHECK("placement bit1 pixel", px(&fb, 4, 3) == MYOS_FB_WHITE);
    CHECK("placement bit5 pixel", px(&fb, 8, 3) == MYOS_FB_WHITE);
    CHECK("placement left of glyph untouched", px(&fb, 3, 3) == MYOS_FB_BLACK);
    CHECK("placement right of glyph untouched", px(&fb, 11, 3) == MYOS_FB_BLACK);
    CHECK("placement crossbar", px(&fb, 4, 6) == MYOS_FB_WHITE);
    CHECK("placement guards intact", guards_ok(fb.bytes));

    reset_fb(&fb, 40, 20, 160);
    CHECK("fill for scale 2",
          myos_fb_fill_rect(&fb, 0, 0, 40, 20, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("draw H scale 2",
          myos_fb_draw_text(&fb, 0, 0, "H", 2, &n) == MYOS_FB_OK && n == 104u);
    CHECK("scale2 top-left white", px(&fb, 2, 2) == MYOS_FB_WHITE);
    CHECK("scale2 block second pixel", px(&fb, 3, 3) == MYOS_FB_WHITE);
    CHECK("scale2 margin black", px(&fb, 0, 2) == MYOS_FB_BLACK);
    CHECK("scale2 gap black", px(&fb, 6, 2) == MYOS_FB_BLACK);
    CHECK("scale2 guards intact", guards_ok(fb.bytes));
}

static void test_clipping(void)
{
    myos_fb_t fb;
    uint64_t full = 0, clipped = 0, n = 0;

    reset_fb(&fb, 200, 50, 800);
    CHECK("fill full-size buffer",
          myos_fb_fill_rect(&fb, 0, 0, 200, 50, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("draw full Hello User",
          myos_fb_draw_text(&fb, 0, 0, "Hello User", 1, &full) == MYOS_FB_OK);
    CHECK("full text has foreground pixels", full == 166u);

    reset_fb(&fb, 20, 10, 80);
    CHECK("fill clipped buffer",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("draw clipped at negative origin",
          myos_fb_draw_text(&fb, -5, -5, "Hello User", 1, &clipped) == MYOS_FB_OK);
    CHECK("clipped draw wrote a subset", clipped > 0u && clipped < full);
    CHECK("clipped draw guards intact", guards_ok(fb.bytes));

    reset_fb(&fb, 20, 10, 80);
    CHECK("fill before fully-outside draw",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("fully-right draw is a no-op",
          myos_fb_draw_text(&fb, 1000, 0, "Hello User", 1, &n) == MYOS_FB_OK && n == 0u);
    CHECK("fully-below draw is a no-op",
          myos_fb_draw_text(&fb, 0, 1000, "Hello User", 1, &n) == MYOS_FB_OK && n == 0u);
    CHECK("fully-left draw is a no-op",
          myos_fb_draw_text(&fb, -1000, 0, "Hello User", 1, &n) == MYOS_FB_OK && n == 0u);
    CHECK("INT32_MAX origin is a no-op",
          myos_fb_draw_text(&fb, INT32_MAX, INT32_MAX, "Hello User", 1, &n) == MYOS_FB_OK && n == 0u);
    CHECK("INT32_MIN origin is a no-op",
          myos_fb_draw_text(&fb, INT32_MIN, INT32_MIN, "Hello User", 1, &n) == MYOS_FB_OK && n == 0u);
    CHECK("outside draws guards intact", guards_ok(fb.bytes));

    reset_fb(&fb, 20, 10, 80);
    CHECK("fill before edge draw",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("edge draw is clipped to six pixels",
          myos_fb_draw_text(&fb, 18, 1, "H", 1, &n) == MYOS_FB_OK && n == 6u);
    CHECK("edge pixel at 19,2", px(&fb, 19, 2) == MYOS_FB_WHITE);
    CHECK("blank top row at 19,1", px(&fb, 19, 1) == MYOS_FB_BLACK);
    CHECK("edge draw guards intact", guards_ok(fb.bytes));
}

static void test_invalid_draw(void)
{
    myos_fb_t fb;
    uint64_t n = 123u;
    char longtext[MYOS_FB_MAX_TEXT + 2];

    reset_fb(&fb, 20, 10, 80);
    CHECK("NULL text rejected", myos_fb_draw_text(&fb, 0, 0, NULL, 1, &n) == MYOS_FB_ERR_NULL);
    CHECK("NULL text reports zero", n == 0u);
    CHECK("empty text accepted", myos_fb_draw_text(&fb, 0, 0, "", 1, &n) == MYOS_FB_OK);
    CHECK("empty text reports zero", n == 0u);
    CHECK("scale 0 rejected", myos_fb_draw_text(&fb, 0, 0, "H", 0, &n) == MYOS_FB_ERR_GEOMETRY);
    CHECK("scale too large rejected",
          myos_fb_draw_text(&fb, 0, 0, "H", MYOS_FB_MAX_SCALE + 1u, &n) == MYOS_FB_ERR_GEOMETRY);

    CHECK("fill pattern",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, 0xDEADBEEFu, NULL) == MYOS_FB_OK);
    CHECK("unsupported glyph rejected",
          myos_fb_draw_text(&fb, 0, 0, "HeXlo", 1, &n) == MYOS_FB_ERR_GLYPH);
    CHECK("glyph failure reports zero", n == 0u);
    CHECK("glyph failure wrote nothing", all_pixels(&fb, 0xDEADBEEFu));
    CHECK("glyph failure guards intact", guards_ok(fb.bytes));

    memset(longtext, 'H', MYOS_FB_MAX_TEXT + 1u);
    longtext[MYOS_FB_MAX_TEXT + 1u] = '\0';
    CHECK("overlong text rejected",
          myos_fb_draw_text(&fb, 0, 0, longtext, 1, &n) == MYOS_FB_ERR_RANGE);
    CHECK("overlong text wrote nothing", all_pixels(&fb, 0xDEADBEEFu));
}

static void test_fill_rect(void)
{
    myos_fb_t fb;
    uint64_t n = 0;

    reset_fb(&fb, 20, 10, 80);
    CHECK("fill black",
          myos_fb_fill_rect(&fb, 0, 0, 20, 10, MYOS_FB_BLACK, NULL) == MYOS_FB_OK);
    CHECK("clipped white fill",
          myos_fb_fill_rect(&fb, -5, -5, 10, 10, MYOS_FB_WHITE, &n) == MYOS_FB_OK && n == 25u);
    CHECK("clipped fill corner", px(&fb, 0, 0) == MYOS_FB_WHITE);
    CHECK("clipped fill last pixel", px(&fb, 4, 4) == MYOS_FB_WHITE);
    CHECK("clipped fill right edge untouched", px(&fb, 5, 4) == MYOS_FB_BLACK);
    CHECK("clipped fill bottom edge untouched", px(&fb, 4, 5) == MYOS_FB_BLACK);
    CHECK("clipped fill guards intact", guards_ok(fb.bytes));

    CHECK("outside fill is a no-op",
          myos_fb_fill_rect(&fb, 100, 100, 5, 5, MYOS_FB_WHITE, &n) == MYOS_FB_OK && n == 0u);
    CHECK("zero-width fill rejected",
          myos_fb_fill_rect(&fb, 0, 0, 0, 5, MYOS_FB_WHITE, &n) == MYOS_FB_ERR_GEOMETRY);
    CHECK("negative-height fill rejected",
          myos_fb_fill_rect(&fb, 0, 0, 5, -1, MYOS_FB_WHITE, &n) == MYOS_FB_ERR_GEOMETRY);
    CHECK("INT32 extremes fill is a no-op",
          myos_fb_fill_rect(&fb, INT32_MIN, INT32_MIN, INT32_MAX, INT32_MAX,
                            MYOS_FB_WHITE, &n) == MYOS_FB_OK && n == 0u);
    CHECK("fill guards intact", guards_ok(fb.bytes));

    reset_fb(&fb, 4, 4, 16);
    fb.bytes = 63;
    CHECK("fill rejects short bytes",
          myos_fb_fill_rect(&fb, 0, 0, 4, 4, MYOS_FB_WHITE, &n) == MYOS_FB_ERR_RANGE);
    CHECK("short-bytes fill wrote nothing", all_pixels(&fb, 0u));
}

static void test_stride_padding(void)
{
    myos_fb_t fb;
    uint64_t n = 0;

    reset_fb(&fb, 20, 4, 96);
    CHECK("fill only visible width",
          myos_fb_fill_rect(&fb, 0, 0, 20, 4, MYOS_FB_WHITE, &n) == MYOS_FB_OK && n == 80u);
    CHECK("stride padding untouched by fill", padding_zero(&fb, 20));
    CHECK("visible pixels written", px(&fb, 19, 3) == MYOS_FB_WHITE);

    reset_fb(&fb, 20, 4, 96);
    CHECK("draw with padded stride",
          myos_fb_draw_text(&fb, 0, 0, "H", 1, &n) == MYOS_FB_OK && n == 12u);
    CHECK("padded draw visible pixel", px(&fb, 1, 3) == MYOS_FB_WHITE);
    CHECK("stride padding untouched by text", padding_zero(&fb, 20));
    CHECK("padded draw guards intact", guards_ok(fb.bytes));

    /* A glyph whose right edge crosses the visible width but not the stride:
     * clipping must use fb->width, never stride/4. The H at x=16 has 13
     * visible pixels; the padding at x=20..23 must remain zero. */
    reset_fb(&fb, 20, 8, 96);
    CHECK("right-edge glyph clipped at width",
          myos_fb_draw_text(&fb, 16, 0, "H", 1, &n) == MYOS_FB_OK && n == 13u);
    CHECK("right-edge glyph visible pixel", px(&fb, 18, 1) == MYOS_FB_WHITE);
    CHECK("right-edge glyph writes no stride padding",
          padding_zero(&fb, 20));
    CHECK("right-edge glyph guards intact", guards_ok(fb.bytes));
}

int main(void)
{
    test_font();
    test_validate();
    test_text_width();
    test_glyph_rendering();
    test_placement_and_scale();
    test_clipping();
    test_invalid_draw();
    test_fill_rect();
    test_stride_padding();

    if (failures == 0) {
        printf("fb_text host tests: ALL PASS\n");
        return 0;
    }
    printf("fb_text host tests: FAIL (%d failures)\n", failures);
    return 1;
}
"""


def main():
    cc = None
    for candidate in ("cc", "gcc"):
        if shutil.which(candidate):
            cc = candidate
            break
    if cc is None:
        print("NOTE: no host C compiler found; skipping fb_text host tests")
        return 0

    with tempfile.TemporaryDirectory(prefix="myos-fb-text-") as tmp:
        src = os.path.join(tmp, "driver.c")
        exe = os.path.join(tmp, "driver")
        with open(src, "w", encoding="utf-8") as f:
            f.write(DRIVER)
        cmd = [
            cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
            "-I", ROOT,
            os.path.join(ROOT, "kernel", "fb_text.c"),
            src, "-o", exe,
        ]
        build = subprocess.run(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        if build.returncode != 0:
            print(build.stdout)
            print("FAIL fb_text host build")
            return 1
        run = subprocess.run([exe], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        print(run.stdout, end="")
        return run.returncode


if __name__ == "__main__":
    sys.exit(main())
