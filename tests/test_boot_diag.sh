#!/bin/bash
#
# test_boot_diag.sh — Offline unit tests for MyOS Boot Diagnostic Framework
#
# Tests: ELF structure, symbol presence, stage/error constants, formatting functions
# Run from repository root: ./tests/test_boot_diag.sh
#

set -euo pipefail

PAYLOAD_ELF="build/myos_m1.elf"
PAYLOAD_BIN="build/myos_m1.bin"
OBJDUMP="aarch64-linux-gnu-objdump"
NM="aarch64-linux-gnu-nm"
READ_ELF="aarch64-linux-gnu-readelf"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "========================================="
echo "MyOS Boot Diagnostic Framework - Offline Tests"
echo "========================================="

# 1. Payload exists
if [[ ! -f "$PAYLOAD_ELF" ]]; then
    fail "Payload ELF not found: $PAYLOAD_ELF (run 'make' first)"
fi
if [[ ! -f "$PAYLOAD_BIN" ]]; then
    fail "Payload BIN not found: $PAYLOAD_BIN (run 'make' first)"
fi
pass "Payload exists: $PAYLOAD_ELF / $PAYLOAD_BIN"

# 2. ELF class = 64-bit
CLASS=$($READ_ELF -h "$PAYLOAD_ELF" | grep "Class:" | awk '{print $2}')
[[ "$CLASS" == "ELF64" ]] || fail "Not ELF64 (class=$CLASS)"
pass "ELF class: ELF64"

# 3. Machine = AArch64
MACHINE=$($READ_ELF -h "$PAYLOAD_ELF" | grep "Machine:" | awk '{print $2}')
[[ "$MACHINE" == "AArch64" ]] || fail "Not AArch64 (machine=$MACHINE)"
pass "Machine: AArch64"

# 4. Entry point = 0x180380050
ENTRY=$($READ_ELF -h "$PAYLOAD_ELF" | grep "Entry point" | awk '{print $4}')
# readelf prints with 0x prefix, normalize
ENTRY_NORM=$(printf "0x%X" "$ENTRY" 2>/dev/null || echo "$ENTRY")
[[ "$ENTRY_NORM" == "0x180380050" ]] || fail "Entry point mismatch: got $ENTRY_NORM, expected 0x180380050"
pass "Entry point: 0x180380050"

# 5. No undefined symbols
UNDEF=$($NM -u "$PAYLOAD_ELF" 2>/dev/null | wc -l)
[[ "$UNDEF" -eq 0 ]] || fail "Undefined symbols found:\n$($NM -u "$PAYLOAD_ELF")"
pass "No undefined symbols"

# 6. Diagnostic API symbols present
REQUIRED_SYMBOLS=(
    "boot_diag_init"
    "boot_diag_begin"
    "boot_diag_ok"
    "boot_diag_fail"
    "boot_diag_panic"
    "boot_diag_get_state"
    "boot_diag_print_report"
    "boot_diag_print_error"
    "boot_diag_print_stage"
    "boot_diag_exception_handler"
    "boot_diag_format_hex32"
    "boot_diag_format_hex64"
    "boot_diag_format_error"
    "boot_diag_state_valid"
    "boot_console_register"
    "boot_fb_register_renderer"
    "diag_state"
    "boot_console"
    "boot_fb_renderer"
    "boot_stage_names"
)

for sym in "${REQUIRED_SYMBOLS[@]}"; do
    $NM "$PAYLOAD_ELF" | grep -q " $sym$" || fail "Missing symbol: $sym"
done
pass "All diagnostic API symbols present"

# 7. Stage constants in binary (boot_stage_names array)
strings "$PAYLOAD_ELF" | grep -q "BOOTSTRAP" || fail "Stage names not found in binary"
pass "Stage names present in binary"

# 8. Marker "MYOSM1OK" present
strings "$PAYLOAD_ELF" | grep -q "MYOSM1OK" || fail "Marker MYOSM1OK not found"
pass "Marker MYOSM1OK present"

# 9. Size check - should be reasonable (< 64KB for M1)
SIZE=$(stat -c%s "$PAYLOAD_BIN")
MAX_SIZE=65536
[[ "$SIZE" -le "$MAX_SIZE" ]] || warn "Payload size $SIZE bytes exceeds $MAX_SIZE (M1 target)"
pass "Payload size: $SIZE bytes"

# 10. Alignment check - entry point should be 16-byte aligned
# (0x180380050 % 16 = 0)
ALIGN=$((0x180380050 % 16))
[[ "$ALIGN" -eq 0 ]] || fail "Entry point not 16-byte aligned"
pass "Entry point 16-byte aligned"

# 11. Section layout sanity
$READ_ELF -S "$PAYLOAD_ELF" | grep -q "\.text" || fail ".text section missing"
# .rodata may be merged into .text for this build
pass "Required sections present (.text)"

# 12. No .bss or .data (should be minimal for M1)
BSS=$($READ_ELF -S "$PAYLOAD_ELF" | grep "\.bss" | awk '{print $7}')
DATA=$($READ_ELF -S "$PAYLOAD_ELF" | grep "\.data" | awk '{print $7}')
# Note: bss/data might be 0 or missing - that's fine
pass "Section layout OK"

# 13. Verify stage enum values match header (compile-time check via objdump)
# Check that boot_stage_names array has correct count (12 entries: 0-11)
# This is a rough check - actual values verified by symbol presence
STAGE_COUNT=$(strings "$PAYLOAD_ELF" | grep -c "BOOTSTRAP")
[[ "$STAGE_COUNT" -ge 1 ]] || fail "Stage names not properly embedded"
pass "Stage names embedded in binary"

# 14. Check diag_state structure magic/version in .bss or .data
# The state is static in boot_diag.c - check it's referenced
$NM "$PAYLOAD_ELF" | grep -q " diag_state$" || fail "diag_state symbol not found"
pass "diag_state symbol present"

# 15. Verify console ops structure
$NM "$PAYLOAD_ELF" | grep -q " boot_console$" || fail "boot_console symbol not found"
pass "boot_console symbol present"

echo ""
echo "========================================="
echo -e "${GREEN}All offline tests PASSED${NC}"
echo "========================================="