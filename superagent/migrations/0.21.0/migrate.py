#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""0.21.0 migration helper (from 0.20.1) -- framework prune.

Canonical instructions live in ``superagent/migrations/0.21.0.md``; this
script is the executable form of its ``## Migrate`` steps. Every step is
idempotent and safe on a workspace where its condition does not apply.

Steps (in order):

1. Every rendered workbook directly inside a ``Domains/<Name>/`` folder --
   ``<name>.xlsx`` whose stem is the folder name lowercased, or any
   ``<slug>.xlsx`` that has a ``<slug>.xlsx.meta.yaml`` render-cache sidecar
   beside it -- moves, together with that sidecar (an orphan sidecar too), to
   ``_memory/_checkpoints/0.21.0/<same relative path>``. A spreadsheet the
   user put there (no sidecar, not domain-named) is reported and left alone.
2. The inbox-triage decision log (``_memory/inbox-log.yaml`` and the
   pre-0.17.0 ``Inbox/_processed.yaml``, whichever exist) moves the same way.
   Nothing else under ``Inbox/`` is touched.
3. ``config.yaml`` is NOT edited: ``preferences.workbooks`` and
   ``preferences.inbox_triage`` become inert and are reported as such.
4. ``.version`` -> 0.21.0.

Every move is recorded in ``_memory/_retired/0.21.0-moves.yaml`` (path, kind,
size, sha256); a successful ``validate.py`` relocates the checkpoint folder to
``_memory/_retired/0.21.0-originals/``; ``revert.py`` moves everything back.

Usage::

    uv run python superagent/migrations/0.21.0/migrate.py --workspace <path> [--dry-run]

Exit codes: 0 success (or nothing to do), 1 runtime error, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:  # allow `uv run python <this file>`
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

TO_VERSION = "0.21.0"
FROM_VERSION = "0.20.1"
CHECKPOINT_REL = Path("_memory") / "_checkpoints" / TO_VERSION
RETIRED_REL = Path("_memory") / "_retired"
MOVES_MANIFEST_NAME = f"{TO_VERSION}-moves.yaml"
# Where validate.py parks the checkpoint folder once every check has passed.
ORIGINALS_REL = RETIRED_REL / f"{TO_VERSION}-originals"
WORKBOOK_SUFFIX = ".xlsx"
# The retired render_workbooks tool wrote this mtime / config-signature cache
# beside every workbook it produced; its presence marks a rendered file.
WORKBOOK_META_SUFFIX = ".xlsx.meta.yaml"
# Inbox-triage decision log (0.17.0+) and its pre-0.17.0 location.
INBOX_STATE_RELS = ("_memory/inbox-log.yaml", "Inbox/_processed.yaml")
# Preference blocks nothing reads any more (left in place, reported).
INERT_PREFS = ("workbooks", "inbox_triage")
KIND_WORKBOOK = "workbook"
KIND_WORKBOOK_META = "workbook_meta"
KIND_INBOX_STATE = "inbox_state"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC).astimezone()).replace(microsecond=0).isoformat()


def default_framework_root() -> Path:
    """`superagent/` as resolved from this script's location."""
    return Path(__file__).resolve().parents[2]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def domain_dirs(workspace: Path) -> list[Path]:
    """Every folder directly under `Domains/` (dot- and underscore-prefixed names skipped)."""
    root = workspace / "Domains"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")))


def classify_workbooks(workspace: Path) -> tuple[list[tuple[Path, str, str]], list[Path]]:
    """Split the `.xlsx` (+ sidecar) files directly inside domain folders.

    Returns `(rendered, strays)`: `rendered` rows are `(path, kind, reason)`
    for every file the migration moves -- domain workbooks, per-entity
    workbooks (sidecar present), and every `.xlsx.meta.yaml` sidecar; `strays`
    are user spreadsheets (no sidecar, not domain-named) that stay put.
    """
    rendered: list[tuple[Path, str, str]] = []
    strays: list[Path] = []
    for folder in domain_dirs(workspace):
        files = sorted(p for p in folder.iterdir() if p.is_file())
        names = {p.name for p in files}
        for path in files:
            if path.name.endswith(WORKBOOK_META_SUFFIX):
                rendered.append((path, KIND_WORKBOOK_META, "render-cache sidecar"))
                continue
            if not path.name.endswith(WORKBOOK_SUFFIX):
                continue
            stem = path.name[: -len(WORKBOOK_SUFFIX)]
            if stem.lower() == folder.name.lower():
                rendered.append((path, KIND_WORKBOOK, "domain workbook"))
            elif path.name + ".meta.yaml" in names:
                rendered.append((path, KIND_WORKBOOK, "per-entity workbook; render-cache sidecar present"))
            else:
                strays.append(path)
    return rendered, strays


