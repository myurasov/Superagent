#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""0.19.0 migration helper (from 0.18.1) -- the watchlist becomes the source registry.

Canonical instructions live in ``superagent/migrations/0.19.0.md``; this
script is the executable form of its ``## Migrate`` steps. Every step is
idempotent and safe on a workspace where its condition does not apply.

Steps (in order):

1. ``Sources/Watchlist/`` (or ``config.preferences.watchlist.path``) created
   with a ``README.md`` (framework template, else an inline fallback).
2. ``_memory/watchlist-state.yaml`` seeded (``{schema_version: 1, watchers: {}}``)
   when missing.
3. Every ``_memory/data-sources.yaml`` row whose id is a shipped pack
   (``simplefin``, ``gmail``) is folded into ``Sources/Watchlist/<id>.ref.md``
   (``watch.pack = <id>``; ``enabled`` / ``schedule`` / ``capture_mode`` carried
   over; auth + budgets into ``watch.params``; ``notes`` into the body) and
   its run state (``last_ingest`` -> ``last_success`` + ``last_harvest``,
   ``failure_streak`` -> ``error_streak``, ``last_run`` ->
   ``last_harvest_result``) into ``watchlist-state.yaml``. Rows with no
   shipped pack are left unfolded (they travel with the file in step 4) and
   listed in the report.
4. ``_memory/data-sources.yaml`` moved to ``_memory/_retired/data-sources.yaml``.
5. Every STANDALONE ``.ref.md`` / ``.ref.txt`` under ``Sources/``,
   ``Projects/*/Sources/`` and ``Projects/*/Resources/`` moves to
   ``Sources/Watchlist/<stem>.ref.md`` (stem kept in the usual Sources naming
   convention: lowercase, ``_`` between words) with a ``watch: {type, enabled:
   false}`` block injected (plus a derived read-only ``prompt`` when the type
   is ``subagent``, which the loader requires); sidecars (a sibling document,
   payment-confirmation fields, or ``kind: file`` pointing at a sibling) and
   non-canonical refs stay put. A source folder left empty by a move is
   removed. Old paths are rewritten in ``Domains/*/sources.md``,
   ``Projects/*/sources.md`` and ``_memory/sources-index.yaml`` (the index row
   keeps its id); then the sources index is refreshed.
6. ``config.yaml`` gains ``preferences.watchlist`` (text insert; comments kept).
7. ``_memory/world.yaml`` checkpointed, then rebuilt (derived; failure is a
   warning only).
8. ``.version`` advanced to 0.19.0.

Before any existing file is rewritten (or a ref is moved) its original bytes
are copied to ``<workspace>/_memory/_checkpoints/0.19.0/<relative path>`` so
``revert.py`` can restore them; files created from scratch are listed in
``<checkpoint dir>/_seeded.txt``. A successful ``validate.py`` relocates that
folder to ``_memory/_retired/0.19.0-originals/`` (the checkpoint folder only
exists while the migration is incomplete; the originals stay so revert is
still byte-for-byte). Every move / fold / rewrite is also recorded in
``_memory/_retired/0.19.0-moves.yaml`` (human-readable; drives revert).

Usage::

    uv run python superagent/migrations/0.19.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success (or nothing to do), 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import inspect
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:  # allow `uv run python <this file>`
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from superagent.tools import sources_index as si  # noqa: E402
from superagent.tools.validate import (  # noqa: E402
    DEFAULT_WATCHLIST_PATH,
    WATCH_TYPE_BY_REF_KIND,
)
from superagent.tools.workspace_init import (  # noqa: E402
    WATCHLIST_README_DEFAULT,
    WATCHLIST_STATE_DEFAULT,
)

TO_VERSION = "0.19.0"
FROM_VERSION = "0.18.1"
CHECKPOINT_REL = Path("_memory") / "_checkpoints" / TO_VERSION
SEEDED_MARKER_NAME = "_seeded.txt"
RETIRED_REL = Path("_memory") / "_retired"
MOVES_MANIFEST_NAME = f"{TO_VERSION}-moves.yaml"
# Where validate.py parks the checkpoint folder once every check has passed.
ORIGINALS_REL = RETIRED_REL / f"{TO_VERSION}-originals"
DATA_SOURCES_NAME = "data-sources.yaml"
STATE_NAME = "watchlist-state.yaml"
README_TEMPLATE_REL = Path("templates") / "folder-readmes" / "Watchlist.md"
STATE_TEMPLATE_REL = Path("templates") / "memory" / STATE_NAME
EM_DASH = "—"

