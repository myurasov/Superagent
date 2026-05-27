# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Reconcile ingested transactions against bills.yaml and subscriptions.yaml.

Read-only analysis. Produces:
  * Bills due in the last N days that DO and DO NOT have a matching txn.
  * Recurring-charge candidates: payees seen 3+ times at near-monthly cadence
    over the last 180 days that are NOT already tracked as bills or subs.
  * Optional --json for skill consumption.

Designed to be called by the weekly-review skill (step 2: Bookkeeper pass)
or directly from the CLI.

Usage:
  uv run python -m superagent.tools.reconcile_transactions [--days 7]
  uv run python -m superagent.tools.reconcile_transactions --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BILL_MATCH_DAYS = 5            # +/- window around due date for a bill match
SUB_MATCH_DAYS = 5             # +/- window for subscription renewal match
BILL_MATCH_AMOUNT_PCT = 0.10   # +/- 10% of expected amount
RECURRING_MIN_HITS = 3         # need 3 charges to call something recurring
RECURRING_LOOKBACK_DAYS = 180  # window for recurring-charge detection
NEAR_MONTHLY_DAYS = (25, 35)   # interval span considered "monthly"


@dataclass
class Account:
    slug: str
    name: str
    simplefin_id: str | None


@dataclass
class Txn:
    date: dt.date
    payee: str
    description: str
    amount: float
    account_slug: str | None
    account_id: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def signed_outflow(self) -> bool:
        return self.amount < 0

    @property
    def abs_amount(self) -> float:
        return abs(self.amount)

    @property
    def payee_key(self) -> str:
        text = (self.payee or self.description or "").lower()
        text = re.sub(r"\d+", " ", text)
        text = re.sub(r"[^a-z ]+", " ", text)
        text = " ".join(text.split())
        # Strip very generic trailing tokens.
        for trail in (" payment", " purchase", " debit", " credit"):
            if text.endswith(trail):
                text = text[: -len(trail)]
        return text.strip() or "(unknown)"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_accounts(workspace: Path) -> dict[str, Account]:
    data = _load(workspace / "_memory" / "accounts-index.yaml")
    out: dict[str, Account] = {}
    for row in data.get("accounts") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        sf = row.get("simplefin_account_id")
        out[row["id"]] = Account(slug=row["id"], name=row.get("name", ""), simplefin_id=sf)
    return out


def load_transactions(workspace: Path, accounts: dict[str, Account]) -> list[Txn]:
    """Map SimpleFin account_id -> account_slug; build a list of Txn dataclasses."""
    sf_to_slug: dict[str, str] = {}
    for slug, acc in accounts.items():
        if acc.simplefin_id:
            sf_to_slug[acc.simplefin_id] = slug
    data = _load(workspace / "_memory" / "transactions.yaml")
    out: list[Txn] = []
    for row in data.get("transactions") or []:
        if not isinstance(row, dict):
            continue
        date_str = row.get("date") or ""
        try:
            date = dt.date.fromisoformat(date_str)
        except (TypeError, ValueError):
            continue
        out.append(
            Txn(
                date=date,
                payee=str(row.get("payee") or ""),
                description=str(row.get("description") or ""),
                amount=float(row.get("amount") or 0),
                account_id=str(row.get("account_id") or ""),
                account_slug=sf_to_slug.get(row.get("account_id")),
                raw=row,
            )
        )
    out.sort(key=lambda t: t.date)
    return out


