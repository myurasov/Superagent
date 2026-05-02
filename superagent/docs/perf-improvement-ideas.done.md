# `perf-improvement-ideas.md` — implementation log

This file records which items from `perf-improvement-ideas.md` were implemented as of this build, and where their artifacts live.

Items NOT in this log remain in the original brainstorm doc as future candidates.

**Generated**: 2026-04-28 (autonomous batch implementation).

**Test status at completion**: `pytest -q superagent/tests` → 96 passed.

---

## Quick wins (QW) — all implemented

### QW-1 — Skill-discovery manifest ✓

- File: `superagent/skills/_manifest.yaml` (auto-generated; 47 rows).
- Tool: `tools/build_skill_manifest.py` (--framework / --workspace / --output).
- Generation walks both `superagent/skills/*.md` and `workspace/_custom/skills/*.md`; merges with `origin: framework | custom`.
- Tests: `tests/test_skill_manifest.py` (2 tests).
- Re-run trigger: every Supertailor hygiene pass; every skill add / change.

### QW-2 — Per-skill step index ✓

- 28 long skills (≥ 100 lines) carry an auto-generated `## Step index` block right after frontmatter.
- The agent's read pattern: load frontmatter + step index (~40 lines), then `Read --offset --limit` against the listed range for the relevant step.
- Tool: `tools/add_step_index.py` (--framework / --min-lines / --skill / --check).
- Idempotent: re-runs replace the existing block.

### QW-3 — Read-budget policy in AGENTS.md ✓

- AGENTS.md: § "Read budget (token efficiency)" — explicit rule that for files > 200 lines, Grep first then `Read --offset --limit`; batch parallel reads; use the manifest; use briefing cache; use log summaries.
- Mirrored in `procedures.md` § 38 "Local-first Read Order".
- Anti-pattern scanner enforces (`tools/anti_patterns.py`).

### QW-4 — `summary.md` siblings for unbounded YAML logs ✓

- Tool: `tools/log_summarize.py` (--workspace / --file / --all).
- Targets: `interaction-log.yaml`, `ingestion-log.yaml`, `pm-suggestions.yaml`, `personal-signals.yaml`, `action-signals.yaml`, `decisions.yaml`.
- Output: `<file>.summary.yaml` sibling with totals, last_30_days breakdown, last_7_days breakdown, notable rows.
- Tests: `tests/test_log_summarize_and_diff.py::test_log_summarize_interaction_log`.

### QW-5 — Pre-rendered briefing cache ✓

