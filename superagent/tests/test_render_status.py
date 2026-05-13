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
