#!/usr/bin/env python3
"""
MyOS A8 entry-capture record — host-side reference encoder/parser.

OFLINE, HOST-ONLY. stdlib only. No USB, no DFU, no device, no payload
execution, no kernel dependency. This module is the reference implementation of
the record specified by M6_1_A8_ENTRY_CAPTURE_SPEC.md §3; it does not produce or
assert any real hardware value. Every field value in tests is explicitly
synthetic test data.

Record layout (all little-endian):

  Header (32 bytes)
    +0x00  u64  magic          = b"MYOSA8EC" (LE)
    +0x08  u32  version        = 1
    +0x0C  u32  record_size    = 32 + 16*field_count + 4
    +0x10  u32  field_count    0..MAX_FIELDS
    +0x14  u32  flags          reserved, must be 0
    +0x18  u64  capture_seq    caller label, 0 if unavailable

  Entry (16 bytes each, sorted ascending by field_id)
    +0x00  u32  field_id
    +0x04  u32  field_flags    exactly one of VALID or one UNAVAILABLE_* bit
    +0x08  u64  value          MUST be 0 unless VALID

  Trailer (4 bytes)
    u32 crc = CRC-32/ISO-HDLC over bytes [0, record_size-4)
              poly 0x04C11DB7 (reflected 0xEDB88320),
              init 0xFFFFFFFF, refin=refout=True, xorout 0xFFFFFFFF.
"""

import struct
import sys

MAGIC = b"MYOSA8EC"
VERSION = 1

HEADER_SIZE = 32
ENTRY_SIZE = 16
TRAILER_SIZE = 4
MAX_FIELDS = 16
MAX_RECORD_SIZE = HEADER_SIZE + ENTRY_SIZE * MAX_FIELDS + TRAILER_SIZE  # 292

# Header struct: magic(8s) version(I) record_size(I) field_count(I) flags(I) seq(Q)
_HEADER = struct.Struct("<8sIIIIQ")
_ENTRY = struct.Struct("<IIQ")
_U32 = struct.Struct("<I")

assert _HEADER.size == HEADER_SIZE
assert _ENTRY.size == ENTRY_SIZE
assert MAGIC == b"MYOSA8EC" and len(MAGIC) == 8
# Numeric little-endian magic value, for documentation/cross-checking:
MAGIC_LE = int.from_bytes(MAGIC, "little")  # 0x43453841334F594D

# --- field ids -------------------------------------------------------------
F_CURRENT_EL = 0x01
F_SCTLR_EL1 = 0x02
F_TTBR0_EL1 = 0x03
F_MIDR_EL1 = 0x04
F_CNTFRQ_EL0 = 0x05
F_CNTPCT_EL0_S1 = 0x06
F_CNTPCT_EL0_S2 = 0x07
F_CAPTURE_PC = 0x08
F_HANDOFF_X0 = 0x09

FIELD_NAMES = {
    F_CURRENT_EL: "CurrentEL",
    F_SCTLR_EL1: "SCTLR_EL1",
    F_TTBR0_EL1: "TTBR0_EL1",
    F_MIDR_EL1: "MIDR_EL1",
    F_CNTFRQ_EL0: "CNTFRQ_EL0",
    F_CNTPCT_EL0_S1: "CNTPCT_EL0[1]",
    F_CNTPCT_EL0_S2: "CNTPCT_EL0[2]",
    F_CAPTURE_PC: "capture_pc",
    F_HANDOFF_X0: "handoff_x0",
}
KNOWN_FIELD_IDS = frozenset(FIELD_NAMES)

# --- field flags -----------------------------------------------------------
F_VALID = 0x01
F_UNAVAILABLE_EXCEPTION_LEVEL = 0x02
F_UNAVAILABLE_TRAPPED = 0x04
F_UNAVAILABLE_NOT_PROGRAMMED = 0x08
F_UNAVAILABLE_NOT_CAPTURED = 0x10

UNAVAILABLE_BITS = frozenset({
    F_UNAVAILABLE_EXCEPTION_LEVEL,
    F_UNAVAILABLE_TRAPPED,
    F_UNAVAILABLE_NOT_PROGRAMMED,
    F_UNAVAILABLE_NOT_CAPTURED,
})
_KNOWN_FLAGS = F_VALID | 0x1E
_RESERVED_FLAG_MASK = ~_KNOWN_FLAGS & 0xFFFFFFFF

# Fields the spec marks as EL1-gated (advisory only; see advisory_issues).
EL1_GATED_FIELDS = frozenset({
    F_SCTLR_EL1, F_TTBR0_EL1, F_MIDR_EL1, F_CNTFRQ_EL0,
    F_CNTPCT_EL0_S1, F_CNTPCT_EL0_S2,
})
_U64_MAX = 0xFFFFFFFFFFFFFFFF


