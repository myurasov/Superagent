# Status — {{DOMAIN_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Status + tasks file (4-file structure).

  Sub-section contract:
    ## Status
      ### Current Status (RAG)   — paragraph; bold the RAG word (Green/Amber/Red)
      ### Recent Progress        — `-` bullets, newest first
      ### Active Blockers        — `-` bullets; what's stuck and why
      ### Next Steps             — `-` bullets (NOT numbered)
    ## Open                       — task list (P0 first, then by due date)
    ## Done                       — most-recently-completed first; retain ~30 days

  Superagent keeps Open / Done in sync with `_memory/todo.yaml` — any skill
  that creates or completes tasks updates both the central YAML and the
  relevant domain's `status.md`. The Status block (RAG / progress / blockers /
  next-steps) is hand-curated; skills only update it when the situation
  meaningfully changes.
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Status — {{DOMAIN_NAME}}](#status--domain_name)
  - [Status](#status)
    - [Current Status (RAG)](#current-status-rag)
    - [Recent Progress](#recent-progress)
    - [Active Blockers](#active-blockers)
    - [Next Steps](#next-steps)
  - [Open](#open)
  - [Done](#done)

---

## Status

### Current Status (RAG)

<!-- One paragraph. Bold the RAG word. Example:
     **Green** — annual physical scheduled; med refills caught up; no open issues. -->

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

---

## Open

<!-- Ordered by priority (P0 first) then due date (soonest first). -->

{{OPEN_ITEMS}}

## Done

<!-- Most recently completed first. Retain for 30 days then optionally prune. -->

{{DONE_ITEMS}}
