# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/render_status.py`."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml


def _add_task(workspace: Path, task: dict) -> None:
    todo_path = workspace / "_memory" / "todo.yaml"
    with todo_path.open() as fh:
        data = yaml.safe_load(fh)
    data.setdefault("tasks", []).append(task)
    with todo_path.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def test_render_workspace_todo_with_no_tasks(initialized_workspace: Path) -> None:
    """Render runs cleanly even with the placeholder-only template."""
    from superagent.tools.render_status import main as render_main

    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "workspace",
    ])
    assert rc == 0
    body = (initialized_workspace / "todo.md").read_text()
    assert "Done" in body


def test_render_with_p0_task(initialized_workspace: Path) -> None:
    """A P0 unscoped task lands in workspace todo.md."""
    from superagent.tools.render_status import main as render_main

    _add_task(initialized_workspace, {
        "id": "task-20260428-001",
        "title": "Pay electric bill",
        "description": "",
        "priority": "P0",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": "2026-04-28",
        "completed_date": None,
        "related_domain": None,
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })
    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "workspace",
    ])
    assert rc == 0
    body = (initialized_workspace / "todo.md").read_text()
    assert "task-20260428-001" in body
    assert "Pay electric bill" in body
    assert "P0" in body


def test_render_per_domain_status(initialized_workspace: Path) -> None:
    """A domain-scoped task lands in Domains/<domain>/status.md."""
    from superagent.tools.render_status import main as render_main

    _add_task(initialized_workspace, {
        "id": "task-20260428-002",
        "title": "Schedule annual physical",
        "description": "",
        "priority": "P1",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": "2026-05-15",
        "completed_date": None,
        "related_domain": "health",
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })
    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "health",
    ])
    assert rc == 0
    health_status = (initialized_workspace / "Domains" / "Health" / "status.md").read_text()
    assert "task-20260428-002" in health_status
    assert "Schedule annual physical" in health_status


def test_splice_preserves_curated_narrative(initialized_workspace: Path) -> None:
    """Re-rendering a domain status.md keeps the curated narrative intact.

    The Open/Done tables at the bottom get refreshed from todo.yaml, but
    everything strictly above `## Open` is preserved — RAG line, Recent
    Progress bullets, Active Blockers, Next Steps. The `_Last updated:`
    timestamp gets bumped to the new render time.
    """
    from superagent.tools.render_status import main as render_main

    # Seed a curated Vehicles status.md with substantive narrative.
    vehicles_status = initialized_workspace / "Domains" / "Vehicles" / "status.md"
    vehicles_status.parent.mkdir(parents=True, exist_ok=True)
    curated = (
        "# Status — Vehicles\n\n"
        "> **[Do not change manually — managed by Superagent]**\n\n"
        "_Last updated: 2026-05-01T00:00:00-07:00_\n\n"
        "---\n\n"
        "## Status\n\n"
        "### Current Status (RAG)\n\n"
        "**Yellow** — front-plate violation pending CHP sign-off.\n\n"
        "### Recent Progress\n\n"
        "- 2026-05-13 — CHP cited RU23806; plate installed 2026-05-27.\n\n"
        "### Active Blockers\n\n"
        "- Need a peace officer to sign Certificate of Correction.\n\n"
        "### Next Steps\n\n"
        "- Drop by Calaveras sheriff or local CHP office.\n\n"
        "---\n\n"
        "## Open\n\n"
        "<!-- stale tables here will be regenerated -->\n\n"
        "## Done\n\n"
        "<!-- stale -->\n"
    )
    vehicles_status.write_text(curated)

    _add_task(initialized_workspace, {
        "id": "task-20260513-002",
        "title": "Get Certificate of Correction signed",
        "description": "",
        "priority": "P0",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": "2026-05-26",
        "completed_date": None,
        "related_domain": "vehicles",
        "related_project": None,
        "related_asset": "audi-q7-2022",
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })

    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "vehicles",
    ])
    assert rc == 0
    body = vehicles_status.read_text()

    # Narrative preserved.
    assert "**Yellow**" in body
    assert "CHP cited RU23806" in body
    assert "peace officer to sign" in body
    assert "Drop by Calaveras sheriff" in body
    # `_Last updated:` bumped (no longer 2026-05-01).
    assert "_Last updated: 2026-05-01" not in body
    # Open table refreshed from todo.yaml.
    assert "task-20260513-002" in body
    assert "Get Certificate of Correction signed" in body
    # Stale stub comment is gone (we replaced from `## Open` down).
    assert "stale tables here" not in body
    assert "<!-- stale -->" not in body


