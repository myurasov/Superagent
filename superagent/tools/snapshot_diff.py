#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Compute diffs between two `_memory/_checkpoints/<date>/` snapshots.

Implements superagent/docs/_internal/ideas-better-structure.md item #6 + superagent/docs/_internal/perf-improvement-ideas.md QW-5
(provides the "what changed since" query that makes pre-rendered briefings
intelligible).

Output: a markdown report listing per-file changes (rows added / modified /
removed), new entities, status flips on existing entities, and a summary
header.

Usage:
  uv run python -m superagent.tools.snapshot_diff --since 2026-04-21 [--until 2026-04-28]
  uv run python -m superagent.tools.snapshot_diff --weekly
  uv run python -m superagent.tools.snapshot_diff --monthly
  uv run python -m superagent.tools.snapshot_diff --from <date> --to <date>

When checkpoint folders are missing, falls through to comparing live `_memory/`
against the latest checkpoint.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def find_checkpoint(workspace: Path, day: dt.date) -> Path | None:
    """Return the checkpoint folder for `day`, or None if absent."""
    p = workspace / "_memory" / "_checkpoints" / day.strftime("%Y-%m-%d")
    return p if p.exists() else None


def latest_checkpoint(workspace: Path) -> Path | None:
    """Return the most recent checkpoint folder, or None."""
    root = workspace / "_memory" / "_checkpoints"
    if not root.exists():
        return None
    candidates = sorted(p for p in root.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def collect_yaml_files(memory_root: Path) -> dict[str, Path]:
    """Return {filename: path} for every *.yaml directly under memory_root."""
    if not memory_root.exists():
        return {}
    return {p.name: p for p in memory_root.glob("*.yaml")}


def row_id(row: dict[str, Any]) -> str | None:
    """Pick the primary id from a row."""
    for k in ("id", "name", "title", "handle", "external_id"):
        v = row.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def index_rows(data: Any, list_key_hint: str | None = None) -> dict[str, dict[str, Any]]:
    """Return {row_id: row} for the list-of-rows in a YAML doc."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        if not isinstance(value, list):
            continue
        if list_key_hint and key != list_key_hint:
            continue
        for row in value:
            if not isinstance(row, dict):
                continue
            rid = row_id(row)
            if rid:
                out[f"{key}:{rid}"] = row
    return out


def diff_files(old_root: Path, new_root: Path) -> list[dict[str, Any]]:
    """Return per-file diff rows between two memory roots."""
    old_files = collect_yaml_files(old_root)
    new_files = collect_yaml_files(new_root)
    all_names = sorted(set(old_files) | set(new_files))
    diffs: list[dict[str, Any]] = []
    for name in all_names:
        diff_row: dict[str, Any] = {"file": name}
        if name not in old_files:
            diff_row["change"] = "added_file"
            new_data = load_yaml(new_files[name])
            new_idx = index_rows(new_data)
            diff_row["added_count"] = len(new_idx)
            diffs.append(diff_row)
            continue
        if name not in new_files:
            diff_row["change"] = "removed_file"
            old_data = load_yaml(old_files[name])
            old_idx = index_rows(old_data)
            diff_row["removed_count"] = len(old_idx)
            diffs.append(diff_row)
            continue
        old_data = load_yaml(old_files[name])
        new_data = load_yaml(new_files[name])
        old_idx = index_rows(old_data)
        new_idx = index_rows(new_data)
        added = sorted(set(new_idx) - set(old_idx))
        removed = sorted(set(old_idx) - set(new_idx))
        modified: list[tuple[str, list[str]]] = []
        for key in sorted(set(new_idx) & set(old_idx)):
            new_row = new_idx[key]
            old_row = old_idx[key]
            changed_fields = sorted(
                f for f in (set(new_row) | set(old_row))
                if new_row.get(f) != old_row.get(f)
            )
            if changed_fields:
                modified.append((key, changed_fields))
        if not (added or removed or modified):
            continue
        diff_row.update({
            "change": "modified",
            "added": added,
            "removed": removed,
            "modified": [{"row": k, "fields": fs} for k, fs in modified],
        })
        diffs.append(diff_row)
    return diffs


def status_flips(diffs: list[dict[str, Any]],
                 old_root: Path, new_root: Path) -> list[dict[str, Any]]:
    """For modified rows, report changes specifically to a `status` field."""
    flips: list[dict[str, Any]] = []
    for d in diffs:
        if d.get("change") != "modified":
            continue
        for mod in d.get("modified", []):
            if "status" not in mod.get("fields", []):
                continue
            old_data = load_yaml(old_root / d["file"])
            new_data = load_yaml(new_root / d["file"])
            old_idx = index_rows(old_data)
            new_idx = index_rows(new_data)
            old_row = old_idx.get(mod["row"], {})
            new_row = new_idx.get(mod["row"], {})
            flips.append({
                "file": d["file"],
                "row": mod["row"],
                "from": old_row.get("status"),
                "to": new_row.get("status"),
            })
    return flips


def render_markdown(old_root: Path, new_root: Path,
                    label_old: str, label_new: str,
                    diffs: list[dict[str, Any]],
                    flips: list[dict[str, Any]]) -> str:
    lines = [
        f"# Snapshot diff — {label_old} → {label_new}",
        "",
        f"_Generated {now_dt().isoformat(timespec='seconds')}_",
        "",
        "## Summary",
        "",
        f"- Files changed: **{len(diffs)}**",
        f"- Status flips: **{len(flips)}**",
        f"- Files added: **{sum(1 for d in diffs if d['change'] == 'added_file')}**",
        f"- Files removed: **{sum(1 for d in diffs if d['change'] == 'removed_file')}**",
        f"- Total rows added: **{sum(len(d.get('added', [])) for d in diffs)}**",
        f"- Total rows removed: **{sum(len(d.get('removed', [])) for d in diffs)}**",
        f"- Total rows modified: **{sum(len(d.get('modified', [])) for d in diffs)}**",
        "",
    ]
    if flips:
        lines.append("## Status flips")
        lines.append("")
        for flip in flips:
            lines.append(
                f"- `{flip['file']}` / `{flip['row']}`: "
                f"`{flip['from']}` → `{flip['to']}`"
            )
        lines.append("")
    if diffs:
        lines.append("## Per-file changes")
        lines.append("")
        for d in diffs:
            lines.append(f"### `{d['file']}` — {d['change']}")
            lines.append("")
            if d["change"] == "added_file":
                lines.append(f"- New file with **{d['added_count']}** rows.")
            elif d["change"] == "removed_file":
                lines.append(f"- File removed; had **{d['removed_count']}** rows.")
            else:
                if d.get("added"):
                    lines.append(f"- **Added rows ({len(d['added'])})**:")
                    for k in d["added"][:10]:
                        lines.append(f"  - `{k}`")
                    if len(d["added"]) > 10:
                        lines.append(f"  - … and {len(d['added']) - 10} more")
                if d.get("removed"):
                    lines.append(f"- **Removed rows ({len(d['removed'])})**:")
                    for k in d["removed"][:10]:
                        lines.append(f"  - `{k}`")
                    if len(d["removed"]) > 10:
                        lines.append(f"  - … and {len(d['removed']) - 10} more")
                if d.get("modified"):
                    lines.append(f"- **Modified rows ({len(d['modified'])})**:")
                    for mod in d["modified"][:10]:
                        fields = ", ".join(mod["fields"][:5])
                        if len(mod["fields"]) > 5:
                            fields += f", … (+{len(mod['fields']) - 5})"
                        lines.append(f"  - `{mod['row']}`: {fields}")
                    if len(d["modified"]) > 10:
                        lines.append(f"  - … and {len(d['modified']) - 10} more")
            lines.append("")
    return "\n".join(lines)


def resolve_old_new(args: argparse.Namespace, workspace: Path) -> tuple[Path, Path, str, str]:
    """Resolve --since/--until/--weekly/--monthly into two memory roots + labels."""
    today = now_dt().date()
    if args.weekly:
        old_day = today - dt.timedelta(days=7)
        new_day = today
    elif args.monthly:
        old_day = today - dt.timedelta(days=30)
        new_day = today
    else:
        old_day = parse_iso_date(args.since) if args.since else today - dt.timedelta(days=7)
        new_day = parse_iso_date(args.until) if args.until else today
    old_dir = find_checkpoint(workspace, old_day) or latest_checkpoint(workspace)
    if old_dir is None:
        raise FileNotFoundError(
            f"no checkpoint found for {old_day.isoformat()} (and no checkpoints exist)"
        )
    new_dir = find_checkpoint(workspace, new_day)
    if new_dir is None:
        # Compare against live _memory/.
        new_dir = workspace / "_memory"
        new_label = "live"
    else:
        new_label = new_day.isoformat()
    old_label = old_dir.name
    return old_dir, new_dir, old_label, new_label


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="snapshot_diff")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--since", type=str, default=None,
                        help="Date (ISO) of the older snapshot.")
    parser.add_argument("--until", type=str, default=None,
                        help="Date (ISO) of the newer snapshot.")
    parser.add_argument("--weekly", action="store_true",
                        help="Compare today vs 7 days ago.")
    parser.add_argument("--monthly", action="store_true",
                        help="Compare today vs 30 days ago.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write to this path (default stdout).")
    parser.add_argument("--json", action="store_true",
                        help="Emit raw diff as JSON instead of rendering markdown.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"No workspace at {workspace}", file=sys.stderr)
        return 1
    try:
        old_root, new_root, label_old, label_new = resolve_old_new(args, workspace)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    diffs = diff_files(old_root, new_root)
    flips = status_flips(diffs, old_root, new_root)
    if args.json:
        out = json.dumps({
            "from": label_old, "to": label_new,
            "diffs": diffs, "status_flips": flips,
        }, indent=2, default=str)
    else:
        out = render_markdown(old_root, new_root, label_old, label_new, diffs, flips)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out)
        print(f"Wrote {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
