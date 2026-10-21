#!/usr/bin/env python3
"""
Offline tests for the A8 entry-capture record (tools/host/capture_record.py).

ALL FIELD VALUES HERE ARE SYNTHETIC TEST DATA. Nothing in this file is a
captured iPhone value, and no test encodes an "expected" hardware register
value. The record format is defined by M6_1_A8_ENTRY_CAPTURE_SPEC.md §3.

The byte-exact fixture below is written out by hand (independent of the
encoder), and its CRC is computed with zlib (an independent implementation),
so the encoder is never validated only against itself.
"""

import os
import struct
import sys
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "host"))

import capture_record as cr  # noqa: E402

passed = 0
failed = 0


def report(name, ok, detail=""):
    global passed, failed
    if ok:
        print(f"PASS: {name}")
        passed += 1
    else:
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
        failed += 1


def expect_raises(name, exc, fn):
    try:
        fn()
    except exc:
        report(name, True)
    except Exception as e:  # noqa: BLE001
        report(name, False, f"raised {type(e).__name__}: {e}")
    else:
        report(name, False, f"did not raise {exc.__name__}")


# --------------------------------------------------------------------------
# Hand-written, independently-derived fixture
# --------------------------------------------------------------------------

# Two entries: CurrentEL (VALID, 0x4) and MIDR_EL1 (VALID, 0x...AABBCCDD).
FIXTURE_SEQ = 0x1122334455667788
FIXTURE_PREFIX = bytes([
    # magic "MYOSA8EC"
    0x4D, 0x59, 0x4F, 0x53, 0x41, 0x38, 0x45, 0x43,
    # version = 1
    0x01, 0x00, 0x00, 0x00,
    # record_size = 68 (0x44)
    0x44, 0x00, 0x00, 0x00,
    # field_count = 2
    0x02, 0x00, 0x00, 0x00,
    # flags = 0
    0x00, 0x00, 0x00, 0x00,
    # capture_seq = 0x1122334455667788
    0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11,
    # entry 0: CurrentEL id=1 flags=VALID value=0x4
    0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # entry 1: MIDR_EL1 id=4 flags=VALID value=0x00000000AABBCCDD
    0x04, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
    0xDD, 0xCC, 0xBB, 0xAA, 0x00, 0x00, 0x00, 0x00,
])
FIXTURE_CRC = zlib.crc32(FIXTURE_PREFIX) & 0xFFFFFFFF
FIXTURE_BYTES = FIXTURE_PREFIX + struct.pack("<I", FIXTURE_CRC)


def fixture_fields():
    return [
        (cr.F_CURRENT_EL, cr.F_VALID, 0x4),
        (cr.F_MIDR_EL1, cr.F_VALID, 0x00000000AABBCCDD),
    ]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_standard_crc_vector():
    # Independent known answer for CRC-32/ISO-HDLC.
    report("crc32('123456789') == 0xCBF43926",
           cr.crc32(b"123456789") == 0xCBF43926)
    report("crc32 agrees with zlib for fixture prefix",
           cr.crc32(FIXTURE_PREFIX) == FIXTURE_CRC)


def test_expected_bytes_independent():
    blob = cr.encode(fixture_fields(), capture_seq=FIXTURE_SEQ)
    report("encoder output equals hand-written bytes + zlib CRC",
           blob == FIXTURE_BYTES,
           f"got {blob.hex()} want {FIXTURE_BYTES.hex()}")
    report("fixture size is 68 bytes", len(FIXTURE_BYTES) == 68)


def test_round_trip():
    blob = cr.encode(fixture_fields(), capture_seq=FIXTURE_SEQ)
    rec = cr.parse(blob)
    report("round-trip magic", rec["magic"] == "MYOSA8EC")
    report("round-trip version", rec["version"] == 1)
    report("round-trip record_size", rec["record_size"] == 68)
    report("round-trip field_count", rec["field_count"] == 2)
    report("round-trip capture_seq", rec["capture_seq"] == FIXTURE_SEQ)
    report("round-trip currentEL value", rec["fields"][0]["value"] == 0x4)
    report("round-trip fields are valid",
           all(f["valid"] for f in rec["fields"]))


