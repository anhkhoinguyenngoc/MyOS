#!/usr/bin/env python3
"""MyOS M7.14/M7.15 — raw-entry exception-path runtime harness (QEMU emulation).

Runtime evidence only. This harness boots the ACTUAL production EL1 entry
trampoline, vector table and capture handler (platform/apple/a8/raw_entry.S
and raw_vectors.S, linked with the production raw_entry.ld) under
qemu-system-aarch64 `-M virt,secure=off,virtualization=off` (no EL3, no
EL2: the CPU resets and stays at EL1), lets the test-only trigger
(tests/a8_runtime/a8_trigger.S, which replaces raw_main.c in this image
only) raise a controlled AArch64 BRK exception, and reads the exception
record back from guest memory via QMP pmemsave after the REAL handler
marks it complete.

Two run modes:
  - DEFAULT (M7.14 flow, no --shim): pre-exception state is whatever the
    emulator resets to (EL1h, SPSel=1) — see the contract below;
  - SCENARIO runs (M7.15, --shim/--shim-elf/--spsel given): a second
    QEMU loader device starts the CPU inside a test-only boot shim
    (tests/a8_runtime/a8_boot_shim.S) that masks DAIF, selects SP_EL1 via
    `msr spsel,#1`, seeds it with a shim-owned 16-byte-aligned area through
    the selected bank (`mov sp`), then selects the requested pre-exception
    SPSel (0 = SP0, 1 = SPx) and branches to the unmodified image entry.
    The shim is validated fail-closed BEFORE QEMU starts (symbols, size,
    image-entry word, DAIF/SPSel/seed/br disassembly and order), so a
    wrong-scenario or tampered shim can never produce a run. The seed is
    never written with `msr sp_el1`: aarch64 binutils 2.47 assembles that
    instruction to 0xd51c4109 (S3_4_C4_C1_0), which QEMU 11.1.1 TCG rejects
    as an Undefined Instruction (both facts observed in controlled probe
    runs during M7.15); the spsel/mov technique uses only unambiguous
    instructions.

Entry-state contract (emulator-established, observed during the M7.14
capability assessment — NOT claimed for PongoOS or physical n61ap):
  - exception level: EL1 (highest implemented: secure=off, virtualization=off);
    QEMU resets the CPU at EL1h, so SPSel=1 and the active stack is SP_EL1;
  - a synchronous exception taken from EL1 therefore dispatches to vector
    slot 4 (Current EL, SPx, Synchronous) in the default flow; in a scenario
    run the dispatch slot derives from the PRE-exception SPSel the shim set
    (SP0 -> slot 0, SPx -> slot 4) and is asserted against the executed
    vector slot, never hard-coded;
  - MMU off, caches off (SCTLR_EL1 reset; never touched by any code on this
    path; not directly asserted by the harness);
  - DAIF masked at reset (observed PSTATE 0x3c5); asserted from the record's
    captured SPSR_EL1 and, in scenario runs, additionally established by the
    shim itself;
  - initial sp is 0 and never read: raw_entry.S overwrites the active stack
    pointer with the image-owned __stack_top before any stack access, so the
    handler must capture sp == __stack_top exactly (SPx). In the SP0 scenario
    the trampoline writes SP_EL0 only, exception entry selects SP_EL1 for the
    handler (Arm: "When an exception is taken, the stack pointer for the
    target Exception level SP_ELx is automatically selected"), so the record
    must capture the shim-seeded SP_EL1 value instead;
  - x0 (boot-arg pointer) is 0 in the emulator and is not used by the
    trigger; the production bootr x0 semantics are not exercised here.

Live evidence (all re-derived per run; no evidence file is ever read back
as proof):
  1. the exception record bytes read from guest memory after the completion
     marker is set (the record is written only by the real handler);
  2. QEMU's own `-d int` trace: exception class, from/to EL, ESR, SPSR, ELR,
     the dispatched vector PC and the handler-entry PSTATE — cross-checked
     against the record;
  3. the parked PC and PSTATE from QMP `info registers`.

Every missing capability, timeout, malformed reply, stale image or contract
mismatch fails closed with a specific reason. Exit codes: 0 = PASS, 1 =
FAIL. Physical boot/display remain UNVERIFIED; this harness proves
emulation-time behavior of the production exception code only.
"""
import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

EC_BRK = 0x3C
ESR_IL_BIT = 1 << 25
EXPECTED_SLOT = 4
SPSR_MODE_MASK = 0x1F
SPSR_MODE_EL1T = 0x04
SPSR_MODE_EL1H = 0x05
SPSR_DAIF_MASK = 0x3C0
SPSR_IL_BIT = 1 << 20
MAGIC = b"MYOSA8E1"
VERSION = 1
RECORD_SIZE = 0x48
MARKER_OFFSET = 0x40
QEMU_MACHINE = "virt,secure=off,virtualization=off"
QEMU_CPU = "cortex-a57"
QEMU_RAM = "64M"
# Arm exception model: "When an exception is taken, the stack pointer for
# the target Exception level SP_ELx is automatically selected." The
# handler-entry PSTATE is therefore EL1h (PSTATE.SP=1) for BOTH scenarios;
# the pre-exception mode (EL1t vs EL1h) is what SPSR records and what
# selects the SP0/SPx vector group. Observed in the QEMU -d int trace as
# entry PSTATE 0x3c5 against trace SPSR 0x3c4 in the SP0 probe run.
HANDLER_ENTRY_PSTATE_MODE = SPSR_MODE_EL1H
DEFAULT_SHIM_LOAD_ADDR = 0x43000000
SHIM_SYMBOLS = [
    "myos_a8_shim_start",
    "myos_a8_shim_image_entry",
    "myos_a8_shim_sp_area_top",
    "myos_a8_shim_end",
]