# data-sources rows whose id names a pack that ships in 0.19.0 are folded.
FOLDABLE_PACKS = ("simplefin", "gmail")
RELATED_DOMAIN_BY_PACK = {"simplefin": "finances"}
TITLE_BY_PACK = {
    "simplefin": "SimpleFIN Bridge - bank and brokerage feed",
    "gmail": "Gmail - new mail matching a query",
}
# Row keys that are NOT carried into `watch.params` (they map elsewhere).
LIFECYCLE_KEYS = frozenset({
    "id", "kind", "enabled", "schedule", "capture_mode", "notes",
    "last_ingest", "last_run", "failure_streak",
})
# Legacy `capture_mode` values -> watchlist values. `manual` stays `manual`
# (B4: a manual source is never widened); `scheduled` is the old name for
# `automatic`; a `disabled` row keeps `enabled: false` and becomes `manual`.
CAPTURE_MODE_MAP = {
    "manual": "manual", "automatic": "automatic",
    "scheduled": "automatic", "disabled": "manual",
}
# `.ref.md` frontmatter keys that mark a payment-confirmation sidecar
# (contracts/payment-confirmations.md § 2).
PAYMENT_KEYS = ("payee", "amount", "confirmation")
# `_memory/<file>` YAML inputs that must parse before any step writes.
PREFLIGHT_YAML = ("config.yaml", DATA_SOURCES_NAME, "sources-index.yaml", STATE_NAME)

WATCHLIST_CONFIG_BLOCK = [
    "",
    "  # Watchlist -- change-detection tier and source registry (contracts/watchlist.md).",
    "  # Watchers are `.ref.md` files under `Sources/Watchlist/` carrying a `watch:` block.",
    "  watchlist:",
    '    path: "Sources/Watchlist"        # registry folder (workspace-relative)',
    "    cycles: [daily-update]           # default cadence membership for a watcher",
    "    evict_after_days: 14             # default quiet window before mark-only eviction (null = never)",
    "    allow_cmd: false                 # `cmd` watchers run shell from a data file; opt in explicitly",
    "    min_check_interval_minutes: null # default detect throttle (null = none)",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC).astimezone()).replace(microsecond=0).isoformat()


def default_framework_root() -> Path:
    """`superagent/` as resolved from this script's location."""
    return Path(__file__).resolve().parents[2]


def ref_id(stem: str) -> str:
    """Watcher id for a moved ref: the stem kept in the usual Sources naming
    convention (lowercase, `_` between words, `-` inside tokens), e.g.
    `home_assistant-hub` -> `home_assistant-hub`. Only characters outside
    `[a-z0-9_-]` are folded to `_`."""
    slug = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_-")
    return re.sub(r"_{2,}", "_", slug) or "ref"


def yaml_scalar(value: Any) -> str:
    """Render one scalar as a single-line YAML value (double-quoted strings)."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return yaml.safe_dump(value, default_flow_style=True).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _block_end(lines: list[str], start: int, end: int, indent: int) -> int:
    """First non-blank, non-comment line in [start, end) with indent <= `indent`."""
    for k in range(start, end):
        ln = lines[k]
        if ln.strip() and not _is_comment(ln) and _indent(ln) <= indent:
            return k
    return end


def _insert_at(lines: list[str], start: int, end: int) -> int:
    """Insertion index just after the last non-blank line in [start, end).

    Trailing column-0 comment lines are stepped over as well: a comment run
    sitting flush-left right before the next top-level key is that key's
    header (`# --- Data sources ---`), not part of the block being extended.
    Indented comments stay inside the block.
    """
    k = end
    while k > start and (not lines[k - 1].strip()
                         or (_is_comment(lines[k - 1]) and _indent(lines[k - 1]) == 0)):
        k -= 1
    return k


def _find_key(lines: list[str], start: int, end: int, indent: int, key: str) -> int | None:
    pat = re.compile(rf"^ {{{indent}}}{re.escape(key)}:(\s|$)")
    return next((k for k in range(start, end) if pat.match(lines[k])), None)


def _leading_comments(text: str) -> str:
    """The run of comment / blank lines at the top of a YAML document."""
    out: list[str] = []
    for ln in text.split("\n"):
        if ln.strip() and not ln.lstrip().startswith("#"):
            break
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return ("\n".join(out) + "\n\n") if out else ""


# ---------------------------------------------------------------------------
# 0.19.0-era reference-file helpers. PRIVATE on purpose: this migration reads
# the 0.18.x shapes it converts (`.ref.md` AND `.ref.txt` references, a
# `<doc>.ref.md` sidecar beside its document) and must not depend on a later
# release's index rules -- since 0.20.0 `tools/sources_index.py` knows only
# registry `.ref.md` watchers and `<doc>.<ext>.meta.md` sidecars.
# ---------------------------------------------------------------------------

REF_SUFFIXES = (".ref.md", ".ref.txt")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def is_ref_file(path: Path) -> bool:
    """True for a 0.18.x reference file (`.ref.md` or `.ref.txt`)."""
    return any(path.name.endswith(suffix) for suffix in REF_SUFFIXES)


def ref_stem(path: str | Path) -> str | None:
    """The ref filename minus its `.ref.md` / `.ref.txt` suffix, or None if not a ref."""
    name = Path(path).name
    for suffix in REF_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)] or None
    return None


def companion_document(ref_path: Path) -> Path | None:
    """The document a ref describes: form A `<doc>.<ext>.ref.md` beside `<doc>.<ext>`,
    or form B `<stem>.ref.md` beside `<stem>.<ext>`; None for a standalone ref."""
    stem = ref_stem(ref_path)
    if stem is None:
        return None
    parent = ref_path.parent
    full = parent / stem
    if full.is_file() and not is_ref_file(full):
        return full
    try:
        siblings = sorted(p for p in parent.iterdir()
                          if p.is_file() and p.name != ref_path.name and not is_ref_file(p)
                          and p.stem == stem)
    except OSError:
        return None
    return siblings[0] if siblings else None


def parse_canonical_ref(path: Path) -> tuple[dict[str, Any] | None, str]:
    """(frontmatter, body) for a canonical ref; (None, raw text) otherwise. Never raises."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, ""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text
    if not isinstance(fm, dict):
        return None, text
    return fm, m.group(2).strip()


