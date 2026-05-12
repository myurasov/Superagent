# Local-first Read Order

<!-- Migrated from `procedures.md § 38`. Citation form: `contracts/local-first-read-order.md`. -->

Implements superagent/docs/_internal/perf-improvement-ideas.md QW-7. Codifies the read-order discipline.

**Every skill that needs data MUST consult local first**:

1. **Local index** (`_memory/<index>.yaml`). For the Sources index specifically, always call `uv run python -m superagent.tools.sources_index refresh` first — the index is derived from the filesystem and the call is mtime-lazy when nothing changed.
2. **Local Sources cache** (`<cache_path>/<hash>/`, default `Sources/_cache/`) — `_summary.md` first, then `_toc.yaml`, then only relevant chunks. If the matching ref is not yet in canonical form, run `tools/sources_normalize.py apply --mode ask <path>` (interactive) before fetching.
3. **Domain / Project history.md** for narrative recall.
4. **Events stream** (`tools/log_window.py read`) for cross-entity timelines.
5. **Live MCP / CLI source** ONLY when **all** are true:
   (a) the local read returned no candidates that match the question; AND
   (b) the time window the question is asking about extends past the source's `last_ingest`; AND
   (c) freshness genuinely matters for the question.

When the live call happens, capture-through MUST run (per § 2 ingestion contract) so the next read is local.

**Anti-pattern**: "I'll check both" (local + live in parallel) — flagged by the anti-pattern scanner. Local first; fall through only when justified.
