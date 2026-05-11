---
name: superagent-expenses
description: >-
  Categorize and review spending. Reads ingested transactions; renders by
  category, by merchant, by domain. Spots anomalies vs trailing average.
  Cross-references against `bills.yaml` + `subscriptions.yaml` to detect
  unmapped recurring charges.
triggers:
  - expenses
  - spending this month
  - spending by category
  - where did my money go
  - expense audit
mcp_required: []
mcp_optional:
  - finance ingestors (plaid / monarch / ynab / csv) — required for any data
cli_required: []
cli_optional: []
---

# Superagent expenses skill

## 0. Preflight

If no finance ingestor has been enabled (no rows in `data-sources.yaml` with kind: finance and `enabled: true`), surface:

> "No finance data ingested yet. Set up `plaid`, `monarch`, `ynab`, or the `csv` ingestor first — see `superagent/docs/data-sources.md#finance`."

Stop here.

Read order (per `contracts/local-first-read-order.md`):
1. `_memory/accounts-index.yaml.<acct>.transactions[]` — per-account chronological history (mandated by `contracts/payment-confirmations.md § 4 step 3a`); already covers user-reported + agent-initiated payments.
2. `_memory/transactions.yaml` — cross-account ingestor-normalized output (when populated by Plaid / Monarch / YNAB / CSV ingestors).
3. Live ingestor pull only when both above are stale.

The two sources are intentionally redundant: per-account transactions support `expenses --account <id>` cleanly; the cross-account file supports merchant + category aggregation. Ingestors keep them in sync via the dedup key in `contracts/payment-confirmations.md § 4.3`.

## 1. Branch on intent

### `expenses` (default) — current month

1. Default window: month-to-date.
2. Read transactions for the window from the ingestor's normalized output.
3. Total inflows / outflows.
4. Group outflows by `category` (the categorization the ingestor applied — typically Plaid / Monarch's auto-category, but can be overridden via `_memory/expense-categories.yaml` user rules).
5. Render a markdown table sorted by amount descending:

   ```
   | Category | Amount | % of total | Δ vs avg |
   |---|---|---|---|
   ```
6. Below the table: top 5 merchants in the window.
7. Below that: anomalies (single transactions > 1.5× P95 of last 90 days for that category).

### `expenses --month <YYYY-MM>` / `--last-N-months N`

Same shape, different window.

### `expenses --category <name>`

Filter to one category. Lists merchants, transaction count, total, and trailing-12-month trend.

### `expenses --domain <slug>`

Cross-reference: which bills + subscriptions belong to this domain (from `bills.yaml.domain` and the linked-subs of the `accounts-index.yaml` rows whose domain matches), and what they cost in the window.

### `expenses --year <YYYY>`

Annual rollup with month-over-month deltas. Useful for tax prep — also surfaces tax-deductible categories (charitable, medical, business, education) tagged by `_memory/expense-categories.yaml`.

### `expenses --unmapped`

Recurring charges (≥ 2 occurrences, consistent merchant + amount tolerance ±5%, periodic interval) that aren't in `bills.yaml` or `subscriptions.yaml`. Prompts to add or ignore.

## 2. Capture user-defined categorization rules

If the user corrects a category ("That's not 'Dining' — that's 'Groceries'"), capture into `_memory/expense-categories.yaml.rules[]`:

```yaml
- match: "<merchant substring>"
  amount_min: null
  amount_max: null
  category: "<category>"
  domain: "<domain>"   # optional
```

The next ingest run will apply the rule to incoming transactions.

## 3. Sync downstream

After any analysis:
- Append a one-line entry to `interaction-log.yaml` with the window and any anomalies surfaced.
- For meaningful anomalies (e.g. > 2× P95), surface in next `daily-update`.
