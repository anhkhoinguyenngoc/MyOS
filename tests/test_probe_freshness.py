#!/usr/bin/env python3
"""
MyOS M-P2.34 -- Offline tests for probe freshness validator.
Synthetic fixtures only; no hardware. v2 schema is DESIGN PROPOSAL ONLY.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "host"))
import probe_freshness as pf

# v1 layout constants
MAGIC_V1 = b"MP1PRB01"
MAGIC_V2 = b"MP1PRB02"
REPORT_SIZE = 0x60
OFF_MAGIC = 0x00
OFF_VER = 0x08
OFF_SIZE = 0x0C
OFF_TOKEN = 0x58

import struct

def _make_v1():
    b = bytearray(REPORT_SIZE)
    b[OFF_MAGIC:OFF_MAGIC+8] = MAGIC_V1
    struct.pack_into("<I", b, OFF_VER, 1)
    struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE)
    # rest zero
    return bytes(b)

def _make_v2(token=0x123456789ABCDEF0):
    b = bytearray(REPORT_SIZE)
    b[OFF_MAGIC:OFF_MAGIC+8] = MAGIC_V2
    struct.pack_into("<I", b, OFF_VER, 2)
    struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE)
    struct.pack_into("<Q", b, OFF_TOKEN, token & ((1<<64)-1))
    return bytes(b)

class TestV1(unittest.TestCase):
    def test_v1_valid_freshness_unverified(self):
        st,_ = pf.validate_report(_make_v1())
        self.assertEqual(st, "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_v1_valid_with_token_supplied(self):
        st,_ = pf.validate_report(_make_v1(), expected_token=0x1)
        self.assertEqual(st, "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_v1_bad_magic(self):
        b = bytearray(_make_v1())
        b[0] ^= 0xFF
        st,_ = pf.validate_report(bytes(b))
        self.assertEqual(st, "INVALID")

    def test_v1_bad_version(self):
        b = bytearray(_make_v1())
        struct.pack_into("<I", b, OFF_VER, 2)  # wrong
        st,_ = pf.validate_report(bytes(b))
        self.assertEqual(st, "INVALID")

    def test_v1_bad_size(self):
        b = bytearray(_make_v1())
        struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE-4)
        st,_ = pf.validate_report(bytes(b))
        self.assertEqual(st, "INVALID")

    def test_v1_truncated(self):
        b = _make_v1()[:REPORT_SIZE-1]
        st,_ = pf.validate_report(b)
        self.assertEqual(st, "INVALID")

class TestV2(unittest.TestCase):
    def test_v2_valid_fresh(self):
        st,_ = pf.validate_report(_make_v2(0xA), expected_token=0xA)
        self.assertEqual(st, "V2_VALID_FRESH")

    def test_v2_valid_stale(self):
        st,_ = pf.validate_report(_make_v2(0xA), expected_token=0xB)
        self.assertEqual(st, "V2_VALID_STALE")

    def test_v2_unverifiable_no_token(self):
        st,_ = pf.validate_report(_make_v2(0xA), expected_token=None)
        self.assertEqual(st, "V2_VALID_FRESHNESS_UNVERIFIABLE")

    def test_v2_bad_magic(self):
        b = bytearray(_make_v2(0x1))
        b[0] ^= 0xAA
        st,_ = pf.validate_report(bytes(b), expected_token=0x1)
        self.assertEqual(st, "INVALID")

    def test_v2_bad_version(self):
        b = bytearray(_make_v2(0x1))
        struct.pack_into("<I", b, OFF_VER, 3)
        st,_ = pf.validate_report(bytes(b), expected_token=0x1)
        self.assertEqual(st, "INVALID")

    def test_v2_bad_size(self):
        b = bytearray(_make_v2(0x1))
        struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE+8)
        st,_ = pf.validate_report(bytes(b), expected_token=0x1)
        self.assertEqual(st, "INVALID")

    def test_v2_truncated(self):
        b = _make_v2(0x1)[:REPORT_SIZE-1]
        st,_ = pf.validate_report(b, expected_token=0x1)
        self.assertEqual(st, "INVALID")

    def test_v2_token_mismatch_distinct(self):
        st,_ = pf.validate_report(_make_v2(0), expected_token=5)
        self.assertEqual(st, "V2_VALID_STALE")

    def test_unknown_version_rejected(self):
        b = bytearray(_make_v1())
        struct.pack_into("<I", b, OFF_VER, 5)
        st,_ = pf.validate_report(bytes(b))
        self.assertEqual(st, "INVALID")

if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
