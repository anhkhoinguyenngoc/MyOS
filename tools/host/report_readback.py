#!/usr/bin/env python3
"""
MyOS M-P2.8 -- Offline report-readback prototype (SIMULATED, no hardware).

The smallest testable prototype for retrieving the MP1PRB01 probe report
bytes through a transport, operating entirely offline on synthetic memory
and a mock transport.  ALL VALUES PRODUCED HERE ARE SYNTHETIC/SIMULATED
AND ARE NEVER HARDWARE EVIDENCE.

DESIGN CHOICE -- why this candidate and no other (Phase 2, source-checked)
--------------------------------------------------------------------------
Candidate A: post-handoff ep0 class-IN "read memory" request.
  REJECTED.  ep0_device_request (pongoos-src/src/shell/usbloader.c:105)
  is a CLOSED if/else set: the only IN (0xA1) requests are bRequest 1
  (stdout ring copy via fetch_stdoutbuf) and bRequest 2 (the 1-byte
  command_in_progress flag); no IN request consumes a host-supplied
  address (`setup->wIndex` never appears).  Anything outside the set
  makes ep0_setup_stage() return false -> ep_stall(&ep0_in)
  (synopsys_otg.c ep0_out_interrupt: "The SETUP stage failed, so stall").
  Worse, after `bootr` there is no servicing context at all:
  entry.c runs timer_disable(); usb_teardown(); disable_interrupts();
  serial_teardown(); interrupt_teardown(); before jump_to_image, and
  task_irq_teardown() masks every IRQ vector.

Candidate B: KTRW ep1 (0x81) bulk push via usb_write_commit().
  REJECTED.  usb_write()/usb_write_commit() have ZERO call sites in the
  pinned tree (definition only, synopsys_otg.c), the vendor ep0 request
  implements the OUT direction only, and the OTG IRQ that would drive
  the IN transfer is masked after teardown.

Candidate C: live-shell stdout channel  <-- THE PROTOTYPE BELOW
  JUSTIFIED, every hop exists in source:
    host -> ep0 OUT bRequest 3 (stdin <=512) carries "md8 <base> <size>"
         (usbloader.c:120; main.c md8_cmd -> hexdump()),
    device -> iprintf hexdump rows "0x%09llx: %02x " x HEXDUMP_COLS=16
         (main.c:145-168) land in the stdout ring
         (io.c: stdout_buf[STDOUT_BUFLEN=0x1000], pongo.h:470),
    host <- ep0 IN bRequest 1 with wLength 512 or 0x1000
         (usbloader.c:151) drains it (fetch_stdoutbuf is destructive,
         io.c:55), and ep0 IN bRequest 2 (wLength 1) reports
         command_in_progress for completion polling (command.c:128,153).
  This path works ONLY while the PongoOS shell is alive (pre-handoff);
  at that moment the report's dynamic slots are still zero (they are
  written by the probe after the jump).  The prototype therefore
  transfers a SYNTHETIC report through a real-shaped pipe: it proves the
  transfer, framing, validation and failure handling end-to-end offline,
  and it does NOT claim post-handoff readback.

Candidate D (UART serial capture) and E (watchdog reboot channel)
  REJECTED: serial_teardown() restores the iBoot console before the
  jump (uart.c), and wdt_enable() is a dead stub (`return;` before
  `#if 0`, lowlevel.c) with wdt_disable() called by interrupt_teardown().

Failure handling, mapped to source behaviour
--------------------------------------------
  * incomplete transfer : the non-blocking stdout ring DISCARDS old
    bytes when full (io.c:88-96), fetch_stdoutbuf drains destructively,
    AND an EP0 IN whose ring content exceeds wLength serves only the
    TAIL -- the head is destroyed (usbloader.c:154-158) -- so a slow or
    wrongly-sized host loses rows; all detected by strict row accounting
    in parse_md8_strict().
  * timeout             : offline deterministic poll budget (no wall
    clock, no sleeps); a real transport would use its I/O timeout.
  * disconnect          : PongoChannelGone propagates (the mock models
    usb_teardown() as the device vanishing from the bus).
  * malformed data      : bad hex text -> MalformedTransfer; bad
    magic/version/size/reserved -> MalformedReport.
  * unavailable memory  : host-side window guard; the runtime upload
    buffer VA is heap-allocated and unknown to the host (M-P2.4), only
    the offset (0x200) and size (0x60) are source/pinned constants.
    NOTE: real md8 has NO bounds check (main.c md8_cmd), so an
    out-of-window read on hardware would be undefined behaviour; the
    guard exists on the host side of this prototype.

Validation performed (fixed-size 0x60 report)
----------------------------------------------
  magic == "MP1PRB01", version == 1, report_size == 0x60 (parse_report),
  reserved u64 at RPT_ZERO_OFF == 0, plus transfer integrity = strict
  hexdump row coverage (addresses base+16*i, exact octet counts, exact
  total length).  The MP1PRB01 format defines NO checksum field, so
  integrity here is structural, not cryptographic -- adding a checksum
  would require changing boot/aarch64/pongo_probe.S (out of scope).

Usage:
  python3 tools/host/report_readback.py --selfcheck   # offline, SIMULATED
"""

