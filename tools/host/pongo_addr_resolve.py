#!/usr/bin/env python3
"""
MyOS M-P2.4 -- PongoOS symbol / address resolver (host-side, stdlib only).

Resolves *link-time* symbol addresses from the pinned, non-stripped PongoOS
Mach-O artifact and reproduces the PongoOS address model (image-relative,
cacheable VA, physical alias, runtime-slid) from the pinned source.

It deliberately does NOT invent a runtime address. In particular the raw
`bootr` upload buffer (`loader_xfer_recv_data`) is a heap allocation
(`alloc_contig(UPLOADSZ)`); its runtime address is *not* a symbol and is
NOT statically resolvable. Without an explicit runtime datum the tool
reports `UNRESOLVED_NEEDS_RUNTIME_DATUM` and exits non-zero.

Evidence anchors (pinned checkout):
  pongoos-src/src/kernel/pongo.h:341-345   kCacheableView = 0x400000000
  pongoos-src/src/kernel/mm.c:893-895      phystokv(pa) = pa - 0x800000000 + kCacheableView
  pongoos-src/src/kernel/mm.c:896-902      vatophys_static(va) = va - kCacheableView + 0x800000000
  pongoos-src/src/kernel/mm.c:887-889      alloc_contig(size) = phystokv(alloc_phys(size))
  pongoos-src/src/kernel/mm.c:303-327      pongo_base = &start ; gPongoSlide = 0x100000000 - pongo_base
  pongoos-src/src/kernel/entry.c:309-312   raw jump target = loader_xfer_recv_data - kCacheableView + 0x800000000
  pongoos-src/src/shell/usbloader.c:32,35  UPLOADSZ = 1 MiB ; uint8_t * loader_xfer_recv_data
  pongoos-src/src/shell/usbloader.c:94-99  usbloader_init(): loader_xfer_recv_data = alloc_contig(UPLOADSZ)

Usage:
  python3 tools/host/pongo_addr_resolve.py [--macho PATH] [--repo ROOT]
  python3 tools/host/pongo_addr_resolve.py --artifact-dir artifacts/m-p2.26-cb83dd83
  python3 tools/host/pongo_addr_resolve.py --symbol _loader_xfer_recv_data
  python3 tools/host/pongo_addr_resolve.py --symbol _pongo_entry --slide 0x1000
  python3 tools/host/pongo_addr_resolve.py --symbol _pongo_entry --pongo-base 0x100000000
  python3 tools/host/pongo_addr_resolve.py --buffer-pa  0x812340000
  python3 tools/host/pongo_addr_resolve.py --buffer-va  0x412340000
  python3 tools/host/pongo_addr_resolve.py --verify-source
  python3 tools/host/pongo_addr_resolve.py --json

Artifact selection (M-P2.30):
  The default artifact path is unchanged: with neither flag the resolver uses
  <repo>/pongoos-src/build/Pongo. Two explicit, mutually-exclusive selectors
  exist and never fall back to the default:
    --macho PATH       use exactly this PongoOS Mach-O file
    --artifact-dir DIR use the Pongo Mach-O inside a preserved artifact
                       directory (DIR/Pongo or DIR/artifacts/Pongo)
  Selecting an artifact only PARSES it; the resolver never executes code and
  never touches a device.

Exit codes:
  0  success (all requested facts resolved with the evidence given)
  1  artifact/parse error
  2  usage error
  3  requested a runtime address without the required runtime datum
"""

import argparse
import json
import os
import re
import struct
import sys

# --------------------------------------------------------------------------
# Evidence-linked constants (see module docstring for exact source lines)
# --------------------------------------------------------------------------
IMAGE_BASE_DEFAULT = 0x100000000          # Mach-O __TEXT vmaddr; Makefile image_base
K_CACHEABLE_VIEW_DEFAULT = 0x400000000    # pongo.h:344 (default build)
PHYS_VIEW_BASE = 0x800000000              # mm.c:318-319 physical identity view
UPLOADSZ = 1 << 20                        # usbloader.c:32 (1 MiB)

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_UNIXTHREAD = 0x05

# Symbols that hold a *pointer value* rather than being the object itself.
POINTER_VARS = {
    "_loader_xfer_recv_data": (
        "pointer variable: holds the upload-buffer CACHEABLE VA at runtime; "
        "its own address is static, its VALUE is runtime-only"
    ),
}

