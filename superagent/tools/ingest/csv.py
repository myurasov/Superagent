"""Generic bank-statement CSV ingestor.

Parses CSV exports from common banks into a normalized `_memory/transactions.yaml`
index. Intentionally low-tech: works without any API or MCP — the user
downloads a CSV from their bank's web UI and runs:

  uv run python -m superagent.tools.ingest.csv --file ~/Downloads/chase-export.csv

Recognizes column headers from:
  - Chase
  - Bank of America
  - Wells Fargo
  - American Express
  - Schwab (brokerage)
  - Fidelity
  - Generic (date, description, amount columns named loosely)

Idempotency: each transaction's stable hash (sha256 of date|payee|amount|account)
is stored as `external_id`; re-runs over the same CSV are no-ops.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses as dc
import datetime as dt
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso


# Column-name aliases per known bank format. Lowercase comparison.
DATE_COLUMNS = {"date", "transaction date", "posting date", "post date", "trade date"}
AMOUNT_COLUMNS = {"amount", "amount (usd)", "transaction amount", "debit", "credit"}
PAYEE_COLUMNS = {"description", "payee", "merchant", "name", "details"}
CATEGORY_COLUMNS = {"category", "type", "transaction type"}


@dc.dataclass
class NormalizedTxn:
    """One normalized transaction row."""
    external_id: str
    date: str
    payee: str
    amount: float
    currency: str
    category: str
    raw: dict[str, str]


class CsvIngestor(IngestorBase):
    """Generic bank-statement CSV ingestor."""

    source = "csv"
    kind = "file"
    description = "Generic bank-statement CSV import."

    def probe(self) -> ProbeResult:
        """The CSV ingestor is always 'available' — no installs needed."""
        return ProbeResult(
            source=self.source,
            status=ProbeStatus.AVAILABLE,
            detail="No setup required; pass --file at invocation.",
        )

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Run requires a file path. The orchestrator passes it via config_row['file']."""
        started = now_iso()
        t0 = time.time()
        result = RunResult(source=self.source, started_at=started, finished_at=started)
        file_path = config_row.get("file")
        if not file_path:
            result.errors.append("CSV ingestor requires `file` in config_row (a path).")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            result.errors.append(f"file not found: {path}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        account_label = config_row.get("account_label", path.stem)
        currency = config_row.get("currency", "USD")
        try:
            txns = self._parse_csv(path, account_label, currency)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"CSV parse failed: {exc}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        result.items_pulled = len(txns)
        max_items = int(config_row.get("max_items_per_run", 5000))
        if len(txns) > max_items:
            result.truncated = True
            txns = txns[:max_items]

        idx_path = self.workspace / "_memory" / "transactions.yaml"
        index = self._load_index(idx_path)
        existing_ids = {t.get("external_id") for t in (index.get("transactions") or []) if isinstance(t, dict)}
        inserted = 0
        skipped = 0
        for txn in txns:
            if txn.external_id in existing_ids:
                skipped += 1
                continue
            index.setdefault("transactions", []).append({
                "external_id": txn.external_id,
                "date": txn.date,
                "payee": txn.payee,
                "amount": txn.amount,
                "currency": txn.currency,
                "category": txn.category,
                "account_label": account_label,
                "source": "csv",
                "raw": txn.raw,
            })
            inserted += 1
        result.items_inserted = inserted
        result.items_skipped = skipped
        result.destination_summary = {"transactions": inserted}

        if not dry_run and inserted > 0:
            self._save_index(idx_path, index)
        elif dry_run:
            result.notes = f"dry-run; would insert {inserted} transactions"
        result.finished_at = now_iso()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def _load_index(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": 1, "transactions": []}
        try:
            with path.open() as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            return {"schema_version": 1, "transactions": []}
        if not isinstance(data, dict):
            return {"schema_version": 1, "transactions": []}
        data.setdefault("transactions", [])
        return data

    def _save_index(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    def _parse_csv(self, path: Path, account_label: str, currency: str) -> list[NormalizedTxn]:
        """Parse a CSV file. Returns list of NormalizedTxn."""
        txns: list[NormalizedTxn] = []
        with path.open(newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(fh, dialect=dialect)
            field_map = self._map_fields(reader.fieldnames or [])
            if not field_map.get("date") or not field_map.get("amount"):
                raise ValueError(
                    f"could not find date / amount columns in headers: {reader.fieldnames}"
                )
            for raw_row in reader:
                if not raw_row:
                    continue
                date_str = self._normalize_date(raw_row.get(field_map["date"], ""))
                if not date_str:
                    continue
                payee = (raw_row.get(field_map.get("payee") or "", "") or "").strip()
                amount_raw = raw_row.get(field_map["amount"], "0").strip()
                amount_clean = amount_raw.replace(",", "").replace("$", "").replace("(", "-").replace(")", "")
                try:
                    amount = float(amount_clean)
                except ValueError:
                    continue
                category = (raw_row.get(field_map.get("category") or "", "") or "").strip() or "uncategorized"
                hashable = f"{account_label}|{date_str}|{payee}|{amount:.2f}".encode()
                ext_id = "csv:" + hashlib.sha256(hashable).hexdigest()[:16]
                txns.append(NormalizedTxn(
                    external_id=ext_id,
                    date=date_str,
                    payee=payee or "(unnamed)",
                    amount=amount,
                    currency=currency,
                    category=category,
                    raw=dict(raw_row),
                ))
        return txns

    def _map_fields(self, headers: list[str]) -> dict[str, str]:
        """Map our canonical field names to actual CSV headers."""
        out: dict[str, str] = {}
        lc_headers = {h: h.lower().strip() for h in headers}
        for header, lc in lc_headers.items():
            if "date" not in out and lc in DATE_COLUMNS:
                out["date"] = header
            elif "amount" not in out and lc in AMOUNT_COLUMNS:
                out["amount"] = header
            elif "payee" not in out and lc in PAYEE_COLUMNS:
                out["payee"] = header
            elif "category" not in out and lc in CATEGORY_COLUMNS:
                out["category"] = header
        return out

    def _normalize_date(self, value: str) -> str:
        """Normalize various date formats to YYYY-MM-DD."""
        value = (value or "").strip()
        if not value:
            return ""
        for fmt in (
            "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d",
            "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y",
        ):
            try:
                return dt.datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="ingest-csv")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--file", type=Path, required=True, help="CSV file to import.")
    parser.add_argument("--account-label", type=str, default=None,
                        help="Label for this account (defaults to filename).")
    parser.add_argument("--currency", type=str, default="USD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"

    ingestor = CsvIngestor(workspace)
    config_row: dict[str, Any] = {
        "file": str(args.file),
        "account_label": args.account_label or args.file.stem,
        "currency": args.currency,
        "max_items_per_run": 5000,
    }
    result = ingestor.run(config_row, dry_run=args.dry_run)
    print(
        f"pulled={result.items_pulled} inserted={result.items_inserted} "
        f"skipped={result.items_skipped} errors={len(result.errors)} "
        f"duration_ms={result.duration_ms}"
    )
    if result.errors:
        for err in result.errors:
            print(f"  error: {err}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
