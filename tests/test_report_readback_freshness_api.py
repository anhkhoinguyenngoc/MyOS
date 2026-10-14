#!/usr/bin/env python3
"""
M-P2.44 -- regression tests for tools/host/report_readback.py's
validate_report() / ReportReadback.read_report() contract when the
canonical freshness validator (probe_freshness) is composed in.

WHY THIS EXISTS
---------------
M-P2.36 composed the canonical freshness validator into
report_readback.validate_report().  That change accidentally replaced the
legacy return value (the parsed report dict) with a freshness-only dict
and dropped the v1 reserved-field-must-be-zero check, which broke
tests/test_report_readback.py (KeyError: 'magic' and an accepted
reserved!=0 report).  These tests pin the combined contract so the same
regression cannot recur:

  * a valid v1 report still returns the parsed fields (magic/version/
    report_size/reserved_zero/dynamic slots) AND carries freshness_status
    = LEGACY_VALID_FRESHNESS_UNVERIFIED;
  * structural rules (exact size, magic/version/size, reserved == 0) are
    still enforced and raise MalformedReport;
  * a v2 report is classified by the canonical validator: fresh only on
    an exact token match, unverifiable without an expected token, and a
    token mismatch raises MalformedReport (never silently fresh).

OFFLINE ONLY.  All blobs are SYNTHETIC; the transport is the repo's mock.
Nothing here is hardware evidence and no iPhone is touched.
"""

import importlib.util
import os
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "report_readback.py")

sys.path.insert(0, os.path.join(REPO, "tools", "host"))
import probe_report as pr  # noqa: E402
import pongo_client as pc  # noqa: E402

_spec = importlib.util.spec_from_file_location("report_readback", TOOL)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

MAGIC_V1 = b"MP1PRB01"
MAGIC_V2 = b"MP1PRB02"
OFF_TOKEN = 0x58


def _v1(reserved=0, x30=0x180510000, el=0x8, version=None, size=None):
    b = bytearray(pr.RPT_SIZE)
    b[0:8] = MAGIC_V1
    struct.pack_into("<I", b, pr.EQU_MIRROR["RPT_VER_OFF"],
                     pr.RPT_VERSION if version is None else version)
    struct.pack_into("<I", b, pr.EQU_MIRROR["RPT_SIZE_OFF"],
                     pr.RPT_SIZE if size is None else size)
    struct.pack_into("<Q", b, pr.EQU_MIRROR["RPT_X30_OFF"], x30)
    struct.pack_into("<Q", b, pr.EQU_MIRROR["RPT_EL_OFF"], el)
    struct.pack_into("<Q", b, pr.EQU_MIRROR["RPT_ZERO_OFF"], reserved)
    return bytes(b)


def _v2(token=0x1234):
    b = bytearray(pr.RPT_SIZE)
    b[0:8] = MAGIC_V2
    struct.pack_into("<I", b, pr.EQU_MIRROR["RPT_VER_OFF"], 2)
    struct.pack_into("<I", b, pr.EQU_MIRROR["RPT_SIZE_OFF"], pr.RPT_SIZE)
    struct.pack_into("<Q", b, OFF_TOKEN, token & ((1 << 64) - 1))
    return bytes(b)


class TestValidateReportContract(unittest.TestCase):
    """validate_report() must keep the parsed-report contract."""

    def test_v1_returns_parsed_fields_and_freshness(self):
        report = rr.validate_report(_v1())
        # legacy parsed fields survive
        self.assertEqual(report["magic"], "MP1PRB01")
        self.assertEqual(report["version"], pr.RPT_VERSION)
        self.assertEqual(report["report_size"], pr.RPT_SIZE)
        self.assertEqual(report["reserved_zero"], 0)
        self.assertEqual(report["x30_entry"], 0x180510000)
        self.assertEqual(report["current_el"], 0x8)
        self.assertTrue(pr.has_dynamic_data(report))
        # freshness classification is added on top
        self.assertEqual(report["freshness_status"],
                         "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_v1_is_never_fresh_even_with_token(self):
        for tok in (None, 0, 0x1234):
            report = rr.validate_report(_v1(), expected_token=tok)
            self.assertEqual(report["freshness_status"],
                             "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_v1_reserved_nonzero_rejected(self):
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(_v1(reserved=1))

    def test_bad_magic_rejected(self):
        bad = bytearray(_v1())
        bad[0:8] = b"BADPRB01"
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(bytes(bad))

    def test_truncated_rejected(self):
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(_v1()[:10])

    def test_unknown_v1_version_rejected(self):
        # magic MP1PRB01 with version 99 is neither v1 nor v2 -> invalid
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(_v1(version=99))


class TestV2Classification(unittest.TestCase):
    """v2 is classified by the canonical validator, never silently fresh."""

    def test_v2_matching_token_fresh(self):
        report = rr.validate_report(_v2(0xABCD), expected_token=0xABCD)
        self.assertEqual(report["freshness_status"], "V2_VALID_FRESH")

    def test_v2_no_token_unverifiable(self):
        report = rr.validate_report(_v2(0xABCD))
        self.assertEqual(report["freshness_status"],
                         "V2_VALID_FRESHNESS_UNVERIFIABLE")

    def test_v2_token_mismatch_raises(self):
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(_v2(0xABCD), expected_token=0x1111)

    def test_v2_missing_token_never_fresh(self):
        report = rr.validate_report(_v2(0))
        self.assertNotEqual(report["freshness_status"], "V2_VALID_FRESH")


class TestReadReportThroughMock(unittest.TestCase):
    """ReportReadback.read_report() reaches the caller with parsed fields
    and freshness status when driven through the repo's offline mock."""

    BUFFER_VA = 0x1000a9000  # SYNTHETIC

    def _reader(self, blob):
        mem = pc.SparseMemory(
            limit=self.BUFFER_VA + pr.RPT_OFF + pr.RPT_SIZE)
        mem.poke(self.BUFFER_VA + pr.RPT_OFF, blob)
        dev = pc.MockPongoDevice(memory=mem, alive=True)
        return rr.ReportReadback(pc.PongoClient(dev),
                                 (self.BUFFER_VA, self.BUFFER_VA + rr.UPLOADSZ))

    def test_read_report_v1_fields_reach_caller(self):
        report = self._reader(_v1()).read_report(self.BUFFER_VA)
        self.assertEqual(report["magic"], "MP1PRB01")
        self.assertEqual(report["x30_entry"], 0x180510000)
        self.assertEqual(report["freshness_status"],
                         "LEGACY_VALID_FRESHNESS_UNVERIFIED")

    def test_read_report_v2_fresh_reaches_caller(self):
        report = self._reader(_v2(0x55)).read_report(
            self.BUFFER_VA, expected_token=0x55)
        self.assertEqual(report["freshness_status"], "V2_VALID_FRESH")

    def test_read_report_v2_mismatch_raises(self):
        with self.assertRaises(rr.MalformedReport):
            self._reader(_v2(0x55)).read_report(self.BUFFER_VA,
                                                expected_token=0xAA)

    def test_read_report_rejects_reserved_nonzero(self):
        with self.assertRaises(rr.MalformedReport):
            self._reader(_v1(reserved=7)).read_report(self.BUFFER_VA)


if __name__ == "__main__":
    print("NOTE: OFFLINE -- synthetic fixtures; no hardware, no boot.",
          flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)
