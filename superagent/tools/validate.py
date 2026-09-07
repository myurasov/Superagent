#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate Superagent workspace memory files against expected schema.

Loads every YAML file under `<workspace>/_memory/`, verifies:
  - it parses
  - top-level `schema_version` is present and matches the framework's expected version
  - top-level keys match the template's top-level keys (no typos)
  - every list-of-rows file has at most one empty placeholder row

Soft checks (WARNINGS — reported, never affect the exit code):
  - `interaction-log.yaml` rows whose `skill` value is not a skill manifest
    stem (`superagent/skills/<stem>.md` or `<workspace>/_custom/skills/<stem>.md`);
    the legacy `superagent-<stem>` alias is counted separately as "prefixed".
  - `interaction-log.yaml` rows already written in the canonical shape (no
    legacy `timestamp` key) that lack `id` / `ts` / `skill`.
  Legacy rows are append-only history and are never rewritten; the warnings
  exist so drift in NEW appends is visible (per contracts/events-stream.md
  § "Canonical row shape").

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
    "inbox-log.yaml": "decisions",
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


def is_placeholder_row(row: Any) -> bool:
    """True for a template placeholder row (every value empty / null / zero)."""
    return isinstance(row, dict) and all(
        v in (None, "", [], {}, 0) for v in row.values()
    )


def validate_data(name: str, data: Any, framework: Path) -> list[str]:
    """Validate already-loaded memory data for file `name`. Returns error strings."""
    errors: list[str] = []
    if data is None:
        errors.append(f"{name}: file is empty (expected at least schema_version)")
        return errors
    if not isinstance(data, dict):
        errors.append(f"{name}: top-level must be a mapping, got {type(data).__name__}")
        return errors
    if "schema_version" not in data:
        errors.append(f"{name}: missing required key 'schema_version'")
    template_keys = get_template_keys(framework, name)
    if template_keys is not None:
        actual = set(data.keys())
        unexpected = actual - template_keys
        if unexpected:
            errors.append(
                f"{name}: unexpected top-level keys {sorted(unexpected)} "
                f"(template has {sorted(template_keys)})"
            )
    list_key = LIST_FILES.get(name)
    if list_key and list_key in data:
        rows = data[list_key]
        if not isinstance(rows, list):
            errors.append(
                f"{name}: top-level '{list_key}' must be a list, "
                f"got {type(rows).__name__}"
            )
        else:
            empty_rows = [i for i, row in enumerate(rows) if is_placeholder_row(row)]
            if len(empty_rows) > 1:
                errors.append(
                    f"{name}: {len(empty_rows)} placeholder rows found "
                    f"(at most 1 expected for templates)"
                )
    return errors


def validate_file(path: Path, framework: Path) -> list[str]:
    """Validate one memory file. Returns list of error strings (empty = clean)."""
    data, err = load_yaml(path)
    if err:
        return [f"{path.name}: parse failed: {err}"]
    return validate_data(path.name, data, framework)


# ------------------------------------------------ interaction-log soft checks

INTERACTION_LOG = "interaction-log.yaml"
LEGACY_SKILL_PREFIX = "superagent-"
CANONICAL_ILOG_KEYS = ("id", "ts", "skill")
PREVIEW_LIMIT = 8


def skill_stems(framework: Path, workspace: Path | None) -> set[str]:
    """Skill manifest stems: `superagent/skills/*.md` plus `<workspace>/_custom/skills/*.md`.

    A stem is the `.md` filename without its suffix (`migrate`, `browserctl`,
    `browserctl.<app>`). Underscore-prefixed framework files (`_manifest.yaml`,
    `_template.md`) are not skills.
    """
    stems: set[str] = set()
    framework_skills = framework / "skills"
    if framework_skills.is_dir():
        stems.update(
            p.stem for p in framework_skills.glob("*.md") if not p.name.startswith("_")
        )
    if workspace is not None:
        custom_skills = workspace / "_custom" / "skills"
        if custom_skills.is_dir():
            stems.update(
                p.stem for p in custom_skills.glob("*.md") if not p.name.startswith("_")
            )
    return stems


