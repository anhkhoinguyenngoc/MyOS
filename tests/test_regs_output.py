#!/usr/bin/env python3
"""
MyOS M-P2.32 -- offline tests for the `regs` output contract, the host-side
parser (tools/host/regs_output.py) and its fixtures.

OFFLINE ONLY: no hardware, no USB, no DFU, no payload upload, no boot.
Fixtures are SYNTHETIC text and never assert an Apple A8 runtime value.

Coverage
--------
  * SOURCE CONTRACT -- the parser's field set/order is re-derived from the
    real regs.c and must match; the format is `%llx` (lowercase, minimal).
  * ACCEPTED -- complete EL1 report; below-EL1 all-zero report; shell
    prompt / ANSI / CRLF framing; an arbitrary-but-architecturally-valid
    report (proves no hardware value is asserted).
  * REJECTED -- missing field, malformed hex, leading zeros, overflow,
    duplicate field, truncated output, unexpected extra output, and
    syntactically valid values that break a documented architectural rule.

Usage:
  python3 tests/test_regs_output.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "regs_output.py")
REGS_C = os.path.join(REPO, "pongoos-src", "src", "shell", "regs.c")


def _load():
    spec = importlib.util.spec_from_file_location("regs_output", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ro = _load()

# ---------------------------------------------------------------------------
# Fixtures (synthetic; labels say what each one is)
# ---------------------------------------------------------------------------

# A complete, plausible EL1 report.  Values are ARBITRARY and are NOT Apple
# A8 expectations; only their shape is contractual.
FIXTURE_VALID_EL1 = (
    "current_el=0x4\n"
    "midr_el1=0x410fd083\n"
    "sctlr_el1=0x30d00800\n"
    "cntpct_el0=0x1a2b3c4d5e\n"
    "cntfrq_el0=0x16e3600\n"
)

# Below EL1: EL1-only registers read exactly 0x0 (not omitted).
FIXTURE_VALID_BELOW_EL1 = (
    "current_el=0x0\n"
    "midr_el1=0x0\n"
    "sctlr_el1=0x0\n"
    "cntpct_el0=0x0\n"
    "cntfrq_el0=0x0\n"
)

# Real transport framing: bare CR + "pongoOS> " prompt (no newline) with an
# ANSI colour escape, and CRLF line endings.
FIXTURE_VALID_FRAMED = (
    "\r\x1b[31mpongoOS> current_el=0x4\r\n"
    "midr_el1=0x410fd083\r\n"
    "sctlr_el1=0x30d00800\r\n"
    "cntpct_el0=0x1a2b3c4d5e\r\n"
    "cntfrq_el0=0x16e3600\r\n"
    "\rpongoOS> "
)

FIXTURE_MISSING_FIELD = (
    "current_el=0x4\n"
    "midr_el1=0x410fd083\n"
    "cntpct_el0=0x1a2b3c4d5e\n"
    "cntfrq_el0=0x16e3600\n"
)

FIXTURE_DUPLICATE_FIELD = (
    "current_el=0x4\n"
    "midr_el1=0x410fd083\n"
    "midr_el1=0x410fd083\n"
    "sctlr_el1=0x30d00800\n"
    "cntpct_el0=0x1\n"
    "cntfrq_el0=0x16e3600\n"
)

FIXTURE_TRUNCATED = (
    "current_el=0x4\n"
    "midr_el1=0x410fd083\n"
    "sctlr_el1=0x30d00800\n"
    "cntpct_el0=0x1a2b3c4d5e\n"
    "cntfrq_el0=0x"
)

FIXTURE_EXTRA_OUTPUT = (
    "current_el=0x4\n"
    "midr_el1=0x410fd083\n"
    "sctlr_el1=0x30d00800\n"
    "cntpct_el0=0x1a2b3c4d5e\n"
    "cntfrq_el0=0x16e3600\n"
    "Bad command: regsa\n"
)

FIXTURE_ARCH_VIOLATION = (
    "current_el=0x5\n"          # bits[1:0] must be RES0
    "midr_el1=0x1ffffffff\n"    # exceeds 32-bit MIDR_EL1
    "sctlr_el1=0x30d00800\n"
    "cntpct_el0=0x1a2b3c4d5e\n"
    "cntfrq_el0=0x100000000\n"  # exceeds 32-bit CNTFRQ_EL0
)

# Malformed value shapes (each in a full report so only one issue fires).
MALFORMED_VALUES = {
    "empty_hex":        "current_el=0x",
    "uppercase_x":      "current_el=0X4",
    "uppercase_digits": "current_el=0x4A",
    "no_prefix":        "current_el=4",
    "negative":         "current_el=0x-1",
    "non_hex_digit":    "current_el=0xg",
    "leading_zero":     "current_el=0x04",
    "too_wide":         "current_el=0x10000000000000000",
}


def _report_with(first_line, rest=None):
    rest = rest or (
        "midr_el1=0x410fd083\n"
        "sctlr_el1=0x30d00800\n"
        "cntpct_el0=0x1a2b3c4d5e\n"
        "cntfrq_el0=0x16e3600\n"
    )
    return first_line + "\n" + rest


def _issues_of(result):
    return {i["type"] for i in result["issues"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSourceContract(unittest.TestCase):
    """The parser mirrors the real regs.c, so any drift fails here."""

    def test_keys_match_source(self):
        with open(REGS_C, "r", encoding="utf-8", errors="replace") as fh:
            src_keys = ro.keys_from_source(fh.read())
        self.assertEqual(src_keys, ro.KEYS)

    def test_tool_source_check_passes(self):
        self.assertEqual(ro.main(["--source-check", "--regs-source", REGS_C]), 0)

    def test_key_count_is_five(self):
        self.assertEqual(len(ro.KEYS), 5)


class TestAccepted(unittest.TestCase):
    def test_valid_el1(self):
        r = ro.parse(FIXTURE_VALID_EL1)
        self.assertTrue(r["ok"], r["issues"])
        self.assertEqual(r["fields"]["current_el"], 0x4)
        self.assertEqual(r["fields"]["midr_el1"], 0x410fd083)
        self.assertEqual(r["el"], 1)
        self.assertEqual(r["order"], list(ro.KEYS))

    def test_valid_below_el1(self):
        r = ro.parse(FIXTURE_VALID_BELOW_EL1)
        self.assertTrue(r["ok"], r["issues"])
        self.assertEqual(r["el"], 0)
        for key in ("midr_el1", "sctlr_el1", "cntpct_el0", "cntfrq_el0"):
            self.assertEqual(r["fields"][key], 0)

    def test_valid_framed_prompt_and_crlf(self):
        r = ro.parse(FIXTURE_VALID_FRAMED)
        self.assertTrue(r["ok"], r["issues"])

    def test_arbitrary_values_accepted_no_hw_assertion(self):
        """A syntactically valid report with arbitrary values must pass:
        the tool asserts bounds, never a specific device value."""
        odd = ("current_el=0xc\n"
               "midr_el1=0x0\n"
               "sctlr_el1=0xffffffffffffffff\n"
               "cntpct_el0=0xffffffffffffffff\n"
               "cntfrq_el0=0x1\n")
        r = ro.parse(odd)
        self.assertTrue(r["ok"], r["issues"])


class TestRejected(unittest.TestCase):
    def test_missing_field(self):
        r = ro.parse(FIXTURE_MISSING_FIELD)
        self.assertFalse(r["ok"])
        self.assertIn("missing_field", _issues_of(r))

    def test_duplicate_field(self):
        r = ro.parse(FIXTURE_DUPLICATE_FIELD)
        self.assertFalse(r["ok"])
        self.assertIn("duplicate_field", _issues_of(r))

    def test_truncated(self):
        r = ro.parse(FIXTURE_TRUNCATED)
        self.assertFalse(r["ok"])
        self.assertIn("malformed_value", _issues_of(r))
        self.assertIn("missing_field", _issues_of(r))

    def test_extra_output(self):
        r = ro.parse(FIXTURE_EXTRA_OUTPUT)
        self.assertFalse(r["ok"])
        self.assertIn("unexpected_line", _issues_of(r))

    def test_malformed_values(self):
        malformed_family = {"malformed_value", "leading_zero", "overflow"}
        for name, first in MALFORMED_VALUES.items():
            with self.subTest(name=name):
                r = ro.parse(_report_with(first))
                self.assertFalse(r["ok"], name)
                self.assertTrue(_issues_of(r) & malformed_family, name)

    def test_leading_zero_is_specific(self):
        r = ro.parse(_report_with("current_el=0x04"))
        self.assertIn("leading_zero", _issues_of(r))

    def test_overflow_is_specific(self):
        r = ro.parse(_report_with("current_el=0x10000000000000000"))
        self.assertIn("overflow", _issues_of(r))

    def test_unknown_field(self):
        r = ro.parse(_report_with("current_el=0x4", rest=(
            "midr_el1=0x1\nsctlr_el1=0x2\ncntpct_el0=0x3\ncntfrq_el0=0x4\n"
            "totally_new=0x5\n")))
        self.assertFalse(r["ok"])
        self.assertIn("unknown_field", _issues_of(r))


class TestArchitecturalBounds(unittest.TestCase):
    def test_arch_violations_rejected(self):
        r = ro.parse(FIXTURE_ARCH_VIOLATION)
        self.assertFalse(r["ok"])
        self.assertEqual(
            sum(1 for i in r["issues"] if i["type"] == "arch_violation"), 3)

    def test_arch_violations_are_separable(self):
        """With arch enforcement off the same text is syntactically valid --
        proving the rejection is an architectural bound, not a syntax rule."""
        r = ro.parse(FIXTURE_ARCH_VIOLATION, enforce_arch=False)
        self.assertTrue(r["ok"], r["issues"])

    def test_arch_rules_are_bounds_not_hardware_equalities(self):
        names = set()
        for name, predicate, why in ro.ARCH_RULES:
            self.assertIn(name, ro.KEYS)
            self.assertTrue(why, "rule must document its justification")
            names.add(name)
            # A bound accepts a family of values, never exactly one.
            self.assertTrue(predicate(0))
        # current_el accepts exactly the four architecturally valid raws.
        ce = {n: p for n, p, _ in ro.ARCH_RULES}["current_el"]
        accepted = [v for v in range(0, 0x20) if ce(v)]
        self.assertEqual(accepted, [0x0, 0x4, 0x8, 0xC])

    def test_no_hardware_value_is_pinned(self):
        """The five runtime fields are explicitly declared unverified."""
        for key in ro.KEYS:
            self.assertIn(key, ro.UNVERIFIED_RUNTIME_VALUES)


if __name__ == "__main__":
    print("NOTE: OFFLINE -- synthetic fixtures; no hardware, no boot.",
          flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)
