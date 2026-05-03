#!/usr/bin/env python3
"""Sources index manager for Superagent.

Implements the derived-index contract documented in `contracts/sources.md` § 15.6.
The filesystem under `Sources/` (and per-project `Projects/<slug>/Sources/`)
is the source of truth; `_memory/sources-index.yaml` is a derived view that
this module rebuilds on demand.

Key invariants:
  - Hand-curated fields in the index (`notes`, `tags`, `sensitive`,
    `related_*`, `last_accessed`, `read_count`) are PRESERVED across refreshes.
  - The cache subtree (`Sources/_cache/`, or wherever `cache_path` is set)
    and `Sources/README.md` are excluded from the index.
  - Refreshes are lazy: if no file under `Sources/` has an mtime newer than
    `last_filesystem_scan`, the routine no-ops.
  - Missing files are kept for one cycle with `present: false` before being
    dropped, so an accidental `rm` doesn't immediately destroy hand-curated
    notes / cross-references.

CLI:
  python3 -m superagent.tools.sources_index refresh [--force]
  python3 -m superagent.tools.sources_index list   [--kind document|reference] [--category X]
  python3 -m superagent.tools.sources_index get    <id>
  python3 -m superagent.tools.sources_index by-path <path>
  python3 -m superagent.tools.sources_index touch  <id>     # mark last_accessed=now, read_count++
  python3 -m superagent.tools.sources_index remove <id>     # drop a row (does NOT delete the file)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REF_SUFFIXES = (".ref.md", ".ref.txt")
SOURCES_DIRNAME = "Sources"
RESERVED_NAMES = {"README.md"}
DEFAULT_CACHE_REL = "Sources/_cache"

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
    """Load `_memory/config.yaml.preferences.sources` with defaults."""
    cfg = load_yaml(workspace / "_memory" / "config.yaml")
    prefs = (cfg.get("preferences") or {}).get("sources") or {}
    return {
        "cache_path": str(prefs.get("cache_path", DEFAULT_CACHE_REL)),
        "auto_refresh_index": bool(prefs.get("auto_refresh_index", True)),
        "normalize_policy": str(prefs.get("normalize_policy", "ask")),
        "normalize_policy_batch": str(prefs.get("normalize_policy_batch", "keep")),
    }


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
    """True if `path` is a reference file (`.ref.md` or `.ref.txt`)."""
    name = path.name
    return any(name.endswith(suffix) for suffix in REF_SUFFIXES)


def companion_document(ref_path: Path) -> Path | None:
    """For a ref file, return the document it describes if a sidecar pair exists.

    Two sidecar conventions are recognized:
      Form A — `<full-doc-name>.ref.md` (e.g. `camry-title.pdf` paired with
               `camry-title.pdf.ref.md`). Stem-of-the-ref equals the
               document's full filename.
      Form B — `<stem>.ref.md` next to `<stem>.<ext>` (e.g. `camry-title.ref.md`
               paired with `camry-title.pdf`). Stem-of-the-ref matches one
               document file in the same directory by `path.stem`.
    """
    name = ref_path.name
    for suffix in REF_SUFFIXES:
        if not name.endswith(suffix):
            continue
        stem = name[: -len(suffix)]
        parent = ref_path.parent
        # Form A: `<stem>` exists as a file in the same directory.
        full_match = parent / stem
        if full_match.is_file() and not is_ref_file(full_match):
            return full_match
        # Form B: a sibling whose `.stem` equals our ref-stem (no suffix collision).
        siblings = [
            p for p in parent.iterdir()
            if p.is_file()
            and p.name != ref_path.name
            and not is_ref_file(p)
            and p.stem == stem
        ]
        if siblings:
            return siblings[0]
        return None
    return None


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
    stem = path.name
    for suffix in REF_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        stem = path.stem
    return stem.replace("-", " ").replace("_", " ").strip() or path.name


# ---------------------------------------------------------------------------
# Reference parsing (canonical YAML frontmatter only; liberal parsing lives
# in sources_normalize.py and is invoked separately).
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_canonical_ref(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Parse a ref file's YAML frontmatter.

    Returns `(frontmatter_dict, body)` if the file is in canonical form, or
    `(None, raw_body)` if not. NEVER raises — non-canonical files are passed
    through and will be handled by the normalizer when the user reads them.
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

def _excluded_dirs(workspace: Path, config: dict[str, Any]) -> set[Path]:
    """Resolved paths that the walker must skip (cache + reserved subdirs)."""
    cache_path = workspace / config["cache_path"]
    return {cache_path.resolve()}


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


def walk_sources(workspace: Path, config: dict[str, Any]) -> list[Path]:
    """Walk every Sources/ root under the workspace. Return file paths.

    Excludes: cache subtree, README.md files, dotfiles, the `_cache/`
    name anywhere directly under a sources root.
    """
    excluded = _excluded_dirs(workspace, config)
    files: list[Path] = []
    for root in iter_source_roots(workspace):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if any(str(resolved).startswith(str(ex)) for ex in excluded):
                continue
            if path.name in RESERVED_NAMES:
                continue
            if path.name.startswith("."):
                continue
            # Skip anything inside a `_cache/` directory anywhere under Sources/.
            if any(part == "_cache" for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def max_mtime(workspace: Path, config: dict[str, Any]) -> dt.datetime | None:
    """Return the newest mtime under Sources/ (excluding cache). None if empty."""
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


def build_filesystem_row(workspace: Path, path: Path) -> dict[str, Any]:
    """Build a fresh row from filesystem state for one path.

    Sidecar pattern: if `path` is a non-ref document AND a `<stem>.ref.md` /
    `<stem>.ref.txt` exists next to it, the row's metadata comes from that
    sidecar (kind stays `document`).

    Standalone ref: if `path` is a `.ref.md` / `.ref.txt` with no sibling
    non-ref document, the row's `kind` is `reference`.
    """
    rel_path = workspace_relative(workspace, path)
    row = _empty_row()
    row["path"] = rel_path
    row["id"] = id_for_path(rel_path)
    row["category"] = category_from_path(rel_path)
    row["title"] = title_from_filename(path)

    project_slug = _project_slug_from_path(rel_path)
    if project_slug:
        row["related_project"] = project_slug

    if is_ref_file(path):
        companion = companion_document(path)
        if companion is not None:
            # The DOCUMENT is the source; the .ref.md is just metadata for it.
            # Skip the standalone reference row — the document's row will pull
            # the metadata in via the sidecar lookup below.
            row["kind"] = "_skip_ref_with_companion"
            return row
        row["kind"] = "reference"
        fm, _body = parse_canonical_ref(path)
        if fm is not None:
            row["normalized"] = True
            _apply_frontmatter(row, fm)
        else:
            row["normalized"] = False
    else:
        row["kind"] = "document"
        for suffix in REF_SUFFIXES:
            for candidate in (
                path.with_name(path.name + suffix),    # Form A: <doc>.ref.md
                path.with_name(path.stem + suffix),    # Form B: <stem>.ref.md
            ):
                if candidate == path:
                    continue
                if candidate.is_file():
                    fm, _body = parse_canonical_ref(candidate)
                    if fm is not None:
                        _apply_frontmatter(row, fm)
                    break
            else:
                continue
            break
    return row


def _apply_frontmatter(row: dict[str, Any], fm: dict[str, Any]) -> None:
    """Pull canonical fields from a parsed `.ref.md` frontmatter into `row`."""
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


# ---------------------------------------------------------------------------
# Diff and merge
# ---------------------------------------------------------------------------

def merge_existing(new_row: dict[str, Any], existing_row: dict[str, Any]) -> dict[str, Any]:
    """Preserve user-curated fields from `existing_row` onto `new_row`.

    Rule: if the existing row has a non-empty user-set value for any of the
    PRESERVED_FIELDS, keep it. Otherwise take what the filesystem produced.
    """
    merged = dict(new_row)
    for field in PRESERVED_FIELDS:
        existing_val = existing_row.get(field)
        if existing_val in (None, "", [], 0, False):
            continue
        if field == "read_count" and isinstance(existing_val, int) and existing_val > 0:
            merged[field] = existing_val
        elif field == "tags" and isinstance(existing_val, list) and existing_val:
            merged[field] = existing_val
        elif field in ("notes", "title") and isinstance(existing_val, str) and existing_val:
            merged[field] = existing_val
        elif field in ("sensitive",) and isinstance(existing_val, bool):
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

    Strategy:
      - Index existing rows by id.
      - For each scanned row: if id matches an existing row, run `merge_existing`;
        else this is a new row (added: now).
      - Existing rows that disappeared from the scan: mark present=false IF they
        were `present` last time; drop entirely if they were already absent
        (one-cycle grace period).
      - Skip fully-empty placeholder rows from the template (id == "").
    """
    by_id_existing = {r.get("id"): r for r in existing if r.get("id")}
    by_id_scanned = {r.get("id"): r for r in scanned if r.get("id")}

    merged: list[dict[str, Any]] = []
    timestamp = now_iso()

    for sid, srow in by_id_scanned.items():
        if srow.get("kind") == "_skip_ref_with_companion":
            continue
        if sid in by_id_existing:
            merged.append(merge_existing(srow, by_id_existing[sid]))
        else:
            if not srow.get("added"):
                srow["added"] = timestamp
            merged.append(srow)

    for eid, erow in by_id_existing.items():
        if eid in by_id_scanned:
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
    config = load_config(workspace)
    newest = max_mtime(workspace, config)
    if newest is None:
        return last_scan is None
    if last_scan is None:
        return True
    return newest > last_scan


def refresh(workspace: Path, *, force: bool = False) -> dict[str, Any]:
    """Bring `sources-index.yaml` in sync with the filesystem.

    Cheap when nothing changed (one mtime walk + one yaml load + comparison).
    Returns the resulting index dict.
    """
    index = load_index(workspace)
    if not force and not needs_refresh(workspace, index):
        return index
    config = load_config(workspace)
    files = walk_sources(workspace, config)
    scanned = [build_filesystem_row(workspace, p) for p in files]
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
    L.add_argument("--kind", choices=["document", "reference"], default=None)
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
        index = refresh(workspace, force=args.force)
        rows = index.get("sources") or []
        present = sum(1 for r in rows if r.get("present", True))
        absent = len(rows) - present
        print(f"Index has {len(rows)} row(s); {present} present, {absent} missing.")
        print(f"Last scan: {index.get('last_filesystem_scan')}")
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
