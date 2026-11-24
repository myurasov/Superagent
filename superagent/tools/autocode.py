#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Autocode mode: start, park and stop agents that run as registered sessions.

Autocode is an optional runtime (`rules/autocode.md`, `contracts/agents.md`
§ 9): an agent runs as an OpenCode session registered with an Autocode server
under its id, and its duties run from the wakes the owner granted. This is
the ONE module that calls the `autocode` and `opencode` commands. Nothing else
in the framework does, `tools/agents.py` never imports it, and every other
tool works with Autocode off.

  setup              What the `start` skill reads first: is Autocode usable
                     here, is the mode on, is it set up, and the proposed
                     settings (agents, their wakes, work hours, cost cap,
                     model). `--json` for the data. It writes nothing.
  setup enable       Turn Autocode mode on (`setup disable`: off). Writes the
                     switch in `config.yaml`, that line only.
  setup apply        Record the settings the owner approved: config values,
                     a registry row per chosen agent, and a grant per wake
                     with the owner's words. A time the owner changed
                     (`--wake AGENT:NAME=TIME`, a cadence value or a trigger)
                     goes to the grant AND to `preferences.cadences`, so the
                     duty's due time and the wake agree. A wake that is
                     already granted is left as it is. `--dry-run` shows and
                     writes nothing.
  mark <agent>       After this session registered as the agent: mark its
                     registry row (`active` with granted wakes, `dormant`
                     without) and its host. `--off` after it unregistered.
  check <agent> [<duty>] --wake <name>
                     The check a granted wake runs first, under Autocode:
                     the daily cost cap, then `agents check`. Exit 0 due,
                     1 skip (nothing due, no grant, held, or the cap is
                     reached), 3 the check could not say. Wakes call it
                     through `tools/wake_check.sh`.
  me                 Is this session a registered agent? Prints its id and
                     exits 0, or exits 1.
  prompt <agent>     Print the start prompt of an agent (`--dev`: of its dev
                     twin): register under the built id, read the rule and
                     the role file, set the granted wakes and no other.
  start <agent>      Start a session for the agent in this checkout
                     (`opencode run`, with the model of its tier) and give it
                     the start prompt; then mark its registry row (`active`
                     with granted wakes, `dormant` without) and its host.
                     `--print` shows the command and the prompt and starts
                     nothing. Refused unless `preferences.autocode.enabled`.
  park <agent>       Ask the agent to park (write its state, compact, go
                     idle); the row becomes `dormant`.
  stop <agent>       Ask the agent to unregister; the row becomes `off`.
  retire <agent>     Ask the agent to write its closing note and unregister;
                     the row becomes `retired` and its grants are suspended.
  wakes [<agent>]    Compare the wakes that are set with the grants on the
                     registry rows: a wake with no grant, a grant with no
                     wake, a trigger that differs. Needs the owner's shell.
  status             The roster against what is registered. Needs the owner's
                     shell.
  settings           Print optional Autocode settings lines for this prefix
                     (hardening; nothing needs them). It sets nothing: only
                     the owner sets Autocode settings.
  answer <ask-id> --answer TEXT
                     Record the owner's answer on an open ask (the record is
                     `tools/asks.py`'s) and tell the agent that asked, when a
                     session holds it: one message naming the ask. Nobody has
                     to carry the answer over.
  lock-name browser <profile>
                     The name of the Autocode lock two agents share for one
                     browser profile.

No step asks the owner to edit a file or to run a command: the `start` skill
asks in question cards and this tool does the writes (`setup enable`, `setup
apply`, `mark`). Autocode's own settings stay at their defaults; the daily
cost cap is kept by the wake's check, which needs no Autocode setting.

Who can run what. In an agent's own shell the `autocode` command only sends
messages, fires wakes and says who it is; listing agents and wakes is the
owner's (the server's machine, in a terminal). `wakes` and `status` say so
when they are refused. An agent audits its own wakes with its Autocode tool.

Exit codes: 0 ok (`me`: registered); 1 a runtime problem (`me`: not
registered; a message that could not be sent; `wakes`: something differs);
2 usage error (unknown agent, Autocode mode off); 3 the `autocode` or
`opencode` command is missing, or refused this shell.

CLI:
    uv run python -m superagent.tools.autocode setup [--json]
    uv run python -m superagent.tools.autocode setup apply --approval "<the owner's words>" --agents updater
    uv run python -m superagent.tools.autocode start updater [--print]
    uv run python -m superagent.tools.autocode park project:<slug>
    uv run python -m superagent.tools.autocode wakes [updater]
    uv run python -m superagent.tools.autocode settings
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import agent_roles as ar
from superagent.tools import agents, asks, cadence, machines, signals

FRAMEWORK = Path(__file__).resolve().parent.parent
RULE_REL = "superagent/rules/autocode.md"
#: Environment of the calling session that must not leak into a session it starts.
STRIP_ENV_PREFIXES = ("AUTOCODE_", "OPENCODE")
START_TIMEOUT_S = 900
CLI_TIMEOUT_S = 60
BROWSER_LOCK = "browser"
CONFIG_REL = "_memory/config.yaml"
#: The script a wake's check goes through: it finds `uv` whatever PATH the runtime has.
WAKE_CHECK = "sh superagent/tools/wake_check.sh"
#: What `setup` may write in config.yaml: setting -> the key's path under `preferences`.
SETTABLE: dict[str, tuple[str, ...]] = {
    "enabled": ("autocode", "enabled"),
    "daily_usd": ("autocode", "daily_usd"),
    "runner_model": ("autocode", "models", "runner", "model"),
    "runner_variant": ("autocode", "models", "runner", "variant"),
    "work_hours": ("update", "work_hours"),
}
#: Proposed when no work hours are set: scheduled updates stay out of the night.
PROPOSED_WORK_HOURS = "06:00-23:00"
#: Wakes another agent fires, by role: (name, trigger, purpose). The role file says who fires it.
ON_EVENT_WAKES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "bookkeeper": (("after-harvest", "fire-only",
                    "reconcile and review the new rows after a bank harvest"),),
}
_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_CRON_CLOCK_RE = re.compile(r'^cron\s+"(\d{1,2})\s+(\d{1,2})\s')
_LINE_RE = re.compile(r"""^(?P<head>\s*[^\s:#][^:#]*:)[ \t]*(?P<value>"(?:[^"\\]|\\.)*"|'[^']*'|[^#]*?)"""
                      r"""(?P<tail>[ \t]+#.*)?$""")
_EVERY_TRIGGER_RE = re.compile(
    r"^every\s+((?:\d+\s*[smhd])+)(?:\s+from\s+\d{1,2}:\d{2})?(?:\s+until\s+\S.*)?$", re.IGNORECASE)
_CRON_TRIGGER_RE = re.compile(
    r'^cron\s+"(\d{1,2})\s+(\d{1,2})\s+(\*|\d{1,2})\s+\*\s+(\*|[0-7])"(?:\s+until\s+\S.*)?$', re.IGNORECASE)
_CRON_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_KEEP: Any = object()  # "this setting was not given": keep what the config has


class Unavailable(RuntimeError):
    """The `autocode` / `opencode` command is missing, or refused this shell."""


class ConfigError(RuntimeError):
    """`config.yaml` could not be changed safely; nothing was written."""


# ------------------------------------------------------------------- running

def _binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path.home() / f".{name}" / "bin" / name
    if fallback.is_file():
        return str(fallback)
    raise Unavailable(f"the `{name}` command is not installed on this machine")


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
         timeout: int = CLI_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    """Run one command (an explicit argument list, no shell). Tests replace this."""
    return subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd, env=env,
                          timeout=timeout)


