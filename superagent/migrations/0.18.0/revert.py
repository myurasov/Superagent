#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Revert the 0.18.0 migration (back to 0.17.2).

Restores every file captured under
``<workspace>/_memory/_checkpoints/0.18.0/`` to its original relative path
(byte-for-byte), removes the files the migration *created* (currently only
``Outbox/README.md``), and sets ``.version`` back to 0.17.2.
``_memory/world.yaml`` is derived data and is left as-is. The checkpoint
folder is removed after a successful (non-dry-run) restore so a later
re-migration captures fresh originals.

Seed record. ``migrate.py`` records every file it creates from scratch in
``<checkpoint dir>/_seeded.txt`` (one workspace-relative POSIX path per
line; ``#`` comments and blank lines ignored). A path is unlinked here only
when BOTH hold: it is listed in that record AND its bytes still equal the
framework template it was seeded from. Byte-identity alone is not enough --
``workspace_init.py`` seeds the same README from the same template, so an
init-scaffolded README that pre-dated the migration is indistinguishable by
content. With no record (or a path not listed) the file is left in place
and the omission is reported; a redundant file beats a deleted one.

Usage::

    uv run python superagent/migrations/0.18.0/revert.py --workspace <path> [--dry-run]

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

# Name of the seed record inside the checkpoint folder. migrate.py may export
# the same constant; when it does, its value wins (see `seeded_marker_path`).
SEEDED_MARKER_NAME = "_seeded.txt"

# Files the migration can create from scratch -> the framework template each
# is seeded from (relative to `superagent/`). Only these are ever unlinked.
SEEDED_TEMPLATES: dict[str, Path] = {
    "Outbox/README.md": Path("templates") / "folder-readmes" / "Outbox.md",
}


def _load_migrate() -> ModuleType:
    """Import the sibling migrate.py (the folder name is not a valid package name)."""
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_18_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def seeded_marker_path(workspace: Path, mig: ModuleType | None = None) -> Path:
    """Absolute path of the seed record for `workspace` (inside the checkpoint folder)."""
    mig = mig or _load_migrate()
    name = getattr(mig, "SEEDED_MARKER_NAME", SEEDED_MARKER_NAME)
    return Path(workspace) / mig.CHECKPOINT_REL / name


def read_seeded(marker: Path) -> list[str]:
    """Workspace-relative POSIX paths listed in the seed record (empty when absent).

    Blank lines and ``#`` comments are ignored; absolute paths and paths that
    climb out of the workspace (``..`` components) are dropped defensively.
    """
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
        posix = rel.as_posix()
        if posix not in seen:
            seen.append(posix)
    return seen


def run_revert(workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
               out=print) -> int:
    """Restore checkpointed files, unseed recorded files, reset .version. Returns exit code."""
    mig = _load_migrate()
    ws = Path(workspace)
    if not ws.is_dir():
        out(f"error: workspace not found at {ws}")
        return 1
    framework = Path(framework) if framework else mig.default_framework_root()
    prefix = "[dry-run] " if dry_run else ""
    ckpt = ws / mig.CHECKPOINT_REL
    marker = seeded_marker_path(ws, mig)
    seeded = read_seeded(marker)  # read BEFORE the checkpoint folder is removed
    restored = 0
    if ckpt.is_dir():
        for src in sorted(p for p in ckpt.rglob("*") if p.is_file()):
            if src == marker:
                continue  # bookkeeping, not a captured original
            rel = src.relative_to(ckpt)
            dest = ws / rel
            if dest.exists() and dest.read_bytes() == src.read_bytes():
                continue
            out(f"{prefix}restore {rel.as_posix()}")
            restored += 1
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        if not dry_run:
            shutil.rmtree(ckpt)
            parent = ckpt.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    else:
        out(f"{prefix}no checkpoint folder at {mig.CHECKPOINT_REL.as_posix()}; nothing to restore")
    for rel, template_rel in SEEDED_TEMPLATES.items():
        target = ws / rel
        if not target.exists():
            continue
        if rel not in seeded:
            out(f"{prefix}keep {rel} (not recorded as seeded by this migration)")
            continue
        template = framework / template_rel
        if not (template.exists() and target.read_bytes() == template.read_bytes()):
            out(f"{prefix}keep {rel} (seeded, but no longer byte-identical to the template)")
            continue
        out(f"{prefix}remove {rel} (seeded by the migration; byte-identical to the template)")
        if not dry_run:
            target.unlink()
    for rel in seeded:
        if rel not in SEEDED_TEMPLATES:
            out(f"{prefix}keep {rel} (recorded as seeded, but no template is known for it)")
    from superagent.tools.version import set_workspace_version, workspace_version
    current = workspace_version(ws)
    if current != mig.FROM_VERSION:
        out(f"{prefix}.version: {current} -> {mig.FROM_VERSION}")
        if not dry_run:
            set_workspace_version(ws, mig.FROM_VERSION)
    out(f"{prefix}{restored} file(s) restored; world.yaml left as-is (derived)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revert-0.18.0",
                                     description="Revert the 0.18.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_revert(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
