#!/usr/bin/env python3
"""Pre-rendered briefing cache + skill-output write-back caching.

Implements superagent/docs/_internal/perf-improvement-ideas.md QW-5 + MI-5.

Pattern: any skill that produces a structured artifact within the same day
(daily-update, weekly-review, monthly-review, draft-email summaries, top-5
status drafts) writes to `_memory/_artifacts/<skill>/<key>.md` with a
sibling `<key>.meta.yaml`. Subsequent skills (whatsup, conversational
follow-ups about today) check the cache before regenerating.

Invalidation rules per `contracts/briefing-cache.md`:
  - Day flip / week flip / month flip (key changes).
  - Source-file mtime > artifact.created_at.
  - TTL expiry from per-skill default in config.
  - Force refresh via `--refresh`.

Intentionally tiny: ~250 LOC. The agent calls `get` before regen, then
calls `put` after producing fresh content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


DEFAULT_TTL_MINUTES = 720  # 12h


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        out = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
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


def load_config(workspace: Path) -> dict[str, Any]:
    """Read config.preferences.briefing_cache."""
    cfg = load_yaml(workspace / "_memory" / "config.yaml") or {}
    bc = (cfg.get("preferences") or {}).get("briefing_cache") or {}
    return {
        "enabled": bool(bc.get("enabled", True)),
        "ttl_minutes": int(bc.get("ttl_minutes", DEFAULT_TTL_MINUTES)),
        "invalidate_on_source_mtime": bool(bc.get("invalidate_on_source_mtime", True)),
    }


def cache_dir(workspace: Path, skill: str) -> Path:
    return workspace / "_memory" / "_artifacts" / skill


def cache_paths(workspace: Path, skill: str, key: str) -> tuple[Path, Path]:
    base = cache_dir(workspace, skill)
    return base / f"{key}.md", base / f"{key}.meta.yaml"


def hash_inputs(paths: Iterable[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            stat = p.stat()
            h.update(f"{p}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        except OSError:
            h.update(f"{p}:missing".encode())
    return h.hexdigest()[:16]


def is_fresh(meta: dict[str, Any], ttl_minutes: int,
             input_paths: Iterable[Path] | None,
             check_mtime: bool) -> bool:
    """Return True if the cache entry is still valid.

    Order: TTL first (cheap), then `inputs_hash` (authoritative — captures
    both content + mtime since the hash function uses both). The bare
    mtime comparison is *not* used: `now_iso()` truncates to seconds, so
    a sub-second-newer source mtime would otherwise spuriously invalidate
    a fresh cache entry.
    """
    created = parse_iso_dt(meta.get("generated_at"))
    if created is None:
        return False
    age_minutes = (now_dt() - created).total_seconds() / 60.0
    if age_minutes > ttl_minutes:
        return False
    if check_mtime and input_paths is not None:
        recorded = meta.get("inputs_hash")
        if recorded:
            current = hash_inputs(input_paths)
            if recorded != current:
                return False
    return True


def get(workspace: Path, skill: str, key: str,
        input_paths: Iterable[Path] | None = None,
        ttl_minutes: int | None = None,
        check_mtime: bool | None = None) -> dict[str, Any] | None:
    """Return the cached artifact + metadata, or None on miss / stale."""
    cfg = load_config(workspace)
    if not cfg["enabled"]:
        return None
    ttl = ttl_minutes if ttl_minutes is not None else cfg["ttl_minutes"]
    check = check_mtime if check_mtime is not None else cfg["invalidate_on_source_mtime"]
    body_path, meta_path = cache_paths(workspace, skill, key)
    if not body_path.exists() or not meta_path.exists():
        return None
    meta = load_yaml(meta_path) or {}
    paths_for_check = list(input_paths) if input_paths is not None else None
    if not is_fresh(meta, ttl, paths_for_check, check):
        return None
    return {
        "body": body_path.read_text(),
        "meta": meta,
        "body_path": str(body_path),
        "meta_path": str(meta_path),
    }


def put(workspace: Path, skill: str, key: str, body: str,
        input_paths: Iterable[Path] | None = None,
        ttl_minutes: int | None = None,
        notes: str = "") -> dict[str, Any]:
    """Write a fresh artifact + metadata."""
    cfg = load_config(workspace)
    ttl = ttl_minutes if ttl_minutes is not None else cfg["ttl_minutes"]
    body_path, meta_path = cache_paths(workspace, skill, key)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(body)
    inputs_hash = hash_inputs(input_paths or [])
    meta = {
        "schema_version": 1,
        "skill": skill,
        "key": key,
        "generated_at": now_iso(),
        "ttl_minutes": ttl,
        "inputs_hash": inputs_hash,
        "size_bytes": len(body.encode("utf-8")),
        "notes": notes,
    }
    save_yaml(meta_path, meta)
    return meta


def list_artifacts(workspace: Path) -> list[dict[str, Any]]:
    root = workspace / "_memory" / "_artifacts"
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        for meta_path in sorted(skill_dir.glob("*.meta.yaml")):
            meta = load_yaml(meta_path) or {}
            body_path = meta_path.with_name(meta_path.stem.replace(".meta", "") + ".md")
            out.append({
                "skill": skill_dir.name,
                "key": meta.get("key", body_path.stem),
                "body_path": str(body_path),
                "exists": body_path.exists(),
                "generated_at": meta.get("generated_at"),
                "ttl_minutes": meta.get("ttl_minutes"),
                "size_bytes": meta.get("size_bytes", 0),
            })
    return out


def evict(workspace: Path, skill: str | None = None,
          older_than_minutes: int | None = None) -> int:
    """Evict cache entries. Returns count removed."""
    root = workspace / "_memory" / "_artifacts"
    if not root.exists():
        return 0
    removed = 0
    cutoff: dt.datetime | None = None
    if older_than_minutes is not None:
        cutoff = now_dt() - dt.timedelta(minutes=older_than_minutes)
    for skill_dir in root.iterdir():
        if not skill_dir.is_dir():
            continue
        if skill and skill_dir.name != skill:
            continue
        for meta_path in skill_dir.glob("*.meta.yaml"):
            meta = load_yaml(meta_path) or {}
            created = parse_iso_dt(meta.get("generated_at"))
            if cutoff is not None and (created is None or created > cutoff):
                continue
            body = meta_path.with_name(meta_path.stem.replace(".meta", "") + ".md")
            if body.exists():
                body.unlink()
            meta_path.unlink()
            removed += 1
    return removed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="briefing_cache")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("--skill", required=True)
    g.add_argument("--key", required=True)
    g.add_argument("--input", action="append", default=[])
    p = sub.add_parser("put")
    p.add_argument("--skill", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--body-file", required=True, type=Path)
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--ttl-minutes", type=int, default=None)
    sub.add_parser("list")
    e = sub.add_parser("evict")
    e.add_argument("--skill", default=None)
    e.add_argument("--older-than-minutes", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "get":
        inputs = [Path(p) for p in args.input]
        result = get(workspace, args.skill, args.key, input_paths=inputs)
        if result is None:
            print(json.dumps({"hit": False}))
            return 1
        print(json.dumps({"hit": True, "meta": result["meta"], "body_path": result["body_path"]},
                         indent=2, default=str))
        return 0
    if args.cmd == "put":
        body = args.body_file.read_text()
        inputs = [Path(p) for p in args.input]
        meta = put(workspace, args.skill, args.key, body,
                   input_paths=inputs, ttl_minutes=args.ttl_minutes)
        print(json.dumps(meta, indent=2, default=str))
        return 0
    if args.cmd == "list":
        for row in list_artifacts(workspace):
            print(f"{row['skill']:<24}{row['key']:<20}{row['size_bytes']:<10}"
                  f"{(row['generated_at'] or '')[:19]}")
        return 0
    if args.cmd == "evict":
        n = evict(workspace, args.skill, args.older_than_minutes)
        print(f"Evicted {n} entries.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
