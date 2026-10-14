#!/usr/bin/env python3
"""
MyOS M-P2.30 -- offline tests for explicit PongoOS artifact selection in
tools/host/pongo_addr_resolve.py.

These tests are OFFLINE and do NOT execute any payload, talk to a device, or
change any artifact. They cover the M-P2.30 backward-compatible addition:

  * the historical default path is unchanged (pongoos-src/build/Pongo);
  * an explicit --macho PATH is honoured (and never falls back);
  * an explicit --artifact-dir DIR is honoured for both preserved layouts
    (DIR/Pongo and DIR/artifacts/Pongo);
  * invalid / missing selections fail safely with a clear message and a
    non-zero exit code;
  * --macho and --artifact-dir are mutually exclusive (usage error).

Most cases use SYNTHETIC Mach-O fixtures built in memory/test-time so the
suite is self-contained. Selecting the real preserved artifact is an extra
integration check that is skipped when the artifact is not present.

Usage:
  python3 tests/test_pongo_artifact_select.py
Exit 0 = all pass, non-zero = failure.
"""

import contextlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOLVER = os.path.join(REPO, "tools", "host", "pongo_addr_resolve.py")

# load the resolver module by path (it is a script, not an installed package)
_spec = importlib.util.spec_from_file_location("pongo_addr_resolve", RESOLVER)
par = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(par)

# Historical default artifact (the local PongoOS checkout build output).
REAL_DEFAULT = os.path.join(REPO, "pongoos-src", "build", "Pongo")


def find_preserved_root(repo):
    """Return the first artifacts/<name>/ dir containing artifacts/Pongo."""
    base = os.path.join(repo, "artifacts")
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        root = os.path.join(base, name)
        if os.path.isfile(os.path.join(root, "artifacts", "Pongo")):
            return root
    return None


PRESERVED = find_preserved_root(REPO)


# --------------------------------------------------------------------------
# SYNTHETIC Mach-O fixture (invented symbol values; selection test only)
# --------------------------------------------------------------------------
def build_macho(symbols, vmaddr=0x100000000, section_size=0x100):
    """Build a minimal 64-bit LE Mach-O with a symtab (parser/selection only)."""
    seg_cmd_size = 72 + 80  # LC_SEGMENT_64 + one section
    sym_cmd_size = 24
    ncmds = 2
    sizeofcmds = seg_cmd_size + sym_cmd_size

    strtab = b"\x00"
    strx = {}
    for name, _v in symbols:
        strx[name] = len(strtab)
        strtab += name.encode("latin1") + b"\x00"

    symoff = 32 + sizeofcmds
    nlist = b""
    for name, value in symbols:
        nlist += struct.pack("<IBBHQ", strx[name], 0x0e, 1, 0, value)
    stroff = symoff + len(nlist)

    hdr = struct.pack("<IiiIIIII", par.MH_MAGIC_64, 0x100000c, 0x0, 0x5,
                      ncmds, sizeofcmds, 0x1, 0)

    seg = struct.pack("<II", par.LC_SEGMENT_64, seg_cmd_size)
    seg += b"__TEXT".ljust(16, b"\0")
    seg += struct.pack("<QQQQ", vmaddr, section_size, 0, section_size)
    seg += struct.pack("<iiII", 7, 5, 1, 0)
    seg += b"__text".ljust(16, b"\0") + b"__TEXT".ljust(16, b"\0")
    seg += struct.pack("<QQII", vmaddr, section_size, 0, 2)
    seg += b"\0" * 24

    sym = struct.pack("<IIIIII", par.LC_SYMTAB, sym_cmd_size,
                      symoff, len(symbols), stroff, len(strtab))
    cmds = seg + sym
    assert len(cmds) == sizeofcmds, (len(cmds), sizeofcmds)
    return hdr + cmds + nlist + strtab


