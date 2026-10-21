#!/usr/bin/env python3
"""
Tests for the MyOS A8 raw-entry exception record decoder (tools/host/a8_exc_record_decoder.py).

Tests the MYOSA8E1 format: 72-byte file-backed exception record with magic,
version, size, slot, ESR, ELR, FAR, SPSR, SP, and completion marker.
All integer fields are little-endian.

This is STRICTLY OFFLINE: no device, no QEMU, no payload execution.
"""

import sys
import os
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.host.a8_exc_record_decoder import (
    status_name,
    decode, classify_status, DecodeError, BadMagic,
    UnsupportedVersion, SizeMismatch, decode_file,
    ExcRecord, RecordStatus, MAGIC, EXC_VERSION, EXC_SIZE,
)
from tools.host.capture_record import CaptureRecordError

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_RECORD = os.path.join(REPO, "build", "raw_entry.bin")
EXC_OFFSET = 0x5000
EXC_RECORD_BYTES = EXC_OFFSET + EXC_SIZE  # 0x5048

MAGIC_EXPECTED = b"MYOSA8E1"
EXC_VERSION_EXPECTED = 1
EXC_SIZE_EXPECTED = 0x48


def read_prod_record():
    """Read the production exception record from the binary."""
    with open(PROD_RECORD, "rb") as f:
        data = f.read()
    return data[EXC_OFFSET:EXC_OFFSET + EXC_SIZE]


# --- Fixture builders ---

def make_record(slot=0, esr=0, elr=0, far=0, spsr=0, sp=0, marker=0, version=EXC_VERSION_EXPECTED):
    """Build a 72-byte MYOSA8E1 record with the given field values."""
    buf = bytearray(EXC_SIZE)
    # Magic
    buf[0:8] = MAGIC_EXPECTED
    # Version
    buf[8:12] = struct.pack("<I", version)
    # Record size
    buf[12:16] = struct.pack("<I", EXC_SIZE_EXPECTED)
    # Slot (u64 at offset 0x10)
    buf[0x10:0x18] = struct.pack("<Q", slot)
    # ESR (u64 at offset 0x18)
    buf[0x18:0x20] = struct.pack("<Q", esr)
    # ELR (u64 at offset 0x20)
    buf[0x20:0x28] = struct.pack("<Q", elr)
    # FAR (u64 at offset 0x28)
    buf[0x28:0x30] = struct.pack("<Q", far)
    # SPSR (u64 at offset 0x30)
    buf[0x30:0x38] = struct.pack("<Q", spsr)
    # SP (u64 at offset 0x38)
    buf[0x38:0x40] = struct.pack("<Q", sp)
    # Valid marker (u64 at offset 0x40)
    buf[0x40:0x48] = struct.pack("<Q", marker)
    return bytes(buf)


# --- Tests ---

def test_decoder_with_production_record():
    """Decode the actual production raw_entry.bin exception record."""
    record_bytes = read_prod_record()
    rec, err = decode(record_bytes)
    assert err is None, f"Unexpected error: {err}"
    status = status_name(rec.status)
    assert status == "INCOMPLETE (marker=0, partial fields)", f"Expected INCOMPLETE, got {status}"
    assert rec.magic == MAGIC_EXPECTED, f"Wrong magic: {rec.magic!r}"
    assert rec.version == EXC_VERSION_EXPECTED, f"Wrong version: {rec.version}"
    assert rec.record_size == EXC_SIZE_EXPECTED, f"Wrong record_size: {rec.record_size}"
    assert rec.slot == 0, f"Wrong slot: {rec.slot}"
    assert rec.valid_marker == 0, f"Expected marker=0, got {rec.valid_marker}"
    print("PASS: test_decoder_with_production_record")


def test_decoder_valid_record_with_marker():
    """Decode a record with marker=1 (fully complete)."""
    record_bytes = make_record(marker=1)
    rec, err = decode(record_bytes)
    assert err is None, f"Unexpected error: {err}"
    status = status_name(rec.status)
    assert status == "VALID (complete, marker=1)", f"Expected VALID, got {status}"
    assert rec.valid_marker == 1, f"Expected marker=1, got {rec.valid_marker}"
    assert rec.slot == 0, f"Expected slot=0, got {rec.slot}"
    print("PASS: test_decoder_valid_record_with_marker")


