# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/ide.py` (host-IDE detection helper)."""
from __future__ import annotations

import pytest


def test_detect_claude_code_from_marker() -> None:
    from superagent.tools.ide import IDE, detect

    assert detect({"CLAUDECODE": "1"}) is IDE.CLAUDE_CODE
    assert detect({"CLAUDECODE": "true"}) is IDE.CLAUDE_CODE
    assert detect({"CLAUDECODE": "TRUE"}) is IDE.CLAUDE_CODE
    assert detect({"CLAUDECODE": "yes"}) is IDE.CLAUDE_CODE
    assert detect({"CLAUDECODE": "on"}) is IDE.CLAUDE_CODE


def test_detect_cursor_from_any_cursor_var() -> None:
    from superagent.tools.ide import IDE, detect

    assert detect({"CURSOR_TRACE_ID": "abc-123"}) is IDE.CURSOR
    assert detect({"CURSOR_SESSION_ID": "xyz"}) is IDE.CURSOR
    assert detect({"CURSOR_FOO": ""}) is IDE.CURSOR


def test_detect_neither_marker_returns_unknown() -> None:
    from superagent.tools.ide import IDE, detect

    assert detect({}) is IDE.UNKNOWN
    assert detect({"PATH": "/usr/bin", "HOME": "/home/x"}) is IDE.UNKNOWN


def test_detect_claude_wins_when_both_set() -> None:
    from superagent.tools.ide import IDE, detect

    env = {"CLAUDECODE": "1", "CURSOR_TRACE_ID": "abc"}
    assert detect(env) is IDE.CLAUDE_CODE


def test_detect_falsy_claude_marker_is_ignored() -> None:
    from superagent.tools.ide import IDE, detect

    assert detect({"CLAUDECODE": "0"}) is IDE.UNKNOWN
    assert detect({"CLAUDECODE": ""}) is IDE.UNKNOWN
    assert detect({"CLAUDECODE": "false"}) is IDE.UNKNOWN
    assert detect({"CLAUDECODE": "no"}) is IDE.UNKNOWN


def test_detect_falsy_claude_marker_does_not_block_cursor() -> None:
    from superagent.tools.ide import IDE, detect

    env = {"CLAUDECODE": "0", "CURSOR_TRACE_ID": "abc"}
    assert detect(env) is IDE.CURSOR


def test_label_returns_canonical_kebab_case() -> None:
    from superagent.tools.ide import IDE, label

    assert label(IDE.CLAUDE_CODE) == "claude-code"
    assert label(IDE.CURSOR) == "cursor"
    assert label(IDE.UNKNOWN) == "unknown"


def test_detect_default_uses_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import IDE, detect

    monkeypatch.delenv("CLAUDECODE", raising=False)
    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect() is IDE.CLAUDE_CODE


def test_detect_reruns_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No sticky state — switching env between calls switches the answer."""
    from superagent.tools.ide import IDE, detect

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect() is IDE.CLAUDE_CODE
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("CURSOR_TRACE_ID", "xyz")
    assert detect() is IDE.CURSOR
    monkeypatch.delenv("CURSOR_TRACE_ID")
    assert detect() is IDE.UNKNOWN


def test_cli_current_prints_label(capsys: pytest.CaptureFixture[str],
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import main

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    rc = main(["current"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "claude-code"


def test_cli_is_claude_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import main

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["is-claude"]) == 0
    assert main(["is-cursor"]) == 1


def test_cli_is_cursor_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import main

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CURSOR_TRACE_ID", "xyz")
    assert main(["is-cursor"]) == 0
    assert main(["is-claude"]) == 1


def test_cli_no_command_defaults_to_current(capsys: pytest.CaptureFixture[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import main

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "unknown"


def test_cli_env_subcommand_reports_state(capsys: pytest.CaptureFixture[str],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.ide import main

    for key in list(__import__("os").environ):
        if key.startswith("CURSOR_") or key == "CLAUDECODE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CURSOR_TRACE_ID", "xyz")
    rc = main(["env"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "detected=cursor" in out
    assert "CURSOR_TRACE_ID" in out
