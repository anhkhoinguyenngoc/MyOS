#!/usr/bin/env python3
"""
Offline tests for the M1 pwned-DFU command-block wire format.

What is tested here:
  * tests/m1_proto_test.c — unit tests for tools/host/m1_proto.h: explicit
    little-endian encode/decode, byte-exact exec-block construction (the
    expected bytes are hand-written, not produced by the code under test),
    block-size limits, and the payload loader's reject-don't-truncate rule.
  * the layout contract between m1_proto.h and m1_a8.h: M1_PROTO_HEADER_LEN
    (80 bytes) must equal M1_EXEC_HEADER_LEN, and M1_MAX_PAYLOAD must be
    exactly M1_DFU_MAX_XFER - M1_EXEC_HEADER_LEN.

What is NOT tested here: any device, USB, DFU transfer, or libusb path.
No iPhone is present and none is required.
"""

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS_SRC = os.path.join(REPO, "tests", "m1_proto_test.c")
HARNESS_BIN = os.path.join(REPO, "build", "test_m1_proto")
PROTO_H = os.path.join(REPO, "tools", "host", "m1_proto.h")
A8_H = os.path.join(REPO, "platform", "apple", "a8", "m1_a8.h")

passed = 0
failed = 0


def report(name, ok, detail=""):
    global passed, failed
    if ok:
        print(f"PASS: {name}")
        passed += 1
    else:
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
        failed += 1


def read_defines(path):
    """Collect `#define NAME <expr>` object-like macros from a header."""
    defs = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*#define\s+(\w+)\s+([^(].*)$", line)
            if not m:
                continue
            val = m.group(2).split("/*")[0].strip()
            if val:
                defs[m.group(1)] = val
    return defs


def eval_macro(defs, name, _depth=0):
    """Evaluate an integer macro, expanding other integer macros recursively."""
    if _depth > 10:
        raise AssertionError(f"macro expansion too deep at {name}")
    if name not in defs:
        raise AssertionError(f"macro {name} not defined")
    expr = defs[name]
    # Drop integer literal suffixes (u/U/l/L) such as `0x50u`, but only when
    # attached to a numeric literal (never to an identifier like DFU).
    expr = re.sub(r"\b(0[xX][0-9A-Fa-f]+|[0-9]+)[uUlL]+\b", r"\1", expr)
    # Expand bare identifiers that are themselves macros.
    def repl(mo):
        return "(" + str(eval_macro(defs, mo.group(0), _depth + 1)) + ")"
    expr = re.sub(r"\b([A-Za-z_]\w*)\b", repl, expr)
    # `expr` is now only integer literal arithmetic from headers we control.
    value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - trusted input
    return int(value)


def test_layout_contract():
    proto_defs = read_defines(PROTO_H)
    a8_defs = read_defines(A8_H)

    proto_v = eval_macro(proto_defs, "M1_PROTO_HEADER_LEN")
    exec_v = eval_macro(a8_defs, "M1_EXEC_HEADER_LEN")
    max_payload_v = eval_macro(a8_defs, "M1_MAX_PAYLOAD")
    max_xfer_v = eval_macro(a8_defs, "M1_DFU_MAX_XFER")

    report("M1_PROTO_HEADER_LEN == M1_EXEC_HEADER_LEN (80)",
           proto_v == exec_v == 0x50,
           f"proto={proto_v:#x} exec={exec_v:#x}")
    report("M1_MAX_PAYLOAD == M1_DFU_MAX_XFER - M1_EXEC_HEADER_LEN (1968)",
           max_payload_v == max_xfer_v - exec_v == 1968,
           f"max_payload={max_payload_v} xfer={max_xfer_v:#x}")


def build_harness():
    os.makedirs(os.path.join(REPO, "build"), exist_ok=True)
    cmd = ["gcc", "-O2", "-Wall", "-Wextra", "-I" + REPO,
           "-o", HARNESS_BIN, HARNESS_SRC]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        return False
    # gcc must emit no warnings for this file.
    if proc.stderr.strip():
        print("compiler stderr:", proc.stderr.strip(), file=sys.stderr)
        return False
    return True


def test_c_harness():
    proc = subprocess.run([HARNESS_BIN], cwd=REPO, capture_output=True, text=True)
    out = proc.stdout
    print(out.rstrip())
    if proc.stderr.strip():
        print("harness stderr:", proc.stderr.strip(), file=sys.stderr)

    report("C harness exits 0", proc.returncode == 0,
           f"rc={proc.returncode}")
    report("C harness reports PASS", "M1_PROTO_TEST: PASS" in out)
    report("C harness reports no FAIL lines",
           not any(line.startswith("FAIL ") for line in out.splitlines()))


def main():
    if not build_harness():
        print("ERROR: could not compile tests/m1_proto_test.c")
        return 1

    try:
        test_layout_contract()
        test_c_harness()
    except Exception as e:  # noqa: BLE001 - report and fail
        report("m1_proto test raised", False, repr(e))

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
