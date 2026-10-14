#!/usr/bin/env python3
"""
MyOS M-P2.4 -- offline tests for tools/host/pongo_addr_resolve.py.

These tests are OFFLINE and use SYNTHETIC fixtures. Per the task rules:

  * A synthetic Mach-O fixture is built in-memory/test-time with invented
    symbol names/values. It exercises the PARSER ONLY.
  * Synthetic symbol values / synthetic addresses are NEVER presented as
    evidence of any real runtime address. Every such case is labelled
    "SYNTHETIC" in the test method.
  * The real Pongo artifact is only *parsed* for artifact facts (image base,
    entry, symbol VM addresses). Parsing a symbol does not yield a runtime
    upload-buffer address; the test asserts that it does not.

Usage:
  python3 tests/test_pongo_addr_resolve.py
Exit 0 = all pass, non-zero = failure.
"""

import importlib.util
import os
import struct
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVER = os.path.join(REPO, "tools", "host", "pongo_addr_resolve.py")

# load the resolver module by path (it is a script, not an installed package)
_spec = importlib.util.spec_from_file_location("pongo_addr_resolve", RESOLVER)
par = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(par)


# --------------------------------------------------------------------------
# SYNTHETIC Mach-O fixture builder (invented values; parser test only)
# --------------------------------------------------------------------------
def build_macho(symbols, with_symtab=True, segname="__TEXT",
                vmaddr=0x100000000, section_size=0x100):
    """Build a minimal 64-bit little-endian Mach-O in memory.

    symbols: ordered list of (name, value, n_type, n_sect) with SYNTHETIC
             values chosen by the caller.
    """
    seg_cmd_size = 72 + 80  # LC_SEGMENT_64 + one section
    sym_cmd_size = 24
    ncmds = (1 if with_symtab else 0) + 1
    sizeofcmds = seg_cmd_size + (sym_cmd_size if with_symtab else 0)

    strtab = b"\x00"
    strx = {}
    for (name, _v, _t, _s) in symbols:
        strx[name] = len(strtab)
        strtab += name.encode("latin1") + b"\x00"

    lc_start = 32
    symoff = lc_start + sizeofcmds
    nlist = b""
    for (name, value, n_type, n_sect) in symbols:
        nlist += struct.pack("<IBBHQ", strx[name], n_type, n_sect, 0, value)
    stroff = symoff + len(nlist)

    hdr = struct.pack("<IiiIIIII", par.MH_MAGIC_64, 0x100000c, 0x0, 0x5,
                      ncmds, sizeofcmds, 0x1, 0)

    seg = struct.pack("<II", par.LC_SEGMENT_64, seg_cmd_size)
    seg += segname.encode("latin1").ljust(16, b"\0")
    seg += struct.pack("<QQQQ", vmaddr, section_size, 0, section_size)
    seg += struct.pack("<iiII", 7, 5, 1, 0)  # maxprot, initprot, nsects, flags
    seg += b"__text".ljust(16, b"\0") + b"__TEXT".ljust(16, b"\0")
    seg += struct.pack("<QQII", vmaddr, section_size, 0, 2)
    seg += b"\0" * 24  # reloff/nreloc/flags/reserved1/reserved2/reserved3

    cmds = seg
    if with_symtab:
        cmds += struct.pack("<IIIIII", par.LC_SYMTAB, sym_cmd_size,
                            symoff, len(symbols), stroff, len(strtab))
    assert len(cmds) == sizeofcmds, (len(cmds), sizeofcmds)

    blob = hdr + cmds + nlist + strtab
    return blob


def write_tmp(blob, tmpdir, name="Pongo.synthetic"):
    p = os.path.join(tmpdir, name)
    with open(p, "wb") as fh:
        fh.write(blob)
    return p


