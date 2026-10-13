#!/usr/bin/env python3
"""
MyOS M-P2.7 -- Host-side PongoOS shell client (protocol layer, OFFLINE).

Implements the device-side contract of pongoos-src/src/shell/usbloader.c
(`ep0_device_request`) seen from the HOST:

  * control OUT, bmRequestType 0x21 (host->device, class, interface):
        bRequest 1 : upload-init       (arms bulk OUT; usbloader.c)
        bRequest 2 : discard           (drop uploaded data)
        bRequest 3 : stdin             (wLength 1..512, OUT data stage)
        bRequest 4 : stdout mode       (wValue 0/1/0xffff blocking modes)
  * control IN,  bmRequestType 0xA1 (device->host, class, interface):
        bRequest 1 : read stdout       (wLength 512 or 0x1000, IN stage)
        bRequest 2 : command completion check
  * bulk OUT endpoint 2: the raw upload payload
        (usb_out_transfer_dma(2, ...) in usbloader.c)

The transport is injectable so the protocol can be exercised OFFLINE
against a mock.  No real transport (libusb/pyusb) is implemented here:
the environment forbids package installation and has no device access, so
a hardware transport would be dead, speculative code.  The device side of
the mock is faithful to `ep0_device_request` and to the `md8`/`hexdump`
behaviour of src/shell/main.c (md8_cmd: `md8 [base] [size]`, default size
0x20; hexdump: `0x%09llx: %02x ...` in rows of HEXDUMP_COLS=16).

WHY POST-`bootr` READBACK IS IMPOSSIBLE (source evidence, M-P2.7)
-----------------------------------------------------------------
  src/kernel/entry.c (`pongo_entry_cached`):
      timer_disable(); usb_teardown(); disable_interrupts();
      preemption_over = 1;  ...
      serial_teardown(); interrupt_teardown(); ...
  src/kernel/entry.c (`pongo_entry`):
      if(gBootFlag == BOOT_FLAG_RAW)
          jump_to_image_extended(loader_xfer_recv_data phys, gBootArgs, gEntryPoint);
  src/boot/jump_to_image.S (`_jump_to_image_extended`):
      mov x30, x0    ; x30 OVERWRITTEN with the image buffer address
      ...
      ret            ; one-way tail jump; returning would re-enter the image
  src/drivers/usb/synopsys_otg.c (`usb_teardown`):
      reg_write(rGAHBCFG, 0x2e); reg_or(rDCTL, 0x2);   // OTG controller off
  src/drivers/uart/uart.c (`serial_teardown`):
      rUCON0/rULCON0/rUFCON0/rUMCON0 = iBoot values   // console restored
  src/kernel/lowlevel.c:
      wdt_enable() is a dead stub (`return;` before `#if 0`) and
      interrupt_teardown() calls wdt_disable() -> no watchdog reboot path  src/shell/main.c (`pongo_boot_raw`, the `bootr` command):
      gBootFlag = BOOT_FLAG_RAW; task_yield();        // shell never rescheduled

Consequently md8()/read_report_via_md8() here can only ever succeed while
the PongoOS shell is ALIVE (pre-handoff), at which moment the report's
dynamic slots have not been recorded yet.  After the jump the bus state
is "device gone": the MockPongoDevice models that as an explicit teardown and
every call raises PongoChannelGone, exactly like a real host observes.

WHY NO HIDDEN IN REQUEST EXISTS (closed-set contract, M-P2.7)
-------------------------------------------------------------
  src/shell/usbloader.c (`ep0_device_request`) is a closed if/else on
  (bmRequestType, bRequest, wLength); the ONLY IN (0xA1) requests are
  bRequest 1 (stdout ring via fetch_stdoutbuf) and bRequest 2 (the
  1-byte command_in_progress flag).  No IN request takes a device
  address from the host, so there is no arbitrary-read primitive even
  PRE-handoff.  Per src/drivers/usb/synopsys_otg.c (`ep0_out_interrupt`)
  any setup packet for which `ep0_setup_stage()` returns false is
  answered with `ep_stall(&ep0_in)` -- the mock raises
  PongoRequestStalled for exactly the requests the source does not
  implement, including the documented-but-unimplemented KTRW vendor IN
  (0xC1 bRequest 0x41 wIndex 0x1337: `ep0_vendor_request` handles the
  OUT direction only; KTRW IN data is PUSHed on ep1 0x81 by
  `usb_write_commit()`, whose only callers would be live PongoOS code --
  there are ZERO call sites in the pinned tree).  All of ep0/ep1
  servicing runs in the usb task / OTG IRQ context (`usb_main` via
  `task_register_preempt_irq`, synopsys_otg.c:1859-1862), which
  `interrupt_teardown()` -> `task_irq_teardown()` masks entirely
  (kernel/task.c) before the jump.

Usage:
  python3 tools/host/pongo_client.py --selfcheck   # offline mock self-check
"""

