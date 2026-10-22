#!/usr/bin/env python3
"""
AArch64 exception-entry ABI regression: live-argument marshalling
(M7.36 fix of M7.35 finding V1; stdlib only; no device; no QEMU).

arch/aarch64/vectors.S marshals the report arguments for
boot_diag_exception_handler(err_base, exc_class, stage, ctx). The M7.35
finding: the handler preamble loaded x0/x1 first, and the shared macro then
called the C function boot_diag_get_current_stage *before* the final
`bl`, so its return value (the stage, in x0) overwrote base_error — every
exception report carried the stage where the error code belonged (printed
as "CPU.EXCEPTION" instead of "GENERIC.EXCEPTION"; event records stored
stage|0x40). exc_class in x1 only survived because this particular getter
happened not to touch it — an AAPCS-fragile accident.

The fix marshals x0/x1/x2/x3 AFTER the stage getter call. This test
verifies the corrected sequencing on the actual assembled artifact, not by
grepping source text:

  * builds arch/aarch64/vectors.S with the exact KERNEL_CROSSFLAGS into a
    temp directory (build/ is never written);
  * disassembles and runs an abstract-interpretation model over every
    exception handler (sync/irq/fiq/serror), tracking register value
    tokens across the saves and the two calls;
  * asserts at the final `bl boot_diag_exception_handler`:
      - x0 holds the ERR_GENERIC_EXCEPTION literal (loaded after the last
        call that could clobber it; the literal's bytes are cross-checked
        against vectors_defs.h arithmetic);
      - x1 holds the class immediate expected for that vector (0..3);
      - x2 holds the boot-stage token produced by the getter return;
      - x3 points at the exception_context_t pushed on the stack, and the
        four words at that address are ESR/FAR/ELR/SPSR in the C struct order;
      - all 12 pre-decrement saves (ten x0-x18/x30 pairs + the two
        exception-context pairs) happened before the getter call, in the
        exact required order, with sp 16-byte aligned at every call site;
  * verifies the 16 vector-table slots are 128 bytes apart, are 2 KiB
    aligned, and branch to the correct four handlers;
  * verifies the handler parks in a branch-to-self and never returns.

A negative-fixture suite compiles known-buggy mutants (the pre-fix
ordering: arguments set before the getter; getter placed between the
literal load and the call; getter before the register saves; a duplicate
caller-save in place of x30; and reversed context-word order) and proves
the model rejects each one.

Default mode is tolerant so `make host-test-all` still passes on hosts
without the AArch64 cross toolchain. With --source PATH the regression can
be pointed at any revision of vectors.S (e.g.
`git show <rev>:arch/aarch64/vectors.S`) to prove it fails against the
original defect.

Exit 0 = all checks pass. Exit 1 = any failure. No physical-boot claim.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTORS_S = os.path.join(REPO, "arch", "aarch64", "vectors.S")
DEFS_H = os.path.join(REPO, "arch", "aarch64", "vectors_defs.h")
MAKEFILE = os.path.join(REPO, "Makefile")

CC = "aarch64-linux-gnu-gcc"
OBJDUMP = "aarch64-linux-gnu-objdump"


def _find_readelf():
    """Prefer the cross readelf, but accept a host readelf that understands
    AArch64.  The test must not depend on a distro-specific cross prefix."""
    for candidate in ("aarch64-linux-gnu-readelf", "readelf"):
        if shutil.which(candidate):
            return candidate
    return None


READELF = _find_readelf()

# Exact Makefile KERNEL_CROSSFLAGS (+ PLATFORM_DEFS); asserted below.
KERNEL_CROSSFLAGS = [
    "-ffreestanding", "-nostdlib", "-nostartfiles", "-fno-pic", "-fno-pie",
    "-fno-asynchronous-unwind-tables", "-fno-unwind-tables", "-O2", "-Wall",
    "-Wextra", "-mgeneral-regs-only", "-DMYOS_PLATFORM_QEMU_VIRT=1",
]

HANDLERS = {
    "sync_exception_handler": 0x0,
    "irq_exception_handler": 0x1,
    "fiq_exception_handler": 0x2,
    "serror_exception_handler": 0x3,
}
GETTER = "boot_diag_get_current_stage"
EXC_HANDLER = "boot_diag_exception_handler"
SAVES_BEFORE_SYNDROME = 10       # x0..x18,x30 as ten stp pairs
CTX_SAVES = 2                    # elr/spsr + esr/far
SP_PER_SAVE = 16
EXPECTED_SAVE_PAIRS = (
    ("x0", "x1"), ("x2", "x3"), ("x4", "x5"), ("x6", "x7"),
    ("x8", "x9"), ("x10", "x11"), ("x12", "x13"), ("x14", "x15"),
    ("x16", "x17"), ("x18", "x30"),
)
EXPECTED_CTX_SAVE_PAIRS = (("x6", "x7"), ("x4", "x5"))
VECTOR_SLOTS = (
    ("vector_sync_el1t", "sync_exception_handler"),
    ("vector_sync_el1h", "sync_exception_handler"),
    ("vector_sync_el0_64", "sync_exception_handler"),
    ("vector_sync_el0_32", "sync_exception_handler"),
    ("vector_irq_el1t", "irq_exception_handler"),
    ("vector_irq_el1h", "irq_exception_handler"),
    ("vector_irq_el0_64", "irq_exception_handler"),
    ("vector_irq_el0_32", "irq_exception_handler"),
    ("vector_fiq_el1t", "fiq_exception_handler"),
    ("vector_fiq_el1h", "fiq_exception_handler"),
    ("vector_fiq_el0_64", "fiq_exception_handler"),
    ("vector_fiq_el0_32", "fiq_exception_handler"),
    ("vector_serror_el1t", "serror_exception_handler"),
    ("vector_serror_el1h", "serror_exception_handler"),
    ("vector_serror_el0_64", "serror_exception_handler"),
    ("vector_serror_el0_32", "serror_exception_handler"),
)

FAILURES = []
REQUIRE = "--require-toolchain" in sys.argv
SOURCE_OVERRIDE = None
for _i, _a in enumerate(sys.argv):
    if _a == "--source" and _i + 1 < len(sys.argv):
        SOURCE_OVERRIDE = sys.argv[_i + 1]


def check(name, cond, detail=""):
    ok = bool(cond)
    print(("PASS  " if ok else "FAIL  ") + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def note(msg):
    print("NOTE  " + msg)


# ---------------------------------------------------------------------------
# Makefile flag parsing (continuation-aware, $(VAR) substitution)
# ---------------------------------------------------------------------------

def _mk_vars():
    with open(MAKEFILE, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*([A-Z_]+)\s*:=\s*(.*)$", lines[i])
        if m:
            buf = m.group(2)
            j = i
            while buf.rstrip().endswith("\\") and j + 1 < len(lines):
                buf = buf.rstrip().rstrip("\\") + " " + lines[j + 1]
                j += 1
            out[m.group(1)] = buf.split()
            i = j + 1
        else:
            i += 1
    return out


def kernel_flags():
    vars_ = _mk_vars()
    flags = vars_.get("KERNEL_CROSSFLAGS")
    if flags is None:
        return None
    resolved = []
    for tok in flags:
        m = re.match(r"^\$\(([A-Z_]+)\)$", tok)
        if m:
            resolved.extend(vars_.get(m.group(1), []))
        else:
            resolved.append(tok)
    return resolved


# ---------------------------------------------------------------------------
# Static source checks (toolchain-free)
# ---------------------------------------------------------------------------

def check_source(src):
    invocs = re.findall(r"^\s*EXCEPTION_ENTRY_COMMON(.*)$", src, re.M)
    check("four handlers, each passing its EXC_CLASS_* argument",
          len(invocs) == 4
          and sorted(a.strip() for a in invocs)
          == ["EXC_CLASS_FIQ", "EXC_CLASS_IRQ", "EXC_CLASS_SERROR",
              "EXC_CLASS_SYNC"],
          ",".join(a.strip() for a in invocs))
    check("no handler loads x0/x1 itself (arguments are the macro's job)",
          re.search(r"^\s*(adrp|ldr)\s+x0\b", src, re.M) is None
          or src.count("adrp    x0, err_generic_exception") <= 1)

    try:
        body = src.split(".macro EXCEPTION_ENTRY_COMMON", 1)[1]
        body = body.split(".endm", 1)[0]
    except IndexError:
        body = ""
    getter = body.find("bl      boot_diag_get_current_stage")
    marsh = body.find("adrp    x0, err_generic_exception")
    call = body.find("bl      boot_diag_exception_handler")
    check("macro: stage getter runs before the report arguments are loaded",
          0 <= getter < marsh < call,
          "getter=%d marsh=%d call=%d" % (getter, marsh, call))
    check("macro: exception class comes from the macro argument, not a "
          "hardcoded immediate",
          re.search(r"mov\s+x1\s*,\s*\\class_id", body) is not None)
    saves = re.findall(r"stp\s+(x\d+), (x\d+), \[sp, #-16\]!", body)
    check("macro: 12 pre-decrement saves (x0-x18/x30 + 32-byte context)",
          len(saves) == SAVES_BEFORE_SYNDROME + CTX_SAVES, str(len(saves)))
    check("macro: caller-saved save set/order is exactly x0..x18,x30",
          tuple(saves[:SAVES_BEFORE_SYNDROME]) == EXPECTED_SAVE_PAIRS,
          repr(saves[:SAVES_BEFORE_SYNDROME]))
    check("macro: exception context saved in C struct order "
          "(elr/spsr then esr/far on the stack)",
          tuple(saves[SAVES_BEFORE_SYNDROME:]) == EXPECTED_CTX_SAVE_PAIRS,
          repr(saves[SAVES_BEFORE_SYNDROME:]))
    mrs = re.findall(r"mrs\s+(x\d+), (ESR_EL1|FAR_EL1|ELR_EL1|SPSR_EL1)",
                     body)
    check("macro: captures exactly ESR/FAR/ELR/SPSR in that order",
          mrs == [("x4", "ESR_EL1"), ("x5", "FAR_EL1"),
                  ("x6", "ELR_EL1"), ("x7", "SPSR_EL1")], repr(mrs))
    check("macro: handler call is the last call (nothing after it)",
          body.rfind("bl      " + EXC_HANDLER) > getter)
    check("macro: never returns (no eret)",
          re.search(r"^\s*eret\b", body, re.M) is None)
    check("macro: parks in an explicit branch-to-self",
          re.search(r"^\s*b\s+\.\s*$", body, re.M) is not None)

    vector_branches = re.findall(r"^\s*b\s+(\w+)\s*$", src, re.M)
    check("source: 16 vector slots branch to the four handlers (4 each)",
          vector_branches == [target for _slot, target in VECTOR_SLOTS],
          repr(vector_branches))
    vbar_align = (re.search(r"\.align\s+11\b", src) is not None
                  or re.search(r"\.balign\s+2048\b", src) is not None)
    slot_align = re.findall(r"\.(?:align\s+7|balign\s+128)\b", src)
    check("source: VBAR table is 2 KiB aligned and all 16 slots are "
          "128-byte aligned (.align or .balign)",
          vbar_align and len(slot_align) == 16, str(len(slot_align)))
    check("source: vbar_el1_install writes VBAR_EL1 from vbar_el1_table",
          re.search(r"msr\s+VBAR_EL1,\s*x0", src) is not None
          and re.search(r"adr\s+x0,\s*vbar_el1_table", src) is not None)

    defs = open(DEFS_H, encoding="utf-8").read()
    vals = {}
    for m in re.finditer(
            r"#define\s+(EXC_CLASS_\w+|DIAG_SUBSYS_GENERIC|"
            r"DIAG_TYPE_EXCEPTION|ERR_GENERIC_EXCEPTION)\s+"
            r"(\(?(?:0x[0-9a-fA-F]+|\d+))",
            defs):
        vals[m.group(1)] = int(m.group(2).lstrip("("), 0)
    if "ERR_GENERIC_EXCEPTION" not in vals:
        # expression-valued define: (DIAG_SUBSYS_GENERIC | DIAG_TYPE_EXCEPTION)
        vals["ERR_GENERIC_EXCEPTION"] = (
            vals.get("DIAG_SUBSYS_GENERIC", 0)
            | vals.get("DIAG_TYPE_EXCEPTION", 0))
    check("vectors_defs.h: base_error = subsystem|type = 0xF0040",
          vals.get("DIAG_SUBSYS_GENERIC") == 0x000F0000
          and vals.get("DIAG_TYPE_EXCEPTION") == 0x00000040
          and vals.get("ERR_GENERIC_EXCEPTION") == 0x000F0040, str(vals))
    for cls, want in HANDLERS.items():
        key = "EXC_CLASS_" + cls.split("_")[0].upper()
        check("vectors_defs.h: %s == %#x" % (key, want),
              vals.get(key) == want, str(vals.get(key)))


def check_makefile():
    flags = kernel_flags()
    check("Makefile KERNEL_CROSSFLAGS parsed (+PLATFORM_DEFS resolved)",
          flags is not None)
    if flags is not None:
        check("test flag set matches the Makefile KERNEL_CROSSFLAGS exactly",
              flags == KERNEL_CROSSFLAGS, " ".join(flags))
    mk = open(MAKEFILE, encoding="utf-8").read()
    check("vectors.o built from its dedicated rule with KERNEL_CROSSFLAGS",
          re.search(r"^\$\(BUILD\)/vectors\.o: arch/aarch64/vectors\.S "
                    r"arch/aarch64/vectors_defs\.h[^\n]*\n\t\$\(CROSS_CC\)"
                    r" \$\(KERNEL_CROSSFLAGS\) -c", mk, re.M) is not None)


# ---------------------------------------------------------------------------
# Disassembly model
# ---------------------------------------------------------------------------

INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+(.+?)\s*$")


def parse_disasm(text):
    funcs = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"[0-9a-f]+ <([^>]+)>:", line)
        if m:
            cur = m.group(1)
            funcs[cur] = []
            continue
        m = INSN_RE.match(line)
        if m and cur is not None:
            insn = re.sub(r"\s+", " ", m.group(2).split("//")[0]).strip()
            funcs[cur].append((int(m.group(1), 16), insn))
    return funcs


def parse_symbols(text):
    """Parse `readelf -sW` enough to get named symbol addresses."""
    symbols = {}
    for line in text.splitlines():
        m = re.match(r"\s*\d+:\s+([0-9a-fA-F]+)\s+\d+\s+\S+\s+"
                     r"\S+\s+\S+\s+\S+\s+(\S+)", line)
        if m:
            symbols[m.group(2)] = int(m.group(1), 16)
    return symbols


def parse_section_addrs(text):
    """Return {section: (address, alignment)} from `readelf -SW`."""
    sections = {}
    for line in text.splitlines():
        m = re.match(r"\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+"
                     r"([0-9a-fA-F]+)\s+(.*)$", line)
        if not m:
            continue
        fields = m.group(3).split()
        if len(fields) < 2:
            continue
        try:
            sections[m.group(1)] = (int(m.group(2), 16),
                                    int(fields[-1], 0))
        except ValueError:
            continue
    return sections


def section_bytes(elf, section):
    """Read the actual bytes of a section via readelf -x."""
    proc = subprocess.run([READELF, "-x", section, elf],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip()[:160]
    out = bytearray()
    for line in proc.stdout.splitlines():
        m = re.match(r"\s*0x([0-9a-fA-F]+)\s+"
                     r"((?:[0-9a-fA-F]{8}\s*){1,4})", line)
        if not m:
            continue
        for word in m.group(2).split():
            out.extend(bytes.fromhex(word))
    return bytes(out), ""


def decode_branch(word, addr):
    """Decode an unconditional AArch64 B; return None if not a B."""
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return addr + imm * 4


def check_vector_table(elf, symbols):
    """Validate the 16-entry 2 KiB VBAR layout and branch destinations."""
    secs = parse_section_addrs(
        subprocess.run([READELF, "-SW", elf], capture_output=True,
                       text=True).stdout)
    if ".text.vectors" not in secs:
        check("vector table section .text.vectors present", False)
        return
    sec_addr, sec_align = secs[".text.vectors"]
    check(".text.vectors has 2 KiB section alignment",
          sec_align == 0x800, str(sec_align))
    base = symbols.get("vbar_el1_table")
    check("vbar_el1_table symbol present and 2 KiB aligned",
          base is not None and base % 0x800 == 0, str(base))
    if base is None:
        return
    data, err = section_bytes(elf, ".text.vectors")
    if data is None:
        check("vector table bytes readable", False, err)
        return
    for idx, (slot, handler) in enumerate(VECTOR_SLOTS):
        addr = base + idx * 0x80
        check("%s is at VBAR+%#x" % (slot, idx * 0x80),
              symbols.get(slot) == addr,
              "%r != %#x" % (symbols.get(slot), addr))
        off = addr - sec_addr
        if off < 0 or off + 4 > len(data):
            check("%s branch bytes in section" % slot, False,
                  "offset %#x size %#x" % (off, len(data)))
            continue
        word = int.from_bytes(data[off:off + 4], "little")
        target = decode_branch(word, addr)
        want = symbols.get(handler)
        check("%s branches to %s" % (slot, handler),
              target is not None and want is not None and target == want,
              "word=%#x target=%r want=%r" % (word, target, want))


def analyze_handler(insns, class_imm):
    """Model one exception handler; return (violations, facts)."""
    viol = []
    facts = {"saves": 0, "getter": None, "final_call": None,
             "class_imm": None, "sp_at_call": None,
             "save_pairs": [], "mrs": [], "stack": {}}
    sp = 0
    regs = {}
    for idx, (addr, text) in enumerate(insns):
        parts = text.split(" ")
        op = parts[0]
        if op == "stp":
            m = re.match(r"stp\s+(x\d+), (x\d+), \[sp, #(-?\d+)\]!", text)
            if not m or m.group(3) != "-16":
                viol.append("unexpected save form: " + text)
                break
            sp -= SP_PER_SAVE
            facts["saves"] += 1
            facts["save_pairs"].append((m.group(1), m.group(2)))
            if sp % 16 != 0:
                viol.append("stack misaligned after save: " + text)
            facts["stack"][sp] = regs.get(m.group(1), "UNSET")
            facts["stack"][sp + 8] = regs.get(m.group(2), "UNSET")
            regs[m.group(1)] = ("SAVED", sp)
            regs[m.group(2)] = ("SAVED", sp + 8)
        elif op == "mrs":
            rd = parts[1].rstrip(",")
            if rd.startswith("w"):
                rd = "x" + rd[1:]
            regs[rd] = ("SYS", parts[2])
            facts["mrs"].append(parts[2].lower())
        elif op == "bl":
            target = re.search(r"<([^>]+)>", text)
            tname = target.group(1) if target else text.split(" ")[1]
            if tname == GETTER:
                if facts["getter"] is not None:
                    viol.append("stage getter called twice")
                if facts["saves"] != SAVES_BEFORE_SYNDROME + CTX_SAVES:
                    viol.append(
                        "stage getter before the register/context saves "
                        "(%d saves so far): the interrupted-context "
                        "registers would be overwritten first"
                        % facts["saves"])
                facts["getter"] = idx
                sp_before = sp
                if sp % 16 != 0:
                    viol.append("stack misaligned at the stage getter call")
                facts["sp_at_getter"] = sp_before
                for r in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7",
                          "x8", "x9", "x10", "x11", "x12", "x13", "x14",
                          "x15", "x16", "x17", "x18"):
                    regs[r] = "CLOBBERED"
                regs["x0"] = "STAGE"          # documented return value
                regs["x30"] = ("RETTO", addr + 4)
            elif tname == EXC_HANDLER:
                facts["final_call"] = idx
                if sp % 16 != 0:
                    viol.append("stack misaligned at the exception-handler "
                                "call")
                facts["sp_at_call"] = sp
                # ---- live-argument assertions -------------------------
                if not (isinstance(regs.get("x0"), tuple)
                        and regs["x0"][0] == "LITVAL"):
                    viol.append("x0 (base_error) is not loaded from the "
                                "error literal at the handler call: %r — a "
                                "call clobbered it" % (regs.get("x0"),))
                if regs.get("x1") != ("IMM", class_imm):
                    viol.append("x1 (exc_class) is not the class immediate "
                                "%#x at the handler call: %r"
                                % (class_imm, regs.get("x1"),))
                if regs.get("x2") != "STAGE":
                    viol.append("x2 (stage) does not carry the getter "
                                "return at the handler call: %r"
                                % (regs.get("x2"),))
                if regs.get("x3") != "SP":
                    viol.append("x3 (ctx) does not point at sp at the "
                                "handler call: %r" % (regs.get("x3"),))
                ctx_sp = facts["sp_at_call"]
                if isinstance(ctx_sp, int):
                    got = tuple(facts["stack"].get(ctx_sp + off)
                                for off in (0, 8, 16, 24))
                    want = (("SYS", "esr_el1"), ("SYS", "far_el1"),
                            ("SYS", "elr_el1"), ("SYS", "spsr_el1"))
                    if got != want:
                        viol.append(
                            "exception_context_t slots are not in "
                            "ESR/FAR/ELR/SPSR order: %r" % (got,))
                if facts["getter"] is None or facts["getter"] > idx:
                    viol.append("stage getter is not called before the "
                                "exception-handler call")
            else:
                viol.append("unexpected call target: " + text)
        elif op == "mov":
            rd = parts[1].rstrip(",")
            if rd.startswith("w"):
                rd = "x" + rd[1:]
            rs = parts[2]
            if rs.startswith("#"):
                imm = int(rs.lstrip("#"), 0)
                regs[rd] = ("IMM", imm)
                if rd == "x1":
                    facts["class_imm"] = imm
            elif rs == "sp":
                regs[rd] = "SP"
            elif rs.startswith("x") or rs.startswith("w"):
                src = rs
                if src.startswith("w"):
                    src = "x" + src[1:]
                regs[rd] = regs.get(src, "UNSET")
            else:
                viol.append("unmodeled mov source: " + text)
        elif op == "adrp":
            # .o relocs resolve the label to its section symbol, so bind the
            # literal by identity (the adrp pc), not by name.
            regs[parts[1].rstrip(",")] = ("LITADR", addr)
            facts["adrp"] = facts.get("adrp", 0) + 1
        elif op == "ldr":
            m = re.match(r"ldr\s+(x\d+), \[(x\d+)\]", text)
            if m and isinstance(regs.get(m.group(2)), tuple) \
                    and regs[m.group(2)][0] == "LITADR":
                regs[m.group(1)] = ("LITVAL", regs[m.group(2)][1])
                facts["ldr"] = facts.get("ldr", 0) + 1
            else:
                viol.append("unmodeled ldr: " + text)
        elif op == "b":
            m = re.match(r"b\s+([0-9a-f]+)\b", text)
            if m and int(m.group(1), 16) == addr:
                facts["park"] = True          # branch-to-self
            else:
                viol.append("handler branches elsewhere: " + text)
        else:
            viol.append("unmodeled instruction: " + text)
    if facts["final_call"] is None:
        viol.append("handler never calls " + EXC_HANDLER)
    if not facts.get("park"):
        viol.append("handler does not park in a branch-to-self")
    if facts["getter"] is None:
        viol.append("stage getter never called")
    if facts["saves"] != SAVES_BEFORE_SYNDROME + CTX_SAVES:
        viol.append("expected %d saves, found %d"
                    % (SAVES_BEFORE_SYNDROME + CTX_SAVES, facts["saves"]))
    if tuple(facts["save_pairs"][:SAVES_BEFORE_SYNDROME]) != \
            EXPECTED_SAVE_PAIRS:
        viol.append("caller-saved save sequence is incomplete, duplicated, "
                    "or reordered: %r"
                    % (facts["save_pairs"][:SAVES_BEFORE_SYNDROME],))
    if tuple(facts["save_pairs"][SAVES_BEFORE_SYNDROME:]) != \
            EXPECTED_CTX_SAVE_PAIRS:
        viol.append("exception-context save sequence is not ELR/SPSR then "
                    "ESR/FAR: %r"
                    % (facts["save_pairs"][SAVES_BEFORE_SYNDROME:],))
    if facts["mrs"] != ["esr_el1", "far_el1", "elr_el1", "spsr_el1"]:
        viol.append("system-register capture sequence is not "
                    "ESR/FAR/ELR/SPSR: %r" % (facts["mrs"],))
    if facts.get("adrp") != 1 or facts.get("ldr") != 1:
        viol.append("expected exactly one adrp/ldr literal pair, found "
                    "%r/%r" % (facts.get("adrp"), facts.get("ldr")))
    return viol, facts


def check_artifact(elf):
    out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", elf],
                         capture_output=True, text=True)
    if out.returncode != 0:
        check("vectors disassembly", False, out.stderr.strip()[:200])
        return
    funcs = parse_disasm(out.stdout)
    symbols = parse_symbols(
        subprocess.run([READELF, "-sW", elf], capture_output=True,
                       text=True).stdout)
    check_vector_table(elf, symbols)
    for name, cls in sorted(HANDLERS.items()):
        if name not in funcs:
            check(name + " present in the object", False)
            continue
        viol, facts = analyze_handler(funcs[name], cls)
        check("%s: model — args marshalled after the stage getter, saves "
              "complete, parks" % name, not viol, "; ".join(viol[:3]))
        check("%s: exception class immediate %#x" % (name, cls),
              facts.get("class_imm") == cls, str(facts.get("class_imm")))
        check("%s: sp restored-equivalent discipline (sp%+d at the final "
              "call)" % (name, facts.get("sp_at_call") or 0),
              facts.get("sp_at_call")
              == -(SAVES_BEFORE_SYNDROME + CTX_SAVES) * SP_PER_SAVE,
              str(facts.get("sp_at_call")))

    # vbar_el1_install must be unchanged: adr/msr/isb/ret, with the
    # architectural VBAR_EL1 write and no conditional/return path.
    inst = funcs.get("vbar_el1_install", [])
    ops = [t.split(" ")[0] for _a, t in inst]
    check("vbar_el1_install unchanged (adr/msr/isb/ret)",
          ops == ["adr", "msr", "isb", "ret"], ",".join(ops))
    check("vbar_el1_install writes VBAR_EL1 from x0 and synchronizes",
          len(inst) == 4
          and re.search(r"\bmsr vbar_el1, x0\b", inst[1][1]) is not None
          and re.search(r"\badr x0, [0-9a-f]+ <vbar_el1_table>",
                        inst[0][1]) is not None
          and inst[2][1] == "isb",
          repr(inst))

    # the four adrp/ldr literal pairs must target .rodata.vectors (the
    # section that holds the single error literal verified below)
    rel = subprocess.run([READELF, "-rW", elf],
                         capture_output=True, text=True)
    if rel.returncode != 0:
        check("relocation inspection", False, rel.stderr.strip()[:120])
    else:
        adrp_relocs = [ln for ln in rel.stdout.splitlines()
                       if "R_AARCH64_ADR_PREL_PG_HI21" in ln]
        check("all four adrp literal loads target .rodata.vectors + 0 "
              "(the verified 0xF0040 quad)",
              len(adrp_relocs) == 4
              and all(".rodata.vectors + 0" in ln for ln in adrp_relocs),
              "; ".join(adrp_relocs)[:160])
        call_relocs = [ln for ln in rel.stdout.splitlines()
                       if "R_AARCH64_CALL26" in ln]
        check("both calls in each of the four handlers are relocations",
              len(call_relocs) == 8, str(len(call_relocs)))

    # the error literal's actual bytes must equal vectors_defs.h arithmetic
    out2 = subprocess.run([OBJDUMP, "-s", "-j", ".rodata.vectors", elf],
                          capture_output=True, text=True)
    m = re.search(r"Contents of section \.rodata\.vectors:\n\s+[0-9a-f]+\s+"
                  r"([0-9a-f ]+)", out2.stdout)
    if not m:
        check(".rodata.vectors literal present", False,
              out2.stdout.strip()[:120])
    else:
        raw = bytes.fromhex(m.group(1).split()[0][:16])
        val = int.from_bytes(raw[:8], "little")
        check("error literal bytes == ERR_GENERIC_EXCEPTION (0xF0040)",
              val == 0x000F0040, hex(val))


# ---------------------------------------------------------------------------
# Negative fixtures: known-buggy handler orderings the model must reject
# ---------------------------------------------------------------------------

MUTANT_PREFIX = """\
    .section .text.vectors, "ax"
    .align 7
    .global mutant_exception_handler
