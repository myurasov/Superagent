#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Apply the 0.25.0 workspace migration (from 0.24.0): agents as first-class objects.

0.25.0 gives the framework a roster of agents (``contracts/agents.md``): role
files with duties and dev twins, ids built from a configured prefix, and a
registry of instances. This script prepares a workspace for that, in this
order:

1. Seed ``preferences.autocode`` in ``_memory/config.yaml`` (the id prefix
   ``sa``, the per-tier model defaults and the cost cap, all at their
   defaults). The block is inserted as text at the end of the ``preferences``
   mapping; every other line of the file is untouched. A config that already
   has the block is left alone. The inserted text is kept in the ledger
   ``_memory/_retired/0.25.0-ledger.yaml`` for the revert.
2. Create the agent registry ``_memory/agents.yaml`` from the framework
   template when the workspace has none. It starts with no agent.
3. Set ``.version`` to 0.25.0.

Idempotent: a second run reports "nothing to do". Nothing is deleted and no
existing row changes. Rows of ``supertailor-suggestions.yaml`` written before
0.25.0 have no ``agent`` field and are read as concerning the lead.

Usage::

    uv run python superagent/migrations/0.25.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

TO_VERSION = "0.25.0"
FROM_VERSION = "0.24.0"
CONFIG_REL = "_memory/config.yaml"
REGISTRY_REL = "_memory/agents.yaml"
LEDGER_REL = "_memory/_retired/0.25.0-ledger.yaml"
BLOCK_KEY = "autocode"
#: The block as 0.25.0 seeds it, written for a two-space indent.
AUTOCODE_BLOCK = '''\
  # Agents and Autocode (see contracts/agents.md). Autocode is an optional
  # runtime that keeps agents running as registered sessions. With it off, the
  # chat session takes whichever role the work needs. Absent block = these
  # defaults.
  autocode:
    # First part of every agent id (`<id_prefix>:<name>`), so several users can
    # share one Autocode server. A lowercase letter first, then lowercase
    # letters, digits or `-`, 16 characters at most. Reserved: owner, autocode,
    # me, project, dev.
    id_prefix: "sa"
    # Default model per tier; null = the harness's own default. `runner` is for
    # the agents that do the work, `dev` for their dev twins. A row in
    # `_memory/agents.yaml` overrides the model of its agent.
    models:
      runner:
        model: null              # "<provider>/<model>", as the harness names it
        variant: null            # the model's variant (e.g. a reasoning effort)
      dev:
        model: null
        variant: null
    # Daily cost cap per agent, in USD; null = no cap. A registry row overrides it.
    daily_usd: null
'''
REGISTRY_FALLBACK = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Superagent memory: the agent registry (contracts/agents.md).\n"
    "schema_version: 1\n"
    "last_updated: null\n"
    "agents: []\n"
)
LEDGER_HEADER = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Ledger of the 0.25.0 migration: what it added, kept for the revert.\n"
)


