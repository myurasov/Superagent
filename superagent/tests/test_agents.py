# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/agents.py`: the roster, ids, duties, and the zero-cost duty check.

Also pins that `cadence status` and `agents duties --overdue` report the same
findings, each under the agent whose duty it is (contracts/agents.md § 3).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import agents as ag
from superagent.tools import cadence as cd

PDT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 10, 8, 9, 0, tzinfo=PDT)


def _ago(days: float = 0, hours: float = 0) -> str:
    return (NOW - dt.timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


def _set_runs(ws: Path, *, last_check: str | None, **runs: str) -> None:
    path = ws / "_memory" / "context.yaml"
    ctx = yaml.safe_load(path.read_text())
    ctx["last_check"] = last_check
    ctx["last_runs"].update(runs)
    path.write_text(yaml.safe_dump(ctx, sort_keys=False))


def _make_current(ws: Path) -> None:
    _set_runs(ws, last_check=_ago(hours=0.25), daily_update=_ago(hours=0.25), weekly_review=_ago(1),
              monthly_review=_ago(hours=2), tailor_review=_ago(1), doctor=_ago(1),
              update_full=_ago(hours=1), update_light=_ago(hours=0.25))


def _registry(ws: Path, rows: list[dict[str, Any]]) -> None:
    (ws / "_memory" / "agents.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "last_updated": None, "agents": rows}, sort_keys=False))


def _set_autocode(ws: Path, block: str) -> None:
    config = ws / "_memory" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    start = text.index("  autocode:\n")
    end = text.index("\n# --- Data sources", start)
    config.write_text(text[:start] + block + text[end:], encoding="utf-8")


def _run(argv: list[str], ws: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = ag.main([*argv, "--workspace", str(ws), "--now", NOW.isoformat()])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------- roster

def test_roster_of_a_fresh_workspace(initialized_workspace: Path) -> None:
    data = ag.roster(initialized_workspace)
    assert data["errors"] == [] and data["prefix"] == "sa"
    assert [(a["id"], a["kind"], a["status"], a["dev_id"]) for a in data["agents"]] == [
        ("sa:superagent", "lead", "off", "sa:superagent:dev"),
        ("sa:bookkeeper", "durable", "off", "sa:bookkeeper:dev"),
        ("sa:updater", "durable", "off", "sa:updater:dev"),
    ]
    assert all(not a["registered"] and a["host"] is None for a in data["agents"])
    project = next(r for r in data["roles"] if r["name"] == "project")
    assert project["instances"] == 0 and project["dev_id"] == "sa:project:dev"
    lines = ag.render_roster(data)
    assert lines[0].split() == ["ID", "ROLE", "KIND", "STATUS", "HOST", "DEV", "TWIN"]
    assert lines[-1].startswith("sa:project:<slug>") and "no agent yet" in lines[-1]


def test_roster_reads_the_registry_and_the_configured_prefix(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _set_autocode(ws, '  autocode:\n    id_prefix: "fam"\n    models:\n'
                      '      runner: {model: "prov/big", variant: "high"}\n'
                      '      dev: {model: "prov/big", variant: "xhigh"}\n    daily_usd: 5\n')
    _registry(ws, [
        {"name": "", "role": "", "status": "off"},  # the template's example row
        {"name": "updater", "role": "updater", "host": "box1", "status": "active", "daily_usd": 2,
         "wakes": [{"name": "tick", "trigger": "every 30m"}]},
        {"name": "project:tax-2026", "role": "project", "project": "project:tax-2026",
         "status": "dormant", "model": "prov/small"},
    ])
    data = ag.roster(ws)
    assert data["errors"] == []
    rows = {a["id"]: a for a in data["agents"]}
    assert set(rows) == {"fam:superagent", "fam:bookkeeper", "fam:updater", "fam:project:tax-2026"}
    updater = rows["fam:updater"]
    assert (updater["status"], updater["host"], updater["daily_usd"], updater["wakes"]) == (
        "active", "box1", 2, 1)
    assert (updater["model"], updater["variant"]) == ("prov/big", "high"), "the runner default"
    assert (updater["dev_model"], updater["dev_variant"]) == ("prov/big", "xhigh")
    project = rows["fam:project:tax-2026"]
    assert project["model"] == "prov/small" and project["variant"] == "high", "the row overrides"
    assert project["dev_id"] == "fam:project:dev" and project["status"] == "dormant"
    assert rows["fam:superagent"]["daily_usd"] == 5, "the configured default cap"
    assert not any("no agent yet" in line for line in ag.render_roster(data))


def test_roster_names_rows_that_fit_no_role(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _registry(ws, [
        {"name": "ghost", "role": "ghost"},
        {"name": "updater", "role": "bookkeeper"},
        {"name": "project", "role": "project"},
        {"name": "bookkeeper", "role": "bookkeeper", "status": "sleeping"},
        {"name": "bookkeeper", "role": "bookkeeper"},
    ])
    errors = "\n".join(ag.roster(ws)["errors"])
    assert "`ghost` has no role file" in errors
    assert "`updater` names role 'bookkeeper'" in errors
    assert "`project` is not `project:<slug>`" in errors
    assert "status 'sleeping'" in errors
    assert "`bookkeeper` is listed twice" in errors


def test_missing_or_broken_registry(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    path = ws / "_memory" / "agents.yaml"
    path.unlink()
    assert ag.load_registry(ws) == ([], []), "no file: an empty registry"
    path.write_text("agents: [\n")
    rows, errors = ag.load_registry(ws)
    assert rows == [] and "cannot be read" in errors[0]


def test_list_command(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["list"], initialized_workspace, capsys)
    assert code == 0 and err == ""
    assert out.splitlines()[1].split()[:4] == ["sa:superagent", "superagent", "lead", "off"]
    code, out, _err = _run(["list", "--json"], initialized_workspace, capsys)
    assert code == 0 and json.loads(out)["prefix"] == "sa"


def test_a_bad_prefix_is_a_usage_error(initialized_workspace: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _set_autocode(ws, '  autocode:\n    id_prefix: "Not Valid"\n')
    for argv in (["list"], ["id", "updater"]):
        code, out, err = _run(argv, ws, capsys)
        assert code == 2 and out == "" and "id prefix" in err


# ----------------------------------------------------------------------- id

def test_id_command(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    assert _run(["id", "updater"], ws, capsys)[:2] == (0, "sa:updater\n")
    assert _run(["id", "updater", "--dev"], ws, capsys)[:2] == (0, "sa:updater:dev\n")
    assert _run(["id", "project:tax-2026"], ws, capsys)[:2] == (0, "sa:project:tax-2026\n")
    code, out, err = _run(["id", "project"], ws, capsys)
    assert code == 2 and out == "" and "needs a slug" in err
    code, _out, err = _run(["id", "nobody"], ws, capsys)
    assert code == 2 and "no role named 'nobody'" in err


def test_a_project_agents_id_follows_the_id_name_on_its_row(initialized_workspace: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    for slug in ("reno-kitchen", "reno-bath", "garden"):
        ag.set_row(ws, f"project:{slug}", status="dormant", now=NOW)
    row = ag.set_row(ws, "project:reno-kitchen", id_name="project:reno:kitchen", now=NOW)
    assert (row["name"], row["id_name"]) == ("project:reno-kitchen", "project:reno:kitchen")
    assert (row["handle"], row["project"]) == ("agent:project:reno-kitchen", "project:reno-kitchen"), (
        "the name, the handle and the project stay")
    data = ag.roster(ws)
    assert data["errors"] == []
    ids = {a["name"]: a["id"] for a in data["agents"]}
    assert ids["project:reno-kitchen"] == "sa:project:reno:kitchen"
    assert ids["project:reno-bath"] == "sa:project:reno-bath" and ids["project:garden"] == "sa:project:garden"
    assert [line for line in ag.render_roster(data) if " is the agent " in line] == [
        "sa:project:reno:kitchen is the agent `project:reno-kitchen` (the id name on its row); "
        "tools take the name"]
    # `agents id` takes the name or the id name, and prints the id on the row
    assert _run(["id", "project:reno-kitchen"], ws, capsys)[:2] == (0, "sa:project:reno:kitchen\n")
    assert _run(["id", "project:reno:kitchen"], ws, capsys)[:2] == (0, "sa:project:reno:kitchen\n")
    assert _run(["id", "project:garden"], ws, capsys)[:2] == (0, "sa:project:garden\n")
    # the Updater is given the id to write to
    ag.default_interests(ws, "project:reno-kitchen", now=NOW)
    told = ag.interested(ws, ["project:reno-kitchen"], when="filed", now=NOW)
    assert [(r["agent"], r["id"]) for r in told] == [("project:reno-kitchen", "sa:project:reno:kitchen")]
    # the command sets it, and an empty value gives the agent the id of its name again
    code, out, _err = _run(["set", "project:reno-bath", "--id-name", "project:reno:bath"], ws, capsys)
    assert code == 0 and out.strip().endswith(", id sa:project:reno:bath")
    assert _run(["id", "project:reno-bath"], ws, capsys)[:2] == (0, "sa:project:reno:bath\n")
    assert _run(["set", "project:reno-bath", "--id-name", ""], ws, capsys)[0] == 0
    assert _run(["id", "project:reno-bath"], ws, capsys)[:2] == (0, "sa:project:reno-bath\n")
    assert ag.set_row(ws, "project:garden", id_name="project:garden", now=NOW).get("id_name") is None


def test_an_id_name_that_cannot_be_one_is_refused(initialized_workspace: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    ag.set_row(ws, "project:reno-kitchen", now=NOW)
    ag.set_row(ws, "project:reno-bath", id_name="project:reno:bath", now=NOW)
    for value, why in (("reno:kitchen", "is not an id name"), ("project", "is not an id name"),
                       ("project:a:b:c:d", "is not an id name"), ("project:Reno:kitchen", "is not a part"),
                       ("project:reno:dev", "`dev` is reserved"), ("project:reno:bath", "is taken"),
                       ("project:reno-bath", "is taken")):
        with pytest.raises(ag.ar.AgentError, match=why):
            ag.set_row(ws, "project:reno-kitchen", id_name=value, now=NOW)
    with pytest.raises(ag.ar.AgentError, match="only a project agent"):
        ag.set_row(ws, "updater", id_name="updater:two", now=NOW)
    code, out, err = _run(["set", "project:reno-kitchen", "--id-name", "reno:kitchen"], ws, capsys)
    assert code == 2 and out == "" and "is not an id name" in err
    # A row edited by hand: the problem is named, and the id is the one the name gives.
    _registry(ws, [{"name": "project:reno-kitchen", "role": "project", "id_name": "elsewhere:x"},
                   {"name": "project:reno-bath", "role": "project", "id_name": "project:garden"},
                   {"name": "project:garden", "role": "project"}])
    data = ag.roster(ws)
    errors = "\n".join(data["errors"])
    assert "`project:reno-kitchen`: 'elsewhere:x' is not an id name" in errors
    assert "`project:garden` and `project:reno-bath` have the same id, sa:project:garden" in errors
    assert {a["name"]: a["id"] for a in data["agents"]}["project:reno-kitchen"] == "sa:project:reno-kitchen"


# ------------------------------------------------------------------- duties

def test_duties_of_a_current_workspace(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _make_current(ws)
    rows, errors = ag.duty_rows(ws, now=NOW)
    assert errors == []
    by_key = {(r["agent"], r["duty"]): r for r in rows}
    assert by_key[("superagent", "weekly-review")]["state"] == "ok"
    assert by_key[("superagent", "weekly-review")]["schedule_value"] == "Sunday 18:00"
    assert by_key[("superagent", "weekly-review")]["last_run"].startswith("2026-10-07")
    assert by_key[("updater", "update-light")]["state"] == "due", "every slot runs a light update"
    assert by_key[("updater", "update-light")]["reason"] == "every slot runs an update"
    assert by_key[("updater", "update-full")]["state"] == "ok"
    assert by_key[("updater", "update-full")]["schedule_value"] == "06:00"
    assert by_key[("updater", "update-full")]["last_run"].startswith("2026-10-08T08:00")
    assert by_key[("bookkeeper", "bills")]["state"] == "on-request"
    assert [r["duty"] for r in rows if r["state"] in ag.RUNNABLE] == ["update-light"]


def test_overdue_duties_match_cadence_status(initialized_workspace: Path) -> None:
    """The acceptance check of contracts/agents.md § 3: one set of findings, two views."""
    ws = initialized_workspace  # fresh template: nothing ever ran
    cd.mark(ws, "monthly-review", phase="start", now=NOW - dt.timedelta(hours=5))
    report = cd.status(ws, now=NOW)
    rows, _errors = ag.duty_rows(ws, now=NOW)
    late = {(r["agent"], r["skill"]) for r in rows if r["state"] in ("overdue", "unfinished")}
    expected = {(c["agent"], c["cadence"]) for c in report["overdue_cadences"]}
    expected |= {(u["agent"], u["cadence"]) for u in report["unfinished_runs"]}
    if report["last_check_stale"]:
        expected.add((report["last_check_agent"], "daily-update"))
    assert late == expected
    assert ("superagent", "daily-update") in late and ("superagent", "weekly-review") in late
    # An unfinished run is reported as unfinished only when the duty is not overdue anyway.
    _make_current(ws)
    cd.mark(ws, "weekly-review", phase="start", now=NOW - dt.timedelta(hours=5))
    rows, _errors = ag.duty_rows(ws, now=NOW)
    weekly = next(r for r in rows if r["duty"] == "weekly-review")
    assert weekly["state"] == "unfinished" and "never finished" in weekly["reason"]


def test_watcher_findings_belong_to_the_updater(initialized_workspace: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    ws = initialized_workspace
    _make_current(ws)
    late = [{"watcher": "bank", "cycle": "daily-update", "last_success": None, "days_since": 9},
            {"watcher": "permit", "cycle": "weekly-review", "last_success": None, "days_since": 30}]
    monkeypatch.setattr(cd, "overdue_watchers", lambda *a, **k: [dict(row) for row in late])
    report = cd.status(ws, now=NOW)
    assert [w["agent"] for w in report["overdue_watchers"]] == ["updater", "updater"]
    assert cd.render(report) == [
        "[updater] Watchers overdue for a check: bank (daily-update; last success never, 9 day(s) ago); "
        "permit (weekly-review; last success never, 30 day(s) ago)."]
    rows, _errors = ag.duty_rows(ws, now=NOW, agent="updater")
    assert [(r["duty"], r["state"]) for r in rows] == [("update-light", "overdue"), ("update-full", "ok")]
    assert rows[0]["reason"] == "2 watcher(s) overdue for a check: bank, permit"
    assert ag.check(ws, "updater", "update-light", now=NOW) == (
        True, "updater update-light: overdue: 2 watcher(s) overdue for a check: bank, permit")


def test_cadence_lines_are_grouped_by_agent(initialized_workspace: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    ws = initialized_workspace  # nothing ever ran
    monkeypatch.setattr(cd, "overdue_watchers", lambda *a, **k: [
        {"watcher": "bank", "cycle": "daily-update", "last_success": None, "days_since": 9}])
    cd.mark(ws, "whatsup", phase="start", now=NOW - dt.timedelta(hours=5))
    report = cd.status(ws, now=NOW)
    lines = cd.render(report)
    assert report["agents"][0] == "superagent"
    owners = [line.split("]")[0].lstrip("[") for line in lines]
    assert owners == ["superagent", "superagent", "superagent", "updater"], lines
    assert lines[0].startswith("[superagent] Context last refreshed never")
    assert lines[1].startswith("[superagent] Overdue cadences: ")
    assert lines[2].startswith("[superagent] Unfinished runs: whatsup started"), (
        "a skill no role claims as a duty goes to the lead")
    assert lines[3].startswith("[updater] Watchers overdue for a check: bank")


def test_render_without_agents_prints_plain_lines() -> None:
    report = {"now": NOW.isoformat(), "last_check": None, "last_check_stale": True,
              "overdue_cadences": [{"cadence": "doctor", "last_run": None, "reason": "r"}],
              "overdue_watchers": [], "unfinished_runs": []}
    assert cd.render(report) == [
        "Context last refreshed never: run `daily-update` (or `whatsup`).",
        "Overdue cadences: doctor (r; last never)."]


def test_duties_command(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    code, out, _err = _run(["duties", "--overdue"], ws, capsys)
    assert (code, out) == (0, ""), "nothing late prints nothing"
    code, out, _err = _run(["duties", "--agent", "bookkeeper"], ws, capsys)
    assert code == 0 and [line.split()[1] for line in out.splitlines()[1:]] == [
        "bills", "subscriptions", "expenses"]
    _set_runs(ws, last_check=_ago(hours=1), weekly_review=_ago(30))
    code, out, _err = _run(["duties", "--overdue"], ws, capsys)
    assert code == 0 and len(out.splitlines()) == 2
    assert out.splitlines()[1].startswith("superagent  weekly-review")
    assert "overdue: no run in the last 7 days; last 2026-09-08" in out
    code, out, _err = _run(["duties", "--overdue", "--json"], ws, capsys)
    assert [r["duty"] for r in json.loads(out)] == ["weekly-review"]
    code, _out, err = _run(["duties", "--agent", "nobody"], ws, capsys)
    assert code == 2 and "no role named 'nobody'" in err


# -------------------------------------------------------------------- check

def test_check_exit_codes(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    assert _run(["check", "superagent", "weekly-review"], ws, capsys) == (1, "", ""), (
        "not due: exit 1 and silence, so a scheduler skips the run at no cost")
    assert _run(["check", "bookkeeper", "bills"], ws, capsys)[0] == 1, "an on-request duty is never due"
    _set_runs(ws, last_check=_ago(days=3), weekly_review=_ago(30))
    code, out, _err = _run(["check", "superagent", "weekly-review"], ws, capsys)
    assert code == 0 and out.startswith("superagent weekly-review: overdue: no run in the last 7 days")
    code, out, _err = _run(["check", "superagent", "daily-update"], ws, capsys)
    assert code == 0 and "context last refreshed 3 day(s) ago" in out
    code, out, err = _run(["check", "superagent", "nope"], ws, capsys)
    assert code == 2 and out == "" and "has no duty 'nope'" in err
    code, _out, err = _run(["check", "nobody", "x"], ws, capsys)
    assert code == 2 and "no role named 'nobody'" in err
    code, _out, err = _run(["check", "project:tax-2026", "x"], ws, capsys)
    assert code == 2 and "'project' has no duty 'x' (duties: none)" in err


def test_check_that_cannot_run_is_not_mistaken_for_not_due(initialized_workspace: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    (ws / "_memory" / "context.yaml").write_text("last_runs: [unclosed\n")
    code, out, err = _run(["check", "superagent", "weekly-review"], ws, capsys)
    assert code == 3 and out == "" and "context.yaml" in err
    assert _run(["duties"], ws, capsys)[0] == 1


def test_agents_tool_reads_only(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace

    def snapshot() -> dict[str, bytes]:
        return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}

    before = snapshot()
    for argv in (["list"], ["id", "updater"], ["duties"], ["duties", "--overdue"],
                 ["duties", "--due"], ["check", "updater", "update-light"],
                 ["check", "updater", "update-full"]):
        _run(argv, ws, capsys)
    assert snapshot() == before


# ------------------------------------------------------- due is not overdue

def test_a_duty_is_due_at_its_scheduled_time_before_it_is_late(initialized_workspace: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    # Yesterday's briefing ended at 08:40, and the context was touched an hour ago.
    _set_runs(ws, last_check=_ago(hours=1), daily_update=_ago(hours=24.33))
    rows, _errors = ag.duty_rows(ws, now=NOW)
    daily = next(r for r in rows if r["duty"] == "daily-update")
    assert daily["state"] == "due", "08:30 has come and it has not run since; nothing is late yet"
    assert daily["reason"].startswith("due since 2026-10-08 08:30 (daily, 08:30)")
    code, out, _err = _run(["check", "superagent", "daily-update"], ws, capsys)
    assert code == 0 and out.startswith("superagent daily-update: due: due since 2026-10-08 08:30")
    assert _run(["duties", "--overdue"], ws, capsys)[1] == "", "the on-open nag stays quiet"
    code, out, _err = _run(["duties", "--due"], ws, capsys)
    assert [line.split()[1] for line in out.splitlines()[1:]] == ["daily-update", "update-light"]
    assert cd.render(cd.status(ws, now=NOW)) == []


def test_a_review_missed_on_sunday_is_due_on_monday(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    monday = dt.datetime(2026, 10, 5, 9, 0, tzinfo=PDT)
    stamp = dt.datetime(2026, 9, 28, 10, 0, tzinfo=PDT).isoformat()  # last ran a Monday, late
    recent = (monday - dt.timedelta(minutes=10)).isoformat()
    _set_runs(ws, last_check=recent, daily_update=recent, weekly_review=stamp, monthly_review=recent,
              tailor_review=recent, doctor=recent, update_full=recent)
    assert ag.check(ws, "superagent", "weekly-review", now=monday)[0] is True
    weekly = next(r for r in ag.duty_rows(ws, now=monday)[0] if r["duty"] == "weekly-review")
    assert weekly["state"] == "due" and "due since 2026-10-04 18:00" in weekly["reason"]
    saturday = dt.datetime(2026, 10, 3, 9, 0, tzinfo=PDT)
    assert ag.check(ws, "superagent", "weekly-review", now=saturday)[0] is False


def test_the_update_duties(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws)
    assert _run(["check", "updater", "update-light"], ws, capsys) == (
        0, "updater update-light: due: every slot runs an update\n", ""), (
        "nothing due and nothing waiting: the slot runs all the same")
    assert _run(["check", "updater", "update-full"], ws, capsys)[0] == 1
    # A watcher that was never checked is due, and so is a document left in the drop folder.
    target = ws / "watched.txt"
    target.write_text("v1")
    registry = ws / "Sources" / "Watchlist"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "Doc.ref.md").write_text(
        "---\nref_version: 2\ntitle: Doc\nwatch:\n  type: path\n  path: " + str(target)
        + "\n  schedule: daily\n---\n# Notes\n")
    code, out, _err = _run(["check", "updater", "update-light"], ws, capsys)
    assert (code, out.strip()) == (0, "updater update-light: due: 1 watcher(s) due: doc")
    scan = ws / "Inbox" / "scan.pdf"
    scan.parent.mkdir(exist_ok=True)
    scan.write_bytes(b"%PDF")
    old = (NOW - dt.timedelta(minutes=5)).timestamp()
    os.utime(scan, (old, old))
    code, out, _err = _run(["check", "updater", "update-light"], ws, capsys)
    assert out.strip().endswith("1 watcher(s) due: doc; 1 document(s) waiting in a drop folder")
    # The full tier goes by its stamp: due once its last run is older than today's 06:00.
    _set_runs(ws, last_check=_ago(hours=0.25), update_full=_ago(hours=4))
    code, out, _err = _run(["check", "updater", "update-full"], ws, capsys)
    assert code == 0 and "update-full: due: due since 2026-10-08 06:00 (daily, 06:00)" in out
    cd.mark(ws, "update-full", phase="finish", now=NOW)
    assert ag.check(ws, "updater", "update-full", now=NOW + dt.timedelta(minutes=1))[0] is False
    cd.mark(ws, "update-light", phase="start", now=NOW - dt.timedelta(minutes=5))
    target.unlink()
    (registry / "Doc.ref.md").unlink()
    scan.unlink()
    light = next(r for r in ag.duty_rows(ws, now=NOW)[0] if r["duty"] == "update-light")
    assert light["state"] == "unfinished" and "never finished" in light["reason"]


# ---------------------------------------------------------------- interests

def _agents_running(ws: Path) -> None:
    """A registry with a Bookkeeper and two project agents held by sessions, and one that is off."""
    _registry(ws, [
        {"name": "bookkeeper", "role": "bookkeeper", "handle": "agent:bookkeeper", "project": None,
         "status": "active", "wakes": [
             {"name": "after-harvest", "trigger": "fire-only", "duty": None, "purpose": "reconcile",
              "status": "active", "approval": "yes", "granted_at": _ago(1)},
             {"name": "monthly", "trigger": 'cron "0 9 1 * *"', "duty": None, "purpose": "close",
              "status": "active", "approval": "yes", "granted_at": _ago(1)}]},
        {"name": "project:kitchen-reno", "role": "project", "handle": "agent:project:kitchen-reno",
         "project": "project:kitchen-reno", "status": "dormant", "wakes": []},
        {"name": "project:garden", "role": "project", "handle": "agent:project:garden",
         "project": "project:garden", "status": "off", "wakes": []},
    ])


def _told(ws: Path, *handles: str, when: str | None = None) -> dict[str, str]:
    return {r["agent"]: (r["deliver"] if r["deliver"] != "fire" else f"fire {r['wake']}")
            for r in ag.interested(ws, list(handles), when=when, now=NOW)}


def test_an_agent_records_and_drops_its_own_interests(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    entry, changed = ag.add_interest(ws, "project:kitchen-reno", pattern="contact:tile-supplier",
                                     why="a vendor that quotes the tiles", now=NOW)
    assert changed and entry == {"pattern": "contact:tile-supplier", "when": None,
                                 "why": "a vendor that quotes the tiles", "fire": None,
                                 "added_at": NOW.isoformat(timespec="seconds")}
    assert ag.add_interest(ws, "project:kitchen-reno", pattern="contact:tile-supplier", now=NOW)[1] is False
    assert ag.add_interest(ws, "project:kitchen-reno", pattern="contact:tile-supplier", why="quotes and delivery",
                           now=NOW)[1] is True, "the same interest is updated in place"
    ag.add_interest(ws, "project:kitchen-reno", pattern="watch:tile-price", when="changed", why="price moves",
                    now=NOW)
    assert [ag.interest_label(i) for i in ag.interests(ws, "project:kitchen-reno")] == [
        "contact:tile-supplier", "watch:tile-price (changed)"]
    assert ag.remove_interest(ws, "project:kitchen-reno", "contact:tile-supplier", now=NOW) == 1
    assert [i["pattern"] for i in ag.interests(ws, "project:kitchen-reno")] == ["watch:tile-price"]
    with pytest.raises(ag.ar.AgentError, match="has no interest"):
        ag.remove_interest(ws, "project:kitchen-reno", "contact:tile-supplier", now=NOW)
    row = next(r for r in ag.roster(ws)["agents"] if r["name"] == "project:kitchen-reno")
    assert [i["pattern"] for i in row["interests"]] == ["watch:tile-price"]


def test_bad_interests_are_refused(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    refused = [
        (dict(agent="project:kitchen-reno", pattern="kitchen", why="w"), "not a handle or a handle glob"),
        (dict(agent="project:kitchen-reno", pattern="tag:tile-order"), "needs its why"),
        (dict(agent="project:kitchen-reno", pattern="tag:tile-order", why="w", when="later"), "`when` 'later'"),
        (dict(agent="project:kitchen-reno", pattern="tag:tile-order", why="w", fire="after-harvest"), "has no grant"),
        (dict(agent="bookkeeper", pattern="bill:*", why="w", fire="monthly"), "runs on a schedule"),
        (dict(agent="updater", pattern="tag:tile-order", why="w"), "keeps no interests of its own"),
        (dict(agent="bookkeeper:dev", pattern="tag:tile-order", why="w"), "a dev twin keeps no interests"),
        (dict(agent="project:nowhere", pattern="tag:tile-order", why="w"), "has no registry row"),
        (dict(agent="nobody", pattern="tag:tile-order", why="w"), "no role named"),
    ]
    for kwargs, match in refused:
        agent = kwargs.pop("agent")
        with pytest.raises(ag.ar.AgentError, match=match):
            ag.add_interest(ws, agent, now=NOW, **kwargs)
    for n in range(ag.ar.MAX_INTERESTS):
        ag.add_interest(ws, "project:kitchen-reno", pattern=f"tag:t{n}", why="w", now=NOW)
    with pytest.raises(ag.ar.AgentError, match="the most an agent keeps"):
        ag.add_interest(ws, "project:kitchen-reno", pattern="tag:one-more", why="w", now=NOW)


def test_the_roles_own_interests_are_written_once(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    added, notes = ag.default_interests(ws, "project:kitchen-reno", now=NOW)
    assert [i["pattern"] for i in added] == ["project:kitchen-reno"] and notes == [], (
        "`project:<slug>` stands for the agent's own project")
    assert ag.default_interests(ws, "project:kitchen-reno", now=NOW) == ([], [])
    added, notes = ag.default_interests(ws, "bookkeeper", now=NOW)
    assert notes == []
    assert [ag.interest_label(i) for i in added] == [
        "domain:finances (harvest) -> wake `after-harvest`", "bill:* (captured)",
        "subscription:* (captured)", "domain:finances (filed)"]
    ag.add_interest(ws, "bookkeeper", pattern="bill:*", when="captured", why="my own words", now=NOW)
    assert ag.default_interests(ws, "bookkeeper", now=NOW) == ([], []), "an agent's own wording stays"
    assert next(i for i in ag.interests(ws, "bookkeeper") if i["pattern"] == "bill:*")["why"] == "my own words"


def test_a_default_that_fires_a_wake_waits_for_its_grant(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _registry(ws, [{"name": "bookkeeper", "role": "bookkeeper", "handle": "agent:bookkeeper",
                    "project": None, "status": "active", "wakes": []}])
    added, notes = ag.default_interests(ws, "bookkeeper", now=NOW)
    assert len(added) == 3 and len(notes) == 1 and "has no grant for the wake `after-harvest`" in notes[0]


def test_the_updater_learns_who_to_tell(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    for agent in ("bookkeeper", "project:kitchen-reno", "project:garden"):
        ag.default_interests(ws, agent, now=NOW)
    assert _told(ws, "watch:simplefin", "domain:finances", when="harvest") == {
        "bookkeeper": "fire after-harvest"}
    assert _told(ws, "bill:phone-plan", "domain:home", when="captured") == {"bookkeeper": "message"}
    assert _told(ws, "document:doc-1", "domain:finances", "project:kitchen-reno", when="filed") == {
        "bookkeeper": "message", "project:kitchen-reno": "message"}
    assert _told(ws, "watch:permit-page", "project:kitchen-reno", when="changed") == {"project:kitchen-reno": "message"}
    assert _told(ws, "watch:permit-page", "project:garden", when="changed") == {}, (
        "no session holds the garden agent, so the run report is its hand-off")
    assert _told(ws, "watch:simplefin", "domain:finances", when="changed") == {}, (
        "an interest narrowed to a harvest is not told about a mere change")
    assert _told(ws, "bill:phone-plan") == {}, "narrowed interests need the kind of update"
    assert _told(ws, "task:t-1", "project:kitchen-reno") == {"project:kitchen-reno": "message"}
    row = ag.interested(ws, ["project:kitchen-reno"], when="filed", now=NOW)[0]
    assert row["id"] == "sa:project:kitchen-reno" and row["patterns"] == ["project:kitchen-reno"]


def test_a_wake_that_may_not_run_is_skipped_not_replaced_by_a_message(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    ag.default_interests(ws, "bookkeeper", now=NOW)
    ag.change_grants(ws, "bookkeeper", wake="after-harvest", action="suspend", now=NOW)
    (row,) = ag.interested(ws, ["domain:finances"], when="harvest", now=NOW)
    assert row["deliver"] == "skip" and "suspended" in row["reason"]
    ag.add_interest(ws, "bookkeeper", pattern="watch:simplefin", why="the feed itself", now=NOW)
    (row,) = ag.interested(ws, ["watch:simplefin", "domain:finances"], when="harvest", now=NOW)
    assert row["deliver"] == "message", "a plain interest that also matches is still delivered"


def test_interested_wants_handles(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    with pytest.raises(ag.ar.AgentError, match="is not a handle"):
        ag.interested(ws, ["kitchen tiles"], now=NOW)
    with pytest.raises(ag.ar.AgentError, match="at least one handle"):
        ag.interested(ws, [], now=NOW)
    with pytest.raises(ag.ar.AgentError, match="`when` 'later'"):
        ag.interested(ws, ["tag:tile-order"], when="later", now=NOW)


def test_interest_commands(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    assert _run(["interest", "bookkeeper", "--defaults"], ws, capsys)[0] == 0
    code, out, _err = _run(["interest", "project:kitchen-reno", "--add", "project:<slug>", "--why", "mine"], ws, capsys)
    assert code == 0 and "project:kitchen-reno: interest project:kitchen-reno recorded" in out
    code, out, _err = _run(["interest", "project:kitchen-reno", "--add", "project:kitchen-reno"], ws, capsys)
    assert code == 0 and "is there already" in out
    code, out, _err = _run(["interest", "bookkeeper"], ws, capsys)
    assert code == 0 and "domain:finances (harvest) -> wake `after-harvest`  reconcile" in out
    code, out, _err = _run(["interest", "bookkeeper", "--json"], ws, capsys)
    assert code == 0 and json.loads(out)[0]["fire"] == "after-harvest"
    code, out, _err = _run(["interested", "--when", "harvest", "--handle", "watch:simplefin",
                            "--handle", "domain:finances"], ws, capsys)
    assert code == 0 and out.strip() == "sa:bookkeeper  fire after-harvest  domain:finances"
    code, out, _err = _run(["interested", "--when", "filed", "--handle", "project:kitchen-reno", "--json"], ws, capsys)
    assert code == 0 and json.loads(out)[0]["deliver"] == "message"
    code, out, _err = _run(["interested", "--handle", "tag:nobody-cares"], ws, capsys)
    assert code == 0 and out == ""
    assert _run(["interest", "project:kitchen-reno", "--remove", "project:kitchen-reno"], ws, capsys)[0] == 0
    assert _run(["interest", "project:kitchen-reno", "--remove", "project:kitchen-reno"], ws, capsys)[0] == 2
    assert _run(["interest", "updater", "--add", "tag:x", "--why", "w"], ws, capsys)[0] == 2
    assert _run(["interested", "--handle", "not a handle"], ws, capsys)[0] == 2


def test_the_briefing_lines_carry_each_agents_interests(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _agents_running(ws)
    assert not any(line.startswith("Interests") for line in ag.heartbeat_lines(ws, now=NOW))
    ag.default_interests(ws, "project:kitchen-reno", now=NOW)
    ag.add_interest(ws, "project:garden", pattern="tag:tile", why="w", now=NOW)
    (line,) = [ln for ln in ag.heartbeat_lines(ws, now=NOW) if ln.startswith("Interests")]
    assert line == "Interests (what the update cycle tells each agent): project:kitchen-reno: project:kitchen-reno."
