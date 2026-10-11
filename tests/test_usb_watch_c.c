/*
 * tests/test_usb_watch_c.c — offline C-level state-machine harness.
 *
 * Unlike the earlier hand-copied model, this harness links the REAL
 * tools/host/usb_watch.c (compiled with -Dmain=usb_watch_main) against a
 * mock libusb backend. It therefore exercises the actual production code
 * paths: argument parsing, initial enumeration, hotplug dispatch, polling
 * fallback, signal wake-up, timeout arithmetic, cleanup and device
 * reference counting.
 *
 * Each scenario runs in a forked child so global state (signal flags,
 * pipes) is isolated and the real exit code can be observed. If the mock
 * detects a device reference-count underflow (the double-unref bug), the
 * child exits with 200 + code so the parent can report it distinctly.
 *
 * No USB hardware, libusb, or root privileges are required.
 */

#define _GNU_SOURCE
#include <libusb-1.0/libusb.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>

#include "mock_usb.h"

/* Defined in tools/host/usb_watch.c compiled with -Dmain=usb_watch_main. */
extern int usb_watch_main(int argc, char **argv);

#define PONGO_V 0x05ac
#define PONGO_P 0x4141
#define OTHER_V 0x1234
#define OTHER_P 0x5678

static int g_pass = 0;
static int g_fail = 0;

static int run_watch_internal(int argc, char **argv, int sig, useconds_t delay_us) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return -900;
    }
    if (pid == 0) {
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDOUT_FILENO);
            dup2(devnull, STDERR_FILENO);
            if (devnull > 2) close(devnull);
        }
        int rc = usb_watch_main(argc, argv);
        int rce = mock_refcount_errors();
        _exit(rce ? 200 + (rc & 0x3f) : rc);
    }

    if (sig && delay_us > 0) {
        usleep(delay_us);
        kill(pid, sig); /* may fail if child already exited; that is fine */
    }

    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        perror("waitpid");
        return -901;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return -902;
}

static int run_watch(int argc, char **argv) {
    return run_watch_internal(argc, argv, 0, 0);
}

static void check(const char *name, int got, int want) {
    if (got == want) {
        printf("PASS: %s (exit %d)\n", name, got);
        g_pass++;
    } else if (got >= 200) {
        printf("FAIL: %s: device refcount underflow (double-unref) detected, exit %d\n",
               name, got);
        g_fail++;
    } else {
        printf("FAIL: %s (expected %d, got %d)\n", name, want, got);
        g_fail++;
    }
}

/* ---------------- scenarios ---------------- */

static void test_hotplug_present_then_detach(void) {
    mock_reset();
    mock_set_hotplug(true);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    mock_scenario_leave(d);
    char *argv[] = {"usb_watch", "-t", "2000", NULL};
    check("hotplug: present then detach", run_watch(3, argv), 0);
}

static void test_hotplug_present_stays(void) {
    mock_reset();
    mock_set_hotplug(true);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    char *argv[] = {"usb_watch", "-t", "300", NULL};
    check("hotplug: present stays", run_watch(3, argv), 2);
}

static void test_hotplug_absent_then_attach_then_detach(void) {
    mock_reset();
    mock_set_hotplug(true);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_scenario_arrive(d);
    mock_scenario_leave(d);
    char *argv[] = {"usb_watch", "-t", "2000", NULL};
    check("hotplug: absent -> attach -> detach", run_watch(3, argv), 0);
}

static void test_hotplug_absent_never_attaches(void) {
    mock_reset();
    mock_set_hotplug(true);
    char *argv[] = {"usb_watch", "-t", "300", NULL};
    check("hotplug: absent never attaches", run_watch(3, argv), 2);
}

static void test_hotplug_unobserved_detach_not_reported(void) {
    mock_reset();
    mock_set_hotplug(true);
    int d = mock_add_device(PONGO_V, PONGO_P); /* never present */
    mock_scenario_leave(d);                    /* spurious LEFT */
    char *argv[] = {"usb_watch", "-t", "300", NULL};
    check("hotplug: unobserved detach not reported", run_watch(3, argv), 2);
}

static void test_hotplug_non_pongo_ignored(void) {
    mock_reset();
    mock_set_hotplug(true);
    int o = mock_add_device(OTHER_V, OTHER_P);
    mock_set_present(o, true);
    mock_scenario_leave(o);
    char *argv[] = {"usb_watch", "-t", "300", NULL};
    check("hotplug: non-Pongo device ignored", run_watch(3, argv), 2);
}

