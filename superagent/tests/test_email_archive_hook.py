# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/email/archive_hook.py` (tool-call hook bridge).

The bridge reads a JSON envelope from stdin (IDE hook surface), pulls
out the tool input + response, coerces both into archive-friendly
shapes, and calls into `tools/email/archive.py`. These tests verify the
parsing paths (dict / MCP-wrapped / text), the privacy gate, and the
never-block contract (always exits 0).

They also pin the CROSS-HARNESS surface, because every harness names
these fields differently and a mismatch fails silently: Claude Code
sends `tool_response`, Cursor sends a JSON-stringified `result_json`
(`afterMCPExecution`) or `tool_output` (`postToolUse`). A `.cursor/hooks.json`
written in Claude Code's schema went unnoticed for weeks, so the matrix
below is a regression guard, and `--raw` (the no-hook path required by
`rules/email-capture-fallback.md`) is covered alongside it.
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
    hook's diagnostic log (machine-local root) to a tmp path so tests
    don't touch the real ~/.superagent/."""
    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True)
    monkeypatch.setenv("SUPERAGENT_HOME", str(tmp_path / "sa-home"))
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
    monkeypatch.setenv("SUPERAGENT_HOME", str(tmp_path / "sa-home"))
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


# ---------------------------------------------------------------------------
# Cross-harness envelope shapes (--kind=auto)
# ---------------------------------------------------------------------------


def _run_argv(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: str
) -> int:
    """Invoke `archive_hook.main` with an arbitrary argv and stdin."""
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return archive_hook.main(argv)


def _read_text(message_id: str, subject: str = "Cross-harness") -> str:
    """A gongrzhe-shaped read_email response body."""
    return (
        f"Thread ID: {message_id}\n"
        f"Subject: {subject}\n"
        "From: Probe <probe@example.com>\n"
        "To: me@example.com\n"
        "Date: Sun, 6 Sep 2026 18:00:00 -0700\n"
        "\n"
        "Body.\n"
    )


def _mcp_result_json(text: str) -> str:
    """Cursor hands the MCP result over as a JSON-encoded STRING."""
    return json.dumps({"content": [{"type": "text", "text": text}], "isError": False})


