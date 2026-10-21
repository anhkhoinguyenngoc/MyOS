#!/usr/bin/env python3
"""
MyOS A8 raw-entry diagnostic capture envelope.

Defines a stable host-side capture envelope for preserving diagnostic evidence
from the A8 raw-entry exception record. The envelope distinguishes raw evidence
from derived interpretation and supports multiple capture sources (synthetic,
QEMU, physical).

This envelope is versioned and self-describing. Future extensions can add fields
without breaking backward compatibility by adding new optional fields.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import json
import hashlib
from datetime import datetime


# --- Envelope version and schema ---

ENVELOPE_VERSION = "1.0.0"
"""Current envelope schema version. Incremented for backward-incompatible changes."""

CAPTURE_FORMAT_VERSION = "1.0.0"
"""Version of the capture format used. Matches ENVELOPE_VERSION for this release."""


# --- Envelope dataclass ---


@dataclass
class CaptureEnvelope:
    """Stable host-side capture envelope for diagnostic evidence."""

    # --- Required fields ---

    format_version: str = field(default=CAPTURE_FORMAT_VERSION)
    """Version of the capture format (e.g., "1.0.0")."""

    source_image_sha256: str = field(default="")
    """SHA-256 hash of the source raw_entry.bin image that produced this capture."""

    source_image_size: int = field(default=0)
    """Size in bytes of the source raw_entry.bin image."""

    capture_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    """ISO-8601 UTC timestamp when the capture was made."""

    # --- Image base and address-space information ---

    image_base: int = field(default=0)
    """Image base address. Meaning depends on address_space: physical or QEMU load address."""

    address_space: Literal["physical", "qemu"] = field(default="physical")
    """Address space meaning for image_base:
    - "physical": physical base address (as determined by PongoOS)
    - "qemu": QEMU load address (--load-addr value)
    """

    # --- Record evidence ---

    record_offset: int = field(default=0x5000)
    """File offset (or guest physical offset) where the exception record begins."""

    record_length: int = field(default=0x48)
    """Length of the exception record in bytes (EXC_SIZE = 0x48)."""

    record_bytes: bytes = field(default_factory=bytes)
    """Raw exception record bytes captured from guest memory or the binary."""

    # --- Decoder result ---

    decoder_status: Optional[str] = field(default=None)
    """Classification of the record state: "VALID", "INCOMPLETE", "TRUNCATED", "MALFORMED"."""

    decoder_field_count: Optional[int] = field(default=None)
    """Number of fields decoded from the record."""

    decoder_slot: Optional[int] = field(default=None)
    """Slot index captured in the record (0..15)."""

    decoder_esr: Optional[int] = field(default=None)
    """ESR_EL1 value captured in the record."""

    decoder_elr: Optional[int] = field(default=None)
    """ELR_EL1 value captured in the record."""

    decoder_marker: Optional[int] = field(default=None)
    """Valid marker value (1 = complete, 0 = incomplete)."""

    # --- QEMU-specific evidence (optional) ---

    qemu_version: Optional[str] = field(default=None)
    """QEMU version string (e.g., "9.1.0")."""

    qemu_machine: Optional[str] = field(default=None)
    """QEMU machine configuration (e.g., "virt,secure=off,virtualization=off")."""

    qemu_cpu: Optional[str] = field(default=None)
    """QEMU CPU model (e.g., "cortex-a57")."""

    qemu_ram_mb: Optional[int] = field(default=None)
    """QEMU RAM size in MiB."""

    # --- Physical-device evidence (optional, typically absent) ---

    device_capabilities: Optional[Dict[str, Any]] = field(default=None)
    """Platform capabilities from the physical device (if available)."""

    # --- Derived interpretation (opaque from raw evidence) ---

    interpretation: Optional[Dict[str, Any]] = field(default=None)
    """Interpreted diagnostic data. Fills in known field values from the
    decoder, but explicitly does NOT assert any physical-device values.
    Keys are decoder-generated names; values are the decoded field values."""

    # --- Schema validation ---

    schema_hash: str = field(default="")
    """SHA-256 of the envelope JSON schema (for future compatibility checks)."""

    # --- Self-validation ---

    def validate(self) -> tuple:
        """Validate the envelope's internal consistency.

        Returns (valid: bool, errors: str or None).
        """
        errors = []

        if self.format_version != ENVELOPE_VERSION:
            errors.append(
                f"format_version {self.format_version} != envelope ENVELOPE_VERSION {ENVELOPE_VERSION}"
            )

        if self.source_image_sha256 and len(self.source_image_sha256) != 64:
            errors.append(
                f"source_image_sha256 invalid length: {len(self.source_image_sha256)}"
            )

        if self.source_image_size <= 0:
            errors.append("source_image_size must be positive")

        if self.record_length != 0x48:
            errors.append(f"record_length {self.record_length} != expected 0x48")

        if self.record_offset != 0x5000:
            errors.append(f"record_offset {self.record_offset} != expected 0x5000")

        if self.decoder_status and self.decoder_status not in (
            "VALID",
            "INCOMPLETE",
            "TRUNCATED",
            "MALFORMED",
        ):
            errors.append(f"invalid decoder_status: {self.decoder_status}")

        if not self.record_bytes:
            errors.append(
                "record_bytes missing: an evidence envelope must carry the raw record"
            )
        elif len(self.record_bytes) != self.record_length:
            errors.append(
                f"record_bytes length {len(self.record_bytes)} != record_length {self.record_length}"
            )

        if errors:
            return False, "; ".join(errors)
        return True, None

    def to_json(self) -> str:
        """Serialize the envelope to JSON, excluding None-valued fields."""
        data = {
            k: v for k, v in self.__dict__.items()
            if v is not None and not k.startswith("_") and k != "schema_hash"
        }
        # Convert bytes to hex string for JSON compatibility
        if "record_bytes" in data and isinstance(data["record_bytes"], bytes):
            data["record_bytes_hex"] = data["record_bytes"].hex()
            del data["record_bytes"]
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "CaptureEnvelope":
        """Deserialize an envelope from JSON."""
        data = json.loads(json_str)
        # Reconstruct raw record bytes from the hex form if present. The hex
        # key must be consumed HERE (not popped earlier): dropping it before
        # reconstruction silently discarded the raw evidence on round-trip
        # while validate() still passed (empty record_bytes skips the length
        # check). Regression-covered by test_a8_pipeline_integration.py.
        rb_hex = data.pop("record_bytes_hex", None)
        if rb_hex is not None:
            data["record_bytes"] = bytes.fromhex(rb_hex)
        # Remove internal fields
        data.pop("schema_hash", None)
        return cls(**data)

    def __str__(self) -> str:
        status = self.decoder_status or "pending"
        hsha = self.source_image_sha256[:16] if self.source_image_sha256 else "N/A"
        return (
            f"CaptureEnvelope(format={self.format_version}, "
            f"sha256={hsha}..., "
            f"status={status})"
        )


# --- Envelope factory functions ---


def make_from_record_bytes(
    record_bytes: bytes,
    source_image_sha256: str,
    source_image_size: int,
    image_base: int,
    address_space: Literal["physical", "qemu"] = "physical",
    decoder_result: Optional[Dict[str, Any]] = None,
    qemu_info: Optional[Dict[str, Any]] = None,
) -> CaptureEnvelope:
    """Create an envelope from raw record bytes and metadata."""

    # Decode the record and extract fields
    decoder_status = None
    field_count = None
    slot = None
    esr = None
    elr = None
    marker = None

    if decoder_result:
        decoder_status = decoder_result.get("status")
        field_count = decoder_result.get("field_count")
        slot = decoder_result.get("slot")
        esr = decoder_result.get("esr")
        elr = decoder_result.get("elr")
        marker = decoder_result.get("marker")

    # Build the envelope
    envelope = CaptureEnvelope(
        format_version=CAPTURE_FORMAT_VERSION,
        source_image_sha256=source_image_sha256,
        source_image_size=source_image_size,
        image_base=image_base,
        address_space=address_space,
        record_offset=0x5000,
        record_length=0x48,
        record_bytes=record_bytes,
        decoder_status=decoder_status,
        decoder_field_count=field_count,
        decoder_slot=slot,
        decoder_esr=esr,
        decoder_elr=elr,
        decoder_marker=marker,
        qemu_version=qemu_info.get("version") if qemu_info else None,
        qemu_machine=qemu_info.get("machine") if qemu_info else None,
        qemu_cpu=qemu_info.get("cpu") if qemu_info else None,
        qemu_ram_mb=qemu_info.get("ram_mb") if qemu_info else None,
    )

    # Compute schema hash
    envelope.schema_hash = hashlib.sha256(envelope.to_json().encode()).hexdigest()[:16]

    return envelope


def make_synthetic_fixture(
    slot: int = 0,
    esr: int = 0,
    elr: int = 0,
    marker: int = 0,
) -> CaptureEnvelope:
    """Create a synthetic capture envelope for testing purposes."""

    # Build a record in the correct layout
    buf = bytearray(0x48)
    buf[0:8] = b"MYOSA8E1"
    buf[8:12] = struct.pack("<I", 1)
    buf[12:16] = struct.pack("<I", 0x48)
    buf[16:24] = struct.pack("<Q", slot)
    buf[24:32] = struct.pack("<Q", esr)
    buf[32:40] = struct.pack("<Q", elr)
    buf[40:48] = struct.pack("<Q", 0)  # FAR
    buf[48:56] = struct.pack("<Q", 0)  # SPSR
    buf[56:64] = struct.pack("<Q", 0)  # SP
    buf[64:72] = struct.pack("<Q", marker)  # marker

    record_bytes = bytes(buf)
    sha256 = hashlib.sha256(record_bytes).hexdigest()

    return CaptureEnvelope(
        format_version=CAPTURE_FORMAT_VERSION,
        source_image_sha256=sha256,
        source_image_size=20552,  # production raw_entry.bin size
        image_base=0,
        address_space="physical",
        record_bytes=record_bytes,
        decoder_status="VALID" if marker == 1 else ("INCOMPLETE" if marker == 0 else None),
        decoder_field_count=sum(1 for v in (slot, esr, elr, marker) if v is not None),
        decoder_slot=slot,
        decoder_esr=esr,
        decoder_elr=elr,
        decoder_marker=marker,
        # Machine-readable provenance label: this envelope is TEST DATA, not
        # a capture from any machine. It survives JSON export so a synthetic
        # fixture can never be mistaken for real evidence downstream.
        interpretation={"synthetic_fixture": True},
    )


# --- CLI ---


def main():
    """CLI for envelope operations."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="MyOS A8 diagnostic capture envelope manager"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Create envelope from record bytes
    create_parser = subparsers.add_parser("create", help="Create envelope from record bytes")
    create_parser.add_argument("sha256", help="SHA-256 of source image")
    create_parser.add_argument("size", type=int, help="Size of source image in bytes")
    create_parser.add_argument("image_base", type=lambda x: int(x, 0), help="Image base address (hex or dec)")
    create_parser.add_argument("--address-space", choices=["physical", "qemu"], default="physical")
    create_parser.add_argument("--decoder-status", default=None)
    create_parser.add_argument("--decoder-field-count", type=int, default=None)
    create_parser.add_argument("--decoder-slot", type=int, default=None)
    create_parser.add_argument("--decoder-esr", type=lambda x: int(x, 0) if x else None, default=None)
    create_parser.add_argument("--decoder-elr", type=lambda x: int(x, 0) if x else None, default=None)
    create_parser.add_argument("--decoder-marker", type=lambda x: int(x, 0) if x else None, default=None)
    create_parser.add_argument("--qemu-version", default=None)
    create_parser.add_argument("--qemu-machine", default=None)
    create_parser.add_argument("--qemu-cpu", default=None)
    create_parser.add_argument("--qemu-ram-mb", type=int, default=None)
    create_parser.add_argument("record_bytes", nargs="?", default="", help="Path to record binary or '-' for stdin")

    # Export envelope to JSON
    export_parser = subparsers.add_parser("export", help="Export envelope to JSON")
    export_parser.add_argument("envelope_json", help="Path to existing envelope JSON or 'stdin'")

    # Validate envelope
    validate_parser = subparsers.add_parser("validate", help="Validate envelope integrity")
    validate_parser.add_argument("envelope_json", help="Path to envelope JSON")

    args = parser.parse_args()

    if args.command == "create":
        # Read record bytes
        if args.record_bytes == "-":
            import sys
            record_bytes = sys.stdin.buffer.read()
        elif args.record_bytes:
            with open(args.record_bytes, "rb") as f:
                record_bytes = f.read()
        else:
            print("Error: record bytes required", file=sys.stderr)
            sys.exit(1)

        # Build QEMU info dict if any QEMU fields provided
        qemu_info = None
        qemu_fields = ["qemu_version", "qemu_machine", "qemu_cpu", "qemu_ram_mb"]
        qemi = {k: getattr(args, k) for k in qemu_fields if getattr(args, k) is not None}
        if qemi:
            qemu_info = qemi

        envelope = make_from_record_bytes(
            record_bytes=record_bytes,
            source_image_sha256=args.sha256,
            source_image_size=args.size,
            image_base=args.image_base,
            address_space=args.address_space,
            decoder_result=None if args.decoder_status is None else {
                "status": args.decoder_status,
                "field_count": args.decoder_field_count,
                "slot": args.decoder_slot,
                "esr": args.decoder_esr,
                "elr": args.decoder_elr,
                "marker": args.decoder_marker,
            },
            qemu_info=qemu_info,
        )
        print(envelope.to_json())

    elif args.command == "export":
        try:
            if args.envelope_json == "stdin":
                import sys
                data = json.loads(sys.stdin.read())
            else:
                with open(args.envelope_json) as f:
                    data = json.load(f)
            envelope = CaptureEnvelope.from_json(json.dumps(data))
            print(envelope.to_json())
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "validate":
        try:
            with open(args.envelope_json) as f:
                data = json.load(f)
            envelope = CaptureEnvelope.from_json(json.dumps(data))
            valid, errors = envelope.validate()
            if valid:
                print("ENVELOPE VALID")
            else:
                print(f"ENVELOPE INVALID: {errors}")
                sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
