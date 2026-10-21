#!/usr/bin/env python3
"""
MyOS M-P2.8 -- deterministic tests for the OFFLINE report-readback
prototype (tools/host/report_readback.py).

EVERY REPORT VALUE, ADDRESS AND RESULT BELOW IS SYNTHETIC/SIMULATED:
the transport is a mock, the memory is synthetic, and nothing here is
hardware evidence.  The tests prove, against the pinned repo as ground
truth:

  * the chosen candidate (live-shell stdout channel: md8 -> hexdump ->
    stdout ring -> ep0 IN) transfers a fixed-size 0x60 report end-to-end
    and validates magic, version, size, reserved field and transfer
    integrity;
  * failure paths are handled distinctly: partial transfer, incomplete
    transfer, timeout, disconnect, malformed text, malformed report and
    unavailable memory;
  * every framing assumption is backed by a line of pinned source
    (TestSourceContract), so source drift fails the suite loudly.

Usage:
  python3 tests/test_report_readback.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import re
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "report_readback.py")
CLIENT = os.path.join(REPO, "tools", "host", "pongo_client.py")
SRC = os.path.join(REPO, "pongoos-src", "src")
PROBE_S = os.path.join(REPO, "platform", "apple", "a8", "pongo_probe.S")

sys.path.insert(0, os.path.join(REPO, "tools", "host"))
import probe_report as pr  # noqa: E402
import pongo_client as pc  # noqa: E402
import pongo_addr_resolve as par  # noqa: E402  (link-6 datum gate)

PONGO_MACHO = os.path.join(REPO, "pongoos-src", "build", "Pongo")

_spec = importlib.util.spec_from_file_location("report_readback", TOOL)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _mk_report(magic=pr.MAGIC_TXT, version=None, size=None, reserved=0,
               x30=0x180510000, el=0x8):
    """SYNTHETIC report blob with explicit, invented dynamic values."""
    rpt = bytearray(pr.RPT_SIZE)
    rpt[0:8] = magic
    struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_VER_OFF"],
                     pr.RPT_VERSION if version is None else version)
    struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_SIZE_OFF"],
                     pr.RPT_SIZE if size is None else size)
    struct.pack_into("<Q", rpt, pr.EQU_MIRROR["RPT_X30_OFF"], x30)
    struct.pack_into("<Q", rpt, pr.EQU_MIRROR["RPT_EL_OFF"], el)
    struct.pack_into("<Q", rpt, pr.EQU_MIRROR["RPT_ZERO_OFF"], reserved)
    return bytes(rpt)


# --------------------------------------------------------------------------
# Test-side device doubles.  Each models one real failure mode of the
# pinned source, SYNTHETICALLY.  They only SUBCLASS the faithful mock; the
# production mock (MockPongoDevice) is not modified.
# --------------------------------------------------------------------------
class RecordingDevice(pc.MockPongoDevice):
    """Captures the shell commands the reader issues."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.commands = []

    def _process_command(self, cmd):
        self.commands.append(cmd)
        super()._process_command(cmd)


class ChunkedStdoutDevice(pc.MockPongoDevice):
    """Models partial IN stages: at most `cap` stdout bytes per
    ep0 IN read (host must reassemble across reads)."""

    def __init__(self, cap, **kw):
        super().__init__(**kw)
        self.cap = cap
        self.data_reads = 0

    def control_in(self, bm, breq, w_length, w_index=0, w_value=0):
        if bm == pc.BM_TYPE_IN and breq == pc.REQ_STDOUT_READ:
            out = super().control_in(bm, breq, w_length, w_index, w_value)
            if len(out) > self.cap:
                head, tail = out[:self.cap], out[self.cap:]
                self.stdbuf[:0] = tail      # remainder for the next read
                if head:
                    self.data_reads += 1
                return head
            if out:
                self.data_reads += 1
            return out
        return super().control_in(bm, breq, w_length, w_index, w_value)