def now_iso() -> str:
    """Current local time, ISO 8601 with offset, seconds precision."""
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def reindent(block: str, indent: int) -> str:
    """`block` (two spaces per level) with `indent` spaces per level instead."""
    if indent == 2:
        return block
    out = []
    for line in block.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        out.append(" " * ((len(line) - len(stripped)) // 2 * indent) + stripped)
    return "".join(out)


def insert_block(text: str) -> tuple[str, str] | None:
    """Add the autocode block at the end of the top-level `preferences` mapping.

    Returns (new text, the inserted text), or None when the file has no block
    mapping named `preferences` to add it to. The insertion is one contiguous
    run of new lines; no existing line changes.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    head = next((i for i, line in enumerate(lines) if re.match(r"^preferences\s*:\s*(#.*)?$", line)), None)
    if head is None:
        return None
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    inside = [j for j in range(head + 1, end) if lines[j].strip() and lines[j].startswith(" ")]
    children = [j for j in inside if not lines[j].lstrip().startswith("#")]
    if not children:
        return None
    indent = len(lines[children[0]]) - len(lines[children[0]].lstrip(" "))
    inserted = "\n" + reindent(AUTOCODE_BLOCK, indent)
    at = inside[-1] + 1
    return "".join(lines[:at]) + inserted + "".join(lines[at:]), inserted


class Migration:
    """One run of the 0.25.0 migration over a workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 out=print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else _REPO_ROOT / "superagent"
        self.dry_run = dry_run
        self.out = out
        self.changed: list[str] = []

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    def step_config(self) -> None:
        path = self.ws / CONFIG_REL
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        before = yaml.safe_load(text)
        prefs = before.get("preferences") if isinstance(before, dict) else None
        if isinstance(prefs, dict) and BLOCK_KEY in prefs:
            return
        result = insert_block(text) if isinstance(prefs, dict) else None
        if result is None:
            self._say(f"{CONFIG_REL} has no `preferences` block to extend; left alone. Agent ids "
                      "use the default prefix `sa` until `preferences.autocode.id_prefix` is set")
            return
        new_text, inserted = result
        after = yaml.safe_load(new_text)
        expected = {**before, "preferences": {**prefs, BLOCK_KEY: yaml.safe_load(AUTOCODE_BLOCK)[BLOCK_KEY]}}
        if after != expected:
            raise ValueError(f"{CONFIG_REL}: adding `preferences.{BLOCK_KEY}` would change another "
                             "key; left alone")
        self._say(f"add `preferences.{BLOCK_KEY}` to {CONFIG_REL} (id prefix `sa`, no model, no cap)")
        self.changed.append(CONFIG_REL)
        if self.dry_run:
            return
        ledger = self.ws / LEDGER_REL
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(LEDGER_HEADER + yaml.safe_dump({
            "schema_version": 1, "migrated_at": now_iso(),
            "config": {"inserted_block": inserted},
        }, sort_keys=False), encoding="utf-8")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)

    def step_registry(self) -> None:
        path = self.ws / REGISTRY_REL
        if path.exists():
            return
        template = self.framework / "templates" / "memory" / "agents.yaml"
        text = template.read_text(encoding="utf-8") if template.is_file() else REGISTRY_FALLBACK
        self._say(f"create {REGISTRY_REL} (the agent registry, with no agent)")
        self.changed.append(REGISTRY_REL)
        if not self.dry_run:
            path.write_text(text, encoding="utf-8")

    def step_version(self) -> None:
        from superagent.tools.version import set_workspace_version, workspace_version
        current = workspace_version(self.ws)
        if current == TO_VERSION:
            return
        self._say(f".version: {current} -> {TO_VERSION}")
        self.changed.append(".version")
        if not self.dry_run:
            set_workspace_version(self.ws, TO_VERSION)

    def run(self) -> int:
        """Run pre-flight + all steps. Returns a process exit code."""
        if not (self.ws / "_memory").is_dir():
            self.out(f"error: no workspace at {self.ws} (no _memory/ folder)")
            return 1
        for rel in (CONFIG_REL, REGISTRY_REL):
            path = self.ws / rel
            if path.exists():
                try:
                    yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError as exc:
                    self.out(f"error: {rel} is not well-formed YAML: {exc}")
                    return 1
        for step in (self.step_config, self.step_registry, self.step_version):
            try:
                step()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.out(f"error in {step.__name__}: {exc}")
                self.out("halted; nothing after this step was applied. Run revert.py to undo.")
                return 1
        if self.changed:
            self._say(f"{len(self.changed)} item(s) {'would be ' if self.dry_run else ''}changed")
        else:
            self._say(f"nothing to do; workspace already at {TO_VERSION} shape")
        return 0


def run_migration(workspace: Path, **kwargs: Any) -> int:
    """Convenience wrapper: `Migration(workspace, **kwargs).run()`."""
    return Migration(workspace, **kwargs).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"migrate-{TO_VERSION}",
        description=f"Apply the {TO_VERSION} workspace migration (idempotent).")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None,
                        help="Path to superagent/ (defaults to this script's tree).")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_migration(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
