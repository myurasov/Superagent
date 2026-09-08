#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""0.18.0 migration helper (from 0.17.2) -- workspace hygiene repairs.

Canonical instructions live in ``superagent/migrations/0.18.0.md``; this
script is the executable form of its ``## Migrate`` steps. Every step is
idempotent and safe on a workspace where its condition does not apply.

Steps (in order):

1. ``history.md`` header normalization -- every top-level dated entry under
   ``## Log`` becomes ``#### YYYY-MM-DD <em-dash> <title>`` and entries are
   stable-sorted newest-first. Everything outside the Log section is kept
   byte-for-byte.
2. ``_memory/model-context.yaml`` -- ``sessions`` trimmed to the 10 newest.
3. ``_memory/config.yaml`` -- every enabled source in ``data-sources.yaml``
   is present under ``data_sources_configured`` and
   ``preferences.ingestion_schedule``; ``last_updated`` refreshed when the
   file changed.
4. ``Outbox/README.md`` re-seeded from the framework template if missing.
5. ``_memory/world.yaml`` rebuilt (derived; failure is a warning only, and a
   rebuild alone does not count as a workspace change).
6. ``_memory/transactions.yaml`` -- stale pending rows marked via
   ``superagent.tools.ingest.simplefin.mark_stale_pending`` when available,
   honouring the ``stale_pending_days`` override on the ``simplefin`` row of
   ``data-sources.yaml``.
7. ``.version`` advanced to 0.18.0.

Pre-flight: ``_memory/config.yaml``, ``_memory/data-sources.yaml`` and
``_memory/model-context.yaml`` (each when present) must parse as YAML, or the
run exits 1 before any step writes.

Before any file is rewritten its original bytes are copied to
``<workspace>/_memory/_checkpoints/0.18.0/<relative path>`` so ``revert.py``
can restore them; files created from scratch are listed in
``<workspace>/_memory/_checkpoints/0.18.0/_seeded.txt`` so ``revert.py`` can
unseed them. This is a migration-scoped checkpoint, not a general workspace
snapshot facility.

Usage::

    uv run python superagent/migrations/0.18.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success (or nothing to do), 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import datetime as dt
import importlib.util
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

TO_VERSION = "0.18.0"
FROM_VERSION = "0.17.2"
CHECKPOINT_REL = Path("_memory") / "_checkpoints" / TO_VERSION
# Seed record inside the checkpoint folder: one workspace-relative POSIX path
# per line for every file this run created from scratch (no original to
# checkpoint). revert.py removes such a file only when it is listed here AND
# still byte-identical to its template.
SEEDED_MARKER_NAME = "_seeded.txt"
SESSIONS_KEEP = 10
EM_DASH = "\u2014"
_DASHES = "\u2014\u2013-"  # em-dash, en-dash, hyphen
DEFAULT_SCHEDULE = "daily"
# `_memory/<file>` YAML inputs that must parse before any step writes.
PREFLIGHT_YAML = ("config.yaml", "data-sources.yaml", "model-context.yaml")

# Source id -> `data_sources_configured` group, mirroring the config template.
SOURCE_GROUPS: dict[str, str] = {
    "gmail": "email", "icloud_mail": "email", "outlook": "email",
    "google_calendar": "calendar", "icloud_calendar": "calendar",
    "outlook_calendar": "calendar",
    "apple_reminders": "reminders_and_notes", "apple_notes": "reminders_and_notes",
    "obsidian": "reminders_and_notes", "notion": "reminders_and_notes",
    "plaid": "finance", "monarch": "finance", "ynab": "finance",
    "csv_only": "finance", "csv": "finance", "simplefin": "finance",
    "apple_health": "health", "whoop": "health", "strava": "health",
    "garmin": "health", "oura": "health", "fitbit": "health",
    "home_assistant": "smart_home", "smartthings": "smart_home", "homekit": "smart_home",
    "tesla": "vehicles", "obd_csv": "vehicles",
    "imessage": "messaging", "whatsapp": "messaging", "signal": "messaging",
}
DEFAULT_GROUP = "other"

