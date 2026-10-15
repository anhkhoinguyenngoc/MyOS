#!/usr/bin/env python3
"""
M-P2.53 -- Sequential report readback and stale-data isolation tests.

Tests the sequencing behavior of report readback operations to ensure
stale data in the stdout ring doesn't corrupt subsequent reads.
"""

import sys
import os
import struct
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "host"))

import probe_report as pr
import pongo_client as pc
import report_readback as rr
from pongo_client import MockPongoDevice, SparseMemory, PongoClient

# Field offsets from probe_report
EQU_MIRROR = {
    "RPT_MAGIC_OFF": 0x00,
    "RPT_VER_OFF":   0x08,
    "RPT_SIZE_OFF":  0x0C,
    "RPT_X30_OFF":   0x10,
    "RPT_X0_OFF":    0x18,
    "RPT_X8_OFF":    0x20,
    "RPT_EL_OFF":    0x28,
    "RPT_SCTLR_OFF": 0x30,
    "RPT_MIDR_OFF":  0x38,
    "RPT_TVAL1_OFF": 0x40,
    "RPT_TVAL2_OFF": 0x48,
    "RPT_TFRQ_OFF":  0x50,
    "RPT_ZERO_OFF":  0x58,
    "RPT_SIZE":      0x60,
    "RPT_VERSION":   1,
    "RPT_OFF":       0x200,
}
RPT_SIZE = 96
RPT_OFF = 0x200
MAGIC_TXT = b"MP1PRB01"
RPT_VERSION = 1

def make_report(x30=0xDEADBEEFDEADBEEF, x0=0xCAFEBABECAFEBABE, x8=0xFEEDFACEFEEDFACE,
                current_el=0x8, sctlr=0x30C5C5C5, midr=0x411FC075,
                cntpct1=0x1111111111111111, cntpct2=0x2222222222222222,
                cntfrq=0x33333333, reserved=0):
    """Create a valid MP1PRB01 report with specified values."""
    v1 = bytearray(96)
    v1[0:8] = b"MP1PRB01"
    struct.pack_into("<I", v1, 8, 1)      # version
    struct.pack_into("<I", v1, 12, 96)    # report_size
    struct.pack_into("<Q", v1, 0x10, x30)  # x30_entry
    struct.pack_into("<Q", v1, 0x18, x0)   # x0_entry
    struct.pack_into("<Q", v1, 0x20, x8)   # x8_entry
    struct.pack_into("<Q", v1, 0x28, current_el)  # current_el
    struct.pack_into("<Q", v1, 0x30, sctlr)  # sctlr_el1
    struct.pack_into("<Q", v1, 0x38, midr)  # midr_el1
    struct.pack_into("<Q", v1, 0x40, cntpct1) # cntpct_el0_1
    struct.pack_into("<Q", v1, 0x48, cntpct2) # cntpct_el0_2
    struct.pack_into("<Q", v1, 0x50, cntfrq)  # cntfrq_el0
    struct.pack_into("<Q", v1, 0x58, reserved) # reserved_zero
    return bytes(v1)

# Pre-built distinct reports
REPORT_A = make_report(
    x30=0xAAAAAAAAAAAAAAAA, x0=0x1111111111111111, x8=0x4444444444444444,
    sctlr=0x1111111111111111, midr=0x11111111,
    cntpct1=0x1111111111111111, cntpct2=0x4444444444444444,
    cntfrq=0x11111111
)

REPORT_B = make_report(
    x30=0xBBBBBBBBBBBBBBBB, x0=0x2222222222222222, x8=0x5555555555555555,
    sctlr=0x2222222222222222, midr=0x22222222,
    cntpct1=0x2222222222222222, cntpct2=0x5555555555555555,
    cntfrq=0x22222222
)

REPORT_C = make_report(
    x30=0xCCCCCCCCCCCCCCCC, x0=0x3333333333333333, x8=0x6666666666666666,
    sctlr=0x3333333333333333, midr=0x33333333,
    cntpct1=0x3333333333333333, cntpct2=0x6666666666666666,
    cntfrq=0x33333333
)

