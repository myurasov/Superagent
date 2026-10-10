# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/signals.py`: the one writer for action-signal and interaction-log rows."""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from superagent.tools import signals as sg
from superagent.tools.log_user_query import REDACTED_PASSWORD
from superagent.tools.validate import check_action_signals, check_interaction_log, skill_stems

TS = "2026-10-08T09:00:00-07:00"
FAKE = "Zq7Xw2Lk9v"  # synthetic secret-shaped string, never a real credential
SIGNAL_KEYS = ["id", "captured_at", "target", "kind", "trigger_phrase", "context", "artifact_ref",
               "proposed_direction", "source_skill", "source_turn", "status", "became_suggestion",
               "became_action", "processed_at", "notes"]
ILOG_KEYS = ["id", "ts", "skill", "action", "summary", "related_domain", "related_project",
             "related_asset", "related_account", "action_items", "ingestion_log_ref"]
CORRUPT_YAML = "schema_version: 1\nsignals:\n- id: x\n   bad_indent: [\n"

HAND_FILE = """\
# header comment kept verbatim
schema_version: 1
last_updated: '2026-10-01T10:00:00-07:00'
signals:
- id: sig-2026-10-01-001
  captured_at: '2026-10-01T10:00:00-07:00'
  target: tailor
  kind: correction
  trigger_phrase: "a hand-written row"
  context: >-
    folded context that must survive
    byte for byte
  status: captured
  notes: null
- id: sig-2026-10-01-002
  captured_at: '2026-10-01T11:00:00-07:00'
  target: superagent
  kind: wish
  trigger_phrase: 'second row'
  context: second
  status: open
- id: sig-2026-10-01-003
  captured_at: '2026-10-01T12:00:00-07:00'
  target: tailor
  kind: ambient
  trigger_phrase: third
  context: third
  status: processed
  processed_at: '2026-10-02T09:00:00-07:00'
  notes: done
"""


def _path(ws: Path) -> Path:
    return ws / "_memory" / "action-signals.yaml"


def _hand_ws(tmp_path: Path, text: str = HAND_FILE) -> Path:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    _path(ws).write_text(text)
    return ws


def _rows(ws: Path) -> list[dict]:
    return [r for r in yaml.safe_load(_path(ws).read_text())["signals"] if r.get("id")]


def _add(ws: Path, **over) -> str:
    fields = {"target": "tailor", "kind": "correction", "trigger_phrase": "wrong order again",
              "context": "daily-update listed P2 before P0", "source_skill": "daily-update", "ts": TS}
    fields.update(over)
    return sg.add_signal(ws, **fields)


# --------------------------------------------------------------------- add

def test_add_on_a_fresh_workspace_writes_the_canonical_row(initialized_workspace: Path) -> None:
    sid = _add(initialized_workspace)
    assert sid == "sig-2026-10-08-001"
    rows = _rows(initialized_workspace)
    assert len(rows) == 1
    assert list(rows[0]) == SIGNAL_KEYS
    assert rows[0]["status"] == "captured" and rows[0]["captured_at"] == TS
    assert check_action_signals(yaml.safe_load(_path(initialized_workspace).read_text())["signals"]) == []
    text = _path(initialized_workspace).read_text()
    assert "\n  - id: sig-2026-10-08-001\n" in text, "template indent (2) kept"
    assert yaml.safe_load(text)["last_updated"] == TS


