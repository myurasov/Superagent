#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate the 0.25.0 migration. Exit 0 iff every check passes.

Checks:

- ``_memory/config.yaml`` parses, and the id prefix it gives
  (``preferences.autocode.id_prefix``, or the default when the block is absent)
  is a valid one;
- ``_memory/agents.yaml`` exists, parses, carries ``schema_version`` and an
  ``agents`` list, and every row in it reads without a problem;
- ``.version`` reads 0.25.0;
- re-running ``migrate.py --dry-run`` reports "nothing to do" (idempotency).

Usage::

    uv run python superagent/migrations/0.25.0/validate.py --workspace <path>

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
    spec = importlib.util.spec_from_file_location("migration_0_25_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_checks(workspace: Path, framework: Path | None = None) -> list[Check]:
    """Every check as (passed, message)."""
    from superagent.tools import agent_roles, agents
    from superagent.tools.version import workspace_version

    mig = _load_migrate()
    ws = Path(workspace)
    results: list[Check] = []

    config = ws / mig.CONFIG_REL
    try:
        yaml.safe_load(config.read_text(encoding="utf-8"))
        results.append((True, f"{mig.CONFIG_REL} parses"))
    except (OSError, yaml.YAMLError) as exc:
        results.append((False, f"{mig.CONFIG_REL} parses ({exc})"))
    try:
        results.append((True, f"the id prefix is valid (`{agent_roles.id_prefix(ws)}`)"))
    except agent_roles.AgentError as exc:
        results.append((False, f"the id prefix is valid ({exc})"))

    registry = ws / mig.REGISTRY_REL
    data = None
    if registry.is_file():
        try:
            data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            data = None
    shaped = (isinstance(data, dict) and "schema_version" in data
              and isinstance(data.get("agents"), list))
    results.append((shaped, f"{mig.REGISTRY_REL} exists with schema_version and an `agents` list"))
    _rows, problems = agents.load_registry(ws)
    results.append((not problems, "every registry row reads"
                    + (f" ({'; '.join(problems)})" if problems else "")))

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
    parser = argparse.ArgumentParser(prog="validate-0.25.0",
                                     description="Validate the 0.25.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace, args.framework)


if __name__ == "__main__":
    sys.exit(main())
