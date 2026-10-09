/*
 * tools/host/usb_settle_shim.c — host-side LD_PRELOAD shim (PC only).
 *
 * Problem (diagnosed with strace on this host, see M1_RUNBOOK §7.3):
 * gaster's wait_usb_handle() calls libusb_open_device_with_vid_pid() in a
 * 5ms loop. Right after gaster's reset_usb_handle(), the iPhone re-enumerates
 * and host udev needs tens-to-hundreds of ms to create the new
 * /dev/bus/usb node. gaster then calls libusb_exit()+libusb_init() and the
 * fresh context's device scan can run inside that window: the device is
 * skipped (node not ready yet) and — with no further hotplug event delivered
 * to the already-started monitor — the process keeps a stale, device-less
 * cache forever. Observed effect: the wait loop does only
 * recvfrom(EAGAIN)+nanosleep with ZERO openat syscalls, while other
 * processes open the very same device fine.
 *
 * Fix (timing only — no USB traffic is altered, checkm8's sensitive
 * transfers never call these functions):
 *   - sleep before every libusb_init() (device state settles while the new
 *     context is not yet scanning), and
 *   - sleep on the first libusb_get_device_list() after each init (covers
 *     lazy-scan libusb builds).
 *
 * Build: gcc -shared -fPIC -o build/usb_settle_shim.so tools/host/usb_settle_shim.c -ldl
 * Use:   LD_PRELOAD=/myos/build/usb_settle_shim.so gaster pwn
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <unistd.h>

typedef int (*init_t)(void **);
typedef ssize_t (*gdl_t)(void *, void ***);

static int need_settle = 1;

int libusb_init(void **ctx) {
    static init_t real = NULL;
    if (!real) real = (init_t)dlsym(RTLD_NEXT, "libusb_init");
    usleep(500000);          /* let udev settle the re-enumerated node */
    need_settle = 1;
    return real(ctx);
}

ssize_t libusb_get_device_list(void *ctx, void ***list) {
    static gdl_t real = NULL;
    if (!real) real = (gdl_t)dlsym(RTLD_NEXT, "libusb_get_device_list");
    if (need_settle) {
        need_settle = 0;
        usleep(300000);      /* first scan of this context: settle window */
    }
    return real(ctx, list);
}
