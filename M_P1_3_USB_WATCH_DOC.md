# M-P1.3 Tier-1 USB Watch — Tool Documentation

**Tool**: `build/usb_watch`  
**Milestone**: M-P1.3 — Tier-1 Execution Witness  
**Revision**: M-P1.3.2 — runtime correctness audit (2026-10-11)  
**Repository**: MyOS @ f7f89d5 + M-P1.3.2 working-tree changes

---

## 1. Purpose

`usb_watch` is a **host-side USB observation tool** that monitors the PongoOS USB device (VID:PID `05ac:4141`) and reports whether it disappears within a configurable timeout.

**This is an OBSERVATION tool, not a proof of probe execution.**

---

## 2. What the Tool Proves and Does Not Prove

| Claim | Supported? | Evidence |
|-------|------------|----------|
| Device `05ac:4141` was present at startup | ✅ YES | Direct USB enumeration |
| Device `05ac:4141` detached after observation began | ✅ YES | Hotplug `DEVICE_LEFT` event or polling delta |
| Device stayed connected for entire timeout | ✅ YES | No detach event observed |
| **Probe executed on A8** | ❌ NO | **Not proven** — disappearance is compatible with: probe execution, USB cable disconnect, device reset, power loss, PongoOS crash, USB stack teardown, host controller reset |

**Key limitation**: USB disappearance is a **necessary but not sufficient** condition for probe execution. It only establishes that the PongoOS USB interface is no longer visible.

---

## 3. Invocation

```bash
build/usb_watch [options]
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `-t, --timeout MS` | Timeout in milliseconds (0 = wait forever) | `30000` (30s) |
| `-p, --poll` | Force polling mode (disable hotplug) | hotplug if available |
| `-h, --help` | Show help and exit | — |

### Examples

```bash
# Wait 30s for device to disappear (default)
build/usb_watch

# Wait 10s with explicit timeout
build/usb_watch -t 10000

# Wait forever until device disappears
build/usb_watch -t 0

# Force polling mode (no hotplug)
build/usb_watch -p -t 5000
```

---

## 4. Exit Codes

| Code | Meaning | When |
|------|---------|------|
| `0` | **DETACH_OBSERVED** | Device was present and detached during observation |
| `1` | **ABSENT_AT_STARTUP** | Device was never observed present during the entire run AND hotplug was unavailable (polling found no attach before timeout/signal) |
| `2` | **TIMEOUT_NO_DETACH** | Timeout/signal without detach (device stayed connected, or device absent but hotplug available and attach never came) |
| `3` | **SETUP_ERROR** | libusb init failure, self-pipe setup failure, hotplug registration failure |
| `4` | **INVALID_ARGS** | Bad command-line arguments (including non-numeric / negative / overflowing `-t` values) |
| `5` | **USB_ENUM_ERROR** | Failed to enumerate USB devices |

---

## 5. Output Format

```
[2026-10-11 14:32:15.042] INITIAL: device present
[2026-10-11 14:32:18.127] DETACH: VID=05ac PID=4141 bus=1 addr=3
[2026-10-11 14:32:18.127] RESULT: DETACH_OBSERVED
```

- Timestamped events: `INITIAL`, `ATTACH`, `DETACH`
- Final `RESULT:` line with exit code meaning
- All timestamps in local time with millisecond precision

---

## 6. Manual Usage During Device Session

### Prerequisites
1. iPhone 6 in checkra1n-pwned state running PongoOS
2. PongoOS device visible as `05ac:4141` (verified via `lsusb`)
3. Host has `libusb-1.0` and appropriate USB permissions (udev rules or root)

### Typical Workflow

```bash
# 1. Verify PongoOS device is present
lsusb -d 05ac:4141
# Should show: Bus 001 Device 003: ID 05ac:4141 Apple, Inc.

# 2. Start usb_watch in background (30s timeout)
build/usb_watch -t 30000 &
WATCH_PID=$!

