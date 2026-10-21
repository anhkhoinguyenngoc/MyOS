/*
 * tests/platform_probe_main.c — host-side probe for MyOS platform descriptors.
 *
 * Compiles the real platform descriptor translation units (QEMU virt and the
 * unsupported Apple A8) with the host compiler and prints their fields so
 * tests/test_platform_boundary.py can assert on them. This performs no MMIO and
 * touches no hardware; it validates the descriptors, not any device.
 */
#include <stdint.h>
#include <stdio.h>

#include "platform/apple/a8/iphone6.h"
#include "platform/qemu-virt/qemu_virt.h"

static void dump(const char *tag, const myos_platform_descriptor_t *p)
{
    int unresolved = 0;
    if (p->unresolved != NULL) {
        while (p->unresolved[unresolved] != NULL) {
            unresolved++;
        }
    }

    printf("%s.id=%d\n", tag, (int)p->id);
    printf("%s.name=%s\n", tag, p->name);
    printf("%s.status=%d\n", tag, (int)p->status);
    printf("%s.capabilities=%u\n", tag, (unsigned)p->capabilities);
    printf("%s.uart_mmio_base=0x%llx\n", tag,
           (unsigned long long)p->uart_mmio_base);
    printf("%s.link_load_addr=0x%llx\n", tag,
           (unsigned long long)p->link_load_addr);
    printf("%s.unresolved_null=%d\n", tag, p->unresolved == NULL ? 1 : 0);
    printf("%s.unresolved_count=%d\n", tag, unresolved);
}

int main(void)
{
    dump("qemu", &myos_platform_qemu_virt);
    dump("a8", &myos_platform_apple_a8);
    return 0;
}
