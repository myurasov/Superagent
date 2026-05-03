# Working-set Contract

<!-- Migrated from `procedures.md § 34`. Citation form: `contracts/working-set.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #23 + superagent/docs/_internal/perf-improvement-ideas.md (measurement section). Backed by `_memory/working-sets.jsonl` (append-only) + `tools/session_scratch.py` for the read-side.

**Every skill invocation records** (when `config.preferences.telemetry.record_working_sets: true`): files read, MCP / CLI calls, outputs produced, total bytes in/out, est. tokens, latency.

**Used by Supertailor's strategic pass** to spot:
- Skills that consistently over-read (loading 50 files when 5 would do).
- Skills that miss obvious local sources (asking about a topic but not reading the relevant Sources entry).
- Patterns where ingest-then-discard occurs (fetched data never read).

**Privacy**: paths + sizes only — never contents.
