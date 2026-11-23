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
  settings           Print the owner-side settings lines for this prefix. It
                     sets nothing: only the owner sets Autocode settings.
  lock-name browser <profile>
                     The name of the Autocode lock two agents share for one
                     browser profile.

Who can run what. In an agent's own shell the `autocode` command only sends
messages, fires wakes and says who it is; listing agents and wakes is the
owner's (the server's machine, in a terminal). `wakes` and `status` say so
when they are refused. An agent audits its own wakes with its Autocode tool.

Exit codes: 0 ok (`me`: registered); 1 a runtime problem (`me`: not
registered; a message that could not be sent; `wakes`: something differs);
2 usage error (unknown agent, Autocode mode off); 3 the `autocode` or
`opencode` command is missing, or refused this shell.

CLI:
    uv run python -m superagent.tools.autocode start updater [--print]
    uv run python -m superagent.tools.autocode park project:<slug>
    uv run python -m superagent.tools.autocode wakes [updater]
    uv run python -m superagent.tools.autocode settings
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from superagent.tools import agent_roles as ar
from superagent.tools import agents, machines

FRAMEWORK = Path(__file__).resolve().parent.parent
RULE_REL = "superagent/rules/autocode.md"
#: Environment of the calling session that must not leak into a session it starts.
STRIP_ENV_PREFIXES = ("AUTOCODE_", "OPENCODE")
START_TIMEOUT_S = 900
CLI_TIMEOUT_S = 60
BROWSER_LOCK = "browser"


class Unavailable(RuntimeError):
    """The `autocode` / `opencode` command is missing, or refused this shell."""


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
    """The zero-cost check a granted wake runs first (`contracts/agents.md` § 8)."""
    parts = ["uv run python -m superagent.tools.agents check", agent]
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
    lines.append(f"5. Send one message to `{info['lead_id']}`: `Started: {info['name']}`."
                 if info["id"] != info["lead_id"] else
                 "5. Tell the owner in one report message that you are started.")
    lines.append("6. Do no other work now. End your turn and go idle: you will be woken.")
    return "\n".join(lines)


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
    lines = ["# Owner-side settings for the agents of this workspace. Run them in a terminal on the",
             "# Autocode server's machine. Only the owner sets these; nothing here was run."]
    lines.append(f"autocode settings set daily_usd {cap} --match '{prefix}:*'" if cap is not None else
                 f"# no daily cap is configured (preferences.autocode.daily_usd): "
                 f"autocode settings set daily_usd <USD> --match '{prefix}:*'")
    for agent in data["agents"]:
        if agent["daily_usd"] is not None and agent["daily_usd"] != cap:
            lines.append(f"autocode settings set daily_usd {agent['daily_usd']} --agent {agent['id']}")
    lines.append(f"autocode settings set retry_attempts 3 --match '{prefix}:*'")
    if lead_id:
        lines.append("# Questions of the other agents go to the lead, the one role that asks the owner:")
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
        if args.command == "prompt":
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
        else:
            print(lock_name(workspace, args.kind, args.name))
    except ar.AgentError as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 2
    except Unavailable as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"autocode: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