# ---------------------------------------------------------------------------
# config.yaml: preferences.watchlist (text-level; comments preserved)
# ---------------------------------------------------------------------------

def watchlist_rel_path(config: dict[str, Any] | None) -> str:
    """`preferences.watchlist.path` from a parsed config, else the default."""
    prefs = (config or {}).get("preferences") if isinstance(config, dict) else None
    wl = prefs.get("watchlist") if isinstance(prefs, dict) else None
    if isinstance(wl, dict) and isinstance(wl.get("path"), str) and wl["path"].strip():
        return wl["path"].strip().strip("/")
    return DEFAULT_WATCHLIST_PATH


def ensure_watchlist_config(text: str, now: str) -> tuple[str, bool]:
    """Insert the `preferences.watchlist` block when absent. Returns (text, changed)."""
    config = yaml.safe_load(text) or {}
    if not isinstance(config, dict):
        raise ValueError("config.yaml is not a mapping")
    prefs = config.get("preferences")
    if isinstance(prefs, dict) and isinstance(prefs.get("watchlist"), dict):
        return text, False
    lines = text.split("\n")
    k = _find_key(lines, 0, len(lines), 0, "preferences")
    if k is None:
        at = _insert_at(lines, 0, len(lines))
        block = ["", "preferences:", *WATCHLIST_CONFIG_BLOCK[1:]]
        lines[at:at] = block
    else:
        if re.match(r"^preferences:\s*(\{\}|~|null)?\s*(#.*)?$", lines[k]):
            lines[k] = re.sub(r":\s*(\{\}|~|null)", ":", lines[k], count=1)
        b_end = _block_end(lines, k + 1, len(lines), 0)
        at = _insert_at(lines, k + 1, b_end)
        lines[at:at] = list(WATCHLIST_CONFIG_BLOCK)
    stamped = False
    for i, ln in enumerate(lines):
        if re.match(r"^last_updated:(\s|$)", ln):
            lines[i] = f'last_updated: "{now}"'
            stamped = True
            break
    if not stamped:
        at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("schema_version:")), 0)
        lines.insert(at, f'last_updated: "{now}"')
    new_text = "\n".join(lines)
    check = yaml.safe_load(new_text)
    wl = ((check or {}).get("preferences") or {}).get("watchlist") if isinstance(check, dict) else None
    if not (isinstance(wl, dict) and wl.get("path") == DEFAULT_WATCHLIST_PATH):
        raise ValueError("config.yaml edit did not produce preferences.watchlist; aborting")
    return new_text, True


# ---------------------------------------------------------------------------
# data-sources.yaml fold-in
# ---------------------------------------------------------------------------

