/*
 * tools/host/usb_watch.c — MyOS Tier-1 USB observation tool.
 *
 * target: host (Arch Linux x86_64 PC) — runs ONLY on the PC side.
 *
 * Purpose: Observe PongoOS USB device (VID:PID 05ac:4141) and report
 * whether it disappears within a configurable timeout. This is an
 * OBSERVATION tool, not proof of probe execution.
 *
 * What this tool DOES:
 *   - Monitors USB for device 05ac:4141 attach/detach
 *   - Reports timestamped events
 *   - Exits with distinct codes for: absent, detached, timeout, error
 *
 * What this tool does NOT do:
 *   - Issue bootr, upload images, send commands
 *   - Run checkra1n, gaster, irecovery, DFU operations
 *   - Claim that disappearance = probe execution
 *     (disappearance is compatible with: execution, disconnect, reset,
 *      power loss, cable failure, PongoOS crash, USB stack teardown)
 *
 * Exit codes:
 *   0  = device detached after observation began (observed disappearance)
 *   1  = device never observed present during the whole run and hotplug
 *        was unavailable (polling found no attach before timeout)
 *   2  = timeout without detach (device stayed connected, or device absent
 *      but hotplug available and attach never came)
 *   3  = libusb init / setup error
 *   4  = invalid arguments
 *   5  = USB enumeration error
 *
 * Signal design (M-P1.3.2):
 *   SIGINT/SIGTERM only set a `volatile sig_atomic_t` flag and write one
 *   byte to a non-blocking self-pipe. No pthread_* or stdio call is made
 *   from the handler. The main loop sleeps in poll() on libusb's poll fds
 *   plus the self-pipe, so a signal wakes the wait promptly and cleanup
 *   runs exactly once.
 */

#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <stdbool.h>
#include <signal.h>
#include <pthread.h>
#include <errno.h>
#include <unistd.h>
#include <poll.h>
#include <fcntl.h>
#include <limits.h>

#define PONGO_VID     0x05ac
#define PONGO_PID     0x4141

static volatile sig_atomic_t g_shutdown = 0;

/* Self-pipe for async-signal-safe wakeup */
static int g_signal_pipe[2] = {-1, -1};

static void sigint_handler(int sig) {
    (void)sig;
    g_shutdown = 1;
    /* write() is async-signal-safe. The write end is O_NONBLOCK so a full
     * pipe can never block (or deadlock) inside a signal handler; the
     * pipe is already readable in that case, so poll() still wakes up. */
    if (g_signal_pipe[1] >= 0) {
        char byte = 1;
        ssize_t ignored = write(g_signal_pipe[1], &byte, 1);
        (void)ignored;
    }
}

/* Block SIGINT/SIGTERM as early as possible (before main() runs) so that a
 * signal arriving during process startup is queued instead of terminating
 * the process with the default action. main() unblocks them only after the
 * self-pipe and handlers are installed. */
__attribute__((constructor))
static void block_signals_early(void) {
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGINT);
    sigaddset(&mask, SIGTERM);
    (void)sigprocmask(SIG_BLOCK, &mask, NULL);
}

static int set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) return -1;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static void print_timestamp(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    struct tm *tm = localtime(&ts.tv_sec);
    char buf[64];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", tm);
    printf("[%s.%03ld] ", buf, ts.tv_nsec / 1000000);
}

static bool device_matches(libusb_device *dev) {
    struct libusb_device_descriptor desc;
    int r = libusb_get_device_descriptor(dev, &desc);
    if (r != LIBUSB_SUCCESS) return false;
    return (desc.idVendor == PONGO_VID && desc.idProduct == PONGO_PID);
}

static void print_device_info(libusb_device *dev, const char *event) {
    struct libusb_device_descriptor desc;
    if (libusb_get_device_descriptor(dev, &desc) == 0) {
        print_timestamp();
        printf("%s: VID=%04x PID=%04x bus=%u addr=%u\n",
               event, desc.idVendor, desc.idProduct,
               libusb_get_bus_number(dev), libusb_get_device_address(dev));
    } else {
        print_timestamp();
        printf("%s: (descriptor read failed)\n", event);
    }
}

/* Hotplug callback context.
 *
 * The mutex protects the flags; in this single-threaded dispatch design the
 * libusb callbacks run on the same thread as the wait loop, but the mutex
 * keeps the state machine safe if libusb ever dispatches from another
 * thread. `ever_present` records that the device was seen at least once,
 * which is what distinguishes exit 1 (never seen) from exit 2 (seen, no
 * detach). */
typedef struct {
    bool device_present;
    bool ever_present;
    bool detach_observed;
    pthread_mutex_t mutex;
    pthread_cond_t cond;
} watch_ctx_t;

