# `ideas-better-structure.md` — implementation log

This file records which items from `ideas-better-structure.md` were implemented as of this build, and where their artifacts live.

Items NOT in this log remain in the original brainstorm doc as future candidates.

**Generated**: 2026-04-28 (autonomous batch implementation).

**Test status at completion**: `pytest -q superagent/tests` → 96 passed.

---

## Implemented

### #2 — Workflow templates as first-class artifacts ✓

**Status**: Implemented.

- Schema: `superagent/templates/workflows/_schema.yaml`.
- 5 starter workflows shipped:
  - `tax-filing.yaml`
  - `trip-planning.yaml`
  - `annual-health-tuneup.yaml`
  - `job-search.yaml`
  - `appliance-replacement.yaml`
- Contract: `procedures.md` § 25 "Workflows Contract".
- Integration: `add-project --workflow <id>` (specified in skill markdown; full instantiation flow documented in skill + procedures).
- Lessons-learned loop: at completion, the user is prompted "anything to capture for next time?" — the answer appends to `_memory/procedures.yaml` cross-referenced from the workflow id.

### #3 — World model YAML — entity graph ✓

**Status**: Implemented (also closes perf-improvement-ideas BB-4).

- Memory template: `templates/memory/world.yaml`.
- Tool: `tools/world.py` (rebuild / related / stats / validate / ensure_node / ensure_edge).
- Skill: `skills/world.md`.
- Contract: `procedures.md` § 24 "World Graph Contract".
- Tests: `tests/test_world.py` (5 tests).
- Auto-population: every entity-mutating skill SHOULD call `ensure_node` / `ensure_edge`. Drift is recoverable via `tools/world.py rebuild`.
- Tag nodes auto-materialize when an edge points at them (avoids orphan-edge warnings).

### #4 — Provenance + facts-with-sources ✓

**Status**: Implemented as schema + contract; per-skill enforcement is opt-in.

- Schema addition: `provenance` field on entity rows (added to `domains-index.yaml`, `projects-index.yaml`; documented for all entity templates).
- Contract: `procedures.md` § 19 "Provenance Contract"; `AGENTS.md` § "Provenance".
- Markdown convention: `<!-- src: <ref> -->` inline annotation on facts in `info.md` § Key Facts.
- Default value: `{ source: "user", at: <created> }` for hand-entered rows.

### #5 — Inbox → Triage → File pipeline ✓

**Status**: Implemented.

- Tool: `tools/inbox_triage.py` (classify / list / stale / record).
- Skill: `skills/inbox-triage.md`.
- Contract: `procedures.md` § 37 "Inbox Triage Contract".
- Tests: `tests/test_inbox_and_anti_patterns.py::test_classify_*`, `test_record_decision_writes_log`, `test_stale_items`.
- Pattern learning: after 3+ files matching the same pattern, the skill offers to auto-apply.
- Decision log: `Inbox/_processed.yaml`.
- Pre-classification by extension + keyword (taxes, medical, vehicles, warranties, legal, identity, pets, education).

### #6 — Differential snapshots + change-detection ✓

**Status**: Implemented.

- Tool: `tools/snapshot_diff.py` (diff_files / status_flips / render_markdown).
- CLI: `--weekly`, `--monthly`, `--since`, `--until`, `--from`/`--to`, `--json`.
- Contract: `procedures.md` § 35 "Snapshot Diff Contract".
- Tests: `tests/test_log_summarize_and_diff.py::test_snapshot_diff_detects_added_row`.
- Falls through to live `_memory/` when target checkpoint is missing.

### #9 — Time-shape vs entity-shape vs event-shape ✓

**Status**: Implemented as taxonomy.

- Contract: `procedures.md` § 17 "Memory Taxonomy".
- AGENTS.md: § "Time-shape vs entity-shape vs event-shape".
- Per-shape table maps every `_memory/*.yaml` to its shape.
- Enforcement: `tools/audit.py` skip-list catches time-shape and state-shape files; the audit-trail is entity-shape only.

### #10 — Sub-domains and sub-projects ✓

**Status**: Implemented as opt-in schema.

- Schema addition: `parent` field on `domains-index.yaml.domains[]` and `projects-index.yaml.projects[]`.
- Contract: `procedures.md` § 22 "Hierarchies Contract".
- Folder convention: child folders nest under parent's path.
- Default: `parent: null` (flat).

