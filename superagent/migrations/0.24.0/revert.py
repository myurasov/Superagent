#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.24.0 migration (back to 0.23.0).

The 0.23.0 tools know one interaction-log file and one prompt-log file, so
everything logged per machine since the migration is folded back first. In
this order:

1. Append every row of every ``_memory/interaction-log/<machine>.yaml`` to the
   single ``_memory/interaction-log.yaml``, oldest first (created when the
   workspace has none). Same for ``_memory/user-queries/<machine>.jsonl`` into
   ``_memory/user-queries.jsonl``. A row already present there is not added
   twice, so a second run changes nothing.
2. Park the per-machine log files under
   ``_memory/_retired/0.24.0-originals/`` (moved, never deleted), remove the
   freeze marker and the beat files (a beat is rewritten on every open), and
   remove the three folders when they are empty.
3. Put the ``refresh_suggested`` lines back into ``context.yaml`` from the
   ledger, and remove the ledger.
4. Set ``.version`` to 0.23.0.

Machine-local state under ``~/.superagent/`` (the machine's name, the nag
marker) is not workspace data and is left alone.

Usage::

    uv run python superagent/migrations/0.24.0/revert.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

ORIGINALS_REL = "_memory/_retired/0.24.0-originals"
ILOG_SKELETON = "schema_version: 1\nentries:\n"


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_24_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _machine_files(folder: Path, suffix: str) -> list[Path]:
    from superagent.tools.syncnames import is_sync_artifact
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(f"*{suffix}")
                  if p.is_file() and not is_sync_artifact(p) and not p.name.startswith(("_", ".")))


def fold_interaction_log(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> int:
    """Append the per-machine rows to the single file; return how many were added."""
    from superagent.tools import machines, signals
    files = _machine_files(ws / "_memory" / machines.ILOG_DIR, ".yaml")
    if not files:
        return 0
    single = ws / mig.ILOG_REL
    text = single.read_text(encoding="utf-8") if single.exists() else ""
    _data, existing = signals.parse_rows(single.name, text, machines.ILOG_KEY)
    present = {json.dumps(r, sort_keys=True, default=str) for r in existing}
    rows = [row for path, _i, row in machines.interaction_log_rows(ws) if path in files
            and json.dumps(row, sort_keys=True, default=str) not in present]
    if rows:
        say(f"fold {len(rows)} row(s) from {len(files)} machine file(s) into {mig.ILOG_REL}")
        if not dry_run:
            new_text = signals.edit_rows_text(single.name, text, machines.ILOG_KEY, append=rows,
                                              skeleton=ILOG_SKELETON)
            signals._atomic_write(single, new_text)
    return len(rows)


def fold_user_queries(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> int:
    """Append the per-machine prompt-log lines to the single file; return how many were added."""
    from superagent.tools import machines
    files = _machine_files(ws / "_memory" / machines.QUERIES_DIR, ".jsonl")
    if not files:
        return 0
    single = ws / mig.QUERIES_REL
    existing = single.read_text(encoding="utf-8").splitlines() if single.exists() else []
    present = set(existing)
    lines: list[tuple[str, str]] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line in present:
                continue
            present.add(line)
            try:
                stamp = str(json.loads(line).get("ts") or "")
            except (ValueError, AttributeError):
                stamp = ""
            lines.append((stamp, line))
    lines.sort(key=lambda pair: pair[0])
    if lines:
        say(f"fold {len(lines)} line(s) from {len(files)} machine file(s) into {mig.QUERIES_REL}")
        if not dry_run:
            body = "\n".join(existing + [line for _stamp, line in lines]) + "\n"
            tmp = single.with_name(single.name + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(single)
    return len(lines)


def park_and_clean(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> None:
    """Park the per-machine log files, drop the marker and beats, remove empty folders."""
    from superagent.tools import machines
    originals = ws / ORIGINALS_REL
    for folder_name, suffix in ((machines.ILOG_DIR, ".yaml"), (machines.QUERIES_DIR, ".jsonl")):
        for path in _machine_files(ws / "_memory" / folder_name, suffix):
            say(f"park _memory/{folder_name}/{path.name}")
            if not dry_run:
                dest = originals / folder_name / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
    disposable = [ws / mig.MARKER_REL, *sorted((ws / "_memory" / machines.BEATS_DIR).glob("*.json"))]
    for path in disposable:
        if path.is_file():
            say(f"remove {path.relative_to(ws).as_posix()}")
            if not dry_run:
                path.unlink()
    for rel in mig.FOLDERS:
        folder = ws / rel
        if folder.is_dir() and not dry_run and not any(folder.iterdir()):
            folder.rmdir()
        elif folder.is_dir() and not dry_run:
            say(f"{rel}/ is not empty (sync conflict copies?); left in place")


def restore_context(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> None:
    """Put the `refresh_suggested` lines back where the migration took them from."""
    ledger = ws / mig.LEDGER_REL
    if not ledger.is_file():
        return
    data = yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}
    entry = data.get("context") if isinstance(data, dict) else None
    path = ws / mig.CONTEXT_REL
    if isinstance(entry, dict) and entry.get("removed_block") and path.is_file():
        text = path.read_text(encoding="utf-8")
        if mig.split_retired_key(text) is None:
            block, anchor = str(entry["removed_block"]), entry.get("after_line")
            lines = text.splitlines(keepends=True)
            at = next((i + 1 for i, line in enumerate(lines) if anchor and line == anchor), None)
            if at is None:
                at = next((i + 1 for i, line in enumerate(lines) if line.startswith("schema_version:")), 0)
            if at < len(lines) and not lines[at].strip():
                at += 1  # keep the blank line that separated the anchor from the block
            new_text = "".join(lines[:at]) + block + "".join(lines[at:])
            say(f"restore `{mig.RETIRED_KEY}` in {mig.CONTEXT_REL}")
            if not dry_run:
                yaml.safe_load(new_text)  # refuse to write a text that does not parse
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(new_text, encoding="utf-8")
                tmp.replace(path)
    if not dry_run:
        ledger.unlink()


def run_revert(workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
               out=print) -> int:
    """Revert the migration. Returns a process exit code."""
    del framework
    mig = _load_migrate()
    ws = Path(workspace)
    if not (ws / "_memory").is_dir():
        out(f"error: no workspace at {ws} (no _memory/ folder)")
        return 1

    def say(msg: str) -> None:
        out(("[dry-run] " if dry_run else "") + msg)

    try:
        fold_interaction_log(ws, mig, dry_run=dry_run, say=say)
        fold_user_queries(ws, mig, dry_run=dry_run, say=say)
        park_and_clean(ws, mig, dry_run=dry_run, say=say)
        restore_context(ws, mig, dry_run=dry_run, say=say)
    except Exception as exc:  # every failure halts the revert with the file named
        out(f"error: revert halted: {exc}")
        return 1
    from superagent.tools.version import set_workspace_version, workspace_version
    if workspace_version(ws) != mig.FROM_VERSION:
        say(f".version: {workspace_version(ws)} -> {mig.FROM_VERSION}")
        if not dry_run:
            set_workspace_version(ws, mig.FROM_VERSION)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.24.0",
                                     description="Revert the 0.24.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_revert(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
