#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Playbook runner — chains skills with conditions.

Implements superagent/docs/_internal/ideas-better-structure.md item #21.

A playbook is a small YAML file under `superagent/playbooks/<name>.yaml`
that names a sequence of skills with optional `if:` conditions and
`then:`/`else:` branches.

This runner is a *resolver*, not an executor: it reads the playbook,
evaluates conditions against workspace state, and prints the ordered list
of skills to invoke (with their argument hints). The agent then invokes
each in turn. This keeps the Python side simple — no agent-tool plumbing
required — while still giving the user a discoverable, declarative way
to express recurring sequences.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def playbooks_dir(framework: Path) -> Path:
    return framework / "playbooks"


def custom_playbooks_dir(workspace: Path) -> Path:
    return workspace / "_custom" / "playbooks"


def find_playbook(framework: Path, workspace: Path, name: str) -> Path | None:
    """Lookup playbook by name, custom overlay first."""
    candidates = [
        custom_playbooks_dir(workspace) / f"{name}.yaml",
        playbooks_dir(framework) / f"{name}.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def list_playbooks(framework: Path, workspace: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for origin, root in (("custom", custom_playbooks_dir(workspace)),
                         ("framework", playbooks_dir(framework))):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.yaml")):
            if path.name == "_schema.yaml":
                continue
            data = load_yaml(path) or {}
            if not isinstance(data, dict):
                continue
            out.append({
                "name": data.get("name", path.stem),
                "stem": path.stem,
                "origin": origin,
                "trigger": data.get("trigger", []),
                "description": data.get("description", ""),
                "step_count": len(data.get("steps", [])),
                "path": str(path),
            })
    return out


# --- Condition evaluation -----------------------------------------------------

WORKSPACE_QUERIES = {
    "bills_overdue": "_memory/bills.yaml",
    "appointments_today": "_memory/appointments.yaml",
    "tasks_p0_open": "_memory/todo.yaml",
    "projects_active": "_memory/projects-index.yaml",
    "important_dates_today": "_memory/important-dates.yaml",
    "subscriptions_audit_flag": "_memory/subscriptions.yaml",
}


def parse_iso_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.UTC)
    if not isinstance(value, str):
        return None
    try:
        out = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.UTC)
    return out


def query_workspace(workspace: Path, query: str) -> int:
    """Return the count for a known query."""
    path = WORKSPACE_QUERIES.get(query)
    if not path:
        return 0
    data = load_yaml(workspace / path)
    if not isinstance(data, dict):
        return 0
    today = dt.datetime.now().astimezone().date()
    if query == "bills_overdue":
        rows = data.get("bills") or []
        return sum(
            1 for r in rows
            if isinstance(r, dict)
            and r.get("status") == "active"
            and (parse_iso_dt(r.get("next_due")) or dt.datetime.max.replace(tzinfo=dt.UTC)).date() < today
        )
    if query == "appointments_today":
        rows = data.get("appointments") or []
        out = 0
        for r in rows:
            if not isinstance(r, dict) or r.get("status") != "scheduled":
                continue
            ts = parse_iso_dt(r.get("start"))
            if ts is not None and ts.date() == today:
                out += 1
        return out
    if query == "tasks_p0_open":
        rows = data.get("tasks") or []
        return sum(
            1 for r in rows
            if isinstance(r, dict)
            and r.get("status") in ("open", "in_progress")
            and r.get("priority") == "P0"
        )
    if query == "projects_active":
        rows = data.get("projects") or []
        return sum(1 for r in rows if isinstance(r, dict) and r.get("status") == "active")
    if query == "important_dates_today":
        rows = data.get("dates") or []
        out = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            ts = parse_iso_dt(r.get("next_occurrence")) or parse_iso_dt(r.get("date"))
            if ts is not None and ts.date() == today:
                out += 1
        return out
    if query == "subscriptions_audit_flag":
        rows = data.get("subscriptions") or []
        return sum(1 for r in rows if isinstance(r, dict) and r.get("audit_flag"))
    return 0


CONDITION_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(==|!=|<=|>=|<|>)\s*(\S+)\s*$")


def eval_condition(cond: str, workspace: Path) -> bool:
    """Evaluate a tiny condition expression. Supported forms:
       <query> <op> <int>
       e.g. "bills_overdue > 0"
    """
    if not cond:
        return True
    cond = cond.strip()
    if cond.lower() == "always":
        return True
    if cond.lower() == "never":
        return False
    m = CONDITION_RE.match(cond)
    if not m:
        return False
    name, op, value = m.group(1), m.group(2), m.group(3)
    actual = query_workspace(workspace, name)
    try:
        rhs = int(value)
    except ValueError:
        return False
    if op == "==":
        return actual == rhs
    if op == "!=":
        return actual != rhs
    if op == "<":
        return actual < rhs
    if op == "<=":
        return actual <= rhs
    if op == ">":
        return actual > rhs
    if op == ">=":
        return actual >= rhs
    return False


# --- Resolver -----------------------------------------------------------------

def resolve(workspace: Path, playbook: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk steps, evaluate conditions, return the flat list of skills to run."""
    out: list[dict[str, Any]] = []
    for step in playbook.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        if "skill" in step:
            cond = step.get("if")
            if cond is not None and not eval_condition(str(cond), workspace):
                continue
            out.append({
                "skill": step["skill"],
                "args": step.get("args", ""),
                "note": step.get("note", ""),
                "via_condition": cond,
            })
            continue
        if "if" in step:
            cond_ok = eval_condition(str(step.get("if")), workspace)
            branch = step.get("then" if cond_ok else "else") or []
            for sub in (branch if isinstance(branch, list) else [branch]):
                if isinstance(sub, str):
                    out.append({"skill": sub, "args": "", "note": "", "via_condition": step.get("if")})
                elif isinstance(sub, dict):
                    out.append({
                        "skill": sub.get("skill", ""),
                        "args": sub.get("args", ""),
                        "note": sub.get("note", ""),
                        "via_condition": step.get("if"),
                    })
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="play")
    parser.add_argument("--framework", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List available playbooks.")
    r = sub.add_parser("resolve", help="Resolve a playbook into a skill sequence.")
    r.add_argument("name", type=str)
    r.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workspace = args.workspace or args.framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        rows = list_playbooks(args.framework, workspace)
        if not rows:
            print("(no playbooks)")
            return 0
        print(f"{'name':<28}{'origin':<12}{'steps':<8}{'trigger phrases'}")
        print("-" * 100)
        for r in rows:
            triggers = ", ".join(r.get("trigger") or [])[:50]
            print(f"{r['stem']:<28}{r['origin']:<12}{r['step_count']:<8}{triggers}")
        return 0
    if args.cmd == "resolve":
        path = find_playbook(args.framework, workspace, args.name)
        if path is None:
            print(f"playbook not found: {args.name}", file=sys.stderr)
            return 1
        data = load_yaml(path) or {}
        steps = resolve(workspace, data)
        if args.json:
            print(json.dumps({"name": args.name, "steps": steps}, indent=2, default=str))
            return 0
        print(f"# Playbook: {data.get('name', args.name)}")
        print(f"# {len(steps)} step(s) to execute (after condition evaluation)")
        print()
        for i, s in enumerate(steps, 1):
            note = f" — {s['note']}" if s.get("note") else ""
            args_part = f" {s['args']}" if s.get("args") else ""
            print(f"{i}. {s['skill']}{args_part}{note}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
