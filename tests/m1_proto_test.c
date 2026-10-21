/*
 * tests/m1_proto_test.c — host unit tests for tools/host/m1_proto.h.
 *
 * Offline, device-free: this exercises ONLY the pure wire-format helpers
 * (little-endian encode/decode, payload loading, exec-block construction).
 * It never touches libusb, USB, DFU, or any device.
 *
 * The expected bytes below are written out by hand (not produced by the code
 * under test), so an endianness or offset regression fails here.
 *
 * Build (from the repo root):  gcc -O2 -Wall -Wextra -I. -o build/test_m1_proto tests/m1_proto_test.c
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "tools/host/m1_proto.h"

static int g_pass = 0;
static int g_fail = 0;

static void check(const char *name, int ok) {
    if (ok) {
        printf("PASS %s\n", name);
        g_pass++;
    } else {
        printf("FAIL %s\n", name);
        g_fail++;
    }
}

/* Reference magic/pc/args for the builder test. */
#define T_MAGIC     0x6578656365786563ULL   /* "cexecexe" */
#define T_ENTRY     0x0000000180380050ULL
#define T_IMG_BASE  0x0000000180380000ULL
#define T_CHALLENGE 0x1122334455667788ULL

static void test_le_roundtrip(void) {
    uint8_t b[8];
    m1_wr64le(b, 0x0102030405060708ULL);
    const uint8_t want[8] = {0x08,0x07,0x06,0x05,0x04,0x03,0x02,0x01};
    check("wr64le emits little-endian bytes", memcmp(b, want, 8) == 0);
    check("rd64le inverts wr64le", m1_rd64le(b) == 0x0102030405060708ULL);

    /* unaligned pointer must still work (byte-wise implementation) */
    uint8_t u[9];
    memcpy(u + 1, b, 8);
    check("rd64le handles unaligned pointer", m1_rd64le(u + 1) == 0x0102030405060708ULL);
}

static void test_builder_bytes(void) {
    const uint8_t payload[4] = {0xAA, 0xBB, 0xCC, 0xDD};
    uint8_t block[0x100];
    memset(block, 0xEE, sizeof(block));

    size_t n = m1_build_exec_block(block, sizeof(block), T_MAGIC, T_ENTRY,
                                   T_IMG_BASE, T_CHALLENGE, payload, sizeof(payload));
    check("builder returns 0x50 + payload_len", n == 0x50u + 4u);

    /* Header words, little-endian, hand-written. */
    const uint8_t want_header[0x50] = {
        /* +0x00 magic "cexecexe" */ 0x63,0x65,0x78,0x65,0x63,0x65,0x78,0x65,
        /* +0x08 pc  0x180380050 */ 0x50,0x00,0x38,0x80,0x01,0x00,0x00,0x00,
        /* +0x10 x0  0x180380000 */ 0x00,0x00,0x38,0x80,0x01,0x00,0x00,0x00,
        /* +0x18 x1 = challenge  */ 0x88,0x77,0x66,0x55,0x44,0x33,0x22,0x11,
        /* +0x20..+0x48 x2..x7   */ 0,0,0,0,0,0,0,0,
                                    0,0,0,0,0,0,0,0,
                                    0,0,0,0,0,0,0,0,
                                    0,0,0,0,0,0,0,0,
                                    0,0,0,0,0,0,0,0,
                                    0,0,0,0,0,0,0,0
    };
    check("builder header bytes exact",
          memcmp(block, want_header, sizeof(want_header)) == 0);
    check("builder appends payload verbatim",
          memcmp(block + 0x50, payload, 4) == 0);
}

