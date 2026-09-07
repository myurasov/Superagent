#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tool-call hook bridge for Gmail MCP -> local email archive.

Wires the Gmail MCP's `send_email` / `read_email` / `search_emails` calls
to the capture helpers in `superagent.tools.email.archive`. Backs
`contracts/email-capture.md`'s "capture-on-touch" rule under any harness
that exposes tool-call hooks.

Reads a JSON payload from stdin (the IDE's hook envelope), dispatches
to one capture function, and exits silently. The hook NEVER blocks the
parent tool call — every error path returns exit 0 and logs to
`~/.superagent/tmp/email_archive_hook.log` (machine-local root per
`rules/machine-local-home.md`) for offline diagnosis.

Privacy gate: `_memory/config.yaml.preferences.privacy.archive_emails`
defaults to true; setting it false disables capture entirely (the
hook reads the flag and exits silently when off).

CLI (hook-driven — stdin is the harness's hook envelope):
    uv run python -m superagent.tools.email.archive_hook --kind=sent
    uv run python -m superagent.tools.email.archive_hook --kind=inbound
    uv run python -m superagent.tools.email.archive_hook --kind=stubs
    uv run python -m superagent.tools.email.archive_hook --kind=auto

CLI (`--raw` — stdin is the MCP response itself, no envelope):
    ... | uv run python -m superagent.tools.email.archive_hook --kind=inbound --raw

`--raw` is the harness-independent path required by
`rules/email-capture-fallback.md`: a harness with no tool-call hooks
(generic AGENTS.md CLIs) still satisfies the capture-on-touch contract
because the agent pipes the response it already has in context. Capture
is idempotent, so running it after a hook already fired is a no-op.

Set `SUPERAGENT_EMAIL_HOOK_DEBUG=1` to dump each received envelope to the
log — the way to discover a harness's payload shape, since these schemas
are version-specific and thinly documented. Off by default: envelopes
carry message bodies.

The two harnesses disagree on every field name, so the bridge accepts
both. Claude Code `PostToolUse` (one hook per tool, selected by matcher
`mcp__gmail__<tool>`):

    {
      "tool_name": "mcp__gmail__send_email",
      "tool_input": {...kwargs passed to the MCP tool...},
      "tool_response": <dict, MCP content array, or plain text>,
      "session_id": "...", "cwd": "...", ... (other fields ignored)
    }

Cursor `afterMCPExecution` (ONE hook, no matcher, `--kind=auto`):

    {
      "tool_name": "read_email",
      "tool_input": "<JSON string of params>",
      "mcp_server_name": "gmail",
      "result_json": "<JSON string of the tool response>",
      "duration": 1234
    }

Cursor's `postToolUse` is also accepted (`tool_output` instead of
`result_json`, `tool_name` in `MCP:<tool>` form) for harnesses or cloud
agents where `afterMCPExecution` is unavailable.

The hook tolerates schema drift: missing fields are treated as empty;
unexpected types are coerced when feasible and skipped otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.email import archive

EXIT_OK = 0  # always; we never block the parent tool call.

FRAMEWORK_ROOT = Path(__file__).resolve().parents[3]


def _log_path() -> Path:
    """Diagnostic log under the machine-local root (never inside the repo)."""
    from superagent.tools.home import tmp_dir

    return tmp_dir(ensure=False) / "email_archive_hook.log"


def _log(message: str) -> None:
    """Append a diagnostic line to the machine-local hook log. Never raises."""
    try:
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}  {message}\n")
    except OSError:
        pass


