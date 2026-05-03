# Memory Persistence Model

<!-- Migrated from `procedures.md § 9`. Citation form: `contracts/memory-persistence.md`. -->

- `_memory/` is the structured-state vault. Everything in there is YAML. Append-only files (`interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`) are NEVER rewritten — only appended to. Index files (`bills.yaml`, `subscriptions.yaml`, `appointments.yaml`, `important-dates.yaml`, `assets-index.yaml`, `accounts-index.yaml`, `contacts.yaml`, `documents-index.yaml`, `domains-index.yaml`, `health-records.yaml`, `data-sources.yaml`) are mutated in place.
- `Domains/<domain>/` is the human-readable narrative layer. Markdown only. Skills auto-update `status.md` and `history.md`; `info.md` is mostly hand-curated (skills update specific named sections only); `rolodex.md` is auto-synced from contact mentions.
- Schema versions live in each YAML's `schema_version` field. Schema migrations (when they happen) are handled by `tools/migrate.py`; the user is asked before any in-place migration.
- Snapshots: a daily snapshot of `_memory/` is auto-saved to `_memory/_checkpoints/<date>/` on the first agent action of each day, with a 14-day retention. Lets the user roll back any "the agent did something I didn't want" mishap.
