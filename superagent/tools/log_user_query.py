#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Log every user prompt to `_memory/user-queries.jsonl`.

Wired as a `beforeSubmitPrompt` hook in Cursor (`.cursor/hooks.json`;
`--source` defaults to `cursor`) and as a `UserPromptSubmit` hook in Claude
Code (`.claude/settings.json`, `--source claude-code`). The Supertailor reads
this log during the strategic pass to spot friction patterns (clusters of
similar queries that aren't being answered well by an existing skill).

Reads the prompt from stdin (the way both harnesses invoke hooks).
Append-only; one JSON object per line; never blocks the prompt.

Harness noise: a leading `<ide_opened_file>...</ide_opened_file>` wrapper is
stripped so only the user's own text is logged. Rows whose (stripped) prompt
starts with a harness-generated marker (`<task-notification>`,
`<cross-session-message`, `Fallback heartbeat:`, ... — the shared
`skill_loader.SYNTHETIC_MARKERS` tuple) are still written but tagged
`"synthetic": true`; friction analysis skips tagged rows.

Privacy: the log is gitignored (lives under `workspace/`).
Disable via `_memory/config.yaml.preferences.privacy.log_user_queries: false`.
Credential-looking material in the prompt is redacted before the row is
written (see `redact()`); patterns are deliberately conservative — a missed
secret beats mangled prose.

Exit code:
  0  always (we never want to block the user's prompt because of a logging error)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    from superagent.tools.skill_loader import SYNTHETIC_MARKERS
except ModuleNotFoundError:
    # The hooks run this file as a script (`uv run python superagent/tools/log_user_query.py`),
    # where only the script's own directory is on sys.path. Put the repo root there so the
    # shared marker tuple stays single-sourced in skill_loader instead of being duplicated.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from superagent.tools.skill_loader import SYNTHETIC_MARKERS

REDACTED_PASSWORD = "[REDACTED:password]"
REDACTED_TOKEN = "[REDACTED:token]"

# Leading IDE-context wrapper(s) Claude Code prepends when the user has a file
# open; the user's real prompt follows the closing tag.
_IDE_OPENED_FILE_RE = re.compile(
    r"\A(?:\s*<ide_opened_file>.*?</ide_opened_file>)+\s*", re.DOTALL
)


def strip_ide_wrapper(text: str) -> str:
    """Drop a leading `<ide_opened_file>...</ide_opened_file>` block, keep the rest."""
    return _IDE_OPENED_FILE_RE.sub("", text, count=1)


def is_synthetic_row(text: str) -> bool:
    """True when the prompt STARTS with a harness marker (a quoted marker mid-prose is not synthetic)."""
    return text.lstrip().startswith(SYNTHETIC_MARKERS)

# (c) URLs: the secret path segment after /claim/ (e.g. SimpleFin claim URLs)
# and the value of any `token=` / `*_token=` query parameter.
_CLAIM_URL_RE = re.compile(r"(/claim/)([A-Za-z0-9~._=+-]+)")
_TOKEN_PARAM_RE = re.compile(r"([?&;][A-Za-z0-9_-]*token=)([^&\s#]+)", re.IGNORECASE)

# (a) Credential markers. With an explicit separator (: or =) or an "is"
# phrase, the following token is redacted unconditionally. With bare
# whitespace, only a secret-looking token (see _looks_secret) is redacted so
# prose like "I forgot my password again" survives intact.
_PASSWORD_SEP_RE = re.compile(
    r"(?i)\b(password|passwd|pass|pwd|pw)\b(\s*[:=]\s*|\s+is\s+)(\"[^\"\s]+\"|'[^'\s]+'|\S+)"
)
_PASSWORD_WS_RE = re.compile(r"(?i)\b(password|passwd|pwd)\b[ \t]+(\S{6,})")

# (b) Long high-entropy runs. Hex needs both digits and letters; base64 needs
# a distinctly-base64 char (+ or =) so ordinary long words never match. The
# generic candidate excludes anything preceded by / or . (file paths, domains).
_HEX_RUN_RE = re.compile(
    r"(?<![0-9A-Za-z])(?=[0-9a-fA-F]*\d)(?=\d*[a-fA-F])[0-9a-fA-F]{32,}(?![0-9A-Za-z])"
)
_B64_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])(?=[A-Za-z0-9+=]*[+=])[A-Za-z0-9+]{32,}={0,3}(?![A-Za-z0-9+/=])"
)
_ENTROPY_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9_./~-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)