static int LIBUSB_CALL hotplug_arrived(libusb_context *ctx, libusb_device *dev,
                                       libusb_hotplug_event event, void *user_data) {
    (void)ctx; (void)event;
    watch_ctx_t *w = (watch_ctx_t *)user_data;
    pthread_mutex_lock(&w->mutex);
    if (!w->device_present && device_matches(dev)) {
        w->device_present = true;
        w->ever_present = true;
        print_device_info(dev, "ATTACH");
        pthread_cond_signal(&w->cond);
    }
    pthread_mutex_unlock(&w->mutex);
    return LIBUSB_SUCCESS;
}

static int LIBUSB_CALL hotplug_left(libusb_context *ctx, libusb_device *dev,
                                    libusb_hotplug_event event, void *user_data) {
    (void)ctx; (void)event;
    watch_ctx_t *w = (watch_ctx_t *)user_data;
    pthread_mutex_lock(&w->mutex);
    /* Only a device we actually observed present can be "detached". This
     * avoids reporting an unobserved detach as a confirmed one. */
    if (w->device_present && device_matches(dev)) {
        w->device_present = false;
        w->detach_observed = true;
        print_device_info(dev, "DETACH");
        pthread_cond_signal(&w->cond);
    }
    pthread_mutex_unlock(&w->mutex);
    return LIBUSB_SUCCESS;
}

/* Polling fallback when hotplug is unavailable (or forced with -p).
 * Waits for an attach, then watches for a detach, until timeout/signal.
 * Returns 0 if a detach was observed, -1 otherwise. */
static int poll_loop(libusb_context *ctx, watch_ctx_t *w, int timeout_ms) {
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);

    print_timestamp();
    printf("Polling fallback active (hotplug unavailable), interval=250ms\n");

    struct pollfd pfd = { .fd = g_signal_pipe[0], .events = POLLIN };

    while (!g_shutdown) {
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed_ms = (now.tv_sec - start.tv_sec) * 1000L +
                          (now.tv_nsec - start.tv_nsec) / 1000000L;

        if (timeout_ms > 0 && elapsed_ms >= timeout_ms) {
            break;
        }

        libusb_device **list = NULL;
        ssize_t cnt = libusb_get_device_list(ctx, &list);
        bool found = false;
        if (cnt >= 0) {
            for (ssize_t i = 0; i < cnt; i++) {
                if (device_matches(list[i])) {
                    found = true;
                    break;
                }
            }
            /* unref_devices=1 releases the reference that
             * libusb_get_device_list() added for every entry. Do NOT call
             * libusb_unref_device() here as well: that would decrement
             * twice and corrupt libusb's internal reference counts. */
            libusb_free_device_list(list, 1);
        }

        pthread_mutex_lock(&w->mutex);
        if (found && !w->device_present) {
            w->device_present = true;
            w->ever_present = true;
            print_timestamp();
            printf("ATTACH (poll)\n");
        } else if (!found && w->device_present) {
            w->device_present = false;
            w->detach_observed = true;
            print_timestamp();
            printf("DETACH (poll)\n");
            pthread_mutex_unlock(&w->mutex);
            return 0;
        }
        pthread_mutex_unlock(&w->mutex);

        /* Wait with poll() on the signal pipe for 250ms or until signal. */
        int poll_timeout = 250;
        if (timeout_ms > 0) {
            long remaining = timeout_ms - elapsed_ms;
            if (remaining < poll_timeout) poll_timeout = (int)remaining;
            if (poll_timeout < 0) poll_timeout = 0;
        }

        struct pollfd local = pfd;
        int pret = poll(&local, 1, poll_timeout);
        if (pret > 0 && (local.revents & POLLIN)) {
            char buf[64];
            while (read(g_signal_pipe[0], buf, sizeof(buf)) > 0) { }
        }
    }
    return -1;
}

static int check_initial_presence(libusb_context *ctx, watch_ctx_t *w) {
    libusb_device **list = NULL;
    ssize_t cnt = libusb_get_device_list(ctx, &list);
    if (cnt < 0) {
        fprintf(stderr, "usb_watch: libusb_get_device_list failed: %s\n",
                libusb_error_name((int)cnt));
        return -1;
    }

    bool found = false;
    for (ssize_t i = 0; i < cnt; i++) {
        if (device_matches(list[i])) {
            found = true;
            print_device_info(list[i], "INITIAL");
            break;
        }
    }
    /* Free with unref=1; do not also call libusb_unref_device() (double
     * decrement / potential use-after-free). */
    libusb_free_device_list(list, 1);

    pthread_mutex_lock(&w->mutex);
    w->device_present = found;
    if (found) w->ever_present = true;
    pthread_mutex_unlock(&w->mutex);

    return found ? 0 : 1;
}

