#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Events stream as a DERIVED VIEW — wholesale rebuild from sources of truth.

Implements st-2026-08-31-010 option (c). The quarterly partitions under
`_memory/events/<YYYY-Qn>.yaml` are derived data — no skill appends to them
per-write. This module rebuilds every partition from the two sources of truth:

  1. `_memory/interaction-log.yaml` — both row schemas: old-format rows
     (timestamp / type / subject, no id) and new-format rows
     (id / ts / skill / action / summary / related_*).
  2. `Domains/*/history.md` + `Projects/*/history.md` (plus archived copies
     under `Archive/*/Domains|Projects/*/history.md`) — H4 entries of the
     form `#### YYYY-MM-DD — <title>`, with an optional time / parenthetical
     suffix between date and separator (`#### 2026-05-26 08:30 PT — ...`,
     `#### 2026-05-28 (later) — ...`). Date-led H4 lines that still fail to
     parse are counted and reported as `unmatched_headers` — never silently
     dropped.

OVERWRITE GUARD: `rebuild` refuses to run when a partition contains rows that
are neither flagged `legacy: true` nor part of a previously-derived partition
(`derived: true` meta) — i.e. a pre-0.16.0 hand-authored partition that a
rebuild would destroy. Run `flag-legacy <quarter>` first (see
migrations/0.16.0.md), or pass `--allow-overwrite` to discard those rows.

LEGACY FREEZE: rows already present in a partition that carry `legacy: true`
are preserved BYTE-VERBATIM — never regenerated. The legacy watermark is the
max `ts` among legacy rows; only source records with `ts` strictly after the
watermark are derived. Any hand-authored partition row that must survive a
rebuild needs the `legacy: true` flag; everything else is regenerated.

DETERMINISM: rebuilding twice over unchanged sources produces byte-identical
partition files (no wall-clock timestamps inside partitions). The partition
index (`_memory/events.yaml`) carries a single `derive_state.derived_at`
which bumps only when partition content actually changed (content hash).

CLI:
    uv run python -m superagent.tools.events_derive              # rebuild (default)
    uv run python -m superagent.tools.events_derive rebuild [--force] [--allow-overwrite]
    uv run python -m superagent.tools.events_derive check        # exit 1 if stale
    uv run python -m superagent.tools.events_derive --check      # same
    uv run python -m superagent.tools.events_derive flag-legacy 2026-Q2
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.log_window import (
    events_root,
    index_path,
    load_yaml,
    now_iso,
    parse_iso_dt,
    partition_path,
    quarter_for,
    save_yaml,
)

SUBJECT_MAX = 120
SUMMARY_MAX = 600

# Old-format interaction-log `type` -> event kind. Types already in the
# canonical taxonomy (skill_run, ingest_run, bill_paid, health_event, ...)
# pass through; unknown types fall back to "other".
KIND_BY_OLD_TYPE = {
    "email": "interaction",
    "email_received": "interaction",
    "email_sent": "interaction",
    "message": "interaction",
    "meeting": "interaction",
    "call": "interaction",
    "note": "interaction",
    "user_report": "interaction",
    "user_clarification": "interaction",
    "maintenance": "maintenance_done",
    "important_date": "important_date_marked",
    "appointment": "appointment_completed",
    "capture": "capture_signal",
    "structural_edit": "audit",
    "migration": "skill_run",
}

CANONICAL_KINDS = {
    "interaction", "ingest_run", "skill_run", "status_flip", "bill_paid",
    "task_completed", "task_created", "appointment_completed", "health_event",
    "maintenance_done", "decision", "important_date_marked", "source_added",
    "source_accessed", "project_milestone", "audit", "capture_signal",
    "cache_evict", "ingest_failure", "history_entry", "watch_changed", "other",
}

# New-format interaction-log `action` -> event kind (default: skill_run).
KIND_BY_NEW_ACTION = {
    "create_project": "project_milestone",
    "import_document": "source_added",
    "file_source": "source_added",
    # A watchlist check that detected a change (contracts/watchlist.md § 8.2):
    # the run logs `action: watch_change_detected`; the timeline shows it as
    # `watch_changed` so weekly-review can answer "what moved this month".
    "watch_change_detected": "watch_changed",
}

