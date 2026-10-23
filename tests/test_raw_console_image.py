#!/usr/bin/env python3
"""Validate the opt-in inline-font console raw image (offline)."""

import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "build", "raw_entry_console.elf")
BIN = os.path.join(ROOT, "build", "raw_entry_console.bin")
PROD = os.path.join(ROOT, "build", "raw_entry.bin")
PROD_SHA = "762d92db7fbd99cc418fb73c25bf076edeb542eeec1bae3b1af08ee456f2e3f2"

ADDRESSES = {
    ".text": 0x0000,
    ".report": 0x0200,
    ".stack": 0x0240,
    ".vectors": 0x4800,
    ".excrecord": 0x5000,
    ".code": 0x5050,
}

failures = 0


def check(name, condition, detail=""):
    global failures
    if condition:
        print("PASS %s" % name)
    else:
        print("FAIL %s%s" % (name, (" :: " + detail) if detail else ""))
        failures += 1


def tool(names):
    for name in names:
        try:
            subprocess.run([name, "--version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
            return name
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def run(command):
    return subprocess.run(command, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)


def parse_sections(text):
    sections = {}
    pattern = re.compile(
        r"\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+"
        r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            sections[match.group(1)] = {
                "type": match.group(2),
                "addr": int(match.group(3), 16),
                "off": int(match.group(4), 16),
                "size": int(match.group(5), 16),
            }
    return sections


def parse_symbols(text):
    symbols = {}
    pattern = re.compile(r"([0-9a-fA-F]+)\s+\S\s+(\S+)")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            symbols[match.group(2)] = int(match.group(1), 16)
    return symbols


def function_block(disassembly, name):
    match = re.search(r"<%s>:\n(.*?)(?:\n\n|\Z)" % re.escape(name),
                      disassembly, re.S)
    return match.group(1) if match else None


def main(argv):
    require = "--require-artifact" in argv
    if not (os.path.exists(ELF) and os.path.exists(BIN)):
        message = "console image not built (run `make raw-entry-console`)"
        if require:
            print("FAIL %s" % message)
            return 1
        print("NOTE: %s; skipping" % message)
        return 0

    readelf = tool(["aarch64-linux-gnu-readelf", "readelf"])
    nm = tool(["aarch64-linux-gnu-nm", "nm"])
    objdump = tool(["aarch64-linux-gnu-objdump", "objdump"])
    if not (readelf and nm and objdump):
        message = "no ELF inspection tools (readelf/nm/objdump)"
        if require:
            print("FAIL %s" % message)
            return 1
        print("NOTE: %s; skipping" % message)
        return 0

    sections = parse_sections(run([readelf, "-SW", ELF]).stdout)
    symbols = parse_symbols(run([nm, ELF]).stdout)
    disassembly = run([objdump, "-d", ELF]).stdout

    for name, address in ADDRESSES.items():
        check("section %s at %#x" % (name, address),
              name in sections and sections[name]["addr"] == address,
              hex(sections.get(name, {}).get("addr", -1)))
    check("no uninitialized .bss",
          ".bss" not in sections or sections[".bss"]["size"] == 0)

    header = run([readelf, "-h", ELF]).stdout
    entry = re.search(r"Entry point address:\s*0x([0-9a-fA-F]+)", header)
    check("entry point is zero",
          entry is not None and int(entry.group(1), 16) == 0)

    for name in ("myos_a8_raw_main", "console_init", "shell_run",
                 "myos_a8_video_read", "myos_font8x8_basic",
                 "console_clear", "console_putc", "console_scroll"):
        check("symbol %s present" % name, name in symbols)
    if ".code" in sections:
        code = sections[".code"]
        for name in ("console_init", "shell_run", "myos_a8_video_read",
                     "myos_font8x8_basic"):
            address = symbols.get(name, -1)
            check("%s lives in .code" % name,
                  code["addr"] <= address < code["addr"] + code["size"],
                  hex(address))

    main_body = function_block(disassembly, "myos_a8_raw_main")
    check("raw_main function present", main_body is not None)
    if main_body:
        check("raw_main calls console_init",
              re.search(r"\bbl\s+\S+\s+<console_init>", main_body) is not None)
        check("raw_main calls shell_run",
              re.search(r"\bbl\s+\S+\s+<shell_run>", main_body) is not None)

    init_body = function_block(disassembly, "console_init")
    check("console_init function present", init_body is not None)
    if init_body:
        check("console_init reads Video through myos_a8_video_read",
              re.search(r"\bbl\s+\S+\s+<myos_a8_video_read>",
                        init_body) is not None)
        check("console_init bypasses myos_a8_fb_from_boot_args",
              "myos_a8_fb_from_boot_args" not in init_body)

    relocations = run([readelf, "-rW", ELF])
    check("no relocations remain",
          "There are no relocations" in relocations.stdout)

    blob = open(BIN, "rb").read()
    check("image fits the 1 MiB upload buffer", len(blob) <= 0x100000,
          str(len(blob)))
    check("console greeting is present",
          b"MyOS v0.1 - Hello User!" in blob)
    check("report magic remains at 0x200",
          blob[0x200:0x208] == b"MYOSA8R1")

    if os.path.exists(PROD):
        production = open(PROD, "rb").read()
        check("production SHA-256 unchanged",
              hashlib.sha256(production).hexdigest() == PROD_SHA)
        check("production image has no console greeting",
              b"MyOS v0.1 - Hello User!" not in production)
    elif require:
        check("production image present", False)

    if failures:
        print("raw console image checks: FAIL (%d)" % failures)
        return 1
    print("raw console image checks: ALL PASS (%d bytes)" % len(blob))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
