# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/cadence.py`: run markers, overdue detection, the on-open nag."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import cadence as cd

PDT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 10, 8, 9, 0, tzinfo=PDT)
AUTO = {"weekly_review_auto": True, "monthly_review_auto": True,
        "cadences": {"tailor_review": "every 90 days", "doctor": "every 180 days"}}


def _ctx_path(ws: Path) -> Path:
    return ws / "_memory" / "context.yaml"


def _ctx(ws: Path) -> dict[str, Any]:
    return yaml.safe_load(_ctx_path(ws).read_text())


def _ago(days: float = 0, hours: float = 0, now: dt.datetime = NOW) -> str:
    return (now - dt.timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


# ------------------------------------------------------------------- markers

def test_start_then_finish_touch_only_their_lines(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    original = _ctx_path(ws).read_text()
    assert cd.mark(ws, "daily-update", phase="start", now=NOW) == "daily_update_started"
    started = _ctx_path(ws).read_text()
    added = [ln for ln in started.splitlines() if ln not in original.splitlines()]
    assert added == [f"  daily_update_started: '{NOW.isoformat()}'"]
    assert "# most recent completed" in started, "template comments survive"
    assert cd.unfinished_runs(_ctx(ws)["last_runs"])[0]["cadence"] == "daily-update"

    later = NOW + dt.timedelta(minutes=20)
    assert cd.mark(ws, "daily-update", phase="finish", now=later) == "daily_update"
    finished = _ctx_path(ws).read_text()
    assert "daily_update_started" not in finished
    assert finished == original.replace("  daily_update: null\n", f"  daily_update: '{later.isoformat()}'\n")
    assert cd.unfinished_runs(_ctx(ws)["last_runs"]) == []


def test_a_completed_stamp_newer_than_the_marker_means_finished() -> None:
    runs = {"daily_update": _ago(hours=1), "daily_update_started": _ago(hours=2),
            "weekly_review": _ago(days=9), "weekly_review_started": _ago(days=1),
            "tailor_review_started": _ago(days=2)}
    found = {u["cadence"]: u for u in cd.unfinished_runs(runs)}
    assert set(found) == {"weekly-review", "supertailor-review"}
    assert found["supertailor-review"]["last_completed"] is None


def test_mark_rejects_unknown_skills_and_refuses_a_broken_file(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    with pytest.raises(ValueError, match="not a cadence skill"):
        cd.mark(ws, "todo", phase="start", now=NOW)
    with pytest.raises(SystemExit) as info:
        cd.main(["start", "todo", "--workspace", str(ws)])
    assert info.value.code == 2
    broken = "schema_version: 1\nlast_runs:\n  daily_update: [\n"
    _ctx_path(ws).write_text(broken)
    with pytest.raises(cd.CorruptFileError, match="refusing to rewrite"):
        cd.mark(ws, "daily-update", phase="start", now=NOW)
    assert cd.main(["finish", "daily-update", "--workspace", str(ws)]) == 1
    assert _ctx_path(ws).read_text() == broken


def test_mark_creates_last_runs_when_absent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "_memory").mkdir(parents=True)
    _ctx_path(ws).write_text("schema_version: 1\nlast_check: null\n")
    cd.mark(ws, "weekly-review", phase="finish", now=NOW)
    assert _ctx(ws) == {"schema_version": 1, "last_check": None,
                        "last_runs": {"weekly_review": NOW.isoformat()}}


# --------------------------------------------------------- overdue cadences

@pytest.mark.parametrize("runs, prefs, since, expected", [
    ({"weekly_review": _ago(8)}, AUTO, None, {"weekly-review"}),
    ({"weekly_review": _ago(6)}, AUTO, None, set()),
    ({"weekly_review": _ago(8)}, {**AUTO, "weekly_review_auto": False}, None, set()),
    ({"monthly_review": "2026-09-07T00:45:00-07:00"}, AUTO, None, {"monthly-review"}),
    ({"monthly_review": "2026-10-02T09:00:00-07:00"}, AUTO, None, set()),
    ({}, AUTO, NOW - dt.timedelta(days=200), {"doctor"}),
    ({}, AUTO, NOW - dt.timedelta(days=100), set()),
    ({"doctor": _ago(100)}, AUTO, NOW - dt.timedelta(days=400), set()),
    ({"doctor": _ago(100)}, {**AUTO, "cadences": {"doctor": "every 60 days"}},
     NOW - dt.timedelta(days=400), {"doctor"}),
    ({"tailor_review": _ago(30)}, {**AUTO, "cadences": {"tailor_review": "every 20 days"}}, None,
     {"supertailor-review"}),
])
def test_overdue_cadence_rules(runs: dict, prefs: dict, since: dt.datetime | None, expected: set) -> None:
    base = {"weekly_review": _ago(1), "monthly_review": _ago(2), "tailor_review": _ago(10)}
    found = {c["cadence"] for c in cd.overdue_cadences({**base, **runs}, prefs, NOW, since)}
    assert found == expected


def test_monthly_is_not_due_on_the_first() -> None:
    first = dt.datetime(2026, 10, 1, 9, 0, tzinfo=PDT)
    runs = {"weekly_review": _ago(1, now=first), "monthly_review": "2026-09-01T09:00:00-07:00",
            "tailor_review": _ago(1, now=first)}
    assert cd.overdue_cadences(runs, AUTO, first, None) == []


# ---------------------------------------------------------- overdue watchers

def _ref(ws: Path, wid: str, watch: dict[str, Any]) -> Path:
    target = ws / "watched.txt"
    target.write_text("v1")
    path = ws / "Sources" / "Watchlist" / f"{wid}.ref.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"ref_version": 2, "title": wid, "watch": {"type": "path", "path": str(target), **watch}}
    path.write_text(f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n# Notes\n")
    return path


def _state(ws: Path, rows: dict[str, dict[str, Any]]) -> None:
    full = {wid: {"status": "active", "error_streak": 0, **row} for wid, row in rows.items()}
    (ws / "_memory" / "watchlist-state.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "last_updated": None, "watchers": full}))


def test_overdue_watchers_are_healthy_rows_past_period_plus_grace(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    now = cd.now_local()
    for wid, watch in {
        "fresh": {"schedule": "daily"}, "late": {"schedule": "daily"},
        "weekly-ok": {"schedule": "weekly"}, "weekly-late": {"schedule": "weekly"},
        "manual": {"schedule": "manual"}, "failing": {"schedule": "daily"},
        "evicted": {"schedule": "daily"}, "off": {"schedule": "daily", "enabled": False},
        "new": {"schedule": "daily"}, "never": {"schedule": "daily"},
    }.items():
        _ref(ws, wid, watch)
    old = (now - dt.timedelta(days=3)).timestamp()
    os.utime(ws / "Sources" / "Watchlist" / "never.ref.md", (old, old))
    _state(ws, {
        "fresh": {"last_success": _ago(1, 12, now=now)},
        "late": {"last_success": _ago(3, now=now)},
        "weekly-ok": {"last_success": _ago(6, now=now)},
        "weekly-late": {"last_success": _ago(9, now=now)},
        "manual": {"last_success": None},
        "failing": {"last_success": _ago(9, now=now), "error_streak": 2},
        "evicted": {"last_success": _ago(30, now=now), "status": "evicted"},
        "off": {"last_success": _ago(30, now=now)},
        "new": {"last_success": None},
        "never": {"last_success": None},
    })
    found = {w["watcher"]: w for w in cd.overdue_watchers(ws, now)}
    assert set(found) == {"late", "weekly-late", "never"}
    assert found["late"]["cycle"] == "daily-update" and found["late"]["days_since"] == 3
    assert found["weekly-late"]["cycle"] == "weekly-review"
    assert found["never"]["last_success"] is None


# -------------------------------------------------------------- status / nag

def _make_current(ws: Path, now: dt.datetime) -> None:
    ctx = _ctx(ws)
    ctx["last_check"] = _ago(hours=1, now=now)
    ctx["last_runs"].update({"weekly_review": _ago(1, now=now), "monthly_review": _ago(hours=2, now=now),
                             "tailor_review": _ago(1, now=now), "doctor": _ago(1, now=now)})
    _ctx_path(ws).write_text(yaml.safe_dump(ctx, sort_keys=False))


def test_status_is_silent_when_everything_is_current(initialized_workspace: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    now = cd.now_local()
    _make_current(ws, now)
    before = _ctx_path(ws).read_text()
    report = cd.status(ws, now=now)
    assert not report["last_check_stale"] and cd.render(report) == []
    assert cd.main(["status", "--stamp", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out == ""
    assert _ctx_path(ws).read_text() == before, "nothing printed -> nothing stamped"


def test_stamp_nags_once_per_day(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace  # fresh template: last_check null, cadences never run
    original = _ctx_path(ws).read_text()
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "Context last refreshed never" in out and "Overdue cadences:" in out
    assert _ctx(ws)["refresh_suggested"] == "2026-10-08"
    changed = [ln for ln in _ctx_path(ws).read_text().splitlines() if ln not in original.splitlines()]
    assert changed == ["refresh_suggested: '2026-10-08'"], "only the bookkeeping line changes"
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert capsys.readouterr().out == "", "second open on the same day stays silent"
    tomorrow = (NOW + dt.timedelta(days=1)).isoformat()
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", tomorrow]) == 0
    assert capsys.readouterr().out != ""
    assert _ctx(ws)["refresh_suggested"] == "2026-10-09"


def test_status_reports_unfinished_runs_and_json(initialized_workspace: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    now = cd.now_local()
    _make_current(ws, now)
    cd.mark(ws, "daily-update", phase="start", now=now - dt.timedelta(hours=5))
    assert cd.main(["status", "--workspace", str(ws)]) == 0
    assert "Unfinished runs: daily-update started" in capsys.readouterr().out
    assert cd.main(["status", "--json", "--workspace", str(ws)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unfinished_runs"][0]["cadence"] == "daily-update"
    assert set(report) >= {"last_check_stale", "overdue_cadences", "overdue_watchers", "unfinished_runs"}