import os
import re
import sys

_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import probe_report  # noqa: E402  (report parser, same tool directory)


class PongoChannelGone(Exception):
    """The USB/console channel is not available (post-`bootr` bus state)."""


class PongoRequestStalled(Exception):
    """EP0 stalled: `ep0_setup_stage()` returned false for this
    (bmRequestType, bRequest, wValue, wIndex, wLength) tuple, so the
    device stalls the endpoint (synopsys_otg.c `ep0_out_interrupt`:
    "The SETUP stage failed, so stall ...").  There is no request that
    reads device memory."""


# -- protocol constants (device side: pongoos-src/src/shell/usbloader.c) --
BM_TYPE_OUT = 0x21   # 0=host->device, 01=class, 01=interface
BM_TYPE_IN  = 0xA1   # 1=device->host, 01=class, 01=interface
REQ_UPLOAD_INIT = 1
REQ_DISCARD     = 2
REQ_STDIN       = 3
REQ_STDOUT_MODE = 4
REQ_STDOUT_READ = 1   # on BM_TYPE_IN
REQ_COMPLETE    = 2   # on BM_TYPE_IN
EP_BULK_OUT     = 0x02
# KTRW vendor request (synopsys_otg.c ep0_vendor_request): only the OUT
# direction (host->device) is implemented; the IN direction documented
# in the comment above it does NOT exist as an ep0 request.
BM_VENDOR_OUT   = 0x41   # host->device, vendor, interface
REQ_KTRW        = 0x41
KTRW_WINDEX     = 0x1337
STDOUT_READ_LEN_MAX = 0x1000
STDIN_MAX           = 512
MD8_DEFAULT_SIZE    = 0x20


class Transport:
    """Abstract USB-class transport. A real implementation would call
    libusb/pyusb; the offline tests use MockPongoDevice."""

    def control_out(self, bm_request_type, b_request, w_value=0,
                    w_length=0, data=b""):
        raise NotImplementedError

    def control_in(self, bm_request_type, b_request, w_length):
        raise NotImplementedError

    def bulk_out(self, ep_addr, data):
        raise NotImplementedError


class SparseMemory:
    """Sparse model of device RAM for the mock.

    Only explicitly poked addresses are materialised, so high synthetic
    buffer VAs (e.g. 0x1000a9000) do not require multi-GB flat
    allocations.  Reads outside any poked range return 0, and __len__
    reports the logical limit, exactly like the flat bytearray model
    this replaces.
    """

    def __init__(self, limit=0):
        self.limit = limit
        self._cells = {}

    def poke(self, addr, data):
        for i, b in enumerate(data):
            self._cells[addr + i] = b

    def __len__(self):
        return self.limit

    def __getitem__(self, idx):
        return self._cells.get(idx, 0)