### #11 — Tagging as first-class index ✓

**Status**: Implemented.

- Memory template: `templates/memory/tags.yaml`.
- Skill: `skills/tags.md` (list / search / add / rename / merge / recount).
- Contract: `procedures.md` § 23 "Tags Contract".
- Auto-register flag in config (`config.preferences.tags.auto_register: true`).
- Strict-canonical mode in config (default `false`).
- Tag aliases supported.

### #13 — Outbox lifecycle ✓

**Status**: Implemented.

- Memory template: `templates/memory/outbox-log.yaml`.
- Folder layout: `Outbox/{drafts,staging,sent,sealed}/` scaffolded by `workspace_init.py`.
- Contract: `procedures.md` § 36 "Outbox Lifecycle Contract".
- Stale-drafts surfacing in weekly-review.
- Tests: `tests/test_workspace_init.py::test_init_creates_outbox_lifecycle_subfolders`.

### #14 — Scenario / what-if planning ✓

**Status**: Implemented (5 canned scenarios).

- Tool: `tools/scenarios.py`.
- Skill: `skills/scenarios.md`.
- Contract: `procedures.md` § 27 "Scenarios Contract".
- Five scenarios: cancel-subscriptions, trial-end-impact, bill-shock, balance-floor, project-overrun.
- Output to stdout + optional `Outbox/scenarios/<name>.md`.
- Tests: `tests/test_play_and_scenarios.py::test_scenarios_*` (3 tests).

### #16 — Events stream as unifying timeline ✓

**Status**: Implemented (also closes perf-improvement-ideas MI-2).

- Memory template: `templates/memory/events.yaml` (partition index schema).
- Per-quarter partition files: `_memory/events/<YYYY-Qn>.yaml` (lazily created).
- Tool: `tools/log_window.py` (append / read / stats / rebuild-index).
- Skill: `skills/events.md`.
- Contract: `procedures.md` § 29 "Events Stream Contract".
- Auto-mirror toggles in config (`auto_mirror_history_md`, `auto_mirror_interaction_log`).
- Tests: `tests/test_log_window.py` (4 tests).

### #17 — Audit trail on every YAML row ✓

**Status**: Implemented.

- Tool: `tools/audit.py` (record_change / read_history / list_files_with_audit).
- Skill: `skills/audit.md`.
- Contract: `procedures.md` § 30 "Audit Trail Contract".
- Sibling files: `<file>.history.jsonl` for every entity-shape `_memory/*.yaml`.
- Skip-list in config: time-shape logs skip auditing.
- Yearly rotation flag in config.
- Tests: `tests/test_audit_and_session.py::test_audit_*` (3 tests).

### #18 — Tiered storage for sensitive data ✓

**Status**: Implemented as tier + auto-route; encryption deferred to roadmap M-01.

- Folder: `_memory/sensitive/` scaffolded by `workspace_init.py`.
- Config: `config.preferences.sensitive.{enabled, path, auto_route_files}`.
- Contract: `procedures.md` § 21 "Sensitive Tier Contract".
- AGENTS.md: § "Visibility and sensitive tier".
- Per-row `sensitive: true` flag supported on entity rows.
- Path override allows symlinking to encrypted disk image.

### #19 — Public / private / shared visibility flags ✓

**Status**: Implemented as schema + outbound-scrub policy.

- Schema addition: `visibility` field on entity rows (default `private`).
- Contract: `procedures.md` § 20 "Visibility Contract".
- Per-domain default cascades (Home defaults to `household`, Career defaults to `private`, etc.).
- Outbound scrub respects visibility.

### #20 — Operational handle pattern (every entity gets `<kind>:<slug>`) ✓

**Status**: Implemented.

- Tool: `tools/handles.py` (parse / format / is_handle / slug_for / collect_handles_in / filter_kind).
- Schema addition: `handle` field on entity rows; the world graph keys by handle.
- Contract: `procedures.md` § 18 "Operational Handles Contract".
- AGENTS.md: § "Operational handles".
- Back-compat: legacy bare ids parse via `LEGACY_PREFIXES` mapping.
- Canonical kind taxonomy in `tools/handles.py.KINDS`.
- Tests: `tests/test_handles.py` (8 tests).

### #21 — Skill bundles / playbooks / "scenes" ✓

**Status**: Implemented.

