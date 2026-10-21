#!/usr/bin/env python3
"""
MyOS M7.13 — host-side model of the Apple A8 raw-entry exception record.

SYNTHETIC FIXTURE WARNING: this test builds a SYNTHETIC exception record in
Python from the layout constants defined in platform/apple/a8/raw_vectors.S.
The synthetic record is a model used to verify that the source-level contract
(field offsets, fixed size, directive layout, marker-last ordering) is
internally consistent and matches the statically linked bytes of
build/raw_entry.bin. It is NOT evidence that the vector table ever executed,
that any exception was captured, or that VBAR_EL1 works on hardware.

Checks (source model + static artifact bytes; no device, no execution):
  * .equ constants in raw_vectors.S are complete, ordered and consistent;
  * the record directive block lays out exactly EXC_SIZE bytes with the
    completion marker in the final field;
  * the handler stores fields in the declared order with the ordering
    barrier (dsb sy) between the data fields and the marker store;
  * the synthetic record built from those constants matches the artifact's
    static header bytes and the artifact's zero initial capture state;
  * marker semantics: 0 = not captured in any file-backed state.

Exit 0 = pass, 1 = failure. Static checks always run; the artifact
comparison is skipped with a NOTE when the build is absent.
"""
import os
import re
import shutil
import struct
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_VS = os.path.join(REPO, "platform", "apple", "a8", "raw_vectors.S")
RAW_BIN = os.path.join(REPO, "build", "raw_entry.bin")
RAW_ELF = os.path.join(REPO, "build", "raw_entry.elf")

EXC_MAGIC = b"MYOSA8E1"
EXC_SIZE = 0x48
FIELD_ORDER = ("EXC_OFF_SLOT", "EXC_OFF_ESR", "EXC_OFF_ELR", "EXC_OFF_FAR",
               "EXC_OFF_SPSR", "EXC_OFF_SP", "EXC_OFF_VALID")

