# Projects Contract (time-bounded efforts)

<!-- Migrated from `procedures.md § 16`. Citation form: `contracts/projects.md`. -->

A **Project** is anything in the user's life with a clear goal, a target date, and a finite set of tasks that — once done — achieve the goal. Distinct from **Domains** (ongoing areas of responsibility that never "complete").

### 16.1 What's a Project (vs. a Domain, vs. a task)

| Concept | Time shape | Example |
|---|---|---|
| **Domain** | indefinite | "Health" — managed forever |
| **Project** | bounded | "File 2026 taxes" — Jan-April; then done |
| **Task** | atomic | "Call accountant to confirm appointment" |

Test: *"Will this benefit from a charter, a deadline, and a status briefing more than once?"* If yes → Project. If no → Task. If it has no end → Domain.

### 16.2 Lifecycle

```
planning → active → completed → archived
            ↑ ↓
           paused
              ↓
         cancelled
```

- **planning** — charter is being drafted; not surfacing in cadences yet.
- **active** — surfacing in `daily-update`, `weekly-review`, `monthly-review` per `config.preferences.projects.deadline_lookahead_days`.
- **paused** — temporarily set aside; suppressed from active surfacing; restorable via `projects resume <slug>`.
- **completed** — all success criteria met; project moves to read-only.
- **cancelled** — abandoned. Reason captured in `history.md`. Counts as terminal.
- **archived** — `doctor` moves to `Archive/<YYYY-MM>/Projects/<slug>/` after `config.preferences.projects.archive_after_days` (default 90) in `completed`. Reversible via `mv`.

### 16.3 4-file structure

Same shape as Domains:

```
Projects/<slug>/
  info.md       — charter (goal, scope, success criteria, deliverables, team, dates, budget)
  status.md     — RAG + open / done tasks (auto-synced from todo.yaml)
  history.md    — chronological log; H4 entries; newest at top
  rolodex.md    — project-scoped contact directory
  sources.md    — curated catalogue of Sources/ entries for this project
  Resources/    — optional, lazily created — drafts, working files, agent artifacts
  Sources/      — optional project-scoped reference library (per § 15.7)
```

Templates: `superagent/templates/projects/<file>.md`.

### 16.4 Charter contract

The `add-project` skill collects the **charter** before creating the folder:

1. **Name** (display title; auto-derives `slug`).
2. **Goal** (one sentence — what does "done" look like?).
3. **Scope** (what's in / out — explicit list).
4. **Target date** (ISO 8601; required for `active` state).
5. **Related domains** (multi-select from `_memory/domains-index.yaml`).
6. **Success criteria** (3-5 checkboxes).
7. **Deliverables** (concrete artifacts produced).
8. **Stakeholders** (list of contact ids).
9. **Recurring** (none / annual / quarterly / monthly / every_n_years).
10. **Budget** (optional: planned amount + currency).

A charter without `goal` and `target_date` cannot transition out of `planning`.

### 16.5 Sync contract (status.md ↔ todo.yaml)

For each Project with `status: active`:

- The `## Open` block in `status.md` is **regenerated** on every change to `_memory/todo.yaml` where any task has `related_project: <slug>`. Tasks are sorted by priority then due date.
- The `## Done` block lists tasks completed in the last 30 days for this project.
- The `### Burn-down` block (only when the charter has ≥ 5 success criteria) renders `<checked>/<total> — <days> days to target`.

`render_status.py --scope project:<slug>` regenerates a single project's status. The `todo` skill triggers this automatically after any task add / complete / update where `related_project` is set.

### 16.6 Surfacing

Active projects surface in cadence skills per these rules:

| Window | Where surfaced |
|---|---|
| Target date < `config.preferences.projects.deadline_lookahead_days` (default 14) | `daily-update` "This week" section + P1 task auto-created if no open tasks for the project in next 7 days |
| Target date in 14-30 days | `weekly-review` "Project deadlines" section |
| Target date > 30 days | `monthly-review` "Active projects" rollup only |
| Status flip (planning → active, active → paused, etc.) | `daily-update` "Project changes" line for the next run |
| New tasks added to a project | counted in `weekly-review` per-project burn-down |
| Stalled (no `history.md` entry in 14 days AND `status: active`) | `weekly-review` "Stalled projects" — prompts user to pause or push |

### 16.7 Recurring projects

For projects with `recurring` ≠ `none`, when the project completes:

1. The current row stays in `_memory/projects-index.yaml` as historical record.
2. The Supercoder-side helper (`_orchestrator`-equivalent) auto-creates the next-cycle row:
   - `id: <base>-<next-period>` (e.g. `tax-2025` → `tax-2026`).
   - Inherits `name` (with year-substitution if applicable), `scope`, `success_criteria`, `deliverables`, `stakeholders`, `related_domains`, `recurring`.
   - `start_date: target_date + 1 day` (or rule-based — annual taxes default to "Jan 1 of next year").
   - `target_date: prior target + 1 cycle`.
   - `status: planning`.
3. The new project's `info.md` carries a `## Prior cycles` link to the just-completed instance's `Archive/<YYYY-MM>/Projects/<old-slug>/history.md`.

Disable per-project via `recurring: none` after completion if the user wants to stop the cycle.

### 16.8 Cross-domain awareness

`related_domains: [..]` makes a Project visible in its parent Domains:

- The Domain's `status.md` § Next Steps surfaces a one-bullet summary per active linked project.
- The Domain's `history.md` carries an H4 entry on every project status flip (planning → active, active → completed) related to that Domain.

This is how a kitchen-reno Project shows up in BOTH `Domains/Home/status.md` AND `Domains/Finance/status.md` without duplicating the project itself.

### 16.9 Archival

90 days after `status: completed` (configurable), `doctor` proposes archive:

1. Move `Projects/<slug>/` → `Archive/<YYYY-MM>/Projects/<slug>/`.
2. Update `_memory/projects-index.yaml.<row>.path` to point at the new location.
3. Move the row from `projects-index.yaml.projects[]` to `projects-index.yaml.archived[]`.
4. Tasks for this project remain in `_memory/todo.yaml` but are no longer surfaced (filtered by status: completed / cancelled).
5. The cache under `Projects/<slug>/Sources/_cache/` is dropped to disk savings; the documents and references stay (they're in `Sources/`, not in `_cache/`).

Reversible: `mv Archive/<YYYY-MM>/Projects/<slug>/ Projects/<slug>/` and update the index. Cancelled projects follow the same archival path; they just never had completion criteria.
