#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate the 0.24.0 migration. Exit 0 iff every check passes.

Checks:

- the three per-machine folders exist under ``_memory/``;
- when the workspace has a single ``interaction-log.yaml`` or
  ``user-queries.jsonl``, the freeze marker
  ``_memory/interaction-log/_frozen.yaml`` parses and its counts do not exceed
  what the files hold now (rows may only have been added since, never lost);
- ``context.yaml`` parses and carries no ``refresh_suggested`` key;
- the merged interaction log reads without an unreadable file;
- ``.version`` reads 0.24.0;
- re-running ``migrate.py --dry-run`` reports "nothing to do" (idempotency).

Usage::

    uv run python superagent/migrations/0.24.0/validate.py --workspace <path>

Exit codes: 0 all checks pass, 1 a check failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

Check = tuple[bool, str]


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_24_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_checks(workspace: Path, framework: Path | None = None) -> list[Check]:
    """Every check as (passed, message)."""
    mig = _load_migrate()
    ws = Path(workspace)
    results: list[Check] = []
    for rel in mig.FOLDERS:
        results.append(((ws / rel).is_dir(), f"{rel}/ exists"))

    ilog, queries, marker = ws / mig.ILOG_REL, ws / mig.QUERIES_REL, ws / mig.MARKER_REL
    if ilog.is_file() or queries.is_file():
        try:
            data = yaml.safe_load(marker.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            data = None
            results.append((False, f"{mig.MARKER_REL} cannot be read: {exc}"))
        if isinstance(data, dict):
            block = data.get("interaction_log")
            if ilog.is_file():
                rows, _last = mig.count_log_rows(ilog)
                ok = isinstance(block, dict) and isinstance(block.get("rows"), int) and block["rows"] <= rows
                results.append((ok, f"{mig.ILOG_REL} frozen at {block.get('rows') if isinstance(block, dict) else '?'} "
                                    f"row(s); it holds {rows} now"))
            block = data.get("user_queries")
            if queries.is_file():
                with queries.open("rb") as fh:
                    lines = sum(1 for _ in fh)
                ok = isinstance(block, dict) and isinstance(block.get("lines"), int) and block["lines"] <= lines
                results.append((ok, f"{mig.QUERIES_REL} frozen at "
                                    f"{block.get('lines') if isinstance(block, dict) else '?'} line(s); "
                                    f"it holds {lines} now"))
    else:
        results.append((True, "no single log file to freeze"))

    context = ws / mig.CONTEXT_REL
    if context.is_file():
        try:
            ctx = yaml.safe_load(context.read_text(encoding="utf-8"))
            results.append((isinstance(ctx, dict) and mig.RETIRED_KEY not in ctx,
                            f"{mig.CONTEXT_REL} carries no `{mig.RETIRED_KEY}` key"))
        except yaml.YAMLError as exc:
            results.append((False, f"{mig.CONTEXT_REL} does not parse: {exc}"))

    from superagent.tools import machines
    errors: list[str] = []
    rows = machines.interaction_log_rows(ws, errors=errors)
    results.append((not errors, f"merged interaction log reads ({len(rows)} rows)"
                                + (f"; unreadable: {', '.join(errors)}" if errors else "")))

    from superagent.tools.version import workspace_version
    current = workspace_version(ws)
    results.append((current == mig.TO_VERSION, f".version reads {current}"))

    lines: list[str] = []
    rc = mig.run_migration(ws, framework=framework, dry_run=True, out=lines.append)
    results.append((rc == 0 and any("nothing to do" in line for line in lines),
                    "re-running migrate.py --dry-run reports nothing to do"))
    return results


def run_validate(workspace: Path, framework: Path | None = None, out=print) -> int:
    """Print every check; return 0 iff all pass."""
    results = run_checks(workspace, framework)
    for ok, message in results:
        out(f"  {'OK  ' if ok else 'FAIL'}  {message}")
    failed = sum(1 for ok, _ in results if not ok)
    out(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-0.24.0",
                                     description="Validate the 0.24.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace, args.framework)


if __name__ == "__main__":
    sys.exit(main())