class TruncatedStdoutDevice(pc.MockPongoDevice):
    """Models the non-blocking stdout ring discarding bytes when full
    (io.c:88-96): the hexdump text arrives short (whole rows missing)."""

    keep_rows = 2

    def _process_command(self, cmd):
        super()._process_command(cmd)
        rows, cut = 0, 0
        for i, b in enumerate(self.stdbuf):
            if b == 0x0A:
                rows += 1
                cut = i + 1
                if rows == self.keep_rows:
                    break
        del self.stdbuf[cut:]


class HungCommandDevice(pc.MockPongoDevice):
    """Models a command handler that never finishes: bRequest 2 keeps
    reporting command_in_progress=1 and stdout stays empty."""

    def control_out(self, bm, breq, w_value=0, w_length=0, data=b"",
                    w_index=0):
        if bm == pc.BM_TYPE_OUT and breq == pc.REQ_STDIN:
            if not self.alive:
                raise pc.PongoChannelGone("control OUT after teardown")
            self.control_log.append((bm, breq, w_value, w_length))
            return                      # SYNTHETIC hang: accepted, never runs
        return super().control_out(bm, breq, w_value, w_length, data, w_index)

    def control_in(self, bm, breq, w_length, w_index=0, w_value=0):
        if bm == pc.BM_TYPE_IN and breq in (pc.REQ_STDOUT_READ,
                                            pc.REQ_COMPLETE):
            if not self.alive:
                raise pc.PongoChannelGone("control IN after teardown")
            self.control_log.append((bm, breq, 0, w_length))
            if breq == pc.REQ_COMPLETE:
                return b"\x01"          # command_in_progress = 1, forever
            return b""
        return super().control_in(bm, breq, w_length, w_index, w_value)


class DisconnectingDevice(pc.MockPongoDevice):
    """Models usb_teardown() racing the drain: after N stdout reads the
    device vanishes from the bus."""

    reads_before_gone = 1

    def __init__(self, **kw):
        super().__init__(**kw)
        self._stdout_reads = 0

    def control_in(self, bm, breq, w_length, w_index=0, w_value=0):
        if (bm == pc.BM_TYPE_IN and breq == pc.REQ_STDOUT_READ
                and self.alive):
            self._stdout_reads += 1
            if self._stdout_reads > self.reads_before_gone:
                self.set_teardown()
        return super().control_in(bm, breq, w_length, w_index, w_value)


class CorruptTextDevice(pc.MockPongoDevice):
    """Models corrupted transport text (a hex octet that is not hex)."""

    def _process_command(self, cmd):
        super()._process_command(cmd)
        self.stdbuf = bytearray(
            re.sub(rb"(: )[0-9a-fA-F]{2}", rb"\1zz", self.stdbuf, count=1))


class PromptDevice(pc.MockPongoDevice):
    """Emits the shell's idle prompt after the output (command.c)."""

    def _process_command(self, cmd):
        super()._process_command(cmd)
        self.stdbuf += b"pongoOS> "


class BannerDevice(pc.MockPongoDevice):
    """SIMULATES unrelated shell output landing in the stdout ring while
    the md8 command runs, pushing the total above a 512-byte EP0 IN."""

    def _process_command(self, cmd):
        super()._process_command(cmd)
        self._ring_write(b"boot log line from the device\n" * 14)


class ReadbackBase(unittest.TestCase):
    """SYNTHETIC fixture: invented VA, mock transport, mock RAM."""

    BUFFER_VA = 0x1000a9000           # SYNTHETIC, not a hardware address

    def setUp(self):
        self.rpt = _mk_report()
        self.mem = pc.SparseMemory(
            limit=self.BUFFER_VA + pr.RPT_OFF + pr.RPT_SIZE)
        self.mem.poke(self.BUFFER_VA + pr.RPT_OFF, self.rpt)
        self.window = (self.BUFFER_VA, self.BUFFER_VA + rr.UPLOADSZ)
        self.dev = RecordingDevice(memory=self.mem, alive=True)
        self.client = pc.PongoClient(self.dev)
        self.reader = rr.ReportReadback(self.client, self.window)

    def poke(self, rpt):
        self.mem.poke(self.BUFFER_VA + pr.RPT_OFF, rpt)