class HarnessFailure(Exception):
    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
        super().__init__("%s: %s" % (reason, detail) if detail else reason)


def parse_hex(text):
    return int(text, 16)


def nm_symbols(nm, elf):
    res = subprocess.run([nm, elf], capture_output=True, text=True)
    if res.returncode != 0:
        raise HarnessFailure("symbols", "nm failed on %s: %s" % (elf, res.stderr.strip()))
    syms = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                syms[parts[-1]] = int(parts[0], 16)
            except ValueError:
                continue
    return syms


BRK_RE = re.compile(r"^\s*([0-9a-fA-F]+):\s+[0-9a-fA-F]+\s+brk\s+#(0x[0-9a-fA-F]+)")


def find_brk(objdump, elf):
    res = subprocess.run([objdump, "-d", elf], capture_output=True, text=True)
    if res.returncode != 0:
        raise HarnessFailure("trigger", "objdump failed on %s: %s" % (elf, res.stderr.strip()))
    hits = []
    for line in res.stdout.splitlines():
        m = BRK_RE.match(line)
        if m:
            hits.append((int(m.group(1), 16), int(m.group(2), 16)))
    if len(hits) != 1:
        raise HarnessFailure(
            "trigger",
            "expected exactly one brk instruction in the runtime image, found %d" % len(hits))
    return hits[0]


SHIM_MSR_RE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+msr\s+(\S.*\S)\s*$")
SHIM_BR_RE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+br\s")
SHIM_ADR_RE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+adr\s+(\S+),\s*([0-9a-f]+)")
SHIM_MOVSP_RE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+mov\s+sp,\s*(x\d+)\s*$")


def _shim_imm(op, name):
    if "#" not in op:
        raise HarnessFailure("shim", "cannot parse boot shim operand: msr %s" % name)
    try:
        return int(op.split("#", 1)[1].strip(), 0)
    except ValueError:
        raise HarnessFailure("shim", "cannot parse boot shim immediate: msr %s" % op)