# Symbols this audit cares about (exact names, Mach-O leading underscore).
KEY_SYMBOLS = [
    "_pongo_entry",
    "_usbloader_init",
    "_loader_xfer_recv_data",
    "_loader_xfer_recv_count",
    "_loader_xfer_recv_size",
    "_loader_xfer_size",
    "_resize_loader_xfer_data",
    "_reallocate_loader_xfer_data",
    "_alloc_contig",
    "_alloc_phys",
    "_free_contig",
    "_free_phys",
    "_phystokv",
    "_vatophys",
    "_vatophys_static",
    "_alloc_static",
    "_alloc_static_base",
    "_alloc_static_current",
    "_gPongoSlide",
    "_gBootArgs",
    "_gEntryPoint",
    "_gBootFlag",
    "_ram_phys_off",
    "_g_phys_off",
    "_early_heap_base",
    "_ppage_list",
    "_ppages",
]


# --------------------------------------------------------------------------
# Artifact selection (paths only; nothing here executes code)
# --------------------------------------------------------------------------
def default_macho_path(repo):
    """The historical default artifact: the local PongoOS checkout build.

    Kept exactly where it has always been so existing callers (and the
    pinned-offset tests) see no change.
    """
    return os.path.join(repo, "pongoos-src", "build", "Pongo")


def locate_artifact_macho(dirpath):
    """Find the Pongo Mach-O inside a preserved artifact directory.

    Accepts either the top-level versioned directory (which contains
    ``artifacts/Pongo``) or the inner ``artifacts`` directory itself.
    Returns an absolute path, or None if neither layout matches.
    """
    if not dirpath:
        return None
    for rel in ("Pongo", os.path.join("artifacts", "Pongo")):
        cand = os.path.join(dirpath, rel)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


# --------------------------------------------------------------------------
# Address model
# --------------------------------------------------------------------------
def phystokv(pa, kcacheable=K_CACHEABLE_VIEW_DEFAULT):
    """mm.c:893-895 -> cacheable VA of a physical address."""
    return pa - PHYS_VIEW_BASE + kcacheable


def vatophys_static(va, kcacheable=K_CACHEABLE_VIEW_DEFAULT):
    """mm.c:896-902 -> physical alias of a kCacheableView VA."""
    return va - kcacheable + PHYS_VIEW_BASE


def handoff_alias(buffer_va, kcacheable=K_CACHEABLE_VIEW_DEFAULT):
    """entry.c:309-312 -> the raw `bootr` jump target (x30). Equals buffer PA."""
    return buffer_va - kcacheable + PHYS_VIEW_BASE


def runtime_va(link_va, g_pongo_slide):
    """mm.c:327 + entry rebase -> runtime VA = link VA - gPongoSlide."""
    return link_va - g_pongo_slide


def runtime_va_from_base(link_va, pongo_base):
    """Equivalent form using the runtime image base pongo_base = &start."""
    return link_va - IMAGE_BASE_DEFAULT + pongo_base


# --------------------------------------------------------------------------
# Minimal Mach-O 64 parser (little-endian, the only form Pongo uses)
# --------------------------------------------------------------------------
class MachOError(Exception):
    pass