class MockPongoDevice(Transport):
    """Offline stand-in for the device side of the PongoOS USB contract.

    SYNTHETIC: it models the protocol framing of usbloader.c and an
    in-memory 'device RAM' image.  It is NOT hardware evidence; it exists
    so the real client protocol code can be exercised deterministically.

    The request table is the CLOSED SET implemented by
    `ep0_device_request` (usbloader.c) plus the OUT half of the KTRW
    vendor request (`ep0_vendor_request`, synopsys_otg.c).  Anything
    else raises PongoRequestStalled, mirroring the device's
    `ep_stall(&ep0_in)` on a failed setup stage.  In particular there is
    no IN request that returns device memory: the two 0xA1 requests
    serve only the stdout ring and the command_in_progress flag.
    """

    HEXDUMP_COLS = 16

    def __init__(self, memory=b"", alive=True):
        self.alive = alive
        self.memory = memory if isinstance(memory, SparseMemory) \
            else bytearray(memory)
        self.stdbuf = bytearray()      # device->host stdout queue
        self.uploaded = bytearray()    # bulk payload capture
        self.armed = False
        self.stdout_mode = None
        self.control_log = []          # (bm, breq, wvalue, wlength)
        self._cmd = b""

    # -- device-side behaviour ----------------------------------------
    def set_teardown(self):
        """Model post-`bootr` state: usb_teardown() disabled the OTG
        controller, so the device simply vanishes from the bus."""
        self.alive = False
        self.stdbuf.clear()

    # stdout ring capacity: STDOUT_BUFLEN - 1 (pongo.h:470, io.c:88-101).
    RING_CAP = 0x1000 - 1

    def _ring_write(self, data):
        """Append to the stdout ring exactly like io.c `_write` in the
        DEFAULT mode (stdout_blocking starts false, io.c:43): when the
        ring is full the OLDEST bytes are discarded (io.c:97-101,
        `else // non-blocking = discard` + memmove shift)."""
        self.stdbuf += data
        overflow = len(self.stdbuf) - self.RING_CAP
        if overflow > 0:
            del self.stdbuf[:overflow]

    def _process_command(self, cmd):
        text = cmd.decode("ascii", "replace").strip()
        if text.startswith("md8"):
            # md8 [base] [size] ; base hex via strtoull(args, NULL, 16)
            toks = text.split()
            base = int(toks[1], 16) if len(toks) > 1 else 0
            size = int(toks[2], 16) if len(toks) > 2 else MD8_DEFAULT_SIZE
            if not base or not size:
                self._ring_write(b"md8 usage: md8 [base] [size]\n")
                return
            line = bytearray()
            for row in range(0, size, self.HEXDUMP_COLS):
                # C printf "0x%09llx: " rendered in Python (same shape)
                line += ("0x%09x: " % (base + row)).encode()
                for col in range(self.HEXDUMP_COLS):
                    idx = row + col
                    if idx < size:
                        val = self.memory[base + idx] \
                            if base + idx < len(self.memory) else 0
                        line += ("%02x " % val).encode()
                    else:
                        line += b"   "
                line += b"\n"
            self._ring_write(bytes(line))
        else:
            self._ring_write(b"mock: command ignored (" + cmd.strip() + b")\n")

    # -- host-facing transport API -------------------------------------
    def _stall(self, bm_request_type, b_request, w_value, w_index,
               w_length):
        raise PongoRequestStalled(
            "stall: setup (bm=0x%02x bRequest=%d wValue=0x%x wIndex=0x%x "
            "wLength=%d) not in ep0_device_request closed set"
            % (bm_request_type, b_request, w_value, w_index, w_length))

    def control_out(self, bm_request_type, b_request, w_value=0,
                    w_length=0, data=b"", w_index=0):
        if not self.alive:
            raise PongoChannelGone("control OUT after teardown")
        self.control_log.append((bm_request_type, b_request, w_value, w_length))
        if bm_request_type == BM_TYPE_OUT:
            if b_request == REQ_UPLOAD_INIT:
                if w_length == 0:            # arm bulk OUT (usbloader.c)
                    self.armed = True
                    return
                if w_length == 4:            # resize upload buffer
                    self.armed = True        # reallocate re-arms the xfer
                    return
            elif b_request == REQ_DISCARD and w_length == 0:
                self.uploaded.clear()
                self.armed = False
                return
            elif b_request == REQ_STDIN and 1 <= w_length <= STDIN_MAX:
                self._cmd += data[:w_length]
                if b"\n" in self._cmd:
                    cmd, _, rest = self._cmd.partition(b"\n")
                    self._cmd = rest
                    self._process_command(cmd)
                return
            elif b_request == REQ_STDOUT_MODE and w_value in (0, 1, 0xffff):
                self.stdout_mode = w_value
                return
        elif bm_request_type == BM_VENDOR_OUT and b_request == REQ_KTRW \
                and w_index == KTRW_WINDEX:
            # ep0_vendor_request accepts the KTRW OUT (host->device)
            # direction only; the device buffers the payload in
            # ktrw_recv_data and nothing in the pinned tree ever sends
            # it back (usb_read has no callers either).  Modelled as
            # accepted-and-dropped.
            return
        self._stall(bm_request_type, b_request, w_value, w_index, w_length)

    def control_in(self, bm_request_type, b_request, w_length,
                   w_index=0, w_value=0):
        if not self.alive:
            raise PongoChannelGone("control IN after teardown")
        self.control_log.append((bm_request_type, b_request, 0, w_length))
        if bm_request_type == BM_TYPE_IN and b_request == REQ_STDOUT_READ \
                and w_length in (512, STDOUT_READ_LEN_MAX):
            # usbloader.c:151-159 -- fetch_stdoutbuf() drains the WHOLE
            # ring (destructive, io.c:55-60) into stdoutbuf_copy; if the
            # content is larger than wLength only the TAIL is served
            # (`buf += xferlen - setup->wLength`) -- the head is
            # PERMANENTLY lost, not deferred.
            data = bytes(self.stdbuf)
            self.stdbuf.clear()
            if len(data) > w_length:
                return data[len(data) - w_length:]
            return data
        if bm_request_type == BM_TYPE_IN and b_request == REQ_COMPLETE \
                and w_length == 1:
            return b"\x00"  # command handler idle
        # Everything else -- including the documented-but-unimplemented
        # KTRW vendor IN (0xC1 bRequest 0x41 wIndex 0x1337) and any
        # invented "read memory" request -- is stalled, exactly like
        # ep0_setup_stage() returning false stalls EP0 IN.
        self._stall(bm_request_type, b_request, w_value, w_index, w_length)

    def bulk_out(self, ep_addr, data):
        if not self.alive:
            raise PongoChannelGone("bulk OUT after teardown")
        if self.armed and ep_addr == EP_BULK_OUT:
            self.uploaded += data


