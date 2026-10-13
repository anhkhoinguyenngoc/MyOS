#!/usr/bin/env python3
"""
MyOS M-P2.11 -- Pre-Handoff Self-Report (PHSR), design B.

Generate and validate the MP1PRB01 probe report BEFORE `bootr`, so that
nothing in the readback path ever depends on USB servicing after the raw
jump (the Design-A dependency this design exists to remove).

WHY DESIGN B IS NECESSARY (traced, not assumed)
------------------------------------------------
Actual `bootr` execution order, pinned to the checked-out PongoOS tree:

  1. shell alive, EP0 + stdout ring working          <-- ONLY live window
     (main.c:257 `bootr` -> pongo_boot_raw)
  2. gBootFlag = BOOT_FLAG_RAW; task_yield()          (main.c pongo_boot_raw)
  3. scheduler loop exits                             (entry.c:210 `while`)
  4. timer_disable(); usb_teardown();                 (entry.c:231-232)
     disable_interrupts(); preemption_over = 1;       (entry.c:233-234)
  5. serial_teardown(); interrupt_teardown();          (entry.c:272,276)
  6. set_exception_stack_core0(); lowlevel_cleanup(); (entry.c:306,308)
     -> _disable_mmu_el1 clears SCTLR.M and SCTLR.C   (lowlevel.c:117-128)
  7. jump_to_image_extended(bufPA, gBootArgs, gEntryPoint)
     -> mov x30,x0; mov x0,x1; mov x8,x2; ...; ret    (jump_to_image.S:42-56)
  8. probe entry writes the report into the upload buffer, then parks

Step 8 (the probe) is strictly AFTER step 1 (the only step with a working
USB channel).  Therefore any byte the *probe* observes can never be read
back over USB -- that is a hard ordering fact, not a tuning problem.

WHAT DESIGN B CAN HONESTLY DO
-----------------------------
Pre-handoff, the host can still read the report block out of the upload
buffer in device RAM through the live shell (the M-P2.8 channel:
`md8` -> stdout ring -> ep0 IN).  What comes back is:

  * CAPTURED -- the real 0x60 bytes sitting in device memory, so the
    header (magic/version/size) is verified against actual device RAM
    rather than against the local file, and
  * CAPTURED -- all nine runtime slots reading ZERO, which is the
    positive, checkable proof that the probe has not run yet and the
    report region is pristine (a non-zero slot here means the buffer was
    reused or the bytes were altered).

Plus, from the pinned source alone:

  * SOURCE_DERIVED -- `current_el` == 0x4 (EL1): PongoOS executes
    `msr sctlr_el1` / `msr vbar_el1` in lowlevel.c without trapping, so
    it cannot have been running below EL1 at step 6.
  * SOURCE_DERIVED -- `reserved_zero` == 0 (format rule).

WHAT IS BLOCKED, EXACTLY
-------------------------
The eight remaining runtime slots stay UNKNOWN before the jump.  The
pinned PongoOS shell exposes no primitive that could produce them:

  * no command reads a CPU system register (command table is the pinned
    set in main.c:254-276, linux.c:86-88, lowlevel.c:548-549,
    command.c:179 -- none of them executes an `mrs`);
  * `md8`/`peek`/`physdump` all require the caller to SUPPLY an address,
    and the three address-valued slots (x30/x0/x8) come from runtime
    globals whose VAs are KASLR-slid: `loader_xfer_recv_data`,
    `gBootArgs`, `gEntryPoint` (entry.c:311).  The host cannot resolve
    the slide (M-P2.4), and no shell command resolves a symbol either;
  * `MIDR_EL1` and `CNTFRQ_EL0` are never read anywhere in the pinned
    tree (absence scan: zero hits);
  * `CNTPCT_EL0` IS readable via the existing `_get_ticks()`, but a
    pre-handoff sample is a *different point in time* from the jump-time
    sample the probe records -- substituting it would be a fabricated
    value, so it is classified UNKNOWN, not CAPTURED.

This module therefore reports provenance per field and REFUSES to
certify a pre-handoff report as post-jump register evidence.

EVIDENCE BOUNDARY
-----------------
Offline tests here prove layout, validation, provenance bookkeeping and
the host parse path.  They do NOT touch USB, DFU, a device, or a boot.
Nothing produced by this module is hardware evidence.

Usage:
  python3 tools/host/probe_selfreport.py --selfcheck   # offline
  python3 tools/host/probe_selfreport.py --bin PATH    # parse built probe
Exit 0 = ok, non-zero = failure.
"""

