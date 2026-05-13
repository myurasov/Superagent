#!/usr/bin/env -S uv run python
"""Lazy `Outbox/` sub-directory materialization + empty-subdir purge.

Per the lazy-Outbox contract (`contracts/outbox-lifecycle.md` § "Lazy
sub-directory creation"), `Outbox/<subdir>/` is created the first time a
skill writes an artifact at that location — never speculatively at init.
This module is the helper any skill / tool calls before writing:

    from superagent.tools.outbox import ensure
    ensure(workspace, "drafts")              # mkdir Outbox/drafts/ if missing
    ensure(workspace, "drafts", "emails")    # mkdir Outbox/drafts/emails/
    ensure(workspace, "handoff")             # mkdir Outbox/handoff/

The shipped `Outbox/` ships flat — `Outbox/README.md` + nothing else. The
four lifecycle stages (drafts / staging / sent / sealed) are conventions,
not pre-created folders; they appear when the agent first uses each.

CLI:

    uv run python -m superagent.tools.outbox ensure <subdir>[/<sub>...]
    uv run python -m superagent.tools.outbox list
    uv run python -m superagent.tools.outbox purge-empty [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Documented lifecycle stages — used for the README + `list` output. Skills
# are NOT restricted to these names; arbitrary nested paths under `Outbox/`
# are allowed (per the layout flexibility documented in `Outbox/README.md`).
KNOWN_STAGES: tuple[str, ...] = ("drafts", "staging", "sent", "sealed")

# Top-level files that are part of the Outbox itself and must not be
# deleted by `purge-empty`.
RESERVED_TOPLEVEL: frozenset[str] = frozenset({"README.md"})


def workspace_default(framework: Path) -> Path:
    return framework.parent / "workspace"


def _outbox_root(workspace: Path) -> Path:
    return workspace / "Outbox"


def _validate_subpath(parts: tuple[str, ...]) -> Path:
    """Reject paths that try to escape Outbox/ (absolute, `..`, empty)."""
    if not parts:
        raise ValueError("ensure requires at least one path component")
    sub = Path(*parts)
    if sub.is_absolute():
        raise ValueError(
            f"ensure path must be relative (not absolute) to Outbox/ (got {sub!s})"
        )
    for part in sub.parts:
        if part in ("", ".", ".."):
            raise ValueError(
                f"ensure path may not contain '.' or '..' components (got {sub!s})"
            )
    return sub


def ensure(workspace: Path, *parts: str) -> bool:
    """Materialize `Outbox/<parts...>/` if not already present.

    Returns True iff the leaf directory was newly created. Idempotent:
    subsequent calls are near-no-ops. Raises ValueError on path components
    that try to escape `Outbox/`.
    """
    sub = _validate_subpath(parts)
    folder = _outbox_root(workspace) / sub
    if folder.is_dir():
        return False
    folder.mkdir(parents=True, exist_ok=True)
    return True


def list_status(workspace: Path) -> list[dict[str, object]]:
    """Return one row per subdir under `Outbox/`, with file count + leaf flag.

    Walks recursively. Skips the reserved top-level README. Output is
    sorted by relative path for stable display.
    """
    root = _outbox_root(workspace)
    rows: list[dict[str, object]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        rel = path.relative_to(root)
        files = [p for p in path.iterdir() if p.is_file()]
        rows.append({
            "path": str(rel),
            "file_count": len(files),
            "is_known_stage": str(rel) in KNOWN_STAGES,
            "has_subdirs": any(p.is_dir() for p in path.iterdir()),
        })
    return rows


def purge_empty(
    workspace: Path,
    *,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """Bottom-up sweep: delete every empty sub-directory of `Outbox/`.

    Sub-directories with files OR with non-empty sub-sub-directories are
    kept. The `Outbox/` root itself and any reserved top-level files
    (README.md) are never touched. Returns (deleted_paths, kept_paths)
    where each path is workspace-relative ("Outbox/<rel>/"). Caller is
    expected to refresh any indexes that referenced the deleted paths.
    """
    root = _outbox_root(workspace)
    deleted: list[str] = []
    kept: list[str] = []
    if not root.is_dir():
        return ([], [])
    # Bottom-up: deepest paths first so empty parents become deletable
    # after their empty children get removed in the same pass.
    all_dirs = [p for p in root.rglob("*") if p.is_dir()]
    all_dirs.sort(key=lambda p: -len(p.parts))
    for path in all_dirs:
        try:
            children = list(path.iterdir())
        except OSError:
            kept.append(f"Outbox/{path.relative_to(root)}/")
            continue
        if children:
            kept.append(f"Outbox/{path.relative_to(root)}/")
            continue
        if not dry_run:
            try:
                path.rmdir()
            except OSError:
                kept.append(f"Outbox/{path.relative_to(root)}/")
                continue
        deleted.append(f"Outbox/{path.relative_to(root)}/")
    return deleted, kept


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="outbox")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser(
        "ensure",
        help="Materialize an Outbox/ sub-directory if missing.",
    )
    e.add_argument(
        "subpath",
        help="Path under Outbox/ to ensure (e.g. 'drafts' or 'drafts/emails').",
    )
    sub.add_parser("list", help="Show every materialized sub-directory + file count.")
    pe = sub.add_parser(
        "purge-empty",
        help="Bottom-up delete empty Outbox/ sub-directories.",
    )
    pe.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or workspace_default(framework)
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if not _outbox_root(workspace).is_dir():
        # Outbox/ should always exist after init; create defensively but warn.
        _outbox_root(workspace).mkdir(parents=True, exist_ok=True)

    if args.cmd == "ensure":
        try:
            parts = tuple(p for p in args.subpath.split("/") if p)
            created = ensure(workspace, *parts)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        verb = "created" if created else "kept"
        print(f"{verb} Outbox/{args.subpath}/")
        return 0

    if args.cmd == "list":
        rows = list_status(workspace)
        if not rows:
            print("Outbox/ is empty (no sub-directories materialized yet).")
            return 0
        for r in rows:
            tag = "  [stage]" if r["is_known_stage"] else "         "
            print(f"  Outbox/{r['path']:30s} {tag}  files: {r['file_count']:3d}")
        return 0

    if args.cmd == "purge-empty":
        deleted, kept = purge_empty(workspace, dry_run=args.dry_run)
        verb = "would delete" if args.dry_run else "deleted"
        for p in deleted:
            print(f"  {verb}: {p}")
        for p in kept:
            print(f"  kept (non-empty): {p}")
        if not deleted and not kept:
            print("  Outbox/ has no sub-directories.")
        if args.dry_run and deleted:
            print(f"\nDry run — re-run without --dry-run to delete {len(deleted)} folder(s).")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
