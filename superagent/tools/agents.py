#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Agents: the roster, ids, duties and the zero-cost duty check.

Implements contracts/agents.md on top of `tools/agent_roles.py` (the role
files) and `tools/cadence.py` (what is late).

  list                  the roster: every role, its instances from the registry
                        (`_memory/agents.yaml`), their ids and dev twins
  id <name>             the id of an agent: `updater`, `updater:dev`,
                        `project:<slug>` (`--dev` asks for the dev twin)
  duties                every duty with its schedule, last run and state;
                        `--agent <role>` narrows it, `--due` keeps what a
                        scheduler would run now, `--overdue` only what is late
                        (the same findings `cadence status` prints)
  check <agent> <duty>  the zero-cost check of one duty, for a scheduled wake:
                        exit 0 when the duty is due (the reason is printed),
                        exit 1 when it is not (nothing is printed). With
                        `--wake NAME` it is the check of a granted wake: exit 1
                        as well when the wake has no grant on the agent's
                        registry row, or the grant is suspended or past its end
                        (the duty may then be left out: a wake with no duty is
                        due whenever its grant holds)
  grant <agent>         record a wake the owner granted, with its exact terms,
                        on the agent's registry row (`--suspend` / `--resume`
                        switch one or all of its grants off and on)
  revoke <agent>        remove a grant from the row
  set <agent>           create or update the agent's registry row: `--host`,
                        `--status`, `--model`, `--variant`, `--daily-usd`,
                        `--project`, `--notes` (this is how a project agent
                        comes to exist)
  interest <agent>      the agent's interests: the kinds of update it wants to
                        hear about, as handles or handle globs (`project:kitchen-reno`,
                        `domain:finances`, `bill:*`). `--add PATTERN --why TEXT`
                        records one (`--when changed|harvest|filed|captured`
                        narrows it to one kind of update; `--fire WAKE` has the
                        update cycle fire a granted `fire-only` wake in place of
                        a message), `--remove PATTERN` drops one, `--defaults`
                        writes the role's own. Twelve per agent at most
  interested            who to tell about one thing an update cycle moved:
                        `--handle` its own handle and each handle it relates to
                        (`--when` says what kind of update it is). One line per
                        agent a session holds: its id, then `message` or
                        `fire <wake>`
  may-fetch             exit 0 when this machine may run the update cycle,
                        exit 1 when the Updater runs on another machine
  heartbeat             lines for the daily briefing: which agents run as
                        sessions of their own, when the update loop last ran,
                        which wakes are granted, and each agent's interests.
                        Nothing when no agent does

A duty's state is one of: `ok`; `due` (its scheduled time has come and it has
not run since, or its check found work: the stamp against the schedule,
`tools/cadence.py`); `overdue` (late by the nag rules of `cadence status`);
`unfinished` (a run began and never finished); `on-request` (no check).
`check` exits 0 for due, overdue and unfinished alike.

Ids are `<prefix>:<instance name>`; the prefix is
`config.preferences.autocode.id_prefix` (default `sa`). The registry holds
names, never ids, so changing the prefix changes every id at once.

`list`, `id`, `duties`, `check`, `interested`, `may-fetch` and `heartbeat` read only. `grant`,
`revoke`, `set` and `interest` write the registry, text-level and parse-checked (the writer of
`tools/signals.py`). No command talks to an Autocode server: the tool works
with Autocode off. Setting a wake in a runtime is `tools/autocode.py`'s work.

Held duties. `config.preferences.update.work_hours` ("07:00-22:00") and
`off_days` (weekday names or dates) say when a scheduled update may run.
Outside them the duties whose skill is `update` are HELD: `check` exits 1 and
`duties` shows the state with "held". A run the owner starts by hand always
runs.

Exit codes: 0 ok (`check`: due); 1 runtime error (`check`: not due); 2 usage
error (unknown agent or duty, a bad prefix or name); 3 `check` could not run
(an unreadable file), so a scheduler sees a failed check and never mistakes it
for "not due".
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import cadence, inbox, machines, signals
from superagent.tools import watchlist as wl

