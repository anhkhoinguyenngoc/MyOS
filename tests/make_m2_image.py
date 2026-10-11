#!/usr/bin/env python3
"""
MyOS M2 Kernel Image Generator
Generates a valid canonical M2 image for validator testing.

Layout:
  [0x0000, 0x0040)            header (64 bytes)
  [0x0040, 0x0070)            segment table (1 entry, 48 bytes)
  [0x0070, 0x4000)            padding
  [0x4000, 0x8000)            RX segment payload (text)
  [0x8000, 0xA000)            RW segment payload (data)
  [0xA000, 0xC000)            BSS (zero-filled by loader, not in file)

Usage:
  make_m2_image.py [output-path]
"""

import sys
import os
import struct

MYOS_IMAGE_MAGIC = 0x4E52454B534F594D  # "MYOSKERN"
MYOS_IMAGE_VERSION = 1
MYOS_ARCH_AARCH64 = 0
MYOS_PAGE_SIZE = 0x4000
MYOS_PROT_READ = 1
MYOS_PROT_WRITE = 2
MYOS_PROT_EXEC = 4

HDR_FMT = '<QIIQQQQIIQ'   # 64 bytes
SEG_FMT = '<QQQQIIQ'      # 48 bytes

LOAD_ADDR = 0x801000000   # FORMAT EXAMPLE ONLY.
                          # load_addr is a PHYSICAL address chosen at
                          # runtime by the loader (must sit above
                          # gBootArgs->topOfKernelData and inside the
                          # free heap). See tests/sim_mmap.py.
IMAGE_SIZE = 0xC000        # header + table + padding + text + data
BSS_SIZE = 0x2000          # zero-fill after image_size
ENTRY_OFFSET = 0x4000      # entry at start of text segment

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'build/myos_m2_test.myos'

    # Segment 1: RX text at file offset 0x4000
    text_offset = 0x4000
    text_filesz = 0x4000
    text_memsz = 0x4000
    text_prot = MYOS_PROT_READ | MYOS_PROT_EXEC

    # Segment 2: RW data at file offset 0x8000
    data_offset = 0x8000
    data_filesz = 0x2000
    data_memsz = 0x2000
    data_prot = MYOS_PROT_READ | MYOS_PROT_WRITE

    header = struct.pack(HDR_FMT,
        MYOS_IMAGE_MAGIC, MYOS_IMAGE_VERSION, MYOS_ARCH_AARCH64,
        LOAD_ADDR, ENTRY_OFFSET, IMAGE_SIZE, BSS_SIZE,
        2, 0, 0)

    seg1 = struct.pack(SEG_FMT,
        LOAD_ADDR + text_offset, text_offset, text_filesz, text_memsz,
        text_prot, MYOS_PAGE_SIZE, 0)
    seg2 = struct.pack(SEG_FMT,
        LOAD_ADDR + data_offset, data_offset, data_filesz, data_memsz,
        data_prot, MYOS_PAGE_SIZE, 0)

    # Build the file: header + segment table + padding + payloads
    buf = bytearray(IMAGE_SIZE)
    buf[0:64] = header
    buf[64:64 + 48] = seg1
    buf[64 + 48:64 + 96] = seg2
    # Fill text payload with a recognizable pattern (0xAA)
    for i in range(text_filesz):
        buf[text_offset + i] = 0xAA
    # Fill data payload with a recognizable pattern (0x55)
    for i in range(data_filesz):
        buf[data_offset + i] = 0x55

    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with open(out, 'wb') as f:
        f.write(buf)

    print(f"Generated {out}: {len(buf)} bytes")
    print(f"  load_addr=0x{LOAD_ADDR:x} entry_offset=0x{ENTRY_OFFSET:x}")
    print(f"  image_size=0x{IMAGE_SIZE:x} bss_size=0x{BSS_SIZE:x}")
    print(f"  seg1: RX  offset=0x{text_offset:x} filesz=0x{text_filesz:x}")
    print(f"  seg2: RW  offset=0x{data_offset:x} filesz=0x{data_filesz:x}")
    return 0

if __name__ == '__main__':
    sys.exit(main())