#!/usr/bin/env bash
# tests/offline_m1.sh — offline (no-device) verification of the M1 artifacts.
# Verifies: build outputs exist, ELF/arch/entry/symbols, size/alignment,
# marker presence, cross-file constant consistency, deterministic rebuild.
# Exit 0 = all checks pass. Any failure prints FAIL_<name>.
set -u
set -o pipefail
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

# --- 2. flat binary: 4-byte aligned, within the single-command-block budget
# The budget is derived from the authoritative header constants (never a
# hard-coded number): payload max = M1_DFU_MAX_XFER - M1_EXEC_HEADER_LEN.
hdr_file=platform/apple/a8/m1_a8.h
max_xfer=$(grep -oE 'define M1_DFU_MAX_XFER[[:space:]]+0x[0-9a-fA-F]+' "$hdr_file" | grep -oE '0x[0-9a-fA-F]+')
exec_hdr=$(grep -oE 'define M1_EXEC_HEADER_LEN[[:space:]]+0x[0-9a-fA-F]+' "$hdr_file" | grep -oE '0x[0-9a-fA-F]+')
budget=$(( max_xfer - exec_hdr ))
sz=$(stat -c %s "$BUILD/myos_m1.bin")
if [ $((sz % 4)) -eq 0 ]; then pass align4; else failchk align4; fi
if [ "$sz" -ge 16 ] && [ "$sz" -le "$budget" ]; then pass fits_dfu; else failchk fits_dfu; fi
echo "payload_size=$sz bytes (budget: $budget = M1_DFU_MAX_XFER($max_xfer) - M1_EXEC_HEADER_LEN($exec_hdr))"
# The header must define the single source of truth for that limit.
grep -q 'define M1_MAX_PAYLOAD[[:space:]]*(M1_DFU_MAX_XFER - M1_EXEC_HEADER_LEN)' "$hdr_file" \
  && pass budget_define || failchk budget_define

# --- 3. ELF class / machine / entry point
hdr=$(${X}readelf -h "$BUILD/myos_m1.elf")
echo "$hdr" | grep -q "Class:.*ELF64"                    && pass class64  || failchk class64
echo "$hdr" | grep -q "AArch64"                          && pass machine  || failchk machine
echo "$hdr" | grep -q "Entry point address: *0x180380050" && pass entry_addr || failchk entry_addr

# --- 3b. entry address is derived consistently from the header and m1.ld
img_base=$(grep -oE 'define M1_IMG_BUF_BASE[[:space:]]+0x[0-9a-fA-F]+' "$hdr_file" | grep -oE '0x[0-9a-fA-F]+')
entry_off=$(grep -oE 'define M1_ENTRY_OFF[[:space:]]+0x[0-9a-fA-F]+' "$hdr_file" | grep -oE '0x[0-9a-fA-F]+')
entry_expect=$(printf '0x%x' $(( img_base + entry_off )))
if [ "$entry_expect" = "0x180380050" ]; then pass entry_header; else failchk entry_header; fi
grep -q "\. = 0x180380050;" platform/apple/a8/m1.ld && pass entry_ld || failchk entry_ld
if echo "$hdr" | grep -q "Entry point address: *$entry_expect"; then pass entry_consistent; else failchk entry_consistent; fi

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

# --- 5c. register discipline: x0/x1 (block base + challenge) AND x30 (the
# hook's return address) must be saved to the stack before the first call,
# and reloaded before `ret`. The boot_diag_* calls are normal C calls: AAPCS
# lets them clobber every caller-saved register (x0-x18) and each `bl`
# overwrites x30, so holding any of them across a `bl` would be a latent
# bug (M7.35 finding V2: without the x30 stash the final `ret` re-enters
# the epilogue in a loop and the hook never resumes — DONE_MAGIC is never
# stored and the M1 PASS condition becomes unreachable). These are pattern
# checks on the linked artifact; the control-flow/stack model over every
# path (sp balance, 16-byte alignment at each call, ret target = entry
# link, return value = ~challenge) lives in tests/test_m1_x30_abi.py.
entry_asm=$(${X}objdump -d --no-show-raw-insn "$BUILD/myos_m1.elf" \
  | awk '/<myos_m1_entry>:/{f=1} f{print} f && /^$/{exit}')
prologue=$(printf '%s\n' "$entry_asm" | awk '/[[:space:]]bl[[:space:]]/{exit} {print}')
if printf '%s\n' "$prologue" | grep -Eq 'stp[[:space:]]+x9, x10, \[sp, #-32\]!'; then
  pass entry_saves_x0x1
else
  failchk entry_saves_x0x1
fi
if printf '%s\n' "$prologue" | grep -Eq 'str[[:space:]]+x30, \[sp, #16\]'; then
  pass entry_saves_x30
else
  failchk entry_saves_x30
fi
last_bl=$(printf '%s\n' "$entry_asm" | grep -n '[[:space:]]bl[[:space:]]' | tail -1 | cut -d: -f1)
epilogue=$(printf '%s\n' "$entry_asm" | sed -n "$((last_bl + 1)),\$p")
if printf '%s\n' "$epilogue" | grep -Eq 'ldr[[:space:]]+x10, \[sp, #8\]'; then
  pass entry_reloads_challenge
else
  failchk entry_reloads_challenge
fi
if printf '%s\n' "$epilogue" | grep -Eq 'ldr[[:space:]]+x30, \[sp, #16\]'; then
  pass entry_reloads_x30
else
  failchk entry_reloads_x30
fi
if printf '%s\n' "$epilogue" | grep -Eq 'add[[:space:]]+sp, sp, #0x20'; then
  pass entry_restores_sp
else
  failchk entry_restores_sp
fi
# the link register is stashed exactly once and restored exactly once
x30_stores=$(printf '%s\n' "$entry_asm" | grep -cE 'str[[:space:]]+x30, \[sp,')
x30_loads=$(printf '%s\n' "$entry_asm" | grep -cE 'ldr[[:space:]]+x30, \[sp,')
if [ "$x30_stores" -eq 1 ] && [ "$x30_loads" -eq 1 ]; then
  pass entry_x30_once
else
  failchk entry_x30_once
fi
# the x30 restore must precede the ret (the ret is the last instruction)
ret_ln=$(printf '%s\n' "$entry_asm" | grep -nE '[[:space:]]ret$' | tail -1 | cut -d: -f1)
rst_ln=$(printf '%s\n' "$entry_asm" | grep -nE 'ldr[[:space:]]+x30, \[sp,' | tail -1 | cut -d: -f1)
if [ -n "$ret_ln" ] && [ -n "$rst_ln" ] && [ "$rst_ln" -lt "$ret_ln" ]; then
  pass entry_x30_before_ret
else
  failchk entry_x30_before_ret
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
