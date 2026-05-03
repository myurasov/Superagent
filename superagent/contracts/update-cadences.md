# Update Cadences

<!-- Migrated from `procedures.md § 4`. Citation form: `contracts/update-cadences.md`. -->

Cadences are aspirational defaults; the user can run any of them on demand at any time, and disable any of them in `config.yaml.preferences.cadences`.

### 4.1 Daily update

**Trigger:** user runs `daily-update` or natural-language equivalent ("morning briefing", "what's today look like").

**Steps (high-level — full version in `skills/daily-update.md`):**

1. Load config + context.
2. Run scheduled ingestors (those with `capture_mode: scheduled` and cadence including `daily`) with light budgets.
3. Compose:
   - **Today's calendar** (from local mirror, fall through to live calendar source for newest items).
   - **Bills due in the next 7 days** (from `bills.yaml`).
   - **Today's appointments** (from `appointments.yaml`).
   - **Important dates today / tomorrow** (from `important-dates.yaml`).
   - **P0 / P1 tasks due today or overdue** (from `todo.yaml`).
   - **Health / fitness signal of note** (last night's sleep, today's planned workout, prescription due to refill).
   - **Inbox highlights** (top 5–10 from email ingest if enabled).
   - **Anything new + interesting from other ingestors** (large transaction, low balance, new package shipped, document expiring this month).
4. Update `context.yaml.last_check` to `now`; append a one-liner to `interaction-log.yaml`.

### 4.2 Weekly review

**Trigger:** user runs `weekly-review` (suggested cadence: Sunday evening or Friday end-of-day per `config.yaml`).

**Steps:**

1. Bookkeeper pass: spend by category vs. budget for the trailing 7 days; new recurring charges spotted; bills paid / unpaid.
2. Coach pass: workouts logged, sleep average, weight trend, any personal-signal events captured.
3. Concierge pass: appointments coming up in the next 14 days, important dates in the next 30 days, document expirations in the next 60 days.
4. Quartermaster pass: any home / vehicle maintenance items due in the next 30 days, warranties expiring in the next 60 days.
5. Tasks: what got done, what slipped, what's due.
6. Append a `weekly-review` entry to `interaction-log.yaml` and to `Domains/Self/history.md`.

### 4.3 Monthly review

**Trigger:** user runs `monthly-review` (suggested cadence: 1st of the month).

**Steps:**

1. **Subscription audit** — every row in `subscriptions.yaml` with `last_used` older than 60 days surfaces with a "cancel?" prompt; new subscriptions detected from ingested transactions vs. last month surface for confirmation.
2. **Document expiration scan** — surface every document expiring in the next 90 days.
3. **Vehicle / home maintenance windows** — every maintenance row whose `next_due` falls in the next 60 days.
4. **Financial recap** — month-over-month spend, savings rate, biggest categories, anomalies.
5. **Health recap** — overdue cleanings / vaccines / screenings; med refill dates in the next 30 days.
6. **Domain hygiene** — any domain with no `history.md` entry in the last 60 days surfaces for "is this still active?".
7. Append entries to `interaction-log.yaml` and to `Domains/Self/history.md`.

### 4.4 Quarterly Supertailor review

**Trigger:** user runs `supertailor-review` (suggested cadence: every 90 days; daily-update nudges when stale).

The Supertailor's hygiene + strategic-improvement passes per `supertailor.agent.md`. Outputs ranked suggestions in `supertailor-suggestions.yaml`; each tagged `destination: superagent` (Supercoder writes into `superagent/`) or `destination: _custom` (Supercoder writes into `workspace/_custom/`). The Supertailor never writes implementation code itself; both destinations route through the Supercoder. The hard safeguard against personal data leaking into framework-bound writes is enforced at proposal time by the Supertailor AND at implementation time by the Supercoder (defense in depth).
