# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/heal.py`: the sync-conflict sweep, stray log rows, the sync-settings check.

A conflict copy is what a folder-sync tool saves when two machines changed one
file before a sync: `<stem>.sync-conflict-<date>-<time>-<device>.<ext>`. The
sweep merges append-only logs by itself and reports everything else.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from superagent.tools import heal, machines
from superagent.tools import signals as sg

MARK = "sync-conflict-20261008-101010-ABCDEFG"
TS = "2026-10-08T10:00:00-07:00"


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "root" / "workspace"
    (ws / "_memory").mkdir(parents=True)
    return ws


def _copy_of(path: Path) -> Path:
    """The conflict-copy path of `path`."""
    return path.with_name(f"{path.stem}.{MARK}{path.suffix}")


def _signal(sid: str, status: str = "captured", **over: object) -> dict:
    row = {"id": sid, "captured_at": TS, "target": "tailor", "kind": "wish",
           "trigger_phrase": f"phrase {sid}", "context": "c", "status": status, "processed_at": None,
           "notes": None}
    row.update(over)
    return row


def _signals_text(rows: list[dict], last_updated: str = TS) -> str:
    head = f"# header comment stays\nschema_version: 1\nlast_updated: '{last_updated}'\nsignals:\n"
    return head + "".join(sg._render_row(r, 2) and "".join(sg._render_row(r, 2)) for r in rows)


def _trash_files() -> list[str]:
    root = heal.tmp_dir() / "heal"
    return sorted(p.name for p in root.rglob("*") if p.is_file()) if root.exists() else []


# --------------------------------------------------------------- yaml row logs

