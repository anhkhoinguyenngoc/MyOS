/*
 * MyOS M2 Kernel Image Format
 * Canonical specification for host tool, PongoOS loader, and image builder.
 * All fields are little-endian, fixed-width, packed.
 */

#ifndef MYOS_IMAGE_H
#define MYOS_IMAGE_H

#include <stdint.h>

#define MYOS_IMAGE_MAGIC      0x4E52454B534F594DULL  /* "MYOSKERN" */
#define MYOS_IMAGE_VERSION    1
#define MYOS_ARCH_AARCH64     0
#define MYOS_MAX_SEGMENTS     16
#define MYOS_PAGE_SIZE        0x4000               /* 16 KB page size */

/* Permission bits (match PongoOS PROT_* where applicable) */
#define MYOS_PROT_READ        1
#define MYOS_PROT_WRITE       2
#define MYOS_PROT_EXEC        4

/* Header: exactly 64 bytes */
typedef struct __attribute__((packed)) {
    uint64_t magic;           /* 0x00: MYOS_IMAGE_MAGIC */
    uint32_t version;         /* 0x08: MYOS_IMAGE_VERSION */
    uint32_t arch;            /* 0x0C: MYOS_ARCH_AARCH64 */
    uint64_t load_addr;       /* 0x10: target physical load address (MYOS_PAGE_SIZE aligned) */
    uint64_t entry_offset;    /* 0x18: entry point offset from load_addr */
    uint64_t image_size;      /* 0x20: total file size including header + segment table + payloads */
    uint64_t bss_size;        /* 0x28: zero-initialized BSS size AFTER image_size (not in file) */
    uint32_t segment_count;   /* 0x30: number of loadable segments (<= MYOS_MAX_SEGMENTS) */
    uint32_t flags;           /* 0x34: reserved, must be 0 */
    uint64_t reserved;        /* 0x38: must be 0 */
} myos_image_hdr_t;

/* Segment entry: exactly 48 bytes */
typedef struct __attribute__((packed)) {
    uint64_t p_paddr;         /* target physical address (load_addr + p_offset) */
    uint64_t p_offset;        /* offset in file from start of header */
    uint64_t p_filesz;        /* size in file (0 for pure BSS segment) */
    uint64_t p_memsz;         /* size in memory (>= p_filesz) */
    uint32_t p_prot;          /* MYOS_PROT_READ | MYOS_PROT_WRITE | MYOS_PROT_EXEC */
    uint32_t p_align;         /* alignment (power of 2, >= MYOS_PAGE_SIZE) */
    uint64_t reserved;        /* must be 0 */
} myos_segment_t;

/* Compile-time size assertions */
#define MYOS_IMAGE_HDR_SIZE   (sizeof(myos_image_hdr_t))   /* 64 */
#define MYOS_SEGMENT_SIZE     (sizeof(myos_segment_t))     /* 48 */

/* File layout:
 *
 * [0x00 .. 0x3F]       myos_image_hdr_t (64 bytes)
 * [0x40 .. 0x40+48*N)  myos_segment_t[segment_count]
 * [after segments .. image_size)  segment payloads (file data)
 * [image_size .. image_size+bss_size)  zero-filled BSS (NOT in file)
 *
 * Enforced invariants (validated by loader and host validator):
 *   1. magic == MYOS_IMAGE_MAGIC
 *   2. version == MYOS_IMAGE_VERSION
 *   3. arch == MYOS_ARCH_AARCH64
 *   4. segment_count <= MYOS_MAX_SEGMENTS
 *   5. load_addr % MYOS_PAGE_SIZE == 0
 *   6. entry_offset < image_size
 *   7. image_size <= received_bytes (upload buffer capacity)
 *   8. header_size + segment_count * segment_size <= image_size
 *   9. For each segment i:
 *        a) p_offset + p_filesz <= image_size
 *        b) p_filesz <= p_memsz
 *        c) p_paddr == load_addr + p_offset   (identity file-to-memory mapping)
 *        d) p_align is power of 2, p_align >= MYOS_PAGE_SIZE
 *        e) p_paddr % p_align == 0
 *        f) p_paddr + p_memsz <= load_addr + image_size + bss_size
 *        g) No overlapping destination ranges with any other segment
 *        h) No overlapping file payload ranges with any other segment
 *   10. Entry point (load_addr + entry_offset) lies within an executable segment
 *        (segment with p_prot & MYOS_PROT_EXEC) and within its p_memsz.
 *   11. BSS region [image_size, image_size + bss_size) is zeroed by loader.
 *   12. All reserved fields and flags must be 0.
 *   13. Unknown permission bits in p_prot cause rejection.
 */

#endif /* MYOS_IMAGE_H */