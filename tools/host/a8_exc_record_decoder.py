#!/usr/bin/env python3
"""
MyOS A8 raw-entry exception record decoder.

Parses the file-backed exception record format specified by raw_vectors.S and
raw_entry.ld for the Apple A8 PongoOS `bootr` RAW entry path.

Record layout (all little-endian, 72 bytes / 0x48 total), as defined by
raw_vectors.S section .excrecord:

  Offset  Size  Field
    +0x00   8    Magic "MYOSA8E1"
    +0x08   4    Version (u32, must be 1)
    +0x0C   4    Record size (u32, must be 0x48 / 72)
    +0x10   8    Slot index (u64, 0..15)
    +0x18   8    ESR_EL1 (u64)
    +0x20   8    ELR_EL1 (u64)
    +0x28   8    FAR_EL1 (u64)
    +0x30   8    SPSR_EL1 (u64)
    +0x38   8    SP at entry (u64, before re-pointing)
    +0x40   8    Valid marker (u64, must be 1 for complete record)
"""

import struct
import sys
from enum import IntEnum
from typing import Optional, Tuple


# --- Error classes ---

class DecodeError(Exception):
    """Base class for decode failures."""
    pass


class BadMagic(DecodeError):
    pass


class UnsupportedVersion(DecodeError):
    pass


class SizeMismatch(DecodeError):
    pass


# --- Record status classification ---

class RecordStatus(IntEnum):
    """Classification of the record state."""
    VALID = 0           # Complete, valid marker set
    INCOMPLETE = 1      # Marker absent (0), fields partially written
    TRUNCATED = 2       # Input too short
    MALFORMED = 3       # Bad magic, bad version, size mismatch


# --- Record data ---

class ExcRecord:
    """Decoded exception record with classification."""
    __slots__ = (
        "status", "raw", "magic", "version", "record_size",
        "slot", "esr", "elr", "far", "spsr", "sp", "valid_marker",
    )

    # Class-level status constants for backward compatibility with tests
    MALFORMED = RecordStatus.MALFORMED
    VALID = RecordStatus.VALID
    INCOMPLETE = RecordStatus.INCOMPLETE
    TRUNCATED = RecordStatus.TRUNCATED

    def __init__(
        self,
        status: int,
        raw: bytes,
        magic: bytes,
        version: int,
        record_size: int,
        slot: Optional[int] = None,
        esr: Optional[int] = None,
        elr: Optional[int] = None,
        far: Optional[int] = None,
        spsr: Optional[int] = None,
        sp: Optional[int] = None,
        valid_marker: Optional[int] = None,
    ):
        self.status = status
        self.raw = raw
        self.magic = magic
        self.version = version
        self.record_size = record_size
        self.slot = slot
        self.esr = esr
        self.elr = elr
        self.far = far
        self.spsr = spsr
        self.sp = sp
        self.valid_marker = valid_marker


# Human-readable status name

def status_name(status: RecordStatus) -> str:
    """Return a human-readable name for a RecordStatus."""
    names = {
        RecordStatus.VALID: "VALID (complete, marker=1)",
        RecordStatus.INCOMPLETE: "INCOMPLETE (marker=0, partial fields)",
        RecordStatus.TRUNCATED: "TRUNCATED (input too short)",
        RecordStatus.MALFORMED: "MALFORMED (bad magic/version/size)",
    }
    return names.get(status, f"UNKNOWN({status})")


def classify_status(record_or_status):
    """Get human-readable status name from a RecordStatus or ExcRecord.

    Accepts either a RecordStatus enum or an ExcRecord object,
    returning the corresponding human-readable string.
    """
    if hasattr(record_or_status, 'status'):
        # It's an ExcRecord object
        return status_name(record_or_status.status)
    else:
        # It's a RecordStatus enum
        return status_name(record_or_status)


# --- Decoder ---

