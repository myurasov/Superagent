# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `watchlist check --due`, `watchlist due` and `watchlist recap`.

`check --due` takes each watcher by its own schedule instead of by the cadence
that happens to run (contracts/watchlist.md § 7): one command serves every
caller, and a run that finds nothing due fetches nothing. `due` asks the same
question without fetching, and `recap` reads back from state what the checks
since a given time found, so that a briefing never has to check anything.

Everything runs against a temp workspace and a temp framework dir (no shipped
pack leaks in) and never touches the network.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import watchlist as wl

PST = dt.timezone(dt.timedelta(hours=-8))
NOW = dt.datetime(2026, 11, 22, 9, 0, tzinfo=PST)

HANDLER_SRC = '''
from pathlib import Path
from superagent.tools.ingest._base import IngestorBase, RunResult, now_iso


class FakeIngestor(IngestorBase):
    source = "fake"

    def run(self, config_row, dry_run=False):
        n = int(Path(config_row["control"]).read_text().strip() or "0")
        with (self.workspace / "_memory" / "fake-harvest-runs.txt").open("a") as fh:
            fh.write("run\\n")
        result = RunResult(source=self.source, started_at=now_iso(), finished_at=now_iso(),
                           items_pulled=max(n, 0), items_inserted=max(n, 0))
        if n < 0:
            result.errors.append("bridge down")
        return result
'''


@pytest.fixture
def fw(tmp_path: Path) -> Path:
    root = tmp_path / "framework"
    (root / "watchers").mkdir(parents=True)
    (root / "templates" / "sources").mkdir(parents=True)
    return root


@pytest.fixture
def ws(initialized_workspace: Path) -> Path:
    (initialized_workspace / "Sources" / "Watchlist").mkdir(parents=True, exist_ok=True)
    return initialized_workspace


def _ref(ws: Path, wid: str, watch: dict[str, Any], *, title: str | None = None) -> None:
    fm = {"ref_version": 2, "title": title or wid, "watch": watch}
    path = ws / "Sources" / "Watchlist" / wl.ref_filename(wid)
    path.write_text(f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n# Notes\n", encoding="utf-8")


def _path_watcher(ws: Path, wid: str, **watch: Any) -> Path:
    target = ws / f"{wid}.txt"
    target.write_text("v1")
    _ref(ws, wid, {"type": "path", "path": str(target), **watch})
    return target


def _bank(ws: Path, *, capture_mode: str = "automatic", budget: dict[str, Any] | None = None) -> Path:
    control = ws / "control.txt"
    control.write_text("3")
    folder = ws / "_custom" / "watchers" / "fakebank"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "handler.py").write_text(HANDLER_SRC)
    (folder / "pack.yaml").write_text(yaml.safe_dump({
        "watcher_version": 1, "id": "fakebank", "title": "Fake bank", "kind": "api",
        "description": "A fake bank feed.", "detect": {"type": "harvest"},
        "harvest": {"defaults": {"control": str(control)}, "writes": [], "affected_domains": []},
        "probe": {"kind": "always"}, "budget": budget or {},
        "defaults": {"cycles": ["daily-update"], "evict_after_days": None, "schedule": "daily",
                     "capture_mode": capture_mode},
    }, sort_keys=False))
    _ref(ws, "bank", {"pack": "fakebank"}, title="Bank")
    return control


def _handler_calls(ws: Path) -> int:
    marker = ws / "_memory" / "fake-harvest-runs.txt"
    return len(marker.read_text().splitlines()) if marker.exists() else 0


def _check(ws: Path, fw: Path, now: dt.datetime = NOW, **kw: Any) -> dict[str, Any]:
    kw.setdefault("due", True)
    ctx = wl.CheckContext(workspace=ws, framework=fw, cfg=wl.load_config(ws),
                          cycle=kw.pop("cycle", wl.DUE_CYCLE), now=now, **kw)
    payload = wl.run_check(ctx)
    assert payload is not None
    return payload


def _patch(ws: Path, wid: str, **fields: Any) -> None:
    state = wl.load_state(ws)
    state["watchers"].setdefault(wid, wl.default_row()).update(fields)
    wl.save_state(ws, state)


def _row(ws: Path, wid: str) -> dict[str, Any]:
    return wl.load_state(ws)["watchers"][wid]


