#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Framework + workspace version inspection and migration-chain resolution.

Backs the `migrate` skill. Implements `contracts/versioning.md` § 5.

This module is read-only against migration files (it parses them to build
the chain) and read/write against `workspace/.version` (the only file it
mutates). It does NOT apply migrations — that is the skill's job, driven
by reading the migration `.md` step-by-step.

Public API:
    current_version() -> str
    workspace_version(workspace) -> str
    set_workspace_version(workspace, version) -> None
    parse(s) -> Version
    compare(a, b) -> int
    bump_kind(from_v, to_v) -> Literal["major", "minor", "patch", "same"]
    find_chain(from_v, to_v, manifest_path=None) -> list[MigrationEntry]
    refresh_manifest(migrations_dir=None) -> int

CLI (all `uv run python -m superagent.tools.version <cmd>`):
    current
    workspace [--workspace PATH]
    check     [--workspace PATH]
    chain     [--workspace PATH]
    set       [--workspace PATH] VERSION
    refresh-manifest
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import datetime as dt
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
MIGRATIONS_DIR = REPO_ROOT / "superagent" / "migrations"
MANIFEST_FILE = MIGRATIONS_DIR / "_manifest.yaml"
DEFAULT_WORKSPACE = REPO_ROOT / "workspace"

# Default workspace version when `.version` is missing — the version
# before this contract existed (per `contracts/versioning.md` § 2).
LEGACY_DEFAULT = "0.1.0"

# ---------------------------------------------------------------------------
# Version dataclass + parsing
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dc.dataclass(frozen=True, order=True)
class Version:
    """Parsed semver triple. Orders naturally by (major, minor, patch)."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse(s: str | Version) -> Version:
    """Parse a `MAJOR.MINOR.PATCH` string into a `Version`.

    Pre-release / build identifiers (e.g. `1.0.0-rc.1`) are explicitly
    rejected per `contracts/versioning.md` § 8.
    """
    if isinstance(s, Version):
        return s
    if not isinstance(s, str):
        raise TypeError(f"version must be str or Version, got {type(s).__name__}")
    raw = s.strip()
    m = _SEMVER_RE.match(raw)
    if not m:
        raise ValueError(
            f"not a valid MAJOR.MINOR.PATCH version: {raw!r} "
            "(pre-release identifiers like '-rc.1' are not yet supported)"
        )
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def compare(a: str | Version, b: str | Version) -> int:
    """Return -1 / 0 / +1 for `a vs b`."""
    pa, pb = parse(a), parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


BumpKind = Literal["major", "minor", "patch", "same"]


def bump_kind(from_v: str | Version, to_v: str | Version) -> BumpKind:
    """Classify the bump from `from_v` to `to_v`.

    Returns `"same"` when versions are equal. Returns the highest-rank
    component that changed (so 0.1.0 -> 0.2.5 is `"minor"`, not `"patch"`).
    Raises if `to_v < from_v` (downgrade).
    """
    pa, pb = parse(from_v), parse(to_v)
    if pa == pb:
        return "same"
    if pb < pa:
        raise ValueError(f"downgrade not supported: {pa} -> {pb}")
    if pb.major != pa.major:
        return "major"
    if pb.minor != pa.minor:
        return "minor"
    return "patch"


# ---------------------------------------------------------------------------
# Framework version (pyproject.toml)
# ---------------------------------------------------------------------------


def current_version(pyproject: Path | None = None) -> str:
    """Read the framework's authoritative version from `pyproject.toml`."""
    path = pyproject or PYPROJECT
    if not path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        version = data["project"]["version"]
    except KeyError as exc:
        raise KeyError(f"pyproject.toml missing project.version: {path}") from exc
    parse(version)  # validate shape early
    return version


# ---------------------------------------------------------------------------
# Workspace .version file
# ---------------------------------------------------------------------------


def _resolve_workspace(workspace: Path | None) -> Path:
    return Path(workspace) if workspace else DEFAULT_WORKSPACE


def workspace_version_path(workspace: Path | None = None) -> Path:
    """Return the absolute path to `<workspace>/.version`."""
    return _resolve_workspace(workspace) / ".version"


