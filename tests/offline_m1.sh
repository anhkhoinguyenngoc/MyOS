#!/usr/bin/env bash
# tests/offline_m1.sh — offline (no-device) verification of the M1 artifacts.
# Verifies: build outputs exist, ELF/arch/entry/symbols, size/alignment,
# marker presence, cross-file constant consistency, deterministic rebuild.
# Exit 0 = all checks pass. Any failure prints FAIL_<name>.
set -u
cd "$(dirname "$0")/.."

X=aarch64-linux-gnu-
BUILD=build
fail=0
pass() { echo "PASS_$1"; }
failchk() { echo "FAIL_$1"; fail=1; }

echo "== offline M1 checks =="

# --- 1. artifacts exist and are non-empty / executable
[ -s "$BUILD/myos_m1.bin" ] && pass size_payload || failchk size_payload
[ -s "$BUILD/myos_m1.elf" ] && pass size_elf     || failchk size_elf
[ -x "$BUILD/m1ctl" ]       && pass size_m1ctl   || failchk size_m1ctl

# --- 2. flat binary: 4-byte aligned, within exec-block budget (0x800-0x50)
sz=$(stat -c %s "$BUILD/myos_m1.bin")
if [ $((sz % 4)) -eq 0 ]; then pass align4; else failchk align4; fi
if [ "$sz" -ge 16 ] && [ "$sz" -le 2016 ]; then pass fits_dfu; else failchk fits_dfu; fi
echo "payload_size=$sz bytes (budget: 2016)"

# --- 3. ELF class / machine / entry point
hdr=$(${X}readelf -h "$BUILD/myos_m1.elf")
echo "$hdr" | grep -q "Class:.*ELF64"                    && pass class64  || failchk class64
echo "$hdr" | grep -q "AArch64"                          && pass machine  || failchk machine
echo "$hdr" | grep -q "Entry point address: *0x180380050" && pass entry_addr || failchk entry_addr

# --- 4. symbols: entry defined (T), zero undefined symbols
syms=$(${X}nm "$BUILD/myos_m1.elf")
echo "$syms" | grep -q "T myos_m1_entry"                 && pass entry_symbol || failchk entry_symbol
if ${X}nm -u "$BUILD/myos_m1.elf" | grep -q '[A-Za-z]'; then failchk no_undef; else pass no_undef; fi

# --- 5. first instruction at entry decodes to our prologue (mov x9, x0)
dis=$(${X}objdump -d --start-address=0x180380050 --stop-address=0x180380058 "$BUILD/myos_m1.elf")
if echo "$dis" | grep -Eq 'mov[[:space:]]+x9,[[:space:]]*x0'; then pass entry_insn; else failchk entry_insn; fi

# --- 5b. ELF must be a pure freestanding image (no dynamic-linker sections)
if ${X}readelf -S "$BUILD/myos_m1.elf" | grep -qE '\.(interp|dynamic|got)'; then
  failchk no_dynamic_sections
else
  pass no_dynamic_sections
fi

# --- 6. marker bytes present in flat binary
if grep -qa "MYOSM1OK" "$BUILD/myos_m1.bin"; then pass marker; else failchk marker; fi

# --- 7. flat binary contains expected sections (.text, .data, .bss)
# Diagnostic state adds .data and .bss - verify they are present
${X}objcopy -O binary --only-section=.text "$BUILD/myos_m1.elf" /tmp/m1_text_only.$$ 2>/dev/null
${X}objcopy -O binary --only-section=.data "$BUILD/myos_m1.elf" /tmp/m1_data_only.$$ 2>/dev/null
if [ -s /tmp/m1_text_only.$$ ] && [ -s /tmp/m1_data_only.$$ ]; then
    # Full binary should be larger than text-only (includes data)
    full_sz=$(stat -c %s "$BUILD/myos_m1.bin")
    text_sz=$(stat -c %s /tmp/m1_text_only.$$)
    if [ "$full_sz" -gt "$text_sz" ]; then pass text_only; else failchk text_only; fi
else
    failchk text_only
fi
rm -f /tmp/m1_text_only.$$ /tmp/m1_data_only.$$

# --- 8. deterministic rebuild
cp "$BUILD/myos_m1.bin" /tmp/m1_a.$$
rm -f "$BUILD/m1_entry.o" "$BUILD/myos_m1.elf" "$BUILD/myos_m1.bin"
if make -s payload >/dev/null 2>&1 && cmp -s /tmp/m1_a.$$ "$BUILD/myos_m1.bin"; then
  pass deterministic
else
  failchk deterministic
fi
rm -f /tmp/m1_a.$$

# --- 9. constants consistency: m1_a8.h vs vendored gaster.c (provenance)
grep -q "insecure_memory_base = 0x180380000" tools/external/gaster/gaster.c && pass const_base   || failchk const_base
grep -q "0x180380000ULL" platform/apple/a8/m1_a8.h                          && pass const_header || failchk const_header
grep -q "define EXEC_MAGIC (0x6578656365786563ULL)" tools/external/gaster/gaster.c && pass const_exec || failchk const_exec
grep -q "0x6578656365786563ULL" platform/apple/a8/m1_a8.h                   && pass const_exec_h || failchk const_exec_h
grep -q 'SRTG:\[iBoot-1992.0.0.1.19\]' tools/external/gaster/gaster.c       && pass const_srtg   || failchk const_srtg

# --- 10. m1ctl prints usage and exits 64 on unknown/missing arg
"$BUILD/m1ctl" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 64 ] && pass m1ctl_usage || failchk m1ctl_usage

# --- 11. payload has no QEMU-virt assumptions
if grep -qa "PL011\|virt" "$BUILD/myos_m1.bin"; then failchk no_qemu_refs; else pass no_qemu_refs; fi

echo "== result: $([ $fail -eq 0 ] && echo ALL_PASS || echo SOME_FAILED) =="
exit $fail