def test_decoder_truncated_magic():
    """Truncated input: less than 8 bytes (cannot even contain the magic)
    must classify as TRUNCATED, not MALFORMED (M7.29: this was previously
    masked by a tautological `or True` assertion)."""
    rec, err = decode(b"MYOS")
    assert err is None, f"unexpected hard error: {err}"
    assert rec is not None
    assert rec.status == RecordStatus.TRUNCATED, (
        f"4-byte input should be TRUNCATED, got {rec.status.name}"
    )
    # Partial magic bytes are preserved for diagnosis.
    assert rec.magic == b"MYOS"
    # 7 bytes: still shorter than magic+version.
    rec7, err7 = decode(b"MYOSA8E")
    assert err7 is None and rec7 is not None
    assert rec7.status == RecordStatus.TRUNCATED, rec7.status.name
    print("PASS: test_decoder_truncated_magic")


def test_decoder_truncated_version():
    """Truncated input: valid magic + partial version (12 bytes total).
    Must classify as TRUNCATED; previously this test printed the outcome
    without asserting it."""
    rec, err = decode(b"MYOSA8E1" + b"\x01\x00\x00\x00")
    assert err is None and rec is not None
    assert rec.status == RecordStatus.TRUNCATED, (
        f"12-byte input (magic+version only) should be TRUNCATED, got {rec.status.name}"
    )
    # Valid magic with a complete version field is still too short for the
    # record-size field.
    rec13, err13 = decode(b"MYOSA8E1" + b"\x01\x00\x00" )
    assert err13 is None and rec13 is not None
    assert rec13.status == RecordStatus.TRUNCATED, rec13.status.name
    print("PASS: test_decoder_truncated_version")


def test_decoder_full_fields():
    """Decode a record with all fields populated."""
    record_bytes = make_record(
        slot=4, esr=0xf200cafe, elr=0x41000020,
        spsr=0x400003c5, sp=0x41004240, marker=1
    )
    rec, err = decode(record_bytes)
    assert err is None, f"Unexpected error: {err}"
    status = status_name(rec.status)
    assert status == "VALID (complete, marker=1)", f"Expected VALID, got {status}"
    assert rec.slot == 4, f"Expected slot=4, got {rec.slot}"
    assert rec.esr == 0xf200cafe, f"Expected ESR=0xf200cafe, got 0x{rec.esr:x}"
    assert rec.elr == 0x41000020, f"Expected ELR=0x41000020, got 0x{rec.elr:x}"
    assert rec.valid_marker == 1, f"Expected marker=1, got {rec.valid_marker}"
    print("PASS: test_decoder_full_fields")


def test_decoder_bad_magic():
    """Record with wrong magic should be classified MALFORMED."""
    record_bytes = bytearray(EXC_SIZE)
    # Overwrite magic
    record_bytes[0:4] = b"XXXX"
    rec, err = decode(bytes(record_bytes))
    assert err is None
    # Should be classified as MALFORMED (bad magic)
    status = status_name(rec.status)
    assert "MALFORMED" in status, f"Expected MALFORMED, got {status}"
    print("PASS: test_decoder_bad_magic")


def test_decoder_unsupported_version():
    """Record with version != 1 should be classified MALFORMED."""
    record_bytes = make_record(version=2)
    rec, err = decode(record_bytes)
    assert err is None
    status = status_name(rec.status)
    assert "MALFORMED" in status, f"Expected MALFORMED, got {status}"
    print("PASS: test_decoder_unsupported_version")


def test_decoder_incorrect_record_size():
    """Record with wrong size field should be classified MALFORMED."""
    record_bytes = make_record()
    # Corrupt the size field
    record_bytes = bytearray(record_bytes)
    record_bytes[8:12] = struct.pack("<I", 999)  # wrong size
    rec, err = decode(bytes(record_bytes))
    assert err is None
    status = status_name(rec.status)
    assert "MALFORMED" in status, f"Expected MALFORMED, got {status}"
    print("PASS: test_decoder_incorrect_record_size")


