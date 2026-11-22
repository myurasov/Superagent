#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Duty stamps, schedules, cadence status, and the on-open staleness nag.

Watchers run only inside an update cycle, and the only hook-free prompt to run
one is the AGENTS.md "On workspace open" nag. When cadences lapse,
nothing else fires: watchers go stale with `error_streak: 0`, and a run
abandoned midway used to leave no trace at all. This tool makes both visible:

  start <name>     stamp `context.yaml.last_runs.<key>_started` as a run begins
                   (the "attempted" marker). `<name>` is a duty of any role
                   (`update-light`, `weekly-review`, a custom role's duty;
                   `<agent>:<duty>` when two roles share a duty id) or one of
                   the cadence skills below.
  finish <name>    stamp `last_runs.<key>` (the "completed" marker) and drop the
                   started marker. A started marker newer than the completed
                   stamp is a run that began and never finished. Readers of
                   `last_runs.<key>` are unaffected: a `_started` key is never
                   a completed run.
  due <name>       exit 0 when the duty's scheduled time has come and it has
                   not run since (the reason is printed), exit 1 when it has.
  status           print what is late:
                     - `last_check` older than 24 hours (or never set);
                     - overdue cadences, by the nag rules below
                       (contracts/update-loop.md § 6);
                     - healthy watchers (active, error_streak 0) whose
                       `last_success` is older than their fastest cycle's
                       period plus one day of grace (daily 1, weekly 7,
                       monthly 31 days; `manual` watchers are never late);
                     - unfinished runs (see `finish`).
                   Each line starts with the agent whose duty it is, in
                   brackets (`[superagent]`, `[updater]`), and lines are
                   grouped by agent in roster order (contracts/agents.md;
                   `tools/agents.py duties --overdue` lists the same findings
                   one duty per row). Prints nothing when everything is
                   current. `--stamp` is
                   the on-open check (AGENTS.md "On workspace open"):
                     - it prints who else wrote here lately (another machine's
                       recent beat, tools/machines.py) and what the conflict
                       sweep merged or found (tools/heal.py), every time;
                     - it prints the late items above at most once per calendar
                       day PER MACHINE: the day it last nagged is kept in
                       `~/.superagent/state/`, never in the workspace;
                     - the only workspace file it writes is this machine's beat
                       file (and a log the sweep merged). A session that opens
                       on a machine that is behind on sync must not rewrite a
                       shared file with its stale copy
                       (rules/concurrent-sessions.md).
                   `--json` prints the full structure instead.

Stamps. Every duty has one stamp, `last_runs.<key>`: the key is the duty's
`schedule` when that names a key of `preferences.cadences`
(`weekly-review` -> `weekly_review`), else the duty's id with `_` for `-`.
Cadence skill -> key: daily-update daily_update, weekly-review weekly_review,
monthly-review monthly_review, supertailor-review tailor_review, doctor
doctor, whatsup whatsup.

Schedules. A value of `preferences.cadences` is one of `"08:30"` (daily),
`"Sunday 18:00"` (weekly), `"1st 09:00"` (monthly) or `"every 90 days"`
(also minutes, hours, weeks). Scheduling goes by the STAMP, never by what day
it is: a duty is DUE when its stamp is older than its latest scheduled time,
so a Sunday review that was missed is due on Monday, and one that ran late
on Wednesday is due again the next Sunday. An `every N` schedule is due when
the stamp is N old. Due is the question a scheduler asks. OVERDUE is the nag:
the rules `status` applies (below), which leave room before they speak.

`context.yaml` edits are text-level (only the touched line changes, comments
survive), parse-checked before (an unparseable file is refused and left
untouched) and after (the file must read back as exactly the intended edit),
then written atomically.