def hexdump_to_text(report_bytes, base_addr=0x1000a9200):
    """Convert report bytes to hexdump text format."""
    lines = []
    for i in range(6):
        addr = base_addr + i * 16
        row = report_bytes[i*16:(i+1)*16]
        line = f"0x{addr:09x}: " + " ".join(f"{b:02x}" for b in row) + " "
        lines.append(line)
    return "\n".join(lines) + "\n"


class TestSequentialReads(unittest.TestCase):
    """Test sequential reads of distinct reports."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.mem.poke(0x1000a9200, REPORT_A)
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)
        self.reader = rr.ReportReadback(pc.PongoClient(self.dev),
                                         (self.base, self.base + 1024*1024))

    def assertReportEqual(self, report, expected, prefix=""):
        for field, expected_val in expected.items():
            actual = report.get(field)
            if isinstance(expected_val, int):
                self.assertEqual(actual, expected_val, 
                    f"{prefix} {field}: expected 0x{expected_val:x}, got 0x{actual:x}")
            else:
                self.assertEqual(actual, expected_val, 
                    f"{prefix} {field}: expected '{expected_val}', got '{actual}'")

    def test_sequential_reads_A_then_B_then_C(self):
        """TC-1: Sequential reads of A, then B, then C."""
        # First read - Report A
        self.mem.poke(0x1000a9200, REPORT_A)
        report = self.reader.read_report(self.base)
        expected = {
            'magic': 'MP1PRB01', 'version': 1, 'report_size': 96,
            'x30_entry': 0xAAAAAAAAAAAAAAAA, 'x0_entry': 0x1111111111111111,
            'x8_entry': 0x4444444444444444, 'current_el': 0x8,
            'sctlr_el1': 0x1111111111111111, 'midr_el1': 0x11111111,
            'cntpct_el0_1': 0x1111111111111111, 'cntpct_el0_2': 0x4444444444444444,
            'cntfrq_el0': 0x11111111, 'reserved_zero': 0
        }
        self.assertReportEqual(report, expected, "Read A")

        # Switch to Report B
        self.mem.poke(0x1000a9200, REPORT_B)
        report = self.reader.read_report(self.base)
        expected = {
            'x30_entry': 0xBBBBBBBBBBBBBBBB, 'x0_entry': 0x2222222222222222,
            'x8_entry': 0x5555555555555555, 'sctlr_el1': 0x2222222222222222,
            'midr_el1': 0x22222222, 'cntpct_el0_1': 0x2222222222222222,
            'cntpct_el0_2': 0x5555555555555555, 'cntfrq_el0': 0x22222222
        }
        self.assertReportEqual(report, expected, "Read B")

        # Switch to Report C
        self.mem.poke(0x1000a9200, REPORT_C)
        report = self.reader.read_report(self.base)
        expected = {
            'x30_entry': 0xCCCCCCCCCCCCCCCC, 'x0_entry': 0x3333333333333333,
            'x8_entry': 0x6666666666666666, 'sctlr_el1': 0x3333333333333333,
            'midr_el1': 0x33333333, 'cntpct_el0_1': 0x3333333333333333,
            'cntpct_el0_2': 0x6666666666666666, 'cntfrq_el0': 0x33333333
        }
        self.assertReportEqual(report, expected, "Read C")


class TestStaleDataInRing(unittest.TestCase):
    """Test handling of stale data in stdout ring."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.mem.poke(0x1000a9200, REPORT_B)  # Memory has Report B
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)
        self.reader = rr.ReportReadback(pc.PongoClient(self.dev),
                                         (0x1000a9000, 0x1000a9000 + 1024*1024))

    def test_stale_data_in_ring_before_read(self):
        """TC-2: Stale data in ring before read should be drained."""
        # Pre-fill ring with Report A hexdump (16 bytes = 1 row)
        stale_text = "0x000000000: 4d 50 31 50 52 42 30 31 01 00 00 00 60 00 00 00 \n"
        self.dev.stdbuf = bytearray(stale_text.encode())
        
        report = self.reader.read_report(0x1000a9000)
        
        # Should get Report B from memory, not stale data
        self.assertEqual(report['x30_entry'], 0xBBBBBBBBBBBBBBBB)
        self.assertEqual(report['x0_entry'], 0x2222222222222222)
        self.assertEqual(report['x8_entry'], 0x5555555555555555)

    def test_stale_full_report_in_ring(self):
        """Stale full report in ring should be drained."""
        # Pre-fill with full Report A hexdump
        stale_text = hexdump_to_text(REPORT_A)
        self.dev.stdbuf = bytearray(stale_text.encode())
        
        # Memory has Report B
        self.mem.poke(0x1000a9200, REPORT_B)
        
        report = self.reader.read_report(0x1000a9000)
        
        # Should get Report B, not Report A
        self.assertEqual(report['x30_entry'], 0xBBBBBBBBBBBBBBBB)
        self.assertEqual(report['x0_entry'], 0x2222222222222222)


