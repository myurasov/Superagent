#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.21.0 migration (back to 0.20.1).

In this order:

1. Move every file parked under ``<workspace>/_memory/_checkpoints/0.21.0/``
   -- or, after a successful ``validate.py`` relocated it,
   ``_memory/_retired/0.21.0-originals/`` -- back to its original relative
   path, byte-for-byte (rendered workbooks, their ``.xlsx.meta.yaml``
   sidecars, the inbox-triage decision log). A live file that has since
   appeared at that path with DIFFERENT bytes is never overwritten: it is
   reported and the parked original stays in the store.
2. Remove the ledger ``_memory/_retired/0.21.0-moves.yaml`` and the store
   folders (only when every parked file was restored).
3. Set ``.version`` to 0.20.1.

Usage::

    uv run python superagent/migrations/0.21.0/revert.py --workspace <path> [--dry-run]

Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
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
    spec = importlib.util.spec_from_file_location("migration_0_21_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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
    ledger_path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    if ledger_path.exists():
        try:
            yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            out(f"error: {ledger_path.relative_to(ws).as_posix()} does not parse: {exc}")
            return 1

    # 1. move back
    restored = 0
    left: list[str] = []
    seen_rel: set[str] = set()
    for store in stores:
        for src in sorted(p for p in store.rglob("*") if p.is_file()):
            rel = src.relative_to(store)
            if rel.as_posix() in seen_rel:
                continue  # the checkpoint copy (earliest original) already won
            seen_rel.add(rel.as_posix())
            dest = ws / rel
            if dest.exists():
                if dest.read_bytes() == src.read_bytes():
                    continue  # already back
                left.append(rel.as_posix())
                out(f"{prefix}keep {rel.as_posix()} in {store.relative_to(ws).as_posix()}/ (a "
                    "different file now exists at the live path; not overwritten)")
                continue
            out(f"{prefix}restore {rel.as_posix()}")
            restored += 1
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
    if not stores:
        out(f"{prefix}no originals at {mig.CHECKPOINT_REL.as_posix()}/ or "
            f"{mig.ORIGINALS_REL.as_posix()}/; nothing to restore")

    # 2. bookkeeping
    if ledger_path.exists():
        out(f"{prefix}remove {ledger_path.relative_to(ws).as_posix()} (ledger)")
        if not dry_run:
            ledger_path.unlink()
            # A workspace that had nothing to move gained an empty `_retired/`
            # from the ledger alone; leave no trace of the round trip.
            retired = ledger_path.parent
            if retired.is_dir() and retired.name == "_retired" and not any(retired.iterdir()):
                retired.rmdir()
    if not dry_run:
        for store in stores:
            remaining = [p for p in store.rglob("*") if p.is_file()]
            if remaining:
                continue  # a kept original stays parked; leave the store
            shutil.rmtree(store)
            parent = store.parent
            if parent.is_dir() and parent.name == "_checkpoints" and not any(parent.iterdir()):
                parent.rmdir()

    # 3. version
    from superagent.tools.version import set_workspace_version, workspace_version
    current = workspace_version(ws)
    if current != mig.FROM_VERSION:
        out(f"{prefix}.version: {current} -> {mig.FROM_VERSION}")
        if not dry_run:
            set_workspace_version(ws, mig.FROM_VERSION)
    tail = f"; {len(left)} left parked" if left else ""
    out(f"{prefix}{restored} file(s) restored{tail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.21.0",
                                     description="Revert the 0.21.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_revert(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
