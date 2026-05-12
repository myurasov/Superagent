#!/usr/bin/env -S uv run python
"""Time-windowed views over append-only logs (events stream and friends).

Implements superagent/docs/_internal/ideas-better-structure.md item #22 + superagent/docs/_internal/perf-improvement-ideas.md MI-2.

For partitioned logs (default: `_memory/events/<YYYY-Qn>.yaml`), this module
yields events in a date range without loading the entire history.

Also writes events into the appropriate quarterly partition. The partition
index `_memory/events.yaml` is updated atomically.

CLI:
  uv run python -m superagent.tools.log_window read --since 2026-04-01 --until 2026-04-28
  uv run python -m superagent.tools.log_window append --kind skill_run --subject "ran daily-update"
  uv run python -m superagent.tools.log_window stats
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    try:
        out = dt.datetime.fromisoformat(raw)
    except ValueError:
        try:
            out = dt.datetime.fromisoformat(raw + "T00:00:00")
        except ValueError:
            return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out


def quarter_for(when: dt.datetime) -> str:
    q = (when.month - 1) // 3 + 1
    return f"{when.year}-Q{q}"


def quarters_in_range(since: dt.datetime, until: dt.datetime) -> list[str]:
    """Return all quarters that overlap [since, until]."""
    out: list[str] = []
    cursor = dt.datetime(since.year, ((since.month - 1) // 3) * 3 + 1, 1,
                         tzinfo=since.tzinfo or dt.timezone.utc)
    end = dt.datetime(until.year, until.month, 1,
                      tzinfo=until.tzinfo or dt.timezone.utc)
    while cursor <= end:
        out.append(quarter_for(cursor))
        if cursor.month >= 10:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 3)
    return out


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def save_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def events_root(workspace: Path) -> Path:
    return workspace / "_memory" / "events"


def index_path(workspace: Path) -> Path:
    return workspace / "_memory" / "events.yaml"


def partition_path(workspace: Path, quarter: str) -> Path:
    return events_root(workspace) / f"{quarter}.yaml"


def load_partition(workspace: Path, quarter: str) -> list[dict[str, Any]]:
    """Return the list of event rows in the given quarter (empty if missing)."""
    data = load_yaml(partition_path(workspace, quarter))
    if not isinstance(data, dict):
        return []
    rows = data.get("events") or []
    return [r for r in rows if isinstance(r, dict)]


def write_partition(workspace: Path, quarter: str, rows: list[dict[str, Any]]) -> None:
    save_yaml(partition_path(workspace, quarter), {
        "schema_version": 1,
        "quarter": quarter,
        "last_updated": now_iso(),
        "events": rows,
    })


def update_index(workspace: Path) -> None:
    """Refresh the partition index in events.yaml."""
    root = events_root(workspace)
    if not root.exists():
        return
    partitions: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        rows = load_partition(workspace, path.stem)
        timestamps: list[dt.datetime] = []
        by_kind: dict[str, int] = {}
        for r in rows:
            ts = parse_iso_dt(r.get("ts"))
            if ts is not None:
                timestamps.append(ts)
            kind = r.get("kind") or "other"
            by_kind[kind] = by_kind.get(kind, 0) + 1
        partitions.append({
            "quarter": path.stem,
            "path": f"_memory/events/{path.name}",
            "first_event_at": min(timestamps).isoformat() if timestamps else None,
            "last_event_at": max(timestamps).isoformat() if timestamps else None,
            "event_count": len(rows),
            "by_kind": by_kind,
        })
    idx_path = index_path(workspace)
    existing = load_yaml(idx_path) or {}
    existing["schema_version"] = 1
    existing["generated_at"] = now_iso()
    existing["partitions"] = partitions
    save_yaml(idx_path, existing)


def append_event(workspace: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Append one event to the appropriate quarterly partition."""
    ts = parse_iso_dt(event.get("ts")) or now_dt()
    event["ts"] = ts.isoformat(timespec="seconds")
    if not event.get("id"):
        existing = load_partition(workspace, quarter_for(ts))
        event["id"] = next_event_id(existing, ts)
    quarter = quarter_for(ts)
    rows = load_partition(workspace, quarter)
    rows.append(event)
    write_partition(workspace, quarter, rows)
    update_index(workspace)
    return event


