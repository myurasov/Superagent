#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Read-only detector for host-IDE memory-store writes.

`rules/memory-routing.md` forbids routing "remember this" content to any
host-IDE memory store outside this repo — in particular Claude Code's
per-project auto-memory at `~/.claude/projects/<slug>/memory/`. This tool
checks whether such a store has accumulated files for THIS repo, so the
doctor / supertailor passes can flag violations early.

The Claude Code project slug is the absolute repo path with every character
outside [A-Za-z0-9-] (slashes, spaces, dots, tildes, …) replaced by `-`.

Strictly READ-ONLY: it lists offending files (path + mtime); it never
creates, migrates, or deletes anything. Remediation is § 4 of the rule
(migrate into `_memory`/`_custom`, then ask before deleting the original).

CLI:
    uv run python -m superagent.tools.memory_routing_check
    uv run python -m superagent.tools.memory_routing_check --since 2026-08-01T00:00:00
    uv run python -m superagent.tools.memory_routing_check --root /some/dir --json

Exit code:
  0  clean — no files found in any candidate memory root (per --since filter)
  1  offending files found (listed on stdout)
  2  bad arguments (e.g. unparseable --since)

Python:
    from superagent.tools.memory_routing_check import check, candidate_roots
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path


def repo_root() -> Path:
    """Absolute path of this installation's repo root."""
    return Path(__file__).resolve().parents[2]


def claude_project_slug(repo: Path) -> str:
    """Derive the Claude Code project-directory slug for `repo`.

    Claude Code names `~/.claude/projects/<slug>/` after the absolute
    project path with `/`, spaces, dots, and other non-alphanumerics
    replaced by `-` (hyphens survive).
    """
    return re.sub(r"[^A-Za-z0-9-]", "-", str(repo.resolve()))


def candidate_roots(repo: Path, home: Path | None = None) -> list[Path]:
    """Known host-IDE memory locations scoped to `repo`.

    Only patterns tied to THIS repo are listed — a global store may hold
    legitimate state for other projects and is out of scope here.
    """
    home = home or Path.home()
    slug = claude_project_slug(repo)
    project_dir = home / ".claude" / "projects" / slug
    return [
        project_dir / "memory",       # Claude Code auto-memory (MEMORY.md etc.)
        project_dir / "MEMORY.md",    # older flat layout
    ]


def _mtime(path: Path) -> dt.datetime:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def check(
    roots: list[Path],
    since: dt.datetime | None = None,
) -> list[tuple[Path, dt.datetime]]:
    """Return `(path, mtime)` for every file found under `roots`.

    A root may be a directory (scanned recursively) or a single file.
    Missing roots are skipped silently. When `since` is given, only files
    modified strictly after it are reported. Read-only — never writes.
    """
    if since is not None and since.tzinfo is None:
        since = since.astimezone()
    offenders: list[tuple[Path, dt.datetime]] = []
    for root in roots:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = sorted(p for p in root.rglob("*") if p.is_file())
        else:
            continue
        for f in files:
            try:
                mtime = _mtime(f)
            except OSError:
                continue
            if since is not None and mtime <= since:
                continue
            offenders.append((f, mtime))
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory_routing_check",
        description="Detect host-IDE memory-store files for this repo (read-only).",
    )
    parser.add_argument("--repo", type=Path, default=None,
                        help="Repo root to derive the host slug from (default: this repo).")
    parser.add_argument("--root", type=Path, action="append", default=None,
                        help="Scan this root instead of the derived candidates (repeatable).")
    parser.add_argument("--since", default=None,
                        help="ISO timestamp; only report files modified after it.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    since: dt.datetime | None = None
    if args.since:
        try:
            since = dt.datetime.fromisoformat(args.since)
        except ValueError:
            print(f"memory_routing_check: bad --since value: {args.since!r}",
                  file=sys.stderr)
            return 2

    roots = args.root or candidate_roots(args.repo or repo_root())
    offenders = check(roots, since=since)

    if args.json:
        print(json.dumps({
            "roots": [str(r) for r in roots],
            "since": since.isoformat() if since else None,
            "offenders": [
                {"path": str(p), "mtime": m.isoformat(timespec="seconds")}
                for p, m in offenders
            ],
            "ok": not offenders,
        }))
    elif offenders:
        print(f"memory-routing violation: {len(offenders)} file(s) in host memory store(s):")
        for p, m in offenders:
            print(f"  {p}  (mtime {m.isoformat(timespec='seconds')})")
        print("Remediation: rules/memory-routing.md § 4 — migrate into workspace/_memory "
              "or _custom, then ask before deleting the external original.")
    else:
        scanned = ", ".join(str(r) for r in roots)
        print(f"clean: no host-IDE memory files found ({scanned})")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
