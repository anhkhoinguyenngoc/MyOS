#!/usr/bin/env python3
"""
MyOS M2 Kernel Image Validator
Validates the canonical myos_image format.
"""

import sys
import struct
import os

# Import format constants from the shared header (replicated here for standalone use)
MYOS_IMAGE_MAGIC = 0x4E52454B534F594D  # "MYOSKERN"
MYOS_IMAGE_VERSION = 1
MYOS_ARCH_AARCH64 = 0
MYOS_MAX_SEGMENTS = 16
MYOS_PAGE_SIZE = 0x4000
MYOS_PROT_READ = 1
MYOS_PROT_WRITE = 2
MYOS_PROT_EXEC = 4

MYOS_IMAGE_HDR_SIZE = 64
MYOS_SEGMENT_SIZE = 48

# Header format: Q I I Q Q Q Q I I Q  (64 bytes)
HDR_FMT = '<QIIQQQQIIQ'
# Segment format: Q Q Q Q I I Q  (48 bytes)
SEG_FMT = '<QQQQIIQ'

VALID_PROT_BITS = MYOS_PROT_READ | MYOS_PROT_WRITE | MYOS_PROT_EXEC

def is_power_of_two(x):
    return x != 0 and (x & (x - 1)) == 0

def check_add_overflow(a, b, max_val=2**64 - 1):
    """Return (ok, sum) where ok is False if a + b overflows or exceeds max_val."""
    if a > max_val - b:
        return False, 0
    return True, a + b

def check_mul_overflow(a, b, max_val=2**64 - 1):
    """Return (ok, product) where ok is False if a * b overflows or exceeds max_val."""
    if a != 0 and b > max_val // a:
        return False, 0
    return True, a * b