FAILURES = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name,
                        (" -- " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(name)


def note(text):
    print("NOTE  " + text)


def main():
    if not os.path.isfile(RAW_VS):
        print("%s check(s) FAILED" % 1)
        return 1
    with open(RAW_VS, encoding="utf-8") as fh:
        vsrc = fh.read()

    equs = dict(
        (m.group(1), int(m.group(2), 0))
        for m in re.finditer(r"\.equ\s+(EXC_\w+)\s*,\s*(0x[0-9a-fA-F]+|\d+)",
                             vsrc))
    for name in ("EXC_SIZE", "EXC_VERSION", "EXC_VALID_MARKER") + FIELD_ORDER:
        check("constant %s defined" % name, name in equs)
    check("EXC_SIZE model value matches", equs.get("EXC_SIZE") == EXC_SIZE,
          str(equs.get("EXC_SIZE")))
    offs = [equs.get(n) for n in FIELD_ORDER]
    check("field offsets strictly ascending and inside the record",
          all(o is not None for o in offs)
          and offs == sorted(offs) and offs[0] >= 16
          and offs[-1] + 8 == EXC_SIZE, str(offs))
    check("completion marker value is nonzero",
          equs.get("EXC_VALID_MARKER") not in (None, 0))

    m = re.search(r'\.ascii\s+"([A-Za-z0-9]+)"', vsrc)
    src_magic = m.group(1).encode() if m else b""
    check("record magic directive matches the model", src_magic == EXC_MAGIC,
          repr(src_magic))

    try:
        block = vsrc.split("myos_a8_exc_record:", 1)[1]
        block = block.split("myos_a8_exc_record_end:", 1)[0]
    except IndexError:
        block = ""
    pos = 0
    layout = []
    for line in block.splitlines():
        m = re.match(r'\s*\.ascii\s+"([A-Za-z0-9]+)"', line)
        if m:
            layout.append(("ascii", pos))
            pos += len(m.group(1))
            continue
        m = re.match(r"\s*\.word\s+(EXC_VERSION|EXC_SIZE)\b", line)
        if m:
            layout.append((m.group(1), pos))
            pos += 4
            continue
        if re.match(r"\s*\.quad\s+0\b", line):
            layout.append(("quad", pos))
            pos += 8
    check("directive block: magic header + version + size + 7 fields",
          [k for k, _ in layout]
          == ["ascii", "EXC_VERSION", "EXC_SIZE"] + ["quad"] * 7,
          str([k for k, _ in layout]))
    check("directive layout sums to exactly EXC_SIZE", pos == EXC_SIZE,
          str(pos))
    check("word fields sit at +0x08 (version) and +0x0C (size)",
          ("EXC_VERSION", 8) in layout and ("EXC_SIZE", 12) in layout)
    quad_pos = [p for k, p in layout if k == "quad"]
    check("quad directive positions match the .equ field offsets",
          quad_pos == offs, "%s vs %s" % (quad_pos, offs))

    handler = vsrc.split("myos_a8_exc_common:", 1)[-1]
    stur_fields = [m.group(1) for m in re.finditer(
        r"stur\s+\w+\s*,\s*\[\s*\w+\s*,\s*#(EXC_OFF_\w+)\s*\]", handler)]
    dsb_pos = handler.find("dsb")
    field_pos = [handler.find("#" + n + "]") for n in stur_fields]
    check("handler stores exactly the six data fields then the marker",
          stur_fields == list(FIELD_ORDER), str(stur_fields))
    check("ordering barrier sits between data fields and the marker store",
          dsb_pos > 0 and max(field_pos[:-1]) < dsb_pos < field_pos[-1],
          "dsb@%d fields@%s" % (dsb_pos, field_pos))

    rec = bytearray(EXC_SIZE)
    rec[0:8] = src_magic
    struct.pack_into("<I", rec, 8, equs.get("EXC_VERSION", 0))
    struct.pack_into("<I", rec, 12, equs.get("EXC_SIZE", 0))
    synth = {  # clearly synthetic values, never measured anywhere
        "EXC_OFF_SLOT": 0,
        "EXC_OFF_ESR": 0x5A5A0000,
        "EXC_OFF_ELR": 0x00C0FFEE,
        "EXC_OFF_FAR": 0x11111111,
        "EXC_OFF_SPSR": 0x3C5,
        "EXC_OFF_SP": 0x0000DEAD,
    }
    for name, value in synth.items():
        struct.pack_into("<Q", rec, equs[name], value)
    check("synthetic record is exactly EXC_SIZE bytes", len(rec) == EXC_SIZE,
          str(len(rec)))
    check("synthetic record header spells the magic",
          bytes(rec[0:8]) == EXC_MAGIC)
    check("synthetic marker field starts at 0 = not captured",
          rec[equs["EXC_OFF_VALID"]:equs["EXC_SIZE"]]
          == b"\x00" * 8)

    if not (os.path.isfile(RAW_BIN) and os.path.isfile(RAW_ELF)
            and shutil.which("aarch64-linux-gnu-nm")):
        note("build/raw_entry.{bin,elf} or toolchain absent; "
             "artifact cross-check skipped")
    else:
        out = subprocess.run(["aarch64-linux-gnu-nm", RAW_ELF],
                             capture_output=True, text=True).stdout
        m = re.search(r"^([0-9a-fA-F]+)\s+\S\s+myos_a8_exc_record$",
                      out, re.M)
        check("exception record symbol present in the artifact",
              m is not None)
        if m:
            roff = int(m.group(1), 16)
            with open(RAW_BIN, "rb") as fh:
                blob = fh.read()
            check("artifact record offset inside the flat image",
                  roff + EXC_SIZE <= len(blob),
                  "rec=%#x bin=%#x" % (roff, len(blob)))
            if roff + EXC_SIZE <= len(blob):
                check("artifact static header matches the synthetic model",
                      bytes(rec[:16]) == blob[roff:roff + 16],
                      "%s vs %s" % (rec[:16].hex(), blob[roff:roff + 16].hex()))
                check("artifact capture fields and marker are zero in file",
                      blob[roff + 16:roff + EXC_SIZE]
                      == b"\x00" * (EXC_SIZE - 16))

    if FAILURES:
        print("%d synthetic record check(s) FAILED" % len(FAILURES))
        return 1
    print("A8 exception-record model checks: ALL PASS "
          "(synthetic fixture; no execution implied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
