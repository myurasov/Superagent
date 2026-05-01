#!/usr/bin/env python3
"""Log every user prompt to `_memory/user-queries.jsonl`.

Wired as a `UserPromptSubmit` hook in Cursor (`.cursor/hooks.json`) and
Claude Code (`.claude/settings.json`). The Tailor reads this log during
the strategic pass to spot friction patterns (clusters of similar queries
that aren't being answered well by an existing skill).

Reads the prompt from stdin (the way both IDEs invoke hooks).
Append-only; one JSON object per line; never blocks the prompt.

Privacy: the log is gitignored (lives under `workspace/`).
Disable via `_memory/config.yaml.preferences.privacy.log_user_queries: false`.

Exit code:
  0  always (we never want to block the user's prompt because of a logging error)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def is_logging_enabled(workspace: Path) -> bool:
    """Read config; default to enabled if config can't be read."""
    config_path = workspace / "_memory" / "config.yaml"
    if not config_path.exists():
        return True
    try:
        with config_path.open() as fh:
            config = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return True
    prefs = config.get("preferences", {}) or {}
    privacy = prefs.get("privacy", {}) or {}
    return bool(privacy.get("log_user_queries", True))


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        prog="log_user_query",
        description="Append the user's prompt to user-queries.jsonl.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace path (default: workspace next to framework).",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="unknown",
        help="Which IDE invoked this hook (e.g. 'cursor', 'claude-code').",
    )
    return parser.parse_args(argv)


def read_prompt() -> dict[str, Any]:
    """Read the prompt payload from stdin.

    Both Cursor and Claude Code pipe a JSON object on stdin describing the
    UserPromptSubmit event. Format may evolve; we capture the raw text and
    any structured fields we can.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        return {"prompt": "", "raw_empty": True}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"prompt": raw.strip(), "raw_text": True}
    if not isinstance(payload, dict):
        return {"prompt": str(payload), "raw_non_dict": True}
    return payload


def main(argv: list[str] | None = None) -> int:
    """Entry point. Always returns 0."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace: Path = args.workspace or framework.parent / "workspace"
    if not workspace.exists():
        return 0
    if not is_logging_enabled(workspace):
        return 0

    payload = read_prompt()
    prompt_text = payload.get("prompt", "") or payload.get("text", "")
    if not isinstance(prompt_text, str):
        prompt_text = str(prompt_text)

    log_dir = workspace / "_memory"
    log_path = log_dir / "user-queries.jsonl"
    log_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": now_iso(),
        "source": args.source,
        "prompt": prompt_text,
        "length": len(prompt_text),
    }
    if "session_id" in payload:
        entry["session_id"] = payload["session_id"]
    if "cwd" in payload:
        entry["cwd"] = payload["cwd"]

    try:
        with log_path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
