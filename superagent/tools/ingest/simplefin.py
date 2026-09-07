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
  * Latency: highly variable. The bridge refreshes institutions
    server-side while the request is open, so a call that normally
    returns in a few seconds can take tens of seconds; a connection
    that needs re-authentication drags out the whole response. Hence
    the generous `DEFAULT_HTTP_TIMEOUT` and the `--timeout` override.

Every fetch failure — HTTP error, network error, or read timeout — is
recorded on the RunResult and surfaces in `_memory/ingestion-log.yaml`.
A refresh that fails must never be silent: a caller reading the log
later has to be able to tell "no new transactions" apart from "the
last attempt never completed".

Idempotency: each transaction is keyed by
`simplefin:<account_id>:<transaction_id>`. Re-runs over the same window
update no rows; only genuinely new rows are appended. Pending transactions
that later post show up as a *new* row (different upstream id) — that is
the SimpleFin behavior and matches user-visible bank-statement behavior.

Because the posted twin arrives under a NEW id, the stored pending row
(whose `posted` was 0, coercing its `date` to 1970-01-01) would otherwise
stay orphaned forever. A reconciliation pass at the end of every run —
also runnable standalone via `--reconcile` / `--reconcile-dry-run` — marks
each such orphan with `superseded_by: <posted external_id>` and copies the
posted date onto it when a UNIQUE same-account / near-amount / near-date /
fuzzy-payee posted twin exists. "Near-amount" allows a small absolute delta
(`RECONCILE_AMOUNT_TOLERANCE`, card tips and rounding) — never a percentage.
Ambiguous orphans (2+ candidates, or two orphans claiming the same posted
row) are skipped, counted, and listed by external_id in the run summary;
specific rows can be held out with `--reconcile-exclude <external_id>`
(repeatable).

Orphans that never find a twin do not linger unmarked either. A second,
additive pass (`mark_stale_pending`) flags every still-orphaned pending row
whose `transacted_at` is older than `stale_pending_days` (default
`DEFAULT_STALE_PENDING_DAYS`; per-source override on the data-sources row)
with `stale_pending: true`. The row is kept verbatim — the flag only tells
window-based consumers (`reconcile_transactions`, expense totals) to skip
it, the same way they skip `superseded_by` rows.
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
DEFAULT_HTTP_TIMEOUT = 180  # seconds; see the module docstring on latency.
PROBE_HTTP_TIMEOUT = 15  # probes ask for balances only and must stay snappy.
SIMPLEFIN_WINDOW_DAYS = 45  # SimpleFin's recommended soft-cap; >45d windows
# trigger a warning and may be capped server-side. Docs say 90; the API itself
# warns at 45 (observed 2026-05-27).
RECONCILE_ORPHAN_DATE = "1970-01-01"  # what posted=0 coerces to in _normalize.
RECONCILE_WINDOW_DAYS = 7  # +/- days between orphan transacted_at and twin date.
RECONCILE_TOKEN_OVERLAP = 0.6  # min shared-token ratio for a fuzzy text match.
RECONCILE_AMOUNT_TOLERANCE = 1.00  # max absolute amount delta (same sign) for a twin.
DEFAULT_STALE_PENDING_DAYS = 14  # orphan age (by transacted_at) before stale_pending.


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


