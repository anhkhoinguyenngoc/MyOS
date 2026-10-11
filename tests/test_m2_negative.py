#!/usr/bin/env python3
"""
MyOS M2 Kernel Image - Negative Test Suite
Tests that malformed images are rejected for the intended reason.

Layout convention used by these tests:
  [0x0000, 0x0040)            header (64 bytes)
  [0x0040, 0x0040+48*N)       segment table
  [0x4000, image_size)        segment payloads (page-aligned offsets)

Because identity mapping requires p_paddr == load_addr + p_offset and
p_paddr must be aligned to p_align (>= MYOS_PAGE_SIZE = 0x4000), every
p_offset used here is a multiple of 0x4000.
"""

import sys
import os
import struct
import tempfile
import subprocess

# Replicate format constants (must match include/myos_image.h)
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

LOAD_ADDR = 0x800200000
IMAGE_SIZE = 0x10000
ENTRY_OFFSET = 0x4000

HDR_FMT = '<QIIQQQQIIQ'
SEG_FMT = '<QQQQIIQ'

def make_header(load_addr=LOAD_ADDR, entry_offset=ENTRY_OFFSET,
                image_size=IMAGE_SIZE, bss_size=0x1000, segment_count=1,
                version=MYOS_IMAGE_VERSION, arch=MYOS_ARCH_AARCH64,
                magic=MYOS_IMAGE_MAGIC, flags=0, reserved=0):
    return struct.pack(HDR_FMT,
        magic, version, arch, load_addr, entry_offset, image_size, bss_size,
        segment_count, flags, reserved)

def make_segment(p_offset=0x4000, p_filesz=0x4000, p_memsz=0x4000,
                 p_prot=MYOS_PROT_READ|MYOS_PROT_EXEC,
                 p_align=MYOS_PAGE_SIZE, p_paddr=None, reserved=0):
    # Identity mapping: p_paddr = load_addr + p_offset (default)
    if p_paddr is None:
        p_paddr = LOAD_ADDR + p_offset
    return struct.pack(SEG_FMT,
        p_paddr, p_offset, p_filesz, p_memsz, p_prot, p_align, reserved)

def make_valid_image():
    """Create a minimal valid image: 1 RX segment, payload at 0x4000."""
    hdr = make_header()
    seg = make_segment()
    payload_size = IMAGE_SIZE - MYOS_IMAGE_HDR_SIZE - MYOS_SEGMENT_SIZE
    payload = b'\x00' * payload_size
    return hdr + seg + payload

def write_temp(data):
    fd, path = tempfile.mkstemp(suffix='.myos')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return path

def run_validator(path):
    """Run validate_m2.py and return (returncode, output)."""
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), 'validate_m2.py'), path],
        capture_output=True, text=True
    )
    return result.returncode, result.stdout + result.stderr

