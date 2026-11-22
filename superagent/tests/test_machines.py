# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/machines.py` and `tools/syncnames.py`.

Machine identity, the beat file, the presence line, machine-local state, the
merged prompt log, and the file names a folder-sync tool leaves behind.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
from pathlib import Path

import pytest

from superagent.tools import log_user_query, machines
from superagent.tools import syncnames as sn

NOW = dt.datetime.fromisoformat("2026-10-08T10:00:00-07:00")


# ------------------------------------------------------------------ syncnames

@pytest.mark.parametrize("name, original", [
    ("todo.sync-conflict-20261121-153000-ABCDEFG.yaml", "todo.yaml"),
    ("todo.history.sync-conflict-20261121-153000-ABCDEFG.jsonl", "todo.history.jsonl"),
    ("Bank.ref.sync-conflict-20261121-153000-ABCDEFG.md", "Bank.ref.md"),
    ("notes.sync-conflict-20261121-153000-ABCDEFG", "notes"),
    (".sync-conflict-20261121-153000-ABCDEFG.version", ".version"),
])
def test_conflict_copy_names(name: str, original: str) -> None:
    assert sn.is_conflict_copy(name) and sn.is_sync_artifact(name)
    assert sn.conflict_original(Path("a/b") / name) == Path("a/b") / original
    assert sn.conflict_info(name) == {"date": "2026-11-21", "time": "15:30:00", "device": "ABCDEFG"}


@pytest.mark.parametrize("name", [
    "todo.yaml", "sync-conflict-notes.md", "report.sync-conflict.pdf", "a.sync-conflict-2026-1.md",
])
def test_ordinary_names_are_not_conflict_copies(name: str) -> None:
    assert not sn.is_conflict_copy(name)
    assert sn.conflict_original(Path(name)) is None and sn.conflict_info(name) is None


def test_sync_temp_files_and_folders() -> None:
    assert sn.is_sync_temp(".syncthing.scan.pdf.tmp") and sn.is_sync_temp("~syncthing~scan.pdf.tmp")
    assert not sn.is_sync_temp("scan.pdf.tmp") and not sn.is_sync_temp(".syncthing.note")
    keep = Path("Sources/Bank/statement.pdf")
    paths = [keep, Path("Sources/Bank/.syncthing.statement.pdf.tmp"),
             Path("Sources/Bank/statement.sync-conflict-20261121-153000-ABCDEFG.pdf"),
             Path(".stversions/Sources/Bank/statement.pdf")]
    assert sn.real_files(paths) == [keep]


# ------------------------------------------------------------------- identity

def test_name_comes_from_the_environment_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERAGENT_MACHINE", "Lab-Box.example.org")
    assert machines.machine_name() == "lab-box"
    assert machines.machine_tag() == "labbox"
    assert not machines.machine_file().exists(), "an environment name is never stored"


def test_name_is_fixed_at_first_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERAGENT_MACHINE")
    monkeypatch.setattr(machines, "_host_name", lambda: "Kitchen-Mac.local")
    assert machines.machine_name() == "kitchen-mac"
    stored = json.loads(machines.machine_file().read_text())
    assert stored["name"] == "kitchen-mac" and stored["source"] == "hostname" and stored["uid"]
    # The host name changes later (a network renames it); the machine keeps its name.
    monkeypatch.setattr(machines, "_host_name", lambda: "dhcp-10-0-0-7")
    assert machines.machine_name() == "kitchen-mac"
    assert machines.machine_identity()["uid"] == stored["uid"]


