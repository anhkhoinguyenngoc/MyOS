#!/usr/bin/env python3
"""
MyOS M6.5 — Boot artifact and platform compatibility preflight (host-only, stdlib).
"""
import argparse
import os
import sys

ELF_MAGIC = b'\x7fELF'


class PreflightError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def check_file_exists(path):
    if not os.path.exists(path):
        raise PreflightError("missing file: %s" % path)
    if not os.path.isfile(path):
        raise PreflightError("not a regular file: %s" % path)
    if os.path.getsize(path) == 0:
        raise PreflightError("empty file: %s" % path)


def check_elf64_le_qemu_virt(path):
    check_file_exists(path)
    try:
        with open(path, 'rb') as fh:
            data = fh.read(64)
    except Exception as e:
        raise PreflightError("cannot read ELF: %s" % e)

    if len(data) < 64:
        raise PreflightError("ELF header truncated: %s" % path)
    if data[0:4] != ELF_MAGIC:
        raise PreflightError("not ELF (bad magic): %s" % path)
    if data[4] != 2:
        raise PreflightError("wrong ELF class (expect 64-bit): %s" % path)
    if data[5] != 1:
        raise PreflightError("wrong endianness (expect little-endian): %s" % path)
    if data[6] != 1:
        raise PreflightError("wrong ELF version: %s" % path)
    # machine AArch64 = 183 (0xb7), type EXEC=2
    machine = data[18] | (data[19] << 8)
    etype = data[16] | (data[17] << 8)
    if etype != 2:
        raise PreflightError("wrong ELF type (expect ET_EXEC): %s" % path)
    if machine != 0xb7:
        raise PreflightError("wrong machine (expect AArch64): %s" % path)


def check_payload_bin(path, max_size):
    check_file_exists(path)
    try:
        sz = os.path.getsize(path)
    except Exception as e:
        raise PreflightError("cannot stat: %s" % e)
    if sz > max_size:
        raise PreflightError("payload exceeds budget: %d > %d bytes" % (sz, max_size))
    if sz < 4:
        raise PreflightError("payload truncated: %d bytes" % sz)


def parse_max_payload():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    hdr_file = os.path.join(repo_root, 'platform', 'apple', 'a8', 'm1_a8.h')
    try:
        with open(hdr_file, encoding='utf-8') as fh:
            txt = fh.read()
    except Exception as e:
        raise PreflightError("cannot read %s: %s" % (hdr_file, e))
    import re
    def get_hex(name):
        m = re.search(r'#define\s+%s\s+(0x[0-9a-fA-F]+)' % re.escape(name), txt)
        if not m:
            raise PreflightError("missing %s in %s" % (name, hdr_file))
        return int(m.group(1), 16)
    dfu = get_hex('M1_DFU_MAX_XFER')
    hdrlen = get_hex('M1_EXEC_HEADER_LEN')
    if dfu < hdrlen:
        raise PreflightError("inconsistent M1 payload limits")
    return dfu - hdrlen


def main(argv=None):
    ap = argparse.ArgumentParser(description='Artifact compatibility preflight')
    ap.add_argument('--check-qemu-kernel', action='store_true')
    ap.add_argument('--kernel-elf', default=None)
    ap.add_argument('--check-m1-payload', action='store_true')
    ap.add_argument('--payload-bin', default=None)
    ap.add_argument('--max-size', default=None)
    ap.add_argument('--platform', default=None)
    args = ap.parse_args(argv)

    try:
        if args.platform == 'apple-a8':
            raise PreflightError('PLATFORM=apple-a8 is UNSUPPORTED')
        if args.check_qemu_kernel:
            elf = args.kernel_elf or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                 'build', 'myos_kernel.elf')
            check_elf64_le_qemu_virt(elf)
        if args.check_m1_payload:
            maxsz = int(args.max_size, 0) if args.max_size else parse_max_payload()
            pbin = args.payload_bin or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                   'build', 'myos_m1.bin')
            check_payload_bin(pbin, maxsz)
    except PreflightError as e:
        print('ERROR: %s' % e.message, file=sys.stderr)
        return 1
    except Exception as e:
        print('ERROR: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
