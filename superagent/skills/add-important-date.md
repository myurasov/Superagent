---
name: superagent-add-important-date
description: >-
  Register a recurring date (birthday, anniversary, observance, deadline,
  document expiration) in `important-dates.yaml`. Recurrence-aware; surfaces
  per the lookahead window in config.
triggers:
  - add a birthday
  - add an anniversary
  - add an important date
  - register <date>
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent add-important-date skill

## 1. Collect the date's data

- **Title** (e.g. "Mom's birthday", "Wedding anniversary", "Tax deadline").
- **Kind**: birthday | anniversary | deadline | observance | renewal | expiration | seasonal | other.
- **For member** — "self" | name | "household" | "world".
- **Contact** — id from `contacts.yaml` if applicable (offer `add-contact` if missing).
- **Date** — ISO 8601 date. For recurring, the original / base date (e.g. mom's actual birthdate).
- **Recurrence**: none | annual | monthly | weekly | every_n_years | leap | hebrew_calendar | lunar_calendar.
  - For MVP, only `annual`, `monthly`, `weekly`, `every_n_years`, `none` are surfacing-supported. Others mark the row `recurrence_unsupported: true` and skip surfacing.
- **Recurrence n** (for `every_n_years`).
- **Lookahead days** — how many days in advance to surface (default from config; per-date override for "ship a gift" lead time).
- **Gift ideas** (for birthdays / anniversaries) — list of free-text ideas.
- **Greetings** preference — card | call | text | in-person | gift | "just remember".
- **Notes** — anything else.

Auto-derive **id** = `date-<slug>` (e.g. `date-mom-birthday`, `date-wedding-anniversary`).

## 2. Compute next_occurrence

For `annual`: this year's <month-day>; if past, next year.
For `monthly`: this month's <day>; if past, next month.
For `weekly`: next occurrence of <day-of-week>.
For `every_n_years`: base year + N × ceil((today_year - base_year) / N).
For `none`: same as `date`.

## 3. Append the date row

```yaml
- id: "<id>"
  title: "<Title>"
  kind: "<kind>"
  for_member: "<member>"
  contact: <contact-id or null>
  date: <base-date>
  recurrence: "<recurrence>"
  recurrence_n: <n or null>
  next_occurrence: <computed>
  lookahead_days: <override or null>
  gift_ideas: <list>
  gift_log: []
  greetings: "<preference>"
  history: []
  tags: []
  notes: "<notes>"
  status: "active"
  created: <now>
  last_updated: <now>
```

## 4. Surface immediately

If `next_occurrence - today <= lookahead_days`:
- Echo back: "Next: <date> — that's in <N> days. I'll surface this in `daily-update` and `whatsup`."

## 5. Confirm

```
Added date "<Title>" — next on <next_occurrence>.
Recurrence: <recurrence>.
Lookahead: <lookahead_days> days.
```

## 6. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "add-important-date"
  summary: "Added <kind> date <Title> (<recurrence>); next on <date>."
```
