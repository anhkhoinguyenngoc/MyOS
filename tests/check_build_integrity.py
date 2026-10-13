#!/usr/bin/env python3
"""check_build_integrity.py -- deterministic, offline verification of the
AArch64 build artifacts against the authoritative expectations declared in the
Makefile and linker scripts.

Artifact paths are derived from Makefile variables (not hardcoded), and entry
points, load bases, section names and their addresses are parsed from the
linker scripts (the source of truth).  Nothing here invents an address.

For every artifact this verifies:
  * the required host build tools exist (reports exactly which are missing),
  * the ELF class / type / machine is ELF64 / EXEC / AArch64,
  * the ELF entry point matches the linker ENTRY symbol and load base,
  * every output section declared in the linker script exists,
  * .text is linked at the script's load base,
  * explicitly addressed sections (e.g. .report 0x200) sit at that address,
  * the image is freestanding (no dynamic-linking sections),
  * the flat binary honours its documented size/format constraints.

The checker is read-only: it never modifies an artifact.  It does not require
QEMU unless --require-qemu is given.  A missing file, missing Makefile
variable, missing linker script or missing required tool is a FAIL, never a
silent pass.

Exit 0 iff every check passes.  Prints PASS_<name> / FAIL_<name> lines.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys

CROSS = "aarch64-linux-gnu-"
BUILD_TOOLS = [CROSS + t for t in ("gcc", "ld", "objcopy", "readelf", "nm", "objdump")]
QEMU_TOOL = "qemu-system-aarch64"

# Verified probe artifact (M-P1.5), pinned as a regression guard so the build
# recipe cannot silently change the validated image.
PROBE_PIN_SIZE = 608
PROBE_PIN_SHA256 = "c90162af659a3eb0a7eda9954dee07aaa96e52c3407434320d3ed0cc4c1d612a"

# M1 payload budget: "must stay <= 2016 bytes" (Makefile), also enforced by
# tests/offline_m1.sh.  The DFU exec block is 0x800 bytes minus the 0x50 header.
M1_BIN_MAX = 2016

# (artifact name, Makefile ELF variable, Makefile binary variable or None)
ARTIFACT_VARS = (
    ("m1", "PAYLOAD_ELF", "PAYLOAD_BIN"),
    ("kernel", "KERNEL_ELF", None),
    ("probe", "PROBE_ELF", "PROBE_BIN"),
)

REPO = "."
_verbose = False
_results: list[tuple[str, bool, str]] = []


class MissingSource(Exception):
    """An authoritative build input could not be located or parsed."""


def rp(path: str) -> str:
    return os.path.join(REPO, path)


def check(name: str, ok: bool, detail: str = "") -> None:
    ok = bool(ok)
    _results.append((name, ok, detail))
    line = f"{'PASS' if ok else 'FAIL'}_{name}"
    if detail and (not ok or _verbose):
        line += f"  ({detail})"
    print(line)


def run(tool: str, args: list[str]) -> str | None:
    exe = shutil.which(tool)
    if exe is None:
        return None
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True)
    except OSError:
        return None
    return proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Makefile / linker-script parsing (authoritative expectations)
# ---------------------------------------------------------------------------

def makefile_vars(text: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(
            r"^([A-Za-z_][A-Za-z0-9_]*)\s*:=\s*(.*?)\s*$", text, re.M
        )
    }


def makefile_expand(value: str, variables: dict[str, str], depth: int = 0) -> str:
    if depth > 16:
        return value

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return makefile_expand(variables[name], variables, depth + 1)
        return m.group(0)

    return re.sub(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", repl, value)


def makefile_rule_linker(text: str, target_var: str) -> str | None:
    """Return the -T linker script used by the rule that builds $(target_var)."""
    token = f"$({target_var}):"
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(token):
            # Recipe/prerequisite lines are indented (tab recipe, space
            # continuation).  The block ends at the next column-0 line.
            for line in lines[i + 1 :]:
                if not line:
                    continue
                if not line[0].isspace():
                    break
                m = re.search(r"-T\s+(\S+\.ld)", line)
                if m:
                    return m.group(1)
    return None


_SEC_RE = re.compile(
    r"^\s*(\.[A-Za-z0-9_.]+)\s*(?:(0x[0-9a-fA-F]+)|(?:ALIGN\([^)]*\)))?\s*:",
    re.M,
)


def parse_linker(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    entry = None
    m = re.search(r"ENTRY\(\s*([^)\s]+)\s*\)", text)
    if m:
        entry = m.group(1)

    bases = [
        int(x, 16)
        for x in re.findall(r"^\s*\.\s*=\s*(0x[0-9a-fA-F]+)\s*;", text, re.M)
    ]

    sections: list[str] = []
    addresses: dict[str, int] = {}
    for m in _SEC_RE.finditer(text):
        name = m.group(1)
        if name in sections:
            continue
        sections.append(name)
        if m.group(2):
            addresses[name] = int(m.group(2), 16)

    asserts = re.findall(r"ASSERT\(\s*(.+?)\s*,", text)
    size_bound = None
    for expr in asserts:
        m = re.search(r"\.\s*<=\s*(0x[0-9a-fA-F]+)", expr)
        if m:
            size_bound = int(m.group(1), 16)

    return {
        "entry": entry,
        "base": bases[0] if bases else None,
        "sections": sections,
        "addresses": addresses,
        "size_bound": size_bound,
    }


def build_specs(makefile_text: str) -> list[dict]:
    variables = makefile_vars(makefile_text)

    def need(var: str) -> str:
        if var not in variables:
            raise MissingSource(f"Makefile variable {var} not found")
        value = makefile_expand(variables[var], variables)
        if not value or "$(" in value:
            raise MissingSource(f"Makefile variable {var} did not expand: {value!r}")
        return value

    specs = []
    for name, elf_var, bin_var in ARTIFACT_VARS:
        elf = need(elf_var)
        binary = need(bin_var) if bin_var else None
        linker = makefile_rule_linker(makefile_text, elf_var)
        if not linker:
            raise MissingSource(f"no -T linker script found for {elf_var} in Makefile")
        specs.append({"name": name, "elf": elf, "bin": binary, "ld": linker})
    return specs


# ---------------------------------------------------------------------------
# ELF helpers
# ---------------------------------------------------------------------------

_READELF_SEC_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+([0-9a-fA-F]+)\s+"
    r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\b"
)


def elf_header(path: str) -> dict | None:
    out = run(CROSS + "readelf", ["-h", path])
    if out is None:
        return None
    header: dict[str, str | None] = {}
    for key, pat in (
        ("class", r"Class:\s+(\S+)"),
        ("type", r"Type:\s+(\S+)"),
        ("machine", r"Machine:\s+(\S+)"),
        ("entry", r"Entry point address:\s+(0x[0-9a-fA-F]+)"),
    ):
        m = re.search(pat, out)
        header[key] = m.group(1) if m else None
    return header


def elf_sections(path: str) -> dict | None:
    out = run(CROSS + "readelf", ["-SW", path])
    if out is None:
        return None
    sections: dict[str, dict] = {}
    for line in out.splitlines():
        m = _READELF_SEC_RE.match(line)
        if not m:
            continue
        name, typ, addr, off, size = m.groups()
        sections[name] = {
            "type": typ,
            "addr": int(addr, 16),
            "off": int(off, 16),
            "size": int(size, 16),
        }
    return sections


def nm_symbol(path: str, name: str) -> int | None:
    out = run(CROSS + "nm", [path])
    if out is None:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            try:
                return int(parts[0], 16)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_tools(require_qemu: bool) -> list[str]:
    missing: list[str] = []
    for tool in BUILD_TOOLS:
        ok = shutil.which(tool) is not None
        check(f"tool_{tool}", ok, "" if ok else "executable not found in PATH")
        if not ok:
            missing.append(tool)
    if require_qemu:
        ok = shutil.which(QEMU_TOOL) is not None
        check(f"tool_{QEMU_TOOL}", ok, "" if ok else "executable not found in PATH")
        if not ok:
            missing.append(QEMU_TOOL)
    return missing


def check_artifact(spec: dict, ld: dict) -> None:
    name = spec["name"]
    base = ld["base"]

    elf_path = rp(spec["elf"])
    if not os.path.isfile(elf_path):
        check(f"{name}_elf_exists", False, f"missing {spec['elf']}")
        return
    check(f"{name}_elf_exists", True, spec["elf"])

    header = elf_header(elf_path)
    if header is None:
        check(f"{name}_elf_format", False, f"readelf failed on {spec['elf']}")
        return
    ok = (
        header["class"] == "ELF64"
        and header["type"] == "EXEC"
        and header["machine"] == "AArch64"
    )
    check(
        f"{name}_elf_format",
        ok,
        f"class={header['class']} type={header['type']} machine={header['machine']}",
    )

    if base is None:
        check(f"{name}_linker_base", False, f"no '. = 0x...' in {spec['ld']}")
    elif header["entry"] is None:
        check(f"{name}_entry_matches_base", False, "ELF has no entry point")
    else:
        check(
            f"{name}_entry_matches_base",
            int(header["entry"], 16) == base,
            f"entry={header['entry']} base=0x{base:x}",
        )

    if ld["entry"] is None:
        check(f"{name}_linker_entry", False, f"no ENTRY() in {spec['ld']}")
    else:
        sym = nm_symbol(elf_path, ld["entry"])
        check(
            f"{name}_entry_symbol",
            sym is not None and sym == base,
            f"{ld['entry']}=0x{sym:x} base=0x{base:x}"
            if sym is not None
            else f"{ld['entry']} not found in ELF",
        )

    sections = elf_sections(elf_path)
    if sections is None:
        check(f"{name}_sections", False, f"readelf -S failed on {spec['elf']}")
    else:
        if not ld["sections"]:
            check(f"{name}_sections", False, f"no output sections parsed from {spec['ld']}")
        for sec in ld["sections"]:
            present = sec in sections
            check(
                f"{name}_section_{sec}",
                present,
                f"declared in {spec['ld']} but absent from ELF" if not present else "",
            )
        if ".text" in sections and base is not None:
            check(
                f"{name}_text_at_base",
                sections[".text"]["addr"] == base,
                f".text@0x{sections['.text']['addr']:x} base=0x{base:x}",
            )
        for sec, addr in ld["addresses"].items():
            got = sections.get(sec, {}).get("addr")
            check(
                f"{name}_section_{sec}_addr",
                got == addr,
                f"{sec}@0x{got:x} expected=0x{addr:x}"
                if got is not None
                else f"{sec} missing",
            )
        dynamic = [
            s
            for s in sections
            if s in (".interp", ".dynamic", ".dynsym", ".got", ".plt", ".hash")
        ]
        check(
            f"{name}_freestanding",
            not dynamic,
            f"dynamic-linking sections present: {dynamic}" if dynamic else "",
        )

    if not spec["bin"]:
        return
    bin_path = rp(spec["bin"])
    if not os.path.isfile(bin_path):
        check(f"{name}_bin_exists", False, f"missing {spec['bin']}")
        return
    with open(bin_path, "rb") as fh:
        data = fh.read()
    check(f"{name}_bin_exists", True, spec["bin"])

    if name == "m1":
        max_size = M1_BIN_MAX
    else:
        max_size = ld["size_bound"]
    if max_size is None:
        check(f"{name}_bin_size", False, "no size bound in linker script")
    else:
        check(
            f"{name}_bin_size",
            0 < len(data) <= max_size,
            f"size={len(data)} max={max_size}",
        )

    if name == "m1":
        check(
            f"{name}_bin_align4",
            len(data) % 4 == 0,
            f"size={len(data)}",
        )


def check_probe_report_magic(makefile_text: str, specs: list[dict]) -> None:
    """Independently cross-check the probe's magic constant (from pongo_probe.S)
    against the built raw image at the .report offset (from pongo_probe.ld)."""
    probe = next((s for s in specs if s["name"] == "probe"), None)
    if probe is None or not probe["bin"]:
        return
    bin_path = rp(probe["bin"])
    if not os.path.isfile(bin_path):
        return  # existence already reported by check_artifact
    ld = parse_linker(rp(probe["ld"]))
    offset = ld["addresses"].get(".report")
    if offset is None:
        check("probe_report_magic", False, f".report address not in {probe['ld']}")
        return

    src_rel = "boot/aarch64/pongo_probe.S"
    if not os.path.isfile(rp(src_rel)):
        check("probe_report_magic", False, f"missing {src_rel}")
        return
    with open(rp(src_rel), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"probe_magic:\s*\.quad\s+(0x[0-9a-fA-F]+)", src)
    if not m:
        check("probe_report_magic", False, "probe_magic .quad not found in pongo_probe.S")
        return
    expected = struct.pack("<Q", int(m.group(1), 16))

    with open(bin_path, "rb") as fh:
        fh.seek(offset)
        got = fh.read(len(expected))
    check(
        "probe_report_magic",
        got == expected,
        f"magic@{offset:#x}={got!r} expected={expected!r}",
    )


def check_probe_pin(specs: list[dict]) -> None:
    probe = next((s for s in specs if s["name"] == "probe"), None)
    if probe is None or not probe["bin"]:
        return
    path = rp(probe["bin"])
    if not os.path.isfile(path):
        return  # existence already reported by check_artifact
    with open(path, "rb") as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    check(
        "probe_pinned_size",
        len(data) == PROBE_PIN_SIZE,
        f"size={len(data)} pinned={PROBE_PIN_SIZE}",
    )
    check(
        "probe_pinned_sha256",
        sha == PROBE_PIN_SHA256,
        f"sha256={sha}",
    )


def main(argv: list[str] | None = None) -> int:
    global REPO, _verbose
    ap = argparse.ArgumentParser(description="AArch64 build integrity checks")
    ap.add_argument("--repo", default=".", help="repository root (default: .)")
    ap.add_argument(
        "--require-qemu",
        action="store_true",
        help="also require qemu-system-aarch64 (for the runtime test target)",
    )
    ap.add_argument(
        "--tools-only", action="store_true", help="verify tool availability only"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="show pass details")
    args = ap.parse_args(argv)

    REPO = os.path.abspath(args.repo)
    _verbose = args.verbose

    print("== build integrity checks ==")
    missing = check_tools(require_qemu=args.require_qemu)

    if not args.tools_only:
        makefile_text = ""
        makefile_path = rp("Makefile")
        if not os.path.isfile(makefile_path):
            check("makefile_present", False, f"missing {makefile_path}")
        else:
            check("makefile_present", True, makefile_path)
            with open(makefile_path, encoding="utf-8") as fh:
                makefile_text = fh.read()

        specs: list[dict] = []
        if makefile_text:
            try:
                specs = build_specs(makefile_text)
                check("artifacts_derived", True, ", ".join(s["name"] for s in specs))
            except (MissingSource, OSError) as exc:
                check("artifacts_derived", False, str(exc))

        for spec in specs:
            ld_path = rp(spec["ld"])
            if not os.path.isfile(ld_path):
                check(f"{spec['name']}_linker_script", False, f"missing {spec['ld']}")
                continue
            check(f"{spec['name']}_linker_script", True, spec["ld"])
            try:
                ld = parse_linker(ld_path)
            except OSError as exc:
                check(f"{spec['name']}_linker_parse", False, str(exc))
                continue
            check_artifact(spec, ld)

        if specs:
            check_probe_report_magic(makefile_text, specs)
            check_probe_pin(specs)

    failed = [n for n, ok, _ in _results if not ok]
    total = len(_results)
    print(f"== {total} checks: {total - len(failed)} passed, {len(failed)} failed ==")
    if missing:
        print("missing required tools: " + ", ".join(missing))
    if failed:
        print("BUILD-INTEGRITY: FAIL")
        return 1
    print("BUILD-INTEGRITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
