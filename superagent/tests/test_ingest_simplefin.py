# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/ingest/simplefin.py` (SimpleFin Bridge ingestor).

The emphasis is fetch-failure bookkeeping: a refresh that fails must land
on the RunResult so it reaches `ingestion-log.yaml` and `data-sources.yaml`.
A read timeout used to escape `except (HTTPError, URLError)` and kill the
run before anything was written, which made a failed refresh indistinguishable
from one that never happened.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
import yaml

ACCESS_URL = "https://user:pa:ss@bridge.example.com/simplefin"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stage_credentials(ws: Path, access_url: str = ACCESS_URL) -> Path:
    """Write a credentials file in the shape `_load_access_url` expects."""
    path = ws / "_memory" / "sensitive" / "simplefin-credentials.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"access_url": access_url}))
    return path


def _accounts_payload(txn_id: str = "t1", pending: bool = False) -> dict:
    """One institution, one account, one transaction."""
    return {
        "errors": [],
        "accounts": [
            {
                "id": "acc1",
                "name": "Checking",
                "currency": "USD",
                "org": {"name": "Test Bank"},
                "transactions": [
                    {
                        "id": txn_id,
                        "amount": "-12.34",
                        "posted": 1756200000,
                        "transacted_at": 1756200000,
                        "description": "COFFEE SHOP",
                        "payee": "Coffee Shop",
                        "pending": pending,
                    }
                ],
            }
        ],
    }


def _ingestor(ws: Path):
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    _stage_credentials(ws)
    return SimpleFinIngestor(ws)


# ---------------------------------------------------------------------------
# fetch-failure bookkeeping
# ---------------------------------------------------------------------------


def test_run_records_read_timeout_as_error_instead_of_raising(tmp_path: Path) -> None:
    """A read timeout is a recorded run error, not a traceback."""
    from superagent.tools.ingest import simplefin

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(
        simplefin, "http_get_json", side_effect=TimeoutError("The read operation timed out")
    ):
        result = ingestor.run({"recency_window_days": 5, "timeout": 7}, dry_run=True)

    assert len(result.errors) == 1
    assert "timed out after 7s" in result.errors[0]
    assert result.items_pulled == 0
    assert result.items_inserted == 0
    # The run still completes and reports its timing.
    assert result.finished_at


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (HTTPError("http://x", 403, "Forbidden", {}, None), "403"),
        (URLError("Tunnel connection failed"), "Tunnel connection failed"),
    ],
)
def test_run_records_http_and_url_errors(tmp_path: Path, exc: Exception, needle: str) -> None:
    """Pre-existing HTTP / network error handling keeps working."""
    from superagent.tools.ingest import simplefin

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(simplefin, "http_get_json", side_effect=exc):
        result = ingestor.run({"recency_window_days": 5}, dry_run=True)

    assert len(result.errors) == 1
    assert needle in result.errors[0]
    assert result.items_pulled == 0


def test_run_error_message_names_the_window(tmp_path: Path) -> None:
    """Errors identify which chunk failed, so multi-chunk backfills stay legible."""
    from superagent.tools.ingest import simplefin

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(simplefin, "http_get_json", side_effect=TimeoutError()):
        result = ingestor.run({"recency_window_days": 5}, dry_run=True)

    assert result.errors[0].startswith("fetch ")
    assert ".." in result.errors[0]


def test_update_source_row_records_the_failure(tmp_path: Path) -> None:
    """A recorded error must reach data-sources.yaml and bump failure_streak."""
    from superagent.tools.ingest._base import RunResult
    from superagent.tools.ingest.simplefin import _update_source_row

    ws = tmp_path / "ws"
    ds = ws / "_memory" / "data-sources.yaml"
    ds.parent.mkdir(parents=True)
    ds.write_text(yaml.safe_dump({
        "schema_version": 1,
        "sources": [{"id": "simplefin", "failure_streak": 2, "last_ingest": "2026-01-01T00:00:00"}],
    }))

    result = RunResult(source="simplefin", started_at="t0", finished_at="t1")
    result.errors = ["fetch 2026-08-24..2026-08-26: timed out after 180s"]
    _update_source_row(ws, "simplefin", result, "ingest-1", window=None)

    row = yaml.safe_load(ds.read_text())["sources"][0]
    assert row["failure_streak"] == 3
    assert row["last_run"]["errors"] == result.errors
    assert row["last_run"]["run_log_id"] == "ingest-1"


