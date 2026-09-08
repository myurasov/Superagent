#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Watchlist — the change-detection tier for external sources.

Normative statement: `superagent/contracts/watchlist.md`. This module is the
generic runtime: it reads the registry (one `.ref.md` per watcher under
`Sources/Watchlist/`, path per `config.preferences.watchlist.path`), runs the
cheap **detect** stage for every watcher eligible in the current cycle
(cycles nest: slower cycles include faster ones — `weekly-review` also checks
daily watchers, `monthly-review` checks all three tiers; `CYCLE_RANK`), and
dispatches the optional **harvest** handler only when detect fires (or when
the detect type *is* the harvest, as for SimpleFIN). Nothing source-specific
lives here — every source is a **pack**.

A `.ref.md` is a WATCHER DEFINITION and nothing else (`ref_version: 2`, since
0.20.0): `title`, `description`, `related_*`, `tags`, `added_by`, `added_at`
and the `watch:` block. A bare (packless) watcher carries its locator inside
`watch:` — `url:` / `path:` / `cmd:` / `prompt:`; a pack instance sets
`watch.pack` (+ `watch.params`). `watch.pack` or `watch.type` is required;
nothing defaults from a ref `kind` any more, and the retired reference keys
(`kind`, `source`, `ttl_minutes`, ...) are load errors naming the 0.20.0
migration. Registry filenames are Title_Case (`Simplefin.ref.md`,
`Home_Assistant-Hub.ref.md`; `title_case()`); the watcher id is the stem
lowercased (`id_from_stem()`), files resolve case-insensitively, and two
files whose lowercase stems collide are a load error.

Built-in detect types (contract § 5):

    url       conditional GET (If-None-Match / If-Modified-Since); 304 = unchanged;
              200 = sha256 of the normalized text (script/style/comments stripped,
              optional `selector`, `ignore_patterns`), body capped at 2 MB
    path      file sha256, or dir newest-mtime + recursive entry count
    cmd       sha256 of stdout (exit 0 required; needs preferences.watchlist.allow_cmd)
    subagent  never fetches — emits a dispatch spec; the agent records via `stamp`
    harvest   run the pack handler's harvest; its RunResult delta is the fingerprint

Any other `detect.type` (e.g. `gmail`) is valid iff the resolved pack ships a
`handler.py` exposing `detect(ctx) -> DetectResult` (protocol in
`tools/ingest/_base.py`). `index_query` is reserved and rejected.

Packs are self-sufficient folders — `superagent/watchers/<id>/` shipped in
core, `workspace/_custom/watchers/<id>/` as the user overlay (custom wins on
an id collision, announced verbatim). `pack.yaml` supplies detect config, a
declarative probe, auth pointer, budget, and defaults; `handler.py` (loaded
BY FILE PATH, never as a package import) supplies `detect()` and/or a
harvest handler (`harvest(config_row, dry_run)` or an `IngestorBase` class).

Writes (contract § 9 / § 12): `_memory/watchlist-state.yaml` (machine-owned,
atomic replace under a lock); on a detected change one string alert in
`context.yaml.alerts` (prior alert for the same watcher moves to
`alerts-archive.yaml`), one `interaction-log.yaml` row
(`action: watch_change_detected`), and `world.yaml` edges for `related_*`;
harvest runs append to `ingestion-log.yaml` (+ one `action-signals.yaml`
tailor row when a harvest reports errors).

CLI (`--workspace` / `--framework` may go before or after the subcommand):

    uv run python -m superagent.tools.watchlist check --cycle daily-update \\
        [--timeout 15] [--report] [--no-harvest] [--dry-run] [--id X]
    uv run python -m superagent.tools.watchlist stamp --id X \\
        (--changed | --unchanged | --unreachable) [--note "..."]
    uv run python -m superagent.tools.watchlist list [--status active|evicted|disabled] [--json]
    uv run python -m superagent.tools.watchlist enable <pack> --id <id> [--param k=v ...] [--title ...]
    uv run python -m superagent.tools.watchlist probe [<id>] [--all] [--json]
    uv run python -m superagent.tools.watchlist harvest --id X [--dry-run] [--backfill]

`superagent.tools.ext_sources` is a thin alias for the same entry point.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses as dc
import datetime as dt
import fcntl
import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

from superagent.tools.ingest._base import (
    DetectContext,
    DetectError,
    DetectResult,
    IngestorBase,
    ProbeResult,
    ProbeStatus,
    RunResult,
    now_iso,
)
from superagent.tools.next_id import next_id
from superagent.tools.validate import (
    DEFAULT_WATCH_PACKS,
    DEFAULT_WATCHLIST_PATH,
    LEGACY_REF_KEYS,
    LEGACY_REF_MIGRATION,
    REF_SUFFIX,
    REF_TOP_KEYS,
    REF_VERSION,
    WATCH_BUILTIN_TYPES,
    WATCH_ID_RE,
    WATCH_LOCATOR_KEY,
    WATCH_RESERVED_TYPES,
    WATCH_TYPES,
    WATCHLIST_STATE,
    title_case_id,
    watch_id_from_stem,
)

__all__ = [
    "DetectContext", "DetectError", "DetectResult", "RunResult", "IngestorBase",
    "main", "run_check", "run_stamp", "import_handler_module",
    "title_case", "id_from_stem", "ref_filename",
]

# ---------------------------------------------------------------------------
# Constants (the schema names are shared with tools/validate.py + the migrations)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
STATE_FILENAME = WATCHLIST_STATE
HANDLER_FILENAME = "handler.py"
HANDLE_KIND = "watch"

#: Detect types implemented in this module. Anything else needs a pack `detect()`.
BUILTIN_DETECT_TYPES = WATCH_BUILTIN_TYPES
#: Every type name the schema layer knows (validate.py); includes pack-provided ones.
DETECT_TYPES = WATCH_TYPES
RESERVED_TYPES = dict.fromkeys(WATCH_RESERVED_TYPES, "not implemented in this release")
# Which detect-config key carries the locator for each detect type (bare
# watchers set it directly in `watch:`; pack instances via `watch.params`).
LOCATOR_KEY = WATCH_LOCATOR_KEY
# `schedule` is a cadence hint; it stands in for `cycles` when no cycles are listed.
SCHEDULE_TO_CYCLES: dict[str, list[str]] = {
    "daily": ["daily-update"],
    "weekly": ["weekly-review"],
    "monthly": ["monthly-review"],
    "manual": [],
}
# Cadence cycles NEST: a slower run covers every faster one, so `check --cycle
# weekly-review` also checks daily watchers and `monthly-review` checks all
# three tiers. Unknown / custom cycle names match themselves only.
CYCLE_RANK: dict[str, int] = {"daily-update": 0, "weekly-review": 1, "monthly-review": 2}
# `watch.` keys copied verbatim into the detect config (locators + url hardening).
# A row value overrides the same-named pack detect field.
DETECT_OVERRIDE_KEYS = (
    "url", "path", "cmd", "prompt", "query",
    "selector", "ignore_patterns", "min_change_interval_minutes",
)
# Every key the `watch:` block owns; anything else in the block is a harvest
# handler override (contract § 8: "same-named keys in the row's watch: block").
WATCH_FIELDS = frozenset({
    "pack", "type", "enabled", "status", "cycles", "evict_after_days", "expires",
    "min_check_interval_minutes", "schedule", "capture_mode", "params",
    *DETECT_OVERRIDE_KEYS,
})
# Packs that ship in core; `probe` flags a shipped folder that discovery could not load.
SHIPPED_PACK_IDS = DEFAULT_WATCH_PACKS

DEFAULT_CONFIG: dict[str, Any] = {
    "path": DEFAULT_WATCHLIST_PATH,
    "cycles": ["daily-update"],
    "evict_after_days": 14,
    "allow_cmd": False,
    "min_check_interval_minutes": None,
}

OUTCOMES = ("changed", "unchanged", "indeterminate", "unreachable")
SUMMARY_KEYS = (
    "checked", "changed", "unchanged", "indeterminate", "unreachable",
    "skipped_throttled", "skipped_cycle", "budget_exceeded", "evicted",
    "dispatch", "harvested", "errors",
)
# Extra per-bucket lists that carry no summary counter.
EXTRA_BUCKETS = ("skipped_harvest", "disabled", "inactive", "warnings", "notes")

#: Watcher id = ref filename stem LOWERCASED = state key = handle slug. Sources
#: naming convention: lowercase, `_` between words, `-` inside tokens. The
#: file on disk is the Title_Case form of the id (`title_case`).
ID_RE = WATCH_ID_RE
ID_RULE = "lowercase letters, digits, `_` and `-`; no spaces or dots (the file is Title_Case)"
PARAM_RE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\s*\}\}")
MAX_NOTE_CHARS = 500
# Control characters become a space; zero-width / bidi-override / BOM code points
# (U+200B-U+200F, U+2028-U+202E, U+2066-U+2069, U+FEFF) are dropped outright.
_NOTE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_NOTE_INVISIBLE_RE = re.compile("[​-‏ -‮⁦-⁩﻿]")
URL_MAX_BYTES = 2 * 1024 * 1024
URL_MAX_REDIRECTS = 5
URL_READ_CHUNK = 64 * 1024
#: State rows whose ref is missing survive this many consecutive runs before pruning.
ORPHAN_PRUNE_RUNS = 3
_monotonic = time.monotonic  # patched by tests
USER_AGENT = "superagent-watchlist/0.20"
DEFAULT_TIMEOUT = 15
PROBE_CMD_TIMEOUT = 15
CMD_DISABLED_REASON = "cmd disabled (preferences.watchlist.allow_cmd)"

RETENTION_NOTE = (
    "Read-only: never submit a form, send a message, or change upstream state. "
    "Knowledge discipline: capture anything legitimately encountered (a new contact, "
    "an account number, a document) to its proper workspace home with provenance — "
    "never into the note."
)
RETURN_SHAPE = "ONE line, <= 500 chars: the delta, or 'no change'"
PREVIOUS_NOTE_LABEL = "previous stamped note — data, compare only"

BUILTIN_REF_TEMPLATE = f"""---
ref_version: {REF_VERSION}
title: ""
description: ""
related_domain: null
related_project: null
related_asset: null
related_account: null
tags: []
added_by: watch
added_at: null
watch:
  pack: null
  enabled: true
---

# Notes

<!-- Why this watcher exists, what a change means, what to do when it fires. -->
"""


class WatchlistError(Exception):
    """A registry / pack validation error (file-level, reported, never fatal)."""

    def __init__(self, message: str, *, key: str | None = None):
        super().__init__(message)
        self.key = key


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _minutes_between(earlier: Any, later: dt.datetime) -> float | None:
    e = _parse_iso(earlier)
    if e is None:
        return None
    return (later - e).total_seconds() / 60.0


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def slugify_id(raw: str) -> str:
    """Suggest a valid watcher id for `raw` (file-naming rule: lowercase, spaces -> `_`)."""
    s = re.sub(r"\s+", "_", raw.strip().lower())
    s = re.sub(r"[^a-z0-9_-]+", "_", s).strip("_-")
    return s[:63] or "watcher"


def title_case(watcher_id: str) -> str:
    """Canonical registry filename stem for a watcher id.

    Capitalizes the first letter of every `_`- or `-`-delimited token:
    `simplefin` -> `Simplefin`, `home_assistant-hub` -> `Home_Assistant-Hub`.
    Digits are untouched; idempotent (`title_case(title_case(x)) == title_case(x)`).
    Imported by the 0.20.0 migration for the case-only renames.
    """
    return title_case_id(watcher_id)


def id_from_stem(stem: str) -> str:
    """Watcher id for a registry filename stem: the stem lowercased.

    `Home_Assistant-Hub` -> `home_assistant-hub`. The id is the state key
    and the `watch:<id>` handle; files resolve case-insensitively, so
    `simplefin.ref.md` and `Simplefin.ref.md` name the same watcher (and two
    such files side by side are a load error).
    """
    return watch_id_from_stem(stem)


def ref_filename(watcher_id: str) -> str:
    """`simplefin` -> `Simplefin.ref.md` — the file `enable` writes for an id."""
    return f"{title_case(watcher_id)}{REF_SUFFIX}"


def is_ref_name(name: str) -> bool:
    """True for `<stem>.ref.md`, suffix matched case-insensitively."""
    return name.lower().endswith(REF_SUFFIX) and len(name) > len(REF_SUFFIX)


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def _leading_comment_block(path: Path) -> str:
    """Return the top-of-file `#` / blank lines so a rewrite keeps the header."""
    if not path.exists():
        return ""
    lines: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                lines.append(line)
            else:
                break
    except OSError:
        return ""
    while lines and not lines[-1].strip():
        lines.pop()
    return ("\n".join(lines) + "\n\n") if lines else ""


