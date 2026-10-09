/*
 * tools/host/m1ctl.c — MyOS M1 pwned-DFU client.
 *
 * target: host (Arch Linux x86_64 PC) — runs ONLY on the PC side.
 *
 * Purpose (M1_PLAN.md §2): drive the exec/memc command interface that the
 * gaster checkm8 payload installs on the iPhone 6, and capture the evidence
 * block returned by MyOS code executing on the A8.
 *
 * Protocol: gaster's own command interface (gaster.c gaster_command
 * L1478 + dfu_send_data L977) — the exact DNLOAD/GETSTATUS sequence
 * proven to drive THIS device-side payload (payload_handle_checkm8_
 * request.S); gaster's decrypt feature runs exec commands through it.
 * The command-block format (EXEC/MEMC magics, pc, x0..x7) and the
 * response format ([DONE_MAGIC][retval][data]) are identical to
 * upstream ipwndfu usbexec.py by design — gaster's payload is
 * derived from ipwndfu's checkm8 payload. Upstream ipwndfu lacks
 * t7000 support; gaster provides the device side for our CPID 0x7000.
 *
 * Subcommands:
 *   m1ctl serial      print iSerial (expect PWND:[checkm8] after pwn)
 *   m1ctl memc        rung 2: handler round-trip (no MyOS code yet)
 *   m1ctl exec        rung 3: execute myos_m1_entry, verify M1 PASS criteria
 *
 * This tool TRANSFERS and OBSERVES. It never claims execution without the
 * full evidence block (M1_PLAN.md §6).
 */
#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#include "../../platform/apple/a8/m1_a8.h"

#define DFU_DNLOAD      1
#define DFU_GETSTATUS   3
#define DFU_STATE_MANIFEST_SYNC 6
#define CMD_TIMEOUT_MS  5000

static libusb_device_handle *open_dfu(void) {
    libusb_device_handle *h = libusb_open_device_with_vid_pid(NULL, M1_APPLE_VID, M1_DFU_PID);
    if (!h) {
        fprintf(stderr, "m1ctl: cannot open DFU device 05ac:1227 (permission? device present? running as root?)\n");
        return NULL;
    }
    return h;
}

/* gaster dfu_send_data() (gaster.c L977-987) — the exact DNLOAD
 * sequence gaster_command() (gaster.c L1478-1493) uses to deliver a
 * command block to THIS device-side payload (payload_handle_checkm8_
 * request.S). Proven path: gaster's own decrypt feature runs exec
 * commands through it. Sequence:
 *   DFU_DNLOAD chunks (<=0x800) of the command block
 *   DFU_DNLOAD 16-byte DFU file suffix (zeros)
 *   DFU_DNLOAD wLength=0
 *   DFU_GETSTATUS x3 -> MANIFEST_SYNC(6) -> MANIFEST(7) -> MANIFEST_WAIT_RESET(8)
 * then the caller fires the hooked control-IN 0xA1/2/0xFFFF. */
static int send_data(libusb_device_handle *h, const uint8_t *data, size_t len) {
    size_t i = 0;
    while (i < len) {
        size_t n = len - i;
        if (n > M1_DFU_MAX_XFER) n = M1_DFU_MAX_XFER;
        int r = libusb_control_transfer(h, 0x21, DFU_DNLOAD, 0, 0,
                                        (unsigned char *)&data[i], (uint16_t)n, CMD_TIMEOUT_MS);
        if (r != (int)n) {
            fprintf(stderr, "m1ctl: DFU_DNLOAD chunk failed: %d (%s)\n", r, libusb_strerror(r));
            return -1;
        }
        i += n;
    }
    uint8_t suffix[16] = {0};
    int r = libusb_control_transfer(h, 0x21, DFU_DNLOAD, 0, 0, suffix, 16, CMD_TIMEOUT_MS);
    if (r != 16) {
        fprintf(stderr, "m1ctl: DFU suffix DNLOAD failed: %d (%s)\n", r, libusb_strerror(r));
        return -1;
    }
    r = libusb_control_transfer(h, 0x21, DFU_DNLOAD, 0, 0, NULL, 0, CMD_TIMEOUT_MS);
    if (r < 0) {
        fprintf(stderr, "m1ctl: DNLOAD0 failed: %d (%s)\n", r, libusb_strerror(r));
        return -1;
    }
    /* dfu_set_state_wait_reset (gaster.c L829-833): status must be
     * DFU_STATUS_OK(0) and state must walk 6 -> 7 -> 8. */
    for (int k = 0; k < 3; k++) {
        uint8_t st[6];
        r = libusb_control_transfer(h, 0xA1, DFU_GETSTATUS, 0, 0, st, 6, CMD_TIMEOUT_MS);
        if (r != 6) {
            fprintf(stderr, "m1ctl: GETSTATUS failed: %d (%s)\n", r, libusb_strerror(r));
            return -1;
        }
        if (st[0] != 0 || st[4] != (uint8_t)(DFU_STATE_MANIFEST_SYNC + k)) {
            fprintf(stderr, "m1ctl: GETSTATUS state %u (want %u) status %u\n",
                    st[4], (unsigned)(DFU_STATE_MANIFEST_SYNC + k), st[0]);
            return -1;
        }
    }
    return 0;
}