class PongoClient:
    """Host-side PongoOS shell client over an injectable Transport."""

    def __init__(self, transport):
        self.t = transport

    # -- low-level protocol --------------------------------------------
    def upload_init(self):
        self.t.control_out(BM_TYPE_OUT, REQ_UPLOAD_INIT, 0, 0, b"")

    def discard(self):
        self.t.control_out(BM_TYPE_OUT, REQ_DISCARD, 0, 0, b"")

    def set_stdout_mode(self, w_value):
        """bRequest 4 (usbloader.c:123-141).

        wValue 0 sets should_wait_for_cmd_handler=1 so the NEXT stdin
        write event-waits until the command handler finished
        (usbloader.c:51,60-61) -- the source-supported way for a host to
        know a command completed without racing command_in_progress
        (command.c:153-155: the flag is 0 while idle/in fgets).
        """
        self.t.control_out(BM_TYPE_OUT, REQ_STDOUT_MODE, w_value, 0, b"")

    def send_command(self, cmd):
        payload = cmd.encode() if isinstance(cmd, str) else bytes(cmd)
        payload = payload.rstrip(b"\n") + b"\n"
        if b"\x00" in payload:
            # usbloader.c usb_write_stdin breaks the FIFO at NUL
            raise ValueError("command contains NUL; device stdin breaks at NUL")
        if len(payload) > STDIN_MAX:
            raise ValueError("command exceeds %d-byte stdin stage" % STDIN_MAX)
        self.t.control_out(BM_TYPE_OUT, REQ_STDIN, 0, len(payload), payload)

    def read_stdout(self, maxlen=STDOUT_READ_LEN_MAX):
        return self.t.control_in(BM_TYPE_IN, REQ_STDOUT_READ, maxlen)

    def check_complete(self):
        return self.t.control_in(BM_TYPE_IN, REQ_COMPLETE, 1) != b"\x00"

    def drain_stdout(self, max_rounds=16):
        out = bytearray()
        for _ in range(max_rounds):
            chunk = self.read_stdout()
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def upload(self, data, chunk=0x4000):
        self.upload_init()
        for i in range(0, len(data), chunk):
            self.t.bulk_out(EP_BULK_OUT, data[i:i + chunk])

    # -- readback helpers (only valid while the shell is ALIVE) --------
    def md8(self, base, length=MD8_DEFAULT_SIZE):
        self.send_command("md8 0x%x 0x%x" % (base, length))
        return self.drain_stdout()

    def read_report_via_md8(self, buffer_va, rpt_off=probe_report.RPT_OFF,
                            rpt_size=probe_report.RPT_SIZE):
        """Attempt a report readback through md8.

        This only ever succeeds while PongoOS is running its live shell
        (pre-handoff).  After `bootr` the device is gone (usb_teardown)
        and PongoChannelGone is raised -- that IS the proof that no
        post-handoff readback path exists.
        """
        text = self.md8(buffer_va + rpt_off, rpt_size)
        blob = parse_hexdump(text)
        return probe_report.parse_report(blob), text


