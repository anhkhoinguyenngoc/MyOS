#!/usr/bin/env python3
"""
MyOS M-P2.10 -- source-contract tests for the minimal post-handoff
report-readback design.

OFFLINE DESIGN VALIDATION ONLY: no hardware, no USB, no probe rebuild,
no modification of pongoos-src.  These tests pin, against the pinned
source, (a) every PRIMITIVE the chosen design relies on, (b) the exact
BLOCKER stack that makes the current implementation unable to service
USB after handoff, and (c) the inventory of PATCH POINTS (statements
that exist today, unguarded).  Passing here validates the design against
source -- it does NOT prove device behavior.

Chosen design (Design A, "self-served report"):
  * Blocker A (runtime buffer address) is removed BY CONSTRUCTION: a new
    class-IN ep0 request would serve bytes from the device-side global
    `loader_xfer_recv_data + 0x200` via `ep0_begin_data_in_stage()`;
    the host never needs the address.
  * Blocker B (post-handoff servicing) is a five-deep stack, each layer
    pinned below: teardown, scheduler-only dispatch, MMU/VBAR off, DAIF
    masked twice (entry.c + probe park), CPU parked in probe code.
  * The design keeps the probe's `b park` untouched as the CPU owner and
    services EP0 from EXCEPTION CONTEXT (`usb_handler()` is
    register-only), which needs: 4 conditionalizations in entry.c, a
    context change for USB dispatch, and ONE probe edit
    (`daifset #0xf` -> IRQ-open) -- the probe edit is documented, NOT
    applied (constraint: no probe rebuild without proven defect).

Usage:
  python3 tests/test_readback_design.py
Exit 0 = all pass, non-zero = failure.
"""

import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "pongoos-src", "src")
PROBE_S = os.path.join(REPO, "boot", "aarch64", "pongo_probe.S")

ENTRY_C = os.path.join(SRC, "kernel", "entry.c")
TASK_C = os.path.join(SRC, "kernel", "task.c")
LOWLEVEL_C = os.path.join(SRC, "kernel", "lowlevel.c")
MM_C = os.path.join(SRC, "kernel", "mm.c")
OTG_C = os.path.join(SRC, "drivers", "usb", "synopsys_otg.c")
USBLOADER_C = os.path.join(SRC, "shell", "usbloader.c")
GPIO_C = os.path.join(SRC, "drivers", "gpio", "gpio.c")
MODLOAD_C = os.path.join(SRC, "dynamic", "modload.c")


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _body(src, signature, end="\n}\n"):
    start = src.index(signature)
    return src[start:src.index(end, start)]