def validate_shim(shim_path, shim_elf, spsel, shim_load_addr, load_addr, nm, objdump):
    """Fail-closed pre-flight of the test-only SPSel boot shim.

    Returns the shim ELF's nm symbols. Every rejection uses reason "shim",
    so a wrong-scenario, mis-linked or tampered shim is refused before QEMU
    ever starts; no partially-validated run can PASS.
    """
    if not os.path.isfile(shim_path):
        raise HarnessFailure("shim", "boot shim binary not found: %s" % shim_path)
    if not os.path.isfile(shim_elf):
        raise HarnessFailure("shim", "boot shim ELF not found: %s" % shim_elf)
    try:
        syms = nm_symbols(nm, shim_elf)
    except HarnessFailure as exc:
        raise HarnessFailure("shim", "nm failed on the boot shim: %s" % exc.detail)
    missing = [name for name in SHIM_SYMBOLS if name not in syms]
    if missing:
        raise HarnessFailure("shim", "boot shim is missing symbols: %s" % ", ".join(missing))
    start = syms["myos_a8_shim_start"]
    if start != shim_load_addr:
        raise HarnessFailure("shim",
                             "boot shim entry is at %#x, expected %#x (linked for a "
                             "different address than --shim-load-addr)"
                             % (start, shim_load_addr))
    with open(shim_path, "rb") as handle:
        blob = handle.read()
    expected_size = syms["myos_a8_shim_end"] - start
    if expected_size <= 0:
        raise HarnessFailure("shim", "boot shim symbols are not ordered (%#x..%#x)"
                             % (start, syms["myos_a8_shim_end"]))
    if len(blob) != expected_size:
        raise HarnessFailure("shim",
                             "boot shim binary is %d bytes, expected %d (link layout does not "
                             "match the flat binary)" % (len(blob), expected_size))
    quad_off = syms["myos_a8_shim_image_entry"] - start
    if quad_off < 0 or quad_off + 8 > len(blob):
        raise HarnessFailure("shim", "boot shim image-entry word lies outside the shim binary")
    quad = struct.unpack_from("<Q", blob, quad_off)[0]
    if quad != load_addr:
        raise HarnessFailure("shim",
                             "boot shim branches to %#x, expected the runtime image base %#x"
                             % (quad, load_addr))
    seed = syms["myos_a8_shim_sp_area_top"]
    if seed & 0xF:
        raise HarnessFailure("shim", "boot shim SP_EL1 seed address %#x is not 16-byte aligned"
                             % seed)
    if not (start < seed <= syms["myos_a8_shim_end"]):
        raise HarnessFailure("shim",
                             "boot shim SP_EL1 seed address %#x lies outside the shim image"
                             % seed)

    res = subprocess.run([objdump, "-d", shim_elf], capture_output=True, text=True)
    if res.returncode != 0:
        raise HarnessFailure("shim", "objdump failed on the boot shim: %s" % res.stderr.strip())
    msr_hits = {}
    br_addrs = []
    adr_hits = []
    movsp_hits = []
    for line in res.stdout.splitlines():
        m = SHIM_BR_RE.match(line)
        if m:
            br_addrs.append(int(m.group(1), 16))
            continue
        m = SHIM_MOVSP_RE.match(line)
        if m:
            movsp_hits.append((int(m.group(1), 16), m.group(2)))
            continue
        m = SHIM_ADR_RE.match(line)
        if m:
            adr_hits.append((int(m.group(1), 16), m.group(2), int(m.group(3), 16)))
            continue
        m = SHIM_MSR_RE.match(line)
        if not m:
            continue
        addr, op = int(m.group(1), 16), m.group(2)
        if op.startswith("spsel,"):
            msr_hits.setdefault("spsel", []).append((addr, _shim_imm(op, "spsel")))
        elif op.startswith("daifset,"):
            msr_hits.setdefault("daifset", []).append((addr, _shim_imm(op, "daifset")))
        else:
            raise HarnessFailure(
                "shim",
                "unexpected msr in the boot shim: %s (the seed must be written through the "
                "selected stack bank, never through a direct SP_EL1 system-register write)"
                % op)
    if len(msr_hits.get("daifset", [])) != 1 or msr_hits["daifset"][0][1] != 0xF:
        raise HarnessFailure("shim",
                             "expected exactly one 'msr daifset, #0xf' in the boot shim, found %d"
                             % len(msr_hits.get("daifset", [])))
    spsel_ops = msr_hits.get("spsel", [])
    if len(spsel_ops) != 2:
        raise HarnessFailure(
            "shim",
            "expected exactly two 'msr spsel' instructions (bank select, then "
            "scenario SPSel) in the boot shim, found %d" % len(spsel_ops))
    if spsel_ops[0][1] != 1:
        raise HarnessFailure("shim",
                             "boot shim first selects SPSel=%d, expected 1 for the SP_EL1 "
                             "seed write" % spsel_ops[0][1])
    if spsel_ops[1][1] != spsel:
        raise HarnessFailure("shim",
                             "boot shim ends with SPSel=%d but the scenario requests SPSel=%d "
                             "(wrong-scenario shim)" % (spsel_ops[1][1], spsel))
    if len(movsp_hits) != 1:
        raise HarnessFailure("shim",
                             "expected exactly one 'mov sp' seed write in the boot shim, "
                             "found %d" % len(movsp_hits))
    if len(br_addrs) != 1:
        raise HarnessFailure("shim",
                             "expected exactly one 'br' in the boot shim, found %d"
                             % len(br_addrs))
    daifset_addr = msr_hits["daifset"][0][0]
    bank_sel_addr = spsel_ops[0][0]
    scenario_sel_addr = spsel_ops[1][0]
    seed_write_addr, seed_reg = movsp_hits[0]
    if not daifset_addr < bank_sel_addr < seed_write_addr < scenario_sel_addr < br_addrs[0]:
        raise HarnessFailure("shim",
                             "boot shim executes daifset@%#x, spsel#1@%#x, mov sp@%#x, "
                             "spsel@%#x, br@%#x; required order is daifset, then the SP_EL1 "
                             "bank select, then the seed write, then the scenario SPSel, "
                             "then the branch"
                             % (daifset_addr, bank_sel_addr, seed_write_addr,
                                scenario_sel_addr, br_addrs[0]))
    seed_adr = [t for a, r, t in adr_hits
                if bank_sel_addr < a < seed_write_addr and t == seed and r == seed_reg]
    if len(seed_adr) != 1:
        raise HarnessFailure("shim",
                             "boot shim does not load myos_a8_shim_sp_area_top (%#x) into %s "
                             "before the seed write" % (seed, seed_reg))
    return syms


def parse_record(data):
    if len(data) != RECORD_SIZE:
        raise HarnessFailure("guest-memory-read",
                             "record dump is %d bytes, expected %d" % (len(data), RECORD_SIZE))
    version, recsize = struct.unpack_from("<II", data, 8)
    slot, esr, elr, far, spsr, sp, marker = struct.unpack_from("<7Q", data, 0x10)
    return {
        "magic": data[0:8],
        "version": version,
        "recsize": recsize,
        "slot": slot,
        "esr": esr,
        "elr": elr,
        "far": far,
        "spsr": spsr,
        "sp": sp,
        "marker": marker,
    }