# ---------------------------------------------------------------------------
# md8 stdout parsing (device format: main.c hexdump, HEXDUMP_COLS=16)
#   0x000000000: 4d 50 31 50 52 42 30 31 01 00 00 00 60 00 00 00
# ---------------------------------------------------------------------------
_HEX_BYTE = re.compile(r"^(?:0x)?[0-9a-fA-F]{2}$")


def parse_hexdump(text):
    """Extract the raw bytes from md8-style hexdump text.

    Handles the exact device framing (`0x%09llx: ` row prefix + `%02x `
    octets; padding cells are three spaces).  The row address is ignored.
    Accepts str or bytes (stdout arrives as bytes from the transport).
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("ascii", "replace")
    out = bytearray()
    for raw in text.splitlines():
        line = raw.strip()
        # strip the "0x%09llx: " row prefix (or a bare "HEXADDR: ")
        line = re.sub(r"^(0x)?[0-9a-fA-F]+:\s*", "", line)
        for tok in line.split():
            if _HEX_BYTE.match(tok):
                out.append(int(tok, 16))
    return bytes(out)


# ---------------------------------------------------------------------------
# CLI self-check (offline; no hardware involved)
# ---------------------------------------------------------------------------

def _selfcheck():
    # SYNTHETIC report + a mock device; labelled clearly, never device evidence.
    rpt = bytearray(probe_report.RPT_SIZE)
    rpt[0:8] = probe_report.MAGIC_TXT
    import struct
    struct.pack_into("<I", rpt, probe_report.EQU_MIRROR["RPT_VER_OFF"],
                     probe_report.RPT_VERSION)
    struct.pack_into("<I", rpt, probe_report.EQU_MIRROR["RPT_SIZE_OFF"],
                     probe_report.RPT_SIZE)
    struct.pack_into("<Q", rpt, probe_report.EQU_MIRROR["RPT_X30_OFF"], 0x180510000)

    BUFFER_VA = 0x1000a9000   # arbitrary synthetic VA
    mem = SparseMemory(limit=BUFFER_VA + 0x200 + probe_report.RPT_SIZE)
    mem.poke(BUFFER_VA + 0x200, bytes(rpt))

    dev = MockPongoDevice(memory=mem, alive=True)
    client = PongoClient(dev)

    # 1) upload framing
    client.upload(b"\xaa\xbb\xcc\xdd")
    assert dev.uploaded == b"\xaa\xbb\xcc\xdd", "upload capture mismatch"

    # 2) live-shell report readback (SYNTHETIC capture, mock only)
    report, text = client.read_report_via_md8(BUFFER_VA)
    assert report["magic"] == "MP1PRB01", "magic mismatch"
    assert report["x30_entry"] == 0x180510000, "x30 slot mismatch"

    # 3) invented "read memory" IN request -> EP0 stall (closed set)
    try:
        dev.control_in(BM_TYPE_IN, 5, 0x60)
    except PongoRequestStalled:
        pass
    else:
        assert False, "invented IN request must stall (no arbitrary read)"

    # 4) documented-but-unimplemented KTRW vendor IN -> stall;
    #    the KTRW vendor OUT half is accepted (and dropped) instead.
    try:
        dev.control_in(0xC1, REQ_KTRW, 0x40, w_index=KTRW_WINDEX)
    except PongoRequestStalled:
        pass
    else:
        assert False, "KTRW vendor IN is not implemented in the source"
    dev.control_out(BM_VENDOR_OUT, REQ_KTRW, 0, 0x40,
                    b"\x00" * 0x40, w_index=KTRW_WINDEX)  # accepted

    # 5) post-teardown: the channel is gone -> the proof of the blocker
    dev.set_teardown()
    try:
        client.md8(BUFFER_VA, probe_report.RPT_SIZE)
    except PongoChannelGone:
        print("PongoClient: OFFLINE SELF-CHECK PASS")
        print("  - upload/control framing matches usbloader.c protocol")
        print("  - live-shell md8 readback parses a (SYNTHETIC) report")
        print("  - invented/KTRW-vendor IN requests are EP0-stalled:")
        print("    no IN request in the closed set returns device memory")
        print("  - after usb_teardown all calls raise PongoChannelGone")
        print("    => no source-supported post-`bootr` report readback path")
        return 0
    assert False, "expected PongoChannelGone after teardown"


def main(argv=None):
    sys.path.insert(0, _HOST_DIR)
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--selfcheck":
        return _selfcheck()
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the offline mock self-check")
    ap.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())