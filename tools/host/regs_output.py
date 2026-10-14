#!/usr/bin/env python3
"""
MyOS M-P2.32 -- host-side parser/validator for the PongoOS `regs` shell
command TEXT output (offline, stdlib only, no hardware).

WHAT THIS PARSES
----------------
The `regs` command (pongoos-src/src/shell/regs.c) prints, over the PongoOS
USB/UART shell, one key=value line per register, in this fixed order:

    current_el=<hex>
    midr_el1=<hex>
    sctlr_el1=<hex>
    cntpct_el0=<hex>
    cntfrq_el0=<hex>

FORMAT FACTS (derived from the C source, not assumed)
-----------------------------------------------------
  * Each line is `<key>=0x<hex>` where the value is produced by newlib
    iprintf("%llx"): a LITERAL `0x` from the format string followed by
    LOWERCASE hexadecimal with MINIMAL width -- no leading zeros, no
    zero-padding, no sign, no `0X`, no `+`.
  * One field per line; the line terminator is LF.  The device shell also
    emits a bare CR plus a `pongoOS> ` prompt (command.c:139-143) with no
    trailing newline, so a captured transcript's first field line is
    commonly prompt-prefixed and ANSI escape sequences may surround the
    prompt.  Both are tolerated here.
  * All five keys always appear.  Below EL1 the four EL1-only registers
    are still printed, reading exactly `0x0` (they are NOT omitted).

EVIDENCE BOUNDARY
-----------------
This tool checks SYNTAX plus a small set of ARCHITECTURAL bounds.  It never
asserts a specific Apple A8 runtime value: `current_el`, `midr_el1`,
`sctlr_el1`, `cntpct_el0` and `cntfrq_el0` at run time are UNKNOWN here and
must not be predicted.  A syntactically valid value is accepted unless it
violates a documented architectural rule (RES0 fields, 32-bit registers).

Usage:
  python3 tools/host/regs_output.py --file transcript.txt [--json]
  python3 tools/host/regs_output.py --stdin
  python3 tools/host/regs_output.py --source-check
Exit 0 = accepted, 1 = rejected (contract/arch violation), 2 = usage/source.
"""

import argparse
import json
import os
import re
import sys

# The five fields, in the order regs.c prints them.  Kept in sync with the
# source by --source-check and by tests/test_regs_output.py.
KEYS = ("current_el", "midr_el1", "sctlr_el1", "cntpct_el0", "cntfrq_el0")

SHELL_PROMPT = "pongoOS>"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FIELD_LINE_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+)=(?P<rest>.*)$")
_HEX_VALUE_RE = re.compile(r"^0x(?P<hex>[0-9a-f]+)$")

U64_MAX = 0xFFFFFFFFFFFFFFFF
U32_MAX = 0xFFFFFFFF

# Architectural bounds (ARM ARM), NOT observations of any particular chip.
# Each entry: (field, predicate, human justification).
ARCH_RULES = (
    ("current_el",
     lambda v: (v & 0x3) == 0 and v <= 0xC,
     "CurrentEL bits[1:0] are RES0 and bits[63:4] are RES0, so only "
     "0x0/0x4/0x8/0xC are architecturally representable"),
    ("midr_el1",
     lambda v: v <= U32_MAX,
     "MIDR_EL1 is a 32-bit register; bits[63:32] are RES0"),
    ("cntfrq_el0",
     lambda v: v <= U32_MAX,
     "CNTFRQ_EL0 is a 32-bit register; bits[63:32] are RES0"),
)

# Fields whose value on Apple A8 is NOT known offline and must never be
# asserted by a fixture.  Informational only.
UNVERIFIED_RUNTIME_VALUES = ("current_el", "midr_el1", "sctlr_el1",
                             "cntpct_el0", "cntfrq_el0")

DEFAULT_REGS_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pongoos-src", "src", "shell", "regs.c")