FRAMEWORK = Path(__file__).resolve().parent.parent
REGISTRY_REL = "_memory/agents.yaml"
REGISTRY_KEY = "agents"
#: Row fields `set` may write. Grants (`wakes`) go through `grant` only.
ROW_FIELDS = ("host", "model", "variant", "daily_usd", "status", "project", "notes")
REGISTRY_SKELETON = "schema_version: 1\nlast_updated: null\n"
STATUSES = ("off", "active", "dormant", "paused", "retired")
#: Statuses of an agent a message reaches: a session holds it.
REACHABLE = ("active", "dormant")
DEFAULT_STATUS = "off"
#: Duties of this skill are held outside `preferences.update.work_hours` / on `off_days`.
HELD_SKILL = "update"
#: The briefing flags an active update loop whose last light run lies further back than the
#: light update's period plus this slack, counted inside work hours: every slot runs one.
HEARTBEAT_SLACK_MINUTES = 30
DEFAULT_LIGHT_PERIOD = dt.timedelta(hours=2)
CUSTOM_CHECK_TIMEOUT = 120
_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_WAKE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_HANDLE_RE = re.compile(r"^[a-z_]+:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
#: Duty states a scheduler acts on (`check` exits 0), and the ones that are late.
RUNNABLE = ("due", "overdue", "unfinished")
LATE = ("overdue", "unfinished")

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# ---------------------------------------------------------------- registry

def load_registry(workspace: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows of `_memory/agents.yaml` (the template's empty row skipped) and its problems.

    A missing file is an empty registry: a workspace that never placed an
    agent has none.
    """
    path = workspace / REGISTRY_REL
    if not path.is_file():
        return [], []
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_LOADER)  # a safe loader
    except (OSError, yaml.YAMLError) as exc:
        return [], [f"{REGISTRY_REL}: cannot be read ({exc})"]
    rows = data.get("agents") if isinstance(data, dict) else None
    if rows is None:
        return [], []
    if not isinstance(rows, list):
        return [], [f"{REGISTRY_REL}: `agents` is not a list"]
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{REGISTRY_REL}: row #{index + 1} is not a mapping")
            continue
        name = row.get("name")
        if not name:
            continue  # the template's example row
        if name in seen:
            errors.append(f"{REGISTRY_REL}: `{name}` is listed twice")
            continue
        seen.add(str(name))
        status = row.get("status") or DEFAULT_STATUS
        if status not in STATUSES:
            errors.append(f"{REGISTRY_REL}: `{name}` has status {status!r} "
                          f"(expected one of {', '.join(STATUSES)})")
        out.append(row)
    return out, errors


def _model_for(role: ar.Role, row: dict[str, Any], autocode: dict[str, Any], *, dev: bool = False
               ) -> tuple[str | None, str | None]:
    """(model, variant): the registry row's own, else the configured default of the tier.

    The lead and its dev twin take no default. The lead is the owner's own
    session, on the model the owner chose there, so only a model named on its
    row is reported. Its dev twin is started on the model of the session that
    starts it (`tools/autocode.py`): (None, None) here.
    """
    if role.kind == "lead":
        return (None, None) if dev else (row.get("model"), row.get("variant"))
    models = autocode.get("models") if isinstance(autocode.get("models"), dict) else {}
    tier = models.get("dev" if dev else role.model_tier)
    tier = tier if isinstance(tier, dict) else {}
    if dev:
        return tier.get("model"), tier.get("variant")
    return row.get("model") or tier.get("model"), row.get("variant") or tier.get("variant")


def roster(workspace: Path, framework: Path = FRAMEWORK) -> dict[str, Any]:
    """The roster as one mapping: prefix, one row per agent, and the problems found.

    A lead or durable role is one agent, with or without a registry row. A
    project role has one agent per registry row that names it. Raises
    AgentError when the configured prefix is not valid.
    """
    roles, errors = ar.collect(framework, workspace)
    prefix = ar.id_prefix(workspace)
    autocode = ar.autocode_preferences(workspace)
    registry, registry_errors = load_registry(workspace)
    errors = errors + registry_errors
    by_name = {str(r["name"]): r for r in registry}
    known_roles = {r.name for r in roles}
    agents: list[dict[str, Any]] = []
    used: set[str] = set()

    def add(role: ar.Role, name: str, row: dict[str, Any]) -> None:
        model, variant = _model_for(role, row, autocode)
        dev_model, dev_variant = _model_for(role, row, autocode, dev=True)
        cap = row.get("daily_usd")
        agents.append({
            "id": ar.agent_id(prefix, name),
            "name": name,
            "role": role.name,
            "kind": role.kind,
            "origin": role.origin,
            "status": row.get("status") or DEFAULT_STATUS,
            "host": row.get("host"),
            "project": row.get("project"),
            "model": model,
            "variant": variant,
            "daily_usd": autocode.get("daily_usd") if cap is None else cap,
            "wakes": len(row.get("wakes") or []),
            "grants": _grants(row),
            "interests": _interests(row),
            "registered": bool(row),
            "dev_id": ar.agent_id(prefix, ar.instance_name(role, dev=True)),
            "dev_model": dev_model,
            "dev_variant": dev_variant,
            "description": role.description,
        })

    for role in roles:
        if role.kind == "project":
            for name, row in by_name.items():
                if row.get("role") != role.name:
                    continue
                used.add(name)
                try:
                    part, slug, is_dev = ar.split_instance(name)
                    if part != role.name or is_dev or not slug:
                        raise ar.AgentError(f"`{name}` is not `{role.name}:<slug>`")
                except ar.AgentError as exc:
                    errors.append(f"{REGISTRY_REL}: {exc}")
                    continue
                add(role, name, row)
        else:
            row = by_name.get(role.name, {})
            if row:
                used.add(role.name)
                if row.get("role") not in (None, "", role.name):
                    errors.append(f"{REGISTRY_REL}: `{role.name}` names role {row.get('role')!r}")
            add(role, role.name, row)
    for name, row in by_name.items():
        if name not in used:
            role_name = row.get("role") or name
            what = "has no role file" if role_name not in known_roles else "does not fit its role"
            errors.append(f"{REGISTRY_REL}: `{name}` {what} (role {role_name!r})")
    return {
        "prefix": prefix,
        "autocode_enabled": ar.autocode_enabled(workspace),
        "agents": agents,
        "roles": [{"name": r.name, "kind": r.kind, "origin": r.origin,
                   "dev_id": ar.agent_id(prefix, ar.instance_name(r, dev=True)),
                   "instances": sum(1 for a in agents if a["role"] == r.name)} for r in roles],
        "errors": errors,
    }


def render_roster(data: dict[str, Any]) -> list[str]:
    """The roster as an aligned text table."""
    rows = [("ID", "ROLE", "KIND", "STATUS", "HOST", "DEV TWIN")]
    for agent in data["agents"]:
        rows.append((agent["id"], agent["role"], agent["kind"], agent["status"],
                     agent["host"] or "-", agent["dev_id"]))
    for role in data["roles"]:
        if role["kind"] == "project" and not role["instances"]:
            rows.append((f"{data['prefix']}:{role['name']}:<slug>", role["name"], role["kind"],
                         "no agent yet", "-", role["dev_id"]))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]


# ------------------------------------------------------------------ duties

def _age(report: dict[str, Any]) -> str:
    now, last = cadence.as_datetime(report["now"]), cadence.as_datetime(report["last_check"])
    return f"{(now - last).days} day(s) ago" if now and last else "never"


def _names(items: list[str], limit: int = 5) -> str:
    return ", ".join(items[:limit]) + (f", +{len(items) - limit} more" if len(items) > limit else "")


def evaluate(role: ar.Role, duty: ar.Duty, report: dict[str, Any], *, workspace: Path,
             framework: Path = FRAMEWORK, now: dt.datetime | None = None) -> tuple[str, str]:
    """(state, reason) of one duty against a `cadence.status()` report.

    States: `overdue` (late by the nag rules), `unfinished` (a run began and
    never finished), `due` (the scheduled time has come, or the check found
    work), `ok`, and `on-request` for a duty with no check.
    """
    now = now or cadence.as_datetime(report["now"]) or cadence.now_local()
    key = ar.duty_stamp_key(duty)
    unfinished = next((row for row in report["unfinished_runs"] if row.get("key") == key), None)
    if duty.check in ar.WATCHER_CHECKS:
        late = report["overdue_watchers"]
        if late:
            names = _names([w["watcher"] for w in late])
            return "overdue", f"{len(late)} watcher(s) overdue for a check: {names}"
        if duty.check == "watchers":
            return "ok", ""
        if unfinished:
            started = str(unfinished["started"])[:16].replace("T", " ")
            return "unfinished", f"started {started} and never finished"
        # Every slot runs an update: the check names what waits, and no longer skips a slot
        # in which nothing does (the owner's decision; work hours, the grant and the cost cap
        # still hold a scheduled run).
        found: list[str] = []
        due = [row["id"] for row in wl.due_rows(workspace, framework, now=now) if row["due"]]
        if due:
            found.append(f"{len(due)} watcher(s) due: {_names(due)}")
        files = inbox.waiting(workspace, now=now)
        if files:
            found.append(f"{len(files)} document(s) waiting in a drop folder")
        return "due", "; ".join(found) or "every slot runs an update"
    if ar.custom_check_name(duty.check):
        return "custom", ""  # the role's own script: run by `check`, never by a listing
    if duty.check != "cadence":
        return "on-request", ""
    for row in report["overdue_cadences"]:
        if row["cadence"] == duty.skill:
            return "overdue", f"{row['reason']}; last {str(row['last_run'] or 'never')[:10]}"
    if duty.skill == "daily-update" and report["last_check_stale"]:
        return "overdue", f"context last refreshed {_age(report)}"
    if unfinished:
        started = str(unfinished["started"])[:16].replace("T", " ")
        return "unfinished", f"started {started} and never finished"
    due_now, why = cadence.duty_due(workspace, f"{role.name}:{duty.id}", now=now, framework=framework)
    return ("due", why) if due_now else ("ok", "")


def duty_rows(workspace: Path, framework: Path = FRAMEWORK, *, now: dt.datetime | None = None,
              agent: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per duty of every role (or of `agent`), and the role problems found.

    Raises AgentError when `agent` names no role.
    """
    roles, errors = ar.collect(framework, workspace)
    if agent is not None:
        role_name = ar.split_instance(agent)[0]
        roles = [r for r in roles if r.name == role_name]
        if not roles:
            raise ar.AgentError(f"no role named {role_name!r}")
    report = cadence.status(workspace, now=now, framework=framework)
    _text, ctx = cadence.load_context(workspace)
    runs = ctx.get("last_runs") if isinstance(ctx.get("last_runs"), dict) else {}
    cadences = cadence.cadence_values(workspace, framework)
    moment = now or cadence.as_datetime(report["now"])
    held = update_window(workspace, moment) if moment else None
    rows: list[dict[str, Any]] = []
    for role in roles:
        for duty in role.duties:
            state, reason = evaluate(role, duty, report, workspace=workspace, framework=framework,
                                     now=moment)
            last = cadence.as_datetime(runs.get(ar.duty_stamp_key(duty)))
            rows.append({
                "agent": role.name,
                "duty": duty.id,
                "skill": duty.skill,
                "tier": duty.tier,
                "schedule": duty.schedule,
                "schedule_value": cadences.get(duty.schedule),
                "check": duty.check,
                "last_run": last.isoformat(timespec="seconds") if last else None,
                "state": state,
                "reason": reason,
                "held": held if duty.skill == HELD_SKILL else None,
            })
    return rows, errors


def render_duties(rows: list[dict[str, Any]]) -> list[str]:
    """The duty rows as an aligned text table ([] when there is no row)."""
    if not rows:
        return []
    table = [("AGENT", "DUTY", "SKILL", "TIER", "SCHEDULE", "LAST RUN", "STATE")]
    for row in rows:
        schedule = row["schedule"]
        if row["schedule_value"]:
            schedule = f"{schedule} ({row['schedule_value']})"
        state = row["state"] + (f": {row['reason']}" if row["reason"] else "")
        if row.get("held") and row["state"] in RUNNABLE:
            state += f" [held: {row['held']}]"
        table.append((row["agent"], row["duty"], row["skill"], row["tier"], schedule,
                      str(row["last_run"] or "-")[:10], state))
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]) - 1)]
    return ["  ".join([cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])] + [row[-1]]).rstrip()
            for row in table]


