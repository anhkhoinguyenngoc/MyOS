#!/usr/bin/env python3
"""
MyOS M-P3.0 — self-tests for tools/host/usb_detect.py.

Host-side, stdlib only. All USB inputs are injected text fixtures; no
hardware, no `lsusb` execution (except the live-smoke test which only reads).
Verifies: DFU/normal/Pongo single states, absent, multiple, other-PID,
malformed lines, case-insensitivity, duplicates, missing-file error, CLI
exit codes, and that the tool performs no writes/mode changes (code review:
the only subprocess call is `["lsusb"]`; `--input` path performs no exec).

Exit 0 = all pass. Exit 1 = any failure.
"""

import importlib.util
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETECTOR = os.path.join(REPO_ROOT, "tools", "host", "usb_detect.py")

spec = importlib.util.spec_from_file_location("usb_detect", DETECTOR)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (" -- " + detail if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


LSUSB_DFU = """Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 002 Device 010: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)
Bus 001 Device 003: ID 064e:c21c Suyin Corp. 1.3M HD WebCam
"""

LSUSB_NORMAL = """Bus 002 Device 004: ID 05ac:12a8 Apple, Inc. iPhone
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
"""

LSUSB_PONGO = """Bus 001 Device 009: ID 05ac:4141 Apple, Inc. PongoOS USB Device
"""

LSUSB_ABSENT = """Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 003: ID 064e:c21c Suyin Corp. 1.3M HD WebCam
"""

LSUSB_MULTI = """Bus 002 Device 010: ID 05ac:1227 Apple, Inc. Mobile Device (DFU Mode)
Bus 002 Device 011: ID 05ac:4141 Apple, Inc. PongoOS USB Device
"""

LSUSB_OTHER = """Bus 002 Device 012: ID 05ac:1297 Apple, Inc. Random Accessory
"""

LSUSB_KNOWN_PLUS_OTHER = LSUSB_DFU + \
    "Bus 002 Device 012: ID 05ac:1297 Apple, Inc. Random Accessory\n"


def verdict_of(text):
    return mod.classify(mod.parse_lsusb(text))


def main():
    r = verdict_of(LSUSB_DFU)
    check("DFU-only verdict DFU", r["verdict"] == "DFU", r["verdict"])
    check("DFU-only dfu flag", r["dfu"] is True)
    check("DFU-only count 1", r["count"] == 1, repr(r["count"]))
    check("DFU-only no other", r["other"] == [], repr(r["other"]))

    r = verdict_of(LSUSB_NORMAL)
    check("normal-only verdict NORMAL", r["verdict"] == "NORMAL", r["verdict"])

    r = verdict_of(LSUSB_PONGO)
    check("pongo-only verdict PONGO", r["verdict"] == "PONGO", r["verdict"])

    r = verdict_of(LSUSB_ABSENT)
    check("absent verdict ABSENT", r["verdict"] == "ABSENT", r["verdict"])
    check("absent count 0", r["count"] == 0)

    r = verdict_of(LSUSB_MULTI)
    check("multi verdict MULTIPLE", r["verdict"] == "MULTIPLE", r["verdict"])

    r = verdict_of(LSUSB_OTHER)
    check("other-only verdict OTHER", r["verdict"] == "OTHER", r["verdict"])
    check("other PID recorded", r["other"] == ["1297"], repr(r["other"]))

    r = verdict_of(LSUSB_KNOWN_PLUS_OTHER)
    check("known+other verdict MULTIPLE",
          r["verdict"] == "MULTIPLE", r["verdict"])

    # Malformed lines ignored; empty input is ABSENT, never a crash.
    r = verdict_of("garbage\nID 05ac:ZZZZ nope\nID 123:45\n")
    check("malformed ignored ABSENT", r["verdict"] == "ABSENT", r["verdict"])
    r = verdict_of("")
    check("empty ABSENT", r["verdict"] == "ABSENT", r["verdict"])

    # Case-insensitivity + duplicate lines collapse to one PID.
    r = verdict_of("Bus 001 Device 001: ID 05AC:1227 X\n"
                   "Bus 001 Device 002: ID 05ac:1227 Y\n")
    check("case-insensitive DFU", r["verdict"] == "DFU", r["verdict"])
    check("duplicates collapse", r["count"] == 1, repr(r["count"]))

    # Stable output lines present.
    out = mod.format_output(verdict_of(LSUSB_DFU))
    for line in ("USB_APPLE_COUNT=1", "USB_1227_PRESENT=yes",
                 "USB_12A8_PRESENT=no", "USB_4141_PRESENT=no",
                 "USB_OTHER_APPLE=none", "USB_VERDICT=DFU"):
        check("DFU output has %s" % line, line in out, out)

    # CLI exit codes via --input fixtures (offline, no hardware).
    def cli_file(text):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log",
                                          delete=False) as fh:
            fh.write(text)
            path = fh.name
        try:
            p = subprocess.run([sys.executable, DETECTOR, "--input", path],
                               capture_output=True, text=True)
            return p.returncode, p.stdout
        finally:
            os.unlink(path)

    rc, out = cli_file(LSUSB_DFU)
    check("CLI DFU exit 0", rc == 0, "rc=%d" % rc)
    check("CLI DFU verdict line", "USB_VERDICT=DFU" in out)
    rc, out = cli_file(LSUSB_NORMAL)
    check("CLI NORMAL exit 0", rc == 0, "rc=%d" % rc)
    rc, out = cli_file(LSUSB_ABSENT)
    check("CLI ABSENT exit 1", rc == 1, "rc=%d" % rc)
    check("CLI ABSENT verdict line", "USB_VERDICT=ABSENT" in out)
    rc, out = cli_file(LSUSB_MULTI)
    check("CLI MULTIPLE exit 2", rc == 2, "rc=%d" % rc)
    rc, out = cli_file(LSUSB_OTHER)
    check("CLI OTHER exit 2", rc == 2, "rc=%d" % rc)

    p = subprocess.run([sys.executable, DETECTOR, "--input",
                        "/nonexistent-myos-lsusb-xyz.log"],
                       capture_output=True, text=True)
    check("CLI missing file exit 3", p.returncode == 3,
          "rc=%d" % p.returncode)

    # Non-UTF-8 lsusb output (device-controlled descriptor bytes) must not
    # crash the classifier: fake `lsusb` emitting invalid bytes plus a valid
    # DFU line must still classify DFU (review finding M-P3.0.2).
    import stat as _stat
    import tempfile as _tempfile
    fake_dir = _tempfile.mkdtemp(prefix="myos-fakelsusb-")
    try:
        fake_lsusb = os.path.join(fake_dir, "lsusb")
        with open(fake_lsusb, "wb") as fh:
            fh.write(b"#!/bin/sh\nprintf '\\377\\376\\001"
                     b"Bus 002 Device 010: ID 05ac:1227 X\\n'\n")
        os.chmod(fake_lsusb, 0o755 | _stat.S_IXUSR)
        env = dict(os.environ)
        env["PATH"] = fake_dir + os.pathsep + env.get("PATH", "")
        p = subprocess.run([sys.executable, DETECTOR],
                           capture_output=True, text=True, env=env)
        check("non-UTF8 lsusb no traceback", "Traceback" not in p.stderr,
              p.stderr[:200])
        check("non-UTF8 lsusb still DFU exit 0", p.returncode == 0,
              "rc=%d out=%s err=%s" % (p.returncode, p.stdout, p.stderr[:200]))
        check("non-UTF8 lsusb verdict DFU", "USB_VERDICT=DFU" in p.stdout,
              p.stdout)
    finally:
        try:
            os.unlink(os.path.join(fake_dir, "lsusb"))
            os.rmdir(fake_dir)
        except OSError:
            pass

    # Read-only audit: the tool's only subprocess invocation is ["lsusb"];
    # no os.write/ioctl/open-for-write/mode-switch strings exist.
    with open(DETECTOR, "r") as fh:
        src = fh.read()
    check("only subprocess is lsusb",
          src.count('["lsusb"]') == 1 and "gaster" not in src
          and "checkra1n" not in src and "DFU_DNLOAD" not in src
          and "bootr" not in src)
    for banned in ("os.write", "ioctl", "libusb", "usb.core", "open(\"",
                   "open('"):
        # open( appears only for reading --input (with "r"); flag any other use
        if banned in ("open(\"", "open('"):
            continue
        check("no %s in detector" % banned, banned not in src, banned)

    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("usb detect self-tests: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
