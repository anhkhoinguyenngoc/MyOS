#!/usr/bin/env python3
"""
M-P2.35 — Offline orchestration test for freshness status propagation.
Demonstrates correct layering using existing APIs; no hardware integration.
"""

import os
import sys
import struct
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "host"))
import probe_freshness as pf

MAGIC_V1 = b"MP1PRB01"
MAGIC_V2 = b"MP1PRB02"
REPORT_SIZE = 0x60
OFF_MAGIC = 0x00
OFF_VER = 0x08
OFF_SIZE = 0x0C
OFF_TOKEN = 0x58

def _v1():
    b = bytearray(REPORT_SIZE)
    b[OFF_MAGIC:OFF_MAGIC+8] = MAGIC_V1
    struct.pack_into("<I", b, OFF_VER, 1)
    struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE)
    return bytes(b)

def _v2(t=0x1234):
    b = bytearray(REPORT_SIZE)
    b[OFF_MAGIC:OFF_MAGIC+8] = MAGIC_V2
    struct.pack_into("<I", b, OFF_VER, 2)
    struct.pack_into("<I", b, OFF_SIZE, REPORT_SIZE)
    struct.pack_into("<Q", b, OFF_TOKEN, t & ((1<<64)-1))
    return bytes(b)

class Orchestration(unittest.TestCase):
    def test_v1_unverified(self):
        st, det = pf.validate_report(_v1())
        self.assertEqual(st, "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_v2_fresh(self):
        st, _ = pf.validate_report(_v2(1), expected_token=1)
        self.assertEqual(st, "V2_VALID_FRESH")

    def test_v2_stale(self):
        st, _ = pf.validate_report(_v2(1), expected_token=2)
        self.assertEqual(st, "V2_VALID_STALE")

    def test_v2_unverifiable(self):
        st, _ = pf.validate_report(_v2(1), expected_token=None)
        self.assertEqual(st, "V2_VALID_FRESHNESS_UNVERIFIABLE")

    def test_truncated_invalid(self):
        st, _ = pf.validate_report(_v1()[:10])
        self.assertEqual(st, "INVALID")

    def test_unknown_version_invalid(self):
        b = bytearray(_v1())
        struct.pack_into("<I", b, OFF_VER, 99)
        st, _ = pf.validate_report(bytes(b))
        self.assertEqual(st, "INVALID")

    def test_stale_not_success(self):
        st, _ = pf.validate_report(_v2(1), expected_token=2)
        self.assertNotEqual(st, "V2_VALID_FRESH")

    def test_unverifiable_not_fresh(self):
        st, _ = pf.validate_report(_v2(1), expected_token=None)
        self.assertNotEqual(st, "V2_VALID_FRESH")

    def test_v1_never_fresh(self):
        for tok in (None, 0, 5):
            st, _ = pf.validate_report(_v1(), expected_token=tok)
            self.assertNotEqual(st, "V2_VALID_FRESH")

if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