# --------------------------------------------------------------------------
# Parser (synthetic)
# --------------------------------------------------------------------------
class SyntheticParserTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="mp24-")

    def test_synthetic_symbols_roundtrip(self):
        # SYNTHETIC names/values -- not real symbols.
        syms = [
            ("_synth_fn", 0x100000004, 0x0e, 1),
            ("_synth_ptr", 0x100000080, 0x0e, 1),
            ("_synth_bss", 0x100000090, 0x0e, 1),
        ]
        p = write_tmp(build_macho(syms), self.tmp)
        m = par.parse_macho(p)
        self.assertEqual(m["image_base"], 0x100000000)
        for name, value, _t, _s in syms:
            self.assertIn(name, m["symbols"])
            self.assertEqual(m["symbols"][name][0]["value"], value)

    def test_synthetic_no_symtab_rejected(self):
        p = write_tmp(build_macho([], with_symtab=False), self.tmp)
        with self.assertRaises(par.MachOError):
            par.parse_macho(p)

    def test_bad_magic_rejected(self):
        p = os.path.join(self.tmp, "bad")
        with open(p, "wb") as fh:
            fh.write(b"NOTMACHO" + b"\0" * 32)
        with self.assertRaises(par.MachOError):
            par.parse_macho(p)

    def test_synthetic_section_lookup(self):
        syms = [("_synth_fn", 0x100000010, 0x0e, 1)]
        p = write_tmp(build_macho(syms), self.tmp)
        m = par.parse_macho(p)
        r = par.resolve_symbol(m, "_synth_fn")
        self.assertEqual(r["section"], "__TEXT,__text")
        self.assertEqual(r["image_relative"], 0x10)
        # no runtime datum given -> no runtime VA invented
        self.assertIsNone(r["runtime_va"])


# --------------------------------------------------------------------------
# Address model (synthetic physical/VA values)
# --------------------------------------------------------------------------
class AddressModelTests(unittest.TestCase):
    def test_phystokv_vatophys_roundtrip(self):
        for pa in (0x800000000, 0x801000000, 0x812340000, 0x8FFFFFFF):
            va = par.phystokv(pa)
            self.assertEqual(par.vatophys_static(va), pa)

    def test_known_pair(self):
        # mm.c formula with kCacheableView=0x400000000, phys view 0x800000000
        self.assertEqual(par.phystokv(0x800000000), 0x400000000)
        self.assertEqual(par.vatophys_static(0x400000000), 0x800000000)

    def test_handoff_alias_equals_pa(self):
        # entry.c: target = VA - kCacheableView + 0x800000000 == physical alias
        pa = 0x812340000
        va = par.phystokv(pa)
        self.assertEqual(par.handoff_alias(va), pa)

    def test_slide_forms_equivalent(self):
        # SYNTHETIC link VA / runtime base. runtime_va(slide) and
        # runtime_va_from_base must agree; values are invented.
        link = 0x1000624ac
        pongo_base = 0x4ABCD0000
        slide = par.IMAGE_BASE_DEFAULT - pongo_base
        self.assertEqual(par.runtime_va(link, slide),
                         par.runtime_va_from_base(link, pongo_base))

    def test_kcacheable_override_respected(self):
        # KPF test build overrides to 0x800000000 (checkra1n Makefile);
        # the override is accepted but default stays 0x400000000.
        self.assertEqual(par.phystokv(0x800000000, 0x800000000), 0x800000000)
        self.assertEqual(par.K_CACHEABLE_VIEW_DEFAULT, 0x400000000)


# --------------------------------------------------------------------------
# Buffer resolution guards: a symbol can NEVER yield a buffer address
# --------------------------------------------------------------------------
class BufferResolutionTests(unittest.TestCase):
    def test_no_datum_is_unresolved(self):
        r = par.resolve_buffer()
        self.assertEqual(r["status"], "UNRESOLVED_NEEDS_RUNTIME_DATUM")

    def test_pa_yields_consistent_set(self):
        r = par.resolve_buffer(buffer_pa=0x812340000)
        self.assertEqual(r["status"], "RESOLVED_FROM_RUNTIME_DATUM")
        self.assertEqual(r["buffer_va"], 0x412340000)
        self.assertEqual(r["handoff_alias_x30"], 0x812340000)
        self.assertEqual(r["report_pa"], 0x812340200)

    def test_va_yields_same_pa(self):
        a = par.resolve_buffer(buffer_pa=0x812340000, buffer_va=0x412340000)
        b = par.resolve_buffer(buffer_va=0x412340000)
        self.assertEqual(a["buffer_pa"], b["buffer_pa"])
        self.assertEqual(a["status"], b["status"])

    def test_contradictory_datum_rejected(self):
        r = par.resolve_buffer(buffer_pa=0x812340000, buffer_va=0x999999999)
        self.assertEqual(r["status"], "INCONSISTENT_RUNTIME_DATUM")


# --------------------------------------------------------------------------
# Source assumptions (real pinned source)
# --------------------------------------------------------------------------
class SourceAssumptionTests(unittest.TestCase):
    def test_pinned_source_matches_model(self):
        ok, rows = par.verify_source(REPO)
        missing = [p for p, _pat, f in rows if not f]
        self.assertTrue(ok, "source assumptions NOT found in: %s" % missing)


