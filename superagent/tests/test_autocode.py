# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/autocode.py`: starting, parking and stopping agents that run as sessions.

No test reaches a real `autocode` or `opencode` command: `autocode._run` and
`autocode._binary` are replaced. The framework must work with Autocode off,
so the last tests pin that no other tool depends on this module.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import agents as ag
from superagent.tools import autocode as ac
from superagent.tools import cadence


class Fake:
    """Stands in for the two commands: records each call and answers from a script."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answers: dict[str, tuple[int, str, str]] = {}

    def run(self, cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
            timeout: int = 0) -> subprocess.CompletedProcess[str]:
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        key = f"{cmd[0]} {cmd[1]}"
        code, out, err = self.answers.get(key, (0, "", ""))
        return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    stand_in = Fake()
    monkeypatch.setattr(ac, "_run", stand_in.run)
    monkeypatch.setattr(ac, "_binary", lambda name: name)
    return stand_in


def _enable(ws: Path, *, model: bool = True) -> None:
    config = ws / "_memory" / "config.yaml"
    data = yaml.safe_load(config.read_text())
    block = data["preferences"]["autocode"]
    block["enabled"] = True
    if model:
        block["models"] = {"runner": {"model": "prov/big", "variant": "high"},
                           "dev": {"model": "prov/big", "variant": "xhigh"}}
    config.write_text(yaml.safe_dump(data, sort_keys=False))


def _grant(ws: Path) -> None:
    ag.grant(ws, "updater", wake="update-light", trigger="every 30m", duty="update-light",
             purpose="keep the stores current", approval="yes")


def _row(ws: Path, name: str) -> dict[str, Any]:
    rows = yaml.safe_load((ws / "_memory" / "agents.yaml").read_text())["agents"]
    return next(r for r in rows if r.get("name") == name)


# ------------------------------------------------------------- start prompt

def test_start_prompt_of_an_agent_with_a_granted_wake(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _grant(ws)
    text = ac.start_prompt(ws, ac.FRAMEWORK, "updater")
    assert "under exactly this id: `sa:updater`" in text and "`id_taken`" in text
    assert "Never register under another id" in text
    assert "Read `superagent/rules/autocode.md`" in text
    assert "Read your role file `superagent/agents/updater.agent.md`." in text
    assert ("name `update-light`; trigger `every 30m`; check `sh superagent/tools/wake_check.sh "
            "updater update-light --wake update-light`") in text
    assert "and no other periodic wake" in text and "Run the skill `update`" in text
    assert "Send one message to `sa:superagent`: `Started: updater`." in text
    ag.change_grants(ws, "updater", action="suspend")
    assert "Set no wake: none is granted to you" in ac.start_prompt(ws, ac.FRAMEWORK, "updater"), (
        "a suspended grant is not set")


def test_start_prompts_of_the_lead_a_project_agent_and_a_dev_twin(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    lead = ac.start_prompt(ws, ac.FRAMEWORK, "superagent")
    assert "`sa:superagent`" in lead and "Tell the owner in one report message" in lead
    with pytest.raises(ar.AgentError, match="has no registry row: create the agent first"):
        ac.start_prompt(ws, ac.FRAMEWORK, "project:kitchen")
    ag.set_row(ws, "project:kitchen")
    ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d until 2027-03-01",
             purpose="look at the permit portal", approval="daily until March 1")
    project = ac.start_prompt(ws, ac.FRAMEWORK, "project:kitchen")
    assert "`sa:project:kitchen`" in project and "Your project is `project:kitchen`" in project
    assert "check `sh superagent/tools/wake_check.sh project:kitchen --wake permit`" in project
    assert "Wake `permit`: look at the permit portal. Do that, then park." in project
    for name, dev in (("updater", True), ("updater:dev", False)):
        twin = ac.start_prompt(ws, ac.FRAMEWORK, name, dev=dev)
        assert "`sa:updater:dev`" in twin and "updater.agent.dev.md" in twin
        assert "rules/framework-changes.md" in twin and "Set no wake. A dev twin wakes on a message." in twin
    with pytest.raises(ar.AgentError, match="no role named 'ghost'"):
        ac.start_prompt(ws, ac.FRAMEWORK, "ghost")


# -------------------------------------------------------------------- start

def test_start_is_refused_while_autocode_mode_is_off(initialized_workspace: Path, fake: Fake,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    assert ac.main(["start", "updater", "--workspace", str(ws)]) == 2
    assert "Autocode mode is off" in capsys.readouterr().err
    assert fake.calls == [], "nothing was started"
    assert ac.main(["start", "updater", "--print", "--workspace", str(ws)]) == 0, "a dry run only shows"
    assert "opencode run --title @sa:updater --auto" in capsys.readouterr().out and fake.calls == []


def test_start_runs_opencode_with_the_tier_model_and_marks_the_row(
        initialized_workspace: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = initialized_workspace
    _enable(ws)
    _grant(ws)
    monkeypatch.setenv("AUTOCODE_TOKEN", "do-not-leak")
    monkeypatch.setenv("AUTOCODE_SESSION", "ses_parent")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_parent")
    done = ac.start(ws, ac.FRAMEWORK, "updater")
    assert done == {"id": "sa:updater", "name": "updater", "status": "active", "host": "testbox"}
    call = fake.calls[0]
    assert call["cmd"][:7] == ["opencode", "run", "--title", "@sa:updater", "--auto", "--model", "prov/big#high"]
    assert call["cmd"][-1] == ac.start_prompt(ws, ac.FRAMEWORK, "updater")
    assert call["cwd"] == ac.FRAMEWORK.parent, "the session opens in the checkout"
    assert not [k for k in call["env"] if k.startswith(("AUTOCODE_", "OPENCODE"))], (
        "the starting session's identity never leaks into the one it starts")
    row = _row(ws, "updater")
    assert (row["status"], row["host"]) == ("active", "testbox") and len(row["wakes"]) == 1
    assert ag.may_fetch(ws) == (True, ""), "this machine is now the one that fetches"
    # A project agent with no grant is started idle, a dev twin leaves no row.
    ag.set_row(ws, "project:kitchen")
    assert ac.start(ws, ac.FRAMEWORK, "project:kitchen")["status"] == "dormant"
    twin = ac.start(ws, ac.FRAMEWORK, "updater", dev=True, auto=False)
    assert twin["status"] is None and "prov/big#xhigh" in fake.calls[-1]["cmd"]
    assert "--auto" not in fake.calls[-1]["cmd"]


def test_a_failed_start_changes_nothing(initialized_workspace: Path, fake: Fake,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _enable(ws, model=False)
    fake.answers["opencode run"] = (1, "", "provider not found")
    before = (ws / "_memory" / "agents.yaml").read_text()
    assert ac.main(["start", "updater", "--workspace", str(ws)]) == 3
    assert "ended with exit 1: provider not found" in capsys.readouterr().err
    assert (ws / "_memory" / "agents.yaml").read_text() == before
    assert "--model" not in fake.calls[0]["cmd"], "no model configured: the harness's own default"


# ------------------------------------------------------ park, stop, retire

def test_park_stop_and_retire_send_a_message_then_mark_the_row(initialized_workspace: Path,
                                                             fake: Fake) -> None:
    ws = initialized_workspace
    ag.set_row(ws, "project:kitchen", status="active", host="testbox")
    ag.grant(ws, "project:kitchen", wake="permit", trigger="every 1d", purpose="p", approval="yes")
    assert ac.tell(ws, ac.FRAMEWORK, "project:kitchen", "park") == {"id": "sa:project:kitchen",
                                                                    "status": "dormant"}
    cmd = fake.calls[-1]["cmd"]
    assert cmd[:3] == ["autocode", "send", "sa:project:kitchen"] and cmd[3].startswith("Park: write your state")
    assert cmd[-2:] == ["--subject", "Park"]
    assert ac.tell(ws, ac.FRAMEWORK, "project:kitchen", "retire")["status"] == "retired"
    row = _row(ws, "project:kitchen")
    assert row["status"] == "retired" and row["wakes"][0]["status"] == "suspended"
    assert ac.tell(ws, ac.FRAMEWORK, "project:kitchen", "stop")["status"] == "off"


def test_a_message_that_was_not_sent_leaves_the_row(initialized_workspace: Path, fake: Fake,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    ag.set_row(ws, "updater", status="active", host="testbox")
    fake.answers["autocode send"] = (1, "", "refused (unknown_agent): no agent sa:updater")
    assert ac.main(["stop", "updater", "--workspace", str(ws)]) == 1
    err = capsys.readouterr().err
    assert "was not sent" in err and "`autocode agents rm sa:updater`" in err
    assert _row(ws, "updater")["status"] == "active", "a row must not say `off` while the session runs"


# ------------------------------------------------------------ wakes, status

def test_wakes_are_audited_against_the_grants(initialized_workspace: Path, fake: Fake,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _grant(ws)
    ag.grant(ws, "updater", wake="update-full", trigger="every 1d", duty="update-full",
             purpose="the full tier", approval="yes")
    fake.answers["autocode wakes"] = (0, json.dumps([
        {"agent": "sa:updater", "name": "update-light", "trigger": "every 15m"},
        {"agent": "sa:updater", "name": "extra", "trigger": "cron \"0 * * * *\""},
        {"agent": "sa:updater", "name": "once", "trigger": "in 45m"},
        {"agent": "sa:superagent", "name": "idle-tidy", "trigger": "idle 30m"},
        {"agent": "other:agent", "name": "x", "trigger": "every 1m"},
    ]), "")
    findings = {(f["agent"], f["wake"]): f["finding"] for f in ac.audit_wakes(ws, ac.FRAMEWORK)}
    assert findings == {("sa:updater", "update-light"): "differs", ("sa:updater", "extra"): "unknown",
                        ("sa:updater", "update-full"): "missing", ("sa:superagent", "idle-tidy"): "unknown"}, (
        "a one-time wake needs no grant, and another prefix is not ours")
    assert ac.main(["wakes", "updater", "--workspace", str(ws)]) == 1
    assert fake.calls[-1]["cmd"] == ["autocode", "wakes", "sa:updater", "--json"]
    assert "sa:updater  extra  unknown: set as" in capsys.readouterr().out
    fake.answers["autocode wakes"] = (2, "", "refused: this command needs the owner")
    assert ac.main(["wakes", "--workspace", str(ws)]) == 3
    assert "needs the owner's shell" in capsys.readouterr().err


def test_status_sets_the_roster_against_what_is_registered(initialized_workspace: Path, fake: Fake) -> None:
    ws = initialized_workspace
    ag.set_row(ws, "updater", status="active", host="testbox")
    fake.answers["autocode agents"] = (0, json.dumps([
        {"id": "sa:superagent", "status": "idle", "machine": "testbox"},
        {"id": "sa:superagent:dev", "status": "working", "machine": "testbox"},
        {"id": "sa:project:ghost", "status": "idle", "machine": "testbox"},
    ]), "")
    rows = {r["id"]: r for r in ac.status(ws, ac.FRAMEWORK)}
    assert fake.calls[-1]["cmd"] == ["autocode", "agents", "sa:*", "--json"]
    assert rows["sa:updater"]["note"] == "the row says it runs, and it is not registered"
    assert rows["sa:superagent"]["note"] == "registered, and the row says `off`"
    assert rows["sa:superagent:dev"]["note"] == "" and rows["sa:bookkeeper"]["note"] == ""
    assert rows["sa:project:ghost"]["note"] == "registered, with no registry row"


def test_me_settings_and_lock_names(initialized_workspace: Path, fake: Fake,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    fake.answers["autocode me"] = (0, json.dumps({"registered": True, "agent": {"id": "sa:updater"}}), "")
    assert ac.me() == "sa:updater" and ac.main(["me", "--workspace", str(ws)]) == 0
    fake.answers["autocode me"] = (0, json.dumps({"registered": False}), "")
    assert ac.me() is None and ac.main(["me", "--workspace", str(ws)]) == 1
    capsys.readouterr()
    lines = ac.settings_lines(ws, ac.FRAMEWORK)
    assert "autocode settings set question_mode agent:sa:superagent --match 'sa:updater'" in lines
    assert "autocode settings set question_mode agent:sa:superagent --match 'sa:project:*'" in lines
    assert "autocode settings set expire_after 0 --match 'sa:*:dev'" in lines
    assert not [ln for ln in lines if "question_mode" in ln and "--match 'sa:superagent'" in ln]
    assert [ln for ln in lines if "wake_mode ask" in ln][0].startswith("# "), "optional, so commented"
    assert not [c for c in fake.calls if c["cmd"][:2] == ["autocode", "settings"]], "it only prints"
    assert ac.lock_name(ws, "browser", "schwab") == "sa:browser:schwab"
    with pytest.raises(ar.AgentError):
        ac.lock_name(ws, "browser", "Not A Profile")


# ----------------------------------------- the framework works without it

def test_no_other_tool_imports_the_autocode_module(framework_dir: Path) -> None:
    offenders: list[str] = []
    for path in sorted((framework_dir / "tools").rglob("*.py")):
        if path.name == "autocode.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{alias.name}" for alias in node.names] + [node.module or ""]
            if any(name.endswith("tools.autocode") or name == "autocode" for name in names):
                offenders.append(path.name)
    assert not offenders, f"only the `agents` skill and the owner call tools/autocode.py: {offenders}"


def test_a_missing_command_is_reported_not_raised(initialized_workspace: Path,
                                                  monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    def missing(name: str) -> str:
        raise ac.Unavailable(f"the `{name}` command is not installed on this machine")

    monkeypatch.setattr(ac, "_binary", missing)
    assert ac.main(["status", "--workspace", str(initialized_workspace)]) == 3
    assert "is not installed on this machine" in capsys.readouterr().err
    assert ag.main(["list", "--workspace", str(initialized_workspace)]) == 0, "the roster needs no Autocode"


# ---------------------------------------------- setup: what the start skill asks

def _config(ws: Path) -> Path:
    return ws / "_memory" / "config.yaml"


def test_set_config_edits_one_line_and_keeps_its_comment(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    before = _config(ws).read_text().splitlines()
    assert ac.set_config(ws, "enabled", True) is True
    after = _config(ws).read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert len(changed) == 1 and changed[0][0].strip() == "enabled: false"
    assert changed[0][1].strip() == "enabled: true" and ar.autocode_enabled(ws) is True
    assert ac.set_config(ws, "enabled", True) is False, "nothing to change, nothing written"
    assert ac.set_config(ws, "work_hours", "06:00-23:00") is True
    line = next(ln for ln in _config(ws).read_text().splitlines() if "work_hours:" in ln)
    assert line.startswith('    work_hours: "06:00-23:00"') and line.rstrip().endswith("null = any hour"), (
        "the value changes and the comment stays")
    assert ac.set_config(ws, "daily_usd", 5.0) is True and ac.set_config(ws, "runner_model", "prov/big") is True
    prefs = yaml.safe_load(_config(ws).read_text())["preferences"]
    assert prefs["update"]["work_hours"] == "06:00-23:00" and prefs["autocode"]["daily_usd"] == 5.0
    assert prefs["autocode"]["models"]["runner"] == {"model": "prov/big", "variant": None}
    assert prefs["autocode"]["models"]["dev"] == {"model": None, "variant": None}, "the other tier is untouched"
    assert ac.set_config(ws, "work_hours", None) is True
    assert yaml.safe_load(_config(ws).read_text())["preferences"]["update"]["work_hours"] is None


def test_set_config_adds_a_missing_key_and_refuses_what_it_cannot_edit(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    _config(ws).write_text("preferences:\n  autocode:\n    id_prefix: \"fam\"   # ours\n\n"
                           "  # The update cycle.\n  update:\n    drop_folders: []\n\nother: 1\n")
    assert ac.set_config(ws, "enabled", True) is True
    assert _config(ws).read_text() == (
        "preferences:\n  autocode:\n    id_prefix: \"fam\"   # ours\n    enabled: true\n\n"
        "  # The update cycle.\n  update:\n    drop_folders: []\n\nother: 1\n")
    with pytest.raises(ac.ConfigError, match="no `preferences.autocode.models.runner` block"):
        ac.set_config(ws, "runner_model", "prov/big")
    _config(ws).write_text("preferences:\n  update:\n    work_hours:\n      - odd\n")
    with pytest.raises(ac.ConfigError, match="over several lines"):
        ac.set_config(ws, "work_hours", "07:00-22:00")
    assert _config(ws).read_text() == "preferences:\n  update:\n    work_hours:\n      - odd\n"
    _config(ws).write_text("preferences: [unclosed\n")
    with pytest.raises(ac.ConfigError, match="cannot be read"):
        ac.set_config(ws, "enabled", True)


@pytest.mark.parametrize("value, trigger", [
    ("08:30", 'cron "30 8 * * *"'), ("Sunday 18:00", 'cron "0 18 * * 0"'),
    ("Monday 07:05", 'cron "5 7 * * 1"'), ("1st 09:00", 'cron "0 9 1 * *"'),
    ("every 30 minutes", "every 30m"), ("every 2 hours", "every 2h"), ("every 90 days", "every 90d"),
    ("every 90 minutes", "every 90m"), ("every 1 week", "every 7d"),
])
def test_a_cadence_value_becomes_a_trigger(value: str, trigger: str) -> None:
    assert ac.trigger_for(cadence.parse_schedule(value)) == trigger


def test_setup_state_proposes_wakes_from_the_duties(initialized_workspace: Path, fake: Fake) -> None:
    ws = initialized_workspace
    ag.set_row(ws, "project:kitchen")
    state = ac.setup_state(ws, ac.FRAMEWORK)
    assert (state["enabled"], state["configured"], state["lead"], state["this_session"]) == (
        False, False, "superagent", None)
    assert state["settings"]["work_hours"] is None and state["proposed"]["work_hours"] == "06:00-23:00"
    by_name = {a["name"]: a for a in state["agents"]}
    assert [(w["name"], w["trigger"], w["duty"]) for w in by_name["superagent"]["proposed"]] == [
        ("daily-update", 'cron "30 8 * * *"', "daily-update"),
        ("weekly-review", 'cron "0 18 * * 0"', "weekly-review"),
        ("monthly-review", 'cron "0 9 1 * *"', "monthly-review")], (
        "a duty that needs the owner's approval gets no wake")
    assert [(w["name"], w["trigger"], w["held"]) for w in by_name["updater"]["proposed"]] == [
        ("update-light", "every 30m", True), ("update-full", 'cron "0 6 * * *"', True)]
    assert [(w["name"], w["trigger"], w["duty"]) for w in by_name["bookkeeper"]["proposed"]] == [
        ("after-harvest", "fire-only", None)]
    assert by_name["project:kitchen"]["proposed"] == [] and by_name["project:kitchen"]["row"] is True
    assert by_name["updater"]["recommended"] and by_name["superagent"]["lead"]
    assert not by_name["bookkeeper"]["recommended"] and not by_name["updater"]["row"]
    text = "\n".join(ac.render_setup(state))
    assert "Mode: OFF. Not set up yet." in text and "This session: not registered." in text
    assert "work hours of the scheduled update: any hour (proposed: 06:00-23:00)" in text
    assert 'wake update-full: cron "0 6 * * *"  [proposed]' in text
    assert "project:kitchen (sa:project:kitchen): row off\n    no wake: it wakes on a message" in text
    assert not [c for c in fake.calls if c["cmd"][1] != "me"], "the state is a read"


def test_apply_is_refused_while_the_mode_is_off_and_without_the_owners_words(
        initialized_workspace: Path, fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    before = {p: p.read_bytes() for p in (ws / "_memory").glob("*.yaml")}
    assert ac.main(["setup", "apply", "--approval", "yes", "--workspace", str(ws)]) == 2
    assert "Autocode mode is off" in capsys.readouterr().err
    assert ac.main(["setup", "enable", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip() == "Autocode mode is on"
    assert ac.main(["setup", "apply", "--workspace", str(ws)]) == 2
    assert "needs --approval" in capsys.readouterr().err
    assert ac.main(["setup", "apply", "--approval", "yes", "--agents", "ghost", "--workspace", str(ws)]) == 2
    assert "no agent named ghost" in capsys.readouterr().err
    assert ac.main(["setup", "disable", "--workspace", str(ws)]) == 0
    assert {p: p.read_bytes() for p in (ws / "_memory").glob("*.yaml")} == before


def test_apply_records_rows_grants_and_settings_with_the_owners_words(
        initialized_workspace: Path, fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    ac.set_config(ws, "enabled", True)
    args = ["setup", "apply", "--approval", "approve all, with a $5 cap", "--agents", "updater,bookkeeper",
            "--work-hours", "06:00-23:00", "--daily-usd", "5", "--workspace", str(ws)]
    registry = (ws / "_memory" / "agents.yaml").read_text()
    assert ac.main([*args, "--dry-run"]) == 0
    shown = capsys.readouterr().out
    assert "would grant updater `update-light`: every 30m" in shown
    assert (ws / "_memory" / "agents.yaml").read_text() == registry, "a dry run writes nothing"
    assert ac.main(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ['set update.work_hours: "06:00-23:00"', "set autocode.daily_usd: 5.0"]
    assert "add the registry row of superagent" in out and "grant bookkeeper `after-harvest`: fire-only" in out
    grants = {name: {g["name"]: g for g in ag.grants(ws, name)} for name in ("superagent", "updater", "bookkeeper")}
    assert set(grants["superagent"]) == {"daily-update", "weekly-review", "monthly-review"}
    light = grants["updater"]["update-light"]
    assert (light["trigger"], light["duty"], light["approval"], light["status"]) == (
        "every 30m", "update-light", "approve all, with a $5 cap", "active")
    assert grants["bookkeeper"]["after-harvest"]["duty"] is None
    assert _row(ws, "superagent")["status"] == "off", "a row is marked running only when the session is"
    state = ac.setup_state(ws, ac.FRAMEWORK)
    assert state["configured"] is True and state["settings"]["daily_usd"] == 5.0
    assert "[granted]" in "\n".join(ac.render_setup(state))
    before = (ws / "_memory" / "agents.yaml").read_text()
    assert ac.main(args) == 0
    assert capsys.readouterr().out.strip() == "nothing to change: the setup already holds these terms"
    assert (ws / "_memory" / "agents.yaml").read_text() == before


def test_apply_takes_the_owners_changes(initialized_workspace: Path, fake: Fake,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    ac.set_config(ws, "enabled", True)
    base = ["setup", "apply", "--approval", "ok", "--workspace", str(ws)]
    assert ac.main([*base, "--agents", "updater", "--work-hours", "07:00-23:00"]) == 2
    assert "lies outside the work hours 07:00-23:00" in capsys.readouterr().err
    assert ag.grants(ws, "updater") == [], "a refused setup writes nothing"
    assert ac.main([*base, "--agents", "updater", "--work-hours", "07:00-23:00",
                    "--wake", 'updater:update-full=cron "15 7 * * *"', "--wake",
                    'superagent:daily-update=cron "0 7 * * *"', "--skip", "superagent:monthly-review"]) == 0
    capsys.readouterr()
    assert {g["name"]: g["trigger"] for g in ag.grants(ws, "updater")} == {
        "update-light": "every 30m", "update-full": 'cron "15 7 * * *"'}
    assert {g["name"]: g["trigger"] for g in ag.grants(ws, "superagent")} == {
        "daily-update": 'cron "0 7 * * *"', "weekly-review": 'cron "0 18 * * 0"'}
    for flags, message in ((["--wake", "updater:nope=every 1h"], "no such proposed wake: updater:nope"),
                           (["--wake", "updater-update-full"], "write it as AGENT:NAME=TRIGGER"),
                           (["--work-hours", "all day"], "write them as"),
                           (["--daily-usd", "lots"], "is not a number"),
                           (["--daily-usd", "-1"], "a positive number")):
        assert ac.main([*base, "--agents", "updater", *flags]) == 2
        assert message in capsys.readouterr().err
    ag.set_row(ws, "project:kitchen")
    assert ac.main([*base, "--agents", "project:kitchen", "--no-wakes", "--work-hours", "none"]) == 0
    assert ag.grants(ws, "project:kitchen") == []
    assert yaml.safe_load(_config(ws).read_text())["preferences"]["update"]["work_hours"] is None


def test_mark_follows_the_registration(initialized_workspace: Path, fake: Fake,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    fake.answers["autocode me"] = (0, json.dumps({"registered": False}), "")
    assert ac.main(["mark", "superagent", "--workspace", str(ws)]) == 1
    assert "this session is not registered as sa:superagent: register first" in capsys.readouterr().err
    fake.answers["autocode me"] = (0, json.dumps({"registered": True, "agent": {"id": "sa:superagent"}}), "")
    assert ac.main(["mark", "superagent", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip() == "sa:superagent: row `dormant` on testbox"
    ag.grant(ws, "superagent", wake="daily-update", trigger='cron "30 8 * * *"', duty="daily-update",
             purpose="p", approval="yes")
    assert ac.mark(ws, ac.FRAMEWORK, "superagent")["status"] == "active"
    with pytest.raises(RuntimeError, match="still registered"):
        ac.mark(ws, ac.FRAMEWORK, "superagent", off=True)
    fake.answers["autocode me"] = (0, json.dumps({"registered": False}), "")
    assert ac.mark(ws, ac.FRAMEWORK, "superagent", off=True)["status"] == "off"
    assert _row(ws, "superagent")["status"] == "off"


# ------------------------------------------------- the check of a granted wake

def _spent(fake: Fake, agent_id: str, usd: float) -> None:
    fake.answers["autocode me"] = (0, json.dumps(
        {"registered": True, "agent": {"id": agent_id, "cost": {"today_usd": usd}}}), "")


def test_the_wake_check_without_a_cap_never_calls_autocode(initialized_workspace: Path, fake: Fake,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    assert ac.main(["check", "updater", "update-full", "--wake", "update-full", "--workspace", str(ws)]) == 1
    assert capsys.readouterr().out == "", "no grant: skipped in silence"
    ag.grant(ws, "updater", wake="update-full", trigger='cron "0 6 * * *"', duty="update-full",
             purpose="p", approval="yes")
    assert ac.main(["check", "updater", "update-full", "--wake", "update-full", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.startswith("updater update-full: due: never ran")
    assert fake.calls == [], "no cap is set, so nothing is asked of the runtime"
    assert ac.main(["check", "updater", "nope", "--wake", "update-full", "--workspace", str(ws)]) == 2


def test_the_wake_check_keeps_the_daily_cap(initialized_workspace: Path, fake: Fake,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    ag.grant(ws, "updater", wake="update-full", trigger='cron "0 6 * * *"', duty="update-full",
             purpose="p", approval="yes")
    ac.set_config(ws, "daily_usd", 5.0)
    assert ac.daily_cap(ws, ac.FRAMEWORK, "updater") == 5.0
    _spent(fake, "sa:updater", 1.25)
    assert ac.run_check(ws, ac.FRAMEWORK, "updater", "update-full", "update-full")[0] == 0
    _spent(fake, "sa:updater", 5.0)
    assert ac.run_check(ws, ac.FRAMEWORK, "updater", "update-full", "update-full") == (1, "")
    sent = [c["cmd"] for c in fake.calls if c["cmd"][1] == "send"]
    assert len(sent) == 1 and sent[0][2] == "sa:superagent" and sent[0][-2:] == ["--subject", "Cost cap"]
    assert "updater spent $5.00 of its $5 daily cap" in sent[0][3]
    assert ac.run_check(ws, ac.FRAMEWORK, "updater", "update-full", "update-full") == (1, "")
    assert len([c for c in fake.calls if c["cmd"][1] == "send"]) == 1, "one notice a day"
    # A row's own cap wins, and the lead reports its own cap to the owner.
    ag.set_row(ws, "updater", daily_usd=20.0)
    assert ac.daily_cap(ws, ac.FRAMEWORK, "updater") == 20.0
    assert ac.run_check(ws, ac.FRAMEWORK, "updater", "update-full", "update-full")[0] == 0
    ag.grant(ws, "superagent", wake="daily-update", trigger='cron "30 8 * * *"', duty="daily-update",
             purpose="p", approval="yes")
    _spent(fake, "sa:superagent", 7.5)
    assert ac.run_check(ws, ac.FRAMEWORK, "superagent", "daily-update", "daily-update") == (1, "")
    last = [c["cmd"] for c in fake.calls if c["cmd"][1] == "send"][-1]
    assert last[2] == "owner" and last[-1] == "--report"
    # A cap that cannot be checked is a failed check, never "run anyway" and never "not due".
    _spent(fake, "sa:other", 0.0)
    assert ac.main(["check", "superagent", "daily-update", "--wake", "daily-update", "--workspace", str(ws)]) == 3
    assert "today's spend cannot be read here (this session is sa:other)" in capsys.readouterr().err
    fake.answers["autocode me"] = (0, "not json", "")
    assert ac.run_check(ws, ac.FRAMEWORK, "superagent", "daily-update", "daily-update")[0] == 3


def test_the_wake_check_script_finds_uv_on_a_bare_path(initialized_workspace: Path, framework_dir: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    ws = initialized_workspace
    script = framework_dir / "tools" / "wake_check.sh"
    env = {"HOME": str(Path.home()), "PATH": f"{Path(uv).parent}:/usr/bin:/bin"}
    args = ["sh", str(script), "updater", "update-full", "--wake", "update-full", "--workspace", str(ws)]
    skipped = subprocess.run(args, capture_output=True, text=True, env=env, cwd="/", check=False, timeout=120)
    assert (skipped.returncode, skipped.stdout) == (1, ""), skipped.stderr
    ag.grant(ws, "updater", wake="update-full", trigger='cron "0 6 * * *"', duty="update-full",
             purpose="p", approval="yes", now=dt.datetime.now().astimezone())
    due = subprocess.run(args, capture_output=True, text=True, env=env, cwd="/", check=False, timeout=120)
    assert due.returncode == 0 and due.stdout.startswith("updater update-full: due"), due.stderr


# ------------------------------------------------------------ the start skill

def test_start_superagent_loads_the_start_skill() -> None:
    from superagent.tools import skill_loader

    skills = skill_loader.discover_skills()

    def fired(prompt: str) -> set[str]:
        return {s["name"] for s in skill_loader.match_skills(prompt, skills)}

    for prompt in ("start superagent", "Start Superagent", "start the agents", "turn on autocode mode",
                   "stop superagent", "change the agents' schedule"):
        assert "superagent-start" in fired(prompt), prompt
    for prompt in ("start my day", "let's start", "how do I start a project", "stop"):
        assert "superagent-start" not in fired(prompt), prompt


def test_the_start_skill_asks_in_cards_and_names_only_real_commands(framework_dir: Path) -> None:
    body = (framework_dir / "skills" / "start.md").read_text(encoding="utf-8")
    commands = set(re.findall(r"superagent\.tools\.autocode ([a-z-]+(?: (?:enable|disable|apply))?)", body))
    assert commands == {"setup", "setup enable", "setup apply", "setup disable", "prompt", "mark",
                        "start", "stop"}
    known = set(ac.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    assert {c.split()[0] for c in commands} <= known
    order = [body.index(mark) for mark in ("setup enable", "setup apply --approval", "tools.autocode prompt",
                                           "tools.autocode mark", "tools.autocode start <agent>")]
    assert order == sorted(order), "the switch, then the approved settings, then the lead, then the others"
    assert "Never ask the owner to edit a file or to run a command" in body
    assert "autocode settings set" not in body, "no owner-side command is part of the start"
    for flag in re.findall(r"--[a-z][a-z-]+", body):
        assert flag in {"--json", "--approval", "--agents", "--work-hours", "--daily-usd", "--wake", "--skip",
                        "--no-wakes", "--runner-model", "--runner-variant", "--dry-run", "--off"}, flag
