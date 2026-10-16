#!/usr/bin/env python3
"""
MyOS M-P3.0 — boot diagnostic log parser (host-side, stdlib only).

Consumes captured serial/UART output from the MyOS boot diagnostic
framework (arch/aarch64/boot_diag.c + boot/aarch64/start.S + kernel/main.c)
and reports:
  - last stage entered (last "[....] STAGE - BEGIN")
  - last stage confirmed successful (last "[  OK ] STAGE")
  - first explicit failure (first "[FAIL] STAGE" / "BOOT FAILED" /
    "KERNEL PANIC" / exception block)
  - exception/panic details if present (ESR/FAR/ELR/SPSR, error, code)
  - whether a complete success marker (MYOS_KERNEL_PASS) appeared
  - whether the evidence is from QEMU or physical hardware (--source flag)

This parser never touches hardware. It reads a file (or stdin) and prints
a stable, machine-parseable summary. It never infers physical success from
QEMU output: the source label is operator-supplied; default is "unknown".

Stable output (stdout, always printed on successful parse):
  DIAG_SOURCE=<qemu|physical|unknown>
  DIAG_LAST_ENTERED=<STAGE|NONE>
  DIAG_LAST_OK=<STAGE|NONE>
  DIAG_FIRST_FAILURE=<STAGE|NONE>
  DIAG_SUCCESS_MARKER=<present|absent>
  DIAG_FAILURE_MARKER=<present|absent>
  DIAG_PANIC_MARKER=<present|absent>
  DIAG_EXCEPTION_CTX=<present|absent>
  DIAG_CONTRADICTORY=<yes|no>
  DIAG_VERDICT=<SUCCESS|FAILURE|INCOMPLETE>
  (plus human-readable lines prefixed with "# " and, when present,
   DIAG_ERROR=<text> / DIAG_CODE=<hex> / DIAG_ESR=... etc.)

Exit codes:
  0 = SUCCESS (MYOS_KERNEL_PASS present, no explicit failure, no contradiction)
  1 = FAILURE (explicit BOOT FAILED / KERNEL PANIC / exception, or
      contradictory success+failure markers)
  2 = INCOMPLETE (no success marker and no explicit failure: missing output,
      truncated log, or timeout)
  3 = usage/file error

Robustness: missing markers, malformed lines, truncated logs, invalid stage
identifiers, duplicate events, timeout text, and contradictory markers are
all handled deterministically and covered by tests/test_boot_diag_parse.py.

Operator guide (capture, save, interpret):
  1. Capture QEMU serial output to a file (repo Makefile `qemu` target tees
     /tmp/qemu_output.log; prefer `qemu-system-aarch64 ... > capture.log 2>&1`
     so the exit status is not masked by a pipe).
  2. Save the file under a uniquely named evidence directory, e.g.
     /tmp/myos-physical-diagnostics-<timestamp>/, with the exact command and
     exit code recorded alongside.
  3. Run: python3 tools/host/boot_diag_parse.py --source qemu capture.log
  4. Interpret: SUCCESS requires MYOS_KERNEL_PASS plus no failure markers.
     FAILURE means the FIRST failure stage + error/code lines identify the
     last good stage (DIAG_LAST_OK). INCOMPLETE means the log ends mid-stage
     (compare DIAG_LAST_ENTERED vs DIAG_LAST_OK) — re-run or check timeout.
  5. Physical hardware: pass --source physical ONLY for logs captured from
     real iPhone hardware via a legitimate, approved interface. QEMU logs
     must never be relabeled as physical.

QEMU vs physical: this tool reports the operator-supplied source label only.
MYOS_KERNEL_PASS in a QEMU log is NOT a physical boot proof.
"""

import argparse
import re
import sys

