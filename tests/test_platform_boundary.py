#!/usr/bin/env python3
"""M6 platform-boundary regression tests (offline, no hardware).

Validates that:
  * the QEMU virt descriptor matches the actual boot path values
    (PL011 base and kernel linker load address) and cannot drift;
  * the platform selection gate refuses to build apple-a8 and the QEMU-only
    boot/UART sources are guarded against non-QEMU builds;
  * the Apple A8 descriptor is explicitly UNSUPPORTED, declares no capability,
    and asserts no MMIO or memory address;
  * the boot-diagnostic framework no longer claims an implemented A8 target;
  * the QEMU kernel image does not contain the (unlinked) A8 descriptor.

It compiles and runs the real descriptor translation units with the host
compiler. No hardware, no QEMU boot, no device interaction.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

QEMU_H = os.path.join("platform", "qemu-virt", "qemu_virt.h")
QEMU_DEFS = os.path.join("platform", "qemu-virt", "qemu_virt_defs.h")
QEMU_C = os.path.join("platform", "qemu-virt", "qemu_virt.c")
QEMU_START = os.path.join("platform", "qemu-virt", "start.S")
QEMU_UART = os.path.join("platform", "qemu-virt", "uart_pl011.S")
QEMU_LD = os.path.join("platform", "qemu-virt", "kernel.ld")
A8_H = os.path.join("platform", "apple", "a8", "iphone6.h")
A8_C = os.path.join("platform", "apple", "a8", "iphone6.c")
PLATFORM_C = os.path.join("platform", "platform.c")

PL011_QEMU = 0x09000000
LOAD_QEMU = 0x40080000


def read(relpath):
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        return f.read()


def parse_c_hex(text, name):
    match = re.search(r"#define\s+" + re.escape(name) + r"\s+0x([0-9A-Fa-f]+)",
                      text)
    assert match, "could not find #define %s" % name
    return int(match.group(1), 16)


def test_qemu_descriptor_matches_boot_path():
    # The QEMU virt constants are defined exactly once (qemu_virt_defs.h) and
    # included by the descriptor header and both assembly units.
    defs = read(QEMU_DEFS)
    assert parse_c_hex(defs, "MYOS_QEMU_VIRT_PL011_BASE") == PL011_QEMU
    assert parse_c_hex(defs, "MYOS_QEMU_VIRT_LOAD_ADDR") == LOAD_QEMU

    qemu_h = read(QEMU_H)
    assert "qemu_virt_defs.h" in qemu_h, \
        "qemu_virt.h must include the single QEMU constants header"
    assert "#define MYOS_QEMU_VIRT_PL011_BASE" not in qemu_h, \
        "qemu_virt.h must not redefine the PL011 base"

    # The early boot stub and the PL011 driver must use the shared definition,
    # not carry their own copy of the value.
    for rel in (QEMU_START, QEMU_UART):
        text = read(rel)
        assert "qemu_virt_defs.h" in text, \
            "%s must include the single QEMU constants header" % rel
        assert "MYOS_QEMU_VIRT_PL011_BASE" in text, \
            "%s must use the shared PL011 base" % rel
        assert "0x09000000" not in text, \
            "%s must not hard-code the PL011 base" % rel

    # The kernel linker script uses the same load address.
    ld = read(QEMU_LD)
    m = re.search(r"\.\s*=\s*0x([0-9A-Fa-f]+)\s*;", ld)
    assert m, "kernel.ld has no load address assignment"
    assert int(m.group(1), 16) == LOAD_QEMU, "kernel.ld load address drifted"


def test_selection_gate_blocks_apple_a8():
    pc = read(PLATFORM_C)
    # An A8 selection must be a hard compile error, not a rename of QEMU code.
    a8_branch = re.search(
        r"#elif\s+defined\(MYOS_PLATFORM_APPLE_A8\)(.*?)(#elif|#else|#endif)",
        pc, re.DOTALL)
    assert a8_branch, "platform.c has no explicit apple-a8 branch"
    assert "#error" in a8_branch.group(1), \
        "apple-a8 selection must fail the build with #error"

    # Default/known-good selection is QEMU virt.
    assert "myos_platform_qemu_virt" in pc
    assert "MYOS_PLATFORM_QEMU_VIRT" in pc

    # The Makefile must gate PLATFORM and reject apple-a8.
    mk = read("Makefile")
    assert re.search(r"PLATFORM\s*\?=\s*qemu-virt", mk), \
        "Makefile must default PLATFORM to qemu-virt"
    assert re.search(r"PLATFORM\)\s*,\s*apple-a8", mk), \
        "Makefile must handle PLATFORM=apple-a8"
    assert re.search(r"\$\(error[^)]*apple-a8", mk, re.IGNORECASE), \
        "Makefile must refuse PLATFORM=apple-a8 with $(error ...)"

    # The kernel link must not contain the A8 descriptor object.
    assert "iphone6.o" not in mk, \
        "A8 descriptor must not be linked into the QEMU kernel"


def test_qemu_only_sources_are_guarded():
    for rel in (QEMU_START, QEMU_UART):
        text = read(rel)
        assert "MYOS_PLATFORM_QEMU_VIRT" in text, \
            "%s is missing the platform guard" % rel
        assert "#error" in text, \
            "%s must #error when built for a non-QEMU platform" % rel


def test_apple_a8_descriptor_is_honest():
    a8_c = read(A8_C)
    a8_h = read(A8_H)
    blob = a8_c + a8_h

    assert "MYOS_PLATFORM_STATUS_UNSUPPORTED" in a8_c, \
        "A8 descriptor must be UNSUPPORTED"
    assert "MYOS_ADDR_UNKNOWN" in a8_c, \
        "A8 descriptor must use MYOS_ADDR_UNKNOWN for unestablished values"
    assert re.search(r"\.capabilities\s*=\s*0", a8_c), \
        "A8 descriptor must declare no capability"
    assert re.search(r"\.uart_mmio_base\s*=\s*MYOS_ADDR_UNKNOWN", a8_c), \
        "A8 descriptor must not assert a UART address"
    assert re.search(r"\.link_load_addr\s*=\s*MYOS_ADDR_UNKNOWN", a8_c), \
        "A8 descriptor must not assert a load address"

    # No QEMU-only constants may leak into the A8 platform source.
    for needle in ("0x09000000", "0x40080000", "PL011"):
        assert needle not in blob, \
            "QEMU-only value %r leaked into the A8 platform source" % needle

    # The A8 source directory as a whole must be free of those values too.
    a8_dir = os.path.join(ROOT, "platform", "apple", "a8")
    for name in sorted(os.listdir(a8_dir)):
        text = read(os.path.join("platform", "apple", "a8", name))
        assert "0x09000000" not in text, name
        assert "0x40080000" not in text, name


def test_boot_diag_banner_no_false_dual_target():
    diag_c = read(os.path.join("kernel", "boot_diag.c"))
    banner = diag_c.split("*/", 1)[0]
    # The old banner claimed an implemented iPhone 6 target; it must not.
    assert "target: iphone6 (Apple A8 real hardware) AND qemu-virt" not in banner, \
        "boot_diag.c still claims an implemented dual target"

    diag_h = read(os.path.join("kernel", "boot_diag.h"))
    # The console comment must say the iPhone console is not implemented.
    assert "not implemented" in diag_h or "Not implemented" in diag_h, \
        "boot_diag.h must state the iPhone console is not implemented"


def _host_cc():
    for cc in ("cc", "gcc"):
        if subprocess.run(["sh", "-c", "command -v %s" % cc],
                          stdout=subprocess.DEVNULL).returncode == 0:
            return cc
    return None


def test_descriptors_compile_and_run():
    cc = _host_cc()
    assert cc, "no host C compiler found"

    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "platform_probe")
        cmd = [cc, "-std=c11", "-Wall", "-Wextra", "-I", ROOT,
               os.path.join(ROOT, "tests", "platform_probe_main.c"),
               os.path.join(ROOT, QEMU_C),
               os.path.join(ROOT, A8_C),
               "-o", exe]
        build = subprocess.run(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        assert build.returncode == 0, "host probe build failed:\n" + build.stdout

        run = subprocess.run([exe], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        assert run.returncode == 0, run.stdout

    fields = {}
    for line in run.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()

    # QEMU virt: known-good and backed by the boot path.
    assert fields["qemu.status"] == "0", "qemu-virt must be KNOWN_GOOD"
    assert fields["qemu.uart_mmio_base"] == "0x9000000"
    assert fields["qemu.link_load_addr"] == "0x40080000"
    assert int(fields["qemu.capabilities"]) & 0x1, "qemu-virt must claim UART"
    assert fields["qemu.unresolved_null"] == "1"

    # Apple A8: unsupported, no claims, open questions listed.
    assert fields["a8.status"] == "1", "apple-a8 must be UNSUPPORTED"
    assert fields["a8.capabilities"] == "0", "apple-a8 must claim nothing"
    assert fields["a8.uart_mmio_base"] == "0x0"
    assert fields["a8.link_load_addr"] == "0x0"
    assert fields["a8.unresolved_null"] == "0"
    assert int(fields["a8.unresolved_count"]) >= 4, \
        "apple-a8 must list its unresolved handoff requirements"


def test_kernel_image_excludes_apple_a8():
    elf = os.path.join(ROOT, "build", "myos_kernel.elf")
    if not os.path.exists(elf):
        return  # kernel not built in this context; the QEMU target covers it
    with open(elf, "rb") as f:
        data = f.read()
    assert b"qemu-virt" in data, "kernel must embed the selected platform name"
    assert b"apple-a8" not in data, \
        "the unselected A8 descriptor must not be linked into the kernel"
    assert b"kernel entry: confirm" not in data, \
        "A8 requirement strings must not be linked into the kernel"


def _have(cmd):
    return subprocess.run(["sh", "-c", "command -v %s" % cmd],
                          stdout=subprocess.DEVNULL).returncode == 0


def _expect_intended_failure(cmd, needle, env=None):
    """Run cmd; assert it fails with `needle` (not an unrelated tool error)."""
    result = subprocess.run(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env)
    assert result.returncode != 0, \
        "expected failure but command succeeded: %s" % cmd
    assert needle in result.stdout, \
        "failure was not for the intended reason.\nexpected %r in:\n%s" % (
            needle, result.stdout)


def test_negative_builds_fail_for_intended_reason():
    """Each platform gate must reject its target with its own message, not a
    generic syntax/tool error."""
    cc = _host_cc()
    assert cc, "no host C compiler found"

    def host_compile(defines):
        return [cc, "-std=c11", "-I", ROOT] + defines + [
            "-c", os.path.join(ROOT, PLATFORM_C), "-o", os.devnull]

    # Makefile gate: selecting apple-a8 must stop at parse time.
    _expect_intended_failure(
        ["make", "-C", ROOT, "PLATFORM=apple-a8", "kernel"],
        "PLATFORM=apple-a8 is UNSUPPORTED")
    # Unknown platform name is rejected with its own message.
    _expect_intended_failure(
        ["make", "-C", ROOT, "PLATFORM=bogus", "kernel"],
        "Unknown PLATFORM 'bogus'")

    # platform.c gates, exercised directly (host compiler).
    _expect_intended_failure(
        host_compile(["-DMYOS_PLATFORM_APPLE_A8=1"]),
        "PLATFORM=apple-a8 is not implemented")
    _expect_intended_failure(
        host_compile(["-DMYOS_PLATFORM_QEMU_VIRT=1",
                      "-DMYOS_PLATFORM_APPLE_A8=1"]),
        "Select exactly one MyOS platform")
    _expect_intended_failure(
        host_compile([]),
        "No MyOS platform selected")

    # QEMU-only boot/UART sources refuse a non-QEMU assembly.
    if _have("aarch64-linux-gnu-gcc"):
        asm = ["aarch64-linux-gnu-gcc", "-ffreestanding", "-nostdlib", "-O2",
               "-c", "-o", os.devnull]
        _expect_intended_failure(
            asm + [os.path.join(ROOT, QEMU_START)],
            "is QEMU virt boot code only")
        _expect_intended_failure(
            asm + [os.path.join(ROOT, QEMU_UART)],
            "is the QEMU virt PL011 driver only")


def main():
    tests = [
        test_qemu_descriptor_matches_boot_path,
        test_selection_gate_blocks_apple_a8,
        test_qemu_only_sources_are_guarded,
        test_apple_a8_descriptor_is_honest,
        test_boot_diag_banner_no_false_dual_target,
        test_descriptors_compile_and_run,
        test_kernel_image_excludes_apple_a8,
        test_negative_builds_fail_for_intended_reason,
    ]
    for test in tests:
        test()
        print("PASS %s" % test.__name__)
    print("PLATFORM BOUNDARY TESTS: PASS")


if __name__ == "__main__":
    sys.exit(main())