class CheckFailedError(RuntimeError):
    """A check could not run (a custom check script failed): never "not due"."""


def run_custom_check(workspace: Path, check: str, *, timeout: int = CUSTOM_CHECK_TIMEOUT
                     ) -> tuple[bool, str]:
    """Run a custom role's check script: (due, its first lines of output).

    `uv`'s interpreter runs `workspace/_custom/tools/<name>.py --workspace
    <workspace>`: exit 0 means due (stdout is the reason), exit 1 not due.
    Any other exit, a missing script or a timeout raises CheckFailedError.
    """
    script = ar.custom_check_path(workspace, check)
    if script is None or not script.is_file():
        raise CheckFailedError(f"check script for `{check}` does not exist")
    try:
        result = subprocess.run([sys.executable, str(script), "--workspace", str(workspace)],
                                check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckFailedError(f"`{check}` could not run ({exc})") from exc
    if result.returncode not in (0, 1):
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise CheckFailedError(f"`{check}` failed with exit {result.returncode}: {tail[0][:200]}")
    reason = " ".join(result.stdout.split())[:300]
    return result.returncode == 0, reason or "its check says so"


def check(workspace: Path, agent: str, duty_id: str | None, framework: Path = FRAMEWORK, *,
          now: dt.datetime | None = None, wake: str | None = None) -> tuple[bool, str]:
    """(is the duty due, why). Raises AgentError for an unknown agent or duty.

    With `wake`, the check of a granted wake: not due when the agent's
    registry row has no grant of that name, or the grant is suspended or past
    its end. A wake with no duty is due whenever its grant holds. A held duty
    (outside the update's work hours) is not due. Raises CheckFailedError when
    a custom check script cannot say.
    """
    now = now or cadence.now_local()
    if wake is not None:
        ok, why = grant_state(workspace, agent, wake, now)
        if not ok:
            return False, why
    if duty_id is None:
        if wake is None:
            raise ar.AgentError("name a duty, or a granted wake with --wake")
        return True, f"{agent}: wake `{wake}` is granted"
    rows, _errors = duty_rows(workspace, framework, now=now, agent=agent)
    row = next((r for r in rows if r["duty"] == duty_id), None)
    if row is None:
        known = ", ".join(r["duty"] for r in rows) or "none"
        raise ar.AgentError(f"{ar.split_instance(agent)[0]!r} has no duty {duty_id!r} "
                            f"(duties: {known})")
    if row["state"] == "custom":
        due, reason = run_custom_check(workspace, row["check"])
        return due, f"{row['agent']} {row['duty']}: due: {reason}" if due else ""
    due = row["state"] in RUNNABLE and not row.get("held")
    return due, f"{row['agent']} {row['duty']}: {row['state']}: {row['reason']}" if due else ""


# ------------------------------------------------------------------- held

def update_window(workspace: Path, now: dt.datetime) -> str | None:
    """Why a scheduled update is held right now, or None when it may run.

    `config.preferences.update.work_hours` is "HH:MM-HH:MM" (it may cross
    midnight); `off_days` lists weekday names and ISO dates. A value that
    cannot be read holds nothing.
    """
    block = ar.load_preferences(workspace).get("update")
    block = block if isinstance(block, dict) else {}
    for day in block.get("off_days") or []:
        if isinstance(day, dt.datetime):
            day = day.date()
        if isinstance(day, dt.date):
            if day == now.date():
                return f"an off day ({day.isoformat()})"
            continue
        text = str(day).strip().lower()
        if len(text) >= 3 and now.strftime("%A").lower().startswith(text):
            return f"an off day ({now:%A})"
        try:
            if dt.date.fromisoformat(text) == now.date():
                return f"an off day ({text})"
        except ValueError:
            continue
    match = _HOURS_RE.match(str(block.get("work_hours") or "").strip())
    if match:
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = int(match.group(3)) * 60 + int(match.group(4))
        minute = now.hour * 60 + now.minute
        inside = start <= minute < end if start <= end else (minute >= start or minute < end)
        if not inside:
            return f"outside work hours ({block['work_hours']})"
    return None


def light_period(workspace: Path) -> dt.timedelta:
    """How far apart the light update's slots lie: `preferences.cadences.update_light`
    when it is an `every N ...` value, else two hours."""
    cadences = ar.load_preferences(workspace).get("cadences")
    value = cadences.get("update_light") if isinstance(cadences, dict) else None
    schedule = cadence.parse_schedule(value)
    return schedule.every if schedule is not None and schedule.every else DEFAULT_LIGHT_PERIOD


def window_opened(workspace: Path, now: dt.datetime) -> dt.datetime | None:
    """When the work hours `now` lies in began; None with no work hours set.

    A loop that was held overnight is not quiet at the start of the day: the
    time it could have run counts from here.
    """
    block = ar.load_preferences(workspace).get("update")
    block = block if isinstance(block, dict) else {}
    match = _HOURS_RE.match(str(block.get("work_hours") or "").strip())
    if not match:
        return None
    opened = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
    return opened if opened <= now else opened - dt.timedelta(days=1)


# ------------------------------------------------------------------ grants

def _registry_rows(workspace: Path) -> tuple[Path, list[Any]]:
    """(the registry path, every row as parsed, the template's example row included)."""
    path = workspace / REGISTRY_REL
    _text, _data, rows = signals._read(path, REGISTRY_KEY)  # noqa: SLF001 - the shared reader
    return path, rows


def _row_index(rows: list[Any], name: str) -> int | None:
    return next((i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("name") == name), None)


def _grants(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in row.get("wakes") or [] if isinstance(g, dict) and g.get("name")]


def grants(workspace: Path, agent: str) -> list[dict[str, Any]]:
    """The wakes granted to `agent` (its instance name), as written on its registry row."""
    rows, _errors = load_registry(workspace)
    row = next((r for r in rows if r.get("name") == agent), None)
    return _grants(row) if row else []


def grant_state(workspace: Path, agent: str, wake: str, now: dt.datetime) -> tuple[bool, str]:
    """(may the wake `wake` of `agent` run, why not): granted, not suspended, not past its end."""
    found = next((g for g in grants(workspace, agent) if g.get("name") == wake), None)
    if found is None:
        return False, f"{agent}: wake `{wake}` has no grant"
    if found.get("status") == "suspended":
        return False, f"{agent}: the grant of wake `{wake}` is suspended"
    ends = machines.parse_ts(found.get("ends"))
    if ends is not None:
        if isinstance(found.get("ends"), dt.date) and not isinstance(found.get("ends"), dt.datetime) \
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(found.get("ends"))):
            ends += dt.timedelta(days=1)  # a date alone means through that day
        if now >= ends:
            return False, f"{agent}: the grant of wake `{wake}` ended {str(found.get('ends'))[:10]}"
    return True, ""