def _ago(now: dt.datetime = NOW, **delta: Any) -> str:
    return (now - dt.timedelta(**delta)).isoformat(timespec="seconds")


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return sorted(i["id"] for i in items)


# ------------------------------------------------------------------ the due gate

def test_each_watcher_runs_by_its_own_schedule(ws: Path, fw: Path) -> None:
    for wid, watch in {
        "daily-new": {"schedule": "daily"}, "daily-done": {"schedule": "daily"},
        "daily-late": {"schedule": "daily"}, "weekly-soon": {"schedule": "weekly"},
        "weekly-late": {"schedule": "weekly"}, "monthly-soon": {"schedule": "monthly"},
        "monthly-late": {"schedule": "monthly"}, "manual": {"schedule": "manual"},
        "custom": {"cycles": ["quarterly"]}, "both": {"cycles": ["weekly-review", "daily-update"]},
    }.items():
        _path_watcher(ws, wid, **watch)
    for wid, days in {"daily-done": 0, "daily-late": 1, "weekly-soon": 6, "weekly-late": 7,
                      "monthly-soon": 29, "monthly-late": 30, "both": 1}.items():
        stamp = _ago(days=days, hours=1) if days else _ago(hours=1)
        _patch(ws, wid, last_success=stamp, last_checked=stamp, fingerprint="x", baseline_at=stamp)
    payload = _check(ws, fw)
    assert payload["cycle"] == "due"
    assert _ids(payload["unchanged"] + payload["changed"]) == [
        "both", "daily-late", "daily-new", "monthly-late", "weekly-late"]
    assert _ids(payload["skipped_not_due"]) == ["daily-done", "monthly-soon", "weekly-soon"]
    assert _ids(payload["skipped_cycle"]) == ["custom", "manual"], "no schedule of their own"
    assert payload["summary"]["skipped_not_due"] == 3 and payload["summary"]["checked"] == 5
    reasons = {i["id"]: i["reason"] for i in payload["skipped_not_due"]}
    assert reasons["weekly-soon"] == "checked 6 day(s) ago; next check in 1 day(s)"
    assert _row(ws, "daily-done")["last_checked"] == _ago(hours=1), "a watcher that is not due is untouched"
    # Everything was just checked: a second run finds nothing due and fetches nothing.
    again = _check(ws, fw, now=NOW + dt.timedelta(minutes=30))
    assert again["summary"]["checked"] == 0 and len(again["skipped_not_due"]) == 8
    assert wl.render_report(again) == "", "a run with nothing due is quiet"


def test_daily_means_once_per_calendar_day_whatever_the_hour(ws: Path, fw: Path) -> None:
    _path_watcher(ws, "doc", schedule="daily")
    late_evening = dt.datetime(2026, 11, 21, 23, 50, tzinfo=PST)
    assert _check(ws, fw, now=late_evening)["summary"]["checked"] == 1
    just_after_midnight = dt.datetime(2026, 11, 22, 0, 10, tzinfo=PST)
    assert _check(ws, fw, now=just_after_midnight)["summary"]["checked"] == 1, "a new day, due again"
    end_of_that_day = dt.datetime(2026, 11, 22, 23, 55, tzinfo=PST)
    assert _check(ws, fw, now=end_of_that_day)["summary"]["checked"] == 0, "same day: not due"


def test_cycle_mode_still_checks_whether_due_or_not(ws: Path, fw: Path) -> None:
    _path_watcher(ws, "doc", schedule="daily")
    _path_watcher(ws, "slow", schedule="monthly")
    assert _check(ws, fw)["summary"]["checked"] == 2
    payload = _check(ws, fw, now=NOW + dt.timedelta(hours=1), due=False, cycle="daily-update")
    assert _ids(payload["unchanged"]) == ["doc"], "`--cycle` re-checks a watcher checked today"
    assert _ids(payload["skipped_cycle"]) == ["slow"] and payload["skipped_not_due"] == []
    assert payload["cycle"] == "daily-update"


