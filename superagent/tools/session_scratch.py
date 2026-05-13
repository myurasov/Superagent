#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Per-session scratchpad / dedupe.

Implements superagent/docs/_internal/perf-improvement-ideas.md MI-1.

Tracks what the agent loaded in the current conversation. Before any read,
the agent (or skill orchestrator) checks the scratchpad: if the file's
mtime hasn't changed since the recorded `at`, OR the recorded hash matches,
the agent skips the redundant read.

Files: `_memory/_session/<session-id>.yaml`. One YAML per session.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml


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


def save_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def session_dir(workspace: Path) -> Path:
    return workspace / "_memory" / "_session"


def session_path(workspace: Path, session_id: str) -> Path:
    return session_dir(workspace) / f"{session_id}.yaml"


def derive_session_id() -> str:
    """Construct a stable-ish session id (timestamp + short hash)."""
    suffix = uuid.uuid4().hex[:6].upper()
    return f"sess-{dt.datetime.now().strftime('%Y-%m-%d-%H%M')}-{suffix}"


def load_session(workspace: Path, session_id: str) -> dict[str, Any]:
    data = load_yaml(session_path(workspace, session_id))
    if not isinstance(data, dict):
        return {
            "session_id": session_id,
            "started_at": now_iso(),
            "loaded_files": [],
            "mcp_calls": [],
            "tool_runs": [],
        }
    data.setdefault("loaded_files", [])
    data.setdefault("mcp_calls", [])
    data.setdefault("tool_runs", [])
    return data


def save_session(workspace: Path, data: dict[str, Any]) -> None:
    save_yaml(session_path(workspace, data["session_id"]), data)


def file_signature(path: Path) -> tuple[int, int, str]:
    """Return (size, mtime, hash16) of a file (or zeros if missing)."""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0, "")
    if stat.st_size > 1_000_000:
        h = ""
    else:
        h = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return (stat.st_size, int(stat.st_mtime), h)


def record_read(workspace: Path, session_id: str, file_path: Path) -> dict[str, Any]:
    data = load_session(workspace, session_id)
    size, mtime, h = file_signature(file_path)
    entry = {
        "path": str(file_path),
        "at": now_iso(),
        "size": size,
        "mtime": mtime,
        "hash": h,
    }
    existing = next(
        (e for e in data["loaded_files"] if e.get("path") == str(file_path)),
        None,
    )
    if existing is not None:
        existing.update(entry)
    else:
        data["loaded_files"].append(entry)
    save_session(workspace, data)
    return entry


def is_already_loaded(workspace: Path, session_id: str,
                      file_path: Path) -> bool:
    """True if this file was already loaded in this session and hasn't changed."""
    data = load_session(workspace, session_id)
    existing = next(
        (e for e in data.get("loaded_files", []) if e.get("path") == str(file_path)),
        None,
    )
    if existing is None:
        return False
    size, mtime, h = file_signature(file_path)
    return (existing.get("size") == size
            and existing.get("mtime") == mtime
            and (h == "" or h == existing.get("hash", "")))


def record_mcp(workspace: Path, session_id: str, server: str, tool: str,
               args_summary: str) -> None:
    data = load_session(workspace, session_id)
    data["mcp_calls"].append({
        "server": server, "tool": tool,
        "args_summary": args_summary,
        "at": now_iso(),
    })
    save_session(workspace, data)


def record_tool(workspace: Path, session_id: str, tool: str, args: str = "") -> None:
    data = load_session(workspace, session_id)
    data["tool_runs"].append({
        "tool": tool,
        "args": args,
        "at": now_iso(),
    })
    save_session(workspace, data)


def list_sessions(workspace: Path) -> list[dict[str, Any]]:
    sd = session_dir(workspace)
    if not sd.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(sd.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = load_yaml(path) or {}
        out.append({
            "session_id": data.get("session_id", path.stem),
            "started_at": data.get("started_at"),
            "loaded_count": len(data.get("loaded_files", [])),
            "mcp_count": len(data.get("mcp_calls", [])),
            "tool_count": len(data.get("tool_runs", [])),
            "size_kb": round(path.stat().st_size / 1024, 1),
        })
    return out


def cleanup(workspace: Path, expire_days: int = 30,
            keep_recent: int = 20) -> int:
    """Remove sessions older than expire_days, beyond `keep_recent` count. Return count removed."""
    sd = session_dir(workspace)
    if not sd.exists():
        return 0
    paths = sorted(sd.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = dt.datetime.now().timestamp() - expire_days * 86400
    keep = set(p.name for p in paths[:keep_recent])
    removed = 0
    for path in paths:
        if path.name in keep:
            continue
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="session_scratch")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--session", type=str, default=None,
                        help="Session id; default derives a new one.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="Start a new session id.")
    r = sub.add_parser("record", help="Record a file read in the session.")
    r.add_argument("--file", required=True, type=Path)
    c = sub.add_parser("check", help="Check whether a file is already loaded.")
    c.add_argument("--file", required=True, type=Path)
    sub.add_parser("list", help="List sessions.")
    cl = sub.add_parser("cleanup", help="Remove old sessions.")
    cl.add_argument("--expire-days", type=int, default=30)
    cl.add_argument("--keep-recent", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "new":
        sid = derive_session_id()
        load_session(workspace, sid)
        save_session(workspace, load_session(workspace, sid))
        print(sid)
        return 0
    if args.cmd == "record":
        sid = args.session or derive_session_id()
        entry = record_read(workspace, sid, args.file)
        print(json.dumps(entry, default=str))
        return 0
    if args.cmd == "check":
        sid = args.session
        if not sid:
            print("--session required", file=sys.stderr)
            return 2
        loaded = is_already_loaded(workspace, sid, args.file)
        print(json.dumps({"already_loaded": loaded}))
        return 0
    if args.cmd == "list":
        for row in list_sessions(workspace):
            print(f"{row['session_id']:<28}{(row.get('started_at') or '')[:19]:<22}"
                  f"{row['loaded_count']:>6} files  {row['mcp_count']:>4} mcp  {row['size_kb']:>6} KB")
        return 0
    if args.cmd == "cleanup":
        n = cleanup(workspace, args.expire_days, args.keep_recent)
        print(f"removed {n} old session(s)")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
