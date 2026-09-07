# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/log_user_query.py`: redaction, harness-noise tagging, privacy opt-out."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from superagent.tools.log_user_query import (
    REDACTED_PASSWORD,
    REDACTED_TOKEN,
    is_logging_enabled,
    main,
    redact,
    strip_ide_wrapper,
)
from superagent.tools.skill_loader import SYNTHETIC_MARKERS

# --- (a) credential markers -------------------------------------------------

def test_password_after_colon_redacted() -> None:
    out = redact("my password: hunter2AB9 please save it")
    assert "hunter2AB9" not in out
    assert REDACTED_PASSWORD in out
    assert out.endswith("please save it")


def test_password_after_equals_redacted() -> None:
    out = redact("set PWD=s3cr3t!x and continue")
    assert "s3cr3t!x" not in out
    assert REDACTED_PASSWORD in out


def test_password_is_phrase_redacted() -> None:
    out = redact("the wifi password is Tr0ub4dor&3 ok?")
    assert "Tr0ub4dor&3" not in out
    assert REDACTED_PASSWORD in out


def test_password_bare_whitespace_with_secret_token() -> None:
    out = redact("password hunter42secret")
    assert "hunter42secret" not in out
    assert REDACTED_PASSWORD in out


# --- (c) URLs ----------------------------------------------------------------

def test_claim_url_token_redacted() -> None:
    url = "https://beta-bridge.simplefin.org/simplefin/claim/AbC123xYz789TOKEN"
    out = redact(f"here is the setup link {url} thanks")
    assert "AbC123xYz789TOKEN" not in out
    assert f"/claim/{REDACTED_TOKEN}" in out
    assert "beta-bridge.simplefin.org/simplefin" in out  # rest of URL kept


def test_token_query_param_redacted() -> None:
    out = redact("open https://example.com/page?id=42&access_token=abc123XYZsecret&x=1")
    assert "abc123XYZsecret" not in out
    assert f"access_token={REDACTED_TOKEN}" in out
    assert "id=42" in out and "x=1" in out  # non-secret params kept


# --- (b) high-entropy blobs ---------------------------------------------------

def test_high_entropy_blob_redacted() -> None:
    blob = "aB3dE5fG7hI9jK1lM2nO4pQ6"
    out = redact(f"the key is {blob} for the api")
    assert blob not in out
    assert REDACTED_TOKEN in out


def test_long_hex_run_redacted() -> None:
    blob = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"
    out = redact(f"hash {blob} end")
    assert blob not in out
    assert REDACTED_TOKEN in out


def test_base64_run_redacted() -> None:
    blob = "QWxhZGRpbjpvcGVuIHNlc2FtZQ+Zm9vYmFy=="
    out = redact(f"header {blob} end")
    assert blob not in out
    assert REDACTED_TOKEN in out


# --- conservative: normal prose untouched -------------------------------------

def test_prose_with_password_word_untouched() -> None:
    text = "I forgot my password again, can you help me reset it tomorrow?"
    assert redact(text) == text


def test_file_paths_untouched() -> None:
    text = (
        "see /Users/someone/Library/Mobile Documents/com~apple~CloudDocs/"
        "my-repo/superagent/tools/log_user_query.py for details"
    )
    assert redact(text) == text


def test_yaml_handles_untouched() -> None:
    text = (
        "link appointment:20260512-dr-smith-cleaning to contact:dr-smith-dentist "
        "and task-20260428-001 in the world graph"
    )
    assert redact(text) == text


def test_camel_case_identifier_untouched() -> None:
    text = "rename getUserAccountByIdentifier to something shorter"
    assert redact(text) == text


def test_empty_and_plain_text_untouched() -> None:
    assert redact("") == ""
    text = "what bills are due this week?"
    assert redact(text) == text


# --- end-to-end: main() applies redaction and still exits 0 -------------------

def _run_main(tmp_path: Path, monkeypatch, prompt: str, config: str | None = None) -> list[dict]:
    """Invoke main() against a fresh workspace; return the parsed rows written (possibly none)."""
    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True, exist_ok=True)
    if config is not None:
        (workspace / "_memory" / "config.yaml").write_text(config)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))

    rc = main(["--workspace", str(workspace), "--source", "test"])
    assert rc == 0

    log_path = workspace / "_memory" / "user-queries.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_main_writes_redacted_row(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "my password: hunter2AB9 and more")
    assert len(rows) == 1
    row = rows[0]
    assert "hunter2AB9" not in row["prompt"]
    assert REDACTED_PASSWORD in row["prompt"]
    assert row["length"] == len(row["prompt"])
    assert "synthetic" not in row


