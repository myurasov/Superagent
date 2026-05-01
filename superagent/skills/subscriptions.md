---
name: superagent-subscriptions
description: >-
  List, audit, log-use, cancel, and analyze subscriptions. The audit pass
  flags candidates not used in `config.surfacing.subscription_unused_days`,
  surfaces total annualized cost, and offers cancellation walkthroughs.
triggers:
  - subscriptions
  - subscription audit
  - what am I subscribed to
  - cancel <subscription>
  - subscription cost
mcp_required: []
mcp_optional:
  - any finance ingestor (for spotting new recurring charges)
  - usage ingestors (Spotify / Strava / Apple Health → updates `last_used`)
cli_required: []
cli_optional: []
---

# Superagent subscriptions skill

## 1. Branch on intent

### List

1. Read `_memory/subscriptions.yaml`.
2. Filter `status: active`.
3. Sort by descending annualized cost.
4. Format table:
   ```
   | Subscription | Cost / mo | Cost / yr | Cadence | Last used | Renews |
   ```
5. Below: total monthly + total annualized cost.

### Audit

1. Re-evaluate each row:
   - `last_used_age` = today minus `last_used` (or "unknown" if null).
   - `audit_flag` = `last_used_age > config.preferences.surfacing.subscription_unused_days`.
2. Bucket:
   - **Keep** — `last_used_age <= 30 days`.
   - **Consider cancelling** — `last_used_age > 60 days`.
   - **Unsure** — `last_used` is null AND `started_at` > 90 days ago.
3. Compute potential annualized savings if all "consider cancelling" were cancelled.
4. Surface table (per `monthly-review` § 2):
   ```
   | Subscription | Cost / yr | Last used | Verdict | Action |
   ```
5. For each "consider cancelling" row, ask **keep / cancel / unsure / replaceable**.
   - On `cancel`: set `status: cancelled`, `cancelled_at: now`, prompt for `cancellation_reason`. Open `url` in chat as a clickable hint: "Cancel here: <url>".
   - On `replaceable`: surface `replaceable_by[]` list and ask which to switch to.
   - On `unsure`: leave row alone, mark `audit_flag: true`.

### Log-use

User said "I just used <sub>" OR an ingestor (Spotify / Strava / etc.) reports usage:

1. Resolve sub by id or name.
2. Set `last_used: now`.
3. (Silent for ingestor calls; one-line confirmation for manual.)

### Cancel

User asked to cancel:

1. Resolve sub.
2. Same as Audit's "cancel" branch.

### New-recurring detection (`subscriptions detect-new`)

If a finance ingestor is enabled:

1. Read trailing 90 days of transactions.
2. Cluster by merchant + amount tolerance ±5%.
3. Identify clusters with ≥ 2 occurrences in different months at consistent cadence.
4. For each, check `subscriptions.yaml` and `bills.yaml` — if not present, surface as audit candidate:

   > "I see a recurring charge **<merchant>** for **<amount>** roughly **{cadence}**. Want to add to subscriptions / bills, or ignore?"

## 2. Sync downstream

After any cancel:
- Append to `interaction-log.yaml` with the savings amount.
- Update `Domains/Finance/status.md` § Recent Progress with one bullet.
- Compute new monthly + annualized total; surface in the response.

## 3. Surfacing

Cap output at 30 rows unless `--all`. End with annualized total + cancellation savings achieved year-to-date.