/* gaster gaster_command() (gaster.c L1478-1493): deliver the command
 * block with dfu_send_data, then read the response through the hooked
 * control-IN 0xA1 / bRequest=2 (DFU_UPLOAD) / wValue=0xFFFF. The
 * device-side hook (payload_handle_checkm8_request.S) intercepts that
 * exact request, executes the command block from the DFU image buffer
 * (0x180380000), and ships [DONE_MAGIC][retval][evidence...] back. */
static int command(libusb_device_handle *h, const uint8_t *req, size_t req_len,
                   uint8_t *resp, size_t resp_len) {
    int r;

    if (send_data(h, req, req_len) != 0) return -1;

    r = libusb_control_transfer(h, 0xA1, 2, 0xFFFF, 0, resp, (uint16_t)resp_len, CMD_TIMEOUT_MS);
    if (r != (int)resp_len) {
        fprintf(stderr, "m1ctl: exec trigger/read failed: %d (%s)\n", r, libusb_strerror(r));
        return -1;
    }
    return 0;
}

static void hexdump(const char *tag, const uint8_t *b, size_t n) {
    printf("%s (%zu bytes):", tag, n);
    for (size_t i = 0; i < n; i++) printf(" %02x", b[i]);
    printf("\n");
}

static int cmd_serial(void) {
    libusb_device_handle *h = open_dfu();
    if (!h) return 1;
    char buf[256] = {0};
    int r = -1;
    libusb_device *dev = libusb_get_device(h);
    struct libusb_device_descriptor dd;
    if (libusb_get_device_descriptor(dev, &dd) == 0) {
        r = libusb_get_string_descriptor_ascii(h, dd.iSerialNumber, (unsigned char *)buf, sizeof(buf) - 1);
    }
    libusb_close(h);
    if (r < 0) { fprintf(stderr, "m1ctl: serial read failed: %s\n", libusb_strerror(r)); return 1; }
    printf("iSerial: %s\n", buf);
    if (strstr(buf, "PWND:[checkm8]")) { printf("STATE: PWNED\n"); return 0; }
    printf("STATE: NOT_PWNED\n");
    return 2;
}

static int cmd_memc(void) {
    libusb_device_handle *h = open_dfu();
    if (!h) return 1;

    /* no-op memcpy: dst = src = image buffer + 0x10, len 8 — proves the
     * device-side handler executes the memc path and answers with DONE. */
    uint8_t req[40] = {0};
    uint64_t *q = (uint64_t *)req;
    q[0] = M1_MEMC_MAGIC;
    q[2] = M1_IMG_BUF_BASE + 0x10;   /* x0 dst */
    q[3] = M1_IMG_BUF_BASE + 0x10;   /* x1 src */
    q[4] = 8;                        /* x2 len */

    uint8_t resp[32] = {0};
    int rc = command(h, req, sizeof(req), resp, sizeof(resp));
    libusb_close(h);
    if (rc != 0) return 1;

    hexdump("memc response", resp, sizeof(resp));
    uint64_t done = *(uint64_t *)(resp + 0);
    uint64_t dst  = *(uint64_t *)(resp + 8);
    if (done == M1_DONE_MAGIC && dst == M1_IMG_BUF_BASE + 0x10) {
        printf("MEMC: HANDLER_LIVE (DONE_MAGIC + dst round-trip OK)\n");
        return 0;
    }
    printf("MEMC: UNEXPECTED (check evidence bytes above)\n");
    return 3;
}

