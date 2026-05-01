# `Projects/` — time-bounded efforts

A **Project** is anything in your life with:

- A clear **goal** (one sentence: what does "done" look like?)
- A **target date** or end condition
- A finite set of **tasks** that, when completed, achieve the goal

Distinct from **Domains** (`Domains/`), which are *ongoing* areas of responsibility (Health, Finance, Home — these never "complete"). Projects start, run, finish.

## Examples

| Project | Domain it touches | Recurring? | Typical duration |
|---|---|---|---|
| File 2026 taxes | Finance | annual | Jan-April |
| Plan summer Italy trip | Travel + Family | none | 2-3 months ahead |
| Find a new dentist | Health | none | 2-4 weeks |
| Replace the dishwasher | Home | none | 1-2 weeks |
| Renovate the kitchen | Home + Finance | none | 3-6 months |
| Job search Q3 2026 | Career | none | 1-3 months |
| Annual health tune-up | Health | annual | physical + dental + eye exam scheduled in same window |
| Move to new house | Home + Finance + Family | none | 3-6 months |
| Get marathon-ready by October | Hobbies + Health | annual | 4-6 months |
| Spring deep-clean | Home | annual | 1 weekend, planned for weeks |
| Annual giving (charitable) | Finance | annual | December |
| Q4 RSU vesting + sell decision | Career + Finance | quarterly | 1 week per quarter |

## Layout

```
Projects/
  README.md                       ← this file
  <project-slug>/
    info.md                        ← charter: goal, scope, success criteria, deliverables, team
    status.md                      ← RAG + open / done tasks (auto-synced)
    history.md                     ← chronological log of decisions, milestones
    rolodex.md                     ← project-scoped contact directory
    sources.md                     ← curated catalogue of Sources/ entries
    Resources/                     ← optional: drafts, working files, agent-generated artifacts
    Sources/                       ← optional: project-scoped `.ref.md` + cache
```

The 4-file structure mirrors `Domains/`. The same skills (`add-contact` updates `rolodex.md`; `log-event` appends to `history.md`; ingestors capture into the relevant log) work for both.

## Lifecycle

```
planning → active → completed → archived
            ↓ ↑
           paused
            ↓
        cancelled
```

- **planning** — charter is being drafted; not actively driving any tasks.
- **active** — tasks are open; cadence skills surface its deadlines.
- **paused** — temporarily set aside (waiting on someone else, on hold by you). Removed from active surfacing; restorable via `projects resume <slug>`.
- **completed** — all success criteria met; project moves to read-only mode.
- **cancelled** — abandoned; preserved for history. Note the reason in `history.md`.
- **archived** — `doctor` moves it to `Archive/<YYYY-MM>/Projects/<slug>/` after 90 days completed. Reversible via `mv`.

## Recurring projects

For projects that repeat on a fixed cadence (taxes, annual health tune-up, year-end giving), set `recurring: annual` (or `quarterly`, `monthly`, `every_n_years`) in `_memory/projects-index.yaml`. When the project completes:

1. The current row stays as historical record.
2. A new row auto-creates with `id: <base>-<next-period>` (e.g. `tax-2025` → `tax-2026`).
3. The new row inherits scope, success criteria, deliverables, and stakeholders from the previous instance.
4. Lessons-learned from the just-completed instance's `history.md` get linked from the new instance's `info.md` ("see prior: `Archive/<YYYY-MM>/Projects/tax-2025/history.md`").

This is how the agent gets smarter at recurring efforts over time — each year's tax project starts with last year's playbook.

## Project management methodology

The conventions Superagent enforces:

- **Charter first** — `add-project` collects goal, scope, success criteria, target date, stakeholders, related domains BEFORE creating the folder. Forces explicit thinking.
- **Daily / weekly surfacing** — `daily-update` highlights any project with a deadline in the next 14 days OR with a recently-changed status. `weekly-review` rolls up project burn-down across all active projects.
- **Burn-down rendering** — when a project has ≥ 5 success criteria, `status.md` shows a simple "checked / total — N days to target" line.
- **Linked tasks** — every task in `_memory/todo.yaml` can carry `related_project: <slug>`. Tasks for a project are filtered into the project's `status.md` `## Open` block automatically.
- **History is sacred** — append-only. Decisions, status flips, milestone completions all log there.
- **Cross-domain awareness** — `related_domains: [..]` makes a project visible to its parent domains' status. The kitchen-reno project shows up in both `Domains/Home/status.md` § Next Steps AND `Domains/Finance/status.md` § Next Steps.

## When NOT to make something a Project

Don't project-ify:

- Single tasks ("call the dentist"). Use `todo` directly.
- Ongoing maintenance ("HVAC filter changes every 90 days"). Use `assets-index.yaml.maintenance[]` + cadence surfacing.
- Reactions to events ("respond to email about X"). Use `interaction-log.yaml` + `follow-up`.

The right test: *"Will this benefit from a charter, a deadline, and a status briefing more than once?"* If yes, project. If no, plain task.

## Adding one

```
add-project
```

The skill walks you through the 5-question charter (name, goal, scope, target date, related domains) and scaffolds the folder + 4 files + index row.
