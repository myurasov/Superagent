# Status — {{PROJECT_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Status + tasks file for a Project (4-file structure).
  Sync contract: identical to Domains/<domain>/status.md.
  Open / Done blocks auto-rendered from `_memory/todo.yaml` filtered by
  `related_project: <slug>`.
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Status — {{PROJECT_NAME}}](#status--project_name)
  - [Status](#status)
    - [Current Status (RAG)](#current-status-rag)
    - [Recent Progress](#recent-progress)
    - [Active Blockers](#active-blockers)
    - [Next Steps](#next-steps)
    - [Burn-down](#burn-down)
  - [Open](#open)
  - [Done](#done)

---

## Status

### Current Status (RAG)

<!-- One paragraph. Bold the RAG word.
     Example: **Green** — gathering 1099s; on track for April 15. -->

{{CURRENT_STATUS_RAG}}

### Recent Progress

<!-- `-` bullets, newest first. -->

{{RECENT_PROGRESS}}

### Active Blockers

<!-- `-` bullets. What's stuck and what would unblock it. Surface honestly. -->

{{ACTIVE_BLOCKERS}}

### Next Steps

<!-- `-` bullets (NOT numbered). What concretely needs to happen next. -->

{{NEXT_STEPS}}

### Burn-down

<!-- For projects with a target_date and ≥ 5 success criteria, render
     the simple burn-down: what's checked vs total, days remaining. -->

{{BURN_DOWN}}

---

## Open

<!-- Tasks from `_memory/todo.yaml` where `related_project: <slug>` AND
     status in (open, in_progress). Auto-rendered. -->

{{OPEN_ITEMS}}

## Done

<!-- Recently completed tasks for this project. Retained for 30 days. -->

{{DONE_ITEMS}}
