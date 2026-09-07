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

import datetime as dt
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


# ---------------------------------------------------------------------------
# pending -> posted reconciliation
#
# SimpleFin re-issues a pending transaction under a NEW id when it posts, so
# the stored pending row (date coerced to 1970-01-01 by posted=0) is never
# deduped away. The reconciliation pass marks it superseded_by its posted
# twin and copies the real date onto it.
# ---------------------------------------------------------------------------


def _stored_row(external_id: str, **overrides) -> dict:
    """A transactions.yaml row in the shape `_normalize` emits.

    Defaults describe the orphaned-pending case: pending, date 1970-01-01.
    """
    row = {
        "external_id": external_id,
        "date": "1970-01-01",
        "transacted_at": "2026-08-18",
        "payee": "Coffee Shop",
        "description": "COFFEE SHOP",
        "memo": None,
        "amount": -12.34,
        "currency": "USD",
        "category": "uncategorized",
        "pending": True,
        "account_id": "acc1",
        "account_label": "Checking",
        "institution": "Test Bank",
        "source": "simplefin",
        "extra": {},
    }
    row.update(overrides)
    return row


def _posted_twin(external_id: str, **overrides) -> dict:
    """The posted counterpart: same account/amount/payee, a real date."""
    row = _stored_row(external_id, date="2026-08-20", transacted_at="2026-08-20", pending=False)
    row.update(overrides)
    return row


def _stage_index(ws: Path, rows: list[dict]) -> Path:
    path = ws / "_memory" / "transactions.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"schema_version": 1, "transactions": rows}))
    return path


def _run_cli(ws: Path, *extra: str) -> int:
    from superagent.tools.ingest import simplefin

    argv = ["ingest-simplefin", "--workspace", str(ws), *extra]
    with patch.object(simplefin.sys, "argv", argv):
        return simplefin.main()


def test_run_reconciles_pending_orphan_against_posted_twin(tmp_path: Path) -> None:
    """End-of-run pass: orphan gets superseded_by + the posted date; counts surface."""
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    posted_date = simplefin.unix_to_iso_date(1756200000)  # the payload's posted ts
    _stage_index(ws, [_stored_row("simplefin:acc1:p1", transacted_at=posted_date)])
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value=_accounts_payload()),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
    ):
        result = ingestor.run({"recency_window_days": 5})

    assert result.items_updated == 1
    assert result.destination_summary["reconciliation"] == {
        "matched": 1,
        "ambiguous_skipped": 0,
        "excluded": 0,
        "ambiguous_ids": [],
        "excluded_ids": [],
        "stale_marked": 0,
        "stale_ids": [],
    }
    rows = yaml.safe_load((ws / "_memory" / "transactions.yaml").read_text())["transactions"]
    orphan = next(r for r in rows if r["external_id"] == "simplefin:acc1:p1")
    assert orphan["superseded_by"] == "simplefin:acc1:t1"
    assert orphan["date"] == posted_date
    # No data loss: everything else on the pending row stays intact.
    assert orphan["pending"] is True
    assert orphan["amount"] == -12.34
    assert orphan["payee"] == "Coffee Shop"


def test_reconcile_skips_ambiguous_double_match(tmp_path: Path, capsys) -> None:
    """Two equally-plausible posted twins -> no merge, counted as ambiguous."""
    ws = tmp_path / "ws"
    _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
        _posted_twin("simplefin:acc1:t2"),
    ])

    assert _run_cli(ws, "--reconcile") == 0

    rows = yaml.safe_load((ws / "_memory" / "transactions.yaml").read_text())["transactions"]
    assert all("superseded_by" not in r for r in rows)
    assert "reconciled=0 ambiguous_skipped=1 excluded=0" in capsys.readouterr().out


def test_reconcile_exclude_flag_holds_a_row_out(tmp_path: Path, capsys) -> None:
    """--reconcile-exclude keeps a named row untouched even with a clean match."""
    ws = tmp_path / "ws"
    _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
    ])

    rc = _run_cli(ws, "--reconcile", "--reconcile-exclude", "simplefin:acc1:p1")

    assert rc == 0
    rows = yaml.safe_load((ws / "_memory" / "transactions.yaml").read_text())["transactions"]
    orphan = next(r for r in rows if r["external_id"] == "simplefin:acc1:p1")
    assert "superseded_by" not in orphan
    assert orphan["date"] == "1970-01-01"
    assert "reconciled=0 ambiguous_skipped=0 excluded=1" in capsys.readouterr().out


