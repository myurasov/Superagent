#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.27.0 workspace migration (back to 0.26.0).

Stop every agent that runs as a session of its own FIRST (the `agents`
skill): the 0.26.0 tools know nothing of Autocode mode. Then, in this order:

1. Remove from ``_memory/config.yaml`` the keys the migration added (the
   ledger ``_memory/_retired/0.27.0-ledger.yaml`` says which): the line of
   each key, whatever its value is now. A line that was edited since (the
   switch turned on, work hours set) is saved to
   ``_memory/_retired/0.27.0-originals/config-keys.yaml``.
2. Remove the ledger and write 0.26.0 to ``.version``.

Left in place, on purpose: rows and grants in ``_memory/agents.yaml`` (the
0.26.0 tools read the rows and ignore the extra grant fields), and any role
file under ``workspace/_custom/agents/``.

Usage::

    uv run python superagent/migrations/0.27.0/revert.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

ORIGINALS_REL = "_memory/_retired/0.27.0-originals"


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_27_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def restore_config(ws: Path, mig: ModuleType, inserted: dict, *, dry_run: bool, say) -> None:
    """Remove the key lines the migration added, saving the ones edited since."""
    path = ws / mig.CONFIG_REL
    if not path.is_file() or not inserted:
        return
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    prefs = before.get("preferences") if isinstance(before, dict) else None
    if not isinstance(prefs, dict):
        return
    expected = dict(prefs)
    lines = text.splitlines(keepends=True)
    edited: list[str] = []
    for block, added in inserted.items():
        current = prefs.get(block)
        keys = [key for key, _value, _line in mig.KEYS.get(block, []) if f"{key}:" in str(added)]
        if not isinstance(current, dict) or not keys:
            continue
        span = mig.block_span(lines, block)
        if span is None:
            continue
        hit, end, _indent = span
        original = set(str(added).splitlines(keepends=True))
        drop = [j for j in range(hit + 1, end)
                if any(re.match(rf"^ +{re.escape(key)}\s*:", lines[j]) for key in keys)]
        edited += [lines[j] for j in drop if lines[j] not in original]
        lines = [line for j, line in enumerate(lines) if j not in drop]
        expected[block] = {k: v for k, v in current.items() if k not in keys}
        say(f"remove {', '.join(keys)} from `preferences.{block}` in {mig.CONFIG_REL}")
    new_text = "".join(lines)
    if new_text == text:
        return
    if yaml.safe_load(new_text) != {**before, "preferences": expected}:
        raise ValueError(f"{mig.CONFIG_REL}: removing the keys would change another key; left alone")
    if edited:
        say(f"{len(edited)} line(s) were edited since the migration; they are kept in "
            f"{ORIGINALS_REL}/config-keys.yaml")
    if dry_run:
        return
    if edited:
        keep = ws / ORIGINALS_REL / "config-keys.yaml"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("".join(edited), encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)


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

    ledger_path = ws / mig.LEDGER_REL
    inserted: dict = {}
    if ledger_path.is_file():
        loaded = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
        inserted = (loaded.get("inserted") or {}) if isinstance(loaded, dict) else {}
    try:
        restore_config(ws, mig, inserted, dry_run=dry_run, say=say)
    except Exception as exc:  # every failure halts the revert with the file named
        out(f"error: revert halted: {exc}")
        return 1
    if ledger_path.is_file() and not dry_run:
        ledger_path.unlink()
    from superagent.tools.version import set_workspace_version, workspace_version
    if workspace_version(ws) != mig.FROM_VERSION:
        say(f".version: {workspace_version(ws)} -> {mig.FROM_VERSION}")
        if not dry_run:
            set_workspace_version(ws, mig.FROM_VERSION)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.27.0",
                                     description="Revert the 0.27.0 workspace migration.")
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