def _resolve_agent(workspace: Path, framework: Path, agent: str) -> ar.Role:
    """The role of an instance name; AgentError for a dev twin, an unknown role or a bad name."""
    role_name, slug, is_dev = ar.split_instance(agent)
    if is_dev:
        raise ar.AgentError("a dev twin has no periodic wake: it wakes on a message")
    roles, _errors = ar.collect(framework, workspace)
    role = next((r for r in roles if r.name == role_name), None)
    if role is None:
        known = ", ".join(r.name for r in roles) or "none"
        raise ar.AgentError(f"no role named {role_name!r} (roles: {known})")
    ar.instance_name(role, slug=slug)  # raises when the name does not fit the role's kind
    return role


def grant(workspace: Path, agent: str, *, wake: str, trigger: str, purpose: str, approval: str,
          duty: str | None = None, cost_per_run_usd: float | None = None, ends: str | None = None,
          framework: Path = FRAMEWORK, now: dt.datetime | None = None) -> dict[str, Any]:
    """Record a wake the owner granted on the agent's registry row; return the grant.

    The grant holds the exact terms the owner approved: the trigger, the duty
    whose check runs first (optional), the purpose, the cost per run and the
    end. `approval` is what the owner said; the tool cannot see an approval,
    so the caller asks first and records the words here. A grant of the same
    name is replaced. The row of a lead or durable role is created when
    missing; a project agent must have its row already. Raises AgentError for
    a bad request, CorruptFileError / WriteRefusedError when the registry
    cannot be written safely.
    """
    now = now or cadence.now_local()
    role = _resolve_agent(workspace, framework, agent)
    if not _WAKE_RE.match(wake or ""):
        raise ar.AgentError(f"wake name {wake!r}: use letters, digits and `- _ . :`, 64 at most")
    for label, value in (("trigger", trigger), ("purpose", purpose), ("approval", approval)):
        if not value or not str(value).strip():
            raise ar.AgentError(f"a grant needs its {label}")
    if duty is not None and role.duty(duty) is None:
        known = ", ".join(d.id for d in role.duties) or "none"
        raise ar.AgentError(f"{role.name!r} has no duty {duty!r} (duties: {known})")
    trigger = " ".join(str(trigger).split())
    if ends is None:
        tail = re.search(r"\buntil\s+(\d{4}-\d{2}-\d{2}(?:[T ]\S+)?)\s*$", trigger)
        ends = tail.group(1) if tail else None
    if ends is not None and machines.parse_ts(ends) is None:
        raise ar.AgentError(f"`ends` {ends!r} is not an ISO date or datetime")
    stamp = now.isoformat(timespec="seconds")
    entry = {"name": wake, "trigger": trigger, "duty": duty, "purpose": " ".join(purpose.split()),
             "cost_per_run_usd": cost_per_run_usd, "ends": ends, "status": "active",
             "approval": " ".join(approval.split()), "granted_at": stamp}
    path, rows = _registry_rows(workspace)
    index = _row_index(rows, agent)
    if index is None:
        if role.kind == "project":
            raise ar.AgentError(f"`{agent}` has no registry row: a project agent exists only "
                                "through its row, so add the row first")
        row = {"name": agent, "role": role.name, "handle": f"agent:{agent}", "project": None,
               "host": None, "model": None, "variant": None, "daily_usd": None, "wakes": [entry],
               "status": DEFAULT_STATUS, "notes": "",
               "provenance": {"source": "agents", "at": stamp}}
        signals.append_row(path, REGISTRY_KEY, row, skeleton=REGISTRY_SKELETON, last_updated=stamp)
    else:
        row = dict(rows[index])
        row["wakes"] = [g for g in _grants(row) if g.get("name") != wake] + [entry]
        signals.replace_row(path, REGISTRY_KEY, index, row, last_updated=stamp)
    machines.beat(workspace, min_interval_s=60)
    return entry


