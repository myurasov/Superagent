#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
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
secret beats mangled prose. `redact()` is also the shared scrubber for signal
text (`action-signals.yaml` trigger phrases and context), so it stays
importable and side-effect free.

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
    # Run by path rather than as a module (`uv run python superagent/tools/log_user_query.py`),
    # only the script's own directory is on sys.path. Put the repo root there so the
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
# whitespace -- including the short cues `pass` / `pw` and the "<cue> to X"
# phrasing ("pw to X") -- only a secret-looking token (see _looks_secret) is
# redacted, so prose like "I forgot my password again" or "pass the salt"
# survives intact.
_PASSWORD_SEP_RE = re.compile(
    r"(?i)\b(password|passwd|pass|pwd|pw)\b(\s*[:=]\s*|\s+is\s+)(\"[^\"\s]+\"|'[^'\s]+'|\S+)"
)
_PASSWORD_WS_RE = re.compile(
    r"(?i)(?P<head>\b(?:password|passwd|pass|pwd|pw)(?:[ \t]+to)?[ \t]+)(?P<secret>\S{6,})"
)

# Tokens that carry a digit or a symbol yet are plainly not credentials, so a
# bare cue never redacts them: `pass contact:dr-smith-dentist` (a handle),
# `pass st-2026-10-06-002` / `task-20260428-001` (a dated id), `pass 2026-10-08`
# (an ISO date). CLI flags (`pass --no-verify`) are caught by a leading '-'.
_HANDLE_RE = re.compile(r"[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9._-]*")
_DATED_ID_RE = re.compile(r"[a-z]+-\d{4}-?\d{2}-?\d{2}-\d{3,}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_CODE_CALL_RE = re.compile(r"[A-Za-z_][\w.]*\(")  # `tests pass `run_all.main(2)``
_WRAP_OPEN = "([{<\"'`"
_WRAP_CLOSE = ".,;:!?)]}>\"'`"


def _has_secret_evidence(core: str) -> bool:
    """A digit, or a symbol other than the word joiners `-` `_` `.` (`foo.bar`, `my-notes` are words)."""
    return any(ch.isdigit() or (not ch.isalnum() and ch not in "-_.") for ch in core)

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

    Requires a digit or a symbol other than the word joiners `-` `_` `.`, so
    ordinary words ("again,", "(because", "pass-through", "module.func") never
    trip the bare-whitespace password pattern. Wrapping punctuation is ignored;
    path-like tokens (containing `/`) are exempt — false negatives preferred.
    `id/secret` pairs, which this exemption skips, get their own pass
    (`_redact_pair`). CLI flags, `kind:slug` handles, dated ids, ISO dates and
    code calls (`name(`) are never secrets.
    """
    core = token.lstrip(_WRAP_OPEN).rstrip(_WRAP_CLOSE)
    if not core or "/" in core or "\\" in core or core.startswith("-"):
        return False
    if _HANDLE_RE.fullmatch(core) or _DATED_ID_RE.fullmatch(core) or _ISO_DATE_RE.fullmatch(core):
        return False
    if _CODE_CALL_RE.search(core):
        return False
    return _has_secret_evidence(core)


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


# (d) id/secret pairs: `<user>/<secret>` or `<email>/<secret>` as ONE
# whitespace-delimited token with exactly one '/'. The '/' exemption in
# _looks_secret (and the '/'-lookbehind of _ENTROPY_CANDIDATE_RE) never sees
# these, so they get their own pass: only the right-hand side is redacted, and
# only when a credential cue sits within _CUE_WINDOW characters on either side,
# or when the pair leads its line (a pasted pair; stricter shape test there,
# since there is no cue to lean on).
_CRED_PAIR_RE = re.compile(
    r"(?<![^\s\"'`(\[{<=:,;])"  # token start: text start, whitespace, quote, bracket, = : , ;
    r"(?P<id>[A-Za-z0-9][A-Za-z0-9._%+-]*(?:@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)?)"
    r"/(?P<secret>[^\s/]+)(?=\s|$)"
)
_CRED_CUE_RE = re.compile(
    r"(?i)\b(?:creds?|credentials?|logins?|accounts?|usernames?|passwords?|passwd|pass|pwd|pw)\b"
)
_CUE_WINDOW = 60
# Left-hand sides that name a folder, not an account (`Projects/tax-2026`).
_PATHISH_IDS = frozenset({
    "domains", "projects", "sources", "outbox", "inbox", "resources", "watchlist",
    "workspace", "superagent", "_memory", "_custom", "tasks", "skills", "tools",
    "contracts", "rules", "docs", "templates", "tests", "migrations", "watchers",
    "agents", "src", "lib", "bin", "scripts", "archive",
})
_HOSTNAME_ID_RE = re.compile(
    r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|io|dev|app|ai|co|us|me|info|biz|ly|tv|xyz|cloud)",
    re.IGNORECASE,
)
_FILE_EXT_RE = re.compile(
    r"\.(?:md|txt|pdf|ya?ml|jsonl?|csv|tsv|xlsx?|docx?|pptx?|py|sh|js|ts|html?|css|png|jpe?g|"
    r"gif|webp|heic|tiff?|zip|gz|tar|log|ini|toml|cfg|conf|xml|ics|eml)$",
    re.IGNORECASE,
)
_NUMERIC_GROUP_RE = re.compile(r"\d+(?:[.:-]\d+)+")  # 2026-09, 1.2.3, 10:30
_SLUG_SEGMENT_RE = re.compile(r"[A-Z]?[a-z]+|[a-z0-9]+")  # `notes`, `Meeting`, `v2`, `2026`
_LINE_LEAD_RE = re.compile(r"[ \t]*(?:(?:[-*+\u2022]|\d+[.)])[ \t]+)?[\"'`]?")


def _pair_secret_shape(core: str, *, strict: bool) -> bool:
    """True when the right-hand side of an `id/secret` pair plausibly IS a credential.

    Both modes need secret evidence (`_has_secret_evidence`: a hyphenated word
    like `sign-in` is not enough) and reject file names (`report.pdf`) and
    numeric groups (`2026-09`). `strict` (no cue nearby; the pair leads its line)
    also needs 8+ characters and refuses slugs whose every `-`/`_`/`.` segment is
    a plain word or lowercase alnum (`feature-x2`, `Meeting-Notes`, `q3_2026`).
    """
    if len(core) < (8 if strict else 4):
        return False
    if REDACTED_PASSWORD in core or REDACTED_TOKEN in core:
        return False
    if _FILE_EXT_RE.search(core) or _NUMERIC_GROUP_RE.fullmatch(core):
        return False
    if not _has_secret_evidence(core):
        return False
    if strict:
        segments = [seg for seg in re.split(r"[-_.]", core) if seg]
        if segments and all(_SLUG_SEGMENT_RE.fullmatch(seg) for seg in segments):
            return False
    return True


def _redact_pair(m: re.Match[str]) -> str:
    """`_CRED_PAIR_RE` callback: keep the id, redact the secret when cue or line position says so."""
    ident, secret = m.group("id"), m.group("secret")
    if ident.isdigit() or ident.lower() in _PATHISH_IDS:
        return m.group(0)  # `24/7`, `10/2026`, `Projects/tax-2026`
    if "@" not in ident and _HOSTNAME_ID_RE.fullmatch(ident):
        return m.group(0)  # scheme-less URL: `example.com/login2fa`
    core = secret.rstrip(_WRAP_CLOSE)
    text, start, end = m.string, m.start(), m.end()
    window = text[max(0, start - _CUE_WINDOW):start] + " " + text[end:end + _CUE_WINDOW]
    if _CRED_CUE_RE.search(window):
        redact_it = _pair_secret_shape(core, strict=False)
    elif _LINE_LEAD_RE.fullmatch(text[text.rfind("\n", 0, start) + 1:start]):
        redact_it = _pair_secret_shape(core, strict=True)
    else:
        redact_it = False
    return f"{ident}/{REDACTED_PASSWORD}" if redact_it else m.group(0)


def _redact_ws_cue(m: re.Match[str]) -> str:
    """`_PASSWORD_WS_RE` callback: keep the cue (and any "to"), redact a secret-looking token."""
    secret = m.group("secret")
    if REDACTED_PASSWORD in secret or not _looks_secret(secret):
        return m.group(0)
    return m.group("head") + REDACTED_PASSWORD


def redact(text: str) -> str:
    """Redact credential-looking material from a prompt before logging.

    Four conservative passes (specific → generic):
      (c) claim-URL path segments and `token=` query-param values;
      (a) tokens following password/passwd/pass/pwd/pw markers (also "pw to X");
      (d) the right-hand side of `<id>/<secret>` and `<email>/<secret>` pairs
          next to a credential cue, or leading a line;
      (b) long high-entropy runs (hex 32+, base64 32+, mixed-case blobs 20+).

    Normal prose, file paths, URLs and `kind:slug` handles pass through
    untouched, and a second run changes nothing. The signal writers reuse this
    function on trigger phrases and context, so it stays side-effect free.
    """
    if not text:
        return text
    text = _CLAIM_URL_RE.sub(rf"\g<1>{REDACTED_TOKEN}", text)
    text = _TOKEN_PARAM_RE.sub(rf"\g<1>{REDACTED_TOKEN}", text)
    text = _PASSWORD_SEP_RE.sub(rf"\g<1>\g<2>{REDACTED_PASSWORD}", text)
    text = _PASSWORD_WS_RE.sub(_redact_ws_cue, text)
    text = _CRED_PAIR_RE.sub(_redact_pair, text)
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