# ---------------------------------------------------------------------------
# history.md header grammar
# ---------------------------------------------------------------------------

_DATE = r"\d{4}-\d{2}-\d{2}"
# `## | ### | #### <date>[rest]` -- date-led at any entry-ish level.
DATE_LED_RE = re.compile(rf"^(?P<hashes>#{{2,4}})\s+(?P<date>{_DATE})(?P<rest>(?:\s+.*)?)\s*$")
# Dash-free suffix right after the date: a clock time (optional tz) or ONE parenthetical.
SUFFIX_RE = re.compile(r"^\s+(?P<suffix>\d{1,2}:\d{2}(?:\s+[A-Za-z]{1,5})?|\([^()]*\))(?=\s|$)")
SEP_RE = re.compile(rf"^\s*[{_DASHES}]+\s*")
# `## | ### | #### <title> <dash> [word ]<date>[ (qualifier) | <dash> word | word]`
# A single word may sit between the separator and the date (`-- captured 2026-07-21`).
TITLE_FIRST_RE = re.compile(
    rf"^(?P<hashes>#{{2,4}})\s+(?P<title>.+?)\s*[{_DASHES}]+\s*(?P<pre>[A-Za-z][\w-]*\s+)?"
    rf"(?P<date>{_DATE})"
    rf"(?P<qual>(?:\s*[{_DASHES}]+\s*|\s+)(?:\([^()]*\)|[A-Za-z][\w-]*))?\s*$"
)
LOG_HEADING_RE = re.compile(r"^## Log\s*$")
SECTION_RE = re.compile(r"^#{1,2}\s")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
LAST_UPDATED_RE = re.compile(r"^(_Last updated: ).*?(_)\s*$")


@dc.dataclass(frozen=True)
class ParsedHeader:
    """A top-level dated entry header and its canonical rendering."""

    date: str
    normalized: str


def parse_header(line: str) -> ParsedHeader | None:
    """Return the canonical `#### <date> -- <title>` form of a dated header, or None.

    Recognizes (a) date-led headers at the wrong level, (b) title-first
    headers with a trailing date (+ optional qualifier, which is appended to
    the title), (c) canonical H4 headers (returned unchanged), and (d)
    date-led H4 headers missing the separator. Undated headers return None.
    """
    m = DATE_LED_RE.match(line)
    if m:
        date, rest = m.group("date"), m.group("rest")
        suffix = ""
        sm = SUFFIX_RE.match(rest)
        if sm:
            suffix = " " + sm.group("suffix")
            rest = rest[sm.end():]
        sep = SEP_RE.match(rest)
        title = rest[sep.end():].strip() if sep else rest.strip()
        head = f"#### {date}{suffix}"
        return ParsedHeader(date, f"{head} {EM_DASH} {title}" if title else head)
    m = TITLE_FIRST_RE.match(line)
    if m:
        title = m.group("title").strip()
        for part in (m.group("pre"), m.group("qual")):
            qual = (part or "").strip(" \t" + _DASHES)
            if qual:
                if not qual.startswith("("):
                    qual = f"({qual})"
                title = f"{title} {qual}"
        return ParsedHeader(m.group("date"), f"#### {m.group('date')} {EM_DASH} {title}")
    return None


@dc.dataclass
class _Entry:
    date: str
    lines: list[str]
    changed: bool


def _log_bounds(lines: list[str]) -> tuple[int, int, list[int]] | None:
    """Locate `## Log`; return (log_idx, section_end, header_line_indices)."""
    log_idx = next((i for i, ln in enumerate(lines) if LOG_HEADING_RE.match(ln)), None)
    if log_idx is None:
        return None
    end = len(lines)
    headers: list[int] = []
    in_fence = False
    for i in range(log_idx + 1, len(lines)):
        ln = lines[i]
        if FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        parsed = parse_header(ln) if ln.startswith("#") else None
        if parsed is not None:
            headers.append(i)
        elif SECTION_RE.match(ln):
            end = i
            break
    return log_idx, end, headers


