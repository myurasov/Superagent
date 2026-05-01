# Project Charter — {{PROJECT_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Project info file (4-file structure):
    info.md     — this file: charter / scope / deliverables / success criteria / team
    status.md   — RAG + open / done tasks (auto-synced from todo.yaml)
    history.md  — chronological log of decisions, milestones, touchpoints
    rolodex.md  — project-specific contact directory
  Plus optional sub-folders:
    sources.md  — curated catalogue of Sources/ entries for this project
    Resources/  — drafts, working files, agent-generated artifacts
    Sources/    — project-scoped `.ref.md` pointers + cached fetches

  A Project is a TIME-BOUNDED effort with a clear goal — distinct from a
  Domain (ongoing area of responsibility). Examples:
    - File 2025 taxes (annual recurring, fixed deadline)
    - Plan summer Italy trip (one-shot, target date in June)
    - Replace the dishwasher (one-shot, target date when current one dies)
    - Renovate the kitchen (one-shot, multi-month, sub-tasks)

  Every section below is required; insert <!-- TBD --> when content
  isn't known yet rather than deleting the section.
-->

---

## Table of Contents

- [Project Charter — {{PROJECT_NAME}}](#project-charter--project_name)
  - [Summary](#summary)
  - [Goal](#goal)
  - [Scope](#scope)
  - [Success Criteria](#success-criteria)
  - [Deliverables](#deliverables)
  - [Timeline & Milestones](#timeline--milestones)
  - [Stakeholders](#stakeholders)
  - [Budget](#budget)
  - [Dependencies & Risks](#dependencies--risks)

---

## Summary

| Field | Value |
|-------|-------|
| **Project** | {{PROJECT_NAME}} |
| **Goal** | {{PROJECT_GOAL}} |
| **Status** | {{PROJECT_STATUS}} <!-- planning / active / paused / completed / cancelled / archived --> |
| **Priority** | {{PROJECT_PRIORITY}} <!-- P0 / P1 / P2 / P3 --> |
| **Related domains** | {{RELATED_DOMAINS}} |
| **Start** | {{START_DATE}} |
| **Target** | {{TARGET_DATE}} |
| **Recurring** | {{RECURRING}} <!-- none / annual / quarterly / monthly / every_n_years --> |

---

## Goal

<!-- One sentence. What does "done" look like? The goal is the test you
     would apply at completion to confirm the project succeeded. -->

{{GOAL_DETAIL}}

---

## Scope

<!-- What's in scope. What's explicitly out of scope. Drawing the line
     here at the start saves a lot of "wait, are we doing X too?" later. -->

**In scope**:
{{SCOPE_IN}}

**Out of scope**:
{{SCOPE_OUT}}

---

## Success Criteria

<!-- Observable outcomes that mean "done". Use checklist format so they
     can be ticked off in `status.md`. -->

- [ ] {{CRITERION_1}}
- [ ] {{CRITERION_2}}
- [ ] {{CRITERION_3}}

---

## Deliverables

<!-- Concrete artifacts the project produces. For "file 2025 taxes":
     filed return, receipts archive, prior-year file in Sources/. -->

- {{DELIVERABLE_1}}
- {{DELIVERABLE_2}}

---

## Timeline & Milestones

<!-- Phases, gates, key dates. For recurring projects, this is the
     repeating cycle (e.g. "annual: gather → file → archive"). -->

| Milestone | Target | Status |
|-----------|--------|--------|
| {{MILESTONE_1}} | {{MILESTONE_1_DATE}} | {{MILESTONE_1_STATUS}} |

---

## Stakeholders

<!-- Headline list. Full contact details live in rolodex.md.
     Roles relevant to a personal project:
       - sponsor:    you (or the household member driving)
       - executor:   who actually does the work
       - approver:   spouse, financial advisor, doctor, contractor
       - reviewer:   accountant, lawyer, second opinion
       - dependents: people affected by the outcome (kids, partner, parents) -->

- **{{NAME_1}}** ({{ROLE_1}}) — {{NOTE_1}}

---

## Budget

<!-- Optional. For projects with money attached. -->

| | Amount | Currency |
|---|---|---|
| Planned | {{BUDGET_PLANNED}} | {{CURRENCY}} |
| Spent | {{BUDGET_SPENT}} | {{CURRENCY}} |
| Variance | {{BUDGET_VARIANCE}} | {{CURRENCY}} |

---

## Dependencies & Risks

<!-- What has to happen for this to succeed. What could derail it. -->

**Dependencies**:
- {{DEPENDENCY_1}}

**Risks** (likelihood × impact = exposure; what would mitigate):

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| {{RISK_1}} | {{RISK_1_LIKELIHOOD}} | {{RISK_1_IMPACT}} | {{RISK_1_MITIGATION}} |