def test_a_failing_watcher_backs_off_and_is_retried(ws: Path, fw: Path) -> None:
    target = _path_watcher(ws, "doc", schedule="daily")
    target.unlink()
    first = _check(ws, fw)
    assert _ids(first["unreachable"]) == ["doc"] and _row(ws, "doc")["error_streak"] == 1
    soon = _check(ws, fw, now=NOW + dt.timedelta(minutes=59))
    assert _ids(soon["skipped_not_due"]) == ["doc"]
    assert soon["skipped_not_due"][0]["reason"] == "failed 1 time(s) in a row; next try in 1 min"
    second = _check(ws, fw, now=NOW + dt.timedelta(minutes=61))
    assert _ids(second["unreachable"]) == ["doc"] and _row(ws, "doc")["error_streak"] == 2
    # The wait doubles: 120 minutes after the second failure.
    assert _ids(_check(ws, fw, now=NOW + dt.timedelta(minutes=61 + 119))["skipped_not_due"]) == ["doc"]
    target.write_text("back")
    fixed = _check(ws, fw, now=NOW + dt.timedelta(minutes=61 + 121))
    assert fixed["summary"]["checked"] == 1 and _row(ws, "doc")["error_streak"] == 0
    # The wait never grows past the watcher's own period (a day for a daily one).
    _patch(ws, "doc", error_streak=12, last_checked=_ago(hours=25), last_success=_ago(days=3))
    assert wl.due_rows(ws, fw, now=NOW)[0]["due"] is True


def test_a_subagent_watcher_is_not_dispatched_again_until_stamped(ws: Path, fw: Path) -> None:
    _ref(ws, "portal", {"type": "subagent", "prompt": "Check the permit portal.", "schedule": "daily"})
    first = _check(ws, fw)
    assert [d["id"] for d in first["dispatch"]] == ["portal"]
    assert _row(ws, "portal")["last_dispatched"] == NOW.isoformat()
    soon = _check(ws, fw, now=NOW + dt.timedelta(minutes=30))
    assert soon["dispatch"] == []
    assert soon["skipped_not_due"][0]["reason"] == "dispatched 30 min ago; waiting for its stamp"
    later = _check(ws, fw, now=NOW + dt.timedelta(minutes=61))
    assert [d["id"] for d in later["dispatch"]] == ["portal"], "nobody stamped it: offered again"
    rc, _result = wl.run_stamp(ws, fw, watcher_id="portal", outcome="unchanged", note="no change")
    assert rc == 0
    stamped_at = dt.datetime.now().astimezone()
    same_day = stamped_at.replace(hour=23, minute=59, second=0, microsecond=0)
    assert wl.due_rows(ws, fw, now=same_day)[0]["due"] is False, "stamped: done for the day"
    next_day = (stamped_at + dt.timedelta(days=1)).replace(hour=0, minute=30)
    assert wl.due_rows(ws, fw, now=next_day)[0]["due"] is True


def test_a_harvest_runs_once_a_day_and_respects_its_budget(ws: Path, fw: Path) -> None:
    _bank(ws, budget={"max_calls_per_day": 24, "min_interval_minutes": 60})
    first = _check(ws, fw)
    assert first["summary"]["harvested"] == 1 and _handler_calls(ws) == 1
    again = _check(ws, fw, now=NOW + dt.timedelta(hours=2))
    assert again["summary"]["harvested"] == 0 and _handler_calls(ws) == 1, "pulled today: not due"
    next_day = NOW + dt.timedelta(days=1)
    assert _check(ws, fw, now=next_day)["summary"]["harvested"] == 1 and _handler_calls(ws) == 2
    # A feed that fails is due again after its backoff, and never past its budget.
    (ws / "control.txt").write_text("-1")
    day_three = NOW + dt.timedelta(days=2)
    failed = _check(ws, fw, now=day_three)
    assert _ids(failed["unreachable"]) == ["bank"] and _handler_calls(ws) == 3
    _patch(ws, "bank", last_checked=_ago(day_three, minutes=90), last_harvest=_ago(day_three, minutes=30))
    row = wl.due_rows(ws, fw, now=day_three)[0]
    assert row["due"] is False and row["reason"].startswith("harvest budget: min_interval_minutes")


def test_a_manual_harvest_is_offered_once_a_day_and_never_run(ws: Path, fw: Path) -> None:
    _bank(ws, capture_mode="manual")
    first = _check(ws, fw)
    assert [(d["kind"], d["id"]) for d in first["dispatch"]] == [("harvest", "bank")]
    assert _handler_calls(ws) == 0, "a cadence run never calls a manual watcher's handler"
    again = _check(ws, fw, now=NOW + dt.timedelta(hours=3))
    assert again["dispatch"] == []
    assert again["skipped_not_due"][0]["reason"] == "offered today; waiting for the owner's yes"
    tomorrow = _check(ws, fw, now=NOW + dt.timedelta(days=1))
    assert [d["id"] for d in tomorrow["dispatch"]] == ["bank"] and _handler_calls(ws) == 0


