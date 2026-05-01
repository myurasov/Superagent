"""Tests for `tools/log_summarize.py` and `tools/snapshot_diff.py`."""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import yaml


def test_log_summarize_interaction_log(initialized_workspace: Path) -> None:
    from superagent.tools.log_summarize import summarize_one

    log_path = initialized_workspace / "_memory" / "interaction-log.yaml"
    data = yaml.safe_load(log_path.read_text()) or {}
    now = dt.datetime.now().astimezone().isoformat()
    data.setdefault("entries", []).extend([
        {"timestamp": now, "type": "skill_run", "subject": "ran daily-update",
         "summary": "morning briefing"},
        {"timestamp": now, "type": "email", "subject": "from a friend",
         "summary": "small talk"},
    ])
    log_path.write_text(yaml.safe_dump(data, sort_keys=False))
    out = summarize_one(log_path, "entries")
    assert out.exists()
    summary = yaml.safe_load(out.read_text())
    assert summary["total_rows"] >= 2
    assert summary["last_30_days"]["count"] >= 2
    assert "skill_run" in summary["last_30_days"]["by_kind"]


def test_snapshot_diff_detects_added_row(initialized_workspace: Path,
                                          tmp_path: Path) -> None:
    from superagent.tools.snapshot_diff import diff_files

    snap_a = tmp_path / "snap_a"
    snap_b = tmp_path / "snap_b"
    snap_a.mkdir()
    snap_b.mkdir()
    # Snap A: empty contacts.
    (snap_a / "contacts.yaml").write_text(
        "schema_version: 1\ncontacts: []\n"
    )
    (snap_b / "contacts.yaml").write_text(
        "schema_version: 1\ncontacts:\n  - id: alice\n    name: Alice\n"
    )
    diffs = diff_files(snap_a, snap_b)
    assert len(diffs) == 1
    assert diffs[0]["change"] == "modified"
    assert any("contacts:alice" == k for k in diffs[0]["added"])