def parse_macho(path):
    try:
        with open(path, "rb") as fh:
            d = fh.read()
    except OSError as e:
        raise MachOError("cannot read %s: %s" % (path, e))
    if len(d) < 32:
        raise MachOError("too short to be Mach-O 64")
    magic = struct.unpack_from("<I", d, 0)[0]
    if magic != MH_MAGIC_64:
        raise MachOError("not MH_MAGIC_64 LE (got 0x%08x)" % magic)
    cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _res = \
        struct.unpack_from("<iiIIIII", d, 4)

    segments, sections = [], []
    symoff = nsyms = stroff = strsize = None
    entry = None

    off = 32
    for _ in range(ncmds):
        if off + 8 > len(d):
            raise MachOError("load command past EOF")
        cmd, cmdsize = struct.unpack_from("<II", d, off)
        if cmdsize < 8:
            raise MachOError("bad load command size")
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", d, off + 8)
        elif cmd == LC_SEGMENT_64:
            segname = d[off + 8:off + 24].split(b"\0")[0].decode("latin1")
            vmaddr, vmsize, fileoff, filesize = \
                struct.unpack_from("<QQQQ", d, off + 24)
            nsects = struct.unpack_from("<I", d, off + 64)[0]
            segments.append({
                "name": segname, "vmaddr": vmaddr, "vmsize": vmsize,
                "fileoff": fileoff, "filesize": filesize, "nsects": nsects,
            })
            so = off + 72
            for _s in range(nsects):
                sectname = d[so:so + 16].split(b"\0")[0].decode("latin1")
                seg = d[so + 16:so + 32].split(b"\0")[0].decode("latin1")
                addr, size, offset, align = struct.unpack_from("<QQII", d, so + 32)
                sections.append({
                    "seg": seg, "name": sectname, "addr": addr,
                    "size": size, "offset": offset, "align": align,
                })
                so += 80
        elif cmd == LC_UNIXTHREAD:
            # arm_thread_state64_t: x[29], fp, lr, sp, pc, cpsr, pad
            st = d[off + 16:]
            if len(st) >= 29 * 8 + 32:
                entry = struct.unpack_from("<Q", st, 29 * 8 + 24)[0]
        off += cmdsize

    if symoff is None:
        raise MachOError("no LC_SYMTAB (artifact is stripped)")
    if stroff is None or stroff + (strsize or 0) > len(d):
        raise MachOError("bad string table bounds")

    def cstr(n):
        if n >= len(d) - stroff:
            return ""
        end = d.find(b"\0", stroff + n)
        if end < 0:
            return ""
        return d[stroff + n:end].decode("latin1")

    symbols = {}
    for i in range(nsyms):
        o = symoff + i * 16
        if o + 16 > len(d):
            break
        n_strx, n_type, n_sect, _n_desc, n_value = \
            struct.unpack_from("<IBBHQ", d, o)
        name = cstr(n_strx)
        symbols.setdefault(name, []).append({
            "value": n_value, "type": n_type, "sect": n_sect,
        })

    # image base = lowest segment vmaddr (__TEXT)
    image_base = min((s["vmaddr"] for s in segments), default=None)
    return {
        "path": os.path.abspath(path),
        "cputype": cputype, "filetype": filetype, "ncmds": ncmds,
        "image_base": image_base, "entry": entry,
        "segments": segments, "sections": sections, "symbols": symbols,
    }


# --------------------------------------------------------------------------
# Source-assumption verification (ties the model to the pinned source)
# --------------------------------------------------------------------------
SOURCE_ASSUMPTIONS = [
    ("pongoos-src/src/kernel/pongo.h",
     r"define\s+kCacheableView\s+0x400000000ULL"),
    ("pongoos-src/src/kernel/mm.c",
     r"paddr\s*-\s*0x800000000\s*\+\s*kCacheableView"),
    ("pongoos-src/src/kernel/mm.c",
     r"-\s*kCacheableView\s*\+\s*0x800000000"),
    ("pongoos-src/src/kernel/mm.c",
     r"gPongoSlide\s*=\s*0x100000000ULL\s*-\s*pongo_base"),
    ("pongoos-src/src/kernel/entry.c",
     r"loader_xfer_recv_data\)\s*-\s*kCacheableView\s*\+\s*0x800000000"),
    ("pongoos-src/src/shell/usbloader.c",
     r"#define\s+UPLOADSZ\s+\(1024\s*\*\s*1024\)"),
    ("pongoos-src/src/shell/usbloader.c",
     r"uint8_t\s*\*\s*loader_xfer_recv_data\s*;"),
    ("pongoos-src/src/shell/usbloader.c",
     r"loader_xfer_recv_data\s*=\s*alloc_contig\(UPLOADSZ\)"),
]


def verify_source(repo):
    """Return (ok, rows) where rows = [(path, pattern, found)]."""
    rows = []
    ok = True
    for rel, pat in SOURCE_ASSUMPTIONS:
        p = os.path.join(repo, rel)
        found = False
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                found = re.search(pat, fh.read()) is not None
        if not found:
            ok = False
        rows.append((rel, pat, found))
    return ok, rows


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def fmt(x):
    return "0x%016x" % x if x is not None else "(none)"


def section_of(macho, addr):
    for s in macho["sections"]:
        if s["addr"] <= addr < s["addr"] + max(s["size"], 1):
            return s
    return None


def resolve_symbol(macho, name, slide=None, pongo_base=None):
    if name not in macho["symbols"]:
        return None
    ent = macho["symbols"][name][0]
    link = ent["value"]
    sec = section_of(macho, link)
    out = {
        "name": name,
        "link_va": link,
        "image_relative": link - (macho["image_base"] or 0),
        "section": ("%s,%s" % (sec["seg"], sec["name"])) if sec else None,
        "pointer_var": POINTER_VARS.get(name),
        "runtime_va": None,
    }
    if slide is not None:
        out["runtime_va"] = runtime_va(link, slide)
    elif pongo_base is not None:
        out["runtime_va"] = runtime_va_from_base(link, pongo_base)
    if out["runtime_va"] is not None:
        out["runtime_physical_alias"] = vatophys_static(out["runtime_va"])
    return out