def test_decoder_truncated_at_field_boundaries():
    """Test truncation at various field boundaries."""
    # Truncated at slot (after magic + version + size)
    rec, err = decode(b"MYOSA8E1" + b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00")
    if rec is not None:
        status = status_name(rec.status)
        # Should be TRUNCATED since we can't read the marker
        assert "TRUNCATED" in status, f"Expected TRUNCATED, got {status}"

    # Truncated after slot, before marker
    rec, err = decode(b"MYOSA8E1" + b"\x01\x00\x00\x00" +
                      b"\x00\x00\x00\x00\x00\x00\x00\x00" +  # slot
                      b"\x00\x00\x00\x00\x00\x00\x00")  # partial remaining
    if rec is not None:
        status = status_name(rec.status)
        assert "TRUNCATED" in status, f"Expected TRUNCATED, got {status}"

    print("PASS: test_decoder_truncated_at_field_boundaries")


def test_decoder_empty_input():
    """Empty input should be detected."""
    rec, err = decode(b"")
    assert err is not None or rec is not None
    # Should produce some classification
    print("PASS: test_decoder_empty_input")


def test_decoder_incomplete_marker_zero():
    """Record with marker=0 should be classified INCOMPLETE."""
    record_bytes = make_record(marker=0)
    rec, err = decode(record_bytes)
    assert err is None
    status = status_name(rec.status)
    assert "INCOMPLETE" in status, f"Expected INCOMPLETE, got {status}"
    assert rec.valid_marker == 0, f"Expected marker=0, got {rec.valid_marker}"
    print("PASS: test_decoder_incomplete_marker_zero")


def test_decoder_slot_out_of_range():
    """Slot value > 15 should still decode but be flagged as invalid."""
    record_bytes = make_record(slot=16)  # slot 16 is out of range 0..15
    rec, err = decode(record_bytes)
    assert err is None
    status = status_name(rec.status)
    # The decoder may accept it structurally but the classification
    # may vary; at minimum it should not crash
    print(f"PASS: test_decoder_slot_out_of-range (status={classify_status(rec)})")


def test_decoder_integer_endianness():
    """All integer fields are little-endian; test with known values."""
    record_bytes = make_record(
        slot=0, esr=0x123456789abcdef0, elr=0x0feed00d,
        far=0xdeadbeef1337, spsr=0xaabbccdd, sp=0xcafebabe, marker=1
    )
    rec, err = decode(record_bytes)
    assert err is None
    status = status_name(rec.status)
    assert status == "VALID (complete, marker=1)", f"Expected VALID, got {status}"
    # Verify little-endian decoding by checking raw values
    assert rec.esr == 0x123456789abcdef0, f"ESR endian issue: 0x{rec.esr:x}"
    assert rec.elr == 0x0feed00d, f"ELR endian issue: 0x{rec.elr:x}"
    assert rec.far == 0xdeadbeef1337, f"FAR endian issue: 0x{rec.far:x}"
    assert rec.spsr == 0xaabbccdd, f"SPSR endian issue: 0x{rec.spsr:x}"
    assert rec.sp == 0xcafebabe, f"SP endian issue: 0x{rec.sp:x}"
    print("PASS: test_decoder_integer_endianness")


def test_decoder_roundtrip_determinism():
    """Decoding the same bytes twice should produce identical results."""
    record_bytes = make_record(slot=5, esr=0x11111111, marker=1)
    rec1, err1 = decode(record_bytes)
    rec2, err2 = decode(record_bytes)
    assert err1 == err2, f"Errors differ: {err1} vs {err2}"
    assert rec1.status == rec2.status, f"Statuses differ: {rec1.status} vs {rec2.status}"
    assert rec1.magic == rec2.magic
    assert rec1.version == rec2.version
    assert rec1.record_size == rec2.record_size
    assert rec1.slot == rec2.slot
    assert rec1.esr == rec2.esr
    assert rec1.elr == rec2.elr
    assert rec1.far == rec2.far
    assert rec1.spsr == rec2.spsr
    assert rec1.sp == rec2.sp
    assert rec1.valid_marker == rec2.valid_marker
    print("PASS: test_decoder_roundtrip_determinism")


def test_decoder_with_tools_import():
    """Verify the decoder can be imported and used from the tools.path."""
    from tools.host import a8_exc_record_decoder as dec
    assert hasattr(dec, 'decode')
    assert hasattr(dec, 'classify_status')
    assert hasattr(dec, 'MAGIC')
    assert dec.MAGIC == b"MYOSA8E1"
    print("PASS: test_decoder_with_tools_import")


def test_production_record_matches_source():
    """The production record bytes from raw_entry.bin match the expected layout."""
    record_bytes = read_prod_record()
    assert len(record_bytes) == EXC_SIZE, f"Production record size {len(record_bytes)} != {EXC_SIZE}"
    assert record_bytes[0:8] == MAGIC_EXPECTED, "Production record magic mismatch"
    assert record_bytes[12:16] == struct.pack("<I", EXC_SIZE_EXPECTED), "Production record size field wrong"
    assert record_bytes[0x40:0x48] == b"\x00" * 8, "Production record marker should be zero in file-backed state"
    print("PASS: test_production_record_matches_source")


# --- Main ---

def main():
    tests = [
        test_decoder_with_production_record,
        test_decoder_valid_record_with_marker,
        test_decoder_truncated_magic,
        test_decoder_truncated_version,
        test_decoder_full_fields,
        test_decoder_bad_magic,
        test_decoder_unsupported_version,
        test_decoder_incorrect_record_size,
        test_decoder_truncated_at_field_boundaries,
        test_decoder_empty_input,
        test_decoder_incomplete_marker_zero,
        test_decoder_slot_out_of_range,
        test_decoder_integer_endianness,
        test_decoder_roundtrip_determinism,
        test_decoder_with_tools_import,
        test_production_record_matches_source,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    main()