def workspace_version(workspace: Path | None = None) -> str:
    """Read `workspace/.version`. Returns `LEGACY_DEFAULT` when missing.

    When the workspace itself doesn't exist, returns `LEGACY_DEFAULT` —
    an absent workspace is logically the same as a pre-tracking one.
    """
    path = workspace_version_path(workspace)
    if not path.exists():
        return LEGACY_DEFAULT
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return LEGACY_DEFAULT
    parse(text)  # validate shape; raises on garbage
    return text


def set_workspace_version(workspace: Path | None, version: str) -> Path:
    """Write `<workspace>/.version` with a single line and trailing newline.

    Validates the version string's shape before writing. Creates the
    workspace directory if it doesn't already exist (caller's responsibility
    to ensure that's intended).
    """
    parse(version)
    ws = _resolve_workspace(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / ".version"
    path.write_text(f"{version}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Migration manifest + chain resolution
# ---------------------------------------------------------------------------


@dc.dataclass(frozen=True)
class MigrationEntry:
    """One row from `superagent/migrations/_manifest.yaml`."""

    to_version: str
    from_version: str
    file: str
    title: str
    breaking: bool
    revertible: bool
    estimated_duration: str

    @property
    def to_v(self) -> Version:
        return parse(self.to_version)

    @property
    def from_v(self) -> Version:
        return parse(self.from_version)


def _load_manifest(manifest_path: Path | None = None) -> list[MigrationEntry]:
    """Load the manifest into a sorted list of `MigrationEntry`."""
    path = manifest_path or MANIFEST_FILE
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = [
        MigrationEntry(
            to_version=row["to_version"],
            from_version=row["from_version"],
            file=row["file"],
            title=row["title"],
            breaking=bool(row.get("breaking", False)),
            revertible=bool(row.get("revertible", True)),
            estimated_duration=row.get("estimated_duration", ""),
        )
        for row in (raw.get("migrations") or [])
    ]
    entries.sort(key=lambda e: e.to_v)
    return entries


def find_chain(
    from_v: str | Version,
    to_v: str | Version,
    manifest_path: Path | None = None,
) -> list[MigrationEntry]:
    """Return ordered migrations to apply going from `from_v` to `to_v`.

    Returns `[]` for same-version or PATCH-only differences (the skill
    handles PATCH-only via silent `.version` bump per
    `contracts/versioning.md` § 4 step 8).

    Raises:
        ValueError: when `to_v < from_v` (downgrade — skill handles
            separately) or when the chain is broken (a required step is
            missing from the manifest).
    """
    fv = parse(from_v)
    tv = parse(to_v)
    if tv == fv:
        return []
    if tv < fv:
        raise ValueError(f"downgrade not a forward chain: {fv} -> {tv}")
    kind = bump_kind(fv, tv)
    if kind == "patch":
        return []

    entries = _load_manifest(manifest_path)
    candidates = [e for e in entries if fv < e.to_v <= tv]

    if not candidates:
        raise ValueError(
            f"no migrations found in manifest for chain {fv} -> {tv}; "
            "did you forget to author one or to refresh the manifest?"
        )

    chain: list[MigrationEntry] = []
    cursor = fv
    for entry in candidates:
        if entry.from_v != cursor:
            raise ValueError(
                f"chain broken at {entry.to_version}: expected from_version "
                f"{cursor}, got {entry.from_version} (manifest "
                "out-of-order or missing intermediate migration)"
            )
        chain.append(entry)
        cursor = entry.to_v

    if cursor != tv:
        raise ValueError(
            f"chain ends at {cursor}, not target {tv} "
            "(missing migration to bridge the gap)"
        )
    return chain


# ---------------------------------------------------------------------------
# Manifest refresh (rebuild from .md frontmatter)
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"migration file missing YAML frontmatter: {md_path}")
    data = yaml.safe_load(m.group(1)) or {}
    if not isinstance(data, dict):
        raise ValueError(f"frontmatter is not a mapping: {md_path}")
    return data


def refresh_manifest(migrations_dir: Path | None = None) -> int:
    """Rebuild `_manifest.yaml` from the `.md` files in the directory.

    Skips `_template.md` and any file whose `to_version` is `0.0.0`
    (the placeholder in the template). Returns the number of entries
    written.
    """
    md_dir = migrations_dir or MIGRATIONS_DIR
    md_files = sorted(p for p in md_dir.glob("*.md") if p.name not in {"README.md", "_template.md"})

    rows: list[dict] = []
    for md in md_files:
        fm = _parse_frontmatter(md)
        to_v = str(fm.get("to_version", "")).strip()
        from_v = str(fm.get("from_version", "")).strip()
        if to_v == "0.0.0" or from_v == "0.0.0":
            continue
        parse(to_v)
        parse(from_v)
        rows.append({
            "to_version": to_v,
            "from_version": from_v,
            "file": md.name,
            "title": fm.get("title", ""),
            "breaking": bool(fm.get("breaking", False)),
            "revertible": bool(fm.get("revertible", True)),
            "estimated_duration": fm.get("estimated_duration", ""),
        })
    rows.sort(key=lambda r: parse(r["to_version"]))

    manifest = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "migrations": rows,
    }
    header = (
        "# [Do not change manually - managed by superagent/tools/version.py refresh-manifest]\n"
        "#\n"
        "# Ordered registry of migration files under `superagent/migrations/`.\n"
        "# Single source of truth for the `migrate` skill's chain resolution.\n"
        "#\n"
        "# Citation form: `migrations/<to_version>.md`.\n"
    )
    body = yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
    out = md_dir / "_manifest.yaml"
    out.write_text(header + body, encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_current(_args: argparse.Namespace) -> int:
    print(current_version())
    return 0


def _cmd_workspace(args: argparse.Namespace) -> int:
    print(workspace_version(args.workspace))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    cur = current_version()
    ws = workspace_version(args.workspace)
    cmp = compare(ws, cur)
    if cmp == 0:
        print(f"up-to-date: workspace and framework both at {cur}")
        return 0
    if cmp > 0:
        print(
            f"downgrade scenario: workspace is {ws}, framework is {cur}; "
            "see `contracts/versioning.md` § 4.2",
            file=sys.stderr,
        )
        return 2
    kind = bump_kind(ws, cur)
    if kind == "patch":
        print(
            f"PATCH-only mismatch: workspace {ws} -> framework {cur}; "
            "run the migrate skill to silently advance .version"
        )
        return 1
    chain = find_chain(ws, cur)
    print(
        f"migration needed: workspace {ws} -> framework {cur} "
        f"({len(chain)} {kind} step{'s' if len(chain) != 1 else ''})"
    )
    return 1


def _cmd_chain(args: argparse.Namespace) -> int:
    cur = current_version()
    ws = workspace_version(args.workspace)
    if compare(ws, cur) >= 0:
        print("no chain to apply")
        return 0
    chain = find_chain(ws, cur)
    if not chain:
        print("no chain to apply (PATCH-only or same version)")
        return 0
    print(f"chain: {ws} -> {cur} ({len(chain)} step{'s' if len(chain) != 1 else ''})")
    for entry in chain:
        flag = " [breaking]" if entry.breaking else ""
        rev = "" if entry.revertible else " [NOT revertible]"
        print(f"  {entry.from_version} -> {entry.to_version}: {entry.title}{flag}{rev}")
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    path = set_workspace_version(args.workspace, args.version)
    print(f"wrote {path}: {args.version}")
    return 0


def _cmd_refresh_manifest(_args: argparse.Namespace) -> int:
    n = refresh_manifest()
    print(f"refreshed {MANIFEST_FILE.relative_to(REPO_ROOT)}: {n} entries")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="version", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("current", help="Print the framework's current version.")

    p_ws = sub.add_parser("workspace", help="Print the workspace's .version.")
    p_ws.add_argument("--workspace", type=Path, default=None)

    p_check = sub.add_parser(
        "check", help="Compare workspace vs framework. Exit: 0 match, 1 migrate needed, 2 downgrade."
    )
    p_check.add_argument("--workspace", type=Path, default=None)

    p_chain = sub.add_parser("chain", help="Show the migration chain that would be applied.")
    p_chain.add_argument("--workspace", type=Path, default=None)

    p_set = sub.add_parser("set", help="Write .version directly (admin escape hatch).")
    p_set.add_argument("--workspace", type=Path, default=None)
    p_set.add_argument("version", type=str)

    sub.add_parser("refresh-manifest", help="Rebuild superagent/migrations/_manifest.yaml.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    handlers = {
        "current": _cmd_current,
        "workspace": _cmd_workspace,
        "check": _cmd_check,
        "chain": _cmd_chain,
        "set": _cmd_set,
        "refresh-manifest": _cmd_refresh_manifest,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
