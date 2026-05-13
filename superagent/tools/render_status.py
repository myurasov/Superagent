#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Render scoped status.md files from `_memory/todo.yaml`.

For each domain referenced by an open task in todo.yaml, regenerate
`Domains/<domain>/status.md` with a priority-grouped task table per the
sync contract documented in `superagent/contracts/task-management.md` § 5.1.

Also regenerates the workspace-level `<workspace>/todo.md` cross-cutting view.

Usage:
  uv run python superagent/tools/render_status.py [--workspace PATH] [--scope SCOPE]

Scope is optional; if omitted, all affected scopes are re-rendered.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

PRIORITIES = ["P0", "P1", "P2", "P3"]
PRIORITY_HEADERS = {
    "P0": "## P0 — Today / Urgent",
    "P1": "## P1 — This Week",
    "P2": "## P2 — Active",
    "P3": "## P3 — Future / Aspirational",
}


def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    """Load YAML file; return None on failure."""
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def fmt_date(value: Any) -> str:
    """Format a date / datetime value for display; return em-dash if null."""
    if value is None:
        return "—"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%b %d")
    s = str(value)
    return s[:10] if len(s) >= 10 else s


def render_task_row(task: dict[str, Any], scope: str) -> str:
    """Render one task as a markdown table row."""
    tid = task.get("id", "—") or "—"
    title = (task.get("title", "") or "").replace("|", "\\|")[:60]
    due = fmt_date(task.get("due_date"))
    if scope == "workspace":
        domain = task.get("related_domain") or "—"
        return f"| {tid} | {title} | {due} | {domain} |"
    return f"| {tid} | {title} | {due} |"


def render_done_row(task: dict[str, Any]) -> str:
    """Render one done task as a markdown table row."""
    tid = task.get("id", "—") or "—"
    title = (task.get("title", "") or "").replace("|", "\\|")[:60]
    completed = fmt_date(task.get("completed_date"))
    return f"| {tid} | {title} | {completed} |"