static void test_builder_limits(void) {
    uint8_t block[0x60];
    uint8_t payload[0x10];
    memset(payload, 0, sizeof(payload));

    check("builder rejects block smaller than header",
          m1_build_exec_block(block, 0x4f, T_MAGIC, T_ENTRY, T_IMG_BASE,
                              T_CHALLENGE, NULL, 0) == 0);
    check("builder accepts empty payload at exact header size",
          m1_build_exec_block(block, 0x50, T_MAGIC, T_ENTRY, T_IMG_BASE,
                              T_CHALLENGE, NULL, 0) == 0x50);
    check("builder accepts payload that exactly fills the block",
          m1_build_exec_block(block, sizeof(block), T_MAGIC, T_ENTRY,
                              T_IMG_BASE, T_CHALLENGE, payload,
                              sizeof(block) - 0x50) == sizeof(block));
    check("builder rejects payload one byte too large",
          m1_build_exec_block(block, sizeof(block), T_MAGIC, T_ENTRY,
                              T_IMG_BASE, T_CHALLENGE, payload,
                              sizeof(block) - 0x50 + 1) == 0);
    check("builder rejects NULL block",
          m1_build_exec_block(NULL, sizeof(block), T_MAGIC, T_ENTRY, T_IMG_BASE,
                              T_CHALLENGE, payload, 1) == 0);
    check("builder rejects non-empty payload with NULL pointer",
          m1_build_exec_block(block, sizeof(block), T_MAGIC, T_ENTRY, T_IMG_BASE,
                              T_CHALLENGE, NULL, 1) == 0);
}

static void write_file(const char *path, const uint8_t *data, size_t n) {
    FILE *f = fopen(path, "wb");
    if (f == NULL) {
        perror("fopen");
        exit(2);
    }
    if (n != 0 && fwrite(data, 1, n, f) != n) {
        perror("fwrite");
        exit(2);
    }
    fclose(f);
}

static void test_loader(void) {
    char tmpl[] = "/tmp/m1_proto_XXXXXX";
    int fd = mkstemp(tmpl);
    check("mkstemp for loader test", fd >= 0);
    if (fd < 0) {
        return;
    }
    close(fd);

    uint8_t out[0x20];
    size_t n = 0;

    /* empty file */
    write_file(tmpl, NULL, 0);
    check("loader rejects empty file",
          m1_load_payload(tmpl, out, sizeof(out), &n) == M1_PAYLOAD_ERR_EMPTY);

    /* 4-byte file */
    const uint8_t four[4] = {0x01, 0x02, 0x03, 0x04};
    write_file(tmpl, four, 4);
    check("loader accepts 4-byte file",
          m1_load_payload(tmpl, out, sizeof(out), &n) == M1_PAYLOAD_OK && n == 4);
    check("loader copies bytes verbatim", memcmp(out, four, 4) == 0);

    /* exactly cap */
    uint8_t big[0x20];
    for (size_t i = 0; i < sizeof(big); i++) big[i] = (uint8_t)i;
    write_file(tmpl, big, sizeof(big));
    check("loader accepts file of exactly cap bytes",
          m1_load_payload(tmpl, out, sizeof(out), &n) == M1_PAYLOAD_OK &&
          n == sizeof(out));

    /* cap + 1 (must be rejected, NOT truncated) */
    uint8_t big1[0x21];
    memcpy(big1, big, sizeof(big));
    big1[0x20] = 0xFF;
    write_file(tmpl, big1, sizeof(big1));
    check("loader rejects file one byte over cap (no silent truncation)",
          m1_load_payload(tmpl, out, sizeof(out), &n) ==
          M1_PAYLOAD_ERR_TOO_LARGE);

    /* missing file */
    check("loader reports open error for missing file",
          m1_load_payload("/tmp/m1_proto_does_not_exist_$$",
                          out, sizeof(out), &n) == M1_PAYLOAD_ERR_OPEN);

    unlink(tmpl);
}

int main(void) {
    test_le_roundtrip();
    test_builder_bytes();
    test_builder_limits();
    test_loader();

    printf("\nM1_PROTO_TEST: %s (%d passed, %d failed)\n",
           g_fail == 0 ? "PASS" : "FAIL", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
