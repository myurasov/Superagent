# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/autocode.py`: starting, parking and stopping agents that run as sessions.

No test reaches a real `autocode` or `opencode` command: `autocode._run` and
`autocode._binary` are replaced. The framework must work with Autocode off,
so the last tests pin that no other tool depends on this module.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import agents as ag
from superagent.tools import autocode as ac


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
    assert ("name `update-light`; trigger `every 30m`; check `uv run python -m superagent.tools.agents "
            "check updater update-light --wake update-light`") in text
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
    assert "check `uv run python -m superagent.tools.agents check project:kitchen --wake permit`" in project
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
