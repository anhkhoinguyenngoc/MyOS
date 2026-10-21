#!/usr/bin/env python3
"""
Apple A8 PongoOS `bootr` RAW entry trampoline: host-side structural and
linked-artifact regression checks (M7.11 contract + M7.12 artifact pipeline
+ M7.13 exception capture; stdlib only; no device; static checks need no
toolchain).

The RAW entry payload (platform/apple/a8/raw_entry.S + raw_main.c +
raw_vectors.S + raw_entry.ld) must keep this contract:

  * entry at file offset 0 (entry.c jumps to the upload-buffer start);
  * position independence: no absolute address anywhere;
  * the image owns its stack: a file-backed .stack region (0x4000 bytes,
    16-byte aligned) that lies inside the uploaded image bytes; the
    inherited stack pointer is replaced before the first stack access;
  * boot_args (x0) is preserved across setup and handed to the C
    continuation in the AAPCS argument register;
  * the C continuation records entry evidence at file offset 0x200, never
    dereferences boot_args, and parks with a branch-to-self (no wfe);
  * M7.13 exception capture: a 2 KiB-aligned EL1 vector table above the
    stack top (16 slots x 128 bytes; every slot classifies itself and
    enters the shared capture handler; zero-padded tails), installed into
    VBAR_EL1 by the entry trampoline with adr/msr/ISB, plus a fixed-size
    file-backed exception record (magic MYOSA8E1, version 1, size 0x48)
    directly after the table and outside the stack; the handler reads
    ESR/ELR/FAR/SPSR first, uses no stack memory, writes the completion
    marker last behind DSB SY, and parks with a branch-to-self; no ERET
    instruction exists anywhere in the image;
  * the image stays within the default 1 MiB PongoOS upload buffer;
  * the kernel/QEMU link stays free of A8 payload objects.

Built-artifact checks (M7.12) inspect the actually linked output: ELF class/
type/entry, the LOAD program header, the section table, the symbol table and
the disassembly, plus a flattened-section equivalence proof against the real
build/raw_entry.bin (every allocated section is extracted from the ELF and
compared with the flat image at its linked address). The layout constants
above mirror raw_main.c and raw_entry.ld; the duplication is explicit and
enforced by cross-checking those sources, the linked ELF and the binary
bytes. A malformed-input suite proves the validator fails closed: missing
artifact, truncated image, wrong magic, oversized image, truncated ELF.

Default mode is tolerant so `make host-test-all` still runs on hosts without
the AArch64 toolchain (an explicit NOTE is printed). With --require-artifact
(used by `make raw-entry-check`) a missing artifact or toolchain is a
FAILURE naming the concrete missing tool; nothing is silently skipped.

Exit 0 = all checks pass. Exit 1 = any failure. The validator asserts only
the file-layout contract; it makes no physical-boot claim.
"""

import contextlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A8 = os.path.join(REPO, "platform", "apple", "a8")

RAW_S = os.path.join(A8, "raw_entry.S")
RAW_C = os.path.join(A8, "raw_main.c")
RAW_VS = os.path.join(A8, "raw_vectors.S")
RAW_LD = os.path.join(A8, "raw_entry.ld")
RAW_ELF = os.path.join(REPO, "build", "raw_entry.elf")
RAW_BIN = os.path.join(REPO, "build", "raw_entry.bin")
MAKEFILE = os.path.join(REPO, "Makefile")

MAGIC = b"MYOSA8R1"
RPT_OFF = 0x200
RPT_SIZE = 0x40
STACK_SIZE = 0x4000
UPLOAD_BUF_BOUND = 0x100000  # default PongoOS upload buffer (usbloader.c)
STACK_END = RPT_OFF + RPT_SIZE + STACK_SIZE  # report+stack prefix end
ENTRY_WORD = bytes.fromhex("f30300aa")  # `mov x19, x0`

# M7.13 exception-capture contract (mirrors raw_vectors.S / raw_entry.ld).
EXC_MAGIC = b"MYOSA8E1"
EXC_VERSION = 1
EXC_SIZE = 0x48
VEC_ALIGN = 0x800  # VBAR_EL1 bits[10:0] are RES0 (2 KiB)
VEC_SLOT = 0x80
VEC_COUNT = 16

KEEP_PATHS = (
    "platform/qemu-virt/start.S",
    "platform/qemu-virt/kernel.ld",
    "platform/apple/a8/m1_entry.S",
    "platform/apple/a8/pongo_probe.S",
    "platform/apple/a8/raw_vectors.S",
    "kernel/main.c",
)

FAILURES = []
REQUIRE_ARTIFACT = "--require-artifact" in sys.argv


