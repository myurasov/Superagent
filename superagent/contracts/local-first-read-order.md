# Local-first Read Order

<!-- Migrated from `procedures.md § 38`. Citation form: `contracts/local-first-read-order.md`. -->

Implements superagent/docs/_internal/perf-improvement-ideas.md QW-7. Codifies the read-order discipline.

**Every skill that needs data MUST consult local first**:

1. **Local index** (`_memory/<index>.yaml`). For the Sources index specifically, always call `uv run python -m superagent.tools.sources_index refresh` first — the index is derived from the filesystem and the call is mtime-lazy when nothing changed. A document under `Sources/` is read from its path (its `.meta.md` sidecar first, when one exists — `contracts/sources.md` § 15.5); a watcher's "did it move?" answer is already local in `_memory/watchlist-state.yaml` (`last_checked`, `last_changed`, `last_outcome`, the stamped note) and its harvested records are in the typed index the pack writes (`transactions.yaml`, ...). Never run a check or a harvest to answer a question the state file or the typed index already answers.
2. **Domain / Project history.md** for narrative recall.
3. **Events stream** (`tools/log_window.py read`) for cross-entity timelines.
4. **Live MCP / CLI source** ONLY when **all** are true:
   (a) the local read returned no candidates that match the question; AND
   (b) the time window the question is asking about extends past the source's `last_success` / `last_harvest` in `watchlist-state.yaml`; AND
   (c) freshness genuinely matters for the question.

When the live call happens, capture-through MUST run (per § 2 ingestion contract) so the next read is local. For a registered watcher the sanctioned live call is `watch harvest --id <id>` (budget-gated, `contracts/watchlist.md` § 8), not an ad-hoc fetch.

**Anti-pattern**: "I'll check both" (local + live in parallel) — flagged by the anti-pattern scanner. Local first; fall through only when justified.