def normalize_history_text(text: str, today: str | None = None) -> tuple[str, int, bool]:
    """Normalize + reorder the `## Log` entries of one history.md.

    Returns (new_text, headers_normalized, reordered). Text outside the Log
    section is preserved byte-for-byte; the `_Last updated: ..._` line (if
    any) is refreshed to `today` only when the text actually changed.
    """
    lines = text.split("\n")
    tail_blank = 0
    while lines and lines[-1] == "":
        lines.pop()
        tail_blank += 1
    bounds = _log_bounds(lines)
    if bounds is None or not bounds[2]:
        return text, 0, False
    log_idx, end, headers = bounds
    preamble = lines[log_idx + 1:headers[0]]
    entries: list[_Entry] = []
    for n, h in enumerate(headers):
        stop = headers[n + 1] if n + 1 < len(headers) else end
        parsed = parse_header(lines[h])
        assert parsed is not None
        body = lines[h + 1:stop]
        entries.append(_Entry(parsed.date, [parsed.normalized, *body],
                              parsed.normalized != lines[h]))
    ordered = sorted(entries, key=lambda e: e.date, reverse=True)  # stable
    reordered = [id(e) for e in ordered] != [id(e) for e in entries]
    n_norm = sum(1 for e in entries if e.changed)
    if not reordered and n_norm == 0:
        return text, 0, False
    out = lines[:log_idx + 1] + preamble
    for n, e in enumerate(ordered):
        chunk = list(e.lines)
        if reordered and n + 1 < len(ordered) and chunk and chunk[-1].strip():
            chunk.append("")  # keep a blank line between relocated entries
        out.extend(chunk)
    out.extend(lines[end:])
    out.extend([""] * tail_blank)
    if today:
        for i, ln in enumerate(out):
            m = LAST_UPDATED_RE.match(ln)
            if m:
                out[i] = f"{m.group(1)}{today}{m.group(2)}"
                break
    return "\n".join(out), n_norm, reordered


def log_section_offenders(text: str) -> list[str]:
    """Return the non-canonical dated headers still present under `## Log`."""
    lines = text.split("\n")
    bounds = _log_bounds(lines)
    if bounds is None:
        return []
    return [lines[h] for h in bounds[2] if parse_header(lines[h]).normalized != lines[h]]


def history_files(ws: Path) -> list[Path]:
    """Every Domain / Project / Archive history.md under the workspace."""
    found: list[Path] = []
    for pattern in ("Domains/*/history.md", "Projects/*/history.md", "Archive/**/history.md"):
        found.extend(p for p in ws.glob(pattern) if p.is_file())
    return sorted(set(found))


# ---------------------------------------------------------------------------
# model-context.yaml sessions trim (text-level; comments preserved)
# ---------------------------------------------------------------------------

_ITEM_RE = re.compile(r"^(\s*)-(\s|$)")
_SESSION_KEYS = ("date", "ts", "at")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_comment(line: str) -> bool:
    """True for a comment-only YAML line (any indent), which never bounds a block."""
    return line.lstrip().startswith("#")


