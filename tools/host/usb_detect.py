#!/usr/bin/env python3
"""
MyOS M-P3.0 — read-only Apple USB detector (host-side, stdlib only).

Identifies the physical iPhone's USB-visible state WITHOUT changing it:
runs `lsusb` (read-only enumeration), parses `ID 05ac:XXXX` tokens, and
reports which known Apple USB identities are present:

  05ac:12a8 -> normal iOS mode (may appear)
  05ac:1227 -> DFU mode (may appear)
  05ac:4141 -> PongoOS device

Any other 05ac:XXXX PID is reported verbatim as OTHER and never interpreted.

SAFETY: this tool only READS. It never switches device modes, never sends
payloads or control transfers, never requests pairing/trust, and never
writes to any device or file (except its own stdout). USB detection alone
is NEVER boot evidence: a DFU/Pongo/normal PID proves attachment/mode only,
never that MyOS executed or that any display output exists.

Stable output (stdout):
  USB_APPLE_COUNT=<n>
  USB_12A8_PRESENT=<yes|no>
  USB_1227_PRESENT=<yes|no>
  USB_4141_PRESENT=<yes|no>
  USB_OTHER_APPLE=<lowercase pid,...|none>
  USB_VERDICT=<NORMAL|DFU|PONGO|ABSENT|MULTIPLE|OTHER>
(human `# …` comment lines are also printed first.)

Exit codes:
  0 = exactly one known state (NORMAL xor DFU xor PONGO, no OTHER PIDs)
  1 = no Apple device at all (ABSENT — check cable/connection; STOP)
  2 = ambiguous (MULTIPLE known states, or any OTHER Apple PID present —
      record exactly what appeared; do NOT interpret as success)
  3 = usage/execution error (`lsusb` missing/fails, unreadable --input)

Usage:
  usb_detect.py                  # run lsusb and classify
  usb_detect.py --input file     # parse saved `lsusb` output (offline, tests)

Operator guide: run before AND after any observation window and save both
snapshots with timestamps:
  date '+%F %T %Z'; python3 tools/host/usb_detect.py; echo "EXIT:$?"
A DFU verdict across both snapshots means "phone attached in DFU throughout";
it does not mean MyOS booted. Any verdict change mid-window confounds the
timeline — record it and stop.
"""

import argparse
import re
import subprocess
import sys

RE_LSUSB_ID = re.compile(r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})")

PID_NORMAL = "12a8"
PID_DFU = "1227"
PID_PONGO = "4141"


def parse_lsusb(text):
    """Parse `lsusb` output text. Returns sorted list of distinct lowercase
    Apple (05ac) PIDs observed.

    Only lines matching `ID VVVV:PPPP` with VID 05ac contribute; all other
    lines (hubs, webcams, headers, tree views) are ignored, not errors.
    """
    pids = set()
    for line in text.splitlines():
        m = RE_LSUSB_ID.search(line)
        if not m:
            continue
        vid, pid = m.group(1).lower(), m.group(2).lower()
        if vid == "05ac":
            pids.add(pid)
    return sorted(pids)


def classify(pids):
    """Classify Apple PID list. Returns dict with verdict fields."""
    pids = list(pids)
    has_normal = PID_NORMAL in pids
    has_dfu = PID_DFU in pids
    has_pongo = PID_PONGO in pids
    other = sorted(p for p in pids
                   if p not in (PID_NORMAL, PID_DFU, PID_PONGO))
    known_count = sum((has_normal, has_dfu, has_pongo))
    if not pids:
        verdict = "ABSENT"
    elif known_count > 1 or (known_count == 1 and other):
        verdict = "MULTIPLE"
    elif other:
        verdict = "OTHER"
    elif has_normal:
        verdict = "NORMAL"
    elif has_dfu:
        verdict = "DFU"
    else:
        verdict = "PONGO"
    return {
        "count": len(pids),
        "normal": has_normal,
        "dfu": has_dfu,
        "pongo": has_pongo,
        "other": other,
        "verdict": verdict,
    }


def format_output(result):
    out = []
    out.append("USB_APPLE_COUNT=%d" % result["count"])
    out.append("USB_12A8_PRESENT=%s" % ("yes" if result["normal"] else "no"))
    out.append("USB_1227_PRESENT=%s" % ("yes" if result["dfu"] else "no"))
    out.append("USB_4141_PRESENT=%s" % ("yes" if result["pongo"] else "no"))
    out.append("USB_OTHER_APPLE=%s" % (",".join(result["other"])
                                       if result["other"] else "none"))
    out.append("USB_VERDICT=%s" % result["verdict"])
    return "\n".join(out) + "\n"


def human_lines(result):
    h = []
    h.append("# Apple USB identities observed: %d "
             "(normal=05ac:12a8 dfu=05ac:1227 pongo=05ac:4141)" % result["count"])
    if result["verdict"] == "ABSENT":
        h.append("# no Apple device visible: check cable/port; STOP "
                 "(no detection, no boot inference)")
    elif result["verdict"] in ("NORMAL", "DFU", "PONGO"):
        h.append("# single %s identity; mode attachment only — "
                 "NOT boot evidence" % result["verdict"])
    elif result["verdict"] == "OTHER":
        h.append("# unknown Apple PID(s) only (%s): record exactly; "
                 "do NOT interpret as success" % ",".join(result["other"]))
    else:
        h.append("# AMBIGUOUS (multiple/other Apple PIDs): record all; "
                 "do NOT interpret as success")
    h.append("# USB detection is never proof MyOS executed or displayed anything")
    return "\n".join(h) + "\n"


def run_lsusb():
    """Execute `lsusb` read-only. Returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(["lsusb"], capture_output=True, text=True,
                              timeout=30)
    except FileNotFoundError:
        return 127, "", "lsusb not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Read-only Apple USB state detector "
                    "(never changes device mode, sends nothing).")
    ap.add_argument("--input", default=None,
                    help="parse saved `lsusb` output file instead of "
                         "running lsusb (offline/testing)")
    args = ap.parse_args(argv)

    if args.input is not None:
        try:
            with open(args.input, "r", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("file error: %s" % exc, file=sys.stderr)
            return 3
    else:
        rc, text, err = run_lsusb()
        if rc != 0:
            print("lsusb failed (rc=%d): %s" % (rc, err.strip()),
                  file=sys.stderr)
            return 3

    pids = parse_lsusb(text)
    result = classify(pids)
    sys.stdout.write(human_lines(result))
    sys.stdout.write(format_output(result))
    if result["verdict"] in ("NORMAL", "DFU", "PONGO"):
        return 0
    if result["verdict"] == "ABSENT":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
