---
name: superagent-whatsup
description: >-
  Quick delta since last_check: a light update of the stores (the `update`
  skill), then the short daily briefing (the `brief` skill): bills due,
  today's appointments, important dates, P0/P1 tasks overdue, anything else
  needing attention. Designed to be the user's first command in the morning
  or whenever they tab back to the workspace.
triggers:
  - what's up
  - status check
  - catch me up
  - quick status
  - what should I know
  - what's next / whats next
  - what are the next steps
  - what are my top tasks / top tasks to complete
  - steps for today
mcp_required: []
mcp_optional:
  - whatever the `update` skill reaches in its light tier: any watcher that is due
cli_required: []
cli_optional: []
---

# Superagent whatsup skill

An **entry point**: it has no steps of its own beyond the two below (`contracts/update-loop.md` § 5). Fetching belongs to the `update` skill and telling to the `brief` skill; this file only says which tier and which kind. Read each of the two skill files before running it.

## 1. Update

Run the **`update`** skill (`skills/update.md`) with tier **`light`** (a watcher that is not due is not fetched, so a second run on the same day is quick). It brings the stores up to date and prints a short run report: keep the report for the reply, above the briefing. When it says the cycle was skipped because another one is running on this machine, go straight to step 2: the stores are being updated by that run.

## 2. Brief

Run the **`brief`** skill (`skills/brief.md`) with kind **`daily`**, **short form** (the delta since the last check, 30 lines at most). It reads what step 1 left in the stores, renders the briefing, stamps the duty and logs the run.
