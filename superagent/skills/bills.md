---
name: superagent-bills
description: >-
  List, mark-paid, and reconcile bills. Cross-checks ingested transactions
  against `bills.yaml.history[]` to flag missing payments and duplicate charges.
triggers:
  - what bills are due
  - mark <bill> paid
  - bill list
  - reconcile bills
  - any bills due this week
mcp_required: []
mcp_optional:
  - any finance ingestor (plaid / monarch / ynab) — for reconciliation
cli_required: []
cli_optional: []
---

# Superagent bills skill

## 1. Branch on intent

### List

User asked "what's due", "show bills", "next bill":

1. Read `_memory/bills.yaml`.
2. Recompute `next_due` for each row (handle late-paid skipped cycles).
3. Filter `status: active`.
4. Group:
   - **Overdue** (`next_due < today` AND no `history` entry for the current cycle).
   - **Due today**.
   - **Due in 1–7 days**.
   - **Due in 8–30 days**.
5. Within each group, sort by `next_due` ascending.
6. Format: `{name} {amount} {currency} — {next_due} ({auto-pay|manual})`.

If `--upcoming-only` requested, skip overdue. If `--domain <slug>`, filter accordingly.

### Mark-paid

User asked "mark <bill> paid", "I just paid <bill>":

1. Resolve bill by id or fuzzy match on name. Confirm if multiple match.
2. Ask for / infer payment date (default: today), amount paid (default: `amount`), confirmation number, source ("manual" or ingestor name).
3. Append to `bills.yaml.<bill>.history[]`:

   ```yaml
   - date: <payment-date>
     amount: <amount paid>
     confirmation: "<conf>"
     source: "<source>"
     notes: ""
   ```
4. Recompute `next_due` from cadence + due_day relative to the payment date.
5. Update `bills.yaml.<bill>.last_updated`.

### Reconcile (`bills reconcile`)

User asked to reconcile recently-ingested transactions against open bills:

1. If a finance ingestor is enabled, read the recent transactions (Plaid / Monarch / CSV ingestor's normalized output — typically `_memory/transactions.yaml` or per-source `interaction-log.yaml` entries).
2. For each transaction in the trailing 30 days:
   - Match by `payee` substring + amount tolerance (±5%).
   - On match: append to the bill's `history[]` if not already present (dedup on `confirmation` or `(date, amount)` tuple).
3. Surface:
   - **Auto-matched**: count + bill names.
   - **Bills due in last 30 days with NO matching transaction**: list — these may have been missed.
   - **Transactions matching no bill**: list — candidates to add via `add-bill` or to flag as one-shot.

### Add (delegate)

User asked to add a bill:

→ Hand off to `add-bill` skill.

### Update (delegate)

User asked to change a bill's amount / cadence / due day:

→ Use the `bills update <id> <field>=<value>` syntax. Persist the change; recompute `next_due` if cadence-relevant fields changed.

## 2. Sync downstream

After any add / mark-paid / update:
- Update relevant `Domains/<Domain>/status.md` § Next Steps.
- Append to `interaction-log.yaml`.
- If a P0 task was auto-created by daily-update for an overdue bill that's now paid, mark that task `done`.

## 3. Surfacing budget

When listing, cap output at 30 lines unless `--all` is passed. If more bills exist than fit, end with: "(N more — run `bills --all` to see everything)".