def _autocode(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return _run([_binary("autocode"), *args])
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"`autocode {args[0]}` could not run ({exc})") from exc


def _owner_json(args: list[str]) -> Any:
    """Output of an owner-only `autocode ... --json` command. Raises Unavailable when refused."""
    result = _autocode([*args, "--json"])
    if result.returncode != 0:
        why = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise Unavailable(
            f"`autocode {args[0]}` was refused here ({why[0][:160]}). It needs the owner's shell: "
            "a terminal on the Autocode server's machine. An agent lists its own wakes with its "
            "Autocode tool")
    try:
        return json.loads(result.stdout or "null")
    except ValueError as exc:
        raise Unavailable(f"`autocode {args[0]}` printed no JSON") from exc


# --------------------------------------------------------------------- facts

def me() -> str | None:
    """The agent id this session is registered under; None when it is not registered."""
    result = _autocode(["me", "--json"])
    try:
        data = json.loads(result.stdout or "null")
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("registered"):
        return None
    agent = data.get("agent")
    return str(agent.get("id")) if isinstance(agent, dict) and agent.get("id") else None


def lock_name(workspace: Path, kind: str, name: str) -> str:
    """`<prefix>:<kind>:<name>`: the Autocode lock agents of this workspace share for one thing."""
    if not ar.SLUG_RE.match(name):
        raise ar.AgentError(f"{name!r} is not a lock name part (lowercase letters, digits and `-`)")
    return f"{ar.id_prefix(workspace)}:{kind}:{name}"


def _agent(workspace: Path, framework: Path, name: str, *, dev: bool = False) -> dict[str, Any]:
    """Everything `start` needs about an agent: ids, role file, model, grants."""
    roles, _errors = ar.collect(framework, workspace)
    role_name, slug, is_dev = ar.split_instance(name)
    dev = dev or is_dev
    role = next((r for r in roles if r.name == role_name), None)
    if role is None:
        known = ", ".join(r.name for r in roles) or "none"
        raise ar.AgentError(f"no role named {role_name!r} (roles: {known})")
    instance = ar.instance_name(role, slug=None if dev else slug, dev=dev)
    data = agents.roster(workspace, framework)
    row = next((a for a in data["agents"] if a["name"] == name), None)
    if not dev and role.kind == "project" and row is None:
        raise ar.AgentError(f"`{name}` has no registry row: create the agent first "
                            f"(`uv run python -m superagent.tools.agents set {name}`)")
    tier = ar.autocode_preferences(workspace).get("models")
    tier = (tier.get("dev") if isinstance(tier, dict) else None) or {}
    return {
        "name": instance, "id": ar.agent_id(data["prefix"], instance), "role": role, "dev": dev,
        "slug": slug,
        "model": (tier.get("model") if dev else (row or {}).get("model")),
        "variant": (tier.get("variant") if dev else (row or {}).get("variant")),
        "grants": [] if dev else [g for g in (row or {}).get("grants", [])
                                  if g.get("status", "active") == "active"],
        "lead_id": ar.agent_id(data["prefix"], (ar.lead(roles) or role).name),
    }


def _rel(path: Path, framework: Path) -> str:
    try:
        return path.relative_to(framework.parent).as_posix()
    except ValueError:
        return str(path)


def check_command(agent: str, grant: dict[str, Any]) -> str:
    """The zero-cost check a granted wake runs first (`contracts/agents.md` § 8).

    It goes through `tools/wake_check.sh` (which finds `uv` on any PATH) to
    `autocode check`: the daily cost cap, then `agents check ... --wake`.
    """
    parts = [WAKE_CHECK, agent]
    if grant.get("duty"):
        parts.append(str(grant["duty"]))
    return " ".join([*parts, "--wake", str(grant["name"])])


def wake_text(info: dict[str, Any], grant: dict[str, Any]) -> str:
    """What a granted wake tells the agent when it fires."""
    duty = info["role"].duty(grant["duty"]) if grant.get("duty") else None
    if duty is not None:
        return (f"Duty `{duty.id}` is due (its check's reason is above). Run the skill "
                f"`{duty.skill}` as your role file says for this duty, then go idle.")
    return f"Wake `{grant['name']}`: {grant.get('purpose') or 'the work this wake was granted for'}. " \
           "Do that, then park."


def start_prompt(workspace: Path, framework: Path, name: str, *, dev: bool = False) -> str:
    """The prompt a new session is started with: who it is, what to read, which wakes to set."""
    info = _agent(workspace, framework, name, dev=dev)
    role: ar.Role = info["role"]
    lines = [
        f"You are being started as the Superagent agent `{info['name']}`, to run as a session "
        "registered with Autocode. Do these steps in order and nothing else.",
        "",
        f"1. Register with Autocode under exactly this id: `{info['id']}` (tool `autocode_me`, "
        f"action `register`; description: \"{role.description[:160]}\"). If the answer is "
        "`id_taken`, another session holds the id: say so, stop, and do nothing else. Never "
        "register under another id.",
        f"2. Read `{RULE_REL}` and follow it from now on.",
    ]
    if info["dev"]:
        dev_path = role.dev_path or role.path.with_name(role.path.name.replace(".agent.md", ".agent.dev.md"))
        lines.append(f"3. You are the dev twin of the role `{role.name}`: read `{_rel(dev_path, framework)}` "
                     f"and `{_rel(role.path, framework)}`, and `superagent/rules/framework-changes.md` "
                     "before any change to the framework.")
        lines.append("4. Set no wake. A dev twin wakes on a message.")
    else:
        step = f"3. Read your role file `{_rel(role.path, framework)}`"
        if role.overlay is not None:
            step += f" and its overlay `{_rel(role.overlay, framework)}`"
        if info["slug"]:
            step += (f". Your project is `project:{info['slug']}`: read its `info.md` and `status.md` "
                     "and the last entries of its `history.md`")
        lines.append(step + ".")
        if info["grants"]:
            lines.append("4. Set exactly these wakes (tool `autocode_wake`, action `set`), and no "
                         "other periodic wake:")
            for grant in info["grants"]:
                lines.append(f"   - name `{grant['name']}`; trigger `{grant['trigger']}`; check "
                             f"`{check_command(info['name'], grant)}`; text: \"{wake_text(info, grant)}\"")
        else:
            lines.append("4. Set no wake: none is granted to you. You wake on a message.")
    step = 5
    if not info["dev"] and role.interests:
        lines.append(f"{step}. Write your role's interests onto your row, so the Updater tells you "
                     "what moved for you: `uv run python -m superagent.tools.agents interest "
                     f"{info['name']} --defaults`.")
        step += 1
    lines.append(f"{step}. Send one message to `{info['lead_id']}`: `Started: {info['name']}`."
                 if info["id"] != info["lead_id"] else
                 f"{step}. Tell the owner in one report message that you are started.")
    lines.append(f"{step + 1}. Do no other work now. End your turn and go idle: you will be woken.")
    return "\n".join(lines)


# ---------------------------------------------------------------------- setup

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def set_config(workspace: Path, setting: str, value: Any) -> bool:
    """Set one known setting (a key of `SETTABLE`) in `config.yaml`. True when the file changed."""
    return _write_config(workspace, ("preferences", *SETTABLE[setting]), value)


def set_cadence(workspace: Path, key: str, value: str) -> bool:
    """Set `preferences.cadences.<key>` in `config.yaml`. True when the file changed."""
    return _write_config(workspace, ("preferences", "cadences", key), value)