def save_yaml_atomic(path: Path, data: Any, *, keep_header: bool = False) -> None:
    """Write YAML via a sibling temp file + rename (atomic on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _leading_comment_block(path) if keep_header else ""
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(path)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a markdown file into (YAML frontmatter dict, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise WatchlistError(f"frontmatter is not valid YAML: {exc}") from exc
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise WatchlistError("frontmatter must be a mapping")
    return fm, m.group(2)


def _line_of_key(text: str, key: str | None) -> int:
    """Best-effort 1-based line of a (possibly dotted) frontmatter key; 1 if unknown."""
    if not key:
        return 1
    lines = text.splitlines()
    parts = key.split(".")
    start = 0
    indent = 0
    found = 1
    for depth, part in enumerate(parts):
        pat = re.compile(r"^(\s*)" + re.escape(part) + r"\s*:")
        hit = None
        for idx in range(start, len(lines)):
            m = pat.match(lines[idx])
            if m and (depth == 0 or len(m.group(1)) > indent):
                hit = idx
                indent = len(m.group(1))
                break
            if depth > 0 and lines[idx].strip() and not lines[idx].startswith(" " * (indent + 1)):
                break
        if hit is None:
            return found
        found = hit + 1
        start = hit + 1
    return found


def sanitize_note(note: str | None) -> str:
    """Cap and clean a stamped note: no control characters, one line, <=500 chars."""
    if not note:
        return ""
    cleaned = _NOTE_INVISIBLE_RE.sub("", str(note))
    cleaned = _NOTE_CONTROL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_NOTE_CHARS]


def _as_int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise WatchlistError(f"`{field}` must be an integer or null", key=f"watch.{field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WatchlistError(f"`{field}` must be an integer or null", key=f"watch.{field}") from exc


def _rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def cycle_covers(run_cycle: str, watcher_cycles: list[str]) -> bool:
    """Does a `check --cycle <run_cycle>` run include a watcher with `watcher_cycles`?

    Exact membership always counts. Beyond that the standard cadences nest
    (`CYCLE_RANK`): a run covers every watcher cycle of equal or lower rank,
    so a daily watcher runs under weekly-review and monthly-review, and a
    weekly one under monthly-review. A custom cycle name (not in
    `CYCLE_RANK`) only ever matches itself; `cycles: []` is never covered.
    """
    if run_cycle in watcher_cycles:
        return True
    rank = CYCLE_RANK.get(run_cycle)
    if rank is None:
        return False
    return any(CYCLE_RANK.get(c, rank + 1) <= rank for c in watcher_cycles)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dc.dataclass
class Config:
    path: str = DEFAULT_CONFIG["path"]
    cycles: list[str] = dc.field(default_factory=lambda: list(DEFAULT_CONFIG["cycles"]))
    evict_after_days: int | None = DEFAULT_CONFIG["evict_after_days"]
    allow_cmd: bool = DEFAULT_CONFIG["allow_cmd"]
    min_check_interval_minutes: int | None = DEFAULT_CONFIG["min_check_interval_minutes"]


def load_config(workspace: Path) -> Config:
    """`config.preferences.watchlist` over `DEFAULT_CONFIG`; absent block = defaults."""
    cfg = load_yaml(workspace / "_memory" / "config.yaml") or {}
    block = ((cfg.get("preferences") or {}).get("watchlist") or {}) if isinstance(cfg, dict) else {}
    if not isinstance(block, dict):
        block = {}
    out = Config()
    if isinstance(block.get("path"), str) and block["path"].strip():
        out.path = block["path"].strip()
    if isinstance(block.get("cycles"), list) and block["cycles"]:
        out.cycles = [str(c) for c in block["cycles"]]
    if "evict_after_days" in block:
        out.evict_after_days = _as_int_or_none(block["evict_after_days"], "evict_after_days")
    if "allow_cmd" in block:
        out.allow_cmd = bool(block["allow_cmd"])
    if "min_check_interval_minutes" in block:
        out.min_check_interval_minutes = _as_int_or_none(
            block["min_check_interval_minutes"], "min_check_interval_minutes"
        )
    return out


def registry_dir(workspace: Path, cfg: Config) -> Path:
    p = Path(cfg.path).expanduser()
    return p if p.is_absolute() else workspace / p


# ---------------------------------------------------------------------------
# Pack handlers — loaded by file path, cached per file
# ---------------------------------------------------------------------------

_HANDLER_CACHE: dict[Path, ModuleType] = {}


def import_handler_module(handler_py: Path) -> ModuleType:
    """Load `<pack>/handler.py` by file path (never a package import). Cached per path.

    Registered in `sys.modules` under a unique synthetic name so dataclasses,
    pickling, and `patch.object` on the module keep working.
    """
    handler_py = Path(handler_py).resolve()
    cached = _HANDLER_CACHE.get(handler_py)
    if cached is not None:
        return cached
    if not handler_py.is_file():
        raise WatchlistError(f"handler not found: {handler_py}")
    mod_name = f"superagent_watcher_pack_{handler_py.parent.name.replace('-', '_')}_{_short_hash(str(handler_py))[:8]}"
    spec = importlib.util.spec_from_file_location(mod_name, handler_py)
    if spec is None or spec.loader is None:
        raise WatchlistError(f"cannot load {handler_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        raise WatchlistError(f"{handler_py}: import failed: {type(exc).__name__}: {exc}") from exc
    _HANDLER_CACHE[handler_py] = module
    return module


def _harvest_entry(module: ModuleType) -> Callable[..., Any] | type[IngestorBase] | None:
    """The module's harvest entry: a `harvest()` function or one IngestorBase subclass."""
    fn = getattr(module, "harvest", None)
    if callable(fn) and not isinstance(fn, type):
        return fn
    classes = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, IngestorBase) and obj is not IngestorBase
        and getattr(obj, "__module__", "") == module.__name__
    ]
    return classes[0] if classes else None


