#!/usr/bin/env python3
"""
MyOS M-P2.34 -- Host-side report freshness validation (offline, stdlib only).

This is a host-side validator only. It does NOT modify probe firmware or
the existing MP1PRB01 wire format. The proposed MP1PRB02 is treated as a
design schema (not implemented in firmware). No hardware access.

Statuses (semantic):
  INVALID
  LEGACY_VALID_FRESHNESS_UNVERIFIED
  V2_VALID_FRESH
  V2_VALID_STALE
  V2_VALID_FRESHNESS_UNVERIFIABLE

Invariant: a valid legacy v1 report is NEVER labelled fresh.
Token comparison is exact (full token, LE byte order). Missing expected token
cannot produce a fresh result. Unknown versions are rejected.
"""

import argparse
import struct
import sys

# v1 layout (source-derived, unchanged)
MAGIC_V1 = b"MP1PRB01"
VERSION_V1 = 1
REPORT_SIZE = 0x60  # bytes

# v2 proposed (design only; not implemented in firmware)
MAGIC_V2 = b"MP1PRB02"
VERSION_V2 = 2

# Offsets (LE)
OFF_MAGIC = 0x00
OFF_VER = 0x08
OFF_SIZE = 0x0C
OFF_TOKEN = 0x58  # proposed for v2 only (v1: reserved_zero)

def _unpack_u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]

def _unpack_u64(blob, off):
    return struct.unpack_from("<Q", blob, off)[0]

def validate_report(blob, expected_token=None):
    """
    Validate a report blob at offset 0.

    Returns (status, detail_dict)
    status in {'INVALID','LEGACY_VALID_FRESHNESS_UNVERIFIED',
               'V2_VALID_FRESH','V2_VALID_STALE','V2_VALID_FRESHNESS_UNVERIFIABLE'}
    """
    if blob is None:
        return ("INVALID", {"reason": "no_blob"})
    if len(blob) < REPORT_SIZE:
        return ("INVALID", {"reason": "truncated", "have": len(blob), "need": REPORT_SIZE})
    if len(blob) != REPORT_SIZE:
        return ("INVALID", {"reason": "size_mismatch", "have": len(blob), "need": REPORT_SIZE})

    magic = blob[OFF_MAGIC:OFF_MAGIC+8]
    ver = _unpack_u32(blob, OFF_VER)
    sz = _unpack_u32(blob, OFF_SIZE)

    # Version-specific
    if magic == MAGIC_V1 and ver == VERSION_V1:
        if sz != REPORT_SIZE:
            return ("INVALID", {"reason": "v1_bad_size", "size": sz})
        # Validate reserved_zero field per MP1PRB01 format contract:
        # offset 0x58 must be zero for v1 (same offset as token in v2).
        reserved = _unpack_u64(blob, OFF_TOKEN)
        if reserved != 0:
            return ("INVALID", {"reason": "v1_bad_reserved", "reserved": reserved})
        return ("LEGACY_VALID_FRESHNESS_UNVERIFIED", {
            "version": "v1",
            "magic": magic.decode("ascii","replace"),
            "size": sz,
        })
    if magic == MAGIC_V2 and ver == VERSION_V2:
        if sz != REPORT_SIZE:
            return ("INVALID", {"reason": "v2_bad_size", "size": sz})
        if expected_token is None:
            return ("V2_VALID_FRESHNESS_UNVERIFIABLE", {
                "version": "v2",
                "magic": magic.decode("ascii","replace"),
                "size": sz,
            })
        try:
            token = _unpack_u64(blob, OFF_TOKEN)
        except struct.error:
            return ("INVALID", {"reason": "v2_bad_token_encoding"})
        if token == expected_token:
            return ("V2_VALID_FRESH", {
                "version": "v2",
                "magic": magic.decode("ascii","replace"),
                "size": sz,
                "token_match": True,
            })
        else:
            return ("V2_VALID_STALE", {
                "version": "v2",
                "magic": magic.decode("ascii","replace"),
                "size": sz,
                "token_match": False,
            })
    return ("INVALID", {
        "reason": "unknown_version_or_magic",
        "magic": magic.decode("ascii","replace"),
        "version": ver,
        "size": sz,
    })

def _parse_token(s):
    if s is None:
        return None
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s, 0)

def main(argv=None):
    ap = argparse.ArgumentParser(description="Host-side report freshness validator (offline).")
    ap.add_argument("--blob", required=True, help="path to 0x60-byte report blob")
    ap.add_argument("--expected-token", help="expected token (int or 0x...) for v2; if omitted, v2 -> unverifiable")
    args = ap.parse_args(argv)
    try:
        with open(args.blob, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        tok = _parse_token(args.expected_token)
    except ValueError:
        print("ERROR: --expected-token expects an integer (decimal or "
              "0x-prefixed hex), got %r" % args.expected_token,
              file=sys.stderr)
        return 2
    st, det = validate_report(data, expected_token=tok)
    print(st)
    for k,v in sorted(det.items()):
        print(f"  {k}={v}")
    if st == "INVALID":
        return 1
    if st == "V2_VALID_STALE":
        return 10
    if st == "V2_VALID_FRESHNESS_UNVERIFIABLE":
        return 20
    return 0

if __name__ == "__main__":
    sys.exit(main())