class ExitCodeTests(unittest.TestCase):
    """M-P2.44: a --verify-source failure must propagate through the
    process exit code even when a later --symbol resolves successfully.
    Before the fix, the symbol branch reset rc to 0 and masked the
    source-assumption failure (OFFLINE; real Mach-O parsed read-only,
    a temporary empty repo simulates the failing source tree)."""

    REAL_MACHO = os.path.join(REPO, "pongoos-src", "build", "Pongo")

    def test_verify_source_failure_not_masked_by_symbol(self):
        if not os.path.isfile(self.REAL_MACHO):
            self.skipTest("real Pongo Mach-O absent")
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            rc = par.main(["--repo", empty, "--macho", self.REAL_MACHO,
                           "--verify-source", "--symbol", "_pongo_entry"])
        self.assertNotEqual(
            rc, 0, "source-verification failure was masked by --symbol")

    def test_verify_source_failure_alone_is_nonzero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            rc = par.main(["--repo", empty, "--verify-source"])
        self.assertNotEqual(rc, 0)

    def test_verify_source_success_with_symbol_is_zero(self):
        if not os.path.isfile(self.REAL_MACHO):
            self.skipTest("real Pongo Mach-O absent")
        rc = par.main(["--repo", REPO, "--verify-source",
                       "--symbol", "_pongo_entry"])
        self.assertEqual(rc, 0)

    def test_malformed_buffer_pa_is_usage_error(self):
        # M-P2.44: malformed hex must be a clean exit 2, not a traceback.
        rc = par.main(["--buffer-pa", "zzz"])
        self.assertEqual(rc, 2)

    def test_malformed_kcacheable_is_usage_error(self):
        rc = par.main(["--buffer-pa", "0x1000", "--k-cacheable-view", "nope"])
        self.assertEqual(rc, 2)

    def test_malformed_slide_is_usage_error(self):
        if not os.path.isfile(self.REAL_MACHO):
            self.skipTest("real Pongo Mach-O absent")
        rc = par.main(["--macho", self.REAL_MACHO, "--symbol", "_pongo_entry",
                       "--slide", "nothex"])
        self.assertEqual(rc, 2)


# --------------------------------------------------------------------------
# Real artifact (parse only; NOT a runtime-address claim)
# --------------------------------------------------------------------------
REAL = os.path.join(REPO, "pongoos-src", "build", "Pongo")


@unittest.skipUnless(os.path.isfile(REAL), "pongoos-src/build/Pongo absent")
class RealArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = par.parse_macho(REAL)

    def test_image_base_and_entry(self):
        self.assertEqual(self.m["image_base"], 0x100000000)
        self.assertEqual(self.m["entry"], 0x100000000)

    def test_key_symbols_present_at_pinned_offsets(self):
        # Artifact facts (link-time), pinned by image-relative offset.
        expect = {
            "_pongo_entry": 0x624ac,
            "_usbloader_init": 0x6d240,
            "_loader_xfer_recv_data": 0xa81f0,
            "_alloc_phys": 0x66bc0,
            "_alloc_contig": 0x66f5c,
            "_phystokv": 0x65818,
            "_vatophys_static": 0x659f4,
            "_gPongoSlide": 0xa56e8,
            "_gBootArgs": 0xa55d8,
        }
        for name, off in expect.items():
            self.assertIn(name, self.m["symbols"], name)
            r = par.resolve_symbol(self.m, name)
            self.assertEqual(r["image_relative"], off, name)

    def test_pointer_var_is_bss_not_the_buffer(self):
        r = par.resolve_symbol(self.m, "_loader_xfer_recv_data")
        self.assertEqual(r["section"], "__DATA,__bss")
        # It is a pointer VARIABLE; its address (link VA) must not be
        # presented as the buffer address.
        self.assertIsNotNone(r["pointer_var"])

    def test_symbols_do_not_yield_buffer_address(self):
        # Even with every symbol parsed, the buffer stays unresolved.
        self.assertEqual(par.resolve_buffer()["status"],
                         "UNRESOLVED_NEEDS_RUNTIME_DATUM")
        # And resolving the pointer symbol without a datum yields no runtime VA.
        r = par.resolve_symbol(self.m, "_loader_xfer_recv_data")
        self.assertIsNone(r["runtime_va"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