# 3. On the device (via PongoOS shell), upload and boot the probe
#    (requires separate checkra1n/gaster session — NOT done by usb_watch)
#    pongoOS> /send path/to/pongo_probe.bin
#    pongoOS> bootr

# 4. Wait for usb_watch to complete
wait $WATCH_PID
WATCH_RC=$?

# 5. Interpret result
case $WATCH_RC in
    0) echo "Device disappeared — compatible with probe execution" ;;
    2) echo "Device stayed connected — probe may not have executed or USB not torn down" ;;
    1) echo "Device never present — check USB connection" ;;
    *) echo "Error: $WATCH_RC" ;;
esac
```

---

## 7. Why USB Disappearance Alone Is Not Definitive Evidence

### Sources of False Positives (disappearance without probe execution)

| Cause | Mechanism |
|-------|-----------|
| USB cable unplugged | Physical disconnect |
| Device reset / reboot | PongoOS or hardware reset |
| Power loss | Battery depletion, charger disconnect |
| PongoOS crash | Kernel panic before `bootr` |
| USB stack teardown | PongoOS `usb_teardown()` without `bootr` |
| Host controller reset | OS/hub power management |
| `bootr` fails before jump | Invalid image, wrong entry point |

### Sources of False Negatives (probe executes but device stays)

| Cause | Mechanism |
|-------|-----------|
| PongoOS doesn't tear down USB | Bug or modified build |
| Probe doesn't park / crashes | Probe bug, exception before park |
| USB device re-enumerates | Same VID:PID but new device instance |

---

## 8. Additional Evidence to Strengthen Conclusion

To move from **observation** to **evidence**, combine with independent signals:

| Tier | Mechanism | What It Proves |
|------|-----------|----------------|
| **Tier-1a** | `lsusb` watch + probe `bootr` | Device disappeared *after* `bootr` issued |
| **Tier-1b** | Serial console log | Probe printed parking message (`b .`) |
| **Tier-2** | Probe report read-back (Tier-0, M-P1.1) | Register state captured at entry |
| **Tier-3** | Reboot forensics | Report persists in upload buffer after reboot |

**Recommended minimal evidence chain for M-P1:**
1. `usb_watch` exit 0 (device disappeared)
2. **AND** `bootr` was issued immediately before (temporal correlation)
3. **AND** no other USB activity (cable intact, no host reset)
4. **AND** serial console shows PongoOS `Booting...` message before disappearance

---

## 9. Implementation Notes

- **Dependencies**: `libusb-1.0`, `pthread` (standard on Arch Linux)
- **Hotplug**: Uses `libusb_hotplug_register_callback` when `LIBUSB_CAP_HAS_HOTPLUG`
- **Polling fallback**: 250ms interval when hotplug unavailable or `-p` given;
  the polling loop also waits for an initial attach (absent device) and only
  exits `1` afterward if the device was never seen.
- **Threading**: Hotplug callbacks are dispatched by `libusb_handle_events*`
  from the main thread inside the wait loop; a mutex guards the shared state.
- **Signal handling (M-P1.3.2)**:
  - `SIGINT`/`SIGTERM` handlers are async-signal-safe: they set a
    `volatile sig_atomic_t` flag and `write()` one byte to a **non-blocking
    self-pipe**. No `pthread_*`, `printf`, or `malloc` is called from the
    handler.
  - The wait loop `poll()`s libusb's poll fds **plus** the self-pipe, so the
    signal wakes the wait promptly; cleanup runs exactly once on exit.
  - Both pipe ends are `O_NONBLOCK`; the write end can never block inside
    the handler, so repeated signals cannot deadlock.
  - Signals are blocked by a load-time constructor and remain blocked until
    the pipe and handlers are installed, then unblocked — a signal arriving
    during startup is handled, not fatal (sub-millisecond pre-init delivery
    remains an inherent race; see limitations).
  - `-t` is parsed with `strtol` and fully validated; `-t notanumber`,
    negative, and overflowing values exit `4` instead of silently becoming
    an infinite wait.
- **No device commands**: Never sends control transfers, bulk uploads, or shell commands

---

## 10. Limitations & Known Issues

1. **Hotplug race**: If a device attaches and detaches before the first
   `libusb_handle_events*` dispatch, the queued events are processed in
   order (ARRIVED then LEFT) and a detach is still reported correctly. A
   device that was already present at registration time emits no callback.
2. **No device identity tracking**: Cannot distinguish same device re-enumeration from new device
3. **Polling granularity**: 250ms interval may miss very brief disconnects
4. **Requires USB permissions**: udev rules or root for device access
5. **No probe-specific correlation**: Cannot link disappearance to specific `bootr` invocation
6. **Startup signal race**: A signal delivered before the runtime can run
   the handler installer (≈<1 ms after `exec`) necessarily takes the default
   action (termination). Signals arriving at or after handler installation
   are queued and handled cleanly (verified in CI tests).
7. **Detach vs. absence**: Poll mode reports exit `1` (never present) for a
   device that never appears; it can still observe a later attach + detach.
   Hotplug mode reports exit `2` if the device simply never appeared. Both
   facts are distinct from a confirmed detach (exit `0`).

---

## 11. Files

| File | Description |
|------|-------------|
| `tools/host/usb_watch.c` | Main implementation |
| `tests/test_usb_watch.py` | Offline tests of the **real binary** (args, timeout, signals) + real C harness results (21 cases) |
| `tests/test_usb_watch_c.c` | C harness: runs the real `usb_watch.c` against a mock libusb (28 state-machine cases) |
| `tests/mock_usb.c`, `tests/mock_usb.h` | Mock libusb backend + scenario scripting (refcount-checked) |
| `tests/mock/libusb-1.0/libusb.h` | Minimal mock libusb header (include-path shadow) |
| `Makefile` | Targets `usb-watch`, `usb-watch-test`, `usb-watch-check` |

## 12. Changes in M-P1.3.2 (runtime correctness audit)

| # | Defect found | Fix |
|---|--------------|-----|
| 1 | Device-list refcount bug: manual `libusb_unref_device()` **plus** `libusb_free_device_list(list,1)` decremented non-matching devices twice (use-after-free risk) | Manual unrefs removed; `libusb_free_device_list(list,1)` is the single release path (regression-checked by mock refcounting) |
| 2 | `-t notanumber` silently became an infinite wait (`atoi`→0) | `strtol` + full validation; invalid values exit `4` |
| 3 | Signal-handler write end could block after ~64K signals; stale fd usable post-close | Both pipe ends `O_NONBLOCK`; pipe fds reset to `-1` after close |
| 4 | Signals during startup could use the default action; race between setup steps | Load-time constructor + block-until-installed, explicit unblock after `sigaction` |
| 5 | Poll mode with an absent device exited `1` immediately, even though polling can observe a later attach | Polling now waits for an initial attach; exit `1` only after the device was never observed |
| 6 | libusb poll fds fetched once; missing fds could stall event dispatch | Poll fds re-fetched each iteration; capped poll when libusb exposes none |
| 7 | Mutex/cond leaked on early-error paths | Single `cleanup:` path destroys them and deregisters callbacks on every exit |

The earlier M-P1.3.1 fix (removing `pthread_cond_signal()` from the signal
handler, replacing it with the self-pipe) was verified correct in the real
implementation: the handler performs only the `write()` of one byte.

---

## 13. Next Steps (M-P1.4+)

- **M-P1.4**: Correlate `usb_watch` disappearance with manual `bootr` trigger via timestamped logs
- **M-P1.5**: Add serial console capture to Tier-1 evidence chain
- **M-P2**: Design probe report retrieval mechanism (requires PongoOS modification or reboot forensics)