import os
import re
import sys

_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import probe_report  # noqa: E402
try:
    from probe_freshness import validate_report as _fresh_validate_report
    _HAS_FRESH = True
except Exception:
    _HAS_FRESH = False
    _fresh_validate_report = None  # type: ignore
from pongo_client import (  # noqa: E402
    PongoClient,
    PongoChannelGone,
    MockPongoDevice,
    SparseMemory,
    STDOUT_READ_LEN_MAX,
)

# -- source-pinned constants ----------------------------------------------
# usbloader.c:151 -- the only legal stdout-read wLength values.
STDOUT_WLEN = (512, STDOUT_READ_LEN_MAX)
# main.c:145 -- #define HEXDUMP_COLS 16
HEXDUMP_COLS = 16
# command.c:144 -- iprintf("pongoOS> ") idle prompt, may sit in the ring.
SHELL_PROMPT = "pongoOS>"
# usbloader.c:26 -- #define UPLOADSZ (1024 * 1024); the host knows the
# buffer SIZE it armed, never the runtime VA.
UPLOADSZ = 1024 * 1024
# Offline poll budget: deterministic, no wall-clock (see docstring).
DEFAULT_MAX_POLLS = 16


class ReadbackError(Exception):
    """Base class for prototype readback failures."""


class UnavailableMemory(ReadbackError):
    """The requested report range is outside the host-known window
    (synthetic/unknown runtime address -- no transport I/O attempted)."""


class ReadbackTimeout(ReadbackError):
    """Poll budget exhausted without the command completing."""


class IncompleteTransfer(ReadbackError):
    """The stdout stream ended before all report bytes arrived (ring
    discard, dropped IN stage, truncation)."""


class MalformedTransfer(ReadbackError):
    """Transport text did not match the device hexdump framing."""


class MalformedReport(ReadbackError):
    """Bytes arrived but the report fails magic/version/size/reserved
    validation."""


# Disconnect handling: PongoChannelGone (pongo_client) IS the disconnect
# classification and deliberately propagates out of read_report().


_ROW_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]+):\s*(.*)$")
_OCTET_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{2}$")


def parse_md8_strict(data, base, size):
    """Reassemble `size` bytes from device hexdump text with full
    integrity checking (rows at base+16*i, exact octet counts).

    Raises IncompleteTransfer when data was lost, MalformedTransfer when
    the framing itself is wrong, MalformedReport never (pure transport).
    """
    if size <= 0:
        raise MalformedTransfer("invalid expected size %d" % size)
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).decode("ascii", "replace")
    else:
        text = str(data)

    exp_rows = (size + HEXDUMP_COLS - 1) // HEXDUMP_COLS
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(SHELL_PROMPT):
            continue  # idle prompt from command.c command_main()
        m = _ROW_RE.match(line)
        if not m:
            raise MalformedTransfer("unparseable line: %r" % line)
        octets = []
        for tok in m.group(2).split():
            if not _OCTET_RE.match(tok):
                raise MalformedTransfer("invalid hex octet %r in %r"
                                        % (tok, line))
            octets.append(int(tok, 16))
        rows.append((int(m.group(1), 16), octets))

    if len(rows) < exp_rows:
        raise IncompleteTransfer("only %d of %d hexdump rows arrived"
                                 % (len(rows), exp_rows))
    if len(rows) > exp_rows:
        raise MalformedTransfer("unexpected extra rows: %d > %d"
                                % (len(rows), exp_rows))

    out = bytearray()
    for i, (addr, octets) in enumerate(rows):
        exp_addr = base + i * HEXDUMP_COLS
        if addr != exp_addr:
            if addr > exp_addr:
                raise IncompleteTransfer(
                    "row %d address 0x%x > expected 0x%x (data lost)"
                    % (i, addr, exp_addr))
            raise MalformedTransfer(
                "row %d address 0x%x < expected 0x%x (duplicate/replay)"
                % (i, addr, exp_addr))
        want = min(HEXDUMP_COLS, size - i * HEXDUMP_COLS)
        if len(octets) < want:
            raise IncompleteTransfer(
                "row %d truncated: %d of %d octets" % (i, len(octets), want))
        if len(octets) > want:
            raise MalformedTransfer(
                "row %d has %d octets, want %d" % (i, len(octets), want))
        out += bytes(octets)

    if len(out) != size:
        raise IncompleteTransfer("assembled %d bytes, want %d"
                                 % (len(out), size))
    return bytes(out)


