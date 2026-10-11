/*
 * tests/mock/libusb-1.0/libusb.h
 *
 * Minimal mock of the libusb-1.0 API, used ONLY by the offline C-level
 * usb_watch test harness. It is placed first on the include path so that
 * `#include <libusb-1.0/libusb.h>` inside tools/host/usb_watch.c resolves
 * here instead of the system header. No real USB access is performed.
 */
#ifndef MYOS_MOCK_LIBUSB_H
#define MYOS_MOCK_LIBUSB_H

#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>
#include <sys/time.h>
#include <poll.h>

#define LIBUSB_CALL

#define LIBUSB_SUCCESS              0
#define LIBUSB_ERROR_IO            -1
#define LIBUSB_ERROR_INVALID_PARAM -2
#define LIBUSB_ERROR_ACCESS        -3
#define LIBUSB_ERROR_NO_DEVICE     -4
#define LIBUSB_ERROR_NOT_FOUND     -5
#define LIBUSB_ERROR_BUSY          -6
#define LIBUSB_ERROR_TIMEOUT       -7
#define LIBUSB_ERROR_OVERFLOW      -8
#define LIBUSB_ERROR_PIPE          -9
#define LIBUSB_ERROR_INTERRUPTED  -10
#define LIBUSB_ERROR_NO_MEM       -11
#define LIBUSB_ERROR_NOT_SUPPORTED -12
#define LIBUSB_ERROR_OTHER        -99

#define LIBUSB_CAP_HAS_HOTPLUG 0x0001

typedef struct libusb_context libusb_context;
typedef struct libusb_device libusb_device;

struct libusb_device_descriptor {
    uint8_t  bLength;
    uint8_t  bDescriptorType;
    uint16_t bcdUSB;
    uint8_t  bDeviceClass;
    uint8_t  bDeviceSubClass;
    uint8_t  bDeviceProtocol;
    uint8_t  bMaxPacketSize0;
    uint16_t idVendor;
    uint16_t idProduct;
    uint16_t bcdDevice;
    uint8_t  iManufacturer;
    uint8_t  iProduct;
    uint8_t  iSerialNumber;
    uint8_t  bNumConfigurations;
};

typedef enum {
    LIBUSB_HOTPLUG_EVENT_DEVICE_ARRIVED = 0x01,
    LIBUSB_HOTPLUG_EVENT_DEVICE_LEFT    = 0x02
} libusb_hotplug_event;

#define LIBUSB_HOTPLUG_MATCH_ANY -1

typedef int libusb_hotplug_callback_handle;
typedef int (*libusb_hotplug_callback_fn)(libusb_context *ctx,
                                          libusb_device *dev,
                                          libusb_hotplug_event event,
                                          void *user_data);

struct libusb_pollfd {
    int fd;
    short events;
};

int libusb_init(libusb_context **ctx);
void libusb_exit(libusb_context *ctx);
int libusb_has_capability(uint32_t capability);
ssize_t libusb_get_device_list(libusb_context *ctx, libusb_device ***list);
void libusb_free_device_list(libusb_device **list, int unref_devices);
void libusb_unref_device(libusb_device *dev);
int libusb_get_device_descriptor(libusb_device *dev,
                                 struct libusb_device_descriptor *desc);
uint8_t libusb_get_bus_number(libusb_device *dev);
uint8_t libusb_get_device_address(libusb_device *dev);
int libusb_hotplug_register_callback(libusb_context *ctx,
                                     libusb_hotplug_event event, int flags,
                                     int vendor_id, int product_id, int dev_class,
                                     libusb_hotplug_callback_fn callback,
                                     void *user_data,
                                     libusb_hotplug_callback_handle *handle);
void libusb_hotplug_deregister_callback(libusb_context *ctx,
                                        libusb_hotplug_callback_handle handle);
const struct libusb_pollfd **libusb_get_pollfds(libusb_context *ctx);
void libusb_free_pollfds(const struct libusb_pollfd **pollfds);
int libusb_handle_events_timeout_completed(libusb_context *ctx,
                                           struct timeval *tv, int *completed);
const char *libusb_error_name(int errcode);

#endif /* MYOS_MOCK_LIBUSB_H */
