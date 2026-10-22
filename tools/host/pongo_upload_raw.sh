#!/bin/sh
#
# MyOS M7.59 — race-safe RAW upload through pongoterm.
#
# The PongoOS bulk-upload completion callback updates loader_xfer_recv_count
# asynchronously.  Sending `bootr` immediately after `/send` can therefore
# race the callback: pongo_boot_raw() sees a zero count, prints
# "please upload a raw image before issuing this command", and the device
# stays at 05ac:4141.  This wrapper:
#
#   1. polls for the PongoOS device instead of sleeping a fixed amount;
#   2. sends /send <image>;
#   3. waits 5 seconds (override with MYOS_UPLOAD_WAIT) for the DMA callback;
#   4. sends bootr.
#
# It does not implement USB protocol itself: pongoterm (built from the pinned
# pongoos-src tree) owns the control/bulk transfers.
#
# Usage:
#   tools/host/pongo_upload_raw.sh [image]
#
# Default image: build/raw_entry_hello.bin
# Environment:
#   PONGOTERM          path to pongoterm (default: pongoos-src/scripts/pongoterm)
#   MYOS_UPLOAD_WAIT   seconds between /send and bootr (default: 5)
#   MYOS_DEVICE_TIMEOUT seconds to wait for 05ac:4141 (default: 60)

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PONGOTERM=${PONGOTERM:-"$REPO_ROOT/pongoos-src/scripts/pongoterm"}
IMAGE=${1:-"$REPO_ROOT/build/raw_entry_hello.bin"}
WAIT_SECS=${MYOS_UPLOAD_WAIT:-5}
DEVICE_TIMEOUT=${MYOS_DEVICE_TIMEOUT:-60}
UPLOADSZ_MAX=$((128 * 1024 * 1024))

usage()
{
    cat >&2 <<EOF
Usage: $0 [image]

Upload a raw image to a PongoOS device at 05ac:4141 and issue bootr after a
${WAIT_SECS}s settle delay.

Environment:
  PONGOTERM=$PONGOTERM
  MYOS_UPLOAD_WAIT=$WAIT_SECS
  MYOS_DEVICE_TIMEOUT=$DEVICE_TIMEOUT
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
esac

if [ ! -x "$PONGOTERM" ]; then
    echo "error: pongoterm not found/executable: $PONGOTERM" >&2
    echo "build it with: make -C $REPO_ROOT/pongoos-src/scripts pongoterm" >&2
    exit 1
fi

if [ ! -f "$IMAGE" ]; then
    echo "error: image not found: $IMAGE" >&2
    exit 1
fi

if ! command -v lsusb >/dev/null 2>&1; then
    echo "error: lsusb not found (install usbutils)" >&2
    exit 1
fi

IMAGE_SIZE=$(wc -c < "$IMAGE" | tr -d ' ')
if [ "$IMAGE_SIZE" -gt "$UPLOADSZ_MAX" ]; then
    echo "error: image is $IMAGE_SIZE bytes; PongoOS upload limit is $UPLOADSZ_MAX" >&2
    exit 1
fi

IMAGE=$(realpath "$IMAGE")

printf 'waiting for PongoOS 05ac:4141 (up to %ss)...\n' "$DEVICE_TIMEOUT" >&2
elapsed=0
while ! lsusb -d 05ac:4141 >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$DEVICE_TIMEOUT" ]; then
        echo "error: PongoOS device 05ac:4141 not found" >&2
        echo "check the cable, pwn/checkra1n state, and udev permissions" >&2
        exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

printf 'uploading %s (%s bytes); waiting %ss before bootr...\n' \
    "$IMAGE" "$IMAGE_SIZE" "$WAIT_SECS" >&2

{
    printf '/send "%s"\n' "$IMAGE"
    sleep "$WAIT_SECS"
    printf 'bootr\n'
} | "$PONGOTERM"

printf 'done: /send and bootr were issued. If the display is still blank,\n' >&2
printf 'capture the device state before the next attempt; do not re-upload blindly.\n' >&2