# --- CRC-32/ISO-HDLC -------------------------------------------------------

def _build_crc_table():
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
        table.append(c & 0xFFFFFFFF)
    return tuple(table)


_CRC_TABLE = _build_crc_table()


def crc32(data):
    """CRC-32/ISO-HDLC (a.k.a. zlib.crc32) over `data`."""
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


# --- error categories ------------------------------------------------------

class CaptureRecordError(Exception):
    """Base class for all capture-record validation failures."""


class BadMagic(CaptureRecordError):
    pass


class UnsupportedVersion(CaptureRecordError):
    pass


class SizeMismatch(CaptureRecordError):
    """Too short, truncated, oversized, trailing bytes, or a size/count
    combination that violates the fixed size formula."""


class BadCrc(CaptureRecordError):
    pass


class BadFlags(CaptureRecordError):
    pass


class ValueForUnavailable(CaptureRecordError):
    pass


class UnknownFieldId(CaptureRecordError):
    pass


class DuplicateField(CaptureRecordError):
    pass


class TooManyFields(CaptureRecordError):
    pass


class MissingCurrentEl(CaptureRecordError):
    pass


class InvalidField(CaptureRecordError):
    """Encoder input that cannot be represented in the format."""


# --- helpers ---------------------------------------------------------------

def record_size_for(field_count):
    return HEADER_SIZE + ENTRY_SIZE * field_count + TRAILER_SIZE


def _flag_error(field_id, flags):
    """Return an error string if `flags` is not exactly one valid choice."""
    flags &= 0xFFFFFFFF
    if flags & _RESERVED_FLAG_MASK:
        return f"field 0x{field_id:02x}: reserved flag bits set (0x{flags:08x})"
    has_valid = bool(flags & F_VALID)
    reasons = [b for b in UNAVAILABLE_BITS if flags & b]
    if has_valid and not reasons:
        return None
    if (not has_valid) and len(reasons) == 1:
        return None
    return (f"field 0x{field_id:02x}: flags 0x{flags:08x} must be exactly one of "
            f"VALID or a single UNAVAILABLE_* reason")


def _check_field(field_id, flags, value):
    if field_id not in KNOWN_FIELD_IDS:
        raise UnknownFieldId(f"unknown field id 0x{field_id:02x}")
    err = _flag_error(field_id, flags)
    if err is not None:
        raise BadFlags(err)
    if not (flags & F_VALID) and value != 0:
        raise ValueForUnavailable(
            f"field 0x{field_id:02x}: value 0x{value:x} with no VALID flag")


# --- encoder ---------------------------------------------------------------

def encode(fields, capture_seq=0, version=VERSION):
    """Serialize `fields` (iterable of (field_id, field_flags, value)).

    Fields are sorted by field_id so serialization is deterministic. Raises a
    CaptureRecordError subclass on invalid input; never silently normalizes.
    """
    if version != VERSION:
        raise UnsupportedVersion(f"cannot encode version {version}")

    if not isinstance(capture_seq, int) or isinstance(capture_seq, bool):
        raise InvalidField("capture_seq must be an int")
    if not (0 <= capture_seq <= _U64_MAX):
        raise InvalidField(f"capture_seq {capture_seq} out of u64 range")

    normalized = []
    seen = set()
    for item in fields:
        try:
            field_id, flags, value = item
        except (TypeError, ValueError):
            raise InvalidField(f"field {item!r} is not a 3-tuple")
        field_id = int(field_id)
        flags = int(flags)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidField("field value must be an int")
        if not (0 <= value <= _U64_MAX):
            raise InvalidField(f"field 0x{field_id:02x} value {value} out of u64 range")
        if not (0 <= field_id <= 0xFFFFFFFF and 0 <= flags <= 0xFFFFFFFF):
            raise InvalidField("field id/flags out of u32 range")
        if field_id in seen:
            raise DuplicateField(f"duplicate field id 0x{field_id:02x}")
        seen.add(field_id)
        _check_field(field_id, flags, value)
        normalized.append((field_id, flags, value))

    if len(normalized) > MAX_FIELDS:
        raise TooManyFields(f"{len(normalized)} fields exceeds {MAX_FIELDS}")

    # Semantic requirement (§3.1): a valid CurrentEL must be present.
    if F_CURRENT_EL not in seen:
        raise MissingCurrentEl("record has no CurrentEL field")
    cur_flags = next(f for i, f, _ in normalized if i == F_CURRENT_EL)
    if not (cur_flags & F_VALID):
        raise MissingCurrentEl("CurrentEL field is not VALID")

    normalized.sort(key=lambda t: t[0])
    record_size = record_size_for(len(normalized))

    out = bytearray()
    out += _HEADER.pack(MAGIC, VERSION, record_size, len(normalized), 0,
                        capture_seq)
    for field_id, flags, value in normalized:
        out += _ENTRY.pack(field_id, flags, value)
    out += _U32.pack(crc32(bytes(out)))
    assert len(out) == record_size
    return bytes(out)


