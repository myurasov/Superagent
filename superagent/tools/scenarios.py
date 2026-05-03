#!/usr/bin/env python3
"""Scenario / what-if engine.

Implements superagent/docs/_internal/ideas-better-structure.md item #14.

Five canned scenarios (no generic engine yet — that's the L-tier roadmap
item). Each reads workspace state and answers a specific what-if question.
Outputs render to stdout AND optionally to `Outbox/scenarios/<name>.md`.

Available:
  cancel-subscriptions  — annual savings if N subscriptions are cancelled
  trial-end-impact      — if all currently-active trials convert, monthly delta
  bill-shock            — if every bill goes up by N%, what's the monthly impact
  balance-floor         — given upcoming bills + subs, when does account X dip below threshold
  project-overrun       — if project X overruns by N%, does the budget still fit
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

import yaml


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        out = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def annualize(amount: float, cadence: str) -> float:
    multipliers = {
        "weekly": 52, "bi-weekly": 26, "monthly": 12,
        "semi-monthly": 24, "bi-monthly": 6,
        "quarterly": 4, "semi-annual": 2, "annual": 1,
        "one-shot": 1, "irregular": 1,
    }
    return amount * multipliers.get(cadence, 12)


# --- Scenario implementations -------------------------------------------------

def cancel_subscriptions(workspace: Path, ids: list[str]) -> dict[str, Any]:
    data = load_yaml(workspace / "_memory" / "subscriptions.yaml") or {}
    rows = [r for r in (data.get("subscriptions") or []) if isinstance(r, dict)]
    matches = [r for r in rows if r.get("id") in ids or r.get("name") in ids]
    if not matches:
        return {"error": f"no matching subscriptions for {ids}",
                "available": [r.get("id") for r in rows if r.get("id")]}
    annual = sum(
        annualize(float(r.get("amount") or 0), r.get("cadence", "monthly"))
        for r in matches
    )
    monthly = annual / 12
    return {
        "scenario": "cancel-subscriptions",
        "matched": [{"id": r.get("id"), "name": r.get("name"),
                     "annual": annualize(float(r.get("amount") or 0),
                                          r.get("cadence", "monthly"))}
                    for r in matches],
        "monthly_savings": round(monthly, 2),
        "annual_savings": round(annual, 2),
        "currency": matches[0].get("currency", "USD"),
    }


def trial_end_impact(workspace: Path) -> dict[str, Any]:
    data = load_yaml(workspace / "_memory" / "subscriptions.yaml") or {}
    rows = [r for r in (data.get("subscriptions") or []) if isinstance(r, dict)]
    trials = [r for r in rows if r.get("status") == "trial" or r.get("trial_ends")]
    monthly = sum(
        annualize(float(r.get("amount") or 0), r.get("cadence", "monthly")) / 12
        for r in trials
    )
    return {
        "scenario": "trial-end-impact",
        "trials": [{"id": r.get("id"), "name": r.get("name"),
                    "amount": r.get("amount"), "cadence": r.get("cadence"),
                    "trial_ends": r.get("trial_ends")} for r in trials],
        "monthly_increase_if_all_convert": round(monthly, 2),
        "annual_increase": round(monthly * 12, 2),
    }


def bill_shock(workspace: Path, percent: float) -> dict[str, Any]:
    data = load_yaml(workspace / "_memory" / "bills.yaml") or {}
    rows = [r for r in (data.get("bills") or []) if isinstance(r, dict) and r.get("status") == "active"]
    base_monthly = sum(
        annualize(float(r.get("amount") or 0), r.get("cadence", "monthly")) / 12
        for r in rows
    )
    delta_monthly = base_monthly * (percent / 100.0)
    return {
        "scenario": "bill-shock",
        "percent": percent,
        "base_monthly": round(base_monthly, 2),
        "increase_monthly": round(delta_monthly, 2),
        "new_monthly": round(base_monthly + delta_monthly, 2),
        "annual_increase": round(delta_monthly * 12, 2),
        "bill_count": len(rows),
    }


def balance_floor(workspace: Path, account_id: str,
                   starting_balance: float, days: int = 60) -> dict[str, Any]:
    bills = load_yaml(workspace / "_memory" / "bills.yaml") or {}
    rows = [r for r in (bills.get("bills") or [])
            if isinstance(r, dict) and r.get("status") == "active"
            and r.get("pay_from_account") == account_id]
    today = now_dt().date()
    horizon = today + dt.timedelta(days=days)
    events: list[tuple[dt.date, float, str]] = []
    for r in rows:
        nd = parse_iso_dt(r.get("next_due"))
        if nd is None:
            continue
        nd_date = nd.date()
        if nd_date <= horizon:
            events.append((nd_date, -float(r.get("amount") or 0), r.get("name", r.get("id", ""))))
    events.sort()
    balance = starting_balance
    timeline: list[dict[str, Any]] = []
    floor_balance = balance
    floor_date = today
    for date, delta, name in events:
        balance += delta
        timeline.append({
            "date": date.isoformat(), "delta": round(delta, 2),
            "balance_after": round(balance, 2), "what": name,
        })
        if balance < floor_balance:
            floor_balance = balance
            floor_date = date
    return {
        "scenario": "balance-floor",
        "account": account_id,
        "starting_balance": starting_balance,
        "horizon_days": days,
        "events": timeline,
        "floor_balance": round(floor_balance, 2),
        "floor_date": floor_date.isoformat(),
        "ending_balance": round(balance, 2),
    }


def project_overrun(workspace: Path, project_id: str, percent: float) -> dict[str, Any]:
    data = load_yaml(workspace / "_memory" / "projects-index.yaml") or {}
    project = next(
        (p for p in (data.get("projects") or [])
         if isinstance(p, dict) and (p.get("id") == project_id or p.get("name") == project_id)),
        None,
    )
    if project is None:
        return {"error": f"project not found: {project_id}"}
    budget = project.get("budget") or {}
    planned = float(budget.get("planned") or 0)
    spent = float(budget.get("spent") or 0)
    if planned == 0:
        return {"error": f"project '{project_id}' has no planned budget"}
    overrun = planned * (percent / 100.0)
    new_total = planned + overrun
    return {
        "scenario": "project-overrun",
        "project": project_id,
        "name": project.get("name"),
        "planned": planned,
        "spent": spent,
        "remaining": planned - spent,
        "overrun_percent": percent,
        "overrun_amount": round(overrun, 2),
        "new_total": round(new_total, 2),
        "currency": budget.get("currency", "USD"),
    }


SCENARIOS = {
    "cancel-subscriptions": cancel_subscriptions,
    "trial-end-impact": trial_end_impact,
    "bill-shock": bill_shock,
    "balance-floor": balance_floor,
    "project-overrun": project_overrun,
}


def render_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Scenario: {result.get('scenario', '?')}", ""]
    for k, v in result.items():
        if k == "scenario":
            continue
        if isinstance(v, list):
            lines.append(f"## {k}")
            lines.append("")
            for item in v:
                if isinstance(item, dict):
                    lines.append("- " + ", ".join(f"**{ik}**: {iv}" for ik, iv in item.items()))
                else:
                    lines.append(f"- {item}")
            lines.append("")
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scenarios")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List available scenarios.")
    c = sub.add_parser("cancel-subscriptions")
    c.add_argument("ids", nargs="+", help="Subscription ids or names to cancel.")
    sub.add_parser("trial-end-impact")
    b = sub.add_parser("bill-shock")
    b.add_argument("--percent", type=float, required=True)
    f = sub.add_parser("balance-floor")
    f.add_argument("--account", type=str, required=True)
    f.add_argument("--starting-balance", type=float, required=True)
    f.add_argument("--days", type=int, default=60)
    p = sub.add_parser("project-overrun")
    p.add_argument("project", type=str)
    p.add_argument("--percent", type=float, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Optional path under Outbox/scenarios/.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        for name in SCENARIOS:
            print(name)
        return 0
    result: dict[str, Any] = {}
    if args.cmd == "cancel-subscriptions":
        result = cancel_subscriptions(workspace, args.ids)
    elif args.cmd == "trial-end-impact":
        result = trial_end_impact(workspace)
    elif args.cmd == "bill-shock":
        result = bill_shock(workspace, args.percent)
    elif args.cmd == "balance-floor":
        result = balance_floor(workspace, args.account, args.starting_balance, args.days)
    elif args.cmd == "project-overrun":
        result = project_overrun(workspace, args.project, args.percent)
    else:
        return 2
    body = render_markdown(result)
    print(body)
    if args.out:
        out = workspace / "Outbox" / "scenarios" / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body)
        print(f"# saved to {out}")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