def test_bad_magic():
    bad = bytearray(FIXTURE_BYTES)
    bad[0] = 0x00
    expect_raises("bad magic rejected", cr.BadMagic, lambda: cr.parse(bytes(bad)))


def test_versions():
    # Supported: version 1 parses.
    report("version 1 accepted", cr.parse(FIXTURE_BYTES)["version"] == 1)

    for v in (0, 2, 0xFFFFFFFF):
        bad = bytearray(FIXTURE_BYTES)
        struct.pack_into("<I", bad, 8, v)
        # cipher fix: recompute CRC so only the version is wrong? No — version
        # is checked before CRC, so a raw edit suffices.
        expect_raises(f"version {v} rejected",
                      cr.UnsupportedVersion, lambda b=bytes(bad): cr.parse(b))

    expect_raises("encoder rejects version 2", cr.UnsupportedVersion,
                  lambda: cr.encode(fixture_fields(), version=2))


def test_lengths():
    expect_raises("11-byte buffer too short", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES[:11]))
    expect_raises("20-byte header-only truncated", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES[:20]))
    expect_raises("35 bytes (< header+trailer) too short", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES[:35]))

    # Minimum structurally-valid length: header + trailer, zero entries.
    min_req = cr.record_size_for(0)
    report("minimum structural record size is 36", min_req == 36)
    hdr = struct.pack("<8sIIIIQ", cr.MAGIC, 1, 36, 0, 0, 0)
    body = hdr + struct.pack("<I", cr.crc32(hdr))
    # Structurally fine, but missing CurrentEL => semantic reject.
    expect_raises("zero-field record rejected (no CurrentEL)",
                  cr.MissingCurrentEl, lambda: cr.parse(body))

    # Truncated body: drop the last payload byte.
    expect_raises("truncated body rejected", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES[:-1]))


def test_oversized_and_trailing():
    expect_raises("trailing byte rejected", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES + b"\x00"))
    expect_raises("oversized copy rejected", cr.SizeMismatch,
                  lambda: cr.parse(FIXTURE_BYTES + FIXTURE_BYTES))

    # record_size lies about the real length.
    bad = bytearray(FIXTURE_BYTES)
    struct.pack_into("<I", bad, 12, 0x200)
    expect_raises("record_size not matching buffer rejected", cr.SizeMismatch,
                  lambda: cr.parse(bytes(bad)))


def test_crc():
    bad = bytearray(FIXTURE_BYTES)
    bad[-1] ^= 0xFF
    expect_raises("crc mismatch rejected", cr.BadCrc,
                  lambda: cr.parse(bytes(bad)))

    # Corrupt a value byte without fixing the CRC.
    bad2 = bytearray(FIXTURE_BYTES)
    bad2[56] ^= 0x01  # inside entry 1 value
    expect_raises("payload corruption detected via crc", cr.BadCrc,
                  lambda: cr.parse(bytes(bad2)))


def test_invalid_flags():
    def with_flags(field_flags, value=0x4):
        hdr = struct.pack("<8sIIIIQ", cr.MAGIC, 1, 52, 1, 0, 0)
        ent = struct.pack("<IIQ", cr.F_CURRENT_EL, field_flags, value)
        pre = hdr + ent
        return pre + struct.pack("<I", cr.crc32(pre))

    expect_raises("reserved flag bit rejected", cr.BadFlags,
                  lambda: cr.parse(with_flags(cr.F_VALID | 0x20)))
    expect_raises("zero flags rejected", cr.BadFlags,
                  lambda: cr.parse(with_flags(0, 0)))
    expect_raises("VALID + UNAVAILABLE rejected", cr.BadFlags,
                  lambda: cr.parse(with_flags(cr.F_VALID | cr.F_UNAVAILABLE_TRAPPED)))
    expect_raises("two UNAVAILABLE reasons rejected", cr.BadFlags,
                  lambda: cr.parse(with_flags(
                      cr.F_UNAVAILABLE_TRAPPED | cr.F_UNAVAILABLE_NOT_CAPTURED, 0)))
    # A single UNAVAILABLE reason is structurally fine (but CurrentEL then not
    # VALID => MissingCurrentEl at the semantic layer).
    expect_raises("unavailable CurrentEL rejected semantically",
                  cr.MissingCurrentEl,
                  lambda: cr.parse(with_flags(cr.F_UNAVAILABLE_NOT_CAPTURED, 0)))


