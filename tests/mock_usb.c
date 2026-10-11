/*
 * tests/mock_usb.c — mock libusb backend for the offline usb_watch harness.
 *
 * Implements the subset of libusb used by tools/host/usb_watch.c and lets a
 * test script device enumeration and hotplug callbacks deterministically.
 * Also simulates libusb's device reference counting so that double-unref
 * mistakes are caught rather than silently tolerated.
 */

#define _GNU_SOURCE
#include <libusb-1.0/libusb.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>

#include "mock_usb.h"

#define MOCK_MAX_DEV     8
#define MOCK_MAX_SCRIPT  16
#define MOCK_MAX_STEPS   16

struct libusb_device {
    int refcnt;                 /* owner reference starts at 1 */
    struct libusb_device_descriptor desc;
};

static libusb_device *g_devs[MOCK_MAX_DEV];
static int g_ndev = 0;

static bool g_hotplug_available = true;
static int  g_enum_error = 0;

static int  g_present[MOCK_MAX_DEV];
static int  g_script[MOCK_MAX_SCRIPT][MOCK_MAX_DEV];
static int  g_script_len = 0;
static int  g_script_pos = 0;

/* step types */
enum { STEP_ARRIVE = 0, STEP_LEAVE = 1, STEP_SIGNAL = 2 };
static int  g_steps[MOCK_MAX_STEPS][2];
static int  g_step_len = 0;
static int  g_step_pos = 0;

static libusb_hotplug_callback_fn g_arrived_cb = NULL;
static libusb_hotplug_callback_fn g_left_cb = NULL;
static void *g_arrived_ud = NULL;
static void *g_left_ud = NULL;

static bool g_pollfds_available = true;

static int g_event_pipe[2] = {-1, -1};
static struct libusb_pollfd g_pollfd;
static const struct libusb_pollfd *g_pollfd_ptrs[2];

static int g_refcount_errors = 0;

static void mock_unref(libusb_device *dev) {
    if (!dev) return;
    dev->refcnt--;
    /* The mock owns one reference for each device in g_devs[]; libusb's
     * get/free list pair must leave that owner reference intact. Dropping
     * below 1 means a device was unreferenced more times than it was
     * referenced (the real libusb would have freed it). */
    if (dev->refcnt < 1) {
        g_refcount_errors++;
    }
}

void mock_reset(void) {
    for (int i = 0; i < g_ndev; i++) free(g_devs[i]);
    g_ndev = 0;

    g_hotplug_available = true;
    g_enum_error = 0;
    g_pollfds_available = true;
    g_script_len = g_script_pos = 0;
    g_step_len = g_step_pos = 0;
    memset(g_present, 0, sizeof(g_present));
    memset(g_script, 0, sizeof(g_script));
    memset(g_steps, 0, sizeof(g_steps));
    g_arrived_cb = g_left_cb = NULL;
    g_arrived_ud = g_left_ud = NULL;
    g_refcount_errors = 0;

    if (g_event_pipe[0] >= 0) {
        close(g_event_pipe[0]);
        close(g_event_pipe[1]);
        g_event_pipe[0] = g_event_pipe[1] = -1;
    }
    if (pipe(g_event_pipe) == 0) {
        (void)fcntl(g_event_pipe[0], F_SETFL, O_NONBLOCK);
        (void)fcntl(g_event_pipe[1], F_SETFL, O_NONBLOCK);
        /* Prime the pipe so the first wait iteration dispatches promptly. */
        char b = 1;
        ssize_t ig = write(g_event_pipe[1], &b, 1);
        (void)ig;
    }
}

void mock_set_hotplug(bool available) { g_hotplug_available = available; }
void mock_set_enum_error(int err) { g_enum_error = err; }
void mock_set_pollfds(bool available) { g_pollfds_available = available; }

int mock_add_device(uint16_t vid, uint16_t pid) {
    if (g_ndev >= MOCK_MAX_DEV) return -1;
    libusb_device *dev = calloc(1, sizeof(*dev));
    if (!dev) return -1;
    dev->refcnt = 1;
    dev->desc.bLength = sizeof(dev->desc);
    dev->desc.idVendor = vid;
    dev->desc.idProduct = pid;
    g_devs[g_ndev] = dev;
    return g_ndev++;
}

void mock_set_present(int index, bool present) {
    if (index < 0 || index >= MOCK_MAX_DEV) return;
    g_present[index] = present ? 1 : 0;
}

void mock_script_add(const int *present_indices, int count) {
    if (g_script_len >= MOCK_MAX_SCRIPT) return;
    int *row = g_script[g_script_len];
    memset(row, 0, MOCK_MAX_DEV * sizeof(int));
    for (int i = 0; i < count; i++) {
        int idx = present_indices[i];
        if (idx >= 0 && idx < MOCK_MAX_DEV) row[idx] = 1;
    }
    g_script_len++;
}

void mock_scenario_arrive(int dev_index) {
    if (g_step_len >= MOCK_MAX_STEPS) return;
    g_steps[g_step_len][0] = STEP_ARRIVE;
    g_steps[g_step_len][1] = dev_index;
    g_step_len++;
}

void mock_scenario_leave(int dev_index) {
    if (g_step_len >= MOCK_MAX_STEPS) return;
    g_steps[g_step_len][0] = STEP_LEAVE;
    g_steps[g_step_len][1] = dev_index;
    g_step_len++;
}

void mock_scenario_signal(int sig) {
    if (g_step_len >= MOCK_MAX_STEPS) return;
    g_steps[g_step_len][0] = STEP_SIGNAL;
    g_steps[g_step_len][1] = sig;
    g_step_len++;
}

int mock_refcount_errors(void) { return g_refcount_errors; }

