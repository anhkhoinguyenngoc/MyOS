#!/usr/bin/env python3
"""MyOS M7.14/M7.15 — host-side tests for the raw-entry exception-path
runtime harness (tests/a8_exc_runtime.py).

Coverage (M7.14 section 12 + M7.15 section 8), all offline host tests:
  - CLI/input failures: missing runtime image, missing ELF, missing
    emulator executable, missing AArch64 tools, invalid timeout, invalid
    load address, mis-named evidence directory;
  - record validation against WRONG completion marker (absent and wrong
    value), WRONG vector slot, WRONG exception syndrome, WRONG saved
    program counter, WRONG entry mode / DAIF state, WRONG exception-time
    stack pointer, wrong magic/version/size — using SYNTHETIC record
    fixtures (clearly labelled; a synthetic fixture can never satisfy the
    runtime target, which only reads live guest memory);
  - -d int trace validation (missing trace, wrong class, wrong levels,
    wrong vector dispatch PC, runaway exceptions) and trace-vs-record
    cross-checks;
  - info-registers parsing including malformed output;
  - end-to-end negative runs of the REAL harness against a live QEMU with
    deliberately corrupted test images: guest that never triggers (timeout),
    guest whose exception is delivered but whose handler never runs
    (timeout), wrong recorded slot, wrong syndrome, corrupted record
    magic, guest that leaves the park path, truncated image, stale image,
    a pre-planted evidence file (must be wiped and must never count as a
    run), and a failing emulator executable (unsupported configuration).
  - the production raw_entry.bin hash is unchanged by the test-only build.

M7.15 additions:
  - CLI fail-closed checks for the scenario shim arguments (incomplete
    --shim/--shim-elf/--spsel trio; --shim-load-addr colliding with
    --load-addr);
  - end-to-end SCENARIO runs of the real harness against live QEMU for
    both SPSel states: SP0 (expected slot 0, EL1t pre-exception SPSR,
    captured sp = the shim-seeded SP_EL1 value) and SPx (expected slot 4,
    EL1h pre-exception SPSR, captured sp = __stack_top, proving the
    trampoline replaced the seeded bank), plus a wrong-expected-slot
    mutant run under the SP0 scenario;
  - shim validation negatives asserting the expected reason: missing shim
    file, wrong-scenario shim in both directions, an eliminated bank-select
    'msr spsel' (incorrect stack-selection setup), a tampered image-entry
    word, and a stale shim (--shim-source in the future);
  - an incorrect trigger PC detected pre-launch via an ELF symtab-patched
    copy of the runtime ELF (expected reason 'trigger');
  - production purity: the production raw_entry.bin contains no test-only
    BRK trigger, differs from the runtime-test image, and the production
    ELF carries no boot-shim symbols.

The end-to-end section runs only when qemu-system-aarch64, the AArch64
toolchain and the runtime artifacts are available; otherwise it prints a
loud SKIP and the unit sections still run. Expected failure REASONS are
asserted, not merely nonzero exit codes.
"""
import hashlib
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HARNESS_PATH = os.path.join(HERE, "a8_exc_runtime.py")

