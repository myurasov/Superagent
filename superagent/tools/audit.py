#!/usr/bin/env -S uv run python
"""Per-row audit trail — write + read.

Implements superagent/docs/_internal/ideas-better-structure.md item #17.

For every entity-shape file in `_memory/`, maintain a sibling
`<file>.history.jsonl` (append-only) capturing every mutation:
  { ts, who, kind: create|update|delete, row_id, old, new, source, note }

The writer is a small wrapper around YAML mutations. Skills should call
`record_change()` BEFORE persisting the new state of a row, so the
diff captures the actual transition.

Yearly rotation (when `config.preferences.audit.rotate_yearly` is true)
moves `<file>.history.jsonl` to `Archive/<YYYY>/<file>.history.jsonl`
at year flip.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml


SKIP_DEFAULT = ("interaction-log.yaml", "ingestion-log.yaml",
                "user-queries.jsonl", "events.yaml")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def is_audit_enabled(workspace: Path) -> bool:
    cfg = load_yaml(workspace / "_memory" / "config.yaml") or {}
    audit = (cfg.get("preferences") or {}).get("audit") or {}
    return bool(audit.get("enabled", True))


def skip_files(workspace: Path) -> set[str]:
    cfg = load_yaml(workspace / "_memory" / "config.yaml") or {}
    audit = (cfg.get("preferences") or {}).get("audit") or {}
    return set(audit.get("skip_files") or SKIP_DEFAULT)


def history_path(file_path: Path) -> Path:
    """Sibling history.jsonl path for a given memory file."""
    return file_path.with_name(file_path.stem + ".history.jsonl")


def record_change(workspace: Path, file_path: Path, kind: str,
                  row_id: str, old: dict[str, Any] | None,
                  new: dict[str, Any] | None,
                  who: str = "user", source: str = "",
                  note: str = "") -> bool:
    """Append one audit row. Returns True if written."""
    if not is_audit_enabled(workspace):
        return False
    if file_path.name in skip_files(workspace):
        return False
    h = history_path(file_path)
    h.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": now_iso(),
        "who": who,
        "kind": kind,
        "row_id": row_id,
        "source": source,
        "note": note,
        "old": old,
        "new": new,
    }
    with h.open("a") as fh:
        fh.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    return True


def read_history(file_path: Path, row_id: str | None = None,
                 limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent N audit entries (optionally filtered by row)."""
    h = history_path(file_path)
    if not h.exists():
        return []
    rows: list[dict[str, Any]] = []
    with h.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row_id and rec.get("row_id") != row_id:
                continue
            rows.append(rec)
    return rows[-limit:]


def list_files_with_audit(workspace: Path) -> list[Path]:
    memory = workspace / "_memory"
    if not memory.exists():
        return []
    return sorted(memory.glob("*.history.jsonl"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="audit")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("history", help="Show audit history for a file or row.")
    h.add_argument("--file", required=True, type=Path,
                   help="Path under _memory/ (e.g. _memory/contacts.yaml).")
    h.add_argument("--row", type=str, default=None,
                   help="Filter by row id.")
    h.add_argument("--limit", type=int, default=50)
    h.add_argument("--json", action="store_true")
    sub.add_parser("list", help="List all audit-trail files in the workspace.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        for p in list_files_with_audit(workspace):
            count = sum(1 for _ in p.open())
            print(f"{p.name:<40}{count:>8} entries")
        return 0
    if args.cmd == "history":
        rows = read_history(args.file, args.row, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
            return 0
        for r in rows:
            print(f"{r.get('ts', '')[:19]}  [{r.get('kind')}]  {r.get('row_id')}  by {r.get('who')}")
            if r.get("note"):
                print(f"    note: {r['note']}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