def data_source_rows(path: Path) -> list[dict[str, Any]]:
    """Mapping rows under `sources:` with a non-empty id (template placeholder skipped)."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("sources") if isinstance(data, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and str(r.get("id") or "").strip()]


def normalize_capture_mode(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    return CAPTURE_MODE_MAP.get(raw, raw)


def fold_watch_block(row: dict[str, Any]) -> dict[str, Any]:
    """The `watch:` mapping for a folded data-sources row (pack instance)."""
    rid = str(row["id"]).strip()
    watch: dict[str, Any] = {"pack": rid}
    if "enabled" in row:
        watch["enabled"] = bool(row.get("enabled"))
    schedule = row.get("schedule")
    if isinstance(schedule, str) and schedule.strip():
        watch["schedule"] = schedule.strip()
    cm = normalize_capture_mode(row.get("capture_mode"))
    if cm:
        watch["capture_mode"] = cm
    # A folded ingestor is an infrastructure feed: quiet is not dead.
    watch["evict_after_days"] = None
    params = {k: v for k, v in row.items() if k not in LIFECYCLE_KEYS}
    if params:
        watch["params"] = params
    return watch


def compose_folded_ref(row: dict[str, Any], now: str) -> str:
    """Full `.ref.md` text for one folded data-sources row."""
    rid = str(row["id"]).strip()
    title = str(row.get("title") or row.get("name") or TITLE_BY_PACK.get(rid) or rid)
    lines = [
        "---",
        "ref_version: 1",
        f"title: {yaml_scalar(title)}",
        "kind: api",
        f"source: {yaml_scalar(rid)}",
    ]
    domain = RELATED_DOMAIN_BY_PACK.get(rid)
    if domain:
        lines.append(f"related_domain: {domain}")
    lines += [f'added_by: "migrate-{TO_VERSION}"', f'added_at: "{now}"']
    dumped = yaml.safe_dump({"watch": fold_watch_block(row)}, sort_keys=False,
                            allow_unicode=True, default_flow_style=False)
    lines += dumped.rstrip("\n").split("\n")
    lines.append("---")
    lines += ["", "# Notes", ""]
    notes = row.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(notes.strip())
        lines.append("")
    lines.append(f"<!-- folded from _memory/{DATA_SOURCES_NAME} by migration {TO_VERSION} on "
                 f"{now[:10]}; run state moved to _memory/{STATE_NAME} -->")
    return "\n".join(lines) + "\n"


def fold_state_row(row: dict[str, Any], now: str) -> dict[str, Any]:
    """The `watchlist-state.yaml` row carrying a data-sources row's run state.

    `baseline_at` (contracts/watchlist.md § 7: the eviction window starts at
    `max(baseline_at, last_changed)`) is the last successful ingest, else now,
    so a folded feed is never aged from before it existed as a watcher.
    """
    last = row.get("last_ingest")
    last_run = row.get("last_run") if isinstance(row.get("last_run"), dict) else None
    try:
        streak = int(row.get("failure_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    return {
        "status": "active" if row.get("enabled") else "disabled",
        "last_checked": last,
        "last_changed": None,
        "last_success": last,
        "baseline_at": last or now,
        "fingerprint": None,
        "error_streak": streak,
        "error_since": None,
        "last_error": None,
        "evicted_at": None,
        "evict_reason": None,
        "last_outcome": None,
        "last_harvest": last,
        "last_harvest_result": last_run,
        "calls_today": 0,
        "calls_today_date": None,
    }


# ---------------------------------------------------------------------------
# Standalone-ref classification + watch block injection
# ---------------------------------------------------------------------------

def _source_points_at_sibling(ref_path: Path, workspace: Path, source: Any) -> bool:
    if not isinstance(source, str) or not source.strip():
        return False
    src = source.strip()
    parent = ref_path.parent.resolve()
    candidates = [ref_path.parent / Path(src).name, workspace / src, Path(src).expanduser()]
    for cand in candidates:
        try:
            if cand.is_file() and cand.resolve().parent == parent and cand.resolve() != ref_path.resolve():
                return True
        except OSError:
            continue
    return False


def classify_ref(ref_path: Path, workspace: Path) -> tuple[str, str, dict[str, Any] | None]:
    """Return (`standalone` | `sidecar` | `noncanonical`, reason, frontmatter)."""
    companion = companion_document(ref_path)
    if companion is not None:
        return "sidecar", f"sibling document {companion.name}", None
    fm, _body = parse_canonical_ref(ref_path)
    if fm is None:
        return "noncanonical", "no canonical frontmatter (normalize before watching)", None
    if any(k in fm for k in PAYMENT_KEYS):
        return "sidecar", "payment-confirmation fields", fm
    if str(fm.get("kind") or "") == "file" and _source_points_at_sibling(ref_path, workspace,
                                                                          fm.get("source")):
        return "sidecar", "kind: file pointing at a sibling", fm
    return "standalone", "", fm


def derived_prompt(fm: dict[str, Any], fallback_title: str) -> str:
    """Read-only one-line-delta prompt for a ref that becomes a `subagent` watcher.

    `api` / `mcp` / `vault` / `manual` refs have no built-in fetcher; the
    watchlist loader rejects a `subagent` watcher without `watch.prompt`, so
    the migration derives one from the ref's own title and locator. The
    prompt is data the agent reads at dispatch time -- it never runs here.
    """
    title = str(fm.get("title") or fallback_title).strip()
    source = str(fm.get("source") or "").strip()
    where = f" at {source}" if source else ""
    return (f"Read {title}{where}; compare against the previous note for this watcher; "
            "return ONE line: the delta, or 'no change'. Read-only "
            f"{EM_DASH} never submit, send, or write.")


def inject_watch_block(text: str, watch_type: str, prompt: str | None = None) -> tuple[str, bool]:
    """Append a `watch:` block to canonical frontmatter (no-op when one exists).

    `prompt` (required by the loader for `subagent` watchers) is written as a
    quoted scalar when given.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text, False
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return text, False
    if any(re.match(r"^watch:(\s|$)", ln) for ln in lines[1:close]):
        return text, False
    block = [
        "watch:",
        f"  # migrated {TO_VERSION} {EM_DASH} set enabled: true to start watching",
        f"  type: {watch_type}",
        "  enabled: false",
    ]
    if prompt:
        block.append(f"  prompt: {yaml_scalar(prompt)}")
    lines[close:close] = block
    return "\n".join(lines), True