def _sort_value(v: Any) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def trim_sessions_text(text: str, keep: int = SESSIONS_KEEP) -> tuple[str, int]:
    """Keep the `keep` newest `sessions` rows (newest-first). Returns (text, dropped)."""
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines) if re.match(r"^sessions:\s*(#.*)?$", ln)), None)
    if idx is None:
        return text, 0
    item_indent: int | None = None
    block_end = len(lines)
    for k in range(idx + 1, len(lines)):
        ln = lines[k]
        if not ln.strip() or _is_comment(ln):
            continue  # blanks and comment-only lines never end the list
        m = _ITEM_RE.match(ln)
        if item_indent is None:
            if m:
                item_indent = len(m.group(1))
                continue
            if _indent(ln) == 0:
                block_end = k
                break
            continue
        ind = _indent(ln)
        if ind < item_indent or (ind == item_indent and not m and item_indent == 0):
            block_end = k
            break
    if item_indent is None:
        return text, 0
    starts = [k for k in range(idx + 1, block_end)
              if (m := _ITEM_RE.match(lines[k])) and len(m.group(1)) == item_indent]
    if len(starts) <= keep:
        return text, 0
    preamble = lines[idx + 1:starts[0]]
    chunks = [lines[s:(starts[n + 1] if n + 1 < len(starts) else block_end)]
              for n, s in enumerate(starts)]
    # Blank and comment-only lines after the LAST row belong to the list's
    # tail, not to that row -- keep them even when the row itself is dropped.
    tail: list[str] = []
    while chunks[-1] and (chunks[-1][-1].strip() == "" or _is_comment(chunks[-1][-1])):
        tail.insert(0, chunks[-1].pop())
    parsed = [yaml.safe_load("\n".join(c)) for c in chunks]
    rows = [p[0] if isinstance(p, list) and p else None for p in parsed]
    key = next((k for k in _SESSION_KEYS
                if all(isinstance(r, dict) and k in r for r in rows)), None)
    order = list(range(len(chunks)))
    if key is not None:
        order.sort(key=lambda i: _sort_value(rows[i][key]), reverse=True)  # stable
    kept = [list(chunks[i]) for i in order[:keep]]
    while kept[-1] and kept[-1][-1].strip() == "":
        kept[-1].pop()
    out = lines[:idx + 1] + preamble
    for c in kept:
        out.extend(c)
    out.extend(tail)
    out.extend(lines[block_end:])
    new_text = "\n".join(out)
    check = yaml.safe_load(new_text)
    if not isinstance(check, dict) or len(check.get("sessions") or []) != keep:
        raise ValueError("sessions trim produced an unexpected YAML shape; aborting")
    return new_text, len(chunks) - keep


# ---------------------------------------------------------------------------
# config.yaml minimal text edits (comments preserved)
# ---------------------------------------------------------------------------

def _block_end(lines: list[str], start: int, end: int, indent: int) -> int:
    """First non-blank, non-comment line in [start, end) with indent <= `indent`.

    Comment-only lines are skipped regardless of their column: a column-0
    comment inside a parent block must not hide the keys that follow it.
    """
    for k in range(start, end):
        ln = lines[k]
        if ln.strip() and not _is_comment(ln) and _indent(ln) <= indent:
            return k
    return end


def _insert_at(lines: list[str], start: int, end: int) -> int:
    """Insertion index just after the last non-blank line in [start, end)."""
    k = end
    while k > start and not lines[k - 1].strip():
        k -= 1
    return k


def _load_simplefin_handler() -> Any | None:
    """Return the SimpleFIN normalizer module, or None when unavailable.

    Pre-0.19.0 trees expose it as ``superagent.tools.ingest.simplefin``; from
    0.19.0 it is the self-contained pack handler
    ``superagent/watchers/simplefin/handler.py`` and is loaded by file path,
    exactly as ``tools/watchlist.py`` loads any pack handler.
    """
    try:
        from superagent.tools.ingest import simplefin  # type: ignore[attr-defined]
        return simplefin
    except ImportError:
        pass
    handler = Path(__file__).resolve().parents[2] / "watchers" / "simplefin" / "handler.py"
    if not handler.is_file():
        return None
    spec = importlib.util.spec_from_file_location("superagent_watchers_simplefin_handler", handler)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 — a broken handler must not abort the migration
        return None
    return module


def _find_key(lines: list[str], start: int, end: int, indent: int, key: str) -> int | None:
    pat = re.compile(rf"^ {{{indent}}}{re.escape(key)}:(\s|$)")
    return next((k for k in range(start, end) if pat.match(lines[k])), None)


def _child_indent(lines: list[str], start: int, end: int, parent_indent: int) -> int:
    for k in range(start, end):
        if lines[k].strip() and not lines[k].lstrip().startswith("#"):
            return _indent(lines[k])
    return parent_indent + 2