static int cmd_exec(void) {
    /* load the MyOS payload from disk and append it to the exec header */
    const char *path = "build/myos_m1.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "m1ctl: cannot open %s (run from project root after `make payload`)\n", path); return 1; }
    uint8_t blob[M1_DFU_MAX_XFER] = {0};
    size_t blob_len = fread(blob + M1_EXEC_HEADER_LEN, 1, sizeof(blob) - M1_EXEC_HEADER_LEN, f);
    fclose(f);
    if (blob_len == 0 || blob_len + M1_EXEC_HEADER_LEN > M1_DFU_MAX_XFER) {
        fprintf(stderr, "m1ctl: bad payload size %zu\n", blob_len);
        return 1;
    }

    /* build exec command block: [magic][pc][x0..x7] + appended payload bytes */
    uint64_t *q = (uint64_t *)blob;
    q[0] = M1_EXEC_MAGIC;
    q[1] = M1_ENTRY_ADDR;        /* pc = myos_m1_entry */
    q[2] = M1_IMG_BUF_BASE;      /* x0 = block base    */
    q[3] = M1_CHALLENGE;         /* x1 = challenge     */
    q[4] = q[5] = q[6] = q[7] = q[8] = 0; /* x2..x7 = 0 */

    printf("exec: entry=0x%llx block=%zu bytes (80-byte header + %zu payload)\n",
           (unsigned long long)M1_ENTRY_ADDR, M1_EXEC_HEADER_LEN + blob_len, blob_len);

    libusb_device_handle *h = open_dfu();
    if (!h) return 1;
    uint8_t resp[M1_RESP_LEN] = {0};
    int rc = command(h, blob, M1_EXEC_HEADER_LEN + blob_len, resp, sizeof(resp));
    libusb_close(h);
    if (rc != 0) return 1;

    hexdump("exec response", resp, sizeof(resp));

    uint64_t done    = *(uint64_t *)(resp + 0x00);
    uint64_t retval  = *(uint64_t *)(resp + 0x08);
    uint64_t cur_el  = *(uint64_t *)(resp + M1_SLOT_CURRENTEL);
    uint64_t midr    = *(uint64_t *)(resp + M1_SLOT_MIDR);
    uint64_t marker  = *(uint64_t *)(resp + M1_SLOT_MARKER);
    uint64_t echo    = *(uint64_t *)(resp + M1_SLOT_ECHO);
    uint64_t cntfrq  = *(uint64_t *)(resp + M1_SLOT_CNTFRQ);
    uint64_t cntpct  = *(uint64_t *)(resp + M1_SLOT_CNTPCT);

    printf("done_magic     = 0x%016llx %s\n", (unsigned long long)done,
           done == M1_DONE_MAGIC ? "OK" : "WRONG");
    printf("retval         = 0x%016llx (expect ~challenge 0x%016llx) %s\n",
           (unsigned long long)retval, (unsigned long long)~M1_CHALLENGE,
           retval == ~M1_CHALLENGE ? "OK" : "WRONG");
    printf("CurrentEL slot = 0x%016llx (%s)\n", (unsigned long long)cur_el,
           (cur_el == 0x4 || cur_el == 0x8 || cur_el == 0xC) ? "EL1/EL2/EL3 plausible" : "IMPLAUSIBLE");
    printf("MIDR_EL1 slot  = 0x%016llx%s\n", (unsigned long long)midr,
           midr ? " (read at EL1)" : " (skipped: not EL1 or 0)");
    printf("marker slot    = 0x%016llx %s\n", (unsigned long long)marker,
           marker == *(uint64_t *)M1_MARKER_BYTES ? "MYOSM1OK OK" : "WRONG");
    printf("echo slot      = 0x%016llx %s\n", (unsigned long long)echo,
           echo == M1_CHALLENGE ? "OK" : "WRONG");
    printf("CNTFRQ_EL0     = 0x%016llx\n", (unsigned long long)cntfrq);
    printf("CNTPCT_EL0     = 0x%016llx\n", (unsigned long long)cntpct);

    int pass = (done == M1_DONE_MAGIC) &&
               (retval == ~M1_CHALLENGE) &&
               (cur_el == 0x4 || cur_el == 0x8 || cur_el == 0xC) &&
               (marker == *(uint64_t *)M1_MARKER_BYTES) &&
               (echo == M1_CHALLENGE);
    printf("M1_VERDICT: %s\n", pass ? "PASS" : "FAIL");
    printf("PASS means: evidence demonstrates instruction execution reached "
           "myos_m1_entry (0x%llx) on the physical A8.\n",
           (unsigned long long)M1_ENTRY_ADDR);
    return pass ? 0 : 4;
}

