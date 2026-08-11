# Rule: Task display ID format

Task display IDs in `workspace/todo.md` follow the format `TASK-NNN` for standalone tasks and `TASK-NNN-X` (where X is A, B, C…) for subtasks of a parent.

## Rules

- **Continuously numbered from oldest** — TASK-001 is the oldest task ever created, not the most recent. New tasks always get the next highest number.
- **No dates** in the ID.
- **Never reassign** — once an ID is given, it is permanent regardless of whether the task is open, done, or cancelled.
- **Subtask letters** — assigned when one logical task breaks into sequential steps (e.g. draft → print → mail → file = TASK-048-A through TASK-048-D).

## Relationship to todo.yaml

The internal `todo.yaml` IDs (`task-YYYYMMDD-NNN`) remain as stable internal keys and are NOT changed. `TASK-NNN` is the display ID shown in `workspace/todo.md` and used in conversation. The mapping is maintained implicitly by creation order.

## Current state

As of 2026-08-10 the highest assigned display ID is TASK-055. Next new task is TASK-056.
