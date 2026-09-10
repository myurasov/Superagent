# Rule: Task display ID format

Task display IDs in `workspace/todo.md` follow the format `TASK-NNN` for standalone tasks and `TASK-NNN-X` (where X is A, B, C…) for subtasks of a parent.

## Rules

- **Continuously numbered from oldest** — TASK-001 is the oldest task ever created, not the most recent. New tasks always get the next highest number.
- **No dates** in the ID.
- **Never reassign** — once an ID is given, it is permanent regardless of whether the task is open, done, or cancelled.
- **Subtask letters** — assigned when one logical task breaks into sequential steps (e.g. draft → print → mail → file = TASK-048-A through TASK-048-D).

## Relationship to todo.yaml

The internal `todo.yaml` IDs (`task-YYYYMMDD-NNN`) remain as stable internal keys and are NOT changed. `TASK-NNN` is the display ID shown in `workspace/todo.md` and used in conversation. The mapping is maintained implicitly by creation order. The render itself is governed by `rules/live-todo.md`.

## In conversation

The internal `todo.yaml` ids (`task-YYYYMMDD-NNN`, legacy `task-NNN`) are machine keys. When discussing items from the task tracker, **do not surface those internal ids to the user** in casual conversation — give a brief inline noun-phrase description of what the task is about (or the `TASK-NNN` display id when the user is working from `workspace/todo.md`). Internal tool calls (grep, file reads, `next_id`) may still use the internal ids to locate the right rows.

## Deriving the next ID

The counter lives in the workspace, never in this rule. At write time:

1. Scan `workspace/todo.md` and `_memory/todo.yaml` for every `TASK-NNN` present (open, done, cancelled, and "Recently done" rows all count — IDs are never reassigned, so retired numbers stay taken).
2. Take the highest `NNN` and add one. Subtask letters (`-A`, `-B`, …) do not advance the counter.
3. If no `TASK-NNN` exists anywhere yet, start at `TASK-001`.

Never record "the current highest ID" in a framework file — it is workspace state and goes stale the day it is written. A household that wants a different numbering scheme overrides this rule at `workspace/_custom/rules/task-ids.md`.