def validate_record(rec, exp):
    out = []
    if rec["magic"] != MAGIC:
        out.append("record magic %r != %r" % (rec["magic"], MAGIC))
    if rec["version"] != VERSION:
        out.append("record version %d != %d" % (rec["version"], VERSION))
    if rec["recsize"] != RECORD_SIZE:
        out.append("record size field %d != %d" % (rec["recsize"], RECORD_SIZE))
    if rec["slot"] != exp["slot"]:
        out.append("vector slot %d != %d (%s)"
                   % (rec["slot"], exp["slot"], exp["slot_label"]))
    ec = rec["esr"] >> 26
    if ec != EC_BRK:
        out.append("ESR exception class %#x != BRK %#x" % (ec, EC_BRK))
    if not (rec["esr"] & ESR_IL_BIT):
        out.append("ESR IL bit not set (trigger is a 32-bit instruction)")
    if (rec["esr"] & 0xFFFFFF) != exp["imm"]:
        out.append("ESR syndrome ISS %#x != trigger immediate %#x"
                   % (rec["esr"] & 0xFFFFFF, exp["imm"]))
    if rec["elr"] != exp["brk"]:
        out.append("ELR %#x != trigger address %#x" % (rec["elr"], exp["brk"]))
    if (rec["spsr"] & SPSR_MODE_MASK) != exp["spsr_mode"]:
        out.append("SPSR mode bits %#x != %s %#x (pre-exception entry-state contract)"
                   % (rec["spsr"] & SPSR_MODE_MASK, exp["spsr_mode_label"],
                      exp["spsr_mode"]))
    if (rec["spsr"] & SPSR_DAIF_MASK) != SPSR_DAIF_MASK:
        out.append("SPSR DAIF %#x not fully masked at trigger" % (rec["spsr"] & SPSR_DAIF_MASK))
    if rec["spsr"] & SPSR_IL_BIT:
        out.append("SPSR IL bit set at trigger (illegal execution state)")
    if rec["sp"] != exp["sp_expect"]:
        out.append("exception-time stack pointer %#x != %s %#x"
                   % (rec["sp"], exp["sp_label"], exp["sp_expect"]))
    if rec["marker"] != 1:
        out.append("completion marker %d != 1 (handler did not finish)" % rec["marker"])
    return out


INT_TAKE_RE = re.compile(r"^Taking exception (\d+) \[([^\]]+)\]")
INT_FROM_RE = re.compile(r"^\.\.\.from (\S+) to (\S+)")
INT_ESR_RE = re.compile(r"^\.\.\.with ESR (0x[0-9a-fA-F]+)/(0x[0-9a-fA-F]+)")
INT_SPSR_RE = re.compile(r"^\.\.\.with SPSR (0x[0-9a-fA-F]+)")
INT_ELR_RE = re.compile(r"^\.\.\.with ELR (0x[0-9a-fA-F]+)")
INT_FAR_RE = re.compile(r"^\.\.\.with FAR (0x[0-9a-fA-F]+)")
INT_TO_RE = re.compile(r"^\.\.\.to (\S+) PC (0x[0-9a-fA-F]+) PSTATE (0x[0-9a-fA-F]+)")


def parse_int_log(text):
    blocks = []
    cur = None
    for line in text.splitlines():
        m = INT_TAKE_RE.match(line)
        if m:
            cur = {"num": int(m.group(1)), "cls": m.group(2), "from_el": None, "to_el": None,
                   "esr_ec": None, "esr": None, "spsr": None, "elr": None, "far": None,
                   "pc": None, "pstate": None}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        m = INT_FROM_RE.match(line)
        if m:
            cur["from_el"], cur["to_el"] = m.group(1), m.group(2)
            continue
        m = INT_ESR_RE.match(line)
        if m:
            cur["esr_ec"], cur["esr"] = int(m.group(1), 16), int(m.group(2), 16)
            continue
        m = INT_SPSR_RE.match(line)
        if m:
            cur["spsr"] = int(m.group(1), 16)
            continue
        m = INT_ELR_RE.match(line)
        if m:
            cur["elr"] = int(m.group(1), 16)
            continue
        m = INT_FAR_RE.match(line)
        if m:
            cur["far"] = int(m.group(1), 16)
            continue
        m = INT_TO_RE.match(line)
        if m:
            cur["pc"], cur["pstate"] = int(m.group(2), 16), int(m.group(3), 16)
            continue
    return blocks


def validate_int_log(blocks, exp):
    out = []
    if not blocks:
        return ["no exception captured in the QEMU -d int trace"]
    if len(blocks) != 1:
        out.append("%d exceptions in trace, expected exactly 1 (trigger then park, no runaway)"
                   % len(blocks))
    blk = blocks[0]
    if "Breakpoint" not in blk["cls"]:
        out.append("first trace exception is [%s], expected [Breakpoint]" % blk["cls"])
    if blk["from_el"] != "EL1" or blk["to_el"] != "EL1":
        out.append("exception levels %s->%s, expected EL1->EL1" % (blk["from_el"], blk["to_el"]))
    if blk["esr"] != exp["esr"]:
        out.append("trace ESR %#x != expected %#x" % (blk["esr"], exp["esr"]))
    if blk["elr"] != exp["brk"]:
        out.append("trace ELR %#x != trigger address %#x" % (blk["elr"], exp["brk"]))
    if blk["pc"] != exp["vector_pc"]:
        out.append("trace dispatched to vector PC %#x, expected slot vector %#x"
                   % (blk["pc"], exp["vector_pc"]))
    if blk["pstate"] is None:
        out.append("trace exception has no target PSTATE line")
    elif (blk["pstate"] & SPSR_MODE_MASK) != exp["entry_pstate_mode"]:
        out.append("handler-entry PSTATE mode %#x != %s %#x (exception entry selects SP_ELx; "
                   "the pre-exception state is recorded in SPSR)"
                   % (blk["pstate"] & SPSR_MODE_MASK, exp["entry_pstate_label"],
                      exp["entry_pstate_mode"]))
    return out


def cross_check_trace_record(rec, blk, vbar):
    out = []
    if blk["esr"] != rec["esr"]:
        out.append("trace ESR %#x != record ESR %#x" % (blk["esr"], rec["esr"]))
    if blk["elr"] != rec["elr"]:
        out.append("trace ELR %#x != record ELR %#x" % (blk["elr"], rec["elr"]))
    if blk["spsr"] != rec["spsr"]:
        out.append("trace SPSR %#x != record SPSR %#x" % (blk["spsr"], rec["spsr"]))
    if blk["pc"] != vbar + rec["slot"] * 0x80:
        out.append("trace dispatch PC %#x does not match record slot %d at VBAR %#x"
                   % (blk["pc"], rec["slot"], vbar))
    return out


