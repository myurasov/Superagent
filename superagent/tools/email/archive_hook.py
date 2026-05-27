#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""PostToolUse hook bridge for Gmail MCP -> local email archive.

Wires the `mcp__gmail__send_email` / `mcp__gmail__read_email` /
`mcp__gmail__search_emails` tool calls to the capture helpers in
`superagent.tools.email.archive`. Backs `contracts/email-capture.md`'s
"capture-on-touch" rule under IDEs that expose tool-call hooks
(Claude Code's `PostToolUse`; Cursor's equivalent).

Reads a JSON payload from stdin (the IDE's hook envelope), dispatches
to one capture function, and exits silently. The hook NEVER blocks the
parent tool call — every error path returns exit 0 and logs to
`.tmp/email_archive_hook.log` in the framework root for offline
diagnosis.

Privacy gate: `_memory/config.yaml.preferences.privacy.archive_emails`
defaults to true; setting it false disables capture entirely (the
hook reads the flag and exits silently when off).

CLI:
    uv run python -m superagent.tools.email.archive_hook --kind=sent
    uv run python -m superagent.tools.email.archive_hook --kind=inbound
    uv run python -m superagent.tools.email.archive_hook --kind=stubs

Stdin envelope (Claude Code / Cursor PostToolUse shape):
    {
      "tool_name": "mcp__gmail__send_email",
      "tool_input": {...kwargs passed to the MCP tool...},
      "tool_response": <MCP response: dict, MCP-wrapped content array,
                        or plain text - the bridge handles all three>,
      "session_id": "...", "cwd": "...", ... (other fields ignored)
    }

The hook tolerates schema drift: missing fields are treated as empty;
unexpected types are coerced when feasible and skipped otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.email import archive

EXIT_OK = 0  # always; we never block the parent tool call.

FRAMEWORK_ROOT = Path(__file__).resolve().parents[3]
LOG_PATH = FRAMEWORK_ROOT / ".tmp" / "email_archive_hook.log"


def _log(message: str) -> None:
    """Append a diagnostic line to `.tmp/email_archive_hook.log`. Never raises."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
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


def _extract_response(envelope: dict[str, Any]) -> Any:
    """Pull the tool's response payload out of the IDE envelope.

    Hook envelopes vary across IDEs and versions; accept any of:
      tool_response, tool_output, response, output.
    """
    for key in ("tool_response", "tool_output", "response", "output"):
        if key in envelope:
            return envelope[key]
    return None


def _flatten_mcp_content(value: Any) -> Any:
    """Unwrap MCP `CallToolResult.content` to a usable dict / string.

    MCP servers may wrap responses as `{"content": [{"type": "text",
    "text": "..."}, ...]}`. Pull the text out so downstream parsers can
    operate on it. If the value isn't wrapped, return as-is.
    """
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
    request = envelope.get("tool_input") or {}
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
        choices=tuple(_DISPATCH.keys()),
        required=True,
        help="Which capture path to dispatch: sent | inbound | stubs.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Override workspace path (default: <framework>/../workspace).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    workspace = _resolve_workspace(args.workspace)
    if not workspace.exists():
        _log(f"{args.kind}: workspace missing at {workspace}; skipping")
        return EXIT_OK
    if not _privacy_enabled(workspace):
        return EXIT_OK
    try:
        envelope = _read_envelope()
    except (OSError, ValueError) as exc:
        _log(f"{args.kind}: stdin read failed: {exc}")
        return EXIT_OK
    if not envelope:
        return EXIT_OK
    try:
        return _DISPATCH[args.kind](envelope, workspace)
    except Exception:  # noqa: BLE001
        _log(f"{args.kind}: unhandled exception:\n{traceback.format_exc()}")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