class TestSyntheticSuccess(ReadbackBase):
    """SIMULATED success path: fixed-size report transferred + validated."""

    def test_roundtrip_validates_every_field(self):
        report = self.reader.read_report(self.BUFFER_VA)
        self.assertEqual(report["magic"], "MP1PRB01")
        self.assertEqual(report["version"], pr.RPT_VERSION)
        self.assertEqual(report["report_size"], pr.RPT_SIZE)
        self.assertEqual(report["reserved_zero"], 0)
        self.assertEqual(report["x30_entry"], 0x180510000)  # SYNTHETIC
        self.assertEqual(report["current_el"], 0x8)         # SYNTHETIC
        self.assertTrue(pr.has_dynamic_data(report),
                        "SYNTHETIC capture must carry dynamic slots")

    def test_reader_issues_md8_at_report_offset(self):
        self.reader.read_report(self.BUFFER_VA)
        self.assertEqual(
            self.dev.commands,
            [b"md8 0x%x 0x%x" % (self.BUFFER_VA + pr.RPT_OFF, pr.RPT_SIZE)])

    def test_stdout_reads_use_a_legal_wlength(self):
        # usbloader.c:151 -- only 512 or 0x1000 are served, else stall.
        self.reader.read_report(self.BUFFER_VA)
        stdout_reads = [e for e in self.dev.control_log
                        if e[0] == pc.BM_TYPE_IN and e[1] == pc.REQ_STDOUT_READ]
        self.assertTrue(stdout_reads)
        for entry in stdout_reads:
            self.assertIn(entry[3], rr.STDOUT_WLEN)