- Folder: `superagent/playbooks/`.
- Schema reference: `playbooks/_schema.yaml`.
- 5 starter playbooks shipped:
  - `start-of-day.yaml`
  - `end-of-week.yaml`
  - `tax-prep-season.yaml`
  - `pre-trip-week.yaml`
  - `health-checkup-quarter.yaml`
- Tool: `tools/play.py` (list / resolve / eval_condition).
- Skill: `skills/play.md`.
- Contract: `procedures.md` § 26 "Playbooks Contract".
- Conditions: small expression language over `bills_overdue`, `appointments_today`, `tasks_p0_open`, `projects_active`, `important_dates_today`, `subscriptions_audit_flag`.
- Custom overlay support: `_custom/playbooks/<name>.yaml` overrides framework playbook.
- Tests: `tests/test_play_and_scenarios.py::test_play_*` (2 tests).

### #22 — Time-windowed views over append-only logs ✓

**Status**: Implemented (also closes perf-improvement-ideas MI-2).

- Tool: `tools/log_window.py` (read_window / append_event / quarter_for / quarters_in_range).
- Quarterly partitioning by default; configurable in `config.preferences.events.partition`.
- Tests: `tests/test_log_window.py::test_quarter_for_consistent`, `test_filter_by_kind`.

### #23 — Agent's working set as explicit object ✓

**Status**: Implemented.

- Memory template: `templates/memory/working-sets.yaml` (schema reference; actual log is `_memory/working-sets.jsonl`).
- Tool integration: `tools/session_scratch.py` records reads / MCP / tool runs.
- Contract: `procedures.md` § 34 "Working-set Contract".
- Privacy: paths + sizes only — never contents.

### #24 — First-class decisions log ✓

**Status**: Implemented.

- Memory template: `templates/memory/decisions.yaml`.
- Skill: `skills/decisions.md` (capture / list / review / show).
- Contract: `procedures.md` § 28 "Decisions Log Contract".
- Schema: confidence, reversibility, alternatives_considered, rationale, review_at, outcome, revisited.
- Surface windows: `weekly-review` (decisions made this week), `monthly-review` (decisions due to review).

### #25 — Notification policy as configuration ✓

**Status**: Implemented.

- Memory template: `templates/memory/notification-policy.yaml`.
- 15 default rules seeded by init: bill-due-today, bill-due-soon, bill-overdue, appt-today, appt-tomorrow-needs-prep, doc-expiring-30d, doc-expiring-90d, important-date-today, important-date-soon, task-overdue-p0p1, subscription-trial-ending, med-refill-due, lab-result-abnormal, project-deadline-close, project-overdue.
- Contract: `procedures.md` § 31 "Notification Policy Contract".
- Auto-create-task: missing-task auto-creation idempotent across runs.
- Per-rule `where`: which skills consume it; per-rule `severity` drives placement.

---

## NOT implemented (remain in `ideas-better-structure.md` as future)

- **#1** — PARA tetrad (Resources promotion) — conceptual, deferred.
- **#7** — Embedded full-text search index (SQLite FTS5) — operational, separate roadmap item.
- **#8** — Embeddings for semantic recall — separate roadmap item (BB-1 in perf doc).
- **#12** — Multi-user separation — schema + conceptual, separate roadmap item (L-01/L-02).
- **#15** — Calendar write-back — operational; the `upstream-writes.yaml` template ships, but no skill writes to it yet.

---

## Cross-cutting changes

- **`config.yaml`** extended with new policy blocks: `events`, `audit`, `tags`, `decisions`, `briefing_cache`, `session`, `telemetry`, `sensitive`, `visibility`, `handles`, `outbox`, `inbox_triage`, `anti_patterns`.
- **`workspace_init.py`** scaffolds new folders: `Outbox/{drafts,staging,sent,sealed}/`, `_memory/{_briefings,_artifacts,_session,_telemetry,_checkpoints,sensitive,events}/`, plus copies the new memory templates.
- **`AGENTS.md`** extended with: read-budget policy, local-first read order, operational handles, visibility/sensitive tier, provenance, time/entity/event-shape taxonomy, prompt-cache discipline.
- **`procedures.md`** extended with 23 new sections (§ 17 through § 39).
- **8 new memory templates** + new fields on existing templates.
- **5 starter workflow templates** + 5 starter playbooks.
- **8 new skills** + 14 new Python tools (all with tests).
- **Test count grew from 43 → 96** (53 new tests).