KNOWN_STAGES = (
    "BOOTSTRAP",
    "CPU_INIT",
    "MEMORY_INIT",
    "MMU_INIT",
    "EXCEPTION_INIT",
    "UART_INIT",
    "TIMER_INIT",
    "DRIVER_INIT",
    "USB_INIT",
    "KERNEL_INIT",
    "KERNEL_MAIN",
)
KNOWN_SET = set(KNOWN_STAGES)

# "[....] STAGE - BEGIN" (framework) — also matches report "[....] STAGE"
RE_BEGIN = re.compile(r"\[\s*\.\.\.\.\s*\]\s+([A-Za-z0-9_]+)(?:\s+-\s+BEGIN)?")
# "[  OK ] STAGE" / "[ OK ] STAGE"
RE_OK = re.compile(r"\[\s*OK\s*\]\s+([A-Za-z0-9_]+)")
# "[FAIL] STAGE" (fail block)
RE_FAIL = re.compile(r"\[FAIL\]\s+([A-Za-z0-9_]+)")
RE_STAGE_LINE = re.compile(r"^\s*Stage:\s+([A-Za-z0-9_]+)\s*$")
RE_ERROR_LINE = re.compile(r"^\s*Error:\s+(.+?)\s*$")
RE_CODE_LINE = re.compile(r"^\s*Code:\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+|[0-9A-Fa-f]+)\s*$")
RE_ESR = re.compile(r"ESR_EL1:\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+)")
RE_FAR = re.compile(r"FAR_EL1:\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+)")
RE_ELR = re.compile(r"ELR_EL1:\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+)")
RE_SPSR = re.compile(r"SPSR_EL1:\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+)")
RE_EC = re.compile(r"\(EC\s*=\s*(0[xX](?:0[xX])?[0-9A-Fa-f]+)\)")


def _normalize_code(raw):
    """Handle both '0x00110001' and buggy '0x0x00110001' forms."""
    s = raw.strip()
    # Collapse duplicated 0x prefix (local boot_diag.c prints "0x" + "0x...").
    while s.lower().startswith("0x0x"):
        s = "0x" + s[4:]
    return s