# --- harness noise: synthetic tagging + IDE wrapper stripping -----------------

_WRAPPER = (
    "<ide_opened_file>The user opened the file /some/repo/workspace/_memory/config.yaml "
    "in the IDE. This may or may not be related to the current task.</ide_opened_file>\n"
)


@pytest.mark.parametrize("marker", SYNTHETIC_MARKERS)
def test_main_tags_harness_marker_rows_synthetic(tmp_path: Path, monkeypatch, marker: str) -> None:
    prompt = f"{marker} some payload text that mentions a daily update"
    rows = _run_main(tmp_path, monkeypatch, prompt)
    assert len(rows) == 1, "synthetic rows are tagged, not dropped (information-preserving)"
    assert rows[0]["synthetic"] is True
    assert rows[0]["prompt"].startswith(marker)


def test_main_tags_cross_session_and_heartbeat_shapes(tmp_path: Path, monkeypatch) -> None:
    """The two newly-added markers, in the shape the harness actually emits them."""
    xsm = '<cross-session-message from="uds:/tmp/x.sock" from-name="peer">done</cross-session-message>'
    rows = _run_main(tmp_path, monkeypatch, xsm)
    assert rows[0]["synthetic"] is True
    rows = _run_main(tmp_path, monkeypatch, "Fallback heartbeat: 1 of 4 reviewers still pending")
    assert rows[-1]["synthetic"] is True


def test_main_leading_whitespace_before_marker_still_synthetic(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "\n  <task-notification>x</task-notification>")
    assert rows[0]["synthetic"] is True


def test_main_plain_prompt_untagged(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "what bills are due this week?")
    assert len(rows) == 1
    assert rows[0]["prompt"] == "what bills are due this week?"
    assert "synthetic" not in rows[0]


def test_main_marker_quoted_mid_prose_is_not_synthetic(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "why does the log contain <task-notification> rows?")
    assert "synthetic" not in rows[0]


def test_main_strips_ide_opened_file_wrapper(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, _WRAPPER + "ask again")
    assert len(rows) == 1
    assert rows[0]["prompt"] == "ask again"
    assert rows[0]["length"] == len("ask again")
    assert "synthetic" not in rows[0]


def test_main_ide_wrapper_around_synthetic_body_is_tagged(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, _WRAPPER + "<task-notification>x</task-notification>")
    assert rows[0]["synthetic"] is True
    assert rows[0]["prompt"].startswith("<task-notification>")


def test_strip_ide_wrapper_handles_multiple_and_absent_wrappers() -> None:
    assert strip_ide_wrapper(_WRAPPER + _WRAPPER + "  hello") == "hello"
    assert strip_ide_wrapper("hello") == "hello"
    assert strip_ide_wrapper("") == ""
    # A wrapper that is not at the very start is left alone (not our shape).
    text = "hello " + _WRAPPER.strip()
    assert strip_ide_wrapper(text) == text


# --- privacy opt-out: preferences.privacy.log_user_queries -------------------

def test_main_respects_privacy_opt_out(tmp_path: Path, monkeypatch) -> None:
    config = "preferences:\n  privacy:\n    log_user_queries: false\n"
    rows = _run_main(tmp_path, monkeypatch, "what bills are due?", config=config)
    assert rows == []
    assert not (tmp_path / "workspace" / "_memory" / "user-queries.jsonl").exists()


def test_main_logs_when_opt_out_absent_from_config(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "what bills are due?", config="preferences: {}\n")
    assert len(rows) == 1


def test_main_malformed_config_defaults_to_enabled(tmp_path: Path, monkeypatch) -> None:
    rows = _run_main(tmp_path, monkeypatch, "what bills are due?", config="preferences: [unclosed\n")
    assert len(rows) == 1


def test_is_logging_enabled_direct(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True)
    assert is_logging_enabled(workspace) is True  # no config.yaml
    cfg = workspace / "_memory" / "config.yaml"
    cfg.write_text("preferences:\n  privacy:\n    log_user_queries: true\n")
    assert is_logging_enabled(workspace) is True
    cfg.write_text("preferences:\n  privacy:\n    log_user_queries: false\n")
    assert is_logging_enabled(workspace) is False
    cfg.write_text("preferences:\n  privacy: null\n")
    assert is_logging_enabled(workspace) is True
