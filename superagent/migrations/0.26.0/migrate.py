#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Apply the 0.26.0 workspace migration (from 0.25.0): update and brief, the asks queue.

0.26.0 splits fetching from telling (``contracts/update-loop.md``): the
``update`` skill brings the stores up to date and the ``brief`` skill reports
from them. This script prepares a workspace for that, in this order:

1. Add the two update cadences to ``preferences.cadences`` in
   ``_memory/config.yaml`` (``update_light: "every 30 minutes"`` and
   ``update_full: "06:00"``), as text at the end of that block. A key that is
   already there is left alone.
2. Add the ``preferences.update`` block (``drop_folders: []``) at the end of
   the ``preferences`` mapping, as text. A config that has the block is left
   alone.
3. Create the asks queue ``_memory/asks.yaml`` from the framework template
   when the workspace has none. It starts with no ask.
4. Set ``.version`` to 0.26.0.

Every other line of ``config.yaml`` is untouched, and the script checks that
the file reads back as the old content plus the new keys. What it inserted is
kept in the ledger ``_memory/_retired/0.26.0-ledger.yaml`` for the revert.
``context.yaml`` is not touched: the stamps of the update duties appear in
``last_runs`` on their first run.

Idempotent: a second run reports "nothing to do". Nothing is deleted and no
existing row changes.

Usage::

    uv run python superagent/migrations/0.26.0/migrate.py --workspace <path> [--dry-run]

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

TO_VERSION = "0.26.0"
FROM_VERSION = "0.25.0"
CONFIG_REL = "_memory/config.yaml"
ASKS_REL = "_memory/asks.yaml"
LEDGER_REL = "_memory/_retired/0.26.0-ledger.yaml"
BLOCK_KEY = "update"
#: The cadence keys 0.26.0 adds, with the line each is written as (one level of indent).
CADENCE_LINES = {
    "update_light": 'update_light: "every 30 minutes"   # the update cycle\'s light tier: how often it may look\n',
    "update_full": 'update_full: "06:00"               # the full tier, once a day\n',
}
CADENCE_VALUES = {"update_light": "every 30 minutes", "update_full": "06:00"}
#: The block as 0.26.0 seeds it, written for a two-space indent.
UPDATE_BLOCK = '''\
  # The update cycle (see contracts/update-loop.md). Absent block = these defaults.
  update:
    # Extra folders drained like `Inbox/`: a folder a sync tool shares between
    # machines, a scanner's output folder. Absolute paths or `~/...`.
    drop_folders: []
'''
ASKS_FALLBACK = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Superagent memory: the asks queue (contracts/autonomy.md § 7).\n"
    "schema_version: 1\n"
    "last_updated: null\n"
    "asks: []\n"
)
LEDGER_HEADER = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Ledger of the 0.26.0 migration: what it added, kept for the revert.\n"
)


def now_iso() -> str:
    """Current local time, ISO 8601 with offset, seconds precision."""
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def preferences_span(lines: list[str]) -> tuple[int, int] | None:
    """(head line, end line) of the top-level `preferences:` block mapping; None when absent."""
    head = next((i for i, line in enumerate(lines) if re.match(r"^preferences\s*:\s*(#.*)?$", line)), None)
    if head is None:
        return None
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    return head, end