def validate_report(blob, expected_token=None):
    """Validate report using canonical freshness validator where available.
    Falls back to legacy structural validation if freshness module unavailable.
    Returns the parsed/validated report dict on success for legacy path,
    otherwise raises MalformedReport on invalid; status semantics are
    exposed via caller-side checks when using canonical validator."""
    if _HAS_FRESH and _fresh_validate_report is not None:
        st, det = _fresh_validate_report(blob, expected_token=expected_token)
        if st == "INVALID":
            raise MalformedReport("freshness: INVALID (%s)" % det)
        if st == "V2_VALID_STALE":
            raise MalformedReport("freshness: V2_VALID_STALE (%s)" % det)
        # For canonical cases, propagate semantic status via exception? No.
        # But we need to preserve legacy API returning report dict.
        # Return a small dict carrying status for callers that inspect.
        return {
            "freshness_status": st,
            "freshness_detail": det,
        }
    # Legacy path
    if blob is None or len(blob) != probe_report.RPT_SIZE:
        raise MalformedReport(
            "report must be exactly 0x%x bytes, got %s"
            % (probe_report.RPT_SIZE,
               "None" if blob is None else "0x%x" % len(blob)))
    try:
        report = probe_report.parse_report(blob)
    except ValueError as exc:
        raise MalformedReport(str(exc))
    reserved = report.get("reserved_zero", 0)
    if reserved != 0:
        raise MalformedReport(
            "reserved field at 0x%x must be zero, got 0x%x"
            % (probe_report.EQU_MIRROR["RPT_ZERO_OFF"], reserved))
    return report


class ReportReadback:
    """Host-side driver for candidate C (live-shell stdout channel).

    Takes any client exposing send_command/read_stdout/check_complete/
    drain_stdout (the real PongoClient over an injectable Transport, or
    a test double).  Works ONLY pre-handoff; post-handoff the transport
    raises PongoChannelGone, which propagates as the disconnect signal.
    """

    def __init__(self, client, window, maxlen=STDOUT_READ_LEN_MAX,
                 max_polls=DEFAULT_MAX_POLLS):
        if maxlen not in STDOUT_WLEN:
            # usbloader.c:151 -- any other wLength makes the device stall.
            raise ValueError("maxlen must be one of %r, got %d"
                             % (STDOUT_WLEN, maxlen))
        if max_polls < 1:
            raise ValueError("max_polls must be >= 1")
        self.client = client
        self.window = window          # (lo, hi): host-known valid range
        self.maxlen = maxlen
        self.max_polls = max_polls

    def read_report(self, buffer_va):
        """Read and validate the fixed-size report at
        buffer_va + RPT_OFF through the stdout channel."""
        if not isinstance(buffer_va, int) or buffer_va <= 0:
            raise UnavailableMemory(
                "invalid buffer base %r (md8 requires a non-zero base, "
                "main.c md8_cmd)" % (buffer_va,))
        addr = buffer_va + probe_report.RPT_OFF
        end = addr + probe_report.RPT_SIZE
        lo, hi = self.window
        if not (lo <= addr and end <= hi):
            raise UnavailableMemory(
                "report range [0x%x,0x%x) outside host-known window "
                "[0x%x,0x%x); the runtime upload-buffer VA is unknown to "
                "the host (M-P2.4) and this offline value is SYNTHETIC"
                % (addr, end, lo, hi))

        # Discard any stale prompt/echo already sitting in the ring
        # (destructive fetch_stdoutbuf, io.c:55) before issuing the dump.
        self.client.drain_stdout()
        # usbloader.c:123-128: wValue 0 sets should_wait_for_cmd_handler,
        # so the stdin write below event-waits (usbloader.c:60-61) until
        # the command handler completed -- no premature-idle race with
        # command_in_progress (command.c:153-155).
        self.client.set_stdout_mode(0)
        self.client.send_command("md8 0x%x 0x%x" % (addr, probe_report.RPT_SIZE))
        text = self._collect()
        blob = parse_md8_strict(text, addr, probe_report.RPT_SIZE)
        return validate_report(blob)

    def _collect(self):
        """Drain stdout until empty AND the command is idle, within the
        deterministic poll budget.  PongoChannelGone propagates."""
        out = bytearray()
        for _ in range(self.max_polls):
            chunk = self.client.read_stdout(self.maxlen)
            if chunk:
                out += chunk
                continue
            if not self.client.check_complete():
                return bytes(out)
        raise ReadbackTimeout(
            "command did not complete within %d polls (offline poll "
            "budget; no wall-clock timeout is modelled)" % self.max_polls)