def _write_config(workspace: Path, keys: tuple[str, ...], value: Any) -> bool:
    """Set one key of `config.yaml`, editing that line only. True when the file changed.

    The key's line is rewritten with its comment kept; a key that is missing
    is added at the end of its block. The result is parsed and compared with
    the old content plus the new value before anything is written. Raises
    ConfigError when the file or the block cannot be read, when the key holds
    a value over several lines, or when the edit would change anything else.
    """
    path = workspace / CONFIG_REL
    try:
        text = path.read_text(encoding="utf-8")
        before = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{CONFIG_REL} cannot be read ({exc})") from exc
    where = ".".join(keys[:-1])
    node: Any = before
    for part in keys[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        raise ConfigError(f"{CONFIG_REL} has no `{where}` block")
    if keys[-1] in node and node[keys[-1]] == value and type(node[keys[-1]]) is type(value):
        return False
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lo, hi, indent = 0, len(lines), 0
    for depth, part in enumerate(keys):
        key_re = re.compile(rf"^ {{{indent}}}{re.escape(part)}\s*:")
        hit = next((j for j in range(lo, hi) if key_re.match(lines[j])), None)
        if depth == len(keys) - 1:
            rendered = json.dumps(value)
            if hit is None:
                body = [j for j in range(lo, hi)
                        if lines[j].strip() and not lines[j].lstrip().startswith("#")]
                lines.insert(body[-1] + 1 if body else lo, f"{' ' * indent}{part}: {rendered}\n")
                break
            found = _LINE_RE.match(lines[hit].rstrip("\n"))
            after = next((j for j in range(hit + 1, hi)
                          if lines[j].strip() and not lines[j].lstrip().startswith("#")), None)
            spans = after is not None and (_indent(lines[after]) > indent
                                           or lines[after].lstrip().startswith("- "))
            if found is None or (not found.group("value").strip() and spans):
                raise ConfigError(f"{CONFIG_REL}: `{'.'.join(keys)}` holds a value over several "
                                  "lines; nothing written")
            lines[hit] = f"{found.group('head')} {rendered}{found.group('tail') or ''}\n"
            break
        if hit is None:
            raise ConfigError(f"{CONFIG_REL} has no `{where}` block")
        end = next((j for j in range(hit + 1, hi) if lines[j].strip()
                    and not lines[j].lstrip().startswith("#") and _indent(lines[j]) <= indent), hi)
        inner = [j for j in range(hit + 1, end)
                 if lines[j].strip() and not lines[j].lstrip().startswith("#")]
        if not inner:
            raise ConfigError(f"{CONFIG_REL}: `{'.'.join(keys[:depth + 1])}` is not a block mapping")
        lo, hi, indent = hit + 1, end, _indent(lines[inner[0]])
    new_text = "".join(lines)
    expected = copy.deepcopy(before)
    node = expected
    for part in keys[:-1]:
        node = node[part]
    node[keys[-1]] = value
    try:
        same = yaml.safe_load(new_text) == expected
    except yaml.YAMLError:
        same = False
    if not same:
        raise ConfigError(f"{CONFIG_REL}: setting `{'.'.join(keys)}` would change another key; "
                          "nothing written")
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)
    machines.beat(workspace, min_interval_s=60)
    return True