def change_grants(workspace: Path, agent: str, *, wake: str | None = None, action: str,
                  now: dt.datetime | None = None) -> list[str]:
    """`revoke`, `suspend` or `resume` one grant of `agent` (or all, with no `wake`).

    Returns the names changed. Raises AgentError when the agent has no row or
    no such grant.
    """
    now = now or cadence.now_local()
    path, rows = _registry_rows(workspace)
    index = _row_index(rows, agent)
    if index is None:
        raise ar.AgentError(f"`{agent}` has no registry row, so it has no grant")
    row = dict(rows[index])
    current = _grants(row)
    hit = [g["name"] for g in current if wake is None or g["name"] == wake]
    if wake is not None and not hit:
        raise ar.AgentError(f"`{agent}` has no grant named {wake!r}")
    if action == "revoke":
        row["wakes"] = [g for g in current if g["name"] not in hit]
    else:
        status = "suspended" if action == "suspend" else "active"
        hit = [name for name in hit
               if next(g for g in current if g["name"] == name).get("status", "active") != status]
        row["wakes"] = [{**g, "status": status} if g["name"] in hit else g for g in current]
    if hit:
        signals.replace_row(path, REGISTRY_KEY, index, row,
                            last_updated=now.isoformat(timespec="seconds"))
        machines.beat(workspace, min_interval_s=60)
    return hit


# --------------------------------------------------------------- interests

def _interests(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in row.get("interests") or [] if isinstance(i, dict) and i.get("pattern")]


def interest_label(interest: dict[str, Any]) -> str:
    """One interest in a few words: its pattern, the kind of update, the wake it fires."""
    label = str(interest.get("pattern"))
    if interest.get("when"):
        label += f" ({interest['when']})"
    if interest.get("fire"):
        label += f" -> wake `{interest['fire']}`"
    return label


def interests(workspace: Path, agent: str) -> list[dict[str, Any]]:
    """The interests on the registry row of `agent` (its instance name)."""
    rows, _errors = load_registry(workspace)
    row = next((r for r in rows if r.get("name") == agent), None)
    return _interests(row) if row else []


def _interest_role(workspace: Path, framework: Path, agent: str) -> ar.Role:
    """The role of an agent that may keep interests; AgentError for one that may not."""
    _role_name, _slug, is_dev = ar.split_instance(agent)
    if is_dev:
        raise ar.AgentError("a dev twin keeps no interests: it wakes on a message from its "
                            "agent or the owner")
    role = _resolve_agent(workspace, framework, agent)
    roles, _errors = ar.collect(framework, workspace)
    if role.name == ar.check_owner(roles, HELD_SKILL):
        raise ar.AgentError(f"`{agent}` runs the update cycle: it tells the others what moved, "
                            "and keeps no interests of its own")
    return role


def _placeholder(pattern: str, agent: str, row: dict[str, Any] | None) -> str:
    """`project:<slug>` as the project of this agent; any other pattern as it is."""
    if pattern != ar.PROJECT_PLACEHOLDER:
        return pattern
    project = (row or {}).get("project")
    if not project:
        raise ar.AgentError(f"`{agent}` has no project on its row, so `{pattern}` stands for nothing")
    return str(project)


def add_interest(workspace: Path, agent: str, *, pattern: str, when: str | None = None,
                 why: str | None = None, fire: str | None = None, framework: Path = FRAMEWORK,
                 now: dt.datetime | None = None) -> tuple[dict[str, Any], bool]:
    """Record one kind of update `agent` wants to hear about; return (the interest, changed).

    The pattern is a handle or a handle glob; `when` narrows it to one kind of
    update; `why` says what the agent does with it. `fire` names a wake the
    owner granted to the agent with the trigger `fire-only`: the update cycle
    then fires that wake in place of a message. An interest with the same
    pattern and `when` is updated in place. Raises AgentError for a bad
    interest, an agent that keeps none (a dev twin, the role that runs the
    update cycle), a project agent with no row, or a full list.
    """
    now = now or cadence.now_local()
    role = _interest_role(workspace, framework, agent)
    path, rows = _registry_rows(workspace)
    index = _row_index(rows, agent)
    if index is None and role.kind == "project":
        raise ar.AgentError(f"`{agent}` has no registry row: a project agent exists only "
                            "through its row, so add the row first")
    row = dict(rows[index]) if index is not None else None
    pattern = _placeholder(str(pattern or "").strip(), agent, row)
    problem = ar.interest_problem(pattern, when, fire)
    if problem:
        raise ar.AgentError(problem)
    if fire is not None:
        granted = next((g for g in _grants(row or {}) if g.get("name") == fire), None)
        if granted is None:
            raise ar.AgentError(f"`{agent}` has no grant for the wake `{fire}`: an interest fires "
                                "only a wake the owner granted")
        if not str(granted.get("trigger") or "").startswith("fire-only"):
            raise ar.AgentError(f"the wake `{fire}` of `{agent}` runs on a schedule "
                                f"({granted.get('trigger')}); an interest fires a `fire-only` wake")
    stamp = now.isoformat(timespec="seconds")
    current = _interests(row or {})
    old = next((i for i in current if i.get("pattern") == pattern and i.get("when") == when), None)
    reason = " ".join(why.split()) if why and why.strip() else (old or {}).get("why")
    if not reason:
        raise ar.AgentError("an interest needs its why: what the agent does with such an update")
    entry = {"pattern": pattern, "when": when, "why": reason, "fire": fire,
             "added_at": (old or {}).get("added_at") or stamp}
    if old == entry:
        return entry, False
    if old is None and len(current) >= ar.MAX_INTERESTS:
        raise ar.AgentError(f"`{agent}` has {len(current)} interests, the most an agent keeps; "
                            "drop one first")
    updated = [entry if i is old else i for i in current] if old is not None else [*current, entry]
    if row is None:
        row = {"name": agent, "role": role.name, "handle": f"agent:{agent}", "project": None,
               "host": None, "model": None, "variant": None, "daily_usd": None, "wakes": [],
               "interests": updated, "status": DEFAULT_STATUS, "notes": "",
               "provenance": {"source": "agents", "at": stamp}}
        signals.append_row(path, REGISTRY_KEY, row, skeleton=REGISTRY_SKELETON, last_updated=stamp)
    else:
        row["interests"] = updated
        signals.replace_row(path, REGISTRY_KEY, index, row, last_updated=stamp)
    machines.beat(workspace, min_interval_s=60)
    return entry, True


