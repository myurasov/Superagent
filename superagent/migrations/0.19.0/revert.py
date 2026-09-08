#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.19.0 migration (back to 0.18.1).

In this order:

1. Restore every file captured under ``<workspace>/_memory/_checkpoints/0.19.0/``
   -- or, after a successful ``validate.py`` parked it there,
   ``_memory/_retired/0.19.0-originals/`` -- to its original relative path,
   byte-for-byte: the pre-move bytes of every relocated ref (recreating a
   source folder the migration removed), the rewritten ``sources.md``
   catalogues, ``_memory/sources-index.yaml``, ``_memory/config.yaml`` (the
   ``preferences.watchlist`` block goes away with it), the pre-rebuild
   ``_memory/world.yaml``, and a pre-existing ``watchlist-state.yaml`` if
   there was one.
2. Remove the registry copies of every relocated ref and every ref the fold
   generated (both listed in ``_memory/_retired/0.19.0-moves.yaml``). A ref
   the ledger does not know about (user-authored after the migration) is
   left alone.
3. Move ``_memory/_retired/data-sources.yaml`` back to ``_memory/``.
4. Delete ``_memory/watchlist-state.yaml`` when the migration seeded it (it
   is machine-owned; a pre-existing file was restored in step 1).
5. Remove ``Sources/Watchlist/README.md`` when the migration seeded it and it
   is still byte-identical to what was seeded; remove the registry folder
   when it is then empty. User files in it are kept.
6. Remove the ledger and the originals / checkpoint folder; set ``.version``
   to 0.18.1.

Edits made to the restored files AFTER the migration are lost by the byte
restore -- re-apply them by hand if they matter.

Usage::

    uv run python superagent/migrations/0.19.0/revert.py --workspace <path> [--dry-run]

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
    spec = importlib.util.spec_from_file_location("migration_0_19_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_seeded(marker: Path) -> list[str]:
    """Workspace-relative POSIX paths listed in the seed record (empty when absent)."""
    if not marker.is_file():
        return []
    seen: list[str] = []
    for raw in marker.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rel = Path(line)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        posix = rel.as_posix() + ("/" if line.endswith("/") else "")
        if posix not in seen:
            seen.append(posix)
    return seen


def _unlink(path: Path, rel: str, *, dry_run: bool, out, why: str) -> int:
    if not path.exists():
        return 0
    out(f"{'[dry-run] ' if dry_run else ''}remove {rel} ({why})")
    if not dry_run:
        path.unlink()
    return 1


def run_revert(workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
               out=print) -> int:
    mig = _load_migrate()
    ws = Path(workspace)
    if not ws.is_dir():
        out(f"error: workspace not found at {ws}")
        return 1
    framework = Path(framework) if framework else mig.default_framework_root()
    prefix = "[dry-run] " if dry_run else ""
    # Originals live in the checkpoint folder while the migration is incomplete
    # and under _retired/ once validate.py has accepted it; read whichever exist.
    stores = [p for p in (ws / mig.CHECKPOINT_REL, ws / mig.ORIGINALS_REL) if p.is_dir()]
    markers = [store / mig.SEEDED_MARKER_NAME for store in stores]
    seeded: list[str] = []
    for m in markers:
        seeded.extend(rel for rel in read_seeded(m) if rel not in seeded)
    ledger_path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    ledger: dict = {}
    if ledger_path.exists():
        try:
            ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            out(f"error: {ledger_path.relative_to(ws).as_posix()} does not parse: {exc}")
            return 1
    ledger = ledger if isinstance(ledger, dict) else {}

    # 1. byte restore (checkpoint folder first, then parked originals)
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

    # 2. registry copies of moved refs + generated (folded) refs
    removed = 0
    for m in ledger.get("moved_refs") or []:
        if not isinstance(m, dict) or not m.get("to"):
            continue
        to_rel, from_rel = str(m["to"]), str(m.get("from") or "")
        dest, orig = ws / to_rel, ws / from_rel
        if not dest.exists():
            continue
        if from_rel and not orig.exists():
            # No checkpointed original came back: move the file itself back.
            out(f"{prefix}move {to_rel} -> {from_rel} (no checkpoint copy)")
            if not dry_run:
                orig.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(orig))
            removed += 1
            continue
        removed += _unlink(dest, to_rel, dry_run=dry_run, out=out, why="relocated ref; original restored")
    for f in ledger.get("folded") or []:
        if isinstance(f, dict) and f.get("ref"):
            removed += _unlink(ws / str(f["ref"]), str(f["ref"]), dry_run=dry_run, out=out,
                               why="generated by the fold")

    # 3. data-sources.yaml back
    live = ws / "_memory" / mig.DATA_SOURCES_NAME
    retired_rows = [r for r in (ledger.get("retired") or []) if isinstance(r, dict) and r.get("to")]
    retired_paths = [ws / str(r["to"]) for r in retired_rows] or [ws / mig.RETIRED_REL / mig.DATA_SOURCES_NAME]
    for retired in retired_paths:
        if retired.exists() and not live.exists():
            out(f"{prefix}move {retired.relative_to(ws).as_posix()} -> _memory/{mig.DATA_SOURCES_NAME}")
            if not dry_run:
                shutil.move(str(retired), str(live))
        elif retired.exists():
            out(f"{prefix}keep {retired.relative_to(ws).as_posix()} (_memory/{mig.DATA_SOURCES_NAME} already exists)")

    # 4. state file
    state_rel = f"_memory/{mig.STATE_NAME}"
    if state_rel in seeded:
        _unlink(ws / state_rel, state_rel, dry_run=dry_run, out=out, why="seeded by the migration")
        # The watchlist tool's fslock sidecar has no life without the state file.
        lock_rel = f"_memory/.{mig.STATE_NAME.rsplit('.', 1)[0]}.lock"
        _unlink(ws / lock_rel, lock_rel, dry_run=dry_run, out=out, why="state lock; state removed")
    elif (ws / state_rel).exists():
        out(f"{prefix}keep {state_rel} (not recorded as seeded; restored from checkpoint if captured)")

    # 5. registry README + folder
    registry = ws / mig.watchlist_rel_path(_config(ws, mig))
    readme = registry / "README.md"
    readme_rel = readme.relative_to(ws).as_posix() if readme.exists() else None
    if readme_rel and readme_rel in seeded:
        template = framework / mig.README_TEMPLATE_REL
        expected = [mig.WATCHLIST_README_DEFAULT.encode("utf-8")]
        if template.exists():
            expected.append(template.read_bytes())
        if readme.read_bytes() in expected:
            _unlink(readme, readme_rel, dry_run=dry_run, out=out, why="seeded; unchanged")
        else:
            out(f"{prefix}keep {readme_rel} (seeded, but edited since)")
    if registry.is_dir():
        leftovers = [p for p in registry.iterdir() if not (dry_run and p.name == "README.md"
                                                            and readme_rel in seeded)]
        if not leftovers:
            out(f"{prefix}remove {registry.relative_to(ws).as_posix()}/ (empty)")
            if not dry_run:
                registry.rmdir()
        else:
            out(f"{prefix}keep {registry.relative_to(ws).as_posix()}/ "
                f"({len(leftovers)} user file(s) remain)")

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
    out(f"{prefix}{restored} file(s) restored, {removed} registry file(s) removed"
        f"{'; world.yaml restored to its pre-migration bytes' if '_memory/world.yaml' in seen_rel else ''}")
    return 0


def _config(ws: Path, mig: ModuleType) -> dict:
    path = ws / "_memory" / "config.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.19.0",
                                     description="Revert the 0.19.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_revert(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
