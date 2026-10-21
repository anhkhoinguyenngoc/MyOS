#!/usr/bin/env python3
"""Regression guard for invalid-stage bounds in QEMU boot diagnostics."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "kernel", "boot_diag.c")


def segmentation():
    text = open(SRC, encoding="utf-8").read()
    return text


def functions(text, name):
    pat = re.compile(r"__attribute__\(\(noreturn\)\)\s+void\s+%s\(.*?\n}\n" % name, re.S)
    return pat.findall(text)


def test_guarded_tables_are_not_indexed_by_raw_stage():
    text = segmentation()
    # The only remaining direct lookups are in paths explicitly guarded
    # by >=BOOT_STAGE_UNKNOWN (returns) or <BOOT_STAGE_UNKNOWN (branch).
    assert text.count("boot_stage_names[stage]") == 4
    assert "if (stage < BOOT_STAGE_UNKNOWN)" in text



def test_all_noreturn_stage_functions_use_safe_stage():
    text = segmentation()
    for fn in ("boot_diag_fail", "boot_diag_panic", "boot_diag_exception_handler"):
        matches = functions(text, fn)
        assert matches, "missing function %s" % fn
        src = matches[0]
        assert "safe_stage" in src, "%s lacks stage sanitization" % fn
        assert "BOOT_STAGE_UNKNOWN" in src, "%s permits invalid stages" % fn
        assert "diag_record_event" in src and "safe_stage" in src, "%s records unsafe stage" % fn


def test_stage_table_has_unknown_slot():
    text = segmentation()
    assert "[BOOT_STAGE_UNKNOWN]        = \"UNKNOWN\"" in text


if __name__ == "__main__":
    test_guarded_tables_are_not_indexed_by_raw_stage()
    test_all_noreturn_stage_functions_use_safe_stage()
    test_stage_table_has_unknown_slot()
    print("BOOT_DIAG_STAGE_GUARD TESTS: PASS")