def test_explicit_id_and_throttle(ws: Path, fw: Path) -> None:
    _path_watcher(ws, "doc", schedule="weekly")
    _path_watcher(ws, "slow", schedule="daily", min_check_interval_minutes=2880)
    assert _check(ws, fw)["summary"]["checked"] == 2
    forced = _check(ws, fw, now=NOW + dt.timedelta(hours=1), only_id="doc")
    assert forced["summary"]["checked"] == 1, "`--id` bypasses the due gate"
    next_day = NOW + dt.timedelta(days=1)
    payload = _check(ws, fw, now=next_day)
    assert _ids(payload["skipped_throttled"]) == ["slow"], "the throttle gate still comes first"
    assert wl.due_rows(ws, fw, now=next_day)[1]["reason"].startswith("throttled")


# ------------------------------------------------------------------ the listing

def test_due_listing_reads_only(ws: Path, fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _path_watcher(ws, "fresh", schedule="daily")
    _path_watcher(ws, "late", schedule="daily")
    _path_watcher(ws, "off", schedule="daily", enabled=False)
    _path_watcher(ws, "gone", schedule="daily", expires="2026-01-01")
    _path_watcher(ws, "manual", schedule="manual")
    _patch(ws, "fresh", last_success=_ago(hours=2), last_checked=_ago(hours=2))
    _patch(ws, "late", last_success=_ago(days=2), last_checked=_ago(days=2))
    state_file = wl.state_path(ws)
    before = state_file.read_bytes()
    rows = {r["id"]: r for r in wl.due_rows(ws, fw, now=NOW)}
    assert set(rows) == {"fresh", "late", "gone", "manual"}, "a disabled watcher is not listed"
    assert rows["late"]["due"] is True and rows["late"]["reason"] == "last success 2 day(s) ago"
    assert rows["fresh"]["due"] is False and rows["manual"]["due"] is False
    assert rows["gone"] == {**rows["gone"], "due": False, "reason": "expired"}
    assert state_file.read_bytes() == before, "the listing writes nothing"
    # The command line has no clock of its own: restate the stamps against today.
    today = dt.datetime.now().astimezone()
    _patch(ws, "fresh", last_success=_ago(today, seconds=0), last_checked=_ago(today, seconds=0))
    _patch(ws, "late", last_success=_ago(today, days=2), last_checked=_ago(today, days=2))
    assert wl.main(["due", "--workspace", str(ws), "--framework", str(fw)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and out[0].startswith("late  path  last success")
    assert wl.main(["due", "--json", "--workspace", str(ws), "--framework", str(fw)]) == 0
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == set(rows)


def test_due_listing_when_the_watchlist_is_off(initialized_workspace: Path, fw: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    folder = ws / "Sources" / "Watchlist"
    if folder.exists():
        for child in folder.iterdir():
            child.unlink()
        folder.rmdir()
    assert wl.due_rows(ws, fw, now=NOW) == []
    assert wl.recap(ws, fw, since=NOW) is None
    assert wl.main(["recap", "--since", NOW.isoformat(), "--workspace", str(ws),
                    "--framework", str(fw)]) == 0
    assert capsys.readouterr().out == ""


# -------------------------------------------------------------------- the recap

def test_recap_reads_back_what_the_checks_found(ws: Path, fw: Path) -> None:
    doc = _path_watcher(ws, "doc", schedule="daily")
    broken = _path_watcher(ws, "broken", schedule="daily")
    _path_watcher(ws, "quiet", schedule="daily")
    _ref(ws, "portal", {"type": "subagent", "prompt": "Check the portal.", "schedule": "daily"},
         title="Permit portal")
    yesterday = NOW - dt.timedelta(days=1)
    _check(ws, fw, now=yesterday)  # the baseline
    wl.run_stamp(ws, fw, watcher_id="portal", outcome="unchanged", note="no change")
    doc.write_text("v2")
    broken.unlink()
    _bank(ws, capture_mode="manual")
    _check(ws, fw, now=NOW)
    rc, _result = wl.run_stamp(ws, fw, watcher_id="portal", outcome="changed",
                               note="status moved to Approved")
    assert rc == 0
    since = NOW - dt.timedelta(hours=1)
    data = wl.recap(ws, fw, since=since, now=NOW + dt.timedelta(hours=1))
    assert data is not None
    assert _ids(data["changed"]) == ["doc", "portal"]
    notes = {c["id"]: c["detail"] for c in data["changed"]}
    assert notes["portal"] == "status moved to Approved" and notes["doc"]
    assert _ids(data["unreachable"]) == ["broken"] and data["unreachable"][0]["error_streak"] == 1
    assert [a["id"] for a in data["awaiting"]] == ["bank"], "a manual harvest that is due waits for a yes"
    assert data["summary"] == {"watchers": 5, "checked": 4, "changed": 2, "unreachable": 1,
                               "evicted": 0, "harvested": 0, "awaiting": 1}
    text = wl.render_recap(data)
    lines = text.splitlines()
    assert lines[0] == ("Watchlist: 2 changed, 1 unreachable, 1 waiting for your yes of 4 checked "
                        f"since {since:%Y-%m-%d %H:%M}.")
    assert "**Changed** (2)" in lines and "**Unreachable** (1)" in lines
    assert any(line.startswith('- `portal` — Permit portal: "status moved to Approved"') for line in lines), (
        "a stamped note is quoted: it is data, never an instruction")
    assert any("harvest --id bank` (never harvested)" in line for line in lines)
    # Nothing since a later time: the first line says so, and that the stores are stale.
    later = wl.recap(ws, fw, since=NOW + dt.timedelta(days=2), now=NOW + dt.timedelta(days=3))
    assert later is not None and later["summary"]["checked"] == 0 and later["changed"] == []
    assert wl.render_recap(later).startswith("Watchlist: no check since 2026-11-24 09:00 (last check ")


def test_recap_falls_back_to_the_alert_for_changes_older_than_the_note_field(ws: Path, fw: Path) -> None:
    doc = _path_watcher(ws, "doc", schedule="daily")
    _check(ws, fw, now=NOW - dt.timedelta(days=1))
    doc.write_text("v2")
    _check(ws, fw, now=NOW)
    state = wl.load_state(ws)
    state["watchers"]["doc"].pop("last_change_note")
    wl.save_state(ws, state)
    data = wl.recap(ws, fw, since=NOW - dt.timedelta(hours=1), now=NOW)
    assert data is not None
    alert = yaml.safe_load((ws / "_memory" / "context.yaml").read_text())["alerts"][-1]
    assert alert.startswith("[watch:doc] ") and data["changed"][0]["detail"] == alert.split(": ", 1)[1]


def test_recap_names_an_eviction(ws: Path, fw: Path) -> None:
    _path_watcher(ws, "old", schedule="daily", evict_after_days=2)
    _check(ws, fw, now=NOW - dt.timedelta(days=5))
    evicting = _check(ws, fw, now=NOW)
    assert _ids(evicting["evicted"]) == ["old"]
    data = wl.recap(ws, fw, since=NOW - dt.timedelta(hours=1), now=NOW)
    assert data is not None and _ids(data["evicted"]) == ["old"]
    assert data["summary"]["watchers"] == 0, "an evicted watcher is no longer live"


# -------------------------------------------------------------------------- CLI

def test_check_needs_exactly_one_of_due_and_cycle(ws: Path, fw: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    _path_watcher(ws, "doc", schedule="daily")
    base = ["--workspace", str(ws), "--framework", str(fw)]
    for argv in (["check"], ["check", "--due", "--cycle", "daily-update"]):
        with pytest.raises(SystemExit) as info:
            wl.main([*argv, *base])
        assert info.value.code == 2
    capsys.readouterr()
    assert wl.main(["check", "--due", *base]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cycle"] == "due" and payload["summary"]["checked"] == 1
    assert wl.main(["check", "--due", "--report", *base]) == 0
    assert capsys.readouterr().out == "", "nothing due and nothing to say"
    assert wl.main(["check", "--cycle", "daily-update", *base]) == 0
    assert json.loads(capsys.readouterr().out)["cycle"] == "daily-update"
    assert wl.main(["recap", "--since", "not-a-date", *base]) == 2
    assert "not an ISO 8601" in capsys.readouterr().err