def trigger_for(schedule: cadence.Schedule) -> str:
    """The Autocode trigger that runs at a cadence schedule's times (the server's time zone)."""
    if schedule.kind == "every":
        minutes = max(int((schedule.every or dt.timedelta()).total_seconds() // 60), 1)
        for unit, size in (("d", 1440), ("h", 60)):
            if minutes % size == 0:
                return f"every {minutes // size}{unit}"
        return f"every {minutes}m"
    clock = f"{schedule.minute} {schedule.hour}"
    if schedule.kind == "weekly":
        return f'cron "{clock} * * {((schedule.weekday or 0) + 1) % 7}"'
    if schedule.kind == "monthly":
        return f'cron "{clock} {schedule.day} * *"'
    return f'cron "{clock} * * *"'


def cadence_for(trigger: str) -> str | None:
    """The cadence value whose times a trigger runs at; None when no cadence value says it.

    `cron "0 8 * * *"` is `08:00`, `cron "0 18 * * 0"` is `Sunday 18:00`,
    `cron "0 12 1 * *"` is `1st 12:00`, `every 2h from 08:00` is `every 2
    hours` (an anchor and an end stay in the trigger; a cadence value has
    neither). A trigger with a list, a range, a step or a time zone has no
    cadence value: the duty's due time could not follow it.
    """
    text = " ".join(str(trigger or "").split())
    every = _EVERY_TRIGGER_RE.match(text)
    if every:
        seconds = sum(int(count) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit.lower()]
                      for count, unit in re.findall(r"(\d+)\s*([smhd])", every.group(1), re.IGNORECASE))
        if seconds <= 0 or seconds % 60:
            return None
        minutes = seconds // 60
        unit, size = next((u, s) for u, s in (("day", 1440), ("hour", 60), ("minute", 1)) if minutes % s == 0)
        return f"every {minutes // size} {unit}{'' if minutes // size == 1 else 's'}"
    cron = _CRON_TRIGGER_RE.match(text)
    if not cron or (cron.group(3) != "*" and cron.group(4) != "*"):
        return None
    clock = f"{int(cron.group(2)):02d}:{int(cron.group(1)):02d}"
    if cron.group(3) != "*":
        day = int(cron.group(3))
        suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        clock = f"{day}{suffix} {clock}"
    elif cron.group(4) != "*":
        clock = f"{_CRON_DAYS[int(cron.group(4)) % 7]} {clock}"
    return clock if cadence.parse_schedule(clock) is not None else None


def _in_step(trigger: str, wake: dict[str, Any]) -> bool:
    """True when a wake's trigger runs at the times its duty is due by (its cadence value)."""
    if not wake.get("schedule_key"):
        return True  # fired by another agent: no cadence value stands behind it
    mine = cadence.parse_schedule(cadence_for(trigger))
    return mine is not None and mine == cadence.parse_schedule(wake.get("cadence"))


def proposed_wakes(workspace: Path, framework: Path, role: ar.Role) -> list[dict[str, Any]]:
    """The wakes proposed for an agent of `role`, from its duties and the cadence values.

    One per duty the role may run on its own (tier `act`) that has a schedule
    in `config.preferences.cadences` and a check: the wake's name is the
    duty's id and its trigger runs at the schedule's times. `schedule_key`
    and `cadence` name that cadence value: the duty is due by it, so the
    wake's time and it must say the same. Plus the wakes another agent fires
    (`ON_EVENT_WAKES`), which have no cadence value. A role with neither
    wakes on a message only.
    """
    values = cadence.cadence_values(workspace, framework)
    wakes: list[dict[str, Any]] = []
    for duty in role.duties:
        schedule = cadence.parse_schedule(values.get(duty.schedule))
        if duty.tier != "act" or duty.check == "none" or schedule is None:
            continue
        wakes.append({"name": duty.id, "trigger": trigger_for(schedule), "duty": duty.id,
                      "purpose": f"the duty `{duty.id}` ({values.get(duty.schedule)})",
                      "held": duty.skill == agents.HELD_SKILL,
                      "schedule_key": duty.schedule, "cadence": str(values.get(duty.schedule))})
    for name, trigger, purpose in ON_EVENT_WAKES.get(role.name, ()):
        wakes.append({"name": name, "trigger": trigger, "duty": None, "purpose": purpose,
                      "held": False, "schedule_key": None, "cadence": None})
    return wakes


def _outside_hours(trigger: str, work_hours: Any) -> bool:
    """True when a `cron "M H ..."` trigger runs at a time outside `work_hours` ("HH:MM-HH:MM")."""
    clock = _CRON_CLOCK_RE.match(trigger.strip())
    window = _HOURS_RE.match(str(work_hours or "").strip())
    if not clock or not window:
        return False
    minute = int(clock.group(2)) * 60 + int(clock.group(1))
    start = int(window.group(1)) * 60 + int(window.group(2))
    end = int(window.group(3)) * 60 + int(window.group(4))
    inside = start <= minute < end if start <= end else (minute >= start or minute < end)
    return not inside


def _has(name: str) -> bool:
    try:
        _binary(name)
    except Unavailable:
        return False
    return True


def setup_state(workspace: Path, framework: Path) -> dict[str, Any]:
    """Everything the `start` skill needs to ask its questions. Reads only.

    `available`: is the `autocode` command here, the `opencode` command, and
    does this shell belong to a session the Autocode plugin runs in. `enabled`
    is the switch. `configured` is true once the lead has a registry row
    (`setup apply` creates it). `this_session` is the id this session is
    registered under. `settings` are the values now, `proposed` what `setup
    apply` writes when nothing else is said. `agents` lists every agent that
    can run, with its granted wakes and the wakes proposed for it. A proposed
    wake that is granted carries the grant's trigger (`granted`), its
    `status`, and `in_step`: does that trigger run at the times of the duty's
    cadence value. A granted wake is that wake, whatever form its trigger has.
    """
    roles, _errors = ar.collect(framework, workspace)
    data = agents.roster(workspace, framework)
    prefs = ar.load_preferences(workspace)
    update = prefs.get("update") if isinstance(prefs.get("update"), dict) else {}
    auto = ar.autocode_preferences(workspace)
    runner = (auto.get("models") or {}).get("runner") if isinstance(auto.get("models"), dict) else None
    runner = runner if isinstance(runner, dict) else {}
    head = ar.lead(roles)
    fetcher = ar.check_owner(roles, "update")
    try:
        mine = me() if _has("autocode") else None
    except Unavailable:
        mine = None
    listed: list[dict[str, Any]] = []
    for agent in data["agents"]:
        role = next((r for r in roles if r.name == agent["role"]), None)
        if role is None:
            continue
        granted = {g["name"]: g for g in agent["grants"]}
        proposed = proposed_wakes(workspace, framework, role)
        for wake in proposed:
            grant = granted.get(wake["name"])
            wake["granted"] = grant["trigger"] if grant else None
            wake["status"] = (grant.get("status") or "active") if grant else None
            wake["in_step"] = grant is None or _in_step(grant["trigger"], wake)
        listed.append({
            "name": agent["name"], "id": agent["id"], "kind": agent["kind"],
            "lead": head is not None and role.name == head.name,
            "recommended": agent["name"] == fetcher,
            "row": agent["registered"], "status": agent["status"], "host": agent["host"],
            "grants": agent["grants"],
            "proposed": proposed,
        })
    hours = update.get("work_hours")
    proposed_hours = hours
    if not hours and not any(_outside_hours(w["granted"] or w["trigger"], PROPOSED_WORK_HOURS)
                             for a in listed for w in a["proposed"] if w["held"]):
        proposed_hours = PROPOSED_WORK_HOURS
    lead_row = next((a for a in listed if a["lead"]), None)
    return {
        "available": {"autocode": _has("autocode"), "opencode": _has("opencode"),
                      "plugin": bool(os.environ.get("AUTOCODE_SESSION"))},
        "enabled": ar.autocode_enabled(workspace),
        "configured": bool(lead_row and lead_row["row"]),
        "this_session": mine,
        "prefix": data["prefix"],
        "lead": lead_row["name"] if lead_row else None,
        "settings": {"work_hours": hours, "off_days": update.get("off_days") or [],
                     "daily_usd": auto.get("daily_usd"), "runner_model": runner.get("model"),
                     "runner_variant": runner.get("variant")},
        "proposed": {"work_hours": proposed_hours},
        "agents": listed,
    }


def render_setup(state: dict[str, Any]) -> list[str]:
    """`setup_state` as the lines the `start` skill shows the owner."""
    have = state["available"]
    if not have["autocode"]:
        first = "Autocode: not installed on this machine. Agents cannot run as sessions of their own here."
    elif not have["plugin"]:
        first = "Autocode: installed, and this session does not run its plugin, so it cannot register."
    else:
        first = "Autocode: available in this session."
    lines = [first,
             f"Mode: {'ON' if state['enabled'] else 'OFF'}. "
             + ("Set up." if state["configured"] else "Not set up yet."),
             "This session: " + (f"registered as {state['this_session']}." if state["this_session"]
                                 else "not registered.")]
    now, plan = state["settings"], state["proposed"]
    hours = now["work_hours"] or "any hour"
    if plan["work_hours"] != now["work_hours"]:
        hours += f" (proposed: {plan['work_hours']})"
    model = now["runner_model"] or "the harness's default"
    if now["runner_model"] and now["runner_variant"]:
        model += f"#{now['runner_variant']}"
    cap = f"${now['daily_usd']:g}" if isinstance(now["daily_usd"], (int, float)) else "none"
    lines += ["", "Settings:",
              f"  work hours of the scheduled update: {hours}",
              f"  daily cost cap per agent: {cap}",
              f"  model of agents started in the background: {model}",
              "", "Agents:"]
    for agent in state["agents"]:
        tags = [t for t, on in (("the lead", agent["lead"]), ("recommended", agent["recommended"])) if on]
        where = f"row {agent['status']}" + (f" on {agent['host']}" if agent["host"] else "") \
            if agent["row"] else "no row yet"
        lines.append(f"  {agent['name']} ({agent['id']})" + (f" [{', '.join(tags)}]" if tags else "")
                     + f": {where}")
        for wake in agent["proposed"]:
            if wake["granted"] is None:
                tag = "proposed"
            elif wake["status"] == "suspended":
                tag = "suspended"
            elif not wake["in_step"]:
                tag = f'granted; out of step with its cadence value "{wake["cadence"]}"'
            else:
                tag = "granted"
            lines.append(f"    wake {wake['name']}: {wake['granted'] or wake['trigger']}  [{tag}]  "
                         f"{wake['purpose']}")
        named = {w["name"] for w in agent["proposed"]}
        for grant in agent["grants"]:
            if grant["name"] not in named:
                state_ = "suspended" if grant.get("status") == "suspended" else "granted"
                lines.append(f"    wake {grant['name']}: {grant['trigger']}  [{state_}]  "
                             f"{grant.get('purpose') or ''}".rstrip())
        if not agent["proposed"] and not agent["grants"]:
            lines.append("    no wake: it wakes on a message")
    return lines


def _resolve_time(name: str, wake: dict[str, Any], value: str) -> tuple[str, str | None]:
    """(the trigger, the cadence value to write or None) for a time the owner gave for a wake.

    `value` is a cadence value (`08:00`, `Sunday 18:00`, `1st 12:00`, `every
    2 hours`) or a trigger (`cron "0 8 * * *"`, `every 2h from 08:00`). The
    other one is derived, so the duty's due time and the wake's time always
    say the same. A trigger no cadence value can say is refused for a duty's
    wake; a wake another agent fires has no cadence value and takes any.
    """
    text = " ".join(str(value).split())
    schedule = cadence.parse_schedule(text)
    if not wake.get("schedule_key"):
        return (trigger_for(schedule) if schedule is not None else text), None
    if schedule is not None:
        return trigger_for(schedule), text
    mapped = cadence_for(text)
    if mapped is None:
        raise ar.AgentError(
            f"{name} `{wake['name']}`: the time {text!r} cannot be kept in step with the duty's due "
            'time. Give it as a time (08:30, "Sunday 18:00", "1st 09:00", "every 2 hours") or as a '
            'plain trigger (cron "30 8 * * *", every 2h from 08:00)')
    return text, mapped


def apply_setup(workspace: Path, framework: Path, *, approval: str, names: list[str] | None = None,
                work_hours: Any = _KEEP, daily_usd: Any = _KEEP, runner_model: Any = _KEEP,
                runner_variant: Any = _KEEP, triggers: dict[tuple[str, str], str] | None = None,
                skip: set[tuple[str, str]] | None = None, no_wakes: bool = False,
                dry_run: bool = False) -> list[str]:
    """Record the setup the owner approved; return one line per thing done.

    The lead and every agent in `names` get a registry row and a grant per
    proposed wake (`proposed_wakes`), without the ones in `skip`, and none at
    all with `no_wakes`. `triggers[(agent, wake)]` is a time the owner
    changed, as a cadence value or as a trigger: the grant gets the trigger
    and `preferences.cadences` gets the matching value, so the duty's due
    time and the wake never disagree. A wake that is already granted is left
    as it is unless the owner changed its time now: its trigger is the
    owner's, in whatever form (an anchor, an end). `approval` is what the
    owner said to these terms and is written on every new grant; the tool
    cannot see an approval, so the skill asks first. Settings that are given
    are written to `config.yaml`. A second run with the same terms changes
    nothing.

    Raises AgentError when the mode is off, when an agent or a wake is not
    known, when a changed time has no cadence value, or when a scheduled
    update (granted or new) would lie outside the work hours.
    """
    if not ar.autocode_enabled(workspace):
        raise ar.AgentError("Autocode mode is off: turn it on first (`setup enable`), on the "
                            "owner's yes")
    if not approval or not approval.strip():
        raise ar.AgentError("`setup apply` needs --approval: the owner's words to these terms")
    state = setup_state(workspace, framework)
    by_name = {a["name"]: a for a in state["agents"]}
    if state["lead"] is None:
        raise ar.AgentError("no lead role is defined")
    chosen = [state["lead"]] + [n for n in dict.fromkeys(names or []) if n != state["lead"]]
    unknown = [n for n in chosen if n not in by_name]
    if unknown:
        raise ar.AgentError(f"no agent named {', '.join(unknown)} (agents: {', '.join(by_name)}). "
                            "A project agent is created with the `add-agent` skill first")
    triggers, skip = dict(triggers or {}), set(skip or ())
    hours = state["settings"]["work_hours"] if work_hours is _KEEP else work_hours
    if hours is not None and not _HOURS_RE.match(str(hours).strip()):
        raise ar.AgentError(f'work hours {hours!r}: write them as "07:00-22:00", or `none`')
    if daily_usd is not _KEEP and daily_usd is not None and not (
            isinstance(daily_usd, (int, float)) and daily_usd > 0):
        raise ar.AgentError("--daily-usd takes a positive number, or `none`")
    known = {(a, w["name"]) for a in chosen for w in by_name[a]["proposed"]}
    stray = sorted(f"{a}:{w}" for a, w in (set(triggers) | skip) - known)
    if stray:
        raise ar.AgentError(f"no such proposed wake: {', '.join(stray)}")
    plan: list[tuple[str, dict[str, Any], str | None]] = []  # (agent, wake with its trigger, new cadence)
    notes: list[str] = []
    for name in chosen:
        for wake in by_name[name]["proposed"]:
            key = (name, wake["name"])
            if key in skip:
                continue
            if key in triggers:
                trigger, value = _resolve_time(name, wake, triggers[key])
                plan.append((name, {**wake, "trigger": trigger}, value))
            elif wake["granted"] is not None:
                trigger = wake["granted"]  # the owner's terms stand until the owner changes them
                if not wake["in_step"]:
                    notes.append(f"note: {name} `{wake['name']}` is granted as `{trigger}` and its cadence "
                                 f"value says \"{wake['cadence']}\": change its time to bring them in step")
            elif no_wakes:
                continue
            else:
                trigger = wake["trigger"]
                plan.append((name, dict(wake), None))
            if wake["held"] and _outside_hours(trigger, hours):
                raise ar.AgentError(f"{name} `{wake['name']}` ({trigger}) lies outside the work hours "
                                    f"{hours}: a held update never runs. Change the time or the hours")
    said = " ".join(approval.split())
    done: list[str] = []
    tag = "would " if dry_run else ""
    for setting, value in (("work_hours", work_hours), ("daily_usd", daily_usd),
                           ("runner_model", runner_model), ("runner_variant", runner_variant)):
        if value is _KEEP or state["settings"][setting] == value:
            continue
        done.append(f"{tag}set {'.'.join(SETTABLE[setting])}: {json.dumps(value)}")
        if not dry_run:
            set_config(workspace, setting, value)
    for _name, wake, value in plan:
        if value is None or cadence.parse_schedule(value) == cadence.parse_schedule(wake["cadence"]):
            continue
        done.append(f"{tag}set cadences.{wake['schedule_key']}: {json.dumps(value)}")
        if not dry_run:
            set_cadence(workspace, wake["schedule_key"], value)
    for name in chosen:
        if not by_name[name]["row"]:
            done.append(f"{tag}add the registry row of {name}")
            if not dry_run:
                agents.set_row(workspace, name, framework=framework, source="start")
    for name, wake, _value in plan:
        held = next((g for g in by_name[name]["grants"] if g["name"] == wake["name"]), None)
        if held and held["trigger"] == wake["trigger"] and held.get("duty") == wake["duty"] \
                and held.get("status", "active") == "active":
            continue
        done.append(f"{tag}grant {name} `{wake['name']}`: {wake['trigger']}")
        if not dry_run:
            agents.grant(workspace, name, wake=wake["name"], trigger=wake["trigger"],
                         purpose=wake["purpose"], approval=said, duty=wake["duty"], framework=framework)
    return (done or ["nothing to change: the setup already holds these terms"]) + notes


def mark(workspace: Path, framework: Path, name: str, *, off: bool = False) -> dict[str, Any]:
    """Mark the registry row of the agent this session registered as (or, with `off`, left).

    `active` with granted wakes, `dormant` without, and this machine as its
    host; `off` after it unregistered. The row follows the registration and
    never leads it: raises RuntimeError when this session is not registered
    under the agent's id (or, with `off`, still is).
    """
    info = _agent(workspace, framework, name)
    mine = me()
    if off:
        if mine == info["id"]:
            raise RuntimeError(f"this session is still registered as {mine}: unregister first")
        agents.set_row(workspace, name, framework=framework, source="start", status="off")
        return {"id": info["id"], "status": "off", "host": None}
    if mine != info["id"]:
        raise RuntimeError(f"this session is not registered as {info['id']}"
                           + (f" (it is {mine})" if mine else "") + ": register first")
    status_ = "active" if info["grants"] else "dormant"
    host = machines.machine_name(create=False)
    agents.set_row(workspace, name, framework=framework, source="start", status=status_, host=host)
    return {"id": info["id"], "status": status_, "host": host}


# ------------------------------------------------------- the check of a wake

def daily_cap(workspace: Path, framework: Path, name: str) -> float | None:
    """The agent's daily cost cap in USD: its registry row's, else `preferences.autocode.daily_usd`."""
    row = next((a for a in agents.roster(workspace, framework)["agents"] if a["name"] == name), None)
    for value in ((row or {}).get("daily_usd"), ar.autocode_preferences(workspace).get("daily_usd")):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None


def spend_today() -> tuple[str | None, float | None]:
    """(the id this session is registered under, what it spent today in USD), from `autocode me`."""
    result = _autocode(["me", "--json"])
    try:
        data = json.loads(result.stdout or "null")
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    cost = agent.get("cost") if isinstance(agent.get("cost"), dict) else data.get("cost")
    spent = cost.get("today_usd") if isinstance(cost, dict) else None
    return (str(agent["id"]) if agent.get("id") else None,
            float(spent) if isinstance(spent, (int, float)) else None)


def _cap_notice(workspace: Path, framework: Path, info: dict[str, Any], spent: float, cap: float) -> None:
    """Say once a day that an agent reached its cap: to the lead, or from the lead to the owner."""
    today = cadence.now_local().date().isoformat()
    noticed = machines.read_local_state(workspace).get("cap_noticed")
    noticed = noticed if isinstance(noticed, dict) else {}
    if noticed.get(info["id"]) == today:
        return
    text = (f"Cost cap: {info['name']} spent ${spent:.2f} of its ${cap:g} daily cap. Its scheduled "
            "runs are skipped until tomorrow. A message still wakes it.")
    to_owner = info["id"] == info["lead_id"]
    args = ["send", "owner" if to_owner else info["lead_id"], text, "--subject", "Cost cap"]
    try:
        _autocode(args + (["--report"] if to_owner else []))
        machines.write_local_state(workspace, cap_noticed={**noticed, info["id"]: today})
    except (Unavailable, OSError):
        return  # the run is skipped either way; the notice is tried again at the next check


def run_check(workspace: Path, framework: Path, name: str, duty: str | None, wake: str
              ) -> tuple[int, str]:
    """(exit code, line to print) of a granted wake's check under Autocode.

    First the daily cost cap, when the agent has one: at or past it the run is
    skipped (1), and one notice a day goes out. Then `agents check ... --wake`
    (0 due, 1 not due). 3 when the cap cannot be checked or a custom check
    failed: a check that cannot say is a failure, never "not due".
    """
    cap = daily_cap(workspace, framework, name)
    if cap is not None:
        info = _agent(workspace, framework, name)
        agent_id, spent = spend_today()
        if agent_id != info["id"] or spent is None:
            return 3, (f"{name}: a daily cap of ${cap:g} is set and today's spend cannot be read "
                       f"here (this session is {agent_id or 'not registered'})")
        if spent >= cap:
            _cap_notice(workspace, framework, info, spent, cap)
            return 1, ""
    due, why = agents.check(workspace, name, duty, framework, wake=wake)
    return (0, why) if due else (1, "")


# -------------------------------------------------------------------- actions

def start_command(workspace: Path, framework: Path, name: str, *, dev: bool = False,
                  auto: bool = True) -> tuple[list[str], dict[str, Any]]:
    """(the `opencode run` argument list that starts the agent, what is known about the agent)."""
    info = _agent(workspace, framework, name, dev=dev)
    cmd = [_binary("opencode"), "run", "--title", f"@{info['id']}"]
    if auto:
        cmd.append("--auto")
    if info["model"]:
        cmd += ["--model", f"{info['model']}#{info['variant']}" if info["variant"] else info["model"]]
    cmd.append(start_prompt(workspace, framework, name, dev=dev))
    return cmd, info


def start(workspace: Path, framework: Path, name: str, *, dev: bool = False, auto: bool = True
          ) -> dict[str, Any]:
    """Start a session for the agent and mark its registry row. Returns what was done.

    Raises AgentError when Autocode mode is off or the agent is unknown, and
    Unavailable when `opencode` is missing or the start failed.
    """
    if not ar.autocode_enabled(workspace):
        raise ar.AgentError("Autocode mode is off (`preferences.autocode.enabled` is false): no "
                            "agent runs as a session of its own. Turn it on first; that is the "
                            "owner's decision")
    cmd, info = start_command(workspace, framework, name, dev=dev, auto=auto)
    env = {k: v for k, v in os.environ.items() if not k.startswith(STRIP_ENV_PREFIXES)}
    try:
        result = _run(cmd, cwd=framework.parent, env=env, timeout=START_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"the session could not be started ({exc})") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise Unavailable(f"`opencode run` ended with exit {result.returncode}: {tail[0][:200]}")
    done = {"id": info["id"], "name": info["name"], "status": None, "host": None}
    if not info["dev"]:
        status = "active" if info["grants"] else "dormant"
        host = machines.machine_name(create=False)
        agents.set_row(workspace, name, framework=framework, source="autocode", status=status, host=host)
        done.update(status=status, host=host)
    return done


MESSAGES = {
    "park": ("Park", "Park: write your state to your files, tell the lead what changed, compact, "
                     "and go idle.", "dormant"),
    "stop": ("Stop", "Stop: finish the step you are in, write your state to your files, cancel "
                     "your wakes, and unregister from Autocode.", "off"),
    "retire": ("Retire", "Retire: write the closing note, tell the lead, cancel your wakes, and "
                         "unregister from Autocode. This agent has ended.", "retired"),
}


def notify(workspace: Path, framework: Path, name: str, subject: str, text: str
           ) -> tuple[bool, str]:
    """Send one message to the agent `name` when a session holds it; return (sent, why not).

    Nothing is sent, and that is no error, when Autocode mode is off, when no
    session holds the agent (its registry row is not `active` or `dormant`; a
    dev twin has no row and is always tried), when this session is that agent,
    or when the runtime refuses. The text carries handles and paths, never data.
    """
    if not ar.autocode_enabled(workspace):
        return False, "Autocode mode is off"
    try:
        info = _agent(workspace, framework, name)
    except ar.AgentError as exc:
        return False, str(exc)
    if not info["dev"]:
        rows, _errors = agents.load_registry(workspace)
        status = next((r.get("status") for r in rows if r.get("name") == info["name"]), None)
        if status not in agents.REACHABLE:
            return False, f"no session holds {info['name']}"
    try:
        if me() == info["id"]:
            return False, "this session is that agent"
        result = _autocode(["send", info["id"], text, "--subject", subject])
    except Unavailable as exc:
        return False, str(exc)
    if result.returncode != 0:
        why = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        return False, why[0][:160]
    return True, ""


def answer(workspace: Path, framework: Path, ask_id: str, text: str, *, by: str | None = None
           ) -> dict[str, Any]:
    """Record the owner's answer on an open ask and tell the agent that asked.

    The record is `asks.answer_ask`. Then one message goes to the asking agent
    when a session holds it, naming the ask and where its answer is; the
    answer itself stays on the row. Returns the row, `told` and `why_not`.
    """
    row = asks.answer_ask(workspace, ask_id, answer=text, by=by)
    agent = str(row.get("agent") or "")
    told, why = notify(
        workspace, framework, agent, "Ask answered",
        f"Answered: {row.get('id')}. The owner's answer is on the row in `_memory/{asks.ASKS_FILE}` "
        f"(`uv run python -m superagent.tools.asks list --agent {agent} --json`). "
        "Read it and go on with the work that waited.")
    return {"row": row, "agent": agent, "told": told, "why_not": why}


def tell(workspace: Path, framework: Path, name: str, action: str) -> dict[str, Any]:
    """Send the agent its `park` / `stop` / `retire` message, then mark its registry row.

    The row changes only when the message was sent: a row that says `off`
    while the session still runs would mislead every other machine.
    """
    info = _agent(workspace, framework, name)
    subject, text, status = MESSAGES[action]
    result = _autocode(["send", info["id"], text, "--subject", subject])
    if result.returncode != 0:
        why = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"the message to {info['id']} was not sent ({why[0][:160]}); the "
                           "registry row is unchanged. The owner can force it: "
                           f"`autocode agents rm {info['id']}`")
    agents.set_row(workspace, name, framework=framework, source="autocode", status=status)
    if action == "retire":
        agents.change_grants(workspace, name, action="suspend")
    return {"id": info["id"], "status": status}


