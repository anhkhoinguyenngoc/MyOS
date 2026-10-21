#!/usr/bin/env python3
"""
MyOS M-P1 probe invariant checker (host-side, stdlib only).

Validates the PongoOS A8 probe's source-level and layout invariants
without requiring a cross-toolchain or a device:

  1. Required files exist; QEMU-kernel path files untouched (presence).
  2. Report .equ offsets are ordered, non-overlapping, and RPT_SIZE matches.
  3. Magic constant is exactly 8 ASCII bytes ("MP1PRB01").
  4. Probe entry symbol exists; linker ENTRY/ASSERTs reference it.
  5. Linker script pins no invented load address (base 0x0, report 0x200,
     1 MiB bound ASSERT) and discards metadata sections.
  6. Assembly uses no stack (`sp`), no link-branch (`bl`), no absolute
     physical-address literals, and only the documented MRS registers.
  7. If build/pongo_probe.bin exists, verifies magic at 0x200 and the
     1 MiB size bound (skipped with a clear message when absent).

Usage:
  python3 tests/check_pongo_probe.py [--repo ROOT]

Exit 0 = all checks pass. Exit 1 = any failure. Exit 2 = skipped-binary
note printed but all static checks passed (never used: absence of the
binary is informational, not a failure).
"""

import os
import re
import struct
import sys

REPO = sys.argv[sys.argv.index("--repo") + 1] \
    if "--repo" in sys.argv else os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))

PROBE_S = os.path.join(REPO, "platform", "apple", "a8", "pongo_probe.S")
PROBE_LD = os.path.join(REPO, "platform", "apple", "a8", "pongo_probe.ld")
PROBE_BIN = os.path.join(REPO, "build", "pongo_probe.bin")

EXPECTED_MAGIC = b"MP1PRB01"
EXPECTED_RPT_OFF = 0x200
UPLOAD_BUF_BOUND = 0x100000  # 1 MiB PongoOS upload buffer (usbloader.c)

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (" -- " + detail if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    # 1. Files exist; QEMU path intact.
    check("probe assembly exists", os.path.isfile(PROBE_S), PROBE_S)
    check("probe linker script exists", os.path.isfile(PROBE_LD), PROBE_LD)
    for keep in ("platform/qemu-virt/start.S", "platform/qemu-virt/kernel.ld",
                 "platform/apple/a8/m1_entry.S", "kernel/main.c"):
        check("QEMU/M1 path intact: " + keep,
              os.path.isfile(os.path.join(REPO, keep)), keep)
    if FAILURES:
        return 1
    src = read(PROBE_S)
    ld = read(PROBE_LD)

    # 2. Report .equ offsets: ordered, non-overlapping, size consistent.
    equs = dict((m.group(1), int(m.group(2), 0)) for m in
                re.finditer(r"\.equ\s+(\w+),\s*(0x[0-9a-fA-F]+|\d+)", src))
    fields = [(k, v) for k, v in equs.items()
              if k.startswith("RPT_") and k.endswith("_OFF")
              and k != "RPT_OFF"]  # RPT_OFF is the block base, not a field
    check("report offsets defined", len(fields) >= 10,
          "found %d" % len(fields))
    # Version/size share the 8-byte slot at +0x08 (two u32); the rest are
    # u64 slots that must not overlap.
    spans = sorted(fields, key=lambda kv: kv[1])
    u64spans = [(k, v) for k, v in spans
                if k not in ("RPT_VER_OFF", "RPT_SIZE_OFF")]
    ok_order = all(b[1] >= a[1] + 8 for a, b in zip(u64spans, u64spans[1:]))
    check("report u64 slots ordered/non-overlapping", ok_order)
    rpt_size = equs.get("RPT_SIZE", 0)
    last_end = max(v for _, v in u64spans) + 8
    check("RPT_SIZE covers all fields", rpt_size == last_end,
          "RPT_SIZE=0x%x last_end=0x%x" % (rpt_size, last_end))
    check("RPT_VERSION == 1", equs.get("RPT_VERSION") == 1)
    check("RPT_OFF == 0x200", equs.get("RPT_OFF") == EXPECTED_RPT_OFF)

    # 3. Magic constant.
    m = re.search(r"probe_magic:\s*\.quad\s+(0x[0-9a-fA-F]+)", src)
    check("magic quad present", m is not None)
    if m:
        val = int(m.group(1), 16)
        check("magic == b'MP1PRB01'",
              struct.pack("<Q", val) == EXPECTED_MAGIC,
              m.group(1))

    # 4. Entry symbol + linker references.
    check("pongo_probe_entry global+label",
          ".global pongo_probe_entry" in src
          and re.search(r"^pongo_probe_entry:", src, re.M) is not None)
    check("ld ENTRY(pongo_probe_entry)",
          "ENTRY(pongo_probe_entry)" in ld)
    check("ld ASSERT entry == 0",
          re.search(r"ASSERT\s*\(\s*pongo_probe_entry\s*==\s*0x0", ld)
          is not None)
    check("ld ASSERT report == 0x200",
          re.search(r"ASSERT\s*\(\s*probe_report\s*==\s*0x200", ld)
          is not None)
    check("ld ASSERT 1MiB bound",
          re.search(r"ASSERT\s*\(\s*\.\s*<=\s*0x100000", ld) is not None)

    # 5. No invented load address in the linker script.
    check("ld base is 0x0", re.search(r"^\s*\.\s*=\s*0x0\s*;",
                                      ld, re.M) is not None)
    check("ld discards metadata",
          all(s in ld for s in ("*(.comment)", "*(.note*)",
                                "*(.eh_frame*)", "*(.gnu*)")))

    # 6. Code discipline: no sp, no bl, no absolute phys literals,
    #    only documented system registers. Comments are stripped first so
    #    documentation (e.g. the VA->PA conversion note) cannot trip the
    #    checks; only emitted code is judged.
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//.*", "", code)
    check("no `sp` register use",
          re.search(r"\bsp\b", code) is None)
    check("no `bl` (x30 holds image base)",
          re.search(r"^\s*bl\b", code, re.M) is None)
    bighex = [h for h in re.findall(r"0x[0-9a-fA-F]+", code)
              if int(h, 16) >= 0x10000
              and h.lower() != "0x313042525031504d"]
    check("no absolute physical-address literals", not bighex,
          ",".join(bighex))
    mrs_regs = set(re.findall(r"mrs\s+x\d+\s*,\s*([A-Za-z0-9_]+)", code))
    check("only documented MRS registers",
          mrs_regs <= {"CurrentEL", "SCTLR_EL1", "MIDR_EL1",
                       "CNTPCT_EL0", "CNTFRQ_EL0"},
          ",".join(sorted(mrs_regs)))
    check("daifset present (IRQ mask)",
          re.search(r"msr\s+daifset\s*,\s*#0xf", code) is not None)
    check("self-branch park present",
          re.search(r"^park:\s*\n\s*b\s+park", code, re.M) is not None)

    # 7. Built binary invariants (informational when absent).
    if os.path.isfile(PROBE_BIN):
        with open(PROBE_BIN, "rb") as fh:
            blob = fh.read()
        check("binary size <= 1MiB", len(blob) <= UPLOAD_BUF_BOUND,
              "%d bytes" % len(blob))
        check("magic at file offset 0x200",
              blob[EXPECTED_RPT_OFF:EXPECTED_RPT_OFF + 8]
              == EXPECTED_MAGIC)
    else:
        print("NOTE  build/pongo_probe.bin absent (no cross-linker on this "
              "host); binary checks skipped, static checks stand.")

    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("M-P1 probe checks: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