def expected_due_dates(bill: dict[str, Any], window_start: dt.date, window_end: dt.date) -> list[dt.date]:
    """Compute every expected due date for `bill` that falls inside the window."""
    cadence = (bill.get("cadence") or "").lower()
    due_day = bill.get("due_day")
    next_due_str = bill.get("next_due") or ""
    out: list[dt.date] = []

    next_due: dt.date | None = None
    if next_due_str:
        try:
            next_due = dt.date.fromisoformat(next_due_str)
        except ValueError:
            next_due = None

    if cadence in ("one-shot", "irregular", ""):
        if next_due and window_start <= next_due <= window_end:
            out.append(next_due)
        return out

    if cadence == "monthly" and isinstance(due_day, int) and 1 <= due_day <= 28:
        cursor = window_start.replace(day=1)
        while cursor <= window_end:
            try:
                d = cursor.replace(day=due_day)
            except ValueError:
                cursor = _next_month(cursor)
                continue
            if window_start <= d <= window_end:
                out.append(d)
            cursor = _next_month(cursor)
        return out

    # Fall back to next_due alone for other cadences.
    if next_due and window_start <= next_due <= window_end:
        out.append(next_due)
    return out


def _next_month(d: dt.date) -> dt.date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1)
    return d.replace(month=d.month + 1)


def find_bill_match(
    bill: dict[str, Any], expected_date: dt.date, transactions: list[Txn]
) -> Txn | None:
    pay_from = bill.get("pay_from_account") or bill.get("account")
    expected_amount = float(bill.get("amount") or 0)
    tol_days = dt.timedelta(days=BILL_MATCH_DAYS)
    for t in transactions:
        if pay_from and t.account_slug != pay_from:
            continue
        if abs((t.date - expected_date).days) > BILL_MATCH_DAYS:
            continue
        if expected_amount and t.abs_amount > 0:
            pct = abs(t.abs_amount - expected_amount) / max(expected_amount, 1.0)
            if pct > BILL_MATCH_AMOUNT_PCT:
                continue
        if expected_amount and not t.signed_outflow:
            continue
        return t
        del tol_days  # unused; left in for future widening
    return None


