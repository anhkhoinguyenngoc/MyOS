#!/usr/bin/env python3
"""
MyOS M-P2.7 -- offline tests for tools/host/pongo_client.py.

Two halves:

A) Protocol tests against MockPongoDevice (SYNTHETIC backend):
   - upload/control framing matches usbloader.c ep0_device_request;
   - a live-shell `md8` readback of a SYNTHETIC report parses correctly;
   - invented "read-memory" IN requests and the documented-but-
     unimplemented KTRW vendor IN are EP0-stalled (closed request set,
     faithful to ep0_setup_stage -> ep_stall);
   - after the device enters teardown (models usb_teardown() post-`bootr`)
     every call raises PongoChannelGone: the executable proof that no
     post-handoff readback path exists in the model.

B) Handoff-channel SOURCE CONTRACT (parses the real pinned PongoOS source):
   proves, from the code itself, that after `bootr` no service remains
   that could read arbitrary report bytes back to the host.

C) Readback capability inventory (M-P2.7): enumerates, from source, every
   device->host byte path, proves the ep0 request table is a closed set
   with no host-addressed read, proves the KTRW IN path has zero callers,
   proves USB servicing requires the IRQ/task context that
   interrupt_teardown() removes, reviews why the probe cannot safely
   return control, and pins the primitives the smallest additional
   mechanism would need (identified, NOT implemented).

Usage:
  python3 tests/test_pongo_client.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import re
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "host", "pongo_client.py")

_spec = importlib.util.spec_from_file_location("pongo_client", TOOL)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

# short aliases used throughout
PongoClient = pc.PongoClient
MockPongoDevice = pc.MockPongoDevice
SparseMemory = pc.SparseMemory
parse_hexdump = pc.parse_hexdump
PongoChannelGone = pc.PongoChannelGone
PongoRequestStalled = pc.PongoRequestStalled
pr = pc.probe_report

SRC = os.path.join(REPO, "pongoos-src", "src")
ENTRY_C = os.path.join(SRC, "kernel", "entry.c")
JUMP_S = os.path.join(SRC, "boot", "jump_to_image.S")
OTG_C = os.path.join(SRC, "drivers", "usb", "synopsys_otg.c")
UART_C = os.path.join(SRC, "drivers", "uart", "uart.c")
LOWLEVEL_C = os.path.join(SRC, "kernel", "lowlevel.c")
SHELL_MAIN_C = os.path.join(SRC, "shell", "main.c")


USBLOADER_C = os.path.join(SRC, "shell", "usbloader.c")
TASK_C = os.path.join(SRC, "kernel", "task.c")
PROBE_S = os.path.join(REPO, "boot", "aarch64", "pongo_probe.S")


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _func_body(src, signature):
    """Return the text of the C function whose header starts with
    `signature`, up to the closing brace at column 0."""
    start = src.index(signature)
    end = src.index("\n}\n", start)
    return src[start:end]


def _synthetic_report(seed):
    """Build a SYNTHETIC 0x60 report with labelled non-zero dynamic slots."""
    rpt = bytearray(pr.RPT_SIZE)
    rpt[0:8] = pr.MAGIC_TXT
    struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_VER_OFF"], pr.RPT_VERSION)
    struct.pack_into("<I", rpt, pr.EQU_MIRROR["RPT_SIZE_OFF"], pr.RPT_SIZE)
    struct.pack_into("<Q", rpt, pr.EQU_MIRROR["RPT_X30_OFF"], 0x180510000 + seed)
    struct.pack_into("<Q", rpt, pr.EQU_MIRROR["RPT_EL_OFF"], 0x8)
    return bytes(rpt)


class TestClientProtocol(unittest.TestCase):
    def setUp(self):
        self.buf_va = 0x1000a9000          # arbitrary SYNTHETIC buffer VA
        self.rpt = _synthetic_report(0x20)
        mem = SparseMemory(limit=self.buf_va + 0x200 + pr.RPT_SIZE)
        mem.poke(self.buf_va + 0x200, self.rpt)
        self.dev = MockPongoDevice(memory=mem, alive=True)
        self.client = PongoClient(self.dev)

    def test_upload_framing_matches_protocol(self):
        self.client.upload(b"\xaa\xbb\xcc\xdd")
        # upload-init control OUT (0x21, bRequest 1, wLength 0) then bulk ep2
        self.assertEqual(self.dev.control_log[0],
                         (pc.BM_TYPE_OUT, pc.REQ_UPLOAD_INIT, 0, 0))
        self.assertEqual(self.dev.uploaded, b"\xaa\xbb\xcc\xdd")

    def test_send_command_framing(self):
        self.client.send_command("hello")
        log = self.dev.control_log[-1]
        self.assertEqual(log[0], pc.BM_TYPE_OUT)
        self.assertEqual(log[1], pc.REQ_STDIN)
        self.assertEqual(log[3], 6)              # "hello\n"
        # the stdin payload reached the command buffer AND was consumed
        # by the command handler (mock answers "command ignored").
        self.assertIn(b"ignored (hello)", self.dev.stdbuf,
                      "stdin payload must reach the command handler")

    def test_md8_live_shell_readback(self):
        # SYNTHETIC: mock RAM only. Works only because the shell is alive.
        text = self.client.md8(self.buf_va + 0x200, pr.RPT_SIZE)
        self.assertIn(b"0x", text)
        blob = parse_hexdump(text)
        self.assertEqual(blob, self.rpt)
        report = pr.parse_report(blob)
        self.assertEqual(report["magic"], "MP1PRB01")
        self.assertEqual(report["x30_entry"], 0x180510020)

    def test_read_report_via_md8_roundtrip(self):
        report, text = self.client.read_report_via_md8(self.buf_va)
        self.assertEqual(report["x30_entry"], 0x180510020)
        self.assertEqual(report["current_el"], 0x8)

    def test_read_report_via_md8_rejects_reserved_nonzero(self):
        """M-P2.45: legacy API must validate reserved_zero == 0, same as
        ReportReadback and probe_selfreport."""
        # Build a report with reserved_zero = 1 (all other fields valid)
        bad = bytearray(self.rpt)
        struct.pack_into("<Q", bad, pr.EQU_MIRROR["RPT_ZERO_OFF"], 1)
        mem = SparseMemory(limit=self.buf_va + 0x200 + pr.RPT_SIZE)
        mem.poke(self.buf_va + 0x200, bytes(bad))
        dev = MockPongoDevice(memory=mem, alive=True)
        client = PongoClient(dev)
        with self.assertRaises(ValueError) as cm:
            client.read_report_via_md8(self.buf_va)
        self.assertIn("reserved field", str(cm.exception))
        self.assertIn("must be zero", str(cm.exception))

    def test_parse_hexdump_ignores_address_columns(self):
        sample = (
            "0x000000000: " + " ".join("%02x" % b for b in self.rpt[:8])
            + "\n0x000000008: " + " ".join("%02x" % b for b in self.rpt[8:16])
            + "\n"
        )
        self.assertEqual(parse_hexdump(sample), self.rpt[:16])


class TestPostHandoffChannelGone(unittest.TestCase):
    def setUp(self):
        self.buf_va = 0x1000a9000
        self.dev = MockPongoDevice(memory=SparseMemory(
            limit=self.buf_va + 0x200 + pr.RPT_SIZE), alive=True)
        self.client = PongoClient(self.dev)

    def test_every_call_raises_after_teardown(self):
        self.dev.set_teardown()
        with self.assertRaises(PongoChannelGone):
            self.client.md8(self.buf_va, pr.RPT_SIZE)
        with self.assertRaises(PongoChannelGone):
            self.client.send_command("md8 0 0x20")
        with self.assertRaises(PongoChannelGone):
            self.client.upload(b"\x00\x01")
        with self.assertRaises(PongoChannelGone):
            self.client.read_stdout()
        with self.assertRaises(PongoChannelGone):
            self.client.read_report_via_md8(self.buf_va)


class TestClosedRequestSet(unittest.TestCase):
    """The mock's request table must be exactly the source's closed set;
    anything else is an EP0 stall, like the real device."""

    def setUp(self):
        self.dev = MockPongoDevice(memory=SparseMemory(limit=0x1000),
                                   alive=True)

    def test_invented_read_memory_in_request_stalls(self):
        with self.assertRaises(PongoRequestStalled):
            self.dev.control_in(pc.BM_TYPE_IN, 5, pr.RPT_SIZE)

    def test_ktrw_vendor_in_stalls_out_accepted(self):
        # IN direction: documented in the comment, NOT implemented.
        with self.assertRaises(PongoRequestStalled):
            self.dev.control_in(0xC1, pc.REQ_KTRW, 0x40,
                                w_index=pc.KTRW_WINDEX)
        # OUT direction: accepted (buffered in ktrw_recv_data, dropped).
        self.dev.control_out(pc.BM_VENDOR_OUT, pc.REQ_KTRW, 0, 0x40,
                             b"\x00" * 0x40, w_index=pc.KTRW_WINDEX)

    def test_known_requests_still_served(self):
        self.dev.control_out(pc.BM_TYPE_OUT, pc.REQ_UPLOAD_INIT, 0, 0)
        self.assertTrue(self.dev.armed)
        self.dev.control_out(pc.BM_TYPE_OUT, pc.REQ_STDOUT_MODE, 1, 0)
        self.assertEqual(self.dev.stdout_mode, 1)
        self.assertEqual(self.dev.control_in(pc.BM_TYPE_IN,
                                             pc.REQ_COMPLETE, 1), b"\x00")

    def test_out_of_range_wlength_stalls(self):
        # usbloader.c: stdin requires 1 <= wLength <= 512.
        with self.assertRaises(PongoRequestStalled):
            self.dev.control_out(pc.BM_TYPE_OUT, pc.REQ_STDIN, 0, 513,
                                 b"x" * 513)
        # stdout read requires wLength 512 or 0x1000.
        with self.assertRaises(PongoRequestStalled):
            self.dev.control_in(pc.BM_TYPE_IN, pc.REQ_STDOUT_READ, 64)


class TestHandoffSourceContract(unittest.TestCase):
    """Executable proof, parsed from the real PongoOS sources, that after
    `bootr` no service survives that could read report bytes back."""

    def test_entry_c_teardowns_before_raw_jump(self):
        src = _read(ENTRY_C)
        pos_timer = src.index("timer_disable();")
        pos_usb = src.index("usb_teardown();")
        # search AFTER usb_teardown: disable_interrupts() also appears
        # earlier in pongo_sched_tick(), not part of the teardown block
        pos_irq_off = src.index("disable_interrupts();", pos_usb)
        pos_serial = src.index("serial_teardown();")
        pos_intr = src.index("interrupt_teardown();")
        pos_jump = src.index("jump_to_image_extended(((uint64_t)loader_xfer_recv_data)")
        self.assertTrue(pos_timer < pos_usb < pos_irq_off,
                        "USB must be torn down before interrupts are masked")
        self.assertTrue(pos_usb < pos_serial < pos_intr < pos_jump,
                        "teardowns must all precede the RAW jump")
        # The shell writes nothing and nothing can run after the jump.
        self.assertIn("gBootFlag == BOOT_FLAG_RAW", src)

    def test_jump_to_image_ret_is_terminal(self):
        src = _read(JUMP_S)
        start = src.index("_jump_to_image_extended:")
        body = src[start:]
        body = body[:body.index(".global") if ".global" in body else len(body)]
        m30 = body.index("mov x30, x0")
        ret = body.index("ret")
        self.assertTrue(m30 < ret,
                        "x30 is overwritten with the image address before ret")
        # No branch/call back to PongoOS anywhere in the jump routine.
        for bad in ("blr", "bl ", "br x"):
            self.assertNotIn(bad, body)

    def test_usb_teardown_disables_otg_controller(self):
        src = _read(OTG_C)
        start = src.index("void usb_teardown() {")
        body = src[start:start + 400]
        self.assertIn("rGAHBCFG", body)
        self.assertIn("rDCTL", body)

    def test_serial_teardown_restores_console(self):
        src = _read(UART_C)
        start = src.index("void serial_teardown(void) {")
        body = src[start:start + 400]
        self.assertIn("rUCON0", body)
        self.assertIn("rULCON0", body)

    def test_wdt_enable_is_a_dead_stub(self):
        src = _read(LOWLEVEL_C)
        start = src.index("void wdt_enable()")
        end = src.index("#endif", start)
        body = src[start:end]
        self.assertIn("return;", body)
        self.assertTrue(body.index("return;") < body.index("#if 0"),
                        "wdt_enable returns before the #if 0 block -> no-op")

    def test_bootr_shell_command_yields_forever(self):
        src = _read(SHELL_MAIN_C)
        start = src.index("void pongo_boot_raw()")
        body = src[start:start + 300]
        self.assertIn("BOOT_FLAG_RAW;", body)
        self.assertIn("task_yield();", body)

    def test_iprintf_usage_after_serial_teardown_is_banned_by_comment(self):
        # entry.c: "[i]printf from here on out, only screen_puts" -- the
        # console is dead before the jump by its own contract.
        src = _read(ENTRY_C)
        self.assertIn("No [i]printf from here on out, only screen_puts", src)


class TestReadbackCapabilityInventory(unittest.TestCase):
    """M-P2.7: enumerate every device->host byte path from the pinned
    source and prove none can carry report bytes after the handoff."""

    def _ep0_body(self):
        return _func_body(_read(USBLOADER_C),
                          "bool ep0_device_request(")

    def test_ep0_request_table_is_closed_and_matches_client(self):
        body = self._ep0_body()
        # The only branch discriminators are bmRequestType 0x21 (class
        # OUT) and 0xA1 (class IN).  Anything else falls through to the
        # trailing `return false` -> EP0 stall.
        bms = re.findall(r"setup->bmRequestType == (0x[0-9A-Fa-f]+)",
                         body)
        self.assertEqual(sorted(set(bms)), ["0x21", "0xA1"])
        # The exact ordered bRequest sequence of the closed set:
        # OUT: 1 (wLength 0), 2, 3, 4, 1 (wLength 4); IN: 1, 2.
        reqs = [int(r) for r in re.findall(r"setup->bRequest == (\d+)",
                                           body)]
        self.assertEqual(reqs, [1, 2, 3, 4, 1, 1, 2])
        # The client's protocol constants must equal the same sets.
        self.assertEqual({pc.REQ_UPLOAD_INIT, pc.REQ_DISCARD,
                          pc.REQ_STDIN, pc.REQ_STDOUT_MODE},
                         {1, 2, 3, 4})
        self.assertEqual({pc.REQ_STDOUT_READ, pc.REQ_COMPLETE}, {1, 2})

    def test_no_in_request_sources_host_supplied_address(self):
        body = self._ep0_body()
        # Both DATA IN stages serve fixed device state (the stdout ring
        # copy and the command_in_progress flag) -- never a host-chosen
        # address, so there is no arbitrary-read primitive PRE-handoff.
        in_calls = re.findall(r"ep0_begin_data_in_stage\(([^,]+),", body)
        self.assertEqual(sorted(c.strip() for c in in_calls),
                         ["&inprog", "buf"])
        self.assertNotIn("setup->wIndex", body)
        # The memory-read shell commands (md8/peek/physdump) are not ep0
        # requests at all: they emit TEXT over iprintf -> stdout ring,
        # and only from a live shell task.
        main_src = _read(SHELL_MAIN_C)
        for cmd in ("void md8_cmd(", "void peek_cmd(",
                    "void phys_page_dump("):
            self.assertIn("iprintf", _func_body(main_src, cmd))

    def test_ktrw_in_path_is_push_only_with_zero_callers(self):
        # usb_write()/usb_write_commit() are the only producers of
        # device->host data on ep1 (0x81); they are defined and declared
        # but NOTHING in the pinned tree calls them, so even PRE-handoff
        # the KTRW IN path has no producer.
        otg = _read(OTG_C)
        usb_hdr = _read(os.path.join(SRC, "drivers", "usb", "usb.h"))
        self.assertIn("usb_write_commit()", otg)     # definition
        self.assertNotIn("usb_write_commit", usb_hdr)  # not even exported
        self.assertIn("size_t\nusb_write(const void *data", otg)
        # The IN transfer completes via the OTG EP interrupt handler.
        self.assertIn("void (*in_transfer_done)(void) = ep1.in_transfer_done;",
                      otg)
        self.assertIn("usb_ep_interrupt", otg)
        # The vendor ep0 request accepts OUT only (the documented KTRW
        # "IN 0x41" has no implementation as a fetch request).
        vendor = _func_body(otg, "ep0_vendor_request(struct setup_packet *setup) {")
        self.assertIn("(setup->bmRequestType & 0x80) == 0", vendor)
        self.assertIn("setup->bRequest == 0x41 && setup->wIndex == 0x1337",
                      vendor)

    def test_usb_servicing_requires_irq_task_context(self):
        otg = _read(OTG_C)
        # ep0 setup packets are processed only inside the EP interrupt
        # path, which runs the usb task (irq-preempt or cooperative).
        self.assertIn("success = ep0_setup_stage(&ep0.setup_packet);", otg)
        self.assertIn("task_register_preempt_irq(&usb_task, usb_main, usb_irq);",
                      otg)
        self.assertIn("task_register(&usb_task, usb_main);", otg)
        # interrupt_teardown() removes every IRQ handler before the jump.
        itd = _func_body(_read(LOWLEVEL_C), "void interrupt_teardown()")
        self.assertIn("wdt_disable();", itd)
        self.assertIn("task_irq_teardown();", itd)
        tid = _func_body(_read(TASK_C), "void task_irq_teardown()")
        self.assertIn("mask_interrupt(i)", tid)

    def test_usb_teardown_single_call_site_is_entry_c(self):
        # usb_teardown() has exactly one call site in the tree: the
        # pre-jump teardown in entry.c.  Nothing re-inits USB afterwards.
        sites = []
        for root, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith((".c", ".h")):
                    continue
                path = os.path.join(root, fn)
                if re.search(r"\busb_teardown\s*\(\s*\)", _read(path)):
                    sites.append(os.path.relpath(path, SRC))
        self.assertEqual(sorted(sites),
                         ["drivers/usb/synopsys_otg.c",
                          "drivers/usb/usb.h",
                          "kernel/entry.c"])

    def test_stall_semantics_match_device_source(self):
        # ep0_out_interrupt(): a setup packet rejected by
        # ep0_setup_stage() stalls the corresponding EP0 direction --
        # the behaviour PongoRequestStalled models.
        body = _func_body(_read(OTG_C),
                          "static void\nep0_out_interrupt() {")
        self.assertIn("The SETUP stage failed, so stall", body)
        self.assertIn("ep_stall(&ep0_in);", body)


class TestProbeCannotReturnControl(unittest.TestCase):
    """Task-6 review, executable: the probe has no safe way to return
    control or to publish its report, so the existing park behaviour
    (unchanged) is the correct one."""

    def test_jump_clobbers_lr_and_zeroes_arg_regs(self):
        src = _read(JUMP_S)
        start = src.index("_jump_to_image_extended:")
        end = src.index(".global _tramp_hook")
        body = src[start:end]
        self.assertIn("mov x30, x0", body)   # LR <- image base
        self.assertIn("mov x0, x1", body)    # x0 <- gBootArgs
        self.assertIn("mov x8, x2", body)    # x8 <- gEntryPoint
        for reg in ("x1", "x2", "x3"):
            self.assertIn("mov %s, xzr" % reg, body)
        # No register retains a pongo_entry return address, and the
        # routine contains no indirect control transfer: a hypothetical
        # probe `ret` would target x30 = the image base = probe entry,
        # i.e. re-enter the probe -- not return to pongo_entry.
        self.assertNotIn("blr", body)
        self.assertNotIn("br ", body)

    def test_exit_to_el1_image_is_unreachable_after_raw_jump(self):
        src = _read(ENTRY_C)
        jump = src.index(
            "jump_to_image_extended(((uint64_t)loader_xfer_recv_data)")
        exit_call = src.index(
            "exit_to_el1_image((void*)gBootArgs, gEntryPoint);")
        self.assertTrue(jump < exit_call)
        # USB and serial are destroyed before the jump regardless, so
        # even a hypothetical return could not publish report bytes.
        self.assertTrue(src.index("usb_teardown();") < jump)
        self.assertTrue(src.index("serial_teardown();") < jump)

    def test_probe_obeys_no_stack_no_link_invariants(self):
        probe = _read(PROBE_S)
        code = re.sub(r"/\*.*?\*/", "", probe, flags=re.S)
        code = re.sub(r"//.*", "", code)
        self.assertIsNone(re.search(r"\bsp\b", code),
                          "probe must not assume a valid stack")
        self.assertIsNone(re.search(r"^\s*bl\b", code, re.M),
                          "probe must not clobber x30 (image base)")


class TestSmallestMechanismPrimitives(unittest.TestCase):
    """Identify -- do NOT implement -- the smallest additional mechanism
    that could retrieve the report: a device-sourced class IN request
    kept serviceable across the jump.  Every primitive it would use must
    exist in the pinned source today; if the pinned source drifts, these
    checks fail and the proposal must be re-derived."""

    def test_device_side_primitives_exist(self):
        otg = _read(OTG_C)
        loader = _read(USBLOADER_C)
        # 1. The extension point: class SETUPs dispatch to
        #    ep0_device_request (synopsys_otg.c ep0_setup_stage).
        self.assertIn("rv = ep0_device_request(setup);", otg)
        # 2. The DATA IN primitive already used by the stdout read can
        #    serve any device-side buffer; ep0 DMA room >> RPT_SIZE.
        self.assertIn("ep0_begin_data_in_stage(buf, xferlen,",
                      self._ep0_body(loader))
        m = re.search(r"#define\s+DMA_BUFFER_SIZE\s+\(0x4000 / 4\)", otg)
        self.assertIsNotNone(m)
        self.assertGreater(0x4000 // 4, pr.RPT_SIZE)
        # 3. The upload buffer (which holds the report at +0x200) is a
        #    device-side global that survives `bootr` (not freed), and
        #    entry.c itself dereferences it at jump time.
        self.assertIn("uint8_t * loader_xfer_recv_data;", loader)
        self.assertIn("loader_xfer_recv_data) - kCacheableView",
                      _read(ENTRY_C))
        equ = pr.equ_constants_from_source(_read(PROBE_S))
        self.assertEqual(equ["RPT_OFF"], 0x200)
        self.assertEqual(equ["RPT_SIZE"], pr.RPT_SIZE)

    def test_only_missing_capability_is_post_jump_servicing(self):
        # The precise blocker: after the RAW jump there is NO execution
        # context left to service the OTG interrupt.  PongoOS exits its
        # scheduler loop on gBootFlag and masks interrupts; the probe
        # itself masks interrupts and parks.  Both sides are pinned here
        # so any future change fails loudly and forces re-derivation.
        entry = _read(ENTRY_C)
        self.assertIn("while (!gBootFlag)", entry)
        self.assertIn("disable_interrupts();", entry)
        probe = _read(PROBE_S)
        self.assertIn("msr     daifset, #0xf", probe)
        self.assertRegex(probe, r"park:\s*\n\s*b\s+park")

    @staticmethod
    def _ep0_body(loader):
        return _func_body(loader, "bool ep0_device_request(")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)