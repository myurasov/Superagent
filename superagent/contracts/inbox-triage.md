# Inbox Triage Contract

<!-- Migrated from `procedures.md § 37`. Citation form: `contracts/inbox-triage.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #5. Backed by `tools/inbox_triage.py` + the `inbox-triage` skill + `Inbox/_processed.yaml` decision log.

**Walk + ask**: every file in `Inbox/` (not starting with `.` or `_`) is classified by extension + filename keyword heuristic, then surfaced for: file / discard / leave / defer.

**Pattern learning**: after 3+ files matching the same pattern get filed to the same destination, the skill offers to auto-apply the rule next time. Rules accumulate in `_memory/inbox-rules.yaml` (lazily created).

**Stale items** (per `config.preferences.inbox_triage.stale_days: 14`): surfaced by `daily-update` and `weekly-review`.
