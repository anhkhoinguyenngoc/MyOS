#!/usr/bin/env python3
"""Focused tests for the declared-dependency freshness check added to
tests/check_build_integrity.py (M7.30).

Covers, with temp files and FIXED mtimes (deterministic, no clock skews):
  * parse_make_rules: continuations, comments, recipes, assignments,
    order-only (`|`) prerequisites, `$(VAR)` expansion;
  * artifact_freshness outcomes: fresh / stale (leaf source),
    stale (intermediate object newer than the artifact),
    missing-artifact / missing-source / no-rule;
  * closure wiring against the REAL Makefile (content-based, read-only).

No repository writes, no builds, no network.  PYTHONDONTWRITEBYTECODE=1
recommended so no __pycache__ is created.
"""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))

import check_build_integrity as cbi  # noqa: E402  (path set up above)

# Fixed mtimes (arbitrary epoch seconds, far in the past): each file gets a
# distinct value so "newer than" is unambiguous on any filesystem.
T1, T2, T3, T4, T5 = 1_000_000_000, 1_000_000_100, 1_000_000_200, \
    1_000_000_300, 1_000_000_400

SYNTH_MAKEFILE = """\
BUILD := build
KERNEL_ELF := $(BUILD)/myos_kernel.elf
PLATFORM ?= qemu-virt
# full-line comment
$(BUILD)/start.o: platform/start.S platform/defs.h | $(BUILD)
\t$(CC) -c $< -o $@
$(BUILD)/main.o: kernel/main.c kernel/main.h \\
                 kernel/console.h | $(BUILD)
\t$(CC) -c $< -o $@
$(KERNEL_ELF): $(BUILD)/start.o $(BUILD)/main.o \\
               platform/kernel.ld
\t$(LD) -T platform/kernel.ld -o $@
"""
SYNTH_VARS = {"BUILD": "build", "KERNEL_ELF": "build/myos_kernel.elf"}


def _write(path: str, content: bytes, mtime: float) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    os.utime(path, (mtime, mtime))


def test_parse_make_rules():
    """Rules must be extracted with continuations joined, recipes/comments/
    assignments skipped, order-only prereqs dropped, and $(VAR)s expanded."""
    rules = cbi.parse_make_rules(SYNTH_MAKEFILE, SYNTH_VARS)
    assert rules["build/start.o"] == ["platform/start.S", "platform/defs.h"], (
        f"order-only prereq must be dropped, got: {rules.get('build/start.o')}"
    )
    assert rules["build/main.o"] == [
        "kernel/main.c", "kernel/main.h", "kernel/console.h"
    ], f"continuation not joined: {rules.get('build/main.o')}"
    assert rules["build/myos_kernel.elf"] == [
        "build/start.o", "build/main.o", "platform/kernel.ld"
    ], f"$(VAR) expansion failed: {rules.get('build/myos_kernel.elf')}"
    assert "PLATFORM" not in rules, "assignment must not parse as a rule"
    assert all("$(BUILD)" not in " ".join(v) for v in rules.values()), (
        "unexpanded variable left in prerequisites"
    )
    print("PASS: test_parse_make_rules")


def test_fresh_when_artifact_newer():
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "kernel/main.c"), b"src", T1)
        _write(os.path.join(td, "kernel/main.h"), b"hdr", T2)
        _write(os.path.join(td, "platform/kernel.ld"), b"ld", T3)
        _write(os.path.join(td, "build/main.o"), b"obj", T4)
        _write(os.path.join(td, "build/myos_kernel.elf"), b"elf", T5)
        rules = {
            "build/main.o": ["kernel/main.c", "kernel/main.h"],
            "build/myos_kernel.elf": ["build/main.o", "platform/kernel.ld"],
        }
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", rules, td)
        assert status == "fresh", f"expected fresh, got {status}: {detail}"
        assert "prerequisites checked" in detail, detail
    print("PASS: test_fresh_when_artifact_newer")


def test_stale_leaf_source_named():
    """A leaf source newer than the artifact must be STALE and the detail
    must name that source (not just 'something is stale')."""
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "kernel/main.c"), b"src", T3)  # NEWER than elf
        _write(os.path.join(td, "platform/kernel.ld"), b"ld", T1)
        _write(os.path.join(td, "build/main.o"), b"obj", T2)
        _write(os.path.join(td, "build/myos_kernel.elf"), b"elf", T2)
        rules = {
            "build/main.o": ["kernel/main.c"],
            "build/myos_kernel.elf": ["build/main.o", "platform/kernel.ld"],
        }
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", rules, td)
        assert status == "stale", f"expected stale, got {status}: {detail}"
        assert "kernel/main.c" in detail and "newer than" in detail, detail
    print("PASS: test_stale_leaf_source_named")