def audit_wakes(workspace: Path, framework: Path, name: str | None = None) -> list[dict[str, Any]]:
    """Wakes that are set against the grants on the registry rows; one row per finding.

    Findings: `unknown` (a periodic wake is set and no grant names it),
    `missing` (an active grant whose wake is not set), `differs` (the trigger
    that is set is not the one granted). Needs the owner's shell.
    """
    data = agents.roster(workspace, framework)
    wanted = {a["id"]: a for a in data["agents"] if name is None or a["name"] == name}
    if name is not None and not wanted:
        raise ar.AgentError(f"no agent named {name!r} in the roster")
    listed = _owner_json(["wakes"] if name is None else ["wakes", next(iter(wanted))])
    by_agent: dict[str, dict[str, str]] = {}
    for wake in listed if isinstance(listed, list) else []:
        if isinstance(wake, dict) and str(wake.get("agent", "")).startswith(f"{data['prefix']}:"):
            by_agent.setdefault(str(wake["agent"]), {})[str(wake.get("name"))] = str(wake.get("trigger") or "")
    findings: list[dict[str, Any]] = []
    for agent_id in sorted(set(wanted) | (set(by_agent) if name is None else set())):
        granted = {g["name"]: g for g in (wanted.get(agent_id) or {}).get("grants", [])
                   if g.get("status", "active") == "active"}
        actual = by_agent.get(agent_id, {})
        for wake, trigger in sorted(actual.items()):
            if wake not in granted:
                if _is_periodic(trigger):
                    findings.append({"agent": agent_id, "wake": wake, "finding": "unknown",
                                     "detail": f"set as `{trigger}` with no grant"})
            elif " ".join(trigger.split()) != granted[wake]["trigger"]:
                findings.append({"agent": agent_id, "wake": wake, "finding": "differs",
                                 "detail": f"set as `{trigger}`, granted as `{granted[wake]['trigger']}`"})
        for wake, entry in sorted(granted.items()):
            if wake not in actual:
                findings.append({"agent": agent_id, "wake": wake, "finding": "missing",
                                 "detail": f"granted as `{entry['trigger']}` and not set"})
    return findings