def keys_from_source(text):
    """Extract the ordered field keys from regs.c's `iprintf("<key>=0x%llx` calls.

    This is the single source of truth for the field set and order.
    """
    out = []
    for m in re.finditer(r'iprintf\(\s*"([A-Za-z0-9_]+)=0x%llx', text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return tuple(out)


def _normalise(text):
    """Split into lines, tolerating CR / CRLF framing."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _strip_prompt(line):
    """Remove ANSI escapes and a leading `pongoOS> ` prompt, if present.

    Returns the content that follows the prompt (possibly empty).
    """
    line = _ANSI_RE.sub("", line)
    idx = line.rfind(SHELL_PROMPT)
    if idx != -1:
        line = line[idx + len(SHELL_PROMPT):]
    return line.strip()


def _check_arch(field, value):
    for name, predicate, why in ARCH_RULES:
        if name == field and not predicate(value):
            return why
    return None


def parse(text, *, enforce_arch=True):
    """Parse `regs` output text into a structured, validated result.

    Never raises on malformed input -- problems are returned in `issues`.
    Returns a dict with: ok, fields (key->int), raw (key->str), order,
    el (int|None), issues (list of dicts).
    """
    issues = []
    fields = {}
    raw = {}
    order = []
    seen = set()

    for lineno, original in enumerate(_normalise(text), start=1):
        line = _strip_prompt(original)
        if line == "":
            continue  # blank line / bare prompt: ignorable framing

        m = _FIELD_LINE_RE.match(line)
        if not m:
            issues.append({"type": "unexpected_line", "line": lineno,
                           "text": original.rstrip("\n"),
                           "detail": "not a key=value field line"})
            continue

        key, rest = m.group("key"), m.group("rest")

        if key not in KEYS:
            issues.append({"type": "unknown_field", "line": lineno, "field": key,
                           "text": line, "detail": f"unknown field {key!r}"})
            continue

        vm = _HEX_VALUE_RE.match(rest)
        if not vm:
            issues.append({"type": "malformed_value", "line": lineno,
                           "field": key, "text": line,
                           "detail": "value is not 0x<lowercase-hex>"})
            continue

        digits = vm.group("hex")
        if len(digits) > 1 and digits[0] == "0":
            issues.append({"type": "leading_zero", "line": lineno, "field": key,
                           "text": line,
                           "detail": "value has leading zeros, but %llx emits "
                                     "minimal width"})
            continue
        if len(digits) > 16:
            issues.append({"type": "overflow", "line": lineno, "field": key,
                           "text": line, "detail": "value exceeds 64 bits"})
            continue

        value = int(digits, 16)

        if key in seen:
            issues.append({"type": "duplicate_field", "line": lineno,
                           "field": key, "text": line,
                           "detail": f"{key} already seen"})
            continue
        seen.add(key)

        if enforce_arch:
            why = _check_arch(key, value)
            if why:
                issues.append({"type": "arch_violation", "line": lineno,
                               "text": line, "detail": why, "field": key,
                               "value": value})
                continue

        fields[key] = value
        raw[key] = rest
        order.append(key)

    for key in KEYS:
        if key not in seen:
            issues.append({"type": "missing_field", "line": 0, "text": "",
                           "detail": f"required field {key} not found"})

    if order and order != [k for k in KEYS if k in fields]:
        issues.append({"type": "out_of_order", "line": 0, "text": "",
                       "detail": f"fields appeared as {order}"})

    el = None
    if "current_el" in fields:
        el = (fields["current_el"] >> 2) & 0x3

    return {
        "ok": not issues,
        "keys_present": order,
        "fields": fields,
        "raw": raw,
        "order": order,
        "el": el,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result, as_json):
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print("regs output contract check")
    rejected = {}
    for issue in result["issues"]:
        if issue.get("field"):
            rejected[issue["field"]] = (issue.get("text", ""), issue["type"])
    for key in KEYS:
        if key in result["fields"]:
            print("  %-12s = %s" % (key, result["raw"][key]))
        elif key in rejected:
            text, kind = rejected[key]
            print("  %-12s = <rejected: %s (%s)>" % (key, text, kind))
        else:
            print("  %-12s = <missing>" % key)
    if result["el"] is not None:
        print("  derived EL   : %d" % result["el"])
    for issue in result["issues"]:
        print("  ISSUE [%s] line %d: %s" % (issue["type"], issue["line"],
                                           issue["detail"]))
    print("  state        : %s" % ("ACCEPTED" if result["ok"] else "REJECTED"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Parse/validate PongoOS `regs` shell output (offline).")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--file", help="transcript file containing regs output")
    src.add_argument("--stdin", action="store_true",
                     help="read the transcript from stdin")
    src.add_argument("--source-check", action="store_true",
                     help="verify KEYS matches pongoos-src/.../regs.c")
    ap.add_argument("--regs-source", default=DEFAULT_REGS_SOURCE,
                    help="path to regs.c for --source-check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.source_check:
        try:
            with open(args.regs_source, "r", encoding="utf-8",
                      errors="replace") as fh:
                src_keys = keys_from_source(fh.read())
        except OSError as exc:
            print("regs_output: ERROR: %s" % exc, file=sys.stderr)
            return 2
        if src_keys != KEYS:
            print("regs_output: ERROR: KEYS %r != source %r" % (KEYS, src_keys),
                  file=sys.stderr)
            return 2
        print("regs_output: source-check OK: %s" % (", ".join(KEYS)))
        return 0

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("regs_output: ERROR: %s" % exc, file=sys.stderr)
            return 2
    elif args.stdin:
        text = sys.stdin.read()
    else:
        ap.error("one of --file/--stdin/--source-check is required")

    result = parse(text)
    _print_result(result, args.json)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
