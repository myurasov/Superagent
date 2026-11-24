# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.31.0 migration (agent interests): role interests on the rows of running agents."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from superagent.tools import agents as ag
from superagent.tools.version import current_version, find_chain, parse

NOW = dt.datetime(2026, 11, 23, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))


def _registry(ws: Path, rows: list[dict]) -> None:
    (ws / "_memory" / "agents.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "last_updated": None, "agents": rows}, sort_keys=False))


def _before(ws: Path) -> None:
    """A 0.30.0-shaped registry: rows without `interests`."""
    _registry(ws, [
        {"name": "bookkeeper", "role": "bookkeeper", "handle": "agent:bookkeeper", "project": None,
         "status": "active", "wakes": [
             {"name": "after-harvest", "trigger": "fire-only", "duty": None, "purpose": "reconcile",
              "status": "active", "approval": "yes", "granted_at": "2026-11-22T09:00:00-08:00"}]},
        {"name": "project:kitchen-reno", "role": "project", "handle": "agent:project:kitchen-reno",
         "project": "project:kitchen-reno", "status": "dormant", "wakes": []},
        {"name": "project:garden", "role": "project", "handle": "agent:project:garden",
         "project": "project:garden", "status": "off", "wakes": []},
    ])


def test_migration_is_registered(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.31.0")
    assert row["from_version"] == "0.30.0" and row["revertible"] is True and row["breaking"] is False
    assert not (framework_dir / "migrations" / "0.31.0").exists(), "the step is one tool command"
    assert parse(current_version()) >= parse("0.31.0")
    assert [step.to_version for step in find_chain("0.30.0", "0.31.0")] == ["0.31.0"]
    assert [step.to_version for step in find_chain("0.29.0", "0.31.0")] == ["0.30.0", "0.31.0"]
    front = yaml.safe_load((framework_dir / "migrations" / "0.31.0.md").read_text().split("---")[1])
    assert front["touches"] == ["workspace/.version", "workspace/_memory/agents.yaml"]


def test_rows_from_the_version_before_read_as_no_interest(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _before(ws)
    assert ag.interests(ws, "bookkeeper") == []
    assert ag.interested(ws, ["domain:finances"], when="harvest", now=NOW) == []
    assert not any(line.startswith("Interests") for line in ag.heartbeat_lines(ws, now=NOW))


def test_the_step_writes_role_interests_for_agents_a_session_holds_and_is_idempotent(
        initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _before(ws)
    running = [a["name"] for a in ag.roster(ws)["agents"]
               if a["status"] in ag.REACHABLE and a["role"] not in ("superagent", "updater")]
    assert running == ["bookkeeper", "project:kitchen-reno"]
    for name in running:
        added, notes = ag.default_interests(ws, name, now=NOW)
        assert added and notes == []
    told = {r["agent"]: r["deliver"] for r in ag.interested(ws, ["domain:finances"], when="harvest", now=NOW)}
    assert told == {"bookkeeper": "fire"}, "the Bookkeeper hears of a bank harvest from the first cycle on"
    assert ag.interests(ws, "project:garden") == [], "an agent no session holds is left alone"
    (line,) = [ln for ln in ag.heartbeat_lines(ws, now=NOW) if ln.startswith("Interests")]
    assert "bookkeeper:" in line and "project:kitchen-reno: project:kitchen-reno" in line
    before = (ws / "_memory" / "agents.yaml").read_text()
    for name in running:
        assert ag.default_interests(ws, name, now=NOW) == ([], [])
    assert (ws / "_memory" / "agents.yaml").read_text() == before