import argparse
import json
import os
import struct
import sys

_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import probe_report  # noqa: E402

# ---------------------------------------------------------------------------
# Provenance classes -- how each field's value came to be known.
# ---------------------------------------------------------------------------

CAPTURED = "captured"                # read out of device RAM over the
                                      # live shell (real bytes)
SOURCE_DERIVED = "source_derived"    # fixed by the pinned PongoOS source
UNKNOWN = "unknown"                  # no pre-handoff primitive exists

PROVENANCE_CLASSES = (CAPTURED, SOURCE_DERIVED, UNKNOWN)

# Per-field default provenance BEFORE any device read, with the exact
# evidence (or the exact reason it cannot be had).  Keys are the
# probe_report.DYNAMIC_FIELDS names, so the layout stays single-sourced.
EXPECTED_PROVENANCE = {
    "x30_entry": UNKNOWN,
    "x0_entry": UNKNOWN,
    "x8_entry": UNKNOWN,
    "current_el": SOURCE_DERIVED,
    "sctlr_el1": UNKNOWN,
    "midr_el1": UNKNOWN,
    "cntpct_el0_1": UNKNOWN,
    "cntpct_el0_2": UNKNOWN,
    "cntfrq_el0": UNKNOWN,
    "reserved_zero": SOURCE_DERIVED,
}

EVIDENCE = {
    "x30_entry":
        "BLOCKED: runtime VA of loader_xfer_recv_data (entry.c:311); "
        "KASLR slide is device-side unknown (M-P2.4) and no shell "
        "command resolves a symbol.",
    "x0_entry":
        "BLOCKED: runtime value of gBootArgs (entry.c:311); same slide "
        "problem, and `md8` needs the address supplied by the caller.",
    "x8_entry":
        "BLOCKED: runtime value of gEntryPoint (entry.c:311); same "
        "slide problem, and `md8` needs the address supplied by the "
        "caller.",
    "current_el":
        "SOURCE_DERIVED: 0x4 (EL1). lowlevel.c executes `msr sctlr_el1` "
        "and `msr vbar_el1` unconditionally before the jump, so execution "
        "cannot have been below EL1.",
    "sctlr_el1":
        "BLOCKED for the full u64: _disable_mmu_el1 (lowlevel.c:117-128) "
        "proves bits [2:0] are M=0,C=0, but the remaining bits are not "
        "host-observable, so the whole value cannot be claimed.",
    "midr_el1":
        "BLOCKED: MIDR_EL1 is never read anywhere in the pinned tree "
        "(absence scan), so there is no C/asm primitive to call.",
    "cntpct_el0_1":
        "BLOCKED: _get_ticks() (lowlevel.c:130-134) can sample CNTPCT "
        "pre-handoff, but that is an EARLIER instant than the probe's "
        "jump-time sample -- recording it as the probe's value would be "
        "a fabricated datum.",
    "cntpct_el0_2":
        "BLOCKED: same as cntpct_el0_1, plus the probe's second sample "
        "is taken after its own delay loop, which has not run yet.",
    "cntfrq_el0":
        "BLOCKED: CNTFRQ_EL0 is never accessed anywhere in the pinned "
        "PongoOS tree (absence scan: zero hits).",
    "reserved_zero":
        "SOURCE_DERIVED: format rule -- the u64 at +0x58 must be 0.",
}


