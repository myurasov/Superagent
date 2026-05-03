# Memory Taxonomy (entity / time / state shape)

<!-- Migrated from `procedures.md § 17`. Citation form: `contracts/memory-taxonomy.md`. -->

Implements ideas-better-structure.md item #9. Codifies the three shapes of YAML files under `_memory/`. Tools enforce; the Supertailor's hygiene pass flags violations.

| Shape | Files | Mutation rule | Read pattern |
|---|---|---|---|
| **Entity** | `contacts.yaml`, `accounts-index.yaml`, `assets-index.yaml`, `domains-index.yaml`, `projects-index.yaml`, `documents-index.yaml`, `subscriptions.yaml`, `bills.yaml`, `appointments.yaml`, `important-dates.yaml`, `sources-index.yaml`, `tags.yaml`, `world.yaml`, `notification-policy.yaml` | Long-lived rows; mutate-in-place; cross-referenced by id | Full read on access; `<file>.history.jsonl` audit-trail sibling captures every mutation |
| **Time (event-shape)** | `interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`, `decisions.yaml`, `outbox-log.yaml`, `upstream-writes.yaml`, `supertailor-suggestions.yaml`, `health-records.yaml.{vitals,symptoms,vaccines,results,visits}`; AND `_memory/events/<YYYY-Qn>.yaml` partitions | Append-only; existing rows MAY mutate fields like `status`, `processed_at`, `outcome` but never the historical content | Time-windowed via `tools/log_window.py read --since --until`; `<file>.summary.yaml` sibling for cheap aggregate reads |
| **State (singleton)** | `context.yaml`, `model-context.yaml`, `data-sources.yaml`, `config.yaml` | Read at session start; write at session end; never grows | Always full-read |

**Enforcement**: `tools/audit.py.record_change()` rejects writes to time-shape and state-shape files (they audit themselves or don't need audit). The skip-list in `config.preferences.audit.skip_files` carries the official list.