Exit codes: 0 ok (`due`: the duty is due); 1 runtime error (unparseable file,
post-write check failed; `due`: not due); 2 usage error (unknown duty).
"""
from __future__ import annotations

import argparse
import calendar
import dataclasses as dc
import datetime as dt
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import agent_roles, heal, machines
from superagent.tools import watchlist as wl

FRAMEWORK = Path(__file__).resolve().parent.parent
CADENCE_KEYS = {
    "daily-update": "daily_update",
    "weekly-review": "weekly_review",
    "monthly-review": "monthly_review",
    "supertailor-review": "tailor_review",
    "doctor": "doctor",
    "whatsup": "whatsup",
}
STARTED_SUFFIX = "_started"
#: Watcher cycle -> period in days (contracts/watchlist.md cycles).
CYCLE_PERIOD_DAYS = {"daily-update": 1, "weekly-review": 7, "monthly-review": 31}
GRACE_DAYS = 1
STALE_CHECK_HOURS = 24
DOCTOR_DAYS = 180
TAILOR_DAYS = 90
WORKSPACE_MIN_AGE_DAYS = 180  # fresh installs are never nagged about doctor

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_EVERY_DAYS_RE = re.compile(r"every\s+(\d+)\s+days?", re.IGNORECASE)
_EVERY_RE = re.compile(r"^every\s+(\d+)\s*(minute|min|hour|day|week)s?$", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(?:(?P<word>[A-Za-z]+|\d{1,2}(?:st|nd|rd|th)?)\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})$")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_EVERY_UNIT = {"minute": "minutes", "min": "minutes", "hour": "hours", "day": "days", "week": "weeks"}


@dc.dataclass(frozen=True)
class Schedule:
    """A parsed value of `preferences.cadences`: when a duty is meant to run."""

    kind: str                        # "daily" | "weekly" | "monthly" | "every"
    hour: int = 0
    minute: int = 0
    weekday: int | None = None       # weekly: 0 = Monday
    day: int | None = None           # monthly: day of the month (the last day when shorter)
    every: dt.timedelta | None = None

    def describe(self) -> str:
        """The schedule in words (`weekly, Sunday 18:00`)."""
        clock = f"{self.hour:02d}:{self.minute:02d}"
        if self.kind == "daily":
            return f"daily, {clock}"
        if self.kind == "weekly":
            return f"weekly, {_WEEKDAYS[self.weekday or 0].capitalize()} {clock}"
        if self.kind == "monthly":
            return f"monthly, day {self.day} {clock}"
        return f"every {self.every}"


def parse_schedule(value: Any) -> Schedule | None:
    """A `Schedule` from `"08:30"`, `"Sunday 18:00"`, `"1st 09:00"` or `"every 90 days"`.

    None for anything else (an empty value, free text): such a duty has no
    schedule and is never due by one.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return None
    every = _EVERY_RE.match(text)
    if every:
        amount = int(every.group(1))
        if amount <= 0:
            return None
        return Schedule("every", every=dt.timedelta(**{_EVERY_UNIT[every.group(2).lower()]: amount}))
    clock = _CLOCK_RE.match(text)
    if not clock:
        return None
    hour, minute = int(clock.group("h")), int(clock.group("m"))
    if hour > 23 or minute > 59:
        return None
    word = (clock.group("word") or "").lower()
    if not word or word == "daily":
        return Schedule("daily", hour, minute)
    if word[0].isdigit():
        day = int(re.match(r"\d+", word).group(0))
        return Schedule("monthly", hour, minute, day=day) if 1 <= day <= 31 else None
    days = [i for i, name in enumerate(_WEEKDAYS) if len(word) >= 3 and name.startswith(word)]
    return Schedule("weekly", hour, minute, weekday=days[0]) if len(days) == 1 else None