def test_reconcile_is_idempotent(tmp_path: Path, capsys) -> None:
    """A second pass over an already-reconciled store is a no-op."""
    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
    ])

    assert _run_cli(ws, "--reconcile") == 0
    after_first = idx.read_text()
    orphan = next(
        r for r in yaml.safe_load(after_first)["transactions"]
        if r["external_id"] == "simplefin:acc1:p1"
    )
    assert orphan["superseded_by"] == "simplefin:acc1:t1"
    assert orphan["date"] == "2026-08-20"

    capsys.readouterr()  # drop the first run's output
    assert _run_cli(ws, "--reconcile") == 0
    assert idx.read_text() == after_first
    assert "reconciled=0" in capsys.readouterr().out


def test_reconcile_dry_run_prints_matches_but_writes_nothing(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
    ])
    before = idx.read_text()
    log = ws / "_memory" / "ingestion-log.yaml"
    log.write_text(yaml.safe_dump({"runs": []}))

    assert _run_cli(ws, "--reconcile-dry-run") == 0

    assert idx.read_text() == before
    assert yaml.safe_load(log.read_text())["runs"] == []  # no log row either
    out = capsys.readouterr().out
    assert "simplefin:acc1:p1 -> simplefin:acc1:t1" in out
    assert "nothing written" in out


def test_reconcile_counts_reach_the_ingestion_log(tmp_path: Path) -> None:
    """The one-shot repair writes the same summary row the ingest run does."""
    ws = tmp_path / "ws"
    _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
    ])
    log = ws / "_memory" / "ingestion-log.yaml"
    log.write_text(yaml.safe_dump({"runs": []}))

    assert _run_cli(ws, "--reconcile") == 0

    runs = yaml.safe_load(log.read_text())["runs"]
    assert len(runs) == 1
    assert runs[0]["items_updated"] == 1
    assert runs[0]["destination_summary"]["reconciliation"] == {
        "matched": 1,
        "ambiguous_skipped": 0,
        "excluded": 0,
        "ambiguous_ids": [],
        "excluded_ids": [],
        "stale_marked": 0,
        "stale_ids": [],
    }


# ---------------------------------------------------------------------------
# amount tolerance + surfaced ids
#
# A posted card charge may exceed its pending authorization by a tip; the
# matcher tolerates a small ABSOLUTE delta (never a percentage) and keeps
# the uniqueness rule. Skipped orphans are named so the user can act.
# ---------------------------------------------------------------------------


def test_reconcile_tolerates_a_small_absolute_amount_delta() -> None:
    from superagent.tools.ingest.simplefin import _plan_reconciliation

    plan = _plan_reconciliation([
        _stored_row("simplefin:acc1:p1", amount=-12.34),
        _posted_twin("simplefin:acc1:t1", amount=-13.34),  # +$1.00 tip
    ])
    assert [m["posted_id"] for m in plan["matches"]] == ["simplefin:acc1:t1"]


def test_reconcile_rejects_amount_delta_beyond_tolerance() -> None:
    from superagent.tools.ingest.simplefin import _plan_reconciliation

    plan = _plan_reconciliation([
        _stored_row("simplefin:acc1:p1", amount=-12.34),
        _posted_twin("simplefin:acc1:t1", amount=-13.35),  # one cent past $1.00
    ])
    assert plan["matches"] == []
    assert plan["ambiguous"] == 0


def test_reconcile_rejects_a_sign_flip_even_within_tolerance() -> None:
    from superagent.tools.ingest.simplefin import _plan_reconciliation

    plan = _plan_reconciliation([
        _stored_row("simplefin:acc1:p1", amount=-0.40),
        _posted_twin("simplefin:acc1:t1", amount=0.40),  # a refund, not the twin
    ])
    assert plan["matches"] == []


def test_reconcile_tolerance_keeps_the_uniqueness_rule() -> None:
    """Two posted rows inside the tolerance band -> ambiguous, not a guess."""
    from superagent.tools.ingest.simplefin import _plan_reconciliation

    plan = _plan_reconciliation([
        _stored_row("simplefin:acc1:p1", amount=-12.34),
        _posted_twin("simplefin:acc1:t1", amount=-12.34),
        _posted_twin("simplefin:acc1:t2", amount=-13.00),
    ])
    assert plan["matches"] == []
    assert plan["ambiguous"] == 1
    assert plan["ambiguous_ids"] == ["simplefin:acc1:p1"]


def test_reconcile_plan_names_excluded_ids() -> None:
    from superagent.tools.ingest.simplefin import _plan_reconciliation

    plan = _plan_reconciliation(
        [_stored_row("simplefin:acc1:p1"), _posted_twin("simplefin:acc1:t1")],
        exclude={"simplefin:acc1:p1"},
    )
    assert plan["excluded"] == 1
    assert plan["excluded_ids"] == ["simplefin:acc1:p1"]