_spec = importlib.util.spec_from_file_location("a8_exc_runtime", HARNESS_PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

VALIDATOR_PATH = os.path.join(HERE, "test_a8_raw_entry.py")
_vspec = importlib.util.spec_from_file_location("a8_raw_entry_validator", VALIDATOR_PATH)
validator = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(validator)

QEMU_PRESENT = shutil.which("qemu-system-aarch64") is not None
NM_PRESENT = shutil.which("aarch64-linux-gnu-nm") is not None
OBJDUMP_PRESENT = shutil.which("aarch64-linux-gnu-objdump") is not None
TOOLCHAIN_PRESENT = shutil.which("aarch64-linux-gnu-gcc") is not None \
    and shutil.which("aarch64-linux-gnu-ld") is not None \
    and shutil.which("aarch64-linux-gnu-objcopy") is not None

RUNTIME_DIR = os.path.join(ROOT, "build", "raw-entry-runtime")
RUNTIME_ELF = os.path.join(RUNTIME_DIR, "raw_entry_runtime.elf")
RUNTIME_BIN = os.path.join(RUNTIME_DIR, "raw_entry_runtime.bin")
PROD_BIN = os.path.join(ROOT, "build", "raw_entry.bin")
PROD_ELF = os.path.join(ROOT, "build", "raw_entry.elf")
SHIM_SRC = os.path.join(ROOT, "tests", "a8_runtime", "a8_boot_shim.S")
SHIM_SP0_ELF = os.path.join(RUNTIME_DIR, "shim", "a8_boot_shim_sp0.elf")
SHIM_SP0_BIN = os.path.join(RUNTIME_DIR, "shim", "a8_boot_shim_sp0.bin")
SHIM_SPX_ELF = os.path.join(RUNTIME_DIR, "shim", "a8_boot_shim_spx.elf")
SHIM_SPX_BIN = os.path.join(RUNTIME_DIR, "shim", "a8_boot_shim_spx.bin")
IMAGE_SOURCES = [os.path.join(ROOT, "platform", "apple", "a8", "raw_entry.S"),
                 os.path.join(ROOT, "platform", "apple", "a8", "raw_vectors.S"),
                 os.path.join(ROOT, "tests", "a8_runtime", "a8_trigger.S"),
                 os.path.join(ROOT, "platform", "apple", "a8", "raw_entry.ld")]

B_DOT = struct.pack("<I", 0x14000000)
B_PLUS4 = struct.pack("<I", 0x14000001)
BRK_OPCODE = 0xD4200000
MOVZ_X16_BASE = 0xD2800000
MOVZ_X16_RD_MASK = 0x1F

failures = []
counter = {"n": 0}


def check(name, cond, detail=""):
    counter["n"] += 1
    if cond:
        print("  PASS %s" % name)
    else:
        failures.append(name)
        print("  FAIL %s  %s" % (name, detail))


def brk_bytes(imm):
    return struct.pack("<I", BRK_OPCODE | ((imm & 0xFFFF) << 5))


def movz_x16_bytes(imm):
    return struct.pack("<I", MOVZ_X16_BASE | ((imm & 0xFFFF) << 5) | 16)


def run_cli(extra):
    res = subprocess.run([sys.executable, HARNESS_PATH] + extra,
                         capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def section(title):
    print("== %s ==" % title)


work = tempfile.mkdtemp(prefix="a8_runtime_host_tests_")

section("CLI and input validation (no emulator launch)")
rc, out = run_cli(["--image", os.path.join(work, "missing.bin"),
                   "--elf", os.path.join(work, "missing.elf"),
                   "--evidence-dir", os.path.join(work, "t1", "evidence")])
check("missing image fails with reason 'image'", rc != 0 and "FAIL: image" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH,
                   "--elf", os.path.join(work, "missing.elf"),
                   "--evidence-dir", os.path.join(work, "t2", "evidence")])
check("missing elf fails with reason 'elf'", rc != 0 and "FAIL: elf" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH,
                   "--qemu", os.path.join(work, "no-such-qemu"),
                   "--evidence-dir", os.path.join(work, "t3", "evidence")])
check("missing emulator fails with reason 'emulator'",
      rc != 0 and "FAIL: emulator" in out, out[-200:])

if QEMU_PRESENT:
    rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH,
                       "--nm", os.path.join(work, "no-such-nm"),
                       "--evidence-dir", os.path.join(work, "t4", "evidence")])
    check("missing AArch64 tool fails with reason 'tool'", rc != 0 and "FAIL: tool" in out, out[-200:])
else:
    print("  SKIP missing-tool test (qemu-system-aarch64 not present)")

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH, "--timeout", "0",
                   "--evidence-dir", os.path.join(work, "t5", "evidence")])
check("non-positive timeout fails with reason 'cli'", rc != 0 and "FAIL: cli" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH, "--load-addr", "not-an-address",
                   "--evidence-dir", os.path.join(work, "t6", "evidence")])
check("invalid load address fails with reason 'cli'", rc != 0 and "FAIL: cli" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH,
                   "--shim", SHIM_SP0_BIN,
                   "--evidence-dir", os.path.join(work, "t7", "evidence")])
check("incomplete scenario shim arguments fail with reason 'cli'",
      rc != 0 and "FAIL: cli" in out and "must be given together" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH,
                   "--shim", SHIM_SP0_BIN, "--shim-elf", SHIM_SP0_ELF, "--spsel", "0",
                   "--load-addr", "0x41000000", "--shim-load-addr", "0x41000000",
                   "--evidence-dir", os.path.join(work, "t8", "evidence")])
check("shim load address colliding with the image load address fails with reason 'cli'",
      rc != 0 and "FAIL: cli" in out and "collides" in out, out[-200:])

