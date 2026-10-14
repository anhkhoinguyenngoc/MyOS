# MyOS M-P2.30 — Offline PongoOS Artifact Selection

Date: 2026-10-13
Run on: Arch Linux main PC (`/home/khoi/Projects/myos`), offline
Physical iPhone connection: **not used, not required, not claimed**
Preserved artifacts: `artifacts/m-p2.26-cb83dd83/`
Resolver: `tools/host/pongo_addr_resolve.py`

**Scope.** This milestone adds a *backward-compatible* way for the existing
offline host tooling to select and validate an explicit PongoOS Mach-O — in
particular the preserved M-P2.26 artifact — **without replacing existing
binaries or changing their hashes**. It is an offline tooling change only: no
payload is executed and no device is contacted.

**Hardware compatibility remains UNKNOWN.** Selecting and parsing a Mach-O is
not evidence that any iPhone booted.

## 1. What changed

`tools/host/pongo_addr_resolve.py` gained one argument, `--artifact-dir DIR`,
mutually exclusive with the pre-existing `--macho PATH`. Both select an
artifact explicitly; neither overrides the other and neither falls back to the
default when the selection is wrong.

| Selection | Behavior |
| --- | --- |
| *(no flag)* | Unchanged default: `<repo>/pongoos-src/build/Pongo` |
| `--macho PATH` | Parse exactly `PATH`; error (exit 1) if unreadable/invalid |
| `--artifact-dir DIR` | Parse `DIR/Pongo` or `DIR/artifacts/Pongo`; error (exit 1) if absent |
| `--macho` **and** `--artifact-dir` | Usage error (exit 2) |

Exit codes are documented in the resolver docstring: `0` success, `1`
artifact/parse error, `2` usage error, `3` missing runtime datum.

Selection only **parses** the artifact. The resolver never executes code and
never communicates with a device.

## 2. Exact commands

Validate the historical default (the local PongoOS checkout build) — unchanged:

```sh
python3 tools/host/pongo_addr_resolve.py --json
python3 tools/host/pongo_addr_resolve.py --symbol _pongo_entry
make pongo-addr-check
```

Validate the **preserved** M-P2.26 artifact explicitly (no default touched):

```sh
python3 tools/host/pongo_addr_resolve.py --artifact-dir artifacts/m-p2.26-cb83dd83 --json
python3 tools/host/pongo_addr_resolve.py --macho artifacts/m-p2.26-cb83dd83/artifacts/Pongo \
        --symbol _regs_cmd --json
make pongo-artifact-check PONGO_ARTIFACT_DIR=artifacts/m-p2.26-cb83dd83
```

Failure paths are explicit (never a silent fallback to the default):

```sh
python3 tools/host/pongo_addr_resolve.py --macho /nonexistent/Pongo          # exit 1
python3 tools/host/pongo_addr_resolve.py --artifact-dir /nonexistent/dir     # exit 1
python3 tools/host/pongo_addr_resolve.py --macho x --artifact-dir y          # exit 2
```

## 3. Tests

New suite `tests/test_pongo_artifact_select.py` (14 tests, all pass) covers:

- the historical default path is unchanged (no flag → `pongoos-src/build/Pongo`);
- explicit `--macho` selection, and that a missing `--macho` does **not** fall
  back to a valid default;
- explicit `--artifact-dir` selection for both preserved layouts
  (`DIR/Pongo` and `DIR/artifacts/Pongo`);
- invalid / missing / empty selections fail with a clear message and exit 1;
- `--macho` + `--artifact-dir` together is a usage error (exit 2);
- a synthetic Mach-O makes most cases self-contained; selecting the real
  preserved artifact (and resolving `_regs_cmd`) is an integration check that
  is skipped when the artifact is absent.

Existing offline suites remain green: `tests/test_pongo_regs_command.py`
(23), `tests/check_pongo_probe.py` (ALL PASS), `tests/test_readback_design.py`
(20), `tests/test_pongo_addr_resolve.py` (18).

## 4. Non-goals and limitations

- No build, rebuild, or relink; no Docker; no package installation.
- The preserved artifacts and the original `pongoos-src/build/` outputs are
  left byte-identical (see the milestone report for hashes/timestamps).
- `artifacts/` is now listed in `.gitignore` so preserved binaries, manifests,
  and logs are never committed accidentally.
- Address-level differences between the preserved and older artifacts remain
  **confounded by differing build toolchains** and are **not** attributed to
  the `regs` change (from M-P2.29).
- No claim of a successful device boot.
