/*
 * MyOS M-P1 — PongoOS probe linker script (raw image for `bootr`).
 *
 * target: iphone6 (Apple A8 real hardware) via PongoOS BOOT_FLAG_RAW path.
 *
 * The image is POSITION-INDEPENDENT (pongo_probe.S uses PC-relative
 * addressing only), so the link base is arbitrary; it is linked at 0 so
 * that file offsets equal link addresses and `objcopy -O binary` yields
 * the exact upload bytes. The PongoOS upload buffer provides the runtime
 * base (dynamic; M2_A4 Strategy B): entry.c:311 jumps to the buffer
 * START, hence ENTRY(pongo_probe_entry) is ASSERTed to file offset 0
 * (M2_A4:309-310) and the report block is fixed at file offset 0x200
 * (RPT_OFF in pongo_probe.S). No other load address appears anywhere:
 * inventing a fixed physical address is explicitly unsupported
 * (M2_A4:294-295). Total size is ASSERTed against the 1 MiB PongoOS
 * upload buffer (usbloader.c:94-99, M2_A4:301-306).
 */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(pongo_probe_entry)

SECTIONS
{
    . = 0x0;
    .text : {
        KEEP(*(.text.probe))
        KEEP(*(.text.probe.*))
    }
    .report 0x200 : {
        KEEP(*(.report))
        KEEP(*(.report.*))
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
        *(.eh_frame_hdr)
        *(.gnu*)
    }
}

/* bootr jumps to the buffer start: entry must be at file offset 0. */
ASSERT(pongo_probe_entry == 0x0, "probe entry must be at file offset 0");
/* Report block must sit exactly at RPT_OFF (checked again off-device). */
ASSERT(probe_report == 0x200, "probe report must be at file offset 0x200");
/* Whole image must fit the PongoOS upload buffer. */
ASSERT(. <= 0x100000, "probe exceeds 1MiB PongoOS upload buffer");
