# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/asks.py`: the asks queue (`_memory/asks.yaml`).

A run with nobody present writes its question here and carries on
(contracts/autonomy.md § 7). The tests pin the row shape, the idempotency key,
answers, expiry, the conflict merge, and that the template survives a write.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import asks, heal, validate

PST = dt.timezone(dt.timedelta(hours=-8))
NOW = dt.datetime(2026, 11, 22, 9, 0, tzinfo=PST)


def _rows(ws: Path) -> list[dict[str, Any]]:
    return asks.load_asks(ws)


def _file(ws: Path) -> Path:
    return ws / "_memory" / "asks.yaml"


def test_a_fresh_workspace_has_an_empty_queue(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    assert _file(ws).is_file(), "the template is seeded by workspace_init"
    assert _rows(ws) == [] and asks.open_asks(ws, now=NOW) == []
    assert validate.validate_file(_file(ws), validate.Path(asks.FRAMEWORK)) == []


def test_add_writes_the_canonical_row_and_keeps_the_template(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    before = _file(ws).read_text()
    ask_id, created = asks.add_ask(
        ws, agent="updater", question="The bank feed is set to manual capture.\n  Pull it now?",
        context="Last pulled 9 days ago.", options=["yes", "skip"], key="harvest:simplefin",
        related=["watch:simplefin"], expires="7d", now=NOW)
    assert created and ask_id == "ask-2026-11-22-testbox-001"
    row = _rows(ws)[0]
    assert tuple(row) == asks.FIELDS
    assert row == {
        "id": ask_id, "asked_at": "2026-11-22T09:00:00-08:00", "agent": "updater",
        "question": "The bank feed is set to manual capture. Pull it now?",
        "context": "Last pulled 9 days ago.", "options": ["yes", "skip"], "tier": "approve",
        "key": "harvest:simplefin", "related": ["watch:simplefin"],
        "expires_at": "2026-11-29T09:00:00-08:00", "status": "open", "answer": None,
        "closed_at": None, "closed_by": None, "notes": None,
    }
    after = _file(ws).read_text()
    assert after.startswith(before.split("last_updated:")[0]), "header and comments are untouched"
    assert "#   id          — " in after, "the template's field notes survive"
    assert yaml.safe_load(after)["last_updated"] == "2026-11-22T09:00:00-08:00"
    assert validate.warnings_for("asks.yaml", yaml.safe_load(after), asks.FRAMEWORK, ws) == []


def test_one_open_ask_per_key(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    first, created = asks.add_ask(ws, agent="updater", question="Project X looks stalled. Pause it?",
                                  key="project-drift:x:stalled", now=NOW)
    again, created_again = asks.add_ask(ws, agent="updater", question="Still stalled. Pause it?",
                                        key="project-drift:x:stalled", now=NOW + dt.timedelta(days=1))
    assert created and not created_again and again == first
    assert len(_rows(ws)) == 1, "a finding that repeats every cycle asks once"
    asks.answer_ask(ws, first, answer="pause it", now=NOW + dt.timedelta(days=2))
    third, created_third = asks.add_ask(ws, agent="updater", question="Stalled again. Pause it?",
                                        key="project-drift:x:stalled", now=NOW + dt.timedelta(days=30))
    assert created_third and third != first, "once answered, the same finding may ask again"
    other, created_other = asks.add_ask(ws, agent="updater", question="No key.", now=NOW)
    assert created_other and other not in (first, third)


def test_answer_and_withdraw(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    one, _ = asks.add_ask(ws, agent="bookkeeper", question="A new charge of 14.99 recurs. Track it?",
                          options=["bill", "subscription", "ignore"], now=NOW)
    two, _ = asks.add_ask(ws, agent="project:kitchen", question="Order the tile sample?", tier="owner",
                          now=NOW)
    later = NOW + dt.timedelta(hours=3)
    row = asks.answer_ask(ws, one, answer="subscription", now=later)
    assert (row["status"], row["answer"], row["closed_by"], row["closed_at"]) == (
        "answered", "subscription", "owner", "2026-11-22T12:00:00-08:00")
    gone = asks.answer_ask(ws, two, withdraw="the sample arrived", now=later)
    assert (gone["status"], gone["answer"], gone["closed_by"]) == (
        "withdrawn", "the sample arrived", "project:kitchen")
    assert asks.open_asks(ws, now=later) == []
    with pytest.raises(asks.signals.WriteRefusedError, match="already answered"):
        asks.answer_ask(ws, one, answer="again", now=later)
    with pytest.raises(asks.signals.WriteRefusedError, match="no ask with id"):
        asks.answer_ask(ws, "ask-2026-01-01-testbox-999", answer="x", now=later)
    with pytest.raises(ValueError, match="exactly one"):
        asks.answer_ask(ws, one, now=later)
    stored = {r["id"]: r for r in _rows(ws)}
    assert stored[one]["question"].startswith("A new charge"), "the question itself never changes"


def test_expiry(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    short, _ = asks.add_ask(ws, agent="updater", question="Pull the feed today?", expires="36h", now=NOW)
    dated, _ = asks.add_ask(ws, agent="updater", question="Renew before the 1st?",
                            expires="2026-12-01", now=NOW)
    forever, _ = asks.add_ask(ws, agent="updater", question="Keep this watcher?", now=NOW)
    day_two = NOW + dt.timedelta(days=2)
    waiting = [r["id"] for r in asks.open_asks(ws, now=day_two)]
    assert waiting == [dated, forever], "an ask past its expiry no longer waits, closed or not"
    assert "open (past its expiry)" in asks.render_line(_rows(ws)[0], day_two)
    closed = asks.expire_asks(ws, now=day_two)
    assert [r["id"] for r in closed] == [short]
    assert (closed[0]["status"], closed[0]["closed_by"], closed[0]["answer"]) == ("expired", "expiry", None)
    assert asks.expire_asks(ws, now=day_two) == [], "a second pass finds nothing"
    assert [r["id"] for r in asks.expire_asks(ws, now=NOW + dt.timedelta(days=30))] == [dated]
    assert [r["id"] for r in asks.open_asks(ws, now=NOW + dt.timedelta(days=400))] == [forever]


@pytest.mark.parametrize("kwargs, match", [
    ({"agent": "ghost", "question": "q"}, "no role named 'ghost'"),
    ({"agent": "Updater", "question": "q"}, "not a role name"),
    ({"agent": "updater", "question": "  "}, "must not be empty"),
    ({"agent": "updater", "question": "q", "tier": "act"}, "tier must be one of"),
    ({"agent": "updater", "question": "q", "key": "has a space"}, "key 'has a space'"),
    ({"agent": "updater", "question": "q", "expires": "soon"}, "not a duration"),
    ({"agent": "updater", "question": "q", "expires": "2026-01-01"}, "must lie in the future"),
])
def test_bad_asks_are_refused(initialized_workspace: Path, kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        asks.add_ask(initialized_workspace, now=NOW, **kwargs)
    assert _rows(initialized_workspace) == []


def test_free_text_is_redacted(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    ask_id, _ = asks.add_ask(ws, agent="updater", question="The portal wants a login. password: hunter2secret ok?",
                             context="token=abcdef0123456789abcdef0123456789abcdef01", now=NOW)
    row = _rows(ws)[0]
    assert "hunter2secret" not in row["question"]
    assert "abcdef0123456789abcdef0123456789abcdef01" not in row["context"]
    asks.answer_ask(ws, ask_id, answer="use password: anothersecret99", now=NOW)
    assert "anothersecret99" not in _rows(ws)[0]["answer"]


def test_a_broken_file_is_refused_and_left_alone(initialized_workspace: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    broken = "schema_version: 1\nasks:\n  - id: [\n"
    _file(ws).write_text(broken)
    with pytest.raises(asks.signals.CorruptFileError):
        asks.add_ask(ws, agent="updater", question="q", now=NOW)
    assert asks.main(["add", "--agent", "updater", "--question", "q", "--workspace", str(ws)]) == 1
    assert "refusing to rewrite" in capsys.readouterr().err
    assert _file(ws).read_text() == broken


def test_a_workspace_without_the_file_gets_one(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    assert asks.load_asks(ws) == []
    ask_id, created = asks.add_ask(ws, agent="superagent", question="Keep the old phone plan?", now=NOW)
    assert created and yaml.safe_load(_file(ws).read_text())["asks"][0]["id"] == ask_id


def test_cli(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    base = ["--workspace", str(ws), "--now", NOW.isoformat()]
    assert asks.main(["add", "--agent", "updater", "--question", "Pull the feed?", "--option", "yes",
                      "--option", "skip", "--key", "harvest:simplefin", "--expires", "7d", *base]) == 0
    ask_id = capsys.readouterr().out.strip()
    assert ask_id == "ask-2026-11-22-testbox-001"
    assert asks.main(["add", "--agent", "updater", "--question", "Pull the feed?",
                      "--key", "harvest:simplefin", *base]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ask_id and "nothing added" in captured.err
    assert asks.main(["add", "--agent", "project:kitchen-reno", "--question", "Sign the form?",
                      "--tier", "owner", *base]) == 0
    capsys.readouterr()
    assert asks.main(["list", "--open", *base]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{ask_id}  updater  open  2026-11-22  expires 2026-11-29  Pull the feed?  [yes / skip]",
        "ask-2026-11-22-testbox-002  project:kitchen-reno  open  2026-11-22  Sign the form?",
    ]
    assert asks.main(["list", "--open", "--agent", "updater", "--count", *base]) == 0
    assert capsys.readouterr().out.strip() == "open=1"
    assert asks.main(["answer", ask_id, "--answer", "yes", *base]) == 0
    assert capsys.readouterr().out.strip() == f"{ask_id}: answered"
    assert asks.main(["answer", ask_id, "--answer", "yes", *base]) == 1
    assert "already answered" in capsys.readouterr().err
    assert asks.main(["list", "--json", *base]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["status"] for r in rows] == ["answered", "open"] and rows[0]["answer"] == "yes"
    assert asks.main(["expire", *base]) == 0 and capsys.readouterr().out == ""
    assert asks.main(["add", "--agent", "nobody", "--question", "q", *base]) == 2
    with pytest.raises(SystemExit) as info:
        asks.main(["answer", ask_id, *base])
    assert info.value.code == 2


def test_a_conflict_copy_of_the_queue_merges_and_a_closed_ask_stays_closed(
        initialized_workspace: Path) -> None:
    ws = initialized_workspace
    shared, _ = asks.add_ask(ws, agent="updater", question="Pull the feed?", now=NOW)
    base_text = _file(ws).read_text()
    # Machine A answers the shared ask; machine B (the copy) adds a new one.
    asks.answer_ask(ws, shared, answer="yes", now=NOW + dt.timedelta(hours=1))
    answered_text = _file(ws).read_text()
    _file(ws).write_text(base_text)
    new_id, _ = asks.add_ask(ws, agent="bookkeeper", question="Track the new charge?",
                             now=NOW + dt.timedelta(hours=2))
    copy = ws / "_memory" / "asks.sync-conflict-20261122-120000-ABCDEFG.yaml"
    copy.write_text(_file(ws).read_text())
    _file(ws).write_text(answered_text)
    outcomes = heal.sweep(ws, dry_run=False)
    assert [o.status for o in outcomes] == ["merged"], [o.detail for o in outcomes]
    assert not copy.exists()
    merged = {r["id"]: r for r in _rows(ws)}
    assert set(merged) == {shared, new_id}
    assert merged[shared]["status"] == "answered" and merged[shared]["answer"] == "yes"
    # The other way round: the copy holds the answer, the original still shows the ask open.
    text, added, replaced = heal.merge_yaml_rows(
        "asks.yaml", base_text, answered_text, "asks", close_rule=heal.CLOSE_RULES["asks.yaml"])
    assert (added, replaced) == (0, 1)
    assert yaml.safe_load(text)["asks"][-1]["status"] == "answered"