def test_reconcile_dry_run_lists_ambiguous_and_stale_ids(tmp_path: Path, capsys) -> None:
    """--reconcile-dry-run prints the skipped orphans and the would-be stale ones."""
    from superagent.tools.ingest import simplefin

    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p1"),
        _posted_twin("simplefin:acc1:t1"),
        _posted_twin("simplefin:acc1:t2"),
        _stored_row("simplefin:acc1:p9", payee="Hardware Store", description="HARDWARE",
                    transacted_at="2026-07-01"),
    ])
    before = idx.read_text()

    with patch.object(simplefin, "_today", return_value=dt.date(2026, 9, 6)):
        assert _run_cli(ws, "--reconcile-dry-run") == 0

    out = capsys.readouterr().out
    assert "ambiguous: simplefin:acc1:p1" in out
    assert "stale: simplefin:acc1:p9" in out
    # An ambiguous orphan that is also past the threshold retires too: it is
    # still listed as ambiguous so the user can resolve it by hand.
    assert "stale: simplefin:acc1:p1" in out
    assert "ambiguous_skipped=1" in out
    assert "stale_marked=2" in out
    assert "nothing written" in out
    assert idx.read_text() == before


# ---------------------------------------------------------------------------
# stale-pending retirement
#
# Orphans that never find a twin are flagged `stale_pending: true` once their
# transacted_at is older than `stale_pending_days`. Additive; never a delete.
# ---------------------------------------------------------------------------

def _today() -> dt.date:
    """Pinned 'now' for the stale-pending tests (2026-09-06)."""
    return dt.date(2026, 9, 6)


def test_mark_stale_pending_flags_old_orphans_and_is_idempotent() -> None:
    from superagent.tools.ingest.simplefin import mark_stale_pending

    rows = [_stored_row("simplefin:acc1:p1", transacted_at="2026-08-01")]

    assert mark_stale_pending(rows, days=14, today=_today()) == 1
    assert rows[0]["stale_pending"] is True
    # Everything else on the row is untouched: the flag is additive.
    assert rows[0]["date"] == "1970-01-01"
    assert rows[0]["pending"] is True
    assert rows[0]["amount"] == -12.34

    assert mark_stale_pending(rows, days=14, today=_today()) == 0
    assert rows[0]["stale_pending"] is True


def test_mark_stale_pending_never_touches_superseded_rows() -> None:
    from superagent.tools.ingest.simplefin import mark_stale_pending

    rows = [
        _stored_row("simplefin:acc1:p1", transacted_at="2026-01-05",
                    superseded_by="simplefin:acc1:t1"),
    ]
    assert mark_stale_pending(rows, days=14, today=_today()) == 0
    assert "stale_pending" not in rows[0]


def test_mark_stale_pending_boundary_is_strictly_older_than_days() -> None:
    from superagent.tools.ingest.simplefin import mark_stale_pending

    # today 2026-09-06, days 14 -> cutoff 2026-08-23. Exactly 14 days old
    # is NOT stale; 15 days old is.
    on_cutoff = _stored_row("simplefin:acc1:p1", transacted_at="2026-08-23")
    past_cutoff = _stored_row("simplefin:acc1:p2", transacted_at="2026-08-22")
    with_time = _stored_row("simplefin:acc1:p3", transacted_at="2026-08-22T23:59:00+00:00")

    assert mark_stale_pending([on_cutoff, past_cutoff, with_time], days=14, today=_today()) == 2
    assert "stale_pending" not in on_cutoff
    assert past_cutoff["stale_pending"] is True
    assert with_time["stale_pending"] is True


def test_mark_stale_pending_ignores_non_orphans_and_unparseable_anchors() -> None:
    from superagent.tools.ingest.simplefin import mark_stale_pending

    rows = [
        _posted_twin("simplefin:acc1:t1", date="2026-01-01", transacted_at="2026-01-01"),
        _stored_row("simplefin:acc1:p1", date="2026-01-01", transacted_at="2026-01-01"),
        _stored_row("simplefin:acc1:p2", transacted_at=None),
        _stored_row("simplefin:acc1:p3", transacted_at="not-a-date"),
    ]
    assert mark_stale_pending(rows, days=14, today=_today()) == 0
    assert all("stale_pending" not in r for r in rows)


