# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/project_drift.py` (project lifecycle drift report)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

TODAY = dt.date(2026, 8, 31)


def _days_ago(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def _make_workspace(tmp_path: Path, projects: list[dict],
                    histories: dict[str, str] | None = None,
                    config: dict | None = None) -> Path:
    ws = tmp_path / "workspace"
    memory = ws / "_memory"
    memory.mkdir(parents=True)
    (memory / "projects-index.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "projects": projects}))
    if config is not None:
        (memory / "config.yaml").write_text(yaml.safe_dump(config))
    for slug, text in (histories or {}).items():
        folder = ws / "Projects" / slug
        folder.mkdir(parents=True)
        (folder / "history.md").write_text(text)
    return ws


def _row(rows: list[dict], pid: str) -> dict:
    return next(r for r in rows if r["id"] == pid)


def test_active_with_stale_history_hints_stalled(tmp_path: Path) -> None:
    from superagent.tools.project_drift import analyze

    ws = _make_workspace(
        tmp_path,
        projects=[
            {"id": "kitchen-reno", "status": "active", "path": "Projects/kitchen-reno"},
            {"id": "no-folder", "status": "active"},
        ],
        histories={
            "kitchen-reno": f"## Log\n\n#### {_days_ago(30)} — Picked countertops\n",
        },
    )
    rows = analyze(ws, today=TODAY)
    stale = _row(rows, "kitchen-reno")
    assert stale["days_since_history"] == 30
    assert stale["hint"] == "stalled?"
    # Active with no history.md at all is also stalled.
    assert _row(rows, "no-folder")["hint"] == "stalled?"


def test_paused_with_recent_history_hints_active(tmp_path: Path) -> None:
    from superagent.tools.project_drift import analyze

    ws = _make_workspace(
        tmp_path,
        projects=[
            {"id": "job-search", "status": "paused", "path": "Projects/job-search"},
            {"id": "trip-italy", "status": "planning", "path": "Projects/trip-italy"},
        ],
        histories={
            # H2-style date header is accepted too.
            "job-search": f"## {_days_ago(3)} — Recruiter call\n",
            "trip-italy": f"#### {_days_ago(60)} — Charter drafted\n",
        },
    )
    rows = analyze(ws, today=TODAY)
    assert _row(rows, "job-search")["hint"] == "active?"
    # Planning with old history is not contradictory — no hint.
    assert _row(rows, "trip-italy")["hint"] is None


def test_completed_past_archive_window_hints_archive_overdue(tmp_path: Path) -> None:
    from superagent.tools.project_drift import analyze

    projects = [
        {"id": "tax-2025", "status": "completed", "path": "Projects/tax-2025",
         "completed_date": _days_ago(120)},
        {"id": "tax-2026", "status": "completed", "path": "Projects/tax-2026",
         "completed_date": _days_ago(10)},
    ]
    ws = _make_workspace(tmp_path, projects=projects)
    rows = analyze(ws, today=TODAY)
    assert _row(rows, "tax-2025")["hint"] == "archive-overdue"  # default 90 days
    assert _row(rows, "tax-2026")["hint"] is None

    # config.preferences.projects.archive_after_days overrides the default.
    ws2 = _make_workspace(
        tmp_path / "override", projects=projects,
        config={"preferences": {"projects": {"archive_after_days": 200}}},
    )
    rows2 = analyze(ws2, today=TODAY)
    assert _row(rows2, "tax-2025")["hint"] is None


def test_healthy_project_and_stub_rows_produce_no_hints(tmp_path: Path) -> None:
    from superagent.tools.project_drift import analyze

    ws = _make_workspace(
        tmp_path,
        projects=[
            {"id": "garden-2026", "status": "active", "path": "Projects/garden-2026"},
            {"id": "", "status": "planning"},  # template stub row
        ],
        histories={
            "garden-2026": f"#### {_days_ago(5)} — Planted tomatoes\n",
        },
    )
    rows = analyze(ws, today=TODAY)
    assert [r["id"] for r in rows] == ["garden-2026"]
    assert rows[0]["hint"] is None


def test_workspace_prefixed_index_path_is_tolerated(tmp_path: Path) -> None:
    from superagent.tools.project_drift import analyze

    # Seen in the wild: index `path` carries a `workspace/` prefix.
    ws = _make_workspace(
        tmp_path,
        projects=[
            {"id": "attic-fan", "status": "active",
             "path": "workspace/Projects/attic-fan"},
        ],
        histories={
            "attic-fan": f"#### {_days_ago(4)} — Ordered the fan\n",
        },
    )
    rows = analyze(ws, today=TODAY)
    assert rows[0]["days_since_history"] == 4
    assert rows[0]["hint"] is None


def test_cli_prints_table_and_exit_code_reflects_drift(
    tmp_path: Path, capsys) -> None:
    from superagent.tools.project_drift import main

    ws = _make_workspace(
        tmp_path,
        projects=[
            {"id": "kitchen-reno", "status": "active", "path": "Projects/kitchen-reno"},
        ],
        histories={
            "kitchen-reno": "## Log\n\n#### 2020-01-01 — Started\n",
        },
    )
    rc = main(["--workspace", str(ws)])
    out = capsys.readouterr().out
    assert rc == 1  # drift present
    assert "kitchen-reno" in out
    assert "stalled?" in out

    rc_json = main(["--workspace", str(ws), "--json"])
    out_json = capsys.readouterr().out
    assert rc_json == 1
    assert '"hint": "stalled?"' in out_json
