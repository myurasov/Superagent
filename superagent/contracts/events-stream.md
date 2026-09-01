# Events Stream Contract

<!-- Migrated from `procedures.md § 29`. Citation form: `contracts/events-stream.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #16 + superagent/docs/_internal/perf-improvement-ideas.md MI-2, revised per st-2026-08-31-010 (option c). Backed by `_memory/events/<YYYY-Qn>.yaml` (partitioned) + `_memory/events.yaml` (partition index) + `tools/log_window.py` (reader) + `tools/events_derive.py` (producer).

**The events stream is a DERIVED VIEW.** Nothing writes to it per-write — not skills, not ingestors, not hooks. The two sources of truth are `_memory/interaction-log.yaml` (both row schemas: old-format `timestamp`/`type` rows and new-format `id`/`ts`/`skill`/`action` rows) and Domain/Project `history.md` H4 entries (`#### YYYY-MM-DD — <title>`, optionally with a time / parenthetical suffix between date and separator — `#### 2026-05-26 08:30 PT — ...`, `#### 2026-05-28 (later) — ...` — including archived copies under `Archive/*/Domains|Projects/*/history.md`; date-led H4 lines that fail to parse are reported as `unmatched_headers` by the rebuild, never silently dropped). Partitions are rebuilt wholesale from those sources by `uv run python -m superagent.tools.events_derive rebuild`, the same way `tools/world.py rebuild` regenerates the world graph.

**Production rules**:

- **Legacy freeze.** Partition rows carrying `legacy: true` (the hand-curated 2026-Q2 rows flagged by migration 0.16.0) are preserved byte-verbatim across rebuilds — never regenerated. The max `ts` among legacy rows is the derivation watermark: only source records strictly after it are derived. Any hand row that must survive a rebuild needs `legacy: true`; every other row is regenerated (and lost if it has no source record). **Overwrite guard:** `rebuild` refuses when a never-derived partition contains rows without `legacy: true` (a pre-0.16.0 hand-authored partition) — run `flag-legacy` (via the `migrate` skill) first; `--allow-overwrite` bypasses the guard and destroys those rows.
- **Determinism.** A rebuild over unchanged sources is byte-identical (no wall-clock timestamps inside partitions; ids are minted sequentially per date in stable sort order). The index's `derive_state.derived_at` bumps only when partition content actually changed (content hash).
- **Config.** `config.preferences.events.mode: derived | off` (default `derived`; `off` disables derivation). `skip_kinds` filters derivation by event kind. The former `auto_mirror_history_md` / `auto_mirror_interaction_log` toggles are retired as of 0.16.0.
- **Rebuild cadence.** `weekly-review` § 8b (derived views step) runs the rebuild; `doctor` runs `events_derive check` (exit 1 = sources newer than the last derivation) and offers a rebuild. Rebuilds are mtime-lazy — a no-op run is cheap.

**Partitioning**: quarterly (`config.preferences.events.partition: quarterly`). The partition index `_memory/events.yaml` is refreshed by the rebuild (per-partition counts, `by_kind`, `legacy_rows`, `derived: true`).

**Reading — consumers unchanged**: skills query via `tools/log_window.py read --since --until` (ranged queries are O(quarters touched)). Cross-entity timelines ("what happened on April 15") remain a single-file query. Derived rows add a `source` field pointing at the source-of-truth record (`interaction-log.yaml#<id>` or `<history.md path>#<header>`).

**Writing**: to put something on the timeline, write it to a source of truth — an `interaction-log.yaml` entry or a `history.md` H4 entry — and it materializes as an event on the next rebuild. Direct partition appends (e.g. `tools/log_window.py append`) are overwritten by the next rebuild unless the row is flagged `legacy: true`; do not use them in skills.
