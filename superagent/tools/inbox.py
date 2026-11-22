#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Drop folders: the documents that wait to be filed.

A drop folder is where the owner leaves a document for Superagent to file with
the `add-source` skill. There is always one, `<workspace>/Inbox/`, and
`config.preferences.update.drop_folders` may list more (a folder a sync tool
shares between machines, a scanner's output folder). The update cycle drains
them (contracts/update-loop.md § 3), and the zero-cost check of the update
duty asks this tool whether anything waits, so a scheduled run with nothing to
file costs nothing.

  list   the items that wait, one per line (`--json` for whole rows). An item
         is a top-level file or folder of a drop folder. Not listed: hidden
         names, `README.md`, a file the sync tool is still writing, and a
         file changed in the last 30 seconds (it may still be arriving;
         `--all` lists it too, marked). A sync conflict copy is listed apart,
         as something to look at, never as a document to file.

This tool reads only. It never moves, opens or deletes a document.

Exit codes: 0 ok; 2 usage error.

CLI:
    uv run python -m superagent.tools.inbox list [--all] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.syncnames import is_conflict_copy, is_sync_artifact

FRAMEWORK = Path(__file__).resolve().parent.parent
DEFAULT_FOLDER = "Inbox"
#: A file changed more recently than this may still be arriving.
SETTLE_SECONDS = 30
SKIP_NAMES = frozenset({"README.md", "Thumbs.db", "desktop.ini"})

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def drop_folders(workspace: Path) -> list[Path]:
    """`<workspace>/Inbox`, then each folder of `preferences.update.drop_folders`, without repeats.

    A listed path may start with `~`; a relative one is taken from the
    workspace. A folder that does not exist is kept in the list (it may appear
    later) and simply holds nothing.
    """
    folders = [workspace / DEFAULT_FOLDER]
    try:
        cfg = yaml.load((workspace / "_memory" / "config.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)  # a safe loader (C or Python)
    except (OSError, yaml.YAMLError):
        cfg = None
    prefs = cfg.get("preferences") if isinstance(cfg, dict) else None
    block = prefs.get("update") if isinstance(prefs, dict) else None
    listed = block.get("drop_folders") if isinstance(block, dict) else None
    for raw in listed if isinstance(listed, list) else ():
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = Path(raw.strip()).expanduser()
        path = path if path.is_absolute() else workspace / path
        if path not in folders:
            folders.append(path)
    return folders


def scan(workspace: Path, *, now: dt.datetime | None = None,
         settle_seconds: int = SETTLE_SECONDS) -> list[dict[str, Any]]:
    """Every top-level item of every drop folder, with what it is.

    Each row: `path`, `folder`, `name`, `kind` (`file` | `folder` |
    `conflict-copy`), `size` (bytes; None for a folder), `settled` (False for
    a file changed in the last `settle_seconds`).
    """
    stamp = (now or dt.datetime.now().astimezone()).timestamp()
    rows: list[dict[str, Any]] = []
    for folder in drop_folders(workspace):
        if not folder.is_dir():
            continue
        for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            name = item.name
            if name.startswith(".") or name in SKIP_NAMES:
                continue
            conflict = is_conflict_copy(name)
            if is_sync_artifact(name) and not conflict:
                continue  # the sync tool is still writing it
            try:
                st = item.stat()
            except OSError:
                continue
            is_dir = item.is_dir()
            rows.append({
                "path": str(item), "folder": str(folder), "name": name,
                "kind": "conflict-copy" if conflict else ("folder" if is_dir else "file"),
                "size": None if is_dir else st.st_size,
                "settled": is_dir or stamp - st.st_mtime >= settle_seconds,
            })
    return rows


def waiting(workspace: Path, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """The documents ready to file: settled files and folders, no conflict copy."""
    return [r for r in scan(workspace, now=now) if r["settled"] and r["kind"] != "conflict-copy"]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="inbox", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="The documents that wait in the drop folders.")
    ls.add_argument("--workspace", type=Path, default=None,
                    help="Workspace path (default: workspace next to the framework).")
    ls.add_argument("--all", action="store_true",
                    help="Also list files still arriving and conflict copies.")
    ls.add_argument("--json", action="store_true", help="Whole rows, as JSON.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    rows = scan(workspace)
    shown = rows if args.all else [r for r in rows if r["settled"] and r["kind"] != "conflict-copy"]
    if args.json:
        print(json.dumps(shown, indent=2))
        return 0
    for row in shown:
        note = "" if row["kind"] == "file" and row["settled"] else (
            f"  ({row['kind']})" if row["kind"] != "file" else "  (still arriving)")
        print(f"{row['path']}{note}")
    apart = [r for r in rows if r["kind"] == "conflict-copy"]
    if apart and not args.all:
        print(f"inbox: {len(apart)} sync conflict copy(ies) in a drop folder, not listed: "
              "look at them by hand (`--all` names them)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