class _FunctionHarvester:
    """Adapts a module-level `harvest(config_row, dry_run=False[, workspace=...])` to `.run()`."""

    def __init__(self, fn: Callable[..., Any], workspace: Path):
        self._fn = fn
        self.workspace = workspace
        self._wants_workspace = "workspace" in inspect.signature(fn).parameters

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        if self._wants_workspace:
            return self._fn(config_row, dry_run=dry_run, workspace=self.workspace)
        return self._fn(config_row, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------


@dc.dataclass
class Pack:
    id: str
    folder: Path
    origin: str  # framework | custom
    title: str = ""
    description: str = ""
    kind: str = "generic"
    parameterized: bool = False
    params: dict[str, dict[str, Any]] = dc.field(default_factory=dict)
    detect: dict[str, Any] = dc.field(default_factory=dict)
    harvest: dict[str, Any] | None = None
    probe: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    budget: dict[str, Any] = dc.field(default_factory=dict)
    defaults: dict[str, Any] = dc.field(default_factory=dict)

    @property
    def detect_type(self) -> str:
        return str(self.detect.get("type") or "")

    @property
    def handler_path(self) -> Path | None:
        p = self.folder / HANDLER_FILENAME
        return p if p.is_file() else None

    def handler_module(self) -> ModuleType:
        if self.handler_path is None:
            raise WatchlistError(f"pack `{self.id}` ships no {HANDLER_FILENAME}")
        return import_handler_module(self.handler_path)

    @property
    def provides_detect(self) -> bool:
        return self.handler_path is not None and callable(
            getattr(self.handler_module(), "detect", None)
        )


def _pack_from_yaml(folder: Path, data: Any, origin: str) -> Pack:
    where = folder / "pack.yaml"
    if not isinstance(data, dict):
        raise WatchlistError(f"{where}: not a mapping")
    pid = str(data.get("id") or folder.name)
    if pid != folder.name:
        raise WatchlistError(f"{where}: id {pid!r} must equal the folder name {folder.name!r}")
    if not ID_RE.match(pid):
        raise WatchlistError(f"{where}: id {pid!r} is not a valid pack id ({ID_RULE})")
    version = data.get("watcher_version", 1)
    if version != 1:
        raise WatchlistError(f"{where}: unsupported watcher_version {version!r}")
    detect = data.get("detect") or {}
    if not isinstance(detect, dict):
        raise WatchlistError(f"{where}: `detect` must be a mapping")
    dtype = detect.get("type")
    if dtype in RESERVED_TYPES:
        raise WatchlistError(f"{where}: detect.type {dtype!r} {RESERVED_TYPES[dtype]}")
    if not isinstance(dtype, str) or not dtype:
        raise WatchlistError(f"{where}: detect.type is required")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise WatchlistError(f"{where}: `params` must be a mapping")
    for name, spec in params.items():
        if not isinstance(spec, dict):
            raise WatchlistError(f"{where}: params.{name} must be a mapping")
    harvest = data.get("harvest")
    if harvest is not None and not isinstance(harvest, dict):
        raise WatchlistError(f"{where}: `harvest` must be a mapping")
    probe = data.get("probe")
    if probe is not None and not isinstance(probe, dict):
        raise WatchlistError(f"{where}: `probe` must be a mapping")
    pack = Pack(
        id=pid,
        folder=folder,
        origin=origin,
        title=str(data.get("title") or pid),
        description=" ".join(str(data.get("description") or "").split()),
        kind=str(data.get("kind") or "generic"),
        parameterized=bool(data.get("parameterized", bool(params))),
        params={str(k): dict(v) for k, v in params.items()},
        detect=dict(detect),
        harvest=dict(harvest) if harvest else None,
        probe=dict(probe) if probe else None,
        auth=dict(data["auth"]) if isinstance(data.get("auth"), dict) else None,
        budget=dict(data.get("budget") or {}),
        defaults=dict(data.get("defaults") or {}),
    )
    # A pack-defined detect type must be backed by handler.py::detect().
    if dtype not in BUILTIN_DETECT_TYPES:
        if pack.handler_path is None:
            raise WatchlistError(
                f"{where}: detect.type {dtype!r} is not built in and the pack ships no "
                f"{HANDLER_FILENAME} exposing detect()"
            )
        if not callable(getattr(pack.handler_module(), "detect", None)):
            raise WatchlistError(
                f"{where}: detect.type {dtype!r} is not built in and {HANDLER_FILENAME} "
                "exposes no detect()"
            )
    if pack.harvest is not None or dtype == "harvest":
        declared = (pack.harvest or {}).get("handler")
        if declared not in (None, HANDLER_FILENAME):
            raise WatchlistError(
                f"{where}: harvest.handler must be omitted (the pack's own {HANDLER_FILENAME} "
                f"is loaded by path); got {declared!r}"
            )
        if pack.handler_path is None:
            raise WatchlistError(f"{where}: harvest declared but the pack ships no {HANDLER_FILENAME}")
        if _harvest_entry(pack.handler_module()) is None:
            raise WatchlistError(
                f"{where}: {HANDLER_FILENAME} exposes neither harvest() nor an IngestorBase subclass"
            )
        if pack.harvest is None:
            pack.harvest = {}
    return pack


def pack_roots(framework: Path, workspace: Path) -> list[tuple[Path, str]]:
    return [
        (framework / "watchers", "framework"),
        (workspace / "_custom" / "watchers", "custom"),
    ]


def discover_packs(
    framework: Path, workspace: Path, *, announce: Callable[[str], None] | None = None
) -> tuple[dict[str, Pack], list[str]]:
    """Scan framework + `_custom` pack folders. Custom wins on id collision."""
    announce = announce or (lambda msg: print(msg, file=sys.stderr))
    packs: dict[str, Pack] = {}
    errors: list[str] = []
    for root, origin in pack_roots(framework, workspace):
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest = folder / "pack.yaml"
            if not manifest.is_file():
                continue
            try:
                data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
                pack = _pack_from_yaml(folder, data, origin)
            except (OSError, yaml.YAMLError) as exc:
                errors.append(f"{manifest}: {exc}")
                continue
            except WatchlistError as exc:
                errors.append(str(exc))
                continue
            if pack.id in packs and origin == "custom":
                announce(f"Using `_custom/watchers/{pack.id}` (overrides framework pack).")
            packs[pack.id] = pack
    return packs, errors


def _template(value: Any, params: dict[str, Any]) -> Any:
    """Substitute `{{name}}` placeholders recursively; unknown names are errors."""
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in params:
                raise WatchlistError(f"unknown pack param `{name}`", key="watch.params")
            v = params[name]
            return "" if v is None else str(v)
        return PARAM_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _template(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_template(v, params) for v in value]
    return value


def resolve_params(pack: Pack, given: dict[str, Any]) -> dict[str, Any]:
    params = dict(given or {})
    for name, spec in pack.params.items():
        if name in params:
            continue
        if "default" in spec:
            params[name] = spec["default"]
        elif spec.get("required"):
            raise WatchlistError(
                f"missing required param `{name}` for pack `{pack.id}`", key="watch.params"
            )
    return params


def render_detect(pack: Pack, params: dict[str, Any]) -> dict[str, Any]:
    """Pack `detect:` with `{{name}}` filled in; empty substituted values are unset."""
    detect = _template(dict(pack.detect), params)
    return {k: v for k, v in detect.items() if k == "type" or v not in ("", None)}


# ---------------------------------------------------------------------------
# Registry (Sources/Watchlist/<id>.ref.md)
# ---------------------------------------------------------------------------


@dc.dataclass
class Watcher:
    id: str
    path: Path
    title: str
    type: str
    description: str = ""
    related: dict[str, str] = dc.field(default_factory=dict)
    tags: list[str] = dc.field(default_factory=list)
    pack: Pack | None = None
    enabled: bool = True
    status: str | None = None  # registry-declared (`status: active` revives)
    cycles: list[str] = dc.field(default_factory=list)
    evict_after_days: int | None = None
    expires: dt.date | None = None
    min_check_interval_minutes: int | None = None
    schedule: str | None = None
    capture_mode: str = "automatic"
    params: dict[str, Any] = dc.field(default_factory=dict)
    detect: dict[str, Any] = dc.field(default_factory=dict)
    harvest: dict[str, Any] | None = None
    budget: dict[str, Any] = dc.field(default_factory=dict)
    probe: dict[str, Any] | None = None
    watch_raw: dict[str, Any] = dc.field(default_factory=dict)
    mtime: float = 0.0

    @property
    def handle(self) -> str:
        return f"{HANDLE_KIND}:{self.id}"

    @property
    def locator(self) -> str:
        key = LOCATOR_KEY.get(self.type)
        return str(self.detect.get(key) or "") if key else ""

    @property
    def canonical_filename(self) -> str:
        return ref_filename(self.id)


def watcher_id_for(path: Path) -> str:
    """The watcher id a registry path names: its `.ref.md` stem, lowercased."""
    name = path.name
    stem = name[: -len(REF_SUFFIX)] if is_ref_name(name) else path.stem
    return id_from_stem(stem)


def _check_ref_schema(fm: dict[str, Any]) -> None:
    """Reject the retired 0.19.0 reference keys and anything outside `ref_version: 2`."""
    legacy = [k for k in fm if k in LEGACY_REF_KEYS]
    if legacy:
        names = ", ".join(f"`{k}`" for k in legacy)
        raise WatchlistError(
            f"legacy reference key(s) {names}: since {LEGACY_REF_MIGRATION} a .ref.md is a watcher "
            f"definition only (ref_version {REF_VERSION}) — run the {LEGACY_REF_MIGRATION} "
            "migration (`migrate`), or move the locator into `watch:` (url: / path: / cmd: / "
            "prompt: / query:) and drop these keys",
            key=str(legacy[0]),
        )
    version = fm.get("ref_version")
    if version is None:
        raise WatchlistError(
            f"ref_version: {REF_VERSION} is required (a .ref.md is a watcher definition; "
            f"a ref written before {LEGACY_REF_MIGRATION} is converted by the "
            f"{LEGACY_REF_MIGRATION} migration)",
            key="ref_version",
        )
    if version != REF_VERSION:
        raise WatchlistError(
            f"ref_version {version!r} is not {REF_VERSION}; run the {LEGACY_REF_MIGRATION} "
            "migration (`migrate`) to convert this ref",
            key="ref_version",
        )
    unknown = [str(k) for k in fm if k not in REF_TOP_KEYS]
    if unknown:
        names = ", ".join(f"`{k}`" for k in unknown)
        raise WatchlistError(
            f"unknown frontmatter key(s) {names} (ref_version {REF_VERSION} allows: "
            f"{', '.join(sorted(REF_TOP_KEYS))})",
            key=unknown[0],
        )


def build_watcher(path: Path, fm: dict[str, Any], cfg: Config, packs: dict[str, Pack]) -> Watcher:
    wid = watcher_id_for(path)
    if not ID_RE.match(wid):
        raise WatchlistError(
            f"id {wid!r} is not a valid watcher id ({ID_RULE}); rename the file to "
            f"`{ref_filename(slugify_id(wid))}`"
        )
    _check_ref_schema(fm)
    watch = fm.get("watch")
    if watch is None:
        raise WatchlistError(
            "`watch:` block is required — a .ref.md is a watcher definition; set watch.pack or "
            "watch.type",
            key="watch",
        )
    if not isinstance(watch, dict):
        raise WatchlistError("`watch:` must be a mapping", key="watch")

    pack: Pack | None = None
    pack_id = watch.get("pack")
    if pack_id is not None:
        pack = packs.get(str(pack_id))
        if pack is None:
            raise WatchlistError(f"unknown pack `{pack_id}`", key="watch.pack")

    declared_type = watch.get("type")
    if declared_type in RESERVED_TYPES:
        raise WatchlistError(
            f"watch.type `{declared_type}` {RESERVED_TYPES[declared_type]}", key="watch.type"
        )
    if pack is not None:
        wtype = pack.detect_type
        if declared_type is not None and str(declared_type) != wtype:
            raise WatchlistError(
                f"watch.type `{declared_type}` conflicts with pack `{pack.id}` ({wtype})",
                key="watch.type",
            )
    elif declared_type is not None:
        wtype = str(declared_type)
    else:
        raise WatchlistError(
            "watch.pack or watch.type is required (a bare watcher: `type: url|path|cmd|subagent` "
            "plus its locator `url:` / `path:` / `cmd:` / `prompt:`; a pack instance: `pack: <id>`)",
            key="watch",
        )
    if pack is None and wtype == "harvest":
        raise WatchlistError(
            f"watch.type `harvest` needs a pack — the pack's {HANDLER_FILENAME} IS the harvest; "
            "set `watch.pack: <id>`",
            key="watch.type",
        )
    if pack is None and wtype not in BUILTIN_DETECT_TYPES:
        hint = ", ".join(sorted(BUILTIN_DETECT_TYPES))
        raise WatchlistError(
            f"watch.type `{wtype}` is not built in ({hint}); use `watch.pack: <id>` for a pack "
            f"whose {HANDLER_FILENAME} exposes detect()",
            key="watch.type",
        )

    # --- detect config -----------------------------------------------------
    params = watch.get("params") or {}
    if not isinstance(params, dict):
        raise WatchlistError("watch.params must be a mapping", key="watch.params")
    params = dict(params)
    detect: dict[str, Any] = {"type": wtype}
    if pack is not None:
        params = resolve_params(pack, params)
        detect.update(render_detect(pack, params))
        detect["type"] = wtype
    for key in DETECT_OVERRIDE_KEYS:
        if watch.get(key) not in (None, ""):
            detect[key] = watch[key]
    locator_key = LOCATOR_KEY.get(wtype)
    if locator_key and not str(detect.get(locator_key) or "").strip():
        msg = f"{wtype} watcher needs its locator: watch.{locator_key}"
        if pack is not None:
            msg += f" (or the pack param that fills it — see `watch.params` for pack `{pack.id}`)"
        raise WatchlistError(msg, key=f"watch.{locator_key}")
    if "ignore_patterns" in detect:
        pats = detect["ignore_patterns"]
        if not isinstance(pats, list):
            raise WatchlistError("ignore_patterns must be a list of regexes",
                                 key="watch.ignore_patterns")
        for pat in pats:
            try:
                re.compile(str(pat))
            except re.error as exc:
                raise WatchlistError(f"bad ignore pattern {pat!r}: {exc}",
                                     key="watch.ignore_patterns") from exc
    if "min_change_interval_minutes" in detect:
        detect["min_change_interval_minutes"] = _as_int_or_none(
            detect["min_change_interval_minutes"], "min_change_interval_minutes"
        )

    # --- lifecycle fields: row (present, even null) > pack.defaults > config
    pdef = pack.defaults if pack else {}

    schedule = watch["schedule"] if "schedule" in watch else pdef.get("schedule")
    if schedule is not None and str(schedule) not in SCHEDULE_TO_CYCLES:
        raise WatchlistError("schedule must be daily|weekly|monthly|manual|null", key="watch.schedule")

    # Resolution: row cycles > row schedule > pack cycles > pack schedule > config.
    if "cycles" in watch:
        cycles = watch["cycles"] if watch["cycles"] is not None else []
    elif "schedule" in watch and watch["schedule"] is not None:
        cycles = list(SCHEDULE_TO_CYCLES[str(watch["schedule"])])
    elif "cycles" in pdef:
        cycles = pdef["cycles"] if pdef["cycles"] is not None else []
    elif schedule is not None:
        cycles = list(SCHEDULE_TO_CYCLES[str(schedule)])
    else:
        cycles = list(cfg.cycles)
    if isinstance(cycles, str):
        cycles = [cycles]
    if not isinstance(cycles, list):
        raise WatchlistError("cycles must be a list", key="watch.cycles")

    if "evict_after_days" in watch:
        evict_after = _as_int_or_none(watch["evict_after_days"], "evict_after_days")
    elif "evict_after_days" in pdef:
        evict_after = _as_int_or_none(pdef["evict_after_days"], "evict_after_days")
    else:
        evict_after = cfg.evict_after_days

    if "min_check_interval_minutes" in watch:
        min_check = _as_int_or_none(watch["min_check_interval_minutes"], "min_check_interval_minutes")
    elif "min_check_interval_minutes" in pdef:
        min_check = _as_int_or_none(pdef["min_check_interval_minutes"], "min_check_interval_minutes")
    else:
        min_check = cfg.min_check_interval_minutes

    capture_mode = watch["capture_mode"] if "capture_mode" in watch else pdef.get("capture_mode", "automatic")
    if capture_mode is None:
        capture_mode = "automatic"
    capture_mode = str(capture_mode)
    if capture_mode not in ("automatic", "manual"):
        raise WatchlistError("capture_mode must be automatic|manual", key="watch.capture_mode")

    expires_raw = watch.get("expires")
    expires: dt.date | None = None
    if expires_raw is not None:
        if isinstance(expires_raw, dt.datetime):
            expires = expires_raw.date()
        elif isinstance(expires_raw, dt.date):
            expires = expires_raw
        else:
            try:
                expires = dt.date.fromisoformat(str(expires_raw))
            except ValueError as exc:
                raise WatchlistError("expires must be YYYY-MM-DD", key="watch.expires") from exc
    status = watch.get("status")
    if status is not None and str(status) not in ("active", "disabled", "evicted"):
        raise WatchlistError("status must be active|disabled|evicted", key="watch.status")

    related = {
        k: str(fm[k]) for k in ("related_domain", "related_project", "related_asset", "related_account")
        if fm.get(k) not in (None, "", [])
    }
    tags = [str(t) for t in (fm.get("tags") or []) if t]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    return Watcher(
        id=wid,
        path=path,
        title=str(fm.get("title") or wid),
        type=wtype,
        description=" ".join(str(fm.get("description") or "").split()),
        related=related,
        tags=tags,
        pack=pack,
        enabled=bool(watch.get("enabled", True)),
        status=str(status) if status is not None else None,
        cycles=[str(c) for c in cycles],
        evict_after_days=evict_after,
        expires=expires,
        min_check_interval_minutes=min_check,
        schedule=str(schedule) if schedule is not None else None,
        capture_mode=capture_mode,
        params=params,
        detect=detect,
        harvest=pack.harvest if pack else None,
        budget=dict(pack.budget) if pack else {},
        probe=pack.probe if pack else None,
        watch_raw=dict(watch),
        mtime=mtime,
    )


def registry_files(folder: Path) -> list[Path]:
    """Every top-level `*.ref.md` in the registry, suffix matched case-insensitively."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and is_ref_name(p.name)),
                  key=lambda p: p.name.lower())


def registry_stray_files(folder: Path) -> list[str]:
    """Files in the registry that are neither `README.md` nor a `.ref.md` — not watchers.

    A watcher definition parked under the wrong suffix (`Foo.md`) would
    otherwise be silently nothing: never loaded, never checked, never
    converted by a migration. `check` and `list` surface these as warnings.
    """
    if not folder.is_dir():
        return []
    out: list[str] = []
    for p in sorted(folder.iterdir(), key=lambda q: q.name.lower()):
        if (not p.is_file() or p.name.startswith(".") or p.name == "README.md"
                or is_ref_name(p.name)):
            continue
        out.append(
            f"{p.name}: not a .ref.md — not a watcher; rename it to "
            f"`{ref_filename(id_from_stem(p.name.split('.')[0]))}` (id = stem lowercased) "
            "or move it out of the registry"
        )
    return out


def find_ref(folder: Path, watcher_id: str) -> Path | None:
    """The registry file for `watcher_id`, whatever its case on disk (None if absent)."""
    wid = id_from_stem(watcher_id)
    for path in registry_files(folder):
        if watcher_id_for(path) == wid:
            return path
    return None


def load_registry(
    workspace: Path, cfg: Config, packs: dict[str, Pack]
) -> tuple[list[Watcher], list[dict[str, Any]]]:
    """Parse every `*.ref.md` in the registry folder. Errors never abort.

    The id is the filename stem lowercased (`Simplefin.ref.md` -> `simplefin`);
    two files whose lowercase stems collide are both reported as errors and
    neither loads. Each error is `{id, file, line, error}` with `error` in
    the contract's `<file>:<line>: <message>` form. `README.md`, `.meta.md`
    sidecars and anything that is not a `.ref.md` are not watchers.
    """
    folder = registry_dir(workspace, cfg)
    watchers: list[Watcher] = []
    errors: list[dict[str, Any]] = []
    if not folder.is_dir():
        return watchers, errors
    paths = registry_files(folder)
    by_id: dict[str, list[Path]] = {}
    for path in paths:
        by_id.setdefault(watcher_id_for(path), []).append(path)
    for path in paths:
        rel = _rel(workspace, path)
        wid = watcher_id_for(path)
        text = ""
        try:
            siblings = by_id[wid]
            if len(siblings) > 1:
                raise WatchlistError(
                    f"watcher id `{wid}` is claimed by {len(siblings)} files "
                    f"({', '.join(p.name for p in siblings)}); ids resolve case-insensitively "
                    f"— rename one (canonical: `{ref_filename(wid)}`)"
                )
            text = path.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
            if fm is None:
                raise WatchlistError("missing YAML frontmatter (`---` block)")
            watchers.append(build_watcher(path, fm, cfg, packs))
        except WatchlistError as exc:
            line = _line_of_key(text, exc.key)
            errors.append({"id": wid, "file": rel, "line": line,
                           "error": f"{rel}:{line}: {exc}"})
        except OSError as exc:
            errors.append({"id": wid, "file": rel, "line": 1,
                           "error": f"{rel}:1: cannot read: {exc}"})
    return watchers, errors


# ---------------------------------------------------------------------------
# State (_memory/watchlist-state.yaml)
# ---------------------------------------------------------------------------


def state_path(workspace: Path) -> Path:
    return workspace / "_memory" / STATE_FILENAME


def default_row() -> dict[str, Any]:
    return {
        "status": "active",
        "last_checked": None,
        "last_changed": None,
        "last_success": None,
        "baseline_at": None,
        "fingerprint": None,
        "last_outcome": None,
        "error_streak": 0,
        "error_since": None,
        "last_error": None,
        "evicted_at": None,
        "evict_reason": None,
    }


def load_state(workspace: Path) -> dict[str, Any]:
    data = load_yaml(state_path(workspace))
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", SCHEMA_VERSION)
    rows = data.get("watchers")
    if not isinstance(rows, dict):
        rows = {}
    data["watchers"] = {str(k): (v if isinstance(v, dict) else {}) for k, v in rows.items()}
    for row in data["watchers"].values():
        for k, v in default_row().items():
            row.setdefault(k, v)
    return data


def save_state(workspace: Path, state: dict[str, Any]) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["last_updated"] = now_iso()
    save_yaml_atomic(state_path(workspace), state, keep_header=True)


def lock_path(workspace: Path) -> Path:
    """`_memory/.watchlist-state.lock` — a persistent 0-byte sibling of the state file.

    Kept next to its target (like an atomic-write `.tmp` sibling) and never
    unlinked: deleting a lock file while another process waits on it is the
    classic flock race, so the sibling simply stays.
    """
    return workspace / "_memory" / f".{STATE_FILENAME.rsplit('.', 1)[0]}.lock"


@contextlib.contextmanager
def state_lock(workspace: Path) -> Iterator[None]:
    """Exclusive lock for the state file (blocking; concurrent checks serialize)."""
    path = lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _icloud_placeholder(registry: Path, watcher_id: str) -> Path | None:
    """`.<Stem>.ref.md.icloud` for the watcher, whatever the stem's case on disk."""
    if not registry.is_dir():
        return None
    suffix = f"{REF_SUFFIX}.icloud"
    for path in registry.iterdir():
        name = path.name
        if not (name.startswith(".") and name.lower().endswith(suffix)):
            continue
        stem = name[1: -len(suffix)]
        if id_from_stem(stem) == watcher_id:
            return path
    return None


def prune_state(state: dict[str, Any], watcher_ids: set[str], *,
                registry: Path | None = None, now_s: str = "") -> tuple[list[str], list[str]]:
    """Age rows whose ref disappeared; prune after `ORPHAN_PRUNE_RUNS` consecutive runs.

    A first-seen orphan is marked (`orphaned_at`, `orphan_runs`) and warned
    about, so a renamed ref can have its state row carried across before
    anything is lost. A row whose ref is an iCloud placeholder
    (`.<id>.ref.md.icloud` — the file exists but is not downloaded) is never
    aged: the ref is not gone. Returns (pruned_ids, warnings).
    """
    rows = state["watchers"]
    orphans = [wid for wid in rows if wid not in watcher_ids]
    rowless = [wid for wid in watcher_ids if wid not in rows]
    warnings: list[str] = []
    pruned: list[str] = []
    for wid in watcher_ids:
        row = rows.get(wid)
        if isinstance(row, dict):
            row.pop("orphan_runs", None)
            row.pop("orphaned_at", None)
    for wid in orphans:
        row = rows[wid]
        placeholder = _icloud_placeholder(registry, wid) if registry is not None else None
        if placeholder is not None:
            warnings.append(
                f"`{wid}`: ref is an iCloud placeholder ({placeholder.name}) — not downloaded, "
                "not deleted; state kept as-is"
            )
            continue
        runs = int(row.get("orphan_runs") or 0) + 1
        row["orphan_runs"] = runs
        row.setdefault("orphaned_at", now_s or now_iso())
        if runs >= ORPHAN_PRUNE_RUNS:
            del rows[wid]
            pruned.append(wid)
        else:
            warnings.append(
                f"state row `{wid}` has no ref (run {runs}/{ORPHAN_PRUNE_RUNS}; pruned on run "
                f"{ORPHAN_PRUNE_RUNS}) — if the ref was renamed, rename the state key to carry "
                "its history across"
            )
    if orphans and rowless:
        warnings.append(
            "possible rename: state rows without a ref: " + ", ".join(sorted(orphans))
            + "; refs without a state row: " + ", ".join(sorted(rowless))
        )
    return pruned, warnings


def _rebaseline(row: dict[str, Any]) -> None:
    row["fingerprint"] = None
    row["baseline_at"] = None
    row["last_changed"] = None
    row["error_streak"] = 0
    row["error_since"] = None
    row["last_error"] = None
    row["last_outcome"] = None
    row.pop("validators", None)


def _evict(row: dict[str, Any], now_s: str, reason: str) -> None:
    row["status"] = "evicted"
    row["evicted_at"] = now_s
    row["evict_reason"] = reason


# ---------------------------------------------------------------------------
# Probes (declarative, per pack)
# ---------------------------------------------------------------------------


def _expand_path(raw: str, workspace: Path) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else workspace / p


def run_probe(probe: dict[str, Any] | None, *, workspace: Path, source: str,
              allow_cmd: bool = False) -> ProbeResult:
    """Evaluate a pack's `probe:` block. Never raises.

    `cmd_exit_zero` runs a shell command from a pack file, so it is gated by
    the same `preferences.watchlist.allow_cmd` flag as the `cmd` detect.
    """
    if not probe:
        return ProbeResult(source=source, status=ProbeStatus.AVAILABLE, detail="no probe declared")
    kind = str(probe.get("kind") or "always")
    hint = str(probe.get("setup_hint") or "").strip()
    if kind == "cmd_exit_zero" and not allow_cmd:
        return ProbeResult(
            source=source, status=ProbeStatus.NEEDS_SETUP,
            detail="cmd_exit_zero probe disabled (preferences.watchlist.allow_cmd)",
            setup_hint=(hint + " " if hint else "")
            + "Set `preferences.watchlist.allow_cmd: true` in _memory/config.yaml to let "
            "probes run shell commands.",
        )
    try:
        if kind == "always":
            return ProbeResult(source=source, status=ProbeStatus.AVAILABLE)
        if kind == "file_exists":
            p = _expand_path(probe.get("path") or "", workspace)
            if p.exists():
                return ProbeResult(source=source, status=ProbeStatus.AVAILABLE, detail=f"found {p}")
            return ProbeResult(source=source, status=ProbeStatus.NEEDS_SETUP,
                               detail=f"missing {p}", setup_hint=hint)
        if kind == "cli_on_path":
            cli = str(probe.get("cli") or "")
            found = shutil.which(cli) if cli else None
            if found:
                return ProbeResult(source=source, status=ProbeStatus.AVAILABLE, detail=found)
            return ProbeResult(source=source, status=ProbeStatus.NOT_DETECTED,
                               detail=f"`{cli}` not on PATH", setup_hint=hint)
        if kind == "python_import":
            module = str(probe.get("module") or "")
            spec = importlib.util.find_spec(module) if module else None
            if spec is not None:
                return ProbeResult(source=source, status=ProbeStatus.AVAILABLE, detail=module)
            return ProbeResult(source=source, status=ProbeStatus.NOT_DETECTED,
                               detail=f"cannot import `{module}`", setup_hint=hint)
        if kind == "cmd_exit_zero":
            cmd = str(probe.get("cmd") or "")
            proc = subprocess.run(  # noqa: S602 — pack-declared probe command
                cmd, shell=True, capture_output=True, text=True,
                timeout=PROBE_CMD_TIMEOUT, cwd=str(workspace) if workspace.exists() else None,
            )
            if proc.returncode == 0:
                return ProbeResult(source=source, status=ProbeStatus.AVAILABLE, detail=cmd)
            return ProbeResult(source=source, status=ProbeStatus.NEEDS_SETUP,
                               detail=f"exit {proc.returncode}: {(proc.stderr or '').strip()[:120]}",
                               setup_hint=hint)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return ProbeResult(source=source, status=ProbeStatus.NEEDS_SETUP,
                           detail=f"probe failed: {exc}", setup_hint=hint)
    return ProbeResult(source=source, status=ProbeStatus.NEEDS_SETUP,
                       detail=f"unknown probe kind `{kind}`", setup_hint=hint)


# ---------------------------------------------------------------------------
# Detect: url
# ---------------------------------------------------------------------------

_SELECTOR_RE = re.compile(r"^([A-Za-z][\w-]*)?(?:#([\w-]+))?(?:\.([\w-]+))?$")
_START_TAG_RE = re.compile(r"<([A-Za-z][\w-]*)([^>]*)>")
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col",
                        "embed", "source", "track", "wbr"})


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(r"(?:^|\s)" + re.escape(name) + r"\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>]+))", attrs)
    if not m:
        return None
    return m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))


