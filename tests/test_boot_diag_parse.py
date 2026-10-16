#!/usr/bin/env python3
"""
MyOS M-P3.0 — self-tests for tools/host/boot_diag_parse.py.

Host-side, stdlib only, no hardware, no QEMU, no toolchain.
Covers: valid pass log, explicit failure, exception/panic details,
missing markers (empty), malformed lines, truncated log, invalid stage
identifiers, duplicate events, timeout log, contradictory markers,
double-0x code tolerance, source-label passthrough, upstream single-0x
format, WARN-line tolerance, and missing-file CLI error.

Exit 0 = all pass. Exit 1 = any failure.
"""

import os
import subprocess
import sys

import importlib.util
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSER = os.path.join(REPO_ROOT, "tools", "host", "boot_diag_parse.py")

spec = importlib.util.spec_from_file_location("boot_diag_parse", PARSER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (" -- " + detail if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


VALID_PASS_LOG = """MyOS kernel entry
After uart init
After diag init
After vbar install
[....] BOOTSTRAP - BEGIN
[  OK ] BOOTSTRAP
[....] CPU_INIT - BEGIN
[  OK ] CPU_INIT
[....] EXCEPTION_INIT - BEGIN
[  OK ] EXCEPTION_INIT
[....] UART_INIT - BEGIN
[  OK ] UART_INIT
[....] TIMER_INIT - BEGIN
[  OK ] TIMER_INIT
[....] DRIVER_INIT - BEGIN
[  OK ] DRIVER_INIT
[....] USB_INIT - BEGIN
[  OK ] USB_INIT
[....] KERNEL_INIT - BEGIN
[  OK ] KERNEL_INIT
[....] KERNEL_MAIN - BEGIN

MyOS Kernel Runtime - QEMU virt
========================================

Boot stages completed:

MyOS Boot Diagnostic Report
===========================

[ OK ] BOOTSTRAP
[ OK ] CPU_INIT
[....] KERNEL_MAIN

MYOS_KERNEL_PASS
========================================
[  OK ] KERNEL_MAIN
"""

FAIL_LOG = """[....] BOOTSTRAP - BEGIN
[  OK ] BOOTSTRAP
[....] MEMORY_INIT - BEGIN
[FAIL] MEMORY_INIT

MyOS Boot Diagnostic
====================

[ OK ] BOOTSTRAP
[FAIL] MEMORY_INIT

Failure:
  Stage: MEMORY_INIT
  Error: MEMORY.FAILED.0x001
  Code: 0x0x00110001

BOOT FAILED
"""

EXC_LOG = """[....] UART_INIT - BEGIN

MyOS KERNEL PANIC - UNHANDLED EXCEPTION
==========================================

Stage: UART_INIT

Error: UART.FAILED.0x000 (EXCEPTION)

Exception Context:
  ESR_EL1: 0x0x96000010 (EC=0x25)
  FAR_EL1: 0x0x0000000009000000
  ELR_EL1: 0x0x0000000040080000
  SPSR_EL1: 0x0x00000000800003c5

SYSTEM HALTED
"""


def main():
    # 1. Valid pass log -> SUCCESS, correct last entered/OK, marker present.
    r = mod.parse_text(VALID_PASS_LOG)
    check("valid pass verdict SUCCESS", r["verdict"] == "SUCCESS", r["verdict"])
    check("valid pass last entered KERNEL_MAIN",
          r["last_entered"] == "KERNEL_MAIN", repr(r["last_entered"]))
    check("valid pass last OK KERNEL_MAIN",
          r["last_ok"] == "KERNEL_MAIN", repr(r["last_ok"]))
    check("valid pass no failure", r["first_failure"] is None,
          repr(r["first_failure"]))
    check("valid pass success marker", r["success_marker"] is True)
    check("valid pass not contradictory", r["contradictory"] is False)

    # 2. Explicit failure -> FAILURE with first failure + error/code.
    r = mod.parse_text(FAIL_LOG)
    check("failure verdict FAILURE", r["verdict"] == "FAILURE", r["verdict"])
    check("failure last entered MEMORY_INIT",
          r["last_entered"] == "MEMORY_INIT", repr(r["last_entered"]))
    check("failure last OK BOOTSTRAP",
          r["last_ok"] == "BOOTSTRAP", repr(r["last_ok"]))
    check("failure first failure MEMORY_INIT",
          r["first_failure"] == "MEMORY_INIT", repr(r["first_failure"]))
    check("failure error text", r["error_text"] == "MEMORY.FAILED.0x001",
          repr(r["error_text"]))
    check("failure double-0x code normalized", r["code"] == "0x00110001",
          repr(r["code"]))

    # 3. Exception/panic details extracted, double-0x tolerated.
    r = mod.parse_text(EXC_LOG)
    check("exception verdict FAILURE", r["verdict"] == "FAILURE", r["verdict"])
    check("exception ctx present", r["has_exception_ctx"] is True)
    check("exception panic marker", r["panic_marker"] is True)
    check("exception ESR", r["esr"] == "0x96000010", repr(r["esr"]))
    check("exception FAR", r["far"] == "0x0000000009000000", repr(r["far"]))
    check("exception ELR", r["elr"] == "0x0000000040080000", repr(r["elr"]))
    check("exception SPSR", r["spsr"] == "0x00000000800003c5", repr(r["spsr"]))
    check("exception EC", r["ec"] == "0x25", repr(r["ec"]))
    check("exception failure stage UART_INIT",
          r["first_failure"] == "UART_INIT", repr(r["first_failure"]))

    # 4. Missing markers (empty log) -> INCOMPLETE, no crash.
    r = mod.parse_text("")
    check("empty log INCOMPLETE", r["verdict"] == "INCOMPLETE", r["verdict"])
    check("empty log last entered NONE", r["last_entered"] is None)
    check("empty log no success", r["success_marker"] is False)

    # 5. Malformed lines are ignored deterministically.
    r = mod.parse_text("garbage line [\n[ OK \n[....]\nMYOS_KERNEL_PAS\n")
    check("malformed log INCOMPLETE", r["verdict"] == "INCOMPLETE", r["verdict"])
    check("malformed no begins", r["begins"] == [], repr(r["begins"]))
    check("malformed no oks", r["oks"] == [], repr(r["oks"]))

    # 6. Truncated log (BEGIN without OK, no markers) -> INCOMPLETE,
    #    last entered set, last OK behind.
    r = mod.parse_text("[....] BOOTSTRAP - BEGIN\n[  OK ] BOOTSTRAP\n"
                       "[....] CPU_INIT - BEGIN\n")
    check("truncated INCOMPLETE", r["verdict"] == "INCOMPLETE", r["verdict"])
    check("truncated last entered CPU_INIT",
          r["last_entered"] == "CPU_INIT", repr(r["last_entered"]))
    check("truncated last OK BOOTSTRAP",
          r["last_ok"] == "BOOTSTRAP", repr(r["last_ok"]))

    # 7. Invalid stage identifiers are recorded but never promoted.
    r = mod.parse_text("[....] FOO - BEGIN\n[  OK ] BAR\n[FAIL] BAZ\n"
                       "Stage: QUX\n")
    check("invalid stages recorded", len(r["invalid_stages"]) >= 3,
          repr(r["invalid_stages"]))
    check("invalid last entered NONE", r["last_entered"] is None,
          repr(r["last_entered"]))
    check("invalid last OK NONE", r["last_ok"] is None, repr(r["last_ok"]))
    check("invalid INCOMPLETE (no valid markers)",
          r["verdict"] == "INCOMPLETE", r["verdict"])
    # UNKNOWN sentinel must also be rejected as a stage event.
    r = mod.parse_text("[....] UNKNOWN - BEGIN\n[  OK ] UNKNOWN\n")
    check("UNKNOWN sentinel not promoted",
          r["last_entered"] is None and r["last_ok"] is None,
          repr((r["last_entered"], r["last_ok"])))

    # 8. Duplicate events: last occurrence wins, duplicates flagged.
    r = mod.parse_text("[....] BOOTSTRAP - BEGIN\n"
                       "[....] CPU_INIT - BEGIN\n"
                       "[  OK ] BOOTSTRAP\n"
                       "[  OK ] BOOTSTRAP\n")
    check("duplicate begins/oks tracked",
          r["duplicate_oks"] is True, repr(r["oks"]))
    check("duplicate last OK BOOTSTRAP",
          r["last_ok"] == "BOOTSTRAP", repr(r["last_ok"]))

    # 9. Timeout log -> INCOMPLETE with timeout hint.
    r = mod.parse_text("QEMU timed out after 10s (kernel may be running)\n"
                       "[....] BOOTSTRAP - BEGIN\n")
    check("timeout INCOMPLETE", r["verdict"] == "INCOMPLETE", r["verdict"])
    check("timeout hint detected", r["timeout_hint"] is True)

    # 10. Contradictory success+failure -> FAILURE + contradictory flag.
    r = mod.parse_text(FAIL_LOG + "\nMYOS_KERNEL_PASS\n")
    check("contradictory verdict FAILURE",
          r["verdict"] == "FAILURE", r["verdict"])
    check("contradictory flagged", r["contradictory"] is True)

    # 11. Source label passthrough (never inferred).
    s = mod.format_summary(mod.parse_text(VALID_PASS_LOG), "qemu")
    check("source qemu passthrough", "DIAG_SOURCE=qemu" in s, s.splitlines()[0])
    s = mod.format_summary(mod.parse_text(VALID_PASS_LOG), "physical")
    check("source physical passthrough", "DIAG_SOURCE=physical" in s)
    s = mod.format_summary(mod.parse_text(VALID_PASS_LOG), "unknown")
    check("source unknown default", "DIAG_SOURCE=unknown" in s)

    # 12. CLI exit codes: 0 success / 1 failure / 2 incomplete.
    def cli(text, source="qemu"):
        p = subprocess.run([sys.executable, PARSER, "--source", source, "-"],
                           input=text, capture_output=True, text=True)
        return p.returncode, p.stdout
    rc, out = cli(VALID_PASS_LOG)
    check("CLI success exit 0", rc == 0, "rc=%d" % rc)
    check("CLI success verdict line", "DIAG_VERDICT=SUCCESS" in out)
    rc, out = cli(FAIL_LOG)
    check("CLI failure exit 1", rc == 1, "rc=%d" % rc)
    check("CLI failure verdict line", "DIAG_VERDICT=FAILURE" in out)
    rc, out = cli("")
    check("CLI incomplete exit 2", rc == 2, "rc=%d" % rc)
    check("CLI incomplete verdict line", "DIAG_VERDICT=INCOMPLETE" in out)

    # Binary bytes on stdin (piped captures may carry non-UTF-8 UART bytes)
    # must decode with replacement, never crash: INCOMPLETE, no traceback
    # (review finding M-P3.0.2; a traceback exit 1 would mimic FAILURE).
    p = subprocess.run([sys.executable, PARSER, "--source", "qemu", "-"],
                       input=b"\x00\x01\xff\xfeMYOS",
                       capture_output=True)
    check("stdin binary no traceback", b"Traceback" not in p.stderr,
          p.stderr[:200].decode("utf-8", "replace"))
    check("stdin binary exit 2 INCOMPLETE", p.returncode == 2,
          "rc=%d" % p.returncode)
    check("stdin binary verdict line",
          b"DIAG_VERDICT=INCOMPLETE" in p.stdout, p.stdout)

    # 13. Upstream single-0x format (post-ffbf25d: "Code: 0x..." /
    #     "ESR_EL1: 0x...") parses identically to local double-0x form.
    #     Forward-compatibility lock for the pending upstream sync.
    upstream_exc = ("[....] UART_INIT - BEGIN\n"
                    "MyOS KERNEL PANIC - UNHANDLED EXCEPTION\n"
                    "Stage: UART_INIT\n"
                    "Error: UART.FAILED.0x000 (EXCEPTION)\n"
                    "Exception Context:\n"
                    "  ESR_EL1: 0x96000010 (EC=0x25)\n"
                    "  FAR_EL1: 0x0000000009000000\n"
                    "  ELR_EL1: 0x0000000040080000\n"
                    "  SPSR_EL1: 0x00000000800003c5\n"
                    "SYSTEM HALTED\n")
    r = mod.parse_text(upstream_exc)
    check("upstream single-0x verdict FAILURE",
          r["verdict"] == "FAILURE", r["verdict"])
    check("upstream single-0x ESR", r["esr"] == "0x96000010", repr(r["esr"]))
    check("upstream single-0x FAR", r["far"] == "0x0000000009000000",
          repr(r["far"]))
    upstream_fail = ("[....] BOOTSTRAP - BEGIN\n"
                     "[  OK ] BOOTSTRAP\n"
                     "[....] MEMORY_INIT - BEGIN\n"
                     "[FAIL] MEMORY_INIT\n"
                     "Failure:\n"
                     "  Stage: MEMORY_INIT\n"
                     "  Error: MEMORY.FAILED.0x001\n"
                     "  Code: 0x00110001\n"
                     "BOOT FAILED\n")
    r = mod.parse_text(upstream_fail)
    check("upstream single-0x code", r["code"] == "0x00110001", repr(r["code"]))
    check("upstream single-0x first failure",
          r["first_failure"] == "MEMORY_INIT", repr(r["first_failure"]))

    # 14. Upstream WARN line for out-of-order boot_diag_ok is ignored
    #     (informational only; must not create stage events or change verdict).
    r = mod.parse_text("[....] BOOTSTRAP - BEGIN\n"
                       "[  OK ] BOOTSTRAP\n"
                       "WARN: boot_diag_ok for CPU_INIT is ignored "
                       "(current stage mismatch)\n"
                       "[....] CPU_INIT - BEGIN\n"
                       "[  OK ] CPU_INIT\n"
                       "MYOS_KERNEL_PASS\n")
    check("WARN ignored verdict SUCCESS", r["verdict"] == "SUCCESS",
          r["verdict"])
    check("WARN ignored last OK CPU_INIT",
          r["last_ok"] == "CPU_INIT", repr(r["last_ok"]))

    # 15. Missing file -> CLI exit 3 (usage/file error, no crash).
    p = subprocess.run([sys.executable, PARSER, "--source", "qemu",
                        "/nonexistent-myos-log-xyz.log"],
                       capture_output=True, text=True)
    check("CLI missing file exit 3", p.returncode == 3,
          "rc=%d" % p.returncode)

    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("boot diag parse self-tests: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