def reindent(block: str, indent: int) -> str:
    """`block` (two spaces per level) with `indent` spaces per level instead."""
    if indent == 2:
        return block
    out = []
    for line in block.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        out.append(" " * ((len(line) - len(stripped)) // 2 * indent) + stripped)
    return "".join(out)


def insert_cadences(text: str, missing: list[str]) -> tuple[str, str] | None:
    """Add the `missing` cadence keys at the end of `preferences.cadences`.

    Returns (new text, the inserted text), or None when the file has no
    `cadences` block mapping with at least one key to take the indent from.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    span = preferences_span(lines)
    if span is None:
        return None
    head, end = span
    block = next((j for j in range(head + 1, end)
                  if re.match(r"^ +cadences\s*:\s*(#.*)?$", lines[j])), None)
    if block is None:
        return None
    base = _indent(lines[block])
    stop = next((j for j in range(block + 1, end)
                 if lines[j].strip() and not lines[j].lstrip().startswith("#")
                 and _indent(lines[j]) <= base), end)
    children = [j for j in range(block + 1, stop)
                if lines[j].strip() and not lines[j].lstrip().startswith("#")]
    if not children:
        return None
    pad = " " * _indent(lines[children[0]])
    inserted = "".join(pad + CADENCE_LINES[key] for key in missing)
    at = children[-1] + 1
    return "".join(lines[:at]) + inserted + "".join(lines[at:]), inserted


def insert_block(text: str) -> tuple[str, str] | None:
    """Add the `update` block at the end of the top-level `preferences` mapping.

    Returns (new text, the inserted text), or None when the file has no block
    mapping named `preferences` to add it to.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    span = preferences_span(lines)
    if span is None:
        return None
    head, end = span
    inside = [j for j in range(head + 1, end) if lines[j].strip() and lines[j].startswith(" ")]
    children = [j for j in inside if not lines[j].lstrip().startswith("#")]
    if not children:
        return None
    inserted = "\n" + reindent(UPDATE_BLOCK, _indent(lines[children[0]]))
    at = inside[-1] + 1
    return "".join(lines[:at]) + inserted + "".join(lines[at:]), inserted


class Migration:
    """One run of the 0.26.0 migration over a workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 out=print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else _REPO_ROOT / "superagent"
        self.dry_run = dry_run
        self.out = out
        self.changed: list[str] = []
        self.ledger: dict[str, Any] = {}

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
            self._say(f"{CONFIG_REL} has no `preferences` block to extend; left alone. The update "
                      "cadences and drop folders use their defaults")
            return
        new_text, expected_prefs = text, dict(prefs)
        cadences = prefs.get("cadences")
        missing = [k for k in CADENCE_LINES if not isinstance(cadences, dict) or k not in cadences]
        if missing and isinstance(cadences, dict):
            result = insert_cadences(new_text, missing)
            if result is None:
                self._say(f"{CONFIG_REL}: `preferences.cadences` is not a block mapping; the update "
                          "cadences use their defaults")
            else:
                new_text, inserted = result
                expected_prefs["cadences"] = {**cadences, **{k: CADENCE_VALUES[k] for k in missing}}
                self.ledger["inserted_cadences"] = inserted
                self._say(f"add {', '.join(missing)} to `preferences.cadences` in {CONFIG_REL}")
        elif missing:
            self._say(f"{CONFIG_REL} has no `preferences.cadences` block; the update cadences use "
                      "their defaults")
        if BLOCK_KEY not in prefs:
            result = insert_block(new_text)
            if result is not None:
                new_text, inserted = result
                expected_prefs[BLOCK_KEY] = yaml.safe_load(UPDATE_BLOCK)[BLOCK_KEY]
                self.ledger["inserted_block"] = inserted
                self._say(f"add `preferences.{BLOCK_KEY}` to {CONFIG_REL} (no extra drop folder)")
        if new_text == text:
            return
        if yaml.safe_load(new_text) != {**before, "preferences": expected_prefs}:
            raise ValueError(f"{CONFIG_REL}: adding the update keys would change another key; left alone")
        self.changed.append(CONFIG_REL)
        if self.dry_run:
            return
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)

    def step_asks(self) -> None:
        path = self.ws / ASKS_REL
        if path.exists():
            return
        template = self.framework / "templates" / "memory" / "asks.yaml"
        text = template.read_text(encoding="utf-8") if template.is_file() else ASKS_FALLBACK
        self._say(f"create {ASKS_REL} (the asks queue, with no ask)")
        self.changed.append(ASKS_REL)
        self.ledger["asks_created"] = True
        if not self.dry_run:
            path.write_text(text, encoding="utf-8")

    def step_ledger(self) -> None:
        if not self.ledger or self.dry_run:
            return
        path = self.ws / LEDGER_REL
        old: dict[str, Any] = {}
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            old = loaded if isinstance(loaded, dict) else {}
        merged = {"schema_version": 1, "migrated_at": old.get("migrated_at") or now_iso(),
                  **{k: v for k, v in old.items() if k not in ("schema_version", "migrated_at")},
                  **self.ledger}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LEDGER_HEADER + yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")

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
        for rel in (CONFIG_REL, ASKS_REL):
            path = self.ws / rel
            if path.exists():
                try:
                    yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError as exc:
                    self.out(f"error: {rel} is not well-formed YAML: {exc}")
                    return 1
        for step in (self.step_config, self.step_asks, self.step_ledger, self.step_version):
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
