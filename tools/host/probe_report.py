#!/usr/bin/env python3
"""
MyOS M-P2.7 -- Probe report parser (host-side, stdlib only, OFFLINE).

Parses the MP1PRB01 report block defined by platform/apple/a8/pongo_probe.S
(the `.equ` block at lines 93-108 is the single source of truth for the
layout).  In the raw image the report lives at fixed file offset 0x200
(RPT_OFF); the probe records it at runtime immediately after the PongoOS
`bootr` handoff.

EVIDENCE BOUNDARY (what this tool can and cannot do)
----------------------------------------------------
It parses the *static file layout*:
  * the fixed header (magic / version / report_size) is present in the
    uploaded file and can be verified off-device;
  * the dynamic register slots (x30/x0/x8/EL/SCTLR/MIDR/CNTPCT/CNTFRQ)
    MUST be zero in the file, because they are written only at runtime,
    AFTER the handoff - that is, after PongoOS has already destroyed every
    channel that could read them back (see tools/host/pongo_client.py and
    tests/test_pongo_client.py for the channel-contract proof).

A non-zero dynamic slot in a parsed blob can therefore only come from a
SYNTHETIC fixture or a hypothetical post-handoff capture mechanism; it is
never hardware evidence.  Nothing here proves a real-device readback.

Usage:
  python3 tools/host/probe_report.py --repo .            # built probe bin
  python3 tools/host/probe_report.py --bin PATH          # explicit probe bin
  python3 tools/host/probe_report.py --blob PATH         # raw 0x60 report blob
  python3 tools/host/probe_report.py --json              # machine-readable
  python3 tools/host/probe_report.py --expect-dynamic    # fail if all-zero
Exit 0 = parse ok, non-zero = parse error / missing input.
"""

import argparse
import json
import os
import re
import struct
import sys

# ---------------------------------------------------------------------------
# Layout constants -- a 1:1 mirror of the `.equ` block in
# platform/apple/a8/pongo_probe.S (lines 93-108).  The tests re-derive the .equ
# values from the source and assert equality with EQU_MIRROR, so any drift
# in either place fails CI.
# ---------------------------------------------------------------------------

EQU_MIRROR = {
    "RPT_MAGIC_OFF": 0x00,
    "RPT_VER_OFF":   0x08,
    "RPT_SIZE_OFF":  0x0C,
    "RPT_X30_OFF":   0x10,
    "RPT_X0_OFF":    0x18,
    "RPT_X8_OFF":    0x20,
    "RPT_EL_OFF":    0x28,
    "RPT_SCTLR_OFF": 0x30,
    "RPT_MIDR_OFF":  0x38,
    "RPT_TVAL1_OFF": 0x40,
    "RPT_TVAL2_OFF": 0x48,
    "RPT_TFRQ_OFF":  0x50,
    "RPT_ZERO_OFF":  0x58,
    "RPT_SIZE":      0x60,
    "RPT_VERSION":   1,
    "RPT_OFF":       0x200,
}

RPT_SIZE    = EQU_MIRROR["RPT_SIZE"]
RPT_VERSION = EQU_MIRROR["RPT_VERSION"]
RPT_OFF     = EQU_MIRROR["RPT_OFF"]
MAGIC_TXT   = b"MP1PRB01"
# Little-endian store of the immediate used in pongo_probe.S:122
# (0x313042525031504D) must equal the ASCII magic on disk.
MAGIC_IMMEDIATE = 0x313042525031504D

# name -> offset for the runtime-recorded u64 slots (written post-handoff)
DYNAMIC_FIELDS = (
    ("x30_entry",   EQU_MIRROR["RPT_X30_OFF"]),   # buffer physical base
    ("x0_entry",    EQU_MIRROR["RPT_X0_OFF"]),    # gBootArgs cacheable VA
    ("x8_entry",    EQU_MIRROR["RPT_X8_OFF"]),    # gEntryPoint
    ("current_el",  EQU_MIRROR["RPT_EL_OFF"]),
    ("sctlr_el1",   EQU_MIRROR["RPT_SCTLR_OFF"]),
    ("midr_el1",    EQU_MIRROR["RPT_MIDR_OFF"]),
    ("cntpct_el0_1", EQU_MIRROR["RPT_TVAL1_OFF"]),
    ("cntpct_el0_2", EQU_MIRROR["RPT_TVAL2_OFF"]),
    ("cntfrq_el0",  EQU_MIRROR["RPT_TFRQ_OFF"]),
    ("reserved_zero", EQU_MIRROR["RPT_ZERO_OFF"]),
)