def write_file(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def run_main(argv):
    """Call par.main(argv) capturing stdout/stderr; return (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = par.main(argv)
    return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# Default behavior must be unchanged
# --------------------------------------------------------------------------
class DefaultBehaviorTests(unittest.TestCase):
    def test_default_macho_path_is_historical_build(self):
        self.assertEqual(par.default_macho_path(REPO), REAL_DEFAULT)
        self.assertEqual(par.default_macho_path("/x"),
                         os.path.join("/x", "pongoos-src", "build", "Pongo"))

    def test_no_flags_uses_default_path(self):
        # A synthetic repo exercises the default resolution everywhere.
        tmp = tempfile.mkdtemp(prefix="mp230-")
        default = os.path.join(tmp, "pongoos-src", "build", "Pongo")
        write_file(default, build_macho([("_pongo_entry", 0x100000000)]))
        rc, out, err = run_main(["--repo", tmp, "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["artifact"], default)


# --------------------------------------------------------------------------
# Explicit --macho selection
# --------------------------------------------------------------------------
class ExplicitMachoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mp230-")

    def test_explicit_macho_is_selected(self):
        p = write_file(os.path.join(self.tmp, "some-pongo"),
                       build_macho([("_pongo_entry", 0x100000000)]))
        rc, out, err = run_main(["--macho", p, "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["artifact"], os.path.abspath(p))

    def test_explicit_macho_does_not_fall_back_to_default(self):
        # A valid default exists, but a missing --macho must still fail.
        write_file(os.path.join(self.tmp, "pongoos-src", "build", "Pongo"),
                   build_macho([("_pongo_entry", 0x100000000)]))
        missing = os.path.join(self.tmp, "does-not-exist")
        rc, out, err = run_main(["--repo", self.tmp, "--macho", missing])
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", err)
        self.assertIn(missing, err)

    def test_non_macho_file_rejected(self):
        bad = os.path.join(self.tmp, "not-a-macho")
        write_file(bad, b"NOTMACHO" + b"\0" * 64)
        rc, out, err = run_main(["--macho", bad])
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", err)


# --------------------------------------------------------------------------
# Explicit --artifact-dir selection (both preserved layouts)
# --------------------------------------------------------------------------
class ArtifactDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mp230-")

    def test_versioned_layout(self):
        # DIR/artifacts/Pongo  (the preserved m-p2.26-<commit> layout)
        p = write_file(os.path.join(self.tmp, "artifacts", "Pongo"),
                       build_macho([("_pongo_entry", 0x100000000)]))
        rc, out, err = run_main(["--artifact-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["artifact"], os.path.abspath(p))

    def test_inner_layout(self):
        # DIR/Pongo  (the inner artifacts directory itself)
        p = write_file(os.path.join(self.tmp, "Pongo"),
                       build_macho([("_pongo_entry", 0x100000000)]))
        rc, out, err = run_main(["--artifact-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["artifact"], os.path.abspath(p))

    def test_locate_helper(self):
        self.assertIsNone(par.locate_artifact_macho(""))
        self.assertIsNone(par.locate_artifact_macho(None))
        empty = tempfile.mkdtemp(prefix="mp230-")
        self.assertIsNone(par.locate_artifact_macho(empty))
        inner = write_file(os.path.join(empty, "Pongo"), b"x")
        self.assertEqual(par.locate_artifact_macho(empty), os.path.abspath(inner))

    def test_empty_dir_fails_safely(self):
        empty = tempfile.mkdtemp(prefix="mp230-")
        rc, out, err = run_main(["--artifact-dir", empty])
        self.assertEqual(rc, 1)
        self.assertIn("no PongoOS Mach-O found", err)

    def test_missing_dir_fails_safely(self):
        rc, out, err = run_main(["--artifact-dir", "/no/such/dir"])
        self.assertEqual(rc, 1)
        self.assertIn("no PongoOS Mach-O found", err)


# --------------------------------------------------------------------------
# Usage errors
# --------------------------------------------------------------------------
class UsageErrorTests(unittest.TestCase):
    def test_macho_and_artifact_dir_mutually_exclusive(self):
        rc, out, err = run_main(["--macho", "/x", "--artifact-dir", "/y"])
        self.assertEqual(rc, 2)
        self.assertIn("mutually exclusive", err)


# --------------------------------------------------------------------------
# Real preserved artifact (integration; skipped when absent)
# --------------------------------------------------------------------------
@unittest.skipUnless(PRESERVED, "preserved artifact directory absent")
class RealPreservedArtifactTests(unittest.TestCase):
    def test_select_by_artifact_dir(self):
        rc, out, err = run_main(["--artifact-dir", PRESERVED, "--json"])
        self.assertEqual(rc, 0, err)
        info = json.loads(out)
        self.assertEqual(info["image_base"], "0x0000000100000000")
        self.assertEqual(info["entry"], "0x0000000100000000")
        self.assertTrue(info["artifact"].endswith(os.path.join("artifacts", "Pongo")))

    def test_select_by_macho_and_resolve_regs_symbol(self):
        rc, out, err = run_main(["--artifact-dir", PRESERVED, "--json"])
        self.assertEqual(rc, 0, err)
        macho = json.loads(out)["artifact"]
        rc, out, err = run_main(["--macho", macho, "--symbol", "_regs_cmd", "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["name"], "_regs_cmd")


@unittest.skipUnless(os.path.isfile(REAL_DEFAULT), "pongoos-src/build/Pongo absent")
class RealDefaultArtifactTests(unittest.TestCase):
    def test_default_still_points_at_worktree_build(self):
        rc, out, err = run_main(["--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["artifact"], REAL_DEFAULT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
