#!/usr/bin/env python3
"""
MyOS A8 diagnostic pipeline: decode exception record → capture envelope.

Integrates:
1. Production image hash validation
2. QEMU runtime evidence consumption (pmemsave record bytes)
3. Independent decoder (a8_exc_record_decoder)
4. Versioned capture envelope (capture_envelope)
5. Schema validation and self-consistency checks
6. Machine-readable + human-readable output

Usage:
    python3 tools/host/decode_envelope.py [--mode {default,sp0,spx}]
        [--evidence-dir DIR] [--image PATH] [--sha HASH]
        [--address-space {physical,qemu}] [--skip-sha-check] [--brief]
    python3 tools/host/decode_envelope.py --help

Exit codes: 0 on success; 1 on any failure (missing/mismatched image,
missing or truncated evidence, decode failure, envelope validation failure).
With --brief, stdout carries exactly one JSON line and all progress/error
reporting goes to stderr.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Optional, Tuple

# ---- Repository root from CWD ----
REPO = os.path.abspath(".")

# ---- Constants ----
PROD_BIN = os.path.join(REPO, "build", "raw_entry.bin")
PROD_SHA = "0eb3af057a6ba82ad55c114d0a0056c1d3cea686375f535c813ba25b2cd8c635"
EXC_SIZE = 72  # 0x48, from raw_vectors.S .excrecord

# Evidence directories relative to REPO
EVIDENCE_DIR_DEFAULT = os.path.join(REPO, "build", "raw-entry-runtime", "evidence")
EVIDENCE_DIR_SP0 = os.path.join(REPO, "build", "raw-entry-runtime", "scenario-sp0", "evidence")
EVIDENCE_DIR_SPX = os.path.join(REPO, "build", "raw-entry-runtime", "scenario-spx", "evidence")


# ---- Helpers ----

def prod_hash_matches_given(path: str, expected: str) -> bool:
    """Verify production image SHA-256 matches given hash."""
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        return h == expected
    except FileNotFoundError:
        return False


def record_hex(esr_val: Optional[int], elr_val: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """Format ESR/ELR as hex strings safely."""
    esr_hex = f"0x{esr_val:x}" if esr_val is not None else None
    elr_hex = f"0x{elr_val:x}" if elr_val is not None else None
    return esr_hex, elr_hex


def read_record_from_evidence(evidence_dir: str, filename: str = "record.bin") -> Optional[bytes]:
    """Read the 72-byte exception record from QEMU evidence directory."""
    path = os.path.join(evidence_dir, filename)
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) != EXC_SIZE:
            print(f"WARNING: {filename} size={len(data)} != expected {EXC_SIZE}", file=sys.stderr)
            return None
        return data
    except FileNotFoundError:
        print(f"ERROR: {path} not found", file=sys.stderr)
        return None


def decode_record(buf: bytes) -> Tuple[Optional[object], Optional[str]]:
    """Decode the exception record using the independent decoder."""
    from a8_exc_record_decoder import decode as _decode
    return _decode(buf)


def classify_status(record: object) -> str:
    """Human-readable status from the decoder."""
    from a8_exc_record_decoder import classify_status as dcs
    if record is None:
        return "NONE"
    return dcs(record.status)


def make_envelope_from_record(
    record_bytes: bytes,
    decoder_record: object,
    source_sha: str,
    source_size: int,
    address_space: str = "physical",
) -> CaptureEnvelope:
    """Create a capture envelope from decoded record data."""
    from capture_envelope import make_from_record_bytes as _make
    return _make(
        record_bytes=record_bytes,
        source_image_sha256=source_sha,
        source_image_size=source_size,
        image_base=0,
        address_space=address_space,
        decoder_result={
            "status": decoder_record.status.name
            if hasattr(decoder_record, "status")
            else str(decoder_record.status),
            "field_count": sum(
                1
                for v in (
                    getattr(decoder_record, "slot", None),
                    getattr(decoder_record, "esr", None),
                    getattr(decoder_record, "elr", None),
                    getattr(decoder_record, "valid_marker", None),
                )
                if v is not None
            ),
            "slot": getattr(decoder_record, "slot", None),
            "esr": getattr(decoder_record, "esr", None),
            "elr": getattr(decoder_record, "elr", None),
            "marker": getattr(decoder_record, "valid_marker", None),
        },
        qemu_info=None,
    )


def validate_envelope(envelope: object) -> Tuple[bool, Optional[str]]:
    from capture_envelope import CaptureEnvelope
    valid, errors = envelope.validate()
    return valid, errors


# ---- CLI ----

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MyOS A8 diagnostic pipeline: decode exception record → capture envelope"
    )
    parser.add_argument(
        "--mode",
        choices=["default", "sp0", "spx"],
        default="default",
        help="QEMU runtime mode (default: default)",
    )
    parser.add_argument(
        "--evidence-dir",
        default=None,
        help="Override evidence directory",
    )
    parser.add_argument(
        "--image",
        default=PROD_BIN,
        help="Path to production raw_entry.bin (default: build/raw_entry.bin)",
    )
    parser.add_argument(
        "--sha",
        default=PROD_SHA,
        help="Expected production image SHA-256 (default: recorded hash)",
    )
    parser.add_argument(
        "--address-space",
        choices=["physical", "qemu"],
        default="physical",
        help="Address space for envelope (default: physical)",
    )
    parser.add_argument(
        "--skip-sha-check",
        action="store_true",
        help="Skip production image hash verification (not recommended)",
    )
    parser.add_argument("--brief", action="store_true", help="Brief machine-readable output only")
    args = parser.parse_args()

    # In --brief mode the ONLY stdout content is the final JSON line; all
    # progress/error reporting goes to stderr so the output is parseable.
    def log(msg: str) -> None:
        print(msg, file=sys.stderr if args.brief else sys.stdout)

    # ---- Step 1: Production image hash verification ----
    if not args.skip_sha_check:
        if not os.path.isfile(args.image):
            print(f"ERROR: Production image not found: {args.image}", file=sys.stderr)
            return 1
        if not prod_hash_matches_given(args.image, args.sha):
            print(
                f"ERROR: Production image SHA-256 mismatch: "
                f"expected {args.sha}, got {hashlib.sha256(open(args.image,'rb').read()).hexdigest()}",
                file=sys.stderr,
            )
            return 1
        log(f"OK: Production image hash verified: {args.sha[:16]}...")

    # ---- Step 2: Determine evidence directory ----
    mode = args.mode
    if args.evidence_dir:
        evidence_dir = args.evidence_dir
    elif mode == "sp0":
        evidence_dir = EVIDENCE_DIR_SP0
    elif mode == "spx":
        evidence_dir = EVIDENCE_DIR_SPX
    else:
        evidence_dir = EVIDENCE_DIR_DEFAULT

    # ---- Step 3: Read raw record bytes from QEMU evidence ----
    record_bytes = read_record_from_evidence(evidence_dir)
    if record_bytes is None:
        print("ERROR: Failed to read exception record from QEMU evidence", file=sys.stderr)
        return 1
    log(f"OK: Read exception record from {evidence_dir}/record.bin ({len(record_bytes)} bytes)")

    # ---- Step 4: Decode the record ----
    decoder_record, decode_error = decode_record(record_bytes)
    if decode_error:
        print(f"ERROR: Decoder error: {decode_error}", file=sys.stderr)
        return 1
    if decoder_record is None:
        print("ERROR: Decoder returned no record", file=sys.stderr)
        return 1

    status = classify_status(decoder_record)
    log(f"OK: Decoder classification: {status}")

    # Show decoded fields
    slot = getattr(decoder_record, "slot", None)
    esr = getattr(decoder_record, "esr", None)
    elr = getattr(decoder_record, "elr", None)
    marker = getattr(decoder_record, "valid_marker", None)
    esr_fmt, elr_fmt = record_hex(esr, elr)
    log(f"  slot={slot}, esr={esr_fmt}, elr={elr_fmt}, marker={marker}")

    # ---- Step 5: Create capture envelope ----
    from capture_envelope import make_from_record_bytes

    if not os.path.isfile(args.image):
        print(
            f"ERROR: Production image not found (needed for envelope size): {args.image}",
            file=sys.stderr,
        )
        return 1
    source_size = os.path.getsize(args.image)
    if source_size <= 0:
        print(f"ERROR: Production image is empty: {args.image}", file=sys.stderr)
        return 1
    envelope = make_from_record_bytes(
        record_bytes=record_bytes,
        source_image_sha256=args.sha,
        source_image_size=source_size,
        image_base=0,
        address_space=args.address_space,
        decoder_result={
            "status": decoder_record.status.name
            if hasattr(decoder_record, "status")
            else str(decoder_record.status),
            "field_count": sum(
                1 for v in (slot, esr, elr, marker) if v is not None
            ),
            "slot": slot,
            "esr": esr,
            "elr": elr,
            "marker": marker,
        },
        qemu_info=None,
    )

    # ---- Step 6: Validate envelope ----
    valid, errors = validate_envelope(envelope)
    if not valid:
        print(f"ERROR: Envelope validation failed: {errors}", file=sys.stderr)
        return 1
    log("OK: Envelope validation passed")

    # ---- Step 7: Output ----
    if args.brief:
        # Machine-readable: just the status and key fields
        print(json.dumps({
            "status": envelope.decoder_status,
            "slot": envelope.decoder_slot,
            "esr": envelope.decoder_esr,
            "elr": envelope.decoder_elr,
            "marker": envelope.decoder_marker,
            "sha256": envelope.source_image_sha256,
            "envelope_valid": True,
        }))
    else:
        # Human-readable summary
        print()
        print("=" * 56)
        print("A8 EXCEPTION RECORD — DECODE + ENVELOPE PIPELINE")
        print("=" * 56)
        print()
        print("Production image:       {}".format(args.image))
        print("SHA-256:              {}".format(args.sha))
        print("Image size:           {} bytes".format(os.path.getsize(args.image)))
        print()
        print("Evidence source:      {}".format(evidence_dir))
        print("Record bytes:         {} bytes ({})".format(len(record_bytes), "VALID" if len(record_bytes) == EXC_SIZE else "TRUNCATED"))
        print()
        print("Decoder result:")
        print("  status:   {}".format(envelope.decoder_status))
        print("  slot:     {}".format(envelope.decoder_slot))
        print("  esr:      {}".format(esr_fmt if esr_fmt else "<unavailable>"))
        print("  elr:      {}".format(elr_fmt if elr_fmt else "<unavailable>"))
        print("  marker:   {}".format(envelope.decoder_marker))
        print()
        print("Envelope:")
        print("  format:       {}".format(envelope.format_version))
        print("  sha256:       {}".format(envelope.source_image_sha256[:16] + "..."))
        print("  image_base:   {}".format(envelope.image_base))
        print("  address_sp:   {}".format(envelope.address_space))
        print("  record_off:   0x{:x}".format(envelope.record_offset))
        print("  record_len:   {}".format(envelope.record_length))
        print()
        if errors:
            print("VALIDATION ERRORS: {}".format(errors))
        else:
            print("Envelope: VALID")
        print()
        print("{}".format(envelope))

    return 0


if __name__ == "__main__":
    sys.exit(main())
