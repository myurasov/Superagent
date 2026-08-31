# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the credential-redaction pass in `tools/log_user_query.py`."""
from __future__ import annotations

import json
from pathlib import Path

from superagent.tools.log_user_query import (
    REDACTED_PASSWORD,
    REDACTED_TOKEN,
    redact,
)

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
        "see /Users/misha/Library/Mobile Documents/com~apple~CloudDocs/"
        "MY-Superagent/superagent/tools/log_user_query.py for details"
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

def test_main_writes_redacted_row(tmp_path: Path, monkeypatch, capsys) -> None:
    import io
    import sys

    from superagent.tools.log_user_query import main

    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True)
    payload = json.dumps({"prompt": "my password: hunter2AB9 and more"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    rc = main(["--workspace", str(workspace), "--source", "test"])
    assert rc == 0

    log_path = workspace / "_memory" / "user-queries.jsonl"
    row = json.loads(log_path.read_text().strip())
    assert "hunter2AB9" not in row["prompt"]
    assert REDACTED_PASSWORD in row["prompt"]
    assert row["length"] == len(row["prompt"])