def _is_periodic(trigger: str) -> bool:
    """`every`, `cron` and `idle` wakes repeat; `in`, `at` and `fire-only` do not."""
    return trigger.strip().lower().startswith(("every", "cron", "idle"))


def status(workspace: Path, framework: Path) -> list[dict[str, Any]]:
    """The roster against what is registered with Autocode. Needs the owner's shell."""
    data = agents.roster(workspace, framework)
    listed = _owner_json(["agents", f"{data['prefix']}:*"])
    registered = {str(a.get("id")): a for a in listed if isinstance(a, dict)} if isinstance(listed, list) else {}
    rows: list[dict[str, Any]] = []
    for agent in data["agents"]:
        live = registered.pop(agent["id"], None)
        note = ""
        if agent["status"] in ("active", "dormant") and live is None:
            note = "the row says it runs, and it is not registered"
        elif agent["status"] in ("off", "retired") and live is not None:
            note = f"registered, and the row says `{agent['status']}`"
        rows.append({"id": agent["id"], "row": agent["status"], "host": agent["host"],
                     "registered": (live or {}).get("status"), "machine": (live or {}).get("machine"),
                     "note": note})
    for agent_id, live in sorted(registered.items()):
        rows.append({"id": agent_id, "row": None, "host": None, "registered": live.get("status"),
                     "machine": live.get("machine"),
                     "note": "" if agent_id.endswith(":dev") else "registered, with no registry row"})
    return rows