def ref_scan_roots(workspace: Path) -> list[Path]:
    """`Sources/`, `Projects/*/Sources/`, `Projects/*/Resources/` that exist."""
    roots: list[Path] = []
    if (workspace / "Sources").is_dir():
        roots.append(workspace / "Sources")
    projects = workspace / "Projects"
    if projects.is_dir():
        for proj in sorted(p for p in projects.iterdir() if p.is_dir()):
            for sub in ("Sources", "Resources"):
                if (proj / sub).is_dir():
                    roots.append(proj / sub)
    return roots


def candidate_refs(workspace: Path, registry: Path) -> list[Path]:
    """Every ref file under the scan roots, excluding the registry and `_cache/` trees."""
    found: list[Path] = []
    reg = registry.resolve()
    for root in ref_scan_roots(workspace):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not is_ref_file(path):
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved == reg or reg in resolved.parents:
                continue
            if any(part == "_cache" for part in path.relative_to(root).parts):
                continue
            found.append(path)
    return found


def catalogue_files(workspace: Path) -> list[Path]:
    """Every `Domains/*/sources.md` + `Projects/*/sources.md`."""
    out: list[Path] = []
    for pattern in ("Domains/*/sources.md", "Projects/*/sources.md"):
        out.extend(p for p in workspace.glob(pattern) if p.is_file())
    return sorted(out)