def test_zero_vs_unavailable():
    # VALID with value 0 is allowed and round-trips as a real zero.
    blob = cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                      (cr.F_HANDOFF_X0, cr.F_VALID, 0)], capture_seq=0)
    rec = cr.parse(blob)
    x0 = next(f for f in rec["fields"] if f["field_id"] == cr.F_HANDOFF_X0)
    report("VALID zero value keeps valid flag", x0["valid"] and x0["value"] == 0)

    # Unavailable with a non-zero value is rejected.
    blob2 = cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                       (cr.F_TTBR0_EL1, cr.F_UNAVAILABLE_EXCEPTION_LEVEL, 0)])
    rec2 = cr.parse(blob2)
    ttbr = next(f for f in rec2["fields"] if f["field_id"] == cr.F_TTBR0_EL1)
    report("UNAVAILABLE field decodes as invalid", not ttbr["valid"])
    expect_raises("encoder rejects nonzero unavailable value",
                  cr.ValueForUnavailable,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                                     (cr.F_TTBR0_EL1,
                                      cr.F_UNAVAILABLE_EXCEPTION_LEVEL, 0x40)]))


def test_integer_boundaries():
    blob = cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                      (cr.F_CAPTURE_PC, cr.F_VALID, 0xFFFFFFFFFFFFFFFF)],
                     capture_seq=0xFFFFFFFFFFFFFFFF)
    rec = cr.parse(blob)
    report("max u64 value round-trips",
           rec["fields"][1]["value"] == 0xFFFFFFFFFFFFFFFF)
    report("max capture_seq round-trips",
           rec["capture_seq"] == 0xFFFFFFFFFFFFFFFF)

    expect_raises("value above u64 rejected", cr.InvalidField,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                                     (cr.F_CAPTURE_PC, cr.F_VALID, 1 << 64)]))
    expect_raises("negative value rejected", cr.InvalidField,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                                     (cr.F_CAPTURE_PC, cr.F_VALID, -1)]))
    expect_raises("negative capture_seq rejected", cr.InvalidField,
                  lambda: cr.encode(fixture_fields(), capture_seq=-1))


def test_counts_and_overflow():
    # field_count huge but buffer tiny.
    bad = bytearray(FIXTURE_BYTES)
    struct.pack_into("<I", bad, 16, 0xFFFFFFFF)
    expect_raises("huge field_count rejected", cr.TooManyFields,
                  lambda: cr.parse(bytes(bad)))

    # field_count just over the max, with a self-consistent record_size.
    count = cr.MAX_FIELDS + 1
    size = cr.record_size_for(count)
    hdr = struct.pack("<8sIIIIQ", cr.MAGIC, 1, size, count, 0, 0)
    body = hdr + b"\x00" * (cr.ENTRY_SIZE * count) + b"\x00" * 4
    expect_raises("field_count over max rejected", cr.TooManyFields,
                  lambda: cr.parse(body))

    # record_size arithmetic overflow attempt: record_size = 0xFFFFFFFF.
    bad2 = bytearray(FIXTURE_BYTES)
    struct.pack_into("<I", bad2, 12, 0xFFFFFFFF)
    expect_raises("record_size overflow rejected", cr.SizeMismatch,
                  lambda: cr.parse(bytes(bad2)))


def test_unknown_and_duplicate_fields():
    def rec_with(entries):
        hdr = struct.pack("<8sIIIIQ", cr.MAGIC, 1,
                          cr.record_size_for(len(entries)), len(entries), 0, 0)
        pre = hdr + b"".join(entries)
        return pre + struct.pack("<I", cr.crc32(pre))

    unknown = rec_with([struct.pack("<IIQ", 0x7F, cr.F_VALID, 0)])
    expect_raises("unknown field id rejected", cr.UnknownFieldId,
                  lambda: cr.parse(unknown))

    dup = rec_with([
        struct.pack("<IIQ", cr.F_CURRENT_EL, cr.F_VALID, 0x4),
        struct.pack("<IIQ", cr.F_CURRENT_EL, cr.F_VALID, 0x4),
    ])
    expect_raises("duplicate field id rejected", cr.DuplicateField,
                  lambda: cr.parse(dup))

    expect_raises("encoder rejects unknown id", cr.UnknownFieldId,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                                     (0x7F, cr.F_VALID, 0)]))
    expect_raises("encoder rejects duplicate id", cr.DuplicateField,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                                     (cr.F_CURRENT_EL, cr.F_VALID, 0x4)]))


