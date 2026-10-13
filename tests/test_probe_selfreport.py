#!/usr/bin/env python3
"""
MyOS M-P2.11 -- offline tests for tools/host/probe_selfreport.py (design B).

OFFLINE ONLY: no hardware, no USB, no DFU, no payload upload, no boot, and
no modification of pongoos-src or of the existing probe binary.

What is covered
---------------
  * EXACT REPORT LAYOUT -- every slot written at the offset the probe's
    own `.equ` block declares (re-derived from boot/aarch64/pongo_probe.S,
    not copied from the parser), header magic/version/report_size exact,
    0x60 total, and the pristine upload block byte-identical to the
    already-built build/pongo_probe.bin at 0x200.
  * REGISTER FIELDS -- per-field provenance, the two source-derivable
    slots, the eight blocked slots, and the certificate's honest buckets.
  * INVALID DATA -- every rejection path of validate_prehandoff()/set().
  * HOST PARSING -- the generated blobs round-trip through the existing
    M-P2.7 parser and the M-P2.8 validator (format reuse, not a fork).
  * SOURCE CONTRACT -- only the facts design B actually rests on: the
    `bootr` teardown ordering, the jump register contract, the EL1 proof,
    the SCTLR partial-knowledge limit, the MIDR/CNTFRQ absence, the shell
    command inventory, and the caller-supplied-address rule.

Usage:
  python3 tests/test_probe_selfreport.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import re
import struct
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "probe_selfreport.py")
PROBE_S = os.path.join(REPO, "boot", "aarch64", "pongo_probe.S")
PROBE_BIN = os.path.join(REPO, "build", "pongo_probe.bin")
PONGO_SRC = os.path.join(REPO, "pongoos-src", "src")

ENTRY_C = os.path.join(PONGO_SRC, "kernel", "entry.c")
LOWLEVEL_C = os.path.join(PONGO_SRC, "kernel", "lowlevel.c")
MM_C = os.path.join(PONGO_SRC, "kernel", "mm.c")
MAIN_C = os.path.join(PONGO_SRC, "shell", "main.c")
PONGO_H = os.path.join(PONGO_SRC, "kernel", "pongo.h")

_spec = importlib.util.spec_from_file_location("probe_selfreport", TOOL)
psr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(psr)
pr = psr.probe_report

HOST_DIR = os.path.join(REPO, "tools", "host")
if HOST_DIR not in sys.path:
    sys.path.insert(0, HOST_DIR)
import probe_report  # noqa: E402  (same module, imported by path for
                     # report_readback's dependency chain)
import report_readback  # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Exact report layout
# ---------------------------------------------------------------------------

class TestExactReportLayout(unittest.TestCase):

    def test_blob_is_exactly_report_size(self):
        for blob in (psr.build_expected_report()[0], psr.pristine_upload_blob()):
            self.assertEqual(len(blob), 0x60, "report must be 0x60 bytes")
            self.assertEqual(len(blob), pr.RPT_SIZE)

    def test_header_magic_version_size_are_exact(self):
        for blob in (psr.build_expected_report()[0], psr.pristine_upload_blob()):
            self.assertEqual(blob[0:8], b"MP1PRB01")
            self.assertEqual(struct.unpack_from("<I", blob, 0x08)[0], 1)
            self.assertEqual(struct.unpack_from("<I", blob, 0x0C)[0], 0x60)

    def test_every_slot_lands_at_the_probe_sources_equ_offset(self):
        """Offsets come from the .equ block in pongo_probe.S -- the single
        source of truth -- not from probe_report.EQU_MIRROR."""
        equ = pr.equ_constants_from_source(_read(PROBE_S))
        self.assertTrue(equ, "no .equ block found in pongo_probe.S")

        blob = bytearray(psr.pristine_upload_blob())
        names = [n for n, _ in pr.DYNAMIC_FIELDS]
        # Distinct non-zero sentinel per slot so a swap cannot pass.
        sentinels = {n: 0x1000 + i * 0x11 for i, n in enumerate(names)}
        for name, _ in pr.DYNAMIC_FIELDS:
            struct.pack_into("<Q", blob, equ[_equ_name(name)], sentinels[name])
        parsed = pr.parse_report(bytes(blob))
        for name in names:
            self.assertEqual(parsed[name], sentinels[name],
                             "slot %s not at source offset 0x%x"
                             % (name, equ[_equ_name(name)]))

    def test_header_equs_come_from_source_too(self):
        equ = pr.equ_constants_from_source(_read(PROBE_S))
        self.assertEqual(equ["RPT_VER_OFF"], 0x08)
        self.assertEqual(equ["RPT_SIZE_OFF"], 0x0C)
        self.assertEqual(equ["RPT_SIZE"], 0x60)
        self.assertEqual(equ["RPT_VERSION"], 1)
        self.assertEqual(equ["RPT_OFF"], 0x200)

    def test_pristine_and_expected_differ_only_at_current_el(self):
        """The host's expectation must not silently diverge from the
        uploaded bytes anywhere except the one source-derived slot."""
        pristine = psr.pristine_upload_blob()
        expected = psr.build_expected_report()[0]
        diff = [i for i in range(0x60) if pristine[i] != expected[i]]
        el_off = pr.EQU_MIRROR["RPT_EL_OFF"]
        self.assertNotEqual(diff, [])
        self.assertTrue(
            all(el_off <= i < el_off + 8 for i in diff),
            "differences outside the current_el slot: %r" % diff)
        self.assertEqual(struct.unpack_from("<Q", pristine, el_off)[0], 0x0)
        self.assertEqual(struct.unpack_from("<Q", expected, el_off)[0], 0x4)

    def test_build_round_trips_through_the_probe_parser(self):
        blob, _ = psr.build_expected_report()
        rep = pr.parse_report(blob)
        self.assertEqual(rep["magic"], "MP1PRB01")
        self.assertEqual(rep["version"], 1)
        self.assertEqual(rep["report_size"], 0x60)

    @unittest.skipUnless(os.path.isfile(PROBE_BIN),
                         "build/pongo_probe.bin not built")
    def test_pristine_upload_block_matches_the_built_probe_image(self):
        """REAL artifact check: the block this tool says the device RAM
        holds must be byte-identical to what is actually linked at 0x200
        in the existing probe binary (which is NOT modified here)."""
        with open(PROBE_BIN, "rb") as fh:
            image = fh.read()
        self.assertGreaterEqual(len(image), 0x200 + 0x60)
        self.assertEqual(psr.pristine_upload_blob(), image[0x200:0x260])

    @unittest.skipUnless(os.path.isfile(PROBE_BIN),
                         "build/pongo_probe.bin not built")
    def test_built_probe_image_still_carries_zero_dynamic_slots(self):
        with open(PROBE_BIN, "rb") as fh:
            image = fh.read()
        rep = pr.parse_report(image, 0x200)
        for name, _ in pr.DYNAMIC_FIELDS:
            self.assertEqual(rep[name], 0,
                             "%s must still be 0 in the untouched image"
                             % name)


def _equ_name(field):
    return {
        "x30_entry": "RPT_X30_OFF", "x0_entry": "RPT_X0_OFF",
        "x8_entry": "RPT_X8_OFF", "current_el": "RPT_EL_OFF",
        "sctlr_el1": "RPT_SCTLR_OFF", "midr_el1": "RPT_MIDR_OFF",
        "cntpct_el0_1": "RPT_TVAL1_OFF", "cntpct_el0_2": "RPT_TVAL2_OFF",
        "cntfrq_el0": "RPT_TFRQ_OFF", "reserved_zero": "RPT_ZERO_OFF",
    }[field]


# ---------------------------------------------------------------------------
# 2. Register fields: provenance must be explicit and honest
# ---------------------------------------------------------------------------

class TestRegisterFieldProvenance(unittest.TestCase):

    def test_provenance_covers_every_dynamic_slot_exactly(self):
        self.assertEqual(set(psr.EXPECTED_PROVENANCE),
                         set(n for n, _ in pr.DYNAMIC_FIELDS))

    def test_exactly_two_fields_are_source_derivable(self):
        derived = sorted(n for n, p in psr.EXPECTED_PROVENANCE.items()
                         if p == psr.SOURCE_DERIVED)
        self.assertEqual(derived, ["current_el", "reserved_zero"])

    def test_exactly_eight_fields_are_blocked(self):
        unknown = sorted(n for n, p in psr.EXPECTED_PROVENANCE.items()
                         if p == psr.UNKNOWN)
        self.assertEqual(len(unknown), 8)
        self.assertNotIn("current_el", unknown)
        self.assertNotIn("reserved_zero", unknown)

    def test_current_el_is_the_raw_mrs_value_at_el1(self):
        # pongo_probe.S stores the RAW `mrs CurrentEL`; at EL1 that is
        # (1 << 2) == 0x4, NOT the shifted value get_el() returns.
        self.assertEqual(psr.EXPECTED_PROVENANCE["current_el"],
                         psr.SOURCE_DERIVED)
        self.assertEqual(psr.expected_field_values()["current_el"], 0x4)
        blob = psr.build_expected_report()[0]
        self.assertEqual(
            struct.unpack_from("<Q", blob, pr.EQU_MIRROR["RPT_EL_OFF"])[0],
            0x4)

    def test_no_slot_is_prefilled_with_a_guess(self):
        """UNKNOWN slots must stay 0 -- a fabricated MIDR/CNTFRQ would be
        the exact failure mode design B must avoid."""
        blob, _ = psr.build_expected_report()
        rep = pr.parse_report(blob)
        for name, prov in psr.EXPECTED_PROVENANCE.items():
            if prov == psr.UNKNOWN:
                self.assertEqual(rep[name], 0,
                                 "%s is UNKNOWN and must not be filled"
                                 % name)

    def test_every_field_carries_explicit_evidence(self):
        for name in psr.EXPECTED_PROVENANCE:
            text = psr.EVIDENCE[name]
            self.assertTrue(text.strip(), "no evidence for %s" % name)
            self.assertIn(":", text, "evidence for %s lacks a citation" % name)
        for name in ("x30_entry", "x0_entry", "x8_entry", "midr_el1",
                     "cntfrq_el0"):
            self.assertIn("BLOCKED", psr.EVIDENCE[name])

    def test_provenance_classes_are_the_documented_three(self):
        self.assertEqual(psr.PROVENANCE_CLASSES,
                         (psr.CAPTURED, psr.SOURCE_DERIVED, psr.UNKNOWN))

    # -- certificate -------------------------------------------------------

    def test_certificate_is_uncaptured_until_a_device_read_happens(self):
        cert = psr.PreHandoffReport().certificate()
        self.assertEqual(cert.status, psr.Certificate.UNCATTURED)
        self.assertFalse(cert.ok)
        self.assertEqual(cert.captured, ())

    def test_certificate_after_capture_keeps_the_blocker_visible(self):
        cert = psr.report_from_capture(psr.pristine_upload_blob()).certificate()
        self.assertTrue(cert.ok)
        self.assertEqual(len(cert.captured), 10)
        self.assertEqual(len(cert.unknown), 8, cert.unknown)
        self.assertEqual(len(cert.source_derived), 2)

    def test_certificate_never_claims_probe_observed_data(self):
        for cert in (psr.PreHandoffReport().certificate(),
                     psr.report_from_capture(
                         psr.pristine_upload_blob()).certificate()):
            self.assertEqual(cert.runtime_observed, ())
            self.assertFalse(cert.is_postjump_evidence)
            self.assertFalse(any(cert.predictable(n)
                                 for n, _ in pr.DYNAMIC_FIELDS
                                 if n not in ("current_el",
                                              "reserved_zero")))

    def test_predictable_is_true_only_for_the_two_derived_slots(self):
        cert = psr.report_from_capture(psr.pristine_upload_blob()).certificate()
        for name, prov in psr.EXPECTED_PROVENANCE.items():
            self.assertEqual(cert.predictable(name),
                             prov == psr.SOURCE_DERIVED, name)
            # A pre-handoff read is genuine for every slot we read ...
            self.assertTrue(cert.prehandoff_known(name), name)

    def test_certificate_serialises_without_losing_the_distinction(self):
        d = psr.report_from_capture(
            psr.pristine_upload_blob()).certificate().as_dict()
        self.assertEqual(d["runtime_observed"], [])
        self.assertIs(d["is_postjump_evidence"], False)
        self.assertEqual(len(d["unknown"]), 8)
        self.assertEqual(d["status"], "prehandoff_valid")

    # -- provenance laundering is refused ----------------------------------

    def test_set_refuses_captured_provenance(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("midr_el1", 0x411fc075, psr.CAPTURED)

    def test_set_refuses_unknown_provenance(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("cntfrq_el0", 24000000, psr.UNKNOWN)

    def test_set_refuses_relabelling_a_blocked_slot(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("cntfrq_el0", 0, psr.SOURCE_DERIVED)
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("x30_entry", 0, psr.SOURCE_DERIVED)

    def test_set_refuses_a_source_value_that_breaks_the_probe_contract(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("current_el", 0x8, psr.SOURCE_DERIVED)
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("reserved_zero", 1, psr.SOURCE_DERIVED)

    def test_set_refuses_unknown_fields_and_bad_provenance(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("not_a_field", 0, psr.SOURCE_DERIVED)
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().set("current_el", 0x4, "made-up")

    def test_set_refuses_non_u64_values(self):
        for bad in (-1, 1 << 64, True, "4"):
            with self.assertRaises(psr.PreHandoffError):
                psr.PreHandoffReport().set("current_el", bad,
                                           psr.SOURCE_DERIVED)

    def test_set_accepts_the_legitimate_derived_values(self):
        rep = (psr.PreHandoffReport()
               .set("current_el", 0x4, psr.SOURCE_DERIVED)
               .set("reserved_zero", 0, psr.SOURCE_DERIVED))
        self.assertEqual(rep.build(), psr.build_expected_report()[0])


# ---------------------------------------------------------------------------
# 3. Invalid data
# ---------------------------------------------------------------------------

class TestInvalidData(unittest.TestCase):

    def test_none_blob(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.validate_prehandoff(None)

    def test_short_blob(self):
        with self.assertRaises(psr.PreHandoffError):
            psr.validate_prehandoff(psr.pristine_upload_blob()[:-1])

    def test_oversized_blob_is_not_tolerated(self):
        """An exact size matters: the format is a fixed 0x60 block."""
        with self.assertRaises(psr.PreHandoffError):
            psr.validate_prehandoff(psr.pristine_upload_blob() + b"\x00")

    def test_bad_magic(self):
        blob = bytearray(psr.pristine_upload_blob())
        blob[0:8] = b"BADPRB01"
        with self.assertRaises(psr.PreHandoffError) as cm:
            psr.validate_prehandoff(bytes(blob))
        self.assertIn("magic", str(cm.exception))

    def test_bad_version(self):
        blob = bytearray(psr.pristine_upload_blob())
        struct.pack_into("<I", blob, 0x08, 2)
        with self.assertRaises(psr.PreHandoffError) as cm:
            psr.validate_prehandoff(bytes(blob))
        self.assertIn("version", str(cm.exception))

    def test_bad_report_size(self):
        blob = bytearray(psr.pristine_upload_blob())
        struct.pack_into("<I", blob, 0x0C, 0x61)
        with self.assertRaises(psr.PreHandoffError) as cm:
            psr.validate_prehandoff(bytes(blob))
        self.assertIn("report_size", str(cm.exception))

    def test_non_zero_reserved_field(self):
        blob = bytearray(psr.pristine_upload_blob())
        struct.pack_into("<Q", blob, pr.EQU_MIRROR["RPT_ZERO_OFF"], 1)
        with self.assertRaises(psr.PreHandoffError) as cm:
            psr.validate_prehandoff(bytes(blob))
        self.assertIn("reserved", str(cm.exception))

    def test_every_runtime_slot_must_be_zero_before_bootr(self):
        """The probe cannot have run before `bootr`, so ANY non-zero
        runtime slot means the bytes were altered."""
        for name, off in pr.DYNAMIC_FIELDS:
            blob = bytearray(psr.pristine_upload_blob())
            struct.pack_into("<Q", blob, off, 1)
            with self.assertRaises(psr.PreHandoffError,
                                   msg="slot %s accepted" % name):
                psr.validate_prehandoff(bytes(blob))

    def test_dirty_slot_names_the_offending_field(self):
        blob = bytearray(psr.pristine_upload_blob())
        struct.pack_into("<Q", blob, pr.EQU_MIRROR["RPT_MIDR_OFF"], 0x41)
        with self.assertRaises(psr.PreHandoffError) as cm:
            psr.validate_prehandoff(bytes(blob))
        self.assertIn("midr_el1", str(cm.exception))

    def test_capture_propagates_every_rejection(self):
        blob = bytearray(psr.pristine_upload_blob())
        blob[0:8] = b"NOPEPRB1"
        with self.assertRaises(psr.PreHandoffError):
            psr.PreHandoffReport().capture(bytes(blob))
        # ... and a failed capture must NOT certify anything.
        self.assertEqual(psr.PreHandoffReport().certificate().status,
                         psr.Certificate.UNCATTURED)

    def test_valid_block_still_reports_the_pristine_slots_as_zero(self):
        rep = psr.validate_prehandoff(psr.pristine_upload_blob())
        for name, _ in pr.DYNAMIC_FIELDS:
            self.assertEqual(rep[name], 0, name)


# ---------------------------------------------------------------------------
# 4. Host parsing (reuse of the existing M-P2.7 / M-P2.8 tools)
# ---------------------------------------------------------------------------

class TestHostParsing(unittest.TestCase):

    def test_round_trip_through_the_m27_parser(self):
        blob, _ = psr.build_expected_report()
        rep = pr.parse_report(blob)
        self.assertEqual(rep["magic"], "MP1PRB01")
        self.assertEqual(rep["current_el"], 0x4)
        self.assertTrue(pr.has_dynamic_data(rep))

    def test_round_trip_through_the_m28_validator(self):
        """Format reuse: the pre-handoff blob must satisfy the SAME
        validator the M-P2.8 readback prototype uses, unchanged."""
        pristine = psr.pristine_upload_blob()
        validated = report_readback.validate_report(pristine)
        self.assertEqual(validated["report_size"], 0x60)
        self.assertEqual(validated["reserved_zero"], 0)

        expected = psr.build_expected_report()[0]
        validated2 = report_readback.validate_report(expected)
        self.assertEqual(validated2["current_el"], 0x4)

    def test_m28_validator_still_rejects_a_corrupt_header(self):
        blob = bytearray(psr.pristine_upload_blob())
        blob[0:8] = b"XXXXXXXX"
        with self.assertRaises(report_readback.MalformedReport):
            report_readback.validate_report(bytes(blob))

    def test_m28_validator_still_rejects_a_non_zero_reserved(self):
        blob = bytearray(psr.pristine_upload_blob())
        struct.pack_into("<Q", blob, pr.EQU_MIRROR["RPT_ZERO_OFF"], 9)
        with self.assertRaises(report_readback.MalformedReport):
            report_readback.validate_report(bytes(blob))

    def test_probe_report_cli_parses_the_generated_blob(self):
        import tempfile
        blob, _ = psr.build_expected_report()
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            out = subprocess.run(
                [sys.executable, os.path.join(HOST_DIR, "probe_report.py"),
                 "--blob", path, "--json"],
                capture_output=True, text=True, cwd=REPO)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("MP1PRB01", out.stdout)
        finally:
            os.unlink(path)

    def test_selfreport_cli_builds_without_a_device(self):
        out = subprocess.run(
            [sys.executable, TOOL, "--json"],
            capture_output=True, text=True, cwd=REPO)
        self.assertEqual(out.returncode, 0, out.stderr)
        # Honest default: assembled on the host, never seen on a device.
        self.assertIn('"status": "uncaptured"', out.stdout)
        self.assertEqual(out.stdout.count(': "unknown"'), 8)
        self.assertIn('"is_postjump_evidence": false', out.stdout)
        self.assertIn('"runtime_observed": []', out.stdout)


# ---------------------------------------------------------------------------
# 5. Source contract -- only what design B actually rests on
# ---------------------------------------------------------------------------

class TestBootrExecutionOrder(unittest.TestCase):
    """The ordering fact that makes design B necessary: the probe runs
    strictly after every channel that could carry its bytes back."""

    def test_probe_runs_after_every_teardown_layer(self):
        entry = _read(ENTRY_C)
        self.assertLess(entry.index("while (!gBootFlag)"),
                        entry.index("usb_teardown();"))
        self.assertLess(entry.index("usb_teardown();"),
                        entry.index("interrupt_teardown();"))
        self.assertLess(entry.index("interrupt_teardown();"),
                        entry.index("lowlevel_cleanup();"))
        # the CALL, not the prototype at entry.c:291
        self.assertLess(entry.index("lowlevel_cleanup();"),
                        entry.index("jump_to_image_extended(((uint64_t)"))

    def test_the_raw_jump_supplies_exactly_the_three_argument_registers(self):
        jump = _read(os.path.join(PONGO_SRC, "boot", "jump_to_image.S"))
        body = jump[jump.index("_jump_to_image_extended:"):
                    jump.index("_tramp_hook:")]
        self.assertIn("mov x30, x0", body)
        self.assertIn("mov x0, x1", body)
        self.assertIn("mov x8, x2", body)
        for reg in ("x1", "x2", "x3"):
            self.assertIn("mov %s, xzr" % reg, body)
        # ... and the probe records exactly those three, no more.
        probe = _read(PROBE_S)
        for equ in ("RPT_X30_OFF", "RPT_X0_OFF", "RPT_X8_OFF"):
            self.assertIn(".equ %s" % equ, probe)

    def test_the_jump_call_site_names_the_runtime_globals_it_passes(self):
        """Why x30/x0/x8 are UNKNOWN pre-handoff: their values come from
        runtime globals, not from anything the host can see."""
        entry = _read(ENTRY_C)
        call = entry[entry.index("jump_to_image_extended(((uint64_t)"):]
        call = call[:call.index(";")]
        self.assertIn("loader_xfer_recv_data", call)
        self.assertIn("gBootArgs", call)
        self.assertIn("gEntryPoint", call)


class TestSourceDerivableFields(unittest.TestCase):
    """Why current_el IS derivable and sctlr_el1 is only PARTIALLY so."""

    def test_pongo_operates_el1_only_registers_without_a_guard(self):
        """Writing VBAR_EL1/SCTLR_EL1 from C would trap below EL1, so
        execution at the jump cannot have been below EL1 -> CurrentEL is
        (1 << 2) == 0x4."""
        low = _read(LOWLEVEL_C)
        self.assertIn('"    msr vbar_el1, x0\\n"', low)
        self.assertIn('"    msr sctlr_el1, x3\\n"', low)
        mm = _read(MM_C)
        self.assertIn("set_vbar_el1((uint64_t)&exception_vector);", mm)
        self.assertIn("disable_mmu_el1();", mm)

    def test_get_el_primitive_exists_and_shifts_the_field_out(self):
        """get_el() proves EL1 is readable, and shows why its return is
        NOT the raw value the probe stores (the probe stores the raw MRS)."""
        low = _read(LOWLEVEL_C)
        self.assertIn('"_get_el:\\n"', low)
        self.assertIn('"    mrs x0, currentel\\n"', low)
        self.assertIn('"    lsr x0, x0, 2\\n"', low)
        self.assertIn("extern uint8_t get_el(void);", _read(PONGO_H))

    def test_disable_mmu_clears_exactly_two_sctlr_bits(self):
        """This is the WHOLE of what the source pins about SCTLR_EL1 --
        hence the full u64 stays UNKNOWN, not source-derived."""
        low = _read(LOWLEVEL_C)
        start = low.index('"_disable_mmu_el1:\\n"')
        end = low.index('"_panic_new_fp:\\n"') if '"_panic_new_fp:\\n"' in low \
            else len(low)
        body = low[start:end]
        ands = [m for m in re.findall(r"and x3, x3, #[^\n]*", body)]
        self.assertEqual(len(ands), 2, ands)
        self.assertIn("#(~1)", ands[0])   # SCTLR.M
        self.assertIn("#(~4)", ands[1])   # SCTLR.C
        # No other SCTLR bit is touched, so the remaining bits are not
        # pinned by source -- the UNKNOWN classification is correct.
        self.assertNotIn("orr x3, x3", body)

    def test_ticks_primitive_exists_but_cannot_reproduce_the_probe_sample(self):
        """_get_ticks() exists, so CNTPCT COULD be sampled pre-handoff --
        the design still refuses it, because the probe's own samples are
        taken later and around its own delay loop."""
        low = _read(LOWLEVEL_C)
        self.assertIn('"_get_ticks:\\n"', low)
        self.assertIn('"    mrs x0, cntpct_el0\\n"', low)
        self.assertIn("extern uint64_t get_ticks();", _read(PONGO_H))

        probe = _read(PROBE_S)
        first = probe.index("str     x14, [x12, #RPT_TVAL1_OFF]")
        second = probe.index("mrs     x14, CNTPCT_EL0", first)
        between = probe[first:second]
        self.assertIn("subs    x15, x15, #1", between,
                      "probe's second CNTPCT sample must follow its own "
                      "delay loop -- a pre-handoff sample is a different "
                      "instant and may not be substituted")


class TestBlockedPrimitives(unittest.TestCase):
    """The exact blockers: what the pinned PongoOS tree cannot do.
    
    M-P2.12 introduced the `regs` command which reads MIDR_EL1 and
    CNTFRQ_EL0 at EL1.  These tests verify the NEW expected behavior
    while preserving the PRE-HANDOFF blockers for the probe (which
    still cannot read these registers before `bootr`).
    """

    def _code_files(self):
        for root, _dirs, files in os.walk(PONGO_SRC):
            for fn in files:
                if fn.endswith((".c", ".h", ".S", ".s")):
                    yield os.path.join(root, fn)

    def test_midr_el1_read_now_exists_in_regs_command(self):
        """M-P2.12 adds MIDR_EL1 read in regs.c at EL1.  This is the
        INTENDED new behavior.  The PRE-HANDOFF probe blocker remains:
        the probe (pongo_probe.S) cannot read MIDR_EL1 before `bootr`."""
        hits = [p for p in self._code_files()
                if re.search(r"midr_el1", _read(p), re.I)]
        # Should now find exactly regs.c
        self.assertEqual(hits, [os.path.join(PONGO_SRC, "shell", "regs.c")])

    def test_cntfrq_el0_read_now_exists_in_regs_command(self):
        """M-P2.12 adds CNTFRQ_EL0 read in regs.c at EL1.  This is the
        INTENDED new behavior.  The PRE-HANDOFF probe blocker remains:
        the probe (pongo_probe.S) cannot read CNTFRQ_EL0 before `bootr`."""
        hits = [p for p in self._code_files()
                if re.search(r"cntfrq_el0", _read(p), re.I)]
        # Should now find exactly regs.c
        self.assertEqual(hits, [os.path.join(PONGO_SRC, "shell", "regs.c")])

    def test_shell_command_inventory_includes_regs_command(self):
        """M-P2.12 adds the `regs` command (total 40).  This test
        verifies the new expected inventory while still checking that
        no OTHER register/symbol commands exist."""
        names = set()
        for root, _dirs, files in os.walk(PONGO_SRC):
            for fn in files:
                if not fn.endswith(".c"):
                    continue
                text = _read(os.path.join(root, fn))
                names |= set(re.findall(
                    r'command_register\s*\(\s*"([^"]+)"', text))
        # Old count was 39; M-P2.12 adds `regs` -> 40
        self.assertEqual(len(names), 40, sorted(names))
        for need in ("md8", "peek", "physdump", "regs"):
            self.assertIn(need, names)
        # Still no OTHER register/symbol commands
        forbidden = {"mrs", "sysreg", "mrs_el1", "readreg", "symbol", "nm", "addr"}
        self.assertFalse(names & forbidden,
                          "unexpected register/symbol command: %r" % sorted(names & forbidden))
        # The `regs` command is the only new register-reading command
        self.assertIn("regs", names)

    def test_memory_read_commands_need_a_caller_supplied_address(self):
        """No shell command can be pointed at `loader_xfer_recv_data`
        without the host already knowing its (KASLR-slid) address."""
        main = _read(MAIN_C)
        self.assertIn("void md8_cmd(const char* cmd, char* args)", main)
        self.assertIn("uint64_t base = strtoull(args, NULL, 16);", main)
        # md8 refuses base == 0, i.e. it has no "find it for me" mode.
        self.assertIn("if (!size || !base) {", main)
        self.assertIn("void peek_cmd(const char* cmd, char* args)", main)
        self.assertIn("uint64_t addr = strtoull(args, NULL, 16);", main)
        self.assertIn("void phys_page_dump(const char* cmd, char* args)",
                      main)
        # physdump maps one physical page at a fixed scratch VA.
        self.assertIn("map_range(0xc10000000, base, 0x4000, 3, 0, true);",
                      main)

    def test_the_host_cannot_resolve_the_runtime_slide(self):
        """The three address-valued slots need `loader_xfer_recv_data`,
        whose runtime VA is slid -- asserted via the M-P2.4 tool's own
        contract rather than re-deriving it here."""
        addr_tool = os.path.join(HOST_DIR, "pongo_addr_resolve.py")
        self.assertTrue(os.path.isfile(addr_tool))
        text = _read(addr_tool)
        self.assertIn("UNRESOLVED", text)
        self.assertIn("--slide", text)


if __name__ == "__main__":
    print("NOTE: OFFLINE -- no hardware, no USB, no DFU, no boot; "
          "pongoos-src and the probe binary are unmodified.", flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)
