#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Collision-safe id minting for dated `<kind>-<date>-<machine>-NNN` ids.

Skills that append rows to `_memory/` files mint ids like
`task-20260831-mybox-001` (todo: note the compact date),
`sig-2026-08-31-mybox-002`, `dec-…`, `uw-…`. Two things make a fresh id safe:

- **Mint-time re-check.** Minting by eye ("last id + 1") collides when two
  rows land in the same session or a prior append was retried. This helper
  re-scans the target file AT CALL TIME and returns max(existing)+1.
- **Machine tag.** The workspace may be open on two machines that have not
  synced yet (`rules/concurrent-sessions.md`); both would mint the same
  "highest plus one". Every new id therefore carries this machine's tag
  (`tools/machines.py`), and the counter runs per machine.

Ids minted before the tag existed (`ilog-2026-08-31-003`) stay valid forever;
nothing renames them. `--no-machine` (Python: `machine=""`) still mints that
untagged shape for a file that must keep it.

Date style is per kind: `task` uses `YYYYMMDD`; every other kind uses
`YYYY-MM-DD` (per `contracts/task-management.md` and the `_memory/`
templates). Both input date forms are accepted and normalized.

Interaction-log ids are minted by `signals log`, which also appends the row;
no skill mints an `ilog-` id by hand.

CLI:
    uv run python -m superagent.tools.next_id --kind task \
        --file workspace/_memory/todo.yaml [--date 2026-08-31]

Python:
    from superagent.tools.next_id import next_id
    next_id("task", "2026-08-31", Path("…/todo.yaml"))
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from superagent.tools.machines import machine_tag
from superagent.tools.syncnames import is_sync_artifact

#: Kinds whose ids carry a compact (YYYYMMDD) date. Everything else is dashed.
COMPACT_DATE_KINDS = frozenset({"task"})

#: Default id shape: `<kind>-<date>-`, then `<machine>-` and the counter.
DEFAULT_TEMPLATE = "{kind}-{date}-"


def _normalize_date(kind: str, date_str: str) -> str:
    """Return `date_str` in the date style `kind` uses.

    Accepts both `YYYY-MM-DD` and `YYYYMMDD` input; raises ValueError on
    anything that is not a real calendar date.
    """
    raw = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"not a YYYY-MM-DD or YYYYMMDD date: {date_str!r}")
    out_fmt = "%Y%m%d" if kind in COMPACT_DATE_KINDS else "%Y-%m-%d"
    return parsed.strftime(out_fmt)


def _scan_text(file_path: Path | Iterable[Path]) -> str:
    """The text of every file to scan: one file, several, or each file in a folder."""
    paths = [file_path] if isinstance(file_path, Path) else list(file_path)
    chunks: list[str] = []
    for path in paths:
        if path.is_dir():
            files = sorted(p for p in path.iterdir() if p.is_file() and not is_sync_artifact(p))
        else:
            files = [path] if path.exists() else []
        for one in files:
            try:
                chunks.append(one.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(chunks)


def next_id(
    kind: str,
    date_str: str,
    file_path: Path | Iterable[Path],
    prefix_template: str = DEFAULT_TEMPLATE,
    *,
    machine: str | None = None,
) -> str:
    """Return the next free zero-padded id for `kind` + `date_str` on this machine.

    Re-reads `file_path` on every call (mint-time re-check): scans for every
    existing `<prefix><machine>-NNN` occurrence and returns
    `<prefix><machine>-{max+1:03d}`. `file_path` is one file, several files,
    or a folder (every file in it is scanned). Duplicated ids in the file are
    harmless: max()+1 skips past them. A missing or empty file yields `…-001`.

    `machine` is the tag to carry: None (the default) is this machine's
    (`tools/machines.machine_tag`), a name is used as given, and "" mints the
    untagged `<prefix>NNN` shape of ids written before the tag existed.
    """
    if not kind or not kind.strip():
        raise ValueError("kind required")
    kind = kind.strip().lower()
    prefix = prefix_template.format(kind=kind, date=_normalize_date(kind, date_str))
    tag = machine_tag() if machine is None else (machine_tag(machine) if machine else "")
    if tag:
        prefix += f"{tag}-"
    text = _scan_text(file_path)
    # (?<![A-Za-z0-9]) keeps `sig-…` from matching inside `psig-…`.
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(prefix) + r"(\d{3,})")
    nums = [int(m.group(1)) for m in pattern.finditer(text)]
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="next_id",
        description="Print the next free <kind>-<date>-<machine>-NNN id for a file.",
    )
    parser.add_argument("--kind", required=True,
                        help="Id kind (task, sig, dec, uw, st, …).")
    parser.add_argument("--file", required=True, type=Path, action="append",
                        help="File (or folder) to scan for existing ids; repeatable.")
    parser.add_argument("--date", default=None,
                        help="Date (YYYY-MM-DD or YYYYMMDD; default: today).")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE,
                        help=f"Prefix template (default: {DEFAULT_TEMPLATE!r}).")
    tagging = parser.add_mutually_exclusive_group()
    tagging.add_argument("--machine", default=None,
                         help="Machine tag to carry (default: this machine's).")
    tagging.add_argument("--no-machine", action="store_true",
                         help="Mint the untagged <kind>-<date>-NNN shape.")
    args = parser.parse_args(argv)

    date_str = args.date or dt.date.today().isoformat()
    try:
        print(next_id(args.kind, date_str, args.file, args.template,
                      machine="" if args.no_machine else args.machine))
    except ValueError as exc:
        print(f"next_id: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
