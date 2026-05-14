# Memory Persistence Model

<!-- Migrated from `procedures.md § 9`. Citation form: `contracts/memory-persistence.md`. -->

- `_memory/` is the structured-state vault. Everything in there is YAML. Append-only files (`interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`) are NEVER rewritten — only appended to. Index files (`bills.yaml`, `subscriptions.yaml`, `appointments.yaml`, `important-dates.yaml`, `assets-index.yaml`, `accounts-index.yaml`, `contacts.yaml`, `documents-index.yaml`, `domains-index.yaml`, `health-records.yaml`, `data-sources.yaml`) are mutated in place.
- `Domains/<domain>/` is the human-readable narrative layer. Markdown only. Skills auto-update `status.md` and `history.md`; `info.md` is mostly hand-curated (skills update specific named sections only); `rolodex.md` is auto-synced from contact mentions.
- Schema versions live in each YAML's `schema_version` field. Schema migrations (when they happen) are handled by `tools/migrate.py`; the user is asked before any in-place migration.
- **Snapshots (design-only, not yet shipped):** the design calls for a daily snapshot of `_memory/` to `_memory/_checkpoints/<date>/` on the first agent action of each day, 14-day retention — see `contracts/snapshot-diff.md` + `roadmap.md` § S-27. The diff reader (`tools/snapshot_diff.py`) ships; the snapshot writer does not. Until S-27 lands, `_memory/_checkpoints/` is created lazily only by manual / external automation, and skills MUST NOT assume it exists.