rc, out = run_cli(["--image", HARNESS_PATH, "--elf", HARNESS_PATH,
                   "--shim-source", os.path.join(work, "t9.S"),
                   "--evidence-dir", os.path.join(work, "t9", "evidence")])
check("--shim-source without --shim fails with reason 'cli'",
      rc != 0 and "FAIL: cli" in out and "requires --shim" in out, out[-200:])

section("record validation with SYNTHETIC fixtures (labelled SYNTHETIC; cannot satisfy the runtime target)")
SYNTH_EXP = {
    "imm": 0xCAFE,
    "brk": 0x41000020,
    "stack_top": 0x41004240,
    "esr": (harness.EC_BRK << 26) | harness.ESR_IL_BIT | 0xCAFE,
    "vector_pc": 0x41004800 + harness.EXPECTED_SLOT * 0x80,
    "slot": harness.EXPECTED_SLOT,
    "slot_label": "Current EL, SPx, Synchronous",
    "spsr_mode": harness.SPSR_MODE_EL1H,
    "spsr_mode_label": "EL1h",
    "entry_pstate_mode": harness.HANDLER_ENTRY_PSTATE_MODE,
    "entry_pstate_label": "EL1h",
    "sp_expect": 0x41004240,
    "sp_label": "image stack top",
}


def synth_record(magic=b"MYOSA8E1", version=1, recsize=0x48, slot=4,
                 esr=SYNTH_EXP["esr"], elr=SYNTH_EXP["brk"], far=0,
                 spsr=0x400003C5, sp=SYNTH_EXP["stack_top"], marker=1):
    blob = magic + struct.pack("<II", version, recsize)
    blob += struct.pack("<7Q", slot, esr, elr, far, spsr, sp, marker)
    return blob


def vrec(**kw):
    return harness.validate_record(harness.parse_record(synth_record(**kw)), SYNTH_EXP)


check("SYNTHETIC correct record passes the validator",
      vrec() == [], str(vrec()))
check("absent marker (0) is rejected",
      any("completion marker" in m for m in vrec(marker=0)), str(vrec(marker=0)))
check("wrong marker (2) is rejected",
      any("completion marker" in m for m in vrec(marker=2)), str(vrec(marker=2)))
check("wrong vector slot (7) is rejected",
      any("vector slot" in m for m in vrec(slot=7)), str(vrec(slot=7)))
check("wrong syndrome (data-abort EC) is rejected",
      any("exception class" in m for m in vrec(esr=(0x24 << 26) | harness.ESR_IL_BIT | 0xCAFE)),
      str(vrec(esr=(0x24 << 26) | harness.ESR_IL_BIT | 0xCAFE)))
check("wrong syndrome (ISS immediate) is rejected",
      any("syndrome" in m for m in vrec(esr=(harness.EC_BRK << 26) | harness.ESR_IL_BIT | 0x42)),
      str(vrec(esr=(harness.EC_BRK << 26) | harness.ESR_IL_BIT | 0x42)))
check("cleared IL bit is rejected",
      any("IL bit" in m for m in vrec(esr=(harness.EC_BRK << 26) | 0xCAFE)),
      str(vrec(esr=(harness.EC_BRK << 26) | 0xCAFE)))
check("wrong saved program counter (ELR) is rejected",
      any("ELR" in m for m in vrec(elr=0x41000024)), str(vrec(elr=0x41000024)))
check("wrong entry mode (EL1t) is rejected",
      any("SPSR mode" in m for m in vrec(spsr=0x400003C1)), str(vrec(spsr=0x400003C1)))
check("unmasked DAIF at trigger is rejected",
      any("DAIF" in m for m in vrec(spsr=0x40000005)), str(vrec(spsr=0x40000005)))
check("illegal-execution-state (SPSR IL) is rejected",
      any("SPSR IL" in m for m in vrec(spsr=0x401003C5)), str(vrec(spsr=0x401003C5)))
check("wrong exception-time stack pointer is rejected",
      any("stack pointer" in m for m in vrec(sp=0x41004248)), str(vrec(sp=0x41004248)))
check("wrong record magic is rejected",
      any("magic" in m for m in vrec(magic=b"XYOSA8E1")), str(vrec(magic=b"XYOSA8E1")))
check("wrong record version is rejected",
      any("version" in m for m in vrec(version=2)), str(vrec(version=2)))
