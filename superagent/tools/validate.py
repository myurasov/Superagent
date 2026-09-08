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
top-level `.ref.md` there is a WATCHER DEFINITION (`ref_version: 2`) and must
carry frontmatter with a `watch:` mapping whose `pack` names a known pack or
whose `type` is a built-in detect type with its locator present
(`watch.url` / `watch.path` / `watch.cmd` / `watch.prompt`). The retired
0.19.0 reference keys (`kind`, `source`, `ttl_minutes`, `sensitive`,
`auth_ref`, `chunk_for_large`, `normalized_at`) are rejected with a pointer
at the 0.20.0 migration. The filename stem lowercased is the watcher id
(`^[a-z0-9][a-z0-9_-]{0,62}$`); two files whose lowercase stems collide are
an error. `index_query` is reserved and rejected. A missing registry folder
is fine (feature off).

Soft checks (WARNINGS — reported, never affect the exit code):
  - `interaction-log.yaml` rows whose `skill` value is not a skill manifest
    stem (`superagent/skills/<stem>.md` or `<workspace>/_custom/skills/<stem>.md`);
    the legacy `superagent-<stem>` alias is counted separately as "prefixed".
  - `interaction-log.yaml` rows already written in the canonical shape (no
    legacy `timestamp` key) that lack `id` / `ts` / `skill`.
  - a registry ref whose filename is not the canonical Title_Case form
    (`Home_Assistant-Hub.ref.md`); the tool loads it case-insensitively.
  - a stray `.ref.md` outside the registry (document metadata belongs in
    `<doc>.<ext>.meta.md`), or a `.ref.txt` anywhere (no longer supported).
  Legacy rows are append-only history and are never rewritten; the warnings
  exist so drift in NEW appends is visible (per contracts/events-stream.md
  § "Canonical row shape").

Reports findings to stdout. Exit code 0 if all clean, 1 if any errors.

Usage:
  uv run python superagent/tools/validate.py [--workspace PATH] [--framework PATH]