def settings_lines(workspace: Path, framework: Path) -> list[str]:
    """The owner-side Autocode settings for this workspace's agents, as command lines."""
    data = agents.roster(workspace, framework)
    prefix = data["prefix"]
    roles, _errors = ar.collect(framework, workspace)
    head = ar.lead(roles)
    lead_id = ar.agent_id(prefix, head.name) if head else None
    cap = ar.autocode_preferences(workspace).get("daily_usd")
    lines = ["# Optional Autocode settings for the agents of this workspace. Nothing needs them: the",
             "# agents run on Autocode's defaults, and the daily cost cap is kept by each wake's check.",
             "# They harden the setup. Only the owner sets them, in a terminal on the Autocode",
             "# server's machine; nothing here was run."]
    lines.append(f"autocode settings set daily_usd {cap} --match '{prefix}:*'" if cap is not None else
                 f"# a hard cap enforced by Autocode itself: "
                 f"autocode settings set daily_usd <USD> --match '{prefix}:*'")
    for agent in data["agents"]:
        if agent["daily_usd"] is not None and agent["daily_usd"] != cap:
            lines.append(f"autocode settings set daily_usd {agent['daily_usd']} --agent {agent['id']}")
    lines.append(f"autocode settings set retry_attempts 3 --match '{prefix}:*'")
    if lead_id:
        lines.append("# Question prompts of the other agents go to the lead (the card of a blocking ask "
                     "still comes to you):")
        for role in roles:
            if role.kind == "lead":
                continue
            pattern = f"{prefix}:{role.name}:*" if role.kind == "project" else f"{prefix}:{role.name}"
            lines.append(f"autocode settings set question_mode agent:{lead_id} --match '{pattern}'")
    lines += ["# An idle agent stays registered:",
              f"autocode settings set expire_after 0 --match '{prefix}:project:*'",
              f"autocode settings set expire_after 0 --match '{prefix}:*:dev'",
              "# Optional: let Autocode hold every periodic wake an agent sets until you approve it",
              "# (every machine needs the 1.1.0-rc5 plugin or newer first):",
              f"# autocode settings set wake_mode ask --match '{prefix}:*'"]
    return lines


