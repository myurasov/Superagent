#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""File names a folder-sync tool leaves behind, and how to tell them apart.

The checkout and the workspace may sit in a folder that a sync tool carries
between machines (`rules/concurrent-sessions.md`). Two kinds of file then
appear that are not the user's data and must never be read as such:

  conflict copy   `<stem>.sync-conflict-<YYYYMMDD>-<HHMMSS>-<device>.<ext>`,
                  the losing version when two machines changed one file before
                  a sync. `tools/heal.py` merges or reports these.
  sync temp file  `.syncthing.<name>.tmp` / `~syncthing~<name>.tmp`, a file
                  still arriving.

Every scan of a registry, an index or a store skips both (a conflict copy of a
watcher ref must not become a second watcher). This module is a leaf: pure
name tests, no imports from other tools, so any tool can use it.

Python:
    from superagent.tools.syncnames import is_sync_artifact, conflict_original
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

#: `.sync-conflict-<date>-<time>-<device>` inside a file name, before the extension.
CONFLICT_RE = re.compile(r"\.sync-conflict-(\d{8})-(\d{6})-([A-Za-z0-9]+)(?=\.|$)")
_TEMP_PREFIXES = (".syncthing.", "~syncthing~")
_TEMP_SUFFIX = ".tmp"
#: Folders a sync tool owns at the root of a synced folder; never walked into.
SYNC_DIRS = frozenset({".stversions", ".stfolder"})


def _name(path: str | Path) -> str:
    return path.name if isinstance(path, Path) else Path(path).name


def is_conflict_copy(path: str | Path) -> bool:
    """True for a sync conflict copy (`todo.sync-conflict-20261121-153000-ABCDEFG.yaml`)."""
    return CONFLICT_RE.search(_name(path)) is not None


def is_sync_temp(path: str | Path) -> bool:
    """True for a file the sync tool is still writing (`.syncthing.<name>.tmp`)."""
    name = _name(path)
    return name.endswith(_TEMP_SUFFIX) and name.startswith(_TEMP_PREFIXES)


def is_sync_artifact(path: str | Path) -> bool:
    """True for anything the sync tool left behind: a conflict copy or a temp file."""
    return is_conflict_copy(path) or is_sync_temp(path)


def in_sync_dir(path: Path) -> bool:
    """True when any folder on the path is one the sync tool owns (`.stversions/`)."""
    return any(part in SYNC_DIRS for part in path.parts)


def conflict_original(path: Path) -> Path | None:
    """The file a conflict copy is a version of; None when `path` is not a conflict copy."""
    if not is_conflict_copy(path):
        return None
    return path.with_name(CONFLICT_RE.sub("", path.name, count=1))


def conflict_info(path: str | Path) -> dict[str, str] | None:
    """`{"date": "2026-11-21", "time": "15:30:00", "device": "ABCDEFG"}` of a conflict copy."""
    m = CONFLICT_RE.search(_name(path))
    if m is None:
        return None
    d, t, device = m.groups()
    return {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "time": f"{t[:2]}:{t[2:4]}:{t[4:]}", "device": device}


def real_files(paths: Iterable[Path]) -> list[Path]:
    """`paths` without sync artifacts and without anything under a sync-owned folder."""
    return [p for p in paths if not is_sync_artifact(p) and not in_sync_dir(p)]