- Tool: `tools/briefing_cache.py` (get / put / list / evict).
- Storage: `_memory/_artifacts/<skill>/<key>.md` + `<key>.meta.yaml`.
- Invalidation: TTL (per-skill, default 720 min) + input-hash (sha256 over input file paths' size + mtime).
- Config: `config.preferences.briefing_cache.{enabled, ttl_minutes, invalidate_on_source_mtime}`.
- Contract: `procedures.md` § 32 "Briefing Cache Contract".
- Tests: `tests/test_briefing_cache.py` (4 tests).

### QW-6 — Real LLM-generated `_summary.md` for the Sources cache ✓

- `tools/sources_cache.py.write_summary()` rewritten to produce a richer summary with:
  - Document-shape header (kind, bytes, line count).
  - "What this document is" (titled by H1 if present + section count).
  - Section index (markdown table) for `Read --offset --limit` targeting — listing every H1/H2/H3/H4 heading + its line number (capped at 40 with overflow note).
  - First 400 chars snippet.
- The agent can ENHANCE this in the same turn by appending a 1-2 sentence overview at the top (BB-2-style write-back).
- Existing tests still pass (`tests/test_sources_cache.py` — 8 tests).

### QW-7 — Codify local-first read order across every ingestor ✓

- Codified as `procedures.md` § 38 "Local-first Read Order" — explicit AND-condition for falling through to live source.
- Mirrored in `AGENTS.md` § "Local-first read order".
- Anti-pattern scanner catches violations (AP-3, AP-5, AP-6).

---

## Medium investments (MI)

### MI-1 — Per-session scratchpad / dedupe ✓

- Tool: `tools/session_scratch.py` (derive_session_id / record_read / is_already_loaded / record_mcp / record_tool / list_sessions / cleanup).
- Storage: `_memory/_session/<session-id>.yaml`.
- Config: `config.preferences.session.{enabled, keep_recent_sessions, expire_days}`.
- Contract: `procedures.md` § 33 "Per-session Scratchpad Contract".
- Tests: `tests/test_audit_and_session.py::test_session_*` (2 tests).

### MI-2 — Time-partitioned interaction log + events stream ✓

(Implemented as part of ideas-better-structure #16 + #22.)

- Tool: `tools/log_window.py` — quarterly partitions (`_memory/events/<YYYY-Qn>.yaml`) with index in `_memory/events.yaml`.
- Reads: O(quarters touched) via `read_window`.
- Writes: `append_event` routes to current quarter's partition.
- Contract: `procedures.md` § 29.
- Tests: `tests/test_log_window.py` (4 tests).

### MI-4 — Range-aware `add_step_index.py` ✓

- Tool: `tools/add_step_index.py` (--check / --min-lines / --skill flags).
- Walks every skill markdown, generates a TOC from H2/H3 headings + line numbers, emits the step-index block.
- Idempotent: replaces existing block on re-run.
- Run as part of the Supertailor / `doctor` hygiene pass to keep step indexes in sync as skills are edited.

### MI-5 — Skill-output write-back caching ✓

- Implemented via the same `tools/briefing_cache.py` (QW-5).
- Convention: every skill whose output is a candidate for re-read writes to `_memory/_artifacts/<skill>/<key>.md` with sibling `<key>.meta.yaml` (skill, key, generated_at, ttl_minutes, inputs_hash, size_bytes).
- The Supertailor's strategic pass surfaces skills that NEVER cache-hit (candidates for shorter TTL or different cache key).

---

## Big bets (BB) — partially implemented

### BB-2 (b) — Practical guidance for Cursor / Claude Code today ✓

(BB-2-a — Anthropic prompt-cache alignment in an API wrapper — NOT implemented; would require a CLI wrapper.)

- AGENTS.md: § "Prompt-cache discipline" — explicit guidance:
  - Don't edit AGENTS.md / procedures.md mid-session.
  - The Supertailor / Supercoder commit-then-restart cycle aligns naturally.
  - Don't open many framework files mid-session.
  - Long ingestion / scenario sessions: prefer dedicated tool invocations.
- The forward-looking BB-2-a path is documented in the same section for when a CLI wrapper ships.

### BB-4 — World-model entity graph ✓

(Implemented as part of ideas-better-structure #3.)

- Tool: `tools/world.py` (rebuild / related / stats / validate / ensure_node / ensure_edge).
- Storage: `_memory/world.yaml` (derived state).
- Skill: `skills/world.md`.
- Contract: `procedures.md` § 24.
- Tests: `tests/test_world.py` (5 tests).
- Auto-rebuild from entity files; safe to delete and regenerate at any time.

---

## Anti-patterns to flag in skills ✓

- Tool: `tools/anti_patterns.py` (scan_file / scan_dir / render_text + 9 patterns + mitigation hints).
- Catalogue: 9 patterns with severity + mitigation.
- Codified in `procedures.md` § 39 "Skill-anti-pattern Catalogue".
- Config: `config.preferences.anti_patterns.scan_skills: true` (default) — runs in Supertailor / `doctor` hygiene pass.
- `--strict` flag exits non-zero on any `warning` hit (suitable for CI).
- Tests: `tests/test_inbox_and_anti_patterns.py::test_anti_patterns_*` (2 tests).
- The shipped framework's own skills pass with ≤ 2 warning hits (verified in test).

---

## NOT implemented (remain in `perf-improvement-ideas.md` as future)

- **MI-3** — Embedded full-text search (SQLite FTS5) — same as ideas-better-structure #7.
- **BB-1** — Embeddings for semantic retrieval — separate roadmap item.
- **BB-2-a** — Anthropic prompt-cache alignment in a CLI wrapper.
- **BB-3** — Pre-warmed cadence briefings (cron / launchd).
- **Measurement** section (full telemetry + A/B framework) — partially: working-sets.jsonl ships per item #23; telemetry config block lands in `config.yaml`; the actual A/B harness is deferred.

---

## Cross-cutting changes (also recorded in `ideas-better-structure.done.md`)

- **`config.yaml`** extended with new policy blocks for every contract.
- **`workspace_init.py`** scaffolds new folders + copies new memory templates.
- **`AGENTS.md`** extended with new operating sections.
- **`procedures.md`** extended with 23 new sections (§ 17 through § 39).
- **Test count grew from 43 → 96**.