static void test_enumeration_error(void) {
    mock_reset();
    mock_set_hotplug(true);
    mock_set_enum_error(LIBUSB_ERROR_IO);
    char *argv[] = {"usb_watch", "-t", "300", NULL};
    check("enumeration error -> exit 5", run_watch(3, argv), 5);
}

static void test_initial_scan_refcount_integrity(void) {
    mock_reset();
    mock_set_hotplug(true);
    int o = mock_add_device(OTHER_V, OTHER_P);
    mock_set_present(o, true);              /* non-matching, scanned first */
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    char *argv[] = {"usb_watch", "-t", "200", NULL};
    check("hotplug: initial-scan refcount integrity", run_watch(3, argv), 2);
}

static void test_poll_present_stays_refcount_integrity(void) {
    mock_reset();
    int o = mock_add_device(OTHER_V, OTHER_P);
    mock_set_present(o, true);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    char *argv[] = {"usb_watch", "-p", "-t", "400", NULL};
    check("poll: present stays (refcount integrity)", run_watch(4, argv), 2);
}

static void test_poll_absent_never_attaches(void) {
    mock_reset();
    char *argv[] = {"usb_watch", "-p", "-t", "400", NULL};
    check("poll: absent never attaches -> exit 1", run_watch(4, argv), 1);
}

static void test_poll_present_then_detach(void) {
    mock_reset();
    int d = mock_add_device(PONGO_V, PONGO_P);
    int s0[] = {d};  mock_script_add(s0, 1);
    int s1[] = {0};  mock_script_add(s1, 0);
    char *argv[] = {"usb_watch", "-p", "-t", "2000", NULL};
    check("poll: present at startup then detach", run_watch(4, argv), 0);
}

static void test_poll_absent_then_attach_then_detach(void) {
    mock_reset();
    int d = mock_add_device(PONGO_V, PONGO_P);
    int s0[] = {0};  mock_script_add(s0, 0);
    int s1[] = {d};  mock_script_add(s1, 1);
    int s2[] = {0};  mock_script_add(s2, 0);
    char *argv[] = {"usb_watch", "-p", "-t", "2000", NULL};
    check("poll: absent -> attach -> detach", run_watch(4, argv), 0);
}

static void test_poll_attach_then_stays(void) {
    mock_reset();
    int d = mock_add_device(PONGO_V, PONGO_P);
    int s0[] = {0};  mock_script_add(s0, 0);
    int s1[] = {d};  mock_script_add(s1, 1);
    char *argv[] = {"usb_watch", "-p", "-t", "700", NULL};
    check("poll: attach then stays", run_watch(4, argv), 2);
}

static void test_hotplug_present_then_detach_no_pollfds(void) {
    /* libusb exposes no poll fds: usb_watch must still dispatch events via
     * the bounded (250ms) poll fallback and observe the detach. */
    mock_reset();
    mock_set_hotplug(true);
    mock_set_pollfds(false);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    mock_scenario_leave(d);
    char *argv[] = {"usb_watch", "-t", "2000", NULL};
    check("hotplug: no-libusb-pollfds still detects detach",
          run_watch(3, argv), 0);
}

static void test_hotplug_detach_then_reattach(void) {
    /* Observation ends at the first confirmed detach; later reattach events
     * must not change the result (and must not be mislabeled). */
    mock_reset();
    mock_set_hotplug(true);
    int d = mock_add_device(PONGO_V, PONGO_P);
    mock_set_present(d, true);
    mock_scenario_leave(d);
    mock_scenario_arrive(d);
    mock_scenario_leave(d);
    char *argv[] = {"usb_watch", "-t", "2000", NULL};
    check("hotplug: detach then reattach -> first detach wins",
          run_watch(3, argv), 0);
}

static void test_hotplug_capability_unavailable_poll(void) {
    /* No hotplug capability and no -p: falls back to polling. Device never
     * appears -> exit 1 (never observed present, hotplug unavailable). */
    mock_reset();
    mock_set_hotplug(false);
    char *argv[] = {"usb_watch", "-t", "400", NULL};
    check("no hotplug capability: absent never attaches -> exit 1",
          run_watch(3, argv), 1);
}

