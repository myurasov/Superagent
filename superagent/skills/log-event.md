---
name: superagent-log-event
description: >-
  One-shot capture: "log this medical visit", "log this car service",
  "log this home repair", "log this conversation with <person>". Routes the
  event to the right `Domains/<domain>/history.md`, updates the right
  index file, and creates any obvious follow-up tasks.
triggers:
  - log this <event>
  - just <happened|got back from|finished> <event>
  - record this <event>
  - capture this conversation / interaction
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent log-event skill

This is the catch-all "I just did / experienced / heard X — record it" capture skill. The agent figures out from context which sub-flow to run.

> **Pre-flight (lazy-materialization contract — `contracts/domains-and-assets.md` § 6.4a)**:
> This skill writes to `Domains/<domain>/history.md` (and sometimes
> `rolodex.md`) for whichever domain the event resolves to. After step 1
> classifies the event and the target domain is known, run:
>
>     uv run python -m superagent.tools.domains ensure <domain-id>
>
> BEFORE the first append. Sub-flows that delegate to a dedicated skill
> (`health-log`, `vehicle-log`, `home-maintenance`, `pet-care`, `bills`)
> inherit that skill's own ensure call — no duplicate needed.

## 1. Classify the event

Heuristics:

| If the event mentions … | Sub-flow |
|---|---|
| a medical / dental / vet provider in `contacts.yaml`, OR a kind word ("appointment", "visit", "checkup", "exam", "shot") | **medical** (see `health-log.md` for the focused variant) |
| a vehicle in `assets-index.yaml`, OR words "service", "oil change", "tire rotation", "brakes", "registration", "inspection" | **vehicle** (see `vehicle-log.md`) |
| home / appliance, OR "repair", "service", "filter", "HVAC", "plumber", "electrician", "contractor" | **home maintenance** (see `home-maintenance.md`) |
| a pet in `contacts.yaml` (role: pet) | **pet care** (see `pet-care.md`) |
| a bill / payment / charge | **bill** (delegate to `bills mark-paid`) |
| "talked to <person>" / "had coffee with <person>" / "called <person>" | **interaction** |
| "log this decision" / "we decided" / "I decided" | **decision** (append a row to `_memory/decisions.yaml` per `contracts/decisions.md` — required: `id`, `ts`, `decision`, `context`, `confidence`, `reversibility`; encouraged: `rationale`, `review_at`; also append to the relevant domain/project `history.md`) |
| anything else | **generic** (append to relevant domain history) |

If multiple plausible matches, ask the user which sub-flow.

## 2. Sub-flow execution

For sub-flows other than **interaction** and **generic**, delegate to the dedicated skill (`health-log`, `vehicle-log`, `home-maintenance`, `pet-care`, `bills`).

For **interaction**:

1. Resolve the contact (offer `add-contact` if missing).
2. Ask: when, summary, action items.
3. Append to `_memory/interaction-log.yaml` in the canonical row shape
   (`templates/memory/interaction-log.yaml` is normative — never the retired
   `timestamp` / `type` / `subject` / `participants` keys). Name the contact
   in `summary`; there is no `participants` field.
   ```yaml
   - id: "<ilog-YYYY-MM-DD-NNN via uv run python -m superagent.tools.next_id --kind ilog --file _memory/interaction-log.yaml>"
     ts: "<when — ISO 8601 datetime with offset>"
     skill: "log-event"
     action: "<meeting|call|note> with <contact name>"
     summary: "<what was discussed / decided with <contact name>>"
     related_domain: <inferred from contact's primary related_domain, or null>
     related_project: null
     related_asset: null
     related_account: null
     action_items: <list>
     ingestion_log_ref: null
   ```
4. For each action item, create a P2 task in `todo.yaml`.
5. Update `Domains/<related_domain>/rolodex.md` row for the contact: `Last contacted` ← `ts`.
6. Optionally append to `Domains/<related_domain>/history.md` if the interaction was substantive.

For **generic**:

1. Ask: which domain.
2. Append H4 entry to `Domains/<Domain>/history.md` with the user's text.
3. Capture any obvious follow-ups as P2 tasks.

## 3. Confirm

```
Logged <event-type> in <domain> on <date>.
Action items captured: <count>.
```

## 4. Logging

`interaction-log.yaml` already updated in step 2.