# `#### <date>[ <time/parenthetical suffix>] <dash separator> <title>`.
# The optional suffix (e.g. `08:30 PT`, `(later)`, `(pm2)`) is dash-free;
# the separator accepts em-dash / en-dash / hyphen(s).
H4_RE = re.compile(
    r"^####\s+(\d{4}-\d{2}-\d{2})"    # ISO date
    r"(?:\s+[^—–-]+?)?"               # optional time / parenthetical suffix
    r"\s*[—–-]+\s*"                   # separator
    r"(.+?)\s*$")
H4_DATE_RE = re.compile(r"^####\s+\d{4}-\d{2}-\d{2}\b")
ITEM_RE = re.compile(r"^  - ")

PARTITION_HEADER = (
    "# [Do not change manually — managed by Superagent]\n"
    "# Superagent events partition — DERIVED VIEW.\n"
    "# Rebuilt wholesale by `tools/events_derive.py rebuild` from\n"
    "# `_memory/interaction-log.yaml` + Domain/Project `history.md`\n"
    "# (per contracts/events-stream.md). Rows carrying `legacy: true`\n"
    "# are frozen byte-verbatim; every other row is regenerated.\n"
    "\n"
)


# ---------------------------------------------------------------- helpers

def clip_line(text: str, limit: int = SUBJECT_MAX) -> str:
    """Collapse whitespace to single spaces and cap at `limit` chars."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "..."


def clip_summary(text: str, limit: int = SUMMARY_MAX) -> str:
    return clip_line(text, limit)


def first_sentence(text: str) -> str:
    flat = " ".join(str(text).split())
    head = re.split(r"(?<=[.!?])\s+", flat, maxsplit=1)[0]
    return head


def slugify(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def entities_for(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for field, kind in (("related_domain", "domain"), ("related_project", "project"),
                        ("related_asset", "asset"), ("related_account", "account")):
        v = row.get(field)
        if isinstance(v, str) and v.strip():
            out.append(f"{kind}:{v.strip()}")
    return out


def kind_for_old_row(row: dict[str, Any]) -> str:
    raw = str(row.get("type") or "").strip()
    if raw in KIND_BY_OLD_TYPE:
        return KIND_BY_OLD_TYPE[raw]
    if raw in CANONICAL_KINDS:
        return raw
    return "other"


def kind_for_new_row(row: dict[str, Any]) -> str:
    """Map a new-format interaction-log row to an event kind.

    `action` wins when it names a known milestone; otherwise the row is an
    `ingest_run` only when `skill` is exactly `ingest` or an `ingest-<source>`
    stem. Compound free-text values (`"ingest + log-event"`) are NOT ingest
    runs — they derive as `skill_run` like every other non-stem value (per
    contracts/events-stream.md § "Canonical row shape").
    """
    action = str(row.get("action") or "").strip()
    if action in KIND_BY_NEW_ACTION:
        return KIND_BY_NEW_ACTION[action]
    skill = str(row.get("skill") or "").strip()
    if skill == "ingest" or skill.startswith("ingest-"):
        return "ingest_run"
    return "skill_run"


def ts_string(value: Any, parsed: dt.datetime) -> str:
    """Deterministic ts string: keep the source's own string when present."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return parsed.isoformat(timespec="seconds")


# ------------------------------------------------------- config + sources

def load_events_config(workspace: Path) -> dict[str, Any]:
    cfg = load_yaml(workspace / "_memory" / "config.yaml") or {}
    prefs = cfg.get("preferences") or {}
    events = prefs.get("events") or {}
    return {
        "mode": str(events.get("mode") or "derived"),
        "skip_kinds": events.get("skip_kinds") or [],
    }


def history_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("Domains/*/history.md", "Projects/*/history.md",
                    "Archive/*/Domains/*/history.md", "Archive/*/Projects/*/history.md"):
        files.extend(sorted(workspace.glob(pattern)))
    return [f for f in files if f.is_file()]


def source_files(workspace: Path) -> list[Path]:
    files = []
    ilog = workspace / "_memory" / "interaction-log.yaml"
    if ilog.exists():
        files.append(ilog)
    files.extend(history_files(workspace))
    return files


def sources_max_mtime(workspace: Path) -> float:
    return max((f.stat().st_mtime for f in source_files(workspace)), default=0.0)