def resolve_buffer(buffer_pa=None, buffer_va=None,
                   kcacheable=K_CACHEABLE_VIEW_DEFAULT):
    """Resolve all representations of the upload buffer from ONE runtime datum.

    Returns a dict with 'status'. A runtime datum is mandatory: the buffer is
    heap-allocated (usbloader.c:94-99), so it can never be derived from symbols.
    """
    missing = ("upload buffer address needs a runtime datum: the runtime value "
               "of loader_xfer_recv_data (buffer cacheable VA) or the buffer PA")
    if buffer_pa is None and buffer_va is None:
        return {
            "status": "UNRESOLVED_NEEDS_RUNTIME_DATUM",
            "detail": missing,
            "static_source": "usbloader.c:94-99 (alloc_contig(UPLOADSZ))",
        }
    if buffer_pa is not None and buffer_va is not None:
        if phystokv(buffer_pa, kcacheable) != buffer_va:
            return {
                "status": "INCONSISTENT_RUNTIME_DATUM",
                "detail": "buffer PA and VA disagree under the Pongo formula",
                "pa_given": buffer_pa, "va_given": buffer_va,
                "va_from_pa": phystokv(buffer_pa, kcacheable),
            }
    if buffer_pa is None:
        buffer_pa = vatophys_static(buffer_va, kcacheable)
    if buffer_va is None:
        buffer_va = phystokv(buffer_pa, kcacheable)
    return {
        "status": "RESOLVED_FROM_RUNTIME_DATUM",
        "buffer_pa": buffer_pa,
        "buffer_va": buffer_va,
        "handoff_alias_x30": handoff_alias(buffer_va, kcacheable),
        "report_pa": buffer_pa + 0x200,
        "report_va": buffer_va + 0x200,
        "k_cacheable_view": kcacheable,
    }