def detect_recurring_candidates(
    transactions: list[Txn],
    bills: list[dict[str, Any]],
    subscriptions: list[dict[str, Any]],
    today: dt.date,
) -> list[dict[str, Any]]:
    cutoff = today - dt.timedelta(days=RECURRING_LOOKBACK_DAYS)
    by_key: dict[tuple[str, str], list[Txn]] = defaultdict(list)
    for t in transactions:
        if t.date < cutoff or not t.signed_outflow:
            continue
        by_key[(t.account_slug or "", t.payee_key)].append(t)

    known_payees = set()
    for b in bills:
        if isinstance(b, dict) and b.get("payee"):
            known_payees.add(_normalize(b["payee"]))
        if isinstance(b, dict) and b.get("name"):
            known_payees.add(_normalize(b["name"]))
    for s in subscriptions:
        if isinstance(s, dict) and s.get("provider"):
            known_payees.add(_normalize(s["provider"]))
        if isinstance(s, dict) and s.get("name"):
            known_payees.add(_normalize(s["name"]))

    out: list[dict[str, Any]] = []
    for (acct, key), hits in by_key.items():
        if len(hits) < RECURRING_MIN_HITS:
            continue
        if _matches_known(key, known_payees):
            continue
        hits.sort(key=lambda t: t.date)
        intervals = [
            (hits[i + 1].date - hits[i].date).days for i in range(len(hits) - 1)
        ]
        # Near-monthly cadence: median interval between 25 and 35 days.
        intervals.sort()
        median = intervals[len(intervals) // 2] if intervals else 0
        if not (NEAR_MONTHLY_DAYS[0] <= median <= NEAR_MONTHLY_DAYS[1]):
            continue
        amounts = sorted(h.abs_amount for h in hits)
        median_amount = amounts[len(amounts) // 2]
        out.append({
            "payee_key": key,
            "sample_payee": hits[-1].payee or hits[-1].description,
            "account_slug": acct or None,
            "hits": len(hits),
            "first": hits[0].date.isoformat(),
            "last": hits[-1].date.isoformat(),
            "median_interval_days": median,
            "median_amount": round(median_amount, 2),
        })
    out.sort(key=lambda r: (-r["hits"], r["payee_key"]))
    return out


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", (s or "").lower())).strip()


def _matches_known(key: str, known: set[str]) -> bool:
    if key in known:
        return True
    for k in known:
        if not k:
            continue
        if k in key or key in k:
            return True
    return False


def reconcile(workspace: Path, days: int) -> dict[str, Any]:
    today = dt.date.today()
    window_start = today - dt.timedelta(days=days)
    accounts = load_accounts(workspace)
    transactions = load_transactions(workspace, accounts)

    bills_data = _load(workspace / "_memory" / "bills.yaml")
    bills = [b for b in (bills_data.get("bills") or []) if isinstance(b, dict) and b.get("id")]

    subs_data = _load(workspace / "_memory" / "subscriptions.yaml")
    subscriptions = [
        s for s in (subs_data.get("subscriptions") or [])
        if isinstance(s, dict) and s.get("id")
    ]

    matched: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for bill in bills:
        if bill.get("status") and bill["status"] not in (None, "active"):
            continue
        for d in expected_due_dates(bill, window_start, today):
            txn = find_bill_match(bill, d, transactions)
            row = {
                "bill_id": bill["id"],
                "name": bill.get("name") or bill["id"],
                "expected_date": d.isoformat(),
                "expected_amount": float(bill.get("amount") or 0),
                "pay_from": bill.get("pay_from_account") or bill.get("account"),
            }
            if txn:
                row["matched_txn"] = {
                    "date": txn.date.isoformat(),
                    "amount": txn.abs_amount,
                    "payee": txn.payee or txn.description,
                    "external_id": txn.raw.get("external_id"),
                }
                matched.append(row)
            else:
                missed.append(row)

    recurring = detect_recurring_candidates(transactions, bills, subscriptions, today)

    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_days": days,
        "window_start": window_start.isoformat(),
        "window_end": today.isoformat(),
        "totals": {
            "transactions": len(transactions),
            "bills_tracked": len(bills),
            "subscriptions_tracked": len(subscriptions),
            "bills_matched": len(matched),
            "bills_missed": len(missed),
            "recurring_candidates": len(recurring),
        },
        "bills_matched": matched,
        "bills_missed": missed,
        "recurring_candidates": recurring,
    }


def format_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    t = report["totals"]
    lines.append(
        f"# Reconciliation — {report['window_start']} -> {report['window_end']} "
        f"({report['window_days']}d)"
    )
    lines.append("")
    lines.append(
        f"- Transactions in store: **{t['transactions']}**; "
        f"bills tracked: **{t['bills_tracked']}**; "
        f"subscriptions tracked: **{t['subscriptions_tracked']}**."
    )
    lines.append(
        f"- Bills due in window: **{t['bills_matched']} matched**, "
        f"**{t['bills_missed']} missed**."
    )
    lines.append(f"- Recurring-charge candidates (not yet tracked): **{t['recurring_candidates']}**.")
    lines.append("")
    if report["bills_missed"]:
        lines.append("## Missed bills")
        for r in report["bills_missed"]:
            lines.append(
                f"- **{r['name']}** — expected ${r['expected_amount']:.2f} on "
                f"{r['expected_date']} from `{r['pay_from']}` (no match)."
            )
        lines.append("")
    if report["bills_matched"]:
        lines.append("## Matched bills")
        for r in report["bills_matched"]:
            m = r["matched_txn"]
            lines.append(
                f"- {r['name']} — ${m['amount']:.2f} on {m['date']} ({m['payee']})."
            )
        lines.append("")
    if report["recurring_candidates"]:
        lines.append("## Recurring-charge candidates (consider adding to bills.yaml / subscriptions.yaml)")
        for r in report["recurring_candidates"]:
            lines.append(
                f"- **{r['sample_payee']}** — {r['hits']} charges, "
                f"~${r['median_amount']:.2f} every {r['median_interval_days']}d "
                f"(account: `{r['account_slug'] or 'unknown'}`, last: {r['last']})."
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(prog="reconcile-transactions")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--days", type=int, default=7,
                        help="Bill-reconciliation window (default: 7).")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    args = parser.parse_args()
    framework = Path(__file__).resolve().parents[1]
    workspace = args.workspace or framework.parent / "workspace"

    report = reconcile(workspace, args.days)
    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
