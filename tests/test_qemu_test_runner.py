#!/usr/bin/env python3
"""Regression checks for the QEMU test runner's exit-status handling."""

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAKEFILE = os.path.join(ROOT, "Makefile")


def read_makefile():
    with open(MAKEFILE, "r", encoding="utf-8") as f:
        return f.read()


def test_qemu_target_does_not_mask_command_status():
    text = read_makefile()
    # The instruction pipeline `command | tee` would report tee's status,
    # not QEMU's status, even when QEMU crashed before emitting a marker.
    assert "2>&1 | tee /tmp/qemu_output.log" not in text
    assert "> /tmp/qemu_output.log 2>&1" in text
    match = re.search(r"timeout\s+$(QEMU_TIMEOUT)\s+\$\(QEMU\)", text)
    assert match is not None or "timeout $(QEMU_TIMEOUT) $(QEMU)" in text


def test_qemu_target_captures_and_reports_exit_code():
    text = read_makefile()
    assert text.count("EXIT_CODE=$$?") == 1
    assert all(marker in text for marker in (
        '[ $$EXIT_CODE -eq 124 ]',
        '[ $$EXIT_CODE -eq 0 ]',
    ))


def test_qemu_target_parses():
    result = subprocess.run(
        ["make", "-n", "qemu"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout


if __name__ == "__main__":
    test_qemu_target_does_not_mask_command_status()
    test_qemu_target_captures_and_reports_exit_code()
    test_qemu_target_parses()
    print("QEMU TEST RUNNER: PASS")