def remove_interest(workspace: Path, agent: str, pattern: str, *, when: str | None = None,
                    now: dt.datetime | None = None) -> int:
    """Drop the interests of `agent` with this pattern (every `when`, or the one given); return how many."""
    now = now or cadence.now_local()
    path, rows = _registry_rows(workspace)
    index = _row_index(rows, agent)
    if index is None:
        raise ar.AgentError(f"`{agent}` has no registry row, so it has no interest")
    row = dict(rows[index])
    pattern = _placeholder(str(pattern or "").strip(), agent, row)
    current = _interests(row)
    kept = [i for i in current
            if not (i.get("pattern") == pattern and (when is None or i.get("when") == when))]
    if len(kept) == len(current):
        raise ar.AgentError(f"`{agent}` has no interest {pattern!r}" + (f" when {when}" if when else ""))
    row["interests"] = kept
    signals.replace_row(path, REGISTRY_KEY, index, row, last_updated=now.isoformat(timespec="seconds"))
    machines.beat(workspace, min_interval_s=60)
    return len(current) - len(kept)


def default_interests(workspace: Path, agent: str, *, framework: Path = FRAMEWORK,
                      now: dt.datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Write the role's own interests onto the row of `agent`; return (those added, what was left out).

    An interest the agent has already (the same pattern and `when`) is left as
    the agent wrote it. One that cannot be recorded, such as a wake to fire
    that has no grant yet, is named in the second list and skipped.
    """
    role = _interest_role(workspace, framework, agent)
    added: list[dict[str, Any]] = []
    notes: list[str] = []
    for default in role.interests:
        try:
            rows, _errors = load_registry(workspace)
            row = next((r for r in rows if r.get("name") == agent), None)
            pattern = _placeholder(default.pattern, agent, row)
            if any(i.get("pattern") == pattern and i.get("when") == default.when for i in _interests(row or {})):
                continue
            entry, _changed = add_interest(workspace, agent, pattern=pattern, when=default.when,
                                           why=default.why, fire=default.fire, framework=framework,
                                           now=now)
        except ar.AgentError as exc:
            notes.append(f"{default.pattern}: {exc}")
            continue
        added.append(entry)
    return added, notes


def interested(workspace: Path, handles: list[str], *, when: str | None = None,
               now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Who to tell about one thing an update cycle moved; one row per agent, by name.

    `handles` are the thing's own handle and the handles it relates to (its
    project, its domain, a contact, tags); `when` is the kind of update. An
    agent is named when a session holds it (`active` or `dormant`) and one of
    its interests matches a handle. Each row says how to deliver: `message`,
    or `fire` with the wake to fire, or `skip` with the reason when the only
    matching interest fires a wake that may not run now (no grant, suspended,
    ended). An interest narrowed to one kind of update matches that kind only.
    """
    now = now or cadence.now_local()
    if when is not None and when not in ar.INTEREST_WHEN:
        raise ar.AgentError(f"`when` {when!r} is not one of {', '.join(ar.INTEREST_WHEN)}")
    given = [str(h).strip() for h in handles or []]
    for handle in given:
        if not _HANDLE_RE.match(handle):
            raise ar.AgentError(f"{handle!r} is not a handle (`<kind>:<slug>`)")
    if not given:
        raise ar.AgentError("give at least one handle")
    prefix = ar.id_prefix(workspace)
    rows, _errors = load_registry(workspace)
    found: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: str(r.get("name"))):
        if row.get("status") not in REACHABLE:
            continue
        hits = [i for i in _interests(row)
                if (i.get("when") is None or i.get("when") == when)
                and any(fnmatch.fnmatchcase(h, str(i["pattern"])) for h in given)]
        if not hits:
            continue
        name = str(row["name"])
        fired = [i for i in hits if i.get("fire")]
        plain = [i for i in hits if not i.get("fire")]
        deliver, wake, reason = "message", None, None
        if fired:
            ok, why_not = grant_state(workspace, name, str(fired[0]["fire"]), now)
            if ok:
                deliver, wake = "fire", str(fired[0]["fire"])
            elif not plain:
                deliver, reason = "skip", why_not
        found.append({"agent": name, "id": ar.agent_id(prefix, name), "deliver": deliver,
                      "wake": wake, "reason": reason,
                      "patterns": sorted({str(i["pattern"]) for i in hits})})
    return found


def render_interested(rows: list[dict[str, Any]]) -> list[str]:
    """One line per agent to tell: its id, how to deliver, what matched."""
    lines: list[str] = []
    for row in rows:
        how = {"message": "message", "fire": f"fire {row['wake']}",
               "skip": f"skip ({row['reason']})"}[row["deliver"]]
        lines.append(f"{row['id']}  {how}  {', '.join(row['patterns'])}")
    return lines


# --------------------------------------------------------------- heartbeat

def _ago(value: Any, now: dt.datetime) -> str:
    when = cadence.as_datetime(value)
    if when is None:
        return "never"
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 90:
        return f"{max(minutes, 0)} min ago"
    return f"{minutes // 60} h ago" if minutes < 48 * 60 else f"{minutes // 1440} day(s) ago"


def heartbeat_lines(workspace: Path, framework: Path = FRAMEWORK, *,
                    now: dt.datetime | None = None) -> list[str]:
    """Lines for the daily briefing about agents that run as sessions of their own.

    [] when no registry row is `active`, `dormant` or `paused` and no wake is
    granted: with no separate agents there is nothing to say. Otherwise: who
    runs where, when the update loop last ran (flagged when it went quiet for
    longer than the light update's period plus half an hour inside its work
    hours, where every slot runs one), and the granted wakes.
    """
    now = now or cadence.now_local()
    data = roster(workspace, framework)
    running = [a for a in data["agents"] if a["status"] in ("active", "dormant", "paused")]
    granted = [(a, g) for a in data["agents"] for g in a["grants"]]
    if not running and not granted:
        return []
    lines: list[str] = []
    if running:
        lines.append("Agents: " + "; ".join(
            f"{a['name']} {a['status']}" + (f" on {a['host']}" if a["host"] else "") for a in running) + ".")
    roles, _errors = ar.collect(framework, workspace)
    owner = ar.check_owner(roles, "update")
    if any(a["name"] == owner and a["status"] == "active" for a in running):
        _text, ctx = cadence.load_context(workspace)
        runs = ctx.get("last_runs") if isinstance(ctx.get("last_runs"), dict) else {}
        line = (f"Update loop: last light update {_ago(runs.get('update_light'), now)}, "
                f"last full {_ago(runs.get('update_full'), now)}.")
        last = cadence.as_datetime(runs.get("update_light"))
        if update_window(workspace, now) is None:
            since = max((t for t in (last, window_opened(workspace, now)) if t is not None),
                        default=None)
            limit = light_period(workspace) + dt.timedelta(minutes=HEARTBEAT_SLACK_MINUTES)
            if since is None or now - since > limit:
                line += (f" No light update in over {int(limit.total_seconds() // 60)} minutes of work "
                         f"hours, where every slot runs one: look at the {owner} agent.")
        lines.append(line)
    active = [f"{a['name']} `{g['name']}` ({g['trigger']})" for a, g in granted
              if g.get("status", "active") == "active"]
    paused = [f"{a['name']} `{g['name']}`" for a, g in granted if g.get("status") == "suspended"]
    if active:
        lines.append("Granted wakes: " + "; ".join(active) + ".")
    if paused:
        lines.append("Suspended grants: " + "; ".join(paused) + ".")
    keen = [f"{a['name']}: " + ", ".join(interest_label(i) for i in a["interests"])
            for a in running if a["interests"]]
    if keen:
        lines.append("Interests (what the update cycle tells each agent): " + "; ".join(keen) + ".")
    return lines


