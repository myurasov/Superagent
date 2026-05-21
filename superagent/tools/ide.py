#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""IDE detection helper.

Superagent supports two host IDEs as first-class citizens:

- **Cursor** — reads `AGENTS.md` natively; project-level MCP config at
  `.cursor/mcp.json`; hooks at `.cursor/hooks.json`.
- **Claude Code** — reads `CLAUDE.md` (which `@`-imports `AGENTS.md`);
  project-level MCP config at `.mcp.json`; hooks at `.claude/settings.json`.

This module centralizes the "which IDE am I running under?" decision so
skills and scripts don't open-code the env-var probes themselves.

Detection is **purely environment-driven** and re-runs on every call —
there is intentionally no config override or sticky state. Users who
switch between Cursor and Claude Code interchangeably get the right
answer on every turn without touching `_memory/config.yaml`.

Detection rules (in order):

1. `CLAUDECODE=1` set in the environment → `IDE.CLAUDE_CODE`.
2. Any `CURSOR_*` env var set (`CURSOR_TRACE_ID`, `CURSOR_SESSION_ID`,
   ...) → `IDE.CURSOR`.
3. Both Claude Code and Cursor markers present (rare; usually means a
   user opened a sub-shell across IDEs) → prefer Claude Code, the more
   specific marker.
4. Neither → `IDE.UNKNOWN`. Callers decide whether to ask the user or
   default to a safe behavior.

API:

    from superagent.tools.ide import IDE, detect, label

    ide = detect()                         # IDE enum
    print(label(ide))                      # "claude-code" / "cursor" / "unknown"

CLI:

    uv run python -m superagent.tools.ide current      # prints label
    uv run python -m superagent.tools.ide is-claude    # exit 0 if Claude Code
    uv run python -m superagent.tools.ide is-cursor    # exit 0 if Cursor

Exit codes match unix convention — 0 on a positive answer, 1 otherwise.
"""
from __future__ import annotations

import argparse
import enum
import os
import sys
from collections.abc import Mapping


class IDE(enum.Enum):
    """The host IDE Superagent is running under."""

    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    UNKNOWN = "unknown"


# Env-var markers. The Claude Code marker is a single well-known flag;
# Cursor sets a family of `CURSOR_*` variables (any one is enough).
CLAUDE_CODE_MARKER = "CLAUDECODE"
CLAUDE_CODE_MARKER_TRUTHY = ("1", "true", "yes", "on")
CURSOR_MARKER_PREFIX = "CURSOR_"


def _has_claude_code(env: Mapping[str, str]) -> bool:
    """Return True when the env carries Claude Code's marker variable."""
    value = env.get(CLAUDE_CODE_MARKER, "")
    return value.lower() in CLAUDE_CODE_MARKER_TRUTHY


def _has_cursor(env: Mapping[str, str]) -> bool:
    """Return True when the env carries any `CURSOR_*` marker variable."""
    return any(key.startswith(CURSOR_MARKER_PREFIX) for key in env)


def detect(env: Mapping[str, str] | None = None) -> IDE:
    """Return the host IDE detected from the environment.

    Pass `env` to make tests deterministic; defaults to `os.environ`.
    Re-runs on every call — no caching, no sticky state.
    """
    env = os.environ if env is None else env
    claude = _has_claude_code(env)
    cursor = _has_cursor(env)
    if claude:
        return IDE.CLAUDE_CODE
    if cursor:
        return IDE.CURSOR
    return IDE.UNKNOWN


def label(ide: IDE) -> str:
    """Return the canonical kebab-case name for an IDE enum value."""
    return ide.value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ide",
        description="Report which IDE Superagent is running under.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("current", help="Print the detected IDE label.")
    sub.add_parser("is-claude", help="Exit 0 iff the active IDE is Claude Code.")
    sub.add_parser("is-cursor", help="Exit 0 iff the active IDE is Cursor.")
    sub.add_parser("env", help="Print the env-var markers the detector saw.")
    return parser


def _cmd_current() -> int:
    print(label(detect()))
    return 0


def _cmd_is_claude() -> int:
    return 0 if detect() is IDE.CLAUDE_CODE else 1


def _cmd_is_cursor() -> int:
    return 0 if detect() is IDE.CURSOR else 1


def _cmd_env() -> int:
    env = os.environ
    claude = _has_claude_code(env)
    print(f"claude_code_marker={'set' if claude else 'unset'} ({CLAUDE_CODE_MARKER})")
    cursor_keys = sorted(k for k in env if k.startswith(CURSOR_MARKER_PREFIX))
    if cursor_keys:
        print(f"cursor_markers=set ({', '.join(cursor_keys)})")
    else:
        print(f"cursor_markers=unset ({CURSOR_MARKER_PREFIX}*)")
    print(f"detected={label(detect())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    cmd = args.cmd or "current"
    dispatch = {
        "current": _cmd_current,
        "is-claude": _cmd_is_claude,
        "is-cursor": _cmd_is_cursor,
        "env": _cmd_env,
    }
    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 2
    return handler()


if __name__ == "__main__":
    sys.exit(main())
