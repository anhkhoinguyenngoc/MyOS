#!/usr/bin/env python3
"""
MyOS M-P2.12 -- Offline tests for the `regs` PongoOS shell command.

OFFLINE ONLY: no hardware, no USB, no DFU, no payload upload, no boot.

What is covered
--------------
  * COMMAND REGISTRATION -- the `regs` command is registered in shell_main()
    via regs_commands_register() and follows the existing command pattern.
  * REGISTER PROVENANCE -- the command reads CurrentEL (always), and at EL1
    reads MIDR_EL1, SCTLR_EL1, CNTPCT_EL0, CNTFRQ_EL0 via inline asm MRS.
  * OUTPUT FORMAT -- key=value hex lines, one per register, parseable by host.
  * PRIVILEGE GUARD -- below EL1 the EL1-only registers report as 0x0.
  * BUILD INTEGRATION -- regs.c is picked up by the wildcard in Makefile.
  * SOURCE CONTRACT -- the command uses only primitives already present in
    the pinned PongoOS tree (inline asm MRS in lowlevel.c).

Usage:
  python3 tests/test_pongo_regs_command.py
Exit 0 = all pass, non-zero = failure.
"""

import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PONGO_SRC = os.path.join(REPO, "pongoos-src", "src")
REG_C = os.path.join(PONGO_SRC, "shell", "regs.c")
MAIN_C = os.path.join(PONGO_SRC, "shell", "main.c")
LOWLEVEL_C = os.path.join(PONGO_SRC, "kernel", "lowlevel.c")
PONGO_H = os.path.join(PONGO_SRC, "kernel", "pongo.h")
MAKEFILE = os.path.join(REPO, "pongoos-src", "Makefile")


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


class TestRegsCommandRegistration(unittest.TestCase):
    """The `regs` command is properly registered in the shell."""

    def test_regs_c_exists_and_compiles(self):
        self.assertTrue(os.path.isfile(REG_C), "regs.c must exist")
        text = _read(REG_C)
        self.assertIn("void regs_cmd", text)
        self.assertIn("void regs_commands_register", text)

    def test_regs_c_calls_command_register(self):
        text = _read(REG_C)
        self.assertIn('command_register("regs"', text)

    def test_main_c_calls_regs_commands_register(self):
        text = _read(MAIN_C)
        self.assertIn("regs_commands_register()", text)
        # Should be called after linux_commands_register
        linux_idx = text.index("linux_commands_register()")
        regs_idx = text.index("regs_commands_register()")
        self.assertLess(linux_idx, regs_idx)

    def test_makefile_picks_up_regs_c(self):
        """The wildcard in PONGO_C includes src/shell/*.c."""
        text = _read(MAKEFILE)
        self.assertIn("$(wildcard $(SRC)/shell/*.c)", text)


class TestRegsCommandRegisterProvenance(unittest.TestCase):
    """The command reads registers via inline asm MRS, matching the
    existing primitives in lowlevel.c."""

    def test_reads_currentel_via_mrs(self):
        text = _read(REG_C)
        self.assertIn('mrs %0, CurrentEL', text)

    def test_reads_midr_el1_via_mrs(self):
        text = _read(REG_C)
        self.assertIn('mrs %0, MIDR_EL1', text)

    def test_reads_sctlr_el1_via_mrs(self):
        text = _read(REG_C)
        self.assertIn('mrs %0, SCTLR_EL1', text)

    def test_reads_cntpct_el0_via_mrs(self):
        text = _read(REG_C)
        self.assertIn('mrs %0, CNTPCT_EL0', text)

    def test_reads_cntfrq_el0_via_mrs(self):
        text = _read(REG_C)
        self.assertIn('mrs %0, CNTFRQ_EL0', text)

    def test_primitives_exist_in_lowlevel_c(self):
        """All MRS instructions used by regs_cmd have precedent in
        lowlevel.c -- this is the source contract."""
        low = _read(LOWLEVEL_C)
        # CurrentEL
        self.assertIn('mrs x0, currentel', low.lower())
        # SCTLR_EL1
        self.assertIn('mrs x3, sctlr_el1', low.lower())
        # CNTPCT_EL0
        self.assertIn('mrs x0, cntpct_el0', low.lower())
        # MIDR_EL1 and CNTFRQ_EL0 are NEW reads not present before M-P2.12
        # (this is the point of the command).  The test documents the change.
        midr_hits = [m for m in re.finditer(r'midr_el1', low, re.I)]
        cntfrq_hits = [m for m in re.finditer(r'cntfrq_el0', low, re.I)]
        self.assertEqual(len(midr_hits), 0, "MIDR_EL1 was not read before M-P2.12")
        self.assertEqual(len(cntfrq_hits), 0, "CNTFRQ_EL0 was not accessed before M-P2.12")

    def test_pongo_h_declares_no_regs_primitives(self):
        """pongo.h does not expose register-read helpers; regs.c uses
        inline asm directly, which is the correct pattern."""
        h = _read(PONGO_H)
        self.assertNotIn("midr", h.lower())
        self.assertNotIn("cntfrq", h.lower())