check("wrong record size field is rejected",
      any("size" in m for m in vrec(recsize=0x40)), str(vrec(recsize=0x40)))

try:
    harness.parse_record(synth_record()[:-8])
    check("truncated record dump is rejected", False, "no failure raised")
except harness.HarnessFailure as exc:
    check("truncated record dump is rejected", exc.reason == "guest-memory-read", str(exc))

section("-d int trace validation with SYNTHETIC fixtures (labelled SYNTHETIC)")
GOOD_LOG = (
    "Taking exception 7 [Breakpoint] on CPU 0\n"
    "...from EL1 to EL1\n"
    "...with ESR 0x3c/0xf200cafe\n"
    "...with SPSR 0x400003c5\n"
    "...with ELR 0x41000020\n"
    "...to EL1 PC 0x41004a00 PSTATE 0x3c5\n"
)


def vlog(text):
    return harness.validate_int_log(harness.parse_int_log(text), SYNTH_EXP)


check("SYNTHETIC correct trace passes", vlog(GOOD_LOG) == [], str(vlog(GOOD_LOG)))
check("missing trace fails", vlog("") == ["no exception captured in the QEMU -d int trace"],
      str(vlog("")))
check("runaway extra exceptions fail", any("runaway" in m for m in vlog(GOOD_LOG + GOOD_LOG)),
      str(vlog(GOOD_LOG + GOOD_LOG)))
check("wrong exception class in trace fails",
      any("[Data Abort]" in m for m in vlog(GOOD_LOG.replace("[Breakpoint]", "[Data Abort]"))),
      str(vlog(GOOD_LOG.replace("[Breakpoint]", "[Data Abort]"))))
check("wrong exception levels in trace fail",
      any("levels" in m for m in vlog(GOOD_LOG.replace("from EL1 to EL1", "from EL2 to EL1"))),
      str(vlog(GOOD_LOG.replace("from EL1 to EL1", "from EL2 to EL1"))))
check("wrong vector dispatch PC in trace fails",
      any("dispatched" in m for m in vlog(GOOD_LOG.replace("PC 0x41004a00", "PC 0x41004800"))),
      str(vlog(GOOD_LOG.replace("PC 0x41004a00", "PC 0x41004800"))))
check("wrong trace ESR fails",
      any("trace ESR" in m for m in vlog(GOOD_LOG.replace("0xf200cafe", "0xf200ca42"))),
      str(vlog(GOOD_LOG.replace("0xf200cafe", "0xf200ca42"))))
check("wrong trace ELR fails",
      any("trace ELR" in m for m in vlog(GOOD_LOG.replace("ELR 0x41000020", "ELR 0x41000024"))),
      str(vlog(GOOD_LOG.replace("ELR 0x41000020", "ELR 0x41000024"))))

section("trace-vs-record cross-checks with SYNTHETIC fixtures (labelled SYNTHETIC)")
rec_dict = harness.parse_record(synth_record())
good_block = harness.parse_int_log(GOOD_LOG)[0]
check("consistent trace and record cross-check cleanly",
      harness.cross_check_trace_record(rec_dict, good_block, 0x41004800) == [])
bad = dict(good_block)
bad["pc"] = 0x41004800
check("dispatch PC that contradicts the record slot fails",
      any("does not match record slot" in m
          for m in harness.cross_check_trace_record(rec_dict, bad, 0x41004800)),
      str(harness.cross_check_trace_record(rec_dict, bad, 0x41004800)))
bad = dict(good_block)
bad["esr"] = 0xF200CA42
check("trace ESR that contradicts the record fails",
      any("trace ESR" in m for m in harness.cross_check_trace_record(rec_dict, bad, 0x41004800)), "")
bad = dict(good_block)
bad["spsr"] = 0x400003C1
check("trace SPSR that contradicts the record fails",
      any("trace SPSR" in m for m in harness.cross_check_trace_record(rec_dict, bad, 0x41004800)), "")
bad = dict(good_block)
bad["elr"] = 0x41000024
check("trace ELR that contradicts the record fails",
      any("trace ELR" in m for m in harness.cross_check_trace_record(rec_dict, bad, 0x41004800)), "")

