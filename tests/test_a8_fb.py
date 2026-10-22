#!/usr/bin/env python3
"""Host tests for platform/apple/a8/a8_fb.c (offline, no hardware).

Compiles the real adapter + renderer with the host compiler and drives them
with a synthetic packed boot_args image and a caller-owned framebuffer.

Covers:
  * Video offsets re-derived from the pinned PongoOS pongo.h (compile-time
    static asserts against the actual struct text);
  * accepted geometry and field extraction (including opaque v_display/
    v_depth, which are recorded but deliberately not decoded);
  * rejection of NULL/misaligned boot_args, zero/misaligned base, zero/
    oversized width/height, short/misaligned/oversized stride, and
    stride*height range overflow;
  * failure paths zero the output descriptor;
  * "Hello User" is centred, scale 3, white on a full-screen black background;
  * a too-small or invalid framebuffer draws nothing;
  * canary bytes outside the declared framebuffer are never touched.

No device, no MMIO, no QEMU. Skipped with NOTE if no host C compiler exists.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PONGO_H = os.path.join("pongoos-src", "src", "kernel", "pongo.h")

DRIVER = r"""
#include "platform/apple/a8/a8_fb.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GUARD 64u
#define FBW 400u
#define FBH 240u
#define FB_STRIDE (FBW * 4u)
#define FB_BYTES ((uint64_t)FB_STRIDE * FBH)

static unsigned char fb_mem[GUARD + FB_BYTES + GUARD] __attribute__((aligned(16)));
static unsigned char bootargs[0x100] __attribute__((aligned(8)));
static int failures;

#define CHECK(name, cond) do { \
    if (cond) { printf("PASS %s\n", name); } \
    else { printf("FAIL %s\n", name); failures++; } \
} while (0)

_Static_assert(MYOS_A8_BOOTARGS_VIDEO_BASE == 0x28u, "base offset");
_Static_assert(MYOS_A8_BOOTARGS_VIDEO_DISPLAY == 0x30u, "display offset");
_Static_assert(MYOS_A8_BOOTARGS_VIDEO_ROWBYTES == 0x38u, "rowBytes offset");
_Static_assert(MYOS_A8_BOOTARGS_VIDEO_WIDTH == 0x40u, "width offset");
_Static_assert(MYOS_A8_BOOTARGS_VIDEO_HEIGHT == 0x48u, "height offset");
_Static_assert(MYOS_A8_BOOTARGS_VIDEO_DEPTH == 0x50u, "depth offset");

static void put64(uint32_t off, uint64_t value)
{
    *(uint64_t *)(void *)(bootargs + off) = value;
}

static void set_video(uint64_t base, uint64_t width, uint64_t height,
                      uint64_t stride, uint64_t depth, uint64_t display)
{
    memset(bootargs, 0, sizeof(bootargs));
    put64(MYOS_A8_BOOTARGS_VIDEO_BASE, base);
    put64(MYOS_A8_BOOTARGS_VIDEO_DISPLAY, display);
    put64(MYOS_A8_BOOTARGS_VIDEO_ROWBYTES, stride);
    put64(MYOS_A8_BOOTARGS_VIDEO_WIDTH, width);
    put64(MYOS_A8_BOOTARGS_VIDEO_HEIGHT, height);
    put64(MYOS_A8_BOOTARGS_VIDEO_DEPTH, depth);
}

static void reset_fb(void)
{
    memset(fb_mem, 0xA5, sizeof(fb_mem));
    memset(fb_mem + GUARD, 0x33, FB_BYTES);
    memset(fb_mem + GUARD + FB_BYTES, 0x5A, GUARD);
}

static int guards_ok(void)
{
    for (uint32_t i = 0; i < GUARD; i++) {
        if (fb_mem[i] != 0xA5u) return 0;
        if (fb_mem[GUARD + FB_BYTES + i] != 0x5Au) return 0;
    }
    return 1;
}

