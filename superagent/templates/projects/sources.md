# Sources — {{PROJECT_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Curated catalogue of `Sources/` entries relevant to this project.
  Same shape as Domains/<domain>/sources.md.

  Foundational rule (contracts/sources.md + § 16): source documents for a
  project live in EITHER:
    - workspace `Sources/documents/<category>/` (cross-cutting; would
      survive the project being archived), OR
    - project-scoped `Projects/{{PROJECT_NAME}}/Sources/documents/` (only
      relevant to this project; goes with the project to Archive/).

  This file lists both, distinguished by the Path column.

  Sync contract: every `add-source --to-project {{PROJECT_ID}}` invocation
  appends a row here AND updates `_memory/sources-index.yaml.<row>.related_project`.
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Sources — {{PROJECT_NAME}}](#sources--project_name)
  - [How this file stays current](#how-this-file-stays-current)
  - [Workspace-level documents (relevant to this project)](#workspace-level-documents-relevant-to-this-project)
  - [Project-scoped documents](#project-scoped-documents)
  - [References (external pointers)](#references-external-pointers)
  - [Project-generated artifacts](#project-generated-artifacts)

---

## How this file stays current

- Every time you say "add X to {{PROJECT_NAME}}" (or run `add-source --to-project {{PROJECT_ID}}`), a row appears here.
- Untagging via `add-source --untag <id>` moves the row to a `## Removed (history)` block.
- The actual files in `Sources/` (workspace-level OR project-scoped) are NEVER deleted by skills. Only this catalogue is mutated.
- Hand-edits are welcome and preserved.

---

## Workspace-level documents (relevant to this project)

<!-- Files under workspace `Sources/documents/<category>/` that you tagged
     to this project. They survive the project being archived. -->

| Title | Path | Category | Added | Notes |
|-------|------|----------|-------|-------|
| {{WS_DOC_1_TITLE}} | {{WS_DOC_1_PATH}} | {{WS_DOC_1_CATEGORY}} | {{WS_DOC_1_ADDED}} | {{WS_DOC_1_NOTES}} |

{{WORKSPACE_DOCS_TABLE_ROWS}}

---

## Project-scoped documents

<!-- Files under `Projects/{{PROJECT_NAME}}/Sources/documents/` — bound to
     this project. When the project archives, these files move to Archive
     with it. Use for things that only make sense in the project's context
     (vendor quotes for a renovation; trip-specific itineraries; etc.). -->

| Title | Path | Category | Added | Notes |
|-------|------|----------|-------|-------|
| {{P_DOC_1_TITLE}} | {{P_DOC_1_PATH}} | {{P_DOC_1_CATEGORY}} | {{P_DOC_1_ADDED}} | {{P_DOC_1_NOTES}} |

{{PROJECT_DOCS_TABLE_ROWS}}

---

## References (external pointers)

<!-- `.ref.md` files relevant to this project — workspace or project-scoped. -->

| Title | Ref path | Kind | Source | Notes |
|-------|----------|------|--------|-------|
| {{REF_1_TITLE}} | {{REF_1_PATH}} | {{REF_1_KIND}} | {{REF_1_SOURCE}} | {{REF_1_NOTES}} |

{{REFERENCES_TABLE_ROWS}}

---

## Project-generated artifacts

<!-- Files in `Projects/{{PROJECT_NAME}}/Resources/` — drafts, sketches,
     photos, agent-rendered briefings that aren't outbound. Listed here for
     a single "what files exist for this project?" view. -->

| Title | Path | Kind | Added | Notes |
|-------|------|------|-------|-------|
| {{ART_1_TITLE}} | {{ART_1_PATH}} | {{ART_1_KIND}} | {{ART_1_ADDED}} | {{ART_1_NOTES}} |

{{ARTIFACTS_TABLE_ROWS}}
