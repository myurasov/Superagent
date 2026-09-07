#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Post-migration checks for 0.18.0.

Mirrors the ``## Validate`` bullets of ``superagent/migrations/0.18.0.md``:

- no non-canonical dated entry header remains under ``## Log`` in any
  Domain / Project / Archive ``history.md``;
- ``_memory/model-context.yaml`` holds at most 10 ``sessions`` rows;
- every ``enabled: true`` source in ``_memory/data-sources.yaml`` is present
  in ``config.yaml`` (``data_sources_configured`` + ``preferences.ingestion_schedule``);
- ``Outbox/README.md`` exists;
- ``.version`` reads ``0.18.0``.

Usage::

    uv run python superagent/migrations/0.18.0/validate.py --workspace <path>

Exit codes: 0 all checks pass, 1 at least one check failed, 2 usage error.
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


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_18_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def run_checks(workspace: Path) -> list[tuple[bool, str]]:
    """Return one (passed, message) row per check."""
    mig = _load_migrate()
    ws = Path(workspace)
    results: list[tuple[bool, str]] = []

    bad_files = 0
    for path in mig.history_files(ws):
        offenders = mig.log_section_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            bad_files += 1
            results.append((False, f"{path.relative_to(ws).as_posix()}: "
                                   f"{len(offenders)} non-canonical header(s), "
                                   f"e.g. {offenders[0]!r}"))
    if not bad_files:
        results.append((True, "history.md: all Log entry headers canonical"))

    mc = ws / "_memory" / "model-context.yaml"
    if mc.exists():
        try:
            data = yaml.safe_load(mc.read_text(encoding="utf-8")) or {}
            n = len(data.get("sessions") or []) if isinstance(data, dict) else 0
            results.append((n <= mig.SESSIONS_KEEP,
                            f"model-context.yaml: {n} session(s) (cap {mig.SESSIONS_KEEP})"))
        except yaml.YAMLError as exc:
            results.append((False, f"model-context.yaml does not parse: {exc}"))
    else:
        results.append((True, "model-context.yaml absent; sessions check skipped"))

    cfg = ws / "_memory" / "config.yaml"
    sources = mig.enabled_sources(ws)
    if cfg.exists() and sources:
        try:
            config = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            config = None
            results.append((False, f"config.yaml does not parse: {exc}"))
        if isinstance(config, dict):
            prefs = config.get("preferences")
            sched = prefs.get("ingestion_schedule") if isinstance(prefs, dict) else None
            for row in sources:
                src = str(row["id"])
                group, value = mig.configured_group(config, src)
                ok_cfg = group is not None and (group == "" or value is True)
                ok_sched = isinstance(sched, dict) and src in sched
                results.append((ok_cfg and ok_sched,
                                f"config.yaml: {src} configured={ok_cfg} scheduled={ok_sched}"))
    else:
        results.append((True, "config.yaml: no enabled sources to check"))

    results.append(((ws / "Outbox" / "README.md").is_file(), "Outbox/README.md exists"))

    from superagent.tools.version import workspace_version
    current = workspace_version(ws)
    results.append((current == mig.TO_VERSION, f".version reads {current}"))
    return results


def run_validate(workspace: Path, out=print) -> int:
    """Print every check and return 0 iff all pass."""
    results = run_checks(workspace)
    for ok, msg in results:
        out(f"{'PASS' if ok else 'FAIL'}  {msg}")
    return 0 if all(ok for ok, _ in results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-0.18.0",
                                     description="Validate the 0.18.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace)


if __name__ == "__main__":
    sys.exit(main())
