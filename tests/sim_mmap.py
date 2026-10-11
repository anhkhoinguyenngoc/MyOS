#!/usr/bin/env python3
"""
MyOS M2 Memory-Map Simulator (corrected)

Models the PongoOS physical memory layout and the MyOS M2 kernel load
plan. This is a HOST-SIDE SIMULATION ONLY: it does not run on the device
and does not modify PongoOS.

Key facts (from pongoos-src audit, revision 4c9b7541629234147fcc778f0ce4162482aaccef):

Address conversion (mm.c:893-901):
    phystokv(pa)      = pa - 0x800000000 + kCacheableView
    vatophys_static(va) = va - kCacheableView + 0x800000000
  With kCacheableView = 0x400000000 (pongo.h:344):
    VA = PA - 0x400000000        (cacheable view of DRAM)
    PA = VA + 0x400000000

boot_args fields (pongo.h:77-104) are PHYSICAL addresses:
    physBase        - physical base of DRAM (typically 0x800000000 on A12)
    memSize         - physical size of DRAM
    topOfKernelData - highest PHYSICAL address used by PongoOS kernel data

Allocator regions (mm.c:794-833, alloc_init):
    ppages          = memSize >> 14                      (16 KB page count)
    alloc_static_base (VA) = (topOfKernelData - 0x400000000) & ~0x3fff
                            == phystokv(topOfKernelData) & ~0x3fff
    alloc_static_end  (VA) = 0x417fe0000
    alloc_static_hardcap (VA) = alloc_static_base + 64 MB
    alloc_heap_base   (VA) = align_up(early_heap, 0x4000)
    alloc_heap_end    (VA) = align_up(phystokv(physBase) + memSize, 0x4000) - 1 MB
  The free physical pages live in the heap region; PongoOS's own
  text/data and the static region are WIRED.

Free-list model (mm.c:619, 642, 697-711):
    ppage_list[i] == PAGE_FREE (0)  <=>  page i is on the free list
    free pages form a linked list via pa_head; each free page's first
    two words hold next/prev physical addresses.

There is NO safe fixed-address reservation primitive in stock PongoOS:
    phys_force_free() is unsafe on arbitrary ranges after init, and
    mark_phys_wired() panics if any page in the range is not FREE.

Because physBase, memSize, topOfKernelData and the framebuffer base are
RUNTIME values, every absolute address derived from them is marked
[HYPOTHETICAL] below.
"""

import sys

# ---- Constants from PongoOS ----
K_CACHEABLE_VIEW = 0x400000000
PHYS_VIEW_BASE = 0x800000000      # the "device" view base used in phystokv
PONGO_TEXT_VA = 0x100000000
FRAMEBUFFER_VA = 0xfb0000000
PAGE_SIZE = 0x4000
ALLOC_STATIC_END_VA = 0x417fe0000
STATIC_HARDCAP_MB = 64
HEAP_RESERVE_MB = 1

# ---- Runtime values (defaults for a typical A12 / iPhone 6-class device) ----
# These MUST be read from gBootArgs on the real device.
PHYS_BASE = 0x800000000           # gBootArgs->physBase   [RUNTIME]
MEM_SIZE = 0x40000000             # gBootArgs->memSize    [RUNTIME] (1 GB example)
TOP_OF_KERNEL_DATA = 0x800400000  # gBootArgs->topOfKernelData [RUNTIME] (physical)

# ---- MyOS M2 kernel load plan (canonical format) ----
# load_addr is a PHYSICAL address, chosen at runtime by the loader to sit
# above topOfKernelData and inside the free heap region.
MYOS_LOAD_ADDR = 0x801000000      # [RUNTIME] example: 16 MB above physBase
MYOS_IMAGE_SIZE = 0xC000
MYOS_BSS_SIZE = 0x2000

def phystokv(pa):
    """Physical address -> cacheable virtual address (mm.c:893)."""
    return pa - PHYS_VIEW_BASE + K_CACHEABLE_VIEW

def vatophys(va):
    """Cacheable virtual address -> physical address (mm.c:901)."""
    return va - K_CACHEABLE_VIEW + PHYS_VIEW_BASE

def align_up(x, a):
    return (x + a - 1) & ~(a - 1)

def page_index(pa):
    """Physical address -> page index in ppage_list[]."""
    return (pa - PHYS_BASE) // PAGE_SIZE