class PreHandoffError(ValueError):
    """A pre-handoff report failed validation."""


def _validate_exact(blob):
    """Length + magic + version + size, on an exact 0x60 block."""
    if blob is None:
        raise PreHandoffError("no blob")
    if len(blob) != probe_report.RPT_SIZE:
        raise PreHandoffError(
            "report must be exactly 0x%x bytes, got 0x%x"
            % (probe_report.RPT_SIZE, len(blob)))
    try:
        return probe_report.parse_report(blob)
    except ValueError as exc:
        raise PreHandoffError(str(exc))


def validate_prehandoff(blob):
    """Validate a 0x60 MP1PRB01 block read from device RAM BEFORE `bootr`.

    On top of the format checks (exact size, magic, version, report_size)
    this enforces the two pre-handoff invariants that make the read
    meaningful:

      1. `reserved_zero` == 0                  (format rule)
      2. EVERY runtime slot == 0               (the probe has not run
         yet -- step 8 of the trace happens after `bootr`; a non-zero
         slot here means the upload buffer was reused or the bytes were
         altered, so the read cannot be trusted)

    Returns the parsed report dict.  Raises PreHandoffError otherwise.
    """
    report = _validate_exact(blob)
    reserved = report["reserved_zero"]
    if reserved != 0:
        raise PreHandoffError(
            "reserved field at 0x%x must be zero, got 0x%x"
            % (probe_report.EQU_MIRROR["RPT_ZERO_OFF"], reserved))
    dirty = [name for name, _ in probe_report.DYNAMIC_FIELDS
             if report[name] != 0]
    if dirty:
        raise PreHandoffError(
            "runtime slot(s) %s non-zero BEFORE `bootr`: the probe cannot "
            "have run yet (it executes after the raw jump), so these "
            "bytes were written by something else and are not trustworthy"
            % ", ".join(dirty))
    return report


def _headered_blob():
    """A 0x60 block with only the static header filled in."""
    blob = bytearray(probe_report.RPT_SIZE)
    blob[0:8] = probe_report.MAGIC_TXT
    struct.pack_into("<I", blob, probe_report.EQU_MIRROR["RPT_VER_OFF"],
                     probe_report.RPT_VERSION)
    struct.pack_into("<I", blob, probe_report.EQU_MIRROR["RPT_SIZE_OFF"],
                     probe_report.RPT_SIZE)
    return blob


def pristine_upload_blob():
    """Exactly what the upload buffer holds for the report block BEFORE
    `bootr`: the static header that pongo_probe.S pre-initializes into the
    file (commit f7f89d5) with every runtime slot still zero.

    This is the shape a live-shell `md8` read returns pre-handoff, and so
    the input validate_prehandoff() expects.  Distinct from
    build_expected_report(), which is the HOST's source-derived
    expectation (it carries current_el = 0x4, which device RAM does not --
    device RAM only holds what was uploaded).
    """
    return bytes(_headered_blob())


def expected_field_values():
    """The value each slot would hold in the host's pre-handoff
    expectation.  UNKNOWN slots are 0 by construction -- a placeholder,
    never a guess."""
    return {name: (0x4 if name == "current_el" else 0)
            for name, _ in probe_report.DYNAMIC_FIELDS}


def build_expected_report():
    """Assemble the host-side pre-handoff report blob from source-derived
    constants alone.  Returns (blob, provenance_map).

    The blob is a REAL, well-formed MP1PRB01 block, but only two fields
    carry meaning beyond "not yet written"; see EXPECTED_PROVENANCE.
    """
    values = expected_field_values()
    blob = _headered_blob()
    for name, off in probe_report.DYNAMIC_FIELDS:
        struct.pack_into("<Q", blob, off, values[name])
    return bytes(blob), dict(EXPECTED_PROVENANCE)


