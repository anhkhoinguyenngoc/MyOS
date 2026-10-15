#!/usr/bin/env python3
"""Regression checks for AArch64 exception entry register and context capture ordering."""

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTORS = os.path.join(ROOT, "build", "vectors.o")


def ensure_vectors():
    subprocess.run(["make", "kernel"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def disassembly():
    ensure_vectors()
    return subprocess.check_output(
        ["aarch64-linux-gnu-objdump", "-d", VECTORS],
        text=True, stderr=subprocess.DEVNULL)


def find_sync_handler(text):
    marker = "<sync_exception_handler>:"
    start = text.index(marker)
    m = re.search(r"\n<[A-Za-z_][A-Za-z0-9_]*>:", text[start + 1:])
    end = start + (m.start() + 1 if m else len(text))
    return text[start:end]


def test_exception_entry_preserves_general_registers():
    text = disassembly()
    block = find_sync_handler(text)
    assert "stp" in block
    for regs in ("x0, x1", "x2, x3", "x4, x5", "x6, x7",
                 "x8, x9", "x10, x11", "x12, x13", "x14, x15",
                 "x16, x17", "x18, x30"):
        pat = r"stp\s+%s" % regs.replace(",", r",\s*")
        assert re.search(pat, block) is not None, regs
    assert re.search(r"mrs\s+x4,\s*esr_el1", block) is not None
    assert re.search(r"mrs\s+x5,\s*far_el1", block) is not None
    assert re.search(r"mrs\s+x6,\s*elr_el1", block) is not None
    assert re.search(r"mrs\s+x7,\s*spsr_el1", block) is not None
    assert re.search(r"mov\s+x3,\s*sp", block) is not None

def test_exception_context_slot_order():
    text = disassembly()
    block = find_sync_handler(text)
    m = re.search(r"mrs\s+x7,\s*spsr_el1", block)
    assert m is not None
    idx = m.start()
    tail = block[idx:]
    syndrome_stps = [line for line in tail.splitlines() if "stp" in line][:2]
    assert "x6, x7" in syndrome_stps[0]
    assert "x4, x5" in syndrome_stps[1]















if __name__ == "__main__":
    test_exception_entry_preserves_general_registers()
    test_exception_context_slot_order()
    print("BOOT_DIAG_EXCEPTION_ENTRY TESTS: PASS")
