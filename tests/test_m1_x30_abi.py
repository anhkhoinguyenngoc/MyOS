#!/usr/bin/env python3
"""
M1 entry ABI regression: return-address (x30) and stack discipline
(M7.36 fix of M7.35 finding V2; stdlib only; no device; no QEMU).

The M1 payload (platform/apple/a8/m1_entry.S) is entered from the pwned-DFU
hook with `blr x8` (tools/external/gaster/payload_handle_checkm8_request.S:
53) and must return to the instruction after that `blr`, where the hook
stores DONE_MAGIC and the returned x0 (M1_PLAN.md section 6: the PASS
condition requires the hook to "come back" with x0 = ~challenge). Each
`bl boot_diag_*` is a normal C call: AAPCS64 lets it clobber every
caller-saved register (x0-x18) and unconditionally overwrites x30, so the
payload must stash x30 before the first call and restore it before `ret`
(M7.35 finding V2: without that, the final `ret` re-enters the epilogue
in a loop and the hook never resumes).

This test does not pattern-match an `x30` instruction and call it done. It
builds the payload from source with the exact Makefile flags (into a temp
directory; build/ is never written), disassembles it, and runs a small
abstract-interpretation model over every control-flow path of
`myos_m1_entry`:

  * tracks sp relative to the entry sp (must be 0 again at `ret`, and
    16-byte aligned at every `bl`);
  * tracks the value token in each register (entry link, base, challenge,
    clobbered-by-call, ...);
  * models stp/str/ldr/ldp stack traffic into a symbolic stack;
  * asserts at `ret`: x30 still/again holds the ENTRY link on every path,
    sp is balanced, and the returned value is ~challenge;
  * asserts the evidence-window behavior: the marker/challenge slots are
    written with the tokens that survived the call-free window.

Unmodeled instructions inside the function poison the tracked state and
fail the model (fail closed), so the model cannot silently pass code it
does not understand.

A negative-fixture suite compiles known-buggy mutants (the pre-fix
pattern: no x30 stash; restore-after-deallocate; misaligned frame) and
proves the model rejects each one — a test that cannot fail is worthless.

Default mode is tolerant so `make host-test-all` still passes on hosts
without the AArch64 cross toolchain (an explicit NOTE is printed). With
--source PATH the regression can be pointed at any revision of
`m1_entry.S` (e.g. `git show <rev>:platform/apple/a8/m1_entry.S`) to prove
it fails against the original defect.

Exit 0 = all checks pass. Exit 1 = any failure. No physical-boot claim.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_S = os.path.join(REPO, "platform", "apple", "a8", "m1_entry.S")
DIAG_C = os.path.join(REPO, "platform", "apple", "a8", "boot_diag_m1.c")
M1_LD = os.path.join(REPO, "platform", "apple", "a8", "m1.ld")
MAKEFILE = os.path.join(REPO, "Makefile")
GASTER_HOOK = os.path.join(REPO, "tools", "external", "gaster",
                           "payload_handle_checkm8_request.S")

# Exact Makefile flags (CROSSFLAGS); asserted against the Makefile below.
CROSSFLAGS = ["-ffreestanding", "-nostdlib", "-nostartfiles", "-fno-pic",
              "-fno-pie", "-fno-asynchronous-unwind-tables",
              "-fno-unwind-tables", "-Os", "-Wall", "-Wextra"]
CC = "aarch64-linux-gnu-gcc"
LD = "aarch64-linux-gnu-ld"
OBJCOPY = "aarch64-linux-gnu-objcopy"
OBJDUMP = "aarch64-linux-gnu-objdump"

ENTRY_FUNC = "myos_m1_entry"
CALLS = ("boot_diag_init", "boot_diag_begin", "boot_diag_ok")
MARKER = b"MYOSM1OK"

FAILURES = []
NOTES = []
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
    NOTES.append(msg)


# ---------------------------------------------------------------------------
# Disassembly parsing (fail-closed: unknown instructions poison the model)
# ---------------------------------------------------------------------------

INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+(.+?)\s*$")


def parse_disasm(text):
    """objdump -d text -> {func: [(addr, insn_text), ...]}."""
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
            insn = m.group(2).split("//")[0]
            insn = re.sub(r"\s+", " ", insn).strip()
            funcs[cur].append((int(m.group(1), 16), insn))
    return funcs


def _imm(tok):
    tok = tok.strip().lstrip("#")
    return int(tok, 0)


# ---------------------------------------------------------------------------
# Abstract-interpretation model of myos_m1_entry
# ---------------------------------------------------------------------------
#
# Registers hold value tokens:
#   ENTRY_LINK      the hook's link register value at payload entry
#   ARG_BASE        x0 at entry (command block base)
#   ARG_CHALLENGE   x1 at entry (host challenge)
#   CLOBBERED       unknown value written by a C call (AAPCS caller-saved)
#   UNSET           never written in this function
#   UNINIT          read from a stack slot this payload never wrote
#   ("RETTO", a)    link written by `bl` falling through to address a
#   ("MVN", tok)    bitwise NOT of tok
#   ("IMM", v)      immediate
#   ("SYS", name)   system-register read
#   ("SYM", name)   adr address of a label
#   ("MEMSYM", name) load from the label's literal storage
# Stack memory is keyed ("S", offset-from-entry-sp); the command block is
# keyed ("B", "BASE", offset). Any unmodeled instruction poisons every modeled
# register and sp (UNKNOWN) and records a violation, so a path cannot continue
# with stale values and be accepted.

CALLER_SAVED = ["x%d" % i for i in range(19)]
UNKNOWN = "UNMODELED"


class State(object):
    def __init__(self):
        self.pc_idx = 0
        self.sp = 0                      # offset from entry sp
        self.regs = dict((r, "UNSET") for r in CALLER_SAVED +
                         ["x19", "x20", "x29", "x30"])
        self.regs["x0"] = "ARG_BASE"
        self.regs["x1"] = "ARG_CHALLENGE"
        self.regs["x19"] = "HANDLER_X19"  # live in the DFU hook
        self.regs["x20"] = "HANDLER_X20"  # live in the DFU hook
        self.regs["x29"] = "HANDLER_X29"  # hook frame pointer
        self.regs["x30"] = "ENTRY_LINK"
        self.mem = {}
        self.block = {}
        self.violations = []
        # Conditional branches are deliberately over-approximated: both
        # successors are explored.  Record which successor each state took so
        # states from different decisions are never merged, even when their
        # current machine state happens to look identical.
        self.path = ()
        self.calls = 0

    def copy(self):
        s = State.__new__(State)
        s.pc_idx = self.pc_idx
        s.sp = self.sp
        s.regs = dict(self.regs)
        s.mem = dict(self.mem)
        s.block = dict(self.block)
        s.violations = self.violations  # shared log
        s.path = self.path
        s.calls = self.calls
        return s

    def poison(self, why):
        # Fail closed: an instruction outside the model invalidates every
        # tracked value, not only sp/x30.  The violation is still recorded.
        self.sp = UNKNOWN
        for reg in self.regs:
            self.regs[reg] = UNKNOWN
        self.violations.append(why)


def _reg(name):
    name = name.strip()
    if name.startswith("w") and name[1:].isdigit():
        return "x" + name[1:]
    return name


def step(insns, st):
    addr, text = insns[st.pc_idx]
    parts = text.split(" ")
    op = parts[0]
    st.pc_idx += 1
    nxt = st.copy()

    if op == "mov" or op == "movz":
        rd, rs = parts[1].rstrip(","), parts[2]
        rd = _reg(rd)
        if rs.startswith("#"):
            nxt.regs[rd] = ("IMM", _imm(rs))
        elif rs in ("xzr", "wzr"):
            nxt.regs[rd] = ("IMM", 0)
        elif re.match(r"^x?\d+$", rs.lstrip("x")) or rs.startswith("x"):
            nxt.regs[rd] = nxt.regs.get(_reg(rs), "UNSET")
        else:
            nxt.poison("unmodeled mov source: " + text)
    elif op == "mvn":
        rd, rs = _reg(parts[1].rstrip(",")), parts[2]
        nxt.regs[rd] = ("MVN", nxt.regs.get(_reg(rs), "UNSET"))
    elif op == "mrs":
        rd = _reg(parts[1].rstrip(","))
        nxt.regs[rd] = ("SYS", parts[2])
    elif op == "adr":
        rd = _reg(parts[1].rstrip(","))
        nxt.regs[rd] = ("SYM", parts[2])
    elif op == "ubfx":
        rd = _reg(parts[1].rstrip(","))
        nxt.regs[rd] = ("BFX", nxt.regs.get(_reg(parts[2]), "UNSET"))
    elif op == "cmp":
        pass
    elif op == "nop":
        pass
    elif op == "stp":
        m = re.match(r"stp\s+(x\d+), (x\d+), \[sp, #(-?0x[0-9a-f]+|-?\d+)\]!",
                     text)
        if not m:
            nxt.poison("unmodeled stp: " + text)
        else:
            if not isinstance(nxt.sp, int):
                nxt.poison("sp unknown before pre-indexed stp: " + text)
            else:
                nxt.sp += _imm(m.group(3))    # imm is negative: pre-decrement
                if nxt.sp % 16 != 0:
                    nxt.violations.append(
                        "stack misaligned (sp%+d) after: %s" % (nxt.sp, text))
                nxt.mem[("S", nxt.sp)] = nxt.regs[m.group(1)]
                nxt.mem[("S", nxt.sp + 8)] = nxt.regs[m.group(2)]
    elif op == "ldp":
        m = re.match(r"ldp\s+(x\d+), (x\d+), \[sp(?:, #(-?0x[0-9a-f]+|-?\d+))?\]",
                     text)
        if not m:
            nxt.poison("unmodeled ldp: " + text)
        else:
            off = _imm(m.group(3)) if m.group(3) else 0
            base = nxt.sp + off if isinstance(nxt.sp, int) else None
            if base is None:
                nxt.poison("sp unknown before ldp: " + text)
            else:
                nxt.regs[m.group(1)] = nxt.mem.get(("S", base), "UNINIT")
                nxt.regs[m.group(2)] = nxt.mem.get(("S", base + 8), "UNINIT")
    elif op == "str":
        m = re.match(r"str\s+(x\d+), \[sp, #(-?0x[0-9a-f]+|-?\d+)\]", text)
        if m:
            if not isinstance(nxt.sp, int):
                nxt.poison("sp unknown before str: " + text)
            else:
                nxt.mem[("S", nxt.sp + _imm(m.group(2)))] = \
                    nxt.regs[m.group(1)]
        else:
            m = re.match(r"str\s+(x\d+), \[(x\d+), #(-?0x[0-9a-f]+|-?\d+)\]",
                         text)
            if not m:
                nxt.poison("unmodeled str: " + text)
            else:
                base = nxt.regs.get(_reg(m.group(2)), "UNSET")
                if base == "ARG_BASE":
                    nxt.block[("B", "BASE", _imm(m.group(3)))] = \
                        nxt.regs[m.group(1)]
                elif base in ("UNSET", "CLOBBERED", "UNINIT", UNKNOWN):
                    nxt.violations.append(
                        "block store through a bad base pointer: " + text)
                # else: store through a derived pointer; slot not tracked
    elif op == "ldr":
        m = re.match(r"ldr\s+(x\d+), \[sp, #(-?0x[0-9a-f]+|-?\d+)\]", text)
        if m:
            if not isinstance(nxt.sp, int):
                nxt.poison("sp unknown before ldr: " + text)
            else:
                nxt.regs[m.group(1)] = nxt.mem.get(
                    ("S", nxt.sp + _imm(m.group(2))), "UNINIT")
        else:
            m = re.match(r"ldr\s+(x\d+), \[(x\d+)\]", text)
            if not m:
                nxt.poison("unmodeled ldr: " + text)
            else:
                src = nxt.regs.get(_reg(m.group(2)), "UNSET")
                if isinstance(src, tuple) and src[0] == "SYM":
                    nxt.regs[m.group(1)] = ("MEMSYM", src[1])
                else:
                    nxt.regs[m.group(1)] = "UNINIT"
    elif op == "add":
        m = re.match(r"add\s+sp, sp, #(-?0x[0-9a-f]+|-?\d+)$", text)
        if not m:
            nxt.poison("unmodeled add: " + text)
        elif not isinstance(nxt.sp, int):
            nxt.poison("sp unknown before add: " + text)
        else:
            nxt.sp += _imm(m.group(1))
    elif op == "bl":
        if not isinstance(nxt.sp, int) or nxt.sp % 16 != 0:
            nxt.violations.append(
                "stack misaligned at call site (sp%+s): %s"
                % (str(nxt.sp), text))
        for r in CALLER_SAVED:
            nxt.regs[r] = "CLOBBERED"
        nxt.regs["x30"] = ("RETTO", addr + 4)
        nxt.calls += 1
    elif op == "ret":
        if nxt.regs.get("x30") != "ENTRY_LINK":
            nxt.violations.append(
                "ret does not return to the hook: x30=%r (address %#x)"
                % (nxt.regs.get("x30"), addr))
        if nxt.sp != 0:
            nxt.violations.append(
                "sp not restored at ret (sp%+s, address %#x)"
                % (str(nxt.sp), addr))
        if nxt.regs.get("x0") != ("MVN", "ARG_CHALLENGE"):
            nxt.violations.append(
                "return value is not ~challenge at ret: x0=%r"
                % (nxt.regs.get("x0"),))
        for reg, token in (("x19", "HANDLER_X19"),
                           ("x20", "HANDLER_X20"),
                           ("x29", "HANDLER_X29")):
            if nxt.regs.get(reg) != token:
                nxt.violations.append(
                    "callee-saved %s clobbered at ret: %r"
                    % (reg, nxt.regs.get(reg)))
        nxt.pc_idx = None                 # terminal
    elif op == "b":
        m = re.match(r"b\s+([0-9a-f]+)\b", text)
        if m:
            tgt = int(m.group(1), 16)
            for i, (a, _t) in enumerate(insns):
                if a == tgt:
                    nxt.pc_idx = i
                    break
            else:
                nxt.poison("branch target outside the function: " + text)
        else:
            nxt.poison("unmodeled b: " + text)
    elif op.startswith("b."):
        m = re.match(r"b\.\w+\s+([0-9a-f]+)\b", text)
        if not m:
            nxt.poison("unmodeled conditional branch: " + text)
        else:
            # both successors are explored by the worklist
            tgt = int(m.group(1), 16)
            for i, (a, _t) in enumerate(insns):
                if a == tgt:
                    branch = st.copy()
                    branch.pc_idx = i
                    branch.path = st.path + ((addr, "taken"),)
                    nxt.path = st.path + ((addr, "fallthrough"),)
                    return nxt, branch
            nxt.poison("branch target outside the function: " + text)
    else:
        nxt.poison("unmodeled instruction: " + text)
    return nxt, None


def _state_key(st):
    """Sound dedupe key: every value a path can change, plus its history.

    The EL1 branch pair in m1_entry.S differs in x12/x13/x16/x17 and in the
    evidence slots.  Keying only a hand-picked subset (as the pre-M7.39 model
    did) can merge paths whose difference matters later, and the global
    first-call check can then be satisfied by the wrong predecessor.  Include
    every modeled register, stack/block memory, call count, and the recorded
    conditional-branch decisions.  Equal keys therefore imply equal future
    behavior; distinct history is kept distinct even when the current register
    values coincide.
    """
    return (st.pc_idx, str(st.sp), st.calls, st.path,
            tuple(sorted((r, repr(v)) for r, v in st.regs.items())),
            tuple(sorted((repr(k), repr(v)) for k, v in st.block.items())),
            tuple(sorted((repr(k), repr(v)) for k, v in st.mem.items())))


def check_model_key_coverage():
    """Prove the dedupe key cannot hide a changed modeled value/history."""
    base = State()
    variants = []
    for reg in sorted(base.regs):
        other = base.copy()
        other.regs[reg] = ("DIFFERENT", reg)
        variants.append(("register " + reg, other))
    other = base.copy()
    other.path = (("0x1234", "taken"),)
    variants.append(("conditional path history", other))
    other = base.copy()
    other.calls = 1
    variants.append(("call count", other))
    other = base.copy()
    other.sp = 16
    variants.append(("stack pointer", other))
    other = base.copy()
    other.mem[("S", 0)] = "DIFFERENT"
    variants.append(("stack memory", other))
    other = base.copy()
    other.block[("B", "BASE", 0x10)] = "DIFFERENT"
    variants.append(("command-block memory", other))
    other = base.copy()
    other.pc_idx = 1
    variants.append(("program counter", other))
    missing = [name for name, other in variants
               if _state_key(base) == _state_key(other)]
    check("model dedupe key covers every register, memory cell, call count, "
          "and branch history", not missing, ",".join(missing))


def simulate(insns, max_steps=4000):
    """Explore every path; return the union of violations."""
    start = State()
    work = [start]
    seen = set()
    violations = []
    ret_states = 0
    steps = 0
    while work:
        st = work.pop()
        while st.pc_idx is not None:
            steps += 1
            if steps > max_steps:
                violations.append("model exceeded the step budget")
                work = []
                break
            key = _state_key(st)
            if key in seen:
                break
            seen.add(key)
            addr, text = insns[st.pc_idx]
            # Check the save on this path's first call, not once globally.
            # A global flag lets a later, defective branch inherit an earlier
            # branch's successful save and thereby pass falsely.
            if text.startswith("bl ") and st.calls == 0:
                if not any(v == "ENTRY_LINK" for k, v in st.mem.items()):
                    violations.append(
                        "hook return address (x30) not saved to the stack "
                        "before the first call (at %#x)" % addr)
            nxt, branch = step(insns, st)
            violations.extend(nxt.violations)
            nxt.violations = []
            if branch is not None:
                branch.violations = []
                work.append(branch)
            st = nxt
        if st.pc_idx is None:
            ret_states += 1
    if ret_states == 0:
        violations.append("no path reached `ret`")
    # dedupe, keep order
    out, seen_v = [], set()
    for v in violations:
        if v not in seen_v:
            seen_v.add(v)
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Build helpers (exact Makefile flags; never writes into build/)
# ---------------------------------------------------------------------------

def toolchain_ok():
    return all(shutil.which(t) for t in (CC, LD, OBJCOPY, OBJDUMP))


def makefile_crossflags():
    """Parse the CROSSFLAGS value out of the Makefile (continuation-aware)."""
    with open(MAKEFILE, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("CROSSFLAGS"):
            buf = line.split(":=", 1)[1] if ":=" in line else line.split("=", 1)[1]
            j = i
            while buf.rstrip().endswith("\\") and j + 1 < len(lines):
                buf = buf.rstrip().rstrip("\\") + " " + lines[j + 1]
                j += 1
            return buf.split()
    return None


def build_payload(tmp, entry_s):
    """Compile + link the payload ELF/BIN from entry_s into tmp."""
    o_entry = os.path.join(tmp, "m1_entry.o")
    o_diag = os.path.join(tmp, "boot_diag_m1.o")
    elf = os.path.join(tmp, "myos_m1.elf")
    binf = os.path.join(tmp, "myos_m1.bin")
    steps = [
        [CC] + CROSSFLAGS + ["-c", entry_s, "-o", o_entry],
        [CC] + CROSSFLAGS + ["-I.", "-c", DIAG_C, "-o", o_diag],
        [LD, "-T", M1_LD, "--build-id=none", "-o", elf, o_entry, o_diag],
        [OBJCOPY, "-O", "binary", elf, binf],
    ]
    for cmd in steps:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
        if proc.returncode != 0:
            return None, None, (proc.stdout + proc.stderr).strip()[:400]
    return elf, binf, ""


# ---------------------------------------------------------------------------
# Negative fixtures: known-buggy payloads the model must reject
# ---------------------------------------------------------------------------

MUTANT_NO_X30 = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
    .global boot_diag_begin
    .global boot_diag_ok
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-16]!
    bl      boot_diag_init
    mov     x0, #0
    bl      boot_diag_begin
    ldp     x9, x10, [sp]
    mov     x0, #0
    bl      boot_diag_ok
    ldr     x10, [sp, #8]
    add     sp, sp, #16
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_RESTORE_AFTER_DEALLOC = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-32]!
    str     x30, [sp, #16]
    bl      boot_diag_init
    ldr     x10, [sp, #8]
    add     sp, sp, #32
    ldr     x30, [sp, #16]
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_MISALIGNED_FRAME = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-24]!
    str     x30, [sp, #16]
    bl      boot_diag_init
    ldr     x10, [sp, #8]
    ldr     x30, [sp, #16]
    add     sp, sp, #24
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_CLOBBERS_X19 = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-32]!
    str     x30, [sp, #16]
    mov     x19, #0
    bl      boot_diag_init
    ldr     x10, [sp, #8]
    ldr     x30, [sp, #16]
    add     sp, sp, #32
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_X30_CLOBBER = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-32]!
    str     x30, [sp, #16]
    bl      boot_diag_init
    ldr     x10, [sp, #8]
    ldr     x30, [sp, #16]
    mov     x30, #0
    add     sp, sp, #32
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_CONDITIONAL_SKIP_RESTORE = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    stp     x9, x10, [sp, #-32]!
    str     x30, [sp, #16]
    bl      boot_diag_init
    ldr     x10, [sp, #8]
    cmp     x10, #0
    b.eq    1f
    ldr     x30, [sp, #16]
1:  add     sp, sp, #32
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANT_ONE_PATH_UNSAVED_X30 = """\
    .section .text, "ax"
    .global myos_m1_entry
    .type myos_m1_entry, %function
    .global boot_diag_init
