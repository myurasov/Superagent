#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.20.0 migration (back to 0.19.0).

In this order:

1. Rename every registry ref the migration retitled back to its pre-migration
   name (``_memory/_retired/0.20.0-moves.yaml`` ``renamed``), through a temp
   name so a case-only rename lands on a case-insensitive filesystem.
2. Rename every ``.meta.md`` sidecar the migration produced back to its
   ``.ref.md`` name (ledger ``sidecars``).
3. Restore every file captured under ``<workspace>/_memory/_checkpoints/0.20.0/``
   -- or, after a successful ``validate.py`` parked it there,
   ``_memory/_retired/0.20.0-originals/`` -- to its original relative path,
   byte-for-byte: the schema-1 bytes of every converted ref (including the
   SimpleFIN cadence that was in force before), the rewritten ``sources.md``
   catalogues, ``_memory/sources-index.yaml`` and the pre-rebuild
   ``_memory/world.yaml``.
4. Recreate ``Sources/_cache/`` when the migration removed it (ledger
   ``removed_dirs``).
5. Remove ``_memory/.watchlist-state.lock`` when it did not exist before the
   migration (validate's dry-run check creates it).
6. Remove the ledger and the originals / checkpoint folder; set ``.version``
   to 0.19.0.

Edits made to the restored files AFTER the migration are lost by the byte
restore -- re-apply them by hand if they matter.

Usage::

    uv run python superagent/migrations/0.20.0/revert.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_20_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _listing(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


def _rename_back(ws: Path, mig: ModuleType, to_rel: str, from_rel: str, *, dry_run: bool,
                 out, why: str) -> int:
    """`to_rel` -> `from_rel` by exact on-disk name; 1 when a rename happened."""
    dest, orig = ws / to_rel, ws / from_rel
    names = _listing(dest.parent)
    if dest.name not in names:
        return 0
    if orig.name in names and orig.parent == dest.parent:
        return 0  # already back (or a same-named file exists beside it)
    if orig.parent != dest.parent and orig.exists():
        out(f"{'[dry-run] ' if dry_run else ''}keep {to_rel} ({from_rel} already exists)")
        return 0
    out(f"{'[dry-run] ' if dry_run else ''}rename {to_rel} -> {from_rel} ({why})")
    if not dry_run:
        orig.parent.mkdir(parents=True, exist_ok=True)
        if orig.parent == dest.parent and orig.name.lower() == dest.name.lower():
            mig.rename_two_step(dest, orig)
        else:
            os.rename(dest, orig)
    return 1


def run_revert(workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
               out=print) -> int:
    mig = _load_migrate()
    ws = Path(workspace)
    if not ws.is_dir():
        out(f"error: workspace not found at {ws}")
        return 1
    prefix = "[dry-run] " if dry_run else ""
    # Originals live in the checkpoint folder while the migration is incomplete
    # and under _retired/ once validate.py has accepted it; read whichever exist.
    stores = [p for p in (ws / mig.CHECKPOINT_REL, ws / mig.ORIGINALS_REL) if p.is_dir()]
    markers = [store / mig.SEEDED_MARKER_NAME for store in stores]
    ledger_path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    ledger: dict = {}
    if ledger_path.exists():
        try:
            ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            out(f"error: {ledger_path.relative_to(ws).as_posix()} does not parse: {exc}")
            return 1
    ledger = ledger if isinstance(ledger, dict) else {}

    # 1. + 2. names back (registry Title_Case renames, then sidecars)
    renamed = 0
    for m in ledger.get("renamed") or []:
        if isinstance(m, dict) and m.get("to") and m.get("from"):
            renamed += _rename_back(ws, mig, str(m["to"]), str(m["from"]), dry_run=dry_run,
                                    out=out, why="Title_Case rename undone")
    for m in ledger.get("sidecars") or []:
        if isinstance(m, dict) and m.get("to") and m.get("from"):
            renamed += _rename_back(ws, mig, str(m["to"]), str(m["from"]), dry_run=dry_run,
                                    out=out, why="sidecar back to .ref.md")

    # 3. byte restore (checkpoint folder first, then parked originals)
    restored = 0
    seen_rel: set[str] = set()
    for store in stores:
        for src in sorted(p for p in store.rglob("*") if p.is_file()):
            if src in markers:
                continue
            rel = src.relative_to(store)
            if rel.as_posix() in seen_rel:
                continue  # the checkpoint copy (earliest original) already won
            seen_rel.add(rel.as_posix())
            dest = ws / rel
            if dest.exists() and dest.read_bytes() == src.read_bytes():
                continue
            out(f"{prefix}restore {rel.as_posix()}")
            restored += 1
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    if not stores:
        out(f"{prefix}no originals at {mig.CHECKPOINT_REL.as_posix()}/ or "
            f"{mig.ORIGINALS_REL.as_posix()}/; nothing to restore")

    # 4. folders the migration removed
    for d in ledger.get("removed_dirs") or []:
        rel = str(d.get("path") or "") if isinstance(d, dict) else ""
        if rel and not (ws / rel).exists():
            out(f"{prefix}recreate {rel} (removed by the migration)")
            if not dry_run:
                (ws / rel).mkdir(parents=True, exist_ok=True)

    # 5. state lock created after the migration
    lock = ws / "_memory" / mig.STATE_LOCK_NAME
    if ledger and ledger.get("state_lock_preexisting") is False and lock.exists():
        out(f"{prefix}remove _memory/{mig.STATE_LOCK_NAME} (did not exist before the migration)")
        if not dry_run:
            lock.unlink()

    # 6. bookkeeping + version
    if ledger_path.exists():
        out(f"{prefix}remove {ledger_path.relative_to(ws).as_posix()} (ledger)")
        if not dry_run:
            ledger_path.unlink()
    if not dry_run:
        for store in stores:
            shutil.rmtree(store)
            parent = store.parent
            if parent.is_dir() and parent.name == "_checkpoints" and not any(parent.iterdir()):
                parent.rmdir()
    from superagent.tools.version import set_workspace_version, workspace_version
    current = workspace_version(ws)
    if current != mig.FROM_VERSION:
        out(f"{prefix}.version: {current} -> {mig.FROM_VERSION}")
        if not dry_run:
            set_workspace_version(ws, mig.FROM_VERSION)
    out(f"{prefix}{renamed} file(s) renamed back, {restored} file(s) restored"
        f"{'; world.yaml restored to its pre-migration bytes' if '_memory/world.yaml' in seen_rel else ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.20.0",
                                     description="Revert the 0.20.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_revert(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
