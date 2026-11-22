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
from superagent.tools import machines

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
    with pytest.raises(ValueError, match="not a cadence skill or a duty"):
        cd.mark(ws, "todo", phase="start", now=NOW)
    assert cd.main(["start", "todo", "--workspace", str(ws)]) == 2
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
    assert _ctx_path(ws).read_text() == original, "the open check writes no shared file"
    assert machines.read_local_state(ws)["refresh_suggested"] == "2026-10-08", (
        "the marker is machine-local (~/.superagent/state/)")
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert capsys.readouterr().out == "", "second open on the same day stays silent"
    tomorrow = (NOW + dt.timedelta(days=1)).isoformat()
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", tomorrow]) == 0
    assert capsys.readouterr().out != ""
    assert machines.read_local_state(ws)["refresh_suggested"] == "2026-10-09"
    assert _ctx_path(ws).read_text() == original


def _files_under(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_open_check_writes_only_this_machines_beat(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    before = _files_under(ws)
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    after = _files_under(ws)
    assert {k for k in after if after[k] != before.get(k)} == {"_memory/machines/testbox.json"}
    beat = json.loads(after["_memory/machines/testbox.json"])
    assert beat["machine"] == "testbox" and beat["at"].startswith("2026-10-08T")


def test_open_check_says_who_else_wrote_here(initialized_workspace: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws, NOW)
    folder = ws / "_memory" / "machines"
    (folder / "laptop.json").write_text(json.dumps(
        {"machine": "laptop", "uid": "aa", "at": (NOW - dt.timedelta(minutes=12)).isoformat()}))
    (folder / "old-box.json").write_text(json.dumps(
        {"machine": "old-box", "uid": "bb", "at": (NOW - dt.timedelta(days=3)).isoformat()}))
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["laptop wrote here 12 min ago: let the sync settle and re-read a file before editing it."]
    # The presence line is not a nag: it shows on every open, also once the day's nag is spent.
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert capsys.readouterr().out.splitlines() == out
    # Plain `status` (a cadence skill asking what is late) says nothing about presence.
    assert cd.main(["status", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert capsys.readouterr().out == ""


def test_open_check_runs_the_conflict_sweep(initialized_workspace: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    _make_current(ws, NOW)
    todo = ws / "_memory" / "todo.yaml"
    copy = ws / "_memory" / "todo.sync-conflict-20261008-101010-ABCDEFG.yaml"
    copy.write_text(todo.read_text() + "# a line only the other machine has\n")
    assert cd.main(["status", "--stamp", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "Sync conflict to fold by hand: workspace/_memory/todo.yaml has a second version" in out
    assert "1 line(s) only in the copy" in out and "a line only the other machine has" not in out
    assert copy.exists(), "reported and left in place"


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


# ------------------------------------------------------ stamps for any duty

def test_any_duty_has_a_stamp(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    assert cd.stamp_key("update-light", ws) == "update_light"
    assert cd.stamp_key("updater:update-full", ws) == "update_full"
    assert cd.stamp_key("supertailor-review", ws) == "tailor_review", "the schedule key names the stamp"
    assert cd.stamp_key("bills", ws) == "bills", "an on-request duty is stamped under its own id"
    assert cd.mark(ws, "update-full", phase="start", now=NOW) == "update_full_started"
    assert cd.unfinished_runs(_ctx(ws)["last_runs"])[0]["key"] == "update_full"
    assert cd.mark(ws, "updater:update-full", phase="finish", now=NOW) == "update_full"
    assert _ctx(ws)["last_runs"]["update_full"] == NOW.isoformat()
    assert "update_full_started" not in _ctx(ws)["last_runs"]
    with pytest.raises(ValueError, match="not a cadence skill or a duty"):
        cd.stamp_key("superagent:update-full", ws)


def test_a_custom_roles_duty_is_stamped_and_an_ambiguous_id_needs_its_agent(
        initialized_workspace: Path) -> None:
    ws = initialized_workspace
    folder = ws / "_custom" / "agents"
    (folder / "gardener.agent.md").write_text(
        "---\nname: gardener\nkind: durable\ndescription: Waters the plants.\nskills: [todo]\n"
        "owns: []\nauthority: act\nmodel_tier: runner\nduties:\n"
        "  - {id: water, skill: todo, tier: act, schedule: on-request, check: none}\n"
        "  - {id: update-full, skill: todo, tier: act, schedule: on-request, check: none}\n---\n# Gardener\n")
    (folder / "gardener.agent.dev.md").write_text("---\ndevelops: gardener\nmodel_tier: dev\n---\n# dev\n")
    assert cd.mark(ws, "water", phase="finish", now=NOW) == "water"
    assert _ctx(ws)["last_runs"]["water"] == NOW.isoformat()
    with pytest.raises(ValueError, match="more than one role; name one of updater:update-full, gardener:update-full"):
        cd.stamp_key("update-full", ws)
    assert cd.stamp_key("gardener:update-full", ws) == "update_full"


def test_an_unfinished_run_of_a_duty_is_named_under_its_agent(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _make_current(ws, NOW)
    cd.mark(ws, "update-full", phase="start", now=NOW - dt.timedelta(hours=3))
    assert cd.render(cd.status(ws, now=NOW)) == [
        "[updater] Unfinished runs: update-full started 2026-10-08 06:00 and never finished."]


# -------------------------------------------------- schedules and due-by-stamp

@pytest.mark.parametrize("value, expected", [
    ("08:30", cd.Schedule("daily", 8, 30)),
    ("Sunday 18:00", cd.Schedule("weekly", 18, 0, weekday=6)),
    ("mon 7:05", cd.Schedule("weekly", 7, 5, weekday=0)),
    ("1st 09:00", cd.Schedule("monthly", 9, 0, day=1)),
    ("15th 09:00", cd.Schedule("monthly", 9, 0, day=15)),
    ("every 90 days", cd.Schedule("every", every=dt.timedelta(days=90))),
    ("every 30 minutes", cd.Schedule("every", every=dt.timedelta(minutes=30))),
    ("Every 6 hours", cd.Schedule("every", every=dt.timedelta(hours=6))),
    ("every 2 weeks", cd.Schedule("every", every=dt.timedelta(weeks=2))),
])
def test_schedule_values(value: str, expected: cd.Schedule) -> None:
    assert cd.parse_schedule(value) == expected


@pytest.mark.parametrize("value", [None, "", "sometimes", "25:00", "08:75", "32nd 09:00", "every 0 days",
                                   "every few days", "s 18:00", "funday 18:00"])
def test_values_that_are_no_schedule(value: object) -> None:
    assert cd.parse_schedule(value) is None


def _at(day: int, hour: int, minute: int = 0, month: int = 10) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, minute, tzinfo=PDT)


def test_latest_scheduled_time() -> None:
    daily = cd.parse_schedule("08:30")
    assert cd.latest_occurrence(daily, _at(8, 9)) == _at(8, 8, 30)
    assert cd.latest_occurrence(daily, _at(8, 8)) == _at(7, 8, 30)
    weekly = cd.parse_schedule("Sunday 18:00")  # 2026-10-04 is a Sunday
    assert cd.latest_occurrence(weekly, _at(4, 18)) == _at(4, 18)
    assert cd.latest_occurrence(weekly, _at(4, 17, 59)) == _at(27, 18, month=9)
    assert cd.latest_occurrence(weekly, _at(8, 9)) == _at(4, 18)
    monthly = cd.parse_schedule("1st 09:00")
    assert cd.latest_occurrence(monthly, _at(1, 8, 59)) == _at(1, 9, month=9)
    assert cd.latest_occurrence(monthly, _at(1, 9)) == _at(1, 9)
    last_day = cd.parse_schedule("31st 09:00")
    assert cd.latest_occurrence(last_day, _at(15, 12)) == _at(30, 9, month=9), "a short month ends early"
    assert cd.latest_occurrence(cd.parse_schedule("every 3 days"), _at(8, 9)) is None


def test_a_missed_sunday_review_is_due_on_monday() -> None:
    weekly = cd.parse_schedule("Sunday 18:00")
    ran = _at(27, 18, 20, month=9)  # the review of Sunday 2026-09-27
    assert cd.schedule_due(weekly, ran, _at(3, 12))[0] is False, "Saturday: not yet"
    assert cd.schedule_due(weekly, ran, _at(4, 18))[0] is True, "Sunday 18:00: due"
    due, why = cd.schedule_due(weekly, ran, _at(5, 9))
    assert due and why == "due since 2026-10-04 18:00 (weekly, Sunday 18:00); last run 2026-09-27", (
        "the Sunday run was missed: it is due on Monday, by its stamp")
    late_run = _at(7, 11)  # it ran late, on Wednesday
    assert cd.schedule_due(weekly, late_run, _at(10, 12))[0] is False
    assert cd.schedule_due(weekly, late_run, _at(11, 18, 5))[0] is True, "back on schedule the next Sunday"
    assert cd.schedule_due(weekly, None, _at(5, 9)) == (True, "never ran (weekly, Sunday 18:00)")


def test_a_run_that_finishes_after_its_time_is_due_again_at_the_next_one() -> None:
    daily = cd.parse_schedule("08:30")
    finished = _at(7, 8, 41)  # yesterday's run ended at 08:41
    assert cd.schedule_due(daily, finished, _at(8, 8, 29))[0] is False
    assert cd.schedule_due(daily, finished, _at(8, 8, 30))[0] is True, "no drift: due at 08:30 sharp"


def test_every_n_schedules_go_by_the_age_of_the_stamp() -> None:
    every = cd.parse_schedule("every 90 days")
    assert cd.schedule_due(every, NOW - dt.timedelta(days=89), NOW)[0] is False
    assert cd.schedule_due(every, NOW - dt.timedelta(days=90), NOW)[0] is True
    assert cd.schedule_due(every, None, NOW, since=NOW - dt.timedelta(days=30))[0] is False, (
        "a new workspace is not due at once")
    assert cd.schedule_due(every, None, NOW, since=NOW - dt.timedelta(days=100))[0] is True
    assert cd.schedule_due(every, None, NOW)[0] is True
    assert cd.schedule_due(None, None, NOW) == (False, "")


def test_due_command(initialized_workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    base = ["--workspace", str(ws), "--now", NOW.isoformat()]
    assert cd.main(["due", "update-full", *base]) == 0
    assert capsys.readouterr().out.strip() == "never ran (daily, 06:00)"
    cd.mark(ws, "update-full", phase="finish", now=NOW - dt.timedelta(hours=1))
    assert cd.main(["due", "update-full", *base]) == 1 and capsys.readouterr().out == ""
    tomorrow = (NOW + dt.timedelta(days=1)).isoformat()
    assert cd.main(["due", "update-full", "--workspace", str(ws), "--now", tomorrow]) == 0
    assert capsys.readouterr().out.startswith("due since 2026-10-09 06:00 (daily, 06:00)")
    assert cd.main(["due", "bills", *base]) == 1, "an on-request duty is never due"
    assert cd.main(["due", "nothing-like-it", *base]) == 2


def test_auto_flag_and_missing_cadence_key(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    config = ws / "_memory" / "config.yaml"
    text = config.read_text()
    assert cd.duty_due(ws, "weekly-review", now=NOW)[0] is True
    config.write_text(text.replace("weekly_review_auto: true", "weekly_review_auto: false"))
    assert cd.duty_due(ws, "weekly-review", now=NOW) == (False, ""), "switched off by its auto flag"
    # A workspace whose config predates a cadence key takes the template's value.
    lines = [ln for ln in text.splitlines(keepends=True) if "update_full:" not in ln]
    config.write_text("".join(lines))
    assert "update_full" not in yaml.safe_load(config.read_text())["preferences"]["cadences"]
    assert cd.cadence_values(ws)["update_full"] == "06:00"
    assert cd.duty_due(ws, "update-full", now=NOW)[0] is True
