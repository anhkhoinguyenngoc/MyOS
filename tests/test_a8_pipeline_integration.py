#!/usr/bin/env python3
"""M7.20/M7.29 — Focused tests for the A8 diagnostic pipeline integration.

Covers, with real assertions (this file previously contained imports only and
was reported PASS by host-test-all without exercising anything):

  * capture envelope schema validation (every rejection path),
  * envelope JSON round-trip preserving the RAW record bytes (regression for
    the from_json bug that silently dropped record_bytes while validate()
    still passed),
  * decoder <-> envelope status consistency,
  * decode_envelope helper functions (hash check, evidence reading),
  * the decode_envelope CLI end-to-end on TEMP FILES ONLY (exit codes,
    machine-readable --brief output, clean failure on missing image even with
    --skip-sha-check).

Everything here is OFFLINE and HOST-ONLY. No build artifact, protected file,
or device is required: all inputs are generated in temporary directories, so
this suite also runs on a bare checkout. QEMU evidence used by the CLI tests
is SYNTHETIC test data, clearly labeled, and never presented as a capture.
"""

import json
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "tools", "host"))

from tools.host.a8_exc_record_decoder import (
    decode,
    classify_status,
    MAGIC,
    EXC_SIZE,
    RecordStatus,
)
from tools.host.capture_envelope import (
    CaptureEnvelope,
    ENVELOPE_VERSION,
    make_from_record_bytes,
    make_synthetic_fixture,
)
from tools.host.decode_envelope import (
    prod_hash_matches_given,
    read_record_from_evidence,
    decode_record,
    record_hex,
)

DECODER_CLI = os.path.join(REPO, "tools", "host", "decode_envelope.py")


# --- helpers ---

def make_record_bytes(slot=3, esr=0xF200CAFE, elr=0x41000020, marker=1):
    """Synthetic 72-byte MYOSA8E1 record (test data, not a capture)."""
    buf = bytearray(EXC_SIZE)
    buf[0:8] = MAGIC
    buf[8:12] = struct.pack("<I", 1)
    buf[12:16] = struct.pack("<I", EXC_SIZE)
    buf[0x10:0x18] = struct.pack("<Q", slot)
    buf[0x18:0x20] = struct.pack("<Q", esr)
    buf[0x20:0x28] = struct.pack("<Q", elr)
    buf[0x40:0x48] = struct.pack("<Q", marker)
    return bytes(buf)


