#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.25.0 workspace migration (back to 0.24.0).

In this order:

1. Take ``preferences.autocode`` out of ``_memory/config.yaml``, when the
   migration put it there (the ledger says so). The text the migration inserted
   is removed byte for byte when it is still there unchanged. A block that was
   edited since (another prefix, a model) is cut out by its shape instead, and
   the removed lines are saved to
   ``_memory/_retired/0.25.0-originals/config-autocode.yaml`` so no setting is
   lost. A block the workspace had before the migration is left alone.
2. Remove the agent registry ``_memory/agents.yaml``: deleted when it names no
   agent, moved to ``_memory/_retired/0.25.0-originals/agents.yaml`` when it
   does.
3. Remove the ledger and write 0.24.0 to ``.version``.

The 0.24.0 tools read neither the block nor the registry, so a revert loses no
behavior of that version.

Usage::

    uv run python superagent/migrations/0.25.0/revert.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

ORIGINALS_REL = "_memory/_retired/0.25.0-originals"


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_25_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def cut_block(text: str, key: str) -> tuple[str, str] | None:
    """Cut `preferences.<key>` out of a config text by its shape.

    Returns (text without the block, the removed lines), or None when the key
    is not a child of the top-level `preferences` mapping. The block is the
    key's line, every deeper-indented line below it, the comment lines
    directly above it, and one blank line above those.
    """
    lines = text.splitlines(keepends=True)
    head = next((i for i, line in enumerate(lines) if line.startswith("preferences:")), None)
    if head is None:
        return None
    end = next((j for j in range(head + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    hit = next((j for j in range(head + 1, end) if lines[j].lstrip(" ").startswith(f"{key}:")), None)
    if hit is None:
        return None
    indent = len(lines[hit]) - len(lines[hit].lstrip(" "))
    stop = hit + 1
    last = hit
    while stop < end:
        line = lines[stop]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        if line.strip():
            last = stop
        stop += 1
    start = hit
    while start > head + 1 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    if start > head + 1 and not lines[start - 1].strip():
        start -= 1
    return "".join(lines[:start] + lines[last + 1:]), "".join(lines[start:last + 1])


def restore_config(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> None:
    """Remove the autocode block the migration added, and the ledger."""
    ledger = ws / mig.LEDGER_REL
    if not ledger.is_file():
        return  # the migration added nothing to the config
    data = yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}
    entry = data.get("config") if isinstance(data, dict) else None
    inserted = str(entry.get("inserted_block") or "") if isinstance(entry, dict) else ""
    path = ws / mig.CONFIG_REL
    if inserted and path.is_file():
        text = path.read_text(encoding="utf-8")
        before = yaml.safe_load(text)
        prefs = before.get("preferences") if isinstance(before, dict) else None
        if isinstance(prefs, dict) and mig.BLOCK_KEY in prefs:
            removed = None
            if text.count(inserted) == 1:
                new_text = text.replace(inserted, "")
            else:
                cut = cut_block(text, mig.BLOCK_KEY)
                if cut is None:
                    raise ValueError(f"{mig.CONFIG_REL}: cannot find the `{mig.BLOCK_KEY}` block to remove")
                new_text, removed = cut
            after = yaml.safe_load(new_text)
            expected = {**before, "preferences": {k: v for k, v in prefs.items() if k != mig.BLOCK_KEY}}
            if after != expected:
                raise ValueError(f"{mig.CONFIG_REL}: removing `preferences.{mig.BLOCK_KEY}` would change "
                                 "another key; left alone")
            say(f"remove `preferences.{mig.BLOCK_KEY}` from {mig.CONFIG_REL}")
            if removed is not None:
                say(f"the block was edited since the migration; its lines are kept in "
                    f"{ORIGINALS_REL}/config-autocode.yaml")
            if not dry_run:
                if removed is not None:
                    keep = ws / ORIGINALS_REL / "config-autocode.yaml"
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    keep.write_text(removed, encoding="utf-8")
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(new_text, encoding="utf-8")
                tmp.replace(path)
    if not dry_run:
        ledger.unlink()


def remove_registry(ws: Path, mig: ModuleType, *, dry_run: bool, say) -> None:
    """Delete an empty registry; park one that names an agent."""
    path = ws / mig.REGISTRY_REL
    if not path.is_file():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data.get("agents") if isinstance(data, dict) else None
    named = [r for r in rows or [] if isinstance(r, dict) and r.get("name")]
    if named:
        say(f"move {mig.REGISTRY_REL} ({len(named)} agent row(s)) to {ORIGINALS_REL}/")
        if not dry_run:
            keep = ws / ORIGINALS_REL / path.name
            keep.parent.mkdir(parents=True, exist_ok=True)
            path.replace(keep)
    else:
        say(f"remove {mig.REGISTRY_REL} (it names no agent)")
        if not dry_run:
            path.unlink()


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
        restore_config(ws, mig, dry_run=dry_run, say=say)
        remove_registry(ws, mig, dry_run=dry_run, say=say)
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
    parser = argparse.ArgumentParser(prog="revert-0.25.0",
                                     description="Revert the 0.25.0 workspace migration.")
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