static void usage(const char *prog) {
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -t, --timeout MS    Timeout in milliseconds (0 = wait forever, default: 30000)\n"
        "  -p, --poll          Force polling mode (disable hotplug)\n"
        "  -h, --help          Show this help\n"
        "\n"
        "Exit codes:\n"
        "  0  device detached after observation began\n"
        "  1  device never observed present (no hotplug; polling timed out)\n"
        "  2  timeout without detach\n"
        "  3  libusb init / setup error\n"
        "  4  invalid arguments\n"
        "  5  USB enumeration error\n"
        "\n"
        "NOTE: Device disappearance is an OBSERVATION compatible with\n"
        "      probe execution, but also with disconnect, reset, power loss,\n"
        "      cable failure, or PongoOS crash. It is NOT proof of execution.\n", prog);
}

int main(int argc, char **argv) {
    int timeout_ms = 30000;
    bool force_poll = false;
    int result = 2;
    bool print_result = false;

    watch_ctx_t w;
    memset(&w, 0, sizeof(w));
    bool w_inited = false;
    bool libusb_inited = false;
    libusb_hotplug_callback_handle hp_arrived = 0, hp_left = 0;

    g_shutdown = 0;
    g_signal_pipe[0] = g_signal_pipe[1] = -1;

    /* Block SIGINT/SIGTERM while the self-pipe and handlers are installed,
     * so a signal arriving during startup is queued rather than lost (or
     * terminating the process with the default action). They were already
     * blocked by the load-time constructor; keep them blocked through the
     * setup below. */
    sigset_t block_mask;
    sigemptyset(&block_mask);
    sigaddset(&block_mask, SIGINT);
    sigaddset(&block_mask, SIGTERM);
    (void)sigprocmask(SIG_BLOCK, &block_mask, NULL);

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--timeout") == 0) {
            if (i + 1 >= argc) { usage(argv[0]); return 4; }
            const char *arg = argv[++i];
            char *end = NULL;
            errno = 0;
            long v = strtol(arg, &end, 10);
            if (errno != 0 || end == arg || *end != '\0' || v < 0 || v > INT_MAX) {
                fprintf(stderr, "usb_watch: invalid timeout: '%s'\n", arg);
                usage(argv[0]);
                return 4;
            }
            timeout_ms = (int)v;
        } else if (strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--poll") == 0) {
            force_poll = true;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 4;
        }
    }

    /* Create self-pipe for async-signal-safe wakeup */
    if (pipe(g_signal_pipe) != 0) {
        perror("usb_watch: pipe");
        return 3;
    }
    /* Both ends non-blocking: the read end so the loop never stalls while
     * draining, the write end so the handler can never block. */
    if (set_nonblocking(g_signal_pipe[0]) != 0 ||
        set_nonblocking(g_signal_pipe[1]) != 0) {
        perror("usb_watch: fcntl");
        close(g_signal_pipe[0]);
        close(g_signal_pipe[1]);
        g_signal_pipe[0] = g_signal_pipe[1] = -1;
        return 3;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = sigint_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0; /* we wake poll() ourselves via the self-pipe */
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Setup complete: deliver any signal that arrived while blocked. The
     * signature is installed above, so an early signal is handled (it sets
     * g_shutdown and wakes the self-pipe) instead of killing the process. */
    sigprocmask(SIG_UNBLOCK, &block_mask, NULL);

    int r = libusb_init(NULL);
    if (r != LIBUSB_SUCCESS) {
        fprintf(stderr, "usb_watch: libusb_init failed: %s\n", libusb_error_name(r));
        result = 3;
        goto cleanup;
    }
    libusb_inited = true;

    bool has_hotplug = libusb_has_capability(LIBUSB_CAP_HAS_HOTPLUG);
    if (force_poll) has_hotplug = false;

    pthread_mutex_init(&w.mutex, NULL);
    pthread_cond_init(&w.cond, NULL);
    w_inited = true;

    if (has_hotplug) {
        /* Register hotplug callbacks FIRST, then enumerate. Callbacks are
         * only dispatched from libusb_handle_events*, which this thread
         * calls after enumeration, so no ATTACH can be observed before the
         * initial scan overwrites device_present. */
        r = libusb_hotplug_register_callback(NULL, LIBUSB_HOTPLUG_EVENT_DEVICE_ARRIVED,
                                             0, PONGO_VID, PONGO_PID, LIBUSB_HOTPLUG_MATCH_ANY,
                                             hotplug_arrived, &w, &hp_arrived);
        if (r != LIBUSB_SUCCESS) {
            fprintf(stderr, "usb_watch: hotplug arrived register failed: %s\n", libusb_error_name(r));
            result = 3;
            goto cleanup;
        }
        r = libusb_hotplug_register_callback(NULL, LIBUSB_HOTPLUG_EVENT_DEVICE_LEFT,
                                             0, PONGO_VID, PONGO_PID, LIBUSB_HOTPLUG_MATCH_ANY,
                                             hotplug_left, &w, &hp_left);
        if (r != LIBUSB_SUCCESS) {
            fprintf(stderr, "usb_watch: hotplug left register failed: %s\n", libusb_error_name(r));
            result = 3;
            goto cleanup;
        }
    }

    int init_rc = check_initial_presence(NULL, &w);
    if (init_rc < 0) {
        result = 5;
        goto cleanup;
    }

    if (init_rc == 1) {
        print_timestamp();
        printf("Device 05ac:4141 not present at startup\n");
        if (has_hotplug) {
            print_timestamp();
            printf("Waiting for attach...\n");
        }
    } else {
        print_timestamp();
        printf("Device 05ac:4141 present at startup\n");
    }

    if (has_hotplug) {
        struct timespec deadline;
        bool have_deadline = false;
        struct timespec now;
        if (timeout_ms > 0) {
            clock_gettime(CLOCK_MONOTONIC, &deadline);
            deadline.tv_sec += timeout_ms / 1000;
            deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
            if (deadline.tv_nsec >= 1000000000L) {
                deadline.tv_sec++;
                deadline.tv_nsec -= 1000000000L;
            }
            have_deadline = true;
        }

        while (!g_shutdown && !w.detach_observed) {
            int poll_timeout = -1;
            if (have_deadline) {
                clock_gettime(CLOCK_MONOTONIC, &now);
                long remaining_ms = (deadline.tv_sec - now.tv_sec) * 1000L +
                                    (deadline.tv_nsec - now.tv_nsec) / 1000000L;
                if (remaining_ms <= 0) {
                    break;
                }
                if (remaining_ms > INT_MAX) remaining_ms = INT_MAX;
                poll_timeout = (int)remaining_ms;
            }

            /* Re-fetch the libusb poll fds each iteration: the set can
             * change as libusb processes events. Append the signal pipe. */
            const struct libusb_pollfd **lp = libusb_get_pollfds(NULL);
            int nfds = 0;
            if (lp) {
                while (lp[nfds]) nfds++;
            }

            struct pollfd *pfds = calloc((size_t)nfds + 1, sizeof(*pfds));
            if (!pfds) {
                if (lp) libusb_free_pollfds(lp);
                fprintf(stderr, "usb_watch: calloc failed\n");
                result = 3;
                goto cleanup;
            }
            for (int i = 0; i < nfds; i++) {
                pfds[i].fd = lp[i]->fd;
                pfds[i].events = lp[i]->events;
            }
            if (lp) libusb_free_pollfds(lp);
            pfds[nfds].fd = g_signal_pipe[0];
            pfds[nfds].events = POLLIN;

            /* If libusb exposes no poll fds we must still dispatch events
             * periodically, so bound the wait instead of sleeping until the
             * deadline. */
            if (nfds == 0 && (poll_timeout < 0 || poll_timeout > 250))
                poll_timeout = 250;

            int pret = poll(pfds, (nfds_t)(nfds + 1), poll_timeout);
            if (pret < 0 && errno != EINTR) {
                perror("usb_watch: poll");
                free(pfds);
                result = 3;
                goto cleanup;
            }

            if (pfds[nfds].revents & POLLIN) {
                char buf[64];
                while (read(g_signal_pipe[0], buf, sizeof(buf)) > 0) { }
            }
            free(pfds);

            if (g_shutdown) break;

            /* Process USB events with zero timeout (we already waited). */
            struct timeval tv = {0, 0};
            libusb_handle_events_timeout_completed(NULL, &tv, NULL);
        }
    } else {
        poll_loop(NULL, &w, timeout_ms);
    }

    if (w.detach_observed) {
        result = 0;
    } else if (!has_hotplug && !w.ever_present) {
        result = 1;
    } else {
        result = 2;
    }
    print_result = true;

cleanup:
    if (hp_arrived) libusb_hotplug_deregister_callback(NULL, hp_arrived);
    if (hp_left) libusb_hotplug_deregister_callback(NULL, hp_left);
    if (w_inited) {
        pthread_mutex_destroy(&w.mutex);
        pthread_cond_destroy(&w.cond);
    }
    if (libusb_inited) libusb_exit(NULL);
    if (g_signal_pipe[0] >= 0) {
        close(g_signal_pipe[0]);
        g_signal_pipe[0] = -1;
    }
    if (g_signal_pipe[1] >= 0) {
        close(g_signal_pipe[1]);
        g_signal_pipe[1] = -1;
    }

    if (print_result) {
        print_timestamp();
        if (result == 0) {
            printf("RESULT: DETACH_OBSERVED\n");
        } else if (result == 1) {
            printf("RESULT: ABSENT_AT_STARTUP\n");
        } else {
            printf("RESULT: TIMEOUT_NO_DETACH\n");
        }
    }

    return result;
}