# ---------------------------------------------------------------------------
# timeout plumbing
# ---------------------------------------------------------------------------


def test_run_defaults_to_the_module_timeout(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(simplefin, "http_get_json", return_value=_accounts_payload()) as spy:
        ingestor.run({"recency_window_days": 5}, dry_run=True)

    assert spy.call_args.kwargs["timeout"] == simplefin.DEFAULT_HTTP_TIMEOUT


def test_run_honors_a_configured_timeout(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(simplefin, "http_get_json", return_value=_accounts_payload()) as spy:
        ingestor.run({"recency_window_days": 5, "timeout": 240}, dry_run=True)

    assert spy.call_args.kwargs["timeout"] == 240


def test_probe_uses_the_short_probe_timeout(tmp_path: Path) -> None:
    """Probes ask for balances only; they must not inherit the fetch timeout."""
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest._base import ProbeStatus

    ingestor = _ingestor(tmp_path / "ws")
    with patch.object(simplefin, "http_get_json", return_value={}) as spy:
        res = ingestor.probe()

    assert res.status == ProbeStatus.AVAILABLE
    assert spy.call_args.kwargs["timeout"] == simplefin.PROBE_HTTP_TIMEOUT
    assert simplefin.PROBE_HTTP_TIMEOUT < simplefin.DEFAULT_HTTP_TIMEOUT


def test_cli_timeout_flag_reaches_the_config_row(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest._base import RunResult
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    _stage_credentials(ws)
    captured: dict = {}

    def _fake_run(self, config_row, dry_run=False):
        captured.update(config_row)
        return RunResult(source="simplefin", started_at="t0", finished_at="t1")

    argv = ["ingest-simplefin", "--workspace", str(ws), "--timeout", "42", "--dry-run"]
    with patch.object(SimpleFinIngestor, "run", _fake_run), patch.object(simplefin.sys, "argv", argv):
        rc = simplefin.main()

    assert rc == 0
    assert captured["timeout"] == 42


def test_cli_without_timeout_flag_leaves_it_unset(tmp_path: Path) -> None:
    """Absent flag means the ingestor falls back to DEFAULT_HTTP_TIMEOUT."""
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest._base import RunResult
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    _stage_credentials(ws)
    captured: dict = {}

    def _fake_run(self, config_row, dry_run=False):
        captured.update(config_row)
        return RunResult(source="simplefin", started_at="t0", finished_at="t1")

    argv = ["ingest-simplefin", "--workspace", str(ws), "--dry-run"]
    with patch.object(SimpleFinIngestor, "run", _fake_run), patch.object(simplefin.sys, "argv", argv):
        simplefin.main()

    assert captured["timeout"] is None


# ---------------------------------------------------------------------------
# happy path (baseline coverage for a previously untested module)
# ---------------------------------------------------------------------------


def test_run_normalizes_and_inserts_transactions(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value=_accounts_payload()),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
    ):
        result = ingestor.run({"recency_window_days": 5})

    assert result.errors == []
    assert result.items_pulled == 1
    assert result.items_inserted == 1
    rows = yaml.safe_load((ws / "_memory" / "transactions.yaml").read_text())["transactions"]
    assert rows[0]["external_id"] == "simplefin:acc1:t1"
    assert rows[0]["amount"] == -12.34
    assert rows[0]["institution"] == "Test Bank"
    assert rows[0]["pending"] is False


def test_run_skips_transactions_already_in_the_index(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value=_accounts_payload()),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
    ):
        ingestor.run({"recency_window_days": 5})
        result = ingestor.run({"recency_window_days": 5})

    assert result.items_pulled == 1
    assert result.items_inserted == 0
    assert result.items_skipped == 1


def test_split_access_url_handles_a_colon_in_the_password() -> None:
    from superagent.tools.ingest.simplefin import split_access_url

    base, user, password = split_access_url(ACCESS_URL)
    assert base == "https://bridge.example.com/simplefin"
    assert user == "user"
    assert password == "pa:ss"
