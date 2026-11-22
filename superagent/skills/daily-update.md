---
name: superagent-daily-update
description: >-
  Daily update: bring the stores up to date (the `update` skill), then the
  daily briefing (the `brief` skill): today's calendar, bills due,
  appointments, important dates, P0/P1 tasks, health signals, inbox
  highlights, and anything the watchers flagged. The 5-minute morning ritual
  that puts the day's surface area in front of you.
triggers:
  - daily update
  - morning briefing
  - what's today look like
  - daily standup
  - daily check
  - update now
  - do update
  - run an update
mcp_required: []
mcp_optional:
  - whatever the `update` skill reaches: any watcher registered in Sources/Watchlist/ that is due
cli_required: []
cli_optional: []
---

# Superagent daily-update skill

An **entry point**: it has no steps of its own beyond the two below (`contracts/update-loop.md` § 5). Fetching belongs to the `update` skill and telling to the `brief` skill; this file only says which tier and which kind. Read each of the two skill files before running it.

## 1. Update

Run the **`update`** skill (`skills/update.md`) with tier **`auto`** (the full tier once a day, the light tier otherwise). It brings the stores up to date and prints a short run report: keep the report for the reply, above the briefing. When it says the cycle was skipped because another one is running on this machine, go straight to step 2: the stores are being updated by that run.

## 2. Brief

Run the **`brief`** skill (`skills/brief.md`) with kind **`daily`**, full form. It reads what step 1 left in the stores, renders the briefing, stamps the duty and logs the run.