def validate_image(data, verbose=False):
    """
    Validate a MyOS M2 kernel image.
    Returns (ok, message).
    """
    # 1. Minimum header size
    if len(data) < MYOS_IMAGE_HDR_SIZE:
        return False, f"file too small: {len(data)} bytes, need at least {MYOS_IMAGE_HDR_SIZE}"

    # 2. Parse header
    try:
        hdr = struct.unpack(HDR_FMT, data[:MYOS_IMAGE_HDR_SIZE])
    except struct.error as e:
        return False, f"header unpack failed: {e}"

    magic, version, arch, load_addr, entry_offset, image_size, bss_size, \
        segment_count, flags, *reserved = hdr

    # 3. Magic
    if magic != MYOS_IMAGE_MAGIC:
        return False, f"bad magic: 0x{magic:016x} (expected 0x{MYOS_IMAGE_MAGIC:016x})"

    # 4. Version
    if version != MYOS_IMAGE_VERSION:
        return False, f"unsupported version: {version} (expected {MYOS_IMAGE_VERSION})"

    # 5. Architecture
    if arch != MYOS_ARCH_AARCH64:
        return False, f"unsupported architecture: {arch} (expected {MYOS_ARCH_AARCH64})"

    # 6. Reserved fields and flags must be zero
    if flags != 0:
        return False, f"flags must be zero, got 0x{flags:08x}"
    for i, r in enumerate(reserved):
        if r != 0:
            return False, f"reserved[{i}] must be zero, got 0x{r:016x}"

    # 7. Segment count
    if segment_count > MYOS_MAX_SEGMENTS:
        return False, f"segment_count {segment_count} exceeds max {MYOS_MAX_SEGMENTS}"

    # 8. Load address alignment
    if load_addr & (MYOS_PAGE_SIZE - 1):
        return False, f"load_addr 0x{load_addr:x} not aligned to {MYOS_PAGE_SIZE}"

    # 9. Entry offset within image_size
    if entry_offset >= image_size:
        return False, f"entry_offset 0x{entry_offset:x} >= image_size 0x{image_size:x}"

    # 10. image_size does not exceed actual file size
    if image_size > len(data):
        return False, f"declared image_size 0x{image_size:x} > actual file size 0x{len(data):x}"

    # 11. Segment table bounds
    ok, seg_table_end = check_add_overflow(MYOS_IMAGE_HDR_SIZE, segment_count * MYOS_SEGMENT_SIZE)
    if not ok:
        return False, "segment table size overflow"
    if seg_table_end > image_size:
        return False, f"segment table extends past image_size: 0x{seg_table_end:x} > 0x{image_size:x}"

    # 12. Parse segments
    segments = []
    seg_data_start = MYOS_IMAGE_HDR_SIZE
    for i in range(segment_count):
        seg_start = seg_data_start + i * MYOS_SEGMENT_SIZE
        try:
            seg = struct.unpack(SEG_FMT, data[seg_start:seg_start + MYOS_SEGMENT_SIZE])
        except struct.error as e:
            return False, f"segment {i} unpack failed: {e}"
        segments.append(seg)

    # 13. Validate each segment
    prev_segs = []
    for i, seg in enumerate(segments):
        p_paddr, p_offset, p_filesz, p_memsz, p_prot, p_align, res = seg

        # Reserved field zero
        if res != 0:
            return False, f"segment {i}: reserved field must be zero"

        # Unknown permission bits
        if p_prot & ~VALID_PROT_BITS:
            return False, f"segment {i}: unknown permission bits 0x{p_prot & ~VALID_PROT_BITS:08x}"

        # Alignment: power of 2 and >= PAGE_SIZE
        if not is_power_of_two(p_align):
            return False, f"segment {i}: p_align 0x{p_align:x} not power of two"
        if p_align < MYOS_PAGE_SIZE:
            return False, f"segment {i}: p_align 0x{p_align:x} < MYOS_PAGE_SIZE 0x{MYOS_PAGE_SIZE:x}"

        # Destination alignment
        if p_paddr & (p_align - 1):
            return False, f"segment {i}: p_paddr 0x{p_paddr:x} not aligned to p_align 0x{p_align:x}"

        # Identity mapping: p_paddr == load_addr + p_offset
        ok, expected_paddr = check_add_overflow(load_addr, p_offset)
        if not ok:
            return False, f"segment {i}: load_addr + p_offset overflow"
        if p_paddr != expected_paddr:
            return False, f"segment {i}: p_paddr 0x{p_paddr:x} != load_addr + p_offset 0x{expected_paddr:x}"

        # filesz <= memsz
        if p_filesz > p_memsz:
            return False, f"segment {i}: p_filesz 0x{p_filesz:x} > p_memsz 0x{p_memsz:x}"

        # File bounds: p_offset + p_filesz <= image_size
        ok, file_end = check_add_overflow(p_offset, p_filesz)
        if not ok:
            return False, f"segment {i}: p_offset + p_filesz overflow"
        if file_end > image_size:
            return False, f"segment {i}: file data extends past image_size: 0x{file_end:x} > 0x{image_size:x}"

        # Memory bounds: p_paddr + p_memsz <= load_addr + image_size + bss_size
        ok, load_end = check_add_overflow(load_addr, image_size)
        if not ok:
            return False, "load_addr + image_size overflow"
        ok, mem_limit = check_add_overflow(load_end, bss_size)
        if not ok:
            return False, "load_addr + image_size + bss_size overflow"
        ok, seg_mem_end = check_add_overflow(p_paddr, p_memsz)
        if not ok:
            return False, f"segment {i}: p_paddr + p_memsz overflow"
        if seg_mem_end > mem_limit:
            return False, f"segment {i}: memory range exceeds image+bss limit: 0x{seg_mem_end:x} > 0x{mem_limit:x}"

        # Destination overlap check
        for j, (prev_paddr, prev_memsz, _po, _pf) in enumerate(prev_segs):
            if not (p_paddr + p_memsz <= prev_paddr or prev_paddr + prev_memsz <= p_paddr):
                return False, f"segment {i} destination overlaps segment {j}"

        # File payload overlap check
        for j, (_pp, _pm, prev_offset, prev_filesz) in enumerate(prev_segs):
            if not (p_offset + p_filesz <= prev_offset or prev_offset + prev_filesz <= p_offset):
                return False, f"segment {i} file payload overlaps segment {j}"

        prev_segs.append((p_paddr, p_memsz, p_offset, p_filesz))

    # 14. Entry point validation
    entry_pa = 0
    ok, entry_pa = check_add_overflow(load_addr, entry_offset)
    if not ok:
        return False, "entry point address overflow"

    entry_in_exec_seg = False
    for i, seg in enumerate(segments):
        p_paddr, p_offset, p_filesz, p_memsz, p_prot, p_align, res = seg
        if p_prot & MYOS_PROT_EXEC:
            ok, seg_end = check_add_overflow(p_paddr, p_memsz)
            if ok and entry_pa >= p_paddr and entry_pa < seg_end:
                entry_in_exec_seg = True
                break
    if not entry_in_exec_seg:
        return False, f"entry point 0x{entry_pa:x} not inside an executable segment"

    # 15. BSS size overflow check
    ok, bss_end = check_add_overflow(image_size, bss_size)
    if not ok:
        return False, "image_size + bss_size overflow"

    if verbose:
        print(f"Image validation OK:")
        print(f"  load_addr:       0x{load_addr:x}")
        print(f"  entry_offset:    0x{entry_offset:x} (entry_pa: 0x{entry_pa:x})")
        print(f"  image_size:      0x{image_size:x}")
        print(f"  bss_size:        0x{bss_size:x}")
        print(f"  segment_count:   {segment_count}")
        for i, seg in enumerate(segments):
            p_paddr, p_offset, p_filesz, p_memsz, p_prot, p_align, res = seg
            prot_str = ""
            if p_prot & MYOS_PROT_READ: prot_str += "R"
            if p_prot & MYOS_PROT_WRITE: prot_str += "W"
            if p_prot & MYOS_PROT_EXEC: prot_str += "X"
            print(f"  seg {i}: paddr=0x{p_paddr:x} offset=0x{p_offset:x} filesz=0x{p_filesz:x} memsz=0x{p_memsz:x} prot={prot_str} align=0x{p_align:x}")

    return True, "OK"

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    verbose = '--verbose' in sys.argv[1:]

    if len(args) < 1:
        print(f"Usage: {sys.argv[0]} <image-file> [--verbose]")
        return 1

    path = args[0]

    try:
        with open(path, 'rb') as f:
            data = f.read()
    except IOError as e:
        print(f"ERROR: {e}")
        return 1

    ok, msg = validate_image(data, verbose)
    if ok:
        print(f"PASS: {path} - {msg}")
        return 0
    else:
        print(f"FAIL: {path} - {msg}")
        return 1

if __name__ == '__main__':
    sys.exit(main())