#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Project lifecycle drift report — index status vs on-disk activity.

Reads `_memory/projects-index.yaml` plus each project's `history.md` under
`Projects/<slug>/` and reports, per project: the index status, days since
the newest history entry (parsed from `#### YYYY-MM-DD` / `## YYYY-MM-DD`
headers), and a drift hint when status and activity contradict:

    active             + no history entry in 21+ days      -> "stalled?"
    planning | paused  + history entry within 14 days      -> "active?"
    completed          + completed_date older than
                         config.preferences.projects.archive_after_days
                         (default 90)                      -> "archive-overdue"

Surface-only: this tool never mutates the index or the project folders.
Fixes stay user-approved — the `doctor` skill runs this and asks per row.

CLI:
    uv run python -m superagent.tools.project_drift           # compact table
    uv run python -m superagent.tools.project_drift --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

STALLED_AFTER_DAYS = 21
ACTIVE_WITHIN_DAYS = 14
DEFAULT_ARCHIVE_AFTER_DAYS = 90

DATE_HEADER_RE = re.compile(r"^#{2,4}\s+(\d{4}-\d{2}-\d{2})\b")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def _parse_date(value: Any) -> dt.date | None:
    """Coerce a YAML field (date, datetime, or ISO string) to a date."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def newest_history_date(history_path: Path) -> dt.date | None:
    """Return the newest `#### YYYY-MM-DD` / `## YYYY-MM-DD` header date."""
    if not history_path.exists():
        return None
    newest: dt.date | None = None
    try:
        text = history_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        m = DATE_HEADER_RE.match(line)
        if not m:
            continue
        d = _parse_date(m.group(1))
        if d and (newest is None or d > newest):
            newest = d
    return newest


def archive_after_days(workspace: Path) -> int:
    config = load_yaml(workspace / "_memory" / "config.yaml") or {}
    projects_prefs = ((config.get("preferences") or {}).get("projects") or {})
    value = projects_prefs.get("archive_after_days")
    return value if isinstance(value, int) else DEFAULT_ARCHIVE_AFTER_DAYS


def _history_path(workspace: Path, pid: str, rel_path: str) -> Path:
    """Resolve a project's history.md, tolerating `workspace/`-prefixed
    index paths (seen in the wild) alongside the documented
    workspace-relative form."""
    candidates = [workspace / rel_path / "history.md"]
    if rel_path.startswith("workspace/"):
        candidates.append(
            workspace / rel_path[len("workspace/"):] / "history.md")
    candidates.append(workspace / "Projects" / pid / "history.md")
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def analyze(workspace: Path, today: dt.date | None = None) -> list[dict[str, Any]]:
    """Return one report row per indexed project (template stub rows skipped)."""
    today = today or dt.date.today()
    archive_days = archive_after_days(workspace)
    index = load_yaml(workspace / "_memory" / "projects-index.yaml") or {}
    rows: list[dict[str, Any]] = []
    for row in index.get("projects") or []:
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        if not pid:
            continue  # template stub row
        status = row.get("status") or ""
        rel_path = row.get("path") or f"Projects/{pid}"
        last_entry = newest_history_date(_history_path(workspace, pid, rel_path))
        days_since = (today - last_entry).days if last_entry else None

        hint = None
        if status == "active" and (days_since is None or days_since >= STALLED_AFTER_DAYS):
            hint = "stalled?"
        elif status in ("planning", "paused") and days_since is not None \
                and days_since <= ACTIVE_WITHIN_DAYS:
            hint = "active?"
        elif status == "completed":
            completed = _parse_date(row.get("completed_date"))
            if completed and (today - completed).days > archive_days:
                hint = "archive-overdue"

        rows.append({
            "id": pid,
            "status": status,
            "last_history": last_entry.isoformat() if last_entry else None,
            "days_since_history": days_since,
            "hint": hint,
        })
    return rows


def format_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No projects in the index."
    header = ("project", "status", "last entry", "hint")
    cells = [header] + [(
        r["id"],
        r["status"] or "?",
        f"{r['days_since_history']}d ago" if r["days_since_history"] is not None else "none",
        r["hint"] or "-",
    ) for r in rows]
    widths = [max(len(c[i]) for c in cells) for i in range(len(header))]
    lines = ["  ".join(c[i].ljust(widths[i]) for i in range(len(header))).rstrip()
             for c in cells]
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="project_drift",
        description="Report project lifecycle drift (index status vs history activity).",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 2
    rows = analyze(workspace)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print(format_table(rows))
        drifted = sum(1 for r in rows if r["hint"])
        print(f"\n{len(rows)} project(s), {drifted} with drift hints.")
    return 1 if any(r["hint"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