/* ---- libusb API ---- */

int libusb_init(libusb_context **ctx) {
    (void)ctx;
    return LIBUSB_SUCCESS;
}

void libusb_exit(libusb_context *ctx) {
    (void)ctx;
    for (int i = 0; i < g_ndev; i++) free(g_devs[i]);
    g_ndev = 0;
}

int libusb_has_capability(uint32_t capability) {
    if (capability == LIBUSB_CAP_HAS_HOTPLUG)
        return g_hotplug_available ? 1 : 0;
    return 0;
}

ssize_t libusb_get_device_list(libusb_context *ctx, libusb_device ***list) {
    (void)ctx;
    if (g_enum_error) return g_enum_error;

    /* Apply the next scripted presence mask, if any. */
    if (g_script_len > 0) {
        int pos = g_script_pos < g_script_len ? g_script_pos : g_script_len - 1;
        for (int i = 0; i < g_ndev; i++) g_present[i] = g_script[pos][i];
        if (g_script_pos < g_script_len) g_script_pos++;
    }

    int n = 0;
    for (int i = 0; i < g_ndev; i++) if (g_present[i]) n++;

    libusb_device **arr = malloc((size_t)(n + 1) * sizeof(*arr));
    if (!arr) return LIBUSB_ERROR_NO_MEM;

    int j = 0;
    for (int i = 0; i < g_ndev; i++) {
        if (g_present[i]) {
            g_devs[i]->refcnt++;   /* get_device_list adds a reference */
            arr[j++] = g_devs[i];
        }
    }
    arr[n] = NULL;
    *list = arr;
    return n;
}

void libusb_free_device_list(libusb_device **list, int unref_devices) {
    if (list && unref_devices) {
        for (int i = 0; list[i]; i++) mock_unref(list[i]);
    }
    free(list);
}

void libusb_unref_device(libusb_device *dev) {
    mock_unref(dev);
}

int libusb_get_device_descriptor(libusb_device *dev,
                                 struct libusb_device_descriptor *desc) {
    if (!dev || !desc) return LIBUSB_ERROR_INVALID_PARAM;
    *desc = dev->desc;
    return LIBUSB_SUCCESS;
}

uint8_t libusb_get_bus_number(libusb_device *dev) {
    (void)dev;
    return 1;
}

uint8_t libusb_get_device_address(libusb_device *dev) {
    (void)dev;
    return 1;
}

int libusb_hotplug_register_callback(libusb_context *ctx,
                                     libusb_hotplug_event event, int flags,
                                     int vendor_id, int product_id, int dev_class,
                                     libusb_hotplug_callback_fn callback,
                                     void *user_data,
                                     libusb_hotplug_callback_handle *handle) {
    (void)ctx; (void)flags; (void)vendor_id; (void)product_id; (void)dev_class;
    if (event == LIBUSB_HOTPLUG_EVENT_DEVICE_ARRIVED) {
        g_arrived_cb = callback;
        g_arrived_ud = user_data;
        if (handle) *handle = 1;
    } else if (event == LIBUSB_HOTPLUG_EVENT_DEVICE_LEFT) {
        g_left_cb = callback;
        g_left_ud = user_data;
        if (handle) *handle = 2;
    }
    return LIBUSB_SUCCESS;
}

void libusb_hotplug_deregister_callback(libusb_context *ctx,
                                        libusb_hotplug_callback_handle handle) {
    (void)ctx;
    if (handle == 1) { g_arrived_cb = NULL; g_arrived_ud = NULL; }
    if (handle == 2) { g_left_cb = NULL; g_left_ud = NULL; }
}

const struct libusb_pollfd **libusb_get_pollfds(libusb_context *ctx) {
    (void)ctx;
    if (!g_pollfds_available || g_event_pipe[0] < 0) return NULL;
    g_pollfd.fd = g_event_pipe[0];
    g_pollfd.events = POLLIN;
    g_pollfd_ptrs[0] = &g_pollfd;
    g_pollfd_ptrs[1] = NULL;
    return g_pollfd_ptrs;
}

void libusb_free_pollfds(const struct libusb_pollfd **pollfds) {
    (void)pollfds;
}

int libusb_handle_events_timeout_completed(libusb_context *ctx,
                                           struct timeval *tv, int *completed) {
    (void)ctx; (void)tv; (void)completed;

    /* Drain our own wakeup pipe. */
    if (g_event_pipe[0] >= 0) {
        char buf[64];
        while (read(g_event_pipe[0], buf, sizeof(buf)) > 0) { }
    }

    if (g_step_pos < g_step_len) {
        int type = g_steps[g_step_pos][0];
        int arg = g_steps[g_step_pos][1];
        g_step_pos++;
        if (type == STEP_ARRIVE) {
            libusb_device *d = g_devs[arg];
            if (g_arrived_cb)
                g_arrived_cb(NULL, d, LIBUSB_HOTPLUG_EVENT_DEVICE_ARRIVED, g_arrived_ud);
        } else if (type == STEP_LEAVE) {
            libusb_device *d = g_devs[arg];
            if (g_left_cb)
                g_left_cb(NULL, d, LIBUSB_HOTPLUG_EVENT_DEVICE_LEFT, g_left_ud);
        } else if (type == STEP_SIGNAL) {
            raise(arg);
        }
    }

    /* Keep the wait loop moving while steps remain. */
    if (g_step_pos < g_step_len && g_event_pipe[1] >= 0) {
        char b = 1;
        ssize_t ig = write(g_event_pipe[1], &b, 1);
        (void)ig;
    }
    return LIBUSB_SUCCESS;
}

const char *libusb_error_name(int errcode) {
    (void)errcode;
    return "mock error";
}
