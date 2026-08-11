# Rule: workspace/todo.md is always live

`workspace/todo.md` is the **single master view** of everything open across all of Superagent. It must reflect reality at all times.

## When to update it

Update `workspace/todo.md` **in the same turn** — never deferred — whenever any of these happen:

- A task is added, completed, cancelled, or its priority/due-date changes
- A project plan changes (new steps, steps removed, deadlines shift)
- A task becomes obviously stale (due date long past, context makes it irrelevant)
- The agent learns something that makes a task moot (e.g., a payment clears, a blocker resolves)

## What goes in it

**Show only open / in-progress tasks.** Completed and cancelled tasks are removed from the live view automatically. Exception: tasks completed in the last 14 days may appear under a "Recently done" section as a brief audit trail, then drop off.

**Status values for auto-closed tasks** (use `auto-` prefix in `todo.yaml` when the agent — not the user — closes a task):
- `auto-done` — agent confirmed it's complete (e.g., payment verified cleared, file confirmed delivered)
- `auto-cancelled` — agent determined it's no longer needed (superseded, moot, or stale past recovery)
- `auto-stale` — overdue with no recent activity; parked for user review

**Remove from `workspace/todo.md` without asking when:**
- Status in `todo.yaml` is `done`, `cancelled`, `auto-done`, or `auto-cancelled`
- Due date is more than 30 days in the past AND no recent session context suggests it's still active → set `auto-stale` in yaml, remove from todo.md
- Task was explicitly superseded by a newer task in the same session

**Ask before removing when:**
- Task is overdue by 7–30 days and there's no clear signal either way
- Task belongs to a domain the agent hasn't touched recently and staleness is ambiguous

## Format

Group by priority (P0 → P1 → P2 → P3), then by project/domain within each group. Each row: checkbox, autonomy marker, task ID, bold title, brief description, due date, project tag.

**Task descriptions:** every task must include a one-line plain-English explanation in italics after the title and an em dash. Spell out abbreviations, name the portal or phone number, and state the concrete outcome. No jargon without definition. Keep it to one clause; do not write paragraphs. Format:

```
**Title** — _plain-english explanation of what this is and why_ `project-tag`
```

Examples:
- ✓ `**Submit CA OTPA** — _California's once-per-lifetime penalty waiver; file Form 2918 on ftb.ca.gov/myftb after 2024 CA return is accepted_`
- ✗ `**Submit CA OTPA** Form 2918 / MyFTB on 2024 (after 2024 CA filed)` ← not italic, jargon unexplained
- ✗ three-sentence explanation ← too long

Add a `_Last updated_` timestamp at the top on every write, including time in the user's timezone (Pacific — PDT in summer, PST in winter). Format: `YYYY-MM-DD HH:MM AM/PM PDT/PST`. Get the current time with `TZ="America/Los_Angeles" date "+%Y-%m-%d %I:%M %p %Z"`.

## Source of truth

`workspace/_memory/todo.yaml` is the canonical structured store. `workspace/todo.md` is a rendered view of it — always derived, never the master. When they conflict, `todo.yaml` wins; update `todo.md` to match.

## Never miss a task — mandatory read protocol

Before writing or updating `workspace/todo.md`, **always read `_memory/todo.yaml` in full** (not from memory or prior context). Then:

1. Extract every task where `status` is `open` or `in_progress`.
2. Cross-check: every such task must appear in `todo.md` (as an active item) OR in the Auto-closed section (with an explicit reason). No silent omissions.
3. If a task's `status` is `done` or `cancelled` in yaml but was shown as open in `todo.md`, move it to "Recently done" or drop it — never leave it active.
4. After writing, do a final count: number of open+in_progress rows in yaml must equal number of active checkboxes in `todo.md` (subtasks count individually).