def test_forced_conflict_on_the_signals_file_merges_by_itself(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "action-signals.yaml"
    shared = [_signal("sig-2026-10-07-001"), _signal("sig-2026-10-07-002")]
    here = [*shared, _signal("sig-2026-10-08-testbox-001")]
    there = [*shared, _signal("sig-2026-10-08-laptop-001"), _signal("sig-2026-10-08-laptop-002")]
    path.write_text(_signals_text(here))
    copy = _copy_of(path)
    copy.write_text(_signals_text(there, last_updated="2026-10-08T10:05:00-07:00"))
    before = path.read_text()

    outcomes = heal.sweep(ws)

    assert [(o.status, o.detail) for o in outcomes] == [("merged", "+2 row(s)")]
    assert not copy.exists(), "the merged copy leaves the synced tree"
    assert _trash_files() == [copy.name], "and is kept in the machine-local trash"
    text = path.read_text()
    assert text.startswith(before[: before.index("last_updated")]), "header and rows above are untouched"
    data = yaml.safe_load(text)
    assert [r["id"] for r in data["signals"]] == [
        "sig-2026-10-07-001", "sig-2026-10-07-002", "sig-2026-10-08-testbox-001",
        "sig-2026-10-08-laptop-001", "sig-2026-10-08-laptop-002"]
    assert data["last_updated"] == "2026-10-08T10:05:00-07:00", "the later of the two stamps"
    assert heal.sweep(ws) == [], "nothing left to do"


def test_merge_is_the_same_on_both_machines(tmp_path: Path) -> None:
    """Both machines see the same pair of files, so both must write the same bytes."""
    base = _signals_text([_signal("sig-1"), _signal("sig-2")])
    copy = _signals_text([_signal("sig-1"), _signal("sig-3")], last_updated="2026-10-09T00:00:00-07:00")
    first = heal.merge_yaml_rows("action-signals.yaml", base, copy, "signals", signals_rules=True)
    second = heal.merge_yaml_rows("action-signals.yaml", base, copy, "signals", signals_rules=True)
    assert first == second and first[1:] == (1, 0)
    # Merging the result with the same copy again changes nothing.
    again = heal.merge_yaml_rows("action-signals.yaml", first[0], copy, "signals", signals_rules=True)
    assert again == (first[0], 0, 0)


def test_a_signal_closed_on_one_machine_stays_closed(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "action-signals.yaml"
    closed = _signal("sig-1", status="processed", processed_at="2026-10-08T11:00:00-07:00",
                     notes="[2026-10-08] processed: became st-1")
    path.write_text(_signals_text([_signal("sig-1"), _signal("sig-2")]))
    _copy_of(path).write_text(_signals_text([closed, _signal("sig-2")]))
    outcomes = heal.sweep(ws)
    assert [(o.status, o.detail) for o in outcomes] == [("merged", "+0 row(s), 1 updated")]
    rows = yaml.safe_load(path.read_text())["signals"]
    assert rows[0] == closed and rows[1]["id"] == "sig-2"


def test_two_different_rows_under_one_id_are_not_merged(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "ingestion-log.yaml"
    path.write_text("schema_version: 1\nruns:\n  - id: ingest-20261008-001\n    source: bank\n")
    copy = _copy_of(path)
    copy.write_text("schema_version: 1\nruns:\n  - id: ingest-20261008-001\n    source: mail\n")
    before = path.read_text()
    (outcome,) = heal.sweep(ws)
    assert outcome.status == "unresolved"
    assert "1 row id(s) carry different content" in outcome.detail and "ingest-20261008-001" in outcome.detail
    assert path.read_text() == before and copy.exists(), "nothing changed; the copy stays for a hand fold"
    assert any(line.startswith(("-", "+")) for line in outcome.diff)


def test_interaction_log_files_merge_by_row(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    sg.append_interaction_log(ws, skill=None, action="note", summary="first", ts=TS)
    path = machines.interaction_log_path(ws)
    copy = _copy_of(path)
    copy.write_text(path.read_text())
    sg.append_interaction_log(ws, skill=None, action="note", summary="second, here", ts=TS)
    extra = sg.ilog_row(rid="ilog-2026-10-08-testbox-009", ts=TS, skill=None, action="note",
                        summary="third, only in the copy")
    copy.write_text(sg.edit_rows_text(copy.name, copy.read_text(), "entries", append=[extra]))
    (outcome,) = heal.sweep(ws)
    assert outcome.status == "merged" and outcome.detail == "+1 row(s)"
    assert [r["summary"] for r in sg.read_interaction_log(ws)] == [
        "first", "second, here", "third, only in the copy"]


# ------------------------------------------------------------------ line logs

def test_jsonl_logs_merge_by_line(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "todo.history.jsonl"
    a, b, c, d = (json.dumps({"ts": f"2026-10-08T10:0{i}:00-07:00", "row_id": f"t{i}"}) for i in range(4))
    path.write_text(f"{a}\n{b}\n{c}\n")
    _copy_of(path).write_text(f"{a}\n{d}\n{b}\n")
    (outcome,) = heal.sweep(ws)
    assert (outcome.status, outcome.detail) == ("merged", "+1 line(s)")
    assert path.read_text() == f"{a}\n{b}\n{c}\n{d}\n"
    nested = ws / "_memory" / "email" / "_messages.jsonl"
    nested.parent.mkdir()
    nested.write_text(f"{a}\n")
    _copy_of(nested).write_text(f"{b}\n")
    assert [o.status for o in heal.sweep(ws)] == ["merged"]
    assert nested.read_text() == f"{a}\n{b}\n"


# -------------------------------------------------------------- everything else

def test_forced_conflict_on_todo_is_reported_and_left_in_place(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "todo.yaml"
    path.write_text("schema_version: 1\ntasks:\n  - id: task-1\n    title: here\n")
    copy = _copy_of(path)
    copy.write_text("schema_version: 1\ntasks:\n  - id: task-1\n    title: there\n  - id: task-2\n")
    before = (path.read_text(), copy.read_text())
    (outcome,) = heal.sweep(ws)
    assert outcome.status == "unresolved"
    assert (outcome.only_in_copy, outcome.only_in_original) == (2, 1)
    assert outcome.device == "ABCDEFG" and outcome.when == "2026-10-08 10:10:10"
    assert outcome.diff == ["-    title: here", "+    title: there", "+  - id: task-2"]
    assert (path.read_text(), copy.read_text()) == before
    lines = heal.render([outcome])
    assert lines[0].startswith("Sync conflict to fold by hand: workspace/_memory/todo.yaml has a second version")
    assert lines[-1].startswith("Fold what is missing into the original, then delete the copy")
    assert heal.sweep_lines(ws) == [lines[0], lines[-1]], "the on-open form carries no diff"


def test_identical_copies_and_beat_copies_are_removed(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    doc = ws / "Sources" / "Bank" / "statement.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF-1.7 \xff\xfe same bytes")
    _copy_of(doc).write_bytes(doc.read_bytes())
    machines.beat(ws)
    beat = machines.beat_path(ws)
    _copy_of(beat).write_text('{"machine": "testbox", "at": "2026-10-08T09:00:00-07:00"}')
    outcomes = heal.sweep(ws)
    assert sorted(o.status for o in outcomes) == ["dropped", "identical"]
    assert not _copy_of(doc).exists() and not _copy_of(beat).exists() and doc.exists() and beat.exists()
    assert heal.render(outcomes) == ["Sync conflict copies removed (nothing in them to keep): 2."]


def test_binary_and_orphan_copies_are_reported(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    doc = ws / "Sources" / "scan.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"\xff\xfe one")
    _copy_of(doc).write_bytes(b"\xff\xfe another")
    gone = ws / "Sources" / f"gone.{MARK}.md"
    gone.write_text("the original was deleted")
    by_name = {Path(o.copy).name: o for o in heal.sweep(ws)}
    assert by_name[_copy_of(doc).name].status == "unresolved"
    assert "not text, or too large to compare" in by_name[_copy_of(doc).name].detail
    assert by_name[gone.name].status == "orphan"
    assert _copy_of(doc).exists() and gone.exists()


def test_diffs_of_sensitive_files_are_withheld(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = _ws(tmp_path)
    (ws / "_memory" / "sensitive").mkdir()
    secret = ws / "_memory" / "sensitive" / "vault.yaml"
    secret.write_text("pin: 1111\n")
    _copy_of(secret).write_text("pin: 2222\n")
    accounts = ws / "_memory" / "accounts-index.yaml"
    accounts.write_text("accounts:\n  - number: 12345\n")
    _copy_of(accounts).write_text("accounts:\n  - number: 67890\n")
    outcomes = heal.sweep(ws)
    assert all(o.sensitive and o.diff == ["(diff withheld: sensitive file)"] for o in outcomes)
    assert heal.main(["sweep", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "diff withheld" in out
    assert not any(secret_text in out for secret_text in ("1111", "2222", "12345", "67890"))


def test_dry_run_changes_nothing_and_json_lists_outcomes(tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    ws = _ws(tmp_path)
    path = ws / "_memory" / "todo.history.jsonl"
    path.write_text('{"a": 1}\n')
    copy = _copy_of(path)
    copy.write_text('{"a": 2}\n')
    assert heal.main(["sweep", "--workspace", str(ws), "--dry-run"]) == 0
    assert "would be merged" in capsys.readouterr().out
    assert path.read_text() == '{"a": 1}\n' and copy.exists() and _trash_files() == []
    assert heal.main(["sweep", "--workspace", str(ws), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "merged"


def test_copies_outside_the_workspace_are_reported_never_merged(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    tasks = ws.parent / "tasks" / "2026" / "10"
    tasks.mkdir(parents=True)
    (tasks / "notes.jsonl").write_text('{"a": 1}\n')
    (tasks / f"notes.{MARK}.jsonl").write_text('{"a": 2}\n')
    hidden = ws / ".stversions" / f"old.{MARK}.yaml"
    hidden.parent.mkdir()
    hidden.write_text("x: 1\n")
    outcomes = heal.sweep(ws, extra_roots=[ws.parent / "tasks"])
    assert [(o.copy, o.status) for o in outcomes] == [(f"tasks/2026/10/notes.{MARK}.jsonl", "unresolved")]
    assert (tasks / "notes.jsonl").read_text() == '{"a": 1}\n'


# ---------------------------------------------------------------------- strays

def _frozen_ws(tmp_path: Path) -> Path:
    ws = _ws(tmp_path)
    legacy = ws / "_memory" / "interaction-log.yaml"
    legacy.write_text(
        "# old single file\nschema_version: 1\nentries:\n"
        "  - id: ilog-2026-10-01-001\n    ts: '2026-10-01T09:00:00-07:00'\n    skill: null\n"
        "    action: note\n    summary: history\n")
    folder = ws / "_memory" / "interaction-log"
    folder.mkdir()
    (folder / "_frozen.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "interaction_log": {"file": "interaction-log.yaml", "rows": 1, "size": legacy.stat().st_size},
    }))
    return ws


def test_rows_appended_to_the_frozen_log_are_flagged_and_moved(tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    ws = _frozen_ws(tmp_path)
    legacy = ws / "_memory" / "interaction-log.yaml"
    frozen_text = legacy.read_text()
    assert heal.stray_lines(ws) == []
    stray = sg.ilog_row(rid="ilog-2026-10-08-003", ts=TS, skill=None, action="note",
                        summary="appended by hand to the old file")
    legacy.write_text(sg.edit_rows_text(legacy.name, frozen_text, "entries", append=[stray]))
    (line,) = heal.stray_lines(ws)
    assert line.startswith("1 row(s) were appended to interaction-log.yaml") and "ilog-2026-10-08-003" in line
    assert heal.main(["strays", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip() == line
    assert heal.main(["strays", "--workspace", str(ws), "--move"]) == 0
    assert capsys.readouterr().out.strip() == (
        "Moved 1 row(s) from interaction-log.yaml to _memory/interaction-log/testbox.yaml.")
    assert legacy.read_text() == frozen_text, "the old file is back to what was frozen"
    moved = yaml.safe_load(machines.interaction_log_path(ws).read_text())["entries"]
    assert moved == [stray]
    assert heal.stray_lines(ws) == [] and heal.move_strays(ws) == 0


def test_no_marker_means_no_strays(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    (ws / "_memory" / "interaction-log.yaml").write_text("schema_version: 1\nentries:\n  - id: a\n")
    assert heal.stray_rows(ws) == ([], [])


# ------------------------------------------------------------------ sync-check

def _sync_config(tmp_path: Path, folder: Path, *, max_conflicts: str, versioning: str) -> Path:
    config = tmp_path / "config.xml"
    config.write_text(
        '<configuration version="37">\n'
        f'  <folder id="other" label="Other" path="{tmp_path / "elsewhere"}" type="sendreceive">\n'
        '    <maxConflicts>10</maxConflicts>\n  </folder>\n'
        f'  <folder id="mine" label="Mine" path="{folder}" type="sendreceive">\n'
        f'    <versioning type="{versioning}"></versioning>\n'
        f'    <maxConflicts>{max_conflicts}</maxConflicts>\n  </folder>\n'
        '  <gui><apikey>NEVER-PRINTED</apikey></gui>\n'
        '</configuration>\n')
    return config


def test_sync_check_names_the_unsafe_settings(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    root = ws.parent
    (root / ".stignore").write_text("(?d).DS_Store\n// a comment\n(?d)**/.git\n")
    config = _sync_config(tmp_path, root, max_conflicts="10", versioning="")
    found = heal.sync_settings(ws, config)
    assert found == {"root": str(root.resolve()), "max_conflicts": 10, "versioning": "",
                     "git_ignore": "delete-flag"}
    findings = heal.sync_findings(ws, config)
    assert len(findings) == 3
    assert "set `maxConflicts` to -1" in findings[0] and "file versioning is off" in findings[1]
    assert "delete flag" in findings[2]
    assert "NEVER-PRINTED" not in " ".join(findings)


def test_sync_check_is_quiet_when_settings_are_safe_or_folder_is_not_synced(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    root = ws.parent
    (root / ".stignore").write_text("(?d).DS_Store\n**/.git\n")
    assert heal.sync_findings(ws, _sync_config(tmp_path, root, max_conflicts="-1", versioning="staggered")) == []
    (root / ".stignore").write_text("(?d).DS_Store\n")
    (finding,) = heal.sync_findings(ws, _sync_config(tmp_path, root, max_conflicts="-1", versioning="simple"))
    assert "`.git` is not ignored" in finding
    unsynced = tmp_path / "plain" / "workspace"
    unsynced.mkdir(parents=True)
    assert heal.sync_findings(unsynced, _sync_config(tmp_path, root, max_conflicts="10", versioning="")) == []
    assert heal.sync_settings(ws, tmp_path / "no-such-config.xml") is None