def _project_slug(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "Projects" else None


def rewrite_catalogue_text(text: str, cat_rel: str, old_rel: str, new_rel: str) -> tuple[str, int]:
    """Replace `old_rel` (and its project-relative form inside that project) with `new_rel`."""
    count = text.count(old_rel)
    new_text = text.replace(old_rel, new_rel)
    slug = _project_slug(old_rel)
    if slug and cat_rel == f"Projects/{slug}/sources.md":
        proj_rel = old_rel[len(f"Projects/{slug}/"):]
        pat = re.compile(rf"(?<![A-Za-z0-9_./-]){re.escape(proj_rel)}")
        new_text, n = pat.subn(new_rel, new_text)
        count += n
    return new_text, count


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Migration:
    """Stateful runner for the eight 0.19.0 steps against one workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 skip_world: bool = False, now: dt.datetime | None = None,
                 out: Callable[[str], None] = print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else default_framework_root()
        self.dry_run = dry_run
        self.skip_world = skip_world
        self.now = now or dt.datetime.now(dt.UTC).astimezone()
        self.out = out
        self.changed: list[str] = []
        self.world_rebuilt = False
        self.config: dict[str, Any] = {}
        self.registry = self.ws / DEFAULT_WATCHLIST_PATH
        self.manifest: dict[str, Any] = {}
        self.unfolded: list[str] = []

    # -- helpers -----------------------------------------------------------
    def _rel(self, path: Path) -> str:
        return path.relative_to(self.ws).as_posix()

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    def _checkpoint(self, path: Path) -> None:
        dest = self.ws / CHECKPOINT_REL / path.relative_to(self.ws)
        if dest.exists():
            return  # keep the earliest original
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    def _seeded_set(self) -> set[str]:
        marker = self.ws / CHECKPOINT_REL / SEEDED_MARKER_NAME
        if not marker.exists():
            return set()
        return {ln.strip() for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()}

    def _record_seeded(self, rel: str) -> None:
        marker = self.ws / CHECKPOINT_REL / SEEDED_MARKER_NAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        if rel in self._seeded_set():
            return
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(f"{rel}\n")

    def _write(self, path: Path, text: str) -> None:
        self.changed.append(self._rel(path))
        if self.dry_run:
            return
        if not path.exists():
            self._record_seeded(self._rel(path))
        elif self._rel(path) not in self._seeded_set():
            self._checkpoint(path)  # a file this migration seeded has no original to keep
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _manifest_path(self) -> Path:
        return self.ws / RETIRED_REL / MOVES_MANIFEST_NAME

    def _load_manifest(self) -> None:
        path = self._manifest_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        data = data if isinstance(data, dict) else {}
        data.setdefault("schema_version", 1)
        data.setdefault("migration", TO_VERSION)
        for key in ("retired", "folded", "unfolded", "moved_refs", "removed_dirs", "rewritten"):
            data.setdefault(key, [])
        self.manifest = data

    def _record(self, key: str, entry: dict[str, Any], *, unique_by: str) -> None:
        rows = self.manifest.setdefault(key, [])
        if any(isinstance(r, dict) and r.get(unique_by) == entry.get(unique_by) for r in rows):
            return
        rows.append(entry)

    def _save_manifest(self) -> None:
        if self.dry_run:
            return
        path = self._manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest["applied_at"] = now_iso(self.now)
        header = (f"# Migration {TO_VERSION} move ledger -- what migrate.py moved, folded and\n"
                  "# rewrote in this workspace. Read by revert.py; safe to delete after the\n"
                  "# migration is accepted.\n")
        path.write_text(header + yaml.safe_dump(self.manifest, sort_keys=False,
                                                allow_unicode=True), encoding="utf-8")

    def _load_config(self) -> None:
        path = self.ws / "_memory" / "config.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.config = cfg if isinstance(cfg, dict) else {}
        self.registry = self.ws / watchlist_rel_path(self.config)

    # -- steps -------------------------------------------------------------
    def step_registry_folder(self) -> None:
        readme = self.registry / "README.md"
        if not self.registry.is_dir():
            self._say(f"{self._rel(self.registry)}/: created (watcher registry)")
            self.changed.append(self._rel(self.registry) + "/")
            if not self.dry_run:
                self.registry.mkdir(parents=True, exist_ok=True)
                self._record_seeded(self._rel(self.registry) + "/")
        if readme.exists():
            return
        template = self.framework / README_TEMPLATE_REL
        text = template.read_text(encoding="utf-8") if template.exists() else WATCHLIST_README_DEFAULT
        origin = README_TEMPLATE_REL.as_posix() if template.exists() else "inline fallback"
        self._say(f"{self._rel(readme)}: seeded from {origin}")
        self._write(readme, text)

    def step_state_seed(self) -> None:
        state = self.ws / "_memory" / STATE_NAME
        if state.exists():
            return
        template = self.framework / STATE_TEMPLATE_REL
        text = template.read_text(encoding="utf-8") if template.exists() else WATCHLIST_STATE_DEFAULT
        origin = STATE_TEMPLATE_REL.as_posix() if template.exists() else "inline default"
        self._say(f"_memory/{STATE_NAME}: seeded from {origin}")
        self._write(state, text)

    def _state_add_rows(self, rows: dict[str, dict[str, Any]]) -> None:
        state_path = self.ws / "_memory" / STATE_NAME
        text = state_path.read_text(encoding="utf-8") if state_path.exists() else WATCHLIST_STATE_DEFAULT
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"_memory/{STATE_NAME} is not a mapping")
        data.setdefault("schema_version", 1)
        watchers = data.get("watchers")
        if not isinstance(watchers, dict):
            watchers = {}
            data["watchers"] = watchers
        added = [wid for wid in rows if wid not in watchers]
        if not added:
            return
        for wid in added:
            watchers[wid] = rows[wid]
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        self._say(f"_memory/{STATE_NAME}: run state carried for {', '.join(added)}")
        self._write(state_path, _leading_comments(text) + body)

    def step_fold(self) -> None:
        ds_live = self.ws / "_memory" / DATA_SOURCES_NAME
        ds_retired = self.ws / RETIRED_REL / DATA_SOURCES_NAME
        source = ds_live if ds_live.exists() else ds_retired if ds_retired.exists() else None
        if source is None:
            return
        rows = data_source_rows(source)
        state_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            rid = str(row["id"]).strip()
            if rid not in FOLDABLE_PACKS:
                self.unfolded.append(rid)
                self._say(f"{DATA_SOURCES_NAME}: row {rid!r} has no shipped pack; "
                          f"left unfolded in _retired/{DATA_SOURCES_NAME}")
                self._record("unfolded", {"id": rid}, unique_by="id")
                continue
            ref = self.registry / f"{rid}.ref.md"
            if ref.exists():
                continue  # already folded (re-run)
            watch = fold_watch_block(row)
            self._say(f"{DATA_SOURCES_NAME}: {rid} -> {self._rel(ref)} (pack {rid}, "
                      f"schedule {watch.get('schedule', '<pack default>')}, "
                      f"capture_mode {watch.get('capture_mode', '<pack default>')})")
            self._write(ref, compose_folded_ref(row, now_iso(self.now)))
            self._record("folded", {"id": rid, "ref": self._rel(ref), "pack": rid},
                         unique_by="id")
            state_rows[rid] = fold_state_row(row, now_iso(self.now))
        if state_rows and not self.dry_run:
            self._state_add_rows(state_rows)
        elif state_rows:
            self._say(f"_memory/{STATE_NAME}: would carry run state for {', '.join(state_rows)}")

    def step_retire(self) -> None:
        live = self.ws / "_memory" / DATA_SOURCES_NAME
        if not live.exists():
            return
        retired_dir = self.ws / RETIRED_REL
        dest = retired_dir / DATA_SOURCES_NAME
        if dest.exists():
            stamp = self.now.strftime("%Y%m%dT%H%M%S")
            dest = retired_dir / f"data-sources.{stamp}.yaml"
        self._say(f"_memory/{DATA_SOURCES_NAME} -> {self._rel(dest)} (retired, not deleted)")
        self.changed.append(self._rel(live))
        self._record("retired", {"from": self._rel(live), "to": self._rel(dest)}, unique_by="to")
        if self.dry_run:
            return
        # The move IS the preservation (revert moves it back); no checkpoint copy.
        retired_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(live), str(dest))

    def step_move_refs(self) -> None:
        moves: list[tuple[str, str]] = []
        for ref in candidate_refs(self.ws, self.registry):
            rel = self._rel(ref)
            verdict, reason, fm = classify_ref(ref, self.ws)
            if verdict == "sidecar":
                self._say(f"keep {rel} (sidecar: {reason})")
                continue
            if verdict == "noncanonical":
                self._say(f"keep {rel} ({reason})")
                continue
            assert fm is not None
            stem = ref_stem(ref.name) or ref.stem
            dest = self.registry / f"{ref_id(stem)}.ref.md"
            if dest.exists():
                self._say(f"keep {rel} (target {self._rel(dest)} already exists; resolve by hand)")
                continue
            kind = str(fm.get("kind") or "")
            watch_type = WATCH_TYPE_BY_REF_KIND.get(kind, "subagent")
            prompt = derived_prompt(fm, stem) if watch_type == "subagent" else None
            text = ref.read_text(encoding="utf-8")
            new_text, injected = inject_watch_block(text, watch_type, prompt)
            self._say(f"move {rel} -> {self._rel(dest)} (watch.type {watch_type}, enabled: false"
                      f"{'; derived read-only prompt' if injected and prompt else ''}"
                      f"{'' if injected else '; existing watch: block kept'})")
            self.changed.append(rel)
            entry = {"from": rel, "to": self._rel(dest), "watch_type": watch_type,
                     "injected": injected}
            if not self.dry_run:
                self._checkpoint(ref)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(new_text, encoding="utf-8")
                shutil.copystat(ref, dest)
                ref.unlink()
                self._record_seeded(self._rel(dest))
            moves.append((rel, self._rel(dest)))
            self._record("moved_refs", entry, unique_by="from")
            self._prune_empty_parent(ref)
        if not moves:
            return
        self._rewrite_catalogues(moves)
        self._rewrite_index(moves)
        self._refresh_index()

    def _prune_empty_parent(self, moved_ref: Path) -> None:
        """Remove the folder a ref left behind when (and only when) it is now empty.

        Scan roots (`Sources/`, `Projects/<x>/Sources|Resources/`) are never
        removed. Revert recreates the folder when it restores the ref.
        """
        parent = moved_ref.parent
        roots = {p.resolve() for p in ref_scan_roots(self.ws)}
        if parent.resolve() in roots or parent.resolve() == self.registry.resolve():
            return
        if self.dry_run:
            # In dry-run the ref still exists; "empty" means nothing besides it.
            leftovers = [p for p in parent.iterdir() if p != moved_ref]
        else:
            leftovers = list(parent.iterdir()) if parent.is_dir() else ["gone"]
        if leftovers:
            return
        rel_dir = self._rel(parent) + "/"
        self._say(f"remove {rel_dir} (empty after the ref moved)")
        self._record("removed_dirs", {"path": rel_dir}, unique_by="path")
        if not self.dry_run:
            parent.rmdir()

    def _rewrite_catalogues(self, moves: list[tuple[str, str]]) -> None:
        for cat in catalogue_files(self.ws):
            cat_rel = self._rel(cat)
            text = cat.read_text(encoding="utf-8")
            new_text, total = text, 0
            for old_rel, new_rel in moves:
                new_text, n = rewrite_catalogue_text(new_text, cat_rel, old_rel, new_rel)
                total += n
            if total:
                self._say(f"{cat_rel}: {total} ref path(s) rewritten")
                self._record("rewritten", {"path": cat_rel, "replacements": total}, unique_by="path")
                self._write(cat, new_text)

    def _rewrite_index(self, moves: list[tuple[str, str]]) -> None:
        idx_path = si.index_path(self.ws)
        if not idx_path.exists():
            return
        index = si.load_index(self.ws)
        by_old = dict(moves)
        touched = 0
        for row in index.get("sources") or []:
            if not isinstance(row, dict) or row.get("path") not in by_old:
                continue
            # Path updated IN PLACE; the row keeps its id so history.md /
            # interaction-log rows that cite `src-...` keep pointing at a live
            # row. `sources_index.refresh` honours path identity (a row whose
            # `path` already names the scanned file keeps its id).
            new_rel = by_old[row["path"]]
            for entry in self.manifest.get("moved_refs", []):
                if entry.get("from") == row["path"]:
                    entry["index_id"] = row.get("id")
            row["path"] = new_rel
            touched += 1
        if not touched:
            return
        self._say(f"_memory/sources-index.yaml: {touched} row path(s) rewritten (ids kept)")
        self.changed.append(self._rel(idx_path))
        if not self.dry_run:
            self._checkpoint(idx_path)
            si.save_index(self.ws, index)

    def _refresh_index(self) -> None:
        if self.dry_run:
            self._say("_memory/sources-index.yaml: would refresh (derived)")
            return
        idx_path = si.index_path(self.ws)
        if idx_path.exists():
            self._checkpoint(idx_path)
        # The installed index tool does the refresh (whatever release it is);
        # its indexing warnings are echoed when it offers the `warn` hook (0.20.0+).
        kwargs: dict[str, Any] = {}
        if "warn" in inspect.signature(si.refresh).parameters:
            kwargs["warn"] = lambda m: self._say(f"index: {m}")
        si.refresh(self.ws, force=True, **kwargs)
        self._say("_memory/sources-index.yaml: refreshed (derived)")

    def step_config(self) -> None:
        path = self.ws / "_memory" / "config.yaml"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        new_text, changed = ensure_watchlist_config(text, now_iso(self.now))
        if changed:
            self._say("config.yaml: preferences.watchlist block added "
                      "(preferences.ingestion_schedule left in place, inert)")
            self._write(path, new_text)

    def step_world(self) -> None:
        if self.skip_world:
            return
        cmd = [sys.executable, "-m", "superagent.tools.world", "--workspace", str(self.ws), "rebuild"]
        if self.dry_run:
            self._say("world.yaml: would run `uv run python -m superagent.tools.world rebuild`")
            return
        world = self.ws / "_memory" / "world.yaml"
        if world.exists():
            self._checkpoint(world)  # derived, but revert restores the pre-migration bytes
        try:
            res = subprocess.run(cmd, cwd=self.framework.parent, capture_output=True,
                                 text=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env-specific
            self._say(f"warning: world rebuild failed to start ({exc}); derived data, skipping")
            return
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["(no output)"]
            self._say(f"warning: world rebuild exited {res.returncode}: {tail[0]}")
            return
        self.world_rebuilt = True
        self._say("world.yaml: rebuilt (derived)")

    def step_version(self) -> None:
        from superagent.tools.version import set_workspace_version, workspace_version
        current = workspace_version(self.ws)
        if current == TO_VERSION:
            return
        self._say(f".version: {current} -> {TO_VERSION}")
        self.changed.append(".version")
        if not self.dry_run:
            set_workspace_version(self.ws, TO_VERSION)

    def run(self) -> int:
        """Run pre-flight + all steps. Returns a process exit code."""
        if not self.ws.is_dir():
            self.out(f"error: workspace not found at {self.ws}")
            return 1
        for rel in PREFLIGHT_YAML:
            path = self.ws / "_memory" / rel
            if not path.exists():
                continue
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.out(f"error: _memory/{rel} is not well-formed YAML: {exc}")
                return 1
        try:
            self._load_config()
            self._load_manifest()
        except (OSError, yaml.YAMLError) as exc:
            self.out(f"error: pre-flight failed: {exc}")
            return 1
        steps = (self.step_registry_folder, self.step_state_seed, self.step_fold,
                 self.step_retire, self.step_move_refs, self.step_config, self.step_world,
                 self.step_version)
        for step in steps:
            try:
                step()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.out(f"error in {step.__name__}: {exc}")
                self.out("halted; nothing after this step was applied. "
                         "Run revert.py to restore checkpointed files.")
                self._save_manifest()
                return 1
        if self.changed:
            self._save_manifest()
            n = len(dict.fromkeys(self.changed))
            self._say(f"{n} file(s) {'would be ' if self.dry_run else ''}changed")
        else:
            suffix = " (world.yaml re-derived)" if self.world_rebuilt else ""
            self._say(f"nothing to do; workspace already at {TO_VERSION} shape{suffix}")
        if self.unfolded:
            self._say(f"unfolded data-sources rows (no shipped pack): {', '.join(self.unfolded)}")
        return 0


def run_migration(workspace: Path, **kwargs: Any) -> int:
    """Convenience wrapper: `Migration(workspace, **kwargs).run()`."""
    return Migration(workspace, **kwargs).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"migrate-{TO_VERSION}",
        description=f"Apply the {TO_VERSION} workspace migration (idempotent).")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None,
                        help="Path to superagent/ (defaults to this script's tree).")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--skip-world", action="store_true",
                        help="Skip the world.yaml rebuild step.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_migration(args.workspace, framework=args.framework,
                         dry_run=args.dry_run, skip_world=args.skip_world)


if __name__ == "__main__":
    sys.exit(main())
