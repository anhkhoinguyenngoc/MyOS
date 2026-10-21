#!/usr/bin/env python3
"""
test_console_lifecycle.py — Host-side lifecycle test for kernel console.

Compiles kernel/console.c with a small fake-backend harness on the host
and runs it, asserting observable console lifecycle behavior without
pulling QEMU MMIO into the host binary. Discovers under make host-test-all.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_cc():
    for c in ("cc", "gcc", "clang"):
        try:
            r = subprocess.run([c, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode == 0:
                return c
        except FileNotFoundError:
            pass
    return None


def main():
    cc = find_cc()
    if cc is None:
        print("SKIP console lifecycle test: no host compiler found")
        return 0
    kernel_dir = os.path.join(ROOT, "kernel")
    test_dir = os.path.join(ROOT, "tests")
    console_c = os.path.join(kernel_dir, "console.c")
    harness_c = os.path.join(test_dir, "console_lifecycle_main.c")
    if not os.path.exists(console_c) or not os.path.exists(harness_c):
        print("SKIP console lifecycle test: missing sources")
        return 0
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, "console_lifecycle")
        cmd = [cc, "-Wall", "-Wextra", "-Werror", "-O2", "-I" + kernel_dir,
               console_c, harness_c, "-o", exe]
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print("FAIL console lifecycle test: compile failed", file=sys.stderr)
            return 1
        r = subprocess.run([exe])
        if r.returncode != 0:
            print("FAIL console lifecycle test: runtime failed", file=sys.stderr)
            return 1
    print("CONSOLE LIFECYCLE HOST TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())