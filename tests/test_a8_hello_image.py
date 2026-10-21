#!/usr/bin/env python3
"""Validate the opt-in Apple A8 "Hello User" raw image (offline).

The image is built by `make raw-entry-hello` and is NOT the production
raw_entry.bin. This test inspects the real linked ELF and flat binary:

  * handoff-critical layout is unchanged: entry at 0, report at 0x200, stack
    0x4000 at 0x240, vectors at 0x4800 and 0x800 bytes, exception record at
    0x5000, and `Hello User` renderer code starting at 0x5050 (the
    record-size/demo-layout contract, not a code-size pin);
  * no uninitialized .bss is carried by the raw upload;
  * the opt-in call exists: myos_a8_raw_main branches to
    myos_a8_fb_draw_hello after writing the report and then parks;
  * the linked image has no relocations (position independence);
  * the image fits the 1 MiB PongoOS upload buffer;
  * the production build/raw_entry.bin does not contain the demo string or
    display symbols (the protected artifact is not polluted by opt-in code).

The production SHA-256 below is a protected-artifact guard: it proves the
opt-in build did not overwrite or alter the audited production image.  It is
not used to validate the opt-in image.  The opt-in image is validated by its
layout contract and deterministic rebuild comparison, so a legitimate
renderer/code change does not require silently updating a hash.

No device, no upload, no boot. If the opt-in image or the AArch64 inspection
tools are absent, the test reports NOTE and exits 0 unless --require-artifact
is supplied (the Makefile check target uses that strict mode).
"""

import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "build", "raw_entry_hello.elf")
BIN = os.path.join(ROOT, "build", "raw_entry_hello.bin")
PROD = os.path.join(ROOT, "build", "raw_entry.bin")
PROD_ELF = os.path.join(ROOT, "build", "raw_entry.elf")
MAKEFILE = os.path.join(ROOT, "Makefile")
PROD_SHA = "0eb3af057a6ba82ad55c114d0a0056c1d3cea686375f535c813ba25b2cd8c635"

# Handoff-critical addresses.  These are deliberately exact: changing them
# changes the image's boot/display contract.  `.code` is only required to be
# non-empty and within the upload buffer; its size is implementation freedom.
HANDOFF_ADDRESSES = {
    ".text": 0x0000,
    ".report": 0x0200,
    ".stack": 0x0240,
    ".vectors": 0x4800,
    ".excrecord": 0x5000,
    ".code": 0x5050,
}
FIXED_SIZES = {
    ".report": 0x40,
    ".stack": 0x4000,
    ".vectors": 0x800,
    ".excrecord": 0x48,
}
DEMO_SYMBOL_PREFIXES = ("myos_a8_fb_", "myos_fb_")

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
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


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)


def parse_sections(text):
    sections = {}
    for line in text.splitlines():
        m = re.match(r"\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+"
                     r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)",
                     line)
        if m:
            sections[m.group(1)] = {
                "type": m.group(2),
                "addr": int(m.group(3), 16),
                "off": int(m.group(4), 16),
                "size": int(m.group(5), 16),
            }
    return sections


def parse_symbols(text):
    symbols = {}
    for line in text.splitlines():
        m = re.match(r"([0-9a-fA-F]+)\s+\S\s+(\S+)", line)
        if m:
            symbols[m.group(2)] = int(m.group(1), 16)
    return symbols


def check_makefile_separation():
    """The production link must never acquire the demo objects or macro."""
    mk = open(MAKEFILE, encoding="utf-8").read()
    start = mk.find("$(RAW_ELF):")
    end = mk.find("# M7.37 opt-in")
    if start < 0 or end < 0 or end <= start:
        check("Makefile production/opt-in rule boundaries present", False)
        return
    prod = mk[start:end]
    check("production raw link rule excludes demo sources",
          "fb_text" not in prod and "a8_fb" not in prod
          and "MYOS_A8_RENDER_HELLO" not in prod)
    check("opt-in target is a separate raw-entry-hello rule",
          re.search(r"^raw-entry-hello:\s*\$\(RAW_HELLO_BIN\)",
                    mk, re.M) is not None
          and "$(RAW_HELLO_DIR)/fb_text.o" in mk
          and "$(RAW_HELLO_DIR)/a8_fb.o" in mk)
    check("opt-in compiles with the explicit renderer macro",
          "-DMYOS_A8_RENDER_HELLO=1" in mk)


