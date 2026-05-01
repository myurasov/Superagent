---
name: superagent-triage-overdue
description: >-
  Force a decision on every overdue task. For each: done | reschedule | drop |
  delegate. Runs in a tight loop until the queue is empty.
triggers:
  - triage overdue
  - clean up overdue tasks
  - what's overdue (and let's deal with it)
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent triage-overdue skill

## 1. Load overdue tasks

Read `_memory/todo.yaml`:

- `status in [open, in_progress]` AND `due_date < today` — these are the overdue tasks.

Sort: P0 first, then by days overdue.

If the queue is empty, surface "Nothing overdue. You're caught up." and stop.

## 2. Triage loop

For each overdue task, in order, ask:

```
[<position> / <total>] <id> {priority} <title>
   Due: <date> ({N} days overdue)
   Domain: <related_domain or "—">
   Description: <description (truncated to 200 chars)>

What do you want to do?
   d - mark Done (it's already complete; just hadn't logged)
   r - Reschedule (give it a new due date)
   p - drop Priority (move to P3 / someday-maybe)
   x - drop / cancel entirely
   l - delegate (ask who; reassigns ownership; capture into related contact)
   s - skip for now (re-surface next time)
```

Apply the user's choice:

- **d**: `status: done`, `completed_date: now`. If the description hints at a follow-up commitment, ask if a new task is needed.
- **r**: ask for new due date; update `due_date`.
- **p**: set `priority: P3`. (User can also pick `priority: P2` if more appropriate.)
- **x**: `status: cancelled`, prompt for a one-line reason in `notes`.
- **l**: ask for delegate's name. If they're in `contacts.yaml`, append the task description to that contact's `notes` ("delegated <task>"). Mark this task `status: cancelled` with note "delegated to <name>".
- **s**: skip; row stays as-is.

## 3. Sync downstream

After each operation:
- Update `todo.yaml`.
- Update the relevant `Domains/<Domain>/status.md` per the sync contract.

## 4. Summary

End with a one-line summary:

```
Triaged: <total>. Done: <d>, Rescheduled: <r>, Deprioritized: <p>, Dropped: <x>, Delegated: <l>, Skipped: <s>.
```

## 5. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "triage-overdue"
  summary: "Triaged <N> overdue tasks: <breakdown>."
```