def test_auto_claude_code_prefixed_tool_name(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """Claude Code's `mcp__gmail__read_email` resolves to the inbound path."""
    envelope = {
        "tool_name": "mcp__gmail__read_email",
        "tool_input": {"messageId": "auto-claude"},
        "tool_response": _read_text("auto-claude"),
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.find("auto-claude", workspace=ws) is not None


def test_auto_cursor_after_mcp_execution(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """Cursor `afterMCPExecution`: bare tool name, JSON-string `result_json`."""
    envelope = {
        "hook_event_name": "afterMCPExecution",
        "tool_name": "read_email",
        "tool_input": json.dumps({"messageId": "auto-cursor-mcp"}),
        "mcp_server_name": "gmail",
        "result_json": _mcp_result_json(_read_text("auto-cursor-mcp")),
        "duration": 812,
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.find("auto-cursor-mcp", workspace=ws) is not None


def test_auto_cursor_post_tool_use(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """Cursor `postToolUse`: `MCP:` prefix, JSON-string `tool_output`."""
    envelope = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:read_email",
        "tool_input": {"messageId": "auto-cursor-ptu"},
        "tool_output": _mcp_result_json(_read_text("auto-cursor-ptu")),
        "cwd": "/project",
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.find("auto-cursor-ptu", workspace=ws) is not None


def test_auto_resolves_search_and_send(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """`search_emails` must not be shadowed by the `send_email` key."""
    search = {
        "tool_name": "search_emails",
        "mcp_server_name": "gmail",
        "tool_input": json.dumps({"query": "x"}),
        "result_json": _mcp_result_json(
            "ID: auto-stub\n"
            "Subject: Stub\n"
            "From: Probe <probe@example.com>\n"
            "Date: Sun, 6 Sep 2026 18:01:00 -0700\n"
        ),
    }
    assert _run(monkeypatch, kind="auto", envelope=search, workspace=ws) == 0
    stub = archive.find("auto-stub", workspace=ws)
    assert stub is not None and stub.kind == "stub"

    send = {
        "tool_name": "send_email",
        "mcp_server_name": "gmail",
        "tool_input": json.dumps({"to": ["x@y.com"], "subject": "S", "body": "B"}),
        "result_json": _mcp_result_json("Email sent successfully with ID: auto-sent"),
    }
    assert _run(monkeypatch, kind="auto", envelope=send, workspace=ws) == 0
    sent = archive.find("auto-sent", workspace=ws)
    assert sent is not None and sent.direction == "out"


def test_auto_skips_other_mcp_server(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """`afterMCPExecution` fires for every server; only Gmail is captured."""
    envelope = {
        "tool_name": "read_email",
        "mcp_server_name": "some-other-server",
        "tool_input": json.dumps({"messageId": "foreign"}),
        "result_json": _mcp_result_json(_read_text("foreign")),
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.find("foreign", workspace=ws) is None


def test_auto_skips_unrelated_gmail_tool(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """Only the three capture triggers dispatch; other Gmail tools no-op."""
    envelope = {
        "tool_name": "list_email_labels",
        "mcp_server_name": "gmail",
        "tool_input": "{}",
        "result_json": _mcp_result_json("INBOX\nSENT\n"),
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.stats(workspace=ws)["counts"]["total"] == 0


def test_auto_missing_tool_name_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    envelope = {"tool_response": _read_text("no-tool-name")}
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert archive.stats(workspace=ws)["counts"]["total"] == 0


# ---------------------------------------------------------------------------
# No-hook fallback (--raw) -- rules/email-capture-fallback.md
# ---------------------------------------------------------------------------


def test_raw_inbound_without_envelope(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """`--raw` takes the MCP response itself, so no harness support is needed."""
    code = _run_argv(
        monkeypatch,
        ["--kind", "inbound", "--raw", "--workspace", str(ws)],
        _read_text("raw-inbound"),
    )
    assert code == 0
    record = archive.find("raw-inbound", workspace=ws)
    assert record is not None
    assert record.kind == "full"


def test_raw_stubs_without_envelope(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    code = _run_argv(
        monkeypatch,
        ["--kind", "stubs", "--raw", "--workspace", str(ws)],
        "ID: raw-stub\n"
        "Subject: Raw stub\n"
        "From: Probe <probe@example.com>\n"
        "Date: Sun, 6 Sep 2026 18:02:00 -0700\n",
    )
    assert code == 0
    assert archive.find("raw-stub", workspace=ws) is not None


def test_raw_sent_uses_request_json_for_body(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gmail's send response is minimal, so `--request-json` carries the body."""
    code = _run_argv(
        monkeypatch,
        [
            "--kind", "sent", "--raw", "--workspace", str(ws),
            "--request-json",
            json.dumps({"to": ["dest@example.com"], "subject": "Raw sent", "body": "B"}),
        ],
        "Email sent successfully with ID: raw-sent",
    )
    assert code == 0
    assert capsys.readouterr().err == ""
    record = archive.find("raw-sent", workspace=ws)
    assert record is not None
    assert record.direction == "out"
    assert record.subject == "Raw sent"
    assert "dest@example.com" in record.to


def _hook_log_text() -> str:
    """The machine-local diagnostic log (routed under tmp by the `ws` fixture)."""
    path = archive_hook._log_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_raw_sent_malformed_request_json_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--raw` is agent-invoked: a bad `--request-json` must not silently
    archive a content-less sent record that `archive find` then vouches for."""
    code = _run_argv(
        monkeypatch,
        [
            "--kind", "sent", "--raw", "--workspace", str(ws),
            "--request-json", "{bad",
        ],
        "Email sent successfully with ID: raw-sent-bad-json",
    )
    assert code == archive_hook.EXIT_USAGE == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert "re-run" in err
    assert archive.find("raw-sent-bad-json", workspace=ws) is None
    assert archive.stats(workspace=ws)["counts"]["total"] == 0
    assert "not valid JSON" in _hook_log_text()


def test_raw_sent_missing_request_json_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Omitting the flag produces the same content-less record; same refusal."""
    code = _run_argv(
        monkeypatch,
        ["--kind", "sent", "--raw", "--workspace", str(ws)],
        "Email sent successfully with ID: raw-sent-no-json",
    )
    assert code == 2
    assert "--request-json" in capsys.readouterr().err
    assert archive.find("raw-sent-no-json", workspace=ws) is None


def test_raw_sent_non_object_request_json_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run_argv(
        monkeypatch,
        [
            "--kind", "sent", "--raw", "--workspace", str(ws),
            "--request-json", "[1, 2]",
        ],
        "Email sent successfully with ID: raw-sent-list",
    )
    assert code == 2
    assert "JSON object" in capsys.readouterr().err
    assert archive.find("raw-sent-list", workspace=ws) is None


def test_raw_inbound_ignores_malformed_request_json(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--request-json` only matters for sent; other kinds log and proceed."""
    code = _run_argv(
        monkeypatch,
        [
            "--kind", "inbound", "--raw", "--workspace", str(ws),
            "--request-json", "{bad",
        ],
        _read_text("raw-inbound-badreq"),
    )
    assert code == 0
    assert capsys.readouterr().err == ""
    assert archive.find("raw-inbound-badreq", workspace=ws) is not None
    assert "not valid JSON" in _hook_log_text()


def test_hook_sent_without_tool_input_stays_silent(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal is `--raw`-only: a harness envelope lacking tool_input
    must never block the parent tool call or write to stderr."""
    envelope = {"tool_response": "Email sent successfully with ID: hook-no-input"}
    assert _run(monkeypatch, kind="sent", envelope=envelope, workspace=ws) == 0
    assert capsys.readouterr().err == ""


def test_debug_env_dumps_envelope_and_still_captures(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """`SUPERAGENT_EMAIL_HOOK_DEBUG=1` writes two `debug:` lines per envelope."""
    monkeypatch.setenv("SUPERAGENT_EMAIL_HOOK_DEBUG", "1")
    envelope = {
        "hook_event_name": "afterMCPExecution",
        "tool_name": "read_email",
        "mcp_server_name": "gmail",
        "result_json": _mcp_result_json(_read_text("debug-dump")),
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    debug_lines = [ln for ln in _hook_log_text().splitlines() if "  debug: " in ln]
    assert len(debug_lines) == 2
    assert "event='afterMCPExecution'" in debug_lines[0]
    assert "keys=" in debug_lines[0]
    assert "envelope=" in debug_lines[1]
    assert archive.find("debug-dump", workspace=ws) is not None


def test_debug_off_by_default_writes_no_dump(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    monkeypatch.delenv("SUPERAGENT_EMAIL_HOOK_DEBUG", raising=False)
    envelope = {"tool_name": "mcp__gmail__read_email", "tool_response": _read_text("no-debug")}
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    assert "debug:" not in _hook_log_text()


def test_raw_is_idempotent_after_a_hook_capture(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    """The fallback is unconditional, so double-capture must not duplicate.

    This is the property that lets the rule say "always capture" instead of
    asking the agent to detect whether a hook fired.
    """
    text = _read_text("both-paths")
    envelope = {
        "hook_event_name": "afterMCPExecution",
        "tool_name": "read_email",
        "mcp_server_name": "gmail",
        "result_json": _mcp_result_json(text),
    }
    assert _run(monkeypatch, kind="auto", envelope=envelope, workspace=ws) == 0
    first = archive.stats(workspace=ws)["counts"]["total"]

    code = _run_argv(
        monkeypatch, ["--kind", "inbound", "--raw", "--workspace", str(ws)], text
    )
    assert code == 0
    assert archive.stats(workspace=ws)["counts"]["total"] == first
    assert archive.find("both-paths", workspace=ws) is not None


def test_raw_empty_stdin_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    code = _run_argv(
        monkeypatch, ["--kind", "inbound", "--raw", "--workspace", str(ws)], ""
    )
    assert code == 0
    assert archive.stats(workspace=ws)["counts"]["total"] == 0


def test_raw_with_auto_is_rejected(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    """`auto` reads the envelope's tool_name, which `--raw` does not have."""
    with pytest.raises(SystemExit) as excinfo:
        _run_argv(
            monkeypatch, ["--kind", "auto", "--raw", "--workspace", str(ws)], "x"
        )
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Helper coverage: JSON-string coercion
# ---------------------------------------------------------------------------


def test_maybe_json_parses_encoded_object() -> None:
    assert archive_hook._maybe_json('{"a": 1}') == {"a": 1}


def test_maybe_json_leaves_plain_text_alone() -> None:
    """The Gmail MCP's own payload is plain text, not JSON."""
    text = "Thread ID: T1\nSubject: Hello\n"
    assert archive_hook._maybe_json(text) == text


def test_maybe_json_leaves_non_strings_alone() -> None:
    payload = {"already": "an object"}
    assert archive_hook._maybe_json(payload) is payload


def test_maybe_json_survives_malformed_json() -> None:
    assert archive_hook._maybe_json('{"broken": ') == '{"broken": '


def test_extract_response_prefers_claude_then_cursor_keys() -> None:
    assert archive_hook._extract_response({"tool_response": "a"}) == "a"
    assert archive_hook._extract_response({"result_json": '{"b": 1}'}) == {"b": 1}
    assert archive_hook._extract_response({"tool_output": '{"c": 2}'}) == {"c": 2}
    # Generic fallbacks for harnesses with no documented envelope.
    assert archive_hook._extract_response({"response": '{"d": 3}'}) == {"d": 3}
    assert archive_hook._extract_response({"output": "e"}) == "e"
    assert archive_hook._extract_response({"unrelated": "x"}) is None


def test_extract_response_precedence_when_several_keys_present() -> None:
    """Claude Code's key wins over Cursor's, which wins over the generics."""
    envelope = {
        "output": "generic",
        "response": "generic2",
        "tool_output": "cursor-post",
        "result_json": "cursor-after",
        "tool_response": "claude",
    }
    assert archive_hook._extract_response(envelope) == "claude"
    del envelope["tool_response"]
    assert archive_hook._extract_response(envelope) == "cursor-after"
    del envelope["result_json"]
    assert archive_hook._extract_response(envelope) == "cursor-post"
    del envelope["tool_output"]
    assert archive_hook._extract_response(envelope) == "generic2"
    del envelope["response"]
    assert archive_hook._extract_response(envelope) == "generic"


def test_wrong_server_only_rejects_a_named_non_gmail_server() -> None:
    """Absent / empty / non-string `mcp_server_name` means "cannot tell": pass."""
    assert archive_hook._wrong_server({}) is False
    assert archive_hook._wrong_server({"mcp_server_name": ""}) is False
    assert archive_hook._wrong_server({"mcp_server_name": None}) is False
    assert archive_hook._wrong_server({"mcp_server_name": 42}) is False
    assert archive_hook._wrong_server({"mcp_server_name": ["gmail"]}) is False
    # Any server whose key mentions gmail (case-insensitive) passes.
    assert archive_hook._wrong_server({"mcp_server_name": "gmail"}) is False
    assert archive_hook._wrong_server({"mcp_server_name": "Gmail-Personal"}) is False
    # A named server that is not Gmail is rejected.
    assert archive_hook._wrong_server({"mcp_server_name": "other"}) is True
