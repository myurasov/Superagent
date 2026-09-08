#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Sources index manager for Superagent.

Implements the derived-index contract documented in `contracts/sources.md` § 15.6.
The filesystem under `Sources/` (and per-project `Projects/<slug>/Sources/`)
is the source of truth; `_memory/sources-index.yaml` is a derived view that
this module rebuilds on demand.

`Sources/` holds three things (since 0.20.0): the user's DOCUMENTS (any
file), optional document SIDECARS `<doc>.<ext>.meta.md` carrying metadata for
the document next to them, and the WATCHER registry `Sources/Watchlist/`
(`config.preferences.watchlist.path`) whose `<name>.ref.md` files are
watcher definitions (`contracts/watchlist.md`). Every `.ref.md` is a watcher;
one found outside the registry is a "stray" indexing warning and is not
indexed. `.ref.txt` is not read by anything.

Key invariants:
  - Hand-curated fields in the index (`notes`, `tags`, `sensitive`,
    `related_*`, `last_accessed`, `read_count`) are PRESERVED across refreshes.
  - `README.md` files and dotfiles are excluded. A leftover `_cache/` folder
    (the 0.19.0 fetch cache, retired) is skipped so it never pollutes the index.
  - Refreshes are lazy: if no file under `Sources/` has an mtime newer than
    `last_filesystem_scan`, the routine no-ops.
  - Missing files are kept for one cycle with `present: false` before being
    dropped, so an accidental `rm` doesn't immediately destroy hand-curated
    notes / cross-references.
  - A sidecar is metadata FOR its document: the document gets the row (kind
    `document`, keyed by the document's path) with the sidecar's frontmatter
    applied; the sidecar itself has no row. A sidecar with no document next
    to it is an "orphan" warning and is not indexed.
  - Watchers are rows of kind `watcher`, keyed by the ref path; their
    frontmatter `watch:` mapping is lifted into the row as `watch` so
    `sources list` / `search` and `tools/world.py` see them. The mapping is
    carried across refreshes and only dropped when the file itself parses
    without a `watch:` block.

CLI:
  uv run python -m superagent.tools.sources_index refresh [--force]
  uv run python -m superagent.tools.sources_index list   [--kind document|watcher] [--category X]
  uv run python -m superagent.tools.sources_index get    <id>
  uv run python -m superagent.tools.sources_index by-path <path>
  uv run python -m superagent.tools.sources_index touch  <id>     # mark last_accessed=now, read_count++
  uv run python -m superagent.tools.sources_index remove <id>     # drop a row (does NOT delete the file)
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.validate import DEFAULT_WATCHLIST_PATH, META_SUFFIX, REF_SUFFIX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCES_DIRNAME = "Sources"
RESERVED_NAMES = {"README.md"}
# The 0.19.0 fetch cache lived at `Sources/_cache/`; retired in 0.20.0. A
# leftover folder is skipped so its raw payloads never become "documents".
LEGACY_CACHE_DIRNAME = "_cache"
# Row kinds. `document` = a user file (with or without a `.meta.md` sidecar);
# `watcher` = a `.ref.md` in the registry (contracts/watchlist.md).
KIND_DOCUMENT = "document"
KIND_WATCHER = "watcher"
ROW_KINDS = (KIND_DOCUMENT, KIND_WATCHER)
# Internal marker for a scanned path that yields no row of its own (a sidecar
# whose document carries the metadata, a stray ref, an orphan sidecar).
_SKIP = "_skip"

Warn = Callable[[str], None]

# Field names that the user may hand-curate; refresh MUST preserve them.
# `title` is derived from the filename by default (`title_from_filename`) but
# is preserved when the user has hand-edited it — the deriver collapses
# meaningful punctuation (e.g. "W-2" -> "W 2"), so the override is essential.
PRESERVED_FIELDS = (
    "title",
    "notes", "tags", "sensitive",
    "related_domain", "related_project", "related_asset", "related_account",
    "last_accessed", "read_count", "added",
)

# Derived from the ref's frontmatter, not hand-curated in the index -- but
# never overwritten with "nothing" by a refresh that could not parse the file.
# See `merge_existing`.
WATCH_FIELD = "watch"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_iso_micro() -> str:
    """Return current local time as ISO 8601 with microsecond precision.

    Used for `last_filesystem_scan` so the lazy mtime comparison doesn't
    misfire when a file is written within the same wall-clock second as the
    refresh that follows it.
    """
    return dt.datetime.now().astimezone().isoformat(timespec="microseconds")


def parse_iso(value: str | None) -> dt.datetime | None:
    """Parse an ISO 8601 string; return None on failure."""
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML; return empty dict on failure or missing."""
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def load_config(workspace: Path) -> dict[str, Any]:
    """Load the `_memory/config.yaml` preferences this module reads, with defaults.

    `preferences.sources.auto_refresh_index` (read-side hint for skills) and
    `preferences.watchlist.path` (where the registry lives, so its `.ref.md`
    files index as watchers rather than strays).
    """
    cfg = load_yaml(workspace / "_memory" / "config.yaml")
    prefs = cfg.get("preferences") or {}
    sources = prefs.get("sources") or {} if isinstance(prefs, dict) else {}
    watchlist = prefs.get("watchlist") or {} if isinstance(prefs, dict) else {}
    registry = DEFAULT_WATCHLIST_PATH
    if isinstance(watchlist, dict) and isinstance(watchlist.get("path"), str) and watchlist["path"].strip():
        registry = watchlist["path"].strip()
    return {
        "auto_refresh_index": bool(sources.get("auto_refresh_index", True))
        if isinstance(sources, dict) else True,
        "watchlist_path": registry,
    }


def registry_dir(workspace: Path, config: dict[str, Any] | None = None) -> Path:
    """The watcher registry folder (`config.preferences.watchlist.path`, default `Sources/Watchlist`)."""
    config = config or load_config(workspace)
    p = Path(config["watchlist_path"]).expanduser()
    return p if p.is_absolute() else workspace / p


# ---------------------------------------------------------------------------
# Path / id helpers
# ---------------------------------------------------------------------------

def workspace_relative(workspace: Path, path: Path) -> str:
    """Return `path` expressed relative to the workspace root, POSIX-style."""
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def id_for_path(rel_path: str) -> str:
    """Compute the canonical row id for a workspace-relative path."""
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:10]
    return f"src-{digest}"


def is_ref_file(path: Path) -> bool:
    """True if `path` is a watcher ref (`<stem>.ref.md`; suffix matched case-insensitively)."""
    name = path.name.lower()
    return name.endswith(REF_SUFFIX) and len(name) > len(REF_SUFFIX)


def is_meta_file(path: Path) -> bool:
    """True if `path` is a document sidecar (`<doc>.<ext>.meta.md`)."""
    name = path.name.lower()
    return name.endswith(META_SUFFIX) and len(name) > len(META_SUFFIX)


def _sidecar_stem(path: Path) -> str | None:
    """The document name a sidecar-shaped filename points at, or None.

    Accepts `.meta.md` (the 0.20.0 sidecar) AND `.ref.md` so the 0.19.0
    migration and the stray-ref warning can still ask "does this legacy
    `<doc>.ref.md` sit next to its document?".
    """
    name = path.name
    for suffix in (META_SUFFIX, REF_SUFFIX):
        if name.lower().endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def companion_document(sidecar_path: Path) -> Path | None:
    """For a sidecar, return the document it describes if the pair exists.

    Two conventions are recognized:
      Form A — `<full-doc-name>.meta.md` (e.g. `camry-title.pdf` paired with
               `camry-title.pdf.meta.md`). Stem-of-the-sidecar equals the
               document's full filename. This is the canonical form.
      Form B — `<stem>.meta.md` next to `<stem>.<ext>` (e.g. `camry-title.meta.md`
               paired with `camry-title.pdf`). Stem-of-the-sidecar matches one
               document file in the same directory by `path.stem`.
    A legacy `<doc>.ref.md` name is resolved the same way (see `_sidecar_stem`).
    """
    stem = _sidecar_stem(sidecar_path)
    if stem is None:
        return None
    parent = sidecar_path.parent

    def _is_document(p: Path) -> bool:
        return p.is_file() and not is_ref_file(p) and not is_meta_file(p)

    # Form A: `<stem>` exists as a file in the same directory.
    full_match = parent / stem
    if _is_document(full_match):
        return full_match
    # Form B: a sibling whose `.stem` equals our sidecar stem (no suffix collision).
    try:
        siblings = sorted(
            p for p in parent.iterdir()
            if p.name != sidecar_path.name and _is_document(p) and p.stem == stem
        )
    except OSError:
        return None
    return siblings[0] if siblings else None


def category_from_path(rel_path: str) -> str:
    """Heuristic: the first sub-folder under Sources/ becomes the category.

    `Sources/vehicles/camry-title.pdf` → `vehicles`
    `Sources/loose-file.md`            → `""`
    `Projects/tax-2025/Sources/return.pdf` → `taxes` heuristic miss → `""`
                                       (project rows carry related_project anyway).
    """
    parts = rel_path.split("/")
    if len(parts) >= 3 and parts[0] == SOURCES_DIRNAME:
        return parts[1] if not parts[1].startswith("_") else ""
    return ""


def title_from_filename(path: Path) -> str:
    """Pretty default title from a filename."""
    stem = _sidecar_stem(path)
    if stem is None:
        stem = path.stem
    elif is_meta_file(path):
        stem = Path(stem).stem or stem   # `manual.pdf.meta.md` -> `manual`
    return stem.replace("-", " ").replace("_", " ").strip() or path.name


def ref_stem(path: str | Path) -> str | None:
    """The ref filename minus its `.ref.md` suffix, or None if not a ref.

    For a watcher this stem LOWERCASED is the watcher id (state key +
    `watch:<id>` handle) — `tools/watchlist.py::id_from_stem`. The file on
    disk keeps the casing it was written with: `enable` writes Title_Case
    (`Home_Assistant-Hub.ref.md`); a hand-written `ha.ref.md` is respected.
    """
    p = Path(path)
    if not is_ref_file(p):
        return None
    return p.name[: -len(REF_SUFFIX)] or None


# ---------------------------------------------------------------------------
# Frontmatter parsing (watcher refs and `.meta.md` sidecars share the shape)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_canonical_ref(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Parse a markdown file's YAML frontmatter.

    Returns `(frontmatter_dict, body)` when the file opens with a `---` block
    that parses to a mapping, or `(None, raw_body)` otherwise. NEVER raises.
    """
    try:
        body = path.read_text()
    except OSError:
        return None, ""
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return None, body
    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, body
    if not isinstance(fm, dict):
        return None, body
    return fm, match.group(2).strip()


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------

def iter_source_roots(workspace: Path) -> list[Path]:
    """Yield the Sources/ root + every Projects/<slug>/Sources/ that exists."""
    roots: list[Path] = []
    primary = workspace / SOURCES_DIRNAME
    if primary.is_dir():
        roots.append(primary)
    projects = workspace / "Projects"
    if projects.is_dir():
        for proj in sorted(projects.iterdir()):
            sub = proj / SOURCES_DIRNAME
            if sub.is_dir():
                roots.append(sub)
    return roots


def walk_sources(workspace: Path, config: dict[str, Any] | None = None) -> list[Path]:
    """Walk every Sources/ root under the workspace. Return file paths.

    Excludes: README.md files, dotfiles, and anything inside a leftover
    `_cache/` directory (the retired 0.19.0 fetch cache). `config` is
    accepted for call-site compatibility and unused.
    """
    del config
    files: list[Path] = []
    for root in iter_source_roots(workspace):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name in RESERVED_NAMES:
                continue
            if path.name.startswith("."):
                continue
            if any(part == LEGACY_CACHE_DIRNAME for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def max_mtime(workspace: Path, config: dict[str, Any] | None = None) -> dt.datetime | None:
    """Return the newest mtime under Sources/. None if empty."""
    del config
    newest: dt.datetime | None = None
    for root in iter_source_roots(workspace):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                m = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    return newest


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------

def _empty_row() -> dict[str, Any]:
    """A blank row with all canonical fields present."""
    return {
        "id": "",
        "kind": "",
        "title": "",
        "path": "",
        "category": "",
        "related_domain": None,
        "related_project": None,
        "related_asset": None,
        "related_account": None,
        "sensitive": False,
        "added": None,
        "last_accessed": None,
        "read_count": 0,
        "present": True,
        "normalized": False,
        "tags": [],
        "notes": "",
    }


def _project_slug_from_path(rel_path: str) -> str | None:
    """If `rel_path` is under `Projects/<slug>/Sources/`, return `<slug>`."""
    parts = rel_path.split("/")
    if len(parts) >= 3 and parts[0] == "Projects" and parts[2] == SOURCES_DIRNAME:
        return parts[1]
    return None


def sidecar_for(document: Path) -> Path | None:
    """The `.meta.md` sidecar describing `document`, if one exists next to it.

    Form A (`<doc>.<ext>.meta.md`) wins over Form B (`<stem>.meta.md`).
    """
    for candidate in (
        document.with_name(document.name + META_SUFFIX),   # Form A: manual.pdf.meta.md
        document.with_name(document.stem + META_SUFFIX),   # Form B: manual.meta.md
    ):
        if candidate != document and candidate.is_file():
            return candidate
    return None


def _in_registry(path: Path, registry: Path) -> bool:
    try:
        return path.parent.resolve() == registry.resolve()
    except OSError:
        return False


def build_filesystem_row(workspace: Path, path: Path, *, registry: Path | None = None,
                         warn: Warn | None = None) -> dict[str, Any]:
    """Build a fresh row from filesystem state for one path.

    - A DOCUMENT (any file that is neither a ref nor a sidecar) gets a
      `document` row; if a `.meta.md` sidecar sits next to it (`sidecar_for`)
      the sidecar's frontmatter is applied to the document's row.
    - A `.meta.md` SIDECAR has no row of its own (`kind: _skip`). One with no
      document next to it is an orphan: warned about, not indexed.
    - A `.ref.md` inside the registry is a WATCHER row (`kind: watcher`) with
      its `watch:` mapping lifted; a `.ref.md` anywhere else is a stray:
      warned about ("stray .ref.md outside the registry"), not indexed.
    """
    warn = warn or (lambda msg: None)
    registry = registry if registry is not None else registry_dir(workspace)
    rel_path = workspace_relative(workspace, path)
    row = _empty_row()
    row["path"] = rel_path
    row["id"] = id_for_path(rel_path)
    row["category"] = category_from_path(rel_path)
    row["title"] = title_from_filename(path)

    project_slug = _project_slug_from_path(rel_path)
    if project_slug:
        row["related_project"] = project_slug

    if is_meta_file(path):
        # The DOCUMENT is the source; the sidecar is metadata for it and the
        # document's row pulls it in via `sidecar_for`.
        row["kind"] = _SKIP
        if companion_document(path) is None:
            warn(f"{rel_path}: orphan sidecar — no document next to it (expected "
                 f"`<doc>.<ext>{META_SUFFIX}` beside its document); not indexed")
        return row
    if is_ref_file(path):
        if not _in_registry(path, registry):
            hint = (f"; document metadata belongs in `{path.name[: -len(REF_SUFFIX)]}{META_SUFFIX}`"
                    if companion_document(path) is not None else
                    f"; document metadata belongs in `<doc>.<ext>{META_SUFFIX}`")
            warn(f"{rel_path}: stray .ref.md outside the registry — a .ref.md is a watcher "
                 f"definition ({workspace_relative(workspace, registry)}/){hint}; not indexed")
            row["kind"] = _SKIP
            return row
        row["kind"] = KIND_WATCHER
        fm, _body = parse_canonical_ref(path)
        if fm is not None:
            row["normalized"] = True
            _apply_frontmatter(row, fm)
        else:
            row["normalized"] = False
        return row
    if _in_registry(path, registry) and path.name not in RESERVED_NAMES:
        # A watcher definition parked under the wrong name (or any stray file)
        # would otherwise become a bogus "document" row and never be watched.
        warn(f"{rel_path}: not a .ref.md — not a watcher; rename it to "
             f"`<name>{REF_SUFFIX}` (id = stem lowercased) or move it out of the "
             f"registry ({workspace_relative(workspace, registry)}/); not indexed")
        row["kind"] = _SKIP
        return row

    row["kind"] = KIND_DOCUMENT
    sidecar = sidecar_for(path)
    if sidecar is not None:
        fm, _body = parse_canonical_ref(sidecar)
        if fm is not None:
            _apply_frontmatter(row, fm)
    return row


def _apply_frontmatter(row: dict[str, Any], fm: dict[str, Any]) -> None:
    """Pull canonical fields from a parsed frontmatter (ref or sidecar) into `row`."""
    if isinstance(fm.get("title"), str) and fm["title"]:
        row["title"] = fm["title"]
    if isinstance(fm.get("category"), str) and fm["category"]:
        row["category"] = fm["category"]
    for key in ("related_domain", "related_project", "related_asset", "related_account"):
        v = fm.get(key)
        if isinstance(v, str) and v:
            row[key] = v
    if isinstance(fm.get("sensitive"), bool):
        row["sensitive"] = fm["sensitive"]
    if isinstance(fm.get("tags"), list):
        row["tags"] = list(fm["tags"])
    # Watcher configuration (contracts/watchlist.md § 2). Lifted verbatim;
    # the ref file stays the source of truth, this is a derived view of it.
    if isinstance(fm.get(WATCH_FIELD), dict):
        row[WATCH_FIELD] = copy.deepcopy(fm[WATCH_FIELD])


# ---------------------------------------------------------------------------
# Diff and merge
# ---------------------------------------------------------------------------

def merge_existing(new_row: dict[str, Any], existing_row: dict[str, Any]) -> dict[str, Any]:
    """Preserve user-curated fields from `existing_row` onto `new_row`.

    Rule: if the existing row has a non-empty user-set value for any of the
    PRESERVED_FIELDS, keep it. Otherwise take what the filesystem produced.

    `watch` (a watcher's detect config, lifted from the ref frontmatter) is
    carried over when the fresh scan produced none AND the file did not parse
    (`normalized: False`) -- a transiently broken file must not wipe the
    row. When the file parsed cleanly without a `watch:` block, the block is
    genuinely gone and the row drops it.
    """
    merged = dict(new_row)
    if (WATCH_FIELD not in merged and isinstance(existing_row.get(WATCH_FIELD), dict)
            and not new_row.get("normalized", False)):
        merged[WATCH_FIELD] = copy.deepcopy(existing_row[WATCH_FIELD])
    for field in PRESERVED_FIELDS:
        existing_val = existing_row.get(field)
        if existing_val in (None, "", [], 0, False):
            continue
        if field == "read_count" and isinstance(existing_val, int) and existing_val > 0 or field == "tags" and isinstance(existing_val, list) and existing_val or field in ("notes", "title") and isinstance(existing_val, str) and existing_val or field in ("sensitive",) and isinstance(existing_val, bool):
            merged[field] = existing_val
        elif field in ("related_domain", "related_project",
                       "related_asset", "related_account",
                       "added", "last_accessed"):
            if existing_val:
                merged[field] = existing_val
    merged["present"] = True
    return merged


def diff_and_merge(existing: list[dict[str, Any]], scanned: list[dict[str, Any]],
                   ) -> list[dict[str, Any]]:
    """Merge a fresh scan into the existing index rows.

    Strategy (4 passes):
      1. id-match: scanned rows whose id matches an existing row -> `merge_existing`
         (path unchanged; preserves curated fields). Path identity counts too: a
         scanned row whose PATH equals an existing present row's path keeps that
         row's id even when the id is not the derived `src-<sha1(path)>` (the
         row's `path` was rewritten in place, e.g. by a migration), so logs that
         cite the id keep pointing at a live row.
      2. rename-detection: for unmatched scanned + unmatched existing rows,
         pair by basename. When EXACTLY one unmatched-existing and EXACTLY one
         unmatched-scanned share the same basename, treat as a directory move:
         take the new row's id+path, transplant curated fields from the old
         row via `merge_existing`. Honors `contracts/sources.md` § 15.6:
         "Changed (path move detected by content-hash + filename match):
         update path in place, preserve everything else."
      3. add-new: remaining unmatched-scanned rows -> append as new (added: now).
      4. mark-absent: remaining unmatched-existing rows -> mark `present: false`
         IF they were `present` last cycle; drop entirely if already absent
         (one-cycle grace period so an accidental `rm` doesn't immediately
         destroy hand-curated notes).
      - Skip fully-empty placeholder rows from the template (id == "").
    """
    by_id_existing = {r.get("id"): r for r in existing if r.get("id")}
    by_id_scanned = {r.get("id"): r for r in scanned if r.get("id")}
    by_path_existing = {r.get("path"): r for r in existing
                        if r.get("id") and r.get("path") and r.get("present", True) is not False}

    merged: list[dict[str, Any]] = []
    timestamp = now_iso()
    matched_existing: set[str] = set()
    matched_scanned: set[str] = set()

    # Pass 1: id-matched rows (path unchanged), then path-matched rows whose
    # existing id differs from the derived one (path rewritten in place).
    for sid, srow in by_id_scanned.items():
        if srow.get("kind") == _SKIP:
            continue
        if sid in by_id_existing:
            merged.append(merge_existing(srow, by_id_existing[sid]))
            matched_existing.add(sid)
            matched_scanned.add(sid)
            continue
        erow = by_path_existing.get(srow.get("path"))
        if erow is not None and erow.get("id") not in matched_existing:
            keep = merge_existing(srow, erow)
            keep["id"] = erow["id"]
            merged.append(keep)
            matched_existing.add(erow["id"])
            matched_scanned.add(sid)

    # Pass 2: rename detection by basename.
    unmatched_existing_by_basename: dict[str, list[dict[str, Any]]] = {}
    for eid, erow in by_id_existing.items():
        if eid in matched_existing:
            continue
        if erow.get("present", True) is False:
            continue
        basename = Path(erow.get("path") or "").name
        if not basename:
            continue
        unmatched_existing_by_basename.setdefault(basename, []).append(erow)

    unmatched_scanned_by_basename: dict[str, list[dict[str, Any]]] = {}
    for sid, srow in by_id_scanned.items():
        if sid in matched_scanned:
            continue
        if srow.get("kind") == _SKIP:
            continue
        basename = Path(srow.get("path") or "").name
        if not basename:
            continue
        unmatched_scanned_by_basename.setdefault(basename, []).append(srow)

    renamed_existing_ids: set[str] = set()
    renamed_scanned_ids: set[str] = set()
    for basename, existing_candidates in unmatched_existing_by_basename.items():
        scanned_candidates = unmatched_scanned_by_basename.get(basename, [])
        if len(existing_candidates) == 1 and len(scanned_candidates) == 1:
            old_row = existing_candidates[0]
            new_row = scanned_candidates[0]
            merged.append(merge_existing(new_row, old_row))
            renamed_existing_ids.add(old_row.get("id"))
            renamed_scanned_ids.add(new_row.get("id"))

    # Pass 3: remaining unmatched-scanned rows -> add as new.
    for sid, srow in by_id_scanned.items():
        if srow.get("kind") == _SKIP:
            continue
        if sid in matched_scanned or sid in renamed_scanned_ids:
            continue
        if not srow.get("added"):
            srow["added"] = timestamp
        merged.append(srow)

    # Pass 4: remaining unmatched-existing rows -> mark present=false (one-cycle grace).
    for eid, erow in by_id_existing.items():
        if eid in matched_existing or eid in renamed_existing_ids:
            continue
        was_present = erow.get("present", True)
        if was_present is False:
            continue
        erow = dict(erow)
        erow["present"] = False
        merged.append(erow)

    merged.sort(key=lambda r: (r.get("path") or "", r.get("id") or ""))
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_path(workspace: Path) -> Path:
    """Path to the index file."""
    return workspace / "_memory" / "sources-index.yaml"


def load_index(workspace: Path) -> dict[str, Any]:
    """Load the index; return template-shaped default if missing."""
    data = load_yaml(index_path(workspace))
    data.setdefault("schema_version", 1)
    data.setdefault("last_filesystem_scan", None)
    data.setdefault("sources", [])
    return data


def save_index(workspace: Path, data: dict[str, Any]) -> None:
    """Save the index atomically."""
    save_yaml(index_path(workspace), data)


def needs_refresh(workspace: Path, index: dict[str, Any]) -> bool:
    """True if the filesystem has changed since `last_filesystem_scan`."""
    last_scan = parse_iso(index.get("last_filesystem_scan"))
    newest = max_mtime(workspace)
    if newest is None:
        return last_scan is None
    if last_scan is None:
        return True
    return newest > last_scan


def _warn_stderr(msg: str) -> None:
    print(f"sources_index: {msg}", file=sys.stderr)


def refresh(workspace: Path, *, force: bool = False, warn: Warn | None = None) -> dict[str, Any]:
    """Bring `sources-index.yaml` in sync with the filesystem.

    Cheap when nothing changed (one mtime walk + one yaml load + comparison).
    Indexing warnings (stray `.ref.md` outside the registry, orphan `.meta.md`
    sidecars) go to `warn` — stderr by default. Returns the resulting index dict.
    """
    index = load_index(workspace)
    if not force and not needs_refresh(workspace, index):
        return index
    warn = warn or _warn_stderr
    config = load_config(workspace)
    registry = registry_dir(workspace, config)
    files = walk_sources(workspace, config)
    scanned = [build_filesystem_row(workspace, p, registry=registry, warn=warn) for p in files]
    existing = list(index.get("sources") or [])
    existing = [r for r in existing if (r or {}).get("id")]
    merged = diff_and_merge(existing, scanned)
    index["sources"] = merged
    index["last_filesystem_scan"] = now_iso_micro()
    save_index(workspace, index)
    return index


def get_by_id(workspace: Path, ref_id: str, *, refresh_first: bool = True,
              ) -> dict[str, Any] | None:
    """Look up one row by id. Refreshes the index first by default."""
    index = refresh(workspace) if refresh_first else load_index(workspace)
    for row in index.get("sources") or []:
        if row.get("id") == ref_id:
            return row
    return None


def get_by_path(workspace: Path, path: str | Path, *, refresh_first: bool = True,
                ) -> dict[str, Any] | None:
    """Look up one row by workspace-relative path."""
    if isinstance(path, Path):
        rel = workspace_relative(workspace, path)
    else:
        rel = path
    index = refresh(workspace) if refresh_first else load_index(workspace)
    for row in index.get("sources") or []:
        if row.get("path") == rel:
            return row
    return None


def update_row(workspace: Path, ref_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Update fields on one row. Returns the updated row or None if not found."""
    index = load_index(workspace)
    rows = index.get("sources") or []
    for i, row in enumerate(rows):
        if row.get("id") == ref_id:
            for k, v in fields.items():
                row[k] = v
            rows[i] = row
            index["sources"] = rows
            save_index(workspace, index)
            return row
    return None


def mark_accessed(workspace: Path, ref_id: str) -> dict[str, Any] | None:
    """Bump `last_accessed = now`, increment `read_count`."""
    index = load_index(workspace)
    rows = index.get("sources") or []
    for i, row in enumerate(rows):
        if row.get("id") == ref_id:
            row["last_accessed"] = now_iso()
            row["read_count"] = int(row.get("read_count", 0) or 0) + 1
            rows[i] = row
            index["sources"] = rows
            save_index(workspace, index)
            return row
    return None


def remove_row(workspace: Path, ref_id: str) -> bool:
    """Drop a row from the index. Does NOT delete the file. Returns True on success."""
    index = load_index(workspace)
    rows = index.get("sources") or []
    new_rows = [r for r in rows if r.get("id") != ref_id]
    if len(new_rows) == len(rows):
        return False
    index["sources"] = new_rows
    save_index(workspace, index)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(prog="sources_index")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="Walk Sources/ and rebuild the index.")
    r.add_argument("--force", action="store_true",
                   help="Ignore mtime check; always rescan.")

    L = sub.add_parser("list", help="List all rows.")
    L.add_argument("--kind", choices=list(ROW_KINDS), default=None)
    L.add_argument("--category", type=str, default=None)
    L.add_argument("--present-only", action="store_true",
                   help="Skip rows whose file disappeared.")

    g = sub.add_parser("get", help="Print one row by id (JSON).")
    g.add_argument("ref_id", type=str)

    bp = sub.add_parser("by-path", help="Print one row by workspace-relative path.")
    bp.add_argument("path", type=str)

    t = sub.add_parser("touch", help="Mark a row as accessed.")
    t.add_argument("ref_id", type=str)

    rm = sub.add_parser("remove", help="Drop a row (does NOT delete the file).")
    rm.add_argument("ref_id", type=str)

    return parser.parse_args(argv)


def _resolve_workspace(arg: Path | None) -> Path:
    """Resolve --workspace flag to a concrete path."""
    if arg is not None:
        return arg
    framework = Path(__file__).resolve().parent.parent
    return framework.parent / "workspace"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workspace = _resolve_workspace(args.workspace)
    if not (workspace / "_memory").exists():
        print(f"No workspace at {workspace}; run workspace_init.py first.",
              file=sys.stderr)
        return 1

    if args.cmd == "refresh":
        warnings: list[str] = []
        index = refresh(workspace, force=args.force, warn=warnings.append)
        rows = index.get("sources") or []
        present = sum(1 for r in rows if r.get("present", True))
        absent = len(rows) - present
        print(f"Index has {len(rows)} row(s); {present} present, {absent} missing.")
        print(f"Last scan: {index.get('last_filesystem_scan')}")
        for w in warnings:
            print(f"WARN  {w}")
        return 0

    if args.cmd == "list":
        index = refresh(workspace)
        rows = index.get("sources") or []
        if args.kind:
            rows = [r for r in rows if r.get("kind") == args.kind]
        if args.category:
            rows = [r for r in rows if r.get("category") == args.category]
        if args.present_only:
            rows = [r for r in rows if r.get("present", True)]
        if not rows:
            print("(no rows)")
            return 0
        print(f"{'id':<18}{'kind':<11}{'category':<14}{'path':<50}{'reads':>6}")
        print("-" * 99)
        for r in rows:
            present_marker = "" if r.get("present", True) else " [missing]"
            print(f"{r.get('id', ''):<18}{r.get('kind', ''):<11}"
                  f"{(r.get('category') or '-'):<14}"
                  f"{(r.get('path') or '')[:48]:<50}"
                  f"{r.get('read_count', 0):>6}{present_marker}")
        return 0

    if args.cmd == "get":
        row = get_by_id(workspace, args.ref_id)
        if row is None:
            print(f"No row with id {args.ref_id!r}", file=sys.stderr)
            return 1
        print(json.dumps(row, indent=2, default=str))
        return 0

    if args.cmd == "by-path":
        row = get_by_path(workspace, args.path)
        if row is None:
            print(f"No row at path {args.path!r}", file=sys.stderr)
            return 1
        print(json.dumps(row, indent=2, default=str))
        return 0

    if args.cmd == "touch":
        row = mark_accessed(workspace, args.ref_id)
        if row is None:
            print(f"No row with id {args.ref_id!r}", file=sys.stderr)
            return 1
        print(f"Touched {args.ref_id}: read_count={row['read_count']}, "
              f"last_accessed={row['last_accessed']}")
        return 0

    if args.cmd == "remove":
        ok = remove_row(workspace, args.ref_id)
        if not ok:
            print(f"No row with id {args.ref_id!r}", file=sys.stderr)
            return 1
        print(f"Removed {args.ref_id} from index (file untouched).")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