static void test_poll_detach_then_reattach(void) {
    mock_reset();
    int d = mock_add_device(PONGO_V, PONGO_P);
    int s0[] = {d};  mock_script_add(s0, 1);   /* present */
    int s1[] = {0};  mock_script_add(s1, 0);   /* detach -> exit 0 */
    int s2[] = {d};  mock_script_add(s2, 1);   /* reattach (unobserved) */
    char *argv[] = {"usb_watch", "-p", "-t", "2000", NULL};
    check("poll: detach then reattach -> first detach wins",
          run_watch(4, argv), 0);
}

static void test_hotplug_signal_during_wait(void) {
    mock_reset();
    mock_set_hotplug(true);
    mock_scenario_signal(SIGINT);
    char *argv[] = {"usb_watch", "-t", "0", NULL};
    check("hotplug: SIGINT interrupts infinite wait", run_watch(3, argv), 2);
}

static void test_hotplug_repeated_signals(void) {
    mock_reset();
    mock_set_hotplug(true);
    mock_scenario_signal(SIGINT);
    mock_scenario_signal(SIGINT);
    mock_scenario_signal(SIGINT);
    char *argv[] = {"usb_watch", "-t", "0", NULL};
    check("hotplug: repeated SIGINT", run_watch(3, argv), 2);
}

static void test_poll_signal_during_wait(void) {
    mock_reset();
    char *argv[] = {"usb_watch", "-p", "-t", "0", NULL};
    /* Device never present; the signal interrupts the wait, so the factual
     * outcome is "absent at startup" (exit 1), not a detach (0) and not a
     * plain timeout (2). */
    int rc = run_watch_internal(4, argv, SIGINT, 300000);
    check("poll: SIGINT interrupts infinite wait", rc, 1);
}

static void test_hotplug_signal_near_timeout(void) {
    mock_reset();
    mock_set_hotplug(true);
    char *argv[] = {"usb_watch", "-t", "800", NULL};
    int rc = run_watch_internal(3, argv, SIGINT, 600000);
    check("hotplug: SIGINT near timeout", rc, 2);
}

static void test_argument_validation(void) {
    char *help[]  = {"usb_watch", "--help", NULL};
    char *bad1[]  = {"usb_watch", "--bogus", NULL};
    char *bad2[]  = {"usb_watch", "-t", "-5", NULL};
    char *bad3[]  = {"usb_watch", "-t", "notanumber", NULL};
    char *bad4[]  = {"usb_watch", "-t", "12abc", NULL};
    char *bad5[]  = {"usb_watch", "-t", "99999999999999999999", NULL};
    char *bad6[]  = {"usb_watch", "-t", NULL};
    check("args: --help -> 0",      run_watch(2, help), 0);
    check("args: unknown -> 4",     run_watch(2, bad1), 4);
    check("args: -t -5 -> 4",       run_watch(3, bad2), 4);
    check("args: -t notanumber -> 4", run_watch(3, bad3), 4);
    check("args: -t 12abc -> 4",    run_watch(3, bad4), 4);
    check("args: -t overflow -> 4", run_watch(3, bad5), 4);
    check("args: -t missing -> 4",  run_watch(2, bad6), 4);
}

int main(void) {
    printf("=== C-level usb_watch tests (real implementation + mock libusb) ===\n\n");

    test_argument_validation();
    test_hotplug_present_then_detach();
    test_hotplug_present_stays();
    test_hotplug_absent_then_attach_then_detach();
    test_hotplug_absent_never_attaches();
    test_hotplug_unobserved_detach_not_reported();
    test_hotplug_non_pongo_ignored();
    test_enumeration_error();
    test_initial_scan_refcount_integrity();
    test_hotplug_present_then_detach_no_pollfds();
    test_poll_present_stays_refcount_integrity();
    test_poll_absent_never_attaches();
    test_poll_present_then_detach();
    test_poll_absent_then_attach_then_detach();
    test_poll_attach_then_stays();
    test_hotplug_detach_then_reattach();
    test_hotplug_capability_unavailable_poll();
    test_poll_detach_then_reattach();
    test_hotplug_signal_during_wait();
    test_hotplug_repeated_signals();
    test_poll_signal_during_wait();
    test_hotplug_signal_near_timeout();

    printf("\n=== Results: %d passed, %d failed ===\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