class Certificate(object):
    """Machine-checkable epistemic status of a pre-handoff report.

    The four buckets are deliberately independent:

      captured         slots whose PRE-HANDOFF bytes were read out of
                       device RAM over the live shell.  Reading zero here
                       means "not yet written", NOT "the probe saw zero".
      source_derived   slots whose value is fixed by the pinned PongoOS
                       source regardless of when it is read.
      unknown          slots whose POST-JUMP (probe-observed) value is
                       unknowable before `bootr` -- always the same 8,
                       capture or no capture.  This is the exact blocker.
      runtime_observed slots that hold a value the probe itself observed.
                       ALWAYS empty pre-handoff: the probe executes at
                       trace step 8, strictly after the raw jump.
    """

    VALID = "prehandoff_valid"
    UNCATTURED = "uncaptured"

    def __init__(self, status, captured=(), source_derived=(), unknown=(),
                 detail=""):
        self.status = status
        self.captured = tuple(captured)
        self.source_derived = tuple(source_derived)
        self.unknown = tuple(unknown)
        self.detail = detail

    @property
    def ok(self):
        return self.status == self.VALID

    @property
    def runtime_observed(self):
        """Empty always -- see the class docstring.  Pinned by test."""
        return ()

    @property
    def is_postjump_evidence(self):
        """Always False -- pre-handoff data can never be post-jump
        register evidence, by the ordering argument in the module doc."""
        return False

    def prehandoff_known(self, field):
        """Do we genuinely know this slot's PRE-HANDOFF value?"""
        return field in self.captured or field in self.source_derived

    def predictable(self, field):
        """Do we know what the probe WILL record for this slot?

        True only for the slots the pinned source pins to a constant
        (current_el, reserved_zero).  False for the 8 blocked slots --
        that is the honest answer, before and after any capture.
        """
        return field not in self.unknown

    def as_dict(self):
        return {
            "status": self.status,
            "captured": list(self.captured),
            "source_derived": list(self.source_derived),
            "unknown": list(self.unknown),
            "runtime_observed": list(self.runtime_observed),
            "is_postjump_evidence": self.is_postjump_evidence,
            "detail": self.detail,
        }