"""
from __future__ import annotations

import argparse
import re
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
# Shared by this validator, `tools/watchlist.py`, `tools/sources_index.py`
# and the migrations (0.19.0, 0.20.0). The watchlist tool owns the runtime
# semantics (contracts/watchlist.md); these are the names.

WATCHLIST_STATE = "watchlist-state.yaml"
# Top-level keys the tool writes to the state singleton (`save_state`); always allowed.
WATCHLIST_STATE_TOP_KEYS = frozenset({"schema_version", "last_updated", "watchers"})
DEFAULT_WATCHLIST_PATH = "Sources/Watchlist"
# A registry file is `<Title_Case>.ref.md`; a document sidecar is `<doc>.<ext>.meta.md`.
REF_SUFFIX = ".ref.md"
META_SUFFIX = ".meta.md"
# The ref schema this release reads. 1 = the 0.19.0 "reference + watch block"
# shape (kind / source / ttl_minutes ...), converted by the 0.20.0 migration.
REF_VERSION = 2
LEGACY_REF_MIGRATION = "0.20.0"
# Frontmatter keys of the retired pull-on-demand reference model. A ref that
# still carries one is rejected on load with a pointer at the migration.
LEGACY_REF_KEYS = frozenset({
    "kind", "source", "ttl_minutes", "sensitive", "auth_ref", "chunk_for_large", "normalized_at",
})
# Every top-level key a `ref_version: 2` file may carry.
REF_TOP_KEYS = frozenset({
    "ref_version", "title", "description",
    "related_domain", "related_project", "related_asset", "related_account",
    "tags", "added_by", "added_at", "watch",
})
# Watcher id = filename stem lowercased = state key = `watch:<id>` handle.
WATCH_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
WATCH_ID_RE = re.compile(WATCH_ID_PATTERN)
# Detect types the schema layer knows in this release (built-in + pack-provided).
WATCH_TYPES = frozenset({"url", "path", "cmd", "subagent", "gmail", "harvest"})
# Detect types implemented inside `tools/watchlist.py`; anything else (e.g.
# `gmail`) is provided by a pack's handler.py and needs `watch.pack`.
WATCH_BUILTIN_TYPES = frozenset({"url", "path", "cmd", "subagent", "harvest"})
# Which `watch.` key carries the locator for each detect type. A bare
# (packless) watcher must set it; a pack instance supplies it via `params`.
WATCH_LOCATOR_KEY = {
    "url": "url", "path": "path", "cmd": "cmd", "subagent": "prompt", "gmail": "query",
}
# Reserved enum slot; the loader rejects it with "not implemented in this release".
WATCH_RESERVED_TYPES = frozenset({"index_query"})
# Packs that ship in core (`superagent/watchers/<id>/pack.yaml`). Discovery
# reads the folders when they exist; this set is the fallback so validation
# does not depend on the pack tree being present.
DEFAULT_WATCH_PACKS = frozenset({"simplefin", "gmail", "url", "cmd", "path", "subagent"})
# LEGACY (0.19.0 ref schema). Ref `kind` -> the detect type it implied when
# `watch.type` / `watch.pack` were absent. Retained ONLY for migrations that
# convert `ref_version: 1` files (kind -> watch.type); neither the tool nor
# this validator defaults from `kind` any more -- `watch.pack` or `watch.type`
# is required.
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
    "url": ((str,), True),
    "path": ((str,), True),
    "cmd": ((str,), True),
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


def is_ref_name(name: str) -> bool:
    """True for `<stem>.ref.md`, matched case-insensitively (`X.Ref.MD` counts)."""
    return name.lower().endswith(REF_SUFFIX) and len(name) > len(REF_SUFFIX)


def ref_stem(name: str) -> str:
    """`Home_Assistant-Hub.ref.md` -> `Home_Assistant-Hub` (suffix matched case-insensitively)."""
    return name[: -len(REF_SUFFIX)] if is_ref_name(name) else Path(name).stem


def watch_id_from_stem(stem: str) -> str:
    """The watcher id for a filename stem: lowercased (`Simplefin` -> `simplefin`)."""
    return stem.lower()


def title_case_id(watcher_id: str) -> str:
    """Canonical filename stem for an id: capitalize each `_` / `-` token's first letter.

    `simplefin` -> `Simplefin`; `home_assistant-hub` -> `Home_Assistant-Hub`.
    Digits are left alone (`2fa_codes` -> `2fa_Codes`). Idempotent.
    """
    return re.sub(r"(^|[_-])([a-z])", lambda m: m.group(1) + m.group(2).upper(),
                  watcher_id.lower())


def watch_ref_files(registry: Path) -> list[Path]:
    """Every top-level `*.ref.md` in the registry folder — exactly what the tool loads.

    Matches `tools/watchlist.py`'s scan: suffix matched case-insensitively, no
    sub-folders, and `README.md` / `.meta.md` files are not watchers.
    """
    if not registry.is_dir():
        return []
    return sorted((p for p in registry.iterdir() if p.is_file() and is_ref_name(p.name)),
                  key=lambda p: p.name.lower())


def check_ref_frontmatter(fm: Any, packs: set[str], label: str) -> list[str]:
    """Schema-check one `ref_version: 2` frontmatter mapping (a watcher definition).

    Legacy 0.19.0 reference keys are rejected by name, pointing at the 0.20.0
    migration; the `watch:` block is then checked by `check_watch_block`.
    """
    if not isinstance(fm, dict):
        return [f"{label}: frontmatter must be a mapping, got {type(fm).__name__}"]
    errors: list[str] = []
    legacy = [k for k in fm if k in LEGACY_REF_KEYS]
    if legacy:
        errors.append(
            f"{label}: legacy reference key(s) {', '.join(repr(k) for k in legacy)} — since "
            f"{LEGACY_REF_MIGRATION} a .ref.md is a watcher definition only (ref_version "
            f"{REF_VERSION}); run the {LEGACY_REF_MIGRATION} migration (`migrate`) or move the "
            "locator into watch: (url: / path: / cmd: / prompt: / query:)"
        )
    version = fm.get("ref_version")
    if version is None:
        errors.append(f"{label}: ref_version: {REF_VERSION} is required (a .ref.md is a watcher "
                      f"definition; a ref written before {LEGACY_REF_MIGRATION} is converted by "
                      f"the {LEGACY_REF_MIGRATION} migration)")
    elif version != REF_VERSION:
        errors.append(f"{label}: ref_version {version!r} is not {REF_VERSION}; run the "
                      f"{LEGACY_REF_MIGRATION} migration (`migrate`) to convert this ref")
    unknown = sorted(str(k) for k in fm if k not in REF_TOP_KEYS and k not in LEGACY_REF_KEYS)
    if unknown:
        errors.append(f"{label}: unknown frontmatter key(s) {', '.join(repr(k) for k in unknown)} "
                      f"(ref_version {REF_VERSION} allows {', '.join(sorted(REF_TOP_KEYS))})")
    if "watch" not in fm or fm["watch"] is None:
        errors.append(f"{label}: registry ref has no 'watch:' block (set watch.pack or watch.type)")
    else:
        errors.extend(check_watch_block(fm["watch"], packs, label))
    return errors


def check_watch_block(watch: Any, packs: set[str], label: str) -> list[str]:
    """Schema-check one `watch:` mapping. Returns error strings (empty = clean).

    `watch.pack` or `watch.type` is required (nothing defaults from a ref
    `kind` any more). A bare watcher must be a built-in type and carry its
    locator (`WATCH_LOCATOR_KEY`) inside the block.
    """
    errors: list[str] = []
    if not isinstance(watch, dict):
        return [f"{label}: 'watch' must be a mapping, got {type(watch).__name__}"]
    pack = watch.get("pack")
    wtype = watch.get("type")
    if pack is not None:
        if not isinstance(pack, str) or pack not in packs:
            errors.append(f"{label}: watch.pack {pack!r} is not a known pack "
                          f"({', '.join(sorted(packs))})")
    elif wtype is None:
        errors.append(f"{label}: watch.pack or watch.type is required")
    elif wtype in WATCH_RESERVED_TYPES:
        errors.append(f"{label}: watch.type {wtype!r} is reserved and not implemented "
                      "in this release")
    elif wtype not in WATCH_TYPES:
        errors.append(f"{label}: watch.type {wtype!r} is not a shipped detect type "
                      f"({', '.join(sorted(WATCH_TYPES))})")
    elif wtype == "harvest":
        errors.append(f"{label}: watch.type 'harvest' needs a pack (set watch.pack)")
    elif wtype not in WATCH_BUILTIN_TYPES:
        errors.append(f"{label}: watch.type {wtype!r} is provided by a pack, not built in; "
                      f"set watch.pack: {wtype}")
    else:
        locator = WATCH_LOCATOR_KEY[wtype]
        if not (isinstance(watch.get(locator), str) and watch[locator].strip()):
            errors.append(f"{label}: a bare {wtype} watcher needs watch.{locator}")
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
    if "cycles" in watch:
        # Mirrors the loader: `null` = no cycles, a bare string = a one-item list.
        cyc = watch["cycles"]
        if isinstance(cyc, str):
            cyc = [cyc]
        if cyc is not None and not (isinstance(cyc, list) and all(isinstance(c, str) for c in cyc)):
            errors.append(f"{label}: watch.cycles must be a list of cadence names (or one name, or null)")
    if "capture_mode" in watch and watch["capture_mode"] not in WATCH_CAPTURE_MODES:
        errors.append(f"{label}: watch.capture_mode must be one of "
                      f"{sorted(WATCH_CAPTURE_MODES)}, got {watch['capture_mode']!r}")
    for key in ("params", "ignore_patterns"):
        if key in watch and watch[key] is not None:
            want = dict if key == "params" else list
            if not isinstance(watch[key], want):
                errors.append(f"{label}: watch.{key} must be a {want.__name__}")
    return errors


def _sources_roots(workspace: Path) -> list[Path]:
    """`Sources/` plus every project's `Sources/` and `Resources/` (payment
    confirmations and their sidecars live under `Resources/`, per
    contracts/payment-confirmations.md)."""
    roots = [workspace / "Sources"]
    projects = workspace / "Projects"
    if projects.is_dir():
        for p in sorted(projects.iterdir()):
            for sub in ("Sources", "Resources"):
                if (p / sub).is_dir():
                    roots.append(p / sub)
    return [r for r in roots if r.is_dir()]


def stray_ref_warnings(workspace: Path, registry: Path) -> list[str]:
    """Soft checks over `Sources/` trees: `.ref.md` outside the registry, any `.ref.txt`.

    Since 0.20.0 every `.ref.md` is a watcher and lives in the registry;
    document metadata is `<doc>.<ext>.meta.md`. `.ref.txt` is not read by
    anything any more.
    """
    warnings: list[str] = []
    registry_resolved = registry.resolve() if registry.exists() else registry
    for root in _sources_roots(workspace):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace).as_posix()
            name = path.name.lower()
            if name.endswith(".ref.txt"):
                warnings.append(f"{rel}: `.ref.txt` is no longer supported ({LEGACY_REF_MIGRATION}); "
                                "convert it to a watcher ref in the registry or delete it")
            elif is_ref_name(path.name) and path.parent.resolve() != registry_resolved:
                stem = ref_stem(path.name)
                # A legacy `<doc>.<ext>.ref.md` sidecar sitting next to its document: name the rename.
                hint = (f"rename it to `{stem}{META_SUFFIX}`" if (path.parent / stem).is_file()
                        else f"document metadata belongs in <doc>.<ext>{META_SUFFIX}")
                warnings.append(
                    f"{rel}: stray .ref.md outside the registry — a .ref.md is a watcher "
                    f"definition ({registry.relative_to(workspace).as_posix()}/); {hint}"
                )
    return warnings


def validate_watchlist_refs(workspace: Path, framework: Path,
                            ) -> tuple[list[str], list[str], list[str]]:
    """Validate every ref in the registry. Returns (ok_labels, errors, warnings).

    Errors: schema (`check_ref_frontmatter`), an id that fails
    `WATCH_ID_PATTERN` once lowercased, two files whose lowercase stems
    collide. Warnings: a filename that is not the canonical Title_Case form,
    plus `stray_ref_warnings` over the Sources trees.
    """
    from superagent.tools.sources_index import parse_canonical_ref

    registry = watchlist_path(workspace)
    refs = watch_ref_files(registry)
    warnings = stray_ref_warnings(workspace, registry)
    if not refs:
        return [], [], warnings
    packs = known_watch_packs(framework, workspace)
    oks: list[str] = []
    errors: list[str] = []
    by_id: dict[str, list[Path]] = {}
    for ref in refs:
        by_id.setdefault(watch_id_from_stem(ref_stem(ref.name)), []).append(ref)
    for ref in refs:
        label = ref.relative_to(workspace).as_posix()
        stem = ref_stem(ref.name)
        wid = watch_id_from_stem(stem)
        if not WATCH_ID_RE.match(wid):
            errors.append(f"{label}: id {wid!r} (filename stem, lowercased) must match "
                          f"{WATCH_ID_PATTERN}: lowercase letters, digits, `_` and `-` only")
            continue
        siblings = by_id[wid]
        if len(siblings) > 1:
            errors.append(f"{label}: watcher id {wid!r} is claimed by "
                          f"{len(siblings)} files ({', '.join(p.name for p in siblings)}); "
                          "ids resolve case-insensitively — rename one")
            continue
        if stem != title_case_id(wid):
            warnings.append(f"{label}: filename is not Title_Case; canonical name is "
                            f"{title_case_id(wid)}{REF_SUFFIX} (loaded case-insensitively)")
        fm, _body = parse_canonical_ref(ref)
        if fm is None:
            errors.append(f"{label}: missing or unparseable YAML frontmatter (`---` block)")
            continue
        errs = check_ref_frontmatter(fm, packs, label)
        if errs:
            errors.extend(errs)
        else:
            oks.append(label)
    return oks, errors, warnings


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
    ref_oks, ref_errs, ref_warns = validate_watchlist_refs(workspace, framework)
    if ref_oks or ref_errs or ref_warns:
        print()
        print(f"Validating {len(ref_oks) + len(ref_errs)} watcher ref(s) in "
              f"{watchlist_path(workspace).relative_to(workspace).as_posix()}/\n")
        for label in ref_oks:
            print(f"  OK     {label}")
        for e in ref_errs:
            print(f"  ERROR  {e}")
        for w in ref_warns:
            print(f"  WARN   {w}")
        total_errors += len(ref_errs)
        total_warnings += len(ref_warns)

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