# ------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    """The `autocode` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    common.add_argument("--framework", type=Path, default=FRAMEWORK, help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="autocode", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("me", parents=[common], help="Is this session a registered agent?")
    su = sub.add_parser("setup", parents=[common],
                        help="The state and the proposed settings; enable, disable, apply.")
    su.add_argument("action", nargs="?", default="status",
                    choices=["status", "enable", "disable", "apply"])
    su.add_argument("--json", action="store_true", help="status: print the data.")
    su.add_argument("--approval", default=None, help="apply: the owner's words to these terms.")
    su.add_argument("--agents", default="", help="apply: the agents to set up beside the lead, a comma list.")
    su.add_argument("--work-hours", default=None, help='apply: "07:00-22:00", or `none` for any hour.')
    su.add_argument("--daily-usd", default=None, help="apply: the daily cost cap per agent, or `none`.")
    su.add_argument("--runner-model", default=None, help="apply: the model of agents started in the background.")
    su.add_argument("--runner-variant", default=None, help="apply: that model's variant.")
    su.add_argument("--wake", action="append", default=[], metavar="AGENT:NAME=TIME",
                    help='apply: another time for a wake, as a cadence value (08:00, "Sunday 18:00", '
                         '"1st 12:00", "every 2 hours") or a trigger (every 2h from 08:00); the cadence '
                         "value in config.yaml follows it (repeatable).")
    su.add_argument("--skip", action="append", default=[], metavar="AGENT:NAME",
                    help="apply: leave a proposed wake out (repeatable).")
    su.add_argument("--no-wakes", action="store_true", help="apply: grant no wake (an idle start).")
    su.add_argument("--dry-run", action="store_true", help="apply: show what would be written.")
    mk = sub.add_parser("mark", parents=[common],
                        help="Mark the row of the agent this session registered as.")
    mk.add_argument("agent")
    mk.add_argument("--off", action="store_true", help="The session unregistered: the row becomes `off`.")
    ck = sub.add_parser("check", parents=[common],
                        help="The check of a granted wake: the cost cap, then `agents check`.")
    ck.add_argument("agent")
    ck.add_argument("duty", nargs="?", default=None)
    ck.add_argument("--wake", required=True)
    for name, text in (("prompt", "Print an agent's start prompt."),
                       ("start", "Start a session for an agent.")):
        cmd = sub.add_parser(name, parents=[common], help=text)
        cmd.add_argument("agent", help="`<role>`, `<role>:dev` or `project:<slug>`.")
        cmd.add_argument("--dev", action="store_true", help="The role's dev twin.")
        if name == "start":
            cmd.add_argument("--print", action="store_true", dest="show",
                             help="Show the command and the prompt; start nothing.")
            cmd.add_argument("--no-auto", action="store_true",
                             help="Do not auto-approve the session's permission prompts.")
    for name in MESSAGES:
        cmd = sub.add_parser(name, parents=[common], help=f"Ask an agent to {name}; mark its row.")
        cmd.add_argument("agent")
    wk = sub.add_parser("wakes", parents=[common], help="Wakes that are set against the grants.")
    wk.add_argument("agent", nargs="?", default=None)
    wk.add_argument("--json", action="store_true")
    st = sub.add_parser("status", parents=[common], help="The roster against what is registered.")
    st.add_argument("--json", action="store_true")
    sub.add_parser("settings", parents=[common], help="Print the owner-side settings lines.")
    an = sub.add_parser("answer", parents=[common],
                        help="Record the owner's answer on an ask and tell the agent that asked.")
    an.add_argument("id")
    an.add_argument("--answer", required=True, help="The owner's answer.")
    an.add_argument("--by", default=None, help="Who answered (default: owner).")
    ln = sub.add_parser("lock-name", parents=[common], help="The name of a shared Autocode lock.")
    ln.add_argument("kind", choices=[BROWSER_LOCK])
    ln.add_argument("name", help="The browser profile.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or args.framework.parent / "workspace"
    fw: Path = args.framework
    try:
        if args.command == "me":
            agent_id = me()
            if agent_id:
                print(agent_id)
            return 0 if agent_id else 1
        if args.command == "check":
            code, line = run_check(workspace, fw, args.agent, args.duty, args.wake)
            if line:
                print(line, file=sys.stderr if code == 3 else sys.stdout)
            return code
        if args.command == "setup":
            return _setup_command(args, workspace, fw)
        if args.command == "mark":
            done = mark(workspace, fw, args.agent, off=args.off)
            print(f"{done['id']}: row `{done['status']}`" + (f" on {done['host']}" if done["host"] else ""))
        elif args.command == "prompt":
            print(start_prompt(workspace, fw, args.agent, dev=args.dev))
        elif args.command == "start":
            if args.show:
                cmd, _info = start_command(workspace, fw, args.agent, dev=args.dev, auto=not args.no_auto)
                print(f"cd {fw.parent} && " + " ".join(cmd[:-1]) + " '<the prompt below>'\n")
                print(cmd[-1])
                return 0
            done = start(workspace, fw, args.agent, dev=args.dev, auto=not args.no_auto)
            print(f"started {done['id']}" + (f": row `{done['status']}` on {done['host']}"
                                             if done["status"] else " (a dev twin has no row)"))
            print("confirm it registered: `autocode agents` in the owner's shell, or "
                  "`autocode_me action=discover` in a session")
        elif args.command in MESSAGES:
            done = tell(workspace, fw, args.agent, args.command)
            print(f"{done['id']}: asked to {args.command}; row `{done['status']}`")
        elif args.command == "wakes":
            findings = audit_wakes(workspace, fw, args.agent)
            if args.json:
                print(json.dumps(findings, indent=2))
            else:
                for row in findings:
                    print(f"{row['agent']}  {row['wake']}  {row['finding']}: {row['detail']}")
                if not findings:
                    print("every wake that is set has a grant, and every grant has its wake")
            return 1 if findings else 0
        elif args.command == "status":
            rows = status(workspace, fw)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    print(f"{row['id']}  row {row['row'] or '-'}  registered {row['registered'] or '-'}"
                          f"  {row['machine'] or row['host'] or '-'}" + (f"  !! {row['note']}" if row["note"] else ""))
        elif args.command == "settings":
            for line in settings_lines(workspace, fw):
                print(line)
        elif args.command == "answer":
            done = answer(workspace, fw, args.id, args.answer, by=args.by)
            print(f"{args.id}: {done['row']['status']}")
            print(f"{args.id}: {done['agent']} was told" if done["told"] else
                  f"{args.id}: {done['agent']} was not told ({done['why_not']}); it reads the "
                  "answer at its next run")
        else:
            print(lock_name(workspace, args.kind, args.name))
    except (ar.AgentError, ValueError) as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 2
    except (signals.CorruptFileError, signals.WriteRefusedError) as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 1
    except (Unavailable, agents.CheckFailedError) as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 1
    return 0


def _value(text: str | None, kind: type = str) -> Any:
    """A `setup apply` flag as a value: not given keeps the config's, `none` clears it."""
    if text is None:
        return _KEEP
    if text.strip().lower() in ("none", "null", ""):
        return None
    if kind is float:
        try:
            return float(text.strip().lstrip("$"))
        except ValueError as exc:
            raise ar.AgentError(f"{text!r} is not a number") from exc
    return text.strip()


def _pairs(items: list[str], *, with_value: bool) -> dict[tuple[str, str], str]:
    """`AGENT:NAME[=TRIGGER]` flags as {(agent, wake): trigger}. An agent name may hold a colon."""
    out: dict[tuple[str, str], str] = {}
    for item in items:
        left, sep, right = item.partition("=")
        agent, colon, wake = left.strip().rpartition(":")
        if not colon or not agent or not wake or (with_value and (not sep or not right.strip())):
            form = "AGENT:NAME=TIME" if with_value else "AGENT:NAME"
            raise ar.AgentError(f"{item!r}: write it as {form}")
        out[(agent, wake)] = right.strip()
    return out


def _setup_command(args: argparse.Namespace, workspace: Path, fw: Path) -> int:
    if args.action in ("enable", "disable"):
        on = args.action == "enable"
        changed = set_config(workspace, "enabled", on)
        print(f"Autocode mode is {'on' if on else 'off'}" + ("" if changed else " (it already was)"))
        return 0
    if args.action == "apply":
        for line in apply_setup(
                workspace, fw, approval=args.approval or "",
                names=[n.strip() for n in args.agents.split(",") if n.strip()],
                work_hours=_value(args.work_hours), daily_usd=_value(args.daily_usd, float),
                runner_model=_value(args.runner_model), runner_variant=_value(args.runner_variant),
                triggers=_pairs(args.wake, with_value=True),
                skip=set(_pairs(args.skip, with_value=False)), no_wakes=args.no_wakes,
                dry_run=args.dry_run):
            print(line)
        return 0
    state = setup_state(workspace, fw)
    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        for line in render_setup(state):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