def _resolve_workspace(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return FRAMEWORK_ROOT / "workspace"


def _privacy_enabled(workspace: Path) -> bool:
    """Read `preferences.privacy.archive_emails`; default to true."""
    config = workspace / "_memory" / "config.yaml"
    if not config.exists():
        return True
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return True
    prefs = (data.get("preferences") or {}) if isinstance(data, dict) else {}
    privacy = (prefs.get("privacy") or {}) if isinstance(prefs, dict) else {}
    return bool(privacy.get("archive_emails", True))


def _read_envelope() -> dict[str, Any]:
    """Parse the JSON envelope on stdin. Returns {} on any failure."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _log(f"envelope: not JSON ({len(raw)} bytes); raw head={raw[:120]!r}")
        return {}
    if not isinstance(payload, dict):
        _log(f"envelope: non-dict ({type(payload).__name__})")
        return {}
    return payload


def _maybe_json(value: Any) -> Any:
    """Parse `value` when it is a JSON-encoded string; else return it as-is.

    Cursor hands the payload over as a JSON-stringified string
    (`tool_output` on `postToolUse`, `result_json` on `afterMCPExecution`)
    where Claude Code hands over a live object. The gongrzhe Gmail MCP's
    own payload is plain text, so a string that does not start with `{`
    or `[` is returned untouched.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _raw_envelope(args: argparse.Namespace) -> dict[str, Any]:
    """Wrap a bare tool response from stdin in a synthetic hook envelope.

    Backs `--raw`, the harness-independent capture path used when no
    tool-call hook exists (generic AGENTS.md CLIs) or when a hook is
    suspected inert. Every downstream parser already accepts the response
    shapes the Gmail MCP produces, so the only work here is presenting the
    payload the way the dispatchers expect. Capture is idempotent, so
    running this after a hook already fired is a safe no-op.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        _log(f"{args.kind}: --raw got empty stdin; skipping capture")
        return {}
    envelope: dict[str, Any] = {
        "hook_event_name": "raw",
        "tool_name": f"raw:{args.kind}",
        "tool_response": raw,
    }
    if args.request_json:
        try:
            envelope["tool_input"] = json.loads(args.request_json)
        except json.JSONDecodeError as exc:
            _log(f"{args.kind}: --request-json is not valid JSON: {exc}")
    return envelope


def _extract_response(envelope: dict[str, Any]) -> Any:
    """Pull the tool's response payload out of the IDE envelope.

    Hook envelopes vary across IDEs and versions; accept any of:
      tool_response  (Claude Code PostToolUse)
      result_json    (Cursor afterMCPExecution -- JSON string)
      tool_output    (Cursor postToolUse -- JSON string)
      response / output (generic fallbacks)
    """
    for key in ("tool_response", "result_json", "tool_output", "response", "output"):
        if key in envelope:
            return _maybe_json(envelope[key])
    return None


def _flatten_mcp_content(value: Any) -> Any:
    """Unwrap MCP `CallToolResult.content` to a usable dict / string.

    MCP servers may wrap responses as `{"content": [{"type": "text",
    "text": "..."}, ...]}`. Some IDE hook envelopes strip the outer dict
    and deliver the content array bare. Pull the text out so downstream
    parsers can operate on it. If the value isn't wrapped, return as-is.
    """
    if isinstance(value, list) and any(
        isinstance(part, dict) and isinstance(part.get("text"), str)
        for part in value
    ):
        value = {"content": value}
    if isinstance(value, dict) and "content" in value:
        parts = value.get("content") or []
        if isinstance(parts, list):
            texts: list[str] = []
            for part in parts:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        texts.append(text)
            if texts:
                joined = "\n".join(texts)
                # Try to parse the unwrapped text as JSON; many servers
                # serialize the structured payload as a JSON string.
                stripped = joined.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        return json.loads(stripped)
                    except json.JSONDecodeError:
                        return joined
                return joined
    return value


# ---------------------------------------------------------------------------
# Text-format parsers (gongrzhe Gmail MCP returns text rather than JSON)
# ---------------------------------------------------------------------------

_SEND_ID_RE = re.compile(
    r"(?:message\s*ID|ID|Message\s*Id)\s*[:=]\s*([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
_HEADER_LINE_RE = re.compile(r"^([A-Za-z][\w\- ]*):\s*(.*)$")
_SEARCH_BLOCK_SPLIT_RE = re.compile(r"\n\s*\n+")


def _parse_send_response_text(text: str) -> dict[str, Any]:
    """Pull `{messageId, threadId}` out of a gongrzhe send_email text.

    Example: "Email sent successfully with ID: 19e6358378c02382".
    Returns {} when no ID is found.
    """
    match = _SEND_ID_RE.search(text)
    if not match:
        return {}
    msg_id = match.group(1)
    return {"messageId": msg_id, "threadId": msg_id}


def _parse_read_response_text(text: str) -> dict[str, Any] | None:
    """Parse gongrzhe read_email text into a Gmail-API-shaped dict.

    Expected layout (whitespace-tolerant):
        Thread ID: <id>
        Subject: <subj>
        From: <addr>
        To: <addr>[, ...]
        [Cc: ...]
        Date: <RFC 2822 date>

        <body...>

        Attachments (N):
        - <name> (<mime>, <size>, ID: <attach-id>)

    Missing sections are handled gracefully. Returns None if no
    Thread/Message ID can be recovered.
    """
    lines = text.splitlines()
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    saw_blank = False
    in_attachments = False
    attachments: list[dict[str, str]] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if in_attachments:
            if not line.strip():
                continue
            attachments.append({"filename": line.strip().lstrip("- ")})
            continue
        if saw_blank:
            # In body region until we hit an "Attachments (N):" line.
            if line.startswith("Attachments (") or line.startswith("Attachments:"):
                in_attachments = True
                continue
            body_lines.append(raw_line)
            continue
        if not line.strip():
            if headers:
                saw_blank = True
            continue
        match = _HEADER_LINE_RE.match(line)
        if match:
            key = match.group(1).strip().lower()
            value = match.group(2).strip()
            if key and value and key not in headers:
                headers[key] = value
            continue
        # Unstructured line before headers complete -> treat as body.
        if headers:
            saw_blank = True
            body_lines.append(raw_line)

    message_id = headers.get("message id") or headers.get("id")
    thread_id = headers.get("thread id") or message_id
    if not message_id and thread_id:
        message_id = thread_id
    if not message_id:
        return None

    gmail_headers: list[dict[str, str]] = []
    header_map = {
        "subject": "Subject",
        "from": "From",
        "to": "To",
        "cc": "Cc",
        "bcc": "Bcc",
        "date": "Date",
        "reply-to": "Reply-To",
    }
    for key, name in header_map.items():
        value = headers.get(key)
        if value:
            gmail_headers.append({"name": name, "value": value})

    internal_date_ms: str | None = None
    date_str = headers.get("date")
    if date_str:
        parsed = _parse_rfc2822_date(date_str)
        if parsed is not None:
            internal_date_ms = str(int(parsed.timestamp() * 1000))

    parts: list[dict[str, Any]] = []
    for attachment in attachments:
        parts.append({"filename": attachment.get("filename", "")})

    return {
        "id": message_id,
        "threadId": thread_id or "",
        "labelIds": ["INBOX"],
        "internalDate": internal_date_ms or "",
        "snippet": (" ".join(body_lines)[:200]).strip(),
        "payload": {
            "headers": gmail_headers,
            "body": {"data": "\n".join(body_lines)},
            "parts": parts,
        },
        "_raw_text": text,
    }


def _parse_search_response_text(text: str) -> list[dict[str, Any]]:
    """Parse gongrzhe search_emails output into a list of stub dicts.

    Each result block is separated by a blank line and looks like:
        ID: 19e3aa2056fb00b8
        Subject: MyCoverageInfo Information Received
        From: MyCoverageInfo <noreply@em.mycoverageinfo.com>
        Date: Mon, 18 May 2026 04:29:16 -0600
    """
    stubs: list[dict[str, Any]] = []
    blocks = _SEARCH_BLOCK_SPLIT_RE.split(text.strip())
    for block in blocks:
        headers: dict[str, str] = {}
        for line in block.splitlines():
            match = _HEADER_LINE_RE.match(line.strip())
            if match:
                key = match.group(1).strip().lower()
                value = match.group(2).strip()
                if value and key not in headers:
                    headers[key] = value
        msg_id = headers.get("id") or headers.get("message id")
        if not msg_id:
            continue
        gmail_headers = []
        for key, name in (
            ("subject", "Subject"),
            ("from", "From"),
            ("to", "To"),
            ("date", "Date"),
        ):
            value = headers.get(key)
            if value:
                gmail_headers.append({"name": name, "value": value})
        internal_date_ms: str | None = None
        if headers.get("date"):
            parsed = _parse_rfc2822_date(headers["date"])
            if parsed is not None:
                internal_date_ms = str(int(parsed.timestamp() * 1000))
        stubs.append(
            {
                "id": msg_id,
                "threadId": msg_id,
                "labelIds": [],
                "internalDate": internal_date_ms or "",
                "snippet": "",
                "payload": {"headers": gmail_headers, "parts": []},
            }
        )
    return stubs


def _parse_rfc2822_date(value: str) -> dt.datetime | None:
    """RFC 2822 -> aware datetime (UTC). Returns None on failure."""
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


# ---------------------------------------------------------------------------
# Coercion helpers — accept dict OR text response shapes
# ---------------------------------------------------------------------------


def _coerce_send_response(response: Any) -> dict[str, Any]:
    """Return a dict with at least `messageId` if extractable, else {}."""
    flat = _flatten_mcp_content(response)
    if isinstance(flat, dict):
        # Already-structured server response — pass through as-is.
        return flat
    if isinstance(flat, str):
        return _parse_send_response_text(flat)
    return {}


def _coerce_read_response(response: Any) -> dict[str, Any] | None:
    """Return a Gmail-API-shaped raw message dict, or None on parse failure."""
    flat = _flatten_mcp_content(response)
    if isinstance(flat, dict):
        # Two shapes possible: a Gmail-API message dict (carries id +
        # payload) or a server wrapper. Detect by presence of `id`.
        if "id" in flat and ("payload" in flat or "snippet" in flat):
            return flat
        # Server wrapper -> try the text field if any.
        text = flat.get("text") or flat.get("body") or flat.get("message")
        if isinstance(text, str):
            return _parse_read_response_text(text)
        return None
    if isinstance(flat, str):
        return _parse_read_response_text(flat)
    return None


def _coerce_search_response(response: Any) -> list[dict[str, Any]]:
    flat = _flatten_mcp_content(response)
    if isinstance(flat, list):
        # Already a list of stub dicts.
        return [item for item in flat if isinstance(item, dict)]
    if isinstance(flat, dict):
        # Common wrapper: {"messages": [...]} or {"results": [...]}.
        for key in ("messages", "results", "items"):
            items = flat.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []
    if isinstance(flat, str):
        return _parse_search_response_text(flat)
    return []


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_sent(envelope: dict[str, Any], workspace: Path) -> int:
    request = _maybe_json(envelope.get("tool_input")) or {}
    if not isinstance(request, dict):
        _log(f"sent: tool_input not a dict ({type(request).__name__})")
        return EXIT_OK
    response = _coerce_send_response(_extract_response(envelope))
    if not response.get("messageId") and not response.get("id"):
        _log("sent: response missing messageId; skipping capture")
        return EXIT_OK
    try:
        result = archive.capture_sent(request, response, workspace=workspace)
    except (ValueError, TypeError, OSError) as exc:
        _log(f"sent: capture_sent raised {type(exc).__name__}: {exc}")
        return EXIT_OK
    _log(f"sent: {result.action} id={result.record.id} path={result.path}")
    return EXIT_OK


def _dispatch_inbound(envelope: dict[str, Any], workspace: Path) -> int:
    raw = _coerce_read_response(_extract_response(envelope))
    if raw is None:
        _log("inbound: could not parse response; skipping capture")
        return EXIT_OK
    if not raw.get("id"):
        _log("inbound: response lacks id; skipping capture")
        return EXIT_OK
    try:
        result = archive.capture_inbound(raw, workspace=workspace)
    except (ValueError, TypeError, OSError) as exc:
        _log(f"inbound: capture_inbound raised {type(exc).__name__}: {exc}")
        return EXIT_OK
    _log(f"inbound: {result.action} id={result.record.id} path={result.path}")
    return EXIT_OK


def _dispatch_stubs(envelope: dict[str, Any], workspace: Path) -> int:
    stubs = _coerce_search_response(_extract_response(envelope))
    if not stubs:
        _log("stubs: no parseable stubs in response; skipping capture")
        return EXIT_OK
    try:
        results = archive.maybe_capture_stubs(stubs, workspace=workspace)
    except (ValueError, TypeError, OSError) as exc:
        _log(f"stubs: maybe_capture_stubs raised {type(exc).__name__}: {exc}")
        return EXIT_OK
    created = sum(1 for r in results if r.action == "created")
    _log(f"stubs: {created} created of {len(results)} processed")
    return EXIT_OK


_DISPATCH = {
    "sent": _dispatch_sent,
    "inbound": _dispatch_inbound,
    "stubs": _dispatch_stubs,
}

# Gmail MCP tool leaf name -> capture path. Matched as a substring of the
# envelope's `tool_name`, because each harness decorates the leaf
# differently: Claude Code sends `mcp__gmail__read_email`, Cursor sends
# `read_email` on `afterMCPExecution` and `MCP:read_email` on
# `postToolUse`. Longest key first so `search_emails` cannot be shadowed.
_TOOL_NAME_KINDS: tuple[tuple[str, str], ...] = (
    ("search_emails", "stubs"),
    ("send_email", "sent"),
    ("read_email", "inbound"),
)


def _infer_kind(envelope: dict[str, Any]) -> str | None:
    """Resolve the capture path from the envelope's tool name.

    Used by `--kind=auto`, which lets a single hook registration cover all
    three Gmail tools. Cursor's `afterMCPExecution` takes no matcher, so
    the filtering that Claude Code does with three matchers happens here
    instead. Returns None when the call is not a Gmail capture trigger.
    """
    tool_name = envelope.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    for leaf, kind in _TOOL_NAME_KINDS:
        if leaf in tool_name:
            return kind
    return None


def _wrong_server(envelope: dict[str, Any]) -> bool:
    """True when the envelope names an MCP server that is not Gmail.

    `afterMCPExecution` fires for EVERY MCP server, and a non-Gmail server
    is free to expose its own `read_email`. `mcp_server_name` is the
    server's key in `mcp.json`, so it is the authoritative discriminator.
    Absent (Claude Code, Cursor `postToolUse`) means "cannot tell" and is
    treated as a pass -- the tool-name match already narrowed it.
    """
    server = envelope.get("mcp_server_name")
    if not isinstance(server, str) or not server:
        return False
    return "gmail" not in server.lower()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="email-archive-hook",
        description=(
            "PostToolUse bridge: feed Gmail MCP tool calls into the local "
            "email archive (workspace/_memory/email/)."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=(*_DISPATCH.keys(), "auto"),
        required=True,
        help=(
            "Which capture path to dispatch: sent | inbound | stubs, or "
            "auto to resolve it from the envelope's tool_name (required "
            "for Cursor's afterMCPExecution, which takes no matcher)."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Override workspace path (default: <framework>/../workspace).",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Treat stdin as the MCP tool response itself rather than a hook "
            "envelope. This is the no-hook path: on a harness with no "
            "tool-call hooks, the agent pipes the response it already has in "
            "context straight in. Requires an explicit --kind."
        ),
    )
    parser.add_argument(
        "--request-json",
        type=str,
        default=None,
        help=(
            "JSON object of the arguments passed to send_email. Only used "
            "with --raw --kind=sent, where the sent body cannot be "
            "recovered from Gmail's minimal response."
        ),
    )
    args = parser.parse_args(argv)
    if args.raw and args.kind == "auto":
        parser.error("--raw needs an explicit --kind (auto reads the envelope's tool_name)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    workspace = _resolve_workspace(args.workspace)
    if not workspace.exists():
        _log(f"{args.kind}: workspace missing at {workspace}; skipping")
        return EXIT_OK
    if not _privacy_enabled(workspace):
        return EXIT_OK
    try:
        envelope = _raw_envelope(args) if args.raw else _read_envelope()
    except (OSError, ValueError) as exc:
        _log(f"{args.kind}: stdin read failed: {exc}")
        return EXIT_OK
    if not envelope:
        return EXIT_OK
    if os.environ.get("SUPERAGENT_EMAIL_HOOK_DEBUG"):
        # Schema-discovery aid: hook payload shapes are version-specific and
        # thinly documented, so dump the envelope keys (and the raw envelope
        # itself) when explicitly asked. Values can carry message bodies, so
        # this is opt-in and never on by default.
        _log(f"debug: event={envelope.get('hook_event_name')!r} keys={sorted(envelope)}")
        _log(f"debug: envelope={json.dumps(envelope, default=str)[:4000]}")

    kind = args.kind
    if kind == "auto":
        if _wrong_server(envelope):
            return EXIT_OK
        resolved = _infer_kind(envelope)
        if resolved is None:
            return EXIT_OK
        kind = resolved
        _log(f"auto: tool={envelope.get('tool_name')!r} -> {kind}")

    try:
        return _DISPATCH[kind](envelope, workspace)
    except Exception:  # noqa: BLE001
        _log(f"{kind}: unhandled exception:\n{traceback.format_exc()}")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
