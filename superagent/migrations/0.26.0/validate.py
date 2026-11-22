#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate the 0.26.0 migration. Exit 0 iff every check passes.

Checks:

- ``_memory/config.yaml`` parses;
- the two update cadences resolve to a schedule the tools can read (from the
  workspace's ``preferences.cadences``, or the framework default when the
  workspace has no such block);
- ``preferences.update.drop_folders``, when present, is a list;
- ``_memory/asks.yaml`` exists, parses, carries ``schema_version`` and an
  ``asks`` list, and its rows read without a warning;
- every duty of every role has a stamp key, and the roster reads without a
  problem;
- ``.version`` reads 0.26.0;
- re-running ``migrate.py --dry-run`` reports "nothing to do" (idempotency).

Usage::

    uv run python superagent/migrations/0.26.0/validate.py --workspace <path>

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
    spec = importlib.util.spec_from_file_location("migration_0_26_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_checks(workspace: Path, framework: Path | None = None) -> list[Check]:
    """Every check as (passed, message)."""
    from superagent.tools import agent_roles, cadence, validate
    from superagent.tools.version import workspace_version

    mig = _load_migrate()
    ws = Path(workspace)
    fw = Path(framework) if framework else _REPO_ROOT / "superagent"
    results: list[Check] = []

    config = ws / mig.CONFIG_REL
    data = None
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        results.append((True, f"{mig.CONFIG_REL} parses"))
    except (OSError, yaml.YAMLError) as exc:
        results.append((False, f"{mig.CONFIG_REL} parses ({exc})"))
    values = cadence.cadence_values(ws, fw)
    for key in mig.CADENCE_LINES:
        schedule = cadence.parse_schedule(values.get(key))
        results.append((schedule is not None,
                        f"cadence `{key}` is a schedule ({values.get(key)!r})"))
    prefs = data.get("preferences") if isinstance(data, dict) else None
    block = prefs.get(mig.BLOCK_KEY) if isinstance(prefs, dict) else None
    folders = block.get("drop_folders") if isinstance(block, dict) else []
    results.append((block is None or (isinstance(block, dict) and isinstance(folders, list)),
                    f"`preferences.{mig.BLOCK_KEY}.drop_folders` is a list"))

    asks = ws / mig.ASKS_REL
    loaded = None
    if asks.is_file():
        try:
            loaded = yaml.safe_load(asks.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            loaded = None
    shaped = (isinstance(loaded, dict) and "schema_version" in loaded
              and isinstance(loaded.get("asks"), list))
    results.append((shaped, f"{mig.ASKS_REL} exists with schema_version and an `asks` list"))
    warnings = validate.check_asks(loaded.get("asks")) if shaped else []
    results.append((not warnings, "every ask row reads" + (f" ({warnings[0]})" if warnings else "")))

    roles, problems = agent_roles.collect(fw, ws)
    keys = [agent_roles.duty_stamp_key(d) for r in roles for d in r.duties]
    results.append((not problems and all(keys),
                    "every role reads and every duty has a stamp key"
                    + (f" ({problems[0]})" if problems else "")))

    version = workspace_version(ws)
    results.append((version == mig.TO_VERSION, f".version reads {mig.TO_VERSION} (is {version})"))

    lines: list[str] = []
    rc = mig.run_migration(ws, framework=fw, dry_run=True, out=lines.append)
    idempotent = rc == 0 and any("nothing to do" in line for line in lines)
    results.append((idempotent, "re-running migrate.py --dry-run reports nothing to do"))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-0.26.0",
                                     description="Validate the 0.26.0 workspace migration.")
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