def http_get_json(
    url: str, auth_header: str, timeout: int = DEFAULT_HTTP_TIMEOUT
) -> dict[str, Any]:
    """GET `url` with HTTP Basic auth and decode the JSON body.

    Raises `HTTPError` / `URLError` on protocol and network failures and
    `TimeoutError` when the read exceeds `timeout` seconds; callers are
    responsible for recording those rather than letting them propagate.
    """
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
    affected_domains = ("finances",)

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
                timeout=PROBE_HTTP_TIMEOUT,
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
        timeout = int(config_row.get("timeout") or DEFAULT_HTTP_TIMEOUT)

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
                data = http_get_json(url, auth_header, timeout=timeout)
            except (HTTPError, URLError, TimeoutError) as exc:
                # TimeoutError is neither HTTPError nor URLError; without it a
                # slow endpoint crashed the run before anything was logged.
                detail = f"timed out after {timeout}s" if isinstance(exc, TimeoutError) else str(exc)
                errors.append(f"fetch {since.date()}..{until.date()}: {detail}")
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

        # Reconcile orphaned pending rows against posted twins on the
        # accounts this run touched (see module docstring). A dry run
        # plans but never writes.
        recon_exclude = {str(x) for x in (config_row.get("reconcile_exclude") or [])}
        account_ids = {a.get("id") for a in accounts_seen if a.get("id")}
        stored_rows = index.get("transactions") or []
        plan = _plan_reconciliation(stored_rows, exclude=recon_exclude, accounts=account_ids)
        reconciled = 0
        if not dry_run:
            reconciled = _apply_reconciliation(stored_rows, plan["matches"])

        # Retire orphans that never found a twin: flag (never drop) pending
        # rows older than `stale_pending_days` so window-based consumers
        # skip them. Runs AFTER reconciliation so a fresh twin wins.
        stale_days = int(config_row.get("stale_pending_days") or DEFAULT_STALE_PENDING_DAYS)
        stale_rows = _stale_pending_candidates(stored_rows, days=stale_days)
        stale_ids = [r["external_id"] for r in stale_rows]
        stale_marked = 0
        if not dry_run:
            stale_marked = mark_stale_pending(stored_rows, days=stale_days)
        result.items_updated = reconciled + stale_marked

        result.destination_summary = {
            "transactions": inserted,
            "institutions": sorted({
                (a.get("org") or {}).get("name", "?") for a in accounts_seen
            }),
            "accounts": len({a.get("id") for a in accounts_seen if a.get("id")}),
            "reconciliation": _reconciliation_summary(
                plan,
                matched=len(plan["matches"]) if dry_run else reconciled,
                stale_marked=len(stale_ids) if dry_run else stale_marked,
                stale_ids=stale_ids,
            ),
        }

        if dry_run:
            result.notes = (result.notes + f"; dry-run, would insert {inserted}").strip("; ")
        elif inserted > 0 or reconciled > 0 or stale_marked > 0:
            _save_index(idx_path, index)

        # Refresh affected Domain marker blocks per
        # contracts/domain-reflection.md. Best-effort; errors do not fail
        # the ingest. Skipped on dry-run (no upstream data changed).
        if not dry_run:
            refresh_errors = self._refresh_domains()
            if refresh_errors:
                result.notes = (result.notes
                                + "; render_domain errors: "
                                + "; ".join(refresh_errors)).strip("; ")

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


def _today() -> dt.date:
    """Local calendar date; a seam so tests and migrations can pin 'now'."""
    return dt.date.today()


def _parse_iso_date(value: Any) -> dt.date | None:
    """Coerce an ISO date OR datetime (string or object) to a `date`.

    YAML may hand back `date` / `datetime` objects for unquoted scalars, and
    `transacted_at` may carry a time component; all collapse to the day.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _norm_text(value: Any) -> str:
    """Lowercase, strip punctuation to spaces, collapse whitespace."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _row_texts(row: dict[str, Any]) -> list[str]:
    return [t for t in (_norm_text(row.get("payee")), _norm_text(row.get("description"))) if t]


def _fuzzy_text_match(a: str, b: str) -> bool:
    """Normalized substring containment, or conservative token overlap."""
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    common = ta & tb
    if not common:
        return False
    return len(common) / min(len(ta), len(tb)) >= RECONCILE_TOKEN_OVERLAP


def _rows_fuzzy_match(pending: dict[str, Any], posted: dict[str, Any]) -> bool:
    """Any payee/description pairing between the two rows fuzzy-matches."""
    return any(
        _fuzzy_text_match(x, y) for x in _row_texts(pending) for y in _row_texts(posted)
    )


