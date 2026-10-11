/*
 * tests/mock_usb.h — scenario control API for the mock libusb backend.
 *
 * The offline C harness (tests/test_usb_watch_c.c) uses these hooks to
 * script device presence and hotplug events, then runs the REAL
 * tools/host/usb_watch.c (compiled with -Dmain=usb_watch_main) against it.
 */
#ifndef MYOS_MOCK_USB_H
#define MYOS_MOCK_USB_H

#include <stdbool.h>
#include <stdint.h>

/* Reset all mock state (devices, scripts, callbacks, event pipe). */
void mock_reset(void);

/* Capability / error injection. */
void mock_set_hotplug(bool available);
void mock_set_enum_error(int err);

/* If false, libusb_get_pollfds() returns NULL (no event fds); exercises the
 * capped-poll fallback inside usb_watch's hotplug wait loop. Default true. */
void mock_set_pollfds(bool available);

/* Allocate a device; returns its index (>= 0) or -1 on failure. Presence is
 * NOT implied: call mock_set_present() for the no-script enumeration path. */
int mock_add_device(uint16_t vid, uint16_t pid);

/* Mark a device present/absent for enumerations that are not scripted. */
void mock_set_present(int index, bool present);

/* Script the presence mask returned by successive libusb_get_device_list()
 * calls (one entry per call, last entry repeated). Each entry is a list of
 * device indices that are present. */
void mock_script_add(const int *present_indices, int count);

/* Hotplug events emitted by successive libusb_handle_events_* calls. */
void mock_scenario_arrive(int dev_index);
void mock_scenario_leave(int dev_index);
void mock_scenario_signal(int sig);

/* Non-zero if any unref drove a device reference count below its owner
 * reference (double-unref / use-after-free simulation). */
int mock_refcount_errors(void);

#endif /* MYOS_MOCK_USB_H */