class PreHandoffReport(object):
    """Assemble + validate the report BEFORE handoff.

    Two independent inputs, both explicit:

      capture(blob)     real bytes read from device RAM over the live
                        shell -> validated, fields marked CAPTURED
      set(field, val,
          prov)         a source-derived value (SOURCE_DERIVED only;
                        CAPTURED/UNKNOWN are refused here so that a
                        derived constant can never be passed off as a
                        device read)

    build() emits the 0x60 blob; certificate() gives the honest verdict.
    """

    def __init__(self):
        self._values = {name: 0 for name, _ in probe_report.DYNAMIC_FIELDS}
        self._provenance = dict(EXPECTED_PROVENANCE)
        self._captured = None

    @property
    def captured(self):
        return self._captured

    def capture(self, blob):
        """Adopt a validated pre-handoff device read."""
        report = validate_prehandoff(blob)
        self._captured = report
        for name, _ in probe_report.DYNAMIC_FIELDS:
            self._values[name] = report[name]
            self._provenance[name] = CAPTURED
        return report

    def set(self, field, value, provenance):
        """Record a SOURCE_DERIVED value.

        CAPTURED and UNKNOWN are refused outright: the only way to mark
        a slot CAPTURED is capture(blob), which requires the real device
        bytes and validates them, so a host constant can never be
        laundered into a device read.  A SOURCE_DERIVED value must also
        agree with the probe's own contract (expected_field_values).
        """
        if field not in EXPECTED_PROVENANCE:
            raise PreHandoffError("unknown field %r" % (field,))
        if provenance not in PROVENANCE_CLASSES:
            raise PreHandoffError("unknown provenance %r" % (provenance,))
        if provenance == CAPTURED:
            raise PreHandoffError(
                "a value may only be CAPTURED via capture(blob); "
                "set() cannot turn a host constant into a device read")
        if provenance == UNKNOWN:
            raise PreHandoffError(
                "field %r has no pre-handoff primitive (%s); storing a "
                "value would be fabricated"
                % (field, EVIDENCE[field]))
        if EXPECTED_PROVENANCE[field] != SOURCE_DERIVED:
            raise PreHandoffError(
                "field %r is %r pre-handoff and cannot carry a "
                "source-derived value (%s)"
                % (field, EXPECTED_PROVENANCE[field], EVIDENCE[field]))
        if not isinstance(value, int) or isinstance(value, bool) \
                or value < 0 or value >= 1 << 64:
            raise PreHandoffError(
                "value for %r must be a u64, got %r" % (field, value))
        expected = expected_field_values()[field]
        if value != expected:
            raise PreHandoffError(
                "source-derived %r must be 0x%x (probe contract), got 0x%x"
                % (field, expected, value))
        self._values[field] = value
        self._provenance[field] = SOURCE_DERIVED
        return self

    def build(self):
        """Emit the exact 0x60 MP1PRB01 blob."""
        blob = _headered_blob()
        for name, off in probe_report.DYNAMIC_FIELDS:
            struct.pack_into("<Q", blob, off, self._values[name])
        return bytes(blob)

    def certificate(self):
        """Honest verdict over the current state.

        `source_derived` and `unknown` come from EXPECTED_PROVENANCE and
        do NOT change on capture: reading device RAM cannot make an
        unknowable post-jump value knowable (the probe has not run).
        `captured` changes from () to every slot, because every slot's
        pre-handoff bytes really were read.
        """
        derived = tuple(sorted(n for n, p in EXPECTED_PROVENANCE.items()
                               if p == SOURCE_DERIVED))
        unknown = tuple(sorted(n for n, p in EXPECTED_PROVENANCE.items()
                               if p == UNKNOWN))
        all_slots = tuple(sorted(n for n, _ in probe_report.DYNAMIC_FIELDS))
        if self._captured is None:
            return Certificate(
                Certificate.UNCATTURED, (), derived, unknown,
                "no device read performed: this report has never been "
                "seen on a device, only assembled on the host")
        return Certificate(
            Certificate.VALID, all_slots, derived, unknown,
            "0x%x bytes read from the upload buffer over the live shell "
            "before `bootr`; runtime slots verified zero (probe has not "
            "run); %d slot(s) still have no knowable post-jump value and "
            "are NOT claimed" % (probe_report.RPT_SIZE, len(unknown)))


def report_from_capture(blob):
    """Build a PreHandoffReport that has just performed `capture(blob)`
    (the shape returned by a live-shell read of the upload buffer)."""
    rep = PreHandoffReport()
    rep.capture(blob)
    return rep


# ---------------------------------------------------------------------------
# CLI -- offline only; nothing here talks to a device.
# ---------------------------------------------------------------------------

