#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Apply the 0.24.0 workspace migration (from 0.23.0): mixed-machine safety.

0.24.0 makes a workspace safe to open on more than one machine
(``rules/concurrent-sessions.md``). The two logs every session appends to
become one file per machine, and the on-open check stops writing a shared
file. This script prepares a workspace for that, in this order:

1. Create ``_memory/interaction-log/``, ``_memory/user-queries/`` and
   ``_memory/machines/`` (the per-machine log files and beat files are written
   there by the tools, each on its first use).
2. Freeze the single log files as read-only history. ``interaction-log.yaml``
   and ``user-queries.jsonl`` stay exactly where they are, byte for byte;
   their size and row count go into the marker
   ``_memory/interaction-log/_frozen.yaml``, which is how ``heal strays``
   later tells a row appended after the freeze.
3. Remove the ``refresh_suggested`` key (and the two comment lines above it)
   from ``context.yaml``: the on-open nag marker is machine-local now
   (``~/.superagent/state/``). The removed lines are kept in the ledger
   ``_memory/_retired/0.24.0-ledger.yaml`` for the revert.
4. Set ``.version`` to 0.24.0.

Idempotent: a second run reports "nothing to do". Nothing is deleted.

Usage::

    uv run python superagent/migrations/0.24.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

TO_VERSION = "0.24.0"
FROM_VERSION = "0.23.0"
FOLDERS = ("_memory/interaction-log", "_memory/user-queries", "_memory/machines")
ILOG_REL = "_memory/interaction-log.yaml"
QUERIES_REL = "_memory/user-queries.jsonl"
MARKER_REL = "_memory/interaction-log/_frozen.yaml"
CONTEXT_REL = "_memory/context.yaml"
LEDGER_REL = "_memory/_retired/0.24.0-ledger.yaml"
RETIRED_KEY = "refresh_suggested"
MARKER_HEADER = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Written once by the 0.24.0 migration. From here on the single log files named\n"
    "# below are read-only history: new rows go to one file per machine\n"
    "# (rules/concurrent-sessions.md). `rows` and `size` are what the files held when\n"
    "# they were frozen; `heal strays` uses them to find a row appended afterwards.\n"
)
LEDGER_HEADER = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Ledger of the 0.24.0 migration: what it removed, kept for the revert.\n"
)


def now_iso() -> str:
    """Current local time, ISO 8601 with offset, seconds precision."""
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def count_log_rows(path: Path) -> tuple[int, str | None]:
    """(number of rows under ``entries``, id of the last row that has one)."""
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)  # a safe loader
    rows = data.get("entries") if isinstance(data, dict) else None
    if rows is None:
        return 0, None
    if not isinstance(rows, list):
        raise ValueError(f"{path.name}: `entries` is not a list")
    last_id = next((str(r["id"]) for r in reversed(rows) if isinstance(r, dict) and r.get("id")), None)
    return len(rows), last_id


def split_retired_key(text: str) -> tuple[str, str, str | None] | None:
    """Cut the ``refresh_suggested`` block out of a context.yaml text.

    Returns (text without the block, the removed block, the line that came
    right before it), or None when the key is not there. The block is the
    key's line, the comment lines directly above it, and one blank line after.
    """
    lines = text.splitlines(keepends=True)
    hit = next((i for i, line in enumerate(lines) if line.startswith(f"{RETIRED_KEY}:")), None)
    if hit is None:
        return None
    start = hit
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    end = hit + 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    anchor = next((lines[i] for i in range(start - 1, -1, -1) if lines[i].strip()), None)
    return "".join(lines[:start] + lines[end:]), "".join(lines[start:end]), anchor


class Migration:
    """One run of the 0.24.0 migration over a workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 out=print) -> None:
        self.ws = Path(workspace)
        self.framework = framework
        self.dry_run = dry_run
        self.out = out
        self.changed: list[str] = []

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    def step_folders(self) -> None:
        for rel in FOLDERS:
            folder = self.ws / rel
            if folder.is_dir():
                continue
            self._say(f"create {rel}/")
            self.changed.append(rel)
            if not self.dry_run:
                folder.mkdir(parents=True, exist_ok=True)

    def step_freeze(self) -> None:
        marker = self.ws / MARKER_REL
        if marker.exists():
            return
        block: dict[str, Any] = {"schema_version": 1, "frozen_at": now_iso()}
        ilog = self.ws / ILOG_REL
        if ilog.is_file():
            rows, last_id = count_log_rows(ilog)
            block["interaction_log"] = {"file": ilog.name, "rows": rows, "size": ilog.stat().st_size,
                                        "last_id": last_id}
            self._say(f"freeze {ILOG_REL} as read-only history ({rows} rows)")
        queries = self.ws / QUERIES_REL
        if queries.is_file():
            with queries.open("rb") as fh:
                lines = sum(1 for _ in fh)
            block["user_queries"] = {"file": queries.name, "lines": lines, "size": queries.stat().st_size}
            self._say(f"freeze {QUERIES_REL} as read-only history ({lines} lines)")
        if len(block) == 2:
            return  # a workspace with neither single file has nothing to freeze
        self.changed.append(MARKER_REL)
        if not self.dry_run:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(MARKER_HEADER + yaml.safe_dump(block, sort_keys=False), encoding="utf-8")

    def step_context(self) -> None:
        path = self.ws / CONTEXT_REL
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        cut = split_retired_key(text)
        if cut is None:
            return
        new_text, block, anchor = cut
        before = yaml.safe_load(text)
        after = yaml.safe_load(new_text)
        if not isinstance(before, dict) or {k: v for k, v in before.items() if k != RETIRED_KEY} != after:
            raise ValueError(f"{CONTEXT_REL}: removing `{RETIRED_KEY}` would change another key; left alone")
        self._say(f"remove `{RETIRED_KEY}` from {CONTEXT_REL} (the nag marker is machine-local now)")
        self.changed.append(CONTEXT_REL)
        if self.dry_run:
            return
        ledger = self.ws / LEDGER_REL
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(LEDGER_HEADER + yaml.safe_dump({
            "schema_version": 1, "migrated_at": now_iso(),
            "context": {"removed_block": block, "after_line": anchor},
        }, sort_keys=False), encoding="utf-8")
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
        for rel in (CONTEXT_REL, "_memory/config.yaml"):
            path = self.ws / rel
            if path.exists():
                try:
                    yaml.safe_load(path.read_text(encoding="utf-8"))
                except yaml.YAMLError as exc:
                    self.out(f"error: {rel} is not well-formed YAML: {exc}")
                    return 1
        for step in (self.step_folders, self.step_freeze, self.step_context, self.step_version):
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