static int fb_region_is(uint8_t value)
{
    for (uint64_t i = 0; i < FB_BYTES; i++) {
        if (fb_mem[GUARD + i] != value) return 0;
    }
    return 1;
}

static uint32_t px(uint32_t x, uint32_t y)
{
    return *(const volatile uint32_t *)(fb_mem + GUARD +
        (uint64_t)y * FB_STRIDE + (uint64_t)x * 4u);
}

static void expect_zero(const myos_fb_t *fb)
{
    if (fb->base != NULL || fb->width != 0u || fb->height != 0u ||
        fb->stride != 0u || fb->bpp != 0u || fb->bytes != 0u ||
        fb->format != MYOS_FB_FMT_NONE) {
        printf("FAIL failure path did not zero the descriptor\n");
        failures++;
    }
}

static void test_video_read(void)
{
    myos_a8_video_t video;
    uint64_t base = (uint64_t)(uintptr_t)(fb_mem + GUARD);

    set_video(base, 400, 240, 1600, 32, 1);
    CHECK("video read accepted",
          myos_a8_video_read((uint64_t)(uintptr_t)bootargs, &video) == MYOS_A8_FB_OK);
    CHECK("video base extracted", video.base == base);
    CHECK("video width extracted", video.width == 400u);
    CHECK("video height extracted", video.height == 240u);
    CHECK("video stride extracted", video.row_bytes == 1600u);
    CHECK("video depth recorded", video.depth == 32u);
    CHECK("video display recorded", video.display == 1u);

    CHECK("NULL video output rejected",
          myos_a8_video_read((uint64_t)(uintptr_t)bootargs, NULL) == MYOS_A8_FB_ERR_NULL);
    CHECK("NULL boot_args rejected",
          myos_a8_video_read(0, &video) == MYOS_A8_FB_ERR_BOOTARGS);
    CHECK("unaligned boot_args rejected",
          myos_a8_video_read((uint64_t)(uintptr_t)bootargs + 1u, &video) == MYOS_A8_FB_ERR_BOOTARGS);
    CHECK("4-byte-only boot_args rejected",
          myos_a8_video_read((uint64_t)(uintptr_t)bootargs + 4u, &video) == MYOS_A8_FB_ERR_BOOTARGS);
    CHECK("address-wrapping boot_args rejected",
          myos_a8_video_read((uint64_t)UINTPTR_MAX - 7u, &video) == MYOS_A8_FB_ERR_BOOTARGS);
}