# --- parser ----------------------------------------------------------------

def parse(buf):
    """Parse and validate a complete record. Raises CaptureRecordError."""
    buf = bytes(buf)
    if len(buf) < HEADER_SIZE + TRAILER_SIZE:
        raise SizeMismatch(
            f"{len(buf)} bytes is shorter than the minimum "
            f"{HEADER_SIZE + TRAILER_SIZE}")

    magic, version, record_size, field_count, flags, capture_seq = \
        _HEADER.unpack_from(buf, 0)

    if magic != MAGIC:
        raise BadMagic(f"magic {magic!r} != {MAGIC!r}")
    if version != VERSION:
        raise UnsupportedVersion(f"version {version} unsupported")
    if flags != 0:
        raise BadFlags(f"header reserved flags must be 0, got 0x{flags:08x}")
    if field_count > MAX_FIELDS:
        raise TooManyFields(f"field_count {field_count} exceeds {MAX_FIELDS}")

    expected = record_size_for(field_count)
    if record_size != expected:
        raise SizeMismatch(
            f"record_size {record_size} != formula {expected} "
            f"(field_count {field_count})")
    if len(buf) != record_size:
        raise SizeMismatch(
            f"buffer {len(buf)} bytes != record_size {record_size} "
            f"(truncated or trailing data)")

    stored_crc = _U32.unpack_from(buf, record_size - TRAILER_SIZE)[0]
    actual_crc = crc32(buf[:record_size - TRAILER_SIZE])
    if stored_crc != actual_crc:
        raise BadCrc(f"crc 0x{stored_crc:08x} != computed 0x{actual_crc:08x}")

    fields = []
    seen = set()
    for i in range(field_count):
        off = HEADER_SIZE + i * ENTRY_SIZE
        field_id, field_flags, value = _ENTRY.unpack_from(buf, off)
        if field_id in seen:
            raise DuplicateField(f"duplicate field id 0x{field_id:02x}")
        seen.add(field_id)
        _check_field(field_id, field_flags, value)
        fields.append({
            "field_id": field_id,
            "name": FIELD_NAMES.get(field_id),
            "flags": field_flags,
            "valid": bool(field_flags & F_VALID),
            "value": value,
        })

    if F_CURRENT_EL not in seen:
        raise MissingCurrentEl("record has no CurrentEL field")
    if not next(f for f in fields if f["field_id"] == F_CURRENT_EL)["valid"]:
        raise MissingCurrentEl("CurrentEL field is not VALID")

    return {
        "magic": magic.decode("ascii"),
        "magic_le": MAGIC_LE,
        "version": version,
        "record_size": record_size,
        "field_count": field_count,
        "flags": flags,
        "capture_seq": capture_seq,
        "crc": stored_crc,
        "fields": fields,
    }


def advisory_issues(record):
    """Non-fatal consistency warnings (never a hardware claim).

    Encodes the spec's conservative gate: while CurrentEL != EL1, an EL1-gated
    field that is marked VALID is worth flagging, because a producer must have a
    proven trap/vector path to have read it. This is advisory only; parse()
    does not reject on it.
    """
    issues = []
    cur = next((f for f in record["fields"] if f["field_id"] == F_CURRENT_EL), None)
    if cur is None or not cur["valid"]:
        return issues
    el = (cur["value"] >> 2) & 0x3
    if el != 1:
        for f in record["fields"]:
            if f["field_id"] in EL1_GATED_FIELDS and f["valid"]:
                issues.append(
                    f"{f['name']} VALID while CurrentEL reports EL{el} "
                    f"(requires a proven trap/vector path)")
    return issues


# --- self-check ------------------------------------------------------------

def _selftest():
    fields = [
        (F_CURRENT_EL, F_VALID, 0x4),
        (F_MIDR_EL1, F_VALID, 0x00000000AABBCCDD),
    ]
    blob = encode(fields, capture_seq=0x1122334455667788)
    rec = parse(blob)
    ok = (rec["field_count"] == 2 and rec["capture_seq"] == 0x1122334455667788
          and rec["fields"][0]["value"] == 0x4)
    print(f"selftest: {len(blob)} bytes, magic={rec['magic']}, "
          f"fields={[f['name'] for f in rec['fields']]}")
    print(f"selftest: crc32(b'123456789') = "
          f"0x{crc32(b'123456789'):08x} (expect 0xcbf43926)")
    print("selftest:", "PASS" if ok and crc32(b"123456789") == 0xCBF43926
          else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: capture_record.py --selftest", file=sys.stderr)
    sys.exit(2)