section("info-registers parsing")
GOOD_REGS = (
    "CPU#0\n"
    " PC=000000004100006c X00=0000000000000000 X01=0000000000000000\n"
    "X02=0000000000000000 X03=0000000000000000 X04=0000000000000000\n"
    "PSTATE=00000000000003c5 ---- EL1h    FPU disabled\n"
)
parsed = harness.parse_info_registers(GOOD_REGS)
check("park PC and PSTATE parsed from info registers",
      parsed["pc"] == 0x4100006C and parsed["pstate"] == 0x3C5, str(parsed))
try:
    harness.parse_info_registers("garbage without registers")
    check("malformed info-registers output is rejected", False, "no failure raised")
except harness.HarnessFailure as exc:
    check("malformed info-registers output is rejected", exc.reason == "registers", str(exc))


def sha_or_none(path):
    return sha256_file(path) if os.path.isfile(path) else None


section("end-to-end negative runs against live QEMU (real harness, corrupted images)")
prod_before = sha_or_none(PROD_BIN)
if not (QEMU_PRESENT and TOOLCHAIN_PRESENT and NM_PRESENT and OBJDUMP_PRESENT):
    print("  SKIP end-to-end section: qemu=%s toolchain=%s nm=%s objdump=%s"
          % (QEMU_PRESENT, TOOLCHAIN_PRESENT, NM_PRESENT, OBJDUMP_PRESENT))