def run_cli(args, timeout=30):
    """Run the decode_envelope CLI; return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, DECODER_CLI] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO,
    )
    return proc.returncode, proc.stdout, proc.stderr


# --- capture envelope schema validation ---

def test_envelope_roundtrip_preserves_record_bytes():
    """JSON export -> import must keep the raw record bytes (M7.29 regression:
    from_json popped record_bytes_hex before reconstruction, silently
    discarding the evidence while validate() still passed)."""
    rec = make_record_bytes(slot=5, esr=0x1111, elr=0x2222, marker=1)
    env = make_from_record_bytes(
        record_bytes=rec,
        source_image_sha256="ab" * 32,
        source_image_size=20552,
        image_base=0,
        address_space="qemu",
        decoder_result={"status": "VALID", "slot": 5, "esr": 0x1111,
                        "elr": 0x2222, "marker": 1},
    )
    restored = CaptureEnvelope.from_json(env.to_json())
    assert restored.record_bytes == rec, (
        "record bytes lost in JSON round-trip: "
        f"{len(restored.record_bytes)} != {len(rec)}"
    )
    assert restored.decoder_slot == 5
    assert restored.decoder_esr == 0x1111
    assert restored.decoder_elr == 0x2222
    assert restored.decoder_marker == 1
    assert restored.source_image_sha256 == "ab" * 32
    assert restored.address_space == "qemu"
    valid, errors = restored.validate()
    assert valid, f"restored envelope should validate: {errors}"
    print("PASS: test_envelope_roundtrip_preserves_record_bytes")


def test_envelope_validate_rejects_missing_record_bytes():
    """An evidence envelope carrying no raw record must NOT validate."""
    env = make_synthetic_fixture()
    env.record_bytes = b""
    valid, errors = env.validate()
    assert not valid, "empty record_bytes must fail validation"
    assert "record_bytes" in errors
    print("PASS: test_envelope_validate_rejects_missing_record_bytes")


def test_envelope_validate_rejects_wrong_record_length():
    env = make_synthetic_fixture()
    env.record_bytes = b"\x00" * 71
    valid, errors = env.validate()
    assert not valid, "71-byte record must fail validation"
    print("PASS: test_envelope_validate_rejects_wrong_record_length")


def test_envelope_validate_rejects_wrong_record_offset():
    env = make_synthetic_fixture()
    env.record_offset = 0x4000
    valid, errors = env.validate()
    assert not valid and "record_offset" in errors
    print("PASS: test_envelope_validate_rejects_wrong_record_offset")


def test_envelope_validate_rejects_wrong_record_size_field():
    """Isolate the DEDICATED `record_length != 0x48` schema check.

    The record bytes are made the same (wrong) length as record_length so the
    cross-check (`record_bytes length != record_length`) cannot fire: the only
    possible validation failure is the dedicated constant check. Asserting its
    exact diagnostic means this test fails if that check is removed or
    weakened, which a loose "record_length in errors" match would not
    (the cross-check message also contains "record_length").
    """
    env = make_synthetic_fixture()
    env.record_length = 0x40          # 64, wrong: schema constant is 0x48
    env.record_bytes = b"\x00" * 0x40  # matches the wrong record_length exactly
    valid, errors = env.validate()
    assert not valid, "wrong record_length must fail validation"
    assert "record_length 64 != expected 0x48" in errors, (
        f"expected the dedicated record_length diagnostic, got: {errors}"
    )
    print("PASS: test_envelope_validate_rejects_wrong_record_size_field")


def test_envelope_validate_rejects_bad_decoder_status():
    env = make_synthetic_fixture()
    env.decoder_status = "CORRUPTED"
    valid, errors = env.validate()
    assert not valid and "decoder_status" in errors
    print("PASS: test_envelope_validate_rejects_bad_decoder_status")


def test_envelope_validate_rejects_nonpositive_source_size():
    env = make_synthetic_fixture()
    env.source_image_size = 0
    valid, errors = env.validate()
    assert not valid and "source_image_size" in errors
    print("PASS: test_envelope_validate_rejects_nonpositive_source_size")


def test_envelope_validate_rejects_bad_sha_length():
    env = make_synthetic_fixture()
    env.source_image_sha256 = "not-a-sha"
    valid, errors = env.validate()
    assert not valid and "sha256" in errors.lower()
    print("PASS: test_envelope_validate_rejects_bad_sha_length")


def test_envelope_validate_rejects_wrong_format_version():
    env = make_synthetic_fixture()
    env.format_version = "99.0.0"
    valid, errors = env.validate()
    assert not valid and "format_version" in errors
    print("PASS: test_envelope_validate_rejects_wrong_format_version")


def test_envelope_to_json_excludes_none_and_schema_hash():
    """Schema stability: null fields and the internal schema hash are not
    serialized, and record bytes travel as hex."""
    env = make_synthetic_fixture()
    data = json.loads(env.to_json())
    assert "schema_hash" not in data
    assert "qemu_version" not in data          # None -> omitted
    assert "record_bytes_hex" in data
    assert "record_bytes" not in data
    assert len(data["record_bytes_hex"]) == EXC_SIZE * 2
    assert env.format_version == ENVELOPE_VERSION
    print("PASS: test_envelope_to_json_excludes_none_and_schema_hash")


def test_envelope_synthetic_fixture_classification():
    """Synthetic fixtures are labeled by marker: 1 -> VALID, 0 -> INCOMPLETE."""
    v = make_synthetic_fixture(slot=1, esr=1, elr=2, marker=1)
    i = make_synthetic_fixture(slot=1, esr=1, elr=2, marker=0)
    assert v.decoder_status == "VALID"
    assert i.decoder_status == "INCOMPLETE"
    for env in (v, i):
        valid, errors = env.validate()
        assert valid, errors
    print("PASS: test_envelope_synthetic_fixture_classification")


def test_envelope_synthetic_fixture_is_labeled_in_json():
    """A synthetic fixture must carry a machine-readable synthetic label that
    survives JSON export, so exported test data can never be mistaken for a
    real capture downstream."""
    env = make_synthetic_fixture(marker=1)
    data = json.loads(env.to_json())
    assert data.get("interpretation", {}).get("synthetic_fixture") is True, (
        "synthetic fixture lost its provenance label in JSON export"
    )
    restored = CaptureEnvelope.from_json(env.to_json())
    assert restored.interpretation == {"synthetic_fixture": True}
    print("PASS: test_envelope_synthetic_fixture_is_labeled_in_json")


def test_envelope_field_count_reflects_decoded_fields():
    """Regression: field_count was hardcoded to 2 while 4 fields were
    populated; it must equal the number of non-None decoded values."""
    env = make_synthetic_fixture(slot=0, esr=0x1234, elr=0x5678, marker=1)
    assert env.decoder_field_count == 4, env.decoder_field_count
    env0 = make_synthetic_fixture()  # all defaults zero -> still decoded
    assert env0.decoder_field_count == 4, env0.decoder_field_count
    print("PASS: test_envelope_field_count_reflects_decoded_fields")


# --- decoder <-> envelope consistency ---

def test_decoder_and_envelope_status_consistent():
    """The status the decoder assigns must be the status the envelope carries,
    for ALL FOUR RecordStatus classifications, each produced by the real
    decoder from self-contained bytes (no fabricated statuses):

      VALID       marker=1, full 72 bytes -> envelope schema-valid
      INCOMPLETE  marker=0, full 72 bytes -> envelope schema-valid
      TRUNCATED   40 bytes                 -> envelope carries the status but
                                              schema validation must REJECT the
                                              short record (fail-closed)
      MALFORMED   72 bytes, corrupted magic-> envelope carries the status;
                                              schema stays valid because the
                                              bytes are structurally complete
                                              (status, not schema, flags it)
    """
    bad_magic = bytearray(make_record_bytes())
    bad_magic[0:8] = b"XXXXXXXX"  # != b"MYOSA8E1"
    cases = [
        (make_record_bytes(marker=1), "VALID", True),
        (make_record_bytes(marker=0), "INCOMPLETE", True),
        (make_record_bytes()[:40], "TRUNCATED", False),
        (bytes(bad_magic), "MALFORMED", True),
    ]
    for raw, expected, expect_valid in cases:
        rec, err = decode(raw)
        assert err is None and rec is not None, (
            f"{expected}: decoder hard-failed: {err}"
        )
        assert rec.status.name == expected, (
            f"decoder status {rec.status.name}, expected {expected}"
        )
        assert expected in classify_status(rec), (
            f"classify_status mismatch for {expected}"
        )
        env = make_from_record_bytes(
            record_bytes=raw,
            source_image_sha256="cd" * 32,
            source_image_size=20552,
            image_base=0,
            decoder_result={"status": rec.status.name, "slot": rec.slot,
                            "esr": rec.esr, "elr": rec.elr,
                            "marker": rec.valid_marker},
        )
        assert env.decoder_status == expected, (
            f"envelope dropped {expected}: got {env.decoder_status}"
        )
        valid, errors = env.validate()
        assert valid == expect_valid, (
            f"{expected}: expected valid={expect_valid}, got {valid}: {errors}"
        )
    print("PASS: test_decoder_and_envelope_status_consistent")


def test_decoder_truncated_input_rejected_before_envelope():
    """A truncated record must be classified TRUNCATED by the decoder and
    must never reach envelope construction through the pipeline helper."""
    raw = make_record_bytes()[:40]
    rec, err = decode(raw)
    assert err is None and rec is not None
    assert rec.status == RecordStatus.TRUNCATED, rec.status
    # And an envelope built from those bytes must fail schema validation.
    env = make_from_record_bytes(
        record_bytes=raw,
        source_image_sha256="ef" * 32,
        source_image_size=20552,
        image_base=0,
    )
    valid, errors = env.validate()
    assert not valid and "record_bytes" in errors
    print("PASS: test_decoder_truncated_input_rejected_before_envelope")


# --- decode_envelope helpers ---

def test_prod_hash_matches_given():
    with tempfile.TemporaryDirectory() as td:
        img = os.path.join(td, "image.bin")
        payload = b"MYOS-TEST-IMAGE" * 16
        with open(img, "wb") as fh:
            fh.write(payload)
        good = hashlib.sha256(payload).hexdigest()
        assert prod_hash_matches_given(img, good) is True
        assert prod_hash_matches_given(img, "0" * 64) is False
        assert prod_hash_matches_given(os.path.join(td, "missing"), good) is False
    print("PASS: test_prod_hash_matches_given")


def test_read_record_from_evidence():
    with tempfile.TemporaryDirectory() as td:
        rec = make_record_bytes()
        with open(os.path.join(td, "record.bin"), "wb") as fh:
            fh.write(rec)
        got = read_record_from_evidence(td)
        assert got == rec, "exact 72-byte record must be returned unchanged"

        with open(os.path.join(td, "short.bin"), "wb") as fh:
            fh.write(rec[:71])
        assert read_record_from_evidence(td, "short.bin") is None, (
            "truncated record must be rejected, not passed through"
        )

        # Oversized record: the rejection must be `!= 72`, not `< 72`.
        # A regression to a one-sided length check would accept this 73-byte
        # record while the 71-byte case above still passed.
        with open(os.path.join(td, "long.bin"), "wb") as fh:
            fh.write(rec + b"\x00")
        assert read_record_from_evidence(td, "long.bin") is None, (
            "oversized (73-byte) record must be rejected, not passed through"
        )

        assert read_record_from_evidence(os.path.join(td, "nope")) is None
    print("PASS: test_read_record_from_evidence")


def test_record_hex_handles_none():
    assert record_hex(0xF200CAFE, None) == ("0xf200cafe", None)
    assert record_hex(None, None) == (None, None)
    print("PASS: test_record_hex_handles_none")


def test_decode_record_uses_independent_decoder():
    rec = make_record_bytes(slot=7, esr=0xAAAA, elr=0xBBBB, marker=1)
    out, err = decode_record(rec)
    assert err is None and out is not None
    assert out.slot == 7 and out.esr == 0xAAAA and out.elr == 0xBBBB
    assert out.valid_marker == 1
    print("PASS: test_decode_record_uses_independent_decoder")


# --- decode_envelope CLI end-to-end (TEMP files only) ---

def _temp_pipeline_inputs(td, marker=1):
    """Create a temp production image + temp synthetic QEMU evidence dir."""
    img = os.path.join(td, "raw_entry.bin")
    image_bytes = os.urandom(4096)
    with open(img, "wb") as fh:
        fh.write(image_bytes)
    sha = hashlib.sha256(image_bytes).hexdigest()
    ev = os.path.join(td, "evidence")
    os.makedirs(ev)
    with open(os.path.join(ev, "record.bin"), "wb") as fh:
        fh.write(make_record_bytes(slot=3, esr=0xF200CAFE,
                                   elr=0x41000020, marker=marker))
    return img, sha, ev


def test_cli_brief_output_is_single_json_line():
    with tempfile.TemporaryDirectory() as td:
        img, sha, ev = _temp_pipeline_inputs(td)
        rc, out, errout = run_cli(
            ["--brief", "--image", img, "--sha", sha, "--evidence-dir", ev]
        )
        assert rc == 0, f"CLI failed rc={rc}\nstdout={out}\nstderr={errout}"
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 1, (
            f"--brief stdout must be exactly one JSON line, got {len(lines)}: {out!r}"
        )
        data = json.loads(lines[0])
        assert data["status"] == "VALID"
        assert data["slot"] == 3
        # The envelope schema carries raw integer register values (the same
        # values stored in the record); hex formatting is presentation-only.
        assert data["esr"] == 0xF200CAFE
        assert data["elr"] == 0x41000020
        assert data["marker"] == 1
        assert data["sha256"] == sha
        assert data["envelope_valid"] is True
    print("PASS: test_cli_brief_output_is_single_json_line")


def test_cli_missing_image_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        _, sha, ev = _temp_pipeline_inputs(td)
        rc, out, errout = run_cli(
            ["--brief", "--image", os.path.join(td, "absent.bin"),
             "--sha", sha, "--evidence-dir", ev]
        )
        assert rc == 1, f"missing image must fail closed, got rc={rc}"
        assert out.strip() == "", f"--brief must emit no stdout on failure: {out!r}"
        assert "ERROR" in errout
    print("PASS: test_cli_missing_image_fails_closed")


def test_cli_wrong_sha_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        img, _, ev = _temp_pipeline_inputs(td)
        rc, out, errout = run_cli(
            ["--brief", "--image", img, "--sha", "0" * 64, "--evidence-dir", ev]
        )
        assert rc == 1, f"sha mismatch must fail closed, got rc={rc}"
        assert "mismatch" in errout.lower()
    print("PASS: test_cli_wrong_sha_fails_closed")


def test_cli_truncated_evidence_fails_closed():
    """Truncated evidence must fail through the EVIDENCE-READ path specifically:
    the helper rejects the size (WARNING with the actual vs expected size) and
    the pipeline reports 'Failed to read exception record' — not some unrelated
    failure — with rc=1 and a clean (empty) --brief stdout.
    """
    with tempfile.TemporaryDirectory() as td:
        img, sha, ev = _temp_pipeline_inputs(td)
        with open(os.path.join(ev, "record.bin"), "wb") as fh:
            fh.write(make_record_bytes()[:40])
        rc, out, errout = run_cli(
            ["--brief", "--image", img, "--sha", sha, "--evidence-dir", ev]
        )
        assert rc == 1, f"truncated evidence must fail closed, got rc={rc}"
        assert out.strip() == "", (
            f"--brief must emit no stdout on this failure path: {out!r}"
        )
        assert "size=40 != expected 72" in errout, (
            f"expected the size-rejection warning identifying this path, got:\n{errout}"
        )
        assert "Failed to read exception record" in errout, (
            f"expected the evidence-read error identifying this path, got:\n{errout}"
        )
    print("PASS: test_cli_truncated_evidence_fails_closed")


def test_cli_skip_sha_with_missing_image_clean_error():
    """Regression: --skip-sha-check with a missing image used to raise an
    unhandled FileNotFoundError traceback (os.path.getsize before the
    isfile guard). It must now fail with a clean ERROR and rc=1."""
    with tempfile.TemporaryDirectory() as td:
        _, _, ev = _temp_pipeline_inputs(td)
        rc, out, errout = run_cli(
            ["--brief", "--skip-sha-check",
             "--image", os.path.join(td, "absent.bin"), "--evidence-dir", ev]
        )
        assert rc == 1, f"must fail closed, got rc={rc}"
        assert "Traceback" not in errout, f"unhandled traceback:\n{errout}"
        assert "ERROR" in errout
    print("PASS: test_cli_skip_sha_with_missing_image_clean_error")


def test_cli_incomplete_marker_reported_not_valid():
    """marker=0 evidence must be classified INCOMPLETE end-to-end, never
    dressed up as VALID."""
    with tempfile.TemporaryDirectory() as td:
        img, sha, ev = _temp_pipeline_inputs(td, marker=0)
        rc, out, errout = run_cli(
            ["--brief", "--image", img, "--sha", sha, "--evidence-dir", ev]
        )
        assert rc == 0, f"CLI failed rc={rc}\nstderr={errout}"
        data = json.loads([ln for ln in out.splitlines() if ln.strip()][0])
        assert "INCOMPLETE" in data["status"], data["status"]
        assert data["marker"] == 0
    print("PASS: test_cli_incomplete_marker_reported_not_valid")


# --- Main ---

def main():
    tests = [
        test_envelope_roundtrip_preserves_record_bytes,
        test_envelope_validate_rejects_missing_record_bytes,
        test_envelope_validate_rejects_wrong_record_length,
        test_envelope_validate_rejects_wrong_record_offset,
        test_envelope_validate_rejects_wrong_record_size_field,
        test_envelope_validate_rejects_bad_decoder_status,
        test_envelope_validate_rejects_nonpositive_source_size,
        test_envelope_validate_rejects_bad_sha_length,
        test_envelope_validate_rejects_wrong_format_version,
        test_envelope_to_json_excludes_none_and_schema_hash,
        test_envelope_synthetic_fixture_classification,
        test_envelope_synthetic_fixture_is_labeled_in_json,
        test_envelope_field_count_reflects_decoded_fields,
        test_decoder_and_envelope_status_consistent,
        test_decoder_truncated_input_rejected_before_envelope,
        test_prod_hash_matches_given,
        test_read_record_from_evidence,
        test_record_hex_handles_none,
        test_decode_record_uses_independent_decoder,
        test_cli_brief_output_is_single_json_line,
        test_cli_missing_image_fails_closed,
        test_cli_wrong_sha_fails_closed,
        test_cli_truncated_evidence_fails_closed,
        test_cli_skip_sha_with_missing_image_clean_error,
        test_cli_incomplete_marker_reported_not_valid,
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

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        sys.exit(1)
    print("All tests passed!")


if __name__ == "__main__":
    main()
