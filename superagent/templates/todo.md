# Todo — {{SCOPE}}

> **[Do not change manually — managed by Superagent]**

<!--
  Unified open-task view for the workspace. Renders every task from
  `_memory/todo.yaml` (regardless of related_domain / related_project)
  as priority-grouped markdown tables; the Scope column disambiguates
  each row using the operational-handle form `project:<slug>` or
  `domain:<id>`. Skills regenerate this file on every add / complete /
  update operation per the sync contract in
  contracts/task-management.md § 5.1.

  Per-project status lives at `workspace/Projects/<slug>/status.md` and
  per-domain status lives at `workspace/Domains/<domain>/status.md`;
  those views are scoped to their own bucket, while `workspace/todo.md`
  is the cross-cutting all-tasks view.
-->

_Last updated: {{LAST_UPDATED}}_

---

## P0 — Today / Urgent

<!-- Bills due today, health-critical refills, time-bound emergencies. -->

| ID | Task | Due | Scope |
|----|------|-----|-------|
{{P0_ROWS}}

## P1 — This Week

<!-- Appointments in the next 7 days, deadlines this week, important dates Sat/Sun. -->

| ID | Task | Due | Scope |
|----|------|-----|-------|
{{P1_ROWS}}

## P2 — Active

<!-- General "should do this month". -->

| ID | Task | Due | Scope |
|----|------|-----|-------|
{{P2_ROWS}}

## P3 — Future / Aspirational

<!-- Someday-maybe — backlog. -->

| ID | Task | Due | Scope |
|----|------|-----|-------|
{{P3_ROWS}}

## Done

<!-- Recently completed; auto-pruned after 30 days. -->

| ID | Task | Completed |
|----|------|-----------|
{{DONE_ROWS}}
