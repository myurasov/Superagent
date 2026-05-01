---
name: superagent-events
description: >-
  Read or append events in the unified time-stream. Time-partitioned
  storage (`_memory/events/<YYYY-Qn>.yaml`) so reads are O(log n). The
  source of truth for cross-entity timelines; per-entity history.md
  files are denormalized projections.
triggers:
  - events
  - timeline
  - what happened on
  - log an event
  - log event
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent events skill

Implements ideas-better-structure.md item #16 + perf-improvement-ideas.md MI-2. Engine: `tools/log_window.py`.

## 1. Branch on intent

### Read (default for "what happened")

User said "what happened on April 15" / "show events this week":

1. Determine window:
   - "today" -> start of today -> now.
   - "this week" -> last 7 days.
   - "this month" -> last 30 days.
   - "<date>" -> 00:00:00 to 23:59:59 of that date.
   - "between X and Y" -> explicit range.
2. Run:
   ```
   python3 -m superagent.tools.log_window read --since <iso> --until <iso> [--kind <kind>] [--limit 200]
   ```
3. Group by `kind`. Surface: `<ts>  [<kind>] <subject>` per row.

### Append

When a skill (or the user) reports something happened:

```
python3 -m superagent.tools.log_window append \
  --kind <kind> --subject "<short>" --summary "<paragraph>" \
  [--related-domain <id>] [--related-project <id>] \
  [--payload '<json>'] [--tags "tag1,tag2"]
```

Standard event kinds (see `events.yaml` template):
- interaction, ingest_run, skill_run, status_flip
- bill_paid, task_completed, task_created
- appointment_completed, health_event, maintenance_done
- decision, important_date_marked
- source_added, source_accessed, project_milestone
- audit, capture_signal, cache_evict, ingest_failure, other

### Stats

`events stats` -- partition counts; useful after rotation or import.

```
python3 -m superagent.tools.log_window stats
```

### Rebuild index

After manual edits to a partition file:

```
python3 -m superagent.tools.log_window rebuild-index
```

Refreshes `_memory/events.yaml` (the partition index over the events/ directory).

## 2. Mirror contract

When `config.preferences.events.auto_mirror_history_md` is true (default), every H4 entry appended to a `Domains/<X>/history.md` or `Projects/<X>/history.md` ALSO emits a corresponding event with `kind: project_milestone` (for projects) or `kind: <inferred>` (for domains). This keeps the events stream complete.

When `config.preferences.events.auto_mirror_interaction_log` is true (default), every row appended to `interaction-log.yaml` ALSO emits an event with `kind: interaction` (or the more specific kind if the row's `type` field maps cleanly).

## 3. Logging

This skill appends to events stream directly (it's the canonical writer). It also adds a single line to `interaction-log.yaml` for cadence-skill discoverability.