/*
 * probe — replicate gaster's wait_usb_handle steps one by one with
 * per-step diagnostics (open / set_configuration / raw serial read at
 * gaster's 5ms timeout and at 5000ms). Used to pinpoint why gaster's
 * post-SPRAY wait loop never succeeds while other processes can talk to
 * the device. Read-only diagnostics; no exploit traffic.
 */
static int cmd_probe(void) {
    struct timespec t0, t1;
    long ms;
#define ELAPSED() do { \
        clock_gettime(CLOCK_MONOTONIC, &t1); \
        ms = (t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000; \
    } while (0)

    clock_gettime(CLOCK_MONOTONIC, &t0);
    libusb_device_handle *h = libusb_open_device_with_vid_pid(NULL, M1_APPLE_VID, M1_DFU_PID);
    ELAPSED();
    if (!h) { printf("open: FAIL (%ld ms)\n", ms); return 1; }
    printf("open: OK (%ld ms)\n", ms);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    int r = libusb_set_configuration(h, 1);
    ELAPSED();
    printf("set_configuration(1): %s (%ld ms)\n", r == 0 ? "OK" : libusb_error_name(r), ms);

    libusb_device *dev = libusb_get_device(h);
    struct libusb_device_descriptor dd;
    uint8_t buf[64];

    for (int to = 5; to <= 5000; to *= 1000) {
        if (libusb_get_device_descriptor(dev, &dd) != 0) { printf("devdesc: FAIL\n"); break; }
        clock_gettime(CLOCK_MONOTONIC, &t0);
        r = libusb_control_transfer(h, 0x80, 6, (3u << 8) | dd.iSerialNumber, 0x409,
                                    buf, sizeof(buf), to);
        ELAPSED();
        printf("serial_read @%dms: %s (%ld ms)", to, r > 0 ? "OK" : libusb_error_name(r), ms);
        if (r > 0) {
            printf(" [");
            for (int i = 2; i < r; i += 2) putchar(buf[i]);
            printf("]");
        }
        printf("\n");
    }
    libusb_close(h);
    return 0;
#undef ELAPSED
}

int main(int argc, char **argv) {
    if (libusb_init(NULL) != 0) { fprintf(stderr, "m1ctl: libusb_init failed\n"); return 1; }
    int rc = 1;
    if (argc == 2 && strcmp(argv[1], "serial") == 0)      rc = cmd_serial();
    else if (argc == 2 && strcmp(argv[1], "memc") == 0)   rc = cmd_memc();
    else if (argc == 2 && strcmp(argv[1], "exec") == 0)   rc = cmd_exec();
    else if (argc == 2 && strcmp(argv[1], "probe") == 0)  rc = cmd_probe();
    else {
        fprintf(stderr,
            "usage: m1ctl serial|probe|memc|exec\n"
            "  serial  read DFU iSerial (PWNED state check)\n"
            "  probe   step-by-step USB diagnostics (gaster wait-loop replica)\n"
            "  memc    pwned-DFU handler round-trip (rung 2)\n"
            "  exec    execute MyOS payload on device, verify evidence (rung 3)\n");
        rc = 64;
    }
    libusb_exit(NULL);
    return rc;
}