def test_owner_can_set_the_name(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("SUPERAGENT_MACHINE")
    monkeypatch.setattr(machines, "_host_name", lambda: "localhost")
    assert machines.main(["name", "--set", "desk"]) == 0
    assert capsys.readouterr().out.strip() == "desk"
    assert machines.machine_name() == "desk"
    assert machines.main(["name", "--set", "Bad Name!"]) == 2
    assert "not a machine name" in capsys.readouterr().err
    assert machines.machine_name() == "desk"


def test_unusable_host_name_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERAGENT_MACHINE")
    monkeypatch.setattr(machines, "_host_name", lambda: "___")
    assert machines.machine_name(create=False) == "machine"
    assert not machines.machine_file().exists(), "create=False stores nothing"


# ----------------------------------------------------------------------- beat

def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    return ws


def test_beat_writes_one_small_file(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert machines.beat(ws, now=NOW) is True
    beat = json.loads((ws / "_memory" / "machines" / "testbox.json").read_text())
    assert beat["machine"] == "testbox" and beat["at"] == "2026-10-08T10:00:00-07:00"
    assert sorted(p.name for p in (ws / "_memory" / "machines").iterdir()) == ["testbox.json"]
    # Hot-path callers throttle: a beat younger than the interval is left alone.
    assert machines.beat(ws, now=NOW + dt.timedelta(seconds=30), min_interval_s=60) is False
    assert machines.beat(ws, now=NOW + dt.timedelta(seconds=90), min_interval_s=60) is True


def test_beat_needs_a_workspace(tmp_path: Path) -> None:
    assert machines.beat(tmp_path / "nowhere", now=NOW) is False
    assert not (tmp_path / "nowhere").exists()
    assert machines.main(["beat", "--workspace", str(tmp_path / "nowhere")]) == 1


def _other_beat(ws: Path, name: str, minutes_ago: float, uid: str = "zz") -> None:
    folder = ws / "_memory" / "machines"
    folder.mkdir(exist_ok=True)
    at = (NOW - dt.timedelta(minutes=minutes_ago)).isoformat()
    (folder / f"{name}.json").write_text(json.dumps({"machine": name, "uid": uid, "at": at}))


def test_who_lists_other_machines_that_wrote_lately(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    machines.beat(ws, now=NOW)
    _other_beat(ws, "laptop", 12)
    _other_beat(ws, "tablet", 0.2)
    _other_beat(ws, "old-box", 45)
    (ws / "_memory" / "machines" / "laptop.sync-conflict-20261008-095900-ABCDEFG.json").write_text(
        json.dumps({"machine": "ghost", "uid": "g", "at": NOW.isoformat()}))
    (ws / "_memory" / "machines" / "broken.json").write_text("{not json")
    rows = machines.who(ws, now=NOW)
    assert [r["machine"] for r in rows] == ["tablet", "laptop"], "own beat, old beats and sync copies are left out"
    assert machines.presence_lines(ws, now=NOW) == [
        "tablet wrote here under a minute ago: let the sync settle and re-read a file before editing it.",
        "laptop wrote here 12 min ago: let the sync settle and re-read a file before editing it.",
    ]
    assert [r["machine"] for r in machines.who(ws, now=NOW, within_minutes=60)] == ["tablet", "laptop", "old-box"]


def test_two_machines_with_one_name_are_told(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    _other_beat(ws, "testbox", 5, uid="another-machine")
    lines = machines.presence_lines(ws, now=NOW)
    assert len(lines) == 1 and "also calls itself testbox" in lines[0] and "name --set" in lines[0]


def test_who_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = _ws(tmp_path)
    _other_beat(ws, "laptop", 3)
    now = dt.datetime.now().astimezone()
    (ws / "_memory" / "machines" / "laptop.json").write_text(json.dumps(
        {"machine": "laptop", "uid": "u", "at": (now - dt.timedelta(minutes=3)).isoformat(), "harness": "x"}))
    assert machines.main(["who", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.startswith("laptop wrote here 3 min ago")
    assert machines.main(["who", "--workspace", str(ws), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["machine"] == "laptop"


# ---------------------------------------------------------------- local state

def test_local_state_is_per_workspace_and_outside_it(tmp_path: Path) -> None:
    one, two = _ws(tmp_path / "a"), _ws(tmp_path / "b")
    machines.write_local_state(one, refresh_suggested="2026-10-08")
    machines.write_local_state(one, other=1)
    assert machines.read_local_state(one) == {
        "refresh_suggested": "2026-10-08", "other": 1, "workspace": str(one.resolve())}
    assert machines.read_local_state(two) == {}
    path = machines.local_state_path(one)
    assert one not in path.parents and path.parent.name == "state"


# ------------------------------------------------------------- the prompt log

def _log_prompt(ws: Path, monkeypatch: pytest.MonkeyPatch, prompt: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))
    assert log_user_query.main(["--workspace", str(ws), "--source", "test"]) == 0


def test_prompt_log_is_one_file_per_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _ws(tmp_path)
    legacy = ws / "_memory" / "user-queries.jsonl"
    old = json.dumps({"ts": "2026-09-01T08:00:00-07:00", "source": "cursor", "prompt": "old", "length": 3}) + "\n"
    legacy.write_text(old)
    _log_prompt(ws, monkeypatch, "what bills are due")
    monkeypatch.setenv("SUPERAGENT_MACHINE", "laptop")
    _log_prompt(ws, monkeypatch, "<task-notification> done")
    assert legacy.read_text() == old, "the single file of an older workspace is read-only history"
    folder = ws / "_memory" / "user-queries"
    assert sorted(p.name for p in folder.iterdir()) == ["laptop.jsonl", "testbox.jsonl"]
    (folder / "laptop.sync-conflict-20261008-101010-ABCDEFG.jsonl").write_text(
        json.dumps({"ts": "2026-10-08T10:10:10-07:00", "prompt": "a conflict copy"}) + "\n")
    (folder / ".syncthing.tablet.jsonl.tmp").write_text("{}\n")
    rows = machines.read_user_queries(ws)
    assert rows[0]["prompt"] == "old", "oldest first"
    # The two new rows carry the same second; a tie keeps file order, so compare as a set.
    assert sorted(r["prompt"] for r in rows[1:]) == ["<task-notification> done", "what bills are due"]
    assert [r["prompt"] for r in machines.read_user_queries(ws, include_synthetic=False)] == [
        "old", "what bills are due"]
    assert (ws / "_memory" / "machines" / "testbox.json").is_file(), "a prompt marks the machine active"


def test_prompt_log_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture[str]) -> None:
    ws = _ws(tmp_path)
    (ws / "_memory" / "user-queries.jsonl").write_text(
        json.dumps({"ts": "2026-09-01T08:00:00-07:00", "source": "cursor", "prompt": "old one"}) + "\n"
        + "not json\n")
    _log_prompt(ws, monkeypatch, "newer one")
    assert log_user_query.main(["--workspace", str(ws), "--show"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[0].endswith("cursor  old one") and out[1].endswith("test  newer one")
    assert log_user_query.main(["--workspace", str(ws), "--show", "--since", "2026-09-02", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prompt"] == "newer one"
    assert log_user_query.main(["--workspace", str(ws), "--show", "--last", "1"]) == 0
    assert capsys.readouterr().out.strip().endswith("newer one")
    assert log_user_query.main(["--workspace", str(ws), "--show", "--since", "yesterday"]) == 2


def test_dated_ids_with_a_machine_tag_are_not_redacted() -> None:
    text = "pass sig-2026-10-06-mybox-002 and task-20261006-mybox-001 along"
    assert log_user_query.redact(text) == text
