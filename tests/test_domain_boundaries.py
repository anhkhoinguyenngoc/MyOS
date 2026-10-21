#!/usr/bin/env python3
"""
Domain-boundary regression tests (M6.3).

Enforces the Dune-inspired architecture split:
  - Core/arch must not contain QEMU machine addresses or platform includes.
  - The kernel link inputs must not include host tools or A8 payload objects.
  - Moved files exist at the correct domain locations; the old boot/ path is gone.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_path_exists(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        raise AssertionError("expected %s to exist" % rel)


def check_path_missing(rel):
    path = os.path.join(ROOT, rel)
    if os.path.exists(path):
        raise AssertionError("expected %s to be absent" % rel)


def assert_no_machine_address_in_core_arch():
    """Core kernel and architecture must be free of QEMU machine constants."""
    for root, dirs, files in os.walk(os.path.join(ROOT, "kernel")):
        for f in files:
            if f.endswith((".c", ".h", ".S", ".s", ".ld")):
                p = os.path.join(root, f)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                for bad in ("0x09000000", "0x40080000", "MYOS_QEMU_VIRT_PL011_BASE"):
                    if bad in text:
                        raise AssertionError("%s contains %s" % (p, bad))
                if "#include" in text:
                    for line in text.splitlines():
                        if line.strip().startswith("#include"):
                            if "platform/qemu-virt" in line:
                                raise AssertionError("%s includes platform/qemu-virt: %s" % (p, line))

    for root, dirs, files in os.walk(os.path.join(ROOT, "arch")):
        for f in files:
            if f.endswith((".c", ".h", ".S", ".s", ".ld")):
                p = os.path.join(root, f)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                for bad in ("0x09000000", "0x40080000", "MYOS_PLATFORM_QEMU_VIRT"):
                    if bad in text:
                        raise AssertionError("%s contains %s" % (p, bad))


def assert_kernel_link_inputs_clean():
    makefile = os.path.join(ROOT, "Makefile")
    with open(makefile, encoding="utf-8") as fh:
        txt = fh.read()
    for bad in ("tools/host", "iphone6.o", "boot_diag_m1.o", "pongo_probe.o", "m1_entry.o"):
        if bad in txt:
            # Check if it's in the kernel link context
            # Look specifically in the KERNEL_ELF recipe
            m = re.search(r'^\$\(KERNEL_ELF\):.*?(?=^\S)', txt, re.M | re.S)
            if m and bad in m.group(0):
                raise AssertionError("kernel link inputs include %s" % bad)


def assert_moved_files_present_and_old_gone():
    # Moved files at new locations
    check_path_exists("platform/qemu-virt/qemu_virt_defs.h")
    check_path_exists("platform/qemu-virt/start.S")
    check_path_exists("platform/qemu-virt/kernel.ld")
    check_path_exists("platform/qemu-virt/uart_pl011.S")
    check_path_exists("platform/apple/a8/m1_entry.S")
    check_path_exists("platform/apple/a8/m1.ld")
    check_path_exists("platform/apple/a8/pongo_probe.S")
    check_path_exists("platform/apple/a8/pongo_probe.ld")
    check_path_exists("platform/apple/a8/boot_diag_m1.c")
    check_path_exists("kernel/boot_diag.c")
    check_path_exists("kernel/boot_diag.h")
    # Old paths gone
    check_path_missing("boot/aarch64/start.S")
    check_path_missing("boot/aarch64/kernel.ld")
    check_path_missing("boot/aarch64/m1_entry.S")
    check_path_missing("boot/aarch64/m1.ld")
    check_path_missing("boot/aarch64/pongo_probe.S")
    check_path_missing("boot/aarch64/pongo_probe.ld")
    check_path_missing("boot/aarch64")
    check_path_missing("arch/aarch64/boot_diag.c")
    check_path_missing("arch/aarch64/boot_diag.h")
    check_path_missing("arch/aarch64/boot_diag_m1.c")
    check_path_missing("arch/aarch64/uart_pl011.S")


def assert_single_source_qemu_constants():
    defs = os.path.join(ROOT, "platform/qemu-virt/qemu_virt_defs.h")
    with open(defs, encoding="utf-8") as fh:
        d = fh.read()
    assert "MYOS_QEMU_VIRT_PL011_BASE" in d
    assert "MYOS_QEMU_VIRT_LOAD_ADDR" in d
    qemu_h = os.path.join(ROOT, "platform/qemu-virt/qemu_virt.h")
    with open(qemu_h, encoding="utf-8") as fh:
        h = fh.read()
    assert "qemu_virt_defs.h" in h
    assert "#define MYOS_QEMU_VIRT_PL011_BASE" not in h
    for rel in ("platform/qemu-virt/start.S", "platform/qemu-virt/uart_pl011.S"):
        p = os.path.join(ROOT, rel)
        with open(p, encoding="utf-8") as fh:
            t = fh.read()
        assert "qemu_virt_defs.h" in t
        assert "MYOS_QEMU_VIRT_PL011_BASE" in t
        assert "0x09000000" not in t


def main():
    assert_moved_files_present_and_old_gone()
    assert_no_machine_address_in_core_arch()
    assert_kernel_link_inputs_clean()
    assert_single_source_qemu_constants()
    print("DOMAIN BOUNDARIES: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