class TestPartialTransfer(ReadbackBase):
    def test_fragmented_stdout_is_reassembled(self):
        dev = ChunkedStdoutDevice(cap=64, memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        report = reader.read_report(self.BUFFER_VA)
        self.assertEqual(report["magic"], "MP1PRB01")
        self.assertGreaterEqual(dev.data_reads, 2,
                                "report must arrive across multiple reads")


class TestIncompleteTransfer(ReadbackBase):
    def test_missing_rows_detected(self):
        dev = TruncatedStdoutDevice(memory=self.mem, alive=True)
        dev.keep_rows = 2
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        with self.assertRaises(rr.IncompleteTransfer) as cm:
            reader.read_report(self.BUFFER_VA)
        self.assertIn("2 of 6", str(cm.exception))

    def test_head_of_ring_dropped_detected(self):
        # The ring discards the OLDEST bytes first (io.c:88-96), so the
        # first hexdump row can vanish while later rows survive.
        dev = TruncatedStdoutDevice(memory=self.mem, alive=True)
        dev.keep_rows = 6                      # all rows kept ...
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        # ... but drop the first row's text from the ring before drain.
        orig = dev._process_command

        def drop_head(cmd):
            orig(cmd)
            first_nl = dev.stdbuf.find(b"\n")
            del dev.stdbuf[:first_nl + 1]

        dev._process_command = drop_head
        with self.assertRaises(rr.IncompleteTransfer) as cm:
            reader.read_report(self.BUFFER_VA)
        self.assertIn("5 of 6", str(cm.exception))


class TestTimeout(ReadbackBase):
    def test_poll_budget_exhausted_raises_timeout(self):
        dev = HungCommandDevice(memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window,
                                   max_polls=4)
        with self.assertRaises(rr.ReadbackTimeout) as cm:
            reader.read_report(self.BUFFER_VA)
        self.assertIn("4 polls", str(cm.exception))
        # no bytes were ever returned while the command was in progress
        self.assertFalse(dev.stdbuf)


class TestDisconnect(ReadbackBase):
    def test_channel_gone_propagates(self):
        dev = DisconnectingDevice(memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        with self.assertRaises(pc.PongoChannelGone):
            reader.read_report(self.BUFFER_VA)
        self.assertFalse(dev.alive)


class TestMalformedData(ReadbackBase):
    def test_corrupt_hex_text_rejected(self):
        dev = CorruptTextDevice(memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        with self.assertRaises(rr.MalformedTransfer):
            reader.read_report(self.BUFFER_VA)

    def test_bad_report_fields_rejected(self):
        cases = {
            "magic": _mk_report(magic=b"BADPRB01"),
            "version": _mk_report(version=2),
            "size": _mk_report(size=pr.RPT_SIZE + 4),
            "reserved": _mk_report(reserved=1),
        }
        for field, blob in cases.items():
            with self.subTest(field=field):
                self.poke(blob)
                with self.assertRaises(rr.MalformedReport) as cm:
                    self.reader.read_report(self.BUFFER_VA)
                self.assertTrue(str(cm.exception),
                                "error must carry a diagnostic message")
        self.poke(self.rpt)             # restore fixture


class TestUnavailableMemory(ReadbackBase):
    def test_out_of_window_rejected_before_any_io(self):
        with self.assertRaises(rr.UnavailableMemory):
            self.reader.read_report(self.BUFFER_VA + 0x100000)
        self.assertEqual(self.dev.control_log, [],
                         "no transport I/O may happen after the guard")

    def test_nonpositive_base_rejected(self):
        with self.assertRaises(rr.UnavailableMemory):
            self.reader.read_report(0)
        with self.assertRaises(rr.UnavailableMemory):
            self.reader.read_report(-0x1000)
        self.assertEqual(self.dev.control_log, [])


class TestPromptAndStaleOutput(ReadbackBase):
    def test_stale_ring_content_is_drained_first(self):
        self.dev.stdbuf += b"previous command output\r\n"  # stale bytes
        report = self.reader.read_report(self.BUFFER_VA)
        self.assertEqual(report["magic"], "MP1PRB01")

    def test_trailing_prompt_lines_are_tolerated(self):
        dev = PromptDevice(memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window)
        report = reader.read_report(self.BUFFER_VA)
        self.assertEqual(report["magic"], "MP1PRB01")


class TestReaderContract(unittest.TestCase):
    def test_illegal_wlength_rejected(self):
        client = pc.PongoClient(pc.MockPongoDevice(memory=b"", alive=True))
        with self.assertRaises(ValueError):
            rr.ReportReadback(client, (0, 0x1000), maxlen=64)   # stalls!
        with self.assertRaises(ValueError):
            rr.ReportReadback(client, (0, 0x1000), max_polls=0)


class TestStrictParser(unittest.TestCase):
    BASE = 0x1000a9200

    def _text(self, rows, base=None):
        base = self.BASE if base is None else base
        out = b""
        for i, octets in enumerate(rows):
            out += ("0x%x: " % (base + i * 16)).encode()
            out += b"".join(b"%02x " % o for o in octets) + b"\n"
        return out

    def test_exact_roundtrip(self):
        want = bytes(range(0x60))
        rows = [want[i * 16:(i + 1) * 16] for i in range(6)]
        self.assertEqual(rr.parse_md8_strict(self._text(rows),
                                             self.BASE, 0x60), want)

    def test_empty_stream_is_incomplete(self):
        with self.assertRaises(rr.IncompleteTransfer):
            rr.parse_md8_strict(b"", self.BASE, 0x60)

    def test_dropped_head_is_incomplete(self):
        rows = [bytes(range(i * 16, i * 16 + 16)) for i in range(6)]
        # first row starts 0x10 late (head of the ring was discarded)
        text = self._text(rows, base=self.BASE + 16)
        with self.assertRaises(rr.IncompleteTransfer) as cm:
            rr.parse_md8_strict(text, self.BASE, 0x60)
        self.assertIn("data lost", str(cm.exception))

    def test_duplicate_row_is_malformed(self):
        rows = [bytes(range(i * 16, i * 16 + 16)) for i in range(6)]
        text = self._text(rows).splitlines(True)
        text[1] = text[0]              # duplicate row 0 in slot 1
        with self.assertRaises(rr.MalformedTransfer):
            rr.parse_md8_strict(b"".join(text), self.BASE, 0x60)

    def test_garbage_line_is_malformed(self):
        with self.assertRaises(rr.MalformedTransfer):
            rr.parse_md8_strict(b"not a hexdump line\n", self.BASE, 0x60)

    def test_short_last_row_is_incomplete(self):
        rows = [bytes(range(i * 16, i * 16 + 16)) for i in range(6)]
        lines = self._text(rows).splitlines(True)
        lines[5] = lines[5][:43]        # prefix + 10 octets, no newline
        with self.assertRaises(rr.IncompleteTransfer) as cm:
            rr.parse_md8_strict(b"".join(lines), self.BASE, 0x60)
        self.assertIn("truncated", str(cm.exception))

    def test_non_hex_octet_is_malformed(self):
        rows = [b"\x00" * 16 for _ in range(6)]
        text = bytearray(self._text(rows))
        text = re.sub(rb"(: )[0-9a-fA-F]{2}", rb"\1zz", bytes(text), count=1)
        with self.assertRaises(rr.MalformedTransfer):
            rr.parse_md8_strict(text, self.BASE, 0x60)

    def test_validate_report_rejects_wrong_length(self):
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(b"\x00" * 16)
        with self.assertRaises(rr.MalformedReport):
            rr.validate_report(None)


def _src(name):
    return _read(os.path.join(SRC, name))


class TestSourceContract(unittest.TestCase):
    """Every framing/validation assumption of the prototype must be
    backed by the pinned source; drift fails here."""

    def test_hexdump_framing_matches_device(self):
        main = _src(os.path.join("shell", "main.c"))
        self.assertIn("#define HEXDUMP_COLS 16", main)
        self.assertIn('iprintf("0x%09llx: ", ', main)
        self.assertIn('iprintf("%02x ", ', main)
        self.assertIn("hexdump((void*)base, size);", main)

    def test_md8_requires_nonzero_base_and_size(self):
        main = _src(os.path.join("shell", "main.c"))
        self.assertIn("if (!size || !base)", main)
        self.assertIn("strtoull(args, NULL, 16)", main)

    def test_stdout_read_wlengths_are_exactly_512_or_0x1000(self):
        loader = _src(os.path.join("shell", "usbloader.c"))
        self.assertIn(
            "setup->bRequest == 1 && (setup->wLength == 512 || "
            "setup->wLength == 0x1000)", loader)
        self.assertIn("fetch_stdoutbuf(buf, &xferlen);", loader)

    def test_stdin_stage_is_1_to_512_bytes(self):
        loader = _src(os.path.join("shell", "usbloader.c"))
        self.assertIn(
            "setup->bRequest == 3 && setup->wLength > 0 && "
            "setup->wLength <= 512", loader)

    def test_upload_buffer_size_constant(self):
        loader = _src(os.path.join("shell", "usbloader.c"))
        self.assertIn("#define UPLOADSZ (1024 * 1024)", loader)
        self.assertGreater(rr.UPLOADSZ, pr.RPT_OFF + pr.RPT_SIZE,
                           "report must fit inside one default upload buffer")

    def test_stdout_ring_is_0x1000_and_destructive(self):
        pongo_h = _src("kernel/pongo.h")
        self.assertIn("#define STDOUT_BUFLEN 0x1000", pongo_h)
        io_c = _src("kernel/support/io.c")
        start = io_c.index("void fetch_stdoutbuf(")
        body = io_c[start:start + 300]
        self.assertIn("stdout_buf_len = 0;", body)   # destructive drain
        # non-blocking ring DISCARDS old bytes -> incomplete transfers
        self.assertIn("non-blocking = discard", io_c)

    def test_completion_flag_and_prompt_come_from_command_main(self):
        cmd = _src("shell/command.c")
        self.assertIn("volatile uint8_t command_in_progress = 0;", cmd)
        self.assertIn('iprintf("pongoOS> ")', cmd)

    def test_report_layout_constants_match_probe_source(self):
        equ = pr.equ_constants_from_source(_read(PROBE_S))
        self.assertEqual(equ["RPT_OFF"], pr.RPT_OFF)
        self.assertEqual(equ["RPT_SIZE"], pr.RPT_SIZE)
        self.assertEqual(equ["RPT_VERSION"], pr.RPT_VERSION)
        self.assertEqual(rr.HEXDUMP_COLS, 16)

    def test_prototype_is_documented_as_offline_simulated(self):
        doc = _read(TOOL)
        self.assertIn("SIMULATED", doc)
        self.assertIn("NEVER HARDWARE EVIDENCE", doc)
        # The chosen candidate and every rejected one are recorded.
        for tag in ("Candidate A", "Candidate B", "Candidate C",
                    "Candidate D"):
            self.assertIn(tag, doc)


# ==========================================================================
# M-P2.9 additions: source-backed transfer boundary cases and the link-6
# address gate.  ALL FIXTURES AND RESULTS ARE SIMULATED.
# ==========================================================================
class TestRingOverflowDiscard(unittest.TestCase):
    """io.c:88-101 -- the default non-blocking ring (stdout_blocking
    starts false, io.c:43) discards the OLDEST bytes when full."""

    def test_big_dump_is_capped_and_head_is_discarded(self):
        dev = RecordingDevice(memory=pc.SparseMemory(limit=0), alive=True)
        # 0x1000 bytes -> 256 rows x 62 chars = 15872 bytes of text
        dev._process_command(b"md8 0x1000a9200 0x1000")
        self.assertEqual(len(dev.stdbuf), dev.RING_CAP)
        self.assertLessEqual(len(dev.stdbuf), 0x1000 - 1)  # source cap
        # the OLDEST row is gone ...
        self.assertNotIn(b"0x1000a9200: ", dev.stdbuf)
        # ... while the NEWEST row survives byte-exact (base + 0xff0)
        self.assertIn(b"0x1000aa1f0: ", dev.stdbuf)


class TestEp0InTailServe(unittest.TestCase):
    """usbloader.c:151-159 -- fetch_stdoutbuf drains the WHOLE ring
    (destructive) and serves only the TAIL when content > wLength; the
    head is destroyed, not deferred."""

    def test_oversized_ring_serves_exact_tail_then_empty(self):
        dev = pc.MockPongoDevice(memory=b"", alive=True)
        payload = bytes(i % 256 for i in range(600))   # SIMULATED bytes
        dev._ring_write(payload)
        out = dev.control_in(pc.BM_TYPE_IN, pc.REQ_STDOUT_READ, 512)
        self.assertEqual(out, payload[88:])            # exact tail bytes
        # second read is empty: the head is permanently gone
        self.assertEqual(
            dev.control_in(pc.BM_TYPE_IN, pc.REQ_STDOUT_READ, 512), b"")


class TestLossDetectedAt512(ReadbackBase):
    def test_reader_never_returns_a_wrong_report_when_head_is_lost(self):
        dev = BannerDevice(memory=self.mem, alive=True)
        reader = rr.ReportReadback(pc.PongoClient(dev), self.window,
                                   maxlen=512)
        # ring content = 372 report bytes + 420 banner bytes > 512, so
        # the EP0 IN serves only the tail and the report head is lost.
        with self.assertRaises((rr.MalformedTransfer,
                                rr.IncompleteTransfer)):
            reader.read_report(self.BUFFER_VA)


class TestExactBytePreservation(ReadbackBase):
    """All 0x60 bytes survive the device-shaped hexdump framing exactly,
    including values >= 0x80 (main.c:112 `0xFF & ((char*)mem)[i]`).
    SIMULATED byte values."""

    def _highbit_report(self):
        rpt = bytearray(_mk_report())
        for off in range(0x10, 0x58):        # dynamic slots only
            rpt[off] = (0x80 + off) & 0xFF   # 0x90..0xD7, all >= 0x80
        return bytes(rpt)

    def test_all_96_bytes_survive_exactly(self):
        blob = self._highbit_report()
        self.assertTrue(any(b >= 0x80 for b in blob[0x10:0x58]))
        self.poke(blob)
        report = self.reader.read_report(self.BUFFER_VA)
        for name, off in pr.DYNAMIC_FIELDS:
            want = struct.unpack_from("<Q", blob, off)[0]
            self.assertEqual(report[name], want, name)
        # byte-for-byte at the transport level too:
        addr = self.BUFFER_VA + pr.RPT_OFF
        text = self.client.md8(addr, pr.RPT_SIZE)
        self.assertEqual(rr.parse_md8_strict(text, addr, pr.RPT_SIZE), blob)


class TestAddressWidthParsing(unittest.TestCase):
    """`0x%09llx` is a MINIMUM width (main.c:106): row prefixes may be
    9..16+ hex digits depending on the base address."""

    WANT = bytes(range(0x60))

    def _rows(self, base, width):
        out = b""
        for i in range(6):
            out += ("0x%0*x: " % (width, base + i * 16)).encode()
            out += b"".join(b"%02x " % o
                            for o in self.WANT[i * 16:(i + 1) * 16])
            out += b"\n"
        return out

    def test_minimum_width_zero_padded(self):
        base = 0xA9200
        self.assertEqual(
            rr.parse_md8_strict(self._rows(base, 9), base, 0x60), self.WANT)

    def test_wider_than_minimum(self):
        base = 0x1000a9200
        self.assertEqual(
            rr.parse_md8_strict(self._rows(base, 16), base, 0x60), self.WANT)


class TestModeFramingOrder(ReadbackBase):
    """The reader must set stdout mode 0 (bRequest 4 wValue 0) BEFORE the
    md8 stdin write, so the write event-waits for command completion
    (usbloader.c:51,60-61,123-128) instead of racing the flag."""

    def test_mode_set_before_stdin_then_stdout_reads(self):
        self.reader.read_report(self.BUFFER_VA)
        log = self.dev.control_log
        i_mode = log.index((pc.BM_TYPE_OUT, pc.REQ_STDOUT_MODE, 0, 0))
        i_stdin = next(i for i, e in enumerate(log)
                       if e[0] == pc.BM_TYPE_OUT and e[1] == pc.REQ_STDIN)
        out_reads = [i for i, e in enumerate(log)
                     if e[0] == pc.BM_TYPE_IN and e[1] == pc.REQ_STDOUT_READ]
        self.assertLess(i_mode, i_stdin)
        self.assertTrue(any(i > i_stdin for i in out_reads),
                        "stdout must be drained after the command is issued")


class TestAddressGate(unittest.TestCase):
    """Link 6, executable: even the two-step leak (md8 the pointer var,
    then md8 the report) is gated on runtime datums that artifacts do
    not contain (M-P2.4 STOP_AT_EVIDENCE)."""

    @unittest.skipUnless(os.path.isfile(PONGO_MACHO),
                         "pongoos-src/build/Pongo absent")
    def test_slide_resolves_pointer_but_never_the_buffer(self):
        m = par.parse_macho(PONGO_MACHO)
        # With a slide datum the POINTER VARIABLE's runtime VA resolves
        # (that is what a host would md8 first) ...
        r = par.resolve_symbol(m, "_loader_xfer_recv_data", slide=0x1000)
        self.assertIsNotNone(r["runtime_va"])
        # ... but the slide alone NEVER yields the buffer: its value is
        # heap-allocated (usbloader.c:94-99).
        self.assertEqual(par.resolve_buffer()["status"],
                         "UNRESOLVED_NEEDS_RUNTIME_DATUM")
        # Given the actual buffer datum, everything resolves -- the only
        # missing piece is that one runtime value.
        res = par.resolve_buffer(buffer_va=0x412340000)
        self.assertEqual(res["status"], "RESOLVED_FROM_RUNTIME_DATUM")
        self.assertEqual(res["report_va"], 0x412340200)


def _body(src, signature):
    start = src.index(signature)
    end = src.index("\n}\n", start)
    return src[start:end]


class TestCandidateCSourceLinks(unittest.TestCase):
    """M-P2.9: pin every remaining claim of the candidate-C trace to the
    exact pinned source; any drift fails here."""

    def test_md8_is_a_raw_unbounded_dereference(self):
        main = _read(os.path.join(SRC, "shell", "main.c"))
        self.assertIn('command_register("md8", "memory dump", md8_cmd);',
                      main)
        body = _body(main, "void md8_cmd(")
        self.assertIn("hexdump((void*)base, size);", body)
        # NO bounds/range validation exists between the host-supplied
        # address and the raw dereference:
        self.assertNotIn("kCacheableView", body)
        self.assertNotIn("loader_xfer_recv_data", body)
        self.assertNotIn("if (base + size", body)

    def test_hexdump_signed_mask_and_padding_term(self):
        main = _read(os.path.join(SRC, "shell", "main.c"))
        self.assertIn("0xFF & ((char*)mem)[i]", main)   # signed-char safe
        self.assertIn("(HEXDUMP_COLS - len % HEXDUMP_COLS)", main)
        # For the fixed 0x60 report the padding term is exactly zero,
        # so every row is full -- no truncation, no ambiguous cells.
        self.assertEqual(pr.RPT_SIZE % 16, 0)
        self.assertEqual(pr.RPT_SIZE, 0x60)

    def test_ep0_in_max_transfer_equals_ring_capacity(self):
        otg = _read(os.path.join(SRC, "drivers", "usb", "synopsys_otg.c"))
        self.assertIn("#define DMA_BUFFER_SIZE    (0x4000 / 4)", otg)
        self.assertEqual(0x4000 // 4, rr.STDOUT_READ_LEN_MAX)
        self.assertIn("if (ep0.setup_packet.wLength <= DMA_BUFFER_SIZE)",
                      otg)   # anything larger stalls the setup stage

    def test_ep0_in_serves_tail_and_kills_head(self):
        loader = _read(os.path.join(SRC, "shell", "usbloader.c"))
        self.assertIn("fetch_stdoutbuf(buf, &xferlen);", loader)
        self.assertIn("buf += xferlen - setup->wLength;", loader)
        self.assertIn("ep0_begin_data_in_stage(buf, xferlen", loader)

    def test_ring_producer_dies_with_preemption_over(self):
        io_c = _read(os.path.join(SRC, "kernel", "support", "io.c"))
        self.assertIn("case 1: if (preemption_over) file = 2;", io_c)
        entry = _read(os.path.join(SRC, "kernel", "entry.c"))
        # entry.c sets preemption_over three times: at pongo_entry_cached
        # (:126, early init), to 0 at scheduler start (:200), and to 1 in
        # the PRE-JUMP teardown block -- pin the teardown occurrence.
        usb = entry.index("usb_teardown();")
        self.assertLess(usb, entry.index("preemption_over = 1;", usb))

    def test_stdin_completion_wait_mechanism(self):
        loader = _read(os.path.join(SRC, "shell", "usbloader.c"))
        self.assertIn("if (should_wait_for_cmd_handler)", loader)
        self.assertIn("event_wait(&command_handler_iter);", loader)
        self.assertIn("should_wait_for_cmd_handler = 1;", loader)  # wValue 0
        cmd = _read(os.path.join(SRC, "shell", "command.c"))
        # Flag lifecycle: 0 while idle/reading, 1 only DURING execute --
        # polling 0 alone can be premature unless mode 0 was set first.
        self.assertLess(cmd.index("command_in_progress = 0;"),
                        cmd.index("fgets(command_buffer,512,stdin)"))
        self.assertLess(cmd.index("fgets(command_buffer,512,stdin)"),
                        cmd.index("command_in_progress = 1;"))

    def test_shell_death_precedes_teardown_and_jump(self):
        entry = _read(os.path.join(SRC, "kernel", "entry.c"))
        # Teardown only runs AFTER the scheduler loop exits on gBootFlag;
        # by then pongo_boot_raw has already task_yield()ed forever, so
        # md8 can never be issued again (first in-sequence death).
        loop = entry.index("while (!gBootFlag)")
        timer = entry.index("timer_disable();", loop)
        preempt = entry.index("preemption_over = 1;", timer)  # teardown
        jump = entry.index("jump_to_image_extended(", preempt)
        self.assertLess(loop, timer)
        self.assertLess(timer, preempt)
        self.assertLess(preempt, jump)

    def test_printf_reaches_the_stdout_ring(self):
        sip = os.path.join(REPO, "pongoos-src", "newlib",
                           "src", "newlib", "libc", "stdio", "siprintf.c")
        self.assertIn("int iprintf(const char", _read(sip))
        io_c = _read(os.path.join(SRC, "kernel", "support", "io.c"))
        self.assertIn("int _write(int file, char *ptr, int len)", io_c)
        self.assertIn("stdout_buf[stdout_buf_len++] = ptr[i];", io_c)


if __name__ == "__main__":
    print("NOTE: ALL TEST RESULTS BELOW ARE SIMULATED -- mock transport, "
          "synthetic memory, invented addresses; never hardware evidence.",
          flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)
