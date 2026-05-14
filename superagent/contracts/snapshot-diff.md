# Snapshot Diff Contract

<!-- Migrated from `procedures.md § 35`. Citation form: `contracts/snapshot-diff.md`. -->

> **Status: design-only (writer side).** The diff reader (`tools/snapshot_diff.py`) ships and works against any pair of snapshot directories. The **daily snapshot writer does not exist yet** — `_memory/_checkpoints/<date>/` is not auto-populated. Tracked as `roadmap.md` § S-27. Skills MUST NOT assume daily snapshots exist; the diff reader gracefully falls through to comparing live `_memory/` when checkpoint folders are absent. The "weekly-review / monthly-review consume snapshot-diff" hook below is a future integration, not a current behaviour.

Implements superagent/docs/_internal/ideas-better-structure.md item #6. Backed by `tools/snapshot_diff.py`.

**Daily snapshots (planned, per `config.preferences.privacy.snapshot_memory_daily: true`):** once S-27 ships, `_memory/_checkpoints/<YYYY-MM-DD>/` will carry a copy of `_memory/` at the start of the first agent action of the day, with `snapshot_retention_days: 14`.

**Diff queries** (work today against any pair of checkpoint dirs):
- `snapshot_diff --weekly` — today vs 7 days ago.
- `snapshot_diff --monthly` — today vs 30 days ago.
- `snapshot_diff --since <date>` / `--from <a> --to <b>` — explicit range.

**Output**: markdown report with per-file changes (rows added / modified / removed) + status flips on existing entities.

**Future integration:** once daily snapshots ship, `weekly-review` and `monthly-review` will consume `snapshot_diff` output as part of their "what changed" sections. Today these skills do not call it.