def select_tasks_for_scope(tasks: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    """Filter task list down to those that belong to the given scope.

    Scope formats:
      - "workspace"             — tasks with no related_domain AND no related_project
      - "project:<slug>"        — tasks with related_project == slug
      - "<domain-id>"           — tasks with related_domain == domain-id (legacy)
    """
    if scope == "workspace":
        return [t for t in tasks if not t.get("related_domain") and not t.get("related_project")]
    if scope.startswith("project:"):
        proj = scope[len("project:"):]
        return [t for t in tasks if (t.get("related_project") or "").lower() == proj.lower()]
    return [t for t in tasks if (t.get("related_domain") or "").lower() == scope.lower()]


def group_open_by_priority(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket open / in_progress tasks by priority (P0..P3)."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        if t.get("status") not in ("open", "in_progress"):
            continue
        prio = t.get("priority", "P2")
        if prio not in PRIORITIES:
            prio = "P2"
        grouped[prio].append(t)
    for prio in grouped:
        grouped[prio].sort(key=lambda t: (t.get("due_date") is None, t.get("due_date") or ""))
    return grouped


def select_recent_done(
    tasks: list[dict[str, Any]], days: int = 30
) -> list[dict[str, Any]]:
    """Return tasks marked done in the last N days, newest first."""
    cutoff = dt.date.today() - dt.timedelta(days=days)
    done = []
    for t in tasks:
        if t.get("status") != "done":
            continue
        completed = t.get("completed_date")
        if isinstance(completed, str) and len(completed) >= 10:
            try:
                d = dt.date.fromisoformat(completed[:10])
            except ValueError:
                continue
        elif isinstance(completed, dt.date):
            d = completed
        else:
            continue
        if d >= cutoff:
            done.append((d, t))
    done.sort(key=lambda pair: pair[0], reverse=True)
    return [t for _d, t in done]


def render_status_md(
    scope_name: str, scope: str, tasks: list[dict[str, Any]]
) -> str:
    """Render the markdown body of a status.md (or workspace todo.md) file."""
    scoped = select_tasks_for_scope(tasks, scope)
    grouped = group_open_by_priority(scoped)
    done = select_recent_done(scoped, days=30)

    lines: list[str] = []
    if scope == "workspace":
        lines.append("# Todo — workspace")
    else:
        lines.append(f"# Status — {scope_name}")
    lines.append("")
    lines.append("> **[Do not change manually — managed by Superagent]**")
    lines.append("")
    lines.append(f"_Last updated: {now_iso()}_")
    lines.append("")
    if scope == "workspace":
        for prio in PRIORITIES:
            rows = grouped.get(prio, [])
            if not rows:
                continue
            lines.append(PRIORITY_HEADERS[prio])
            lines.append("")
            lines.append("| ID | Task | Due | Domain |")
            lines.append("|----|------|-----|--------|")
            for t in rows:
                lines.append(render_task_row(t, scope))
            lines.append("")
        lines.append("## Done")
        lines.append("")
        lines.append("| ID | Task | Completed |")
        lines.append("|----|------|-----------|")
        if done:
            for t in done:
                lines.append(render_done_row(t))
        else:
            lines.append("| — | — | — |")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append("## Status")
    lines.append("")
    lines.append("### Current Status (RAG)")
    lines.append("")
    lines.append("**Green** — see Open / Done below for the full picture.")
    lines.append("")
    lines.append("### Recent Progress")
    lines.append("")
    lines.append("<!-- regenerated by render_status.py — hand-edits welcome above -->")
    lines.append("")
    lines.append("### Active Blockers")
    lines.append("")
    lines.append("<!-- list blockers as `-` bullets -->")
    lines.append("")
    lines.append("### Next Steps")
    lines.append("")
    lines.append("<!-- list next steps as `-` bullets -->")
    lines.append("")
    lines.append("## Open")
    lines.append("")
    any_open = False
    for prio in PRIORITIES:
        rows = grouped.get(prio, [])
        if not rows:
            continue
        any_open = True
        lines.append(f"### {prio}")
        lines.append("")
        lines.append("| ID | Task | Due |")
        lines.append("|----|------|-----|")
        for t in rows:
            lines.append(render_task_row(t, scope))
        lines.append("")
    if not any_open:
        lines.append("<!-- no open tasks -->")
        lines.append("")
    lines.append("## Done")
    lines.append("")
    lines.append("| ID | Task | Completed |")
    lines.append("|----|------|-----------|")
    if done:
        for t in done:
            lines.append(render_done_row(t))
    else:
        lines.append("| — | — | — |")
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        prog="render_status",
        description="Regenerate scoped status.md files from todo.yaml.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace path (default: workspace next to framework).",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help="Optional single scope to render ('workspace' or a domain id). "
             "Default: regenerate all affected scopes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace: Path = args.workspace or framework.parent / "workspace"

    todo_path = workspace / "_memory" / "todo.yaml"
    domains_index = workspace / "_memory" / "domains-index.yaml"
    projects_index = workspace / "_memory" / "projects-index.yaml"
    if not todo_path.exists():
        print(f"No todo.yaml at {todo_path}", file=sys.stderr)
        return 1

    todo_data = load_yaml(todo_path) or {}
    tasks = todo_data.get("tasks") or []
    domains_data = load_yaml(domains_index) or {}
    domains = domains_data.get("domains") or []
    domain_by_id = {d["id"]: d for d in domains if isinstance(d, dict) and d.get("id")}
    projects_data = load_yaml(projects_index) or {}
    projects = projects_data.get("projects") or []
    project_by_id = {p["id"]: p for p in projects if isinstance(p, dict) and p.get("id")}

    scopes = []
    if args.scope:
        scopes = [args.scope]
    else:
        scopes = ["workspace"] + sorted(
            {(t.get("related_domain") or "").strip() for t in tasks if t.get("related_domain")}
        ) + [
            f"project:{(t.get('related_project') or '').strip()}"
            for t in tasks if t.get("related_project")
        ]
        scopes = list(dict.fromkeys(scopes))

    rendered = 0
    for scope in scopes:
        if not scope:
            continue
        if scope == "workspace":
            out_path = workspace / "todo.md"
            scope_name = "workspace"
        elif scope.startswith("project:"):
            proj_id = scope[len("project:"):]
            project = project_by_id.get(proj_id)
            if not project:
                print(f"  skip   unknown project id '{proj_id}'")
                continue
            scope_name = project.get("name", proj_id)
            project_path = project.get("path") or f"Projects/{proj_id}"
            out_path = workspace / project_path / "status.md"
        else:
            domain = domain_by_id.get(scope)
            if not domain:
                print(f"  skip   unknown domain id '{scope}'")
                continue
            scope_name = domain.get("name", scope)
            out_path = workspace / "Domains" / scope_name / "status.md"
        body = render_status_md(scope_name, scope, tasks)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body)
        print(f"  wrote  {out_path}")
        rendered += 1

    print(f"\nRendered {rendered} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