# ---------------------------------------------------------------------------
# CLI self-check -- OFFLINE, every value SYNTHETIC/SIMULATED
# ---------------------------------------------------------------------------
def _synthetic_report():
    rpt = bytearray(probe_report.RPT_SIZE)
    rpt[0:8] = probe_report.MAGIC_TXT
    import struct
    struct.pack_into("<I", rpt, probe_report.EQU_MIRROR["RPT_VER_OFF"],
                     probe_report.RPT_VERSION)
    struct.pack_into("<I", rpt, probe_report.EQU_MIRROR["RPT_SIZE_OFF"],
                     probe_report.RPT_SIZE)
    struct.pack_into("<Q", rpt, probe_report.EQU_MIRROR["RPT_X30_OFF"],
                     0x180510000)
    struct.pack_into("<Q", rpt, probe_report.EQU_MIRROR["RPT_EL_OFF"], 0x8)
    return bytes(rpt)


def _selfcheck():
    BUFFER_VA = 0x1000a9000        # SYNTHETIC VA (invented, not hardware)
    WINDOW = (BUFFER_VA, BUFFER_VA + UPLOADSZ)
    print("report_readback: OFFLINE SELF-CHECK (ALL VALUES SIMULATED)")

    # 1) success path: synthetic report -> mock device -> strict reader
    rpt = _synthetic_report()
    mem = SparseMemory(limit=BUFFER_VA + probe_report.RPT_OFF + probe_report.RPT_SIZE)
    mem.poke(BUFFER_VA + probe_report.RPT_OFF, rpt)
    dev = MockPongoDevice(memory=mem, alive=True)
    reader = ReportReadback(PongoClient(dev), WINDOW)
    report = reader.read_report(BUFFER_VA)
    assert report["magic"] == "MP1PRB01"
    assert report["report_size"] == probe_report.RPT_SIZE
    assert report["reserved_zero"] == 0
    assert report["x30_entry"] == 0x180510000
    print("  [SIMULATED] success: 0x%x-byte report transferred and "
          "validated (magic/version/size/reserved/integrity)" % probe_report.RPT_SIZE)

    # 2) unavailable memory: out-of-window address -> no I/O at all
    before = len(dev.control_log)
    try:
        reader.read_report(BUFFER_VA + 0x100000)
    except UnavailableMemory:
        assert len(dev.control_log) == before, "guard must run before I/O"
        print("  [SIMULATED] unavailable memory: rejected before any I/O")
    else:
        assert False, "out-of-window read must raise UnavailableMemory"

    # 3) malformed report: corrupt magic in device memory
    bad = bytearray(rpt)
    bad[0:8] = b"BADPRB01"
    mem.poke(BUFFER_VA + probe_report.RPT_OFF, bytes(bad))
    try:
        reader.read_report(BUFFER_VA)
    except MalformedReport:
        print("  [SIMULATED] malformed report: bad magic rejected")
    else:
        assert False, "bad magic must raise MalformedReport"

    # 4) disconnect: usb_teardown model -> PongoChannelGone propagates
    mem.poke(BUFFER_VA + probe_report.RPT_OFF, rpt)   # restore
    dev.set_teardown()
    try:
        reader.read_report(BUFFER_VA)
    except PongoChannelGone:
        print("  [SIMULATED] disconnect: PongoChannelGone propagates "
              "(no post-`bootr` channel, entry.c teardown)")
        print("report_readback: OFFLINE SELF-CHECK PASS")
        return 0
    assert False, "expected PongoChannelGone after teardown"


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--selfcheck":
        return _selfcheck()
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the offline SIMULATED self-check")
    ap.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