else:
    build = subprocess.run(
        ["make", "-s", "build/raw-entry-runtime/raw_entry_runtime.bin",
         "build/raw-entry-runtime/raw_entry_runtime.elf"],
        cwd=ROOT, capture_output=True, text=True)
    if build.returncode != 0:
        print("  SKIP end-to-end section: runtime artifacts unavailable (make failed): %s"
              % build.stderr[-300:])
    else:
        syms = harness.nm_symbols("aarch64-linux-gnu-nm", RUNTIME_ELF)
        brk_off, imm = harness.find_brk("aarch64-linux-gnu-objdump", RUNTIME_ELF)
        with open(RUNTIME_BIN, "rb") as handle:
            image = handle.read()
        check("built image brk encoding matches the ELF (mutation sanity)",
              image[brk_off:brk_off + 4] == brk_bytes(imm),
              "at %#x: %s" % (brk_off, image[brk_off:brk_off + 4].hex()))
        slot4_off = syms["__vectors"] + harness.EXPECTED_SLOT * 0x80
        slot_word = struct.unpack_from("<I", image, slot4_off)[0]
        check("built image slot stub matches movz x16, #%d (mutation sanity)"
              % harness.EXPECTED_SLOT,
              (slot_word & ~(0xFFFF << 5) & ~MOVZ_X16_RD_MASK) == MOVZ_X16_BASE
              and ((slot_word >> 5) & 0xFFFF) == harness.EXPECTED_SLOT
              and (slot_word & MOVZ_X16_RD_MASK) == 16,
              hex(slot_word))
        park_off = syms["myos_a8_exc_park"]

        def mutate(name, patches):
            data = bytearray(image)
            for off, blob in patches:
                if off + len(blob) > len(data):
                    raise AssertionError("patch beyond image end")
                data[off:off + len(blob)] = blob
            dst = os.path.join(work, name)
            with open(dst, "wb") as handle:
                handle.write(bytes(data))
            return dst

        def run_e2e(name, image_path, expect, timeout="3", sources=None, preplant=None):
            run_e2e.count += 1
            case_dir = os.path.join(work, "c%02d" % run_e2e.count)
            evdir = os.path.join(case_dir, "evidence")
            if preplant is not None:
                os.makedirs(evdir)
                for fname, content in preplant.items():
                    with open(os.path.join(evdir, fname), "w") as handle:
                        handle.write(content)
            cli_args = ["--image", image_path, "--elf", RUNTIME_ELF,
                        "--evidence-dir", evdir, "--timeout", timeout]
            for src in (sources or []):
                cli_args += ["--source", src]
            rc, out = run_cli(cli_args)
            check("e2e %s fails with reason '%s'" % (name, expect),
                  rc != 0 and expect in out, "rc=%d tail=%r" % (rc, out[-250:]))
            return evdir

        run_e2e.count = 0
        run_e2e("park-only-no-exception", mutate("park_only.bin", [(brk_off, B_DOT)]),
                "timeout")
        run_e2e("vector-delivered-but-handler-never-runs",
                mutate("no_handler.bin", [(slot4_off + 4, B_DOT)]), "timeout")
        run_e2e("wrong-recorded-slot",
                mutate("wrong_slot.bin", [(slot4_off, movz_x16_bytes(7))]), "vector slot")
        run_e2e("wrong-syndrome-imm",
                mutate("wrong_iss.bin", [(brk_off, brk_bytes(0x42))]), "syndrome")
        run_e2e("corrupted-record-magic",
                mutate("bad_magic.bin", [(syms["myos_a8_exc_record"], b"XYOSA8E1")]), "magic")
        run_e2e("guest-leaves-park-path",
                mutate("wrong_park.bin",
                       [(park_off, struct.pack("<I", 0x14000000 |
                                               (((brk_off + 4 - park_off) // 4) & 0x3FFFFFF)))]),
                "park")
        with open(os.path.join(work, "truncated.bin"), "wb") as handle:
            handle.write(image[:0x5000])
        run_e2e("truncated-image", os.path.join(work, "truncated.bin"), "image-size")
        planted_evdir = run_e2e(
            "pre-planted-evidence-ignored",
            mutate("planted_case.bin", [(brk_off, B_DOT)]), "timeout",
            preplant={"summary.txt": "raw-entry-runtime-test: PASS (forged)\n",
                      "record.bin": synth_record().decode("latin1")})
        check("pre-planted evidence files were wiped, not consumed",
              not os.path.exists(os.path.join(planted_evdir, "summary.txt"))
              and not os.path.exists(os.path.join(planted_evdir, "record.bin")),
              str(os.listdir(planted_evdir) if os.path.isdir(planted_evdir) else "missing"))
        future_src = os.path.join(work, "future_source.S")
        with open(future_src, "w") as handle:
            handle.write("/* fixture */\n")
        future = time.time() + 3600
        os.utime(future_src, (future, future))
        run_e2e("stale-image", RUNTIME_BIN, "stale", sources=[future_src])
        rc, out = run_cli(["--image", RUNTIME_BIN, "--elf", RUNTIME_ELF,
                           "--evidence-dir", os.path.join(work, "case_badname", "not-evidence")])
        check("mis-named evidence directory fails with reason 'cli'",
              rc != 0 and "FAIL: cli" in out, out[-200:])
        fake_qemu = os.path.join(work, "fake-qemu.sh")
        with open(fake_qemu, "w") as handle:
            handle.write("#!/bin/sh\necho \"qemu: unsupported machine type\" >&2\nexit 1\n")
        os.chmod(fake_qemu, 0o755)
        rc, out = run_cli(["--image", RUNTIME_BIN, "--elf", RUNTIME_ELF, "--qemu", fake_qemu,
                           "--evidence-dir", os.path.join(work, "case_fakeqemu", "evidence")])
        check("emulator that exits immediately fails with reason 'emulator-exited-early'",
              rc != 0 and "emulator-exited-early" in out, out[-250:])

        section("M7.15 scenario runs against live QEMU (SP0 and SPx, real shim)")
        shim_build = subprocess.run(
            ["make", "-s",
             "build/raw-entry-runtime/shim/a8_boot_shim_sp0.elf",
             "build/raw-entry-runtime/shim/a8_boot_shim_sp0.bin",
             "build/raw-entry-runtime/shim/a8_boot_shim_spx.elf",
             "build/raw-entry-runtime/shim/a8_boot_shim_spx.bin"],
            cwd=ROOT, capture_output=True, text=True)
        if shim_build.returncode != 0:
            check("scenario shim artifacts build", False,
                  (shim_build.stderr or shim_build.stdout)[-300:])
        else:
            check("scenario shim artifacts build", True)

            scenario_count = {"n": 0}

            def run_scenario(name, spsel, shim_bin, shim_elf, expect, token=None,
                             image=None, timeout="5"):
                scenario_count["n"] += 1
                case_dir = os.path.join(work, "sc%02d" % scenario_count["n"])
                evdir = os.path.join(case_dir, "evidence")
                cli_args = ["--image", image or RUNTIME_BIN, "--elf", RUNTIME_ELF,
                            "--evidence-dir", evdir, "--timeout", timeout,
                            "--shim", shim_bin, "--shim-elf", shim_elf,
                            "--spsel", str(spsel), "--shim-source", SHIM_SRC]
                for src in IMAGE_SOURCES:
                    cli_args += ["--source", src]
                rc, out = run_cli(cli_args)
                if expect is None:
                    check("scenario %s PASSES with %r" % (name, token),
                          rc == 0 and token is not None and token in out,
                          "rc=%d tail=%r" % (rc, out[-250:]))
                else:
                    detail_token = token if token else "FAIL: %s" % expect
                    check("scenario %s fails with '%s'" % (name, detail_token),
                          rc != 0 and expect in out and detail_token in out,
                          "rc=%d tail=%r" % (rc, out[-250:]))
                return evdir

            run_scenario("SP0 (Current EL, SP0, Synchronous)", 0,
                         SHIM_SP0_BIN, SHIM_SP0_ELF, None, "slot=0")
            run_scenario("SPx (Current EL, SPx, Synchronous)", 1,
                         SHIM_SPX_BIN, SHIM_SPX_ELF, None, "slot=4")

            slot0_off = syms["__vectors"]
            wrong_slot0 = mutate("scenario_wrong_slot0.bin",
                                 [(slot0_off, movz_x16_bytes(7))])
            run_scenario("SP0 with a wrong-expected-vector-slot mutant", 0,
                         SHIM_SP0_BIN, SHIM_SP0_ELF, "record-validation",
                         "vector slot", image=wrong_slot0)

            section("M7.15 shim validation negatives (fail-closed, expected reasons)")

            def run_shim_neg(name, shim_bin, shim_elf, spsel, expect, detail,
                             shim_source=None, extra=None):
                cli_args = ["--image", RUNTIME_BIN, "--elf", RUNTIME_ELF,
                            "--evidence-dir", os.path.join(work, name, "evidence"),
                            "--timeout", "3",
                            "--shim", shim_bin, "--shim-elf", shim_elf,
                            "--spsel", str(spsel)]
                if shim_source is not None:
                    cli_args += ["--shim-source", shim_source]
                cli_args += (extra or [])
                rc, out = run_cli(cli_args)
                check("%s fails with '%s'" % (name, detail),
                      rc != 0 and "FAIL: %s" % expect in out and detail in out,
                      "rc=%d tail=%r" % (rc, out[-250:]))

            run_shim_neg("missing-shim-binary", os.path.join(work, "no_such_shim.bin"),
                         SHIM_SP0_ELF, 0, "shim", "not found")
            run_shim_neg("wrong-scenario-sp0-shim-requested-spsel-1",
                         SHIM_SP0_BIN, SHIM_SP0_ELF, 1, "shim", "wrong-scenario shim")
            run_shim_neg("wrong-scenario-spx-shim-requested-spsel-0",
                         SHIM_SPX_BIN, SHIM_SPX_ELF, 0, "shim", "wrong-scenario shim")

            with open(SHIM_SP0_ELF, "rb") as fh:
                shim_elf_blob = fh.read()
            with open(SHIM_SP0_BIN, "rb") as fh:
                shim_bin_blob = fh.read()
            shim_start = harness.nm_symbols("aarch64-linux-gnu-nm", SHIM_SP0_ELF)[
                "myos_a8_shim_start"]

            dis = subprocess.run(["aarch64-linux-gnu-objdump", "-d", SHIM_SP0_ELF],
                                 capture_output=True, text=True)
            bank_sel_addr = None
            for line in dis.stdout.splitlines():
                m = re.match(r"^\s*([0-9a-f]+):\s+[0-9a-f]+\s+msr\s+spsel,", line)
                if m:
                    bank_sel_addr = int(m.group(1), 16)
                    break
            check("bank-select 'msr spsel' located in the SP0 shim",
                  bank_sel_addr is not None)
            if bank_sel_addr is not None:
                nop_elf = validator.elf_patch_vaddr(shim_elf_blob, bank_sel_addr,
                                                    struct.pack("<I", 0xd503201f))
                nop_bin = bytearray(shim_bin_blob)
                nop_off = bank_sel_addr - shim_start
                nop_bin[nop_off:nop_off + 4] = struct.pack("<I", 0xd503201f)
                nop_elf_path = os.path.join(work, "shim_no_bank_select.elf")
                nop_bin_path = os.path.join(work, "shim_no_bank_select.bin")
                with open(nop_elf_path, "wb") as fh:
                    fh.write(nop_elf)
                with open(nop_bin_path, "wb") as fh:
                    fh.write(bytes(nop_bin))
                run_shim_neg("shim-without-stack-selection-setup",
                             nop_bin_path, nop_elf_path, 0, "shim",
                             "expected exactly two 'msr spsel'")

            quad_off = harness.nm_symbols("aarch64-linux-gnu-nm", SHIM_SP0_ELF)[
                "myos_a8_shim_image_entry"] - shim_start
            tampered = bytearray(shim_bin_blob)
            struct.pack_into("<Q", tampered, quad_off, 0x42000000)
            tampered_path = os.path.join(work, "shim_tampered_entry.bin")
            with open(tampered_path, "wb") as fh:
                fh.write(bytes(tampered))
            run_shim_neg("tampered-shim-image-entry-word", tampered_path,
                         SHIM_SP0_ELF, 0, "shim", "branches to")

            future_src = os.path.join(work, "future_shim_source.S")
            with open(future_src, "w") as fh:
                fh.write("/* fixture */\n")
            future = time.time() + 3600
            os.utime(future_src, (future, future))
            run_shim_neg("stale-boot-shim", SHIM_SP0_BIN, SHIM_SP0_ELF, 0,
                         "stale", "boot shim is older", shim_source=future_src)

            section("M7.15 incorrect trigger PC detected pre-launch (ELF symtab patch)")
            raw_main_addr = syms["myos_a8_raw_main"]
            with open(RUNTIME_ELF, "rb") as fh:
                elf_blob = fh.read()
            try:
                shifted_elf = validator.elf_patch_symbol(
                    elf_blob, "myos_a8_raw_main", new_value=raw_main_addr + 4)
            except ValueError as exc:
                check("shifted-trigger-ELF fixture builds", False, str(exc))
            else:
                shifted_path = os.path.join(work, "runtime_shifted_trigger.elf")
                with open(shifted_path, "wb") as fh:
                    fh.write(shifted_elf)
                rc, out = run_cli(["--image", RUNTIME_BIN, "--elf", shifted_path,
                                   "--evidence-dir",
                                   os.path.join(work, "case_shifted_trigger", "evidence")])
                check("incorrect trigger PC fails with reason 'trigger'",
                      rc != 0 and "FAIL: trigger" in out and
                      "not the first instruction" in out, "rc=%d tail=%r" % (rc, out[-250:]))

prod_after = sha_or_none(PROD_BIN)
if prod_before is None or prod_after is None:
    print("  SKIP production-hash comparison (build/raw_entry.bin absent; run `make raw-entry`)")
else:
    check("production raw_entry.bin unchanged by test-only builds", prod_before == prod_after,
          "%s != %s" % (prod_before, prod_after))

section("M7.15 production artifact vs test-only artifacts (M7.15 section 8 purity)")
if os.path.isfile(PROD_BIN) and os.path.isfile(RUNTIME_BIN):
    with open(PROD_BIN, "rb") as fh:
        prod_blob = fh.read()
    with open(RUNTIME_BIN, "rb") as fh:
        rt_blob = fh.read()

    def brk_count(blob):
        nwords = len(blob) // 4
        words = struct.unpack("<%dI" % nwords, blob[:nwords * 4])
        return sum(1 for w in words if (w & 0xFFE0001F) == 0xD4200000)

    check("production raw_entry.bin contains no test-only BRK trigger",
          brk_count(prod_blob) == 0, "brk words=%d" % brk_count(prod_blob))
    check("runtime-test image contains exactly the BRK trigger",
          brk_count(rt_blob) == 1, "brk words=%d" % brk_count(rt_blob))
    check("test-only runtime image is byte-distinct from the production image",
          prod_blob != rt_blob)
else:
    print("  SKIP production-purity comparison (production or runtime image absent)")

if os.path.isfile(PROD_ELF) and NM_PRESENT:
    prod_syms = harness.nm_symbols("aarch64-linux-gnu-nm", PROD_ELF)
    leaked = [n for n in ("myos_a8_shim_start", "myos_a8_shim_image_entry",
                          "myos_a8_shim_sp_area_top", "myos_a8_shim_end")
              if n in prod_syms]
    check("production ELF carries no boot-shim symbols", not leaked, ", ".join(leaked))
else:
    print("  SKIP production-ELF symbol purity (build/raw_entry.elf or nm absent)")

shutil.rmtree(work, ignore_errors=True)
print("== %s: %d checks, %d failures =="
      % ("HOST-TEST-A8-EXC-RUNTIME-HARNESS", counter["n"], len(failures)))
if failures:
    for name in failures:
        print("  FAILED: %s" % name)
    sys.exit(1)
sys.exit(0)
