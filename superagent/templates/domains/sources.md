# Sources — {{DOMAIN_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  The curated catalogue of `Sources/` entries relevant to this domain.

  Foundational rule (procedures.md § 15): source documents are NEVER stored
  inside Domains/<domain>/. They live in `Sources/documents/<category>/`
  (immutable, indexed, cached). This file is the human-readable, domain-scoped
  POINTER LIST so when you open the domain you can see "what does Superagent
  know about this domain?" at a glance.

  Sync contract: every `add-source --to-domain <id>` invocation appends a row
  here AND updates `_memory/sources-index.yaml.<row>.related_domain`.

  The structured truth lives in `_memory/sources-index.yaml`. This file is the
  human-readable projection of that index, filtered to this domain. It is
  managed by skills BUT respects user hand-edits (diff-and-merge, not
  blind-clobber). Add commentary, group rows differently, leave notes —
  Superagent will preserve them.
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Sources — {{DOMAIN_NAME}}](#sources--domain_name)
  - [How this file stays current](#how-this-file-stays-current)
  - [Documents](#documents)
  - [References (external pointers)](#references-external-pointers)
  - [Domain-generated artifacts](#domain-generated-artifacts)

---

## How this file stays current

- Every time you say "add X to {{DOMAIN_NAME}}" (or run `add-source --to-domain {{DOMAIN_ID}}`), a row appears here.
- Every time you remove a Sources entry from this domain (`add-source --untag <id>`), the row is moved to `## Removed (history)` at the bottom.
- The agent NEVER deletes rows from `Sources/` itself — only from this index. The actual file in `Sources/documents/` stays put.
- Hand-edits are welcome — group rows your way, add commentary in the **Notes** column, regroup under custom sub-headings. Superagent preserves your structure on the next sync.

---

## Documents

<!-- Files in `Sources/documents/<category>/...` that belong to this domain.
     Format: one row per source. The Path is workspace-relative.
     Sensitive items get a 🔒 marker (rendered as the literal "[sensitive]"
     here so we keep the no-emoji rule for committed framework files). -->

| Title | Path | Category | Added | Notes |
|-------|------|----------|-------|-------|
| {{DOC_1_TITLE}} | {{DOC_1_PATH}} | {{DOC_1_CATEGORY}} | {{DOC_1_ADDED}} | {{DOC_1_NOTES}} |

{{DOCUMENTS_TABLE_ROWS}}

---

## References (external pointers)

<!-- `.ref.md` files under `Sources/references/<category>/` that point at
     external data (MCP / CLI / URL / API / vault / manual). Resolved to
     fresh content via the local-first cache (procedures.md § 15.5). -->

| Title | Ref path | Kind | Source | Notes |
|-------|----------|------|--------|-------|
| {{REF_1_TITLE}} | {{REF_1_PATH}} | {{REF_1_KIND}} | {{REF_1_SOURCE}} | {{REF_1_NOTES}} |

{{REFERENCES_TABLE_ROWS}}

---

## Domain-generated artifacts

<!-- Optional. Files in `Domains/{{DOMAIN_NAME}}/Resources/` that were
     produced by you or by Superagent for this domain (drafts, photos,
     scratch worksheets, agent-rendered briefings that aren't outbound).

     These are NOT in `Sources/` — they live alongside the domain because
     they're working artifacts, not vault-grade records. Listed here so the
     domain has a single "what files exist for me?" view. -->

| Title | Path | Kind | Added | Notes |
|-------|------|------|-------|-------|
| {{ART_1_TITLE}} | {{ART_1_PATH}} | {{ART_1_KIND}} | {{ART_1_ADDED}} | {{ART_1_NOTES}} |

{{ARTIFACTS_TABLE_ROWS}}
