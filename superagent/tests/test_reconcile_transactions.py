# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/reconcile_transactions.py` (bills vs. ingested transactions).

Two concerns: the loader must drop rows the SimpleFin ingestor has retired
(`superseded_by`, `stale_pending`) so they never count as spend or match a
bill, and the matching / recurring-detection helpers must behave on small
synthetic ledgers.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from superagent.tools import reconcile_transactions as rt

TODAY = dt.date(2026, 9, 6)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _txn_row(external_id: str, **overrides) -> dict:
    """A transactions.yaml row in the shape the SimpleFin ingestor emits."""
    row = {
        "external_id": external_id,
        "date": "2026-09-01",
        "transacted_at": "2026-09-01",
        "payee": "Power Company",
        "description": "POWER CO AUTOPAY",
        "amount": -120.00,
        "currency": "USD",
        "category": "uncategorized",
        "pending": False,
        "account_id": "sf-acc-1",
        "source": "simplefin",
    }
    row.update(overrides)
    return row


def _stage_workspace(
    tmp_path: Path,
    transactions: list[dict],
    bills: list[dict] | None = None,
    subscriptions: list[dict] | None = None,
) -> Path:
    ws = tmp_path / "ws"
    mem = ws / "_memory"
    mem.mkdir(parents=True)
    (mem / "accounts-index.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "accounts": [
            {"id": "checking", "name": "Checking", "simplefin_account_id": "sf-acc-1"},
        ],
    }))
    (mem / "transactions.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "transactions": transactions,
    }))
    (mem / "bills.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "bills": bills or [],
    }))
    (mem / "subscriptions.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "subscriptions": subscriptions or [],
    }))
    return ws


def _txn(date: str, amount: float, payee: str = "Power Company", slug: str | None = "checking") -> rt.Txn:
    return rt.Txn(
        date=dt.date.fromisoformat(date),
        payee=payee,
        description=payee.upper(),
        amount=amount,
        account_slug=slug,
        account_id="sf-acc-1",
    )


# ---------------------------------------------------------------------------
# load_transactions: retired rows never enter the analysis
# ---------------------------------------------------------------------------


def test_load_transactions_skips_superseded_and_stale_rows(tmp_path: Path) -> None:
    ws = _stage_workspace(tmp_path, [
        _txn_row("simplefin:sf-acc-1:live"),
        _txn_row("simplefin:sf-acc-1:sup", superseded_by="simplefin:sf-acc-1:live"),
        _txn_row("simplefin:sf-acc-1:stale", date="1970-01-01", pending=True,
                 transacted_at="2026-07-01", stale_pending=True),
        # A plain orphan with the coerced date parses fine and is kept; the
        # date window in `reconcile` is what excludes it, not the loader.
        _txn_row("simplefin:sf-acc-1:orphan", date="1970-01-01", pending=True),
    ])
    accounts = rt.load_accounts(ws)

    txns = rt.load_transactions(ws, accounts)

    ids = sorted(t.raw["external_id"] for t in txns)
    assert ids == ["simplefin:sf-acc-1:live", "simplefin:sf-acc-1:orphan"]
    assert all(t.account_slug == "checking" for t in txns)


def test_load_transactions_drops_rows_without_a_parseable_date(tmp_path: Path) -> None:
    ws = _stage_workspace(tmp_path, [
        _txn_row("simplefin:sf-acc-1:ok"),
        _txn_row("simplefin:sf-acc-1:bad", date="not-a-date"),
        _txn_row("simplefin:sf-acc-1:none", date=None),
    ])
    txns = rt.load_transactions(ws, rt.load_accounts(ws))
    assert [t.raw["external_id"] for t in txns] == ["simplefin:sf-acc-1:ok"]


def test_is_retired_recognises_both_flags() -> None:
    assert rt.is_retired({"superseded_by": "x"}) is True
    assert rt.is_retired({"stale_pending": True}) is True
    assert rt.is_retired({"stale_pending": False}) is False
    assert rt.is_retired({"pending": True, "date": "1970-01-01"}) is False


# ---------------------------------------------------------------------------
# matching helpers
# ---------------------------------------------------------------------------