def test_render_project_path_no_doubling(initialized_workspace: Path) -> None:
    """`path: workspace/Projects/<slug>` in projects-index does not double-nest.

    Earlier versions of render_status joined the workspace root with the
    `path` from projects-index naively, producing
    `workspace/workspace/Projects/<slug>/status.md`. The fix strips the
    leading `workspace/` from the index value before joining.
    """
    import yaml

    from superagent.tools.render_status import main as render_main

    # Register a project whose `path` carries the workspace-rooted form.
    projects_index = initialized_workspace / "_memory" / "projects-index.yaml"
    data = yaml.safe_load(projects_index.read_text()) or {"projects": []}
    data.setdefault("projects", []).append({
        "id": "test-project",
        "name": "Test Project",
        "status": "active",
        "path": "workspace/Projects/test-project",
    })
    projects_index.write_text(yaml.safe_dump(data, sort_keys=False))

    _add_task(initialized_workspace, {
        "id": "task-20260601-001",
        "title": "Project-scoped task",
        "description": "",
        "priority": "P2",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": None,
        "completed_date": None,
        "related_domain": None,
        "related_project": "test-project",
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })

    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "project:test-project",
    ])
    assert rc == 0
    assert (initialized_workspace / "Projects" / "test-project" / "status.md").is_file()
    assert not (initialized_workspace / "workspace").exists()


def test_workspace_todo_includes_all_open_tasks(initialized_workspace: Path) -> None:
    """workspace/todo.md is the unified view: every open task appears regardless of scope.

    The Scope column carries the `project:<slug>` / `domain:<id>` label
    so each row stays unambiguous when a task belongs to a project, a
    domain, or neither.
    """
    from superagent.tools.render_status import main as render_main

    _add_task(initialized_workspace, {
        "id": "task-20260428-010",
        "title": "Schedule annual physical",
        "description": "",
        "priority": "P1",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": "2026-05-15",
        "completed_date": None,
        "related_domain": "health",
        "related_project": None,
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })
    _add_task(initialized_workspace, {
        "id": "task-20260428-011",
        "title": "File 1099 supplement",
        "description": "",
        "priority": "P2",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": "2026-06-01",
        "completed_date": None,
        "related_domain": "finances",
        "related_project": "tax-2026",
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })
    _add_task(initialized_workspace, {
        "id": "task-20260428-012",
        "title": "Unscoped reminder",
        "description": "",
        "priority": "P3",
        "status": "open",
        "created": dt.datetime.now().astimezone().isoformat(),
        "due_date": None,
        "completed_date": None,
        "related_domain": None,
        "related_project": None,
        "related_asset": None,
        "related_account": None,
        "related_appointment": None,
        "related_bill": None,
        "tags": [],
        "source": "user",
    })

    rc = render_main([
        "--workspace", str(initialized_workspace),
        "--scope", "workspace",
    ])
    assert rc == 0
    body = (initialized_workspace / "todo.md").read_text()

    assert "task-20260428-010" in body
    assert "domain:health" in body
    assert "task-20260428-011" in body
    assert "project:tax-2026" in body  # project takes precedence over domain
    assert "task-20260428-012" in body
    assert "| Scope |" in body
    # Domain column header should no longer be present.
    assert "| Domain |" not in body