def inbox_state_files(workspace: Path) -> list[Path]:
    return [workspace / rel for rel in INBOX_STATE_RELS if (workspace / rel).is_file()]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Migration:
    """Stateful runner for the four 0.21.0 steps against one workspace."""

    def __init__(self, workspace: Path, *, framework: Path | None = None, dry_run: bool = False,
                 now: dt.datetime | None = None, out: Callable[[str], None] = print) -> None:
        self.ws = Path(workspace)
        self.framework = Path(framework) if framework else default_framework_root()
        self.dry_run = dry_run
        self.now = now or dt.datetime.now(dt.UTC).astimezone()
        self.out = out
        self.changed: list[str] = []
        self.config: dict[str, Any] = {}
        self.manifest: dict[str, Any] = {}
        self.strays: list[str] = []
        self.blocked: list[str] = []

    # -- helpers -----------------------------------------------------------
    def _rel(self, path: Path) -> str:
        return path.relative_to(self.ws).as_posix()

    def _say(self, msg: str) -> None:
        self.out(("[dry-run] " if self.dry_run else "") + msg)

    def _manifest_path(self) -> Path:
        return self.ws / RETIRED_REL / MOVES_MANIFEST_NAME

    def _load_manifest(self) -> None:
        path = self._manifest_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        data = data if isinstance(data, dict) else {}
        data.setdefault("schema_version", 1)
        data.setdefault("migration", TO_VERSION)
        data.setdefault("moved", [])
        data.setdefault("kept", [])
        self.manifest = data

    def _record(self, key: str, entry: dict[str, Any], *, unique_by: str = "path") -> None:
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
        header = (f"# Migration {TO_VERSION} move ledger -- what migrate.py moved out of this\n"
                  "# workspace (rendered workbooks + inbox-triage state). Read by revert.py;\n"
                  "# safe to delete after the migration is accepted.\n")
        path.write_text(header + yaml.safe_dump(self.manifest, sort_keys=False,
                                                allow_unicode=True), encoding="utf-8")

    def _load_config(self) -> None:
        path = self.ws / "_memory" / "config.yaml"
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.config = cfg if isinstance(cfg, dict) else {}

    def _move_to_store(self, path: Path, kind: str, reason: str) -> None:
        """Move `path` to the checkpoint store at the same relative path; record it."""
        rel = self._rel(path)
        dest = self.ws / CHECKPOINT_REL / rel
        if dest.exists():
            # A halted earlier run already parked an original here; the live
            # file is a second copy. Never overwrite the earliest original.
            self.blocked.append(rel)
            self._say(f"keep {rel} ({CHECKPOINT_REL.as_posix()}/{rel} already exists from an "
                      "earlier run; resolve by hand)")
            return
        self._say(f"retire {rel} -> {CHECKPOINT_REL.as_posix()}/{rel} ({reason})")
        self.changed.append(rel)
        entry = {"path": rel, "kind": kind, "reason": reason,
                 "size": path.stat().st_size, "sha256": sha256_of(path)}
        if not self.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
        self._record("moved", entry)

    # -- steps -------------------------------------------------------------
    def step_workbooks(self) -> None:
        rendered, strays = classify_workbooks(self.ws)
        for path, kind, reason in rendered:
            self._move_to_store(path, kind, reason)
        for path in strays:
            rel = self._rel(path)
            self.strays.append(rel)
            self._say(f"note: {rel} is not a rendered workbook (no render-cache sidecar, not "
                      "domain-named); left as is")
            self._record("kept", {"path": rel, "reason": "user spreadsheet"})

    def step_inbox_state(self) -> None:
        for path in inbox_state_files(self.ws):
            self._move_to_store(path, KIND_INBOX_STATE, "inbox-triage decision log (tool retired)")

    def step_config_note(self) -> None:
        prefs = self.config.get("preferences")
        if not isinstance(prefs, dict):
            return
        inert = [k for k in INERT_PREFS if k in prefs]
        if inert:
            self._say("config.yaml: preferences." + " / preferences.".join(inert)
                      + f" left in place (inert since {TO_VERSION}; nothing reads them)")

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
        cfg_path = self.ws / "_memory" / "config.yaml"
        if cfg_path.exists():
            try:
                yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.out(f"error: _memory/config.yaml is not well-formed YAML: {exc}")
                return 1
        try:
            self._load_config()
            self._load_manifest()
        except (OSError, yaml.YAMLError) as exc:
            self.out(f"error: pre-flight failed: {exc}")
            return 1
        steps = (self.step_workbooks, self.step_inbox_state, self.step_config_note,
                 self.step_version)
        for step in steps:
            try:
                step()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.out(f"error in {step.__name__}: {exc}")
                self.out("halted; nothing after this step was applied. "
                         "Run revert.py to move parked files back.")
                self._save_manifest()
                return 1
        if self.changed:
            self._save_manifest()
            n = len(dict.fromkeys(self.changed))
            self._say(f"{n} file(s) {'would be ' if self.dry_run else ''}changed")
        else:
            self._say(f"nothing to do; workspace already at {TO_VERSION} shape")
        if self.blocked:
            self._say(f"files left in place (resolve by hand, re-run): {', '.join(self.blocked)}")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_migration(args.workspace, framework=args.framework, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