def _plan_reconciliation(
    rows: list[dict[str, Any]],
    exclude: set[str] | None = None,
    accounts: set[str] | None = None,
) -> dict[str, Any]:
    """Match orphaned pending rows (date 1970-01-01) to their posted twins.

    A pending transaction that later posts arrives under a NEW external_id,
    so dedupe never retires the stored pending row; with `posted=0` its date
    coerced to 1970-01-01 and it looks eternally ancient. For each such
    orphan this finds posted rows on the SAME account with a same-sign amount
    within RECONCILE_AMOUNT_TOLERANCE (absolute — card tips, never a
    percentage), a posted/transacted date within +/-RECONCILE_WINDOW_DAYS of
    the orphan's transacted_at, and a fuzzy payee/description match. Only a
    UNIQUE match is proposed; 2+ candidates — or two orphans claiming the
    same posted row — are skipped as ambiguous. Already-superseded rows are
    skipped, so the pass is idempotent. `accounts=None` means all accounts.

    Returns `{"matches": [...], "ambiguous": int, "excluded": int,
    "ambiguous_ids": [...], "excluded_ids": [...]}` — the id lists name the
    skipped orphans so the user can act via `--reconcile-exclude`. The
    caller applies matches via `_apply_reconciliation` (or just prints them
    on a dry run).
    """
    exclude = exclude or set()
    matches: list[dict[str, Any]] = []
    ambiguous_ids: list[str] = []
    excluded_ids: list[str] = []
    posted_rows = [
        r for r in rows
        if isinstance(r, dict)
        and not r.get("pending")
        and r.get("external_id")
        and r.get("external_id") not in exclude
        and r.get("account_id")
        and r.get("date") != RECONCILE_ORPHAN_DATE
    ]
    for row in rows:
        if not isinstance(row, dict) or not row.get("external_id"):
            continue
        if not row.get("pending") or row.get("date") != RECONCILE_ORPHAN_DATE:
            continue
        if row.get("superseded_by"):
            continue  # already reconciled on a prior pass
        if accounts is not None and row.get("account_id") not in accounts:
            continue
        if row["external_id"] in exclude:
            excluded_ids.append(row["external_id"])
            continue
        anchor = _parse_iso_date(row.get("transacted_at"))
        if anchor is None:
            continue  # nothing to anchor the date window on
        candidates = []
        for cand in posted_rows:
            if cand.get("account_id") != row.get("account_id"):
                continue
            if not _amounts_near(row.get("amount"), cand.get("amount")):
                continue
            cand_dates = [
                d
                for d in (
                    _parse_iso_date(cand.get("date")),
                    _parse_iso_date(cand.get("transacted_at")),
                )
                if d is not None
            ]
            if not any(abs((d - anchor).days) <= RECONCILE_WINDOW_DAYS for d in cand_dates):
                continue
            if not _rows_fuzzy_match(row, cand):
                continue
            candidates.append(cand)
        if len(candidates) == 1:
            cand = candidates[0]
            matches.append({
                "pending_id": row["external_id"],
                "posted_id": cand["external_id"],
                "date": cand.get("date"),
                "amount": row.get("amount"),
                "pending_payee": row.get("payee") or row.get("description"),
                "posted_payee": cand.get("payee") or cand.get("description"),
            })
        elif len(candidates) > 1:
            ambiguous_ids.append(row["external_id"])
    # A posted row may supersede at most one pending row; competing claims
    # are ambiguous too.
    claims: dict[str, int] = {}
    for m in matches:
        claims[m["posted_id"]] = claims.get(m["posted_id"], 0) + 1
    contested = {pid for pid, n in claims.items() if n > 1}
    if contested:
        ambiguous_ids.extend(m["pending_id"] for m in matches if m["posted_id"] in contested)
        matches = [m for m in matches if m["posted_id"] not in contested]
    return {
        "matches": matches,
        "ambiguous": len(ambiguous_ids),
        "excluded": len(excluded_ids),
        "ambiguous_ids": ambiguous_ids,
        "excluded_ids": excluded_ids,
    }


def _amounts_near(pending_amount: Any, posted_amount: Any) -> bool:
    """True when both amounts share a sign and differ by <= the tolerance.

    Absolute, not percentage: a posted card charge may exceed its pending
    authorization by a tip or a rounding cent, but a 25% drift on a busy
    account is far more often a different visit to the same merchant.
    """
    try:
        a, b = float(pending_amount), float(posted_amount)
    except (TypeError, ValueError):
        return False
    if a and b and (a < 0) != (b < 0):
        return False
    return abs(a - b) <= RECONCILE_AMOUNT_TOLERANCE + 1e-9


def _apply_reconciliation(rows: list[dict[str, Any]], matches: list[dict[str, Any]]) -> int:
    """Mark each matched pending row superseded; fix its date. No data loss.

    A row that was flagged `stale_pending` on an earlier pass and only now
    finds its twin loses the flag: it is no longer an orphan.
    """
    by_id = {r.get("external_id"): r for r in rows if isinstance(r, dict)}
    applied = 0
    for m in matches:
        row = by_id.get(m["pending_id"])
        if row is None or row.get("superseded_by"):
            continue
        row["superseded_by"] = m["posted_id"]
        if m.get("date"):
            row["date"] = m["date"]
        row.pop("stale_pending", None)
        applied += 1
    return applied