def parse_text(text):
    """Parse log text. Returns dict with summary fields (deterministic)."""
    lines = text.splitlines()
    begins = []  # (stage, lineno) for valid BEGIN events, in order
    oks = []  # (stage, lineno) for valid OK events, in order
    fails = []  # (stage, lineno) for valid [FAIL] events, in order
    invalid_stages = []  # (raw, lineno) for unknown stage names
    failure_stage_lines = []  # Stage: lines
    error_text = None
    code_raw = None
    esr = far = elr = spsr = ec = None
    success_marker = False
    boot_failed = False
    panic_marker = False
    halted = False
    timeout_hint = False

    for idx, line in enumerate(lines, start=1):
        if "MYOS_KERNEL_PASS" in line:
            success_marker = True
        if "BOOT FAILED" in line:
            boot_failed = True
        if "KERNEL PANIC" in line:
            panic_marker = True
        if "SYSTEM HALTED" in line:
            halted = True
        upper = line.upper()
        if "TIMED OUT" in upper or "TIMEOUT" in upper or "TIMED-OUT" in upper:
            timeout_hint = True

        m = RE_BEGIN.search(line)
        if m:
            raw = m.group(1).upper()
            if raw in KNOWN_SET:
                begins.append((raw, idx))
            else:
                # UNKNOWN sentinel and any other unknown token are invalid
                # for BEGIN/OK tracking; record but do not promote.
                invalid_stages.append((raw, idx))
            # A line is either BEGIN-ish or OK-ish; OK pattern cannot also
            # match here, so continue scanning other patterns below.
        m = RE_OK.search(line)
        if m:
            raw = m.group(1).upper()
            if raw in KNOWN_SET:
                oks.append((raw, idx))
            else:
                invalid_stages.append((raw, idx))
        m = RE_FAIL.search(line)
        if m:
            raw = m.group(1).upper()
            if raw in KNOWN_SET:
                fails.append((raw, idx))
            else:
                invalid_stages.append((raw, idx))
        m = RE_STAGE_LINE.match(line)
        if m:
            failure_stage_lines.append((m.group(1).upper(), idx))
        m = RE_ERROR_LINE.match(line)
        if m and error_text is None:
            error_text = m.group(1).strip()
        m = RE_CODE_LINE.match(line)
        if m and code_raw is None:
            code_raw = _normalize_code(m.group(1))
        m = RE_ESR.search(line)
        if m and esr is None:
            esr = _normalize_code(m.group(1))
        m = RE_FAR.search(line)
        if m and far is None:
            far = _normalize_code(m.group(1))
        m = RE_ELR.search(line)
        if m and elr is None:
            elr = _normalize_code(m.group(1))
        m = RE_SPSR.search(line)
        if m and spsr is None:
            spsr = _normalize_code(m.group(1))
        m = RE_EC.search(line)
        if m and ec is None:
            ec = _normalize_code(m.group(1))

    last_entered = begins[-1][0] if begins else None
    last_ok = oks[-1][0] if oks else None
    # First explicit failure: earliest of [FAIL] event, BOOT FAILED line,
    # KERNEL PANIC line, or exception context presence.
    first_failure = None
    if fails:
        first_failure = fails[0][0]
    elif failure_stage_lines and (boot_failed or panic_marker):
        cand = failure_stage_lines[0][0]
        first_failure = cand if cand in KNOWN_SET else cand

    has_exception_ctx = esr is not None or far is not None or elr is not None
    explicit_failure = boot_failed or panic_marker or bool(fails) or has_exception_ctx
    contradictory = success_marker and explicit_failure

    if contradictory or explicit_failure:
        verdict = "FAILURE"
    elif success_marker:
        verdict = "SUCCESS"
    else:
        verdict = "INCOMPLETE"

    return {
        "total_lines": len(lines),
        "begins": begins,
        "oks": oks,
        "fails": fails,
        "invalid_stages": invalid_stages,
        "failure_stage_lines": failure_stage_lines,
        "last_entered": last_entered,
        "last_ok": last_ok,
        "first_failure": first_failure,
        "success_marker": success_marker,
        "boot_failed": boot_failed,
        "panic_marker": panic_marker,
        "halted": halted,
        "timeout_hint": timeout_hint,
        "error_text": error_text,
        "code": code_raw,
        "esr": esr,
        "far": far,
        "elr": elr,
        "spsr": spsr,
        "ec": ec,
        "has_exception_ctx": has_exception_ctx,
        "explicit_failure": explicit_failure,
        "contradictory": contradictory,
        "verdict": verdict,
        "duplicate_begins": len(begins) != len({b[0] for b in begins}),
        "duplicate_oks": len(oks) != len({o[0] for o in oks}),
    }


def format_summary(result, source):
    """Build stable summary lines (machine-parseable DIAG_* lines)."""
    def stage(v):
        return v if v is not None else "NONE"

    out = []
    out.append("DIAG_SOURCE=%s" % source)
    out.append("DIAG_LAST_ENTERED=%s" % stage(result["last_entered"]))
    out.append("DIAG_LAST_OK=%s" % stage(result["last_ok"]))
    out.append("DIAG_FIRST_FAILURE=%s" % stage(result["first_failure"]))
    out.append("DIAG_SUCCESS_MARKER=%s" % ("present" if result["success_marker"] else "absent"))
    out.append("DIAG_FAILURE_MARKER=%s" % ("present" if result["boot_failed"] else "absent"))
    out.append("DIAG_PANIC_MARKER=%s" % ("present" if result["panic_marker"] else "absent"))
    out.append("DIAG_EXCEPTION_CTX=%s" % ("present" if result["has_exception_ctx"] else "absent"))
    out.append("DIAG_CONTRADICTORY=%s" % ("yes" if result["contradictory"] else "no"))
    out.append("DIAG_VERDICT=%s" % result["verdict"])
    if result["error_text"] is not None:
        out.append("DIAG_ERROR=%s" % result["error_text"])
    if result["code"] is not None:
        out.append("DIAG_CODE=%s" % result["code"])
    if result["esr"] is not None:
        out.append("DIAG_ESR=%s" % result["esr"])
    if result["far"] is not None:
        out.append("DIAG_FAR=%s" % result["far"])
    if result["elr"] is not None:
        out.append("DIAG_ELR=%s" % result["elr"])
    if result["spsr"] is not None:
        out.append("DIAG_SPSR=%s" % result["spsr"])
    return "\n".join(out) + "\n"


