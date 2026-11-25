# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for wake grants, the check of a granted wake, held duties, custom checks and the
one-fetching-machine rule (`tools/agents.py`, contracts/agents.md § 8).

A periodic wake runs only with a grant the owner approved, recorded on the
agent's registry row. `agents check ... --wake NAME` is what a scheduled wake
runs first: no grant, no run.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import agents as ag

PDT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 10, 8, 9, 0, tzinfo=PDT)  # a Thursday


def _registry(ws: Path) -> dict[str, Any]:
    return yaml.safe_load((ws / "_memory" / "agents.yaml").read_text())


def _row(ws: Path, name: str) -> dict[str, Any]:
    return next(r for r in _registry(ws)["agents"] if r.get("name") == name)


def _write_registry(ws: Path, rows: list[dict[str, Any]]) -> None:
    (ws / "_memory" / "agents.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "last_updated": None, "agents": rows}, sort_keys=False))


def _set_update_block(ws: Path, **values: Any) -> None:
    config = ws / "_memory" / "config.yaml"
    data = yaml.safe_load(config.read_text())
    data["preferences"]["update"].update(values)
    config.write_text(yaml.safe_dump(data, sort_keys=False))


def _make_current(ws: Path) -> None:
    path = ws / "_memory" / "context.yaml"
    ctx = yaml.safe_load(path.read_text())
    recent = (NOW - dt.timedelta(minutes=15)).isoformat()
    ctx["last_check"] = recent
    for key in ("daily_update", "weekly_review", "monthly_review", "tailor_review", "doctor",
                "update_full", "update_light"):
        ctx["last_runs"][key] = recent
    path.write_text(yaml.safe_dump(ctx, sort_keys=False))


def _grant(ws: Path, agent: str = "updater", **kw: Any) -> dict[str, Any]:
    terms = {"wake": "update-light", "trigger": "every 30m", "purpose": "keep the stores current",
             "approval": "yes, every 30 minutes", "duty": "update-light", **kw}
    return ag.grant(ws, agent, now=NOW, **terms)


# ------------------------------------------------------------------- grants

def test_grant_creates_the_row_and_keeps_the_template(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    before = (ws / "_memory" / "agents.yaml").read_text()
    entry = _grant(ws, cost_per_run_usd=0.15)
    assert entry == {"name": "update-light", "trigger": "every 30m", "duty": "update-light",
                     "purpose": "keep the stores current", "cost_per_run_usd": 0.15, "ends": None,
                     "status": "active", "approval": "yes, every 30 minutes",
                     "granted_at": NOW.isoformat()}
    row = _row(ws, "updater")
    assert (row["role"], row["handle"], row["status"], row["host"]) == ("updater", "agent:updater", "off", None)
    assert row["wakes"] == [entry] and row["provenance"]["source"] == "agents"
    after = (ws / "_memory" / "agents.yaml").read_text()
    assert after.startswith(before.split("last_updated:")[0]) and "#   wakes       — " in after
    assert ag.grants(ws, "updater") == [entry]
    data = ag.roster(ws)
    assert data["errors"] == [] and data["autocode_enabled"] is False
    assert next(a for a in data["agents"] if a["name"] == "updater")["grants"] == [entry]


def test_a_second_grant_is_added_and_the_same_name_is_replaced(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _grant(ws)
    _grant(ws, wake="update-full", trigger="cron \"0 6 * * *\"", duty="update-full")
    replaced = _grant(ws, trigger="every 1h until 2027-03-01", approval="hourly until March 1")
    assert [g["name"] for g in ag.grants(ws, "updater")] == ["update-full", "update-light"]
    assert replaced["ends"] == "2027-03-01", "an end in the trigger is the grant's end"
    assert _row(ws, "updater")["wakes"][1]["trigger"] == "every 1h until 2027-03-01"
    assert len([r for r in _registry(ws)["agents"] if r.get("name") == "updater"]) == 1


def test_a_project_agent_needs_its_row_first(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    with pytest.raises(ar.AgentError, match="exists only through its row"):
        ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d until 2027-03-01",
                 purpose="watch the permit", approval="yes", now=NOW)
    _write_registry(ws, [{"name": "project:kitchen", "role": "project", "status": "dormant",
                          "notes": "keep me"}])
    entry = ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d until 2027-03-01",
                     purpose="watch the permit", approval="daily until March 1, yes", now=NOW)
    row = _row(ws, "project:kitchen")
    assert row["wakes"] == [entry] and row["notes"] == "keep me" and row["status"] == "dormant"
    assert entry["duty"] is None and entry["ends"] == "2027-03-01"


@pytest.mark.parametrize("agent, kw, match", [
    ("ghost", {}, "no role named 'ghost'"),
    ("updater:dev", {}, "a dev twin has no periodic wake"),
    ("updater", {"wake": "has a space"}, "wake name"),
    ("updater", {"trigger": " "}, "needs its trigger"),
    ("updater", {"approval": ""}, "needs its approval"),
    ("updater", {"duty": "nope"}, "has no duty 'nope'"),
    ("updater", {"ends": "next spring"}, "not an ISO date"),
    ("project", {"duty": None}, "needs a slug"),
])
def test_bad_grants_are_refused(initialized_workspace: Path, agent: str, kw: dict, match: str) -> None:
    ws = initialized_workspace
    before = (ws / "_memory" / "agents.yaml").read_text()
    with pytest.raises(ar.AgentError, match=match):
        _grant(ws, agent, **kw)
    assert (ws / "_memory" / "agents.yaml").read_text() == before


def test_suspend_resume_and_revoke(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _grant(ws)
    _grant(ws, wake="update-full", trigger="at 06:00", duty="update-full")
    assert ag.change_grants(ws, "updater", action="suspend", now=NOW) == ["update-light", "update-full"]
    assert {g["status"] for g in ag.grants(ws, "updater")} == {"suspended"}
    assert ag.change_grants(ws, "updater", action="suspend", now=NOW) == [], "nothing left to suspend"
    assert ag.change_grants(ws, "updater", wake="update-light", action="resume", now=NOW) == ["update-light"]
    assert {g["name"]: g["status"] for g in ag.grants(ws, "updater")} == {
        "update-light": "active", "update-full": "suspended"}
    assert ag.change_grants(ws, "updater", wake="update-full", action="revoke", now=NOW) == ["update-full"]
    assert [g["name"] for g in ag.grants(ws, "updater")] == ["update-light"]
    with pytest.raises(ar.AgentError, match="no grant named 'gone'"):
        ag.change_grants(ws, "updater", wake="gone", action="revoke", now=NOW)
    with pytest.raises(ar.AgentError, match="has no registry row"):
        ag.change_grants(ws, "bookkeeper", action="suspend", now=NOW)


# ---------------------------------------------------- the check of a wake

def _due_watcher(ws: Path) -> None:
    target = ws / "watched.txt"
    target.write_text("v1")
    registry = ws / "Sources" / "Watchlist"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "Doc.ref.md").write_text(
        f"---\nref_version: 2\ntitle: Doc\nwatch:\n  type: path\n  path: {target}\n  schedule: daily\n---\n")


def test_a_wake_without_a_grant_does_not_run(initialized_workspace: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    _due_watcher(ws)
    base = ["--workspace", str(ws), "--now", NOW.isoformat()]
    assert ag.main(["check", "updater", "update-light", *base]) == 0, "the duty itself is due"
    capsys.readouterr()
    assert ag.main(["check", "updater", "update-light", "--wake", "update-light", *base]) == 1
    assert capsys.readouterr().out == "", "no grant: skipped in silence, at no cost"
    assert ag.check(ws, "updater", "update-light", now=NOW, wake="update-light") == (
        False, "updater: wake `update-light` has no grant")
    _grant(ws)
    assert ag.main(["check", "updater", "update-light", "--wake", "update-light", *base]) == 0
    assert capsys.readouterr().out.startswith("updater update-light: due: 1 watcher(s) due: doc")
    ag.change_grants(ws, "updater", action="suspend", now=NOW)
    assert ag.check(ws, "updater", "update-light", now=NOW, wake="update-light") == (
        False, "updater: the grant of wake `update-light` is suspended")


def test_a_grant_ends_with_its_day(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _write_registry(ws, [{"name": "project:kitchen", "role": "project", "status": "dormant"}])
    ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d", ends="2026-10-08",
             purpose="watch the permit", approval="yes, through the 8th", now=NOW)
    assert ag.check(ws, "project:kitchen", None, now=NOW, wake="permit") == (
        True, "project:kitchen: wake `permit` is granted"), "a wake with no duty runs while its grant holds"
    last_minute = dt.datetime(2026, 10, 8, 23, 59, tzinfo=PDT)
    assert ag.check(ws, "project:kitchen", None, now=last_minute, wake="permit")[0] is True
    next_day = dt.datetime(2026, 10, 9, 0, 1, tzinfo=PDT)
    assert ag.check(ws, "project:kitchen", None, now=next_day, wake="permit") == (
        False, "project:kitchen: the grant of wake `permit` ended 2026-10-08")
    with pytest.raises(ar.AgentError, match="name a duty, or a granted wake"):
        ag.check(ws, "project:kitchen", None, now=NOW)


# ------------------------------------------------------------- held duties

def test_update_duties_are_held_outside_work_hours_and_on_off_days(
        initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    _due_watcher(ws)
    assert ag.update_window(ws, NOW) is None
    _set_update_block(ws, work_hours="10:00-22:00")
    assert ag.update_window(ws, NOW) == "outside work hours (10:00-22:00)"
    assert ag.check(ws, "updater", "update-light", now=NOW) == (False, "")
    rows, _errors = ag.duty_rows(ws, now=NOW)
    light = next(r for r in rows if r["duty"] == "update-light")
    assert light["state"] == "due" and light["held"] == "outside work hours (10:00-22:00)"
    assert next(r for r in rows if r["duty"] == "weekly-review")["held"] is None, "only the update is held"
    assert "[held: outside work hours (10:00-22:00)]" in "\n".join(ag.render_duties(rows))
    assert ag.main(["duties", "--due", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert "update-light" not in capsys.readouterr().out, "a scheduler would not run it now"
    assert ag.check(ws, "updater", "update-light", now=NOW.replace(hour=10)) [0] is True
    _set_update_block(ws, work_hours="22:00-06:00")  # a window across midnight
    assert ag.update_window(ws, NOW.replace(hour=23)) is None
    assert ag.update_window(ws, NOW.replace(hour=5, minute=59)) is None
    assert ag.update_window(ws, NOW.replace(hour=6)) == "outside work hours (22:00-06:00)"
    _set_update_block(ws, work_hours=None, off_days=["Thursday"])
    assert ag.update_window(ws, NOW) == "an off day (Thursday)"
    assert ag.update_window(ws, NOW + dt.timedelta(days=1)) is None
    _set_update_block(ws, off_days=["2026-10-08", "sat"])
    assert ag.update_window(ws, NOW) == "an off day (2026-10-08)"
    assert ag.update_window(ws, NOW + dt.timedelta(days=2)) == "an off day (Saturday)"
    _set_update_block(ws, work_hours="whenever", off_days=["someday"])
    assert ag.update_window(ws, NOW) is None, "a value that cannot be read holds nothing"


# ----------------------------------------------------- one fetching machine

def test_only_the_updaters_machine_fetches(initialized_workspace: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    assert ag.may_fetch(ws) == (True, ""), "no separate Updater: every machine may fetch"
    _write_registry(ws, [{"name": "updater", "role": "updater", "host": "box1", "status": "off"}])
    assert ag.may_fetch(ws)[0] is True, "a row that is not active places nothing"
    _write_registry(ws, [{"name": "updater", "role": "updater", "host": "box1", "status": "active"}])
    ok, why = ag.may_fetch(ws)
    assert not ok and why.startswith("the updater agent runs on box1: this machine (testbox) does not fetch")
    assert ag.main(["may-fetch", "--workspace", str(ws)]) == 1
    assert "runs on box1" in capsys.readouterr().out
    _write_registry(ws, [{"name": "updater", "role": "updater", "host": "testbox", "status": "active"}])
    assert ag.may_fetch(ws) == (True, "")
    assert ag.main(["may-fetch", "--workspace", str(ws)]) == 0 and capsys.readouterr().out == ""


# ------------------------------------------------------------ custom checks

ROLE = """---
name: gardener
kind: durable
description: Waters the plants.
skills: [todo]
owns: []
authority: act
model_tier: runner
duties:
  - {id: water, skill: todo, tier: act, schedule: on-request, check: "tool:water_due"}
---
# Gardener
"""


def _gardener(ws: Path, script: str | None) -> None:
    folder = ws / "_custom" / "agents"
    (folder / "gardener.agent.md").write_text(ROLE)
    (folder / "gardener.agent.dev.md").write_text("---\ndevelops: gardener\nmodel_tier: dev\n---\n# dev\n")
    if script is not None:
        (ws / "_custom" / "tools" / "water_due.py").write_text(script)


def test_a_custom_role_runs_its_own_check(initialized_workspace: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _gardener(ws, "import sys\nprint('the soil is dry')\nsys.exit(0)\n")
    roles, errors = ar.collect(workspace=ws)
    assert errors == [] and any(r.name == "gardener" for r in roles)
    rows, _errors = ag.duty_rows(ws, now=NOW, agent="gardener")
    assert rows[0]["state"] == "custom", "a listing never runs the script"
    assert ag.check(ws, "gardener", "water", now=NOW) == (True, "gardener water: due: the soil is dry")
    (ws / "_custom" / "tools" / "water_due.py").write_text("import sys\nsys.exit(1)\n")
    assert ag.check(ws, "gardener", "water", now=NOW) == (False, "")
    (ws / "_custom" / "tools" / "water_due.py").write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(7)\n")
    with pytest.raises(ag.CheckFailedError, match="failed with exit 7: boom"):
        ag.check(ws, "gardener", "water", now=NOW)
    assert ag.main(["check", "gardener", "water", "--workspace", str(ws)]) == 3, (
        "a check that cannot say is a failure, never 'not due'")
    assert "failed with exit 7" in capsys.readouterr().err


def test_a_custom_check_must_exist_and_is_for_custom_roles_only(initialized_workspace: Path,
                                                                framework_dir: Path) -> None:
    ws = initialized_workspace
    _gardener(ws, None)
    _roles, errors = ar.collect(workspace=ws)
    assert errors == ["gardener.agent.md: duty #1: check script `_custom/tools/water_due.py` does not exist"]
    problems = ar.check_frontmatter(yaml.safe_load(ROLE.split("---")[1]), Path("gardener.agent.md"),
                                    skills={"todo"}, schedules=set())
    assert any("a custom role may also say `tool:<name>`" in p for p in problems), (
        "a framework role cannot name a script of the workspace")
    assert ar.custom_check_name("tool:water_due") == "water_due"
    assert ar.custom_check_name("cadence") is None and ar.custom_check_name("tool:../x") is None


# ---------------------------------------------------------------------- CLI

def test_grant_and_revoke_commands(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    base = ["--workspace", str(ws), "--now", NOW.isoformat()]
    assert ag.main(["grant", "updater", "--wake", "update-light", "--trigger", "every 30m", *base]) == 2
    assert "a grant needs --purpose, --approval" in capsys.readouterr().err
    assert ag.main(["grant", "updater", "--wake", "update-light", "--trigger", "every 30m",
                    "--duty", "update-light", "--purpose", "keep current", "--approval", "yes",
                    "--cost", "0.2", *base]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["updater: wake `update-light` granted (every 30m)",
                   "its check: uv run python -m superagent.tools.agents check updater update-light "
                   "--wake update-light"]
    assert ag.main(["grant", "updater", "--suspend", *base]) == 0
    assert capsys.readouterr().out.strip() == "updater: grant `update-light` suspended"
    assert ag.main(["grant", "updater", "--wake", "update-light", "--resume", *base]) == 0
    assert capsys.readouterr().out.strip() == "updater: grant `update-light` resumed"
    assert ag.main(["revoke", "updater", "--wake", "update-light", *base]) == 0
    assert capsys.readouterr().out.strip() == "updater: grant `update-light` removed"
    assert ag.grants(ws, "updater") == []
    assert ag.main(["revoke", "updater", "--wake", "update-light", *base]) == 2
    (ws / "_memory" / "agents.yaml").write_text("agents: [unclosed\n")
    assert ag.main(["grant", "updater", "--wake", "w", "--trigger", "every 1h", "--purpose", "p",
                    "--approval", "yes", *base]) == 1
    assert (ws / "_memory" / "agents.yaml").read_text() == "agents: [unclosed\n"


# ------------------------------------------------------------------ the row

def test_set_creates_and_updates_a_row(initialized_workspace: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    row = ag.set_row(ws, "project:kitchen", now=NOW, source="add-agent")
    assert (row["role"], row["handle"], row["project"], row["status"], row["wakes"]) == (
        "project", "agent:project:kitchen", "project:kitchen", "off", [])
    assert _row(ws, "project:kitchen")["provenance"] == {"source": "add-agent", "at": NOW.isoformat()}
    assert "sa:project:kitchen" in [a["id"] for a in ag.roster(ws)["agents"]]
    _grant(ws)
    updated = ag.set_row(ws, "updater", host="box1", status="active", now=NOW)
    assert (updated["host"], updated["status"]) == ("box1", "active")
    assert len(updated["wakes"]) == 1, "a row change never touches the grants"
    before = (ws / "_memory" / "agents.yaml").read_text()
    ag.set_row(ws, "updater", host="box1", now=NOW + dt.timedelta(hours=1))
    assert (ws / "_memory" / "agents.yaml").read_text() == before, "nothing to change, nothing written"
    for agent, fields, match in (("updater", {"wakes": []}, "not a field of a registry row: wakes"),
                                 ("updater", {"status": "asleep"}, "status 'asleep'"),
                                 ("updater:dev", {}, "a dev twin"), ("project:dev", {}, "a dev twin"),
                                 ("project", {}, "needs a slug"), ("ghost", {}, "no role named")):
        with pytest.raises(ar.AgentError, match=match):
            ag.set_row(ws, agent, now=NOW, **fields)
    assert ag.main(["set", "updater", "--status", "paused", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip() == "updater: status paused, host box1"
    assert ag.main(["set", "ghost", "--status", "off", "--workspace", str(ws)]) == 2


# ---------------------------------------------------------------- heartbeat

def test_heartbeat_is_silent_without_separate_agents_and_flags_a_quiet_loop(
        initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    assert ag.heartbeat_lines(ws, now=NOW) == []
    assert ag.main(["heartbeat", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert capsys.readouterr().out == ""
    _grant(ws)
    ag.set_row(ws, "updater", status="active", host="box1", now=NOW)
    ag.set_row(ws, "project:kitchen", status="dormant", now=NOW)
    ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d until 2027-03-01", purpose="p",
             approval="yes", now=NOW)
    ag.change_grants(ws, "project:kitchen", action="suspend", now=NOW)
    assert ag.heartbeat_lines(ws, now=NOW) == [
        "Agents: project:kitchen dormant; updater active on box1.",
        "Update loop: last light update 15 min ago, last full 15 min ago.",
        "Granted wakes: updater `update-light` (every 30m).",
        "Suspended grants: project:kitchen `permit`.",
    ]
    later = NOW + dt.timedelta(hours=3)
    assert ag.heartbeat_lines(ws, now=later)[1].endswith(
        "No light update in over 60 minutes of work hours, where every slot runs one: "
        "look at the updater agent."), "the light period (30 minutes) plus half an hour"
    _set_update_block(ws, work_hours="13:00-14:00")
    assert "No light update" not in ag.heartbeat_lines(ws, now=later)[1], "quiet on purpose: outside work hours"


def test_a_loop_held_overnight_is_not_quiet_when_the_day_begins(initialized_workspace: Path) -> None:
    """The quiet time counts from the start of work hours: no alarm at the first slot of the day."""
    ws = initialized_workspace
    _make_current(ws)
    _grant(ws)
    ag.set_row(ws, "updater", status="active", host="box1", now=NOW)
    _set_update_block(ws, work_hours="08:45-20:00")
    assert ag.window_opened(ws, NOW) == NOW.replace(hour=8, minute=45)
    assert ag.light_period(ws) == dt.timedelta(minutes=30)
    path = ws / "_memory" / "context.yaml"
    ctx = yaml.safe_load(path.read_text())
    ctx["last_runs"]["update_light"] = (NOW - dt.timedelta(hours=14)).isoformat(timespec="seconds")
    path.write_text(yaml.safe_dump(ctx, sort_keys=False))
    assert "No light update" not in ag.heartbeat_lines(ws, now=NOW)[1], (
        "work hours began 15 minutes ago: the first slot may still be running")
    late = NOW + dt.timedelta(minutes=50)
    assert "No light update in over 60 minutes of work hours" in ag.heartbeat_lines(ws, now=late)[1], (
        "65 minutes into work hours with no run: a true alarm")