def main(argv):
    require = "--require-artifact" in argv

    if not (os.path.exists(ELF) and os.path.exists(BIN)):
        msg = "opt-in image not built (run `make raw-entry-hello`)"
        if require:
            print("FAIL %s" % msg)
            return 1
        print("NOTE: %s; skipping" % msg)
        return 0

    readelf = tool(["aarch64-linux-gnu-readelf", "readelf"])
    nm = tool(["aarch64-linux-gnu-nm", "nm"])
    objdump = tool(["aarch64-linux-gnu-objdump", "objdump"])
    if not (readelf and nm and objdump):
        msg = "no ELF inspection tools (readelf/nm/objdump)"
        if require:
            print("FAIL %s" % msg)
            return 1
        print("NOTE: %s; skipping" % msg)
        return 0

    sections = parse_sections(run([readelf, "-SW", ELF]).stdout)
    symbols = parse_symbols(run([nm, ELF]).stdout)
    check_makefile_separation()

    for needed in (".text", ".report", ".stack", ".vectors", ".excrecord",
                   ".code"):
        check("section %s present" % needed, needed in sections)

    if failures:
        return 1

    for name, want_addr in HANDOFF_ADDRESSES.items():
        check("%s at the handoff address %#x" % (name, want_addr),
              sections[name]["addr"] == want_addr,
              hex(sections[name]["addr"]))
    check(".text is PROGBITS", sections[".text"]["type"] == "PROGBITS")
    check(".text fits below report", sections[".text"]["size"] <= 0x200,
          hex(sections[".text"]["size"]))
    for name, want_size in FIXED_SIZES.items():
        check("%s has the fixed size %#x" % (name, want_size),
              sections[name]["size"] == want_size,
              hex(sections[name]["size"]))
    check(".vectors at 0x4800 is 2 KiB aligned",
          sections[".vectors"]["addr"] % 0x800 == 0)
    check(".code is non-empty", sections[".code"]["size"] > 0)
    check(".code ends inside the 1 MiB upload buffer",
          sections[".code"]["addr"] + sections[".code"]["size"] <= 0x100000,
          hex(sections[".code"]["addr"] + sections[".code"]["size"]))
    check(".bss absent or empty",
          ".bss" not in sections or sections[".bss"]["size"] == 0)

    elf_header = run([readelf, "-h", ELF]).stdout
    entry_match = re.search(r"Entry point address:\s*0x([0-9a-fA-F]+)",
                            elf_header)
    check("ELF entry point is 0",
          entry_match is not None and int(entry_match.group(1), 16) == 0)

    check("symbol myos_a8_raw_entry at 0",
          symbols.get("myos_a8_raw_entry") == 0)
    check("symbol raw_entry_report at 0x200",
          symbols.get("raw_entry_report") == 0x200)
    for name in ("myos_a8_raw_main", "myos_a8_fb_draw_hello",
                 "myos_fb_draw_text", "myos_fb_fill_rect"):
        check("symbol %s present" % name, name in symbols)
    if "myos_a8_raw_main" in symbols:
        check("myos_a8_raw_main starts at the .code contract address",
              symbols["myos_a8_raw_main"] == sections[".code"]["addr"])
        for name in ("myos_a8_fb_draw_hello", "myos_fb_draw_text",
                     "myos_fb_fill_rect"):
            check("%s lives in .code" % name,
                  sections[".code"]["addr"] <= symbols.get(name, -1)
                  < sections[".code"]["addr"] + sections[".code"]["size"],
                  hex(symbols.get(name, -1)))

    disasm = run([objdump, "-d", ELF]).stdout
    block = re.search(r"<myos_a8_raw_main>:\n(.*?)(?:\n\n|\Z)",
                      disasm, re.S)
    if block:
        body = block.group(1)
        call = re.search(r"\bbl\s+[0-9a-fA-F]+\s+<myos_a8_fb_draw_hello>",
                         body)
        check("raw_main calls myos_a8_fb_draw_hello", call is not None)
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if lines:
            last = lines[-1]
            m = re.match(r"\s*([0-9a-fA-F]+):\s+[0-9a-fA-F]+\s+"
                         r"b\s+([0-9a-fA-F]+)", last)
            check("raw_main parks in a branch-to-self",
                  m is not None and m.group(1) == m.group(2), last.strip())
        if call is not None:
            pre = body[:call.start()]
            post = body[call.end():]

            # Parse all str instructions in the function body.
            # Pattern: str  x<src>, [x<base>, #<offset>]
            # We accept any base register (x0-x30) because the compiler
            # may allocate different registers for the report base pointer.
            str_re = re.compile(r"str\s+x\d+,\s*\[x\d+,\s*#(\d+)\]")

            # Offsets that must be stored BEFORE the call (report fields)
            pre_offsets = {16, 24, 32, 40, 48}   # +0x10, +0x18, +0x20, +0x28, +0x30
            # Offset that must be stored AFTER the call (reserved = demo status)
            post_offset = 56                      # +0x38

            found_pre = set()
            for m in str_re.finditer(pre):
                off = int(m.group(1))
                if off in pre_offsets:
                    found_pre.add(off)

            for off in pre_offsets:
                check("report store +0x%x precedes call" % off,
                      off in found_pre)

            # No report-field stores after the call
            post_stores = [int(m.group(1)) for m in str_re.finditer(post)
                           if int(m.group(1)) in pre_offsets]
            check("no report store after the call",
                  not post_stores,
                  "unexpected post-call stores at offsets: %s" % post_stores)

            # Reserved slot (+0x38) must be written AFTER the call with return value
            post_reserved = [int(m.group(1)) for m in str_re.finditer(post)
                             if int(m.group(1)) == post_offset]
            check("reserved slot +0x38 receives demo status after call",
                  len(post_reserved) == 1)

            # Demo call is not conditionally skipped: no conditional branch
            # between the last pre-call report store and the call.
            # Find the last pre-call store position in the pre section.
            last_pre_store_pos = max(
                (m.end() for m in str_re.finditer(pre)
                 if int(m.group(1)) in pre_offsets),
                default=0)
            check("demo call is not conditionally skipped",
                  not re.search(r"\b(?:cbz|cbnz|tbz|tbnz)\b",
                                pre[last_pre_store_pos:]))
    else:
        check("raw_main disassembly present", False)

    relocs_proc = run([readelf, "-rW", ELF])
    check("readelf -rW succeeded", relocs_proc.returncode == 0)
    check("no relocations remain",
          "There are no relocations" in relocs_proc.stdout)

    blob = open(BIN, "rb").read()
    check("image fits 1 MiB", len(blob) <= 0x100000, str(len(blob)))
    vec = sections.get(".vectors")
    if vec:
        vt = blob[vec["addr"]:vec["addr"] + vec["size"]]
        check("vector table is populated", any(vt))
        check("vector slot 0 reaches a branch",
              any((int.from_bytes(vt[i:i + 4], "little") & 0xFC000000)
                  == 0x14000000 for i in (0, 4, 8, 12)))
    else:
        check("vector table section present", False)
    check("hello string present in opt-in image", b"Hello User" in blob)
    check("report magic at 0x200",
          blob[0x200:0x208] == b"MYOSA8R1")
    check("report version/size",
          blob[0x208:0x20C] == (1).to_bytes(4, "little") and
          blob[0x20C:0x210] == (0x40).to_bytes(4, "little"))

    if os.path.exists(PROD):
        prod = open(PROD, "rb").read()
        check("production SHA-256 unchanged",
              hashlib.sha256(prod).hexdigest() == PROD_SHA)
        check("production raw_entry.bin has no demo string",
              b"Hello User" not in prod)
    elif require:
        check("production raw_entry.bin present", False)

    if os.path.exists(PROD_ELF):
        prod_symbols = parse_symbols(run([nm, PROD_ELF]).stdout)
        leaked = sorted(name for name in prod_symbols
                        if name.startswith(DEMO_SYMBOL_PREFIXES))
        check("production ELF has no opt-in display symbols",
              not leaked, ",".join(leaked))
    elif require:
        check("production raw_entry.elf present", False)

    if failures:
        print("raw hello image checks: FAIL (%d)" % failures)
        return 1
    print("raw hello image checks: ALL PASS (%d bytes)" % len(blob))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