def check(name, cond, detail=""):
    ok = bool(cond)
    print(("PASS  " if ok else "FAIL  ") + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def note(msg):
    print("NOTE  " + msg)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def run(tool, args):
    exe = shutil.which(tool)
    if exe is None:
        return None
    proc = subprocess.run([exe, *args], capture_output=True, text=True)
    return proc.stdout + proc.stderr


def no_trailing_ws(path):
    with open(path, "r", encoding="utf-8") as fh:
        return all(line == line.rstrip() for line in fh.read().splitlines())


def check_files():
    check("raw_entry.S exists", os.path.isfile(RAW_S), RAW_S)
    check("raw_main.c exists", os.path.isfile(RAW_C), RAW_C)
    check("raw_vectors.S exists", os.path.isfile(RAW_VS), RAW_VS)
    check("raw_entry.ld exists", os.path.isfile(RAW_LD), RAW_LD)
    for path, label in ((RAW_S, "assembly"), (RAW_C, "C"),
                        (RAW_VS, "vector assembly"), (RAW_LD, "linker script")):
        check("no trailing whitespace in " + label, no_trailing_ws(path))
    for keep in KEEP_PATHS:
        check("existing path intact: " + keep,
              os.path.isfile(os.path.join(REPO, keep)), keep)


def check_assembly(src):
    code = strip_comments(src)
    check("entry symbol global+label",
          ".global myos_a8_raw_entry" in src
          and re.search(r"^myos_a8_raw_entry:", src, re.M) is not None)

    body = code.split("myos_a8_raw_entry:", 1)[-1]
    body = body.split(".size myos_a8_raw_entry", 1)[0]
    norm = re.sub(r"\s+", " ", body)
    seq = ("mov x19, x0", "adr x9, __stack_top", "mov sp, x9",
           "adr x9, __vectors", "msr vbar_el1, x9", "isb",
           "mov x0, x19", "b myos_a8_raw_main")
    pos = -1
    for ins in seq:
        pos = norm.find(ins, pos + 1)
        if pos < 0:
            break
    check("ordered setup: preserve x0 -> owned stack -> VBAR install "
          "-> ISB -> C arg -> tail branch", pos >= 0)

    no_sp = re.sub(r"mov\s+sp\s*,\s*x9", "", code)
    check("owned stack install is the only sp use",
          re.search(r"\bsp\b", no_sp) is None)
    check("no bl (no return into PongoOS)",
          re.search(r"^\s*bl\b", code, re.M) is None)
    msrs = re.findall(r"\bmsr\s+(\w+)", code)
    check("trampoline system-register writes are exactly one vbar_el1 install",
          msrs == ["vbar_el1"], ",".join(msrs))
    check("no system-register reads in trampoline",
          re.search(r"\bmrs\b", code) is None)
    check("VBAR install is image-relative (adr), not an absolute literal",
          re.search(r"adr\s+x9\s*,\s*__vectors", code) is not None)
    check("wait instructions (wfe/wfi/sev) absent from trampoline",
          re.search(r"\b(wfe|wfi|sev)\b", code) is None)
    bighex = [h for h in re.findall(r"0x[0-9a-fA-F]+", code)
              if int(h, 16) >= 0x10000]
    check("no absolute address literals", not bighex, ",".join(bighex))

    m = re.search(r"\.equ\s+RAW_STACK_SIZE\s*,\s*(0x[0-9a-fA-F]+)", src)
    stack_size = int(m.group(1), 16) if m else None
    check("RAW_STACK_SIZE == 0x4000", stack_size == STACK_SIZE, str(stack_size))

    check("stack section progbits",
          re.search(r'\.section\s+\.stack\s*,\s*"aw"\s*,\s*@progbits',
                    src) is not None)
    check("stack symbols global",
          ".global __stack_bottom" in src and ".global __stack_top" in src)
    check("stack 16-byte aligned",
          len(re.findall(r"\.balign\s+16", src)) >= 2)
    try:
        sb = src.index("__stack_bottom:")
        st = src.index("__stack_top:")
    except ValueError:
        sb = st = -1
    check("stack region is a filled .skip block",
          sb >= 0 and st > sb and ".skip RAW_STACK_SIZE" in src[sb:st])

    check("no // comments in assembly", "//" not in src)
    check("no placeholders in assembly",
          re.search(r"\b(TODO|FIXME|XXX)\b", src) is None)
    return stack_size


def check_vectors_source(vsrc):
    vcode = strip_comments(vsrc)
    check("no // comments in vector assembly", "//" not in vsrc)
    check("no placeholders in vector assembly",
          re.search(r"\b(TODO|FIXME|XXX)\b", vsrc) is None)
    check("vector table 2 KiB aligned (.balign 0x800)",
          re.search(r"^\s*\.balign\s+0x800\s*$", vsrc, re.M) is not None)
    slots = re.findall(r"^\s*VEC_SLOT\s+(\d+)\b", vsrc, re.M)
    check("16 VEC_SLOT invocations with indices 0..15",
          [int(s) for s in slots] == list(range(VEC_COUNT)), ",".join(slots))
    check("slot stubs classify with movz x16, #idx",
          re.search(r"movz\s+x16\s*,\s*#\\idx", vsrc) is not None)
    branches = len(re.findall(r"^\s*b\s+myos_a8_exc_common\b", vsrc, re.M))
    check("slot macro branches once to the shared capture handler",
          branches == 1, str(branches))
    check("slot tails are zero-filled to the full slot size",
          re.search(r"\.fill\s+0x80\s+-\s*\(\.\s*-\s*vec_slot_", vsrc)
          is not None)
    check("record is file-backed progbits",
          re.search(r'\.section\s+\.excrecord\s*,\s*"aw"\s*,\s*@progbits',
                    vsrc) is not None)
    check("record magic directive spells MYOSA8E1",
          re.search(r'\.ascii\s+"MYOSA8E1"', vsrc) is not None)
    check("vector contract symbols are global",
          ".global myos_a8_exc_record" in vsrc
          and ".global __vectors" in vsrc
          and ".global myos_a8_exc_common" in vsrc)
    equs = dict(
        (m.group(1), int(m.group(2), 0))
        for m in re.finditer(r"\.equ\s+(EXC_\w+)\s*,\s*(0x[0-9a-fA-F]+|\d+)",
                             vsrc))
    for name, want in (
        ("EXC_SIZE", EXC_SIZE), ("EXC_VERSION", EXC_VERSION),
        ("EXC_OFF_SLOT", 0x10), ("EXC_OFF_ESR", 0x18),
        ("EXC_OFF_ELR", 0x20), ("EXC_OFF_FAR", 0x28),
        ("EXC_OFF_SPSR", 0x30), ("EXC_OFF_SP", 0x38),
        ("EXC_OFF_VALID", EXC_SIZE - 8),
        ("EXC_VALID_MARKER", 1),
    ):
        check("EXC constant %s == %#x" % (name, want),
              equs.get(name) == want, str(equs.get(name)))
    handler = vcode.split("myos_a8_exc_common:", 1)[-1]
    marks = [re.search(pat, handler)
             for pat in (r"#EXC_OFF_SLOT\]", r"#EXC_OFF_SP\]", r"dsb\s+sy",
                         r"#EXC_VALID_MARKER", r"#EXC_OFF_VALID\]")]
    order = [m.start() if m else -1 for m in marks]
    check("handler source stores fields in order, barriers, then the marker",
          min(order) >= 0 and order == sorted(order), str(order))
    check("no exception-return (eret) in vector assembly",
          re.search(r"\beret\b", vcode) is None)
    check("no calls (bl) in vector assembly",
          re.search(r"^\s*bl\b", vcode, re.M) is None)
    check("no wait instructions in vector assembly (wfe/wfi/sev)",
          re.search(r"\b(wfe|wfi|sev)\b", vcode) is None)
    bighex = [h for h in re.findall(r"0x[0-9a-fA-F]+", vsrc)
              if int(h, 16) >= 0x10000]
    check("no absolute address literals in vector assembly", not bighex,
          ",".join(bighex))
    return equs


def check_c(csrc):
    ccode = strip_comments(csrc)
    check("C entry signature",
          re.search(r"void\s+myos_a8_raw_main\s*\(\s*uint64_t\s+boot_args\s*\)",
                    ccode) is not None)

    m = re.search(r"#define\s+RAW_REPORT_SIZE\s+(0x[0-9a-fA-F]+)u?", csrc)
    check("RAW_REPORT_SIZE == 0x40",
          m is not None and int(m.group(1), 16) == RPT_SIZE)

    try:
        body = csrc.split("typedef struct raw_entry_report {", 1)[1]
        body = body.split("} raw_entry_report_t;", 1)[0]
    except IndexError:
        body = ""
    for pat, label in (
        (r"\bchar\s+magic\[8\]\s*;", "magic[8]"),
        (r"\buint32_t\s+version\s*;", "version"),
        (r"\buint32_t\s+report_size\s*;", "report_size"),
        (r"\buint64_t\s+boot_args\s*;", "boot_args"),
        (r"\buint64_t\s+sp_at_entry\s*;", "sp_at_entry"),
        (r"\buint64_t\s+stack_bottom\s*;", "stack_bottom"),
        (r"\buint64_t\s+stack_top\s*;", "stack_top"),
        (r"\buint64_t\s+stack_checks\s*;", "stack_checks"),
        (r"\buint64_t\s+reserved\s*;", "reserved"),
    ):
        check("report field: " + label, re.search(pat, body) is not None)
    check("report has six u64 fields",
          len(re.findall(r"\buint64_t\s+\w+\s*;", body)) == 6)
    for off in ("+0x00", "+0x08", "+0x0C", "+0x10", "+0x18",
                "+0x20", "+0x28", "+0x30", "+0x38"):
        check("documented offset " + off, off in body)

    chars = ""
    m = re.search(r"\.magic\s*=\s*\{([^}]*)\}", csrc)
    if m:
        chars = "".join(re.findall(r"'([^'])'", m.group(1)))
    check("magic initializer spells MYOSA8R1", chars == MAGIC.decode(), chars)

    check("compile-time report size assert",
          re.search(r"_Static_assert\s*\(\s*sizeof\s*\(\s*raw_entry_report_t\s*\)"
                    r"\s*==\s*RAW_REPORT_SIZE", csrc) is not None)
    check("report placed in .report", 'section(".report")' in csrc)
    check("report object is volatile",
          re.search(r"volatile\s+raw_entry_report_t\s+raw_entry_report",
                    csrc) is not None)

    flags = dict((m.group(1), int(m.group(2))) for m in re.finditer(
        r"#define\s+(RAW_STACK_CHK_\w+)\s+\(1u\s*<<\s*(\d+)\)", ccode))
    check("three distinct stack check bits",
          sorted(flags.values()) == [0, 1, 2], str(sorted(flags.values())))
    for name in sorted(flags):
        check("stack check bit used: " + name,
              re.search(r"checks\s*\|=\s*" + name, ccode) is not None)

    check("boot_args never dereferenced",
          re.search(r"(\*\s*boot_args|boot_args\s*\[|boot_args\s*->)",
                    ccode) is None)
    check("sp sampled via inline asm",
          re.search(r"mov\s+%0\s*,\s*sp", ccode) is not None)
    check("branch-to-self park loop",
          re.search(r"for\s*\(\s*;\s*;\s*\)", ccode) is not None)
    check("no system-register access in C",
          re.search(r"\b(mrs|msr)\b", ccode) is None)
    check("no wait instructions in C (wfe/wfi/sev)",
          re.search(r"\b(wfe|wfi|sev)\b", ccode) is None)

    bighex = [h for h in re.findall(r"0x[0-9a-fA-F]+", ccode)
              if int(h, 16) >= 0x10000]
    check("no absolute address literals in C", not bighex, ",".join(bighex))
    check("no // comments in C", "//" not in csrc)
    check("no placeholders in C",
          re.search(r"\b(TODO|FIXME|XXX)\b", csrc) is None)


def check_ld(ld, stack_size_from_s):
    check("ENTRY(myos_a8_raw_entry)", "ENTRY(myos_a8_raw_entry)" in ld)
    check("link base is 0x0",
          re.search(r"^\s*\.\s*=\s*0x0\s*;", ld, re.M) is not None)
    check("ASSERT entry == 0",
          re.search(r"ASSERT\s*\(\s*myos_a8_raw_entry\s*==\s*0x0",
                    ld) is not None)
    check("ASSERT report == 0x200",
          re.search(r"ASSERT\s*\(\s*raw_entry_report\s*==\s*0x200",
                    ld) is not None)
    m = re.search(r"ASSERT\s*\(\s*__stack_top\s*-\s*__stack_bottom\s*==\s*"
                  r"(0x[0-9a-fA-F]+)", ld)
    ld_stack = int(m.group(1), 16) if m else None
    check("ASSERT stack size == RAW_STACK_SIZE",
          ld_stack is not None and ld_stack == stack_size_from_s)
    check("ASSERT stack alignment x2",
          len(re.findall(r"ASSERT\s*\(\s*\(__stack_(?:bottom|top)\s*&\s*0xf\)"
                         r"\s*==\s*0", ld)) == 2)
    check("ASSERT stack above report",
          re.search(r"ASSERT\s*\(\s*__stack_bottom\s*>=\s*raw_entry_report\s*"
                    r"\+\s*0x40", ld) is not None)
    check("ASSERT vector table VBAR_EL1 2 KiB alignment",
          re.search(r"ASSERT\s*\(\s*\(__vectors\s*&\s*0x7ff\)\s*==\s*0", ld)
          is not None)
    check("ASSERT vector table size == 16 slots of 128 bytes",
          re.search(r"ASSERT\s*\(\s*SIZEOF\s*\(\s*\.vectors\s*\)\s*==\s*"
                    r"(0x[0-9a-fA-F]+)", ld) is not None
          and int(re.search(r"ASSERT\s*\(\s*SIZEOF\s*\(\s*\.vectors\s*\)"
                            r"\s*==\s*(0x[0-9a-fA-F]+)", ld).group(1), 16)
          == VEC_ALIGN)
    check("ASSERT vector table not overlapping the stack",
          re.search(r"ASSERT\s*\(\s*__vectors\s*>=\s*__stack_top", ld)
          is not None)
    check("ASSERT exception record follows the vector table",
          re.search(r"ASSERT\s*\(\s*myos_a8_exc_record\s*==\s*__vectors_end",
                    ld) is not None)
    check("ASSERT exception record 8-byte aligned",
          re.search(r"ASSERT\s*\(\s*\(\s*myos_a8_exc_record\s*&\s*7\)\s*==\s*0",
                    ld) is not None)
    check("ASSERT exception record outside the stack region",
          re.search(r"ASSERT\s*\(\s*myos_a8_exc_record\s*>=\s*__stack_top",
                    ld) is not None)
    m = re.search(r"ASSERT\s*\(\s*SIZEOF\s*\(\s*\.excrecord\s*\)\s*==\s*"
                  r"(0x[0-9a-fA-F]+)", ld)
    check("ASSERT exception record size == EXC_SIZE (0x48)",
          m is not None and int(m.group(1), 16) == EXC_SIZE)
    check("ASSERT 1 MiB upload-buffer bound",
          re.search(r"ASSERT\s*\(\s*\.\s*<=\s*0x100000", ld) is not None)
    check("section order text<report<stack<vectors<excrecord",
          ld.index(".text :") < ld.index(".report 0x200 :")
          < ld.index(".stack :") < ld.index(".vectors ALIGN(0x800) :")
          < ld.index(".excrecord :"))
    check("vector table section collects .vectors with KEEP",
          "KEEP(*(.vectors))" in ld)
    check("exception record section collects .excrecord with KEEP",
          "KEEP(*(.excrecord))" in ld)
    check("discards metadata",
          all(s in ld for s in ("*(.comment)", "*(.note*)",
                                "*(.eh_frame*)", "*(.gnu*)")))
    allowed = {"0x0", "0xf", "0x40", "0x200", "0x4000", "0x100000",
               "0x800", "0x7ff", "0x48"}
    found = set(h.lower() for h in re.findall(r"0x[0-9a-fA-F]+", ld))
    check("no invented load addresses (hex whitelist)",
          found <= allowed, ",".join(sorted(found - allowed)))
    check("no // comments in linker script", "//" not in ld)
    check("no placeholders in linker script",
          re.search(r"\b(TODO|FIXME|XXX)\b", ld) is None)


def check_makefile(mk):
    check("Makefile defines RAW_ELF/RAW_BIN",
          "RAW_ELF := $(BUILD)/raw_entry.elf" in mk
          and "RAW_BIN := $(BUILD)/raw_entry.bin" in mk)
    check("raw-entry target builds RAW_BIN",
          re.search(r"^raw-entry:\s+\$\(RAW_BIN\)\s*$", mk, re.M) is not None)
    check("raw-entry is phony",
          re.search(r"^\.PHONY:.*\braw-entry\b", mk, re.M) is not None)
    m = re.search(r"^\$\(RAW_ELF\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    check("RAW_ELF links with raw_entry.ld",
          m is not None and "-T platform/apple/a8/raw_entry.ld" in m.group(1))
    m = re.search(r"^\$\(BUILD\)/raw_main\.o:[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    check("raw_main.c built with -mgeneral-regs-only",
          m is not None and "-mgeneral-regs-only" in m.group(1))
    m = re.search(r"^\$\(BUILD\)/raw_vectors\.o:\s+platform/apple/a8/"
                  r"raw_vectors\.S[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    check("raw_vectors.S compiled from its dedicated rule",
          m is not None and "$(CROSS_CC)" in m.group(1))
    m = re.search(r"^\$\(RAW_ELF\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    check("RAW_ELF links raw_vectors.o",
          m is not None and "$(BUILD)/raw_vectors.o" in m.group(1)
          and "-o $@" in m.group(1))
    m = re.search(r"^\$\(KERNEL_ELF\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    kern = m.group(1) if m else ""
    check("kernel link free of A8 payload objects",
          m is not None and "raw_entry.o" not in kern
          and "raw_main.o" not in kern and "raw_vectors.o" not in kern)
    m = re.search(r"^all:\s*(.*)$", mk, re.M)
    check("default all target unchanged (no raw-entry)",
          m is not None and "raw-entry" not in m.group(1))
    check("qemu target present", re.search(r"^qemu:", mk, re.M) is not None)

    m = re.search(r"^\$\(PROBE_ELF\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    probe = m.group(1) if m else ""
    check("probe link free of raw objects",
          m is not None and "raw_entry.o" not in probe
          and "raw_main.o" not in probe and "raw_vectors.o" not in probe)
    m = re.search(r"^\$\(PAYLOAD_ELF\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    m1 = m.group(1) if m else ""
    check("M1 payload link free of raw objects",
          m is not None and "raw_entry.o" not in m1
          and "raw_main.o" not in m1 and "raw_vectors.o" not in m1)

    check("raw-entry-check target exists",
          re.search(r"^raw-entry-check:\s+\$\(RAW_BIN\)\s+\$\(RAW_VERIFY_BIN\)"
                    r"\s*$", mk, re.M) is not None)
    check("raw-entry-check is phony",
          re.search(r"^\.PHONY:.*\braw-entry-check\b", mk, re.M) is not None)
    m = re.search(r"^raw-entry-check:[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    recipe = m.group(1) if m else ""
    check("raw-entry-check runs the strict validator",
          "tests/test_a8_raw_entry.py --require-artifact" in recipe)
    check("raw-entry-check byte-compares a second derivation",
          re.search(r"^[ \t]*cmp\s+\$\(RAW_BIN\)\s+\$\(RAW_VERIFY_BIN\)",
                    recipe, re.M) is not None)
    check("raw-entry-check prints its PASS banner only after cmp",
          "=== RAW-ENTRY CHECK: PASS" in recipe
          and recipe.rfind("cmp $(RAW_BIN)")
          < recipe.rfind("=== RAW-ENTRY CHECK: PASS"))
    stage = recipe.find("artifact validation PASS")
    check("raw-entry-check separates validation and comparison stages",
          0 <= stage < recipe.find("cmp $(RAW_BIN)"))
    m = re.search(r"^\$\(RAW_VERIFY_BIN\):[^\n]*((?:\n[ \t].*)+)", mk, re.M)
    verify = m.group(1) if m else ""
    check("verify rule re-derives with the same script and flags",
          m is not None and "platform/apple/a8/raw_entry.ld" in verify
          and "-mgeneral-regs-only" in verify
          and "$(CROSS_OBJCOPY)" in verify)
    check("verify rule includes raw_vectors.S",
          "platform/apple/a8/raw_vectors.S" in verify)
    check("verify rule writes only into the verify directory",
          "$(RAW_BIN)" not in verify
          and "$(BUILD)/raw_entry.elf" not in verify)


def check_binary(path=None):
    path = path or RAW_BIN
    if not os.path.isfile(path):
        if REQUIRE_ARTIFACT:
            check("raw artifact binary present", False,
                  "%s missing; run `make raw-entry`" % path)
        else:
            note("build/raw_entry.bin absent; run `make raw-entry` "
                 "for binary checks")
        return None
    for dep in (RAW_S, RAW_C, RAW_VS, RAW_LD):
        if (os.path.isfile(dep)
                and os.path.getmtime(dep) > os.path.getmtime(path)):
            check("artifact is not older than its sources", False,
                  "%s is newer than %s; run `make raw-entry`"
                  % (dep, path))
            break
    with open(path, "rb") as fh:
        blob = fh.read()
    check("flat image, not an ELF container", blob[:4] != b"\x7fELF")
    check("binary size <= 1 MiB upload buffer",
          len(blob) <= UPLOAD_BUF_BOUND, "0x%x" % len(blob))
    check("binary covers the report+stack prefix",
          len(blob) >= STACK_END, "0x%x" % len(blob))
    check("entry first instruction is `mov x19, x0`",
          blob[:4] == ENTRY_WORD, blob[:4].hex())
    check("magic at 0x200", blob[RPT_OFF:RPT_OFF + 8] == MAGIC)
    if len(blob) >= RPT_OFF + RPT_SIZE:
        version, size = struct.unpack_from("<II", blob, RPT_OFF + 8)
        check("report version == 1", version == 1, str(version))
        check("report size field == 0x40", size == RPT_SIZE, str(size))
    check("dynamic report fields zero in file",
          blob[RPT_OFF + 16:RPT_OFF + RPT_SIZE] == b"\x00" * (RPT_SIZE - 16))
    check("stack region file-backed and zero-filled",
          blob[RPT_OFF + RPT_SIZE:STACK_END] == b"\x00" * STACK_SIZE)
    return blob


def nm_syms(path):
    out = run("aarch64-linux-gnu-nm", [path]) or ""
    syms = {}
    for line in out.splitlines():
        m = re.match(r"([0-9a-fA-F]+)\s+(\S+)\s+(\S+)", line)
        if m:
            syms[m.group(3)] = int(m.group(1), 16)
    return syms


def check_elf(path=None):
    path = path or RAW_ELF
    if not os.path.isfile(path):
        if REQUIRE_ARTIFACT:
            check("raw artifact ELF present", False,
                  "%s missing; run `make raw-entry`" % path)
        else:
            note("build/raw_entry.elf absent; run `make raw-entry` "
                 "for ELF checks")
        return None, None, None
    missing = [t for t in ("aarch64-linux-gnu-readelf",
                           "aarch64-linux-gnu-nm",
                           "aarch64-linux-gnu-objdump",
                           "aarch64-linux-gnu-objcopy")
               if shutil.which(t) is None]
    if missing:
        if REQUIRE_ARTIFACT:
            check("AArch64 toolchain available", False,
                  "missing tool(s): " + ", ".join(missing))
        else:
            note("cross tools unavailable (%s); ELF checks skipped"
                 % ", ".join(missing))
        return None, None, None

    hdr = run("aarch64-linux-gnu-readelf", ["-h", path]) or ""
    check("ELF64 EXEC AArch64",
          "ELF64" in hdr and "EXEC" in hdr and "AArch64" in hdr)
    m = re.search(r"Entry point address:\s*(0x[0-9a-fA-F]+)", hdr)
    check("ELF entry point == 0x0", m is not None and int(m.group(1), 16) == 0)

    sw = run("aarch64-linux-gnu-readelf", ["-SW", path]) or ""
    secs = {}
    for line in sw.splitlines():
        m = re.match(r"\s*\[\s*(\d+)\]\s+(\S*)\s+(\S+)\s+([0-9a-f]+)\s+"
                     r"([0-9a-f]+)\s+([0-9a-f]+)", line)
        if m and int(m.group(1)) != 0:
            secs[m.group(2)] = (m.group(3), int(m.group(4), 16),
                                int(m.group(6), 16))
    check(".text PROGBITS at 0",
          secs.get(".text", ("", None, None))[0] == "PROGBITS"
          and secs.get(".text", ("", None, None))[1] == 0)
    check(".text fits below the pinned report",
          ".text" in secs and secs[".text"][2] <= RPT_OFF)
    check(".report PROGBITS at 0x200 size 0x40",
          secs.get(".report") == ("PROGBITS", RPT_OFF, RPT_SIZE),
          str(secs.get(".report")))
    check(".stack PROGBITS size 0x4000 after report",
          ".stack" in secs and secs[".stack"][0] == "PROGBITS"
          and secs[".stack"][2] == STACK_SIZE
          and secs[".stack"][1] == secs[".report"][1] + RPT_SIZE,
          str(secs.get(".stack")))
    check("no NOBITS sections (image is fully file-backed)",
          not any(t == "NOBITS" and sz > 0 for t, _, sz in secs.values()))
    forbidden = (".interp", ".dynamic", ".dynsym", ".dynstr", ".got",
                 ".got.plt", ".plt", ".hash", ".gnu.hash", ".bss")
    check("no dynamic/GOT/bss sections",
          not any(n in secs for n in forbidden))
    check("no relocation sections",
          not any(n.startswith(".rela") or n.startswith(".rel.")
                  for n in secs))
    alloc = sorted(n for n, (t, _, _) in secs.items() if t == "PROGBITS")
    check("allocated sections are exactly "
          ".text/.report/.stack/.vectors/.excrecord",
          alloc == [".excrecord", ".report", ".stack", ".text", ".vectors"],
          ",".join(alloc))

    syms = nm_syms(path)
    check("entry symbol at 0", syms.get("myos_a8_raw_entry") == 0)
    check("report symbol at 0x200", syms.get("raw_entry_report") == RPT_OFF)
    sb = syms.get("__stack_bottom")
    st = syms.get("__stack_top")
    check("stack symbols present", sb is not None and st is not None)
    if sb is not None and st is not None:
        check("stack symbols 16-byte aligned", sb % 16 == 0 and st % 16 == 0)
        check("stack span == 0x4000", st - sb == STACK_SIZE)
        check("stack starts at/after report end", sb >= RPT_OFF + RPT_SIZE)
    uout = run("aarch64-linux-gnu-nm", ["-u", path]) or ""
    check("no undefined symbols", uout.strip() == "", uout.strip())

    dout = run("aarch64-linux-gnu-objdump",
               ["-d", "--no-show-raw-insn", path]) or ""
    ins = []
    for line in dout.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+(.+?)\s*$", line)
        if m:
            ins.append((int(m.group(1), 16),
                        re.sub(r"\s+", " ", m.group(2)).strip()))
    want = (
        r"^mov x19, x0$",
        r"^adr x9, [0-9a-f]+ <__stack_top>$",
        r"^mov sp, x9$",
        r"^adr x9, [0-9a-f]+ <__vectors>$",
        r"^msr vbar_el1, x9$",
        r"^isb$",
        r"^mov x0, x19$",
        r"^b [0-9a-f]+ <myos_a8_raw_main>$",
    )
    seq_ok = (len(ins) >= len(want) and ins[0][0] == 0
              and all(re.match(p, ins[i][1]) for i, p in enumerate(want)))
    check("disassembly entry sequence "
          "(preserve x0 -> stack -> VBAR -> ISB -> C arg -> tail branch)",
          seq_ok, "; ".join(t for _, t in ins[:8]))
    sp_idx = next((i for i, (_, t) in enumerate(ins) if t == "mov sp, x9"),
                  None)
    check("no inherited stack access before sp install",
          sp_idx is not None
          and all(re.search(r"\bsp\b", t) is None
                  for _, t in ins[:sp_idx]))
    park = False
    for addr, txt in ins:
        m = re.match(r"^b ([0-9a-f]+) <", txt)
        if m and int(m.group(1), 16) == addr:
            park = True
    check("branch-to-self park present", park)
    check("no bl calls in image",
          all(not t.startswith("bl ") for _, t in ins))
    return secs, syms, ins


def check_artifact(blob, secs, syms, ins, elf=None, exc_equs=None):
    if blob is None or secs is None:
        return
    elf = elf or RAW_ELF
    objcopy = shutil.which("aarch64-linux-gnu-objcopy")
    if objcopy is None:
        note("objcopy unavailable; flattened-section equivalence skipped")
        return

    lw = run("aarch64-linux-gnu-readelf", ["-lW", elf]) or ""
    loads = re.findall(
        r"^\s+LOAD\s+0x[0-9a-f]+\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+"
        r"(0x[0-9a-f]+)\s+(0x[0-9a-f]+)", lw, re.M)
    check("exactly one LOAD segment", len(loads) == 1, str(len(loads)))
    if len(loads) == 1:
        vaddr, paddr, filesz, memsz = (int(g, 16) for g in loads[0])
        check("LOAD segment mirrors the flat image "
              "(vaddr/paddr 0, filesz == memsz == file size)",
              vaddr == 0 and paddr == 0 and filesz == memsz
              and filesz == len(blob),
              "va=%#x pa=%#x fs=%#x ms=%#x bin=%#x"
              % (vaddr, paddr, filesz, memsz, len(blob)))

    tmp = tempfile.mkdtemp(prefix="a8raw_flat_")
    try:
        expected = bytearray(len(blob))
        complete = True
        for name, (stype, addr, size) in sorted(secs.items()):
            if stype != "PROGBITS":
                continue
            out = os.path.join(tmp, name.strip(".") + ".bin")
            proc = subprocess.run(
                [objcopy, "-O", "binary", "--only-section=" + name,
                 elf, out], capture_output=True, text=True)
            if proc.returncode != 0:
                check("extract section " + name, False,
                      (proc.stderr or proc.stdout).strip()[:120])
                complete = False
                continue
            with open(out, "rb") as fh:
                data = fh.read()
            fits = len(data) == size and addr + size <= len(blob)
            check("flat image carries section %s at 0x%x (%#x bytes)"
                  % (name, addr, size),
                  fits and blob[addr:addr + size] == data)
            if fits:
                expected[addr:addr + size] = data
            else:
                complete = False
        if complete:
            check("flat image equals the linked sections at their addresses",
                  bytes(expected) == blob)
    finally:
        shutil.rmtree(tmp)

    rpt = syms.get("raw_entry_report")
    if rpt is not None:
        check("magic at the linked report symbol offset",
              blob[rpt:rpt + 8] == MAGIC)
    sb = syms.get("__stack_bottom")
    st = syms.get("__stack_top")
    if sb is not None and st is not None:
        check("stack symbols describe a bounded region inside the flat image",
              sb < st and st <= len(blob)
              and blob[sb:st] == b"\x00" * (st - sb),
              "sb=%#x st=%#x bin=%#x" % (sb, st, len(blob)))
    main_addr = syms.get("myos_a8_raw_main")
    br = next((t for _, t in ins
               if re.match(r"^b [0-9a-f]+ <myos_a8_raw_main>$", t)), None)
    if main_addr is not None and br is not None:
        target = int(re.match(r"^b ([0-9a-f]+)", br).group(1), 16)
        check("entry tail branch resolves to the linked C entry",
              target == main_addr,
              "branch -> %#x, nm -> %#x" % (target, main_addr))

    # --- M7.13 exception capture: vector table + record contract --------
    exc_equs = exc_equs or {}
    st = syms.get("__stack_top")
    vec = syms.get("__vectors")
    vec_end = syms.get("__vectors_end")
    rec = syms.get("myos_a8_exc_record")
    rec_end = syms.get("myos_a8_exc_record_end")
    common = syms.get("myos_a8_exc_common")
    check("vector/handler/record symbols present",
          None not in (vec, vec_end, rec, rec_end, common))
    if None not in (vec, vec_end, rec, rec_end, common):
        check("vector table satisfies VBAR_EL1 2 KiB alignment",
              vec % VEC_ALIGN == 0, hex(vec))
        check("vector table size == 16 slots of 128 bytes",
              vec_end - vec == VEC_ALIGN, hex(vec_end - vec))
        check("vector table and record live inside the flat image",
              0 <= vec < vec_end <= rec <= rec_end <= len(blob),
              "vec=%#x rec_end=%#x bin=%#x" % (vec, rec_end, len(blob)))
        check("exception record offset follows the vector table",
              rec == vec_end and rec_end == rec + EXC_SIZE,
              "rec=%#x vec_end=%#x rec_end=%#x" % (rec, vec_end, rec_end))
        check("image ends exactly at the exception record end",
              len(blob) == rec_end, "0x%x" % len(blob))
        if st is not None:
            check("vector table and record do not overlap the stack region",
                  vec >= st and rec >= st, "st=%#x vec=%#x rec=%#x"
                  % (st, vec, rec))
            expect_vec = (st + VEC_ALIGN - 1) & ~(VEC_ALIGN - 1)
            check("only gap is the VBAR alignment pad (stack top -> vectors)",
                  vec == expect_vec, "vec=%#x expect=%#x" % (vec, expect_vec))
            check("alignment pad bytes are zero-filled",
                  blob[st:vec] == b"\x00" * (vec - st))
        if rec + EXC_SIZE <= len(blob):
            check("exception record magic in the flat image",
                  blob[rec:rec + 8] == EXC_MAGIC, blob[rec:rec + 8].hex())
            ver, size = struct.unpack_from("<II", blob, rec + 8)
            check("record version == 1", ver == EXC_VERSION, str(ver))
            check("record size field == EXC_SIZE (0x48)",
                  size == EXC_SIZE, str(size))
            check("record capture fields zero in file (deterministic state)",
                  blob[rec + 0x10:rec + EXC_SIZE]
                  == b"\x00" * (EXC_SIZE - 0x10))
            bad = []
            for i in range(VEC_COUNT):
                off = vec + i * VEC_SLOT
                w0, w1 = struct.unpack_from("<II", blob, off)
                imm26 = w1 & 0x03FFFFFF
                if imm26 & 0x02000000:
                    imm26 -= 0x04000000
                movz_ok = ((w0 & 0xFFE0001F) == 0xD2800010
                           and ((w0 >> 5) & 0xFFFF) == i)
                branch_ok = ((w1 & 0xFC000000) == 0x14000000
                             and off + 4 + imm26 * 4 == common)
                tail_ok = (blob[off + 8:off + VEC_SLOT]
                           == b"\x00" * (VEC_SLOT - 8))
                if not (movz_ok and branch_ok and tail_ok):
                    bad.append(str(i))
            check("all 16 vector slots classify and branch to the handler "
                  "with zero-filled tails", not bad,
                  "slots: " + ",".join(bad))

    if common is not None and ins:
        hslice = [(a, t) for a, t in ins if common <= a < RPT_OFF]
        hins = [t for _, t in hslice]
        regs = sorted(t.split(",", 1)[1].strip()
                      for t in hins if t.startswith("mrs "))
        check("handler reads exactly ESR/ELR/FAR/SPSR_EL1",
              regs == ["elr_el1", "esr_el1", "far_el1", "spsr_el1"],
              ",".join(regs))
        check("handler performs no system-register writes",
              not any(t.startswith("msr ") for t in hins))
        stur_idx = [i for i, t in enumerate(hins) if t.startswith("stur ")]
        mrs_idx = [i for i, t in enumerate(hins) if t.startswith("mrs ")]
        sample_idx = next((i for i, t in enumerate(hins)
                           if t == "mov x13, sp"), None)
        repoint_idx = next((i for i, t in enumerate(hins)
                            if t == "mov sp, x14"), None)
        check("handler reads all state and samples sp before any store",
              len(stur_idx) == 7 and bool(mrs_idx)
              and max(mrs_idx) < stur_idx[0]
              and sample_idx is not None and sample_idx < stur_idx[0]
              and repoint_idx is not None and sample_idx < repoint_idx,
              "mrs@%s sp@%s stur@%s" % (mrs_idx, sample_idx, stur_idx))
        dsb_idx = [i for i, t in enumerate(hins) if t.startswith("dsb ")]
        check("six field stores, DSB SY barrier, then the marker store",
              len(stur_idx) == 7 and dsb_idx == [stur_idx[5] + 1]
              and stur_idx[6] == dsb_idx[0] + 2,
              "stur@%s dsb@%s" % (stur_idx, dsb_idx))
        offs = []
        for i in stur_idx:
            m = re.search(r"\[x15, #(0x[0-9a-fA-F]+|\d+)\]", hins[i])
            offs.append(int(m.group(1), 0) if m else None)
        want_offs = [exc_equs.get("EXC_OFF_SLOT"),
                     exc_equs.get("EXC_OFF_ESR"),
                     exc_equs.get("EXC_OFF_ELR"),
                     exc_equs.get("EXC_OFF_FAR"),
                     exc_equs.get("EXC_OFF_SPSR"),
                     exc_equs.get("EXC_OFF_SP"),
                     exc_equs.get("EXC_OFF_VALID")]
        check("handler store offsets match raw_vectors.S EXC constants",
              offs == want_offs, str(offs))
        park_a, park_t = hslice[-1]
        m = re.match(r"^b ([0-9a-f]+) <", park_t)
        check("handler ends in a stable branch-to-self park",
              m is not None and int(m.group(1), 16) == park_a, park_t)
        check("handler never returns (no ret in handler)",
              not any(re.match(r"^ret\b", t) for t in hins))
    check("no exception-return (eret) anywhere in the image",
          not any(re.match(r"^eret\b", t) for _, t in ins))
    check("no wait instructions in image (wfe/wfi/sev)",
          not any(re.search(r"\b(wfe|wfi|sev)\b", t) for _, t in ins))


def _elf_internals(data):
    """Defensive ELF64-LE section-header walk for the negative tests."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not an ELF64 little-endian file")
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    if (e_shentsize < 64 or e_shnum == 0 or e_shoff < 64
            or e_shoff + e_shnum * e_shentsize > len(data)):
        raise ValueError("bad section header table")
    sections = []
    symtab = None
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_type = struct.unpack_from("<I", data, off + 4)[0]
        sh_addr = struct.unpack_from("<Q", data, off + 16)[0]
        sh_offset = struct.unpack_from("<Q", data, off + 24)[0]
        sh_size = struct.unpack_from("<Q", data, off + 32)[0]
        sh_link = struct.unpack_from("<I", data, off + 40)[0]
        sh_entsize = struct.unpack_from("<Q", data, off + 56)[0]
        sections.append((sh_type, sh_addr, sh_offset, sh_size))
        if sh_type == 2:  # SHT_SYMTAB
            symtab = (sh_offset, sh_size, sh_link, sh_entsize or 24)
    return sections, symtab


def elf_patch_symbol(data, name, new_value=None, new_shndx=None):
    """Patch one symbol's st_value/st_shndx (negative-test fixture builder)."""
    data = bytearray(data)
    sections, symtab = _elf_internals(data)
    if symtab is None:
        raise ValueError("no .symtab section")
    soff, ssize, link, entsize = symtab
    if link >= len(sections) or soff + ssize > len(data):
        raise ValueError("symtab out of bounds")
    str_off = sections[link][2]
    for off in range(soff, soff + ssize, entsize):
        st_name = struct.unpack_from("<I", data, off)[0]
        if str_off + st_name >= len(data):
            raise ValueError("symbol name outside the file")
        end = data.find(b"\x00", str_off + st_name)
        if end < 0:
            raise ValueError("unterminated symbol name")
        sname = bytes(data[str_off + st_name:end]).decode("ascii", "replace")
        if sname == name:
            if new_value is not None:
                struct.pack_into("<Q", data, off + 8, new_value)
            if new_shndx is not None:
                struct.pack_into("<H", data, off + 6, new_shndx)
            return bytes(data)
    raise ValueError("symbol %s not found" % name)


def elf_patch_vaddr(data, vaddr, new4):
    """Overwrite one 4-byte instruction at a linked address in the ELF."""
    data = bytearray(data)
    sections, _ = _elf_internals(data)
    for sh_type, sh_addr, sh_offset, sh_size in sections:
        if sh_type == 1 and sh_addr <= vaddr < sh_addr + sh_size:
            fileoff = sh_offset + (vaddr - sh_addr)
            if fileoff + 4 > len(data):
                raise ValueError("patch target outside the file")
            data[fileoff:fileoff + 4] = new4
            return bytes(data)
    raise ValueError("no PROGBITS section covers %#x" % vaddr)


def patch_blob(blob, off, data):
    out = bytearray(blob)
    out[off:off + len(data)] = data
    return bytes(out)


ERET_WORD = bytes.fromhex("e0039fd6")  # eret


def check_validator_rejects_bad_inputs(exc_equs):
    if not (os.path.isfile(RAW_BIN) and os.path.isfile(RAW_ELF)):
        note("artifacts absent; malformed-input suite skipped")
        return
    with open(RAW_BIN, "rb") as fh:
        good = fh.read()
    with open(RAW_ELF, "rb") as fh:
        good_elf = fh.read()
    syms = nm_syms(RAW_ELF)
    for name in ("__vectors", "myos_a8_exc_record", "myos_a8_exc_common",
                 "myos_a8_raw_entry", "__stack_bottom"):
        if name not in syms:
            check("negative suite prerequisites (" + name + ")", False,
                  "symbol missing from the real artifact")
            return
    tmp = tempfile.mkdtemp(prefix="a8raw_neg_")

    def probe(label, blob, elf, expect, mtime=None):
        global REQUIRE_ARTIFACT
        p_bin = os.path.join(tmp, "neg.bin")
        p_elf = os.path.join(tmp, "neg.elf")
        for stale in (p_bin, p_elf):
            if os.path.exists(stale):
                os.remove(stale)
        if blob is not None:
            with open(p_bin, "wb") as fh:
                fh.write(blob)
            if mtime is not None:
                os.utime(p_bin, (mtime, mtime))
        if elf is not None:
            with open(p_elf, "wb") as fh:
                fh.write(elf)
        saved = REQUIRE_ARTIFACT
        REQUIRE_ARTIFACT = True
        before = len(FAILURES)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                nb = check_binary(p_bin)
                nsecs, nsyms, nins = check_elf(p_elf)
                check_artifact(nb, nsecs, nsyms, nins, elf=p_elf,
                               exc_equs=exc_equs)
        finally:
            REQUIRE_ARTIFACT = saved
        reason_hit = any(expect in f for f in FAILURES[before:])
        del FAILURES[before:]
        check("validator rejects %s (reason: %s)" % (label, expect),
              reason_hit)

    try:
        probe("missing artifact under --require-artifact", None, None,
              "raw artifact binary present")
        probe("truncated image", good[:0x40], good_elf, "magic at 0x200")
        probe("wrong report magic",
              good[:RPT_OFF] + b"XXXXXXXX" + good[RPT_OFF + 8:], good_elf,
              "magic at 0x200")
        probe("oversized image (over the 1 MiB bound)",
              good + b"\x00" * (UPLOAD_BUF_BOUND + 1 - len(good)), good_elf,
              "binary size <= 1 MiB upload buffer")
        probe("truncated ELF (header only, no sections)", good,
              good_elf[:64], ".text PROGBITS at 0")
        probe("wrong exception-record magic",
              patch_blob(good, syms["myos_a8_exc_record"], b"XXXXXXXX"),
              good_elf, "exception record magic")
        probe("malformed exception-record size field",
              patch_blob(good, syms["myos_a8_exc_record"] + 12,
                         struct.pack("<I", 0x999)),
              good_elf, "record size field")
        probe("corrupted vector-table slot",
              patch_blob(good, syms["__vectors"],
                         b"\x1f\x20\x03\xd5" * 2),
              good_elf, "vector slots classify")
        probe("misaligned vector-table address",
              good, elf_patch_symbol(good_elf, "__vectors",
                                     syms["__vectors"] | 0x40),
              "VBAR_EL1 2 KiB alignment")
        probe("exception record overlapping the stack",
              good, elf_patch_symbol(good_elf, "myos_a8_exc_record",
                                     syms["__stack_bottom"] + 0x200),
              "do not overlap the stack")
        probe("invalid entry-point symbol",
              good, elf_patch_symbol(good_elf, "myos_a8_raw_entry", 0x4),
              "entry symbol at 0")
        probe("unresolved exception handler symbol",
              good, elf_patch_symbol(good_elf, "myos_a8_exc_common",
                                     new_shndx=0),
              "no undefined symbols")
        probe("forbidden exception-return in the handler",
              good, elf_patch_vaddr(good_elf, syms["myos_a8_exc_common"],
                                    ERET_WORD),
              "no exception-return (eret)")
        probe("stale artifact (predates its sources)", good, good_elf,
              "artifact is not older than its sources", mtime=1)
    finally:
        shutil.rmtree(tmp)


def main():
    check_files()
    if FAILURES:
        print("missing files; static checks cannot proceed")
        return 1
    src = read(RAW_S)
    csrc = read(RAW_C)
    vsrc = read(RAW_VS)
    ld = read(RAW_LD)
    mk = read(MAKEFILE)
    stack_size = check_assembly(src)
    exc_equs = check_vectors_source(vsrc)
    check_c(csrc)
    check_ld(ld, stack_size)
    check_makefile(mk)
    blob = check_binary()
    secs, syms, ins = check_elf()
    check_artifact(blob, secs, syms, ins, exc_equs=exc_equs)
    check_validator_rejects_bad_inputs(exc_equs)
    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("A8 RAW entry checks (static + linked artifact): ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