def _selfcheck():
    print("probe_selfreport: OFFLINE SELF-CHECK (NO DEVICE, NO USB)")
    rep = PreHandoffReport()
    blob, prov = build_expected_report()
    assert len(blob) == probe_report.RPT_SIZE
    parsed = probe_report.parse_report(blob)
    assert parsed["magic"] == "MP1PRB01"
    assert parsed["current_el"] == 0x4
    assert all(parsed[n] == 0 for n, _ in probe_report.DYNAMIC_FIELDS
               if n != "current_el")
    print("  [OFFLINE] expected report builds: 0x%x bytes, header valid"
          % len(blob))

    # An expected report with UNKNOWN slots is NOT capturable -> uncaptured.
    cert = rep.certificate()
    assert cert.status == Certificate.UNCATTURED
    assert cert.runtime_observed == () and not cert.is_postjump_evidence
    print("  [OFFLINE] uncertified before any device read: %s" % cert.status)

    # Capturing the PRISTINE upload block (what a live-shell `md8` of the
    # upload buffer returns before `bootr`) validates and certifies.
    pristine = pristine_upload_blob()
    assert pristine != blob, "pristine device block must not carry the host's"
    #                                            source-derived current_el
    captured = report_from_capture(pristine)
    cert = captured.certificate()
    assert cert.ok, cert.detail
    assert cert.captured == tuple(sorted(n for n, _ in
                                         probe_report.DYNAMIC_FIELDS))
    assert len(cert.unknown) == 8, cert.unknown   # the exact blocker
    assert cert.runtime_observed == ()
    assert not cert.is_postjump_evidence
    print("  [OFFLINE] pristine capture certified: %s "
          "(%d slots captured, %d post-jump values unknowable)"
          % (cert.status, len(cert.captured), len(cert.unknown)))

    # A dirty dynamic slot before the jump must be rejected.
    dirty = bytearray(pristine)
    struct.pack_into("<Q", dirty, probe_report.EQU_MIRROR["RPT_X30_OFF"],
                     0x1234)
    try:
        validate_prehandoff(bytes(dirty))
    except PreHandoffError as exc:
        print("  [OFFLINE] non-zero runtime slot before jump rejected: %s"
              % str(exc).split(":")[0])
    else:
        raise AssertionError("dirty pre-handoff slot must be rejected")

    # A non-zero reserved field must be rejected.
    bad = bytearray(pristine)
    struct.pack_into("<Q", bad, probe_report.EQU_MIRROR["RPT_ZERO_OFF"], 1)
    try:
        validate_prehandoff(bytes(bad))
    except PreHandoffError:
        print("  [OFFLINE] non-zero reserved field rejected")
    else:
        raise AssertionError("non-zero reserved must be rejected")

    # set() must refuse to launder a host constant as a device read.
    try:
        PreHandoffReport().set("midr_el1", 0x411fc075, CAPTURED)
    except PreHandoffError:
        print("  [OFFLINE] set() refuses CAPTURED provenance")
    else:
        raise AssertionError("CAPTURED must be refused by set()")

    # ... and must refuse a fabricated value for an UNKNOWN field.
    try:
        PreHandoffReport().set("cntfrq_el0", 24000000, UNKNOWN)
    except PreHandoffError:
        print("  [OFFLINE] set() refuses a value for an UNKNOWN field")
    else:
        raise AssertionError("UNKNOWN provenance must be refused")

    # ... and must refuse to relabel an UNKNOWN slot as source-derived.
    try:
        PreHandoffReport().set("cntfrq_el0", 0, SOURCE_DERIVED)
    except PreHandoffError:
        print("  [OFFLINE] set() refuses relabelling an UNKNOWN slot")
    else:
        raise AssertionError("relabel UNKNOWN as SOURCE_DERIVED must fail")

    print("probe_selfreport: OFFLINE SELF-CHECK PASS")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--selfcheck":
        return _selfcheck()
    ap = argparse.ArgumentParser(
        description="Pre-handoff MP1PRB01 self-report (offline host tool).")
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the offline self-check")
    ap.add_argument("--bin", help="built probe image to inspect")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.bin:
        try:
            report = probe_report.report_from_probe_bin(args.bin)
        except (OSError, ValueError) as exc:
            print("probe_selfreport: ERROR: %s" % exc, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("static header in %s: %s v%d size 0x%x"
                  % (args.bin, report["magic"], report["version"],
                     report["report_size"]))
        return 0

    blob, prov = build_expected_report()
    cert = PreHandoffReport().certificate()
    if args.json:
        print(json.dumps({"blob_len": len(blob), "provenance": prov,
                          "certificate": cert.as_dict()}, indent=2,
                         sort_keys=True))
    else:
        for name, _ in probe_report.DYNAMIC_FIELDS:
            print("  %-14s %-15s %s" % (name, prov[name], EVIDENCE[name]))
        print("status: %s (captured=%d source_derived=%d unknown=%d)"
              % (cert.status, len(cert.captured), len(cert.source_derived),
                 len(cert.unknown)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
