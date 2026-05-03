# Snapshot Diff Contract

<!-- Migrated from `procedures.md § 35`. Citation form: `contracts/snapshot-diff.md`. -->

Implements ideas-better-structure.md item #6. Backed by `tools/snapshot_diff.py`.

**Daily snapshots** (per `config.preferences.privacy.snapshot_memory_daily: true`): `_memory/_checkpoints/<YYYY-MM-DD>/` carries a copy of `_memory/` at the start of the first agent action of the day. Retention: `snapshot_retention_days: 14`.

**Diff queries**:
- `snapshot_diff --weekly` — today vs 7 days ago.
- `snapshot_diff --monthly` — today vs 30 days ago.
- `snapshot_diff --since <date>` / `--from <a> --to <b>` — explicit range.

**Output**: markdown report with per-file changes (rows added / modified / removed) + status flips on existing entities.

The `weekly-review` and `monthly-review` skills consume snapshot-diff output as part of their "what changed" sections.