def select_html(html: str, selector: str) -> str | None:
    """Return the outer HTML of the first element matching a *simple* selector.

    Supported: `tag`, `#id`, `.class`, `tag#id`, `tag.class`. Anything more
    (descendant combinators, attribute selectors, pseudo-classes) is rejected
    with DetectError — no HTML parser dependency ships with the framework.
    """
    m = _SELECTOR_RE.match(selector.strip())
    if not m or not any(m.groups()):
        raise DetectError(
            f"unsupported selector {selector!r} (only tag, #id, .class, tag#id, tag.class)"
        )
    tag, id_, cls = m.groups()
    for sm in _START_TAG_RE.finditer(html):
        t, attrs = sm.group(1), sm.group(2)
        if t.lower() in _VOID_TAGS:
            continue
        if tag and t.lower() != tag.lower():
            continue
        if id_ and _attr(attrs, "id") != id_:
            continue
        if cls and cls not in (_attr(attrs, "class") or "").split():
            continue
        if attrs.rstrip().endswith("/"):
            return sm.group(0)
        depth = 1
        tag_re = re.compile(r"<(/?)" + re.escape(t) + r"\b[^>]*>", re.IGNORECASE)
        for em in tag_re.finditer(html, sm.end()):
            if em.group(1) == "/":
                depth -= 1
            elif not em.group(0).rstrip(">").rstrip().endswith("/"):
                depth += 1
            if depth == 0:
                return html[sm.start():em.end()]
        return html[sm.start():]
    return None


