#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Report-only scanner for iCloud sync-conflict artifacts ("name 2.ext").

iCloud Drive resolves concurrent writes by materializing "<name> N.<ext>"
sibling files (and occasionally "<dir> N" folders). This tool finds them and
prints a short report. It NEVER deletes or renames anything: cleanup is a
human-approved action (the agent triages into a report first, then executes
on approval).

Prelude usage (daily-update):
    uv run python -m superagent.tools.icloud_dup_check --if-stale 24h

Silent when clean or throttled; self-throttles via a sentinel in
`~/.superagent/tmp/`. No-ops silently when the repo is not under iCloud Drive
(override with --force). Always exits 0: informational, never blocking.

False positives (legitimate "name N.ext" files) go in the user-maintained
ignore list at `workspace/_memory/icloud-dup-ignore.txt` (repo-relative
paths, one per line, `#` comments allowed).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from superagent.tools.home import tmp_dir

REPO = Path(__file__).resolve().parents[2]
# Dot-directories (.git, .venv, legacy browser profiles, IDE state) are tool
# state, never user data — and browser profiles legitimately contain
# Chromium-generated "name N" files. Skip them wholesale.
SKIP_DIRS = {"node_modules", "__pycache__"}
# "stem 2.ext", "stem 12.ext", "stem 2 .ext" (stray space), "dirname 2"
PAT = re.compile(r"^.+ ([2-9]|[1-9]\d) ?(\.[^.]*)?$")


def parse_window(s: str) -> float:
    m = re.fullmatch(r"(\d+)([hd])", s.strip())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 24h or 7d")
    return int(m.group(1)) * (3600 if m.group(2) == "h" else 86400)


def load_ignored(repo: Path) -> set[str]:
    """Repo-relative paths the user has marked as legitimate."""
    ignore_file = repo / "workspace" / "_memory" / "icloud-dup-ignore.txt"
    ignored: set[str] = set()
    if ignore_file.exists():
        for line in ignore_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ignored.add(line)
    return ignored


def scan(repo: Path, ignored: set[str]) -> list[str]:
    """All paths under `repo` matching the conflict-copy pattern."""
    hits: list[str] = []
    for p in repo.rglob("*"):
        rel = p.relative_to(repo)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if PAT.match(p.name) and str(rel) not in ignored:
            kind = "dir" if p.is_dir() else "file"
            hits.append(f"{kind}  {rel}")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--if-stale", type=parse_window, default=None, metavar="24h",
                    help="no-op if a scan already ran within this window")
    ap.add_argument("--force", action="store_true",
                    help="scan even when the repo is not under iCloud Drive")
    ap.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    ap.add_argument("--limit", type=int, default=20, help="max paths to list (default 20)")
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    on_icloud = "Mobile Documents" in str(repo) or "com~apple~CloudDocs" in str(repo)
    if not on_icloud and not args.force:
        return 0

    sentinel = tmp_dir() / ".last-icloud-dup-check"
    if args.if_stale and sentinel.exists() and time.time() - sentinel.stat().st_mtime < args.if_stale:
        return 0

    hits = scan(repo, load_ignored(repo))
    sentinel.touch()

    if hits:
        print(f"icloud-dup-check: {len(hits)} possible iCloud sync artifact(s) found "
              f"('name N.ext' pattern; may include legitimate names):")
        for h in hits[: args.limit]:
            print("  ", h)
        if len(hits) > args.limit:
            print(f"   ... and {len(hits) - args.limit} more")
        print("   Report only - nothing was changed. Ask Superagent to triage and clean "
              "(report first, delete/rename only on approval).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