def test_cases():
    cases = []

    # 1. Valid minimal image
    cases.append(("valid minimal image", make_valid_image(), True, None))

    # 2. Bad magic
    img = bytearray(make_valid_image())
    img[0:8] = struct.pack('<Q', 0xDEADBEEF)
    cases.append(("bad magic", bytes(img), False, "magic"))

    # 3. Bad version
    img = make_header(version=2) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("bad version", img, False, "version"))

    # 4. Bad architecture
    img = make_header(arch=1) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("bad architecture", img, False, "architecture"))

    # 5. Nonzero flags
    img = make_header(flags=1) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("nonzero flags", img, False, "flags"))

    # 6. Nonzero reserved field
    img = make_header(reserved=0x1234) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("nonzero reserved", img, False, "reserved"))

    # 7. Truncated header
    cases.append(("truncated header", make_valid_image()[:32], False, "too small"))

    # 8. Truncated segment table (image_size matches actual data so the
    #    segment-table bounds check is what fires)
    img = make_header(segment_count=2, entry_offset=0x10, image_size=0x88) + \
          make_segment() + b'\x00' * 24
    cases.append(("truncated segment table", img, False, "segment table"))

    # 9. Declared image_size larger than actual file
    img = make_header(image_size=0x20000) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("image_size > file size", img, False, "image_size"))

    # 10. Segment count over limit
    img = make_header(segment_count=17) + make_segment() + b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("segment count over limit", img, False, "segment_count"))

    # 11. Entry offset beyond image (overflow-sized entry_offset)
    img = make_header(entry_offset=0xFFFFFFFFFFFFFFFF) + make_segment() + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("entry_offset overflow", img, False, "entry_offset"))

    # 12. Integer overflow: load_addr + p_offset overflows 64 bits.
    #     load_addr=0xFFFFFFFFFFFFC000 is page-aligned; +0x4000 overflows.
    img = make_header(load_addr=0xFFFFFFFFFFFFC000) + \
          make_segment(p_offset=0x4000, p_paddr=0xFFFFFFFFFFFFC000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("load_addr + p_offset overflow", img, False, "overflow"))

    # 13. Segment file range outside image
    img = make_header(image_size=0x8000) + \
          make_segment(p_offset=0x4000, p_filesz=0x5000, p_memsz=0x5000) + \
          b'\x00' * (0x8000 - 64 - 48)
    cases.append(("segment file range OOB", img, False, "file data extends past"))

    # 14. filesz > memsz
    img = make_header() + make_segment(p_filesz=0x5000, p_memsz=0x4000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("filesz > memsz", img, False, "p_filesz"))

    # 15. Destination range outside image+bss
    img = make_header(image_size=IMAGE_SIZE, bss_size=0x1000) + \
          make_segment(p_offset=0, p_filesz=0, p_memsz=0x20000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("destination range OOB", img, False, "memory range exceeds"))

    # 16. Destination overlap (identity mapping preserved).
    #     seg1 dest [0x800204000, 0x80020C000), seg2 dest [0x800208000, 0x80020C000).
    seg1 = make_segment(p_offset=0x4000, p_filesz=0x4000, p_memsz=0x8000)
    seg2 = make_segment(p_offset=0x8000, p_filesz=0x4000, p_memsz=0x4000)
    img = make_header(segment_count=2, image_size=IMAGE_SIZE) + seg1 + seg2 + \
          b'\x00' * (IMAGE_SIZE - 64 - 96)
    cases.append(("destination overlap", img, False, "overlaps"))

    # 17. File payload overlap (under identity mapping this also implies
    #     destination overlap; destination check fires first).
    #     seg1 file [0x4000, 0xC000), seg2 file [0x8000, 0xC000).
    seg1 = make_segment(p_offset=0x4000, p_filesz=0x8000, p_memsz=0x8000)
    seg2 = make_segment(p_offset=0x8000, p_filesz=0x4000, p_memsz=0x4000)
    img = make_header(segment_count=2, image_size=IMAGE_SIZE) + seg1 + seg2 + \
          b'\x00' * (IMAGE_SIZE - 64 - 96)
    cases.append(("file payload overlap", img, False, "overlaps"))

    # 18. Invalid alignment (not power of 2)
    img = make_header() + make_segment(p_align=0x3000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("alignment not power of 2", img, False, "not power of two"))

    # 19. Alignment too small
    img = make_header() + make_segment(p_align=0x1000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("alignment too small", img, False, "p_align"))

    # 20. Unknown permission bits
    img = make_header() + make_segment(p_prot=0x8) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("unknown permission bits", img, False, "unknown permission"))

    # 21. Entry point outside any executable segment (segment is RW,
    #     entry lands past it)
    img = make_header(entry_offset=0xC000) + \
          make_segment(p_prot=MYOS_PROT_READ|MYOS_PROT_WRITE) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("entry not in exec segment", img, False, "executable segment"))

    # 22. Entry point in non-executable segment (RW only)
    img = make_header(entry_offset=ENTRY_OFFSET) + \
          make_segment(p_prot=MYOS_PROT_READ|MYOS_PROT_WRITE) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("entry in RW segment", img, False, "executable segment"))

    # 23. Empty executable segment (filesz=0, memsz=0)
    img = make_header(entry_offset=ENTRY_OFFSET) + \
          make_segment(p_filesz=0, p_memsz=0, p_prot=MYOS_PROT_EXEC) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("empty executable segment", img, False, "executable segment"))

    # 24. BSS overflow (bss_size = max uint64)
    img = make_header(bss_size=0xFFFFFFFFFFFFFFFF) + make_segment() + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("bss overflow", img, False, "overflow"))

    # 25. Load address not aligned
    img = make_header(load_addr=0x800200001) + \
          make_segment(p_offset=0x1001, p_paddr=0x800201001) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("load addr not aligned", img, False, "load_addr"))

    # 26. Segment p_paddr not aligned to p_align
    img = make_header() + make_segment(p_paddr=0x800204001) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("segment paddr not aligned", img, False, "p_paddr"))

    # 27. Identity mapping violation (p_paddr != load_addr + p_offset)
    img = make_header() + make_segment(p_offset=0x4000, p_paddr=0x800300000) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("identity mapping violation", img, False, "p_paddr"))

    # 28. Segment reserved field nonzero
    img = make_header() + make_segment(reserved=0x1234) + \
          b'\x00' * (IMAGE_SIZE - 64 - 48)
    cases.append(("segment reserved nonzero", img, False, "reserved"))

    # 29. Zero segments but entry_offset set
    img = make_header(segment_count=0, entry_offset=ENTRY_OFFSET) + \
          b'\x00' * (IMAGE_SIZE - 64)
    cases.append(("zero segments with entry", img, False, "executable segment"))

    # 30. Multiple valid segments (should pass)
    seg1 = make_segment(p_offset=0x4000, p_filesz=0x4000, p_memsz=0x4000,
                        p_prot=MYOS_PROT_READ|MYOS_PROT_EXEC)
    seg2 = make_segment(p_offset=0x8000, p_filesz=0x8000, p_memsz=0x8000,
                        p_prot=MYOS_PROT_READ|MYOS_PROT_WRITE)
    img = make_header(segment_count=2, image_size=IMAGE_SIZE, bss_size=0x1000) + seg1 + seg2 + \
          b'\x00' * (IMAGE_SIZE - 64 - 96)
    cases.append(("multiple valid segments", img, True, None))

    # 31. BSS-only segment (filesz=0, memsz>0) with entry in a prior exec segment
    seg1 = make_segment(p_offset=0x4000, p_filesz=0x4000, p_memsz=0x4000,
                        p_prot=MYOS_PROT_READ|MYOS_PROT_EXEC)
    seg2 = make_segment(p_offset=0x8000, p_filesz=0, p_memsz=0x4000,
                        p_prot=MYOS_PROT_READ|MYOS_PROT_WRITE)
    img = make_header(segment_count=2, image_size=IMAGE_SIZE, bss_size=0x1000) + seg1 + seg2 + \
          b'\x00' * (IMAGE_SIZE - 64 - 96)
    cases.append(("bss-only segment valid", img, True, None))

    return cases

def main():
    cases = test_cases()
    passed = 0
    failed = 0

    for name, data, expect_pass, expected_reason in cases:
        path = write_temp(data)
        try:
            rc, output = run_validator(path)
            actual_pass = (rc == 0)

            if actual_pass != expect_pass:
                print(f"FAIL: {name}")
                print(f"  Expected: {'PASS' if expect_pass else 'FAIL'}")
                print(f"  Actual:   {'PASS' if actual_pass else 'FAIL'}")
                print(f"  Output:   {output.strip()}")
                failed += 1
                continue

            # If expecting failure, check the reason matches
            if not expect_pass and expected_reason:
                if expected_reason.lower() not in output.lower():
                    print(f"FAIL: {name}")
                    print(f"  Expected reason containing: '{expected_reason}'")
                    print(f"  Actual output: {output.strip()}")
                    failed += 1
                    continue

            print(f"PASS: {name}")
            passed += 1
        finally:
            os.unlink(path)

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {len(cases)} total")
    print(f"{'='*50}")

    return 1 if failed > 0 else 0

if __name__ == '__main__':
    sys.exit(main())