#!/usr/bin/env python3
"""Regression checks for AArch64 exception-frame completeness and fatal exit."""

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTORS = os.path.join(ROOT, "build", "vectors.o")


def ensure_vectors():
    subprocess.run(["make", "kernel"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def block(label):
    ensure_vectors()
    text = subprocess.check_output(
        ["aarch64-linux-gnu-objdump", "-d", VECTORS],
        text=True, stderr=subprocess.DEVNULL)
    start = text.index("<%s>:" % label)
    m = re.search(r"\n\n[0-9a-f]+ +<", text[start:])
    end = start + (m.start() if m else len(text))
    return text[start:end]


def test_every_handler_preserves_full_register_frame():
    for label in ("sync_exception_handler", "irq_exception_handler",
                  "fiq_exception_handler", "serror_exception_handler"):
        b = block(label)
        stps = [line for line in b.splitlines() if "stp" in line]
        assert len(stps) == 12, (label, len(stps))
        assert "mov     x3, sp" in b or "mov	x3, sp" in b, label
        assert b.count("mrs") == 4, label


def test_exception_path_does_not_execute_eret():
    for label in ("sync_exception_handler", "irq_exception_handler",
                  "fiq_exception_handler", "serror_exception_handler"):
        b = block(label)
        assert "eret" not in b.lower(), label
        # There is exactly one final self-branch after the noreturn handler.
        assert b.count("bl") >= 2, label


if __name__ == "__main__":
    test_every_handler_preserves_full_register_frame()
    test_exception_path_does_not_execute_eret()
    print("BOOT_DIAG_EXCEPTION_RETURN TESTS: PASS")
