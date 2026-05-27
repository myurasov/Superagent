# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/email/archive_hook.py` (PostToolUse bridge).

The bridge reads a JSON envelope from stdin (IDE hook surface), pulls
out the tool input + response, coerces both into archive-friendly
shapes, and calls into `tools/email/archive.py`. These tests verify the
parsing paths (dict / MCP-wrapped / text), the privacy gate, and the
never-block contract (always exits 0).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools.email import archive, archive_hook


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a workspace root with `_memory/` pre-created. Routes the
    hook's `.tmp` log to a tmp path so tests don't litter the framework
    .tmp directory."""
    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True)
    monkeypatch.setattr(archive_hook, "LOG_PATH", tmp_path / "hook.log")
    return workspace


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str,
    envelope: dict[str, Any] | str | None,
    workspace: Path,
) -> int:
    """Invoke `archive_hook.main` with stdin pre-filled and a workspace flag."""
    if envelope is None:
        stdin = ""
    elif isinstance(envelope, str):
        stdin = envelope
    else:
        stdin = json.dumps(envelope)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return archive_hook.main(["--kind", kind, "--workspace", str(workspace)])


def _privacy_off(workspace: Path) -> None:
    """Write a config that disables archive_emails."""
    config = workspace / "_memory" / "config.yaml"
    config.write_text(
        yaml.safe_dump({"preferences": {"privacy": {"archive_emails": False}}}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Sent capture
# ---------------------------------------------------------------------------


def test_sent_from_dict_response(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {
            "to": ["leon@example.com"],
            "cc": ["susan@example.com"],
            "subject": "Hi Leon",
            "body": "test body",
        },
        "tool_response": {"messageId": "outbound-1", "threadId": "t-out-1"},
    }
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    record = archive.find("outbound-1", workspace=ws)
    assert record is not None
    assert record.direction == "out"
    assert record.subject == "Hi Leon"
    assert "leon@example.com" in record.to


def test_sent_from_text_response_with_id_line(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """gongrzhe MCP returns 'Email sent successfully with ID: <id>'."""
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {
            "to": ["a@b.com"],
            "subject": "S",
            "body": "B",
        },
        "tool_response": "Email sent successfully with ID: outbound-2",
    }
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    record = archive.find("outbound-2", workspace=ws)
    assert record is not None
    assert record.direction == "out"


def test_sent_from_mcp_wrapped_content(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """MCP CallToolResult content array unwrapped to text."""
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {"to": ["x@y.com"], "subject": "S", "body": "B"},
        "tool_response": {
            "content": [
                {"type": "text", "text": "Email sent successfully with ID: outbound-3"}
            ]
        },
    }
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    assert archive.find("outbound-3", workspace=ws) is not None


def test_sent_missing_id_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {"to": ["x@y.com"], "subject": "S", "body": "B"},
        "tool_response": "something else with no id",
    }
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    # No record written.
    assert archive.stats(workspace=ws)["counts"]["total"] == 0


# ---------------------------------------------------------------------------
# Inbound (read) capture
# ---------------------------------------------------------------------------


def test_inbound_from_text_response(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """gongrzhe MCP returns a structured-text email body."""
    text = (
        "Thread ID: inbound-thread-1\n"
        "Subject: Hello there\n"
        "From: Alice <alice@example.com>\n"
        "To: me@example.com\n"
        "Date: Mon, 26 May 2026 04:29:16 -0600\n"
        "\n"
        "Body line one.\n"
        "Body line two.\n"
    )
    envelope = {
        "tool_name": "mcp__gmail__read_email",
        "tool_input": {"messageId": "inbound-1"},
        "tool_response": text,
    }
    assert _run(monkeypatch, kind="inbound", envelope=envelope, workspace=ws) == 0
    record = archive.find("inbound-thread-1", workspace=ws)
    assert record is not None
    assert record.subject == "Hello there"
    assert "alice@example.com" in record.from_


def test_inbound_from_dict_passthrough(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """If the response is already a Gmail-API message dict, pass through."""
    raw = {
        "id": "inbound-2",
        "threadId": "t-in-2",
        "labelIds": ["INBOX"],
        "internalDate": "1748256000000",
        "snippet": "snip",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Direct dict"},
                {"name": "From", "value": "bob@example.com"},
            ],
            "body": {"data": ""},
            "parts": [],
        },
    }
    envelope = {
        "tool_name": "mcp__gmail__read_email",
        "tool_input": {"messageId": "inbound-2"},
        "tool_response": raw,
    }
    assert _run(monkeypatch, kind="inbound", envelope=envelope, workspace=ws) == 0
    record = archive.find("inbound-2", workspace=ws)
    assert record is not None
    assert record.subject == "Direct dict"


def test_inbound_unparseable_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    envelope = {
        "tool_name": "mcp__gmail__read_email",
        "tool_input": {"messageId": "inbound-3"},
        "tool_response": "garbage with no headers",
    }
    assert _run(monkeypatch, kind="inbound", envelope=envelope, workspace=ws) == 0
    assert archive.stats(workspace=ws)["counts"]["total"] == 0


# ---------------------------------------------------------------------------
# Search stubs capture
# ---------------------------------------------------------------------------


def test_stubs_from_text_response(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    text = (
        "ID: stub-1\n"
        "Subject: First\n"
        "From: Alice <alice@example.com>\n"
        "Date: Mon, 18 May 2026 04:29:16 -0600\n"
        "\n"
        "ID: stub-2\n"
        "Subject: Second\n"
        "From: Bob <bob@example.com>\n"
        "Date: Mon, 19 May 2026 04:29:16 -0600\n"
    )
    envelope = {
        "tool_name": "mcp__gmail__search_emails",
        "tool_input": {"query": "from:example.com"},
        "tool_response": text,
    }
    assert _run(monkeypatch, kind="stubs", envelope=envelope, workspace=ws) == 0
    assert archive.find("stub-1", workspace=ws) is not None
    assert archive.find("stub-2", workspace=ws) is not None
    counts = archive.stats(workspace=ws)["counts"]
    assert counts["stubs"] == 2


def test_stubs_from_list_response(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    envelope = {
        "tool_name": "mcp__gmail__search_emails",
        "tool_input": {"query": "anything"},
        "tool_response": [
            {
                "id": "stub-list-1",
                "threadId": "t",
                "labelIds": [],
                "internalDate": "1748256000000",
                "snippet": "",
                "payload": {
                    "headers": [{"name": "Subject", "value": "L1"}],
                    "parts": [],
                },
            }
        ],
    }
    assert _run(monkeypatch, kind="stubs", envelope=envelope, workspace=ws) == 0
    assert archive.find("stub-list-1", workspace=ws) is not None


# ---------------------------------------------------------------------------
# Resilience: never block the parent tool call
# ---------------------------------------------------------------------------


def test_empty_stdin_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    assert _run(monkeypatch, kind="sent", envelope=None, workspace=ws) == 0


def test_malformed_stdin_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    assert _run(monkeypatch, kind="sent", envelope="not-json{", workspace=ws) == 0


def test_missing_workspace_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(archive_hook, "LOG_PATH", tmp_path / "hook.log")
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {"to": ["x"], "subject": "S", "body": "B"},
        "tool_response": "Email sent successfully with ID: zzz",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))
    code = archive_hook.main(
        ["--kind", "sent", "--workspace", str(tmp_path / "does-not-exist")]
    )
    assert code == 0


def test_privacy_gate_disables_capture(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    _privacy_off(ws)
    envelope = {
        "tool_name": "mcp__gmail__send_email",
        "tool_input": {"to": ["x"], "subject": "S", "body": "B"},
        "tool_response": "Email sent successfully with ID: should-not-store",
    }
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    assert archive.find("should-not-store", workspace=ws) is None


# ---------------------------------------------------------------------------
# Helper coverage: text parsers
# ---------------------------------------------------------------------------


def test_parse_send_response_text_picks_id() -> None:
    assert archive_hook._parse_send_response_text(
        "Email sent successfully with ID: ABC123"
    ) == {"messageId": "ABC123", "threadId": "ABC123"}


def test_parse_send_response_text_no_match() -> None:
    assert archive_hook._parse_send_response_text("hello world") == {}


def test_parse_read_response_text_headers_and_body() -> None:
    text = (
        "Thread ID: T1\n"
        "Subject: Hello\n"
        "From: a@b.com\n"
        "Date: Mon, 26 May 2026 04:29:16 -0600\n"
        "\n"
        "Body content here.\n"
    )
    raw = archive_hook._parse_read_response_text(text)
    assert raw is not None
    assert raw["id"] == "T1"
    headers = {h["name"]: h["value"] for h in raw["payload"]["headers"]}
    assert headers["Subject"] == "Hello"
    assert headers["From"] == "a@b.com"


def test_parse_search_response_text_blocks() -> None:
    text = (
        "ID: A\n"
        "Subject: One\n"
        "From: a@b.com\n"
        "Date: Mon, 18 May 2026 04:29:16 -0600\n"
        "\n"
        "ID: B\n"
        "Subject: Two\n"
        "From: c@d.com\n"
        "Date: Mon, 19 May 2026 04:29:16 -0600\n"
    )
    stubs = archive_hook._parse_search_response_text(text)
    assert [s["id"] for s in stubs] == ["A", "B"]


def test_flatten_mcp_content_unwraps_text() -> None:
    wrapped = {
        "content": [
            {"type": "text", "text": "hello world"},
        ]
    }
    assert archive_hook._flatten_mcp_content(wrapped) == "hello world"


def test_flatten_mcp_content_parses_json_text() -> None:
    wrapped = {
        "content": [
            {"type": "text", "text": json.dumps({"messageId": "x", "threadId": "y"})}
        ]
    }
    result = archive_hook._flatten_mcp_content(wrapped)
    assert isinstance(result, dict)
    assert result["messageId"] == "x"


def test_flatten_mcp_content_passthrough_for_plain_dict() -> None:
    plain = {"messageId": "x"}
    assert archive_hook._flatten_mcp_content(plain) == plain