def normalize_html(html: str, *, selector: str | None, ignore_patterns: list[str]) -> str:
    """Strip volatile markup and scope the hash (contract § 5 step 3)."""
    if selector:
        picked = select_html(html, selector)
        if picked is None:
            raise DetectError(f"selector {selector!r} matched nothing")
        html = picked
    html = re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    for pat in ignore_patterns:
        html = re.sub(str(pat), "", html, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", html).strip()


class _BoundedRedirects(HTTPRedirectHandler):
    max_redirections = URL_MAX_REDIRECTS


_OPENER = build_opener(_BoundedRedirects)


def _http_open(url: str, method: str, timeout: int, headers: dict[str, str] | None = None):  # noqa: ANN202
    req = Request(url, method=method,
                  headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 — user-configured URL


def _read_bounded(resp: Any, max_bytes: int, deadline: float, timeout: int) -> bytes:
    """Read at most `max_bytes + 1` bytes in chunks, giving up once `deadline` passes.

    urllib's socket timeout only bounds each individual recv; a server that
    trickles bytes could otherwise hold a check open indefinitely.
    """
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        if _monotonic() > deadline:
            raise DetectError(f"body read exceeded the {timeout}s timeout budget")
        chunk = resp.read(min(URL_READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _validators_from(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    etag = headers.get("ETag") if headers is not None else None
    last_mod = headers.get("Last-Modified") if headers is not None else None
    if etag:
        out["etag"] = str(etag).strip()
    if last_mod:
        out["last_modified"] = str(last_mod).strip()
    return out


def detect_url(det: dict[str, Any], *, timeout: int,
               validators: dict[str, str] | None = None) -> DetectResult:
    url = str(det.get("url") or "")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise DetectError(f"url must start with http:// or https:// (got {url!r})")
    if "@" in parts.netloc:
        raise DetectError("url must not carry userinfo (credentials) — use a pack auth pointer")
    selector = det.get("selector")
    ignore = [str(p) for p in (det.get("ignore_patterns") or [])]
    conditional: dict[str, str] = {}
    if validators:
        if validators.get("etag"):
            conditional["If-None-Match"] = validators["etag"]
        if validators.get("last_modified"):
            conditional["If-Modified-Since"] = validators["last_modified"]
    deadline = _monotonic() + timeout
    try:
        with _http_open(url, "GET", timeout, conditional) as resp:
            data = _read_bounded(resp, URL_MAX_BYTES, deadline, timeout)
            charset = resp.headers.get_content_charset() or "utf-8"
            new_validators = _validators_from(resp.headers)
    except HTTPError as exc:
        if exc.code == 304 and conditional:
            return DetectResult(None, "304 Not Modified", {"validators": dict(validators or {})})
        raise DetectError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DetectError(f"network: {getattr(exc, 'reason', exc)}") from exc
    truncated = len(data) > URL_MAX_BYTES
    data = data[:URL_MAX_BYTES]
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    text = normalize_html(text, selector=selector, ignore_patterns=ignore)
    parts_detail = ["GET content hash"]
    if conditional:
        parts_detail.append("validators sent; 200 returned")
    if truncated:
        parts_detail.append(f"truncated at {URL_MAX_BYTES} bytes")
    if selector:
        parts_detail.append(f"selector {selector!r}")
    return DetectResult(_sha256(text.encode("utf-8")), "; ".join(parts_detail),
                        {"validators": new_validators})


# ---------------------------------------------------------------------------
# Detect: path / cmd
# ---------------------------------------------------------------------------


def detect_path(det: dict[str, Any], *, workspace: Path) -> DetectResult:
    raw = str(det.get("path") or "")
    p = _expand_path(raw, workspace)
    if not p.exists():
        raise DetectError(f"path not found: {p}")
    if p.is_file():
        h = hashlib.sha256()
        try:
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError as exc:
            raise DetectError(f"cannot read {p}: {exc}") from exc
        return DetectResult("sha256:" + h.hexdigest(), "file sha256")
    newest = 0.0
    count = 0
    try:
        for root, dirs, files in os.walk(p):
            for name in dirs + files:
                count += 1
                with contextlib.suppress(OSError):
                    newest = max(newest, (Path(root) / name).stat().st_mtime)
    except OSError as exc:
        raise DetectError(f"cannot walk {p}: {exc}") from exc
    return DetectResult(f"dir:{int(newest)}:{count}", f"dir newest mtime + {count} entries")


def detect_cmd(det: dict[str, Any], *, cfg: Config, workspace: Path, timeout: int) -> DetectResult:
    if not cfg.allow_cmd:
        raise DetectError(CMD_DISABLED_REASON)
    cmd = str(det.get("cmd") or "")
    if not cmd.strip():
        raise DetectError("empty cmd")
    try:
        proc = subprocess.run(  # noqa: S602 — user-owned registry, gated by allow_cmd
            cmd, shell=True, capture_output=True, timeout=timeout,
            cwd=str(workspace) if workspace.exists() else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise DetectError(f"cmd timed out after {timeout}s") from exc
    except OSError as exc:
        raise DetectError(f"cmd failed to start: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise DetectError(f"exit {proc.returncode}: {err}" if err else f"exit {proc.returncode}")
    return DetectResult(_sha256(proc.stdout), "stdout sha256")


# ---------------------------------------------------------------------------
# Harvest bridge
# ---------------------------------------------------------------------------


def load_handler(pack: Pack, workspace: Path) -> Any:
    """The pack's harvest handler as an object with `.run(config_row, dry_run)`."""
    if pack.harvest is None:
        raise WatchlistError(f"pack `{pack.id}` declares no harvest")
    entry = _harvest_entry(pack.handler_module())
    if entry is None:
        raise WatchlistError(
            f"pack `{pack.id}`: {HANDLER_FILENAME} exposes neither harvest() nor an IngestorBase subclass"
        )
    if isinstance(entry, type):
        return entry(workspace)
    return _FunctionHarvester(entry, workspace)


def harvest_config_row(watcher: Watcher, row: dict[str, Any],
                       extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """`harvest.defaults` < extra `watch:` keys < `watch.params` < `extra` + last_ingest + id.

    `extra` carries per-invocation flags such as `{"backfill": True}` from
    `harvest --id X --backfill`; a handler honours `backfill_window_days` only
    when `config_row["backfill"]` is truthy.
    """
    assert watcher.harvest is not None
    overrides = {k: v for k, v in watcher.watch_raw.items() if k not in WATCH_FIELDS}
    config_row: dict[str, Any] = {
        **dict(watcher.harvest.get("defaults") or {}),
        **overrides,
        **watcher.params,
        **(extra or {}),
        "last_ingest": row.get("last_harvest") or row.get("last_success"),
        "id": watcher.id,
    }
    max_window = (watcher.budget or {}).get("max_window_days")
    window = config_row.get("recency_window_days")
    if max_window is not None and window is not None:
        with contextlib.suppress(TypeError, ValueError):
            config_row["recency_window_days"] = min(int(window), int(max_window))
    return config_row


def budget_check(watcher: Watcher, row: dict[str, Any], now: dt.datetime) -> str | None:
    """Return a reason string when the harvest must be withheld, else None."""
    budget = watcher.budget or {}
    max_calls = budget.get("max_calls_per_day")
    if max_calls is not None:
        today = now.date().isoformat()
        calls_today = int(row.get("calls_today") or 0) if row.get("calls_today_date") == today else 0
        if calls_today >= int(max_calls):
            return f"max_calls_per_day reached ({calls_today}/{max_calls})"
    min_interval = budget.get("min_interval_minutes")
    if min_interval is not None and row.get("last_harvest"):
        mins = _minutes_between(row["last_harvest"], now)
        if mins is not None and mins < float(min_interval):
            return f"min_interval_minutes not elapsed ({int(mins)}/{min_interval} min)"
    return None


def _mint_ingest_run_id(runs: list[Any], today: dt.date) -> str:
    stamp = today.strftime("%Y%m%d")
    n = 0
    for row in runs:
        if isinstance(row, dict):
            rid = str(row.get("id") or "")
            if rid.startswith(f"ingest-{stamp}-"):
                with contextlib.suppress(ValueError):
                    n = max(n, int(rid.rsplit("-", 1)[-1]))
    return f"ingest-{stamp}-{n + 1:03d}"


def append_ingestion_log(workspace: Path, result: RunResult, *, trigger: str,
                         window: dict[str, Any] | None = None) -> str:
    path = workspace / "_memory" / "ingestion-log.yaml"
    log = load_yaml(path)
    if not isinstance(log, dict):
        log = {"schema_version": 1, "runs": []}
    runs = [r for r in (log.get("runs") or []) if isinstance(r, dict) and r.get("id")]
    run_id = _mint_ingest_run_id(runs, dt.date.today())
    runs.append(result.to_log_row(run_id, trigger, window))
    log["runs"] = runs
    save_yaml_atomic(path, log, keep_header=True)
    return run_id


def append_tailor_signal(workspace: Path, watcher: Watcher, *, run_id: str,
                         errors: list[str], ts: str) -> str | None:
    """One ambient `target: tailor` row per failing harvest run (contract § 9)."""
    path = workspace / "_memory" / "action-signals.yaml"
    data = load_yaml(path)
    if not isinstance(data, dict):
        data = {"schema_version": 1, "signals": []}
    signals = [s for s in (data.get("signals") or []) if isinstance(s, dict) and s.get("id")]
    artifact_ref = f"ingestion-log:{run_id}"
    trigger = f"harvest `{watcher.id}` reported {len(errors)} error(s)"
    if any(s.get("artifact_ref") == artifact_ref and s.get("trigger_phrase") == trigger
           and s.get("status") == "captured" for s in signals):
        return None
    sid = next_id("sig", ts[:10], path)
    pack_label = watcher.pack.id if watcher.pack else watcher.type
    signals.append({
        "id": sid,
        "captured_at": ts,
        "target": "tailor",
        "kind": "ambient",
        "trigger_phrase": trigger,
        "context": (
            f"watchlist harvest for {watcher.handle} via pack `{pack_label}`: "
            + "; ".join(errors)[:300]
        ),
        "artifact_ref": artifact_ref,
        "proposed_direction": None,
        "source_skill": "watch",
        "source_turn": None,
        "status": "captured",
        "became_suggestion": None,
        "became_action": None,
        "processed_at": None,
        "notes": None,
    })
    data["signals"] = signals
    data["last_updated"] = ts
    save_yaml_atomic(path, data, keep_header=True)
    return sid


def refresh_domains(workspace: Path, domains: list[str]) -> list[str]:
    """Best-effort `render_domain.refresh` for a pack's affected_domains."""
    if not domains:
        return []
    try:
        from superagent.tools import render_domain
    except ImportError as exc:
        return [f"render_domain import failed: {exc}"]
    try:
        summary = render_domain.refresh(workspace, [str(d) for d in domains])
    except Exception as exc:  # noqa: BLE001
        return [f"render_domain.refresh failed: {exc}"]
    return list(summary.get("errors", []))


@dc.dataclass
class HarvestOutcome:
    result: RunResult
    run_id: str | None
    changed: bool
    fingerprint: str
    detail: str


def _count_handler_call(row: dict[str, Any], now: dt.datetime) -> None:
    """Every handler call hits the upstream API — dry-run included — so it counts."""
    today = now.date().isoformat()
    calls = int(row.get("calls_today") or 0) if row.get("calls_today_date") == today else 0
    row["calls_today"] = calls + 1
    row["calls_today_date"] = today


def persist_budget_counters(workspace: Path, counters: dict[str, dict[str, Any]]) -> None:
    """Write ONLY `calls_today` / `calls_today_date` for the given ids (dry-run bookkeeping).

    A `--dry-run` writes no state, alerts or logs — but its handler calls did
    reach the API, so the budget counters alone are persisted, on top of
    whatever is on disk.
    """
    if not counters:
        return
    state = load_state(workspace)
    for wid, fields in counters.items():
        row = state["watchers"].setdefault(wid, default_row())
        row["calls_today"] = fields.get("calls_today")
        row["calls_today_date"] = fields.get("calls_today_date")
    save_state(workspace, state)


def run_harvest(
    watcher: Watcher, row: dict[str, Any], *, workspace: Path, now: dt.datetime,
    dry_run: bool, trigger: str, extra: dict[str, Any] | None = None,
) -> HarvestOutcome:
    """Dispatch the handler (budget already checked), record the run, return the delta."""
    assert watcher.pack is not None and watcher.harvest is not None
    handler = load_handler(watcher.pack, workspace)
    _count_handler_call(row, now)
    result = handler.run(harvest_config_row(watcher, row, extra), dry_run=dry_run)
    if not isinstance(result, RunResult):
        raise WatchlistError(f"pack `{watcher.pack.id}`: harvest returned {type(result).__name__}, not RunResult")
    delta = result.items_inserted + result.items_updated
    fingerprint = (
        f"harvest:{result.finished_at}:inserted={result.items_inserted}"
        f":updated={result.items_updated}"
    )
    detail = (
        f"pulled={result.items_pulled} inserted={result.items_inserted} "
        f"updated={result.items_updated} errors={len(result.errors)}"
    )
    run_id: str | None = None
    if not dry_run:
        run_id = append_ingestion_log(workspace, result, trigger=trigger)
        row["last_harvest"] = _iso(now)
        row["last_harvest_result"] = {
            "items_pulled": result.items_pulled,
            "items_inserted": result.items_inserted,
            "items_updated": result.items_updated,
            "errors": list(result.errors),
            "run_id": run_id,
        }
        if result.errors:
            append_tailor_signal(workspace, watcher, run_id=run_id, errors=list(result.errors),
                                 ts=_iso(now))
        errs = refresh_domains(workspace, list(watcher.harvest.get("affected_domains") or []))
        if errs:
            detail += "; domain refresh: " + "; ".join(errs)
    else:
        detail = "dry-run; " + detail
    return HarvestOutcome(result=result, run_id=run_id, changed=delta > 0,
                         fingerprint=fingerprint, detail=detail)


# ---------------------------------------------------------------------------
# Change effects: alerts, interaction-log, world edges
# ---------------------------------------------------------------------------


def alert_text(watcher: Watcher, *, ts: str, summary: str) -> str:
    return f"[{watcher.handle}] {ts}: {summary}"


def append_alert(workspace: Path, watcher: Watcher, *, summary: str, ts: str) -> str | None:
    """One live string alert per watcher; the prior one moves to alerts-archive verbatim."""
    path = workspace / "_memory" / "context.yaml"
    ctx = load_yaml(path)
    if not isinstance(ctx, dict):
        return None
    alerts = ctx.get("alerts")
    if not isinstance(alerts, list):
        alerts = []
    prefix = f"[{watcher.handle}] "
    prior = [a for a in alerts if isinstance(a, str) and a.startswith(prefix)]
    if prior:
        archive_path = workspace / "_memory" / "alerts-archive.yaml"
        archive = load_yaml(archive_path)
        if not isinstance(archive, dict):
            archive = {"schema_version": 1, "archived": []}
        archived = archive.get("archived")
        if not isinstance(archived, list):
            archived = []
        archived.extend({"archived_at": ts, "kind": "alert", "text": a} for a in prior)
        archive["archived"] = archived
        save_yaml_atomic(archive_path, archive, keep_header=True)
        alerts = [a for a in alerts if a not in prior]
    text = alert_text(watcher, ts=ts, summary=summary)
    alerts.append(text)
    ctx["alerts"] = alerts
    save_yaml_atomic(path, ctx, keep_header=True)
    return text


def append_interaction_log(workspace: Path, watcher: Watcher, *, summary: str, ts: str,
                           ingestion_log_ref: str | None = None) -> str | None:
    path = workspace / "_memory" / "interaction-log.yaml"
    data = load_yaml(path)
    if not isinstance(data, dict):
        data = {"schema_version": 1, "entries": []}
    entries = [
        e for e in (data.get("entries") or [])
        if isinstance(e, dict) and not (e.get("id") is None and not e.get("action") and not e.get("summary"))
    ]
    rid = next_id("ilog", ts[:10], path)
    entries.append({
        "id": rid,
        "ts": ts,
        "skill": "watch",
        "action": "watch_change_detected",
        "summary": f"{watcher.handle} — {summary}",
        "related_domain": watcher.related.get("related_domain"),
        "related_project": watcher.related.get("related_project"),
        "related_asset": watcher.related.get("related_asset"),
        "related_account": watcher.related.get("related_account"),
        "action_items": [],
        "ingestion_log_ref": ingestion_log_ref,
    })
    data["entries"] = entries
    save_yaml_atomic(path, data, keep_header=True)
    return rid


def _edge_targets(watcher: Watcher) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for field, value in watcher.related.items():
        kind = field.replace("related_", "", 1)
        out.append((value if ":" in value else f"{kind}:{value}", field))
    return out


def sync_world_edges(workspace: Path, watchers: list[Watcher]) -> list[str]:
    """Ensure `watch:<id>` nodes + related_* edges exist (contract § 9). Idempotent, churn-free."""
    world_file = workspace / "_memory" / "world.yaml"
    if not world_file.exists() or not watchers:
        return []
    try:
        from superagent.tools import world
    except ImportError:
        return []
    written: list[str] = []
    try:
        data = world.load_world(workspace)
        nodes = {n.get("id") for n in data.get("nodes", []) if isinstance(n, dict)}
        edges = {(e.get("from"), e.get("to"), e.get("kind"))
                 for e in data.get("edges", []) if isinstance(e, dict)}
        for w in watchers:
            rel_path = _rel(workspace, w.path)
            if w.handle not in nodes:
                world.ensure_node(workspace, w.handle, HANDLE_KIND, rel_path,
                                  label=w.title, tags=list(w.tags))
                written.append(w.handle)
            for target, kind in _edge_targets(w):
                if (w.handle, target, kind) not in edges:
                    world.ensure_edge(workspace, w.handle, target, kind, evidence=rel_path)
                    written.append(target)
    except Exception as exc:  # noqa: BLE001 — the graph is derived; never fail a check on it
        print(f"watchlist: world edge write skipped: {exc}", file=sys.stderr)
    return written


def apply_change_effects(workspace: Path, watcher: Watcher, *, summary: str, ts: str,
                         ingestion_log_ref: str | None = None) -> dict[str, Any]:
    alert = append_alert(workspace, watcher, summary=summary, ts=ts)
    ilog = append_interaction_log(workspace, watcher, summary=summary, ts=ts,
                                  ingestion_log_ref=ingestion_log_ref)
    edges = sync_world_edges(workspace, [watcher])
    return {"alert": alert, "interaction_log_id": ilog, "world_edges": edges}


# ---------------------------------------------------------------------------
# Check run
# ---------------------------------------------------------------------------


def empty_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {"summary": dict.fromkeys(SUMMARY_KEYS, 0)}
    for key in SUMMARY_KEYS[1:]:
        payload[key] = []
    for key in EXTRA_BUCKETS:
        payload[key] = []
    return payload


def _item(watcher: Watcher, **extra: Any) -> dict[str, Any]:
    base = {"id": watcher.id, "title": watcher.title, "type": watcher.type,
            "handle": watcher.handle}
    if watcher.pack:
        base["pack"] = watcher.pack.id
    base.update(extra)
    return base


def _bucket(payload: dict[str, Any], key: str, item: dict[str, Any]) -> None:
    payload[key].append(item)
    if key in payload["summary"]:
        payload["summary"][key] += 1


@dc.dataclass
class CheckContext:
    workspace: Path
    framework: Path
    cfg: Config
    cycle: str
    timeout: int = DEFAULT_TIMEOUT
    dry_run: bool = False
    no_harvest: bool = False
    only_id: str | None = None
    now: dt.datetime = dc.field(default_factory=_now)

    @property
    def now_iso(self) -> str:
        return _iso(self.now)


def stamp_command(watcher_id: str) -> str:
    return (
        f"uv run python -m superagent.tools.watchlist stamp --id {watcher_id} "
        "(--changed | --unchanged | --unreachable) [--note \"<one line>\"]"
    )


def subagent_dispatch_spec(watcher: Watcher, row: dict[str, Any]) -> dict[str, Any]:
    """Contract § 10. `previous_note` is a labelled DATA field — never in the prompt."""
    prompt = str(watcher.detect.get("prompt") or "").strip()
    return {
        "kind": "subagent",
        "id": watcher.id,
        "title": watcher.title,
        "handle": watcher.handle,
        "prompt": prompt + "\n\n" + RETENTION_NOTE,
        "previous_note": row.get("fingerprint"),
        "previous_note_label": PREVIOUS_NOTE_LABEL,
        "last_changed": row.get("last_changed"),
        "last_checked": row.get("last_checked"),
        "description": watcher.description,
        "return_shape": RETURN_SHAPE,
        "stamp_command": stamp_command(watcher.id),
    }


def harvest_dispatch_spec(watcher: Watcher, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Contract § 8: a `capture_mode: manual` harvest awaits the user's explicit yes."""
    return {
        "kind": "harvest",
        "id": watcher.id,
        "title": watcher.title,
        "handle": watcher.handle,
        "pack": watcher.pack.id if watcher.pack else None,
        "last_harvest": row.get("last_harvest"),
        "reason": reason,
        "command": f"uv run python -m superagent.tools.watchlist harvest --id {watcher.id}",
    }


def evaluate_eviction(watcher: Watcher, row: dict[str, Any], now: dt.datetime) -> str | None:
    """Mark-only eviction (contract § 7 step 6). Returns the reason when evicted this call."""
    if watcher.evict_after_days is None or row.get("status") != "active":
        return None
    if row.get("last_outcome") == "changed":
        return None
    anchors = [t for t in (_parse_iso(row.get("baseline_at")), _parse_iso(row.get("last_changed"))) if t]
    if not anchors:
        return None  # never baselined — nothing to age
    anchor = max(anchors)
    window = dt.timedelta(days=watcher.evict_after_days)
    if now - anchor <= window:
        return None
    last_success = _parse_iso(row.get("last_success"))
    if last_success is None or last_success <= now - window:
        return None  # no successful check inside the window: unreachable, kept
    _evict(row, _iso(now), "stale")
    return "stale"


def _detect(ctx: CheckContext, watcher: Watcher, row: dict[str, Any]) -> DetectResult:
    det = watcher.detect
    if watcher.type == "url":
        validators = row.get("validators") if isinstance(row.get("validators"), dict) else None
        return detect_url(det, timeout=ctx.timeout,
                          validators=validators if row.get("fingerprint") else None)
    if watcher.type == "path":
        return detect_path(det, workspace=ctx.workspace)
    if watcher.type == "cmd":
        return detect_cmd(det, cfg=ctx.cfg, workspace=ctx.workspace, timeout=ctx.timeout)
    if watcher.pack is None or not watcher.pack.provides_detect:
        raise DetectError(f"no detect implementation for type `{watcher.type}`")
    result = watcher.pack.handler_module().detect(DetectContext(
        watcher_id=watcher.id, detect=dict(det), workspace=ctx.workspace,
        framework=ctx.framework, timeout=ctx.timeout, dry_run=ctx.dry_run,
        row=dict(row), now=ctx.now,
    ))
    if not isinstance(result, DetectResult):
        raise DetectError(
            f"pack `{watcher.pack.id}` detect() returned {type(result).__name__}, not DetectResult"
        )
    return result


def _record_failure(row: dict[str, Any], now_s: str, error: str) -> None:
    row["last_checked"] = now_s
    row["error_streak"] = int(row.get("error_streak") or 0) + 1
    row["error_since"] = row.get("error_since") or now_s
    row["last_error"] = error
    row["last_outcome"] = "unreachable"


def _record_success(row: dict[str, Any], now_s: str) -> None:
    row["last_checked"] = now_s
    row["last_success"] = now_s
    row["error_streak"] = 0
    row["error_since"] = None
    row["last_error"] = None


def _first_report_note(watcher: Watcher) -> str | None:
    if watcher.type == "cmd":
        return f"First check for cmd watcher `{watcher.id}` runs: {watcher.locator}"
    if watcher.type == "url":
        parts = urlsplit(watcher.locator)
        return f"First check for url watcher `{watcher.id}`: {parts.netloc}{parts.path or '/'}"
    return None


def _apply_fingerprint(
    ctx: CheckContext, payload: dict[str, Any], watcher: Watcher, row: dict[str, Any],
    detected: DetectResult, *, forced_changed: bool | None = None,
    ingestion_log_ref: str | None = None,
) -> str:
    """Compare against the stored fingerprint; write effects. Returns the outcome."""
    now_s = ctx.now_iso
    _record_success(row, now_s)
    row.update(detected.state or {})
    if "validators" in row and not row["validators"]:
        row.pop("validators")
    prev = row.get("fingerprint")
    fingerprint = detected.fingerprint if detected.fingerprint is not None else prev
    if fingerprint is None:
        raise DetectError("not-modified response without a stamped fingerprint")
    if prev is None:
        row["baseline_at"] = now_s
        note = _first_report_note(watcher)
        if note:
            payload["notes"].append(note)
        if not forced_changed:
            row["fingerprint"] = fingerprint
            row["last_outcome"] = "unchanged"
            _bucket(payload, "unchanged", _item(watcher, detail=f"baseline captured; {detected.detail}"))
            return "unchanged"
        # A harvest that inserted rows on its very first run IS a change worth
        # alerting on — never a silent baseline.
        is_changed = True
    else:
        is_changed = (fingerprint != prev) if forced_changed is None else forced_changed
    if is_changed:
        min_change = watcher.detect.get("min_change_interval_minutes")
        if min_change is not None and row.get("last_changed"):
            mins = _minutes_between(row["last_changed"], ctx.now)
            if mins is not None and mins < float(min_change):
                # Flap guard: fingerprint advances silently; last_changed untouched.
                row["fingerprint"] = fingerprint
                row["last_outcome"] = "unchanged"
                _bucket(payload, "unchanged", _item(
                    watcher, detail=f"change suppressed (min_change_interval_minutes={min_change})"
                ))
                return "unchanged"
        row["fingerprint"] = fingerprint
        row["last_changed"] = now_s
        row["last_outcome"] = "changed"
        item = _item(watcher, detail=detected.detail, fingerprint=fingerprint, previous=prev)
        if not ctx.dry_run:
            item["effects"] = apply_change_effects(
                ctx.workspace, watcher, summary=detected.detail or "fingerprint moved", ts=now_s,
                ingestion_log_ref=ingestion_log_ref,
            )
        _bucket(payload, "changed", item)
        return "changed"
    row["last_outcome"] = "unchanged"
    _bucket(payload, "unchanged", _item(watcher, detail=detected.detail))
    return "unchanged"


def _harvest_gate(ctx: CheckContext, watcher: Watcher, row: dict[str, Any],
                  payload: dict[str, Any]) -> bool:
    """capture_mode / --no-harvest gate for cadence runs (budget is separate)."""
    if watcher.capture_mode == "manual":
        _bucket(payload, "dispatch", harvest_dispatch_spec(watcher, row, reason="capture_mode manual"))
        return False
    if ctx.no_harvest:
        payload["skipped_harvest"].append(_item(watcher, reason="--no-harvest"))
        return False
    return True


def _try_harvest(ctx: CheckContext, payload: dict[str, Any], watcher: Watcher,
                 row: dict[str, Any], *, trigger: str,
                 extra: dict[str, Any] | None = None) -> HarvestOutcome | None:
    reason = budget_check(watcher, row, ctx.now)
    if reason:
        _bucket(payload, "budget_exceeded", _item(watcher, reason=reason))
        return None
    try:
        outcome = run_harvest(watcher, row, workspace=ctx.workspace, now=ctx.now,
                              dry_run=ctx.dry_run, trigger=trigger, extra=extra)
    except WatchlistError as exc:
        _bucket(payload, "errors", _item(watcher, error=str(exc)))
        return None
    except Exception as exc:  # noqa: BLE001 — a handler crash is a reportable error
        _bucket(payload, "errors", _item(watcher, error=f"harvest raised {type(exc).__name__}: {exc}"))
        return None
    _bucket(payload, "harvested", _item(
        watcher, run_id=outcome.run_id, detail=outcome.detail,
        items_inserted=outcome.result.items_inserted, errors=list(outcome.result.errors),
    ))
    return outcome


def _apply_harvest_outcome(ctx: CheckContext, payload: dict[str, Any], watcher: Watcher,
                           row: dict[str, Any], outcome: HarvestOutcome) -> None:
    payload["summary"]["checked"] += 1
    if outcome.result.errors and outcome.result.items_pulled == 0:
        _record_failure(row, ctx.now_iso, "; ".join(outcome.result.errors)[:300])
        _bucket(payload, "unreachable", _item(watcher, error=row["last_error"]))
        return
    _apply_fingerprint(ctx, payload, watcher, row, DetectResult(outcome.fingerprint, outcome.detail),
                       forced_changed=outcome.changed, ingestion_log_ref=outcome.run_id)


def check_one(ctx: CheckContext, payload: dict[str, Any], watcher: Watcher,
              state: dict[str, Any]) -> None:
    rows = state["watchers"]
    row = rows.get(watcher.id)
    if row is None:
        row = default_row()
        rows[watcher.id] = row
    now_s = ctx.now_iso
    explicit = ctx.only_id is not None

    # 1. Load: enabled: false freezes; flipping back re-baselines and re-arms.
    if not watcher.enabled:
        if row.get("status") != "disabled":
            row["status"] = "disabled"
        payload["disabled"].append(_item(watcher, reason="enabled: false"))
        return
    if row.get("status") == "disabled":
        _rebaseline(row)
        row["status"] = "active"
        payload["warnings"].append(f"`{watcher.id}` re-enabled; fingerprint re-baselined")

    # 7. Revive: `status: active` edited after the eviction; otherwise evicted rows are skipped.
    if row.get("status") == "evicted":
        evicted_at = _parse_iso(row.get("evicted_at"))
        revive = watcher.status == "active" and (
            evicted_at is None or watcher.mtime > evicted_at.timestamp()
        )
        if not revive:
            payload["inactive"].append(_item(watcher, status="evicted", reason=row.get("evict_reason")))
            return
        _rebaseline(row)
        row["status"] = "active"
        row["evicted_at"] = None
        row["evict_reason"] = None
        payload["warnings"].append(f"`{watcher.id}` revived (status: active); re-baselined")

    # 2. Throttle gate — first. 3. Cycle gate. `--id` bypasses both.
    if not explicit:
        if watcher.min_check_interval_minutes and row.get("last_checked"):
            mins = _minutes_between(row["last_checked"], ctx.now)
            if mins is not None and mins < float(watcher.min_check_interval_minutes):
                _bucket(payload, "skipped_throttled", _item(
                    watcher, minutes_since_check=int(mins),
                    min_check_interval_minutes=watcher.min_check_interval_minutes,
                ))
                return
        if not cycle_covers(ctx.cycle, watcher.cycles):
            _bucket(payload, "skipped_cycle", _item(watcher, cycles=watcher.cycles))
            return

    # 4. Expiry.
    if watcher.expires is not None and ctx.now.date() > watcher.expires:
        _evict(row, now_s, "expired")
        _bucket(payload, "evicted", _item(watcher, reason="expired", expires=watcher.expires.isoformat()))
        return

    # 5. Detect.
    if watcher.type == "subagent":
        # A watcher whose quiet window has already run out is evicted now, not
        # dispatched one last time.
        reason = evaluate_eviction(watcher, row, ctx.now)
        if reason:
            _bucket(payload, "evicted", _item(watcher, reason=reason))
            return
        _bucket(payload, "dispatch", subagent_dispatch_spec(watcher, row))
        return
    if watcher.type == "harvest":
        if _harvest_gate(ctx, watcher, row, payload):
            outcome = _try_harvest(ctx, payload, watcher, row, trigger="scheduled")
            if outcome is not None:
                _apply_harvest_outcome(ctx, payload, watcher, row, outcome)
    else:
        payload["summary"]["checked"] += 1
        try:
            detected = _detect(ctx, watcher, row)
            result = _apply_fingerprint(ctx, payload, watcher, row, detected)
        except DetectError as exc:
            _record_failure(row, now_s, str(exc))
            _bucket(payload, "unreachable", _item(watcher, error=str(exc)))
        except Exception as exc:  # noqa: BLE001 — never let one watcher abort the run
            _record_failure(row, now_s, f"{type(exc).__name__}: {exc}")
            _bucket(payload, "unreachable", _item(watcher, error=row["last_error"]))
        else:
            if result == "changed" and watcher.harvest is not None and _harvest_gate(
                ctx, watcher, row, payload
            ):
                _try_harvest(ctx, payload, watcher, row, trigger="scheduled")

    # 6. Eviction (mark-only).
    reason = evaluate_eviction(watcher, row, ctx.now)
    if reason:
        _bucket(payload, "evicted", _item(watcher, reason=reason))


def run_check(ctx: CheckContext) -> dict[str, Any] | None:
    """Run one cycle. Returns the JSON payload, or None when the feature is off."""
    folder = registry_dir(ctx.workspace, ctx.cfg)
    if not folder.is_dir():
        return None
    payload = empty_payload()
    packs, pack_errors = discover_packs(ctx.framework, ctx.workspace)
    for err in pack_errors:
        _bucket(payload, "errors", {"id": None, "error": err})
    watchers, reg_errors = load_registry(ctx.workspace, ctx.cfg, packs)
    for err in reg_errors:
        _bucket(payload, "errors", err)
    reg_rel = _rel(ctx.workspace, folder)
    payload["warnings"].extend(f"{reg_rel}/{msg}" for msg in registry_stray_files(folder))
    if ctx.only_id is not None and not any(w.id == ctx.only_id for w in watchers):
        _bucket(payload, "errors", {"id": ctx.only_id, "error": f"no watcher `{ctx.only_id}`"})
    with state_lock(ctx.workspace):
        state = load_state(ctx.workspace)
        known_ids = {w.id for w in watchers} | {e["id"] for e in reg_errors if e.get("id")}
        pruned, warnings = prune_state(state, known_ids, registry=folder, now_s=ctx.now_iso)
        payload["warnings"].extend(warnings)
        if pruned:
            payload["warnings"].append("pruned state rows: " + ", ".join(sorted(pruned)))
        if not ctx.dry_run:
            sync_world_edges(ctx.workspace, watchers)
        for watcher in watchers:
            if ctx.only_id is not None and watcher.id != ctx.only_id:
                continue
            check_one(ctx, payload, watcher, state)
        if not ctx.dry_run:
            save_state(ctx.workspace, state)
        else:
            persist_budget_counters(ctx.workspace, {
                h["id"]: {k: state["watchers"][h["id"]].get(k)
                          for k in ("calls_today", "calls_today_date")}
                for h in payload["harvested"] if h["id"] in state["watchers"]
            })
    payload["cycle"] = ctx.cycle
    payload["dry_run"] = ctx.dry_run
    payload["checked_at"] = ctx.now_iso
    return payload


# ---------------------------------------------------------------------------
# Stamp
# ---------------------------------------------------------------------------


def run_stamp(workspace: Path, framework: Path, *, watcher_id: str, outcome: str,
              note: str | None) -> tuple[int, dict[str, Any]]:
    cfg = load_config(workspace)
    packs, _ = discover_packs(framework, workspace)
    watchers, errors = load_registry(workspace, cfg, packs)
    watcher = next((w for w in watchers if w.id == watcher_id), None)
    if watcher is None:
        err = next((e for e in errors if e.get("id") == watcher_id), None)
        return 2, {"error": err["error"] if err else f"no watcher `{watcher_id}`"}
    clean = sanitize_note(note)
    now = _now()
    now_s = _iso(now)
    result: dict[str, Any] = {"id": watcher.id, "outcome": outcome, "note": clean}
    if watcher.type != "subagent":
        result["warning"] = f"`{watcher.id}` is a {watcher.type} watcher; stamping is meant for subagent"
    with state_lock(workspace):
        state = load_state(workspace)
        row = state["watchers"].setdefault(watcher.id, default_row())
        if row.get("status") == "evicted":
            _rebaseline(row)
            row["status"] = "active"
            row["evicted_at"] = None
            row["evict_reason"] = None
            result["revived"] = True
        if outcome == "unreachable":
            _record_failure(row, now_s, clean or "subagent reported unreachable")
        else:
            _record_success(row, now_s)
            if row.get("baseline_at") is None:
                row["baseline_at"] = now_s
            if outcome == "changed":
                row["fingerprint"] = clean or f"changed {now_s}"
                row["last_changed"] = now_s
                row["last_outcome"] = "changed"
                result["effects"] = apply_change_effects(
                    workspace, watcher, summary=clean or "subagent reported a change", ts=now_s
                )
            else:
                if row.get("fingerprint") is None:
                    row["fingerprint"] = clean or "no change"
                row["last_outcome"] = "unchanged"
        reason = evaluate_eviction(watcher, row, now)
        if reason:
            result["evicted"] = reason
        save_state(workspace, state)
        result["state"] = dict(row)
    return 0, result


# ---------------------------------------------------------------------------
# list / enable / probe / harvest
# ---------------------------------------------------------------------------


def list_rows(workspace: Path, framework: Path, *, status: str | None = None) -> list[dict[str, Any]]:
    cfg = load_config(workspace)
    packs, _ = discover_packs(framework, workspace)
    watchers, errors = load_registry(workspace, cfg, packs)
    state = load_state(workspace)["watchers"]
    rows: list[dict[str, Any]] = []
    for w in watchers:
        row = state.get(w.id) or {}
        eff = "disabled" if not w.enabled else str(row.get("status") or "active")
        rows.append({
            "id": w.id, "title": w.title, "type": w.type, "pack": w.pack.id if w.pack else None,
            "status": eff, "cycles": w.cycles, "capture_mode": w.capture_mode,
            "schedule": w.schedule, "evict_after_days": w.evict_after_days,
            "min_check_interval_minutes": w.min_check_interval_minutes,
            "last_checked": row.get("last_checked"), "last_changed": row.get("last_changed"),
            "last_outcome": row.get("last_outcome"), "error_streak": row.get("error_streak", 0),
            "file": _rel(workspace, w.path), "related": w.related,
        })
    for e in errors:
        rows.append({"id": e.get("id"), "status": "error", "error": e.get("error"), "file": e.get("file")})
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def _parse_kv(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise WatchlistError(f"--param expects k=v (got {pair!r})")
        out[key.strip()] = value
    return out


def render_ref(framework: Path, fields: dict[str, Any]) -> str:
    """Frontmatter + Notes body from `templates/sources/ref.md` (or the builtin)."""
    template_path = framework / "templates" / "sources" / "ref.md"
    text = BUILTIN_REF_TEMPLATE
    if template_path.is_file():
        with contextlib.suppress(OSError):
            text = template_path.read_text(encoding="utf-8")
    try:
        base, body = parse_frontmatter(text)
    except WatchlistError:
        base, body = parse_frontmatter(BUILTIN_REF_TEMPLATE)
    fm: dict[str, Any] = dict(base or {})
    # Blank template placeholders ("<short title>") the caller did not fill in.
    for key, value in list(fm.items()):
        if isinstance(value, str) and value.startswith("<") and value.endswith(">"):
            fm[key] = ""
    for key, value in fields.items():
        if key == "watch" and isinstance(fm.get("watch"), dict) and isinstance(value, dict):
            merged = dict(fm["watch"])
            merged.update(value)
            fm["watch"] = merged
        else:
            fm[key] = value
    fm.setdefault("ref_version", REF_VERSION)
    fm["ref_version"] = REF_VERSION
    for legacy in LEGACY_REF_KEYS:
        fm.pop(legacy, None)
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = body if body.strip() else "\n# Notes\n"
    return f"---\n{front}---\n{body.lstrip(chr(10))}"


def _describe(pack: Pack, detect: dict[str, Any], params: dict[str, Any]) -> str:
    """One-line `description` for an `enable`d ref: what this instance points at.

    A non-parameterized pack (e.g. `simplefin`) contributes its own
    description; a parameterized one is described by its locator (the URL,
    the command, the query, the prompt's first line) so the ref is legible
    in `list` and in a briefing without opening it.
    """
    if not pack.parameterized:
        return pack.description or pack.title
    dtype = pack.detect_type
    if dtype in LOCATOR_KEY:
        locator = str(detect.get(LOCATOR_KEY[dtype]) or "").strip()
        if dtype == "subagent":
            first_line = locator.splitlines()[0] if locator else ""
            return first_line[:120] or pack.title
        return locator or pack.title
    # Pack-defined type: `<pack id>: <primary param>` (the first required param).
    primary = next((n for n, spec in pack.params.items() if spec.get("required")), None)
    value = str(params.get(primary) or "").strip() if primary else ""
    return f"{pack.title}: {value}" if value else pack.title


def enable_pack(workspace: Path, framework: Path, *, pack_id: str, watcher_id: str | None,
                params: dict[str, str], title: str | None) -> Path:
    """Write `<Title_Case id>.ref.md` for a pack (contract § 4). Never overwrites.

    The existence check is case-insensitive, like loading: `--id simplefin`
    refuses when `simplefin.ref.md` or `SIMPLEFIN.ref.md` is already there.
    """
    cfg = load_config(workspace)
    packs, _ = discover_packs(framework, workspace)
    pack = packs.get(pack_id)
    if pack is None:
        known = ", ".join(sorted(packs)) or "(none)"
        raise WatchlistError(f"unknown pack `{pack_id}`; available: {known}")
    wid = id_from_stem(watcher_id or pack.id)
    if not ID_RE.match(wid):
        raise WatchlistError(
            f"id {wid!r} is not a valid watcher id ({ID_RULE}); try `--id {slugify_id(wid)}`"
        )
    folder = registry_dir(workspace, cfg)
    existing = find_ref(folder, wid)
    if existing is not None:
        raise WatchlistError(f"{_rel(workspace, existing)} already exists (id `{wid}`)")
    target = folder / ref_filename(wid)
    resolved = resolve_params(pack, dict(params))
    detect = render_detect(pack, resolved)
    fields: dict[str, Any] = {
        "title": title or (pack.title if wid == pack.id else f"{wid} ({pack.title})"),
        "description": _describe(pack, detect, resolved),
        "added_by": "watch",
        "added_at": now_iso(),
        "watch": {"pack": pack.id, "enabled": True},
    }
    if params:
        fields["watch"]["params"] = dict(params)
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(render_ref(framework, fields), encoding="utf-8")
    return target


def probe_rows(workspace: Path, framework: Path, *, watcher_id: str | None,
               all_watchers: bool) -> list[dict[str, Any]]:
    """No args: every discovered pack. `<id>`: that watcher's pack. `--all`: every watcher."""
    cfg = load_config(workspace)
    packs, pack_errors = discover_packs(framework, workspace)
    rows: list[dict[str, Any]] = []
    if watcher_id is None and not all_watchers:
        for pack in sorted(packs.values(), key=lambda p: p.id):
            res = run_probe(pack.probe, workspace=workspace, source=pack.id, allow_cmd=cfg.allow_cmd)
            rows.append({"pack": pack.id, "origin": pack.origin,
                         "probe": str((pack.probe or {}).get("kind") or "none"),
                         "status": res.status, "detail": res.detail, "setup_hint": res.setup_hint})
        for err in pack_errors:
            rows.append({"pack": None, "origin": None, "probe": None, "status": "error",
                         "detail": err, "setup_hint": ""})
        if (framework / "watchers" / "_manifest.yaml").is_file():
            for missing in sorted(SHIPPED_PACK_IDS - set(packs)):
                rows.append({"pack": missing, "origin": "framework", "probe": None,
                             "status": "error", "setup_hint": "",
                             "detail": f"shipped pack `{missing}` not found under watchers/"})
        return rows
    watchers, errors = load_registry(workspace, cfg, packs)
    for w in watchers:
        if watcher_id is not None and w.id != watcher_id:
            continue
        if w.pack is None:
            rows.append({"id": w.id, "pack": None, "status": "n/a",
                         "detail": f"bare {w.type} watcher; no pack probe", "setup_hint": ""})
            continue
        res = run_probe(w.probe, workspace=workspace, source=w.id, allow_cmd=cfg.allow_cmd)
        rows.append({"id": w.id, "pack": w.pack.id, "status": res.status,
                     "detail": res.detail, "setup_hint": res.setup_hint})
    for e in errors:
        if watcher_id is None or e.get("id") == watcher_id:
            rows.append({"id": e.get("id"), "pack": None, "status": "error",
                         "detail": e.get("error"), "setup_hint": ""})
    if watcher_id is not None and not rows:
        rows.append({"id": watcher_id, "pack": None, "status": "error",
                     "detail": f"no watcher `{watcher_id}`", "setup_hint": ""})
    return rows


def run_explicit_harvest(workspace: Path, framework: Path, *, watcher_id: str,
                         dry_run: bool, backfill: bool = False) -> tuple[int, dict[str, Any]]:
    cfg = load_config(workspace)
    packs, _ = discover_packs(framework, workspace)
    watchers, errors = load_registry(workspace, cfg, packs)
    watcher = next((w for w in watchers if w.id == watcher_id), None)
    if watcher is None:
        err = next((e for e in errors if e.get("id") == watcher_id), None)
        return 2, {"error": err["error"] if err else f"no watcher `{watcher_id}`"}
    if watcher.harvest is None:
        return 2, {"error": f"`{watcher_id}` has no harvest handler (type {watcher.type})"}
    ctx = CheckContext(workspace=workspace, framework=framework, cfg=cfg, cycle="manual",
                       dry_run=dry_run)
    payload = empty_payload()
    extra = {"backfill": True} if backfill else None
    with state_lock(workspace):
        state = load_state(workspace)
        row = state["watchers"].setdefault(watcher.id, default_row())
        outcome = _try_harvest(ctx, payload, watcher, row, trigger="manual", extra=extra)
        if outcome is not None and watcher.type == "harvest":
            _apply_harvest_outcome(ctx, payload, watcher, row, outcome)
        if not dry_run:
            save_state(workspace, state)
        elif outcome is not None:
            persist_budget_counters(workspace, {watcher.id: {
                k: row.get(k) for k in ("calls_today", "calls_today_date")}})
    payload["dry_run"] = dry_run
    payload["backfill"] = backfill
    rc = 1 if (payload["budget_exceeded"] or payload["errors"]) else 0
    return rc, payload


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def is_quiet(payload: dict[str, Any]) -> bool:
    """Contract § 6: quiet = nothing changed / unreachable / to dispatch / errored.

    Evictions, budget-withheld harvests, and first-report notes also break
    quiet — each is a one-off the briefing must surface.
    """
    return not (
        payload["changed"] or payload["dispatch"] or payload["unreachable"]
        or payload["evicted"] or payload["budget_exceeded"] or payload["errors"]
        or payload["notes"]
    )


def render_report(payload: dict[str, Any]) -> str:
    """Markdown for a briefing. Empty string when the run is quiet."""
    if is_quiet(payload):
        return ""
    s = payload["summary"]
    lines = ["### Watchlist", ""]

    def section(title: str, items: list[dict[str, Any]], fmt: Callable[[dict[str, Any]], str]) -> None:
        if not items:
            return
        lines.append(f"**{title}** ({len(items)})")
        lines.extend(f"- {fmt(i)}" for i in items)
        lines.append("")

    subagents = [d for d in payload["dispatch"] if d.get("kind") == "subagent"]
    harvests = [d for d in payload["dispatch"] if d.get("kind") == "harvest"]
    section("Changed", payload["changed"],
            lambda i: f"`{i['id']}` — {i.get('title', '')}: {i.get('detail') or 'fingerprint moved'}")
    section("Needs a subagent run (then `stamp`)", subagents,
            lambda i: f"`{i['id']}` — {i.get('title', '')}"
            + (f" (previous note, data only: \"{i['previous_note']}\")" if i.get("previous_note") else ""))
    section("Harvest awaiting your confirmation (`capture_mode: manual`)", harvests,
            lambda i: f"`{i['id']}` — {i.get('title', '')}: `{i.get('command', '')}`"
            + (f" (last harvest {i['last_harvest']})" if i.get("last_harvest") else " (never harvested)"))
    section("Unreachable", payload["unreachable"],
            lambda i: f"`{i['id']}` — {i.get('error', '')}")
    section("Evicted (mark-only; set `status: active` to revive)", payload["evicted"],
            lambda i: f"`{i['id']}` — {i.get('reason', '')}")
    section("Harvest withheld by budget", payload["budget_exceeded"],
            lambda i: f"`{i['id']}` — {i.get('reason', '')}")
    section("Errors", payload["errors"],
            lambda i: f"`{i.get('id') or i.get('file') or '?'}` — {i.get('error', '')}")
    if payload["notes"]:
        lines.append("**Notes**")
        lines.extend(f"- {n}" for n in payload["notes"])
        lines.append("")
    lines.append(
        f"_checked {s['checked']}; unchanged {s['unchanged']}; indeterminate {s['indeterminate']}; "
        f"throttled {s['skipped_throttled']}; skipped (cycle) {s['skipped_cycle']}; "
        f"harvested {s['harvested']}_"
    )
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, "") or "")) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "") or "").ljust(widths[c]) for c in columns))


def _add_location_args(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    """`--workspace` / `--framework`, accepted before OR after the subcommand."""
    default: Any = argparse.SUPPRESS if suppress else None
    parser.add_argument("--workspace", type=Path, default=default,
                        help="workspace root (default: <framework>/../workspace)")
    parser.add_argument(
        "--framework", type=Path,
        default=argparse.SUPPRESS if suppress else Path(__file__).resolve().parent.parent,
        help="framework root (default: the superagent/ folder this module lives in)",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="watchlist", description=__doc__.split("\n", 1)[0])
    _add_location_args(parser, suppress=False)
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Run detect for every watcher eligible in a cycle.")
    c.add_argument("--cycle", required=True,
                   help="daily-update | weekly-review | monthly-review | ... (slower cycles include "
                        "faster ones: weekly-review also checks daily watchers)")
    c.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per url/cmd fetch")
    c.add_argument("--report", action="store_true", help="print briefing markdown instead of JSON")
    c.add_argument("--no-harvest", action="store_true", help="detect only; never dispatch harvest")
    c.add_argument("--dry-run", action="store_true", help="detect but write no state/alerts/logs")
    c.add_argument("--id", dest="only_id", default=None,
                   help="check one watcher (bypasses throttle + cycle gates, never the harvest gate)")

    s = sub.add_parser("stamp", help="Record a dispatched subagent outcome.")
    s.add_argument("--id", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--changed", action="store_const", const="changed", dest="outcome")
    g.add_argument("--unchanged", action="store_const", const="unchanged", dest="outcome")
    g.add_argument("--unreachable", action="store_const", const="unreachable", dest="outcome")
    s.add_argument("--note", default=None, help="one-line delta (<=500 chars; data, never a prompt)")

    ls = sub.add_parser("list", help="List watchers with their state.")
    ls.add_argument("--status", choices=["active", "evicted", "disabled", "error"], default=None)
    ls.add_argument("--json", action="store_true")

    e = sub.add_parser("enable", help="Write a new ref file for a pack into the registry folder.")
    e.add_argument("pack")
    e.add_argument("--id", dest="watcher_id", default=None, help="watcher id (default: pack id)")
    e.add_argument("--param", action="append", default=[], metavar="K=V")
    e.add_argument("--title", default=None)

    p = sub.add_parser("probe", help="Run pack probes (all packs, or one watcher's pack).")
    p.add_argument("watcher_id", nargs="?", default=None)
    p.add_argument("--all", action="store_true", help="probe every watcher in the registry")
    p.add_argument("--json", action="store_true")

    h = sub.add_parser("harvest", help="Run one watcher's harvest handler explicitly.")
    h.add_argument("--id", dest="watcher_id", required=True)
    h.add_argument("--dry-run", action="store_true",
                   help="handler runs with dry_run=True; only the budget counters are persisted")
    h.add_argument("--backfill", action="store_true",
                   help="pass backfill: true so the handler pulls backfill_window_days")

    for subparser in (c, s, ls, e, p, h):
        _add_location_args(subparser, suppress=True)
    return parser.parse_args(argv)


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1

    if args.cmd == "check":
        cfg = load_config(workspace)
        ctx = CheckContext(workspace=workspace, framework=framework, cfg=cfg, cycle=args.cycle,
                           timeout=args.timeout, dry_run=args.dry_run, no_harvest=args.no_harvest,
                           only_id=args.only_id)
        payload = run_check(ctx)
        if payload is None:
            if not args.report:
                print("{}")
            return 0
        if args.report:
            text = render_report(payload)
            if text:
                print(text, end="")
        else:
            _emit(payload)
        return 1 if payload["summary"]["errors"] else 0

    if args.cmd == "stamp":
        rc, result = run_stamp(workspace, framework, watcher_id=args.id, outcome=args.outcome,
                               note=args.note)
        _emit(result)
        return rc

    if args.cmd == "list":
        cfg = load_config(workspace)
        if not registry_dir(workspace, cfg).is_dir():
            print("[]" if args.json else f"watchlist off: {cfg.path}/ does not exist")
            return 0
        rows = list_rows(workspace, framework, status=args.status)
        for msg in registry_stray_files(registry_dir(workspace, cfg)):
            print(f"warning: {cfg.path}/{msg}", file=sys.stderr)
        if args.json:
            _emit(rows)
        else:
            _print_table(rows, ["id", "type", "pack", "status", "last_checked", "last_changed",
                                "last_outcome"])
        return 0

    if args.cmd == "enable":
        try:
            target = enable_pack(workspace, framework, pack_id=args.pack, watcher_id=args.watcher_id,
                                 params=_parse_kv(args.param), title=args.title)
        except WatchlistError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"created {_rel(workspace, target)}")
        return 0

    if args.cmd == "probe":
        rows = probe_rows(workspace, framework, watcher_id=args.watcher_id, all_watchers=args.all)
        if args.json:
            _emit(rows)
        else:
            cols = ["pack", "origin", "probe", "status", "detail", "setup_hint"] if (
                args.watcher_id is None and not args.all
            ) else ["id", "pack", "status", "detail", "setup_hint"]
            _print_table(rows, cols)
        return 0

    if args.cmd == "harvest":
        rc, payload = run_explicit_harvest(workspace, framework, watcher_id=args.watcher_id,
                                           dry_run=args.dry_run, backfill=args.backfill)
        _emit(payload)
        return rc

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