# ---------------------------------------------------------------- the row

def set_row(workspace: Path, agent: str, *, framework: Path = FRAMEWORK,
            now: dt.datetime | None = None, source: str = "agents", **fields: Any) -> dict[str, Any]:
    """Create or update the registry row of `agent` (its instance name); return the row.

    `fields` may hold `host`, `model`, `variant`, `daily_usd`, `status`,
    `project`, `notes`; a field that is not given keeps its value. A new row
    starts `off`, and the row of a project agent names its Project
    (`project:<slug>`) unless told otherwise. Grants are never written here
    (`grant`). Raises AgentError for a dev twin, an unknown role, a field that
    is not a row field or a status that is not one.
    """
    now = now or cadence.now_local()
    role = _resolve_agent(workspace, framework, agent)
    unknown = sorted(set(fields) - set(ROW_FIELDS))
    if unknown:
        raise ar.AgentError(f"not a field of a registry row: {', '.join(unknown)} "
                            f"(fields: {', '.join(ROW_FIELDS)})")
    if fields.get("status") is not None and fields["status"] not in STATUSES:
        raise ar.AgentError(f"status {fields['status']!r} is not one of {', '.join(STATUSES)}")
    stamp = now.isoformat(timespec="seconds")
    path, rows = _registry_rows(workspace)
    index = _row_index(rows, agent)
    if index is None:
        _role, slug, _dev = ar.split_instance(agent)
        row = {"name": agent, "role": role.name, "handle": f"agent:{agent}",
               "project": f"project:{slug}" if slug else None, "host": None, "model": None,
               "variant": None, "daily_usd": None, "wakes": [], "status": DEFAULT_STATUS,
               "notes": "", "provenance": {"source": source, "at": stamp}}
        row.update(fields)
        signals.append_row(path, REGISTRY_KEY, row, skeleton=REGISTRY_SKELETON, last_updated=stamp)
    else:
        row = dict(rows[index])
        if all(row.get(k) == v for k, v in fields.items()):
            return row
        row.update(fields)
        signals.replace_row(path, REGISTRY_KEY, index, row, last_updated=stamp)
    machines.beat(workspace, min_interval_s=60)
    return row


# ------------------------------------------------------- one fetching machine

def may_fetch(workspace: Path, framework: Path = FRAMEWORK) -> tuple[bool, str]:
    """(may this machine run the update cycle, why not).

    One machine fetches: when the registry row of the role that runs the
    update is `active` and names a `host`, the cycle belongs to that machine,
    and any other machine reads the stores it fills. With no such row (no
    separate Updater runs) every machine may fetch.
    """
    roles, _errors = ar.collect(framework, workspace)
    owner = ar.check_owner(roles, "update")
    rows, _problems = load_registry(workspace)
    row = next((r for r in rows if r.get("name") == owner), None)
    if not row or row.get("status") != "active" or not row.get("host"):
        return True, ""
    here = machines.machine_name(create=False)
    if row["host"] == here:
        return True, ""
    return False, (f"the {owner} agent runs on {row['host']}: this machine ({here}) does not fetch; "
                   "the stores are kept current from there")


# --------------------------------------------------------------------- CLI