def decode(buf: bytes) -> Tuple[Optional[ExcRecord], Optional[str]]:
    """
    Decode and classify an A8 raw-entry exception record.

    Returns (record, error) where:
      - record is populated if input has sufficient bytes for partial decode
      - error is None on success (record classified), or a string describing
        the failure if decode cannot proceed
    """
    if not buf:
        return None, "empty input"

    available = len(buf)

    # Initialize record structure
    record = ExcRecord(
        status=RecordStatus.MALFORMED,
        raw=buf,
        magic=buf[0:8] if available >= 8 else buf[0:available],
        version=0,
        record_size=0,
        slot=None, esr=None, elr=None, far=None,
        spsr=None, sp=None, valid_marker=None,
    )

    # --- Magic validation (8 bytes at offset 0) ---
    if available < 8:
        # Shorter than the magic field itself: structurally truncated input,
        # not a malformed record (nothing here can contradict the magic).
        record.magic = buf[0:available] if available > 0 else b""
        record.status = RecordStatus.TRUNCATED
        return record, None

    magic = buf[0:8]
    if magic != MAGIC:
        record.magic = magic
        return record, None  # Bad magic; can still classify

    record.magic = magic

    # --- Version validation (4 bytes at offset 8) ---
    if available < 8 + 4:
        record.status = ExcRecord.TRUNCATED
        return record, None

    version = struct.unpack_from("<I", buf, 8)[0]
    record.version = version

    if version != EXC_VERSION:
        # Valid magic but wrong version -> MALFORMED
        return record, None

    record.version = version

    # --- Record size validation (4 bytes at offset 12) ---
    # Only validate record_size if we have enough bytes to potentially
    # read the full record. If too short, classify as TRUNCATED.
    if available < EXC_SIZE:
        record.status = ExcRecord.TRUNCATED
        return record, None

    record_size = struct.unpack_from("<I", buf, 12)[0]
    record.record_size = record_size

    if record_size != EXC_SIZE:
        # Valid magic + version but wrong size -> MALFORMED
        return record, None

    # --- Successfully past header (12 bytes); read remaining fields ---
    # Slot (u64 at offset 0x10)
    if available >= 0x10 + 8:
        record.slot = struct.unpack_from("<Q", buf, 0x10)[0]

    # ESR (u64 at offset 0x18)
    if available >= 0x18 + 8:
        record.esr = struct.unpack_from("<Q", buf, 0x18)[0]

    # ELR (u64 at offset 0x20)
    if available >= 0x20 + 8:
        record.elr = struct.unpack_from("<Q", buf, 0x20)[0]

    # FAR (u64 at offset 0x28)
    if available >= 0x28 + 8:
        record.far = struct.unpack_from("<Q", buf, 0x28)[0]

    # SPSR (u64 at offset 0x30)
    if available >= 0x30 + 8:
        record.spsr = struct.unpack_from("<Q", buf, 0x30)[0]

    # SP (u64 at offset 0x38)
    if available >= 0x38 + 8:
        record.sp = struct.unpack_from("<Q", buf, 0x38)[0]

    # Valid marker (u64 at offset 0x40)
    if available >= 0x40 + 8:
        record.valid_marker = struct.unpack_from("<Q", buf, 0x40)[0]

    # --- Classify based on available data ---
    return _classify_record(record), None


def _classify_record(rec: ExcRecord) -> ExcRecord:
    """Classify the record status based on field values."""

    # If we couldn't read the marker due to truncation
    if rec.valid_marker is None:
        if rec.status == ExcRecord.MALFORMED:
            return rec  # already malformed from earlier check
        rec.status = ExcRecord.TRUNCATED
        return rec

    # Marker present and valid (value 1)
    if rec.valid_marker == 1:
        rec.status = ExcRecord.VALID
        return rec

    # Marker present but zero -> INCOMPLETE
    if rec.valid_marker == 0:
        rec.status = ExcRecord.INCOMPLETE
        return rec

    # Marker has some other value -> INCOMPLETE
    rec.status = ExcRecord.INCOMPLETE
    return rec


# --- Convenience: decode from file path ---

def decode_file(path: str) -> Tuple[Optional[ExcRecord], Optional[str]]:
    """Decode record from a file path."""
    try:
        with open(path, "rb") as f:
            buf = f.read()
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except OSError as e:
        return None, f"read error: {e}"
    return decode(buf)


# --- Module-level constants for test imports ---

MAGIC = b"MYOSA8E1"
EXC_VERSION = 1
EXC_SIZE = 0x48


# --- Standalone CLI ---

def main():
    """CLI entry point: decode a record file and print machine-readable output."""
    import argparse
    parser = argparse.ArgumentParser(
        description="MyOS A8 raw-entry exception record decoder"
    )
    parser.add_argument(
        "file", nargs="?", help="Path to raw binary record file"
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="Print only the classification status"
    )
    args = parser.parse_args()

    if args.file:
        record, error = decode_file(args.file)
    else:
        # Read from stdin
        try:
            buf = sys.stdin.buffer.read()
        except Exception as e:
            print(f"Error reading stdin: {e}", file=sys.stderr)
            sys.exit(1)
        record, error = decode(buf)

    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if args.brief:
        print(status_name(record.status) if record else "NONE")
        sys.exit(0)

    # Print machine-readable output
    if record:
        print(f"magic: {record.magic!r}")
        print(f"version: {record.version}")
        print(f"record_size: {record.record_size} (expected {EXC_SIZE})")
        print(f"status: {status_name(record.status)}")
        print(f"slot: {record.slot}")
        print(f"esr: 0x{record.esr:x}" if record.esr is not None else "esr: <unavailable>")
        print(f"elr: 0x{record.elr:x}" if record.elr is not None else "elr: <unavailable>")
        print(f"far: 0x{record.far:x}" if record.far is not None else "far: <unavailable>")
        print(f"spsr: 0x{record.spsr:x}" if record.spsr is not None else "spsr: <unavailable>")
        print(f"sp: 0x{record.sp:x}" if record.sp is not None else "sp: <unavailable>")
        print(f"valid_marker: {record.valid_marker}")
    else:
        print("failed to decode")

    sys.exit(0)


if __name__ == "__main__":
    main()