def _preview(values: dict[str, int]) -> str:
    items = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ", ".join(f"{v!r} x{n}" for v, n in items[:PREVIEW_LIMIT])
    if len(items) > PREVIEW_LIMIT:
        shown += f", ... (+{len(items) - PREVIEW_LIMIT} more)"
    return shown


def check_interaction_log(rows: Any, stems: set[str], name: str = INTERACTION_LOG) -> list[str]:
    """Soft-check interaction-log rows. Returns WARNING strings (never errors).

    - `skill` values that are neither a stem nor null -> "not a skill stem".
    - `superagent-<stem>` values -> reported separately as the legacy prefixed alias.
    - Rows already in the canonical shape (no legacy `timestamp` key) that lack
      any of `id` / `ts` / `skill` -> "new-shape row(s) missing ...".

    Legacy `timestamp` / `type` / `subject` rows are append-only history:
    their shape is never flagged, only their `skill` value if they carry one.
    """
    if not isinstance(rows, list):
        return []
    unknown: dict[str, int] = {}
    prefixed: dict[str, int] = {}
    incomplete: list[int] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or is_placeholder_row(row):
            continue
        if "skill" in row and row["skill"] is not None:
            value = row["skill"]
            text = value if isinstance(value, str) else repr(value)
            if text in stems:
                pass
            elif (
                isinstance(value, str)
                and text.startswith(LEGACY_SKILL_PREFIX)
                and text[len(LEGACY_SKILL_PREFIX):] in stems
            ):
                prefixed[text] = prefixed.get(text, 0) + 1
            else:
                unknown[text] = unknown.get(text, 0) + 1
        if "timestamp" not in row:
            missing = [k for k in CANONICAL_ILOG_KEYS if k not in row]
            if missing:
                incomplete.append(i)
    warnings: list[str] = []
    if unknown:
        warnings.append(
            f"{name}: {sum(unknown.values())} row(s) use a 'skill' value that is not "
            f"a skill stem (framework or _custom overlay): {_preview(unknown)}"
        )
    if prefixed:
        warnings.append(
            f"{name}: {sum(prefixed.values())} row(s) use the legacy prefixed "
            f"'superagent-<stem>' skill alias: {_preview(prefixed)}"
        )
    if incomplete:
        shown = ", ".join(str(i) for i in incomplete[:PREVIEW_LIMIT])
        if len(incomplete) > PREVIEW_LIMIT:
            shown += f", ... (+{len(incomplete) - PREVIEW_LIMIT} more)"
        warnings.append(
            f"{name}: {len(incomplete)} new-shape row(s) missing one of "
            f"{'/'.join(CANONICAL_ILOG_KEYS)} (row index {shown})"
        )
    return warnings


def warnings_for(name: str, data: Any, framework: Path, workspace: Path | None) -> list[str]:
    """Soft checks for one loaded memory file. Currently only the interaction log."""
    if name != INTERACTION_LOG or not isinstance(data, dict):
        return []
    return check_interaction_log(
        data.get(LIST_FILES[INTERACTION_LOG]), skill_stems(framework, workspace), name
    )


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
    total_warnings = 0
    for path in yaml_files:
        data, err = load_yaml(path)
        if err:
            errs = [f"{path.name}: parse failed: {err}"]
            warns: list[str] = []
        else:
            errs = validate_data(path.name, data, framework)
            warns = warnings_for(path.name, data, framework, workspace)
        if errs:
            total_errors += len(errs)
            for e in errs:
                print(f"  ERROR  {e}")
        else:
            print(f"  OK     {path.name}")
        total_warnings += len(warns)
        for w in warns:
            print(f"  WARN   {w}")
    print()
    suffix = f" ({total_warnings} warning(s) — soft checks, exit code unaffected)" if total_warnings else ""
    if total_errors == 0:
        print(f"All {len(yaml_files)} files clean.{suffix}")
        return 0
    print(f"{total_errors} error(s) found across {len(yaml_files)} files.{suffix}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
