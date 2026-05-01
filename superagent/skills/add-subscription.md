---
name: superagent-add-subscription
description: >-
  Register a recurring subscription (streaming, software, gym, donation,
  app sub) in `subscriptions.yaml`. Used heavily by monthly-review's
  audit pass.
triggers:
  - add a subscription
  - new recurring subscription
  - I started <service>
  - I'm now paying for <service>
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent add-subscription skill

## 1. Collect the subscription's data

- **Name** (e.g. "Netflix", "Adobe CC", "1Password Family").
- **Provider** — same as Name in most cases.
- **Category**: entertainment | software | gym | media | news | gaming | cloud_storage | hosting | charity | other.
- **Amount** (numeric).
- **Currency** (default from config).
- **Cadence**: monthly | annual | quarterly | weekly | one-shot.
- **Next renewal** (ISO 8601 date).
- **Pay-from account** (id from `accounts-index.yaml`).
- **URL** (where to manage / cancel — important).
- **Started at** (ISO 8601 date).
- **Trial ends** (ISO 8601 date or null) — if the user is on a free trial. Daily-update will alert 7 days + 1 day before this date.
- **Shared with** (list of names — family-plan members).
- **Replaceable by** (list — free / cheaper alternatives the user has acknowledged).
- **Notes** — context (e.g. "essential for work", "discounted family plan").

Auto-derive **id** = `sub-<slug>` (e.g. `sub-netflix`, `sub-adobe-cc`).

## 2. Append the subscription row

```yaml
- id: "<id>"
  name: "<Name>"
  provider: "<Provider>"
  category: "<category>"
  amount: <amount>
  currency: "<currency>"
  cadence: "<cadence>"
  next_renewal: <date>
  pay_from_account: <account-id or null>
  url: "<url>"
  started_at: <date>
  trial_ends: <date or null>
  shared_with: <list>
  last_used: null
  audit_flag: false
  replaceable_by: <list>
  tags: []
  notes: "<notes>"
  status: "active"
  cancelled_at: null
  cancellation_reason: ""
  created: <now>
  last_updated: <now>
```

## 3. Cross-link the account

If `pay_from_account` is set, append `<id>` to `accounts-index.yaml.<acct>.linked_subs[]`.

## 4. Trial-ending guard

If `trial_ends` is set:
- Add a P1 task to `todo.yaml` due 1 day before `trial_ends`: "Decide whether to keep <Name> trial — cancel before <date> if not keeping."
- daily-update will additionally surface "Trial of <Name> ends in N days" 7 days out.

## 5. Confirm

```
Added subscription "<Name>" (id: <id>) — <amount> <currency> {cadence}.
Next renewal: <date>.
Trial ends: <date or "n/a">.
```

## 6. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "add-subscription"
  summary: "Added subscription <Name> (<amount> {cadence})."
  related_domain: "finance"
  related_account: <account-id>
```