def next_event_id(existing: list[dict[str, Any]], when: dt.datetime) -> str:
    today = when.strftime("%Y-%m-%d")
    prefix = f"evt-{today}-"
    nums = []
    for r in existing:
        rid = r.get("id", "")
        if isinstance(rid, str) and rid.startswith(prefix):
            try:
                nums.append(int(rid.rsplit("-", 1)[-1]))
            except ValueError:
                pass
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def read_window(workspace: Path, since: dt.datetime,
                until: dt.datetime) -> Iterable[dict[str, Any]]:
    """Yield events with `since <= ts <= until`."""
    quarters = quarters_in_range(since, until)
    for q in quarters:
        rows = load_partition(workspace, q)
        for row in rows:
            ts = parse_iso_dt(row.get("ts"))
            if ts is None:
                continue
            if since <= ts <= until:
                yield row


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="log_window")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("read", help="Read events in a window.")
    r.add_argument("--since", type=str, required=True)
    r.add_argument("--until", type=str, default=None)
    r.add_argument("--kind", type=str, default=None,
                   help="Filter by event kind.")
    r.add_argument("--limit", type=int, default=200)
    r.add_argument("--json", action="store_true")

    a = sub.add_parser("append", help="Append a single event row.")
    a.add_argument("--kind", type=str, required=True)
    a.add_argument("--actor", type=str, default="user")
    a.add_argument("--subject", type=str, required=True)
    a.add_argument("--summary", type=str, default="")
    a.add_argument("--related-domain", type=str, default=None)
    a.add_argument("--related-project", type=str, default=None)
    a.add_argument("--payload", type=str, default=None,
                   help="JSON-encoded payload dict.")
    a.add_argument("--tags", type=str, default=None,
                   help="Comma-separated tags.")

    s = sub.add_parser("stats", help="Show partition stats.")
    s.add_argument("--json", action="store_true")

    sub.add_parser("rebuild-index",
                   help="Refresh events.yaml partition index.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    events_root(workspace).mkdir(parents=True, exist_ok=True)

    if args.cmd == "read":
        since = parse_iso_dt(args.since) or now_dt() - dt.timedelta(days=7)
        until = parse_iso_dt(args.until) if args.until else now_dt()
        rows = list(read_window(workspace, since, until))
        if args.kind:
            rows = [r for r in rows if r.get("kind") == args.kind]
        rows = rows[: args.limit]
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            for r in rows:
                print(f"{r.get('ts')}  [{r.get('kind')}] {r.get('subject')}")
        return 0
    if args.cmd == "append":
        payload = json.loads(args.payload) if args.payload else {}
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        ev = append_event(workspace, {
            "kind": args.kind,
            "actor": args.actor,
            "subject": args.subject,
            "summary": args.summary,
            "related_domain": args.related_domain,
            "related_project": args.related_project,
            "payload": payload,
            "tags": tags,
        })
        print(f"appended {ev['id']} ({ev['ts']}) -> partition {quarter_for(parse_iso_dt(ev['ts']))}")
        return 0
    if args.cmd == "stats":
        update_index(workspace)
        idx = load_yaml(index_path(workspace)) or {}
        if args.json:
            print(json.dumps(idx, indent=2, default=str))
            return 0
        partitions = idx.get("partitions", [])
        for p in partitions:
            print(f"{p['quarter']:<10} {p['event_count']:<8} "
                  f"{p.get('first_event_at', '')[:19]:<22} -> {p.get('last_event_at', '')[:19]}")
        total = sum(p.get("event_count", 0) for p in partitions)
        print(f"\n{len(partitions)} partition(s), {total} total events.")
        return 0
    if args.cmd == "rebuild-index":
        update_index(workspace)
        print(f"rebuilt {index_path(workspace)}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
