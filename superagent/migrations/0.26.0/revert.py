#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.26.0 workspace migration (back to 0.25.0).

In this order:

1. Remove from ``_memory/config.yaml`` what the migration added (the ledger
   ``_memory/_retired/0.26.0-ledger.yaml`` says what): the two update
   cadences and the ``preferences.update`` block. Inserted text that is
   unchanged is removed byte for byte. Text edited since (another time, a
   drop folder) is cut out by its shape, and its lines are saved to
   ``_memory/_retired/0.26.0-originals/``.
2. Remove ``_memory/asks.yaml`` when the migration created it: deleted when
   it holds no ask, moved to ``_memory/_retired/0.26.0-originals/`` when it
   does.
3. Remove the ledger and write 0.25.0 to ``.version``.

Left in place, on purpose: the stamps ``update_light`` / ``update_full`` in
``context.yaml`` and the fields ``last_dispatched`` / ``last_change_note`` in
``watchlist-state.yaml``. The 0.25.0 tools ignore them.

Usage::

    uv run python superagent/migrations/0.26.0/revert.py --workspace <path> [--dry-run]

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

ORIGINALS_REL = "_memory/_retired/0.26.0-originals"


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_26_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def cut_block(text: str, key: str, mig: ModuleType) -> tuple[str, str] | None:
    """Cut `preferences.<key>` out of a config text by its shape.

    Returns (text without the block, the removed lines), or None when the key
    is not a child of the top-level `preferences` mapping. The block is the
    key's line, every deeper-indented line below it, the comment lines
    directly above it, and one blank line above those.
    """
    lines = text.splitlines(keepends=True)
    span = mig.preferences_span(lines)
    if span is None:
        return None
    head, end = span
    children = [j for j in range(head + 1, end)
                if lines[j].strip() and lines[j].startswith(" ") and not lines[j].lstrip().startswith("#")]
    if not children:
        return None
    level = len(lines[children[0]]) - len(lines[children[0]].lstrip(" "))
    hit = next((j for j in children if re.match(rf"^ {{{level}}}{re.escape(key)}\s*:", lines[j])), None)
    if hit is None:
        return None
    last = hit
    for j in range(hit + 1, end):
        line = lines[j]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= level:
            break
        if line.strip():
            last = j
    start = hit
    while start > head + 1 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    if start > head + 1 and not lines[start - 1].strip():
        start -= 1
    return "".join(lines[:start] + lines[last + 1:]), "".join(lines[start:last + 1])


def cut_cadences(text: str, keys: list[str]) -> tuple[str, str]:
    """Remove the lines of the given cadence keys; returns (new text, the removed lines)."""
    kept: list[str] = []
    removed: list[str] = []
    for line in text.splitlines(keepends=True):
        if any(re.match(rf"^ +{re.escape(key)}\s*:", line) for key in keys):
            removed.append(line)
        else:
            kept.append(line)
    return "".join(kept), "".join(removed)


def restore_config(ws: Path, mig: ModuleType, ledger: dict, *, dry_run: bool, say) -> None:
    """Remove the cadence keys and the update block the migration added."""
    path = ws / mig.CONFIG_REL
    cadence_text = str(ledger.get("inserted_cadences") or "")
    block_text = str(ledger.get("inserted_block") or "")
    if not path.is_file() or not (cadence_text or block_text):
        return
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    prefs = before.get("preferences") if isinstance(before, dict) else None
    if not isinstance(prefs, dict):
        return
    expected = dict(prefs)
    new_text = text
    saved: dict[str, str] = {}
    if cadence_text and isinstance(prefs.get("cadences"), dict):
        keys = [k for k in mig.CADENCE_LINES if f"{k}:" in cadence_text and k in prefs["cadences"]]
        if keys:
            if new_text.count(cadence_text) == 1:
                new_text = new_text.replace(cadence_text, "")
            else:
                new_text, removed = cut_cadences(new_text, keys)
                saved["config-cadences.yaml"] = removed
            expected["cadences"] = {k: v for k, v in prefs["cadences"].items() if k not in keys}
            say(f"remove {', '.join(keys)} from `preferences.cadences` in {mig.CONFIG_REL}")
    if block_text and mig.BLOCK_KEY in prefs:
        if new_text.count(block_text) == 1:
            new_text = new_text.replace(block_text, "")
        else:
            cut = cut_block(new_text, mig.BLOCK_KEY, mig)
            if cut is None:
                raise ValueError(f"{mig.CONFIG_REL}: cannot find the `{mig.BLOCK_KEY}` block to remove")
            new_text, removed = cut
            saved["config-update.yaml"] = removed
        expected.pop(mig.BLOCK_KEY, None)
        say(f"remove `preferences.{mig.BLOCK_KEY}` from {mig.CONFIG_REL}")
    if new_text == text:
        return
    if yaml.safe_load(new_text) != {**before, "preferences": expected}:
        raise ValueError(f"{mig.CONFIG_REL}: removing the update keys would change another key; left alone")
    for name in saved:
        say(f"those lines were edited since the migration; they are kept in {ORIGINALS_REL}/{name}")
    if dry_run:
        return
    for name, removed in saved.items():
        keep = ws / ORIGINALS_REL / name
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text(removed, encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)


def remove_asks(ws: Path, mig: ModuleType, ledger: dict, *, dry_run: bool, say) -> None:
    """Delete an empty asks queue the migration created; park one that holds an ask."""
    path = ws / mig.ASKS_REL
    if not ledger.get("asks_created") or not path.is_file():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data.get("asks") if isinstance(data, dict) else None
    real = [r for r in rows or [] if isinstance(r, dict) and r.get("id")]
    if real:
        say(f"move {mig.ASKS_REL} ({len(real)} ask(s)) to {ORIGINALS_REL}/")
        if not dry_run:
            keep = ws / ORIGINALS_REL / path.name
            keep.parent.mkdir(parents=True, exist_ok=True)
            path.replace(keep)
    else:
        say(f"remove {mig.ASKS_REL} (it holds no ask)")
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

    ledger_path = ws / mig.LEDGER_REL
    ledger: dict = {}
    if ledger_path.is_file():
        loaded = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
        ledger = loaded if isinstance(loaded, dict) else {}
    try:
        restore_config(ws, mig, ledger, dry_run=dry_run, say=say)
        remove_asks(ws, mig, ledger, dry_run=dry_run, say=say)
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
    parser = argparse.ArgumentParser(prog="revert-0.26.0",
                                     description="Revert the 0.26.0 workspace migration.")
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