def sources_fingerprint(workspace: Path) -> str:
    """Hash of the source-file SET, so moves/deletions register as changes
    even when no surviving file's mtime moved (mtime alone misses `mv`/`rm`)."""
    paths = sorted(f.relative_to(workspace).as_posix()
                   for f in source_files(workspace))
    return hashlib.sha256("\n".join(paths).encode()).hexdigest()


# ------------------------------------------------------------ legacy rows

def split_partition_items(text: str) -> list[str]:
    """Split a partition file's `events:` list into per-row text chunks."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if ln.rstrip("\n") == "events:":
            start = i + 1
            break
    if start is None:
        return []
    chunks: list[str] = []
    current: list[str] = []
    for ln in lines[start:]:
        if ITEM_RE.match(ln):
            if current:
                chunks.append("".join(current))
            current = [ln]
        elif current:
            current.append(ln)
    if current:
        chunks.append("".join(current))
    return chunks


def parse_chunk(chunk: str) -> dict[str, Any] | None:
    try:
        rows = yaml.safe_load(chunk)
    except yaml.YAMLError:
        return None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def collect_legacy(workspace: Path) -> tuple[dict[str, list[tuple[dict[str, Any], str]]],
                                             dt.datetime | None]:
    """Return {quarter: [(row_dict, verbatim_chunk), ...]} + the watermark.

    The watermark is the max `ts` among legacy rows across all partitions;
    source records at or before it are never re-derived.
    """
    legacy: dict[str, list[tuple[dict[str, Any], str]]] = {}
    watermark: dt.datetime | None = None
    root = events_root(workspace)
    if not root.exists():
        return legacy, watermark
    for path in sorted(root.glob("*.yaml")):
        for chunk in split_partition_items(path.read_text()):
            row = parse_chunk(chunk)
            if not row or row.get("legacy") is not True:
                continue
            legacy.setdefault(path.stem, []).append((row, chunk))
            ts = parse_iso_dt(row.get("ts"))
            if ts is not None and (watermark is None or ts > watermark):
                watermark = ts
    return legacy, watermark


def count_unflagged_hand_rows(workspace: Path) -> int:
    """Rows a rebuild would destroy irrecoverably: rows without `legacy: true`
    inside partitions that were never produced by a rebuild (no `derived: true`
    top-level meta) — i.e. pre-0.16.0 hand-authored partitions."""
    root = events_root(workspace)
    if not root.exists():
        return 0
    n = 0
    for path in sorted(root.glob("*.yaml")):
        data = load_yaml(path)
        if not isinstance(data, dict) or data.get("derived") is True:
            continue
        rows = data.get("events") or []
        n += sum(1 for r in rows
                 if isinstance(r, dict) and r.get("legacy") is not True)
    return n


# -------------------------------------------------------------- derivation

def derive_from_interaction_log(workspace: Path, watermark: dt.datetime | None,
                                skip_kinds: list[str]) -> list[tuple]:
    """Yield (sort_key, event_dict) for post-watermark interaction-log rows."""
    data = load_yaml(workspace / "_memory" / "interaction-log.yaml") or {}
    entries = data.get("entries") or []
    out: list[tuple] = []
    for i, row in enumerate(entries):
        if not isinstance(row, dict):
            continue
        raw_ts = row.get("ts") or row.get("timestamp")
        ts = parse_iso_dt(raw_ts)
        if ts is None or (watermark is not None and ts <= watermark):
            continue
        old_format = "ts" not in row and "timestamp" in row
        if old_format:
            kind = kind_for_old_row(row)
            subject = clip_line(row.get("subject") or first_sentence(row.get("summary") or ""))
        else:
            kind = kind_for_new_row(row)
            subject = clip_line(row.get("action") or first_sentence(row.get("summary") or ""))
        if kind in skip_kinds:
            continue
        anchor = row.get("id") or ts_string(raw_ts, ts)
        event = {
            "ts": ts_string(raw_ts, ts),
            "kind": kind,
            "actor": "agent",
            "subject": subject,
            "summary": clip_summary(row.get("summary") or ""),
            "entities": entities_for(row),
            "source": f"interaction-log.yaml#{anchor}",
        }
        out.append(((ts, 0, ("interaction-log.yaml", i)), event))
    return out


def derive_from_history(workspace: Path, watermark: dt.datetime | None,
                        skip_kinds: list[str]) -> tuple[list[tuple], list[str]]:
    """Return (keyed events, unmatched date-led H4 headers) from history.md.

    Events are (sort_key, event_dict) pairs for post-watermark H4 entries.
    Unmatched headers are `<rel path>:<line no>` strings for date-led H4
    lines the regex could not parse — surfaced, never silently dropped.
    """
    if "history_entry" in skip_kinds:
        return [], []
    out: list[tuple] = []
    unmatched: list[str] = []
    for path in history_files(workspace):
        rel = path.relative_to(workspace).as_posix()
        parts = path.relative_to(workspace).parts
        # .../Domains/<Name>/history.md or .../Projects/<slug>/history.md
        container_kind = "project" if "Projects" in parts else "domain"
        container_slug = slugify(parts[-2])
        lines = path.read_text().splitlines()
        pos = 0
        for n, line in enumerate(lines):
            m = H4_RE.match(line)
            if m is None:
                if H4_DATE_RE.match(line):
                    unmatched.append(f"{rel}:{n + 1}")
                continue
            date_str, title = m.group(1), m.group(2)
            try:
                naive = dt.datetime.fromisoformat(date_str)
            except ValueError:
                continue
            ts = naive.astimezone()
            if watermark is not None and ts <= watermark:
                continue
            body: list[str] = []
            for follow in lines[n + 1:]:
                if follow.startswith("#"):
                    break
                body.append(follow)
            event = {
                "ts": ts.isoformat(timespec="seconds"),
                "kind": "history_entry",
                "actor": "agent",
                "subject": clip_line(title),
                "summary": clip_summary("\n".join(body)),
                "entities": [f"{container_kind}:{container_slug}"],
                "source": f"{rel}#{date_str} — {title}",
            }
            out.append(((ts, 1, (rel, pos)), event))
            pos += 1
    return out, unmatched


def assign_ids(sorted_events: list[dict[str, Any]],
               legacy: dict[str, list[tuple[dict[str, Any], str]]]) -> None:
    """Mint per-date sequential ids, starting above any legacy id numbers."""
    floor: dict[str, int] = {}
    for chunks in legacy.values():
        for row, _ in chunks:
            rid = str(row.get("id") or "")
            m = re.match(r"^evt-(\d{4}-\d{2}-\d{2})-(\d+)$", rid)
            if m:
                date, num = m.group(1), int(m.group(2))
                floor[date] = max(floor.get(date, 0), num)
    counter: dict[str, int] = {}
    for ev in sorted_events:
        date = str(ev["ts"])[:10]
        n = counter.get(date, floor.get(date, 0)) + 1
        counter[date] = n
        ev_id = f"evt-{date}-{n:03d}"
        # Insert id first for readability / schema parity with legacy rows.
        ev_items = list(ev.items())
        ev.clear()
        ev["id"] = ev_id
        ev.update(ev_items)


# --------------------------------------------------------------- rendering

def render_partition(quarter: str,
                     legacy_chunks: list[tuple[dict[str, Any], str]],
                     derived: list[dict[str, Any]]) -> str:
    ts_pairs: list[tuple[dt.datetime, str]] = []
    by_kind: dict[str, int] = {}
    for row, _ in legacy_chunks:
        parsed = parse_iso_dt(row.get("ts"))
        if parsed is not None:
            ts_pairs.append((parsed, str(row.get("ts"))))
        kind = str(row.get("kind") or "other")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    for ev in derived:
        parsed = parse_iso_dt(ev.get("ts"))
        if parsed is not None:
            ts_pairs.append((parsed, str(ev.get("ts"))))
        kind = str(ev.get("kind") or "other")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    ts_pairs.sort(key=lambda p: p[0])
    meta = {
        "schema_version": 1,
        "quarter": quarter,
        "derived": True,
        "first_event_at": ts_pairs[0][1] if ts_pairs else None,
        "last_event_at": ts_pairs[-1][1] if ts_pairs else None,
        "event_count": len(legacy_chunks) + len(derived),
        "legacy_rows": len(legacy_chunks),
        "by_kind": {k: by_kind[k] for k in sorted(by_kind)},
    }
    parts = [PARTITION_HEADER,
             yaml.safe_dump(meta, sort_keys=False, allow_unicode=True),
             "\nevents:\n"]
    for _, chunk in legacy_chunks:
        parts.append(chunk if chunk.endswith("\n") else chunk + "\n")
    if derived:
        dumped = yaml.safe_dump(derived, sort_keys=False, allow_unicode=True, width=88)
        parts.append("".join(
            ("  " + ln if ln.strip() else ln)
            for ln in dumped.splitlines(keepends=True)))
    return "".join(parts)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


# ----------------------------------------------------------------- rebuild

def rebuild(workspace: Path, force: bool = False,
            allow_overwrite: bool = False) -> dict[str, Any]:
    """Rebuild all partitions from the sources of truth. Returns a summary."""
    cfg = load_events_config(workspace)
    if cfg["mode"] == "off":
        return {"skipped": "events mode is 'off' in config"}
    if not allow_overwrite:
        hand_rows = count_unflagged_hand_rows(workspace)
        if hand_rows:
            return {"error": (
                f"{hand_rows} hand-authored partition row(s) are not flagged "
                "`legacy: true` and were never derived — a rebuild would "
                "destroy them irrecoverably. Run `events_derive flag-legacy "
                "<quarter>` first (see migrations/0.16.0.md), or pass "
                "--allow-overwrite to discard them.")}
    idx = load_yaml(index_path(workspace)) or {}
    state = idx.get("derive_state") or {}
    current_mtime = sources_max_mtime(workspace)
    current_fp = sources_fingerprint(workspace)
    if (not force and state.get("derived_at")
            and current_mtime <= float(state.get("sources_max_mtime") or 0)
            and current_fp == state.get("sources_fingerprint")):
        return {"skipped": "sources unchanged since last derivation"}

    skip_kinds = [str(k) for k in cfg["skip_kinds"]]
    legacy, watermark = collect_legacy(workspace)
    from_history, unmatched = derive_from_history(workspace, watermark, skip_kinds)
    keyed = (derive_from_interaction_log(workspace, watermark, skip_kinds)
             + from_history)
    keyed.sort(key=lambda pair: pair[0])
    derived = [ev for _, ev in keyed]
    assign_ids(derived, legacy)

    by_quarter: dict[str, list[dict[str, Any]]] = {}
    for ev in derived:
        parsed = parse_iso_dt(ev["ts"])
        by_quarter.setdefault(quarter_for(parsed), []).append(ev)

    quarters = sorted(set(legacy) | set(by_quarter))
    changed = False
    partitions_meta: list[dict[str, Any]] = []
    for quarter in quarters:
        path = partition_path(workspace, quarter)
        text = render_partition(quarter, legacy.get(quarter, []),
                                by_quarter.get(quarter, []))
        old = path.read_text() if path.exists() else None
        if old != text:
            write_text_atomic(path, text)
            changed = True
        meta = yaml.safe_load(text)
        partitions_meta.append({
            "quarter": quarter,
            "path": f"_memory/events/{quarter}.yaml",
            "first_event_at": meta.get("first_event_at"),
            "last_event_at": meta.get("last_event_at"),
            "event_count": meta.get("event_count"),
            "legacy_rows": meta.get("legacy_rows"),
            "by_kind": meta.get("by_kind"),
            "derived": True,
        })
    # Remove fully-derived partitions whose content no longer derives.
    root = events_root(workspace)
    if root.exists():
        for path in sorted(root.glob("*.yaml")):
            if path.stem not in quarters:
                path.unlink()
                changed = True

    content_hash = hashlib.sha256()
    for quarter in quarters:
        content_hash.update(partition_path(workspace, quarter).read_bytes())
    digest = content_hash.hexdigest()

    idx["schema_version"] = 1
    idx["partitions"] = partitions_meta
    new_state = dict(state)
    if changed or digest != state.get("content_hash") or not state.get("derived_at"):
        new_state["derived_at"] = now_iso()
    new_state["content_hash"] = digest
    new_state["sources_max_mtime"] = sources_max_mtime(workspace)
    new_state["sources_fingerprint"] = current_fp
    idx["derive_state"] = new_state
    save_yaml(index_path(workspace), idx)

    return {
        "partitions": len(quarters),
        "derived_events": len(derived),
        "legacy_rows": sum(len(v) for v in legacy.values()),
        "watermark": watermark.isoformat(timespec="seconds") if watermark else None,
        "changed": changed,
        "unmatched_headers": unmatched,
    }


def check_stale(workspace: Path) -> tuple[bool, str]:
    """Return (stale, message). Stale = sources newer than last derivation."""
    idx = load_yaml(index_path(workspace)) or {}
    state = idx.get("derive_state") or {}
    if not state.get("derived_at"):
        if count_unflagged_hand_rows(workspace):
            return True, ("events stream has never been derived and existing "
                          "partition rows are not flagged `legacy: true` — "
                          "run the migrate skill (0.16.0: flag-legacy, then "
                          "rebuild); a bare rebuild refuses to overwrite them")
        return True, "events stream has never been derived — run rebuild"
    current = sources_max_mtime(workspace)
    recorded = float(state.get("sources_max_mtime") or 0)
    if current > recorded:
        return True, ("sources changed since last derivation "
                      f"({state.get('derived_at')}) — run rebuild")
    if sources_fingerprint(workspace) != state.get("sources_fingerprint"):
        return True, ("source file set changed (moved/removed history.md) "
                      f"since last derivation ({state.get('derived_at')}) "
                      "— run rebuild")
    return False, f"events stream fresh (derived {state.get('derived_at')})"


def flag_legacy(workspace: Path, quarter: str) -> int:
    """One-time migration helper: add `legacy: true` to every row of a partition.

    Pure line insertion — every existing byte is preserved. Idempotent.
    """
    path = partition_path(workspace, quarter)
    if not path.exists():
        print(f"no partition at {path}", file=sys.stderr)
        return 1
    lines = path.read_text().splitlines(keepends=True)
    in_events = False
    starts = []
    for i, ln in enumerate(lines):
        if ln.rstrip("\n") == "events:":
            in_events = True
        elif in_events and ITEM_RE.match(ln):
            starts.append(i)
    flagged = 0
    for idx_n in range(len(starts) - 1, -1, -1):
        begin = starts[idx_n]
        end = starts[idx_n + 1] if idx_n + 1 < len(starts) else len(lines)
        region = "".join(lines[begin:end])
        if re.search(r"^    legacy: true$", region, flags=re.MULTILINE):
            continue
        lines.insert(begin + 1, "    legacy: true\n")
        flagged += 1
    if flagged:
        write_text_atomic(path, "".join(lines))
    print(f"flagged {flagged} row(s) legacy in {quarter} "
          f"({len(starts) - flagged} already flagged)")
    return 0


# --------------------------------------------------------------------- CLI

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="events_derive")
    parser.add_argument("cmd", nargs="?", default="rebuild",
                        choices=["rebuild", "check", "flag-legacy"])
    parser.add_argument("quarter", nargs="?", default=None,
                        help="Quarter for flag-legacy (e.g. 2026-Q2).")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even when sources are unchanged.")
    parser.add_argument("--allow-overwrite", action="store_true",
                        help="Rebuild even over hand-authored partition rows "
                             "not flagged `legacy: true` (DESTROYS them).")
    parser.add_argument("--check", action="store_true", dest="check_flag",
                        help="Alias for the `check` subcommand.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    cmd = "check" if args.check_flag else args.cmd
    if cmd == "rebuild":
        summary = rebuild(workspace, force=args.force,
                          allow_overwrite=args.allow_overwrite)
        if "error" in summary:
            print(f"refused: {summary['error']}", file=sys.stderr)
            return 1
        if "skipped" in summary:
            print(f"skipped: {summary['skipped']}")
            return 0
        print(f"rebuilt {summary['partitions']} partition(s): "
              f"{summary['derived_events']} derived + "
              f"{summary['legacy_rows']} legacy row(s); "
              f"watermark {summary['watermark']}; "
              f"{'content changed' if summary['changed'] else 'no content change'}")
        unmatched = summary.get("unmatched_headers") or []
        if unmatched:
            print(f"warning: {len(unmatched)} date-led H4 header(s) did not "
                  "parse and were NOT derived:", file=sys.stderr)
            for ref in unmatched:
                print(f"  {ref}", file=sys.stderr)
        return 0
    if cmd == "check":
        stale, message = check_stale(workspace)
        print(message)
        return 1 if stale else 0
    if cmd == "flag-legacy":
        if not args.quarter:
            print("flag-legacy requires a quarter (e.g. 2026-Q2)", file=sys.stderr)
            return 2
        return flag_legacy(workspace, args.quarter)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
