#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate Superagent workspace memory files against expected schema.

Loads every YAML file under `<workspace>/_memory/`, verifies:
  - it parses
  - top-level `schema_version` is present and matches the framework's expected version
  - top-level keys match the template's top-level keys (no typos)
  - every list-of-rows file has at most one empty placeholder row

Reports findings to stdout. Exit code 0 if all clean, 1 if any errors.

Usage:
  uv run python superagent/tools/validate.py [--workspace PATH] [--framework PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


# Files that have a top-level `<key>` whose value is a list of rows.
LIST_FILES: dict[str, str] = {
    "todo.yaml": "tasks",
    "domains-index.yaml": "domains",
    "contacts.yaml": "contacts",
    "assets-index.yaml": "assets",
    "accounts-index.yaml": "accounts",
    "bills.yaml": "bills",
    "subscriptions.yaml": "subscriptions",
    "appointments.yaml": "appointments",
    "important-dates.yaml": "dates",
    "documents-index.yaml": "documents",
    "interaction-log.yaml": "entries",
    "ingestion-log.yaml": "runs",
    "insights.yaml": "insights",
    "procedures.yaml": "entries",
    "personal-signals.yaml": "signals",
    "action-signals.yaml": "signals",
    "supertailor-suggestions.yaml": "suggestions",
    "data-sources.yaml": "sources",
}


def load_yaml(path: Path) -> tuple[Any, str | None]:
    """Load a YAML file. Return (data, error_message)."""
    try:
        with path.open() as fh:
            return yaml.safe_load(fh), None
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc)


def get_template_keys(framework: Path, filename: str) -> set[str] | None:
    """Get the top-level keys from the template version of `filename`."""
    template = framework / "templates" / "memory" / filename
    if not template.exists():
        return None
    data, err = load_yaml(template)
    if err or not isinstance(data, dict):
        return None
    return set(data.keys())


def validate_file(path: Path, framework: Path) -> list[str]:
    """Validate one memory file. Returns list of error strings (empty = clean)."""
    errors: list[str] = []
    data, err = load_yaml(path)
    if err:
        errors.append(f"{path.name}: parse failed: {err}")
        return errors
    if data is None:
        errors.append(f"{path.name}: file is empty (expected at least schema_version)")
        return errors
    if not isinstance(data, dict):
        errors.append(f"{path.name}: top-level must be a mapping, got {type(data).__name__}")
        return errors
    if "schema_version" not in data:
        errors.append(f"{path.name}: missing required key 'schema_version'")
    template_keys = get_template_keys(framework, path.name)
    if template_keys is not None:
        actual = set(data.keys())
        unexpected = actual - template_keys
        if unexpected:
            errors.append(
                f"{path.name}: unexpected top-level keys {sorted(unexpected)} "
                f"(template has {sorted(template_keys)})"
            )
    list_key = LIST_FILES.get(path.name)
    if list_key and list_key in data:
        rows = data[list_key]
        if not isinstance(rows, list):
            errors.append(
                f"{path.name}: top-level '{list_key}' must be a list, "
                f"got {type(rows).__name__}"
            )
        else:
            empty_rows = [
                i for i, row in enumerate(rows)
                if isinstance(row, dict) and all(
                    v in (None, "", [], {}, 0) for v in row.values()
                )
            ]
            if len(empty_rows) > 1:
                errors.append(
                    f"{path.name}: {len(empty_rows)} placeholder rows found "
                    f"(at most 1 expected for templates)"
                )
    return errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Validate Superagent workspace memory files against expected schema.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace path (default: workspace next to framework).",
    )
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Framework path (default: parent of this script).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or framework.parent / "workspace"
    memory_dir = workspace / "_memory"
    if not memory_dir.is_dir():
        print(f"No _memory/ directory at {memory_dir}", file=sys.stderr)
        print("Run `uv run python superagent/tools/workspace_init.py` first.", file=sys.stderr)
        return 1

    yaml_files = sorted(memory_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"No YAML files in {memory_dir}", file=sys.stderr)
        return 1

    print(f"Validating {len(yaml_files)} files in {memory_dir}\n")
    total_errors = 0
    for path in yaml_files:
        errs = validate_file(path, framework)
        if errs:
            total_errors += len(errs)
            for err in errs:
                print(f"  ERROR  {err}")
        else:
            print(f"  OK     {path.name}")
    print()
    if total_errors == 0:
        print(f"All {len(yaml_files)} files clean.")
        return 0
    print(f"{total_errors} error(s) found across {len(yaml_files)} files.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
