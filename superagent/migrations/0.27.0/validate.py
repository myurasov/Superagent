#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate the 0.27.0 migration. Exit 0 iff every check passes.

Checks:

- ``_memory/config.yaml`` parses;
- ``preferences.autocode.enabled``, when present, is true or false;
- ``preferences.update.work_hours`` and ``off_days``, when present, are a
  string or null and a list;
- the roster reads without a problem (role files, registry rows, grants);
- ``.version`` reads 0.27.0;
- re-running ``migrate.py --dry-run`` reports "nothing to do" (idempotency).

Usage::

    uv run python superagent/migrations/0.27.0/validate.py --workspace <path>

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
    spec = importlib.util.spec_from_file_location("migration_0_27_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_checks(workspace: Path, framework: Path | None = None) -> list[Check]:
    """Every check as (passed, message)."""
    from superagent.tools import agents
    from superagent.tools.version import workspace_version

    mig = _load_migrate()
    ws = Path(workspace)
    fw = Path(framework) if framework else _REPO_ROOT / "superagent"
    results: list[Check] = []
    data = None
    try:
        data = yaml.safe_load((ws / mig.CONFIG_REL).read_text(encoding="utf-8"))
        results.append((True, f"{mig.CONFIG_REL} parses"))
    except (OSError, yaml.YAMLError) as exc:
        results.append((False, f"{mig.CONFIG_REL} parses ({exc})"))
    prefs = data.get("preferences") if isinstance(data, dict) else None
    prefs = prefs if isinstance(prefs, dict) else {}
    autocode = prefs.get("autocode") if isinstance(prefs.get("autocode"), dict) else {}
    update = prefs.get("update") if isinstance(prefs.get("update"), dict) else {}
    results.append((autocode.get("enabled", False) in (True, False),
                    "`preferences.autocode.enabled` is true or false"))
    results.append((update.get("work_hours") is None or isinstance(update.get("work_hours"), str),
                    "`preferences.update.work_hours` is a string or null"))
    results.append((isinstance(update.get("off_days", []), list),
                    "`preferences.update.off_days` is a list"))
    try:
        problems = agents.roster(ws, fw)["errors"]
    except Exception as exc:  # a prefix or a file that cannot be read
        problems = [str(exc)]
    results.append((not problems, "the roster reads" + (f" ({problems[0]})" if problems else "")))
    version = workspace_version(ws)
    results.append((version == mig.TO_VERSION, f".version reads {mig.TO_VERSION} (is {version})"))
    lines: list[str] = []
    rc = mig.run_migration(ws, framework=fw, dry_run=True, out=lines.append)
    results.append((rc == 0 and any("nothing to do" in line for line in lines),
                    "re-running migrate.py --dry-run reports nothing to do"))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-0.27.0",
                                     description="Validate the 0.27.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = run_checks(args.workspace, args.framework)
    for passed, message in results:
        print(f"[{'ok' if passed else 'FAIL'}] {message}")
    failed = [m for ok, m in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