def ensure_yaml_child(lines: list[str], path: list[str], key: str, value: str) -> str | None:
    """Ensure `path -> key: value` exists as a mapping entry, creating levels as needed.

    Returns the action taken ("inserted", "exists") for the caller's report.
    Parent levels that are missing are appended at the end of their block;
    a missing top-level key is appended at the end of the file.
    """
    start, end, indent = 0, len(lines), 0
    for level in path:
        k = _find_key(lines, start, end, indent, level)
        if k is None:
            at = _insert_at(lines, start, end)
            new = [" " * indent + f"{level}:"]
            if indent == 0 and at > 0 and lines[at - 1].strip():
                new.insert(0, "")
            lines[at:at] = new
            k = at + len(new) - 1
            end += len(new)
        else:
            m = re.match(r"^\s*[^:]+:\s*(\{\}|\[\])\s*$", lines[k])
            if m:  # empty flow mapping -> block form
                lines[k] = lines[k][:lines[k].index(m.group(1))].rstrip()
        b_end = _block_end(lines, k + 1, end, indent)
        indent = _child_indent(lines, k + 1, b_end, indent)
        start, end = k + 1, b_end
    if _find_key(lines, start, end, indent, key) is not None:
        return "exists"
    lines.insert(_insert_at(lines, start, end), " " * indent + f"{key}: {value}")
    return "inserted"


def set_yaml_scalar(lines: list[str], path: list[str], key: str, value: str) -> bool:
    """Replace the scalar of `path -> key` in place, keeping any inline comment."""
    start, end, indent = 0, len(lines), 0
    for level in path:
        k = _find_key(lines, start, end, indent, level)
        if k is None:
            return False
        b_end = _block_end(lines, k + 1, end, indent)
        indent = _child_indent(lines, k + 1, b_end, indent)
        start, end = k + 1, b_end
    k = _find_key(lines, start, end, indent, key)
    if k is None:
        return False
    m = re.match(r"^(\s*[^:#]+:\s*)([^#]*?)(\s*#.*)?$", lines[k])
    if not m:
        return False
    lines[k] = f"{m.group(1)}{value}{m.group(3) or ''}"
    return True


def data_source_rows(ws: Path) -> list[dict[str, Any]]:
    """All mapping rows under `sources:` in `_memory/data-sources.yaml` ([] if absent).

    A file that does not parse raises `yaml.YAMLError` -- callers must not
    treat a broken registry as "no sources" (that would silently skip step 3
    while the migration still exits 0). `Migration.run()` pre-flights the
    parse so the steps themselves never see the error.
    """
    path = ws / "_memory" / "data-sources.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("sources") if isinstance(data, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def enabled_sources(ws: Path) -> list[dict[str, Any]]:
    """Rows of `_memory/data-sources.yaml` with `enabled: true` and a non-empty id.

    Propagates `yaml.YAMLError` from a malformed file (see `data_source_rows`).
    """
    return [r for r in data_source_rows(ws) if r.get("id") and r.get("enabled")]


def data_source_row(ws: Path, source: str) -> dict[str, Any]:
    """The `data-sources.yaml` row whose `id` is `source`, or {} when absent."""
    return next((r for r in data_source_rows(ws) if r.get("id") == source), {})


def configured_group(config: dict[str, Any], source: str) -> tuple[str | None, Any]:
    """Return (group, value) for `source` under `data_sources_configured`, or (None, None)."""
    dsc = config.get("data_sources_configured")
    if isinstance(dsc, dict):
        for group, members in dsc.items():
            if isinstance(members, dict) and source in members:
                return str(group), members[source]
    elif isinstance(dsc, list) and source in dsc:
        return "", True
    return None, None