def human_lines(result, source):
    """Human-readable interpretation (comment lines)."""
    h = []
    h.append("# source: %s (operator-supplied; QEMU output is never physical proof)" % source)
    h.append("# lines scanned: %d; stage-BEGIN events: %d; stage-OK events: %d; "
             "[FAIL] events: %d; invalid stage tokens: %d"
             % (result["total_lines"], len(result["begins"]), len(result["oks"]),
                len(result["fails"]), len(result["invalid_stages"])))
    if result["last_entered"] is None and result["last_ok"] is None \
            and not result["success_marker"] and not result["explicit_failure"]:
        h.append("# no diagnostic markers found: empty log, wrong file, or "
                 "output was never captured")
    elif result["verdict"] == "SUCCESS":
        h.append("# SUCCESS: MYOS_KERNEL_PASS observed with no failure markers; "
                 "last OK stage %s" % (result["last_ok"] or "NONE"))
    elif result["verdict"] == "FAILURE":
        if result["contradictory"]:
            h.append("# CONTRADICTION: both MYOS_KERNEL_PASS and failure markers "
                     "present; treat as FAILURE and inspect the log")
        h.append("# FAILURE: last entered %s; last OK %s; first failure %s"
                 % (result["last_entered"] or "NONE",
                    result["last_ok"] or "NONE",
                    result["first_failure"] or "NONE"))
        if result["has_exception_ctx"]:
            h.append("# exception context present (ESR/FAR/ELR/SPSR above)")
    else:
        h.append("# INCOMPLETE: no success marker and no explicit failure; "
                 "last entered %s vs last OK %s%s"
                 % (result["last_entered"] or "NONE",
                    result["last_ok"] or "NONE",
                    "; timeout hint present" if result["timeout_hint"] else ""))
        h.append("# likely truncated log, timeout, or early hang before KERNEL_MAIN")
    if result["invalid_stages"]:
        raws = ", ".join(sorted({r for r, _ in result["invalid_stages"]}))
        h.append("# note: invalid stage token(s) ignored: %s" % raws)
    if result["duplicate_begins"] or result["duplicate_oks"]:
        h.append("# note: duplicate stage events observed (re-entry or "
                 "re-transmitted log lines)")
    return "\n".join(h) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Parse MyOS boot diagnostic UART logs and summarize "
                    "last entered/OK/failure stages (read-only, no hardware).")
    ap.add_argument("logfile", nargs="?", default="-",
                    help="log file to parse ('-' or omitted = stdin)")
    ap.add_argument("--source", choices=("qemu", "physical", "unknown"),
                    default="unknown",
                    help="evidence origin label (default: unknown; QEMU logs "
                         "must use qemu, never physical)")
    args = ap.parse_args(argv)

    try:
        if args.logfile == "-":
            text = sys.stdin.read()
        else:
            with open(args.logfile, "r", errors="replace") as fh:
                text = fh.read()
    except OSError as exc:
        print("file error: %s" % exc, file=sys.stderr)
        return 3

    result = parse_text(text)
    sys.stdout.write(human_lines(result, args.source))
    sys.stdout.write(format_summary(result, args.source))
    if result["verdict"] == "SUCCESS":
        return 0
    if result["verdict"] == "FAILURE":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