PC_RE = re.compile(r"\bPC=([0-9a-fA-F]+)")
PSTATE_RE = re.compile(r"\bPSTATE=([0-9a-fA-F]+)")


def parse_info_registers(text):
    m_pc = PC_RE.search(text)
    m_ps = PSTATE_RE.search(text)
    if not m_pc or not m_ps:
        raise HarnessFailure("registers", "could not parse PC/PSTATE from info registers output")
    return {"pc": int(m_pc.group(1), 16), "pstate": int(m_ps.group(1), 16), "raw": text}


class Qmp:
    def __init__(self, sock_path, connect_deadline=5.0, proc=None, stdout_path=None):
        self.sock = None
        self._buf = ""
        deadline = time.monotonic() + connect_deadline
        last_err = "socket file never appeared"
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                raise HarnessFailure("emulator-exited-early",
                                     "QEMU exited before QMP came up; stdout:\n%s"
                                     % tail_file(stdout_path or ""))
            if os.path.exists(sock_path):
                try:
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(5.0)
                    self.sock.connect(sock_path)
                    break
                except OSError as exc:
                    last_err = str(exc)
                    self.sock.close()
                    self.sock = None
            time.sleep(0.05)
        if self.sock is None:
            raise HarnessFailure("qmp-connect", "cannot connect to %s: %s" % (sock_path, last_err))
        greeting = self._recv()
        if "QMP" not in greeting:
            raise HarnessFailure("qmp-handshake", "unexpected greeting: %r" % greeting)
        self.cmd("qmp_capabilities")

    def _recv(self):
        while True:
            while "\n" not in self._buf:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout as exc:
                    raise HarnessFailure("qmp-io", "QMP read timeout: %s" % exc)
                if not chunk:
                    raise HarnessFailure("qmp-io", "QMP socket closed unexpectedly")
                self._buf += chunk.decode(errors="replace")
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                break
        return json.loads(line)

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        try:
            self.sock.sendall((json.dumps(msg) + "\n").encode())
        except OSError as exc:
            raise HarnessFailure("qmp-io", "QMP send failed: %s" % exc)
        while True:
            resp = self._recv()
            if "event" in resp:
                continue
            if "error" in resp:
                raise HarnessFailure("qmp-error", "%s: %s"
                                     % (name, resp["error"].get("desc", resp["error"])))
            return resp

    def read_memory(self, path, addr, size):
        self.cmd("pmemsave", val=addr, size=size, filename=path)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise HarnessFailure("guest-memory-read", "pmemsave produced no file: %s" % exc)
        if len(data) != size:
            raise HarnessFailure("guest-memory-read",
                                 "read %d bytes at %#x, expected %d"
                                 % (len(data), addr, size))
        return data

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


REQUIRED_SYMBOLS = [
    "myos_a8_raw_entry",
    "myos_a8_raw_main",
    "__vectors",
    "__vectors_end",
    "__stack_bottom",
    "__stack_top",
    "myos_a8_exc_common",
    "myos_a8_exc_park",
    "myos_a8_exc_record",
    "myos_a8_exc_record_end",
]

_TEMP_DIRS = []


def make_temp_dir(prefix):
    path = tempfile.mkdtemp(prefix=prefix)
    _TEMP_DIRS.append(path)
    return path


def cleanup_temp_dirs():
    while _TEMP_DIRS:
        shutil.rmtree(_TEMP_DIRS.pop(), ignore_errors=True)


def tail_file(path, limit=800):
    try:
        with open(path) as handle:
            text = handle.read()
    except OSError:
        return ""
    return text[-limit:]


