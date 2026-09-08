#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""0.20.0 migration helper (from 0.19.0) -- refs are watchers only.

Canonical instructions live in ``superagent/migrations/0.20.0.md``; this
script is the executable form of its ``## Migrate`` steps. Every step is
idempotent and safe on a workspace where its condition does not apply.

Steps (in order):

1. Every ``<registry>/*.ref.md`` moves to ref schema 2 by text edit:
   ``ref_version: 2``; the legacy top-level keys ``kind``, ``source``,
   ``ttl_minutes``, ``sensitive``, ``auth_ref``, ``chunk_for_large`` and
   ``normalized_at`` are dropped; a bare watcher's old ``source`` becomes the
   locator inside ``watch:`` (``cmd`` -> ``watch.cmd``, ``url`` -> ``watch.url``,
   ``path`` -> ``watch.path``); a ref that relied on kind-to-type defaulting
   gets an explicit ``watch.type``; a pack instance simply drops ``source``.
   Every other line, comment and the markdown body are preserved.
2. Registry files the framework itself wrote -- frontmatter ``added_by`` of
   ``watch``, ``init`` or ``migrate-*`` -- become Title_Case
   (``simplefin.ref.md`` -> ``Simplefin.ref.md``); a case-only rename goes
   through a temp name so it works on a case-insensitive filesystem. A ref
   with ``added_by: user``, any other value, or no ``added_by`` at all is
   user-named: its casing is respected and it is reported as kept. The
   ``_memory/sources-index.yaml`` row keeps its id (path rewritten in place)
   and catalogue rows follow.
3. Every document sidecar ``<doc>.<ext>.ref.md`` under ``Sources/``,
   ``Projects/*/Sources/`` and ``Projects/*/Resources/`` becomes
   ``<doc>.<ext>.meta.md``; catalogue rows and the index row (id kept) follow.
   A ``.ref.md`` that is neither a sidecar nor in the registry is reported as a
   stray and left alone.
4. The registry's SimpleFIN ref reads ``cycles: [daily-update]``,
   ``schedule: daily``, ``capture_mode: automatic`` -- a user-directed cadence
   change (the previous values go into the ledger).
5. ``Sources/_cache/`` is removed when (and only when) it is empty.
6. Sources index refreshed; ``_memory/world.yaml`` checkpointed and rebuilt;
   ``.version`` -> 0.20.0. The machine-owned ``watchlist-state.yaml`` is never
   touched (validate's dry run passes ``--no-harvest``, so it writes no state).

Before any existing file is rewritten or renamed its original bytes are copied
to ``<workspace>/_memory/_checkpoints/0.20.0/<relative path>`` so ``revert.py``
can restore them. A successful ``validate.py`` relocates that folder to
``_memory/_retired/0.20.0-originals/``. Every rename / conversion / rewrite is
also recorded in ``_memory/_retired/0.20.0-moves.yaml`` (drives revert).

Usage::

    uv run python superagent/migrations/0.20.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success (or nothing to do), 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:  # allow `uv run python <this file>`
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from superagent.tools import sources_index as si  # noqa: E402
from superagent.tools.validate import (  # noqa: E402  (schema names shared with the tool)
    DEFAULT_WATCHLIST_PATH,
    LEGACY_REF_KEYS,
    META_SUFFIX,
    REF_SUFFIX,
    REF_TOP_KEYS,
    REF_VERSION,
    WATCH_LOCATOR_KEY,
    WATCH_TYPE_BY_REF_KIND,
)
from superagent.tools.watchlist import id_from_stem, title_case  # noqa: E402

TO_VERSION = "0.20.0"
FROM_VERSION = "0.19.0"
CHECKPOINT_REL = Path("_memory") / "_checkpoints" / TO_VERSION
SEEDED_MARKER_NAME = "_seeded.txt"
RETIRED_REL = Path("_memory") / "_retired"
MOVES_MANIFEST_NAME = f"{TO_VERSION}-moves.yaml"
# Where validate.py parks the checkpoint folder once every check has passed.
ORIGINALS_REL = RETIRED_REL / f"{TO_VERSION}-originals"
STATE_NAME = "watchlist-state.yaml"
STATE_LOCK_NAME = ".watchlist-state.lock"
LEGACY_TXT_SUFFIX = ".ref.txt"
# Top-level frontmatter keys of the retired reference model (display order).
LEGACY_TOP_KEYS = tuple(k for k in ("kind", "source", "ttl_minutes", "sensitive", "auth_ref",
                                    "chunk_for_large", "normalized_at") if k in LEGACY_REF_KEYS)
# Which `watch:` key carries the locator for each bare detect type.
LOCATOR_BY_TYPE = dict(WATCH_LOCATOR_KEY)
# Entries cut from the frontmatter are kept verbatim in the body under this marker
# when they carry information the tool no longer reads (nothing is lost).
KEPT_MARKER = f"<!-- {TO_VERSION}: frontmatter entries outside ref_version {REF_VERSION}, kept verbatim; not read by the tool -->"
SIMPLEFIN_PACK = "simplefin"
SIMPLEFIN_CADENCE: dict[str, Any] = {
    "schedule": "daily", "capture_mode": "automatic", "cycles": ["daily-update"],
}
CACHE_REL = "Sources/_cache"
# `.ref.md` frontmatter keys that mark a payment-confirmation sidecar
# (contracts/payment-confirmations.md § 2).
PAYMENT_KEYS = ("payee", "amount", "confirmation")
# `_memory/<file>` YAML inputs that must parse before any step writes.
PREFLIGHT_YAML = ("config.yaml", "sources-index.yaml", STATE_NAME)
RENAME_TMP_SUFFIX = ".0-20-0.renaming"
ABSENT = "<absent>"
EM_DASH = "—"
TOP_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(\s|$)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC).astimezone()).replace(microsecond=0).isoformat()


def default_framework_root() -> Path:
    """`superagent/` as resolved from this script's location."""
    return Path(__file__).resolve().parents[2]


def yaml_scalar(value: Any) -> str:
    """Render one scalar as a single-line YAML value (double-quoted strings)."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return yaml.safe_dump(value, default_flow_style=True).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def is_ref_name(name: str) -> bool:
    return name.endswith(REF_SUFFIX)


def is_meta_name(name: str) -> bool:
    return name.endswith(META_SUFFIX)


def _is_ref_or_meta(path: Path) -> bool:
    return is_ref_name(path.name) or is_meta_name(path.name) or path.name.endswith(LEGACY_TXT_SUFFIX)


def target_name(name: str) -> str:
    """Title_Case registry filename for a `.ref.md` name (`simplefin.ref.md` -> `Simplefin.ref.md`)."""
    stem = name[: -len(REF_SUFFIX)]
    return title_case(id_from_stem(stem)) + REF_SUFFIX


# `added_by` values that mean the FRAMEWORK chose the filename: `enable` (`watch`),
# `init`, and any earlier migration (`migrate-0.19.0`, ...). Only these refs are
# renamed to Title_Case; every other ref is user-named and keeps its casing.
FRAMEWORK_AUTHORS = ("watch", "init")
FRAMEWORK_AUTHOR_PREFIX = "migrate"


def framework_written(fm: dict[str, Any] | None) -> bool:
    """True when the ref's frontmatter says the framework named the file.

    `added_by: watch` / `init` / `migrate-*` -> True. `added_by: user`, any
    other value, a missing key, or unparseable frontmatter -> False (the user's
    name is respected whenever authorship is not provably the tool's).
    """
    if not isinstance(fm, dict):
        return False
    who = fm.get("added_by")
    if not isinstance(who, str):
        return False
    who = who.strip()
    return who in FRAMEWORK_AUTHORS or who.startswith(FRAMEWORK_AUTHOR_PREFIX)


def rename_target(ref: Path) -> str:
    """The on-disk name this migration wants for `ref`.

    Title_Case (`target_name`) for a framework-written ref; the ref's own name,
    unchanged, for a user-named one.
    """
    return target_name(ref.name) if framework_written(frontmatter_of(ref)) else ref.name


def watchlist_rel_path(config: dict[str, Any] | None) -> str:
    """`preferences.watchlist.path` from a parsed config, else the default."""
    prefs = (config or {}).get("preferences") if isinstance(config, dict) else None
    wl = prefs.get("watchlist") if isinstance(prefs, dict) else None
    if isinstance(wl, dict) and isinstance(wl.get("path"), str) and wl["path"].strip():
        return wl["path"].strip().strip("/")
    return DEFAULT_WATCHLIST_PATH


def registry_refs(registry: Path) -> list[Path]:
    """Every `*.ref.md` directly in the registry, by its on-disk name (README.md is not one)."""
    if not registry.is_dir():
        return []
    return sorted(p for p in registry.iterdir() if p.is_file() and is_ref_name(p.name))


def rename_two_step(src: Path, dest: Path) -> None:
    """Rename through a temp name so a case-only change lands on a case-insensitive filesystem."""
    tmp = src.with_name(dest.name + RENAME_TMP_SUFFIX)
    os.rename(src, tmp)
    os.rename(tmp, dest)


def derived_prompt(fm: dict[str, Any], fallback_title: str) -> str:
    """Read-only one-line-delta prompt for a bare `subagent` watcher without one."""
    title = str(fm.get("title") or fallback_title).strip()
    source = str(fm.get("source") or "").strip()
    where = f" at {source}" if source else ""
    return (f"Read {title}{where}; compare against the previous note for this watcher; "
            "return ONE line: the delta, or 'no change'. Read-only "
            f"{EM_DASH} never submit, send, or write.")


# ---------------------------------------------------------------------------
# Frontmatter text surgery (comments preserved)
# ---------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[list[str], int] | None:
    """(lines, index of the closing `---`) for canonical frontmatter, else None."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return None
    return lines, close


def _closing(lines: list[str]) -> int:
    return next(i for i in range(1, len(lines)) if lines[i].strip() == "---")


def _span_end(lines: list[str], start: int, end: int, indent: int) -> int:
    """Index just past the last continuation line of the entry at `start`.

    Continuation = lines indented deeper than `indent`; blank lines belong to
    the entry only when more continuation follows them.
    """
    last = start
    k = start + 1
    while k < end:
        ln = lines[k]
        if not ln.strip():
            k += 1
            continue
        if _indent(ln) > indent:
            last = k
            k += 1
            continue
        break
    return last + 1


def _find_top_key(lines: list[str], end: int, key: str) -> int | None:
    pat = re.compile(rf"^{re.escape(key)}:(\s|$)")
    return next((k for k in range(1, end) if pat.match(lines[k])), None)


def _child_indent(lines: list[str], start: int, end: int, default: int = 2) -> int:
    for k in range(start + 1, end):
        ln = lines[k]
        if ln.strip() and not _is_comment(ln):
            return _indent(ln)
    return default


def _set_child(lines: list[str], wstart: int, wend: int, indent: int, key: str,
               rendered: str, *, after: str | None = None) -> tuple[int, bool]:
    """Set `<indent><key>: <rendered>` inside the block [wstart, wend).

    An existing key keeps its inline comment (a nested block value collapses
    onto the key line); a missing key is inserted after `after` when that key
    exists, else right under the block's own line. Returns (new wend, changed).
    """
    pat = re.compile(rf"^( {{{indent}}}{re.escape(key)}:)(\s*)([^#]*?)(\s*#.*)?$")
    for k in range(wstart + 1, wend):
        m = pat.match(lines[k])
        if not m:
            continue
        span = _span_end(lines, k, wend, indent)
        new_line = f"{m.group(1)} {rendered}{m.group(4) or ''}"
        if lines[k] == new_line and span == k + 1:
            return wend, False
        lines[k:span] = [new_line]
        return wend - (span - k - 1), True
    at = wstart + 1
    if after:
        apat = re.compile(rf"^ {{{indent}}}{re.escape(after)}:(\s|$)")
        for k in range(wstart + 1, wend):
            if apat.match(lines[k]):
                at = _span_end(lines, k, wend, indent)
                break
    lines.insert(at, f"{' ' * indent}{key}: {rendered}")
    return wend + 1, True


def _watch_block(lines: list[str]) -> tuple[int, int, int] | None:
    """(watch line, block end, child indent) for the top-level `watch:` entry, or None."""
    close = _closing(lines)
    k = _find_top_key(lines, close, "watch")
    if k is None:
        return None
    end = _span_end(lines, k, close, 0)
    return k, end, _child_indent(lines, k, end)


def _expand_inline_watch(lines: list[str], watch: dict[str, Any]) -> bool:
    """`watch: {..}` (flow form) becomes block form so children can be edited."""
    block = _watch_block(lines)
    if block is None:
        return False
    k, end, _ = block
    m = re.match(r"^watch:\s*([^#]*?)(\s*#.*)?$", lines[k])
    if not m or not m.group(1).strip():
        return False
    dumped = yaml.safe_dump(watch, sort_keys=False, allow_unicode=True,
                            default_flow_style=False).rstrip("\n")
    children = [f"  {ln}" for ln in dumped.split("\n")] if watch else []
    lines[k:end] = [f"watch:{m.group(2) or ''}", *children]
    return True


def _cut_top_key(lines: list[str], key: str) -> list[str]:
    """Remove the top-level entry `key` (with its continuation lines); return the raw lines."""
    close = _closing(lines)
    k = _find_top_key(lines, close, key)
    if k is None:
        return []
    end = _span_end(lines, k, close, 0)
    raw = lines[k:end]
    del lines[k:end]
    return raw


def _append_kept_block(lines: list[str], raw: list[str]) -> None:
    """Append the cut frontmatter lines verbatim to the body inside a fenced YAML block."""
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(["", KEPT_MARKER, "```yaml", *raw, "```", ""])


def _set_ref_version(lines: list[str]) -> bool:
    close = _closing(lines)
    k = _find_top_key(lines, close, "ref_version")
    if k is None:
        lines.insert(1, f"ref_version: {REF_VERSION}")
        return True
    m = re.match(r"^(ref_version:\s*)([^#]*?)(\s*#.*)?$", lines[k])
    new_line = f"{m.group(1)}{REF_VERSION}{m.group(3) or ''}" if m else f"ref_version: {REF_VERSION}"
    if lines[k] == new_line:
        return False
    lines[k] = new_line
    return True


def _parse_lines(lines: list[str]) -> dict[str, Any]:
    fm = yaml.safe_load("\n".join(lines[1:_closing(lines)])) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a mapping")
    return fm


def frontmatter_of(path: Path) -> dict[str, Any] | None:
    """Parsed canonical frontmatter of a markdown file, or None (never raises)."""
    try:
        parsed = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if parsed is None:
        return None
    try:
        return _parse_lines(parsed[0])
    except (yaml.YAMLError, ValueError):
        return None


def convert_ref_text(text: str, stem: str = "ref") -> tuple[str, dict[str, Any] | None, str | None]:
    """Move one ref's frontmatter to schema 2 by text edit.

    Returns `(new_text, summary, blocker)`: `summary` is None when nothing had
    to change; `blocker` (non-None) means the file was left untouched and says
    why -- the user resolves it by hand and re-runs.
    """
    parsed = split_frontmatter(text)
    if parsed is None:
        return text, None, "no canonical frontmatter"
    lines, close = parsed
    try:
        fm = _parse_lines(lines)
    except yaml.YAMLError as exc:
        return text, None, f"frontmatter is not valid YAML: {exc}"
    except ValueError as exc:
        return text, None, str(exc)
    legacy = [k for k in LEGACY_TOP_KEYS if k in fm]
    unknown = [str(k) for k in fm if k not in REF_TOP_KEYS and k not in LEGACY_REF_KEYS]
    watch_raw = fm.get("watch")
    if watch_raw is not None and not isinstance(watch_raw, dict):
        return text, None, "`watch:` is not a mapping"
    watch: dict[str, Any] = dict(watch_raw or {})
    if fm.get("ref_version") == REF_VERSION and not legacy and not unknown:
        return text, None, None
    kind = str(fm.get("kind") or "")
    source = fm.get("source")
    source = "" if source is None else str(source)
    pack = watch.get("pack")
    declared = watch.get("type")
    wtype: str | None = None
    if pack is None:
        wtype = str(declared) if declared is not None else WATCH_TYPE_BY_REF_KIND.get(kind)
        if wtype is None:
            return text, None, (f"cannot derive watch.type (kind {kind!r}; no watch.type / "
                                "watch.pack) -- set one by hand, then re-run")
    body_before = lines[close:]
    summary: dict[str, Any] = {
        "dropped": {k: fm[k] for k in legacy}, "type_set": None, "moved_source_to": None,
        "watch_injected": False, "kept_in_body": [],
    }

    if _find_top_key(lines, _closing(lines), "watch") is None:
        close = _closing(lines)
        lines[close:close] = ["watch:"]
        summary["watch_injected"] = True
    elif watch_raw is not None:
        _expand_inline_watch(lines, watch)
    wstart, wend, indent = _watch_block(lines)  # type: ignore[misc]
    if pack is None:
        assert wtype is not None
        if declared is None:
            wend, _ = _set_child(lines, wstart, wend, indent, "type", wtype)
            summary["type_set"] = wtype
        locator = LOCATOR_BY_TYPE.get(wtype)
        if locator and watch.get(locator) in (None, ""):
            value = source if locator != "prompt" else derived_prompt(fm, stem)
            if value:
                wend, _ = _set_child(lines, wstart, wend, indent, locator, yaml_scalar(value),
                                     after="type")
                summary["moved_source_to"] = f"watch.{locator}"
    # Cut the retired / unknown entries. Pure fetch-model mechanics (`kind`,
    # `ttl_minutes`, `sensitive`, `chunk_for_large`, `normalized_at`, a
    # `source` that was moved or names the pack itself) are dropped -- their
    # values live on in the ledger. Anything informative (`auth_ref`, a
    # `source` nothing else carries, every key schema 2 does not know) is kept
    # verbatim in the body so the ref loads and nothing the user wrote is lost.
    kept: list[str] = []
    for key in (*legacy, *unknown):
        raw = _cut_top_key(lines, key)
        if not raw:
            continue
        keep = key in unknown
        if key == "auth_ref":
            keep = bool(str(fm.get(key) or "").strip())
        elif key == "source":
            keep = bool(source) and summary["moved_source_to"] is None \
                and not (pack is not None and source == str(pack))
        if keep:
            kept.extend(raw)
            summary["kept_in_body"].append(key)
    if kept:
        _append_kept_block(lines, kept)
    _set_ref_version(lines)

    new_text = "\n".join(lines)
    check = _parse_lines(lines)
    if check.get("ref_version") != REF_VERSION or any(k in check for k in LEGACY_REF_KEYS) \
            or any(k not in REF_TOP_KEYS for k in check):
        raise ValueError("schema-2 conversion self-check failed (keys outside schema 2 remain)")
    cw = check.get("watch")
    if not isinstance(cw, dict):
        raise ValueError("schema-2 conversion self-check failed (watch: is not a mapping)")
    if summary["moved_source_to"] and summary["moved_source_to"] != "watch.prompt" \
            and cw.get(summary["moved_source_to"].split(".", 1)[1]) != source:
        raise ValueError("schema-2 conversion self-check failed (locator mismatch)")
    body_after = lines[_closing(lines):]
    expected = list(body_before)
    if kept:
        while expected and not expected[-1].strip():
            expected.pop()
        expected += ["", KEPT_MARKER, "```yaml", *kept, "```", ""]
    if body_after != expected:
        raise ValueError("schema-2 conversion self-check failed (body changed)")
    return new_text, summary, None


def apply_simplefin_cadence(text: str) -> tuple[str, dict[str, Any] | None, bool]:
    """Set the user-directed SimpleFIN cadence on a `watch.pack: simplefin` ref.

    Returns `(new_text, previous_values, changed)`; `previous_values` is None
    when the text is not a SimpleFIN pack instance.
    """
    parsed = split_frontmatter(text)
    if parsed is None:
        return text, None, False
    lines, _close = parsed
    try:
        fm = _parse_lines(lines)
    except (yaml.YAMLError, ValueError):
        return text, None, False
    watch = fm.get("watch")
    if not isinstance(watch, dict) or watch.get("pack") != SIMPLEFIN_PACK:
        return text, None, False
    previous = {k: watch.get(k, ABSENT) for k in SIMPLEFIN_CADENCE}
    _expand_inline_watch(lines, watch)
    wstart, wend, indent = _watch_block(lines)  # type: ignore[misc]
    changed = False
    wend, c = _set_child(lines, wstart, wend, indent, "schedule",
                         SIMPLEFIN_CADENCE["schedule"], after="pack")
    changed |= c
    wend, c = _set_child(lines, wstart, wend, indent, "capture_mode",
                         SIMPLEFIN_CADENCE["capture_mode"], after="schedule")
    changed |= c
    wend, c = _set_child(lines, wstart, wend, indent, "cycles",
                         yaml_scalar(SIMPLEFIN_CADENCE["cycles"]), after="schedule")
    changed |= c
    if not changed:
        return text, previous, False
    new_text = "\n".join(lines)
    check = _parse_lines(lines).get("watch") or {}
    if any(check.get(k) != v for k, v in SIMPLEFIN_CADENCE.items()):
        raise ValueError("SimpleFIN cadence edit self-check failed")
    return new_text, previous, True


# ---------------------------------------------------------------------------
# Sidecar / stray classification
# ---------------------------------------------------------------------------

def companion_of(ref: Path) -> Path | None:
    """The document a `.ref.md` describes: `<doc>.<ext>.ref.md` (form A) or
    `<stem>.ref.md` beside `<stem>.<ext>` (form B); None for a standalone ref."""
    stem = ref.name[: -len(REF_SUFFIX)]
    full = ref.parent / stem
    if full.is_file() and not _is_ref_or_meta(full):
        return full
    siblings = [p for p in sorted(ref.parent.iterdir())
                if p.is_file() and p.name != ref.name and not _is_ref_or_meta(p) and p.stem == stem]
    return siblings[0] if siblings else None


def _source_points_at_sibling(ref_path: Path, workspace: Path, source: Any) -> Path | None:
    if not isinstance(source, str) or not source.strip():
        return None
    src = source.strip()
    parent = ref_path.parent.resolve()
    for cand in (ref_path.parent / Path(src).name, workspace / src, Path(src).expanduser()):
        try:
            if cand.is_file() and cand.resolve().parent == parent \
                    and cand.resolve() != ref_path.resolve() and not _is_ref_or_meta(cand):
                return cand
        except OSError:
            continue
    return None


def classify_sidecar(ref: Path, workspace: Path) -> tuple[Path | None, str]:
    """(target `.meta.md` path, reason) for a sidecar; (None, reason) for a stray."""
    companion = companion_of(ref)
    if companion is not None:
        return ref.with_name(companion.name + META_SUFFIX), f"sibling document {companion.name}"
    fm = frontmatter_of(ref)
    if isinstance(fm, dict):
        if str(fm.get("kind") or "") == "file":
            sib = _source_points_at_sibling(ref, workspace, fm.get("source"))
            if sib is not None:
                return ref.with_name(sib.name + META_SUFFIX), f"kind: file pointing at {sib.name}"
        if any(k in fm for k in PAYMENT_KEYS):
            return (ref.with_name(ref.name[: -len(REF_SUFFIX)] + META_SUFFIX),
                    "payment-confirmation fields; no document beside it")
    return None, "neither a document sidecar nor in the registry"


def ref_scan_roots(workspace: Path) -> list[Path]:
    """`Sources/`, `Projects/*/Sources/`, `Projects/*/Resources/` that exist."""
    roots: list[Path] = []
    if (workspace / "Sources").is_dir():
        roots.append(workspace / "Sources")
    projects = workspace / "Projects"
    if projects.is_dir():
        for proj in sorted(p for p in projects.iterdir() if p.is_dir()):
            for sub in ("Sources", "Resources"):
                if (proj / sub).is_dir():
                    roots.append(proj / sub)
    return roots


def _under_registry(path: Path, registry: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    reg = registry.resolve()
    return resolved == reg or reg in resolved.parents


def outside_refs(workspace: Path, registry: Path, *, suffix: str = REF_SUFFIX) -> list[Path]:
    """Every `<suffix>` file under the scan roots that is not in the registry or a `_cache/`."""
    found: list[Path] = []
    for root in ref_scan_roots(workspace):
        for path in sorted(root.rglob(f"*{suffix}")):
            if not path.is_file() or _under_registry(path, registry):
                continue
            if any(part == "_cache" for part in path.relative_to(root).parts):
                continue
            found.append(path)
    return found


def meta_sidecars(workspace: Path) -> list[Path]:
    return [p for root in ref_scan_roots(workspace) for p in sorted(root.rglob(f"*{META_SUFFIX}"))
            if p.is_file()]


def meta_document(meta: Path) -> Path | None:
    """The document a `.meta.md` sidecar describes (`<doc>.<ext>.meta.md`), or None."""
    doc = meta.with_name(meta.name[: -len(META_SUFFIX)])
    return doc if doc.is_file() and not _is_ref_or_meta(doc) else None


def catalogue_files(workspace: Path) -> list[Path]:
    """Every `Domains/*/sources.md` + `Projects/*/sources.md`."""
    out: list[Path] = []
    for pattern in ("Domains/*/sources.md", "Projects/*/sources.md"):
        out.extend(p for p in workspace.glob(pattern) if p.is_file())
    return sorted(out)


def _project_slug(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "Projects" else None


def _project_relative(rel_path: str, cat_rel: str) -> str | None:
    slug = _project_slug(rel_path)
    if slug and cat_rel == f"Projects/{slug}/sources.md":
        return rel_path[len(f"Projects/{slug}/"):]
    return None


def rewrite_catalogue_text(text: str, cat_rel: str, old_rel: str, new_rel: str) -> tuple[str, int]:
    """Replace `old_rel` (and its project-relative form inside that project) with `new_rel`."""
    count = text.count(old_rel)
    new_text = text.replace(old_rel, new_rel)
    proj_rel = _project_relative(old_rel, cat_rel)
    if proj_rel:
        pat = re.compile(rf"(?<![A-Za-z0-9_./-]){re.escape(proj_rel)}")
        new_text, n = pat.subn(_project_relative(new_rel, cat_rel) or new_rel, new_text)
        count += n
    return new_text, count


def rewrite_sidecar_mentions(text: str, cat_rel: str, doc_rel: str) -> tuple[str, int]:
    """On a catalogue line naming `doc_rel`, the shorthand `` `.ref.md` `` becomes `` `.meta.md` ``."""
    needles = [doc_rel]
    proj_rel = _project_relative(doc_rel, cat_rel)
    if proj_rel:
        needles.append(proj_rel)
    out: list[str] = []
    count = 0
    for ln in text.split("\n"):
        if any(n in ln for n in needles) and f"`{REF_SUFFIX}`" in ln:
            ln = ln.replace(f"`{REF_SUFFIX}`", f"`{META_SUFFIX}`")
            count += 1
        out.append(ln)
    return "\n".join(out), count


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Migration:
    """Stateful runner for the six 0.20.0 steps against one workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 skip_world: bool = False, now: dt.datetime | None = None,
                 out: Callable[[str], None] = print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else default_framework_root()
        self.dry_run = dry_run
        self.skip_world = skip_world
        self.now = now or dt.datetime.now(dt.UTC).astimezone()
        self.out = out
        self.changed: list[str] = []
        self.world_rebuilt = False
        self.config: dict[str, Any] = {}
        self.registry = self.ws / DEFAULT_WATCHLIST_PATH
        self.manifest: dict[str, Any] = {}
        self.strays: list[str] = []
        self.blocked: list[str] = []
        self.index_dirty = False

    # -- helpers -----------------------------------------------------------
    def _rel(self, path: Path) -> str:
        return path.relative_to(self.ws).as_posix()

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    def _checkpoint(self, path: Path) -> None:
        dest = self.ws / CHECKPOINT_REL / path.relative_to(self.ws)
        if dest.exists():
            return  # keep the earliest original
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    def _seeded_set(self) -> set[str]:
        marker = self.ws / CHECKPOINT_REL / SEEDED_MARKER_NAME
        if not marker.exists():
            return set()
        return {ln.strip() for ln in marker.read_text(encoding="utf-8").splitlines() if ln.strip()}

    def _record_seeded(self, rel: str) -> None:
        marker = self.ws / CHECKPOINT_REL / SEEDED_MARKER_NAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        if rel in self._seeded_set():
            return
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(f"{rel}\n")

    def _write(self, path: Path, text: str) -> None:
        self.changed.append(self._rel(path))
        if self.dry_run:
            return
        if not path.exists():
            self._record_seeded(self._rel(path))
        elif self._rel(path) not in self._seeded_set():
            self._checkpoint(path)  # a file this migration seeded has no original to keep
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _manifest_path(self) -> Path:
        return self.ws / RETIRED_REL / MOVES_MANIFEST_NAME

    def _load_manifest(self) -> None:
        path = self._manifest_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        data = data if isinstance(data, dict) else {}
        data.setdefault("schema_version", 1)
        data.setdefault("migration", TO_VERSION)
        for key in ("converted", "renamed", "sidecars", "strays", "cadence", "removed_dirs",
                    "rewritten", "index_paths"):
            data.setdefault(key, [])
        data.setdefault("state_lock_preexisting",
                        (self.ws / "_memory" / STATE_LOCK_NAME).exists())
        self.manifest = data

    def _record(self, key: str, entry: dict[str, Any], *, unique_by: str) -> None:
        rows = self.manifest.setdefault(key, [])
        if any(isinstance(r, dict) and r.get(unique_by) == entry.get(unique_by) for r in rows):
            return
        rows.append(entry)

    def _save_manifest(self) -> None:
        if self.dry_run:
            return
        path = self._manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest["applied_at"] = now_iso(self.now)
        header = (f"# Migration {TO_VERSION} move ledger -- what migrate.py converted, renamed\n"
                  "# and rewrote in this workspace. Read by revert.py; safe to delete after\n"
                  "# the migration is accepted.\n")
        path.write_text(header + yaml.safe_dump(self.manifest, sort_keys=False,
                                                allow_unicode=True), encoding="utf-8")

    def _load_config(self) -> None:
        path = self.ws / "_memory" / "config.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.config = cfg if isinstance(cfg, dict) else {}
        self.registry = self.ws / watchlist_rel_path(self.config)

    def _index_id_for(self, rel: str) -> str | None:
        idx_path = si.index_path(self.ws)
        if not idx_path.exists():
            return None
        for row in si.load_index(self.ws).get("sources") or []:
            if isinstance(row, dict) and row.get("path") == rel and row.get("id"):
                return str(row["id"])
        return None

    def _finish_interrupted_renames(self) -> None:
        """A `<name>.ref.md.0-20-0.renaming` left by a halted run is completed."""
        if not self.registry.is_dir():
            return
        names = {p.name for p in self.registry.iterdir()}
        for tmp in sorted(self.registry.glob(f"*{RENAME_TMP_SUFFIX}")):
            final = tmp.name[: -len(RENAME_TMP_SUFFIX)]
            if any(n.lower() == final.lower() for n in names):
                self._say(f"warning: {self._rel(tmp)} left by an interrupted run; "
                          f"{final} already exists -- resolve by hand")
                continue
            self._say(f"{self._rel(tmp)} -> {final} (interrupted rename completed)")
            if not self.dry_run:
                os.rename(tmp, tmp.with_name(final))

    # -- steps -------------------------------------------------------------
    def step_schema_v2(self) -> None:
        for ref in registry_refs(self.registry):
            rel = self._rel(ref)
            text = ref.read_text(encoding="utf-8")
            stem = ref.name[: -len(REF_SUFFIX)]
            new_text, summary, blocker = convert_ref_text(text, stem)
            if blocker:
                self.blocked.append(rel)
                self._say(f"keep {rel} ({blocker})")
                continue
            if summary is None:
                continue
            dropped = ", ".join(summary["dropped"]) or "nothing"
            detail = f"dropped {dropped}"
            if summary["moved_source_to"]:
                detail += f"; source -> {summary['moved_source_to']}"
            if summary["type_set"]:
                detail += f"; watch.type set to {summary['type_set']}"
            if summary["watch_injected"]:
                detail += "; watch: block added"
            if summary["kept_in_body"]:
                detail += f"; kept verbatim in the body: {', '.join(summary['kept_in_body'])}"
            self._say(f"{rel}: ref_version 1 -> {REF_VERSION} ({detail})")
            self._record("converted", {"path": rel, **summary}, unique_by="path")
            self._write(ref, new_text)
            self.index_dirty = True

    def step_title_case(self) -> None:
        pairs: list[tuple[str, str]] = []
        names = {p.name for p in self.registry.iterdir()} if self.registry.is_dir() else set()
        # A file in the registry that is not a `.ref.md` (and not the README) is
        # not a watcher: nothing loads, checks, or converts it. Say so; leave it.
        for stray in sorted(names):
            if (stray == "README.md" or stray.startswith(".")
                    or stray.lower().endswith(REF_SUFFIX.lower())
                    or not (self.registry / stray).is_file()):
                continue
            self._say(f"note: {self._rel(self.registry / stray)} is not a .ref.md -- not a "
                      "watcher; rename it to `<name>.ref.md` or move it out of the "
                      "registry (left as is)")
        for ref in registry_refs(self.registry):
            want = target_name(ref.name)
            if want == ref.name:
                continue
            rel = self._rel(ref)
            if not framework_written(frontmatter_of(ref)):
                # The user named this file (`added_by: user`, another value, or
                # none at all): its casing is respected, whatever it is.
                self._say(f"kept {rel} (user-named; casing respected)")
                continue
            if want in names:
                self._say(f"keep {rel} (rename target {want} already exists; two watchers share "
                          f"the id {id_from_stem(want[: -len(REF_SUFFIX)])!r} -- resolve by hand)")
                continue
            dest = ref.with_name(want)
            self._say(f"rename {rel} -> {self._rel(dest)} (Title_Case, framework-written ref; id "
                      f"{id_from_stem(want[: -len(REF_SUFFIX)])!r} unchanged)")
            self.changed.append(rel)
            entry: dict[str, Any] = {"from": rel, "to": self._rel(dest),
                                     "index_id": self._index_id_for(rel)}
            if not self.dry_run:
                self._checkpoint(ref)
                rename_two_step(ref, dest)
            names.discard(ref.name)
            names.add(want)
            self._record("renamed", entry, unique_by="from")
            pairs.append((rel, self._rel(dest)))
        # Index rows whose path differs from the on-disk name only by case (the
        # file was already Title_Case, or the user renamed it; the row was
        # written before) are aligned too, so `refresh` keeps their ids through
        # path identity.
        listed = set(pairs)
        for ref in registry_refs(self.registry):
            # After a real rename the listing already carries the new name; in a
            # dry run `rename_target` projects it (a user-named ref maps to itself).
            new_rel = self._rel(ref.with_name(rename_target(ref)))
            for row_path in self._index_paths_matching(new_rel):
                if (row_path, new_rel) not in listed and row_path != new_rel:
                    pairs.append((row_path, new_rel))
                    listed.add((row_path, new_rel))
                    self._record("index_paths", {"from": row_path, "to": new_rel,
                                                 "index_id": self._index_id_for(row_path)},
                                 unique_by="from")
        if pairs:
            self._rewrite_catalogues(pairs)
            self._rewrite_index(pairs)
            self.index_dirty = True
        self._report_dangling_registry_paths()

    def _index_paths_matching(self, new_rel: str) -> list[str]:
        idx_path = si.index_path(self.ws)
        if not idx_path.exists():
            return []
        return [str(row["path"]) for row in si.load_index(self.ws).get("sources") or []
                if isinstance(row, dict) and isinstance(row.get("path"), str)
                and row["path"].lower() == new_rel.lower() and row["path"] != new_rel]

    def _report_dangling_registry_paths(self) -> None:
        """Informational: catalogue rows that name a registry ref no file backs."""
        if not self.registry.is_dir():
            return
        reg_rel = self._rel(self.registry)
        on_disk = {p.name.lower() for p in self.registry.iterdir()}
        pat = re.compile(rf"{re.escape(reg_rel)}/([^\s`|()\[\]]+{re.escape(REF_SUFFIX)})")
        for cat in catalogue_files(self.ws):
            for m in pat.finditer(cat.read_text(encoding="utf-8")):
                name = m.group(1)
                if name.lower() not in on_disk:
                    self._say(f"note: {self._rel(cat)} names {reg_rel}/{name}, which does not "
                              "exist (left as is)")

    def step_sidecars(self) -> None:
        pairs: list[tuple[str, str]] = []
        docs: list[str] = []
        for ref in outside_refs(self.ws, self.registry):
            rel = self._rel(ref)
            target, reason = classify_sidecar(ref, self.ws)
            if target is None:
                self.strays.append(rel)
                self._say(f"stray {rel} ({reason}; move it into {self._rel(self.registry)}/ as a "
                          f"watcher or rename it to {META_SUFFIX} by hand)")
                self._record("strays", {"path": rel, "reason": reason}, unique_by="path")
                continue
            if target.exists():
                self._say(f"keep {rel} (target {self._rel(target)} already exists; resolve by hand)")
                self.strays.append(rel)
                continue
            self._say(f"rename {rel} -> {self._rel(target)} (sidecar: {reason})")
            self.changed.append(rel)
            entry: dict[str, Any] = {"from": rel, "to": self._rel(target), "reason": reason,
                                     "index_id": self._index_id_for(rel)}
            doc = meta_document(target)  # the document exists whether or not the rename ran
            if not self.dry_run:
                self._checkpoint(ref)
                os.rename(ref, target)
            if doc is not None:
                entry["document"] = self._rel(doc)
                docs.append(self._rel(doc))
            self._record("sidecars", entry, unique_by="from")
            pairs.append((rel, self._rel(target)))
        for txt in outside_refs(self.ws, self.registry, suffix=LEGACY_TXT_SUFFIX):
            self._say(f"note: {self._rel(txt)} is a `{LEGACY_TXT_SUFFIX}` (not recognized since "
                      f"{TO_VERSION}); convert it to a watcher or a {META_SUFFIX} by hand")
        if not pairs:
            return
        self._rewrite_catalogues(pairs, sidecar_docs=docs)
        self._rewrite_index(pairs)
        self.index_dirty = True

    def _rewrite_catalogues(self, pairs: list[tuple[str, str]],
                            sidecar_docs: list[str] | None = None) -> None:
        for cat in catalogue_files(self.ws):
            cat_rel = self._rel(cat)
            text = cat.read_text(encoding="utf-8")
            new_text, total = text, 0
            for old_rel, new_rel in pairs:
                new_text, n = rewrite_catalogue_text(new_text, cat_rel, old_rel, new_rel)
                total += n
            for doc_rel in sidecar_docs or []:
                new_text, n = rewrite_sidecar_mentions(new_text, cat_rel, doc_rel)
                total += n
            if total and new_text != text:
                self._say(f"{cat_rel}: {total} path(s) rewritten")
                prior = next((r for r in self.manifest["rewritten"] if r.get("path") == cat_rel), None)
                if prior:
                    prior["replacements"] = int(prior.get("replacements") or 0) + total
                else:
                    self._record("rewritten", {"path": cat_rel, "replacements": total},
                                 unique_by="path")
                self._write(cat, new_text)

    def _rewrite_index(self, pairs: list[tuple[str, str]]) -> None:
        idx_path = si.index_path(self.ws)
        if not idx_path.exists():
            return
        index = si.load_index(self.ws)
        by_old = dict(pairs)
        touched = 0
        for row in index.get("sources") or []:
            if not isinstance(row, dict) or row.get("path") not in by_old:
                continue
            # Path updated IN PLACE; the row keeps its id (0.19.0's path-identity
            # rule in sources_index.refresh keeps it through the next refresh).
            row["path"] = by_old[row["path"]]
            touched += 1
        if not touched:
            return
        self._say(f"_memory/sources-index.yaml: {touched} row path(s) rewritten (ids kept)")
        self.changed.append(self._rel(idx_path))
        if not self.dry_run:
            self._checkpoint(idx_path)
            si.save_index(self.ws, index)

    def step_simplefin_cadence(self) -> None:
        for ref in registry_refs(self.registry):
            text = ref.read_text(encoding="utf-8")
            try:
                new_text, previous, changed = apply_simplefin_cadence(text)
            except ValueError as exc:
                raise ValueError(f"{self._rel(ref)}: {exc}") from exc
            if previous is None or not changed:
                continue
            rel = self._rel(ref)
            self._say(f"{rel}: cadence -> schedule daily, capture_mode automatic, cycles "
                      f"[daily-update] (user-directed; was schedule {previous['schedule']!r}, "
                      f"capture_mode {previous['capture_mode']!r}, cycles {previous['cycles']!r})")
            self._record("cadence", {"path": rel, "previous": previous,
                                     "applied": dict(SIMPLEFIN_CADENCE)}, unique_by="path")
            self._write(ref, new_text)
            self.index_dirty = True

    def step_cache_dir(self) -> None:
        cache = self.ws / CACHE_REL
        prefs = self.config.get("preferences") if isinstance(self.config.get("preferences"), dict) else {}
        srcs = prefs.get("sources") if isinstance(prefs, dict) else None
        if isinstance(srcs, dict) and any(str(k).startswith("cache_") for k in srcs):
            self._say("config.yaml: preferences.sources.cache_* left in place (inert since "
                      f"{TO_VERSION}; nothing reads it)")
        if not cache.is_dir():
            return
        entries = [p for p in cache.iterdir() if p.name != ".DS_Store"]
        if entries:
            self._say(f"keep {CACHE_REL}/ ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'}; "
                      "not empty -- remove by hand if unwanted)")
            return
        self._say(f"remove {CACHE_REL}/ (empty; the fetch cache is retired)")
        self.changed.append(CACHE_REL + "/")
        self._record("removed_dirs", {"path": CACHE_REL + "/"}, unique_by="path")
        if not self.dry_run:
            for junk in cache.iterdir():
                junk.unlink()
            cache.rmdir()

    def step_index_refresh(self) -> None:
        if not self.index_dirty:
            return
        if self.dry_run:
            self._say("_memory/sources-index.yaml: would refresh (derived)")
            return
        idx_path = si.index_path(self.ws)
        if idx_path.exists():
            self._checkpoint(idx_path)
        si.refresh(self.ws, force=True, warn=lambda m: self._say(f"index: {m}"))
        self._say("_memory/sources-index.yaml: refreshed (derived)")

    def step_world(self) -> None:
        if self.skip_world:
            return
        cmd = [sys.executable, "-m", "superagent.tools.world", "--workspace", str(self.ws), "rebuild"]
        if self.dry_run:
            self._say("world.yaml: would run `uv run python -m superagent.tools.world rebuild`")
            return
        world = self.ws / "_memory" / "world.yaml"
        if world.exists():
            self._checkpoint(world)  # derived, but revert restores the pre-migration bytes
        try:
            res = subprocess.run(cmd, cwd=self.framework.parent, capture_output=True,
                                 text=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env-specific
            self._say(f"warning: world rebuild failed to start ({exc}); derived data, skipping")
            return
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["(no output)"]
            self._say(f"warning: world rebuild exited {res.returncode}: {tail[0]}")
            return
        self.world_rebuilt = True
        self._say("world.yaml: rebuilt (derived)")

    def step_version(self) -> None:
        from superagent.tools.version import set_workspace_version, workspace_version
        current = workspace_version(self.ws)
        if current == TO_VERSION:
            return
        self._say(f".version: {current} -> {TO_VERSION}")
        self.changed.append(".version")
        if not self.dry_run:
            set_workspace_version(self.ws, TO_VERSION)

    def run(self) -> int:
        """Run pre-flight + all steps. Returns a process exit code."""
        if not self.ws.is_dir():
            self.out(f"error: workspace not found at {self.ws}")
            return 1
        for rel in PREFLIGHT_YAML:
            path = self.ws / "_memory" / rel
            if not path.exists():
                continue
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.out(f"error: _memory/{rel} is not well-formed YAML: {exc}")
                return 1
        try:
            self._load_config()
            self._load_manifest()
            self._finish_interrupted_renames()
        except (OSError, yaml.YAMLError) as exc:
            self.out(f"error: pre-flight failed: {exc}")
            return 1
        steps = (self.step_schema_v2, self.step_title_case, self.step_sidecars,
                 self.step_simplefin_cadence, self.step_cache_dir, self.step_index_refresh,
                 self.step_world, self.step_version)
        for step in steps:
            try:
                step()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.out(f"error in {step.__name__}: {exc}")
                self.out("halted; nothing after this step was applied. "
                         "Run revert.py to restore checkpointed files.")
                self._save_manifest()
                return 1
        if self.changed:
            self._save_manifest()
            n = len(dict.fromkeys(self.changed))
            self._say(f"{n} file(s) {'would be ' if self.dry_run else ''}changed")
        else:
            suffix = " (world.yaml re-derived)" if self.world_rebuilt else ""
            self._say(f"nothing to do; workspace already at {TO_VERSION} shape{suffix}")
        if self.blocked:
            self._say(f"refs left at ref_version 1 (fix by hand, re-run): {', '.join(self.blocked)}")
        if self.strays:
            self._say(f"stray .ref.md outside the registry (not moved): {', '.join(self.strays)}")
        return 0


def run_migration(workspace: Path, **kwargs: Any) -> int:
    """Convenience wrapper: `Migration(workspace, **kwargs).run()`."""
    return Migration(workspace, **kwargs).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"migrate-{TO_VERSION}",
        description=f"Apply the {TO_VERSION} workspace migration (idempotent).")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Path to the workspace root (folder holding _memory/).")
    parser.add_argument("--framework", type=Path, default=None,
                        help="Path to superagent/ (defaults to this script's tree).")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--skip-world", action="store_true",
                        help="Skip the world.yaml rebuild step.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_migration(args.workspace, framework=args.framework,
                         dry_run=args.dry_run, skip_world=args.skip_world)


if __name__ == "__main__":
    sys.exit(main())