def test_encoder_rejections():
    expect_raises("encoder rejects missing CurrentEL", cr.MissingCurrentEl,
                  lambda: cr.encode([(cr.F_MIDR_EL1, cr.F_VALID, 1)]))
    expect_raises("encoder rejects non-VALID CurrentEL", cr.MissingCurrentEl,
                  lambda: cr.encode([(cr.F_CURRENT_EL,
                                      cr.F_UNAVAILABLE_NOT_CAPTURED, 0)]))
    expect_raises("encoder rejects reserved flag bits", cr.BadFlags,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID | 0x40, 0x4)]))

    # The known-id set (9) is smaller than the format bound (16), so a fully
    # populated valid record fits; this documents that MAX_FIELDS is an upper
    # bound and the encoder's TooManyFields branch is exercised via the parser.
    all_fields = [(cr.F_CURRENT_EL, cr.F_VALID, 0x4)]
    for fid in sorted(cr.KNOWN_FIELD_IDS):
        if fid == cr.F_CURRENT_EL:
            continue
        all_fields.append((fid, cr.F_UNAVAILABLE_NOT_CAPTURED, 0))
    full = cr.encode(all_fields, capture_seq=1)
    report("record with every known field round-trips",
           cr.parse(full)["field_count"] == len(cr.KNOWN_FIELD_IDS))
    report("known-id set fits within MAX_FIELDS",
           len(cr.KNOWN_FIELD_IDS) <= cr.MAX_FIELDS)

    # > MAX_FIELDS but all ids unknown would hit UnknownFieldId first; build a
    # valid-id set larger than the cap is impossible (9 ids), so this checks the
    # cap via unknown ids too.
    expect_raises("encoder rejects malformed field tuple", cr.InvalidField,
                  lambda: cr.encode([(cr.F_CURRENT_EL, cr.F_VALID)]))


def test_deterministic():
    a = cr.encode(fixture_fields(), capture_seq=FIXTURE_SEQ)
    b = cr.encode(fixture_fields(), capture_seq=FIXTURE_SEQ)
    report("repeated encode is byte-identical", a == b)

    # Field order must not matter: sorted by id.
    reordered = list(reversed(fixture_fields()))
    c = cr.encode(reordered, capture_seq=FIXTURE_SEQ)
    report("field order does not change bytes", c == a)


def test_advisory():
    # CurrentEL = EL2 (0x8) with an EL1-gated field marked VALID.
    blob = cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x8),
                      (cr.F_MIDR_EL1, cr.F_VALID, 0x00000000AABBCCDD)])
    issues = cr.advisory_issues(cr.parse(blob))
    report("advisory flags EL1-gated field at EL2", len(issues) == 1)

    blob2 = cr.encode([(cr.F_CURRENT_EL, cr.F_VALID, 0x4),
                       (cr.F_MIDR_EL1, cr.F_VALID, 0x00000000AABBCCDD)])
    report("advisory silent at EL1", cr.advisory_issues(cr.parse(blob2)) == [])


def test_magic_constant():
    report("MAGIC is 8 bytes", len(cr.MAGIC) == 8)
    report("MAGIC_LE matches documented value",
           cr.MAGIC_LE == 0x43453841534F594D)


# --------------------------------------------------------------------------

def main():
    tests = [
        test_standard_crc_vector,
        test_expected_bytes_independent,
        test_round_trip,
        test_bad_magic,
        test_versions,
        test_lengths,
        test_oversized_and_trailing,
        test_crc,
        test_invalid_flags,
        test_zero_vs_unavailable,
        test_integer_boundaries,
        test_counts_and_overflow,
        test_unknown_and_duplicate_fields,
        test_encoder_rejections,
        test_deterministic,
        test_advisory,
        test_magic_constant,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            report(f"{t.__name__} raised", False, repr(e))

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