def cmd_info(macho, as_json):
    key = {}
    for n in KEY_SYMBOLS:
        if n in macho["symbols"]:
            key[n] = resolve_symbol(macho, n)
        else:
            key[n] = None
    info = {
        "artifact": macho["path"],
        "image_base": fmt(macho["image_base"]),
        "entry": fmt(macho["entry"]),
        "segments": [{"name": s["name"], "vmaddr": fmt(s["vmaddr"]),
                      "vmsize": fmt(s["vmsize"])} for s in macho["segments"]],
        "symbols": key,
        "address_model_constants": {
            "IMAGE_BASE": fmt(IMAGE_BASE_DEFAULT),
            "kCacheableView": fmt(K_CACHEABLE_VIEW_DEFAULT),
            "PHYS_VIEW_BASE": fmt(PHYS_VIEW_BASE),
            "UPLOADSZ": UPLOADSZ,
        },
        "buffer_status": resolve_buffer()["status"],
    }
    if as_json:
        print(json.dumps(info, indent=2, default=str))
        return 0
    print("artifact : %s" % info["artifact"])
    print("image base: %s   entry(LC_UNIXTHREAD): %s"
          % (info["image_base"], info["entry"]))
    print("segments :")
    for s in info["segments"]:
        print("  %-8s vmaddr=%s vmsize=%s" % (s["name"], s["vmaddr"], s["vmsize"]))
    print("key symbols (link-time VM addresses):")
    for n in KEY_SYMBOLS:
        r = key[n]
        if r is None:
            print("  %-30s (absent)" % n)
        else:
            print("  %-30s VM=%s  img+0x%x  %s"
                  % (n, fmt(r["link_va"]), r["image_relative"],
                     r["section"] or "?"))
    print()
    print("upload buffer: %s" % info["buffer_status"])
    print("  %s" % resolve_buffer()["detail"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="PongoOS symbol / address resolver (offline, read-only).")
    ap.add_argument("--macho", default=None,
                    help="path to Pongo Mach-O "
                         "(default: pongoos-src/build/Pongo)")
    ap.add_argument("--artifact-dir", default=None,
                    help="preserved artifact directory containing a PongoOS "
                         "Mach-O (DIR/Pongo or DIR/artifacts/Pongo); mutually "
                         "exclusive with --macho")
    ap.add_argument("--repo", default=None, help="repository root")
    ap.add_argument("--symbol", default=None, help="resolve one symbol")
    ap.add_argument("--slide", default=None,
                    help="runtime gPongoSlide (hex), for --symbol")
    ap.add_argument("--pongo-base", default=None,
                    help="runtime &start base (hex), for --symbol")
    ap.add_argument("--buffer-pa", default=None,
                    help="runtime buffer physical address (hex)")
    ap.add_argument("--buffer-va", default=None,
                    help="runtime buffer cacheable VA (hex)")
    ap.add_argument("--k-cacheable-view", default=None,
                    help="override kCacheableView (hex); default 0x400000000")
    ap.add_argument("--verify-source", action="store_true",
                    help="verify the pinned source still matches the model")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    repo = args.repo or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    kc = int(args.k_cacheable_view, 16) if args.k_cacheable_view \
        else K_CACHEABLE_VIEW_DEFAULT

    if args.macho and args.artifact_dir:
        print("ERROR: --macho and --artifact-dir are mutually exclusive "
              "(give an explicit file or an artifact directory, not both)",
              file=sys.stderr)
        return 2

    rc = 0

    if args.verify_source:
        ok, rows = verify_source(repo)
        if args.json:
            print(json.dumps({"ok": ok, "checks": [
                {"path": p, "pattern": pat, "found": f} for p, pat, f in rows
            ]}, indent=2))
        else:
            print("source-assumption verification (repo=%s)" % repo)
            for p, pat, f in rows:
                print("  %s  %-45s  %s"
                      % ("PASS" if f else "FAIL", p, pat[:45]))
            print("RESULT: %s" % ("PASS" if ok else "FAIL"))
        rc = 0 if ok else 1
        if not (args.symbol or args.buffer_pa or args.buffer_va):
            return rc

    if args.artifact_dir:
        located = locate_artifact_macho(args.artifact_dir)
        if located is None:
            print("ERROR: no PongoOS Mach-O found in artifact directory %s "
                  "(looked for Pongo and artifacts/Pongo)"
                  % args.artifact_dir, file=sys.stderr)
            return 1
        macho_path = located
    else:
        macho_path = args.macho or default_macho_path(repo)
    if args.symbol:
        try:
            m = parse_macho(macho_path)
        except MachOError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
    else:
        m = None

    if args.symbol:
        slide = int(args.slide, 16) if args.slide else None
        pbase = int(args.pongo_base, 16) if args.pongo_base else None
        r = resolve_symbol(m, args.symbol, slide=slide, pongo_base=pbase)
        if r is None:
            print("ERROR: symbol %s not found in %s"
                  % (args.symbol, macho_path), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(r, indent=2, default=str))
        else:
            print("symbol          : %s" % r["name"])
            print("link-time VM    : %s" % fmt(r["link_va"]))
            print("image-relative  : 0x%x" % r["image_relative"])
            print("section         : %s" % (r["section"] or "(none)"))
            if r["pointer_var"]:
                print("semantics       : %s" % r["pointer_var"])
            if r["runtime_va"] is not None:
                print("runtime VA      : %s" % fmt(r["runtime_va"]))
                print("runtime PA alias: %s" % fmt(r["runtime_physical_alias"]))
            else:
                print("runtime VA      : UNRESOLVED (need --slide or "
                      "--pongo-base; both are device/runtime data)")
        rc = 0

    if args.buffer_pa or args.buffer_va:
        bpa = int(args.buffer_pa, 16) if args.buffer_pa else None
        bva = int(args.buffer_va, 16) if args.buffer_va else None
        r = resolve_buffer(bpa, bva, kcacheable=kc)
        if args.json:
            print(json.dumps(r, indent=2, default=str))
        else:
            print("upload buffer resolution")
            for k, v in r.items():
                if isinstance(v, int):
                    print("  %-20s %s" % (k, fmt(v)))
                else:
                    print("  %-20s %s" % (k, v))
        if r["status"] != "RESOLVED_FROM_RUNTIME_DATUM":
            rc = 3 if r["status"] == "UNRESOLVED_NEEDS_RUNTIME_DATUM" else 1

    if m is not None and not args.symbol and not args.buffer_pa \
            and not args.buffer_va:
        rc = cmd_info(m, args.json)
    elif m is None and not args.verify_source and not args.symbol \
            and not args.buffer_pa and not args.buffer_va:
        try:
            m = parse_macho(macho_path)
        except MachOError as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1
        rc = cmd_info(m, args.json)

    return rc


if __name__ == "__main__":
    sys.exit(main())