def test_apply_reconciliation_clears_a_stale_flag_when_the_twin_arrives() -> None:
    from superagent.tools.ingest.simplefin import _apply_reconciliation, _plan_reconciliation

    rows = [
        _stored_row("simplefin:acc1:p1", stale_pending=True),
        _posted_twin("simplefin:acc1:t1"),
    ]
    plan = _plan_reconciliation(rows)
    assert _apply_reconciliation(rows, plan["matches"]) == 1
    assert rows[0]["superseded_by"] == "simplefin:acc1:t1"
    assert "stale_pending" not in rows[0]


def test_run_marks_stale_orphans_after_reconciliation_and_saves(tmp_path: Path) -> None:
    """A fetch that inserts nothing still persists newly-flagged stale rows."""
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    posted_date = simplefin.unix_to_iso_date(1756200000)
    idx = _stage_index(ws, [
        # The payload's row is already stored -> inserted=0.
        _posted_twin("simplefin:acc1:t1", date=posted_date, transacted_at=posted_date),
        # An orphan with no twin, 30 days before "today".
        _stored_row("simplefin:acc1:p9", payee="Hardware Store", description="HARDWARE",
                    amount=-80.00, transacted_at="2026-08-07"),
    ])
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value=_accounts_payload()),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
        patch.object(simplefin, "_today", return_value=_today()),
    ):
        result = ingestor.run({"recency_window_days": 5, "stale_pending_days": 21})

    assert result.items_inserted == 0
    assert result.items_updated == 1
    recon = result.destination_summary["reconciliation"]
    assert recon["stale_marked"] == 1
    assert recon["stale_ids"] == ["simplefin:acc1:p9"]
    rows = yaml.safe_load(idx.read_text())["transactions"]
    orphan = next(r for r in rows if r["external_id"] == "simplefin:acc1:p9")
    assert orphan["stale_pending"] is True
    assert orphan["date"] == "1970-01-01"  # kept; only the flag was added


def test_run_honours_a_longer_stale_pending_days_override(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p9", payee="Hardware Store", description="HARDWARE",
                    amount=-80.00, transacted_at="2026-08-07"),  # 30 days old
    ])
    before = idx.read_text()
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value={"errors": [], "accounts": []}),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
        patch.object(simplefin, "_today", return_value=_today()),
    ):
        result = ingestor.run({"recency_window_days": 5, "stale_pending_days": 60})

    assert result.destination_summary["reconciliation"]["stale_marked"] == 0
    assert idx.read_text() == before


def test_run_dry_run_reports_stale_candidates_without_writing(tmp_path: Path) -> None:
    from superagent.tools.ingest import simplefin
    from superagent.tools.ingest.simplefin import SimpleFinIngestor

    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p9", payee="Hardware Store", description="HARDWARE",
                    amount=-80.00, transacted_at="2026-08-07"),
    ])
    before = idx.read_text()
    ingestor = _ingestor(ws)
    with (
        patch.object(simplefin, "http_get_json", return_value={"errors": [], "accounts": []}),
        patch.object(SimpleFinIngestor, "_refresh_domains", return_value=[]),
        patch.object(simplefin, "_today", return_value=_today()),
    ):
        result = ingestor.run({"recency_window_days": 5}, dry_run=True)

    recon = result.destination_summary["reconciliation"]
    assert recon["stale_marked"] == 1
    assert recon["stale_ids"] == ["simplefin:acc1:p9"]
    assert result.items_updated == 0
    assert idx.read_text() == before


def test_reconcile_only_reads_stale_pending_days_from_the_data_sources_row(
    tmp_path: Path, capsys
) -> None:
    from superagent.tools.ingest import simplefin

    ws = tmp_path / "ws"
    idx = _stage_index(ws, [
        _stored_row("simplefin:acc1:p9", transacted_at="2026-08-07"),  # 30 days old
    ])
    ds = ws / "_memory" / "data-sources.yaml"
    ds.write_text(yaml.safe_dump({
        "schema_version": 1,
        "sources": [{"id": "simplefin", "enabled": True, "stale_pending_days": 45}],
    }))
    before = idx.read_text()

    with patch.object(simplefin, "_today", return_value=_today()):
        assert _run_cli(ws, "--reconcile") == 0
    assert "stale_marked=0" in capsys.readouterr().out
    assert idx.read_text() == before

    # Without the override the default (14d) applies and the row is flagged.
    ds.unlink()
    with patch.object(simplefin, "_today", return_value=_today()):
        assert _run_cli(ws, "--reconcile") == 0
    assert "stale_marked=1" in capsys.readouterr().out
    rows = yaml.safe_load(idx.read_text())["transactions"]
    assert rows[0]["stale_pending"] is True