static void test_from_boot_args(void)
{
    myos_fb_t fb;
    uint64_t base = (uint64_t)(uintptr_t)(fb_mem + GUARD);
    uint64_t ba = (uint64_t)(uintptr_t)bootargs;

    reset_fb();
    set_video(base, 400, 240, 1600, 32, 1);
    CHECK("valid adapter accepted", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_OK);
    CHECK("adapter base", fb.base == (volatile uint8_t *)(fb_mem + GUARD));
    CHECK("adapter width", fb.width == 400u);
    CHECK("adapter height", fb.height == 240u);
    CHECK("adapter stride", fb.stride == 1600u);
    CHECK("adapter bpp", fb.bpp == 4u);
    CHECK("adapter format",
          fb.format == MYOS_FB_FMT_32BPP_ORDER_AGNOSTIC);
    CHECK("adapter bytes", fb.bytes == FB_BYTES);

    CHECK("NULL descriptor rejected", myos_a8_fb_from_boot_args(ba, NULL) == MYOS_A8_FB_ERR_NULL);
    CHECK("NULL boot_args rejected", myos_a8_fb_from_boot_args(0, &fb) == MYOS_A8_FB_ERR_BOOTARGS);
    expect_zero(&fb);

    set_video(0, 400, 240, 1600, 32, 1);
    CHECK("NULL video base rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_VIDEO);
    expect_zero(&fb);

    set_video(base + 1u, 400, 240, 1600, 32, 1);
    CHECK("misaligned video base rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_VIDEO);
    expect_zero(&fb);

    set_video(base, 0, 240, 1600, 32, 1);
    CHECK("zero width rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_GEOMETRY);
    set_video(base, 400, 0, 1600, 32, 1);
    CHECK("zero height rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_GEOMETRY);
    set_video(base, MYOS_FB_MAX_DIM + 1u, 240, 1600, 32, 1);
    CHECK("oversized width rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_GEOMETRY);
    set_video(base, 400, MYOS_FB_MAX_DIM + 1u, 1600, 32, 1);
    CHECK("oversized height rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_GEOMETRY);

    set_video(base, 400, 240, 1596, 32, 1);
    CHECK("short stride rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_STRIDE);
    set_video(base, 400, 240, 1602, 32, 1);
    CHECK("misaligned stride rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_STRIDE);
    set_video(base, 1, 1, MYOS_FB_MAX_STRIDE + 4u, 32, 1);
    CHECK("oversized stride rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_STRIDE);

    set_video(base, 1, 8192, MYOS_FB_MAX_STRIDE, 32, 1);
    CHECK("stride*height range rejected", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_ERR_RANGE);
    expect_zero(&fb);

    /* v_depth is documented by pongo.h as "Pixel Depth and other
     * parameters"; the adapter must not invent a decoding. All values are
     * recorded, none is gated on. */
    set_video(base, 400, 240, 1600, 0, 1);
    CHECK("opaque depth 0 not decoded", myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_OK);
    set_video(base, 400, 240, 1600, 24, 0);
    CHECK("opaque depth 24/display 0 not decoded",
          myos_a8_fb_from_boot_args(ba, &fb) == MYOS_A8_FB_OK);
}

static void test_draw_hello(void)
{
    uint64_t base = (uint64_t)(uintptr_t)(fb_mem + GUARD);
    uint64_t ba = (uint64_t)(uintptr_t)bootargs;
    uint64_t pixels = 999;

    reset_fb();
    set_video(base, 400, 240, 1600, 32, 1);
    CHECK("draw hello accepted",
          myos_a8_fb_draw_hello(ba, &pixels) == MYOS_A8_FB_OK);
    CHECK("draw hello wrote pixels", pixels > 0u);
    CHECK("draw hello full-string pixel count", pixels == 1494u);
    CHECK("draw hello centring row blank", px(80, 108) == MYOS_FB_BLACK);
    CHECK("draw hello glyph H white", px(83, 111) == MYOS_FB_WHITE);
    CHECK("draw hello scale-3 block", px(85, 113) == MYOS_FB_WHITE);
    CHECK("draw hello space cell black", px(200, 108) == MYOS_FB_BLACK);
    CHECK("draw hello full-screen background black", px(0, 0) == MYOS_FB_BLACK);
    CHECK("draw hello bottom-right black", px(399, 239) == MYOS_FB_BLACK);
    CHECK("draw hello guards intact", guards_ok());
    CHECK("draw hello glyph top margin black", px(83, 108) == MYOS_FB_BLACK);

    reset_fb();
    set_video(base, 100, 20, 400, 32, 1);
    pixels = 999;
    CHECK("too-small framebuffer rejected",
          myos_a8_fb_draw_hello(ba, &pixels) == MYOS_A8_FB_ERR_GEOMETRY);
    CHECK("too-small framebuffer reports zero", pixels == 0u);
    CHECK("too-small framebuffer wrote nothing", fb_region_is(0x33u));
    CHECK("too-small framebuffer guards intact", guards_ok());

    reset_fb();
    set_video(base, 400, 240, 1600, 32, 1);
    pixels = 999;
    CHECK("NULL boot_args draw rejected",
          myos_a8_fb_draw_hello(0, &pixels) == MYOS_A8_FB_ERR_BOOTARGS);
    CHECK("NULL boot_args reports zero", pixels == 0u);
    CHECK("NULL boot_args wrote nothing", fb_region_is(0x33u));

    CHECK("status name ok", strcmp(myos_a8_fb_status_name(MYOS_A8_FB_OK), "ok") == 0);
    CHECK("status name invalid",
          strcmp(myos_a8_fb_status_name((myos_a8_fb_status_t)99), "invalid") == 0);
}

int main(void)
{
    test_video_read();
    test_from_boot_args();
    test_draw_hello();

    if (failures == 0) {
        printf("a8_fb host tests: ALL PASS\n");
        return 0;
    }
    printf("a8_fb host tests: FAIL (%d failures)\n", failures);
    return 1;
}
"""


def pongo_offset_crosscheck(cc, tmp):
    """Compile the real struct text from pongo.h and assert our offsets."""
    path = os.path.join(ROOT, PONGO_H)
    with open(path, encoding="utf-8") as f:
        text = f.read()

    video = re.search(r"struct Boot_Video\s*\{(.*?)\};", text, re.S)
    assert video, "pongo.h: struct Boot_Video not found"

    start = text.index("typedef struct boot_args {")
    end = text.index("struct Boot_Video Video;", start)
    end += len("struct Boot_Video Video;")
    body = text[start + len("typedef struct boot_args {"):end]

    source = """#include <stdint.h>
struct Boot_Video {%s};
typedef struct boot_args {%s
} __attribute__((packed)) boot_args;
_Static_assert(sizeof(struct Boot_Video) == 48, "Boot_Video size");
_Static_assert(__builtin_offsetof(boot_args, Video.v_baseAddr) == 0x28, "v_baseAddr");
_Static_assert(__builtin_offsetof(boot_args, Video.v_display) == 0x30, "v_display");
_Static_assert(__builtin_offsetof(boot_args, Video.v_rowBytes) == 0x38, "v_rowBytes");
_Static_assert(__builtin_offsetof(boot_args, Video.v_width) == 0x40, "v_width");
_Static_assert(__builtin_offsetof(boot_args, Video.v_height) == 0x48, "v_height");
_Static_assert(__builtin_offsetof(boot_args, Video.v_depth) == 0x50, "v_depth");
int main(void) { return 0; }
""" % (video.group(1), body)

    src = os.path.join(tmp, "pongo_offsets.c")
    exe = os.path.join(tmp, "pongo_offsets")
    with open(src, "w", encoding="utf-8") as f:
        f.write(source)
    build = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            src, "-o", exe],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
    if build.returncode != 0:
        print(build.stdout)
        print("FAIL pongo.h Video-offset cross-check")
        return False
    print("PASS pongo.h Video offsets match the adapter constants")
    return True


def main():
    cc = None
    for candidate in ("cc", "gcc"):
        if shutil.which(candidate):
            cc = candidate
            break
    if cc is None:
        print("NOTE: no host C compiler found; skipping a8_fb host tests")
        return 0

    with tempfile.TemporaryDirectory(prefix="myos-a8-fb-") as tmp:
        if os.path.isfile(os.path.join(ROOT, PONGO_H)):
            if not pongo_offset_crosscheck(cc, tmp):
                return 1
        else:
            print("NOTE: pongoos-src not present; skipping pongo.h offset "
                  "cross-check")

        src = os.path.join(tmp, "driver.c")
        exe = os.path.join(tmp, "driver")
        with open(src, "w", encoding="utf-8") as f:
            f.write(DRIVER)
        cmd = [
            cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
            "-I", ROOT,
            os.path.join(ROOT, "kernel", "fb_text.c"),
            os.path.join(ROOT, "platform", "apple", "a8", "a8_fb.c"),
            src, "-o", exe,
        ]
        build = subprocess.run(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        if build.returncode != 0:
            print(build.stdout)
            print("FAIL a8_fb host build")
            return 1
        run = subprocess.run([exe], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        print(run.stdout, end="")
        return run.returncode


if __name__ == "__main__":
    sys.exit(main())
