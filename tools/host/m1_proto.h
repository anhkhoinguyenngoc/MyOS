/*
 * tools/host/m1_proto.h — M1 pwned-DFU command-block wire format (host side).
 *
 * target: host (x86_64). Header-only, freestanding, no libusb, no device.
 *
 * The device-side handler (gaster's payload_handle_checkm8_request.S) reads a
 * command block out of the DFU image buffer as 64-bit little-endian words:
 *
 *   +0x00  u64  magic        (EXEC_MAGIC or MEMC_MAGIC)
 *   +0x08  u64  pc           (EXEC) / unused, 0 (MEMC)
 *   +0x10  u64  x0
 *   +0x18  u64  x1
 *   +0x20  u64  x2
 *   ... up to x7 at +0x48     (80-byte header total)
 *   +0x50  ...               payload (EXEC only)
 *
 * These helpers exist so the host encodes/decodes that layout explicitly,
 * without reading or writing a byte buffer through a wider pointer type. That
 * removes the strict-aliasing and unaligned-access UB the previous m1ctl used,
 * and makes the little-endian byte order an explicit, testable property rather
 * than a host-endianness assumption. tests/m1_proto_test.c checks the bytes.
 */
#ifndef MYOS_M1_PROTO_H
#define MYOS_M1_PROTO_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

/* Size of the exec/memc command header (must match M1_EXEC_HEADER_LEN). */
#define M1_PROTO_HEADER_LEN 0x50u

/* Little-endian 64-bit load/store over an unaligned byte pointer. */
static inline uint64_t m1_rd64le(const uint8_t *p) {
    return (uint64_t)p[0]
         | ((uint64_t)p[1] << 8)
         | ((uint64_t)p[2] << 16)
         | ((uint64_t)p[3] << 24)
         | ((uint64_t)p[4] << 32)
         | ((uint64_t)p[5] << 40)
         | ((uint64_t)p[6] << 48)
         | ((uint64_t)p[7] << 56);
}

static inline void m1_wr64le(uint8_t *p, uint64_t v) {
    p[0] = (uint8_t)(v      );
    p[1] = (uint8_t)(v >>  8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
    p[4] = (uint8_t)(v >> 32);
    p[5] = (uint8_t)(v >> 40);
    p[6] = (uint8_t)(v >> 48);
    p[7] = (uint8_t)(v >> 56);
}

enum m1_payload_status {
    M1_PAYLOAD_OK = 0,
    M1_PAYLOAD_ERR_OPEN,       /* file could not be opened                     */
    M1_PAYLOAD_ERR_EMPTY,      /* file is empty                                */
    M1_PAYLOAD_ERR_TOO_LARGE,  /* file does not fit in `cap` (rejected)        */
    M1_PAYLOAD_ERR_READ,       /* read error while loading                     */
};

/*
 * Load at most `cap` payload bytes from `path` into `dst`.
 *
 * A file larger than `cap` is REJECTED (M1_PAYLOAD_ERR_TOO_LARGE); it is never
 * silently truncated. On success writes the byte count to *out_len.
 */
static inline enum m1_payload_status
m1_load_payload(const char *path, uint8_t *dst, size_t cap, size_t *out_len) {
    if (path == NULL || dst == NULL || out_len == NULL || cap == 0) {
        return M1_PAYLOAD_ERR_READ;
    }
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        return M1_PAYLOAD_ERR_OPEN;
    }
    size_t n = fread(dst, 1, cap, f);
    if (ferror(f)) {
        fclose(f);
        return M1_PAYLOAD_ERR_READ;
    }
    if (n == 0) {
        fclose(f);
        return M1_PAYLOAD_ERR_EMPTY;
    }
    if (n == cap) {
        /* Reached the limit: distinguish "exactly cap" from "too large". */
        int extra = fgetc(f);
        if (extra != EOF) {
            fclose(f);
            return M1_PAYLOAD_ERR_TOO_LARGE;
        }
        if (ferror(f)) {
            fclose(f);
            return M1_PAYLOAD_ERR_READ;
        }
    }
    fclose(f);
    *out_len = n;
    return M1_PAYLOAD_OK;
}

/*
 * Build an EXEC command block: [80-byte header][payload].
 *
 * Writes header words (magic at +0x00, pc at +0x08, x0..x7 at +0x10..+0x48)
 * and copies `payload_len` bytes to +0x50. x0 = img_base, x1 = challenge,
 * x2..x7 = 0. Returns the total block length, or 0 if the block does not fit
 * in `block_cap` (including the case block_cap < header size).
 */
static inline size_t
m1_build_exec_block(uint8_t *block, size_t block_cap, uint64_t magic,
                    uint64_t entry, uint64_t img_base, uint64_t challenge,
                    const uint8_t *payload, size_t payload_len) {
    if (block == NULL) {
        return 0;
    }
    if (block_cap < M1_PROTO_HEADER_LEN) {
        return 0;
    }
    if (payload_len > block_cap - M1_PROTO_HEADER_LEN) {
        return 0;
    }
    if (payload_len != 0 && payload == NULL) {
        return 0;
    }

    m1_wr64le(block + 0x00, magic);
    m1_wr64le(block + 0x08, entry);      /* pc                              */
    m1_wr64le(block + 0x10, img_base);   /* x0                              */
    m1_wr64le(block + 0x18, challenge);  /* x1                              */
    m1_wr64le(block + 0x20, 0);          /* x2                              */
    m1_wr64le(block + 0x28, 0);          /* x3                              */
    m1_wr64le(block + 0x30, 0);          /* x4                              */
    m1_wr64le(block + 0x38, 0);          /* x5                              */
    m1_wr64le(block + 0x40, 0);          /* x6                              */
    m1_wr64le(block + 0x48, 0);          /* x7                              */

    for (size_t i = 0; i < payload_len; i++) {
        block[M1_PROTO_HEADER_LEN + i] = payload[i];
    }
    return M1_PROTO_HEADER_LEN + payload_len;
}

#endif /* MYOS_M1_PROTO_H */
