# Outbox Lifecycle Contract

<!-- Migrated from `procedures.md § 36`. Citation form: `contracts/outbox-lifecycle.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #13. Backed by `_memory/outbox-log.yaml` + sub-folders under `Outbox/`.

**Lifecycle stages** (folders under `Outbox/`):

- `drafts/` — in-progress; agent may revise; mutable.
- `staging/` — finalized; awaiting user "send"; mutable until sent.
- `sent/` — user marked sent; immutable thereafter.
- `sealed/` — snapshots (e.g. handoff packet versions); immutable on creation.

**Every artifact tracked**: append a row to `_memory/outbox-log.yaml.artifacts[]` on create + on each stage transition. The `artifact.path` field advances with the file as it moves between sub-folders.

**Sealing**: on `seal`, the file is moved to `sealed/`, the row's `sealed: true` + `sealed_hash: <sha256>` is recorded. Future writes to the same path are refused.

**Stale-drafts surfacing**: drafts older than `config.preferences.outbox.drafts_stale_days: 14` surface in `weekly-review`.