class TestServePrimitive(unittest.TestCase):
    """Design A serves the report from the device-side global: the exact
    primitive that must exist (and does) is `ep0_begin_data_in_stage`
    copying an arbitrary device buffer into the EP0 DMA buffer."""

    def test_data_in_stage_copies_arbitrary_buffer(self):
        otg = _read(OTG_C)
        # match the DEFINITION (the forward declaration at :201 ends
        # with ');' and must not be picked up)
        body = _body(otg, "ep0_begin_data_in_stage(const void *data, "
                          "uint32_t size, void (*callback)(void)) {")
        self.assertIn("if (size > ep0.setup_packet.wLength)", body)
        self.assertIn("memcpy(ep0_in.default_xfer_dma_data, data, size);",
                      body)
        self.assertIn("BUG(0x64696e32626967)", body)   # 'din2big' guard

    def test_ep0_dma_buffer_fits_the_0x60_report(self):
        otg = _read(OTG_C)
        self.assertIn("#define DMA_BUFFER_SIZE    (0x4000 / 4)", otg)
        self.assertIn("ep0_in .default_xfer_dma_size = DMA_BUFFER_SIZE;",
                      otg)
        self.assertGreater(0x4000 // 4, 0x60)

    def test_closed_set_dispatch_point_is_where_the_branch_goes(self):
        otg = _read(OTG_C)
        self.assertIn("rv = ep0_device_request(setup);", otg)
        # The new branch lives inside usbloader.c, which file-locally
        # owns the buffer pointer -- no host-supplied address anywhere.
        loader = _read(USBLOADER_C)
        self.assertIn("uint8_t * loader_xfer_recv_data;", loader)
        self.assertIn("bool ep0_device_request(struct setup_packet *setup)",
                      loader)

    def test_cache_invalidate_primitive_has_a_live_call_site(self):
        # The probe wrote the report into DRAM; the serving side must be
        # able to drop stale cache lines first.  Primitive + precedent:
        loader = _read(USBLOADER_C)
        self.assertIn(
            "cache_invalidate(loader_xfer_recv_data, loader_xfer_recv_count);",
            loader)   # usbloader_xfer_done_cb, live code


class TestBlockerStack(unittest.TestCase):
    """Every layer that currently prevents post-handoff servicing."""

    def test_blocker_mmu_disabled_before_jump(self):
        mm = _read(MM_C)
        body = _body(mm, "void lowlevel_cleanup(void)")
        self.assertIn("cache_clean_and_invalidate((void*)ram_phys_off,",
                      body)
        self.assertIn("disable_mmu_el1();", body)
        ll = _read(LOWLEVEL_C)
        self.assertIn('"_disable_mmu_el1:\\n"', ll)
        self.assertIn('"    and x3, x3, #(~1)\\n"', ll)   # clear SCTLR.M
        self.assertIn('"    and x3, x3, #(~4)\\n"', ll)   # clear SCTLR.C

    def test_blocker_vectors_live_at_a_code_va(self):
        # VBAR is a linked code address; with the MMU off it is not a
        # physical address, so no PongoOS handler can be entered.
        mm = _read(MM_C)
        self.assertIn("set_vbar_el1((uint64_t)&exception_vector);", mm)

    def test_blocker_usb_dispatch_defers_to_the_scheduler(self):
        task = _read(TASK_C)
        self.assertIn(
            "task_link(new); // preempting IRQ handlers aren't switched "
            "into immediately, but on next scheduler run", task)
        self.assertIn("task_set_sched_head(new);", task)
        otg = _read(OTG_C)
        # USB is registered as a PREEMPT irq task -> deferred -> needs
        # pongo_sched_tick(), which only runs inside `while (!gBootFlag)`.
        self.assertIn("task_register_preempt_irq(&usb_task, usb_main, "
                      "usb_irq);", otg)
        entry = _read(ENTRY_C)
        self.assertIn("char sched_ret = pongo_sched_tick();", entry)
        self.assertLess(entry.index("while (!gBootFlag)"),
                        entry.index("timer_disable();"))

    def test_blocker_daif_masked_by_entry_then_by_probe(self):
        entry = _read(ENTRY_C)
        # disable_interrupts() also appears earlier (entry.c:83, :99);
        # pin the teardown-block occurrence after usb_teardown().
        self.assertLess(entry.index("usb_teardown();"),
                        entry.index("disable_interrupts();",
                                    entry.index("usb_teardown();")))
        probe = _read(PROBE_S)
        # Probe masks ALL of DAIF while parked (design requires an
        # IRQ-open variant; probe binary intentionally NOT modified).
        self.assertIn("msr     daifset, #0xf", probe)
        self.assertIn("park:", probe)

    def test_blocker_probe_owns_the_cpu_indefinitely(self):
        probe = _read(PROBE_S)
        start = probe.index("park:")
        self.assertIn("b       park", probe[start:start + 80])
        # No way back: the jump contract clobbers LR (M-P2.9 pins the
        # full register contract), so the design services from exception
        # context instead of returning to a PongoOS loop.
        jump = _read(os.path.join(REPO, "pongoos-src", "src", "boot",
                                  "jump_to_image.S"))
        self.assertIn("mov x30, x0", jump)


class TestServicingContextPrimitives(unittest.TestCase):
    """Design A's context: EP0 processing via usb_handler() straight from
    exception context -- pinned prerequisites."""

    def test_usb_handler_is_register_only(self):
        otg = _read(OTG_C)
        body = _body(otg, "void usb_handler() {")
        self.assertIn("reg_read(rGINTSTS)", body)
        self.assertIn("usb_ep_interrupt();", body)
        self.assertIn("usb_reset();", body)
        # No scheduler, locking, allocation or console dependency:
        for forbidden in ("task_", "lock_", "yield", "iprintf", "printf",
                          "malloc", "event_"):
            self.assertNotIn(forbidden, body,
                             "usb_handler must stay exception-context safe")

    def test_irq_mode_iteration_already_runs_usb_main_per_irq(self):
        otg = _read(OTG_C)
        self.assertIn("usb_irq_mode = 1;", otg)
        main = _body(otg, "void usb_main() {")
        self.assertIn("disable_interrupts();", main)
        self.assertIn("usb_handler();", main)
        self.assertIn("enable_interrupts();", main)
        self.assertIn("task_exit_irq();", main)

    def test_cooperative_irq_registration_api_exists(self):
        task = _read(TASK_C)
        self.assertIn(
            "void task_register_irq(struct task* task, void (*entry)(), "
            "int irq_id) {", task)
        # Its only in-tree use is COMMENTED OUT -- honest inventory: the
        # API exists, there is no live caller to copy behavior from.
        gpio = _read(GPIO_C)
        use = gpio.index("task_register_irq(&gpio_task")
        self.assertLess(gpio.index("/*"), use)
        self.assertLess(use, gpio.index("*/", use))
        self.assertIn("PONGO_EXPORT(task_register_irq);",
                      _read(MODLOAD_C))

    def test_cooperative_irq_branch_switches_in_exception_context(self):
        # task_switch_irq: a NON-preempt IRQ task is switched into
        # immediately (_task_switch) and returns via irq_ret -- i.e. it
        # does NOT depend on the scheduler loop.
        task = _read(TASK_C)
        self.assertIn("new->irq_ret = task_current();", task)
        self.assertIn("_task_switch(new);", task)
        self.assertIn("the irq handler wasn't exited correctly?!", task)
        self.assertIn("we are not entering an irq task", task)

    def test_exception_stack_survives_to_the_jump(self):
        ll = _read(LOWLEVEL_C)
        self.assertIn("uint64_t exception_stack[0x4000/8]", ll)
        entry = _read(ENTRY_C)
        # re-armed immediately before lowlevel_cleanup / the RAW jump
        self.assertLess(entry.index("set_exception_stack_core0();",
                                    entry.index("pongo_entry_cached();")),
                        entry.index("lowlevel_cleanup();"))


class TestPatchPointInventory(unittest.TestCase):
    """The exact statements Design A must condition (they are currently
    UNCONDITIONAL -- verified by absence of any guard)."""

    def test_teardown_statements_have_no_flag_guard_today(self):
        entry = _read(ENTRY_C)
        for call in ("usb_teardown();", "disable_interrupts();",
                     "interrupt_teardown();"):
            self.assertIsNone(
                re.search(r"if\s*\([^)]*\)\s*\n?\s*[^;{]*" + call, entry),
                "%s must currently be unconditional" % call)

    def test_cleanup_split_point_is_two_adjacent_lines(self):
        mm = _read(MM_C)
        body = _body(mm, "void lowlevel_cleanup(void)")
        # keep cache_clean_and_invalidate, skip disable_mmu_el1
        self.assertLess(body.index("cache_clean_and_invalidate"),
                        body.index("disable_mmu_el1();"))

    def test_usb_registration_is_the_single_dispatch_choice(self):
        otg = _read(OTG_C)
        self.assertLess(otg.index("usb_irq = 0;"),
                        otg.index("if (usb_irq_mode) {"))
        self.assertLess(otg.index("task_register_preempt_irq(&usb_task,"),
                        otg.index("else task_register(&usb_task, "
                                  "usb_main);"))

    def test_probe_mask_site_and_el0_guard_order(self):
        probe = _read(PROBE_S)
        # The daifset must move AFTER recording (or become IRQ-open) --
        # pin the exact current site and its EL0 guard ordering.
        self.assertLess(probe.index("cbz     x15, park"),
                        probe.index("msr     daifset, #0xf"))
        self.assertLess(probe.index("msr     daifset, #0xf"),
                        probe.index("mrs     x14, SCTLR_EL1"))


class TestAddressDatumRoutes(unittest.TestCase):
    """Blocker A inventory: no existing output reveals the buffer
    address, so a host-visible datum requires NEW device code -- or the
    design simply never needs it (device-side pointer serve)."""

    def test_no_live_print_reveals_the_buffer_address(self):
        bad = []
        for root, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith((".c", ".h")):
                    continue
                path = os.path.join(root, fn)
                with open(path, "r", encoding="utf-8",
                          errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        code = line.split("//", 1)[0]
                        if "loader_xfer_recv_data" not in code:
                            continue
                        if re.search(r"\b(iprintf|fiprintf|printf|puts|"
                                     r"siprintf)\s*\(", code):
                            bad.append("%s:%d" % (os.path.relpath(path, SRC),
                                                  i))
        self.assertEqual(bad, [],
                         "a live print would already leak the datum")

    def test_datum_ensembling_primitives_exist_for_alternative(self):
        # The documented alternative (device assembles the block pre-jump)
        # can read SCTLR/CNTPCT from C-callable asm today ...
        ll = _read(LOWLEVEL_C)
        self.assertIn('"    mrs x3, sctlr_el1\\n"', ll)
        self.assertIn('"    mrs x0, cntpct_el0\\n"', ll)
        # ... M-P2.12 adds MIDR/CNTFRQ reads in the `regs` command.
        # The PRE-HANDOFF probe (pongo_probe.S) still cannot read these.
        # This test verifies the new expected location.
        hits = []
        for root, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith((".c", ".h", ".S", ".s")):
                    continue
                path = os.path.join(root, fn)
                with open(path, "r", encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
                if re.search(r"midr_el1|cntfrq_el0", text, re.I):
                    hits.append(os.path.relpath(path, SRC))
        # M-P2.12: should now find exactly shell/regs.c
        self.assertEqual(hits, ["shell/regs.c"])


if __name__ == "__main__":
    print("NOTE: OFFLINE DESIGN VALIDATION -- source-contract only; no "
          "hardware, no probe rebuild, pongoos-src unmodified.",
          flush=True)
    sys.exit(0 if unittest.main(verbosity=2, exit=False).result.wasSuccessful()
             else 1)