def _stale_pending_candidates(
    rows: list[dict[str, Any]], *, days: int = DEFAULT_STALE_PENDING_DAYS,
    today: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Return the orphan rows `mark_stale_pending` WOULD flag (no mutation).

    An orphan qualifies when it is `pending`, still carries the coerced
    RECONCILE_ORPHAN_DATE, has no `superseded_by`, is not already flagged,
    and its `transacted_at` is strictly older than `days` days before
    `today`. Rows without a parseable `transacted_at` are left alone.
    """
    today = today or _today()
    cutoff = today - dt.timedelta(days=days)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("external_id"):
            continue
        if not row.get("pending") or row.get("date") != RECONCILE_ORPHAN_DATE:
            continue
        if row.get("superseded_by") or row.get("stale_pending"):
            continue
        anchor = _parse_iso_date(row.get("transacted_at"))
        if anchor is None or anchor >= cutoff:
            continue
        out.append(row)
    return out


def mark_stale_pending(
    rows: list[dict[str, Any]], *, days: int = DEFAULT_STALE_PENDING_DAYS,
    today: dt.date | None = None,
) -> int:
    """Flag long-orphaned pending rows with `stale_pending: true`; return count.

    Additive and information-preserving: the row stays in the store verbatim
    apart from the new flag, so consumers that window by `date` can skip it
    explicitly (like `superseded_by`) instead of silently losing it to the
    1970 date. Idempotent — already-flagged rows are not re-counted — and it
    never touches a row that carries `superseded_by`. `days` is the
    per-source `stale_pending_days` (default DEFAULT_STALE_PENDING_DAYS);
    `today` is injectable for tests and migrations.
    """
    candidates = _stale_pending_candidates(rows, days=days, today=today)
    for row in candidates:
        row["stale_pending"] = True
    return len(candidates)


def _reconciliation_summary(
    plan: dict[str, Any], *, matched: int, stale_marked: int, stale_ids: list[str],
) -> dict[str, Any]:
    """Build the `reconciliation` block of a run's destination_summary."""
    return {
        "matched": matched,
        "ambiguous_skipped": plan["ambiguous"],
        "excluded": plan["excluded"],
        "ambiguous_ids": list(plan.get("ambiguous_ids") or []),
        "excluded_ids": list(plan.get("excluded_ids") or []),
        "stale_marked": stale_marked,
        "stale_ids": list(stale_ids),
    }


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


def _run_reconcile_only(workspace: Path, exclude: set[str], dry_run: bool) -> int:
    """One-shot repair: reconcile the existing store without fetching.

    `dry_run` prints the proposed matches (pending id -> posted id, amount,
    payee pair), the ambiguous orphans it skipped, and the orphans it would
    flag `stale_pending`, then writes nothing — no index save, no log row.
    The stale threshold honours `stale_pending_days` on the data-sources row.
    """
    started = now_iso()
    t0 = time.time()
    idx_path = workspace / SimpleFinIngestor.INDEX_PATH
    index = _load_index(idx_path)
    rows = index.get("transactions") or []
    source_row = _find_source_row(
        _load_data_sources(workspace / SimpleFinIngestor.DATA_SOURCES_PATH), "simplefin"
    ) or {}
    stale_days = int(source_row.get("stale_pending_days") or DEFAULT_STALE_PENDING_DAYS)

    plan = _plan_reconciliation(rows, exclude=exclude)
    for m in plan["matches"]:
        print(
            f"  {m['pending_id']} -> {m['posted_id']} amount={m['amount']} "
            f"payee={m['pending_payee']!r} ~ {m['posted_payee']!r}"
        )
    _print_skipped_ids(plan["ambiguous_ids"], plan["excluded_ids"])
    if dry_run:
        stale_ids = [r["external_id"] for r in _stale_pending_candidates(rows, days=stale_days)]
        for sid in stale_ids:
            print(f"  stale: {sid} (would mark stale_pending; older than {stale_days}d)")
        print(
            f"reconcile dry-run: matched={len(plan['matches'])} "
            f"ambiguous_skipped={plan['ambiguous']} excluded={plan['excluded']} "
            f"stale_marked={len(stale_ids)} (nothing written)"
        )
        return 0
    applied = _apply_reconciliation(rows, plan["matches"])
    stale_ids = [r["external_id"] for r in _stale_pending_candidates(rows, days=stale_days)]
    stale_marked = mark_stale_pending(rows, days=stale_days)
    for sid in stale_ids:
        print(f"  stale: {sid} (marked stale_pending; older than {stale_days}d)")
    if applied or stale_marked:
        _save_index(idx_path, index)

    result = RunResult(source="simplefin", started_at=started, finished_at=now_iso())
    result.items_updated = applied + stale_marked
    result.destination_summary = {
        "reconciliation": _reconciliation_summary(
            plan, matched=applied, stale_marked=stale_marked, stale_ids=stale_ids
        )
    }
    result.notes = "reconcile-only run (no fetch)"
    result.duration_ms = int((time.time() - t0) * 1000)
    log_path = workspace / SimpleFinIngestor.INGESTION_LOG_PATH
    log_data = yaml.safe_load(log_path.read_text()) if log_path.exists() else {"runs": []}
    if not isinstance(log_data, dict):
        log_data = {"runs": []}
    run_id = _next_log_id(log_data)
    log_row = result.to_log_row(run_id, trigger="manual", window=None)
    log_row.pop("id", None)
    _append_ingestion_log(workspace, run_id, log_row)

    print(
        f"reconciled={applied} ambiguous_skipped={plan['ambiguous']} "
        f"excluded={plan['excluded']} stale_marked={stale_marked} "
        f"duration_ms={result.duration_ms}"
    )
    return 0


def _print_skipped_ids(ambiguous_ids: list[str], excluded_ids: list[str]) -> None:
    """List the orphans reconciliation skipped so the user can act on them."""
    for aid in ambiguous_ids:
        print(f"  ambiguous: {aid} (2+ candidate twins; hold out with --reconcile-exclude)")
    for eid in excluded_ids:
        print(f"  excluded: {eid} (held out by --reconcile-exclude)")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest-simplefin")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--backfill", action="store_true",
                        help="Pull backfill_window_days instead of incremental delta.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pending", action="store_true",
                        help="Exclude pending transactions.")
    parser.add_argument("--timeout", type=int, default=None,
                        help=(
                            "Per-request read timeout in seconds "
                            f"(default {DEFAULT_HTTP_TIMEOUT}). Raise it when the "
                            "bridge is slow refreshing institutions."
                        ))
    parser.add_argument("--reconcile", action="store_true",
                        help=(
                            "Run ONLY the pending->posted reconciliation over the "
                            "existing store (no fetch)."
                        ))
    parser.add_argument("--reconcile-dry-run", action="store_true",
                        help="Print proposed reconciliation matches without writing.")
    parser.add_argument("--reconcile-exclude", action="append", default=[],
                        metavar="EXTERNAL_ID",
                        help=(
                            "Hold a specific row out of reconciliation "
                            "(repeatable)."
                        ))
    args = parser.parse_args()

    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"

    if args.reconcile or args.reconcile_dry_run:
        return _run_reconcile_only(
            workspace, set(args.reconcile_exclude), dry_run=args.reconcile_dry_run
        )

    data_sources = _load_data_sources(workspace / SimpleFinIngestor.DATA_SOURCES_PATH)
    row = _find_source_row(data_sources, "simplefin") or {}
    config_row: dict[str, Any] = {
        "last_ingest": row.get("last_ingest"),
        "recency_window_days": row.get("recency_window_days", DEFAULT_RECENCY_DAYS),
        "backfill_window_days": row.get("backfill_window_days", DEFAULT_BACKFILL_DAYS),
        "max_items_per_run": row.get("max_items_per_run", DEFAULT_MAX_ITEMS),
        "backfill": args.backfill,
        "include_pending": not args.no_pending,
        "timeout": args.timeout,
        "reconcile_exclude": args.reconcile_exclude,
        "stale_pending_days": row.get("stale_pending_days"),
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
        f"updated={result.items_updated} skipped={result.items_skipped} "
        f"errors={len(result.errors)} "
        f"truncated={result.truncated} duration_ms={result.duration_ms}"
    )
    if result.notes:
        print(f"  notes: {result.notes}")
    if result.destination_summary:
        insts = result.destination_summary.get("institutions") or []
        print(f"  institutions: {', '.join(insts)}")
        print(f"  accounts: {result.destination_summary.get('accounts')}")
        recon = result.destination_summary.get("reconciliation") or {}
        if recon:
            print(
                f"  reconciliation: matched={recon.get('matched', 0)} "
                f"ambiguous_skipped={recon.get('ambiguous_skipped', 0)} "
                f"excluded={recon.get('excluded', 0)} "
                f"stale_marked={recon.get('stale_marked', 0)}"
            )
            _print_skipped_ids(recon.get("ambiguous_ids") or [], recon.get("excluded_ids") or [])
            for sid in recon.get("stale_ids") or []:
                print(f"  stale: {sid} (marked stale_pending)")
    for err in result.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