def simulate():
    print("MyOS M2 Memory-Map Simulation (HYPOTHETICAL - runtime values unverified)")
    print("=" * 72)
    print(f"  physBase (gBootArgs->physBase)      = 0x{PHYS_BASE:012x}  [RUNTIME]")
    print(f"  memSize  (gBootArgs->memSize)       = 0x{MEM_SIZE:012x}  [RUNTIME]")
    print(f"  topOfKernelData (physical)          = 0x{TOP_OF_KERNEL_DATA:012x}  [RUNTIME]")
    print()

    dram_end = PHYS_BASE + MEM_SIZE
    print("Physical DRAM:")
    print(f"  DRAM base  = 0x{PHYS_BASE:012x}")
    print(f"  DRAM end   = 0x{dram_end:012x}")
    print(f"  DRAM size  = 0x{MEM_SIZE:012x}")
    print(f"  page count = {MEM_SIZE // PAGE_SIZE}  (16 KB pages)")
    print()

    # Allocator regions (from mm.c alloc_init)
    alloc_static_base_va = (TOP_OF_KERNEL_DATA - PHYS_VIEW_BASE + K_CACHEABLE_VIEW) & ~0x3fff
    alloc_static_hardcap_va = alloc_static_base_va + STATIC_HARDCAP_MB * 0x100000
    alloc_static_end_va = min(ALLOC_STATIC_END_VA, alloc_static_hardcap_va)
    alloc_heap_end_va = align_up(phystokv(PHYS_BASE) + MEM_SIZE, PAGE_SIZE) - HEAP_RESERVE_MB * 0x100000

    print("PongoOS allocator regions (virtual addresses, cacheable view):")
    print(f"  alloc_static_base  = 0x{alloc_static_base_va:012x}  (PA 0x{vatophys(alloc_static_base_va):012x})")
    print(f"  alloc_static_end   = 0x{alloc_static_end_va:012x}  (PA 0x{vatophys(alloc_static_end_va):012x})")
    print(f"  alloc_static_hardcap = 0x{alloc_static_hardcap_va:012x}")
    print(f"  alloc_heap_end     = 0x{alloc_heap_end_va:012x}  (PA 0x{vatophys(alloc_heap_end_va):012x})")
    print(f"  (alloc_heap_base depends on early_heap [RUNTIME])")
    print()

    # Free physical memory available for alloc_phys / alloc_contig
    # (heap region, minus the 1 MB reserved at the top)
    free_pa_start = TOP_OF_KERNEL_DATA   # approximate; ppage_list also carved out
    free_pa_end = vatophys(alloc_heap_end_va)
    print("Free physical memory (approximate, for alloc_phys/alloc_contig):")
    print(f"  free PA start ~ 0x{free_pa_start:012x}  (above topOfKernelData)")
    print(f"  free PA end   ~ 0x{free_pa_end:012x}  (DRAM end - 1 MB)")
    print()

    # MyOS M2 kernel placement
    myos_va = phystokv(MYOS_LOAD_ADDR)
    myos_end_pa = MYOS_LOAD_ADDR + MYOS_IMAGE_SIZE + MYOS_BSS_SIZE
    myos_end_va = phystokv(myos_end_pa)
    print("MyOS M2 kernel load plan:")
    print(f"  load_addr (PA)     = 0x{MYOS_LOAD_ADDR:012x}")
    print(f"  load_addr (VA)     = 0x{myos_va:012x}  [HYPOTHETICAL]")
    print(f"  image_size         = 0x{MYOS_IMAGE_SIZE:x}")
    print(f"  bss_size           = 0x{MYOS_BSS_SIZE:x}")
    print(f"  end (PA)           = 0x{myos_end_pa:012x}")
    print(f"  end (VA)           = 0x{myos_end_va:012x}  [HYPOTHETICAL]")
    print(f"  first page index   = {page_index(MYOS_LOAD_ADDR)}")
    print(f"  last page index    = {page_index(myos_end_pa - 1)}")
    print()

    # Sanity checks
    print("Sanity checks:")
    in_dram = PHYS_BASE <= MYOS_LOAD_ADDR and myos_end_pa <= dram_end
    print(f"  load range within DRAM:          {'YES' if in_dram else 'NO'}")
    aligned = (MYOS_LOAD_ADDR & (PAGE_SIZE - 1)) == 0
    print(f"  load_addr page-aligned:          {'YES' if aligned else 'NO'}")
    above_pongo = MYOS_LOAD_ADDR >= TOP_OF_KERNEL_DATA
    print(f"  load_addr above PongoOS data:    {'YES' if above_pongo else 'NO (CHECK!)'}")
    in_free = free_pa_start <= MYOS_LOAD_ADDR and myos_end_pa <= free_pa_end
    print(f"  load range within free heap:     {'YES' if in_free else 'NO (CHECK!)'}")
    print()

    # Free-list model
    print("Free-list model (ppage_list[] / pa_head):")
    print("  ppage_list[i] == PAGE_FREE (0)  <=>  page i is on the free list")
    print("  free page node layout:  word0 = next PA,  word1 = prev PA")
    print("  To reserve the MyOS range WITHOUT modifying PongoOS is NOT")
    print("  possible with stock primitives: phys_force_free() is unsafe on")
    print("  arbitrary ranges after init, and mark_phys_wired() panics if")
    print("  any page in the range is not FREE. A future PongoOS loader")
    print("  must pop the pages from the free list directly (walk pa_head,")
    print("  unlink the nodes, set ppage_list[i] = PAGE_WIRED).")
    print()

    ok = in_dram and aligned and above_pongo and in_free
    if not ok:
        print("RESULT: LOAD PLAN INVALID for the assumed runtime values.")
        return 1
    print("RESULT: LOAD PLAN VALID for the assumed runtime values (HYPOTHETICAL).")
    return 0

if __name__ == '__main__':
    sys.exit(simulate())