def test_add_changes_nothing_else_in_the_file(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    _add(ws)
    new = _path(ws).read_text()
    expected_prefix = HAND_FILE.replace("'2026-10-01T10:00:00-07:00'\nsignals",
                                        f"'{TS}'\nsignals", 1)
    assert new.startswith(expected_prefix), "every pre-existing byte survives (bar last_updated)"
    assert new[len(expected_prefix):].startswith("- id: sig-2026-10-08-001\n"), "file's own indent (0)"


def test_add_mints_sequential_ids(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    assert [_add(ws), _add(ws)] == ["sig-2026-10-08-001", "sig-2026-10-08-002"]
    assert [r["id"] for r in _rows(ws)][-2:] == ["sig-2026-10-08-001", "sig-2026-10-08-002"]


def test_add_redacts_free_text(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    _add(ws, trigger_phrase=f"the portal pass {FAKE}", context=f"login jdoe/{FAKE} failed",
         proposed_direction=f"pw to {FAKE}")
    row = _rows(ws)[-1]
    for field in ("trigger_phrase", "context", "proposed_direction"):
        assert FAKE not in row[field]
        assert REDACTED_PASSWORD in row[field]
    assert FAKE not in _path(ws).read_text()


@pytest.mark.parametrize("over, message", [
    ({"kind": "critique"}, "kind must be one of"),
    ({"target": "framework"}, "target must be one of"),
    ({"source_skill": "superagent-migrate"}, "bare stem 'migrate'"),
    ({"source_skill": "not-a-skill"}, "not a skill stem"),
    ({"trigger_phrase": "  "}, "non-empty"),
])
def test_add_rejects_off_schema_values(tmp_path: Path, over: dict, message: str) -> None:
    ws = _hand_ws(tmp_path)
    with pytest.raises(ValueError, match=message):
        _add(ws, **over)
    assert _path(ws).read_text() == HAND_FILE


def test_add_turns_an_empty_flow_list_into_block_form(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path, "schema_version: 1\nlast_updated: null\nsignals: []\n")
    _add(ws)
    text = _path(ws).read_text()
    assert "signals:\n  - id: sig-2026-10-08-001\n" in text
    assert len(_rows(ws)) == 1


def test_add_keeps_the_file_mode(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    _path(ws).chmod(0o600)
    _add(ws)
    assert stat.S_IMODE(_path(ws).stat().st_mode) == 0o600


# ------------------------------------------------------------ refusals

def _ilog_ws(tmp_path: Path, text: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    (ws / "_memory" / "interaction-log.yaml").write_text(text)
    return ws


@pytest.mark.parametrize("text", [
    CORRUPT_YAML,
    "- just\n- a list\n",
    "schema_version: 1\nsignals:\n  not: a list\n",
])
def test_add_and_close_refuse_a_broken_file(tmp_path: Path, text: str) -> None:
    ws = _hand_ws(tmp_path, text)
    with pytest.raises(sg.CorruptFileError, match="refusing to rewrite"):
        _add(ws)
    with pytest.raises(sg.CorruptFileError):
        sg.close_signal(ws, "sig-2026-10-01-001", declined="x", ts=TS)
    assert _path(ws).read_text() == text, "left byte-for-byte as it was"
    assert sg.main(["add", "--workspace", str(ws), "--target", "tailor", "--kind", "wish",
                    "--trigger", "t", "--context", "c"]) == 1
    assert _path(ws).read_text() == text


def test_log_refuses_a_broken_file(tmp_path: Path) -> None:
    corrupt = CORRUPT_YAML.replace("signals", "entries")
    ws = _ilog_ws(tmp_path, corrupt)
    with pytest.raises(sg.CorruptFileError, match="line "):
        sg.append_interaction_log(ws, skill="todo", action="add_task", summary="s", ts=TS)
    assert (ws / "_memory" / "interaction-log.yaml").read_text() == corrupt


def test_text_layout_it_cannot_follow_is_refused(tmp_path: Path) -> None:
    flow = "schema_version: 1\nsignals: [{id: sig-2026-10-01-001, status: captured}]\n"
    ws = _hand_ws(tmp_path, flow)
    with pytest.raises(sg.WriteRefusedError):
        _add(ws)
    assert _path(ws).read_text() == flow


# ----------------------------------------------------------------- list

def test_open_means_status_not_processed_or_declined(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    # 001 captured, 002 off-enum 'open' (still open), 003 processed (closed)
    assert [r["id"] for r in sg.open_signals(ws)] == ["sig-2026-10-01-001", "sig-2026-10-01-002"]
    assert [r["id"] for r in sg.open_signals(ws, "tailor")] == ["sig-2026-10-01-001"]
    assert sg.open_counts(ws) == {"tailor": 1, "superagent": 1, "total": 2}


def test_list_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = _hand_ws(tmp_path)
    assert sg.main(["list", "--open", "--count", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip() == "tailor=1 superagent=1 total=2"
    assert sg.main(["list", "--open", "--workspace", str(ws)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [ln.split()[0] for ln in lines] == ["sig-2026-10-01-001", "sig-2026-10-01-002"]


def test_template_placeholder_row_is_not_a_signal(initialized_workspace: Path) -> None:
    assert sg.load_signals(initialized_workspace) == []
    assert sg.open_counts(initialized_workspace)["total"] == 0


# ---------------------------------------------------------------- close

def test_close_became_suggestion_touches_only_that_row(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    before = _rows(ws)
    row = sg.close_signal(ws, "sig-2026-10-01-001", became_suggestion="st-2026-10-08-004", ts=TS)
    after = _rows(ws)
    assert row["status"] == "processed" and row["became_suggestion"] == "st-2026-10-08-004"
    assert row["processed_at"] == TS
    assert row["notes"] == "[2026-10-08] processed: became st-2026-10-08-004"
    assert after[0] == row and after[1:] == before[1:]
    text = _path(ws).read_text()
    assert text.startswith("# header comment kept verbatim\n")
    assert HAND_FILE[HAND_FILE.index("- id: sig-2026-10-01-002"):] in text, "later rows byte-identical"


def test_close_became_action_on_an_off_enum_open_row(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    row = sg.close_signal(ws, "sig-2026-10-01-002", became_action="updated Home info.md", ts=TS)
    assert row["status"] == "processed" and row["became_action"] == "updated Home info.md"
    assert "became a workspace action" in row["notes"]


def test_close_declined_appends_to_existing_notes(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    sid = _add(ws)
    sg.close_signal(ws, sid, declined="duplicate of sig-2026-10-01-001", note="checked", ts=TS)
    row = _rows(ws)[-1]
    assert row["status"] == "declined"
    assert row["notes"] == "[2026-10-08] declined: duplicate of sig-2026-10-01-001 (checked)"
    ws2 = _hand_ws(tmp_path / "b", HAND_FILE.replace("  notes: null\n", "  notes: first note\n", 1))
    row2 = sg.close_signal(ws2, "sig-2026-10-01-001", declined="no longer relevant", ts=TS)
    assert row2["notes"] == "first note [2026-10-08] declined: no longer relevant"


def test_close_refuses_closed_unknown_and_ambiguous(tmp_path: Path) -> None:
    ws = _hand_ws(tmp_path)
    with pytest.raises(sg.WriteRefusedError, match="already processed"):
        sg.close_signal(ws, "sig-2026-10-01-003", declined="x", ts=TS)
    with pytest.raises(sg.WriteRefusedError, match="no signal"):
        sg.close_signal(ws, "sig-2026-01-01-999", declined="x", ts=TS)
    with pytest.raises(ValueError, match="exactly one"):
        sg.close_signal(ws, "sig-2026-10-01-001", declined="x", became_action="y", ts=TS)
    assert _path(ws).read_text() == HAND_FILE
    assert sg.main(["close", "sig-2026-10-01-003", "--declined", "x", "--workspace", str(ws)]) == 1
    with pytest.raises(SystemExit) as info:
        sg.main(["close", "sig-2026-10-01-001", "--declined", "x", "--became-action", "y"])
    assert info.value.code == 2


# ------------------------------------------------------- interaction log

def test_log_appends_a_canonical_row(initialized_workspace: Path, framework_dir: Path) -> None:
    rid = sg.append_interaction_log(initialized_workspace, skill="todo", action="add_task",
                                    summary="added a P1 task", ts=TS, related_domain="home",
                                    action_items=["follow up"])
    assert rid == "ilog-2026-10-08-001"
    data = yaml.safe_load((initialized_workspace / "_memory" / "interaction-log.yaml").read_text())
    rows = [r for r in data["entries"] if r.get("id")]
    assert list(rows[-1]) == ILOG_KEYS
    assert rows[-1]["related_domain"] == "home" and rows[-1]["action_items"] == ["follow up"]
    assert check_interaction_log(rows, skill_stems(framework_dir, initialized_workspace)) == []
    assert sg.append_interaction_log(initialized_workspace, skill=None, action="note",
                                     summary="non-skill note", ts=TS) == "ilog-2026-10-08-002"


@pytest.mark.parametrize("skill, message", [
    ("superagent-migrate", "bare stem 'migrate'"),
    ("browserctl (amazon)", "not a skill stem"),
])
def test_log_refuses_non_stem_skills(initialized_workspace: Path, skill: str, message: str) -> None:
    path = initialized_workspace / "_memory" / "interaction-log.yaml"
    before = path.read_text()
    with pytest.raises(ValueError, match=message):
        sg.append_interaction_log(initialized_workspace, skill=skill, action="x", summary="y", ts=TS)
    assert path.read_text() == before
    assert sg.main(["log", "--workspace", str(initialized_workspace), "--skill", skill,
                    "--action", "x", "--summary", "y"]) == 2


def test_cli_add_prints_the_id(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = sg.main(["add", "--workspace", str(initialized_workspace), "--target", "superagent",
                  "--kind", "wish", "--trigger", "track gift ideas", "--context", "chat aside",
                  "--ts", TS])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "sig-2026-10-08-001"
    with pytest.raises(SystemExit) as info:
        sg.main(["add", "--target", "tailor", "--kind", "critique", "--trigger", "t", "--context", "c"])
    assert info.value.code == 2