class TestMixedOutput(unittest.TestCase):
    """Test handling of mixed valid/truncated/malformed output."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)
        self.reader = rr.ReportReadback(pc.PongoClient(self.dev),
                                         (0x1000a9000, 0x1000a9000 + 1024*1024))

    def test_valid_prefix_then_truncated_second(self):
        """TC-3: Valid report prefix followed by truncated second report."""
        # Pre-fill with Report A + first 2 rows of Report B
        prefix = REPORT_A[:32] + REPORT_B[:32]  # 32 bytes = 2 rows
        self.dev.stdbuf = bytearray(prefix)
        
        # Memory has Report C
        self.mem.poke(0x1000a9200, REPORT_C)
        
        report = self.reader.read_report(self.base)
        
        # Should get Report C from memory
        self.assertEqual(report['x30_entry'], 0xCCCCCCCCCCCCCCCC)

    def test_valid_then_truncated_same_address(self):
        """Valid report followed by truncated data at same address."""
        # First fill ring with valid report A
        full_a = REPORT_A
        text_a = hexdump_to_text(full_a)
        self.dev.stdbuf = bytearray(text_a.encode())
        
        # Then memory has Report B
        self.mem.poke(0x1000a9200, REPORT_B)
        
        report = self.reader.read_report(self.base)
        self.assertEqual(report['x30_entry'], 0xBBBBBBBBBBBBBBBB)


class TestEmptyAndMalformedAfterSuccess(unittest.TestCase):
    """Test empty/malformed responses after successful read."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)
        self.reader = rr.ReportReadback(pc.PongoClient(self.dev),
                                         (0x1000a9000, 0x1000a9000 + 1024*1024))

    def test_empty_output_after_success(self):
        """TC-5: Empty output after successful read."""
        # First read succeeds
        self.mem.poke(0x1000a9200, REPORT_A)
        report = self.reader.read_report(self.base)
        self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)
        
        # Now device is torn down (simulates disconnected/empty output)
        self.dev.set_teardown()
        
        with self.assertRaises(pc.PongoChannelGone):
            self.reader.read_report(self.base)

    def test_malformed_after_success(self):
        """TC-6: Malformed response after successful read."""
        # First read succeeds
        self.mem.poke(0x1000a9200, REPORT_A)
        report = self.reader.read_report(self.base)
        self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)
        
        # Now device returns malformed hexdump
        # Test the using parser directly since device always generates valid output
        malformed_text = b"row 1\nbad stuff\n"
        with self.assertRaises(Exception):
            rr.parse_md8_strict(malformed_text, 0x1000a9200, 96)
        
        # Now verify sequential re-read of different report works correctly
        self.mem.poke(0x1000a9200, REPORT_B)
        report = self.reader.read_report(self.base)
        self.assertEqual(report['x30_entry'], 0xBBBBBBBBBBBBBBBB)

    def test_command_failure_after_success(self):
        """TC-6: Command failure after successful read."""
        # First read succeeds
        self.mem.poke(0x1000a9200, REPORT_A)
        report = self.reader.read_report(self.base)
        self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)
        
        # Now device is torn down
        self.dev.set_teardown()
        
        with self.assertRaises(pc.PongoChannelGone):
            self.reader.read_report(self.base)


