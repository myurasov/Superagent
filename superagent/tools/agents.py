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
                        exit 1 when it is not (nothing is printed)

A duty's state is one of: `ok`; `due` (its scheduled time has come and it has
not run since, or its check found work: the stamp against the schedule,
`tools/cadence.py`); `overdue` (late by the nag rules of `cadence status`);
`unfinished` (a run began and never finished); `on-request` (no check).
`check` exits 0 for due, overdue and unfinished alike.

Ids are `<prefix>:<instance name>`; the prefix is
`config.preferences.autocode.id_prefix` (default `sa`). The registry holds
names, never ids, so changing the prefix changes every id at once.

This tool reads only. It works with Autocode off and never talks to an
Autocode server.

Exit codes: 0 ok (`check`: due); 1 runtime error (`check`: not due); 2 usage
error (unknown agent or duty, a bad prefix or name); 3 `check` could not run
(an unreadable file), so a scheduler sees a failed check and never mistakes it
for "not due".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import cadence, inbox
from superagent.tools import watchlist as wl

FRAMEWORK = Path(__file__).resolve().parent.parent
REGISTRY_REL = "_memory/agents.yaml"
STATUSES = ("off", "active", "dormant", "paused", "retired")
DEFAULT_STATUS = "off"
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
    """(model, variant): the registry row's own, else the configured default of the tier."""
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
        found: list[str] = []
        due = [row["id"] for row in wl.due_rows(workspace, framework, now=now) if row["due"]]
        if due:
            found.append(f"{len(due)} watcher(s) due: {_names(due)}")
        files = inbox.waiting(workspace, now=now)
        if files:
            found.append(f"{len(files)} document(s) waiting in a drop folder")
        return ("due", "; ".join(found)) if found else ("ok", "")
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
        table.append((row["agent"], row["duty"], row["skill"], row["tier"], schedule,
                      str(row["last_run"] or "-")[:10], state))
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]) - 1)]
    return ["  ".join([cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])] + [row[-1]]).rstrip()
            for row in table]


def check(workspace: Path, agent: str, duty_id: str, framework: Path = FRAMEWORK, *,
          now: dt.datetime | None = None) -> tuple[bool, str]:
    """(is the duty due, why). Raises AgentError for an unknown agent or duty."""
    rows, _errors = duty_rows(workspace, framework, now=now, agent=agent)
    row = next((r for r in rows if r["duty"] == duty_id), None)
    if row is None:
        known = ", ".join(r["duty"] for r in rows) or "none"
        raise ar.AgentError(f"{ar.split_instance(agent)[0]!r} has no duty {duty_id!r} "
                            f"(duties: {known})")
    due = row["state"] in RUNNABLE
    return due, f"{row['agent']} {row['duty']}: {row['state']}: {row['reason']}" if due else ""


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
    ch.add_argument("duty")
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
                rows = [r for r in rows if r["state"] in RUNNABLE]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for line in render_duties(rows):
                    print(line)
            return 0
        due, why = check(workspace, args.agent, args.duty, args.framework, now=args.now)
        if due:
            print(why)
        return 0 if due else 1
    except ar.AgentError as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 2
    except (cadence.CorruptFileError, OSError) as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 3 if args.command == "check" else 1


if __name__ == "__main__":
    sys.exit(main())
