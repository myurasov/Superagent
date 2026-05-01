---
name: superagent-important-dates
description: >-
  List upcoming important dates, draft a greeting, suggest a gift,
  mark a date acknowledged, retire a date.
triggers:
  - important dates
  - upcoming birthdays / anniversaries
  - draft a birthday card for <name>
  - gift idea for <name>
  - dates this week / month
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent important-dates skill

## 1. Branch on intent

### List

1. Read `_memory/important-dates.yaml`.
2. Recompute `next_occurrence` for each row.
3. Default window: next 30 days. Honor `--days N` override.
4. Sort by `next_occurrence` ascending.
5. Format: `{date} ({days} days) — {title} ({recurrence_year_count} years)`.

### Draft greeting

User asked: "draft a card / message / email for <date>".

1. Resolve date by id or fuzzy title match.
2. Look up the contact (if `contact` is set in the row): get name + relationship + last_contacted.
3. Look up history with this contact (search `interaction-log.yaml` for last 6 months mentioning this person).
4. Look up `gift_log[]` for the last 3 years if relevant.
5. Compose a draft greeting in the user's voice (per `model-context.yaml.communication.style`):
   - Opening (warm, calibrated to relationship).
   - One specific reference (a recent shared moment from the interaction log, OR a callback to the previous year's gift).
   - A simple, sincere closer.
6. Render to `workspace/Outbox/greetings/<date-id>-<year>.md`.
7. Append to row's `history[]` with `acknowledged: false` (flips to true when user marks it sent).

### Suggest gift

User asked: "what should I get <name> this year?".

1. Resolve date by id or fuzzy title match (or via `contact` if user named the contact).
2. Read the row's `gift_ideas[]` (capture: anytime the user said "X would be a good gift for <name>" should have been auto-captured by chat hooks).
3. Read `gift_log[]` for the last 5 years to avoid repeats.
4. Read `Domains/Family/info.md` and the contact's `notes` for hints.
5. Output a ranked list with one-line rationale for each (top 3, plus "wildcard" alternative).
6. Ask: "Want me to add one of these to your gift_ideas list, or log a gift if you've decided?"

### Log gift / acknowledgment

When the user confirms they sent / gave / acknowledged:

1. Append to `important-dates.yaml.<date>.gift_log[]`:
   ```yaml
   - year: <next_occurrence year>
     gift: "<text>"
     reaction: ""
   ```
2. Append to `history[]`:
   ```yaml
   - year: <next_occurrence year>
     acknowledged: true
     notes: "<text>"
   ```
3. Recompute `next_occurrence` (advance by recurrence).

### Retire

User asked to retire a date (e.g. friendship ended, milestone passed):

1. Set `status: retired`. Don't delete; row stays for history.

## 2. Sync downstream

After any operation:
- Update `Domains/<for_member's-likely-domain>/history.md` if a greeting was drafted or a gift was logged.
- Append to `interaction-log.yaml`.

## 3. Capture gift ideas in passing

This skill is a target for the `personal-signal` hook for **target: superagent**. When the user mentions a gift idea in passing ("X would be a great gift for Mom") in any session, the hook should append the idea to the matching `important-dates.yaml.<date>.gift_ideas[]` and surface a one-line capture confirmation.
