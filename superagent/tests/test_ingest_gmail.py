# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/ingest/gmail.py` (Gmail metadata ingestor)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stage_credentials(creds_dir: Path) -> tuple[Path, Path]:
    """Write fake OAuth credentials in the format the ingestor expects."""
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds = creds_dir / "credentials.json"
    keys = creds_dir / "gcp-oauth.keys.json"
    creds.write_text(json.dumps({
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "scope": (
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/gmail.settings.basic"
        ),
        "token_type": "Bearer",
        "expiry_date": 9999999999999,
    }))
    keys.write_text(json.dumps({
        "installed": {
            "client_id": "fake-client.apps.googleusercontent.com",
            "client_secret": "fake-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:3000/oauth2callback"],
        }
    }))
    return creds, keys


def _build_fake_service(
    list_responses: dict[str, list[list[dict]]] | None = None,
    get_responses: dict[str, dict] | None = None,
    profile: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that mirrors the Gmail API fluent surface.

    `list_responses[label]` is a list of pages (each page is a list of
    {"id": ...} dicts). `get_responses[id]` is the full message dict
    returned by `messages.get`. `profile` is the dict returned by
    `getProfile`.
    """
    list_responses = list_responses or {}
    get_responses = get_responses or {}
    profile = profile or {"emailAddress": "user@example.com"}

    list_calls: dict[str, int] = {}

    service = MagicMock()
    service.users.return_value.getProfile.return_value.execute.return_value = profile

    def _list(userId: str, labelIds: list[str], q: str, maxResults: int, pageToken: str | None):
        del userId, q, maxResults
        label = labelIds[0]
        idx = list_calls.get(label, 0)
        pages = list_responses.get(label, [[]])
        page = pages[idx] if idx < len(pages) else []
        list_calls[label] = idx + 1
        next_token = "next" if idx + 1 < len(pages) else None
        execute_mock = MagicMock()
        execute_mock.execute.return_value = {"messages": page, "nextPageToken": next_token}
        return execute_mock

    service.users.return_value.messages.return_value.list.side_effect = _list

    def _get(userId: str, id: str, format: str, metadataHeaders: list[str]):
        del userId, format, metadataHeaders
        execute_mock = MagicMock()
        execute_mock.execute.return_value = get_responses.get(id, {})
        return execute_mock

    service.users.return_value.messages.return_value.get.side_effect = _get
    return service


def _msg(
    mid: str,
    subject: str = "Hello",
    sender: str = "alice@example.com",
    internal_date_ms: int = 1747000000000,  # 2025-05-12 (deterministic shard)
    label_ids: list[str] | None = None,
) -> dict:
    return {
        "id": mid,
        "threadId": f"thr-{mid}",
        "internalDate": str(internal_date_ms),
        "labelIds": label_ids or ["INBOX"],
        "snippet": f"snippet-for-{mid}",
        "sizeEstimate": 1234,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Mon, 12 May 2025 10:00:00 +0000"},
            ]
        },
    }


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_missing_credentials_returns_needs_setup(tmp_path: Path) -> None:
    from superagent.tools.ingest._base import ProbeStatus
    from superagent.tools.ingest.gmail import GmailIngestor

    ws = tmp_path / "ws"
    ingestor = GmailIngestor(
        ws,
        credentials_path=tmp_path / "missing.json",
        oauth_keys_path=tmp_path / "missing-keys.json",
    )
    res = ingestor.probe()
    assert res.status == ProbeStatus.NEEDS_SETUP
    assert "missing.json" in res.detail or "OAuth tokens not found" in res.detail
    assert res.setup_hint


def test_probe_missing_oauth_keys_returns_needs_setup(tmp_path: Path) -> None:
    from superagent.tools.ingest._base import ProbeStatus
    from superagent.tools.ingest.gmail import GmailIngestor

    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    ingestor = GmailIngestor(
        tmp_path / "ws",
        credentials_path=creds,
        oauth_keys_path=tmp_path / "missing-keys.json",
    )
    res = ingestor.probe()
    assert res.status == ProbeStatus.NEEDS_SETUP


def test_probe_success_returns_available_with_email(tmp_path: Path) -> None:
    from superagent.tools.ingest._base import ProbeStatus
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ingestor = GmailIngestor(tmp_path / "ws", credentials_path=creds, oauth_keys_path=keys)

    fake_service = _build_fake_service(profile={"emailAddress": "test@gmail.com"})
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        res = ingestor.probe()
    assert res.status == ProbeStatus.AVAILABLE
    assert "test@gmail.com" in res.detail


def test_probe_http_403_returns_permission_denied(tmp_path: Path) -> None:
    from googleapiclient.errors import HttpError

    from superagent.tools.ingest._base import ProbeStatus
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ingestor = GmailIngestor(tmp_path / "ws", credentials_path=creds, oauth_keys_path=keys)

    fake_resp = MagicMock(status=403, reason="Forbidden")
    fake_service = MagicMock()
    fake_service.users.return_value.getProfile.return_value.execute.side_effect = (
        HttpError(fake_resp, b'{"error": "denied"}')
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        res = ingestor.probe()
    assert res.status == ProbeStatus.PERMISSION_DENIED


# ---------------------------------------------------------------------------
# _shard_for + _load_existing_ids + _append_shard
# ---------------------------------------------------------------------------


def test_shard_for_uses_yyyy_mm_prefix() -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    assert GmailIngestor._shard_for("2026-05-12T10:00:00+00:00") == "2026-05"
    assert GmailIngestor._shard_for("2024-12-31T23:59:59Z") == "2024-12"


def test_shard_for_falls_back_to_current_month_on_garbage() -> None:
    import datetime as dt

    from superagent.tools.ingest.gmail import GmailIngestor

    expected = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m")
    assert GmailIngestor._shard_for("") == expected
    assert GmailIngestor._shard_for("nope") == expected


def test_load_existing_ids_empty_dir(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    assert GmailIngestor._load_existing_ids(tmp_path / "missing") == set()
    (tmp_path / "empty").mkdir()
    assert GmailIngestor._load_existing_ids(tmp_path / "empty") == set()


def test_load_existing_ids_collects_across_shards(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    out = tmp_path / "_gmail"
    out.mkdir()
    (out / "2026-04.jsonl").write_text(
        json.dumps({"id": "a"}) + "\n" + json.dumps({"id": "b"}) + "\n"
    )
    (out / "2026-05.jsonl").write_text(
        json.dumps({"id": "c"}) + "\n\n" + json.dumps({"id": "d"}) + "\n"
    )
    assert GmailIngestor._load_existing_ids(out) == {"a", "b", "c", "d"}


def test_append_shard_appends_one_per_line(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    path = tmp_path / "_gmail" / "2026-05.jsonl"
    GmailIngestor._append_shard(path, [{"id": "a"}, {"id": "b"}])
    GmailIngestor._append_shard(path, [{"id": "c"}])
    lines = path.read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# run (full happy path)
# ---------------------------------------------------------------------------


def test_run_pulls_inserts_and_shards_by_month(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ws = tmp_path / "ws"
    ingestor = GmailIngestor(ws, credentials_path=creds, oauth_keys_path=keys)

    fake_service = _build_fake_service(
        list_responses={
            "INBOX": [[{"id": "m1"}, {"id": "m2"}]],
            "SENT": [[{"id": "m3"}]],
        },
        get_responses={
            "m1": _msg("m1", subject="april msg", internal_date_ms=1712000000000),  # 2024-04
            "m2": _msg("m2", subject="may msg",   internal_date_ms=1715000000000),  # 2024-05
            "m3": _msg("m3", subject="reply",     internal_date_ms=1715000000000, label_ids=["SENT"]),
        },
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        result = ingestor.run({"recency_window_days": 30, "max_items_per_run": 100})

    assert result.errors == []
    assert result.items_pulled == 3
    assert result.items_inserted == 3
    assert result.items_skipped == 0
    assert result.destination_summary == {
        "_memory/_gmail/2024-04.jsonl": 1,
        "_memory/_gmail/2024-05.jsonl": 2,
    }
    out_dir = ws / "_memory" / "_gmail"
    assert (out_dir / "2024-04.jsonl").is_file()
    assert (out_dir / "2024-05.jsonl").is_file()
    rows_apr = [json.loads(line) for line in (out_dir / "2024-04.jsonl").read_text().splitlines()]
    assert rows_apr[0]["id"] == "m1"
    assert rows_apr[0]["subject"] == "april msg"
    assert rows_apr[0]["from"] == "alice@example.com"
    assert rows_apr[0]["captured_at"]


def test_run_dedupes_against_existing_shards(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ws = tmp_path / "ws"
    out_dir = ws / "_memory" / "_gmail"
    out_dir.mkdir(parents=True)
    (out_dir / "2024-04.jsonl").write_text(json.dumps({"id": "m1"}) + "\n")

    ingestor = GmailIngestor(ws, credentials_path=creds, oauth_keys_path=keys)
    fake_service = _build_fake_service(
        list_responses={"INBOX": [[{"id": "m1"}, {"id": "m2"}]]},
        get_responses={"m2": _msg("m2", internal_date_ms=1715000000000)},
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        result = ingestor.run({"labels": ["INBOX"]})

    assert result.items_pulled == 2
    assert result.items_skipped == 1  # m1 already on disk
    assert result.items_inserted == 1
    assert result.destination_summary == {"_memory/_gmail/2024-05.jsonl": 1}


def test_run_dedupes_within_run_across_labels(tmp_path: Path) -> None:
    """A message visible in both INBOX and SENT should be fetched once."""
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ingestor = GmailIngestor(tmp_path / "ws", credentials_path=creds, oauth_keys_path=keys)
    fake_service = _build_fake_service(
        list_responses={
            "INBOX": [[{"id": "shared"}]],
            "SENT": [[{"id": "shared"}]],
        },
        get_responses={"shared": _msg("shared", internal_date_ms=1715000000000)},
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        result = ingestor.run({"labels": ["INBOX", "SENT"]})
    assert result.items_pulled == 1
    assert result.items_inserted == 1


def test_run_truncates_at_max_items(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ingestor = GmailIngestor(tmp_path / "ws", credentials_path=creds, oauth_keys_path=keys)
    inbox_ids = [{"id": f"m{i}"} for i in range(100)]
    sent_ids = [{"id": f"s{i}"} for i in range(100)]
    gets = {f"m{i}": _msg(f"m{i}", internal_date_ms=1715000000000) for i in range(100)}
    gets.update({f"s{i}": _msg(f"s{i}", internal_date_ms=1715000000000) for i in range(100)})
    fake_service = _build_fake_service(
        list_responses={"INBOX": [inbox_ids], "SENT": [sent_ids]},
        get_responses=gets,
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        result = ingestor.run({"labels": ["INBOX", "SENT"], "max_items_per_run": 5})
    assert result.items_pulled == 5
    assert result.items_inserted == 5
    assert result.truncated is True


def test_run_dry_run_writes_nothing(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ws = tmp_path / "ws"
    ingestor = GmailIngestor(ws, credentials_path=creds, oauth_keys_path=keys)
    fake_service = _build_fake_service(
        list_responses={"INBOX": [[{"id": "m1"}]]},
        get_responses={"m1": _msg("m1", internal_date_ms=1715000000000)},
    )
    with patch.object(GmailIngestor, "_service_lazy", return_value=fake_service):
        result = ingestor.run({"labels": ["INBOX"]}, dry_run=True)
    assert result.items_inserted == 1
    assert "dry-run" in result.notes
    assert not (ws / "_memory" / "_gmail" / "2024-05.jsonl").exists()


def test_run_records_errors_per_label_and_continues(tmp_path: Path) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    creds, keys = _stage_credentials(tmp_path / "gmail-mcp")
    ingestor = GmailIngestor(tmp_path / "ws", credentials_path=creds, oauth_keys_path=keys)

    service = MagicMock()

    def _list(userId, labelIds, q, maxResults, pageToken):
        del userId, q, maxResults, pageToken
        if labelIds == ["INBOX"]:
            raise RuntimeError("rate-limited")
        execute = MagicMock()
        execute.execute.return_value = {"messages": [{"id": "ok1"}], "nextPageToken": None}
        return execute

    service.users.return_value.messages.return_value.list.side_effect = _list

    def _get(userId, id, format, metadataHeaders):
        del userId, format, metadataHeaders
        execute = MagicMock()
        execute.execute.return_value = _msg(id, internal_date_ms=1715000000000)
        return execute

    service.users.return_value.messages.return_value.get.side_effect = _get

    with patch.object(GmailIngestor, "_service_lazy", return_value=service):
        result = ingestor.run({"labels": ["INBOX", "SENT"]})

    assert any("INBOX" in e for e in result.errors)
    assert result.items_inserted == 1
    assert result.items_pulled == 1


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


def test_registry_gmail_entry_matches_module() -> None:
    from superagent.tools.ingest._registry import find
    from superagent.tools.ingest.gmail import GmailIngestor

    spec = find("gmail")
    assert spec is not None
    assert spec.kind == GmailIngestor.kind
    assert spec.module == "gmail"
    assert spec.source == GmailIngestor.source


@pytest.mark.parametrize("attr", ["source", "kind", "description"])
def test_class_attrs_set(attr: str) -> None:
    from superagent.tools.ingest.gmail import GmailIngestor

    val = getattr(GmailIngestor, attr)
    assert isinstance(val, str) and val
