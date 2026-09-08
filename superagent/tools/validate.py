#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Validate Superagent workspace memory files against expected schema.

Loads every YAML file under `<workspace>/_memory/`, verifies:
  - it parses
  - top-level `schema_version` is present and matches the framework's expected version
  - top-level keys match the template's top-level keys (no typos)
  - every list-of-rows file has at most one empty placeholder row
  - `watchlist-state.yaml` (when present) carries a `watchers` mapping

Then validates the watcher registry (`Sources/Watchlist/`, or
`config.preferences.watchlist.path`; per contracts/watchlist.md): every
top-level `.ref.md` there must carry canonical frontmatter with a `watch:`
mapping whose `pack` names a known pack or whose `type` (explicit, or defaulted
from the ref `kind`) is a shipped detect type. `index_query` is reserved and
rejected. A missing registry folder is fine (feature off).

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
}

# ------------------------------------------------------- watchlist schema
# Shared by this validator and by migration 0.19.0. The watchlist tool owns
# the runtime semantics (contracts/watchlist.md); these are the names.

WATCHLIST_STATE = "watchlist-state.yaml"
# Top-level keys the tool writes to the state singleton (`save_state`); always allowed.
WATCHLIST_STATE_TOP_KEYS = frozenset({"schema_version", "last_updated", "watchers"})
DEFAULT_WATCHLIST_PATH = "Sources/Watchlist"
# Detect types implemented in this release.
WATCH_TYPES = frozenset({"url", "path", "cmd", "subagent", "gmail", "harvest"})
# Reserved enum slot; the loader rejects it with "not implemented in this release".
WATCH_RESERVED_TYPES = frozenset({"index_query"})
# Packs that ship in core (`superagent/watchers/<id>/pack.yaml`). Discovery
# reads the folders when they exist; this set is the fallback so validation
# does not depend on the pack tree being present.
DEFAULT_WATCH_PACKS = frozenset({"simplefin", "gmail", "url", "cmd", "path", "subagent"})
# `watch.type` defaults from the ref's `kind` when omitted.
# Ref `kind` -> default detect type when `watch.type` / `watch.pack` are
# absent (contracts/watchlist.md § 2). `api`, `mcp`, `vault` deliberately
# have NO default: those refs must name a pack or an explicit type.
WATCH_TYPE_BY_REF_KIND = {
    "url": "url", "cli": "cmd", "file": "path", "manual": "subagent",
}
WATCH_CAPTURE_MODES = frozenset({"manual", "automatic"})
WATCH_SCALAR_KEYS = {
    # key: (allowed python types, allow None)
    "enabled": ((bool,), False),
    "evict_after_days": ((int,), True),
    "min_check_interval_minutes": ((int,), True),
    "min_change_interval_minutes": ((int,), True),
    "expires": ((str,), True),
    "schedule": ((str,), True),
    "selector": ((str,), True),
    "prompt": ((str,), True),
    "query": ((str,), True),
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
    if name == WATCHLIST_STATE:
        template_keys = (template_keys or set()) | WATCHLIST_STATE_TOP_KEYS
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
    if name == WATCHLIST_STATE:
        errors.extend(check_watchlist_state(data, name))
    return errors


def check_watchlist_state(data: dict[str, Any], name: str = WATCHLIST_STATE) -> list[str]:
    """`watchlist-state.yaml` is a state-shape singleton: `watchers` is a mapping of mappings."""
    errors: list[str] = []
    watchers = data.get("watchers")
    if watchers is None:
        errors.append(f"{name}: missing required key 'watchers' (use `watchers: {{}}`)")
        return errors
    if not isinstance(watchers, dict):
        errors.append(f"{name}: 'watchers' must be a mapping keyed by watcher id, "
                      f"got {type(watchers).__name__}")
        return errors
    for wid, row in watchers.items():
        if not isinstance(wid, str) or not wid:
            errors.append(f"{name}: watcher key {wid!r} must be a non-empty string id")
        if not isinstance(row, dict):
            errors.append(f"{name}: watchers.{wid} must be a mapping, got {type(row).__name__}")
    return errors


# ------------------------------------------------------- watchlist registry

def watchlist_path(workspace: Path) -> Path:
    """`<workspace>/<config.preferences.watchlist.path>` (default `Sources/Watchlist`)."""
    rel = DEFAULT_WATCHLIST_PATH
    cfg, err = load_yaml(workspace / "_memory" / "config.yaml")
    if not err and isinstance(cfg, dict):
        prefs = cfg.get("preferences")
        wl = prefs.get("watchlist") if isinstance(prefs, dict) else None
        if isinstance(wl, dict) and isinstance(wl.get("path"), str) and wl["path"].strip():
            rel = wl["path"].strip()
    return workspace / rel


def known_watch_packs(framework: Path, workspace: Path | None) -> set[str]:
    """Pack ids: `superagent/watchers/*/pack.yaml` + `<workspace>/_custom/watchers/*/pack.yaml`.

    Falls back to `DEFAULT_WATCH_PACKS` when neither tree exists, so validation
    does not depend on the pack folders having landed.
    """
    packs: set[str] = set()
    roots = [framework / "watchers"]
    if workspace is not None:
        roots.append(workspace / "_custom" / "watchers")
    for root in roots:
        if not root.is_dir():
            continue
        for pack in root.glob("*/pack.yaml"):
            if not pack.parent.name.startswith("_"):
                packs.add(pack.parent.name)
    return packs or set(DEFAULT_WATCH_PACKS)


def watch_ref_files(registry: Path) -> list[Path]:
    """Every top-level `<id>.ref.md` in the registry folder — exactly what the tool loads.

    Matches `tools/watchlist.py`'s scan: no `.ref.txt`, no sub-folders, and
    `README.md` is documentation, not a watcher.
    """
    if not registry.is_dir():
        return []
    return sorted(p for p in registry.glob("*.ref.md") if p.is_file())


def check_watch_block(watch: Any, ref_kind: Any, packs: set[str], label: str) -> list[str]:
    """Schema-check one `watch:` mapping. Returns error strings (empty = clean)."""
    errors: list[str] = []
    if not isinstance(watch, dict):
        return [f"{label}: 'watch' must be a mapping, got {type(watch).__name__}"]
    pack = watch.get("pack")
    wtype = watch.get("type")
    if pack is not None:
        if not isinstance(pack, str) or pack not in packs:
            errors.append(f"{label}: watch.pack {pack!r} is not a known pack "
                          f"({', '.join(sorted(packs))})")
    else:
        effective = wtype if wtype is not None else WATCH_TYPE_BY_REF_KIND.get(str(ref_kind or ""))
        if effective in WATCH_RESERVED_TYPES:
            errors.append(f"{label}: watch.type {effective!r} is reserved and not implemented "
                          "in this release")
        elif effective is None:
            errors.append(f"{label}: watch.type missing and ref kind {ref_kind!r} has no "
                          "default detect type")
        elif effective not in WATCH_TYPES:
            errors.append(f"{label}: watch.type {effective!r} is not a shipped detect type "
                          f"({', '.join(sorted(WATCH_TYPES))})")
        elif effective == "subagent" and not (isinstance(watch.get("prompt"), str)
                                              and watch["prompt"].strip()):
            errors.append(f"{label}: a bare subagent watcher needs watch.prompt")
        elif effective == "harvest":
            errors.append(f"{label}: watch.type 'harvest' needs a pack (set watch.pack)")
    for key, (types, nullable) in WATCH_SCALAR_KEYS.items():
        if key not in watch:
            continue
        v = watch[key]
        if v is None and nullable:
            continue
        if isinstance(v, bool) and bool not in types:
            errors.append(f"{label}: watch.{key} must be {'/'.join(t.__name__ for t in types)}")
        elif not isinstance(v, types):
            errors.append(f"{label}: watch.{key} must be {'/'.join(t.__name__ for t in types)}"
                          f"{' or null' if nullable else ''}, got {type(v).__name__}")
    if "cycles" in watch and not (isinstance(watch["cycles"], list)
                                  and all(isinstance(c, str) for c in watch["cycles"])):
        errors.append(f"{label}: watch.cycles must be a list of cadence names")
    if "capture_mode" in watch and watch["capture_mode"] not in WATCH_CAPTURE_MODES:
        errors.append(f"{label}: watch.capture_mode must be one of "
                      f"{sorted(WATCH_CAPTURE_MODES)}, got {watch['capture_mode']!r}")
    for key in ("params", "ignore_patterns"):
        if key in watch and watch[key] is not None:
            want = dict if key == "params" else list
            if not isinstance(watch[key], want):
                errors.append(f"{label}: watch.{key} must be a {want.__name__}")
    return errors


def validate_watchlist_refs(workspace: Path, framework: Path) -> tuple[list[str], list[str]]:
    """Validate every ref in the registry. Returns (ok_labels, errors)."""
    from superagent.tools.sources_index import parse_canonical_ref

    registry = watchlist_path(workspace)
    refs = watch_ref_files(registry)
    if not refs:
        return [], []
    packs = known_watch_packs(framework, workspace)
    oks: list[str] = []
    errors: list[str] = []
    for ref in refs:
        label = ref.relative_to(workspace).as_posix()
        fm, _body = parse_canonical_ref(ref)
        if fm is None:
            errors.append(f"{label}: not in canonical frontmatter form (normalize it first)")
            continue
        if "watch" not in fm:
            errors.append(f"{label}: registry ref has no 'watch:' block")
            continue
        errs = check_watch_block(fm["watch"], fm.get("kind"), packs, label)
        if errs:
            errors.extend(errs)
        else:
            oks.append(label)
    return oks, errors


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

    # Watcher registry (contracts/watchlist.md). Absent folder = feature off.
    ref_oks, ref_errs = validate_watchlist_refs(workspace, framework)
    if ref_oks or ref_errs:
        print()
        print(f"Validating {len(ref_oks) + len(ref_errs)} watcher ref(s) in "
              f"{watchlist_path(workspace).relative_to(workspace).as_posix()}/\n")
        for label in ref_oks:
            print(f"  OK     {label}")
        for e in ref_errs:
            print(f"  ERROR  {e}")
        total_errors += len(ref_errs)

    print()
    suffix = f" ({total_warnings} warning(s) — soft checks, exit code unaffected)" if total_warnings else ""
    n_files = len(yaml_files) + len(ref_oks) + len(ref_errs)
    if total_errors == 0:
        print(f"All {n_files} files clean.{suffix}")
        return 0
    print(f"{total_errors} error(s) found across {n_files} files.{suffix}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