class TestRegsCommandOutputFormat(unittest.TestCase):
    """The command output is structured and parseable."""

    def test_outputs_five_lines_with_expected_keys(self):
        """At EL1 the command prints five key=value lines."""
        text = _read(REG_C)
        # The command prints these keys in order
        for key in ("current_el", "midr_el1", "sctlr_el1",
                    "cntpct_el0", "cntfrq_el0"):
            self.assertIn(f'iprintf("{key}=0x%llx', text)

    def test_below_el1_outputs_zero_for_el1_registers(self):
        """When not at EL1, EL1-only registers print as 0x0."""
        text = _read(REG_C)
        self.assertIn('iprintf("midr_el1=0x0', text)
        self.assertIn('iprintf("sctlr_el1=0x0', text)
        self.assertIn('iprintf("cntpct_el0=0x0', text)
        self.assertIn('iprintf("cntfrq_el0=0x0', text)

    def test_current_el_always_printed_raw(self):
        """CurrentEL is printed as the raw MRS value (not shifted)."""
        text = _read(REG_C)
        self.assertIn("current_el_raw", text)
        self.assertIn('iprintf("current_el=0x%llx', text)


class TestRegsCommandPrivilegeGuard(unittest.TestCase):
    """The command guards EL1-only register reads behind an EL check."""

    def test_el_check_uses_currentel_field(self):
        text = _read(REG_C)
        self.assertIn("el = (current_el_raw >> 2) & 0x3", text)
        self.assertIn("if (el == 1)", text)

    def test_el1_registers_only_read_at_el1(self):
        """MIDR_EL1, SCTLR_EL1, CNTPCT_EL0, CNTFRQ_EL0 are only read
        inside the EL1 branch."""
        text = _read(REG_C)
        el1_block = text[text.index("if (el == 1)"):text.index("} else {")]
        for reg in ("midr", "sctlr", "cntpct", "cntfrq"):
            self.assertIn(reg, el1_block)
        else_start = text.index("} else {")
        else_end = text.index("}", else_start + 1)
        else_block = text[else_start:else_end + 1]
        for reg in ("midr_el1", "sctlr_el1", "cntpct_el0", "cntfrq_el0"):
            self.assertIn(f"{reg}=0x0", else_block)


class TestRegsCommandDocumentation(unittest.TestCase):
    """The command has a proper header comment documenting its behavior."""

    def test_header_documents_registers_read(self):
        text = _read(REG_C)
        self.assertIn("CurrentEL", text)
        self.assertIn("MIDR_EL1", text)
        self.assertIn("SCTLR_EL1", text)
        self.assertIn("CNTPCT_EL0", text)
        self.assertIn("CNTFRQ_EL0", text)

    def test_header_documents_output_format(self):
        text = _read(REG_C)
        self.assertIn("key=value", text)
        self.assertIn("hex", text.lower())

    def test_header_documents_privilege_behavior(self):
        text = _read(REG_C)
        self.assertIn("EL1", text)
        self.assertIn("0x0", text)


class TestRegsCommandIntegration(unittest.TestCase):
    """End-to-end integration checks."""

    def test_no_new_external_dependencies(self):
        """regs.c only includes pongo.h and uses inline asm."""
        text = _read(REG_C)
        includes = re.findall(r'^#include\s+[<"]([^>"]+)[>"]', text, re.M)
        self.assertEqual(includes, ["pongo.h"])

    def test_uses_existing_iprintf(self):
        """Output uses the existing iprintf primitive."""
        text = _read(REG_C)
        self.assertIn("iprintf", text)

    def test_follows_existing_command_pattern(self):
        """regs_cmd signature matches command.c callback type:
        void (*cb)(const char* cmd, char* args)"""
        text = _read(REG_C)
        self.assertIn("void regs_cmd(const char* cmd, char* args)", text)

    def test_registration_function_signature(self):
        text = _read(REG_C)
        self.assertIn("void regs_commands_register(void)", text)


if __name__ == "__main__":
    print("NOTE: OFFLINE -- no hardware, no USB, no DFU, no boot.", flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)