def latest_occurrence(schedule: Schedule, now: dt.datetime) -> dt.datetime | None:
    """The latest scheduled time at or before `now`; None for an `every N` schedule."""
    if schedule.kind == "every":
        return None
    at = now.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    if schedule.kind == "daily":
        return at if at <= now else at - dt.timedelta(days=1)
    if schedule.kind == "weekly":
        at -= dt.timedelta(days=(now.weekday() - (schedule.weekday or 0)) % 7)
        return at if at <= now else at - dt.timedelta(days=7)
    year, month = now.year, now.month
    for _ in range(2):
        day = min(schedule.day or 1, calendar.monthrange(year, month)[1])
        at = now.replace(year=year, month=month, day=day, hour=schedule.hour,
                         minute=schedule.minute, second=0, microsecond=0)
        if at <= now:
            return at
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return at


def schedule_due(schedule: Schedule | None, last: dt.datetime | None, now: dt.datetime, *,
                 since: dt.datetime | None = None) -> tuple[bool, str]:
    """(is a duty with this schedule due, why): the stamp `last` against the schedule.

    A clock schedule is due when the stamp is older than its latest scheduled
    time, or when there is no stamp. An `every N` schedule is due when the
    stamp is N old; with no stamp it counts from `since` (when the workspace
    began), so a new workspace is not due at once. No schedule: never due.
    """
    if schedule is None:
        return False, ""
    if schedule.kind == "every":
        start = last or since
        if start is None:
            return True, f"never ran ({schedule.describe()})"
        if now - start >= (schedule.every or dt.timedelta()):
            what = f"last run {str(_iso(last))[:10]}" if last else "never ran"
            return True, f"{what} ({schedule.describe()})"
        return False, ""
    at = latest_occurrence(schedule, now)
    if last is None:
        return True, f"never ran ({schedule.describe()})"
    if at is not None and last < at:
        return True, f"due since {at:%Y-%m-%d %H:%M} ({schedule.describe()}); last run {str(_iso(last))[:10]}"
    return False, ""


class CorruptFileError(Exception):
    """`context.yaml` exists but is not a YAML mapping; never rewritten."""


class WriteRefusedError(Exception):
    """The edited text did not read back as the intended edit; nothing written."""


# --------------------------------------------------------------------- helpers

def now_local() -> dt.datetime:
    """Current local time, seconds precision, with offset."""
    return dt.datetime.now().astimezone().replace(microsecond=0)


