# Todo — {{SCOPE}}

> **[Do not change manually — managed by Superagent]**

<!--
  Cross-cutting / domain-scoped task view. Renders the relevant subset of
  `_memory/todo.yaml` as priority-grouped markdown tables. Skills regenerate
  this file on every add / complete / update operation per the sync contract
  in contracts/task-management.md § 5.1.

  Workspace-wide todo lives at `workspace/todo.md`
  (SCOPE = "workspace"). Per-domain todos live at
  `workspace/Domains/<domain>/status.md` (the `## Open` and
  `## Done` blocks there).
-->

_Last updated: {{LAST_UPDATED}}_

---

## P0 — Today / Urgent

<!-- Bills due today, health-critical refills, time-bound emergencies. -->

| ID | Task | Due | Domain |
|----|------|-----|--------|
{{P0_ROWS}}

## P1 — This Week

<!-- Appointments in the next 7 days, deadlines this week, important dates Sat/Sun. -->

| ID | Task | Due | Domain |
|----|------|-----|--------|
{{P1_ROWS}}

## P2 — Active

<!-- General "should do this month". -->

| ID | Task | Due | Domain |
|----|------|-----|--------|
{{P2_ROWS}}

## P3 — Future / Aspirational

<!-- Someday-maybe — backlog. -->

| ID | Task | Due | Domain |
|----|------|-----|--------|
{{P3_ROWS}}

## Done

<!-- Recently completed; auto-pruned after 30 days. -->

| ID | Task | Completed |
|----|------|-----------|
{{DONE_ROWS}}
