#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Apply the 0.27.0 workspace migration (from 0.26.0): Autocode mode and agent creation.

0.27.0 lets agents run as sessions registered with Autocode
(``rules/autocode.md``) and adds the ``add-agent`` skill. Nothing runs by
itself after this migration: the switch it adds is off. In this order:

1. Add three keys to ``_memory/config.yaml``, as text, each one line at the
   end of its block: ``preferences.autocode.enabled: false`` (the switch),
   and ``preferences.update.work_hours: null`` and ``off_days: []`` (when a
   scheduled update may run). A key that is already there is left alone, and
   so is a config without the block (the tools use the defaults).
2. Set ``.version`` to 0.27.0.

Every other line of ``config.yaml`` is untouched, and the script checks that
the file reads back as the old content plus the new keys. What it inserted is
kept in the ledger ``_memory/_retired/0.27.0-ledger.yaml`` for the revert. The
registry ``_memory/agents.yaml`` is not touched: grants written from now on
carry more fields, and older rows read as before.

Idempotent: a second run reports "nothing to do".

Usage::

    uv run python superagent/migrations/0.27.0/migrate.py --workspace <path> [--dry-run]

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

TO_VERSION = "0.27.0"
FROM_VERSION = "0.26.0"
CONFIG_REL = "_memory/config.yaml"
LEDGER_REL = "_memory/_retired/0.27.0-ledger.yaml"
#: block -> [(key, default value, the line it is written as, without indent)].
KEYS: dict[str, list[tuple[str, Any, str]]] = {
    "autocode": [
        ("enabled", False,
         "enabled: false               # the switch: true = agents run as sessions registered "
         "with Autocode (rules/autocode.md)\n"),
    ],
    "update": [
        ("work_hours", None,
         'work_hours: null             # when a scheduled update may run, "07:00-22:00"; null = any hour\n'),
        ("off_days", [],
         'off_days: []                 # weekday names ("Saturday") or dates ("2026-12-25")\n'),
    ],
}
LEDGER_HEADER = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Ledger of the 0.27.0 migration: what it added, kept for the revert.\n"
)


def now_iso() -> str:
    """Current local time, ISO 8601 with offset, seconds precision."""
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def block_span(lines: list[str], block: str) -> tuple[int, int, int] | None:
    """(key line, line after the block's last line, indent of its children) of `preferences.<block>`.

    None when `preferences` or the block is not a block mapping with at least
    one child to take the indent from.
    """
    head = next((i for i, line in enumerate(lines) if re.match(r"^preferences\s*:\s*(#.*)?$", line)), None)
    if head is None:
        return None
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    children = [j for j in range(head + 1, end)
                if lines[j].strip() and lines[j].startswith(" ") and not lines[j].lstrip().startswith("#")]
    if not children:
        return None
    level = _indent(lines[children[0]])
    hit = next((j for j in children if re.match(rf"^ {{{level}}}{re.escape(block)}\s*:\s*(#.*)?$", lines[j])), None)
    if hit is None:
        return None
    last = hit
    for j in range(hit + 1, end):
        if lines[j].strip() and _indent(lines[j]) <= level:
            break
        if lines[j].strip():
            last = j
    inner = [j for j in range(hit + 1, last + 1)
             if lines[j].strip() and not lines[j].lstrip().startswith("#")]
    if not inner:
        return None
    return hit, last + 1, _indent(lines[inner[0]])


def insert_keys(text: str, block: str, missing: list[str]) -> tuple[str, str] | None:
    """Add the `missing` keys of `block` at the end of that block; (new text, inserted text) or None."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    span = block_span(lines, block)
    if span is None:
        return None
    _hit, at, indent = span
    inserted = "".join(" " * indent + line for key, _value, line in KEYS[block] if key in missing)
    return "".join(lines[:at]) + inserted + "".join(lines[at:]), inserted


class Migration:
    """One run of the 0.27.0 migration over a workspace."""

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
        if not isinstance(prefs, dict):
            self._say(f"{CONFIG_REL} has no `preferences` block to extend; left alone (the switch is off)")
            return
        new_text, expected, ledger = text, dict(prefs), {}
        for block, keys in KEYS.items():
            current = prefs.get(block)
            if not isinstance(current, dict):
                self._say(f"{CONFIG_REL} has no `preferences.{block}` block; its new keys use their defaults")
                continue
            missing = [key for key, _value, _line in keys if key not in current]
            if not missing:
                continue
            result = insert_keys(new_text, block, missing)
            if result is None:
                self._say(f"{CONFIG_REL}: `preferences.{block}` is not a block mapping; left alone")
                continue
            new_text, inserted = result
            expected[block] = {**current, **{key: value for key, value, _line in keys if key in missing}}
            ledger[block] = inserted
            self._say(f"add {', '.join(missing)} to `preferences.{block}` in {CONFIG_REL}")
        if new_text == text:
            return
        if yaml.safe_load(new_text) != {**before, "preferences": expected}:
            raise ValueError(f"{CONFIG_REL}: adding the new keys would change another key; left alone")
        self.changed.append(CONFIG_REL)
        if self.dry_run:
            return
        ledger_path = self.ws / LEDGER_REL
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(LEDGER_HEADER + yaml.safe_dump(
            {"schema_version": 1, "migrated_at": now_iso(), "inserted": ledger}, sort_keys=False),
            encoding="utf-8")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)

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
        path = self.ws / CONFIG_REL
        if path.exists():
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.out(f"error: {CONFIG_REL} is not well-formed YAML: {exc}")
                return 1
        for step in (self.step_config, self.step_version):
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