def as_datetime(value: Any) -> dt.datetime | None:
    """A timezone-aware datetime from an ISO string, datetime or date; None otherwise."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.astimezone()
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time()).astimezone()
    if isinstance(value, str) and value.strip():
        try:
            parsed = dt.datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.astimezone()
    return None


def _context_path(workspace: Path) -> Path:
    return workspace / "_memory" / "context.yaml"


def _parse(text: str, name: str) -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_LOADER)  # a safe loader (C or Python)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        raise CorruptFileError(
            f"{name} is not valid YAML ({where}{getattr(exc, 'problem', None) or exc}); refusing to "
            "rewrite it. Fix it by hand (`uv run python -m superagent.tools.validate` locates the line)"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CorruptFileError(f"{name}: top level is a {type(data).__name__}, expected a mapping")
    return data


def load_context(workspace: Path) -> tuple[str, dict[str, Any]]:
    """(text, parsed) of context.yaml; ("", {}) when absent. Raises CorruptFileError."""
    path = _context_path(workspace)
    if not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    return text, _parse(text, path.name)


def load_preferences(workspace: Path) -> dict[str, Any]:
    """`config.yaml.preferences` (read-only; {} when missing or unreadable)."""
    try:
        cfg = yaml.load((workspace / "_memory" / "config.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)
    except (OSError, yaml.YAMLError):
        return {}
    prefs = cfg.get("preferences") if isinstance(cfg, dict) else None
    return prefs if isinstance(prefs, dict) else {}


def _render(key: str, value: Any) -> str:
    return yaml.safe_dump({key: value}, allow_unicode=True, default_flow_style=False).rstrip("\n")


def _set_child(text: str, parent: str, key: str, value: Any, *, delete: bool = False) -> str:
    """Set (or delete) `parent.key` in a top-level block mapping, editing only that line."""
    if text and not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines(keepends=True)
    head_re = re.compile(rf"^{re.escape(parent)}\s*:(.*)$")
    k = next((i for i, ln in enumerate(lines) if head_re.match(ln.rstrip("\n"))), None)
    if k is None:
        if delete:
            return text
        return text + f"{parent}:\n  {_render(key, value)}\n"
    rest = head_re.match(lines[k].rstrip("\n")).group(1).split("#", 1)[0].strip()
    if rest in ("{}", "null", "~"):
        lines[k] = f"{parent}:\n"
    end = next((j for j in range(k + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    children = [j for j in range(k + 1, end)
                if lines[j].strip() and not lines[j].lstrip().startswith("#")]
    indent = (len(lines[children[0]]) - len(lines[children[0]].lstrip(" "))) if children else 2
    key_re = re.compile(rf"^ {{{indent}}}{re.escape(key)}\s*:")
    hit = next((j for j in children if key_re.match(lines[j])), None)
    if delete:
        if hit is not None:
            del lines[hit]
        return "".join(lines)
    new_line = " " * indent + _render(key, value) + "\n"
    if hit is not None:
        lines[hit] = new_line
    else:
        lines.insert((children[-1] if children else k) + 1, new_line)
    return "".join(lines)


def _write_checked(workspace: Path, new_text: str, expected: dict[str, Any]) -> None:
    """Parse-check `new_text` against `expected`, then write context.yaml atomically."""
    path = _context_path(workspace)
    try:
        if _parse(new_text, path.name) != expected:
            raise WriteRefusedError(f"{path.name}: post-write check failed; nothing written")
    except CorruptFileError as exc:
        raise WriteRefusedError(f"{path.name}: the edited text does not parse; nothing written") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


# ----------------------------------------------------------------- markers

def cadence_values(workspace: Path, framework: Path = FRAMEWORK) -> dict[str, Any]:
    """`preferences.cadences`: the config template's values, overlaid by the workspace's own.

    A workspace that predates a cadence key still gets its default.
    """
    values: dict[str, Any] = {}
    try:
        template = yaml.load((framework / "templates" / "memory" / "config.yaml")
                             .read_text(encoding="utf-8"), Loader=_LOADER)  # a safe loader
    except (OSError, yaml.YAMLError):
        template = None
    for prefs in ((template or {}).get("preferences") if isinstance(template, dict) else None,
                  load_preferences(workspace)):
        block = prefs.get("cadences") if isinstance(prefs, dict) else None
        if isinstance(block, dict):
            values.update({str(k): v for k, v in block.items()})
    return values


def find_duty(name: str, workspace: Path, framework: Path = FRAMEWORK
              ) -> tuple[agent_roles.Role, agent_roles.Duty] | None:
    """The (role, duty) that `name` names: a duty id, or `<agent>:<duty>`. None when no role has it.

    Raises ValueError when two roles have a duty with that id and no agent is given.
    """
    roles, _errors = agent_roles.collect(framework, workspace)
    agent, _sep, duty_id = name.rpartition(":")
    hits = [(role, duty) for role in roles for duty in role.duties
            if duty.id == duty_id and (not agent or role.name == agent)]
    if len(hits) > 1:
        owners = ", ".join(f"{role.name}:{duty.id}" for role, duty in hits)
        raise ValueError(f"{name!r} is a duty of more than one role; name one of {owners}")
    return hits[0] if hits else None


def stamp_key(name: str, workspace: Path, framework: Path = FRAMEWORK) -> str:
    """The `last_runs` key of a cadence skill or a duty. Raises ValueError for neither."""
    if name in CADENCE_KEYS:
        return CADENCE_KEYS[name]
    found = find_duty(name, workspace, framework)
    if found is None:
        raise ValueError(f"{name!r} is not a cadence skill or a duty of any role "
                         f"(cadence skills: {', '.join(CADENCE_KEYS)}; duties: "
                         "`uv run python -m superagent.tools.agents duties`)")
    return agent_roles.duty_stamp_key(found[1])


def mark(workspace: Path, skill: str, *, phase: str, now: dt.datetime | None = None,
         framework: Path = FRAMEWORK) -> str:
    """`phase` "start" stamps `<key>_started`; "finish" stamps `<key>` and drops the marker.

    `skill` is a cadence skill or a duty of any role (`stamp_key`). Returns
    the `last_runs` key written. Raises ValueError for a name that is neither,
    CorruptFileError / WriteRefusedError when context.yaml cannot be written
    safely.
    """
    key = stamp_key(skill, workspace, framework)
    stamp = _iso(now or now_local())
    text, ctx = load_context(workspace)
    runs = ctx.get("last_runs")
    if runs is not None and not isinstance(runs, dict):
        raise CorruptFileError("context.yaml: `last_runs` is not a mapping; refusing to rewrite it")
    expected = dict(ctx)
    new_runs = dict(runs or {})
    if phase == "start":
        new_runs[key + STARTED_SUFFIX] = stamp
        new_text = _set_child(text, "last_runs", key + STARTED_SUFFIX, stamp)
        written = key + STARTED_SUFFIX
    else:
        new_runs[key] = stamp
        new_runs.pop(key + STARTED_SUFFIX, None)
        new_text = _set_child(text, "last_runs", key, stamp)
        new_text = _set_child(new_text, "last_runs", key + STARTED_SUFFIX, None, delete=True)
        written = key
    expected["last_runs"] = new_runs
    _write_checked(workspace, new_text, expected)
    return written


# ------------------------------------------------------------------ status

def _every_days(value: Any, default: int) -> int:
    m = _EVERY_DAYS_RE.search(str(value or ""))
    return int(m.group(1)) if m else default


def workspace_since(workspace: Path) -> dt.datetime | None:
    """Earliest of the first interaction-log row and `workspace/.version` (birth) time."""
    found: list[dt.datetime] = []
    for _path, _index, row in machines.interaction_log_rows(workspace)[:5]:
        when = as_datetime(row.get("ts") or row.get("timestamp"))
        if when:
            found.append(when)
    version = workspace / ".version"
    if version.exists():
        st = version.stat()
        born = getattr(st, "st_birthtime", None) or st.st_mtime
        found.append(dt.datetime.fromtimestamp(born).astimezone())
    return min(found) if found else None


def overdue_cadences(runs: dict[str, Any], prefs: dict[str, Any], now: dt.datetime,
                     since: dt.datetime | None) -> list[dict[str, Any]]:
    """Overdue cadences by the nag rules (contracts/update-loop.md § 6): what is late, not what is due."""
    cadences = prefs.get("cadences") if isinstance(prefs.get("cadences"), dict) else {}
    out: list[dict[str, Any]] = []

    def add(skill: str, last: dt.datetime | None, reason: str) -> None:
        out.append({"cadence": skill, "last_run": _iso(last), "reason": reason})

    last = as_datetime(runs.get("weekly_review"))
    if prefs.get("weekly_review_auto") and (last is None or now - last > dt.timedelta(days=7)):
        add("weekly-review", last, "no run in the last 7 days")
    last = as_datetime(runs.get("monthly_review"))
    if (prefs.get("monthly_review_auto") and now.day > 1
            and (last is None or (last.year, last.month) < (now.year, now.month))):
        add("monthly-review", last, f"the {now:%B} pass, due the 1st, has not run")
    doctor_days = _every_days(cadences.get("doctor"), DOCTOR_DAYS)
    last = as_datetime(runs.get("doctor"))
    old_enough = since is not None and now - since > dt.timedelta(days=WORKSPACE_MIN_AGE_DAYS)
    if old_enough and (last is None or now - last > dt.timedelta(days=doctor_days)):
        add("doctor", last, f"no hygiene pass in {doctor_days}+ days")
    tailor_days = _every_days(cadences.get("tailor_review"), TAILOR_DAYS)
    last = as_datetime(runs.get("tailor_review"))
    if last is None or now - last > dt.timedelta(days=tailor_days):
        add("supertailor-review", last, f"no framework review in {tailor_days}+ days")
    return out


def overdue_watchers(workspace: Path, now: dt.datetime, *, framework: Path = FRAMEWORK,
                     grace_days: int = GRACE_DAYS) -> list[dict[str, Any]]:
    """Healthy watchers whose last success is older than their fastest cycle's period plus grace.

    Healthy = enabled, state `status: active`, `error_streak` 0 (erroring and
    evicted rows are reported by their own checks). A watcher that never
    succeeded is measured from its ref file's mtime, so a freshly registered
    one is not late yet. `manual` / custom-cycle watchers are never late.
    """
    packs, _errors = wl.discover_packs(framework, workspace, announce=lambda _msg: None)
    watchers, _registry_errors = wl.load_registry(workspace, wl.load_config(workspace), packs)
    state = wl.load_yaml(wl.state_path(workspace))
    rows = state.get("watchers") if isinstance(state, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    out: list[dict[str, Any]] = []
    for watcher in watchers:
        periods = {c: CYCLE_PERIOD_DAYS[c] for c in watcher.cycles if c in CYCLE_PERIOD_DAYS}
        if not watcher.enabled or not periods:
            continue
        row = rows.get(watcher.id) if isinstance(rows.get(watcher.id), dict) else {}
        if row.get("status", "active") != "active" or int(row.get("error_streak") or 0) > 0:
            continue
        cycle = min(periods, key=periods.__getitem__)
        last = as_datetime(row.get("last_success"))
        anchor = last or (dt.datetime.fromtimestamp(watcher.mtime).astimezone() if watcher.mtime else None)
        limit = dt.timedelta(days=periods[cycle] + grace_days)
        if anchor is None or now - anchor > limit:
            out.append({
                "watcher": watcher.id,
                "cycle": cycle,
                "last_success": _iso(last),
                "days_since": None if anchor is None else (now - anchor).days,
            })
    return out


def unfinished_runs(runs: dict[str, Any]) -> list[dict[str, Any]]:
    """Cadence runs whose started marker is newer than their completed stamp."""
    out: list[dict[str, Any]] = []
    for key, value in runs.items():
        if not str(key).endswith(STARTED_SUFFIX):
            continue
        base = str(key)[: -len(STARTED_SUFFIX)]
        started, done = as_datetime(value), as_datetime(runs.get(base))
        if started and (done is None or done < started):
            skill = next((s for s, k in CADENCE_KEYS.items() if k == base), base)
            out.append({"cadence": skill, "key": base, "started": _iso(started),
                        "last_completed": _iso(done)})
    return out


def duty_due(workspace: Path, name: str, *, now: dt.datetime | None = None,
             framework: Path = FRAMEWORK) -> tuple[bool, str]:
    """(is the duty's scheduled time here and the duty not run since, why).

    The stamp of the duty against its schedule value in `preferences.cadences`
    (`schedule_due`). A duty switched off by `preferences.<key>_auto: false`,
    one with no schedule value, and an on-request duty are never due. Raises
    ValueError for a name that is no duty and no cadence skill.
    """
    now = now or now_local()
    key = stamp_key(name, workspace, framework)
    prefs = load_preferences(workspace)
    if prefs.get(f"{key}_auto") is False:
        return False, ""
    _text, ctx = load_context(workspace)
    runs = ctx.get("last_runs") if isinstance(ctx.get("last_runs"), dict) else {}
    schedule = parse_schedule(cadence_values(workspace, framework).get(key))
    return schedule_due(schedule, as_datetime(runs.get(key)), now, since=workspace_since(workspace))


def status(workspace: Path, *, now: dt.datetime | None = None, framework: Path = FRAMEWORK,
           grace_days: int = GRACE_DAYS) -> dict[str, Any]:
    """Everything `status` reports, as one mapping."""
    now = now or now_local()
    _text, ctx = load_context(workspace)
    runs = ctx.get("last_runs") if isinstance(ctx.get("last_runs"), dict) else {}
    last_check = as_datetime(ctx.get("last_check"))
    stale = last_check is None or now - last_check > dt.timedelta(hours=STALE_CHECK_HOURS)
    report = {
        "now": _iso(now),
        "last_check": _iso(last_check),
        "last_check_stale": stale,
        "overdue_cadences": overdue_cadences(runs, load_preferences(workspace), now,
                                             workspace_since(workspace)),
        "overdue_watchers": overdue_watchers(workspace, now, framework=framework,
                                             grace_days=grace_days),
        "unfinished_runs": unfinished_runs(runs),
        "refresh_suggested": machines.read_local_state(workspace).get("refresh_suggested"),
    }
    return assign_agents(report, workspace, framework)


def assign_agents(report: dict[str, Any], workspace: Path, framework: Path = FRAMEWORK
                  ) -> dict[str, Any]:
    """Name the agent each finding belongs to (contracts/agents.md "Duties").

    A cadence finding goes to the role with a duty that runs that skill, a
    watcher finding to the role whose duty sits behind the `watchers` check,
    and anything unclaimed to the lead. Adds `agent` to every finding row,
    `last_check_agent`, and `agents` (the roster order `render` groups by).
    With no readable role file the names are None and `render` prints the
    lines without a prefix.
    """
    roles, _errors = agent_roles.collect(framework, workspace)
    for row in report["overdue_cadences"]:
        row["agent"] = agent_roles.duty_owner(roles, row["cadence"])
    for row in report["unfinished_runs"]:
        owner = agent_roles.stamp_owner(roles, row["key"])
        if owner is not None:
            row["agent"], row["cadence"] = owner[0].name, owner[1].id
        else:
            row["agent"] = agent_roles.duty_owner(roles, row["cadence"])
    watcher_agent = agent_roles.watchers_owner(roles)
    for row in report["overdue_watchers"]:
        row["agent"] = watcher_agent
    report["last_check_agent"] = agent_roles.duty_owner(roles, "daily-update")
    report["agents"] = [role.name for role in roles]
    return report


def _by_agent(rows: list[dict[str, Any]]) -> dict[str | None, list[dict[str, Any]]]:
    """Finding rows keyed by their `agent` (None when a row has none)."""
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get("agent"), []).append(row)
    return groups


def render(report: dict[str, Any]) -> list[str]:
    """Human lines for the findings in `report`, grouped by agent; [] when everything is current.

    A line starts with `[<agent>] ` when the finding has one (`assign_agents`).
    Agents come in roster order, and one agent's lines stay together: stale
    context, overdue cadences, overdue watchers, unfinished runs.
    """
    found: dict[str | None, list[str]] = {}
    now = as_datetime(report["now"])
    if report["last_check_stale"]:
        last = as_datetime(report["last_check"])
        age = f"{(now - last).days} day(s) ago, {last:%Y-%m-%d}" if last and now else "never"
        found.setdefault(report.get("last_check_agent"), []).append(
            f"Context last refreshed {age}: run `daily-update` (or `whatsup`).")
    for agent, rows in _by_agent(report["overdue_cadences"]).items():
        found.setdefault(agent, []).append("Overdue cadences: " + "; ".join(
            f"{c['cadence']} ({c['reason']}; last {str(c['last_run'] or 'never')[:10]})"
            for c in rows) + ".")
    for agent, rows in _by_agent(report["overdue_watchers"]).items():
        found.setdefault(agent, []).append("Watchers overdue for a check: " + "; ".join(
            f"{w['watcher']} ({w['cycle']}; last success {str(w['last_success'] or 'never')[:10]}"
            + (f", {w['days_since']} day(s) ago)" if w["days_since"] is not None else ")")
            for w in rows) + ".")
    for agent, rows in _by_agent(report["unfinished_runs"]).items():
        found.setdefault(agent, []).append("Unfinished runs: " + "; ".join(
            f"{u['cadence']} started {str(u['started'])[:16].replace('T', ' ')} and never finished"
            for u in rows) + ".")
    order = report.get("agents") or []
    lines: list[str] = []
    for agent in sorted(found, key=lambda a: order.index(a) if a in order else len(order)):
        prefix = f"[{agent}] " if agent else ""
        lines += [prefix + line for line in found[agent]]
    return lines


def stamp_refresh_suggested(workspace: Path, today: str) -> None:
    """Record that this machine nagged `today` (YYYY-MM-DD), in `~/.superagent/state/`.

    Machine-local on purpose: the workspace may sync between machines, and a
    marker written into `context.yaml` at session open let a stale copy of
    that file win over fresher content from another machine.
    """
    machines.write_local_state(workspace, refresh_suggested=today)


def on_open(workspace: Path, now: dt.datetime) -> list[str]:
    """The every-time part of the on-open check: presence, conflict sweep, stray rows, beat.

    Returns the lines to print. Never raises: a sweep that fails is reported
    as a line, and the cadence report still follows.
    """
    lines = machines.presence_lines(workspace, now=now)
    try:
        lines += heal.sweep_lines(workspace)
        lines += heal.stray_lines(workspace)
    except Exception as exc:  # the open check must not die on one bad file
        lines.append(f"Conflict sweep failed ({exc}); run `uv run python -m superagent.tools.heal sweep`.")
    machines.beat(workspace, now=now)
    return lines


# --------------------------------------------------------------------- CLI

def _aware(value: str) -> dt.datetime:
    parsed = as_datetime(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """The `cadence` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    common.add_argument("--now", type=_aware, default=None, help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="cadence", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("start", "Stamp the attempted marker for a run of a duty."),
                       ("finish", "Stamp the completed marker and drop the attempted one."),
                       ("due", "Exit 0 when the duty's scheduled time has come and it has not run since.")):
        cmd = sub.add_parser(name, parents=[common], help=text)
        cmd.add_argument("skill", metavar="name",
                         help="A duty (`update-full`, `<agent>:<duty>`) or a cadence skill.")
    st = sub.add_parser("status", parents=[common], help="Print stale context, overdue cadences / "
                                                          "watchers and unfinished runs.")
    st.add_argument("--stamp", action="store_true",
                    help="On-open mode: silent when already nagged today; stamp refresh_suggested.")
    st.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    st.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    now = args.now or now_local()
    try:
        if args.command in ("start", "finish"):
            key = mark(workspace, args.skill, phase=args.command, now=now)
            print(f"last_runs.{key} = {_iso(now)}")
            return 0
        if args.command == "due":
            due, why = duty_due(workspace, args.skill, now=now)
            if due:
                print(why)
            return 0 if due else 1
        report = status(workspace, now=now, grace_days=args.grace_days)
        today = now.date().isoformat()
        open_lines = on_open(workspace, now) if args.stamp else []
        # Late items: at most once per day on this machine (AGENTS.md "On workspace open").
        nagged = args.stamp and report["refresh_suggested"] == today
        lines = [] if nagged else render(report)
        if args.json:
            print(json.dumps({**report, "on_open": open_lines}, indent=2))
        else:
            for line in open_lines + lines:
                print(line)
        if args.stamp and lines:
            stamp_refresh_suggested(workspace, today)
    except ValueError as exc:
        print(f"cadence: {exc}", file=sys.stderr)
        return 2
    except (CorruptFileError, WriteRefusedError) as exc:
        print(f"cadence: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