def test_expected_due_dates_monthly_expands_across_the_window() -> None:
    bill = {"cadence": "monthly", "due_day": 15}
    dates = rt.expected_due_dates(bill, dt.date(2026, 7, 1), dt.date(2026, 9, 6))
    assert dates == [dt.date(2026, 7, 15), dt.date(2026, 8, 15)]


def test_expected_due_dates_one_shot_uses_next_due_only_inside_window() -> None:
    inside = {"cadence": "one-shot", "next_due": "2026-09-03"}
    outside = {"cadence": "one-shot", "next_due": "2026-10-03"}
    window = (dt.date(2026, 8, 30), dt.date(2026, 9, 6))
    assert rt.expected_due_dates(inside, *window) == [dt.date(2026, 9, 3)]
    assert rt.expected_due_dates(outside, *window) == []


def test_find_bill_match_requires_account_window_amount_and_outflow() -> None:
    bill = {"id": "bill-power", "amount": 120.00, "pay_from_account": "checking"}
    due = dt.date(2026, 9, 1)

    hit = _txn("2026-09-03", -118.00)                      # within 5d and 10%
    wrong_account = _txn("2026-09-03", -118.00, slug="savings")
    too_late = _txn("2026-09-09", -118.00)                 # 8 days out
    too_far_off = _txn("2026-09-03", -95.00)               # >10% off
    inflow = _txn("2026-09-03", 118.00)                    # a credit, not a payment

    assert rt.find_bill_match(bill, due, [wrong_account, too_late, too_far_off, inflow]) is None
    assert rt.find_bill_match(bill, due, [wrong_account, hit]) is hit


def test_detect_recurring_candidates_flags_untracked_monthly_payee() -> None:
    txns = [
        _txn("2026-06-05", -15.99, payee="Stream Box"),
        _txn("2026-07-05", -15.99, payee="Stream Box"),
        _txn("2026-08-05", -15.99, payee="Stream Box"),
        # Tracked already -> excluded even though it recurs.
        _txn("2026-06-01", -120.00, payee="Power Company"),
        _txn("2026-07-01", -120.00, payee="Power Company"),
        _txn("2026-08-01", -120.00, payee="Power Company"),
        # Only two hits -> below RECURRING_MIN_HITS.
        _txn("2026-07-10", -40.00, payee="Gym Club"),
        _txn("2026-08-10", -40.00, payee="Gym Club"),
    ]
    bills = [{"id": "bill-power", "name": "Power Company", "payee": "Power Company"}]

    out = rt.detect_recurring_candidates(txns, bills, [], TODAY)

    assert [c["payee_key"] for c in out] == ["stream box"]
    assert out[0]["hits"] == 3
    assert out[0]["median_amount"] == 15.99
    assert out[0]["account_slug"] == "checking"


# ---------------------------------------------------------------------------
# end-to-end: a stale orphan does not satisfy a bill
# ---------------------------------------------------------------------------


def test_reconcile_ignores_a_stale_orphan_when_matching_bills(tmp_path: Path, monkeypatch) -> None:
    """Even if a retired row's date were inside the window it must not match."""
    ws = _stage_workspace(
        tmp_path,
        transactions=[
            # A superseded pending row whose date got copied from its twin.
            _txn_row("simplefin:sf-acc-1:p1", pending=True, date="2026-09-01",
                     superseded_by="simplefin:sf-acc-1:t1"),
            # A stale orphan hand-dated inside the window.
            _txn_row("simplefin:sf-acc-1:p2", pending=True, date="2026-09-01",
                     stale_pending=True),
        ],
        bills=[{
            "id": "bill-power", "name": "Power", "amount": 120.00,
            "cadence": "one-shot", "next_due": "2026-09-01",
            "pay_from_account": "checking", "status": "active",
        }],
    )

    class _FixedDate(dt.date):
        @classmethod
        def today(cls) -> dt.date:  # type: ignore[override]
            return TODAY

    monkeypatch.setattr(rt.dt, "date", _FixedDate)
    report = rt.reconcile(ws, days=7)

    assert report["totals"]["transactions"] == 0
    assert report["totals"]["bills_missed"] == 1
    assert report["totals"]["bills_matched"] == 0
    assert report["bills_missed"][0]["bill_id"] == "bill-power"
