#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Collision-safe id minting for dated `<kind>-<date>-NNN` ids.

Skills that append rows to `_memory/` files mint ids like
`ilog-2026-08-31-003` (interaction log), `task-20260831-001` (todo — note
the compact date), `sig-2026-08-31-002`, `evt-…`, `dec-…`. Minting by eye
("last id + 1") collides when two rows land in the same session or a prior
append was retried. This helper re-scans the target file AT CALL TIME and
returns max(existing)+1 — mint-time re-check semantics.

Date style is per kind: `task` uses `YYYYMMDD`; every other kind uses
`YYYY-MM-DD` (per `contracts/task-management.md` and the `_memory/`
templates). Both input date forms are accepted and normalized.

CLI:
    uv run python -m superagent.tools.next_id --kind ilog \
        --file workspace/_memory/interaction-log.yaml [--date 2026-08-31]

Python:
    from superagent.tools.next_id import next_id
    next_id("ilog", "2026-08-31", Path("…/interaction-log.yaml"))
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

#: Kinds whose ids carry a compact (YYYYMMDD) date. Everything else is dashed.
COMPACT_DATE_KINDS = frozenset({"task"})

#: Default id shape: `<kind>-<date>-NNN`.
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


def next_id(
    kind: str,
    date_str: str,
    file_path: Path,
    prefix_template: str = DEFAULT_TEMPLATE,
) -> str:
    """Return the next free zero-padded id for `kind` + `date_str`.

    Re-reads `file_path` on every call (mint-time re-check): scans for every
    existing `<prefix>NNN` occurrence and returns `<prefix>{max+1:03d}`.
    Duplicated ids in the file are harmless — max()+1 skips past them.
    A missing or empty file yields `…-001`.
    """
    if not kind or not kind.strip():
        raise ValueError("kind required")
    kind = kind.strip().lower()
    prefix = prefix_template.format(kind=kind, date=_normalize_date(kind, date_str))
    text = ""
    if file_path.exists():
        text = file_path.read_text(encoding="utf-8")
    # (?<![A-Za-z0-9]) keeps `sig-…` from matching inside `psig-…`.
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(prefix) + r"(\d{3,})")
    nums = [int(m.group(1)) for m in pattern.finditer(text)]
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="next_id",
        description="Print the next free <kind>-<date>-NNN id for a file.",
    )
    parser.add_argument("--kind", required=True,
                        help="Id kind (ilog, task, sig, evt, dec, …).")
    parser.add_argument("--file", required=True, type=Path,
                        help="File to scan for existing ids.")
    parser.add_argument("--date", default=None,
                        help="Date (YYYY-MM-DD or YYYYMMDD; default: today).")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE,
                        help=f"Prefix template (default: {DEFAULT_TEMPLATE!r}).")
    args = parser.parse_args(argv)

    date_str = args.date or dt.date.today().isoformat()
    try:
        print(next_id(args.kind, date_str, args.file, args.template))
    except ValueError as exc:
        print(f"next_id: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