def run_harness(args):
    if args.timeout <= 0:
        raise HarnessFailure("cli", "--timeout must be > 0")
    try:
        load_addr = parse_hex(args.load_addr)
    except ValueError:
        raise HarnessFailure("cli", "--load-addr is not a hex address: %r" % args.load_addr)
    shim_given = (args.shim is not None or args.shim_elf is not None
                  or args.spsel is not None or args.shim_load_addr is not None
                  or args.shim_source)
    shim_load_addr = None
    if shim_given:
        if args.shim_source and args.shim is None:
            raise HarnessFailure("cli", "--shim-source requires --shim")
        if args.shim is None or args.shim_elf is None or args.spsel is None:
            raise HarnessFailure(
                "cli",
                "--shim, --shim-elf and --spsel must be given together (pre-exception "
                "state selection is fail-closed)")
        if args.shim_load_addr is None:
            shim_load_addr = DEFAULT_SHIM_LOAD_ADDR
        else:
            try:
                shim_load_addr = parse_hex(args.shim_load_addr)
            except ValueError:
                raise HarnessFailure("cli", "--shim-load-addr is not a hex address: %r"
                                     % args.shim_load_addr)
        if shim_load_addr == load_addr:
            raise HarnessFailure("cli",
                                 "--shim-load-addr %#x collides with --load-addr" % shim_load_addr)
    if not os.path.isfile(args.image):
        raise HarnessFailure("image", "runtime image not found: %s" % args.image)
    if not os.path.isfile(args.elf):
        raise HarnessFailure("elf", "runtime ELF not found: %s" % args.elf)
    qemu = shutil.which(args.qemu)
    if qemu is None:
        raise HarnessFailure("emulator", "emulator executable not found: %s" % args.qemu)
    nm = shutil.which(args.nm)
    objdump = shutil.which(args.objdump)
    if nm is None or objdump is None:
        raise HarnessFailure("tool", "missing AArch64 tools: nm=%s objdump=%s" % (nm, objdump))

    syms = nm_symbols(nm, args.elf)
    missing = [name for name in REQUIRED_SYMBOLS if name not in syms]
    if missing:
        raise HarnessFailure("symbols", "runtime image is missing symbols: %s" % ", ".join(missing))
    if syms["myos_a8_raw_entry"] != 0:
        raise HarnessFailure("layout", "entry symbol at %#x, expected image offset 0"
                             % syms["myos_a8_raw_entry"])

    brk_addr, imm = find_brk(objdump, args.elf)
    if brk_addr != syms["myos_a8_raw_main"]:
        raise HarnessFailure("trigger",
                             "brk at %#x is not the first instruction of myos_a8_raw_main (%#x)"
                             % (brk_addr, syms["myos_a8_raw_main"]))

    shim_syms = None
    shim_size = 0
    shim_seed = None
    if args.shim is not None:
        shim_syms = validate_shim(args.shim, args.shim_elf, args.spsel,
                                  shim_load_addr, load_addr, nm, objdump)
        shim_size = shim_syms["myos_a8_shim_end"] - shim_syms["myos_a8_shim_start"]
        shim_seed = shim_load_addr + (shim_syms["myos_a8_shim_sp_area_top"]
                                      - shim_syms["myos_a8_shim_start"])

    image_size = os.path.getsize(args.image)
    expected_size = syms["myos_a8_exc_record_end"]
    if image_size != expected_size:
        raise HarnessFailure("image-size", "runtime image is %d bytes, expected %d (truncated/oversized)"
                             % (image_size, expected_size))
    if shim_size and not (shim_load_addr + shim_size <= load_addr
                          or shim_load_addr >= load_addr + image_size):
        raise HarnessFailure("shim",
                             "boot shim region %#x-%#x overlaps the runtime image region %#x-%#x"
                             % (shim_load_addr, shim_load_addr + shim_size,
                                load_addr, load_addr + image_size))
    with open(args.image, "rb") as handle:
        image = handle.read()
    rec_off = syms["myos_a8_exc_record"]
    if image[rec_off:rec_off + 8] != MAGIC:
        raise HarnessFailure("image-content", "exception-record magic missing in the image file")
    file_version, file_recsize = struct.unpack_from("<II", image, rec_off + 8)
    if file_version != VERSION or file_recsize != RECORD_SIZE:
        raise HarnessFailure("image-content", "record header in image file is version=%d size=%d"
                             % (file_version, file_recsize))
    file_marker = struct.unpack_from("<Q", image, rec_off + MARKER_OFFSET)[0]
    if file_marker != 0:
        raise HarnessFailure("image-content",
                             "completion marker is %#x in the untouched image file (must be 0)" % file_marker)

    now = time.time()
    image_mtime = os.path.getmtime(args.image)
    elf_mtime = os.path.getmtime(args.elf)
    stale = []
    if image_mtime < elf_mtime:
        stale.append(args.elf)
    for src in args.source:
        if not os.path.isfile(src):
            raise HarnessFailure("cli", "--source not found: %s" % src)
        if image_mtime < os.path.getmtime(src):
            stale.append(src)
    if stale:
        raise HarnessFailure("stale", "runtime image is older than: %s" % ", ".join(stale))
    shim_mtime = None
    if args.shim is not None:
        shim_mtime = os.path.getmtime(args.shim)
        stale = []
        if shim_mtime < os.path.getmtime(args.shim_elf):
            stale.append(args.shim_elf)
        for src in args.shim_source:
            if not os.path.isfile(src):
                raise HarnessFailure("cli", "--shim-source not found: %s" % src)
            if shim_mtime < os.path.getmtime(src):
                stale.append(src)
        if stale:
            raise HarnessFailure("stale", "boot shim is older than: %s" % ", ".join(stale))
    if image_mtime > now + 60:
        raise HarnessFailure("stale", "runtime image mtime is in the future")
    if shim_mtime is not None and shim_mtime > now + 60:
        raise HarnessFailure("stale", "boot shim mtime is in the future")

    if os.path.basename(os.path.normpath(args.evidence_dir)) != "evidence":
        raise HarnessFailure("cli", "--evidence-dir must be named 'evidence' (got %r)"
                             % args.evidence_dir)
    if os.path.exists(args.evidence_dir):
        shutil.rmtree(args.evidence_dir)
    os.makedirs(args.evidence_dir)
    int_log_path = os.path.join(args.evidence_dir, "qemu_int.log")
    stdout_path = os.path.join(args.evidence_dir, "qemu_stdout.log")
    poll_path = os.path.join(args.evidence_dir, "record_poll.bin")
    qmp_dir = make_temp_dir("a8qmp-")
    qmp_sock = os.path.join(qmp_dir, "qmp.sock")

    vbar = load_addr + syms["__vectors"]
    record_addr = load_addr + syms["myos_a8_exc_record"]
    stack_top = load_addr + syms["__stack_top"]
    park = load_addr + syms["myos_a8_exc_park"]
    if vbar & 0x7FF:
        raise HarnessFailure("layout", "runtime vector table %#x is not 2 KiB aligned" % vbar)

    # Scenario-derived expectations: the dispatch slot comes from the
    # pre-exception SPSel (SP0 -> slots 0-3, SPx -> slots 4-7), the record's
    # SPSR keeps that pre-exception mode, and the captured stack pointer is
    # the bank the handler actually runs on (exception entry selects SP_EL1).
    if args.shim is None:
        scenario = ("no boot shim: pre-exception SPSel=1 inherited from the QEMU virt "
                    "EL1 reset state (M7.14 default flow)")
    elif args.spsel == 0:
        scenario = ("boot shim at %#x: msr daifset,#0xf; spsel#1 + mov sp seeds SP_EL1 "
                    "with the shim area top %#x; msr spsel,#0 selects SP0; trampoline "
                    "then replaces SP_EL0 only (SP_EL1 keeps the seed for the handler)"
                    % (shim_load_addr, shim_seed))
    else:
        scenario = ("boot shim at %#x: msr daifset,#0xf; spsel#1 + mov sp seeds SP_EL1 "
                    "with the shim area top %#x; msr spsel,#1 keeps SPx; trampoline then "
                    "replaces the active SP_EL1 with __stack_top"
                    % (shim_load_addr, shim_seed))
    if args.spsel == 0:
        slot = 0
        slot_label = "Current EL, SP0, Synchronous"
        spsr_mode = SPSR_MODE_EL1T
        spsr_mode_label = "EL1t"
        sp_expect = shim_seed
        sp_label = "shim-seeded SP_EL1 top"
    else:
        slot = EXPECTED_SLOT
        slot_label = "Current EL, SPx, Synchronous"
        spsr_mode = SPSR_MODE_EL1H
        spsr_mode_label = "EL1h"
        sp_expect = stack_top
        sp_label = "image stack top"
    exp = {
        "load_addr": load_addr,
        "vbar": vbar,
        "record_addr": record_addr,
        "stack_top": stack_top,
        "park": park,
        "brk": load_addr + brk_addr,
        "imm": imm,
        "esr": (EC_BRK << 26) | ESR_IL_BIT | imm,
        "slot": slot,
        "slot_label": slot_label,
        "spsr_mode": spsr_mode,
        "spsr_mode_label": spsr_mode_label,
        "sp_expect": sp_expect,
        "sp_label": sp_label,
        "entry_pstate_mode": HANDLER_ENTRY_PSTATE_MODE,
        "entry_pstate_label": "EL1h",
        "scenario": scenario,
        "vector_pc": vbar + slot * 0x80,
    }

    qemu_cmd = [
        qemu,
        "-machine", QEMU_MACHINE,
        "-cpu", QEMU_CPU,
        "-m", QEMU_RAM,
        "-display", "none",
        "-qmp", "unix:%s,server=on,wait=off" % qmp_sock,
        "-d", "int",
        "-D", int_log_path,
    ]
    if args.shim is None:
        # M7.14 default: the loader device that carries cpu-num=0 also sets
        # the boot PC, which starts execution inside the image.
        qemu_cmd += ["-device",
                     "loader,file=%s,addr=%#x,cpu-num=0"
                     % (os.path.abspath(args.image), load_addr)]
    else:
        # Scenario runs: the image is loaded (no cpu-num), then the shim is
        # loaded at its own address with cpu-num=0 so the boot PC starts at
        # the shim, which establishes DAIF/SPSel/SP_EL1 before branching to
        # the unmodified production image entry.
        qemu_cmd += ["-device",
                     "loader,file=%s,addr=%#x" % (os.path.abspath(args.image), load_addr),
                     "-device",
                     "loader,file=%s,addr=%#x,cpu-num=0"
                     % (os.path.abspath(args.shim), shim_load_addr)]
    with open(stdout_path, "w") as stdout_handle:
        proc = subprocess.Popen(qemu_cmd, stdout=stdout_handle, stderr=subprocess.STDOUT)
        qmp = None
        try:
            qmp = Qmp(qmp_sock, proc=proc, stdout_path=stdout_path)
            deadline = time.monotonic() + args.timeout
            data = None
            while True:
                if proc.poll() is not None:
                    raise HarnessFailure("emulator-exited-early",
                                         "QEMU exited before the record completed; stdout:\n%s"
                                         % tail_file(stdout_path))
                data = qmp.read_memory(poll_path, record_addr, RECORD_SIZE)
                if struct.unpack_from("<Q", data, MARKER_OFFSET)[0] == 1:
                    break
                if time.monotonic() > deadline:
                    raise HarnessFailure(
                        "timeout",
                        "guest never completed the exception record (marker not set within %.1fs); "
                        "either the handler did not run or the exception was never delivered"
                        % args.timeout)
                time.sleep(0.05)

            rec = parse_record(data)
            rec_failures = validate_record(rec, exp)
            if rec_failures:
                raise HarnessFailure("record-validation", "; ".join(rec_failures))

            regs_resp = qmp.cmd("human-monitor-command", **{"command-line": "info registers"})
            regs = parse_info_registers(regs_resp["return"])
            if regs["pc"] != park:
                raise HarnessFailure("park", "guest PC %#x is not the handler park loop %#x"
                                     % (regs["pc"], park))

            data_second = qmp.read_memory(poll_path, record_addr, RECORD_SIZE)
            if data_second != data:
                raise HarnessFailure("record-unstable",
                                     "exception record changed while being inspected")

            status = qmp.cmd("query-status")["return"]["status"]

            qmp.cmd("quit")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise HarnessFailure("quit-timeout", "QEMU did not exit on quit")
        finally:
            if qmp is not None:
                qmp.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    try:
        with open(int_log_path) as handle:
            log_text = handle.read()
    except OSError as exc:
        raise HarnessFailure("trace", "QEMU -d int log missing: %s" % exc)
    blocks = parse_int_log(log_text)
    trace_failures = validate_int_log(blocks, exp)
    if trace_failures:
        raise HarnessFailure("trace-validation", "; ".join(trace_failures))
    cross_failures = cross_check_trace_record(rec, blocks[0], vbar)
    if cross_failures:
        raise HarnessFailure("trace-record-mismatch", "; ".join(cross_failures))

    with open(os.path.join(args.evidence_dir, "record.bin"), "wb") as handle:
        handle.write(data)
    with open(os.path.join(args.evidence_dir, "record.txt"), "w") as handle:
        handle.write("magic=%s\nversion=%d\nsize=%d\nslot=%d\nesr=%#x\nelr=%#x\nfar=%#x\n"
                     "spsr=%#x\nsp=%#x\nmarker=%d\n"
                     % (rec["magic"].decode(errors="replace"), rec["version"], rec["recsize"],
                        rec["slot"], rec["esr"], rec["elr"], rec["far"], rec["spsr"], rec["sp"],
                        rec["marker"]))
    with open(os.path.join(args.evidence_dir, "registers.txt"), "w") as handle:
        handle.write(regs["raw"])
    with open(os.path.join(args.evidence_dir, "trace.txt"), "w") as handle:
        handle.write(log_text)
    with open(os.path.join(args.evidence_dir, "command.txt"), "w") as handle:
        handle.write(" ".join(qemu_cmd) + "\n")
    with open(os.path.join(args.evidence_dir, "summary.txt"), "w") as handle:
        handle.write(
            "raw-entry-runtime-test: PASS (emulation evidence only; physical boot UNVERIFIED)\n"
            "qemu=%s machine=%s cpu=%s ram=%s\n"
            "scenario=%s\n"
            "load_addr=%#x vbar=%#x record=%#x park=%#x\n"
            "trigger=brk #%#x at %#x esr=%#x vector_pc=%#x\n"
            "expected: slot=%d (%s) spsr_mode=%#x (%s) sp=%#x (%s) entry_pstate_mode=%#x\n"
            "captured: slot=%d esr=%#x elr=%#x spsr=%#x sp=%#x marker=%d\n"
            "guest status at inspection: %s\n"
            % (qemu, QEMU_MACHINE, QEMU_CPU, QEMU_RAM, scenario, load_addr, vbar,
               record_addr, park, imm, brk_addr, exp["esr"], exp["vector_pc"],
               exp["slot"], exp["slot_label"], exp["spsr_mode"], exp["spsr_mode_label"],
               exp["sp_expect"], exp["sp_label"], exp["entry_pstate_mode"],
               rec["slot"], rec["esr"], rec["elr"],
               rec["spsr"], rec["sp"], rec["marker"], status))
    return exp, rec, regs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="MyOS M7.14/M7.15 raw-entry exception-path runtime harness "
                    "(QEMU emulation, optional SPSel scenario shims)")
    parser.add_argument("--image", required=True)
    parser.add_argument("--elf", required=True)
    parser.add_argument("--qemu", default="qemu-system-aarch64")
    parser.add_argument("--nm", default="aarch64-linux-gnu-nm")
    parser.add_argument("--objdump", default="aarch64-linux-gnu-objdump")
    parser.add_argument("--load-addr", default="0x41000000")
    parser.add_argument("--shim", default=None,
                        help="test-only boot shim flat binary (scenario run; requires "
                             "--shim-elf and --spsel)")
    parser.add_argument("--shim-elf", default=None,
                        help="boot shim ELF used for symbol and disassembly validation")
    parser.add_argument("--shim-load-addr", default=None,
                        help="address the boot shim is loaded at (default %#x)"
                             % DEFAULT_SHIM_LOAD_ADDR)
    parser.add_argument("--spsel", type=int, choices=(0, 1), default=None,
                        help="pre-exception SPSel the shim must establish (requires --shim)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--source", action="append", default=[],
                        help="source file the image must not be older than (repeatable)")
    parser.add_argument("--shim-source", action="append", default=[],
                        help="source file the boot shim must not be older than (repeatable; "
                             "requires --shim)")
    args = parser.parse_args(argv)
    try:
        exp, rec, _regs = run_harness(args)
    except HarnessFailure as exc:
        print("raw-entry-runtime-test: FAIL: %s: %s" % (exc.reason, exc.detail), file=sys.stderr)
        return 1
    finally:
        cleanup_temp_dirs()
    print("raw-entry-runtime-test: PASS (QEMU emulation: real handler wrote slot=%d esr=%#x "
          "elr=%#x spsr=%#x sp=%#x marker=1; guest parked at %#x; scenario: %s; evidence in %s)"
          % (rec["slot"], rec["esr"], rec["elr"], rec["spsr"], rec["sp"], exp["park"],
             exp["scenario"], args.evidence_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
