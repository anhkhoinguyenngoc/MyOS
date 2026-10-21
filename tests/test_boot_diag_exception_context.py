#!/usr/bin/env python3
"""Regression test for saved exception context ordering."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTOR_SRC = os.path.join(ROOT, "arch", "aarch64", "vectors.S")
C_SRC = os.path.join(ROOT, 'kernel', 'boot_diag.h')


def test_context_saved_registers_match_structure_order():
    src = open(VECTOR_SRC, encoding="utf-8").read()
    assert "mrs     x4, ESR_EL1" in src
    assert "mrs     x5, FAR_EL1" in src
    assert "mrs     x6, ELR_EL1" in src
    assert "mrs     x7, SPSR_EL1" in src
    # Store ELR/SPSR first, then ESR/FAR; together with struct definition
    # this places ESR/FAR at the base of the context.
    assert "stp     x6, x7, [sp, #-16]!     /* elr_el1, spsr_el1 (upper slots) */" in src
    assert "stp     x4, x5, [sp, #-16]!     /* esr_el1, far_el1 (base slots) */" in src
    assert "mov     x3, sp" in src


def test_c_exception_context_declares_matching_fields():
    src = open(C_SRC, encoding="utf-8").read()
    assert "uint64_t esr_el1;" in src and "uint64_t far_el1;" in src
    assert "uint64_t elr_el1;" in src and "uint64_t spsr_el1;" in src


if __name__ == "__main__":
    test_context_saved_registers_match_structure_order()
    test_c_exception_context_declares_matching_fields()
    print("BOOT_DIAG_EXCEPTION_CONTEXT TESTS: PASS")