def update_config_text(text: str, sources: list[dict[str, Any]], now: str) -> tuple[str, list[str]]:
    """Ensure every enabled source is registered in config.yaml. Returns (text, actions)."""
    config = yaml.safe_load(text) or {}
    if not isinstance(config, dict):
        raise ValueError("config.yaml is not a mapping")
    lines = text.split("\n")
    actions: list[str] = []
    for row in sources:
        src = str(row["id"])
        group, value = configured_group(config, src)
        if group is None:
            grp = SOURCE_GROUPS.get(src, DEFAULT_GROUP)
            k = _find_key(lines, 0, len(lines), 0, "data_sources_configured")
            if isinstance(config.get("data_sources_configured"), list) and k is not None:
                # Legacy flat-list form: append one `- <source>` item.
                b_end = _block_end(lines, k + 1, len(lines), 0)
                lines.insert(_insert_at(lines, k + 1, b_end), f"  - {src}")
                actions.append(f"data_sources_configured: {src} (added)")
            else:
                ensure_yaml_child(lines, ["data_sources_configured", grp], src, "true")
                actions.append(f"data_sources_configured.{grp}.{src}: true (added)")
        elif group != "" and value is not True:
            if set_yaml_scalar(lines, ["data_sources_configured", group], src, "true"):
                actions.append(f"data_sources_configured.{group}.{src}: true (was {value!r})")
        prefs = config.get("preferences")
        sched = prefs.get("ingestion_schedule") if isinstance(prefs, dict) else None
        if not (isinstance(sched, dict) and src in sched):
            cadence = str(row.get("schedule") or "").strip() or DEFAULT_SCHEDULE
            ensure_yaml_child(lines, ["preferences", "ingestion_schedule"], src, f'"{cadence}"')
            actions.append(f"preferences.ingestion_schedule.{src}: {cadence} (added)")
    if not actions:
        return text, []
    stamped = False
    for i, ln in enumerate(lines):
        if re.match(r"^last_updated:(\s|$)", ln):
            lines[i] = f'last_updated: "{now}"'
            stamped = True
            break
    if not stamped:
        at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("schema_version:")), 0)
        lines.insert(at, f'last_updated: "{now}"')
    actions.append(f"last_updated: {now}")
    new_text = "\n".join(lines)
    check = yaml.safe_load(new_text)
    for row in sources:
        src = str(row["id"])
        g, v = configured_group(check, src)
        sched = (check.get("preferences") or {}).get("ingestion_schedule") or {}
        if g is None or (g != "" and v is not True) or src not in sched:
            raise ValueError(f"config.yaml edit did not register {src!r}; aborting")
    return new_text, actions


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def default_framework_root() -> Path:
    """`superagent/` as resolved from this script's location."""
    return Path(__file__).resolve().parents[2]


def now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC).astimezone()).replace(microsecond=0).isoformat()