def test_stale_intermediate_object():
    """An intermediate object newer than the artifact is stale even when all
    leaf sources are older (recompiled but not relinked)."""
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "kernel/main.c"), b"src", T1)
        _write(os.path.join(td, "build/main.o"), b"obj", T3)  # NEWER than elf
        _write(os.path.join(td, "build/myos_kernel.elf"), b"elf", T2)
        rules = {
            "build/main.o": ["kernel/main.c"],
            "build/myos_kernel.elf": ["build/main.o"],
        }
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", rules, td)
        assert status == "stale", f"expected stale, got {status}: {detail}"
        assert "build/main.o" in detail, detail
    print("PASS: test_stale_intermediate_object")


def test_missing_artifact_reported():
    """A missing artifact must return the explicit missing-artifact status
    (no crash, no pass)."""
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "kernel/main.c"), b"src", T1)
        rules = {"build/myos_kernel.elf": ["kernel/main.c"]}
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", rules, td)
        assert status == "missing-artifact", (status, detail)
    print("PASS: test_missing_artifact_reported")


def test_missing_source_reported():
    """A declared prerequisite file that does not exist must return the
    explicit missing-source status naming it (unverifiable, not 'fresh')."""
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "build/myos_kernel.elf"), b"elf", T5)
        _write(os.path.join(td, "kernel/main.c"), b"src", T1)
        rules = {
            "build/myos_kernel.elf": ["kernel/main.c", "kernel/absent.c"],
        }
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", rules, td)
        assert status == "missing-source", (status, detail)
        assert "kernel/absent.c" in detail, detail
    print("PASS: test_missing_source_reported")


def test_no_rule_reported():
    """An artifact the Makefile declares no (real) rule for must return
    no-rule, not a vacuous 'fresh'."""
    with tempfile.TemporaryDirectory() as td:
        _write(os.path.join(td, "build/myos_kernel.elf"), b"elf", T5)
        status, detail = cbi.artifact_freshness("build/myos_kernel.elf", {}, td)
        assert status == "no-rule", (status, detail)
    print("PASS: test_no_rule_reported")


def test_real_makefile_closures():
    """Against the REAL Makefile: each artifact's parsed closure must reach
    its actual leaf sources and linker script.  Content-based (no mtimes),
    read-only; guards the wiring between parse_make_rules and the build."""
    with open(os.path.join(REPO, "Makefile"), encoding="utf-8") as fh:
        text = fh.read()
    variables = cbi.makefile_vars(text)
    rules = cbi.parse_make_rules(text, variables)

    def closure(target: str) -> set[str]:
        seen: set[str] = set()

        def walk(t: str) -> None:
            for pre in rules.get(t, []):
                if pre in seen:
                    continue
                seen.add(pre)
                walk(pre)

        walk(target)
        return seen

    kernel = closure("build/myos_kernel.elf")
    for leaf in ("kernel/main.c", "platform/qemu-virt/start.S",
                 "platform/qemu-virt/kernel.ld", "arch/aarch64/vectors.S",
                 "kernel/console.c"):
        assert leaf in kernel, f"{leaf} missing from kernel closure"

    raw_bin = closure("build/raw_entry.bin")
    for leaf in ("platform/apple/a8/raw_entry.S",
                 "platform/apple/a8/raw_main.c",
                 "platform/apple/a8/raw_vectors.S",
                 "platform/apple/a8/raw_entry.ld"):
        assert leaf in raw_bin, f"{leaf} missing from raw_entry.bin closure"

    m1_bin = closure("build/myos_m1.bin")
    for leaf in ("platform/apple/a8/m1_entry.S",
                 "platform/apple/a8/boot_diag_m1.c",
                 "platform/apple/a8/m1.ld", "kernel/boot_diag.h"):
        assert leaf in m1_bin, f"{leaf} missing from myos_m1.bin closure"

    probe_elf = closure("build/pongo_probe.elf")
    assert "platform/apple/a8/pongo_probe.S" in probe_elf, sorted(probe_elf)
    assert "platform/apple/a8/pongo_probe.ld" in probe_elf, sorted(probe_elf)
    print("PASS: test_real_makefile_closures")


RUN = [
    test_parse_make_rules,
    test_fresh_when_artifact_newer,
    test_stale_leaf_source_named,
    test_stale_intermediate_object,
    test_missing_artifact_reported,
    test_missing_source_reported,
    test_no_rule_reported,
    test_real_makefile_closures,
]

if __name__ == "__main__":
    for fn in RUN:
        fn()
    print(f"OK ({len(RUN)} tests)")