myos_m1_entry:
    mov     x9, x0
    mov     x10, x1
    cmp     x9, #0
    b.eq    1f
    stp     x9, x10, [sp, #-32]!
    str     x30, [sp, #16]
1:  bl      boot_diag_init
    ldr     x10, [sp, #8]
    ldr     x30, [sp, #16]
    add     sp, sp, #32
    mvn     x0, x10
    ret
    .size myos_m1_entry, .-myos_m1_entry
"""

MUTANTS = (
    ("pre-fix pattern (no x30 stash at all)", MUTANT_NO_X30,
     "ret does not return to the hook"),
    ("restore after deallocate (reads a dead slot)", MUTANT_RESTORE_AFTER_DEALLOC,
     "ret does not return to the hook"),
    ("misaligned frame (sp not 16-byte aligned at call)", MUTANT_MISALIGNED_FRAME,
     "stack misaligned"),
    ("clobbers handler-owned x19", MUTANT_CLOBBERS_X19,
     "callee-saved x19"),
    ("restores x30 then explicitly clobbers it", MUTANT_X30_CLOBBER,
     "ret does not return to the hook"),
    ("conditional path skips the x30 restore",
     MUTANT_CONDITIONAL_SKIP_RESTORE,
     "ret does not return to the hook"),
    ("one conditional path reaches the first call without saving x30",
     MUTANT_ONE_PATH_UNSAVED_X30,
     "hook return address (x30) not saved to the stack before the first call"),
)


# ---------------------------------------------------------------------------
# Static source checks (toolchain-free)
# ---------------------------------------------------------------------------

def check_source(src):
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    body = code.split("myos_m1_entry:", 1)[-1]
    body = body.split(".size myos_m1_entry", 1)[0] if ".size" in body else body

    check("entry calls exactly the three boot_diag functions",
          sorted(re.findall(r"^\s*bl\s+(\w+)", body, re.M)) == sorted(CALLS),
          ",".join(re.findall(r"^\s*bl\s+(\w+)", body, re.M)))

    stash = re.search(r"str\s+x30\s*,\s*\[sp\s*,\s*#16\]", body)
    restore = re.search(r"ldr\s+x30\s*,\s*\[sp\s*,\s*#16\]", body)
    first_bl = body.find("bl ")
    last_bl = body.rfind("bl ")
    ret = body.rfind("ret")
    check("x30 stashed to the stack", stash is not None)
    check("x30 restored from the stack", restore is not None)
    if stash and restore:
        check("x30 stash precedes the first call",
              body.index(stash.group(0)) < first_bl)
        check("x30 restore follows the last call and precedes ret",
              last_bl < body.index(restore.group(0)) < ret)
    check("the link register is never re-derived by hand",
          re.search(r"^\s*mov\s+x30\s*,", body, re.M) is None)
    check("32-byte frame: stp pre-decrement and add-back agree",
          re.search(r"stp\s+x9, x10, \[sp, #-32\]!", body) is not None
          and re.search(r"add\s+sp, sp, #32", body) is not None)
    check("no stale no-call claim in the header (M7.35 V2)",
          "x19/x20/x30 are not" not in src)


def check_makefile():
    flags = makefile_crossflags()
    check("Makefile CROSSFLAGS parsed", flags is not None)
    if flags is not None:
        check("test flag set matches the Makefile CROSSFLAGS exactly",
              flags == CROSSFLAGS, " ".join(flags))
    mk = open(MAKEFILE, encoding="utf-8").read()
    check("payload entry compiled with $(CROSSFLAGS) (no extra ABI flags)",
          re.search(r"^\$\(BUILD\)/m1_entry\.o:.*\n\t\$\(CROSS_CC\)"
                    r" \$\(CROSSFLAGS\) -c", mk, re.M) is not None)
    check("payload linked with m1.ld via ld (no PIE/dynamic sections)",
          "-T platform/apple/a8/m1.ld --build-id=none" in mk)


def check_hook_model():
    if not os.path.isfile(GASTER_HOOK):
        note("vendored hook source absent; return-target model not "
             "cross-checked")
        return
    src = open(GASTER_HOOK, encoding="utf-8").read()
    check("hook enters the payload with `blr x8` and then stores DONE_MAGIC "
          "and the returned x0 (the contract this regression defends)",
          re.search(r"blr\s+x8", src) is not None
          and re.search(r"stp\s+x8, x0, \[x20\]", src) is not None)


# ---------------------------------------------------------------------------
# Artifact model checks
# ---------------------------------------------------------------------------

def check_model_on_elf(elf, binf, label):
    out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", elf],
                         capture_output=True, text=True)
    if out.returncode != 0:
        check(label + "disassembly", False, out.stderr.strip()[:200])
        return
    funcs = parse_disasm(out.stdout)
    if ENTRY_FUNC not in funcs:
        check(label + "entry function present", False,
              ",".join(sorted(funcs)[:8]))
        return
    insns = funcs[ENTRY_FUNC]
    viol = simulate(insns)
    check(label + "control-flow model: ret returns to the hook, sp balanced, "
          "return value ~challenge", not viol, "; ".join(viol[:4]))

    # behavioral slots: the evidence-window stores must carry the tokens
    # that survived the call-free window (base pointer, challenge echo).
    # Explore every path like the main model does.
    checked_echo = 0
    work = [State()]
    seen = set()
    while work:
        st = work.pop()
        while st.pc_idx is not None:
            key = _state_key(st)
            if key in seen:
                break
            seen.add(key)
            _addr, t = insns[st.pc_idx]
            nxt, branch = step(insns, st)
            if t.startswith("str x10, [x9"):
                checked_echo += 1
                check(label + "challenge echo slot written with the "
                      "surviving challenge token",
                      nxt.block.get(("B", "BASE", 0x28)) == "ARG_CHALLENGE",
                      repr(nxt.regs.get("x10")))
            if branch is not None:
                work.append(branch)
            st = nxt
    if checked_echo == 0:
        check(label + "challenge echo store located in the disassembly",
              False, "no `str x10, [x9, #...]` found")
    if binf and os.path.isfile(binf):
        with open(binf, "rb") as fh:
            blob = fh.read()
        check(label + "marker bytes MYOSM1OK in the flat image",
              MARKER in blob)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    src_path = SOURCE_OVERRIDE or ENTRY_S
    if not os.path.isfile(src_path):
        check("m1_entry.S present", False, src_path)
        return 1
    src = open(src_path, encoding="utf-8").read()
    if SOURCE_OVERRIDE:
        note("using --source %s (regression target under test)" % src_path)
    check_source(src)
    check_makefile()
    check_hook_model()
    check_model_key_coverage()

    if not toolchain_ok():
        if REQUIRE:
            check("AArch64 toolchain available", False, "missing cross tools")
        else:
            note("cross toolchain unavailable; build/model checks skipped")
        return 1 if FAILURES else 0

    tmp = tempfile.mkdtemp(prefix="m1x30_model_")
    try:
        elf, binf, err = build_payload(tmp, src_path)
        check("payload builds from source with the Makefile flags",
              elf is not None, err)
        if elf is not None:
            check_model_on_elf(elf, binf, "")

        # negative fixtures: the model must reject known-buggy payloads
        for name, code, expect in MUTANTS:
            mdir = os.path.join(tmp, "mut_" + re.sub(r"\W+", "_", name)[:24])
            os.makedirs(mdir)
            msrc = os.path.join(mdir, "m1_entry.S")
            with open(msrc, "w", encoding="utf-8") as fh:
                fh.write(code)
            melf, _mb, merr = build_payload(mdir, msrc)
            if melf is None:
                check("mutant compiles: " + name, False, merr)
                continue
            out = subprocess.run(
                [OBJDUMP, "-d", "--no-show-raw-insn", melf],
                capture_output=True, text=True)
            funcs = parse_disasm(out.stdout)
            viol = simulate(funcs.get(ENTRY_FUNC, []))
            check("model rejects mutant: " + name,
                  any(expect in v for v in viol),
                  "expected %r, got %r" % (expect, viol[:2]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print("%d check(s) FAILED" % len(FAILURES))
        return 1
    print("M1 x30 ABI checks: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