def equ_constants_from_source(text):
    """Extract `.equ NAME, value` pairs from the probe assembly source."""
    out = {}
    for m in re.finditer(r"\.equ\s+([A-Z0-9_]+)\s*,\s*(0x[0-9a-fA-F]+|\d+)", text):
        out[m.group(1)] = int(m.group(2), 0)
    return out


def parse_report(blob, offset=0):
    """Parse a 0x60-byte MP1PRB01 report block.

    Raises ValueError on a short blob, bad magic, bad version or a
    report_size that disagrees with RPT_SIZE.
    Returns a dict with 'magic','version','report_size' plus one u64 key
    per DYNAMIC_FIELDS entry.
    """
    if blob is None:
        raise ValueError("no blob")
    if len(blob) - offset < RPT_SIZE:
        raise ValueError(
            "blob too short for report: need %d bytes at offset 0x%x, have %d"
            % (RPT_SIZE, offset, len(blob) - offset)
        )
    if len(blob) - offset != RPT_SIZE:
        raise ValueError(
            "report must be exactly 0x%x bytes, got %d"
            % (RPT_SIZE, len(blob) - offset)
        )
    slot = blob[offset:offset + RPT_SIZE]

    if slot[0:8] != MAGIC_TXT:
        raise ValueError(
            "bad magic: got %r, want %r" % (slot[0:8], MAGIC_TXT)
        )
    version = struct.unpack_from("<I", slot, EQU_MIRROR["RPT_VER_OFF"])[0]
    if version != RPT_VERSION:
        raise ValueError("bad version: got %d, want %d" % (version, RPT_VERSION))
    report_size = struct.unpack_from("<I", slot, EQU_MIRROR["RPT_SIZE_OFF"])[0]
    if report_size != RPT_SIZE:
        raise ValueError(
            "bad report_size: got 0x%x, want 0x%x" % (report_size, RPT_SIZE)
        )

    report = {
        "magic": slot[0:8].decode("ascii", "replace"),
        "version": version,
        "report_size": report_size,
    }
    for name, off in DYNAMIC_FIELDS:
        report[name] = struct.unpack_from("<Q", slot, off)[0]
    # Validate reserved field per MP1PRB01 format contract:
    # platform/apple/a8/pongo_probe.S defines RPT_ZERO_OFF = 0x58 with comment
    # "reserved_zero (always 0)". This is a structural requirement.
    reserved = report.get("reserved_zero", 0)
    if reserved != 0:
        raise ValueError(
            "reserved field at 0x%x must be zero, got 0x%x"
            % (EQU_MIRROR["RPT_ZERO_OFF"], reserved))
    return report


def has_dynamic_data(report):
    """True if any runtime-recorded slot is non-zero (SYNTHETIC capture)."""
    return any(report[name] != 0 for name, _ in DYNAMIC_FIELDS)


def report_from_probe_bin(bin_path):
    """Parse the static header baked into a built raw probe image at 0x200."""
    with open(bin_path, "rb") as f:
        data = f.read()
    if len(data) < RPT_OFF + RPT_SIZE:
        raise ValueError(
            "probe image too small (%d bytes) to carry report at 0x%x"
            % (len(data), RPT_OFF)
        )
    return parse_report(data, RPT_OFF)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print(report, as_json):
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print("MP1PRB01 probe report")
    print("  magic        : %s" % report["magic"])
    print("  version      : %d" % report["version"])
    print("  report_size  : 0x%x" % report["report_size"])
    for name, _ in DYNAMIC_FIELDS:
        print("  %-14s: 0x%016x" % (name, report[name]))
    state = "DYNAMIC SLOTS SET (synthetic capture? not a static file)" \
        if has_dynamic_data(report) \
        else "dynamic slots zero (static upload: runtime fields not yet written)"
    print("  state        : %s" % state)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Parse an MP1PRB01 probe report (offline, host-side)."
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--repo", default=".", help="repo root (default: .)")
    src.add_argument("--bin", help="path to a built raw probe image")
    src.add_argument("--blob", help="path to a raw 0x60-byte report blob")
    ap.add_argument("--json", action="store_true",
                    help="print JSON instead of human text")
    ap.add_argument("--expect-dynamic", action="store_true",
                    help="fail when all dynamic slots are zero")
    args = ap.parse_args(argv)

    try:
        if args.blob:
            with open(args.blob, "rb") as f:
                report = parse_report(f.read(), 0)
        else:
            bin_path = args.bin or os.path.join(args.repo, "build", "pongo_probe.bin")
            report = report_from_probe_bin(bin_path)
    except (OSError, ValueError) as exc:
        print("probe_report: ERROR: %s" % exc, file=sys.stderr)
        return 1

    if args.expect_dynamic and not has_dynamic_data(report):
        print("probe_report: ERROR: expected dynamic slots, all are zero",
              file=sys.stderr)
        return 1

    _print(report, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())