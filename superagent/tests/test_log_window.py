"""Tests for `tools/log_window.py` (events stream + windowing)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path


def test_append_and_read_roundtrip(initialized_workspace: Path) -> None:
    from superagent.tools.log_window import (
        append_event, parse_iso_dt, read_window,
    )

    ev = append_event(initialized_workspace, {
        "kind": "skill_run",
        "actor": "test",
        "subject": "ran tests",
        "summary": "smoke test",
    })
    assert ev["id"].startswith("evt-")
    assert ev["kind"] == "skill_run"
    rows = list(read_window(
        initialized_workspace,
        parse_iso_dt(ev["ts"]) - dt.timedelta(minutes=1),
        parse_iso_dt(ev["ts"]) + dt.timedelta(minutes=1),
    ))
    ids = [r["id"] for r in rows]
    assert ev["id"] in ids


def test_partition_index_updated(initialized_workspace: Path) -> None:
    from superagent.tools.log_window import (
        append_event, index_path, load_yaml,
    )

    append_event(initialized_workspace, {
        "kind": "skill_run",
        "actor": "test",
        "subject": "test event",
    })
    idx = load_yaml(index_path(initialized_workspace))
    assert isinstance(idx, dict)
    partitions = idx.get("partitions") or []
    assert len(partitions) >= 1
    # Every partition has the required schema fields.
    for p in partitions:
        for field in ("quarter", "path", "event_count"):
            assert field in p


def test_quarter_for_consistent() -> None:
    from superagent.tools.log_window import quarter_for

    assert quarter_for(dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)) == "2026-Q1"
    assert quarter_for(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)) == "2026-Q2"
    assert quarter_for(dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)) == "2026-Q3"
    assert quarter_for(dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc)) == "2026-Q4"


def test_filter_by_kind(initialized_workspace: Path) -> None:
    from superagent.tools.log_window import (
        append_event, parse_iso_dt, read_window,
    )

    e1 = append_event(initialized_workspace, {
        "kind": "skill_run", "actor": "a", "subject": "skill A"})
    e2 = append_event(initialized_workspace, {
        "kind": "decision", "actor": "user", "subject": "decision B"})
    since = parse_iso_dt(e1["ts"]) - dt.timedelta(minutes=1)
    until = parse_iso_dt(e2["ts"]) + dt.timedelta(minutes=1)
    all_rows = list(read_window(initialized_workspace, since, until))
    assert {r["id"] for r in all_rows} >= {e1["id"], e2["id"]}
    decisions = [r for r in all_rows if r.get("kind") == "decision"]
    assert any(r["id"] == e2["id"] for r in decisions)
