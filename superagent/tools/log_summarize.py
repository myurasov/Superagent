#!/usr/bin/env python3
"""Maintain `<file>.summary.yaml` siblings for unbounded YAML logs.

Implements perf-improvement-ideas.md QW-4.

For files that grow without bound (`interaction-log.yaml`,
`ingestion-log.yaml`, `pm-suggestions.yaml`), maintain a small sibling
`<file>.summary.yaml` with aggregate counts, last-N-day breakdowns, and
notable-row pointers. Skills consult the summary first; only pull actual
rows when the summary tells them they need to.

Run on a nightly cron OR as part of any skill that mutates one of these
files. Idempotent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml


# Files we know how to summarize, mapped to their list-key.
SUMMARIZABLE: dict[str, str] = {
    "interaction-log.yaml": "entries",
    "ingestion-log.yaml": "runs",
    "pm-suggestions.yaml": "suggestions",
    "personal-signals.yaml": "signals",
    "action-signals.yaml": "signals",
    "decisions.yaml": "decisions",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def get_row_ts(row: dict[str, Any]) -> dt.datetime | None:
    """Look up a timestamp on a row (different files use different field names)."""
    for field in ("ts", "timestamp", "captured_at", "started_at", "proposed_at",
                  "created", "date"):
        ts = parse_iso(row.get(field))
        if ts is not None:
            return ts
    return None


def get_row_kind(row: dict[str, Any]) -> str:
    """Pick a `kind` to bucket by, depending on file shape."""
    for field in ("type", "kind", "category", "target"):
        v = row.get(field)
        if isinstance(v, str) and v:
            return v
    return "other"


def filter_window(rows: Iterable[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = now_dt() - dt.timedelta(days=days)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = get_row_ts(row)
        if ts is None:
            continue
        if ts >= cutoff:
            out.append(row)
    return out


def notable_rows(rows: Iterable[dict[str, Any]], limit: int = 5) -> list[str]:
    """Pull up to N short headlines from recent rows."""
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in ("subject", "title", "summary", "decision", "trigger_phrase"):
            v = row.get(field)
            if isinstance(v, str) and v.strip():
                out.append(v.strip().splitlines()[0][:120])
                break
        if len(out) >= limit:
            break
    return out


def build_summary(path: Path, list_key: str) -> dict[str, Any]:
    data = load_yaml(path)
    rows_raw = data.get(list_key) or []
    rows = [r for r in rows_raw if isinstance(r, dict)]
    timed = [(r, get_row_ts(r)) for r in rows]
    timestamps = [t for _r, t in timed if t is not None]
    last_30 = filter_window(rows, 30)
    last_7 = filter_window(rows, 7)
    last_30_sorted = sorted(
        last_30, key=lambda r: get_row_ts(r) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "source_file": path.name,
        "total_rows": len(rows),
        "first_entry_at": min(timestamps).isoformat() if timestamps else None,
        "last_entry_at": max(timestamps).isoformat() if timestamps else None,
        "last_30_days": {
            "count": len(last_30),
            "by_kind": dict(Counter(get_row_kind(r) for r in last_30)),
            "notable": notable_rows(last_30_sorted, limit=5),
        },
        "last_7_days": {
            "count": len(last_7),
            "by_kind": dict(Counter(get_row_kind(r) for r in last_7)),
        },
    }
    return summary


def summarize_one(path: Path, list_key: str) -> Path:
    summary = build_summary(path, list_key)
    out = path.with_name(path.stem + ".summary.yaml")
    save_yaml(out, summary)
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="log_summarize")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--file", type=str, default=None,
                        help="Specific file under _memory/ to summarize.")
    parser.add_argument("--all", action="store_true",
                        help="Summarize every known summarizable log.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    memory = workspace / "_memory"
    if not memory.is_dir():
        print(f"no _memory at {memory}", file=sys.stderr)
        return 1
    targets: list[tuple[Path, str]] = []
    if args.file:
        if args.file not in SUMMARIZABLE:
            print(f"don't know how to summarize {args.file!r}; "
                  f"known: {', '.join(SUMMARIZABLE)}", file=sys.stderr)
            return 2
        targets.append((memory / args.file, SUMMARIZABLE[args.file]))
    elif args.all:
        for fname, key in SUMMARIZABLE.items():
            p = memory / fname
            if p.exists():
                targets.append((p, key))
    else:
        print("specify --file <name> or --all", file=sys.stderr)
        return 2
    for path, key in targets:
        out = summarize_one(path, key)
        print(f"summarized {path.name} -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
