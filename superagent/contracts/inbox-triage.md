# Inbox Triage Contract

<!-- Migrated from `procedures.md § 37`. Citation form: `contracts/inbox-triage.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #5. Backed by `tools/inbox_triage.py` + the `inbox-triage` skill + the `_memory/inbox-log.yaml` decision log.

**Decision log**: `_memory/inbox-log.yaml` — time-shape, append-only (`contracts/memory-taxonomy.md`); one row per triage decision (`ts`, `file` = the name as dropped in `Inbox/`, `action`, `destination`, `note`). Written only through `inbox_triage.py record` (also by `add-source` when it files from `Inbox/`); the command refuses to overwrite an unparseable log and refuses to write while a pre-0.17.0 `Inbox/_processed.yaml` is still present (the `migrate` skill moves it).

**Walk + ask**: every file in `Inbox/` (not starting with `.` or `_`) is classified by extension + filename keyword heuristic, then surfaced for: file / discard / leave / defer.

**Pattern learning**: after 3+ files matching the same pattern get filed to the same destination, the skill offers to auto-apply the rule next time. Rules accumulate in `_memory/inbox-rules.yaml` (lazily created).

**Stale items** (per `config.preferences.inbox_triage.stale_days: 14`): surfaced by `daily-update` and `weekly-review`.
