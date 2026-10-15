#!/usr/bin/env python3
"""Deterministic checks for AArch64 exception vectors."""

import re
import subprocess
import sys

REPO = None
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
ELF = os.path.join(BUILD, "myos_kernel.elf")


def ensure_kernel():
    # Makefile's kernel target also produces required ELF/vectors.
    r = subprocess.run(["make", "kernel"], cwd=ROOT, text=True,
                       capture_output=True, check=False)
    assert r.returncode == 0, r.stderr


def objdump_vectors():
    ensure_kernel()
    tmp = "/tmp/opencode/myos-vectors.bin"
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    subprocess.run(
        ["aarch64-linux-gnu-objcopy", "-O", "binary",
         "--only-section=.text.vectors", os.path.join(BUILD, "vectors.o"), tmp],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(tmp, "rb") as f:
        return f.read()


def test_vector_table_is_2k():
    ensure_kernel()
    b = objdump_vectors()[:16*128]
    assert len(b) == 16 * 128, "expected 2048-byte vector table, got %d" % len(b)


def test_synchronous_slot_branches_to_handler():
    b = objdump_vectors()[:16*128]
    # First uncondition branch word to sync_exception_handler.
    instr = int.from_bytes(b[:4], "little")
    assert (instr & 0xfc000000) == 0x14000000, "first slot must be B"


def test_all_slots_are_branches():
    b = objdump_vectors()[:16*128]
    for i in range(16):
        instr = int.from_bytes(b[i*128:i*128+4], "little")
        assert (instr & 0xfc000000) == 0x14000000, "slot %d does not begin with B" % i


if __name__ == "__main__":
    ensure_kernel()
    test_vector_table_is_2k()
    test_synchronous_slot_branches_to_handler()
    test_all_slots_are_branches()
    print("BOOT_DIAG_EXCEPTION_VECTOR TESTS: PASS")