class TestRepeatedReads(unittest.TestCase):
    """Test repeated reads of same report."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.mem.poke(0x1000a9200, REPORT_A)
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.reader = rr.ReportReadback(pc.PongoClient(self.dev),
                                         (0x1000a9000, 0x1000a9000 + 1024*1024))

    def test_repeated_reads_same_report(self):
        """TC-10: Repeated reads of same report return same data."""
        for i in range(5):
            report = self.reader.read_report(self.base)
            self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)
            self.assertEqual(report['x0_entry'], 0x1111111111111111)

    def test_repeated_reads_different_addresses(self):
        """Same base address is stable across multiple reads."""
        self.mem.poke(0x1000a9200, REPORT_A)
        for _ in range(3):
            report = self.reader.read_report(self.base)
            self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)

    def make_v2_report(self, token):
        v2 = bytearray(96)
        v2[0:8] = b"MP1PRB02"
        struct.pack_into("<I", v2, 8, 2)
        struct.pack_into("<I", v2, 12, 96)
        struct.pack_into("<Q", v2, 88, token)  # token at offset 0x58
        return bytes(v2)

    def test_v2_matching_token_fresh(self):
        """v2 with matching token should be fresh."""
        v2 = self.make_v2_report(0x123456789ABCDEF0)
        self.mem.poke(0x1000a9200, v2)
        report = self.reader.read_report(self.base, expected_token=0x123456789ABCDEF0)
        self.assertEqual(report['freshness_status'], 'V2_VALID_FRESH')

    def test_v2_mismatch_token_stale(self):
        """v2 with mismatched token should be stale."""
        v2 = self.make_v2_report(0x123456789ABCDEF0)
        self.mem.poke(0x1000a9200, v2)
        with self.assertRaises(rr.MalformedReport) as cm:
            self.reader.read_report(self.base, expected_token=0x1111111111111111)
        self.assertIn('V2_VALID_STALE', str(cm.exception))

    def test_v2_no_token_unverifiable(self):
        """v2 without expected token should be unverifiable."""
        v2 = self.make_v2_report(0x123456789ABCDEF0)
        self.mem.poke(0x1000a9200, v2)
        report = self.reader.read_report(self.base)  # No expected_token
        self.assertEqual(report['freshness_status'], 'V2_VALID_FRESHNESS_UNVERIFIABLE')

    def test_v1_token_ignored(self):
        """v1 report should ignore expected_token parameter."""
        self.mem.poke(0x1000a9200, REPORT_A)
        report = self.reader.read_report(self.base, expected_token=0xDEADBEEF)
        self.assertEqual(report['freshness_status'], 'LEGACY_VALID_FRESHNESS_UNVERIFIED')


class TestLegacyApi(unittest.TestCase):
    """Test legacy read_report_via_md8 API."""
    
    def setUp(self):
        self.base = 0x1000a9000
        self.mem = SparseMemory(limit=0x1000a9000 + 0x200 + 96)
        self.dev = MockPongoDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)

    def test_legacy_path_accepts_valid(self):
        self.mem.poke(0x1000a9200, REPORT_A)
        report, text = self.client.read_report_via_md8(0x1000a9000)
        self.assertEqual(report['x30_entry'], 0xAAAAAAAAAAAAAAAA)

    def test_legacy_rejects_bad_reserved(self):
        bad = bytearray(REPORT_A)
        struct.pack_into("<Q", bad, 0x58, 0xDEADBEEF)
        self.mem.poke(0x1000a9200, bytes(bad))
        with self.assertRaises(ValueError):
            self.client.read_report_via_md8(self.base)

    def test_legacy_rejects_v2(self):
        v2 = bytearray(96)
        v2[0:8] = b"MP1PRB02"
        struct.pack_into("<I", v2, 8, 2)
        struct.pack_into("<I", v2, 12, 96)
        struct.pack_into("<Q", v2, 88, 0x123456789ABCDEF0)
        self.mem.poke(0x1000a9200, bytes(v2))
        with self.assertRaises(ValueError):
            self.client.read_report_via_md8(self.base)


if __name__ == '__main__':
    unittest.main(verbosity=2)
