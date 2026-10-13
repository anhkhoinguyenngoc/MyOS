#!/usr/bin/env python3
"""
MyOS M-P2.7 -- offline tests for tools/host/probe_report.py.

Covers, with the real repo as ground truth:
  * the parser's layout constants are a 1:1 mirror of the `.equ` block in
    boot/aarch64/pongo_probe.S (lines 93-108) -- re-derived from source,
    so the test fails if either side drifts;
  * the magic immediate stored by pongo_probe.S decodes to "MP1PRB01";
  * the built build/pongo_probe.bin carries the static header at 0x200 and
    every dynamic (runtime-recorded) slot is zero in the file;
  * a SYNTHETIC full report (all dynamic slots set) round-trips through the
    parser -- clearly labelled, never presented as hardware evidence.

Usage:
  python3 tests/test_probe_report.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "probe_report.py")
PROBE_S = os.path.join(REPO, "boot", "aarch64", "pongo_probe.S")
PROBE_BIN = os.path.join(REPO, "build", "pongo_probe.bin")

_spec = importlib.util.spec_from_file_location("probe_report", TOOL)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


class TestLayoutSourceContract(unittest.TestCase):
    """Parser constants must equal the .equ block in the probe source."""

    def test_equ_mirror_matches_source(self):
        with open(PROBE_S, "r", encoding="utf-8") as f:
            src = f.read()
        equ = pr.equ_constants_from_source(src)
        self.assertEqual(
            set(equ), set(pr.EQU_MIRROR),
            "source .equ names drifted from probe_report.EQU_MIRROR",
        )
        for name, value in pr.EQU_MIRROR.items():
            self.assertEqual(
                equ[name], value,
                ".equ %s in source is 0x%x but parser says 0x%x"
                % (name, equ[name], value),
            )

    def test_dynamic_offsets_consistent_with_equ(self):
        for name, off in pr.DYNAMIC_FIELDS:
            equ_name = {
                "x30_entry": "RPT_X30_OFF", "x0_entry": "RPT_X0_OFF",
                "x8_entry": "RPT_X8_OFF", "current_el": "RPT_EL_OFF",
                "sctlr_el1": "RPT_SCTLR_OFF", "midr_el1": "RPT_MIDR_OFF",
                "cntpct_el0_1": "RPT_TVAL1_OFF", "cntpct_el0_2": "RPT_TVAL2_OFF",
                "cntfrq_el0": "RPT_TFRQ_OFF", "reserved_zero": "RPT_ZERO_OFF",
            }[name]
            self.assertEqual(
                off, pr.EQU_MIRROR[equ_name],
                "field %s offset inconsistent with %s" % (name, equ_name),
            )

    def test_magic_immediate_decodes_to_ascii_magic(self):
        self.assertEqual(struct.pack("<Q", pr.MAGIC_IMMEDIATE), pr.MAGIC_TXT)
        self.assertEqual(pr.MAGIC_TXT, b"MP1PRB01")


class TestStaticHeaderInBuiltProbe(unittest.TestCase):
    """The built raw image must carry the header; dynamic slots must be 0."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(PROBE_BIN):
            raise unittest.SkipTest("build/pongo_probe.bin not built")
        cls.report = pr.report_from_probe_bin(PROBE_BIN)

    def test_parses(self):
        self.assertEqual(self.report["magic"], "MP1PRB01")
        self.assertEqual(self.report["version"], pr.RPT_VERSION)
        self.assertEqual(self.report["report_size"], pr.RPT_SIZE)

    def test_dynamic_slots_zero_in_static_file(self):
        self.assertFalse(pr.has_dynamic_data(self.report),
                         "static upload must not carry runtime slot values")
        for name, _ in pr.DYNAMIC_FIELDS:
            self.assertEqual(self.report[name], 0, "%s must be 0 in file" % name)


class TestSyntheticRoundTrip(unittest.TestCase):
    """SYNTHETIC fixture: verifies the parser reads u64 slots at the right
    offsets.  These values are invented and are NOT hardware evidence."""

    def _blob(self, values):
        rpt = bytearray(pr.RPT_SIZE)
        rpt[0:8] = pr.MAGIC_TXT
        struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_VER_OFF"], pr.RPT_VERSION)
        struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_SIZE_OFF"], pr.RPT_SIZE)
        for name, off in pr.DYNAMIC_FIELDS:
            struct.pack_into("<Q", rpt, off, values[name])
        return bytes(rpt)

    def test_roundtrip(self):
        values = {
            "x30_entry": 0x180510000, "x0_entry": 0x4010000000,
            "x8_entry": 0x1000a8000, "current_el": 0x8,
            "sctlr_el1": 0x1005d, "midr_el1": 0x461f002,
            "cntpct_el0_1": 0x12345, "cntpct_el0_2": 0x12378,
            "cntfrq_el0": 0x1200000, "reserved_zero": 0,
        }
        report = pr.parse_report(self._blob(values))
        self.assertTrue(pr.has_dynamic_data(report))
        for name, _ in pr.DYNAMIC_FIELDS:
            self.assertEqual(report[name], values[name], name)

    def test_bad_magic_rejected(self):
        blob = bytearray(pr.RPT_SIZE)
        blob[0:8] = b"NONPRB01"
        with self.assertRaises(ValueError):
            pr.parse_report(bytes(blob))

    def test_short_blob_rejected(self):
        with self.assertRaises(ValueError):
            pr.parse_report(b"\x00\x00")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)