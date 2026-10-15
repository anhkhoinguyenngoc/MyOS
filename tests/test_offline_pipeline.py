#!/usr/bin/env python3
"""Regression checks for offline test pipeline status propagation."""

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


import re


def script(name):
    with open(os.path.join(ROOT, "tests", name), encoding="utf-8") as f:
        return f.read()


def test_shell_scripts_enable_pipefail():
    for name in ("offline_m1.sh", "test_boot_diag.sh",
                 "test_boot_diag_extended.sh"):
        text = script(name)
        assert "pipefail" in text, name
        assert re.search(r"set\s+-[A-Za-z]*o\s+pipefail", text), name


def test_scripts_syntax():
    for name in ("offline_m1.sh", "test_boot_diag.sh",
                 "test_boot_diag_extended.sh"):
        path = os.path.join(ROOT, "tests", name)
        result = subprocess.run(["bash", "-n", path],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True)
        assert result.returncode == 0, result.stdout


if __name__ == "__main__":
    test_shell_scripts_enable_pipefail()
    test_scripts_syntax()
    print("OFFLINE PIPELINE TESTS: PASS")