mutant_exception_handler:
"""

MUTANT_SAVES = """\
    stp     x0, x1, [sp, #-16]!
    stp     x2, x3, [sp, #-16]!
    stp     x4, x5, [sp, #-16]!
    stp     x6, x7, [sp, #-16]!
    stp     x8, x9, [sp, #-16]!
    stp     x10, x11, [sp, #-16]!
    stp     x12, x13, [sp, #-16]!
    stp     x14, x15, [sp, #-16]!
    stp     x16, x17, [sp, #-16]!
    stp     x18, x30, [sp, #-16]!
    mrs     x4, ESR_EL1
    mrs     x5, FAR_EL1
    mrs     x6, ELR_EL1
    mrs     x7, SPSR_EL1
    stp     x6, x7, [sp, #-16]!
    stp     x4, x5, [sp, #-16]!
"""

MUTANT_LIT = """\
    .section .rodata.vectors
    .align 3
err_generic_exception:
    .quad   0x000F0040
    .section .text.vectors, "ax"
"""

# Mutants that the old count-only save check could accept: the same number
# of pre-decrement stores, but not the required register set / context order.
MUTANT_SAVES_DUPLICATE = MUTANT_SAVES.replace(
    "    stp     x18, x30, [sp, #-16]!",
    "    stp     x0, x1, [sp, #-16]!")
MUTANT_SAVES_CONTEXT_REVERSED = MUTANT_SAVES.replace(
    "    stp     x6, x7, [sp, #-16]!\n"
    "    stp     x4, x5, [sp, #-16]!",
    "    stp     x4, x5, [sp, #-16]!\n"
    "    stp     x6, x7, [sp, #-16]!")

MUTANT_BODY = """\
    bl      boot_diag_get_current_stage
    mov     w4, w0
    adrp    x0, err_generic_exception
    ldr     x0, [x0, #:lo12:err_generic_exception]
    mov     x1, #0x0
    mov     x2, x4
    mov     x3, sp
    bl      boot_diag_exception_handler
    b       .
"""

MUTANT_DUPLICATE_SAVE = (
    MUTANT_PREFIX + MUTANT_SAVES_DUPLICATE + MUTANT_BODY + MUTANT_LIT
)

MUTANT_CONTEXT_ORDER = (
    MUTANT_PREFIX + MUTANT_SAVES_CONTEXT_REVERSED + MUTANT_BODY + MUTANT_LIT
)

MUTANT_PRE_FIX = (
    MUTANT_PREFIX + """\
    adrp    x4, err_generic_exception
    ldr     x0, [x4, #:lo12:err_generic_exception]
    mov     x1, #0x0
""" + MUTANT_SAVES + """\
    bl      boot_diag_get_current_stage
    mov     w4, w0
    mov     x2, x4
    mov     x3, sp
    bl      boot_diag_exception_handler
    b       .
""" + MUTANT_LIT
)

MUTANT_GETTER_LAST = (
    MUTANT_PREFIX + MUTANT_SAVES + """\
    adrp    x0, err_generic_exception
    ldr     x0, [x0, #:lo12:err_generic_exception]
    mov     x1, #0x0
    bl      boot_diag_get_current_stage
    mov     x2, x0
    mov     x3, sp
    bl      boot_diag_exception_handler
    b       .
""" + MUTANT_LIT
)

MUTANT_GETTER_FIRST = (
    MUTANT_PREFIX + """\
    bl      boot_diag_get_current_stage
""" + MUTANT_SAVES + """\
    adrp    x0, err_generic_exception
    ldr     x0, [x0, #:lo12:err_generic_exception]
    mov     x1, #0x0
    mov     x2, x0
    mov     x3, sp
    bl      boot_diag_exception_handler
    b       .
""" + MUTANT_LIT
)

MUTANTS = (
    ("pre-fix ordering (args set before the getter)", MUTANT_PRE_FIX,
     "x0 (base_error) is not loaded from the error literal"),
    ("getter placed after the args are marshalled", MUTANT_GETTER_LAST,
     "x0 (base_error) is not loaded from the error literal"),
    ("getter before the register saves", MUTANT_GETTER_FIRST,
     "stage getter before the register/context saves"),
    ("duplicate caller-save instead of x30", MUTANT_DUPLICATE_SAVE,
     "caller-saved save sequence is incomplete, duplicated, or reordered"),
    ("reversed exception-context word order", MUTANT_CONTEXT_ORDER,
     "exception_context_t slots are not in ESR/FAR/ELR/SPSR order"),
)


def toolchain_ok():
    return READELF is not None and all(
        shutil.which(t) for t in (CC, OBJDUMP))


def build_vectors(tmp, src_path):
    elf_obj = os.path.join(tmp, "vectors.o")
    # -I arch/aarch64: a --source temp copy is not next to vectors_defs.h;
    # for the in-tree build the quoted include already resolves there.
    proc = subprocess.run(
        [CC] + KERNEL_CROSSFLAGS
        + ["-I", os.path.join(REPO, "arch", "aarch64"),
           "-c", src_path, "-o", elf_obj],
        capture_output=True, text=True, cwd=REPO)
    if proc.returncode != 0:
        return None, (proc.stdout + proc.stderr).strip()[:400]
    return elf_obj, ""


def main():
    src_path = SOURCE_OVERRIDE or VECTORS_S
    if not os.path.isfile(src_path):
        check("vectors.S present", False, src_path)
        return 1
    src = open(src_path, encoding="utf-8").read()
    if SOURCE_OVERRIDE:
        note("using --source %s (regression target under test)" % src_path)
    check_source(src)
    check_makefile()

    if not toolchain_ok():
        if REQUIRE:
            check("AArch64 toolchain available", False, "missing cross tools")
        else:
            note("cross toolchain unavailable; artifact/model checks skipped")
        return 1 if FAILURES else 0

    tmp = tempfile.mkdtemp(prefix="vecabi_model_")
    try:
        obj, err = build_vectors(tmp, src_path)
        check("vectors.o builds from source with KERNEL_CROSSFLAGS",
              obj is not None, err)
        if obj is not None:
            check_artifact(obj)

        for name, code, expect in MUTANTS:
            mdir = os.path.join(tmp, "mut_" + re.sub(r"\W+", "_", name)[:24])
            os.makedirs(mdir)
            msrc = os.path.join(mdir, "vectors.S")
            with open(msrc, "w", encoding="utf-8") as fh:
                fh.write(code)
            mobj, merr = build_vectors(mdir, msrc)
            if mobj is None:
                check("mutant compiles: " + name, False, merr)
                continue
            out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", mobj],
                                 capture_output=True, text=True)
            funcs = parse_disasm(out.stdout)
            viol, _facts = analyze_handler(
                funcs.get("mutant_exception_handler", []), 0x0)
            check("model rejects mutant: " + name,
                  any(expect in v for v in viol),
                  "expected %r, got %r" % (expect, viol[:3]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("vectors ABI checks: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
