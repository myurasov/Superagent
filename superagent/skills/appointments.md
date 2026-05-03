---
name: superagent-appointments
description: >-
  List upcoming appointments, prep for one (assemble briefing context),
  mark complete (with outcome + follow-ups), reschedule, cancel.
triggers:
  - appointments
  - upcoming appointments
  - prep for <appointment>
  - mark <appointment> done
  - reschedule <appointment>
mcp_required: []
mcp_optional:
  - calendar ingestors (gcal / icloud_calendar / outlook_calendar)
cli_required: []
cli_optional: []
---

# Superagent appointments skill

## 1. Branch on intent

### List (default)

1. Read `_memory/appointments.yaml`.
2. Filter `status: scheduled` AND `start >= today`.
3. Sort ascending by `start`.
4. Format: `{date} {time} — {title} with {provider}, for {member} ({location})`.
5. Cap at 14 days unless `--all` or `--upcoming-days N` passed.

### Prep

User asked to prep for an appointment:

1. Resolve appointment by id or fuzzy name match.
2. Assemble a prep brief:
   - **Provider context**: from `contacts.yaml.<provider>` — last contacted date, prior visit notes (search `Domains/<domain>/history.md`).
   - **Member context**:
     - For health appointments: from `health-records.yaml` for that `for_member` — active conditions, current meds, any open symptoms in last 30 days, any abnormal results not yet followed up.
     - For vehicle appointments: from `assets-index.yaml.<asset>` — last service date, mileage, open maintenance items.
     - For pet appointments: from `assets-index.yaml.<asset>` (pets stored as assets) — vaccination status, current meds, food.
   - **Prep notes**: from `appointments.yaml.<appt>.prep_notes`.
   - **Questions to ask**: extract from prep_notes + any open follow-ups from `appointments.yaml.history[]` matching this provider.
   - **What to bring**: heuristic by kind — insurance card for medical/dental/vision; med list for medical; vehicle title for some mechanic visits; pet vaccine record for vet; pay-stubs for financial; etc.
3. Render the brief as a printable markdown doc into `workspace/Outbox/appointments/<id>-prep.md`.
4. Set `appointments.yaml.<appt>.prep_done: true`.
5. If a P1 prep task exists in `todo.yaml` for this appointment, mark it `done`.

### Mark complete

User said "I just got back from / had <appointment>":

1. Resolve appointment.
2. Ask:
   - **Outcome** — free text. What happened, what was decided, what they said.
   - **Medications changed** (yes / no, only for medical / vet appointments).
   - **Follow-ups** — list of `{description, due_date}` items.
3. Set `status: completed`, `completed_at: now`, `outcome: <text>`, `followup: <list>`.
4. For each follow-up, create a P2 task in `todo.yaml` with `related_appointment: <id>`.
5. Append to the relevant `Domains/<Domain>/history.md`:

   ```markdown
   #### <YYYY-MM-DD> — <Title> with <provider>

   <outcome>

   Follow-ups:
   - [ ] <description> (due <date>)

   ---
   ```
6. If `kind` is medical / dental / vision / mental_health / vet, also append a row to `_memory/health-records.yaml.visits[]` per its schema.
7. **Recurrence**: if `recurrence` is non-null, auto-create the next occurrence in `appointments.yaml` (set `start = completed_at + interval`, `status: scheduled`, `prep_done: false`).

### Reschedule

User asked to move an appointment:

1. Resolve appointment.
2. Ask new `start`.
3. Update `start`, set `status: scheduled` (if it was `rescheduled` already).
4. Re-emit any prep task with the new due date.

### Cancel

1. Resolve appointment.
2. Set `status: cancelled`, `notes` += "<reason>".
3. Mark any prep task `cancelled`.

## 2. Sync downstream

After any operation:
- Update `Domains/<Domain>/status.md` § Next Steps (remove cancelled / completed; add new if rescheduled).
- Update `Domains/<Domain>/rolodex.md` row for the provider with `Last contacted` ← appointment's completed_at.
- Append to `interaction-log.yaml`.

## 3. Sources

Calendar ingestors push items into `appointments.yaml` automatically when their detection heuristic fires (per `contracts/ingestion.md` ingestor obligations). Skill prefers local data; calls live calendar only for the strictly-newer slice on `--refresh`.
