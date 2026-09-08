# Memory Taxonomy (entity / time / state shape)

<!-- Migrated from `procedures.md § 17`. Citation form: `contracts/memory-taxonomy.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #9. Codifies the three shapes of YAML files under `_memory/`. Tools enforce; the Supertailor's hygiene pass flags violations.

| Shape | Files | Mutation rule | Read pattern |
|---|---|---|---|
| **Entity** | `contacts.yaml`, `accounts-index.yaml`, `assets-index.yaml`, `domains-index.yaml`, `projects-index.yaml`, `documents-index.yaml`, `subscriptions.yaml`, `bills.yaml`, `appointments.yaml`, `important-dates.yaml`, `sources-index.yaml`, `tags.yaml`, `world.yaml`, `transactions.yaml` (ingested bank / card rows keyed by external id; in-place reconciliation writes such as a bill / subscription match or a category correction are shape-legal) | Long-lived rows; mutate-in-place; cross-referenced by id | Full read on access; `<file>.history.jsonl` audit-trail sibling captures every mutation |
| **Time (event-shape)** | `interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`, `decisions.yaml`, `outbox-log.yaml`, `inbox-log.yaml`, `upstream-writes.yaml`, `supertailor-suggestions.yaml`, `health-records.yaml.{vitals,symptoms,vaccines,results,visits}`; `_memory/events/<YYYY-Qn>.yaml` partitions; `_memory/email/_messages.jsonl` (latest-wins per Gmail `message.id`) | Append-only; existing rows MAY mutate fields like `status`, `processed_at`, `outcome` but never the historical content | Time-windowed via `tools/log_window.py read --since --until`; `<file>.summary.yaml` sibling for cheap aggregate reads |
| **State (singleton)** | `context.yaml`, `model-context.yaml`, `config.yaml`, `watchlist-state.yaml` (machine-owned: written only by `tools/watchlist.py` under an fslock; the watcher *registry* is the folder of `Sources/Watchlist/<Title_Case>.ref.md` files, per `contracts/watchlist.md`), `_memory/email/_index.yaml` | Read at session start; write at session end; never grows | Always full-read |

`data-sources.yaml` (retired by 0.19.0, moved to `_memory/_retired/`) mixed per-source entity rows with machine-written run state — the taxonomy violation the watchlist resolves: configuration is user-editable refs under `Sources/Watchlist/`, run state is the `watchlist-state.yaml` singleton.

**Enforcement**: `tools/audit.py.record_change()` rejects writes to time-shape and state-shape files (they audit themselves or don't need audit). The skip-list in `config.preferences.audit.skip_files` carries the official list.

**Per-message blob trees** (e.g. `_memory/email/<YYYY>/<MM>/<DD>/*.json` per `contracts/email-capture.md`) are NOT first-class memory-shape files — they are the immutable backing store the time-shape sidecar (`_messages.jsonl`) indexes. Audit / mutation rules apply to the sidecar; the per-message JSON is rewritten in place only on `stub -> full` upgrade. The same pattern can host future per-message archives (iMessage, Slack DMs, etc.) without expanding the table.
