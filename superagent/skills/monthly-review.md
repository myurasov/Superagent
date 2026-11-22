---
name: superagent-monthly-review
description: >-
  Monthly heavy pass: bring the stores up to date (the `update` skill), then
  the monthly briefing (the `brief` skill): subscription audit (cancel
  candidates), document expirations in next 90 days, vehicle / home
  maintenance windows in next 60 days, financial month recap, health overdue
  items, domain hygiene (which domains haven't been touched in 60+ days).
triggers:
  - monthly review
  - first of the month
  - month wrap
  - month-end review
mcp_required: []
mcp_optional:
  - whatever the `update` skill reaches: any watcher that is due
cli_required: []
cli_optional: []
---

# Superagent monthly-review skill

An **entry point**: it has no steps of its own beyond the two below (`contracts/update-loop.md` § 5). Fetching belongs to the `update` skill and telling to the `brief` skill; this file only says which tier and which kind. Read each of the two skill files before running it.

## 1. Update

Run the **`update`** skill (`skills/update.md`) with tier **`auto`** (the full tier unless it already ran today; each watcher is checked by its own schedule, so no cadence has to run first). It brings the stores up to date and prints a short run report: keep the report for the reply, above the briefing. When it says the cycle was skipped because another one is running on this machine, go straight to step 2: the stores are being updated by that run.

## 2. Brief

Run the **`brief`** skill (`skills/brief.md`) with kind **`monthly`**. It reads what step 1 left in the stores, renders the briefing, stamps the duty and logs the run. With the owner present it ends with the subscription and hygiene follow-ups.