def _looks_secret(token: str) -> bool:
    """True if a whitespace-following token plausibly IS a credential.

    Requires a digit or an internal symbol so ordinary dictionary words
    ("again,", "tomorrow", "reset") never trip the bare-whitespace password
    pattern. Trailing sentence punctuation is ignored; path-like tokens
    (containing `/`) are exempt — false negatives preferred.
    """
    core = token.rstrip(".,;:!?)\"'")
    if not core or "/" in core or "\\" in core:
        return False
    return any(ch.isdigit() or not ch.isalnum() for ch in core)


def _looks_high_entropy(token: str) -> bool:
    """True for random-looking blobs: 20+ chars mixing cases and digits.

    A run of 5+ same-case letters reads as a word (camelCase identifiers,
    long filenames), so those are left alone — false negatives preferred.
    """
    if REDACTED_TOKEN in token or REDACTED_PASSWORD in token:
        return False
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_upper and has_lower and has_digit):
        return False
    return not re.search(r"[a-z]{5,}|[A-Z]{5,}", token)


def redact(text: str) -> str:
    """Redact credential-looking material from a prompt before logging.

    Three conservative passes (specific → generic):
      (c) claim-URL path segments and `token=` query-param values;
      (a) tokens following password/passwd/pass/pwd/pw markers;
      (b) long high-entropy runs (hex 32+, base64 32+, mixed-case blobs 20+).

    Normal prose, file paths, and `kind:slug` handles pass through untouched.
    """
    if not text:
        return text
    text = _CLAIM_URL_RE.sub(rf"\g<1>{REDACTED_TOKEN}", text)
    text = _TOKEN_PARAM_RE.sub(rf"\g<1>{REDACTED_TOKEN}", text)
    text = _PASSWORD_SEP_RE.sub(rf"\g<1>\g<2>{REDACTED_PASSWORD}", text)
    text = _PASSWORD_WS_RE.sub(
        lambda m: (f"{m.group(1)} {REDACTED_PASSWORD}"
                   if _looks_secret(m.group(2)) and REDACTED_PASSWORD not in m.group(2)
                   else m.group(0)),
        text,
    )
    text = _HEX_RUN_RE.sub(REDACTED_TOKEN, text)
    text = _B64_RUN_RE.sub(REDACTED_TOKEN, text)
    text = _ENTROPY_CANDIDATE_RE.sub(
        lambda m: REDACTED_TOKEN if _looks_high_entropy(m.group(0)) else m.group(0),
        text,
    )
    return text


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
        default="cursor",
        help="Which IDE invoked this hook (default: 'cursor').",
    )
    return parser.parse_args(argv)


def read_prompt() -> dict[str, Any]:
    """Read the prompt payload from stdin.

    The harness pipes a JSON object on stdin describing the prompt-submit
    event. Format may evolve; we capture the raw text and any structured
    fields we can.
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
    prompt_text = strip_ide_wrapper(prompt_text)
    synthetic = is_synthetic_row(prompt_text)
    try:
        prompt_text = redact(prompt_text)
    except Exception:
        # Fail closed: never write an unredacted prompt, never block the turn.
        prompt_text = "[REDACTION-ERROR: prompt withheld]"

    log_dir = workspace / "_memory"
    log_path = log_dir / "user-queries.jsonl"
    log_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": now_iso(),
        "source": args.source,
        "prompt": prompt_text,
        "length": len(prompt_text),
    }
    if synthetic:
        entry["synthetic"] = True
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
