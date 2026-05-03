# Events Stream Contract

<!-- Migrated from `procedures.md § 29`. Citation form: `contracts/events-stream.md`. -->

Implements ideas-better-structure.md item #16 + perf-improvement-ideas.md MI-2. Backed by `_memory/events/<YYYY-Qn>.yaml` (partitioned) + `_memory/events.yaml` (partition index) + `tools/log_window.py`.

**The events stream is the unified canonical timeline.** Per-entity history.md files remain as denormalized projections.

**Auto-mirror**:
- `config.preferences.events.auto_mirror_history_md: true` (default) — every H4 entry appended to a `Domains/<X>/history.md` or `Projects/<X>/history.md` ALSO emits an event.
- `config.preferences.events.auto_mirror_interaction_log: true` (default) — every row appended to `interaction-log.yaml` ALSO emits an event.

**Partitioning**: quarterly by default (`config.preferences.events.partition: quarterly`). Monthly available for high-volume workspaces. The partition index `_memory/events.yaml` is updated atomically by `tools/log_window.py update_index`.

**Reading**: skills query via `tools/log_window.py read --since --until` (ranged queries are O(quarters touched)). Cross-entity timelines ("what happened on April 15") are now a single-file query.

**Writing**: skills call `tools/log_window.py.append_event(workspace, event_dict)`. The event id (`evt-YYYY-MM-DD-NNN`) is auto-generated.