class Migration:
    """Stateful runner for the seven 0.18.0 steps against one workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 skip_world: bool = False, now: dt.datetime | None = None,
                 out: Callable[[str], None] = print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else default_framework_root()
        self.dry_run = dry_run
        self.skip_world = skip_world
        self.now = now or dt.datetime.now(dt.UTC).astimezone()
        self.out = out
        self.changed: list[str] = []  # workspace files (re)written; drives "nothing to do"
        self.world_rebuilt = False  # derived world.yaml re-derived; NOT a workspace change

    # -- helpers -----------------------------------------------------------
    def _rel(self, path: Path) -> str:
        return path.relative_to(self.ws).as_posix()

    def _checkpoint(self, path: Path) -> None:
        dest = self.ws / CHECKPOINT_REL / path.relative_to(self.ws)
        if dest.exists():
            return  # keep the earliest original
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    def _record_seeded(self, rel: str) -> None:
        """Append `rel` to the seed record (idempotent) so revert.py may unseed it."""
        marker = self.ws / CHECKPOINT_REL / SEEDED_MARKER_NAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        existing = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
        if rel in existing:
            return
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(f"{rel}\n")

    def _write(self, path: Path, text: str) -> None:
        self.changed.append(self._rel(path))
        if self.dry_run:
            return
        if path.exists():
            self._checkpoint(path)
        else:
            self._record_seeded(self._rel(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    # -- steps -------------------------------------------------------------
    def step_history(self) -> None:
        today = self.now.date().isoformat()
        for path in history_files(self.ws):
            text = path.read_text(encoding="utf-8")
            new, n_norm, reordered = normalize_history_text(text, today)
            if new == text:
                continue
            self._say(f"history {self._rel(path)}: {n_norm} header(s) normalized, "
                      f"reordered: {'yes' if reordered else 'no'}")
            self._write(path, new)

    def step_sessions(self) -> None:
        path = self.ws / "_memory" / "model-context.yaml"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        new, dropped = trim_sessions_text(text)
        if dropped:
            self._say(f"model-context.yaml: sessions trimmed to {SESSIONS_KEEP} "
                      f"newest ({dropped} dropped)")
            self._write(path, new)

    def step_config(self) -> None:
        path = self.ws / "_memory" / "config.yaml"
        if not path.exists():
            return
        sources = enabled_sources(self.ws)
        if not sources:
            return
        text = path.read_text(encoding="utf-8")
        new, actions = update_config_text(text, sources, now_iso(self.now))
        if actions:
            for a in actions:
                self._say(f"config.yaml: {a}")
            self._write(path, new)

    def step_outbox_readme(self) -> None:
        dest = self.ws / "Outbox" / "README.md"
        if dest.exists():
            return
        template = self.framework / "templates" / "folder-readmes" / "Outbox.md"
        if not template.exists():
            self._say(f"warning: template missing at {template}; Outbox/README.md not seeded")
            return
        self._say("Outbox/README.md: seeded from templates/folder-readmes/Outbox.md")
        self._write(dest, template.read_text(encoding="utf-8"))

    def step_world(self) -> None:
        if self.skip_world:
            return
        cmd = [sys.executable, "-m", "superagent.tools.world", "--workspace", str(self.ws), "rebuild"]
        if self.dry_run:
            self._say("world.yaml: would run `uv run python -m superagent.tools.world rebuild`")
            return
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
        # Derived data: re-deriving it is not a workspace change, so a re-run
        # that only rebuilds world.yaml still reports "nothing to do".
        self.world_rebuilt = True
        self._say("world.yaml: rebuilt (derived)")

    def step_transactions(self) -> None:
        # Since 0.19.0 the SimpleFIN normalizer lives in its pack folder
        # (superagent/watchers/simplefin/handler.py), so it is loaded by file
        # path; the pre-0.19.0 package import is tried first for old trees.
        path = self.ws / "_memory" / "transactions.yaml"
        if not path.exists():
            return
        simplefin = _load_simplefin_handler()
        mark = getattr(simplefin, "mark_stale_pending", None)
        if mark is None:
            self._say("transactions.yaml: mark_stale_pending not available in this build; skipped")
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            self._say(f"warning: transactions.yaml does not parse ({exc}); skipped")
            return
        rows = data.get("transactions") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return
        # Honour the per-source `stale_pending_days` override exactly like the
        # ingestor does; a user who configured e.g. 45 days must not have
        # 15-44 day old orphans flagged stale by the migration (nothing
        # un-flags them later).
        default_days = int(getattr(simplefin, "DEFAULT_STALE_PENDING_DAYS", 14))
        days = int(data_source_row(self.ws, "simplefin").get("stale_pending_days") or default_days)
        count = mark(rows, days=days, today=self.now.date())
        if count <= 0:
            return
        self._say(f"transactions.yaml: {count} stale pending row(s) marked")
        self._write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

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
        # Pre-flight: every YAML file a step reads must parse BEFORE any step
        # writes, so a broken registry can neither be silently skipped nor
        # halt the run after history files have already been rewritten.
        for rel in PREFLIGHT_YAML:
            path = self.ws / "_memory" / rel
            if not path.exists():
                continue
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.out(f"error: _memory/{rel} is not well-formed YAML: {exc}")
                return 1
        steps = (self.step_history, self.step_sessions, self.step_config,
                 self.step_outbox_readme, self.step_world, self.step_transactions,
                 self.step_version)
        for step in steps:
            try:
                step()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.out(f"error in {step.__name__}: {exc}")
                self.out("halted; nothing after this step was applied. "
                         "Run revert.py to restore checkpointed files.")
                return 1
        if not self.changed:
            suffix = " (world.yaml re-derived)" if self.world_rebuilt else ""
            self._say(f"nothing to do; workspace already at 0.18.0 shape{suffix}")
        else:
            self._say(f"{len(self.changed)} file(s) {'would be ' if self.dry_run else ''}changed")
        return 0


def run_migration(workspace: Path, **kwargs: Any) -> int:
    """Convenience wrapper: `Migration(workspace, **kwargs).run()`."""
    return Migration(workspace, **kwargs).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate-0.18.0",
        description="Apply the 0.18.0 workspace migration (idempotent).")
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
