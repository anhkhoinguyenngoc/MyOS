#!/usr/bin/env python3
"""Host tests for kernel/fb_console.c and kernel/shell.c (offline)."""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = r"""
#include "kernel/fb_console.h"
#include "kernel/shell.h"
#include "platform/apple/a8/a8_fb.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define FB_MAX_W 400u
#define FB_MAX_H 300u

static uint32_t fb_mem[FB_MAX_W * FB_MAX_H] __attribute__((aligned(16)));
static uint64_t fake_base;
static uint64_t fake_width;
static uint64_t fake_height;
static uint64_t fake_row_bytes;
static myos_a8_fb_status_t fake_status;
static int failures;

#define CHECK(name, condition) do { \
    if (condition) { \
        printf("PASS %s\n", name); \
    } else { \
        printf("FAIL %s\n", name); \
        failures++; \
    } \
} while (0)

myos_a8_fb_status_t myos_a8_video_read(uint64_t boot_args,
                                       myos_a8_video_t *video)
{
    (void)boot_args;
    if (video == NULL) {
        return MYOS_A8_FB_ERR_NULL;
    }
    memset(video, 0, sizeof(*video));
    if (fake_status != MYOS_A8_FB_OK) {
        return fake_status;
    }
    video->base = fake_base;
    video->display = 0u;
    video->row_bytes = fake_row_bytes;
    video->width = fake_width;
    video->height = fake_height;
    video->depth = 32u;
    return MYOS_A8_FB_OK;
}

static void reset_video(uint32_t width, uint32_t height, uint32_t row_bytes)
{
    memset(fb_mem, 0xA5, sizeof(fb_mem));
    fake_base = (uint64_t)(uintptr_t)fb_mem;
    fake_width = width;
    fake_height = height;
    fake_row_bytes = row_bytes;
    fake_status = MYOS_A8_FB_OK;
}

static uint32_t px(uint32_t x, uint32_t y)
{
    return fb_mem[((uint64_t)y * fake_row_bytes + (uint64_t)x * 4u) / 4u];
}

static int all_visible_black(void)
{
    for (uint32_t y = 0; y < fake_height; y++) {
        for (uint32_t x = 0; x < fake_width; x++) {
            if (px(x, y) != MYOS_FB_BLACK) {
                return 0;
            }
        }
    }
    return 1;
}

static uint64_t white_count(void)
{
    uint64_t count = 0;

    for (uint32_t y = 0; y < fake_height; y++) {
        for (uint32_t x = 0; x < fake_width; x++) {
            if (px(x, y) == MYOS_FB_WHITE) {
                count++;
            }
        }
    }
    return count;
}

static int padding_untouched(void)
{
    const unsigned char *bytes = (const unsigned char *)fb_mem;

    for (uint32_t y = 0; y < fake_height; y++) {
        for (uint64_t b = fake_width * 4u; b < fake_row_bytes; b++) {
            if (bytes[(uint64_t)y * fake_row_bytes + b] != 0xA5u) {
                return 0;
            }
        }
    }
    return 1;
}

static void test_init_and_clear(void)
{
    uint32_t columns = 0u;
    uint32_t rows = 0u;

    reset_video(64u, 32u, 288u);
    CHECK("init accepts valid video", console_init(0u) == 0);
    console_get_size(&columns, &rows);
    CHECK("screen is four cells wide", columns == 4u);
    CHECK("screen is two cells tall", rows == 2u);

    console_clear();
    CHECK("clear makes every visible pixel black", all_visible_black());
    CHECK("clear leaves stride padding untouched", padding_untouched());

    reset_video(64u, 32u, 288u);
    fake_status = MYOS_A8_FB_ERR_VIDEO;
    CHECK("init propagates video read failure", console_init(0u) == -1);
    console_putc('H');
    CHECK("failed init leaves memory untouched", fb_mem[0] == 0xA5A5A5A5u);

    reset_video(8u, 8u, 32u);
    CHECK("init rejects zero columns", console_init(0u) == -4);
    reset_video(64u, 8u, 256u);
    CHECK("init rejects zero rows", console_init(0u) == -4);
    reset_video(64u, 32u, 252u);
    CHECK("init clamps stride below width", console_init(0u) == 0);
    console_get_size(&columns, &rows);
    CHECK("clamped screen is three cells wide", columns == 3u);
    CHECK("clamped screen is two cells tall", rows == 2u);
    console_clear();
    CHECK("clamped clear fills last visible pixel", px(62u, 0u) == MYOS_FB_BLACK);
    CHECK("clamped clear does not write past stride rows",
          fb_mem[2016] == 0xA5A5A5A5u);
}

static void test_glyph_and_cursor(void)
{
    uint32_t column = 99u;
    uint32_t row = 99u;

    reset_video(64u, 32u, 288u);
    CHECK("glyph init", console_init(0u) == 0);
    console_clear();
    console_putc('H');
    console_get_cursor(&column, &row);
    CHECK("glyph advances one column", column == 1u && row == 0u);
    CHECK("glyph top-left pixel is white", px(0u, 0u) == MYOS_FB_WHITE);
    CHECK("glyph top gap is black", px(4u, 0u) == MYOS_FB_BLACK);
    CHECK("glyph second row is white", px(0u, 2u) == MYOS_FB_WHITE);

    console_putc('\b');
    console_get_cursor(&column, &row);
    CHECK("backspace moves to previous cell", column == 0u && row == 0u);
    CHECK("backspace clears the cell", px(0u, 0u) == MYOS_FB_BLACK);
}

static void test_wrap_and_tab(void)
{
    uint32_t column = 99u;
    uint32_t row = 99u;

    reset_video(64u, 32u, 288u);
    CHECK("wrap init", console_init(0u) == 0);
    console_clear();
    for (uint32_t i = 0; i < 4u; i++) {
        console_putc('H');
    }
    console_get_cursor(&column, &row);
    CHECK("line wrap moves to next row", column == 0u && row == 1u);
    CHECK("last glyph before wrap is present", px(48u, 0u) == MYOS_FB_WHITE);

    console_clear();
    console_putc('\t');
    console_get_cursor(&column, &row);
    CHECK("tab is bounded by the line width", column == 0u && row == 1u);
}

static void test_scroll(void)
{
    uint32_t column = 99u;
    uint32_t row = 99u;

    reset_video(64u, 48u, 288u);
    CHECK("scroll init", console_init(0u) == 0);
    console_clear();
    console_putc('\n');
    console_putc('H');
    console_putc('\n');
    console_putc('X');
    console_scroll();
    console_get_cursor(&column, &row);
    CHECK("scroll parks cursor at last row", column == 0u && row == 2u);
    CHECK("scroll moves row one upward", px(0u, 0u) == MYOS_FB_WHITE);
    CHECK("scroll clears the new last row", px(0u, 32u) == MYOS_FB_BLACK);
}

static void test_shell(void)
{
    uint64_t before;

    reset_video(320u, 240u, 1280u);
    CHECK("shell init", console_init(0u) == 0);
    console_clear();

    before = white_count();
    shell_execute("echo Hello User");
    CHECK("shell echo writes pixels", white_count() > before);

    before = white_count();
    shell_execute("help");
    CHECK("shell help writes pixels", white_count() > before);

    before = white_count();
    shell_execute("version");
    CHECK("shell version writes pixels", white_count() > before);

    shell_execute("bogus");
    shell_execute("clear");
    CHECK("shell clear leaves a visible greeting", white_count() > 0u);

    before = white_count();
    shell_run();
    CHECK("shell demo writes pixels", white_count() > before);
}

int main(void)
{
    test_init_and_clear();
    test_glyph_and_cursor();
    test_wrap_and_tab();
    test_scroll();
    test_shell();

    if (failures != 0) {
        printf("FAILURES %d\n", failures);
        return 1;
    }
    printf("ALL PASS\n");
    return 0;
}
"""


def main() -> int:
    compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        print("NOTE: no host C compiler; skipping framebuffer console tests")
        return 0

    with tempfile.TemporaryDirectory(prefix="myos-fb-console-") as tmp:
        driver = os.path.join(tmp, "fb_console_test.c")
        binary = os.path.join(tmp, "fb_console_test")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(DRIVER)

        cmd = [
            compiler,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            ROOT,
            driver,
            os.path.join(ROOT, "kernel", "fb_console.c"),
            os.path.join(ROOT, "kernel", "shell.c"),
            "-o",
            binary,
        ]
        built = subprocess.run(cmd, text=True, capture_output=True)
        if built.returncode != 0:
            sys.stdout.write(built.stdout)
            sys.stderr.write(built.stderr)
            print("FAIL: host compile failed")
            return 1

        ran = subprocess.run([binary], text=True, capture_output=True)
        sys.stdout.write(ran.stdout)
        sys.stderr.write(ran.stderr)
        return ran.returncode


if __name__ == "__main__":
    sys.exit(main())