def _aware(value: str) -> dt.datetime:
    parsed = cadence.as_datetime(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """The `agents` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    common.add_argument("--framework", type=Path, default=FRAMEWORK, help=argparse.SUPPRESS)
    common.add_argument("--now", type=_aware, default=None, help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="agents", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", parents=[common], help="The roster: roles, instances, ids, dev twins.")
    ls.add_argument("--json", action="store_true", help="Print the roster as JSON.")
    ident = sub.add_parser("id", parents=[common], help="Print the id of an agent.")
    ident.add_argument("name", help="`<role>`, `<role>:dev` or `project:<slug>`.")
    ident.add_argument("--dev", action="store_true", help="The id of the role's dev twin.")
    du = sub.add_parser("duties", parents=[common], help="Duties with schedule, last run and state.")
    du.add_argument("--agent", default=None, help="Only this role's duties.")
    which = du.add_mutually_exclusive_group()
    which.add_argument("--due", action="store_true",
                       help="Only duties a scheduler would run now: due, overdue or unfinished.")
    which.add_argument("--overdue", action="store_true", help="Only duties that are late.")
    du.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    ch = sub.add_parser("check", parents=[common],
                        help="Zero-cost check of one duty: exit 0 due, exit 1 not due.")
    ch.add_argument("agent")
    ch.add_argument("duty", nargs="?", default=None)
    ch.add_argument("--wake", default=None,
                    help="The granted wake this check runs for: no grant, no run.")
    gr = sub.add_parser("grant", parents=[common],
                        help="Record a wake the owner granted, or suspend / resume grants.")
    gr.add_argument("agent", help="`<role>` or `project:<slug>`.")
    gr.add_argument("--wake", default=None, help="The wake's name.")
    gr.add_argument("--trigger", default=None, help='When it runs: "every 30m", "cron ...", "... until 2027-03-01".')
    gr.add_argument("--purpose", default=None, help="Why, in a line.")
    gr.add_argument("--approval", default=None, help="The owner's words that approved these exact terms.")
    gr.add_argument("--duty", default=None, help="The duty whose check runs before the wake.")
    gr.add_argument("--cost", type=float, default=None, dest="cost", help="Expected cost per run, in USD.")
    gr.add_argument("--ends", default=None, help="ISO date or datetime the grant ends.")
    switch = gr.add_mutually_exclusive_group()
    switch.add_argument("--suspend", action="store_true", help="Switch the grant (or all) off.")
    switch.add_argument("--resume", action="store_true", help="Switch the grant (or all) back on.")
    rv = sub.add_parser("revoke", parents=[common], help="Remove a grant from the agent's row.")
    rv.add_argument("agent")
    rv.add_argument("--wake", required=True)
    st = sub.add_parser("set", parents=[common], help="Create or update an agent's registry row.")
    st.add_argument("agent", help="`<role>` or `project:<slug>`.")
    st.add_argument("--host", default=None, help="The machine that runs it.")
    st.add_argument("--status", default=None, choices=STATUSES)
    st.add_argument("--model", default=None)
    st.add_argument("--variant", default=None)
    st.add_argument("--daily-usd", type=float, default=None, dest="daily_usd")
    st.add_argument("--project", default=None, help="The Project's handle, for a project agent.")
    st.add_argument("--notes", default=None)
    it = sub.add_parser("interest", parents=[common],
                        help="An agent's interests: what the update cycle should tell it about.")
    it.add_argument("agent", help="`<role>` or `project:<slug>`.")
    what = it.add_mutually_exclusive_group()
    what.add_argument("--add", default=None, metavar="PATTERN",
                      help="A handle or a handle glob: project:kitchen-reno, domain:finances, bill:*.")
    what.add_argument("--remove", default=None, metavar="PATTERN", help="Drop this interest.")
    what.add_argument("--defaults", action="store_true", help="Write the role's own interests.")
    it.add_argument("--when", default=None, choices=ar.INTEREST_WHEN,
                    help="Only this kind of update (default: any).")
    it.add_argument("--why", default=None, help="What the agent does with such an update.")
    it.add_argument("--fire", default=None, metavar="WAKE",
                    help="Fire this granted `fire-only` wake in place of a message.")
    it.add_argument("--json", action="store_true", help="Print the agent's interests as JSON.")
    who = sub.add_parser("interested", parents=[common],
                         help="Who to tell about one thing an update cycle moved.")
    who.add_argument("--handle", action="append", required=True, dest="handles", metavar="HANDLE",
                     help="The thing's own handle, and each handle it relates to; repeatable.")
    who.add_argument("--when", default=None, choices=ar.INTEREST_WHEN, help="The kind of update.")
    who.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    sub.add_parser("heartbeat", parents=[common],
                   help="Lines for the briefing about agents that run as sessions of their own.")
    sub.add_parser("may-fetch", parents=[common],
                   help="Exit 0 when this machine may run the update cycle.")
    return parser


def _warn(errors: list[str]) -> None:
    for line in errors:
        print(f"agents: {line}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or args.framework.parent / "workspace"
    try:
        if args.command == "list":
            data = roster(workspace, args.framework)
            _warn(data["errors"])
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for line in render_roster(data):
                    print(line)
            return 0
        if args.command == "id":
            roles, errors = ar.collect(args.framework, workspace)
            _warn(errors)
            print(ar.resolve_id(args.name, roles, ar.id_prefix(workspace), dev=args.dev))
            return 0
        if args.command == "duties":
            rows, errors = duty_rows(workspace, args.framework, now=args.now, agent=args.agent)
            _warn(errors)
            if args.overdue:
                rows = [r for r in rows if r["state"] in LATE]
            elif args.due:
                rows = [r for r in rows if r["state"] in RUNNABLE and not r.get("held")]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for line in render_duties(rows):
                    print(line)
            return 0
        if args.command == "heartbeat":
            for line in heartbeat_lines(workspace, args.framework, now=args.now):
                print(line)
            return 0
        if args.command == "interest":
            if args.add:
                entry, changed = add_interest(workspace, args.agent, pattern=args.add, when=args.when,
                                              why=args.why, fire=args.fire,
                                              framework=args.framework, now=args.now)
                print(f"{args.agent}: interest {interest_label(entry)} "
                      + ("recorded" if changed else "is there already"))
            elif args.remove:
                count = remove_interest(workspace, args.agent, args.remove, when=args.when, now=args.now)
                print(f"{args.agent}: {count} interest(s) removed")
            elif args.defaults:
                added, notes = default_interests(workspace, args.agent, framework=args.framework,
                                                 now=args.now)
                for entry in added:
                    print(f"{args.agent}: interest {interest_label(entry)} recorded")
                if not added:
                    print(f"{args.agent}: the role's interests are there already"
                          if not notes else f"{args.agent}: no interest added")
                _warn(notes)
            elif args.json:
                print(json.dumps(interests(workspace, args.agent), indent=2, default=str))
            else:
                for entry in interests(workspace, args.agent):
                    print(f"{interest_label(entry)}  {entry.get('why') or ''}".rstrip())
            return 0
        if args.command == "interested":
            rows = interested(workspace, args.handles, when=args.when, now=args.now)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for line in render_interested(rows):
                    print(line)
            return 0
        if args.command == "may-fetch":
            ok, why = may_fetch(workspace, args.framework)
            if not ok:
                print(why)
            return 0 if ok else 1
        if args.command == "set":
            given = {k: getattr(args, k) for k in ROW_FIELDS if getattr(args, k) is not None}
            row = set_row(workspace, args.agent, framework=args.framework, now=args.now, **given)
            print(f"{args.agent}: status {row.get('status')}, host {row.get('host') or '-'}")
            return 0
        if args.command == "revoke":
            for name in change_grants(workspace, args.agent, wake=args.wake, action="revoke", now=args.now):
                print(f"{args.agent}: grant `{name}` removed")
            return 0
        if args.command == "grant":
            if args.suspend or args.resume:
                action = "suspend" if args.suspend else "resume"
                changed = change_grants(workspace, args.agent, wake=args.wake, action=action, now=args.now)
                for name in changed:
                    print(f"{args.agent}: grant `{name}` {'suspended' if args.suspend else 'resumed'}")
                return 0
            missing = [f"--{k}" for k in ("wake", "trigger", "purpose", "approval") if not getattr(args, k)]
            if missing:
                raise ar.AgentError(f"a grant needs {', '.join(missing)}")
            entry = grant(workspace, args.agent, wake=args.wake, trigger=args.trigger,
                          purpose=args.purpose, approval=args.approval, duty=args.duty,
                          cost_per_run_usd=args.cost, ends=args.ends, framework=args.framework,
                          now=args.now)
            check_cmd = " ".join(filter(None, [
                "uv run python -m superagent.tools.agents check", args.agent, entry["duty"],
                "--wake", entry["name"]]))
            print(f"{args.agent}: wake `{entry['name']}` granted ({entry['trigger']})")
            print(f"its check: {check_cmd}")
            return 0
        due, why = check(workspace, args.agent, args.duty, args.framework, now=args.now,
                         wake=args.wake)
        if due:
            print(why)
        return 0 if due else 1
    except ar.AgentError as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 2
    except CheckFailedError as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 3
    except (signals.CorruptFileError, signals.WriteRefusedError) as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 1
    except (cadence.CorruptFileError, OSError) as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 3 if args.command == "check" else 1


if __name__ == "__main__":
    sys.exit(main())
