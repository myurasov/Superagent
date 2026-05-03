# Surfacing Contracts

<!-- Migrated from `procedures.md § 8`. Citation form: `contracts/surfacing.md`. -->

How Superagent decides **when** to bring something to your attention. Surfacing is the inverse of capture: capture writes silently, surfacing reads loudly.

### 8.1 Bills surfacing

| Window | Where surfaced |
|---|---|
| Due today | `daily-update` "Today" section + P0 task auto-created |
| Due in 1–7 days | `daily-update` "This week" section |
| Due in 8–30 days | `weekly-review` "Coming up" section |
| Overdue | `daily-update` "Overdue" section + P0 task + alert |
| Auto-paid (per `bills.yaml.<row>.auto_pay: true`) | `daily-update` shows once on the due date as confirmation, then drops |
| New recurring charge spotted in transactions but not in `bills.yaml` | `weekly-review` "New?" section, asks user to add or ignore |

### 8.2 Subscription audit surfacing

Per `monthly-review`. Each subscription row gets an audit verdict:

- `keep` — used recently, cost reasonable.
- `consider-cancelling` — `last_used` > 60 days OR cost > category P75.
- `unsure` — no usage signal available; ask user.

### 8.3 Document expiration surfacing

Each document with an `expires_at` field:

- `> 90 days` → invisible.
- `60–90 days` → `monthly-review` "Coming up" section.
- `30–60 days` → `weekly-review` "Action" section + auto P2 task.
- `< 30 days` → `daily-update` "Today's alerts" + auto P1 task (or P0 if < 7 days).
- Expired → `daily-update` `**EXPIRED**` block until the user resolves.

### 8.4 Maintenance window surfacing

Same window logic as documents, applied to:

- Vehicle service intervals (oil, tires, brakes, registration, inspection).
- Home maintenance items (HVAC service, filter changes, gutter cleaning, pest control, smoke-detector batteries).
- Pet maintenance items (vaccines, parasite prevention, dental).

### 8.5 Important date surfacing

| Window | Surface |
|---|---|
| Today | `daily-update` "Today" |
| Tomorrow | `daily-update` "Tomorrow" |
| In 7 days | `daily-update` "Coming up" + offers to draft a card / pick a gift |
| In 30 days | `weekly-review` "Coming up" |
| Recurrence reset (the day after) | append to `important-dates.yaml.<row>.history[]`, recompute next occurrence |
