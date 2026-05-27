# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""SimpleFin Bridge ingestor.

Pulls bank / credit-card / brokerage transactions from SimpleFin Bridge
(https://beta-bridge.simplefin.org/) into `_memory/transactions.yaml`.

The long-lived Access URL is generated once by claiming a setup token
(see `superagent/tools/simplefin_claim.py`) and lives in
`workspace/_memory/sensitive/simplefin-credentials.yaml` with mode 600.

SimpleFin's `/accounts` endpoint:
  * Auth: HTTP Basic, embedded in the Access URL userinfo. The password
    may itself contain a colon, which urllib's parser cannot handle —
    `split_access_url` extracts userinfo by regex instead.
  * Window: 90 days max per call. Larger backfills are chunked.
  * Budget: <= 24 requests per day total across this account.

Idempotency: each transaction is keyed by
`simplefin:<account_id>:<transaction_id>`. Re-runs over the same window
update no rows; only genuinely new rows are appended. Pending transactions
that later post show up as a *new* row (different upstream id) — that is
the SimpleFin behavior and matches user-visible bank-statement behavior.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso

DEFAULT_RECENCY_DAYS = 30
DEFAULT_BACKFILL_DAYS = 365
DEFAULT_MAX_ITEMS = 2000
SIMPLEFIN_WINDOW_DAYS = 45  # SimpleFin's recommended soft-cap; >45d windows
# trigger a warning and may be capped server-side. Docs say 90; the API itself
# warns at 45 (observed 2026-05-27).


def split_access_url(access_url: str) -> tuple[str, str, str]:
    """Return (clean_url_without_userinfo, username, password).

    Handles passwords containing colons (which urllib's URL parser cannot).
    """
    m = re.match(r"^(https?://)([^@]+)@(.+)$", access_url)
    if not m:
        raise ValueError("access URL missing userinfo")
    scheme, userinfo, rest = m.groups()
    user, _, password = userinfo.partition(":")
    return f"{scheme}{rest}", user, password


def basic_auth_header(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def http_get_json(url: str, auth_header: str, timeout: int = 60) -> dict[str, Any]:
    req = Request(
        url,
        method="GET",
        headers={
            "Authorization": auth_header,
            "User-Agent": "superagent-simplefin/0.1",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_amount(raw: Any) -> float:
    """SimpleFin returns amounts as decimal strings (signed). Parse robustly."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    return float(str(raw).replace(",", "").replace("$", ""))


def unix_to_iso_date(ts: Any) -> str:
    """SimpleFin timestamps are seconds since epoch (int)."""
    if ts is None:
        return ""
    return dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).strftime("%Y-%m-%d")


class SimpleFinIngestor(IngestorBase):
    """SimpleFin Bridge transactions -> transactions.yaml."""

    source = "simplefin"
    kind = "api"
    description = "SimpleFin Bridge bank/CC/brokerage transactions."

    CREDENTIAL_PATH = Path("_memory/sensitive/simplefin-credentials.yaml")
    INDEX_PATH = Path("_memory/transactions.yaml")
    DATA_SOURCES_PATH = Path("_memory/data-sources.yaml")
    INGESTION_LOG_PATH = Path("_memory/ingestion-log.yaml")

    def _credentials_file(self) -> Path:
        return self.workspace / self.CREDENTIAL_PATH

    def _load_access_url(self) -> str | None:
        path = self._credentials_file()
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            return None
        url = data.get("access_url") if isinstance(data, dict) else None
        return url if isinstance(url, str) and url.startswith("https://") else None

    def probe(self) -> ProbeResult:
        access_url = self._load_access_url()
        if not access_url:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NEEDS_SETUP,
                detail=f"missing {self.CREDENTIAL_PATH}",
                setup_hint=(
                    "Sign up at https://beta-bridge.simplefin.org, generate a setup "
                    "token, then run `uv run python -m superagent.tools.simplefin_claim "
                    "<TOKEN>` to claim it."
                ),
            )
        try:
            base, user, password = split_access_url(access_url)
        except ValueError as exc:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.PERMISSION_DENIED,
                detail=f"malformed access URL: {exc}",
            )
        try:
            http_get_json(
                base.rstrip("/") + "/accounts?balances-only=1",
                basic_auth_header(user, password),
                timeout=15,
            )
        except HTTPError as exc:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.PERMISSION_DENIED,
                detail=f"HTTP {exc.code}",
            )
        except URLError as exc:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NOT_DETECTED,
                detail=f"network: {exc.reason}",
            )
        return ProbeResult(source=self.source, status=ProbeStatus.AVAILABLE)

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        started = now_iso()
        t0 = time.time()
        result = RunResult(source=self.source, started_at=started, finished_at=started)

        access_url = self._load_access_url()
        if not access_url:
            result.errors.append(f"missing {self.CREDENTIAL_PATH}; run simplefin_claim first")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        try:
            base, user, password = split_access_url(access_url)
        except ValueError as exc:
            result.errors.append(f"malformed access URL: {exc}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        auth_header = basic_auth_header(user, password)

        last_ingest_iso = config_row.get("last_ingest")
        recency_days = int(config_row.get("recency_window_days") or DEFAULT_RECENCY_DAYS)
        backfill_days = int(config_row.get("backfill_window_days") or DEFAULT_BACKFILL_DAYS)
        max_items = int(config_row.get("max_items_per_run") or DEFAULT_MAX_ITEMS)
        backfill = bool(config_row.get("backfill"))
        include_pending = bool(config_row.get("include_pending", True))

        now_utc = dt.datetime.now(tz=dt.UTC)
        if backfill:
            window_start = now_utc - dt.timedelta(days=backfill_days)
        elif last_ingest_iso:
            try:
                window_start = dt.datetime.fromisoformat(
                    str(last_ingest_iso).replace("Z", "+00:00")
                )
                if window_start.tzinfo is None:
                    window_start = window_start.replace(tzinfo=dt.UTC)
            except ValueError:
                window_start = now_utc - dt.timedelta(days=recency_days)
        else:
            window_start = now_utc - dt.timedelta(days=recency_days)

        # Always include a 3-day overlap to catch late-posting transactions.
        window_start -= dt.timedelta(days=3)
        chunks = _chunk_range(window_start, now_utc, SIMPLEFIN_WINDOW_DAYS)
        result.notes = (
            f"window {window_start.date()}..{now_utc.date()} "
            f"({len(chunks)} chunk(s) of <={SIMPLEFIN_WINDOW_DAYS}d)"
        )

        all_txns: list[dict[str, Any]] = []
        errors: list[str] = []
        accounts_seen: list[dict[str, Any]] = []
        for since, until in chunks:
            url = (
                base.rstrip("/")
                + f"/accounts?start-date={int(since.timestamp())}"
                + f"&end-date={int(until.timestamp())}"
                + f"&pending={1 if include_pending else 0}"
            )
            try:
                data = http_get_json(url, auth_header)
            except (HTTPError, URLError) as exc:
                errors.append(f"fetch {since.date()}..{until.date()}: {exc}")
                continue
            for err in data.get("errors") or []:
                errors.append(str(err))
            for acc in data.get("accounts") or []:
                accounts_seen.append(acc)
                org = (acc.get("org") or {}).get("name") or "?"
                acc_id = acc.get("id") or ""
                acc_name = acc.get("name") or ""
                acc_currency = acc.get("currency") or "USD"
                for txn in acc.get("transactions") or []:
                    norm = self._normalize(txn, acc_id, acc_name, org, acc_currency)
                    if norm is not None:
                        all_txns.append(norm)

        result.items_pulled = len(all_txns)
        result.errors = errors

        if len(all_txns) > max_items:
            result.truncated = True
            all_txns = all_txns[:max_items]

        idx_path = self.workspace / self.INDEX_PATH
        index = _load_index(idx_path)
        existing_ids = {
            row.get("external_id")
            for row in (index.get("transactions") or [])
            if isinstance(row, dict)
        }

        inserted = 0
        skipped = 0
        for row in all_txns:
            if row["external_id"] in existing_ids:
                skipped += 1
                continue
            index.setdefault("transactions", []).append(row)
            existing_ids.add(row["external_id"])
            inserted += 1

        result.items_inserted = inserted
        result.items_skipped = skipped
        result.destination_summary = {
            "transactions": inserted,
            "institutions": sorted({
                (a.get("org") or {}).get("name", "?") for a in accounts_seen
            }),
            "accounts": len({a.get("id") for a in accounts_seen if a.get("id")}),
        }

        if dry_run:
            result.notes = (result.notes + f"; dry-run, would insert {inserted}").strip("; ")
        elif inserted > 0:
            _save_index(idx_path, index)

        result.finished_at = now_iso()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def _normalize(
        self,
        txn: dict[str, Any],
        account_id: str,
        account_name: str,
        institution: str,
        currency: str,
    ) -> dict[str, Any] | None:
        txn_id = txn.get("id")
        if not txn_id:
            return None
        try:
            amount = parse_amount(txn.get("amount"))
        except (TypeError, ValueError):
            return None
        posted_iso = unix_to_iso_date(txn.get("posted"))
        transacted_iso = unix_to_iso_date(txn.get("transacted_at")) or posted_iso
        return {
            "external_id": f"simplefin:{account_id}:{txn_id}",
            "date": posted_iso or transacted_iso,
            "transacted_at": transacted_iso or None,
            "payee": (txn.get("payee") or "").strip() or None,
            "description": (txn.get("description") or "").strip(),
            "memo": (txn.get("memo") or "").strip() or None,
            "amount": amount,
            "currency": currency,
            "category": "uncategorized",
            "pending": bool(txn.get("pending", False)),
            "account_id": account_id,
            "account_label": account_name,
            "institution": institution,
            "source": "simplefin",
            "extra": txn.get("extra") or {},
        }


def _chunk_range(
    start: dt.datetime, end: dt.datetime, max_days: int
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Split [start, end] into chunks no wider than `max_days`."""
    if end <= start:
        return [(start, end)]
    chunks: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = start
    width = dt.timedelta(days=max_days)
    while cursor < end:
        nxt = min(cursor + width, end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


def _load_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "transactions": []}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {"schema_version": 1, "transactions": []}
    if not isinstance(data, dict):
        return {"schema_version": 1, "transactions": []}
    data.setdefault("transactions", [])
    return data


def _save_index(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def _load_data_sources(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "sources": []}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        return {"schema_version": 1, "sources": []}
    data.setdefault("sources", [])
    return data


def _save_data_sources(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = now_iso()
    with path.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def _find_source_row(data: dict[str, Any], source: str) -> dict[str, Any] | None:
    for row in data.get("sources") or []:
        if isinstance(row, dict) and row.get("id") == source:
            return row
    return None


def _next_log_id(log: dict[str, Any]) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    n = 1
    for row in log.get("runs") or []:
        if isinstance(row, dict):
            rid = str(row.get("id") or "")
            if rid.startswith(f"ingest-{today}-"):
                with contextlib.suppress(ValueError):
                    n = max(n, int(rid.rsplit("-", 1)[-1]) + 1)
    return f"ingest-{today}-{n:03d}"


def _append_ingestion_log(workspace: Path, run_id: str, log_row: dict[str, Any]) -> None:
    path = workspace / SimpleFinIngestor.INGESTION_LOG_PATH
    if not path.exists():
        return
    log = yaml.safe_load(path.read_text()) or {"runs": []}
    if not isinstance(log, dict):
        log = {"runs": []}
    log.setdefault("runs", [])
    # Drop the placeholder template row if present.
    log["runs"] = [r for r in log["runs"] if isinstance(r, dict) and r.get("id")]
    log["runs"].append({"id": run_id, **log_row})
    with path.open("w") as fh:
        yaml.safe_dump(log, fh, sort_keys=False, allow_unicode=True)


def _update_source_row(
    workspace: Path, source: str, result: RunResult, run_id: str, window: dict[str, Any]
) -> None:
    path = workspace / SimpleFinIngestor.DATA_SOURCES_PATH
    data = _load_data_sources(path)
    row = _find_source_row(data, source)
    if row is None:
        return
    row["last_ingest"] = result.finished_at
    row["last_run"] = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "items_pulled": result.items_pulled,
        "items_inserted": result.items_inserted,
        "items_updated": result.items_updated,
        "items_skipped": result.items_skipped,
        "errors": result.errors,
        "truncated": result.truncated,
        "duration_ms": result.duration_ms,
        "run_log_id": run_id,
    }
    if result.errors:
        row["failure_streak"] = int(row.get("failure_streak") or 0) + 1
    else:
        row["failure_streak"] = 0
    _save_data_sources(path, data)


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest-simplefin")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--backfill", action="store_true",
                        help="Pull backfill_window_days instead of incremental delta.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pending", action="store_true",
                        help="Exclude pending transactions.")
    args = parser.parse_args()

    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"

    data_sources = _load_data_sources(workspace / SimpleFinIngestor.DATA_SOURCES_PATH)
    row = _find_source_row(data_sources, "simplefin") or {}
    config_row: dict[str, Any] = {
        "last_ingest": row.get("last_ingest"),
        "recency_window_days": row.get("recency_window_days", DEFAULT_RECENCY_DAYS),
        "backfill_window_days": row.get("backfill_window_days", DEFAULT_BACKFILL_DAYS),
        "max_items_per_run": row.get("max_items_per_run", DEFAULT_MAX_ITEMS),
        "backfill": args.backfill,
        "include_pending": not args.no_pending,
    }

    ingestor = SimpleFinIngestor(workspace)
    result = ingestor.run(config_row, dry_run=args.dry_run)

    if not args.dry_run:
        log_path = workspace / SimpleFinIngestor.INGESTION_LOG_PATH
        log_data = yaml.safe_load(log_path.read_text()) if log_path.exists() else {"runs": []}
        if not isinstance(log_data, dict):
            log_data = {"runs": []}
        run_id = _next_log_id(log_data)
        log_row = result.to_log_row(run_id, trigger="manual", window=None)
        log_row.pop("id", None)
        _append_ingestion_log(workspace, run_id, log_row)
        _update_source_row(workspace, "simplefin", result, run_id, window=None)

    print(
        f"pulled={result.items_pulled} inserted={result.items_inserted} "
        f"skipped={result.items_skipped} errors={len(result.errors)} "
        f"truncated={result.truncated} duration_ms={result.duration_ms}"
    )
    if result.notes:
        print(f"  notes: {result.notes}")
    if result.destination_summary:
        insts = result.destination_summary.get("institutions") or []
        print(f"  institutions: {', '.join(insts)}")
        print(f"  accounts: {result.destination_summary.get('accounts')}")
    for err in result.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
